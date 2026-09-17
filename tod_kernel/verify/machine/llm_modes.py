"""验证脚本的机器检查：模型调用件的三种模式（运行、录制、回放）。"""

from __future__ import annotations

import threading
from pathlib import Path

from tod_kernel import llm
from tod_kernel.verify.base import Checker, banner
from tod_kernel.verify.glossary import GLOSSARY_RECORDING


# ───────────────────────── 检查组：模型调用件的三种模式 ─────────────────────────

FAKE_SERVICE_TEXT = "  这是假服务回的固定文字。  "  # 首尾留空白，用来看「去掉首尾空白」是在工具里做的


def fake_service():
    """起一个只回固定文字的本地小服务，替真模型服务受一次请求；返回（服务对象, 端口, 线程）。"""
    import http.server
    import json as json_module

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            body = json_module.dumps({"choices": [{"message": {"content": FAKE_SERVICE_TEXT}}]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # 不往标准错误刷访问日志
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, name="fake-llm-service", daemon=True)
    thread.start()
    return server, server.server_address[1], thread


def closed_port() -> int:
    """找一个此刻没人监听的端口：绑上再放开，拿它的号。运行模式那条检查用它当打不开的地址。"""
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def llm_mode_checks() -> Checker:
    """第四步验证目标二：模型调用件三种模式各走各的路，不联网也能验。

    三个检查都不碰真的模型服务：回放读临时录制文件，录制对着本机起的假服务，运行故意指向一个打不开的端口。
    """
    import shutil
    import tempfile

    title = "第四步：模型调用件的三种模式"
    banner(title)
    c = Checker(title)
    print("── 断言 ──")
    work = Path(tempfile.mkdtemp(prefix="tod-llm-modes-"))
    try:
        recorded = llm.Request(system="你只回一句话。", user="这条请求已经录过。", shape=llm.SHAPE_TEXT)
        fresh = llm.Request(system="你只回一句话。", user="这条请求没有录过。", shape=llm.SHAPE_TEXT)
        recorded_text = "这是录制文件里的回答原文。"
        recording = work / "recording.json"
        llm.save_to_recording(recording, recorded, recorded_text)
        entries = llm.read_recording(recording)
        c.check("录制文件是一个列表，每项有请求哈希、请求原文（系统提示、用户内容、输出形状）与回答原文四样",
                len(entries) == 1 and set(entries[0]) == {"request_hash", "request", "response"}
                and set(entries[0]["request"]) == {"system", "user", "shape"}, entries)

        base = {"mode": llm.MODE_REPLAY, "base_url": "http://127.0.0.1:1/v1", "model": "假模型",
                "timeout_s": 5, "recording_path": str(recording), "print_calls": False}
        reply = llm.make_caller(base)(recorded)
        c.check("回放模式：请求哈希命中时返回录制的回答原文，记录里模式是「回放」、请求哈希是这条请求的",
                reply.text == recorded_text and reply.record["mode"] == llm.MODE_REPLAY
                and reply.record["request_hash"] == llm.request_hash(recorded), reply)
        error = None
        try:
            llm.make_caller(base)(fresh)
        except llm.LLMError as exc:
            error = exc
        c.check("回放模式：请求哈希不命中时抛出模型调用错误，一句话说明是「录制文件里没有这条请求」，"
                "错误全文附上请求原文，好让人照着补录或改提示词",
                error is not None and "录制文件里没有这条请求" in error.brief
                and fresh.user in str(error) and fresh.system in str(error), repr(error))

        server, port, thread = fake_service()
        try:
            second = work / "recording2.json"
            record_config = {**base, "mode": llm.MODE_RECORD, "base_url": f"http://127.0.0.1:{port}/v1",
                             "recording_path": str(second)}
            reply = llm.make_caller(record_config)(fresh)
            saved = llm.read_recording(second)
            c.check("录制模式：对着假服务调一次，回答是它回的固定文字，记录里模式是「录制」",
                    reply.text == FAKE_SERVICE_TEXT and reply.record["mode"] == llm.MODE_RECORD, reply)
            c.check("录制模式：录制文件里多出这一条，请求哈希、请求原文与回答原文都在",
                    len(saved) == 1 and saved[0]["request_hash"] == llm.request_hash(fresh)
                    and saved[0]["request"] == {"system": fresh.system, "user": fresh.user, "shape": fresh.shape}
                    and saved[0]["response"] == FAKE_SERVICE_TEXT, saved)
            replayed = llm.make_caller({**base, "recording_path": str(second)})(fresh)
            c.check("刚录下的这一条，换回放模式再调同一个请求就能命中，回答逐字相同",
                    replayed.text == FAKE_SERVICE_TEXT and replayed.record["mode"] == llm.MODE_REPLAY, replayed)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)

        port = closed_port()
        url = f"http://127.0.0.1:{port}/v1"
        error = None
        try:
            llm.make_caller({**base, "mode": llm.MODE_RUN, "base_url": url})(fresh)
        except llm.LLMError as exc:
            error = exc
        c.check(f"运行模式：服务地址指向打不开的端口时，抛出的错误里带着那个地址 {url}/chat/completions",
                error is not None and "连不上模型服务" in error.brief and f"{url}/chat/completions" in error.brief,
                repr(error))

        error = None
        try:
            llm.make_caller(base)(llm.Request(system="x", user="y", shape="表格"))
        except llm.LLMError as exc:
            error = exc
        c.check("输出形状只认「文本」与「JSON」，给别的形状就报错",
                error is not None and "输出形状" in str(error), repr(error))

        c.check("入库的示例配置六项齐全，模式是「回放」，录制文件指向术语澄清那一份",
                llm.load_config(llm.PACKAGE_DIR / llm.EXAMPLE_CONFIG_NAME)["mode"] == llm.MODE_REPLAY
                and llm.load_config(llm.PACKAGE_DIR / llm.EXAMPLE_CONFIG_NAME)["recording_path"] == GLOSSARY_RECORDING,
                llm.load_config(llm.PACKAGE_DIR / llm.EXAMPLE_CONFIG_NAME))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return c
