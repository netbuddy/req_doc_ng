"""A2 异步：worker 作业核心、QueuedModelOrchestration 入队、AgentRun poll + SSE 推送端点。"""
import uuid

import fakeredis
import fakeredis.aioredis
import pytest
import redis
import redis.asyncio
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

import app.api.agent_runs as agent_runs_mod
import app.db.models  # noqa: F401  register tables
import app.workers.tasks as tasks
from app.adapters.event_bus import RedisStreamEventBus
from app.adapters.llm import StubSourceIntakeJudge
from app.db.base import Base, make_engine, make_session_factory
from app.db.models import AgentRun, IntakeRecord, Material, ModelResult, Project
from app.deps import get_agent_run_repo
from app.domain.enums import ModelJudgement
from app.main import app
from app.repositories.agent_run import SqlAgentRunRepository
from app.repositories.sqlalchemy import SqlProcessRecordRepository
from app.services.model_orchestration import QueuedModelOrchestration
from app.workers.tasks import _process


class RecordingBus:
    """测试用事件总线：记录 publish 调用（run_id, event）。"""

    live = False

    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    def publish(self, run_id: str, event: str) -> None:
        self.published.append((run_id, event))

    async def subscribe(self, run_id, last_id="0"):  # pragma: no cover
        return
        yield


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


def _seed_project(session) -> str:
    p = Project(name="demo")
    session.add(p)
    session.flush()
    return str(p.id)


def _seed_request(session, pid, key="K1") -> str:
    return SqlProcessRecordRepository(session).create_intake_request(
        pid, key, "系统应支持导出 docx", "访谈", "U1"
    )


# ---- worker 作业核心（_process）----

def test_worker_process_accepts_and_run_succeeds(session):
    pid = _seed_project(session)
    ctx = _seed_request(session, pid)
    run_id = SqlAgentRunRepository(session).create("source_intake", ctx)
    session.commit()

    _process(session, ctx, run_id, StubSourceIntakeJudge(ModelJudgement.ACCEPTABLE))

    assert session.get(AgentRun, uuid.UUID(run_id)).status == "succeeded"
    assert session.scalar(select(IntakeRecord)).intake_conclusion == "accepted"
    assert session.scalar(select(Material)) is not None


def test_worker_process_failed_judgement_run_still_succeeds_no_material(session):
    pid = _seed_project(session)
    ctx = _seed_request(session, pid, key="K2")
    run_id = SqlAgentRunRepository(session).create("source_intake", ctx)
    session.commit()

    _process(session, ctx, run_id, StubSourceIntakeJudge(ModelJudgement.JUDGEMENT_FAILED))

    assert session.get(AgentRun, uuid.UUID(run_id)).status == "succeeded"  # 任务本身跑完了
    assert session.scalar(select(Material)) is None  # 业务结局=判断失败，不写材料
    assert session.scalar(select(ModelResult)).judgement == "judgement_failed"


# ---- 异步编排：只登记 AgentRun + 入队 ----

def test_queued_orchestration_creates_run_and_enqueues(session):
    calls: list[tuple[str, str]] = []
    orch = QueuedModelOrchestration(
        session, SqlAgentRunRepository(session), lambda c, r: calls.append((c, r))
    )
    ctx = str(uuid.uuid4())
    run_id = orch.request_source_intake_judgement(ctx)

    assert calls == [(ctx, run_id)]  # 入队参数 = (context, run_id)
    run = session.get(AgentRun, uuid.UUID(run_id))
    assert run.status == "queued" and run.kind == "source_intake"


# ---- poll 端点 ----

def test_poll_endpoint_returns_status_events_and_404():
    # 共享内存库（StaticPool）：TestClient 在 threadpool 线程跑 sync 路由，需跨线程共用同一 DB。
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)

    seed = factory()
    repo = SqlAgentRunRepository(seed)
    run_id = repo.create("source_intake", None)
    repo.mark_started(run_id)
    repo.mark_succeeded(run_id)
    seed.commit()
    seed.close()

    def _override():
        s = factory()
        try:
            yield SqlAgentRunRepository(s)
        finally:
            s.close()

    app.dependency_overrides[get_agent_run_repo] = _override
    try:
        client = TestClient(app)
        r = client.get(f"/api/agent-runs/{run_id}")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "succeeded"
        assert any(e["event"] == "agent_run.completed" for e in data["events"])
        assert client.get("/api/agent-runs/00000000-0000-0000-0000-000000000000").status_code == 404
    finally:
        app.dependency_overrides.pop(get_agent_run_repo, None)
        engine.dispose()


# ---- 事件发布顺序（提交后发布）----

def test_process_publishes_started_then_completed(session):
    pid = _seed_project(session)
    ctx = _seed_request(session, pid, key="Kpub")
    run_id = SqlAgentRunRepository(session).create("source_intake", ctx)
    session.commit()
    bus = RecordingBus()

    _process(session, ctx, run_id, StubSourceIntakeJudge(ModelJudgement.ACCEPTABLE), bus)

    assert [e for _, e in bus.published] == ["agent_run.started", "agent_run.completed"]
    assert all(rid == run_id for rid, _ in bus.published)


