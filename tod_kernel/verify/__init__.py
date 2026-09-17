"""验证脚本（包）：场景表、检查组表与总入口 main。

在仓根下运行 `python -m tod_kernel.verify`。本文件把各组模块的名字原样导出，控制台照旧写 verify.run_scenario、verify.SCENARIOS 等；
控制台要改的四个开关（SHOW_EXCHANGES、PRINT_EVENTS、PRINT_CHECKS、EXTRA_SUBSCRIBERS）与 LAST_RUN 住在 verify.base，要在那里读写。
"""

from __future__ import annotations

import sys  # noqa: F401

from tod_kernel.verify.base import (Checker, EXPECTED_UTTERANCES, HOST_JOIN_SECONDS,
                                    KERNEL_THREAD_PREFIX, RUNS_DIR, Run,
                                    SAMPLE_DIR_INPUT, STEP_TWO_RUN_FILES, TASK_DEFS_DIR, ask_events,
                                    banner, call_sequence, check_explainable, check_integrity,
                                    check_kernel_is_task_agnostic, check_matches_step_two, check_run_file,
                                    check_selection_trail, check_sources_and_senders, check_step_final,
                                    check_trace_shape, check_waiting_then_success, expected_source, fake_event, frame,
                                    get_at, intake_def, kernel, named, of_call, run_scenario, stack, stacked,
                                    status_values, step_at, step_sequence, taskdef)  # noqa: F401
from tod_kernel.verify.intake import (BAD_DEFINITIONS, BAD_GOAL_AT_END, BAD_GOAL_EXCEPTION_NUMBER, BAD_GOAL_NAME,
                                      BAD_GOAL_PREDICATE, BAD_GOAL_UTTERANCE_END, EXCEPTION_TOOLS, INTAKE_ANSWERS,
                                      INTAKE_FINAL_DATA, INTAKE_TOOLS, OPTIONS_TEXT, REGISTER_NOTE, _drop_top_key,
                                      _repeat_without_max, _unknown_slot, bad_goal_params, check_exception_call,
                                      check_one_bad_definition, check_other_tool_calls, check_registered_three,
                                      check_terminated, common_checks, exception_common, intake_exception_scenario,
                                      intake_scenario_one, load_error_checks, position, rule_numbers)  # noqa: F401
from tod_kernel.verify.glossary import (CLARIFY_TEXT, FALLBACK_OPTIONS, GLOSSARY_FILE, GLOSSARY_INPUT_ONE,
                                        GLOSSARY_INPUT_TWO, GLOSSARY_PLACES, GLOSSARY_RECORDING, GLOSSARY_SOURCE,
                                        GLOSSARY_TERM_ONE, GLOSSARY_TERM_TWO, GLOSSARY_TOOLS, REPLY_ALTS, REPLY_ASK,
                                        REPLY_CHOOSE_ONE, REPLY_CONFIRM, REPLY_DEFER, REPLY_DEFER_TERM, REPLY_KEY,
                                        REPLY_REVISE, REPLY_REWRITE, REPLY_VAGUE, STEP_TEXT_CHECK, STEP_TEXT_CHOICE,
                                        STEP_TEXT_NONE, STEP_TEXT_REQUEST, STEP_TEXT_SUGGEST, SUGGEST_QUESTION,
                                        acts_of, check_model_record, check_notice, check_question_registered,
                                        check_tool_sequence, check_understanding, confirm_utterance, functions_of,
                                        glossary_call, glossary_def, glossary_scenario_alts, glossary_scenario_ask,
                                        glossary_scenario_confirm, glossary_scenario_defer, glossary_scenario_revise,
                                        glossary_scenario_rewrite, glossary_scenario_vague, glossary_tail,
                                        model_record, notices_of, pattern_number, registered_questions, run_glossary,
                                        segment_source, segment_text, utterance_of, written)  # noqa: F401
from tod_kernel.verify.eval import (EVAL_FILE, EVAL_FLOOR, EVAL_RECORDING, EVAL_STEP, _content_matches, _value_matches,
                                    load_eval_cases, score_case, understand_case, understand_eval_checks)  # noqa: F401
from tod_kernel.verify.machine import (CTX_DRAFT_ONE, CTX_DRAFT_TWO, CTX_FEEDBACK, CTX_REPLY, CTX_REPLY_TWO, CTX_TERM,
                                       FAKE_DATA, FAKE_SERVICE_TEXT, FAKE_SLOTS, SCHEMA_FILE, SCHEMA_STRUCTURAL,
                                       _ask_once, _raises_definition_error, _stub_caller, _understand_once,
                                       all_scenarios, closed_port, console_transcript_checks, context_pack_checks,
                                       current_step_checks, expected_summaries, fake_service, http_get,
                                       llm_mode_checks, observatory_checks, schema_checks, understand_machine_checks,
                                       update_group_checks, utterance_round_clause_checks)  # noqa: F401


# 十个场景：（标题, 跑它的函数）。控制台按这张表列菜单，编号 1 起，0 是全部。
# 一种机制留一个场景（2026-09-16 用户裁定）；第五步把术语澄清换成对话理解的六个场景，「模型不可达」场景退役，
# 它验的事改由对话理解机器检查组里一条断言验（2026-09-18 裁定问题九）。
SCENARIOS = [
    ("材料接入登记，正常（登记完再问）", intake_scenario_one),
    ("材料接入登记，目标写错报异常，使用者选被动终止", intake_exception_scenario),
    ("加载错误，三份坏文件", load_error_checks),
    ("术语澄清，确认", glossary_scenario_confirm),
    ("术语澄清，提修改意见", glossary_scenario_revise),
    ("术语澄清，给整段改写", glossary_scenario_rewrite),
    ("术语澄清，要换一份", glossary_scenario_alts),
    ("术语澄清，推迟并顺手改术语", glossary_scenario_defer),
    ("术语澄清，提问后再确认", glossary_scenario_ask),
    ("术语澄清，含糊后选择", glossary_scenario_vague),
]

# 不算场景的检查组：（标题, 跑它的函数）。控制台选 0 时与场景一起跑，范围与直接跑验证脚本相同。
CHECK_GROUPS = [
    ("观测台", observatory_checks),
    ("任务定义 JSON Schema", schema_checks),
    ("告知异常话的循环次数写法", utterance_round_clause_checks),
    ("当前步的记录、判据与显示", current_step_checks),
    ("更新组先核对再写入", update_group_checks),
    ("模型调用件的三种模式", llm_mode_checks),
    ("上下文包与对话历史的三条规则", context_pack_checks),
    ("控制台的问答打印", console_transcript_checks),
    ("对话理解标注集评测", understand_eval_checks),
    ("对话理解的机器检查", understand_machine_checks),
]


def main() -> int:
    checkers = [scenario() for _, scenario in SCENARIOS] + [group() for _, group in CHECK_GROUPS]
    banner("汇总")
    all_ok = True
    for c in checkers:
        total = len(c.results)
        print(f"{c.title}：共 {total} 条断言，通过 {c.passed} 条，失败 {total - c.passed} 条")
        for description, ok in c.results:
            if not ok:
                print(f"  失败：{description}")
        all_ok = all_ok and c.passed == total
    print("结论：全部断言通过" if all_ok else "结论：有断言失败")
    return 0 if all_ok else 1
