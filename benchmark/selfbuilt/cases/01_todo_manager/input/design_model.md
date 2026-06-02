# 待办事项管理系统设计模型

## 1. 类图

```mermaid
classDiagram
    class Task {
      +int id
      +str title
      +str status
      +str priority
      +str? due
    }

    class TaskRepository {
      +load() list~Task~
      +save(tasks) None
      +next_id(tasks) int
    }

    class TodoService {
      +add(title, due, priority) Task
      +list(status) list~Task~
      +mark_done(id) Task
      +delete(id) Task
    }

    class CliController {
      +main(argv) int
    }

    TaskRepository --> Task
    TodoService --> TaskRepository
    CliController --> TodoService
```

## 2. 添加任务活动图

```mermaid
flowchart TD
    A["用户执行 add 命令"] --> B["解析 title/due/priority"]
    B --> C{"输入是否合法?"}
    C -- "否" --> D["stderr 输出错误并返回非 0"]
    C -- "是" --> E["读取 JSON 任务文件"]
    E --> F["计算 max(id)+1"]
    F --> G["创建 open 任务"]
    G --> H["保存 JSON"]
    H --> I["stdout 输出 created task"]
```

## 3. 状态机

```mermaid
stateDiagram-v2
    [*] --> open: add
    open --> done: done
    done --> done: done again
    open --> deleted: delete
    done --> deleted: delete
    deleted --> [*]
```

## 4. 模块建议

Agent 可以自行决定文件结构，但推荐至少包含：

- `todo_manager/__main__.py`：命令行入口。
- `todo_manager/cli.py`：参数解析和输出。
- `todo_manager/core.py`：业务逻辑。
- `todo_manager/storage.py`：JSON 文件读写。
