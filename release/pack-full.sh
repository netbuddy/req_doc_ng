#!/usr/bin/env bash
# 打全量离线包（开发机执行）。
#
# 用法：
#   release/pack-full.sh --dest <ssh目标>:<目录> [--package-id full-YYYYMMDD-NN] [--allow-dirty] [--skip-frontend-build]
# 例：
#   release/pack-full.sh --dest user@target-host:/opt/reqdoc-packages
#
# 磁盘纪律：镜像归档与前端产物包**不在开发机落盘**——docker save 与 tar 的输出经 ssh 管道直接写入
# 目标机；本地只用命名管道（不占空间）旁路计算一次 sha256，用于与目标机上落地文件的 sha256 比对，
# 从而端到端确认传输无损。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=lib-common.sh
source "$SCRIPT_DIR/lib-common.sh"
REQDOC_LOG_PREFIX="pack-full"

DEST=""
PACKAGE_ID=""
ALLOW_DIRTY=0
SKIP_FRONTEND_BUILD=0

while [ $# -gt 0 ]; do
  case "$1" in
    --dest) DEST="$2"; shift 2 ;;
    --package-id) PACKAGE_ID="$2"; shift 2 ;;
    --allow-dirty) ALLOW_DIRTY=1; shift ;;
    --skip-frontend-build) SKIP_FRONTEND_BUILD=1; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) die "未知参数：$1" ;;
  esac
done

[ -n "$DEST" ] || die "必须指定 --dest <ssh目标>:<目录>"
DEST_HOST="${DEST%%:*}"
DEST_ROOT="${DEST#*:}"
[ "$DEST_HOST" != "$DEST_ROOT" ] || die "--dest 格式应为 <ssh目标>:<目录>，例：user@target-host:/opt/reqdoc-packages"

require_cmd git tar ssh sha256sum python3 npm
check_docker

# --------------------------------------------------------------- 出包前门禁 --
step "出包前检查"

# 工作区门禁：只看进入镜像与包的路径（后端、前端、发布脚本、编排文件）。
# 文档目录的未提交改动不影响包内容，不拦。包的身份锚定在 git 提交号上，
# 构建面不干净时 git_commit 字段就是一句假话。
DIRTY="$(cd "$REPO_ROOT" && git status --porcelain -- backend frontend release docker-compose.yml)"
if [ -n "$DIRTY" ]; then
  if [ "$ALLOW_DIRTY" -eq 1 ]; then
    warn "构建面存在未提交改动，已按 --allow-dirty 放行，manifest 的 git_commit 将带 -dirty 标注："
    printf '%s\n' "$DIRTY" >&2
  else
    printf '%s\n' "$DIRTY" >&2
    die "构建面（backend/ frontend/ release/ docker-compose.yml）有未提交改动。先提交，或显式加 --allow-dirty。"
  fi
fi

GIT_COMMIT="$(cd "$REPO_ROOT" && git rev-parse HEAD)"
[ -z "$DIRTY" ] || GIT_COMMIT="${GIT_COMMIT}-dirty"
BUILT_AT="$(date -Iseconds)"
[ -n "$PACKAGE_ID" ] || PACKAGE_ID="full-$(date +%Y%m%d)-01"
IMAGE_TAG="reqdoc-api:base-${PACKAGE_ID#full-}"
DB_IMAGE="pgvector/pgvector:pg16"
REDIS_IMAGE="redis:7"
PROBE_IMAGE="reqdoc-load-probe:1"
PKG_DIR="${DEST_ROOT}/${PACKAGE_ID}"

log "包标识：${PACKAGE_ID}"
log "git 提交：${GIT_COMMIT}"
log "后端镜像标签：${IMAGE_TAG}"
log "目标：${DEST_HOST}:${PKG_DIR}"

ssh "$DEST_HOST" "mkdir -p '$PKG_DIR/images' '$PKG_DIR/compose' '$PKG_DIR/scripts'" \
  || die "无法在 ${DEST_HOST} 上创建包目录（检查 ssh 连通性与写权限）"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# 流式发送：stdin → 目标机文件；同时在本地旁路算 sha256（命名管道，不落盘）。
# 用法：<产生字节的命令> | stream_to <目标机相对路径>
stream_to() {
  local rel="$1" fifo="$WORK/hash.fifo" hashfile="$WORK/hash.txt"
  rm -f "$fifo" "$hashfile"; mkfifo "$fifo"
  ( sha256sum < "$fifo" | awk '{print $1}' > "$hashfile" ) &
  local hpid=$!
  tee "$fifo" | ssh "$DEST_HOST" "cat > '$PKG_DIR/$rel'"
  wait "$hpid"
  local local_hash remote_hash size
  local_hash="$(cat "$hashfile")"
  read -r remote_hash size <<<"$(ssh "$DEST_HOST" "sha256sum '$PKG_DIR/$rel' | awk '{print \$1}' | tr '\n' ' '; stat -c %s '$PKG_DIR/$rel'")"
  [ "$local_hash" = "$remote_hash" ] \
    || die "传输校验不一致：${rel} 本地 sha256=${local_hash} 目标机 sha256=${remote_hash}"
  printf '%s  %s  %s\n' "$local_hash" "$size" "$rel" >> "$WORK/files.txt"
  log "已送达 ${rel}（$(numfmt --to=iec-i --suffix=B "$size" 2>/dev/null || echo "${size}B")，sha256 两侧一致）"
}

