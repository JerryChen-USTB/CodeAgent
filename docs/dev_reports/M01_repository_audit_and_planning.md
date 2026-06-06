# M01 仓库审计、文档阅读与计划基线

## 里程碑目标

完成仓库文档、系统设计和 benchmark 资料审阅，建立实现计划与审核闸门。

## 完成内容

- 阅读 `docs/codex/prompt.md`、课程题目、SRS、设计文档包和 benchmark 目录。
- 创建 `docs/codex/plans.md`，作为后续里程碑唯一事实来源。
- 记录架构基线、风险登记、测试策略、合规矩阵和 benchmark 执行计划。

## 关键文件

- `docs/codex/plans.md`
- `docs/codex/prompt.md`
- `docs/analysis/《基于大语言模型的软件工程智能体》需求规格说明书_v0.1.md`
- `docs/design/`
- `benchmark/`

## 设计决策

- 项目主线锁定为 CLI + LangGraph + LangChain 的 `implement -> test -> debug -> repair` 闭环。
- Python + pytest 是 MVP 主路径；当前 benchmark 中的 unittest 命令通过通用测试命令 runner 兼容。
- benchmark hidden evaluation/oracle 材料不得进入 Agent 可见上下文。

## 验证命令

```powershell
Test-Path docs/codex/plans.md
rg -n "^### M[0-9][0-9]" docs/codex/plans.md
```

## 结果

- `docs/codex/plans.md` 已存在。
- 计划包含 26 个里程碑，满足至少 20 个里程碑要求。

## 已知问题

- M01 完成时发现 `.gitignore` 未忽略本地 secret 文件；该问题进入 M02 处理。

## 对应关系

- 对应 SRS：FR-73 到 FR-77、NFR-19 到 NFR-22。
- 对应设计文档：`00` 总览、`04` LangGraph 工作流、`10` Benchmark 设计。

## 下一步

执行 M02 secret hygiene 与仓库安全预检。
