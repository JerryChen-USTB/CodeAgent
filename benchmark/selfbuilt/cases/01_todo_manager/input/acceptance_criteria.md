# 待办事项管理系统验收标准

## AC-01 添加任务

给定不存在的任务文件，当执行：

```bash
python -m todo_manager --file tasks.json add --title "Write report" --due 2026-06-10 --priority high
```

则：

- 退出码为 0。
- stdout 包含 `created task #1: Write report`。
- `tasks.json` 中保存一条 ID 为 1、状态为 open、优先级为 high 的任务。

## AC-02 查看和过滤

给定任务文件中有 open 和 done 任务：

- `list` 显示所有任务。
- `list --status open` 只显示 open 任务。
- `list --status done` 只显示 done 任务。
- 输出按 ID 升序。

## AC-03 完成任务

执行 `done 1` 后：

- 退出码为 0。
- stdout 包含 `completed task #1`。
- JSON 文件中该任务状态变为 `done`。

## AC-04 删除任务

执行 `delete 1` 后：

- 退出码为 0。
- stdout 包含 `deleted task #1`。
- JSON 文件中不再包含该任务。

## AC-05 异常输入

以下情况必须失败并返回非 0：

- 空标题。
- 错误日期格式。
- 不存在的任务 ID。
- 无法解析的 JSON 文件。

错误信息应写入 stderr，且包含文档中定义的关键词。
