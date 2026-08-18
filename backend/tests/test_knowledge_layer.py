"""V2 知识层第一步：资产＋快照两表的机制验证。

验证五件事（知识层落库对齐稿·第一步）：能建户口、能连提两版快照、旧快照改不动、
重复抢占同一序号只有一笔成功、同样内容指纹一致（跨端可复现的规范化计算）。
"""
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

from app.db.base import Base, make_session_factory
from app.db.models import KnowledgeAsset, KnowledgeSnapshot
from app.domain.errors import InvalidInput, RejectedTransition
from app.repositories.knowledge import (
    CONTENT_HASH_ALG,
    canonical_content_hash,
    create_asset,
    submit_snapshot,
)

PROJECT = uuid.uuid4()
TASK = uuid.uuid4()
AUDIT = uuid.uuid4()


def _content(title: str = "系统应支持导出订单报表") -> dict:
    return {
        "kind": "需求知识",
        "title": title,
        "description": f"{title}——供运营人员每日核对。",
        "category": "功能需求",
    }


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    s = make_session_factory(engine)()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def test_create_asset_and_two_snapshots(session):
    asset = create_asset(session, PROJECT, "需求知识")
    assert asset.status == "待确认" and asset.id.version == 7  # UUID v7

    first = submit_snapshot(session, asset, _content(), author_kind="智能体", task_ref=TASK)
    second = submit_snapshot(
        session, asset, _content("系统应支持导出订单报表（含退款单）"),
        author_kind="治理者", audit_ref=AUDIT,
    )
    assert (first.seq_no, second.seq_no) == (1, 2)
    assert first.content_hash_alg == CONTENT_HASH_ALG
    session.commit()

    rows = session.query(KnowledgeSnapshot).filter_by(asset_id=asset.id).order_by(
        KnowledgeSnapshot.seq_no
    ).all()
    assert [r.seq_no for r in rows] == [1, 2]  # 旧版本仍在，只追加不覆盖


def test_snapshot_is_immutable(session):
    asset = create_asset(session, PROJECT, "需求知识")
    snap = submit_snapshot(session, asset, _content(), author_kind="智能体", task_ref=TASK)
    session.commit()

    snap.content_sha256 = "篡改"
    with pytest.raises(RejectedTransition):
        session.flush()
    session.rollback()


def test_duplicate_seq_only_one_wins(session):
    """重复提交（如网络重试）同时抢到同一序号：唯一约束保证只有一笔成功。"""
    asset = create_asset(session, PROJECT, "需求知识")
    submit_snapshot(session, asset, _content(), author_kind="智能体", task_ref=TASK)
    session.commit()

    dup = KnowledgeSnapshot(
        asset_id=asset.id, seq_no=1, content=_content(),
        content_sha256=canonical_content_hash(_content()), content_hash_alg=CONTENT_HASH_ALG,
        author_kind="智能体", task_ref=TASK,
    )
    session.add(dup)
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_kind_must_match_asset(session):
    asset = create_asset(session, PROJECT, "领域概念")
    with pytest.raises(InvalidInput):
        submit_snapshot(session, asset, _content(), author_kind="智能体", task_ref=TASK)


def test_author_credential_pairing(session):
    asset = create_asset(session, PROJECT, "需求知识")
    with pytest.raises(InvalidInput):  # 智能体缺产生任务
        submit_snapshot(session, asset, _content(), author_kind="智能体")
    with pytest.raises(InvalidInput):  # 治理者缺留痕
        submit_snapshot(session, asset, _content(), author_kind="治理者")


def test_hash_reproducible_regardless_of_key_order():
    a = {"kind": "需求知识", "title": "甲", "description": "乙", "category": "其他"}
    b = {"category": "其他", "description": "乙", "title": "甲", "kind": "需求知识"}
    assert canonical_content_hash(a) == canonical_content_hash(b)  # 键序不同，指纹相同
    assert canonical_content_hash(a) != canonical_content_hash({**a, "title": "丙"})
