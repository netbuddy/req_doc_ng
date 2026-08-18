"""领域枚举 —— 跨线用稳定 ASCII 码（docs/40 shared/前端契约适配 §2；中文是展示层）。"""
from __future__ import annotations

from enum import Enum


class IntakeConclusion(str, Enum):
    """LDM-003.intake_conclusion（状态承载字段）。"""

    ACCEPTED = "accepted"
    RETURNED_FOR_SUPPLEMENT = "returned_for_supplement"
    EXCLUDED = "excluded"


class ModelJudgement(str, Enum):
    """来源接入判断类 LDM-015 的判定结论（状态机守卫取值）。"""

    ACCEPTABLE = "acceptable"
    INSUFFICIENT_CONTENT = "insufficient_content"
    UNCLEAR_ATTRIBUTION = "unclear_attribution"
    NO_ASSET_VALUE = "no_asset_value"
    JUDGEMENT_FAILED = "judgement_failed"


class IntakeRequestStatus(str, Enum):
    """AEP-001 submitTextIntake 受理结论。"""

    SUBMITTED_FOR_JUDGEMENT = "submitted_for_judgement"
    REJECTED_PRECHECK = "rejected_precheck"


class IntakeOutcome(str, Enum):
    """AEP-002 acceptIntakeJudgementResult 裁定结论。"""

    ACCEPTED = "accepted"
    RETURNED_FOR_SUPPLEMENT = "returned_for_supplement"
    EXCLUDED = "excluded"
    JUDGEMENT_FAILED = "judgement_failed"


class AgentRunStatus(str, Enum):
    """异步任务（AgentRun）状态（ADR-007 / 25-05）。"""

    QUEUED = "queued"
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AiRequestStage(str, Enum):
    """AI 请求生命周期阶段词表（链路回执条唯一来源，04A §2.1 增补）。

    阶段是后端事实：内联段经对话端点 SSE stage 帧发出，队列段由 AgentRun
    状态/事件映射（queued/started→running/终态）；「停滞」是前端按阈值
    派生的显示态，不进本词表。
    """

    ACCEPTED = "accepted"          # 已受理（命令词解析通过 / 请求合法）
    INTERPRETING = "interpreting"  # 命令解释 lane 调模型中
    DISPATCHING = "dispatching"    # 校验与派发中（含确定性回写）
    QUEUED = "queued"              # 已入队等待 worker
    RUNNING = "running"            # worker / 生成型 lane 模型执行中
    WRITING = "writing"            # 结果回写中


# ---- SCN-001-P02 需求要素初始识别与登记（分析转化服务）----


class MaterialParseStatus(str, Enum):
    """LDM-004.parse_status（状态承载字段，见 state-machines/材料解析.md）。"""

    PARSING = "parsing"            # 解析中（识别请求上下文承载；LDM-004 尚未创建）
    PARSED = "parsed"             # 已解析（成功 ∧ 存在要素）
    UNPROCESSABLE = "unprocessable"  # 不可继续处理（成功 ∧ 无可处理要素）


class ElementType(str, Enum):
    """LDM-005.element_type（语义类型，见 枚举字典.md）。"""

    FUNCTIONAL_REQUIREMENT = "functional_requirement"
    QUALITY_ATTRIBUTE = "quality_attribute"
    CONSTRAINT = "constraint"
    DATA_REQUIREMENT = "data_requirement"
    INTERFACE_REQUIREMENT = "interface_requirement"
    GOAL = "goal"
    SCENARIO = "scenario"
    TERM = "term"
    ASSUMPTION = "assumption"
    BUSINESS_RULE = "business_rule"
    ROLE = "role"
    EXTERNAL_SYSTEM = "external_system"


class ElementProcessStatus(str, Enum):
    """LDM-005.process_status —— 人工确认生命周期（见 state-machines/需求要素.md）。

    模型对内容的裁定与置信度降级为证据字段（ModelVerdict），不作为状态取值。
    """

    PENDING_CONFIRMATION = "pending_confirmation"  # 待确认（初始态；AI 复核/修订迭代为会话事实，不迁状态）
    CONFIRMED = "confirmed"                        # 已确认（进入 SCN-002 唯一入口；含采纳修订稿即确认）
    REVOKED = "revoked"                            # 已撤销（保留识别事实）


