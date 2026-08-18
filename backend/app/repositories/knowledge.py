"""V2 知识层最小读写（第一步：资产＋快照两表；设计正本＝docs/v2/design/数据模型.md）。

职责边界：本模块只做「建户口、追加快照」两件事与其不变式校验（类别一致、作者身份
与凭据配套、指纹计算）。完整的内容结构校验（api/schemas/knowledge.yaml）不在这里——
那属于将来「提交候选」入口（供给接口）的门禁；数据库与本模块只守住底线。
"""
from __future__ import annotations

import hashlib
import json
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import KnowledgeAsset, KnowledgeSnapshot
from app.domain.errors import InvalidInput

# 指纹算法标识：sha256 作用于 JCS 风格的规范化序列化（键排序、紧凑分隔、UTF-8 原文）。
# 说明：完整的 RFC 8785 还规定了浮点数的专门写法；当前内容规范里数值仅出现在字符串
# 属性值中，如将来内容里出现真正的浮点数字段，跨语言复现需补齐该项并升级本标识。
CONTENT_HASH_ALG = "sha256/jcs"


def canonical_content_hash(content: dict) -> str:
    """对内容做规范化序列化后计算 SHA-256——同样的内容在任何端算出同样的指纹。"""
    canonical = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create_asset(session: Session, project_id: uuid.UUID, kind: str) -> KnowledgeAsset:
    """建户口：登记一条知识的身份。状态从「待确认」起步，内容经 submit_snapshot 追加。"""
    asset = KnowledgeAsset(project_id=project_id, kind=kind)
    session.add(asset)
    session.flush()
    return asset


def submit_snapshot(
    session: Session,
    asset: KnowledgeAsset,
    content: dict,
    *,
    author_kind: str,
    task_ref: uuid.UUID | None = None,
    audit_ref: uuid.UUID | None = None,
) -> KnowledgeSnapshot:
    """追加一版快照。三条不变式在此守住：

    ①类别一致——内容判别字段（kind）以户口为准，不一致拒绝；
    ②作者与凭据配套——智能体必须带产生任务（task_ref），治理者必须带留痕（audit_ref）；
    ③序号递增——取当前最大序号＋1；若两笔提交同时挤进来，唯一约束保证只有一笔成功
    （修改本身独占，此处只是防重放的保险）。
    """
    if content.get("kind") != asset.kind:
        raise InvalidInput(f"内容类别（{content.get('kind')}）与资产户口（{asset.kind}）不一致")
    if author_kind == "智能体" and task_ref is None:
        raise InvalidInput("智能体提交必须带产生任务标识（task_ref）")
    if author_kind == "治理者" and audit_ref is None:
        raise InvalidInput("治理者提交必须带操作留痕标识（audit_ref）")

    next_seq = (
        session.scalar(
            select(func.coalesce(func.max(KnowledgeSnapshot.seq_no), 0)).where(
                KnowledgeSnapshot.asset_id == asset.id
            )
        )
        or 0
    ) + 1
    snapshot = KnowledgeSnapshot(
        asset_id=asset.id,
        seq_no=next_seq,
        content=content,
        content_sha256=canonical_content_hash(content),
        content_hash_alg=CONTENT_HASH_ALG,
        author_kind=author_kind,
        task_ref=task_ref,
        audit_ref=audit_ref,
    )
    session.add(snapshot)
    session.flush()
    return snapshot
