"""条目评审服务与 AgentRun 读侧。

自 schemas.py 按业务域拆包（命名规范见包入口 __init__.py）。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.domain.enums import (
    DiagnosisMode,
    DiagnosisTrigger,
    DialogueOutcomeType,
    RequirementItemStatus,
    RequirementItemType,
    ReviewDisplayCode,
    ReviewFindingType,
    ReviewItemStatus,
    VerdictDecision,
    VerdictKind,
)

from .materials import ActionFact

from .elements import MaterialCanvasRead, RequirementElementRead

from .item_formation import ItemRevisionRecordRead


# ---- SCN-003 条目评审（条目评审服务 AEP-032/033/034 + 需求条目服务 AEP-037）----


class ConfirmationGateRead(BaseModel):
    """确认准入读视图（通用门禁投影，SCN-004 图表确认等复用；can_submit=false 时给出阻断原因）。"""

    can_submit: bool = False
    blocked_reasons: list[str] = Field(default_factory=list)
    review_summary_ref: str | None = None


class ReviewFindingRead(BaseModel):
    """诊断发现项读视图（v5：结论的只读证据行，无人工复核字段）。

    v2 质量诊断器 additive 旁路字段（rule_code/dimension/severity/evidence_span）：可空，
    旧消费者忽略即可；来自诊断轮次 quality_meta，缺失时降级为无 chip/无高亮。
    """

    finding_ref: str
    finding_type: ReviewFindingType
    diagnosis_summary: str
    basis_summary: str
    rule_code: str | None = None       # RequirementQualityRule 稳定码
    dimension: str | None = None       # QualityDimension 稳定码
    severity: str = "medium"           # QualitySeverity 稳定码
    evidence_span: str | None = None   # 基准表达中恰好出现一次的逐字片段（供高亮）
    # ---- 问题否决（AEP-116）：读投影时按指纹现算，不落库 ----
    #: 用户已裁定这条不是问题（本轮无论模型是否重提，都不计入阻断）。
    vetoed: bool = False
    veto_ref: str | None = None        # 命中的那条否决留痕（撤销时用）
    veto_reason: str | None = None     # 用户当时给的理由（可空）
    #: 这条能否被否决。指纹＝规则码+证据片段，两者皆缺则无法跨轮匹配，界面不给否决入口
    #: ——绝不拿模型自由撰写的问题摘要当匹配键（措辞漂移会同时造成误命中与漏命中）。
    can_veto: bool = False
    #: 条目已有人工确认来源，这条来源对齐类发现因此降为非阻断提示（读时现算，同 vetoed）。
    #: 只降来源对齐一类：与业务规则矛盾（BIZ-RULE-CONFLICT）及歧义/可测试性等判据照常阻断。
    source_attested: bool = False


class RevisionPointRead(BaseModel):
    """修订点读视图（分点选择、一次采纳；find 在基准表达中唯一定位）。"""

    point_ref: str
    label: str
    #: 模型输出的发现项序号。它与读视图 findings 的数组序不是一回事（发现项读出序由数据库
    #: 排序决定），故消费方应优先用下面的 finding_ref；此字段保留供存量数据与排障对照。
    finding_index: int = 0
    #: 本修订点所针对的发现项引用。存量轮次的元数据没有引用，此处为空，消费方回退按下标配对。
    finding_ref: str | None = None
    find: str
    replace: str
    basis: str = ""
    group: str | None = None
    #: 本点所针对的发现项已被用户裁定为「不是问题」，故本点不应再被采纳（界面标灰不可勾选）。
    vetoed: bool = False


class FindingVetoRead(BaseModel):
    """问题否决留痕读视图（AEP-116）：用户裁定「这条不是问题」的记录，跨轮生效。"""

    veto_ref: str
    item_ref: str
    finding_type: ReviewFindingType
    rule_code: str | None = None
    evidence_span: str | None = None
    finding_summary: str = ""          # 否决当时那条问题的摘要（展示用，不参与匹配）
    reason: str | None = None          # 用户理由（可选）
    operator_ref: str = ""
    at: str = ""                       # 登记时间
    revoked: bool = False
    revoked_at: str | None = None


class SourceAttestationRead(BaseModel):
    """人工确认背书读视图：材料里没写这条，由人确认它是真实需求并负责登记。

    **独立证据类别**，与「来源要素」并列而不混同：背书不产生任何材料锚点、不生成引文，
    条目的 source_element_refs 保持原样（背书条目通常为空）。界面据此显示背书标记与理由，
    绝不据此显示原文——因为确实没有对应的材料位置。
    """

    record_ref: str
    reason: str                        # 用户填的理由（必填，原文照录）
    operator_ref: str = ""
    at: str = ""                       # 登记时间


class VerdictAdjudicationRead(BaseModel):
    """结论裁决读视图（AEP-034 留痕投影）。"""

    decision: VerdictDecision
    selected_point_refs: list[str] = Field(default_factory=list)
    excluded_point_refs: list[str] = Field(default_factory=list)
    #: 采纳时用户对所选点替换文本的改稿（{point_ref: 用户终稿}）；空＝未改稿，采纳的就是 AI 原案。
    #: AI 原案始终在同轮 revision_points 里原样保留，两者并存供留痕对照。
    point_edits: dict[str, str] = Field(default_factory=dict)
    reason: str | None = None
    operator_ref: str = ""
    at: str = ""


class VerdictRead(BaseModel):
    """LDM-009 轮次读视图（v5：结论=判断；线程结论卡与收折回执的素材）。"""

    round_ref: str
    round_no: int = 1
    # run/batch 关联（issue #10 B2a，只增不改）：每轮次归属的诊断批次与条目，供按 run 归因失败
    # 事实（弃用前端以条目全局最新态猜测本 run 失败的归因错位）。旧消费者忽略即可。
    batch_ref: str = ""
    item_ref: str = ""
    diagnosis_mode: DiagnosisMode
    trigger: DiagnosisTrigger = DiagnosisTrigger.USER_SUBMIT
    status: str  # running / completed / failed（诊断处理状态投影）
    verdict_kind: VerdictKind | None = None
    verdict_summary: str | None = None
    findings: list[ReviewFindingRead] = Field(default_factory=list)
    revision_points: list[RevisionPointRead] = Field(default_factory=list)
    supplement_gaps: list[str] = Field(default_factory=list)
    context_coverage: str = ""
    model_result_refs: list[str] = Field(default_factory=list)
    invalidated: bool = False
    invalidated_reason: str | None = None
    superseded_by: str | None = None
    adjudication: VerdictAdjudicationRead | None = None
    overridden: bool = False
    confirm_result: str | None = None
    effective: bool = False  # 当前版本有效且未被裁决/替代（=待裁决的站立结论）
    reason: str | None = None  # 失败/未能诊断原因
    created_at: str = ""
    # v2 质量诊断器旁路（可空）：质量画像 6 维评分 / EARS 改写脚手架
    quality_profile: dict | None = None   # {overall, dimensions:[{key,score,note}]}
    ears_rewrite: dict | None = None      # {pattern_type, lines:[...], note}
    # ---- 问题否决派生（AEP-116）：读投影时现算，随撤销自动恢复 ----
    #: 本轮仍然成立的阻断性问题条数（发现项类型非 no_blocker、且未被否决者计数）。
    blocking_finding_count: int = 0
    #: 本轮曾报出阻断性问题，但它们已被用户逐条裁定为「不是问题」，一条不剩。
    #: 只认用户的逐条裁定，不认人工确认降格——「已被逐条裁定（N 条）」这句留痕由它驱动。
    all_blocking_findings_vetoed: bool = False
    #: 本轮曾报出阻断性问题，且此刻一条待用户处理的都不剩（被裁定或因人工确认降格都算）。
    #: 这是「直接确认」通道的开门条件；服务端确认时会重新核算一遍，不采信前端传来的判断。
    blocking_findings_cleared: bool = False


class SourceAlignmentRead(BaseModel):
    """来源语义对齐分读视图（AEP-105）：LLM 逐源 alignment + source_drift 派生 drift 合并。"""

    element_ref: str
    wing: str | None = None            # knowledge_category：requirement / business
    anchor: str | None = None          # 来源锚点引文
    alignment: float | None = None     # 0.0–1.0（LLM 产出；无诊断/未产出为 None）
    drift: bool = False                # 是否偏离（source_drift_tokens 非空）
    drift_tokens: list[str] = Field(default_factory=list)
    note: str | None = None


class ItemQualityRead(BaseModel):
    """需求条目质量投影（AEP-105）：最新一轮诊断的质量元数据，详情卡「质量诊断」页签数据源。"""

    item_ref: str
    req_no: str
    base_expression: str               # 基准表达 = span/对齐分定位锚
    has_diagnosis: bool = False
    round_ref: str | None = None
    round_no: int = 0
    status: str = ""                   # running / completed / failed
    verdict_kind: VerdictKind | None = None
    verdict_summary: str | None = None
    quality_profile: dict | None = None
    findings: list[ReviewFindingRead] = Field(default_factory=list)
    revision_points: list[RevisionPointRead] = Field(default_factory=list)
    ears_rewrite: dict | None = None
    source_alignments: list[SourceAlignmentRead] = Field(default_factory=list)


class DialogueMessageRead(BaseModel):
    """评审对话消息读视图（解释 / 草案；LDM-015 投影，领域零副作用）。"""

    message_ref: str
    kind: DialogueOutcomeType
    user_message: str = ""
    text: str = ""                      # 解释正文
    draft_value: str | None = None      # 草案完整表达
    draft_note: str | None = None       # 缺来源预警
    draft_seq: int | None = None        # 稿次
    suggestion_ref: str | None = None   # 采纳草案时的 AEP-036 候选建议引用
    in_flight: bool = False             # 在途草案（未采纳未放弃）
    #: 这条交换发生在哪一页（review/formation）。条目形成页与本页共用同一个阶段键，
    #: 本页只显示自己的交换，例外是形成页留下的在途候选建议——它要显示，但须标明来源。
    #: 存量载荷没有这一项，为空即来源不明，按原样显示不猜。
    origin: str | None = None
    created_at: str = ""


class ReviewRequirementItemRead(BaseModel):
    """条目评审读视图（v5：LDM-007 + 结论/轮次历史 + 对话消息 + 派生显示态）。"""

    item_ref: str
    req_no: str
    expression: str
    req_type: RequirementItemType
    status: RequirementItemStatus
    version_no: str = "1"
    source_element_refs: list[str] = Field(default_factory=list)
    formation_basis_ref: str | None = None
    verification_method: list[str] = Field(default_factory=list)  # 验证方式（29148 属性补齐）
    verification_note: str | None = None  # 验收准则（缺失=评审"建议补充来源"前置信号）
    priority: str | None = None  # 条目优先级（仅人工设定）
    revision_records: list[ItemRevisionRecordRead] = Field(default_factory=list)
    review_status: ReviewItemStatus  # 派生显示态：无结论/诊断中/待裁决/已确认/已终止
    status_note: str = ""            # 显示态说明（如：结论被拒绝待人驱动、来源缺口未闭合）
    # 用户可见显示态封闭集（issue #10 B2a，只增不改）：把 review_status 的 no_verdict 细分为
    # 待诊断/诊断失败/结论已拒绝/待补充来源。display_note=对应说明句单点（含待裁决说明句、
    # 诊断失败连击次数、到达路径副语），B2b 前端两处（区1/区5）消费同一字段，退役 deriveReviewDisplay。
    display_code: ReviewDisplayCode = ReviewDisplayCode.PENDING_DIAGNOSIS
    display_note: str = ""
    current_verdict: VerdictRead | None = None      # 站立结论（待裁决）
    verdict_history: list[VerdictRead] = Field(default_factory=list)  # 已裁决/已替代/失效轮次（新→旧）
    dialogue_messages: list[DialogueMessageRead] = Field(default_factory=list)  # 旧→新
    supplement_gaps_open: list[str] = Field(default_factory=list)  # 未闭合来源缺口（阻断再诊断）
    #: 已按 AI 建议采纳过多少次「建议修订」仍未通过。**纯事实，不是门禁**——
    #: 评审往复的终点只有「AI 判通过」与「人工撤回」两个，什么时候不值得再改由用户判断
    #: （2026-07-20 用户拍板废除原采纳链空转熔断）。界面据此给非阻断提示，不禁用任何入口。
    adopted_revise_rounds: int = 0
    #: 本条目的问题否决留痕（含已撤销者，新→旧）：用户裁定过「不是问题」的完整账目，界面可查。
    finding_vetoes: list[FindingVetoRead] = Field(default_factory=list)
    #: 人工确认背书（材料未记载该需求，由人确认它成立）。空＝没有背书过。
    #: 它是与来源要素并列的独立证据类别，不是来源要素的一种——界面必须分开显示。
    source_attestation: SourceAttestationRead | None = None
    #: 当前这次「旧结论失效」正是人工确认造成的——即来源缺口刚刚被确认闭合。
    #: 与 source_attestation 的区别：后者是粘性事实（背过书就一直在），本字段只在那一刻为真，
    #: 背书之后的任何一次普通修订都会让它转假。界面据此把说明句升格为醒目横幅（说明句仍取
    #: display_note，不另造文案），判据与说明句选句同一单点（见服务层同名派生）。
    attestation_closed_gap: bool = False
    available_actions: list[ActionFact] = Field(default_factory=list)


class DiagnosisRunProgressRead(BaseModel):
    """诊断批次轻量进度（批次只汇总过程状态，不新增业务事实）。"""

    run_ref: str
    item_refs: list[str] = Field(default_factory=list)
    diagnosis_mode: DiagnosisMode
    status: str  # running / completed
    completed_count: int = 0
    total_count: int = 0
    # 本批次内失败轮次数（issue #10 B2a，只增不改）：按 run 直接归因，分子=该批 processing_status
    # ∈{failed,not_diagnosable} 的轮次数。前端弃用以条目全局最新态猜测本 run 失败（结算窗口内被新批
    # 重诊则漏报、跨批遗留失败可误计）。批次收束后仍保留可查（契约守卫），failed_count 随之稳定。
    failed_count: int = 0
    next_action: str | None = None


class ItemReviewWorkspaceRead(BaseModel):
    """条目评审页面唯一工作区读视图（AEP-033；线程/会话条/动态流三投影的素材）。"""

    review_context_ref: str
    formation_context_ref: str
    workspace_version: str
    material_canvas: MaterialCanvasRead | None = None
    source_elements: list[RequirementElementRead] = Field(default_factory=list)
    review_items: list[ReviewRequirementItemRead] = Field(default_factory=list)
    diagnosis_options: list[DiagnosisMode] = Field(default_factory=list)
    diagnosis_runs: list[DiagnosisRunProgressRead] = Field(default_factory=list)
    available_operations: list[ActionFact] = Field(default_factory=list)
    confirmed_count: int = 0
    total_count: int = 0
    next_action: str | None = None


class ItemReviewDiagnosisCommand(BaseModel):
    """AEP-032 startDiagnosis 入参（批次级提交、条目级处理）。"""

    project_ref: str
    item_refs: list[str] = Field(default_factory=list)
    diagnosis_mode: str  # DiagnosisMode 稳定码（不可识别时业务拒绝，不走 422）
    workspace_version: str
    operator_ref: str
    idempotency_key: str


class ItemReviewDiagnosisRequestResult(BaseModel):
    """AEP-032 受理返回（受理立即返回；批次经 agent_run_ref 追踪，逐条目实时刷新）。"""

    status: str  # submitted / rejected_precheck
    review_context_ref: str | None = None
    agent_run_ref: str | None = None
    next_action: str | None = None


class VerdictAdjudicationCommand(BaseModel):
    """AEP-034 adjudicateVerdict 入参（v5 重定义：裁决对象=结论；副作用链后端原子执行）。"""

    project_ref: str
    item_ref: str
    round_ref: str
    decision: VerdictDecision
    selected_point_refs: list[str] | None = None  # 仅采纳 revise：所选修订点（None=全选）
    #: 仅采纳 revise：用户对所选点替换文本的改稿（{point_ref: 用户终稿}）。不传＝按 AI 原案采纳，
    #: 行为与本字段引入前完全一致。定位片段 find 不变，只换替换文本；空字符串会被拒（等同取消勾选）。
    point_edits: dict[str, str] | None = None
    reason: str | None = None                     # 拒绝必填（回复正文即理由）
    workspace_version: str
    operator_ref: str
    idempotency_key: str


class FindingVetoCommand(BaseModel):
    """AEP-116 否决/恢复一条诊断问题（用户裁定「这条不是问题」，或撤销该裁定）。

    action=veto 时给 finding_ref（用户正看着的那条发现项，服务端由它取指纹）；
    action=restore 时给 veto_ref（要撤销的那条留痕）。
    """

    project_ref: str
    item_ref: str
    action: str                    # veto / restore
    finding_ref: str | None = None
    veto_ref: str | None = None
    reason: str | None = None      # 否决理由（可选）
    operator_ref: str
    idempotency_key: str


class SourceAttestationCommand(BaseModel):
    """人工确认背书入参：材料里漏写了这条，人工确认它是真实需求。

    这是对「条目的依据必须能在材料里指出来」的**授权例外**，所以理由必填、操作者与时间
    全程留痕；服务端只登记背书事实，绝不代写任何材料锚点或引文。不设撤销动作——
    见服务层 attest_source 的说明。
    """

    project_ref: str
    item_ref: str
    reason: str                        # 必填：为什么它是真实需求
    operator_ref: str = Field(min_length=1)  # 必填：授权例外要能追到人，不许留空
    idempotency_key: str


class ReviewDialogueCommand(BaseModel):
    """AEP-095 reviewDialogue 入参（对话面：无斜杠自由文本领域零写入；
    2026-07-06 扩展：message 可含 /命令词，斜杠命令解释后派发既有端点逻辑）。"""

    project_ref: str
    item_ref: str
    message: str
    draft_ref: str | None = None  # 在途草案引用（迭代时带上）
    selected_item_refs: list[str] = Field(default_factory=list)  # 上下文：区1 勾选的诊断范围
    workspace_version: str
    operator_ref: str
    idempotency_key: str
    #: 这条命令是不是用户亲手输入的。页面自行发起的命令（如条目进入「待补充来源」态时
    #: 自动查一次候选来源）传 False：它不是用户说的话，不写演示留痕，否则每次进页面都会
    #: 多出一对用户从未输入过的气泡（冷审查 T20260718-demo-chat-transcript F2）。
    #: 缺省 True——不传即按用户输入处理，既有调用方与手敲命令的行为都不变。
    user_initiated: bool = True


class SourceCandidateRead(BaseModel):
    """为条目找候选来源读视图（issue #30；候选=同批次已确认、未链接到本条的要素投影）。

    element_ref 逐条取自服务算出的差集，content/source_quote 为要素事实（原文引文为登记依据），
    reason/rank 为 AI lane 的推荐理由与相关度排序。前端候选卡与一键登记接线属后续卡。
    """

    element_ref: str
    element_type: str = ""
    content: str = ""
    source_quote: str | None = None  # 首个原文引文（登记为来源时的依据；无锚点时为 None）
    reason: str = ""                 # AI 推荐理由
    rank: int = 0                    # 相关度排序（1 最相关）


class ReviewDialogueResult(BaseModel):
    """AEP-095 返回（四出口：解释 / 草案 / 轻量重评运行引用 / 斜杠命令回执）。"""

    outcome_type: DialogueOutcomeType
    explanation: str | None = None
    draft: DialogueMessageRead | None = None
    agent_run_ref: str | None = None  # reeval / 命令派发出队列型运行时返回
    next_action: str | None = None
    # ---- outcome_type=command 的解释回执（时间线审计）----
    command_word: str | None = None
    operation: str | None = None
    operation_label: str | None = None
    params_echo: dict | None = None
    message: str | None = None  # clarify / cannot_comply / 未知命令 / 校验失败的用户可见文案
    # ---- /找来源 命令的候选来源载荷（issue #30；非空即候选卡，前端凭此渲染，只加不改）----
    source_candidates: list[SourceCandidateRead] | None = None


class ItemConfirmationCommand(BaseModel):
    """AEP-037 确认写入入参（v5：仅覆盖确认直写路径保留用户面；理由必填）。"""

    project_ref: str
    item_ref: str
    workspace_version: str
    override: bool = False       # 覆盖确认（无视站立结论；理由必填）
    reason: str | None = None
    review_summary_ref: str | None = None  # 兼容字段（历史客户端）
    operator_ref: str
    idempotency_key: str


class ItemConfirmationResult(BaseModel):
    """确认写入结果或准入失败原因。"""

    status: str  # confirmed / rejected_precheck
    item_ref: str
    item_status: RequirementItemStatus
    next_action: str | None = None


class ItemWithdrawCommand(BaseModel):
    """人工撤回入参（待确认 → 已终止；理由必填）。"""

    project_ref: str
    item_ref: str
    workspace_version: str
    reason: str
    operator_ref: str
    idempotency_key: str


class ItemWithdrawResult(BaseModel):
    status: str  # terminated / rejected_precheck
    item_ref: str
    item_status: RequirementItemStatus
    next_action: str | None = None

