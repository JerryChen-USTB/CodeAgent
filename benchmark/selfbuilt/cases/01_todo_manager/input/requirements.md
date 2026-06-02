# 待办事项管理系统需求说明

## 1. 项目背景

开发一个面向个人用户的命令行待办事项管理系统。用户希望通过终端快速记录任务、查看任务、标记完成任务，并将任务长期保存在本地文件中。系统不需要联网，不需要图形界面，也不需要多用户登录。

本案例要求 Agent 从空 `workspace/` 开始实现完整 Python 项目。

## 2. 技术约束

- 项目语言：Python 3.11+。
- 项目形态：CLI 工具。
- 入口命令：`python -m todo_manager`。
- 持久化方式：JSON 文件。
- 仅允许使用 Python 标准库。
- Agent 需要自行创建包目录、模块、命令行入口和必要的测试。

## 3. 命令行接口

所有命令都通过 `--file` 指定任务文件：

```bash
python -m todo_manager --file tasks.json <command> [options]
```

### 3.1 添加任务

```bash
python -m todo_manager --file tasks.json add --title "Write report" --due 2026-06-10 --priority high
```

规则：

- `--title` 必填，去除首尾空白后不能为空。
- `--due` 可选，格式为 `YYYY-MM-DD`。
- `--priority` 可选，只能是 `low`、`normal`、`high`，默认 `normal`。
- 新任务状态为 `open`。
- 新任务 ID 从 `1` 开始，后续使用当前最大 ID 加一。

成功输出：

```text
created task #1: Write report
```

### 3.2 查看任务

```bash
python -m todo_manager --file tasks.json list
python -m todo_manager --file tasks.json list --status open
python -m todo_manager --file tasks.json list --status done
```

规则：

- `--status` 可选，只能是 `all`、`open`、`done`，默认 `all`。
- 按任务 ID 升序输出。
- 没有任务时输出 `no tasks`。

每行格式：

```text
#<id> [<status>] <priority> <title> due <date-or-none>
```

示例：

```text
#1 [open] high Write report due 2026-06-10
#2 [done] normal Buy milk due none
```

### 3.3 完成任务

```bash
python -m todo_manager --file tasks.json done 1
```

规则：

- 任务存在时，将状态改为 `done`。
- 已完成任务重复完成不报错，仍输出完成信息。

成功输出：

```text
completed task #1: Write report
```

### 3.4 删除任务

```bash
python -m todo_manager --file tasks.json delete 1
```

成功输出：

```text
deleted task #1: Write report
```

## 4. 数据格式

任务文件是 UTF-8 JSON，顶层为数组：

```json
[
  {
    "id": 1,
    "title": "Write report",
    "status": "open",
    "priority": "high",
    "due": "2026-06-10"
  }
]
```

保存时要求：

- 使用两个空格缩进。
- 保持中文内容不转义。
- 字段键稳定，至少包含 `id`、`title`、`status`、`priority`、`due`。

## 5. 异常处理

- 文件不存在时视为空任务列表。
- JSON 文件格式错误时，命令失败，退出码非 0，stderr 包含 `invalid task file`。
- 任务 ID 不存在时，命令失败，stderr 包含 `task not found`。
- 日期格式错误时，命令失败，stderr 包含 `invalid due date`。
- 标题为空时，命令失败，stderr 包含 `title is required`。
- 非法优先级或状态过滤值应由 CLI 参数校验拒绝。

## 6. 阶段要求

Agent 应完成以下阶段：

1. 实现：根据本文档、PRD、用户故事和设计模型创建项目代码。
2. 测试：根据验收标准设计测试并运行。
3. 调试：如果测试失败，定位命令行解析、文件读写或状态流转问题。
4. 修复：修改代码并重新运行测试，直到通过。
