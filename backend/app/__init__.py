"""需求治理平台后端（v0.1）。

设计事实源：docs/40-detailed-design/。本包实现 SCN-001-P01 文本接入切片：
- domain/   领域枚举、状态机、错误
- services/ 材料接收服务（AEP-001/002）+ 端口
- repositories/ 端口的 in-memory 适配（持久化=后续增量，经端口替换）
- api/      FastAPI 路由（/api）、Pydantic DTO
"""
