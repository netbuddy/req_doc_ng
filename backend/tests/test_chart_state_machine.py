"""需求图表/追溯关系状态机契约 + 受控表达校验规则（SCN-004）。"""
import pytest

from app.domain.chart_rules import (
    FORMAT_TYPE_MATRIX,
    TYPE_KIND_MAP,
    preview_capability,
    validate_controlled_source,
)
from app.domain.enums import ChartFormat, ChartKind, ChartType
from app.domain.errors import RejectedTransition
from app.domain.state_machine import (
    CHART_TRANSITIONS,
    TRACE_TRANSITIONS,
    ChartEvent,
    ChartState,
    TraceEvent,
    TraceState,
    chart_listed_pairs,
    chart_transition,
    trace_listed_pairs,
    trace_transition,
)


# ---- 需求图表状态机（LDM-012）----


def test_chart_listed_pairs_count():
    assert len(chart_listed_pairs()) == len(CHART_TRANSITIONS) == 9


@pytest.mark.parametrize("current,event,expected", [
    (ChartState.INITIAL, ChartEvent.CREATE, ChartState.DRAFT),
    (ChartState.DRAFT, ChartEvent.APPLY_SOURCE_CHANGE, ChartState.DRAFT),
    (ChartState.DRAFT, ChartEvent.START_VERIFICATION, ChartState.PENDING_CONFIRMATION),
    (ChartState.PENDING_CONFIRMATION, ChartEvent.REQUEST_REVERIFICATION, ChartState.PENDING_CONFIRMATION),
    (ChartState.PENDING_CONFIRMATION, ChartEvent.CONFIRM, ChartState.CONFIRMED),
    (ChartState.PENDING_CONFIRMATION, ChartEvent.RETURN_FOR_REVISION, ChartState.RETURNED_FOR_REVISION),
    (ChartState.PENDING_CONFIRMATION, ChartEvent.VOID, ChartState.VOIDED),
    (ChartState.RETURNED_FOR_REVISION, ChartEvent.RESUME_EDITING, ChartState.DRAFT),
    (ChartState.RETURNED_FOR_REVISION, ChartEvent.VOID, ChartState.VOIDED),
])
def test_chart_transitions(current, event, expected):
    assert chart_transition(current, event) is expected


@pytest.mark.parametrize("current,event", [
    # 待确认冻结源码编辑（SCN-004 §5.4 N01）
    (ChartState.PENDING_CONFIRMATION, ChartEvent.APPLY_SOURCE_CHANGE),
    # 已确认/作废是终态
    (ChartState.CONFIRMED, ChartEvent.APPLY_SOURCE_CHANGE),
    (ChartState.CONFIRMED, ChartEvent.CONFIRM),
    (ChartState.CONFIRMED, ChartEvent.START_VERIFICATION),
    (ChartState.VOIDED, ChartEvent.RESUME_EDITING),
    (ChartState.VOIDED, ChartEvent.START_VERIFICATION),
    # 草稿不能直接确认或作废（须先进入核对）
    (ChartState.DRAFT, ChartEvent.CONFIRM),
    (ChartState.DRAFT, ChartEvent.REQUEST_REVERIFICATION),
    # 退回修订不能直接确认（须 resume 后重走核对）
    (ChartState.RETURNED_FOR_REVISION, ChartEvent.CONFIRM),
    (ChartState.RETURNED_FOR_REVISION, ChartEvent.START_VERIFICATION),
])
def test_chart_default_reject(current, event):
    with pytest.raises(RejectedTransition):
        chart_transition(current, event)


def test_chart_default_reject_set_complete():
    listed = chart_listed_pairs()
    rejected = {(s, e) for s in ChartState for e in ChartEvent if (s, e) not in listed}
    assert len(rejected) == len(ChartState) * len(ChartEvent) - len(listed)
    for s, e in rejected:
        with pytest.raises(RejectedTransition):
            chart_transition(s, e)


# ---- 追溯关系状态机（LDM-013）----


def test_trace_listed_pairs_count():
    assert len(trace_listed_pairs()) == len(TRACE_TRANSITIONS) == 8


