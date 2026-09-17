"""验证脚本的机器检查：任务定义 JSON Schema：用 Schema 文件核对各份任务定义文件。"""

from __future__ import annotations

from tod_kernel.verify.base import Checker, TASK_DEFS_DIR, banner
from tod_kernel.verify.intake import BAD_DEFINITIONS


# 三份写坏的定义里属于结构错误、应当被 JSON Schema 拦下的两份；「引用不存在的槽位」是语义错误，schema 管不了，由加载器拦。
SCHEMA_STRUCTURAL = {"缺顶层键", "步骤只写重复直到不写最多"}
SCHEMA_FILE = "任务定义.schema.json"


def schema_checks() -> Checker:
    """第三步第九项：任务定义文件的 JSON Schema。现存三份好文件通过；三份写坏的定义里结构错误被拦下，语义错误放行。
    用本机的 jsonschema 库；没装时这组不做断言，打印提示。"""
    import json as json_module

    title = "第三步·任务定义 JSON Schema"
    banner(title)
    c = Checker(title)
    try:
        import jsonschema
    except ImportError:
        print("提示：本机没有安装 jsonschema，这组断言跳过（pip install jsonschema 后重跑）")
        return c
    print("── 断言 ──")
    schema = json_module.loads((TASK_DEFS_DIR / SCHEMA_FILE).read_text(encoding="utf-8"))
    schema_error = None
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError as exc:
        schema_error = exc
    c.check("schema 文件本身是合法的 JSON Schema 2020-12", schema_error is None, repr(schema_error))
    if schema_error is not None:
        return c
    validator = jsonschema.Draft202012Validator(schema)
    # 对话模式文件 patterns.json 不是任务定义，格式由加载器的 load_patterns 校验
    good = sorted(path for path in TASK_DEFS_DIR.glob("*.json") if path.name not in (SCHEMA_FILE, "patterns.json"))
    for path in good:
        errors = [e.message for e in validator.iter_errors(json_module.loads(path.read_text(encoding="utf-8")))]
        c.check(f"好文件 {path.name} 通过 schema", not errors, errors)
    c.check("好文件恰好三份（材料接入登记、它的目标写错样例、术语澄清）", len(good) == 3, [path.name for path in good])
    for bad_title, source, breaker, _, _ in BAD_DEFINITIONS:
        definition = json_module.loads((TASK_DEFS_DIR / source).read_text(encoding="utf-8"))
        breaker(definition)
        errors = [e.message for e in validator.iter_errors(definition)]
        if bad_title in SCHEMA_STRUCTURAL:
            c.check(f"写坏的定义「{bad_title}」是结构错误，被 schema 拦下", bool(errors), errors)
        else:
            c.check(f"写坏的定义「{bad_title}」是语义错误，schema 放行、由加载器拦下", not errors, errors)
    return c
