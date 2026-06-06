# 2026-06-06 增量补丁工作流与 Todo Manager 优化合并报告

## 1. 分支信息

- 工作分支：`codex/incremental-file-patch-workflow`
- 合并目标：`main`
- 报告日期：2026-06-06
- 工作主题：围绕 Todo Manager 自建 benchmark、增量补丁工作流、repair 能力、可观测性、CLI 演示体验进行系统性优化。

## 2. 总体结论

本分支当前状态适合提交并合并到 `main`。核心原因如下：

1. 全量测试通过：`397 passed, 1 skipped`。
2. 本分支相关 Python 文件 ruff 检查通过。
3. Todo Manager 输入材料、演示手册、CLI 快速运行能力、增量补丁工作流、调试与修复能力均已完成配套测试或文档更新。
4. 已知 `ruff check .` 会扫描到 benchmark 官方/样例 evaluation 代码中的既有风格问题，以及非本分支改动的 `codeagent/benchmark/environment.py` 风格问题；本次合并采用“本分支相关文件 lint 通过”作为质量门禁。

## 3. 主要工作内容

### 3.1 Todo Manager 自建 benchmark 输入材料升级

Todo Manager 案例从旧版较薄的输入材料升级为四份更接近真实软件开发任务的中文材料：

- `PRD.md`
- `user_stories.md`
- `design_model.md`
- `acceptance_criteria.md`

主要变化包括：

- 将旧 `prd.md` 与 `requirements.md` 整合为更完整的 `PRD.md`。
- 明确默认成品必须是可连续交互的 TUI，而不是一次一个命令的 CLI。
- 丰富业务场景、数据持久化、错误处理、边界行为、输入输出契约。
- 扩展设计模型，包含分层结构、领域模型、状态流转和交互流程。
- 将用户故事改为更自然、详细的中文叙述。
- 调整 Todo Manager oracle，使其更贴近连续 TUI 会话和真实验收。

### 3.2 benchmark 与运行产物目录整理

对 benchmark 相关说明与配置进行了整理，明确不同产物目录的职责：

- benchmark 标准评测产物归入 `codeagent_runs/benchmarks/...`。
- 演示运行产物归入 `codeagent_runs/demos/...`。
- 自建 benchmark case 本体继续保留在 `benchmark/selfbuilt/cases/...`。

这样避免 `benchmark/codeagent_runs`、`benchmark/selfbuilt/codeagent_runs`、仓库根 `codeagent_runs` 等同名目录造成理解混乱。

### 3.3 LLM 输出语言提示词调整

在提示词规则中加入简体中文输出要求：

- 面向用户的自然语言字段使用简体中文。
- 代码标识符、路径、命令、依赖名、错误日志等技术 token 保持原文。

此项用于减少实现计划、测试计划、修复计划等产物默认输出英文的问题。

### 3.4 增量单文件补丁工作流重构

本分支对实现、测试、修复阶段的补丁生成工作流进行了结构性调整：

- 不再一次性要求 LLM 生成多个文件的补丁。
- 改为按计划文件顺序逐个生成单文件补丁。
- 每个文件生成后立即审查、应用、落盘。
- 前一个文件应用后的内容会成为后续文件上下文，改善跨文件一致性。
- 单个补丁失败时只重试该文件，不重试整批补丁。
- 单个补丁失败记录会传给后续补丁，提醒 LLM 避免重复错误。

同时新增阶段级上下文机制：

- 阶段开始时由 host 确定性读取一次上下文。
- 后续单文件补丁复用阶段上下文，不再每个文件前调用 LLM 决定读哪些文件。
- 每个成功应用的文件通过 `applied_file_context.md` 追加到后续上下文。
- 生成 `stage_patch_context.md/json`，便于复盘阶段开始时 LLM 看到的材料。

此项减少了 `PatchFileContextDecision` 一类重复 LLM 调用，也降低了工作流成本。

### 3.5 人工审批体验优化

补丁审批选项调整为：

- 是，应用此补丁
- 是，应用此补丁，本阶段不再提示
- 否，告知 CodeAgent 如何调整

移除了“拒绝本项”和“取消整次运行”，降低现场演示和普通使用时的理解成本。

当用户选择“本阶段不再提示”后：

- 后续同阶段文件补丁会自动通过。
- CLI 输出自动通过信息。
- 输出目标文件路径，便于在支持终端链接的环境中打开。
- 自动审批记录会写入 `decision_trace.jsonl` 和 `workflow_events.jsonl`。

### 3.6 终端文件链接与 CLI 展示修复

针对 Windows 终端中出现 `]8;;`、蓝色路径异常、无法跳转等问题，统一了文件路径展示逻辑：

