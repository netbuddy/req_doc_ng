"""领域对象定义（app/domain/model.py）的守护测试。

三层核对：①定义可用——全部概念可构造、不可变、不变条件生效；②与契约一致——
知识单元内容值对象的字段名与结构正本 api/schemas/knowledge.yaml 逐一对齐；
③与设计文档一致——概念清单与枚举列值对照《领域模型》正本（文档在设计仓，经
本地 docs 符号链接可达；干净克隆无该目录时跳过，与走读引用测试同一处理）。
"""
import dataclasses
import pathlib
import re
import uuid
from datetime import UTC, datetime

import pytest
import yaml

from app.domain import model as m
from app.domain.errors import InvalidInput

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
KNOWLEDGE_YAML = REPO / "api" / "schemas" / "knowledge.yaml"
DOMAIN_DOC = REPO / "docs" / "v2" / "design" / "领域模型.md"

_UID = uuid.uuid4()
_NOW = datetime.now(UTC)


def _anchor() -> m.SourceAnchor:
    return m.SourceAnchor(
        material_id=_UID,
        parse_result_id=_UID,
        start_offset=0,
        end_offset=4,
        quote="精确子串",
        hit_count=1,
    )


def _content() -> m.RequirementContent:
    return m.RequirementContent(
        title="退款时限",
        description="系统应在 3 个工作日内完成退款。",
        category=m.RequirementCategory.功能需求,
        attributes=(m.AttributeItem(name="时限", value="3 个工作日"),),
    )


def _snapshot(**overrides) -> m.Snapshot:
    kwargs = dict(
        id=_UID,
        asset_id=_UID,
        seq_no=1,
        content=_content(),
        content_sha256="0" * 64,
        author_kind=m.AuthorKind.智能体,
        task_id=_UID,
        anchors=(_anchor(),),
        submitted_at=_NOW,
    )
    kwargs.update(overrides)
    return m.Snapshot(**kwargs)


def test_all_concepts_construct_and_frozen():
    """全概念图谱可构造；frozen 拒绝改动。"""
    project = m.Project(id=_UID, name="示例项目", created_at=_NOW)
    m.UserAccount(id=_UID, display_name="治理者甲")
    m.Membership(project_id=_UID, user_id=_UID)
    m.Material(
        id=_UID, project_id=_UID, name="访谈记录", source_kind=m.SourceKind.粘贴,
        content="原文", content_sha256="0" * 64, imported_by=_UID, imported_at=_NOW,
    )
    m.ParseResult(
        id=_UID, material_id=_UID, parser_version="v1",
        text="规范化全文", text_sha256="0" * 64, created_at=_NOW,
    )
    m.Task(
        id=_UID, project_id=_UID, kind=m.TaskKind.提取, status=m.TaskStatus.已登记,
        initiated_by=_UID, material_ids=(_UID,),
        failure_items=(m.TaskFailureItem(reason="材料乱码"),),
        created_at=_NOW, updated_at=_NOW,
    )
    m.Asset(
        id=_UID, project_id=_UID, kind=m.AssetKind.需求知识,
        status=m.AssetStatus.待确认, created_at=_NOW,
    )
    _snapshot()
    m.ConceptContent(name="退款单", definition="记录一次退款请求的单据。", aliases=("退款申请",))
    m.Version(
        id=_UID, asset_id=_UID, version_no=1,
        snapshot_id=_UID, decision_id=_UID, created_at=_NOW,
    )
    m.Decision(
        id=_UID, asset_id=_UID, snapshot_id=_UID, kind=m.DecisionKind.确认,
        decided_by=_UID, gate_record={"结构规范": "通过"}, decided_at=_NOW,
    )
    m.AuditLog(
        id=_UID, project_id=_UID, user_id=_UID, action="确认候选", occurred_at=_NOW,
    )
    m.AgentRun(
        id=_UID, task_id=_UID, model_version="qwen3.8-27b",
        prompt_version="extract-v1", started_at=_NOW,
    )
    m.TaskReadRecord(task_id=_UID, parse_result_id=_UID, read_at=_NOW)

    with pytest.raises(dataclasses.FrozenInstanceError):
        project.name = "改名"  # type: ignore[misc]


