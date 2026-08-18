"""端口的 SQLAlchemy 适配（持久化到 Postgres/SQLite）。

写权威边界（VAL-003）：LDM-002/003 只经 SqlSourceAssetRepository；LDM-015 只经
SqlModelResultRepository；接入请求上下文经 SqlProcessRecordRepository。
追溯（LDM-013）与审计落库为后续增量，此处复用 in-memory 记录器。
"""
from __future__ import annotations

import hashlib

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.llm import (
    ChartSourceSuggester,
    ChartVerifier,
    ElementOperationExecutor,
    ElementReviewer,
    ItemStructureRechecker,
    RequirementItemDiagnoser,
    RequirementItemFormatter,
    SourceElementRecognizer,
    SourceIntakeJudge,
    StubChartSourceSuggester,
    StubChartVerifier,
    StubElementCommandInterpreter,
    StubElementOperationExecutor,
    StubElementReviewer,
    StubFormationCommandInterpreter,
    StubItemCommandInterpreter,
    StubItemDraftComposer,
    StubItemExplainer,
    StubItemStructureRechecker,
    StubRequirementItemDiagnoser,
    StubRequirementItemFormatter,
    StubSourceElementRecognizer,
    StubSourceIntakeJudge,
)
from app.api.schemas import (
    ElementAiExecutionResultCommand,
    ElementReviewResultCommand,
    IntakeJudgementResultCommand,
    RecognitionResultCommand,
)
from app.domain.naming import material_default_name
from app.db.models import (
    AdoptionRecord,
    AgentRun,
    ElementFacetProjection,
    ItemStructureProjection,
    ChartSourceRevision,
    ChartSuggestionRequest,
    ChartVerificationFinding,
    ChartVerificationRequest,
    ChartVerificationRound,
    ElementChangeDraft,
    ElementHistory,
    ElementOperation,
    IntakeRecord,
    IntakeRequest,
    Issue,
    ItemDiagnosisRequest,
    ItemDiagnosisRound,
    ItemFindingVeto,
    ItemFormationRequest,
    ItemizationOutcome,
    ItemReviewFinding,
    ItemRevisionSuggestion,
    Material,
    MaterialParseResult,
    MaterialRevision,
    MaterialSupplement,
    ModelResult,
    ParseRequest,
    Project,
    RequirementChart,
    RequirementElement,
    RequirementItem,
    RequirementItemRevision,
    TraceLink,
)
from app.domain.enums import (
    AgentRunStatus,
    ChartStatus,
    DiagnosisTrigger,
    ElementProcessStatus,
    IntakeConclusion,
    MaterialParseStatus,
    ModelJudgement,
    RequirementItemStatus,
    VerdictDecision,
    VerdictKind,
)
from app.interfaces.repositories import (
    FacetProjectionRow,
    ItemStructureProjectionRow,
    ChartFindingRow,
    ChartRevisionRow,
    ChartRow,
    ChartSuggestionRequestRow,
    ChartVerificationRequestRow,
    ChartVerificationRoundRow,
    DiagnosisBatchRow,
    DiagnosisRoundRow,
    DraftRow,
    ElementCreateRow,
    ElementHistoryRow,
    ElementRow,
    FindingVetoRow,
    FormationRequestRow,
    InflightFormationRow,
    InflightRevisionRow,
    ItemOutcomeRow,
    ItemRevisionRow,
    ItemRow,
    ItemSuggestionRow,
    OperationRow,
    ProjectRow,
    RecognitionRead,
    RecognizedElementRow,
    IssueRow,
    RequestContent,
    ReviewFindingRow,
    StagePayloadRow,
    SupplementRow,
    TraceLinkRow,
)
from app.repositories.in_memory import InMemoryAudit, InMemoryTraceGraph
from app.services.analysis_transformation import AnalysisTransformationService
from app.services.chart_collaboration import ChartCollaborationService
from app.services.item_formation import ItemFormationService, RequirementItemService
from app.services.item_review import ItemReviewService
from app.services.material_receiving import MaterialReceivingService
from app.services.model_orchestration import ModelInferenceOrchestration


def _as_uuid(ref: Optional[str]) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(ref) if ref is not None else None
    except (ValueError, AttributeError, TypeError):
        return None


class SqlProjectScope:
    def __init__(self, session: Session) -> None:
        self._s = session

    def is_project_selected(self, project_ref: str) -> bool:
        pid = _as_uuid(project_ref)
        return pid is not None and self._s.get(Project, pid) is not None


def _project_row(p: Project) -> ProjectRow:
    return ProjectRow(
        str(p.id), p.name, p.scope, p.background,
        created_at=p.created_at.isoformat() if p.created_at else None,
        domain_profile_key=p.domain_profile_key,
    )


class SqlProjectRepository:
    """业务项目 LDM-001 写/读（物理属来源资产仓储）。"""

    def __init__(self, session: Session) -> None:
        self._s = session

    def create(self, name: str, scope: Optional[str], background: Optional[str],
               domain_profile_key: Optional[str] = None,
               operator_ref: str = "", idempotency_key: Optional[str] = None) -> str:
        p = Project(name=name, scope=scope, background=background,
                    domain_profile_key=domain_profile_key,
                    operator_ref=operator_ref, idempotency_key=idempotency_key)
        self._s.add(p)
        self._s.flush()
        return str(p.id)

    def get(self, project_id: str) -> Optional[ProjectRow]:
        p = self._s.get(Project, _as_uuid(project_id))
        return _project_row(p) if p else None

    def list_all(self) -> list[ProjectRow]:
        rows = self._s.scalars(select(Project).order_by(Project.created_at)).all()
        return [_project_row(p) for p in rows]

    def find_by_idempotency_key(self, key: str) -> Optional[ProjectRow]:
        p = self._s.scalars(select(Project).where(Project.idempotency_key == key)).first()
        return _project_row(p) if p else None


class SqlModelResultRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def record_intake_judgement(
        self, judgement: ModelJudgement, applies_to: Optional[str], basis: str
    ) -> str:
        mr = ModelResult(
            judgement=judgement.value,
            basis=basis or None,
            applies_to_ref=_as_uuid(applies_to),
            stage="source_intake",
            process_status="pending",
        )
        self._s.add(mr)
        self._s.flush()
        return str(mr.id)

    def read_intake_judgement(self, model_result_ref: str) -> Optional[ModelJudgement]:
        mr = self._s.get(ModelResult, _as_uuid(model_result_ref))
        return ModelJudgement(mr.judgement) if mr else None

    def read_basis(self, model_result_ref: str) -> Optional[str]:
        mr = self._s.get(ModelResult, _as_uuid(model_result_ref))
        return mr.basis if mr else None

    # --- SCN-001-P02 需求要素识别类 LDM-015 ---
    def record_element_recognition(
        self, applies_to: Optional[str], result_code: str,
        elements: Sequence[RecognizedElementRow], basis: str,
    ) -> str:
        payload = [
            {
                "element_type": e.element_type,
                "content": e.content,
                "source_anchor": e.source_anchor,
                "confidence": e.confidence,
                "model_verdict": e.model_verdict,
                "verdict_reason": e.verdict_reason,
            }
            for e in elements
        ]
        mr = ModelResult(
            judgement=result_code,  # 结果分类码：recognized/no_elements/failed
            basis=basis or None,
            result_content=json.dumps(payload, ensure_ascii=False) if payload else None,
            applies_to_ref=_as_uuid(applies_to),
            stage="element_recognition",
            process_status="pending",
        )
        self._s.add(mr)
        self._s.flush()
        return str(mr.id)

    def read_element_recognition(self, model_result_ref: str) -> Optional[RecognitionRead]:
        mr = self._s.get(ModelResult, _as_uuid(model_result_ref))
        if mr is None:
            return None
        rows: list[RecognizedElementRow] = []
        if mr.result_content:
            for item in json.loads(mr.result_content):
                rows.append(
                    RecognizedElementRow(
                        element_type=item["element_type"],
                        content=item["content"],
                        source_anchor=item.get("source_anchor"),
                        confidence=item.get("confidence"),
                        model_verdict=item.get("model_verdict"),
                        verdict_reason=item.get("verdict_reason"),
                    )
                )
        return RecognitionRead(result_code=mr.judgement, elements=tuple(rows), basis=mr.basis)

    # --- SCN-001-P03/P04 复核/执行类 LDM-015 ---
    def record_stage_payload(
        self, stage: str, applies_to: Optional[str], result_code: str,
        payload_json: Optional[str], basis: str,
        recheck_idempotency_key: Optional[str] = None,
    ) -> str:
        # recheck_idempotency_key：仅结构复核受理信封写入，落索引列供等值幂等查询
        # （取代 result_content LIKE 片段匹配）；其余 stage 恒 None。
        mr = ModelResult(
            judgement=result_code,
            basis=basis or None,
            result_content=payload_json,
            applies_to_ref=_as_uuid(applies_to),
            stage=stage,
            process_status="pending",
            recheck_idempotency_key=recheck_idempotency_key or None,
        )
        self._s.add(mr)
        self._s.flush()
        return str(mr.id)

    def read_stage_payload(self, model_result_ref: str) -> Optional[StagePayloadRow]:
        mr = self._s.get(ModelResult, _as_uuid(model_result_ref))
        if mr is None:
            return None
        return StagePayloadRow(
            ref=str(mr.id), stage=mr.stage, result_code=mr.judgement,
            payload=mr.result_content, basis=mr.basis,
        )

    def update_stage_payload(self, model_result_ref: str, payload_json: str) -> None:
        mr = self._s.get(ModelResult, _as_uuid(model_result_ref))
        if mr is not None:
            mr.result_content = payload_json
            self._s.flush()

    def latest_stage_payload(self, stage: str, applies_to: str) -> Optional[StagePayloadRow]:
        mr = self._s.scalars(
            select(ModelResult)
            .where(ModelResult.stage == stage, ModelResult.applies_to_ref == _as_uuid(applies_to))
            .order_by(ModelResult.created_at.desc(), ModelResult.id.desc())
            .limit(1)
        ).first()
        if mr is None:
            return None
        return StagePayloadRow(
            ref=str(mr.id), stage=mr.stage, result_code=mr.judgement,
            payload=mr.result_content, basis=mr.basis,
        )

    # --- SCN-004 图表建议/核对类 LDM-015 处置状态 ---

    def set_process_status(self, model_result_ref: str, status: str) -> None:
        mr = self._s.get(ModelResult, _as_uuid(model_result_ref))
        if mr is None:
            return
        mr.process_status = status
        self._s.flush()

    # --- LDM-015 采纳结论明细（AI效能统计口径设计 §7）---
    def record_adoption(
        self, *, model_result_ref: str, project_ref: str, stage: str,
        subject_type: str, subject_ref: str, outcome: str,
        operator_ref: str, idempotency_key: str, basis_ref: Optional[str] = None,
    ) -> None:
        exists = self._s.scalars(
            select(AdoptionRecord).where(AdoptionRecord.idempotency_key == idempotency_key)
        ).first()
        if exists is not None:  # 幂等重放：不重复登记
            return
        self._s.add(AdoptionRecord(
            model_result_ref=_as_uuid(model_result_ref),
            project_id=_as_uuid(project_ref),
            stage=stage,
            subject_type=subject_type,
            subject_ref=_as_uuid(subject_ref),
            outcome=outcome,
            basis_ref=_as_uuid(basis_ref) if basis_ref else None,
            operator_ref=operator_ref,
            idempotency_key=idempotency_key,
        ))
        self._s.flush()

    def read_process_status(self, model_result_ref: str) -> Optional[str]:
        mr = self._s.get(ModelResult, _as_uuid(model_result_ref))
        return mr.process_status if mr else None

    def stage_payloads_of(self, stage: str, applies_to_refs: Sequence[str]) -> list[StagePayloadRow]:
        ids = [u for u in (_as_uuid(r) for r in applies_to_refs) if u is not None]
        if not ids:
            return []
        rows = self._s.scalars(
            select(ModelResult)
            .where(ModelResult.stage == stage, ModelResult.applies_to_ref.in_(ids))
            .order_by(ModelResult.created_at.desc(), ModelResult.id.desc())
        ).all()
        return [
            StagePayloadRow(ref=str(mr.id), stage=mr.stage, result_code=mr.judgement,
                            payload=mr.result_content, basis=mr.basis)
            for mr in rows
        ]


class SqlProcessRecordRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def find_context_by_idempotency(self, key: str) -> Optional[str]:
        row = self._s.scalar(select(IntakeRequest).where(IntakeRequest.idempotency_key == key))
        return str(row.id) if row else None

    def create_intake_request(
        self, project_ref: str, key: str, raw_text: str, source_note: str, operator_ref: str
    ) -> str:
        req = IntakeRequest(
            project_id=_as_uuid(project_ref),
            raw_text=raw_text,
            source_note=source_note or "",
            operator_ref=operator_ref,
            idempotency_key=key,
        )
        self._s.add(req)
        self._s.flush()
        return str(req.id)

    def context_exists(self, context_ref: str) -> bool:
        cid = _as_uuid(context_ref)
        return cid is not None and self._s.get(IntakeRequest, cid) is not None

    def read_request_content(self, context_ref: str) -> Optional[RequestContent]:
        req = self._s.get(IntakeRequest, _as_uuid(context_ref))
        if req is None:
            return None
        return RequestContent(str(req.project_id), req.raw_text, req.source_note)

    def mark_stopped(self, context_ref: str, reason: str, next_action: str) -> None:
        req = self._s.get(IntakeRequest, _as_uuid(context_ref))
        if req is not None:
            req.stop_next_action = next_action

    def read_stop_next_action(self, context_ref: str) -> Optional[str]:
        req = self._s.get(IntakeRequest, _as_uuid(context_ref))
        return req.stop_next_action if req else None

    # --- SCN-001-P02 识别请求上下文（ParseRequest）---
    def find_parse_context_by_idempotency(self, key: str) -> Optional[str]:
        row = self._s.scalar(select(ParseRequest).where(ParseRequest.idempotency_key == key))
        return str(row.id) if row else None

    def latest_parse_context_of_material(self, material_ref: str) -> Optional[str]:
        mid = _as_uuid(material_ref)
        if mid is None:
            return None
        row = self._s.scalars(
            select(ParseRequest)
            .where(ParseRequest.material_ref == mid)
            .order_by(ParseRequest.created_at.desc(), ParseRequest.id.desc())
            .limit(1)
        ).first()
        return str(row.id) if row else None

    def create_parse_request(
        self, project_ref: str, key: str, material_ref: str, operator_ref: str
    ) -> str:
        req = ParseRequest(
            project_id=_as_uuid(project_ref),
            material_ref=_as_uuid(material_ref),
            operator_ref=operator_ref,
            idempotency_key=key,
        )
        self._s.add(req)
        self._s.flush()
        return str(req.id)

    def parse_context_exists(self, context_ref: str) -> bool:
        cid = _as_uuid(context_ref)
        return cid is not None and self._s.get(ParseRequest, cid) is not None

    def read_parse_material_ref(self, context_ref: str) -> Optional[str]:
        req = self._s.get(ParseRequest, _as_uuid(context_ref))
        return str(req.material_ref) if req else None

    def mark_parse_stopped(self, context_ref: str, reason: str, next_action: str) -> None:
        req = self._s.get(ParseRequest, _as_uuid(context_ref))
        if req is not None:
            req.stop_next_action = next_action

    def read_parse_stop_next_action(self, context_ref: str) -> Optional[str]:
        req = self._s.get(ParseRequest, _as_uuid(context_ref))
        return req.stop_next_action if req else None

    # --- 工作区版本 ---
    def read_workspace_version(self, context_ref: str) -> int:
        req = self._s.get(ParseRequest, _as_uuid(context_ref))
        return req.workspace_version if req else 1

    def bump_workspace_version(self, context_ref: str) -> int:
        req = self._s.get(ParseRequest, _as_uuid(context_ref))
        if req is None:
            return 1
        req.workspace_version = (req.workspace_version or 1) + 1
        self._s.flush()
        return req.workspace_version

    def project_of_context(self, context_ref: str) -> Optional[str]:
        ref = _as_uuid(context_ref)
        if ref is None:
            return None
        for model in (IntakeRequest, ParseRequest):
            row = self._s.get(model, ref)
            if row is not None:
                return str(row.project_id)
        return None

    # --- P03/P04 操作请求上下文 ---
    def find_operation_by_idempotency(self, key: str) -> Optional[str]:
        row = self._s.scalar(select(ElementOperation).where(ElementOperation.idempotency_key == key))
        return str(row.id) if row else None

    def create_element_operation(
        self, project_ref: str, context_ref: str, kind: str,
        payload_json: str, operator_ref: str, key: str,
    ) -> str:
        op = ElementOperation(
            project_id=_as_uuid(project_ref),
            parse_context_ref=_as_uuid(context_ref),
            kind=kind,
            payload=payload_json,
            operator_ref=operator_ref,
            idempotency_key=key,
        )
        self._s.add(op)
        self._s.flush()
        return str(op.id)

    def read_element_operation(self, operation_ref: str) -> Optional[OperationRow]:
        op = self._s.get(ElementOperation, _as_uuid(operation_ref))
        if op is None:
            return None
        return OperationRow(
            id=str(op.id), kind=op.kind, parse_context_ref=str(op.parse_context_ref),
            payload=op.payload, operator_ref=op.operator_ref,
        )

    def find_inflight_revisions(
        self, context_ref: str, element_refs: Sequence[str]
    ) -> list[InflightRevisionRow]:
        """给定知识项上尚未终态的 AI 修订运行（确认守卫数据源）。

        「哪条知识项在被 AI 修订」这件事没有单独的列可查，只能沿派发链路反着走：
        AI 修订派发时先建一条 kind='revision' 的 process_element_operation（payload 里
        记着 target_element_refs），再建一条 kind='element_execution' 的 agent_run 挂在
        那条 operation 上（context_ref 指向它）。所以这里 join 两表取未终态的运行，
        再解 payload 把行摊到知识项。kind='revision' 只有 AI 修订这一个产地（复核用
        'review'、AI 执行用 'execution'），不会误伤别的操作。
        走 agent_run.context_ref 与 process_element_operation.parse_context_ref 既有索引。

        只按状态粗筛不判龄：判死阈值按 lane 定，是 run_liveness 的职责。
        """
        wanted = {str(r) for r in element_refs}
        cid = _as_uuid(context_ref)
        if not wanted or cid is None:
            return []
        rows = self._s.execute(
            select(
                ElementOperation.id, ElementOperation.payload,
                AgentRun.id, AgentRun.status, AgentRun.created_at,
            )
            .join(AgentRun, AgentRun.context_ref == ElementOperation.id)
            .where(
                ElementOperation.parse_context_ref == cid,
                ElementOperation.kind == "revision",
                AgentRun.kind == "element_execution",
                AgentRun.status.in_(
                    (AgentRunStatus.QUEUED.value, AgentRunStatus.STARTED.value)
                ),
            )
            .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        ).all()
        found: list[InflightRevisionRow] = []
        for op_id, payload, run_id, status, created_at in rows:
            try:
                targets = json.loads(payload or "{}").get("target_element_refs", [])
                # 交集判定收进 try：targets 非列表（如显式 null）时的 TypeError 同样属「payload 脏了」，
                # 不该让确认动作炸——decide 走同一方法，这里炸了软拦截守卫反把主路堵死
                matched = [str(t) for t in targets if str(t) in wanted]
            except (ValueError, AttributeError, TypeError):
                continue  # payload 脏了当作查不到在途修订
            for target in matched:
                found.append(InflightRevisionRow(
                    element_ref=target, operation_ref=str(op_id),
                    agent_run_ref=str(run_id), status=status, created_at=created_at,
                ))
        return found

    # --- P04 变更草案（同一上下文仅一份 open，新建即替换旧 open）---
    def save_change_draft(
        self, project_ref: str, context_ref: str, workspace_version: int,
        operation_type: str, origin: str, items_json: str,
        target_refs_json: str, suggestion_refs_json: str, source_ranges_json: str,
        impact_summary_json: str, create_gate: str, next_action: Optional[str],
    ) -> str:
        cid = _as_uuid(context_ref)
        for old in self._s.scalars(
            select(ElementChangeDraft).where(
                ElementChangeDraft.parse_context_ref == cid,
                ElementChangeDraft.status == "open",
            )
        ):
            old.status = "cancelled"
        draft = ElementChangeDraft(
            project_id=_as_uuid(project_ref),
            parse_context_ref=cid,
            workspace_version=workspace_version,
            operation_type=operation_type,
            origin=origin,
            items=items_json,
            target_refs=target_refs_json,
            suggestion_refs=suggestion_refs_json,
            source_ranges=source_ranges_json,
            impact_summary=impact_summary_json,
            create_gate=create_gate,
            next_action=next_action,
            status="open",
        )
        self._s.add(draft)
        self._s.flush()
        return str(draft.id)

    def _draft_row(self, d: ElementChangeDraft) -> DraftRow:
        return DraftRow(
            id=str(d.id), parse_context_ref=str(d.parse_context_ref),
            workspace_version=d.workspace_version, operation_type=d.operation_type,
            origin=d.origin, items=d.items,
            target_refs=d.target_refs or "[]", suggestion_refs=d.suggestion_refs or "[]",
            source_ranges=d.source_ranges or "[]", impact_summary=d.impact_summary or "[]",
            create_gate=d.create_gate, next_action=d.next_action, status=d.status,
            updated_at=d.updated_at.isoformat() if d.updated_at else None,
        )

    def read_open_draft(self, context_ref: str) -> Optional[DraftRow]:
        d = self._s.scalars(
            select(ElementChangeDraft)
            .where(
                ElementChangeDraft.parse_context_ref == _as_uuid(context_ref),
                ElementChangeDraft.status == "open",
            )
            .order_by(ElementChangeDraft.created_at.desc())
            .limit(1)
        ).first()
        return self._draft_row(d) if d else None

    def read_draft(self, draft_ref: str) -> Optional[DraftRow]:
        d = self._s.get(ElementChangeDraft, _as_uuid(draft_ref))
        return self._draft_row(d) if d else None

    def mark_draft_confirmed(self, draft_ref: str) -> None:
        d = self._s.get(ElementChangeDraft, _as_uuid(draft_ref))
        if d is not None:
            d.status = "confirmed"
            self._s.flush()

    # --- TC-08 完备度投影（仅 AEP-024 写；整批替换，可整层重算）---

    def replace_facet_projection(self, element_ref: str, rows: list[FacetProjectionRow]) -> None:
        eid = _as_uuid(element_ref)
        for old in self._s.scalars(
            select(ElementFacetProjection).where(ElementFacetProjection.element_ref == eid)
        ).all():
            self._s.delete(old)
        for r in rows:
            self._s.add(ElementFacetProjection(
                element_ref=eid,
                element_version=r.element_version,
                rubric_version=r.rubric_version,
                facet_key=r.facet_key,
                facet_status=r.facet_status,
                evidence=r.evidence,
                note=r.note,
                correctness=r.correctness,
                completeness=r.completeness,
                model_result_ref=_as_uuid(r.model_result_ref),
            ))
        self._s.flush()

    def facet_projections_of(self, element_refs: list[str]) -> dict[str, list[FacetProjectionRow]]:
        ids = [u for u in (_as_uuid(e) for e in element_refs) if u is not None]
        if not ids:
            return {}
        out: dict[str, list[FacetProjectionRow]] = {}
        for p in self._s.scalars(
            select(ElementFacetProjection)
            .where(ElementFacetProjection.element_ref.in_(ids))
            .order_by(ElementFacetProjection.created_at, ElementFacetProjection.id)
        ).all():
            out.setdefault(str(p.element_ref), []).append(FacetProjectionRow(
                element_ref=str(p.element_ref),
                element_version=p.element_version,
                rubric_version=p.rubric_version,
                facet_key=p.facet_key,
                facet_status=p.facet_status,
                evidence=p.evidence,
                note=p.note,
                correctness=p.correctness,
                completeness=p.completeness,
                model_result_ref=str(p.model_result_ref) if p.model_result_ref else None,
            ))
        return out

    # --- 条目陈述达标投影（仅条目形成/修订链路写；整批替换，可整层重算）---

    def replace_item_structure_projection(
        self, item_ref: str, rows: list[ItemStructureProjectionRow]
    ) -> None:
        iid = _as_uuid(item_ref)
        for old in self._s.scalars(
            select(ItemStructureProjection).where(ItemStructureProjection.item_ref == iid)
        ).all():
            self._s.delete(old)
        for r in rows:
            self._s.add(ItemStructureProjection(
                item_ref=iid,
                item_content_rev=r.item_content_rev,
                profile_version=r.profile_version,
                convention_key=r.convention_key or "ears-cn",
                row_kind=r.row_kind,
                key=r.key,
                facet_status=r.facet_status,
                value_text=r.value_text,
                evidence=r.evidence,
                note=r.note,
                statement_conformance=r.statement_conformance,
                completeness=r.completeness,
                model_result_ref=_as_uuid(r.model_result_ref),
            ))
        self._s.flush()

    def item_structure_projections_of(
        self, item_refs: list[str]
    ) -> dict[str, list[ItemStructureProjectionRow]]:
        ids = [u for u in (_as_uuid(i) for i in item_refs) if u is not None]
        if not ids:
            return {}
        out: dict[str, list[ItemStructureProjectionRow]] = {}
        for p in self._s.scalars(
            select(ItemStructureProjection)
            .where(ItemStructureProjection.item_ref.in_(ids))
            .order_by(ItemStructureProjection.created_at, ItemStructureProjection.id)
        ).all():
            out.setdefault(str(p.item_ref), []).append(ItemStructureProjectionRow(
                item_ref=str(p.item_ref),
                item_content_rev=p.item_content_rev,
                profile_version=p.profile_version,
                row_kind=p.row_kind,
                key=p.key,
                facet_status=p.facet_status,
                value_text=p.value_text,
                evidence=p.evidence,
                note=p.note,
                statement_conformance=p.statement_conformance,
                completeness=p.completeness,
                model_result_ref=str(p.model_result_ref) if p.model_result_ref else None,
                convention_key=p.convention_key or "ears-cn",
            ))
        return out