class ModelVerdict(str, Enum):
    """模型对要素内容的裁定（证据字段，只做排序与预标记，不作为状态）。"""

    PROCESSABLE = "processable"                            # 可处理
    SUSPECTED_NEEDS_SUPPLEMENT = "suspected_needs_supplement"  # 疑似需补充
    # 稳定码保持 suspected_noise 不变（跨线契约）；用户可见标签 2026-07-25 统一为「建议剔除」
    SUSPECTED_NOISE = "suspected_noise"


class NoiseTriage(str, Enum):
    """人工对「建议剔除候选」的处置标记（与 ModelVerdict 并存，不改写模型证据）。

    空值＝尚未处置：model_verdict=suspected_noise 的知识项默认待在候选区。
    RESTORED＝人工判定模型误杀，撤回到正常列表。移回候选区＝清空本标记回到空值，
    「曾撤回又移回」的事实由 LDM-005 变更历史承载，故不设第三个取值。
    模型意见与人工裁定并存，是 AI 效能统计「模型误杀率」的原料。
    """

    RESTORED = "restored"  # 已撤回到正常列表


class ReviewConclusion(str, Enum):
    """AI 复核（核要素）对单条要素的结论（分析中裁定矩阵的列）。"""

    PASS = "pass"                    # 可通过
    NEEDS_REVISION = "needs_revision"  # 须修订（附修订稿）
    FAIL = "fail"                    # 不可通过


class RecognitionRequestStatus(str, Enum):
    """AEP-021 submitElementRecognition 受理结论。"""

    SUBMITTED_FOR_RECOGNITION = "submitted_for_recognition"
    REJECTED_PRECHECK = "rejected_precheck"


# ---- SCN-002-P01 需求要素批次条目化与形成（条目形成服务 / 需求条目服务）----


class RequirementItemStatus(str, Enum):
    """LDM-007.status（状态承载字段，见 state-machines/需求条目.md）。

    本切片只落 AEP-038 创建待确认与 AEP-036 待确认字段修订自环；
    confirmed/superseded/terminated 由 SCN-003 承接（此处仅留稳定码）。
    """

    PENDING_CONFIRMATION = "pending_confirmation"  # 待确认（AEP-038 唯一产物）
    CONFIRMED = "confirmed"                        # 确认态（SCN-003）
    SUPERSEDED = "superseded"                      # 被替代（SCN-003）
    TERMINATED = "terminated"                      # 已终止（SCN-003）


class RequirementItemType(str, Enum):
    """LDM-007.req_type（功能/质量/约束/数据/接口）。"""

    FUNCTIONAL = "functional"
    QUALITY = "quality"
    CONSTRAINT = "constraint"
    DATA = "data"
    INTERFACE = "interface"


class VerificationMethod(str, Enum):
    """LDM-007.verification_method 取值（29148 口径四方法；允许组合多选，落库逗号连接）。

    方法选择属工程判断：模型可提建议初稿（AEP-036 可修订），不适用"无法归纳为空"口径。
    """

    TEST = "test"
    DEMONSTRATION = "demonstration"
    INSPECTION = "inspection"
    ANALYSIS = "analysis"


