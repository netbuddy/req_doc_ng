#!/usr/bin/env bash
# 发布后自检（离线机执行）：以终态证据判定本次安装/升级是否正确，而不是以「命令没报错」为准。
#
# 用法：bash scripts/verify-release.sh --install-root <安装目录> [--package-dir <包目录>]
# 未给 --package-dir 时读安装目录里留存的 manifest 副本。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib-common.sh
source "$SCRIPT_DIR/lib-common.sh"
REQDOC_LOG_PREFIX="verify"

INSTALL_ROOT=""
PACKAGE_DIR=""
while [ $# -gt 0 ]; do
  case "$1" in
    --install-root) INSTALL_ROOT="$(cd "$2" && pwd)"; shift 2 ;;
    --package-dir) PACKAGE_DIR="$(cd "$2" && pwd)"; shift 2 ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) die "未知参数：$1" ;;
  esac
done
[ -n "$INSTALL_ROOT" ] || die "必须指定 --install-root <安装目录>"

if [ -n "$PACKAGE_DIR" ]; then
  MANIFEST="$PACKAGE_DIR/manifest.json"
else
  MANIFEST="$(ls -1t "$INSTALL_ROOT"/state/manifest-*.json 2>/dev/null | head -1 || true)"
fi
[ -n "${MANIFEST:-}" ] && [ -f "$MANIFEST" ] || die "找不到 manifest.json（用 --package-dir 指定包目录）"

PACKAGE_ID="$(manifest_get "$MANIFEST" package_id)"
APP_IMAGE="$(manifest_get "$MANIFEST" app_image.tag)"
APP_IMAGE_ID="$(manifest_get "$MANIFEST" app_image.id)"
MIGRATION_HEAD="$(manifest_get "$MANIFEST" migration_head)"
SOURCE_DIGEST="$(manifest_get "$MANIFEST" source_digest)"
FRONTEND_DIGEST="$(manifest_get "$MANIFEST" frontend_digest)"
APP_PORT="$(sed -n 's/^APP_PORT=//p' "$INSTALL_ROOT/.env" | tail -1)"
POSTGRES_USER="$(sed -n 's/^POSTGRES_USER=//p' "$INSTALL_ROOT/.env" | tail -1)"
POSTGRES_DB="$(sed -n 's/^POSTGRES_DB=//p' "$INSTALL_ROOT/.env" | tail -1)"

COMPOSE=(docker compose --project-name reqdoc --project-directory "$INSTALL_ROOT" -f "$INSTALL_ROOT/docker-compose.yml")
FAIL=0
mark() { [ "$1" -eq 0 ] || FAIL=1; }

step "发布后自检（包标识 ${PACKAGE_ID}）"

# 1 对外服务的容器用的确实是包里那个镜像（不是同名旧镜像，也不是别处来的镜像）。
RUNNING_IMAGE_ID="$("${COMPOSE[@]}" ps -q api | head -1 | xargs -r docker inspect --format '{{.Image}}')"
compare_value "api 容器镜像 ID" "$APP_IMAGE_ID" "${RUNNING_IMAGE_ID:-<容器未运行>}"; mark $?
WORKER_IMAGE_ID="$("${COMPOSE[@]}" ps -q worker | head -1 | xargs -r docker inspect --format '{{.Image}}')"
compare_value "worker 容器镜像 ID" "$APP_IMAGE_ID" "${WORKER_IMAGE_ID:-<容器未运行>}"; mark $?

