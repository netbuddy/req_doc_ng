"""健康检查（基础设施，非领域）。对齐 frontend/src/api/health.ts。"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.schemas import HealthPayload
from app.config import settings

router = APIRouter(tags=["infra"])


@router.get("/health", response_model=HealthPayload)
def get_health() -> HealthPayload:
    # v0.1 无 DB 依赖（in-memory）；接入 Postgres 后在 checks.db 加真实探活。
    return HealthPayload(
        status="ok",
        checks={"app": "ok"},
        service=settings.service,
        version=settings.version,
        environment=settings.environment,
        ready=True,
    )
