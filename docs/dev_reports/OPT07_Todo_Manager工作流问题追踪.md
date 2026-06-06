# OPT07 Todo Manager 工作流问题追踪

## 背景

本文记录 Todo Manager 自建 benchmark 在半交互演示中暴露出的工作流问题，作为后续集中修复和验收依据。

相关运行：

- 最新运行：`codeagent_runs/demos/todo_manager/20260605_221901/interactive/runs/2026-06-05_141953_591251_implement-test-debug-repair_a2db85`
- 上一轮追溯运行：`codeagent_runs/demos/todo_manager/20260605_211947/interactive/runs/2026-06-05_132107_217931_implement-test-debug-repair_c630a7`

## 问题清单

### P0-01 测试阶段出现假成功，只运行了最后一个测试文件

现象：

- 测试计划要求覆盖 `tests/test_models.py`、`tests/test_storage.py`、`tests/test_service.py`、`tests/test_tui.py`、`tests/test_app.py`。
- `testing/test_plan.md` 中的测试命令为 `python -m pytest -q`。
- 实际审批通过后执行的是 `python -m pytest tests/test_app.py -q`。
- 只运行 `tests/test_app.py` 中的 5 个用例，全部通过后工作流直接结束为成功。

证据：

- `testing/test_command.json` 记录的命令为 `python -m pytest tests/test_app.py -q`。
- `testing/test_result.json` 记录 `5 passed, 0 failed, total=5`。
- `workflow_events.jsonl` 中 `approve_test_command` 的 payload 已经包含该窄命令，但用户审批界面没有看到命令文本。

初步原因：

- 增量单文件测试补丁会将多个 `TestingPatchDraft` 合并。
- `_combine_testing_patch_drafts()` 当前按顺序遍历每个单文件 draft，并用 `command = typed.command or command` 更新命令。
- 最后一个单文件补丁是 `tests/test_app.py`，它的 draft command 覆盖了计划命令，导致最终只运行最后一个测试文件。
- `TestingService.apply_patch_and_prepare_command()` 又优先使用 `draft.command`，因此窄命令被带入命令审批和实际执行。

影响：

- 严重。会让 Agent 误以为测试阶段成功，跳过 debug/repair。
- benchmark 结果被高估，演示结果不可信。

建议修复：

- 增量测试阶段合并补丁时，最终命令应优先使用已审批 `TestingPlan.command` 或配置的公开测试命令。
- 单文件 draft 的 command 只能作为局部建议，不应覆盖最终全量验证命令。
- 增加质量门：若测试计划生成了多个测试文件，最终命令不能只引用其中一个文件，除非计划明确说明只需验证单文件且人工审批可见。
- 对 Todo Manager benchmark，推荐最终命令固定为 `python -m pytest -q` 或 `python -m pytest tests -q`。

### P0-02 命令审批界面没有显示即将执行的命令

现象：

- CLI 询问“运行此测试命令？”时，只显示了待审查文件列表和“同意后会在项目目录中执行命令”。
- 用户无法在审批前看到实际命令。
- 批准后才看到工具执行了 `python -m pytest tests/test_app.py -q`。

证据：

- `workflow_events.jsonl` 中 `approval_requested` 的 payload 包含 `"command": "python -m pytest tests/test_app.py -q"`。
- 紧接着的 `approval_context_presented` 只包含 `files` 和 `hint`，不包含 command。
- `_print_approval_context()` 当前只打印 `_approval_context_refs()` 返回的文件引用和 `_approval_hint()`。

影响：

- 高。命令是副作用审批的核心内容，不可见会让人工审批失去意义。
- 与“减少命令行操作、清楚说明命令做什么”的演示手册要求冲突。

建议修复：

- 对所有 command 审批，在选项之前明确打印：
  - 将执行的命令。
  - 工作目录 cwd。
  - 若命令来自计划或补丁 draft，应显示来源。
