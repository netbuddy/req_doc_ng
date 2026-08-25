# 需求治理平台（req_doc_ng）

需求工程资产管理平台：材料接入 → 要素识别 → 条目形成 → 条目评审 → 图表与追溯 → 发布导出的全流程工作台。前后端分离：

| 目录 | 内容 | 技术栈 |
| --- | --- | --- |
| `backend/` | API 服务、RQ 异步 worker、数据库迁移、测试 | Python 3.12 + FastAPI + SQLAlchemy + Alembic，包管理用 uv |
| `frontend/` | 单页应用 | React 19 + TypeScript + Vite + Ant Design 6 |
| `api/` | 接口契约（OpenAPI YAML）。后端有互锁测试核对实现，前端类型由它生成，**不可删** | OpenAPI 3 |
| `docker/`、`docker-compose.yml` | 基础设施编排：Postgres(pgvector) + Redis + 可选容器 worker/api/pgadmin | Docker Compose |
| `release/` | 离线全量包打包与安装脚本（生产交付用，开发环境用不到） | Bash |
| `tools/api-docs/` | 静态 API 文档查看页 | HTML |

本仓只含代码与工程文件，不含设计文档。代码注释里引用的 `docs/...` 路径是历史出处标注，运行、测试、构建都不依赖这些路径。

## 一、环境依赖

