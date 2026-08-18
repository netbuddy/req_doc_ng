"""材料一态制两列 + 材料列表接口（2026-08-07，字段级差异表 5-补 的最小三件）。

覆盖：①默认名称三规则之「粘贴取首行」；②接收落库时名称与哈希的计算
（走真实 SQLite 的 SqlIntakeRepository.save_material_and_intake_record）；
③列表接口的 V2 信封形状与倒序。
"""
import hashlib
import uuid

import pytest
from fastapi.testclient import TestClient

import app.db.models  # noqa: F401  register tables
import app.deps as deps
from app.db.base import Base, make_engine, make_session_factory
from app.db.models import IntakeRequest, Material
from app.domain.naming import material_default_name
from app.main import app
from app.repositories.sqlalchemy import SqlSourceAssetRepository


@pytest.fixture()
def session_factory(tmp_path, monkeypatch):
    engine = make_engine(f"sqlite:///{tmp_path / 'materials.db'}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    monkeypatch.setattr(deps, "_SessionFactory", factory)
    yield factory
    engine.dispose()


def test_default_name_takes_first_line_truncated():
    assert material_default_name("三、退款流程补充\n第二行", "x") == "三、退款流程补充"
    long = "这一行的文字明显超过二十个字所以必须发生截断行为才对"
    named = material_default_name(long, "x")
    assert named.endswith("…") and len(named) == 21
    assert material_default_name("  \n\n", "2026-08-07 10:00") == "粘贴材料-2026-08-07 10:00"


def test_save_material_computes_name_and_sha256(session_factory):
    raw = "响应时间要求：500 毫秒以内。\n其余见附件。"
    with session_factory() as s:
        req = IntakeRequest(
            project_id=uuid.uuid4(), raw_text=raw, source_note="",
            operator_ref="op-1", idempotency_key="k-1",
        )
        s.add(req); s.flush()
        material_ref = SqlSourceAssetRepository(s).save_material_and_intake_record(
            str(req.id), model_result_ref=str(uuid.uuid4())
        )
        m = s.get(Material, uuid.UUID(material_ref))
        assert m.name == "响应时间要求:500 毫秒以内。"[:20] or m.name.startswith("响应时间要求")
        assert m.content_sha256 == hashlib.sha256(raw.encode("utf-8")).hexdigest()
        s.commit()


def test_list_materials_envelope_and_order(session_factory):
    project = uuid.uuid4()
    with session_factory() as s:
        s.add(Material(project_id=project, raw_text="第一份", name="第一份", content_sha256="a" * 64))
        s.flush()
        s.add(Material(project_id=project, raw_text="第二份", name="第二份", content_sha256="b" * 64))
        s.add(Material(project_id=uuid.uuid4(), raw_text="别的项目", name="别的项目", content_sha256="c" * 64))
        s.commit()
    body = TestClient(app).get(f"/api/projects/{project}/materials").json()
    assert body["result"] == "成功"
    names = [row["name"] for row in body["data"]]
    assert set(names) == {"第一份", "第二份"}
    assert all(set(row) == {"material_id", "name", "source_kind", "imported_at", "content_sha256"} for row in body["data"])
