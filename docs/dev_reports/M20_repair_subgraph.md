# M20 RepairSubgraph 开发报告

## 目标

M20 实现修复阶段子图与修复阶段服务，支持最终修复计划、最小 repair patch、风险检查、patch 审批与应用、回归命令审批、修复后测试验证、修复报告和主图多轮修复路由。该能力对齐 SRS FR-57~FR-65、AC-11、UC-05，以及设计文档中修复阶段的 patch-first、HITL、风险检查和回归验证要求。

## 主要变更

- `codeagent/stages/repair_service.py`：新增 `RepairService`、`RepairPlan`、`RepairFileChange`、`RepairRequest`，封装修复计划持久化、repair patch 生成、风险检查、patch 审批、patch 应用、回归命令审批、测试执行、结果解析和修复报告。
- `codeagent/workflow/subgraphs/repair.py`：新增确定性修复阶段 handler、基础修复子图和带 patch/command interrupt 的修复子图。
- `codeagent/tools/risk_checker.py`：新增 `RepairRiskChecker`，将测试文件、测试基础设施、skip/xfail、硬编码和测试断言删除等风险纳入高风险检查。
- `tests/integration/test_repair_stage.py`：覆盖成功修复、风险补丁拒绝、敏感/隐藏目标 fail-closed、回归命令拒绝、隐藏 benchmark 命令拒绝、修复后测试失败、主图重试上限、独立子图、SQLite resume 和 patch hash 防篡改。
- `tests/unit/tools/test_shell_runner.py`：将长输出非超时场景的 timeout 放宽，避免负载下误判；专门的超时测试仍覆盖超时行为。

## 关键设计决策

- 修复阶段保持 patch-first：所有源码修改先生成 `repair.patch.diff`，审批通过后才应用。
- 被拒绝的敏感/隐藏 repair 目标在 diff 生成前 fail closed，不写 `repair_patch_attempt_*.diff` 或最终 `repair.patch.diff`，避免把隐藏 benchmark 或 secret-like 内容落入运行产物。
- 风险检查更保守：修复 patch 不允许修改测试文件或测试基础设施，如 `conftest.py`、`pytest.ini`、`tox.ini`、`setup.cfg`、`pyproject.toml`。
- patch 审批 payload 包含 `patch_sha256`，应用前重新计算 hash，防止审批后 diff 被替换。
- 回归命令审批支持 approve/edit/reject/cancel，并拒绝 `evaluation`、`oracle_tests`、`expected_result.json` 等隐藏 benchmark 参数。

## 使用方式

确定性 handler：

```python
service = RepairService(run_context=run_context)
handler = create_repair_stage_handler(
    service=service,
    request_builder=lambda state: repair_request,
)
subgraph = build_repair_subgraph(handler)
```

带 interrupt/resume 的修复子图：

```python
subgraph = build_interrupting_repair_subgraph(
    service=service,
    request_builder=lambda state: repair_request,
    checkpointer=saver,
)
```

## 验证结果

- `python -m pytest tests\integration\test_repair_stage.py -q`：16 passed。
- `python -m pytest tests\integration\test_repair_stage.py tests\integration\test_debugging_stage.py tests\integration\test_testing_stage.py tests\integration\test_implementation_stage.py tests\integration\test_resume.py tests\unit\workflow tests\unit\tools tests\unit\reports -q`：138 passed。
- `python -m pytest -q`：213 passed。
- `python -m codeagent --help`：通过，CLI help 正常输出。
- `codeagent --help`：通过，CLI help 正常输出。

## 审查结果

- 规格复核：PASS。此前发现的隐藏 benchmark repair 目标读取/落盘风险已修复。
- 质量复核：APPROVED。此前发现的敏感/隐藏目标 candidate diff 落盘风险和测试基础设施未标高风险问题均已修复。

## 已知限制与后续工作

- 当前修复计划由测试注入的结构化 `RepairPlan` 驱动；后续 LLM Repairer 节点可生成该 schema。
- 当前风险检查以路径和 diff 模式为主；后续 benchmark 迭代中可加入更强的过拟合检测。
- M21 之后需要把这些 interrupt payload 接入 CLI wizard/approval UI。
