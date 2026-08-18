"""SCN-005 演示数据一键就位（docs/iterations/SCN-005/演示脚本.md §0）。

幂等：按 req_no 去重。就位内容：演示项目 + 1 份已接入材料 +
6 条确认态需求条目（功能×3、质量、约束、接口）+ 2 条待确认条目（演候选门禁）。
用法：cd backend && uv run python -m app.scripts.seed_scn005_demo
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.config import DEMO_PROJECT_ID, settings
from app.db.base import make_engine, make_session_factory
from app.db.models import Material, Project, RequirementItem
from app.scripts.seed_demo_project import ensure_demo_project

RAW_TEXT = (
    "关于订单模块的需求，整理如下，供评审。"
    "用户下单后，系统要通过短信和邮件给用户发送通知。"
    "订单金额超过 500 元时，需要走人工审核后才能提交。"
    "系统应支持把订单导出为 PDF 格式，方便财务归档。"
    "历史订单数据至少保留三年。"
    "库存不足时，下单要被拦截并提示用户。"
    "订单查询页面的响应时间不超过两秒。"
    "系统必须部署在企业内网环境。"
    "系统应提供 OpenAPI 兼容的订单查询接口。"
)

ITEMS = [
    ("FR-001", "用户下单后，系统应通过短信和邮件向用户发送通知", "functional", "confirmed"),
    ("FR-002", "订单金额超过 500 元时，系统应要求人工审核通过后方可提交", "functional", "confirmed"),
    ("FR-003", "系统应支持将订单导出为 PDF 格式以便财务归档", "functional", "confirmed"),
    ("NFR-001", "订单查询页面的响应时间不超过两秒", "quality", "confirmed"),
    ("CON-001", "系统必须部署在企业内网环境", "constraint", "confirmed"),
    ("IF-001", "系统应提供 OpenAPI 兼容的订单查询接口", "interface", "confirmed"),
    ("FR-008", "历史订单数据至少保留三年", "functional", "pending_confirmation"),
    ("FR-009", "库存不足时，系统应拦截下单并提示用户", "functional", "pending_confirmation"),
]


def main() -> None:
    engine = make_engine(settings.database_url)
    session = make_session_factory(engine)()
    try:
        pid = ensure_demo_project(session, name="订单模块（演示）")
        project = session.get(Project, pid)
        if project is not None and project.scope is None:
            project.scope = "release-v0.1"

        material = session.scalars(
            select(Material).where(Material.project_id == pid)
        ).first()
        if material is None:
            material = Material(
                project_id=pid,
                raw_text=RAW_TEXT,
                source_note="订单模块需求评审整理，2026-03-15",
            )
            session.add(material)
            session.flush()

        existing = {
            r.req_no for r in session.scalars(
                select(RequirementItem).where(RequirementItem.project_id == pid)
            ).all()
        }
        created = 0
        for req_no, expression, req_type, status in ITEMS:
            if req_no in existing:
                continue
            session.add(RequirementItem(
                project_id=pid, parse_result_ref=uuid.uuid4(), formation_context_ref=uuid.uuid4(),
                req_no=req_no, expression=expression, req_type=req_type, status=status,
                source_element_refs="[]",
            ))
            created += 1
        session.commit()
        confirmed = sum(1 for i in ITEMS if i[3] == "confirmed")
        print(f"seed 完成：项目 {DEMO_PROJECT_ID}，材料 1 份，新建条目 {created} 条"
              f"（目标：确认态 {confirmed} / 待确认 {len(ITEMS) - confirmed}）")
    finally:
        session.close()


if __name__ == "__main__":
    main()
