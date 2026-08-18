"""AEP-094 AI 效能按环节统计（模型推理结果仓储·统计读面）。

口径事实源：docs/40-detailed-design/shared/AI效能统计口径设计.md。
只读投影：统计单元=LDM-015 采纳结论明细（ldm015_adoption_record），可整层重建；
统计读取不改变任何处理状态（UINV-23）。跨聚合直查 ORM 表是纯读模型的合法形态
（同 overview_read.py / trace_read.py）。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

import json

from app.api.schemas import (
    AiCalibrationBucketRead,
    AiCalibrationRead,
    AiCoverageRead,
    AiDeliveryFailureInstanceRead,
    AiDeliveryFailureInstancesRead,
    AiDeliveryFailureRead,
    AiEffectivenessRead,
    AiFailureStageCountRead,
    AiRiskSignalRead,
    AiStageEffectRead,
)
from app.db.models import (
    AdoptionRecord,
    AgentRun,
    ChartSuggestionRequest,
    ChartVerificationRequest,
    IntakeRequest,
    ItemDiagnosisRequest,
    ItemFormationRequest,
    ModelResult,
    ParseRequest,
    Project,
    RequirementElement,
    RequirementItem,
)
from app.domain.errors import NotFound

STAGES = (
    "source_intake",
    "element_recognition",
    "element_review",
    "element_execution",
    "item_formation",
    "item_diagnosis",
    "chart_source_suggestion",
    "chart_verification",
)

_SETTLED = ("adopted", "adopted_with_revision", "rejected", "transferred_to_issue")

# 记录级 pending 计数：stage → 请求上下文表（applies_to_ref 指向；均带 project_id）
_STAGE_CONTEXT = {
    "source_intake": IntakeRequest,
    "element_recognition": ParseRequest,
    "element_review": ParseRequest,
    "element_execution": ParseRequest,
    "item_formation": ItemFormationRequest,
    "item_diagnosis": ItemDiagnosisRequest,
    "chart_source_suggestion": ChartSuggestionRequest,
    "chart_verification": ChartVerificationRequest,
}

# 交付失败块（口径设计 §5.5）：lane = LDM-015.stage；项目归属经各 lane 请求上下文表
# （applies_to_ref → 该表 project_id）过滤。复核环节 applies_to_ref = 条目 id（AEP-114）。
_DELIVERY_STAGE_CONTEXT = {
    **_STAGE_CONTEXT,
    "item_structure_recheck": RequirementItem,
}
# item_diagnoser 三段校验的摔倒点（诊断可靠性设计裁定 4）；缺失 → unclassified（未分关）。
_FAILURE_STAGES = ("parse", "llm_error", "structure", "aggregation", "synthesis")
_UNCLASSIFIED = "unclassified"


def _is_delivery_failed(judgement: Optional[str]) -> bool:
    """交付失败行判定（口径设计 §5.5）：稳定码以 _failed 结尾；
    element_recognition 的结果码域=recognized/no_elements/failed，失败为裸 failed。"""
    j = judgement or ""
    return j == "failed" or j.endswith("_failed")


def _as_uuid(value: str) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None


def _content_dict(content: Optional[str]) -> dict:
    """LDM-015 result_content JSON → dict；缺失/不可解析 → {}。"""
    if not content:
        return {}
    try:
        data = json.loads(content)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _failure_stage_of(content: Optional[str]) -> Optional[str]:
    """取 failure.stage；缺失/不可解析 → None（归未分关）。"""
    failure = _content_dict(content).get("failure")
    if isinstance(failure, dict) and isinstance(failure.get("stage"), str):
        return failure["stage"]
    return None


def _failure_detail_of(content: Optional[str]) -> Optional[str]:
    """取 failure.detail 白话失败详情（不含模型原文）。"""
    failure = _content_dict(content).get("failure")
    if isinstance(failure, dict) and isinstance(failure.get("detail"), str):
        return failure["detail"]
    return None


def _item_ref_of(content: Optional[str]) -> Optional[str]:
    """取受影响条目 item_ref（条目类 lane 失败载荷带）。"""
    ref = _content_dict(content).get("item_ref")
    return ref if isinstance(ref, str) else None


class AiEffectivenessService:
    """AEP-094：环节效果 / 置信度校准 / AI 覆盖 / 风险信号（窗口参数共享）。"""

    def __init__(self, session: Session) -> None:
        self._s = session

    def read(self, project_ref: str, window_days: int = 30) -> AiEffectivenessRead:
        pid = _as_uuid(project_ref)
        if pid is None or self._s.get(Project, pid) is None:
            raise NotFound(f"项目不存在：{project_ref}")
        now = datetime.now(timezone.utc)
        since = now - timedelta(days=window_days)
        prev_since = since - timedelta(days=window_days)

        details = self._details(pid, since)
        stages = self._stages(pid, since, details)
        calibration = self._calibration(details)
        coverage = self._coverage(pid)
        risk = self._risk_signals(pid, since, prev_since, details, calibration)
        delivery_failures = self._delivery_failures(pid, since)
        return AiEffectivenessRead(
            project_ref=project_ref, window_days=window_days,
            stages=stages, calibration=calibration, coverage=coverage, risk_signals=risk,
            delivery_failures=delivery_failures,
        )

    # ------------------------------------------------------------------

    def _details(self, pid: uuid.UUID, since: datetime) -> list[AdoptionRecord]:
        return list(self._s.scalars(
            select(AdoptionRecord).where(
                AdoptionRecord.project_id == pid, AdoptionRecord.created_at >= since,
            )
        ).all())

    def _stages(
        self, pid: uuid.UUID, since: datetime, details: list[AdoptionRecord]
    ) -> list[AiStageEffectRead]:
        rows: list[AiStageEffectRead] = []
        for stage in STAGES:
            of_stage = [d for d in details if d.stage == stage]
            counts = {o: sum(1 for d in of_stage if d.outcome == o) for o in _SETTLED}
            context_model = _STAGE_CONTEXT[stage]
            pending = int(self._s.scalar(
                select(func.count()).select_from(ModelResult).where(
                    ModelResult.stage == stage,
                    ModelResult.process_status == "pending",
                    ModelResult.created_at >= since,
                    ModelResult.applies_to_ref.in_(
                        select(context_model.id).where(context_model.project_id == pid)
                    ),
                )
            ) or 0)
            rows.append(AiStageEffectRead(
                stage=stage, total=sum(counts.values()), pending_records=pending,
                adopted=counts["adopted"],
                adopted_with_revision=counts["adopted_with_revision"],
                rejected=counts["rejected"],
                transferred_to_issue=counts["transferred_to_issue"],
            ))
        return rows

    def _calibration(self, details: list[AdoptionRecord]) -> AiCalibrationRead:
        # 样本 = 识别明细 ×（要素置信度, 采纳/拒绝结局）；superseded 不计入（口径 §5.2）
        samples: list[tuple[float, bool]] = []
        recog = [d for d in details
                 if d.stage == "element_recognition" and d.outcome in _SETTLED]
        if recog:
            refs = [d.subject_ref for d in recog]
            confidence = {
                str(e.id): e.confidence
                for e in self._s.scalars(
                    select(RequirementElement).where(RequirementElement.id.in_(refs))
                ).all()
                if e.confidence is not None
            }
            for d in recog:
                conf = confidence.get(str(d.subject_ref))
                if conf is None:
                    continue
                samples.append((float(conf), d.outcome in ("adopted", "adopted_with_revision")))

        n = len(samples)
        buckets: list[AiCalibrationBucketRead] = []
        ece: float | None = None
        if n > 0:
            grouped: dict[int, list[tuple[float, bool]]] = {}
            for conf, ok in samples:
                idx = min(int(conf * 10), 9)
                grouped.setdefault(idx, []).append((conf, ok))
            weighted = 0.0
            for idx in sorted(grouped):
                grp = grouped[idx]
                avg_conf = sum(c for c, _ in grp) / len(grp)
                accuracy = sum(1 for _, ok in grp if ok) / len(grp)
                weighted += abs(accuracy - avg_conf) * len(grp)
                buckets.append(AiCalibrationBucketRead(
                    range=f"{idx / 10:.1f}-{(idx + 1) / 10:.1f}",
                    avg_confidence=round(avg_conf, 4), accuracy=round(accuracy, 4), count=len(grp),
                ))
            ece = round(weighted / n, 4)

        if n < 20:
            rating = "insufficient"
        elif ece is not None and ece <= 0.05:
            rating = "excellent"
        elif ece is not None and ece <= 0.10:
            rating = "good"
        elif ece is not None and ece <= 0.20:
            rating = "fair"
        else:
            rating = "poor"
        return AiCalibrationRead(ece=ece, rating=rating, sample_size=n, buckets=buckets)

    def _coverage(self, pid: uuid.UUID) -> AiCoverageRead:
        items = self._s.scalars(
            select(RequirementItem).where(RequirementItem.project_id == pid)
        ).all()
        pipeline_ctx = {
            str(r) for r in self._s.scalars(
                select(ItemFormationRequest.id).where(ItemFormationRequest.project_id == pid)
            ).all()
        }
        # 触达口径不限窗口：条目在任一环节出现于采纳明细即触达
        touched_refs = {
            str(r) for r in self._s.scalars(
                select(AdoptionRecord.subject_ref).where(
                    AdoptionRecord.project_id == pid,
                    AdoptionRecord.subject_type == "requirement_item",
                )
            ).all()
        }
        touched = untouched = not_applicable = 0
        for item in items:
            if str(item.formation_context_ref) not in pipeline_ctx:
                not_applicable += 1  # 直写导入：无形成管线上下文
            elif str(item.id) in touched_refs:
                touched += 1
            else:
                untouched += 1
        return AiCoverageRead(
            touched=touched, untouched=untouched,
            not_applicable=not_applicable, total_items=len(items),
        )

    def _risk_signals(
        self, pid: uuid.UUID, since: datetime, prev_since: datetime,
        details: list[AdoptionRecord], calibration: AiCalibrationRead,
    ) -> list[AiRiskSignalRead]:
        settled = [d for d in details if d.outcome in _SETTLED]
        rejected = sum(1 for d in settled if d.outcome == "rejected")
        transferred = sum(1 for d in settled if d.outcome == "transferred_to_issue")

        # 低置信度集中：本窗识别校准样本中 confidence<0.6 占比（阈值暂定，口径 §5.4）
        low_conf = 0
        low_total = calibration.sample_size
        for b in calibration.buckets:
            if float(b.range.split("-")[0]) < 0.6:
                low_conf += b.count
        low_ratio = (low_conf / low_total) if low_total else 0.0
        low_level = "high" if low_ratio > 0.2 else "medium" if low_ratio > 0.1 else "low"

        # 拒绝率环比：本窗 vs 前一等长窗（百分点）
        prev = self._s.scalars(
            select(AdoptionRecord).where(
                AdoptionRecord.project_id == pid,
                AdoptionRecord.created_at >= prev_since,
                AdoptionRecord.created_at < since,
            )
        ).all()
        prev_settled = [d for d in prev if d.outcome in _SETTLED]
        rate = (rejected / len(settled)) if settled else 0.0
        if prev_settled:
            prev_rate = sum(1 for d in prev_settled if d.outcome == "rejected") / len(prev_settled)
            delta_pp = (rate - prev_rate) * 100
            reject_level = "high" if delta_pp > 15 else "medium" if delta_pp > 5 else "low"
        else:
            reject_level = "low"  # 无前窗基线：环比不成立，不报上升（冷启动不误报）

        transfer_ratio = (transferred / len(settled)) if settled else 0.0
        transfer_level = "high" if transfer_ratio > 0.08 else "medium" if transfer_ratio > 0.03 else "low"

        return [
            AiRiskSignalRead(key="low_confidence", level=low_level, value=low_conf),
            AiRiskSignalRead(key="rejection_rising", level=reject_level, value=rejected),
            AiRiskSignalRead(key="issue_conversion", level=transfer_level, value=transferred),
            # 来源冲突依赖 AEP-065（延期）：不显示虚构值
            AiRiskSignalRead(key="source_conflict", level="deferred", value=0),
        ]

    def _delivery_failures(
        self, pid: uuid.UUID, since: datetime
    ) -> list[AiDeliveryFailureRead]:
        """交付失败＝LDM-015 交付失败行（_is_delivery_failed，口径设计 §5.5）；按 lane × 失败关卡聚合。

        分母=该 lane 窗口内总判定行数；分子=交付失败行数；比率归前端。
        仅 item_diagnosis 写 failure.stage，其余 lane 失败行归「未分关」桶。
        只含 total>0 的 lane（无判定行的 lane 不占位）。
        """
        rows: list[AiDeliveryFailureRead] = []
        for stage, ctx in _DELIVERY_STAGE_CONTEXT.items():
            ctx_ids = select(ctx.id).where(ctx.project_id == pid)
            records = self._s.execute(
                select(ModelResult.judgement, ModelResult.result_content).where(
                    ModelResult.stage == stage,
                    ModelResult.created_at >= since,
                    ModelResult.applies_to_ref.in_(ctx_ids),
                )
            ).all()
            total = len(records)
            if total == 0:
                continue
            buckets = {fs: 0 for fs in _FAILURE_STAGES}
            unclassified = 0
            failed = 0
            for judgement, content in records:
                if not _is_delivery_failed(judgement):
                    continue
                failed += 1
                fstage = _failure_stage_of(content)
                if fstage in buckets:
                    buckets[fstage] += 1
                else:
                    unclassified += 1  # 缺 failure.stage 的失败行如实归未分关，不猜摔倒点
            by_stage = [
                AiFailureStageCountRead(failure_stage=fs, count=buckets[fs])
                for fs in _FAILURE_STAGES if buckets[fs]
            ]
            if unclassified:
                by_stage.append(
                    AiFailureStageCountRead(failure_stage=_UNCLASSIFIED, count=unclassified)
                )
            rows.append(AiDeliveryFailureRead(
                stage=stage, total=total, failed=failed, by_failure_stage=by_stage,
            ))
        return rows

    def delivery_failure_instances(
        self, project_ref: str, stage: str, failure_stage: Optional[str] = None,
        window_days: int = 30, limit: int = 50,
    ) -> AiDeliveryFailureInstancesRead:
        """交付失败个案钻取（口径 §5.5）：某 lane[×失败关卡] 的失败行明细，最近在前。

        只读；白话详情取 failure.detail 或 basis（不含模型原文）；best-effort 解析受影响
        条目编号与关联 AgentRun 状态（接入运行态·诊断中心重试/降级跟进）。
        """
        pid = _as_uuid(project_ref)
        if pid is None or self._s.get(Project, pid) is None:
            raise NotFound(f"项目不存在：{project_ref}")
        ctx = _DELIVERY_STAGE_CONTEXT.get(stage)
        if ctx is None:  # 未知 lane：空结果（不抛，前端给中性空态）
            return AiDeliveryFailureInstancesRead(
                stage=stage, failure_stage=failure_stage, window_days=window_days,
                total_failed=0, instances=[],
            )
        since = datetime.now(timezone.utc) - timedelta(days=window_days)
        ctx_ids = select(ctx.id).where(ctx.project_id == pid)
        rows = list(self._s.scalars(
            select(ModelResult).where(
                ModelResult.stage == stage,
                ModelResult.created_at >= since,
                ModelResult.applies_to_ref.in_(ctx_ids),
            ).order_by(ModelResult.created_at.desc())
        ).all())

        def bucket(r: ModelResult) -> str:
            fs = _failure_stage_of(r.result_content)
            return fs if fs in _FAILURE_STAGES else _UNCLASSIFIED

        failed = [r for r in rows if _is_delivery_failed(r.judgement)]
        if failure_stage is not None:
            failed = [r for r in failed if bucket(r) == failure_stage]
        total = len(failed)
        top = failed[:max(0, limit)]

        # best-effort：条目编号（条目类 lane 失败载荷带 item_ref）
        item_refs = {_item_ref_of(r.result_content) for r in top}
        item_refs.discard(None)
        req_no: dict[str, str] = {}
        if item_refs:
            req_no = {
                str(i.id): i.req_no
                for i in self._s.scalars(
                    select(RequirementItem).where(
                        RequirementItem.id.in_([_as_uuid(x) for x in item_refs])
                    )
                ).all()
            }
        # best-effort：关联 AgentRun 状态（kind==stage ∧ context_ref==applies_to_ref，取最近）
        ctx_refs = {r.applies_to_ref for r in top if r.applies_to_ref is not None}
        run_status: dict[str, str] = {}
        if ctx_refs:
            for run in self._s.scalars(
                select(AgentRun).where(
                    AgentRun.kind == stage, AgentRun.context_ref.in_(list(ctx_refs)),
                ).order_by(AgentRun.created_at.desc())
            ).all():
                run_status.setdefault(str(run.context_ref), run.status)

        instances = [
            AiDeliveryFailureInstanceRead(
                occurred_at=r.created_at.isoformat() if r.created_at else "",
                failure_stage=bucket(r),
                detail=(_failure_detail_of(r.result_content) or r.basis or "")[:400],
                subject_req_no=req_no.get(str(_item_ref_of(r.result_content) or "")),
                run_status=run_status.get(str(r.applies_to_ref)),
            )
            for r in top
        ]
        return AiDeliveryFailureInstancesRead(
            stage=stage, failure_stage=failure_stage, window_days=window_days,
            total_failed=total, instances=instances,
        )
