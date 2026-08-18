"""条目陈述档案（item profile 知识资产）加载。

设计事实源：
- docs/40-detailed-design/domains/DS-001-需求形成域/条目完备性档案与结构投影.md §1（单文件 schema、判定链路）。
- docs/40-detailed-design/domains/DS-001-需求形成域/需求规约方案与档案选型.md（方案维度、目录升级、公共层）。

要点：
- 档案随代码版本化：本目录 conventions/<convention_key>/<req_type>.yaml，不入库、不提供运行时编辑。
- 方案（convention）为档案层第一维度，封闭三项（CONVENTION_KEYS）；无档案类型返回 None → 调用方降级为纯句式规范化。
- facet/payload 字段 key 为稳定 ASCII 码，发布后语义不变；profile_version 语义变更 +1。
- common.yaml 承载方案无关的模态词规范与质量规则 Q1–Q7，是共享写作约束的单一来源。
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from app.domain.enums import RequirementItemType

_PROFILE_DIR = Path(__file__).resolve().parent
_CONVENTIONS_DIR = _PROFILE_DIR / "conventions"
_COMMON_FILE = _PROFILE_DIR / "common.yaml"

# not_applicable：判据驱动的 N/A 裁定（仅声明了 applicability 条件的成分可裁；不计缺口）。
FACET_STATUSES = ("present", "missing", "ambiguous", "not_applicable")
STATEMENT_CONFORMANCE_VALUES = ("conforms", "deviates", "not_applicable")

# 面向用户开放的封闭方案集（选型文档 §1.1）；ears-cn 为默认，无配置行即此方案。
CONVENTION_KEYS = ("ears-cn", "boilerplate-cn", "master-cn")
DEFAULT_CONVENTION = "ears-cn"

# 封闭五类 req_type，以 RequirementItemType 枚举为单一来源。
REQ_TYPES = tuple(t.value for t in RequirementItemType)


@dataclass(frozen=True)
class ProfileFacet:
    key: str
    label: str
    required: bool
    criteria: str
    revision_hint: str
    # 适用性条件（N/A 通道）：声明后该成分在陈述不满足条件时可裁 not_applicable（不计缺口）；
    # None＝未声明适用性，成分行为完全不变（回归收窄，只有声明者才可能被裁 N/A）。
    applicability: str | None = None


@dataclass(frozen=True)
class ProfilePayloadField:
    key: str
    label: str
    facet_ref: str


@dataclass(frozen=True)
class ItemProfile:
    convention_key: str
    req_type: str
    profile_version: int
    statement_pattern: str
    facets: tuple[ProfileFacet, ...]
    payload_fields: tuple[ProfilePayloadField, ...]

    def facet(self, key: str) -> ProfileFacet | None:
        for f in self.facets:
            if f.key == key:
                return f
        return None

    def completeness_of(self, facet_status: dict[str, str]) -> str:
        """由 facet 判定推导陈述完备性（服务端确定性计算，不采信模型自评）。

        not_applicable（判据驱动 N/A）视同满足，不计缺口——判据本不适配该陈述形态。
        """
        for f in self.facets:
            if f.required and facet_status.get(f.key) not in ("present", "not_applicable"):
                return "incomplete"
        return "complete"


@dataclass(frozen=True)
class ConventionPattern:
    label: str
    pattern: str


@dataclass(frozen=True)
class ConventionExample:
    req_type: str
    statement: str


@dataclass(frozen=True)
class ConventionMeta:
    convention_key: str
    display_name: str
    blueprint: str
    positioning: str
    pattern_overview: tuple[ConventionPattern, ...]
    examples: tuple[ConventionExample, ...]


@dataclass(frozen=True)
class QualityRule:
    key: str
    label: str
    rule: str


@dataclass(frozen=True)
class CommonConstraints:
    """方案无关公共写作约束（common.yaml；选型文档 §4）。"""

    modal_intro: str
    modal_required: str
    modal_recommended: str
    modal_permitted: str
    modal_forbidden: str
    modal_banned: str
    quality_rules: tuple[QualityRule, ...]


def _parse_profile(path: Path, convention_key: str) -> ItemProfile:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"档案文件不是映射结构：{path.name}")
    file_convention = str(data.get("convention_key") or "")
    req_type = str(data.get("req_type") or "")
    version = data.get("profile_version")
    pattern = str(data.get("statement_pattern") or "").strip()
    if file_convention != convention_key:
        raise ValueError(f"档案 convention_key 与目录名不一致：{convention_key}/{path.name}")
    if req_type != path.stem:
        raise ValueError(f"档案 req_type 与文件名不一致：{convention_key}/{path.name}")
    if not isinstance(version, int) or version < 1:
        raise ValueError(f"档案 profile_version 非法：{convention_key}/{path.name}")
    if not pattern:
        raise ValueError(f"档案缺 statement_pattern：{convention_key}/{path.name}")
    facets: list[ProfileFacet] = []
    seen: set[str] = set()
    for raw in data.get("facets") or []:
        key = str(raw.get("key") or "")
        if not key or not key.isascii() or key in seen:
            raise ValueError(f"档案 facet key 非法或重复：{convention_key}/{path.name}:{key!r}")
        seen.add(key)
        applicability_raw = raw.get("applicability")
        facets.append(ProfileFacet(
            key=key,
            label=str(raw.get("label") or key),
            required=bool(raw.get("required", False)),
            criteria=str(raw.get("criteria") or ""),
            revision_hint=str(raw.get("revision_hint") or ""),
            applicability=str(applicability_raw).strip() or None if applicability_raw else None,
        ))
    if not facets:
        raise ValueError(f"档案无 facet：{convention_key}/{path.name}")
    fields: list[ProfilePayloadField] = []
    seen_fields: set[str] = set()
    for raw in data.get("payload_fields") or []:
        key = str(raw.get("key") or "")
        facet_ref = str(raw.get("facet_ref") or "")
        if not key or not key.isascii() or key in seen_fields:
            raise ValueError(f"档案 payload 字段 key 非法或重复：{convention_key}/{path.name}:{key!r}")
        if facet_ref not in seen:
            raise ValueError(f"档案 payload 字段未绑定有效 facet：{convention_key}/{path.name}:{key}→{facet_ref!r}")
        seen_fields.add(key)
        fields.append(ProfilePayloadField(
            key=key,
            label=str(raw.get("label") or key),
            facet_ref=facet_ref,
        ))
    return ItemProfile(
        convention_key=convention_key, req_type=req_type, profile_version=version,
        statement_pattern=pattern, facets=tuple(facets), payload_fields=tuple(fields),
    )


def _parse_meta(path: Path, convention_key: str) -> ConventionMeta:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"方案元数据不是映射结构：{convention_key}/_meta.yaml")
    if str(data.get("convention_key") or "") != convention_key:
        raise ValueError(f"方案元数据 convention_key 与目录名不一致：{convention_key}")
    patterns = tuple(
        ConventionPattern(label=str(r.get("label") or ""), pattern=str(r.get("pattern") or ""))
        for r in data.get("pattern_overview") or []
    )
    examples = tuple(
        ConventionExample(req_type=str(r.get("req_type") or ""), statement=str(r.get("statement") or ""))
        for r in data.get("examples") or []
    )
    covered = {e.req_type for e in examples}
    if covered != set(REQ_TYPES):
        missing = set(REQ_TYPES) - covered
        extra = covered - set(REQ_TYPES)
        raise ValueError(
            f"方案 {convention_key} 的 _meta.examples 未覆盖五类："
            f"缺 {sorted(missing)}，多 {sorted(extra)}"
        )
    return ConventionMeta(
        convention_key=convention_key,
        display_name=str(data.get("display_name") or convention_key),
        blueprint=str(data.get("blueprint") or ""),
        positioning=str(data.get("positioning") or ""),
        pattern_overview=patterns,
        examples=examples,
    )


@lru_cache(maxsize=1)
def _load() -> tuple[dict[str, dict[str, ItemProfile]], dict[str, ConventionMeta]]:
    """按方案分组加载全部档案 + 元数据；校验封闭集、目录一致性与五类覆盖。"""
    present_dirs = {p.name for p in _CONVENTIONS_DIR.iterdir() if p.is_dir()}
    if present_dirs != set(CONVENTION_KEYS):
        missing = set(CONVENTION_KEYS) - present_dirs
        extra = present_dirs - set(CONVENTION_KEYS)
        raise ValueError(f"方案目录集合与封闭集不一致：缺 {sorted(missing)}，多 {sorted(extra)}")
    profiles: dict[str, dict[str, ItemProfile]] = {}
    metas: dict[str, ConventionMeta] = {}
    for convention_key in CONVENTION_KEYS:
        conv_dir = _CONVENTIONS_DIR / convention_key
        by_type: dict[str, ItemProfile] = {}
        for path in sorted(conv_dir.glob("*.yaml")):
            if path.name == "_meta.yaml":
                continue
            profile = _parse_profile(path, convention_key)
            by_type[profile.req_type] = profile
        if set(by_type) != set(REQ_TYPES):
            missing = set(REQ_TYPES) - set(by_type)
            extra = set(by_type) - set(REQ_TYPES)
            raise ValueError(f"方案 {convention_key} 档案未覆盖五类：缺 {sorted(missing)}，多 {sorted(extra)}")
        profiles[convention_key] = by_type
        metas[convention_key] = _parse_meta(conv_dir / "_meta.yaml", convention_key)
    return profiles, metas


@lru_cache(maxsize=1)
def _load_common() -> CommonConstraints:
    data = yaml.safe_load(_COMMON_FILE.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("common.yaml 不是映射结构")
    modal = data.get("modal_words") or {}
    rules = tuple(
        QualityRule(key=str(r.get("key") or ""), label=str(r.get("label") or ""), rule=str(r.get("rule") or ""))
        for r in data.get("quality_rules") or []
    )
    if not rules:
        raise ValueError("common.yaml 缺 quality_rules")
    return CommonConstraints(
        modal_intro=str(modal.get("intro") or ""),
        modal_required=str(modal.get("required") or ""),
        modal_recommended=str(modal.get("recommended") or ""),
        modal_permitted=str(modal.get("permitted") or ""),
        modal_forbidden=str(modal.get("forbidden") or ""),
        modal_banned=str(modal.get("banned") or ""),
        quality_rules=rules,
    )


def profiles_of(convention_key: str = DEFAULT_CONVENTION) -> dict[str, ItemProfile]:
    """取某方案下 {req_type: ItemProfile}；未知方案返回空表（调用方降级）。"""
    return _load()[0].get(convention_key, {})


def get_profile(req_type: str, convention_key: str = DEFAULT_CONVENTION) -> ItemProfile | None:
    """按方案 + 条目类型取档案；无档案类型返回 None（调用方降级为纯句式规范化）。"""
    return profiles_of(convention_key).get(req_type)


def convention_catalog() -> list[ConventionMeta]:
    """全部方案元数据，按封闭集顺序（AEP-102 目录数据源）。"""
    metas = _load()[1]
    return [metas[key] for key in CONVENTION_KEYS]


def convention_meta(convention_key: str) -> ConventionMeta | None:
    return _load()[1].get(convention_key)


def convention_display_name(convention_key: str) -> str:
    meta = convention_meta(convention_key)
    return meta.display_name if meta is not None else convention_key


def common_constraints() -> CommonConstraints:
    return _load_common()


def common_constraints_text() -> str:
    """公共写作约束的 Prompt 注入块（方案无关，随任一方案档案一并注入）。"""
    c = _load_common()
    lines = [
        "【公共写作约束（适用于全部规约方案）】",
        f"模态词规范：{c.modal_intro}",
        f"- 强制：{c.modal_required}",
        f"- 推荐：{c.modal_recommended}",
        f"- 允许：{c.modal_permitted}",
        f"- 禁止：{c.modal_forbidden}",
        f"- 不得混用：{c.modal_banned}",
        "质量规则：",
    ]
    for r in c.quality_rules:
        lines.append(f"- {r.key} {r.label}：{r.rule}")
    return "\n".join(lines)
