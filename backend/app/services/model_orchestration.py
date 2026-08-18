"""模型推理编排服务（实现 interfaces.ModelOrchestration）。

组织送检 → 调模型服务适配器判定/识别 → 登记 LDM-015 → 触发回交。
- 来源接入判断（AEP-003）：judge → 记 LDM-015（判定+依据）→ 回交材料接收服务 AEP-002。
- 知识项识别（AEP-004）：recognizer → 记 LDM-015（结果码+要素集 JSON+依据）→ 回交分析转化服务 AEP-022。

judge/recognizer 由装配注入：真实=Llm*，测试/无模型=Stub*。

A1：同步调用 LLM（POST 请求线程内完成）。A2：判定/识别挪到 Redis RQ worker + AgentRun，
request_* 只创建 AgentRun 并返回其引用。
"""
from __future__ import annotations

from typing import Callable, Optional

from sqlalchemy.orm import Session

import json

from app.adapters.llm import (
    ChartSourceSuggester,
    ChartVerifier,
    ElementOperationExecutor,
    ElementReviewer,
    ItemStructureRechecker,
    RecognitionResult,
    RequirementItemDiagnoser,
    RequirementItemFormatter,
    SourceElementRecognizer,
    SourceIntakeJudge,
    serialize_diagnosed_finding,
)
from app.domain.anchors import first_anchor_quote
from app.domain.enums import ELEMENT_TO_ITEM_TYPE
from app.interfaces.repositories import ItemReviewRepository
from app.interfaces import (
    ItemFormationProcessRepository,
    ModelResultRepository,
    ProcessRecordRepository,
    RecognizedElementRow,
    SourceAssetRepository,
)
from app.interfaces.services import AgentRuns
from app.log import log_event

_COMPONENT = "model-orchestration"


def _recognition_rows(result: RecognitionResult) -> tuple[str, list[RecognizedElementRow]]:
    """RecognitionResult(适配器) → (结果码, 写库行)。识别失败/空集 → 空行。"""
    if result.failed:
        return "failed", []
    rows = [
        RecognizedElementRow(
            element_type=el.element_type.value,
            content=el.content,
            source_anchor=el.source_anchor,
            confidence=el.confidence,
            model_verdict=el.verdict.value if el.verdict else None,
            verdict_reason=el.verdict_reason,
        )
        for el in result.elements
    ]
    return ("recognized" if rows else "no_elements"), rows


