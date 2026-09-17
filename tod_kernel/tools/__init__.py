"""工具与工具表（包）。

工具是「能做什么」的静态定义，工具调用是工具的一次调用。工具表分两层：
- 静态工具表（STATIC_TOOLS）：每个工具的工具名、参数名清单、前置条件、可写槽位，不依赖任何任务定义；
  任务定义加载器（taskdef.py）导入它，用来校验工具名与参数名、求前置条件、拼依据说明。
- 运行工具表（build_table 按任务建）：在静态表之上配好工具实现，询问工具绑定任务定义的话语模板，交给内核。
任务定义数据文件里只有工具名，不含任何代码。
工具放进变更组的新值必须是新构造的对象，内核不再复制。

包内分工：base（工具、工具表、工具规格、共用助手，末尾汇总各模块登记的工具）、dialogue（询问、告知、告知异常）、
understanding（对话理解）、patterns（答疑、标记推迟）、intake（材料接入登记的三个领域工具）、glossary（生成术语释义）。
本文件把各模块的名字原样导出，外部照旧写 from tod_kernel.tools import …。
"""

from __future__ import annotations

from tod_kernel.tools.base import (CATALOG_HEAD, CATEGORIES, MODEL_TOOLS, STATIC_TOOLS, Tool, ToolSpec, ToolTable,
                                   _IMPLS, _plain, build_table, pattern_tool_names, prompt_pack_of, set_at,
                                   system_prompt_for, tool_catalog)  # noqa: F401
from tod_kernel.tools.dialogue import (ABORT_BY_USER, ABORT_UNABLE, ASK_NOTE, EXCEPTION_OPTIONS, EXCEPTION_OUTCOMES,
                                       EXCEPTION_PARAM_NAMES, EXCEPTION_TOOL, NOTICE_KIND, NOTIFY_TOOL, REASK_LINE,
                                       REASK_TEMPLATE_KEY, REDO, REDO_RESULT, ask, exception_utterance, notify,
                                       report_exception)  # noqa: F401
from tod_kernel.tools.understanding import (ACTIVE_FUNCTIONS, ACT_KEYS, AFFIRM, ALTS_FEEDBACK, APPEND, CLARIFY,
                                            CONFIDENCE_FLOOR, DEFER, DEFERRED_MARK, DENY, FALLBACK_CHOICE_ADOPT,
                                            FALLBACK_CHOICE_DEFER, FALLBACK_CHOICE_REVISE, FALLBACK_OPTIONS,
                                            FALLBACK_TEXT, FEEDBACK_SLOT, INFORM, KEY_ASK, KEY_CHOICE,
                                            KEY_CLARIFY_TEXT, KEY_OPTIONS, KEY_PATH, KEY_SLOT, KEY_VALUE,
                                            LAST_QUESTION_SLOT, MISMATCH_OPTIONS, MISMATCH_TEXT, NO_QUESTION,
                                            ORIGINAL_QUESTION, OTHER, PAIRING, QUESTION_CHECK, QUESTION_CHOICE,
                                            QUESTION_REQUEST, QUESTION_SUGGEST, READ_VALUE, REASK_KEY,
                                            REASK_ORIGINAL_KEY, REPLACE_KEY, REPLY_SLOT, REQALTS, REQUEST,
                                            RESPONSE_FUNCTIONS, ROUTES_KEY, STEP_INSERTS, UNDERSTAND_NOTE,
                                            UNDERSTAND_PROVIDES, UNDERSTAND_REPLY_LABEL, UNDERSTAND_REQUIRED_SLOTS,
                                            UNDERSTAND_TOOL, UNDERSTAND_VALUES, defer_target, fallback_acts, land_acts,
                                            normalize_acts, parse_acts, route_modes_of, understand, understand_schema,
                                            value_fits)  # noqa: F401
from tod_kernel.tools.patterns import EXPLAIN_PROVIDES, EXPLAIN_TOOL, MARK_DEFERRED_TOOL, explain, mark_deferred  # noqa: F401
from tod_kernel.tools.intake import (GENERATE_MANIFEST_NOTE, INCLUDED, LIST_DIR_NOTE, MANIFEST_PATH,
                                     REGISTER_FILE_NOTE, SAMPLE_DIRS, generate_manifest, list_dir, register_file)  # noqa: F401
from tod_kernel.tools.glossary import (CONFIRMED_SLOT, DRAFT_DEFINITION_NOTE, DRAFT_PROVIDES, DRAFT_SLOT, DRAFT_TOOL,
                                       SOURCE_SLOT, TERM_SLOT, draft_definition)  # noqa: F401
