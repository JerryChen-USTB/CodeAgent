# 待办事项管理系统设计模型

## 1. 设计目标

待办事项管理系统应实现为一个小型分层 Python 应用。交互式文本 TUI 是主要产品界面，但 TUI 不应直接承担所有持久化和业务规则。清晰分层可以让系统更容易测试、调试和修复：

- UI 层负责显示菜单、读取用户输入、打印用户可见消息。
- 应用服务层负责校验操作、协调任务增删改查。
- 仓储层负责 JSON 文件读取和保存。
- 领域模型负责表示任务数据和任务状态。

实现时可以使用不同文件名，但这些职责应在代码中保持清晰。

## 2. 推荐包结构

```text
workspace/
└── todo_manager/
    ├── __init__.py
    ├── __main__.py        # python -m todo_manager 入口
    ├── app.py             # 启动装配和退出码处理
    ├── tui.py             # 交互式菜单循环和输入提示
    ├── service.py         # 任务业务操作和校验
    ├── storage.py         # JSON 读写
    └── models.py          # Task 数据类或等价领域对象
```

这是推荐结构，不是强制结构。若实现规模较小，也可以合并部分文件，但必须满足 PRD 中的行为要求。

## 3. 系统结构图

```mermaid
flowchart TD
    User["终端用户"] --> Entry["__main__.py"]
    Entry --> App["应用启动"]
    App --> Repo["TaskRepository"]
    App --> TUI["InteractiveTodoTui"]
    TUI --> Service["TodoService"]
    Service --> Repo
    Repo --> JsonFile["UTF-8 JSON 任务文件"]
    Service --> Task["Task 领域对象"]
    TUI --> Stdout["stdout 普通文本菜单/结果"]
    TUI --> Stderr["stderr 启动级错误"]
```

启动流程应先根据 `--file` 创建仓储对象，读取已有任务，然后进入 TUI 循环。若出现非法 JSON 等启动级错误，应在进入菜单前报告并退出。

## 4. 领域模型

### 4.1 Task

每个任务包含以下字段：

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `id` | int | 正整数，在当前任务文件内唯一 |
| `title` | str | 去除首尾空白后不能为空 |
| `status` | str | 只能是 `open` 或 `done` |
| `priority` | str | 只能是 `low`、`normal`、`high` |
| `due` | str 或 null | `YYYY-MM-DD` 或 null |

### 4.2 任务状态机

```mermaid
stateDiagram-v2
    [*] --> open: 创建任务
    open --> done: 标记完成
    done --> done: 再次标记完成
    open --> deleted: 删除任务
    done --> deleted: 删除任务
    deleted --> [*]
```

删除任务时不需要在 JSON 中保留 deleted 状态，直接从任务数组中移除即可。

## 5. 类图

```mermaid
classDiagram
    class Task {
      +int id
      +str title
      +str status
      +str priority
      +str? due
      +to_dict() dict
      +from_dict(data) Task
    }

    class TaskRepository {
      -Path path
      +load() list~Task~
      +save(tasks) None
      +next_id(tasks) int
    }

    class TodoService {
      -TaskRepository repository
      +add_task(title, due, priority) Task
      +list_tasks(status_filter) list~Task~
      +mark_done(task_id) Task
      +delete_task(task_id) Task
    }

    class InputValidator {
      +validate_title(value) str
      +validate_due(value) str?
      +validate_priority(value) str
      +validate_status_filter(value) str
      +parse_task_id(value) int
    }

    class InteractiveTodoTui {
      -TodoService service
      -TextIO input
      -TextIO output
      +run() int
      -render_menu() None
      -handle_add() None
      -handle_list() None
      -handle_done() None
      -handle_delete() None
    }

    class App {
      +main(argv) int
    }

    App --> TaskRepository
    App --> TodoService
    App --> InteractiveTodoTui
    TodoService --> TaskRepository
    TodoService --> InputValidator
    TaskRepository --> Task
    InteractiveTodoTui --> TodoService
```

UI 层可以捕获可恢复校验错误并输出错误信息；启动级致命错误应返回非 0 退出码。

## 6. TUI 会话状态图

```mermaid
stateDiagram-v2
    [*] --> Starting
    Starting --> FatalError: 任务文件 JSON 非法
    Starting --> ShowingMenu: 数据加载成功
    ShowingMenu --> AddingTask: 选择 1
    ShowingMenu --> ListingTasks: 选择 2
    ShowingMenu --> CompletingTask: 选择 3
    ShowingMenu --> DeletingTask: 选择 4
    ShowingMenu --> Exiting: 选择 5/q/quit/exit
    ShowingMenu --> ShowingMenu: 未知选项
    AddingTask --> ShowingMenu: 成功或校验错误
    ListingTasks --> ShowingMenu: 成功或校验错误
    CompletingTask --> ShowingMenu: 成功或 task not found
    DeletingTask --> ShowingMenu: 成功或 task not found
    Exiting --> [*]
    FatalError --> [*]
```

