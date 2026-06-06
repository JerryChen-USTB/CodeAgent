# OPT03 Plan/实现解耦与 CLI 审批可读性优化

## 背景问题

用户在真实体验 Todo Manager 任务时发现：批准 Implementation Plan 后几乎立刻出现实现补丁，测试阶段也有同类现象。根因是旧版 `ImplementationPlan`、`TestingPlan`、`RepairPlan` 本身携带 `old_content/new_content`，所谓“批准计划”实际上已经批准了一份包含完整代码内容的结构化产物。系统随后只是把这些内容本地转换为 patch，因此 Plan 失去了“先讨论方案、再生成实现”的意义。

同时，CLI 审批提示只显示动作本身，没有充分列出用户应当审查哪些文件，也没有在任务完成时汇总关键产物。对答辩和真实使用来说，可读性不足。

## 改造目标

- 让计划阶段只表达目标、策略、涉及文件、验收点、风险和命令建议。
- 计划被批准后，才第二次调用 LLM 生成具体补丁草案。
- `respond` 在计划审批时重新生成计划，在补丁审批时基于已批准计划重新生成补丁草案。
- CLI 审批前清楚列出计划文件、补丁文件和即将影响的项目文件，使用 `文件名 (相对路径)` 展示。
- 任务结束后打印关键文件清单，便于用户审查实现代码、测试代码和审计报告。
- `workflow.log` 明确记录 `plan_generation` 与 `patch_generation` 两类 LLM 调用。

## 代码实现

### 1. 三阶段纯计划 schema

实现阶段：

- `ImplementationFileChange` 只保留 `path/change_type/rationale/public_interfaces/acceptance_notes`。
- `ImplementationPlan` 只保留 `requirements_summary/implementation_strategy/changes/acceptance_criteria/risk_notes`。
- `ImplementationPatchDraft` 新增为补丁草案 schema，包含 `old_content/new_content/syntax_check_targets`。

测试阶段：

- `TestingPlan` 只描述测试目标、策略、计划测试文件、验收点、推荐命令和框架。
- `TestingPatchDraft` 才包含完整测试文件内容和实际测试命令。
- testing 阶段继续禁止 `0 passed` 被算作成功。

修复阶段：

- `RepairPlan` 只描述根因、策略、涉及文件、预期效果和验证命令。
- `RepairPatchDraft` 才包含具体修复内容。
- 新增 `review_repair_plan` 审批点，和实现/测试计划审批保持一致。

三个 Plan schema 都启用 `extra="forbid"`，因此模型或测试夹具如果把 `old_content/new_content` 塞回 Plan，会直接校验失败。

### 2. 两次 LLM 调用

`PlanGenerationService` 现在拆分为：

- `create_implementation_request()` / `create_testing_request()` / `create_repair_request()`：只生成纯计划。
- `create_implementation_patch_draft()` / `create_testing_patch_draft()` / `create_repair_patch_draft()`：在计划审批通过后生成补丁草案。

prompt 也同步拆分。计划 prompt 明确禁止完整代码、diff、`old_content/new_content`；补丁 prompt 明确要求根据已批准计划生成具体文件内容。

### 3. CLI 工作流调整

`codeagent/cli/executor.py` 的执行顺序调整为：

- implementation：生成计划 -> 审批计划 -> 生成补丁草案 -> 审批补丁 -> 应用补丁 -> 语法检查。
- testing：生成测试计划 -> 审批计划 -> 生成测试补丁草案 -> 审批补丁 -> 应用测试 -> 审批并运行测试命令。
- repair：生成修复计划 -> 审批计划 -> 生成修复补丁草案 -> 审批补丁 -> 应用修复 -> 审批并运行回归命令。

计划审批只允许两个选项：同意，或不同意并提出修改意见重新生成。补丁和命令审批仍保留拒绝、取消、手动编辑等选项，因为这些步骤会产生副作用。

### 4. 审批可读性

审批前 CLI 会打印“请先审查以下文件”，并列出：

- 计划文件，例如 `implementation_plan.md (implementation/implementation_plan.md)`。
- 计划 JSON 文件。
- 补丁草案 JSON 文件。
- 实际 patch 文件。
- 已经真实存在、可打开审查的项目文件，例如 `models.py (todo_manager/models.py)`。

