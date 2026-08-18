#!/usr/bin/env bash
# 数据库备份（离线机执行）。升级前必做：31 个迁移脚本中有 downgrade 为空实现，
# 回退函数不可依赖，备份是唯一可靠的数据回滚手段。
#
# 用法：bash scripts/backup-db.sh --install-root <安装目录> [--label 说明]
# 产物：<安装目录>/backups/reqdoc-<时间戳>[-说明].dump（pg_dump 自定义格式，用 pg_restore 恢复）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib-common.sh
source "$SCRIPT_DIR/lib-common.sh"
REQDOC_LOG_PREFIX="backup-db"

INSTALL_ROOT=""
LABEL=""
while [ $# -gt 0 ]; do
  case "$1" in
    --install-root) INSTALL_ROOT="$(cd "$2" && pwd)"; shift 2 ;;
    --label) LABEL="-$2"; shift 2 ;;
    -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
    *) die "未知参数：$1" ;;
  esac
done
[ -n "$INSTALL_ROOT" ] || die "必须指定 --install-root <安装目录>"

POSTGRES_USER="$(sed -n 's/^POSTGRES_USER=//p' "$INSTALL_ROOT/.env" | tail -1)"
POSTGRES_DB="$(sed -n 's/^POSTGRES_DB=//p' "$INSTALL_ROOT/.env" | tail -1)"
COMPOSE=(docker compose --project-name reqdoc --project-directory "$INSTALL_ROOT" -f "$INSTALL_ROOT/docker-compose.yml")

mkdir -p "$INSTALL_ROOT/backups"
OUT="$INSTALL_ROOT/backups/reqdoc-$(date +%Y%m%d-%H%M%S)${LABEL}.dump"

"${COMPOSE[@]}" exec -T db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > "$OUT" \
  || { rm -f "$OUT"; die "备份失败（db 容器是否在运行？）"; }

SIZE="$(stat -c %s "$OUT")"
[ "$SIZE" -gt 0 ] || { rm -f "$OUT"; die "备份文件为空，判定失败"; }
sha256_of_file "$OUT" > "${OUT}.sha256"
log "备份完成：${OUT}（$(numfmt --to=iec-i --suffix=B "$SIZE" 2>/dev/null || echo "${SIZE}B")）"
log "恢复方法：docker compose ... exec -T db pg_restore -U ${POSTGRES_USER} -d ${POSTGRES_DB} --clean --if-exists < ${OUT}"
