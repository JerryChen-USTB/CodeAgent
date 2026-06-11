# VS Code 插件调试指导手册

本文面向 CodeAgent VS Code Webview 插件的开发、调试和演示验收。插件只负责 IDE 内的表单、文件选择、进度展示和人工审批；智能体实际运行仍由 Python CLI 通过 `python -m codeagent vscode-run --config <task>` 完成。

## 1. 调试前准备

### 1.1 安装 Python 包

在普通 PowerShell 中执行：

```powershell
$RepoRoot = "D:\Projects\CodeAgent"
python -m pip install -e "$RepoRoot"
python -m codeagent --help
```

如果 `--help` 能正常显示 `run`、`wizard`、`vscode-run` 等命令，说明插件后端桥接命令可用。

### 1.2 安装并构建插件

进入插件目录：

```powershell
cd D:\Projects\CodeAgent\vscode-extension
npm install
npm run compile
npm test
```

期望结果：

- `npm run compile` 成功生成 `dist/webview/main.js` 和 `dist/webview/style.css`。
- `npm test` 通过 Vitest 单元测试。

### 1.3 检查 API Key

如果要真实运行 Agent，需要确认当前环境可读取 OpenRouter API Key：

```powershell
python -c "import os; print('OPENROUTER_API_KEY configured:', bool(os.environ.get('OPENROUTER_API_KEY')))"
```

期望输出：

```text
OPENROUTER_API_KEY configured: True
```

## 2. 启动扩展开发宿主

### 2.1 打开插件源码目录

用 VS Code 打开：

```text
D:\Projects\CodeAgent\vscode-extension
```

注意：这里打开的是插件工程，不是 CodeAgent 仓库根目录。

### 2.2 按 F5 启动

在 `vscode-extension` 窗口中按 `F5`。

正常情况下会弹出一个新的 VS Code 窗口，标题通常带有“扩展开发宿主”。这个新窗口才是用来测试插件的窗口。

如果 VS Code 弹出“选择调试器”，优先选择 VS Code 扩展调试相关配置；若没有配置，先确认 `vscode-extension/.vscode/launch.json` 存在，并重新打开插件目录。

### 2.3 打开 CodeAgent 面板

在“扩展开发宿主”窗口中打开命令面板：

```text
Ctrl + Shift + P
```

执行：

```text
CodeAgent: Open Panel
```

正常会出现 CodeAgent Webview 面板。

## 3. 准备 Todo Manager 演示工作区

不要在 `D:\Projects\CodeAgent` 仓库根目录里直接运行案例。推荐创建一个独立演示空间：

下面命令假设 CodeAgent 仓库根目录是 `D:\Projects\CodeAgent`。如果你的仓库位置不同，请先修改 `$RepoRoot`，并把后文中可以直接复制的路径按你的实际仓库位置替换。

```powershell
$RepoRoot = "D:\Projects\CodeAgent"
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$DemoRoot = Join-Path $RepoRoot "codeagent_runs\demos\todo_manager\vscode_$Stamp"

New-Item -ItemType Directory -Force -Path `
  "$DemoRoot\interactive\input", `
  "$DemoRoot\interactive\workspace", `
  "$DemoRoot\interactive\runs" | Out-Null

$InputFiles = @("PRD.md", "user_stories.md", "design_model.md", "acceptance_criteria.md")
foreach ($Name in $InputFiles) {
  Copy-Item "$RepoRoot\benchmark\selfbuilt\cases\01_todo_manager\input\$Name" "$DemoRoot\interactive\input\$Name" -Force
}

