"""区5 对话（L3）：命令词确定性解析、解释 lane 调用、参数校验与操作派发。

AEP-096：命令词由 domain.chat_commands 确定性解析（未知命令不调模型），解释结果
只在该命令允许的操作集合内派发；派发目标是 L2 业务模块，本模块不自行写库。
"""

from typing import Optional

from app.api.schemas import (
    ElementAiExecutionCommand,
    ElementDialogueCommand,
    ElementDialogueResult,
    ElementEditCommand,
    ElementOperationRequestResult,
    ElementReviewCommand,
    ElementRevisionCommand,
    ElementWorkspaceRead,
    ManualElementCorrectionCommand,
    MaterialErratumCommand,
    MaterialSupplementCommand,
)
from app.domain.chat_commands import (
    ANALYSIS_COMMANDS,
    ANALYSIS_FREETEXT_OPERATIONS,
    UnknownCommand,
    resolve_command,
)
from app.domain.enums import AiRequestStage, ElementType
from app.domain.errors import InvalidInput, RejectedTransition
from app.log import log_event

from app.services.analysis_change_drafts import AnalysisChangeDrafts
from app.services.analysis_lifecycle import AnalysisLifecycle
from app.services.analysis_source_changes import AnalysisSourceChanges
from app.services.analysis_support import AnalysisSupport
from app.services.analysis_support import _COMPONENT
from app.services.analysis_workspace import AnalysisWorkspace


_DIALOGUE_OPERATION_LABELS = {
    "edit.adjust_type": "改类型（就地修订）",
    "edit.revise_expression": "改表达（就地修订）",
    "edit.adjust_anchor": "改范围（就地修订）",
    "manual.split": "拆分（变更草案）",
    "manual.merge": "合并（变更草案）",
    "manual.add_missing": "新增遗漏（变更草案）",
    "ai_execution.split": "拆分（AI 执行）",
    "ai_execution.merge": "合并（AI 起草）",
    "erratum": "勘误（改源）",
    "supplement": "补入（改源）",
    "revise.ai": "AI 修订迭代",
    "review": "AI 复核",
}


