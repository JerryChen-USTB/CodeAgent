# Library Lending 开发团队演示手册

本文是一份面向开发团队的 `04_library_lending` 专项演示手册。它会一步一步带读者完成完整演示：准备独立演示空间，使用简单命令行方式启动 Agent，使用 wizard 体验半交互式运行，审查 Agent 生成的计划和补丁，最后在浏览器中运行 Agent 生成出来的图书借阅管理系统，并用单 case benchmark 做标准化验证。

请特别注意：本手册要求在新的演示空间根目录下启动 CodeAgent，不在 `D:\Projects\CodeAgent` 仓库根目录里直接运行演示。新的演示空间放在当前仓库的 `codeagent_runs/demos/library_lending/<时间戳>/` 下，属于运行产物，会被 Git 忽略。每次演示都会创建带时间戳的新目录，不需要删除上一次演示空间。

## 1. 案例介绍

### 1.1 Library Lending 是什么任务

`04_library_lending` 是自建 benchmark 的第四个案例。它要求 CodeAgent 从空 `workspace/` 开始，生成一个可在浏览器中本地运行的 Python 图书借阅管理系统。

本案例已经从旧版 CLI 升级为 Web UI。Agent 可见输入只保留四份简体中文材料：

| 材料 | 路径 | 用途 |
|---|---|---|
| PRD | `input/PRD.md` | 描述本地 Web UI、SQLite 持久化、业务规则、错误处理和启动方式 |
| 用户故事 | `input/user_stories.md` | 描述管理员如何通过浏览器完成图书、读者、借还书和逾期操作 |
| 设计模型 | `input/design_model.md` | 描述标准库 Web 分层、SQLite 表、路由、流程和状态 |
| 验收标准 | `input/acceptance_criteria.md` | 描述浏览器页面、HTTP 表单、库存、逾期和 oracle 验收点 |

最终软件应当通过下面的默认入口启动：

```powershell
python -m library_lending --db library.db --host 127.0.0.1 --port 8000
```

启动后，用户打开浏览器访问：

```text
http://127.0.0.1:8000/
```

页面应包含 `图书借阅管理系统`，并能通过普通 HTML 表单完成添加图书、注册读者、借书、还书、查看库存和查询逾期。这个案例不要求炫酷前端，但必须是真正能在浏览器里操作的本地 Web 软件。

### 1.2 本案例考察 Agent 的能力

| 能力 | 在本案例中的体现 |
|---|---|
| Web 入口实现 | 使用标准库 `http.server` 启动本地服务 |
| 浏览器交互 | 用 HTML 页面和表单完成业务操作 |
| 数据持久化 | 使用 SQLite 保存图书、读者和借阅 |
| 业务规则 | 检查库存、重复借阅、固定 14 天借期和逾期 |
| 错误处理 | 页面显示 `invalid date`、`no available copies` 等稳定错误短语 |
| 可测试性 | 暴露 `create_server(...)`，方便 oracle 用动态端口测试 |

CodeAgent 会按实现、测试、调试、修复的流程工作。读者既要看最终软件，也要看 Agent 在每个阶段产生的计划、补丁、测试和报告。

## 2. 演示前准备

### 2.1 打开新的 PowerShell

建议使用普通 PowerShell 或 Windows Terminal。后续命令只用于准备空间、启动 Agent、启动生成的软件和运行 benchmark。阅读报告时，尽量使用编辑器打开文件。审批界面列出文件名时，在支持终端链接的环境中可以按住 `Ctrl` 并单击文件名打开。

### 2.2 设置仓库位置

输入：

```powershell
$RepoRoot = "D:\Projects\CodeAgent"
```

如果你的仓库不在这个路径，请改成实际路径。后续命令会通过 `$RepoRoot` 复制案例材料，但不会在仓库根目录运行演示。

### 2.3 安装或刷新 CodeAgent

输入：

```powershell
python -m pip install -e "$RepoRoot"
python -m codeagent --help
```

如果能看到 `run`、`wizard`、`benchmark` 等命令，说明 CLI 可用。

### 2.4 检查 API Key

输入：

```powershell
python -c "import os; print('OPENROUTER_API_KEY configured:', bool(os.environ.get('OPENROUTER_API_KEY')))"
```

