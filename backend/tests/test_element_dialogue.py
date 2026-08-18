"""AEP-096 区5 对话命令解释：命令词确定性解析 + Stub 解释 + 校验派发。

设计事实源：docs/40 slices/SCN-001-P02/页面详细设计.md §5.2、前端交互与接口.md §5.1、
docs/30 06A §3.4（AEP-096）。Stub 解释器 = 原前端确定性解析的移植，无 LLM 环境全程确定性。
"""
import uuid

from app.adapters.llm import CommandInterpretation
from app.api.schemas import ElementDialogueCommand, SourceAnchorRange
from app.domain.enums import ElementProcessStatus as ES
from app.repositories.in_memory import build_analysis_wiring

RAW = "系统应支持一键导出所需数据。导出任务需在30秒内完成。系统要给用户发送通知。希望提供统一的数据工作台。库存不足时下单要被拦截并提示用户。"


def _wiring(**kwargs):
    w = build_analysis_wiring(auto_complete=True, **kwargs)
    w.source_assets.seed_material("M-1", raw_text=RAW, accepted=True)
    return w


def _workspace(w, key="K1"):
    from app.api.schemas import ElementRecognitionCommand

    r = w.service.submit_element_recognition(ElementRecognitionCommand(
        project_ref="P-1", material_ref="M-1", operator_ref="U1", idempotency_key=key,
    ))
    return r.parse_context_ref, w.service.read_element_workspace(r.parse_context_ref)


def _dialogue(w, ctx, message, targets=None, ranges=None, version=None):
    ws = w.service.read_element_workspace(ctx)
    return w.service.element_dialogue(ElementDialogueCommand(
        parse_context_ref=ctx,
        workspace_version=version or ws.workspace_version,
        message=message,
        target_element_refs=targets or [],
        selected_text_ranges=ranges or [],
        operator_ref="U1",
        idempotency_key=uuid.uuid4().hex,
    ))


class _FakeInterpreter:
    def __init__(self, interpretation):
        self._interpretation = interpretation
        self.calls = 0

    def interpret(self, command_word, message, context):
        self.calls += 1
        return self._interpretation


# ---- 命令词确定性解析层 ----

def test_unknown_command_deterministic_reply_no_llm_no_dispatch():
    w = _wiring()
    ctx, ws = _workspace(w)
    fake = _FakeInterpreter(CommandInterpretation(status="done", operation="review"))
    w.service._command_interpreter = fake
    r = _dialogue(w, ctx, "/不存在 随便写")
    assert r.outcome == "unknown_command" and r.command_word == "不存在"
    assert "可用命令" in r.message
    assert fake.calls == 0  # 不调模型
    assert w.service.read_element_workspace(ctx).workspace_version == ws.workspace_version  # 无派发


def test_empty_message_clarifies():
    w = _wiring()
    ctx, _ = _workspace(w)
    assert _dialogue(w, ctx, "   ").outcome == "clarify"


def test_version_conflict_rejects_before_dispatch():
    w = _wiring()
    ctx, ws = _workspace(w)
    r = _dialogue(w, ctx, "/改类型 功能需求", targets=[ws.elements[0].id], version="stale-0")
    assert r.outcome == "rejected_precheck" and "版本不一致" in r.message


# ---- 就地修订（executed：内联工作区）----

def test_adjust_type_executes_and_returns_workspace():
    w = _wiring()
    ctx, ws = _workspace(w)
    target = ws.elements[0]
    r = _dialogue(w, ctx, "/改类型 改为「质量属性」。", targets=[target.id])
    assert r.outcome == "executed" and r.operation == "edit.adjust_type"
    assert r.command_word == "改类型" and r.operation_label
    updated = next(e for e in r.workspace.elements if e.id == target.id)
    assert updated.element_type.value == "quality_attribute"
    assert updated.version == target.version + 1


def test_adjust_type_without_target_clarifies():
    w = _wiring()
    ctx, _ = _workspace(w)
    r = _dialogue(w, ctx, "/改类型 功能需求")
    assert r.outcome == "clarify" and "选中目标要素" in r.message


