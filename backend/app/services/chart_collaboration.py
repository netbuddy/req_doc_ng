"""图表协同服务（SCN-004-P01 受控图表创建与追溯预建立 + P02 图表核对确认与追溯正式确立）。

设计事实源：docs/30 …/SCN-004_受控图表确认与追溯关系成立流程.md。
- P01：来源准入（仅确认态 LDM-007）→ 草稿壳 + 预建立 LDM-013 → 源码编辑循环
  （人工/AI 建议，AI 输出先落 LDM-015，用户采纳后才写 LDM-012）→ 受控校验 + 追溯同步。
- P02：核对发起（草稿→待确认，冻结编辑）→ AI 图文核对（先落图文核对类 LDM-015）→
  用户逐项复核 → 确认准入裁定 → 图表确认与追溯正式确立同批成立；任一失败不对外成立。
- AI 核对失败不得降级为纯人工确认；阻断发现项被接受时不得确认。
业务结局用返回值；默认拒绝/版本冲突用 RejectedTransition。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from app.api.schemas import (
    ActionFact,
    ChartConfirmationCommand,
    ChartConfirmationResult,
    ChartCreateCommand,
    ChartCreateResult,
    ChartBusinessSourceRead,
    ChartEligibleSourceListRead,
    ChartEligibleSourceRead,
    ChartFindingDecisionCommand,
    ChartFindingRead,
    ChartIssueCommand,
    ChartLifecycleCommand,
    ChartListRead,
    ChartRead,
    ChartRevisionRead,
    ChartSourceChangeCommand,
    ChartSuggestionCommand,
    ChartSuggestionHandlingCommand,
    ChartSuggestionRead,
    ChartSuggestionRequestResult,
    ChartSuggestionThreadEntryRead,
    ChartVerificationCommand,
    ChartVerificationRead,
    ChartVerificationRequestResult,
    ChartWorkspaceRead,
    ConfirmationGateRead,
    IssueListRead,
    IssueRead,
    TraceLinkListRead,
    TraceLinkRead,
)
from app.domain.chart_rules import (
    TYPE_KIND_MAP,
    preview_capability,
    validate_controlled_source,
)
from app.domain.labels import CHART_TYPE_GUIDE
from app.domain.enums import (
    ChartFindingDecision as CFD,
    ChartFindingType as CFT,
    ChartFormat,
    ChartSourceChangeOrigin,
    ChartSourceKind,
    ELEMENT_KNOWLEDGE_CATEGORY,
    ChartStatus as CS,
    ChartSuggestionHandling as CSH,
    ChartSuggestionRequestKind,
    ChartType,
    ChartVerificationProcessingStatus as CVS,
    ElementProcessStatus,
    IssueStatus,
    IssueType,
    KnowledgeCategory,
    RequirementItemStatus as IS,
    TraceLinkStatus as TS,
    TraceRelationType,
)
from app.domain.errors import InvalidInput, NotFound, RejectedTransition
from app.domain.state_machine import (
    ChartEvent,
    ChartState,
    TraceEvent,
    TraceState,
    chart_transition,
    trace_transition,
)
from app.interfaces import (
    ChartFindingRow,
    ChartProcessRepository,
    ChartRepository,
    ChartRow,
    ChartVerificationRoundRow,
    IssueRepository,
    IssueRow,
    ModelOrchestration,
    ModelResultRepository,
    RequirementItemRepository,
    SourceAssetRepository,
    TraceLinkRepository,
    TraceLinkRow,
)
from app.log import log_event
from app.services.run_liveness import dead_run_verdict

_COMPONENT = "chart-collaboration"

# 本服务核对 lane 的 rq task 名（HK-2 判死阈值经 job_timeout_for(lane) 取值的键）
_VERIFICATION_LANE = "run_chart_verification"

# 接受即阻断确认的发现项类型（SCN-004 §5.3：存在隐藏需求/图文冲突/来源缺口/追溯缺口不得确认；
# undeterminable 被接受 = 承认无法判断，同样不得确认）
_BLOCKING_FINDING_TYPES = {
    CFT.SUSPECTED_HIDDEN_REQUIREMENT.value,
    CFT.CHART_TEXT_CONFLICT.value,
    CFT.SOURCE_COVERAGE_GAP.value,
    CFT.TRACE_GAP.value,
    CFT.UNDETERMINABLE.value,
}

# 发现项类型 → 缺省问题项类型映射（转问题项）
_FINDING_ISSUE_TYPE = {
    CFT.SUSPECTED_HIDDEN_REQUIREMENT.value: IssueType.HIDDEN_REQUIREMENT,
    CFT.CHART_TEXT_CONFLICT.value: IssueType.CONFLICT,
    CFT.SOURCE_COVERAGE_GAP.value: IssueType.INSUFFICIENT_SOURCE,
    CFT.TRACE_GAP.value: IssueType.GAP,
}

_CHART_FINDING_TYPES = {t.value for t in CFT}


def _parse_source_refs(raw: Optional[str]) -> list[dict]:
    """LDM-012.source_refs 读时兼容（06 B.2，零迁移）：
    旧格式=纯 id 字符串列表 → {kind: requirement_item, ref}；新格式=[{kind, ref}]。
    所有读取路径一律经本函数，保证存量纯 id 图表零影响。
    """
    if not raw:
        return []
    out: list[dict] = []
    for item in json.loads(raw):
        if isinstance(item, str):
            out.append({"kind": ChartSourceKind.REQUIREMENT_ITEM.value, "ref": item})
        elif isinstance(item, dict) and item.get("ref"):
            out.append({
                "kind": item.get("kind") or ChartSourceKind.REQUIREMENT_ITEM.value,
                "ref": item["ref"],
            })
    return out


def _source_ref_ids(raw: Optional[str]) -> list[str]:
    """仅取 ref id 列表（兼容读；供只需 id 的既有路径复用）。"""
    return [s["ref"] for s in _parse_source_refs(raw)]


# 业务领域知识翼类型（图表 SUPPORTING_CONTENT 候选/校验；派生自单一来源）
_BUSINESS_ELEMENT_TYPES = [
    t.value for t, c in ELEMENT_KNOWLEDGE_CATEGORY.items()
    if c == KnowledgeCategory.BUSINESS
]


class ChartCollaborationService:
    """图表协同服务（P01 编辑循环 + P02 核对确认 + 追溯/问题项读视图）。"""

    def __init__(
        self,
        model_orchestration: ModelOrchestration,
        model_results: ModelResultRepository,
        charts: ChartRepository,
        trace_links: TraceLinkRepository,
        issues: IssueRepository,
        chart_process: ChartProcessRepository,
        items: RequirementItemRepository,
        source_assets: Optional[SourceAssetRepository] = None,
    ) -> None:
        self._model_orchestration = model_orchestration
        self._model_results = model_results
        self._charts = charts
        self._trace_links = trace_links
        self._issues = issues
        self._chart_process = chart_process
        self._items = items
        self._source_assets = source_assets  # P4：业务知识来源（SUPPORTING_CONTENT）候选/校验

    # ------------------------------------------------------------------
    # P01-N01/N02：候选来源 + 创建准入
    # ------------------------------------------------------------------

    def list_eligible_sources(self, project_ref: str) -> ChartEligibleSourceListRead:
        rows = self._items.confirmed_items_of_project(project_ref)
        # 06 B.1 两段候选：确认态条目 + 业务翼确认态要素（SUPPORTING_CONTENT）。
        biz = []
        if self._source_assets is not None:
            biz = [
                e for e in self._source_assets.list_project_elements_by_type(
                    project_ref, _BUSINESS_ELEMENT_TYPES)
                if e.process_status == ElementProcessStatus.CONFIRMED.value
            ]
        next_action = None
        if not rows and not biz:
            next_action = "当前项目暂无确认态需求条目或业务知识；请先完成条目确认或知识项确认"
        return ChartEligibleSourceListRead(
            project_ref=project_ref,
            sources=[self._project_source(i) for i in rows],
            business_sources=[
                ChartBusinessSourceRead(
                    element_ref=e.id, element_type=e.element_type, content=e.content[:120],
                )
                for e in biz
            ],
            next_action=next_action,
        )

    def _source_precheck(
        self, project_ref: str, sources: list[dict],
    ) -> Optional[str]:
        """来源准入（N02，06 B.1 按 kind 分路）：条目走确认态条目校验；业务知识来源
        走业务翼确认态要素校验。返回不可创建原因或 None。"""
        if not sources:
            return "未选择受控来源：图表须从确认态需求条目或确认态业务知识发起"
        biz_map: Optional[dict] = None
        for s in sources:
            kind, ref = s["kind"], s["ref"]
            if kind == ChartSourceKind.REQUIREMENT_ITEM.value:
                item = self._items.get_item(ref)
                if item is None or item.project_ref != project_ref:
                    return "来源条目不在当前项目或已不存在，请刷新后重新选择"
                if item.status != IS.CONFIRMED.value:
                    return "来源条目不处于确认态，不能作为图表创建依据；请先完成条目确认"
            elif kind == ChartSourceKind.SUPPORTING_CONTENT.value:
                if self._source_assets is None:
                    return "业务知识来源当前不可用"
                if biz_map is None:
                    biz_map = {
                        e.id: e for e in self._source_assets.list_project_elements_by_type(
                            project_ref, _BUSINESS_ELEMENT_TYPES)
                    }
                el = biz_map.get(ref)
                if el is None:
                    return "来源业务知识不在当前项目或已不存在，请刷新后重新选择"
                if el.process_status != ElementProcessStatus.CONFIRMED.value:
                    return "来源业务知识不处于确认态，请先在知识抽取页完成确认"
            else:
                return "不支持的图表来源类别"
        return None

    def create_chart(self, command: ChartCreateCommand) -> ChartCreateResult:
        # 幂等：图表创建无独立请求表，以修订留痕键承载（创建即首个版本留痕）
        replay = self._charts.find_revision_by_idempotency(command.idempotency_key)
        if replay is not None:
            return ChartCreateResult(status="created", chart_ref=replay,
                                     next_action="图表已创建（幂等重放）")

        if command.source_kind not in (
            ChartSourceKind.REQUIREMENT_ITEM, ChartSourceKind.SUPPORTING_CONTENT
        ):
            return ChartCreateResult(
                status="rejected_precheck",
                next_action="图表来源仅支持确认态需求条目或确认态业务知识（文档章节来源留待后续）",
            )
        if command.chart_type not in TYPE_KIND_MAP:
            return ChartCreateResult(status="rejected_precheck",
                                     next_action="图表类型不可识别，请重新选择")
        matrix_errors = validate_controlled_source(command.format, command.chart_type, "占位")
        if matrix_errors and "不支持图表类型" in matrix_errors[0]:
            return ChartCreateResult(status="rejected_precheck", next_action=matrix_errors[0])

        # 逐条带 kind（本迭代单一 source_kind 应用于全部 source_refs；混合来源格式已就绪，06 B.2）
        source_ref_ids = list(dict.fromkeys(command.source_refs))  # 去重保序
        sources = [{"kind": command.source_kind.value, "ref": r} for r in source_ref_ids]
        reason = self._source_precheck(command.project_ref, sources)
        if reason is not None:
            log_event(_COMPONENT, "chart.create.rejected", level="WARN",
                      project_ref=command.project_ref,
                      reject_reason="source_precheck", ok=False)
            return ChartCreateResult(status="rejected_precheck", next_action=reason)

        # N04 图表创建：INITIAL→draft（状态机裁定）
        nxt = chart_transition(ChartState.INITIAL, ChartEvent.CREATE)
        assert nxt is ChartState.DRAFT
        item_n = sum(1 for s in sources if s["kind"] == ChartSourceKind.REQUIREMENT_ITEM.value)
        biz_n = len(sources) - item_n
        basis_parts = []
        if item_n:
            basis_parts.append(f"{item_n} 条确认态需求条目")
        if biz_n:
            basis_parts.append(f"{biz_n} 条确认态业务知识")
        creation_basis = "来源准入通过：" + " + ".join(basis_parts)
        # 主题可空：先落确定性临时标题；初稿生成结果会以语义标题回填
        title = command.title.strip() or self._provisional_title(
            command.project_ref, source_ref_ids, command.chart_type,
        )
        chart_ref = self._charts.create_chart(
            command.project_ref, title,
            TYPE_KIND_MAP[command.chart_type].value, command.chart_type.value,
            command.format.value, command.source_kind.value,
            json.dumps(sources), creation_basis, command.operator_ref,
        )
        self._charts.add_revision(
            chart_ref, 1, "", command.format.value,
            ChartSourceChangeOrigin.MANUAL.value, None, "创建图表",
            command.operator_ref, command.idempotency_key,
        )
        # N05 自动预建立追溯：逐来源 INITIAL→pre_established（来源类别决定上游 node 类型）
        for s in sources:
            up_type = ("requirement_item" if s["kind"] == ChartSourceKind.REQUIREMENT_ITEM.value
                       else "element")
            self._pre_establish_link(command.project_ref, s["ref"], chart_ref, up_type)
        log_event(_COMPONENT, "chart.status.transition", chart_ref=chart_ref,
                  from_status="initial", to_status=CS.DRAFT.value,
                  sm_event=ChartEvent.CREATE.value, source_count=len(sources), ok=True)

        if not command.generate_initial:
            return ChartCreateResult(
                status="created", chart_ref=chart_ref,
                next_action="图表与预建立追溯已形成；进入源码编辑循环",
            )
        # 创建即初稿：立即发起 initial 建议请求（结果经受控校验自动应用为初稿；
        # 失败/拒绝停靠在设计页对话时间线可见，图表回退为可手工编辑的空稿）
        context_ref = self._chart_process.create_suggestion_request(
            command.project_ref, chart_ref, 1, "",
            command.operator_ref, f"{command.idempotency_key}:initial",
            kind=ChartSuggestionRequestKind.INITIAL.value,
        )
        self._model_orchestration.request_chart_suggestion(context_ref)
        log_event(_COMPONENT, "chart.initial.submitted", chart_ref=chart_ref,
                  context_ref=context_ref, ok=True)
        return ChartCreateResult(
            status="created", chart_ref=chart_ref,
            initial_suggestion_context_ref=context_ref,
            next_action="图表已创建，正基于来源条目生成初稿；预建立追溯已形成",
        )

    def _provisional_title(
        self, project_ref: str, source_refs: list[str], chart_type: ChartType,
    ) -> str:
        """确定性临时标题：首条来源编号 + 图表类型标签（初稿生成后被语义标题覆盖）。"""
        type_label = next(
            (g["label"] for g in CHART_TYPE_GUIDE if g["code"] == chart_type.value),
            chart_type.value,
        )
        first = self._items.get_item(source_refs[0]) if source_refs else None
        prefix = f"{first.req_no} " if first is not None else ""
        return f"{prefix}{type_label}"

    def _pre_establish_link(
        self, project_ref: str, upstream_ref: str, chart_ref: str,
        upstream_type: str = "requirement_item",
    ) -> None:
        existing = self._trace_links.find_link(upstream_ref, chart_ref, TraceRelationType.CHART.value)
        if existing is None:
            trace_transition(TraceState.INITIAL, TraceEvent.PRE_ESTABLISH)
            basis = ("图表创建自动预建立（来源=确认态需求条目）" if upstream_type == "requirement_item"
                     else "图表创建自动预建立（来源=确认态业务知识）")
            self._trace_links.create_link(
                project_ref, TraceRelationType.CHART.value,
                upstream_type, upstream_ref, "chart", chart_ref,
                TS.PRE_ESTABLISHED.value, basis,
            )
            log_event(_COMPONENT, "trace.link.pre_established", chart_ref=chart_ref,
                      upstream_ref=upstream_ref, ok=True)
        elif existing.status == TS.SUSPECT_PENDING_REVIEW.value:
            trace_transition(TraceState.SUSPECT_PENDING_REVIEW, TraceEvent.SYNC)
            self._trace_links.set_link_status(existing.id, TS.PRE_ESTABLISHED.value,
                                              status_reason=None)
            log_event(_COMPONENT, "trace.link.resynced", chart_ref=chart_ref,
                      upstream_ref=upstream_ref, ok=True)
        elif existing.status == TS.INVALID.value:
            # 同一条边重新预建立 = 该边的新生命周期（唯一约束下复用行）
            self._trace_links.set_link_status(existing.id, TS.PRE_ESTABLISHED.value,
                                              status_reason=None)
            log_event(_COMPONENT, "trace.link.re_pre_established", chart_ref=chart_ref,
                      upstream_ref=upstream_ref, ok=True)

    # ------------------------------------------------------------------
    # 读视图
    # ------------------------------------------------------------------

    def list_charts(self, project_ref: str) -> ChartListRead:
        rows = self._charts.charts_of_project(project_ref)
        return ChartListRead(
            project_ref=project_ref,
            charts=[
                ChartRead(
                    chart_ref=c.id, title=c.title, chart_kind=c.chart_kind,
                    chart_type=c.chart_type, format=c.format, status=c.status,
                    draft_version=c.draft_version,
                    source_count=len(_source_ref_ids(c.source_refs)),
                    updated_at=c.updated_at,
                )
                for c in rows
            ],
            next_action=None if rows else "从确认态需求条目发起创建第一份受控图表",
        )

    def _reconcile_stale_verification(self, chart_ref: str) -> None:
        """读侧自愈（HK-2，仿 docx `_reconcile_stale_exports`）：VERIFYING 悬轮联查 AgentRun 收尸。

        悬轮的核对请求 run 已失败 / 缺失 / 超判死阈值 → 轮次落 FAILED＋通知，
        `can_reverify` 单飞守卫随之解锁。在飞未超龄不动（防误杀）；脱敏归因（硬规则 8）。
        """
        round_ = self._chart_process.latest_round_of_chart(chart_ref)
        if round_ is None or round_.processing_status != CVS.VERIFYING.value:
            return
        session = getattr(self._chart_process, "session", None)
        if session is None:  # 非 SQL 装配（测试替身）无从联查 run，跳过
            return
        from app.repositories.agent_run import SqlAgentRunRepository
        from app.services.notification import notify_agent_run_lost

        agent_runs = SqlAgentRunRepository(session)
        run = agent_runs.find_by_context(round_.request_ref, "chart_verification")
        verdict = dead_run_verdict(_VERIFICATION_LANE, run, now=datetime.now(timezone.utc))
        if verdict is None:  # run 在飞未超龄（或已收束成功）：不收尸
            return
        if verdict == "run_stale":
            # 僵尸 run 判死：run 落 failed（repo 内联动既有 AI 任务失败通知，防静默）
            agent_runs.mark_failed(str(run.id), "执行进程失联，读侧对账判死（HK-2）")
        elif verdict == "run_missing":
            notify_agent_run_lost(session, "chart_verification", round_.id)
        self._chart_process.finish_round(
            round_.id, CVS.FAILED.value,
            reason="执行进程失联，已自动对账；可重试核对或退回修订，不得降级为纯人工确认",
        )
        log_event(_COMPONENT, "chart.verification.round_reconciled", level="WARN",
                  ok=False, round_ref=round_.id, chart_ref=chart_ref,
                  request_ref=round_.request_ref,
                  run_id=str(run.id) if run is not None else None,
                  verdict=verdict)

    def read_chart_workspace(self, chart_ref: str) -> ChartWorkspaceRead:
        chart = self._require_chart(chart_ref)
        try:  # HK-2 读侧自愈：对账失败只记 WARN，不得阻塞读主流程
            self._reconcile_stale_verification(chart.id)
        except Exception as exc:  # noqa: BLE001
            log_event(_COMPONENT, "chart.verification.reconcile_error", level="WARN",
                      ok=False, error_code=type(exc).__name__)
        return self._workspace(chart)

    def _require_chart(self, chart_ref: str) -> ChartRow:
        chart = self._charts.get_chart(chart_ref)
        if chart is None:
            raise NotFound("需求图表不存在")
        return chart

    def _project_source(self, item) -> ChartEligibleSourceRead:
        return ChartEligibleSourceRead(
            item_ref=item.id, req_no=item.req_no, expression=item.expression,
            req_type=item.req_type, status=item.status,
            curation_note=item.curation_note, boundary_note=item.boundary_note,
            verification_method=item.verification_method,
            verification_note=item.verification_note, priority=item.priority,
        )

    def _project_link(self, link: TraceLinkRow, label: Optional[str] = None) -> TraceLinkRead:
        return TraceLinkRead(
            link_ref=link.id, relation_type=link.relation_type,
            upstream_type=link.upstream_type, upstream_ref=link.upstream_ref,
            upstream_label=label,
            downstream_type=link.downstream_type, downstream_ref=link.downstream_ref,
            downstream_label=None,
            status=TS(link.status), initial_basis=link.initial_basis,
            status_reason=link.status_reason,
            established_basis=link.established_basis, established_at=link.established_at,
            issue_ref=link.issue_ref,
        )

    def _project_finding(self, f: ChartFindingRow) -> ChartFindingRead:
        return ChartFindingRead(
            finding_ref=f.id, finding_type=CFT(f.finding_type),
            summary=f.summary, basis_summary=f.basis_summary,
            related_source_refs=list(json.loads(f.related_source_refs or "[]")),
            decision=CFD(f.decision) if f.decision else None,
            decision_reason=f.decision_reason, decision_operator=f.decision_operator,
            decided_at=f.decided_at, issue_ref=f.issue_ref,
            is_blocking=f.finding_type in _BLOCKING_FINDING_TYPES,
        )

    def _suggestion_views_of_chart(
        self, chart_ref: str,
    ) -> tuple[list[ChartSuggestionThreadEntryRead], list[ChartSuggestionRead]]:
        """建议请求全生命周期投影（时间升序）：送检中/已登记/停靠都必须可见，不得静默。"""
        thread: list[ChartSuggestionThreadEntryRead] = []
        suggestions: list[ChartSuggestionRead] = []
        for req in reversed(self._chart_process.suggestion_requests_of_chart(chart_ref)):
            payload = self._model_results.latest_stage_payload("chart_source_suggestion", req.id)
            suggestion: Optional[ChartSuggestionRead] = None
            if payload is not None and payload.result_code == "suggested":
                body = json.loads(payload.payload) if payload.payload else {}
                suggestion = ChartSuggestionRead(
                    suggestion_ref=payload.ref,
                    source_code=str(body.get("source_code") or ""),
                    explanation=str(body.get("explanation") or ""),
                    process_status=self._model_results.read_process_status(payload.ref) or "pending",
                    created_for_version=body.get("base_draft_version"),
                )
                suggestions.append(suggestion)
                status = "suggested"
            elif req.stop_next_action:
                status = "stopped"
            else:
                status = "generating"
            thread.append(ChartSuggestionThreadEntryRead(
                context_ref=req.id, intent=req.intent, created_at=req.created_at,
                kind=req.kind, status=status,
                stop_reason=req.stop_next_action, suggestion=suggestion,
            ))
        return thread, suggestions

    def _verification_of_chart(self, chart_ref: str) -> Optional[ChartVerificationRead]:
        round_ = self._chart_process.latest_round_of_chart(chart_ref)
        if round_ is None:
            return None
        return ChartVerificationRead(
            round_ref=round_.id, round_no=round_.round_no,
            chart_draft_version=round_.chart_draft_version,
            processing_status=round_.processing_status, reason=round_.reason,
            invalidated=round_.invalidated,
            findings=[self._project_finding(f)
                      for f in self._chart_process.findings_of_round(round_.id)],
        )

    def _workspace(
        self, chart: ChartRow, validation_errors: Optional[list[str]] = None,
        next_action: Optional[str] = None,
    ) -> ChartWorkspaceRead:
        source_refs = list(_source_ref_ids(chart.source_refs))
        sources: list[ChartEligibleSourceRead] = []
        labels: dict[str, str] = {}
        for ref in source_refs:
            item = self._items.get_item(ref)
            if item is None:
                continue
            sources.append(self._project_source(item))
            labels[ref] = f"{item.req_no} {item.expression[:30]}"
        links = self._trace_links.links_of_chart(chart.id)
        suggestion_thread, suggestions = self._suggestion_views_of_chart(chart.id)
        round_ = self._chart_process.latest_round_of_chart(chart.id)
        findings = self._chart_process.findings_of_round(round_.id) if round_ else []
        blocked = self._confirmation_blockers(chart, round_, findings, links)

        is_draft = chart.status == CS.DRAFT.value
        is_pending = chart.status == CS.PENDING_CONFIRMATION.value
        is_returned = chart.status == CS.RETURNED_FOR_REVISION.value
        round_running = round_ is not None and round_.processing_status == CVS.VERIFYING.value
        # 与 _verification_entry_blockers 同口径：失效关系不参与可核对裁定
        active_links = [l for l in links if l.status != TS.INVALID.value]
        can_start = is_draft and bool(chart.source_code.strip()) and bool(active_links) and all(
            l.status == TS.PRE_ESTABLISHED.value for l in active_links
        )
        can_reverify = is_pending and not round_running
        undecided = any(f.decision is None for f in findings)
        confirmable = is_pending and not blocked

        return ChartWorkspaceRead(
            chart_ref=chart.id, project_ref=chart.project_ref, title=chart.title,
            chart_kind=chart.chart_kind, chart_type=chart.chart_type, format=chart.format,
            source_code=chart.source_code, draft_version=chart.draft_version,
            status=chart.status, status_reason=chart.status_reason,
            preview_capability=preview_capability(ChartFormat(chart.format)),
            creation_basis=chart.creation_basis,
            verification_conclusion=chart.verification_conclusion,
            confirm_basis=chart.confirm_basis,
            sources=sources,
            trace_links=[self._project_link(l, labels.get(l.upstream_ref)) for l in links],
            suggestions=suggestions,
            suggestion_thread=suggestion_thread,
            verification=self._verification_of_chart(chart.id),
            revisions=[
                ChartRevisionRead(
                    revision_ref=r.id, draft_version=r.draft_version,
                    change_origin=r.change_origin, note=r.note,
                    operator_ref=r.operator_ref, created_at=r.at,
                )
                for r in self._charts.revisions_of(chart.id)
            ],
            confirmation_gate=ConfirmationGateRead(
                can_submit=confirmable, blocked_reasons=blocked,
                review_summary_ref=round_.id if round_ else None,
            ),
            available_actions=[
                ActionFact(key="apply_source_change", enabled=is_draft,
                           disabled_reason=None if is_draft else "仅草稿中可编辑源码（待确认已冻结编辑）"),
                ActionFact(key="request_suggestion", enabled=is_draft,
                           disabled_reason=None if is_draft else "仅草稿中可请求 AI 源码建议"),
                ActionFact(key="start_verification", enabled=can_start or can_reverify,
                           disabled_reason=None if (can_start or can_reverify)
                           else ("核对进行中" if round_running
                                 else "需具备受控源码与全部预建立追溯后才能发起核对")),
                ActionFact(key="submit_finding_decision", enabled=is_pending and undecided,
                           disabled_reason=None if (is_pending and undecided) else "当前无待复核发现项"),
                ActionFact(key="confirm_chart", enabled=confirmable,
                           disabled_reason=None if confirmable
                           else (blocked[0] if blocked else "确认准入未通过")),
                ActionFact(key="return_for_revision", enabled=is_pending,
                           disabled_reason=None if is_pending else "仅待确认可退回修订"),
                ActionFact(key="void_chart", enabled=is_pending or is_returned,
                           disabled_reason=None if (is_pending or is_returned) else "当前状态不可作废"),
                ActionFact(key="resume_editing", enabled=is_returned,
                           disabled_reason=None if is_returned else "仅退回修订可重回编辑"),
            ],
            validation_errors=validation_errors or [],
            next_action=next_action,
        )

    # ------------------------------------------------------------------
    # P01-N07/N10/N11：人工源码变更应用 + 受控校验 + 预建立追溯同步
    # ------------------------------------------------------------------

    def apply_source_change(
        self, chart_ref: str, command: ChartSourceChangeCommand,
    ) -> ChartWorkspaceRead:
        replay = self._charts.find_revision_by_idempotency(command.idempotency_key)
        if replay is not None:  # 幂等重放：返回当前工作区
            return self.read_chart_workspace(replay)

        chart = self._require_chart(chart_ref)
        if chart.project_ref != command.project_ref:
            raise NotFound("图表不在当前项目")
        # 状态机裁定：仅草稿接受源码变更（待确认冻结编辑，默认拒绝）
        chart_transition(ChartState(chart.status), ChartEvent.APPLY_SOURCE_CHANGE)
        if command.expected_draft_version != chart.draft_version:
            raise RejectedTransition("草稿已被更新（版本不一致），请刷新后基于最新版本编辑")

        return self._apply_validated_change(
            chart, command.source_code, command.format, command.chart_type,
            list(dict.fromkeys(command.source_refs)),
            ChartSourceChangeOrigin.MANUAL.value, None, "人工源码编辑",
            command.operator_ref, command.idempotency_key,
        )

    def _apply_validated_change(
        self, chart: ChartRow, source_code: str, format_: ChartFormat,
        chart_type: ChartType, source_refs: list[str], change_origin: str,
        suggestion_ref: Optional[str], note: str, operator_ref: str,
        idempotency_key: str,
    ) -> ChartWorkspaceRead:
        """受控校验 → 写 LDM-012 → 追溯同步（人工编辑与 AI 采纳共用的唯一写入路径）。"""
        errors = validate_controlled_source(format_, chart_type, source_code)
        # 源码变更不改来源类别：沿用图表创建时的 source_kind（06 B.2 格式随写演进）。
        sources = [{"kind": chart.source_kind, "ref": r} for r in source_refs]
        source_reason = self._source_precheck(chart.project_ref, sources)
        if source_reason is not None:
            errors.append(source_reason)
        if errors:
            # 非受控/来源不足：草稿壳保留，有效源码不更新（§4.5 行6/行7）
            log_event(_COMPONENT, "chart.source.rejected", level="WARN",
                      chart_ref=chart.id, error_count=len(errors), ok=False)
            return self._workspace(chart, validation_errors=errors,
                                   next_action="修正源码格式或来源引用后重新应用")

        new_version = self._charts.update_chart_source(
            chart.id, source_code, format_.value, chart_type.value,
            TYPE_KIND_MAP[chart_type].value, json.dumps(sources),
        )
        self._charts.add_revision(
            chart.id, new_version, source_code, format_.value,
            change_origin, suggestion_ref, note, operator_ref, idempotency_key,
        )
        self._sync_trace_links(chart, source_refs)
        log_event(_COMPONENT, "chart.source.applied", chart_ref=chart.id,
                  draft_version=new_version, change_origin=change_origin, ok=True)
        updated = self._require_chart(chart.id)
        return self._workspace(updated, next_action="源码已应用；可继续编辑、预览或发起核对")

    def _sync_trace_links(self, chart: ChartRow, new_source_refs: list[str]) -> None:
        """N11 覆盖对象变化后同步预建立关系（新增→预建立；移除→失效）。"""
        up_type = ("requirement_item" if chart.source_kind == ChartSourceKind.REQUIREMENT_ITEM.value
                   else "element")
        current = {l.upstream_ref: l for l in self._trace_links.links_of_chart(chart.id)}
        new_set = set(new_source_refs)
        for ref in new_source_refs:
            if ref not in current or current[ref].status != TS.PRE_ESTABLISHED.value:
                self._pre_establish_link(chart.project_ref, ref, chart.id, up_type)
        for ref, link in current.items():
            if ref in new_set or link.status == TS.INVALID.value:
                continue
            trace_transition(TraceState(link.status), TraceEvent.INVALIDATE)
            self._trace_links.set_link_status(
                link.id, TS.INVALID.value, status_reason="来源引用已从图表移除",
            )
            log_event(_COMPONENT, "trace.link.invalidated", chart_ref=chart.id,
                      upstream_ref=ref, reason_code="source_removed", ok=True)

    # ------------------------------------------------------------------
    # P01-N08/N09：AI 源码建议（登记 LDM-015 → 用户采纳/修订采纳/拒绝）
    # ------------------------------------------------------------------

    def request_chart_suggestion(
        self, chart_ref: str, command: ChartSuggestionCommand,
    ) -> ChartSuggestionRequestResult:
        replay = self._chart_process.find_suggestion_request_by_idempotency(command.idempotency_key)
        if replay is not None:
            return ChartSuggestionRequestResult(
                status="submitted", suggestion_context_ref=replay,
                next_action="建议请求已受理（幂等重放）",
            )
        chart = self._require_chart(chart_ref)
        if chart.project_ref != command.project_ref:
            raise NotFound("图表不在当前项目")
        if chart.status != CS.DRAFT.value:
            return ChartSuggestionRequestResult(
                status="rejected_precheck",
                next_action="仅草稿中的图表可请求 AI 源码建议；待确认图表已冻结编辑",
            )
        context_ref = self._chart_process.create_suggestion_request(
            command.project_ref, chart.id, chart.draft_version,
            command.intent, command.operator_ref, command.idempotency_key,
        )
        run = self._model_orchestration.request_chart_suggestion(context_ref)
        log_event(_COMPONENT, "chart.suggestion.submitted", chart_ref=chart.id,
                  context_ref=context_ref, ok=True)
        return ChartSuggestionRequestResult(
            status="submitted", suggestion_context_ref=context_ref, agent_run_ref=run,
            next_action="AI 源码建议生成中；建议登记后需人工采纳才会更新图表",
        )

    def prepare_chart_suggestion(self, context_ref: str) -> Optional[dict]:
        """编排回调：组装建议送检上下文；不能送检时停靠并返回 None。"""
        req = self._chart_process.get_suggestion_request(context_ref)
        if req is None:
            return None
        chart = self._charts.get_chart(req.chart_ref)
        if chart is None or chart.status != CS.DRAFT.value:
            self._chart_process.mark_suggestion_stopped(
                context_ref, "图表已离开草稿状态，本次建议请求不再执行",
            )
            return None
        # 送检上下文按 kind 组装（06 B.3）：条目给 req 字段；业务知识给类型+内容。
        sources = []
        biz_map = None
        for s in _parse_source_refs(chart.source_refs):
            if s["kind"] == ChartSourceKind.REQUIREMENT_ITEM.value:
                item = self._items.get_item(s["ref"])
                if item is not None:
                    sources.append({
                        "kind": "requirement_item", "id": item.id, "req_no": item.req_no,
                        "expression": item.expression, "req_type": item.req_type,
                    })
            elif s["kind"] == ChartSourceKind.SUPPORTING_CONTENT.value and self._source_assets is not None:
                if biz_map is None:
                    biz_map = {
                        e.id: e for e in self._source_assets.list_project_elements_by_type(
                            chart.project_ref, _BUSINESS_ELEMENT_TYPES)
                    }
                el = biz_map.get(s["ref"])
                if el is not None:
                    sources.append({
                        "kind": "supporting_content", "id": el.id,
                        "element_type": el.element_type, "content": el.content,
                    })
        return {
            "project_ref": chart.project_ref,
            "chart": {
                "title": chart.title, "chart_type": chart.chart_type,
                "format": chart.format, "draft_version": chart.draft_version,
            },
            "sources": sources,
            "current_source": chart.source_code,
            "intent": req.intent,
        }

    def accept_chart_suggestion_result(self, context_ref: str, model_result_ref: str) -> None:
        """编排回调：建议结果承接。

        失败 → 停靠原因；修订建议 → LDM-015 登记待人工处理；
        创建初稿（kind=initial）→ 经受控校验自动应用为初稿并回填语义标题；
        自动应用不成立（版本被抢先编辑/校验未通过）时降级为待采纳建议卡，不静默。
        """
        result = self._model_results.read_stage_payload(model_result_ref)
        if result is None:
            raise RejectedTransition("图表源码建议类 LDM-015 不存在")
        if result.result_code == "suggestion_failed":
            self._chart_process.mark_suggestion_stopped(
                context_ref,
                (result.basis or "AI 建议生成失败") + "；可重试请求或继续人工编辑，不伪造候选建议",
            )
            log_event(_COMPONENT, "chart.suggestion.failed", level="WARN",
                      context_ref=context_ref, ok=False)
            return
        req = self._chart_process.get_suggestion_request(context_ref)
        if req is not None and req.kind == ChartSuggestionRequestKind.INITIAL.value:
            self._auto_apply_initial(req, model_result_ref)
            return
        log_event(_COMPONENT, "chart.suggestion.registered", context_ref=context_ref,
                  model_result_ref=model_result_ref, ok=True)

    def _auto_apply_initial(self, req, model_result_ref: str) -> None:
        """创建初稿自动应用：仍走唯一受控写入路径；不成立即降级为待采纳建议。"""
        chart = self._charts.get_chart(req.chart_ref)
        payload = self._model_results.read_stage_payload(model_result_ref)
        body = json.loads(payload.payload) if payload and payload.payload else {}
        source_code = str(body.get("source_code") or "")
        if (
            chart is None or chart.status != CS.DRAFT.value
            or chart.draft_version != req.base_draft_version  # 用户已抢先手工编辑
        ):
            log_event(_COMPONENT, "chart.initial.degraded", level="WARN",
                      context_ref=req.id, reason_code="draft_diverged", ok=False)
            return  # 留作待采纳建议卡（时间线可见）
        workspace = self._apply_validated_change(
            chart, source_code, ChartFormat(chart.format), ChartType(chart.chart_type),
            list(_source_ref_ids(chart.source_refs)),
            ChartSourceChangeOrigin.AI_INITIAL.value, model_result_ref,
            "创建初稿自动应用", req.operator_ref, f"{req.id}:initial-apply",
        )
        if workspace.validation_errors:
            log_event(_COMPONENT, "chart.initial.degraded", level="WARN",
                      context_ref=req.id, reason_code="validation_failed",
                      error_count=len(workspace.validation_errors), ok=False)
            return  # 生成结果未过受控校验：留作待采纳建议卡，用户可修订采纳
        title = str(body.get("title") or "").strip()
        if title:
            self._charts.set_chart_title(chart.id, title)  # 语义标题回填临时标题
        self._model_results.set_process_status(model_result_ref, "adopted")
        self._model_results.record_adoption(
            model_result_ref=model_result_ref, project_ref=chart.project_ref,
            stage="chart_source_suggestion", subject_type="chart_draft",
            subject_ref=chart.id, outcome="adopted", operator_ref=req.operator_ref,
            idempotency_key=f"{req.id}:initial-adoption",
        )
        log_event(_COMPONENT, "chart.initial.applied", chart_ref=chart.id,
                  context_ref=req.id, model_result_ref=model_result_ref, ok=True)

    def handle_chart_suggestion(
        self, chart_ref: str, suggestion_ref: str, command: ChartSuggestionHandlingCommand,
    ) -> ChartWorkspaceRead:
        chart = self._require_chart(chart_ref)
        if chart.project_ref != command.project_ref:
            raise NotFound("图表不在当前项目")
        payload = self._model_results.read_stage_payload(suggestion_ref)
        if payload is None or payload.stage != "chart_source_suggestion":
            raise NotFound("图表源码建议不存在")
        body = json.loads(payload.payload) if payload.payload else {}
        req = self._chart_process.get_suggestion_request(str(body.get("context_ref") or ""))
        if req is None or req.chart_ref != chart.id:
            raise NotFound("该建议不属于当前图表")

        status = self._model_results.read_process_status(suggestion_ref) or "pending"
        if status != "pending":
            # 幂等语义：同向重复处理返回当前工作区；反向处理默认拒绝
            expected = {
                CSH.ADOPT: "adopted", CSH.REVISE_AND_ADOPT: "revised_adopted",
                CSH.REJECT: "rejected",
            }[command.handling]
            if status == expected:
                return self._workspace(chart, next_action="建议已处理（幂等重放）")
            raise RejectedTransition("该建议已处理，不能再次处置")

        if command.handling is CSH.REJECT:
            if not (command.reason or "").strip():
                raise InvalidInput("拒绝 AI 建议必须填写理由")
            # 拒绝不删除 LDM-015、不改 LDM-012（§4.5 行5）
            self._model_results.set_process_status(suggestion_ref, "rejected")
            self._model_results.record_adoption(
                model_result_ref=suggestion_ref, project_ref=chart.project_ref,
                stage="chart_source_suggestion", subject_type="chart_draft",
                subject_ref=chart.id, outcome="rejected", operator_ref=command.operator_ref,
                idempotency_key=f"{command.idempotency_key}:adoption:{suggestion_ref}",
            )
            log_event(_COMPONENT, "chart.suggestion.handled", chart_ref=chart.id,
                      suggestion_ref=suggestion_ref, handling="reject", ok=True)
            return self._workspace(chart, next_action="建议已拒绝，图表草稿保持不变；可继续编辑或重新请求 AI")

        # 采纳 / 修订采纳：候选源码仍需进入受控校验（N09→N10）
        chart_transition(ChartState(chart.status), ChartEvent.APPLY_SOURCE_CHANGE)
        candidate = command.revised_source if command.handling is CSH.REVISE_AND_ADOPT else None
        source_code = (candidate if candidate is not None else str(body.get("source_code") or ""))
        origin = (ChartSourceChangeOrigin.AI_REVISED_ADOPTED
                  if command.handling is CSH.REVISE_AND_ADOPT
                  else ChartSourceChangeOrigin.AI_ADOPTED)
        workspace = self._apply_validated_change(
            chart, source_code, ChartFormat(chart.format), ChartType(chart.chart_type),
            list(_source_ref_ids(chart.source_refs)), origin.value,
            suggestion_ref, f"AI 建议{'修订' if origin is ChartSourceChangeOrigin.AI_REVISED_ADOPTED else ''}采纳",
            command.operator_ref, command.idempotency_key,
        )
        if not workspace.validation_errors:
            self._model_results.set_process_status(
                suggestion_ref,
                "revised_adopted" if origin is ChartSourceChangeOrigin.AI_REVISED_ADOPTED else "adopted",
            )
            self._model_results.record_adoption(
                model_result_ref=suggestion_ref, project_ref=chart.project_ref,
                stage="chart_source_suggestion", subject_type="chart_draft",
                subject_ref=chart.id,
                outcome=("adopted_with_revision"
                         if origin is ChartSourceChangeOrigin.AI_REVISED_ADOPTED else "adopted"),
                operator_ref=command.operator_ref,
                idempotency_key=f"{command.idempotency_key}:adoption:{suggestion_ref}",
            )
            log_event(_COMPONENT, "chart.suggestion.handled", chart_ref=chart.id,
                      suggestion_ref=suggestion_ref, handling=command.handling.value, ok=True)
            workspace = self._workspace(self._require_chart(chart.id),
                                        next_action=workspace.next_action)
        return workspace

    # ------------------------------------------------------------------
    # P02-N01：核对发起与待确认推进（冻结编辑）
    # ------------------------------------------------------------------

    def start_chart_verification(
        self, chart_ref: str, command: ChartVerificationCommand,
    ) -> ChartVerificationRequestResult:
        replay = self._chart_process.find_verification_request_by_idempotency(command.idempotency_key)
        if replay is not None:
            return ChartVerificationRequestResult(
                status="submitted", request_ref=replay,
                next_action="核对请求已受理（幂等重放）",
            )
        chart = self._require_chart(chart_ref)
        if chart.project_ref != command.project_ref:
            raise NotFound("图表不在当前项目")

        # 核对准入：受控表达、来源对象与预建立追溯成立（多由 P01 编辑循环维护）
        blocked = self._verification_entry_blockers(chart)
        if blocked:
            log_event(_COMPONENT, "chart.verification.rejected", level="WARN",
                      chart_ref=chart.id, reject_reason=blocked[0][:60], ok=False)
            return ChartVerificationRequestResult(
                status="rejected_precheck", next_action="；".join(blocked),
            )

        if chart.status == CS.DRAFT.value:
            nxt = chart_transition(ChartState.DRAFT, ChartEvent.START_VERIFICATION)
            assert nxt is ChartState.PENDING_CONFIRMATION
            self._charts.set_chart_status(chart.id, CS.PENDING_CONFIRMATION.value)
            log_event(_COMPONENT, "chart.status.transition", chart_ref=chart.id,
                      from_status=CS.DRAFT.value, to_status=CS.PENDING_CONFIRMATION.value,
                      sm_event=ChartEvent.START_VERIFICATION.value, ok=True)
        else:
            chart_transition(ChartState(chart.status), ChartEvent.REQUEST_REVERIFICATION)
            running = self._chart_process.latest_round_of_chart(chart.id)
            if running is not None and running.processing_status == CVS.VERIFYING.value:
                return ChartVerificationRequestResult(
                    status="rejected_precheck",
                    next_action="上一轮核对仍在进行中，请等待收束后再重新核对",
                )

        request_ref = self._chart_process.create_verification_request(
            command.project_ref, chart.id, chart.draft_version,
            command.operator_ref, command.idempotency_key,
        )
        self._chart_process.create_round(chart.id, request_ref, chart.draft_version)
        run = self._model_orchestration.request_chart_verification(request_ref)
        log_event(_COMPONENT, "chart.verification.submitted", chart_ref=chart.id,
                  request_ref=request_ref, draft_version=chart.draft_version, ok=True)
        return ChartVerificationRequestResult(
            status="submitted", request_ref=request_ref, agent_run_ref=run,
            next_action="AI 图文核对进行中；核对结果登记后进入用户复核",
        )

    def _verification_entry_blockers(self, chart: ChartRow) -> list[str]:
        if chart.status not in (CS.DRAFT.value, CS.PENDING_CONFIRMATION.value):
            return ["图表不处于可核对状态（已确认、已作废或退回修订中）"]
        blocked: list[str] = []
        if not chart.source_code.strip():
            blocked.append("图表尚无受控源码，请先完成源码编辑")
        sources = _parse_source_refs(chart.source_refs)
        if not sources:
            blocked.append("图表缺少来源对象，请回编辑循环补充来源")
        reason = self._source_precheck(chart.project_ref, sources) if sources else None
        if reason is not None:
            blocked.append(reason)
        links = self._trace_links.links_of_chart(chart.id)
        active = [l for l in links if l.status != TS.INVALID.value]
        if not active:
            blocked.append("图表缺少预建立追溯关系，请回编辑循环重新同步")
        elif any(l.status != TS.PRE_ESTABLISHED.value for l in active):
            blocked.append("存在待补全的追溯关系，请回编辑循环重新同步后再发起核对")
        return blocked

    # ------------------------------------------------------------------
    # P02-N02/N03/N04 编排回调：核对上下文组装 + 结果登记承接
    # ------------------------------------------------------------------

    def prepare_chart_verification(self, request_ref: str) -> Optional[dict]:
        req = self._chart_process.get_verification_request(request_ref)
        round_ = self._chart_process.round_of_request(request_ref)
        if req is None or round_ is None:
            return None
        chart = self._charts.get_chart(req.chart_ref)
        if chart is None or chart.status != CS.PENDING_CONFIRMATION.value:
            self._chart_process.finish_round(
                round_.id, CVS.FAILED.value,
                reason="图表已离开待确认状态，本轮核对不能继续",
            )
            return None
        # 送检上下文按 kind 组装（06 B.3）：条目给 req 字段；业务知识给类型+内容。
        sources = []
        biz_map = None
        for s in _parse_source_refs(chart.source_refs):
            if s["kind"] == ChartSourceKind.REQUIREMENT_ITEM.value:
                item = self._items.get_item(s["ref"])
                if item is not None:
                    sources.append({
                        "kind": "requirement_item", "id": item.id, "req_no": item.req_no,
                        "expression": item.expression, "req_type": item.req_type,
                    })
            elif s["kind"] == ChartSourceKind.SUPPORTING_CONTENT.value and self._source_assets is not None:
                if biz_map is None:
                    biz_map = {
                        e.id: e for e in self._source_assets.list_project_elements_by_type(
                            chart.project_ref, _BUSINESS_ELEMENT_TYPES)
                    }
                el = biz_map.get(s["ref"])
                if el is not None:
                    sources.append({
                        "kind": "supporting_content", "id": el.id,
                        "element_type": el.element_type, "content": el.content,
                    })
        links = [
            {"upstream_ref": l.upstream_ref, "relation_type": l.relation_type,
             "status": l.status}
            for l in self._trace_links.links_of_chart(chart.id)
            if l.status != TS.INVALID.value
        ]
        return {
            "project_ref": chart.project_ref,
            "chart": {
                "title": chart.title, "chart_type": chart.chart_type,
                "format": chart.format, "source_code": chart.source_code,
            },
            "sources": sources,
            "trace_links": links,
        }

    def accept_chart_verification_result(self, request_ref: str, model_result_ref: str) -> None:
        round_ = self._chart_process.round_of_request(request_ref)
        if round_ is None:
            raise RejectedTransition("该核对请求没有进行中的轮次，结果不可承接")
        result = self._model_results.read_stage_payload(model_result_ref)
        if result is None:
            raise RejectedTransition("图文核对类 LDM-015 不存在")

        if result.result_code == "verification_failed":
            # AI 核对失败：不得降级为纯人工确认（§5.3）；保留重试/退回入口
            self._chart_process.finish_round(
                round_.id, CVS.FAILED.value, model_result_ref=model_result_ref,
                reason=(result.basis or "图文核对失败") + "；可重试核对、稍后处理或退回修订，不得降级为纯人工确认",
            )
            log_event(_COMPONENT, "chart.verification.failed", level="WARN",
                      round_ref=round_.id, chart_ref=round_.chart_ref, ok=False)
            return

        body = json.loads(result.payload) if result.payload else {}
        accepted = 0
        for entry in body.get("findings", []):
            if not isinstance(entry, dict):
                continue
            ftype = str(entry.get("finding_type") or "")
            summary = str(entry.get("summary") or "").strip()
            if ftype not in _CHART_FINDING_TYPES or not summary:
                continue  # 结构不完整的发现项不可承接
            refs = entry.get("related_source_refs")
            self._chart_process.add_finding(
                round_.id, round_.chart_ref, ftype, summary,
                str(entry.get("basis_summary") or ""),
                json.dumps([str(r) for r in refs] if isinstance(refs, list) else []),
                model_result_ref,
            )
            accepted += 1

        if accepted == 0:
            self._chart_process.finish_round(
                round_.id, CVS.FAILED.value, model_result_ref=model_result_ref,
                reason="模型结果结构不可承接（缺少必要核对结构）；可重试核对或退回修订",
            )
            log_event(_COMPONENT, "chart.verification.result_unacceptable", level="WARN",
                      round_ref=round_.id, chart_ref=round_.chart_ref, ok=False)
            return

        self._chart_process.finish_round(
            round_.id, CVS.COMPLETED.value, model_result_ref=model_result_ref,
        )
        log_event(_COMPONENT, "chart.verification.completed", round_ref=round_.id,
                  chart_ref=round_.chart_ref, finding_count=accepted, ok=True)

    # ------------------------------------------------------------------
    # P02-N05：用户复核发现项
    # ------------------------------------------------------------------

    def submit_chart_finding_decision(
        self, chart_ref: str, finding_ref: str, command: ChartFindingDecisionCommand,
    ) -> ChartWorkspaceRead:
        replay = self._chart_process.find_finding_decision_by_idempotency(command.idempotency_key)
        if replay is not None:
            return self.read_chart_workspace(chart_ref)

        chart = self._require_chart(chart_ref)
        finding = self._chart_process.get_finding(finding_ref)
        if finding is None or finding.chart_ref != chart.id:
            raise NotFound("核对发现项不存在或不属于该图表")
        if finding.decision is not None:
            raise RejectedTransition("该发现项已复核，不能重复裁定")
        latest = self._chart_process.latest_round_of_chart(chart.id)
        if latest is None or latest.id != finding.round_ref or latest.invalidated:
            raise RejectedTransition("该发现项所属核对轮次已失效，请重新核对后复核")
        if latest.chart_draft_version != chart.draft_version:
            raise RejectedTransition("核对轮次与当前草稿版本不一致，请重新核对")

        reason = (command.reason or "").strip() or None
        if command.decision is CFD.REJECTED and not reason:
            raise InvalidInput("拒绝 AI 发现项必须记录拒绝理由")

        self._chart_process.record_finding_decision(
            finding_ref, command.decision.value, reason,
            command.operator_ref, command.idempotency_key,
        )
        if finding.model_result_ref:  # 采纳结论明细（口径设计 §4 chart_verification 行）
            self._model_results.record_adoption(
                model_result_ref=finding.model_result_ref, project_ref=chart.project_ref,
                stage="chart_verification", subject_type="finding", subject_ref=finding_ref,
                outcome="adopted" if command.decision is CFD.ACCEPTED else "rejected",
                operator_ref=command.operator_ref,
                idempotency_key=f"{command.idempotency_key}:adoption:{finding_ref}",
            )
        log_event(_COMPONENT, "chart.finding.decided", chart_ref=chart.id,
                  finding_ref=finding_ref, decision=command.decision.value, ok=True)
        return self.read_chart_workspace(chart_ref)

    # ------------------------------------------------------------------
    # P02-N06/N08/N09：确认准入裁定 + 图表确认与追溯正式确立（同批成立）
    # ------------------------------------------------------------------

    def _confirmation_blockers(
        self, chart: ChartRow, round_: Optional[ChartVerificationRoundRow],
        findings: list[ChartFindingRow], links: list[TraceLinkRow],
    ) -> list[str]:
        """N06 确认准入校验：返回阻断原因列表；空 = 允许确认。"""
        if chart.status == CS.CONFIRMED.value:
            return ["图表已确认"]
        if chart.status != CS.PENDING_CONFIRMATION.value:
            return ["图表不处于待确认状态"]
        if round_ is None:
            return ["尚无有效图文核对结果；AI 核对必须在确认前完成"]
        if round_.processing_status == CVS.VERIFYING.value:
            return ["图文核对进行中"]
        if round_.invalidated:
            return [round_.invalidated_reason or "核对轮次已失效，需重新核对"]
        if round_.processing_status == CVS.FAILED.value:
            return [round_.reason or "图文核对失败；不得降级为纯人工确认，请重试核对或退回修订"]
        if round_.chart_draft_version != chart.draft_version:
            return ["核对轮次与当前草稿版本不一致，需重新核对"]
        blocked: list[str] = []
        if any(f.decision is None for f in findings):
            blocked.append("存在未复核的核对发现项")
        accepted_blockers = sorted({
            f.finding_type for f in findings
            if f.decision == CFD.ACCEPTED.value and f.finding_type in _BLOCKING_FINDING_TYPES
        })
        if accepted_blockers:
            blocked.append("存在被接受的阻断发现项（" + "、".join(accepted_blockers) + "），不得确认")
        for f in findings:
            if f.issue_ref:
                issue = self._issues.get_issue(f.issue_ref)
                if issue is not None and issue.status != IssueStatus.CLOSED.value:
                    blocked.append("存在关联问题项未闭合，不得确认")
                    break
        active = [l for l in links if l.status != TS.INVALID.value]
        if not active:
            blocked.append("缺少预建立追溯关系")
        elif any(l.status != TS.PRE_ESTABLISHED.value for l in active):
            blocked.append("存在待补全的追溯关系，不得确认")
        return blocked

    def confirm_chart(
        self, chart_ref: str, command: ChartConfirmationCommand,
    ) -> ChartConfirmationResult:
        replay = self._chart_process.find_round_confirmation_by_idempotency(command.idempotency_key)
        if replay is not None:
            chart = self._require_chart(chart_ref)
            return ChartConfirmationResult(
                status="confirmed", chart_ref=chart_ref,
                chart_status=CS(chart.status),
                next_action="图表已确认（幂等重放）",
            )

        chart = self._require_chart(chart_ref)
        if chart.project_ref != command.project_ref:
            raise NotFound("图表不在当前项目")
        round_ = self._chart_process.latest_round_of_chart(chart.id)
        findings = self._chart_process.findings_of_round(round_.id) if round_ else []
        links = self._trace_links.links_of_chart(chart.id)
        blocked = self._confirmation_blockers(chart, round_, findings, links)
        if blocked:
            log_event(_COMPONENT, "chart.confirm.blocked", level="WARN",
                      chart_ref=chart.id, reject_reason=blocked[0][:60], ok=False)
            return ChartConfirmationResult(
                status="rejected_precheck", chart_ref=chart.id,
                chart_status=CS(chart.status), next_action="；".join(blocked),
            )

        # N08 图表正式确认 + N09 追溯正式确立：同一会话事务内完成，任一失败整体回滚
        nxt = chart_transition(ChartState(chart.status), ChartEvent.CONFIRM)
        assert nxt is ChartState.CONFIRMED
        accepted = sum(1 for f in findings if f.decision == CFD.ACCEPTED.value)
        rejected = sum(1 for f in findings if f.decision == CFD.REJECTED.value)
        conclusion = f"图文核对收束：接受 {accepted} 项、拒绝 {rejected} 项，无阻断发现项"
        basis = (
            f"确认依据：图文核对类 LDM-015 {round_.model_result_ref}；"
            f"用户复核收束（接受 {accepted}/拒绝 {rejected}）；预建立追溯关系全部成立"
        )
        self._charts.set_chart_status(chart.id, CS.CONFIRMED.value)
        self._charts.record_confirmation(chart.id, conclusion, basis, command.operator_ref)
        established = 0
        for link in links:
            if link.status == TS.INVALID.value:
                continue
            trace_transition(TraceState(link.status), TraceEvent.ESTABLISH)
            self._trace_links.set_link_status(
                link.id, TS.EFFECTIVE.value,
                established_basis=f"随图表确认同批确立；确认依据引用 LDM-015 {round_.model_result_ref}",
            )
            established += 1
            log_event(_COMPONENT, "trace.link.established", chart_ref=chart.id,
                      link_ref=link.id, upstream_ref=link.upstream_ref, ok=True)
        self._chart_process.record_round_confirmation(
            round_.id, "confirmed", command.idempotency_key,
        )
        log_event(_COMPONENT, "chart.status.transition", chart_ref=chart.id,
                  from_status=CS.PENDING_CONFIRMATION.value, to_status=CS.CONFIRMED.value,
                  sm_event=ChartEvent.CONFIRM.value, trace_established=established, ok=True)
        return ChartConfirmationResult(
            status="confirmed", chart_ref=chart.id, chart_status=CS.CONFIRMED,
            trace_established_count=established,
            next_action="图表已确认，追溯关系已正式确立；可进入追溯查询或文档编排候选",
        )

    # ------------------------------------------------------------------
    # P02-N07：不通过分支（退回修订 / 作废 / 重回编辑 / 转问题项）
    # ------------------------------------------------------------------

    def return_chart_for_revision(
        self, chart_ref: str, command: ChartLifecycleCommand,
    ) -> ChartWorkspaceRead:
        chart = self._require_chart(chart_ref)
        nxt = chart_transition(ChartState(chart.status), ChartEvent.RETURN_FOR_REVISION)
        assert nxt is ChartState.RETURNED_FOR_REVISION
        reason = (command.reason or "").strip() or "用户退回修订"
        self._charts.set_chart_status(chart.id, CS.RETURNED_FOR_REVISION.value, reason)
        for link in self._trace_links.links_of_chart(chart.id):
            if link.status != TS.PRE_ESTABLISHED.value:
                continue
            trace_transition(TraceState.PRE_ESTABLISHED, TraceEvent.MARK_SUSPECT)
            self._trace_links.set_link_status(
                link.id, TS.SUSPECT_PENDING_REVIEW.value, status_reason="图表退回修订，关系待补全",
            )
        self._chart_process.invalidate_rounds_of_chart(chart.id, "图表退回修订，旧核对轮次失效")
        log_event(_COMPONENT, "chart.status.transition", chart_ref=chart.id,
                  from_status=chart.status, to_status=CS.RETURNED_FOR_REVISION.value,
                  sm_event=ChartEvent.RETURN_FOR_REVISION.value, ok=True)
        return self._workspace(self._require_chart(chart.id),
                               next_action="图表已退回修订；重回编辑后需重新核对")

    def resume_chart_editing(
        self, chart_ref: str, command: ChartLifecycleCommand,
    ) -> ChartWorkspaceRead:
        chart = self._require_chart(chart_ref)
        nxt = chart_transition(ChartState(chart.status), ChartEvent.RESUME_EDITING)
        assert nxt is ChartState.DRAFT
        self._charts.set_chart_status(chart.id, CS.DRAFT.value, None)
        # 待补全关系随重回编辑重新同步（来源仍确认态时恢复预建立）
        for link in self._trace_links.links_of_chart(chart.id):
            if link.status != TS.SUSPECT_PENDING_REVIEW.value:
                continue
            item = self._items.get_item(link.upstream_ref)
            if item is not None and item.status == IS.CONFIRMED.value:
                trace_transition(TraceState.SUSPECT_PENDING_REVIEW, TraceEvent.SYNC)
                self._trace_links.set_link_status(link.id, TS.PRE_ESTABLISHED.value, None)
        log_event(_COMPONENT, "chart.status.transition", chart_ref=chart.id,
                  from_status=chart.status, to_status=CS.DRAFT.value,
                  sm_event=ChartEvent.RESUME_EDITING.value, ok=True)
        return self._workspace(self._require_chart(chart.id),
                               next_action="已重回源码编辑循环；编辑后需重新发起核对")

    def void_chart(
        self, chart_ref: str, command: ChartLifecycleCommand,
    ) -> ChartWorkspaceRead:
        chart = self._require_chart(chart_ref)
        nxt = chart_transition(ChartState(chart.status), ChartEvent.VOID)
        assert nxt is ChartState.VOIDED
        reason = (command.reason or "").strip() or "用户作废图表"
        self._charts.set_chart_status(chart.id, CS.VOIDED.value, reason)
        for link in self._trace_links.links_of_chart(chart.id):
            if link.status == TS.INVALID.value:
                continue
            if link.status == TS.EFFECTIVE.value:
                continue  # 有效关系不在本迭代作废路径内（确认后不可作废，状态机已挡）
            trace_transition(TraceState(link.status), TraceEvent.INVALIDATE)
            self._trace_links.set_link_status(
                link.id, TS.INVALID.value, status_reason="图表已作废，关系失效",
            )
        self._chart_process.invalidate_rounds_of_chart(chart.id, "图表已作废")
        log_event(_COMPONENT, "chart.status.transition", chart_ref=chart.id,
                  from_status=chart.status, to_status=CS.VOIDED.value,
                  sm_event=ChartEvent.VOID.value, ok=True)
        return self._workspace(self._require_chart(chart.id),
                               next_action="图表已作废；如仍需图表请重新创建")

    def create_issue_from_finding(
        self, chart_ref: str, finding_ref: str, command: ChartIssueCommand,
    ) -> IssueRead:
        replay = self._issues.find_issue_by_idempotency(command.idempotency_key)
        if replay is not None:
            issue = self._issues.get_issue(replay)
            assert issue is not None
            return self._project_issue(issue)

        chart = self._require_chart(chart_ref)
        finding = self._chart_process.get_finding(finding_ref)
        if finding is None or finding.chart_ref != chart.id:
            raise NotFound("核对发现项不存在或不属于该图表")
        if finding.issue_ref is not None:
            issue = self._issues.get_issue(finding.issue_ref)
            if issue is not None:
                return self._project_issue(issue)

        issue_type = command.issue_type or _FINDING_ISSUE_TYPE.get(
            finding.finding_type, IssueType.OTHER,
        )
        links = [l for l in self._trace_links.links_of_chart(chart.id)
                 if l.status != TS.INVALID.value]
        issue_ref = self._issues.create_issue(
            command.project_ref, issue_type.value,
            (command.title or f"图表核对问题：{finding.summary[:80]}").strip(),
            (command.description or finding.basis_summary or finding.summary).strip(),
            "chart_verification", chart.id, finding_ref,
            json.dumps([l.id for l in links]), command.operator_ref,
            command.idempotency_key,
        )
        self._chart_process.set_finding_issue(finding_ref, issue_ref)
        for link in links:
            self._trace_links.set_link_issue(link.id, issue_ref)  # 关系保持未确认并关联问题项
        if finding.model_result_ref:
            self._model_results.set_process_status(finding.model_result_ref, "transferred_to_issue")
            self._model_results.record_adoption(
                model_result_ref=finding.model_result_ref, project_ref=chart.project_ref,
                stage="chart_verification", subject_type="finding", subject_ref=finding_ref,
                outcome="transferred_to_issue", operator_ref=command.operator_ref,
                idempotency_key=f"{command.idempotency_key}:adoption:{finding_ref}",
            )
        log_event(_COMPONENT, "issue.created", chart_ref=chart.id, issue_ref=issue_ref,
                  finding_ref=finding_ref, issue_type=issue_type.value, ok=True)
        issue = self._issues.get_issue(issue_ref)
        assert issue is not None
        return self._project_issue(issue)

    # ------------------------------------------------------------------
    # 问题项 / 追溯 读视图
    # ------------------------------------------------------------------

    def _project_issue(self, issue: IssueRow) -> IssueRead:
        return IssueRead(
            issue_ref=issue.id, issue_type=IssueType(issue.issue_type),
            status=IssueStatus(issue.status), title=issue.title,
            description=issue.description, origin_kind=issue.origin_kind,
            chart_ref=issue.chart_ref, finding_ref=issue.finding_ref,
            trace_link_refs=list(json.loads(issue.trace_link_refs or "[]")),
            created_by=issue.created_by, created_at=issue.created_at,
        )

    def list_issues(self, project_ref: str) -> IssueListRead:
        rows = self._issues.issues_of_project(project_ref)
        return IssueListRead(
            project_ref=project_ref,
            issues=[self._project_issue(i) for i in rows],
            next_action=None if rows else "暂无问题项；图表核对阻断发现项可转入问题项",
        )

    def list_trace_links(
        self, project_ref: str, status: Optional[str] = None,
        chart_ref: Optional[str] = None,
    ) -> TraceLinkListRead:
        rows = self._trace_links.links_of_project(project_ref, status, chart_ref)
        links: list[TraceLinkRead] = []
        chart_titles: dict[str, str] = {}
        for link in rows:
            item = self._items.get_item(link.upstream_ref)
            label = f"{item.req_no} {item.expression[:30]}" if item else None
            read = self._project_link(link, label)
            if link.downstream_ref not in chart_titles:
                chart = self._charts.get_chart(link.downstream_ref)
                chart_titles[link.downstream_ref] = chart.title if chart else ""
            read = read.model_copy(update={
                "downstream_label": chart_titles.get(link.downstream_ref) or None,
            })
            links.append(read)
        return TraceLinkListRead(project_ref=project_ref, links=links)