期望看到：

```text
OPENROUTER_API_KEY configured: True
```

如果没有显示 `True`，请先在当前 PowerShell 临时配置：

```powershell
$env:OPENROUTER_API_KEY = "你的 OpenRouter API Key"
```

如果希望新终端也可用，可以设置用户级环境变量：

```powershell
setx OPENROUTER_API_KEY "你的 OpenRouter API Key"
```

执行 `setx` 后请重新打开 PowerShell，再重新检查。不要把真实 key 写入文档、报告、截图或 Git 仓库。

## 3. 创建独立演示空间

### 3.1 创建带时间戳的新目录

输入：

```powershell
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$DemoRoot = Join-Path $RepoRoot "codeagent_runs\demos\library_lending\$Stamp"
New-Item -ItemType Directory -Force -Path $DemoRoot | Out-Null
Set-Location $DemoRoot
$DemoRoot
```

输出应类似：

```text
D:\Projects\CodeAgent\codeagent_runs\demos\library_lending\20260606_153000
```

从现在开始，所有演示命令都在 `$DemoRoot` 下运行。

### 3.2 准备输入材料和工作区

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
  Copy-Item "$RepoRoot\benchmark\selfbuilt\cases\04_library_lending\input\$Name" ".\direct\input\$Name"
  Copy-Item "$RepoRoot\benchmark\selfbuilt\cases\04_library_lending\input\$Name" ".\interactive\input\$Name"
}
```

目录用途如下：

| 目录 | 用途 |
|---|---|
| `direct\workspace` | 简单 `codeagent run` 使用的空工作区 |
| `direct\runs` | 简单 run 的 Agent 产物 |
| `interactive\workspace` | wizard 半交互式运行使用的空工作区 |
| `interactive\runs` | wizard 的 Agent 产物 |

输入：

```powershell
explorer .
```

打开 `direct\input` 或 `interactive\input`，先读 `PRD.md` 和 `acceptance_criteria.md`。重点看：默认入口必须启动本地 Web 服务、浏览器页面必须可操作、使用 SQLite 持久化、隐藏 oracle 会通过 HTTP 表单验证。

## 4. 简单命令行方式启动 Agent

这一章演示最轻量的路径：不写 YAML，不进入 wizard，不跑 benchmark。我们只用一条 `codeagent run` 命令让 Agent 从四份材料生成 Web UI 软件。

### 4.1 确认当前目录

输入：

```powershell
Get-Location
```

确认路径在 `$DemoRoot`。如果不在，输入：

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

这条命令会让 Agent 自动完成实现、测试、调试和修复。它不会执行隐藏 oracle，也不会产生 benchmark 分数。它适合快速展示“从四份输入材料到可运行软件”的过程。

### 4.3 查看直接运行产物

运行结束后输入：

```powershell
$LatestDirectRun = Get-ChildItem .\direct\runs -Directory |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
explorer $LatestDirectRun.FullName
```

建议按顺序查看：

| 文件 | 看什么 |
|---|---|
| `final_report.md` | 本次运行是否成功 |
| `task_config.yaml` | 规范化后的任务配置 |
| `metadata.json` | 模型、路径和运行元信息 |
| `workflow.log` | Agent 从计划到测试的时间线 |
| `implementation\implementation_plan.md` | 实现计划 |
| `implementation\implementation.patch.diff` | 实现补丁 |
| `testing\test_plan.md` | 测试计划 |
| `testing\test_result.json` | 公开自测结果 |

讲解时可以提醒读者：直接 run 虽然没有手写配置，但所有关键信息都会落盘，后续仍然可复盘。

## 5. 半交互式 wizard 演示

这一章是重点。它展示用户如何通过中文表单创建任务，并在人工审批点理解 Agent 的计划、补丁和测试。

### 5.1 启动 wizard

确认当前目录是 `$DemoRoot` 后输入：

```powershell
python -m codeagent wizard
```

### 5.2 推荐填写方式

按表单填写：

| 表单项 | 推荐填写 | 说明 |
|---|---|---|
| 执行阶段 | 实现 + 测试 + 调试 + 修复 | 展示完整流水线 |
| 项目目录 | `interactive\workspace` | 半交互式运行的空工作区 |
| 输入材料 | 四份 `interactive\input\*.md` | PRD、用户故事、设计模型、验收标准 |
| 输出目录 | `interactive\runs` | Agent 产物目录 |
| 测试命令 | `python -m pytest -q` | Agent 生成公开 pytest 自测 |
| 模型 | 本次演示模型 | 可选 `google/gemini-3.5-flash` 等 |
| 审批模式 | 人工审批 | 方便现场讲解计划和补丁 |

输入材料按这个顺序添加：

```text
interactive\input\PRD.md
interactive\input\user_stories.md
interactive\input\design_model.md
interactive\input\acceptance_criteria.md
```

确认页重点检查：

| 摘要项 | 应该是什么 |
|---|---|
| 项目目录 | `$DemoRoot\interactive\workspace` |
| 输出目录 | `$DemoRoot\interactive\runs` |
| 阶段 | `implement, test, debug, repair` |
| 审批模式 | `manual` |
| 测试命令 | `python -m pytest -q` |

如果项目目录指向 `D:\Projects\CodeAgent`，说明填错了，请取消并重新运行 wizard。

## 6. 审查 Agent 产物

人工审批模式下，CodeAgent 会在关键节点停下来。不要急着一路批准，演示价值就在这里。

### 6.1 审查实现计划

终端通常会列出：

```text
implementation_plan.md (implementation/implementation_plan.md)
```

在支持终端链接的环境中，按住 `Ctrl` 并单击文件名打开。如果不支持，手动进入 `interactive\runs\<最新 run>\implementation\implementation_plan.md`。

阅读时重点看：

| 检查点 | 为什么重要 |
|---|---|
| 是否明确 Web UI | 本案例不能只做 CLI |
| 是否使用标准库 | 应使用 `http.server` 和 `sqlite3`，不引入 Flask |
| 是否有 `create_server` | oracle 会导入它并用动态端口测试 |
| 是否有浏览器页面 | 首页、表单、库存页、逾期页都要能访问 |
| 是否覆盖业务规则 | 14 天借期、库存、重复借阅、还书、逾期 |
| 是否覆盖错误处理 | 稳定错误短语必须出现在 HTML 响应正文 |

如果计划不足，可以反馈：

```text
请确保默认入口启动本地 Web 服务，使用 http.server + sqlite3，并实现 create_server(db_path, host, port) 供测试动态端口启动。
```

### 6.2 审查实现补丁

计划通过后，打开：

```text
implementation\implementation.patch.diff
implementation\file_patches
```

重点看：

- 是否创建 `library_lending/__main__.py`。
- 是否暴露 `create_server(...)`。
- 是否有 HTTP handler、HTML 表单和路由。
- 是否使用 SQLite 初始化和查询。
- 是否没有创建 Flask/FastAPI/Django 依赖。
- 是否只修改 `interactive\workspace`，不改 CodeAgent 源码。

第一次补丁审批时，可以认真看第一个文件；确认方向正确后，可选择“是，应用此补丁，本阶段不再提示”，让同阶段后续单文件补丁自动通过。

### 6.3 审查测试计划和测试补丁

实现完成后，打开：

```text
testing\test_plan.md
testing\test.patch.diff
```

测试计划应覆盖：

- 本地 Web 服务启动。
- 首页包含 `图书借阅管理系统`。
- 表单添加图书和注册读者。
- 借书后 due date 是 14 天后。
- 库存 available 会变化。
- 重复借阅、无库存、非法日期等错误。
- SQLite 重新加载后数据仍在。

测试命令通常是：

```text
python -m pytest -q
```

这只是 Agent 自己生成的公开自测，不是隐藏 oracle。

### 6.4 如果进入调试或修复阶段

如果公开测试失败，打开：

| 文件 | 看什么 |
|---|---|
| `debugging\failure_summary.md` | 失败摘要 |
| `debugging\debug_report.md` | 根因分析 |
| `repair\repair_plan.md` | 修复策略 |
| `repair\repair.patch.diff` | 修复补丁 |
| `repair\repair_test_result.json` | 修复后的测试结果 |

讲解时可以说：测试失败不是演示失败，正好可以展示 Agent 如何根据失败证据进入调试和修复。

## 7. 半交互式运行结束后读结果

打开：

```text
interactive\runs
```

进入最新 run 目录，建议查看：

| 文件 | 用途 |
|---|---|
| `task_config.yaml` | wizard 表单固化后的标准配置 |
| `metadata.json` | 模型和运行元数据 |
| `final_report.md` | 最终状态和阶段摘要 |
| `workflow.log` | 最适合讲解 Agent 做了什么 |
| `decision_trace.jsonl` | 每个审批点的人工或自动决策 |
| `workflow_events.jsonl` | 机器可读事件流 |

然后打开：

```text
interactive\workspace
```

这里应能看到 Agent 生成的 `library_lending` 包和测试文件。它就是下一章要在浏览器里运行的软件。

## 8. 在浏览器中运行生成的软件

这一章是案例四和前三个案例最大的不同：最终产物不是 TUI，而是本地 Web UI。

### 8.1 启动生成的软件

打开第一个 PowerShell，输入：

```powershell
Set-Location "$DemoRoot\interactive\workspace"
python -m library_lending --db library.db --host 127.0.0.1 --port 8054
```

如果终端显示类似下面内容，说明服务已启动：

```text
Library Lending Manager running at http://127.0.0.1:8054/
```

这个命令会占用当前终端。后续请保持它运行。演示结束时按 `Ctrl+C` 停止服务。

### 8.2 打开浏览器

在浏览器地址栏访问：

```text
http://127.0.0.1:8054/
```

首页应显示 `图书借阅管理系统`，并能看到添加图书、注册读者、借书、还书、库存和逾期查询入口。

### 8.3 添加图书

在页面上找到添加图书表单，填写：

| 字段 | 值 |
|---|---|
| ISBN | `978-1` |
| 书名 | `Clean Code` |
| 作者 | `Robert Martin` |
| 册数 | `2` |

提交后，页面应显示：

```text
book 978-1 available copies: 2
```

进入库存页，应看到：

```text
978-1 Clean Code by Robert Martin copies 2 available 2
```

### 8.4 注册读者

在注册读者表单中填写：

| 字段 | 值 |
|---|---|
| 读者编号 | `r1` |
| 姓名 | `Ada` |

提交后应看到：

```text
reader r1 registered
```

### 8.5 办理借书

在借书表单中填写：

| 字段 | 值 |
|---|---|
| 读者编号 | `r1` |
| ISBN | `978-1` |
| 借出日期 | `2026-06-01` |

提交后应看到：

```text
borrowed 978-1 by r1 due 2026-06-15
```

再进入库存页，应看到 available 从 2 变成 1：

```text
978-1 Clean Code by Robert Martin copies 2 available 1
```

这一步说明系统正确记录了未归还借阅，并让库存随借阅变化。

### 8.6 查询逾期

打开逾期查询入口，日期输入：

```text
2026-06-20
```

应看到：

```text
r1 978-1 due 2026-06-15
```

因为借出日期是 2026-06-01，应还日期是 14 天后的 2026-06-15，所以 2026-06-20 查询时已经逾期。

### 8.7 办理还书

在还书表单中填写：

| 字段 | 值 |
|---|---|
| 读者编号 | `r1` |
| ISBN | `978-1` |
| 归还日期 | `2026-06-05` |

提交后应看到：

```text
returned 978-1 by r1
```

再次查看库存，应看到 available 恢复为 2。再次用 `2026-06-20` 查询逾期，应看到：

```text
no overdue loans
```

### 8.8 验证持久化

在第一个 PowerShell 中按 `Ctrl+C` 停止服务，然后重新启动：

```powershell
python -m library_lending --db library.db --host 127.0.0.1 --port 8054
```

刷新浏览器中的库存页。之前添加的 `Clean Code` 应仍然存在。这说明数据保存到了 `library.db`，不是临时内存。

演示结束后按 `Ctrl+C` 停止服务，并输入：

```powershell
Set-Location $DemoRoot
```

## 9. 三种运行方式对比

| 对比项 | 简单命令行 run | 半交互式 wizard | 单 case benchmark |
|---|---|---|---|
| 目的 | 快速生成软件 | 展示人工审批和过程可解释性 | 标准化隐藏评测 |
| 启动方式 | `python -m codeagent run ...` | `python -m codeagent wizard` | `python -m codeagent benchmark --config ...` |
| 是否人工填写表单 | 否 | 是 | 否 |
| 是否人工审批 | 自动审批 | 推荐人工审批 | 自动审批 |
| 是否运行隐藏 oracle | 否 | 否 | 是 |
| 生成软件位置 | `direct\workspace` | `interactive\workspace` | `case_workspaces\04_library_lending\workspace` |
| 适合证明 | 一条命令跑通流程 | Agent 计划、补丁、测试可审查 | 最终软件通过标准化验收 |

推荐讲解方式：

> run 证明 CodeAgent 能快速从材料生成软件，wizard 证明人可以审查和干预过程，benchmark 证明最终软件能通过隐藏 oracle。

## 10. 常见问题

### 10.1 为什么本案例不用 Flask

本案例特意要求标准库 Web UI，用 `http.server` 和 `sqlite3` 即可。这样它和第五个 Flask API 案例形成区分：第四案考察浏览器页面和表单交互，第五案考察 Flask API。

### 10.2 如果浏览器打不开页面

先确认启动服务的 PowerShell 没有退出，并且端口是当前访问的端口。若 `8054` 被占用，可以换端口：

```powershell
python -m library_lending --db library.db --host 127.0.0.1 --port 8055
```

然后访问：

```text
http://127.0.0.1:8055/
```

### 10.3 如果生成的软件还是 CLI

打开 `implementation\implementation_plan.md` 和 `implementation\implementation.patch.diff`，检查是否创建了 HTTP server 和 HTML 页面。若还在审批阶段，可以反馈：

```text
请改为本地 Web UI：默认入口启动 http.server 服务，浏览器访问首页，并用 HTML 表单完成图书、读者、借书、还书、库存和逾期操作。
```

### 10.4 如果公开自测通过但浏览器体验不对

先看生成的页面是否包含稳定短语，例如 `book 978-1 available copies: 2`、`borrowed 978-1 by r1 due 2026-06-15`。这些短语既方便人读，也方便 oracle 验证。

如果页面只有 JSON 或只显示调试信息，说明不满足“浏览器可用”的产品目标。

### 10.5 如果中文显示乱码

优先用浏览器和 VS Code 查看。必要时在 PowerShell 中执行：

```powershell
chcp 65001
```

报告文件本身按 UTF-8 保存，通常在编辑器中正常显示。

## 11. 运行 Library Lending 单 case benchmark

前面的 run 和 wizard 用来讲“怎么使用 CodeAgent”；benchmark 用来验证“生成软件是否通过隐藏验收”。请把 benchmark 放在演示结尾。

如果只想演示本章的单 case benchmark，不需要先完成第 4 到第 10 章，但需要先完成这些准备：

1. 完成第 2.2 节，设置 `$RepoRoot`。
2. 完成第 2.3 节，确认 `python -m codeagent --help` 可用。
3. 完成第 2.4 节，确认 `OPENROUTER_API_KEY configured: True`。
4. 完成第 3.1 节，创建本次演示专用的 `$DemoRoot` 并进入该目录。

第 3.1 节中的 `codeagent_runs\demos\library_lending\$Stamp` 是“本次演示临时空间”，用来保存本章创建的 benchmark 配置和 case 副本；它不是 benchmark 最终评分输出目录。benchmark 运行结果仍会写到仓库统一的 `codeagent_runs\benchmarks\selfbuilt` 下。

### 11.1 准备演示专用 case 副本

输入：

```powershell
Set-Location $DemoRoot

