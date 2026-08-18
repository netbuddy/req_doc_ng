"""要素完备性判据（rubric 知识资产）加载。

设计事实源：docs/40-detailed-design/domains/DS-001-需求形成域/要素完备性判据与诊断投影.md §1。
- 判据随代码版本化：本目录 <element_type>.yaml，不入库、不提供运行时编辑。
- 无判据类型返回 None → 调用方降级为通用复核。
- facet key 为稳定 ASCII 码，发布后语义不变；rubric_version 语义变更 +1。
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

_RUBRIC_DIR = Path(__file__).resolve().parent

# not_applicable：判据驱动的 N/A 裁定（仅声明了 applicability 条件的成分可裁；不计缺口）。
FACET_STATUSES = ("present", "missing", "ambiguous", "not_applicable")
CORRECTNESS_VALUES = ("consistent_with_source", "deviates", "unverifiable")


@dataclass(frozen=True)
class RubricFacet:
    key: str
    label: str
    required: bool
    criteria: str
    revision_hint: str
    # 适用性条件（N/A 通道）：声明后该成分在要素不满足条件时可裁 not_applicable（不计缺口）；
    # None＝未声明适用性，成分行为完全不变（回归收窄，只有声明者才可能被裁 N/A）。
    applicability: str | None = None


@dataclass(frozen=True)
class ElementRubric:
    element_type: str
    rubric_version: int
    facets: tuple[RubricFacet, ...]

    def facet(self, key: str) -> RubricFacet | None:
        for f in self.facets:
            if f.key == key:
                return f
        return None

    def completeness_of(self, facet_status: dict[str, str]) -> str:
        """由 facet 判定推导完备性（服务端确定性计算，不采信模型自评）。

        not_applicable（判据驱动 N/A）视同满足，不计缺口——判据本不适配该要素形态。
        """
        for f in self.facets:
            if f.required and facet_status.get(f.key) not in ("present", "not_applicable"):
                return "incomplete"
        return "complete"


def _parse_rubric(path: Path) -> ElementRubric:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"判据文件不是映射结构：{path.name}")
    element_type = str(data.get("element_type") or "")
    version = data.get("rubric_version")
    if element_type != path.stem:
        raise ValueError(f"判据 element_type 与文件名不一致：{path.name}")
    if not isinstance(version, int) or version < 1:
        raise ValueError(f"判据 rubric_version 非法：{path.name}")
    facets: list[RubricFacet] = []
    seen: set[str] = set()
    for raw in data.get("facets") or []:
        key = str(raw.get("key") or "")
        if not key or not key.isascii() or key in seen:
            raise ValueError(f"判据 facet key 非法或重复：{path.name}:{key!r}")
        seen.add(key)
        applicability_raw = raw.get("applicability")
        facets.append(RubricFacet(
            key=key,
            label=str(raw.get("label") or key),
            required=bool(raw.get("required", False)),
            criteria=str(raw.get("criteria") or ""),
            revision_hint=str(raw.get("revision_hint") or ""),
            applicability=str(applicability_raw).strip() or None if applicability_raw else None,
        ))
    if not facets:
        raise ValueError(f"判据无 facet：{path.name}")
    return ElementRubric(element_type=element_type, rubric_version=version, facets=tuple(facets))


@lru_cache(maxsize=1)
def all_rubrics() -> dict[str, ElementRubric]:
    rubrics: dict[str, ElementRubric] = {}
    for path in sorted(_RUBRIC_DIR.glob("*.yaml")):
        rubric = _parse_rubric(path)
        rubrics[rubric.element_type] = rubric
    return rubrics


def get_rubric(element_type: str) -> ElementRubric | None:
    """按类型取判据；无判据类型返回 None（调用方降级为通用复核）。"""
    return all_rubrics().get(element_type)
