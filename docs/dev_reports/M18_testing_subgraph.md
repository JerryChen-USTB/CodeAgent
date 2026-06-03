# M18 TestingSubgraph 开发报告

## 目标

M18 实现测试阶段子图与测试阶段服务，支持测试方案审核、测试 patch 审批、测试命令审批、执行 pytest/unittest 命令、解析测试结果并写入报告。该能力对齐 SRS 中测试阶段输入输出、人工确认、副作用操作审批和全过程产物留存要求。

## 主要变更

- `codeagent/stages/testing_service.py`：新增 `TestingService`、`TestingPlan`、`TestFileChange`、`TestingRequest`，封装测试方案持久化、测试 patch 生成与校验、HITL 决策处理、命令审批、测试执行、结果解析和阶段报告。
- `codeagent/workflow/subgraphs/testing.py`：新增确定性测试阶段 handler、基础测试子图和带 interrupt 的测试子图，显式建模方案审核、patch 审批和命令审批。
- `tests/integration/test_testing_stage.py`：覆盖成功/失败测试执行、命令拒绝与编辑、隐藏 benchmark 路径保护、patch hash 防篡改、SQLite resume 下的 plan/patch/command interrupt 流程。
- `codeagent/stages/__init__.py`、`codeagent/workflow/subgraphs/__init__.py`：导出 M18 新增服务与子图入口。

## 关键设计决策

- 测试文件变更必须走 patch-first：服务只允许测试 patch 修改 `tests/` 或 `test_*.py` 风格路径，并拒绝敏感路径、生成目录和隐藏 benchmark 材料路径。
- 测试命令执行前必须审批：命令支持 approve/edit/reject/cancel，edit 后重新校验命令，不允许命令参数或 `--option=value` 指向 `evaluation`、`oracle_tests` 或 `expected_result.json`。
- interrupt 子图使用三段式 HITL：`review_test_plan`、`approve_test_patch`、`approve_test_command`，并通过 SQLite checkpoint resume 恢复。
- patch 审批 payload 包含 `patch_sha256`，apply 前重新计算 hash，防止审批后 diff 被替换。
- 方案或 patch 的 edit 决策会重新生成 patch 审批 payload；当前计划会覆盖写入 `testing/test_plan.md` 和 `testing/test_plan.json`，避免恢复阶段读取旧计划导致命令、框架或报告错配。

## 使用方式

M18 主要通过注入 `TestingService` 与 `TestingRequest` 使用：

```python
service = TestingService(run_context=run_context)
handler = create_testing_stage_handler(
    service=service,
    request_builder=lambda state: testing_request,
)
subgraph = build_testing_subgraph(handler)
```

需要显式 HITL/resume 时使用：

```python
subgraph = build_interrupting_testing_subgraph(
    service=service,
    request_builder=lambda state: testing_request,
    checkpointer=saver,
)
```

## 验证结果

- `python -m pytest tests\integration\test_testing_stage.py -q`：15 passed。
- `python -m pytest tests\integration\test_testing_stage.py tests\integration\test_implementation_stage.py tests\integration\test_resume.py tests\unit\workflow tests\unit\tools tests\unit\reports -q`：110 passed。
- `python -m pytest -q`：185 passed。
- `python -m codeagent --help`：通过，CLI help 正常输出。
- `codeagent --help`：通过，CLI help 正常输出。

## 审查结果

- 规格复核：PASS。此前发现的 edit 后 stale `test_plan.json` 风险已通过回归测试和服务层持久化修复解决。
- 质量复核：APPROVED。此前发现的隐藏 benchmark `--option=value` 命令路径漏洞、patch hash 防篡改和 edit 恢复路径问题均已修复并覆盖测试。

## 已知限制与后续工作

- 当前测试阶段消费结构化 `TestingPlan`；后续 M12/M18 之后的 LLM TestDesigner/TestWriter 接入可生成该结构化输入。
- 当前 MVP 以 pytest 为主，保留 unittest 解析入口；更多测试框架在后续扩展阶段接入。
- 调试与修复阶段尚未消费 M18 的失败测试报告，后续 M19/M20 将串联失败摘要、日志和修复计划。
