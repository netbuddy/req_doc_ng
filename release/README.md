# release/ —— 离线交付脚本

本目录是**开发侧**的离线发布工具，产出交付给无外网生产机器的安装包。设计依据：`docs/proposals/离线发布打包方案.md`（全量包体系）与 `docs/proposals/离线部署增量更新方案.md`（更新机制）。

## 文件

| 文件 | 运行侧 | 职责 |
|---|---|---|
| `lib-common.sh` | 两侧共用 | 日志、指纹计算、环境检查、manifest 读取 |
| `pack-full.sh` | 开发机 | 构建镜像与前端产物、流式送到目标机、生成 manifest 与校验和清单 |
| `install-full.sh` | 离线机 | 十步安装：环境检查 → 包校验 → 探针 → 导入镜像 → 部署 → 迁移与模板初始化 → 启动 → 自检 → 状态文件 |
| `verify-release.sh` | 离线机 | 发布后自检：十项终态检验 |
| `status.sh` | 离线机 | 现状查询与状态文件/镜像内台账互证 |
| `backup-db.sh` | 离线机 | 数据库备份（`pg_dump -Fc`） |
| `compose/docker-compose.offline.yml` | 离线机 | 离线编排：镜像按标签引用、无 build 段、含迁移与模板初始化两个一次性服务 |
| `compose/env.template` | 离线机 | 环境变量模板 |
| `INSTALL.md` | 离线机 | 交给运维人员的安装手册，随包发出 |

## 打全量包

```bash
release/pack-full.sh --dest <ssh目标>:<目录> [--package-id full-YYYYMMDD-NN]
```

**镜像归档与前端产物包不在开发机落盘**：`docker save` 与 `tar` 的输出经 ssh 管道直接写到目标机；本地只用命名管道旁路算一次 sha256，与目标机上落地文件的 sha256 比对，端到端确认传输无损。开发机上留下的只有构建产生的镜像本身（在 Docker 存储里）与 `frontend/dist`。

出包前有两道门禁：构建面（`backend/ frontend/ release/ docker-compose.yml`）必须没有未提交改动，否则 manifest 里的 git 提交号是假话（确需例外用 `--allow-dirty`，标注会写进 manifest）；镜像内工具链自检必须全部就位，缺一项即拒绝出包。

## 安装与自检

见 `INSTALL.md`。包内 `scripts/` 是本目录离线机侧脚本的副本，安装脚本会把它们复制到安装目录，后续运维直接用安装目录下的副本。

## 与开发用编排的关系

仓库根的 `docker-compose.yml` 是开发用的（带 build 段、发布数据库端口、api 挂在 release profile 下），与本目录的离线编排互不影响，不要混用。
