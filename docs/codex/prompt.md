# CodeAgent 长程开发提示词

你是 Codex，作为一名资深 Staff Engineer、架构负责人、测试负责人。你的任务是在当前仓库中，从现有需求规格说明书、系统设计文档和 benchmark 初步设计出发，连续数小时专注完成基于大语言模型的软件工程智能体项目 CodeAgent 的成熟实现。

本项目不是普通代码生成器，而是一个面向软件工程任务的智能体运行时。目标系统必须通过 CLI 启动，基于 LangGraph + LangChain 编排“实现 → 测试 → 调试 → 修复”四个连续阶段，调用大语言模型和本地工具，围绕项目仓库完成读取需求、规划实现、生成/修改代码、生成测试方案、执行测试、分析失败日志、定位根因、生成修复补丁、回归验证、输出全过程报告的闭环。

你将长时间运行：先完整阅读、审计、规划，再按里程碑逐步实现、验证、修复、记录。不要跳过规划阶段。不要只搭脚手架后停止。不要在没有真实运行测试的情况下声称完成。

---

## 0. 最高优先级原则

1. **先读文档，再写代码。** 当前项目已经有详细 SRS、系统设计文档和 benchmark 设计；它们是行为准则，不是可选参考。实现必须以文档为基线。
2. **先创建 `plans.md`，再编码。** 在任何代码修改前，必须创建并提交/保存一份高质量 `plans.md`。
3. **至少 20 个里程碑。** `plans.md` 必须包含不少于 20 个里程碑，预计总耗时数小时。每个里程碑必须包含：范围、关键文件/模块、验收标准、验证命令、测试要求、风险和回滚/修复策略。
4. **每个小模块都要自测。** 每完成一个小模块，必须自行设计单元测试、运行测试、修复失败，确保健壮性和高完成度。
5. **实现高质量工程，而不是演示型假实现。** CLI、工作流、模型配置、工具、HITL、日志、报告、benchmark 都必须可运行、可复现、可检查。
6. **持续回顾文档。** 每完成 2–3 个里程碑，回看 SRS 和系统设计，检查实现是否偏离；在 `plans.md` 中追加状态、偏差、修复计划和已完成验证。
7. **真实验证。** 所有声明必须有命令、日志或报告支撑。不得说“应该通过”；必须运行并记录结果。
8. **不要泄露密钥。** 项目根目录有 `Software Engineering Project.txt`，里面保存 OpenRouter API Key。只把它当作本地秘密读取，不要打印、不要写入日志、不要提交、不要复制到报告。

---

## 1. 必须首先阅读的资料

在写任何代码前，按以下顺序阅读并摘要到 `plans.md` 的“Document Review”部分：

1. 课程题目文档：`题目设计.md`
2. 需求规格说明书：`docs/analysis/《基于大语言模型的软件工程智能体》需求规格说明书_v0.1.md`
3. 系统设计文档包：
   - `docs/design/README_设计文档包索引.md`
   - `docs/design/00_系统设计方案总览.md`
   - `docs/design/01_系统架构设计.md`
   - `docs/design/02_模块划分与职责设计.md`
   - `docs/design/03_数据流与状态模型设计.md`
   - `docs/design/04_LangGraph工作流设计.md`（最核心，必须重点阅读）
   - `docs/design/05_核心类与接口设计.md`
   - `docs/design/06_工具调用与HITL设计.md`
   - `docs/design/07_错误处理与重试设计.md`
   - `docs/design/08_关键技术选型与配置设计.md`
   - `docs/design/09_运行产物与可复现设计.md`
   - `docs/design/10_Benchmark与扩展预留设计.md`
4. Benchmark 设计与案例：
   - `benchmark/README.md`
   - `benchmark/benchmark.yaml`（如果存在）
   - `benchmark/cases/**`
   - `benchmark/selfbuilt/README.md`
   - `benchmark/selfbuilt/selfbuilt_benchmark.yaml`（如果存在）
   - `benchmark/selfbuilt/cases/**`
   - `docs/test/**` 中所有 benchmark 相关报告。
