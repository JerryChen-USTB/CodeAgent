# OPT04 人工审批提示与调试/修复可见性优化

## 背景问题

用户在 Todo Manager 真实体验中观察到两个主要问题：

1. 测试失败后进入调试阶段，但调试阶段很快结束，用户看不清它读取了哪些证据、是否重新运行了测试、最终如何定位问题。
2. 人工审批选项过多，计划和补丁审批都出现了手动编辑、拒绝、取消等选项，和当前“同意或提出修改意见”的使用预期不一致。

同时，修复阶段虽然会执行回归验证并写入 `repair_test_result.json`，但 CLI 没有流式显示每次修复后的测试计数，导致用户只能看到“修复失败/成功”，看不到具体 `passed/failed/errors/skipped`。

## 处理结论

调试阶段之前“闪过”的原因并不是跳过调试，而是系统在已有 testing 阶段失败日志和测试报告时，默认不再次运行同一条测试命令，而是直接读取已有失败证据做静态分析。这样速度很快，但 CLI 只打印了泛化状态，用户无法判断发生了什么。

本次改造后：

- 真实交互终端中，如果已有失败日志，调试阶段会先询问是否重跑同一条测试命令复现；选择不运行则继续静态分析。
- 非交互 `run --config`、CLI 自动化测试和 benchmark 不会因为这个询问阻塞，会继续使用已有失败证据。
- 调试阶段会打印失败数量、失败用例、证据来源、复现状态、定位置信度、首要嫌疑文件和 `debug_report.md` 路径。
- 修复阶段每次验证都打印测试计数，失败时提示 `repair_test_result.json` 和 `repair_report.md`。

## 代码改动

### 1. 审批提示收敛

`codeagent/cli/approval_console.py` 新增按审批动作定制的选项文案：

- 计划审批：
  - `是，实施此计划`
  - `否，告知 CodeAgent 如何调整`
- 补丁审批：
  - `是，应用此补丁`
  - `否，告知 CodeAgent 如何调整`
- 命令审批保留安全分支，但文案缩短为：
  - `是，运行命令`
  - `否，修改命令`
  - `否，不运行命令`
  - `取消本次运行`

`ImplementationService`、`TestingService`、`RepairService` 的计划审批标题分别改为：

- `实施此实现计划？`
- `实施此测试计划？`
- `实施此修复计划？`

补丁审批标题分别改为：

- `应用此实现补丁？`
- `应用此测试补丁？`
- `应用此修复补丁？`

三类补丁审批 payload 的 `allowed_decisions` 都收敛为 `["approve", "respond"]`，不再向用户暴露 edit/reject/cancel。

### 2. 调试阶段可见性

`DebuggingRequest` 新增 `attempt_index`，CLI 根据当前 `repair_attempt` 计算第几轮调试。

`DebuggingService.run()` 新增：

- `debugging_attempt_started` workflow 事件。
- 调试入口 `agent_status`，说明失败日志、测试报告和是否运行复现命令。
- 证据摘要 `agent_status`，说明失败数量、失败用例名、证据来源和是否已运行复现。
- `debugging_attempt_finished` workflow 事件。
- 调试完成 `agent_status`，说明复现状态、置信度、首要嫌疑文件和 `debug_report.md`。

复现命令执行时还会发出：

- `tool_started`
- `tool_finished`

这样 CLI 上不再只看到“调试阶段已完成”，而能看到调试阶段实际做了什么。

### 3. 修复阶段测试结果输出

`RepairService.run_prepared_command()` 在执行回归验证前后新增：

- `tool_started`：显示即将执行的回归命令。
- `tool_finished`：显示命令退出码。
- `test_result`：显示 `passed/failed/errors/skipped/total`。
- `agent_status`：成功时提示通过数量和 `repair_report.md`；失败时提示失败数量、`repair_test_result.json` 和 `repair_report.md`。

因此测试阶段之后的验证结果不会再“只写文件不打印”。当 repair 失败并返回 debugging 时，用户可以在 CLI 上看到每轮修复后的测试结果。

### 4. 审批记录顺序和日志可读性

CLI 主流程在 testing/repair 计划审批通过后，会立即记录 `approval_decision`，然后才触发 `patch_generation_requested`。对应 service 的 `prepare_patch_approval()` 增加 `record_plan_review=False` 参数，避免同一条计划审批被重复记录。

终端超链接只在真实 TTY 中启用；非 TTY、pytest、保存日志等场景只输出普通 `文件名 (相对路径)`，避免 `workflow` 或 console log 中出现裸 OSC 控制符。

## 对用户问题的解释

