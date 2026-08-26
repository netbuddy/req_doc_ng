#!/usr/bin/env bash
# ReqDoc 需求治理平台 —— AppImage 打包（AppImage 单机模式方案 §5）。
# 用法：release/appimage/build.sh [x86_64|aarch64] [--with-font]
#   前置：frontend/dist 已构建（cd frontend && npm run build）；本机装有 uv、curl、tar。
#   aarch64 可在 x86_64 机器上交叉组装（依赖全是预编译轮子，不编译），但最终验收必须在真 arm64 机器上做。
#   --with-font：把出包机的 Noto CJK 字体打进包（目标机没有中文字体时 PlantUML 出图会缺字）。
set -euo pipefail
ARCH="${1:-x86_64}"; shift || true
WITH_FONT=0; [ "${1:-}" = "--with-font" ] && WITH_FONT=1
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
CACHE="$HERE/cache"; STAGE="$HERE/stage/$ARCH"; APPDIR="$STAGE/AppDir"; OUT="$HERE/out"
mkdir -p "$CACHE" "$OUT"
log()  { printf '[appimage] %s\n' "$*" >&2; }
die()  { printf '[appimage] 失败：%s\n' "$*" >&2; exit 1; }

# ---- 按架构选外部件 ----
PY_TAG="20260825"; PY_VER="3.12.14"
case "$ARCH" in
  x86_64)  PY_TRIPLE="x86_64-unknown-linux-gnu";  JRE_ARCH="x64";     UV_PLATFORM="x86_64-unknown-linux-gnu" ;;
  aarch64) PY_TRIPLE="aarch64-unknown-linux-gnu"; JRE_ARCH="aarch64"; UV_PLATFORM="aarch64-unknown-linux-gnu" ;;
  *) die "不支持的架构：$ARCH（只支持 x86_64 / aarch64）" ;;
esac
PY_TAR="cpython-${PY_VER}+${PY_TAG}-${PY_TRIPLE}-install_only_stripped.tar.gz"
JRE_TAR="temurin21-jre-${JRE_ARCH}.tar.gz"
RUNTIME="runtime-${ARCH}"
TOOL="appimagetool-x86_64.AppImage"   # 打包工具本身在出包机（x86_64）上跑，目标架构由 --runtime-file 决定

fetch() {  # fetch <文件名> <URL>
  [ -s "$CACHE/$1" ] && return 0
  log "下载 $1"
  curl -fL --retry 3 -o "$CACHE/$1.part" "$2" && mv "$CACHE/$1.part" "$CACHE/$1"
}
fetch "$PY_TAR" "https://github.com/astral-sh/python-build-standalone/releases/download/${PY_TAG}/${PY_TAR}"
fetch "$JRE_TAR" "https://api.adoptium.net/v3/binary/latest/21/ga/linux/${JRE_ARCH}/jre/hotspot/normal/eclipse"
fetch "$RUNTIME" "https://github.com/AppImage/type2-runtime/releases/download/continuous/${RUNTIME}"
fetch "$TOOL" "https://github.com/AppImage/appimagetool/releases/download/continuous/${TOOL}"
chmod +x "$CACHE/$TOOL"

[ -f "$ROOT/frontend/dist/index.html" ] || die "frontend/dist 不存在：先 cd frontend && npm run build"
command -v uv >/dev/null || die "缺 uv"

# ---- 组装 AppDir ----
rm -rf "$STAGE"; mkdir -p "$APPDIR/usr/app/backend" "$APPDIR/usr/app/frontend" "$APPDIR/usr/jre"
log "解压独立 Python"
tar -xzf "$CACHE/$PY_TAR" -C "$APPDIR/usr"          # 得到 AppDir/usr/python/
log "解压 JRE"
tar -xzf "$CACHE/$JRE_TAR" -C "$APPDIR/usr/jre" --strip-components=1
log "复制后端源码与 plantuml.jar"
rsync -a --exclude '__pycache__' --exclude '.venv' --exclude 'var' --exclude 'tests' \
  "$ROOT/backend/app" "$ROOT/backend/tools" "$ROOT/backend/pyproject.toml" "$APPDIR/usr/app/backend/"
rm -f "$APPDIR/usr/app/backend/tools/"*.sh "$APPDIR/usr/app/backend/tools/vulture_baseline.txt"
log "复制前端产物"
rsync -a "$ROOT/frontend/dist" "$APPDIR/usr/app/frontend/"

log "安装后端依赖（按 uv.lock 精确重建，目标平台 $UV_PLATFORM）"
(cd "$ROOT/backend" && uv export --frozen --no-dev --no-hashes --no-emit-project -o "$STAGE/requirements.txt" >/dev/null)
SITE="$APPDIR/usr/python/lib/python3.12/site-packages"
uv pip install --quiet --python-platform "$UV_PLATFORM" --python-version 3.12 --only-binary :all: \
  --target "$SITE" -r "$STAGE/requirements.txt"
find "$SITE" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

if [ "$WITH_FONT" = 1 ]; then
  FONT=/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc
  [ -f "$FONT" ] || die "出包机缺 $FONT（apt install fonts-noto-cjk）"
  mkdir -p "$APPDIR/usr/share/fonts"; cp "$FONT" "$APPDIR/usr/share/fonts/"
  cat > "$APPDIR/usr/share/fonts/fonts.conf" <<'XML'
<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig><include ignore_missing="yes">/etc/fonts/fonts.conf</include><dir prefix="default">.</dir></fontconfig>
XML
fi

cp "$HERE/AppRun" "$HERE/reqdoc.desktop" "$HERE/reqdoc.svg" "$APPDIR/"
chmod +x "$APPDIR/AppRun"
GIT_COMMIT="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
printf 'arch=%s\ngit_commit=%s\nbuilt_at=%s\npython=%s\n' "$ARCH" "$GIT_COMMIT" "$(date -u +%FT%TZ)" "$PY_VER" > "$APPDIR/usr/app/BUILD.txt"

# ---- 打包（static runtime：目标机不需要 libfuse2）----
OUTFILE="$OUT/ReqDoc-${GIT_COMMIT}-${ARCH}.AppImage"
log "打包 → $OUTFILE"
ARCH="$ARCH" "$CACHE/$TOOL" --appimage-extract-and-run --runtime-file "$CACHE/$RUNTIME" "$APPDIR" "$OUTFILE" 2>&1 | grep -v '^$' | tail -3 >&2
ls -la "$OUTFILE"
log "完成。试跑：$OUTFILE --no-browser；目标机无 FUSE 时也可 $OUTFILE --appimage-extract-and-run"
