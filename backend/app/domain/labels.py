"""领域枚举的中文标签与提示词语义指南（唯一来源，供提示词模板动态渲染）。

对齐 docs/40 shared/data-models/枚举字典.md；提示词不得再手写枚举清单，
新增/调整枚举值时只改枚举与本文件，模板经渲染自动同步（渲染测试兜底覆盖率）。
"""
from __future__ import annotations

from app.domain.enums import (
    ChartFindingType,
    ChartFormat,
    ChartType,
    DiagnosisMode,
    EarsPattern,
    ELEMENT_KNOWLEDGE_CATEGORY,
    ElementType,
    ItemPriority,
    KnowledgeCategory,
    ModelJudgement,
    ModelVerdict,
    QualityDimension,
    QualitySeverity,
    RequirementItemType,
    RequirementQualityRule,
    ReviewConclusion,
    ReviewFindingType,
    VerdictKind,
    VerificationMethod,
)

# ---- LDM-005.element_type：中文名 + 稳定码（枚举字典 §element_type）----
ELEMENT_TYPE_LABELS: dict[ElementType, str] = {
    ElementType.FUNCTIONAL_REQUIREMENT: "功能需求",
    ElementType.QUALITY_ATTRIBUTE: "质量属性",
    ElementType.CONSTRAINT: "约束",
    ElementType.DATA_REQUIREMENT: "数据需求",
    ElementType.INTERFACE_REQUIREMENT: "接口需求",
    ElementType.GOAL: "目标",
    ElementType.SCENARIO: "场景",
    ElementType.TERM: "术语",
    ElementType.ASSUMPTION: "假设",
    ElementType.BUSINESS_RULE: "业务规则",
    ElementType.ROLE: "角色",
    ElementType.EXTERNAL_SYSTEM: "外部系统",
}

# ---- 抽取显著性判据 / 归类提示（03 §1.1 术语/角色/外部系统登记门槛；04 §2 业务规则归类）----
# 单一来源：判据/提示文本只改这里，经 partials/element_salience.jinja2 渲染（禁模板手写）。
ELEMENT_SALIENCE: dict[ElementType, str] = {
    ElementType.TERM: (
        "只登记满足以下任一条件的术语：材料中给出了定义或解释的；项目/领域特有而非通用词汇的；"
        "同一材料中用法存在歧义或前后不一致的。不要登记仅仅出现过的普通领域名词、通用技术词汇或日常用语。"
    ),
    ElementType.ROLE: (
        "只登记材料中承担了职责、发起了动作或被赋予权限的参与者；不要为每次提及重复登记同一角色。"
    ),
    ElementType.EXTERNAL_SYSTEM: (
        "只登记与本系统存在交互（数据交换、调用、依赖）的外部系统；不要登记仅作背景提及的系统名。"
    ),
    ElementType.BUSINESS_RULE: (
        "来自业务侧的政策、审批规则、计算/费率规则、合规或监管规定。它描述“业务上必须如此”，"
        "而非系统行为。遇到此类陈述优先归 business_rule，不要归入约束（约束面向设计/实现限制）"
        "或功能需求（功能需求面向系统行为）。业务规则可作为后续约束/功能需求条目的来源依据。"
    ),
}


def element_type_entries() -> list[dict[str, str]]:
    """模板变量：[{code, label, category}]，顺序与枚举声明一致。

    category = 两翼归属稳定码（派生自 ELEMENT_KNOWLEDGE_CATEGORY 单一来源）。
    """
    entries: list[dict[str, str]] = []
    for t in ElementType:
        entry: dict[str, str] = {
            "code": t.value,
            "label": ELEMENT_TYPE_LABELS[t],
            "category": ELEMENT_KNOWLEDGE_CATEGORY[t].value,
        }
        if t in ELEMENT_SALIENCE:
            entry["salience"] = ELEMENT_SALIENCE[t]
        entries.append(entry)
    return entries


# ---- 全局检索资产大类 → 中文组头（单一来源，供 SearchService 分组组头，01 §6.2）----
# 术语用"知识项"（两翼新词，非"要素"）；前端组头直接取 API 返回的 label，不复制此映射。
SEARCH_ENTITY_GROUP_LABELS: dict[str, str] = {
    "requirement_item": "需求条目",
    "element": "知识项",
    "chart": "图表",
    "document": "文档",
    "material": "材料",
}


