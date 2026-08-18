#!/usr/bin/env bash
# 需求治理平台 —— Linux 开发环境一键搭建脚本（与 setup-windows.ps1 任务面对齐）。
#
# 用法（仓库根目录）：
#   ./setup-linux.sh all --mirror        # 一键全流程（国内网络建议带 --mirror）
#   ./setup-linux.sh <任务名> [开关]
#
# 任务名：
#   check    检查各项工具是否就绪（只读，不改动任何东西）
#   install  安装缺失的必备工具：Git、uv、Node.js ≥20.19（apt 系发行版；需要 sudo）
#            （Python 3.12 不必单独装：uv sync 发现缺失时会自动下载管理版 CPython）
#   config   写开发配置：生成 backend/.env、图形渲染浏览器配置、（--mirror 时）镜像源
#   deps     下载项目依赖：后端 uv sync（读 uv.lock 精确重建）+ 前端 npm ci
#   infra    数据库与基础设施，两条路线二选一：
#              默认（Docker 引擎可用时）：起 Postgres + Redis 容器（同 docker-compose.yml）；
#              --native-db（需要 sudo）：apt 原生安装 PostgreSQL（含 pgvector 扩展）与
#                Redis，建 req_doc 角色与 req_v1 库。
#            两条路线最后都执行数据库迁移（alembic upgrade head）。
#            宿主端口被占时可用环境变量改容器映射：POSTGRES_PORT=15432 REDIS_PORT=16379 ./setup-linux.sh infra
#            （此时 backend/.env 的 DATABASE_URL 端口要一起改。）
#   seed     导入全流程演示数据集（幂等；--reset 清空重建；前置＝infra 已执行）
#   build    编译校验：后端全量字节码编译 + 前端 tsc 类型检查与 vite 生产构建
#   verify   测试校验：后端 pytest 全量 + 前端 vitest 全量，并对照已知基线解读结果
#   start    后台启动后端 API（:8000）与前端 dev server（:5173），日志落 .run/
#            （日常调试推荐用 Makefile 前台跑：make backend / make frontend，日志直接在终端）
#   stop     停止开发进程（回收 :8000/:5173）、本机 rq worker，并停掉 compose 容器
#   all      按 check → install → config → deps → infra → build → verify 顺序全部执行
#
# 开关：
#   --with-docker  install 时一并安装 Docker（get.docker.com 官方脚本；装完需重新登录使 docker 组生效）
#   --with-tools   install 时一并安装可选工具：LibreOffice+中文字体（docx→PDF 精确预览）、
#                  Java 运行时（plantuml 渲染）、mermaid-cli（mermaid 渲染）
#   --mirror       config 时切国内镜像：npm→npmmirror、PyPI→清华、uv 的 CPython 下载→npmmirror；
#                  install 时 uv 改走 PyPI（清华源）安装而非 GitHub 下载
#   --native-db    infra 走原生 apt 安装路线（不依赖 Docker；需要 sudo）
#   --reset        seed 时先清空演示项目再重建
#
# 不装 Docker 也能开发：测试全部跑内存 SQLite；只有启动真实服务（start / infra / seed）
# 需要 Postgres——没有 Docker 就用 infra --native-db 原生安装。

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"
FRONTEND_DIR="$REPO_ROOT/frontend"
RUN_DIR="$REPO_ROOT/.run"
FAIL_COUNT=0

# 输出着色（非终端时自动关闭）
if [ -t 1 ]; then
  C_OK=$'\033[32m'; C_WARN=$'\033[33m'; C_SECT=$'\033[36m'; C_RST=$'\033[0m'
else
  C_OK=''; C_WARN=''; C_SECT=''; C_RST=''
fi
section() { printf '\n%s==== %s ====%s\n' "$C_SECT" "$1" "$C_RST"; }
ok()      { printf '  %s[OK]%s %s\n' "$C_OK" "$C_RST" "$1"; }
info()    { printf '  [i] %s\n' "$1"; }
warn()    { printf '  %s[!]%s %s\n' "$C_WARN" "$C_RST" "$1"; }
bad()     { printf '  %s[缺]%s %s\n' "$C_WARN" "$C_RST" "$1"; FAIL_COUNT=$((FAIL_COUNT + 1)); }
die()     { printf '%s[中止]%s %s\n' "$C_WARN" "$C_RST" "$1" >&2; exit 1; }
has()     { command -v "$1" >/dev/null 2>&1; }