class SqlSourceAssetRepository:
    """LDM-002/003 唯一权威写入口。已接入时从接入请求上下文（同 DB）取内容形成 LDM-002。"""

    def __init__(self, session: Session) -> None:
        self._s = session

    def _record_of(self, context_ref: str) -> Optional[IntakeRecord]:
        cid = _as_uuid(context_ref)
        if cid is None:
            return None
        return self._s.scalar(select(IntakeRecord).where(IntakeRecord.context_ref == cid))

    def save_material_and_intake_record(self, context_ref: str, model_result_ref: str) -> str:
        cid = _as_uuid(context_ref)
        req = self._s.get(IntakeRequest, cid)
        material = Material(
            project_id=req.project_id,
            raw_text=req.raw_text,
            source_note=req.source_note,
            # 材料一态制（2026-08-07）：名称默认取正文首行；哈希导入时一次计算，此后不许改写。
            name=material_default_name(
                req.raw_text, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
            ),
            content_sha256=hashlib.sha256(req.raw_text.encode("utf-8")).hexdigest(),
        )
        self._s.add(material)
        self._s.flush()
        self._s.add(
            IntakeRecord(
                project_id=req.project_id,
                context_ref=cid,
                intake_conclusion=IntakeConclusion.ACCEPTED.value,
                material_ref=material.id,
                model_result_ref=_as_uuid(model_result_ref),
            )
        )
        self._s.flush()
        return str(material.id)

    def save_intake_conclusion(
        self, context_ref: str, conclusion: IntakeConclusion, model_result_ref: str
    ) -> None:
        cid = _as_uuid(context_ref)
        req = self._s.get(IntakeRequest, cid)
        self._s.add(
            IntakeRecord(
                project_id=req.project_id,
                context_ref=cid,
                intake_conclusion=conclusion.value,
                material_ref=None,
                model_result_ref=_as_uuid(model_result_ref),
            )
        )
        self._s.flush()

    def conclusion_of(self, context_ref: str) -> Optional[IntakeConclusion]:
        rec = self._record_of(context_ref)
        return IntakeConclusion(rec.intake_conclusion) if rec else None

    def material_of(self, context_ref: str) -> Optional[str]:
        rec = self._record_of(context_ref)
        return str(rec.material_ref) if rec and rec.material_ref else None

    def model_result_ref_of(self, context_ref: str) -> Optional[str]:
        rec = self._record_of(context_ref)
        return str(rec.model_result_ref) if rec and rec.model_result_ref else None

    # --- SCN-001-P02 已接入校验 + LDM-004/005 写入/读取 ---
    def _parse_result_of(self, context_ref: str) -> Optional[MaterialParseResult]:
        cid = _as_uuid(context_ref)
        if cid is None:
            return None
        return self._s.scalar(
            select(MaterialParseResult).where(MaterialParseResult.context_ref == cid)
        )

    def is_material_accepted(self, material_ref: str) -> bool:
        mid = _as_uuid(material_ref)
        if mid is None:
            return False
        rec = self._s.scalar(
            select(IntakeRecord).where(
                IntakeRecord.material_ref == mid,
                IntakeRecord.intake_conclusion == IntakeConclusion.ACCEPTED.value,
            )
        )
        return rec is not None

    def read_material_content(self, material_ref: str) -> Optional[RequestContent]:
        mat = self._s.get(Material, _as_uuid(material_ref))
        if mat is None:
            return None
        proj = self._s.get(Project, mat.project_id)  # P6a/P6b：项目领域上下文 + 领域档案 key
        return RequestContent(
            str(mat.project_id), mat.raw_text, mat.source_note,
            project_scope=proj.scope if proj else None,
            project_background=proj.background if proj else None,
            domain_profile_key=proj.domain_profile_key if proj else None,
        )

    def save_parse_result_and_elements(
        self, context_ref: str, material_ref: str, model_result_ref: str,
        elements: Sequence[RecognizedElementRow],
    ) -> str:
        cid = _as_uuid(context_ref)
        req = self._s.get(ParseRequest, cid)
        parse = MaterialParseResult(
            project_id=req.project_id,
            material_ref=_as_uuid(material_ref),
            context_ref=cid,
            model_result_ref=_as_uuid(model_result_ref),
            parse_status=MaterialParseStatus.PARSED.value,
        )
        self._s.add(parse)
        self._s.flush()
        for e in elements:
            # 全集登记：初始 process_status 一律「待确认」；模型裁定入证据字段
            self._s.add(
                RequirementElement(
                    project_id=req.project_id,
                    parse_result_ref=parse.id,
                    element_type=e.element_type,
                    content=e.content,
                    source_anchor=e.source_anchor,
                    confidence=e.confidence,
                    process_status=ElementProcessStatus.PENDING_CONFIRMATION.value,
                    model_verdict=e.model_verdict,
                    verdict_reason=e.verdict_reason,
                    model_result_ref=_as_uuid(model_result_ref),
                )
            )
        self._s.flush()
        return str(parse.id)

    def save_parse_conclusion(
        self, context_ref: str, material_ref: str, model_result_ref: str, note: str
    ) -> str:
        cid = _as_uuid(context_ref)
        req = self._s.get(ParseRequest, cid)
        parse = MaterialParseResult(
            project_id=req.project_id,
            material_ref=_as_uuid(material_ref),
            context_ref=cid,
            model_result_ref=_as_uuid(model_result_ref),
            parse_status=MaterialParseStatus.UNPROCESSABLE.value,
            parse_note=note or None,
        )
        self._s.add(parse)
        self._s.flush()
        return str(parse.id)

    def parse_status_of(self, context_ref: str) -> Optional[str]:
        parse = self._parse_result_of(context_ref)
        return parse.parse_status if parse else None

    def parse_result_of(self, context_ref: str) -> Optional[str]:
        parse = self._parse_result_of(context_ref)
        return str(parse.id) if parse else None

    def parse_context_of(self, parse_result_ref: str) -> Optional[str]:
        parse = self._s.get(MaterialParseResult, _as_uuid(parse_result_ref))
        return str(parse.context_ref) if parse else None

    def parse_basis_of(self, context_ref: str) -> Optional[str]:
        parse = self._parse_result_of(context_ref)
        if parse is None:
            return None
        if parse.model_result_ref is not None:
            mr = self._s.get(ModelResult, parse.model_result_ref)
            if mr is not None and mr.basis:
                return mr.basis
        return parse.parse_note

    def _element_row(self, r: RequirementElement) -> ElementRow:
        return ElementRow(
            id=str(r.id),
            element_type=r.element_type,
            content=r.content,
            source_anchor=r.source_anchor,
            confidence=r.confidence,
            process_status=r.process_status,
            model_verdict=r.model_verdict,
            verdict_reason=r.verdict_reason,
            noise_triage=r.noise_triage,
            version=r.version or 1,
            superseded=bool(r.superseded),
            review_conclusion=r.review_conclusion,
            review_basis=r.review_basis,
            revision_draft=r.revision_draft,
            correction_note=r.correction_note,
            origin_refs=r.origin_refs,
            updated_at=r.updated_at.isoformat() if r.updated_at else None,
        )

    def elements_of(self, parse_result_ref: str) -> list[ElementRow]:
        pid = _as_uuid(parse_result_ref)
        if pid is None:
            return []
        rows = self._s.scalars(
            select(RequirementElement)
            .where(RequirementElement.parse_result_ref == pid)
            .order_by(RequirementElement.created_at, RequirementElement.id)
        ).all()
        return [self._element_row(r) for r in rows]

    def list_project_elements_by_type(
        self, project_ref: str, element_types
    ) -> list[ElementRow]:
        pid = _as_uuid(project_ref)
        if pid is None or not element_types:
            return []
        rows = self._s.scalars(
            select(RequirementElement)
            .where(
                RequirementElement.project_id == pid,
                RequirementElement.element_type.in_(list(element_types)),
                RequirementElement.superseded.is_(False),
            )
            .order_by(RequirementElement.created_at, RequirementElement.id)
        ).all()
        return [self._element_row(r) for r in rows]

    def _new_element(self, parse: MaterialParseResult, c: ElementCreateRow) -> RequirementElement:
        return RequirementElement(
            project_id=parse.project_id,
            parse_result_ref=parse.id,
            element_type=c.element_type,
            content=c.content,
            source_anchor=c.source_anchor,
            confidence=c.confidence,
            process_status=c.process_status,
            model_verdict=c.model_verdict,
            correction_state=c.correction_state,
            correction_note=c.correction_note,
            origin_refs=json.dumps(list(c.origin_refs), ensure_ascii=False) if c.origin_refs else None,
        )

    # --- SCN-001-P04 版本关系层：替代旧要素（superseded 留痕）+ 创建新要素 ---
    def apply_element_changes(
        self, context_ref: str,
        creates,
        closes,
    ) -> list[str]:
        parse = self._parse_result_of(context_ref)
        if parse is None:
            return []
        for element_id, correction_state, note in closes:
            row = self._s.get(RequirementElement, _as_uuid(element_id))
            if row is not None and row.parse_result_ref == parse.id:
                row.superseded = True
                row.correction_state = correction_state
                row.correction_note = note or row.correction_note
        new_ids: list[str] = []
        for c in creates:
            row = self._new_element(parse, c)
            self._s.add(row)
            self._s.flush()
            new_ids.append(str(row.id))
        self._s.flush()
        return new_ids

    def add_elements(self, context_ref: str, creates) -> list[str]:
        parse = self._parse_result_of(context_ref)
        if parse is None:
            return []
        new_ids: list[str] = []
        for c in creates:
            row = self._new_element(parse, c)
            self._s.add(row)
            self._s.flush()
            new_ids.append(str(row.id))
        return new_ids

    # --- SCN-001-P03 确认生命周期写方法 ---
    def get_element(self, element_id: str) -> Optional[ElementRow]:
        row = self._s.get(RequirementElement, _as_uuid(element_id))
        return self._element_row(row) if row else None

    def set_element_status(
        self, element_id: str, status: str, bump_version: bool = False,
        clear_review: bool = False,
    ) -> None:
        row = self._s.get(RequirementElement, _as_uuid(element_id))
        if row is None:
            return
        row.process_status = status
        if bump_version:
            row.version = (row.version or 1) + 1
        if clear_review:
            row.review_conclusion = None
            row.review_basis = None
            row.revision_draft = None
        self._s.flush()

    def set_element_noise_triage(self, element_id: str, triage: Optional[str]) -> None:
        row = self._s.get(RequirementElement, _as_uuid(element_id))
        if row is None:
            return
        # 只动人工标记：model_verdict / verdict_reason 是模型证据，此处一个字节都不碰
        row.noise_triage = triage
        self._s.flush()

    def set_element_review(
        self, element_id: str, conclusion: Optional[str], basis: Optional[str],
        revision_draft: Optional[str],
    ) -> None:
        row = self._s.get(RequirementElement, _as_uuid(element_id))
        if row is None:
            return
        row.review_conclusion = conclusion
        row.review_basis = basis
        if revision_draft is not None:
            row.revision_draft = revision_draft
        self._s.flush()

    def set_revision_draft(self, element_id: str, draft: Optional[str]) -> None:
        row = self._s.get(RequirementElement, _as_uuid(element_id))
        if row is None:
            return
        row.revision_draft = draft
        self._s.flush()

    def apply_element_edit(
        self, element_id: str, element_type: Optional[str], content: Optional[str],
        source_anchor: Optional[str], note: Optional[str],
    ) -> None:
        row = self._s.get(RequirementElement, _as_uuid(element_id))
        if row is None:
            return
        if element_type is not None:
            row.element_type = element_type
        if content is not None:
            row.content = content
        if source_anchor is not None:
            row.source_anchor = source_anchor
        if note is not None:
            row.correction_note = note
        row.version = (row.version or 1) + 1
        self._s.flush()

    # --- 变更历史 ---
    def record_element_history(
        self, element_ref: str, project_ref: str, version: int, action: str,
        from_status: Optional[str], to_status: Optional[str],
        operator_ref: str, note: Optional[str], snapshot_json: Optional[str],
    ) -> None:
        eid = _as_uuid(element_ref)
        project_id = _as_uuid(project_ref)
        if project_id is None and eid is not None:
            row = self._s.get(RequirementElement, eid)
            project_id = row.project_id if row is not None else None
        self._s.add(ElementHistory(
            element_ref=eid,
            project_id=project_id,
            version=version,
            action=action,
            from_status=from_status,
            to_status=to_status,
            operator_ref=operator_ref or "",
            note=note,
            snapshot=snapshot_json,
        ))
        self._s.flush()

    def element_history_of(self, element_ref: str) -> list[ElementHistoryRow]:
        rows = self._s.scalars(
            select(ElementHistory)
            .where(ElementHistory.element_ref == _as_uuid(element_ref))
            .order_by(ElementHistory.created_at.desc(), ElementHistory.id.desc())
        ).all()
        return [
            ElementHistoryRow(
                id=str(h.id), element_ref=str(h.element_ref), version=h.version,
                action=h.action, from_status=h.from_status, to_status=h.to_status,
                operator_ref=h.operator_ref, note=h.note, snapshot=h.snapshot,
                at=h.created_at.isoformat() if h.created_at else "",
            )
            for h in rows
        ]

    def merge_history_for_material(self, material_ref: str) -> list[ElementHistoryRow]:
        # snapshot 是 JSON 文本列：先用 LIKE 收窄候选，再在 Python 侧按字段精确判定
        # （避免依赖数据库 JSON 函数，且 LIKE 命中包含关系不等于字段相等）。
        if not material_ref:
            return []
        rows = self._s.scalars(
            select(ElementHistory)
            .where(ElementHistory.action == "merge")
            .where(ElementHistory.snapshot.like(f"%{material_ref}%"))
            .order_by(ElementHistory.created_at.asc(), ElementHistory.id.asc())
        ).all()
        out: list[ElementHistoryRow] = []
        for h in rows:
            try:
                snap = json.loads(h.snapshot or "{}")
            except ValueError:
                continue
            if not isinstance(snap, dict) or snap.get("merged_from_material") != material_ref:
                continue
            out.append(ElementHistoryRow(
                id=str(h.id), element_ref=str(h.element_ref), version=h.version,
                action=h.action, from_status=h.from_status, to_status=h.to_status,
                operator_ref=h.operator_ref, note=h.note, snapshot=h.snapshot,
                at=h.created_at.isoformat() if h.created_at else "",
            ))
        return out

    # --- 改源联动（勘误/补入）---
    def apply_material_erratum(
        self, material_ref: str, new_raw_text: str, note: str, operator_ref: str
    ) -> int:
        mat = self._s.get(Material, _as_uuid(material_ref))
        if mat is None:
            return 1
        # 旧正文快照留档（原快照不改写）
        self._s.add(MaterialRevision(
            material_ref=mat.id,
            source_version=mat.source_version or 1,
            raw_text=mat.raw_text,
            note=note,
            operator_ref=operator_ref or "",
        ))
        mat.raw_text = new_raw_text
        mat.source_version = (mat.source_version or 1) + 1
        self._s.flush()
        return mat.source_version

    def add_material_supplement(
        self, material_ref: str, content: str, basis: str, operator_ref: str
    ) -> str:
        row = MaterialSupplement(
            material_ref=_as_uuid(material_ref),
            content=content,
            basis=basis,
            operator_ref=operator_ref or "",
        )
        self._s.add(row)
        self._s.flush()
        return str(row.id)

    def supplements_of(self, material_ref: str) -> list[SupplementRow]:
        rows = self._s.scalars(
            select(MaterialSupplement)
            .where(MaterialSupplement.material_ref == _as_uuid(material_ref))
            .order_by(MaterialSupplement.created_at, MaterialSupplement.id)
        ).all()
        return [
            SupplementRow(
                id=str(s.id), content=s.content, basis=s.basis,
                operator_ref=s.operator_ref,
                at=s.created_at.isoformat() if s.created_at else "",
            )
            for s in rows
        ]

    def material_source_version(self, material_ref: str) -> int:
        mat = self._s.get(Material, _as_uuid(material_ref))
        return (mat.source_version or 1) if mat else 1


def _wire_sync_intake(
    session: Session, judge: Optional[SourceIntakeJudge]
) -> tuple[MaterialReceivingService, ModelInferenceOrchestration]:
    """同步接入装配：service + 编排（编排 on_judgement 已挂 accept）。"""
    model_results = SqlModelResultRepository(session)
    process_records = SqlProcessRecordRepository(session)
    orchestration = ModelInferenceOrchestration(
        judge=judge or StubSourceIntakeJudge(),
        process_records=process_records,
        model_results=model_results,
    )
    service = MaterialReceivingService(
        project_scope=SqlProjectScope(session),
        model_orchestration=orchestration,
        model_results=model_results,
        process_records=process_records,
        source_assets=SqlSourceAssetRepository(session),
        trace_graph=InMemoryTraceGraph(),  # LDM-013 落库=后续增量
        audit=InMemoryAudit(),              # 审计落库=后续增量
    )

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
    return service, orchestration


def build_sql_service(
    session: Session,
    auto_complete: bool = True,
    judge: Optional[SourceIntakeJudge] = None,
) -> MaterialReceivingService:
    service, orchestration = _wire_sync_intake(session, judge)
    if not auto_complete:
        orchestration.on_judgement = None
    return service


def run_source_intake_judgement(
    session: Session, context_ref: str, judge: SourceIntakeJudge
) -> None:
    """worker/inline 内：对已提交的 context 同步跑判定（judge→登记 LDM-015→accept）。"""
    _, orchestration = _wire_sync_intake(session, judge)
    orchestration.request_source_intake_judgement(context_ref)


# ---- SCN-001-P02 分析转化服务同步装配 ----


