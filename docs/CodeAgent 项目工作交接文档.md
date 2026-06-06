# CodeAgent 项目工作交接文档

> 面向对象：即将接手 CodeAgent 的开发团队成员  
> 编写日期：2026-06-06  
> 编写原则：以当前源码为准，历史文档仅作背景参考  
> 本次交接说明：本文编写时没有重跑全量测试或全量 benchmark，避免额外消耗时间和 token；文中测试结果来自已有开发报告和运行产物，凡是未重新确认的地方会明确说明“需要进一步确认”。

## 0. 先读这一段：如何使用这份文档

这份文档不是产品宣传页，也不是只列命令的 README。请把它当作一次带新人上手的交接教程：先知道项目为什么存在，再理解代码如何组织，再跟着命令把项目跑起来，最后再看 benchmark 和后续工作。

建议阅读顺序如下：

1. 先读第 1 到第 3 节，建立项目全局印象。
2. 再读第 4 到第 7 节，理解架构、模块和运行产物。
3. 按第 8 节完成本地环境和最小运行。
4. 按第 9 到第 11 节理解 benchmark，尤其注意隐藏 oracle 和运行副本。
5. 最后读第 12 到第 14 节，了解常见问题、风险和后续工作。

历史文档中有些内容是设计草案或阶段性汇报，可能已经被当前实现超越。遇到文档和代码不一致时，以 `codeagent/`、`benchmark/`、`scripts/` 下的当前代码和配置为准。

## 1. 项目是什么

CodeAgent 是一个本地命令行软件工程智能体，目标是让大语言模型在开发者监督下完成 Python 项目的连续工程任务。它覆盖的主流程是：

```text
实现 implement -> 测试 test -> 调试 debug -> 修复 repair
```

更具体地说，它会读取需求、PRD、用户故事、设计模型、错误日志或已有项目代码，然后让 LLM 生成结构化计划，再在审批后生成补丁、运行测试、分析失败、生成修复补丁并输出报告。

项目来源见：

- `docs/题目设计.md`：课程题目和交付要求。
- `docs/analysis/《基于大语言模型的软件工程智能体》需求规格说明书_v0.1.md`：需求规格说明。注意这里是历史需求文档，当前实现已经有多轮优化，最终以代码为准。

从课程目标看，CodeAgent 主要选择了题目中的两个连续能力组合：

- “实现 + 测试”：根据需求或设计材料生成代码和测试，并执行测试。
- “调试 + 程序修复”：根据错误项目和失败测试定位问题，生成修复补丁并验证。

当前项目没有实现完整 IDE 插件或 Web 前端。课程题目里提到 IDE 集成，但本项目范围已收敛到 CLI；如果最终验收仍强制要求 IDE 集成，需要后续补一个轻量 VSCode Task、命令包装或本地服务入口。

## 2. 当前完成情况

截至当前代码，项目已完成一个可运行的 Python 包和 CLI。核心能力包括：

- CLI 命令：`wizard`、`run`、`implement`、`test`、`debug`、`repair`、`benchmark`、`resume`。
- 配置系统：YAML/JSON 配置加载、阶段连续性校验、路径归一化、模型配置、权限配置。
- 工作流：基于 LangGraph 的主图和四阶段路由。
- LLM 调用：通过 LangChain 的 OpenAI-compatible `ChatOpenAI` 接入 OpenRouter。
- 结构化输出：实现计划、测试计划、调试分析、修复计划、补丁草案均用 Pydantic schema 校验。
- patch-first：LLM 先产出计划和补丁草案，审批后由项目自己的 `PatchService` 校验并应用。
- HITL：人工审批计划、补丁和命令；benchmark 模式自动审批但仍写入审计日志。
- checkpoint/resume：每次运行创建 SQLite checkpoint，支持查看和恢复 pending interrupt。
- 报告与日志：每次 run 写入 `metadata.json`、`task_config.yaml`、`workflow.log`、`workflow_events.jsonl`、`decision_trace.jsonl`、`artifacts_index.json`、阶段报告和最终报告。
- benchmark：支持公开/通用 benchmark、自建 benchmark、case 复制隔离、隐藏 oracle 评测和聚合报告。

已有开发报告中记录过以下验证结果：

- `docs/dev_reports/项目实现汇报.md` 中记录过 `python -m pytest -q` 为 `303 passed`，公开 benchmark 6 个启用 case 成功，自建 benchmark 5/5 成功。
- `docs/dev_reports/2026-06-06_增量补丁工作流与Todo_Manager优化合并报告.md` 中记录过后续优化分支 `397 passed, 1 skipped`。

需要注意：本文编写时没有重跑上述命令。因此这些是历史验证证据，不等同于当前机器上刚刚重新验证通过。接手成员接管后建议先跑轻量 smoke，再视需要跑完整测试。

## 3. 新人先看哪些文件

如果你只想快速建立项目地图，建议按下面顺序看。

第一组：项目入口和当前说明

- `README.md`：当前对外 README，包含安装、API Key、CLI、运行产物和 benchmark 命令。
- `pyproject.toml`：Python 包名、依赖、脚本入口和 pytest 配置。
- `codeagent/__main__.py`：`python -m codeagent` 的入口，转到 CLI。
- `codeagent/cli/app.py`：所有 CLI 子命令的定义，是理解系统怎么启动的第一站。

第二组：核心工作流

- `codeagent/cli/executor.py`：把 `TaskConfig` 转成 `RunContext`，注入阶段 handler，然后启动 LangGraph。这个文件很大，但它是 CLI 和阶段服务之间的胶水。
- `codeagent/workflow/main_graph.py`：LangGraph 主图结构。
- `codeagent/workflow/routing.py`：阶段之间如何跳转。
- `codeagent/workflow/state.py`：checkpoint-safe 的 `AgentState`。
- `codeagent/runtime/run_context.py`：一次 run 的目录、产物和上下文如何初始化。

第三组：四个阶段服务

- `codeagent/stages/implementation_service.py`：实现阶段，写计划、生成补丁、语法检查、报告。
- `codeagent/stages/testing_service.py`：测试阶段，生成测试计划和测试补丁，执行测试并解析结果。
- `codeagent/stages/debugging_service.py`：调试阶段，收集失败证据、定位候选文件、生成根因和修复计划。
- `codeagent/stages/repair_service.py`：修复阶段，生成修复计划和补丁，做风险检查并回归验证。

第四组：LLM、补丁、命令和报告

- `codeagent/agents/plan_generation.py`：所有 LLM 结构化计划和补丁生成的核心。
- `codeagent/models/factory.py`：OpenRouter/OpenAI-compatible 模型客户端创建。
- `codeagent/services/patch_service.py`：统一 diff 的创建、解析、校验、应用和回滚。
- `codeagent/tools/shell_tools.py`：受限 shell 命令执行和日志记录。
- `codeagent/reports/writer.py`：阶段报告和最终报告生成。

