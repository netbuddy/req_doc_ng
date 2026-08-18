"""区5 对话斜杠命令注册表（部署期稳定词表，AEP-095/096/097 共用）。

边界：命令词解析是**确定性**的（未注册词直接报错、不调模型）；命令正文的参数
解释交命令解释 lane（LLM），其输出操作码必须落在该命令的 operations 白名单内。
guidance 会渲染进解释 lane 的 system 块，只写参数格式与消歧规则，
枚举清单（要素类型、诊断模式）仍由 labels.py 单一来源注入，不得在此手写。
"""
from __future__ import annotations

from dataclasses import dataclass

_SLASH_PREFIXES = ("/", "／")
# 命令词终结符：空白或常见中英文标点（命令词后即命令正文）
_WORD_TERMINATORS = " \t\n\r：:，,。；;"


class UnknownCommand(Exception):
    """命令词未注册（确定性回执，不调模型）。"""

    def __init__(self, word: str) -> None:
        super().__init__(word)
        self.word = word


@dataclass(frozen=True)
class ChatCommand:
    word: str  # 命令词（不含斜杠），如 "改类型"
    operations: tuple[str, ...]  # 允许解释出的操作码白名单
    guidance: str  # 解释 lane system 命令表中的参数格式与消歧规则


def _registry(*commands: ChatCommand) -> dict[str, ChatCommand]:
    return {c.word: c for c in commands}


# ---- 分析转化页（SCN-001-P02，AEP-096）----

# 无斜杠自由文本允许的意图操作码（替代原前端修订动词正则）
ANALYSIS_FREETEXT_OPERATIONS: tuple[str, ...] = ("revise.ai", "review")

ANALYSIS_COMMANDS: dict[str, ChatCommand] = _registry(
    ChatCommand(
        word="改类型",
        operations=("edit.adjust_type",),
        guidance="params.new_element_type=目标要素类型稳定码（按类型清单把用户写的中文名映射为稳定码）；用户未写目标类型→clarify。",
    ),
    ChatCommand(
        word="改表达",
        operations=("edit.revise_expression", "revise.ai"),
        guidance=(
            "用户给出的目标表达能独立成立（对照上下文中当前要素表达，可原样整体顶替且不丢关键信息）"
            "→ edit.revise_expression，params.new_content=目标表达；"
            "只给新值、片段或局部改法（如「修订为：300笔」「把200改成300」），或只写修订方向/要求"
            "→ revise.ai，params.instruction=用户的修订要求原样。"
        ),
    ),
    ChatCommand(
        word="改范围",
        operations=("edit.adjust_anchor",),
        guidance="来源范围取请求上下文中的区3选区，无需参数；上下文无选区时 clarify 提醒先选区。",
    ),
    ChatCommand(
        word="拆分",
        operations=("manual.split", "ai_execution.split"),
        guidance=(
            "用户写明了拆法（≥2 条结果且每条是能独立成立的完整表达，常为编号或分行）→ manual.split，"
            "params.new_content=拆分结果（每行一条，去编号）；"
            "只表达想拆、未写拆法或结果为片段 → ai_execution.split，params.instruction=用户要求原样。"
        ),
    ),
    ChatCommand(
        word="合并",
        operations=("manual.merge", "ai_execution.merge"),
        guidance=(
            "params.target_element_refs=参与合并的要素 id（含当前目标；按用户写的「名称」或序号在上下文要素清单中解析，解析不出→clarify）；"
            "用户写明了能独立成立的合并后完整表达 → manual.merge，params.new_content=该表达；"
            "未写、或只给片段/方向（如「由 AI 起草」）→ ai_execution.merge，params.instruction=用户要求原样。"
        ),
    ),
    ChatCommand(
        word="新增遗漏",
        operations=("manual.add_missing",),
        guidance=(
            "params.new_content=要补登的要素完整表达；正文为空且上下文有选区时用选区文本；两者皆无→clarify。"
            "params.new_element_type=该要素的类型稳定码（可选）：用户点名了类型（如「这应该属于接口需求」）"
            "就按用户所说映射；未点名则按补登内容判断最合适的一种；判断不出就省略该参数。"
        ),
    ),
    ChatCommand(
        word="勘误",
        operations=("erratum",),
        guidance="params.old_text=原文片段、params.new_text=更正后文本（常见格式：把「原文」改正为「更正后」）；缺任一→clarify。",
    ),
    ChatCommand(
        word="补入",
        operations=("supplement",),
        guidance="params.content=补入的新事实、params.basis=依据（谁说的/哪次会议/什么凭证，常见格式：<内容>（依据：<依据>））；缺依据→clarify。",
    ),
)

# ---- 条目评审页（SCN-003-P01，AEP-095 斜杠预处理）----