$DemoCaseRoot = Join-Path $DemoRoot "benchmark_case"
New-Item -ItemType Directory -Force -Path $DemoCaseRoot | Out-Null
Copy-Item -LiteralPath "$RepoRoot\benchmark\selfbuilt\cases\04_library_lending" `
  -Destination $DemoCaseRoot `
  -Recurse `
  -Force

$DemoCase = Join-Path $DemoCaseRoot "04_library_lending"
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

这一步只修改本次演示空间里的 case 副本，不改仓库原始 case。

### 11.2 创建 benchmark 配置

输入：

```powershell
$DemoCaseConfigPosix = $CaseConfig -replace "\\", "/"
@"
schema_version: 1
name: library_lending_demo_benchmark
benchmark_id: library_lending_demo_benchmark
description: Library Lending Web UI single-case demo benchmark.
output_dir: ../../../benchmarks/selfbuilt
default_agent_visible_paths:
  - input
  - workspace
default_hidden_paths:
  - oracle_tests
cases:
  - case_id: 04_library_lending
    config: "$DemoCaseConfigPosix"
    enabled: true
    difficulty: medium_high
    project_type: web_ui
"@ | Set-Content -Path .\library_lending_benchmark.yaml -Encoding UTF8
```

打开 `library_lending_benchmark.yaml` 确认：

- `config` 指向本次演示空间的 `benchmark_case\04_library_lending\case.yaml`。
- `output_dir` 指向仓库统一的 `codeagent_runs\benchmarks\selfbuilt`。
- 模型写在 case 副本的 `case.yaml`，不是 benchmark 顶层。

### 11.3 启动 benchmark

输入：

```powershell
python -m codeagent benchmark --config .\library_lending_benchmark.yaml
```

benchmark 会：

1. 复制 case 副本到本次 benchmark 运行目录。
2. 让 Agent 只看到公开 `input/` 和空 `workspace/`。
3. 运行实现、测试、调试、修复流程。
4. 最后执行隐藏 oracle。

### 11.4 查看 benchmark 输出

运行结束后输入：

```powershell
$BenchmarkOutputRoot = Join-Path $RepoRoot "codeagent_runs\benchmarks\selfbuilt"
$LatestBenchmark = Get-ChildItem $BenchmarkOutputRoot -Directory |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
explorer $LatestBenchmark.FullName
```

重点看：

| 文件或目录 | 看什么 |
|---|---|
| `benchmark_report.md` | 总体是否成功，`oracle_success` 是否为 `True` |
| `benchmark_result.json` | 机器可读评测结果 |
| `case_workspaces\04_library_lending\workspace` | Agent 最终生成的软件 |
| `case_runs\04_library_lending\<最新 run>` | Agent 自己的运行产物 |
| `oracle_logs` | 隐藏 oracle 的运行日志 |

### 11.5 启动 benchmark 生成的软件

如果 benchmark 通过后想实际打开浏览器体验，不要启动前面 `interactive\workspace` 里的软件，而要启动 benchmark 输出目录里的软件。

输入：

```powershell
$BenchmarkWorkspace = Join-Path $LatestBenchmark.FullName "case_workspaces\04_library_lending\workspace"
Set-Location $BenchmarkWorkspace
python -m library_lending --db library.db --host 127.0.0.1 --port 8054
```

保持这个 PowerShell 不要关闭，然后在浏览器访问：

```text
http://127.0.0.1:8054/
```

页面应显示 `图书借阅管理系统`。接下来可以按第 8 章的浏览器操作流程体验添加图书、注册读者、借书、还书、查看库存和查询逾期。区别只是第 8 章启动的是 wizard 生成的软件，本节启动的是 benchmark 通过隐藏 oracle 后留下的软件副本。

如果 `8054` 被占用，可以换成 `8055`：

```powershell
python -m library_lending --db library.db --host 127.0.0.1 --port 8055
```

演示结束后，在启动服务的 PowerShell 中按 `Ctrl+C` 停止服务，再回到本次演示根目录：

```powershell
Set-Location $DemoRoot
```

### 11.6 benchmark 验证什么

隐藏 oracle 会验证：

- `create_server(db_path, host, port)` 是否存在。
- 首页是否可访问并包含 `图书借阅管理系统`。
- HTTP 表单能添加图书、注册读者、借书、还书。
- 库存 available 会随借还变化。
- due date 是否为借出日期后 14 天。
- 逾期查询是否正确。
- 重启服务后 SQLite 数据仍在。
- 错误响应是否包含稳定短语。

如果 Agent 自测通过但 oracle 失败，优先打开 `oracle_logs`，再对照 PRD 和验收标准检查生成软件。常见原因是：只做了 CLI、没有 `create_server`、页面没有稳定短语、库存计算不对，或错误只写到终端而没有出现在 HTML 响应中。