class AnalysisDialogue:
    def __init__(
        self,
        command_interpreter,
        support: AnalysisSupport,
        workspace: AnalysisWorkspace,
        lifecycle: AnalysisLifecycle,
        source_changes: AnalysisSourceChanges,
        change_drafts: AnalysisChangeDrafts,
    ) -> None:
        self._command_interpreter = command_interpreter
        self._support = support
        self._workspace = workspace
        self._lifecycle = lifecycle
        self._source_changes = source_changes
        self._change_drafts = change_drafts

    def element_dialogue(
        self, command: ElementDialogueCommand,
        on_stage: Optional[callable] = None,  # AiRequestStage 稳定码回调（SSE 流式链路回执）
    ) -> ElementDialogueResult:
        def _stage(stage: AiRequestStage) -> None:
            if on_stage is not None:
                on_stage(stage.value)

        context = command.parse_context_ref
        if not (command.message or "").strip():
            return ElementDialogueResult(outcome="clarify", message="请输入内容后再发送。")

        try:
            chat_command, _ = resolve_command(ANALYSIS_COMMANDS, command.message)
        except UnknownCommand as exc:
            words = "、".join(f"/{w}" for w in ANALYSIS_COMMANDS)
            log_event(_COMPONENT, "dialogue.command.unknown", level="WARN",
                      context_ref=context, word=exc.word, ok=False)
            return ElementDialogueResult(
                outcome="unknown_command", command_word=exc.word,
                message=f"未知命令 /{exc.word}。可用命令：{words}；不带斜杠即自由对话。",
            )
        word = chat_command.word if chat_command else None
        log_event(_COMPONENT, "dialogue.command.resolved", context_ref=context,
                  command_word=word or "(free-text)")
        _stage(AiRequestStage.ACCEPTED)

        if self._command_interpreter is None:
            raise InvalidInput("命令解释能力未装配")

        try:
            self._support._require_parsed(context)
            self._support._require_version(context, command.workspace_version)
            _stage(AiRequestStage.INTERPRETING)
            interpretation = self._command_interpreter.interpret(
                word, command.message, self._dialogue_context(context, command)
            )
            if interpretation.failed:
                log_event(_COMPONENT, "dialogue.interpret.completed", level="WARN",
                          context_ref=context, command_word=word, ok=False)
                return ElementDialogueResult(
                    outcome="rejected_precheck", command_word=word,
                    message="命令解释服务暂不可用，请稍后重试；确认 / 拒绝等直发操作不受影响。",
                )
            if interpretation.status in ("clarify", "cannot_comply"):
                log_event(_COMPONENT, "dialogue.interpret.refused", context_ref=context,
                          command_word=word, status=interpretation.status)
                return ElementDialogueResult(
                    outcome=interpretation.status, command_word=word,
                    message=interpretation.reason or "请补充信息后重试。",
                )
            allowed = chat_command.operations if chat_command else ANALYSIS_FREETEXT_OPERATIONS
            if interpretation.operation not in allowed:
                log_event(_COMPONENT, "dialogue.params.invalid", level="WARN",
                          context_ref=context, command_word=word,
                          operation=interpretation.operation, ok=False)
                return ElementDialogueResult(
                    outcome="clarify", command_word=word, operation=interpretation.operation,
                    message="该命令不支持解释出的操作，请换个说法或换用对应命令。",
                )
            log_event(_COMPONENT, "dialogue.interpret.completed", context_ref=context,
                      command_word=word, operation=interpretation.operation, ok=True)
            _stage(AiRequestStage.DISPATCHING)
            result = self._dispatch_dialogue_operation(
                command, word, interpretation.operation, dict(interpretation.params)
            )
        except (InvalidInput, RejectedTransition) as exc:
            log_event(_COMPONENT, "dialogue.dispatch.failed", level="WARN",
                      context_ref=context, command_word=word, reason=str(exc), ok=False)
            return ElementDialogueResult(
                outcome="rejected_precheck", command_word=word, message=str(exc),
            )
        log_event(_COMPONENT, "dialogue.dispatch.completed", context_ref=context,
                  command_word=word, operation=result.operation, outcome=result.outcome, ok=True)
        return result

    def _dialogue_context(self, context_ref: str, command: ElementDialogueCommand) -> dict:
        """解释 lane 的工作区上下文（控 token：清单截 60 条、表达截 40 字、选区截 300 字）。"""
        workspace = self._workspace.read_element_workspace(context_ref)
        selected_ref = (
            command.target_element_refs[0] if command.target_element_refs
            else workspace.selected_element_ref
        )
        selected = next((e for e in workspace.elements if e.id == selected_ref), None)
        selection_text = "".join(r.exact for r in command.selected_text_ranges)[:300]
        return {
            "selected_element": (
                {"id": selected.id, "type": selected.element_type.value,
                 "status": selected.process_status.value, "content": selected.content,
                 "has_revision_draft": bool((selected.revision_draft or "").strip())}
                if selected else None
            ),
            "elements": [
                {"id": e.id, "type": e.element_type.value,
                 "status": e.process_status.value, "content": e.content[:40]}
                for e in workspace.elements[:60]
            ],
            "selection_text": selection_text,
            "has_selection": bool(command.selected_text_ranges),
            "checked_element_refs": command.target_element_refs,
        }

    def _dispatch_dialogue_operation(
        self, command: ElementDialogueCommand, word: Optional[str],
        operation: str, params: dict,
    ) -> ElementDialogueResult:
        context = command.parse_context_ref
        dispatch_key = f"{command.idempotency_key}:dispatch"
        label = _DIALOGUE_OPERATION_LABELS.get(operation, operation)

        def _clarify(message: str) -> ElementDialogueResult:
            log_event(_COMPONENT, "dialogue.params.invalid", level="WARN",
                      context_ref=context, command_word=word, operation=operation, ok=False)
            return ElementDialogueResult(
                outcome="clarify", command_word=word, operation=operation,
                operation_label=label, message=message,
            )

        def _executed(workspace: ElementWorkspaceRead, message: Optional[str] = None) -> ElementDialogueResult:
            return ElementDialogueResult(
                outcome="executed", command_word=word, operation=operation,
                operation_label=label, params_echo=params, message=message,
                workspace=workspace, next_action=workspace.next_action,
            )

        def _from_request(result: ElementOperationRequestResult) -> ElementDialogueResult:
            if result.status != "accepted":
                return ElementDialogueResult(
                    outcome="rejected_precheck", command_word=word, operation=operation,
                    operation_label=label, message=result.next_action,
                )
            return ElementDialogueResult(
                outcome="queued", command_word=word, operation=operation,
                operation_label=label, params_echo=params,
                agent_run_ref=result.agent_run_ref,
            )

        target = command.target_element_refs[0] if command.target_element_refs else None
        needs_target = operation in (
            "edit.adjust_type", "edit.revise_expression", "edit.adjust_anchor",
            "manual.split", "ai_execution.split", "revise.ai",
        )
        if needs_target and not target:
            return _clarify("请先在区1 选中目标要素。")

        if operation.startswith("edit."):
            edit_type = operation.split(".", 1)[1]
            new_type: Optional[ElementType] = None
            if edit_type == "adjust_type":
                raw_type = str(params.get("new_element_type") or "").strip()
                try:
                    new_type = ElementType(raw_type)
                except ValueError:
                    return _clarify("请写出有效的目标类型（如「功能需求」「约束」）。")
            if edit_type == "revise_expression" and not str(params.get("new_content") or "").strip():
                return _clarify("请写出修订后的完整表达（「修订为：<表达>」）。")
            if edit_type == "adjust_anchor" and not command.selected_text_ranges:
                return _clarify("请先在区3 选中新的原文范围。")
            workspace = self._lifecycle.edit_element(ElementEditCommand(
                parse_context_ref=context, workspace_version=command.workspace_version,
                element_ref=target, edit_type=edit_type, new_element_type=new_type,
                new_content=params.get("new_content"),
                selected_text_ranges=command.selected_text_ranges,
                operator_ref=command.operator_ref, idempotency_key=dispatch_key,
            ))
            # 就地修订软提示：人工直发有最终权威不阻断，但表达超出来源证据时标记偏离
            drift_note: Optional[str] = None
            if edit_type == "revise_expression":
                edited = next((e for e in workspace.elements if e.id == target), None)
                if edited and edited.source_drift_tokens:
                    tokens = "、".join(edited.source_drift_tokens)
                    drift_note = (f"注意：新表达包含原文与补入块中没有的事实（{tokens}），"
                                  "已标记「偏离原文」。材料记错了→「/勘误」更正原文；"
                                  "业务决策变了→「/补入」登记依据。")
                    log_event(_COMPONENT, "element.edit.source_drift", level="WARN",
                              element_ref=target, drift_tokens=edited.source_drift_tokens, ok=True)
            return _executed(workspace, message=drift_note)

        if operation.startswith("manual."):
            op_type = operation.split(".", 1)[1]
            if op_type == "split":
                parts = [p.strip() for p in str(params.get("new_content") or "").split("\n") if p.strip()]
                if len(parts) < 2:
                    return _clarify("拆分至少需要两条结果（每行一条）；不写拆法可只写要求由 AI 建议。")
                targets = [target]
            elif op_type == "merge":
                targets = [str(r) for r in (params.get("target_element_refs") or [])]
                current = self._support._current_elements(context)
                targets = list(dict.fromkeys(targets))
                if len(targets) < 2 or any(t not in current for t in targets):
                    return _clarify("合并需要至少两条当前集合内的要素；请用「要素表达」点名参与要素。")
            else:  # add_missing
                if not str(params.get("new_content") or "").strip():
                    return _clarify("请写出要补登的要素表达，或先在区3 选中原文。")
                targets = []
            # 新增遗漏的类型：解释模型给出（用户点名则取用户所说，否则按内容判断）；
            # 缺省不再恒为「目标」——无类型信息时才由服务层兜底。
            manual_new_type: Optional[ElementType] = None
            if op_type == "add_missing":
                raw_new_type = str(params.get("new_element_type") or "").strip()
                if raw_new_type:
                    try:
                        manual_new_type = ElementType(raw_new_type)
                    except ValueError:
                        return _clarify(
                            "请写出有效的知识项类型（如「接口需求」「业务规则」），或不写类型交给 AI 判断。"
                        )
            draft = self._change_drafts.submit_manual_element_correction(ManualElementCorrectionCommand(
                parse_context_ref=context, workspace_version=command.workspace_version,
                operation_type=op_type, target_element_refs=targets,
                selected_text_ranges=command.selected_text_ranges,
                new_content=params.get("new_content"),
                new_element_type=manual_new_type,
                operator_ref=command.operator_ref, idempotency_key=dispatch_key,
            ))
            return _executed(self._workspace.read_element_workspace(context), message=draft.next_action)

        if operation.startswith("ai_execution."):
            op_type = operation.split(".", 1)[1]
            if op_type == "merge":
                targets = list(dict.fromkeys(str(r) for r in (params.get("target_element_refs") or [])))
                current = self._support._current_elements(context)
                if len(targets) < 2 or any(t not in current for t in targets):
                    return _clarify("合并需要至少两条当前集合内的要素；请用「要素表达」点名参与要素。")
            else:
                targets = [target]
            return _from_request(self._change_drafts.submit_element_ai_execution(ElementAiExecutionCommand(
                parse_context_ref=context, workspace_version=command.workspace_version,
                operation_type=op_type, target_element_refs=targets,
                selected_text_ranges=command.selected_text_ranges,
                execution_instruction=str(params.get("instruction") or command.message),
                operator_ref=command.operator_ref, idempotency_key=dispatch_key,
            )))

        if operation == "erratum":
            workspace = self._source_changes.material_erratum(MaterialErratumCommand(
                parse_context_ref=context, workspace_version=command.workspace_version,
                old_text=str(params.get("old_text") or ""), new_text=str(params.get("new_text") or ""),
                operator_ref=command.operator_ref, idempotency_key=dispatch_key,
            ))
            return _executed(workspace)

        if operation == "supplement":
            workspace = self._source_changes.material_supplement(MaterialSupplementCommand(
                parse_context_ref=context, workspace_version=command.workspace_version,
                content=str(params.get("content") or ""), basis=str(params.get("basis") or ""),
                target_element_refs=command.target_element_refs,
                operator_ref=command.operator_ref, idempotency_key=dispatch_key,
            ))
            return _executed(workspace)

        if operation == "revise.ai":
            return _from_request(self._lifecycle.revise_element(ElementRevisionCommand(
                parse_context_ref=context, workspace_version=command.workspace_version,
                element_ref=target, mode="ai",
                instruction=str(params.get("instruction") or command.message),
                operator_ref=command.operator_ref, idempotency_key=dispatch_key,
            )))

        if operation == "review":
            return _from_request(self._lifecycle.submit_element_review(ElementReviewCommand(
                parse_context_ref=context, workspace_version=command.workspace_version,
                target_element_refs=command.target_element_refs,
                selected_text_ranges=command.selected_text_ranges,
                review_intent=str(params.get("review_intent") or command.message),
                operator_ref=command.operator_ref, idempotency_key=dispatch_key,
            )))

        raise InvalidInput(f"不支持的对话操作：{operation}")
