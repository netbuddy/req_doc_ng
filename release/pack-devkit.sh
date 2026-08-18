#!/usr/bin/env bash
# 打全量开发环境离线包（联网开发机执行）。
#
# 产物＝一个 tar.gz。拷到离线 Linux x86_64 机器解压，运行包内 install-devkit.sh，
# 即可完全离线重建开发调试环境，不需要再下载任何依赖。包内容：
#   源码树、uv 二进制、Node.js 运行时、uv 管理版 CPython 3.12、
#   后端全部依赖（uv 缓存形态，含 dev 组）、前端全部依赖（npm 缓存形态）、
#   图形渲染工具链（Temurin JRE 供 plantuml、mermaid-cli + chrome-headless-shell
#   供 mermaid、Noto CJK 中文字体供两者渲染中文；字体为 SIL OFL 许可，可随包分发）。
# 数据库不随本包：复用全量发布包（pack-full.sh 产物）装好的 db/redis 容器——
# 安装器会停掉其 api/worker 业务容器、给 db/redis 加宿主端口发布、把旧库表
# 增量迁移到本包代码的最新版本（详见 install-devkit.sh）。
# LibreOffice 同样不随包：安装器生成转发脚本，docx→PDF 在全量包镜像内的一次性容器里执行。
#
# 用法：
#   release/pack-devkit.sh [--output <目录>] [--package-id devkit-YYYYMMDD-NN]
#     --output      产物 tar.gz 落地目录（默认当前目录）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=lib-common.sh
source "$SCRIPT_DIR/lib-common.sh"
# shellcheck disable=SC2034  # lib-common.sh 的 log()/warn()/die() 读取本变量
REQDOC_LOG_PREFIX="pack-devkit"

NODE_MAJOR=22                       # Vite 8 下限 20.19；随包发 22 LTS

OUTPUT_DIR="$PWD"
PACKAGE_ID=""
while [ $# -gt 0 ]; do
  case "$1" in
    --output) OUTPUT_DIR="$2"; shift 2 ;;
    --package-id) PACKAGE_ID="$2"; shift 2 ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) die "未知参数：$1" ;;
  esac
done
[ -n "$PACKAGE_ID" ] || PACKAGE_ID="devkit-$(date +%Y%m%d)-01"

require_cmd git tar sha256sum python3 npm uv curl
[ "$(uname -s)/$(uname -m)" = "Linux/x86_64" ] || die "本脚本在 Linux x86_64 上出包（包与出包机同平台）。"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
PKG="$STAGE/$PACKAGE_ID"
mkdir -p "$PKG/vendor/uv" "$PKG/vendor/node" "$PKG/vendor/uv-cache" "$PKG/vendor/npm-cache"

step "1/6 源码树"
if git -C "$REPO_ROOT" rev-parse HEAD >/dev/null 2>&1; then
  GIT_REF="$(git -C "$REPO_ROOT" rev-parse HEAD)"
  git -C "$REPO_ROOT" archive --format=tar.gz -o "$PKG/repo.tar.gz" HEAD
else
  # 仓库尚无提交时按暂存区打包（新迁出仓的初始状态）
  GIT_REF="staged-uncommitted"
  (cd "$REPO_ROOT" && git ls-files -z | tar --null -T - -czf "$PKG/repo.tar.gz")
fi
log "源码基准：$GIT_REF"

step "2/6 uv 二进制与管理版 CPython 3.12"
cp "$(command -v uv)" "$PKG/vendor/uv/uv"
chmod +x "$PKG/vendor/uv/uv"
UV_VERSION="$(uv --version | awk '{print $2}')"
uv python install 3.12 >/dev/null 2>&1 || true   # 本机已有管理版时瞬时返回
PY_BIN="$(UV_PYTHON_PREFERENCE=only-managed uv python find 3.12)" \
  || die "uv 未能提供管理版 CPython 3.12（先联网执行 uv python install 3.12）"
# readlink -f 必须有：uv 返回的常是不带补丁号的符号链接目录（cpython-3.12-… → cpython-3.12.13-…），
# 不解析就 tar 的话只会存进一个 163 字节的符号链接（2026-08-17 实测踩坑）
PY_HOME="$(readlink -f "$(dirname "$PY_BIN")/..")"
PY_NAME="$(basename "$PY_HOME")"
log "打入 CPython：$PY_NAME"
tar -C "$(dirname "$PY_HOME")" -czf "$PKG/vendor/cpython.tar.gz" "$PY_NAME"

step "3/6 后端依赖（uv 缓存，按 uv.lock 全量预热，含 dev 组）"
UV_CACHE_DIR="$PKG/vendor/uv-cache" \
  UV_PROJECT_ENVIRONMENT="$STAGE/venv-throwaway" \
  UV_PYTHON="$PY_BIN" \
  uv sync --frozen --directory "$REPO_ROOT/backend"
rm -rf "$STAGE/venv-throwaway"

step "4/6 前端依赖（npm 缓存，按 package-lock.json 全量预热）"
FE_TMP="$STAGE/fe-throwaway"
mkdir -p "$FE_TMP"
cp "$REPO_ROOT/frontend/package.json" "$REPO_ROOT/frontend/package-lock.json" "$FE_TMP/"
(cd "$FE_TMP" && npm ci --cache "$PKG/vendor/npm-cache" --no-audit --no-fund --loglevel=error)
rm -rf "$FE_TMP"

