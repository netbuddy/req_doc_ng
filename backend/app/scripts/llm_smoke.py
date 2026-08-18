"""在你的局域网里对真实 Qwen 打一次来源接入判断（验证 A1 的真 LLM 通路）。

用法（在能连到 llama.cpp 的机器上）：
  LLM_BASE_URL=http://192.168.1.50:8080/v1 \\
  .venv/bin/python -m app.scripts.llm_smoke "系统应支持导出 docx 文档。"

未设 LLM_BASE_URL 时用 stub（不联网），仅验证脚本本身。
"""
from __future__ import annotations

import sys

from app.adapters.llm import build_source_intake_judge
from app.config import settings


def main() -> None:
    text = sys.argv[1] if len(sys.argv) > 1 else "系统应支持将确认后的需求导出为 docx 文档。"
    print(f"LLM_BASE_URL = {settings.llm_base_url or '(未设，用 stub)'}")
    print(f"model        = {settings.llm_model}")
    print(f"提交文本      = {text}")
    print("-" * 40)
    result = build_source_intake_judge(settings).judge("demo-project", text, "冒烟测试")
    print(f"判定 judgement = {result.judgement.value}")
    print(f"依据 basis     = {result.basis}")


if __name__ == "__main__":
    main()
