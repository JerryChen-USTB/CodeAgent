# M27 审查加固与最终验证汇报

## 目标

本轮工作针对最终代码审查反馈和真实 Sonnet benchmark 回归做闭环：修复路径归一化误判、长路径覆盖遗漏、活跃文档模型/secret 不一致、README CLI 示例错误、自建 benchmark 可见规格冲突，以及 SQLite 连接未关闭导致的 Windows 数据库文件锁问题。

## 主要变更

- `codeagent.filesystem` 增加 `append_text`、`touch`、`is_file`，并扩展到 run context、checkpoint、transcript、artifact index、CLI testing report、benchmark report/prepare logs、testing/debugging/repair 阶段报告与 patch artifact。
- `PlanGenerationService` 改进路径归一化：仅在 wrapper 目录不存在或剥离后路径更可信时去掉 `workspace/` 等前缀，真实同名目录会保留。
- LLM prompt 增加 SQLite 连接关闭要求：`with sqlite3.connect(...) as conn` 不能单独关闭连接，生成代码应显式 `close()`、`try/finally` 或 `contextlib.closing`。
- CodeAgent 自身 SQLite checkpoint 初始化、状态检查和 SQLite saver 使用 `contextlib.closing`，并用 Windows 文件删除回归测试证明连接已释放。
- `PlanGenerationService` 的可见上下文读取、failure log 发现，以及 implementation resume 路径中的 prepared plan/patch 读取进一步切到长路径 helper。
- 活跃文档统一为临时默认模型 `anthropic/claude-sonnet-4.6`；secret 来源统一为环境变量，禁止读取本地 secret 文件。
- `README.md` 阶段子命令示例改为真实 CLI 参数形式。
- 自建 benchmark 可见输入更新：`02_personal_ledger` CSV 导出明确为 stored/addition order；`05_meeting_room_booking` 明确 SQLite 连接必须关闭。

## 验证

- 红绿回归：新增 11 个覆盖路径归一化、活跃文档、长路径和 benchmark 可见规格的测试，先 11 failed，再修复后 11 passed。
- 相关单元：`python -m pytest tests\unit\agents tests\unit\docs tests\unit\runtime tests\unit\reports tests\unit\benchmark -q` -> 53 passed。
- 阶段集成：testing/debugging/repair 三个集成文件分别 16、13、17 passed。
- 全量：`python -m pytest -q` -> 289 passed。
- 编译与 CLI：`python -m compileall -q codeagent tests`、`python -m codeagent --help`、`codeagent --help` 均通过。
- 真实 OpenRouter smoke：默认 `ModelConfig()` 使用 `anthropic/claude-sonnet-4.6` 返回预期 marker。
- 审查 follow-up：SQLite 文件锁测试先 2 failed，再修复后 2 passed；plan generation + implementation 25 passed；runtime/checkpoint/report/benchmark report 22 passed。

## Benchmark 结果

- 公共 benchmark：`benchmark/codeagent_runs/benchmark/2026-06-03_095303_304297_codeagent_course_benchmark_b88270/benchmark_result.json`，success_rate=1.00，BugsInPy optional blocker=1，执行 case 均 `source_unchanged=True`。
- 自建 benchmark：首次 fresh Sonnet run 为 4/5，失败原因是 `05_meeting_room_booking` 生成代码未关闭 SQLite 连接，Windows 下 oracle 临时数据库文件被锁。补强 prompt 和可见要求后重跑：`benchmark/selfbuilt/codeagent_runs/benchmark/2026-06-03_100356_416230_codeagent_selfbuilt_python_benchmark_670ea1/benchmark_result.json`，5/5，blocked=0，全部 `oracle_success=True` 且 `source_unchanged=True`。

## 残余风险

- BugsInPy 仍受本机 WSL/conda 环境限制，当前按 explicit blocker 记录。
- 真实 LLM 输出仍可能随模型采样波动；已通过更明确的 prompt、可见需求和回归测试降低已知波动风险。
- `workspace/` / `project/` wrapper 前缀剥离仍基于文件系统存在性；如果未来需求确实要从空项目新建顶层 `workspace/` 目录，需要增加显式路径规则。
