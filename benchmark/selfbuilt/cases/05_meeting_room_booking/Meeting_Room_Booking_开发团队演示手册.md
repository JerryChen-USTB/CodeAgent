# Meeting Room Booking 开发团队演示手册

本文是一份面向开发团队的 `05_meeting_room_booking` 专项演示手册。它会一步一步带读者完成完整演示：准备独立演示空间，使用简单命令行方式启动 Agent，使用 wizard 体验半交互式运行，审查 Agent 生成的计划、补丁和测试，最后在浏览器中运行 Agent 生成出来的 Flask 会议室预约系统，并用单 case benchmark 做标准化验证。

请特别注意：本手册要求在新的演示空间根目录下启动 CodeAgent，不在 `D:\Projects\CodeAgent` 仓库根目录里直接运行演示。新的演示空间放在当前仓库的 `codeagent_runs/demos/meeting_room_booking/<时间戳>/` 下，属于运行产物，会被 Git 忽略。每次演示都会创建带时间戳的新目录，不需要删除上一次演示空间。

## 1. 案例介绍

### 1.1 Meeting Room Booking 是什么任务

`05_meeting_room_booking` 是自建 benchmark 的第五个案例，也是难度最高的自建案例。它要求 CodeAgent 从空 `workspace/` 开始，生成一个 Flask 会议室预约系统。这个系统既要有浏览器 Web UI，也要保留 JSON API。

可以这样向开发团队解释它和案例四的关系：

| 案例 | 技术形态 | 交互方式 | 重点 |
|---|---|---|---|
| 案例四 Library Lending | 标准库 Web UI | 浏览器 HTML 表单 | 不依赖第三方 Web 框架 |
| 案例五 Meeting Room Booking | Flask Web UI + JSON API | 浏览器页面 + HTTP API | Web 应用框架、API 契约、冲突检测 |

案例五不是把案例四的图书业务换成 Flask，而是保留会议室预约题材，并把它升级为可在浏览器中操作的 Flask Web 应用。

### 1.2 四份输入材料

Agent 可见输入只保留四份简体中文材料：

| 材料 | 路径 | 用途 |
|---|---|---|
| PRD | `input/PRD.md` | 描述 Flask Web UI、JSON API、SQLite、冲突检测、启动方式和非目标 |
| 用户故事 | `input/user_stories.md` | 描述管理员和员工如何创建会议室、预约、筛选和取消 |
| 设计模型 | `input/design_model.md` | 描述 Flask app factory、路由、service、repository、SQLite 表和状态流 |
| 验收标准 | `input/acceptance_criteria.md` | 描述 Web UI、API、持久化、冲突和错误处理的验收点 |

最终软件应支持默认启动：

```powershell
python -m meeting_room_booking --db meeting_rooms.db --host 127.0.0.1 --port 8000
```

启动后浏览器访问：

```text
http://127.0.0.1:8000/
```

首页应包含 `会议室预约系统`，并能进入会议室管理和预约管理页面。JSON API 也必须保留，例如 `/health`、`/rooms`、`/bookings`。

### 1.3 本案例考察什么

| 能力 | 在本案例中的体现 |
|---|---|
| Flask 项目生成 | 创建 `meeting_room_booking` 包、`create_app` 工厂函数和启动入口 |
| Web UI | 浏览器页面和表单可以创建会议室、创建预约、查询、取消 |
| JSON API | 保留稳定 API，供自动化集成和 oracle 验收 |
| SQLite 持久化 | 使用可注入 db_path，重启 app 后数据仍在 |
| 时间冲突检测 | 同会议室重叠预约被拒绝，边界相接允许 |
| 测试与修复 | 生成公开自测，失败时进入调试和修复 |

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
$DemoRoot = Join-Path $RepoRoot "codeagent_runs\demos\meeting_room_booking\$Stamp"
New-Item -ItemType Directory -Force -Path $DemoRoot | Out-Null
Set-Location $DemoRoot
$DemoRoot
```

输出应类似：

```text
D:\Projects\CodeAgent\codeagent_runs\demos\meeting_room_booking\20260606_153000
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
  Copy-Item "$RepoRoot\benchmark\selfbuilt\cases\05_meeting_room_booking\input\$Name" ".\direct\input\$Name"
  Copy-Item "$RepoRoot\benchmark\selfbuilt\cases\05_meeting_room_booking\input\$Name" ".\interactive\input\$Name"
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

