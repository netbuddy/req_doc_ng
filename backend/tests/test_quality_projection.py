"""P2 质量持久化与投影：quality_meta 组装 / drift 派生 / quality_alert_summary 聚合。

覆盖 05 篇 AC-P2-03（zip 对齐）/AC-P2-05（只算已诊断）/测试义务 test_quality_projection。
"""
import json
from datetime import datetime, timedelta, timezone
import uuid

import pytest

import app.db.models  # noqa: F401  register tables
from app.db.base import Base, make_engine, make_session_factory
from app.db.models import ItemDiagnosisRound, Project
from app.repositories.asset_read import AssetReadRepository
from app.services.item_review import _build_quality_meta, _drift_tokens


# ---- 纯函数：quality_meta 组装（降级不拒收，全空 → None） ----

def test_build_quality_meta_full():
    verdict = {
        "verdict_kind": "revise", "verdict_summary": "x",
        "findings": [{"finding_type": "untestable", "rule_code": "INCOSE-R7",
                      "evidence_span": "尽快", "severity": "medium", "dimension": "verifiable"}],
        "quality_profile": {"overall": 72, "dimensions": []},
        "ears_rewrite": {"pattern_type": "event_driven", "lines": ["WHEN ..."]},
        "source_alignments": [{"element_ref": "E1", "alignment": 0.64, "note": "偏离"}],
    }
    meta = json.loads(_build_quality_meta(verdict))
    assert meta["quality_profile"]["overall"] == 72
    assert meta["findings"][0]["rule_code"] == "INCOSE-R7"
    assert meta["source_alignments"][0]["alignment"] == 0.64


def test_build_quality_meta_empty_returns_none():
    verdict = {
        "findings": [{"finding_type": "no_blocker", "diagnosis_summary": "ok"}],
        "quality_profile": None, "ears_rewrite": None, "source_alignments": None,
    }
    assert _build_quality_meta(verdict) is None  # 无任何质量字段 → 不落库


# ---- 纯函数：来源偏离（多位数阈值新颖性；单位数不计） ----

def test_drift_tokens_threshold():
    assert _drift_tokens("金额 ≥ 500 元，5 秒内处理", "阈值建议 800 元") == ["500"]
    assert _drift_tokens("响应不超过 5 秒", "任意来源") == []  # 单位数不计入偏离
    assert _drift_tokens("与来源一致 800 元", "阈值 800 元") == []


# ---- DB：quality_alert_summary 只统计已诊断条目，按严重度计数 ----

@pytest.fixture()
def session():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = make_session_factory(engine)()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def _round(pid, item, no, verdict, quality_meta):
    return ItemDiagnosisRound(
        project_id=pid, item_ref=item, batch_ref=uuid.uuid4(), round_no=no,
        diagnosis_mode="standard", processing_status="completed",
        verdict_kind=verdict, verdict_summary="s", quality_meta=quality_meta,
    )


def test_quality_alert_summary_counts_only_diagnosed(session):
    """统一口径（与质量端点/维护列表同源）：LDM-009 发现项 × quality_meta 按序 zip 计严重度。"""
    from app.db.models import ItemReviewFinding

    p = Project(name="q")
    session.add(p)
    session.flush()
    item_a, item_b, item_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    meta = json.dumps({"findings": [
        {"finding_type": "ambiguous_expression", "severity": "high"},
        {"finding_type": "untestable", "severity": "medium"},
        {"finding_type": "no_blocker", "severity": "medium"},  # no_blocker 不计
    ]})

    def add_findings(round_, types):
        session.flush()
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for i, t in enumerate(types):
            # created_at 显式递增：zip 口径按 (created_at, id) 排序，同事务写入需保证确定序
            session.add(ItemReviewFinding(
                round_ref=round_.id, item_ref=round_.item_ref, finding_type=t,
                diagnosis_summary="s", suggested_disposition="revise",
                created_at=base + timedelta(seconds=i),
            ))

    # item_a: 两轮，最新一轮（round_no=2）计数；旧轮忽略
    r1 = _round(p.id, item_a, 1, "revise", json.dumps({"findings": [{"finding_type": "x", "severity": "low"}]}))
    session.add(r1)
    add_findings(r1, ["x"])
    r2 = _round(p.id, item_a, 2, "revise", meta)
    session.add(r2)
    add_findings(r2, ["ambiguous_expression", "untestable", "no_blocker"])
    # item_b: 已诊断但无发现项 → 计入 diagnosed，不计告警
    session.add(_round(p.id, item_b, 1, "pass", None))
    # item_c: 未诊断（无 verdict）→ 完全不计
    session.add(_round(p.id, item_c, 1, None, None))
    session.flush()

    s = AssetReadRepository(session).quality_alert_summary(str(p.id))
    assert s == {"high": 1, "medium": 1, "low": 0, "diagnosed_items": 2}


