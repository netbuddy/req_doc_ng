"""要素状态生命周期（L2）：裁定、复核送检与承接、修订、定稿、就地编辑、重开、历史。

迁移合法性一律由 domain.state_machine 裁定（默认拒绝），本模块不自行判定可达性。
"""
from __future__ import annotations

import json
from typing import Optional

from app.api.schemas import (
    ElementDecisionCommand,
    ElementDecisionPrecheckCommand,
    ElementDecisionPrecheckRead,
    ElementEditCommand,
    ElementHistoryRead,
    ElementHistoryRecordRead,
    ElementOperationRequestResult,
    ElementReopenCommand,
    ElementReviewCommand,
    ElementReviewResultCommand,
    ElementRevisionCommand,
    ElementTriageCommand,
    ElementWorkspaceRead,
    GuardedElementRead,
    RevisionFinalizeCommand,
)
from app.domain.anchors import anchor_from_ranges, build_anchor_json
from app.domain.enums import ElementProcessStatus as ES
from app.domain.enums import ModelVerdict, NoiseTriage
from app.domain.errors import InvalidInput, NotFound, RejectedTransition
from app.domain.state_machine import ElementEvent, element_transition
from app.interfaces import ElementCreateRow
from app.interfaces.repositories import InflightRevisionRow
from app.log import log_event

from app.services.analysis_support import AnalysisSupport
from app.services.analysis_support import _COMPONENT
from app.services.analysis_support import _ranges_to_dicts
from app.services.analysis_workspace import AnalysisWorkspace
from app.services.run_liveness import is_run_alive, lane_for_kind


_EDIT_TYPES = {"adjust_type", "adjust_anchor", "revise_expression"}
# AI 修订与 AI 执行共用一条 rq lane（AgentRun.kind='element_execution'），判死阈值随之
_REVISION_LANE = lane_for_kind("element_execution")
# 二次确认弹层里每条知识项的摘要长度：够认人即可，不把整段表达灌进弹层
_BRIEF_CHARS = 60