打开 `direct\input` 或 `interactive\input`，先读 `PRD.md` 和 `acceptance_criteria.md`。重点看：生成软件必须有 Flask Web UI、JSON API、SQLite 持久化、冲突检测和取消预约释放时间段。

## 4. 简单命令行方式启动 Agent

这一章演示最轻量的路径：不写 YAML，不进入 wizard，不跑 benchmark。只用一条 `codeagent run` 命令，让 Agent 从四份材料生成 Flask 项目。

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

这条命令会让 Agent 自动完成实现、测试、调试和修复。它不会执行隐藏 oracle，也不会产生 benchmark 分数。它适合快速展示“从四份输入材料到可运行 Flask 软件”的过程。

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

讲解时提醒读者：直接 run 虽然自动审批，但产物仍完整落盘，可以复查每个阶段。

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
| 是否明确 Flask Web UI | 本案例不能只做 JSON API |
| 是否保留 JSON API | oracle 会稳定检查 `/health`、`/rooms`、`/bookings` |
| 是否有 `create_app` | Flask app factory 是测试和部署入口 |
| 是否有 `requirements.txt` | 必须声明 Flask 依赖 |
| 是否使用 SQLite | 数据要跨重启持久化 |
| 是否覆盖冲突检测 | 重叠预约拒绝，边界相接允许 |
| 是否覆盖取消预约 | 取消后不显示，并释放时间段 |
| 是否关闭数据库连接 | Windows 临时数据库文件不能被占用 |

如果计划不足，可以反馈：

```text
请确保实现同时包含浏览器 Web UI 和 JSON API：Web UI 用 /ui/rooms、/ui/bookings 表单操作，API 保留 /health、/rooms、/bookings，并暴露 create_app(db_path=None)。
```

### 6.2 审查实现补丁

计划通过后，打开：

```text
implementation\implementation.patch.diff
implementation\file_patches
```

重点看：

- 是否创建 `meeting_room_booking/__init__.py` 并导出 `create_app`。
- 是否创建 `meeting_room_booking/__main__.py` 支持 `python -m meeting_room_booking --db ...`。
- 是否创建 `requirements.txt` 且包含 Flask。
- 是否有 Flask 路由：`/`、`/ui/rooms`、`/ui/bookings`、`/health`、`/rooms`、`/bookings`。
- 是否有 service/repository 或等价分层。
- 是否处理 SQLite 连接关闭。
- 是否只修改 `interactive\workspace`，不改 CodeAgent 源码。

第一次补丁审批时，可以认真看第一个文件；确认方向正确后，可选择“是，应用此补丁，本阶段不再提示”，让同阶段后续单文件补丁自动通过。

### 6.3 审查测试计划和测试补丁

实现完成后，打开：

```text
testing\test_plan.md
testing\test.patch.diff
```

测试计划应覆盖：

- `create_app(db_path=...)` 可以导入。
- 首页包含 `会议室预约系统`。
- Web 表单创建会议室和预约。
- Web 列表支持按日期和会议室筛选。
- Web 表单取消预约。
- JSON API 创建会议室、创建预约、查询、取消。
- 重叠冲突、边界相接、取消后重新预约。
- SQLite 重启后数据仍在。

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

讲解时可以说：测试失败不代表演示失败，正好可以展示 Agent 如何根据失败证据进入调试和修复。

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

这里应能看到 Agent 生成的 `meeting_room_booking` 包、`requirements.txt` 和测试文件。它就是下一章要在浏览器里运行的软件。

## 8. 在浏览器中运行生成的软件

这一章是案例五的核心体验：最终产物既能在浏览器里操作，也能通过 JSON API 被调用。

### 8.1 安装生成项目依赖

打开第一个 PowerShell，输入：

```powershell
Set-Location "$DemoRoot\interactive\workspace"
python -m pip install -r requirements.txt
```

这一步安装 Agent 生成项目声明的 Flask 依赖。若 `requirements.txt` 不存在，说明生成结果没有满足案例要求，请回到实现计划或补丁检查原因。

### 8.2 启动 Flask Web 应用

输入：

```powershell
python -m meeting_room_booking --db meeting_rooms.db --host 127.0.0.1 --port 8055
```