### 调试阶段是跑测试阶段的测试用例吗？

分两种情况：

- 如果用户在调试复现审批中选择运行命令，调试阶段会运行任务配置中的同一条测试命令，例如 `python -m pytest -q`。这通常会再次运行 testing 阶段生成的测试用例。
- 如果用户选择不运行，或者在非交互/benchmark 自动模式下已有 testing 失败日志，调试阶段会读取 testing 阶段留下的日志和 `test_result.json` 做静态分析，不再次跑测试。

### 后续再测是在调试阶段还是修复阶段？

修复补丁应用之后的回归验证发生在 repair 阶段。调试阶段负责定位原因和生成修复依据，repair 阶段负责应用修复并再次运行测试命令验证。

之前只在 testing 阶段看到 `[测试结果] 40 passed, 4 failed...`，是因为 repair 阶段没有把验证结果流式打印出来。本次已补齐，repair 每轮验证都会输出 `[测试结果] ...`。

## 验证结果

已执行：

```powershell
python -m py_compile codeagent\cli\approval_console.py codeagent\cli\executor.py codeagent\stages\debugging_service.py codeagent\stages\repair_service.py codeagent\stages\testing_service.py codeagent\stages\implementation_service.py
python -m pytest tests\unit\cli tests\unit\stages tests\integration\test_cli_wizard.py tests\integration\test_cli_run.py tests\integration\test_repair_stage.py -q
python -m pytest tests\integration\test_debugging_stage.py -q
python -m pytest tests\integration\test_implementation_stage.py -q
python -m pytest tests\integration\test_testing_stage.py::test_interrupting_testing_subgraph_reviews_plan_patch_and_command tests\integration\test_testing_stage.py::test_interrupting_testing_subgraph_plan_review_is_plan_only tests\integration\test_testing_stage.py::test_interrupting_testing_subgraph_rejects_tampered_approved_patch -q
```

结果：

- 编译检查通过。
- CLI/unit/stages/CLI run/repair 组合：57 passed。
- debugging 集成测试：13 passed。
- implementation 集成测试：13 passed。
- testing 关键中断用例：3 passed。

三阶段 testing/implementation/debugging 整文件并行运行曾因超时中断，随后已拆分验证关键路径。

## 真实 Todo Manager 单例验证

为了避免全量 benchmark 的时间和 token 成本，本次只运行 Todo Manager 一个真实场景：

```powershell
python -m codeagent run --config codeagent_runs\opt04_validation\todo_manager\task.yaml
```

运行目录：

```text
codeagent_runs/opt04_validation/todo_manager/runs/2026-06-04_093151_091147_implement-test-debug-repair_b4dcce
```

结果：

- implementation：先生成 `ImplementationPlan`，再生成 `ImplementationPatchDraft`，实现阶段成功。
- testing：Agent 自测先得到 `82 passed, 2 failed, 0 errors, 0 skipped`。
- debugging：CLI 显示第 1 次调试，读取 testing 阶段失败日志和 `test_result.json`，未重跑命令，定位首要嫌疑为 `todo_manager/cli.py`，报告为 `debugging/debug_report.md`。
- repair：生成修复计划和修复补丁后执行回归验证，CLI 显示 `84 passed, 0 failed, 0 errors, 0 skipped`。
- 最终状态：`succeeded`。

生成软件体验命令：

```powershell
python -m todo_manager --file demo_tasks.json list
python -m todo_manager --file demo_tasks.json add --title "Write report" --due 2026-06-10 --priority high
python -m todo_manager --file demo_tasks.json add --title "Buy milk"
python -m todo_manager --file demo_tasks.json list
python -m todo_manager --file demo_tasks.json done 1
python -m todo_manager --file demo_tasks.json list --status done
python -m todo_manager --file demo_tasks.json delete 2
python -m todo_manager --file demo_tasks.json list
```

实际输出覆盖了空列表、添加任务、查看任务、完成任务、按状态过滤和删除任务，行为符合公开需求。

## 剩余限制

- 底层仍保留旧的 edit/reject/cancel 决策处理逻辑，用于兼容历史 checkpoint 和测试夹具；但新生成给用户的计划/补丁审批 payload 不再暴露这些选项。
- 调试阶段的“是否重跑复现命令”只在真实交互 TTY 中出现；非交互模式为了避免 EOF 或卡住，会继续静态分析已有失败证据。
- 本次没有运行全量自建 benchmark，避免额外时间和 token 成本；本轮改动主要集中在 CLI/HITL/debug/repair 可见性，已用阶段级集成测试和一个真实 Todo Manager 单例覆盖。
