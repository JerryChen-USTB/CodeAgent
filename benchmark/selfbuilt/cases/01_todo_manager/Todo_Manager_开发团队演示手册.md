# Todo Manager 开发团队演示手册

本文是一份面向开发团队的 Todo Manager 专项演示手册。它的目标不是把所有命令一次性列出来，而是一步一步带着读者完成一次完整演示：先了解案例，再创建独立演示空间，随后先体验不写配置文件的简单非交互式运行，再体验半交互式 wizard 运行，最后按需运行单 case benchmark，并实际启动 Agent 生成出来的 Todo Manager 软件。

请特别注意：本手册要求在新的演示空间根目录下启动 CodeAgent，不在 `D:\Projects\CodeAgent` 仓库根目录里直接运行演示。新的演示空间仍然放在当前仓库的 `codeagent_runs/demos/todo_manager/<时间戳>/` 下，方便统一管理；该目录属于运行产物，会被 Git 忽略。每次演示都会使用时间戳创建新目录，因此不需要删除上一次演示空间。

## 1. 本案例整体介绍

### 1.1 Todo Manager 是什么任务

`01_todo_manager` 是自建 benchmark 的第一个案例。它要求 CodeAgent 从空 `workspace/` 开始，生成一个可运行的 Python 待办事项管理软件。

本案例当前已经升级为 TUI 交互体验，Agent 可见输入只保留四份简体中文材料：

| 材料 | 路径 | 用途 |
|---|---|---|
| PRD | `input/PRD.md` | 最核心的产品需求，描述用户场景、TUI 交互、数据格式、错误处理和边界要求 |
| 用户故事 | `input/user_stories.md` | 用自然语言说明用户如何连续使用软件 |
| 设计模型 | `input/design_model.md` | 给出推荐分层、领域模型、状态机和流程设计 |
| 验收标准 | `input/acceptance_criteria.md` | 说明如何判断最终软件是否符合要求 |

最终软件应当能通过下面的默认入口启动：

```powershell
python -m todo_manager --file tasks.json
```

启动后应进入一个简单的文本 TUI 菜单。用户在同一个运行会话中完成添加任务、查看任务、标记完成、删除任务和退出。演示时不要把它做成“一次一个 shell 命令”的体验，因为这个案例升级后的重点就是让成品像一个真实软件一样连续交互。

### 1.2 CodeAgent 在这个案例中要做什么

CodeAgent 的工作不是一次问答式生成代码，而是按软件工程流程执行：

1. 读取公开输入材料和空工作区。
2. 调用 LLM 生成实现计划，计划中不包含完整代码。
3. 计划通过后，再调用 LLM 生成实现补丁草案。
4. 本地服务把补丁草案转换为 diff，审查并应用到工作区。
5. 进入测试阶段，先生成测试计划，再生成可见测试文件。
6. 运行 Agent 自测。
7. 如果测试失败，进入调试和修复阶段。
8. 在 benchmark 模式下，最后再由隐藏 oracle 测试最终成品。

读者需要理解两个层次：

| 层次 | 回答的问题 | 主要看哪里 |
|---|---|---|
| Agent 工作流 | Agent 做了什么，为什么这么做，是否运行了自测 | `implementation/`、`testing/`、`workflow.log`、`final_report.md` |
| benchmark 评测 | 生成软件是否通过隐藏验收，原始 case 是否没有被污染 | `benchmark_report.md`、`benchmark_result.json`、`oracle_logs/` |

## 2. 演示前准备

### 2.1 打开一个新的 PowerShell

建议使用普通 PowerShell 或 Windows Terminal。后续命令只用于准备空间、启动 Agent 和运行生成的软件。阅读报告时，请尽量使用编辑器打开文件，不用命令行读文件。进入人工审批界面后，CLI 会列出可审查的文件名；在支持终端链接的环境里，可以按住 `Ctrl` 并单击文件名直接打开。

### 2.2 设置仓库位置

在 PowerShell 中输入：

```powershell
$RepoRoot = "D:\Projects\CodeAgent"
```

这条命令只是在当前终端里保存 CodeAgent 仓库的位置。后面会通过这个变量复制材料和引用 benchmark case，但我们不会进入仓库根目录运行演示。

如果你的仓库不在 `D:\Projects\CodeAgent`，请把上面的路径改成实际路径。

### 2.3 在新空间里安装或刷新 CodeAgent

仍然在当前 PowerShell 中输入：

```powershell
python -m pip install -e "$RepoRoot"
```

这条命令把 CodeAgent 以 editable 模式安装到当前 Python 环境。这样后面无论当前目录在哪里，都可以运行：

```powershell
python -m codeagent --help
```

如果你看到 `wizard`、`run`、`benchmark` 等命令，说明 CLI 入口可用。这个检查只是确认工具能启动。

### 2.4 检查 LLM API Key 是否可用

输入：

```powershell
python -c "import os; print('OPENROUTER_API_KEY configured:', bool(os.environ.get('OPENROUTER_API_KEY')))"
```

