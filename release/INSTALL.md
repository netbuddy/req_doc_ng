# 需求治理平台 · 离线安装手册

本手册面向在**无外网的生产机器**上安装本系统的运维人员。读者不需要了解本项目的开发过程，按本文顺序执行即可。全过程不需要联网：所需的三个容器镜像、前端页面产物、数据库初始化内容都在包里。

## 1 这个包里装的是什么

| 组成 | 说明 |
|---|---|
| 应用镜像 `reqdoc-api:base-*` | 后端服务。同一个镜像同时用于 API 容器与异步任务容器（worker），避免两者版本不一致 |
| 数据库镜像 `pgvector/pgvector:pg16` | PostgreSQL 16，带向量检索扩展 |
| 队列镜像 `redis:7` | 异步任务队列的存储 |
| `frontend-dist.tar.gz` | 浏览器页面（前端产物）。安装后由应用容器直接提供，不另起 Web 服务器 |
| `compose/` | 容器编排文件与环境变量模板 |
| `scripts/` | 安装、自检、现状查询、数据库备份脚本 |
| `manifest.json`、`SHA256SUMS` | 包的自述文件与逐文件校验和，安装脚本据此核对安装结果 |

**不在包内、需要生产环境提供的**：AI 推理服务。本系统通过环境变量指向一个已有的 OpenAI 兼容推理端点（接口格式与 OpenAI 相同的服务，不要求是 OpenAI 本身）。不配置也能装、能用，但 AI 对话与要素识别会返回占位内容而不调用真实模型，全局检索会退回纯词法匹配（只按词面命中，不做语义相似检索）。

## 2 安装前的前置条件

| # | 条件 | 检查方法 |
|---|---|---|
| 1 | 已安装 Docker，当前用户可用 | `docker info` 能正常输出 |
| 2 | Docker Compose 为 V2 | `docker compose version`（**中间是空格**）可执行 |
| 3 | 磁盘可用空间 ≥ 10 GB | `df -h`，看 Docker 数据目录与安装目录所在分区 |
| 4 | 对外端口空闲（默认 9180） | `ss -ltn | grep 9180` 无输出 |
| 5 | 已知推理端点地址（可选） | 形如 `http://<地址>:<端口>/v1`，`curl <地址>/v1/models` 可通 |

上述条件安装脚本会逐项自动检查，任一不满足即打印明确提示并退出，不会留下半程状态。

## 3 安装步骤

```bash
# 1. 进入包目录（<包目录> 为本包解压/拷贝后的位置）
cd <包目录>

# 2. 执行安装。安装目录建议 /opt/reqdoc（需要写权限，无权限时改用有权限的路径）
bash scripts/install-full.sh \
  --package-dir "$PWD" \
  --install-root /opt/reqdoc \
  --set POSTGRES_PASSWORD=<自定的数据库口令> \
  --set LLM_BASE_URL=http://<推理服务地址>:<端口>/v1 \
  --set LLM_MODEL=<模型标识>
```

`--set KEY=VALUE` 可重复，用于填写环境变量；也可以先执行一次让脚本生成 `/opt/reqdoc/.env`，编辑该文件后重跑。**数据库口令必须设置**：模板里的占位值 `CHANGE-ME-BEFORE-INSTALL` 会被脚本拒绝。

安装脚本按十步执行，每步失败即停：

1. 环境检查（Docker、Compose 版本、磁盘、端口、必需命令）
2. 包完整性校验（逐文件核对 `SHA256SUMS`，拷贝损坏在此暴露）
3. 镜像归档格式探针（先导入十几 KB 的探针镜像，格式不兼容秒级暴露，环境零改动）
4. 导入三个正式镜像，并核对镜像 ID 与包声明一致
5. 部署编排文件与环境变量
6. 展开前端页面产物
7. 数据库表结构迁移 → 导入内置文档模板（发布功能依赖模板，空库必须做这一步）
8. 启动应用与异步任务容器
9. 安装后自检（见第 4 节）
10. 写入状态文件 `/opt/reqdoc/state/deployed.json`

完成后浏览器访问 `http://<本机地址>:9180/`。

## 4 安装结果怎么判定为正确

「命令没报错」不等于「装对了」。第 9 步的自检检查的是**结果**，共十项：

