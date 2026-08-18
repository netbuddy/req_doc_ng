"""工作区只读组装（L1）：要素工作区、材料解析上下文、来源画布、要素展示投影。

只读层：不写库、不迁状态。被 lifecycle/source_changes/change_drafts/dialogue 共同
依赖，故独立成层，只向下调 support。
"""

import json
from dataclasses import replace as dataclass_replace
from typing import Optional

from app.api.schemas import (
    ActionFact,
    ElementChangeDraftRead,
    ElementFacetFindingRead,
    ElementFacetReviewRead,
    ElementWorkspaceRead,
    MaterialCanvasRead,
    MaterialParseContextRead,
    RequirementElementRead,
    SourceAnchorRange,
)
from app.domain.enums import (
    ElementProcessStatus as ES,
    ITEMIZABLE_ELEMENT_TYPES,
    MaterialParseStatus,
    ModelVerdict,
    NoiseTriage,
)
from app.domain.errors import NotFound
from app.domain.rubrics import get_rubric
from app.interfaces import ElementRow, FacetProjectionRow

from app.services.analysis_support import AnalysisSupport


_OPERATION_KEYS = (
    "decide", "ai_review", "revise", "edit_element", "source_correction",
    "add_missing", "split_merge", "ai_execute", "confirm_change",
)


def _in_triage_group(e: RequirementElementRead) -> bool:
    """这一条是否待在区1 底部那个默认折叠的「AI 建议剔除的候选」分组里。

    与前端 `view-models/requirement-analysis.ts` 的 `isTriageCandidate` 同一套判据：模型判为
    建议剔除、人工尚未撤回、且尚未被撤销（撤销即处置完毕，该条随即离箱回到正常列表）。
    改判据须两处同步。此处只用于挑默认选中目标——落在这个分组里的行在页面上默认看不见。
    """
    return (
        e.model_verdict == ModelVerdict.SUSPECTED_NOISE
        and e.noise_triage != NoiseTriage.RESTORED
        and e.process_status != ES.REVOKED
    )