如果终端显示类似下面内容，说明服务已启动：

```text
Running on http://127.0.0.1:8055
```

这个命令会占用当前终端。后续保持它运行。演示结束时按 `Ctrl+C` 停止服务。

### 8.3 打开浏览器

在浏览器地址栏访问：

```text
http://127.0.0.1:8055/
```

首页应显示 `会议室预约系统`，并能看到会议室管理和预约管理入口。

### 8.4 创建会议室

进入会议室管理页面，填写：

| 字段 | 值 |
|---|---|
| 名称 | `Room A` |
| 容量 | `8` |
| 位置 | `2F` |

提交后，页面应显示 `Room A`，会议室列表中应能看到容量 `8` 和位置 `2F`。

如果再次提交同名 `Room A`，页面应显示：

```text
room already exists
```

这一步证明系统有唯一性校验，不只是简单插入数据。

### 8.5 创建预约

进入预约管理页面，填写：

| 字段 | 值 |
|---|---|
| 会议室 | `Room A` 的 ID 或下拉选项 |
| 预约人 | `Ada` |
| 标题 | `Weekly Sync` |
| 开始时间 | `2026-06-10 09:00` |
| 结束时间 | `2026-06-10 10:00` |

提交后，页面应显示 `Weekly Sync`、`Ada` 和 `2026-06-10 09:00`。如果页面提供筛选框，输入日期 `2026-06-10` 和会议室后，应能看到这条 active 预约。

### 8.6 演示冲突检测和边界相接

尝试在同一个会议室创建重叠预约：

| 字段 | 值 |
|---|---|
| 预约人 | `Bob` |
| 标题 | `Overlap` |
| 开始时间 | `2026-06-10 09:30` |
| 结束时间 | `2026-06-10 10:30` |

页面应显示：

```text
booking conflict
```

再创建边界相接预约：

| 字段 | 值 |
|---|---|
| 预约人 | `Bob` |
| 标题 | `Next` |
| 开始时间 | `2026-06-10 10:00` |
| 结束时间 | `2026-06-10 11:00` |

这条应允许创建。讲解时强调：`09:00-10:00` 和 `10:00-11:00` 边界相接，不算冲突。

### 8.7 取消预约并重新预约

在预约列表中找到 `Weekly Sync`，点击或提交取消操作。取消后，再用 `2026-06-10` 查询，`Weekly Sync` 不应出现在 active 列表中。

随后创建一个原本会与 `Weekly Sync` 重叠的预约：

| 字段 | 值 |
|---|---|
| 预约人 | `Dana` |
| 标题 | `Replacement` |
| 开始时间 | `2026-06-10 09:15` |
| 结束时间 | `2026-06-10 09:45` |

这条应创建成功。它证明取消预约不仅改变显示，也真的释放了冲突检测中的时间段。

### 8.8 验证 SQLite 持久化

在第一个 PowerShell 中按 `Ctrl+C` 停止服务，然后重新启动：

```powershell
python -m meeting_room_booking --db meeting_rooms.db --host 127.0.0.1 --port 8055
```

刷新浏览器中的会议室和预约页面。之前创建的 `Room A`、`Next`、`Replacement` 应仍然存在；已取消的 `Weekly Sync` 不应出现在 active 查询结果中。

## 9. 用 JSON API 快速交叉验证

Web UI 是演示重点，JSON API 是自动化验收重点。可以打开第二个 PowerShell，用少量命令证明同一软件也能被 API 调用。

### 9.1 健康检查

输入：

```powershell
curl.exe -i http://127.0.0.1:8055/health
```

应看到：

```text
HTTP/1.1 200 OK
{"status":"ok"}
```

### 9.2 查看会议室

输入：

```powershell
curl.exe -i http://127.0.0.1:8055/rooms
```

应能看到 JSON 数组，包含 `Room A`。

### 9.3 创建 API 预约并验证冲突

创建另一个会议室，避免和浏览器演示数据混在一起：

```powershell
'{"name":"Room B","capacity":4,"location":"3F"}' |
  curl.exe -i -X POST http://127.0.0.1:8055/rooms `
    -H "Content-Type: application/json" `
    --data-binary "@-"
```

创建预约：

