"""材料接收服务（AEP-001/002）。

设计事实源：docs/40 domains/DS-001/interfaces/材料接收服务.md、state-machines/材料接入.md、
slices/SCN-001-P01/约束与验收.md。业务结局用返回值；默认拒绝用 RejectedTransition。
"""
from __future__ import annotations

from app.api.schemas import (
    ActionFact,
    IntakeDecisionResult,
    IntakeJudgementResultCommand,
    IntakeRequestResult,
    IntakeResultRead,
    TextIntakeCommand,
)
from app.domain.enums import (
    IntakeConclusion,
    IntakeOutcome,
    IntakeRequestStatus,
    ModelJudgement,
)
from app.domain.errors import NotFound, RejectedTransition
from app.interfaces import (
    AuditTrail,
    ModelOrchestration,
    ModelResultRepository,
    ProcessRecordRepository,
    ProjectScope,
    SourceAssetRepository,
    TraceGraph,
)


class MaterialReceivingService:
    def __init__(
        self,
        project_scope: ProjectScope,
        model_orchestration: ModelOrchestration,
        model_results: ModelResultRepository,
        process_records: ProcessRecordRepository,
        source_assets: SourceAssetRepository,
        trace_graph: TraceGraph,
        audit: AuditTrail,
    ) -> None:
        self._project_scope = project_scope
        self._model_orchestration = model_orchestration
        self._model_results = model_results
        self._process_records = process_records
        self._source_assets = source_assets
        self._trace_graph = trace_graph
        self._audit = audit

    # AEP-001 —— gate『已选定项目 ∧ 非空文本』；不写 LDM-002，送检后返回。
    def submit_text_intake(self, command: TextIntakeCommand) -> IntakeRequestResult:
        replay = self._process_records.find_context_by_idempotency(command.idempotency_key)
        if replay is not None:
            return IntakeRequestResult(
                status=IntakeRequestStatus.SUBMITTED_FOR_JUDGEMENT, context_ref=replay
            )

        if not self._project_scope.is_project_selected(command.project_ref) or not command.raw_text.strip():
            return IntakeRequestResult(
                status=IntakeRequestStatus.REJECTED_PRECHECK,
                next_action="请选定项目并补充非空文本后重新提交",
            )

        context = self._process_records.create_intake_request(
            command.project_ref,
            command.idempotency_key,
            command.raw_text,
            command.source_note,
            command.operator_ref,
        )
        agent_run = self._model_orchestration.request_source_intake_judgement(context)
        return IntakeRequestResult(
            status=IntakeRequestStatus.SUBMITTED_FOR_JUDGEMENT,
            context_ref=context,
            agent_run_ref=agent_run,
        )

    # AEP-002 —— 仅在『接入请求上下文』受理；四分支；仅可接入写 LDM-002。
    def accept_intake_judgement_result(
        self, command: IntakeJudgementResultCommand
    ) -> IntakeDecisionResult:
        context = command.intake_context_ref

        if not self._process_records.context_exists(context):
            raise RejectedTransition("接入请求上下文不存在；AEP-002 仅在『接入请求上下文』受理")
        if self._source_assets.conclusion_of(context) is not None:
            raise RejectedTransition("该上下文已有接入结论；需经 AEP-001 补充重提后再判定")

        judgement = self._model_results.read_intake_judgement(command.model_result_ref)

        # 判断失败：过程停靠、保留人工继续、不污染事实（VAL-005）；状态不迁移。
        if judgement is None or judgement is ModelJudgement.JUDGEMENT_FAILED:
            self._process_records.mark_stopped(context, "模型判断失败或结果不可承接", "可人工继续或重判")
            return IntakeDecisionResult(
                outcome=IntakeOutcome.JUDGEMENT_FAILED, next_action="可人工继续或重判（不污染事实）"
            )

        # 可接入 ∧ 服务确认接收 → 唯一写 LDM-002 的分支（VAL-002/003）。
        if judgement is ModelJudgement.ACCEPTABLE and command.service_accepts:
            material = self._source_assets.save_material_and_intake_record(context, command.model_result_ref)
            self._trace_graph.pre_establish_source_trace(material)  # AEP-077
            self._audit.record_intake_accepted(material, command.operator_ref)
            self._record_intake_adoption(context, command)
            return IntakeDecisionResult(
                outcome=IntakeOutcome.ACCEPTED,
                intake_conclusion=IntakeConclusion.ACCEPTED,
                material_ref=material,
            )

        # 可接入但服务未确认接收：守卫『∧ 服务确认接收』未满足 → 不写 LDM-002。
        if judgement is ModelJudgement.ACCEPTABLE and not command.service_accepts:
            self._source_assets.save_intake_conclusion(
                context, IntakeConclusion.RETURNED_FOR_SUPPLEMENT, command.model_result_ref
            )
            self._record_intake_adoption(context, command)
            return IntakeDecisionResult(
                outcome=IntakeOutcome.RETURNED_FOR_SUPPLEMENT,
                intake_conclusion=IntakeConclusion.RETURNED_FOR_SUPPLEMENT,
                next_action="服务未确认接收，请复核后重提",
            )

        # 内容不足 ∨ 归属不明 → 退回补充（不写 LDM-002）。
        if judgement in (ModelJudgement.INSUFFICIENT_CONTENT, ModelJudgement.UNCLEAR_ATTRIBUTION):
            self._source_assets.save_intake_conclusion(
                context, IntakeConclusion.RETURNED_FOR_SUPPLEMENT, command.model_result_ref
            )
            self._record_intake_adoption(context, command)
            return IntakeDecisionResult(
                outcome=IntakeOutcome.RETURNED_FOR_SUPPLEMENT,
                intake_conclusion=IntakeConclusion.RETURNED_FOR_SUPPLEMENT,
                next_action="请补充材料后重提",
            )

        # 无需求资产价值 → 已排除（不写 LDM-002）。
        if judgement is ModelJudgement.NO_ASSET_VALUE:
            self._source_assets.save_intake_conclusion(
                context, IntakeConclusion.EXCLUDED, command.model_result_ref
            )
            self._record_intake_adoption(context, command)
            return IntakeDecisionResult(
                outcome=IntakeOutcome.EXCLUDED,
                intake_conclusion=IntakeConclusion.EXCLUDED,
                next_action="已排除：无需求资产价值",
            )

        raise RejectedTransition(f"未覆盖的判定取值：{judgement!r}")

    def _record_intake_adoption(self, context: str, command) -> None:
        """采纳结论明细（口径设计 §4：接入判定被承接=adopted；判定失败停靠不写）。"""
        project_ref = self._process_records.project_of_context(context)
        if not project_ref:
            return
        self._model_results.record_adoption(
            model_result_ref=command.model_result_ref, project_ref=project_ref,
            stage="source_intake", subject_type="material_intake", subject_ref=context,
            outcome="adopted", operator_ref=command.operator_ref,
            idempotency_key=f"{command.idempotency_key}:adoption:{context}",
        )

    # 结果查询读视图（intakeApi.getResult）—— available_actions 是后端事实。
    def read_intake_result(self, context_ref: str) -> IntakeResultRead:
        if not self._process_records.context_exists(context_ref):
            raise NotFound("接入请求上下文不存在")

        conclusion = self._source_assets.conclusion_of(context_ref)
        if conclusion is None:
            stop_next = self._process_records.read_stop_next_action(context_ref)
            if stop_next is not None:  # 判断失败停靠
                return IntakeResultRead(
                    context_ref=context_ref,
                    next_action=stop_next,
                    available_actions=[ActionFact(key="retry", enabled=True)],
                )
            return IntakeResultRead(  # 仍在判断中
                context_ref=context_ref,
                next_action="判断进行中",
                available_actions=[],
            )

        if conclusion is IntakeConclusion.ACCEPTED:
            actions = [ActionFact(key="start_recognition", enabled=True)]  # 开放 P02
            next_action = None
        elif conclusion is IntakeConclusion.RETURNED_FOR_SUPPLEMENT:
            actions = [ActionFact(key="resubmit", enabled=True)]
            next_action = "请补充材料后重提"
        else:  # EXCLUDED
            actions = [ActionFact(key="resubmit", enabled=False, disabled_reason="已排除：无需求资产价值")]
            next_action = "已排除"

        model_result_ref = self._source_assets.model_result_ref_of(context_ref)
        basis = self._model_results.read_basis(model_result_ref) if model_result_ref else None
        return IntakeResultRead(
            context_ref=context_ref,
            intake_conclusion=conclusion,
            material_ref=self._source_assets.material_of(context_ref),
            basis=basis,
            next_action=next_action,
            available_actions=actions,
        )