ITEM_REVIEW_COMMANDS: dict[str, ChatCommand] = _registry(
    ChatCommand(
        word="诊断",
        operations=("start_diagnosis",),
        guidance=(
            "params.diagnosis_mode=诊断模式稳定码（按诊断模式清单把用户写的中文映射为稳定码，未写默认 standard）；"
            "params.scope=selected（用户提到已勾选/这些条目且上下文有勾选集）或 current（当前条目）。"
        ),
    ),
    ChatCommand(
        word="采纳结论",
        operations=("adjudicate_adopt",),
        guidance="采纳当前条目的现行结论；params.selected_point_ordinals=用户点名的修订点序号列表（如「修订点 1、2」，未点名=全部）。",
    ),
    ChatCommand(
        word="拒绝结论",
        operations=("adjudicate_reject",),
        guidance="params.reason=拒绝理由（必填，取命令正文中的理由部分）；无理由→clarify。",
    ),
    ChatCommand(
        word="采纳草案",
        operations=("adopt_draft",),
        guidance="采纳上下文中的在途修订草案，无需参数；上下文无在途草案→clarify。",
    ),
    ChatCommand(
        word="修订",
        operations=("manual_revision", "draft"),
        guidance=(
            "用户给出的目标表达能独立成立（对照上下文中当前条目表达，可原样整体顶替且不丢关键信息）"
            "→ manual_revision，params.new_expression=目标表达；"
            "只给新值、片段或局部改法（如「修订为：3秒」「把200改成300」），或只写修订方向"
            "→ draft，params.instruction=用户要求原样（转起草草案）。"
        ),
    ),
    ChatCommand(
        word="找来源",
        operations=("find_sources",),
        guidance=(
            "为当前条目在同批次已确认、尚未链接到本条的要素中检索候选来源，无需参数；"
            "命令正文可空。"
        ),
    ),
    ChatCommand(
        word="覆盖确认",
        operations=("override_confirm",),
        guidance="params.reason=覆盖确认理由（必填）；无理由→clarify。",
    ),
    ChatCommand(
        word="撤回",
        operations=("withdraw",),
        guidance="params.reason=撤回理由（必填）；无理由→clarify。",
    ),
)


# ---- 条目形成页（SCN-002-P01，AEP-097）----

FORMATION_COMMANDS: dict[str, ChatCommand] = _registry(
    ChatCommand(
        word="生成条目",
        operations=("start_itemization",),
        guidance=(
            "发起条目化批次；params.scope=selected（用户提到已勾选/这些要素且上下文有勾选集）"
            "或 all（默认：全部可条目化要素）。"
        ),
    ),
    ChatCommand(
        word="改类型",
        operations=("revise.req_type",),
        guidance="params.new_req_type=目标条目类型稳定码（按条目类型清单把用户写的中文名映射为稳定码）；未写目标类型→clarify。",
    ),
    ChatCommand(
        word="修订",
        operations=("revise.field", "draft.field"),
        guidance=(
            "params.field_key=目标字段稳定码（按修订字段清单映射用户点名的字段，未点名默认 expression）；"
            "用户给出的目标值能独立成立（对照上下文中当前条目该字段值，可原样整体顶替且不丢关键信息）"
            "→ revise.field，params.new_value=目标值；"
            "只给新值、片段或局部改法（如「修订为：3秒」「把200改成300」），或只写修订方向"
            "→ draft.field，params.instruction=用户要求原样（仅条目表达支持起草，其它字段→clarify）。"
        ),
    ),
    ChatCommand(
        word="规范化",
        operations=("draft.normalize",),
        guidance="按当前条目类型的陈述档案规范化条目表达（出建议稿，不直接生效）；params.instruction=用户附加要求原样（可省略）。",
    ),
    ChatCommand(
        word="拆分",
        operations=("split.manual",),
        guidance=(
            "params.new_expressions=拆分结果（每行一条，≥2 条且每条是能独立成立的完整表达，去编号）；"
            "只表达想拆、未写拆法或结果为片段 → clarify（形成页拆分须由用户写明拆法）。"
        ),
    ),
    ChatCommand(
        word="归并",
        operations=("merge.manual",),
        guidance=(
            "params.target_item_refs=参与归并的待确认条目 item_ref（含当前目标；按用户写的 REQ 编号或「表达」"
            "在上下文条目清单中解析，解析不出→clarify）；"
            "params.new_expression=归并后能独立成立的完整表达（必填，未写→clarify）。"
        ),
    ),
    ChatCommand(
        word="问来源",
        operations=("explain.source",),
        guidance="指认当前条目的来源要素、原文锚点与形成依据；确定性回答，无需参数（不调解释模型）。",
    ),
    ChatCommand(
        word="复核",
        operations=("structure.recheck",),
        guidance=(
            "对当前条目发起结构体检复核（只判不改，AEP-114）；确定性直发，无需参数（不调解释模型）。"
            "现行判定条目直发回执不调模型；批次复核归区2 按钮。"
        ),
    ),
    ChatCommand(
        word="引用依据",
        operations=("reference.supporting_basis",),
        guidance=(
            "把业务领域知识（术语/业务规则/角色/外部系统）引用为当前条目的支撑依据（登记预建立"
            " supporting_basis 边，随条目确认转有效）；params.element_refs=要引用的业务知识 id 列表"
            "（按用户写的名称在上下文「业务知识候选」清单中解析，解析不出→clarify）；"
            "上下文无候选或用户未点名→clarify。"
        ),
    ),
)


def resolve_command(registry: dict[str, ChatCommand], message: str) -> tuple[ChatCommand | None, str]:
    """确定性解析命令词。

    返回 (命令, 原文完整保留)；无斜杠前缀 → (None, message)；
    命令词未注册 → raise UnknownCommand(word)。
    """
    stripped = message.lstrip()
    if not stripped or stripped[0] not in _SLASH_PREFIXES:
        return None, message
    rest = stripped[1:]
    end = len(rest)
    for i, ch in enumerate(rest):
        if ch in _WORD_TERMINATORS:
            end = i
            break
    word = rest[:end].strip()
    command = registry.get(word)
    if command is None:
        raise UnknownCommand(word)
    return command, message


def command_guide(registry: dict[str, ChatCommand]) -> list[dict[str, object]]:
    """渲染解释 lane system 块命令表用（部署期稳定，注意保持字节稳定利于前缀缓存）。"""
    return [
        {"word": c.word, "operations": list(c.operations), "guidance": c.guidance}
        for c in registry.values()
    ]