step "5/6 Node.js ${NODE_MAJOR}.x 运行时"
NODE_VERSION="$(curl -fsSL https://nodejs.org/dist/index.json \
  | python3 -c "import json,sys; print(next(e['version'] for e in json.load(sys.stdin) if e['version'].startswith('v${NODE_MAJOR}.')))")" \
  || die "无法从 nodejs.org 解析 v${NODE_MAJOR} 最新版本号"
NODE_TAR="node-${NODE_VERSION}-linux-x64.tar.xz"
log "下载 ${NODE_TAR}"
curl -fL --retry 3 -o "$PKG/vendor/node/$NODE_TAR" "https://nodejs.org/dist/${NODE_VERSION}/${NODE_TAR}" \
  || curl -fL --retry 3 -o "$PKG/vendor/node/$NODE_TAR" \
       "https://registry.npmmirror.com/-/binary/node/${NODE_VERSION}/${NODE_TAR}" \
  || die "Node.js 下载失败（nodejs.org 与 npmmirror 均不可达）"

step "6/7 图形渲染工具链（JRE / mermaid-cli / chrome-headless-shell / 中文字体）"
# plantuml 渲染引擎：Temurin 21 JRE（linux x64，解压即用；plantuml.jar 本就在源码树 backend/tools/）
log '下载 Temurin 21 JRE'
curl -fL --retry 3 -o "$PKG/vendor/jre.tar.gz" \
  "https://api.adoptium.net/v3/binary/latest/21/ga/linux/x64/jre/hotspot/normal/eclipse" \
  || die "Temurin JRE 下载失败（api.adoptium.net 不可达）"
# mermaid-cli：装进临时前缀后整包收走（顺带把相关 npm 包预热进包内缓存）；
# PUPPETEER_SKIP_DOWNLOAD：浏览器内核单独打包，不让 puppeteer 往本机缓存里下
MMD_BUILD="$STAGE/mermaid-build"
mkdir -p "$MMD_BUILD"
(cd "$MMD_BUILD" && PUPPETEER_SKIP_DOWNLOAD=1 npm install "@mermaid-js/mermaid-cli" \
    --cache "$PKG/vendor/npm-cache" --no-audit --no-fund --loglevel=error)
tar -C "$MMD_BUILD" -czf "$PKG/vendor/mermaid.tar.gz" node_modules
# mermaid 的浏览器内核：chrome-headless-shell（Chrome 官方无头精简版，与包内 puppeteer 版本配套）
log '下载 chrome-headless-shell'
CHROME_DL="$STAGE/chrome-dl"
"$MMD_BUILD/node_modules/.bin/puppeteer" browsers install chrome-headless-shell --path "$CHROME_DL" >/dev/null
CHROME_BIN="$(find "$CHROME_DL" -name chrome-headless-shell -type f | head -1)"
[ -n "$CHROME_BIN" ] || die "chrome-headless-shell 下载失败"
tar -C "$CHROME_DL" -czf "$PKG/vendor/chrome-headless-shell.tar.gz" .
# 中文字体：plantuml 的 java2d 与无头 Chrome 渲染中文都靠它，离线机可能没有
FONT_SRC=/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc
[ -f "$FONT_SRC" ] || die "出包机缺 $FONT_SRC（apt install fonts-noto-cjk 后重跑）"
mkdir -p "$PKG/vendor/fonts"
cp "$FONT_SRC" "$PKG/vendor/fonts/"

step "7/7 安装器、校验与打包"
cp "$SCRIPT_DIR/install-devkit.sh" "$PKG/install-devkit.sh"
chmod +x "$PKG/install-devkit.sh"

# 校验清单：普通文件逐个 sha256（缓存目录文件数以万计，整体校验交给 tar 的解压完整性）
(cd "$PKG" && sha256sum repo.tar.gz vendor/uv/uv vendor/cpython.tar.gz "vendor/node/$NODE_TAR" \
  vendor/jre.tar.gz vendor/mermaid.tar.gz vendor/chrome-headless-shell.tar.gz \
  vendor/fonts/NotoSansCJK-Regular.ttc > checksums.txt)

python3 - "$PKG/manifest.json" <<EOF
import json, sys
json.dump({
    "package_id": "$PACKAGE_ID",
    "kind": "devkit",
    "built_at": "$(date -Iseconds)",
    "git_ref": "$GIT_REF",
    "platform": "linux-x86_64",
    "uv_version": "$UV_VERSION",
    "node_version": "$NODE_VERSION",
    "cpython": "$PY_NAME",
    "graphics": "Temurin21-JRE + mermaid-cli + chrome-headless-shell + NotoSansCJK",
    "database": "复用全量发布包的 db/redis 容器（install-devkit.sh --full-stack 对接）",
}, open(sys.argv[1], "w"), ensure_ascii=False, indent=2)
EOF

mkdir -p "$OUTPUT_DIR"
OUT_TAR="$OUTPUT_DIR/${PACKAGE_ID}.tar.gz"
log "打包 → $OUT_TAR（内容量 $(du -sh "$PKG" | cut -f1)，压缩需数分钟）"
if command -v pigz >/dev/null 2>&1; then
  tar -C "$STAGE" -cf - "$PACKAGE_ID" | pigz > "$OUT_TAR"
else
  tar -C "$STAGE" -czf "$OUT_TAR" "$PACKAGE_ID"
fi
log "完成：$OUT_TAR（$(du -sh "$OUT_TAR" | cut -f1)）"
log "sha256：$(sha256_of_file "$OUT_TAR")"
log "离线机使用：tar -xzf ${PACKAGE_ID}.tar.gz && cd ${PACKAGE_ID} && ./install-devkit.sh --dir ~/req_doc_ng"
