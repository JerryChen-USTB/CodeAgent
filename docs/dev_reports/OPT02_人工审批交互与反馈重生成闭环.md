# OPT02 人工审批交互与反馈重生成闭环汇报

## 1. 问题背景

用户在 Todo Manager 半交互运行中发现两个体验和流程问题：

- 在审查测试计划时选择 `respond` 后，系统把它当成 testing 阶段失败，随后进入 debugging/repair，表现为“我明明是在提修改意见，为什么突然变成测试失败并开始修复”。
- implementation 阶段生成 `ImplementationPlan` 后没有清晰的计划审批环节，终端直接进入“生成、校验并应用实现补丁”，用户无法判断自己审批的是计划还是补丁。

同时，原审批提示仍使用英文命令式输入，如 `approve/edit/reject/respond/cancel`，不符合 wizard 已经中文表单化后的使用体验。

## 2. 根因分析

根因分为三类：

1. `respond` 的语义没有闭环。它被解析为合法审批决策，但阶段服务收到非 `approve` 后统一生成失败结果，主图便按“测试失败”进入 debug/repair。
2. implementation 阶段只有补丁审批，没有单独的计划审批。虽然 `implementation_plan.md` 会落盘，但用户没有机会在生成 patch 前审查计划。
3. 审批控制台仍是旧式文本输入，既不直观，也容易让用户误解 `respond`、`reject`、`cancel` 的含义差异。

## 3. 修复内容

### 3.1 计划审批只保留两个选项

实现计划和测试计划的人工审批选项收敛为：

1. 同意当前计划，继续下一步。
2. 不同意，输入修改意见，让 Agent 重新生成计划。

因此 `review_implementation_plan` 和 `review_test_plan` 的审批 payload 现在只暴露：

```json
["approve", "respond"]
```

`reject/cancel/edit` 不再出现在计划审批里。它们仍保留在补丁审批、命令审批等副作用审批中。

### 3.2 implementation 阶段新增计划审批点

implementation 阶段现在分为两步：

1. LLM 生成 `ImplementationPlan`，系统写入 `implementation/implementation_plan.md` 和 `implementation/implementation_plan.json`。
2. 人工审批计划。只有同意后，系统才会生成、校验并应用实现补丁。

如果用户选择“提出修改意见”，执行器会把意见写入下一轮 LLM prompt，重新生成 `ImplementationPlan`。

### 3.3 testing 阶段修复 `respond` 路由

testing 阶段计划审批选择“提出修改意见”后：

- 记录到 `decision_trace.jsonl`，`decision_type=respond`。
- 记录到 `workflow.log`，事件为 `approval_feedback_regeneration`。
- 调用 LLM 重新生成 `TestingPlan`。
- 不写入 testing 失败结果。
- 不进入 debugging/repair。

测试补丁审批中的 `respond` 也会重新生成测试方案，而不是被误判为阶段失败。

### 3.4 审批 UI 中文选择题化

人工审批控制台改为中文选择题：

- 使用上下键移动，回车选中。
- 计划审批只显示“批准并继续”和“提出修改意见”。
- 选择“提出修改意见”后，必须输入具体意见。
- 非交互或测试环境仍保留脚本化输入后端，便于自动测试。

## 4. 验证情况

已运行：

```powershell
python -m compileall -q codeagent
python -m pytest tests/integration/test_cli_wizard.py tests/integration/test_cli_run.py tests/integration/test_implementation_stage.py tests/integration/test_testing_stage.py -q
python -m pytest tests/integration/test_cli_wizard.py tests/integration/test_cli_run.py tests/integration/test_implementation_stage.py tests/integration/test_testing_stage.py tests/integration/test_repair_stage.py tests/integration/test_benchmark_runner.py -q
```

结果：

- 语法检查通过。
- 人工审批 UI、ImplementationPlan 审批、TestingPlan 反馈重生成、implementation/testing 阶段服务和 benchmark runner 相关测试通过。
- 已覆盖“测试计划选择 respond 后重新生成测试方案且不进入 debug/repair”的回归场景。
- 已覆盖“实现计划审批 payload 只允许 approve/respond”的回归场景。
- 已覆盖“测试计划审批 payload 只允许 approve/respond”的回归场景。

## 5. 仍需注意

- 旧 run 目录不会被迁移。历史 run 中的审批记录和当前版本语义可能不同。
- 真实 LLM 的反馈重生成质量仍取决于提示词和模型输出，系统现在会保留完整 workflow 日志，便于继续迭代 prompt。
- 为控制 token 成本，本次没有重新跑全量自建 benchmark；需要真实 LLM 验证时建议只跑 Todo Manager 或 Meeting Room 单 case。
