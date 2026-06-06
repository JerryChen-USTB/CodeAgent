# CodeAgent TUI Harness

这是一个独立的开发辅助工具，用真实 PTY 启动并操作 `python -m codeagent wizard`。它不调用 CodeAgent 的内部测试 backend，也不修改 `codeagent/` 产品代码；Codex 可以通过 `observe` 查看当前 TUI 画面，再通过 `act` 发送真实按键或文本。

## 安装工具依赖

```powershell
python -m pip install -r tools/tui_harness/requirements.txt
```

Windows 使用 `pywinpty`，Linux/macOS 使用 `pexpect`，两端都使用 `pyte` 解析 ANSI 终端画面。

## 启动真实 TUI 会话

```powershell
python -m tools.tui_harness start `
  --session codeagent_runs/dev_tui_sessions/20260605_001 `
  --cwd D:\Projects\CodeAgent `
  -- python -m codeagent wizard
```

`start` 会启动一个本地 daemon 持有 PTY。后续命令通过 loopback IPC 控制同一个 TUI 进程，因此适合 Codex 多次观察、多次操作。

## 观察当前画面

```powershell
python -m tools.tui_harness observe --session codeagent_runs/dev_tui_sessions/20260605_001
python -m tools.tui_harness observe --session codeagent_runs/dev_tui_sessions/20260605_001 --json
```

`observe` 会输出当前屏幕、识别到的 `prompt_kind`、可见选项、审批上下文文件、命令和建议动作。审批阶段看到文件路径后，Codex 应先读取对应文件，再决定批准还是反馈调整意见。

## 执行动作

```powershell
python -m tools.tui_harness act --session codeagent_runs/dev_tui_sessions/20260605_001 --select-label "项目目录"
python -m tools.tui_harness act --session codeagent_runs/dev_tui_sessions/20260605_001 --text "D:\Projects\CodeAgent\codeagent_runs\demos\todo_manager\...\interactive\workspace"
python -m tools.tui_harness act --session codeagent_runs/dev_tui_sessions/20260605_001 --approve
python -m tools.tui_harness act --session codeagent_runs/dev_tui_sessions/20260605_001 --approve-rest
python -m tools.tui_harness act --session codeagent_runs/dev_tui_sessions/20260605_001 --respond "请补充完整的 TUI 端到端测试。"
```

`--select-label` 按当前画面的可见 label 定位选项，不依赖固定的“向下几次”。当画面无法识别时，工具只建议使用低层 `--keys` 调试动作。

## 停止会话

```powershell
python -m tools.tui_harness stop --session codeagent_runs/dev_tui_sessions/20260605_001
```

## 产物

每个 session 目录会包含：

- `session.json`：daemon PID、端口、命令、状态。
- `screen.txt`：最近一次解析后的终端画面。
- `terminal.raw.log`：原始终端输出。
- `terminal.clean.log`：去除 ANSI 控制序列后的输出。
- `events.jsonl`：启动、观察、动作、停止等工具事件。