计划中提到但尚未创建的项目文件不会被打印成可点击路径，避免 VS Code 终端跳转到不存在的文件。补丁审批也只列出已存在的项目文件；新增文件会在补丁应用后出现在关键文件清单和产物索引中。

计划审批提示会说明：补丁尚未生成；同意后才会调用 LLM 生成补丁草案。补丁审批提示会说明：批准后才会修改项目工作区文件。

任务结束后，CLI 会打印关键文件清单，包括阶段修改文件、`final_report.md`、`workflow.log`、`workflow_events.jsonl`、`decision_trace.jsonl` 和 `artifacts_index.json`。

## 测试结果

已通过的自动化验证：

```powershell
python -m py_compile codeagent\cli\executor.py codeagent\agents\plan_generation.py codeagent\stages\implementation_service.py codeagent\stages\testing_service.py codeagent\stages\repair_service.py codeagent\workflow\subgraphs\repair.py codeagent\cli\progress.py
python -m pytest tests\integration\test_repair_stage.py -q
python -m pytest tests\integration\test_implementation_stage.py tests\integration\test_testing_stage.py tests\integration\test_cli_run.py tests\integration\test_benchmark_runner.py tests\unit\agents\test_plan_generation.py -q
python -m pytest tests\integration\test_cli_wizard.py -q
python -m pytest tests\unit\stages\test_plan_schema_boundaries.py tests\unit\agents\test_plan_generation.py -q
```

当前结果：

- repair 集成测试：17 passed。
- implementation/testing/CLI run/benchmark/PlanGeneration 组合测试：80 passed。
- wizard 集成测试：19 passed。
- Plan schema 边界与 PlanGeneration 单元测试：21 passed。

## 真实 LLM 验证

已执行 Todo Manager 单 case，使用真实 OpenRouter 模型 `anthropic/claude-sonnet-4.6`。运行目录：

```text
codeagent_runs/real_validation/opt03_todo_manager/runs/2026-06-04_065643_303727_implement-test-debug-repair_ebeeff
```

验证方式：

- 从 `benchmark/selfbuilt/cases/01_todo_manager/input/` 复制 Todo Manager 的公开输入材料到干净目录。当前新版材料为 `PRD.md`、`user_stories.md`、`design_model.md`、`acceptance_criteria.md`。
- `workspace` 初始为空。
- 使用脚本模拟人工审批：第一次 `review_implementation_plan` 选择 `respond`，要求重新生成计划；第二次实现计划及后续补丁、测试计划、测试补丁、测试命令均批准。
- 不读取隐藏 oracle，不运行全量 benchmark。

验证结果：

- 终端输出显示第一次实现计划审批后重新生成计划，随后才出现“正在调用 LLM 生成 ImplementationPatchDraft”。
- CLI 审批提示列出 `implementation_plan.md (implementation/implementation_plan.md)`、`implementation_patch_draft.json (implementation/implementation_patch_draft.json)` 以及 `models.py (todo_manager/models.py)` 等短文件名加相对路径。
- `workflow.log` 记录了 `generation_kind=plan_generation` 与 `generation_kind=patch_generation`。
- `implementation/implementation_plan.json` 未命中 `old_content/new_content/patch/diff`。
- testing 阶段独立生成 `TestingPlan` 和 `TestingPatchDraft`。
- Agent 自测结果：`105 passed, 0 failed, 0 errors, 0 skipped`。
- 最终状态：`FINAL_STATUS=succeeded`。

生成软件体验：

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

实际输出覆盖了空列表、添加任务、列表展示、完成任务、按状态过滤和删除任务，行为符合公开需求。

补充说明：本次脚本化审批通过 PowerShell here-string 传入中文反馈时，`decision_trace.jsonl` 中 comment 字段被当前终端编码替换为问号；结构化字段 `decision_type=respond/approve`、`decision_source=user`、`presented_to_user=true` 正确。真实 questionary 交互不依赖该脚本输入方式。

## 剩余限制

- 终端 hyperlink 依赖终端支持情况，目前稳定输出为 `文件名 (相对路径)`，不强依赖 VS Code 可点击协议。
- 真实 LLM 对复杂任务仍可能生成不稳定补丁；系统现在会把问题暴露在计划、补丁、测试和 workflow 日志中，而不是静默跳过。
- benchmark 自动审批仍不会停下来要求人工确认，但 decision trace 会记录为 `benchmark_auto`，并且也走“先计划、后补丁”的两步生成。
