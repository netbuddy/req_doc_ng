#!/usr/bin/env bash
# 全量离线安装（离线机执行）。
#
# 用法：
#   bash scripts/install-full.sh --package-dir <包目录> --install-root <安装目录> [--set KEY=VALUE ...] [--yes]
# 例：
#   bash scripts/install-full.sh --package-dir /home/yun/reqdoc-packages/full-20260727-01 \
#        --install-root /opt/reqdoc --set POSTGRES_PASSWORD=xxx --set LLM_BASE_URL=http://10.0.0.9:8084/v1
#
# 任一环境检查不通过即退出，不做半程安装。已安装过的环境请勿重复执行本脚本（升级走差分通道）。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib-common.sh
source "$SCRIPT_DIR/lib-common.sh"
REQDOC_LOG_PREFIX="install"

PACKAGE_DIR=""
INSTALL_ROOT=""
ASSUME_YES=0
declare -a SETS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --package-dir) PACKAGE_DIR="$(cd "$2" && pwd)"; shift 2 ;;
    --install-root) INSTALL_ROOT="$2"; shift 2 ;;
    --set) SETS+=("$2"); shift 2 ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) die "未知参数：$1" ;;
  esac
done

[ -n "$PACKAGE_DIR" ] || die "必须指定 --package-dir <包目录>"
[ -n "$INSTALL_ROOT" ] || die "必须指定 --install-root <安装目录>"
MANIFEST="$PACKAGE_DIR/manifest.json"
[ -f "$MANIFEST" ] || die "包目录内没有 manifest.json：${PACKAGE_DIR}"

PACKAGE_ID="$(manifest_get "$MANIFEST" package_id)"
APP_IMAGE="$(manifest_get "$MANIFEST" app_image.tag)"
APP_IMAGE_ID="$(manifest_get "$MANIFEST" app_image.id)"
DB_IMAGE="$(manifest_get "$MANIFEST" db_image.tag)"
DB_IMAGE_ID="$(manifest_get "$MANIFEST" db_image.id)"
REDIS_IMAGE="$(manifest_get "$MANIFEST" redis_image.tag)"
REDIS_IMAGE_ID="$(manifest_get "$MANIFEST" redis_image.id)"
PROBE_IMAGE="$(manifest_get "$MANIFEST" probe_image.tag)"

step "第 1 步 环境检查"
require_cmd tar sha256sum python3 find
check_docker
check_compose_v2
mkdir -p "$INSTALL_ROOT" || die "无法创建安装目录 ${INSTALL_ROOT}（权限不足时改用有写权限的路径）"
INSTALL_ROOT="$(cd "$INSTALL_ROOT" && pwd)"
check_disk "$INSTALL_ROOT" 10
DOCKER_ROOT="$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo /var/lib/docker)"
[ -d "$DOCKER_ROOT" ] && check_disk "$DOCKER_ROOT" 10

# 端口取值：命令行 --set APP_PORT 优先，其次已存在的 .env，最后模板默认。
ENV_FILE="$INSTALL_ROOT/.env"
APP_PORT="$(printf '%s\n' "${SETS[@]:-}" | sed -n 's/^APP_PORT=//p' | tail -1)"
if [ -z "$APP_PORT" ] && [ -f "$ENV_FILE" ]; then
  APP_PORT="$(sed -n 's/^APP_PORT=//p' "$ENV_FILE" | tail -1)"
fi
[ -n "$APP_PORT" ] || APP_PORT="$(sed -n 's/^APP_PORT=//p' "$PACKAGE_DIR/compose/env.template" | tail -1)"
check_port_free "$APP_PORT"

step "第 2 步 包完整性校验"
( cd "$PACKAGE_DIR" && sha256sum --quiet -c SHA256SUMS ) \
  || die "包内文件校验和与 SHA256SUMS 不符：包在传输或存放过程中损坏，请重新拷贝"
log "包内全部文件校验通过（包标识 ${PACKAGE_ID}）"

step "第 3 步 镜像归档格式探针"
# 先导入十几 KB 的探针镜像：归档格式与正式镜像完全一致，格式不兼容在此秒级暴露，
# 而不是等到 GB 级镜像导入数分钟后才失败、且环境已处于半程状态。
if ! docker load -i "$PACKAGE_DIR/images/probe.tar" >/dev/null 2>"$INSTALL_ROOT/probe-error.log"; then
  cat "$INSTALL_ROOT/probe-error.log" >&2
  die "探针镜像导入失败。本包的镜像归档为 OCI 布局且层数据经 gzip 压缩，当前 Docker 可能因版本较旧
        （低于 25）无法识别。请回传 'docker version' 与 'docker compose version' 的完整输出，
        开发侧将切换导出格式重新出包。本次安装未对环境做任何修改，可直接退出。"
