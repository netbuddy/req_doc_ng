"""P6b 领域档案加载器测试义务（09 §2 P6；设计 08 §2）。

覆盖：封闭集/generic 兜底/内容加载/坏档案拒载/list 目录序。
"""
import pytest

from app.domain.domain_profiles import (
    DomainProfileError,
    _parse_profile,
    domain_profile_keys,
    get_domain_profile,
    list_domain_profiles,
    load_domain_profiles,
)


def test_closed_set_includes_generic_and_pilot():
    keys = domain_profile_keys()
    assert "generic" in keys and "ecommerce-fulfillment" in keys
    assert get_domain_profile("generic").is_empty  # generic 空内容=等同 P6a


def test_generic_fallback_for_none_and_unknown():
    assert get_domain_profile(None).key == "generic"
    assert get_domain_profile("nonexistent-domain").key == "generic"


def test_ecommerce_profile_content_loaded():
    p = get_domain_profile("ecommerce-fulfillment")
    assert p.label == "电商订单履约" and p.version >= 1
    assert any(t.term == "履约单" for t in p.glossary_seed)
    assert "系统" in p.common_terms  # 通用词排除清单
    assert p.rule_patterns and not p.is_empty


def test_list_generic_first():
    assert list_domain_profiles()[0].key == "generic"


def test_bad_profile_rejected(tmp_path):
    # key 与目录名不一致 → 拒载
    d = tmp_path / "bad-domain"
    d.mkdir()
    (d / "_meta.yaml").write_text("key: wrong-key\nlabel: X\nversion: 1\n", encoding="utf-8")
    with pytest.raises(DomainProfileError):
        _parse_profile(d)
    # 缺 label → 拒载
    (d / "_meta.yaml").write_text("key: bad-domain\nversion: 1\n", encoding="utf-8")
    with pytest.raises(DomainProfileError):
        _parse_profile(d)
    # version 非法 → 拒载
    (d / "_meta.yaml").write_text("key: bad-domain\nlabel: X\nversion: abc\n", encoding="utf-8")
    with pytest.raises(DomainProfileError):
        _parse_profile(d)


def test_load_cached_singleton():
    assert load_domain_profiles() is load_domain_profiles()  # lru_cache 单例


def test_render_domain_reference_two_state():
    from app.domain.domain_profiles import render_domain_reference

    # generic（空档案）→ 空串（模板 |default → 省整段）
    empty = render_domain_reference(get_domain_profile("generic"))
    assert empty == {"domain_glossary": "", "domain_common_terms": "", "domain_rule_patterns": ""}
    # 电商档案 → 术语/通用词/规则模式非空
    ref = render_domain_reference(get_domain_profile("ecommerce-fulfillment"))
    assert "履约单" in ref["domain_glossary"]
    assert "系统" in ref["domain_common_terms"]
    assert ref["domain_rule_patterns"]


# ---- AEP-103 只读目录 + 项目档案往返（AC-P6-02 后端）----

def test_aep103_catalog_shape():
    from app.api.config import list_domain_profiles_catalog

    catalog = list_domain_profiles_catalog()
    assert catalog[0].key == "generic"  # generic 置顶
    ecom = next(p for p in catalog if p.key == "ecommerce-fulfillment")
    assert ecom.label == "电商订单履约" and ecom.version >= 1 and ecom.description


def test_project_create_read_domain_profile_roundtrip():
    from app.api.schemas import CreateProjectCommand
    from app.repositories.in_memory import InMemoryProjectRepository
    from app.services.project_context import ProjectContextService

    svc = ProjectContextService(InMemoryProjectRepository())
    created = svc.create_project(CreateProjectCommand(
        name="电商项目", domain_profile_key="ecommerce-fulfillment",
        operator_ref="测试者", idempotency_key="dp-k1"))
    assert created.domain_profile_key == "ecommerce-fulfillment"
    assert created.domain_profile_label == "电商订单履约"  # 派生中文名
    # 不指定 → generic 兜底（AC-P6-05 默认行为）
    plain = svc.create_project(CreateProjectCommand(
        name="无领域项目", operator_ref="测试者", idempotency_key="dp-k2"))
    assert plain.domain_profile_key is None and plain.domain_profile_label == "通用"