- `approval_context_presented` 事件也应记录 command 和 cwd，便于溯源。
- 命令修改选项应基于可见命令进行编辑。

### P1-01 目标文件路径和关键文件路径看起来是静态文本，不能跳转打开

现象：

- 自动通过补丁时输出 `目标文件：test_app.py (tests/test_app.py)`，看起来是普通文本，不能像人工审批文件列表那样 Ctrl+单击打开。
- 最终关键文件列表也显示为普通文本。

证据：

- 人工审批文件列表使用 `_terminal_link(ref)` 输出。
- 自动通过补丁消息 `_emit_auto_approved_patch_message()` 使用 `target_ref.display`。
- 关键文件摘要 `_key_file_summary()` 返回 `[ref.display ...]`。

影响：

- 中高。用户刚开启“本阶段不再提示”后，最需要从自动通过日志快速打开目标文件核查结果。
- 当前表现与此前要求“这里的文件路径实现应该和人工审批那里一样”不一致。

建议修复：

- 自动通过补丁消息和最终关键文件摘要都改为使用与人工审批一致的 `DisplayPathRef` 和 `_terminal_link()`。
- 进度输出层需要确认不会剥离 OSC 8 终端链接序列。
- `workflow_events.jsonl` 仍记录纯文本 display，CLI 展示负责渲染链接。

### P1-02 修复阶段第一轮只净增 1 个通过用例

现象：

- 上一轮运行中，repair 前为 `86 passed, 6 failed`。
- repair 后为 `87 passed, 5 failed`。
- 表面看只多通过 1 个用例。

实际变化：

- 修好旧失败：`test_ac07_invalid_input`。
- 修好旧失败：`tests/test_tui.py::TestAddTask::test_add_value_error`。
- 仍失败但形态推进：`test_ac02_add_task` 从文件未创建推进为 `KeyError: 'done'`。
- 仍失败但形态推进：`test_storage.py::test_sorted_by_id_and_format` 从 `TypeError` 推进为格式断言失败。
- 仍失败：`test_ac01_empty_file_normal_exit`，只修了菜单关键词，没有创建空 JSON 文件。
- 新暴露或引入失败：`test_ac06_filter_by_status`，因为 `medium` 被接受后第二个任务开始出现在 stdout，测试要求全文不出现该标题。

初步原因：

- LLM 做了局部症状修复，但没有收敛到跨文件数据契约。
- 测试中同时存在 `status` 和持久化 `done` 的契约差异，需要跨 `models.py`、`storage.py`、`service.py`、`tui.py`、`app.py` 统一处理。
- 第一轮修复计划没有覆盖“空文件启动应创建 `[]`”、“JSON 需要 `done` 字段兼容”、“过滤测试对 stdout 全文敏感”等核心点。

影响：

- 中高。Agent 似乎在修复，但修复质量不稳定，容易进入多轮 debug/repair。

建议修复：

- repair plan prompt 应要求根据最新失败断言建立完整契约，不只修 traceback 表层错误。
- 单文件补丁前应携带与该文件相关的失败断言和最新 traceback，而不只携带压缩后的计划摘要。
- 修复计划需要明确列出“要保持兼容的字段和输出契约”。

### P1-03 修复后第二轮 repair 被旧失败证据污染

现象：

- 第一轮 repair 后的最新失败为 `5 failed, 87 passed`，其中包括 `KeyEr...` 和 `assert...`。
- 第二轮 repair plan 仍然描述 `validator` 不接受 `medium`、文件未创建等旧问题。
- 这些旧问题第一轮已经被修复或已转化为新的失败形态。

证据：

- 第二轮 repair planner prompt 同时包含旧的 `testing/test_result.json`：`6 failed, 86 passed`，以及最新 repair 后摘要：`5 failed, 87 passed`。
- 最新失败摘要中的关键错误被截断为 `KeyEr...`、`assert...`，而旧 traceback 中的 `FileNotFoundError` 信息更完整，容易误导 LLM。