def test_revise_expression_with_explicit_content_executes():
    w = _wiring()
    ctx, ws = _workspace(w)
    target = ws.elements[0]
    r = _dialogue(w, ctx, "/改表达 修订为：系统应支持一键导出数据（含权限校验）。", targets=[target.id])
    assert r.outcome == "executed" and r.operation == "edit.revise_expression"
    updated = next(e for e in r.workspace.elements if e.id == target.id)
    assert "权限校验" in updated.content


def test_revise_expression_drift_soft_note():
    """就地修订不阻断（人工权威），但表达超出来源证据时回执带偏离软提示。"""
    w = _wiring()
    ctx, ws = _workspace(w)
    target = ws.elements[1]  # 「导出任务需在30秒内完成。」
    r = _dialogue(w, ctx, "/改表达 修订为：导出任务需在300秒内完成。", targets=[target.id])
    assert r.outcome == "executed" and r.operation == "edit.revise_expression"
    assert "偏离" in (r.message or "") and "300" in r.message
    assert "勘误" in r.message and "补入" in r.message
    updated = next(e for e in r.workspace.elements if e.id == target.id)
    assert updated.source_drift_tokens == ["300"]


def test_revise_expression_direction_only_queues_ai_revision():
    w = _wiring()
    ctx, ws = _workspace(w)
    r = _dialogue(w, ctx, "/改表达 把主语补全，表达更正式一些", targets=[ws.elements[0].id])
    assert r.outcome == "queued" and r.operation == "revise.ai"
    assert r.agent_run_ref


def test_adjust_anchor_requires_selection_then_executes():
    w = _wiring()
    ctx, ws = _workspace(w)
    target = ws.elements[0].id
    r = _dialogue(w, ctx, "/改范围 调整为当前选区。", targets=[target])
    assert r.outcome == "clarify" and "区3" in r.message
    ranges = [SourceAnchorRange(start=0, end=13, exact="系统应支持一键导出所需数据。")]
    r2 = _dialogue(w, ctx, "/改范围 调整为当前选区。", targets=[target], ranges=ranges)
    assert r2.outcome == "executed" and r2.operation == "edit.adjust_anchor"


# ---- 版本关系层（变更草案）与 AI 执行 ----

def test_split_with_explicit_lines_creates_draft():
    w = _wiring()
    ctx, ws = _workspace(w)
    r = _dialogue(w, ctx, "/拆分 1. 系统应支持一键导出\n2. 导出需覆盖所需数据", targets=[ws.elements[0].id])
    assert r.outcome == "executed" and r.operation == "manual.split"
    assert r.workspace.change_draft is not None
    assert r.workspace.change_draft.operation_type == "split"


def test_split_direction_only_queues_ai_execution():
    w = _wiring()
    ctx, ws = _workspace(w)
    r = _dialogue(w, ctx, "/拆分 按导出动作和数据范围拆", targets=[ws.elements[0].id])
    assert r.outcome == "queued" and r.operation == "ai_execution.split"


def test_merge_resolves_targets_by_quoted_names():
    w = _wiring()
    ctx, ws = _workspace(w)
    r = _dialogue(
        w, ctx, "/合并 与「导出任务」合并，合并后表达由 AI 起草。", targets=[ws.elements[0].id],
    )
    assert r.outcome == "queued" and r.operation == "ai_execution.merge"
    assert r.params_echo["target_element_refs"] == [ws.elements[0].id, ws.elements[1].id]


def test_merge_with_explicit_expression_creates_manual_draft():
    w = _wiring()
    ctx, ws = _workspace(w)
    r = _dialogue(
        w, ctx, "/合并 与「导出任务」合并，合并后表达：系统应在30秒内完成一键导出。",
        targets=[ws.elements[0].id],
    )
    assert r.outcome == "executed" and r.operation == "manual.merge"
    assert r.workspace.change_draft.operation_type == "merge"