# ---- 知识类属（两翼）：中文名 + 短名 + 语气提示（枚举字典 §knowledge_category；40 增补 §2）----
KNOWLEDGE_CATEGORY_LABELS: dict[KnowledgeCategory, str] = {
    KnowledgeCategory.REQUIREMENT: "需求领域知识",
    KnowledgeCategory.BUSINESS: "业务领域知识",
}
KNOWLEDGE_CATEGORY_SHORT_LABELS: dict[KnowledgeCategory, str] = {
    KnowledgeCategory.REQUIREMENT: "需求知识",
    KnowledgeCategory.BUSINESS: "业务知识",
}
_KNOWLEDGE_CATEGORY_HINTS: dict[KnowledgeCategory, str] = {
    KnowledgeCategory.REQUIREMENT: "规定性陈述——系统应当如何",
    KnowledgeCategory.BUSINESS: "描述性世界知识——领域本来如此",
}

# 话语特征判别指引（01 §5）：解决"归哪翼/哪类"，与显著性判据（P3 解决"该不该登记"）分工。
KNOWLEDGE_DISCOURSE_GUIDANCE = (
    "判别提示：材料中的陈述若在**描述世界本来如此**——定义性（如“结算周期指从流水截止到"
    "出具报告的区间”）、分类性（如“用户分为操作员和管理员”）、关系性（如“订单包含多个订单项”）、"
    "规则性（如“单笔超 5 万须二级审批”）——归业务领域知识；若在**规定系统应当如何**——功能性"
    "（如“系统应支持自动对账”）、性能性（如“响应不超过 2 秒”）、约束性（如“必须符合等保三级”）、"
    "目标性（如“要提升结算时效”）——归需求领域知识。"
)


def knowledge_category_entries() -> list[dict[str, str]]:
    """模板变量：[{code, label, short, hint}]，顺序与枚举声明一致（需求翼在前）。"""
    return [
        {
            "code": c.value,
            "label": KNOWLEDGE_CATEGORY_LABELS[c],
            "short": KNOWLEDGE_CATEGORY_SHORT_LABELS[c],
            "hint": _KNOWLEDGE_CATEGORY_HINTS[c],
        }
        for c in KnowledgeCategory
    ]


# ---- LDM-007.req_type：中文名 + 稳定码（枚举字典 §req_type）----
REQUIREMENT_ITEM_TYPE_LABELS: dict[RequirementItemType, str] = {
    RequirementItemType.FUNCTIONAL: "功能需求",
    RequirementItemType.QUALITY: "质量属性",
    RequirementItemType.CONSTRAINT: "约束",
    RequirementItemType.DATA: "数据需求",
    RequirementItemType.INTERFACE: "接口需求",
}


def requirement_item_type_entries() -> list[dict[str, str]]:
    """模板变量：[{code, label}]，顺序与枚举声明一致。"""
    return [{"code": t.value, "label": REQUIREMENT_ITEM_TYPE_LABELS[t]} for t in RequirementItemType]


# ---- AEP-036 可修订字段（单一来源：服务白名单与提示词字段清单均由此派生）----
# 顺序即语义分层：陈述内容字段在前（修订触发达标重诊），属性字段在后（仅留痕）。
ITEM_REVISION_FIELD_GUIDE: list[dict[str, str]] = [
    {"code": "expression", "label": "条目表达", "hint": "一句完整的需求表述（内容修订）"},
    {"code": "req_type", "label": "条目类型", "hint": "取条目类型稳定码"},
    {"code": "curation_note", "label": "内容整理说明", "hint": "形成时的整理口径（内容修订）"},
    {"code": "boundary_note", "label": "条目边界说明", "hint": "与相邻条目的边界（内容修订）"},
    {"code": "source_element_refs", "label": "条目来源",
     "hint": "本条目来源要素 id 的 JSON 数组字符串（如 [\"<id>\"]），仅接受同批次已确认要素；"
             "登记即内容修订（触发旧诊断轮失效）。评审页出口卡与对话通道均可登记，同受门禁校验"},
    {"code": "verification_method", "label": "验证方式",
     "hint": f"多选逗号分隔，取 {'/'.join(m.value for m in VerificationMethod)}"},
    {"code": "verification_note", "label": "验收准则", "hint": "可观察的验收口径"},
    {"code": "priority", "label": "条目优先级",
     "hint": f"取 {'/'.join(p.value for p in ItemPriority)}（仅人工设定）"},
]

