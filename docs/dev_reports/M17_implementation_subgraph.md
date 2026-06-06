# M17 ImplementationSubgraph 开发报告

## 目标

实现 CodeAgent 的实现阶段服务与 LangGraph 子图，使实现阶段能够从结构化实现计划出发，生成可审计 patch，经过 HITL 审批后再修改项目文件，并输出计划、patch、变更清单、语法检查日志、实现报告和阶段结果。

## 主要变更

- `codeagent/stages/implementation_service.py`
  - 新增 `ImplementationPlan`、`ImplementationFileChange`、`ImplementationRequest`。
  - 新增 `ImplementationService`，串联 patch 生成、候选验证、审批记录、patch 应用、`python -m py_compile` 语法检查、阶段报告写入。
  - 新增 `prepare_approval()`，在项目文件修改前生成审批 payload。
  - 新增 `apply_prepared_patch()`，恢复审批后直接应用已审批的 `implementation.patch.diff`，并校验 `patch_sha256`，避免重新生成不同 patch。
- `codeagent/workflow/subgraphs/implementation.py`
  - 新增确定性 stage handler，便于主图注入和测试。
  - 新增 interrupting subgraph：`prepare_patch -> approve_patch(interrupt) -> apply_patch`。
- `tests/integration/test_implementation_stage.py`
  - 覆盖计划 schema、成功路径、patch 候选重试、敏感路径不落盘、语法失败、取消审批、edit 审批、子图状态更新、真实 interrupt/resume、审批 patch 不重新生成。

## 关键设计决策

- 实现服务先采用确定性结构化输入，后续 LLM Coder 只需产出 `ImplementationPlan`，降低 M17 的不确定性。
- 所有项目文件修改继续走 `PatchService`，实现阶段不直接写项目源码。
- 敏感/越界目标在 diff 生成和候选 patch 落盘前 fail-closed，避免被拒绝候选泄漏到运行产物。
- HITL interrupt payload 包含 patch path、changed files、风险等级、增删行数和 patch hash。
- resume approve 后应用审批时生成的 patch 文件，而不是重新调用 patch 生成逻辑。
- edit 决策支持 `edited_payload.plan`，先记录 edit，再以编辑后的计划进入 approved 路径。

## 使用方式

测试或上层节点可构造：

```python
plan = ImplementationPlan(
    requirements_summary="...",
    impact_summary="...",
    changes=[
        ImplementationFileChange(
            path="module.py",
            old_content=None,
            new_content="VALUE = 1\n",
            rationale="..."
        )
    ],
    syntax_check_targets=["module.py"],
)
request = ImplementationRequest(plan=plan, approval=approval_decision)
result = ImplementationService(run_context=context).run(request)
```

真实 LangGraph 审批流使用 `build_interrupting_implementation_subgraph()`，首次执行在 `approve_patch` 节点产生 interrupt；resume 时传入 `{"decision_type": "approve"}` 后进入 apply 节点。

## 验证命令

- `python -m pytest tests/integration/test_implementation_stage.py -q` -> 10 passed
- `python -m pytest tests/integration/test_implementation_stage.py tests/integration/test_resume.py tests/unit/workflow tests/unit/tools tests/unit/reports -q` -> 95 passed
- `python -m compileall -q codeagent` -> passed
- `python -m pytest -q` -> 170 passed
- `python -m codeagent --help` -> exited 0
- `codeagent --help` -> exited 0

## 复查结果

- 规格复查初次发现：实现审批缺少真实 LangGraph interrupt/resume 语义；已拆分子图节点并新增真实恢复测试。
- 规格复查二次发现：resume 后会重新生成 patch，弱化“审批哪个 patch 就应用哪个 patch”的保证；已新增 hash 校验和直接应用已审批 patch 的路径。
- 规格复查三次发现：prepared apply 使用 resume-time plan 生成报告和语法目标；已持久化 `implementation_plan.json`，恢复后使用审批时 plan。
- 规格复查最终结果：PASS。
- 质量复查发现：敏感目标候选 patch 在被拒绝前会落盘；已前置路径/敏感预检并新增回归测试。
- 质量复查最终结果：APPROVED。

## 已知限制与后续

- 当前实现计划仍由测试或后续 LLM 节点提供，M17 未实现完整 Coder LLM 调用闭环。
- edit 决策支持结构化 `edited_payload.plan`；reject/respond 当前写为可重试失败，后续节点化 Coder 循环可将其路由回计划/生成节点。
- 语法检查以 changed `.py` 文件为主，完整测试执行将在 M18 TestingSubgraph 中落地。