def test_merge_unresolvable_names_clarifies():
    w = _wiring()
    ctx, ws = _workspace(w)
    r = _dialogue(w, ctx, "/合并 与「不存在的要素」合并。", targets=[ws.elements[0].id])
    assert r.outcome == "clarify"


def test_add_missing_creates_draft():
    w = _wiring()
    ctx, _ = _workspace(w)
    r = _dialogue(w, ctx, "/新增遗漏 补登一条：库存不足时下单要被拦截并提示用户。")
    assert r.outcome == "executed" and r.operation == "manual.add_missing"
    assert r.workspace.change_draft.operation_type == "add_missing"


# ---- 改源（勘误 / 补入）----

def test_erratum_executes_and_affected_elements_reset():
    w = _wiring()
    ctx, ws = _workspace(w)
    r = _dialogue(w, ctx, "/勘误 把「30秒」改正为「10秒」。")
    assert r.outcome == "executed" and r.operation == "erratum"
    assert "10秒" in r.workspace.material_canvas.raw_text


def test_erratum_missing_pair_clarifies():
    w = _wiring()
    ctx, _ = _workspace(w)
    r = _dialogue(w, ctx, "/勘误 原文写错了")
    assert r.outcome == "clarify" and "勘误格式" in r.message


def test_supplement_requires_basis_then_executes():
    w = _wiring()
    ctx, _ = _workspace(w)
    r = _dialogue(w, ctx, "/补入 导出并发上限为10")
    assert r.outcome == "clarify" and "依据" in r.message
    r2 = _dialogue(w, ctx, "/补入 导出并发上限为10（依据：0705评审会张三口径）")
    assert r2.outcome == "executed" and r2.operation == "supplement"
    assert any("导出并发上限" in s.content for s in r2.workspace.material_canvas.supplements)


# ---- 无斜杠自由文本（意图路由服务端化）----

def test_free_text_revise_verbs_queue_ai_revision():
    w = _wiring()
    ctx, ws = _workspace(w)
    r = _dialogue(w, ctx, "帮我润色一下这条要素的表达", targets=[ws.elements[0].id])
    assert r.outcome == "queued" and r.operation == "revise.ai"


def test_free_text_question_queues_review():
    w = _wiring()
    ctx, ws = _workspace(w)
    r = _dialogue(w, ctx, "这条要素有没有类型标错的问题？", targets=[ws.elements[0].id])
    assert r.outcome == "queued" and r.operation == "review"


def test_free_text_review_without_target_or_selection_rejected():
    w = _wiring()
    ctx, _ = _workspace(w)
    r = _dialogue(w, ctx, "整体看看有没有问题")
    assert r.outcome == "rejected_precheck" and r.message


# ---- 解释结论对象通道（clarify / cannot_comply / 白名单 / 失败）----

def test_cannot_comply_passthrough_no_dispatch():
    w = _wiring()
    ctx, ws = _workspace(w)
    w.service._command_interpreter = _FakeInterpreter(
        CommandInterpretation(status="cannot_comply", reason="要求编造原文没有的事实")
    )
    r = _dialogue(w, ctx, "/改表达 加上原文没有的规格", targets=[ws.elements[0].id])
    assert r.outcome == "cannot_comply" and "编造" in r.message
    assert w.service.read_element_workspace(ctx).workspace_version == ws.workspace_version


def test_operation_outside_command_whitelist_clarifies():
    w = _wiring()
    ctx, ws = _workspace(w)
    w.service._command_interpreter = _FakeInterpreter(
        CommandInterpretation(status="done", operation="erratum", params={"old_text": "a", "new_text": "b"})
    )
    r = _dialogue(w, ctx, "/改类型 功能需求", targets=[ws.elements[0].id])
    assert r.outcome == "clarify"


def test_interpreter_infrastructure_failure_never_dispatches():
    w = _wiring()
    ctx, ws = _workspace(w)
    w.service._command_interpreter = _FakeInterpreter(
        CommandInterpretation(status="clarify", failed=True)
    )
    r = _dialogue(w, ctx, "/改类型 功能需求", targets=[ws.elements[0].id])
    assert r.outcome == "rejected_precheck" and "暂不可用" in r.message
    assert w.service.read_element_workspace(ctx).workspace_version == ws.workspace_version