5. 官方文档（实现 LangChain / LangGraph 前必须参考）：
   - LangChain 官方文档：https://docs.langchain.com/oss/python/langchain/overview
   - LangGraph 官方文档：https://docs.langchain.com/oss/python/langgraph/overview
   - 需要实现 interrupt、streaming、checkpoint、StateGraph、tools、structured output、Human-in-the-loop 时，继续查阅对应子页面；不要凭旧记忆猜 API。

`plans.md` 必须记录：读到的核心约束、范围内/范围外、P0/P1 功能、模块映射、主要风险、文档间冲突和你的解决策略。

---

## 2. 目标系统范围

实现一个本地可运行的 Python CLI 软件工程智能体，覆盖：

```text
实现 → 测试 → 调试 → 修复
```

系统必须支持：

1. CLI 启动：
   - `codeagent --help`
   - `codeagent wizard`
   - `codeagent run --config task.yaml`
   - `codeagent implement --project ./repo --requirements requirements.md`
   - `codeagent test --project ./repo --test-cmd "pytest -q"`
   - `codeagent debug --project ./repo --test-cmd "pytest -q" --log failing.log`
   - `codeagent repair --project ./repo --test-cmd "pytest -q"`
   - `codeagent benchmark --config benchmark.yaml`
   - `codeagent resume --run-id <run_id>`
2. 三种运行模式：半交互式 wizard、非交互式命令、配置文件模式。
3. 阶段选择与校验：允许单阶段或连续阶段组合；拒绝不连续或顺序错误组合。
4. LangGraph 主图 + 四个阶段子图：ImplementationSubgraph、TestingSubgraph、DebuggingSubgraph、RepairSubgraph。
5. LangChain 模型与工具层：统一模型调用、工具注册、结构化输出和工具级 HITL。
6. OpenRouter 模型调用：使用 `anthropic/claude-opus-4.8` 驱动本项目的软件工程智能体。
7. 工具系统：项目扫描、文件读取、代码搜索、日志读取、patch 生成/校验/应用、shell/pytest 执行、pytest 结果解析、报告写入、artifact 记录。
8. patch-first：所有项目源码和测试文件修改必须先生成 unified diff，审批后才能应用。
9. HITL：测试方案、实现 patch、测试 patch、修复 patch、测试/复现/回归命令执行都必须能人工审批；benchmark 模式可自动审批，但必须记录 decision trace。
10. SQLite checkpoint：支持中断、resume、run_id/thread_id 关联。
11. streaming 进度展示：CLI 实时显示当前阶段、当前节点、工具调用摘要、测试结果、下一步。
12. 日志和产物：metadata、task_config、transcript、decision_trace、stage_result、阶段报告、最终报告、patch、changed_files、benchmark 结果。
13. Benchmark：支持公共数据集案例和 5 个自建 benchmark 案例，统计成功率并输出报告。

---

## 3. 技术栈硬性要求

优先遵循设计文档中的技术选型：

- Python 3.11+
- LangGraph
- LangChain
- `langchain-openai` 或当前官方推荐的 OpenAI-compatible 接入方式
- OpenRouter OpenAI-compatible API
- 模型：`anthropic/claude-opus-4.8`
- pytest
- SQLite checkpoint（优先使用 LangGraph SQLite checkpointer；如官方 API 有变化，按最新文档适配并记录）
- Typer + Rich，或 argparse + Rich
- Pydantic
- YAML/JSON 配置
- Markdown + JSON/JSONL 报告

建议但不强制：

- ruff / mypy / pyright，用于 lint 和类型检查
- coverage，用于测试覆盖率辅助
- python-dotenv，用于本地环境变量加载，但不得把密钥提交

不得使用：

