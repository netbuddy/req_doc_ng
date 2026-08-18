"""智能体供给接口（边界②）的契约正本。

这是治理内核向智能体层露出的唯一入口（供给接口门面）：智能体读取输入、提交产出
都只经这里，不得触碰存储访问模块与治理内核的内部模块——该纪律由构建期的依赖方向
静态检查强制（选型裁定记录决定点六）。骨架期两层同进程，本接口就是进程内的函数调用，
不做序列化。

实现方（治理内核侧的门面模块）必须完整实现本 Protocol；mypy 静态检查不通过即违约。
数据结构不在本文件定义——正本在 api/schemas/*.yaml，此处只使用生成的类型
（api/models_generated.py，勿手改）。

派发上下文（本次任务要处理哪些材料、治理者退回意见等）由智能体运行时随任务
启动时传给智能体，不经本接口；本接口的每个调用都以任务标识为第一参数，
供留痕与范围记录归属到具体任务。
"""

from typing import Protocol
from uuid import UUID

from api.models_generated import (
    AnchorInput,
    BusinessRejection,
    CandidateSubmissionReceipt,
    KnowledgeContent,
    MaterialParsedText,
)


class SupplyFacade(Protocol):
    """供给接口门面——智能体层可调用的全部操作（骨架期共两个）。"""

    def read_material(self, task_id: UUID, material_id: UUID) -> MaterialParsedText | BusinessRejection:
        """读取一份材料的解析文本（只读工具）。

        参数：
            task_id: 本次认知任务的任务标识——每次读取都以它归属留痕；
                平台记录「该任务读取了哪些材料范围」，锚定查找与追溯复核
                共用这份记录（共享结构裁定记录决定点二的连带要求，属主
                任务台账——冻结包裁定甲-18）。
            material_id: 要读取的材料标识，来自派发上下文所列的输入材料。

        返回：
            成功＝材料解析文本（含解析结果标识与规范化全文——锚点基准，
            提交候选时的引文必须是这份全文的精确子串）；
            业务拒绝＝「材料不存在或不可读」等，原因码见业务拒绝原因码表。
        """
        ...

    def submit_candidate(
        self,
        task_id: UUID,
        content: KnowledgeContent,
        anchors: list[AnchorInput],
    ) -> CandidateSubmissionReceipt | BusinessRejection:
        """提交一条候选知识——内容型产出的唯一入口，只能落为「待确认」（R-027）。

        参数：
            task_id: 本次认知任务的任务标识——候选稿快照记录产生它的任务，
                运行记录与内容由此双向可查（真实回执的根基）。
            content: 知识单元内容——需求知识或领域概念（结构见
                api/schemas/knowledge.yaml；歧义属性按 R-007 用候选解释
                列表形态提交，不得自行择一）。
            anchors: 来源锚定列表，每条＝材料标识加引文（智能体只抄引文、
                不计偏移；内核对每条引文做硬校验后自行查找计算偏移与命中数）。
                知识单元与锚点一对多，至少一条（R-006 锚定随提取建立）。

        返回：
            成功＝候选提交回执（资产标识加本次固化的候选稿快照标识）；
            业务拒绝＝「引文非原文子串」（任一锚点校验不过，整次提交不落库）等。

        规则：提交即固化一份不可变候选稿快照，作者身份记「智能体」；
        锚定与派生关系随写入自动记录（顶层设计 4.1）；不存在
        「写入已确认」的通路（授权矩阵不变条件一）。
        """
        ...