TUI 菜单循环应是面向行输入的：

1. 打印菜单。
2. 读取一行输入。
3. 分发到一个动作。
4. 除退出或 EOF 外，回到菜单。

## 7. 启动流程

```mermaid
flowchart TD
    A["python -m todo_manager --file tasks.json"] --> B["argparse 解析 --file"]
    B --> C["TaskRepository.load()"]
    C --> D{"文件是否存在?"}
    D -- "否" --> E["使用空任务列表"]
    D -- "是" --> F{"是否为合法 JSON 任务数组?"}
    F -- "否" --> G["stderr 输出 invalid task file; 非 0 退出"]
    F -- "是" --> H["转换为 Task 对象"]
    E --> I["启动 InteractiveTodoTui.run()"]
    H --> I
    I --> J["使用 TUI 返回码退出"]
```

## 8. 添加任务流程

```mermaid
flowchart TD
    A["用户选择添加"] --> B["提示输入标题"]
    B --> C{"标题去空白后非空?"}
    C -- "否" --> X["输出 title is required; 返回菜单"]
    C -- "是" --> D["提示输入截止日期"]
    D --> E{"空值或 YYYY-MM-DD?"}
    E -- "否" --> Y["输出 invalid due date; 返回菜单"]
    E -- "是" --> F["提示输入优先级"]
    F --> G{"空值/low/normal/high?"}
    G -- "否" --> Z["输出 invalid priority; 返回菜单"]
    G -- "是" --> H["TodoService.add_task"]
    H --> I["Repository.save"]
    I --> J["输出 created task #id: title"]
    J --> K["返回菜单"]
```

校验应发生在写文件前。添加失败时不得创建半成品任务。

## 9. 查看任务流程

```mermaid
flowchart TD
    A["用户选择查看"] --> B["提示输入状态过滤条件"]
    B --> C{"过滤条件是否合法?"}
    C -- "否" --> D["输出 invalid status; 返回菜单"]
    C -- "是" --> E["TodoService.list_tasks"]
    E --> F{"是否有匹配任务?"}
    F -- "否" --> G["输出 no tasks"]
    F -- "是" --> H["按稳定格式逐行输出任务"]
    G --> I["返回菜单"]
    H --> I
```

查看操作不应修改 JSON 文件。

## 10. 完成和删除流程

```mermaid
flowchart TD
    A["用户选择完成或删除"] --> B["提示输入任务 ID"]
    B --> C{"ID 可解析且任务存在?"}
    C -- "否" --> D["输出 task not found; 返回菜单"]
    C -- "是，完成" --> E["设置 status=done"]
    C -- "是，删除" --> F["移除任务"]
    E --> G["Repository.save"]
    F --> G
    G --> H["输出确认信息"]
    H --> I["返回菜单"]
```

完成和删除操作成功后都应立即保存。

## 11. 持久化设计

仓储层职责：

- 解析配置的数据文件路径。
- 文件不存在时返回空列表。
- 读取 UTF-8 文本并解析 JSON。
- 校验顶层 JSON 是任务对象数组。
- 将字典转换为 `Task` 对象。
- 使用两个空格缩进和 `ensure_ascii=False` 保存任务数组。

推荐实现细节：

- 使用 `json.loads` 和 `json.dumps`。
- 使用 `datetime.date.fromisoformat` 校验截止日期。
- 保存时先写入同目录临时文件，再用 `Path.replace` 替换目标文件。
- 输出和保存任务时按 ID 升序排序。

## 12. 错误处理模型

```mermaid
flowchart LR
    Error["错误条件"] --> Recoverable{"是否为会话内可恢复错误?"}
    Recoverable -- "是" --> Message["输出指定错误关键词"]
    Message --> Menu["返回主菜单"]
    Recoverable -- "否" --> Stderr["stderr 输出指定错误关键词"]
    Stderr --> Exit["非 0 退出"]
```

可恢复错误包括非法标题、日期、优先级、过滤条件、任务 ID 和未知菜单选项。致命错误包括启动时读取到非法 JSON 文件。

## 13. 可测试性要求

隐藏 oracle 会将程序作为 subprocess 启动，把生成的 `workspace` 加入 `PYTHONPATH`，通过 stdin 发送菜单选项，并检查 stdout、stderr 和 JSON 文件。因此：

- 不要依赖无法在 `subprocess.run(input=...)` 中工作的交互特性。
- EOF 后不要无限等待输入。
- 用户提示和结果应写入 stdout。
- 不需要外部依赖。
- 输出消息应稳定，便于断言。