def _wire_sync_recognition(
    session: Session,
    recognizer: Optional[SourceElementRecognizer] = None,
    reviewer: Optional[ElementReviewer] = None,
    executor: Optional[ElementOperationExecutor] = None,
) -> tuple[AnalysisTransformationService, ModelInferenceOrchestration]:
    """同步分析转化装配：service + 编排（识别/复核/执行回交钩子均已挂 accept）。"""
    model_results = SqlModelResultRepository(session)
    process_records = SqlProcessRecordRepository(session)
    source_assets = SqlSourceAssetRepository(session)
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
    return service, orchestration


def build_sql_analysis_service(
    session: Session,
    auto_complete: bool = True,
    recognizer: Optional[SourceElementRecognizer] = None,
    reviewer: Optional[ElementReviewer] = None,
    executor: Optional[ElementOperationExecutor] = None,
) -> AnalysisTransformationService:
    service, orchestration = _wire_sync_recognition(session, recognizer, reviewer, executor)
    if not auto_complete:
        orchestration.on_recognition = None
        orchestration.on_review = None
        orchestration.on_execution = None
    return service


def run_element_recognition_judgement(
    session: Session, context_ref: str, recognizer: SourceElementRecognizer
) -> None:
    """worker/inline 内：对已提交的识别 context 同步跑识别（recognize→登记 LDM-015→accept）。"""
    _, orchestration = _wire_sync_recognition(session, recognizer=recognizer)
    orchestration.request_element_recognition(context_ref)


def run_element_review_judgement(
    session: Session, operation_ref: str, reviewer: ElementReviewer
) -> None:
    """worker/inline 内：对复核操作上下文同步跑复核（review→登记 LDM-015→accept）。"""
    _, orchestration = _wire_sync_recognition(session, reviewer=reviewer)
    orchestration.request_element_review(operation_ref)


def run_element_execution_judgement(
    session: Session, operation_ref: str, executor: ElementOperationExecutor
) -> None:
    """worker/inline 内：对执行操作上下文同步跑 AI 执行（execute→登记 LDM-015→accept）。"""
    _, orchestration = _wire_sync_recognition(session, executor=executor)
    orchestration.request_element_execution(operation_ref)


# ---- SCN-002-P01 条目形成：仓储 + 同步装配 ----


class SqlRequirementItemRepository:
    """LDM-007 需求条目 + 字段修订记录（写权威=条目形成服务/需求条目服务，VAL-003）。"""

    def __init__(self, session: Session) -> None:
        self._s = session

    def _item_row(self, r: RequirementItem) -> ItemRow:
        return ItemRow(
            id=str(r.id), project_ref=str(r.project_id),
            parse_result_ref=str(r.parse_result_ref),
            formation_context_ref=str(r.formation_context_ref),
            req_no=r.req_no, expression=r.expression, req_type=r.req_type,
            status=r.status, version_no=r.version_no or 1,
            source_element_refs=r.source_element_refs,
            formation_basis_ref=str(r.formation_basis_ref) if r.formation_basis_ref else None,
            curation_note=r.curation_note,
            boundary_note=r.boundary_note,
            verification_method=r.verification_method,
            verification_note=r.verification_note,
            priority=r.priority,
        )

    def create_pending_item(
        self, project_ref: str, parse_result_ref: str, formation_context_ref: str,
        req_no: str, expression: str, req_type: str,
        source_element_refs_json: str, formation_basis_ref: Optional[str],
        curation_note: Optional[str] = None, boundary_note: Optional[str] = None,
        verification_method: Optional[str] = None, verification_note: Optional[str] = None,
    ) -> str:
        item = RequirementItem(
            project_id=_as_uuid(project_ref),
            parse_result_ref=_as_uuid(parse_result_ref),
            formation_context_ref=_as_uuid(formation_context_ref),
            req_no=req_no, expression=expression, req_type=req_type,
            status=RequirementItemStatus.PENDING_CONFIRMATION.value,  # AEP-038 唯一产物
            version_no=1,
            source_element_refs=source_element_refs_json,
            formation_basis_ref=_as_uuid(formation_basis_ref),
            curation_note=curation_note,
            boundary_note=boundary_note,
            verification_method=verification_method,
            verification_note=verification_note,
            # priority 无模型通道：形成时恒空，仅经 AEP-036 人工设定
        )
        self._s.add(item)
        self._s.flush()
        return str(item.id)

    def get_item(self, item_ref: str) -> Optional[ItemRow]:
        item = self._s.get(RequirementItem, _as_uuid(item_ref))
        return self._item_row(item) if item else None

    def items_of_parse_result(self, parse_result_ref: str) -> list[ItemRow]:
        pid = _as_uuid(parse_result_ref)
        if pid is None:
            return []
        rows = self._s.scalars(
            select(RequirementItem)
            .where(RequirementItem.parse_result_ref == pid)
            .order_by(RequirementItem.created_at, RequirementItem.id)
        ).all()
        return [self._item_row(r) for r in rows]

    def count_items_of_project(self, project_ref: str) -> int:
        pid = _as_uuid(project_ref)
        if pid is None:
            return 0
        rows = self._s.scalars(
            select(RequirementItem.id).where(RequirementItem.project_id == pid)
        ).all()
        return len(rows)

    def max_req_seq_of_project(self, project_ref: str) -> int:
        pid = _as_uuid(project_ref)
        if pid is None:
            return 0
        req_nos = self._s.scalars(
            select(RequirementItem.req_no).where(RequirementItem.project_id == pid)
        ).all()
        seqs = [int(m.group(1)) for m in (re.match(r"REQ-(\d+)$", n or "") for n in req_nos) if m]
        return max(seqs, default=0)

    def apply_item_field(self, item_ref: str, field_key: str, new_value: str) -> None:
        item = self._s.get(RequirementItem, _as_uuid(item_ref))
        if item is None:
            return
        if field_key == "expression":
            item.expression = new_value
        elif field_key == "req_type":
            item.req_type = new_value
        elif field_key == "curation_note":
            item.curation_note = new_value
        elif field_key == "boundary_note":
            item.boundary_note = new_value
        elif field_key == "verification_method":
            item.verification_method = new_value
        elif field_key == "verification_note":
            item.verification_note = new_value
        elif field_key == "priority":
            item.priority = new_value
        elif field_key == "source_element_refs":
            # 来源要素引用清单（JSON 数组字符串；规范化由服务层完成，此处只落库）
            item.source_element_refs = new_value
        self._s.flush()

    def record_item_revision(
        self, item_ref: str, field_key: str, before_value: str, after_value: str,
        revision_mode: str, suggestion_ref: Optional[str], reason: Optional[str],
        operator_ref: str, idempotency_key: str,
        selected_point_refs: Optional[str] = None,
    ) -> str:
        rev = RequirementItemRevision(
            item_ref=_as_uuid(item_ref), field_key=field_key,
            before_value=before_value, after_value=after_value,
            revision_mode=revision_mode, suggestion_ref=_as_uuid(suggestion_ref),
            selected_point_refs=selected_point_refs,
            reason=reason, operator_ref=operator_ref, idempotency_key=idempotency_key,
        )
        self._s.add(rev)
        self._s.flush()
        return str(rev.id)

    def _revision_row(self, r: RequirementItemRevision) -> ItemRevisionRow:
        return ItemRevisionRow(
            id=str(r.id), item_ref=str(r.item_ref), field_key=r.field_key,
            before_value=r.before_value, after_value=r.after_value,
            revision_mode=r.revision_mode,
            suggestion_ref=str(r.suggestion_ref) if r.suggestion_ref else None,
            selected_point_refs=r.selected_point_refs,
            reason=r.reason, operator_ref=r.operator_ref,
            at=r.created_at.isoformat() if r.created_at else "",
        )

    def revisions_of(self, item_ref: str) -> list[ItemRevisionRow]:
        iid = _as_uuid(item_ref)
        if iid is None:
            return []
        rows = self._s.scalars(
            select(RequirementItemRevision)
            .where(RequirementItemRevision.item_ref == iid)
            .order_by(RequirementItemRevision.created_at.desc(), RequirementItemRevision.id.desc())
        ).all()
        return [self._revision_row(r) for r in rows]

    def find_revision_by_idempotency(self, key: str) -> Optional[ItemRevisionRow]:
        row = self._s.scalar(
            select(RequirementItemRevision).where(RequirementItemRevision.idempotency_key == key)
        )
        return self._revision_row(row) if row else None

    def set_item_status(self, item_ref: str, status: str) -> None:
        item = self._s.get(RequirementItem, _as_uuid(item_ref))
        if item is None:
            return
        item.status = status
        self._s.flush()

    def confirmed_items_of_project(self, project_ref: str) -> list[ItemRow]:
        pid = _as_uuid(project_ref)
        if pid is None:
            return []
        rows = self._s.scalars(
            select(RequirementItem)
            .where(
                RequirementItem.project_id == pid,
                RequirementItem.status == RequirementItemStatus.CONFIRMED.value,
            )
            .order_by(RequirementItem.created_at, RequirementItem.id)
        ).all()
        return [self._item_row(r) for r in rows]


class SqlItemFormationProcessRepository:
    """条目化批次过程记录：批次上下文 + 逐要素归因 + 建议处置 + 幂等。"""

    def __init__(self, session: Session) -> None:
        self._s = session

    def find_formation_by_idempotency(self, key: str) -> Optional[str]:
        row = self._s.scalar(
            select(ItemFormationRequest).where(ItemFormationRequest.idempotency_key == key)
        )
        return str(row.id) if row else None

    def create_formation_request(
        self, project_ref: str, parse_context_ref: str, parse_result_ref: str,
        scope_type: str, target_refs_json: str, operator_ref: str, idempotency_key: str,
        convention_key: str = "ears-cn",
    ) -> str:
        req = ItemFormationRequest(
            project_id=_as_uuid(project_ref),
            parse_context_ref=_as_uuid(parse_context_ref),
            parse_result_ref=_as_uuid(parse_result_ref),
            scope_type=scope_type, target_refs=target_refs_json,
            operator_ref=operator_ref, idempotency_key=idempotency_key,
            convention_key=convention_key,
        )
        self._s.add(req)
        self._s.flush()
        return str(req.id)

    def get_formation_request(self, formation_context_ref: str) -> Optional[FormationRequestRow]:
        req = self._s.get(ItemFormationRequest, _as_uuid(formation_context_ref))
        if req is None:
            return None
        return FormationRequestRow(
            id=str(req.id), project_ref=str(req.project_id),
            parse_context_ref=str(req.parse_context_ref),
            parse_result_ref=str(req.parse_result_ref),
            scope_type=req.scope_type, target_refs=req.target_refs or "[]",
            operator_ref=req.operator_ref, stop_next_action=req.stop_next_action,
            convention_key=req.convention_key or "ears-cn",
        )

    def mark_formation_stopped(self, formation_context_ref: str, next_action: str) -> None:
        req = self._s.get(ItemFormationRequest, _as_uuid(formation_context_ref))
        if req is not None:
            req.stop_next_action = next_action
            self._s.flush()

    def record_outcome(
        self, formation_context_ref: str, element_ref: str, result_status: str,
        item_ref: Optional[str], formation_basis_ref: Optional[str],
        reason: Optional[str], next_action: Optional[str],
    ) -> None:
        self._s.add(ItemizationOutcome(
            formation_context_ref=_as_uuid(formation_context_ref),
            element_ref=_as_uuid(element_ref),
            result_status=result_status,
            item_ref=_as_uuid(item_ref),
            formation_basis_ref=_as_uuid(formation_basis_ref),
            reason=reason, next_action=next_action,
        ))
        self._s.flush()

    def _outcome_row(self, o: ItemizationOutcome) -> ItemOutcomeRow:
        return ItemOutcomeRow(
            id=str(o.id), formation_context_ref=str(o.formation_context_ref),
            element_ref=str(o.element_ref), result_status=o.result_status,
            item_ref=str(o.item_ref) if o.item_ref else None,
            formation_basis_ref=str(o.formation_basis_ref) if o.formation_basis_ref else None,
            reason=o.reason, next_action=o.next_action,
        )

    def outcomes_of(self, formation_context_ref: str) -> list[ItemOutcomeRow]:
        cid = _as_uuid(formation_context_ref)
        if cid is None:
            return []
        rows = self._s.scalars(
            select(ItemizationOutcome)
            .where(ItemizationOutcome.formation_context_ref == cid)
            .order_by(ItemizationOutcome.created_at, ItemizationOutcome.id)
        ).all()
        return [self._outcome_row(o) for o in rows]

    def latest_outcomes_of_parse_result(self, parse_result_ref: str) -> list[ItemOutcomeRow]:
        pid = _as_uuid(parse_result_ref)
        if pid is None:
            return []
        latest = self._s.scalars(
            select(ItemFormationRequest)
            .where(ItemFormationRequest.parse_result_ref == pid)
            .order_by(ItemFormationRequest.created_at.desc(), ItemFormationRequest.id.desc())
            .limit(1)
        ).first()
        return self.outcomes_of(str(latest.id)) if latest else []

    def latest_formation_of_parse_result(self, parse_result_ref: str) -> Optional[str]:
        pid = _as_uuid(parse_result_ref)
        if pid is None:
            return None
        latest = self._s.scalars(
            select(ItemFormationRequest.id)
            .where(ItemFormationRequest.parse_result_ref == pid)
            .order_by(ItemFormationRequest.created_at.desc(), ItemFormationRequest.id.desc())
            .limit(1)
        ).first()
        return str(latest) if latest else None

    def find_inflight_formation_of_parse_result(
        self, parse_result_ref: str
    ) -> Optional[InflightFormationRow]:
        """同解析结果最近一个 AgentRun 仍处 queued/started 的批次（HK-1 单飞守卫）。

        只按状态筛选不判龄（判活阈值由服务经 run_liveness 按 lane 裁定）；
        走 agent_run.context_ref 与 formation_request.parse_result_ref 既有索引，无需新索引。
        """
        pid = _as_uuid(parse_result_ref)
        if pid is None:
            return None
        row = self._s.execute(
            select(ItemFormationRequest.id, AgentRun.id, AgentRun.status, AgentRun.created_at)
            .join(AgentRun, AgentRun.context_ref == ItemFormationRequest.id)
            .where(
                ItemFormationRequest.parse_result_ref == pid,
                AgentRun.kind == "item_formation",
                AgentRun.status.in_(
                    (AgentRunStatus.QUEUED.value, AgentRunStatus.STARTED.value)
                ),
            )
            .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
            .limit(1)
        ).first()
        if row is None:
            return None
        return InflightFormationRow(
            formation_context_ref=str(row[0]), agent_run_ref=str(row[1]),
            status=row[2], created_at=row[3],
        )

    def _latest_recheck_envelope_run(self, *criteria) -> Optional[InflightFormationRow]:
        """复核信封×AgentRun 联查骨架（在飞去重/幂等重放共用；issue #8 清理债收口）。"""
        row = self._s.execute(
            select(ModelResult.id, AgentRun.id, AgentRun.status, AgentRun.created_at)
            .join(AgentRun, AgentRun.context_ref == ModelResult.id)
            .where(
                ModelResult.stage == "item_structure_recheck",
                AgentRun.kind == "item_structure_recheck",
                *criteria,
            )
            .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
            .limit(1)
        ).first()
        if row is None:
            return None
        return InflightFormationRow(
            formation_context_ref=str(row[0]), agent_run_ref=str(row[1]),
            status=row[2], created_at=row[3],
        )

    def find_inflight_recheck_of_parse_result(
        self, parse_result_ref: str
    ) -> Optional[InflightFormationRow]:
        """该解析结果最近一个仍处 queued/started 的结构复核批次（AEP-114 在飞去重）。

        复核批次上下文=LDM-015 受理信封（stage=item_structure_recheck，applies_to=parse_result；
        零迁移设计，任务卡裁定 4）；AgentRun.context_ref 指向该信封。只按状态筛选不判龄，
        是否仍算在飞由服务经 run_liveness 按 lane 阈值裁定（同 HK-1）。
        """
        pid = _as_uuid(parse_result_ref)
        if pid is None:
            return None
        return self._latest_recheck_envelope_run(
            ModelResult.applies_to_ref == pid,
            AgentRun.status.in_(
                (AgentRunStatus.QUEUED.value, AgentRunStatus.STARTED.value)
            ),
        )

    def find_recheck_by_idempotency(
        self, key: str, parse_result_ref: Optional[str] = None
    ) -> Optional[InflightFormationRow]:
        """幂等重放（issue #12 卡B K_LIKE 修复）：按索引列等值找回原复核批次。

        取代旧 result_content LIKE 片段匹配（`%`/`_` 键当通配、含反斜杠键 json 转义后永不
        自匹配、无域过滤跨项目泄漏三病）：改索引列 recheck_idempotency_key 等值＋域过滤
        （applies_to_ref＝信封所属 parse_result）。注意：键契约=前端 randomUUID 全局唯一，
        列亦全局 unique——域过滤是防御性收窄，不是「同 key 跨项目并存」的承载（违约外部同 key
        会在写入撞 unique，兜底挂 issue #12，审查裁定 F1）。列存原始键，天然
        免疫 json 序列化形状漂移（K_LIKE-c）。任何状态的原批次均命中——重放不因原批失败而
        重复受理。AgentRun 外连（inline 执行无 run 行；行形状以空 run 引用回落）。
        """
        if not key:
            return None
        criteria = [
            ModelResult.stage == "item_structure_recheck",
            ModelResult.judgement == "batch_accepted",
            ModelResult.recheck_idempotency_key == key,
        ]
        pid = _as_uuid(parse_result_ref) if parse_result_ref else None
        if pid is not None:
            criteria.append(ModelResult.applies_to_ref == pid)
        envelope = self._s.execute(
            select(ModelResult.id, ModelResult.created_at)
            .where(*criteria)
            .order_by(ModelResult.created_at.desc(), ModelResult.id.desc())
            .limit(1)
        ).first()
        if envelope is None:
            return None
        run = self._s.execute(
            select(AgentRun.id, AgentRun.status, AgentRun.created_at)
            .where(AgentRun.context_ref == envelope[0],
                   AgentRun.kind == "item_structure_recheck")
            .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
            .limit(1)
        ).first()
        return InflightFormationRow(
            formation_context_ref=str(envelope[0]),
            agent_run_ref=str(run[0]) if run else "",
            status=run[1] if run else "succeeded",
            created_at=run[2] if run else envelope[1],
        )

    def save_suggestion(
        self, item_ref: str, field_key: str, proposed_value: str, reason: str,
        model_result_ref: Optional[str],
    ) -> str:
        s = ItemRevisionSuggestion(
            item_ref=_as_uuid(item_ref), field_key=field_key,
            proposed_value=proposed_value, reason=reason,
            status="candidate", model_result_ref=_as_uuid(model_result_ref),
        )
        self._s.add(s)
        self._s.flush()
        return str(s.id)

    def _suggestion_row(self, s: ItemRevisionSuggestion) -> ItemSuggestionRow:
        return ItemSuggestionRow(
            id=str(s.id), item_ref=str(s.item_ref), field_key=s.field_key,
            proposed_value=s.proposed_value, reason=s.reason, status=s.status,
            model_result_ref=str(s.model_result_ref) if s.model_result_ref else None,
            created_at=s.created_at.isoformat() if s.created_at else None,
        )

    def get_suggestion(self, suggestion_ref: str) -> Optional[ItemSuggestionRow]:
        s = self._s.get(ItemRevisionSuggestion, _as_uuid(suggestion_ref))
        return self._suggestion_row(s) if s else None

    def suggestions_of_items(self, item_refs: Sequence[str]) -> list[ItemSuggestionRow]:
        ids = [u for u in (_as_uuid(r) for r in item_refs) if u is not None]
        if not ids:
            return []
        rows = self._s.scalars(
            select(ItemRevisionSuggestion)
            .where(ItemRevisionSuggestion.item_ref.in_(ids))
            .order_by(ItemRevisionSuggestion.created_at, ItemRevisionSuggestion.id)
        ).all()
        return [self._suggestion_row(s) for s in rows]

    def set_suggestion_status(self, suggestion_ref: str, status: str) -> None:
        s = self._s.get(ItemRevisionSuggestion, _as_uuid(suggestion_ref))
        if s is not None:
            s.status = status
            self._s.flush()


