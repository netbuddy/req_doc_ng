"""FastAPI 应用装配。FE 面路由挂 /api；错误按 shared/前端契约适配 §3 映射。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse

from app.api import (
    materials,
    agent_runs,
    ai_effectiveness,
    analysis,
    assets,
    charts,
    config,
    diagrams,
    health,
    intake,
    item_formation,
    item_review,
    notifications,
    overview,
    projects,
    publication,
    runtime_status,
    search,
    templates,
    trace,
    transcript,
    workbench_reserved,
)
from app.config import settings
from app.domain.errors import InvalidInput, NotFound, RejectedTransition
from app.workers.queue import warn_if_async_without_worker


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        warn_if_async_without_worker()  # 韧性：REDIS_URL 已配但无 worker → WARN，避免 job 静默排队
    except Exception:
        pass
    yield


app = FastAPI(title="req-doc backend", version=settings.version, lifespan=lifespan)


@app.exception_handler(RejectedTransition)
async def _handle_rejected(_: Request, exc: RejectedTransition) -> JSONResponse:
    # 默认拒绝（非法状态迁移）→ 409 + {success:false,error}
    return JSONResponse(status_code=409, content={"success": False, "error": str(exc)})


@app.exception_handler(NotFound)
async def _handle_not_found(_: Request, exc: NotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content={"success": False, "error": str(exc)})


@app.exception_handler(InvalidInput)
async def _handle_invalid_input(_: Request, exc: InvalidInput) -> JSONResponse:
    return JSONResponse(status_code=400, content={"success": False, "error": str(exc)})


app.include_router(health.router, prefix="/api")
app.include_router(runtime_status.router, prefix="/api")
app.include_router(notifications.router, prefix="/api")
app.include_router(intake.router, prefix="/api")
app.include_router(materials.router, prefix="/api")
app.include_router(analysis.router, prefix="/api")
app.include_router(item_formation.router, prefix="/api")
app.include_router(item_review.router, prefix="/api")
app.include_router(charts.router, prefix="/api")
app.include_router(diagrams.router, prefix="/api")
app.include_router(publication.router, prefix="/api")
app.include_router(templates.router, prefix="/api")
app.include_router(agent_runs.router, prefix="/api")
app.include_router(projects.router, prefix="/api")
app.include_router(overview.router, prefix="/api")
app.include_router(assets.router, prefix="/api")
app.include_router(ai_effectiveness.router, prefix="/api")
app.include_router(trace.router, prefix="/api")
app.include_router(search.router, prefix="/api")
app.include_router(config.router, prefix="/api")
app.include_router(transcript.router, prefix="/api")
app.include_router(workbench_reserved.router, prefix="/api")

# ---------------------------------------------------------------- 前端静态产物 --
# 离线部署形态：前端与 API 同源，由本进程直接服务 Vite 产物（FRONTEND_DIST 指向 dist 目录）。
# 本地开发不设该变量 → 整段不生效，前端仍走 vite dev server 的 /api 代理。
# 顺序纪律：本段必须在全部 /api 路由注册之后，兜底路由才不会截走接口请求。
_frontend_dist = Path(settings.frontend_dist_dir).resolve() if settings.frontend_dist_dir else None

if _frontend_dist is not None and (_frontend_dist / "index.html").is_file():
    _index_html = _frontend_dist / "index.html"

    @app.get("/{spa_path:path}", include_in_schema=False)
    async def serve_frontend(spa_path: str) -> Response:
        # 未命中的 /api 路径必须是 404 而不是 index.html：否则前端把 HTML 当 JSON 解析，
        # 接口打错字的症状会从「404」退化成「解析失败」，难以归因。
        if spa_path == "api" or spa_path.startswith("api/"):
            return JSONResponse(status_code=404, content={"success": False, "error": "Not Found"})
        candidate = (_frontend_dist / spa_path).resolve() if spa_path else _index_html
        # 目录穿越防线：请求路径归一化后必须仍在 dist 之内（../ 拼出的路径在此被挡回首页）。
        inside_dist = candidate == _frontend_dist or _frontend_dist in candidate.parents
        if spa_path and inside_dist and candidate.is_file():
            # 资源文件名带内容指纹（内容变则文件名变），可长期缓存。
            return FileResponse(candidate, headers={"Cache-Control": "public, max-age=31536000, immutable"})
        # 其余一律回落首页（单页应用的前端路由由浏览器侧接管）。
        # 首页禁缓存：升级后旧首页引用的指纹文件已不存在，浏览器拿旧首页的症状是白屏。
        return FileResponse(_index_html, headers={"Cache-Control": "no-cache"})
