"""端口的 in-memory 适配（测试 / 无 DB 无模型时用）。持久化=SQLAlchemy 增量。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Sequence

from app.adapters.llm import (
    StubElementCommandInterpreter,
    StubElementOperationExecutor,
    StubElementReviewer,
    StubSourceElementRecognizer,
    StubSourceIntakeJudge,
)
from app.api.schemas import (
    ElementAiExecutionResultCommand,
    ElementReviewResultCommand,
    IntakeJudgementResultCommand,
    RecognitionResultCommand,
)
from app.domain.enums import (
    ElementProcessStatus,
    IntakeConclusion,
    MaterialParseStatus,
    ModelJudgement,
)
from app.interfaces.repositories import (
    DraftRow,
    FacetProjectionRow,
    ElementHistoryRow,
    ElementRow,
    InflightRevisionRow,
    OperationRow,
    ProjectRow,
    RecognitionRead,
    RequestContent,
    StagePayloadRow,
    SupplementRow,
)
from app.services.analysis_transformation import AnalysisTransformationService
from app.services.material_receiving import MaterialReceivingService
from app.services.model_orchestration import ModelInferenceOrchestration


class InMemoryProjectScope:
    def __init__(self, selected: Optional[set[str]] = None) -> None:
        self._selected = selected if selected is not None else set()

    def select(self, project_ref: str) -> None:
        self._selected.add(project_ref)

    def is_project_selected(self, project_ref: str) -> bool:
        return project_ref in self._selected


class InMemoryProjectRepository:
    def __init__(self) -> None:
        self._rows: dict[str, ProjectRow] = {}
        self._idempotency: dict[str, str] = {}  # 幂等键 → 项目 id
        self._seq = 0

    def create(self, name: str, scope: Optional[str], background: Optional[str],
               domain_profile_key: Optional[str] = None,
               operator_ref: str = "", idempotency_key: Optional[str] = None) -> str:
        self._seq += 1
        pid = f"PRJ-{self._seq}"
        self._rows[pid] = ProjectRow(
            pid, name, scope, background,
            created_at=datetime.now(timezone.utc).isoformat(),
            domain_profile_key=domain_profile_key,
        )
        if idempotency_key:
            self._idempotency[idempotency_key] = pid
        return pid

    def get(self, project_id: str) -> Optional[ProjectRow]:
        return self._rows.get(project_id)

    def list_all(self) -> list[ProjectRow]:
        return list(self._rows.values())

    def find_by_idempotency_key(self, key: str) -> Optional[ProjectRow]:
        pid = self._idempotency.get(key)
        return self._rows.get(pid) if pid else None


class InMemoryModelResultRepository:
    def __init__(self) -> None:
        self._judgements: dict[str, ModelJudgement] = {}
        self._basis: dict[str, str] = {}
        self._recognitions: dict[str, RecognitionRead] = {}
        self._seq = 0

    def record_intake_judgement(
        self, judgement: ModelJudgement, applies_to: Optional[str], basis: str
    ) -> str:
        self._seq += 1
        ref = f"MR-{self._seq}"
        self._judgements[ref] = judgement
        self._basis[ref] = basis
        return ref

    def seed_judgement(self, ref: str, judgement: ModelJudgement) -> None:  # 测试用
        self._judgements[ref] = judgement

    def read_intake_judgement(self, model_result_ref: str) -> Optional[ModelJudgement]:
        return self._judgements.get(model_result_ref)

    def read_basis(self, model_result_ref: str) -> Optional[str]:
        return self._basis.get(model_result_ref)

    def record_element_recognition(
        self, applies_to: Optional[str], result_code: str,
        elements, basis: str,
    ) -> str:
        self._seq += 1
        ref = f"MR-{self._seq}"
        self._recognitions[ref] = RecognitionRead(
            result_code=result_code, elements=tuple(elements), basis=basis
        )
        return ref

    def read_element_recognition(self, model_result_ref: str) -> Optional[RecognitionRead]:
        return self._recognitions.get(model_result_ref)

    def seed_recognition(self, ref: str, reco: RecognitionRead) -> None:  # 测试用
        self._recognitions[ref] = reco

    # --- P03/P04 复核/执行类 LDM-015 ---
    def record_stage_payload(
        self, stage: str, applies_to, result_code: str, payload_json, basis: str,
        recheck_idempotency_key=None,
    ) -> str:
        # recheck_idempotency_key：内存实现不建幂等索引（不承载 recheck 幂等查询），仅
        # 保持端口签名一致。
        self._seq += 1
        ref = f"MR-{self._seq}"
        self._stage_payloads = getattr(self, "_stage_payloads", {})
        self._stage_order = getattr(self, "_stage_order", [])
        row = StagePayloadRow(ref=ref, stage=stage, result_code=result_code,
                              payload=payload_json, basis=basis)
        self._stage_payloads[ref] = row
        self._stage_order.append((stage, applies_to, ref))
        return ref

    def read_stage_payload(self, model_result_ref: str):
        return getattr(self, "_stage_payloads", {}).get(model_result_ref)

    def update_stage_payload(self, model_result_ref: str, payload_json: str) -> None:
        row = getattr(self, "_stage_payloads", {}).get(model_result_ref)
        if row is not None:
            self._stage_payloads[model_result_ref] = StagePayloadRow(
                ref=row.ref, stage=row.stage, result_code=row.result_code,
                payload=payload_json, basis=row.basis,
            )

    def latest_stage_payload(self, stage: str, applies_to: str):
        for s, a, ref in reversed(getattr(self, "_stage_order", [])):
            if s == stage and a == applies_to:
                return self._stage_payloads[ref]
        return None

    # --- LDM-015 采纳结论明细（幂等：同键跳过）---
    def record_adoption(self, *, model_result_ref: str, project_ref: str, stage: str,
                        subject_type: str, subject_ref: str, outcome: str,
                        operator_ref: str, idempotency_key: str, basis_ref=None) -> None:
        self.adoptions = getattr(self, "adoptions", {})
        if idempotency_key in self.adoptions:
            return
        self.adoptions[idempotency_key] = {
            "model_result_ref": model_result_ref, "project_ref": project_ref, "stage": stage,
            "subject_type": subject_type, "subject_ref": subject_ref, "outcome": outcome,
            "operator_ref": operator_ref, "basis_ref": basis_ref,
        }


class InMemoryProcessRecordRepository:
    def __init__(self) -> None:
        self._by_key: dict[str, str] = {}
        self._contexts: set[str] = set()
        self._stops: dict[str, str] = {}
        self._content: dict[str, RequestContent] = {}
        # 识别请求上下文（ParseRequest）
        self._parse_by_key: dict[str, str] = {}
        self._parse_contexts: set[str] = set()
        self._parse_material: dict[str, str] = {}
        self._parse_stops: dict[str, str] = {}
        self._seq = 0

    def find_context_by_idempotency(self, key: str) -> Optional[str]:
        return self._by_key.get(key)

    def create_intake_request(
        self, project_ref: str, key: str, raw_text: str, source_note: str, operator_ref: str
    ) -> str:
        self._seq += 1
        context = f"CTX-{self._seq}"
        self._by_key[key] = context
        self._contexts.add(context)
        self._content[context] = RequestContent(project_ref, raw_text, source_note or "")
        self._context_projects = getattr(self, "_context_projects", {})
        self._context_projects[context] = project_ref
        return context

    def context_exists(self, context_ref: str) -> bool:
        return context_ref in self._contexts

    def read_request_content(self, context_ref: str) -> Optional[RequestContent]:
        return self._content.get(context_ref)

    def mark_stopped(self, context_ref: str, reason: str, next_action: str) -> None:
        self._stops[context_ref] = next_action

    def read_stop_next_action(self, context_ref: str) -> Optional[str]:
        return self._stops.get(context_ref)

    def seed_context(self, context_ref: str) -> None:  # 测试用
        self._contexts.add(context_ref)

    # --- SCN-001-P02 识别请求上下文（ParseRequest）---
    def find_parse_context_by_idempotency(self, key: str) -> Optional[str]:
        return self._parse_by_key.get(key)

    def latest_parse_context_of_material(self, material_ref: str) -> Optional[str]:
        # _parse_material 按创建顺序插入，取最后一个命中的即最近一次
        latest: Optional[str] = None
        for context, mid in self._parse_material.items():
            if mid == material_ref:
                latest = context
        return latest

    def create_parse_request(
        self, project_ref: str, key: str, material_ref: str, operator_ref: str
    ) -> str:
        self._seq += 1
        context = f"PCTX-{self._seq}"
        self._parse_by_key[key] = context
        self._parse_contexts.add(context)
        self._parse_material[context] = material_ref
        self._context_projects = getattr(self, "_context_projects", {})
        self._context_projects[context] = project_ref
        return context

    def parse_context_exists(self, context_ref: str) -> bool:
        return context_ref in self._parse_contexts

    def read_parse_material_ref(self, context_ref: str) -> Optional[str]:
        return self._parse_material.get(context_ref)

    def mark_parse_stopped(self, context_ref: str, reason: str, next_action: str) -> None:
        self._parse_stops[context_ref] = next_action

    def read_parse_stop_next_action(self, context_ref: str) -> Optional[str]:
        return self._parse_stops.get(context_ref)

    def seed_parse_context(self, context_ref: str, material_ref: str = "M-seed") -> None:  # 测试用
        self._parse_contexts.add(context_ref)
        self._parse_material[context_ref] = material_ref

    # --- 工作区版本 ---
    def read_workspace_version(self, context_ref: str) -> int:
        self._versions = getattr(self, "_versions", {})
        return self._versions.get(context_ref, 1)

    def bump_workspace_version(self, context_ref: str) -> int:
        self._versions = getattr(self, "_versions", {})
        self._versions[context_ref] = self._versions.get(context_ref, 1) + 1
        return self._versions[context_ref]

    def project_of_context(self, context_ref: str) -> Optional[str]:
        self._context_projects = getattr(self, "_context_projects", {})
        return self._context_projects.get(context_ref)

    # --- P03/P04 操作请求上下文 ---
    def find_operation_by_idempotency(self, key: str) -> Optional[str]:
        return getattr(self, "_op_by_key", {}).get(key)

    def create_element_operation(
        self, project_ref: str, context_ref: str, kind: str,
        payload_json: str, operator_ref: str, key: str,
    ) -> str:
        self._seq += 1
        op_ref = f"OP-{self._seq}"
        self._ops = getattr(self, "_ops", {})
        self._op_by_key = getattr(self, "_op_by_key", {})
        self._ops[op_ref] = OperationRow(
            id=op_ref, kind=kind, parse_context_ref=context_ref,
            payload=payload_json, operator_ref=operator_ref,
        )
        self._op_by_key[key] = op_ref
        return op_ref

    def read_element_operation(self, operation_ref: str) -> Optional[OperationRow]:
        return getattr(self, "_ops", {}).get(operation_ref)

    def find_inflight_revisions(
        self, context_ref: str, element_refs: Sequence[str]
    ) -> list[InflightRevisionRow]:
        """内存装配里永远没有在途修订，恒空。

        「在途」的定义是那条 AgentRun 还没跑完，而内存装配配的是同步编排——派发即执行、
        执行完才返回，没有 AgentRun 行，也不存在任何一刻能观察到未终态的运行。
        在途守卫的真行为一律由 SQL 装配的用例覆盖（tests/test_revision_inflight_guard.py）。
        """
        return []

    # --- P04 变更草案 ---
    def save_change_draft(
        self, project_ref: str, context_ref: str, workspace_version: int,
        operation_type: str, origin: str, items_json: str,
        target_refs_json: str, suggestion_refs_json: str, source_ranges_json: str,
        impact_summary_json: str, create_gate: str, next_action: Optional[str],
    ) -> str:
        self._seq += 1
        draft_ref = f"DR-{self._seq}"
        self._drafts = getattr(self, "_drafts", {})
        for d in list(self._drafts.values()):
            if d.parse_context_ref == context_ref and d.status == "open":
                self._drafts[d.id] = DraftRow(**{**d.__dict__, "status": "cancelled"})
        self._drafts[draft_ref] = DraftRow(
            id=draft_ref, parse_context_ref=context_ref,
            workspace_version=workspace_version, operation_type=operation_type,
            origin=origin, items=items_json, target_refs=target_refs_json,
            suggestion_refs=suggestion_refs_json, source_ranges=source_ranges_json,
            impact_summary=impact_summary_json, create_gate=create_gate,
            next_action=next_action, status="open",
        )
        return draft_ref

    def read_open_draft(self, context_ref: str) -> Optional[DraftRow]:
        for d in reversed(list(getattr(self, "_drafts", {}).values())):
            if d.parse_context_ref == context_ref and d.status == "open":
                return d
        return None

    def read_draft(self, draft_ref: str) -> Optional[DraftRow]:
        return getattr(self, "_drafts", {}).get(draft_ref)

    def mark_draft_confirmed(self, draft_ref: str) -> None:
        self._drafts = getattr(self, "_drafts", {})
        d = self._drafts.get(draft_ref)
        if d is not None:
            self._drafts[draft_ref] = DraftRow(**{**d.__dict__, "status": "confirmed"})

    # --- TC-08 完备度投影（仅 AEP-024 写；整批替换，可整层重算）---

    def replace_facet_projection(self, element_ref: str, rows: list[FacetProjectionRow]) -> None:
        self._facet_projections = getattr(self, "_facet_projections", {})
        self._facet_projections[element_ref] = list(rows)

    def facet_projections_of(self, element_refs: list[str]) -> dict[str, list[FacetProjectionRow]]:
        self._facet_projections = getattr(self, "_facet_projections", {})
        return {
            ref: list(self._facet_projections[ref])
            for ref in element_refs
            if self._facet_projections.get(ref)
        }

    # --- 条目陈述达标投影（仅条目形成/修订链路写；整批替换，可整层重算）---

    def replace_item_structure_projection(self, item_ref: str, rows: list) -> None:
        self._item_structure_projections = getattr(self, "_item_structure_projections", {})
        self._item_structure_projections[item_ref] = list(rows)

    def item_structure_projections_of(self, item_refs: list[str]) -> dict[str, list]:
        self._item_structure_projections = getattr(self, "_item_structure_projections", {})
        return {
            ref: list(self._item_structure_projections[ref])
            for ref in item_refs
            if self._item_structure_projections.get(ref)
        }


class InMemorySourceAssetRepository:
    """LDM-002 / LDM-003 唯一权威写入口（VAL-003）。"""

    def __init__(self) -> None:
        self._conclusions: dict[str, IntakeConclusion] = {}
        self._materials: dict[str, str] = {}
        self._model_refs: dict[str, str] = {}
        self.save_material_calls = 0
        # SCN-001-P02：已接入材料 + LDM-004/005
        self._accepted_materials: set[str] = set()
        self._material_content: dict[str, RequestContent] = {}
        self._parse: dict[str, dict] = {}          # ctx -> {ref,status,basis}
        self._elements: dict[str, list[ElementRow]] = {}  # parse_result_ref -> rows
        self.save_parse_calls = 0
        self._seq = 0

    def save_material_and_intake_record(self, context_ref: str, model_result_ref: str) -> str:
        self.save_material_calls += 1
        self._seq += 1
        material = f"LDM-002-{self._seq}"
        self._materials[context_ref] = material
        self._conclusions[context_ref] = IntakeConclusion.ACCEPTED
        self._model_refs[context_ref] = model_result_ref
        return material

    def save_intake_conclusion(
        self, context_ref: str, conclusion: IntakeConclusion, model_result_ref: str
    ) -> None:
        self._conclusions[context_ref] = conclusion
        self._model_refs[context_ref] = model_result_ref

    def conclusion_of(self, context_ref: str) -> Optional[IntakeConclusion]:
        return self._conclusions.get(context_ref)

    def material_of(self, context_ref: str) -> Optional[str]:
        return self._materials.get(context_ref)

    def model_result_ref_of(self, context_ref: str) -> Optional[str]:
        return self._model_refs.get(context_ref)

    def seed_conclusion(self, context_ref: str, conclusion: IntakeConclusion) -> None:  # 测试用
        self._conclusions[context_ref] = conclusion

    # --- SCN-001-P02 已接入校验 + LDM-004/005 ---
    def seed_material(  # 测试用
        self, material_ref: str, project_ref: str = "P-1",
        raw_text: str = "示例材料原文", source_note: str = "", accepted: bool = True,
    ) -> None:
        self._material_content[material_ref] = RequestContent(project_ref, raw_text, source_note)
        if accepted:
            self._accepted_materials.add(material_ref)

    def is_material_accepted(self, material_ref: str) -> bool:
        return material_ref in self._accepted_materials

    def read_material_content(self, material_ref: str) -> Optional[RequestContent]:
        return self._material_content.get(material_ref)

    def save_parse_result_and_elements(
        self, context_ref: str, material_ref: str, model_result_ref: str, elements,
    ) -> str:
        self.save_parse_calls += 1
        self._seq += 1
        pref = f"LDM-004-{self._seq}"
        self._parse[context_ref] = {"ref": pref, "status": MaterialParseStatus.PARSED.value, "basis": None}
        # 全集登记：初始 process_status 一律「待确认」；模型裁定入证据字段
        self._elements[pref] = [
            ElementRow(
                id=f"{pref}-E{i}",
                element_type=e.element_type,
                content=e.content,
                source_anchor=e.source_anchor,
                confidence=e.confidence,
                process_status=ElementProcessStatus.PENDING_CONFIRMATION.value,
                model_verdict=e.model_verdict,
                verdict_reason=e.verdict_reason,
            )
            for i, e in enumerate(elements)
        ]
        return pref

    def save_parse_conclusion(
        self, context_ref: str, material_ref: str, model_result_ref: str, note: str
    ) -> str:
        self._seq += 1
        pref = f"LDM-004-{self._seq}"
        self._parse[context_ref] = {"ref": pref, "status": MaterialParseStatus.UNPROCESSABLE.value, "basis": note}
        self._elements[pref] = []
        return pref

    def parse_status_of(self, context_ref: str) -> Optional[str]:
        p = self._parse.get(context_ref)
        return p["status"] if p else None

    def parse_result_of(self, context_ref: str) -> Optional[str]:
        p = self._parse.get(context_ref)
        return p["ref"] if p else None

    def parse_basis_of(self, context_ref: str) -> Optional[str]:
        p = self._parse.get(context_ref)
        return p["basis"] if p else None

    def elements_of(self, parse_result_ref: str) -> list[ElementRow]:
        return list(self._elements.get(parse_result_ref, []))

    def list_project_elements_by_type(self, project_ref, element_types) -> list[ElementRow]:
        # 测试替身：不按项目区分（in_memory 通常单项目），按类型 + 未替代过滤。
        types = set(element_types or ())
        return [
            r for rows in self._elements.values() for r in rows
            if r.element_type in types and not r.superseded
        ]

    def _find_element(self, element_id: str) -> Optional[tuple[str, int, ElementRow]]:
        for pref, rows in self._elements.items():
            for i, r in enumerate(rows):
                if r.id == element_id:
                    return pref, i, r
        return None

    def _replace_element(self, element_id: str, **changes) -> Optional[ElementRow]:
        found = self._find_element(element_id)
        if found is None:
            return None
        pref, i, r = found
        new_row = ElementRow(**{**r.__dict__, **changes})
        self._elements[pref][i] = new_row
        return new_row

    def _create_element(self, pref: str, c) -> str:
        self._seq += 1
        new_id = f"{pref}-E{self._seq}"
        self._elements[pref].append(ElementRow(
            id=new_id, element_type=c.element_type, content=c.content,
            source_anchor=c.source_anchor, confidence=c.confidence,
            process_status=c.process_status,
            model_verdict=c.model_verdict,
            correction_note=c.correction_note,
            origin_refs=json.dumps(list(c.origin_refs), ensure_ascii=False) if c.origin_refs else None,
        ))
        return new_id

    def apply_element_changes(self, context_ref: str, creates, closes) -> list[str]:
        p = self._parse.get(context_ref)
        if p is None:
            return []
        pref = p["ref"]
        for element_id, correction_state, note in closes:
            self._replace_element(element_id, superseded=True, correction_note=note or None)
        return [self._create_element(pref, c) for c in creates]

    def add_elements(self, context_ref: str, creates) -> list[str]:
        p = self._parse.get(context_ref)
        if p is None:
            return []
        return [self._create_element(p["ref"], c) for c in creates]

    # --- SCN-001-P03 确认生命周期写方法 ---
    def get_element(self, element_id: str) -> Optional[ElementRow]:
        found = self._find_element(element_id)
        return found[2] if found else None

    def set_element_status(
        self, element_id: str, status: str, bump_version: bool = False,
        clear_review: bool = False,
    ) -> None:
        found = self._find_element(element_id)
        if found is None:
            return
        _, _, r = found
        changes: dict = {"process_status": status}
        if bump_version:
            changes["version"] = r.version + 1
        if clear_review:
            changes.update(review_conclusion=None, review_basis=None, revision_draft=None)
        self._replace_element(element_id, **changes)

    def set_element_noise_triage(self, element_id: str, triage) -> None:
        # 只动人工标记：model_verdict / verdict_reason 是模型证据，此处一个字节都不碰
        self._replace_element(element_id, noise_triage=triage)

    def set_element_review(
        self, element_id: str, conclusion, basis, revision_draft,
    ) -> None:
        changes: dict = {"review_conclusion": conclusion, "review_basis": basis}
        if revision_draft is not None:
            changes["revision_draft"] = revision_draft
        self._replace_element(element_id, **changes)

    def set_revision_draft(self, element_id: str, draft) -> None:
        self._replace_element(element_id, revision_draft=draft)

    def apply_element_edit(
        self, element_id: str, element_type, content, source_anchor, note,
    ) -> None:
        found = self._find_element(element_id)
        if found is None:
            return
        _, _, r = found
        changes: dict = {"version": r.version + 1}
        if element_type is not None:
            changes["element_type"] = element_type
        if content is not None:
            changes["content"] = content
        if source_anchor is not None:
            changes["source_anchor"] = source_anchor
        if note is not None:
            changes["correction_note"] = note
        self._replace_element(element_id, **changes)

    # --- 变更历史 ---
    def record_element_history(
        self, element_ref: str, project_ref: str, version: int, action: str,
        from_status, to_status, operator_ref: str, note, snapshot_json,
    ) -> None:
        self._history = getattr(self, "_history", {})
        self._seq += 1
        self._history.setdefault(element_ref, []).append(ElementHistoryRow(
            id=f"H-{self._seq}", element_ref=element_ref, version=version,
            action=action, from_status=from_status, to_status=to_status,
            operator_ref=operator_ref or "", note=note, snapshot=snapshot_json,
            at=f"T{self._seq}",
        ))

    def element_history_of(self, element_ref: str) -> list[ElementHistoryRow]:
        return list(reversed(getattr(self, "_history", {}).get(element_ref, [])))

    def merge_history_for_material(self, material_ref: str) -> list[ElementHistoryRow]:
        if not material_ref:
            return []
        out: list[ElementHistoryRow] = []
        for rows in getattr(self, "_history", {}).values():
            for h in rows:
                if h.action != "merge" or not h.snapshot:
                    continue
                try:
                    snap = json.loads(h.snapshot)
                except ValueError:
                    continue
                if isinstance(snap, dict) and snap.get("merged_from_material") == material_ref:
                    out.append(h)
        return out

    # --- 改源联动（勘误/补入）---
    def apply_material_erratum(
        self, material_ref: str, new_raw_text: str, note: str, operator_ref: str
    ) -> int:
        self._material_versions = getattr(self, "_material_versions", {})
        old = self._material_content.get(material_ref)
        if old is None:
            return 1
        versions = self._material_versions.setdefault(material_ref, [])
        versions.append(old.raw_text)
        self._material_content[material_ref] = RequestContent(
            old.project_ref, new_raw_text, old.source_note
        )
        return len(versions) + 1

    def add_material_supplement(
        self, material_ref: str, content: str, basis: str, operator_ref: str
    ) -> str:
        self._supplements = getattr(self, "_supplements", {})
        self._seq += 1
        ref = f"SUP-{self._seq}"
        self._supplements.setdefault(material_ref, []).append(SupplementRow(
            id=ref, content=content, basis=basis, operator_ref=operator_ref, at=f"T{self._seq}",
        ))
        return ref

    def supplements_of(self, material_ref: str) -> list[SupplementRow]:
        return list(getattr(self, "_supplements", {}).get(material_ref, []))

    def material_source_version(self, material_ref: str) -> int:
        versions = getattr(self, "_material_versions", {}).get(material_ref, [])
        return len(versions) + 1


class InMemoryTraceGraph:
    def __init__(self) -> None:
        self.pre_established: list[str] = []

    def pre_establish_source_trace(self, material_ref: str) -> None:
        self.pre_established.append(material_ref)


class InMemoryAudit:
    def __init__(self) -> None:
        self.accepted: list[str] = []

    def record_intake_accepted(self, material_ref: str, operator_ref: str) -> None:
        self.accepted.append(material_ref)


@dataclass
class Wiring:
    service: MaterialReceivingService
    project_scope: InMemoryProjectScope
    model_orchestration: ModelInferenceOrchestration
    model_results: InMemoryModelResultRepository
    process_records: InMemoryProcessRecordRepository
    source_assets: InMemorySourceAssetRepository
    trace_graph: InMemoryTraceGraph
    audit: InMemoryAudit


def build_wiring(
    auto_complete: bool = False,
    selected_projects: Optional[set[str]] = None,
    canned: ModelJudgement = ModelJudgement.ACCEPTABLE,
) -> Wiring:
    project_scope = InMemoryProjectScope(selected_projects)
    model_results = InMemoryModelResultRepository()
    process_records = InMemoryProcessRecordRepository()
    source_assets = InMemorySourceAssetRepository()
    trace_graph = InMemoryTraceGraph()
    audit = InMemoryAudit()
    # 测试判定用 stub（canned 指定分支）；真实模型见 deps。
    orchestration = ModelInferenceOrchestration(
        judge=StubSourceIntakeJudge(canned), process_records=process_records, model_results=model_results
    )
    service = MaterialReceivingService(
        project_scope=project_scope,
        model_orchestration=orchestration,
        model_results=model_results,
        process_records=process_records,
        source_assets=source_assets,
        trace_graph=trace_graph,
        audit=audit,
    )
    if auto_complete:
        def _hook(context_ref: str, model_result_ref: str) -> None:
            service.accept_intake_judgement_result(
                IntakeJudgementResultCommand(
                    model_result_ref=model_result_ref,
                    intake_context_ref=context_ref,
                    operator_ref="system",
                    idempotency_key=f"auto-{context_ref}",
                    service_accepts=True,
                )
            )

        orchestration.on_judgement = _hook

    return Wiring(
        service=service,
        project_scope=project_scope,
        model_orchestration=orchestration,
        model_results=model_results,
        process_records=process_records,
        source_assets=source_assets,
        trace_graph=trace_graph,
        audit=audit,
    )


# ---- SCN-001-P02 分析转化服务 in-memory 装配 ----


@dataclass
class AnalysisWiring:
    service: AnalysisTransformationService
    model_orchestration: ModelInferenceOrchestration
    model_results: InMemoryModelResultRepository
    process_records: InMemoryProcessRecordRepository
    source_assets: InMemorySourceAssetRepository


def build_analysis_wiring(
    auto_complete: bool = False,
    recognizer=None,
    reviewer=None,
    executor=None,
) -> AnalysisWiring:
    model_results = InMemoryModelResultRepository()
    process_records = InMemoryProcessRecordRepository()
    source_assets = InMemorySourceAssetRepository()
    orchestration = ModelInferenceOrchestration(
        process_records=process_records,
        model_results=model_results,
        recognizer=recognizer or StubSourceElementRecognizer(),
        source_assets=source_assets,
        reviewer=reviewer or StubElementReviewer(),
        executor=executor or StubElementOperationExecutor(),
    )
    service = AnalysisTransformationService(
        model_orchestration=orchestration,
        model_results=model_results,
        process_records=process_records,
        source_assets=source_assets,
        command_interpreter=StubElementCommandInterpreter(),
    )
    if auto_complete:
        def _hook(context_ref: str, model_result_ref: str) -> None:
            service.accept_recognition_result(
                RecognitionResultCommand(
                    model_result_ref=model_result_ref,
                    parse_context_ref=context_ref,
                    operator_ref="system",
                    idempotency_key=f"auto-{context_ref}",
                )
            )

        def _review_hook(operation_ref: str, model_result_ref: str) -> None:
            service.accept_element_review_result(
                ElementReviewResultCommand(
                    model_result_ref=model_result_ref,
                    operation_context_ref=operation_ref,
                    operator_ref="system",
                    idempotency_key=f"auto-{operation_ref}",
                )
            )

        def _execution_hook(operation_ref: str, model_result_ref: str) -> None:
            service.accept_element_ai_execution_result(
                ElementAiExecutionResultCommand(
                    model_result_ref=model_result_ref,
                    operation_context_ref=operation_ref,
                    operator_ref="system",
                    idempotency_key=f"auto-{operation_ref}",
                )
            )

        orchestration.on_recognition = _hook
        orchestration.on_review = _review_hook
        orchestration.on_execution = _execution_hook

    return AnalysisWiring(
        service=service,
        model_orchestration=orchestration,
        model_results=model_results,
        process_records=process_records,
        source_assets=source_assets,
    )