def _wire_sync_item_formation(
    session: Session,
    item_formatter: Optional[RequirementItemFormatter] = None,
    commit_each: bool = False,
    item_rechecker: Optional[ItemStructureRechecker] = None,
) -> tuple[ItemFormationService, ModelInferenceOrchestration]:
    """同步条目形成装配：service + 编排（逐要素 accept + 批次收束 + 结构复核回调已挂）。"""
    model_results = SqlModelResultRepository(session)
    process_records = SqlProcessRecordRepository(session)
    source_assets = SqlSourceAssetRepository(session)
    formation_process = SqlItemFormationProcessRepository(session)
    items = SqlRequirementItemRepository(session)
    orchestration = ModelInferenceOrchestration(
        process_records=process_records,
        model_results=model_results,
        source_assets=source_assets,
        item_formatter=item_formatter or StubRequirementItemFormatter(),
        item_formation_process=formation_process,
        item_rechecker=item_rechecker or StubItemStructureRechecker(),
        commit_each=session.commit if commit_each else None,
    )
    # AEP-097 对话能力（同步装配用 stub：无模型环境保持确定性）
    item_service = RequirementItemService(
        items=items,
        formation_process=formation_process,
        process_records=process_records,
        source_assets=source_assets,
        reviews=SqlItemReviewRepository(session),
        model_results=model_results,
    )
    from app.services.config_registry import resolve_active_convention

    def _supporting_basis_writer(project_ref, element_ref, item_ref, operator_ref):
        # P7 §1.2：/引用依据 复用 P4 create_supporting_basis（业务翼确认态要素 → 条目预建立边）
        from app.api.schemas import SupportingBasisCommand
        from app.repositories.trace_read import TraceReadRepository
        from app.services.trace_analysis import TraceAnalysisService
        tsvc = TraceAnalysisService(
            TraceReadRepository(session), SqlTraceLinkRepository(session),
            SqlIssueRepository(session),
        )
        return tsvc.create_supporting_basis(project_ref, SupportingBasisCommand(
            element_ref=element_ref, item_ref=item_ref, operator_ref=operator_ref,
        ))

    service = ItemFormationService(
        model_orchestration=orchestration,
        model_results=model_results,
        process_records=process_records,
        formation_process=formation_process,
        items=items,
        source_assets=source_assets,
        command_interpreter=StubFormationCommandInterpreter(),
        draft_composer=StubItemDraftComposer(),
        explainer=StubItemExplainer(),
        item_service=item_service,
        active_convention_resolver=lambda: resolve_active_convention(session),
        supporting_basis_writer=_supporting_basis_writer,
        session=session,  # 缺陷 4：链式派发事务解耦＋单条失败持久通知
    )
    orchestration.on_item_formation_element = service.accept_item_formation_element_result
    orchestration.on_item_formation_completed = service.complete_item_formation_batch
    orchestration.on_item_recheck_prepare = service.prepare_item_structure_recheck
    orchestration.on_item_recheck_result = service.accept_item_structure_recheck_result
    # 走查第三轮裁定：内容修订自动结构体检（对话修订与 AEP-036 同一挂点）
    item_service.on_content_changed_recheck = service.dispatch_chained_recheck
    return service, orchestration


def build_sql_item_formation_service(
    session: Session,
    auto_complete: bool = True,
    item_formatter: Optional[RequirementItemFormatter] = None,
    item_rechecker: Optional[ItemStructureRechecker] = None,
) -> ItemFormationService:
    service, orchestration = _wire_sync_item_formation(
        session, item_formatter, item_rechecker=item_rechecker
    )
    if not auto_complete:
        orchestration.on_item_formation_element = None
        orchestration.on_item_formation_completed = None
    return service


def build_sql_requirement_item_service(
    session: Session, chain_incremental: bool = True,
) -> RequirementItemService:
    # 阶段策略解耦 P1：对象层不再绑 on_revised 链式回环——修订只写事实、发布 ItemRevised
    # 事件，链式增量诊断迁回评审裁决采纳动作（_adopt_revise）显式续接。故 chain_incremental
    # 已成惰性参数（对象层任何情况都不自动链），仅为调用方（seed_full_demo）签名兼容而保留。
    service = RequirementItemService(
        items=SqlRequirementItemRepository(session),
        formation_process=SqlItemFormationProcessRepository(session),
        process_records=SqlProcessRecordRepository(session),
        source_assets=SqlSourceAssetRepository(session),
        reviews=SqlItemReviewRepository(session),
        model_results=SqlModelResultRepository(session),
    )
    # 走查第三轮裁定：内容修订自动结构体检（同步装配 stub 立即收束；结构体检链 P2 前不动）
    formation_service, _ = _wire_sync_item_formation(session)
    service.on_content_changed_recheck = formation_service.dispatch_chained_recheck
    return service


def run_item_formation_judgement(
    session: Session, formation_context_ref: str, item_formatter: RequirementItemFormatter
) -> None:
    """worker/inline 内：对条目化批次逐要素跑格式化（format→登记 LDM-015→accept 裁定创建）。

    逐要素 commit：单要素条目一旦承接立即可见，界面按条目实时刷新。
    """
    _, orchestration = _wire_sync_item_formation(
        session, item_formatter=item_formatter, commit_each=True
    )
    orchestration.request_item_formation(formation_context_ref)


def run_item_structure_recheck_judgement(
    session: Session, recheck_context_ref: str, rechecker: ItemStructureRechecker
) -> None:
    """worker/inline 内：对结构复核批次逐条目跑体检（recheck→登记 LDM-015→重写投影）。

    逐条目 commit：单条目投影一旦重写立即可见，界面徽标实时刷新（AEP-114）。
    """
    _, orchestration = _wire_sync_item_formation(
        session, item_rechecker=rechecker, commit_each=True
    )
    orchestration.request_item_structure_recheck(recheck_context_ref)


# ---- SCN-003 条目评审（诊断批次过程记录 + LDM-009 诊断轮次/发现项）----


