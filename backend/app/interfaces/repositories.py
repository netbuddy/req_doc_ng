"""仓储接口（数据层契约）。归其服务的领域/聚合，一份，读写方共享 import。

写权威边界（VAL-003）由具体适配器保证：LDM-002/003 只经 SourceAssetRepository，
LDM-015 只经 ModelResultRepository，接入请求上下文经 ProcessRecordRepository。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol, Sequence, runtime_checkable

from app.domain.enums import IntakeConclusion, ModelJudgement


@dataclass(frozen=True)
class RequestContent:
    """接入请求上下文承载的提交内容（供模型送检 + 已接入时形成 LDM-002）。"""

    project_ref: str
    raw_text: str
    source_note: str
    # P6a：项目领域上下文（LDM-001 既有字段，只读注入识别/扫描 lane；缺省 None=不注入段）
    project_scope: str | None = None
    project_background: str | None = None
    # P6b：项目领域档案 key（None=generic=不注入领域先验）
    domain_profile_key: str | None = None


@dataclass(frozen=True)
class RecognizedElementRow:
    """识别出的单个要素（写 LDM-005 用；稳定码，不耦合适配器/枚举类型）。

    model_verdict 是证据字段（ModelVerdict 稳定码）；登记时 process_status 一律待确认。
    """

    element_type: str      # ElementType 稳定码
    content: str
    source_anchor: Optional[str]
    confidence: Optional[float]
    model_verdict: Optional[str] = None  # ModelVerdict 稳定码（证据）
    verdict_reason: Optional[str] = None  # 模型给该条裁定的具体理由（证据；模型漏给时为 None）


@dataclass(frozen=True)
class RecognitionRead:
    """从 LDM-015 读回的结构化识别结果（供 AEP-022 承接分支 + 写 LDM-005）。"""

    result_code: str       # recognized / no_elements / failed
    elements: tuple[RecognizedElementRow, ...]
    basis: Optional[str]


@dataclass(frozen=True)
class ElementRow:
    """LDM-005 读投影（供工作区读视图，含 id 与生命周期/证据/修订字段）。"""

    id: str
    element_type: str
    content: str
    source_anchor: Optional[str]
    confidence: Optional[float]
    process_status: str                       # ElementProcessStatus（确认生命周期）
    model_verdict: Optional[str] = None       # ModelVerdict（证据）
    verdict_reason: Optional[str] = None      # 该裁定的具体理由（证据）
    noise_triage: Optional[str] = None        # NoiseTriage（人工处置标记；None＝未处置）
    version: int = 1
    superseded: bool = False
    review_conclusion: Optional[str] = None   # ReviewConclusion
    review_basis: Optional[str] = None
    revision_draft: Optional[str] = None
    correction_note: Optional[str] = None
    origin_refs: Optional[str] = None         # JSON
    updated_at: Optional[str] = None          # ISO；最近一次写入时刻（区5 复核·修订稿卡的时间线位置）


@dataclass(frozen=True)
class FacetProjectionRow:
    """要素完备度投影行（过程记录读写投影；element_version 为版本锚，设计增补 §3）。"""

    element_ref: str
    element_version: int
    rubric_version: int
    facet_key: str
    facet_status: str  # present / missing / ambiguous
    evidence: Optional[str] = None
    note: Optional[str] = None
    correctness: Optional[str] = None
    completeness: Optional[str] = None
    model_result_ref: Optional[str] = None


@dataclass(frozen=True)
class ItemStructureProjectionRow:
    """条目陈述达标投影行（过程记录读写投影；item_content_rev 为版本锚，条目档案增补 §3）。"""

    item_ref: str
    item_content_rev: int
    profile_version: int
    row_kind: str  # facet / field
    key: str
    facet_status: Optional[str] = None       # 仅 facet 行：present / missing / ambiguous
    value_text: Optional[str] = None         # 仅 field 行
    evidence: Optional[str] = None
    note: Optional[str] = None
    statement_conformance: Optional[str] = None
    completeness: Optional[str] = None
    model_result_ref: Optional[str] = None
    convention_key: str = "ears-cn"          # 判定所依据的规约方案（口径锚；存量回填 ears-cn）


@dataclass(frozen=True)
class ElementHistoryRow:
    """LDM-005 变更历史读投影（US-E4-01）。"""

    id: str
    element_ref: str
    version: int
    action: str
    from_status: Optional[str]
    to_status: Optional[str]
    operator_ref: str
    note: Optional[str]
    snapshot: Optional[str]  # JSON
    at: str                  # ISO 时间


@dataclass(frozen=True)
class SupplementRow:
    """LDM-002 补入来源块读投影（带补标记呈现）。"""

    id: str
    content: str
    basis: str
    operator_ref: str
    at: str


@dataclass(frozen=True)
class OperationRow:
    """P03/P04 操作请求上下文读投影。"""

    id: str
    kind: str  # review / execution
    parse_context_ref: str
    payload: str  # JSON
    operator_ref: str


@dataclass(frozen=True)
class InflightRevisionRow:
    """一条知识项上尚未终态的 AI 修订运行读投影（确认守卫数据源）。

    一条知识项可能连着多个修订运行（用户连发几轮指令），故按知识项逐行给出，
    由服务侧再用 run_liveness 逐行判活——本投影只按状态粗筛，不判龄。
    status/created_at 命名对齐 AgentRun 行，可直接交 run_liveness.is_run_alive。
    """

    element_ref: str
    operation_ref: str
    agent_run_ref: str
    status: str                # AgentRun 状态（queued/started）
    created_at: datetime       # AgentRun 入队时间（判活龄基准）


@dataclass(frozen=True)
class DraftRow:
    """P04 变更草案读投影（items 为 JSON 字符串）。"""

    id: str
    parse_context_ref: str
    workspace_version: int
    operation_type: str
    origin: str  # manual / review_adopted / ai_execution
    items: str  # JSON：[{action, origin_refs, note, element{...}}]
    target_refs: str  # JSON
    suggestion_refs: str  # JSON
    source_ranges: str  # JSON
    impact_summary: str  # JSON
    create_gate: str
    next_action: Optional[str]
    status: str  # open/confirmed/cancelled
    updated_at: Optional[str] = None  # ISO；草案最近一次写入时刻（区5 变更草案卡的时间线位置）


@dataclass(frozen=True)
class StagePayloadRow:
    """LDM-015 结构化结果读投影（复核/执行类）。"""

    ref: str
    stage: str
    result_code: str
    payload: Optional[str]  # JSON
    basis: Optional[str]


@dataclass(frozen=True)
class ElementCreateRow:
    """创建 LDM-005 的一行（P04 确认创建 / 扫原文补漏 / 人工新增；含留痕字段）。"""

    element_type: str
    content: str
    source_anchor: Optional[str]  # 结构化锚点 JSON
    confidence: Optional[float]
    process_status: str  # 新登记一律 pending_confirmation
    model_verdict: Optional[str] = None
    correction_state: Optional[str] = None
    correction_note: Optional[str] = None
    origin_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProjectRow:
    """业务项目 LDM-001 读投影（供服务映射为接口结构，不外泄 ORM）。

    2026-08-07 项目管理组重构：status 死列随迁移 c1u2v3w4x5y6 删除，投影同步去掉。
    """

    id: str
    name: str
    scope: Optional[str]
    background: Optional[str]
    created_at: Optional[str] = None  # ISO8601（总览台项目列表/选中卡展示）
    domain_profile_key: Optional[str] = None  # P6b 领域档案 key（None=generic）


@dataclass(frozen=True)
class ItemRow:
    """LDM-007 读投影（供条目形成工作区读视图与 AEP-036）。"""

    id: str
    project_ref: str
    parse_result_ref: str
    formation_context_ref: str
    req_no: str
    expression: str
    req_type: str                   # RequirementItemType 稳定码
    status: str                     # RequirementItemStatus 稳定码
    version_no: int
    source_element_refs: str        # JSON：来源 LDM-005 id 列表
    formation_basis_ref: Optional[str]
    curation_note: Optional[str] = None   # 内容整理说明（20 基线 §5.7；档案增补 §4）
    boundary_note: Optional[str] = None   # 条目边界说明（同上）
    verification_method: Optional[str] = None  # 验证方式（多选逗号连接；29148 属性补齐）
    verification_note: Optional[str] = None    # 验收准则（模型初稿只准归纳来源）
    priority: Optional[str] = None             # 条目优先级（仅人工设定）


@dataclass(frozen=True)
class ItemRevisionRow:
    """LDM-007 字段修订记录读投影。"""

    id: str
    item_ref: str
    field_key: str
    before_value: str
    after_value: str
    revision_mode: str
    suggestion_ref: Optional[str]
    selected_point_refs: Optional[str]  # JSON：所选修订点出处（v5）
    reason: Optional[str]
    operator_ref: str
    at: str


@dataclass(frozen=True)
class ItemOutcomeRow:
    """条目化批次逐要素归因结果读投影。"""

    id: str
    formation_context_ref: str
    element_ref: str
    result_status: str              # ItemizationResultStatus 稳定码
    item_ref: Optional[str]
    formation_basis_ref: Optional[str]
    reason: Optional[str]
    next_action: Optional[str]


@dataclass(frozen=True)
class ItemSuggestionRow:
    """字段修订候选建议读投影（来源=条目格式化类 LDM-015）。"""

    id: str
    item_ref: str
    field_key: str
    proposed_value: str
    reason: str
    status: str                     # candidate/accepted/rejected/expired
    model_result_ref: Optional[str]
    created_at: Optional[str] = None  # ISO；建议生成时刻（区5 建议卡的时间线位置）


@dataclass(frozen=True)
class FormationRequestRow:
    """条目化批次上下文读投影。"""

    id: str
    project_ref: str
    parse_context_ref: str
    parse_result_ref: str
    scope_type: str
    target_refs: str                # JSON
    operator_ref: str
    stop_next_action: Optional[str]
    convention_key: str = "ears-cn"  # 本批次固定的生效规约方案（发起时读取一次；存量回填 ears-cn）


@dataclass(frozen=True)
class InflightFormationRow:
    """同解析结果最近一个 AgentRun 仍处 queued/started 的条目化批次投影（HK-1 单飞守卫数据源）。

    status/created_at 命名对齐 AgentRun 行，可直接交 run_liveness.is_run_alive 判活。
    """

    formation_context_ref: str
    agent_run_ref: str
    status: str                # AgentRun 状态（queued/started）
    created_at: datetime       # AgentRun 入队时间（判活龄基准）


@runtime_checkable
class RequirementItemRepository(Protocol):
    """需求条目仓储：LDM-007 的唯一权威写入口（VAL-003）。

    待确认创建的写权威 = 条目形成服务；字段修订的写权威 = 需求条目服务，均经此仓储。
    """

    def create_pending_item(
        self, project_ref: str, parse_result_ref: str, formation_context_ref: str,
        req_no: str, expression: str, req_type: str,
        source_element_refs_json: str, formation_basis_ref: Optional[str],
        curation_note: Optional[str] = None, boundary_note: Optional[str] = None,
        verification_method: Optional[str] = None, verification_note: Optional[str] = None,
    ) -> str:
        """创建待确认 LDM-007（status 固定 pending_confirmation, version_no=1），返回 item_ref。"""
        ...

    def get_item(self, item_ref: str) -> Optional[ItemRow]: ...

    def items_of_parse_result(self, parse_result_ref: str) -> list[ItemRow]: ...

    def count_items_of_project(self, project_ref: str) -> int:
        """项目内条目计数（临时编号 REQ-xxx 生成用）。"""
        ...

    def max_req_seq_of_project(self, project_ref: str) -> int:
        """项目内已用 REQ 编号最大序号（新编号=最大号+1，计数法在有删除/并发时会撞号）。"""
        ...

    def apply_item_field(self, item_ref: str, field_key: str, new_value: str) -> None:
        """更新待确认条目字段（迁移合法性由服务用状态机裁定后调用）。"""
        ...

    def record_item_revision(
        self, item_ref: str, field_key: str, before_value: str, after_value: str,
        revision_mode: str, suggestion_ref: Optional[str], reason: Optional[str],
        operator_ref: str, idempotency_key: str,
    ) -> str:
        """写字段修订记录，返回 revision_record_ref。"""
        ...

    def revisions_of(self, item_ref: str) -> list[ItemRevisionRow]: ...

    def find_revision_by_idempotency(self, key: str) -> Optional[ItemRevisionRow]: ...

    def set_item_status(self, item_ref: str, status: str) -> None:
        """更新条目状态（迁移合法性由服务用状态机裁定后调用；AEP-037 确认写入）。"""
        ...

    def confirmed_items_of_project(self, project_ref: str) -> list[ItemRow]:
        """项目内确认态条目（SCN-004 图表创建候选来源投影）。"""
        ...


@dataclass(frozen=True)
class DiagnosisBatchRow:
    """诊断批次过程记录读投影（批次只是执行组织方式，不是结果返回边界）。"""

    id: str
    project_ref: str
    parse_context_ref: str
    parse_result_ref: str
    review_context_ref: str
    item_refs: str                  # JSON：条目 id 列表
    diagnosis_mode: str             # DiagnosisMode 稳定码
    operator_ref: str
    stop_next_action: Optional[str]
    created_at: str                 # ISO8601


@dataclass(frozen=True)
class DiagnosisRoundRow:
    """LDM-009 诊断轮次读投影（逐条目诊断处理状态 + 确认依据）。"""

    id: str
    project_ref: str
    item_ref: str
    batch_ref: str
    round_no: int                   # 条目内轮次序号（最近轮次判定用）
    diagnosis_mode: str             # DiagnosisMode 稳定码
    processing_status: str          # DiagnosisProcessingStatus 稳定码
    context_coverage: str
    model_result_ref: Optional[str]
    reason: Optional[str]           # 未能诊断/失败原因
    invalidated: bool
    invalidated_reason: Optional[str]
    trigger: str                    # DiagnosisTrigger 稳定码（v5）
    verdict_kind: Optional[str]     # VerdictKind 稳定码（v5 结论状态字）
    verdict_summary: Optional[str]
    revision_points: Optional[str]  # JSON：修订点列表（仅 revise）
    supplement_gaps: Optional[str]  # JSON：来源缺口清单（仅 supplement）
    superseded_by: Optional[str]
    excluded_point_refs: Optional[str]  # JSON：采纳时排除的点
    adjudication_decision: Optional[str]  # VerdictDecision 稳定码
    adjudication_selected_points: Optional[str]  # JSON
    adjudication_reason: Optional[str]
    adjudication_point_edits: Optional[str]  # JSON：{point_ref: 用户改稿终稿}（可空=未改稿）
    adjudication_operator: Optional[str]
    adjudicated_at: Optional[str]   # ISO8601
    overridden: bool
    confirm_result: Optional[str]
    confirm_basis: Optional[str]
    confirmed_by: Optional[str]
    created_at: str                 # ISO8601
    quality_meta: Optional[str] = None  # JSON：v2 质量诊断器旁路元数据（可空；降级不拒收）


@dataclass(frozen=True)
class ReviewFindingRow:
    """LDM-009 诊断发现项读投影（含逐项复核判断）。"""

    id: str
    round_ref: str
    item_ref: str
    finding_type: str               # ReviewFindingType 稳定码
    diagnosis_summary: str
    basis_summary: str
    suggested_disposition: str      # SuggestedDisposition 稳定码
    suggested_field: Optional[str]
    suggested_value: Optional[str]
    suggested_reason: Optional[str]
    suggestion_ref: Optional[str]
    model_result_ref: Optional[str]
    decision: Optional[str]         # FindingReviewDecision 稳定码
    decision_reason: Optional[str]
    decision_operator: Optional[str]
    decided_at: Optional[str]       # ISO8601


@dataclass(frozen=True)
class FindingVetoRow:
    """LDM-009 诊断问题否决留痕读投影（用户裁定「这条不是问题」）。

    存的是**问题指纹**（rule_code + evidence_span）而非某一行发现项的引用：发现项每轮重写，
    而一次否决要对未来所有轮次生效。撤销＝revoked_at 非空（行不删，留痕保住）。
    """

    id: str
    project_ref: str
    item_ref: str
    rule_code: Optional[str]
    evidence_span: Optional[str]
    finding_type: str               # ReviewFindingType 稳定码
    finding_summary: str            # 否决当时那条发现项的问题摘要（展示用，不参与匹配）
    origin_finding_ref: Optional[str]
    reason: Optional[str]           # 用户理由（可选）
    operator_ref: str
    created_at: str                 # ISO8601
    revoked_at: Optional[str]       # ISO8601；非空=已撤销，该指纹恢复计入阻断
    revoked_by: Optional[str]


@runtime_checkable
class ItemReviewRepository(Protocol):
    """条目评审仓储：诊断批次过程记录 + LDM-009 诊断轮次/发现项/复核判断/确认依据。"""

    # ---- 诊断批次（过程记录，P01-N05）----

    def find_batch_by_idempotency(self, key: str) -> Optional[str]: ...

    def create_batch(
        self, project_ref: str, parse_context_ref: str, parse_result_ref: str,
        review_context_ref: str, item_refs_json: str, diagnosis_mode: str,
        operator_ref: str, idempotency_key: str,
    ) -> str: ...

    def get_batch(self, batch_ref: str) -> Optional[DiagnosisBatchRow]: ...

    def batches_of_parse_result(self, parse_result_ref: str) -> list[DiagnosisBatchRow]:
        """按创建时间倒序。"""
        ...

    # ---- 诊断轮次（LDM-009，P01-N12）----

    def create_round(
        self, project_ref: str, item_ref: str, batch_ref: str,
        diagnosis_mode: str, context_coverage: str, trigger: str = "user_submit",
    ) -> str:
        """创建诊断中轮次（processing_status=diagnosing），返回 round_ref。"""
        ...

    def running_round_of(self, batch_ref: str, item_ref: str) -> Optional[DiagnosisRoundRow]: ...

    def finish_round(
        self, round_ref: str, processing_status: str,
        model_result_ref: Optional[str] = None, reason: Optional[str] = None,
    ) -> None: ...

    def rounds_of_batch(self, batch_ref: str) -> list[DiagnosisRoundRow]: ...

    def latest_round_of_item(self, item_ref: str) -> Optional[DiagnosisRoundRow]: ...

    def has_running_round(self, item_ref: str) -> bool: ...

    def has_user_initiated_round(self, item_ref: str) -> bool:
        """该条目是否存在用户显式发起的诊断轮次（trigger ∈ {user_submit, dialogue_reeval}）。

        白名单口径：新增 trigger 枚举值默认不算诊断史（失败关闭）；NULL 按 user_submit 计。
        单条 EXISTS，供链式守卫在交互写路径上判有史，替代整篇文档批次扫描。
        """
        ...

    def count_adopted_revise_rounds(self, item_ref: str) -> int:
        """该条目「被采纳过的建议修订」轮次数（供 _REPEATED_REVISE_HINT_AT 的事实日志与读投影
        adopted_revise_rounds 使用；采纳链空转熔断已于 2026-07-20 废除，本计数不再是任何停发判据）。

        口径：verdict_kind = revise 且 adjudication_decision = adopted 的轮次计数——
        即「用户照办了几次，仍没走到通过」。已失效轮次同样计入：链式复诊前置必然全是
        失效轮（AEP-036 先失效旧轮再回调），要求「未失效」会使计数恒为 0、提示永不出现。
        """
        ...

    def invalidate_rounds_of_item(self, item_ref: str, reason: str) -> None:
        """条目修订后旧诊断轮次失效（P03-N07；失效轮次不得作为 P04 依据）。"""
        ...

    # ---- 诊断发现项与逐项复核（LDM-009，P02）----

    def add_finding(
        self, round_ref: str, item_ref: str, finding_type: str,
        diagnosis_summary: str, basis_summary: str, suggested_disposition: str,
        suggested_field: Optional[str], suggested_value: Optional[str],
        suggested_reason: Optional[str], suggestion_ref: Optional[str],
        model_result_ref: Optional[str],
    ) -> str: ...

    def findings_of_round(self, round_ref: str) -> list[ReviewFindingRow]: ...

    def get_finding(self, finding_ref: str) -> Optional[ReviewFindingRow]: ...

    def find_decision_by_idempotency(self, key: str) -> Optional[str]:
        """幂等重放：返回已裁定的 finding_ref。"""
        ...

    def record_decision(
        self, finding_ref: str, decision: str, reason: Optional[str],
        operator_ref: str, idempotency_key: str,
    ) -> None:
        """【冻结历史口径】v5 起不再新写；保留供存量数据测试与只读投影。"""
        ...

    # ---- 问题否决留痕（AEP-116；用户裁定「这条不是问题」，跨轮生效）----

    def add_finding_veto(
        self, project_ref: str, item_ref: str, finding_type: str,
        rule_code: Optional[str], evidence_span: Optional[str],
        finding_summary: str, origin_finding_ref: Optional[str],
        reason: Optional[str], operator_ref: str, idempotency_key: str,
    ) -> str:
        """登记一条否决（返回 veto_ref）。指纹＝rule_code + evidence_span。"""
        ...

    def vetoes_of_item(self, item_ref: str, include_revoked: bool = False) -> list[FindingVetoRow]:
        """该条目的否决留痕（按登记时间正序）。缺省只返回生效中的（未撤销）。"""
        ...

    def get_finding_veto(self, veto_ref: str) -> Optional[FindingVetoRow]: ...

    def find_veto_by_idempotency(self, key: str) -> Optional[str]:
        """幂等重放：返回已登记的 veto_ref。"""
        ...

    def revoke_finding_veto(self, veto_ref: str, operator_ref: str) -> None:
        """撤销否决（置 revoked_at，不删行）；该指纹随即恢复计入阻断。已撤销者再撤为空操作。"""
        ...

    # ---- v5 结论与裁决（LDM-009）----

    def set_round_verdict(
        self, round_ref: str, verdict_kind: str, verdict_summary: str,
        revision_points_json: Optional[str], supplement_gaps_json: Optional[str],
        quality_meta_json: Optional[str] = None,
    ) -> None:
        """诊断收束时落结论（结论=判断，仅轮次铸造）。

        quality_meta_json：v2 质量诊断器旁路元数据（可空；降级不拒收，落库失败不影响结论）。
        """
        ...

    def record_adjudication(
        self, round_ref: str, decision: str, selected_points_json: Optional[str],
        excluded_points_json: Optional[str], reason: Optional[str],
        operator_ref: str, idempotency_key: str,
        point_edits_json: Optional[str] = None,
    ) -> None:
        """结论裁决（采纳/拒绝）；副作用链由服务执行。

        point_edits_json：采纳修订时用户对所选点替换文本的改稿（JSON：{point_ref: 终稿}）；
        None＝未改稿。AI 原案不经此列，它在轮次 revision_points 里原样保留。
        """
        ...

    def find_adjudication_by_idempotency(self, key: str) -> Optional[str]:
        """幂等重放：返回已裁决的 round_ref。"""
        ...

    def supersede_round(self, old_round_ref: str, new_round_ref: str) -> None:
        """对话重评改判：旧结论被新轮次替代。"""
        ...

    def mark_round_overridden(self, round_ref: str) -> None:
        """覆盖确认：标记当时站立结论被人工覆盖（效能统计覆盖率）。"""
        ...

    # ---- 确认依据（LDM-009，P04-N05）----

    def record_confirmation(
        self, round_ref: str, confirm_result: str, confirm_basis: str,
        operator_ref: str, idempotency_key: str,
    ) -> None: ...

    def find_confirmation_by_idempotency(self, key: str) -> Optional[str]:
        """幂等重放：返回已确认的 round_ref。"""
        ...


@runtime_checkable
class ItemFormationProcessRepository(Protocol):
    """条目化批次过程记录仓储：批次上下文 + 逐要素归因 + 建议处置 + 幂等。"""

    def find_formation_by_idempotency(self, key: str) -> Optional[str]: ...

    def create_formation_request(
        self, project_ref: str, parse_context_ref: str, parse_result_ref: str,
        scope_type: str, target_refs_json: str, operator_ref: str, idempotency_key: str,
        convention_key: str = "ears-cn",
    ) -> str:
        """建条目化批次上下文，返回 formation_context_ref；convention_key 随批次固定。"""
        ...

    def get_formation_request(self, formation_context_ref: str) -> Optional[FormationRequestRow]: ...

    def mark_formation_stopped(self, formation_context_ref: str, next_action: str) -> None: ...

    def record_outcome(
        self, formation_context_ref: str, element_ref: str, result_status: str,
        item_ref: Optional[str], formation_basis_ref: Optional[str],
        reason: Optional[str], next_action: Optional[str],
    ) -> None:
        """记录一条逐要素归因结果（created/blocked/failed/skipped）。"""
        ...

    def outcomes_of(self, formation_context_ref: str) -> list[ItemOutcomeRow]: ...

    def latest_outcomes_of_parse_result(self, parse_result_ref: str) -> list[ItemOutcomeRow]:
        """该解析结果最近一次批次的逐要素结果（工作区呈现用）。"""
        ...

    def latest_formation_of_parse_result(self, parse_result_ref: str) -> Optional[str]:
        """该解析结果最近一次条目化批次上下文（回放既有形成工作区用）。"""
        ...

    def find_inflight_formation_of_parse_result(
        self, parse_result_ref: str
    ) -> Optional[InflightFormationRow]:
        """该解析结果最近一个 AgentRun 仍处 queued/started 的批次（HK-1 单飞守卫）。

        只按状态筛选，不判龄；是否仍算在飞由服务经 run_liveness 按 lane 阈值裁定。
        """
        ...

    def find_inflight_recheck_of_parse_result(
        self, parse_result_ref: str
    ) -> Optional[InflightFormationRow]:
        """该解析结果最近一个仍处 queued/started 的结构复核批次（AEP-114 在飞去重）。

        批次上下文=LDM-015 受理信封（stage=item_structure_recheck，applies_to=parse_result）；
        行形状复用 InflightFormationRow（formation_context_ref 字段承载信封引用）。
        """
        ...

    def find_recheck_by_idempotency(
        self, key: str, parse_result_ref: Optional[str] = None
    ) -> Optional[InflightFormationRow]:
        """幂等重放（issue #12 卡B）：按 recheck_idempotency_key 索引列等值找回原复核批次。

        parse_result_ref 给定时附加域过滤（applies_to_ref＝信封所属 parse_result，跨项目
        同 key 不互命中）；返回信封引用＋其 AgentRun。取代旧 result_content LIKE 片段匹配。
        """
        ...

    def save_suggestion(
        self, item_ref: str, field_key: str, proposed_value: str, reason: str,
        model_result_ref: Optional[str],
    ) -> str: ...

    def get_suggestion(self, suggestion_ref: str) -> Optional[ItemSuggestionRow]: ...

    def suggestions_of_items(self, item_refs: Sequence[str]) -> list[ItemSuggestionRow]: ...

    def set_suggestion_status(self, suggestion_ref: str, status: str) -> None: ...


@runtime_checkable
class ProjectRepository(Protocol):
    """业务项目 LDM-001 仓储（物理属来源资产仓储；写权威=项目上下文服务，VAL-003）。"""

    def create(self, name: str, scope: Optional[str], background: Optional[str]) -> str:
        """建业务项目，返回 id。"""
        ...

    def get(self, project_id: str) -> Optional[ProjectRow]: ...

    def list_all(self) -> list[ProjectRow]: ...

    def find_by_idempotency_key(self, key: str) -> Optional[ProjectRow]:
        """按创建幂等键找已建项目（同键重放返回同一项目）。"""
        ...


@runtime_checkable
class ModelResultRepository(Protocol):
    """模型推理结果仓储：登记 / 读 来源接入判断类 LDM-015。"""

    def record_intake_judgement(
        self, judgement: ModelJudgement, applies_to: Optional[str], basis: str
    ) -> str:
        """登记一条来源接入判断 LDM-015（判定 + 依据），返回 model_result_ref。"""
        ...

    def read_intake_judgement(self, model_result_ref: str) -> Optional[ModelJudgement]: ...

    def read_basis(self, model_result_ref: str) -> Optional[str]: ...

    # --- SCN-001-P02 需求要素识别类 LDM-015 ---
    def record_element_recognition(
        self, applies_to: Optional[str], result_code: str,
        elements: Sequence[RecognizedElementRow], basis: str,
    ) -> str:
        """登记一条要素识别类 LDM-015（结果码 + 结构化要素集 JSON + 依据），返回 model_result_ref。"""
        ...

    def read_element_recognition(self, model_result_ref: str) -> Optional[RecognitionRead]: ...

    # --- SCN-001-P03/P04 复核/执行类 LDM-015（结构化 payload）---
    def record_stage_payload(
        self, stage: str, applies_to: Optional[str], result_code: str,
        payload_json: Optional[str], basis: str,
        recheck_idempotency_key: Optional[str] = None,
    ) -> str:
        """登记一条复核/执行类 LDM-015，返回 model_result_ref。

        recheck_idempotency_key：仅结构复核受理信封传入，落 ModelResult 幂等索引列供等值
        幂等查询（见 find_recheck_by_idempotency）；其余 stage 恒 None。
        """
        ...

    def read_stage_payload(self, model_result_ref: str) -> Optional[StagePayloadRow]: ...

    def update_stage_payload(self, model_result_ref: str, payload_json: str) -> None:
        """改写既有 LDM-015 的结构化 payload（过程信封账目登记：版本锚/逐条目结局）。

        仅限过程信封类记录（batch_accepted）；判定结论类 LDM-015 一经登记不改写。
        """
        ...

    def latest_stage_payload(self, stage: str, applies_to: str) -> Optional[StagePayloadRow]:
        """该上下文最近一条指定 stage 的 LDM-015（复核建议投影用）。"""
        ...

    # --- SCN-004 图表源码建议/图文核对类 LDM-015 处置状态 ---
    def set_process_status(self, model_result_ref: str, status: str) -> None:
        """更新 LDM-015 处置状态（pending/adopted/revised_adopted/rejected/transferred_to_issue）。"""
        ...

    def stage_payloads_of(self, stage: str, applies_to_refs: Sequence[str]) -> list[StagePayloadRow]:
        """按上下文集合读指定 stage 的全部 LDM-015（建议面板投影用；含处置状态见 payload 外的 process_status）。"""
        ...

    def read_process_status(self, model_result_ref: str) -> Optional[str]: ...

    # --- LDM-015 采纳结论明细（AI效能统计口径设计 §7；统计=AEP-094 只读聚合）---
    def record_adoption(
        self, *, model_result_ref: str, project_ref: str, stage: str,
        subject_type: str, subject_ref: str, outcome: str,
        operator_ref: str, idempotency_key: str, basis_ref: Optional[str] = None,
    ) -> None:
        """登记采纳结论明细（逐被裁定对象；idempotency_key 冲突时静默跳过=幂等重放）。"""
        ...


@runtime_checkable
class ProcessRecordRepository(Protocol):
    """过程记录仓储：接入请求上下文 + 幂等 + 失败停靠。"""

    def find_context_by_idempotency(self, key: str) -> Optional[str]: ...

    def create_intake_request(
        self, project_ref: str, key: str, raw_text: str, source_note: str, operator_ref: str
    ) -> str:
        """建接入请求上下文（承载提交内容，供 已接入 时形成 LDM-002）。"""
        ...

    def context_exists(self, context_ref: str) -> bool: ...

    def read_request_content(self, context_ref: str) -> Optional[RequestContent]:
        """读该上下文的提交内容（供模型送检）。"""
        ...

    def mark_stopped(self, context_ref: str, reason: str, next_action: str) -> None: ...

    def read_stop_next_action(self, context_ref: str) -> Optional[str]:
        """若该上下文有失败停靠，返回其 next_action，否则 None。"""
        ...

    # --- SCN-001-P02 识别请求上下文（ParseRequest）---
    def find_parse_context_by_idempotency(self, key: str) -> Optional[str]: ...

    def latest_parse_context_of_material(self, material_ref: str) -> Optional[str]:
        """该材料最近一次识别请求上下文；从未识别过则 None（进页只读回放用）。"""
        ...

    def create_parse_request(
        self, project_ref: str, key: str, material_ref: str, operator_ref: str
    ) -> str:
        """建识别请求上下文（承载 material_ref，供已解析时形成 LDM-004/005）。"""
        ...

    def parse_context_exists(self, context_ref: str) -> bool: ...

    def read_parse_material_ref(self, context_ref: str) -> Optional[str]:
        """读该识别请求上下文关联的已接入材料引用（供识别送检读原文）。"""
        ...

    def mark_parse_stopped(self, context_ref: str, reason: str, next_action: str) -> None: ...

    def read_parse_stop_next_action(self, context_ref: str) -> Optional[str]: ...

    # --- 工作区版本（要素集变更时递增；P03/P04 命令版本校验用）---
    def read_workspace_version(self, context_ref: str) -> int: ...

    def project_of_context(self, context_ref: str) -> Optional[str]:
        """上下文（接入/解析请求）所属项目（采纳明细冗余 project_id 用）。"""
        ...

    def bump_workspace_version(self, context_ref: str) -> int: ...

    # --- P03/P04 操作请求上下文 ---
    def find_operation_by_idempotency(self, key: str) -> Optional[str]: ...

    def create_element_operation(
        self, project_ref: str, context_ref: str, kind: str,
        payload_json: str, operator_ref: str, key: str,
    ) -> str: ...

    def read_element_operation(self, operation_ref: str) -> Optional[OperationRow]: ...

    def find_inflight_revisions(
        self, context_ref: str, element_refs: Sequence[str]
    ) -> list[InflightRevisionRow]:
        """给定知识项上尚未终态（queued/started）的 AI 修订运行（确认守卫数据源）。

        只按 AgentRun 状态粗筛，判龄留给服务侧的 run_liveness——判活阈值是 lane 的事，
        仓储不该知道。element_refs 为空时返回空表（不做全上下文扫描）。
        """
        ...

    # --- P04 变更草案（同一上下文仅一份 open 草案，新建即替换）---
    def save_change_draft(
        self, project_ref: str, context_ref: str, workspace_version: int,
        operation_type: str, origin: str, items_json: str,
        target_refs_json: str, suggestion_refs_json: str, source_ranges_json: str,
        impact_summary_json: str, create_gate: str, next_action: Optional[str],
    ) -> str: ...

    def read_open_draft(self, context_ref: str) -> Optional[DraftRow]: ...

    def read_draft(self, draft_ref: str) -> Optional[DraftRow]: ...

    def mark_draft_confirmed(self, draft_ref: str) -> None: ...

    # --- TC-08 要素完备度投影（过程记录；仅 AEP-024 写，可整层重算，不作门禁）---
    def replace_facet_projection(self, element_ref: str, rows: list["FacetProjectionRow"]) -> None:
        """按要素整批替换投影行（新一轮复核覆盖旧轮；设计增补 §3）。"""
        ...

    def facet_projections_of(self, element_refs: list[str]) -> dict[str, list["FacetProjectionRow"]]:
        """按要素集合读投影行（工作区徽章/筛选用）。"""
        ...

    # --- 条目陈述达标投影（过程记录；仅条目形成/修订链路写，可整层重算，不作门禁）---
    def replace_item_structure_projection(
        self, item_ref: str, rows: list["ItemStructureProjectionRow"]
    ) -> None:
        """按条目整批替换投影行（新一轮格式化/诊断覆盖旧轮；条目档案增补 §3）。"""
        ...

    def item_structure_projections_of(
        self, item_refs: list[str]
    ) -> dict[str, list["ItemStructureProjectionRow"]]:
        """按条目集合读投影行（区4 徽章 / 区5 达标度筛选用）。"""
        ...


@runtime_checkable
class SourceAssetRepository(Protocol):
    """来源资产仓储：LDM-002 / LDM-003 的唯一权威写入口。"""

    def save_material_and_intake_record(self, context_ref: str, model_result_ref: str) -> str:
        """仅可接入分支：写 LDM-002 + LDM-003.intake_conclusion=已接入，返回 material_ref。"""
        ...

    def save_intake_conclusion(
        self, context_ref: str, conclusion: IntakeConclusion, model_result_ref: str
    ) -> None:
        """非可接入分支：只写 LDM-003 结论，不写 LDM-002。"""
        ...

    def conclusion_of(self, context_ref: str) -> Optional[IntakeConclusion]: ...

    def material_of(self, context_ref: str) -> Optional[str]: ...

    def model_result_ref_of(self, context_ref: str) -> Optional[str]:
        """该上下文接入记录关联的 LDM-015 引用（供读取判定依据）。"""
        ...

    # --- SCN-001-P02 已接入校验 + LDM-004/005 写入/读取 ---
    def is_material_accepted(self, material_ref: str) -> bool:
        """N01 门禁：该 LDM-002 是否有『已接入』LDM-003。"""
        ...

    def read_material_content(self, material_ref: str) -> Optional[RequestContent]:
        """读已接入材料原文（供识别送检）。"""
        ...

    def save_parse_result_and_elements(
        self, context_ref: str, material_ref: str, model_result_ref: str,
        elements: Sequence[RecognizedElementRow],
    ) -> str:
        """可登记分支：写 LDM-004(已解析) + 全部初始 LDM-005，返回 parse_result_ref。"""
        ...

    def save_parse_conclusion(
        self, context_ref: str, material_ref: str, model_result_ref: str, note: str
    ) -> str:
        """无可处理要素分支：只写 LDM-004(不可继续处理)，不写 LDM-005，返回 parse_result_ref。"""
        ...

    def parse_status_of(self, context_ref: str) -> Optional[str]:
        """该识别上下文的 LDM-004.parse_status 稳定码；无则 None（默认拒绝守卫用）。"""
        ...

    def parse_result_of(self, context_ref: str) -> Optional[str]: ...

    def parse_context_of(self, parse_result_ref: str) -> Optional[str]:
        """LDM-004 → 其识别请求上下文（条目形成服务定位要素工作区用）。"""
        ...

    def parse_basis_of(self, context_ref: str) -> Optional[str]: ...

    def elements_of(self, parse_result_ref: str) -> list[ElementRow]: ...

    def list_project_elements_by_type(
        self, project_ref: str, element_types: Sequence[str]
    ) -> list[ElementRow]:
        """项目内指定类型的未替代要素（登记归并名称匹配用；P3 §2.1）。"""
        ...

    # --- SCN-001-P04 版本关系层：替代旧要素 + 创建新要素（唯一受控变更入口）---
    def apply_element_changes(
        self, context_ref: str,
        creates: Sequence[ElementCreateRow],
        closes: Sequence[tuple[str, str, str]],  # (element_id, correction_state, note)
    ) -> list[str]:
        """替代旧要素（保留行，superseded=True）+ 创建新要素（待确认），返回新 id 列表。"""
        ...

    def add_elements(
        self, context_ref: str, creates: Sequence[ElementCreateRow]
    ) -> list[str]:
        """向该上下文的解析结果追加新要素（扫原文补漏产物；一律待确认）。"""
        ...

    # --- SCN-001-P03 确认生命周期（写权威=分析转化服务经此仓储）---
    def get_element(self, element_id: str) -> Optional[ElementRow]: ...

    def set_element_status(
        self, element_id: str, status: str, bump_version: bool = False,
        clear_review: bool = False,
    ) -> None:
        """迁移 process_status（迁移合法性由服务用状态机裁定后调用）。"""
        ...

    def set_element_noise_triage(self, element_id: str, triage: Optional[str]) -> None:
        """写「建议剔除候选」的人工处置标记（None＝移回候选区）。

        只写 noise_triage 一列：model_verdict 与 verdict_reason 是模型证据，任何用户
        动作都不得改写；本方法也不迁移 process_status、不升版本。
        """
        ...

    def set_element_review(
        self, element_id: str, conclusion: Optional[str], basis: Optional[str],
        revision_draft: Optional[str],
    ) -> None:
        """写最近一次 AI 复核结论/意见/修订稿（分析中证据，不改状态）。"""
        ...

    def set_revision_draft(self, element_id: str, draft: Optional[str]) -> None: ...

    def apply_element_edit(
        self, element_id: str, element_type: Optional[str], content: Optional[str],
        source_anchor: Optional[str], note: Optional[str],
    ) -> None:
        """就地修订（改类型/改范围/改表达）：字段更新 + 版本 +1；不迁状态。"""
        ...

    # --- 变更历史（US-E4-01）---
    def record_element_history(
        self, element_ref: str, project_ref: str, version: int, action: str,
        from_status: Optional[str], to_status: Optional[str],
        operator_ref: str, note: Optional[str], snapshot_json: Optional[str],
    ) -> None: ...

    def element_history_of(self, element_ref: str) -> list[ElementHistoryRow]: ...

    def merge_history_for_material(self, material_ref: str) -> list[ElementHistoryRow]:
        """该材料触发的登记归并留痕（action=merge ∧ snapshot.merged_from_material=材料）。

        既有知识项在本材料工作区的可见性投影读路径：归并不新建要素，
        本材料的识别产物落在既有要素的历史行上，只能反查。
        """
        ...

    # --- 改源联动（勘误/补入，US-E3-04/05）---
    def apply_material_erratum(
        self, material_ref: str, new_raw_text: str, note: str, operator_ref: str
    ) -> int:
        """原文出新来源版本：旧正文快照入版本表，raw_text 更新，返回新版本号。"""
        ...

    def add_material_supplement(
        self, material_ref: str, content: str, basis: str, operator_ref: str
    ) -> str:
        """追加「补」来源块（原快照不动），返回补入块 id。"""
        ...

    def supplements_of(self, material_ref: str) -> list[SupplementRow]: ...

    def material_source_version(self, material_ref: str) -> int: ...


# ---- SCN-004 受控图表确认与追溯关系成立 ----


@dataclass(frozen=True)
class ChartRow:
    """LDM-012 读投影（图表工作区读视图用）。"""

    id: str
    project_ref: str
    title: str
    chart_kind: str                 # ChartKind 稳定码
    chart_type: str                 # ChartType 稳定码
    format: str                     # ChartFormat 稳定码
    source_code: str
    draft_version: int
    status: str                     # ChartStatus 稳定码
    status_reason: Optional[str]
    source_kind: str                # ChartSourceKind 稳定码
    source_refs: str                # JSON：来源确认态 LDM-007 id 列表
    creation_basis: str
    verification_conclusion: Optional[str]
    confirm_basis: Optional[str]
    confirmed_by: Optional[str]
    created_by: str
    created_at: str                 # ISO8601
    updated_at: str                 # ISO8601


@dataclass(frozen=True)
class ChartRevisionRow:
    """LDM-012 源码修订留痕读投影。"""

    id: str
    chart_ref: str
    draft_version: int
    source_code: str
    format: str
    change_origin: str              # ChartSourceChangeOrigin 稳定码
    suggestion_ref: Optional[str]
    note: Optional[str]
    operator_ref: str
    at: str                         # ISO8601


@dataclass(frozen=True)
class TraceLinkRow:
    """LDM-013 读投影（追溯视图 + 确认准入裁定用）。"""

    id: str
    project_ref: str
    dimension: str
    relation_type: str              # TraceRelationType 稳定码
    upstream_type: str
    upstream_ref: str
    downstream_type: str
    downstream_ref: str
    status: str                     # TraceLinkStatus 稳定码
    initial_basis: str
    status_reason: Optional[str]
    established_basis: Optional[str]
    established_at: Optional[str]   # ISO8601
    issue_ref: Optional[str]


@dataclass(frozen=True)
class IssueRow:
    """LDM-011 读投影（问题项列表用）。"""

    id: str
    project_ref: str
    issue_type: str                 # IssueType 稳定码
    status: str                     # IssueStatus 稳定码
    title: str
    description: str
    origin_kind: str
    chart_ref: Optional[str]
    finding_ref: Optional[str]
    trace_link_refs: str            # JSON
    created_by: str
    created_at: str                 # ISO8601


@dataclass(frozen=True)
class ChartSuggestionRequestRow:
    """AI 图表源码建议请求上下文读投影。"""

    id: str
    project_ref: str
    chart_ref: str
    base_draft_version: int
    intent: str
    operator_ref: str
    stop_next_action: Optional[str]
    created_at: str = ""            # ISO8601
    kind: str = "revision"          # initial=创建初稿 / revision=修订建议


@dataclass(frozen=True)
class ChartVerificationRequestRow:
    """图文核对请求上下文读投影。"""

    id: str
    project_ref: str
    chart_ref: str
    chart_draft_version: int
    operator_ref: str
    stop_next_action: Optional[str]


@dataclass(frozen=True)
class ChartVerificationRoundRow:
    """图文核对轮次读投影。"""

    id: str
    chart_ref: str
    request_ref: str
    round_no: int
    chart_draft_version: int        # 版本锚
    processing_status: str          # ChartVerificationProcessingStatus 稳定码
    model_result_ref: Optional[str]
    reason: Optional[str]
    invalidated: bool
    invalidated_reason: Optional[str]
    confirm_result: Optional[str]
    created_at: str                 # ISO8601


@dataclass(frozen=True)
class ChartFindingRow:
    """图文核对发现项读投影（含逐项复核判断）。"""

    id: str
    round_ref: str
    chart_ref: str
    finding_type: str               # ChartFindingType 稳定码
    summary: str
    basis_summary: str
    related_source_refs: str        # JSON
    model_result_ref: Optional[str]
    decision: Optional[str]         # ChartFindingDecision 稳定码
    decision_reason: Optional[str]
    decision_operator: Optional[str]
    decided_at: Optional[str]       # ISO8601
    issue_ref: Optional[str]


@runtime_checkable
class ChartRepository(Protocol):
    """图表资产仓储：LDM-012 的唯一权威写入口（写权威=图表资产模块经图表协同服务）。"""

    def create_chart(
        self, project_ref: str, title: str, chart_kind: str, chart_type: str,
        format_: str, source_kind: str, source_refs_json: str,
        creation_basis: str, created_by: str,
    ) -> str:
        """创建草稿壳（status=draft, draft_version=1, source_code=""），返回 chart_ref。"""
        ...

    def get_chart(self, chart_ref: str) -> Optional[ChartRow]: ...

    def charts_of_project(self, project_ref: str) -> list[ChartRow]: ...

    def update_chart_source(
        self, chart_ref: str, source_code: str, format_: str,
        chart_type: str, chart_kind: str, source_refs_json: str,
    ) -> int:
        """更新受控源码（draft_version+1 由仓储完成），返回新版本号。"""
        ...

    def set_chart_status(
        self, chart_ref: str, status: str, status_reason: Optional[str] = None,
    ) -> None:
        """更新图表状态（迁移合法性由服务用状态机裁定后调用）。"""
        ...

    def set_chart_title(self, chart_ref: str, title: str) -> None:
        """更新图表主题（创建初稿自动应用时以模型语义标题回填临时标题）。"""
        ...

    def record_confirmation(
        self, chart_ref: str, conclusion: str, confirm_basis: str, operator_ref: str,
    ) -> None:
        """写核对结论与确认依据（N08；仅确认准入通过后调用）。"""
        ...

    def add_revision(
        self, chart_ref: str, draft_version: int, source_code: str, format_: str,
        change_origin: str, suggestion_ref: Optional[str], note: Optional[str],
        operator_ref: str, idempotency_key: Optional[str] = None,
    ) -> str: ...

    def revisions_of(self, chart_ref: str) -> list[ChartRevisionRow]: ...

    def find_revision_by_idempotency(self, key: str) -> Optional[str]:
        """幂等重放：返回已应用的 chart_ref。"""
        ...


@runtime_checkable
class TraceLinkRepository(Protocol):
    """追溯图谱仓储：LDM-013 的唯一权威写入口（写权威=追溯图谱模块）。"""

    def create_link(
        self, project_ref: str, relation_type: str,
        upstream_type: str, upstream_ref: str,
        downstream_type: str, downstream_ref: str,
        status: str, initial_basis: str,
    ) -> str: ...

    def find_link(
        self, upstream_ref: str, downstream_ref: str, relation_type: str,
    ) -> Optional[TraceLinkRow]: ...

    def links_of_chart(self, chart_ref: str) -> list[TraceLinkRow]:
        """下游=该图表的全部关系。"""
        ...

    def links_of_project(
        self, project_ref: str, status: Optional[str] = None,
        chart_ref: Optional[str] = None,
    ) -> list[TraceLinkRow]: ...

    def set_link_status(
        self, link_ref: str, status: str, status_reason: Optional[str] = None,
        established_basis: Optional[str] = None,
    ) -> None:
        """更新关系状态（迁移合法性由服务用状态机裁定后调用；确立时写 established_*）。"""
        ...

    def set_link_issue(self, link_ref: str, issue_ref: str) -> None: ...


@runtime_checkable
class IssueRepository(Protocol):
    """问题项仓储：LDM-011 最小实现（创建 + 列表；处置闭环归 SCN-006）。"""

    def create_issue(
        self, project_ref: str, issue_type: str, title: str, description: str,
        origin_kind: str, chart_ref: Optional[str], finding_ref: Optional[str],
        trace_link_refs_json: str, created_by: str, idempotency_key: str,
    ) -> str: ...

    def get_issue(self, issue_ref: str) -> Optional[IssueRow]: ...

    def issues_of_project(self, project_ref: str) -> list[IssueRow]: ...

    def find_issue_by_idempotency(self, key: str) -> Optional[str]: ...


@runtime_checkable
class ChartProcessRepository(Protocol):
    """图表过程记录仓储：AI 建议/核对请求上下文 + 核对轮次 + 发现项 + 幂等。"""

    # ---- AI 源码建议请求（P01-N08）----

    def find_suggestion_request_by_idempotency(self, key: str) -> Optional[str]: ...

    def create_suggestion_request(
        self, project_ref: str, chart_ref: str, base_draft_version: int,
        intent: str, operator_ref: str, idempotency_key: str,
        kind: str = "revision",
    ) -> str: ...

    def get_suggestion_request(self, context_ref: str) -> Optional[ChartSuggestionRequestRow]: ...

    def suggestion_requests_of_chart(self, chart_ref: str) -> list[ChartSuggestionRequestRow]:
        """按创建时间倒序（建议面板投影用）。"""
        ...

    def mark_suggestion_stopped(self, context_ref: str, next_action: str) -> None: ...

    # ---- 核对请求（P02-N01）----

    def find_verification_request_by_idempotency(self, key: str) -> Optional[str]: ...

    def create_verification_request(
        self, project_ref: str, chart_ref: str, chart_draft_version: int,
        operator_ref: str, idempotency_key: str,
    ) -> str: ...

    def get_verification_request(self, request_ref: str) -> Optional[ChartVerificationRequestRow]: ...

    def mark_verification_stopped(self, request_ref: str, next_action: str) -> None: ...

    # ---- 核对轮次 ----

    def create_round(
        self, chart_ref: str, request_ref: str, chart_draft_version: int,
    ) -> str:
        """创建核对中轮次（processing_status=verifying，round_no=图表内递增），返回 round_ref。"""
        ...

    def latest_round_of_chart(self, chart_ref: str) -> Optional[ChartVerificationRoundRow]: ...

    def round_of_request(self, request_ref: str) -> Optional[ChartVerificationRoundRow]: ...

    def get_round(self, round_ref: str) -> Optional[ChartVerificationRoundRow]: ...

    def finish_round(
        self, round_ref: str, processing_status: str,
        model_result_ref: Optional[str] = None, reason: Optional[str] = None,
    ) -> None: ...

    def invalidate_rounds_of_chart(self, chart_ref: str, reason: str) -> None:
        """图表退回修订/作废后旧核对轮次显式失效。"""
        ...

    def record_round_confirmation(
        self, round_ref: str, confirm_result: str, idempotency_key: str,
    ) -> None: ...

    def find_round_confirmation_by_idempotency(self, key: str) -> Optional[str]:
        """幂等重放：返回已确认的 round_ref。"""
        ...

    # ---- 核对发现项与逐项复核 ----

    def add_finding(
        self, round_ref: str, chart_ref: str, finding_type: str,
        summary: str, basis_summary: str, related_source_refs_json: str,
        model_result_ref: Optional[str],
    ) -> str: ...

    def findings_of_round(self, round_ref: str) -> list[ChartFindingRow]: ...

    def get_finding(self, finding_ref: str) -> Optional[ChartFindingRow]: ...

    def record_finding_decision(
        self, finding_ref: str, decision: str, reason: Optional[str],
        operator_ref: str, idempotency_key: str,
    ) -> None: ...

    def find_finding_decision_by_idempotency(self, key: str) -> Optional[str]:
        """幂等重放：返回已裁定的 finding_ref。"""
        ...

    def set_finding_issue(self, finding_ref: str, issue_ref: str) -> None: ...