这条命令只检查环境变量是否存在，不会打印真实 API Key。

期望看到：

```text
OPENROUTER_API_KEY configured: True
```

如果显示 `True`，说明当前终端已经能读取 API Key，可以继续下一步。

如果没有显示 `True`，请先配置 `OPENROUTER_API_KEY`。Windows PowerShell 中可以临时为当前终端会话配置：

```powershell
$env:OPENROUTER_API_KEY = "你的 OpenRouter API Key"
```

如果希望以后新打开的终端也能使用，可以设置用户级环境变量：

```powershell
setx OPENROUTER_API_KEY "你的 OpenRouter API Key"
```

执行 `setx` 后，请重新打开 PowerShell，再重新运行本小节的检查命令。不要把真实 key 写入手册、报告、截图或 Git 仓库。

## 3. 创建本次独立演示空间

### 3.1 创建带时间戳的新目录

输入：

```powershell
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$DemoRoot = Join-Path $RepoRoot "codeagent_runs\demos\todo_manager\$Stamp"
New-Item -ItemType Directory -Force -Path $DemoRoot | Out-Null
Set-Location $DemoRoot
$DemoRoot
```

这几行命令做了三件事：

1. 生成一个时间戳，例如 `20260605_153000`。
2. 在仓库内创建一个新目录，例如 `D:\Projects\CodeAgent\codeagent_runs\demos\todo_manager\20260605_153000`。
3. 把当前终端切换到这个新目录。

从这一刻开始，所有 CodeAgent 演示命令都在 `$DemoRoot` 下运行。这样每次演示都有自己的独立空间，不需要删除上一次的内容；同时所有演示空间都集中在仓库的 `codeagent_runs/demos/` 下，便于查找和清理。

### 3.2 准备运行需要的目录

输入：

```powershell
New-Item -ItemType Directory -Force -Path `
  ".\direct\input", `
  ".\direct\workspace", `
  ".\direct\runs", `
  ".\interactive\input", `
  ".\interactive\workspace", `
  ".\interactive\runs" | Out-Null

$InputFiles = @("PRD.md", "user_stories.md", "design_model.md", "acceptance_criteria.md")
foreach ($Name in $InputFiles) {
  Copy-Item "$RepoRoot\benchmark\selfbuilt\cases\01_todo_manager\input\$Name" ".\direct\input\$Name"
  Copy-Item "$RepoRoot\benchmark\selfbuilt\cases\01_todo_manager\input\$Name" ".\interactive\input\$Name"
}
```

这一步是在新空间里准备两套公开输入材料和两个空工作区：

| 目录 | 用途 |
|---|---|
| `direct\workspace` | 第 4 章直接 `codeagent run` 使用，不写配置文件 |
| `direct\runs` | 直接非交互式运行的 Agent 产物 |
| `interactive\workspace` | 第 5 章 wizard 使用 |
| `interactive\runs` | 半交互式运行的 Agent 产物 |

现在打开当前演示空间：

```powershell
explorer .
```

这条命令只是打开文件资源管理器。请在资源管理器中进入 `direct\input` 或 `interactive\input`，用 VS Code、记事本或其他编辑器打开四份材料。两边材料内容相同，只是服务于不同运行方式。建议先读 `PRD.md`，再读 `acceptance_criteria.md`。

阅读重点：

| 文件 | 重点看什么 |
|---|---|
| `PRD.md` | 默认启动必须进入 TUI；任务应保存到 JSON；错误提示和退出行为要稳定 |
| `user_stories.md` | 用户希望在同一个会话中连续添加、查看、完成和删除任务 |
| `design_model.md` | 推荐的 `models/storage/service/tui` 分层，以及状态流转 |
| `acceptance_criteria.md` | oracle 会通过 stdin 驱动 TUI，并检查 JSON、错误恢复和持久化 |

## 4. 非交互式运行：不写配置文件直接启动 Agent

这一部分演示最简单的非交互式路径：不创建 YAML，不跑 benchmark，也不进入 wizard 表单。读者只需要在本次新空间根目录下敲一条 `codeagent run` 命令，CodeAgent 就会读取四份输入材料，在 `direct\workspace` 中生成 Todo Manager。

请注意，这条快速命令的重点是“操作简单”。模型可以直接通过 `--model` 指定；其它 OpenRouter 默认配置仍来自 CodeAgent 的默认配置，例如 `OPENROUTER_API_KEY` 环境变量和 OpenRouter base URL。

### 4.1 确认当前目录是新空间根目录

输入：

```powershell
Get-Location
```

确认输出是类似：

```text
D:\Projects\CodeAgent\codeagent_runs\demos\todo_manager\20260606_103625
```

如果不是，请输入：

```powershell
Set-Location $DemoRoot
```

### 4.2 直接运行 CodeAgent

输入：