ITEM_REVISION_FIELD_LABELS: dict[str, str] = {
    entry["code"]: entry["label"] for entry in ITEM_REVISION_FIELD_GUIDE
}

# ---- 借修订表落库但并非「把某字段从旧值改成新值」的记录：人工确认背书即此类 ----
# 背书为零迁移借用 ldm007_item_revision 落库（before=""、after=已人工确认…、field_key 如下），
# 但它一个字段都没改。这类 field_key 不计入内容修订序号、不进诊断提示词、不计入修订次数、
# 渲染走白话特判。单一来源：版本锚/提示词/计数三处消费引用本集，禁散写字面量。
# 与 _ATTRIBUTE_FIELDS（item_formation.py，真实字段编辑但不改陈述内容）语义分列——后者仍是
# 真修订：仍进诊断提示词、仍计入修订次数，只是不推进内容修订序号。两集只在版本锚一处重合。
SOURCE_ATTESTATION_FIELD_KEY = "source_attestation"
NON_REVISION_FIELD_KEYS: frozenset[str] = frozenset({SOURCE_ATTESTATION_FIELD_KEY})


# ---- 模型对内容的裁定（证据预标记，不是状态）----
# 本表是写给模型看的提示词材料（识别 lane 由它渲染裁定清单）。同一套判据前端另有一份面向用户的
# 说法：`frontend/src/view-models/requirement-analysis.ts` 的 MODEL_VERDICT_META.hint，在模型
# 漏给逐条理由时兜底显示。改判据须两处同步。两份文本有意不逐字一致：每条 hint 后半句是给模型的
# 指令（如「无法确定时也用它」「仍要登记，交由人工裁定」），只属本表，不进用户界面。
MODEL_VERDICT_GUIDE: list[dict[str, str]] = [
    {"code": ModelVerdict.PROCESSABLE.value, "label": "可处理",
     "hint": "有原文依据、表达可用；无法确定时也用它"},
    {"code": ModelVerdict.SUSPECTED_NEEDS_SUPPLEMENT.value, "label": "疑似需补充",
     "hint": "有依据但信息不完整，可能需要补充来源材料"},
    {"code": ModelVerdict.SUSPECTED_NOISE.value, "label": "建议剔除",
     "hint": "寒暄、下期范围等不承载需求信息的内容；仍要登记，交由人工裁定"},
]

# ---- AI 复核结论（供人工裁定，不直接生效）----
REVIEW_CONCLUSION_GUIDE: list[dict[str, str]] = [
    {"code": ReviewConclusion.PASS.value, "label": "可通过",
     "hint": "要素表达清晰、有来源依据、类型正确"},
    {"code": ReviewConclusion.NEEDS_REVISION.value, "label": "须修订",
     "hint": "有依据但表达不完整/含糊/类型或边界有问题；必须给 revised_content（基于来源依据改写，"
             "不引入依据之外的信息）；若已有未采纳修订稿 revision_draft，在其基础上继续完善"},
    {"code": ReviewConclusion.FAIL.value, "label": "不可通过",
     "hint": "无来源依据、不承载需求信息或与其它要素完全重复"},
]

# ---- 来源接入判断（AEP-001 送检；judgement_failed 为系统侧失败语义，不供模型选择）----
INTAKE_JUDGEMENT_GUIDE: list[dict[str, str]] = [
    {"code": ModelJudgement.ACCEPTABLE.value, "label": "可接入",
     "hint": "含可用于需求分析的实质内容，来源归属清晰"},
    {"code": ModelJudgement.INSUFFICIENT_CONTENT.value, "label": "内容不足",
     "hint": "内容过少或空泛，不足以作为来源材料"},
    {"code": ModelJudgement.UNCLEAR_ATTRIBUTION.value, "label": "归属不明",
     "hint": "无法判断文本是否属于该项目，或来源不清"},
    {"code": ModelJudgement.NO_ASSET_VALUE.value, "label": "无需求资产价值",
     "hint": "寒暄、闲聊等与需求无关的内容，无需求资产价值"},
]