# ------------------------------------------------------------------ 构建镜像 --
step "构建后端镜像（含 LibreOffice 与图形工具链）"
docker build \
  --target api \
  --build-arg "PACKAGE_ID=${PACKAGE_ID}" \
  --build-arg "GIT_COMMIT=${GIT_COMMIT}" \
  --build-arg "BUILT_AT=${BUILT_AT}" \
  -t "$IMAGE_TAG" \
  "$REPO_ROOT/backend"

step "构建镜像归档格式探针（FROM scratch，十几 KB）"
printf 'reqdoc offline image load probe\n' > "$WORK/probe.txt"
printf 'FROM scratch\nCOPY probe.txt /probe.txt\n' > "$WORK/Dockerfile"
docker build -q -t "$PROBE_IMAGE" "$WORK" >/dev/null

for img in "$DB_IMAGE" "$REDIS_IMAGE"; do
  docker image inspect "$img" >/dev/null 2>&1 || die "本地缺少镜像 ${img}，请先 docker pull 后重跑"
done

API_IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$IMAGE_TAG")"
DB_IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$DB_IMAGE")"
REDIS_IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$REDIS_IMAGE")"
API_IMAGE_SIZE="$(docker image inspect --format '{{.Size}}' "$IMAGE_TAG")"
log "后端镜像 ID：${API_IMAGE_ID}（$(numfmt --to=iec-i --suffix=B "$API_IMAGE_SIZE" 2>/dev/null || echo "$API_IMAGE_SIZE")）"

# ------------------------------------------------ 镜像内事实：源码指纹与迁移头 --
step "采集镜像内事实（源码逐文件指纹、迁移头、工具链自检）"
docker run --rm "$IMAGE_TAG" sh -c \
  'cd /app && find app alembic tools -type f ! -path "*/__pycache__/*" -printf "%p\n" | LC_ALL=C sort | xargs -r -d "\n" sha256sum' \
  > "$WORK/source-files.sha256"
SOURCE_DIGEST="$(sha256sum "$WORK/source-files.sha256" | awk '{print $1}')"
SOURCE_FILE_COUNT="$(wc -l < "$WORK/source-files.sha256")"
log "镜像内源码文件 ${SOURCE_FILE_COUNT} 个，汇总指纹 ${SOURCE_DIGEST}"

MIGRATION_HEAD="$(docker run --rm "$IMAGE_TAG" sh -c 'cd /app && uv run --no-sync alembic heads 2>/dev/null' | awk 'NF{print $1}' | tail -1)"
[ -n "$MIGRATION_HEAD" ] || die "无法从镜像内取得 alembic 迁移头"
log "迁移头：${MIGRATION_HEAD}"

TOOLCHAIN="$(docker run --rm "$IMAGE_TAG" sh -c '
  printf "java=%s\n" "$(java -version 2>&1 | head -1)"
  printf "mmdc=%s\n" "$(mmdc --version 2>/dev/null || echo missing)"
  printf "chromium=%s\n" "$(google-chrome --version 2>/dev/null || echo missing)"
  printf "dot=%s\n" "$(dot -V 2>&1 || echo missing)"
  printf "soffice=%s\n" "$(soffice --version 2>/dev/null | head -1 || echo missing)"
  printf "plantuml_jar=%s\n" "$(test -f /app/tools/plantuml.jar && echo present || echo missing)"
')"
printf '%s\n' "$TOOLCHAIN" >&2
printf '%s\n' "$TOOLCHAIN" | grep -q 'missing' && die "镜像内工具链不完整（见上方逐项输出），拒绝出包"

# ------------------------------------------------------------------ 前端产物 --
step "构建前端生产产物"
if [ "$SKIP_FRONTEND_BUILD" -eq 0 ]; then
  ( cd "$REPO_ROOT/frontend" && npm run build )
fi
[ -f "$REPO_ROOT/frontend/dist/index.html" ] || die "未找到 frontend/dist/index.html，前端构建未产出"
FRONTEND_DIGEST="$(dir_digest "$REPO_ROOT/frontend/dist")"
FRONTEND_FILES="$(cd "$REPO_ROOT/frontend/dist" && find . -type f | wc -l)"
log "前端产物 ${FRONTEND_FILES} 个文件，目录指纹 ${FRONTEND_DIGEST}"

