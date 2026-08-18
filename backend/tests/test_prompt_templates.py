"""迁移链路提示词模板渲染测试（source_intake / item_* / chart_*）。

守护点（与 test_element_prompt_templates.py 同口径）：
- 八个模板均可用样例变量渲染出非空 (system, user) 对，无残留模板语法；
- 枚举/取值经动态渲染全数出现（labels/enums 是唯一来源，新增值漏改模板即失败）；
- 输出 schema 与解析器同源常量可注入；拒绝通道（cannot_comply 等）确在 system 块；
- 动态语料（意图/上下文/已排除点等）确在 user 块，不进 system；
- system 块逐字节稳定（前缀缓存前提）；漏传变量立刻报错（StrictUndefined）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from jinja2.exceptions import UndefinedError

from app.adapters.llm import (
    _CHART_SUGGESTION_OUTPUT,
    _CHART_VERIFICATION_OUTPUT_ITEM,
    _DIAGNOSIS_OUTPUT,
    _DRAFT_OUTPUT,
    _ELEMENT_COMMAND_OUTPUT,
    _FORMATION_COMMAND_OUTPUT,
    _INTAKE_OUTPUT,
    _ITEM_COMMAND_OUTPUT,
    _ITEM_FORMATION_OUTPUT,
    _REEVAL_OUTPUT,
    _SECTION_MANUSCRIPT_DRAFT_OUTPUT,
    _SOURCE_CANDIDATE_OUTPUT,
    _format_item_profiles,
)
from app.adapters.prompts.environment import dumps, render_pair
from app.domain.chat_commands import (
    ANALYSIS_COMMANDS,
    FORMATION_COMMANDS,
    ITEM_REVIEW_COMMANDS,
    command_guide,
)
from app.domain.enums import (
    ChartFindingType,
    ChartFormat,
    ChartType,
    DiagnosisMode,
    ModelJudgement,
    RequirementItemType,
    ReviewFindingType,
    VerdictKind,
)


def _intake_vars() -> dict:
    return {
        "project_ref": "P-1", "source_note": "访谈", "raw_text": "系统应支持导出。",
        "output_schema": dumps(_INTAKE_OUTPUT),
    }


def _formation_vars() -> dict:
    return {
        "project_ref": "P-1", "raw_text": "系统应支持导出。", "elements": "[]",
        "profiles_text": _format_item_profiles(),
        "output_schema": dumps(_ITEM_FORMATION_OUTPUT),
    }


def _diagnosis_vars() -> dict:
    return {
        "project_ref": "P-1", "diagnosis_mode": "incremental", "item": "{}",
        "sources": "[]", "business_sources": "[]", "raw_text": "系统应支持导出。",
        "revisions": "[]",
        "attestation": "",  # 无人工确认背书（默认路径；有背书的渲染另测）
        "prior_findings": "[]", "excluded_points": '[{"label":"补口径"}]',
        "thread_context": "用户认为判定过严",
        "output_schema": dumps(_DIAGNOSIS_OUTPUT),
    }


def _reeval_vars() -> dict:
    return {
        "item": "{}", "standing_verdict": "{}", "message": "阈值有来源，请复查",
        "excluded_points": "[]", "thread_context": "（无）",
        "output_schema": dumps(_REEVAL_OUTPUT),
    }


def _draft_vars() -> dict:
    return {
        "item": "{}", "sources": "[]", "intent": "改成：导出超时不超过五秒",
        "current_draft": "（无，在当前表达上起草第 1 稿）",
        "structure_context": "",
        "output_schema": dumps(_DRAFT_OUTPUT),
    }


def _explain_vars() -> dict:
    return {"item": "{}", "verdict_context": "{}", "question": "为什么判不可测试？"}


def _source_candidates_vars() -> dict:
    return {
        "item": '{"req_no": "REQ-006", "expression": "大额订单需人工审核"}',
        "candidates": '[{"id": "E1", "content": "下单后通知用户", "source_quote": "系统应在下单后通知用户"}]',
        "output_schema": dumps(_SOURCE_CANDIDATE_OUTPUT),
    }


def _chart_suggestion_vars() -> dict:
    return {
        "project_ref": "P-1", "chart": "{}", "sources": "[]",
        "current_source": "（空）", "intent": "补上超时分支",
        "output_schema": dumps(_CHART_SUGGESTION_OUTPUT),
    }


def _chart_verification_vars() -> dict:
    return {
        "project_ref": "P-1", "chart": "{}", "sources": "[]", "trace_links": "[]",
        "output_schema": dumps(_CHART_VERIFICATION_OUTPUT_ITEM),
    }


def _element_command_vars() -> dict:
    return {
        "commands": command_guide(ANALYSIS_COMMANDS),
        "command_word": "改类型", "message": "/改类型 改为功能需求",
        "context": '{"selected_element": null}',
        "output_schema": dumps(_ELEMENT_COMMAND_OUTPUT),
    }


def _item_command_vars() -> dict:
    return {
        "commands": command_guide(ITEM_REVIEW_COMMANDS),
        "command_word": "诊断", "message": "/诊断 对已勾选条目发起标准诊断",
        "context": '{"item": null}',
        "output_schema": dumps(_ITEM_COMMAND_OUTPUT),
    }


def _formation_command_vars() -> dict:
    return {
        "commands": command_guide(FORMATION_COMMANDS),
        "command_word": "改类型", "message": "/改类型 改为约束",
        "context": '{"selected_item": null}',
        "output_schema": dumps(_FORMATION_COMMAND_OUTPUT),
    }


def _section_manuscript_draft_vars() -> dict:
    return {
        "section_title": "编写目的",
        "section_purpose": "说明本文档的目的与预期读者。",
        "content_types": "人工撰稿",
        "assets": '[{"req_no": "FR-001", "type": "功能需求", "expression": "系统应支持导出"}]',
        "examples": '["范例：本章应清晰界定文档目的与范围。"]',
        "project_scope": "订单中心",
        "project_background": "面向内部运营",
        "output_schema": dumps(_SECTION_MANUSCRIPT_DRAFT_OUTPUT),
    }


_CASES = [
    ("source_intake", _intake_vars),
    ("item_formation", _formation_vars),
    ("item_diagnosis", _diagnosis_vars),
    ("item_reeval", _reeval_vars),
    ("item_draft", _draft_vars),
    ("item_source_candidates", _source_candidates_vars),
    ("item_explain", _explain_vars),
    ("chart_suggestion", _chart_suggestion_vars),
    ("chart_verification", _chart_verification_vars),
    ("element_command", _element_command_vars),
    ("item_command", _item_command_vars),
    ("formation_command", _formation_command_vars),
    ("section_manuscript_draft", _section_manuscript_draft_vars),
]


@pytest.mark.parametrize("name,vars_fn", _CASES)
def test_templates_render_nonempty_pairs(name, vars_fn):
    system, user = render_pair(name, **vars_fn())
    assert system and user
    assert "{{" not in system and "{%" not in system  # 无残留模板语法
    assert "{{" not in user and "{%" not in user


@pytest.mark.parametrize("name,vars_fn", _CASES)
def test_system_block_byte_stable(name, vars_fn):
    """system 只放部署期稳定内容：同变量两次渲染逐字节一致（前缀缓存前提）。"""
    s1, _ = render_pair(name, **vars_fn())
    s2, _ = render_pair(name, **vars_fn())
    assert s1 == s2


@pytest.mark.parametrize("name", [c[0] for c in _CASES])
def test_missing_variable_fails_fast(name):
    with pytest.raises(UndefinedError):
        render_pair(name, project_ref="P-1")  # 缺其余变量


def test_intake_judgements_injected_without_failed():
    """四个可选类别全数注入；judgement_failed 是系统侧失败语义，不得出现在提示词。"""
    system, user = render_pair("source_intake", **_intake_vars())
    for j in ModelJudgement:
        if j is ModelJudgement.JUDGEMENT_FAILED:
            assert j.value not in system
        else:
            assert j.value in system, f"缺判定类别 {j.value}"
    assert "系统应支持导出。" in user and "系统应支持导出。" not in system


def test_formation_refusal_channel_and_dynamic_split():
    system, user = render_pair("item_formation", **_formation_vars())
    assert "cannot_comply" in system and "element_ref" in system
    assert "系统应支持导出。" in user and "系统应支持导出。" not in system


def test_source_candidates_rules_in_system():
    """A1：找来源提示词三条关键规则被固定——候选仅限给定差集（以 id 引用）、
    逐条带要素 id 与理由、cannot_comply 拒绝通道；语料（候选内容）只进 user 块。"""
    system, user = render_pair("item_source_candidates", **_source_candidates_vars())
    # 规则一：候选只能来自给定候选集，以 element_id 引用，禁自拟
    assert "候选只能来自给定候选集" in system
    assert "element_id" in system and "禁止自拟" in system
    # 规则二：逐条给推荐理由 + 相关度排序
    assert "逐条给推荐理由" in system and "相关度排序" in system
    # 规则三：cannot_comply 显式拒绝通道（候选集为空/语料不足不凑数）
    assert "cannot_comply" in system and "诚实性优先" in system
    # 语料（候选原文）放 user 块以命中前缀缓存
    assert "下单后通知用户" in user and "下单后通知用户" not in system


def test_formation_profiles_injected_in_system():
    """条目档案（封闭五类）全数注入 system；结构判定枚举随 output_schema 进入。"""
    system, _ = render_pair("item_formation", **_formation_vars())
    for req_type in ("functional", "quality", "constraint", "data", "interface"):
        assert f"【{req_type} 条目陈述档案" in system, f"缺 {req_type} 档案"
    assert "response_measure" in system  # QAS 量化度量面向
    assert "statement_conformance" in system and "facet_findings" in system
    assert "不得编造" in system  # 缺失成分只指缺，不得凑句式


def test_formation_profiles_switch_by_convention_with_common_layer():
    """profiles_text 随方案切换（句式差异）且恒含方案无关公共写作约束（选型文档 §4/§5）。"""
    ears = _format_item_profiles("ears-cn")
    boiler = _format_item_profiles("boilerplate-cn")
    master = _format_item_profiles("master-cn")
    # 方案标头各异
    assert "中文 EARS" in ears and "中文 Boilerplates" in boiler and "中文 MASTeR" in master
    # 方案特有 facet 只在对应方案出现
    assert "modal_word" in boiler and "modal_word" not in ears
    assert "interaction_kind" in master and "interaction_kind" not in ears
    # 公共写作约束（模态词 + Q1–Q7）在每套方案注入块中恒存
    for text in (ears, boiler, master):
        assert "公共写作约束" in text and "Q7" in text and "不得" in text


@pytest.mark.parametrize("name,vars_fn", [
    ("item_diagnosis", _diagnosis_vars), ("item_reeval", _reeval_vars),
])
def test_verdict_object_rules_injected(name, vars_fn):
    """v5 结论对象契约：状态字与发现项类型全数注入（共享 partial，双模板同源）。"""
    system, _ = render_pair(name, **vars_fn())
    for k in VerdictKind:
        assert k.value in system, f"{name} 缺状态字 {k.value}"
    for t in ReviewFindingType:
        assert t.value in system, f"{name} 缺发现项类型 {t.value}"
    assert "finding_index" in system and "已排除修订点" in system


def test_diagnosis_revision_basis_discipline_in_system():
    """A1：修订方案受与发现项同套的依据约束——口径须有语料出处并写进 basis。

    回归的缺陷（2026-07-15）：模板只要求发现项逐字引用依据，replace 文本无约束，
    LLM 遂把「历史行为」自拟为语料中不存在的「近12个月退货率」，下一轮诚实地把它
    报为来源不一致 → 采纳链永不收敛。
    """
    system, _ = render_pair("item_diagnosis", **_diagnosis_vars())
    assert "修订方案受同一条依据纪律约束" in system
    assert "该点 basis 写明出处" in system
    assert "supplement_gaps" in system


def test_diagnosis_revise_is_a_promise_not_a_default():
    """revise 的唯一判据＝「采纳这些点后能不能过」；兑现不了必须改判 supplement/withdraw。

    回归的用户走查（2026-07-15，REQ-006）：条目表达讲大额审核、来源要素讲下单通知，
    靠改表达消不掉来源不一致（要对齐就得改需求含义，为共享块所禁）。系统却连发 7 轮
    revise、用户采纳 4 次仍被打回——每发一次 revise 就是许一个「采纳即过」的诺。
    初版本有「合成后应达建议通过」的判据，却被「缺值问题不计入本条」的豁免架空：
    模型只要把不可修的问题归进「如实报出」那档，就能继续发 revise。本断言钉住减法后的判据。
    """
    system, _ = render_pair("item_diagnosis", **_diagnosis_vars())
    assert "revise 是一个承诺" in system
    assert "兑现不了就不许给 revise" in system
    # 三个出口俱在，且 supplement 明确零修订点
    assert "取 **supplement**" in system and "不给任何修订点" in system
    assert "取 withdraw" in system
    # 两条禁令：不许为凑点编值；不许拿「还有一部分能改」当发 revise 的理由
    assert "不许为了凑够「revise 至少一个点」而编值" in system
    assert "不许拿「还有一部分能改」当发 revise 的理由" in system
    assert "「还有问题」不等于「问题能靠改表达解决」" in system
    # 豁免条款必须已删除——它正是 treadmill 的闸门
    assert "不计入本条" not in system, "完整性判据的豁免条款复活，revise 又能挟不可修残留"


def test_diagnosis_incremental_must_hold_the_promise():
    """增量诊断是判据最易失守处：只剩缺值/来源对不上时改判 supplement，不得退回 revise 编点。

    实测（2026-07-15）：判据只写通用档时，标准轮遵守、增量轮不遵守（编出「30秒」）；
    对增量明写后同场景改判 supplement + gaps、零修订点、无自拟值。
    """
    system, _ = render_pair("item_diagnosis", **_diagnosis_vars())
    assert "增量诊断尤其要守住这条" in system
    assert "不要因为「还有问题没解决」就退回 revise 再编一个点" in system
    assert "同一条目被反复判 revise 而始终不通过，是这条判据被破坏的信号" in system


def test_diagnosis_basis_is_provenance_not_reasoning():
    """basis 只写出处与引文，不写推理过程——basis 原样展示给用户。"""
    system, _ = render_pair("item_diagnosis", **_diagnosis_vars())
    assert "basis 只写出处、不写过程" in system
    assert "不得写推理过程、自我对话、规则权衡、占位说明" in system


def test_diagnosis_revision_completeness_in_system():
    """A1：修订方案须一次修完——语料足以修好的问题本轮全给，不留给下一轮。"""
    system, _ = render_pair("item_diagnosis", **_diagnosis_vars())
    assert "修订方案要一次修完" in system
    assert "不得留一部分等下一轮再提" in system
    # 完整性是 revise 承诺的直接后果，不再留任何豁免口子
    assert "既然 revise 意味着「采纳后就能过」" in system


def test_diagnosis_quick_mode_revision_basis_narrowed():
    """A3（裁定 3 候选 a）：quick 无 raw_text，其修订点只依据来源要素引文。

    上下文事实：prepare_item_diagnosis 仅在非 quick 模式读 raw_text，而 sources
    不分模式恒装配且带 source_quote —— 故窄化到引文是零成本且自洽的。
    """
    system, _ = render_pair("item_diagnosis", **_diagnosis_vars())
    assert "快速初筛（quick）模式下材料原文不在你的上下文中" in system
    assert "source_quote" in system
    assert "改跑标准诊断" in system


def test_revision_basis_rules_stay_out_of_shared_partial():
    """落点不变式：修订依据纪律只在 item_diagnosis，不得漂进共享 partial。

    item_reeval 共用 verdict_object_rules 但其上下文无 sources/raw_text/业务依据
    （llm.py 的 reeval() 不传），也不含 quality_rules —— 把引用这些语料的规则注入
    该 lane 等于下达无法遵守的指令。本断言在有人图省事往共享块搬规则时失败。
    """
    reeval_system, _ = render_pair("item_reeval", **_reeval_vars())
    for leaked in ("修订方案受同一条依据纪律约束", "修订方案要一次修完",
                   "快速初筛（quick）模式下材料原文不在你的上下文中"):
        assert leaked not in reeval_system, f"规则漂入 item_reeval（该 lane 无对应语料）：{leaked}"


def test_diagnosis_modes_in_system_and_context_in_user():
    system, user = render_pair("item_diagnosis", **_diagnosis_vars())
    for m in DiagnosisMode:
        assert m.value in system, f"缺诊断模式 {m.value}"
    assert "补口径" in user and "用户认为判定过严" in user  # 动态上下文只进 user
    assert "补口径" not in system and "用户认为判定过严" not in system


def test_reeval_actions_and_message_binding():
    system, user = render_pair("item_reeval", **_reeval_vars())
    assert "maintain" in system and "supersede" in system
    assert "阈值有来源，请复查" in user and "阈值有来源" not in system


def test_draft_refusal_channel_and_intent_in_user():
    system, user = render_pair("item_draft", **_draft_vars())
    assert "cannot_comply" in system and "proposed_value" in system
    assert "导出超时不超过五秒" in user and "导出超时" not in system


def test_draft_injects_structure_context_into_user_block():
    """起草请求带上结构体检上下文：待补成分的判定原因、补写示例、句式模板都进 user 块。

    走查反馈第⑦组——此前模型只拿到一个成分名，界面上已经算好的判定依据一样都没进提示词。
    动态语料（具体的成分数据）只许进 user 块；system 块含一行固定的「结构体检上下文」
    约束行（照体检结果补写、不自行另立成分），它不随入参变化——这是 2026-07-20 裁定消费
    经 manager 追认的第⑦组配套改动，故本用例在断言 system 不含动态成分数据的同时，也断言
    system 含那行固定约束。
    """
    context = dumps({
        "待补成分": [{
            "成分": "响应度量", "判定": "missing",
            "判定原因": "未见量化阈值", "补写示例": "请给出可验证的量化指标",
        }],
        "句式模板": "当<触发>时，系统应<行为>，且<可观测结果>",
    })
    system, user = render_pair("item_draft", **{**_draft_vars(), "structure_context": context})
    assert "响应度量" in user and "未见量化阈值" in user
    assert "请给出可验证的量化指标" in user
    assert "当<触发>时" in user
    assert "响应度量" not in system and "未见量化阈值" not in system
    # system 块给出「照体检结果补写、不自行另立成分」的约束
    assert "结构体检上下文" in system


def test_draft_without_structure_context_says_none_not_blank():
    """没有有效体检时明说「无」，不留一段空白让模型以为漏给了什么。"""
    _, user = render_pair("item_draft", **{**_draft_vars(), "structure_context": ""})
    assert "无（该条目没有当前有效的体检结果）" in user
    # 段里只有那句「无」，不带成分数据（对照上一个用例注入的样本）
    assert "响应度量" not in user and "未见量化阈值" not in user


def test_explain_plain_text_contract():
    system, user = render_pair("item_explain", **_explain_vars())
    assert "300" in system and "JSON" in system  # 纯文本契约（不输出结构化标记）
    assert "为什么判不可测试？" in user


def test_chart_suggestion_enums_and_refusal():
    system, user = render_pair("chart_suggestion", **_chart_suggestion_vars())
    for f in ChartFormat:
        assert f.value in system, f"缺表达方式 {f.value}"
    for t in ChartType:
        assert t.value in system, f"缺图表类型 {t.value}"
    assert "cannot_comply" in system
    assert "补上超时分支" in user and "补上超时分支" not in system


def test_chart_verification_finding_types_injected():
    system, _ = render_pair("chart_verification", **_chart_verification_vars())
    for t in ChartFindingType:
        assert t.value in system, f"缺发现项类型 {t.value}"
    assert "no_obvious_issue" in system and "undeterminable" in system


def test_element_command_table_and_refusal_channels():
    """命令表全数注入 system（注册表是唯一来源）；clarify/cannot_comply 契约在 system；原文只进 user。"""
    system, user = render_pair("element_command", **_element_command_vars())
    for word, cmd in ANALYSIS_COMMANDS.items():
        assert f"/{word}" in system, f"缺命令 /{word}"
        for op in cmd.operations:
            assert op in system, f"缺操作码 {op}"
    assert "clarify" in system and "cannot_comply" in system
    assert "revise.ai" in system and "review" in system  # 自由文本意图
    assert "完整表达" in system and "独立成立" in system  # 完整性判据（防「修订为：<片段>」整体替换）
    assert "改为功能需求" in user and "改为功能需求" not in system


def test_item_command_table_and_refusal_channels():
    system, user = render_pair("item_command", **_item_command_vars())
    for word, cmd in ITEM_REVIEW_COMMANDS.items():
        assert f"/{word}" in system, f"缺命令 /{word}"
        for op in cmd.operations:
            assert op in system, f"缺操作码 {op}"
    for m in DiagnosisMode:
        assert m.value in system, f"缺诊断模式 {m.value}"
    assert "clarify" in system and "cannot_comply" in system
    assert "完整表达" in system and "独立成立" in system  # 完整性判据（防「修订为：<片段>」整体替换）
    assert "已勾选条目" in user and "已勾选条目" not in system


def test_formation_command_table_and_refusal_channels():
    system, user = render_pair("formation_command", **_formation_command_vars())
    for word, cmd in FORMATION_COMMANDS.items():
        assert f"/{word}" in system, f"缺命令 /{word}"
        for op in cmd.operations:
            assert op in system, f"缺操作码 {op}"
    for t in RequirementItemType:
        assert t.value in system, f"缺条目类型 {t.value}"
    for field in ("expression", "req_type", "curation_note", "boundary_note",
                  "verification_method", "verification_note", "priority"):
        assert field in system, f"缺修订字段 {field}"
    assert "clarify" in system and "cannot_comply" in system
    assert "完整表达" in system and "独立成立" in system
    assert "改为约束" in user and "改为约束" not in system


def test_diagnosis_never_points_at_missing_values_whatever_the_verdict():
    """兜底铁律：语料无值的问题绝不给修订点——与最终取哪个结论状态字无关。

    实测教训（2026-07-15）：把「缺值问题作发现项报出、但不给它修订点」这条**局部禁令**
    删掉、只留「整条改判 supplement」的**全局判据**后，模型立刻退回 revise 并编出「30秒」
    （basis 自承「材料原文未提供具体间隔值…暂定为30秒」）。即：本模型可靠遵守具体的局部
    禁令，不可靠遵守全局判据切换。故判据与禁令并存，不是二选一。
    """
    system, _ = render_pair("item_diagnosis", **_diagnosis_vars())
    assert "兜底铁律" in system
    assert "绝不为它产出修订点" in system
    # 缺口的合法容身处＝发现项；并点破 revise 携 gaps 会被拒收
    assert "写进**该问题对应的发现项**" in system
    assert "revise 结论携带缺口清单会被**整轮拒收**" in system
    # 直接封死实测中出现过的编造话术
    assert "暂定/暂用/占位/依据常见实践/待人工确认" in system


def test_diagnosis_attestation_absent_leaves_no_seam_in_user_block():
    """A1 护栏（2026-07-26 按冷审查 V2 重写）：无背书条目的 user 块不许留下条件段的接缝。

    为什么换写法：原护栏拿「**改动后**的模板挖掉背书段」当基准，两侧几乎恒等——块标签
    独占一行时在 trim_blocks/lstrip_blocks 下无论段内正文怎么写都不吐字符，于是它拦不住
    作者当时真正要做的那个决定（空行放在 `{% endif %}` 之内还是之外）。把块内那个空行移到
    `{% endif %}` 之后，原护栏照样通过，而每一条无背书条目的 user 块会多出一个空行——正是
    该护栏自称要防的前缀缓存整体失效。

    改为直接断言接缝本身：
    - 不出现三连换行（条件段留下的空行会在两段之间多出一个空行）；
    - 各分段标题在 user 块里恰好出现一次（段落既不重复也不消失）。
    """
    _, user = render_pair("item_diagnosis", **_diagnosis_vars())
    # 条件段整段不渲染。注意「人工确认来源」这五个字在 user 块里还有第二个出处：材料原文那
    # 一行的证据封闭列举（K8 后与 system 块同口径），它对所有条目常在，故这里钉的是段落标题。
    assert "人工确认来源（JSON" not in user
    assert "\n\n\n" not in user, "条件段落在 user 块里留下了多余空行（前缀缓存会整体失效）"
    for heading in ("\n\n业务依据·业务领域知识", "\n\n材料原文（", "\n\n字段修订记录（"):
        assert user.count(heading) == 1, f"分段标题 {heading!r} 在 user 块里不是恰好一次"


def test_diagnosis_attestation_present_keeps_section_seams_clean():
    """带背书时同样不许出现接缝漂移：背书段插入后各分段标题仍各出现一次、无三连空行。"""
    attestation = dumps({"reason": "口头提出", "operator_ref": "u-1", "at": "2026-07-25T02:00:00Z"})
    _, user = render_pair("item_diagnosis", **{**_diagnosis_vars(), "attestation": attestation})
    assert "\n\n\n" not in user
    for heading in ("\n\n人工确认来源（", "\n\n业务依据·业务领域知识", "\n\n材料原文（"):
        assert user.count(heading) == 1, f"分段标题 {heading!r} 在 user 块里不是恰好一次"


def test_diagnosis_user_block_evidence_list_is_not_narrower_than_system():
    """K8 结构性守卫：user 块不得留下比 system 块更窄的「诊断依据只能来自…」封闭列举。

    这是第四次复发的唯一结构性拦法。该模板里「合法证据来源」被封闭列举了三处，本卡改了
    system 块那两处、漏了 user 块「材料原文」那一行——它就在刚注入的人工确认段下方六行，
    同一情境下两条指令内容相反：先递给模型一个事实，再告诉它这个事实不是可采信的依据。
    """
    system, user = render_pair("item_diagnosis", **_diagnosis_vars())
    marker = "诊断依据只能来自"
    for block_name, text in (("system", system), ("user", user)):
        for line in text.splitlines():
            if marker not in line:
                continue
            for term in ("业务依据", "人工确认来源"):
                assert term in line, (
                    f"{block_name} 块的证据封闭列举漏了「{term}」：{line.strip()}"
                )


def test_diagnosis_attestation_present_renders_block_and_reason_verbatim():
    """A2 上下文：条目带人工确认背书时，user 块给出事实声明＋理由原文＋操作者与时间。"""
    attestation = dumps({
        "reason": "客户在启动会上口头提出，会议纪要未记录",
        "operator_ref": "u-analyst-1",
        "at": "2026-07-25T02:00:00Z",
    })
    _, user = render_pair("item_diagnosis", **{**_diagnosis_vars(), "attestation": attestation})
    assert "人工确认来源" in user
    assert "客户在启动会上口头提出，会议纪要未记录" in user  # 理由原文逐字，不摘编
    assert "u-analyst-1" in user and "2026-07-25T02:00:00Z" in user
    # 旧轮发现项仍会经 prior_findings 复述「缺来源」，区块须当场点破不得据此重报
    assert "不得据此重报" in user


def test_diagnosis_attestation_rule_and_evidence_list_stay_consistent():
    """A2 规则行：system 块给出背书处置规则，且合法依据的封闭列举同步含人工确认。

    只加规则不改列举，两处会当场自相矛盾——模型一边被告知「依据只能来自条目/要素/业务
    依据/材料原文」，一边被要求据人工确认放行（veto 卡冷审查 C19 的同款场景）。
    """
    system, _ = render_pair("item_diagnosis", **_diagnosis_vars())
    assert "以及本次上下文里明确给出的人工确认来源记录" in system  # 封闭列举已扩
    assert "它闭合的只有「这条需求找不到出处」这一个缺口" in system
    # 走查实测（2026-07-25，REQ-006 从未背书）：规则常驻 system 块（前缀缓存要求逐字节稳定），
    # 模型据此臆断「人工确认已闭合出处缺口」并写进结论摘要——凭空断言一件没发生过的事。
    # 故规则必须自带否定分支：没有那一段就整条不适用，且不许声称有过人工确认。
    assert "只在用户上下文里确实出现「人工确认来源」段时才生效" in system
    assert "更不得在 verdict_summary 或任何发现项里声称" in system
    # 走查实测（2026-07-25，REQ-008）：只说「不许再判缺来源」会被读成「缺什么都不许判
    # supplement」，模型转而自拟出「Excel (.xlsx)、订单号、金额…」并把 basis 写成「依据常规
    # 业务实践」——正是模板别处已明令禁止的编值。故必须把两类缺口分开说。
    assert "但它没有替你补上任何具体值" in system
    assert "绝不因为来源缺口已闭合就去编值" in system
    assert "来源＝人工确认" in system
    # 红线：背书≠有材料出处，其余判据不放水
    assert "不得因此臆造引文、编造来源要素编号或假装材料里写了什么" in system
    assert "其余判据一律照常执行" in system
    # 增量诊断那段的旧口径（来源对不上就取 supplement）须带上背书例外，否则两段打架
    assert "唯一例外是带人工确认来源的条目" in system
    # K9：结论状态字三分支被模板自称为「唯一判据」，其中「表达讲的事在来源要素中没有依据
    # → supplement」那一支必须带背书例外，否则它与上面那条人工确认规则对同一条目给相反指令，
    # 而模型按声明的优先级裁决冲突时会选错那一边。
    assert "要对齐来源就得改掉需求本身的含义——**带人工确认来源的条目除外**" in system