class ModelInferenceOrchestration:
    """A1 同步编排（接入判断 + 要素识别，实现 ModelOrchestration）。

    on_judgement / on_recognition 由装配挂为对应服务的回交回调；未设=仅派发。
    要素识别读原文经 source_assets（ParseRequest 只存 material_ref，不存原文）。
    """

    def __init__(
        self,
        process_records: ProcessRecordRepository,
        model_results: ModelResultRepository,
        judge: Optional[SourceIntakeJudge] = None,
        recognizer: Optional[SourceElementRecognizer] = None,
        source_assets: Optional[SourceAssetRepository] = None,
        reviewer: Optional[ElementReviewer] = None,
        executor: Optional[ElementOperationExecutor] = None,
        item_formatter: Optional[RequirementItemFormatter] = None,
        item_formation_process: Optional[ItemFormationProcessRepository] = None,
        item_diagnoser: Optional[RequirementItemDiagnoser] = None,
        item_reviews: Optional[ItemReviewRepository] = None,
        item_rechecker: Optional[ItemStructureRechecker] = None,
        chart_suggester: Optional[ChartSourceSuggester] = None,
        chart_verifier: Optional[ChartVerifier] = None,
        commit_each: Optional[Callable[[], None]] = None,
    ) -> None:
        self._process_records = process_records
        self._model_results = model_results
        self._judge = judge
        self._recognizer = recognizer
        self._source_assets = source_assets
        self._reviewer = reviewer
        self._executor = executor
        self._item_formatter = item_formatter
        self._item_formation_process = item_formation_process
        self._item_diagnoser = item_diagnoser
        self._item_reviews = item_reviews
        self._item_rechecker = item_rechecker
        self._chart_suggester = chart_suggester
        self._chart_verifier = chart_verifier
        # 逐条目提交回调（SCN-003-P01-N13 实时呈现：worker 内每承接一个条目结果就提交一次）
        self._commit_each = commit_each
        self._seq = 0
        self.dispatched: list[str] = []
        self.on_judgement: Optional[Callable[[str, str], None]] = None
        self.on_recognition: Optional[Callable[[str, str], None]] = None
        self.on_review: Optional[Callable[[str, str], None]] = None
        self.on_execution: Optional[Callable[[str, str], None]] = None
        # (formation_context_ref, element_ref, model_result_ref|None) -> None 逐要素结果承接
        # （model_result_ref=None 表示要素已不在集合，按 skipped 归因）
        self.on_item_formation_element: Optional[Callable[[str, str, Optional[str]], None]] = None
        # 批次收束回调（版本推进 + 失败停靠裁定）
        self.on_item_formation_completed: Optional[Callable[[str], None]] = None
        # (batch_ref, item_ref) -> Optional[dict] 诊断执行上下文（None=该条目不能送检）
        self.on_item_diagnosis_prepare: Optional[Callable[[str, str], Optional[dict]]] = None
        # (batch_ref, item_ref, model_result_ref) -> None 结果承接（N10–N12）
        self.on_item_diagnosis_result: Optional[Callable[[str, str, str], None]] = None
        # AEP-114：(recheck_ref, item_ref) -> Optional[dict] 复核上下文（None=该条目跳过）
        self.on_item_recheck_prepare: Optional[Callable[[str, str], Optional[dict]]] = None
        # (recheck_ref, item_ref, model_result_ref) -> None 复核结果承接（只刷新投影）
        self.on_item_recheck_result: Optional[Callable[[str, str, str], None]] = None
        # SCN-004：context_ref -> Optional[dict] 图表建议/核对送检上下文（None=不能送检）
        self.on_chart_suggestion_prepare: Optional[Callable[[str], Optional[dict]]] = None
        self.on_chart_suggestion_result: Optional[Callable[[str, str], None]] = None
        self.on_chart_verification_prepare: Optional[Callable[[str], Optional[dict]]] = None
        self.on_chart_verification_result: Optional[Callable[[str, str], None]] = None

    def request_source_intake_judgement(self, context_ref: str) -> str:
        self._seq += 1
        run = f"AR-{self._seq}"
        self.dispatched.append(context_ref)
        if self.on_judgement is not None and self._judge is not None:  # A1 同步判定+回交
            content = self._process_records.read_request_content(context_ref)
            if content is not None:
                result = self._judge.judge(content.project_ref, content.raw_text, content.source_note)
                model_result_ref = self._model_results.record_intake_judgement(
                    result.judgement, context_ref, result.basis
                )
                self.on_judgement(context_ref, model_result_ref)
        return run

    def request_element_recognition(self, context_ref: str) -> str:
        self._seq += 1
        run = f"AR-{self._seq}"
        self.dispatched.append(context_ref)
        if (
            self.on_recognition is not None
            and self._recognizer is not None
            and self._source_assets is not None
        ):  # A1 同步识别+回交
            material_ref = self._process_records.read_parse_material_ref(context_ref)
            content = self._source_assets.read_material_content(material_ref) if material_ref else None
            if content is not None:
                from app.domain.domain_profiles import get_domain_profile
                result = self._recognizer.recognize(
                    content.project_ref,
                    self._material_text(content.raw_text, material_ref),
                    content.source_note,
                    project_scope=content.project_scope,  # P6a 领域上下文注入
                    project_background=content.project_background,
                    domain_profile=get_domain_profile(content.domain_profile_key),  # P6b 领域档案
                )
                result_code, rows = _recognition_rows(result)
                model_result_ref = self._model_results.record_element_recognition(
                    context_ref, result_code, rows, result.basis
                )
                self.on_recognition(context_ref, model_result_ref)
        return run

    # --- 复核/执行公共：读操作上下文 + 材料 + 当前要素集 ---

    def _material_text(self, raw_text: str, material_ref: Optional[str]) -> str:
        """提示词语料 = 原文 + 补入来源块。补块是人工登记的补充事实来源，
        与原文同等地位；不拼入则模型无法引用补入事实（先补入再修订的闭环会断）。"""
        if not material_ref or self._source_assets is None:
            return raw_text
        supplements_of = getattr(self._source_assets, "supplements_of", None)
        if supplements_of is None:
            return raw_text
        supplements = supplements_of(material_ref)
        if not supplements:
            return raw_text
        blocks = "\n".join(f"- {s.content}（依据：{s.basis}）" for s in supplements)
        return f"{raw_text}\n\n【补入来源块】（人工登记的补充事实来源，与原文同等地位）：\n{blocks}"

    def _operation_inputs(self, operation_ref: str):
        op = self._process_records.read_element_operation(operation_ref)
        if op is None or self._source_assets is None:
            return None
        material_ref = self._process_records.read_parse_material_ref(op.parse_context_ref)
        content = self._source_assets.read_material_content(material_ref) if material_ref else None
        if content is None:
            return None
        parse_result_ref = self._source_assets.parse_result_of(op.parse_context_ref)
        elements = self._source_assets.elements_of(parse_result_ref) if parse_result_ref else []
        element_dicts = []
        for e in elements:
            if e.superseded:
                continue
            quote = first_anchor_quote(e.source_anchor)
            element_dicts.append({
                "id": e.id, "element_type": e.element_type,
                "content": e.content, "source_quote": quote,
                "process_status": e.process_status,
                "model_verdict": e.model_verdict,
                # 人工对模型「建议剔除」裁定的处置标记（冷审查裁定 C4）：不带上它，一条刚被人工
                # 撤回到正常列表的知识项送去复核时，复核侧看到的仍只有「建议剔除」，会照旧判不可通过，
                # 人工纠正模型误杀的裁定在复核链路上等于没发生。
                "noise_triage": e.noise_triage,
                "revision_draft": e.revision_draft,
            })
        payload = json.loads(op.payload)
        quotes = [
            r.get("exact") or content.raw_text[r.get("start", 0):r.get("end", 0)]
            for r in payload.get("selected_text_ranges", [])
        ]
        source_text = self._material_text(content.raw_text, material_ref)
        return op, content, element_dicts, payload, [q for q in quotes if q], source_text

    def request_element_review(self, operation_ref: str) -> str:
        """AEP-005：复核送检（核要素结论 / 扫原文补漏）→ 登记 LDM-015 → 回交 AEP-024。"""
        self._seq += 1
        run = f"AR-{self._seq}"
        self.dispatched.append(operation_ref)
        if self.on_review is not None and self._reviewer is not None:
            inputs = self._operation_inputs(operation_ref)
            if inputs is not None:
                op, content, element_dicts, payload, quotes, source_text = inputs
                target_refs = list(payload.get("target_element_refs", []))
                intent = payload.get("review_intent", "")
                body: dict = {
                    "workspace_version": payload.get("workspace_version"),
                    "operation_ref": operation_ref,
                }
                if target_refs:  # 核要素：逐条结论
                    targets = [e for e in element_dicts if e["id"] in set(target_refs)]
                    outcome = self._reviewer.review_elements(
                        content.project_ref, source_text, content.source_note,
                        targets, intent,
                    )
                    body["mode"] = "elements"
                    if outcome.failed:
                        code = "review_failed"
                        body["findings"] = []
                    else:
                        code = "reviewed"
                        body["findings"] = [
                            {
                                "element_ref": f.element_ref,
                                "conclusion": f.conclusion.value,
                                "opinion": f.opinion,
                                "revised_content": f.revised_content,
                                # 完备性判定（设计增补 §2；无判据类型为空/None）
                                "correctness": f.correctness,
                                "completeness": f.completeness,
                                "rubric_version": f.rubric_version,
                                "facet_findings": [
                                    {
                                        "facet": ff.facet,
                                        "status": ff.status,
                                        "evidence": ff.evidence,
                                        "note": ff.note,
                                    }
                                    for ff in f.facets
                                ],
                            }
                            for f in outcome.findings
                        ]
                    basis = outcome.basis
                else:  # 扫原文补漏：产物为新「待确认」要素（要素清单瘦身：只给覆盖判定所需字段）
                    slim_elements = [
                        {"id": e["id"], "content": e["content"], "source_quote": e["source_quote"]}
                        for e in element_dicts
                    ]
                    scan = self._reviewer.scan_missing(
                        content.project_ref, source_text, content.source_note,
                        slim_elements, quotes, intent,
                    )
                    body["mode"] = "scan"
                    if scan.failed:
                        code = "review_failed"
                        body["items"] = []
                    else:
                        code = "reviewed"
                        body["items"] = [
                            {
                                "element_type": i.element_type.value,
                                "content": i.content,
                                "source_quote": i.source_quote,
                                "confidence": i.confidence,
                            }
                            for i in scan.items
                        ]
                    basis = scan.basis
                model_result_ref = self._model_results.record_stage_payload(
                    "element_review", op.parse_context_ref, code,
                    json.dumps(body, ensure_ascii=False), basis,
                )
                self.on_review(operation_ref, model_result_ref)
        return run

    def request_item_formation(self, formation_context_ref: str) -> str:
        """AEP-007：条目格式化送检（批次内逐要素执行、逐要素归因、逐要素回交）。

        每个要素：单独调 AI 格式化 → 登记格式化类 LDM-015 → 结果承接（服务回调，
        写 LDM-007）。单要素结果不等待同批次其它要素；批次收束后统一回调 completed。
        """
        self._seq += 1
        run = f"AR-{self._seq}"
        self.dispatched.append(formation_context_ref)
        if (
            self.on_item_formation_element is not None
            and self._item_formatter is not None
            and self._item_formation_process is not None
            and self._source_assets is not None
        ):
            req = self._item_formation_process.get_formation_request(formation_context_ref)
            if req is not None:
                material_ref = self._process_records.read_parse_material_ref(req.parse_context_ref)
                content = (
                    self._source_assets.read_material_content(material_ref) if material_ref else None
                )
                raw_text = content.raw_text if content else ""
                for ref in json.loads(req.target_refs or "[]"):
                    e = self._source_assets.get_element(ref)
                    if e is None:  # 要素已不在集合：交服务按 skipped 归因，不调 AI
                        self.on_item_formation_element(formation_context_ref, ref, None)
                        if self._commit_each is not None:
                            self._commit_each()
                        continue
                    quote = first_anchor_quote(e.source_anchor)
                    element_dict = {
                        "id": e.id, "element_type": e.element_type,
                        "content": e.content, "source_quote": quote,
                        # 目标条目类型 = 领域确定性映射（档案注入依据，不采信模型）
                        "req_type": ELEMENT_TO_ITEM_TYPE.get(e.element_type),
                    }
                    # 本批次固定的规约方案随批次传入（发起时读取一次，执行期不再回读配置）。
                    result = self._item_formatter.format_items(
                        req.project_ref, raw_text, [element_dict], req.convention_key
                    )
                    if result.failed:
                        code, items = "formation_failed", []
                    else:
                        items = [
                            {
                                "element_ref": it.element_ref,
                                "expression": it.expression,
                                "suggestion": it.suggestion,
                                "suggestion_reason": it.suggestion_reason,
                                # 档案结构判定（增补 §2 result_content；无档案/解析降级时为空）
                                "req_type": it.req_type,
                                "profile_version": it.profile_version,
                                # 判定所依据的规约方案（口径锚，与 profile_version 并列；选型文档 §5）
                                "convention_key": it.convention_key or req.convention_key,
                                "statement_conformance": it.statement_conformance,
                                "completeness": it.completeness,
                                "facet_findings": [
                                    {"facet": f.facet, "status": f.status,
                                     "evidence": f.evidence, "note": f.note}
                                    for f in it.facets
                                ],
                                "payload_values": [
                                    {"field": key, "value": value}
                                    for key, value in it.payload_values
                                ],
                                "curation_note": it.curation_note,
                                "boundary_note": it.boundary_note,
                                "verification_note": it.verification_note,
                                "verification_method": list(it.verification_method),
                            }
                            for it in result.items
                        ]
                        code = "formatted" if items else "formation_failed"
                    model_result_ref = self._model_results.record_stage_payload(
                        "item_formation", formation_context_ref, code,
                        json.dumps({
                            "formation_context_ref": formation_context_ref,
                            "element_ref": ref,
                            "items": items,
                        }, ensure_ascii=False),
                        result.basis,
                    )
                    self.on_item_formation_element(formation_context_ref, ref, model_result_ref)
                    if self._commit_each is not None:  # 逐要素落库，界面按条目实时刷新
                        self._commit_each()
                if self.on_item_formation_completed is not None:
                    self.on_item_formation_completed(formation_context_ref)
                    if self._commit_each is not None:
                        self._commit_each()
        return run

    def _diagnosis_wired(self) -> bool:
        """诊断编排回调/依赖是否齐备（未装配=纯派发形态，不执行）。"""
        return (
            self.on_item_diagnosis_prepare is not None
            and self.on_item_diagnosis_result is not None
            and self._item_diagnoser is not None
            and self._item_reviews is not None
        )

    def _batch_item_refs(self, batch_ref: str) -> list[str]:
        batch = self._item_reviews.get_batch(batch_ref) if self._item_reviews else None
        return list(json.loads(batch.item_refs or "[]")) if batch is not None else []

    def _pending_diagnosis_items(self, batch_ref: str) -> list[str]:
        """批次内仍有进行中诊断轮次的条目（已收束/未能诊断的条目不在其列）。

        「待诊断」谓词 = 该条目在本批次存在 diagnosing 轮次；结果承接 / 准入拒收都会
        令轮次转终态，故已处理条目自然出列。逐条目子 job 与增量重诊据此推进游标。
        """
        if not self._diagnosis_wired():
            return []
        return [
            ref for ref in self._batch_item_refs(batch_ref)
            if self._item_reviews.running_round_of(batch_ref, ref) is not None
        ]

    def _diagnose_one_item(self, batch_ref: str, item_ref: str) -> None:
        """单条目诊断执行体（准入→AI→登记 LDM-015→结果承接）；逐条目 commit。

        批次循环与逐条目子 job 共用同一执行体（单一来源）：单条目结果不等待其它条目。
        """
        context = self.on_item_diagnosis_prepare(batch_ref, item_ref)
        if context is None:  # 该条目未能进行诊断（原因已由服务记录，轮次已转终态）
            if self._commit_each is not None:
                self._commit_each()
            return
        result = self._item_diagnoser.diagnose(
            context["project_ref"], context["diagnosis_mode"],
            context["item"], context["sources"], context["raw_text"],
            context["revisions"], context["prior_findings"],
            excluded_points=context.get("excluded_points") or [],
            thread_context=context.get("thread_context") or "",
            business_sources=context.get("business_sources") or [],
            attestation=context.get("attestation"),  # 人工确认背书（可空）
        )
        # 失败分关落账（诊断可靠性设计裁定 4）：stage+白话 detail 进 result_content，
        # 事后可定位摔在哪一关；禁落模型原文（AGENTS.md 硬规 8）。单条目失败只令该条目
        # diagnosis_failed 落库，批次 run 不因单条夭折（拆逐条目子 job 后语义不变）。
        if result.failed:
            body = {
                "item_ref": item_ref,
                "failure": {
                    "stage": result.failure_stage,
                    "detail": result.basis,
                },
            }
            model_result_ref = self._model_results.record_stage_payload(
                "item_diagnosis", batch_ref, "diagnosis_failed",
                json.dumps(body, ensure_ascii=False), result.basis,
            )
            self.on_item_diagnosis_result(batch_ref, item_ref, model_result_ref)
            if self._commit_each is not None:
                self._commit_each()
            return
        # v5 结论对象契约：状态字 + 证据发现项 + 修订点/缺口（服务端承接时再守卫）
        # v2 质量诊断器 additive：finding 增旁路字段 + 顶层质量画像/EARS/逐源对齐分
        body = {
            "item_ref": item_ref,
            "verdict": {
                "verdict_kind": result.verdict_kind,
                "verdict_summary": result.verdict_summary,
                "findings": [serialize_diagnosed_finding(f) for f in result.findings],
                "revision_points": list(result.revision_points),
                "supplement_gaps": list(result.supplement_gaps),
                "quality_profile": result.quality_profile,
                "ears_rewrite": result.ears_rewrite,
                "source_alignments": result.source_alignments,
            },
        }
        model_result_ref = self._model_results.record_stage_payload(
            "item_diagnosis", batch_ref, "diagnosed",
            json.dumps(body, ensure_ascii=False), result.basis,
        )
        self.on_item_diagnosis_result(batch_ref, item_ref, model_result_ref)
        if self._commit_each is not None:  # 逐条目落库，界面按条目实时刷新
            self._commit_each()

    def request_item_diagnosis(self, batch_ref: str) -> str:
        """SCN-003-P01-N08：条目诊断送检（批次内逐条目执行、逐条目归因、逐条目回交）。

        同步/inline 形态：批次内逐条目就地循环执行（每条目独立执行体）。
        rq 形态的逐条目子 job 调度见 diagnose_next_pending_item（worker 侧链式再入队）。
        """
        self._seq += 1
        run = f"AR-{self._seq}"
        self.dispatched.append(batch_ref)
        if self._diagnosis_wired():
            for item_ref in self._batch_item_refs(batch_ref):
                self._diagnose_one_item(batch_ref, item_ref)
        return run

    def diagnose_next_pending_item(self, batch_ref: str) -> bool:
        """逐条目子 job 游标推进：处理下一个待诊断条目并 commit，返回处理后批次内是否仍
        有待诊断条目（用于 worker 决定链式再入队；增量重诊得以在同队列 FIFO 交错插入）。

        入口无待诊断条目（本次未处理任何条目，如批次已收束）亦返回 False。
        """
        if not self._diagnosis_wired():
            return False
        pending = self._pending_diagnosis_items(batch_ref)
        if not pending:
            return False
        self._diagnose_one_item(batch_ref, pending[0])
        # 处理后重算剩余：末条目处理完即 False（无空转子 job），否则 True 触发再入队
        return bool(self._pending_diagnosis_items(batch_ref))

    def request_item_structure_recheck(self, recheck_context_ref: str) -> str:
        """AEP-114：条目结构复核送检（批次内逐条目执行、逐条目归因、逐条目回交）。

        每个条目：准入复核+上下文组装（服务回调）→ 调 AI（只判不改）→ 登记复核类
        LDM-015 → 结果承接（服务回调，重写达标投影并锚定当前内容修订序号）。
        失败仅登记失败类 LDM-015：旧投影原样保留，不阻断任何流程（A4）。
        recheck_context_ref = 批次受理信封（item_structure_recheck stage 的 LDM-015 过程记录）。
        """
        self._seq += 1
        run = f"AR-{self._seq}"
        self.dispatched.append(recheck_context_ref)
        if (
            self.on_item_recheck_prepare is not None
            and self.on_item_recheck_result is not None
            and self._item_rechecker is not None
        ):
            envelope = self._model_results.read_stage_payload(recheck_context_ref)
            if envelope is not None:
                body = json.loads(envelope.payload) if envelope.payload else {}
                for item_ref in body.get("item_refs", []):
                    context = self.on_item_recheck_prepare(recheck_context_ref, str(item_ref))
                    if context is None:  # 条目已离开待确认/不在集合：跳过（原因已由服务记录）
                        if self._commit_each is not None:
                            self._commit_each()
                        continue
                    result = self._item_rechecker.recheck(
                        context["project_ref"], context["raw_text"],
                        context["item"], context["sources"], context["convention_key"],
                    )
                    if result.failed:
                        code, review = "recheck_failed", None
                    else:
                        code = "rechecked"
                        review = {
                            "profile_version": result.profile_version,
                            "convention_key": context["convention_key"],
                            "statement_conformance": result.statement_conformance,
                            "completeness": result.completeness,
                            "facet_findings": [
                                {"facet": f.facet, "status": f.status,
                                 "evidence": f.evidence, "note": f.note}
                                for f in result.facets
                            ],
                            "payload_values": [
                                {"field": key, "value": value}
                                for key, value in result.payload_values
                            ],
                        }
                    model_result_ref = self._model_results.record_stage_payload(
                        "item_structure_recheck", str(item_ref), code,
                        json.dumps({
                            "recheck_context_ref": recheck_context_ref,
                            "item_ref": str(item_ref),
                            "review": review,
                        }, ensure_ascii=False),
                        result.basis,
                    )
                    self.on_item_recheck_result(recheck_context_ref, str(item_ref), model_result_ref)
                    if self._commit_each is not None:  # 逐条目落库，界面徽标实时刷新
                        self._commit_each()
        return run

    def request_chart_suggestion(self, context_ref: str) -> str:
        """SCN-004-P01-N08：图表源码建议送检 → 登记图表源码建议类 LDM-015 → 回交图表协同服务。"""
        self._seq += 1
        run = f"AR-{self._seq}"
        self.dispatched.append(context_ref)
        if (
            self.on_chart_suggestion_prepare is not None
            and self.on_chart_suggestion_result is not None
            and self._chart_suggester is not None
        ):
            context = self.on_chart_suggestion_prepare(context_ref)
            if context is not None:
                result = self._chart_suggester.suggest(
                    context["project_ref"], context["chart"], context["sources"],
                    context["current_source"], context["intent"],
                )
                if result.failed or result.proposal is None:
                    code, body = "suggestion_failed", {"context_ref": context_ref}
                else:
                    code = "suggested"
                    body = {
                        "context_ref": context_ref,
                        "source_code": result.proposal.source_code,
                        "explanation": result.proposal.explanation,
                        "title": result.proposal.title,
                        "base_draft_version": context["chart"].get("draft_version"),
                    }
                model_result_ref = self._model_results.record_stage_payload(
                    "chart_source_suggestion", context_ref, code,
                    json.dumps(body, ensure_ascii=False), result.basis,
                )
                self.on_chart_suggestion_result(context_ref, model_result_ref)
        return run

    def request_chart_verification(self, request_ref: str) -> str:
        """SCN-004-P02-N03/N04：图文一致性核对送检 → 登记图文核对类 LDM-015 → 回交图表协同服务。"""
        self._seq += 1
        run = f"AR-{self._seq}"
        self.dispatched.append(request_ref)
        if (
            self.on_chart_verification_prepare is not None
            and self.on_chart_verification_result is not None
            and self._chart_verifier is not None
        ):
            context = self.on_chart_verification_prepare(request_ref)
            if context is not None:
                result = self._chart_verifier.verify(
                    context["project_ref"], context["chart"], context["sources"],
                    context["trace_links"],
                )
                body = {
                    "request_ref": request_ref,
                    "findings": [
                        {
                            "finding_type": f.finding_type,
                            "summary": f.summary,
                            "basis_summary": f.basis_summary,
                            "related_source_refs": list(f.related_source_refs),
                        }
                        for f in result.findings
                    ],
                }
                code = "verification_failed" if result.failed else "verified"
                model_result_ref = self._model_results.record_stage_payload(
                    "chart_verification", request_ref, code,
                    json.dumps(body, ensure_ascii=False), result.basis,
                )
                self.on_chart_verification_result(request_ref, model_result_ref)
        return run

    def request_element_execution(self, operation_ref: str) -> str:
        """AEP-006：指定操作 AI 执行/修订迭代送检 → 登记执行类 LDM-015 → 回交 AEP-028。"""
        self._seq += 1
        run = f"AR-{self._seq}"
        self.dispatched.append(operation_ref)
        if self.on_execution is not None and self._executor is not None:
            inputs = self._operation_inputs(operation_ref)
            if inputs is not None:
                op, content, element_dicts, payload, quotes, source_text = inputs
                target_ids = set(payload.get("target_element_refs", []))
                targets = [dict(e) for e in element_dicts if e["id"] in target_ids]
                # 修订稿以显式槽位下发（content 保持正式表达）；提示词约定在修订稿基础上迭代
                current_draft = ""
                if op.kind == "revision":
                    current_draft = next(
                        (t.get("revision_draft") or "" for t in targets if t.get("revision_draft")), "",
                    )
                result = self._executor.execute(
                    content.project_ref, source_text,
                    payload.get("operation_type", ""),
                    payload.get("execution_instruction", ""),
                    targets, quotes,
                    current_draft=current_draft,
                )
                if result.failed:
                    code, after = "execution_failed", []
                else:
                    after = [
                        {
                            "element_type": item.element_type.value,
                            "content": item.content,
                            "source_quote": item.source_anchor,
                            "confidence": item.confidence,
                        }
                        for item in result.after_items
                    ]
                    code = "executed" if after else "execution_failed"
                model_result_ref = self._model_results.record_stage_payload(
                    "element_execution", op.parse_context_ref, code,
                    json.dumps({
                        "workspace_version": payload.get("workspace_version"),
                        "operation_ref": operation_ref,
                        "after_items": after,
                    }, ensure_ascii=False),
                    result.basis,
                )
                self.on_execution(operation_ref, model_result_ref)
        return run


