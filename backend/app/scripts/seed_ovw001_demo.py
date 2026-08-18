"""OVW-001 演示数据一键就位（docs/iterations/OVW-001/演示脚本.md §0）。

用 stub 判定/识别/格式化驱动**真实服务链**（不调模型、结局确定），种出
7 条不同阶段状态的「新增需求」流程 + 1 个空项目，供总览台阶段面板与恢复演示：

  A 接入完成待分析   B 需补充停靠   C 已排除停靠   D 分析进行中
  E 无可处理要素死路 F 已形成待确认条目 G 可形成条目（待发起批次）

幂等：按项目名去重（演示项目已存在则整体跳过）。
用法：cd backend && uv run python -m app.scripts.seed_ovw001_demo
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.llm import (
    StubSourceElementRecognizer,
    StubSourceIntakeJudge,
)
from app.api.schemas import (
    ElementDecisionCommand,
    ElementRecognitionCommand,
    ItemizationBatchCommand,
    TextIntakeCommand,
)
from app.config import settings
from app.db.base import make_engine, make_session_factory
from app.db.models import Project
from app.domain.enums import ItemizationScopeType, ModelJudgement
from app.repositories.sqlalchemy import (
    build_sql_analysis_service,
    build_sql_item_formation_service,
    build_sql_service,
)

DEMO_NAME = "运营效率演示（OVW-001）"
EMPTY_NAME = "空白项目（OVW-001）"

_FORMABLE_TYPES = {
    "functional_requirement", "quality_attribute", "constraint",
    "data_requirement", "interface_requirement",
}


def _intake(session: Session, pid: str, note: str, text: str, key: str, judgement=None) -> str:
    """经真实材料接收服务接入（stub 判定确定结局）；返回 intake context_ref。"""
    judge = StubSourceIntakeJudge(judgement) if judgement else None
    svc = build_sql_service(session, auto_complete=True, judge=judge)
    result = svc.submit_text_intake(TextIntakeCommand(
        project_ref=pid, raw_text=text, source_note=note,
        operator_ref="seed", idempotency_key=key,
    ))
    session.commit()
    return result.context_ref


def _recognize(session: Session, pid: str, ctx: str, key: str, recognizer=None):
    """经真实分析转化服务识别；返回 (analysis_svc, workspace)。"""
    svc = build_sql_analysis_service(session, auto_complete=True, recognizer=recognizer)
    intake_svc = build_sql_service(session, auto_complete=True)
    material_ref = intake_svc.read_intake_result(ctx).material_ref
    assert material_ref, f"接入 {ctx} 无材料引用"
    submitted = svc.submit_element_recognition(ElementRecognitionCommand(
        project_ref=pid, material_ref=material_ref, operator_ref="seed", idempotency_key=key,
    ))
    session.commit()
    workspace = svc.read_element_workspace(submitted.parse_context_ref)
    return svc, workspace


def _confirm_formable(session: Session, svc, workspace):
    """经真实裁定命令确认可形成类型要素（E5 门禁开）；返回刷新后的工作区。"""
    targets = [
        e.id for e in (workspace.elements or [])
        if e.element_type.value in _FORMABLE_TYPES
    ]
    assert targets, "stub 识别未产出可形成类型要素"
    updated = svc.decide_elements(ElementDecisionCommand(
        parse_context_ref=workspace.parse_context_ref,
        workspace_version=workspace.workspace_version,
        element_refs=targets, decision="confirm",
        operator_ref="seed", idempotency_key=f"confirm-{workspace.parse_context_ref}",
    ))
    session.commit()
    return updated


def _form_items(session: Session, pid: str, workspace) -> None:
    """经真实条目形成服务批次条目化（stub 格式化）。"""
    svc = build_sql_item_formation_service(session, auto_complete=True)
    svc.start_element_itemization_batch(ItemizationBatchCommand(
        project_ref=pid,
        parse_result_ref=workspace.parse_result_ref,
        workspace_version=workspace.workspace_version,
        scope_type=ItemizationScopeType.ALL_ELIGIBLE,
        operator_ref="seed", idempotency_key=f"form-{workspace.parse_context_ref}",
    ))
    session.commit()


def main() -> None:
    engine = make_engine(settings.database_url)
    session = make_session_factory(engine)()
    try:
        existing = session.scalars(select(Project).where(Project.name == DEMO_NAME)).first()
        if existing is not None:
            print(f"seed 跳过：项目「{DEMO_NAME}」已存在（{existing.id}）")
            return

        demo = Project(name=DEMO_NAME, scope="release-v0.1", background="OVW-001 总览台演示")
        session.add(demo)
        if session.scalars(select(Project).where(Project.name == EMPTY_NAME)).first() is None:
            session.add(Project(name=EMPTY_NAME, scope="release-v0.1", background="总览台空态演示"))
        session.flush()
        pid = str(demo.id)
        session.commit()

        # A 接入完成待分析（行5：accepted、未发起识别）
        _intake(session, pid, "A-支付对账需求（待分析）",
                "系统应支持每日自动生成支付对账单。对账差异要能导出明细。",
                "ovw001-a")

        # B 需补充停靠（行3）
        _intake(session, pid, "B-模糊描述材料（需补充）",
                "这个功能大概就是那样，先做起来再说。",
                "ovw001-b", judgement=ModelJudgement.INSUFFICIENT_CONTENT)

        # C 已排除停靠（行4）
        _intake(session, pid, "C-闲聊记录（已排除）",
                "中午吃什么？楼下新开了家面馆，据说不错。",
                "ovw001-c", judgement=ModelJudgement.NO_ASSET_VALUE)

        # D 分析进行中（行9：parsed、要素待确认）
        ctx_d = _intake(session, pid, "D-订单导出需求（分析中）",
                        "系统应支持把订单导出为 PDF。导出耗时不超过五秒。系统必须部署在内网。目标是减少人工归档。",
                        "ovw001-d")
        _recognize(session, pid, ctx_d, "ovw001-d-rec")

        # E 无可处理要素死路（行8：unprocessable）
        ctx_e = _intake(session, pid, "E-流水账记录（无可处理要素）",
                        "上周开了三次会。会议纪要还没整理。下周继续讨论。",
                        "ovw001-e")
        _recognize(session, pid, ctx_e, "ovw001-e-rec",
                   recognizer=StubSourceElementRecognizer(elements=()))

        # F 已形成待确认条目（行14）
        ctx_f = _intake(session, pid, "F-导出与通知需求（已形成条目）",
                        "系统应支持将报表导出为 docx。导出完成后要通知用户。响应时间不超过两秒。系统必须支持单点登录。",
                        "ovw001-f")
        svc_f, ws_f = _recognize(session, pid, ctx_f, "ovw001-f-rec")
        ws_f = _confirm_formable(session, svc_f, ws_f)
        _form_items(session, pid, ws_f)

        # G 可形成条目、未发起批次（行11）
        ctx_g = _intake(session, pid, "G-权限管理需求（可形成条目）",
                        "系统应支持按角色分配权限。权限变更需要留痕。审计日志至少保留一年。",
                        "ovw001-g")
        svc_g, ws_g = _recognize(session, pid, ctx_g, "ovw001-g-rec")
        _confirm_formable(session, svc_g, ws_g)

        print(f"seed 完成：演示项目「{DEMO_NAME}」（{pid}）7 条流程 + 空项目「{EMPTY_NAME}」")
        print("  A 待分析 / B 需补充 / C 已排除 / D 分析中 / E 死路 / F 已形成 / G 可形成")
    finally:
        session.close()


if __name__ == "__main__":
    main()