```powershell
python -m codeagent run `
  --project .\direct\workspace `
  --requirements .\direct\input\PRD.md `
  --requirements .\direct\input\user_stories.md `
  --requirements .\direct\input\design_model.md `
  --requirements .\direct\input\acceptance_criteria.md `
  --stages implement,test,debug,repair `
  --output-dir .\direct\runs `
  --test-cmd "python -m pytest -q" `
  --model google/gemini-3.5-flash `
  --auto-approve
```

这条命令的含义是：

| 参数 | 作用 |
|---|---|
| `--project .\direct\workspace` | 指定 Agent 要写代码的空工作区 |
| `--requirements ...` | 逐个传入 PRD、用户故事、设计模型和验收标准 |
| `--stages implement,test,debug,repair` | 运行实现、测试、调试、修复完整流程 |
| `--output-dir .\direct\runs` | 把本次 Agent 运行产物写入独立目录 |
| `--test-cmd "python -m pytest -q"` | 让 Agent 生成并运行公开 pytest 自测 |
| `--model google/gemini-3.5-flash` | 指定本次直接 run 使用的 LLM 模型 |
| `--auto-approve` | 自动通过计划、补丁和命令审批，让这次运行真正无人值守 |

运行过程中，终端会持续输出阶段进度。你可以这样向读者解释：

| 看到的内容 | 含义 |
|---|---|
| 实现阶段正在生成计划 | LLM 正在根据四份输入材料规划要写哪些模块 |
| 正在生成单文件补丁 | Agent 按文件顺序生成代码，每个文件应用后会成为后续上下文 |
| 测试阶段正在生成测试计划 | Agent 不只是写代码，还要设计公开自测 |
| 运行测试命令 | Agent 在 `direct\workspace` 中执行公开测试 |
| 调试或修复阶段 | 如果测试失败，Agent 会分析失败原因并尝试修复 |

这一条命令不会执行隐藏 oracle，也不会产生 benchmark 分数。它适合课堂或组内演示时快速展示“从输入材料到可运行软件”的主流程。因为启用了 `--auto-approve`，终端不会停下来等待你逐项审批；如果想展示人工审查，请使用第 5 章 wizard。

### 4.3 打开本次直接运行的产物

运行结束后，输入：

```powershell
$LatestDirectRun = Get-ChildItem .\direct\runs -Directory |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
explorer $LatestDirectRun.FullName
```

这条命令只是帮你打开最新一次直接运行的 Agent 产物目录。后续阅读都在文件资源管理器和编辑器里完成。

建议按下面顺序打开文件：

| 文件或目录 | 用途 |
|---|---|
| `final_report.md` | 本次 Agent 运行的最终摘要 |
| `task_config.yaml` | 本次快速命令被规范化后的任务配置 |
| `metadata.json` | 本次 run 元数据，包含最终使用的模型配置 |
| `workflow.log` | 从路由、LLM 调用、审批、补丁到测试的可读时间线 |
| `implementation\implementation_plan.md` | 实现计划，先于代码补丁生成 |
| `implementation\implementation.patch.diff` | 实际应用到工作区的实现 diff |
| `implementation\stage_patch_context.md` | 实现阶段开始时读取的一次性上下文快照 |
| `testing\test_plan.md` | 测试计划 |
| `testing\test_result.json` | Agent 自测结果 |

讲解时重点提醒读者：虽然我们没有手写配置文件，但 CodeAgent 仍会把最终规范化配置保存到 `task_config.yaml`，所以这次运行仍然可复盘、可审计。

### 4.4 体验直接运行生成的软件

回到 PowerShell，输入：

```powershell
Set-Location .\direct\workspace
python -m todo_manager --file tasks.json
```

这一步是在运行直接非交互式流程生成出来的软件。注意，我们只用一条命令启动软件，后续在软件自己的 TUI 里操作。

进入 TUI 后，按下面步骤操作：

1. 选择 `1` 添加任务。
2. 标题输入 `Prepare Todo demo`。
3. 截止日期输入 `2026-06-10`。
4. 优先级输入 `high`。
5. 回到主菜单后选择 `2` 查看任务。
6. 过滤条件输入 `all` 或直接回车。
7. 选择 `3` 标记完成。
8. 任务 ID 输入 `1`。
9. 再选择 `2` 查看任务。
10. 过滤条件输入 `done`。
11. 选择 `5` 退出。

你要向读者强调：

> 这里证明的是最终成品能像普通软件一样连续使用。我们不是每一步都重新敲一条 shell 命令，而是启动一次程序后，在同一个 TUI 会话里连续操作。

操作结束后输入：

```powershell
Set-Location $DemoRoot
```

回到演示空间根目录，准备进入半交互式演示。

## 5. 半交互式运行：使用 wizard 创建 Todo 任务

这一部分是重点。它展示普通用户不写 YAML，也能通过中文表单创建任务并直接启动 Agent。

### 5.1 确认当前目录仍是新演示空间

输入：

```powershell
Get-Location
```

确认输出是类似：

```text
D:\Projects\CodeAgent\codeagent_runs\demos\todo_manager\20260605_153000
```

如果不是，请输入：

```powershell
Set-Location $DemoRoot
```