# ---- SCN-003 v5 条目诊断结论对象（状态字/发现项类型/诊断模式）----
VERDICT_KIND_GUIDE: list[dict[str, str]] = [
    {"code": VerdictKind.PASS.value, "label": "建议通过",
     "hint": "未发现阻断问题；findings 须全为 no_blocker，不给修订点与缺口"},
    {"code": VerdictKind.REVISE.value, "label": "建议修订",
     "hint": "问题可通过修订表达解决；必须给出至少 1 个修订点"},
    {"code": VerdictKind.WITHDRAW.value, "label": "建议撤回",
     "hint": "条目与既有确认态条目重复、不构成独立需求或不应存在；不给修订点"},
    {"code": VerdictKind.SUPPLEMENT.value, "label": "建议补充来源",
     "hint": "问题无法靠修订表达解决、必须先补来源；必须给出 supplement_gaps，不给修订点"},
]

REVIEW_FINDING_TYPE_GUIDE: list[dict[str, str]] = [
    {"code": ReviewFindingType.SOURCE_INCONSISTENCY.value, "label": "来源不一致",
     "hint": "表达不忠实于来源要素与原文：改变含义或混入来源没有的信息"},
    {"code": ReviewFindingType.AMBIGUOUS_EXPRESSION.value, "label": "表达歧义",
     "hint": "存在含糊、多义、无法唯一理解的措辞"},
    {"code": ReviewFindingType.UNTESTABLE.value, "label": "不可测试",
     "hint": "缺少可验证口径（可观察行为、阈值或验收观察点）"},
    {"code": ReviewFindingType.MISSING_FIELD.value, "label": "字段缺漏",
     "hint": "类型不恰当，或必要约束/范围缺失"},
    {"code": ReviewFindingType.NO_BLOCKER.value, "label": "无阻断",
     "hint": "该检查面未发现阻断问题（pass 结论只允许此类发现项）"},
]

DIAGNOSIS_MODE_GUIDE: list[dict[str, str]] = [
    {"code": DiagnosisMode.QUICK.value, "label": "快速初筛", "hint": "只查明显阻断问题"},
    {"code": DiagnosisMode.STANDARD.value, "label": "标准诊断", "hint": "按四类检查重点完整诊断"},
    {"code": DiagnosisMode.COMPREHENSIVE.value, "label": "全面诊断", "hint": "可引用更多上下文交叉核对"},
    {"code": DiagnosisMode.INCREMENTAL.value, "label": "增量诊断",
     "hint": "只围绕本次修订差异，并说明旧问题是否已解决"},
]

# ---- 需求质量诊断器（v2 签名件；6 维评分 / 规则分类法 / EARS 句式，单一来源供 item_diagnosis 渲染）----
# 规则码是 ReviewFindingType 的细化：finding_type 是聚合守卫看的粗类，rule_code 给 chip 与维度归属。
QUALITY_DIMENSION_GUIDE: list[dict[str, object]] = [
    {"code": QualityDimension.UNAMBIGUOUS.value, "label": "无歧义", "weight": 0.24,
     "hint": "无含糊、多义、可选逃逸或弱化情态，唯一可理解"},
    {"code": QualityDimension.VERIFIABLE.value, "label": "可验证", "weight": 0.22,
     "hint": "有可观察行为、阈值或验收口径，可测量"},
    {"code": QualityDimension.COMPLETE.value, "label": "完整性", "weight": 0.18,
     "hint": "触发/主体/响应/可观测结果齐全，阈值与范围已定义"},
    {"code": QualityDimension.SINGULAR.value, "label": "原子性", "weight": 0.14,
     "hint": "单一需求单一职责，无复合动作串联"},
    {"code": QualityDimension.CONSISTENT.value, "label": "一致性", "weight": 0.12,
     "hint": "与来源、术语、其它条目不矛盾"},
    {"code": QualityDimension.TRACEABLE.value, "label": "可追溯", "weight": 0.10,
     "hint": "来源锚点齐全、与来源现值对齐（由来源语义对齐分佐证）"},
]

QUALITY_SEVERITY_GUIDE: list[dict[str, str]] = [
    {"code": QualitySeverity.HIGH.value, "label": "高"},
    {"code": QualitySeverity.MEDIUM.value, "label": "中"},
    {"code": QualitySeverity.LOW.value, "label": "低"},
]

