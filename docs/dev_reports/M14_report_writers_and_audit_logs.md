# M14 Report Writers 与 Audit Logs

## 目标

本里程碑将 M13 的状态和报告 schema 落到文件系统产物中，提供阶段报告、`stage_result.json`、`final_report.md`、`decision_trace.jsonl`、`transcript.jsonl` 和 artifact index 的统一写入能力。

## 主要变更

- 新增 `codeagent/reports/writer.py`：实现 `ReportWriter`、`StageReportPaths` 和 `ReportReferenceError`。
- 新增 `codeagent/reports/decision_trace.py`：实现 `DecisionTraceWriter`，统一写入人工审批和路由决策事件。
- 更新 `codeagent/reports/__init__.py`，导出报告写入和审计 trace 类型。
- 新增 `tests/unit/reports/test_writer.py`，覆盖阶段报告写入、artifact 引用校验、失败报告、final report、decision trace 和 Markdown 表格转义。
- 调整 `tests/unit/tools/test_shell_runner.py` 中失败退出码用例的超时阈值，避免嵌套 pytest 在 Windows 负载下误判为 timeout；专门的 timeout 用例仍保留 `0.2s` 阈值。

## 需求与设计对齐

- 对齐 FR-67~FR-72：阶段结果、最终报告、transcript、decision trace、artifact index 均可本地落盘并追溯。
- 对齐 FR-83/FR-84 与 NFR-08：用户拒绝、测试失败、阶段失败或取消需要保留原因和后续建议。
- 对齐设计 03/09 的一致性规则：报告中引用的 artifact id 必须已登记在 `artifacts_index.json`。
- 对齐设计 07 的失败报告约束：失败阶段写入错误 ID、类别、消息、相关产物和下一步建议。
- final report 只从 `StageResult`、`ArtifactStore` 和审计记录生成，不调用模型补写结论。

## 关键设计决策

- `ReportWriter.write_stage_report()` 同时写入 `stage_result.json` 和 `stage_report.md`，并把两者注册为 artifact。
- `ReportWriter.write_final_report()` 在写入前复用阶段结果校验，避免 failed/cancelled 阶段缺少失败原因或下一步建议。
- `ReportReferenceError` 用于阻止未注册 artifact 引用进入阶段报告或最终报告。
- `DecisionTraceWriter` 只追加结构化 JSONL 事件，不重写既有日志。
- Markdown 表格统一通过 `_markdown_cell()` 转义 `|` 并折叠换行，防止摘要或错误消息破坏表格结构。

## 验证

- `python -m pytest tests/unit/reports -q`：10 passed。
- `python -m pytest tests/unit/reports tests/unit/runtime tests/unit/workflow tests/unit/tools/test_shell_runner.py -q`：45 passed。
- `python -m compileall -q codeagent/reports codeagent/runtime codeagent/workflow codeagent/tools`：通过。
- `python -m codeagent --help`：退出码 0。
- `python -m pytest -q`：140 passed。

## 复查结果

- 规格审阅初次发现 `write_final_report()` 未强制 failed/cancelled 阶段包含失败原因和下一步建议，且最终报告未渲染失败详情；已补红灯测试并修复，规格复查 PASS。
- 质量审阅发现 Markdown 表格单元格未转义；已补包含 `|` 和换行的回归测试，并在 stage/final/artifact/failure 表格统一转义，质量复查 APPROVED。

## 限制与后续

- 当前报告模板为代码内简单 Markdown 渲染函数；后续如需复杂格式，可在不改变 `ReportWriter` 输入约束的前提下拆分为模板文件。
- M14 尚未接入 LangGraph 节点，M15 主图与路由落地后应由阶段节点调用 `ReportWriter`。
