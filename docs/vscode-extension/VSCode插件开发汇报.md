# VS Code Webview 插件开发汇报

## 背景

本次开发补齐 CodeAgent 的 IDE 集成能力。插件目标不是重写智能体，而是在 VS Code 内提供更适合鼠标和键盘操作的任务配置、运行进度、文件跳转和人工审批界面。智能体实际执行仍然复用现有 Python CLI、`TaskConfig`、LangGraph 工作流、HITL 审批、运行产物和审计日志。

开发分支：

```text
codex/vscode-extension
```

## 实现内容

### Python CLI 桥接层

新增 `codeagent/cli/plugin_bridge.py`，提供 VS Code 插件专用 JSONL 协议：

- `PluginProgressReporter`：把现有 workflow event 转为 stdout JSONL。
- `PluginApprovalConsole`：把审批请求发送给插件，并从 stdin 读取审批决策。
- `run_vscode_bridge()`：加载插件生成的任务配置，调用现有 `execute_task_config()`。

`codeagent/cli/app.py` 新增命令：

```powershell
python -m codeagent vscode-run --config <task.json|task.yaml>
```

`codeagent/cli/executor.py` 增加轻量扩展点：

- reporter 和 approval console 可在 run context 创建后接收 `bind_run_context(context)`。
- 支持插件审批控制台接管审批上下文展示，避免普通终端提示混入 JSONL stdout。

普通 `wizard`、`run`、阶段子命令和 benchmark 的行为保持不变。

### VS Code 扩展

新增目录：

```text
vscode-extension/
```

技术栈：

- TypeScript
- React
- Vite
- VS Code Extension API
- Vitest

主要文件：

- `src/extension.ts`：注册 `codeagent.openPanel`，创建 WebviewPanel，生成临时 task config，启动 Python 子进程，处理文件打开和审批回传。
- `src/bridge.ts`：JSONL 分块解析、任务配置生成、审批决策序列化。
- `src/common/protocol.ts`：Extension Host 与 Webview 共享消息类型。
- `src/webview/App.tsx`：Webview 主界面。
- `src/webview/styles.css`：VS Code 主题适配样式。

## 交互能力

插件面板打开后直接显示任务表单，字段与当前 CLI wizard 对齐：

- 执行阶段组合
- 项目目录
- 输出目录
- 测试命令
- 模型
- 审批模式
- 输入材料

输入材料支持：

- 从 VS Code 弹窗选择文件。
- 拖拽文件到材料区域；若 VS Code 或系统限制导致 Webview 无法读取路径，可使用选择文件按钮。
- 文件名以蓝色显示，悬停展示路径，点击可在 VS Code 中打开。

运行时界面包含：

- 顶部阶段进度条：填写配置信息、用户选择的阶段、已完成。
- 当前节点面板：显示当前阶段和最新 Agent 状态，并有轻量运行中动画。
- 历史节点：可展开或收起，收起后只保留当前最新节点。
- 运行配置快照：运行中可展开查看启动前表单内容。
- 底部审批栏：当 Python CLI 请求人工审批时弹出选择题组件，支持批准、反馈、编辑、拒绝和取消等决策。

## 桥接协议

Python stdout 输出 JSONL：

```json
{"type":"run_started","run_id":"...","run_dir":"...","task_config":{}}
{"type":"workflow_event","event":{},"line":"..."}
{"type":"approval_requested","request":{},"context":{},"choices":[]}
{"type":"run_completed","run_id":"...","final_status":"succeeded","stage_results":{}}
{"type":"error","code":"...","message":"...","retryable":false}
```

插件 stdin 回传审批：

```json
{"interrupt_id":"implementation_plan","decision_type":"approve"}
{"interrupt_id":"test_patch","decision_type":"respond","comment":"请增加边界测试。"}
{"interrupt_id":"testing_command","decision_type":"edit","edited_payload":{"command":"python -m pytest -q"}}
```

该协议让 VS Code 插件不需要解析 Rich/TTY 文本，审批、进度、文件引用都使用结构化数据。

## 使用方式

安装 Python 包：

```powershell
python -m pip install -e ".[dev]"
```

构建扩展：

```powershell
cd vscode-extension
npm install
npm run compile
```

在 VS Code 扩展开发宿主中运行命令：

```text
CodeAgent: Open Panel
```

如 Python 命令不是 `python`，可在 VS Code 设置中配置：

```json
{
  "codeagent.pythonPath": "python"
}
```

## 验证记录

已执行：

```powershell
python -m py_compile codeagent\cli\plugin_bridge.py codeagent\cli\app.py codeagent\cli\executor.py
python -m pytest -q tests\unit\cli\test_plugin_bridge.py tests\test_cli_contract.py
cd vscode-extension
npm test
npm run compile
```

结果：

- Python 桥接层与 CLI 契约测试通过。
- VS Code 扩展 Vitest 测试通过。
- TypeScript 编译与 Vite Webview 构建通过。

## 已知限制

- 插件 MVP 不启动常驻 HTTP 服务，只通过 Python 子进程 stdin/stdout 通信。
- Webview 从 VS Code Explorer 拖拽文件时，部分 VS Code 版本或安全策略可能无法把真实路径暴露给 Webview；文件选择按钮是稳定兜底入口。
- 当前没有加入 VS Code 官方扩展集成测试框架，自动化测试主要覆盖桥接解析、配置生成和审批序列化。
- 真正调用 LLM 的端到端运行仍依赖本机环境变量 `OPENROUTER_API_KEY`、模型可用性和目标项目测试环境。

## 结论

本次实现完成了 CodeAgent 的 VS Code Webview 插件 MVP。插件以可视化表单和审批面板改善人机交互，同时保持智能体主体、审计产物、checkpoint、HITL 和报告机制仍由现有 Python CLI 承担，符合“IDE 插件负责 UI，智能体运行仍在 CLI 中”的设计目标。