# ---- 链路回执条：on_stage 阶段事实（04A §2.1 增补）----

def test_on_stage_sequence_for_executed_command():
    w = _wiring()
    ctx, ws = _workspace(w)
    stages: list[str] = []
    ws2 = w.service.element_dialogue(ElementDialogueCommand(
        parse_context_ref=ctx, workspace_version=ws.workspace_version,
        message="/改类型 功能需求", target_element_refs=[ws.elements[0].id],
        operator_ref="U1", idempotency_key=uuid.uuid4().hex,
    ), on_stage=stages.append)
    assert ws2.outcome == "executed"
    assert stages == ["accepted", "interpreting", "dispatching"]


def test_on_stage_not_emitted_for_unknown_command():
    """未知命令确定性回执：不受理、不调模型 → 零阶段帧。"""
    w = _wiring()
    ctx, ws = _workspace(w)
    stages: list[str] = []
    r = w.service.element_dialogue(ElementDialogueCommand(
        parse_context_ref=ctx, workspace_version=ws.workspace_version,
        message="/不存在 x", operator_ref="U1", idempotency_key=uuid.uuid4().hex,
    ), on_stage=stages.append)
    assert r.outcome == "unknown_command"
    assert stages == []


def test_on_stage_stops_at_interpreting_for_clarify():
    w = _wiring()
    ctx, ws = _workspace(w)
    stages: list[str] = []
    r = w.service.element_dialogue(ElementDialogueCommand(
        parse_context_ref=ctx, workspace_version=ws.workspace_version,
        message="/改类型", target_element_refs=[ws.elements[0].id],
        operator_ref="U1", idempotency_key=uuid.uuid4().hex,
    ), on_stage=stages.append)
    assert r.outcome == "clarify"
    assert stages == ["accepted", "interpreting"]  # 未通过解释，不进入派发


# ---- 新增遗漏的类型参数（解释模型给出类型，不再恒为「目标」）----

def _fake_add_missing(w, params):
    w.service._command_interpreter = _FakeInterpreter(CommandInterpretation(
        status="done", operation="manual.add_missing", params=params,
    ))


def test_add_missing_uses_interpreted_element_type():
    """用户点名「应该属于接口需求」→ 草案按该类型创建。"""
    w = _wiring()
    ctx, _ = _workspace(w)
    _fake_add_missing(w, {
        "new_content": "系统应向支付网关提供退款回调接口。",
        "new_element_type": "interface_requirement",
    })
    r = _dialogue(w, ctx, "/新增遗漏 系统应向支付网关提供退款回调接口，这应该属于接口需求。")
    assert r.outcome == "executed" and r.operation == "manual.add_missing"
    created = r.workspace.change_draft.after_items
    assert [i.element_type.value for i in created] == ["interface_requirement"]


def test_add_missing_invalid_element_type_clarifies():
    """类型名不是稳定码 → 追问，不静默落成别的类型。"""
    w = _wiring()
    ctx, ws = _workspace(w)
    _fake_add_missing(w, {"new_content": "任意一条补登内容。", "new_element_type": "接口需求哦"})
    r = _dialogue(w, ctx, "/新增遗漏 任意一条补登内容，算接口需求哦。")
    assert r.outcome == "clarify"
    assert w.service.read_element_workspace(ctx).change_draft is None  # 未落草案


def test_add_missing_without_type_falls_back_to_goal():
    """解释结果无类型（模型也判断不出）→ 服务层兜底「目标」，行为不回归。"""
    w = _wiring()
    ctx, _ = _workspace(w)
    _fake_add_missing(w, {"new_content": "希望整体上更快。"})
    r = _dialogue(w, ctx, "/新增遗漏 希望整体上更快。")
    assert r.outcome == "executed"
    assert [i.element_type.value for i in r.workspace.change_draft.after_items] == ["goal"]
