"""结构化日志(harness-engineering/logging)。

一行一 JSON 事件,最小字段 ts/level/component/event,可选 body(run_id/ok/error_code…)。
硬规则:body 只放摘要/计数/布尔/稳定码,绝不写密钥、原始 prompt、模型响应、用户原文。
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from app.diagnostics import buffer as _diagnostics_buffer

_LEVELS = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARN": logging.WARNING, "ERROR": logging.ERROR}

_logger = logging.getLogger("reqdoc")
_configured = False


def _configure() -> None:
    global _configured
    if _configured:
        return
    if not _logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        _logger.addHandler(handler)
    _logger.setLevel(logging.INFO)
    _logger.propagate = False
    _configured = True


def log_event(component: str, event: str, msg: str = "", level: str = "INFO", **fields: Any) -> None:
    """发射一条稳定结构化日志。None 字段自动省略;调用方负责不传敏感原文。"""
    _configure()
    record: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "component": component,
        "event": event,
    }
    if msg:
        record["msg"] = msg
    record.update({k: v for k, v in fields.items() if v is not None})
    _logger.log(_LEVELS.get(level, logging.INFO), json.dumps(record, ensure_ascii=False))
    # 运行态诊断投影(04A §2.1):WARN/ERROR 按事件码计数,只喂白名单字段,不喂 msg/body。
    try:
        _diagnostics_buffer.record(component, event, level)
    except Exception:  # noqa: BLE001 诊断投影绝不影响日志主路径
        pass
