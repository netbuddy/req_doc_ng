"""领域档案（domain profile 知识资产）加载（P6b；设计 08 §2）。

要点（复用 item_profiles 纪律）：
- 档案随代码版本化：本目录 <profile_key>/{_meta.yaml, glossary_seed.yaml, common_terms.yaml,
  rule_patterns.yaml}；不入库、不提供运行时编辑（改档案=改代码+评审）。
- 封闭集：generic 必在（默认，内容为空集，行为等同 P6a）；其余为首批试点领域。
- 幻觉防线：glossary_seed/common_terms/rule_patterns 只服务业务翼抽取的判别与显著性判据，
  不得作为要素内容来源（防线声明在注入段，见 partials/domain_reference.jinja2）。
- key 为稳定 ASCII 码；version 语义变更 +1；坏档案（缺 _meta/字段非法）→ 加载即 raise。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

_PROFILE_DIR = Path(__file__).resolve().parent
GENERIC_KEY = "generic"


class DomainProfileError(Exception):
    """领域档案缺失/不可读/schema 不符——阻塞加载，不以默认冒充。"""


@dataclass(frozen=True)
class GlossaryTerm:
    term: str
    definition: str


@dataclass(frozen=True)
class DomainProfile:
    key: str
    label: str
    description: str
    version: int
    glossary_seed: tuple[GlossaryTerm, ...] = field(default_factory=tuple)
    common_terms: tuple[str, ...] = field(default_factory=tuple)
    rule_patterns: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_empty(self) -> bool:
        """generic（或任何空内容档案）：无注入内容，行为等同 P6a。"""
        return not (self.glossary_seed or self.common_terms or self.rule_patterns)


def _read_yaml(path: Path):
    if not path.exists():
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:  # noqa: BLE001
        raise DomainProfileError(f"领域档案文件不可读/非法：{path.name}（{type(exc).__name__}）") from exc


def _str_list(raw, where: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or any(not isinstance(x, str) for x in raw):
        raise DomainProfileError(f"领域档案 {where} 必须是字符串列表")
    return tuple(x.strip() for x in raw if x.strip())


def _parse_profile(profile_dir: Path) -> DomainProfile:
    key = profile_dir.name
    meta = _read_yaml(profile_dir / "_meta.yaml")
    if not isinstance(meta, dict):
        raise DomainProfileError(f"领域档案缺 _meta.yaml 或非映射：{key}")
    if meta.get("key") != key:
        raise DomainProfileError(f"领域档案 key 与目录名不一致：{key} vs {meta.get('key')!r}")
    label = str(meta.get("label") or "").strip()
    if not label:
        raise DomainProfileError(f"领域档案缺 label：{key}")
    try:
        version = int(meta.get("version"))
    except (TypeError, ValueError) as exc:
        raise DomainProfileError(f"领域档案 version 非法：{key}") from exc

    glossary_raw = _read_yaml(profile_dir / "glossary_seed.yaml") or []
    glossary: list[GlossaryTerm] = []
    if not isinstance(glossary_raw, list):
        raise DomainProfileError(f"领域档案 glossary_seed 必须是列表：{key}")
    for row in glossary_raw:
        if not isinstance(row, dict) or not str(row.get("term") or "").strip():
            raise DomainProfileError(f"领域档案 glossary_seed 行缺 term：{key}")
        glossary.append(GlossaryTerm(
            term=str(row["term"]).strip(), definition=str(row.get("definition") or "").strip()))

    return DomainProfile(
        key=key, label=label, description=str(meta.get("description") or "").strip(),
        version=version, glossary_seed=tuple(glossary),
        common_terms=_str_list(_read_yaml(profile_dir / "common_terms.yaml"), f"{key}/common_terms"),
        rule_patterns=_str_list(_read_yaml(profile_dir / "rule_patterns.yaml"), f"{key}/rule_patterns"),
    )


@lru_cache(maxsize=1)
def load_domain_profiles() -> dict[str, DomainProfile]:
    """加载封闭集（目录遍历 + schema 校验）；generic 必在，缺失即错。"""
    profiles: dict[str, DomainProfile] = {}
    for child in sorted(_PROFILE_DIR.iterdir()):
        if not child.is_dir() or child.name.startswith("__"):
            continue
        profiles[child.name] = _parse_profile(child)
    if GENERIC_KEY not in profiles:
        raise DomainProfileError("领域档案封闭集缺 generic（默认档案必在）")
    return profiles


def domain_profile_keys() -> tuple[str, ...]:
    return tuple(load_domain_profiles().keys())


def get_domain_profile(key: str | None) -> DomainProfile:
    """按 key 取档案；None/未知 key 降级为 generic（等同 P6a，零迁移安全）。"""
    profiles = load_domain_profiles()
    return profiles.get(key or GENERIC_KEY) or profiles[GENERIC_KEY]


def list_domain_profiles() -> list[DomainProfile]:
    """AEP-103 只读目录：generic 置顶，其余按 key 序。"""
    profiles = load_domain_profiles()
    ordered = [profiles[GENERIC_KEY]]
    ordered += [p for k, p in profiles.items() if k != GENERIC_KEY]
    return ordered


GLOSSARY_INJECT_LIMIT = 30  # 领域术语注入上限（控 token；08 §2.3）


def render_domain_reference(profile: DomainProfile | None) -> dict[str, str]:
    """渲染领域判别参考模板变量（截断上限）；空档案/generic 返回空串 → 模板省整段。"""
    if profile is None or profile.is_empty:
        return {"domain_glossary": "", "domain_common_terms": "", "domain_rule_patterns": ""}
    glossary = "；".join(
        f"{t.term}={t.definition}" if t.definition else t.term
        for t in profile.glossary_seed[:GLOSSARY_INJECT_LIMIT]
    )
    return {
        "domain_glossary": glossary,
        "domain_common_terms": "、".join(profile.common_terms),
        "domain_rule_patterns": "；".join(profile.rule_patterns),
    }