class AnalysisWorkspace:
    def __init__(
        self,
        model_results,
        process_records,
        source_assets,
        support: AnalysisSupport,
    ) -> None:
        self._model_results = model_results
        self._process_records = process_records
        self._source_assets = source_assets
        self._support = support

    def read_element_workspace(self, context_ref: str) -> ElementWorkspaceRead:
        if not self._process_records.parse_context_exists(context_ref):
            raise NotFound("识别请求上下文不存在")

        version = str(self._process_records.read_workspace_version(context_ref))
        canvas = self._support._build_canvas(context_ref)
        parse_status = self._source_assets.parse_status_of(context_ref)

        if parse_status is None:
            stop_next = self._process_records.read_parse_stop_next_action(context_ref)
            if stop_next is not None:  # 识别失败停靠
                return ElementWorkspaceRead(
                    parse_context_ref=context_ref,
                    workspace_version=version,
                    material_canvas=canvas,
                    next_action=stop_next,
                    available_actions=[ActionFact(key="retry", enabled=True)],
                    available_operations=self._operations_disabled("识别未完成"),
                )
            return ElementWorkspaceRead(  # 仍在识别中
                parse_context_ref=context_ref,
                workspace_version=version,
                material_canvas=canvas,
                next_action="识别进行中",
                available_actions=[],
                available_operations=self._operations_disabled("识别进行中"),
            )

        parse_result_ref = self._source_assets.parse_result_of(context_ref)
        basis = self._source_assets.parse_basis_of(context_ref)
        draft_row = self._process_records.read_open_draft(context_ref)
        draft = self._project_draft(draft_row, context_ref) if draft_row else None
        review_note = self._latest_review_note(context_ref)

        if parse_status == MaterialParseStatus.PARSED.value:
            rows = self._source_assets.elements_of(parse_result_ref) if parse_result_ref else []
            facet_reviews = self._facet_reviews_of(rows)
            corpus = self._support._source_corpus(canvas)
            elements = [
                self._project_element(r, facet_review=facet_reviews.get(r.id), corpus=corpus)
                for r in rows
            ]
            live = [e for e in elements if not e.superseded]
            # 默认选中跳过建议剔除候选（冷审查裁定 C2）：候选分组默认折叠，选中一条看不见的行，
            # 用户会看到区4 详情与区5「当前目标」显示着某条内容、而区1 列表里没有任何一行被选中。
            # 全部知识项都是候选时才落回候选（否则页面无目标可选）。
            selectable = [e for e in live if not _in_triage_group(e)] or live
            selected = next(
                (e.id for e in selectable if e.process_status == ES.PENDING_CONFIRMATION),
                selectable[0].id if selectable else None,
            )
            has_confirmed_expr = any(
                e.process_status == ES.CONFIRMED and e.element_type.value in ITEMIZABLE_ELEMENT_TYPES
                for e in live
            )
            actions = [
                # E5 下游门禁：只有「已确认」的需求表达类要素开放条目形成
                ActionFact(
                    key="start_item_formation",
                    enabled=has_confirmed_expr,
                    disabled_reason=None if has_confirmed_expr else "只有「已确认」的需求表达类要素才能进入条目形成",
                ),
                ActionFact(key="review", enabled=True),
                ActionFact(key="correction", enabled=True),
            ]
            operations = self._operations_parsed(draft)
            next_action = None
            # 本上下文已含的要素不再作为「既有项」重复投影一遍（裁定 C2）
            merged_existing = self._merged_existing_elements(canvas, {e.id for e in elements})
        else:  # 不可继续处理
            elements = []
            merged_existing = []
            selected = None
            actions = [
                ActionFact(key="start_item_formation", enabled=False, disabled_reason="未识别出可处理知识项"),
                ActionFact(key="correction", enabled=True),
            ]
            operations = self._operations_unprocessable(draft)
            next_action = "未识别出可处理知识项"

        return ElementWorkspaceRead(
            parse_context_ref=context_ref,
            parse_result_ref=parse_result_ref,
            workspace_version=version,
            parse_status=parse_status,
            material_canvas=canvas,
            elements=elements,
            merged_existing_elements=merged_existing,
            selected_element_ref=selected,
            basis=basis,
            review_note=review_note,
            change_draft=draft,
            next_action=next_action,
            available_actions=actions,
            available_operations=operations,
        )

    def _merged_existing_elements(
        self, canvas, exclude_element_refs: Optional[set[str]] = None
    ) -> list[RequirementElementRead]:
        """既有知识项在本材料工作区的可见投影（纯读，零迁移；03 §2.1 登记归并的读侧补面）。

        登记归并不新建要素：本材料识别出的术语/角色/外部系统若与既往材料同名，
        识别产物只落在既有要素的 merge 历史行上（snapshot.merged_from_material=本材料，
        merged_anchor=本材料锚点）。此处按材料反查该批留痕，把既有要素当前行连同本材料
        锚点投影出来，供区1 显示、区3 标注；不改事实、不进 elements、不参与门禁与批量裁决。

        exclude_element_refs＝本上下文 elements 已含的要素 id（冷审查裁定 C2）。
        同一份材料重复识别时，上一轮识别出的要素会被这一轮当成「既往同名要素」归并，
        留下指向本材料的 merge 留痕；从总览恢复执行又会落回上一轮那个上下文，于是同一个
        要素同时出现在 elements 与本清单里。界面按 id 判「既有」，两份副本都被判为只读——
        该要素在知识抽取页彻底无法确认或重开，另外还带出重复 key、区3 假重叠、区1 计数
        翻倍三条后果。在这里排除即一次收口。
        """
        material_ref = canvas.material_ref if canvas else None
        if not material_ref:
            return []
        excluded = exclude_element_refs or set()
        anchors: dict[str, Optional[str]] = {}
        for h in self._source_assets.merge_history_for_material(material_ref):
            try:
                snap = json.loads(h.snapshot or "{}")
            except ValueError:
                continue
            if not isinstance(snap, dict):
                continue
            # 同一材料重复识别会留多行：后写的锚点覆盖先写的（仓储按时间升序返回）
            anchors[h.element_ref] = snap.get("merged_anchor")
        out: list[RequirementElementRead] = []
        for element_ref, anchor in anchors.items():
            if element_ref in excluded:
                continue
            row = self._source_assets.get_element(element_ref)
            if row is None or row.superseded:
                continue
            # 锚点为空就是没有锚点，不回落到既往材料的锚点（冷审查裁定 C6）：
            # 模型对某条归并项没给引文时 merged_anchor 为 None，回落会把别的材料的锚点
            # 投影到本材料上，前端比对材料引用不等即判失效，打出「来源定位待修正」并
            # 指向一个对既有项恒禁用的「改范围」按钮——假告警加点不动的指引。
            # 给 None 时前端走「未提供来源锚点」，如实且无死指引。
            # corpus=None：既有项的表达来自既往材料，不参与本材料的原文偏离标记
            out.append(self._project_element(
                dataclass_replace(row, source_anchor=anchor), corpus=None
            ))
        return out

    def _project_element(
        self,
        r: ElementRow,
        facet_review: Optional[ElementFacetReviewRead] = None,
        corpus: Optional[str] = None,
    ) -> RequirementElementRead:
        origin_refs: list[str] = []
        if r.origin_refs:
            try:
                origin_refs = list(json.loads(r.origin_refs))
            except ValueError:
                origin_refs = []
        drift_tokens: list[str] = []
        if corpus is not None and not r.superseded:
            drift_tokens = self._support._novel_tokens_against(corpus, r.content)
        return RequirementElementRead(
            id=r.id, element_type=r.element_type, content=r.content,
            source_anchor=r.source_anchor, confidence=r.confidence,
            source_drift_tokens=drift_tokens,
            process_status=r.process_status,
            model_verdict=r.model_verdict,
            verdict_reason=r.verdict_reason,
            noise_triage=r.noise_triage,
            version=r.version, superseded=r.superseded,
            review_conclusion=r.review_conclusion, review_basis=r.review_basis,
            revision_draft=r.revision_draft,
            correction_note=r.correction_note,
            origin_refs=origin_refs,
            facet_review=facet_review,
            updated_at=getattr(r, "updated_at", None),
        )

    @staticmethod
    def _facet_rows_of(
        finding: dict, element_ref: str, element_version: int, model_result_ref: str
    ) -> list[FacetProjectionRow]:
        """LDM-015 单条结论 → 投影行（TC-08；无 facet 判定返回空）。"""
        rubric_version = finding.get("rubric_version")
        raw_facets = finding.get("facet_findings")
        if not isinstance(rubric_version, int) or not isinstance(raw_facets, list):
            return []
        rows: list[FacetProjectionRow] = []
        for ff in raw_facets:
            if not isinstance(ff, dict) or not ff.get("facet"):
                continue
            rows.append(FacetProjectionRow(
                element_ref=element_ref,
                element_version=element_version,
                rubric_version=rubric_version,
                facet_key=str(ff["facet"]),
                facet_status=str(ff.get("status") or ""),
                evidence=ff.get("evidence"),
                note=ff.get("note"),
                correctness=finding.get("correctness"),
                completeness=finding.get("completeness"),
                model_result_ref=model_result_ref,
            ))
        return rows

    def _facet_reviews_of(self, rows: list[ElementRow]) -> dict[str, ElementFacetReviewRead]:
        """完备度投影读路径（TC-08：读 process_element_facet_projection）。

        非权威、可整层重算：仅供徽章/筛选/提示，不参与状态迁移或门禁（设计增补 §3）。
        element.version 与投影 element_version 不一致 → stale（待重诊）。
        """
        projections = self._process_records.facet_projections_of([r.id for r in rows])
        version_of = {r.id: r.version for r in rows}
        type_of = {r.id: r.element_type for r in rows}
        out: dict[str, ElementFacetReviewRead] = {}
        for ref, prows in projections.items():
            if not prows:
                continue
            rubric = get_rubric(type_of.get(ref, ""))
            facets: list[ElementFacetFindingRead] = []
            for p in prows:
                spec = rubric.facet(p.facet_key) if rubric is not None else None
                facets.append(ElementFacetFindingRead(
                    facet_key=p.facet_key,
                    label=spec.label if spec else p.facet_key,
                    required=spec.required if spec else False,
                    status=p.facet_status,
                    evidence=p.evidence,
                    note=p.note,
                    revision_hint=(
                        spec.revision_hint
                        if spec and p.facet_status not in ("present", "not_applicable")
                        else None
                    ),
                ))
            head = prows[0]
            out[ref] = ElementFacetReviewRead(
                rubric_version=head.rubric_version,
                correctness=head.correctness,
                completeness=head.completeness,
                facets=facets,
                stale=version_of.get(ref, head.element_version) != head.element_version,
            )
        return out

    def _latest_review_note(self, context_ref: str) -> Optional[str]:
        latest = self._model_results.latest_stage_payload("element_review", context_ref)
        if latest is None:
            return None
        if latest.result_code == "review_failed":
            return latest.basis or "复核失败，可重试或人工直接裁定"
        payload = json.loads(latest.payload) if latest.payload else {}
        if payload.get("mode") == "scan":
            n = len(payload.get("items", []))
            return f"扫原文补漏完成：新增 {n} 条待确认要素" if n else "扫原文补漏完成：未发现漏识别项"
        n = len(payload.get("findings", []))
        return f"复核完成：{n} 条结论待裁定"

    def read_material_parse_context(self, material_ref: str) -> MaterialParseContextRead:
        """该材料最近一次识别请求上下文（进页只读回放用）。

        知识抽取页不带恢复锚点进入时靠它判断「这份材料是否已经识别过」：识别过就读回既有
        工作区（只读回放，不发起识别），没识别过才停在未识别态。缺了这一步页面会把已识别的
        材料当成未识别，区5 全禁用，用户只剩「重新识别」一条路（那会另起一份清单）。
        """
        if not self._source_assets.is_material_accepted(material_ref):
            raise NotFound("材料不存在或未接入")
        return MaterialParseContextRead(
            material_ref=material_ref,
            parse_context_ref=self._process_records.latest_parse_context_of_material(material_ref),
        )

    def read_material_canvas(self, material_ref: str) -> MaterialCanvasRead:
        """N01 前：区3 未识别态只读呈现已接入材料正文（不依赖识别上下文）。"""
        if not self._source_assets.is_material_accepted(material_ref):
            raise NotFound("材料不存在或未接入，无法呈现来源画布")
        canvas = self._support._canvas_of(material_ref)
        if canvas is None:
            raise NotFound("材料内容不可读")
        return canvas

    def _project_draft(self, row, context_ref: str) -> ElementChangeDraftRead:
        items = json.loads(row.items)
        target_refs = json.loads(row.target_refs) if row.target_refs else []
        before: list[RequirementElementRead] = []
        parse_result_ref = self._source_assets.parse_result_of(context_ref)
        if parse_result_ref:
            current = {e.id: e for e in self._source_assets.elements_of(parse_result_ref)}
            for tid in target_refs:
                e = current.get(tid)
                if e:
                    before.append(self._project_element(e))
        after: list[RequirementElementRead] = []
        for i, item in enumerate(items):
            if item.get("action") != "create":
                continue
            el = item["element"]
            after.append(RequirementElementRead(
                id=f"draft-{i}",
                element_type=el["element_type"],
                content=el["content"],
                source_anchor=el.get("source_anchor"),
                confidence=el.get("confidence"),
                process_status=el.get("process_status", ES.PENDING_CONFIRMATION.value),
            ))
        return ElementChangeDraftRead(
            draft_ref=row.id,
            workspace_version=str(row.workspace_version),
            operation_type=row.operation_type,
            target_element_refs=target_refs,
            before_items=before,
            after_items=after,
            source_ranges=[SourceAnchorRange(**r) for r in json.loads(row.source_ranges or "[]")],
            impact_summary=json.loads(row.impact_summary or "[]"),
            create_gate=row.create_gate,
            next_action=row.next_action,
            updated_at=getattr(row, "updated_at", None),
        )

    def _operations_disabled(self, reason: str) -> list[ActionFact]:
        return [ActionFact(key=k, enabled=False, disabled_reason=reason) for k in _OPERATION_KEYS]

    def _operations_parsed(self, draft: Optional[ElementChangeDraftRead]) -> list[ActionFact]:
        ops = [ActionFact(key=k, enabled=True) for k in _OPERATION_KEYS[:-1]]
        can_confirm = draft is not None and draft.create_gate == "creatable"
        reason = None if can_confirm else ("无待确认变更草案" if draft is None else f"草案不可创建：{draft.create_gate}")
        ops.append(ActionFact(key="confirm_change", enabled=can_confirm, disabled_reason=reason))
        return ops

    def _operations_unprocessable(self, draft: Optional[ElementChangeDraftRead]) -> list[ActionFact]:
        ops: list[ActionFact] = []
        for k in _OPERATION_KEYS[:-1]:
            if k == "add_missing":
                ops.append(ActionFact(key=k, enabled=True))
            else:
                ops.append(ActionFact(key=k, enabled=False, disabled_reason="无可处理要素"))
        can_confirm = draft is not None and draft.create_gate == "creatable"
        ops.append(ActionFact(
            key="confirm_change", enabled=can_confirm,
            disabled_reason=None if can_confirm else "无待确认变更草案",
        ))
        return ops