# uv 常装在 ~/.local/bin；刚装完的本会话也要能找到
export PATH="$HOME/.local/bin:$PATH"

# ---------------------------------------------------------------- 参数解析 ----
TASK="${1:-help}"
shift || true
WITH_DOCKER=0; WITH_TOOLS=0; MIRROR=0; NATIVE_DB=0; RESET=0
for arg in "$@"; do
  case "$arg" in
    --with-docker) WITH_DOCKER=1 ;;
    --with-tools)  WITH_TOOLS=1 ;;
    --mirror)      MIRROR=1 ;;
    --native-db)   NATIVE_DB=1 ;;
    --reset)       RESET=1 ;;
    *) die "不认识的开关：$arg（可用：--with-docker --with-tools --mirror --native-db --reset）" ;;
  esac
done

node_version_ok() {
  has node || return 1
  local ver major minor
  ver="$(node --version | tr -d 'v')"
  major="${ver%%.*}"; minor="$(echo "$ver" | cut -d. -f2)"
  [ "$major" -gt 20 ] || { [ "$major" -eq 20 ] && [ "$minor" -ge 19 ]; }
}

docker_ready() { has docker && docker info >/dev/null 2>&1; }

require_apt() {
  has apt-get || die '本脚本的安装路线只支持 apt 系发行版（Debian/Ubuntu）。其它发行版请按 README 手工安装 Git、uv、Node.js ≥20.19 后，从 config 任务继续。'
}

# ------------------------------------------------------------------ check ----
task_check() {
  section '环境检查（只读）'
  if has git; then ok "$(git --version)"; else bad 'Git 未安装（install 任务可装）。'; fi
  if has uv; then ok "uv $(uv --version | awk '{print $2}')"; else bad 'uv 未安装（install 任务可装）。后端依赖管理全靠它。'; fi
  if has node; then
    if node_version_ok; then ok "Node.js $(node --version)（满足 Vite 8 下限 20.19）"
    else bad "Node.js $(node --version) 低于 Vite 8 要求的 20.19（install 任务会升级到 22.x）。"; fi
  else bad 'Node.js 未安装（install 任务可装）。'; fi
  if has python3; then ok "系统 $(python3 --version)"
  else info '系统无 python3：没关系，uv sync 会自动下载管理版 CPython 3.12。'; fi

  if has docker; then
    if docker info >/dev/null 2>&1; then ok "$(docker --version | sed 's/,.*//')，引擎可用"
    else info 'Docker 已装但引擎不可用（未启动或当前用户不在 docker 组）：跑 infra 前处理。'; fi
  else info '未装 Docker：测试与构建不需要它；起真实服务可 install --with-docker，或 infra --native-db 原生装数据库。'
  fi
  if has psql && systemctl is-active --quiet postgresql 2>/dev/null; then ok '原生 PostgreSQL 服务在运行。'; fi
  if systemctl is-active --quiet redis-server 2>/dev/null; then ok '原生 Redis 服务在运行。'; fi

  if has soffice; then ok 'LibreOffice（可选：docx→PDF 精确预览）'
  else info '未装 LibreOffice（可选：docx→PDF 精确预览），对应功能自动降级。'; fi
  if has java; then ok 'Java 运行时（可选：plantuml 图形渲染）'
  else info '未装 Java 运行时（可选：plantuml 图形渲染），对应功能自动降级。'; fi
  if has mmdc; then ok 'mermaid-cli（可选：mermaid 图形渲染）'
  else info '未装 mermaid-cli（可选：mermaid 渲染；缺失时后端有 1 个测试用例会失败），对应功能自动降级。'; fi

  if [ "$FAIL_COUNT" -gt 0 ]; then
    printf '\n%s共有 %d 项必备工具缺失。执行：./setup-linux.sh install%s\n' "$C_WARN" "$FAIL_COUNT" "$C_RST"
  else
    printf '\n%s必备工具全部就绪。%s\n' "$C_OK" "$C_RST"
  fi
}

