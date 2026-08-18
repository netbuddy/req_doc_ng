# 提示词模板机制（要素链路）

单文件编写、双消息发送：每个用途一个 `.jinja2` 模板，内含 `{% block system %}` 与
`{% block user %}`；`environment.render_pair(name, **vars)` 渲染出 (system, user) 消息对。

## 规约

1. **动态单一来源**：枚举清单/裁定语义 ← `app/domain/labels.py`；判据状态字 ←
   `app/domain/rubrics`；输出 JSON 形状 ← `app/adapters/llm.py` 中与解析器同文件的
   `_*_OUTPUT*` 常量。模板内禁止手写这些内容（渲染测试
   `tests/test_element_prompt_templates.py` 与 `tests/test_prompt_templates.py`
   覆盖率兜底）。
2. **缓存友好分块**：system 块只放部署期稳定内容（角色/规则/类型清单/输出契约），
   逐字节稳定以命中推理侧前缀缓存；每次变化的语料（原文+补块、目标、修订稿、意图、
   判据文本）一律放 user 块。
3. 共享段放 `partials/`（类型清单、来源依据规则、v5 结论对象规则）；模板头注释声明
   依赖变量清单。
4. `StrictUndefined`：漏传变量渲染即错；改模板后先跑渲染测试。
5. **显式拒绝通道**：模型可能无法合规完成的用途，输出契约须有 status=cannot_comply
   （或该 lane 的失败/无法判断语义）+ 一句可展示给用户的中文 reason，禁止空数组静默失败。

## 模板清单

| 模板 | 用途 | 调用方 |
|---|---|---|
| source_intake | AEP-001 来源接入判断 | LlmSourceIntakeJudge |
| element_recognition | AEP-022 首次识别 | LlmSourceElementRecognizer |
| element_review | AEP-023 复核（对话轮次） | LlmElementReviewer.review_elements |
| element_scan | P03 扫原文补漏 | LlmElementReviewer.scan_missing |
| element_execution | AEP-025 指定操作执行/修订迭代 | LlmElementOperationExecutor |
| item_formation | AEP-007 条目格式化建议（v3 注入五类条目陈述档案 `domain/item_profiles/`，输出档案结构判定） | LlmRequirementItemFormatter |
| item_diagnosis | AEP-032 条目诊断（v5 结论对象） | LlmRequirementItemDiagnoser |
| item_reeval | AEP-095 轻量重评（改判唯一通道） | LlmItemReevalResponder |
| item_draft | AEP-095 修订草案起草 | LlmItemDraftComposer |
| item_source_candidates | issue #30 为条目找候选来源（cannot_comply 拒绝通道；候选只引用输入差集要素 id） | LlmItemSourceCandidateComposer |
| item_explain | AEP-095 解释问答（纯文本） | LlmItemExplainer |
| chart_suggestion | SCN-004-P01-N08 图表源码建议 | LlmChartSourceSuggester |
| chart_verification | SCN-004-P02-N03 图文一致性核对 | LlmChartVerifier |
| element_command | AEP-096 区5 对话命令解释（知识抽取页；命令表 ← `domain/chat_commands.py`） | LlmElementCommandInterpreter |
| item_command | AEP-095 斜杠命令解释（条目评审页；命令表 ← `domain/chat_commands.py`） | LlmItemCommandInterpreter |
| formation_command | AEP-097 斜杠命令解释（条目形成页；命令表 ← `domain/chat_commands.py`） | LlmFormationCommandInterpreter |
| section_manuscript_draft | AEP-110 章节撰稿 AI 起草初稿（撰稿阶段预填；examples 少样本 + 关联确认态资产事实输入；发布渲染仍确定性） | LlmSectionManuscriptDrafter |