# 2 镜像内源码逐文件指纹重算：文件写错位置、载荷与清单不一致、删除清单漏执行都在此暴露。
ACTUAL_SOURCE_DIGEST="$("${COMPOSE[@]}" exec -T api sh -c \
  'cd /app && find app alembic tools -type f ! -path "*/__pycache__/*" -printf "%p\n" | LC_ALL=C sort | xargs -r -d "\n" sha256sum' \
  | sha256sum | awk '{print $1}')"
compare_value "镜像内源码汇总指纹" "$SOURCE_DIGEST" "$ACTUAL_SOURCE_DIGEST"; mark $?

# 3 镜像内补丁台账首行与包标识一致（发现绕过脚本手工换镜像的情况）。
PATCHES_HEAD="$("${COMPOSE[@]}" exec -T api sh -c 'head -1 /app/PATCHES.txt' 2>/dev/null | tr -d '\r')"
case "$PATCHES_HEAD" in
  *"package_id=${PACKAGE_ID}"*) printf '  [一致] %-28s %s\n' "镜像内台账首行" "$PATCHES_HEAD" ;;
  *) printf '  [不符] %-28s 期望含 package_id=%s 实测=%s\n' "镜像内台账首行" "$PACKAGE_ID" "${PATCHES_HEAD:-<空>}"; FAIL=1 ;;
esac

# 4 宿主机前端产物目录整体指纹：解压不完整、解错目录在此暴露。
compare_value "前端产物目录指纹" "$FRONTEND_DIGEST" "$(dir_digest "$INSTALL_ROOT/frontend-dist")"; mark $?

# 5 数据库表结构确实迁到了本包对应的版本（迁移执行一半中断在此暴露）。
CURRENT_HEAD="$("${COMPOSE[@]}" exec -T api sh -c 'cd /app && uv run --no-sync alembic current 2>/dev/null' \
  | awk 'NF{print $1}' | tail -1)"
compare_value "数据库迁移头" "$MIGRATION_HEAD" "${CURRENT_HEAD:-<空>}"; mark $?

# 6 模板注册表非空：为空时发布功能直接不可用，但界面上要走到发布环节才暴露。
TEMPLATE_COUNT="$("${COMPOSE[@]}" exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
  'select count(*) from template_registry' 2>/dev/null | tr -d '[:space:]')"
if [ -n "$TEMPLATE_COUNT" ] && [ "$TEMPLATE_COUNT" -gt 0 ] 2>/dev/null; then
  printf '  [通过] %-28s %s 条\n' "模板注册表" "$TEMPLATE_COUNT"
else
  printf '  [不符] %-28s 期望 >0 实测=%s\n' "模板注册表" "${TEMPLATE_COUNT:-<查询失败>}"; FAIL=1
fi

# 7 图形与文档工具链在对外服务的镜像里就位（缺失时导出的图会静默降级为源码文本）。
TOOLS="$("${COMPOSE[@]}" exec -T api sh -c '
  for c in java mmdc google-chrome dot soffice; do
    command -v $c >/dev/null 2>&1 && printf "%s=ok " "$c" || printf "%s=missing " "$c"
  done
  test -f /app/tools/plantuml.jar && printf "plantuml.jar=ok" || printf "plantuml.jar=missing"
')"
if printf '%s' "$TOOLS" | grep -q missing; then
  printf '  [不符] %-28s %s\n' "镜像内工具链" "$TOOLS"; FAIL=1
else
  printf '  [通过] %-28s %s\n' "镜像内工具链" "$TOOLS"
fi

# 8 健康端点：对外端口上真的有服务在应答，且自报 ready。
HEALTH="$(curl -s --max-time 8 "http://127.0.0.1:${APP_PORT}/api/health" || true)"
case "$HEALTH" in
  *'"status":"ok"'*) printf '  [通过] %-28s %s\n' "健康端点" "$HEALTH" ;;
  *) printf '  [不符] %-28s %s\n' "健康端点" "${HEALTH:-<无响应>}"; FAIL=1 ;;
esac

# 9 前端页面确实由本进程同源提供（返回 HTML 而非 404）。
INDEX_CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "http://127.0.0.1:${APP_PORT}/")"
compare_value "首页 HTTP 状态" "200" "$INDEX_CODE"; mark $?

# 10 异步任务 worker 已在 Redis 注册（容器在跑但 worker 没起来，任务会静默排队）。
WORKER_COUNT="$("${COMPOSE[@]}" exec -T redis redis-cli --no-raw SMEMBERS rq:workers 2>/dev/null | grep -c 'rq:worker:' || true)"
if [ "${WORKER_COUNT:-0}" -ge 1 ]; then
  printf '  [通过] %-28s %s 个\n' "已注册 RQ worker" "$WORKER_COUNT"
else
  printf '  [不符] %-28s 期望 ≥1 实测=%s\n' "已注册 RQ worker" "${WORKER_COUNT:-0}"; FAIL=1
fi

echo
if [ "$FAIL" -eq 0 ]; then
  log "自检全部通过：本次发布判定为成功"
  exit 0
fi
die "自检存在不通过项（见上方逐项输出）"