class ItemPriority(str, Enum):
    """LDM-007.priority（高/中/低；范围外语义归发布范围机制，不进本枚举）。

    仅人工设定与修订（无模型通道），留痕经 AEP-036 修订记录。
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# 需求表达类要素 → 条目类型（准入 + 类型映射是领域事实，不交给模型；
# 条目形成服务准入过滤与条目档案注入共用同一来源）
ELEMENT_TO_ITEM_TYPE: dict[str, str] = {
    ElementType.FUNCTIONAL_REQUIREMENT.value: RequirementItemType.FUNCTIONAL.value,
    ElementType.QUALITY_ATTRIBUTE.value: RequirementItemType.QUALITY.value,
    ElementType.CONSTRAINT.value: RequirementItemType.CONSTRAINT.value,
    ElementType.DATA_REQUIREMENT.value: RequirementItemType.DATA.value,
    ElementType.INTERFACE_REQUIREMENT.value: RequirementItemType.INTERFACE.value,
}

# 可条目化谓词单一来源（两翼谓词之一，见 40 增补《知识项两翼框架与命名口径》§2）：
# = ELEMENT_TO_ITEM_TYPE 的键集。服务层禁止再写本地 5 类白名单，一律 import 本常量。
ITEMIZABLE_ELEMENT_TYPES: frozenset[str] = frozenset(ELEMENT_TO_ITEM_TYPE)


class KnowledgeCategory(str, Enum):
    """知识类属（两翼，派生维度不落库；见 40 增补《知识项两翼框架与命名口径》）。"""

    REQUIREMENT = "requirement"   # 需求领域知识：规定性陈述（optative）
    BUSINESS = "business"         # 业务领域知识：描述性世界知识（indicative）


# 两翼归属谓词单一来源（全函数，每类唯一归属）。与 ITEMIZABLE_ELEMENT_TYPES（可条目化）
# 是两个独立谓词：可条目化 ⊂ 需求翼；目标/场景属需求翼但不可条目化。翼调整 = 只改本映射。
# P3 落地 business_rule 时追加 BUSINESS_RULE: BUSINESS（映射全覆盖防漏测试强制）。
ELEMENT_KNOWLEDGE_CATEGORY: dict[ElementType, KnowledgeCategory] = {
    ElementType.FUNCTIONAL_REQUIREMENT: KnowledgeCategory.REQUIREMENT,
    ElementType.QUALITY_ATTRIBUTE: KnowledgeCategory.REQUIREMENT,
    ElementType.CONSTRAINT: KnowledgeCategory.REQUIREMENT,
    ElementType.DATA_REQUIREMENT: KnowledgeCategory.REQUIREMENT,
    ElementType.INTERFACE_REQUIREMENT: KnowledgeCategory.REQUIREMENT,
    ElementType.GOAL: KnowledgeCategory.REQUIREMENT,
    ElementType.SCENARIO: KnowledgeCategory.REQUIREMENT,
    ElementType.TERM: KnowledgeCategory.BUSINESS,
    ElementType.ASSUMPTION: KnowledgeCategory.BUSINESS,
    ElementType.BUSINESS_RULE: KnowledgeCategory.BUSINESS,
    ElementType.ROLE: KnowledgeCategory.BUSINESS,
    ElementType.EXTERNAL_SYSTEM: KnowledgeCategory.BUSINESS,
}


def knowledge_category_of(element_type: "ElementType | str") -> str:
    """派生要素的两翼归属稳定码（"requirement"|"business"）。派生投影，不落库。

    组装读模型时统一经本函数派生（schemas/服务/追溯一处来源）。
    """
    et = element_type if isinstance(element_type, ElementType) else ElementType(element_type)
    return ELEMENT_KNOWLEDGE_CATEGORY[et].value


class ItemizationScopeType(str, Enum):
    """AEP-038 批次范围（单选=批次大小为 1，不是另一套流程）。"""

    ALL_ELIGIBLE = "all_eligible"
    SELECTED_ELEMENTS = "selected_elements"
    SINGLE_ELEMENT = "single_element"


class ItemizationResultStatus(str, Enum):
    """ItemizationResultRead.result_status —— 逐要素归因结论。"""

    CREATED = "created"    # 创建待确认 LDM-007
    BLOCKED = "blocked"    # 准入/锚点/裁定拦截停靠
    FAILED = "failed"      # 模型格式化失败/不可承接
    SKIPPED = "skipped"    # 不在批次范围或已形成条目


class ItemRevisionMode(str, Enum):
    """AEP-036 字段修订方式。"""

    MANUAL = "manual"
    ACCEPT_SUGGESTION = "accept_suggestion"
    REVISE_AND_ACCEPT_SUGGESTION = "revise_and_accept_suggestion"
    REJECT_SUGGESTION = "reject_suggestion"


class RecognitionOutcome(str, Enum):
    """AEP-022 acceptRecognitionResult 裁定结论。"""

    REGISTERED = "registered"                          # 可登记（写 LDM-004+LDM-005）
    NO_PROCESSABLE_ELEMENTS = "no_processable_elements"  # 无可处理要素（只写 LDM-004）
    RECOGNITION_FAILED = "recognition_failed"           # 识别失败停靠（状态不迁移）


# ---- SCN-003 需求条目评审与确认（条目评审服务 / 需求条目服务）----


class DiagnosisMode(str, Enum):
    """AEP-032 请求诊断参数·诊断模式（只决定 AI 诊断上下文范围与检查重点，不分流规则诊断）。"""

    QUICK = "quick"
    STANDARD = "standard"
    COMPREHENSIVE = "comprehensive"
    INCREMENTAL = "incremental"


class DiagnosisProcessingStatus(str, Enum):
    """LDM-009 诊断轮次·条目诊断处理状态（SCN-003 §5/§7 逐条目结果矩阵）。"""

    DIAGNOSING = "diagnosing"            # 诊断中（不创建正式结论）
    COMPLETED = "completed"              # 已完成诊断（承载诊断结论与发现项）
    NOT_DIAGNOSABLE = "not_diagnosable"  # 未能进行诊断（准入/版本/上下文原因）
    FAILED = "failed"                    # 诊断失败（模型失败/超时/结果不可承接）


class ReviewFindingType(str, Enum):
    """LDM-009 诊断发现项类型（诊断结论分类）。"""

    SOURCE_INCONSISTENCY = "source_inconsistency"
    AMBIGUOUS_EXPRESSION = "ambiguous_expression"
    UNTESTABLE = "untestable"
    MISSING_FIELD = "missing_field"
    NO_BLOCKER = "no_blocker"


class SuggestedDisposition(str, Enum):
    """诊断发现项·建议处置（建议不等于执行；执行须经 P02 复核裁定）。"""

    CONFIRM = "confirm"
    REVISE = "revise"
    REDIAGNOSE = "rediagnose"
    SUPPLEMENT_SOURCE = "supplement_source"
    NONE = "none"


class FindingReviewDecision(str, Enum):
    """【冻结历史口径】逐发现项复核判断（v5 起废除；存量记录只读，不再新写）。"""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    NEEDS_IMPROVEMENT = "needs_improvement"


class DiagnosisTrigger(str, Enum):
    """AEP-032 触发方式（v5：用户提交 / 修订后链式自动增量 / 对话轻量重评）。"""

    USER_SUBMIT = "user_submit"
    REVISION_CHAINED = "revision_chained"
    DIALOGUE_REEVAL = "dialogue_reeval"


class VerdictKind(str, Enum):
    """LDM-009 结论状态字（v5 裁决对象；聚合一致性由服务端守卫）。"""

    PASS = "pass"              # 建议通过（采纳即确认）
    REVISE = "revise"          # 建议修订（附修订点；采纳=应用所选点+自动增量重诊）
    WITHDRAW = "withdraw"      # 建议撤回（采纳=条目终止）
    SUPPLEMENT = "supplement"  # 建议补充来源（采纳=登记缺口）


class VerdictDecision(str, Enum):
    """AEP-034 结论裁决（v5 重定义：裁决对象=结论，非发现项）。"""

    ADOPTED = "adopted"
    REJECTED = "rejected"


class DialogueOutcomeType(str, Enum):
    """AEP-095 对话面产物类型（解释=无副作用；草案=待采纳；重评=改判唯一通道；
    命令=斜杠命令解释与派发回执，2026-07-06 扩展）。"""

    EXPLANATION = "explanation"
    DRAFT = "draft"
    REEVAL = "reeval"
    COMMAND = "command"


class ReviewItemStatus(str, Enum):
    """条目评审派生显示态（v5：状态机文档 §3；非持久化，由 AEP-033 读视图派生）。"""

    NO_VERDICT = "no_verdict"                        # 无结论（未诊断/结论作废/轮次失效）
    DIAGNOSING = "diagnosing"                        # 诊断中（含修订后自动增量）
    AWAITING_ADJUDICATION = "awaiting_adjudication"  # 待裁决（当前版本有有效结论）
    CONFIRMED = "confirmed"
    TERMINATED = "terminated"


class ReviewDisplayCode(str, Enum):
    """条目评审用户可见显示态封闭集（状态机文档 §3；issue #10 B2a：AEP-033 读视图单点输出）。

    把粗粒 `ReviewItemStatus.NO_VERDICT` 按最近轮次事实二次细分为四格，使
    「进行过诊断的条目永不回到待诊断」这一到达路径承诺成为后端单一来源
    （原前端 `deriveReviewDisplay` 代管的确定性分桶下沉；B2b 消费本枚举后前端派生退役）。
    诊断中/待裁决/已确认/已终止四码与 ReviewItemStatus 同值同义（细分只发生在 NO_VERDICT）。
    """

    DIAGNOSING = "diagnosing"
    AWAITING_ADJUDICATION = "awaiting_adjudication"
    CONFIRMED = "confirmed"
    TERMINATED = "terminated"
    PENDING_DIAGNOSIS = "pending_diagnosis"    # 待诊断：当前版本无任何诊断轮次
    DIAGNOSIS_FAILED = "diagnosis_failed"      # 诊断失败：最新轮失败（未失效，可重试）
    VERDICT_REJECTED = "verdict_rejected"      # 结论已拒绝：最新终态轮为人工拒绝（待人驱动）
    SUPPLEMENT_PENDING = "supplement_pending"  # 待补充来源：来源缺口未闭合（阻断再诊断）


# ---- 需求质量诊断器（v2 需求管理工作台签名件；扩 item_diagnosis 契约，见
#      docs/proposals/requirement-management-redesign/02_质量诊断引擎与契约设计.md）----
# 质量元数据为诊断轮次旁路产物：降级不拒收，既有 verdict/finding 聚合守卫不受影响。


class QualityDimension(str, Enum):
    """需求陈述 6 维质量口径（对齐 ISO/IEC 29148；诊断器评分维度 = v2 质量画像雷达）。"""

    UNAMBIGUOUS = "unambiguous"   # 无歧义
    VERIFIABLE = "verifiable"     # 可验证
    SINGULAR = "singular"         # 原子性
    COMPLETE = "complete"         # 完整性
    CONSISTENT = "consistent"     # 一致性
    TRACEABLE = "traceable"       # 可追溯（由来源语义对齐分 / source_drift 佐证）


class QualitySeverity(str, Enum):
    """诊断发现项严重度（标注三级配色；服务端 clamp/归一化）。"""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class RequirementQualityRule(str, Enum):
    """requirement smell / INCOSE / EARS 规则码——既有 ReviewFindingType 的细化（非替代）。

    码值即前端 chip 文本，与交互基准原型逐字对齐；聚合守卫仍以 finding_type 粗类为准。
    """

    INCOSE_R7 = "INCOSE-R7"            # 模糊 / 不可测量量词
    INCOSE_R21 = "INCOSE-R21"          # 可选逃逸子句
    MODAL_WEAK = "MODAL-WEAK"          # 弱化情态（可以 / 尽量）
    SMELL_UNDEF = "SMELL-UNDEF"        # 未定义阈值 / 术语
    SMELL_COMPOUND = "SMELL-COMPOUND"  # 复合动作（违反原子性）
    SMELL_PASSIVE = "SMELL-PASSIVE"    # 被动语态
    SMELL_UNIVERSAL = "SMELL-UNIVERSAL"  # 全称量词
    EARS_INCOMPLETE = "EARS-INCOMPLETE"  # EARS 要件缺失（触发 / 主体 / 响应 / 可观测结果）
    SRC_DRIFT = "SRC-DRIFT"            # 来源偏离 / 不一致（复用 source_drift）
    BIZ_RULE_CONFLICT = "BIZ-RULE-CONFLICT"  # 与业务知识矛盾（P7；细化自 source_inconsistency，单列证据）


class EarsPattern(str, Enum):
    """EARS 句式类型（改写脚手架的模式标签）。"""

    UBIQUITOUS = "ubiquitous"       # 泛在
    EVENT_DRIVEN = "event_driven"   # 事件驱动（WHEN）
    STATE_DRIVEN = "state_driven"   # 状态驱动（WHILE）
    UNWANTED = "unwanted"           # 非期望行为（IF-THEN-SHALL NOT）
    OPTIONAL = "optional"           # 可选特性（WHERE）
    COMPLEX = "complex"             # 复合


# ---- SCN-005 文档内容索引与发布制品生成（文档编排服务 / 导出执行服务）----


class DocumentStatus(str, Enum):
    """LDM-014.status（文档级状态承载；索引/定稿/基线子对象各有自身状态）。"""

    INDEX_DRAFT = "index_draft"          # 编排中（尚未保存过可用索引）
    INDEX_BLOCKED = "index_blocked"      # 索引受阻（模板问题/必填缺失/准入不通过）
    INDEX_READY = "index_ready"          # 索引形成，可生成 Markdown
    MARKDOWN_DRAFT = "markdown_draft"    # Markdown 中间稿阶段（窗口微调中）
    MARKDOWN_FINALIZED = "markdown_finalized"  # Markdown 定稿，可导出
    BASELINE_PUBLISHED = "baseline_published"  # 已形成发布基线（只读复核）


class SlotAssetType(str, Enum):
    """文档内容索引条目承载的资产类型（追溯依据不入文档内容，仅派生绑定）。"""

    REQUIREMENT_ITEM = "requirement_item"  # 确认态 LDM-007
    MATERIAL = "material"                  # 支撑材料 LDM-002
    CHART = "chart"                        # 受控图表 LDM-012（status=confirmed）
    BOILERPLATE = "boilerplate"            # 模板自带文本（非治理资产）


class EditImpact(str, Enum):
    """P02 窗口编辑影响分类（§5.3 编辑影响处理规则）。"""

    DOC_EXPRESSION = "doc_expression"      # 纯文档表达微调 → 可进定稿
    CONFIRMED_ITEM = "confirmed_item"      # 触及确认态 LDM-007 → 待修订清单/回流
    INDEX_STRUCTURE = "index_structure"    # 触及章节结构 → 回 P01
    NO_SOURCE_FACT = "no_source_fact"      # 无来源新事实 → 不可定稿
    OTHER_ASSET = "other_asset"            # 触及图表/追溯等其它正式资产 → 回对应资产流程


class PatchStatus(str, Enum):
    """预览编辑补丁处置状态（未定稿前不是正式资产）。"""

    PENDING = "pending"            # 已记录未定稿
    FINALIZED = "finalized"        # 随定稿写入 Markdown 定稿版本
    REFLOWED = "reflowed"          # 已确认回流条目修订（生成新的待确认 LDM-007）
    DISCARDED = "discarded"        # 用户撤销/重新生成丢弃


class MarkdownDraftStatus(str, Enum):
    """Markdown 中间稿状态。"""

    DRAFT = "draft"                # 可编辑中间稿
    FINALIZED = "finalized"        # 定稿版本（可导出）
    SUPERSEDED = "superseded"      # 被重新生成/索引调整替代，需重新生成
    AWAITING_ITEM_REVISION = "awaiting_item_revision"  # 等待条目修订收束，不可定稿


class DocxExportStatus(str, Enum):
    """候选 docx 导出件状态（导出成功≠发布）。"""

    CONVERTING = "converting"          # 转换中（AgentRun 承载）
    SUCCEEDED = "succeeded"            # 候选件已登记，待检查
    FAILED = "failed"                  # 转换失败（保留原因与重试/降级入口）
    CHECK_REJECTED = "check_rejected"  # 用户检查不通过
    BASELINE_CONFIRMED = "baseline_confirmed"  # 已被确认为发布基线候选件
    MANUAL_FALLBACK = "manual_fallback"        # 人工降级登记（非系统转换成功）


# ---- SCN-004 受控图表确认与追溯关系成立（图表协同服务 / 追溯图谱模块 / 问题项模块）----


class ChartStatus(str, Enum):
    """LDM-012.status（状态承载字段，见 枚举字典.md；作废态为 SCN-004 §5.5 补充）。"""

    DRAFT = "draft"                                # 草稿中（P01 编辑循环）
    PENDING_CONFIRMATION = "pending_confirmation"  # 待确认（P02 核对发起后冻结编辑）
    CONFIRMED = "confirmed"                        # 已确认（受控图表，可被文档编排消费）
    RETURNED_FOR_REVISION = "returned_for_revision"  # 退回修订（须 resume 后重回 P01）
    VOIDED = "voided"                              # 作废（终态；相关追溯失效）


class ChartKind(str, Enum):
    """LDM-012.chart_kind（表格/图形/UML，见 枚举字典.md；由 chart_type 派生写入）。"""

    TABLE = "table"
    GRAPHIC = "graphic"
    UML = "uml"


class ChartType(str, Enum):
    """LDM-012 图表类型（SCN-004 §4.2 受控图表类型）。"""

    FLOWCHART = "flowchart"
    STATE_DIAGRAM = "state_diagram"
    RELATION_DIAGRAM = "relation_diagram"
    SEQUENCE_DIAGRAM = "sequence_diagram"
    DECISION_TABLE = "decision_table"
    COMPARISON_TABLE = "comparison_table"


class ChartFormat(str, Enum):
    """LDM-012.format（受控文本化表达方式，见 枚举字典.md）。"""

    MERMAID = "mermaid"
    PLANTUML = "plantuml"
    MARKDOWN_TABLE = "markdown_table"


class ChartSourceKind(str, Enum):
    """图表创建来源类别（本迭代仅确认态需求条目；其余留稳定码）。"""

    REQUIREMENT_ITEM = "requirement_item"
    SUPPORTING_CONTENT = "supporting_content"  # 预留（后续增量）
    DOCUMENT_SECTION = "document_section"      # 预留（后续增量）


class TraceLinkStatus(str, Enum):
    """LDM-013.status（见 枚举字典.md；SCN-004 用语对照：已建立≈预建立、
    已确认有效≈有效、待补全≈可疑待复核）。"""

    PRE_ESTABLISHED = "pre_established"            # 预建立（不得作为正式追溯依据）
    EFFECTIVE = "effective"                        # 有效（随图表确认同批正式确立）
    SUSPECT_PENDING_REVIEW = "suspect_pending_review"  # 可疑待复核（来源不足/退回修订）
    INVALID = "invalid"                            # 失效（终态）


class TraceRelationType(str, Enum):
    """LDM-013.relation_type 空间维度关系类型（见 枚举字典.md；本迭代仅 CHART）。"""

    SOURCE = "source"
    REVIEW_ADOPTION = "review_adoption"
    CHART = "chart"
    DOCUMENT_REFERENCE = "document_reference"
    SUPPORTING_BASIS = "supporting_basis"


class ChartFindingType(str, Enum):
    """图文核对发现项类型（SCN-004 §5.2 AI 过程输出）。"""

    SUSPECTED_HIDDEN_REQUIREMENT = "suspected_hidden_requirement"
    CHART_TEXT_CONFLICT = "chart_text_conflict"
    SOURCE_COVERAGE_GAP = "source_coverage_gap"
    TRACE_GAP = "trace_gap"
    NO_OBVIOUS_ISSUE = "no_obvious_issue"
    UNDETERMINABLE = "undeterminable"


class ChartFindingDecision(str, Enum):
    """核对发现项用户复核判断（拒绝必须记录理由）。"""

    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ChartVerificationProcessingStatus(str, Enum):
    """图文核对轮次处理状态（AI 失败不得降级为纯人工确认）。"""

    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"


class ChartSuggestionHandling(str, Enum):
    """AI 图表源码建议处理命令（SCN-004-P01-N09；拒绝不更新 LDM-012）。"""

    ADOPT = "adopt"
    REVISE_AND_ADOPT = "revise_and_adopt"
    REJECT = "reject"


class ChartSourceChangeOrigin(str, Enum):
    """图表源码变更依据（LDM-012 草稿版本留痕）。"""

    MANUAL = "manual"
    AI_ADOPTED = "ai_adopted"
    AI_REVISED_ADOPTED = "ai_revised_adopted"
    AI_INITIAL = "ai_initial"  # 创建初稿自动应用（生成结果仍经受控校验）


class ChartSuggestionRequestKind(str, Enum):
    """AI 建议请求类别：创建初稿（结果自动应用为初稿）/ 修订建议（结果待人工采纳）。"""

    INITIAL = "initial"
    REVISION = "revision"


class IssueType(str, Enum):
    """LDM-011.issue_type（本迭代仅图表核对来源；闭环归 SCN-006）。"""

    HIDDEN_REQUIREMENT = "hidden_requirement"
    CONFLICT = "conflict"
    GAP = "gap"
    INSUFFICIENT_SOURCE = "insufficient_source"
    OTHER = "other"


class IssueStatus(str, Enum):
    """LDM-011.status（本迭代仅创建 pending；处置流转归 SCN-006）。"""

    PENDING = "pending"
    PROCESSING = "processing"
    BLOCKED = "blocked"
    CLOSED = "closed"
