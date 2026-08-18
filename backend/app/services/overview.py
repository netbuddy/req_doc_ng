"""需求资产目录服务最小面（AEP-052 资产盘点 + AEP-072 跨任务状态聚合）。

只读投影（UINV-21/22 / 页面设计 §1）：阶段状态由既有事实源按下方迁移表实时派生，
不建表、不写状态、不持第二份事实源；「恢复」由需求管理工作台经既有读端点回放，
本服务只提供恢复锚点（intake/parse/formation context refs）。

派生迁移表（事实源；docs/iterations/OVW-001/覆盖标记表.md §1 与之逐行对应）：
以 IntakeRequest（接入请求上下文）为流程根；多识别请求/多批次取「到达最深 + 最新」代表。

| # | 链状态                                | ①接入        | ②分析          | ③形成        | current       | resumable |
|---|--------------------------------------|-------------|---------------|-------------|---------------|-----------|
| 1 | 无接入记录、无停靠                     | in_progress | -             | -           | intake        | 是        |
| 2 | 无接入记录、有停靠                     | stopped     | -             | -           | intake        | 是(重试)  |
| 3 | 结论=returned_for_supplement          | stopped     | -             | -           | intake        | 否        |
| 4 | 结论=excluded                         | stopped     | -             | -           | intake        | 否        |
| 5 | accepted、0 识别请求                  | done        | not_started   | -           | analysis      | 是        |
| 6 | 有识别请求、无结果、无停靠             | done        | in_progress   | -           | analysis      | 是        |
| 7 | 有识别请求、无结果、有停靠             | done        | stopped       | -           | analysis      | 是(重试)  |
| 8 | parse_status=unprocessable            | done        | stopped(死路) | -           | analysis      | 否        |
| 9 | parsed、无已确认 live 要素            | done        | in_progress   | not_started | analysis      | 是        |
|10 | parsed、有确认但无可形成类型          | done        | in_progress   | not_started | analysis      | 是        |
|11 | ≥1 已确认可形成要素、0 批次           | done        | done          | not_started | itemFormation | 是        |
|12 | 有形成批次、0 条目、无停靠             | done        | done          | in_progress | itemFormation | 是        |
|13 | 有形成批次、0 条目、有停靠             | done        | done          | stopped     | itemFormation | 是(重试)  |
|14 | 有形成批次、≥1 条目                   | done        | done          | done        | itemFormation | 是(查看)  |

阶段④条目评审恒 not_started（SCN-003 未上线，前端标「待接入」）。

终结态处置（OVW-001 修订 2026-07-10）：已放弃（IntakeRequest.dismissed_at 非空）的接入根
不进入投影（软删，行保留可审计）；行 3/4（需补充/已排除）标 dismissable=True，开放
「继续编辑」（AEP-112 预填后经 AEP-001 重提为新流程）与「放弃本次接入」（AEP-111）。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from app.api.schemas import (
    FlowDismissRead,
    FlowStageStatusRead,
    IntakePrefillRead,
    OverviewConversionChainRead,
    OverviewCoverageRead,
    OverviewRead,
    OverviewStatMetricRead,
    OverviewTraceRiskRead,
    OverviewTypeBridgeRead,
    RequirementFlowRead,
)
from app.domain.enums import (
    ElementProcessStatus,
    ELEMENT_TO_ITEM_TYPE,
    IntakeConclusion,
    ITEMIZABLE_ELEMENT_TYPES,
    MaterialParseStatus,
    RequirementItemStatus,
)
from app.domain.errors import NotFound, RejectedTransition
from app.log import log_event
from app.repositories.overview_read import (
    ElementFact,
    FormationRequestFact,
    IntakeRequestFact,
    ItemFact,
    OverviewReadRepository,
    ParseRequestFact,
    ParseResultFact,
    ProjectOverviewFacts,
)

STAGES = ("intake", "analysis", "itemFormation", "itemReview")

_STAGE_LABELS = {
    "intake": "材料接入",
    "analysis": "知识抽取",
    "itemFormation": "条目形成",
    "itemReview": "条目评审",
}
_STATUS_LABELS = {
    "done": "完成",
    "in_progress": "进行中",
    "stopped": "停靠",
    "not_started": "未开始",
}

# 五类型的规范输出顺序（类型瓦片与数字桥共用）。element_type → 类型 key 的映射不在此重写：
# 一律用 ELEMENT_TO_ITEM_TYPE（enums.py 单一来源），其值域即下列五个 key。
_TYPE_KEY_ORDER = ("functional", "quality", "constraint", "data", "interface")


def _iso_utc(value: datetime) -> str:
    """无时区（SQLite 回读）按 UTC 归一化，保证幂等回放的 ISO 串稳定。"""
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


def _live(elements: list[ElementFact]) -> list[ElementFact]:
    """资产口径的存量要素：排除被拆分/合并替代与已撤销。"""
    return [
        e for e in elements
        if not e.superseded and e.process_status != ElementProcessStatus.REVOKED.value
    ]


def _has_confirmed_formable(elements: list[ElementFact]) -> bool:
    return any(
        e.process_status == ElementProcessStatus.CONFIRMED.value
        and e.element_type in ITEMIZABLE_ELEMENT_TYPES
        for e in _live(elements)
    )


# ---- 需求转化链与数字桥（同一次事实载入派生；口径见任务卡 T20260724-overview-conversion-chain）----


@dataclass(frozen=True)
class _ConversionIndex:
    """转化链、数字桥、类型计数三者共用的派生索引（一次事实载入内建成，禁二次查库）。"""

    live_requirement: list[ElementFact]           # 需求类存量知识项（可形成条目的五类）
    material_of_element: dict[str, str]           # 知识项 id → 材料 id（经解析结果解析）
    materials_with_requirement: set[str]          # 识别出需求类知识项的材料
    materials_formed: set[str]                    # 其中已有条目产出的材料
    items_by_element: dict[str, list[ItemFact]]   # 知识项 id → 引用它为来源的条目


def _build_conversion_index(
    facts: ProjectOverviewFacts, live_elements: list[ElementFact]
) -> _ConversionIndex:
    material_by_parse_result = {r.id: r.material_ref for r in facts.parse_results}
    live_requirement = [e for e in live_elements if e.element_type in ITEMIZABLE_ELEMENT_TYPES]

    material_of_element = {
        e.id: material_by_parse_result[e.parse_result_ref]
        for e in live_requirement
        if e.parse_result_ref in material_by_parse_result
    }
    materials_with_requirement = set(material_of_element.values())
    # 直建条目（无知识项来源）的 parse_result_ref 不指向任何解析结果，故取交集：
    # 不能让一条无来源条目把一份并不存在的材料算进「已形成」。
    materials_formed = {
        material_by_parse_result[i.parse_result_ref]
        for i in facts.items
        if i.parse_result_ref in material_by_parse_result
    } & materials_with_requirement

    items_by_element: dict[str, list[ItemFact]] = {}
    for item in facts.items:
        for ref in item.source_element_refs:
            items_by_element.setdefault(ref, []).append(item)

    return _ConversionIndex(
        live_requirement=live_requirement,
        material_of_element=material_of_element,
        materials_with_requirement=materials_with_requirement,
        materials_formed=materials_formed,
        items_by_element=items_by_element,
    )


def _derive_conversion_chain(
    facts: ProjectOverviewFacts,
    live_elements: list[ElementFact],
    idx: _ConversionIndex,
    status_counter: Counter[str],
) -> OverviewConversionChainRead:
    confirmed = sum(
        1 for e in idx.live_requirement
        if e.process_status == ElementProcessStatus.CONFIRMED.value
    )
    direct = sum(1 for i in facts.items if not i.source_element_refs)
    closed = (
        status_counter.get(RequirementItemStatus.SUPERSEDED.value, 0)
        + status_counter.get(RequirementItemStatus.TERMINATED.value, 0)
    )
    return OverviewConversionChainRead(
        elements_total=len(live_elements),
        elements_requirement=len(idx.live_requirement),
        elements_other=len(live_elements) - len(idx.live_requirement),
        elements_confirmed=confirmed,
        elements_pending=len(idx.live_requirement) - confirmed,
        materials_with_requirement=len(idx.materials_with_requirement),
        materials_formed=len(idx.materials_formed),
        materials_unformed=len(idx.materials_with_requirement) - len(idx.materials_formed),
        items_total=len(facts.items),
        items_pending=status_counter.get(RequirementItemStatus.PENDING_CONFIRMATION.value, 0),
        items_confirmed=status_counter.get(RequirementItemStatus.CONFIRMED.value, 0),
        items_closed=closed,
        items_sourced=len(facts.items) - direct,
        items_direct=direct,
    )


def _derive_type_bridges(
    facts: ProjectOverviewFacts, idx: _ConversionIndex
) -> list[OverviewTypeBridgeRead]:
    """按需求类型给全五份去向账（一次下发，前端切换不再请求）。"""
    elements_by_type: dict[str, list[ElementFact]] = {}
    for e in idx.live_requirement:
        elements_by_type.setdefault(ELEMENT_TO_ITEM_TYPE[e.element_type], []).append(e)
    items_by_type: dict[str, list[ItemFact]] = {}
    for i in facts.items:
        items_by_type.setdefault(i.req_type, []).append(i)

    bridges: list[OverviewTypeBridgeRead] = []
    for key in _TYPE_KEY_ORDER:
        elements = elements_by_type.get(key, [])
        confirmed = [
            e for e in elements
            if e.process_status == ElementProcessStatus.CONFIRMED.value
        ]
        entered = 0
        material_pending = 0
        not_adopted = 0
        produced: dict[str, ItemFact] = {}  # 按条目 id 去重：归并而成的条目只算一次
        for e in confirmed:
            linked = idx.items_by_element.get(e.id, [])
            if linked:
                entered += 1
                for item in linked:
                    produced[item.id] = item
            elif idx.material_of_element.get(e.id) in idx.materials_formed:
                # 所在材料执行过形成，但这个知识项没被任何条目采用
                not_adopted += 1
            else:
                material_pending += 1

        same_type = sum(1 for item in produced.values() if item.req_type == key)
        type_items = items_by_type.get(key, [])
        direct = sum(1 for i in type_items if not i.source_element_refs)
        bridges.append(OverviewTypeBridgeRead(
            key=key,
            elements_total=len(elements),
            elements_confirmed=len(confirmed),
            elements_pending=len(elements) - len(confirmed),
            entered_formation=entered,
            not_formed=material_pending + not_adopted,
            not_formed_material_pending=material_pending,
            not_formed_not_adopted=not_adopted,
            items_from_elements_same_type=same_type,
            items_from_elements_other_type=len(produced) - same_type,
            items_total=len(type_items),
            items_sourced=len(type_items) - direct,
            items_direct=direct,
        ))
    return bridges


class _Indexes:
    """单次载入事实的字典索引（O(1) 解析每条流程）。"""

    def __init__(self, facts: ProjectOverviewFacts) -> None:
        self.record_by_ctx = {r.context_ref: r for r in facts.intake_records}
        self.parse_requests_by_material: dict[str, list[ParseRequestFact]] = {}
        for pr in facts.parse_requests:
            self.parse_requests_by_material.setdefault(pr.material_ref, []).append(pr)
        self.parse_result_by_ctx: dict[str, ParseResultFact] = {
            r.context_ref: r for r in facts.parse_results
        }
        self.elements_by_pr: dict[str, list[ElementFact]] = {}
        for e in facts.elements:
            self.elements_by_pr.setdefault(e.parse_result_ref, []).append(e)
        self.items_by_pr: dict[str, list] = {}
        for i in facts.items:
            self.items_by_pr.setdefault(i.parse_result_ref, []).append(i)
        self.formations_by_pr: dict[str, list[FormationRequestFact]] = {}
        for f in facts.formation_requests:
            self.formations_by_pr.setdefault(f.parse_result_ref, []).append(f)


class OverviewService:
    """AEP-052/072 只读投影 + 终结态处置（AEP-111 软删 / AEP-112 预填读，OVW-001 修订 2026-07-10）。

    覆盖度/缺口/可疑不在本服务重算：注入 `TraceAnalysisService` 转读其 AEP-062/063/064
    口径（04A §10 边界：来自追溯分析服务），未注入时对应组保持空（前端显示待接入）。
    """

    def __init__(self, read_repo: OverviewReadRepository, trace_service=None) -> None:
        self._repo = read_repo
        self._trace = trace_service

    # ---- AEP-072 流程投影 ----

    def list_requirement_flows(self, project_ref: str) -> list[RequirementFlowRead]:
        facts = self._load(project_ref)
        idx = _Indexes(facts)
        flows = [self._resolve_flow(root, idx) for root in facts.intake_requests]
        flows.sort(key=lambda f: f.updated_at, reverse=True)
        log_event(
            "overview", "aep072.flows_resolved",
            project_ref=project_ref, flows=len(flows),
            resumable=sum(1 for f in flows if f.resumable),
        )
        return flows

    # ---- AEP-052 + AEP-072 合并读 ----

    def read_project_overview(self, project_ref: str) -> OverviewRead:
        facts = self._load(project_ref)
        idx = _Indexes(facts)
        flows = [self._resolve_flow(root, idx) for root in facts.intake_requests]
        flows.sort(key=lambda f: f.updated_at, reverse=True)

        live_elements = _live(facts.elements)
        status_counter: Counter[str] = Counter(i.status for i in facts.items)
        conversion_index = _build_conversion_index(facts, live_elements)
        chain = _derive_conversion_chain(facts, live_elements, conversion_index, status_counter)
        bridges = _derive_type_bridges(facts, conversion_index)
        if chain.items_pending + chain.items_confirmed + chain.items_closed != chain.items_total:
            # 界面「三块之和＝资产盘点条目数」的对账行由此保证；不等即出现了四态之外的状态码
            log_event(
                "overview", "aep052.item_status_unaccounted", level="WARN",
                msg="条目状态计数之和不等于条目总数，状态区对账行将不成立",
                project_ref=project_ref, items=chain.items_total,
                accounted=chain.items_pending + chain.items_confirmed + chain.items_closed,
            )

        charts_count, documents_count, issues_count = self._repo.count_catalog_assets(project_ref)
        coverage: list[OverviewCoverageRead] = []
        trace_risk = None
        if self._trace is not None:
            directions = self._trace.read_coverage(project_ref).directions
            coverage = [
                OverviewCoverageRead(key=d.key, covered=d.covered, total=d.total, ratio=d.ratio)
                for d in directions
            ]
            counts = self._trace.read_entry(project_ref).counts
            trace_risk = OverviewTraceRiskRead(
                gaps=counts.gaps, suspects=counts.suspect, issues=issues_count,
            )

        overview = OverviewRead(
            project_ref=project_ref,
            asset_metrics=[
                OverviewStatMetricRead(key="materials", value=len(facts.materials)),
                OverviewStatMetricRead(key="elements", value=len(live_elements)),
                OverviewStatMetricRead(key="items", value=len(facts.items)),
                OverviewStatMetricRead(key="charts", value=charts_count),
                OverviewStatMetricRead(key="documents", value=documents_count),
                OverviewStatMetricRead(key="issues", value=issues_count),
            ],
            coverage=coverage,
            trace_risk=trace_risk,
            # 类型计数取自数字桥同一份分组：保证「阶段一·需求类」恒等于五个类型瓦片之和
            requirement_type_metrics=[
                OverviewStatMetricRead(key=b.key, value=b.elements_total) for b in bridges
            ],
            requirement_status_metrics=[
                OverviewStatMetricRead(key="pending", value=chain.items_pending),
                OverviewStatMetricRead(key="confirmed", value=chain.items_confirmed),
                OverviewStatMetricRead(key="closed", value=chain.items_closed),
            ],
            flows=flows,
            conversion_chain=chain,
            type_bridge=bridges,
        )
        log_event(
            "overview", "aep052.overview_read",
            project_ref=project_ref,
            materials=len(facts.materials), elements=len(live_elements),
            items=len(facts.items), flows=len(flows),
            # 转化链关键计数（排障时可直接对照界面四节点与对账行）
            elements_requirement=chain.elements_requirement,
            elements_confirmed=chain.elements_confirmed,
            materials_with_requirement=chain.materials_with_requirement,
            materials_formed=chain.materials_formed,
            items_pending=chain.items_pending, items_confirmed=chain.items_confirmed,
            items_closed=chain.items_closed, items_direct=chain.items_direct,
        )
        return overview

    # ---- 终结态流程处置（OVW-001 修订 2026-07-10）----

    _TERMINAL_CONCLUSIONS = (
        IntakeConclusion.RETURNED_FOR_SUPPLEMENT.value,
        IntakeConclusion.EXCLUDED.value,
    )

    def read_intake_prefill(self, project_ref: str, context_ref: str) -> IntakePrefillRead:
        """AEP-112 继续编辑预填：读旧上下文提交内容；重提仍走 AEP-001（新上下文新流程）。"""
        if not self._repo.project_exists(project_ref):
            raise NotFound(f"项目不存在：{project_ref}")
        source = self._repo.read_intake_source(project_ref, context_ref)
        if source is None:
            raise NotFound("接入请求上下文不存在或不属于该项目")
        raw_text, source_note = source
        # raw_text 属用户内容：日志只记长度，不记原文
        log_event(
            "overview", "aep112.intake_prefill_read",
            project_ref=project_ref, context_ref=context_ref, raw_text_chars=len(raw_text),
        )
        return IntakePrefillRead(context_ref=context_ref, raw_text=raw_text, source_note=source_note)

    def dismiss_intake_flow(self, project_ref: str, context_ref: str, operator_ref: str) -> FlowDismissRead:
        """AEP-111 放弃本次接入（软删）：仅终结态（需补充/已排除）可放弃；幂等回放既有时间戳。"""
        if not self._repo.project_exists(project_ref):
            raise NotFound(f"项目不存在：{project_ref}")
        facts = self._repo.read_intake_dismiss_facts(project_ref, context_ref)
        if facts is None:
            raise NotFound("接入请求上下文不存在或不属于该项目")
        if facts.dismissed_at is not None:
            return FlowDismissRead(
                context_ref=context_ref, dismissed_at=_iso_utc(facts.dismissed_at)
            )
        if facts.conclusion not in self._TERMINAL_CONCLUSIONS:
            raise RejectedTransition("仅终结态流程（需补充/已排除）可放弃本次接入")
        when = self._repo.mark_intake_dismissed(context_ref)
        log_event(
            "overview", "aep111.flow_dismissed",
            project_ref=project_ref, context_ref=context_ref,
            conclusion=facts.conclusion, operator_ref=operator_ref,
        )
        return FlowDismissRead(context_ref=context_ref, dismissed_at=_iso_utc(when))

    # ---- 内部 ----

    def _load(self, project_ref: str) -> ProjectOverviewFacts:
        if not self._repo.project_exists(project_ref):
            raise NotFound(f"项目不存在：{project_ref}")
        return self._repo.load_project_facts(project_ref)

    def _resolve_flow(self, root: IntakeRequestFact, idx: _Indexes) -> RequirementFlowRead:
        status: dict[str, str] = {s: "not_started" for s in STAGES}
        detail: dict[str, Optional[str]] = {s: None for s in STAGES}
        detail["itemReview"] = "待接入（SCN-003）"
        timestamps: list[datetime] = [root.created_at]
        resumable = True
        dismissable = False
        current = "intake"
        material_ref: Optional[str] = None
        parse_ctx: Optional[str] = None
        formation_ctx: Optional[str] = None

        record = idx.record_by_ctx.get(root.id)
        if record is None:
            # 行1/行2：接入判断在途或送检失败停靠
            if root.stop_next_action:
                status["intake"], detail["intake"] = "stopped", "接入判断失败，可重试"
            else:
                status["intake"], detail["intake"] = "in_progress", "接入判断中"
        else:
            timestamps.append(record.created_at)
            material_ref = record.material_ref
            conclusion = record.intake_conclusion
            if conclusion == IntakeConclusion.ACCEPTED.value:
                status["intake"] = "done"
                current = "analysis"
            elif conclusion == IntakeConclusion.RETURNED_FOR_SUPPLEMENT.value:
                # 行3：需补充（重提=新上下文，本流程不可前进；终结态可预填重提/可放弃）
                status["intake"], detail["intake"] = "stopped", "需补充：补充后重新提交为新流程"
                resumable = False
                dismissable = True
            else:
                # 行4：已排除（终结态可预填重提/可放弃）
                status["intake"], detail["intake"] = "stopped", "已排除：无需求资产价值"
                resumable = False
                dismissable = True

        if status["intake"] == "done":
            parse_ctx, formation_ctx, resumable, current = self._resolve_analysis_onward(
                material_ref, idx, status, detail, timestamps, resumable,
            )

        current_status = status[current]
        summary = f"{_STAGE_LABELS[current]} · {_STATUS_LABELS[current_status]}"
        return RequirementFlowRead(
            flow_id=root.id,
            title=root.source_note.strip() or "未命名来源",
            summary=summary,
            current_stage=current,
            resume_stage=current,
            resumable=resumable,
            dismissable=dismissable,
            stages=[
                FlowStageStatusRead(stage=s, status=status[s], detail=detail[s])
                for s in STAGES
            ],
            intake_context_ref=root.id,
            material_ref=material_ref,
            parse_context_ref=parse_ctx,
            formation_context_ref=formation_ctx,
            updated_at=max(timestamps).isoformat(),
        )

    def _resolve_analysis_onward(
        self,
        material_ref: Optional[str],
        idx: _Indexes,
        status: dict[str, str],
        detail: dict[str, Optional[str]],
        timestamps: list[datetime],
        resumable: bool,
    ) -> tuple[Optional[str], Optional[str], bool, str]:
        """②分析 → ③形成 派生；返回 (parse_ctx, formation_ctx, resumable, current)。"""
        current = "analysis"
        if material_ref is None:
            # 防御边缘：accepted 却无材料引用（不应出现）
            log_event(
                "overview", "aep072.accepted_without_material", level="WARN",
                msg="接入已确认但缺少材料引用",
            )
            return None, None, resumable, current

        parse_requests = idx.parse_requests_by_material.get(material_ref, [])
        if not parse_requests:
            # 行5：已接入、未发起识别
            return None, None, resumable, current

        rep = self._representative_parse_request(parse_requests, idx)
        parse_ctx = rep.id
        timestamps.append(rep.created_at)
        result = idx.parse_result_by_ctx.get(rep.id)
        if result is None:
            # 行6/行7：识别送检中或失败停靠
            if rep.stop_next_action:
                status["analysis"], detail["analysis"] = "stopped", "识别失败，可重试"
            else:
                status["analysis"], detail["analysis"] = "in_progress", "要素识别中"
            return parse_ctx, None, resumable, current

        timestamps.append(result.created_at)
        if result.parse_status == MaterialParseStatus.UNPROCESSABLE.value:
            # 行8：无可处理要素（死路）
            status["analysis"], detail["analysis"] = "stopped", "无可处理要素"
            return parse_ctx, None, False, current

        elements = idx.elements_by_pr.get(result.id, [])
        timestamps.extend(e.updated_at for e in elements)
        if not elements:
            # 防御边缘：parsed 却无要素（save_parse_result_and_elements 只在有要素时写 parsed）
            log_event(
                "overview", "aep072.parsed_without_elements", level="WARN",
                msg="解析结果为已解析但无要素", parse_result_ref=result.id,
            )
            status["analysis"], detail["analysis"] = "in_progress", "要素确认中"
            return parse_ctx, None, resumable, current
        if not _has_confirmed_formable(elements):
            # 行9/行10：要素尚未确认到位（或确认的均非可形成类型）
            status["analysis"], detail["analysis"] = "in_progress", "要素确认中"
            return parse_ctx, None, resumable, current

        status["analysis"] = "done"
        current = "itemFormation"

        items = idx.items_by_pr.get(result.id, [])
        formations = idx.formations_by_pr.get(result.id, [])
        if items:
            # 行14：已形成待确认条目
            timestamps.extend(i.updated_at for i in items)
            status["itemFormation"] = "done"
            latest_item = max(items, key=lambda i: i.updated_at)
            return parse_ctx, latest_item.formation_context_ref, resumable, current
        if formations:
            # 行12/行13：批次在途或失败停靠
            rep_f = max(formations, key=lambda f: f.created_at)
            timestamps.append(rep_f.created_at)
            if rep_f.stop_next_action:
                status["itemFormation"], detail["itemFormation"] = "stopped", "条目化批次失败，可重试"
            else:
                status["itemFormation"], detail["itemFormation"] = "in_progress", "条目化批次执行中"
            return parse_ctx, rep_f.id, resumable, current
        # 行11：具备形成条件、未发起批次
        return parse_ctx, None, resumable, current

    @staticmethod
    def _representative_parse_request(
        parse_requests: list[ParseRequestFact], idx: _Indexes
    ) -> ParseRequestFact:
        """多识别请求取「到达最深 + 最新」代表（深度：条目>批次>已确认可形成>有结果>裸请求）。"""

        def rank(pr: ParseRequestFact) -> tuple[int, datetime]:
            result = idx.parse_result_by_ctx.get(pr.id)
            if result is None:
                return 1, pr.created_at
            if idx.items_by_pr.get(result.id):
                return 5, pr.created_at
            if idx.formations_by_pr.get(result.id):
                return 4, pr.created_at
            if _has_confirmed_formable(idx.elements_by_pr.get(result.id, [])):
                return 3, pr.created_at
            return 2, pr.created_at

        return max(parse_requests, key=rank)
