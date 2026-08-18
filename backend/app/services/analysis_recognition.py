"""识别请求与识别结果承接（L2）：AEP-021 送检、AEP-022 全集登记、登记期同名要素归并。"""
from __future__ import annotations

from typing import Optional

from app.api.schemas import (
    ElementRecognitionCommand,
    RecognitionDecisionResult,
    RecognitionRequestResult,
    RecognitionResultCommand,
)
from app.domain.anchors import build_anchor_json
from app.domain.enums import (
    ElementProcessStatus as ES,
    ElementType,
    RecognitionOutcome,
    RecognitionRequestStatus,
)
from app.domain.errors import RejectedTransition
from app.domain.naming import normalize_element_name
from app.interfaces import ElementRow, RecognizedElementRow
from app.log import log_event

from app.services.analysis_support import AnalysisSupport
from app.services.analysis_support import _COMPONENT


_MERGEABLE_TYPES = (
    ElementType.TERM.value, ElementType.ROLE.value, ElementType.EXTERNAL_SYSTEM.value,
)


class AnalysisRecognition:
    def __init__(
        self,
        model_orchestration,
        model_results,
        process_records,
        source_assets,
        support: AnalysisSupport,
    ) -> None:
        self._model_orchestration = model_orchestration
        self._model_results = model_results
        self._process_records = process_records
        self._source_assets = source_assets
        self._support = support

    # AEP-021 —— gate『material 已接入』；不写 LDM-004/005，送检后返回。
    def submit_element_recognition(
        self, command: ElementRecognitionCommand
    ) -> RecognitionRequestResult:
        replay = self._process_records.find_parse_context_by_idempotency(command.idempotency_key)
        if replay is not None:
            return RecognitionRequestResult(
                status=RecognitionRequestStatus.SUBMITTED_FOR_RECOGNITION, parse_context_ref=replay
            )

        if not self._source_assets.is_material_accepted(command.material_ref):
            return RecognitionRequestResult(
                status=RecognitionRequestStatus.REJECTED_PRECHECK,
                next_action="材料未接入或状态不允许识别，请先完成来源接入后再识别",
            )

        context = self._process_records.create_parse_request(
            command.project_ref, command.idempotency_key, command.material_ref, command.operator_ref
        )
        agent_run = self._model_orchestration.request_element_recognition(context)
        log_event(_COMPONENT, "element.recognition.submitted", context_ref=context)
        return RecognitionRequestResult(
            status=RecognitionRequestStatus.SUBMITTED_FOR_RECOGNITION,
            parse_context_ref=context,
            agent_run_ref=agent_run,
        )

    # AEP-022 —— 仅在『识别请求上下文』受理；三分支；仅可登记写 LDM-004+LDM-005。
    # 全集登记：全部要素初始 process_status 一律「待确认」；模型裁定入证据字段。
    def accept_recognition_result(
        self, command: RecognitionResultCommand
    ) -> RecognitionDecisionResult:
        context = command.parse_context_ref

        if not self._process_records.parse_context_exists(context):
            raise RejectedTransition("识别请求上下文不存在；AEP-022 仅在『识别请求上下文』受理")
        if self._source_assets.parse_status_of(context) is not None:
            raise RejectedTransition("该上下文已有解析结论；需经 AEP-021 重新发起识别")

        reco = self._model_results.read_element_recognition(command.model_result_ref)

        # 识别失败：过程停靠、保留人工继续、不伪造要素（VAL-005）；状态不迁移。
        if reco is None or reco.result_code == "failed":
            self._process_records.mark_parse_stopped(
                context, "模型识别失败或结果不可承接", "识别失败：可重试识别，不伪造要素"
            )
            log_event(_COMPONENT, "element.recognition.failed", level="WARN",
                      context_ref=context, ok=False)
            return RecognitionDecisionResult(
                outcome=RecognitionOutcome.RECOGNITION_FAILED,
                next_action="识别失败：可重试识别（不污染事实）",
            )

        material_ref = self._process_records.read_parse_material_ref(context)

        # 识别成功 ∧ 存在要素 → 唯一写 LDM-004(已解析)+全部 LDM-005 的分支（全集登记）。
        if reco.result_code == "recognized" and reco.elements:
            anchored = self._anchor_elements(material_ref, reco.elements)
            # P3 登记归并（03 §2.1）：term/role/external_system 按名称规范化归并到既有同名要素，
            # 不新建重复要素；确认态命中走草案通道（不静默改确认态事实）。
            project_ref = self._support._project_of(context)
            new_elements, merges = self._partition_merge_targets(project_ref, anchored)
            parse_result_ref = self._source_assets.save_parse_result_and_elements(
                context, material_ref, command.model_result_ref, new_elements
            )
            for row in self._source_assets.elements_of(parse_result_ref):
                self._support._history(row, "register", None, ES.PENDING_CONFIRMATION.value,
                              command.operator_ref, "识别登记（初始待确认）")
            merged = self._apply_registration_merges(merges, material_ref, command.operator_ref)
            self._process_records.bump_workspace_version(context)
            log_event(_COMPONENT, "element.recognition.registered",
                      context_ref=context, element_count=len(new_elements),
                      merged_count=merged, ok=True)
            return RecognitionDecisionResult(
                outcome=RecognitionOutcome.REGISTERED,
                parse_result_ref=parse_result_ref,
                element_count=len(new_elements),
            )

        # 识别成功 ∧ 无可处理要素 → 只写 LDM-004(不可继续处理)，不写 LDM-005。
        parse_result_ref = self._source_assets.save_parse_conclusion(
            context, material_ref, command.model_result_ref, reco.basis or "无可处理知识项"
        )
        self._process_records.bump_workspace_version(context)
        return RecognitionDecisionResult(
            outcome=RecognitionOutcome.NO_PROCESSABLE_ELEMENTS,
            parse_result_ref=parse_result_ref,
            element_count=0,
            next_action="未识别出可处理知识项，不进入条目形成主路径",
        )

    def _anchor_elements(
        self, material_ref: Optional[str], elements: tuple[RecognizedElementRow, ...]
    ) -> list[RecognizedElementRow]:
        """识别承接时把 exact 引文换算成结构化锚点 JSON（offset + 引文选择器）。"""
        content = self._source_assets.read_material_content(material_ref) if material_ref else None
        raw_text = content.raw_text if content else ""
        out: list[RecognizedElementRow] = []
        for e in elements:
            anchor = build_anchor_json(material_ref or "", raw_text, e.source_anchor)
            out.append(RecognizedElementRow(
                element_type=e.element_type,
                content=e.content,
                source_anchor=anchor,
                confidence=e.confidence,
                model_verdict=e.model_verdict,
                verdict_reason=e.verdict_reason,
            ))
        return out

    def _partition_merge_targets(self, project_ref, anchored):
        """按名称规范化把可归并类型识别结果分为 (新建, [(既有目标, 识别项)])（03 §2.1）。

        同名索引优先命中确认态（走草案），否则未确认态（自动归并）；business_rule 不归并。
        """
        if not project_ref:
            return list(anchored), []
        existing = self._source_assets.list_project_elements_by_type(project_ref, _MERGEABLE_TYPES)
        index: dict[tuple[str, str], ElementRow] = {}
        for row in existing:
            name = normalize_element_name(row.content)
            if not name:
                continue
            key = (row.element_type, name)
            prev = index.get(key)
            if prev is None or (
                row.process_status == ES.CONFIRMED.value and prev.process_status != ES.CONFIRMED.value
            ):
                index[key] = row
        new_elements: list[RecognizedElementRow] = []
        merges: list[tuple[ElementRow, RecognizedElementRow]] = []
        for e in anchored:
            if e.element_type in _MERGEABLE_TYPES:
                name = normalize_element_name(e.content)
                target = index.get((e.element_type, name)) if name else None
                if target is not None:
                    merges.append((target, e))
                    continue
            new_elements.append(e)
        return new_elements, merges

    def _apply_registration_merges(self, merges, material_ref, operator_ref) -> int:
        """登记来源锚点追加（03 §2.1，选型 B：锚点数走 merge 留痕，主锚点保留单材料）。

        未确认态：自动归并（版本 +1 + 留痕 merge）；确认态：不改内容，登记锚点追加草案
        （校正通道，待人工裁定）。追加锚点不合成、只引用本次识别结果的锚点。
        """
        count = 0
        for target, reco in merges:
            note = f"归并：同名要素（{target.element_type}）来自材料 {material_ref}；来源锚点追加"
            snapshot = {"merged_from_material": material_ref, "merged_anchor": reco.source_anchor}
            if target.process_status == ES.CONFIRMED.value:
                self._source_assets.set_revision_draft(
                    target.id, f"[锚点追加草案] {note}（待人工裁定）"
                )
                self._support._history(target, "merge", None, None, operator_ref,
                              note + "（确认态：登记为待裁定草案，不改事实）", snapshot)
            else:
                self._source_assets.set_element_status(
                    target.id, target.process_status, bump_version=True
                )
                self._support._history(target, "merge", None, None, operator_ref, note, snapshot)
            count += 1
        return count
