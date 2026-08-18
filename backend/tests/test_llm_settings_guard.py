"""配置必生效防线（T20260720-model-provider-registry · A2 + A5）。

要杜绝的事故：人在设置页改了模型服务地址，某条链路却仍打着进程启动时冻结的 env 地址，
直到上了生产才发现「界面配置没生效」。本仓真出过这个洞——`app/deps.py` 里的 8 个 LLM
客户端工厂都拿模块级 env `settings` 构建，于是异步任务 lane 读库生效、交互式 lane 不生效。

两道判据（任务卡 A5，2026-07-20 方案门改写）：
① 静态断言——产品代码里没有任何 LLM 客户端工厂拿模块级 env settings 当实参；
② 行为断言——库里改配置后不重启进程，下一次请求真的打到新地址；异步任务 lane 与交互式
   lane **各覆盖一条**，只测一条会漏掉正好坏掉的那一半。

判据没有沿用卡面原话「无 settings.llm_ 直读」：`app/adapters/llm.py` 的 17 个工厂读的是
调用方注入的已解析配置对象的字段，不是进程 env，禁掉那种写法只会逼出一层无收益的取值改写。
"""
from __future__ import annotations

import ast
import pathlib

import pytest
from sqlalchemy import delete

import app.db.models  # noqa: F401  register tables
import app.adapters.llm as llm_module
from app.adapters.llm import build_item_explainer, build_source_intake_judge
from app.config import Settings
from app.db.base import Base, make_session_factory
from app.db.models import ConfigEntry
from app.services.config_registry import ConfigRegistryService, resolve_llm_settings
from app.api.schemas import LlmProviderSaveCommand, LlmProviderWrite
from tests.provider_stub import ProviderStub

APP_DIR = pathlib.Path(__file__).resolve().parent.parent / "app"

# 豁免名单——这些地方拿 env settings 是本分，不是漏洞：
#   config.py            env 快照本身的定义处；
#   config_registry.py   把 env 当兜底底座解析出生效配置的地方（即那道闸门自己）；
#   adapters/llm.py      工厂的形参就叫 settings，读的是调用方注入的已解析配置；
#   adapters/embeddings.py 嵌入服务没有配置域，本就只有 env 一个来源；
#   scripts/llm_smoke.py 命令行冒烟脚本，不在请求链路上。
_EXEMPT = {
    "config.py",
    "services/config_registry.py",
    "adapters/llm.py",
    "adapters/embeddings.py",
    "scripts/llm_smoke.py",
}
# 模块级 env 配置对象的基础绑定名（各文件里的 `import ... as 别名` 由 _env_binding_names 另补）。
_ENV_SETTINGS_NAMES = {"settings", "env_settings"}


def _llm_factory_names() -> set[str]:
    """app/adapters/llm.py 里全部「吃 Settings 造 LLM 客户端」的工厂函数名。

    工厂集只取自 adapters/llm.py：本仓架构把全部 LLM 客户端工厂集中于此一处，别处不新建工厂
    （其它模块的 build_*，如 build_embedder / build_agent_run_event_bus，本就该拿 env 配置，
    不是本守护的对象；把它们卷进来只会误报）。
    """
    return {
        name
        for name in dir(llm_module)
        if name.startswith("build_") and callable(getattr(llm_module, name))
    }


def _env_binding_names(tree: ast.Module) -> set[str]:
    """某模块里绑定到进程 env 配置对象的全部本地名。

    含 `from app.config import settings as cfg` 这类别名——只认基础名会让别名写法从守护底下溜过。
    """
    names = set(_ENV_SETTINGS_NAMES)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "app.config":
            for alias in node.names:
                if alias.name in {"settings", "env_settings"}:
                    names.add(alias.asname or alias.name)
    return names