class SqlItemReviewRepository:
    """条目评审仓储：诊断批次 + LDM-009 诊断轮次、发现项、复核判断、确认依据。"""

    def __init__(self, session: Session) -> None:
        self._s = session

    @property
    def session(self) -> Session:
        """供跨仓储写同事务记录（HK-2 读侧对账联查 AgentRun），与主事实同 commit。"""
        return self._s

    # ---- 诊断批次（过程记录）----

    def find_batch_by_idempotency(self, key: str) -> Optional[str]:
        row = self._s.scalar(
            select(ItemDiagnosisRequest.id).where(ItemDiagnosisRequest.idempotency_key == key)
        )
        return str(row) if row else None

    def create_batch(
        self, project_ref: str, parse_context_ref: str, parse_result_ref: str,
        review_context_ref: str, item_refs_json: str, diagnosis_mode: str,
        operator_ref: str, idempotency_key: str,
    ) -> str:
        batch = ItemDiagnosisRequest(
            project_id=_as_uuid(project_ref),
            parse_context_ref=_as_uuid(parse_context_ref),
            parse_result_ref=_as_uuid(parse_result_ref),
            review_context_ref=_as_uuid(review_context_ref),
            item_refs=item_refs_json, diagnosis_mode=diagnosis_mode,
            operator_ref=operator_ref, idempotency_key=idempotency_key,
        )
        self._s.add(batch)
        self._s.flush()
        return str(batch.id)

    def _batch_row(self, b: ItemDiagnosisRequest) -> DiagnosisBatchRow:
        return DiagnosisBatchRow(
            id=str(b.id), project_ref=str(b.project_id),
            parse_context_ref=str(b.parse_context_ref),
            parse_result_ref=str(b.parse_result_ref),
            review_context_ref=str(b.review_context_ref),
            item_refs=b.item_refs, diagnosis_mode=b.diagnosis_mode,
            operator_ref=b.operator_ref, stop_next_action=b.stop_next_action,
            created_at=b.created_at.isoformat() if b.created_at else "",
        )

    def get_batch(self, batch_ref: str) -> Optional[DiagnosisBatchRow]:
        b = self._s.get(ItemDiagnosisRequest, _as_uuid(batch_ref))
        return self._batch_row(b) if b else None

    def batches_of_parse_result(self, parse_result_ref: str) -> list[DiagnosisBatchRow]:
        pid = _as_uuid(parse_result_ref)
        if pid is None:
            return []
        rows = self._s.scalars(
            select(ItemDiagnosisRequest)
            .where(ItemDiagnosisRequest.parse_result_ref == pid)
            .order_by(ItemDiagnosisRequest.created_at.desc(), ItemDiagnosisRequest.id.desc())
        ).all()
        return [self._batch_row(b) for b in rows]

    # ---- 诊断轮次（LDM-009）----

    def _round_row(self, r: ItemDiagnosisRound) -> DiagnosisRoundRow:
        return DiagnosisRoundRow(
            id=str(r.id), project_ref=str(r.project_id), item_ref=str(r.item_ref),
            batch_ref=str(r.batch_ref), round_no=r.round_no or 1,
            diagnosis_mode=r.diagnosis_mode,
            processing_status=r.processing_status, context_coverage=r.context_coverage,
            model_result_ref=str(r.model_result_ref) if r.model_result_ref else None,
            reason=r.reason, invalidated=bool(r.invalidated),
            invalidated_reason=r.invalidated_reason,
            trigger=r.trigger or "user_submit",
            verdict_kind=r.verdict_kind, verdict_summary=r.verdict_summary,
            revision_points=r.revision_points, supplement_gaps=r.supplement_gaps,
            superseded_by=str(r.superseded_by) if r.superseded_by else None,
            excluded_point_refs=r.excluded_point_refs,
            adjudication_decision=r.adjudication_decision,
            adjudication_selected_points=r.adjudication_selected_points,
            adjudication_reason=r.adjudication_reason,
            adjudication_point_edits=r.adjudication_point_edits,
            adjudication_operator=r.adjudication_operator,
            adjudicated_at=r.adjudicated_at.isoformat() if r.adjudicated_at else None,
            overridden=bool(r.overridden),
            confirm_result=r.confirm_result, confirm_basis=r.confirm_basis,
            confirmed_by=r.confirmed_by,
            created_at=r.created_at.isoformat() if r.created_at else "",
            quality_meta=r.quality_meta,
        )

    def create_round(
        self, project_ref: str, item_ref: str, batch_ref: str,
        diagnosis_mode: str, context_coverage: str, trigger: str = "user_submit",
    ) -> str:
        iid = _as_uuid(item_ref)
        existing = self._s.scalars(
            select(ItemDiagnosisRound.id).where(ItemDiagnosisRound.item_ref == iid)
        ).all()
        round_ = ItemDiagnosisRound(
            project_id=_as_uuid(project_ref), item_ref=iid,
            batch_ref=_as_uuid(batch_ref), round_no=len(existing) + 1,
            diagnosis_mode=diagnosis_mode, trigger=trigger,
            processing_status="diagnosing", context_coverage=context_coverage,
        )
        self._s.add(round_)
        self._s.flush()
        return str(round_.id)

    def running_round_of(self, batch_ref: str, item_ref: str) -> Optional[DiagnosisRoundRow]:
        r = self._s.scalar(
            select(ItemDiagnosisRound).where(
                ItemDiagnosisRound.batch_ref == _as_uuid(batch_ref),
                ItemDiagnosisRound.item_ref == _as_uuid(item_ref),
                ItemDiagnosisRound.processing_status == "diagnosing",
            )
        )
        return self._round_row(r) if r else None

    def finish_round(
        self, round_ref: str, processing_status: str,
        model_result_ref: Optional[str] = None, reason: Optional[str] = None,
    ) -> None:
        r = self._s.get(ItemDiagnosisRound, _as_uuid(round_ref))
        if r is None:
            return
        r.processing_status = processing_status
        if model_result_ref is not None:
            r.model_result_ref = _as_uuid(model_result_ref)
        if reason is not None:
            r.reason = reason
        self._s.flush()

    def rounds_of_batch(self, batch_ref: str) -> list[DiagnosisRoundRow]:
        rows = self._s.scalars(
            select(ItemDiagnosisRound)
            .where(ItemDiagnosisRound.batch_ref == _as_uuid(batch_ref))
            .order_by(ItemDiagnosisRound.round_no, ItemDiagnosisRound.id)
        ).all()
        return [self._round_row(r) for r in rows]

    def latest_round_of_item(self, item_ref: str) -> Optional[DiagnosisRoundRow]:
        r = self._s.scalar(
            select(ItemDiagnosisRound)
            .where(ItemDiagnosisRound.item_ref == _as_uuid(item_ref))
            .order_by(ItemDiagnosisRound.round_no.desc())
            .limit(1)
        )
        return self._round_row(r) if r else None

    def has_running_round(self, item_ref: str) -> bool:
        r = self._s.scalar(
            select(ItemDiagnosisRound.id).where(
                ItemDiagnosisRound.item_ref == _as_uuid(item_ref),
                ItemDiagnosisRound.processing_status == "diagnosing",
            ).limit(1)
        )
        return r is not None

    def has_user_initiated_round(self, item_ref: str) -> bool:
        # 白名单 EXISTS：只认用户显式发起的 trigger，新枚举值默认不算诊断史（失败关闭）；
        # NULL trigger 经 coalesce 按 user_submit 计（历史数据兜底，与 _round_row 归一同口径）。
        r = self._s.scalar(
            select(ItemDiagnosisRound.id).where(
                ItemDiagnosisRound.item_ref == _as_uuid(item_ref),
                func.coalesce(
                    ItemDiagnosisRound.trigger, DiagnosisTrigger.USER_SUBMIT.value
                ).in_(
                    [DiagnosisTrigger.USER_SUBMIT.value, DiagnosisTrigger.DIALOGUE_REEVAL.value]
                ),
            ).limit(1)
        )
        return r is not None

    def count_adopted_revise_rounds(self, item_ref: str) -> int:
        # 单条 COUNT（交互写路径，勿扫全批次）：采纳过的 revise 轮次数＝用户照办了几次仍未通过。
        # 失效轮同样计入（链式前置必然全失效，排除失效会使计数恒 0、熔断永不生效）。
        return int(self._s.scalar(
            select(func.count(ItemDiagnosisRound.id)).where(
                ItemDiagnosisRound.item_ref == _as_uuid(item_ref),
                ItemDiagnosisRound.verdict_kind == VerdictKind.REVISE.value,
                ItemDiagnosisRound.adjudication_decision == VerdictDecision.ADOPTED.value,
            )
        ) or 0)

    def invalidate_rounds_of_item(self, item_ref: str, reason: str) -> None:
        rows = self._s.scalars(
            select(ItemDiagnosisRound).where(
                ItemDiagnosisRound.item_ref == _as_uuid(item_ref),
                ItemDiagnosisRound.invalidated.is_(False),
            )
        ).all()
        for r in rows:
            r.invalidated = True
            r.invalidated_reason = reason
        self._s.flush()

    # ---- v5 结论与裁决（LDM-009）----

    def set_round_verdict(
        self, round_ref: str, verdict_kind: str, verdict_summary: str,
        revision_points_json: Optional[str], supplement_gaps_json: Optional[str],
        quality_meta_json: Optional[str] = None,
    ) -> None:
        r = self._s.get(ItemDiagnosisRound, _as_uuid(round_ref))
        if r is None:
            return
        r.verdict_kind = verdict_kind
        r.verdict_summary = verdict_summary
        r.revision_points = revision_points_json
        r.supplement_gaps = supplement_gaps_json
        if quality_meta_json is not None:
            r.quality_meta = quality_meta_json
        self._s.flush()

    def record_adjudication(
        self, round_ref: str, decision: str, selected_points_json: Optional[str],
        excluded_points_json: Optional[str], reason: Optional[str],
        operator_ref: str, idempotency_key: str,
        point_edits_json: Optional[str] = None,
    ) -> None:
        r = self._s.get(ItemDiagnosisRound, _as_uuid(round_ref))
        if r is None:
            return
        r.adjudication_decision = decision
        r.adjudication_selected_points = selected_points_json
        r.excluded_point_refs = excluded_points_json
        r.adjudication_reason = reason
        r.adjudication_point_edits = point_edits_json
        r.adjudication_operator = operator_ref
        r.adjudicated_at = datetime.now(timezone.utc)
        r.adjudication_idempotency_key = idempotency_key
        self._s.flush()

    def find_adjudication_by_idempotency(self, key: str) -> Optional[str]:
        r = self._s.scalar(
            select(ItemDiagnosisRound.id)
            .where(ItemDiagnosisRound.adjudication_idempotency_key == key)
        )
        return str(r) if r else None

    def supersede_round(self, old_round_ref: str, new_round_ref: str) -> None:
        r = self._s.get(ItemDiagnosisRound, _as_uuid(old_round_ref))
        if r is None:
            return
        r.superseded_by = _as_uuid(new_round_ref)
        self._s.flush()

    def mark_round_overridden(self, round_ref: str) -> None:
        r = self._s.get(ItemDiagnosisRound, _as_uuid(round_ref))
        if r is None:
            return
        r.overridden = True
        self._s.flush()

    # ---- 诊断发现项与逐项复核 ----

    def _finding_row(self, f: ItemReviewFinding) -> ReviewFindingRow:
        return ReviewFindingRow(
            id=str(f.id), round_ref=str(f.round_ref), item_ref=str(f.item_ref),
            finding_type=f.finding_type, diagnosis_summary=f.diagnosis_summary,
            basis_summary=f.basis_summary, suggested_disposition=f.suggested_disposition,
            suggested_field=f.suggested_field, suggested_value=f.suggested_value,
            suggested_reason=f.suggested_reason,
            suggestion_ref=str(f.suggestion_ref) if f.suggestion_ref else None,
            model_result_ref=str(f.model_result_ref) if f.model_result_ref else None,
            decision=f.decision, decision_reason=f.decision_reason,
            decision_operator=f.decision_operator,
            decided_at=f.decided_at.isoformat() if f.decided_at else None,
        )

    def add_finding(
        self, round_ref: str, item_ref: str, finding_type: str,
        diagnosis_summary: str, basis_summary: str, suggested_disposition: str,
        suggested_field: Optional[str], suggested_value: Optional[str],
        suggested_reason: Optional[str], suggestion_ref: Optional[str],
        model_result_ref: Optional[str],
    ) -> str:
        f = ItemReviewFinding(
            round_ref=_as_uuid(round_ref), item_ref=_as_uuid(item_ref),
            finding_type=finding_type, diagnosis_summary=diagnosis_summary,
            basis_summary=basis_summary, suggested_disposition=suggested_disposition,
            suggested_field=suggested_field, suggested_value=suggested_value,
            suggested_reason=suggested_reason, suggestion_ref=_as_uuid(suggestion_ref),
            model_result_ref=_as_uuid(model_result_ref),
        )
        self._s.add(f)
        self._s.flush()
        return str(f.id)

    def findings_of_round(self, round_ref: str) -> list[ReviewFindingRow]:
        rows = self._s.scalars(
            select(ItemReviewFinding)
            .where(ItemReviewFinding.round_ref == _as_uuid(round_ref))
            .order_by(ItemReviewFinding.created_at, ItemReviewFinding.id)
        ).all()
        return [self._finding_row(f) for f in rows]

    def get_finding(self, finding_ref: str) -> Optional[ReviewFindingRow]:
        f = self._s.get(ItemReviewFinding, _as_uuid(finding_ref))
        return self._finding_row(f) if f else None

    def find_decision_by_idempotency(self, key: str) -> Optional[str]:
        row = self._s.scalar(
            select(ItemReviewFinding.id).where(
                ItemReviewFinding.decision_idempotency_key == key
            )
        )
        return str(row) if row else None

    def record_decision(
        self, finding_ref: str, decision: str, reason: Optional[str],
        operator_ref: str, idempotency_key: str,
    ) -> None:
        f = self._s.get(ItemReviewFinding, _as_uuid(finding_ref))
        if f is None:
            return
        f.decision = decision
        f.decision_reason = reason
        f.decision_operator = operator_ref
        f.decided_at = datetime.now(timezone.utc)
        f.decision_idempotency_key = idempotency_key
        self._s.flush()

    # ---- 问题否决留痕（AEP-116）----

    def _veto_row(self, v: ItemFindingVeto) -> FindingVetoRow:
        return FindingVetoRow(
            id=str(v.id), project_ref=str(v.project_id), item_ref=str(v.item_ref),
            rule_code=v.rule_code, evidence_span=v.evidence_span,
            finding_type=v.finding_type, finding_summary=v.finding_summary or "",
            origin_finding_ref=str(v.origin_finding_ref) if v.origin_finding_ref else None,
            reason=v.reason, operator_ref=v.operator_ref,
            created_at=v.created_at.isoformat() if v.created_at else "",
            revoked_at=v.revoked_at.isoformat() if v.revoked_at else None,
            revoked_by=v.revoked_by,
        )

    def add_finding_veto(
        self, project_ref: str, item_ref: str, finding_type: str,
        rule_code: Optional[str], evidence_span: Optional[str],
        finding_summary: str, origin_finding_ref: Optional[str],
        reason: Optional[str], operator_ref: str, idempotency_key: str,
    ) -> str:
        v = ItemFindingVeto(
            project_id=_as_uuid(project_ref), item_ref=_as_uuid(item_ref),
            finding_type=finding_type, rule_code=rule_code, evidence_span=evidence_span,
            finding_summary=finding_summary,
            origin_finding_ref=_as_uuid(origin_finding_ref),
            reason=reason, operator_ref=operator_ref, idempotency_key=idempotency_key,
        )
        self._s.add(v)
        self._s.flush()
        return str(v.id)

    def vetoes_of_item(self, item_ref: str, include_revoked: bool = False) -> list[FindingVetoRow]:
        stmt = select(ItemFindingVeto).where(ItemFindingVeto.item_ref == _as_uuid(item_ref))
        if not include_revoked:
            stmt = stmt.where(ItemFindingVeto.revoked_at.is_(None))
        rows = self._s.scalars(
            stmt.order_by(ItemFindingVeto.created_at, ItemFindingVeto.id)
        ).all()
        return [self._veto_row(v) for v in rows]

    def get_finding_veto(self, veto_ref: str) -> Optional[FindingVetoRow]:
        v = self._s.get(ItemFindingVeto, _as_uuid(veto_ref))
        return self._veto_row(v) if v else None

    def find_veto_by_idempotency(self, key: str) -> Optional[str]:
        row = self._s.scalar(
            select(ItemFindingVeto.id).where(ItemFindingVeto.idempotency_key == key)
        )
        return str(row) if row else None

    def revoke_finding_veto(self, veto_ref: str, operator_ref: str) -> None:
        v = self._s.get(ItemFindingVeto, _as_uuid(veto_ref))
        if v is None or v.revoked_at is not None:  # 已撤销再撤＝空操作（幂等）
            return
        v.revoked_at = datetime.now(timezone.utc)
        v.revoked_by = operator_ref
        self._s.flush()

    # ---- 确认依据（P04）----

    def record_confirmation(
        self, round_ref: str, confirm_result: str, confirm_basis: str,
        operator_ref: str, idempotency_key: str,
    ) -> None:
        r = self._s.get(ItemDiagnosisRound, _as_uuid(round_ref))
        if r is None:
            return
        r.confirm_result = confirm_result
        r.confirm_basis = confirm_basis
        r.confirmed_by = operator_ref
        r.confirm_idempotency_key = idempotency_key
        self._s.flush()

    def find_confirmation_by_idempotency(self, key: str) -> Optional[str]:
        row = self._s.scalar(
            select(ItemDiagnosisRound.id).where(
                ItemDiagnosisRound.confirm_idempotency_key == key
            )
        )
        return str(row) if row else None