影响：

- 高。后续修复会重复修改已解决问题，浪费轮次，甚至覆盖正确改动。

建议修复：

- 进入 retry repair 时，最新 `repair/repair_test_result.json` 和 `repair/logs/repair_verify.stdout.log` 应作为最高优先级证据。
- 旧的 `testing/test_result.json` 可以保留，但必须标记为 historical，不应与最新失败并列。
- 失败摘要不要截断关键异常类型和断言行；至少保留完整 nodeid、异常类型、断言表达式和当前/期望值。
- 生成一份 failure delta：旧失败、已修复、仍失败、新失败。

### P1-04 单文件补丁阶段信息从计划到补丁发生丢失

现象：

- 第一轮 repair 的总体计划 prompt 中能看到完整测试源码和关键断言，例如 `task["done"]`、`content == []`、`"写报告" not in result.stdout`。
- 但单文件 patch prompt 只带入 LLM 自己选择读取的少量文件和计划摘要。
- 写 `models.py`、`tui.py` 时没有稳定携带完整集成测试断言，因此没有修到 `done`、空文件创建和过滤 stdout 等契约。

影响：

- 中高。计划阶段“知道”的信息没有传到真正写代码的阶段。

建议修复：

- 为每个计划 change 绑定相关失败断言、相关测试片段和约束摘要。
- 单文件补丁 prompt 必须包含该目标文件相关的断言片段，而不是完全依赖 LLM 自主请求上下文。
- `PatchFileContextDecision` 可以继续存在，但应补充系统强制上下文。

### P1-05 多轮 repair 产物覆盖，溯源困难

现象：

- 同一个 run 中多次进入 repair 时，`repair_plan.md`、`repair_plan.json`、`debug_report.md`、`patch_file_decision_attempts.json`、`incremental_work_summary.md` 等固定文件名会被后续轮次覆盖。
- 第一轮完整计划和决策只能从 `workflow_events.jsonl` 回捞。

影响：

- 中。人工分析和自动诊断都变难。

建议修复：

- repair/debug 每次 attempt 使用子目录，例如 `repair/attempt_01/`、`repair/attempt_02/`。
- 顶层保留 latest 指针或汇总文件。
- workflow event 中记录 attempt 编号和对应 artifact path。

### P2-01 旧运行中 workflow_events.jsonl 出现非 JSON 行

现象：

- 上一轮运行的 `workflow_events.jsonl` 有 4 行不能 JSON 解析。
- 坏行内容是 prompt 片段，例如 `can be validated by Pydantic...`。
- 最新运行暂未复现该问题，`workflow_events.jsonl` 可完整解析。

影响：

- 中。事件日志是关键审计依据，一旦 JSONL 破损，自动分析脚本会失败。

建议修复：

- 检查 workflow trace 写入是否可能与流式输出或 logger 共享同一文件句柄。
- 增加 JSONL 写入单元测试和运行后 `workflow_events.jsonl` 自检。
- 即使保留 prompt 全文，也必须作为 JSON 字段写入，不能裸写。

### P2-02 运行中旧代码不会自动切换到新工作流

现象：

- 上一轮 run 仍出现 `PatchLoopDecision`，因为它在去掉自主调度前已经启动。
- 最新 run 已出现 `PatchFileContextDecision`，说明重启后才使用新逻辑。

影响：

- 低到中。开发调试时容易误判“修复没生效”。

建议修复：

- 演示手册和开发说明中补充：工作流代码调整后，需要启动新的 run 才能验证。
- 在 run metadata 中记录 git commit、branch、dirty 状态或代码版本摘要，便于追溯。

## 待修复优先级

