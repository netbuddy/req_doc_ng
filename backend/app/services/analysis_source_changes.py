"""改源操作（L2）：勘误出新来源版本、补入追加「补」块，受影响要素回「待确认」。

只装动 LDM-002 的两个操作；要素侧的回摆随操作发生，不含要素生命周期主流程。
"""
from __future__ import annotations

import json

from app.api.schemas import ElementWorkspaceRead, MaterialErratumCommand, MaterialSupplementCommand
from app.domain.anchors import build_anchor_json
from app.domain.enums import ElementProcessStatus as ES
from app.domain.errors import InvalidInput, NotFound
from app.log import log_event

from app.services.analysis_support import AnalysisSupport
from app.services.analysis_support import _COMPONENT
from app.services.analysis_workspace import AnalysisWorkspace




class AnalysisSourceChanges:
    def __init__(
        self,
        process_records,
        source_assets,
        support: AnalysisSupport,
        workspace: AnalysisWorkspace,
    ) -> None:
        self._process_records = process_records
        self._source_assets = source_assets
        self._support = support
        self._workspace = workspace

    def material_erratum(self, command: MaterialErratumCommand) -> ElementWorkspaceRead:
        context = command.parse_context_ref
        self._support._require_parsed(context)
        self._support._require_version(context, command.workspace_version)
        old_text = command.old_text or ""
        new_text = command.new_text or ""
        if not old_text.strip() or old_text == new_text:
            raise InvalidInput("勘误必须给出原文中的待修正片段与不同的修正结果")

        canvas = self._support._build_canvas(context)
        if canvas is None:
            raise NotFound("材料内容不可读")
        raw_text = canvas.raw_text
        occurrences = raw_text.count(old_text)
        if occurrences == 0:
            raise InvalidInput("待修正片段在当前原文中不存在")
        if occurrences > 1:
            raise InvalidInput("待修正片段在原文中出现多处，请提供更长的唯一片段")

        edit_start = raw_text.find(old_text)
        edit_end_old = edit_start + len(old_text)
        delta = len(new_text) - len(old_text)
        new_raw = raw_text[:edit_start] + new_text + raw_text[edit_end_old:]

        note = command.reason or f"勘误：{old_text} → {new_text}"
        new_version = self._source_assets.apply_material_erratum(
            canvas.material_ref, new_raw, note, command.operator_ref
        )

        # 锚点重挂 + 受影响要素回「待确认」（改源联动，正交于迁移表）
        parse_result_ref = self._source_assets.parse_result_of(context)
        rows = self._source_assets.elements_of(parse_result_ref) if parse_result_ref else []
        for row in rows:
            if row.superseded or not row.source_anchor:
                continue
            try:
                anchor = json.loads(row.source_anchor)
                ranges = anchor.get("ranges", [])
            except (ValueError, AttributeError):
                continue
            affected = False
            new_ranges = []
            for r in ranges:
                start, end = r.get("start", -1), r.get("end", -1)
                if 0 <= start < end:
                    if end <= edit_start:  # 编辑点之前：不动
                        new_ranges.append(r)
                        continue
                    if start >= edit_end_old:  # 编辑点之后：平移
                        r = {**r, "start": start + delta, "end": end + delta}
                        new_ranges.append(r)
                        continue
                    # 与勘误重叠：按修正后文本重挂（exact 内替换后重新定位）
                    affected = True
                    new_exact = str(r.get("exact", "")).replace(old_text, new_text)
                    rebuilt = build_anchor_json(canvas.material_ref, new_raw, new_exact)
                    if rebuilt:
                        new_ranges.extend(json.loads(rebuilt)["ranges"])
                else:
                    new_ranges.append(r)
            new_anchor_json = json.dumps(
                {"material_ref": canvas.material_ref, "ranges": new_ranges}, ensure_ascii=False
            )
            if affected:
                new_content = row.content.replace(old_text, new_text)
                self._source_assets.apply_element_edit(
                    row.id, None, new_content if new_content != row.content else None,
                    new_anchor_json, f"原文勘误联动（来源版本 v{new_version}）",
                )
                if row.process_status != ES.PENDING_CONFIRMATION.value:
                    self._source_assets.set_element_status(
                        row.id, ES.PENDING_CONFIRMATION.value, clear_review=True
                    )
                self._support._history(row, "erratum_reanchor", row.process_status,
                              ES.PENDING_CONFIRMATION.value, command.operator_ref,
                              f"原文勘误（{note}），受影响要素回待确认",
                              snapshot={"content": row.content, "source_anchor": row.source_anchor})
            elif delta != 0:
                self._source_assets.apply_element_edit(row.id, None, None, new_anchor_json, None)

        log_event(_COMPONENT, "material.erratum.applied", material_ref=canvas.material_ref,
                  source_version=new_version, ok=True)
        self._process_records.bump_workspace_version(context)
        return self._workspace.read_element_workspace(context)

    def material_supplement(self, command: MaterialSupplementCommand) -> ElementWorkspaceRead:
        context = command.parse_context_ref
        self._support._require_parsed(context)
        self._support._require_version(context, command.workspace_version)
        if not command.content.strip():
            raise InvalidInput("补入内容不能为空")
        if not command.basis.strip():
            raise InvalidInput("补入必须写明依据（谁说的/哪次会议/什么凭证）")

        canvas = self._support._build_canvas(context)
        if canvas is None:
            raise NotFound("材料内容不可读")
        supplement_ref = self._source_assets.add_material_supplement(
            canvas.material_ref, command.content, command.basis, command.operator_ref
        )

        # 相关要素回「待确认」（补入联动）
        for ref in command.target_element_refs:
            row = self._support._require_element(context, ref)
            if row.process_status != ES.PENDING_CONFIRMATION.value:
                self._source_assets.set_element_status(
                    row.id, ES.PENDING_CONFIRMATION.value, clear_review=True
                )
            self._support._history(row, "supplement_linked", row.process_status,
                          ES.PENDING_CONFIRMATION.value, command.operator_ref,
                          f"补入来源（依据：{command.basis}），相关要素回待确认")

        log_event(_COMPONENT, "material.supplement.added", material_ref=canvas.material_ref,
                  supplement_ref=supplement_ref, ok=True)
        self._process_records.bump_workspace_version(context)
        return self._workspace.read_element_workspace(context)