def test_invariants_reject_illegal_values():
    """定义随身携带的不变条件（出处见各类 docstring）逐条生效。"""
    with pytest.raises(InvalidInput):
        m.AttributeItem(name="时限")  # 两形态都缺
    with pytest.raises(InvalidInput):
        m.AttributeItem(name="时限", value="3 天", candidates=("3 天", "3 个工作日"))  # 两形态都带
    with pytest.raises(InvalidInput):
        m.AttributeItem(name="时限", candidates=("只有一条",))  # 未消解形态候选不足两条
    with pytest.raises(InvalidInput):
        _snapshot(author_kind=m.AuthorKind.智能体, task_id=None)  # 智能体缺产生任务
    with pytest.raises(InvalidInput):
        _snapshot(author_kind=m.AuthorKind.治理者, audit_id=None)  # 治理者缺修订留痕
    with pytest.raises(InvalidInput):
        _snapshot(anchors=())  # 缺来源锚定
    with pytest.raises(InvalidInput):
        m.Task(
            id=_UID, project_id=_UID, kind=m.TaskKind.提取, status=m.TaskStatus.已登记,
            initiated_by=_UID, material_ids=(), created_at=_NOW, updated_at=_NOW,
        )  # 输入材料为空
    with pytest.raises(InvalidInput):
        m.Decision(
            id=_UID, asset_id=_UID, snapshot_id=_UID, kind=m.DecisionKind.退回,
            decided_by=_UID, decided_at=_NOW,
        )  # 退回缺意见
    with pytest.raises(InvalidInput):
        m.Decision(
            id=_UID, asset_id=_UID, snapshot_id=_UID, kind=m.DecisionKind.确认,
            decided_by=_UID, decided_at=_NOW,
        )  # 确认缺门禁判定记录
    with pytest.raises(InvalidInput):
        m.RequirementContent(
            kind=m.AssetKind.领域概念, title="错判别", description="x",
            category=m.RequirementCategory.其他,
        )


def test_content_value_objects_match_contract():
    """知识单元内容两变体的字段名与结构正本 knowledge.yaml 完全一致（§3.14）。"""
    spec = yaml.safe_load(KNOWLEDGE_YAML.read_text(encoding="utf-8"))
    for schema_name, cls in [
        ("RequirementContent", m.RequirementContent),
        ("ConceptContent", m.ConceptContent),
    ]:
        contract_fields = set(spec[schema_name]["properties"])
        domain_fields = {f.name for f in dataclasses.fields(cls)}
        assert domain_fields == contract_fields, (
            f"{schema_name} 字段漂移：领域独有 {domain_fields - contract_fields}，"
            f"契约独有 {contract_fields - domain_fields}"
        )
    # 属性项在契约里是两形态 oneOf、在领域里是带二选一不变条件的单类（§3.15），
    # 只核对字段并集一致。
    attr_union = set()
    for variant in spec["AttributeItem"]["oneOf"]:
        attr_union |= set(variant["properties"])
    assert {f.name for f in dataclasses.fields(m.AttributeItem)} == attr_union


needs_doc = pytest.mark.skipif(
    not DOMAIN_DOC.is_file(), reason="设计仓文档不可达（干净克隆无 docs 符号链接）"
)


@needs_doc
def test_concept_list_matches_domain_doc():
    """《领域模型》§2 概念清单的每个存储落点表名都有同名领域类（表名转帕斯卡命名）。"""
    text = DOMAIN_DOC.read_text(encoding="utf-8")
    section = text.split("## 2 概念清单")[1].split("## 3 ")[0]
    entity_lines = [
        line for line in section.splitlines()
        if line.startswith("|") and ("| 实体" in line or "| 关系" in line)
    ]
    tables = [
        match.group(1)
        for line in entity_lines
        for match in [re.search(r"表\s+([a-z_]+)", line)]
        if match
    ]
    assert len(tables) >= 13, "概念清单解析异常：存储落点表名不足 13 个"
    value_objects = {"RequirementContent", "ConceptContent", "AttributeItem",
                     "SourceAnchor", "TaskFailureItem"}
    for table in tables:
        cls_name = "".join(part.capitalize() for part in table.split("_"))
        assert hasattr(m, cls_name), f"概念缺失：表 {table} 应有领域类 {cls_name}"
    for vo in value_objects:
        assert hasattr(m, vo), f"值对象缺失：{vo}"


@needs_doc
def test_enum_values_match_domain_doc():
    """《领域模型》§3 每处「列值：」声明都与某个领域枚举的取值集合一致。"""
    text = DOMAIN_DOC.read_text(encoding="utf-8")
    section = text.split("## 3 字段定义")[1].split("## 4 ")[0]
    enum_sets = [
        frozenset(e.value for e in enum_cls)
        for enum_cls in (
            m.SourceKind, m.TaskKind, m.TaskStatus, m.AssetKind,
            m.AssetStatus, m.AuthorKind, m.DecisionKind, m.RequirementCategory,
        )
    ]
    declared = re.findall(r"列值：([^（）。；|]+)", section)
    assert len(declared) >= 8, "列值声明解析异常：应至少 8 处"
    for raw in declared:
        values = frozenset(v.strip() for v in raw.split("／") if v.strip())
        if len(values) == 1:
            assert any(values <= s for s in enum_sets), f"列值 {values} 不在任何枚举中"
        else:
            assert values in enum_sets, f"列值集合 {sorted(values)} 与所有领域枚举都不一致"