class QueuedModelOrchestration:
    """A2 异步编排（实现 ModelOrchestration）。

    请求侧只登记 AgentRun 并入队；真正的判定/识别跑在 worker（见 app/workers/tasks.py）。
    入队前先提交事务，保证上下文 + AgentRun 已落库、worker 可读。
    单个实例的 enqueue 目标由装配决定（接入→run_source_intake；识别→run_element_recognition）。
    """

    def __init__(
        self,
        session: Session,
        agent_runs: AgentRuns,
        enqueue: Callable[[str, str], None],
        enqueue_review: Optional[Callable[[str, str], None]] = None,
        enqueue_execution: Optional[Callable[[str, str], None]] = None,
        enqueue_item_formation: Optional[Callable[[str, str], None]] = None,
        enqueue_item_diagnosis: Optional[Callable[[str, str], None]] = None,
        enqueue_item_structure_recheck: Optional[Callable[[str, str], None]] = None,
        enqueue_chart_suggestion: Optional[Callable[[str, str], None]] = None,
        enqueue_chart_verification: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self._session = session
        self._agent_runs = agent_runs
        self._enqueue = enqueue
        self._enqueue_review = enqueue_review or enqueue
        self._enqueue_execution = enqueue_execution or enqueue
        self._enqueue_item_formation = enqueue_item_formation or enqueue
        self._enqueue_item_diagnosis = enqueue_item_diagnosis or enqueue
        self._enqueue_item_structure_recheck = enqueue_item_structure_recheck or enqueue
        self._enqueue_chart_suggestion = enqueue_chart_suggestion or enqueue
        self._enqueue_chart_verification = enqueue_chart_verification or enqueue

    # ------------------------------------------------------------------
    # 派发单一来源（create → commit → enqueue；enqueue 抛异常补偿）
    # ------------------------------------------------------------------

    def _dispatch(
        self, kind: str, context_ref: str, enqueue: Callable[[str, str], None]
    ) -> str:
        """登记 AgentRun → 提交（落库后再入队，worker 方可读）→ 入队；enqueue 抛异常时补偿。

        commit 先于 enqueue 的顺序**不得翻转**（inline 后台线程与 rq worker 都必须先见已提交
        行；B1 审查 C4 可见性论证在案）。K1 幻影 queued 批修复（issue #12）：enqueue 同步抛
        异常（redis 抖动等）→ AgentRun 滞留 queued，被 run_liveness 判活、堵死 in_flight 修复
        通道约 60 分钟。补偿=置 run failed（白话原因）+ 独立 commit，令判活口径立即判死、
        修复通道解堵。enqueue 表面成功但 job 丢失的悬轮不在此列（属 HK-2 对账器职责，见
        幂等普查表 §6 挂账）。
        """
        run_id = self._agent_runs.create(kind, context_ref)
        self._session.commit()  # 落库后再入队，避免 worker 读不到（顺序不得翻转）
        try:
            enqueue(context_ref, run_id)
        except Exception as exc:  # noqa: BLE001 入队失败必须补偿，否则遗留幻影 queued 批
            self._compensate_failed_dispatch(run_id, kind, exc)
        return run_id

    def _compensate_failed_dispatch(self, run_id: str, kind: str, exc: Exception) -> None:
        """入队失败补偿：置 run failed + 独立 commit（结构化日志三点：失败/置败/解堵）。"""
        log_event(_COMPONENT, "async.enqueue.failed", level="ERROR", ok=False,
                  run_id=run_id, kind=kind, error_code=type(exc).__name__,
                  hint="任务入队失败，补偿置 run failed 以解堵 in_flight 修复通道")
        self._session.rollback()  # 清除 enqueue 抛出前可能残留的脏事务（防污染补偿提交）
        # 白话原因（不落异常原文/模型原文，硬规 8）；mark_failed 内联登记 AI 任务失败通知
        self._agent_runs.mark_failed(run_id, "任务入队失败，可重试")
        self._session.commit()  # 独立提交补偿：run 置 failed 即刻生效
        log_event(_COMPONENT, "async.dispatch.compensated", level="WARN", ok=False,
                  run_id=run_id, kind=kind, run_status="failed")
        log_event(_COMPONENT, "async.dispatch.unblocked", run_id=run_id, kind=kind,
                  hint="幻影 queued 批已判死，is_run_alive/in_flight 去重立即解堵")

    def request_source_intake_judgement(self, context_ref: str) -> str:
        return self._dispatch("source_intake", context_ref, self._enqueue)

    def request_element_recognition(self, context_ref: str) -> str:
        return self._dispatch("element_recognition", context_ref, self._enqueue)

    def request_element_review(self, operation_ref: str) -> str:
        return self._dispatch("element_review", operation_ref, self._enqueue_review)

    def request_element_execution(self, operation_ref: str) -> str:
        return self._dispatch("element_execution", operation_ref, self._enqueue_execution)

    def request_item_formation(self, formation_context_ref: str) -> str:
        return self._dispatch(
            "item_formation", formation_context_ref, self._enqueue_item_formation
        )

    def request_item_diagnosis(self, batch_ref: str) -> str:
        return self._dispatch("item_diagnosis", batch_ref, self._enqueue_item_diagnosis)

    def request_item_structure_recheck(self, recheck_context_ref: str) -> str:
        return self._dispatch(
            "item_structure_recheck", recheck_context_ref,
            self._enqueue_item_structure_recheck,
        )

    def request_chart_suggestion(self, context_ref: str) -> str:
        return self._dispatch(
            "chart_suggestion", context_ref, self._enqueue_chart_suggestion
        )

    def request_chart_verification(self, request_ref: str) -> str:
        return self._dispatch(
            "chart_verification", request_ref, self._enqueue_chart_verification
        )
