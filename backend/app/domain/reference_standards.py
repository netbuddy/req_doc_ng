"""引用标准目录（T20260721-reference-standards-catalog）。

需求工程常引标准文件的**引用元数据**：标准号、名称、版本年份、发布机构、一句适用说明、
可选链接、类别。目录服务于「参考资料」类章节的撰稿——把一条条目按统一引用行格式插入撰稿
正文，撰稿正文仍是唯一权威。

要点：
- **本模块是内置清单的唯一来源**：配置域白名单、读侧合并、设置页目录面板与撰稿选取器都从
  这里取，任何一方都不得复制第二份清单定义（对齐 item_profiles 的 CONVENTION_KEYS 纪律）。
- **只登记引用元数据，不存标准全文、不做上传**：要把某份标准当分析材料用，请走既有材料接入
  管线。界面上另有一句划界说明。
- **两层存储**：内置清单随代码版本化（改清单＝改代码＋评审）；用户增补的自有条目与被停用的
  内置条目落配置存储的一行 JSON（零数据库迁移）。本模块只提供归一、校验与合并的纯函数，
  不碰数据库、不读配置存储。
- **内置条目不可编辑，只可停用**：停用只写「被停用的标识」，内置定义本身不进配置存储——
  内置条目日后随代码修订（如标准出了新版）时，用户侧不会留着一份过期副本。

「参考资料类章节」的判定（`is_reference_section_title`）也在本模块，同为单一来源：撰稿选取
入口是否出现由后端算好、以布尔字段下发，前端不得散落章节 key 字符串。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# 类别封闭集
# ---------------------------------------------------------------------------
# 元组顺序即目录排序时的类别次序（国际标准在前、指南在后）。
CATEGORY_KEYS: tuple[str, ...] = ("international", "national", "guide")
CATEGORY_LABELS: dict[str, str] = {
    "international": "国际标准",
    "national": "国家标准",
    "guide": "指南",
}
DEFAULT_CATEGORY = "national"

# 条目标识的字符集：它是停用清单里的键，也是前端列表的 React key，必须稳定且可作 ASCII 标识。
_ENTRY_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")

# 字段长度上限：拦住把整份标准正文粘进说明字段这类误用（目录只登记元数据）。
_MAX_LEN: dict[str, int] = {
    "code": 80,
    "title": 200,
    "year": 20,
    "issuer": 120,
    "note": 200,
    "url": 500,
}


@dataclass(frozen=True)
class ReferenceStandard:
    """一条引用标准条目。

    key       稳定标识（内置条目由本模块给定；自有条目保存时校验或按标准号自动生成）
    code      标准号完整形式，含年份，如 `GB/T 8567-2006`
    title     标准名称
    year      版本年份（与 code 里的年份一致；单列一列便于目录展示与排序）
    issuer    发布机构
    note      一句适用说明（在需求工作中什么场景引用它）
    category  类别，取值属 CATEGORY_KEYS
    url       官方出处链接，可空
    builtin   True＝内置条目（不可编辑，只可停用）；False＝用户自有条目
    """

    key: str
    code: str
    title: str
    year: str
    issuer: str
    note: str
    category: str
    url: str = ""
    builtin: bool = True


# ---------------------------------------------------------------------------
# 内置预置清单
# ---------------------------------------------------------------------------
# 纪律：每条的标准号与现行版本年份都经官方渠道查证（国标查国家标准全文公开系统，ISO/IEC 查
# iso.org，IEEE 查 standards.ieee.org，INCOSE 查 incose.org）；查不到权威出处的候选一律弃收
# ——引错标准号比留白更糟。查证出处逐条记在任务卡「## 预置清单核实」节。
#
# 条目标识（key）不带年份：标准出新版时只改本表内容、标识不变，用户此前「停用了哪几条」的
# 选择才不会因为标识变了而失效（停用清单存的就是这些标识）。
#
# title 一律用标准的**官方名称**：国标用官方中文名，国际标准与指南用官方英文名——引用行会
# 原样插进交付文档，写非官方译名会让引用对不上原件。
BUILTIN_STANDARDS: tuple[ReferenceStandard, ...] = (
    ReferenceStandard(
        key="iso-iec-ieee-29148",
        code="ISO/IEC/IEEE 29148:2018",
        title="Systems and software engineering — Life cycle processes — Requirements engineering",
        year="2018",
        issuer="ISO/IEC/IEEE",
        note="需求获取、分析与规格说明撰写的过程与文档结构依据",
        category="international",
        url="https://www.iso.org/standard/72089.html",
    ),
    ReferenceStandard(
        key="iso-iec-25010",
        code="ISO/IEC 25010:2023",
        title=(
            "Systems and software engineering — Systems and software Quality Requirements and "
            "Evaluation (SQuaRE) — Product quality model"
        ),
        year="2023",
        issuer="ISO/IEC",
        note="定义产品质量特性，写质量属性需求时的分类依据",
        category="international",
        url="https://www.iso.org/standard/78176.html",
    ),
    ReferenceStandard(
        key="iso-iec-ieee-12207",
        code="ISO/IEC/IEEE 12207:2026",
        title="Systems and software engineering — Software life cycle processes",
        year="2026",
        issuer="ISO/IEC/IEEE",
        note="软件生命周期过程框架，界定需求活动在全流程中的位置",
        category="international",
        url="https://www.iso.org/standard/90219.html",
    ),
    ReferenceStandard(
        key="gbt-8567",
        code="GB/T 8567-2006",
        title="计算机软件文档编制规范",
        year="2006",
        issuer="国家质量监督检验检疫总局、国家标准化管理委员会",
        note="规定软件开发各阶段应编制的文档种类与格式",
        category="national",
        url="https://openstd.samr.gov.cn/bzgk/gb/newGbInfo?hcno=84C42B6277D2714B7176B10C6E6B1A44",
    ),
    ReferenceStandard(
        key="gbt-9385",
        code="GB/T 9385-2008",
        title="计算机软件需求规格说明规范",
        year="2008",
        issuer="国家质量监督检验检疫总局、国家标准化管理委员会",
        note="编写软件需求规格说明的内容组成与质量要求",
        category="national",
        url="https://openstd.samr.gov.cn/bzgk/gb/newGbInfo?hcno=2790825C43AD0B69E3C38C140BFFCFE6",
    ),
    ReferenceStandard(
        key="gbt-11457",
        code="GB/T 11457-2006",
        title="信息技术 软件工程术语",
        year="2006",
        issuer="国家质量监督检验检疫总局、国家标准化管理委员会",
        note="统一软件工程术语与定义，供文档引用术语出处",
        category="national",
        url="https://openstd.samr.gov.cn/bzgk/gb/newGbInfo?hcno=07E3E9867D23EA5A74EB525A44622E86",
    ),
    ReferenceStandard(
        key="incose-gtwr",
        code="INCOSE-TP-2010-006-04",
        title="INCOSE Guide to Writing Requirements",
        year="2023",
        issuer="INCOSE 需求工作组",
        note="单条需求语句的写法规则与可核查性判据参考",
        category="guide",
        url=(
            "https://www.incose.org/docs/default-source/working-groups/requirements-wg/"
            "guidetowritingrequirements/incose_rwg_gtwr_v4_summary_sheet.pdf"
        ),
    ),
)

BUILTIN_KEYS: frozenset[str] = frozenset(s.key for s in BUILTIN_STANDARDS)


# ---------------------------------------------------------------------------
# 「参考资料类章节」判定
# ---------------------------------------------------------------------------
# 按章节**标题**关键词判定，不按章节 key。实证依据（2026-07-22 析案）：用户自建的
# 「4XXB SRS 模板」v2 里，key 为 `intro.references` 的章节标题是「1.3 文档概述」（根本不是
# 参考资料），而真正的参考资料章节「2 引用文档」key 却是 `overview`——自定义模板的 key 从
# 内置骨架复制而来，与语义脱钩，按 key 判会两头判错。
_REFERENCE_TITLE_TOKENS: tuple[str, ...] = (
    "参考资料",
    "参考文献",
    "引用文档",
    "引用文件",
    "引用标准",
    "规范性引用",
    "references",
)


def is_reference_section_title(title: str | None) -> bool:
    """章节标题看起来是不是「参考资料」类章节。

    判定是启发式的：模板由用户自由撰写，系统拿不到语义标记（章节内容类型是封闭集，加新标记
    要改模板 schema）。判错的后果有限——命中只是多给一个「从目录选取」的快捷入口，漏判也只是
    少一个快捷方式，撰稿本身照常。
    """
    text = (title or "").strip().lower()
    if not text:
        return False
    return any(token in text for token in _REFERENCE_TITLE_TOKENS)


# ---------------------------------------------------------------------------
# 自有条目：归一（读侧求稳）与校验（写侧从严）
# ---------------------------------------------------------------------------


def _clean(value: Any, field: str) -> str:
    text = str(value or "").strip()
    limit = _MAX_LEN.get(field)
    return text[:limit] if limit else text


def slug_from_code(code: str) -> str:
    """按标准号生成条目标识：`GB/T 8567-2006` → `gb-t-8567-2006`。

    只在保存时用户没给标识的情况下兜底；生成不出合法标识（如标准号全是中文）时返回空串，
    由调用方另行取值。
    """
    slug = re.sub(r"[^A-Za-z0-9]+", "-", (code or "").lower()).strip("-")
    return slug[:40].strip("-")


def normalize_custom_entries(rows: Any) -> tuple[ReferenceStandard, ...]:
    """配置存储里的自有条目 JSON → 条目元组（读侧求稳：坏行跳过，不抛异常）。

    读侧宽容是有意的：配置存储的这一行 JSON 是历史数据，若某次写入留下了缺字段的行，目录该
    照常打开而不是整页报错。写侧的 `validate_custom_entries` 才是从严的那一道。
    """
    if not isinstance(rows, list):
        return ()
    out: list[ReferenceStandard] = []
    seen: set[str] = set()
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        key = _clean(raw.get("key"), "key")
        code = _clean(raw.get("code"), "code")
        title = _clean(raw.get("title"), "title")
        if not _ENTRY_KEY_RE.match(key) or not code or not title:
            continue
        if key in seen or key in BUILTIN_KEYS:
            continue
        seen.add(key)
        category = str(raw.get("category") or "")
        out.append(ReferenceStandard(
            key=key,
            code=code,
            title=title,
            year=_clean(raw.get("year"), "year"),
            issuer=_clean(raw.get("issuer"), "issuer"),
            note=_clean(raw.get("note"), "note"),
            category=category if category in CATEGORY_KEYS else DEFAULT_CATEGORY,
            url=_clean(raw.get("url"), "url"),
            builtin=False,
        ))
    return tuple(out)


def normalize_disabled_keys(rows: Any) -> tuple[str, ...]:
    """配置存储里的停用清单 JSON → 标识元组；不认识的标识丢弃。

    丢弃而非报错：内置条目日后可能因标准废止而从代码里删除，此时存量的停用标识成了孤儿，
    留着无害但也无意义。
    """
    if not isinstance(rows, list):
        return ()
    seen: list[str] = []
    for raw in rows:
        key = str(raw or "").strip()
        if key in BUILTIN_KEYS and key not in seen:
            seen.append(key)
    return tuple(seen)


def validate_custom_entries(rows: list[dict[str, Any]]) -> tuple[ReferenceStandard, ...]:
    """保存前校验自有条目（写侧从严）：任何一条不合规即抛 ValueError，说明是哪一条哪里不合规。

    调用方（配置管理服务）负责把 ValueError 转成接口层的 400。
    """
    out: list[ReferenceStandard] = []
    seen: set[str] = set()
    for index, raw in enumerate(rows, start=1):
        where = f"第 {index} 条自有条目"
        code = _clean(raw.get("code"), "code")
        title = _clean(raw.get("title"), "title")
        if not code:
            raise ValueError(f"{where}的标准号不能为空")
        if not title:
            raise ValueError(f"{where}（{code}）的名称不能为空")
        key = _clean(raw.get("key"), "key") or slug_from_code(code)
        if not key:
            raise ValueError(f"{where}（{code}）无法由标准号生成标识，请手动填写条目标识")
        if not _ENTRY_KEY_RE.match(key):
            raise ValueError(
                f"{where}的标识只能用字母、数字、连字符与下划线（至多 40 位）：{key!r}"
            )
        if key in BUILTIN_KEYS:
            raise ValueError(f"{where}的标识与内置条目重名：{key}（内置条目请用停用，不要覆盖）")
        if key in seen:
            raise ValueError(f"{where}的标识重复：{key}")
        category = str(raw.get("category") or "")
        if category not in CATEGORY_KEYS:
            allowed = "、".join(f"{k}（{CATEGORY_LABELS[k]}）" for k in CATEGORY_KEYS)
            raise ValueError(f"{where}（{code}）的类别非法：{category!r}；允许：{allowed}")
        url = _clean(raw.get("url"), "url")
        if url and not url.startswith(("http://", "https://")):
            raise ValueError(f"{where}（{code}）的链接必须以 http:// 或 https:// 开头")
        seen.add(key)
        out.append(ReferenceStandard(
            key=key, code=code, title=title,
            year=_clean(raw.get("year"), "year"),
            issuer=_clean(raw.get("issuer"), "issuer"),
            note=_clean(raw.get("note"), "note"),
            category=category, url=url, builtin=False,
        ))
    return tuple(out)


def validate_disabled_keys(keys: list[str]) -> tuple[str, ...]:
    """保存前校验停用清单：只能停用现存的内置条目；不认识的标识即报错（防拼错被静默吞掉）。"""
    out: list[str] = []
    for raw in keys:
        key = str(raw or "").strip()
        if key not in BUILTIN_KEYS:
            raise ValueError(f"要停用的内置条目不存在：{key!r}")
        if key not in out:
            out.append(key)
    return tuple(out)


# ---------------------------------------------------------------------------
# 合并：内置（标注停用）＋ 自有
# ---------------------------------------------------------------------------


def _sort_key(entry: ReferenceStandard) -> tuple[int, str, str]:
    """排序口径：类别（按 CATEGORY_KEYS 次序）→ 标准号 → 条目标识。

    末位加标识是为了让排序完全确定——两条标准号相同的条目（用户增补时可能出现）不会因字典
    序不稳定而在两次读取间换位。
    """
    try:
        category_rank = CATEGORY_KEYS.index(entry.category)
    except ValueError:
        category_rank = len(CATEGORY_KEYS)
    return (category_rank, entry.code, entry.key)


def merge_catalog(
    custom: tuple[ReferenceStandard, ...], disabled_keys: tuple[str, ...],
) -> tuple[tuple[ReferenceStandard, bool], ...]:
    """内置清单＋自有条目 → 目录全集，每条附带「是否启用」。

    返回全集而非只返回启用项：设置页的目录面板要展示被停用的内置条目才能让用户恢复它们。
    只消费启用项的一方（撰稿选取器）自行过滤 enabled。
    """
    disabled = set(disabled_keys)
    merged = [(s, s.key not in disabled) for s in BUILTIN_STANDARDS]
    merged.extend((s, True) for s in custom)
    merged.sort(key=lambda pair: _sort_key(pair[0]))
    return tuple(merged)