def test_run_source_intake_failure_publishes_started_then_failed(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    seed = factory()
    pid = _seed_project(seed)
    ctx = _seed_request(seed, pid, key="Kfail")
    run_id = SqlAgentRunRepository(seed).create("source_intake", ctx)
    seed.commit()
    seed.close()

    class RaisingJudge:
        def judge(self, project_ref, raw_text, source_note):
            raise RuntimeError("boom")

    bus = RecordingBus()
    monkeypatch.setattr(tasks, "_SessionFactory", factory)
    monkeypatch.setattr(tasks, "build_source_intake_judge", lambda s: RaisingJudge())
    monkeypatch.setattr(tasks, "build_agent_run_event_bus", lambda s: bus)

    tasks.run_source_intake(ctx, run_id)

    assert [e for _, e in bus.published] == ["agent_run.started", "agent_run.failed"]
    check = factory()
    assert check.get(AgentRun, uuid.UUID(run_id)).status == "failed"
    check.close()
    engine.dispose()


# ---- SSE：Redis Streams 推送 + 终态帧内联结果 ----

def test_sse_terminal_frame_inlines_result(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    # 跑完一次接入，让 DB 有 accepted 结论
    seed = factory()
    pid = _seed_project(seed)
    ctx = _seed_request(seed, pid, key="Ksse")
    run_id = SqlAgentRunRepository(seed).create("source_intake", ctx)
    seed.commit()
    _process(seed, ctx, run_id, StubSourceIntakeJudge(ModelJudgement.ACCEPTABLE))  # Null bus
    seed.close()

    # fakeredis 支撑的 live 总线（发布/订阅共享同一 server）
    server = fakeredis.FakeServer()
    monkeypatch.setattr(
        redis.Redis, "from_url",
        staticmethod(lambda url, **kw: fakeredis.FakeStrictRedis(server=server, **kw)),
    )
    monkeypatch.setattr(
        redis.asyncio.Redis, "from_url",
        staticmethod(lambda url, **kw: fakeredis.aioredis.FakeRedis(server=server, **kw)),
    )
    live_bus = RedisStreamEventBus("redis://fake")
    live_bus.publish(run_id, "agent_run.started")
    live_bus.publish(run_id, "agent_run.completed")

    # 端点用 live 总线 + sqlite 会话读结论
    monkeypatch.setattr(agent_runs_mod, "agent_run_event_bus", live_bus)
    monkeypatch.setattr(agent_runs_mod, "new_session", factory)

    client = TestClient(app)
    r = client.get(f"/api/agent-runs/{run_id}/events")

    assert r.status_code == 200
    body = r.text
    assert "agent_run.started" in body
    assert "agent_run.completed" in body
    assert '"result"' in body  # 终态帧内联结果
    assert "intake_conclusion" in body and "accepted" in body  # 前端无需第三次调用
    engine.dispose()


# ---- 时间戳口径（T20260724-agent-run-observability ④）----
# 缺陷：PostgreSQL 的 now() 返回事务开始时刻，worker 的终态迁移写在横跨 LLM 调用的长事务里，
# 于是"完成时刻"被记成"开始时刻"（实测有 status=succeeded 而 updated_at 停在 started 时刻的行）。


def test_agent_run_updated_at_uses_statement_clock_per_dialect():
    """列定义钉住：PG 取语句时刻 clock_timestamp()，SQLite 取语义等价的 CURRENT_TIMESTAMP。"""
    from sqlalchemy.dialects import postgresql, sqlite

    expr = AgentRun.__table__.c.updated_at.onupdate.arg

    assert str(expr.compile(dialect=postgresql.dialect())) == "clock_timestamp()"
    assert str(expr.compile(dialect=sqlite.dialect())) == "CURRENT_TIMESTAMP"


@pytest.fixture()
def pg_engine():
    """真 PostgreSQL 引擎；不可用则跳过（事务开始时刻与语句时刻的差异只在 PG 上成立）。"""
    from sqlalchemy import text

    from app.config import settings

    if not settings.database_url.startswith("postgresql"):
        pytest.skip("需要 PostgreSQL 才能复现事务开始时刻与语句时刻的差异")
    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 环境无 PG：跳过而非失败
        engine.dispose()
        pytest.skip("PostgreSQL 不可达")
    yield engine
    engine.dispose()


def test_updated_at_records_statement_time_inside_long_transaction(pg_engine):
    """长事务内的终态迁移：updated_at 记迁移真正发生的时刻，不是事务开始时刻。

    修前（onupdate=now()）本测试红：两个时刻在 PG 上恒相等。
    用同构影子表跑，不写真实 agent_run 表。
    """
    import time

    from sqlalchemy import MetaData, text

    probe = AgentRun.__table__.to_metadata(MetaData(), name="agent_run_clock_probe")
    probe.drop(pg_engine, checkfirst=True)
    probe.create(pg_engine)
    try:
        run_id = uuid.uuid4()
        with pg_engine.begin() as conn:
            conn.execute(probe.insert().values(id=run_id, kind="item_formation", status="queued"))

        with pg_engine.begin() as conn:  # 一段长事务：开始 → 干活（此处以 sleep 模拟）→ 终态迁移
            tx_started_at = conn.execute(text("SELECT now()")).scalar()
            time.sleep(0.3)
            conn.execute(probe.update().where(probe.c.id == run_id).values(status="succeeded"))
            updated_at = conn.execute(
                probe.select().where(probe.c.id == run_id)
            ).mappings().one()["updated_at"]

        elapsed = (updated_at - tx_started_at).total_seconds()
        assert elapsed >= 0.25, f"updated_at 停在事务开始时刻（相差仅 {elapsed:.3f} 秒）"
    finally:
        probe.drop(pg_engine, checkfirst=True)