class AnalysisLifecycle:
    def __init__(
        self,
        model_orchestration,
        model_results,
        process_records,
        source_assets,
        support: AnalysisSupport,
        workspace: AnalysisWorkspace,
    ) -> None:
        self._model_orchestration = model_orchestration
        self._model_results = model_results
        self._process_records = process_records
        self._source_assets = source_assets
        self._support = support
        self._workspace = workspace

    # ---- 在途修订守卫（确认与预检共用；软拦截，不迁移状态、不阻塞状态机）----

    def _inflight_revisions(
        self, context_ref: str, element_refs: list[str]
    ) -> list[InflightRevisionRow]:
        """这些知识项里，哪几条正被 AI 起草修订。

        仓储按 AgentRun 状态粗筛（queued/started），这里再逐行判活：超判死阈值的僵尸运行
        不算在途——守卫不得因为一个永远回不来的运行把确认永久挡在二次确认后面。判活口径
        走 run_liveness 单点（HK-1），本模块不另写一份存活判断。
        """
        rows = self._process_records.find_inflight_revisions(context_ref, element_refs)
        return [r for r in rows if is_run_alive(_REVISION_LANE, r)]

    def precheck_decide_elements(
        self, command: ElementDecisionPrecheckCommand
    ) -> ElementDecisionPrecheckRead:
        """确认前的在途修订预检：只读，不迁移状态、不升工作区版本。

        单独成端点而不是让确认接口回带守卫结果，是为了让「没有在途修订」这条主路上的
        确认请求与响应一个字节都不变——守卫上线不该改动既有确认契约。
        """
        context = command.parse_context_ref
        self._support._require_parsed(context)
        if not command.element_refs:
            return ElementDecisionPrecheckRead(guarded=[])
        rows = self._inflight_revisions(context, command.element_refs)
        guarded: list[GuardedElementRead] = []
        seen: set[str] = set()
        for row in rows:
            # 一条知识项可能连着多轮修订运行，弹层里只列一次（取最近那次，仓储已按入队倒序）
            if row.element_ref in seen:
                continue
            seen.add(row.element_ref)
            element = self._source_assets.get_element(row.element_ref)
            content = (element.content if element is not None else "") or ""
            guarded.append(GuardedElementRead(
                element_ref=row.element_ref,
                content_brief=content[:_BRIEF_CHARS],
                agent_run_ref=row.agent_run_ref,
                run_status=row.status,
            ))
        if guarded:
            log_event(_COMPONENT, "element.confirm.inflight_revision_guarded",
                      context_ref=context, guarded_count=len(guarded),
                      checked_count=len(command.element_refs), ok=True)
        return ElementDecisionPrecheckRead(guarded=guarded)

    def decide_elements(self, command: ElementDecisionCommand) -> ElementWorkspaceRead:
        context = command.parse_context_ref
        self._support._require_parsed(context)
        self._support._require_version(context, command.workspace_version)
        if command.decision not in ("confirm", "reject"):
            raise InvalidInput(f"不支持的裁定：{command.decision}")
        if not command.element_refs:
            raise InvalidInput("请至少选定一条知识项")
        event = ElementEvent.CONFIRM if command.decision == "confirm" else ElementEvent.REJECT

        # 在途修订守卫（软拦截）：确认会把正在起草的修订稿搁置成孤儿稿，故留痕要记下
        # 「用户是在明知有 AI 在起草的情况下确认的」。只影响留痕与日志，一条都不拦——
        # 人工裁决有最终权威。拒绝不查：拒绝本就把这条连同稿件一起废掉，没有搁置问题。
        guarded_refs: set[str] = set()
        if event is ElementEvent.CONFIRM:
            guarded_refs = {
                g.element_ref for g in self._inflight_revisions(context, command.element_refs)
            }
            if guarded_refs and not command.inflight_revision_ack:
                # 未经二次确认就撞上守卫：前端漏调预检，或预检与确认之间修订刚被派发。
                # 仍旧放行（软拦截），但记 WARN 供事后核对这条注记是怎么来的。
                log_event(_COMPONENT, "element.confirm.inflight_revision_unacked", level="WARN",
                          context_ref=context, element_count=len(guarded_refs), ok=True)

        for ref in command.element_refs:
            row = self._support._require_element(context, ref)
            # 第二道防线（冷审查裁定 C1）：模型判为「建议剔除」且人工尚未撤回的知识项待在候选区里
            # 等人工处置，此时不能确认它。确认不会把它挪出候选区（候选判据只看模型裁定与撤回标记），
            # 于是库里会留下一条「已确认」却在正常列表遍寻不着的知识项，它还会顺带打开条目形成门禁。
            # 只拦确认：撤销/拒绝是候选区的正当出口（处置过的条目随即离箱），一律放行。
            if (
                event is ElementEvent.CONFIRM
                and row.model_verdict == ModelVerdict.SUSPECTED_NOISE.value
                and row.noise_triage != NoiseTriage.RESTORED.value
            ):
                raise InvalidInput("这一条在「AI 建议剔除的候选」里，要先撤回到正常列表才能确认")
            nxt = element_transition(ES(row.process_status), event)  # 默认拒绝在此裁定
            self._source_assets.set_element_status(row.id, nxt.value)
            note = command.reason or ("人工直接确认" if event is ElementEvent.CONFIRM else "人工直接拒绝")
            if row.id in guarded_refs:
                note = f"{note}（确认时有 AI 修订在途，修订稿被搁置）"
            self._support._history(row, command.decision, row.process_status, nxt.value,
                          command.operator_ref, note)
            self._support._record_adoption(
                context, "element_recognition", "element", row.id,
                "adopted" if event is ElementEvent.CONFIRM else "rejected",
                command.operator_ref, f"{command.idempotency_key}:adoption:{row.id}",
            )
            log_event(_COMPONENT, "element.status.transition", element_ref=row.id,
                      from_status=row.process_status, to_status=nxt.value,
                      sm_event=event.value, ok=True)
            if row.id in guarded_refs:
                log_event(_COMPONENT, "element.confirm.over_inflight_revision", level="WARN",
                          element_ref=row.id, acknowledged=command.inflight_revision_ack, ok=True)
        self._process_records.bump_workspace_version(context)
        return self._workspace.read_element_workspace(context)

    def triage_elements(self, command: ElementTriageCommand) -> ElementWorkspaceRead:
        """「AI 建议剔除的候选」的人工处置：撤回到正常列表 / 移回候选区。

        与 decide_elements 的分工：那个是确认生命周期的裁定（确认/拒绝，迁移 process_status）；
        这个只改「这条显示在哪个列表里」，不迁移状态、不升版本——撤回后的知识项仍是「待确认」，
        照样要走确认才能进条目形成。模型证据（model_verdict / verdict_reason）在此一律不动。
        """
        context = command.parse_context_ref
        self._support._require_parsed(context)
        self._support._require_version(context, command.workspace_version)
        if command.action not in ("restore", "return"):
            raise InvalidInput(f"不支持的处置：{command.action}")
        if not command.element_refs:
            raise InvalidInput("请至少选定一条知识项")
        triage = NoiseTriage.RESTORED.value if command.action == "restore" else None

        for ref in command.element_refs:
            row = self._support._require_element(context, ref)
            # 候选区只装模型裁定为「建议剔除」的知识项；对其余条目这个动作没有意义，
            # 与其静默放过留下一个改不回来的标记，不如在此拒绝
            if row.model_verdict != ModelVerdict.SUSPECTED_NOISE.value:
                raise InvalidInput("只有模型裁定为「建议剔除」的知识项才能做候选区处置")
            self._source_assets.set_element_noise_triage(row.id, triage)
            action = "restore_from_triage" if command.action == "restore" else "return_to_triage"
            note = command.reason or (
                "人工撤回到正常列表（模型裁定原样保留）" if command.action == "restore"
                else "人工移回建议剔除候选区"
            )
            # 前后状态相同：本动作不迁移确认生命周期，历史只留「谁在何时把它挪到哪边」
            self._support._history(row, action, row.process_status, row.process_status,
                                   command.operator_ref, note)
            log_event(_COMPONENT, "element.triage.changed", element_ref=row.id,
                      action=action, model_verdict=row.model_verdict,
                      to_triage=triage or "none", ok=True)
        self._process_records.bump_workspace_version(context)
        return self._workspace.read_element_workspace(context)

    def submit_element_review(self, command: ElementReviewCommand) -> ElementOperationRequestResult:
        replay = self._process_records.find_operation_by_idempotency(command.idempotency_key)
        if replay is not None:
            return ElementOperationRequestResult(status="accepted", operation_context_ref=replay)

        precheck = self._support._operation_precheck(command.parse_context_ref, command.workspace_version)
        if precheck is not None:
            return precheck
        if not command.target_element_refs and not command.selected_text_ranges:
            return ElementOperationRequestResult(
                status="rejected_precheck",
                next_action="请选定知识项（核要素）或划选原文范围（扫原文补漏）后再发起审核",
            )

        context = command.parse_context_ref
        # 复核是对话轮次（会话事实：复核进行中），不迁移状态；仅「待确认」要素可复核
        for ref in command.target_element_refs:
            row = self._support._require_element(context, ref)
            if row.process_status != ES.PENDING_CONFIRMATION.value:
                return ElementOperationRequestResult(
                    status="rejected_precheck",
                    next_action="仅「待确认」要素可发起复核；终态要素请先重开（P04）",
                )
            self._support._history(row, "request_review", row.process_status, row.process_status,
                          command.operator_ref, "发起 AI 复核（对话轮次，不迁移状态）")

        payload = json.dumps({
            "workspace_version": command.workspace_version,
            "target_element_refs": command.target_element_refs,
            "selected_text_ranges": _ranges_to_dicts(command.selected_text_ranges),
            "review_intent": command.review_intent,
        }, ensure_ascii=False)
        operation = self._process_records.create_element_operation(
            self._support._project_of(context), context,
            "review", payload, command.operator_ref, command.idempotency_key,
        )
        if command.target_element_refs:
            self._process_records.bump_workspace_version(context)
        run = self._model_orchestration.request_element_review(operation)
        log_event(_COMPONENT, "element.review.submitted", context_ref=context,
                  target_count=len(command.target_element_refs),
                  mode="elements" if command.target_element_refs else "scan")
        return ElementOperationRequestResult(
            status="accepted", operation_context_ref=operation, agent_run_ref=run
        )

    # AEP-024 —— 模型编排内部回交：核要素→写结论证据（状态留在分析中）；
    # 扫原文补漏→新「待确认」要素并入集合；失败→留失败记录，可重试（不丢工作）。
    def accept_element_review_result(
        self, command: ElementReviewResultCommand
    ) -> ElementWorkspaceRead:
        op = self._process_records.read_element_operation(command.operation_context_ref)
        if op is None or op.kind != "review":
            raise RejectedTransition("复核操作上下文不存在；AEP-024 仅承接复核回交")
        result = self._model_results.read_stage_payload(command.model_result_ref)
        if result is None:
            raise RejectedTransition("复核结果 LDM-015 不存在")

        context = op.parse_context_ref
        op_payload = json.loads(op.payload)
        targets = list(op_payload.get("target_element_refs", []))
        body = json.loads(result.payload) if result.payload else {}

        if result.result_code == "review_failed":
            # 失败停靠：要素停「分析中」不丢失；留失败原因，可重试或人工直接裁定。
            for ref in targets:
                row = self._source_assets.get_element(ref)
                if row is not None:
                    self._source_assets.set_element_review(
                        ref, None, result.basis or "复核失败，可重试或人工直接裁定", None
                    )
                    self._support._history(row, "review_failed", row.process_status, row.process_status,
                                  "system", result.basis or "复核失败")
            log_event(_COMPONENT, "element.review.failed", level="WARN",
                      context_ref=context, ok=False)
            self._process_records.bump_workspace_version(context)
            return self._workspace.read_element_workspace(context)

        if body.get("mode") == "scan":
            items = body.get("items", [])
            creates: list[ElementCreateRow] = []
            canvas = self._support._build_canvas(context)
            raw_text = canvas.raw_text if canvas else ""
            material_ref = canvas.material_ref if canvas else ""
            for it in items:
                anchor = build_anchor_json(material_ref, raw_text, it.get("source_quote"))
                creates.append(ElementCreateRow(
                    element_type=it.get("element_type", "goal"),
                    content=it.get("content", ""),
                    source_anchor=anchor,
                    confidence=it.get("confidence"),
                    process_status=ES.PENDING_CONFIRMATION.value,
                    correction_state="review_scan",
                    correction_note="扫原文补漏识别",
                ))
            if creates:
                new_ids = self._source_assets.add_elements(context, creates)
                for nid in new_ids:
                    row = self._source_assets.get_element(nid)
                    if row is not None:
                        self._support._history(row, "register", None, ES.PENDING_CONFIRMATION.value,
                                      op.operator_ref, "扫原文补漏登记（待确认）")
            log_event(_COMPONENT, "element.review.scan_registered",
                      context_ref=context, new_count=len(creates), ok=True)
        else:
            for f in body.get("findings", []):
                ref = f.get("element_ref")
                row = self._source_assets.get_element(ref) if ref else None
                if row is None:
                    continue
                self._source_assets.set_element_review(
                    ref, f.get("conclusion"), f.get("opinion"), f.get("revised_content"),
                )
                self._support._history(row, "review_result", row.process_status, row.process_status,
                              "system", f"AI 复核结论：{f.get('conclusion')}｜{f.get('opinion','')}")
                # TC-08：facet 判定写完备度投影（过程记录，element_version 为版本锚；
                # 仅本回交入口可写，无 facet（无判据/解析降级）则不写、不清旧投影）
                facet_rows = self._workspace._facet_rows_of(f, row.id, row.version, command.model_result_ref)
                if facet_rows:
                    self._process_records.replace_facet_projection(row.id, facet_rows)
            log_event(_COMPONENT, "element.review.concluded",
                      context_ref=context, finding_count=len(body.get("findings", [])), ok=True)

        self._process_records.bump_workspace_version(context)
        return self._workspace.read_element_workspace(context)

    def revise_element(self, command: ElementRevisionCommand) -> ElementOperationRequestResult:
        context = command.parse_context_ref
        self._support._require_parsed(context)
        self._support._require_version(context, command.workspace_version)
        row = self._support._require_element(context, command.element_ref)
        # 修订迭代是对话轮次（会话事实：未采纳修订稿），不迁移状态；仅「待确认」要素可迭代
        if row.process_status != ES.PENDING_CONFIRMATION.value:
            raise RejectedTransition("仅「待确认」要素可迭代修订稿；终态要素请先重开（P04）")

        if command.mode == "manual":
            if not (command.draft_content or "").strip():
                raise InvalidInput("人工修订必须提供修订稿内容")
            self._source_assets.set_revision_draft(row.id, command.draft_content)
            self._support._history(row, "revision_iterated", row.process_status, row.process_status,
                          command.operator_ref, "人工更新修订稿（会话数据，未采纳不生效）")
            self._process_records.bump_workspace_version(context)
            return ElementOperationRequestResult(status="accepted")

        if command.mode != "ai":
            raise InvalidInput(f"不支持的修订方式：{command.mode}")
        if not (command.instruction or "").strip():
            raise InvalidInput("AI 修订必须携带修订指令")
        payload = json.dumps({
            "workspace_version": command.workspace_version,
            "operation_type": "revise_expression",
            "target_element_refs": [row.id],
            "selected_text_ranges": [],
            "execution_instruction": command.instruction,
        }, ensure_ascii=False)
        operation = self._process_records.create_element_operation(
            self._support._project_of(context), context,
            "revision", payload, command.operator_ref, command.idempotency_key,
        )
        self._process_records.bump_workspace_version(context)
        run = self._model_orchestration.request_element_execution(operation)
        log_event(_COMPONENT, "element.revision.submitted", element_ref=row.id)
        return ElementOperationRequestResult(
            status="accepted", operation_context_ref=operation, agent_run_ref=run
        )

    def finalize_revision(self, command: RevisionFinalizeCommand) -> ElementOperationRequestResult:
        context = command.parse_context_ref
        self._support._require_parsed(context)
        self._support._require_version(context, command.workspace_version)
        row = self._support._require_element(context, command.element_ref)
        current = ES(row.process_status)

        if command.action == "adopt":
            # 采纳即确认：content = 修订稿、版本 +1、待确认 → 已确认
            if not (row.revision_draft or "").strip():
                raise InvalidInput("该要素没有未采纳的修订稿")
            novel = self._support._novel_fact_tokens(context, row.revision_draft)
            if novel:
                # 超出原文守卫：修订稿引入原文（含补块）没有的事实 → 阻断，先补入依据
                log_event(_COMPONENT, "element.revision.adopt_blocked", element_ref=row.id,
                          novel_tokens=novel, ok=False)
                return ElementOperationRequestResult(
                    status="rejected_precheck",
                    next_action=("修订稿包含原文没有的事实（" + "、".join(novel)
                                 + "）。请先通过「补入」登记依据，再重新采纳。"),
                )
            nxt = element_transition(current, ElementEvent.ADOPT_REVISION)
            self._source_assets.apply_element_edit(
                row.id, None, row.revision_draft, None, "采纳修订稿（采纳即确认）"
            )
            self._source_assets.set_revision_draft(row.id, None)
            self._source_assets.set_element_status(row.id, nxt.value)
            self._support._history(row, "adopt_revision", current.value, nxt.value,
                          command.operator_ref, "采纳修订稿（采纳即确认）")
            self._support._record_adoption(
                context, "element_review", "element", row.id, "adopted_with_revision",
                command.operator_ref, f"{command.idempotency_key}:adoption:{row.id}",
            )
        elif command.action == "abandon":
            # 不采纳 = 清除修订稿草稿；状态不变（对话可继续）
            nxt = current
            self._source_assets.set_revision_draft(row.id, None)
            self._support._history(row, "discard_revision_draft", current.value, current.value,
                          command.operator_ref, "不采纳修订稿（清除草稿，状态不变）")
        else:
            raise InvalidInput(f"不支持的修订定夺：{command.action}")

        log_event(_COMPONENT, "element.revision.finalized", element_ref=row.id,
                  action=command.action, to_status=nxt.value, ok=True)
        self._process_records.bump_workspace_version(context)
        return ElementOperationRequestResult(status="accepted")

    def edit_element(self, command: ElementEditCommand) -> ElementWorkspaceRead:
        context = command.parse_context_ref
        self._support._require_parsed(context)
        self._support._require_version(context, command.workspace_version)
        if command.edit_type not in _EDIT_TYPES:
            raise InvalidInput(f"不支持的就地修订：{command.edit_type}")
        row = self._support._require_element(context, command.element_ref)

        new_type: Optional[str] = None
        new_content: Optional[str] = None
        new_anchor: Optional[str] = None
        if command.edit_type == "adjust_type":
            if not command.new_element_type:
                raise InvalidInput("调整类型必须给出新类型")
            new_type = command.new_element_type.value
            note = command.reason or f"改类型：{row.element_type} → {new_type}"
        elif command.edit_type == "adjust_anchor":
            canvas = self._support._build_canvas(context)
            raw_text = canvas.raw_text if canvas else ""
            material_ref = canvas.material_ref if canvas else ""
            ranges = _ranges_to_dicts(command.selected_text_ranges)
            new_anchor = anchor_from_ranges(material_ref, raw_text, ranges) if ranges else None
            if not new_anchor:
                raise InvalidInput("调整来源范围必须提供新的原文选区")
            note = command.reason or "改范围：调整来源锚点"
        else:  # revise_expression
            if not (command.new_content or "").strip():
                raise InvalidInput("改写表达必须提供修订后内容")
            new_content = command.new_content
            note = command.reason or "改表达：一次性改写"

        self._source_assets.apply_element_edit(row.id, new_type, new_content, new_anchor, note)
        updated = self._source_assets.get_element(row.id)
        self._support._history(row, command.edit_type, row.process_status, row.process_status,
                      command.operator_ref, note,
                      snapshot={"element_type": row.element_type, "content": row.content,
                                "source_anchor": row.source_anchor, "version": row.version})
        log_event(_COMPONENT, "element.edited", element_ref=row.id,
                  edit_type=command.edit_type,
                  version=updated.version if updated else row.version + 1, ok=True)
        self._process_records.bump_workspace_version(context)
        return self._workspace.read_element_workspace(context)

    def reopen_element(self, command: ElementReopenCommand) -> ElementWorkspaceRead:
        context = command.parse_context_ref
        self._support._require_parsed(context)
        self._support._require_version(context, command.workspace_version)
        row = self._support._require_element(context, command.element_ref)
        current = ES(row.process_status)
        nxt = element_transition(current, ElementEvent.REOPEN)  # 仅已确认/已撤销可重开

        # 新版本：版本+1；原结论进入历史快照保留
        self._source_assets.apply_element_edit(row.id, None, None, None, None)
        # 未采纳的修订稿要活过回流。clear_review 会把复核结论、复核依据、修订稿一并清空——
        # 对前两者是对的（回流是重新处置，旧结论已进历史快照），对修订稿不对：已确认条目上
        # 那份搁置的修订稿，回流的目的恰恰就是回来采纳它，清掉等于把用户要办的事办没了。
        # 这里先接住再写回，不改 clear_review 本身的语义（别处的重开仍按原样清干净）。
        pending_draft = row.revision_draft
        self._source_assets.set_element_status(row.id, nxt.value, clear_review=True)
        if (pending_draft or "").strip():
            self._source_assets.set_revision_draft(row.id, pending_draft)
            log_event(_COMPONENT, "element.reflow.revision_draft_preserved",
                      element_ref=row.id, from_status=current.value, ok=True)
        action = "reopen" if current is ES.REVOKED else "reflow"
        note = command.reason or ("误撤销重开（原撤销结论保留于历史）" if current is ES.REVOKED
                                  else "下游回流重新处置（旧版本保留于历史）")
        self._support._history(row, action, current.value, nxt.value, command.operator_ref, note,
                      snapshot={"content": row.content, "version": row.version,
                                "review_conclusion": row.review_conclusion,
                                "review_basis": row.review_basis})
        log_event(_COMPONENT, "element.reopened", element_ref=row.id,
                  from_status=current.value, ok=True)
        self._process_records.bump_workspace_version(context)
        return self._workspace.read_element_workspace(context)

    def read_element_history(self, context_ref: str, element_ref: str) -> ElementHistoryRead:
        if not self._process_records.parse_context_exists(context_ref):
            raise NotFound("识别请求上下文不存在")
        if self._source_assets.get_element(element_ref) is None:
            raise NotFound("知识项不存在")
        records = [
            ElementHistoryRecordRead(
                version=h.version, action=h.action,
                from_status=h.from_status, to_status=h.to_status,
                operator_ref=h.operator_ref, note=h.note, snapshot=h.snapshot, at=h.at,
            )
            for h in self._source_assets.element_history_of(element_ref)
        ]
        return ElementHistoryRead(element_ref=element_ref, records=records)
