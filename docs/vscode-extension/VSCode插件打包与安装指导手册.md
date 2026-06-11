# VS Code 插件打包与安装指导手册

本文档说明如何将 CodeAgent VS Code 插件打包为 VSIX 文件，并通过 VS Code 图形界面安装使用。

## 1. 适用范围

当前 VS Code 插件只负责 Webview 界面、任务配置、文件选择、进度展示和人工审批交互。智能体实际执行仍依赖本机 Python 环境中的 CodeAgent CLI。

因此，安装 VSIX 前需要确认：

- 本机已安装 VS Code。
- 本机已安装 Node.js 和 npm，用于插件构建与打包。
- 本机 Python 环境中已经可以运行 CodeAgent CLI。
- 当前仓库路径示例为 `D:\Projects\CodeAgent`。如果你的仓库路径不同，请把文档中的路径替换成实际路径。

## 2. 插件目录与产物位置

插件源码目录：

```text
D:\Projects\CodeAgent\vscode-extension
```

打包后生成的 VSIX 文件位置：

```text
D:\Projects\CodeAgent\vscode-extension\codeagent-vscode-0.1.0.vsix
```

如果版本号发生变化，VSIX 文件名中的 `0.1.0` 也会随之变化，以实际生成的文件名为准。

## 3. 打包 VSIX

在 VS Code 中打开插件目录：

```text
D:\Projects\CodeAgent\vscode-extension
```

打开 VS Code 集成终端后，依次执行：

```powershell
npm install
npm run compile
npx @vscode/vsce package --no-dependencies --out codeagent-vscode-0.1.0.vsix
```

执行完成后，在 `vscode-extension` 目录下应能看到：

```text
codeagent-vscode-0.1.0.vsix
```

如果只修改了前端或扩展代码，通常重新执行 `npm run compile` 和 `npx @vscode/vsce package ...` 即可。

## 4. 通过 VS Code 图形界面安装

安装方法只使用 VS Code 图形界面，不需要命令行。

1. 打开 VS Code。
2. 点击左侧活动栏中的“扩展”图标。
3. 点击扩展面板右上角的 `...` 菜单。
4. 选择“从 VSIX 安装...”。
5. 在文件选择窗口中选择：

```text
D:\Projects\CodeAgent\vscode-extension\codeagent-vscode-0.1.0.vsix
```

6. 等待 VS Code 完成安装。
7. 如果 VS Code 提示重新加载窗口，点击“重新加载”。

安装完成后，左侧活动栏应出现 CodeAgent 图标。

## 5. 配置 Python 路径

插件启动智能体时会调用 Python CLI，因此需要确保插件使用的 Python 环境中已经安装并可运行 CodeAgent。

如果默认的 `python` 就是正确环境，可以不改设置。

如果需要指定虚拟环境中的 Python，请使用 VS Code 图形界面配置：

1. 打开 VS Code 设置。
2. 搜索 `CodeAgent: Python Path`。
3. 将其设置为实际 Python 解释器路径，例如：

```text
D:\Projects\CodeAgent\.venv\Scripts\python.exe
```

不同机器上的路径可能不同，请以实际环境为准。

## 6. 安装后使用

1. 用 VS Code 打开需要验收或开发的项目目录。
2. 点击左侧活动栏中的 CodeAgent 图标。
3. 在侧边栏中点击“打开 CodeAgent 面板”。
4. 在主编辑区的 CodeAgent 面板中填写任务表单：
   - 执行阶段
   - 项目目录
   - 输出目录
   - 测试命令
   - 模型
   - 审批模式
   - 输入材料
5. 点击“启动”开始运行。

运行过程中可以在面板中查看当前节点、历史节点、审批弹窗和最终运行结果。

## 7. 更新安装

如果重新打包了新的 VSIX，需要重新安装：

1. 打开 VS Code 的“扩展”面板。
2. 找到已安装的 CodeAgent 插件。
3. 可以先卸载旧版本，也可以直接再次使用“从 VSIX 安装...”选择新的 VSIX 文件。
4. 安装完成后，根据 VS Code 提示重新加载窗口。

如果左侧 CodeAgent 图标或面板内容没有更新，优先尝试重新加载 VS Code 窗口。

## 8. 常见检查项

如果安装后没有看到左侧 CodeAgent 图标：

- 确认安装的是最新打包出来的 VSIX。
- 重新加载 VS Code 窗口。
- 在“扩展”面板中确认 CodeAgent 插件处于启用状态。

如果点击“打开 CodeAgent 面板”后无法运行任务：

- 检查 `CodeAgent: Python Path` 是否指向正确 Python 环境。
- 检查该 Python 环境中是否已经安装 CodeAgent CLI。
- 打开 VS Code 的“输出”面板，选择 `CodeAgent` 通道查看错误信息。

如果 Webview 能打开但运行中报错：

- 优先查看 CodeAgent 面板中的节点历史。
- 查看本次运行目录中的 `run_health.md`、`workflow_events.jsonl`、`decision_trace.jsonl` 和 `workflow.log`。
- 必要时使用项目中的运行可观测性工具分析本次运行目录。
