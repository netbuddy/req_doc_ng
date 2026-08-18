"""条目陈述档案加载器多方案校验（选型文档 §1）。

覆盖：封闭方案集、convention_key↔目录名一致、五类 req_type 齐全、_meta.examples 五类覆盖、
get_profile(req_type, convention_key)、未知方案/未知类型降级、公共层注入块。
"""
import pytest

from app.domain import item_profiles as ip


def test_convention_keys_are_closed_triple():
    assert ip.CONVENTION_KEYS == ("ears-cn", "boilerplate-cn", "master-cn")
    assert ip.DEFAULT_CONVENTION == "ears-cn"


def test_each_convention_covers_five_req_types():
    for key in ip.CONVENTION_KEYS:
        profiles = ip.profiles_of(key)
        assert set(profiles) == set(ip.REQ_TYPES)
        for req_type, profile in profiles.items():
            assert profile.convention_key == key       # convention_key 与目录名一致
            assert profile.req_type == req_type          # req_type 与文件名一致
            assert profile.profile_version >= 1
            assert profile.statement_pattern.strip()
            assert profile.facets, f"{key}/{req_type} 无 facet"


def test_convention_specific_facets_present():
    # 方案差异化必备 facet（选型文档 §1.4）
    assert ip.get_profile("functional", "boilerplate-cn").facet("modal_word") is not None
    assert ip.get_profile("functional", "boilerplate-cn").facet("object") is not None
    assert ip.get_profile("functional", "master-cn").facet("interaction_kind") is not None
    assert ip.get_profile("quality", "master-cn").facet("master_variant") is not None
    assert ip.get_profile("constraint", "master-cn").facet("master_variant") is not None
    # ears-cn 保持现状：functional 无 modal_word/object（内容不变不变式）
    assert ip.get_profile("functional", "ears-cn").facet("modal_word") is None


def test_meta_examples_cover_five_req_types():
    for meta in ip.convention_catalog():
        assert {e.req_type for e in meta.examples} == set(ip.REQ_TYPES)
        assert meta.pattern_overview  # 句式模板速览非空
        assert meta.display_name and meta.blueprint and meta.positioning


def test_get_profile_defaults_to_ears_cn():
    assert ip.get_profile("functional").convention_key == "ears-cn"


def test_unknown_convention_and_type_degrade_to_none():
    assert ip.get_profile("functional", "no-such-convention") is None
    assert ip.get_profile("no-such-type", "ears-cn") is None
    assert ip.profiles_of("no-such-convention") == {}


def test_convention_catalog_ordered_and_named():
    cat = ip.convention_catalog()
    assert [m.convention_key for m in cat] == list(ip.CONVENTION_KEYS)
    assert ip.convention_display_name("master-cn") == "中文 MASTeR"
    assert ip.convention_display_name("unknown") == "unknown"


def test_common_constraints_layer():
    c = ip.common_constraints()
    assert {r.key for r in c.quality_rules} == {"Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"}
    text = ip.common_constraints_text()
    assert "模态词规范" in text and "不得" in text
    assert "Q7" in text


# ---- 判据驱动 N/A 通道（T20260714-completeness-na-gate）----


def test_not_applicable_in_facet_statuses():
    assert "not_applicable" in ip.FACET_STATUSES


@pytest.mark.parametrize("convention", ip.CONVENTION_KEYS)
def test_data_lifecycle_declares_applicability_others_none(convention):
    """三方案 data 档案 lifecycle_or_volume 均声明适用性；同档其余成分未声明（零变）。"""
    p = ip.get_profile("data", convention)
    assert p.facet("lifecycle_or_volume").applicability
    assert p.facet("data_object").applicability is None
    assert p.facet("key_attributes").applicability is None


def test_profile_completeness_na_treated_as_satisfied():
    p = ip.get_profile("data", "ears-cn")
    assert p.completeness_of({
        "data_object": "present", "key_attributes": "present",
        "lifecycle_or_volume": "not_applicable",
    }) == "complete"
    assert p.completeness_of({
        "data_object": "present", "key_attributes": "present",
        "lifecycle_or_volume": "missing",
    }) == "incomplete"
