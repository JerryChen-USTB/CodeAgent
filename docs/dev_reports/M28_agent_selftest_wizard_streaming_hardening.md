# M28 Agent 自测、中文 Wizard 与流式运行体验加固汇报

## 1. 背景与问题

本轮收尾来自实际体验反馈，核心问题不是单个 bug，而是产品完成度不足：

- benchmark 的 Agent 可见测试可能只跑语法检查，出现 `0 passed, 0 failed, 0 errors, 0 skipped` 仍被视为成功。
- 半交互 wizard 是英文填空流程，用户需要先生成配置，再手动运行 `run --config`。
- CLI 进度输出主要来自 LangGraph 节点完成事件，阶段内部 LLM 调用、补丁生成、命令执行期间缺少实时说明。
- benchmark 报告没有清晰区分 Agent 自己的公开自测和 runner-only 隐藏 oracle。
- self-built case 的隐藏 oracle 超时较短，曾被复用到 Agent 自测，导致大量已收集测试被 15 秒超时中断并误解为 0 tests。
- OpenRouter 默认最大输出预算过高时，部分大 case 可能因为服务商预算预占而被拒绝。

这些问题会削弱课程展示中的可信度：系统看起来能完成 benchmark，但用户无法确认 Agent 是否真的设计、生成并运行了自己的测试。

## 2. 实现改造

### 2.1 Agent 自测链路

`PlanGenerationService` 新增 `create_testing_request()`，通过 OpenRouter LLM 生成结构化 `TestingPlan`。该计划包含测试目标、策略、验收条件、测试文件变更和测试命令。

CLI executor 的 testing 阶段现在调用完整 `TestingService`：

1. 生成测试方案。
2. 生成测试补丁。
3. 校验测试补丁只能写入 `tests/` 或 `test_*.py`。
4. 应用测试补丁。
5. 执行 Agent 自测命令。
6. 解析并写入 `test_result.json` / `test_report.md`。

隐藏 oracle 仍只由 benchmark evaluator 在最后运行，不进入 Agent prompt，也不会作为 Agent 自测命令。

### 2.2 0 测试失败规则

TestingService 增加非零测试验证：

- `total == 0` 且命令成功时失败。
- unittest 输出 `Ran 0 tests` 或 `NO TESTS RAN` 时失败。
- pytest 输出 `collected 0 items` 时失败。
- `py_compile` 只能作为实现阶段语法检查，不能作为 testing 阶段成功证据。

因此 `0 passed` 不再可能作为 benchmark 成功路径通过。

### 2.3 Agent 自测超时与 oracle 超时拆分

benchmark case 原有 `test_command.timeout_seconds` 继续作为 runner-only oracle 评分预算。若配置命令引用隐藏路径，BenchmarkRunner 会把 Agent 可见命令替换为安全命令，并把 Agent 自测超时提升到运行时默认值 `120` 秒。

这样可以保留 oracle 的原始评分规则，同时避免 Agent 生成的公开测试因为 oracle 的短超时被提前杀掉。benchmark evaluator 对自测超时会报告 `agent self-test timed out`，并尽量从 pytest 日志中的 `collected N items` 补充已收集测试总数。

### 2.4 Wizard 交互重构

`codeagent wizard` 改为中文表单：

- 阶段选择为选择题。
- 输入材料支持多选。
- 项目路径、输出目录和测试命令保留文本输入。
- 确认任务摘要后直接启动 Agent。

运行目录仍会保存 `task_config.yaml`、metadata、checkpoint、阶段报告和最终报告，所以直接运行不影响可复现性。

### 2.5 中文进度与流式输出

主图执行改为请求 `stream_mode=["updates", "custom", "messages"]`。新增 `emit_progress()` 用于从阶段节点内部写入 LangGraph custom stream。

CLI 现在可以显示：

- 当前阶段开始。
- LLM 正在生成哪类结构化计划。
- 测试补丁生成和应用。
- shell 测试命令开始执行。
- 测试结果计数。
- 最终状态和运行目录。

输出改为中文，例如 `[测试阶段] 正在根据公开需求、实现产物和可见源码设计自测用例`。

CLI help、配置错误、wizard 路径错误和审批解析错误也同步中文化；`resume` help 中旧的 “Planned skeleton” 文案已移除。Typer 内置 `--help` 描述仍由框架生成，暂保留英文。