### 5.2 启动中文 wizard

输入：

```powershell
python -m codeagent wizard
```

这条命令会打开中文任务表单，并在确认后直接启动 Agent。它不是只生成配置文件，确认后会真的开始运行。

### 5.3 按表单一步一步填写

下面是推荐填写方式。

| 表单项 | 推荐填写 | 说明 |
|---|---|---|
| 执行阶段 | 完整流水线：实现 + 测试 + 调试 + 修复 | 展示完整软件工程闭环 |
| 项目目录 | interactive\workspace | 这是本次半交互式运行的空工作区 |
| 输入材料 | 添加四份 `interactive\input\*.md` | PRD、用户故事、设计模型、验收标准 |
| 输出目录 | interactive\runs | Agent 运行产物写到这里 |
| 测试命令 | python -m pytest -q | testing 阶段会生成公开 pytest 测试 |
| 模型 | 选择本次演示模型 | 默认是 `google/gemini-3.5-flash`；也可以选择其他 OpenRouter 候选 |
| 审批模式 | 开启人工审批 | 便于现场解释计划、补丁和命令 |

输入材料建议按这个顺序添加：

```text
interactive\input\PRD.md
interactive\input\user_stories.md
interactive\input\design_model.md
interactive\input\acceptance_criteria.md
```

如果 wizard 自动候选列表没有出现这些文件，就选择手动输入路径。

### 5.4 最终确认前怎么看

wizard 会显示任务摘要。请确认这些信息：

| 摘要项 | 应该是什么 |
|---|---|
| 项目目录 | `$DemoRoot\interactive\workspace` |
| 输出目录 | `$DemoRoot\interactive\runs` |
| 执行阶段 | `implement, test, debug, repair` |
| 输入材料 | 四份 Markdown 材料 |
| 测试命令 | `python -m pytest -q` |
| 模型 | 本次想演示的模型，例如 `google/gemini-3.5-flash` |
| 审批模式 | `manual` |

如果看到项目目录指向了 `D:\Projects\CodeAgent`，说明填错了。请取消，重新运行 wizard，并把项目目录填成 `interactive\workspace`。

确认后，Agent 会开始运行。运行目录中的 `task_config.yaml` 和 `metadata.json` 会保存最终模型配置；如果现场有人问“到底调用了哪个模型”，优先打开这两个文件确认，而不是只看终端记忆。

## 6. 半交互式运行时如何审查 Agent

人工审批模式下，CodeAgent 会在关键点停下来，让你看计划、补丁或命令。不要急着一路批准，演示的价值就在这里。

### 6.1 审查实现计划

当终端提示审查实现计划时，先不要马上批准。

审批提示会列出已经生成并落盘的计划文件，通常显示为类似下面的形式：

```text
implementation_plan.md (implementation/implementation_plan.md)
```

在支持终端链接的环境中，按住 `Ctrl` 并单击这个文件名，就可以直接用编辑器打开实现计划。这样不需要再手动打开文件资源管理器查找最新 run 目录。

如果当前终端不支持 `Ctrl+单击` 打开文件，再手动进入 `interactive\runs\<最新 run>\implementation\implementation_plan.md` 查看。

阅读时看这几件事：

| 检查点 | 为什么重要 |
|---|---|
| 是否提到 TUI | 本案例不能只做一条条命令 |
| 是否提到 JSON 持久化 | 任务退出后必须保存 |
| 是否有 `todo_manager` 包入口 | `python -m todo_manager` 要可运行 |
| 是否有错误处理 | 空标题、非法日期、非法优先级、坏 JSON 都要处理 |
| 是否没有测试文件 | 实现阶段不应该生成测试 |

如果计划满意，回到终端选择“实施此计划”。如果不满意，选择“告知 CodeAgent 如何调整”，输入清楚的中文反馈，例如：

```text
请明确默认启动必须进入文本 TUI 菜单，不要只实现 add/list/done/delete 这类一次一个命令的 CLI。
```

Agent 会带着反馈重新生成计划。

### 6.2 审查实现补丁

计划通过后，Agent 会生成补丁草案。此时打开同一个最新 run 目录中的：

```text
implementation\implementation_patch_draft.json
implementation\implementation.patch.diff
```

建议先看 diff，而不是先看 JSON。

重点看：

| 检查点 | 怎么判断 |
|---|---|
| 是否创建了 `todo_manager` 包 | 应看到 `todo_manager/__main__.py` 或等价入口 |
| 是否包含 TUI 循环 | 应有菜单、读取用户输入、回到菜单 |
| 是否使用 JSON 文件 | 应有读取和保存任务数据的逻辑 |
| 是否没有访问隐藏路径 | 不应出现 `oracle_tests`、`evaluation` |
| 变更范围是否合理 | 不应修改仓库源码，只应改演示工作区 |

现在实现补丁是按单文件生成和审批的。第一次补丁审批通常会列出单个 patch 草案和单个 diff，例如：

