# 待办事项管理系统验收标准

## AC-01 默认入口启动 TUI

给定空 `workspace/` 中已经由 Agent 实现 `todo_manager` 包，当执行：

```bash
python -m todo_manager --file tasks.json
```

则：

- 程序进入交互式文本菜单，而不是要求用户继续输入子命令参数。
- stdout 包含 `Todo Manager`。
- stdout 能看到添加、查看、完成、删除和退出五类动作。
- 输入 `5`、`q`、`quit` 或 `exit` 后，程序退出码为 0。
- stdin 到达 EOF 时程序不会无限挂起。

## AC-02 一个会话内完成添加和查看

给定不存在的任务文件，当通过 stdin 输入以下内容：

```text
1
Write report
2026-06-10
high
1
Buy milk


2
open
5
```

则：

- 退出码为 0。
- stdout 包含 `created task #1: Write report`。
- stdout 包含 `created task #2: Buy milk`。
- stdout 包含 `#1 [open] high Write report due 2026-06-10`。
- stdout 包含 `#2 [open] normal Buy milk due none`。
- JSON 文件中保存两条任务，状态均为 `open`。

## AC-03 完成任务并跨会话持久化

给定任务文件中已有 `#1 Write report` 和 `#2 Buy milk`，当再次启动程序并输入：

```text
3
1
2
done
5
```

则：

- stdout 包含 `completed task #1: Write report`。
- done 列表中包含 `#1 [done] high Write report due 2026-06-10`。
- done 列表中不包含 `#2`。
- 程序退出后，JSON 文件中任务 `#1` 的 `status` 为 `done`。

## AC-04 删除任务并保存

给定任务文件中存在 `#2 Buy milk`，当启动程序并输入：

```text
4
2
2
all
5
```

则：

- stdout 包含 `deleted task #2: Buy milk`。
- 后续任务列表不再显示 `#2 [open] normal Buy milk due none`。
- JSON 文件中不再包含 ID 为 2 的任务。

## AC-05 空列表和过滤

给定不存在的任务文件，当启动程序并输入：

```text
2
all
5
```

则：

- 退出码为 0。
- stdout 包含 `no tasks`。

给定任务文件中同时存在 open 和 done 任务：

- 输入过滤条件 `all` 或空值时显示全部任务。
- 输入 `open` 时只显示 open 任务。
- 输入 `done` 时只显示 done 任务。
- 输出按 ID 升序。

## AC-06 可恢复输入错误

以下错误必须在 TUI 会话中可恢复，不能导致程序崩溃或输出 traceback：

| 输入场景 | 期望错误关键词 |
| --- | --- |
| 添加任务时标题为空 | `title is required` |
| 添加任务时日期为 `2026/06/10` | `invalid due date` |
| 添加任务时优先级为 `urgent` | `invalid priority` |
| 查看任务时过滤条件为 `blocked` | `invalid status` |
| 完成或删除不存在 ID | `task not found` |
| 输入未知菜单选项 | `unknown option` |

每个错误发生后，程序应返回主菜单并继续接受后续输入。

## AC-07 启动级数据文件错误

给定 `tasks.json` 已存在但内容不是合法 JSON，当执行：

```bash
python -m todo_manager --file tasks.json
```

则：

- 程序退出码非 0。
- stderr 包含 `invalid task file`。
- 程序不进入菜单。
- 程序不覆盖原有损坏文件。

## AC-08 JSON 数据格式

保存后的 JSON 文件必须满足：

- 顶层为数组。
- 每个任务至少包含 `id`、`title`、`status`、`priority`、`due`。
- 无截止日期时 `due` 为 `null`。
- 保存时使用两个空格缩进。
- 保存时保留中文内容，不转义为 `\uXXXX`。

## AC-09 技术约束

- 入口命令为 `python -m todo_manager`。
- 必须支持 `--file` 指定任务文件。
- 默认 `python -m todo_manager --file tasks.json` 是交互式 TUI。
- 仅允许使用 Python 标准库。
- 程序能在 Windows、Linux、macOS 的普通终端中运行。