1. P0-01：测试阶段最终命令不能被最后一个单文件 draft 覆盖。已修复。
2. P0-02：命令审批必须显示实际命令和 cwd。已修复。
3. P1-01：自动通过和关键文件路径使用与人工审批一致的可跳转链接。已修复。
4. P1-03：repair retry 必须优先使用最新失败证据，避免旧证据污染。已修复。
5. P1-04：单文件补丁 prompt 强制携带相关断言和失败上下文。
6. P1-05：debug/repair 多轮产物按 attempt 分目录保存。
7. P2-01：workflow_events.jsonl 写入自检。
8. P2-02：run metadata 记录代码版本。

## 本次修复记录

修复范围：P0-01、P0-02、P1-01。

- P0-01：增量测试补丁合并后，最终 `TestingPatchDraft.command` 固定使用已审批 `TestingPlan.command`，不再被最后一个单文件 draft 的局部命令覆盖；`TestingService` 的命令审批和执行也改为以计划命令为准。提示词同步要求单文件测试补丁不要把命令缩窄到当前文件。
- P0-02：命令审批上下文会在用户选择前显示即将执行的命令和工作目录；`approval_context_presented` 事件同步记录 `command` 和 `cwd`，便于事后溯源。
- P1-01：自动通过补丁消息中的目标文件、最终关键文件列表改为使用与人工审批一致的终端文件链接渲染；事件日志仍保留纯文本路径。

验证命令：

- `python -m ruff check codeagent/agents/plan_generation.py codeagent/cli/executor.py codeagent/stages/testing_service.py tests/integration/test_cli_run.py tests/unit/cli/test_terminal_links.py`
- `python -m pytest tests/unit/cli/test_terminal_links.py -q`
- `python -m pytest tests/integration/test_cli_run.py::test_incremental_testing_applies_file_patches_before_running_generated_tests -q`
- `python -m pytest tests/integration/test_cli_run.py::test_incremental_patch_approval_can_auto_approve_rest_of_stage -q`
- `python -m pytest tests/integration/test_testing_stage.py -q`
- `python -m pytest tests/integration/test_cli_run.py -q`
- `python -m pytest tests/unit/agents/test_plan_generation.py -q`

## 本次可观测性改进记录

修复范围：LLM 调用证据包与轻量事件索引。

- 所有通过 `PlanGenerationService._invoke_schema()` 发起的结构化 LLM 调用，都会在所属阶段目录下写入独立证据包：`<stage>/llm_calls/<编号>_<调用类型>_<Schema>/`。
- 每个证据包包含 `request.json`、顶层 `prompt.full.txt` / `prompt.manifest.json`、`call_summary.md`，并为每次尝试写入 `attempt_01/`、`attempt_02/` 等子目录。
- 每个 attempt 子目录包含完整脱敏 prompt、原始脱敏响应、解析后的结构化输出、校验状态与错误信息：`prompt.full.txt`、`response.raw.txt`、`response.parsed.json`、`validation.json`。
- `workflow_events.jsonl` 不再直接塞入 `_invoke_schema` 的完整 prompt、完整 response 或完整结构化 output，而是记录 `call_id`、`prompt_path`、`response_path`、`output_path`、`validation_path` 等索引字段。
- 原有 `plan_generation_attempts.json` / `patch_generation_attempts.json` 仍保留，用于兼容既有测试和快速查看重试摘要。
- 该改进先解决“LLM 每次看到什么、返回了什么、为什么通过/失败”的基础可观测性；后续还需要继续处理 repair/debug 多轮 attempt 归档、failure delta、最新失败证据优先级等问题。

验证命令：

- `python -m ruff check codeagent/agents/plan_generation.py tests/unit/agents/test_plan_generation.py`
- `python -m pytest tests/unit/agents/test_plan_generation.py -q`
- `python -m pytest tests/unit/runtime/test_run_context.py -q`
- `python -m pytest tests/integration/test_cli_run.py -q`
- `python -m pytest tests/integration/test_testing_stage.py -q`

## 本次文件路径渲染修复记录

