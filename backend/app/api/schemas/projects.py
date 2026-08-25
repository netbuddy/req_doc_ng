"""项目上下文服务（业务项目 LDM-001）。

自 schemas.py 按业务域拆包（命名规范见包入口 __init__.py）。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ---- 项目上下文服务（业务项目 LDM-001；2026-08-07 项目管理组重构：V2 应答信封）----

class CreateProjectCommand(BaseModel):
    """createProject 入参（含操作者与幂等键，对齐 V1 写接口纪律与 V2 留痕要求）。"""

    name: str = Field(description="项目名称（必填，去首尾空白后不得为空）。")
    scope: str | None = Field(default=None, description="项目范围说明（选填）。")
    background: str | None = Field(default=None, description="项目背景说明（选填）。")
    domain_profile_key: str | None = Field(default=None, description="领域档案键（封闭集，选填；缺省不注入领域先验）。")
    operator_ref: str = Field(description="操作者标识——创建者，随行存储（V2 操作留痕的接口准备）。")
    idempotency_key: str = Field(description="幂等键——同键重放返回同一项目，不重复建行。")


class ProjectSummary(BaseModel):
    """项目摘要——列表一行所需字段（详情走「读单个项目」）。"""

    project_id: str = Field(description="项目标识（UUID）。")
    name: str = Field(description="项目名称。")
    created_at: str = Field(description="创建时刻（ISO 8601）。")


class ProjectDetail(BaseModel):
    """项目详情——单读与创建回执的载荷。"""

    project_id: str = Field(description="项目标识（UUID）。")
    name: str = Field(description="项目名称。")
    scope: str | None = Field(default=None, description="项目范围说明。")
    background: str | None = Field(default=None, description="项目背景说明。")
    domain_profile_key: str | None = Field(default=None, description="领域档案键（封闭集；空=不注入领域先验）。")
    domain_profile_label: str = Field(description="领域档案显示名——按键派生，不落表。")
    created_at: str = Field(description="创建时刻（ISO 8601）。")


class ProjectDeletionReport(BaseModel):
    """删除清点回执（级联删净摘要；逐表明细走结构化日志）。"""

    project_id: str = Field(description="被删项目标识。")
    project_name: str = Field(description="被删项目名称。")
    deleted_rows: int = Field(description="全部表删除行数合计（含项目行自身）。")
    table_counts: dict[str, int] = Field(description="表名 → 删除行数（删净证据摘要）。")
    files_deleted: int = Field(description="落盘导出文件实删个数。")
    files_failed: int = Field(description="落盘文件删除失败个数（已记结构化日志，不回滚）。")






class SuccessOfProjectList(BaseModel):
    """成功信封：项目摘要列表。"""

    result: Literal["成功"] = Field(description="应答信封结果字段，本操作恒为「成功」。")
    data: list[ProjectSummary] = Field(description="全部项目摘要，按创建时刻升序。")


class SuccessOfProjectDetail(BaseModel):
    """成功信封：项目详情（单读与创建回执共用）。"""

    result: Literal["成功"] = Field(description="应答信封结果字段，本操作恒为「成功」。")
    data: ProjectDetail = Field(description="项目详情。")


class SuccessOfProjectDeletion(BaseModel):
    """成功信封：删除清点回执。"""

    result: Literal["成功"] = Field(description="应答信封结果字段，删除成功时为「成功」。")
    data: ProjectDeletionReport = Field(description="删除清点回执。")


class DomainProfileRead(BaseModel):
    """AEP-103 领域档案只读目录项（建项目下拉 + 设置页展示）。"""

    key: str
    label: str
    description: str = ""
    version: int = 1
