"""配置管理入口路由（04A §9 设置工作台 / UINV-19）。

只写配置、不写治理事实；保存经审计留痕；密钥只写不回显。
外观域为浏览器本地偏好（04A §9.1），不设任何端点。
响应约定同 publication：2xx 裸 DTO；未知域/非法字段经异常处理器 → 404/400。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.schemas import (
    ConfigDomainRead,
    ConfigDomainStatusRead,
    ConfigSaveCommand,
    ConfigSaveResult,
    DomainProfileRead,
    ExportReadinessRead,
    LlmProviderListRead,
    LlmProviderSaveCommand,
    ModelCapabilityProbeResult,
    ModelConnectionTestCommand,
    ModelConnectionTestResult,
    ReferenceStandardCatalogRead,
    ReferenceStandardSaveCommand,
)
from app.deps import get_config_registry_service
from app.services.config_registry import ConfigRegistryService

router = APIRouter(tags=["config"])


@router.get("/config/domains", response_model=list[ConfigDomainStatusRead])
def list_config_domains(
    service: ConfigRegistryService = Depends(get_config_registry_service),
) -> list[ConfigDomainStatusRead]:
    """全部支撑能力配置域的状态（设置工作台左区菜单状态签）。"""
    return service.list_domain_status()


@router.get("/config/domain-profiles", response_model=list[DomainProfileRead])
def list_domain_profiles_catalog() -> list[DomainProfileRead]:
    """AEP-103：领域档案只读目录（建项目下拉 + 设置页展示）；封闭集，无写端点。"""
    from app.domain.domain_profiles import list_domain_profiles

    return [
        DomainProfileRead(key=p.key, label=p.label, description=p.description, version=p.version)
        for p in list_domain_profiles()
    ]


@router.get("/config/model-service/providers", response_model=LlmProviderListRead)
def list_model_service_providers(
    service: ConfigRegistryService = Depends(get_config_registry_service),
) -> LlmProviderListRead:
    """模型服务 provider 列表 + 启用指针 + 类型封闭集目录；密钥仅返回“已设置”标志。

    尚未保存过 provider 列表时，返回由存量单表单配置（或 env 默认）投影出的唯一 provider。
    """
    return service.list_providers()


@router.put("/config/model-service/providers", response_model=LlmProviderListRead)
def save_model_service_providers(
    command: LlmProviderSaveCommand,
    service: ConfigRegistryService = Depends(get_config_registry_service),
) -> LlmProviderListRead:
    """整表替换 provider 列表并指定启用项；写 config_audit 留痕（仅字段名，不记值）。

    不复用通用 `PUT /config/{domain}`：通用端点的 values 是平铺标量、secrets 键须命中固定
    白名单，装不下 provider 数组与逐 provider 的动态密钥键，硬塞会削弱通用端点的校验。
    """
    return service.save_providers(command)


@router.get("/config/reference-standards", response_model=ReferenceStandardCatalogRead)
def list_reference_standards(
    service: ConfigRegistryService = Depends(get_config_registry_service),
) -> ReferenceStandardCatalogRead:
    """AEP-118：引用标准目录全集（内置＋自有），含被停用的内置条目。

    只登记引用元数据（标准号/名称/年份/机构/说明/链接/类别），不承接标准全文——把某份标准
    当分析材料用请走材料接入。撰稿选取器按 enabled 过滤后消费。
    """
    return service.list_reference_standards()


@router.put("/config/reference-standards", response_model=ReferenceStandardCatalogRead)
def save_reference_standards(
    command: ReferenceStandardSaveCommand,
    service: ConfigRegistryService = Depends(get_config_registry_service),
) -> ReferenceStandardCatalogRead:
    """AEP-118：整表替换用户层（自有条目 + 内置条目停用清单）；写 config_audit 留痕。

    不复用通用 `PUT /config/{domain}`：通用端点的 values 是平铺标量，装不下条目数组，硬塞
    会绕过逐条字段校验（标准号/名称必填、类别封闭集、标识字符集与重名）。
    """
    return service.save_reference_standards(command)


@router.get("/config/export/readiness", response_model=ExportReadinessRead)
def get_export_readiness(
    service: ConfigRegistryService = Depends(get_config_registry_service),
) -> ExportReadinessRead:
    """导出能力就绪清单：逐项探测 docx 导出依赖的本地工具链。

    无输入、无副作用（只定位可执行文件并取版本，不发起转换）故用 GET；三段路径不与 `/config/{domain}` 撞。
    """
    return service.export_readiness()


@router.get("/config/{domain}", response_model=ConfigDomainRead)
def get_config_domain(
    domain: str,
    service: ConfigRegistryService = Depends(get_config_registry_service),
) -> ConfigDomainRead:
    """单域配置：已保存值覆盖 env 默认（source 标注来源）；密钥仅返回“已设置”标志。"""
    return service.get_domain(domain)


@router.put("/config/{domain}", response_model=ConfigSaveResult)
def save_config_domain(
    domain: str,
    command: ConfigSaveCommand,
    service: ConfigRegistryService = Depends(get_config_registry_service),
) -> ConfigSaveResult:
    """保存配置：字段白名单校验；写 config_audit 留痕（仅字段名，不记值）。"""
    return service.save_domain(domain, command)


@router.post("/config/model-service/test-connection", response_model=ModelConnectionTestResult)
def test_model_connection(
    command: ModelConnectionTestCommand,
    service: ConfigRegistryService = Depends(get_config_registry_service),
) -> ModelConnectionTestResult:
    """模型服务测试连接（GET {base}/models）：结果只含状态/延迟/错误码，无明文密钥。"""
    return service.test_model_connection(command)


@router.post("/config/model-service/probe-capabilities", response_model=ModelCapabilityProbeResult)
def probe_model_capabilities(
    command: ModelConnectionTestCommand,
    service: ConfigRegistryService = Depends(get_config_registry_service),
) -> ModelCapabilityProbeResult:
    """模型服务逐能力探测（C1 可达 / C2 能生成 / C3 可关思考 / C4 结构化输出 /
    C5 有效上下文 / C6 未识别字段是否静默接受）。

    请求体与两级连通测试同一个命令（地址、模型、类型、密钥取用规则一致，包括「改了地址就不能
    用已存密钥」那条断言）。返回稳定结果码与实测数值，白话文案由前端映射；不写库、不改启用状态
    ——探到的能力档案要等用户点「应用」、随 provider 配置保存才生效。
    """
    return service.probe_capabilities(command)