# ---------------------------------------------------------------- install ----
task_install() {
  section '安装必备工具'
  local need_apt=0
  has git || need_apt=1
  node_version_ok || need_apt=1
  [ "$WITH_TOOLS" -eq 1 ] && need_apt=1
  if [ "$need_apt" -eq 1 ]; then
    require_apt
    sudo apt-get update -qq
  fi

  if has git; then ok 'Git 已存在，跳过安装。'
  else echo '>> 安装 Git（apt）'; sudo apt-get install -y git; fi

  if has uv; then ok 'uv 已存在，跳过安装。'
  elif [ "$MIRROR" -eq 1 ]; then
    # 官方安装器从 GitHub Releases 拉二进制，国内常不可达；镜像模式改走 PyPI 清华源
    echo '>> 安装 uv（pip + 清华源）'
    has pip3 || { require_apt; sudo apt-get install -y python3-pip; }
    pip3 install --user --break-system-packages -i https://pypi.tuna.tsinghua.edu.cn/simple uv 2>/dev/null \
      || pip3 install --user -i https://pypi.tuna.tsinghua.edu.cn/simple uv
  else
    echo '>> 安装 uv（官方安装器）'
    curl -LsSf https://astral.sh/uv/install.sh | sh
  fi
  has uv || die 'uv 安装后仍不可用：确认 ~/.local/bin 在 PATH 上后重跑。'

  if node_version_ok; then ok "Node.js $(node --version) 已满足要求，跳过安装。"
  else
    echo '>> 安装 Node.js 22.x（NodeSource 官方源；会覆盖过旧版本）'
    require_apt
    curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
    sudo apt-get install -y nodejs
  fi

  if [ "$WITH_DOCKER" -eq 1 ]; then
    if has docker; then ok 'Docker 已存在，跳过安装。'
    else
      echo '>> 安装 Docker（get.docker.com 官方脚本）'
      curl -fsSL https://get.docker.com | sudo sh
      sudo usermod -aG docker "$USER"
      warn '已把当前用户加入 docker 组：重新登录后免 sudo 使用 docker。'
    fi
  fi

  if [ "$WITH_TOOLS" -eq 1 ]; then
    echo '>> 安装可选工具：LibreOffice + 中文字体、Java 运行时、mermaid-cli'
    sudo apt-get install -y libreoffice-writer fonts-noto-cjk default-jre
    if has mmdc; then ok 'mermaid-cli 已存在，跳过安装。'
    else sudo npm install -g @mermaid-js/mermaid-cli; fi
    if ! find_browser >/dev/null; then
      echo '>> 本机无浏览器：安装 chrome-headless-shell（Chrome 官方无头精简版，mermaid 渲染内核，装到 ~/.cache/reqdoc-chrome）'
      npx -y puppeteer browsers install chrome-headless-shell --path "$HOME/.cache/reqdoc-chrome"
    fi
  fi
  printf '\n%s安装完成。%s\n' "$C_OK" "$C_RST"
}

# ----------------------------------------------------------------- config ----
find_browser() {
  local cand
  for cand in /usr/bin/google-chrome /usr/bin/google-chrome-stable; do
    [ -x "$cand" ] && { echo "$cand"; return 0; }
  done
  for cand in google-chrome google-chrome-stable chromium chromium-browser; do
    has "$cand" && { command -v "$cand"; return 0; }
  done
  # 最后兜底：install --with-tools 装的 chrome-headless-shell（Chrome 官方无头精简版）
  cand="$(find "$HOME/.cache/reqdoc-chrome" -name chrome-headless-shell -type f 2>/dev/null | head -1)"
  [ -n "$cand" ] && { echo "$cand"; return 0; }
  return 1
}