修复范围：自动通过补丁日志与关键文件摘要中的可跳转路径。

- 问题原因不是路径计算错误，而是输出通道差异：人工审批上下文直接 `print()` 到终端，可以使用 `_terminal_link()` 拼接 OSC 8 终端超链接；自动通过补丁日志走 `emit_progress` -> LangGraph custom event -> Rich/TUI 进度渲染，不能把 OSC 8 控制序列当普通字符串塞进事件消息。
- 旧实现把 `_terminal_link()` 的结果直接写进 `agent_status.message`，在 Rich/TUI 中被半处理，导致用户看到裸露的 `]8;;` 和 `file:///...` URL，且无法正常 Ctrl+单击跳转。
- 新实现保留人工审批的直接输出方式不变；进度事件改为传递纯文本 `message` 加结构化 `message_link` / `file_links`，由 `ProgressReporter` / `TuiProgressReporter` 使用 Rich `Text(style="link ...")` 渲染。
- 自动通过补丁日志现在只显示目标文件名，例如 `models.py (todo_manager/models.py)`；URL 不再作为可见文本出现。
- 最终关键文件摘要也改为同一套结构化 link 渲染，避免 run 结束时再次出现裸露 OSC 8 序列。

验证命令：

- `python -m ruff check codeagent/cli/progress.py codeagent/cli/tui.py codeagent/cli/executor.py tests/unit/cli/test_terminal_links.py`
- `python -m pytest tests/unit/cli/test_terminal_links.py -q`
- `python -m pytest tests/integration/test_cli_run.py::test_incremental_patch_approval_can_auto_approve_rest_of_stage -q`
- `python -m pytest tests/unit/cli/test_tui.py -q`

## 本次补丁审查与重试可解释性修复记录

修复范围：减少不必要的单文件补丁重试，并在发生重试时向用户说明原因。

- `hardcoded_case` 风险规则从“一旦新增 `if x == "literal"` 就高危拦截”调整为上下文敏感：测试路径中的字面量分支仍按高危处理；产品代码中的普通字面量分支降为低风险 finding，不再导致 repair 风险检查直接拒绝。
- 单文件补丁因为校验失败、风险检查失败、人工反馈、应用失败而需要重试时，CLI/TUI 会输出简短原因，例如“单文件补丁 app.py 第 1 次未通过：补丁应用失败（...）；正在重新生成当前文件。”
- 对结构化 LLM 输出重试新增 `llm_retry_scheduled` 事件和 TUI 消息。当 LLM 第一次返回坏 JSON 或 schema 校验失败时，用户会看到“上次 LLM 输出未通过 ... 结构化校验；正在重试。”
- 单文件补丁通用提示词补充约束：不要针对单个失败输入、失败专用字面量或断言文本写精确匹配分支，优先在业务语义位置修复。

验证命令：

- `python -m ruff check codeagent/services/patch_service.py codeagent/cli/executor.py codeagent/agents/plan_generation.py tests/unit/tools/test_patch_service.py tests/integration/test_cli_run.py tests/unit/agents/test_plan_generation.py tests/integration/test_repair_stage.py`
- `python -m pytest tests/unit/tools/test_patch_service.py -q`
- `python -m pytest tests/unit/agents/test_plan_generation.py::test_plan_generation_records_schema_retry_reason -q`
- `python -m pytest tests/integration/test_cli_run.py::test_incremental_patch_retry_reports_reason_to_cli -q`
- `python -m pytest tests/integration/test_cli_run.py -q`
- `python -m pytest tests/unit/agents/test_plan_generation.py -q`
- `python -m pytest tests/integration/test_repair_stage.py -q`

## 本次 workflow_events.jsonl 写入加固记录

修复范围：防止新 run 的 `workflow_events.jsonl` 出现非 JSON 行，并为历史 run 提供旁路恢复能力。

