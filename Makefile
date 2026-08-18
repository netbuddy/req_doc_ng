# 需求治理平台 —— 本地启停编排。
#
# 调试友好的分层：
#   基础设施（后台，无业务代码）：Postgres + Redis        → make infra-up / infra-down
#   业务进程（前台，各占一个终端，日志实时打印）：
#       后端 API   uvicorn --reload                       → make backend
#       识别 worker RQ（异步模式需要；跑本机 live 代码）  → make worker
#       前端       Vite dev server                        → make frontend
#
# 典型调试：终端0 `make infra-up`（一次）；终端1 `make backend`；终端2 `make worker`；终端3 `make frontend`。
# Ctrl-C 即停对应前台进程。容器化 worker（不调试 worker 代码时）见 worker-bg。

SHELL       := /bin/bash
COMPOSE     := docker compose
UV          := uv
NPM         := npm

BACKEND_DIR := backend
FRONTEND_DIR:= frontend

API_HOST    := 127.0.0.1
API_PORT    := 8000
WEB_PORT    := 5173
REDIS_LOCAL := redis://localhost:6379/0
# 追加 uvicorn 参数（默认已带 --reload）
UVICORN_ARGS ?=

.DEFAULT_GOAL := help
.PHONY: help infra-up infra-down backend worker frontend check-report-deck \
        stop-backend stop-worker stop-frontend down status \
        migrate build build-api worker-bg logs-worker

help: ## 显示可用任务
	@echo "需求治理平台 · 本地启停（前台进程各占一个终端）"
	@grep -E '^[a-zA-Z0-9_-]+:.*##' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

# ------------------------------------------------------ 基础设施（后台） ----
infra-up: ## [后台] 启动基础设施：Postgres + Redis
	$(COMPOSE) up -d --wait db redis
	@echo ">> 基础设施就绪（db:5432 / redis:6379）"

infra-down: ## [后台] 停止所有容器（db/redis/worker/pgadmin）
	$(COMPOSE) stop

# ------------------------------------------ 业务进程（前台，实时打印日志） ----
backend: infra-up ## [前台] 后端 API（uvicorn --reload；日志打到本终端）
	@echo ">> 后端 API 前台运行于 http://$(API_HOST):$(API_PORT)（Ctrl-C 停止）"
	$(UV) run --directory $(BACKEND_DIR) uvicorn app.main:app \
	  --host $(API_HOST) --port $(API_PORT) --reload $(UVICORN_ARGS)

worker: infra-up ## [前台] 识别/AI worker（RQ；跑本机 live 代码；日志打到本终端）
	-@$(COMPOSE) stop worker >/dev/null 2>&1 || true   # 避免与容器 worker 抢任务
	@echo ">> worker 前台监听队列 intake（Ctrl-C 停止）"
	cd $(BACKEND_DIR) && PYTHONPATH=. $(UV) run rq worker -u $(REDIS_LOCAL) intake

frontend: ## [前台] 前端 Vite dev server（日志打到本终端）
	@echo ">> 前端前台运行于 http://$(API_HOST):$(WEB_PORT)（Ctrl-C 停止）"
	$(NPM) --prefix $(FRONTEND_DIR) run dev

# ------------------------------------------------------------ 停止 & 辅助 ----
stop-backend: ## 停止后端 API（回收本机 :8000）
	-@fuser -k -TERM $(API_PORT)/tcp >/dev/null 2>&1 && echo ">> 已停 API(:$(API_PORT))" || true

stop-worker: ## 停止 worker（容器 worker + 本机 RQ worker）
	-@$(COMPOSE) stop worker >/dev/null 2>&1 && echo ">> 已停容器 worker" || true
	-@pgrep -f "rq worker.*[i]ntake" | xargs -r kill -TERM >/dev/null 2>&1 && echo ">> 已停本机 worker(intake)" || true

stop-frontend: ## 停止前端 Vite dev server（回收本机 :5173）
	-@fuser -k -TERM $(WEB_PORT)/tcp >/dev/null 2>&1 && echo ">> 已停前端(:$(WEB_PORT))" || true

down: ## 停止整个系统（回收本机 :8000/:5173 + 停所有容器）
	@$(MAKE) --no-print-directory stop-backend
	@$(MAKE) --no-print-directory stop-frontend
	@$(MAKE) --no-print-directory stop-worker
	$(COMPOSE) stop

status: ## 查看容器与端口状态
	@echo "== 容器 =="; $(COMPOSE) ps
	@echo "== 本机进程 =="; \
	  (fuser $(API_PORT)/tcp >/dev/null 2>&1 && echo "API  :$(API_PORT)  运行中" || echo "API  :$(API_PORT)  未运行"); \
	  (fuser $(WEB_PORT)/tcp >/dev/null 2>&1 && echo "前端 :$(WEB_PORT)  运行中" || echo "前端 :$(WEB_PORT)  未运行")

check-report-deck: ## 校验项目汇报大纲、演示脚本、图片清单与顶层图片完全一致
	bash docs/reports/slides/check-slide-deck.sh

migrate: ## 应用数据库迁移（alembic upgrade head）
	cd $(BACKEND_DIR) && PYTHONPATH=. $(UV) run alembic upgrade head

build: ## 构建 worker 容器镜像（瘦镜像，不含 LibreOffice）
	$(COMPOSE) build worker

build-api: ## 构建发布用 API 容器镜像（含 LibreOffice + 中文字体；精确预览用）
	$(COMPOSE) --profile release build api

worker-bg: ## [后台] 以容器方式跑 worker（不调试 worker 代码时；勿与 make worker 同开）
	$(COMPOSE) up -d worker

logs-worker: ## 跟踪容器 worker 日志
	$(COMPOSE) logs -f worker
