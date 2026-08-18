"""诊断事件环形缓冲（运行态面板 / 诊断中心的只读投影,04A §2.1）。

log_event 发射 WARN/ERROR 时同步喂入,按事件码聚合为 级别/首次出现/最近出现/次数。
不是日志存储:stdout 结构化日志仍是唯一事实源,这里只做白名单摘要,进程重启即清空。

边界:缓冲是进程内的——worker 进程的事件进不了 API 进程缓冲;worker 侧失败的
运行影响经 agent_run 表聚合可见(services/runtime_status.py),不依赖本缓冲。

铁律(AGENTS.md 规则 8):只存 事件码/级别/时间/计数,绝不存 msg 原文或 body 字段。
"""
from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock

_CAPTURE_LEVELS = frozenset({"WARN", "ERROR"})
_MAX_ENTRIES = 100


class DiagnosticsBuffer:
    """按事件码聚合的线程安全环形缓冲;超容量时淘汰最久未出现的事件码。"""

    def __init__(self, max_entries: int = _MAX_ENTRIES) -> None:
        self._lock = Lock()
        self._max = max_entries
        self._entries: dict[str, dict] = {}

    def record(self, component: str, event: str, level: str) -> None:
        if level not in _CAPTURE_LEVELS:
            return
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            entry = self._entries.get(event)
            if entry is None:
                if len(self._entries) >= self._max:
                    oldest = min(self._entries, key=lambda k: self._entries[k]["last_seen"])
                    del self._entries[oldest]
                self._entries[event] = {
                    "event": event,
                    "component": component,
                    "level": level,
                    "first_seen": now,
                    "last_seen": now,
                    "count": 1,
                }
            else:
                entry["level"] = level
                entry["last_seen"] = now
                entry["count"] += 1

    def snapshot(self, limit: int = 50) -> list[dict]:
        """最近出现优先的白名单摘要列表(拷贝,调用方可安全序列化)。"""
        with self._lock:
            entries = sorted(self._entries.values(), key=lambda e: e["last_seen"], reverse=True)
            return [dict(e) for e in entries[:limit]]

    def reset(self) -> None:
        """仅供测试。"""
        with self._lock:
            self._entries.clear()


buffer = DiagnosticsBuffer()
