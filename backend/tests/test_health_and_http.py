"""HTTP 层：health 形状 + intake 端到端 + 业务结局 200 + 404（前端契约适配 §1/§3）。"""
from fastapi.testclient import TestClient

from app.deps import get_service
from app.main import app
from app.repositories.in_memory import build_wiring

# HTTP 测试用 in-memory 覆盖 DB 依赖（不触真 Postgres，项目用 PRJ-DEMO 字符串）。
_test_wiring = build_wiring(auto_complete=True, selected_projects={"PRJ-DEMO"})
app.dependency_overrides[get_service] = lambda: _test_wiring.service

client = TestClient(app)


def test_health_shape_matches_frontend():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["checks"]["app"] == "ok"
    assert body["ready"] is True
    assert body["service"] and body["version"] and body["environment"]


def test_intake_end_to_end_accepted():
    # auto-complete stub（代 RQ+LLM）已把判定=可接入完成。
    # 2026-08-08 路线 A：三拍制保留，应答为 V2 信封；项目标识只走路径。
    payload = {
        "text": "系统应支持导出 docx。",
        "source_note": "访谈纪要",
        "operator_ref": "U1",
        "idempotency_key": "K-http-accept",
    }
    r = client.post("/api/projects/PRJ-DEMO/intake", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["result"] == "成功"
    ctx = body["data"]["context_ref"]
    assert ctx

    r2 = client.get(f"/api/projects/PRJ-DEMO/intake/{ctx}")
    assert r2.status_code == 200
    envelope = r2.json()
    assert envelope["result"] == "成功"
    res = envelope["data"]
    assert res["intake_conclusion"] == "accepted"
    assert res["material_ref"]
    assert any(a["key"] == "start_recognition" and a["enabled"] for a in res["available_actions"])


def test_intake_idempotent_replay_returns_same_context():
    payload = {
        "text": "同一幂等键重放。",
        "operator_ref": "U1",
        "idempotency_key": "K-http-replay",
    }
    first = client.post("/api/projects/PRJ-DEMO/intake", json=payload).json()
    replay = client.post("/api/projects/PRJ-DEMO/intake", json=payload).json()
    assert first["result"] == replay["result"] == "成功"
    assert replay["data"]["context_ref"] == first["data"]["context_ref"]


def test_precheck_returns_business_rejection_envelope():
    # 前检不过＝业务拒绝信封（200），不再是混在数据里的 rejected_precheck 状态。
    r = client.post("/api/projects/PRJ-UNKNOWN/intake", json={
        "text": "x", "operator_ref": "U1", "idempotency_key": "K-http-precheck",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["result"] == "业务拒绝"
    assert body["rejection"]["reason_code"] == "项目未选定"
    assert body["rejection"]["message"]

    r2 = client.post("/api/projects/PRJ-DEMO/intake", json={
        "text": "   ", "operator_ref": "U1", "idempotency_key": "K-http-empty",
    })
    assert r2.status_code == 200
    assert r2.json()["rejection"]["reason_code"] == "材料正文为空"


def test_result_query_unknown_context_returns_404():
    r = client.get("/api/projects/PRJ-DEMO/intake/CTX-NONEXISTENT")
    assert r.status_code == 404
    assert r.json()["success"] is False