explorer $DemoRoot
Set-Clipboard $DemoRoot
Write-Host "请在 VS Code 的“打开文件夹”中粘贴这个路径："
Write-Host $DemoRoot
```

然后在“扩展开发宿主”窗口中选择：

```text
文件 -> 打开文件夹...
```

打开刚才创建的 `$DemoRoot` 目录。上面的命令会把实际路径复制到剪贴板，也会在 PowerShell 中打印出来；在“打开文件夹”对话框里直接粘贴即可。

路径格式类似：

```text
D:\Projects\CodeAgent\codeagent_runs\demos\todo_manager\vscode_20260611_153000
```

如果你修改了 `$RepoRoot`，例如仓库在 `E:\Code\CodeAgent`，则路径格式类似：

```text
E:\Code\CodeAgent\codeagent_runs\demos\todo_manager\vscode_20260611_153000
```

注意：上面的 `20260611_153000` 只是示例时间戳，实际应以 PowerShell 打印出来、或已复制到剪贴板的 `$DemoRoot` 为准。

## 4. 填写插件表单

重新执行 `CodeAgent: Open Panel` 后，按下面方式填写：

| 字段 | 推荐值 |
|---|---|
| 执行阶段 | 完整流水线：实现 + 测试 + 调试 + 修复 |
| 项目目录 | 下拉选择 `interactive\workspace` |
| 输出目录 | 下拉选择 `interactive\runs` |
| 测试命令 | `python -m pytest -q` |
| 模型 | `google/gemini-3.5-flash` 或本次验收指定模型 |
| 审批模式 | 第一次验收建议人工审批 |

目录下拉菜单只显示当前工作区下的相对路径，例如：

```text
interactive\workspace
interactive\runs
```

选择完成后，输入框和下方只读路径区应显示完整绝对路径。

输入材料添加四个文件：

```text
interactive\input\PRD.md
interactive\input\user_stories.md
interactive\input\design_model.md
interactive\input\acceptance_criteria.md
```

可从左侧资源管理器拖入，也可以点击“选择文件”。

## 5. 运行时验收点

点击“启动”后，应重点观察：

- 顶部阶段进度条是否进入“实现 / 测试 / 调试 / 修复”。
- 当前节点卡片是否显示正在执行的阶段和状态。
- 节点历史是否持续追加事件。
- “展开配置信息”是否能看到运行前表单快照。
- 人工审批时底部是否弹出审批组件。
- 审批组件中的文件按钮是否能在 VS Code 中打开文件。
- 运行中“启动”按钮是否切换为红色“停止”按钮。

人工审批建议第一次不要直接一路批准，至少检查：

- 实现计划是否明确 TUI、JSON 持久化、`python -m todo_manager`。
- 实现补丁是否只写入演示工作区。
- 测试计划是否包含 TUI 连续交互和持久化验证。

## 6. 如何中途停止运行

### 6.1 新版插件

运行中点击表单右上角红色“停止”按钮。

Windows 下扩展会使用：

```text
taskkill /T /F
```

结束 Python 子进程树，避免只杀父进程导致子进程残留。

### 6.2 旧版插件或按钮不可用

优先使用下面两种方式：

1. 关闭 CodeAgent Webview 面板。
2. 回到插件源码窗口，按 `Shift + F5` 停止扩展调试。

如果仍有残留进程，可在 PowerShell 中查看：

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -like '*codeagent*' -and $_.CommandLine -like '*vscode-run*' } |
  Select-Object ProcessId, CommandLine
```

确认是插件启动的进程后结束：

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -like '*codeagent*' -and $_.CommandLine -like '*vscode-run*' } |
  ForEach-Object { taskkill /PID $_.ProcessId /T /F }