def _wire_sync_item_review(
    session: Session,
    item_diagnoser: Optional[RequirementItemDiagnoser] = None,
    commit_each: bool = False,
) -> tuple[ItemReviewService, ModelInferenceOrchestration]:
    """同步条目评审装配：service + 编排（prepare/result 回调已挂）。"""
    model_results = SqlModelResultRepository(session)
    process_records = SqlProcessRecordRepository(session)
    source_assets = SqlSourceAssetRepository(session)
    formation_process = SqlItemFormationProcessRepository(session)
    items = SqlRequirementItemRepository(session)
    reviews = SqlItemReviewRepository(session)
    orchestration = ModelInferenceOrchestration(
        process_records=process_records,
        model_results=model_results,
        source_assets=source_assets,
        item_diagnoser=item_diagnoser or StubRequirementItemDiagnoser(),
        item_reviews=reviews,
        commit_each=session.commit if commit_each else None,
    )
    service = ItemReviewService(
        model_orchestration=orchestration,
        model_results=model_results,
        process_records=process_records,
        formation_process=formation_process,
        items=items,
        source_assets=source_assets,
        reviews=reviews,
        command_interpreter=StubItemCommandInterpreter(),
        trace_links=SqlTraceLinkRepository(session),  # P7 评审业务依据段
    )
    orchestration.on_item_diagnosis_prepare = service.prepare_item_diagnosis
    orchestration.on_item_diagnosis_result = service.accept_item_diagnosis_result
    # 阶段策略解耦 P1：采纳修订承接方（revision_applier）。对象层不再绑 on_revised——
    # 链式增量诊断由评审服务在裁决采纳动作（_adopt_revise）内显式续接，不经对象层钩子。
    item_service = RequirementItemService(
        items=items, formation_process=formation_process,
        process_records=process_records, source_assets=source_assets,
        reviews=reviews, model_results=model_results,
    )
    # 走查第三轮裁定：评审侧采纳修订同样自动结构体检（同一 apply_item_revision 挂点）
    formation_service, _ = _wire_sync_item_formation(session, commit_each=commit_each)
    item_service.on_content_changed_recheck = formation_service.dispatch_chained_recheck
    service.revision_applier = item_service.apply_item_revision
    return service, orchestration


def build_sql_item_review_service(
    session: Session,
    auto_complete: bool = True,
    item_diagnoser: Optional[RequirementItemDiagnoser] = None,
) -> ItemReviewService:
    service, orchestration = _wire_sync_item_review(session, item_diagnoser)
    if not auto_complete:
        orchestration.on_item_diagnosis_prepare = None
        orchestration.on_item_diagnosis_result = None
    return service


def run_item_diagnosis_judgement(
    session: Session, batch_ref: str, item_diagnoser: RequirementItemDiagnoser
) -> None:
    """worker/inline 内：对诊断批次逐条目跑诊断（准入→AI→登记 LDM-015→承接写 LDM-009）。

    逐条目 commit：单条目结果一旦承接立即可见，界面按条目实时刷新（N13）。
    整批就地循环形态（inline 后台执行用）；rq 逐条目子 job 用 run_item_diagnosis_step。
    """
    _, orchestration = _wire_sync_item_review(session, item_diagnoser, commit_each=True)
    orchestration.request_item_diagnosis(batch_ref)


def run_item_diagnosis_step(
    session: Session, batch_ref: str, item_diagnoser: RequirementItemDiagnoser
) -> bool:
    """worker（rq）内：逐条目子 job 单步——处理下一个待诊断条目并 commit。

    返回处理后批次内是否仍有待诊断条目：True → worker 链式再入队下一子 job（增量重诊
    得以在同队列 FIFO 交错）；False → 批次收束，worker 收尾 mark_succeeded。
    """
    _, orchestration = _wire_sync_item_review(session, item_diagnoser, commit_each=True)
    return orchestration.diagnose_next_pending_item(batch_ref)


# ---- SCN-004 图表协同服务（LDM-012/013/011 + 图表过程记录）----


class SqlChartRepository:
    """LDM-012 需求图表 + 源码修订留痕（写权威=图表资产模块经图表协同服务，VAL-003）。"""

    def __init__(self, session: Session) -> None:
        self._s = session

    def _chart_row(self, c: RequirementChart) -> ChartRow:
        return ChartRow(
            id=str(c.id), project_ref=str(c.project_id), title=c.title,
            chart_kind=c.chart_kind, chart_type=c.chart_type, format=c.format,
            source_code=c.source_code, draft_version=c.draft_version or 1,
            status=c.status, status_reason=c.status_reason,
            source_kind=c.source_kind, source_refs=c.source_refs,
            creation_basis=c.creation_basis,
            verification_conclusion=c.verification_conclusion,
            confirm_basis=c.confirm_basis, confirmed_by=c.confirmed_by,
            created_by=c.created_by,
            created_at=c.created_at.isoformat() if c.created_at else "",
            updated_at=c.updated_at.isoformat() if c.updated_at else "",
        )

    def create_chart(
        self, project_ref: str, title: str, chart_kind: str, chart_type: str,
        format_: str, source_kind: str, source_refs_json: str,
        creation_basis: str, created_by: str,
    ) -> str:
        chart = RequirementChart(
            project_id=_as_uuid(project_ref), title=title,
            chart_kind=chart_kind, chart_type=chart_type, format=format_,
            source_code="", draft_version=1,
            status=ChartStatus.DRAFT.value, source_kind=source_kind,
            source_refs=source_refs_json, creation_basis=creation_basis,
            created_by=created_by,
        )
        self._s.add(chart)
        self._s.flush()
        return str(chart.id)

    def get_chart(self, chart_ref: str) -> Optional[ChartRow]:
        c = self._s.get(RequirementChart, _as_uuid(chart_ref))
        return self._chart_row(c) if c else None

    def charts_of_project(self, project_ref: str) -> list[ChartRow]:
        pid = _as_uuid(project_ref)
        if pid is None:
            return []
        rows = self._s.scalars(
            select(RequirementChart)
            .where(RequirementChart.project_id == pid)
            .order_by(RequirementChart.created_at.desc(), RequirementChart.id.desc())
        ).all()
        return [self._chart_row(c) for c in rows]

    def update_chart_source(
        self, chart_ref: str, source_code: str, format_: str,
        chart_type: str, chart_kind: str, source_refs_json: str,
    ) -> int:
        c = self._s.get(RequirementChart, _as_uuid(chart_ref))
        if c is None:
            return 0
        c.source_code = source_code
        c.format = format_
        c.chart_type = chart_type
        c.chart_kind = chart_kind
        c.source_refs = source_refs_json
        c.draft_version = (c.draft_version or 1) + 1
        self._s.flush()
        return c.draft_version

    def set_chart_status(
        self, chart_ref: str, status: str, status_reason: Optional[str] = None,
    ) -> None:
        c = self._s.get(RequirementChart, _as_uuid(chart_ref))
        if c is None:
            return
        c.status = status
        c.status_reason = status_reason
        self._s.flush()

    def set_chart_title(self, chart_ref: str, title: str) -> None:
        c = self._s.get(RequirementChart, _as_uuid(chart_ref))
        if c is None:
            return
        c.title = title
        self._s.flush()

    def record_confirmation(
        self, chart_ref: str, conclusion: str, confirm_basis: str, operator_ref: str,
    ) -> None:
        c = self._s.get(RequirementChart, _as_uuid(chart_ref))
        if c is None:
            return
        c.verification_conclusion = conclusion
        c.confirm_basis = confirm_basis
        c.confirmed_by = operator_ref
        c.confirmed_at = datetime.now(timezone.utc)
        self._s.flush()

    def add_revision(
        self, chart_ref: str, draft_version: int, source_code: str, format_: str,
        change_origin: str, suggestion_ref: Optional[str], note: Optional[str],
        operator_ref: str, idempotency_key: Optional[str] = None,
    ) -> str:
        rev = ChartSourceRevision(
            chart_ref=_as_uuid(chart_ref), draft_version=draft_version,
            source_code=source_code, format=format_, change_origin=change_origin,
            suggestion_ref=_as_uuid(suggestion_ref), note=note,
            operator_ref=operator_ref, idempotency_key=idempotency_key,
        )
        self._s.add(rev)
        self._s.flush()
        return str(rev.id)

    def revisions_of(self, chart_ref: str) -> list[ChartRevisionRow]:
        cid = _as_uuid(chart_ref)
        if cid is None:
            return []
        rows = self._s.scalars(
            select(ChartSourceRevision)
            .where(ChartSourceRevision.chart_ref == cid)
            .order_by(ChartSourceRevision.draft_version.desc(), ChartSourceRevision.id.desc())
        ).all()
        return [
            ChartRevisionRow(
                id=str(r.id), chart_ref=str(r.chart_ref), draft_version=r.draft_version,
                source_code=r.source_code, format=r.format, change_origin=r.change_origin,
                suggestion_ref=str(r.suggestion_ref) if r.suggestion_ref else None,
                note=r.note, operator_ref=r.operator_ref,
                at=r.created_at.isoformat() if r.created_at else "",
            )
            for r in rows
        ]

    def find_revision_by_idempotency(self, key: str) -> Optional[str]:
        row = self._s.scalar(
            select(ChartSourceRevision.chart_ref).where(ChartSourceRevision.idempotency_key == key)
        )
        return str(row) if row else None


class SqlTraceLinkRepository:
    """LDM-013 追溯关系（写权威=追溯图谱模块，VAL-003）。"""

    def __init__(self, session: Session) -> None:
        self._s = session

    def _link_row(self, t: TraceLink) -> TraceLinkRow:
        return TraceLinkRow(
            id=str(t.id), project_ref=str(t.project_id), dimension=t.dimension,
            relation_type=t.relation_type,
            upstream_type=t.upstream_type, upstream_ref=str(t.upstream_ref),
            downstream_type=t.downstream_type, downstream_ref=str(t.downstream_ref),
            status=t.status, initial_basis=t.initial_basis,
            status_reason=t.status_reason, established_basis=t.established_basis,
            established_at=t.established_at.isoformat() if t.established_at else None,
            issue_ref=str(t.issue_ref) if t.issue_ref else None,
        )

    def create_link(
        self, project_ref: str, relation_type: str,
        upstream_type: str, upstream_ref: str,
        downstream_type: str, downstream_ref: str,
        status: str, initial_basis: str,
    ) -> str:
        link = TraceLink(
            project_id=_as_uuid(project_ref), dimension="spatial",
            relation_type=relation_type,
            upstream_type=upstream_type, upstream_ref=_as_uuid(upstream_ref),
            downstream_type=downstream_type, downstream_ref=_as_uuid(downstream_ref),
            status=status, initial_basis=initial_basis,
        )
        self._s.add(link)
        self._s.flush()
        return str(link.id)

    def find_link(
        self, upstream_ref: str, downstream_ref: str, relation_type: str,
    ) -> Optional[TraceLinkRow]:
        t = self._s.scalar(
            select(TraceLink).where(
                TraceLink.upstream_ref == _as_uuid(upstream_ref),
                TraceLink.downstream_ref == _as_uuid(downstream_ref),
                TraceLink.relation_type == relation_type,
            )
        )
        return self._link_row(t) if t else None

    def links_of_chart(self, chart_ref: str) -> list[TraceLinkRow]:
        cid = _as_uuid(chart_ref)
        if cid is None:
            return []
        rows = self._s.scalars(
            select(TraceLink)
            .where(TraceLink.downstream_type == "chart", TraceLink.downstream_ref == cid)
            .order_by(TraceLink.created_at, TraceLink.id)
        ).all()
        return [self._link_row(t) for t in rows]

    def promote_pre_established_supporting_basis(self, item_ref: str) -> int:
        """条目确认 → 其预建立支撑依据边转有效（P7 §1.2「随条目确认转有效」）。返回转有效条数。"""
        iid = _as_uuid(item_ref)
        if iid is None:
            return 0
        rows = self._s.scalars(select(TraceLink).where(
            TraceLink.downstream_type == "requirement_item",
            TraceLink.downstream_ref == iid,
            TraceLink.relation_type == "supporting_basis",
            TraceLink.status == "pre_established",
        )).all()
        for t in rows:
            t.status = "effective"
        return len(rows)

    def supporting_basis_upstream_refs(self, item_ref: str) -> list[str]:
        """条目的支撑依据（P7 评审「业务依据」段）上游业务知识要素 id，按建边序。"""
        iid = _as_uuid(item_ref)
        if iid is None:
            return []
        rows = self._s.scalars(
            select(TraceLink.upstream_ref)
            .where(
                TraceLink.downstream_type == "requirement_item",
                TraceLink.downstream_ref == iid,
                TraceLink.relation_type == "supporting_basis",
                TraceLink.upstream_type == "element",
            )
            .order_by(TraceLink.created_at, TraceLink.id)
        ).all()
        return [str(r) for r in rows]

    def links_of_project(
        self, project_ref: str, status: Optional[str] = None,
        chart_ref: Optional[str] = None,
    ) -> list[TraceLinkRow]:
        pid = _as_uuid(project_ref)
        if pid is None:
            return []
        stmt = select(TraceLink).where(TraceLink.project_id == pid)
        if status:
            stmt = stmt.where(TraceLink.status == status)
        if chart_ref:
            stmt = stmt.where(TraceLink.downstream_ref == _as_uuid(chart_ref))
        rows = self._s.scalars(stmt.order_by(TraceLink.created_at.desc(), TraceLink.id.desc())).all()
        return [self._link_row(t) for t in rows]

    def set_link_status(
        self, link_ref: str, status: str, status_reason: Optional[str] = None,
        established_basis: Optional[str] = None,
    ) -> None:
        t = self._s.get(TraceLink, _as_uuid(link_ref))
        if t is None:
            return
        t.status = status
        t.status_reason = status_reason
        if established_basis is not None:
            t.established_basis = established_basis
            t.established_at = datetime.now(timezone.utc)
        self._s.flush()

    def set_link_issue(self, link_ref: str, issue_ref: str) -> None:
        t = self._s.get(TraceLink, _as_uuid(link_ref))
        if t is None:
            return
        t.issue_ref = _as_uuid(issue_ref)
        self._s.flush()


