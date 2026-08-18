"""P3 预留接口（AEP-106/107/108）：三端点返回 deferred 占位，不查库、不造假。

覆盖 05 篇 AC-P3-01。
"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
_PID = "00000000-0000-0000-0000-000000000000"


def test_trace_coverage_deferred():
    r = client.get(f"/api/projects/{_PID}/workbench/trace-coverage")
    assert r.status_code == 200
    body = r.json()
    assert body["deferred"] is True and body["items"] == [] and body["note"]


def test_ai_copilot_deferred():
    r = client.get(f"/api/projects/{_PID}/workbench/ai-copilot", params={"item_ref": "x"})
    assert r.status_code == 200
    body = r.json()
    assert body["deferred"] is True and body["items"] == [] and body["note"]


def test_change_impact_deferred():
    r = client.get(f"/api/projects/{_PID}/workbench/change-impact", params={"item_ref": "x"})
    assert r.status_code == 200
    body = r.json()
    assert body["deferred"] is True and body["items"] == [] and body["note"]
