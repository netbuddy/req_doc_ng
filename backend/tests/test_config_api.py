"""配置管理入口测试（04 §3.5 / CONN-006 / 04A §9）。

覆盖硬边界：保存留痕（仅字段名不记值）、密钥只写不回显（脱敏占位）、
空密钥=保留原值、未知域/字段拒绝、测试连接不回显密钥、resolve_llm_settings 读通。
"""
from __future__ import annotations

import contextlib
import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.db.models  # noqa: F401  register tables
from app.api.schemas import ConfigSaveCommand, ModelConnectionTestCommand
from app.config import Settings
from app.db.base import Base, make_engine, make_session_factory
from app.db.models import ConfigAudit, ConfigEntry
from app.deps import get_config_registry_service
from app.main import app
from app.services.config_registry import (
    SECRET_PLACEHOLDER,
    ConfigRegistryService,
    resolve_active_convention,
    resolve_export_dir,
    resolve_llm_settings,
)
from app.domain.errors import InvalidInput

SECRET = "sk-test-plain-secret"


@pytest.fixture()
def session():
    # 共享内存库（StaticPool）：TestClient 在 threadpool 线程跑 sync 路由，需跨线程共用同一 DB。
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    s = factory()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


@pytest.fixture()
def client(session):
    def _override():
        service = ConfigRegistryService(session)
        yield service
        session.commit()

    app.dependency_overrides[get_config_registry_service] = _override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_config_registry_service, None)


def _save(client, domain="model_service", values=None, secrets=None):
    return client.put(
        f"/api/config/{domain}",
        json={
            "values": values or {},
            "secrets": secrets or {},
            "operator_ref": "U1",
        },
    )


def test_domains_status_lists_capability_and_governance_domains(client):
    resp = client.get("/api/config/domains")
    assert resp.status_code == 200
    domains = {row["domain"]: row for row in resp.json()}
    assert set(domains) == {
        "model_service", "export", "chart_rendering", "requirement_convention",
        "reference_standards",
    }
    # 未保存前：生效值来自 env，configured=False
    assert all(not row["configured"] and row["source"] == "env" for row in domains.values())
    assert domains["model_service"]["downstream"] == "模型服务适配器"
    # 生成治理域（需求规约）与外部能力域语义区分
    assert domains["requirement_convention"]["group"] == "生成治理"
    # 文档资源域（引用标准目录）：既不连接外部服务，也不影响模型生成行为，单列一组
    assert domains["reference_standards"]["group"] == "文档资源"