task_config() {
  section '写开发配置'
  local env_file="$BACKEND_DIR/.env"

  if [ "$MIRROR" -eq 1 ]; then
    npm config set registry https://registry.npmmirror.com
    # uv 的 PyPI 索引与管理版 CPython 下载源（CPython 原生源是 GitHub Releases，国内常不可达）
    local marker='# setup-linux.sh --mirror（删除本段即回退官方源）'
    if ! grep -qF "$marker" "$HOME/.bashrc" 2>/dev/null; then
      {
        echo ''
        echo "$marker"
        echo 'export UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple'
        echo 'export UV_PYTHON_INSTALL_MIRROR=https://registry.npmmirror.com/-/binary/python-build-standalone'
      } >> "$HOME/.bashrc"
    fi
    export UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
    export UV_PYTHON_INSTALL_MIRROR=https://registry.npmmirror.com/-/binary/python-build-standalone
    ok 'npm 源已切 npmmirror；uv 的 PyPI 与 CPython 下载源已切国内镜像（写入 ~/.bashrc）。'
  fi

  if [ -f "$env_file" ]; then ok 'backend/.env 已存在，保持不动。'
  else
    cp "$BACKEND_DIR/.env.example" "$env_file"
    ok 'backend/.env 已从模板生成。REDIS_URL 留空＝AI 任务同步执行（不需要 worker）；要接 LLM 就填 LLM_BASE_URL。'
  fi

  # 图形渲染浏览器：仓库默认配置钉 /usr/bin/google-chrome；不在该路径时生成本机配置并用 PUPPETEER_CONFIG 指过去
  if [ -x /usr/bin/google-chrome ]; then
    ok '图形渲染用默认配置（/usr/bin/google-chrome）。'
  else
    local browser
    if browser="$(find_browser)"; then
      local pptr="$BACKEND_DIR/tools/puppeteer.local.json"
      printf '{\n  "executablePath": "%s",\n  "args": ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"]\n}\n' "$browser" > "$pptr"
      if ! grep -q 'PUPPETEER_CONFIG' "$env_file"; then
        {
          echo ''
          echo '# 本机 mermaid 渲染浏览器配置（setup-linux.sh config 生成）'
          echo "PUPPETEER_CONFIG=$pptr"
        } >> "$env_file"
      fi
      ok "图形渲染已指向本机浏览器：$browser"
    else
      info '未找到 Chrome/Chromium，跳过图形渲染配置；装浏览器后重跑 config 即可补上。'
    fi
  fi
}

# ------------------------------------------------------------------- deps ----
task_deps() {
  section '下载项目依赖'
  echo '>> 后端 uv sync（按 uv.lock 精确重建虚拟环境，缺 Python 3.12 时自动下载）'
  (cd "$BACKEND_DIR" && uv sync)
  echo '>> 前端 npm ci（按 package-lock.json 精确重建 node_modules）'
  (cd "$FRONTEND_DIR" && npm ci)
  printf '\n%s依赖就绪。%s\n' "$C_OK" "$C_RST"
}

# ------------------------------------------------------------------ infra ----
run_migrate() {
  echo '>> 数据库迁移（alembic upgrade head）'
  # env -u PYTHONPATH：宿主机若有全局 PYTHONPATH（如 ROS），会把体外包混进 Python 进程，清掉最稳
  (cd "$BACKEND_DIR" && env -u PYTHONPATH uv run alembic upgrade head)
}

native_pg_sql() { sudo -u postgres psql -tAc "$1" ${2:+-d "$2"}; }

task_infra_native() {
  require_apt
  echo '>> 原生安装 PostgreSQL + pgvector + Redis（apt，需要 sudo）'
  sudo apt-get update -qq
  sudo apt-get install -y postgresql postgresql-contrib redis-server
  sudo systemctl enable --now postgresql redis-server

  local pgver
  pgver="$(native_pg_sql 'SHOW server_version' | cut -d. -f1 | tr -d ' ')"
  # 迁移链会执行 CREATE EXTENSION vector，pgvector 扩展装不上则迁移必失败，这一步不可省
  if ! sudo apt-get install -y "postgresql-$pgver-pgvector"; then
    die "apt 里没有 postgresql-$pgver-pgvector：请接入 PGDG 官方源（apt.postgresql.org）后重跑，或改用 Docker 路线。"
  fi
  ok "PostgreSQL $pgver 与 Redis 服务已启用（5432 / 6379）。"

  if [ "$(native_pg_sql "SELECT count(*) FROM pg_roles WHERE rolname='req_doc'")" != "1" ]; then
    native_pg_sql "CREATE ROLE req_doc LOGIN PASSWORD 'req_doc' CREATEDB" >/dev/null
    ok '角色 req_doc 已创建（口令 req_doc，仅限本地开发）。'
  else ok '角色 req_doc 已存在。'; fi
  if [ "$(native_pg_sql "SELECT count(*) FROM pg_database WHERE datname='req_v1'")" != "1" ]; then
    native_pg_sql 'CREATE DATABASE req_v1 OWNER req_doc' >/dev/null
    ok '数据库 req_v1 已创建。'
  else ok '数据库 req_v1 已存在。'; fi
  native_pg_sql 'CREATE EXTENSION IF NOT EXISTS vector' req_v1 >/dev/null
  native_pg_sql 'CREATE EXTENSION IF NOT EXISTS pg_trgm' req_v1 >/dev/null

  # .env 仍是模板默认值（免密，适配容器 trust 认证）时改为带口令连接；用户改过的值不动
  local env_file="$BACKEND_DIR/.env" tmpl native
  tmpl='DATABASE_URL=postgresql+psycopg://req_doc@localhost:5432/req_v1'
  native='DATABASE_URL=postgresql+psycopg://req_doc:req_doc@localhost:5432/req_v1'
  if [ -f "$env_file" ] && grep -qF "$tmpl" "$env_file"; then
    sed -i "s|$tmpl|$native|" "$env_file"
    ok '.env 的 DATABASE_URL 已改为原生连接（带口令）。'
  fi
}

