# M19 DebuggingSubgraph 开发报告

## 目标

M19 实现调试阶段子图与调试阶段服务，支持失败证据收集、可选复现命令审批与执行、静态日志兜底、失败摘要、故障定位、根因分析、修复建议和调试报告产物。该能力对齐 SRS 中 FR-49~FR-56、AC-10、UC-04，以及设计文档中“复现、日志摘要、源码搜索、可疑位置排序、根因分析、修复建议”的调试阶段流程。

## 主要变更

- `codeagent/stages/debugging_service.py`：新增 `DebuggingService`、`DebuggingRequest`、`FaultCandidate`、`FaultLocalization`，封装复现命令审批、Shell 执行、失败日志读取、测试结果解析、源码候选定位、根因/修复建议生成和阶段报告。
- `codeagent/workflow/subgraphs/debugging.py`：新增确定性调试阶段 handler、基础调试子图和带 reproduction command interrupt 的调试子图。
- `tests/integration/test_debugging_stage.py`：覆盖复现成功、复现拒绝后的静态分析、低置信度报告、隐藏 benchmark 防护、外部日志读取防护、主图路由到 repair、独立子图和 SQLite resume interrupt。
- `codeagent/stages/__init__.py`、`codeagent/workflow/subgraphs/__init__.py`：导出 M19 新增服务与子图入口。

## 关键设计决策

- 调试阶段是证据优先的确定性实现：优先使用复现命令的 pytest/unittest 输出和栈追踪；若用户拒绝命令或未提供命令，则使用受限日志/测试报告进行静态分析，并降低置信度。
- `fault_localization.json` 必须带候选位置、证据和置信度；没有足够证据时允许输出低置信度且无候选，而不是猜测高置信根因。
- 复现命令使用 HITL：支持 approve/edit/reject/cancel，命令执行仍走 `ShellRunner` 允许列表和日志记录。
- 调试日志和测试报告读取 fail closed：路径必须在项目根或当前 run_dir 内，且拒绝 secret-like 路径、敏感后缀、`Software Engineering Project.txt`、`evaluation`、`oracle_tests` 和 `expected_result.json`。
- 隐藏 benchmark 防护覆盖命令参数和 `--option=value`，包括裸 `evaluation` / `oracle_tests` 参数。

## 使用方式

确定性 handler：

```python
service = DebuggingService(run_context=run_context)
handler = create_debugging_stage_handler(
    service=service,
    request_builder=lambda state: debugging_request,
)
subgraph = build_debugging_subgraph(handler)
```

带 interrupt/resume 的调试子图：

```python
subgraph = build_interrupting_debugging_subgraph(
    service=service,
    request_builder=lambda state: debugging_request,
    checkpointer=saver,
)
```

## 验证结果

- `python -m pytest tests\integration\test_debugging_stage.py -q`：12 passed。
- `python -m pytest tests\integration\test_debugging_stage.py tests\integration\test_testing_stage.py tests\integration\test_implementation_stage.py tests\integration\test_resume.py tests\unit\workflow tests\unit\tools tests\unit\reports -q`：122 passed。
- `python -m pytest -q`：197 passed。
- `python -m codeagent --help`：通过，CLI help 正常输出。
- `codeagent --help`：通过，CLI help 正常输出。

## 审查结果

- 规格复核：PASS。安全修复后再次复核确认无 P0/P1/P2 规格缺口。
- 质量复核：APPROVED。此前发现的任意外部日志读取风险和裸隐藏 benchmark 命令参数风险已修复并覆盖回归测试。

## 已知限制与后续工作

- 当前根因分析为确定性证据摘要；后续 LLM Debugger 节点可基于同一 `FaultLocalization` schema 生成更细粒度的调用链和修复解释。
- 当前源码搜索以 Python 文件、栈追踪和关键词为主；后续可加入 AST、覆盖率和更强的失败测试到源码映射。
- M20 RepairSubgraph 将消费 `debugging/fault_localization.json`、`debugging/root_cause.md` 和 `debugging/repair_plan.md` 生成修复 patch 并验证。