```text
001_01_todo_manager_models.py.json (implementation/file_patches/001_01_todo_manager_models.py.json)
001_01_todo_manager_models.py.patch.diff (implementation/file_patches/001_01_todo_manager_models.py.patch.diff)
```

补丁审批保留三个选项：

```text
是，应用此补丁
是，应用此补丁，本阶段不再提示
否，告知 CodeAgent 如何调整
```

如果只是演示流程，推荐第一个补丁仔细审查后选择“是，应用此补丁，本阶段不再提示”。后续同阶段补丁会自动通过，终端会输出自动通过信息和目标文件名；这些自动通过记录也会写入 `decision_trace.jsonl` 和 `workflow_events.jsonl`。这一步批准后，文件才会真正写入 `interactive\workspace`。

### 6.3 审查测试计划

实现完成后，Agent 进入 testing 阶段。它会先生成测试计划。打开：

```text
testing\test_plan.md
```

重点看：

| 检查点 | 为什么重要 |
|---|---|
| 是否测试 TUI 会话 | 应通过 stdin 或等价方式驱动连续交互 |
| 是否测试持久化 | 退出后重新打开应看到任务 |
| 是否测试错误恢复 | 输错内容后程序不能崩溃 |
| 是否测试空列表 | 没有任务时应输出稳定提示 |
| 是否不是 0 测试 | 0 个测试不能算成功 |
| 测试文件数量是否合理 | 当前策略首选 1 个测试文件，复杂场景最多 2 个，不应拆成很多零散文件 |

满意后批准。若不满意，可以反馈：

```text
请增加通过 stdin 驱动 TUI 的端到端测试，并覆盖添加、查看、完成、删除和重新打开后的持久化。
```

### 6.4 审查测试补丁和测试命令

测试计划通过后，Agent 会生成测试文件。打开：

```text
testing\test.patch.diff
testing\test_patch_draft.json
testing\file_patches
```

重点看：

- 测试文件是否在 `tests/` 或 `test_*.py` 路径下。
- 测试文件是否控制在 1 到 2 个；如果是 2 个，应能看出单元测试和 TUI 端到端测试的拆分理由。
- 测试是否运行真实软件，而不是只测内部函数。
- subprocess 测试的 cwd 是否指向真实项目根目录。
- 测试是否没有引用隐藏 `oracle_tests`。

之后 Agent 会请求批准测试命令。推荐命令通常是：

```text
python -m pytest -q
```

这条命令的含义是运行 Agent 刚生成的公开自测，不是隐藏 oracle。
审批界面应当直接显示命令和工作目录；如果只看到“运行此测试命令？”却看不到命令本身，就说明 CLI 展示层需要排查。

### 6.5 如果进入调试或修复阶段怎么看

如果测试失败，CodeAgent 会进入 debugging/repair。此时不要慌，它正是在展示连续开发流程。

优先打开这些文件：

| 文件 | 看什么 |
|---|---|
| `debugging\failure_summary.md` | 失败现象摘要 |
| `debugging\debug_report.md` | 失败定位、可疑文件、根因和修复建议 |
| `debugging\llm_debug_analysis.json` | LLM 调试节点输出的结构化归因、证据和候选文件 |
| `repair\repair_plan.md` | 修复策略 |
| `repair\repair.patch.diff` | 具体修复内容 |
| `repair\repair_test_result.json` | 修复后的回归结果 |

讲解时可以这样说：

> 测试失败不代表演示失败。这个系统本来就是为了覆盖实现、测试、调试、修复的连续流程。关键是看它是否能根据失败证据定位问题、生成修复计划、应用补丁并重新验证。

## 7. 半交互式运行结束后看懂结果

运行结束后，终端会打印运行目录。你也可以在资源管理器中打开：

```text
interactive\runs
```

进入最新 run 目录，按下面顺序阅读。

### 7.1 task_config.yaml 和 metadata.json

先打开：

```text
task_config.yaml
metadata.json
```

重点看：

- `model.model_name` 或 `metadata.model.model_name` 是否是本次选择的模型。
- `model.base_url` 是否是 `https://openrouter.ai/api/v1`。
- `model.api_key_env` 是否是 `OPENROUTER_API_KEY`，注意这里记录的是环境变量名，不是密钥值。
- `project_path` 是否指向 `$DemoRoot\interactive\workspace`。
- `test_command.command` 是否是 `python -m pytest -q`。
- `permissions.approval_mode` 是否是 `manual`。

讲解时可以这样说：

> wizard 表单不是临时输入。确认后，它会被固化成标准任务配置，模型选择、项目路径、输入材料、测试命令和审批模式都可以复查。

### 7.2 final_report.md

打开：

```text
final_report.md
```

重点看：

- 最终状态是否为成功。
- implementation 阶段是否完成。
- testing 阶段是否运行了非零数量测试。
- 如果有 debug/repair，最终是否修复成功。

### 7.3 workflow.log

打开：

```text
workflow.log
```

它是最适合讲解 Agent 在做什么的文件。建议沿着时间线找这些关键词：

