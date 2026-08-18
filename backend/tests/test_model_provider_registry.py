"""模型服务多 provider 管理（T20260720-model-provider-registry · A1）。

覆盖：列表增删改与启用切换落库读回、密钥掩码与「留空=保留原值」、删除 provider 连带清密钥、
类型封闭集校验、存量单表单配置升级后作为默认 provider 完整保留、启用中 provider 决定生效配置。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.db.models  # noqa: F401  register tables
from app.api.schemas import ConfigSaveCommand
from app.config import Settings
from app.db.base import Base, make_session_factory
from app.db.models import ConfigAudit, ConfigEntry
from app.deps import get_config_registry_service
from app.main import app
from app.services.config_registry import ConfigRegistryService, resolve_llm_settings

SECRET = "sk-provider-plain-secret"
SECRET_B = "sk-provider-second-secret"


@pytest.fixture()
def session():
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


def _provider(**over):
    row = {
        "name": "本地 llama.cpp",
        "provider_type": "llama_cpp",
        "base_url": "http://llm.local/v1",
        "model": "qwen2.5",
        "timeout_seconds": 180.0,
        "max_retries": 3,
        "concurrency_limit": 5,
    }
    row.update(over)
    return row


def _save(client, providers, active=None, operator="U1"):
    return client.put(
        "/api/config/model-service/providers",
        json={"providers": providers, "active_provider_id": active, "operator_ref": operator},
    )


# ---- 存量兼容：升级前保存的单表单配置照旧生效 ----


def test_legacy_single_form_projects_as_default_provider(client, session):
    """升级前用单表单保存过的配置，升级后读侧投影为一个 id 为 default 的启用 provider。"""
    service = ConfigRegistryService(session)
    service.save_domain(
        "model_service",
        ConfigSaveCommand(
            values={"service_name": "老配置", "base_url": "http://legacy.local/v1",
                    "model": "qwen-legacy", "timeout_seconds": 42},
            secrets={"api_key": SECRET},
            operator_ref="U0",
        ),
    )
    session.commit()

    body = client.get("/api/config/model-service/providers").json()
    assert body["source"] == "env"  # 尚未保存过 providers 数组
    assert len(body["providers"]) == 1
    p = body["providers"][0]
    assert p["id"] == "default"
    assert p["name"] == "老配置"
    assert p["base_url"] == "http://legacy.local/v1"
    assert p["model"] == "qwen-legacy"
    assert p["timeout_seconds"] == 42
    assert p["active"] is True
    # 存量密钥（存在既有 api_key 键下）照旧被认作该 provider 的密钥，且不回显明文
    assert p["api_key_set"] is True
    assert SECRET not in json.dumps(body, ensure_ascii=False)


def test_legacy_api_key_still_resolves_for_default_provider(session):
    base = Settings()
    service = ConfigRegistryService(session, base_settings=base)
    service.save_domain(
        "model_service",
        ConfigSaveCommand(
            values={"base_url": "http://legacy.local/v1", "model": "qwen-legacy"},
            secrets={"api_key": SECRET},
            operator_ref="U0",
        ),
    )
    session.commit()
    effective = resolve_llm_settings(session, base)
    assert effective.llm_base_url == "http://legacy.local/v1"
    assert effective.llm_api_key == SECRET


def test_provider_types_catalog_is_backend_single_source(client):
    body = client.get("/api/config/model-service/providers").json()
    keys = [t["key"] for t in body["provider_types"]]
    assert keys == ["llama_cpp", "ollama", "vllm", "openai_compatible"]
    # 显示名与说明都来自后端，前端不得另写一份清单
    assert all(t["label"] and t["description"] for t in body["provider_types"])


# ---- 增删改与启用切换 ----


def test_save_multiple_providers_and_switch_active(client, session):
    resp = _save(
        client,
        [
            _provider(id="default", name="本地 llama.cpp"),
            _provider(name="局域网 Ollama", provider_type="ollama",
                      base_url="http://192.168.0.9:11434/v1", model="qwen2.5:7b"),
        ],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "saved"
    assert [p["name"] for p in body["providers"]] == ["本地 llama.cpp", "局域网 Ollama"]
    # 未指定启用项 → 落在第一个
    assert body["active_provider_id"] == "default"
    ollama_id = body["providers"][1]["id"]
    assert ollama_id != "default" and ollama_id  # 新增项由服务端派号

    # 切换启用项：刷新后仍在（读回自库）
    switched = _save(
        client,
        [
            _provider(id="default", name="本地 llama.cpp"),
            _provider(id=ollama_id, name="局域网 Ollama", provider_type="ollama",
                      base_url="http://192.168.0.9:11434/v1", model="qwen2.5:7b"),
        ],
        active=ollama_id,
    )
    assert switched.json()["active_provider_id"] == ollama_id
    reread = client.get("/api/config/model-service/providers").json()
    assert reread["active_provider_id"] == ollama_id
    assert [p["active"] for p in reread["providers"]] == [False, True]


def test_active_provider_drives_effective_settings(client, session):
    saved = _save(
        client,
        [
            _provider(id="default"),
            _provider(name="vLLM 服务", provider_type="vllm",
                      base_url="http://gpu.local:8000/v1", model="Qwen2.5-7B-Instruct",
                      timeout_seconds=90.0),
        ],
    ).json()
    vllm_id = saved["providers"][1]["id"]
    _save(
        client,
        [
            _provider(id="default"),
            _provider(id=vllm_id, name="vLLM 服务", provider_type="vllm",
                      base_url="http://gpu.local:8000/v1", model="Qwen2.5-7B-Instruct",
                      timeout_seconds=90.0),
        ],
        active=vllm_id,
    )
    session.commit()

    base = Settings()
    effective = resolve_llm_settings(session, base)
    assert effective.llm_base_url == "http://gpu.local:8000/v1"
    assert effective.llm_model == "Qwen2.5-7B-Instruct"
    assert effective.llm_timeout == 90.0
    assert effective.llm_provider_type == "vllm"
    # 未配置的参数仍回落 env
    assert effective.llm_max_tokens == base.llm_max_tokens


def test_active_provider_syncs_flat_fields_for_legacy_read_path(client, session):
    """启用项的连接参数同步回平铺字段，既有 /config/model_service 读端点不会与列表各说各话。"""
    _save(client, [_provider(id="default", name="甲", base_url="http://a.local/v1", model="m-a")])
    fields = {f["key"]: f["value"] for f in client.get("/api/config/model_service").json()["fields"]}
    assert fields["base_url"] == "http://a.local/v1"
    assert fields["model"] == "m-a"
    assert fields["service_name"] == "甲"


def test_delete_provider_removes_it_and_prunes_its_secret(client, session):
    created = _save(
        client,
        [_provider(id="default"), _provider(name="待删", api_key=SECRET_B)],
    ).json()
    doomed_id = created["providers"][1]["id"]
    assert created["providers"][1]["api_key_set"] is True

    _save(client, [_provider(id="default")])
    session.commit()
    entry = session.scalars(select(ConfigEntry).where(ConfigEntry.domain == "model_service")).one()
    secrets = json.loads(entry.secrets)
    # 孤儿密钥不留：既无用又是泄露面
    assert f"api_key:{doomed_id}" not in secrets
    assert SECRET_B not in entry.secrets
    assert len(client.get("/api/config/model-service/providers").json()["providers"]) == 1


# ---- 密钥：只写不回显、留空保留、可显式清除 ----


def test_secret_never_echoed_and_blank_keeps_existing(client, session):
    resp = _save(client, [_provider(id="default", api_key=SECRET)])
    assert resp.status_code == 200
    assert SECRET not in resp.text
    assert resp.json()["providers"][0]["api_key_set"] is True

    # 再存一次不带密钥（前端脱敏占位下没重输）→ 原值保留
    again = _save(client, [_provider(id="default", model="qwen-new")])
    assert again.json()["providers"][0]["api_key_set"] is True
    session.commit()
    entry = session.scalars(select(ConfigEntry).where(ConfigEntry.domain == "model_service")).one()
    assert json.loads(entry.secrets)["api_key:default"] == SECRET

    # 显式清除
    cleared = _save(client, [_provider(id="default", clear_api_key=True)])
    assert cleared.json()["providers"][0]["api_key_set"] is False


def test_new_default_secret_supersedes_legacy_key(client, session):
    """为 default 重新输入密钥后，存量老键作废——避免两处密钥并存、以后不知哪个生效。"""
    service = ConfigRegistryService(session)
    service.save_domain(
        "model_service",
        ConfigSaveCommand(values={"base_url": "http://legacy/v1"}, secrets={"api_key": SECRET},
                          operator_ref="U0"),
    )
    session.commit()
    _save(client, [_provider(id="default", api_key=SECRET_B)])
    session.commit()

    entry = session.scalars(select(ConfigEntry).where(ConfigEntry.domain == "model_service")).one()
    secrets = json.loads(entry.secrets)
    assert secrets["api_key:default"] == SECRET_B
    assert "api_key" not in secrets
    assert resolve_llm_settings(session, Settings()).llm_api_key == SECRET_B


def test_save_providers_writes_audit_with_field_names_only(client, session):
    _save(client, [_provider(id="default", api_key=SECRET)])
    session.commit()
    audit = session.scalars(select(ConfigAudit)).all()[-1]
    assert audit.domain == "model_service"
    assert audit.action == "save_providers"
    assert audit.operator_ref == "U1"
    assert set(json.loads(audit.changed_keys)) == {"providers", "active_provider_id", "api_key"}
    # 留痕只记字段名，绝不记值/密钥
    assert SECRET not in audit.changed_keys


# ---- 校验：封闭集与必填 ----


@pytest.mark.parametrize(
    "bad, hint",
    [
        ({"provider_type": "sglang"}, "类型"),
        ({"name": "  "}, "名称"),
        ({"base_url": ""}, "地址"),
        ({"model": ""}, "模型"),
    ],
)
def test_invalid_provider_rejected(client, bad, hint):
    resp = _save(client, [_provider(**bad)])
    assert resp.status_code == 400
    assert hint in resp.text


def test_client_assigned_id_is_accepted(client):
    """界面新增一条时就地派号并直接设为使用中，一次保存即生效（不必先存一遍再来设）。"""
    body = _save(client, [_provider(id="default"), _provider(id="pab12cd34", name="新加的")],
                 active="pab12cd34").json()
    assert body["active_provider_id"] == "pab12cd34"
    assert [p["id"] for p in body["providers"]] == ["default", "pab12cd34"]


@pytest.mark.parametrize("bad_id", ["a b", "p/../x", "api_key:default", "x" * 41, "中文"])
def test_malformed_provider_id_rejected(client, bad_id):
    """id 会成为密钥字典的键，字符集必须收紧。"""
    resp = _save(client, [_provider(id=bad_id)])
    assert resp.status_code == 400
    assert "标识" in resp.text


def test_duplicate_provider_id_rejected(client):
    resp = _save(client, [_provider(id="x"), _provider(id="x", name="重号")])
    assert resp.status_code == 400


def test_empty_provider_list_rejected(client):
    assert _save(client, []).status_code == 400


def test_missing_operator_rejected(client):
    resp = _save(client, [_provider()], operator="   ")
    assert resp.status_code == 400


def test_unknown_active_id_falls_back_to_first(client):
    body = _save(client, [_provider(id="default")], active="ghost").json()
    assert body["active_provider_id"] == "default"