### 2.6 Benchmark 报告增强

`CaseEvaluation` 增加：

- `agent_test_success`
- `agent_test_total`
- `agent_test_command`
- `agent_test_report`

benchmark 成功条件现在同时要求：

1. Agent 工作流 `final_status=succeeded`。
2. Agent 可见自测成功且测试总数大于 0。
3. runner-only 隐藏 oracle 未失败。
4. 原始 case 模板未被污染。

为降低课程现场演示成本，新增 `benchmark/selfbuilt/meeting_room_demo_benchmark.yaml`，只启用会议室预约系统一个 case。当前该 case 是 Flask Web UI + JSON API；完整 `selfbuilt_benchmark.yaml` 仍用于最终验收或显式回归。

### 2.7 模型预算与错误脱敏

默认 `ModelConfig.max_tokens` 从按模型默认改为 `16384`，避免 OpenRouter 对 Claude Sonnet 请求 65536 输出 token 预算导致配额不足。该值仍可在 YAML/JSON 任务配置中覆盖。

新增共享脱敏工具，模型错误、计划生成尝试审计和最终报告会隐藏 OpenRouter key 管理链接、用户标识、Bearer/API key/token/password 等敏感信息，避免把服务商返回的账户关联 URL 写入演示产物。

## 3. 测试与验证

新增或更新的回归测试覆盖：

- LLM testing request schema 生成。
- testing prompt 不包含隐藏 oracle 内容。
- 0 测试结果不能成功。
- LangGraph 多模式 stream event 归一化。
- wizard scripted backend 直接启动 Agent。
- benchmark 聚合报告包含 Agent 自测字段。

已运行的关键验证：

- `python -m py_compile ...`：核心改动文件语法检查通过。
- `python -m pytest tests\integration\test_cli_wizard.py -q`：11 passed。
- `python -m pytest tests\integration\test_cli_run.py -q`：9 passed。
- `python -m pytest tests\integration\test_benchmark_runner.py -q`：17 passed。
- `python -m pytest tests\unit\agents\test_plan_generation.py tests\unit\workflow\test_routing.py -q`：26 passed。
- `python -m pytest tests\integration\test_testing_stage.py::test_testing_service_rejects_zero_collected_tests -q`：passed。
- `python -m pytest tests\unit -q`：185 passed。
- `python -m pytest tests\integration\test_testing_stage.py tests\integration\test_benchmark_runner.py tests\integration\test_cli_wizard.py tests\integration\test_cli_run.py -q`：55 passed。
- `python -m pytest -q`：303 passed。
- `python -m compileall -q codeagent tests`：通过。
- `python -m codeagent --help`、`python -m codeagent benchmark --help`、`python -m codeagent wizard --help`、`python -m codeagent resume --help`：通过，主要说明文本已中文化。
- 真实 OpenRouter 低成本 benchmark：只运行 `01_todo_manager` 一个 self-built case，`success_rate=1.00 (1/1)`；Agent 自测 `43 passed`，`agent_test_total=43`，隐藏 oracle `oracle_success=True`，原始 case `source_unchanged=True`。

## 4. 已知限制

- 真实 LLM 生成测试仍可能受模型输出波动影响；系统通过 schema 校验、隐藏路径校验、0 测试失败规则和 benchmark oracle 降低风险。
- questionary 仅在真实 TTY 中提供方向键选择；非 TTY 环境使用脚本化 fallback，以保证自动化测试可运行。
- CLI 已能输出阶段内 custom 事件，但还不是 token-by-token 展示模型内容；这是有意限制，避免展示隐藏思维链或大段 JSON。
- 出于 token 和时间成本控制，本轮真实 self-built benchmark 最终只重跑一个代表 case；全量历史 self-built 通过记录仍保留在 M26/M27，后续需要全量验收时再显式执行。

## 5. 后续建议

- 为 testing prompt 增加更多语言/框架模板，目前重点仍是 Python pytest/unittest。
- 在 benchmark 聚合报告中进一步统计 Agent 自测覆盖类型，例如正常、异常、边界场景。
- 将 wizard 的表单配置抽象为可扩展 schema，便于后续支持 Web/TUI 前端。