- 自动通过补丁输出和人工审批文件列表使用同一套可点击路径生成方式。
- 自动通过时只输出目标文件，不再输出补丁文件和 JSON 草稿文件。
- 减少冗余路径信息，使 CLI 输出更稳定、更适合演示。

### 3.7 测试阶段策略优化

测试阶段从“最多 6 个测试文件”调整为：

- 首选 1 个测试文件。
- 复杂场景最多 2 个测试文件。
- 如果使用 2 个测试文件，计划中必须说明拆分理由。
- 禁止多文件测试计划却生成窄命令，例如只运行 `tests/test_app.py`。
- 推荐测试命令使用 `python -m pytest -q` 或 `python -m pytest tests -q`。
- 测试总函数规模建议 25-60，硬上限 80。

此外，本分支已取消“单个测试文件最多 15 个测试函数”的硬限制。单文件生成 19 个测试函数不再触发重试，只要整体测试套件不超过总量上限即可。

### 3.8 可观测性增强

为追踪 LLM 行为和工作流行为，补强了多类产物：

- `workflow_events.jsonl`
- `workflow.log`
- `decision_trace.jsonl`
- LLM 调用 attempts 文件
- `stage_patch_context.md/json`
- `applied_file_context.md`
- repair/debug 阶段失败证据和分析产物

同时修复 JSONL 并发写入损坏问题：

- `WorkflowTraceRecorder.record()` 使用路径级锁。
- JSONL 行先完整序列化，再在锁内一次性追加。
- `workflow_events.jsonl` 与 `workflow.log` 在同一锁内连续写入，降低顺序不一致风险。
- `JsonlRecorder` 同步加固，避免 transcript 和 decision trace 遇到同类问题。
- 新增 JSONL 校验工具函数，支持逐行解析并报告坏行。

### 3.9 Debugging 与 Repair 能力增强

调试阶段新增 LLM Agent 节点，输出结构化 `DebuggingAnalysis`：

- `failure_origin`
- 置信度
- 候选文件
- 证据
- 根因
- 修复策略
- 是否允许修复测试代码
- 推荐验证命令

调试阶段现在会优先读取最新失败证据：

- repair 后失败时优先使用 `repair/repair_test_result.json`、`repair/after_test.log`、`repair/repair_report.md`。
- 再回退到 testing 阶段产物。

Repair 阶段支持保守修复可见生成测试代码：

- 默认仍然修产品代码。
- 仅当调试分析明确判定为 `generated_test_code`、`mixed` 或 `test_harness`，且证据指向可见测试自身错误时，允许修复 `tests/**`。
- 仍禁止修改隐藏 oracle、evaluation、expected_result、pytest 配置、conftest。
- 仍禁止通过删除测试、skip/xfail、弱化断言等方式“修复”。

此项针对 Todo Manager 场景中“测试代码自身错误导致 repair 误修产品代码”的问题。

### 3.10 CLI 快速运行能力

`python -m codeagent run` 增强为可直接完成 Todo Manager 简单非交互式演示：

- 新增 `--requirements/-r`，支持重复传入多份输入材料。
- 新增 `--model/--model-name`，可直接指定模型，例如 `google/gemini-3.5-flash`。
- 新增 `--auto-approve`，用于无人值守运行。

这使得演示手册中可以提供“不写 YAML、不跑 benchmark、不进入 wizard”的简单命令。

### 3.11 Todo Manager 开发团队演示手册

新增并多次修订 `benchmark/selfbuilt/cases/01_todo_manager/Todo_Manager_开发团队演示手册.md`，形成面向开发团队的保姆级演示教程：

- 先介绍 Todo Manager 案例。
- 指导创建带时间戳的新演示空间。
- 新增直接非交互式 `codeagent run` 流程。
- 保留并强化半交互式 wizard 流程。
- 指导读者审查实现计划、测试计划、单文件补丁和测试命令。
- 指导阅读运行产物、工作流日志、调试分析和修复报告。
- 指导启动生成出来的 TUI 软件并连续操作。
- 将单 case benchmark 放到最后作为可选标准化评测流程。

手册现在强调：新空间仍放在仓库的 `codeagent_runs/demos/todo_manager/<时间戳>/` 下管理，不再建议放到仓库外的 D 盘目录。

### 3.12 TUI Harness 工具

新增 `tools/tui_harness` 及相关测试，用于后续对 TUI 软件进行更稳定的驱动和屏幕解析：

- 动作模型
- 屏幕快照解析
- CLI/daemon 通信
- PTY 后端
- 单元测试与 smoke 测试骨架

真实 PTY smoke 测试默认跳过，需要显式设置环境变量后运行。

## 4. 重要文件变更概览

### 4.1 核心工作流