第五组：benchmark

- `benchmark/README.md`：公开/通用 benchmark 目录规则。
- `benchmark/benchmark.yaml`：公开/通用 benchmark 聚合配置。
- `benchmark/selfbuilt/README.md`：自建 benchmark 说明。
- `benchmark/selfbuilt/selfbuilt_benchmark.yaml`：5 个自建案例聚合配置。
- `benchmark/selfbuilt/meeting_room_demo_benchmark.yaml`：单个会议室案例演示配置。
- `codeagent/benchmark/runner.py`：benchmark 运行主流程。
- `codeagent/benchmark/evaluator.py`：Agent 自测和隐藏 oracle 如何判定成功。

第六组：历史设计和汇报

- `docs/design/README_设计文档包索引.md`：系统设计文档入口。
- `docs/design/00_系统设计方案总览.md`：设计总览。
- `docs/test/自建benchmark案例设计报告.md`：自建 benchmark 的设计思路。
- `docs/test/benchmark样例整理报告.md`：公开 benchmark 样例来源。
- `docs/optimization/优化任务看板.md`：后期优化任务摘要。
- `docs/dev_reports/`：详细开发过程。内容很多，查历史原因时再读，不建议新人第一天全看完。

## 4. 项目文件结构说明

当前仓库顶层结构大致如下：

```text
CodeAgent/
  codeagent/                 核心 Python 包
  benchmark/                 benchmark 配置和案例模板
  scripts/                   BugsInPy / WSL / conda 辅助脚本
  tests/                     单元测试和集成测试
  docs/                      题目、需求、设计、测试、开发报告和本文档
  codeagent_runs/            本地运行产物，已被 gitignore 忽略
  dataset/                   原始公开数据集快照，主要用于追溯和后续扩展
  pyproject.toml             包配置和依赖
  README.md                  当前项目说明
```

`codeagent/` 内部可以按职责理解：

```text
codeagent/
  cli/             Typer 命令、wizard、审批 UI、进度展示、resume
  config/          Pydantic 配置 schema、默认值、配置加载、阶段校验
  workflow/        LangGraph 主图、路由、状态、checkpoint、事件流
  stages/          implementation/testing/debugging/repair 四阶段服务
  agents/          prompt 注册和 LLM 结构化生成服务
  models/          模型工厂、结构化输出、密钥解析
  context/         项目扫描、文件读取、代码搜索、敏感路径过滤
  services/        patch 服务等跨阶段服务
  tools/           shell、patch、pytest 解析、权限、HITL 工具
  reports/         运行产物、报告、JSONL 记录、artifact index
  benchmark/       case 装载、runner、evaluator、环境检测、报告
  runtime/         run context、命令结果模型
  adapters/        pytest/unittest 测试结果解析器
  errors/          统一错误结构
```

`benchmark/` 内部有两条线：

```text
benchmark/
  cases/               公开/通用 benchmark 案例模板
  benchmark.yaml       公开/通用 benchmark 聚合配置
  selfbuilt/
    cases/             自建案例模板
    selfbuilt_benchmark.yaml
    meeting_room_demo_benchmark.yaml
```

非常重要：`benchmark/cases/` 和 `benchmark/selfbuilt/cases/` 是可复用模板，原则上不要直接让 Agent 在这些原始目录里写代码。benchmark runner 会复制到 `codeagent_runs/benchmarks/.../case_workspaces/` 后再运行。

## 5. 系统架构和主要实现方法

### 5.1 从 CLI 到工作流

用户运行命令后，入口是 `codeagent/cli/app.py`。它用 Typer 定义命令：

```text
python -m codeagent wizard
python -m codeagent run --config ...
python -m codeagent implement ...
python -m codeagent test ...
python -m codeagent debug ...
python -m codeagent repair ...
python -m codeagent benchmark ...
python -m codeagent resume ...
```

除 `benchmark` 和 `resume` 外，大多数任务最终都会变成一个 `TaskConfig`，再交给 `codeagent/cli/executor.py` 的 `execute_task_config()`。

`execute_task_config()` 做几件事：

1. 调用 `create_run_context()` 创建本次运行目录。
2. 初始化 `CheckpointManager` 和 SQLite saver。
3. 创建初始 `AgentState`。
4. 通过 `WorkflowFactory` 构建 LangGraph。
5. 将四个阶段 handler 注入主图。
6. 流式执行 graph，并把事件写入 `workflow.log` 和 `workflow_events.jsonl`。
7. 根据最终状态写 `final_report.md`。

### 5.2 TaskConfig 是所有运行的核心输入

`TaskConfig` 定义在 `codeagent/config/schema.py`。它描述一次任务的全部关键信息：

- `stages`：阶段列表，合法值是 `implement`、`test`、`debug`、`repair`。
- `project_path`：Agent 可以修改的项目目录。
- `input_materials`：需求、PRD、用户故事、设计模型、错误日志等输入材料。
- `model`：模型供应商、模型名、base URL、API key 环境变量。
- `runtime`：checkpoint、修复次数、命令超时、日志截断等运行参数。
- `permissions`：审批模式，默认人工审批。
- `test_command`：测试/调试/修复阶段使用的命令。
- `agent_visibility`：Agent 可见路径和隐藏路径，benchmark 中尤其重要。
- `mode`：`wizard`、`run` 或 `benchmark`。

阶段校验在 `codeagent/config/validators.py` 中实现。当前要求阶段必须按顺序且连续。例如：

- 合法：`implement,test`
- 合法：`test,debug,repair`
- 合法：`debug`
- 不合法：`implement,debug`，因为跳过了中间的 `test`
- 不合法：`repair,debug`，因为顺序反了

### 5.3 LangGraph 主图是确定性路由

主图在 `codeagent/workflow/main_graph.py` 中。它不是自由聊天式多 Agent，而是清晰的状态机：

```text
route_entry
  -> implementation
  -> route_after_implementation
  -> testing
  -> route_after_testing
  -> debugging
  -> route_after_debugging
  -> repair
  -> route_after_repair
  -> final_success / final_failed / final_cancelled
```

路由规则在 `codeagent/workflow/routing.py`。关键逻辑是：

- implementation 成功后，如果选择了 test，就进入 testing；否则成功结束。
- testing 成功后直接成功结束，不再进入 debug/repair。
- testing 失败后，如果选择了 debug，就进入 debugging；否则失败结束。
- debugging 成功后，如果选择了 repair，就进入 repair；否则成功结束。
- repair 成功则成功结束。
- repair 失败且还没达到 `max_repair_attempts`，会回到 debugging 再分析一轮。