task_infra() {
  section '数据库与基础设施'
  if [ "$NATIVE_DB" -eq 0 ] && docker_ready; then
    echo '>> 启动 db 与 redis 容器（Docker 路线）'
    (cd "$REPO_ROOT" && docker compose up -d --wait db redis)
  elif [ "$NATIVE_DB" -eq 1 ]; then
    task_infra_native
  else
    info 'Docker 引擎不可用。两条路线任选：'
    info '  ① 启动 Docker（或把当前用户加入 docker 组）后重跑 infra；'
    info '  ② ./setup-linux.sh infra --native-db（apt 原生安装 PostgreSQL + pgvector + Redis）。'
    info '测试与构建不需要数据库，可先继续 build / verify。'
    return 0
  fi
  run_migrate
  printf '\n%s数据库就绪。导入全流程演示数据：./setup-linux.sh seed%s\n' "$C_OK" "$C_RST"
}

# ------------------------------------------------------------------- seed ----
task_seed() {
  section '导入全流程演示数据集'
  [ -d "$BACKEND_DIR/.venv" ] || die '后端虚拟环境不存在：先跑 deps 任务。'
  local seed_args=(run python -m app.scripts.seed_full_demo)
  [ "$RESET" -eq 1 ] && seed_args+=(--reset)
  echo '>> 导入演示数据（幂等；已存在演示项目「电商订单中心（演示）」时自动跳过，--reset 清空重建）'
  (cd "$BACKEND_DIR" && env -u PYTHONPATH uv "${seed_args[@]}")
  printf '\n%s演示数据就绪。起服务后即可在界面里看到全流程数据（start 任务或 make backend/frontend）。%s\n' "$C_OK" "$C_RST"
}

# ------------------------------------------------------------------ build ----
task_build() {
  section '编译校验'
  echo '>> 后端全量字节码编译（compileall，等价语法检查）'
  (cd "$BACKEND_DIR" && env -u PYTHONPATH uv run python -m compileall -q app)
  echo '>> 前端 tsc 类型检查 + vite 生产构建'
  (cd "$FRONTEND_DIR" && npm run build)
  printf '\n%s编译校验通过。%s\n' "$C_OK" "$C_RST"
}

# ----------------------------------------------------------------- verify ----
task_verify() {
  section '测试校验'
  local backend_rc=0 frontend_rc=0
  echo '>> 后端 pytest 全量（内存 SQLite，不需要数据库在跑）'
  (cd "$BACKEND_DIR" && env -u PYTHONPATH uv run pytest) || backend_rc=$?
  echo '>> 前端 vitest 全量'
  (cd "$FRONTEND_DIR" && npm test) || frontend_rc=$?

  section '结果对照基线（2026-08-17 迁出时的已知状态）'
  echo '  后端基线：全过。两个环境相关的例外——1 例需要能连上 Postgres（连不上会自动跳过，属正常）；'
  echo '  1 例（test_publication_chart_fragment 的 docx 渲染 mermaid 用例）需要 mmdc，未装 mermaid-cli 时会失败。'
  echo '  前端基线：恰好 2 例已知遗留失败（theme、app-shell 各 1，记录在案），其余全过。'
  if [ "$backend_rc" -eq 0 ]; then ok '后端测试全过。'
  else warn '后端有失败用例：若只有上述 mmdc 那 1 例，属预期；否则按上方输出排查。'; fi
  if [ "$frontend_rc" -eq 0 ]; then ok '前端测试全过（连已知遗留失败都没出现，说明基线已被修复）。'
  else warn '前端有失败用例：若恰好是基线里那 2 例，属预期；多于 2 例才需要排查。'; fi
}

