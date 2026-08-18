#!/usr/bin/env bash
# 开发环境离线安装器（离线 Linux x86_64 机器执行；pack-devkit.sh 会把本脚本放进包根目录）。
# 自包含：不依赖仓库内其它脚本，不需要网络。
#
# 前置：数据库来自已安装的全量发布包（install-full.sh 装出的 /opt/reqdoc 形态）。
# 本安装器对接它：停掉 api/worker 两个业务容器（让位给本机开发进程）、给 db/redis
# 补宿主端口发布、把旧库表用 alembic 增量迁移到本包代码的最新版本。
# 没有全量包也能装（--no-db）：测试全跑内存 SQLite，只是起不了真实服务。
#
# 用法（在解压出的包目录里执行）：
#   ./install-devkit.sh [--dir <安装目录>] [--full-stack <全量包目录>] [--no-db]
#                       [--db-port N] [--redis-port N] [--rebuild-db] [--verify]
#     --dir         开发环境安装目录（默认 ~/req_doc_ng；须为空或不存在）
#     --full-stack  全量发布包的安装目录（默认自动探测 /opt/reqdoc）
#     --no-db       跳过数据库对接
#     --db-port     db 容器发布到宿主的端口（默认 5432；被占时换个值）
#     --redis-port  redis 容器发布到宿主的端口（默认 6379）
#     --rebuild-db  迁移前先清空整个业务库重建（丢弃全量包里的旧数据；默认只做增量迁移保数据）
#     --verify      安装完成后离线跑测试与生产构建，对照基线核验
#
# 安装完成后的日常使用：每个新终端先 source <安装目录>/dev-env.sh，
# 然后 make backend / make frontend / ./setup-linux.sh <任务> 照常使用。
# 恢复生产形态（必须带 --no-deps）：
#   cd <全量包目录> && docker compose --project-name reqdoc up -d --no-deps api worker
#   不带 --no-deps 会连带触发全量包的一次性 migrate 服务，而旧镜像的迁移链不认识
#   开发迁移后的新库版本号，会直接报错（2026-08-17 在目标机实测）。库表结构是加法式
#   演进，旧版应用跑在新库表上不受影响。
set -euo pipefail

PKG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
log()  { printf '[%s] [install-devkit] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; }
warn() { printf '[%s] [install-devkit] 警告：%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; }
die()  { printf '[%s] [install-devkit] 失败：%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; exit 1; }
step() { printf '\n===== %s =====\n' "$*" >&2; }

INSTALL_DIR="$HOME/req_doc_ng"
FULL_STACK_DIR=""
NO_DB=0
DB_PORT=5432
REDIS_PORT=6379
REBUILD_DB=0
VERIFY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dir) INSTALL_DIR="$2"; shift 2 ;;
    --full-stack) FULL_STACK_DIR="$2"; shift 2 ;;
    --no-db) NO_DB=1; shift ;;
    --db-port) DB_PORT="$2"; shift 2 ;;
    --redis-port) REDIS_PORT="$2"; shift 2 ;;
    --rebuild-db) REBUILD_DB=1; shift ;;
    --verify) VERIFY=1; shift ;;
    -h|--help) sed -n '2,26p' "$0"; exit 0 ;;
    *) die "未知参数：$1" ;;
  esac
done

step "0/6 前置检查"
[ "$(uname -s)/$(uname -m)" = "Linux/x86_64" ] || die "本包只适用于 Linux x86_64（当前：$(uname -s)/$(uname -m)）。"
for c in tar sha256sum xz; do command -v "$c" >/dev/null 2>&1 || die "缺少命令：$c"; done
# glibc 下限＝包内 Node.js 22 官方二进制的要求（Ubuntu 20.04 / Debian 10 / RHEL 8 及以上满足）
GLIBC_VER="$(getconf GNU_LIBC_VERSION 2>/dev/null | awk '{print $2}')"
if [ -n "$GLIBC_VER" ] && [ "$(printf '%s\n2.28\n' "$GLIBC_VER" | sort -V | head -1)" != "2.28" ]; then
  die "glibc $GLIBC_VER 低于 2.28（包内 Node.js 22 的下限）：请换更新的发行版。"
