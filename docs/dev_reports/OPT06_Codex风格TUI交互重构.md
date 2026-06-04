# OPT06 Codex 风格 TUI 交互重构汇报

## 背景

原 `codeagent wizard` 使用 questionary 串行提问。它能完成选择题和多选题，但体验仍像“逐题填空”：用户必须按固定顺序回答，想回头修改需要重新启动；运行阶段和审批阶段也和表单风格割裂。用户希望参考 openai/codex CLI 的 TUI 风格，但不需要固定聊天输入框。

本轮采用 Python `prompt_toolkit` 实现 CodeAgent 自己的终端交互层，目标是保留 CLI 产品属性，同时让 wizard 更像可回看、可修改、原地更新的问卷式任务面板。

## 实现内容

- 新增 `codeagent/cli/tui.py`：
  - `WizardFormState`：可测试的表单状态模型，支持字段移动、任意字段更新和字段选项内联展开。
  - `CodexLikeWizardSession`：Codex 风格 wizard 会话，用户可按任意顺序编辑基础设置、输入材料、运行策略、模型与审批模式。
  - `PromptToolkitTuiDriver`：基于 `prompt_toolkit` 的选择、多选和文本输入控件。
  - `TuiApprovalConsole`：复用既有 `ApprovalDecision`，把审批渲染为同一风格的选择面板。
  - `TuiProgressReporter`：消费现有 workflow events，以滚动记录方式展示阶段、工具、测试和最终状态。
- `codeagent wizard` 默认在 TTY 中启动新 TUI；非 TTY 环境使用脚本式行输入，TTY 中的 TUI 初始化错误会直接暴露，避免用降级掩盖环境问题。
- wizard 新增模型选择字段，固定候选包括：
  - `anthropic/claude-opus-4.8`
  - `anthropic/claude-sonnet-4.6`
  - `openai/gpt-5.5`
  - `google/gemini-3.5-flash`
  - `deepseek/deepseek-v4-pro`
  - `minimax/minimax-m3`
  - `qwen/qwen3.7-max`
- `build_task_config_from_answers` 将模型选择写入 `TaskConfig.model.model_name`，运行目录中的 `task_config.yaml` 因此可以复现同一模型配置。
- `execute_task_config` 增加 `approval_console` 注入点。wizard 使用 `TuiApprovalConsole`，普通 `run/benchmark` 默认继续使用原审批控制台。
- `prompt-toolkit>=3,<4` 被加入显式依赖，避免依赖 questionary 的间接安装。

## 用户体验变化

新 wizard 不再要求用户按固定顺序回答问题。表单分成五组：

1. 基础设置
2. 输入材料
3. 运行策略
4. 模型与审批
5. 最终确认

用户可以使用 `↑/↓` 移动、`Enter` 编辑、`Space` 展开当前字段选项或多选、`Ctrl+S` 开始运行、`Ctrl+C` 取消。确认后仍然直接启动 Agent，不需要再运行 `run --config`。

配置期间，任务表单以非全屏方式固定在终端中原地更新；字段选项直接在当前字段下方展开，选择后收起。开始运行后，审批和阶段进度使用同一套 Codex 风格输出。计划/补丁审批仍保持“同意”与“提出修改意见”两个核心选项；命令审批保留必要安全选项。

## 测试与验证

新增和更新的测试覆盖：

- 表单状态 reducer：移动、展开/收起、字段更新。
- wizard 任意顺序编辑：模型、审批模式、项目目录、输入材料、输出目录、测试命令。
- 模型选择默认值和七个固定候选。
- 表单校验失败时不直接启动运行。
- TUI 审批 adapter：计划/补丁审批支持 `approve/respond`，`respond` 必须填写意见。
- 进度 reporter：测试结果等 workflow event 能渲染为中文滚动输出。
- 非 TTY fallback：`codeagent wizard` 仍能通过脚本式输入构建配置并直接运行。

已执行验证命令：

```powershell
python -m py_compile codeagent\cli\tui.py codeagent\cli\wizard.py codeagent\cli\approval_console.py codeagent\cli\executor.py
python -m pytest tests\unit\cli tests\integration\test_cli_wizard.py -q
python -m pytest tests\integration\test_cli_run.py::test_testing_plan_response_regenerates_tests_without_entering_debug -q
```

## 已知限制

- 真实 TTY 的视觉效果需要在 VS Code/Windows Terminal 中人工体验；自动化测试主要覆盖状态机和适配器，不直接截图终端 UI。
- 本轮没有把模型选择扩展到 `run --model` 或各阶段子命令，配置文件和 benchmark 仍通过 `model.model_name` 控制模型。
- 本轮没有引入鼠标交互。键盘优先更适合 Windows 终端兼容性和自动化测试。
- `questionary` 仍保留在依赖中，因为普通审批控制台和历史测试仍可使用它；wizard 默认入口已不再依赖 questionary。

## 结论

本轮重构把 wizard 从“逐题问答”升级为可回看、可修改、可直接运行的 Codex 风格 TUI，同时保持原有 CLI 工作流、审批记录、运行产物和 benchmark 机制不变。模型选择被纳入普通用户入口，半交互演示能够更清楚地展示“填写任务 -> 选择模型 -> 审批 -> 观看 Agent 运行 -> 体验生成软件”的完整链路。
