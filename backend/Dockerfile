# 需求治理平台后端镜像（多阶段，一份 Dockerfile 出两个镜像）：
#   deps   —— Python 依赖层（uv + uv.lock）。worker/api 共用，不含 LibreOffice、不含源码。
#   worker —— deps + 源码，跑 RQ worker。docx 生成走 python-docx，不需要 LibreOffice → 瘦镜像。
#   api    —— deps + LibreOffice + 中文字体 + 源码，跑 uvicorn。精确预览(docx→PDF)在 API 进程内调 soffice。
#
# 构建：
#   docker compose build worker                       # worker 目标（默认，无 LibreOffice）
#   docker compose --profile release build api        # api 目标（含 LibreOffice）
#   docker build --target worker -t reqdoc-worker .    # 直接 docker build 亦可
#   docker build --target api    -t reqdoc-api    .
FROM python:3.12-slim AS deps

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

RUN pip install --no-cache-dir uv

# 先按锁文件装依赖（利用层缓存；不装项目本身，代码用 PYTHONPATH 直接 import）。
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ------------------------------------------------------------------ worker 镜像 --
# 不含 LibreOffice：worker 只跑 8 个 AI 任务 + docx 生成(python-docx)，都不需要真实排版引擎。
FROM deps AS worker
COPY . .
CMD ["sh", "-c", "uv run --no-sync rq worker -u \"${REDIS_URL:-redis://redis:6379/0}\" intake"]

# --------------------------------------------------------------------- api 镜像 --
# 精确预览（候选/基线 docx → PDF）依赖 LibreOffice 无头转换 + 中文字体（否则中文显示豆腐块）。
# 离线交付用的就是本目标：api 与 worker 容器共用它，避免两容器版本分叉（离线发布打包方案 §1.1）。
# 系统软件层全部放在 COPY 源码之前：改代码只重建源码层，不重装这一千多 MB。
FROM deps AS api
RUN apt-get update \
    && apt-get install -y --no-install-recommends libreoffice-writer fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# 图形源码栅格化工具链（发布预览与 docx 导出把 mermaid/plantuml 围栏出成真图，缺失则降级为源码文本块）：
#   plantuml 一路 —— default-jre-headless 跑 plantuml.jar，graphviz 供关系类图布局；
#   mermaid  一路 —— nodejs 跑 mmdc，chromium 供 mmdc 量文字尺寸算版面。
# tools/puppeteer.json 里写死的是 /usr/bin/google-chrome（宿主机形态），容器内建同名符号链接指向
# Debian 的 chromium，一份配置同时适配两处，不必为容器另存一份配置。
RUN apt-get update \
    && apt-get install -y --no-install-recommends default-jre-headless graphviz nodejs npm chromium \
    && ln -sf /usr/bin/chromium /usr/bin/google-chrome \
    && rm -rf /var/lib/apt/lists/*

# PUPPETEER_SKIP_DOWNLOAD=1：不让 puppeteer 另下一份 Chromium（约 400 MB），改用上面装的系统 chromium。
RUN PUPPETEER_SKIP_DOWNLOAD=1 npm install -g @mermaid-js/mermaid-cli@11.16.0 \
    && npm cache clean --force

COPY . .

# 镜像内补丁台账首行：全量基线记录。状态文件与它互证，可发现绕过脚本的手工改动（§4.3、§7.1）。
ARG PACKAGE_ID=dev
ARG GIT_COMMIT=unknown
ARG BUILT_AT=unknown
RUN printf 'base package_id=%s git_commit=%s built_at=%s\n' \
    "${PACKAGE_ID}" "${GIT_COMMIT}" "${BUILT_AT}" > /app/PATCHES.txt

CMD ["sh", "-c", "uv run --no-sync uvicorn app.main:app --host 0.0.0.0 --port \"${API_PORT:-8000}\""]