- `codeagent/cli/executor.py`
- `codeagent/agents/plan_generation.py`
- `codeagent/services/patch_service.py`
- `codeagent/stages/implementation_service.py`
- `codeagent/stages/testing_service.py`
- `codeagent/stages/debugging_service.py`
- `codeagent/stages/repair_service.py`
- `codeagent/tools/risk_checker.py`

### 4.2 CLI 与交互体验

- `codeagent/cli/app.py`
- `codeagent/cli/approval_console.py`
- `codeagent/cli/progress.py`
- `codeagent/cli/tui.py`
- `codeagent/config/cli_mapping.py`
- `codeagent/config/defaults.py`

### 4.3 可观测性

- `codeagent/reports/jsonl_utils.py`
- `codeagent/reports/transcript.py`
- `codeagent/reports/workflow_trace.py`

### 4.4 benchmark 与文档

- `benchmark/selfbuilt/cases/01_todo_manager/...`
- `benchmark/selfbuilt/cases/01_todo_manager/Todo_Manager_开发团队演示手册.md`
- `docs/dev_reports/OPT07_Todo_Manager工作流问题追踪.md`
- `docs/design/02_模块划分与职责设计.md`
- `docs/design/04_LangGraph工作流设计.md`
- `docs/test/自建benchmark案例设计报告.md`
- `docs/optimization/优化任务看板.md`

### 4.5 测试

- `tests/integration/test_cli_run.py`
- `tests/integration/test_debugging_stage.py`
- `tests/integration/test_repair_stage.py`
- `tests/integration/test_benchmark_runner.py`
- `tests/unit/agents/test_plan_generation.py`
- `tests/unit/runtime/test_artifacts_and_logs.py`
- `tests/unit/config/test_cli_mapping.py`
- `tests/unit/tui_harness/...`

## 5. 验证结果

### 5.1 全量测试

命令：

```powershell
python -m pytest -q
```

结果：

```text
397 passed, 1 skipped
```

跳过项：

- `tests/integration/test_tui_harness_smoke.py`
- 原因：需要设置 `CODEAGENT_TUI_HARNESS_SMOKE=1` 才运行真实 PTY smoke 测试。

### 5.2 本分支相关 Python 文件 lint

命令范围：

- `codeagent/` 中本分支改动文件
- `tools/tui_harness`
- 本分支新增或修改的测试文件

结果：

```text
All checks passed.
```

### 5.3 已知非阻塞事项

`ruff check .` 会扫描 benchmark 官方/样例 evaluation 文件，并报告既有风格问题，例如：

- `benchmark/cases/humaneval_.../evaluation/test_solution.py`
- `benchmark/cases/mbpp_.../evaluation/test_solution.py`
- 非本分支核心改动的 `codeagent/benchmark/environment.py`

这些问题不影响本分支功能验证，也不属于本次工作范围。若后续希望将 `ruff check .` 作为全仓质量门禁，应单独整理 lint exclude 或修复 benchmark 样例风格。

## 6. 合并风险评估

### 6.1 主要收益

- 大幅提升 Todo Manager 自建 benchmark 的输入质量和评测可信度。
- 降低多文件大补丁一次性生成导致的失败风险。
- 降低 LLM 重复上下文选择调用，减少成本。
- 增强 repair 阶段信息来源和测试修复边界。
- 改善 CLI 审批、文件链接、自动通过展示和演示手册体验。
- 增强可观测性，使失败追溯更可诊断。

### 6.2 主要风险

- 增量补丁工作流改动较大，后续真实 LLM 运行仍可能暴露模型输出稳定性问题。
- 修复阶段允许在保守条件下修改可见测试，需持续观察风险边界是否足够严格。
- benchmark Todo Manager oracle 更强调连续 TUI 会话，可能提升任务难度。
- `tools/tui_harness` 当前仍是辅助工具，真实 PTY smoke 测试默认跳过。

### 6.3 风险缓解

- 已增加单元测试和集成测试覆盖增量补丁、审批、JSONL、repair、debugging、CLI 参数映射。
- repair 测试修改路径有风险检查保护。
- Todo Manager 演示手册明确区分直接 run、wizard 和 benchmark。
- JSONL 可观测性增强便于后续追踪真实 LLM 问题。

## 7. 合并建议

建议将 `codex/incremental-file-patch-workflow` 合并到 `main`。

合并后建议继续跟进：

1. 用 `google/gemini-3.5-flash` 和主力模型各跑一轮 Todo Manager benchmark，持续观察 oracle 失败原因。
2. 单独处理全仓 `ruff check .` 会扫到 benchmark 样例代码的问题，决定是修复样例还是配置 exclude。
3. 继续基于新增可观测性分析 repair 多轮失败案例。
4. 逐步将 TUI harness 接入更多自建 benchmark 的交互式验收。