def test_save_then_read_masks_secret_and_overlays_env(client, session):
    resp = _save(
        client,
        values={"base_url": "http://llm.local/v1", "model": "qwen-plus", "timeout_seconds": 30},
        secrets={"api_key": SECRET},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["saved"] is True
    assert set(body["changed_keys"]) >= {"base_url", "model", "api_key"}
    # 响应全文不得出现明文密钥
    assert SECRET not in resp.text

    read = client.get("/api/config/model_service")
    assert read.status_code == 200
    assert SECRET not in read.text
    data = read.json()
    fields = {f["key"]: f for f in data["fields"]}
    assert fields["base_url"]["value"] == "http://llm.local/v1"
    assert fields["base_url"]["source"] == "saved"
    # 未保存字段回落 env 默认
    assert fields["max_retries"]["source"] == "env"
    secret = data["secrets"][0]
    assert secret["key"] == "api_key"
    assert secret["set"] is True
    assert secret["placeholder"] == SECRET_PLACEHOLDER


def test_save_writes_audit_with_field_names_only(client, session):
    _save(client, values={"model": "qwen-plus"}, secrets={"api_key": SECRET})
    audits = session.scalars(select(ConfigAudit)).all()
    assert len(audits) == 1
    audit = audits[0]
    assert audit.domain == "model_service"
    assert audit.operator_ref == "U1"
    changed = json.loads(audit.changed_keys)
    assert "api_key" in changed and "model" in changed
    # 留痕只记字段名，绝不记值/密钥
    assert SECRET not in audit.changed_keys


def test_empty_secret_keeps_existing_value(client, session):
    _save(client, secrets={"api_key": SECRET})
    resp = _save(client, values={"model": "qwen-max"}, secrets={"api_key": ""})
    assert "api_key" not in resp.json()["changed_keys"]
    entry = session.scalars(select(ConfigEntry).where(ConfigEntry.domain == "model_service")).one()
    assert json.loads(entry.secrets)["api_key"] == SECRET


def test_unknown_domain_404_and_unknown_field_400(client):
    assert client.get("/api/config/appearance").status_code == 404  # 外观域无后端（04A §9.1）
    resp = _save(client, values={"prompt_template": "x"})
    assert resp.status_code == 400


def test_missing_operator_rejected(client):
    resp = client.put(
        "/api/config/model_service",
        json={"values": {"model": "m"}, "secrets": {}, "operator_ref": "  "},
    )
    assert resp.status_code == 400


def test_connection_probe_never_echoes_secret(client, session, monkeypatch):
    captured: dict = {}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers or {}
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"data": [{"id": "qwen"}]}, request=request)

    monkeypatch.setattr("app.services.config_registry.httpx.get", fake_get)
    # 存的地址要与测试请求的地址一致：已存密钥只对保存时的地址有效（外泄面守卫，C1）
    _save(client, values={"base_url": "http://llm.local/v1"}, secrets={"api_key": SECRET})
    resp = client.post(
        "/api/config/model-service/test-connection",
        json={"base_url": "http://llm.local/v1", "use_saved_key": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["model_count"] == 1
    # 已保存密钥进请求头（配置读通），但响应全文无明文
    assert captured["headers"]["Authorization"] == f"Bearer {SECRET}"
    assert SECRET not in resp.text


def test_saved_key_refused_when_base_url_changed(client, monkeypatch):
    """C1 外泄面守卫：改了地址还想用已存密钥测试 → 400 拒收，密钥绝不发往新地址。

    没有这道守卫时，一个只带 `{base_url: 攻击者地址, use_saved_key: True}` 的裸请求就能把已存
    明文密钥经 Authorization 头带向任意地址（端点无鉴权）。
    """
    calls: list[str] = []

    def fake_get(url, headers=None, timeout=None):  # 一旦被调到就说明密钥已外发，测试即失败
        calls.append(url)
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"data": []}, request=request)

    monkeypatch.setattr("app.services.config_registry.httpx.get", fake_get)
    _save(client, values={"base_url": "http://llm.local/v1"}, secrets={"api_key": SECRET})
    resp = client.post(
        "/api/config/model-service/test-connection",
        json={"base_url": "https://attacker.example/v1", "use_saved_key": True},
    )
    assert resp.status_code == 400
    assert "重新输入密钥" in resp.text
    assert calls == []  # 请求根本没发出，密钥没被带向任何地方


def test_default_does_not_use_saved_key(client, session, monkeypatch):
    """C1：use_saved_key 缺省为 False——只给 base_url 的裸请求不会替调用方取用已存密钥。"""
    captured: dict = {}

    def fake_get(url, headers=None, timeout=None):
        captured["headers"] = headers or {}
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"data": [{"id": "qwen"}]}, request=request)

    monkeypatch.setattr("app.services.config_registry.httpx.get", fake_get)
    _save(client, values={"base_url": "http://llm.local/v1"}, secrets={"api_key": SECRET})
    resp = client.post(
        "/api/config/model-service/test-connection",
        json={"base_url": "http://llm.local/v1"},  # 不写 use_saved_key
    )
    assert resp.status_code == 200
    # 缺省不带已存密钥：请求头里没有 Authorization
    assert "Authorization" not in captured["headers"]


