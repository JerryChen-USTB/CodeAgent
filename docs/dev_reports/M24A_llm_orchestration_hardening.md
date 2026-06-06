# M24A LLM 编排加固与 Benchmark 回归包报告

## 阶段目标

M24A 的目标是在 M24 公共 benchmark 真实 OpenRouter 调用通过之后，继续加固 LLM 驱动的实现/修复编排链路，避免真实模型输出波动、路径越界、敏感信息泄露、hidden oracle 暴露和 benchmark 模板污染。

## 关键实现

- `PlanGenerationService` 为实现和修复计划生成新增 `plan_generation_attempts.json` 审计文件，记录 schema、prompt hash、prompt 长度、尝试次数、attempt 状态、脱敏响应预览和脱敏错误摘要。
- 模型返回非 JSON、字段缺失、类型错误或模型调用异常时，会按配置重试，并把每次失败写入审计文件；最终错误仍保持脱敏。
- repair prompt 现在能够发现 `ShellRunner` 因 Windows 长路径保护而缩短的 `cmd-<hash>.stdout.log` / `cmd-<hash>.stderr.log`，避免修复模型拿不到最新测试失败证据。
- 结构化计划在进入 patch 阶段前会被规范化和检查：拒绝绝对路径、项目根外路径、`evaluation`、`oracle_tests`、`expected_result.json`、配置 hidden root、`.env` 等敏感或生成目录。
- Benchmark 结果新增 source case 快照字段：`source_snapshot_before`、`source_snapshot_after`、`source_unchanged`，用于证明原始 case 在运行前后保持可复用。
- 新增五类自建回归 pack 集成测试，覆盖可见测试、runner-only oracle、nested hidden path、项目相对命令、`{{CASE_DIR}}` 占位符归一化和聚合报告。

## 对齐情况

- 对齐 SRS：覆盖全过程日志、测试/调试/修复产物、benchmark 成功率统计、模型错误处理和副作用审批记录。
- 对齐设计 07：LLM 结构化输出失败可重试、可诊断、可定位。
- 对齐设计 09：运行产物保持可复现，报告不合成成功。
- 对齐设计 10：原始 benchmark case 作为只读模板，执行前复制到干净工作区，hidden oracle 仅由 runner-only evaluator 使用。

## 真实 Benchmark 结果

- 命令：`python -m codeagent benchmark --config benchmark\benchmark.yaml`
- 结果：6/6 通过，`success_rate=1.00`
- 结果目录：`benchmark/codeagent_runs/benchmark/2026-06-03_060921_184932_codeagent_course_benchmark_b870e4`
- 每个启用 case 均记录 `source_unchanged=True`
- 每个 LLM 实现/修复 case 均生成 `plan_generation_attempts.json`
- 对最新 run artifacts 的敏感值扫描未发现 OpenRouter key、Bearer token 或 `OPENROUTER_API_KEY=` 明文值

## 验证命令

- `python -m pytest tests\unit\agents -q` -> 12 passed
- `python -m pytest tests\integration\test_benchmark_runner.py tests\unit\agents -q` -> 23 passed
- `python -m pytest tests\integration\test_cli_run.py tests\unit\tools\test_shell_runner.py -q` -> 20 passed
- `python -m compileall -q codeagent\benchmark codeagent\agents` -> passed
- `python -m pytest -q` -> 256 passed
- `python -m compileall -q codeagent` -> passed
- `python -m codeagent benchmark --config benchmark\benchmark.yaml` -> 6/6 passed

## 后续关注

M25 将进入 BugsInPy 可选路径和环境检测。需要继续保持当前边界：真实 OpenRouter 调用只从环境变量取密钥，benchmark 原始 case 不被污染，hidden oracle 不进入 Agent 可见上下文，失败必须有明确 blocker 或 error artifact。
