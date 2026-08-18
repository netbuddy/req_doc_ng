"""分析转化服务（SCN-001-P02 识别登记 + P03 确认工作台 + P04 版本关系层）。

设计事实源：docs/40 domains/DS-001/state-machines/需求要素.md（确认生命周期，迁移表是事实源）、
docs/30 05A/SCN-001（P01–P04 分支矩阵）。
- LDM-005.process_status 承载人工确认生命周期（待确认/分析中/修订中/已确认/已撤销）；
  模型裁定与置信度降级为证据字段（model_verdict/confidence）。
- 写权威在本服务（VAL-003）；迁移合法性由 domain.state_machine 裁定，默认拒绝。
- 改源（勘误/补入）动 LDM-002：出新来源版本/追加「补」块，受影响要素回「待确认」。
- 拆分/合并/新增属版本关系层（P04 草案→确认创建）；产物一律「待确认」。
业务结局用返回值；默认拒绝/版本冲突用 RejectedTransition。
"""

from typing import Optional

from app.api.schemas import (
    ElementAiExecutionCommand,
    ElementAiExecutionResultCommand,
    ElementChangeConfirmCommand,
    ElementChangeDraftRead,
    ElementDecisionCommand,
    ElementDecisionPrecheckCommand,
    ElementDecisionPrecheckRead,
    ElementDialogueCommand,
    ElementDialogueResult,
    ElementEditCommand,
    ElementHistoryRead,
    ElementOperationRequestResult,
    ElementRecognitionCommand,
    ElementReopenCommand,
    ElementReviewCommand,
    ElementReviewResultCommand,
    ElementRevisionCommand,
    ElementTriageCommand,
    ElementWorkspaceRead,
    ManualElementCorrectionCommand,
    MaterialCanvasRead,
    MaterialParseContextRead,
    MaterialErratumCommand,
    MaterialSupplementCommand,
    RecognitionDecisionResult,
    RecognitionRequestResult,
    RecognitionResultCommand,
    RevisionFinalizeCommand,
)
from app.interfaces import (
    ModelOrchestration,
    ModelResultRepository,
    ProcessRecordRepository,
    SourceAssetRepository,
)
from app.services.analysis_change_drafts import AnalysisChangeDrafts
from app.services.analysis_dialogue import AnalysisDialogue
from app.services.analysis_lifecycle import AnalysisLifecycle
from app.services.analysis_recognition import AnalysisRecognition
from app.services.analysis_source_changes import AnalysisSourceChanges
from app.services.analysis_support import AnalysisSupport
from app.services.analysis_workspace import AnalysisWorkspace


