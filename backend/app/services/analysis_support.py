"""分析转化共享底座（L0）：校验、共享只读装配、共享留痕写点、偏离原文纯函数。

准入判据（任务卡 T20260722 constraints）：只收纯函数、被至少两个模块使用的共享
读取/校验，以及只写单表的共享留痕写点（`_history` 写历史表、`_record_adoption`
写 LDM-015 采纳记录）。其余写库方法一律归各业务模块。
本层不依赖任何上层模块，只调仓储与领域编排。
"""

import json
import re
from typing import Optional

from app.api.schemas import (
    ElementOperationRequestResult,
    MaterialCanvasRead,
    MaterialSupplementRead,
    MaterialTextBlockRead,
    SourceAnchorRange,
)
from app.domain.anchors import split_blocks
from app.domain.enums import MaterialParseStatus
from app.domain.errors import NotFound, RejectedTransition
from app.interfaces import ElementRow


_COMPONENT = "analysis-transformation"


def _ranges_to_dicts(ranges: list[SourceAnchorRange]) -> list[dict]:
    return [r.model_dump() for r in ranges]


class AnalysisSupport:
    def __init__(
        self,
        model_results,
        process_records,
        source_assets,
    ) -> None:
        self._model_results = model_results
        self._process_records = process_records
        self._source_assets = source_assets

    _FACT_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*|\d+(?:\.\d+)?")

    def _require_element(self, context_ref: str, element_ref: str) -> ElementRow:
        current = self._current_elements(context_ref)
        row = current.get(element_ref)
        if row is None:
            raise RejectedTransition(
                f"目标要素 {element_ref} 不在当前集合（版本冲突或已被替代），请刷新工作区"
            )
        return row

    def _require_parsed(self, context_ref: str, allow_unprocessable: bool = False) -> None:
        if not self._process_records.parse_context_exists(context_ref):
            raise NotFound("识别请求上下文不存在")
        status = self._source_assets.parse_status_of(context_ref)
        allowed = {MaterialParseStatus.PARSED.value}
        if allow_unprocessable:
            allowed.add(MaterialParseStatus.UNPROCESSABLE.value)
        if status not in allowed:
            raise RejectedTransition("当前工作区未形成解析结论，不能进行该操作")

    def _require_version(self, context_ref: str, workspace_version: str) -> None:
        current = str(self._process_records.read_workspace_version(context_ref))
        if workspace_version != current:
            raise RejectedTransition("工作区已更新（版本不一致），请刷新后重试")

    def _current_elements(self, context_ref: str) -> dict:
        parse_result_ref = self._source_assets.parse_result_of(context_ref)
        if not parse_result_ref:
            return {}
        return {
            e.id: e
            for e in self._source_assets.elements_of(parse_result_ref)
            if not e.superseded
        }

    def _operation_precheck(
        self, context_ref: str, workspace_version: str
    ) -> Optional[ElementOperationRequestResult]:
        if not self._process_records.parse_context_exists(context_ref):
            raise NotFound("识别请求上下文不存在")
        status = self._source_assets.parse_status_of(context_ref)
        if status != MaterialParseStatus.PARSED.value:
            return ElementOperationRequestResult(
                status="rejected_precheck",
                next_action="当前工作区未形成可处理要素集合，无法发起该操作",
            )
        current = str(self._process_records.read_workspace_version(context_ref))
        if workspace_version != current:
            return ElementOperationRequestResult(
                status="rejected_precheck",
                next_action="工作区已更新（版本不一致），请刷新后重试",
            )
        return None

    def _project_of_element(self, row: ElementRow) -> str:
        return ""  # project 归属由仓储自 LDM-005 行补全

    def _project_of(self, context_ref: str) -> str:
        # 操作/草案记录归属项目：从材料内容取（RequestContent.project_ref）。
        material_ref = self._process_records.read_parse_material_ref(context_ref)
        content = self._source_assets.read_material_content(material_ref) if material_ref else None
        return content.project_ref if content else ""

    def _history(
        self, row: ElementRow, action: str, from_status: Optional[str],
        to_status: Optional[str], operator_ref: str, note: Optional[str],
        snapshot: Optional[dict] = None,
    ) -> None:
        updated = self._source_assets.get_element(row.id)
        version = updated.version if updated else row.version
        self._source_assets.record_element_history(
            row.id, self._project_of_element(row), version, action,
            from_status, to_status, operator_ref, note,
            json.dumps(snapshot, ensure_ascii=False) if snapshot else None,
        )

    def _record_adoption(self, context: str, stage: str, subject_type: str,
                         subject_ref: str, outcome: str, operator_ref: str,
                         idempotency_key: str) -> None:
        """采纳结论明细（口径设计 §4）：挂该上下文最近一条对应环节 LDM-015；无记录不写。"""
        latest = self._model_results.latest_stage_payload(stage, context)
        if latest is None:
            return
        project_ref = self._process_records.project_of_context(context)
        if not project_ref:
            return
        self._model_results.record_adoption(
            model_result_ref=latest.ref, project_ref=project_ref, stage=stage,
            subject_type=subject_type, subject_ref=subject_ref, outcome=outcome,
            operator_ref=operator_ref, idempotency_key=idempotency_key,
        )

    def _build_canvas(self, context_ref: str) -> Optional[MaterialCanvasRead]:
        material_ref = self._process_records.read_parse_material_ref(context_ref)
        return self._canvas_of(material_ref) if material_ref else None

    def _canvas_of(self, material_ref: str) -> Optional[MaterialCanvasRead]:
        """由 LDM-002 当前来源版本装配来源画布（含「补」来源块）。"""
        content = self._source_assets.read_material_content(material_ref)
        if content is None:
            return None
        title = "来源材料"
        for seg in (content.source_note or "").split("；"):
            if seg.startswith("接入对象:") and len(seg) > len("接入对象:"):
                title = seg[len("接入对象:"):]
                break
        supplements = [
            MaterialSupplementRead(
                supplement_ref=s.id, content=s.content, basis=s.basis,
                operator_ref=s.operator_ref, at=s.at,
            )
            for s in self._source_assets.supplements_of(material_ref)
        ]
        return MaterialCanvasRead(
            material_ref=material_ref,
            title=title,
            source_note=content.source_note or None,
            raw_text=content.raw_text,
            source_version=self._source_assets.material_source_version(material_ref),
            blocks=[MaterialTextBlockRead(**b) for b in split_blocks(content.raw_text)],
            supplements=supplements,
        )

    @staticmethod
    def _source_corpus(canvas: Optional[MaterialCanvasRead]) -> str:
        """来源事实语料 = 原文 + 补入块（勘误出新版原文、补入扩充语料，均可消解偏离）。"""
        if canvas is None:
            return ""
        parts = [canvas.raw_text or ""]
        parts.extend(s.content or "" for s in (canvas.supplements or []))
        return "\n".join(parts).lower()

    @classmethod
    def _novel_tokens_against(cls, corpus: str, text: str) -> list[str]:
        """超出原文检查（启发式）：文本中的数字/拉丁术语 token 在来源语料中
        均不存在时视为新增事实。中文语义级新增无法确定性判定，留待 AI 校验增强。"""
        novel: list[str] = []
        for token in cls._FACT_TOKEN_RE.findall(text or ""):
            if token.lower() not in corpus and token not in novel:
                novel.append(token)
        return novel[:8]

    def _novel_fact_tokens(self, context_ref: str, draft: str) -> list[str]:
        corpus = self._source_corpus(self._build_canvas(context_ref))
        return self._novel_tokens_against(corpus, draft)