```powershell
'{"room_id":2,"user":"Chen","title":"API Review","start":"2026-06-11 09:00","end":"2026-06-11 10:00"}' |
  curl.exe -i -X POST http://127.0.0.1:8055/bookings `
    -H "Content-Type: application/json" `
    --data-binary "@-"
```

再提交重叠预约：

```powershell
'{"room_id":2,"user":"Dana","title":"Overlap","start":"2026-06-11 09:30","end":"2026-06-11 10:30"}' |
  curl.exe -i -X POST http://127.0.0.1:8055/bookings `
    -H "Content-Type: application/json" `
    --data-binary "@-"
```

应看到状态码 409 和：

```json
{"error":"booking conflict"}
```

讲解时可以说：浏览器页面和 API 复用同一套业务规则，不是两套割裂实现。

### 9.4 回到演示空间

演示结束后，在运行 Flask 的 PowerShell 中按 `Ctrl+C` 停止服务，然后输入：

```powershell
Set-Location $DemoRoot
```

## 10. 三种运行方式对比

| 对比项 | 简单命令行 run | 半交互式 wizard | 单 case benchmark |
|---|---|---|---|
| 目的 | 快速生成软件 | 展示人工审批和过程可解释性 | 标准化隐藏评测 |
| 启动方式 | `python -m codeagent run ...` | `python -m codeagent wizard` | `python -m codeagent benchmark --config ...` |
| 是否人工填写表单 | 否 | 是 | 否 |
| 是否人工审批 | 自动审批 | 推荐人工审批 | 自动审批 |
| 是否运行隐藏 oracle | 否 | 否 | 是 |
| 生成软件位置 | `direct\workspace` | `interactive\workspace` | `case_workspaces\05_meeting_room_booking\workspace` |
| 适合证明 | 一条命令跑通流程 | Agent 计划、补丁、测试可审查 | 最终软件通过标准化验收 |

推荐讲解方式：

> run 证明 CodeAgent 能快速从材料生成软件，wizard 证明人可以审查和干预过程，benchmark 证明最终软件能通过隐藏 oracle。案例五额外证明：一个生成项目可以同时服务浏览器用户和 API 调用方。

## 11. 常见问题

### 11.1 为什么案例五同时要求 Web UI 和 API

因为它是 Flask 综合项目。Web UI 用于真实演示，API 用于稳定验收和系统集成。两者应复用同一套 service 和 repository，业务规则不能写两遍。

### 11.2 如果缺少 requirements.txt

说明 Agent 没有满足 Flask 项目的依赖声明要求。打开实现计划和补丁检查是否提到 `Flask>=3.0,<4.0`。若还在审批阶段，可以反馈：

```text
请创建 workspace/requirements.txt，并声明 Flask>=3.0,<4.0。
```

### 11.3 如果浏览器打不开页面

先确认启动服务的 PowerShell 没有退出，并且端口是当前访问的端口。若 `8055` 被占用，可以换端口：

```powershell
python -m meeting_room_booking --db meeting_rooms.db --host 127.0.0.1 --port 8056
```

然后访问：

```text
http://127.0.0.1:8056/
```

### 11.4 如果生成软件只有 API 没有页面

打开 `implementation\implementation_plan.md` 和 `implementation\implementation.patch.diff`，检查是否有 `/`、`/ui/rooms`、`/ui/bookings`。若还在审批阶段，可以反馈：

```text
请补充浏览器 Web UI：首页包含会议室预约系统，/ui/rooms 提供会议室列表和创建表单，/ui/bookings 提供预约列表、筛选、创建和取消操作。
```

### 11.5 如果 API 正常但 Web UI 冲突检测不对

这通常说明 Web UI 和 JSON API 没有复用同一套 service。检查实现中是否把冲突检测只写在 API 路由里。正确做法是 Web 表单和 API 都调用同一个业务函数。

### 11.6 如果 Windows 删除临时数据库失败

可能是 SQLite 连接没有关闭。材料已经要求每次数据库操作后关闭连接。检查 repository 是否使用了显式 `close()`、`try/finally` 或 `contextlib.closing`。

## 12. 运行 Meeting Room Booking 单 case benchmark

前面的 run 和 wizard 用来讲“怎么使用 CodeAgent”；benchmark 用来验证“生成软件是否通过隐藏验收”。请把 benchmark 放在演示结尾。