# ---- 发现项与质量元数据的配对：按引用，不按下标（REQ-101 走查报障的错位）----
#
# 根因：一轮诊断的多条发现项在同一事务同一循环里连续插入，created_at 取数据库 now()
# 而同事务内该函数返回同一值，故这批行时间戳完全相同；读侧按 (created_at, id) 排序，
# 时间并列即退化为随机 UUID 序，与写入序无关。而规则编号等四样存在轮次 quality_meta 里
# 按模型输出序排列，读侧若按下标拉链就会张冠李戴。
#
# 注意本文件上方 test_quality_alert_summary_counts_only_diagnosed 的辅助函数主动给每条
# 发现项手工递增了 created_at——那是在规避根因场景，故此前没有测试网住这个缺陷。
# 下面的用例反其道而行：故意把读出序做成与写入序相反，配对仍须正确。

def _write_findings_with_reversed_read_order(session, round_, entries):
    """按 entries 顺序写入发现项，再把 created_at 倒排，使读出序恰为写入序的逆序。

    返回写入序的发现项引用列表（＝模型输出序）。
    """
    from datetime import datetime, timedelta, timezone

    from app.db.models import ItemReviewFinding
    from app.repositories.sqlalchemy import SqlItemReviewRepository

    repo = SqlItemReviewRepository(session)
    refs = [
        repo.add_finding(
            str(round_.id), str(round_.item_ref), e["finding_type"],
            e["diagnosis_summary"], "", "none", None, None, None, None, None,
        )
        for e in entries
    ]
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i, ref in enumerate(refs):
        row = session.get(ItemReviewFinding, uuid.UUID(ref))
        row.created_at = base + timedelta(seconds=len(refs) - i)  # 倒排
    session.flush()
    return refs


def _verdict_two_findings():
    return {
        "verdict_kind": "revise", "verdict_summary": "s",
        "findings": [
            {"finding_type": "ambiguous_expression", "diagnosis_summary": "第一条：表达含糊",
             "rule_code": "ISO-29148-A", "severity": "high", "dimension": "unambiguous",
             "evidence_span": "尽快"},
            {"finding_type": "untestable", "diagnosis_summary": "第二条：无法验证",
             "rule_code": "INCOSE-R7", "severity": "low", "dimension": "verifiable",
             "evidence_span": "友好"},
        ],
        "revision_points": [
            {"point_ref": "P2", "label": "改可验证", "finding_index": 1,
             "find": "友好", "replace": "响应不超过 2 秒"},
        ],
    }


def _project(session, round_):
    """经真实读仓储取轮次行（含真实排序），再走服务投影——不绕过被测的那段排序。"""
    from app.repositories.sqlalchemy import SqlItemReviewRepository, build_sql_item_review_service

    session.flush()
    row = SqlItemReviewRepository(session).latest_round_of_item(str(round_.item_ref))
    return build_sql_item_review_service(session)._project_round(row, effective=True)


def test_finding_metadata_pairs_by_reference_not_by_position(session):
    """读出序与写入序相反时，每条发现项仍拿到属于自己的规则编号与严重度。"""
    p = Project(name="q")
    session.add(p)
    session.flush()
    verdict = _verdict_two_findings()
    round_ = _round(p.id, uuid.uuid4(), 1, "revise", None)
    session.add(round_)
    session.flush()
    refs = _write_findings_with_reversed_read_order(session, round_, verdict["findings"])
    round_.quality_meta = _build_quality_meta(verdict, refs)
    session.flush()

    view = _project(session, round_)

    # 读出序确已与写入序相反——否则这个用例证明不了任何事。
    assert [f.diagnosis_summary for f in view.findings] == ["第二条：无法验证", "第一条：表达含糊"]
    by_summary = {f.diagnosis_summary: f for f in view.findings}
    first, second = by_summary["第一条：表达含糊"], by_summary["第二条：无法验证"]
    assert (first.rule_code, first.severity, first.evidence_span) == ("ISO-29148-A", "high", "尽快")
    assert (second.rule_code, second.severity, second.evidence_span) == ("INCOSE-R7", "low", "友好")


def test_revision_point_carries_finding_reference(session):
    """修订点带上它所针对的发现项引用——一键修复按钮据此挂到正确的行。"""
    p = Project(name="q")
    session.add(p)
    session.flush()
    verdict = _verdict_two_findings()
    round_ = _round(p.id, uuid.uuid4(), 1, "revise", None)
    round_.revision_points = json.dumps(verdict["revision_points"], ensure_ascii=False)
    session.add(round_)
    session.flush()
    refs = _write_findings_with_reversed_read_order(session, round_, verdict["findings"])
    round_.quality_meta = _build_quality_meta(verdict, refs)
    session.flush()

    view = _project(session, round_)
    point = view.revision_points[0]
    target = next(f for f in view.findings if f.finding_ref == point.finding_ref)
    # finding_index=1 指的是模型输出的第二条；引用必须指向它，而不是读出序的第二条。
    assert target.diagnosis_summary == "第二条：无法验证"
    assert view.findings[1].diagnosis_summary == "第一条：表达含糊"  # 读出序第二条另有其人