@pytest.mark.parametrize("current,event,expected", [
    (TraceState.INITIAL, TraceEvent.PRE_ESTABLISH, TraceState.PRE_ESTABLISHED),
    (TraceState.PRE_ESTABLISHED, TraceEvent.SYNC, TraceState.PRE_ESTABLISHED),
    (TraceState.PRE_ESTABLISHED, TraceEvent.MARK_SUSPECT, TraceState.SUSPECT_PENDING_REVIEW),
    (TraceState.SUSPECT_PENDING_REVIEW, TraceEvent.SYNC, TraceState.PRE_ESTABLISHED),
    (TraceState.PRE_ESTABLISHED, TraceEvent.ESTABLISH, TraceState.EFFECTIVE),
    (TraceState.PRE_ESTABLISHED, TraceEvent.INVALIDATE, TraceState.INVALID),
    (TraceState.SUSPECT_PENDING_REVIEW, TraceEvent.INVALIDATE, TraceState.INVALID),
    (TraceState.EFFECTIVE, TraceEvent.MARK_SUSPECT, TraceState.SUSPECT_PENDING_REVIEW),
])
def test_trace_transitions(current, event, expected):
    assert trace_transition(current, event) is expected


@pytest.mark.parametrize("current,event", [
    # 可疑待复核不得直接确立（须先重新同步回预建立）
    (TraceState.SUSPECT_PENDING_REVIEW, TraceEvent.ESTABLISH),
    # 失效是终态
    (TraceState.INVALID, TraceEvent.ESTABLISH),
    (TraceState.INVALID, TraceEvent.SYNC),
    (TraceState.INVALID, TraceEvent.PRE_ESTABLISH),
    # 有效关系不重复确立、不回退预建立
    (TraceState.EFFECTIVE, TraceEvent.ESTABLISH),
    (TraceState.EFFECTIVE, TraceEvent.SYNC),
])
def test_trace_default_reject(current, event):
    with pytest.raises(RejectedTransition):
        trace_transition(current, event)


# ---- 受控表达校验规则（chart_rules）----


def test_format_type_matrix_covers_all_types():
    covered = set().union(*FORMAT_TYPE_MATRIX.values())
    assert covered == set(ChartType)
    assert set(TYPE_KIND_MAP) == set(ChartType)
    assert TYPE_KIND_MAP[ChartType.DECISION_TABLE] is ChartKind.TABLE


def test_preview_capability_by_format():
    assert preview_capability(ChartFormat.MERMAID) == "renderable"
    assert preview_capability(ChartFormat.MARKDOWN_TABLE) == "renderable"
    assert preview_capability(ChartFormat.PLANTUML) == "not_previewable"


@pytest.mark.parametrize("format_,chart_type,source,ok", [
    # mermaid 正例
    (ChartFormat.MERMAID, ChartType.FLOWCHART, "flowchart TD\n  A --> B", True),
    (ChartFormat.MERMAID, ChartType.STATE_DIAGRAM, "stateDiagram-v2\n  [*] --> S1", True),
    (ChartFormat.MERMAID, ChartType.SEQUENCE_DIAGRAM, "sequenceDiagram\n  A->>B: hi", True),
    (ChartFormat.MERMAID, ChartType.RELATION_DIAGRAM, "erDiagram\n  A ||--o{ B : has", True),
    # mermaid 反例：头与类型不匹配 / 空 / 只有声明行
    (ChartFormat.MERMAID, ChartType.FLOWCHART, "sequenceDiagram\n  A->>B: hi", False),
    (ChartFormat.MERMAID, ChartType.FLOWCHART, "", False),
    (ChartFormat.MERMAID, ChartType.FLOWCHART, "flowchart TD", False),
    # plantuml
    (ChartFormat.PLANTUML, ChartType.SEQUENCE_DIAGRAM, "@startuml\nA -> B: hi\n@enduml", True),
    (ChartFormat.PLANTUML, ChartType.SEQUENCE_DIAGRAM, "A -> B: hi", False),
    (ChartFormat.PLANTUML, ChartType.SEQUENCE_DIAGRAM, "@startuml\n@enduml", False),
    # markdown 表格
    (ChartFormat.MARKDOWN_TABLE, ChartType.DECISION_TABLE, "| 条件 | 动作 |\n|---|---|\n| A | B |", True),
    (ChartFormat.MARKDOWN_TABLE, ChartType.DECISION_TABLE, "| 条件 | 动作 |\n| A | B |", False),
    (ChartFormat.MARKDOWN_TABLE, ChartType.DECISION_TABLE, "| 条件 | 动作 |\n|---|---|\n| A |", False),
    # 表达方式 × 类型不匹配
    (ChartFormat.MARKDOWN_TABLE, ChartType.FLOWCHART, "| a |\n|---|\n| b |", False),
    (ChartFormat.MERMAID, ChartType.DECISION_TABLE, "flowchart TD\n  A --> B", False),
])
def test_validate_controlled_source(format_, chart_type, source, ok):
    errors = validate_controlled_source(format_, chart_type, source)
    assert (errors == []) is ok
