"""区5 斜杠命令注册表：确定性解析 + 命令表覆盖守护。

设计事实源：docs/40 slices/SCN-001-P02/页面详细设计.md §5.2、SCN-003-P01/页面详细设计.md §7.5。
"""
import pytest

from app.domain.chat_commands import (
    ANALYSIS_COMMANDS,
    ITEM_REVIEW_COMMANDS,
    UnknownCommand,
    command_guide,
    resolve_command,
)


def test_no_slash_is_free_text_and_message_preserved():
    cmd, message = resolve_command(ANALYSIS_COMMANDS, "帮我看看这个要素有没有问题")
    assert cmd is None
    assert message == "帮我看看这个要素有没有问题"


def test_known_word_resolves_and_keeps_full_message():
    cmd, message = resolve_command(ANALYSIS_COMMANDS, "/改类型 改为功能需求")
    assert cmd is not None and cmd.word == "改类型"
    assert message == "/改类型 改为功能需求"  # 原文完整保留（解释 lane 需要）


def test_fullwidth_slash_and_leading_whitespace():
    cmd, _ = resolve_command(ITEM_REVIEW_COMMANDS, "  ／诊断 标准")
    assert cmd is not None and cmd.word == "诊断"


@pytest.mark.parametrize("message,word", [
    ("/改表达：修订为：新表达", "改表达"),   # 冒号终结命令词
    ("/拆分\n1. 甲\n2. 乙", "拆分"),        # 换行终结
    ("/勘误，把「a」改正为「b」", "勘误"),     # 逗号终结
    ("/补入", "补入"),                        # 只有命令词
])
def test_word_terminators(message, word):
    cmd, _ = resolve_command(ANALYSIS_COMMANDS, message)
    assert cmd is not None and cmd.word == word


def test_unknown_word_raises_with_word():
    with pytest.raises(UnknownCommand) as exc:
        resolve_command(ANALYSIS_COMMANDS, "/不存在的命令 x")
    assert exc.value.word == "不存在的命令"


def test_bare_slash_is_unknown():
    with pytest.raises(UnknownCommand) as exc:
        resolve_command(ANALYSIS_COMMANDS, "/ 你好")
    assert exc.value.word == ""


@pytest.mark.parametrize("registry", [ANALYSIS_COMMANDS, ITEM_REVIEW_COMMANDS])
def test_command_guide_covers_registry(registry):
    """命令表覆盖守护：每个注册词都进 system 命令表，operations/guidance 非空。"""
    guide = command_guide(registry)
    assert {g["word"] for g in guide} == set(registry)
    for g in guide:
        assert g["operations"] and g["guidance"]


def test_registries_expected_words():
    assert set(ANALYSIS_COMMANDS) == {"改类型", "改表达", "改范围", "拆分", "合并", "新增遗漏", "勘误", "补入"}
    assert set(ITEM_REVIEW_COMMANDS) == {
        "诊断", "采纳结论", "拒绝结论", "采纳草案", "修订", "找来源", "覆盖确认", "撤回",
    }


def test_find_sources_registered_and_routed_by_stub():
    """A4：/找来源 注册进评审命令表，且桩解释器把无参正文路由到 find_sources 操作码。"""
    from app.adapters.llm import StubItemCommandInterpreter

    cmd, _ = resolve_command(ITEM_REVIEW_COMMANDS, "/找来源")
    assert cmd is not None and cmd.word == "找来源"
    assert cmd.operations == ("find_sources",)

    interp = StubItemCommandInterpreter().interpret("找来源", "/找来源", {})
    assert interp.status == "done"
    assert interp.operation == "find_sources"
    assert interp.operation in cmd.operations  # 解释出的操作码落在白名单内


@pytest.mark.parametrize("registry,word", [
    (ANALYSIS_COMMANDS, "改表达"),
    (ANALYSIS_COMMANDS, "合并"),
    (ITEM_REVIEW_COMMANDS, "修订"),
])
def test_wholesale_replace_commands_require_completeness_criterion(registry, word):
    """整体替换类命令的 guidance 必须含完整性判据与片段分流（防「修订为：300笔」吞掉全文）。"""
    guidance = registry[word].guidance
    assert "独立成立" in guidance
    assert "片段" in guidance