- 根因判断为同一 run 内多个事件入口并发追加同一个 JSONL 文件，普通 `open("a").write()` 没有进程内互斥，可能导致事件片段交错或丢失。
- `WorkflowTraceRecorder.record()` 现在先完成 JSONL 行和 Markdown 日志片段序列化，再在同一路径锁内连续写入 `workflow_events.jsonl` 与 `workflow.log`，减少两份追踪产物顺序不一致。
- `JsonlRecorder.append()` 同步使用路径级写入锁，覆盖 `decision_trace.jsonl` 与 `transcript.jsonl` 的同类风险。
- 新增 JSONL 校验与历史恢复工具：可逐行报告坏行行号、错误类型和内容预览；可从 `workflow.log` 中的完整 JSON block 生成 `workflow_events.jsonl.repaired`，不覆盖原始文件。

验证命令：

- `python -m ruff check codeagent/reports/workflow_trace.py codeagent/reports/transcript.py codeagent/reports/jsonl_utils.py tests/unit/runtime/test_artifacts_and_logs.py`
- `python -m pytest tests/unit/runtime/test_artifacts_and_logs.py -q`

## 本次 repair 能力补强记录

修复范围：LLM 调试分析、最新失败证据优先、保守可见测试修复。

- 调试阶段新增结构化 `DebuggingAnalysis`，在静态定位后调用 LLM 输出 `failure_origin`、候选文件、证据、根因、修复策略、推荐验证命令和 `test_repair_allowed`。
- 调试阶段会写入 `debugging/llm_debug_analysis.json` 与 `debugging/llm_debug_analysis.md`，并把故障归因和测试修复许可合并到 `fault_localization.json`、`debug_report.md`、`debug_trace.jsonl`。
- repair/debug 的失败证据上下文优先读取最新 `repair/repair_test_result.json`、`repair/after_test.log`、`repair/repair_report.md`，再回退到 debugging 和 testing 产物，降低旧失败污染第二轮 repair plan 的概率。
- `RepairPlan` 新增 `failure_origin`、`test_repair_allowed`、`test_repair_rationale`；默认仍只修产品代码，只有调试分析明确授权 `generated_test_code`、`mixed` 或 `test_harness` 时才允许规划可见测试修复。
- `RepairRiskChecker` 保守放开普通可见测试文件修改，并继续拒绝隐藏 oracle、evaluation、expected_result、删除测试、skip/xfail、真正删除断言、`conftest.py`、pytest 配置和测试基础设施修改。
- 底层 patch 风险识别收窄 `test_assertion_removal`：同一测试文件中删除旧断言并新增新断言的替换场景不再被当作直接删除断言，避免明显坏测试修复被过度拦截。
- `fault_localization.json` 与 `llm_debug_analysis.json` 中的路径统一输出为 POSIX 风格，便于跨平台阅读和终端跳转。

验证命令：

- `python -m ruff check codeagent/stages/debugging_service.py codeagent/stages/repair_service.py codeagent/agents/plan_generation.py codeagent/tools/risk_checker.py codeagent/cli/executor.py codeagent/services/patch_service.py tests/integration/test_debugging_stage.py tests/integration/test_repair_stage.py tests/unit/agents/test_plan_generation.py`
- `python -m pytest tests/integration/test_debugging_stage.py -q`
- `python -m pytest tests/integration/test_repair_stage.py -q`
- `python -m pytest tests/unit/agents/test_plan_generation.py -q`

## 验收建议

- 重新运行 Todo Manager 半交互演示。
- testing 阶段生成 5 个测试文件后，命令审批界面必须显示 `python -m pytest -q` 或等价全量测试命令。
- 审批通过后，测试结果 total 应覆盖全部生成测试，而不是只有最后一个文件。
- 自动通过补丁日志中的目标文件支持 Ctrl+单击打开。
- 若测试失败进入 repair，第二轮 repair plan 必须围绕最新失败展开，不能重复修复已经解决的旧问题。