- MetaGPT、ChatDev、AutoGPT 等已有软件工程智能体系统作为基础进行封装。
- 直接把 API Key 写入源码、配置模板、README、日志或报告。
- 让被评测 Agent 读取 hidden oracle tests、expected answers 或 evaluation 目录内容。
- 为通过 benchmark 硬编码答案、删除测试、跳过断言、特判样例。
- 未经审批执行危险 shell 命令，如 `rm -rf`、`sudo`、`curl | sh`、任意网络上传下载、修改系统路径等。

---

## 4. OpenRouter API Key 和模型接入要求

项目根目录存在：

```text
Software Engineering Project.txt
```

这个文件保存 OpenRouter API Key。你必须这样处理：

1. 本地运行和验证时，可以读取该文件获取 API Key。
2. 不要在任何输出中打印完整 key。
3. 不要把 key 写入 `plans.md`、README、报告、metadata、transcript、测试快照或任何 commit。
4. 确保 `.gitignore` 包含：
   - `Software Engineering Project.txt`
   - `.env`
   - `.env.*`
   - 任何本地 secret 文件。
5. 实现时优先支持环境变量：
   - `OPENROUTER_API_KEY`
6. 如果环境变量缺失，可以在本地开发运行路径中安全读取根目录 `Software Engineering Project.txt`，但只在内存中使用。
7. 模型配置默认值应为：

```yaml
model:
  provider: openai_compatible
  model_name: anthropic/claude-opus-4.8
  base_url: https://openrouter.ai/api/v1
  api_key_env: OPENROUTER_API_KEY
  temperature: 0.2
  timeout_seconds: 120
  max_retries: 2
```

8. 如果 LangChain 当前版本的 OpenAI-compatible 初始化方式变化，必须查官方文档适配，并在 README / `plans.md` 中记录最终依赖版本和初始化方式。

---

## 5. `plans.md` 的强制结构

在编码前创建 `plans.md`，不得省略。`plans.md` 至少包含以下章节：

```markdown
# CodeAgent Implementation Plan

## 1. Document Review
- 已阅读文件清单
- 核心需求摘要
- 设计基线摘要
- P0/P1 优先级摘要
- 文档间冲突与处理策略

## 2. Architecture Baseline
- 系统层次
- LangGraph 主图与四阶段子图
- LangChain 模型/工具层
- patch-first + HITL
- SQLite checkpoint/resume
- 日志与报告
- Benchmark runner

## 3. Milestone Plan
至少 20 个里程碑。每个里程碑包含：
- Scope
- Key files/modules
- Acceptance criteria
- Verification commands
- Unit/integration tests to add
- Risks and mitigations
- Status: pending / in-progress / done / blocked

## 4. Risk Register
- LangGraph API 变化
- LangChain/OpenRouter 接入
- patch 应用可靠性
- HITL/resume 幂等性
- pytest 日志解析
- benchmark 隔离与 hidden tests
- 长上下文与日志截断
- API Key 泄露风险
- BugsInPy 环境复杂度

## 5. Test Strategy
- 单元测试策略
- 集成测试策略
- CLI 测试策略
- LangGraph 节点测试策略
- Benchmark 测试顺序

## 6. Compliance Matrix
- SRS FR/NFR 到模块和测试的映射
- 课程要求到实现产物的映射

## 7. Running Notes
每完成一个小模块或里程碑，追加：
- 完成内容
- 运行命令
- 结果摘要
- 失败与修复
- 文档是否需要调整
- 下一步
```

`plans.md` 中的里程碑不得少于 20 个。建议覆盖但不限于以下 24 个主题：