def _scan_for_env_settings_to_factory(
    root: pathlib.Path, factories: set[str], *, exempt: frozenset[str] = frozenset()
) -> list[str]:
    """扫 root 下所有 .py，找出把 env 配置对象交给 LLM 客户端工厂的调用；返回 offender 列表。

    位置实参 `build_x(settings)` 与关键字实参 `build_x(settings=settings)` 都要抓——后者是写回归
    最自然的形态，早先只遍历 node.args 恰恰漏掉了这最可能的一扇门。别名绑定名经 _env_binding_names
    逐文件解析后一并纳入。offender 形如 `相对路径:行号 工厂名(实参形态)`，空列表=干净。
    """
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel in exempt:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        env_names = _env_binding_names(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name not in factories:
                continue
            for arg in node.args:
                if isinstance(arg, ast.Name) and arg.id in env_names:
                    offenders.append(f"{rel}:{node.lineno} {name}({arg.id})")
            for kw in node.keywords:
                if kw.arg and isinstance(kw.value, ast.Name) and kw.value.id in env_names:
                    offenders.append(f"{rel}:{node.lineno} {name}({kw.arg}={kw.value.id})")
    return offenders


def test_no_product_code_feeds_env_settings_to_llm_factories():
    factories = _llm_factory_names()
    assert len(factories) >= 15, "工厂清单取空了，守护会形同虚设"

    offenders = _scan_for_env_settings_to_factory(APP_DIR, factories, exempt=_EXEMPT)
    assert offenders == [], (
        "以下调用把进程启动时冻结的 env 配置直接交给了 LLM 客户端工厂，"
        "界面上改的模型服务配置对这些链路将永不生效；"
        "改为先经 resolve_llm_settings 取本次请求的生效配置：\n  "
        + "\n  ".join(f"app/{o}" for o in offenders)
    )


def test_guard_catches_regressions_in_all_natural_forms(tmp_path):
    """守护本身的反向验证：对临时代码树调用**真实的**扫描例程，三种回归写法都必须被抓出来。

    早先的反向测试内联复制了一份只扫位置实参的检测逻辑，既没调真正的守护例程、又复用了同一个
    盲区，给出的是虚假的健壮性信心。现在直接跑 `_scan_for_env_settings_to_factory`（主测试用的
    同一套），并覆盖位置 / 关键字 / 别名三种形态。
    """
    factories = _llm_factory_names()
    # ① 位置实参
    (tmp_path / "positional.py").write_text(
        "from app.config import settings\n"
        "from app.adapters.llm import build_item_explainer\n"
        "x = build_item_explainer(settings)\n",
        encoding="utf-8",
    )
    # ② 关键字实参——写回归最自然的方式（每个工厂首参就叫 settings），旧守护恰恰漏掉
    (tmp_path / "keyword.py").write_text(
        "from app.config import settings\n"
        "from app.adapters.llm import build_source_intake_judge\n"
        "j = build_source_intake_judge(settings=settings)\n",
        encoding="utf-8",
    )
    # ③ 别名——from app.config import settings as cfg
    (tmp_path / "aliased.py").write_text(
        "from app.config import settings as cfg\n"
        "from app.adapters.llm import build_element_reviewer\n"
        "r = build_element_reviewer(cfg)\n",
        encoding="utf-8",
    )
    # 反例：把调用方注入的已解析配置（非 env）交给工厂是正当写法，绝不能被误报
    (tmp_path / "clean.py").write_text(
        "from app.adapters.llm import build_item_explainer\n"
        "def f(resolved):\n"
        "    return build_item_explainer(resolved)\n",
        encoding="utf-8",
    )

    offenders = _scan_for_env_settings_to_factory(tmp_path, factories)
    joined = "\n".join(offenders)
    assert "positional.py:3 build_item_explainer(settings)" in joined
    assert "keyword.py:3 build_source_intake_judge(settings=settings)" in joined
    assert "aliased.py:3 build_element_reviewer(cfg)" in joined
    # clean.py 的正当写法不计入——三种坏形态，不多不少
    assert len(offenders) == 3


# ---------------------------------------------------------------------------
# 行为断言：改库配置 → 不重启进程 → 下一次调用打到新地址
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    session = factory()
    try:
        yield session, factory
    finally:
        session.close()
        engine.dispose()


def _point_at(session, base_url: str, model: str, provider_type: str = "llama_cpp"):
    """在库里把启用中的 provider 指向给定地址（等同于用户在设置页保存一次）。"""
    ConfigRegistryService(session).save_providers(
        LlmProviderSaveCommand(
            providers=[LlmProviderWrite(
                id="default", name="测试端点", provider_type=provider_type,
                base_url=base_url, model=model, timeout_seconds=5,
            )],
            active_provider_id="default",
            operator_ref="U1",
        )
    )
    session.commit()


def test_async_task_lane_hits_the_newly_saved_address(db):
    """异步任务 lane（workers/tasks.py 那条）：进程不重启，下一次任务即打到新地址。"""
    session, factory = db
    import app.workers.tasks as tasks

    with ProviderStub(models=("m-saved",),
                      chat_content='{"judgement":"acceptable","basis":"够用"}') as stub:
        _point_at(session, stub.base_url, "m-saved")
        # worker 每次任务现取生效配置：这里换成测试库的 session 工厂，其余链路原样
        original = tasks._SessionFactory
        tasks._SessionFactory = factory
        try:
            judge = build_source_intake_judge(tasks._llm_settings())
            outcome = judge.judge("p1", "一段需求原文", "备注")
        finally:
            tasks._SessionFactory = original
        sent = stub.requests[-1]

    assert outcome.basis == "够用"
    assert sent["body"]["model"] == "m-saved"
    assert sent["path"].endswith("/chat/completions")


def test_interactive_lane_hits_the_newly_saved_address(db):
    """交互式 lane（deps.py 那条）：同样进程不重启即生效——本卡修复的正是这一半。"""
    session, _ = db
    import app.deps as deps

    with ProviderStub(models=("m-interactive",), chat_content="这是解释") as stub:
        _point_at(session, stub.base_url, "m-interactive")
        explainer = build_item_explainer(deps._llm_settings(session))
        answer = explainer.explain({"req_no": "REQ-001"}, {"verdict_summary": "通过"}, "为什么？")
        sent = stub.requests[-1]

    assert answer == "这是解释"
    assert sent["body"]["model"] == "m-interactive"


def test_switching_active_provider_redirects_next_call_without_restart(db):
    """切换启用 provider 后，下一次调用改打另一个端点——两端点都在跑，靠库配置分流。"""
    session, _ = db
    import app.deps as deps

    with ProviderStub(models=("m-first",), chat_content="甲") as first, \
            ProviderStub(models=("m-second",), chat_content="乙") as second:
        _point_at(session, first.base_url, "m-first")
        assert build_item_explainer(deps._llm_settings(session)).explain({}, {}, "?") == "甲"

        _point_at(session, second.base_url, "m-second")  # 用户在设置页改了启用项
        assert build_item_explainer(deps._llm_settings(session)).explain({}, {}, "?") == "乙"

        # 第二次调用没有再打到第一个端点
        assert len([r for r in first.requests if r["method"] == "POST"]) == 1
        assert len([r for r in second.requests if r["method"] == "POST"]) == 1


def test_provider_type_flows_through_to_the_wire(db):
    """provider 类型也随生效配置流到线路上：ollama 收到自己的关思考字段，而非 llama.cpp 的专属扩展。"""
    session, _ = db
    import app.deps as deps

    with ProviderStub(models=("qwen2.5:7b",), chat_content="ok") as stub:
        _point_at(session, stub.base_url, "qwen2.5:7b", provider_type="ollama")
        assert deps._llm_settings(session).llm_provider_type == "ollama"
        build_item_explainer(deps._llm_settings(session)).explain({}, {}, "?")
        assert "chat_template_kwargs" not in stub.requests[-1]["body"]
        assert stub.requests[-1]["body"]["reasoning_effort"] == "none"


def test_clearing_config_falls_back_to_env(db):
    """清空库配置 → 回落 env 兜底（不发起任何网络调用，避免测试打到真实端点）。"""
    session, _ = db
    base = Settings(llm_base_url="http://env-fallback.local/v1", llm_model="env-model")
    _point_at(session, "http://saved.local/v1", "saved-model")
    assert resolve_llm_settings(session, base).llm_base_url == "http://saved.local/v1"

    session.execute(delete(ConfigEntry).where(ConfigEntry.domain == "model_service"))
    session.commit()
    effective = resolve_llm_settings(session, base)
    assert effective is base
    assert effective.llm_base_url == "http://env-fallback.local/v1"
    assert effective.llm_model == "env-model"


def test_config_read_failure_does_not_break_the_request(db):
    """配置面故障不该让功能整个不可用：读取抛错时回落 env。"""
    session, _ = db
    import app.deps as deps

    class Boom:
        def scalar(self, *_a, **_k):
            raise RuntimeError("db down")

    assert deps._llm_settings(Boom()) is deps.settings
