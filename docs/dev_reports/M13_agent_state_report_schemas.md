# M13 AgentState、StageResult、ErrorRecord 与报告 Schema

## 目标

本里程碑实现工作流状态和报告领域对象的基础模型，使后续 LangGraph checkpoint、阶段报告、错误处理、人工审批、工具调用记录和 final report 聚合都能使用统一的、可 JSON 序列化的数据结构。

## 主要变更

- 新增 `codeagent/workflow/state.py`：定义 `AgentState`、`create_initial_state()`、`state_to_json_dict()` 和 `CheckpointSafetyError`。
- 新增 `codeagent/reports/schemas.py`：定义 `StageResult`、`ToolCallRecord`、`HumanDecision`、`CodeChange`、`TestResultRecord`、`DebugResult`、`RepairResult`。
- 新增 `codeagent/errors/exceptions.py`：定义 `ErrorRecord` 和可转换为错误记录的 `CodeAgentError`。
- 更新 `codeagent/reports/__init__.py`，并新增 `codeagent/workflow`、`codeagent/errors` package exports。
- 新增 `tests/unit/workflow/test_state_schema.py`，覆盖状态 JSON roundtrip、Path 归一化、非序列化值拒绝、非有限 float 拒绝、Pydantic 校验失败、审批/工具/测试/修复/调试记录序列化。

## 需求与设计对齐

- 对齐 FR-13/FR-14：阶段间通过轻量 state 和阶段产物引用传递上下文，状态写 checkpoint 前必须可 JSON 序列化。
- 对齐 FR-67~FR-72：运行元数据、transcript、阶段结果、最终报告和产物索引后续可复用这些 schema 生成可追踪记录。
- 对齐 FR-81~FR-84：模型、工具、测试命令、用户拒绝等失败可用 `ErrorRecord` 和 `StageResult.error` 记录。
- 对齐数据对象表：覆盖 AgentState、ToolCallRecord、HumanDecision、CodeChange、TestResult、DebugResult、RepairResult。
- 对齐设计文档 03/05/07/09：LangGraph state 使用 TypedDict，Pydantic 对象用于持久化领域记录；大文本不进入 state，只保存摘要和产物引用。

## 关键设计决策

- `AgentState` 只保留 primitive JSON 值、路径字符串、摘要和 artifact id，不保存原始日志或大段模型输出。
- `state_to_json_dict()` 递归转换 `Path`、tuple 和 Pydantic model，同时拒绝 object、非字符串 dict key、超长字符串、`NaN`、`Infinity`、`-Infinity`。
- `StageResult.summary` 限制为 8000 字符，避免阶段报告或模型原文误塞入 checkpoint。
- `HumanDecision` 保留 `edited_payload`，以便 `edit` 决策可审计。
- `ToolCallRecord.status` 支持 `succeeded`、`failed`、`denied`、`blocked`、`skipped`，避免 respond/cancel/降级跳过路径被误记为失败。
- `TestResultRecord.success` 在未显式提供时由退出码和失败/错误计数推导，方便后续报告聚合。

## 验证

- `python -m pytest tests/unit/workflow/test_state_schema.py -q`：13 passed。
- `python -m pytest tests/unit/workflow/test_state_schema.py tests/unit/runtime/test_artifacts_and_logs.py tests/unit/tools/test_permissions.py -q`：29 passed。
- `python -m compileall -q codeagent`：通过。
- `python -m codeagent --help`：退出码 0。
- `python -m pytest -q`：130 passed。

## 复查结果

- 规格审阅初次发现 `HumanDecision` 缺少 `edited_payload`、`ToolCallRecord` 缺少 blocked/skipped 状态；已补测试并修复，规格复查 PASS。
- 质量审阅发现 `state_to_json_dict()` 允许 `NaN/Infinity` 生成非标准 JSON；已补 `nan/inf/-inf` 回归测试并修复，质量复查 APPROVED。

## 限制与后续

- 本里程碑只定义 schema 和状态转换，不负责实际写入 `stage_result.json`、`transcript.jsonl` 或 `final_report.md`；这些将在 M14 报告写入与审计日志中落地。
- `StageResult` 当前不强制校验 artifact id 是否存在于 `artifacts_index.json`；后续 `ReportWriter`/`ArtifactStore` 集成时应执行引用一致性检查。
