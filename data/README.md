# PaperExpress 历史记录数据目录

本目录用于存储本地历史记录缓存，请勿将此目录提交到版本控制系统。

## 目录结构

```
data/
├── index.json          # 历史记录索引文件
├── papers/             # 论文速递结果（JSON 文件）
│   └── {hash}.json    # 每条记录一个文件
├── intensive/          # 精读结果（JSON 文件）
│   └── {hash}.json    # 每条记录一个文件
└── agent/              # 研究 Agent 结果（JSON 文件）
    └── {hash}.json    # 每条记录一个文件
```

## 说明

- **papers/**：保存每次论文速递的完整结果（配置 + 论文列表 + 翻译结果）
- **intensive/**：保存每篇论文的精读分析结果
- **agent/**：保存研究 Agent 的查询理解、搜索策略、检索结果和综述
- **index.json**：历史记录的索引文件，用于快速查找

## 缓存命中规则

- **论文速递**：基于 `categories + timeRange + maxPapers` 生成哈希
- **精读**：基于 `arXiv ID + 论文标题` 生成哈希
- **研究 Agent**：基于 `question + timeRange + maxPapers + papersPerQuery + maxSubQueries + model + 日期桶` 生成哈希

## 数据安全

- 目录已在 `.gitignore` 中排除，不会同步到远程仓库
- 如需备份，可手动导出 JSON 文件