这意味着 debug/repair 不是每次都会跑。只有测试失败时，才会进入调试和修复闭环。

### 5.4 LLM 输出不是直接写文件

当前实现最重要的安全边界是“两步结构化输出”：

1. 第一轮 LLM 只生成计划，例如 `ImplementationPlan`、`TestingPlan`、`RepairPlan`。计划中不能包含完整文件内容、diff、`old_content` 或 `new_content`。
2. 计划通过审批后，第二轮 LLM 才生成补丁草案，例如 `ImplementationPatchDraft`、`TestingPatchDraft`、`RepairPatchDraft`。

相关代码在 `codeagent/agents/plan_generation.py` 和四个 `codeagent/stages/*_service.py` 中。

后期优化还引入了增量单文件补丁工作流。也就是说，系统会按计划中的文件顺序逐个生成、审批、应用单文件补丁，而不是一次性让 LLM 生成大补丁。这样更容易定位失败，也更适合演示。

### 5.5 patch-first 和风险控制

项目文件修改走 `PatchService`，代码在 `codeagent/services/patch_service.py`。它负责：

- 从结构化 file changes 创建 unified diff。
- 解析 diff。
- 校验 patch path 不能越出项目根目录。
- 拒绝敏感文件、生成目录、隐藏 benchmark 路径。
- 检测测试删除、skip/xfail、可疑硬编码等风险。
- 应用补丁前做文件快照。
- 应用失败时尽量回滚。

修复阶段额外使用 `codeagent/tools/risk_checker.py`，默认禁止 repair 修改测试文件。只有 debugging 明确判定问题来自可见生成测试，并且计划中设置允许修复测试时，才允许有限修改普通可见测试文件。

### 5.6 Shell 命令不是任意执行

测试和验证命令走 `codeagent/tools/shell_tools.py` 的 `ShellRunner`。当前策略只允许较窄的一组命令：

- `pytest`
- `python -m pytest`
- `python -m unittest`
- `python -m py_compile`

它会拒绝明显高风险选项、越界路径参数和非测试类命令。命令执行会记录：

- 原始命令
- argv
- cwd
- timeout
- exit code
- stdout/stderr 日志
- 是否超时
- 命令记录 JSON

这意味着如果你在配置里写 `npm test`、`pip install`、`powershell ...` 这类命令，普通 stage 很可能会被策略拒绝。BugsInPy 是特殊路径，由 benchmark runner 的 prepare/oracle 流程处理，不是普通 Agent shell 权限。

### 5.7 报告和审计产物

每次 run 都会创建一个唯一目录，默认在 `codeagent_runs/<run_id>/` 下。核心文件包括：

```text
metadata.json
task_config.yaml
transcript.jsonl
decision_trace.jsonl
workflow.log
workflow_events.jsonl
artifacts_index.json
checkpoints.sqlite
final_report.md
implementation/
testing/
debugging/
repair/
benchmark/
```

建议新人第一次调试时重点看：

- `final_report.md`：最终摘要。
- `workflow.log`：人类可读的时间线。
- `workflow_events.jsonl`：机器可读事件，适合查精确状态。
- `decision_trace.jsonl`：人工或自动审批记录。
- `artifacts_index.json`：所有产物索引。
- 每个阶段的 `stage_result.json` 和 `stage_report.md`。
- 每个阶段的 `llm_calls/`：prompt、response、解析结果和校验记录。

## 6. 核心功能和工作流详解

### 6.1 实现阶段 implementation

输入通常是需求材料和项目目录。实现阶段会：

1. 扫描项目结构和可见输入材料。
2. 调用 LLM 生成 `ImplementationPlan`。
3. 写入 `implementation/implementation_plan.md` 和 JSON 版本。
4. 让用户审批计划，或在 auto/benchmark 模式自动审批。
5. 生成单文件或聚合补丁草案。
6. 写入 patch 文件和补丁草案 JSON。
7. 审批后应用到 `project_path`。
8. 对改动的 Python 文件做内部 `compile()` 语法检查。
9. 写入 `implementation_report.md`、`changed_files.json`、`stage_result.json`。

关注点：

- implementation 阶段原则上不应该生成测试文件。
- 如果 LLM 计划里目标文件路径不合理，应该在计划审批时反馈，而不是等补丁应用失败。

### 6.2 测试阶段 testing

测试阶段会：

1. 根据需求、实现结果和项目结构生成 `TestingPlan`。
2. 写入 `testing/test_plan.md`。
3. 审批测试计划。
4. 生成可见测试文件补丁，通常放到 `tests/` 或 `test_*.py`。
5. 校验测试补丁质量。
6. 应用测试补丁。
7. 审批测试命令。
8. 执行测试并解析 pytest/unittest 输出。
9. 写入 `testing/test_result.json`、`testing/test_report.md`、日志和阶段结果。

当前实现特别强调：Agent 自测不能是空测试。以下情况会被视为失败：

- `Ran 0 tests`
- `no tests ran`
- `collected 0 items`
- 命令成功但 total 为 0
- 只用 `py_compile` 冒烟，不生成真实测试

这一点对 benchmark 成功率很重要。runner 不只看隐藏 oracle，也要求 Agent 自己生成并运行过非空可见测试。

### 6.3 调试阶段 debugging

调试阶段会从测试失败结果、错误日志或输入材料中收集证据。它会：

1. 读取 testing 阶段的 stdout/stderr 或用户提供的 error log。
2. 根据配置决定是否重新运行复现命令。
3. 解析失败测试名和错误摘要。
4. 从 traceback 和项目源码中定位候选文件。
5. 调用 LLM 生成结构化 `DebuggingAnalysis`。如果模型不可用或失败，会回退到静态定位结果。
6. 写入 `fault_localization.json`、`root_cause.md`、`repair_plan.md`、`debug_report.md`。

调试阶段还会识别一种特殊问题：生成的测试脚手架本身有错，例如 subprocess 的 `cwd` 拼错，导致产品代码根本没运行。这时 debugging 会把失败归因为 `test_harness` 或 `generated_test_code`，后续 repair 才可能被授权修复可见测试。

### 6.4 修复阶段 repair

修复阶段会：

1. 读取 debugging 阶段的根因、候选文件和修复建议。
2. 调用 LLM 生成 `RepairPlan`。
3. 审批修复计划。
4. 生成修复补丁草案。
5. 校验 patch 和 repair 风险。
6. 审批后应用补丁。
7. 执行回归验证命令。
8. 解析验证结果，写入 `repair_test_result.json`、`after_test.log`、`repair_report.md`。
9. 成功则结束；失败则按路由进入下一轮 debugging/repair，直到达到最大次数。

默认最大修复尝试次数在 `codeagent/config/defaults.py` 中是 3，也可由配置覆盖。