| 依赖 | 版本要求 | 用途 |
| --- | --- | --- |
| Python | ≥ 3.12 | 后端 |
| [uv](https://docs.astral.sh/uv/) | 最新版即可 | Python 依赖管理（读 `backend/uv.lock` 精确重建） |
| Node.js | ≥ 20.19（或 ≥ 22） | 前端（Vite 8 的下限） |
| Docker + Docker Compose | 现行版本 | Postgres、Redis（也可自装这两个服务替代） |
| GNU Make | 可选 | `Makefile` 提供的启停快捷方式（Windows 原生没有，见下文） |

以下为可选依赖，缺失时对应功能自动降级、不影响其它开发调试：

- **LibreOffice**（`soffice` 在 PATH 上）：发布环节 docx→PDF 精确预览。
- **Node 包 `@mermaid-js/mermaid-cli`**（`mmdc` 在 PATH 上）与 **Java 运行时**：图形源码（mermaid/plantuml）本地栅格化；`plantuml.jar` 已随仓库放在 `backend/tools/`，不需要另外下载。mmdc 依赖本机浏览器：`backend/tools/puppeteer.json` 钉的是 `/usr/bin/google-chrome`，机器上浏览器在别的路径时改这个文件或用 `PUPPETEER_CONFIG` 指到自己的配置。注意：后端测试里有一个用例（`tests/test_publication_chart_fragment.py` 的 docx 渲染 mermaid 用例）会真实调用 mmdc，没装 mermaid-cli 时该用例失败，其余用例不受影响。
- **本地 LLM 服务**（llama.cpp/ollama 等 OpenAI 兼容接口）：AI 识别、起草、评审等功能。不配置时平台其余功能照常可用。

## 二、Linux / macOS 快速开始

**一键脚本（Linux）**：仓库根目录的 `setup-linux.sh` 与 Windows 版任务面完全对齐，把检查、安装（apt，仅 Debian/Ubuntu 系）、配置、数据库（Docker 容器或 `--native-db` 原生 apt 安装 PostgreSQL+pgvector+Redis）、依赖下载、演示数据导入、编译、测试整套流程做完：

```bash
./setup-linux.sh all --mirror      # 一键全流程（国内网络建议带 --mirror）
./setup-linux.sh help              # 任务清单：check / install / config / deps / infra / seed / build / verify / start / stop
```

macOS 上 `install` 与 `--native-db` 两个安装环节依赖 apt 不可用，请按下面手工步骤装工具；其余任务通用。下面是手工步骤（脚本做的就是这些事）：

```bash
# 1. 后端依赖（按 uv.lock 精确重建虚拟环境）
cd backend
uv sync
cp .env.example .env        # 按需填 LLM_BASE_URL 等；全部留空也能起服务

# 2. 基础设施 + 数据库迁移（回到仓库根目录）
cd ..
make infra-up               # 等价于 docker compose up -d --wait db redis
make migrate                # 等价于 cd backend && uv run alembic upgrade head

# 3. 起后端（占一个终端）
make backend                # uvicorn --reload，http://127.0.0.1:8000

# 4. 前端依赖与 dev server（占另一个终端）
cd frontend
npm ci
npm run dev                 # http://localhost:5173，/api 代理到 :8000

# 5.（可选）导入全流程演示数据
cd backend
uv run python -m app.scripts.seed_full_demo          # 幂等；--reset 清空重建
```

异步 AI 任务：`backend/.env` 的 `REDIS_URL` 留空时 AI 任务在 API 进程内同步执行，不需要 worker；填 `redis://localhost:6379/0` 后须另开终端跑 `make worker`（本机 live 代码）或 `make worker-bg`（容器 worker，不便于调试 worker 代码）。

`make help` 列出全部启停任务；`make down` 一键回收。

## 三、Windows 环境搭建

推荐两条路线，二选一：

### 路线 A（推荐）：WSL2 + Docker Desktop

Docker Desktop 开启 WSL2 集成，代码检出到 WSL2 的 Linux 文件系统内（如 `~/wp/req_doc_ng`，不要放在 `/mnt/c/` 下，文件监听与 IO 性能差一个量级），然后完全按上面 Linux 步骤操作。这条路线与 Linux 开发体验一致，无兼容性问题。

### 路线 B：Windows 原生

**一键脚本**：仓库根目录的 `setup-windows.ps1` 把检查、安装（winget）、配置、数据库、依赖下载、演示数据导入、编译、测试整套流程做完：

```powershell
# 在仓库根目录的 PowerShell 里执行（国内网络加 -Mirror 切换 npm / PyPI / CPython 下载镜像）
powershell -ExecutionPolicy Bypass -File .\setup-windows.ps1 all
# 各环节也可以单独执行：check / install / config / deps / infra / seed / build / verify / start / stop
powershell -ExecutionPolicy Bypass -File .\setup-windows.ps1 help
```

数据库（Postgres + Redis）有两条路线，`infra` 任务自动或按开关选择：Docker 引擎在运行就起容器（与 Linux 路线同一套 compose）；没有 Docker 时在**管理员 PowerShell** 里跑 `infra -NativeDb`，脚本原生安装 PostgreSQL 16（winget 官方包，自带 pgAdmin）、装入 pgvector 扩展、创建 `req_doc` 角色与 `req_v1` 库、安装 Memurai（Redis 7 兼容的 Windows 原生实现，开发版免费），最后执行数据库迁移。注意：pgvector 官方对 Windows 只提供源码编译（MSVC/nmake），脚本装入的是社区预编译包 `andreiramani/pgvector_pgsql_windows`——迁移链会执行 `CREATE EXTENSION vector`，这一步不可省；介意非官方产物就自行编译后重跑。

数据库就绪后 `seed` 任务导入全流程演示数据集（幂等，重复执行自动跳过；`-Reset` 清空重建）。脚本默认不装 Docker Desktop；要装用 `-WithDocker`，要装 LibreOffice/JRE/mermaid-cli 三件可选渲染工具用 `-WithTools`。

以下为手工路线的等价说明（脚本做的就是这些事）。各组件均有官方 Windows 支持：uv、Python 3.12、Node.js 装原生版；Postgres 与 Redis 用 Docker Desktop 起（`docker compose up -d --wait db redis`），或自装 Windows 版 Postgres 16（需 pgvector 扩展）。已知差异与处置：

1. **Makefile 不可用**（依赖 bash/fuser）。直接跑等价命令：
   ```powershell
   # 后端（backend 目录下）
   uv sync
   Copy-Item .env.example .env
   uv run alembic upgrade head
   uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
   # 前端（frontend 目录下）
   npm ci
   npm run dev
   ```
2. **RQ 默认 worker 不支持 Windows 原生运行**（依赖 `os.fork`）。三种处置任选：`.env` 的 `REDIS_URL` 留空走同步执行（开发调试完全够用，推荐）；worker 跑容器 `docker compose up -d worker`；或原生装 Memurai 后用不依赖 fork 的 SimpleWorker——`cd backend; uv run rq worker -u redis://localhost:6379/0 intake --worker-class rq.worker.SimpleWorker`（限制：任务超时强杀不生效，仅开发用）。
3. **换行符**：仓库带 `.gitattributes`（`* text=auto eol=lf`），检出即为 LF，不要改动本地 `core.autocrlf` 去覆盖它；CRLF 会破坏容器内脚本与 lock 文件校验。
4. **路径长度**：本仓最长路径远在 260 字符限制之内，无需特殊设置；若你把仓库放得很深，开启 `git config --global core.longpaths true` 兜底。
5. **可选工具**：LibreOffice、Java、mermaid-cli 都有 Windows 版，装好后如不在 PATH，用 `.env` 的 `SOFFICE_PATH` / `JAVA_PATH` / `MMDC_PATH` 指定绝对路径；不装则对应功能降级，不影响主流程。

## 四、开发验证命令

| 目的 | 命令 |
| --- | --- |
| 后端全量测试（内存 SQLite，不需要 Postgres 在跑；个别用例例外——1 例需要能连上 `DATABASE_URL` 的 Postgres，连不上自动跳过；1 例需要 mmdc，见上文可选依赖） | `cd backend && uv run pytest` |
| 后端坏味道门禁（ruff 等） | `cd backend && bash tools/smell_check.sh`（Windows 原生用 `uv run ruff check .`） |
| 前端测试 | `cd frontend && npm test` |
| 前端类型检查 + 生产构建 | `cd frontend && npm run build` |
| 前端 lint | `cd frontend && npm run lint` |
| 契约类型重生成（改了 `api/v1-openapi.yaml` 之后） | `cd frontend && npm run generate:api:file` |

## 五、接口契约机制

`api/openapi.yaml` 是活契约：只收已实现并定案的接口。后端测试 `backend/tests/test_contract_openapi.py` 把契约与 FastAPI 实际路由逐条核对，两边不一致即测试失败；前端 `src/api/generated/schema.ts` 由 `npm run generate:api:file` 从契约快照生成（生成产物已入库，克隆后不重新生成也能构建）。改接口 = 契约、实现、测试、前端类型四处同批更新。

## 六、常用端口

| 端口 | 服务 |
| --- | --- |
| 8000 | 后端 API（uvicorn） |
| 5173 | 前端 Vite dev server（`/api` 代理到 8000；`VITE_API_PROXY_TARGET` 可改指向） |
| 5432 / 6379 | Postgres / Redis（docker compose） |
| 5050 | pgadmin（可选，`docker compose up -d pgadmin`） |

## 七、离线包（生产交付与离线开发）

`release/` 下有两套离线打包，都在联网机出包、拷到离线 Linux x86_64 机器使用：

- **生产交付**：`pack-full.sh` 出全量安装包（应用镜像 + 数据库镜像 + 前端产物 + 编排），离线机用包内 `install-full.sh` 安装成 docker 化服务（默认 `/opt/reqdoc`）。
- **离线开发**：`pack-devkit.sh` 出开发环境包（源码 + uv/Node.js/CPython 工具链 + 前后端全部依赖缓存，约 214MB，不含数据库镜像），离线机用包内 `install-devkit.sh` 安装——全程离线重建虚拟环境与 node_modules，之后 `uv sync`/`npm ci` 也都从包内缓存取，不需要网络。数据库复用已装好的全量安装包：安装器停掉其 api/worker 业务容器（本机开发进程接管）、给 db/redis 补宿主端口发布（`--db-port`/`--redis-port` 可避开被占端口）、把全量包出包时的旧库表用 alembic 增量迁移到开发代码的最新版本（`--rebuild-db` 可改为清库重建）。没有全量包时加 `--no-db` 也能装，测试全跑内存 SQLite。恢复生产形态必须带 `--no-deps`（`docker compose --project-name reqdoc up -d --no-deps api worker`），否则全量包的一次性迁移服务会因旧镜像不认识新库版本号而报错。

开发包的完备性边界：图形渲染工具链整套随包——plantuml 用包内 Temurin JRE（plantuml.jar 本在源码树），mermaid 用包内 mermaid-cli＋chrome-headless-shell（Chrome 官方无头精简版），中文渲染用随包的 Noto CJK 字体（装到用户级字体目录）；无头 Chrome 依赖的少量系统库（libnss3 等）安装器会逐个 ldd 核对并点名缺项。LibreOffice 是唯一不进包的组件，也不必装在离线机——安装器生成 `soffice` 转发脚本，后端每次转 docx→PDF 时经它临时起一个全量包镜像的一次性容器在容器内转换（输入输出靠目录挂载共享，与生产引擎同源），宿主机文件系统上并没有 LibreOffice。离线机的操作系统层要求：Linux x86_64、glibc ≥ 2.28（Ubuntu 20.04 / Debian 10 / RHEL 8 以上；安装器会检查）、Docker Engine＋Compose V2（全量包本来就要求）；此外需要的 deb 包**全部列在 `release/devkit-os-debs.txt`**（无头 Chrome 的 18 个共享库＋fontconfig＋xz-utils/git/make/psmisc/iproute2 五件基础工具，文件头带联网机下载依赖闭包的命令），该清单经裸 Ubuntu 24.04 容器实测装齐即可双渲染出图。除清单外无需任何系统包：Python、Node、npm、libpq、编译工具链全部由包内自带或二进制 wheel 覆盖。
