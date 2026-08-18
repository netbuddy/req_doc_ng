"""材料接入状态机（LDM-003）—— 迁移表是事实源。

绑定 docs/40 domains/DS-001/state-machines/材料接入.md：
每行 = 一条迁移；未列出的 (状态, 事件) 组合默认拒绝。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class IntakeState(str, Enum):
    INITIAL = "initial"
    INTAKE_REQUEST_CONTEXT = "intake_request_context"
    ACCEPTED = "accepted"
    RETURNED_FOR_SUPPLEMENT = "returned_for_supplement"
    EXCLUDED = "excluded"


class IntakeEvent(str, Enum):
    SUBMIT = "AEP-001"          # 文本接入命令 / 补充后重提
    ACCEPT_RESULT = "AEP-002"   # 模型判断结果交接


@dataclass(frozen=True)
class Transition:
    current: IntakeState
    event: IntakeEvent
    guard: str
    nxt: IntakeState


TRANSITIONS: tuple[Transition, ...] = (
    Transition(IntakeState.INITIAL, IntakeEvent.SUBMIT,
               "已选定项目 ∧ 非空文本", IntakeState.INTAKE_REQUEST_CONTEXT),
    Transition(IntakeState.INTAKE_REQUEST_CONTEXT, IntakeEvent.ACCEPT_RESULT,
               "可接入 ∧ 服务确认接收", IntakeState.ACCEPTED),
    Transition(IntakeState.INTAKE_REQUEST_CONTEXT, IntakeEvent.ACCEPT_RESULT,
               "内容不足 ∨ 归属不明", IntakeState.RETURNED_FOR_SUPPLEMENT),
    Transition(IntakeState.INTAKE_REQUEST_CONTEXT, IntakeEvent.ACCEPT_RESULT,
               "无需求资产价值", IntakeState.EXCLUDED),
    Transition(IntakeState.RETURNED_FOR_SUPPLEMENT, IntakeEvent.SUBMIT,
               "用户补充重提", IntakeState.INTAKE_REQUEST_CONTEXT),
)

TERMINAL_STATES: tuple[IntakeState, ...] = (IntakeState.ACCEPTED, IntakeState.EXCLUDED)


def listed_pairs() -> set[tuple[IntakeState, IntakeEvent]]:
    return {(t.current, t.event) for t in TRANSITIONS}


def default_reject_pairs() -> set[tuple[IntakeState, IntakeEvent]]:
    listed = listed_pairs()
    return {(s, e) for s in IntakeState for e in IntakeEvent if (s, e) not in listed}


# ---- 材料解析状态机（LDM-004）—— 迁移表是事实源 ----
# 绑定 docs/40 domains/DS-001/state-machines/材料解析.md：
# 每行 = 一条迁移；未列出的 (状态, 事件) 组合默认拒绝。
# 建模说明：『解析中』由识别请求上下文承载（LDM-004 尚未创建），承接成功时 LDM-004 直接创建于终态。


class ParseState(str, Enum):
    INITIAL = "initial"                # 已接入材料，未发起识别
    PARSING = "parsing"                # 识别请求上下文（送检中）
    PARSED = "parsed"                  # 已解析（成功 ∧ 存在要素）
    UNPROCESSABLE = "unprocessable"    # 不可继续处理（成功 ∧ 无可处理要素）


class ParseEvent(str, Enum):
    START_RECOGNITION = "AEP-021"  # 需求要素识别启动命令
    ACCEPT_RESULT = "AEP-022"      # 识别结果承接（模型编排回交）


@dataclass(frozen=True)
class ParseTransition:
    current: ParseState
    event: ParseEvent
    guard: str
    nxt: ParseState


PARSE_TRANSITIONS: tuple[ParseTransition, ...] = (
    ParseTransition(ParseState.INITIAL, ParseEvent.START_RECOGNITION,
                    "LDM-003.intake_conclusion=已接入", ParseState.PARSING),
    ParseTransition(ParseState.PARSING, ParseEvent.ACCEPT_RESULT,
                    "识别成功 ∧ 存在需求要素", ParseState.PARSED),
    ParseTransition(ParseState.PARSING, ParseEvent.ACCEPT_RESULT,
                    "识别成功 ∧ 无可处理要素", ParseState.UNPROCESSABLE),
)

PARSE_TERMINAL_STATES: tuple[ParseState, ...] = (ParseState.PARSED, ParseState.UNPROCESSABLE)


def parse_listed_pairs() -> set[tuple[ParseState, ParseEvent]]:
    return {(t.current, t.event) for t in PARSE_TRANSITIONS}


def parse_default_reject_pairs() -> set[tuple[ParseState, ParseEvent]]:
    listed = parse_listed_pairs()
    return {(s, e) for s in ParseState for e in ParseEvent if (s, e) not in listed}


# ---- 需求要素状态机（LDM-005 人工确认生命周期）—— 迁移表是事实源 ----
# 绑定 docs/40 domains/DS-001/state-machines/需求要素.md（2026-07-05 收敛为 3 态 + 重开）：
# 每行 = 一条迁移；未列出的 (状态, 事件[, 守卫]) 组合默认拒绝。
# AI 复核与修订迭代是工作区会话事实（复核结论 / 未采纳修订稿），不进入生命周期状态。

from app.domain.enums import ElementProcessStatus as ES  # noqa: E402
from app.domain.errors import RejectedTransition  # noqa: E402


class ElementEvent(str, Enum):
    CONFIRM = "confirm"                # 人工确认（以当前版本表达）
    REJECT = "reject"                  # 人工拒绝（撤销，保留识别事实）
    ADOPT_REVISION = "adopt_revision"  # 采纳修订稿（采纳即确认；守卫：超出原文须先补入）
    REOPEN = "reopen"                  # 重开（已撤销）/ 回流（已确认），产生新版本


@dataclass(frozen=True)
class ElementTransition:
    current: ES
    event: ElementEvent
    guard: str  # 谓词描述
    nxt: ES


ELEMENT_TRANSITIONS: tuple[ElementTransition, ...] = (
    # 待确认（唯一非终态；对话协同期间状态不变）
    ElementTransition(ES.PENDING_CONFIRMATION, ElementEvent.CONFIRM, "以当前版本表达确认", ES.CONFIRMED),
    ElementTransition(ES.PENDING_CONFIRMATION, ElementEvent.ADOPT_REVISION,
                      "存在未采纳修订稿；超出原文事实须先补入", ES.CONFIRMED),
    ElementTransition(ES.PENDING_CONFIRMATION, ElementEvent.REJECT, "—", ES.REVOKED),
    # 重开 / 回流（产生新版本，回到待确认）
    ElementTransition(ES.CONFIRMED, ElementEvent.REOPEN, "下游回流/重新处置（新版本）", ES.PENDING_CONFIRMATION),
    ElementTransition(ES.REVOKED, ElementEvent.REOPEN, "误撤销重开（新版本）", ES.PENDING_CONFIRMATION),
)

ELEMENT_TERMINAL_STATES: tuple[ES, ...] = (ES.CONFIRMED, ES.REVOKED)


def element_listed_pairs() -> set[tuple[ES, ElementEvent]]:
    return {(t.current, t.event) for t in ELEMENT_TRANSITIONS}


def element_transition(current: ES, event: ElementEvent) -> ES:
    """事件迁移；未列出的 (状态,事件) 默认拒绝。业务守卫（修订稿存在、超出原文检查）在服务层执行。"""
    for t in ELEMENT_TRANSITIONS:
        if t.current == current and t.event == event:
            return t.nxt
    raise RejectedTransition(f"默认拒绝：状态 {current.value} 不接受事件 {event.value}")


# ---- 需求条目状态机（LDM-007，SCN-002-P01 + SCN-003 确认切片）—— 迁移表是事实源 ----
# 绑定 docs/40 domains/DS-001/state-machines/需求条目.md：
# AEP-038 创建待确认 + AEP-036 待确认字段修订自环 + AEP-037 确认状态写入（SCN-003-P04）；
# 被替代/已终止（替代关系、退回、拒绝、转问题项）归 SCN-003 后续迭代，未列出的 (状态,事件) 默认拒绝。

from app.domain.enums import RequirementItemStatus as IS  # noqa: E402


class ItemEvent(str, Enum):
    FORM = "AEP-038"    # 条目化批次（条目形成服务）→ 创建待确认
    REVISE = "AEP-036"  # 应用修订（需求条目服务）→ 待确认自环（旧诊断轮次失效+链式增量诊断）
    CONFIRM = "AEP-037"  # 确认写入（采纳「建议通过」内联 / 覆盖确认直写，理由必填）
    TERMINATE = "terminate"  # 终止（采纳「建议撤回」 / 人工撤回，理由必填）


class ItemState(str, Enum):
    INITIAL = "initial"
    PENDING_CONFIRMATION = IS.PENDING_CONFIRMATION.value
    CONFIRMED = IS.CONFIRMED.value
    SUPERSEDED = IS.SUPERSEDED.value
    TERMINATED = IS.TERMINATED.value


@dataclass(frozen=True)
class ItemTransition:
    current: ItemState
    event: ItemEvent
    guard: str
    nxt: ItemState


ITEM_TRANSITIONS: tuple[ItemTransition, ...] = (
    ItemTransition(ItemState.INITIAL, ItemEvent.FORM,
                   "来源 LDM-005 已确认 ∧ element_type 属需求表达类", ItemState.PENDING_CONFIRMATION),
    ItemTransition(ItemState.PENDING_CONFIRMATION, ItemEvent.REVISE,
                   "条目仍为待确认 ∧ 版本一致 ∧ 字段可修订", ItemState.PENDING_CONFIRMATION),
    ItemTransition(ItemState.PENDING_CONFIRMATION, ItemEvent.CONFIRM,
                   "采纳「建议通过」：结论有效 ∧ 版本一致（准入内联）；或覆盖确认：理由必填 ∧ 无在途运行",
                   ItemState.CONFIRMED),
    ItemTransition(ItemState.PENDING_CONFIRMATION, ItemEvent.TERMINATE,
                   "采纳「建议撤回」（理由=结论依据）；或人工撤回（理由必填）",
                   ItemState.TERMINATED),
)


def item_listed_pairs() -> set[tuple[ItemState, ItemEvent]]:
    return {(t.current, t.event) for t in ITEM_TRANSITIONS}


def item_transition(current: ItemState, event: ItemEvent) -> ItemState:
    """需求条目迁移；未列出的 (状态,事件) 默认拒绝（如对非待确认条目字段修订）。"""
    for t in ITEM_TRANSITIONS:
        if t.current == current and t.event == event:
            return t.nxt
    raise RejectedTransition(f"默认拒绝：需求条目状态 {current.value} 不接受事件 {event.value}")

# ---- 需求文档状态机（LDM-014，SCN-005）—— 迁移表是事实源 ----
# 绑定 docs/30 …/SCN-005 §4.5/§5.5/§6.5 分支结果矩阵：
# 每行 = 一条迁移；未列出的 (状态, 事件) 组合默认拒绝。
# 建模说明：索引/Markdown/导出件/基线是 LDM-014 内四个不同状态对象；
# 本表承载文档级主线，子对象状态见各自 status 列。

from app.domain.enums import DocumentStatus as DS  # noqa: E402


class DocEvent(str, Enum):
    SAVE_INDEX = "save-index"            # P01 索引保存（含准入校验）
    GENERATE_MARKDOWN = "generate-md"    # P02 生成/重新生成中间稿
    FINALIZE_MARKDOWN = "finalize-md"    # P02 定稿确认
    REOPEN_INDEX = "reopen-index"        # 回 P01 调整索引编排
    REOPEN_MARKDOWN = "reopen-md"        # 定稿后回窗口（作废定稿）
    CONFIRM_BASELINE = "confirm-baseline"  # P03 发布基线确认


class DocState(str, Enum):
    INDEX_DRAFT = DS.INDEX_DRAFT.value
    INDEX_BLOCKED = DS.INDEX_BLOCKED.value
    INDEX_READY = DS.INDEX_READY.value
    MARKDOWN_DRAFT = DS.MARKDOWN_DRAFT.value
    MARKDOWN_FINALIZED = DS.MARKDOWN_FINALIZED.value
    BASELINE_PUBLISHED = DS.BASELINE_PUBLISHED.value


@dataclass(frozen=True)
class DocTransition:
    current: DocState
    event: DocEvent
    guard: str
    nxt: DocState


DOC_TRANSITIONS: tuple[DocTransition, ...] = (
    # P01：保存索引 → 就绪 / 受阻（模板 schema 通过 ∧ 资产均确认态 ∧ 必填覆盖 → 就绪）
    DocTransition(DocState.INDEX_DRAFT, DocEvent.SAVE_INDEX, "准入通过", DocState.INDEX_READY),
    DocTransition(DocState.INDEX_DRAFT, DocEvent.SAVE_INDEX, "必填缺失 ∨ 模板问题", DocState.INDEX_BLOCKED),
    DocTransition(DocState.INDEX_BLOCKED, DocEvent.SAVE_INDEX, "补建后重排 ∧ 准入通过", DocState.INDEX_READY),
    DocTransition(DocState.INDEX_BLOCKED, DocEvent.SAVE_INDEX, "仍不满足", DocState.INDEX_BLOCKED),
    DocTransition(DocState.INDEX_READY, DocEvent.SAVE_INDEX, "增量调整 ∧ 准入通过", DocState.INDEX_READY),
    DocTransition(DocState.INDEX_READY, DocEvent.SAVE_INDEX, "调整后不满足", DocState.INDEX_BLOCKED),
    # P02：生成/微调/定稿
    DocTransition(DocState.INDEX_READY, DocEvent.GENERATE_MARKDOWN, "索引就绪", DocState.MARKDOWN_DRAFT),
    DocTransition(DocState.MARKDOWN_DRAFT, DocEvent.GENERATE_MARKDOWN, "重新生成（新版本）", DocState.MARKDOWN_DRAFT),
    DocTransition(DocState.MARKDOWN_DRAFT, DocEvent.FINALIZE_MARKDOWN,
                  "无不可定稿项 ∧ 无未收束确认态条目修订 ∧ 用户确认", DocState.MARKDOWN_FINALIZED),
    DocTransition(DocState.MARKDOWN_DRAFT, DocEvent.REOPEN_INDEX, "调整章节映射/内容选择/排序", DocState.INDEX_READY),
    DocTransition(DocState.MARKDOWN_FINALIZED, DocEvent.REOPEN_MARKDOWN, "定稿后继续微调（作废定稿）", DocState.MARKDOWN_DRAFT),
    DocTransition(DocState.MARKDOWN_FINALIZED, DocEvent.REOPEN_INDEX, "定稿后调整索引", DocState.INDEX_READY),
    DocTransition(DocState.MARKDOWN_FINALIZED, DocEvent.GENERATE_MARKDOWN, "定稿后重新生成（作废定稿）", DocState.MARKDOWN_DRAFT),
    # P03：基线确认（导出成功≠发布；确认后进入只读复核）
    DocTransition(DocState.MARKDOWN_FINALIZED, DocEvent.CONFIRM_BASELINE,
                  "候选导出件有效 ∧ 用户显式确认", DocState.BASELINE_PUBLISHED),
    # 基线形成后再改动 → 必须走新一轮 P01/P02（基线本身只读）
    DocTransition(DocState.BASELINE_PUBLISHED, DocEvent.REOPEN_INDEX, "新一轮编排", DocState.INDEX_READY),
    DocTransition(DocState.BASELINE_PUBLISHED, DocEvent.REOPEN_MARKDOWN, "新一轮微调", DocState.MARKDOWN_DRAFT),
)


def doc_listed_pairs() -> set[tuple[DocState, DocEvent]]:
    return {(t.current, t.event) for t in DOC_TRANSITIONS}


def doc_transition(current: DocState, event: DocEvent, ready: bool = True) -> DocState:
    """需求文档迁移；同 (状态,事件) 多行时以 ready 选择结果行；未列出默认拒绝。

    ready=True 取该 (状态,事件) 的第一行（准入通过/成功）；False 取第二行（受阻）。
    """
    matched = [t for t in DOC_TRANSITIONS if t.current == current and t.event == event]
    if not matched:
        raise RejectedTransition(f"默认拒绝：需求文档状态 {current.value} 不接受事件 {event.value}")
    if len(matched) == 1:
        return matched[0].nxt
    return matched[0].nxt if ready else matched[1].nxt


# ---- 需求图表状态机（LDM-012，SCN-004）—— 迁移表是事实源 ----
# 绑定 docs/30 …/SCN-004 §4.5/§5.5 分支结果矩阵与 枚举字典.md LDM-012.status：
# 每行 = 一条迁移；未列出的 (状态, 事件) 组合默认拒绝。
# 建模说明：待确认即冻结源码编辑（apply-source-change 仅 draft 接受）；
# 原 P01-N13 可核对裁定并入 start-verification（SCN-004 §4.4 N12 备注）。

from app.domain.enums import ChartStatus as CS  # noqa: E402


class ChartEvent(str, Enum):
    CREATE = "create-chart"                      # P01-N04 草稿壳（来源准入通过后）
    APPLY_SOURCE_CHANGE = "apply-source-change"  # P01-N10 源码变更应用（草稿自环）
    START_VERIFICATION = "start-verification"    # P02-N01 核对发起（草稿→待确认，冻结编辑）
    REQUEST_REVERIFICATION = "request-reverification"  # P02 重新核对（待确认自环）
    CONFIRM = "confirm-chart"                    # P02-N08 图表正式确认
    RETURN_FOR_REVISION = "return-for-revision"  # P02-N07 退回修订
    RESUME_EDITING = "resume-editing"            # 退回修订→草稿（重回 P01 循环）
    VOID = "void-chart"                          # P02-N07 作废


class ChartState(str, Enum):
    INITIAL = "initial"
    DRAFT = CS.DRAFT.value
    PENDING_CONFIRMATION = CS.PENDING_CONFIRMATION.value
    CONFIRMED = CS.CONFIRMED.value
    RETURNED_FOR_REVISION = CS.RETURNED_FOR_REVISION.value
    VOIDED = CS.VOIDED.value


@dataclass(frozen=True)
class ChartTransition:
    current: ChartState
    event: ChartEvent
    guard: str
    nxt: ChartState


CHART_TRANSITIONS: tuple[ChartTransition, ...] = (
    ChartTransition(ChartState.INITIAL, ChartEvent.CREATE,
                    "来源准入通过（确认态来源）∧ 主题/类型/表达方式已承接", ChartState.DRAFT),
    ChartTransition(ChartState.DRAFT, ChartEvent.APPLY_SOURCE_CHANGE,
                    "受控格式 ∧ 图表类型匹配 ∧ 来源引用成立 ∧ 版本一致", ChartState.DRAFT),
    ChartTransition(ChartState.DRAFT, ChartEvent.START_VERIFICATION,
                    "受控表达成立 ∧ 来源对象存在 ∧ 预建立追溯成立", ChartState.PENDING_CONFIRMATION),
    ChartTransition(ChartState.PENDING_CONFIRMATION, ChartEvent.REQUEST_REVERIFICATION,
                    "上一轮已收束或失败", ChartState.PENDING_CONFIRMATION),
    ChartTransition(ChartState.PENDING_CONFIRMATION, ChartEvent.CONFIRM,
                    "有效图文核对 LDM-015 ∧ 复核收束 ∧ 无被接受的阻断发现项 ∧ 追溯预建立仍成立",
                    ChartState.CONFIRMED),
    ChartTransition(ChartState.PENDING_CONFIRMATION, ChartEvent.RETURN_FOR_REVISION,
                    "用户处置=退回修订 ∨ 确认准入不通过处置", ChartState.RETURNED_FOR_REVISION),
    ChartTransition(ChartState.PENDING_CONFIRMATION, ChartEvent.VOID,
                    "用户处置=作废", ChartState.VOIDED),
    ChartTransition(ChartState.RETURNED_FOR_REVISION, ChartEvent.RESUME_EDITING,
                    "重回源码编辑循环", ChartState.DRAFT),
    ChartTransition(ChartState.RETURNED_FOR_REVISION, ChartEvent.VOID,
                    "放弃本图表", ChartState.VOIDED),
)

CHART_TERMINAL_STATES: tuple[ChartState, ...] = (ChartState.CONFIRMED, ChartState.VOIDED)


def chart_listed_pairs() -> set[tuple[ChartState, ChartEvent]]:
    return {(t.current, t.event) for t in CHART_TRANSITIONS}


def chart_transition(current: ChartState, event: ChartEvent) -> ChartState:
    """需求图表迁移；未列出的 (状态,事件) 默认拒绝（如待确认状态下编辑源码）。"""
    for t in CHART_TRANSITIONS:
        if t.current == current and t.event == event:
            return t.nxt
    raise RejectedTransition(f"默认拒绝：需求图表状态 {current.value} 不接受事件 {event.value}")


# ---- 追溯关系状态机（LDM-013，SCN-004）—— 迁移表是事实源 ----
# 绑定 枚举字典.md LDM-013.status 与 SCN-004 §4.4 N05/N11、§5.4 N09：
# 预建立不得作为正式追溯依据；有效只能随图表确认同批确立；
# effective→suspect 留给上游变更触发复核（SCN-006 承接），本迭代不触发。

from app.domain.enums import TraceLinkStatus as TS  # noqa: E402


class TraceEvent(str, Enum):
    PRE_ESTABLISH = "pre-establish"   # P01-N05/N11 自动预建立
    SYNC = "sync-trace"               # P01-N11 覆盖对象同步（自环/待补全恢复）
    MARK_SUSPECT = "mark-suspect"     # 来源不足 / 图表退回修订 → 可疑待复核
    ESTABLISH = "establish"           # P02-N09 预建立→有效（仅随图表确认同批）
    INVALIDATE = "invalidate"         # 图表作废 / 来源引用移除 → 失效


class TraceState(str, Enum):
    INITIAL = "initial"
    PRE_ESTABLISHED = TS.PRE_ESTABLISHED.value
    EFFECTIVE = TS.EFFECTIVE.value
    SUSPECT_PENDING_REVIEW = TS.SUSPECT_PENDING_REVIEW.value
    INVALID = TS.INVALID.value


@dataclass(frozen=True)
class TraceTransition:
    current: TraceState
    event: TraceEvent
    guard: str
    nxt: TraceState


TRACE_TRANSITIONS: tuple[TraceTransition, ...] = (
    TraceTransition(TraceState.INITIAL, TraceEvent.PRE_ESTABLISH,
                    "上下游对象 ∧ 关系类型 ∧ 初始依据已确定", TraceState.PRE_ESTABLISHED),
    TraceTransition(TraceState.PRE_ESTABLISHED, TraceEvent.SYNC,
                    "覆盖对象仍成立（自环）", TraceState.PRE_ESTABLISHED),
    TraceTransition(TraceState.PRE_ESTABLISHED, TraceEvent.MARK_SUSPECT,
                    "来源不足 ∨ 图表退回修订", TraceState.SUSPECT_PENDING_REVIEW),
    TraceTransition(TraceState.SUSPECT_PENDING_REVIEW, TraceEvent.SYNC,
                    "重新同步成功", TraceState.PRE_ESTABLISHED),
    TraceTransition(TraceState.PRE_ESTABLISHED, TraceEvent.ESTABLISH,
                    "图表确认同批提交 ∧ 无缺口/问题项阻断", TraceState.EFFECTIVE),
    TraceTransition(TraceState.PRE_ESTABLISHED, TraceEvent.INVALIDATE,
                    "图表作废 ∨ 来源引用移除", TraceState.INVALID),
    TraceTransition(TraceState.SUSPECT_PENDING_REVIEW, TraceEvent.INVALIDATE,
                    "图表作废", TraceState.INVALID),
    TraceTransition(TraceState.EFFECTIVE, TraceEvent.MARK_SUSPECT,
                    "上游对象变更触发复核（SCN-006 承接）", TraceState.SUSPECT_PENDING_REVIEW),
)

TRACE_TERMINAL_STATES: tuple[TraceState, ...] = (TraceState.INVALID,)


def trace_listed_pairs() -> set[tuple[TraceState, TraceEvent]]:
    return {(t.current, t.event) for t in TRACE_TRANSITIONS}


def trace_transition(current: TraceState, event: TraceEvent) -> TraceState:
    """追溯关系迁移；未列出的 (状态,事件) 默认拒绝（如对失效关系再确立）。"""
    for t in TRACE_TRANSITIONS:
        if t.current == current and t.event == event:
            return t.nxt
    raise RejectedTransition(f"默认拒绝：追溯关系状态 {current.value} 不接受事件 {event.value}")