# rule → label / 维度 / 细化自的 finding_type / 默认严重度 / 是否可一键修复（=能否表达为 find→replace 修订点）
QUALITY_RULE_GUIDE: list[dict[str, object]] = [
    {"code": RequirementQualityRule.INCOSE_R21.value, "label": "可选逃逸子句",
     "dimension": QualityDimension.UNAMBIGUOUS.value,
     "finding_type": ReviewFindingType.AMBIGUOUS_EXPRESSION.value,
     "default_severity": QualitySeverity.HIGH.value, "can_autofix": True,
     "hint": "「必要时/如有必要/视情况」等逃逸措辞使需求不确定、不可测；建议移除"},
    {"code": RequirementQualityRule.INCOSE_R7.value, "label": "模糊量词",
     "dimension": QualityDimension.VERIFIABLE.value,
     "finding_type": ReviewFindingType.UNTESTABLE.value,
     "default_severity": QualitySeverity.MEDIUM.value, "can_autofix": True,
     "hint": "「尽快/快速/良好/较大」等不可测量表述；建议替换为量化指标"},
    {"code": RequirementQualityRule.SMELL_UNDEF.value, "label": "未定义阈值",
     "dimension": QualityDimension.COMPLETE.value,
     "finding_type": ReviewFindingType.MISSING_FIELD.value,
     "default_severity": QualitySeverity.MEDIUM.value, "can_autofix": True,
     "hint": "「超时/大额/高并发」等未给具体阈值；建议补入定义"},
    {"code": RequirementQualityRule.MODAL_WEAK.value, "label": "弱化情态",
     "dimension": QualityDimension.UNAMBIGUOUS.value,
     "finding_type": ReviewFindingType.AMBIGUOUS_EXPRESSION.value,
     "default_severity": QualitySeverity.MEDIUM.value, "can_autofix": True,
     "hint": "「可以/应尽量/尽量」使强制性不明确；宜用「应/必须」"},
    {"code": RequirementQualityRule.SMELL_COMPOUND.value, "label": "复合动作",
     "dimension": QualityDimension.SINGULAR.value,
     "finding_type": ReviewFindingType.AMBIGUOUS_EXPRESSION.value,
     "default_severity": QualitySeverity.LOW.value, "can_autofix": False,
     "hint": "单句串联多个动作，违反单一职责；建议拆分为独立可验证条目（不给修订点）"},
    {"code": RequirementQualityRule.SMELL_PASSIVE.value, "label": "被动语态",
     "dimension": QualityDimension.UNAMBIGUOUS.value,
     "finding_type": ReviewFindingType.AMBIGUOUS_EXPRESSION.value,
     "default_severity": QualitySeverity.LOW.value, "can_autofix": False,
     "hint": "被动式使执行主体不明确；宜改为「系统应…」主动式（不给修订点）"},
    {"code": RequirementQualityRule.SMELL_UNIVERSAL.value, "label": "全称量词",
     "dimension": QualityDimension.COMPLETE.value,
     "finding_type": ReviewFindingType.MISSING_FIELD.value,
     "default_severity": QualitySeverity.LOW.value, "can_autofix": False,
     "hint": "「所有/任何/总是」范围过宽，易与例外冲突；建议限定适用边界（不给修订点）"},
    {"code": RequirementQualityRule.EARS_INCOMPLETE.value, "label": "EARS 要件缺失",
     "dimension": QualityDimension.COMPLETE.value,
     "finding_type": ReviewFindingType.MISSING_FIELD.value,
     "default_severity": QualitySeverity.MEDIUM.value, "can_autofix": False,
     "hint": "缺触发条件/执行主体/系统响应/可观测结果之一；按 EARS 补齐"},
    {"code": RequirementQualityRule.SRC_DRIFT.value, "label": "来源偏离",
     "dimension": QualityDimension.CONSISTENT.value,
     "finding_type": ReviewFindingType.SOURCE_INCONSISTENCY.value,
     "default_severity": QualitySeverity.HIGH.value, "can_autofix": False,
     "hint": "条目取值与来源现值不一致（来源已修订）；走勘误/补入消解，不直接改写"},
    {"code": RequirementQualityRule.BIZ_RULE_CONFLICT.value, "label": "与业务知识矛盾",
     "dimension": QualityDimension.CONSISTENT.value,
     "finding_type": ReviewFindingType.SOURCE_INCONSISTENCY.value,
     "default_severity": QualitySeverity.HIGH.value, "can_autofix": False,
     "hint": "条目与「业务依据」段所引业务规则/术语矛盾（R=S∧D，需求须与领域规则相容）；"
             "以证据行单列指明矛盾点，不臆造，走 source_inconsistency 结论"},
]