| 关键词 | 含义 |
|---|---|
| plan_generation | LLM 正在生成纯计划 |
| patch_generation | 计划通过后，LLM 正在生成补丁草案 |
| approval | 用户或配置做出的审批决策 |
| patch | 补丁生成、校验、应用 |
| stage_patch_context | 阶段开始时读取并复用的上下文快照 |
| testing | 测试计划、测试补丁、测试命令和测试结果 |
| debugging_analysis | 调试阶段 LLM 对失败的归因和修复建议 |
| final | 工作流最终状态 |

如果需要机器可读的事件流，打开同目录下的：

```text
workflow_events.jsonl
```

它的每一行都是一条 JSON 事件，适合后续脚本分析。遇到路径很长的 `llm_calls` 深层文件时，Windows 终端可能打不开某些文件；这属于可观测性制品路径长度问题，不代表 Agent 工作流一定失败。

### 7.4 decision_trace.jsonl

打开：

```text
decision_trace.jsonl
```

这个文件说明每个审批点是怎么通过的。

重点看：

- `decision_type=approve` 表示批准。
- `decision_type=respond` 表示用户提出反馈并要求重新生成。
- `presented_to_user=true` 表示确实展示给用户审查过。
- `decision_source=user` 表示来自人工决策。
- `decision_source=stage_patch_auto_approve` 表示用户选择了“本阶段不再提示”后，系统自动通过了后续单文件补丁。

### 7.5 stage_patch_context.md 和 applied_file_context.md

当前单文件补丁流程会在每个增量阶段写入上下文产物，例如：

```text
implementation\stage_patch_context.md
implementation\applied_file_context.md
testing\stage_patch_context.md
testing\applied_file_context.md
repair\stage_patch_context.md
repair\applied_file_context.md
```

它们分别回答两个问题：

| 文件 | 回答的问题 |
|---|---|
| `stage_patch_context.md` | 阶段开始时，工作流一次性给 LLM 准备了哪些公开材料、源码、失败日志和项目结构 |
| `applied_file_context.md` | 前面已经通过并落盘的单文件补丁内容，如何作为后续补丁的上下文 |

这部分很适合解释最近的工作流优化：不是每写一个文件都重新让 LLM 决定读哪些文件，而是在阶段开始形成上下文快照，后续补丁复用同一份阶段上下文，并追加已经应用的文件内容。

### 7.6 workspace

打开：

```text
interactive\workspace
```

这里是半交互式 wizard 运行生成的软件。你应该能看到 `todo_manager` 包和测试文件。

讲解重点：

> wizard 不是只创建了一个任务配置，它最后也落到了真实软件文件上。`interactive\workspace` 就是用户通过中文表单生成出来的软件项目。

## 8. 运行半交互式生成出来的软件

这一部分是最终体验：像普通用户一样使用成品。

### 8.1 进入生成软件的工作区

输入：

```powershell
Set-Location "$DemoRoot\interactive\workspace"
```

这条命令只是进入半交互式 wizard 生成的软件目录。

### 8.2 启动 TUI

输入：

```powershell
python -m todo_manager --file tasks.json
```

此时应进入 Todo Manager 的文本菜单。后面不要再输入 `add`、`list` 这类 shell 子命令，而是在 TUI 里操作。

### 8.3 完成一次真实用户操作

按下面步骤操作：

1. 在主菜单选择添加任务。
2. 标题输入 `Review CodeAgent demo manual`。
3. 截止日期输入 `2026-06-10`。
4. 优先级输入 `high`。
5. 回到主菜单后选择查看任务。
6. 过滤条件输入 `open`。
7. 回到主菜单后选择标记完成。
8. 任务 ID 输入 `1`。
9. 再选择查看任务。
10. 过滤条件输入 `done`。
11. 选择退出。

如果想证明持久化，再次启动：

```powershell
python -m todo_manager --file tasks.json
```

选择查看 `done` 任务，应该还能看到刚才完成的任务。这说明 JSON 持久化生效。

### 8.4 打开 tasks.json

在资源管理器中打开：

```text
interactive\workspace\tasks.json
```

重点看：

- 是否是 JSON 数组。
- 是否包含任务 `title`。
- 是否包含 `status`。
- 是否保留 `due_date` 和 `priority`。

这一步帮助读者理解：TUI 看到的任务不是临时输出，而是被持久化到了本地数据文件。

### 8.5 回到演示空间根目录

输入：

```powershell
Set-Location $DemoRoot
```

现在一次完整演示已经结束。

## 9. 三种运行方式的区别

