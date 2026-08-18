"""持久化集成测试（SQLite create_all）：Sql 仓储把 LDM-001/002/003/015 真正落库。"""
import uuid

import pytest
from sqlalchemy import select

import app.db.models  # noqa: F401  register tables
from app.adapters.llm import StubSourceIntakeJudge
from app.api.schemas import TextIntakeCommand
from app.db.base import Base, make_engine, make_session_factory
from app.db.models import IntakeRecord, IntakeRequest, Material, ModelResult, Project
from app.domain.enums import IntakeConclusion, IntakeRequestStatus, ModelJudgement
from app.repositories.sqlalchemy import build_sql_service


@pytest.fixture()
def session():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    s = factory()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def _seed_project(session) -> str:
    p = Project(name="demo")
    session.add(p)
    session.flush()
    return str(p.id)


def _cmd(pid, text="系统应支持导出 docx。", key="K1"):
    return TextIntakeCommand(
        project_ref=pid, raw_text=text, source_note="访谈", operator_ref="U1", idempotency_key=key
    )


def test_accepted_persists_all_rows(session):
    pid = _seed_project(session)
    svc = build_sql_service(session, auto_complete=True)  # 默认 stub 判定=可接入
    r = svc.submit_text_intake(_cmd(pid))
    session.commit()

    read = svc.read_intake_result(r.context_ref)
    assert read.intake_conclusion is IntakeConclusion.ACCEPTED
    assert read.material_ref
    assert read.basis  # LDM-015 判定依据已回填读视图

    material = session.scalar(select(Material))
    assert material is not None and material.raw_text == "系统应支持导出 docx。"  # LDM-002 承载原文
    rec = session.scalar(select(IntakeRecord))
    assert rec.intake_conclusion == "accepted" and rec.material_ref == material.id
    assert session.scalar(select(ModelResult)).judgement == "acceptable"


def test_excluded_persists_no_material(session):
    pid = _seed_project(session)
    svc = build_sql_service(
        session, auto_complete=True, judge=StubSourceIntakeJudge(ModelJudgement.NO_ASSET_VALUE)
    )
    r = svc.submit_text_intake(_cmd(pid, key="K2"))
    session.commit()

    read = svc.read_intake_result(r.context_ref)
    assert read.intake_conclusion is IntakeConclusion.EXCLUDED
    assert read.material_ref is None
    assert session.scalar(select(Material)) is None  # VAL-002：未接入不写 LDM-002
    assert session.scalar(select(IntakeRecord)).intake_conclusion == "excluded"


def test_judgement_failed_persists_no_material(session):
    pid = _seed_project(session)
    svc = build_sql_service(
        session, auto_complete=True, judge=StubSourceIntakeJudge(ModelJudgement.JUDGEMENT_FAILED)
    )
    r = svc.submit_text_intake(_cmd(pid, key="KF"))
    session.commit()

    read = svc.read_intake_result(r.context_ref)
    assert read.intake_conclusion is None  # 失败不写 LDM-003 结论
    assert read.next_action  # 保留人工继续
    assert session.scalar(select(Material)) is None
    assert session.scalar(select(ModelResult)).judgement == "judgement_failed"  # LDM-015 仍登记


def test_idempotent_replay_single_request_row(session):
    pid = _seed_project(session)
    svc = build_sql_service(session, auto_complete=True)
    a = svc.submit_text_intake(_cmd(pid, key="SAME"))
    session.commit()
    b = svc.submit_text_intake(_cmd(pid, key="SAME"))
    session.commit()
    assert a.context_ref == b.context_ref
    assert len(session.scalars(select(IntakeRequest)).all()) == 1


def test_unknown_project_rejected(session):
    svc = build_sql_service(session, auto_complete=True)
    r = svc.submit_text_intake(_cmd(str(uuid.uuid4()), key="K3"))
    assert r.status is IntakeRequestStatus.REJECTED_PRECHECK
    assert session.scalar(select(IntakeRequest)) is None
