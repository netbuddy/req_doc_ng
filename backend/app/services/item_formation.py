"""条目形成服务（AEP-038）+ 需求条目服务（AEP-036 最小面）—— SCN-002-P01。

设计事实源：docs/40 domains/DS-001/interfaces/条目形成服务.md、需求条目服务.md、
state-machines/需求条目.md（迁移表是事实源）、slices/SCN-002-P01/约束与验收.md。
- 只创建/修订处于待确认状态的 LDM-007；确认、终止、替代与版本演进归 SCN-003。
- VAL-002：条目格式化建议先落 LDM-015（模型编排登记）；本服务裁定后才写 LDM-007。
- VAL-003：待确认创建写权威 = 条目形成服务；字段修订写权威 = 需求条目服务；均经需求条目仓储。
- 逐要素归因：物理批量送检，但输出、裁定、停靠与创建按单个 LDM-005 记结果。
- AEP-038 不承接字段修订；AEP-008/AEP-017 保持停用。
业务结局用返回值；默认拒绝/版本冲突用 RejectedTransition。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional, Protocol

from app.adapters.llm import (
    FormationCommandInterpreter,
    ItemDraftComposer,
    ItemExplainer,
)
from app.api.schemas import (
    ActionFact,
    BlockedElementRead,
    ElementFacetFindingRead,
    FormationDialogueCommand,
    FormationDialogueResult,
    ItemFormationWorkspaceRead,
    ItemizationBatchCommand,
    ItemizationBatchRequestResult,
    ItemizationResultRead,
    ItemRevisionCommand,
    ItemRevisionRecordRead,
    ItemRevisionResult,
    ItemRevisionSuggestionRead,
    ItemStructureReviewRead,
    MaterialCanvasRead,
    MaterialSupplementRead,
    MaterialTextBlockRead,
    PendingRequirementItemRead,
    RequirementConventionCatalogRead,
    RequirementConventionRead,
    ConventionExampleRead,
    ConventionPatternRead,
    RequirementElementRead,
    StructureRecheckCommand,
    StructureRecheckOutcomeRead,
    StructureRecheckRequestResult,
)
from app.db.models import RECHECK_IDEMPOTENCY_PAYLOAD_KEY
from app.domain.anchors import anchor_quotes, first_anchor_quote, split_blocks
from app.domain.chat_commands import FORMATION_COMMANDS, UnknownCommand, resolve_command
from app.domain.enums import (
    ELEMENT_TO_ITEM_TYPE as _ELEMENT_TO_ITEM_TYPE,
    AiRequestStage,
    ElementProcessStatus as ES,
    ElementType,
    KnowledgeCategory,
    knowledge_category_of,
    ItemizationResultStatus as IR,
    ItemizationScopeType,
    ItemPriority,
    ItemRevisionMode,
    RequirementItemStatus as IS,
    RequirementItemType,
    VerificationMethod,
)
from app.domain.errors import InvalidInput, NotFound, RejectedTransition
from app.domain.naming import normalize_element_name, normalize_text
from app.domain.item_profiles import (
    DEFAULT_CONVENTION,
    convention_catalog,
    convention_display_name,
    get_profile,
)
from app.domain.labels import (
    ELEMENT_TYPE_LABELS,
    ITEM_REVISION_FIELD_LABELS,
    NON_REVISION_FIELD_KEYS,
    REQUIREMENT_ITEM_TYPE_LABELS,
)
from app.domain.state_machine import ItemEvent, ItemState, item_transition
from app.interfaces import (
    ElementRow,
    ItemFormationProcessRepository,
    ItemReviewRepository,
    ItemRow,
    ModelOrchestration,
    ModelResultRepository,
    ProcessRecordRepository,
    RequirementItemRepository,
    SourceAssetRepository,
)
from app.events import DomainEventPublisher, ItemRevised
from app.interfaces.repositories import ItemRevisionRow, ItemStructureProjectionRow
from app.log import log_event
from app.services.run_liveness import is_run_alive

_COMPONENT = "item-formation"

# 本服务批次 lane 的 rq task 名（HK-1 判活阈值经 job_timeout_for(lane) 取值的键）
_FORMATION_LANE = "run_item_formation"
# 结构复核 lane 的 rq task 名与 LDM-015 stage（AEP-114；只判不改，结果只刷新达标投影）
_RECHECK_LANE = "run_item_structure_recheck"
_STAGE_RECHECK = "item_structure_recheck"

# 可修订字段白名单（单一来源 labels.ITEM_REVISION_FIELD_GUIDE；含 29148 属性补齐三字段）
_REVISABLE_FIELDS = tuple(ITEM_REVISION_FIELD_LABELS)

# AEP-097 区5 对话（与评审页 AEP-095 同口径：自由文本确定性路由，不调解释模型）
_DRAFT_MARKS = ("修订为", "改成", "改为", "写进", "加上", "补上", "表达修订", "起草")
_STAGE_DRAFT = "item_revision_draft"  # 与评审页同 stage：建议卡同源投影
_STAGE_EXPLAIN = "item_formation_explanation"

_FORMATION_OPERATION_LABELS = {
    "start_itemization": "生成待确认条目",
    "revise.req_type": "修订条目类型",
    "revise.field": "字段修订",
    "draft.field": "AI 起草修订建议",
    "draft.normalize": "规范化建议",
    "split.manual": "条目拆分",
    "merge.manual": "条目归并",
    "explain.source": "来源指认",
    "reference.supporting_basis": "引用业务知识依据",
    "structure.recheck": "条目结构复核",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

# 属性字段：修订留痕但不视为条目内容变更——不推进投影版本锚、不失效诊断轮次、
# 不触发链式增量诊断（诊断/达标判定的对象是陈述表达；优先级更无模型通道）。
# curation/boundary_note 维持 P2 拍板口径（视为内容修订→达标待重诊），不在此列。
_ATTRIBUTE_FIELDS = ("verification_method", "verification_note", "priority")


def _canonical_source_refs(raw: Optional[str]) -> str:
    """来源要素引用清单的规范形：去重 ＋ 升序 ＋ JSON 序列化。

    before/after 均按此口径投影，使「同一集合换序」不被判为内容变更
    （避免误触发诊断失效）；非法/空输入折叠为 "[]"（留痕语义）。
    """
    try:
        refs = json.loads(raw or "[]")
    except (TypeError, ValueError):
        refs = []
    unique = sorted({str(r) for r in refs}) if isinstance(refs, list) else []
    return json.dumps(unique, ensure_ascii=False)


def _item_field_value(item: ItemRow, field_key: str) -> str:
    """可修订字段的当前值（修订留痕 before_value 用；说明字段空值以空串留痕）。"""
    return {
        "expression": item.expression,
        "req_type": item.req_type,
        "curation_note": item.curation_note or "",
        "boundary_note": item.boundary_note or "",
        "source_element_refs": _canonical_source_refs(item.source_element_refs),
        "verification_method": item.verification_method or "",
        "verification_note": item.verification_note or "",
        "priority": item.priority or "",
    }[field_key]


def split_verification_methods(stored: Optional[str]) -> list[str]:
    """验证方式落库逗号连接 → 读视图列表（空值恒为空列表；形成/评审/资产读侧共用）。"""
    return [c for c in (stored or "").split(",") if c]


def normalize_verification_method(raw: str) -> str:
    """验证方式多选规范化：逗号分隔 → 去重保序；非法码 InvalidInput。

    允许组合（29148 口径；如 demonstration,analysis）；空串由调用侧先行拒绝。
    """
    codes: list[str] = []
    for part in raw.split(","):
        code = part.strip().lower()
        if not code:
            continue
        if code not in _VERIFICATION_METHOD_CODES:
            raise InvalidInput(
                f"不支持的验证方式：{code}（可选 {'/'.join(sorted(_VERIFICATION_METHOD_CODES))}，多选逗号分隔）"
            )
        if code not in codes:
            codes.append(code)
    if not codes:
        raise InvalidInput("验证方式不能为空（多选逗号分隔）")
    return ",".join(codes)


def content_revision_seq(revisions: list[ItemRevisionRow]) -> int:
    """条目内容修订序号（投影版本锚；增补 §3 勘正口径）。

    = 1 + 内容变更型修订记录数（before != after；拒绝建议等无变更留痕不计；
    属性字段修订不计——验证方式/验收准则/优先级不改变陈述内容；
    非修订记录不计——人工确认背书借表落库但没改任何字段，见 NON_REVISION_FIELD_KEYS，
    否则背书会让投影判 stale、区4 体检整块消失且永不自愈）。
    `LDM-007.version_no` 归 req_no 替代族谱（SCN-003 变更路径），不用作此锚。
    """
    return 1 + sum(
        1 for r in revisions
        if r.before_value != r.after_value
        and r.field_key not in _ATTRIBUTE_FIELDS
        and r.field_key not in NON_REVISION_FIELD_KEYS
    )
_ITEM_TYPE_CODES = {t.value for t in RequirementItemType}
_VERIFICATION_METHOD_CODES = {m.value for m in VerificationMethod}
_PRIORITY_CODES = {p.value for p in ItemPriority}


class TxSession(Protocol):
    """链式派发的事务边界句柄（装配层注入 SQLAlchemy Session；测试可注入等价对象）。"""

    def commit(self) -> None: ...
    def rollback(self) -> None: ...


@dataclass(frozen=True)
class ChainedRecheckDispatch:
    """链式结构复核派发结果：run 供前端跟踪，信封 ref 供回执读取（AEP-114 读侧）。"""

    run_ref: str
    recheck_context_ref: str


def build_recheck_envelope(
    model_results: ModelResultRepository,
    *,
    project_ref: str,
    parse_result_ref: str,
    item_refs: list[str],
    operator_ref: str,
    chained: bool = False,
    idempotency_key: Optional[str] = None,
    basis: str = "结构复核批次受理（过程信封）",
) -> str:
    """结构复核批次受理信封（LDM-015；手动/链式共用，issue #8 清理债：信封构造收口单处）。

    item_revs 由 prepare 阶段逐条目锚定（缺陷 1 CAS 版本锚）；refreshed/expired_skipped/
    failed/skipped 由执行过程逐条目登记（回执两集合口径的事实源）。
    """
    payload: dict = {
        "project_ref": project_ref,
        "parse_result_ref": parse_result_ref,
        "item_refs": item_refs,
        "operator_ref": operator_ref,
        "item_revs": {},
    }
    if chained:
        payload["chained"] = True
    if idempotency_key:
        # payload 内键保留（向后兼容读侧/历史行）；同步写入索引列供等值幂等查询。
        payload[RECHECK_IDEMPOTENCY_PAYLOAD_KEY] = idempotency_key
    return model_results.record_stage_payload(
        _STAGE_RECHECK, parse_result_ref, "batch_accepted",
        json.dumps(payload, ensure_ascii=False), basis,
        recheck_idempotency_key=idempotency_key or None,
    )


def dispatch_chained_structure_recheck(
    model_results: ModelResultRepository,
    model_orchestration: ModelOrchestration,
    items: RequirementItemRepository,
    item_refs: list[str],
    session: Optional[TxSession] = None,
) -> Optional[ChainedRecheckDispatch]:
    """内容变更后的链式结构复核派发（修订/拆分/归并挂点；走查第三轮裁定 2026-07-11）。

    目的=让「修订后未复核」退化为不足感知的在途瞬态：真判定秒级落地，UI 不再呈现该状态。
    绕过批级在飞去重（单事件信封；CAS 版本锚使重复判定收敛无害）。

    事务边界（issue #8 缺陷 4 裁定）：用户写入先 commit 落库，派发不进主事务——
    派发失败独立捕获（回滚脏事务＋结构化日志＋持久通知），不得丢用户写入、
    不得让后续请求踩 PendingRollbackError；体检刷新失败不阻断修订/拆分/归并主流程
    （修复通道=AEP-114 手动入口）。未注入 session 的装配（纯内存测试）保持原吞错行为。
    模块级函数：形成服务与评审侧装配（revision_applier 的条目服务）共用，避免循环装配。
    """
    refs = [r for r in item_refs if r]
    if not refs:
        return None
    head = items.get_item(refs[0])
    if head is None:
        return None
    if session is not None:
        session.commit()  # 用户写入先落库：链式派发不进主事务
    try:
        envelope_ref = build_recheck_envelope(
            model_results,
            project_ref=head.project_ref,
            parse_result_ref=head.parse_result_ref,
            item_refs=refs,
            operator_ref="system",
            chained=True,
            basis="结构复核批次受理（内容变更链式，过程信封）",
        )
        run = model_orchestration.request_item_structure_recheck(envelope_ref)
        log_event(_COMPONENT, "item.recheck.chained", context_ref=envelope_ref,
                  target_count=len(refs), ok=True)
        return ChainedRecheckDispatch(run_ref=run, recheck_context_ref=envelope_ref)
    except Exception as exc:  # noqa: BLE001 链式体检失败不得阻断内容写入主流程
        log_event(_COMPONENT, "item.recheck.chain_failed", level="ERROR", ok=False,
                  item_ref=refs[0], target_count=len(refs),
                  error_code=type(exc).__name__,
                  hint="用户写入已落库；体检未发起，可经区2「复核」修复通道补检")
        if session is not None:
            session.rollback()  # 清除派发残留的脏事务，防后续请求 PendingRollbackError
            from app.services.notification import notify_safely

            notify_safely(
                session,  # type: ignore[arg-type]  TxSession 由装配层以 Session 注入
                kind="recheck.dispatch_failed",
                dedup_key=f"recheck.dispatch_failed:{refs[0]}",
                title="条目结构体检未能自动发起",
                summary="内容修订已保存；自动体检派发失败，可在条目形成页区2「复核」手动补检。",
                project_ref=head.project_ref,
                ref=refs[0],
            )
            session.commit()
        return None


def project_element(r: ElementRow) -> RequirementElementRead:
    """ElementRow → 读视图投影（条目形成/条目评审工作区共用）。"""
    origin_refs: list[str] = []
    if r.origin_refs:
        try:
            origin_refs = list(json.loads(r.origin_refs))
        except ValueError:
            origin_refs = []
    return RequirementElementRead(
        id=r.id, element_type=r.element_type, content=r.content,
        source_anchor=r.source_anchor, confidence=r.confidence,
        process_status=r.process_status, model_verdict=r.model_verdict,
        # 两个证据/处置字段与 analysis_workspace._project_element 对齐：同一个读模型的两份投影，
        # 少一列就会在条目形成/评审页开始显示它们的那天静默取到空值（冷审查裁定 K4）
        verdict_reason=r.verdict_reason, noise_triage=r.noise_triage,
        version=r.version, superseded=r.superseded,
        review_conclusion=r.review_conclusion, review_basis=r.review_basis,
        revision_draft=r.revision_draft, correction_note=r.correction_note,
        origin_refs=origin_refs,
    )


def material_raw_text(process_records, source_assets, parse_context_ref: str) -> str:
    """读取工作区材料原文（LDM-002 当前来源版本）。"""
    material_ref = process_records.read_parse_material_ref(parse_context_ref)
    content = source_assets.read_material_content(material_ref) if material_ref else None
    return content.raw_text if content else ""


def build_material_canvas(process_records, source_assets, parse_context_ref: str) -> Optional[MaterialCanvasRead]:
    """材料正文画布读视图（条目形成/条目评审只读来源画布共用）。"""
    material_ref = process_records.read_parse_material_ref(parse_context_ref)
    if material_ref is None:
        return None
    content = source_assets.read_material_content(material_ref)
    if content is None:
        return None
    title = "来源材料"
    for seg in (content.source_note or "").split("；"):
        if seg.startswith("接入对象:") and len(seg) > len("接入对象:"):
            title = seg[len("接入对象:"):]
            break
    return MaterialCanvasRead(
        material_ref=material_ref,
        title=title,
        source_note=content.source_note or None,
        raw_text=content.raw_text,
        source_version=source_assets.material_source_version(material_ref),
        blocks=[MaterialTextBlockRead(**b) for b in split_blocks(content.raw_text)],
        supplements=[
            MaterialSupplementRead(
                supplement_ref=s.id, content=s.content, basis=s.basis,
                operator_ref=s.operator_ref, at=s.at,
            )
            for s in source_assets.supplements_of(material_ref)
        ],
    )


def _anchor_resolvable(element: ElementRow, raw_text: str) -> bool:
    """来源锚点能否回到 LDM-002 原文（offset 或引文任一可回）。"""
    if not element.source_anchor:
        return False
    try:
        ranges = json.loads(element.source_anchor).get("ranges", [])
    except (ValueError, AttributeError):
        return False
    return any(
        (0 <= r.get("start", -1) < r.get("end", 0) <= len(raw_text))
        or (r.get("exact") and r["exact"] in raw_text)
        for r in ranges
    )


class ItemFormationService:
    """条目形成服务（AEP-038 + 工作区读视图）。"""

    def __init__(
        self,
        model_orchestration: ModelOrchestration,
        model_results: ModelResultRepository,
        process_records: ProcessRecordRepository,
        formation_process: ItemFormationProcessRepository,
        items: RequirementItemRepository,
        source_assets: SourceAssetRepository,
        command_interpreter: Optional[FormationCommandInterpreter] = None,
        draft_composer: Optional[ItemDraftComposer] = None,
        explainer: Optional[ItemExplainer] = None,
        item_service: Optional["RequirementItemService"] = None,
        active_convention_resolver: Optional[Callable[[], str]] = None,
        supporting_basis_writer: Optional[Callable[..., object]] = None,
        session: Optional[TxSession] = None,
    ) -> None:
        self._model_orchestration = model_orchestration
        self._model_results = model_results
        self._process_records = process_records
        self._formation_process = formation_process
        self._items = items
        self._source_assets = source_assets
        # AEP-097 区5 对话能力（未装配时对话端点按能力缺失拒绝，直发端点不受影响）
        self._command_interpreter = command_interpreter
        self._draft_composer = draft_composer
        self._explainer = explainer
        self._item_service = item_service
        # 生效规约方案读取器（发起批次时读取一次并随批次固定；AEP-102 也读它）。
        # 未注入时回落默认方案（ears-cn），保持无配置=现状行为。
        self._active_convention_resolver = active_convention_resolver or (lambda: DEFAULT_CONVENTION)
        # P7 §1.2 业务知识依据引用写通道（复用 P4 create_supporting_basis）；未注入时 /引用依据 拒绝
        self._supporting_basis_writer = supporting_basis_writer
        # 事务边界句柄（缺陷 4：链式派发与用户写入解耦；缺陷 10：单条失败持久通知）。
        # 未注入（纯内存装配）时链式派发保持原行为、失败面仅结构化日志。
        self._session = session
        # 批次内材料原文缓存（issue #8 清理债：批次逐条重读全文材料）；服务实例按请求/任务建。
        self._raw_text_cache: dict[str, str] = {}

    # ------------------------------------------------------------------
    # AEP-102：需求规约方案目录（只读；设置页与区2 徽标数据源）
    # ------------------------------------------------------------------

    def list_requirement_conventions(self) -> RequirementConventionCatalogRead:
        """全部规约方案元数据 + 句式模板 + 完整示例 + 当前生效方案 key。

        只读、无副作用、不含 Prompt 原文（VAL-007）；文案取档案元数据（前端禁硬编码）。
        """
        active = self._active_convention_resolver()
        conventions = [
            RequirementConventionRead(
                convention_key=m.convention_key,
                display_name=m.display_name,
                blueprint=m.blueprint,
                positioning=m.positioning,
                pattern_overview=[
                    ConventionPatternRead(label=p.label, pattern=p.pattern) for p in m.pattern_overview
                ],
                examples=[
                    ConventionExampleRead(req_type=e.req_type, statement=e.statement) for e in m.examples
                ],
            )
            for m in convention_catalog()
        ]
        return RequirementConventionCatalogRead(active_convention=active, conventions=conventions)

    # ------------------------------------------------------------------
    # AEP-038：条目化批次启动（N01 入口承接 + N02 准入过滤 + N03 批次建立）
    # ------------------------------------------------------------------

    def start_element_itemization_batch(
        self, command: ItemizationBatchCommand
    ) -> ItemizationBatchRequestResult:
        replay = self._formation_process.find_formation_by_idempotency(command.idempotency_key)
        if replay is not None:  # 幂等重放：返回原批次，不重复创建
            return ItemizationBatchRequestResult(status="submitted", formation_context_ref=replay)

        parse_context = self._source_assets.parse_context_of(command.parse_result_ref)
        if parse_context is None:
            raise NotFound("解析结果不存在；条目化批次只接收已入库要素集合")

        # HK-1 单飞守卫：同 parse_result 已有在飞批次 → 复用在途（返回原批次供前端复挂轮询），
        # 不建新批不重复入队（防执行中刷新/双标签页并发双批 → 重复条目 + REQ 编号重号）。
        # 超判死阈值的悬批不挡新批（僵尸 run 不得永久锁死入口；收尸对账由 HK-2/HK-3 承接）。
        inflight = self._formation_process.find_inflight_formation_of_parse_result(
            command.parse_result_ref
        )
        if inflight is not None:
            if is_run_alive(_FORMATION_LANE, inflight):
                log_event(_COMPONENT, "item.batch.dedup_inflight", ok=True,
                          context_ref=inflight.formation_context_ref,
                          run_id=inflight.agent_run_ref, run_status=inflight.status)
                return ItemizationBatchRequestResult(
                    status="in_flight",
                    formation_context_ref=inflight.formation_context_ref,
                    agent_run_ref=inflight.agent_run_ref,
                    next_action="条目化批次执行中：已复用在途批次并恢复进度跟踪，请等待完成",
                )
            log_event(_COMPONENT, "item.batch.stale_inflight_ignored", level="WARN", ok=False,
                      context_ref=inflight.formation_context_ref,
                      run_id=inflight.agent_run_ref, run_status=inflight.status,
                      hint="悬批超判死阈值，不阻挡新批；状态收尸属 HK-2/HK-3 对账职责")

        current = str(self._process_records.read_workspace_version(parse_context))
        if command.workspace_version != current:
            return ItemizationBatchRequestResult(
                status="rejected_precheck",
                next_action="工作区已更新（版本不一致），请刷新后重试",
            )

        elements = {
            e.id: e
            for e in self._source_assets.elements_of(command.parse_result_ref)
            if not e.superseded
        }

        # N01 批次范围：全部可条目化 / 勾选子集 / 单个（=批次大小 1）
        if command.scope_type == ItemizationScopeType.ALL_ELIGIBLE:
            candidates = list(elements.values())
        else:
            if not command.target_element_refs:
                return ItemizationBatchRequestResult(
                    status="rejected_precheck",
                    next_action="请先勾选需要条目化的知识项",
                )
            if (
                command.scope_type == ItemizationScopeType.SINGLE_ELEMENT
                and len(command.target_element_refs) != 1
            ):
                raise InvalidInput("single_element 批次只能携带一个要素")
            missing = [r for r in command.target_element_refs if r not in elements]
            if missing:
                raise RejectedTransition("选定要素不在当前集合（版本冲突或已被替代），请刷新工作区")
            candidates = [elements[r] for r in command.target_element_refs]

        # N02 准入过滤：已确认 ∧ 需求表达类 → 主路径；其余逐要素停靠归因
        eligible: list[ElementRow] = []
        blocked: list[tuple[ElementRow, str, str]] = []  # (row, reason, next_action)
        raw_text = self._material_raw_text(parse_context)
        already_itemized = self._itemized_element_refs(command.parse_result_ref)
        for row in candidates:
            if row.element_type not in _ELEMENT_TO_ITEM_TYPE:
                if command.scope_type != ItemizationScopeType.ALL_ELIGIBLE:
                    blocked.append((row, "支撑或上下文类要素仅作为依据，不单独生成需求条目",
                                    "如需成为条目，请回需求分析调整要素类型"))
                continue  # 全选批次下支撑性要素只是不进入，不算停靠
            if row.process_status != ES.CONFIRMED.value:
                blocked.append((row, "要素未处于已确认状态，不进入条目形成",
                                "请回需求分析（SCN-001-P04）确认或校正该要素"))
                continue
            if not _anchor_resolvable(row, raw_text):
                blocked.append((row, "来源锚点缺失或无法回到原文，不创建无来源条目",
                                "请回需求分析调整来源范围或补充材料"))
                continue
            if row.id in already_itemized:
                blocked.append((row, "该要素已形成待确认条目", "如需修订请在条目列表使用字段修订"))
                continue
            eligible.append(row)

        if not eligible:
            # 分支：无适合条目形成的需求表达类要素 → 不建批次、不创建 LDM-007
            log_event(_COMPONENT, "item.batch.no_eligible", context_ref=parse_context, ok=False)
            return ItemizationBatchRequestResult(
                status="rejected_precheck",
                next_action="没有适合条目形成的知识项：请先在需求分析中确认需求表达类要素",
            )

        # N03 批次建立（运行过程边界，不形成新的需求事实源）。
        # 生效规约方案在此读取一次并随批次固定（批次内一致，避免执行中途切换导致混排；选型文档 §5）。
        convention_key = self._active_convention_resolver()
        log_event(_COMPONENT, "item.batch.convention_fixed", context_ref=parse_context,
                  convention_key=convention_key)
        formation_context = self._formation_process.create_formation_request(
            command.project_ref, parse_context, command.parse_result_ref,
            command.scope_type.value, json.dumps([e.id for e in eligible]),
            command.operator_ref, command.idempotency_key,
            convention_key=convention_key,
        )
        for row, reason, next_action in blocked:  # 准入停靠逐要素归因
            self._formation_process.record_outcome(
                formation_context, row.id, IR.BLOCKED.value, None, None, reason, next_action
            )

        run = self._model_orchestration.request_item_formation(formation_context)
        log_event(_COMPONENT, "item.batch.submitted", context_ref=formation_context,
                  eligible_count=len(eligible), blocked_count=len(blocked))
        return ItemizationBatchRequestResult(
            status="submitted",
            formation_context_ref=formation_context,
            agent_run_ref=run,
        )

    # ------------------------------------------------------------------
    # N05 送检结果承接 + N06 裁定 + N07 创建待确认 LDM-007（模型编排内部回交）
    # ------------------------------------------------------------------

    def accept_item_formation_element_result(
        self, formation_context_ref: str, element_ref: str,
        model_result_ref: Optional[str],
    ) -> None:
        """逐要素承接：单要素格式化结果一旦落库立即可见，不等待同批次其它要素。"""
        req = self._formation_process.get_formation_request(formation_context_ref)
        if req is None:
            raise RejectedTransition("条目化批次上下文不存在")

        element = self._source_assets.get_element(element_ref)
        if element is None or model_result_ref is None:
            self._formation_process.record_outcome(
                formation_context_ref, element_ref, IR.SKIPPED.value, None, None,
                "要素已不在当前集合", "请刷新工作区",
            )
            return

        result = self._model_results.read_stage_payload(model_result_ref)
        if result is None:
            raise RejectedTransition("条目格式化结果 LDM-015 不存在")

        if result.result_code == "formation_failed":
            # 分支：模型格式化失败/超时/不可承接 → 失败类 LDM-015 已落，不写 LDM-007
            self._formation_process.record_outcome(
                formation_context_ref, element_ref, IR.FAILED.value, None, model_result_ref,
                result.basis or "条目格式化失败", "可重试生成或人工继续，不伪造条目",
            )
            log_event(_COMPONENT, "item.formation.element_failed", level="WARN",
                      context_ref=formation_context_ref, element_ref=element_ref, ok=False)
            return

        body = json.loads(result.payload) if result.payload else {}
        entry = next(
            (i for i in body.get("items", []) if str(i.get("element_ref")) == element_ref),
            None,
        )
        verdict = self._adjudicate_entry(element, entry)
        if verdict is not None:  # N06 拦截：不写 LDM-007
            reason, next_action = verdict
            self._formation_process.record_outcome(
                formation_context_ref, element_ref, IR.FAILED.value, None, model_result_ref,
                reason, next_action,
            )
            log_event(_COMPONENT, "item.formation.entry_rejected", level="WARN",
                      element_ref=element_ref, ok=False)
            return

        # N07：状态机裁定 初始→待确认（唯一合法创建迁移）
        nxt = item_transition(ItemState.INITIAL, ItemEvent.FORM)
        assert nxt is ItemState.PENDING_CONFIRMATION
        req_no = f"REQ-{self._items.max_req_seq_of_project(req.project_ref) + 1:03d}"
        item_ref = self._items.create_pending_item(
            req.project_ref, req.parse_result_ref, formation_context_ref,
            req_no, str(entry["expression"]).strip(),
            _ELEMENT_TO_ITEM_TYPE[element.element_type],
            json.dumps([element_ref]), model_result_ref,
            curation_note=str(entry.get("curation_note") or "").strip() or None,
            boundary_note=str(entry.get("boundary_note") or "").strip() or None,
            verification_method=self._accept_verification_method(entry),
            verification_note=str(entry.get("verification_note") or "").strip() or None,
        )
        # 达标投影落表（过程记录，可整层重算；形成时内容修订序号=1）。
        # 口径锚取本批次固定方案（req.convention_key），与 LDM-015 记录一致。
        self._write_structure_projection(
            item_ref, entry, model_result_ref, req.convention_key, item_content_rev=1,
        )
        self._formation_process.record_outcome(
            formation_context_ref, element_ref, IR.CREATED.value, item_ref, model_result_ref,
            None, None,
        )
        self._model_results.record_adoption(
            model_result_ref=model_result_ref, project_ref=req.project_ref,
            stage="item_formation", subject_type="requirement_item", subject_ref=item_ref,
            outcome="adopted", operator_ref="system",
            idempotency_key=f"formation:{formation_context_ref}:adoption:{element_ref}",
        )
        suggestion = str(entry.get("suggestion") or "").strip()
        if suggestion and suggestion != str(entry["expression"]).strip():
            self._formation_process.save_suggestion(
                item_ref, "expression", suggestion,
                str(entry.get("suggestion_reason") or "模型替代表达建议"),
                model_result_ref,
            )
        log_event(_COMPONENT, "item.status.transition", item_ref=item_ref,
                  from_status="initial", to_status=IS.PENDING_CONFIRMATION.value,
                  sm_event=ItemEvent.FORM.value, ok=True)

    def complete_item_formation_batch(self, formation_context_ref: str) -> None:
        """批次收束：全批失败才停靠（保留可重试语义）；版本推进一次。"""
        req = self._formation_process.get_formation_request(formation_context_ref)
        if req is None:
            raise RejectedTransition("条目化批次上下文不存在")
        outcomes = self._formation_process.outcomes_of(formation_context_ref)
        created = sum(1 for o in outcomes if o.result_status == IR.CREATED.value)
        failed = any(o.result_status == IR.FAILED.value for o in outcomes)
        if created == 0 and failed:
            self._formation_process.mark_formation_stopped(
                formation_context_ref, "条目格式化失败：可重试或人工继续"
            )
            log_event(_COMPONENT, "item.formation.failed", level="WARN",
                      context_ref=formation_context_ref, ok=False)
        self._process_records.bump_workspace_version(req.parse_context_ref)
        target_refs = list(json.loads(req.target_refs or "[]"))
        log_event(_COMPONENT, "item.batch.completed", context_ref=formation_context_ref,
                  created_count=created, target_count=len(target_refs), ok=True)

# ------------------------------------------------------------------
    # AEP-114：条目结构复核（只判不改；结果重写达标投影并锚定当前内容修订序号）
    # ------------------------------------------------------------------

    def _recheck_target_state(self, item: ItemRow, review) -> Optional[str]:
        """复核目标态（裁定 2 收窄口径）。

        'stale'＝修订后未复核（投影版本锚落后于当前内容修订序号）；
        'missing'＝无体检结果（从未落投影，或投影未得出完备性判定——含人工拆分/归并条目）；
        None＝不在目标集：非待确认（已终止不在流程内，烧预算无意义；确认态判定已冻结），
        或现行判定（内容与档案未变时重跑只产生 LLM 判定方差——一条没动过的「完备」被
        随机翻成「不完备」是信任缺陷）。
        """
        if item.status != IS.PENDING_CONFIRMATION.value:
            return None
        if review is None or not review.facets:
            return "missing"
        if review.stale:
            return "stale"
        if review.completeness is None:
            return "missing"
        return None

    def start_structure_recheck(
        self, command: StructureRecheckCommand
    ) -> StructureRecheckRequestResult:
        """结构复核批次受理（AEP-114）：受理立即返回，逐条目异步，AgentRun 追踪。

        item_refs 空=默认目标集；幂等重放同 key 返回原批次（缺陷 9）；在飞去重按目标
        覆盖集比对（缺陷 3）：已覆盖目标复用在途批次，未覆盖目标正常入队新批。
        批次上下文=LDM-015 受理信封（stage=item_structure_recheck；零迁移，不新增过程表）。
        """
        replay = self._formation_process.find_recheck_by_idempotency(
            command.idempotency_key, command.parse_result_ref
        )
        if replay is not None:  # 幂等重放：返回原批次，不重复受理（照 AEP-038 同款）
            log_event(_COMPONENT, "item.recheck.idempotent_replay", ok=True,
                      context_ref=replay.formation_context_ref,
                      run_id=replay.agent_run_ref)
            return StructureRecheckRequestResult(
                status="submitted",
                recheck_context_ref=replay.formation_context_ref,
                agent_run_ref=replay.agent_run_ref or None,
                next_action="结构复核已受理（幂等重放）：完成后体检结果自动刷新",
            )

        parse_context = self._source_assets.parse_context_of(command.parse_result_ref)
        if parse_context is None:
            raise NotFound("解析结果不存在；结构复核只接收已入库条目集合")
        current = str(self._process_records.read_workspace_version(parse_context))
        if command.workspace_version != current:
            log_event(_COMPONENT, "item.recheck.rejected_precheck", level="WARN", ok=False,
                      context_ref=command.parse_result_ref, reason="workspace_version_conflict")
            return StructureRecheckRequestResult(
                status="rejected_precheck",
                next_action="工作区已更新（版本不一致），请刷新后重试",
            )

        # 目标集先算（在飞覆盖集比对需要；单条路径只算被点名条目的修订序号，免全工作区 N+1）
        items = self._items.items_of_parse_result(command.parse_result_ref)
        if command.item_refs:
            known = {i.id: i for i in items}
            missing_refs = [r for r in command.item_refs if r not in known]
            if missing_refs:
                raise RejectedTransition("选定条目不在当前集合（版本冲突或已被替代），请刷新工作区")
            scoped = [known[r] for r in command.item_refs]
        else:
            scoped = items
        reviews = self._structure_reviews_of(scoped)
        state_of = {i.id: self._recheck_target_state(i, reviews.get(i.id)) for i in scoped}
        if command.item_refs:
            targets = [r for r in command.item_refs if state_of.get(r)]
            if not targets:
                # 缺陷 6：区分「现行判定」与「已离开待确认」——判定随确认/终止冻结，
                # 不得误报「判定已是当前表达的结果」（对话路径守卫的 AEP-114 同款）
                frozen = [
                    known[r] for r in command.item_refs
                    if known[r].status != IS.PENDING_CONFIRMATION.value
                ]
                if len(frozen) == len(command.item_refs):
                    log_event(_COMPONENT, "item.recheck.rejected_precheck", level="WARN",
                              ok=False, context_ref=command.parse_result_ref,
                              reason="targets_left_pending", target_count=len(frozen))
                    return StructureRecheckRequestResult(
                        status="rejected_precheck",
                        next_action="所选条目均已离开待确认，判定随状态冻结不再复核；旧体检结果仅供参考",
                    )
                # 全部现行判定（或混有冻结条目）：零 LLM 直发回执（裁定 2）
                suffix = f"；其中 {len(frozen)} 条已离开待确认，判定随状态冻结" if frozen else ""
                return StructureRecheckRequestResult(
                    status="noop_current",
                    next_action=f"判定已是当前表达的结果，无需复核{suffix}",
                )
        else:
            targets = [i.id for i in scoped if state_of[i.id]]
            if not targets:
                log_event(_COMPONENT, "item.recheck.rejected_precheck", level="WARN", ok=False,
                          context_ref=command.parse_result_ref, reason="no_targets")
                return StructureRecheckRequestResult(
                    status="rejected_precheck",
                    next_action="没有需要复核的条目：待确认条目均有当前体检",
                )

        # 在飞去重（缺陷 3）：同解析结果在途批次只挡「已被其覆盖」的目标；
        # 未覆盖目标正常入队新批，杜绝「从未尝试却报失败」。悬批不挡新批（同 HK-1）。
        covered_note = ""
        inflight = self._formation_process.find_inflight_recheck_of_parse_result(
            command.parse_result_ref
        )
        if inflight is not None:
            if is_run_alive(_RECHECK_LANE, inflight):
                envelope = self._model_results.read_stage_payload(
                    inflight.formation_context_ref
                )
                body = json.loads(envelope.payload) if envelope and envelope.payload else {}
                # 合并裁定修复 K8：covered 只算在途批「尚未定格」的余集——已 refreshed/
                # expired_skipped/failed/skipped 的条目在途批不会再访，不得吞掉对它们的重试。
                settled = {
                    str(r)
                    for bucket in ("refreshed", "expired_skipped", "failed", "skipped")
                    for r in (body.get(bucket) or [])
                }
                covered = {str(r) for r in (body.get("item_refs") or [])} - settled
                uncovered = [r for r in targets if r not in covered]
                if not uncovered:
                    log_event(_COMPONENT, "item.recheck.dedup_inflight", ok=True,
                              context_ref=inflight.formation_context_ref,
                              run_id=inflight.agent_run_ref, run_status=inflight.status)
                    return StructureRecheckRequestResult(
                        status="in_flight",
                        recheck_context_ref=inflight.formation_context_ref,
                        agent_run_ref=inflight.agent_run_ref,
                        target_item_refs=targets,
                        next_action="结构复核执行中：已复用在途批次并恢复进度跟踪，请等待完成",
                    )
                skipped = len(targets) - len(uncovered)
                if skipped:
                    covered_note = f"；另有 {skipped} 条已在途批次中，不重复入队"
                log_event(_COMPONENT, "item.recheck.partial_inflight", ok=True,
                          context_ref=inflight.formation_context_ref,
                          covered_count=skipped, uncovered_count=len(uncovered))
                targets = uncovered
            else:
                log_event(_COMPONENT, "item.recheck.stale_inflight_ignored", level="WARN",
                          ok=False, context_ref=inflight.formation_context_ref,
                          run_id=inflight.agent_run_ref, run_status=inflight.status,
                          hint="悬批超判死阈值，不阻挡新批")

        stale_count = sum(1 for r in targets if state_of[r] == "stale")
        missing_count = len(targets) - stale_count

        # 受理信封（过程记录，非判定结论；applies_to=parse_result 供在飞去重联查）
        envelope_ref = build_recheck_envelope(
            self._model_results,
            project_ref=command.project_ref,
            parse_result_ref=command.parse_result_ref,
            item_refs=targets,
            operator_ref=command.operator_ref,
            idempotency_key=command.idempotency_key,
        )
        run = self._model_orchestration.request_item_structure_recheck(envelope_ref)
        log_event(_COMPONENT, "item.recheck.submitted", context_ref=envelope_ref,
                  target_count=len(targets), stale_count=stale_count,
                  missing_count=missing_count)
        return StructureRecheckRequestResult(
            status="submitted",
            recheck_context_ref=envelope_ref,
            agent_run_ref=run,
            target_item_refs=targets,
            next_action=f"结构复核已受理，完成后体检结果自动刷新{covered_note}",
        )

    def dispatch_chained_recheck(self, item_refs: list[str]) -> Optional[ChainedRecheckDispatch]:
        """内容变更后的链式结构复核派发（修订/拆分/归并挂点；走查第三轮裁定）。"""
        return dispatch_chained_structure_recheck(
            self._model_results, self._model_orchestration, self._items, item_refs,
            session=self._session,
        )

    def read_structure_recheck_outcome(
        self, recheck_context_ref: str, project_ref: Optional[str] = None
    ) -> "StructureRecheckOutcomeRead":
        """复核批次逐条目结局读视图（AEP-114 读侧；回执两集合口径的事实源）。

        事实全部来自受理信封的过程账目（执行侧逐条目登记）：已重判 / 修订在飞已过期
        跳过（缺陷 1 CAS 丢弃）/ 复核失败（旧判保留）/ 离开流程跳过；余量=尚未执行。
        """
        envelope = self._model_results.read_stage_payload(recheck_context_ref)
        # 合并裁定修复 K_outcome：① 只认批次受理信封（per-item 判定类共用 stage,
        # result_code=batch_accepted 才是批次信封）；② 校验 project 归属（与两个 POST
        # 端点对称,防跨项目 IDOR 读）。二者任一不符=不存在，不泄漏他项目账目。
        if envelope is None or envelope.stage != _STAGE_RECHECK or envelope.result_code != "batch_accepted":
            raise NotFound("结构复核批次不存在")
        body = json.loads(envelope.payload) if envelope.payload else {}
        if project_ref is not None and str(body.get("project_ref") or "") != project_ref:
            raise NotFound("结构复核批次不存在")
        targets = [str(r) for r in (body.get("item_refs") or [])]
        refreshed = [str(r) for r in (body.get("refreshed") or [])]
        expired = [str(r) for r in (body.get("expired_skipped") or [])]
        failed = [str(r) for r in (body.get("failed") or [])]
        skipped = [str(r) for r in (body.get("skipped") or [])]
        settled = {*refreshed, *expired, *failed, *skipped}
        return StructureRecheckOutcomeRead(
            recheck_context_ref=recheck_context_ref,
            target_item_refs=targets,
            refreshed_refs=refreshed,
            expired_skipped_refs=expired,
            failed_refs=failed,
            skipped_refs=skipped,
            pending_refs=[r for r in targets if r not in settled],
        )

    def _update_recheck_envelope(
        self, recheck_context_ref: str, mutate: Callable[[dict], None]
    ) -> None:
        """受理信封过程账目登记（item_revs 版本锚 / 逐条目结局；零迁移，信封为既有 JSON）。"""
        envelope = self._model_results.read_stage_payload(recheck_context_ref)
        if envelope is None:
            return
        body = json.loads(envelope.payload) if envelope.payload else {}
        mutate(body)
        self._model_results.update_stage_payload(
            recheck_context_ref, json.dumps(body, ensure_ascii=False)
        )

    def prepare_item_structure_recheck(
        self, recheck_context_ref: str, item_ref: str
    ) -> Optional[dict]:
        """复核执行前逐条目准入与上下文组装（编排回调）；None=跳过该条目。

        准入=待确认或确认态（走查第三轮裁定：覆盖"采纳修订→秒内确认"竞态——确认后
        内容已冻结，判一次即永久现行，无判定方差风险）；已终止/被替代跳过（不在流程内）。
        版本锚（缺陷 1 CAS）：此刻把 content_revision_seq 锚进批次信封——判定基于此刻
        读到的表达，accept 时序号前进即判定过期丢弃，禁止事后盖章。
        口径锚沿既有投影记录的 convention_key，无投影者走批次→生效方案解析链（缺陷 2）。
        """
        # 合并裁定修复 K7：版本锚的读取必须不晚于其所保护的内容快照。先锚后读内容——
        # 若内容读到期间修订抢先提交，accept 时 current_rev>anchored 判为过期跳过（失败安全），
        # 而非旧表达配新锚导致 CAS 自比通过、陈旧判定被盖章为现行。
        anchored_rev = content_revision_seq(self._items.revisions_of(item_ref))
        item = self._items.get_item(item_ref)
        if item is None or item.status not in (
            IS.PENDING_CONFIRMATION.value, IS.CONFIRMED.value,
        ):
            log_event(_COMPONENT, "item.recheck.item_skipped", level="WARN", ok=False,
                      context_ref=recheck_context_ref, item_ref=item_ref,
                      reason="条目不存在或已终止/被替代")
            self._update_recheck_envelope(
                recheck_context_ref,
                lambda body: body.setdefault("skipped", []).append(item_ref),
            )
            return None
        self._update_recheck_envelope(
            recheck_context_ref,
            lambda body: body.setdefault("item_revs", {}).__setitem__(item_ref, anchored_rev),
        )
        parse_context = self._source_assets.parse_context_of(item.parse_result_ref)
        raw_text = self._cached_material_raw_text(parse_context) if parse_context else ""
        sources: list[dict] = []
        for ref in json.loads(item.source_element_refs or "[]"):
            element = self._source_assets.get_element(ref)
            if element is None:
                continue
            sources.append({
                "id": element.id, "element_type": element.element_type,
                "content": element.content,
                "source_quote": first_anchor_quote(element.source_anchor),
            })
        return {
            "project_ref": item.project_ref,
            "raw_text": raw_text,
            "item": {
                "item_ref": item.id, "req_no": item.req_no,
                "expression": item.expression, "req_type": item.req_type,
                "curation_note": item.curation_note or "",
                "boundary_note": item.boundary_note or "",
            },
            "sources": sources,
            "convention_key": self._item_convention(item),
        }

    def accept_item_structure_recheck_result(
        self, recheck_context_ref: str, item_ref: str, model_result_ref: str
    ) -> None:
        """复核结果承接（编排回调）：CAS 比对版本锚后重写投影（锚=prepare 时快照序号）。

        - 版本锚前进（在飞期间修订/拆分/归并）→ 判定针对旧表达，丢弃不写投影，
          信封记「已过期跳过」（缺陷 1；该条目保持 stale，双入口可重跑）。
        - completeness 未得出（必备面向未全判定）→ 视同失败不承接（缺陷 5 服务端强制；
          「判定不造假」红线：不伪造完备性收敛）。
        - 失败类结果（recheck_failed）：旧投影原样保留＋持久通知（issue #8 第 10 项：
          run 成功内的单条失败也要用户离页可见），不阻断任何流程（A4）。
        """
        result = self._models_read_or_raise(model_result_ref)
        if result.result_code != "rechecked":
            self._record_recheck_item_failure(recheck_context_ref, item_ref)
            return
        body = json.loads(result.payload) if result.payload else {}
        review = body.get("review") or {}
        if review.get("completeness") not in ("complete", "incomplete"):
            log_event(_COMPONENT, "item.recheck.completeness_missing", level="WARN", ok=False,
                      context_ref=recheck_context_ref, item_ref=item_ref,
                      hint="必备面向未全判定，结果不承接（判定不造假）；旧投影原样保留")
            self._record_recheck_item_failure(recheck_context_ref, item_ref)
            return
        envelope = self._model_results.read_stage_payload(recheck_context_ref)
        envelope_body = json.loads(envelope.payload) if envelope and envelope.payload else {}
        anchored_rev = (envelope_body.get("item_revs") or {}).get(item_ref)
        current_rev = content_revision_seq(self._items.revisions_of(item_ref))
        if anchored_rev is None:
            # 防御分支：prepare 必锚（同一执行循环内）；缺锚按旧口径盖当前号并留痕
            log_event(_COMPONENT, "item.recheck.anchor_missing", level="WARN", ok=False,
                      context_ref=recheck_context_ref, item_ref=item_ref)
            anchored_rev = current_rev
        if current_rev > int(anchored_rev):
            # CAS 丢弃：判定针对旧表达；新表达的链式体检自会跟进（或经修复通道重跑）
            log_event(_COMPONENT, "item.recheck.expired_skipped", level="WARN", ok=False,
                      context_ref=recheck_context_ref, item_ref=item_ref,
                      anchored_rev=int(anchored_rev), current_rev=current_rev,
                      hint="在飞期间条目内容已修订，本次判定丢弃；条目可重新复核")
            self._update_recheck_envelope(
                recheck_context_ref,
                lambda b: b.setdefault("expired_skipped", []).append(item_ref),
            )
            return
        self._write_structure_projection(
            item_ref, review, model_result_ref,
            str(review.get("convention_key") or DEFAULT_CONVENTION),
            item_content_rev=int(anchored_rev),
        )
        self._update_recheck_envelope(
            recheck_context_ref,
            lambda b: b.setdefault("refreshed", []).append(item_ref),
        )
        log_event(_COMPONENT, "item.recheck.projection_refreshed", ok=True,
                  context_ref=recheck_context_ref, item_ref=item_ref,
                  item_content_rev=int(anchored_rev))

    def _models_read_or_raise(self, model_result_ref: str):
        result = self._model_results.read_stage_payload(model_result_ref)
        if result is None:
            raise RejectedTransition("结构复核结果 LDM-015 不存在")
        return result

    def _record_recheck_item_failure(self, recheck_context_ref: str, item_ref: str) -> None:
        """单条复核失败：信封记账＋持久通知（用户离页仍可发现；A10）。"""
        log_event(_COMPONENT, "item.recheck.item_failed", level="WARN", ok=False,
                  context_ref=recheck_context_ref, item_ref=item_ref,
                  hint="旧投影原样保留，可重试复核")
        self._update_recheck_envelope(
            recheck_context_ref,
            lambda b: b.setdefault("failed", []).append(item_ref),
        )
        if self._session is not None:
            from app.services.notification import notify_safely

            item = self._items.get_item(item_ref)
            req_no = f"：{item.req_no}" if item else ""
            notify_safely(
                self._session,  # type: ignore[arg-type]  装配层以 Session 注入
                kind="recheck.item_failed",
                dedup_key=f"recheck.item_failed:{item_ref}",
                title=f"条目结构复核未完成{req_no}",
                summary="该条目本次复核失败，旧体检结果保留原样；可在条目形成页区2「复核」重试。",
                project_ref=item.project_ref if item else None,
                ref=item_ref,
            )

    def _recheck_current_item(
        self, command: FormationDialogueCommand
    ) -> FormationDialogueResult:
        """区5 /复核＝复核当前条目（确定性直发，不经解释 lane；裁定 3）。"""
        label = _FORMATION_OPERATION_LABELS["structure.recheck"]
        item = self._dialogue_item_of(command)
        if item is None:
            return FormationDialogueResult(
                outcome="clarify", command_word="复核", operation="structure.recheck",
                operation_label=label, message="请先在区5 选中目标条目再复核。",
            )
        if item.status != IS.PENDING_CONFIRMATION.value:
            # 已确认/终止条目判定随状态冻结：不复核，也不误报「判定已是当前表达的结果」
            return FormationDialogueResult(
                outcome="rejected_precheck", command_word="复核",
                operation="structure.recheck", operation_label=label,
                message=f"{item.req_no} 已离开待确认，判定随确认冻结不再复核；旧体检结果仅供参考。",
            )
        outcome = self.start_structure_recheck(StructureRecheckCommand(
            project_ref=command.project_ref,
            parse_result_ref=command.parse_result_ref,
            workspace_version=command.workspace_version,
            item_refs=[item.id],
            operator_ref=command.operator_ref,
            idempotency_key=f"{command.idempotency_key}:dispatch",
        ))
        if outcome.status == "noop_current":
            # 现行判定：零 LLM、不产生 AgentRun 的确定性回执（裁定 2）
            return FormationDialogueResult(
                outcome="explanation", command_word="复核", operation="structure.recheck",
                operation_label=label,
                explanation=(
                    f"{item.req_no} 的体检判定已是当前表达的结果（内容未再修订），无需复核；"
                    "区4 报告即当前有效结论。"
                ),
            )
        if outcome.status == "in_flight":
            return FormationDialogueResult(
                outcome="queued", command_word="复核", operation="structure.recheck",
                operation_label=label, agent_run_ref=outcome.agent_run_ref,
                formation_context_ref=command.formation_context_ref,
                structure_recheck_context_ref=outcome.recheck_context_ref,
                message="结构复核执行中：已复用在途批次并恢复进度跟踪，完成后自动刷新。",
            )
        if outcome.status != "submitted":
            return FormationDialogueResult(
                outcome="rejected_precheck", command_word="复核",
                operation="structure.recheck", operation_label=label,
                message=outcome.next_action,
            )
        return FormationDialogueResult(
            outcome="queued", command_word="复核", operation="structure.recheck",
            operation_label=label, agent_run_ref=outcome.agent_run_ref,
            formation_context_ref=command.formation_context_ref,
            structure_recheck_context_ref=outcome.recheck_context_ref,
            message=(
                f"复核已受理：AI 正在按句式档案重新体检 {item.req_no} 的当前表达，"
                "完成后徽标与区4 报告自动刷新。"
            ),
        )

    @staticmethod
    def _accept_verification_method(entry: dict) -> Optional[str]:
        """承接模型建议的验证方式初稿：仅收编枚举内取值（去重保序），其余静默丢弃。

        方法属工程判断（允许建议初稿）；不合法输出降级为空，不拦截条目创建。
        """
        raw = entry.get("verification_method")
        parts = raw if isinstance(raw, list) else str(raw or "").split(",")
        codes: list[str] = []
        for part in parts:
            code = str(part or "").strip().lower()
            if code in _VERIFICATION_METHOD_CODES and code not in codes:
                codes.append(code)
        return ",".join(codes) or None

    def _adjudicate_entry(
        self, element: ElementRow, entry: Optional[dict]
    ) -> Optional[tuple[str, str]]:
        """N06 条目表达与来源映射裁定；返回 None=可写入，否则 (原因, next_action)。"""
        if entry is None:
            return ("模型输出未逐要素归因，格式化结果不可承接", "可重试生成或人工继续")
        expression = str(entry.get("expression") or "").strip()
        if not expression:
            return ("格式化结果为空，不可写入条目", "可重试生成或人工继续")
        # 含义一致性最小裁定：表达不得与来源要素完全脱钩（无任何字面重叠视为改变含义）
        content = element.content.strip()
        if content and not (set(expression) & set(content)):
            return ("格式化结果疑似改变要素含义，已拦截", "请修正格式化结果或人工处理")
        return None

    # ------------------------------------------------------------------
    # AEP-097：区5 对话命令解释（命令词确定性解析 + LLM 正文解释 + 校验派发）
    # ------------------------------------------------------------------

    def formation_dialogue(
        self, command: FormationDialogueCommand,
        on_stage: Optional[callable] = None,  # AiRequestStage 稳定码回调（SSE 流式链路回执）
    ) -> FormationDialogueResult:
        def _stage(stage: AiRequestStage) -> None:
            if on_stage is not None:
                on_stage(stage.value)

        context_ref = command.formation_context_ref or command.parse_result_ref
        message = (command.message or "").strip()
        if not message:
            return FormationDialogueResult(outcome="clarify", message="请输入内容后再发送。")

        try:
            chat_command, _ = resolve_command(FORMATION_COMMANDS, message)
        except UnknownCommand as exc:
            words = "、".join(f"/{w}" for w in FORMATION_COMMANDS)
            log_event(_COMPONENT, "dialogue.command.unknown", level="WARN",
                      context_ref=context_ref, word=exc.word, ok=False)
            return FormationDialogueResult(
                outcome="unknown_command", command_word=exc.word,
                message=f"未知命令 /{exc.word}。可用命令：{words}；不带斜杠即自由对话。",
            )
        word = chat_command.word if chat_command else None
        log_event(_COMPONENT, "dialogue.command.resolved", context_ref=context_ref,
                  command_word=word or "(free-text)")
        _stage(AiRequestStage.ACCEPTED)

        parse_context = self._source_assets.parse_context_of(command.parse_result_ref)
        if parse_context is None:
            raise NotFound("解析结果不存在；条目形成对话只接收已入库要素集合")
        current = str(self._process_records.read_workspace_version(parse_context))
        if command.workspace_version != current:
            return FormationDialogueResult(
                outcome="rejected_precheck", command_word=word,
                message="工作区已更新（版本不一致），请刷新后重试",
            )

        try:
            if word == "问来源":
                # 确定性来源指认：不调解释/生成模型，直接读来源要素与锚点
                _stage(AiRequestStage.DISPATCHING)
                result = self._explain_item_source(command)
            elif word == "复核":
                # /复核＝复核当前条目（AEP-114 直发通道，无自由参数不经解释 lane；
                # 现行判定条目零 LLM 直发回执，裁定 2/3）
                _stage(AiRequestStage.DISPATCHING)
                result = self._recheck_current_item(command)
            elif chat_command is None:
                # 自由文本：确定性路由（评审页同口径）——修订动词→起草；其余→解释
                item = self._dialogue_item_of(command)
                if item is None:
                    return FormationDialogueResult(
                        outcome="clarify", message="请先在区5 选中目标条目再对话。",
                    )
                _stage(AiRequestStage.RUNNING)
                if any(m in message for m in _DRAFT_MARKS):
                    result = self._formation_draft(
                        item, message, command, word=None, operation="draft.field", params={},
                    )
                else:
                    result = self._formation_explain(item, message, command)
            else:
                if self._command_interpreter is None:
                    raise InvalidInput("命令解释能力未装配")
                _stage(AiRequestStage.INTERPRETING)
                interpretation = self._command_interpreter.interpret(
                    word, command.message, self._formation_dialogue_context(command)
                )
                if interpretation.failed:
                    log_event(_COMPONENT, "dialogue.interpret.completed", level="WARN",
                              context_ref=context_ref, command_word=word, ok=False)
                    return FormationDialogueResult(
                        outcome="rejected_precheck", command_word=word,
                        message="命令解释服务暂不可用，请稍后重试；生成 / 修订等直发操作不受影响。",
                    )
                if interpretation.status in ("clarify", "cannot_comply"):
                    log_event(_COMPONENT, "dialogue.interpret.refused", context_ref=context_ref,
                              command_word=word, status=interpretation.status)
                    return FormationDialogueResult(
                        outcome=interpretation.status, command_word=word,
                        message=interpretation.reason or "请补充信息后重试。",
                    )
                if interpretation.operation not in chat_command.operations:
                    log_event(_COMPONENT, "dialogue.params.invalid", level="WARN",
                              context_ref=context_ref, command_word=word,
                              operation=interpretation.operation, ok=False)
                    return FormationDialogueResult(
                        outcome="clarify", command_word=word, operation=interpretation.operation,
                        message="该命令不支持解释出的操作，请换个说法或换用对应命令。",
                    )
                log_event(_COMPONENT, "dialogue.interpret.completed", context_ref=context_ref,
                          command_word=word, operation=interpretation.operation, ok=True)
                _stage(AiRequestStage.DISPATCHING)
                result = self._dispatch_formation_operation(
                    command, word, interpretation.operation, dict(interpretation.params), _stage,
                )
        except (InvalidInput, RejectedTransition) as exc:
            log_event(_COMPONENT, "dialogue.dispatch.failed", level="WARN",
                      context_ref=context_ref, command_word=word, reason=str(exc), ok=False)
            return FormationDialogueResult(
                outcome="rejected_precheck", command_word=word, message=str(exc),
            )
        log_event(_COMPONENT, "dialogue.dispatch.completed", context_ref=context_ref,
                  command_word=word, operation=result.operation, outcome=result.outcome, ok=True)
        return result

    def _dialogue_item_of(self, command: FormationDialogueCommand) -> Optional[ItemRow]:
        if not command.item_ref:
            return None
        item = self._items.get_item(command.item_ref)
        if item is None or item.parse_result_ref != command.parse_result_ref:
            return None
        return item

    def _formation_dialogue_context(self, command: FormationDialogueCommand) -> dict:
        """解释 lane 的形成上下文（控 token：清单截 60 条、表达截 40 字、说明截 80 字）。"""
        items = self._items.items_of_parse_result(command.parse_result_ref)
        selected = self._dialogue_item_of(command)
        return {
            "selected_item": (
                {"item_ref": selected.id, "req_no": selected.req_no,
                 "req_type": selected.req_type, "status": selected.status,
                 "expression": selected.expression,
                 "curation_note": (selected.curation_note or "")[:80],
                 "boundary_note": (selected.boundary_note or "")[:80],
                 "verification_method": selected.verification_method or "",
                 "verification_note": (selected.verification_note or "")[:80],
                 "priority": selected.priority or ""}
                if selected else None
            ),
            "pending_items": [
                {"item_ref": i.id, "req_no": i.req_no, "req_type": i.req_type,
                 "status": i.status, "expression": i.expression[:40]}
                for i in items[:60]
            ],
            "selected_element_refs": command.selected_element_refs,
            # P7 §1.2：业务知识依据候选（名称匹配当前条目表达；供 /引用依据 名→id 解析）
            "business_candidates": (
                self._business_knowledge_candidates(command.project_ref, selected.expression)
                if selected is not None else []
            ),
        }

    def _business_knowledge_candidates(self, project_ref: str, expression: str) -> list[dict]:
        """P7 §1.2 业务知识依据推荐：名称（规范化）出现在条目表达中的业务翼确认态要素。

        与 P4 A.2 派生边同一确定性匹配口径：business_rule 名不稳定不做名称匹配（仍可手动引用）；
        名长 <2 跳过（防高频短词误配）。
        """
        biz_types = [
            et.value for et in ElementType
            if knowledge_category_of(et.value) == KnowledgeCategory.BUSINESS.value
            and et.value != ElementType.BUSINESS_RULE.value
        ]
        haystack = normalize_text(expression)
        out: list[dict] = []
        for row in self._source_assets.list_project_elements_by_type(project_ref, biz_types):
            if row.process_status != ES.CONFIRMED.value:
                continue
            name = normalize_element_name(row.content)
            if len(name) >= 2 and name in haystack:
                out.append({"id": row.id, "element_type": row.element_type, "content": row.content})
        return out

    def _dispatch_formation_operation(
        self, command: FormationDialogueCommand, word: Optional[str],
        operation: str, params: dict, _stage: callable,
    ) -> FormationDialogueResult:
        dispatch_key = f"{command.idempotency_key}:dispatch"
        label = _FORMATION_OPERATION_LABELS.get(operation, operation)

        def _clarify(message: str) -> FormationDialogueResult:
            log_event(_COMPONENT, "dialogue.params.invalid", level="WARN",
                      context_ref=command.formation_context_ref or command.parse_result_ref,
                      command_word=word, operation=operation, ok=False)
            return FormationDialogueResult(
                outcome="clarify", command_word=word, operation=operation,
                operation_label=label, message=message,
            )

        def _rejected(message: Optional[str]) -> FormationDialogueResult:
            return FormationDialogueResult(
                outcome="rejected_precheck", command_word=word, operation=operation,
                operation_label=label, message=message,
            )

        def _executed(
            message: Optional[str] = None, created: Optional[list[str]] = None,
            recheck_run: Optional[str] = None, recheck_context: Optional[str] = None,
        ) -> FormationDialogueResult:
            workspace = (
                self.read_item_formation_workspace(command.formation_context_ref)
                if command.formation_context_ref else None
            )
            return FormationDialogueResult(
                outcome="executed", command_word=word, operation=operation,
                operation_label=label, params_echo=params, message=message,
                created_item_refs=created or [], workspace=workspace,
                structure_recheck_run_ref=recheck_run,
                structure_recheck_context_ref=recheck_context,
                next_action=workspace.next_action if workspace else None,
            )

        if operation == "start_itemization":
            scope = str(params.get("scope") or "all")
            refs = list(command.selected_element_refs) if scope == "selected" else []
            if scope == "selected" and not refs:
                return _clarify("上下文没有勾选的要素；请先在区1 勾选，或直接对全部可条目化要素发起。")
            scope_type = (
                ItemizationScopeType.ALL_ELIGIBLE if not refs
                else ItemizationScopeType.SINGLE_ELEMENT if len(refs) == 1
                else ItemizationScopeType.SELECTED_ELEMENTS
            )
            result = self.start_element_itemization_batch(ItemizationBatchCommand(
                project_ref=command.project_ref, parse_result_ref=command.parse_result_ref,
                workspace_version=command.workspace_version, scope_type=scope_type,
                target_element_refs=refs, operator_ref=command.operator_ref,
                idempotency_key=dispatch_key,
            ))
            if result.status == "in_flight":
                # HK-1 复用在途：按队列支返回原批次 run，前端沿用 watchBatchRun 复挂轮询
                return FormationDialogueResult(
                    outcome="queued", command_word=word, operation=operation,
                    operation_label=label, params_echo=params,
                    agent_run_ref=result.agent_run_ref,
                    formation_context_ref=result.formation_context_ref,
                    message="条目化批次执行中：已恢复进度跟踪，完成后自动刷新。",
                )
            if result.status != "submitted":
                return _rejected(result.next_action)
            return FormationDialogueResult(
                outcome="queued", command_word=word, operation=operation,
                operation_label=label, params_echo=params,
                agent_run_ref=result.agent_run_ref,
                formation_context_ref=result.formation_context_ref,
                message="条目化批次已受理，完成后自动刷新待确认条目列表。",
            )

        # 其余操作均针对区5 选中的目标条目
        item = self._dialogue_item_of(command)
        if item is None:
            return _clarify("请先在区5 选中目标条目。")

        if operation in ("revise.req_type", "revise.field"):
            if self._item_service is None:
                raise RejectedTransition("修订承接方未装配，无法应用修订")
            if operation == "revise.req_type":
                field_key = "req_type"
                new_value = str(params.get("new_req_type") or "").strip()
                if not new_value:
                    return _clarify("请写出目标条目类型（如「功能需求」「约束」）。")
            else:
                field_key = str(params.get("field_key") or "expression").strip()
                if field_key not in _REVISABLE_FIELDS:
                    labels = "、".join(ITEM_REVISION_FIELD_LABELS.values())
                    return _clarify(f"不支持修订该字段；可修订字段：{labels}。")
                new_value = str(params.get("new_value") or "").strip()
                if not new_value:
                    return _clarify("请写出「修订为：<完整值>」，或只写修订方向由 AI 起草。")
            result = self._item_service.apply_item_revision(ItemRevisionCommand(
                project_ref=command.project_ref, item_ref=item.id,
                workspace_version=command.workspace_version,
                revision_mode=ItemRevisionMode.MANUAL, field_key=field_key,
                revised_value=new_value, reason=f"对话修订（/{word}）",
                operator_ref=command.operator_ref, idempotency_key=dispatch_key,
            ))
            if result.status != "applied":
                return _rejected(result.next_action)
            return _executed(
                message=result.next_action,
                recheck_run=result.structure_recheck_run_ref,
                recheck_context=result.structure_recheck_context_ref,
            )

        if operation in ("draft.field", "draft.normalize"):
            if operation == "draft.field":
                field_key = str(params.get("field_key") or "expression").strip()
                if field_key != "expression":
                    return _clarify("起草仅支持条目表达；其它字段请用「修订为：<完整值>」直改。")
                intent = str(params.get("instruction") or command.message)
            else:
                intent = self._normalize_intent(item, str(params.get("instruction") or "").strip())
            _stage(AiRequestStage.RUNNING)
            return self._formation_draft(item, intent, command, word=word, operation=operation, params=params)

        if operation == "split.manual":
            if self._item_service is None:
                raise RejectedTransition("条目写权威未装配，无法拆分")
            parts = [p.strip() for p in str(params.get("new_expressions") or "").split("\n") if p.strip()]
            if len(parts) < 2:
                return _clarify("拆分至少需要两条结果（每行一条完整表达）。")
            # 链式自动体检随写方法触发（issue #8 清理债：触发点归写权威，不再散在执行器）
            created, recheck = self._item_service.split_pending_item(
                command.project_ref, item.id, parts,
                command.workspace_version, command.operator_ref,
            )
            return _executed(
                message=f"已拆分为 {len(created)} 条待确认条目，原条目 {item.req_no} 终止。",
                created=created, recheck_run=recheck.run_ref if recheck else None,
                recheck_context=recheck.recheck_context_ref if recheck else None,
            )

        if operation == "merge.manual":
            if self._item_service is None:
                raise RejectedTransition("条目写权威未装配，无法归并")
            refs = [str(r) for r in (params.get("target_item_refs") or [])]
            if item.id not in refs:
                refs.insert(0, item.id)
            new_expression = str(params.get("new_expression") or "").strip()
            if not new_expression:
                return _clarify("请写出「归并后表达：<完整表达>」（归并必填）。")
            merged, recheck = self._item_service.merge_pending_items(
                command.project_ref, refs, new_expression,
                command.workspace_version, command.operator_ref,
            )
            return _executed(
                message=f"已归并 {len(refs)} 条为新的待确认条目，原条目终止。",
                created=[merged], recheck_run=recheck.run_ref if recheck else None,
                recheck_context=recheck.recheck_context_ref if recheck else None,
            )

        if operation == "reference.supporting_basis":
            # P7 §1.2：把业务知识引用为当前条目的支撑依据（复用 P4 create_supporting_basis）。
            # 条目待确认 → 预建立边；随条目确认（评审采纳「建议通过」）由既有机制转有效。
            if self._supporting_basis_writer is None:
                raise RejectedTransition("支撑依据写通道未装配，无法引用依据")
            element_refs = [str(r).strip() for r in (params.get("element_refs") or []) if str(r).strip()]
            if not element_refs:
                return _clarify("请点名要引用的业务知识（术语/业务规则/角色/外部系统）。")
            statuses: list[str] = []
            for element_ref in element_refs:
                res = self._supporting_basis_writer(
                    command.project_ref, element_ref, item.id, command.operator_ref,
                )
                statuses.append(getattr(res, "status", "pre_established"))
            effective = statuses.count("effective")
            pre = statuses.count("pre_established")
            parts = []
            if pre:
                parts.append(f"{pre} 条预建立（随条目确认转有效）")
            if effective:
                parts.append(f"{effective} 条有效")
            return _executed(
                message=f"已引用 {len(element_refs)} 条业务知识为支撑依据：" + "、".join(parts) + "。",
            )

        raise InvalidInput(f"不支持的对话操作：{operation}")

    def _item_sources_brief(self, item: ItemRow) -> list[dict]:
        sources: list[dict] = []
        for ref in json.loads(item.source_element_refs or "[]"):
            element = self._source_assets.get_element(ref)
            if element is not None:
                sources.append({"id": element.id, "content": element.content})
        return sources

    def _candidate_expression_suggestion(self, item_ref: str):
        """在途候选建议（field=expression）：新稿替代旧稿的迭代锚。"""
        candidates = [
            s for s in self._formation_process.suggestions_of_items([item_ref])
            if s.status == "candidate" and s.field_key == "expression"
        ]
        return candidates[-1] if candidates else None

    def _item_convention(self, item: ItemRow) -> str:
        """条目判定所依据的规约方案（口径锚；缺陷 2 解析链）。

        既有投影记录的 convention_key（形成时固定，切换不回溯）→ 条目继承的
        formation_context_ref 所在批次固定方案（拆分/归并条目无投影时的正解）→
        AEP-102 当前生效方案 → 默认方案。禁止无条件硬编码回退。
        """
        rows = self._process_records.item_structure_projections_of([item.id]).get(item.id) or []
        for r in rows:
            if r.convention_key:
                return r.convention_key
        if item.formation_context_ref:
            req = self._formation_process.get_formation_request(item.formation_context_ref)
            if req is not None and req.convention_key:
                return req.convention_key
        return self._active_convention_resolver() or DEFAULT_CONVENTION

    def _normalize_intent(self, item: ItemRow, extra: str) -> str:
        """规范化 = 按条目类型陈述档案起草（档案是唯一句式来源，不手写要求）。"""
        try:
            type_label = REQUIREMENT_ITEM_TYPE_LABELS[RequirementItemType(item.req_type)]
        except ValueError:
            type_label = item.req_type
        profile = get_profile(item.req_type, self._item_convention(item))
        pattern = f"，句式：{profile.statement_pattern}" if profile is not None else ""
        base = (
            f"按{type_label}条目陈述档案规范化当前表达{pattern}；"
            "保持需求含义不变，不新增来源之外的事实。"
        )
        return f"{base}补充要求：{extra}" if extra else base

    def _draft_structure_context(self, item: ItemRow) -> Optional[dict]:
        """起草请求随附的结构体检上下文：句式模板＋逐条待补成分的判定原因与补写示例。

        与区4 体检报告同源同口径（同一个 _structure_reviews_of）。此前起草只拿到条目四字段
        与用户那句意图，界面上已经算好并展示给用户的判定原因、补写示例、句式模板一样都没进
        提示词，模型只能凭一个成分名自行推导（2026-07-20 走查反馈第⑦组）。

        投影过期（条目内容改过、尚未重新体检）时返回 None 不注入——过期的判定说的是旧版本
        内容，喂给模型只会误导；这与区4 收起旧报告是同一个口径。
        """
        review = self._structure_reviews_of([item]).get(item.id)
        if review is None or review.stale:
            return None
        gaps = [
            {
                "成分": f.label,
                "判定": f.status,
                "判定原因": f.note or "",
                "补写示例": f.revision_hint or "",
            }
            for f in review.facets
            if f.status in ("missing", "ambiguous")
        ]
        profile = get_profile(item.req_type, self._item_convention(item))
        context: dict = {"待补成分": gaps}
        if profile is not None and profile.statement_pattern:
            context["句式模板"] = profile.statement_pattern
        # 既无待补成分也无句式模板时不必注入（模板会渲染成「无」）
        return context if (gaps or context.get("句式模板")) else None

    def _formation_draft(
        self, item: ItemRow, intent: str, command: FormationDialogueCommand,
        word: Optional[str], operation: str, params: dict,
    ) -> FormationDialogueResult:
        label = _FORMATION_OPERATION_LABELS.get(operation, operation)
        if self._draft_composer is None:
            raise InvalidInput("草案起草能力未装配")

        def _explain_only(text: str, next_action: Optional[str]) -> FormationDialogueResult:
            return FormationDialogueResult(
                outcome="explanation", command_word=word, operation=operation,
                operation_label=label, explanation=text, next_action=next_action,
            )

        current = self._candidate_expression_suggestion(item.id)
        outcome = self._draft_composer.compose(
            {"item_ref": item.id, "req_no": item.req_no,
             "expression": item.expression, "req_type": item.req_type},
            self._item_sources_brief(item), intent,
            current.proposed_value if current else None,
            self._draft_structure_context(item),
        )
        if outcome.failed:
            return _explain_only("建议起草服务不可用，请稍后重试或使用字段修订。", "重试或字段修订")
        if not outcome.proposed_value:
            # cannot_comply 拒绝通道：模型判断该意图无法起草为修订建议，原因直接回给用户
            return _explain_only(
                outcome.reason or "AI 判断该意图无法起草为修订建议。",
                "调整意图后重试，或使用字段修订",
            )
        if current is not None:
            # 新稿替代旧稿：旧候选过期（原位迭代）
            self._formation_process.set_suggestion_status(current.id, "expired")
        message_ref = self._model_results.record_stage_payload(
            _STAGE_DRAFT, item.id, "drafted",
            json.dumps({
                "item_ref": item.id, "proposed_value": outcome.proposed_value,
                "note": outcome.note, "user_message": intent, "at": _now_iso(),
                # 来源页面：本页与条目评审页共用同一个阶段键，载荷不标来源则评审页读投影
                # 无从区分，本页的交换会混进那边的对话历史（2026-07-20 用户报障）。
                "origin": "formation",
            }, ensure_ascii=False),
            "形成页对话修订建议（未采纳零副作用）",
        )
        reason = (
            "规范化建议（按条目陈述档案起草）" if operation == "draft.normalize"
            else "对话建议（由用户意见起草）"
        )
        if outcome.note:
            reason = f"{reason}；{outcome.note}"
        suggestion_ref = self._formation_process.save_suggestion(
            item.id, "expression", outcome.proposed_value, reason, message_ref,
        )
        log_event(_COMPONENT, "dialogue.drafted", item_ref=item.id,
                  suggestion_ref=suggestion_ref, ok=True)
        # 建议生成时刻：区5 建议卡按此时刻排进时间线（写入即读回，避免前端用本地时钟伪造）
        saved_suggestion = self._formation_process.get_suggestion(suggestion_ref)
        workspace = (
            self.read_item_formation_workspace(command.formation_context_ref)
            if command.formation_context_ref else None
        )
        return FormationDialogueResult(
            outcome="draft", command_word=word, operation=operation,
            operation_label=label, params_echo=params,
            suggestion=ItemRevisionSuggestionRead(
                suggestion_ref=suggestion_ref, item_ref=item.id, field_key="expression",
                proposed_value=outcome.proposed_value, reason=reason, status="candidate",
                created_at=saved_suggestion.created_at if saved_suggestion else None,
            ),
            workspace=workspace,
            next_action="采纳建议将应用字段修订；拒绝零副作用。",
        )

    def _formation_explain(
        self, item: ItemRow, message: str, command: FormationDialogueCommand,
    ) -> FormationDialogueResult:
        if self._explainer is None:
            raise InvalidInput("解释能力未装配")
        context = {
            "formation_basis": ("模型格式化建议" if item.formation_basis_ref else "人工形成（拆分/归并）"),
            "source_elements": self._item_sources_brief(item),
            "curation_note": item.curation_note or "",
            "boundary_note": item.boundary_note or "",
        }
        text = self._explainer.explain(
            {"item_ref": item.id, "req_no": item.req_no,
             "expression": item.expression, "req_type": item.req_type},
            context, message,
        )
        if not text:
            return FormationDialogueResult(
                outcome="explanation", explanation="解释服务不可用，请稍后重试。",
            )
        self._model_results.record_stage_payload(
            _STAGE_EXPLAIN, item.id, "explained",
            json.dumps({"item_ref": item.id, "user_message": message,
                        "explanation": text, "at": _now_iso()}, ensure_ascii=False),
            "形成解释（不改条目）",
        )
        return FormationDialogueResult(
            outcome="explanation", explanation=text,
            next_action="解释不改变条目；需要修改可用 /修订 或直接写修订要求。",
        )

    def _explain_item_source(self, command: FormationDialogueCommand) -> FormationDialogueResult:
        """确定性来源指认（/问来源）：来源要素 + 原文锚点逐字引文 + 形成依据，不调模型。"""
        item = self._dialogue_item_of(command)
        if item is None:
            return FormationDialogueResult(
                outcome="clarify", command_word="问来源",
                operation="explain.source",
                operation_label=_FORMATION_OPERATION_LABELS["explain.source"],
                message="请先在区5 选中目标条目。",
            )
        lines: list[str] = [f"{item.req_no} 的来源指认："]
        for ref in json.loads(item.source_element_refs or "[]"):
            element = self._source_assets.get_element(ref)
            if element is None:
                lines.append(f"- 来源要素 {ref}：已不在当前集合")
                continue
            try:
                type_label = ELEMENT_TYPE_LABELS[ElementType(element.element_type)]
            except ValueError:
                type_label = element.element_type
            entry = f"- 来源要素（{type_label}）：{element.content}"
            exacts = anchor_quotes(element.source_anchor)
            if exacts:
                quoted = "」「".join(e[:60] for e in exacts[:3])
                entry += f"；原文锚点：「{quoted}」"
            lines.append(entry)
        lines.append(
            "- 形成依据：" + ("模型格式化建议（经服务裁定后写入）" if item.formation_basis_ref
                             else "人工形成（拆分/归并）")
        )
        explanation = "\n".join(lines)
        self._model_results.record_stage_payload(
            _STAGE_EXPLAIN, item.id, "explained",
            json.dumps({"item_ref": item.id, "user_message": command.message,
                        "explanation": explanation, "at": _now_iso()}, ensure_ascii=False),
            "来源指认（确定性，不调模型）",
        )
        return FormationDialogueResult(
            outcome="explanation", command_word="问来源", operation="explain.source",
            operation_label=_FORMATION_OPERATION_LABELS["explain.source"],
            explanation=explanation,
        )

    # ------------------------------------------------------------------
    # 工作区读视图（五区同一 workspace_version）
    # ------------------------------------------------------------------

    def read_latest_workspace_of_parse_result(
        self, parse_result_ref: str
    ) -> ItemFormationWorkspaceRead:
        """回放该解析结果最近一次批次的形成工作区（进入形成页时找回既有待确认条目）。"""
        formation_context = self._formation_process.latest_formation_of_parse_result(
            parse_result_ref
        )
        if formation_context is None:
            raise NotFound("该解析结果尚未发起条目化批次")
        return self.read_item_formation_workspace(formation_context)

    def read_item_formation_workspace(
        self, formation_context_ref: str
    ) -> ItemFormationWorkspaceRead:
        req = self._formation_process.get_formation_request(formation_context_ref)
        if req is None:
            raise NotFound("条目化批次上下文不存在")

        version = str(self._process_records.read_workspace_version(req.parse_context_ref))
        canvas = self._build_canvas(req.parse_context_ref)
        raw_text = canvas.raw_text if canvas else ""

        eligible: list[RequirementElementRead] = []
        blocked: list[BlockedElementRead] = []
        intent_context: list[RequirementElementRead] = []  # P7：确认态 goal/scenario 只读意图组
        for row in self._source_assets.elements_of(req.parse_result_ref):
            if row.superseded:
                continue
            projected = self._project_element(row)
            if row.element_type not in _ELEMENT_TO_ITEM_TYPE:
                if row.process_status == ES.REVOKED.value:
                    continue  # 已撤销支撑要素不呈现
                # P7 §1.1 意图背景：确认态 goal/scenario（需求翼不可条目化）作只读组，
                # 只读、不入批次、不建边（意图链缺失为独立后续项）；不进 blocked 支撑列。
                if (row.process_status == ES.CONFIRMED.value
                        and knowledge_category_of(row.element_type)
                        == KnowledgeCategory.REQUIREMENT.value):
                    intent_context.append(projected)
                    continue
                blocked.append(BlockedElementRead(
                    **projected.model_dump(), formation_role="supporting",
                    blocked_reason="支撑或上下文类要素仅作为依据",
                ))
            elif row.process_status != ES.CONFIRMED.value:
                reason = ("已撤销要素不参与条目形成"
                          if row.process_status == ES.REVOKED.value
                          else "要素未确认：请回需求分析确认或校正")
                blocked.append(BlockedElementRead(
                    **projected.model_dump(), formation_role="blocked", blocked_reason=reason,
                ))
            elif not _anchor_resolvable(row, raw_text):
                blocked.append(BlockedElementRead(
                    **projected.model_dump(), formation_role="blocked",
                    blocked_reason="来源锚点缺失或无法回到原文",
                ))
            else:
                eligible.append(projected)

        items = self._items.items_of_parse_result(req.parse_result_ref)
        structure_reviews = self._structure_reviews_of(items)
        pending_items = [self._project_item(i, structure_reviews.get(i.id)) for i in items]
        outcomes = self._formation_process.outcomes_of(formation_context_ref)
        batch_results = [
            ItemizationResultRead(
                element_ref=o.element_ref, result_status=o.result_status,
                item_ref=o.item_ref, formation_basis_ref=o.formation_basis_ref,
                reason=o.reason, next_action=o.next_action,
            )
            for o in outcomes
        ]
        suggestions = [
            ItemRevisionSuggestionRead(
                suggestion_ref=s.id, item_ref=s.item_ref, field_key=s.field_key,
                proposed_value=s.proposed_value, reason=s.reason, status=s.status,
                created_at=s.created_at,
            )
            for s in self._formation_process.suggestions_of_items([i.id for i in items])
        ]

        has_pending = any(i.status == IS.PENDING_CONFIRMATION.value for i in items)
        has_failed = any(o.result_status == IR.FAILED.value for o in outcomes)
        has_candidate = any(s.status == "candidate" for s in suggestions)
        itemized = self._itemized_element_refs(req.parse_result_ref)
        formable = [e for e in eligible if e.id not in itemized]

        next_action = req.stop_next_action
        if next_action is None and not pending_items and not formable:
            next_action = "没有适合条目形成的需求表达类要素：请回需求分析确认或校正"

        # 默认选中：优先第一条待确认（拆分/归并后列表首位可能是已终止条目）
        default_selected = next(
            (i.item_ref for i in pending_items if i.status == IS.PENDING_CONFIRMATION),
            pending_items[0].item_ref if pending_items else None,
        )
        return ItemFormationWorkspaceRead(
            formation_context_ref=formation_context_ref,
            parse_result_ref=req.parse_result_ref,
            workspace_version=version,
            # 区2 只读徽标：本批次固定的生效规约方案名（切换唯一入口=设置页）。
            convention_key=req.convention_key,
            convention_display_name=convention_display_name(req.convention_key),
            material_canvas=canvas,
            eligible_elements=eligible,
            blocked_elements=blocked,
            intent_context=intent_context,
            pending_items=pending_items,
            selected_item_ref=default_selected,
            batch_results=batch_results,
            revision_suggestions=suggestions,
            available_actions=[
                ActionFact(key="start_review", enabled=has_pending,
                           disabled_reason=None if has_pending else "尚未形成待确认条目"),
                ActionFact(key="return_to_elements", enabled=True),
                ActionFact(key="retry_itemization", enabled=has_failed,
                           disabled_reason=None if has_failed else "没有失败停靠项"),
            ],
            available_operations=[
                ActionFact(key="start_itemization", enabled=bool(formable),
                           disabled_reason=None if formable else "没有可条目化的已确认需求表达类要素"),
                ActionFact(key="apply_revision", enabled=has_pending,
                           disabled_reason=None if has_pending else "尚未形成待确认条目"),
                ActionFact(key="accept_revision_suggestion", enabled=has_candidate,
                           disabled_reason=None if has_candidate else "没有候选修订建议"),
            ],
            next_action=next_action,
        )

    # ---- 投影与装配 ----

    def _project_element(self, r: ElementRow) -> RequirementElementRead:
        return project_element(r)

    def _write_structure_projection(
        self, item_ref: str, entry: dict, model_result_ref: str, convention_key: str,
        item_content_rev: int,
    ) -> None:
        """结构判定 → 投影表（增补 §3；仅形成/结构复核链路从已登记 LDM-015 写入）。

        convention_key 为判定所依据的规约方案（口径锚）；徽章按本列渲染，方案切换不追溯（选型文档 §5）。
        item_content_rev 为版本锚：形成时恒 1；结构复核锚定当前内容修订序号（AEP-114）。
        """
        profile_version = entry.get("profile_version")
        raw_facets = entry.get("facet_findings")
        if not isinstance(profile_version, int) or not isinstance(raw_facets, list) or not raw_facets:
            return  # 无档案类型/降级批次：不落投影（既有投影原样保留，区4 沿现状渲染）
        conformance = entry.get("statement_conformance")
        completeness = entry.get("completeness")
        rows: list[ItemStructureProjectionRow] = []
        for fr in raw_facets:
            if not isinstance(fr, dict) or not fr.get("facet"):
                continue
            rows.append(ItemStructureProjectionRow(
                item_ref=item_ref, item_content_rev=item_content_rev, profile_version=profile_version,
                convention_key=convention_key,
                row_kind="facet", key=str(fr["facet"]),
                facet_status=str(fr.get("status") or "") or None,
                evidence=fr.get("evidence"), note=fr.get("note"),
                statement_conformance=conformance, completeness=completeness,
                model_result_ref=model_result_ref,
            ))
        for pv in entry.get("payload_values") or []:
            if not isinstance(pv, dict) or not pv.get("field"):
                continue
            rows.append(ItemStructureProjectionRow(
                item_ref=item_ref, item_content_rev=item_content_rev, profile_version=profile_version,
                convention_key=convention_key,
                row_kind="field", key=str(pv["field"]), value_text=pv.get("value"),
                statement_conformance=conformance, completeness=completeness,
                model_result_ref=model_result_ref,
            ))
        if rows:
            self._process_records.replace_item_structure_projection(item_ref, rows)
            log_event(_COMPONENT, "item.projection.convention_recorded", item_ref=item_ref,
                      convention_key=convention_key, profile_version=profile_version,
                      item_content_rev=item_content_rev)

    def _structure_reviews_of(self, items: list[ItemRow]) -> dict[str, ItemStructureReviewRead]:
        """陈述达标投影读路径（P2：读 process_item_structure_projection）。

        非权威、可整层重算：仅供区4 徽章、区5 达标度筛选与提示，不参与状态迁移或门禁。
        条目内容修订序号与投影 item_content_rev 不一致 → stale（达标待重诊）。
        """
        projections = self._process_records.item_structure_projections_of([i.id for i in items])
        type_of = {i.id: i.req_type for i in items}
        out: dict[str, ItemStructureReviewRead] = {}
        for ref, prows in projections.items():
            facet_rows = [p for p in prows if p.row_kind == "facet" and p.facet_status]
            if not facet_rows:
                continue
            head = facet_rows[0]
            # 徽章口径按投影记录的方案取档案（方案切换不追溯，旧投影仍按其原方案渲染；选型文档 §5）。
            recorded_convention = head.convention_key or DEFAULT_CONVENTION
            profile = get_profile(type_of.get(ref, ""), recorded_convention)
            facets: list[ElementFacetFindingRead] = []
            for p in facet_rows:
                spec = profile.facet(p.key) if profile is not None else None
                facets.append(ElementFacetFindingRead(
                    facet_key=p.key,
                    label=spec.label if spec else p.key,
                    required=spec.required if spec else False,
                    status=p.facet_status or "",
                    evidence=p.evidence,
                    note=p.note,
                    revision_hint=(
                        spec.revision_hint
                        if spec and p.facet_status not in ("present", "not_applicable")
                        else None
                    ),
                ))
            current_rev = content_revision_seq(self._items.revisions_of(ref))
            out[ref] = ItemStructureReviewRead(
                profile_version=head.profile_version,
                convention_key=recorded_convention,
                statement_conformance=head.statement_conformance,
                completeness=head.completeness,
                facets=facets,
                # 过期仅由内容修订序号错位触发；方案切换不判过期（切换不回溯）。
                # 用 > 而非 !=：锚是持久快照，计数规则变更（如背书行排除）可使现算值回退到快照之下，
                # 那不是内容变更；修订记录只追加、现算序号只增不减，真实内容变更只会使现算值大于快照
                # （与 :1015 CAS 侧的 > 口径一致）。
                stale=current_rev > head.item_content_rev,
            )
        return out

    def _project_item(
        self, row: ItemRow, structure_review: Optional[ItemStructureReviewRead] = None
    ) -> PendingRequirementItemRead:
        pending = row.status == IS.PENDING_CONFIRMATION.value
        return PendingRequirementItemRead(
            item_ref=row.id, req_no=row.req_no, expression=row.expression,
            req_type=row.req_type, status=row.status, version_no=row.version_no,
            source_element_refs=list(json.loads(row.source_element_refs or "[]")),
            formation_basis_ref=row.formation_basis_ref,
            curation_note=row.curation_note,
            boundary_note=row.boundary_note,
            verification_method=split_verification_methods(row.verification_method),
            verification_note=row.verification_note,
            priority=row.priority,
            structure_review=structure_review,
            revision_records=[
                ItemRevisionRecordRead(
                    record_ref=rev.id, field_key=rev.field_key,
                    before_value=rev.before_value, after_value=rev.after_value,
                    revision_mode=rev.revision_mode, operator_ref=rev.operator_ref,
                    reason=rev.reason, created_at=rev.at,
                )
                for rev in self._items.revisions_of(row.id)
            ],
            available_actions=[
                ActionFact(key="apply_revision", enabled=pending,
                           disabled_reason=None if pending else "仅待确认条目可字段修订"),
                ActionFact(key="enter_review", enabled=pending,
                           disabled_reason=None if pending else "条目已离开待确认"),
            ],
        )

    def _itemized_element_refs(self, parse_result_ref: str) -> set[str]:
        refs: set[str] = set()
        for item in self._items.items_of_parse_result(parse_result_ref):
            refs.update(json.loads(item.source_element_refs or "[]"))
        return refs

    def _material_raw_text(self, parse_context_ref: str) -> str:
        return material_raw_text(self._process_records, self._source_assets, parse_context_ref)

    def _cached_material_raw_text(self, parse_context_ref: str) -> str:
        """批次执行内材料原文按上下文缓存（issue #8 清理债：复核批次逐条重读全文）。"""
        cached = self._raw_text_cache.get(parse_context_ref)
        if cached is None:
            cached = self._material_raw_text(parse_context_ref)
            self._raw_text_cache[parse_context_ref] = cached
        return cached

    def _build_canvas(self, parse_context_ref: str) -> Optional[MaterialCanvasRead]:
        return build_material_canvas(self._process_records, self._source_assets, parse_context_ref)


