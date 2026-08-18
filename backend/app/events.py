"""进程内领域事件骨架（阶段策略解耦 P1）。

设计取向（提案 docs/proposals/stage-policy-decoupling/README.md §4）：
共享对象层只报告事实、不裁决后果——修订写入后发布领域事件，由各阶段应用层
在自己拥有的动作上决定后果。**不引入消息中间件**：发布同步、进程内、逐个调用订阅者。

P1 只落 `ItemRevised` 与发布器骨架：链式复诊此期由评审服务在采纳动作内显式续接
（不经订阅者），事件本身尚无功能订阅者，为 P2（结构体检链迁回形成动作）与
P3（触发矩阵）预留发布缝。新增事件类型与订阅者按此文件扩展。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List


@dataclass(frozen=True)
class ItemRevised:
    """需求条目内容修订已落库这一事实。

    origin：修订发起来源，供订阅者按阶段语境分流（如评审采纳链只认 `review_adoption`）。
    取值约定：`review_adoption`（评审裁决采纳 revise 结论）／`direct`（AEP-036 直发、
    形成页与评审页对话的人工修订等非采纳路径的缺省值）。
    """

    item_ref: str
    revision_ref: str
    origin: str


DomainEvent = ItemRevised
EventHandler = Callable[[DomainEvent], None]


class DomainEventPublisher:
    """同步进程内领域事件发布器：注册订阅者，发布时逐个调用。

    未注册任何订阅者时 publish 为空操作（对象层无条件发布，是否有消费者由装配层决定）。
    订阅者异常不被吞没——发布器不做隔离，装配层须保证订阅者自身健壮（P1 无订阅者，
    该约束随 P2 首个真实订阅者一并明确）。
    """

    def __init__(self) -> None:
        self._handlers: List[EventHandler] = []

    def subscribe(self, handler: EventHandler) -> None:
        self._handlers.append(handler)

    def publish(self, event: DomainEvent) -> None:
        for handler in self._handlers:
            handler(event)