1. 仓库审计、文档阅读、需求追踪矩阵。
2. Python 包结构、`pyproject.toml`、依赖锁定、基础测试框架。
3. CLI 基础命令和 `--help`。
4. Pydantic 配置模型：TaskConfig、BenchmarkConfig、ModelConfig、RuntimeConfig。
5. run_id、输出目录、metadata、artifact index、报告目录结构。
6. 安全 secret 处理、OpenRouter/Claude 模型接入、模型工厂。
7. 项目扫描、敏感文件跳过、文件读取、代码搜索工具。
8. ShellRunner、pytest 执行、超时、stdout/stderr 保存。
9. pytest 结果解析器和测试报告 JSON/Markdown。
10. PatchService：生成、校验、摘要、应用 unified diff。
11. ToolRegistry、ToolResult、权限策略和工具级 HITL 兜底。
12. AgentState、核心数据对象、stage_result schema。
13. LangGraph 主图骨架、阶段路由、decision_trace。
14. SQLite checkpoint、interrupt/resume 基础能力。
15. ImplementationSubgraph：需求解析、计划、代码 patch、语法检查、实现报告。
16. TestingSubgraph：测试目标、测试方案、方案审批、测试 patch、命令审批、测试执行。
17. DebuggingSubgraph：失败日志读取、复现、源码搜索、fault localization、root cause、repair_plan。
18. RepairSubgraph：修复计划、风险检查、patch 审批、回归验证、多轮修复闭环。
19. CLI wizard 与审批 UI，Rich 进度展示和 streaming events。
20. 非交互式 `run`、阶段子命令、配置文件模式。
21. BenchmarkRunner、CaseLoader、SuccessEvaluator、MetricAggregator。
22. HumanEval/MBPP 与 QuixBugs benchmark 适配和通过。
23. BugsInPy 准备脚本、环境检测、运行适配和通过。
24. 5 个自建 benchmark 案例运行、失败迭代、最终报告、README 和演示材料。

---

## 6. 实现流程要求

严格按以下流程工作：

1. **Planning first**
   - 阅读文档。
   - 创建 `plans.md`。
   - 写出至少 20 个里程碑。
   - 写出风险登记表、架构摘要、测试策略、合规矩阵。
   - 在 `plans.md` 还不完整时，不要写业务代码。

2. **Scaffold second**
   - 建立最小 Python 包结构。
   - 配置依赖、测试、CLI 入口。
   - 让 `codeagent --help`、`pytest` 的最小版本能运行。

3. **Implement milestone by milestone**
   - 每次只实现一个清晰范围。
   - 保持 diff 可审阅。
   - 每个模块都写单元测试。
   - 每完成一个小模块就运行相关测试。
   - 每完成一个里程碑就运行更广泛测试，并更新 `plans.md`。
   - 如果环境支持 git，里程碑完成后做清晰 commit；不得提交密钥、大型 benchmark 输出或临时环境文件。

4. **Verify and repair continuously**
   - 测试失败必须立刻分析日志并修复。
   - 类型错误、lint 错误、CLI 错误、checkpoint/resume 错误都要修复。
   - 不要留下“后续再修”的 P0/P1 缺口。

5. **Document as you go**
   - README 记录安装、配置、API Key 使用方式、CLI 示例、benchmark 运行方式。
   - 阶段报告模板、benchmark 报告模板、输出目录结构必须和实现一致。
   - `plans.md` 是持续更新的工程日志。

---

## 7. 文档修改规则

已有文档是基线，但实现过程中可以发现不一致、过时或不可实现之处。你可以适应性修改文档，但必须遵守：

1. **先备份再修改。** 修改任何已有文档前，先复制到：

```text
docs/_backups/<YYYYMMDD_HHMMSS>/<original_relative_path>
```

2. **标注版本号。** 修改后的文档必须更新版本号或变更记录。例如：
   - SRS 从 `v0.1` 更新到 `v0.2-implementation-aligned`
   - 设计文档从 `v1.0` 更新到 `v1.1-implementation-aligned`
3. **写清变更原因。** 在文档末尾增加“实现对齐变更记录”，说明：
   - 修改了什么
   - 为什么修改
   - 对实现和测试的影响
   - 是否改变课程验收范围
4. **不要为了偷懒降低需求。** 文档修改只能用于对齐实现细节、修正错误、补充版本/API变化；不能把未完成的 P0 功能改成“不做”。
5. **文档冲突处理。** 如果 SRS、系统设计、benchmark 设计互相冲突：
   - 优先遵循课程题目和 SRS 的 P0 目标。
   - 对具体架构实现，优先遵循最新系统设计文档。
   - 在 `plans.md` 记录冲突、选择、理由和后续影响。

