"""P2 两翼框架基座：knowledge_category 单一来源防漏 + 派生 + 谓词关系测试。"""
from app.domain.enums import (
    ELEMENT_KNOWLEDGE_CATEGORY,
    ELEMENT_TO_ITEM_TYPE,
    ITEMIZABLE_ELEMENT_TYPES,
    ElementType,
    KnowledgeCategory,
    knowledge_category_of,
)


def test_knowledge_category_covers_all_element_types():
    """映射全覆盖防漏：ElementType 任一成员缺归属即失败（新增类型忘配翼→红）。"""
    missing = [t for t in ElementType if t not in ELEMENT_KNOWLEDGE_CATEGORY]
    assert not missing, f"ELEMENT_KNOWLEDGE_CATEGORY 缺映射: {missing}"


def test_knowledge_category_values_are_valid():
    for t, cat in ELEMENT_KNOWLEDGE_CATEGORY.items():
        assert isinstance(cat, KnowledgeCategory)


def test_itemizable_equals_element_to_item_type_keys():
    """可条目化谓词单一来源：ITEMIZABLE == frozenset(ELEMENT_TO_ITEM_TYPE)。"""
    assert ITEMIZABLE_ELEMENT_TYPES == frozenset(ELEMENT_TO_ITEM_TYPE)


def test_itemizable_subset_of_requirement_wing():
    """两谓词关系：可条目化 ⊂ 需求翼（目标/场景属需求翼但不可条目化）。"""
    for code in ITEMIZABLE_ELEMENT_TYPES:
        assert knowledge_category_of(code) == KnowledgeCategory.REQUIREMENT.value
    # 需求翼含不可条目化成员（goal/scenario）
    assert knowledge_category_of("goal") == "requirement"
    assert knowledge_category_of("scenario") == "requirement"
    assert "goal" not in ITEMIZABLE_ELEMENT_TYPES
    assert "scenario" not in ITEMIZABLE_ELEMENT_TYPES


def test_business_wing_members():
    """业务翼成员（P3 起含 business_rule）。"""
    business = {t.value for t, c in ELEMENT_KNOWLEDGE_CATEGORY.items()
               if c == KnowledgeCategory.BUSINESS}
    assert business == {"term", "assumption", "role", "external_system", "business_rule"}


def test_knowledge_category_of_accepts_enum_and_str():
    assert knowledge_category_of(ElementType.TERM) == "business"
    assert knowledge_category_of("term") == "business"
    assert knowledge_category_of(ElementType.FUNCTIONAL_REQUIREMENT) == "requirement"


def test_derived_field_on_read_model_11_types_sample():
    """要素读模型派生字段正确（11 类抽样）。"""
    from app.api.schemas import RequirementElementRead
    for t in ElementType:
        m = RequirementElementRead(
            id="x", element_type=t.value, content="c", process_status="confirmed",
        )
        assert m.knowledge_category == knowledge_category_of(t.value)
        assert m.knowledge_category in ("requirement", "business")
