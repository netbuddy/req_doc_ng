"""契约互锁测试：api/openapi.yaml（重构活契约）必须与后端实现一致。

原理＝把活契约里声明的每个操作，与 FastAPI 运行时导出的规范逐项核对：
路径与方法存在、请求体与 200 应答的字段名集合一致、必填集合一致、枚举取值一致。
谁改了代码没改契约（或反之），本测试当场失败——活契约从「文档」升格为「机器约束」。
契约允许是实现的子集（存量接口不在契约里不算错）；核对只针对契约声明的部分。
"""
from pathlib import Path

import pytest
import yaml

from app.main import app

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT = yaml.safe_load((REPO_ROOT / "api" / "openapi.yaml").read_text())
SCHEMAS_DIR = REPO_ROOT / "api"


class _ContractResolver:
    """契约侧解引用：按标准语义，文件引用相对于「引用所在的文件」解析。

    带状态：跟踪当前所在文件（起点＝api/openapi.yaml），跨文件引用后更新；
    同文件引用（#/Key）在当前文件内取。每个待比对结构用一个新实例，
    结构内的属性引用链只要不跨回旧文件即解析正确（现有契约文件满足此前提）。
    """

    def __init__(self) -> None:
        self._file = None  # None＝openapi.yaml 本体；否则为当前 schema 文件路径

    def __call__(self, schema):
        while isinstance(schema, dict) and "$ref" in schema:
            ref = schema["$ref"]
            fp, _, key = ref.partition("#/")
            if fp:
                base = self._file.parent if self._file else SCHEMAS_DIR
                self._file = (base / fp).resolve()
                doc = yaml.safe_load(self._file.read_text())
            else:
                doc = yaml.safe_load(self._file.read_text()) if self._file else CONTRACT
            for part in key.split("/"):
                doc = doc[part]
            schema = doc
        return schema


def _resolve_runtime(schema, components):
    while isinstance(schema, dict) and "$ref" in schema:
        schema = components[schema["$ref"].rsplit("/", 1)[-1]]
    return schema


def _shape(schema, resolve):
    """取对象结构的可比形状：{字段名: 枚举取值集合或 None}＋必填集合。"""
    schema = resolve(schema)
    props, enums = {}, {}
    for name, sub in (schema.get("properties") or {}).items():
        sub = resolve(sub)
        vals = set(sub.get("enum") or [])
        for member in sub.get("anyOf", []):  # 运行时的可空写法 anyOf[X, null]
            member = resolve(member)
            vals |= set(member.get("enum") or [])
        enums[name] = {v for v in vals if v is not None} or None
        props[name] = True
    return props.keys(), set(schema.get("required") or []), enums


RUNTIME = app.openapi()
COMPONENTS = RUNTIME.get("components", {}).get("schemas", {})
SERVER_PREFIX = (CONTRACT.get("servers") or [{}])[0].get("url", "")

CASES = [
    (path, method)
    for path, item in CONTRACT["paths"].items()
    for method in item
    if method in ("get", "post", "put", "delete", "patch")
]


@pytest.mark.parametrize("path,method", CASES)
def test_contract_operation_matches_runtime(path, method):
    runtime_item = RUNTIME["paths"].get(SERVER_PREFIX + path)
    assert runtime_item is not None, f"契约声明的路径未在实现中找到：{SERVER_PREFIX + path}"
    runtime_op = runtime_item.get(method)
    assert runtime_op is not None, f"契约声明的方法未实现：{method.upper()} {path}"
    contract_op = CONTRACT["paths"][path][method]

    pairs = []
    c_body = contract_op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
    r_body = runtime_op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
    if c_body:
        assert r_body is not None, "契约有请求体而实现没有"
        pairs.append(("请求体", c_body, r_body))
    c_resp = contract_op["responses"]["200"]["content"]["application/json"]["schema"]
    r_resp = runtime_op["responses"]["200"]["content"]["application/json"]["schema"]
    pairs.append(("应答", c_resp, r_resp))

    for label, c_schema, r_schema in pairs:
        c_props, c_req, c_enums = _shape(c_schema, _ContractResolver())
        r_props, r_req, r_enums = _shape(r_schema, lambda s: _resolve_runtime(s, COMPONENTS))
        assert set(c_props) == set(r_props), f"{label}字段名不一致：契约 {sorted(c_props)} vs 实现 {sorted(r_props)}"
        assert c_req == r_req, f"{label}必填集合不一致：契约 {sorted(c_req)} vs 实现 {sorted(r_req)}"
        for name, vals in c_enums.items():
            if vals:
                assert r_enums.get(name) == vals, f"{label}字段 {name} 枚举不一致：契约 {sorted(vals)} vs 实现 {sorted(r_enums.get(name) or [])}"