---

## 8. 关键功能验收要求

实现完成时，至少应满足：

1. `codeagent --help` 显示所有主要命令、参数和示例。
2. `codeagent wizard` 能完成半交互式任务配置。
3. `codeagent run --config task.yaml` 能执行配置文件任务。
4. 阶段组合校验正确：支持单阶段和连续组合；拒绝不连续组合。
5. 每次运行创建独立 `codeagent_runs/<run_id>/`。
6. 输出目录包含：
   - `metadata.json`
   - `task_config.yaml`
   - `transcript.jsonl`
   - `decision_trace.jsonl`
   - `artifacts_index.json`
   - `implementation/`
   - `testing/`
   - `debugging/`
   - `repair/`
   - `logs/`
   - `patches/`
   - `final_report.md`
7. 实现阶段能读取需求/设计材料和项目骨架，生成实现计划、patch、变更清单、实现报告。
8. 测试阶段必须先生成 `test_plan.md`，审批后再生成测试文件，审批后再执行 pytest。
9. 调试阶段能读取失败日志和测试报告，定位失败测试、搜索源码、输出 fault localization、root cause、repair plan。
10. 修复阶段能生成最小修复 patch，审批后应用，重新运行 pytest；失败时最多迭代默认 3 次。
11. 所有有副作用操作必须可审批、可拒绝、可记录。
12. checkpoint/resume 至少能恢复到 interrupt 审批点，或者在不可恢复时输出清晰原因和已有产物。
13. benchmark 能批量运行案例、隔离输出目录、统计成功率、汇总失败原因。
14. 不把隐藏 oracle tests 暴露给被评测 Agent。
15. 不泄露 API Key。

---

## 9. 单元测试与质量要求

开发时每完成一个小模块，必须写测试并运行。

最低测试覆盖范围：

1. 配置解析：合法/非法 stages、缺失必选字段、路径校验、默认值。
2. 阶段连续性：合法组合和非法组合。
3. 输出目录初始化：run_id 唯一、目录结构完整、不覆盖已有产物。
4. Project scanner：识别源码/测试目录、跳过敏感文件。
5. 文件工具：读取、搜索、截断、错误路径。
6. PatchService：生成/解析/校验/应用 unified diff；越权路径和敏感文件拒绝。
7. ShellRunner：命令白名单、超时、stdout/stderr、退出码。
8. Pytest parser：passed/failed/errors、失败测试名、异常摘要。
9. Tool permission policy：allow/ask/deny 分类。
10. HITL 数据结构：approve/edit/reject/respond/cancel。
11. LangGraph 路由：测试通过跳过调试修复；测试失败进入调试；修复失败循环；达到最大次数失败。
12. Report writer：stage_result、final_report、artifact index。
13. Benchmark evaluator：成功条件、失败原因、分类统计。
14. CLI：`--help`、配置文件模式、常见错误提示。

每个里程碑验证命令必须写进 `plans.md`。推荐维护并不断扩展：

```bash
python -m pytest -q
python -m pytest tests/unit -q
python -m pytest tests/integration -q
codeagent --help
codeagent run --config examples/task.yaml
codeagent benchmark --config benchmark/benchmark.yaml
```

如果添加 ruff/mypy/pyright：

```bash
ruff check .
python -m mypy src tests
```

不要在测试未运行时声称通过。

---

## 10. Benchmark 要求与顺序

系统基本可运行后，必须按以下顺序进行 benchmark，并在每轮失败后迭代修复，直到所有可运行测试全部通过。

### 10.1 通用数据集顺序

1. **HumanEval / MBPP**
   - 先运行仓库中已有 HumanEval/MBPP 相关 case。
   - 验证实现 + 测试能力。
   - 修复所有失败。