| # | 检查项 | 它能发现什么 |
|---|---|---|
| 1–2 | API 与 worker 容器用的镜像 ID 与包声明一致 | 跑起来的是别的镜像（同名旧镜像、别处导入的镜像） |
| 3 | 镜像内源码逐文件重算指纹后与包声明一致 | 文件缺失、内容不符、载荷与清单不一致 |
| 4 | 镜像内台账首行的包标识与本次安装一致 | 有人绕过脚本手工换过镜像 |
| 5 | 前端产物目录整体指纹与包声明一致 | 解压不完整、解到了别的目录 |
| 6 | 数据库迁移头与包声明一致 | 迁移执行了一半中断 |
| 7 | 模板注册表非空 | 模板未导入（症状要走到发布环节才暴露） |
| 8 | 镜像内工具链齐全（Java、mermaid、chromium、graphviz、LibreOffice、plantuml.jar） | 缺失时导出文档里的图会静默降级成源码文本 |
| 9–10 | 健康端点自报正常、首页返回 200、异步 worker 已注册 | 服务没起来、页面没人提供、任务会静默排队 |

任何一项不通过，脚本以非零码退出并逐项打印「期望值 / 实测值」。

随时可重新执行自检与查看现状：

```bash
bash /opt/reqdoc/scripts/verify-release.sh --install-root /opt/reqdoc
bash /opt/reqdoc/scripts/status.sh --install-root /opt/reqdoc
```

## 5 日常运维

```bash
cd /opt/reqdoc

# 启停（compose 命令统一带这三个参数，否则会另起一套项目）
docker compose --project-name reqdoc --project-directory /opt/reqdoc -f docker-compose.yml ps
docker compose --project-name reqdoc --project-directory /opt/reqdoc -f docker-compose.yml stop
docker compose --project-name reqdoc --project-directory /opt/reqdoc -f docker-compose.yml up -d

# 日志
docker compose --project-name reqdoc --project-directory /opt/reqdoc -f docker-compose.yml logs -f api

# 数据库备份（升级前必做）
bash scripts/backup-db.sh --install-root /opt/reqdoc --label before-upgrade

# 重算全局检索索引（顶栏 ⌘K 搜索用；见下）
docker compose --project-name reqdoc --project-directory /opt/reqdoc -f docker-compose.yml \
  --profile ops run --rm reindex
```

**关于全局检索索引**：顶栏 ⌘K 的全局检索读的是一张派生索引表，该表**不随业务数据写入自动回填**——新录入的条目、知识项、文档要等重算之后才搜得到。请把上面这条 `reindex` 命令加入定时任务（例如每晚一次），否则用户会看到「明明有条目却搜不出来」。重算是全量幂等操作，重复执行安全；数据量为数万节点时耗时秒级到分钟级。其余功能（条目形成、评审、发布、追溯、图表）都不依赖这张表。

数据存放位置：业务数据在 Docker 命名卷 `reqdoc_reqdoc_pg_data`，导出的文档在 `reqdoc_reqdoc_exports`，两者不随容器重建丢失。前端页面产物在 `/opt/reqdoc/frontend-dist`（宿主目录，升级时整目录替换）。

## 6 常见故障

| 现象 | 原因与处置 |
|---|---|
| 第 3 步探针镜像导入失败 | 本机 Docker 版本过旧，无法识别新格式镜像归档。回传 `docker version` 与 `docker compose version` 的完整输出，开发侧改用旧格式重新出包。此时环境未被改动，直接退出即可 |
| 第 1 步报端口被占用 | 换端口：`--set APP_PORT=<其他端口>` 重跑 |
| 第 7 步迁移失败 | 多为数据库口令或既有数据卷冲突。查看 `docker compose ... logs db`；全新安装可删除数据卷后重装（**会清空数据**） |
| 页面能打开但操作报错 | 先看 `logs api`；再执行 `verify-release.sh` 看是哪一项终态不符 |
| AI 对话回复固定内容、要素识别结果为空 | `LLM_BASE_URL` 未配置或端点不通。改 `/opt/reqdoc/.env` 后重启：`docker compose ... up -d api worker` |
| 导出的 Word 文档里图变成了代码文本 | 工具链缺失。执行自检第 8 项确认；正常包不应出现 |

## 7 升级

日常代码更新走差分包（另行提供），只替换后端源码与前端产物，不重传镜像。升级前务必先执行 `backup-db.sh`。
