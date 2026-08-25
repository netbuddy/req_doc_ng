"""引用标准目录（配置域 reference_standards）。

自 schemas.py 按业务域拆包（命名规范见包入口 __init__.py）。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# AEP-118 引用标准目录（配置域 reference_standards）
# ---------------------------------------------------------------------------
# 清单定义的单一来源是 app/domain/reference_standards.py：本处只做读写投影，不复制任何
# 条目内容与类别标签。只登记引用元数据，不承接标准全文（全文走材料接入）。


class ReferenceStandardCategoryRead(BaseModel):
    """类别封闭集的一项；中文标签由后端给，前端不硬编码。"""

    key: str
    label: str


class ReferenceStandardRead(BaseModel):
    """目录中的一条引用标准条目。

    builtin=True 的条目随代码版本化，只可停用（enabled=False）不可编辑；
    builtin=False 的自有条目可增可改可删，其 enabled 恒为 True。
    """

    key: str
    code: str
    title: str
    year: str = ""
    issuer: str = ""
    note: str = ""
    category: str
    category_label: str
    url: str = ""
    builtin: bool
    enabled: bool


class ReferenceStandardCatalogRead(BaseModel):
    """目录全集（内置＋自有），含被停用的内置条目。

    返回全集而非只返回启用项：设置页要展示被停用的内置条目才能让用户恢复它们。只消费启用
    项的一方（撰稿选取器）按 enabled 自行过滤。
    """

    entries: list[ReferenceStandardRead] = Field(default_factory=list)
    categories: list[ReferenceStandardCategoryRead] = Field(default_factory=list)
    builtin_count: int = 0
    custom_count: int = 0
    disabled_count: int = 0
    # saved = 库里存过用户层数据；builtin = 从未保存过，目录全部来自内置清单。
    source: str = "builtin"
    updated_at: str | None = None
    updated_by: str | None = None


class ReferenceStandardWrite(BaseModel):
    """单条自有条目写入项：key 留空＝按标准号自动生成标识。"""

    key: str | None = None
    code: str
    title: str
    year: str = ""
    issuer: str = ""
    note: str = ""
    category: str
    url: str = ""


class ReferenceStandardSaveCommand(BaseModel):
    """整表替换用户层：custom_entries 即保存后的完整自有条目列表，缺席者视为删除。

    内置条目不出现在这里——它们改不了，只能出现在 disabled_builtin_keys 里被停用。
    """

    custom_entries: list[ReferenceStandardWrite] = Field(default_factory=list)
    disabled_builtin_keys: list[str] = Field(default_factory=list)
    operator_ref: str