## 7. 配置文件怎么读

### 7.1 最小任务配置模板

顶层 `examples/` 目录已经移除，所以当前仓库不再提供可直接运行的最小示例配置。如果需要临时写一个任务配置，可以按下面的结构组织。这个示例是“配置模板”，不是仓库中已经存在的文件：

```yaml
schema_version: 1
task_id: demo-task
title: Demo task
stages:
  - implement
  - test
project_path: path/to/project
output_dir: codeagent_runs/demo
language: python
test_framework: pytest
input_materials:
  - material_type: requirements
    path: path/to/requirements.md
    required: true
    multi: false
test_command:
  command: "python -m pytest -q"
  timeout_seconds: 120
```

这类配置从配置文件所在目录解析相对路径。正式运行前要确认 `project_path` 和所有 `input_materials.path` 都真实存在。若只是想先验证 CLI 是否可用，优先用第 8.3 和第 8.4 节的轻量 smoke，不需要先创建任务配置。

### 7.2 benchmark case 配置

自建案例见 `benchmark/selfbuilt/cases/01_todo_manager/case.yaml`。它有一些额外字段，例如 `entrypoint`、`workspace`、`success_criteria`。当前 `TaskConfig` 的 Pydantic schema 对未知字段是 `extra="ignore"`，所以这些字段主要是给人和 benchmark 文档看的，实际运行最关键的是：

- `stages`
- `workspace.path` 或 `project.path`
- `input_materials`
- `agent_visibility`
- `test_command`

`TaskConfig` 有兼容逻辑，会把 `workspace.path` 或 `project.path` 归一化为 `project_path`。

### 7.3 模型配置

默认模型配置在 `codeagent/config/defaults.py`：

```text
provider = openai_compatible
model_name = google/gemini-3.5-flash
base_url = https://openrouter.ai/api/v1
api_key_env = OPENROUTER_API_KEY
```

可在任务配置里覆盖：

```yaml
model:
  provider: openai_compatible
  model_name: google/gemini-3.5-flash
  base_url: https://openrouter.ai/api/v1
  api_key_env: OPENROUTER_API_KEY
  temperature: 0.2
  timeout_seconds: 120
  max_retries: 2
  max_tokens: 16384
```

需要进一步确认：`codeagent/config/defaults.py` 中列出的若干候选模型名属于运行时可选列表，是否都在当前 OpenRouter 账号下可用，需要接手成员按实际账号和模型供应商状态验证。不要把候选列表等同于稳定可用模型列表。

### 7.4 审批模式

默认是人工审批：

```yaml
permissions:
  approval_mode: manual
```

自动审批：

```yaml
permissions:
  approval_mode: auto
```

benchmark 模式会强制自动审批必要副作用，但会记录为 `decision_source=benchmark_auto`。普通 auto 模式记录为 `decision_source=user_configured_auto`。

## 8. 如何在本地跑起来

以下命令默认都在仓库根目录执行：

```powershell
cd D:\Projects\CodeAgent
```

如果接手成员在不同路径克隆项目，请把命令中的路径替换为自己的仓库根目录。

### 8.1 安装依赖

命令：

```powershell
python -m pip install -e ".[dev]"
```

在哪里执行：仓库根目录。

这条命令做什么：以 editable 模式安装 `codeagent` 包，并安装开发测试依赖 `pytest`。

正常结果：命令成功结束，后续可以运行 `python -m codeagent --help`，也可以用安装后的 `codeagent --help`。

常见失败：

- Python 版本太低。`pyproject.toml` 要求 Python 3.11+。
- shell 对 `".[dev]"` 引号处理异常。PowerShell 中推荐保留双引号。
- 网络或 PyPI 访问失败。需要配置镜像或重试。

### 8.2 配置 OpenRouter API Key

命令：

```powershell
[Environment]::SetEnvironmentVariable("OPENROUTER_API_KEY", "<your-key>", "User")
```

在哪里执行：PowerShell 任意目录均可。

这条命令做什么：把 OpenRouter API Key 写入 Windows 用户级环境变量。新开终端后生效。

正常结果：命令没有输出。重新打开终端后，CodeAgent 能从 `OPENROUTER_API_KEY` 读取密钥。

常见失败：

- 设置后没有重开终端，当前进程读不到新变量。
- 变量名写错。当前默认只找 `OPENROUTER_API_KEY`。
- Key 无效、余额不足或模型不可用。运行时会在阶段结果里记录模型错误，但不会打印真实 key。

如果只想先验证 CLI 和报告目录，可以先不配 key，运行第 8.4 节的 debug-only 示例。该示例如果触发 LLM 调试分析失败，会回退到静态调试结果。

### 8.3 查看 CLI 帮助

命令：

```powershell
python -m codeagent --help
```

在哪里执行：仓库根目录。

这条命令做什么：验证包入口、Typer CLI 和依赖是否可用。

正常结果：终端会显示 CodeAgent 根命令说明和子命令，例如 `wizard`、`run`、`benchmark`、`resume`。

常见失败：

- `No module named codeagent`：没有安装包，或当前 Python 环境不是安装时的环境。
- Typer/Rich 相关导入失败：依赖没有装完整，重新执行 `python -m pip install -e ".[dev]"`。

### 8.4 运行轻量 smoke 测试

命令：

```powershell
python -m pytest -q tests\test_package_smoke.py tests\test_cli_contract.py
```

在哪里执行：仓库根目录。

这条命令做什么：验证包导入、CLI contract 和基础命令帮助是否仍然正常。因为顶层 `examples/` 已移除，这是当前更适合接手第一步执行的低成本检查。

正常结果：pytest 显示测试通过，例如：

```text
... passed
```

常见失败：

- `No module named codeagent`：先执行 `python -m pip install -e ".[dev]"`，并确认当前 Python 环境一致。
- CLI contract 失败：优先看 `codeagent/cli/app.py` 是否改了命令名、参数或帮助文本。
- pytest 找不到文件：确认 `tests/` 目录仍存在，并且命令是在仓库根目录执行。

### 8.5 运行一个真实任务

> 参考单独的演示手册 `benchmark\selfbuilt\cases\01_todo_manager\Todo_Manager_开发团队演示手册.md` 

如果要让 Agent 从需求实现一个项目，请不要直接改 benchmark 原始模板。先复制一个演示副本。

命令：

```powershell
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$demo = "codeagent_runs\demos\todo_manager\$stamp"
New-Item -ItemType Directory -Force $demo | Out-Null
Copy-Item -Recurse benchmark\selfbuilt\cases\01_todo_manager "$demo\case"
python -m codeagent run `
  --project "$demo\case\workspace" `
  --stages implement,test,debug,repair `
  --requirements "$demo\case\input\PRD.md" `
  --requirements "$demo\case\input\user_stories.md" `
  --requirements "$demo\case\input\design_model.md" `
  --requirements "$demo\case\input\acceptance_criteria.md" `
  --output-dir "$demo\direct" `
  --model google/gemini-3.5-flash `
  --auto-approve
