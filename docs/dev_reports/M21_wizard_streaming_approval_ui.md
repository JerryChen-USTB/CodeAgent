# M21 Wizard、流式进度与审批 UI 汇报

## 目标

实现 CLI 半交互式向导、审批输入解析、流式事件进度展示和取消报告能力，为后续 M22 将 CLI 命令接入真实工作流执行做准备。

## 主要变更

- `codeagent/cli/wizard.py`
  - 新增 `WizardPromptAnswers`、`build_task_config_from_answers()`、`render_task_summary()`。
  - `codeagent wizard` 可采集阶段、项目目录、输入材料、输出目录、测试命令，并生成 `TaskConfig(mode="wizard")`。
  - 用户确认后初始化 `codeagent_runs/<run_id>/`；用户取消时写入 `wizard/stage_result.json` 和 `final_report.md`。
- `codeagent/cli/approval_console.py`
  - 新增审批输入解析，将 approve/edit/reject/respond/cancel 转换为 `ApprovalDecision`。
  - 支持 edit JSON payload，并按 `ApprovalRequest.allowed_decisions` 拒绝非法决策。
- `codeagent/cli/progress.py`
  - 新增 `ProgressEventFormatter` 和 `ProgressReporter.render_events()`。
  - 将 normalized workflow events 渲染为稳定的 CLI 文本，覆盖 stage、route、result、tool、final、approval 等事件。
- `tests/integration/test_cli_wizard.py`
  - 覆盖脚本化 wizard 输入、任务摘要、取消报告、审批决策、edit payload、进度渲染和项目路径目录校验。

## 设计决策

- M21 只实现 UI/controller 层，不启动业务阶段执行；真实 `run/implement/test/debug/repair` 命令接入留给 M22。
- 终端渲染保持薄层，核心行为通过纯函数和控制器测试，降低 Rich/交互式测试的不稳定性。
- `project_path` 必须是已存在目录；输入材料允许是已存在文件或目录，以便兼容需求文档、设计材料和项目资料。
- 取消操作会创建运行目录并写最终报告，但不会修改项目源码。

## 使用方式

```bash
codeagent wizard
```

向导依次要求输入阶段、项目路径、输入材料路径、输出目录和测试命令。确认后会初始化运行目录；取消后会写 cancelled final report。

## 验证记录

- `python -m pytest tests\integration\test_cli_wizard.py -q` -> 11 passed。
- `python -m pytest tests\integration\test_cli_wizard.py tests\test_cli_contract.py tests\unit\workflow tests\unit\reports tests\unit\runtime -q` -> 61 passed。
- `python -m pytest -q` -> 224 passed。
- `python -m compileall -q codeagent` -> passed。
- `python -m codeagent --help` -> passed。
- `python -m codeagent wizard --help` -> passed。
- `codeagent --help` -> passed。

## 审查结论

- 规格审查：PASS。
- 质量审查：首次发现项目路径可误接受普通文件的 P2 问题；已通过目录校验和回归测试修复，复审 APPROVED。

## 限制与后续

- M21 不消费 OpenRouter token；真实 LLM 调用验证仍按计划在最终示例/benchmark 前使用 `OPENROUTER_API_KEY` 受控执行，且不得打印或持久化密钥。
- M22 需要将已生成的 `TaskConfig` 接入 `run --config`、阶段子命令和 LangGraph 执行流。
