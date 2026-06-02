# M16 SQLite Checkpoint、Interrupt 与 Resume

## 目标

本里程碑为 M15 的 LangGraph 主图加入 SQLite checkpoint 支持，并提供 `thread_id=run_id` 的恢复配置、pending interrupt 持久化、`resume --run-id` 只读检查和 `Command(resume=...)` 恢复路径。

## 主要变更

- 新增 `codeagent/workflow/checkpoint.py`：实现 `CheckpointManager`、SQLite saver 生命周期、thread config、pending interrupt JSON 读写。
- 新增 `codeagent/cli/resume.py`：实现 run 目录检查、resume summary、checkpoint 缺失/损坏 fallback、`resume_run_from_checkpoint()`。
- 更新 `codeagent/workflow/main_graph.py` 与 `factory.py`：支持向 `StateGraph.compile()` 注入 checkpointer。
- 更新 `codeagent/runtime/run_context.py`：初始化 `checkpoints.sqlite` 时不占用 LangGraph `checkpoints` 表名，避免 schema 冲突。
- 更新 `codeagent/cli/app.py`：`resume` 命令支持 `--output-root` 和 `--decision-json`。
- 新增 `tests/integration/test_resume.py`，使用真实 LangGraph `interrupt()` 和 `Command(resume=...)` 覆盖 checkpoint 恢复。

## 需求与设计对齐

- 对齐 FR-14/FR-19：关键状态通过 SQLite checkpointer 持久化，可用 `run_id` 的 thread config 获取状态。
- 对齐 FR-15/FR-16/FR-83：pending interrupt payload 可落盘、读取、展示，并可用人工决策 JSON 恢复。
- 对齐 FR-68/FR-70~FR-72：checkpoint 不可用时回退读取 final report 和 artifact index，至少可查看中断前产物。
- 对齐 UC-07：`resume --run-id` 能定位 run_dir、检查 task_config/checkpoint/pending interrupt，并输出可恢复或只读摘要。
- 对齐设计 04/07/09：`thread_id=run_id`，SQLite 位于 run_dir，checkpoint 缺失/损坏时展示只读 artifact summary。

## 关键设计决策

- `CheckpointManager.create_sqlite_saver()` 使用本地 `langgraph.checkpoint.sqlite.SqliteSaver` 和显式连接生命周期。
- `CheckpointManager.get_thread_config()` 固定返回 `{"configurable": {"thread_id": run_id}}`。
- `pending_interrupt.json` 只保存 JSON-safe payload；坏 JSON 不使 CLI 崩溃，而是按无 pending interrupt 处理。
- completed 判断不再依赖 `final_report.md` 内容，因为 run 初始化会写 placeholder；只有 artifact index 中存在 `final_report` artifact 时才标记 completed。
- CLI `--decision-json` 无效时输出稳定错误并退出 1，不暴露 traceback。

## 验证

- `python -m pytest tests/integration/test_resume.py -q`：9 passed。
- `python -m pytest tests/integration/test_resume.py tests/unit/runtime tests/unit/workflow tests/test_cli_contract.py -q`：49 passed。
- `python -m compileall -q codeagent/workflow codeagent/cli codeagent/runtime`：通过。
- `python -m codeagent --help`：退出码 0。
- `python -m pytest -q`：160 passed。
- `python -m codeagent resume --run-id missing-run --output-root .`：按预期退出 1，并打印 `not_found` 摘要。

## 复查结果

- 规格审阅初次发现 placeholder final report 被误判 completed、CLI 只检查不恢复；已改为 final_report artifact 判定完成，并补真实 interrupt/resume 测试，规格复查 PASS。
- 质量审阅发现坏 pending interrupt JSON 和坏 `--decision-json` 会冒泡异常；已补回归测试并修复，质量复查 APPROVED。

## 限制与后续

- 当前默认 resume graph 仍使用 M15 skeleton stage handler；M17~M20 接入真实 stage subgraph 后，`resume_run_from_checkpoint()` 将沿用同一 checkpointer/thread config。
- CLI 的人工决策输入目前是 JSON 字符串；M21 approval UI 会提供更友好的交互式审批入口。
