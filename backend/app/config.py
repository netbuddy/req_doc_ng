"""运行配置。从 backend/.env 加载（若存在），再读环境变量。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# 加载 backend/.env（不覆盖已存在的进程环境变量）。
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


@dataclass(frozen=True)
class Settings:
    service: str = "req-doc-backend"
    version: str = "0.1.0"
    environment: str = os.getenv("APP_ENV", "dev")
    # 默认指向 docker-compose 的 Postgres（db=req_v1, user=req_doc, trust auth）。
    database_url: str = os.getenv(
        "DATABASE_URL", "postgresql+psycopg://req_doc@localhost:5432/req_v1"
    )
    # 外部 LLM（llama.cpp OpenAI 兼容）。未设 LLM_BASE_URL → 用 stub 判定（不调模型）。
    # 例：LLM_BASE_URL=http://192.168.1.50:8080/v1
    llm_base_url: str | None = os.getenv("LLM_BASE_URL")
    # 兼容需要鉴权的 OpenAI 兼容服务（如 dashscope）。本地 llama.cpp 留空即可。
    # 密钥硬边界：只写不回显，绝不进入日志/issue/PR（AGENTS.md 硬规则 8）。
    llm_api_key: str | None = os.getenv("LLM_API_KEY")
    llm_model: str = os.getenv("LLM_MODEL", "qwen2.5")
    # 推理引擎类型（llama_cpp|ollama|vllm|openai_compatible），决定请求体里带哪些非标准扩展字段。
    # env 只给兜底默认；实际取值由设置页保存的启用 provider 经 resolve_llm_settings 覆盖。
    # 键的封闭集与显示名在 app/adapters/llm.py 单点定义（PROVIDER_TYPES）。
    llm_provider_type: str = os.getenv("LLM_PROVIDER_TYPE", "llama_cpp")
    llm_timeout: float = float(os.getenv("LLM_TIMEOUT", "180"))
    # 单次回复的输出上限（token）。注意这是"生成长度上限"，不是上下文窗口；
    # 多轮对话的几十k 输入上下文由服务端 n_ctx 承载，与此无关。
    llm_max_tokens: int = int(os.getenv("LLM_MAX_TOKENS", "131072"))
    # 模型上下文窗口（token，= llama.cpp 每 slot n_ctx）。仅用于"提示词超长"告警阈值：
    # 当 估算输入 token + max_tokens 超过它时告警（提示可能被截断/超上下文）。
    llm_context_tokens: int = int(os.getenv("LLM_CONTEXT_TOKENS", "262144"))
    # 按 lane 预算（链路可观测设计，DS-001 接口文档 2026-07-06 增补）：
    # 命令解释 lane 输出只有几十 token，收紧超时本身就是链路故障检测器；
    # 对话三 lane（起草/解释/重评）输出有界（≤2000 字），同样不该共用长生成预算。
    llm_interpret_timeout: float = float(os.getenv("LLM_INTERPRET_TIMEOUT", "30"))
    llm_interpret_max_tokens: int = int(os.getenv("LLM_INTERPRET_MAX_TOKENS", "512"))
    llm_dialogue_timeout: float = float(os.getenv("LLM_DIALOGUE_TIMEOUT", "60"))
    llm_dialogue_max_tokens: int = int(os.getenv("LLM_DIALOGUE_MAX_TOKENS", "2048"))
    # Qwen3 等推理模型：关掉 thinking（否则 reasoning 吃光 token、content 为空）。
    llm_disable_thinking: bool = os.getenv("LLM_DISABLE_THINKING", "true").lower() in ("1", "true", "yes")
    # 结构化输出（OpenAI 兼容 response_format）三档：auto=从 json_schema 起探测，端点拒绝则
    # 降级 json_object，再降纯提示词模式；也可指定起始档位或 off。注意：指定档位是"起点"非"钉死"，
    # 端点 4xx 拒绝该参数时仍会继续降级（降级只发生一次并缓存在客户端）；未识别值记 WARN 并按
    # prompt_only 生效。三档均记结构化日志（诊断可靠性设计裁定 1，不静默）。
    llm_structured_output: str = os.getenv("LLM_STRUCTURED_OUTPUT", "auto")  # auto|json_schema|json_object|off
    # 启用结构化输出的 lane 白名单（逗号分隔）。item_diagnosis 先行灰度，其余 lane 待回填结论后推广。
    llm_structured_lanes: str = os.getenv("LLM_STRUCTURED_LANES", "item_diagnosis")
    # 能力探测档案（JSON 字符串）：设置页对该端点逐项探测（能否关思考/结构化输出实测生效到哪一档/
    # 有效上下文多大）后「应用」的结果，由 resolve_llm_settings 从启用 provider 的配置投影进来。
    # 空串 = 从未探测，适配层一律回落按 provider 类型的先验默认，行为与探测机制上线前逐字节一致。
    # 形状与解析在 app/adapters/llm.py 单点定义（CapabilityProfile / parse_capability_profile）；
    # env 通常不设，留字段是为了让配置对象成为「档案 → 各 lane 客户端」的唯一通道。
    llm_capability_profile: str = os.getenv("LLM_CAPABILITY_PROFILE", "")
    # 设了 REDIS_URL → RQ 真异步（判定跑在独立 worker）；不设 → inline（同步执行，仍走 AgentRun）。
    redis_url: str | None = os.getenv("REDIS_URL")
    # 全局检索 embedding（OpenAI 兼容 /embeddings 端点）。嵌入模型 ≠ 对话模型，独立配置，不复用 llm_*。
    # 未设 EMBEDDING_BASE_URL → 注入 StubEmbedder：search_index.embedding 全 NULL，检索静默降级纯词法
    # （global-search 工作包 README 不变式 7）。例：EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
    embedding_base_url: str | None = os.getenv("EMBEDDING_BASE_URL")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "text-embedding-v3")
    # 决定 search_index.embedding 的 Vector(dim)；换模型改维度需新迁移 + 全量重嵌（06 §6 风险）。
    # 默认 1024 对齐 dashscope text-embedding-v3 及常见本地嵌入服务输出维度（迁移里同为 1024）。
    embedding_dim: int = int(os.getenv("EMBEDDING_DIM", "1024"))
    # 需鉴权嵌入服务（dashscope）用；本地留空。密钥硬边界：只写不回显，绝不进日志/issue/PR（硬规则 8）。
    embedding_api_key: str | None = os.getenv("EMBEDDING_API_KEY")
    embedding_timeout: float = float(os.getenv("EMBEDDING_TIMEOUT", "30"))
    # 前端生产产物（Vite dist）目录。离线部署由 api 容器同源服务前端，不另起前端容器
    # （离线发布打包方案 §3.1）。留空 → 不挂载静态文件，只提供 /api（本地开发形态：前端走 vite dev）。
    frontend_dist_dir: str = os.getenv("FRONTEND_DIST", "")
    # 候选 docx 导出件落盘目录（SCN-005-P03；相对 backend/）。
    export_dir: str = os.getenv(
        "EXPORT_DIR", str(Path(__file__).resolve().parent.parent / "var" / "exports")
    )
    # 精确预览（docx→PDF）用的 LibreOffice 可执行文件路径；留空则自动探测 soffice/libreoffice。
    soffice_path: str = os.getenv("SOFFICE_PATH", "")
    # docx→PDF 单次转换超时（秒）。
    pdf_render_timeout: float = float(os.getenv("PDF_RENDER_TIMEOUT", "120"))
    # 图形源码（mermaid/plantuml）本地栅格化：全部落地，运行时不出网、不把需求内容送第三方。
    # 留空则自动探测：mmdc 走 PATH（@mermaid-js/mermaid-cli），java 走 PATH。
    mmdc_path: str = os.getenv("MMDC_PATH", "")
    java_path: str = os.getenv("JAVA_PATH", "")
    # plantuml.jar 与 mmdc 的 puppeteer 配置（系统 chrome + --no-sandbox）默认落 backend/tools/。
    # 该目录入版本库（不同于被 .gitignore 排除的 backend/var/）：这两个文件是渲染功能的构件，
    # 不入库则换机器克隆构建出的镜像必然缺图形能力且无报错（离线发布打包方案 §3.4）。
    plantuml_jar_path: str = os.getenv(
        "PLANTUML_JAR", str(Path(__file__).resolve().parent.parent / "tools" / "plantuml.jar")
    )
    puppeteer_config_path: str = os.getenv(
        "PUPPETEER_CONFIG", str(Path(__file__).resolve().parent.parent / "tools" / "puppeteer.json")
    )
    # 单张图形栅格化超时（秒）。
    diagram_render_timeout: float = float(os.getenv("DIAGRAM_RENDER_TIMEOUT", "60"))


settings = Settings()

# 演示用固定项目 id（持久化后 project_ref 必须是真实 UUID）。
DEMO_PROJECT_ID = "00000000-0000-0000-0000-000000000001"