| 对比项 | 直接非交互式 run | 半交互式 wizard | 单 case benchmark |
|---|---|---|---|
| 面向对象 | 想快速跑通流程的开发者 | 普通用户、现场演示、人工审查 | 评测者、课程验收、CI 式评测 |
| 启动方式 | `python -m codeagent run --project ... --requirements ... --model ... --auto-approve` | `python -m codeagent wizard` | `python -m codeagent benchmark --config ...` |
| 用户是否填写表单 | 否 | 是 | 否 |
| 是否需要手写配置文件 | 否 | 否 | 需要一个 benchmark 聚合配置和 case 配置 |
| 模型选择位置 | 通过 `--model` 参数指定，最终保存到 run 的 `task_config.yaml` | 在 wizard 表单中选择，最终保存到 run 的 `task_config.yaml` | 写在本次演示 case 副本的 `case.yaml`，最终保存到 run 的 `task_config.yaml` |
| 是否自动审批 | 是，命令中显式传入 `--auto-approve` | 可选择人工审批，推荐演示时开启 | benchmark 中默认自动审批 |
| 是否执行隐藏 oracle | 否 | 否 | 是 |
| 输出位置 | `codeagent_runs/demos/todo_manager/<时间戳>/direct/runs/` | `codeagent_runs/demos/todo_manager/<时间戳>/interactive/runs/` | `codeagent_runs/benchmarks/selfbuilt/` |
| 生成软件位置 | `codeagent_runs/demos/todo_manager/<时间戳>/direct/workspace` | `codeagent_runs/demos/todo_manager/<时间戳>/interactive/workspace` | `codeagent_runs/benchmarks/selfbuilt/.../case_workspaces/01_todo_manager/workspace` |
| 最适合证明 | 一条命令从材料生成软件 | 用户能用中文表单创建任务并看懂过程 | Agent 能通过标准化隐藏评测 |

推荐讲解方式：

> 直接 run 最适合快速演示主流程，wizard 最适合展示可审查的人机协作体验，benchmark 最适合最后证明标准化评测结果。三种路径都从同一组 Todo Manager 材料出发，但服务于不同场景。

## 10. 演示时常见问题

### 10.1 为什么不在 CodeAgent 仓库根目录运行

因为演示应该像真实用户使用产品一样，从一个独立任务空间启动。这样有三个好处：

- 不会把演示产物混进仓库根目录。
- 每次演示都有时间戳，可以保留历史结果。
- 更容易说明 Agent 修改的是任务 workspace，不是 CodeAgent 自己的源码。

### 10.2 为什么要复制四份输入材料到 direct/input 和 interactive/input

直接 run 和 wizard 都应该像真实用户任务一样从本次新空间读取输入材料。把材料复制到新空间中，能让读者直观看到“这就是本次任务输入”，也避免误以为 Agent 读取了仓库中的隐藏文件。

### 10.3 如果直接非交互式 run 失败怎么办

先打开最新直接运行目录：

```text
direct\runs\<最新 run>
```

推荐阅读顺序：

1. `final_report.md`
2. `workflow.log`
3. `testing\test_result.json`
4. `debugging\debug_report.md`
5. `repair\repair_report.md`

不要先改代码。先判断失败发生在实现、测试、调试还是修复阶段。如果需要确认是否满足隐藏验收，再到最后运行单 case benchmark。

### 10.4 为什么 todo_benchmark.yaml 顶层不写模型选择

直接 run 使用 `--model` 指定模型；wizard 在表单中选择模型；benchmark 则应在本次演示 case 副本的 `case.yaml` 中写模型。

不要把 `model_name` 写在 `todo_benchmark.yaml` 顶层来表示模型选择。`todo_benchmark.yaml` 是 benchmark 聚合配置，只说明“跑哪些 case、输出到哪里”；真正传给 Agent 的运行配置是每个 case 的 `TaskConfig`。

### 10.5 如果 wizard 自动发现不到输入材料怎么办

选择手动输入路径，依次填入：

```text
interactive\input\PRD.md
interactive\input\user_stories.md
interactive\input\design_model.md
interactive\input\acceptance_criteria.md
```

如果仍然失败，确认当前目录是 `$DemoRoot`，并确认这些文件确实存在。

### 10.6 如果生成的软件不能用 `python -m todo_manager` 启动怎么办

先打开本次 run 的：

```text
implementation\changed_files.json
implementation\implementation_plan.md
implementation\implementation.patch.diff
```

检查 Agent 是否创建了 `todo_manager/__main__.py` 或等价入口。若半交互式运行还在审批阶段，可以在实现计划或补丁审批时反馈：

```text
请确保默认入口 python -m todo_manager --file tasks.json 可以启动文本 TUI。
```

### 10.7 如果终端中文显示乱码怎么办

优先用编辑器打开文件阅读，少用终端打印 Markdown。必要时可以在 PowerShell 中输入：

```powershell
chcp 65001
```

这条命令把当前控制台切到 UTF-8 代码页，但不同终端字体和配置仍可能影响显示。报告文件本身按 UTF-8 保存，通常在 VS Code 中打开是正常的。

## 11. 运行 Todo 单 case benchmark

前面的直接 run 和 wizard 已经能说明“怎么使用 CodeAgent”；benchmark 用来补充说明“怎么标准化评测 CodeAgent”。

如果只想演示本章的单 case benchmark，不需要先完成第 4 到第 10 章，但需要先完成这些准备：