fi
rm -f "$INSTALL_ROOT/probe-error.log"
docker rmi "$PROBE_IMAGE" >/dev/null 2>&1 || true
log "探针导入成功：镜像归档格式与本机 Docker 兼容"

step "第 4 步 导入镜像并核对镜像 ID"
for archive in images/reqdoc-api.tar images/pgvector-pg16.tar images/redis-7.tar; do
  log "导入 ${archive} …"
  docker load -i "$PACKAGE_DIR/$archive" >/dev/null
done
FAIL=0
compare_value "应用镜像 ID" "$APP_IMAGE_ID" "$(docker image inspect --format '{{.Id}}' "$APP_IMAGE")" || FAIL=1
compare_value "数据库镜像 ID" "$DB_IMAGE_ID" "$(docker image inspect --format '{{.Id}}' "$DB_IMAGE")" || FAIL=1
compare_value "Redis 镜像 ID" "$REDIS_IMAGE_ID" "$(docker image inspect --format '{{.Id}}' "$REDIS_IMAGE")" || FAIL=1
[ "$FAIL" -eq 0 ] || die "导入的镜像与包声明不一致，安装中止"

step "第 5 步 部署编排与环境变量"
mkdir -p "$INSTALL_ROOT/state/logs" "$INSTALL_ROOT/backups" "$INSTALL_ROOT/frontend-dist"
cp "$PACKAGE_DIR/compose/docker-compose.offline.yml" "$INSTALL_ROOT/docker-compose.yml"
cp -r "$PACKAGE_DIR/scripts" "$INSTALL_ROOT/scripts"
cp "$PACKAGE_DIR/manifest.json" "$INSTALL_ROOT/state/manifest-${PACKAGE_ID}.json"
[ -f "$ENV_FILE" ] || cp "$PACKAGE_DIR/compose/env.template" "$ENV_FILE"
chmod 600 "$ENV_FILE"

# 写入/覆盖环境变量：镜像标签与端口由包决定，其余由 --set 传入或现场编辑 .env。
set_env() {
  local key="$1" value="$2"
  if grep -q "^${key}=" "$ENV_FILE"; then
    python3 - "$ENV_FILE" "$key" "$value" <<'PY'
import sys
path, key, value = sys.argv[1], sys.argv[2], sys.argv[3]
lines = open(path).read().splitlines(True)
out = [f"{key}={value}\n" if l.startswith(f"{key}=") else l for l in lines]
open(path, "w").writelines(out)
PY
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}
set_env REQDOC_IMAGE "$APP_IMAGE"
set_env REQDOC_DB_IMAGE "$DB_IMAGE"
set_env REQDOC_REDIS_IMAGE "$REDIS_IMAGE"
set_env APP_PORT "$APP_PORT"
for kv in "${SETS[@]:-}"; do
  [ -n "$kv" ] || continue
  set_env "${kv%%=*}" "${kv#*=}"
done

PG_PASS="$(sed -n 's/^POSTGRES_PASSWORD=//p' "$ENV_FILE" | tail -1)"
[ -n "$PG_PASS" ] && [ "$PG_PASS" != "CHANGE-ME-BEFORE-INSTALL" ] \
  || die "请先设置数据库口令：编辑 ${ENV_FILE} 的 POSTGRES_PASSWORD，或安装时加 --set POSTGRES_PASSWORD=<口令>"

LLM_URL="$(sed -n 's/^LLM_BASE_URL=//p' "$ENV_FILE" | tail -1)"
if [ -z "$LLM_URL" ]; then
  warn "未配置 LLM_BASE_URL：AI 对话与要素识别将返回占位内容、不调用真实模型（其余功能不受影响）。"
  if [ "$ASSUME_YES" -eq 0 ]; then
    read -r -p "确认以降级形态继续安装？[y/N] " ans
    [ "$ans" = "y" ] || [ "$ans" = "Y" ] || die "已按操作员选择中止，可填好 ${ENV_FILE} 后重跑"
  fi
