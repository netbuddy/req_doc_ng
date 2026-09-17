"""验证脚本的机器检查（子包）：一组检查一个文件，不算场景，控制台选 0 时与场景一起跑。

本文件按检查组表的顺序把各组的名字原样导出；检查组表（CHECK_GROUPS）在 verify/__init__.py，顺序不变。
"""

from __future__ import annotations

from tod_kernel.verify.machine.observatory import all_scenarios, expected_summaries, http_get, observatory_checks  # noqa: F401
from tod_kernel.verify.machine.schema import SCHEMA_FILE, SCHEMA_STRUCTURAL, schema_checks  # noqa: F401
from tod_kernel.verify.machine.round_clause import utterance_round_clause_checks  # noqa: F401
from tod_kernel.verify.machine.current_step import current_step_checks  # noqa: F401
from tod_kernel.verify.machine.update_group import update_group_checks  # noqa: F401
from tod_kernel.verify.machine.llm_modes import FAKE_SERVICE_TEXT, closed_port, fake_service, llm_mode_checks  # noqa: F401
from tod_kernel.verify.machine.context_pack import (CTX_DRAFT_ONE, CTX_DRAFT_TWO, CTX_FEEDBACK, CTX_REPLY,
                                                    CTX_REPLY_TWO, CTX_TERM, context_pack_checks)  # noqa: F401
from tod_kernel.verify.machine.console_transcript import console_transcript_checks  # noqa: F401
from tod_kernel.verify.machine.understanding import (FAKE_DATA, FAKE_SLOTS, _ask_once, _raises_definition_error,
                                                     _stub_caller, _understand_once, understand_machine_checks)  # noqa: F401
