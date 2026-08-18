"""材料读侧路由（V2 契约对齐的第一个接口）。

响应约定＝V2 治理接口的应答信封（api/openapi.yaml listMaterials）：成功与业务拒绝
同走 200、以 result 字段区分——与本仓其余路由的「2xx 裸 DTO」约定不同，属有意为之：
V2 切换期的新增接口一律走信封，存量接口不动（docs/v2/drafts/V1现状与V2蓝图差距对照）。
"""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Material
from app.deps import get_read_session

router = APIRouter(tags=["materials"])


class MaterialSummary(BaseModel):
    """材料摘要——列表一行所需字段（对齐 V2 契约 MaterialSummary）。"""

    material_id: str = Field(description="材料标识（UUID）——材料的唯一身份；名称只是展示标签。")
    name: str = Field(description="材料名称——展示用标签，允许同名；粘贴导入默认取正文首行。")
    source_kind: str = Field(description="来源形态：「文件」或「粘贴」。当前实现只有粘贴一种。")
    imported_at: str = Field(description="导入时刻（ISO 8601）。")
    content_sha256: str = Field(description="内容哈希（SHA-256）——「导入即不可改写」的机器凭据；早于加列的存量材料此字段为空串。")


class SuccessOfMaterialList(BaseModel):
    """成功信封：材料列表。"""

    result: Literal["成功"] = Field(description="应答信封结果字段，本操作恒为「成功」。")
    data: list[MaterialSummary] = Field(description="项目内全部材料，按导入时刻倒序。")


@router.get(
    "/projects/{project_id}/materials",
    response_model=SuccessOfMaterialList,
    summary="列出项目内全部材料",
    description="返回项目内全部材料的摘要列表（名称、来源形态、导入时刻、内容哈希），按导入时刻倒序。",
)
def list_materials(
    project_id: str,
    session: Session = Depends(get_read_session),
) -> SuccessOfMaterialList:
    rows = session.scalars(
        select(Material)
        .where(Material.project_id == uuid.UUID(project_id))
        .order_by(Material.created_at.desc())
    ).all()
    return SuccessOfMaterialList(
        result="成功",
        data=[
            MaterialSummary(
                material_id=str(m.id),
                name=m.name,
                source_kind="粘贴",
                imported_at=m.created_at.isoformat(),
                content_sha256=m.content_sha256,
            )
            for m in rows
        ]
    )