```

在哪里执行：仓库根目录。

这条命令做什么：复制 Todo Manager 自建案例到 `codeagent_runs/demos/...`，让 Agent 在复制出的空 `workspace` 中从四份中文材料实现 TUI 项目、生成测试、失败时调试和修复。

正常结果：运行会调用 LLM，耗时和 token 取决于模型状态。最终如果成功，会看到 `最终状态：succeeded`，并在 `$demo\direct\<run_id>\` 下看到 `final_report.md` 和各阶段产物。

常见失败：

- 缺 API Key：设置 `OPENROUTER_API_KEY`。
- 模型不可用：换 `--model` 或在配置中改模型。
- 补丁无法应用：看对应阶段的 `file_patches/`、`patch_attempts.json`、`workflow.log`。
- 测试失败后没有修复成功：看 `debugging/debug_report.md` 和 `repair/repair_report.md`，判断是产品实现问题、测试脚手架问题还是模型遗漏需求。

如果想人工审查计划和补丁，把命令里的 `--auto-approve` 去掉。普通终端会逐步提示审批；wizard 模式会使用更友好的中文 TUI。

### 8.6 使用 wizard

命令：

```powershell
python -m codeagent wizard
```

在哪里执行：仓库根目录或任意项目目录均可，但路径选择要注意。

这条命令做什么：打开中文表单，选择项目目录、阶段、输入材料、模型和审批模式，然后直接启动 Agent。

正常结果：终端出现固定表单。用方向键移动，`Enter` 编辑，`Space` 展开或选择，`Ctrl+S` 在配置有效后启动。

常见失败：

- 非 TTY 环境会退回脚本式行输入。
- 终端兼容性差时，TUI 可能显示不完整。可改用 `python -m codeagent run --config ...`。
- wizard 只负责配置和审批体验，核心工作流仍由 `execute_task_config()` 执行。

### 8.7 跑测试

不建议新人第一步就跑全量测试，因为已有报告显示全量耗时较长。建议先跑轻量命令：

```powershell
python -m pytest -q tests\test_package_smoke.py tests\test_cli_contract.py
```

在哪里执行：仓库根目录。

这条命令做什么：检查包导入和 CLI contract。

正常结果：pytest 显示少量测试通过。

如果要做合并前验证，再运行：

```powershell
python -m pytest -q
```

正常结果以当前代码为准。历史报告记录过 `397 passed, 1 skipped`，但接手后请重新记录自己的实际结果。

常见失败：

- 真实 PTY smoke 可能默认 skip，这是正常的。
- 顶层 `tools/tui_harness/` 已移除，但当前测试目录中仍可能残留 `tests/unit/tui_harness/` 和 `tests/integration/test_tui_harness_smoke.py` 对它的引用。若全量 `pytest` 因 `ModuleNotFoundError: tools.tui_harness` 失败，应同步删除或改写这些测试，而不是把它理解成核心 Agent 工作流损坏。
- Windows 上长路径或文件锁问题，看 `codeagent/filesystem.py`、`codeagent/reports/jsonl_utils.py`、`codeagent/workflow/checkpoint.py` 相关测试。
- 如果失败来自 benchmark 样例代码风格，而不是核心代码，参考 `docs/dev_reports/2026-06-06_增量补丁工作流与Todo_Manager优化合并报告.md` 中的 ruff 说明。

## 9. benchmark 设计先讲清楚

CodeAgent 的 benchmark 分两类：

1. 公开/通用 benchmark：`benchmark/benchmark.yaml`，来自 HumanEval、MBPP、QuixBugs，以及默认禁用的 BugsInPy。
2. 自建 benchmark：`benchmark/selfbuilt/selfbuilt_benchmark.yaml`，课程团队自建的 5 个普通软件项目案例。

无论哪类，核心原则都是：

- 原始 case 目录是模板，不直接修改。
- runner 先把 case 复制到干净的本次运行目录。
- Agent 只能看到 `input/` 和 `workspace/` 等可见路径。
- `evaluation/`、`oracle_tests/`、`expected_result.json` 等隐藏评测材料只给 runner/evaluator 使用。
- Agent 必须生成并运行非空自测。
- runner 最后再执行隐藏 oracle 或读取 oracle 结果判分。

### 9.1 benchmark runner 的实际流程

核心代码是 `codeagent/benchmark/runner.py`：

1. `CaseLoader` 读取 benchmark YAML。
2. 创建 benchmark run 目录，例如 `codeagent_runs/benchmarks/selfbuilt/<timestamp>_<benchmark_id>_<hash>/`。
3. 每个 enabled case 复制到 `case_workspaces/<case_id>/`。
4. 读取复制后的 case 配置。
5. 把 `project_path`、`input_materials`、可见路径、隐藏路径映射到复制后的目录。
6. 如果 test command 指向隐藏路径，runner 会把它保存为 `oracle_command`，同时给 Agent 换成安全的可见命令或让 testing 阶段生成可见自测。
7. 调用 `execute_task_config()` 执行 Agent。
8. `CaseEvaluator` 读取 Agent 自测结果，要求成功且 total > 0。
9. 如果有隐藏 oracle 命令，runner 用 `ShellRunner` 在 case 副本中执行。
10. 比较原始 case 的前后快照，确保模板没有被污染。
11. 写出 `benchmark_result.json` 和 `benchmark_report.md`。

### 9.2 成功判定

一个 case 成功通常要同时满足：

- CodeAgent workflow 的 `final_status == succeeded`。
- Agent 自测成功。
- Agent 自测 `total > 0`。
- 隐藏 oracle 没有失败；如果该 case 没有 oracle，则 oracle_success 可以是 `null`。
- 原始 case 模板没有被修改。

这比只看最终状态更严格。很多“看似成功”的运行会因为自测为空、隐藏测试失败或模板被污染而被 benchmark 判失败。

## 10. 自建 benchmark 怎么运行和演示

自建 benchmark 是当前最适合课程演示的路径，因为案例材料完整、主题普通、难度递进，且不依赖外部数据集 checkout。

### 10.1 自建案例清单

路径：`benchmark/selfbuilt/cases/`

| case_id | 项目形态 | 难度 | 说明 |
| --- | --- | --- | --- |
| `01_todo_manager` | TUI + JSON | 入门 | 待办事项管理，连续交互、持久化、过滤和校验 |
| `02_personal_ledger` | TUI + JSON | 简单 | 个人记账、流水查询、统计、编辑删除 |
| `03_student_gradebook` | TUI + JSON | 中等 | 学生、课程、成绩、排名、统计 |
| `04_library_lending` | 标准库 Web UI + SQLite | 中高 | 图书、读者、借还、库存、逾期 |
| `05_meeting_room_booking` | Flask Web UI + JSON API + SQLite | 高 | 会议室、预约、冲突检测、取消、API |

每个 case 当前都有：

```text
case.yaml
input/
  PRD.md
  user_stories.md
  design_model.md
  acceptance_criteria.md