class AnalysisTransformationService:
    def __init__(
        self,
        model_orchestration: ModelOrchestration,
        model_results: ModelResultRepository,
        process_records: ProcessRecordRepository,
        source_assets: SourceAssetRepository,
        command_interpreter=None,  # AEP-096 命令解释 lane（可选注入；deps 装配）
    ) -> None:
        self._model_orchestration = model_orchestration
        self._model_results = model_results
        self._process_records = process_records
        self._source_assets = source_assets
        # 逐层向下装配：support(L0) → workspace(L1) → 业务模块(L2) → dialogue(L3)。
        self._support = AnalysisSupport(
            model_results=model_results,
            process_records=process_records,
            source_assets=source_assets,
        )
        self._workspace = AnalysisWorkspace(
            model_results=model_results,
            process_records=process_records,
            source_assets=source_assets,
            support=self._support,
        )
        self._recognition = AnalysisRecognition(
            model_orchestration=model_orchestration,
            model_results=model_results,
            process_records=process_records,
            source_assets=source_assets,
            support=self._support,
        )
        self._source_changes = AnalysisSourceChanges(
            process_records=process_records,
            source_assets=source_assets,
            support=self._support,
            workspace=self._workspace,
        )
        self._lifecycle = AnalysisLifecycle(
            model_orchestration=model_orchestration,
            model_results=model_results,
            process_records=process_records,
            source_assets=source_assets,
            support=self._support,
            workspace=self._workspace,
        )
        self._change_drafts = AnalysisChangeDrafts(
            model_orchestration=model_orchestration,
            model_results=model_results,
            process_records=process_records,
            source_assets=source_assets,
            support=self._support,
            workspace=self._workspace,
        )
        self._dialogue = AnalysisDialogue(
            command_interpreter=command_interpreter,
            support=self._support,
            workspace=self._workspace,
            lifecycle=self._lifecycle,
            source_changes=self._source_changes,
            change_drafts=self._change_drafts,
        )

    # 命令解释 lane 的装配点在 dialogue 子模块；此处代理读写，使构造完成后替换该依赖
    # 的调用方（如 tests/test_element_dialogue.py 注入 Stub 解释器）仍作用于真正的使用方。
    @property
    def _command_interpreter(self):
        return self._dialogue._command_interpreter

    @_command_interpreter.setter
    def _command_interpreter(self, value) -> None:
        self._dialogue._command_interpreter = value

    # ------------------------------------------------------------------
    # P02：AEP-021 识别启动 / AEP-022 识别结果承接
    # ------------------------------------------------------------------

    def submit_element_recognition(
        self, command: ElementRecognitionCommand
    ) -> RecognitionRequestResult:
        return self._recognition.submit_element_recognition(command=command)

    def accept_recognition_result(
        self, command: RecognitionResultCommand
    ) -> RecognitionDecisionResult:
        return self._recognition.accept_recognition_result(command=command)

    # ------------------------------------------------------------------
    # N07/N08 工作区读视图（五区唯一刷新边界）
    # ------------------------------------------------------------------

    def read_element_workspace(self, context_ref: str) -> ElementWorkspaceRead:
        return self._workspace.read_element_workspace(context_ref=context_ref)

    def read_material_parse_context(self, material_ref: str) -> MaterialParseContextRead:
        return self._workspace.read_material_parse_context(material_ref=material_ref)

    def read_material_canvas(self, material_ref: str) -> MaterialCanvasRead:
        return self._workspace.read_material_canvas(material_ref=material_ref)

    # ------------------------------------------------------------------
    # P03：直接裁定（确认/拒绝，单条或批量）
    # ------------------------------------------------------------------

    def decide_elements(self, command: ElementDecisionCommand) -> ElementWorkspaceRead:
        return self._lifecycle.decide_elements(command=command)

    def precheck_decide_elements(
        self, command: ElementDecisionPrecheckCommand
    ) -> ElementDecisionPrecheckRead:
        return self._lifecycle.precheck_decide_elements(command=command)

    # ------------------------------------------------------------------
    # 「AI 建议剔除的候选」人工处置（撤回到正常列表 / 移回候选区）
    # ------------------------------------------------------------------

    def triage_elements(self, command: ElementTriageCommand) -> ElementWorkspaceRead:
        return self._lifecycle.triage_elements(command=command)

    # ------------------------------------------------------------------
    # P03：AEP-023 复核送检（核要素 → 分析中 / 扫原文补漏）/ AEP-024 复核结果承接
    # ------------------------------------------------------------------

    def submit_element_review(self, command: ElementReviewCommand) -> ElementOperationRequestResult:
        return self._lifecycle.submit_element_review(command=command)

    def accept_element_review_result(
        self, command: ElementReviewResultCommand
    ) -> ElementWorkspaceRead:
        return self._lifecycle.accept_element_review_result(command=command)

    # ------------------------------------------------------------------
    # P03：修订迭代（对话轮次，不迁移状态；AI 辅助 / 人工直改修订稿）与采纳修订稿
    # ------------------------------------------------------------------

    def revise_element(self, command: ElementRevisionCommand) -> ElementOperationRequestResult:
        return self._lifecycle.revise_element(command=command)

    def finalize_revision(self, command: RevisionFinalizeCommand) -> ElementOperationRequestResult:
        return self._lifecycle.finalize_revision(command=command)

    # ------------------------------------------------------------------
    # E3：就地修订（改类型/改范围/改表达 —— 版本+1，不迁状态）
    # ------------------------------------------------------------------

    def edit_element(self, command: ElementEditCommand) -> ElementWorkspaceRead:
        return self._lifecycle.edit_element(command=command)

    # ------------------------------------------------------------------
    # E3：改源联动（勘误 / 补入 —— 动 LDM-002，受影响要素回「待确认」）
    # ------------------------------------------------------------------

    def material_erratum(self, command: MaterialErratumCommand) -> ElementWorkspaceRead:
        return self._source_changes.material_erratum(command=command)

    def material_supplement(self, command: MaterialSupplementCommand) -> ElementWorkspaceRead:
        return self._source_changes.material_supplement(command=command)

    # ------------------------------------------------------------------
    # E4：重开（已撤销→待确认）/ 回流（已确认→待确认），产生新版本
    # ------------------------------------------------------------------

    def reopen_element(self, command: ElementReopenCommand) -> ElementWorkspaceRead:
        return self._lifecycle.reopen_element(command=command)

    # ------------------------------------------------------------------
    # E4：变更历史（谁/何时/改了什么）
    # ------------------------------------------------------------------

    def read_element_history(self, context_ref: str, element_ref: str) -> ElementHistoryRead:
        return self._lifecycle.read_element_history(context_ref=context_ref, element_ref=element_ref)

    # ------------------------------------------------------------------
    # AEP-096：区5 对话命令解释（命令词确定性解析 + LLM 正文解释 + 校验派发）
    # ------------------------------------------------------------------

    def element_dialogue(
        self, command: ElementDialogueCommand,
        on_stage: Optional[callable] = None,  # AiRequestStage 稳定码回调（SSE 流式链路回执）
    ) -> ElementDialogueResult:
        return self._dialogue.element_dialogue(command=command, on_stage=on_stage)

    # ------------------------------------------------------------------
    # P04：AEP-025 AI执行 / AEP-027 人工校正（拆分/合并/新增） /
    #      AEP-028 执行结果承接 / AEP-029 确认创建
    # ------------------------------------------------------------------

    def submit_element_ai_execution(
        self, command: ElementAiExecutionCommand
    ) -> ElementOperationRequestResult:
        return self._change_drafts.submit_element_ai_execution(command=command)

    def accept_element_ai_execution_result(
        self, command: ElementAiExecutionResultCommand
    ):
        return self._change_drafts.accept_element_ai_execution_result(command=command)

    def submit_manual_element_correction(
        self, command: ManualElementCorrectionCommand
    ) -> ElementChangeDraftRead:
        return self._change_drafts.submit_manual_element_correction(command=command)

    def confirm_element_change_draft(
        self, command: ElementChangeConfirmCommand
    ) -> ElementWorkspaceRead:
        return self._change_drafts.confirm_element_change_draft(command=command)

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