如果只想演示本章的单 case benchmark，不需要先完成第 4 到第 11 章，但需要先完成这些准备：

1. 完成第 2.2 节，设置 `$RepoRoot`。
2. 完成第 2.3 节，确认 `python -m codeagent --help` 可用。
3. 完成第 2.4 节，确认 `OPENROUTER_API_KEY configured: True`。
4. 完成第 3.1 节，创建本次演示专用的 `$DemoRoot` 并进入该目录。

第 3.1 节中的 `codeagent_runs\demos\meeting_room_booking\$Stamp` 是“本次演示临时空间”，用来保存本章创建的 benchmark 配置和 case 副本；它不是 benchmark 最终评分输出目录。benchmark 运行结果仍会写到仓库统一的 `codeagent_runs\benchmarks\selfbuilt` 下。

### 12.1 准备演示专用 case 副本

输入：

```powershell
Set-Location $DemoRoot

$DemoCaseRoot = Join-Path $DemoRoot "benchmark_case"
New-Item -ItemType Directory -Force -Path $DemoCaseRoot | Out-Null
Copy-Item -LiteralPath "$RepoRoot\benchmark\selfbuilt\cases\05_meeting_room_booking" `
  -Destination $DemoCaseRoot `
  -Recurse `
  -Force

$DemoCase = Join-Path $DemoCaseRoot "05_meeting_room_booking"
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

### 12.2 创建 benchmark 配置

输入：

```powershell
$DemoCaseConfigPosix = $CaseConfig -replace "\\", "/"
@"
schema_version: 1
name: meeting_room_booking_demo_benchmark
benchmark_id: meeting_room_booking_demo_benchmark
description: Meeting Room Booking Flask Web UI single-case demo benchmark.
output_dir: ../../../benchmarks/selfbuilt
default_agent_visible_paths:
  - input
  - workspace
default_hidden_paths:
  - oracle_tests
cases:
  - case_id: 05_meeting_room_booking
    config: "$DemoCaseConfigPosix"
    enabled: true
    difficulty: high
    project_type: flask_web_ui
    dependency_note: "Agent should create workspace/requirements.txt with Flask dependency during implementation."
"@ | Set-Content -Path .\meeting_room_booking_benchmark.yaml -Encoding UTF8
```

打开 `meeting_room_booking_benchmark.yaml` 确认：

- `config` 指向本次演示空间的 `benchmark_case\05_meeting_room_booking\case.yaml`。
- `output_dir` 指向仓库统一的 `codeagent_runs\benchmarks\selfbuilt`。
- 模型写在 case 副本的 `case.yaml`，不是 benchmark 顶层。

### 12.3 启动 benchmark

输入：

```powershell
python -m codeagent benchmark --config .\meeting_room_booking_benchmark.yaml
```

benchmark 会：

1. 复制 case 副本到本次 benchmark 运行目录。
2. 让 Agent 只看到公开 `input/` 和空 `workspace/`。
3. 运行实现、测试、调试、修复流程。
4. 最后执行隐藏 oracle。

### 12.4 查看 benchmark 输出

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
| `case_workspaces\05_meeting_room_booking\workspace` | Agent 最终生成的软件 |
| `case_runs\05_meeting_room_booking\<最新 run>` | Agent 自己的运行产物 |
| `oracle_logs` | 隐藏 oracle 的运行日志 |

### 12.5 benchmark 验证什么

隐藏 oracle 会验证：

- `create_app(db_path=...)` 是否可导入。
- `/health`、`/rooms`、`/bookings` JSON API 是否符合合同。
- 首页是否包含 `会议室预约系统`。
- Web 表单能创建会议室和预约。
- Web 列表能按日期和会议室显示 active 预约。
- Web 表单能取消预约。
- 重叠预约是否返回 `booking conflict`。
- 边界相接预约是否允许。
- 取消后同时间段是否可重新预约。
- 使用同一个 SQLite 文件重新创建 app 后数据是否仍在。
- 错误响应是否包含稳定错误短语。

如果 Agent 自测通过但 oracle 失败，优先打开 `oracle_logs`，再对照 PRD 和验收标准检查生成软件。常见原因是：只做了 API 没有 Web UI、Web UI 和 API 使用了两套业务规则、缺少 `create_app`、缺少 `requirements.txt`、冲突检测边界错误，或错误短语不稳定。