# --------------------------------------------------------------- 流式发送包 --
step "导出并发送镜像归档（本地不落盘）"
docker save "$PROBE_IMAGE"  | stream_to "images/probe.tar"
docker save "$IMAGE_TAG"    | stream_to "images/reqdoc-api.tar"
docker save "$DB_IMAGE"     | stream_to "images/pgvector-pg16.tar"
docker save "$REDIS_IMAGE"  | stream_to "images/redis-7.tar"

step "发送前端产物与脚本"
tar -C "$REPO_ROOT/frontend" -czf - dist | stream_to "frontend-dist.tar.gz"
cat "$WORK/source-files.sha256" | stream_to "source-files.sha256"
for f in lib-common.sh install-full.sh verify-release.sh status.sh backup-db.sh; do
  cat "$SCRIPT_DIR/$f" | stream_to "scripts/$f"
done
cat "$SCRIPT_DIR/compose/docker-compose.offline.yml" | stream_to "compose/docker-compose.offline.yml"
cat "$SCRIPT_DIR/compose/env.template" | stream_to "compose/env.template"
cat "$SCRIPT_DIR/INSTALL.md" | stream_to "INSTALL.md"

# ------------------------------------------------------------------ manifest --
step "生成 manifest 与校验和清单"
UV_LOCK_HASH="$(sha256_of_file "$REPO_ROOT/backend/uv.lock")"
DOCKERFILE_HASH="$(sha256_of_file "$REPO_ROOT/backend/Dockerfile")"

python3 - "$WORK/manifest.json" <<PY
import json, sys, subprocess

files = []
with open("$WORK/files.txt") as fh:
    for line in fh:
        sha, size, path = line.split()
        files.append({"path": path, "sha256": sha, "size": int(size)})

manifest = {
    "package_type": "full",
    "package_id": "$PACKAGE_ID",
    "git_commit": "$GIT_COMMIT",
    "built_at": "$BUILT_AT",
    "built_by": "$(id -un)",
    "build_host": "$(hostname)",
    "docker_version": "$(docker version --format '{{.Server.Version}}')",
    "uv_lock_hash": "$UV_LOCK_HASH",
    "dockerfile_hash": "$DOCKERFILE_HASH",
    "app_image": {"tag": "$IMAGE_TAG", "id": "$API_IMAGE_ID", "size": $API_IMAGE_SIZE,
                  "archive": "images/reqdoc-api.tar"},
    "db_image": {"tag": "$DB_IMAGE", "id": "$DB_IMAGE_ID", "archive": "images/pgvector-pg16.tar"},
    "redis_image": {"tag": "$REDIS_IMAGE", "id": "$REDIS_IMAGE_ID", "archive": "images/redis-7.tar"},
    "probe_image": {"tag": "$PROBE_IMAGE", "archive": "images/probe.tar"},
    "migration_head": "$MIGRATION_HEAD",
    "source_digest": "$SOURCE_DIGEST",
    "source_file_count": $SOURCE_FILE_COUNT,
    "frontend_digest": "$FRONTEND_DIGEST",
    "frontend_file_count": $FRONTEND_FILES,
    "files": files,
}
with open(sys.argv[1], "w") as fh:
    json.dump(manifest, fh, ensure_ascii=False, indent=2)
PY

cat "$WORK/manifest.json" | ssh "$DEST_HOST" "cat > '$PKG_DIR/manifest.json'"
# SHA256SUMS 在目标机就地生成（覆盖包内除自身外的全部文件），随后与本地在传输中算得的
# 逐文件指纹逐条比对——两处独立计算一致，才说明包内容与声明一致。
ssh "$DEST_HOST" "cd '$PKG_DIR' && find . -type f ! -name SHA256SUMS -printf '%P\n' | LC_ALL=C sort | xargs -r -d '\n' sha256sum > SHA256SUMS"

step "出包自校验"
# 把目标机就地生成的清单取回本地比对：传输中算得的指纹与包内清单声明的指纹逐条一致，
# 才说明「包里是什么」与「清单说是什么」是同一件事。
ssh "$DEST_HOST" "cat '$PKG_DIR/SHA256SUMS'" > "$WORK/remote-sums.txt"
MISMATCH=0
while read -r sha size rel; do
  remote_sha="$(awk -v p="$rel" '$2==p || $2=="./"p {print $1}' "$WORK/remote-sums.txt")"
  if [ "$sha" != "$remote_sha" ]; then
    warn "校验和不一致：${rel} 传输中=${sha} 包内清单=${remote_sha}"
    MISMATCH=1
  fi
done < "$WORK/files.txt"
[ "$MISMATCH" -eq 0 ] || die "出包自校验失败，包不可用"

TOTAL="$(ssh "$DEST_HOST" "du -sh '$PKG_DIR' | awk '{print \$1}'")"
step "打包完成"
log "包目录：${DEST_HOST}:${PKG_DIR}（合计 ${TOTAL}）"
log "下一步：在目标机执行 cd '${PKG_DIR}' && bash scripts/install-full.sh --package-dir '${PKG_DIR}' --install-root <安装目录>"