class RequirementItemService:
    """需求条目服务（AEP-036 applyItemRevision 待确认分支 + SCN-003-P03 旧诊断轮次失效标记）。"""

    def __init__(
        self,
        items: RequirementItemRepository,
        formation_process: ItemFormationProcessRepository,
        process_records: ProcessRecordRepository,
        source_assets: SourceAssetRepository,
        reviews: Optional["ItemReviewRepository"] = None,
        model_results: Optional[ModelResultRepository] = None,
        events: Optional[DomainEventPublisher] = None,
        on_content_changed_recheck: Optional[
            Callable[[list[str]], Optional["ChainedRecheckDispatch"]]
        ] = None,
    ) -> None:
        self._items = items
        self._formation_process = formation_process
        self._process_records = process_records
        self._source_assets = source_assets
        self._reviews = reviews  # SCN-003：条目修订后标记旧 LDM-009 诊断轮次失效（未装配时跳过）
        self._model_results = model_results  # 采纳结论明细回写（未装配时跳过）
        # 阶段策略解耦 P1：对象层只报告事实——修订落库后发布 ItemRevised 领域事件；
        # 链式复诊等阶段后果由各阶段应用层在自己拥有的动作上续接（评审采纳→_adopt_revise），
        # 不再由本共享服务经 on_revised 直连钩子无差别触发。未注入发布器时发布为空操作。
        self._events = events or DomainEventPublisher()
        # 走查第三轮裁定：内容修订后自动结构体检（装配层注入形成服务 dispatch_chained_recheck；
        # 未装配时跳过——「修订后未复核」不再是用户可见状态，判定在途瞬态收敛）。
        # 本期（P1）结构体检链原样保留，迁回形成动作属 P2。
        self.on_content_changed_recheck = on_content_changed_recheck

    def _normalize_source_element_refs(self, item: ItemRow, raw: str) -> str:
        """来源登记（issue #30 出口）：校验并规范化来源要素引用清单。

        门禁与条目形成侧同口径——仅接受属于本条目同一解析批次
        （parse_result_ref 一致）、未被替代、且处于已确认状态的要素 id；
        任一 id 不合法即整体拒绝，不落半成品修订记录（校验先于唯一写入点
        apply_item_field）。规范化＝去重＋升序，与 before 值同口径便于内容变更判定。
        空集拒绝：登记后集合不得为空。
        """
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            raise InvalidInput("来源要素引用需为 JSON 数组字符串（如 [\"<要素 id>\"]）")
        if not isinstance(parsed, list) or any(not isinstance(r, str) for r in parsed):
            raise InvalidInput("来源要素引用需为字符串 id 的 JSON 数组")
        refs = sorted(set(parsed))
        if not refs:
            raise InvalidInput("来源要素不能为空（登记后至少保留一个同批次已确认要素）")
        batch = {row.id: row for row in self._source_assets.elements_of(item.parse_result_ref)}
        for ref in refs:
            row = batch.get(ref)
            if row is None:
                raise InvalidInput(f"要素 {ref} 不属于本条目所在的解析批次或不存在")
            if row.superseded:
                raise InvalidInput(f"要素 {ref} 已被替代版本取代，不能作为条目来源")
            if row.process_status != ES.CONFIRMED.value:
                raise InvalidInput(f"要素 {ref} 未确认，不能作为条目来源")
        return json.dumps(refs, ensure_ascii=False)

    def apply_item_revision(
        self, command: ItemRevisionCommand, *, origin: str = "direct",
    ) -> ItemRevisionResult:
        # origin：修订发起来源，随 ItemRevised 事件外发（评审采纳传 "review_adoption"，
        # 其余直发路径取缺省 "direct"）。P1 事件无功能订阅者，链式复诊由评审采纳动作显式续接。
        replay = self._items.find_revision_by_idempotency(command.idempotency_key)
        if replay is not None:  # 幂等重放：返回原修订，不重复修订
            return ItemRevisionResult(
                status="applied", item_ref=replay.item_ref,
                workspace_version=self._version_of_item(replay.item_ref) or "1",
                revision_record_ref=replay.id,
                next_action="修订已应用（幂等重放）：条目仍为待确认，需进入评审或重新诊断",
            )

        item = self._items.get_item(command.item_ref)
        if item is None:
            raise NotFound("需求条目不存在")

        # 状态机默认拒绝：仅待确认条目可走 AEP-036 自环（确认态不可原地修改，VAL-001）
        nxt = item_transition(ItemState(item.status), ItemEvent.REVISE)
        assert nxt is ItemState.PENDING_CONFIRMATION

        current = self._version_of_item(item.id)
        if current is not None and command.workspace_version != current:
            return ItemRevisionResult(
                status="rejected_precheck", item_ref=item.id, workspace_version=current,
                next_action="工作区已更新（版本不一致），请刷新后重试",
            )

        if command.field_key not in _REVISABLE_FIELDS:
            raise InvalidInput(
                f"字段 {command.field_key} 不可修订（本切片仅 {'/'.join(_REVISABLE_FIELDS)}）"
            )

        mode = command.revision_mode

        if mode == ItemRevisionMode.REJECT_SUGGESTION:
            # 拒绝建议：只更新建议处置，不改条目字段
            suggestion = self._require_candidate_suggestion(command, item.id)
            self._formation_process.set_suggestion_status(suggestion.id, "rejected")
            self._record_suggestion_adoption(item, suggestion, "rejected", command)
            before = _item_field_value(item, command.field_key)
            record_ref = self._items.record_item_revision(
                item.id, command.field_key, before, before, mode.value,
                suggestion.id, command.reason, command.operator_ref, command.idempotency_key,
            )
            log_event(_COMPONENT, "item.revision.suggestion_rejected",
                      item_ref=item.id, ok=True)
            return ItemRevisionResult(
                status="applied", item_ref=item.id,
                workspace_version=current or command.workspace_version,
                revision_record_ref=record_ref,
                next_action="修订建议已拒绝；条目字段保持不变",
            )

        # manual / accept_suggestion / revise_and_accept_suggestion → 确定修订后值
        suggestion = None
        if mode == ItemRevisionMode.MANUAL:
            new_value = (command.revised_value or "").strip()
        elif mode == ItemRevisionMode.ACCEPT_SUGGESTION:
            suggestion = self._require_candidate_suggestion(command, item.id)
            new_value = suggestion.proposed_value
        elif mode == ItemRevisionMode.REVISE_AND_ACCEPT_SUGGESTION:
            suggestion = self._require_candidate_suggestion(command, item.id)
            new_value = (command.revised_value or "").strip()
        else:
            raise InvalidInput(f"不支持的修订方式：{mode}")

        if not new_value:
            raise InvalidInput("修订后字段值不能为空")
        if command.field_key == "req_type" and new_value not in _ITEM_TYPE_CODES:
            raise InvalidInput(f"不支持的需求条目类型：{new_value}")
        if command.field_key == "verification_method":
            new_value = normalize_verification_method(new_value)
        if command.field_key == "priority" and new_value not in _PRIORITY_CODES:
            raise InvalidInput(
                f"不支持的条目优先级：{new_value}（可选 {'/'.join(p.value for p in ItemPriority)}）"
            )
        if command.field_key == "source_element_refs":
            new_value = self._normalize_source_element_refs(item, new_value)

        before = _item_field_value(item, command.field_key)
        self._items.apply_item_field(item.id, command.field_key, new_value)
        record_ref = self._items.record_item_revision(
            item.id, command.field_key, before, new_value, mode.value,
            suggestion.id if suggestion else None, command.reason,
            command.operator_ref, command.idempotency_key,
            selected_point_refs=(
                json.dumps(command.selected_point_refs, ensure_ascii=False)
                if command.selected_point_refs else None
            ),
        )
        if suggestion is not None:
            self._formation_process.set_suggestion_status(suggestion.id, "accepted")
            self._record_suggestion_adoption(
                item, suggestion,
                "adopted" if mode == ItemRevisionMode.ACCEPT_SUGGESTION else "adopted_with_revision",
                command,
            )

        # 修订后仍待确认；内容变更使旧诊断轮次失效（SCN-003-P03-N07），必须回 P01 增量诊断；
        # 属性字段（验证方式/验收准则/优先级）留痕但不失效诊断、不触发链式增量
        content_changed = new_value != before and command.field_key not in _ATTRIBUTE_FIELDS
        if self._reviews is not None and content_changed:
            self._record_superseded_findings(item, command)
            self._reviews.invalidate_rounds_of_item(
                item.id, "条目已修订，旧诊断轮次失效，需增量诊断"
            )
        parse_context = self._source_assets.parse_context_of(item.parse_result_ref)
        new_version = (
            str(self._process_records.bump_workspace_version(parse_context))
            if parse_context else command.workspace_version
        )
        log_event(_COMPONENT, "item.status.transition", item_ref=item.id,
                  from_status=item.status, to_status=IS.PENDING_CONFIRMATION.value,
                  sm_event=ItemEvent.REVISE.value, field_key=command.field_key, ok=True)
        # 阶段策略解耦 P1：对象层只报告事实——内容修订落库后发布 ItemRevised 领域事件，
        # 不再由本共享服务判定链式复诊后果。链式增量诊断改由评审服务在裁决采纳动作内显式续接
        # （_adopt_revise，origin="review_adoption"）；形成页/评审页对话/AEP-036 直发均不再链。
        # 故本方法返回的 agent_run_ref 恒为空（直发路径不触发诊断），前端点灯以评审采纳回执为准。
        if content_changed:
            self._events.publish(ItemRevised(
                item_ref=item.id, revision_ref=record_ref, origin=origin,
            ))
        # 走查第三轮裁定：内容修订后自动结构体检——真判定秒级刷新投影，
        # 「修订后未复核」不再作为用户可见状态存在（派发失败独立捕获＋持久通知，不阻断修订）。
        # 结构体检链本期（P1）原样保留，迁回形成动作属 P2。
        recheck_run_ref = None
        recheck_context_ref = None
        if self.on_content_changed_recheck is not None and content_changed:
            recheck = self.on_content_changed_recheck([item.id])
            if recheck is not None:
                recheck_run_ref = recheck.run_ref
                recheck_context_ref = recheck.recheck_context_ref
        recheck_tail = "；已自动发起结构体检刷新达标判定" if recheck_run_ref else ""
        if not content_changed:
            if new_value != before:
                applied_note = "修订已应用：属性字段不触发重新诊断"
            else:
                applied_note = "修订已应用：条目仍为待确认，需重新诊断"
        else:
            # 对象层不再自动发起链式增量诊断（阶段策略已迁回评审采纳动作）：
            # 只陈述真发生的事——旧结论随版本失效、结构体检是否已发起。
            applied_note = f"修订已应用：旧结论随版本失效{recheck_tail}"
        return ItemRevisionResult(
            status="applied", item_ref=item.id, workspace_version=new_version,
            revision_record_ref=record_ref, agent_run_ref=None,
            structure_recheck_run_ref=recheck_run_ref,
            structure_recheck_context_ref=recheck_context_ref,
            next_action=applied_note,
        )

    # ------------------------------------------------------------------
    # 拆分 / 归并（仅经 AEP-097 对话派发可达；FORM+TERMINATE 复合，无新迁移）
    # ------------------------------------------------------------------

    def split_pending_item(
        self, project_ref: str, item_ref: str, new_expressions: list[str],
        workspace_version: str, operator_ref: str,
    ) -> tuple[list[str], Optional["ChainedRecheckDispatch"]]:
        """拆分：FORM×N 人工待确认条目（继承来源集合）+ TERMINATE 原条目。

        重放安全：原条目终止后再次拆分被状态机默认拒绝（terminated 不接受 TERMINATE）。
        返回（新条目 refs, 链式体检派发）——链式自动体检随写方法触发（issue #8 清理债：
        触发点归写权威；新条目无体检结果，失败不阻断拆分）。
        """
        item = self._items.get_item(item_ref)
        if item is None:
            raise NotFound("需求条目不存在")
        parts = [p.strip() for p in new_expressions if p.strip()]
        if len(parts) < 2:
            raise InvalidInput("拆分至少需要两条结果（每行一条完整表达）")
        nxt = item_transition(ItemState(item.status), ItemEvent.TERMINATE)
        assert nxt is ItemState.TERMINATED
        current = self._version_of_item(item.id)
        if current is not None and workspace_version != current:
            raise RejectedTransition("工作区已更新（版本不一致），请刷新后重试")

        created: list[str] = []
        for expression in parts:
            nxt_new = item_transition(ItemState.INITIAL, ItemEvent.FORM)
            assert nxt_new is ItemState.PENDING_CONFIRMATION
            req_no = f"REQ-{self._items.max_req_seq_of_project(project_ref) + 1:03d}"
            new_ref = self._items.create_pending_item(
                project_ref, item.parse_result_ref, item.formation_context_ref,
                req_no, expression, item.req_type,
                item.source_element_refs, None,  # 人工形成：无模型形成依据
                curation_note=f"由 {item.req_no} 拆分",
                boundary_note=item.boundary_note,
            )
            created.append(new_ref)
            log_event(_COMPONENT, "item.status.transition", item_ref=new_ref,
                      from_status="initial", to_status=IS.PENDING_CONFIRMATION.value,
                      sm_event=ItemEvent.FORM.value, operator_ref=operator_ref, ok=True)
        self._items.set_item_status(item.id, IS.TERMINATED.value)
        log_event(_COMPONENT, "item.status.transition", item_ref=item.id,
                  from_status=IS.PENDING_CONFIRMATION.value, to_status=IS.TERMINATED.value,
                  sm_event=ItemEvent.TERMINATE.value, operator_ref=operator_ref,
                  reject_reason=f"拆分为 {len(parts)} 条", ok=True)
        parse_context = self._source_assets.parse_context_of(item.parse_result_ref)
        if parse_context is not None:
            self._process_records.bump_workspace_version(parse_context)
        log_event(_COMPONENT, "item.split.applied", item_ref=item.id,
                  created_count=len(created), ok=True)
        recheck = (
            self.on_content_changed_recheck(created)
            if self.on_content_changed_recheck is not None else None
        )
        return created, recheck

    def merge_pending_items(
        self, project_ref: str, target_item_refs: list[str], new_expression: str,
        workspace_version: str, operator_ref: str,
    ) -> tuple[str, Optional["ChainedRecheckDispatch"]]:
        """归并：FORM×1（来源集合取并）+ TERMINATE 组内各条目；归并后表达必填。

        返回（归并条目 ref, 链式体检派发）——触发点归写权威，同拆分。
        """
        refs = list(dict.fromkeys(target_item_refs))
        if len(refs) < 2:
            raise InvalidInput("归并需要至少两条待确认条目")
        expression = new_expression.strip()
        if not expression:
            raise InvalidInput("归并必须给出归并后完整表达")
        items: list[ItemRow] = []
        for ref in refs:
            item = self._items.get_item(ref)
            if item is None:
                raise NotFound(f"需求条目不存在：{ref}")
            nxt = item_transition(ItemState(item.status), ItemEvent.TERMINATE)
            assert nxt is ItemState.TERMINATED
            items.append(item)
        if len({i.parse_result_ref for i in items}) != 1:
            raise InvalidInput("归并条目必须来自同一要素集合")
        if len({i.req_type for i in items}) != 1:
            raise InvalidInput("归并条目类型不一致，请先用 /改类型 统一条目类型")
        head = items[0]
        current = self._version_of_item(head.id)
        if current is not None and workspace_version != current:
            raise RejectedTransition("工作区已更新（版本不一致），请刷新后重试")

        source_refs: list[str] = []
        for i in items:
            for ref in json.loads(i.source_element_refs or "[]"):
                if ref not in source_refs:
                    source_refs.append(ref)
        nxt_new = item_transition(ItemState.INITIAL, ItemEvent.FORM)
        assert nxt_new is ItemState.PENDING_CONFIRMATION
        req_no = f"REQ-{self._items.max_req_seq_of_project(project_ref) + 1:03d}"
        merged_ref = self._items.create_pending_item(
            project_ref, head.parse_result_ref, head.formation_context_ref,
            req_no, expression, head.req_type,
            json.dumps(source_refs), None,  # 人工形成：无模型形成依据
            curation_note="由 " + "、".join(i.req_no for i in items) + " 归并",
        )
        log_event(_COMPONENT, "item.status.transition", item_ref=merged_ref,
                  from_status="initial", to_status=IS.PENDING_CONFIRMATION.value,
                  sm_event=ItemEvent.FORM.value, operator_ref=operator_ref, ok=True)
        for i in items:
            self._items.set_item_status(i.id, IS.TERMINATED.value)
            log_event(_COMPONENT, "item.status.transition", item_ref=i.id,
                      from_status=IS.PENDING_CONFIRMATION.value, to_status=IS.TERMINATED.value,
                      sm_event=ItemEvent.TERMINATE.value, operator_ref=operator_ref,
                      reject_reason=f"归并入 {req_no}", ok=True)
        parse_context = self._source_assets.parse_context_of(head.parse_result_ref)
        if parse_context is not None:
            self._process_records.bump_workspace_version(parse_context)
        log_event(_COMPONENT, "item.merge.applied", item_ref=merged_ref,
                  merged_count=len(items), ok=True)
        recheck = (
            self.on_content_changed_recheck([merged_ref])
            if self.on_content_changed_recheck is not None else None
        )
        return merged_ref, recheck

    def _record_suggestion_adoption(self, item, suggestion, outcome: str, command) -> None:
        """修订建议裁定明细（口径设计 §4 item_formation 行；建议无 LDM-015 来源时跳过）。"""
        if self._model_results is None or not suggestion.model_result_ref:
            return
        self._model_results.record_adoption(
            model_result_ref=suggestion.model_result_ref, project_ref=item.project_ref,
            stage="item_formation", subject_type="requirement_item", subject_ref=item.id,
            outcome=outcome, operator_ref=command.operator_ref,
            idempotency_key=f"{command.idempotency_key}:adoption:{suggestion.id}",
            basis_ref=suggestion.id,
        )

    def _record_superseded_findings(self, item, command) -> None:
        """旧轮次随修订失效：未裁决结论 → superseded（已裁决结局保留；v5 subject=review_round）。"""
        if self._model_results is None or self._reviews is None:
            return
        round_ = self._reviews.latest_round_of_item(item.id)
        if (round_ is None or round_.invalidated or not round_.verdict_kind
                or round_.adjudication_decision is not None or not round_.model_result_ref):
            return
        self._model_results.record_adoption(
            model_result_ref=round_.model_result_ref, project_ref=item.project_ref,
            stage="item_diagnosis", subject_type="review_round", subject_ref=round_.id,
            outcome="superseded", operator_ref=command.operator_ref,
            idempotency_key=f"{command.idempotency_key}:adoption:supersede:{round_.id}",
        )

    def _require_candidate_suggestion(self, command: ItemRevisionCommand, item_ref: str):
        if not command.suggestion_ref:
            raise InvalidInput("该修订方式必须携带 suggestion_ref")
        suggestion = self._formation_process.get_suggestion(command.suggestion_ref)
        if suggestion is None or suggestion.item_ref != item_ref:
            raise NotFound("修订建议不存在或不属于该条目")
        if suggestion.status != "candidate":
            raise RejectedTransition("该建议已处置，不能重复采纳或拒绝")
        return suggestion

    def _version_of_item(self, item_ref: str) -> Optional[str]:
        item = self._items.get_item(item_ref)
        if item is None:
            return None
        parse_context = self._source_assets.parse_context_of(item.parse_result_ref)
        if parse_context is None:
            return None
        return str(self._process_records.read_workspace_version(parse_context))