2. **QuixBugs**
   - 再运行 QuixBugs 相关 case。
   - 验证调试 + 修复能力。
   - 修复所有失败。
3. **BugsInPy**
   - 最后运行 BugsInPy 相关 case。
   - 如果环境需要 WSL/conda/外部准备脚本，先实现环境检测、准备脚本调用、清晰错误提示和运行说明。
   - 如果当前机器缺少必要环境，不要静默跳过；要把缺失项作为 blocker 记录在 `plans.md`、README 和 benchmark 报告中，并尽可能完善自动准备脚本。

### 10.2 自建 benchmark

通用数据集全部通过后，运行 5 个自建 Benchmark 案例：

1. `01_todo_manager`
2. `02_personal_ledger`
3. `03_student_gradebook`
4. `04_library_lending`
5. `05_meeting_room_booking`

要求：

- 每个 case 从干净 workspace 副本开始。
- benchmark runner 必须把原始 case 目录视为只读模板；每次运行先复制整个 case 到本次干净 run workspace，Agent、patch、依赖安装、测试和日志都只作用于副本，成功/失败/超时/中断都不得回写原始 case。
- `input/` 是 Agent 可见上下文。
- `oracle_tests/` 只由 benchmark runner 用于评分，不能提供给 Agent。
- 每个 case 独立 run_dir。
- 每个 case 保存日志、patch、报告、成功/失败原因。
- 失败时迭代修复 CodeAgent 本体、提示词、工具或工作流，不要硬编码 case 答案。
- 最终输出 `benchmark_result.json` 和 `benchmark_report.md`，包含总成功率、分类成功率、每个案例明细和失败原因聚合。

---

## 11. LangGraph / LangChain 核心实现约束

LangGraph 工作流和工具调用是本项目最核心的工作。实现时必须严格围绕以下设计：

1. 主图只负责阶段路由、成功/失败分支、重试循环和最终报告。
2. 四个阶段分别实现为子图，便于单独测试和展示。
3. 阶段内显式表达工具循环：

```text
agent_node → need_tool? → tool_node → agent_node
```

4. LLM 节点输出结构化数据，优先使用 Pydantic schema。
5. HITL 审批节点只做 interrupt，不执行副作用。
6. `apply_patch`、`run_shell`、`write_report` 等副作用节点必须幂等，避免 resume 后重复执行。
7. 所有条件路由写入 `decision_trace.jsonl`。
8. 默认 `max_repair_attempts=3`。
9. streaming 进度事件要能被 CLI 渲染。
10. checkpoint 使用 run_id/thread_id 关联到本次运行目录。

---

## 12. Agent 节点提示词约束

实现各 Agent 节点的 system prompt 时，必须包含这些约束：

1. 当前 MVP 只支持 Python + pytest。
2. 不能猜测文件内容；需要信息时使用 `read_file` / `search_code` / `scan_project`。
3. 所有项目源码/测试文件变更必须走 patch-first。
4. 输出必须符合指定结构化 schema。
5. 不要修改敏感文件，如 `.env`、密钥、证书、token 文件。
6. 不要读取或使用 benchmark hidden oracle tests / evaluation 目录作为解题上下文。
7. 不要删除测试、跳过断言、硬编码样例来“通过”测试。
8. 不要声称测试通过，除非工具返回的 pytest 结果确实通过。
9. 不输出隐藏思维链；只输出可审计的摘要、理由、证据和结果。
10. 优先最小、相关、可审查的 patch，避免无关大规模改动。

---

## 13. 输出产物规范

一次完整运行建议生成：

