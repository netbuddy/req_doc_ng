"""对话端点 SSE 流式回执（AEP-095/096 流式变体）：帧序、终帧同构、错误降级。

设计事实源：docs/40 slices/SCN-001-P02/前端交互与接口.md §5.1 SSE 变体。
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.api.sse_dialogue import stream_dialogue, wants_event_stream
from app.domain.errors import RejectedTransition


class _Dummy(BaseModel):
    outcome: str = "executed"
    message: str | None = "完成"


def _mini_app(run) -> TestClient:
    app = FastAPI()

    @app.post("/x")
    def x():
        return stream_dialogue(run)

    return TestClient(app)


def _frames(text: str) -> list[tuple[str, str]]:
    frames = []
    for block in text.strip().split("\n\n"):
        event = data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[len("event: "):]
            elif line.startswith("data: "):
                data = line[len("data: "):]
        if event and data is not None:
            frames.append((event, data))
    return frames


def test_stage_frames_then_result_frame():
    def run(on_stage):
        on_stage("accepted")
        on_stage("interpreting")
        on_stage("dispatching")
        return _Dummy()

    client = _mini_app(run)
    resp = client.post("/x")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    frames = _frames(resp.text)
    assert [e for e, _ in frames] == ["stage", "stage", "stage", "result"]
    assert '"stage": "accepted"' in frames[0][1] and '"at"' in frames[0][1]
    assert '"outcome":"executed"' in frames[3][1].replace(" ", "")


def test_domain_error_becomes_error_frame_not_http_error():
    def run(on_stage):
        on_stage("accepted")
        raise RejectedTransition("工作区已更新（版本不一致），请刷新后重试")

    resp = _mini_app(run).post("/x")
    assert resp.status_code == 200  # 流内已 200，错误只能降级为帧
    frames = _frames(resp.text)
    assert frames[0][0] == "stage"
    assert frames[-1][0] == "error"
    assert "版本不一致" in frames[-1][1]


def test_unexpected_error_degrades_to_generic_error_frame():
    def run(on_stage):
        raise RuntimeError("boom with secrets")

    resp = _mini_app(run).post("/x")
    frames = _frames(resp.text)
    assert frames[-1][0] == "error"
    assert "boom" not in frames[-1][1]  # 意外错误不外泄内部信息
    assert "服务内部错误" in frames[-1][1]


def test_wants_event_stream_header_detection():
    class _Req:
        def __init__(self, accept):
            self.headers = {"accept": accept}

    assert wants_event_stream(_Req("text/event-stream"))
    assert wants_event_stream(_Req("application/json, text/event-stream"))
    assert not wants_event_stream(_Req("application/json"))
    assert not wants_event_stream(_Req(""))