```

## 7. 日志和诊断入口

### 7.1 CodeAgent 输出通道

在扩展开发宿主或源码窗口底部打开：

```text
输出 -> CodeAgent
```

正常启动面板时应看到类似：

```text
CodeAgent extension activated.
Command codeagent.openPanel invoked.
Creating CodeAgent webview panel.
Webview frontend is ready.
```

如果没有 `Webview frontend is ready.`，说明 Webview 前端脚本没有成功运行。

### 7.2 Webview 开发者工具

在扩展开发宿主窗口中打开命令面板，执行：

```text
Developer: Open Webview Developer Tools
```

重点看 Console 中的红色错误，包括：

- CSP 拦截脚本或样式。
- `main.js` 加载失败。
- React 运行时异常。
- `acquireVsCodeApi` 调用异常。

### 7.3 运行产物

Agent 运行后，打开输出目录下最新 run：

```text
interactive\runs\<latest-run>
```

重点检查：

```text
final_report.md
task_config.yaml
metadata.json
workflow.log
workflow_events.jsonl
decision_trace.jsonl
implementation\implementation_plan.md
testing\test_plan.md
testing\test_result.json
```

## 8. 常见问题处理

### 8.1 面板空白

先在插件目录重新构建：

```powershell
cd D:\Projects\CodeAgent\vscode-extension
npm run compile
```

然后关闭扩展开发宿主，回到源码窗口重新按 `F5`。

如果仍然空白，检查 `输出 -> CodeAgent` 是否有：

```text
Webview script URI
Webview style URI
```

再打开 Webview Developer Tools 看 Console 错误。

### 8.2 卡在“CodeAgent 面板正在加载...”

通常说明 HTML 已加载，但前端 JS 没有执行。排查顺序：

1. 确认 `dist/webview/main.js` 存在。
2. 执行 `npm run compile`。
3. 检查 Webview Developer Tools 的 Console。
4. 检查 CSP 是否允许 `${webview.cspSource}` 和 nonce 脚本。

### 8.3 中文乱码

新版插件启动 Python 时会设置：

```text
PYTHONIOENCODING=utf-8
PYTHONUTF8=1
```

如果仍然乱码：

1. 先确认已经重启扩展开发宿主，旧运行无法恢复。
2. 重新启动一次 CodeAgent 运行。
3. 检查 `workflow.log`、`workflow_events.jsonl` 中原始内容是否正常。
4. 若仅 PowerShell 乱码，可尝试：

```powershell
chcp 65001
```

### 8.4 拖拽文件变成在编辑区打开

新版 Webview 会在全局接管 `dragover/drop`，正常不需要按 `Shift`。

如果仍发生：

1. 确认已重新 `npm run compile` 并重新按 `F5`。
2. 尽量把文件拖到 CodeAgent 面板内。
3. 使用“选择文件”按钮作为等价入口。

### 8.5 同一个输入材料出现两次

新版 Webview 会把 `file:///D:/...`、`/D:/...`、`D:\...` 等格式归一化后再去重。

如果旧面板里已经重复，点击文件标签右侧的 `x` 删除重复项，或重新打开面板。

### 8.6 目录下拉显示不完整或风格发白

不要使用原生 `datalist`。当前实现是自绘 Combobox：

- 下拉列表显示相对路径。
- 选中后输入框和只读路径区显示绝对路径。
- 菜单颜色使用 VS Code 主题变量。

如果看到大片白色候选列表，说明仍在使用旧构建，需要重新编译并重启扩展开发宿主。

### 8.7 项目目录填错

正式启动前必须确认：

```text
项目目录 = <DemoRoot>\interactive\workspace
输出目录 = <DemoRoot>\interactive\runs
```

如果使用第 3 节默认演示目录，可以直接对照下面两个绝对路径：

```text
项目目录 = D:\Projects\CodeAgent\codeagent_runs\demos\todo_manager\vscode_20260611_153000\interactive\workspace
输出目录 = D:\Projects\CodeAgent\codeagent_runs\demos\todo_manager\vscode_20260611_153000\interactive\runs
```

如果你的 `$RepoRoot` 不是 `D:\Projects\CodeAgent`，请把上面路径开头替换成你的实际仓库根目录；时间戳部分也应替换成第 3 节 PowerShell 实际打印出来的目录名。

不要把项目目录填成：

```text
D:\Projects\CodeAgent
```

否则 Agent 可能会把任务产物写进 CodeAgent 仓库源码目录。

## 9. 修改插件后的固定验证

每次修改 `vscode-extension/src` 后，至少执行：

```powershell
cd D:\Projects\CodeAgent\vscode-extension
npm run compile
npm test
```

如果改动涉及 Python bridge，还应执行：

```powershell
cd D:\Projects\CodeAgent
python -m pytest tests\unit\cli tests\unit\workflow -q
python -m pytest -q tests\test_package_smoke.py tests\test_cli_contract.py
```

## 10. 最小调试闭环

一次完整插件调试建议按下面顺序：

1. `npm run compile`
2. `npm test`
3. 源码窗口按 `F5`
4. 扩展开发宿主打开演示根目录
5. 执行 `CodeAgent: Open Panel`
6. 填写表单并拖入四份材料
7. 点击“启动”
8. 检查进度条、节点历史、审批弹窗、文件打开
9. 必要时点击“停止”
10. 查看 `输出 -> CodeAgent` 和最新 run 目录

这个闭环能覆盖插件 UI、Webview 通信、Python 子进程、JSONL 协议、人工审批和运行产物生成。
