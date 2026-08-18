"""领域错误。业务结局用返回值表达；仅契约违规用异常。"""
from __future__ import annotations


class DomainError(Exception):
    """领域层错误基类。"""


class RejectedTransition(DomainError):
    """未列出的 (状态, 事件) 组合默认拒绝（状态机规则）→ HTTP 409。"""


class NotFound(DomainError):
    """引用对象不存在 → HTTP 404。"""


class InvalidInput(DomainError):
    """输入不合法（如项目名为空）→ HTTP 400。"""