def test_connection_probe_failure_maps_to_error_code(client, monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr("app.services.config_registry.httpx.get", fake_get)
    resp = client.post(
        "/api/config/model-service/test-connection",
        json={"base_url": "http://llm.local/v1"},
    )
    assert resp.status_code == 200  # 业务停靠：探测失败是结果，不是接口错误
    body = resp.json()
    assert body["ok"] is False
    assert body["error_code"] == "ConnectError"


def test_resolve_llm_settings_overlays_saved_config(session):
    base = Settings()
    service = ConfigRegistryService(session, base_settings=base)
    service.save_domain(
        "model_service",
        ConfigSaveCommand(
            values={"base_url": "http://cfg.local/v1", "timeout_seconds": 42},
            secrets={"api_key": SECRET},
            operator_ref="U1",
        ),
    )
    session.commit()
    effective = resolve_llm_settings(session, base)
    assert effective.llm_base_url == "http://cfg.local/v1"
    assert effective.llm_timeout == 42.0
    assert effective.llm_api_key == SECRET
    # 未配置字段保持 env 默认
    assert effective.llm_max_tokens == base.llm_max_tokens


def test_resolve_llm_settings_without_saved_config_falls_back_to_env(session):
    base = Settings()
    assert resolve_llm_settings(session, base) is base


# ---- 需求规约域（生成治理）：白名单 + 生效方案读侧（选型文档 §1.1/§2）----


def test_requirement_convention_whitelist_rejects_unknown_value(session):
    service = ConfigRegistryService(session)
    with pytest.raises(InvalidInput):
        service.save_domain(
            "requirement_convention",
            ConfigSaveCommand(values={"active_convention": "bogus-cn"}, secrets={}, operator_ref="U1"),
        )


def test_requirement_convention_accepts_whitelisted_value(session):
    service = ConfigRegistryService(session)
    service.save_domain(
        "requirement_convention",
        ConfigSaveCommand(values={"active_convention": "master-cn"}, secrets={}, operator_ref="U1"),
    )
    session.commit()
    assert resolve_active_convention(session) == "master-cn"


def test_resolve_active_convention_defaults_to_ears_cn(session):
    # 无配置行 = 默认方案（零迁移，与现状行为一致）
    assert resolve_active_convention(session) == "ears-cn"


# ---- 导出域：僵尸字段 convert_timeout_seconds 删除后的读写行为（T20260724 A1）----


def test_export_domain_no_longer_exposes_convert_timeout(client):
    read = client.get("/api/config/export")
    assert read.status_code == 200
    assert [f["key"] for f in read.json()["fields"]] == ["export_dir"]


def test_legacy_convert_timeout_payload_survives_read_write_untouched(client, session):
    """存量记录里残留的 convert_timeout_seconds：读写两侧都不炸，存量键与取值在读改写往返后仍在。

    删字段只改投影口径（DomainSpec.fields），不迁移不清理存量数据——这是卡面的兼容纪律。
    """
    session.add(
        ConfigEntry(
            domain="export",
            payload=json.dumps({"export_dir": "/old/exports", "convert_timeout_seconds": 60}),
            secrets="{}",
        )
    )
    session.commit()

    read = client.get("/api/config/export")
    assert read.status_code == 200
    fields = {f["key"]: f["value"] for f in read.json()["fields"]}
    assert fields == {"export_dir": "/old/exports"}  # 旧键不再投影

    resp = _save(client, domain="export", values={"export_dir": "/new/exports"})
    assert resp.status_code == 200
    assert resp.json()["changed_keys"] == ["export_dir"]

    entry = session.scalars(select(ConfigEntry).where(ConfigEntry.domain == "export")).one()
    session.refresh(entry)
    payload = json.loads(entry.payload)
    assert payload["export_dir"] == "/new/exports"
    assert payload["convert_timeout_seconds"] == 60  # 存量键原样留在库里，未被改写或删除
    assert [f["key"] for f in client.get("/api/config/export").json()["fields"]] == ["export_dir"]


def test_resolve_export_dir_prefers_saved_over_env(session):
    """设置页保存的导出目录必须真正被读到（此前后端只读 env，保存等于白存）。"""
    base = Settings(export_dir="/env/exports")
    assert resolve_export_dir(session, base) == "/env/exports"  # 无配置行 → env
    service = ConfigRegistryService(session, base)
    service.save_domain(
        "export", ConfigSaveCommand(values={"export_dir": "/saved/exports"}, secrets={}, operator_ref="U1")
    )
    session.commit()
    assert resolve_export_dir(session, base) == "/saved/exports"


def test_resolve_export_dir_falls_back_when_saved_value_blank(session):
    """存了空串/空白 → 回落 env，绝不把 docx 写到进程当前目录。"""
    base = Settings(export_dir="/env/exports")
    service = ConfigRegistryService(session, base)
    service.save_domain(
        "export", ConfigSaveCommand(values={"export_dir": "   "}, secrets={}, operator_ref="U1")
    )
    session.commit()
    assert resolve_export_dir(session, base) == "/env/exports"


@pytest.mark.parametrize("bad", ["exports", "~/exports", "./exports", "../exports"])
def test_export_dir_rejects_non_absolute_path_on_save(client, bad):
    """相对路径与 `~` 前缀在保存这一步就拒掉：二者都会让 docx 落到后端进程的当前目录且不报错。"""
    resp = _save(client, domain="export", values={"export_dir": bad})
    assert resp.status_code == 400
    assert "绝对路径" in resp.json()["error"]


@contextlib.contextmanager
def _log_sink():
    """收集结构化日志：reqdoc logger 关了 propagate，pytest 的 caplog 抓不到，直接挂个 handler。"""
    lines: list[str] = []

    class _Sink(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(record.getMessage())

    logger, sink = logging.getLogger("reqdoc"), _Sink()
    logger.addHandler(sink)
    try:
        yield lines
    finally:
        logger.removeHandler(sink)


def test_resolve_export_dir_falls_back_when_saved_value_not_absolute(session):
    """库里已有的坏值（校验上线前存下的/绕开界面直写的）：回落 env，并留一行 WARN。"""
    base = Settings(export_dir="/env/exports")
    session.add(
        ConfigEntry(domain="export", payload=json.dumps({"export_dir": "~/exports"}), secrets="{}")
    )
    session.commit()
    with _log_sink() as lines:
        assert resolve_export_dir(session, base) == "/env/exports"
    events = [json.loads(line) for line in lines]
    fallback = [e for e in events if e["event"] == "config.export_dir.invalid_fallback"]
    assert len(fallback) == 1
    assert fallback[0]["level"] == "WARN" and fallback[0]["error_code"] == "not_absolute_path"
    assert "~/exports" not in "".join(lines)  # 只记来源与原因码，绝不记目录取值（硬规则 8）


def test_resolve_export_dir_logs_which_source_it_used(session):
    """回落 env 与用保存值各留一行来源，好在事后答上「这次导出用的是哪个目录配置」。"""
    base = Settings(export_dir="/env/exports")
    with _log_sink() as lines:
        resolve_export_dir(session, base)  # 无配置行
    assert [json.loads(x)["source"] for x in lines if "export_dir.resolved" in x] == ["env"]

    ConfigRegistryService(session, base).save_domain(
        "export", ConfigSaveCommand(values={"export_dir": "/saved/exports"}, secrets={}, operator_ref="U1")
    )
    session.commit()
    with _log_sink() as lines:
        resolve_export_dir(session, base)
    resolved = [json.loads(x) for x in lines if "export_dir.resolved" in x]
    assert [e["source"] for e in resolved] == ["saved"]
    assert "/saved/exports" not in "".join(lines)


def test_export_domain_rejects_removed_field_on_save(client):
    # 字段已从白名单移除：显式再写它按未知字段拒绝（与其它未知字段同一口径）
    assert _save(client, domain="export", values={"convert_timeout_seconds": 90}).status_code == 400


# ---- 导出能力就绪清单（T20260724 A2/A3）----


def _readiness(client):
    resp = client.get("/api/config/export/readiness")
    assert resp.status_code == 200
    body = resp.json()
    return body, {item["key"]: item for item in body["items"]}


def test_export_readiness_matches_adapter_verdicts(client):
    """就绪结论必须与适配器判定同源（A3）：soffice 项 == pdf_render_available()，图形两项 == resolve_tools()。"""
    from app.adapters.diagram_render import resolve_tools
    from app.adapters.docx_to_pdf import pdf_render_available

    body, items = _readiness(client)
    assert [i["key"] for i in body["items"]] == ["pdf_preview", "mermaid_diagram", "plantuml_diagram"]
    tools = resolve_tools()
    assert items["pdf_preview"]["ready"] is pdf_render_available()
    assert items["mermaid_diagram"]["ready"] is (tools["mmdc"] is not None)
    assert items["plantuml_diagram"]["ready"] is (tools["java"] is not None and tools["plantuml_jar"] is not None)
    assert body["all_ready"] is all(i["ready"] for i in body["items"])


def test_export_readiness_never_converts_or_renders(client, monkeypatch):
    """探测零副作用（A2）：不得调用任何真实转换/渲染入口。"""
    from app.adapters import diagram_render, docx_to_pdf

    def _boom(*args, **kwargs):  # pragma: no cover - 被调用即测试失败
        raise AssertionError("就绪探测不得发起真实转换/渲染")

    monkeypatch.setattr(docx_to_pdf, "convert_docx_to_pdf", _boom)
    monkeypatch.setattr(diagram_render, "render_to_png", _boom)
    body, _ = _readiness(client)
    assert isinstance(body["checked_at"], str) and body["checked_at"]


def test_export_readiness_reports_missing_tools_with_stable_outcomes(client, monkeypatch):
    """缺失态：结果码指出缺的是哪一个依赖，且路径/版本不再下发（A2 构造缺失态）。"""
    from app.services import config_registry as registry

    monkeypatch.setattr(registry, "find_soffice", lambda: None)
    monkeypatch.setattr(
        registry, "resolve_tools", lambda: {"mmdc": None, "java": None, "plantuml_jar": None}
    )
    body, items = _readiness(client)
    assert body["all_ready"] is False
    assert items["pdf_preview"]["outcome"] == "soffice_missing"
    assert items["mermaid_diagram"]["outcome"] == "mmdc_missing"
    assert items["plantuml_diagram"]["outcome"] == "java_missing"
    assert all(i["path"] is None and i["version"] is None for i in body["items"])


def test_blank_jar_path_reads_the_same_on_both_sides(monkeypatch):
    """PLANTUML_JAR 显式设成空串时，就绪清单与真实渲染必须给同一个结论：jar 不在位。

    这是「防两处漂移」那条约束真出过事的地方：`Path('')` 等于 `PosixPath('.')`、`.exists()` 为真，
    所以渲染侧曾经不判缺失，而是带着 `-jar ""` 去起 java，最后以「渲染失败」收场——
    与清单侧报的「工具缺失」性质不同，用户看到两套说法。
    """
    import dataclasses

    from app.adapters import diagram_render

    monkeypatch.setattr(
        diagram_render, "settings", dataclasses.replace(diagram_render.settings, plantuml_jar_path="")
    )
    monkeypatch.setattr(diagram_render, "_resolve_java", lambda: "/usr/bin/java")
    assert diagram_render.resolve_tools()["plantuml_jar"] is None
    with pytest.raises(diagram_render.DiagramRenderUnavailable):
        diagram_render.render_to_png("@startuml\nA -> B\n@enduml", "plantuml")


def test_export_readiness_distinguishes_missing_jar_from_missing_java(client, monkeypatch):
    """Java 在、jar 不在：结果码是 plantuml_jar_missing，不与 java_missing 混为一谈。"""
    from app.services import config_registry as registry

    monkeypatch.setattr(
        registry, "resolve_tools", lambda: {"mmdc": "/x/mmdc", "java": "/x/java", "plantuml_jar": None}
    )
    monkeypatch.setattr(registry, "mmdc_version", lambda _p: None)
    _body, items = _readiness(client)
    assert items["plantuml_diagram"]["outcome"] == "plantuml_jar_missing"
    assert items["plantuml_diagram"]["ready"] is False
    # 版本取不到不影响就绪结论：mmdc 定位到了就算就绪
    assert items["mermaid_diagram"] == {
        "key": "mermaid_diagram", "ready": True, "outcome": "ready",
        "path": "/x/mmdc", "version": None,
    }
