#!/usr/bin/env bash
# 现状查询（离线机执行）：当前装的是哪个包、各服务什么状态、状态文件与镜像内台账是否互证一致。
#
# 用法：bash scripts/status.sh --install-root <安装目录>
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib-common.sh
source "$SCRIPT_DIR/lib-common.sh"
REQDOC_LOG_PREFIX="status"

INSTALL_ROOT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --install-root) INSTALL_ROOT="$(cd "$2" && pwd)"; shift 2 ;;
    -h|--help) sed -n '2,5p' "$0"; exit 0 ;;
    *) die "未知参数：$1" ;;
  esac
done
[ -n "$INSTALL_ROOT" ] || die "必须指定 --install-root <安装目录>"
STATE="$INSTALL_ROOT/state/deployed.json"
[ -f "$STATE" ] || die "找不到状态文件 ${STATE}：该目录尚未完成安装"

PACKAGE_ID="$(manifest_get "$STATE" package_id)"
APP_IMAGE="$(manifest_get "$STATE" app_image.tag)"
APP_PORT="$(manifest_get "$STATE" app_port)"

echo "安装目录      ${INSTALL_ROOT}"
echo "包标识        ${PACKAGE_ID}"
echo "应用镜像      ${APP_IMAGE}"
echo "安装时间      $(manifest_get "$STATE" installed_at)"
echo "迁移头        $(manifest_get "$STATE" migration_head)"
echo "对外端口      ${APP_PORT}"

COMPOSE=(docker compose --project-name reqdoc --project-directory "$INSTALL_ROOT" -f "$INSTALL_ROOT/docker-compose.yml")
echo
echo "— 服务状态 —"
# 列依次是：服务、运行状态、健康探测结果（无探测则 -）、对外端口（无则 -）。
# 不用 compose 的 table 格式：其表头对 .Health 渲染成 <no value>，看着像故障。
"${COMPOSE[@]}" ps --format '{{.Service}}\t{{.State}}\t{{.Health}}\t{{.Ports}}' 2>/dev/null \
  | awk -F'\t' '{printf "%-10s %-10s %-10s %s\n", $1, $2, ($3==""?"-":$3), ($4==""?"-":$4)}' || true

echo
echo "— 互证 —"
# 状态文件（宿主）与镜像内台账（镜像）是两处独立记录。不一致说明有人绕过脚本动过环境。
LEDGER="$("${COMPOSE[@]}" exec -T api sh -c 'head -1 /app/PATCHES.txt' 2>/dev/null | tr -d '\r' || true)"
echo "镜像内台账首行  ${LEDGER:-<读取失败：api 容器未运行>}"
case "$LEDGER" in
  *"package_id=${PACKAGE_ID}"*) echo "互证结果        一致" ;;
  "") echo "互证结果        无法判定（api 容器未运行）" ;;
  *) warn "互证结果        不一致：状态文件记 ${PACKAGE_ID}，镜像内台账为 ${LEDGER}" ;;
esac

echo
echo "— 健康端点 —"
curl -s --max-time 5 "http://127.0.0.1:${APP_PORT}/api/health" || echo "<无响应>"
echo