# ------------------------------------------------------------------ start ----
port_busy() { ss -tln 2>/dev/null | awk '{print $4}' | grep -qE ":$1\$"; }

task_start() {
  section '后台启动开发进程（日志落 .run/）'
  [ -f "$BACKEND_DIR/.env" ] || die '缺 backend/.env：先跑 config 任务。'
  [ -d "$BACKEND_DIR/.venv" ] || die '后端虚拟环境不存在：先跑 deps 任务。'
  [ -d "$FRONTEND_DIR/node_modules" ] || die '前端 node_modules 不存在：先跑 deps 任务。'
  port_busy 8000 && die '端口 8000 已被占用：先 ./setup-linux.sh stop，或确认不是别的项目在用。'
  port_busy 5173 && die '端口 5173 已被占用：先 ./setup-linux.sh stop，或确认不是别的项目在用。'
  if ! docker_ready && ! systemctl is-active --quiet postgresql 2>/dev/null; then
    warn '本机既无 Docker 引擎也无原生 Postgres 服务在跑：后端 API 连不上数据库会报错，先跑 infra。'
  fi

  mkdir -p "$RUN_DIR"
  (cd "$BACKEND_DIR" && env -u PYTHONPATH nohup uv run uvicorn app.main:app \
      --host 0.0.0.0 --port 8000 --reload >"$RUN_DIR/backend.log" 2>&1 & echo $! >"$RUN_DIR/backend.pid")
  (cd "$FRONTEND_DIR" && nohup npm run dev >"$RUN_DIR/frontend.log" 2>&1 & echo $! >"$RUN_DIR/frontend.pid")
  ok "后端 API → http://localhost:8000（日志 .run/backend.log）"
  ok "前端    → http://localhost:5173（日志 .run/frontend.log；/api 自动代理到 8000）"
  info '停止：./setup-linux.sh stop。前台调试请改用 make backend / make frontend。'
}

# ------------------------------------------------------------------- stop ----
task_stop() {
  section '停止开发进程与容器'
  if fuser -k -TERM 8000/tcp >/dev/null 2>&1; then ok '已停止端口 8000 的进程（后端 API）。'
  else info '端口 8000 无监听进程。'; fi
  if fuser -k -TERM 5173/tcp >/dev/null 2>&1; then ok '已停止端口 5173 的进程（前端 dev server）。'
  else info '端口 5173 无监听进程。'; fi
  if pgrep -f 'rq worker.*[i]ntake' >/dev/null 2>&1; then
    pgrep -f 'rq worker.*[i]ntake' | xargs -r kill -TERM
    ok '已停止本机 rq worker（intake 队列）。'
  fi
  rm -f "$RUN_DIR/backend.pid" "$RUN_DIR/frontend.pid"
  if docker_ready; then
    (cd "$REPO_ROOT" && docker compose stop) && ok 'compose 容器已停止。'
  fi
  info '原生安装的 PostgreSQL / Redis 是常驻系统服务，本任务不动它们；要停用 systemctl stop postgresql redis-server。'
}

# ------------------------------------------------------------------- 调度 ----
show_usage() {
  sed -n '2,50p' "$0" | grep -E '^# ?' | sed 's/^# \{0,1\}//'
}

case "$TASK" in
  help)    show_usage ;;
  check)   task_check ;;
  install) task_install ;;
  config)  task_config ;;
  deps)    task_deps ;;
  infra)   task_infra ;;
  seed)    task_seed ;;
  build)   task_build ;;
  verify)  task_verify ;;
  start)   task_start ;;
  stop)    task_stop ;;
  all)
    task_check
    task_install
    task_config
    task_deps
    task_infra
    task_build
    task_verify
    section '全部完成'
    printf '%s开发环境已就绪。接下来：%s\n' "$C_OK" "$C_RST"
    printf '  导入演示数据：./setup-linux.sh seed\n'
    printf '  启动开发进程：./setup-linux.sh start（前台调试用 make backend / make frontend）\n'
    ;;
  *) die "不认识的任务：$TASK（./setup-linux.sh help 查看用法）" ;;
esac