fi
# 以下都不阻断安装，只是把缺口说清楚
command -v git >/dev/null 2>&1 || warn "未装 git：不影响构建与运行，但没法做版本管理（git 需从发行版源安装）。"
command -v make >/dev/null 2>&1 || warn "未装 make：Makefile 快捷方式不可用；改用 ./setup-linux.sh start/stop 或直接敲 uv/npm 命令。"
command -v fuser >/dev/null 2>&1 || warn "未装 fuser（psmisc 包）：setup-linux.sh stop 的端口回收会跳过。"
command -v ss >/dev/null 2>&1 || warn "未装 ss（iproute2 包）：setup-linux.sh start 的端口占用守卫会跳过。"
if [ -e "$INSTALL_DIR" ] && [ -n "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]; then
  die "安装目录非空：$INSTALL_DIR（换 --dir 或清空后重试）"
fi
if [ "$NO_DB" -eq 0 ]; then
  [ -n "$FULL_STACK_DIR" ] || { [ -f /opt/reqdoc/docker-compose.yml ] && FULL_STACK_DIR=/opt/reqdoc; }
  if [ -z "$FULL_STACK_DIR" ]; then
    warn "未找到全量发布包安装目录（默认探测 /opt/reqdoc）：按 --no-db 处理。要对接请用 --full-stack <目录> 重跑。"
    NO_DB=1
  else
    [ -f "$FULL_STACK_DIR/docker-compose.yml" ] || die "--full-stack 目录里没有 docker-compose.yml：$FULL_STACK_DIR"
    [ -f "$FULL_STACK_DIR/.env" ] || die "--full-stack 目录里没有 .env（全量包安装未完成？）：$FULL_STACK_DIR"
    command -v docker >/dev/null 2>&1 || die "对接数据库需要 docker 命令。"
    docker info >/dev/null 2>&1 || die "Docker 守护进程不可用（未启动或无权限）。"
  fi
fi
log "包目录：$PKG_DIR"
log "安装目录：$INSTALL_DIR"
[ "$NO_DB" -eq 1 ] || log "全量包目录：$FULL_STACK_DIR（db→宿主:${DB_PORT}，redis→宿主:${REDIS_PORT}）"

step "1/6 校验包完整性"
(cd "$PKG_DIR" && sha256sum -c checksums.txt) || die "校验失败：包在拷贝中受损，请重新传输。"

step "2/6 展开源码树"
mkdir -p "$INSTALL_DIR"
tar -xzf "$PKG_DIR/repo.tar.gz" -C "$INSTALL_DIR"

step "3/6 安装工具链（uv / Node.js / CPython / 依赖缓存）"
TOOL="$INSTALL_DIR/.toolchain"
mkdir -p "$TOOL/bin"
cp "$PKG_DIR/vendor/uv/uv" "$TOOL/bin/uv"
chmod +x "$TOOL/bin/uv"

tar -xJf "$PKG_DIR"/vendor/node/node-*-linux-x64.tar.xz -C "$TOOL"
NODE_HOME="$(echo "$TOOL"/node-*-linux-x64)"
ln -sfn "$NODE_HOME" "$TOOL/node"

tar -xzf "$PKG_DIR/vendor/cpython.tar.gz" -C "$TOOL"
PY_HOME="$(echo "$TOOL"/cpython-*)"
ln -sfn "$PY_HOME" "$TOOL/cpython"
PY_BIN="$TOOL/cpython/bin/python3.12"
[ -x "$PY_BIN" ] || die "包内 CPython 展开异常：找不到 $PY_BIN"

# 依赖缓存留在安装目录：日后改动 pyproject/package.json 后仍可离线重装（uv sync / npm ci）
cp -a "$PKG_DIR/vendor/uv-cache" "$TOOL/uv-cache"
cp -a "$PKG_DIR/vendor/npm-cache" "$TOOL/npm-cache"

cat > "$INSTALL_DIR/dev-env.sh" <<EOF
# 离线开发环境变量（install-devkit.sh 生成）。
# 每个新终端先：source $INSTALL_DIR/dev-env.sh
export PATH="$TOOL/bin:$TOOL/node/bin:\$PATH"
export UV_CACHE_DIR="$TOOL/uv-cache"
export UV_OFFLINE=1                      # uv 永不出网，全部从包内缓存取
export UV_PYTHON="$PY_BIN"
export npm_config_cache="$TOOL/npm-cache"
export npm_config_offline=true           # npm 永不出网
export npm_config_audit=false
export npm_config_fund=false
EOF
log "环境文件已生成：$INSTALL_DIR/dev-env.sh"

# shellcheck source=/dev/null
source "$INSTALL_DIR/dev-env.sh"

step "4/6 离线重建后端虚拟环境（uv sync --frozen）"
(cd "$INSTALL_DIR/backend" && env -u PYTHONPATH uv sync --frozen)

step "5/6 离线重建前端 node_modules（npm ci）"
(cd "$INSTALL_DIR/frontend" && npm ci)

step "6/6 初始配置与图形渲染工具链"
DEV_ENV="$INSTALL_DIR/backend/.env"
if [ ! -f "$DEV_ENV" ]; then
  cp "$INSTALL_DIR/backend/.env.example" "$DEV_ENV"
fi
# 覆盖式写入一个配置项：删旧行＋整行追加（不用 sed 替换串：值里含 & | 等字符会破坏 sed 语义）
set_env_kv() {
  grep -v "^$1=" "$DEV_ENV" > "$DEV_ENV.tmp" || true
  printf '%s=%s\n' "$1" "$2" >> "$DEV_ENV.tmp"
  mv "$DEV_ENV.tmp" "$DEV_ENV"
}

# --- plantuml：包内 JRE + 源码树自带的 plantuml.jar ---
tar -xzf "$PKG_DIR/vendor/jre.tar.gz" -C "$TOOL"
JRE_HOME="$(echo "$TOOL"/jdk-*-jre)"
ln -sfn "$JRE_HOME" "$TOOL/jre"
[ -x "$TOOL/jre/bin/java" ] || die "包内 JRE 展开异常：找不到 $TOOL/jre/bin/java"
set_env_kv JAVA_PATH "$TOOL/jre/bin/java"

# --- mermaid：包内 mermaid-cli + chrome-headless-shell（Chrome 官方无头精简版）---
mkdir -p "$TOOL/mermaid" "$TOOL/chrome"
tar -xzf "$PKG_DIR/vendor/mermaid.tar.gz" -C "$TOOL/mermaid"
tar -xzf "$PKG_DIR/vendor/chrome-headless-shell.tar.gz" -C "$TOOL/chrome"
CHROME_BIN="$(find "$TOOL/chrome" -name chrome-headless-shell -type f | head -1)"
[ -n "$CHROME_BIN" ] || die "包内 chrome-headless-shell 展开异常"
chmod +x "$CHROME_BIN"
printf '{\n  "executablePath": "%s",\n  "args": ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"]\n}\n' \
  "$CHROME_BIN" > "$INSTALL_DIR/backend/tools/puppeteer.devkit.json"
set_env_kv MMDC_PATH "$TOOL/mermaid/node_modules/.bin/mmdc"
set_env_kv PUPPETEER_CONFIG "$INSTALL_DIR/backend/tools/puppeteer.devkit.json"
# 无头 Chrome 依赖少量系统库（libnss3 等），逐个核对并点名缺哪个
MISSING_LIBS="$(ldd "$CHROME_BIN" 2>/dev/null | awk '/not found/{print $1}' | tr '\n' ' ')"
if [ -n "$MISSING_LIBS" ]; then
  warn "无头 Chrome 缺系统库：${MISSING_LIBS}——mermaid 渲染不可用（Debian/Ubuntu 对应 libnss3/libnspr4/libasound2t64 等包，需离线源安装）；plantuml 与其余功能不受影响。"
else
  log "图形渲染工具链就绪：plantuml（包内 JRE）+ mermaid（包内 mmdc + 无头 Chrome）。"
fi

# --- 中文字体：装到用户级字体目录，plantuml 与无头 Chrome 渲染中文都靠它 ---
mkdir -p "$HOME/.local/share/fonts"
cp "$PKG_DIR"/vendor/fonts/*.ttc "$HOME/.local/share/fonts/"
if command -v fc-cache >/dev/null 2>&1; then fc-cache -f "$HOME/.local/share/fonts" >/dev/null 2>&1 || true
else warn "未装 fontconfig（fc-cache 缺失）：图形里的中文可能渲染为方块。"; fi

if [ "$NO_DB" -eq 0 ]; then
  step "对接全量包数据库（停业务容器 → 发布端口 → 迁移库表）"
  # 读全量包 .env 的数据库参数与 AI 服务参数（口令只写入开发 .env，不回显）
  read_env() { sed -n "s/^$1=//p" "$FULL_STACK_DIR/.env" | tail -1; }
  PG_DB="$(read_env POSTGRES_DB)"; PG_USER="$(read_env POSTGRES_USER)"; PG_PASS="$(read_env POSTGRES_PASSWORD)"
  [ -n "$PG_DB" ] && [ -n "$PG_USER" ] && [ -n "$PG_PASS" ] || die "无法从 $FULL_STACK_DIR/.env 读到 POSTGRES_DB/USER/PASSWORD。"

  COMPOSE=(docker compose --project-name reqdoc --project-directory "$FULL_STACK_DIR" -f "$FULL_STACK_DIR/docker-compose.yml")

  log "停掉业务容器 api 与 worker（本机开发进程接管；恢复见包首注释）"
  "${COMPOSE[@]}" stop api worker

  # 全量包编排里 db/redis 不发布宿主端口（只在容器网络内可达），开发进程在宿主机上，
  # 用 override 文件补端口发布后重建这两个容器（数据在卷里，重建不丢）。
  OVERRIDE="$FULL_STACK_DIR/docker-compose.dev-ports.yml"
  cat > "$OVERRIDE" <<EOF
# 开发对接用端口发布（install-devkit.sh 生成）。恢复纯生产形态：
#   docker compose --project-name reqdoc up -d db redis   # 不带本文件即撤掉端口发布
services:
  db:
    ports: ["${DB_PORT}:5432"]
  redis:
    ports: ["${REDIS_PORT}:6379"]
EOF
  "${COMPOSE[@]}" -f "$OVERRIDE" up -d --wait db redis

  if [ "$REBUILD_DB" -eq 1 ]; then
    warn "--rebuild-db：清空业务库 ${PG_DB} 重建（旧数据全部丢弃）"
    "${COMPOSE[@]}" exec -T db psql -U "$PG_USER" -d "$PG_DB" \
      -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public;'
  fi

  # 开发 .env：数据库指向全量包容器；AI 服务参数照抄全量包现场配置（set_env_kv 见步骤 6/6）。
  case "$PG_PASS" in
    *[@:/?#%]*) warn "数据库口令含 URL 保留字符（@ : / ? # %），连接串可能解析失败；建议全量包换口令后重跑。" ;;
  esac
  set_env_kv DATABASE_URL "postgresql+psycopg://${PG_USER}:${PG_PASS}@localhost:${DB_PORT}/${PG_DB}"
  for key in LLM_BASE_URL LLM_API_KEY LLM_MODEL LLM_PROVIDER_TYPE LLM_CONTEXT_TOKENS LLM_MAX_TOKENS \
             LLM_TIMEOUT LLM_DISABLE_THINKING EMBEDDING_BASE_URL EMBEDDING_API_KEY EMBEDDING_MODEL EMBEDDING_DIM; do
    val="$(read_env "$key")"
    [ -z "$val" ] || set_env_kv "$key" "$val"
  done
  log "backend/.env 已指向全量包数据库（localhost:${DB_PORT}），AI 服务参数已照抄现场配置。"
  log "REDIS_URL 保持留空＝AI 任务同步执行；要试异步就填 redis://localhost:${REDIS_PORT}/0 并本机跑 make worker。"

  # LibreOffice 不随包也不必装在宿主机：全量包 api 镜像里带着 LibreOffice＋中文字体
  # （生产的 docx→PDF 精确预览用的就是它）。生成 soffice 包装脚本把转换转发进该镜像跑：
  # 挂载 /tmp（临时 profile/HOME）与安装目录（导出文件），同 uid 运行保证产物归属，断网执行。
  REQDOC_IMAGE="$(read_env REQDOC_IMAGE)"
  if [ -n "$REQDOC_IMAGE" ] && docker image inspect "$REQDOC_IMAGE" >/dev/null 2>&1; then
    cat > "$TOOL/bin/soffice" <<EOF
#!/usr/bin/env bash
# soffice 包装器（install-devkit.sh 生成）：复用全量发布包镜像内的 LibreOffice 与中文字体，
# 宿主机不必安装 LibreOffice。后端经 SOFFICE_PATH 调用本脚本，与直接调 soffice 参数兼容。
exec docker run --rm --network none --user "\$(id -u):\$(id -g)" \\
  -e HOME -e LC_ALL -e LANG \\
  -v /tmp:/tmp -v "$INSTALL_DIR:$INSTALL_DIR" \\
  "$REQDOC_IMAGE" soffice "\$@"
EOF
    chmod +x "$TOOL/bin/soffice"
    set_env_kv SOFFICE_PATH "$TOOL/bin/soffice"
    log "docx→PDF 精确预览已接通：SOFFICE_PATH → 包装脚本 → 全量包镜像内的 LibreOffice（与生产同源）。"
  else
    warn "全量包镜像不可用（REQDOC_IMAGE=$REQDOC_IMAGE）：SOFFICE_PATH 未配置，docx→PDF 精确预览降级。"
  fi

  # 关键一步：全量包的库表停在出包时的旧版本，增量迁移到本包代码的最新版本。
  # alembic 幂等：已在目标版本则无操作；--rebuild-db 后这里就是从零建全套表。
  (cd "$INSTALL_DIR/backend" && env -u PYTHONPATH uv run alembic upgrade head)
  log "库表已迁移到开发代码的最新版本。演示数据：source dev-env.sh 后 ./setup-linux.sh seed"
else
  log "未对接数据库：测试与构建照常可用（内存 SQLite）；要起真实服务时用 --full-stack 重跑。"
fi

if [ "$VERIFY" -eq 1 ]; then
  step "离线校验（--verify）"
  backend_rc=0; frontend_rc=0
  (cd "$INSTALL_DIR/backend" && env -u PYTHONPATH uv run pytest) || backend_rc=$?
  (cd "$INSTALL_DIR/frontend" && npm run build) || frontend_rc=$?
  echo '基线对照：后端应全过（mermaid 渲染用例走包内工具链）；唯一例外＝1 例需要 Postgres，'
  echo '连不上时自动跳过属正常。前端生产构建应通过。'
  if [ "$backend_rc" -eq 0 ]; then log "后端测试全过。"; else warn "后端有失败用例：若是 mermaid 用例，多半是无头 Chrome 缺系统库（见上方 ldd 检查），其余按输出排查。"; fi
  if [ "$frontend_rc" -eq 0 ]; then log "前端生产构建通过。"; else die "前端生产构建失败，按上方输出排查。"; fi
fi

step "安装完成"
log "日常使用："
log "  source $INSTALL_DIR/dev-env.sh     # 每个新终端先执行"
log "  cd $INSTALL_DIR && make backend    # 前台起后端（另开终端 make frontend）"
log "  ./setup-linux.sh <任务>            # check/config/seed/build/verify/start/stop 均可离线用"
log "（install / --mirror 两个联网环节在离线机上不适用）"
if [ "$NO_DB" -eq 0 ]; then
  log "恢复生产形态（--no-deps 必须带，否则旧镜像的迁移器会因不认识新库版本而报错）："
  log "  cd $FULL_STACK_DIR && docker compose --project-name reqdoc up -d --no-deps api worker"
fi
