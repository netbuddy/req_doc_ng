"""任务派发接口（治理内核 ↔ 智能体运行时）的契约正本。

承载《顶层架构设计》4.1 节时序图中「内核→运行时：派发认知任务」与授权矩阵
「任务状态登记」（运行时允许、限所执行的任务）两支箭头，方向各一个 Protocol：

- RuntimeDispatchFacade：智能体运行时向治理内核露出的门面——内核把已登记的
  任务交给运行时执行（首次派发与退回重派）。
- TaskProgressRegistry：治理内核向智能体运行时露出的任务登记入口——运行时
  回报任务执行进展，界面所见任务状态由此而来（真实回执）。

骨架期两侧同进程，两个 Protocol 都是进程内函数调用，不做序列化；实现方不符合
签名即 mypy 检查失败。派发上下文（材料范围、退回意见）的取材与组装是运行时的
认知侧职责（DR-004），本接口只传内核登记过的事实引用，不传组装好的提示词。

智能体本身没有接口：它不是被调用的服务，而是运行时按派发上下文启动的任务
执行者，行动一律走供给接口门面（api/supply_facade.py）。

数据结构不在本文件定义——正本在 api/schemas/*.yaml，此处只使用生成的类型
（api/models_generated.py，勿手改）。
"""

from typing import Protocol
from uuid import UUID

from api.models_generated import BusinessRejection, FailureItem


class RuntimeDispatchFacade(Protocol):
    """运行时派发门面——治理内核可调用的全部操作（骨架期共两个）。

    共同约定：内核先在任务台账登记任务（状态「已登记」）再调用本门面；
    调用受理即返回，执行异步进行，结果经 TaskProgressRegistry 回报——
    内核不等待、也不感知智能体的执行过程。
    """

    def dispatch_extraction(self, task_id: UUID, material_ids: list[UUID]) -> None:
        """派发一次知识提取任务（首次派发）。

        参数：
            task_id: 内核已登记的任务标识——运行时此后的一切登记与
                智能体经供给接口的一切调用都以它归属。
            material_ids: 输入材料的标识清单，即 startExtraction 受理的
                那份清单；运行时据此组装派发上下文（材料正文由智能体
                运行中经只读工具自取，不在此处传内容）。
        """
        ...

    def dispatch_redo(self, task_id: UUID, asset_id: UUID, opinion: str, material_ids: list[UUID]) -> None:
        """派发一次退回重做任务（治理者退回候选后由内核自动发起）。

        参数：
            task_id: 重做任务的任务标识——退回即登记一个新任务
                （sendBackCandidate 的回执所载），不复用原任务。
            asset_id: 被退回的候选所属资产标识——重做提交沿用该资产，
                不新建（供给接口 submit_candidate 的回执约定）。
            opinion: 治理者的退回意见正文，已在内核留痕；随派发上下文
                原文转给智能体（4.1 节「携意见重新派发」）。
            material_ids: 原任务的输入材料清单，界定重做时的读取范围。
        """
        ...


class TaskProgressRegistry(Protocol):
    """任务进度登记入口——智能体运行时可调用的全部操作（骨架期共三个）。

    授权约束（授权矩阵「任务状态登记」行）：仅运行时身份可调，且只能登记
    自己正在执行的任务；越权登记返回业务拒绝。任务五状态中，本入口只驱动
    「已登记→进行中→待裁决／失败」三步；「待裁决→已完成」由治理状态机在
    末条候选裁决完毕时自行迁移（甲-30），运行时无此入口。
    """

    def register_started(self, task_id: UUID) -> None | BusinessRejection:
        """登记任务开始执行（已登记→进行中）。

        参数：
            task_id: 任务标识。

        返回：
            成功＝无载荷（留痕即回执）；业务拒绝＝「任务不存在」
            「状态不允许该迁移」等，原因码见业务拒绝原因码表。
        """
        ...

    def register_awaiting_decision(
        self, task_id: UUID, failure_items: list[FailureItem]
    ) -> None | BusinessRejection:
        """登记候选已全部提交、任务转待治理者裁决（进行中→待裁决）。

        参数：
            task_id: 任务标识。
            failure_items: 失败项清单——部分输入未能产出候选的逐条原因
                （甲-30 对「部分成功」的处置：不阻碍任务完成，如实可查）；
                全部成功则传空列表。

        返回：
            成功＝无载荷；业务拒绝同 register_started。

        规则：本次登记要求该任务至少已有一条候选提交在案；零候选的
        整体失败走 register_failed，不走本入口。
        """
        ...

    def register_failed(self, task_id: UUID, reason: str) -> None | BusinessRejection:
        """登记任务整体失败（进行中→失败，零候选提交，甲-30）。

        参数：
            task_id: 任务标识。
            reason: 整体失败的原因说明，随任务状态呈现给治理者。

        返回：
            成功＝无载荷；业务拒绝同 register_started。
        """
        ...
