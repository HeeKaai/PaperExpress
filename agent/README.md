# Research Agent 设计说明

本目录实现 PaperExpress 的研究型 Agent 模块。它面向“复杂研究问题”的自动化检索与综述，例如“大模型在缺陷修复领域的最新进展”，在不影响原论文速递、趋势速览和精读流程的前提下，提供独立的查询理解、多步检索、知识提炼和质量评估能力。

## 目标

- 将自然语言研究问题拆解为可执行的英文 arXiv 检索策略
- 自动扩展同义词、研究角度、任务范围和排除范围
- 通过工具接口执行多子查询检索，并对结果去重排序
- 基于代表论文生成中文综述、趋势、方法路线、局限和后续建议
- 记录 Agent 执行轨迹，便于用户理解“为什么这样搜、搜到了什么、哪里证据不足”

## 模块结构

```
agent/
├── __init__.py
├── research_agent.py   # 研究 Agent 主流程
├── tooling.py          # 轻量工具注册与执行轨迹
└── README.md           # 当前说明文档
```

## 核心流程

当前 Agent 使用一轮自动执行链路：

```text
用户问题
  -> Planner: 查询理解与意图识别
  -> Sub-query Generation: 生成多角度英文 arXiv 子查询
  -> Tool Registry: 注册可执行工具
  -> arXiv Search: 多子查询检索，必要时宽松重试
  -> Deduplicate & Rank: 去重、相关性排序、新近性排序
  -> Synthesis: LLM 中文综述与知识提炼
  -> Reflection: 确定性质量评估
  -> Trajectory: 返回完整执行轨迹
```

### 1. Planner

`research_agent.py` 中的 `build_planner_prompt()` 会要求 LLM 输出严格 JSON，包含：

- `intent`：研究主题、任务领域、关键实体、同义词、时间倾向、纳入/排除范围
- `strategy`：搜索策略说明和搜索角度
- `sourcePriorities`：数据源优先级
- `subQueries`：4-6 个英文 arXiv 查询

`normalize_planner_result()` 会对 LLM 输出做容错清洗。如果 LLM 返回字段不稳定，会尝试从常见容器字段中提取可执行查询；仍不足时使用 fallback query 生成逻辑，避免因格式轻微偏差直接失败。

### 2. Tool Registry

`tooling.py` 提供三个轻量对象：

- `AgentTool`：封装一个可执行工具
- `ToolRegistry`：按名称注册和调用工具
- `AgentTrajectory`：记录 Agent 的 task、thought、plan、action、observation、reflection 等步骤

当前注册的真实工具是：

- `arxiv_search`：复用 `server.py` 中现有 arXiv 获取逻辑，按分类、时间范围和英文关键词检索论文

Semantic Scholar、OpenAlex 暂时作为规划中的数据源展示在 `sourcePriorities` 中，后续可按同一工具接口扩展为真实工具。

### 3. 多步检索与宽松重试

`run_arxiv_searches()` 会依次执行 Planner 生成的子查询。每次工具调用都会写入 `trajectory`：

- `action`：调用了什么工具、输入了什么查询
- `observation`：命中多少论文、是否失败或触发警告

如果某个精确查询命中 0 篇，Agent 会通过 `relaxed_query_candidates()` 自动生成更宽松的查询版本，例如减少强限定短语、删除过窄词项或回退到领域关键词。宽松重试命中后会写入：

- `subQuery.relaxedQuery`
- `subQuery.relaxationNote`

前端会展示该子查询的实际重试情况。

### 4. 去重与排序

`dedupe_and_rank()` 会合并所有子查询结果，按 arXiv ID / 链接 / 标题去重。排序分数由 `score_paper()` 计算，综合：

- 子查询关键词在标题和摘要中的匹配度
- 同一论文命中的查询角度数量
- 发布时间的新近性

最终只返回 `maxPapers` 篇代表论文，避免 synthesis prompt 过大。

### 5. 智能综述

`build_synthesis_prompt()` 将 Top papers 压缩为轻量 JSON 后交给 LLM，总结为：

- `overview`：中文总体综述
- `keyTrends`：关键趋势与证据编号
- `methodMap`：方法路线与证据编号
- `representativePapers`：代表论文及理由
- `limitations`：局限与证据边界
- `futureDirections`：后续研究或检索建议

若最终论文为空，Agent 不会强行生成综述，而是返回 `empty_synthesis()`，明确提示证据不足。

### 6. Reflection 质量评估

`evaluate_agent_result()` 是确定性评估步骤，不额外调用 LLM，因此不会增加 token 开销。它会根据以下因素生成质量判断：

- 最终论文数量
- 空命中子查询数量
- 宽松重试次数
- 检索错误数量
- 趋势/方法结论是否包含证据编号

返回字段包括：

- `score`：0-100 分
- `coverageLevel` / `coverageLabel`：覆盖状态
- `issues`：主要风险
- `recommendations`：后续优化建议

### 7. Trajectory 执行轨迹

每次运行都会返回 `trajectory`，用于前端“执行轨迹”区域和 Markdown 导出。典型步骤包括：

- 接收研究问题
- 调用 Planner
- 形成多步搜索策略
- 注册检索工具
- 调用 arXiv 工具
- 观察检索结果
- 去重排序
- 生成智能综述
- 评估结果质量

这使当前模块不仅输出结果，也能解释执行过程。

## 后端集成

`server.py` 暴露独立接口：

```text
POST /api/agent/research
```

请求字段：

- `question`：研究问题
- `timeRange`：时间范围，默认 180 天
- `maxPapers`：最终代表论文数
- `papersPerQuery`：每个子查询最多拉取论文数
- `maxSubQueries`：最大子查询数量
- `llm`：OpenAI-compatible LLM 配置

响应字段：

- `intent`
- `strategy`
- `subQueries`
- `sourcePriorities`
- `papers`
- `synthesis`
- `evaluation`
- `trajectory`
- `stats`
- `cached`

缓存写入 `data/agent/`，缓存键包含问题、时间范围、论文数、子查询数、模型、日期桶和 `AGENT_PROMPT_VERSION`，避免旧提示词或旧数据结构误命中。

## 当前边界

- 当前只真实执行 arXiv 检索，其他数据源暂为可扩展计划
- 当前是单轮自动链路，不包含多轮 ReAct 自我修正循环
- Reflection 是确定性评估，不会替代人工学术判断
- 检索结果受 arXiv API 可用性、时间范围和查询表达影响

## 后续可扩展方向

- 引入 ReAct-lite：当评分过低或空查询过多时自动追加一轮补充检索
- 接入 Semantic Scholar / OpenAlex 工具，补充引用、影响力和跨出版源结果
- 将 `trajectory` 扩展为可恢复执行状态，支持前端实时流式展示
- 增加领域模板，例如软件工程、医学、材料、机器人等不同检索策略
