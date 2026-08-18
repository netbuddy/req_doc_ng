"""设置页两级连通测试（T20260720-model-provider-registry · A3）。

第一级「可达」＝带鉴权 GET {base_url}/models；第二级「正确响应」＝一次最小生成请求。
四种失败形态各给稳定结果码（白话文案由前端映射）：不可达/超时、鉴权失败、模型不存在、响应形状异常。
全部对着真实的本地契约桩件跑——桩件形态与「为什么不用传输层打桩」见 tests/provider_stub.py。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.db.models  # noqa: F401  register tables
from app.db.base import Base, make_session_factory
from app.db.models import ConfigEntry
from app.deps import get_config_registry_service
from app.main import app
from app.services.config_registry import ConfigRegistryService
from tests.provider_stub import ProviderStub

SECRET = "sk-level-test-secret"


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


def _test_connection(client, **over):
    payload = {"base_url": "", "model": "qwen2.5", "timeout_seconds": 5.0,
               "provider_type": "llama_cpp", "use_saved_key": False}
    payload.update(over)
    return client.post("/api/config/model-service/test-connection", json=payload).json()


# ---- 第一级：可达 ----


def test_reachability_ok_reports_latency_and_model_list(client):
    with ProviderStub(models=("qwen2.5", "qwen3")) as stub:
        body = _test_connection(client, base_url=stub.base_url)
    assert body["ok"] is True
    assert body["outcome"] == "ok"
    assert body["level"] == "reachability"
    assert body["model_count"] == 2
    assert body["model_listed"] is True
    assert body["latency_ms"] >= 0
    assert body["models"] == ["qwen2.5", "qwen3"]


def test_reachability_reports_model_missing_when_not_listed(client):
    with ProviderStub(models=("qwen3",)) as stub:
        body = _test_connection(client, base_url=stub.base_url, model="qwen2.5")
    assert body["ok"] is False
    assert body["outcome"] == "model_missing"
    assert body["model_listed"] is False
    # 端点上有哪些模型一并回报，用户能照着改
    assert body["models"] == ["qwen3"]


def test_reachability_without_model_does_not_judge_listing(client):
    with ProviderStub(models=("qwen3",)) as stub:
        body = _test_connection(client, base_url=stub.base_url, model="")
    assert body["ok"] is True
    assert body["model_listed"] is None


def test_reachability_auth_failed(client):
    with ProviderStub(require_api_key="right-key") as stub:
        body = _test_connection(client, base_url=stub.base_url, api_key="wrong-key")
    assert body["ok"] is False
    assert body["outcome"] == "auth_failed"
    assert body["error_code"] == "http_401"


def test_reachability_unreachable_on_closed_port(client):
    # 取一个已关闭的端口：桩件退出后其端口即不再监听
    with ProviderStub() as stub:
        dead_url = stub.base_url
    body = _test_connection(client, base_url=dead_url)
    assert body["ok"] is False
    assert body["outcome"] == "unreachable"


def test_reachability_timeout(client):
    with ProviderStub(delay_seconds=1.0) as stub:
        body = _test_connection(client, base_url=stub.base_url, timeout_seconds=0.2)
    assert body["ok"] is False
    assert body["outcome"] == "timeout"


def test_reachability_bad_response_shape(client):
    # OpenAI 兼容面的 /models 必须回 {"data": [...]}；回别的形状说明地址指错了地方
    with ProviderStub(models_body_override={"models": ["qwen2.5"]}) as stub:
        body = _test_connection(client, base_url=stub.base_url)
    assert body["ok"] is False
    assert body["outcome"] == "bad_response"
    assert body["error_code"] == "model_list_shape"


_CLOSED_OUTCOMES = {"ok", "unreachable", "timeout", "auth_failed", "model_missing", "bad_response"}


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://192.168.1.10:8O80/v1",  # 端口打成字母 O
        "http://[::1/v1",               # 坏 IPv6（缺右括号）
    ],
)
def test_malformed_base_url_stays_in_closed_result_set(client, bad_url):
    """C2：地址写坏（httpx 构造请求即抛 InvalidURL）不能逃成未捕获 500，须落封闭结果集。

    没有把 httpx.InvalidURL 纳入两个 probe 的 except 时，一个合理的地址笔误会得到裸 500——正是
    这个「两级测试」本应优雅拦下的那类错误。这里断言得到的是 200 + 封闭集里的结果码。
    """
    resp = client.post(
        "/api/config/model-service/test-connection",
        json={"base_url": bad_url, "model": "qwen2.5", "provider_type": "llama_cpp",
              "use_saved_key": False},
    )
    assert resp.status_code == 200  # 不是 500
    body = resp.json()
    assert body["ok"] is False
    assert body["outcome"] in _CLOSED_OUTCOMES
    assert body["outcome"] == "unreachable"


def test_malformed_base_url_in_generation_level_too(client):
    """C2：第二级生成测试同样把畸形地址收进封闭集（两处 except 都要改）。"""
    resp = client.post(
        "/api/config/model-service/test-connection",
        json={"base_url": "http://192.168.1.10:8O80/v1", "model": "qwen2.5",
              "provider_type": "llama_cpp", "level": "generation", "use_saved_key": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["outcome"] in _CLOSED_OUTCOMES


# ---- 第二级：正确响应 ----


def test_generation_ok_reports_reply_length_only(client):
    with ProviderStub(chat_content="OK") as stub:
        body = _test_connection(client, base_url=stub.base_url, level="generation")
    assert body["ok"] is True
    assert body["outcome"] == "ok"
    assert body["level"] == "generation"
    assert body["reply_length"] == 2
    # 响应正文不外带：结果里只报长度，任何字段都不承载模型回复原文
    assert all(value != "OK" for value in body.values() if isinstance(value, str))


def test_generation_model_missing_maps_from_404(client):
    with ProviderStub(models=("qwen3",)) as stub:
        body = _test_connection(client, base_url=stub.base_url, model="qwen2.5", level="generation")
    assert body["ok"] is False
    assert body["outcome"] == "model_missing"
    assert body["error_code"] == "http_404"


def test_generation_auth_failed(client):
    with ProviderStub(require_api_key="right-key") as stub:
        body = _test_connection(client, base_url=stub.base_url, level="generation",
                                api_key="wrong-key")
    assert body["ok"] is False
    assert body["outcome"] == "auth_failed"


def test_generation_empty_content_is_bad_response(client):
    # 200 但没回出内容（形状不对、或推理段吃光了 token）：对使用者同样是不可用
    with ProviderStub(chat_content="   ") as stub:
        body = _test_connection(client, base_url=stub.base_url, level="generation")
    assert body["ok"] is False
    assert body["outcome"] == "bad_response"
    assert body["error_code"] == "empty_content"


def test_generation_timeout(client):
    with ProviderStub(delay_seconds=1.0) as stub:
        body = _test_connection(client, base_url=stub.base_url, level="generation",
                                timeout_seconds=0.2)
    assert body["outcome"] == "timeout"


def test_generation_requires_model(client):
    with ProviderStub() as stub:
        resp = client.post(
            "/api/config/model-service/test-connection",
            json={"base_url": stub.base_url, "model": "", "level": "generation"},
        )
    assert resp.status_code == 400


# ---- 草稿可测、不写库、不动启用状态 ----


def test_unsaved_draft_can_be_tested_without_any_config_row(client, session):
    """未保存的草稿也能测：地址/模型/类型全部随请求体来，服务端不读库。"""
    assert session.scalars(select(ConfigEntry)).all() == []
    with ProviderStub(models=("draft-model",)) as stub:
        body = _test_connection(client, base_url=stub.base_url, model="draft-model")
    assert body["ok"] is True
    # 测试动作不写库
    assert session.scalars(select(ConfigEntry)).all() == []


def test_saved_key_of_named_provider_is_used(client, session):
    """测试按 provider_id 取已存密钥；请求头带对了，响应仍不含明文。

    已存密钥只对保存时的地址有效（C1 外泄面守卫），所以这里把 provider 存成与测试请求同一个
    地址——正是「用已存密钥测自己那台服务」的正常用法。
    """
    with ProviderStub(require_api_key=SECRET) as stub:
        client.put(
            "/api/config/model-service/providers",
            json={
                "providers": [{"name": "甲", "provider_type": "llama_cpp", "id": "default",
                               "base_url": stub.base_url, "model": "qwen2.5", "api_key": SECRET}],
                "active_provider_id": "default", "operator_ref": "U1",
            },
        )
        session.commit()
        resp = client.post(
            "/api/config/model-service/test-connection",
            json={"base_url": stub.base_url, "model": "qwen2.5",
                  "use_saved_key": True, "provider_id": "default"},
        )
        assert stub.requests[-1]["authorized"] is True
    assert resp.json()["ok"] is True
    assert SECRET not in resp.text


def test_saved_key_refused_when_base_url_differs_from_stored(client, session):
    """C1 外泄面守卫：草稿地址被改得与已存地址不同时，用已存密钥测试被 400 拒。"""
    with ProviderStub(require_api_key=SECRET) as stub:
        client.put(
            "/api/config/model-service/providers",
            json={
                "providers": [{"name": "甲", "provider_type": "llama_cpp", "id": "default",
                               "base_url": stub.base_url, "model": "qwen2.5", "api_key": SECRET}],
                "active_provider_id": "default", "operator_ref": "U1",
            },
        )
        session.commit()
        resp = client.post(
            "/api/config/model-service/test-connection",
            json={"base_url": "http://someone-else/v1", "model": "qwen2.5",
                  "use_saved_key": True, "provider_id": "default"},
        )
        # 请求根本没打到桩件（密钥没被带向别处）
        assert all(r["method"] != "GET" for r in stub.requests)
    assert resp.status_code == 400
    assert "重新输入密钥" in resp.text


def test_saved_key_binding_ignores_trailing_slash(client, session):
    """C1：地址一致性判定按归一化比较——仅结尾斜杠之差不算「改了地址」，仍可用已存密钥。"""
    with ProviderStub(require_api_key=SECRET) as stub:
        client.put(
            "/api/config/model-service/providers",
            json={
                "providers": [{"name": "甲", "provider_type": "llama_cpp", "id": "default",
                               "base_url": stub.base_url, "model": "qwen2.5", "api_key": SECRET}],
                "active_provider_id": "default", "operator_ref": "U1",
            },
        )
        session.commit()
        resp = client.post(
            "/api/config/model-service/test-connection",
            json={"base_url": stub.base_url + "/", "model": "qwen2.5",
                  "use_saved_key": True, "provider_id": "default"},
        )
        assert stub.requests[-1]["authorized"] is True
    assert resp.json()["ok"] is True


def test_testing_does_not_change_active_provider(client, session):
    client.put(
        "/api/config/model-service/providers",
        json={
            "providers": [
                {"name": "甲", "provider_type": "llama_cpp", "id": "default",
                 "base_url": "http://a/v1", "model": "m-a"},
                {"name": "乙", "provider_type": "ollama", "id": "second",
                 "base_url": "http://b/v1", "model": "m-b:7b"},
            ],
            "active_provider_id": "second", "operator_ref": "U1",
        },
    )
    session.commit()
    before = client.get("/api/config/model-service/providers").json()
    with ProviderStub() as stub:
        _test_connection(client, base_url=stub.base_url, provider_type="vllm")
    after = client.get("/api/config/model-service/providers").json()
    assert after == before