```text
codeagent_runs/
└── <run_id>/
    ├── metadata.json
    ├── task_config.yaml
    ├── transcript.jsonl
    ├── decision_trace.jsonl
    ├── artifacts_index.json
    ├── implementation/
    │   ├── implementation_plan.md
    │   ├── patch.diff
    │   ├── changed_files.json
    │   └── implementation_report.md
    ├── testing/
    │   ├── test_plan.md
    │   ├── test_plan_review.json
    │   ├── test_patch.diff
    │   ├── test_stdout.log
    │   ├── test_stderr.log
    │   ├── test_report.json
    │   └── test_report.md
    ├── debugging/
    │   ├── reproduction_report.md
    │   ├── failure_summary.md
    │   ├── fault_localization.json
    │   ├── root_cause.md
    │   ├── repair_plan.md
    │   └── debug_report.md
    ├── repair/
    │   ├── repair_plan.final.md
    │   ├── repair.patch.diff
    │   ├── before_test.log
    │   ├── after_test.log
    │   ├── changed_files.json
    │   └── repair_report.md
    └── final_report.md
```

每个阶段都必须生成 `stage_result.json`，失败时必须写清：失败原因、已有产物、下一步建议。

---

## 14. 提示词工程要求

Agent 节点质量高度依赖提示词。实现 Planner、Coder、TestDesigner、TestWriter、Debugger、Repairer、Verifier 等节点时，必须专门设计详细、充分、可维护的 system prompt / task prompt，不能只写几句过短提示。

每类节点提示词至少包含：角色、目标、输入上下文说明、允许/禁止行为、工具使用规则、patch-first 约束、hidden oracle 禁止规则、输出 schema、失败处理、验证标准和可审计摘要要求。必要时提供少量格式示例，保证模型输出稳定、可解析、可复现。

提示词应作为工程资产管理：集中放在清晰目录或模块中，配套单元测试或快照测试，并在 README 或开发文档中说明职责和设计理由。不得把提示词写成不可维护的散落字符串。

---

## 15. 开发者友好汇报文档要求

每完成一个里程碑，除更新 `plans.md` 外，还要撰写或追加清晰易懂、面向开发者的汇报文档。建议位置：

```text
docs/dev_reports/Mxx_<milestone_name>.md
```

每份文档至少包含：里程碑目标、完成内容、涉及模块和关键文件、核心设计决策、如何运行/验证、测试结果、已知问题、与 SRS/设计文档的对应关系、下一步建议。不要堆砌日志，不复制大段旧文档；用路径、命令和结果支撑结论。

----

## 16. 最终完成标准

在你停止前，必须完成以下检查：

1. `plans.md` 完整且持续更新。
2. README 能指导新用户安装、配置 API Key、运行 CLI、运行 benchmark。
3. 所有 P0 功能有代码、测试或明确验收证据。
4. 所有新增模块都有单元测试。
5. 主要集成路径有测试或可复现 demo。
6. `python -m pytest -q` 通过。
7. CLI help 和至少一个示例 task 可运行。
8. HumanEval/MBPP benchmark 通过。
9. QuixBugs benchmark 通过。
10. BugsInPy benchmark 尽可能通过；若环境限制无法运行，必须有环境检测、准备脚本、清晰 blocker 和文档。
11. 5 个自建 benchmark 全部运行，尽可能全部通过；若有失败，必须迭代修复到最佳状态，并在报告中写清剩余失败原因。
12. API Key 未泄露，secret 文件未提交。
13. 文档与实现一致；如已修改文档，必须有备份、版本号和变更记录。
14. 所有报告、patch、日志、benchmark 结果都在预期目录。

---

## 17. 现在开始

现在开始执行以下第一步：

1. 阅读仓库文档和 benchmark 目录。
2. 创建 `plans.md`，包含至少 20 个里程碑、风险登记、架构摘要、测试策略、合规矩阵、benchmark 计划和运行日志区。
3. 在 `plans.md` 完成且经过我审核通过前，不要开始写业务代码。

---

## 实现对齐变更记录

| 日期 | 变更 | 原因 | 影响 |
|---|---|---|---|
| 2026-06-03 | 补充 benchmark 原始 case 只读模板与干净 run workspace 副本规则。 | 防止 benchmark 运行污染原始案例，保证案例可重复利用。 | 不改变课程验收范围；实现 BenchmarkRunner 时必须复制 case 后再运行 Agent 和评测。 |
