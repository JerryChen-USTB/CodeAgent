# OPT01 审批测试解耦与工作流追踪日志汇报

## 1. 问题背景

用户在 Todo Manager 半交互演示产物中发现两个问题：

- `decision_trace.jsonl` 记录了 `"auto": false, "type": "human_decision"`，但运行过程中没有出现人工审批提示。
- implementation 阶段生成了 `tests/test_todo.py`，testing 阶段只做轻微复用，两个阶段职责没有清晰分离。

同时，原有 `transcript.jsonl` 和阶段报告能说明结果，但不能按工作流顺序完整复盘每个节点、LLM 调用、审批、状态转移和命令执行过程。因此本次优化新增 `workflow.log` 与 `workflow_events.jsonl`。

## 2. 根因分析

审批记录问题的根因不是单个字段写错，而是执行链路语义不清：

- CLI 主流程直接调用阶段服务，`PlanGenerationService` 预先构造了 `ApprovalDecision(approve)`。
- `auto=false` 只表示“不是 benchmark 自动审批”，不表示“用户真的审批过”。
- `type=human_decision` 是旧报告 schema 名称，实际承载的是审批决策事件。

testing 解耦问题的根因是 implementation prompt 和目标校验没有禁止测试文件。模型会把“完整项目”理解为包含业务代码和测试，因此提前生成了 `tests/test_todo.py`。

## 3. 实现改动

### 3.1 wizard 审批模式

`codeagent wizard` 新增审批模式字段：

- 默认：`manual`，开启人工审批。
- 可选：`auto`，关闭人工审批并自动批准方案、补丁和命令。

该字段写入 `task_config.yaml`：

```yaml
permissions:
  approval_mode: manual
```

自动审批不会伪装成人工审批。记录中会明确写出来源：

- `decision_source=user`
- `decision_source=user_configured_auto`
- `decision_source=benchmark_auto`

### 3.2 审批记录修正

`ApprovalDecision` 和 `HumanDecision` 增加字段：

- `event_type=approval_decision`
- `decision_source`
- `presented_to_user`
- `decided_by`

兼容保留 `type=human_decision`，避免破坏旧报告和测试，但新字段用于准确解释事件含义。

### 3.3 implementation/testing 解耦

implementation 阶段新增双重约束：

- prompt 明确要求只生成业务/源码文件，不生成测试文件。
- 结构化计划校验拒绝 `tests/**`、`test_*.py`、`*_test.py`、`conftest.py`、`pytest.ini` 等测试产物路径。

testing 阶段新增质量校验：

- 测试补丁必须包含真正的 pytest/unittest 测试用例。
- 空测试包、helper 文件、仅复用已有测试都不能算完成 testing 阶段。
- 非法测试路径仍由 patch 校验报告，不会被质量校验遮蔽。

### 3.4 workflow.log

每个 run 新增：

- `workflow.log`：面向人阅读的完整工作流追踪日志。
- `workflow_events.jsonl`：机器可读事件流。

记录内容包括：

- run 初始化信息。
- LangGraph 节点完成、路由决策、最终状态。
- LLM prompt、LLM response、通过校验后的结构化计划。
- 审批请求和审批结果。
- testing 命令执行开始/结束、stdout/stderr 日志路径、退出码。
- stage finalize 结果、artifact ids、changed files、测试统计。

安全边界：

- API Key、Bearer token、password、secret 等会脱敏。
- `oracle_tests`、`evaluation`、`expected_result.json` 等隐藏 benchmark 材料会脱敏。
- 不记录模型隐藏思维链，只记录可审计输入、输出和产物。

### 3.5 benchmark 最终自测结果修正

真实 LLM 单 case 验证时发现一个后续问题：Todo Manager 的 testing 阶段先出现 2 个失败用例，随后 debugging/repair 阶段修复成功，隐藏 oracle 也通过；但 benchmark 聚合报告仍读取 `testing/test_result.json`，因此误判 `agent self-test failed`。

本次已修正 evaluator：

- 优先读取 `repair/repair_test_result.json` 作为最终 Agent 自测结果。
- 若没有 repair 结果，再回退读取 `testing/test_result.json`。
- 新增回归测试覆盖“testing 失败但 repair 验证通过时，benchmark case 应成功”的场景。

## 4. 验证情况

已运行：

```powershell
python -m compileall -q codeagent
python -m pytest tests/integration/test_cli_wizard.py tests/integration/test_testing_stage.py tests/unit/reports/test_writer.py -q
python -m pytest tests/unit/agents/test_plan_generation.py tests/unit/runtime/test_run_context.py tests/integration/test_cli_wizard.py tests/integration/test_cli_run.py tests/integration/test_testing_stage.py tests/unit/reports/test_writer.py -q
python -m pytest tests/integration/test_benchmark_runner.py -q
python -m pytest tests/unit -q
python -m pytest tests/integration/test_cli_wizard.py tests/integration/test_cli_run.py tests/integration/test_testing_stage.py tests/integration/test_benchmark_runner.py -q
python -m pytest -q
```

结果：

- 语法检查通过。
- 相关旧测试和新增回归测试通过。
- `83 passed` 覆盖计划生成、runtime、wizard、CLI run、testing stage、report writer。
- 完整 unit 测试通过：`187 passed`。
- 重点集成测试通过：`64 passed`。
- 全量测试通过：`310 passed`。
- 真实 OpenRouter 单 case Todo Manager benchmark 已运行一次。该次运行证明真实 LLM 能按新规则让 implementation 只生成业务代码、testing 生成 57 个自测并触发 debug/repair；同时暴露并推动修复了 benchmark evaluator 对 repair 后最终自测结果的误判。为节省 token，修复后未再次调用真实 LLM 全量重跑，而是用 benchmark runner 回归测试验证 evaluator 行为。

## 5. 用户如何检查

运行完成后进入 run directory，重点看：

```text
workflow.log
workflow_events.jsonl
decision_trace.jsonl
task_config.yaml
implementation/implementation_plan.md
testing/test_plan.md
testing/test_result.json
```

判断审批是否真实：

- 人工审批：`auto=false`、`decision_source=user`、`presented_to_user=true`。
- wizard 关闭审批：`auto=true`、`decision_source=user_configured_auto`、`presented_to_user=false`。
- benchmark：`auto=true`、`decision_source=benchmark_auto`、`presented_to_user=false`。

判断 testing 是否独立发挥作用：

- implementation plan 不应包含测试文件。
- testing plan 应包含测试文件变更。
- `testing/test_result.json` 中 `total` 必须大于 0。

## 6. 后续注意事项

- 旧 run 目录不会被自动迁移；新字段和 `workflow.log` 只出现在后续 run 中。
- 真实 LLM 可能仍偶尔生成不合格测试计划，系统现在会失败并要求重新生成，而不是静默降级。
- 后续优化继续登记在 `docs/optimization/优化任务看板.md`，不再追加到 `docs/codex/plans.md`。
