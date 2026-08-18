"""变更草案流水线（L2）：AI 执行送检与承接、人工拆分/合并/新增、草案生成与确认创建。

P04 版本关系层：草案 → 确认创建，产物一律「待确认」。
"""
from __future__ import annotations

import json
from typing import Optional

from app.api.schemas import (
    ElementAiExecutionCommand,
    ElementAiExecutionResultCommand,
    ElementChangeConfirmCommand,
    ElementChangeDraftRead,
    ElementOperationRequestResult,
    ElementWorkspaceRead,
    ManualElementCorrectionCommand,
)
from app.domain.anchors import anchor_from_ranges, build_anchor_json
from app.domain.enums import ElementProcessStatus as ES
from app.domain.errors import InvalidInput, RejectedTransition
from app.interfaces import ElementCreateRow
from app.log import log_event

from app.services.analysis_support import AnalysisSupport
from app.services.analysis_support import _COMPONENT
from app.services.analysis_support import _ranges_to_dicts
from app.services.analysis_workspace import AnalysisWorkspace


_AI_EXECUTION_TYPES = {"add_missing", "split", "merge", "adjust_type", "adjust_anchor", "revise_expression"}
_MANUAL_OPERATION_TYPES = {"add_missing", "split", "merge"}


class AnalysisChangeDrafts:
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

    def submit_element_ai_execution(
        self, command: ElementAiExecutionCommand
    ) -> ElementOperationRequestResult:
        replay = self._process_records.find_operation_by_idempotency(command.idempotency_key)
        if replay is not None:
            return ElementOperationRequestResult(status="accepted", operation_context_ref=replay)

        precheck = self._support._operation_precheck(command.parse_context_ref, command.workspace_version)
        if precheck is not None:
            return precheck
        if command.operation_type not in _AI_EXECUTION_TYPES:
            return ElementOperationRequestResult(
                status="rejected_precheck", next_action=f"不支持的操作类型：{command.operation_type}"
            )
        if not command.execution_instruction.strip():
            return ElementOperationRequestResult(
                status="rejected_precheck", next_action="AI 执行必须携带用户指定操作说明"
            )
        if not command.target_element_refs and command.operation_type != "add_missing":
            return ElementOperationRequestResult(
                status="rejected_precheck", next_action="AI 执行必须选定目标要素"
            )

        payload = json.dumps({
            "workspace_version": command.workspace_version,
            "operation_type": command.operation_type,
            "target_element_refs": command.target_element_refs,
            "selected_text_ranges": _ranges_to_dicts(command.selected_text_ranges),
            "execution_instruction": command.execution_instruction,
        }, ensure_ascii=False)
        operation = self._process_records.create_element_operation(
            self._support._project_of(command.parse_context_ref), command.parse_context_ref,
            "execution", payload, command.operator_ref, command.idempotency_key,
        )
        run = self._model_orchestration.request_element_execution(operation)
        return ElementOperationRequestResult(
            status="accepted", operation_context_ref=operation, agent_run_ref=run
        )

    # AEP-028 —— 模型编排内部回交：
    # kind=execution → 校验结构化结果 → 转入变更草案或停靠；
    # kind=revision → 修订稿迭代（写 revision_draft，要素保持「修订中」）。
    def accept_element_ai_execution_result(
        self, command: ElementAiExecutionResultCommand
    ):
        op = self._process_records.read_element_operation(command.operation_context_ref)
        if op is None or op.kind not in ("execution", "revision"):
            raise RejectedTransition("执行操作上下文不存在；AEP-028 仅承接 AI 执行/修订回交")
        result = self._model_results.read_stage_payload(command.model_result_ref)
        if result is None:
            raise RejectedTransition("执行结果 LDM-015 不存在")

        op_payload = json.loads(op.payload)
        context = op.parse_context_ref
        targets = list(op_payload.get("target_element_refs", []))

        if op.kind == "revision":
            ref = targets[0] if targets else None
            row = self._source_assets.get_element(ref) if ref else None
            exec_payload = json.loads(result.payload) if result.payload else {}
            after = exec_payload.get("after_items", [])
            if row is not None:
                if result.result_code == "execution_failed" or not after:
                    # 修订失败：要素停「修订中」不丢，留失败记录可重试
                    self._support._history(row, "revision_failed", row.process_status, row.process_status,
                                  "system", result.basis or "AI 修订失败，可重试或人工修订")
                else:
                    self._source_assets.set_revision_draft(row.id, after[0].get("content"))
                    self._support._history(row, "revision_iterated", row.process_status, row.process_status,
                                  "system", "AI 修订稿更新")
            self._process_records.bump_workspace_version(context)
            return self._workspace.read_element_workspace(context)

        operation_type = op_payload.get("operation_type", "revise_expression")
        version = self._process_records.read_workspace_version(context)
        if result.result_code == "execution_failed":
            # basis 携带执行器/模型给出的原因（含 cannot_comply 的拒绝说明），停靠原样透传给用户
            reason = result.basis or "AI 执行失败，未产生可承接的结构化变更结果"
            draft_ref = self._save_draft(
                context, version, operation_type, "ai_execution", [], targets, [],
                op_payload.get("selected_text_ranges", []),
                [reason],
                "stopped", f"{reason}；可调整指令重试、先补入依据或转人工校正",
            )
            return self._read_draft_read(draft_ref, context)

        exec_payload = json.loads(result.payload) if result.payload else {}
        after = exec_payload.get("after_items", [])
        items = self._build_items(context, operation_type, targets, after, note="AI 执行")
        gate, gate_next = self._judge_create_gate(context, items)
        draft_ref = self._save_draft(
            context, version, operation_type, "ai_execution", items, targets, [],
            op_payload.get("selected_text_ranges", []),
            self._impact_summary(items), gate, gate_next,
        )
        return self._read_draft_read(draft_ref, context)

    # AEP-027 —— 人工校正（版本关系层：拆分/合并/新增）进入变更草案。
    def submit_manual_element_correction(
        self, command: ManualElementCorrectionCommand
    ) -> ElementChangeDraftRead:
        context = command.parse_context_ref
        self._support._require_parsed(context, allow_unprocessable=command.operation_type == "add_missing")
        self._support._require_version(context, command.workspace_version)
        op = command.operation_type
        if op not in _MANUAL_OPERATION_TYPES:
            raise InvalidInput(f"不支持的操作类型：{op}（改类型/改范围/改表达请用就地修订）")
        if not (command.new_content or command.new_element_type or command.selected_text_ranges):
            raise InvalidInput("人工校正必须包含目标内容、目标类型或目标锚点中的至少一项")

        current = self._support._current_elements(context)
        targets = command.target_element_refs
        for tid in targets:
            if tid not in current:
                raise RejectedTransition(f"目标要素 {tid} 不在当前集合（版本冲突或已被替代），请刷新工作区")

        canvas = self._support._build_canvas(context)
        raw_text = canvas.raw_text if canvas else ""
        material_ref = canvas.material_ref if canvas else ""
        ranges = _ranges_to_dicts(command.selected_text_ranges)
        anchor_from_selection = anchor_from_ranges(material_ref, raw_text, ranges) if ranges else None
        items: list[dict] = []

        if op == "add_missing":
            if not command.new_content:
                raise InvalidInput("新增遗漏要素必须提供要素内容")
            items.append(self._create_item(
                context,
                element_type=command.new_element_type.value if command.new_element_type else "goal",
                content=command.new_content,
                anchor_json=anchor_from_selection,
                quote=command.new_content if not anchor_from_selection else None,
                note=command.reason or "人工新增遗漏要素",
            ))
        elif op == "split":
            if len(targets) != 1 or not command.new_content:
                raise InvalidInput("拆分操作必须选定一个目标要素并以换行提供拆分结果")
            parts = [p.strip() for p in command.new_content.split("\n") if p.strip()]
            if len(parts) < 2:
                raise InvalidInput("拆分结果至少两条（换行分隔）")
            cur = current[targets[0]]
            items.append({"action": "close", "origin_refs": [targets[0]], "note": "被拆分替代"})
            for p in parts:
                items.append(self._create_item(
                    context, element_type=cur.element_type, content=p,
                    quote=p, existing_anchor=cur.source_anchor,
                    origin_refs=[targets[0]], note=command.reason or "人工拆分",
                ))
        else:  # merge
            if len(targets) < 2:
                raise InvalidInput("合并操作必须选定至少两个目标要素")
            merged = command.new_content or "；".join(current[t].content for t in targets)
            first = current[targets[0]]
            for tid in targets:
                items.append({"action": "close", "origin_refs": [tid], "note": "被合并替代"})
            items.append(self._create_item(
                context, element_type=command.new_element_type.value if command.new_element_type else first.element_type,
                content=merged, existing_anchor=self._merge_anchors(context, [current[t] for t in targets]),
                origin_refs=list(targets), note=command.reason or "人工合并",
            ))

        gate, gate_next = self._judge_create_gate(context, items)
        version = self._process_records.read_workspace_version(context)
        draft_ref = self._save_draft(
            context, version, op, "manual", items, list(targets), [], ranges,
            self._impact_summary(items), gate, gate_next,
        )
        return self._read_draft_read(draft_ref, context)

    # AEP-029 —— 确认创建：版本关系层唯一改变 LDM-005 集合的工作台命令入口。
    # 旧要素替代留痕（superseded），新要素一律落「待确认」。
    def confirm_element_change_draft(
        self, command: ElementChangeConfirmCommand
    ) -> ElementWorkspaceRead:
        context = command.parse_context_ref
        draft = self._process_records.read_draft(command.draft_ref)
        if draft is None or draft.parse_context_ref != context or draft.status != "open":
            raise RejectedTransition("变更草案不存在或已结束；请重新发起校正/修订")
        self._support._require_version(context, command.workspace_version)
        if str(draft.workspace_version) != command.workspace_version:
            raise RejectedTransition("草案基于旧工作区版本，请刷新后重新形成草案")
        if draft.create_gate != "creatable":
            raise RejectedTransition(f"草案未通过创建裁定（{draft.create_gate}）：{draft.next_action or '不可创建'}")

        items = json.loads(draft.items)
        origin = draft.origin
        creates: list[ElementCreateRow] = []
        closes: list[tuple[str, str, str]] = []
        for item in items:
            if item.get("action") == "close":
                for tid in item.get("origin_refs", []):
                    closes.append((tid, origin, item.get("note", "")))
            elif item.get("action") == "create":
                el = item["element"]
                creates.append(ElementCreateRow(
                    element_type=el["element_type"],
                    content=el["content"],
                    source_anchor=el.get("source_anchor"),
                    confidence=el.get("confidence"),
                    process_status=ES.PENDING_CONFIRMATION.value,
                    correction_state=origin,
                    correction_note=item.get("note"),
                    origin_refs=tuple(item.get("origin_refs", [])),
                ))
        for tid, _o, note in closes:
            row = self._source_assets.get_element(tid)
            if row is not None:
                self._support._history(row, "supersede", row.process_status, row.process_status,
                              command.operator_ref, note or "被新版本替代（保留留痕）")
                self._support._record_adoption(
                    context, "element_recognition", "element", row.id, "superseded",
                    command.operator_ref, f"{command.idempotency_key}:adoption:supersede:{row.id}",
                )
        new_ids = self._source_assets.apply_element_changes(context, creates, closes)
        for nid in new_ids:
            row = self._source_assets.get_element(nid)
            if row is not None:
                self._support._history(row, "register", None, ES.PENDING_CONFIRMATION.value,
                              command.operator_ref, f"{draft.operation_type} 创建（待确认）")
                self._support._record_adoption(
                    context, "element_execution", "element", row.id, "adopted",
                    command.operator_ref, f"{command.idempotency_key}:adoption:create:{row.id}",
                )
        self._process_records.mark_draft_confirmed(draft.id)
        self._process_records.bump_workspace_version(context)
        log_event(_COMPONENT, "element.change.confirmed", context_ref=context,
                  operation_type=draft.operation_type,
                  create_count=len(creates), supersede_count=len(closes), ok=True)
        return self._workspace.read_element_workspace(context)

    def _build_items(
        self, context_ref: str, operation_type: str,
        targets: list[str], after: list[dict], note: str,
    ) -> list[dict]:
        """AI 执行结构化结果 → 草案项：替代目标 + 创建 after 集（add_missing 只创建）。"""
        if not after:
            return []
        current = self._support._current_elements(context_ref)
        items: list[dict] = []
        valid_targets = [t for t in targets if t in current]
        if operation_type != "add_missing":
            for tid in valid_targets:
                items.append({"action": "close", "origin_refs": [tid], "note": f"{note}：被替代"})
        fallback_anchor = current[valid_targets[0]].source_anchor if valid_targets else None
        # merge：来源锚点由系统并联全部目标要素既有锚点（多来源片段不丢），不采用模型引文
        merged_anchor: Optional[str] = None
        if operation_type == "merge" and len(valid_targets) > 1:
            merged_ranges: list[dict] = []
            material_ref: Optional[str] = None
            for tid in valid_targets:
                anchor_raw = current[tid].source_anchor
                if not anchor_raw:
                    continue
                try:
                    parsed = json.loads(anchor_raw)
                except ValueError:
                    continue
                material_ref = material_ref or parsed.get("material_ref")
                merged_ranges.extend(parsed.get("ranges", []))
            if material_ref and merged_ranges:
                merged_anchor = json.dumps(
                    {"material_ref": material_ref, "ranges": merged_ranges}, ensure_ascii=False
                )
        for el in after:
            items.append(self._create_item(
                context_ref,
                element_type=el.get("element_type", "goal"),
                content=el.get("content", ""),
                quote=None if merged_anchor else el.get("source_quote"),
                anchor_json=merged_anchor,
                existing_anchor=fallback_anchor,
                origin_refs=valid_targets if operation_type != "add_missing" else [],
                note=note,
            ))
        return items

    def _create_item(
        self, context_ref: str, element_type: str, content: str,
        quote: Optional[str] = None, anchor_json: Optional[str] = None,
        existing_anchor: Optional[str] = None, origin_refs: Optional[list[str]] = None,
        note: str = "",
    ) -> dict:
        """构造草案 create 项；锚点优先级：显式选区 > 引文定位 > 沿用原要素锚点。"""
        anchor = anchor_json
        if anchor is None and quote:
            canvas = self._support._build_canvas(context_ref)
            if canvas:
                anchor = build_anchor_json(canvas.material_ref, canvas.raw_text, quote)
        if anchor is None:
            anchor = existing_anchor
        return {
            "action": "create",
            "origin_refs": origin_refs or [],
            "note": note,
            "element": {
                "element_type": element_type,
                "content": content,
                "source_anchor": anchor,
                "confidence": None,
                "process_status": ES.PENDING_CONFIRMATION.value,
            },
        }

    def _merge_anchors(self, context_ref: str, elements: list) -> Optional[str]:
        """合并多个要素的锚点 ranges 为一个锚点 JSON。"""
        canvas = self._support._build_canvas(context_ref)
        if canvas is None:
            return None
        ranges: list[dict] = []
        for e in elements:
            if not e.source_anchor:
                continue
            try:
                ranges.extend(json.loads(e.source_anchor).get("ranges", []))
            except (ValueError, AttributeError):
                continue
        return anchor_from_ranges(canvas.material_ref, canvas.raw_text, ranges) if ranges else None

    def _judge_create_gate(self, context_ref: str, items: list[dict]) -> tuple[str, Optional[str]]:
        """N08 按类型裁定（最小实现）：新增内容必须能回到原文或「补」来源，否则回材料补充。"""
        if not items:
            return "stopped", "无可承接的变更内容"
        canvas = self._support._build_canvas(context_ref)
        raw_text = canvas.raw_text if canvas else ""
        supplement_texts = [s.content for s in (canvas.supplements if canvas else [])]
        for item in items:
            if item.get("action") != "create":
                continue
            el = item["element"]
            anchor = el.get("source_anchor")
            resolved = False
            if anchor:
                try:
                    parsed = json.loads(anchor)
                    resolved = any(
                        (0 <= r.get("start", -1) < r.get("end", 0) <= len(raw_text))
                        or (r.get("exact") and r["exact"] in raw_text)
                        for r in parsed.get("ranges", [])
                    )
                except (ValueError, AttributeError):
                    resolved = False
            if not resolved:  # 「补」来源也可作依据（补入留痕）
                resolved = any(el.get("content", "") in s or s in el.get("content", "")
                               for s in supplement_texts)
            if not resolved and not item.get("origin_refs"):
                return (
                    "needs_material_supplement",
                    "拟新增内容无法回到材料原文（疑似无来源事实），请先经「补入」补充来源",
                )
        return "creatable", None

    def _impact_summary(self, items: list[dict]) -> list[str]:
        closes = sum(1 for i in items if i.get("action") == "close")
        creates = sum(1 for i in items if i.get("action") == "create")
        out = []
        if closes:
            out.append(f"将替代 {closes} 个旧要素（保留留痕），其下游引用需按新集合复查")
        if creates:
            out.append(f"将创建 {creates} 个新要素（落「待确认」），确认后方可进入条目形成")
        return out

    def _save_draft(
        self, context_ref: str, version: int, operation_type: str, origin: str,
        items: list[dict], target_refs: list[str], suggestion_refs: list[str],
        source_ranges: list[dict], impact: list[str], gate: str, gate_next: Optional[str],
    ) -> str:
        return self._process_records.save_change_draft(
            self._support._project_of(context_ref), context_ref, version, operation_type, origin,
            json.dumps(items, ensure_ascii=False),
            json.dumps(target_refs, ensure_ascii=False),
            json.dumps(suggestion_refs, ensure_ascii=False),
            json.dumps(source_ranges, ensure_ascii=False),
            json.dumps(impact, ensure_ascii=False),
            gate, gate_next,
        )

    def _read_draft_read(self, draft_ref: str, context_ref: str) -> ElementChangeDraftRead:
        row = self._process_records.read_draft(draft_ref)
        assert row is not None
        return self._workspace._project_draft(row, context_ref)