workspace/
oracle_tests/
```

`workspace/` 在原始模板中应保持为空。`oracle_tests/` 对 Agent 隐藏。

### 10.2 推荐演示：只跑会议室单 case

命令：

```powershell
python -m codeagent benchmark --config benchmark\selfbuilt\meeting_room_demo_benchmark.yaml
```

在哪里执行：仓库根目录。

这条命令做什么：只运行 `05_meeting_room_booking` 一个自建案例。README 中把它标为推荐的低成本现场演示，因为它只跑一个 case，但能展示 Flask Web UI + JSON API + SQLite 的完整项目实现能力。

正常结果：终端最后会显示类似：

```text
Benchmark 已完成：success_rate=...
Benchmark 目录：...
```

输出目录在：

```text
codeagent_runs/benchmarks/selfbuilt/<timestamp>_codeagent_meeting_room_demo_benchmark_<hash>/
```

重点查看：

- `benchmark_report.md`
- `benchmark_result.json`
- `case_runs/05_meeting_room_booking/<run_id>/final_report.md`
- `case_runs/05_meeting_room_booking/<run_id>/workflow.log`
- `case_workspaces/05_meeting_room_booking/workspace/`

常见失败：

- 缺 `OPENROUTER_API_KEY`。
- 模型输出不稳定，导致实现不完整。
- Flask 不可用。`05_meeting_room_booking` 要求 Agent 创建 `workspace/requirements.txt`，但当前 runner 没有通用依赖安装阶段；如果本机 Python 环境没有 Flask，隐藏 oracle 可能失败。需要进一步确认最终演示环境是否预装 Flask，或补充依赖安装策略。
- 端口冲突通常由 oracle 测试处理，但如果生成应用硬编码端口或启动方式不符合材料，会失败。

### 10.3 跑完整自建 benchmark

命令：

```powershell
python -m codeagent benchmark --config benchmark\selfbuilt\selfbuilt_benchmark.yaml
```

在哪里执行：仓库根目录。

这条命令做什么：连续运行 5 个自建案例，完整评估实现、测试、调试、修复能力。

正常结果：终端会显示总体 success rate，并给出 benchmark 目录。

为什么不建议新人一开始就跑：它会调用多次 LLM，耗时和 token 成本都更高，失败时排查范围也更大。建议先跑 debug 示例，再跑一个单 case demo，最后再跑完整自建 benchmark。

常见失败：

- 任一 case 的模型输出波动。
- 交互式 TUI 的 stdout 文案不符合验收材料中的精确字符串。
- testing 阶段生成的自测脚手架有误。
- Web UI case 的 HTML 表单字段、SQLite 持久化或入口命令不符合 oracle。

### 10.4 查看自建 case 演示手册

每个自建 case 下有开发团队演示手册，例如：

- `benchmark/selfbuilt/cases/01_todo_manager/Todo_Manager_开发团队演示手册.md`
- `benchmark/selfbuilt/cases/02_personal_ledger/Personal_Ledger_开发团队演示手册.md`
- `benchmark/selfbuilt/cases/03_student_gradebook/Student_Gradebook_开发团队演示手册.md`
- `benchmark/selfbuilt/cases/04_library_lending/Library_Lending_开发团队演示手册.md`
- `benchmark/selfbuilt/cases/05_meeting_room_booking/Meeting_Room_Booking_开发团队演示手册.md`

这些手册适合准备课程展示，但仍要以当前代码行为为准。如果手册里的命令和 `codeagent/cli/app.py` 不一致，以 CLI 代码和 `README.md` 为准。

## 11. 公开/通用 benchmark 怎么运行，以及当前风险

公开/通用 benchmark 配置在：

```text
benchmark/benchmark.yaml
```

当前启用 case：

- `humaneval_000_has_close_elements`
- `humaneval_001_separate_paren_groups`
- `mbpp_002_similar_elements`
- `mbpp_003_is_not_prime`
- `quixbugs_gcd`
- `quixbugs_find_in_sorted`

默认禁用 case：

- `bugsinpy_black_001`

命令：

```powershell
python -m codeagent benchmark --config benchmark\benchmark.yaml
```

在哪里执行：仓库根目录。

这条命令做什么：运行公开/通用 benchmark 的 enabled cases。HumanEval/MBPP 主要验证函数实现和测试；QuixBugs 主要验证测试、调试、修复；BugsInPy 默认禁用，只作为 blocker 记录或后续手动启用。

正常结果：终端输出 benchmark success rate，并生成：

```text
codeagent_runs/benchmarks/public/<timestamp>_codeagent_course_benchmark_<hash>/
```

必须提醒：当前通用 benchmark 尚未完全测通，不要把它当作已经稳定交付的最终评测路径。

原因如下：

1. 历史报告中有公开 benchmark 成功记录，但本文编写时没有重跑公开 benchmark。
2. 2026-06-06 前后工作流、增量补丁、调试/修复和 benchmark 产物目录都有优化，公开 benchmark 需要重新做分层验证。
3. BugsInPy 依赖 WSL、conda、Python 3.8.3、官方 BugsInPy 脚本和真实项目 checkout，当前默认禁用。
4. SWE-bench Lite 数据集虽在 `dataset/SWE-bench_Lite/`，但当前没有接入 runner/harness。
5. 公开函数级 case 的隐藏 `evaluation/` 需要严格隔离，任何 prompt、测试命令或调试日志泄露隐藏答案都要视为评测失效。
6. 通用 benchmark 更容易暴露模型波动、环境差异和测试命令策略限制。

建议接手后的验证顺序：

1. 先跑轻量 smoke：`python -m pytest -q tests\test_package_smoke.py tests\test_cli_contract.py`，确认包导入和 CLI 正常。
2. 再从公开 benchmark 中选 1 个 HumanEval 或 MBPP case 单独临时配置运行。
3. 再跑 2 个 QuixBugs case，检查 test/debug/repair 闭环。
4. 再跑完整 `benchmark\benchmark.yaml`。
5. 最后再考虑启用 BugsInPy。

需要进一步确认：当前没有单 case public benchmark 的现成聚合 YAML。如果需要低成本验证某一个公开 case，可以复制 `benchmark/benchmark.yaml` 到临时文件，只保留一个 case，再运行临时配置。临时配置应放在 `codeagent_runs/verify/` 或其他忽略目录，不要改坏原始 benchmark 配置。

### 11.1 BugsInPy 可选路径

BugsInPy case 路径：

```text
benchmark/cases/bugsinpy_black_001/
```

相关脚本：

```text
scripts/setup_bugsinpy_wsl_conda.ps1
scripts/prepare_bugsinpy_wsl_conda.ps1
scripts/run_bugsinpy_wsl_conda.ps1
```

环境准备命令：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_bugsinpy_wsl_conda.ps1
```