1. 完成第 2.2 节，设置 `$RepoRoot`。
2. 完成第 2.3 节，确认 `python -m codeagent --help` 可用。
3. 完成第 2.4 节，确认 `OPENROUTER_API_KEY configured: True`。
4. 完成第 3.1 节，创建本次演示专用的 `$DemoRoot` 并进入该目录。

第 3.1 节中的 `codeagent_runs\demos\todo_manager\$Stamp` 是“本次演示临时空间”，用来保存本章创建的 benchmark 配置和 case 副本；它不是 benchmark 最终评分输出目录。benchmark 运行结果仍会写到仓库统一的 `codeagent_runs\benchmarks\selfbuilt` 下。

### 11.1 准备本次演示专用 case 副本

输入：

```powershell
Set-Location $DemoRoot

$DemoCaseRoot = Join-Path $DemoRoot "benchmark_case"
New-Item -ItemType Directory -Force -Path $DemoCaseRoot | Out-Null
Copy-Item -LiteralPath "$RepoRoot\benchmark\selfbuilt\cases\01_todo_manager" `
  -Destination $DemoCaseRoot `
  -Recurse `
  -Force

$DemoCase = Join-Path $DemoCaseRoot "01_todo_manager"
$CaseConfig = Join-Path $DemoCase "case.yaml"
$CaseConfigText = Get-Content -LiteralPath $CaseConfig -Raw
$ModelBlock = @"
model:
  provider: openai_compatible
  model_name: google/gemini-3.5-flash
  base_url: https://openrouter.ai/api/v1
  api_key_env: OPENROUTER_API_KEY
  temperature: 0.2
  max_tokens: 16384
"@
$CaseConfigText = $CaseConfigText -replace "(?m)^entrypoint:", ($ModelBlock + "`r`nentrypoint:")
Set-Content -LiteralPath $CaseConfig -Value $CaseConfigText -Encoding UTF8
```

这一步把原始 Todo case 复制到本次演示空间，只修改副本，不改仓库中的原始 benchmark case。模型选择写在副本 `case.yaml` 中；如果要换模型，只改 `model_name` 这一行。

### 11.2 创建 Todo 专用 benchmark 配置

输入：

```powershell
$DemoCaseConfigPosix = $CaseConfig -replace "\\", "/"
@"
schema_version: 1
name: todo_manager_demo_benchmark
benchmark_id: todo_manager_demo_benchmark
description: Todo Manager single-case demo benchmark.
output_dir: ../../../benchmarks/selfbuilt
default_agent_visible_paths:
  - input
  - workspace
default_hidden_paths:
  - oracle_tests
cases:
  - case_id: 01_todo_manager
    config: "$DemoCaseConfigPosix"
    enabled: true
    difficulty: introductory
    project_type: tui
"@ | Set-Content -Path .\todo_benchmark.yaml -Encoding UTF8
```

打开 `todo_benchmark.yaml` 看一眼，确认：

- `output_dir` 指向仓库统一的 `codeagent_runs\benchmarks\selfbuilt`。
- `config` 指向本次演示空间里的 `benchmark_case\01_todo_manager\case.yaml`。
- `todo_benchmark.yaml` 本身只说明跑哪个 case；模型不写在它的顶层。

### 11.3 启动单 case benchmark

输入：

```powershell
python -m codeagent benchmark --config .\todo_benchmark.yaml
```

这条命令会启动标准 benchmark 流程：

1. runner 读取 `todo_benchmark.yaml`。
2. runner 把本次演示 case 副本复制到 `codeagent_runs\benchmarks\selfbuilt\...\case_workspaces\01_todo_manager`。
3. Agent 只能看到公开 `input/` 和空 `workspace/`。
4. Agent 生成实现、生成自测并运行自测。
5. runner 最后执行隐藏 oracle，判断生成软件是否真的符合验收标准。

### 11.4 打开 benchmark 输出

运行结束后输入：

```powershell
$BenchmarkOutputRoot = Join-Path $RepoRoot "codeagent_runs\benchmarks\selfbuilt"
$LatestBenchmark = Get-ChildItem $BenchmarkOutputRoot -Directory |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
explorer $LatestBenchmark.FullName
```

重点打开这些文件和目录：

| 文件或目录 | 看什么 |
|---|---|
| `benchmark_report.md` | 总体是否成功，`oracle_success` 是否为 `True` |
| `benchmark_result.json` | 机器可读的评测结果和失败原因 |
| `case_workspaces\01_todo_manager\workspace` | Agent 最终生成的软件 |
| `case_runs\01_todo_manager\<最新 run>` | Agent 自己的运行产物 |
| `oracle_logs` | 隐藏 oracle 的运行日志 |

如果 Agent 自测通过但 oracle 失败，先不要立刻断言 workflow 坏了。请把 `oracle_logs`、生成软件行为、PRD 和验收标准放在一起看：有时是生成软件没有覆盖连续交互或边界场景，有时也可能说明 oracle 需要补充更清晰的错误说明。