class SqlIssueRepository:
    """LDM-011 问题项最小实现（创建 + 列表；处置闭环归 SCN-006）。"""

    def __init__(self, session: Session) -> None:
        self._s = session

    def _issue_row(self, i: Issue) -> IssueRow:
        return IssueRow(
            id=str(i.id), project_ref=str(i.project_id), issue_type=i.issue_type,
            status=i.status, title=i.title, description=i.description,
            origin_kind=i.origin_kind,
            chart_ref=str(i.chart_ref) if i.chart_ref else None,
            finding_ref=str(i.finding_ref) if i.finding_ref else None,
            trace_link_refs=i.trace_link_refs, created_by=i.created_by,
            created_at=i.created_at.isoformat() if i.created_at else "",
        )

    def create_issue(
        self, project_ref: str, issue_type: str, title: str, description: str,
        origin_kind: str, chart_ref: Optional[str], finding_ref: Optional[str],
        trace_link_refs_json: str, created_by: str, idempotency_key: str,
    ) -> str:
        issue = Issue(
            project_id=_as_uuid(project_ref), issue_type=issue_type,
            status="pending", title=title, description=description,
            origin_kind=origin_kind, chart_ref=_as_uuid(chart_ref),
            finding_ref=_as_uuid(finding_ref), trace_link_refs=trace_link_refs_json,
            created_by=created_by, idempotency_key=idempotency_key,
        )
        self._s.add(issue)
        self._s.flush()
        return str(issue.id)

    def get_issue(self, issue_ref: str) -> Optional[IssueRow]:
        i = self._s.get(Issue, _as_uuid(issue_ref))
        return self._issue_row(i) if i else None

    def issues_of_project(self, project_ref: str) -> list[IssueRow]:
        pid = _as_uuid(project_ref)
        if pid is None:
            return []
        rows = self._s.scalars(
            select(Issue).where(Issue.project_id == pid)
            .order_by(Issue.created_at.desc(), Issue.id.desc())
        ).all()
        return [self._issue_row(i) for i in rows]

    def find_issue_by_idempotency(self, key: str) -> Optional[str]:
        row = self._s.scalar(select(Issue.id).where(Issue.idempotency_key == key))
        return str(row) if row else None


class SqlChartProcessRepository:
    """图表过程记录：建议/核对请求上下文 + 核对轮次 + 发现项 + 幂等。"""

    def __init__(self, session: Session) -> None:
        self._s = session

    @property
    def session(self) -> Session:
        """供跨仓储写同事务记录（HK-2 读侧对账联查 AgentRun），与主事实同 commit。"""
        return self._s

    # ---- AI 源码建议请求 ----

    def find_suggestion_request_by_idempotency(self, key: str) -> Optional[str]:
        row = self._s.scalar(
            select(ChartSuggestionRequest.id).where(ChartSuggestionRequest.idempotency_key == key)
        )
        return str(row) if row else None

    def create_suggestion_request(
        self, project_ref: str, chart_ref: str, base_draft_version: int,
        intent: str, operator_ref: str, idempotency_key: str,
        kind: str = "revision",
    ) -> str:
        req = ChartSuggestionRequest(
            project_id=_as_uuid(project_ref), chart_ref=_as_uuid(chart_ref),
            base_draft_version=base_draft_version, intent=intent,
            request_kind=kind,
            operator_ref=operator_ref, idempotency_key=idempotency_key,
        )
        self._s.add(req)
        self._s.flush()
        return str(req.id)

    def _suggestion_request_row(self, r: ChartSuggestionRequest) -> ChartSuggestionRequestRow:
        return ChartSuggestionRequestRow(
            id=str(r.id), project_ref=str(r.project_id), chart_ref=str(r.chart_ref),
            base_draft_version=r.base_draft_version, intent=r.intent,
            operator_ref=r.operator_ref, stop_next_action=r.stop_next_action,
            created_at=r.created_at.isoformat() if r.created_at else "",
            kind=r.request_kind or "revision",
        )

    def get_suggestion_request(self, context_ref: str) -> Optional[ChartSuggestionRequestRow]:
        r = self._s.get(ChartSuggestionRequest, _as_uuid(context_ref))
        return self._suggestion_request_row(r) if r else None

    def suggestion_requests_of_chart(self, chart_ref: str) -> list[ChartSuggestionRequestRow]:
        cid = _as_uuid(chart_ref)
        if cid is None:
            return []
        rows = self._s.scalars(
            select(ChartSuggestionRequest)
            .where(ChartSuggestionRequest.chart_ref == cid)
            .order_by(ChartSuggestionRequest.created_at.desc(), ChartSuggestionRequest.id.desc())
        ).all()
        return [self._suggestion_request_row(r) for r in rows]

    def mark_suggestion_stopped(self, context_ref: str, next_action: str) -> None:
        r = self._s.get(ChartSuggestionRequest, _as_uuid(context_ref))
        if r is None:
            return
        r.stop_next_action = next_action
        self._s.flush()

    # ---- 核对请求 ----

    def find_verification_request_by_idempotency(self, key: str) -> Optional[str]:
        row = self._s.scalar(
            select(ChartVerificationRequest.id).where(ChartVerificationRequest.idempotency_key == key)
        )
        return str(row) if row else None

    def create_verification_request(
        self, project_ref: str, chart_ref: str, chart_draft_version: int,
        operator_ref: str, idempotency_key: str,
    ) -> str:
        req = ChartVerificationRequest(
            project_id=_as_uuid(project_ref), chart_ref=_as_uuid(chart_ref),
            chart_draft_version=chart_draft_version,
            operator_ref=operator_ref, idempotency_key=idempotency_key,
        )
        self._s.add(req)
        self._s.flush()
        return str(req.id)

    def get_verification_request(self, request_ref: str) -> Optional[ChartVerificationRequestRow]:
        r = self._s.get(ChartVerificationRequest, _as_uuid(request_ref))
        if r is None:
            return None
        return ChartVerificationRequestRow(
            id=str(r.id), project_ref=str(r.project_id), chart_ref=str(r.chart_ref),
            chart_draft_version=r.chart_draft_version,
            operator_ref=r.operator_ref, stop_next_action=r.stop_next_action,
        )

    def mark_verification_stopped(self, request_ref: str, next_action: str) -> None:
        r = self._s.get(ChartVerificationRequest, _as_uuid(request_ref))
        if r is None:
            return
        r.stop_next_action = next_action
        self._s.flush()

    # ---- 核对轮次 ----

    def _round_row(self, r: ChartVerificationRound) -> ChartVerificationRoundRow:
        return ChartVerificationRoundRow(
            id=str(r.id), chart_ref=str(r.chart_ref), request_ref=str(r.request_ref),
            round_no=r.round_no or 1, chart_draft_version=r.chart_draft_version,
            processing_status=r.processing_status,
            model_result_ref=str(r.model_result_ref) if r.model_result_ref else None,
            reason=r.reason, invalidated=bool(r.invalidated),
            invalidated_reason=r.invalidated_reason, confirm_result=r.confirm_result,
            created_at=r.created_at.isoformat() if r.created_at else "",
        )

    def create_round(
        self, chart_ref: str, request_ref: str, chart_draft_version: int,
    ) -> str:
        cid = _as_uuid(chart_ref)
        existing = self._s.scalars(
            select(ChartVerificationRound.id).where(ChartVerificationRound.chart_ref == cid)
        ).all()
        round_ = ChartVerificationRound(
            chart_ref=cid, request_ref=_as_uuid(request_ref),
            round_no=len(existing) + 1, chart_draft_version=chart_draft_version,
            processing_status="verifying",
        )
        self._s.add(round_)
        self._s.flush()
        return str(round_.id)

    def latest_round_of_chart(self, chart_ref: str) -> Optional[ChartVerificationRoundRow]:
        r = self._s.scalar(
            select(ChartVerificationRound)
            .where(ChartVerificationRound.chart_ref == _as_uuid(chart_ref))
            .order_by(ChartVerificationRound.round_no.desc())
            .limit(1)
        )
        return self._round_row(r) if r else None

    def round_of_request(self, request_ref: str) -> Optional[ChartVerificationRoundRow]:
        r = self._s.scalar(
            select(ChartVerificationRound)
            .where(ChartVerificationRound.request_ref == _as_uuid(request_ref))
            .order_by(ChartVerificationRound.round_no.desc())
            .limit(1)
        )
        return self._round_row(r) if r else None

    def get_round(self, round_ref: str) -> Optional[ChartVerificationRoundRow]:
        r = self._s.get(ChartVerificationRound, _as_uuid(round_ref))
        return self._round_row(r) if r else None

    def finish_round(
        self, round_ref: str, processing_status: str,
        model_result_ref: Optional[str] = None, reason: Optional[str] = None,
    ) -> None:
        r = self._s.get(ChartVerificationRound, _as_uuid(round_ref))
        if r is None:
            return
        r.processing_status = processing_status
        if model_result_ref is not None:
            r.model_result_ref = _as_uuid(model_result_ref)
        if reason is not None:
            r.reason = reason
        self._s.flush()

    def invalidate_rounds_of_chart(self, chart_ref: str, reason: str) -> None:
        rows = self._s.scalars(
            select(ChartVerificationRound).where(
                ChartVerificationRound.chart_ref == _as_uuid(chart_ref),
                ChartVerificationRound.invalidated.is_(False),
            )
        ).all()
        for r in rows:
            r.invalidated = True
            r.invalidated_reason = reason
        self._s.flush()

    def record_round_confirmation(
        self, round_ref: str, confirm_result: str, idempotency_key: str,
    ) -> None:
        r = self._s.get(ChartVerificationRound, _as_uuid(round_ref))
        if r is None:
            return
        r.confirm_result = confirm_result
        r.confirm_idempotency_key = idempotency_key
        self._s.flush()

    def find_round_confirmation_by_idempotency(self, key: str) -> Optional[str]:
        row = self._s.scalar(
            select(ChartVerificationRound.id).where(
                ChartVerificationRound.confirm_idempotency_key == key
            )
        )
        return str(row) if row else None

    # ---- 核对发现项与逐项复核 ----

    def _finding_row(self, f: ChartVerificationFinding) -> ChartFindingRow:
        return ChartFindingRow(
            id=str(f.id), round_ref=str(f.round_ref), chart_ref=str(f.chart_ref),
            finding_type=f.finding_type, summary=f.summary,
            basis_summary=f.basis_summary, related_source_refs=f.related_source_refs,
            model_result_ref=str(f.model_result_ref) if f.model_result_ref else None,
            decision=f.decision, decision_reason=f.decision_reason,
            decision_operator=f.decision_operator,
            decided_at=f.decided_at.isoformat() if f.decided_at else None,
            issue_ref=str(f.issue_ref) if f.issue_ref else None,
        )

    def add_finding(
        self, round_ref: str, chart_ref: str, finding_type: str,
        summary: str, basis_summary: str, related_source_refs_json: str,
        model_result_ref: Optional[str],
    ) -> str:
        f = ChartVerificationFinding(
            round_ref=_as_uuid(round_ref), chart_ref=_as_uuid(chart_ref),
            finding_type=finding_type, summary=summary, basis_summary=basis_summary,
            related_source_refs=related_source_refs_json,
            model_result_ref=_as_uuid(model_result_ref),
        )
        self._s.add(f)
        self._s.flush()
        return str(f.id)

    def findings_of_round(self, round_ref: str) -> list[ChartFindingRow]:
        rows = self._s.scalars(
            select(ChartVerificationFinding)
            .where(ChartVerificationFinding.round_ref == _as_uuid(round_ref))
            .order_by(ChartVerificationFinding.created_at, ChartVerificationFinding.id)
        ).all()
        return [self._finding_row(f) for f in rows]

    def get_finding(self, finding_ref: str) -> Optional[ChartFindingRow]:
        f = self._s.get(ChartVerificationFinding, _as_uuid(finding_ref))
        return self._finding_row(f) if f else None

    def record_finding_decision(
        self, finding_ref: str, decision: str, reason: Optional[str],
        operator_ref: str, idempotency_key: str,
    ) -> None:
        f = self._s.get(ChartVerificationFinding, _as_uuid(finding_ref))
        if f is None:
            return
        f.decision = decision
        f.decision_reason = reason
        f.decision_operator = operator_ref
        f.decided_at = datetime.now(timezone.utc)
        f.decision_idempotency_key = idempotency_key
        self._s.flush()

    def find_finding_decision_by_idempotency(self, key: str) -> Optional[str]:
        row = self._s.scalar(
            select(ChartVerificationFinding.id).where(
                ChartVerificationFinding.decision_idempotency_key == key
            )
        )
        return str(row) if row else None

    def set_finding_issue(self, finding_ref: str, issue_ref: str) -> None:
        f = self._s.get(ChartVerificationFinding, _as_uuid(finding_ref))
        if f is None:
            return
        f.issue_ref = _as_uuid(issue_ref)
        self._s.flush()


def _wire_sync_chart(
    session: Session,
    chart_suggester: Optional[ChartSourceSuggester] = None,
    chart_verifier: Optional[ChartVerifier] = None,
) -> tuple[ChartCollaborationService, ModelInferenceOrchestration]:
    """同步图表协同装配：service + 编排（prepare/result 回调已挂）。"""
    model_results = SqlModelResultRepository(session)
    process_records = SqlProcessRecordRepository(session)
    charts = SqlChartRepository(session)
    trace_links = SqlTraceLinkRepository(session)
    issues = SqlIssueRepository(session)
    chart_process = SqlChartProcessRepository(session)
    items = SqlRequirementItemRepository(session)
    orchestration = ModelInferenceOrchestration(
        process_records=process_records,
        model_results=model_results,
        chart_suggester=chart_suggester or StubChartSourceSuggester(),
        chart_verifier=chart_verifier or StubChartVerifier(),
    )
    service = ChartCollaborationService(
        model_orchestration=orchestration,
        model_results=model_results,
        charts=charts,
        trace_links=trace_links,
        issues=issues,
        chart_process=chart_process,
        items=items,
        source_assets=SqlSourceAssetRepository(session),
    )
    orchestration.on_chart_suggestion_prepare = service.prepare_chart_suggestion
    orchestration.on_chart_suggestion_result = service.accept_chart_suggestion_result
    orchestration.on_chart_verification_prepare = service.prepare_chart_verification
    orchestration.on_chart_verification_result = service.accept_chart_verification_result
    return service, orchestration


def build_sql_chart_service(
    session: Session,
    auto_complete: bool = True,
    chart_suggester: Optional[ChartSourceSuggester] = None,
    chart_verifier: Optional[ChartVerifier] = None,
) -> ChartCollaborationService:
    service, orchestration = _wire_sync_chart(session, chart_suggester, chart_verifier)
    if not auto_complete:
        orchestration.on_chart_suggestion_prepare = None
        orchestration.on_chart_suggestion_result = None
        orchestration.on_chart_verification_prepare = None
        orchestration.on_chart_verification_result = None
    return service


def run_chart_suggestion_judgement(
    session: Session, context_ref: str, chart_suggester: ChartSourceSuggester
) -> None:
    """worker/inline 内：图表源码建议送检（组装→AI→登记 LDM-015→承接停靠/待处理）。"""
    _, orchestration = _wire_sync_chart(session, chart_suggester=chart_suggester)
    orchestration.request_chart_suggestion(context_ref)


def run_chart_verification_judgement(
    session: Session, request_ref: str, chart_verifier: ChartVerifier
) -> None:
    """worker/inline 内：图文一致性核对送检（组装→AI→登记 LDM-015→承接写轮次/发现项）。"""
    _, orchestration = _wire_sync_chart(session, chart_verifier=chart_verifier)
    orchestration.request_chart_verification(request_ref)