在哪里执行：仓库根目录。

这条命令做什么：在 WSL 中安装或复用 Miniconda，创建 `codeagent-bugsinpy-py383` conda 环境，安装 Python 3.8.3、pip/setuptools/wheel 和 dos2unix。

正常结果：脚本打印 Python 版本和 dos2unix 版本。

常见失败：

- 本机没有 WSL。
- WSL 中没有 curl。
- conda 下载失败。
- 网络限制。

准备 case 副本的命令示例：

```powershell
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$case = "codeagent_runs\verify\bugsinpy_black_001_$stamp\case"
Copy-Item -Recurse benchmark\cases\bugsinpy_black_001 $case
powershell -ExecutionPolicy Bypass -File scripts\prepare_bugsinpy_wsl_conda.ps1 -CaseDir $case -WslCommandTimeoutSeconds 1200
```

这条命令会在副本的 `workspace/black/` 下执行 BugsInPy 官方 checkout。不要对原始 `benchmark/cases/bugsinpy_black_001/` 直接 prepare。

运行官方测试：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_bugsinpy_wsl_conda.ps1 -CaseDir $case -AllowTestFailure -WslCommandTimeoutSeconds 1200
```

这条命令用于验证初始 buggy 版本是否按预期失败。Agent 修复后去掉 `-AllowTestFailure`，期望相关测试通过。

需要进一步确认：BugsInPy 当前已经有环境检测和 blocker 记录逻辑，但真实修复流程成本高、依赖多，不建议作为现场主演示。它更适合作为后续扩展能力和高难度真实项目案例。

## 12. 常见问题和排错方法

### 12.1 `Missing model API key`

现象：运行 implement/test/repair 或 benchmark 时提示缺少模型 API key。

原因：没有设置 `OPENROUTER_API_KEY`，或当前终端没有读到用户级环境变量。

处理：

```powershell
[Environment]::SetEnvironmentVariable("OPENROUTER_API_KEY", "<your-key>", "User")
```

然后重新打开终端，再运行：

```powershell
$env:OPENROUTER_API_KEY
```

如果能看到值，说明当前 shell 能读取。不要把 key 写入配置、文档或 Git。

### 12.2 模型不可用、输出 malformed、schema 校验失败

现象：stage result 里出现 model error，或 `llm_calls/` 下 validation 失败。

排查：

1. 看对应阶段的 `llm_calls/<call_id>/call_summary.md`。
2. 看 `attempt_*/response.raw.txt` 和 `validation.json`。
3. 看 `workflow.log` 中的 model/schema 失败摘要。
4. 尝试换模型或提高 `max_retries`。

常见原因：

- 模型名在当前 OpenRouter 账号下不可用。
- 响应没有 JSON 对象。
- JSON 符合语法但不符合 Pydantic schema。
- 输出引用了隐藏路径或越界路径，被后置校验拒绝。

### 12.3 patch 无法应用

现象：implementation/testing/repair 阶段失败，提示 patch validation 或 context mismatch。

排查：

- 看阶段目录下的 `*.patch.diff`。
- 看 `patch_attempts.json` 或 `file_patches/`。
- 看 `workflow.log` 中 patch validation 失败原因。
- 确认 LLM 的 `old_content` 是否和当前项目文件完全一致。

常见原因：

- 运行过程中项目文件被手动改过。
- LLM 读取到的上下文过期。
- 补丁路径包含 `..`、绝对路径或隐藏目录。
- 目标文件已经存在，但补丁以新增文件方式写入。

### 12.4 shell 命令被拒绝

现象：测试或修复阶段提示 command not allowed。

原因：`ShellRunner` 只允许安全测试命令。

处理：

- 优先使用 `python -m pytest -q`。
- 或使用 `python -m unittest discover -s tests`。
- 不要在普通任务的 test command 中写 `pip install`、`powershell`、`npm`、管道、链式命令。

如果确实需要安装依赖，目前建议在运行 Agent 前由开发者准备环境，而不是让 Agent 的测试命令安装。

### 12.5 测试显示成功但 benchmark 仍失败

可能原因：

- Agent 自测 total 为 0。
- 自测只是 `py_compile`，没有真实测试。
- 隐藏 oracle 失败。
- 原始 case 模板被污染。
- `final_status` 不是 `succeeded`。

排查：

- 看 `benchmark_result.json` 中该 case 的字段：
  - `agent_test_success`
  - `agent_test_total`
  - `oracle_success`
  - `source_unchanged`
  - `failure_reason`
- 看该 case 的 `case_runs/<case_id>/<run_id>/testing/test_result.json`。
- 看 `oracle_logs/<case_id>/`。

### 12.6 自建 Web/Flask case 失败

可能原因：

- Agent 没有创建正确 package。
- `python -m <package>` 入口不符合 case.yaml。
- Flask 没安装。
- `create_app(db_path=None)` 函数缺失或签名不对。
- SQLite 文件路径、API JSON 字段、HTML 表单名称和验收材料不一致。

处理：

- 看 case 的 `input/acceptance_criteria.md`，很多字段是精确契约。
- 看隐藏测试失败日志。
- 如果是 Flask 依赖问题，需要进一步确认运行环境依赖安装策略。

### 12.7 workflow_events.jsonl 出现坏行

历史优化中已经加固 JSONL 写入，但旧运行产物中可能有坏行。

处理：

- 新运行优先看新的 `workflow.log`。
- 如需修复旧产物，查 `codeagent/reports/jsonl_utils.py` 相关工具函数。
- 不要直接手动编辑运行产物再当作正式证据，除非明确标记为 repaired copy。

### 12.8 resume 不符合预期

命令：

```powershell
python -m codeagent resume --run-id <run_id>
```

默认输出运行摘要。如果要恢复 pending interrupt，需要提供：

```powershell
python -m codeagent resume --run-id <run_id> --decision-json '{"interrupt_id":"...","decision_type":"approve"}'
```

常见问题：

- run_id 不在默认 `codeagent_runs` 下，需要加 `--output-root`。
- 该 run 已经完成，没有 pending interrupt，只能查看产物。
- decision JSON 的 interrupt_id 不匹配当前 pending interrupt。

resume UX 仍有优化空间，后续建议提供决策模板输出。

## 13. 哪些历史文档可能已经过期

以下文档仍有参考价值，但读的时候要带着“实现可能已经演进”的意识：

- `docs/analysis/《基于大语言模型的软件工程智能体》需求规格说明书_v0.1.md`：需求层文档，很多方向仍成立，但当前实现已经经历 M28 和 OPT 系列优化。
- `docs/design/`：设计文档包，架构原则仍有价值；其中“阶段子图”“工具级 HITL”等表述和当前代码不完全一一对应。当前主图由 CLI 注入 stage handler，阶段服务承担大量实际逻辑。
- `docs/codex/plans.md`：项目搭建过程记录，内容太多，不建议新人细读。查里程碑来源时再看。
- `docs/dev_reports/`：开发过程记录，很多报告是阶段快照。后续优化可能改变命令、目录或行为。
- `docs/test/benchmark样例整理报告.md`：里面曾建议 runner 根据 `expected_result.json` 判分；当前 `CaseEvaluator` 更关注 Agent 自测、隐藏 oracle、source snapshot 和 final_status。

遇到不一致时，优先看：

1. 当前代码。
2. 当前 YAML 配置。
3. 最近的优化报告。
4. 最后才看早期需求和设计文档。

## 14. 后续工作建议

### 14.1 立刻需要接手确认的事项

1. 重新跑轻量 smoke，记录当前环境结果。
2. 重新跑至少一个自建单 case benchmark，建议先用 `meeting_room_demo_benchmark.yaml` 或临时单 case Todo Manager。
3. 按分层策略重新验证公开/通用 benchmark，明确哪些 case 当前稳定、哪些失败。
4. 确认 OpenRouter 默认模型和候选模型是否仍可用。
5. 确认 Flask case 的依赖安装策略。当前项目依赖里没有 Flask，Agent 生成 `requirements.txt` 并不等于 runner 会自动安装。
6. 同步清理移除 `examples/` 和顶层 `tools/tui_harness/` 后的残留引用，例如 README 示例和仍导入 `tools.tui_harness` 的测试文件。

### 14.2 工程功能待优化

- 完善 resume UX：输出 pending interrupt 模板，降低手写 JSON 的门槛。
- 完善单 case benchmark 命令：支持 `--case-id` 或 `--only`，避免复制 YAML。
- 增加依赖准备阶段：尤其是自建 Flask case 和真实项目 benchmark。
- 增强模型 fallback：模型不可用时自动尝试候选模型，而不是只报错。
- 增强 token/cost 统计：目前 LLM call bundle 有 prompt/response 字符数，但没有统一成本报告。
- 增强 HTML/可视化报告：benchmark 成功率、阶段耗时、失败类别可以做成更适合答辩展示的报告。
- 梳理全仓 ruff 策略：决定是否 exclude benchmark oracle/evaluation 样例，或统一修复风格问题。

### 14.3 benchmark 待推进

- 通用 benchmark 重新测通并固化结果。必须明确当前通过率、失败 case、失败原因和模型版本。
- BugsInPy 从 optional blocker 走向稳定可运行，需要固定 WSL/conda 镜像、依赖缓存和超时策略。
- SWE-bench Lite 当前只有数据集，没有接入 harness。若要扩展，需要专门设计 Docker/checkout/evaluation 流程。
- 自建 5 case 应持续用同一模型跑回归，观察是否有模型波动导致的偶发失败。
- 如果后续重新引入独立的 TUI 驱动工具，可以再考虑把 Todo、Ledger、Gradebook 的交互验收从普通 subprocess/stdin 迁移到更稳定的屏幕驱动方式。

### 14.4 安全和隔离待增强

- 当前没有生产级沙箱。ShellRunner 做了命令白名单和路径限制，但不是容器隔离。
- LLM 生成代码和第三方项目测试仍可能执行不可信代码。更强方案是 Docker、临时用户、资源限制和网络隔离。
- Benchmark 隐藏路径隔离已有多处校验，但后续新增 case 时仍要手动检查 prompt、命令和日志是否可能泄露 oracle。
- Git 工作区检查仍不是主能力。运行 Agent 前建议团队自己确认工作区状态，避免误改未提交内容。

### 14.5 课程交付待确认

- IDE 集成是否仍是硬性要求。当前实现主要是 CLI，不含 VSCode 插件或 LSP。
- 最终报告中应说明框架边界：LangGraph/LangChain 提供基础编排和模型抽象；项目自主实现配置、阶段服务、patch-first、HITL、报告和 benchmark。
- 最终演示建议选一条自建 case 作为主线，再用已有报告展示公开 benchmark 和测试覆盖。

## 15. 接手第一天建议执行清单

下面是一条务实路线：

1. 拉取/打开仓库后先看 `README.md` 和本文档。
2. 执行 `python -m pip install -e ".[dev]"`。
3. 执行 `python -m codeagent --help`。
4. 执行 `python -m pytest -q tests\test_package_smoke.py tests\test_cli_contract.py`。
5. 设置 `OPENROUTER_API_KEY`。
6. 复制一个 Todo Manager demo 副本，用 `python -m codeagent run ... --auto-approve` 跑一次。
7. 看最新生成的 demo run 目录里的 `final_report.md` 和 `workflow.log`。
8. 如果有时间，再跑 `python -m codeagent benchmark --config benchmark\selfbuilt\meeting_room_demo_benchmark.yaml`。
9. 记录实际成功/失败结果，更新交接后的验证记录。
10. 再开始改代码。

这样做的好处是，你会先确认最小 CLI 和测试入口，再见到一次真实 LLM run、一次 benchmark run 和完整运行产物。理解这些之后，再看 `codeagent/cli/executor.py` 和四个 stage service 会轻松很多。

## 16. 最后总结

CodeAgent 当前已经不是一个简单 demo，而是一个具备 CLI、结构化 LLM 输出、LangGraph 路由、patch-first、HITL、checkpoint、审计日志和 benchmark runner 的课程级软件工程智能体。

接手时请抓住三条主线：

第一，所有任务都从 `TaskConfig` 进入，最终由 `execute_task_config()` 启动 LangGraph。

第二，所有项目改动都应遵循“计划 -> 审批 -> 补丁草案 -> 校验 -> 审批 -> 应用 -> 验证 -> 报告”的路径。

第三，benchmark 的可信度来自隔离：原始 case 不改，隐藏 oracle 不给 Agent，自测必须非空，runner 最后单独判分。

后续最重要的工作不是再写一堆新功能，而是把当前能力重新验证、固化演示路径、补齐通用 benchmark 风险，并让团队每个人都能根据运行产物解释一次 Agent 到底做了什么。