EARS_PATTERN_GUIDE: list[dict[str, str]] = [
    {"code": EarsPattern.UBIQUITOUS.value, "label": "泛在",
     "hint": "无触发条件的恒常需求：THE <系统> SHALL <响应>"},
    {"code": EarsPattern.EVENT_DRIVEN.value, "label": "事件驱动",
     "hint": "WHEN <触发事件>，THE <系统> SHALL <响应>"},
    {"code": EarsPattern.STATE_DRIVEN.value, "label": "状态驱动",
     "hint": "WHILE <处于某状态>，THE <系统> SHALL <响应>"},
    {"code": EarsPattern.UNWANTED.value, "label": "非期望行为",
     "hint": "IF <非期望条件>，THEN THE <系统> SHALL NOT <行为>"},
    {"code": EarsPattern.OPTIONAL.value, "label": "可选特性",
     "hint": "WHERE <某特性存在>，THE <系统> SHALL <响应>"},
    {"code": EarsPattern.COMPLEX.value, "label": "复合",
     "hint": "多触发/多条件组合，建议拆分为多条独立句式"},
]

# ---- SCN-004 图表协同（受控表达方式/图表类型/图文核对发现项）----
CHART_FORMAT_GUIDE: list[dict[str, str]] = [
    {"code": ChartFormat.MERMAID.value, "label": "Mermaid", "hint": "Mermaid 源码"},
    {"code": ChartFormat.PLANTUML.value, "label": "PlantUML",
     "hint": "PlantUML 源码，@startuml/@enduml 包裹"},
    {"code": ChartFormat.MARKDOWN_TABLE.value, "label": "Markdown 表格", "hint": "Markdown 表格语法"},
]

CHART_TYPE_GUIDE: list[dict[str, str]] = [
    {"code": ChartType.FLOWCHART.value, "label": "流程图", "hint": "mermaid 首行 flowchart TD（或 graph）"},
    {"code": ChartType.STATE_DIAGRAM.value, "label": "状态图", "hint": "mermaid 首行 stateDiagram-v2"},
    {"code": ChartType.RELATION_DIAGRAM.value, "label": "关系图",
     "hint": "mermaid 首行 erDiagram 或 classDiagram"},
    {"code": ChartType.SEQUENCE_DIAGRAM.value, "label": "时序图", "hint": "mermaid 首行 sequenceDiagram"},
    {"code": ChartType.DECISION_TABLE.value, "label": "判定表", "hint": "markdown_table 表达"},
    {"code": ChartType.COMPARISON_TABLE.value, "label": "对照表", "hint": "markdown_table 表达"},
]

CHART_FINDING_TYPE_GUIDE: list[dict[str, str]] = [
    {"code": ChartFindingType.SUSPECTED_HIDDEN_REQUIREMENT.value, "label": "疑似隐藏需求",
     "hint": "图表中存在来源条目未覆盖的新增需求语义（新节点、新规则、新约束）"},
    {"code": ChartFindingType.CHART_TEXT_CONFLICT.value, "label": "图文冲突",
     "hint": "图表表达与来源条目文字表述矛盾（顺序、条件、主体、结果不一致）"},
    {"code": ChartFindingType.SOURCE_COVERAGE_GAP.value, "label": "来源覆盖缺口",
     "hint": "来源条目的关键语义在图表中缺失，或图表引用的来源不完整"},
    {"code": ChartFindingType.TRACE_GAP.value, "label": "追溯缺口",
     "hint": "预建立追溯关系缺失，或与图表实际覆盖对象不符"},
    {"code": ChartFindingType.NO_OBVIOUS_ISSUE.value, "label": "无明显问题",
     "hint": "图表表达可被来源条目支撑；无问题时输出且只输出这一项"},
    {"code": ChartFindingType.UNDETERMINABLE.value, "label": "无法判断",
     "hint": "上下文不足以判断时使用，说明缺少什么；不要猜测"},
]

# ---- P04 指定操作 AI 执行的操作类型语义 ----
EXECUTION_OPERATION_GUIDE: list[dict[str, str]] = [
    {"code": "add_missing", "hint": "新增遗漏要素"},
    {"code": "split", "hint": "拆分混合要素（返回拆分后的多条）"},
    {"code": "merge", "hint": "合并重复要素（返回合并后的一条；锚点由系统按目标要素既有锚点合并，只需给合并后表达）"},
    {"code": "adjust_type", "hint": "调整类型"},
    {"code": "adjust_anchor", "hint": "调整来源锚点"},
    {"code": "revise_expression", "hint": "修订表达（多轮迭代：有【当前修订稿】时在其基础上继续改，除非用户明确要求推倒重来）"},
]