else
  if command -v curl >/dev/null 2>&1; then
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "${LLM_URL%/}/models" || echo 000)"
    [ "$code" = "200" ] && log "推理端点连通性检查通过（${LLM_URL%/}/models → 200）" \
      || warn "推理端点 ${LLM_URL} 探测返回 ${code}：请确认地址与网络可达（不阻断安装）"
  fi
fi

step "第 6 步 部署前端产物"
# 清空目录内容而非删除目录本身：该目录是容器的挂载点，删除重建会换 inode，
# 容器会继续挂在旧目录上，页面更新静默失效。
find "$INSTALL_ROOT/frontend-dist" -mindepth 1 -delete
tar -xzf "$PACKAGE_DIR/frontend-dist.tar.gz" -C "$INSTALL_ROOT/frontend-dist" --strip-components=1
log "前端产物已展开：$(find "$INSTALL_ROOT/frontend-dist" -type f | wc -l) 个文件"

step "第 7 步 数据库迁移与模板初始化"
COMPOSE=(docker compose --project-name reqdoc --project-directory "$INSTALL_ROOT" -f "$INSTALL_ROOT/docker-compose.yml")
"${COMPOSE[@]}" up -d db redis
log "等待数据库与 Redis 就绪 …"
for i in $(seq 1 60); do
  ok_db="$("${COMPOSE[@]}" ps --format '{{.Service}} {{.Health}}' | awk '$1=="db"{print $2}')"
  ok_redis="$("${COMPOSE[@]}" ps --format '{{.Service}} {{.Health}}' | awk '$1=="redis"{print $2}')"
  [ "$ok_db" = "healthy" ] && [ "$ok_redis" = "healthy" ] && break
  sleep 2
  [ "$i" -lt 60 ] || die "数据库或 Redis 未在 120 秒内就绪（db=${ok_db} redis=${ok_redis}）"
done
log "执行数据库迁移 …"
"${COMPOSE[@]}" run --rm migrate || die "数据库迁移失败，安装中止（服务未启动，可修正后重跑）"
log "导入内置文档模板 …"
"${COMPOSE[@]}" run --rm template-init || die "模板初始化失败，安装中止（发布功能依赖模板注册表非空）"

step "第 8 步 启动应用"
"${COMPOSE[@]}" up -d api worker
log "等待应用健康端点就绪 …"
for i in $(seq 1 60); do
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:${APP_PORT}/api/health" 2>/dev/null || echo 000)"
  [ "$code" = "200" ] && break
  sleep 2
  [ "$i" -lt 60 ] || die "应用在 120 秒内未就绪（最后一次 /api/health 返回 ${code}）。查看日志：${COMPOSE[*]} logs api"
done
log "应用已就绪：http://<本机地址>:${APP_PORT}/"

step "第 9 步 安装后自检"
bash "$INSTALL_ROOT/scripts/verify-release.sh" --install-root "$INSTALL_ROOT" --package-dir "$PACKAGE_DIR" \
  || die "安装后自检未通过，请按上方逐项输出处置（服务已启动，但本次安装不判定为成功）"

step "第 10 步 写入状态文件"
python3 - "$INSTALL_ROOT/state/deployed.json" <<PY
import json, os, time
state_path = "$INSTALL_ROOT/state/deployed.json"
record = {
    "package_id": "$PACKAGE_ID",
    "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "app_image": {"tag": "$APP_IMAGE", "id": "$APP_IMAGE_ID"},
    "db_image": "$DB_IMAGE",
    "redis_image": "$REDIS_IMAGE",
    "migration_head": "$(manifest_get "$MANIFEST" migration_head)",
    "source_digest": "$(manifest_get "$MANIFEST" source_digest)",
    "frontend_digest": "$(manifest_get "$MANIFEST" frontend_digest)",
    "app_port": "$APP_PORT",
    "install_root": "$INSTALL_ROOT",
    "package_dir": "$PACKAGE_DIR",
}
history = []
if os.path.exists(state_path):
    try:
        history = json.load(open(state_path)).get("history", [])
    except Exception:
        history = []
history.append({"action": "install-full", **{k: record[k] for k in ("package_id", "installed_at")}})
record["history"] = history
json.dump(record, open(state_path, "w"), ensure_ascii=False, indent=2)
PY
log "状态文件：${INSTALL_ROOT}/state/deployed.json"

step "安装完成"
log "包标识 ${PACKAGE_ID}｜访问地址 http://<本机地址>:${APP_PORT}/"
log "现状查询：bash ${INSTALL_ROOT}/scripts/status.sh --install-root ${INSTALL_ROOT}"