def test_legacy_round_without_reference_keeps_index_pairing(session):
    """存量轮次的元数据没有引用：退回下标配对，行为与改前一致，不猜。"""
    p = Project(name="q")
    session.add(p)
    session.flush()
    verdict = _verdict_two_findings()
    legacy_meta = json.loads(_build_quality_meta(verdict, []))
    assert all(fm["finding_ref"] is None for fm in legacy_meta["findings"])
    round_ = _round(p.id, uuid.uuid4(), 1, "revise", None)
    round_.revision_points = json.dumps(verdict["revision_points"], ensure_ascii=False)
    session.add(round_)
    session.flush()
    _write_findings_with_reversed_read_order(session, round_, verdict["findings"])
    round_.quality_meta = json.dumps(legacy_meta, ensure_ascii=False)
    session.flush()

    view = _project(session, round_)
    # 下标配对：读出序第一条拿到元数据第一条（这正是改前的行为，存量数据维持原状）
    assert view.findings[0].rule_code == "ISO-29148-A"
    assert view.revision_points[0].finding_ref is None


def _verdict_two_high_plus_no_blocker():
    """两条 high 真发现项 + 一条 no_blocker（中）。正确聚合＝high 2、no_blocker 不计。

    严重度特意都取 high：旧的下标配对会因跳过 no_blocker 用行下标而张冠李戴，得出
    high=1/medium=1（把 no_blocker 的 medium 张到某条真发现项头上），与正确值可区分。
    """
    return {
        "verdict_kind": "revise", "verdict_summary": "s",
        "findings": [
            {"finding_type": "ambiguous_expression", "diagnosis_summary": "第一条：表达含糊",
             "rule_code": "ISO-29148-A", "severity": "high", "dimension": "unambiguous",
             "evidence_span": "尽快"},
            {"finding_type": "untestable", "diagnosis_summary": "第二条：无法验证",
             "rule_code": "INCOSE-R7", "severity": "high", "dimension": "verifiable",
             "evidence_span": "友好"},
            {"finding_type": "no_blocker", "diagnosis_summary": "第三条：未见阻断",
             "severity": "medium"},
        ],
    }


def test_alert_severities_pair_by_reference_across_no_blocker(session):
    """asset_read 的严重度聚合（KPI 告警计数 / 维护列表 Q 徽标）按 finding_ref 配对：
    读出序与写入序相反、且含 no_blocker 时，每条真发现项仍取属于自己的严重度。

    这是 C4 根因暴露的形态——跳过 no_blocker 用的是行下标、元数据又按行下标拉链，读出序
    一变就有一份严重度被错配。旧口径此处会得出 high=1/medium=1；正确值是两条真发现项都
    是 high、no_blocker 不计。反序夹具（非手工递增 created_at）才网得住这个缺陷（清理束 25）。
    """
    from sqlalchemy import select
    from app.db.models import ItemReviewFinding

    p = Project(name="q")
    session.add(p)
    session.flush()
    verdict = _verdict_two_high_plus_no_blocker()
    round_ = _round(p.id, uuid.uuid4(), 1, "revise", None)
    session.add(round_)
    session.flush()
    refs = _write_findings_with_reversed_read_order(session, round_, verdict["findings"])
    round_.quality_meta = _build_quality_meta(verdict, refs)
    session.flush()

    repo = AssetReadRepository(session)
    # 前置：读出序确与写入序相反（no_blocker 被读到了首位而非末位），否则用例证明不了任何事。
    read_types = [
        f.finding_type
        for f in session.scalars(
            select(ItemReviewFinding)
            .where(ItemReviewFinding.round_ref == round_.id)
            .order_by(ItemReviewFinding.created_at, ItemReviewFinding.id)
        ).all()
    ]
    assert read_types == ["no_blocker", "untestable", "ambiguous_expression"]

    # 严重度序列（仅真发现项，各取自身）＝两条 high；no_blocker 不计。
    assert sorted(repo._round_finding_severities(round_)) == ["high", "high"]
    assert repo.quality_alert_summary(str(p.id)) == {
        "high": 2, "medium": 0, "low": 0, "diagnosed_items": 1,
    }
    assert repo.item_quality_index(str(p.id))[str(round_.item_ref)]["alert"] == "high"


def test_quality_meta_without_quality_fields_still_returns_none(session):
    """带上引用不得让「无质量字段就不落库」的判据失效（引用恒有值，不算内容）。"""
    verdict = {"findings": [{"finding_type": "no_blocker", "diagnosis_summary": "ok"}],
               "quality_profile": None, "ears_rewrite": None, "source_alignments": None}
    assert _build_quality_meta(verdict, ["some-ref"]) is None
