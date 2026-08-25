"""模型服务多 provider（列表管理与启用指针）。

自 schemas.py 按业务域拆包（命名规范见包入口 __init__.py）。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


# ---- 模型服务多 provider（T20260720：列表管理 + 启用指针；存储零迁移，落既有配置行）----


class LlmProviderTypeRead(BaseModel):
    """provider 类型封闭集目录（前端下拉的唯一来源，禁前端另写一份清单）。"""

    key: str
    label: str
    description: str


class LlmProviderRead(BaseModel):
    """单个 provider 读投影：密钥只报是否已设置，绝不回显明文。"""

    id: str
    name: str
    provider_type: str
    base_url: str
    model: str
    timeout_seconds: float
    max_retries: int
    concurrency_limit: int
    api_key_set: bool
    active: bool
    # 思考模式：是否让这个模型服务带思考跑。默认关——思考模型开着思考时重流程慢 20–50 倍
    # 直至超时，且思考段可能吃光输出预算导致正文为空（116 实测，见能力探测与参数适配提案）。
    thinking_enabled: bool = False
    # 能力探测档案：对这个端点探到的事实（能否关思考、结构化输出真生效到哪一档、有效上下文
    # 多大、探测时间）。空字典=从未探测，适配层按 provider 类型的先验默认走。
    # 形状定义在 app/adapters/llm.py（CapabilityProfile.to_payload），此处只做透明投影。
    capability_profile: dict = Field(default_factory=dict)


class LlmProviderListRead(BaseModel):
    active_provider_id: str
    providers: list[LlmProviderRead] = Field(default_factory=list)
    provider_types: list[LlmProviderTypeRead] = Field(default_factory=list)
    # saved = 库里已存 providers 数组；env = 尚未保存过，列表由存量平铺配置或 env 投影而来。
    source: str = "env"
    updated_at: str | None = None
    updated_by: str | None = None


class LlmProviderWrite(BaseModel):
    """单个 provider 写入项：id 留空=新增（服务端派号）；api_key 留空=保留原值。"""

    id: str | None = None
    name: str
    provider_type: str
    base_url: str
    model: str
    timeout_seconds: float = 180.0
    max_retries: int = 3
    concurrency_limit: int = 5
    api_key: str | None = None
    # 显式清除已保存密钥（与「留空=保留原值」区分开）。
    clear_api_key: bool = False
    # 能力探测档案：缺席（null）=保留库里已存的那份，与密钥「留空=保留原值」同一套语义；
    # 显式给出才覆盖（设置页点「应用探测结果」时带上），给空字典即清空。
    capability_profile: dict | None = None
    # 思考模式开关：同样是缺席=保留原值，显式给出才覆盖。
    thinking_enabled: bool | None = None


class LlmProviderSaveCommand(BaseModel):
    """整表替换：providers 即保存后的完整列表，缺席者视为删除（其密钥一并清除）。"""

    providers: list[LlmProviderWrite] = Field(default_factory=list)
    active_provider_id: str | None = None
    operator_ref: str
