# 个人记账系统设计模型

## 1. 设计目标

个人记账系统应实现为一个小型分层 Python 应用。交互式文本 TUI 是主要产品界面，但 TUI 不应直接承担金额计算、数据校验和 JSON 持久化。清晰分层可以让系统更容易测试、调试和修复：

- UI 层负责显示菜单、读取表单输入、打印用户可见消息。
- 应用服务层负责新增、查询、统计、编辑、删除等业务操作。
- 仓储层负责 JSON 文件读取、保存和损坏文件识别。
- 领域模型负责表示账目记录、金额、类型和校验结果。

实现时可以使用不同文件名，但这些职责应在代码中保持清晰。默认入口必须启动交互式 TUI。

## 2. 推荐包结构

```text
workspace/
└── personal_ledger/
    ├── __init__.py
    ├── __main__.py        # python -m personal_ledger 入口
    ├── app.py             # 参数解析、启动装配、退出码处理
    ├── tui.py             # 菜单循环、表单提示、stdout 文本输出
    ├── service.py         # 记账业务逻辑、统计、编辑、删除
    ├── storage.py         # JSON 读取、保存、文件损坏处理
    └── models.py          # LedgerRecord、Summary 等领域对象
```

这是推荐结构，不是强制结构。若实现规模较小，也可以合并部分文件，但必须满足 PRD 中的行为要求。

## 3. 系统结构图

```mermaid
flowchart TD
    User["终端用户"] --> Entry["__main__.py"]
    Entry --> App["应用启动与参数解析"]
    App --> Repo["LedgerRepository"]
    App --> TUI["InteractiveLedgerTui"]
    TUI --> Service["LedgerService"]
    Service --> Repo
    Repo --> JsonFile["UTF-8 JSON 账本文件"]
    Service --> Record["LedgerRecord 领域对象"]
    Service --> Summary["MonthlySummary 汇总结果"]
    TUI --> Stdout["stdout 菜单/表单/结果"]
    App --> Stderr["stderr 启动级错误"]
```

启动流程应先根据 `--file` 创建仓储对象并读取已有账目，再进入 TUI 循环。若 JSON 文件损坏，应在进入菜单前向 stderr 报告 `invalid ledger file` 并返回非 0。

## 4. 领域模型

### 4.1 LedgerRecord

每条账目记录包含以下字段：

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `id` | int | 正整数，在当前账本文件内唯一 |
| `date` | str | 合法日期，格式 `YYYY-MM-DD` |
| `type` | str | 只能是 `income` 或 `expense` |
| `category` | str | 去除首尾空白后不能为空 |
| `amount` | str | 大于 0 的两位小数字符串 |
| `note` | str | 可为空字符串 |

金额建议使用 `decimal.Decimal` 解析、量化和累加，避免二进制浮点误差。保存到 JSON 前应统一格式化为两位小数字符串。

### 4.2 MonthlySummary

月度汇总结果可以表示为：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `month` | str | 统计月份，格式 `YYYY-MM` |
| `income_total` | Decimal | 当月收入合计 |
| `expense_total` | Decimal | 当月支出合计 |
| `balance` | Decimal | 收入合计减支出合计 |
| `expense_by_category` | dict[str, Decimal] | 支出分类汇总 |

分类汇总只统计 `expense` 记录，输出时按分类名升序排列。

## 5. 类图

```mermaid
classDiagram
    class LedgerRecord {
      +int id
      +str date
      +str type
      +str category
      +str amount
      +str note
      +to_dict() dict
      +from_dict(data) LedgerRecord
    }

    class MonthlySummary {
      +str month
      +Decimal income_total
      +Decimal expense_total
      +Decimal balance
      +dict expense_by_category
    }

    class LedgerRepository {
      -Path path
      +load() list~LedgerRecord~
      +save(records) None
      +next_id(records) int
    }

    class LedgerService {
      -LedgerRepository repository
      +add_record(date, type, category, amount, note) LedgerRecord
      +list_records(month, type_filter, category_filter) list~LedgerRecord~
      +summarize(month) MonthlySummary
      +update_record(id, fields) LedgerRecord
      +delete_record(id) LedgerRecord
    }

    class InteractiveLedgerTui {
      -LedgerService service
      +run() int
      +show_menu() None
      +handle_add() None
      +handle_list() None
      +handle_summary() None
      +handle_edit() None
      +handle_delete() None
    }

    LedgerRepository --> LedgerRecord
    LedgerService --> LedgerRepository
    LedgerService --> MonthlySummary
    InteractiveLedgerTui --> LedgerService
```

## 6. TUI 状态流

```mermaid
stateDiagram-v2
    [*] --> LoadLedger: 启动并读取 --file
    LoadLedger --> Menu: 账本正常或文件不存在
    LoadLedger --> StartupError: JSON 损坏
    StartupError --> [*]: stderr 输出 invalid ledger file

    Menu --> AddForm: 选择 1
    AddForm --> Menu: 新增成功并保存
    AddForm --> Menu: 校验失败并显示错误

    Menu --> ListForm: 选择 2
    ListForm --> Menu: 输出流水或暂无账目

    Menu --> SummaryForm: 选择 3
    SummaryForm --> Menu: 输出月度汇总
    SummaryForm --> Menu: 月份非法并显示错误

    Menu --> EditForm: 选择 4
    EditForm --> Menu: 更新成功并保存
    EditForm --> Menu: ID 不存在或校验失败

    Menu --> DeleteConfirm: 选择 5
    DeleteConfirm --> Menu: 删除成功并保存
    DeleteConfirm --> Menu: 用户取消或 ID 不存在

    Menu --> Exit: 选择 6/q/quit/exit 或 EOF
    Exit --> [*]: 保存并返回 0
```

## 7. 核心流程图

### 7.1 新增账目流程

```mermaid
flowchart TD
    A["用户选择 1 新增账目"] --> B["读取日期、类型、分类、金额、备注"]
    B --> C["校验必填字段"]
    C -->|失败| E["stdout 输出 required field missing"]
    C -->|通过| D["校验日期、类型和金额"]
    D -->|失败| F["stdout 输出稳定错误短语"]
    D -->|通过| G["生成 max(id)+1"]
    G --> H["金额格式化为两位小数字符串"]
    H --> I["追加到记录列表"]
    I --> J["保存 JSON 文件"]
    J --> K["stdout 输出 已新增账目"]
    E --> M["回到主菜单"]
    F --> M
    K --> M
```

### 7.2 查询流水流程

```mermaid
flowchart TD
    A["用户选择 2 查询流水"] --> B["读取月份、类型、分类筛选"]
    B --> C["月份为空或合法 YYYY-MM"]
    C -->|失败| D["stdout 输出 invalid month"]
    C -->|通过| E["按筛选条件过滤记录"]
    E --> F["按 date 升序、同日按 id 升序排序"]
    F --> G{"是否有结果"}
    G -->|否| H["输出 暂无账目"]
    G -->|是| I["逐行输出 #id date type category amount note"]
    D --> J["回到主菜单"]
    H --> J
    I --> J
```

### 7.3 月度统计流程

```mermaid
flowchart TD
    A["用户选择 3 统计汇总"] --> B["读取必填月份 YYYY-MM"]
    B --> C["校验月份"]
    C -->|失败| D["stdout 输出 invalid month"]
    C -->|通过| E["筛选指定月份记录"]
    E --> F["累加 income 和 expense"]
    F --> G["balance = income - expense"]
    G --> H["按支出 category 分组"]
    H --> I["分类名升序"]
    I --> J["输出收入、支出、余额、分类汇总"]
    D --> K["回到主菜单"]
    J --> K
```

## 8. 数据流

```mermaid
flowchart LR
    Input["stdin 菜单和表单输入"] --> TUI["InteractiveLedgerTui"]
    TUI --> Service["LedgerService"]
    Service --> Repo["LedgerRepository"]
    Repo --> Json["ledger.json"]
    Repo --> Service
    Service --> TUI
    TUI --> Output["stdout 用户可见结果"]
    App["启动与文件读取"] --> Error["stderr 启动级错误"]
```

所有正常业务错误都回到 stdout，方便用户继续操作；只有账本文件损坏这类启动级错误写入 stderr 并终止。

## 9. 校验规则建议

- 日期校验：使用 `datetime.date.fromisoformat` 或等价方式，确保 `2026-02-30` 这类不存在的日期会失败。
- 月份校验：使用正则或手动校验 `YYYY-MM`，月份必须为 `01` 到 `12`，`2026-6` 不合法。
- 金额校验：使用 `Decimal`，要求大于 0，最终 `quantize(Decimal("0.01"))`。
- 类型校验：必须支持 `income` 和 `expense`。
- 必填字段校验：新增时日期、类型、分类、金额均不能为空；编辑时只校验用户实际填写的新值。
- ID 校验：编辑和删除应先确认 ID 存在，不存在时输出 `record not found`。

## 10. 文件格式和兼容性

JSON 文件应保持简单、可读、可排查：

```json
[
  {
    "id": 1,
    "date": "2026-06-01",
    "type": "income",
    "category": "salary",
    "amount": "5000.00",
    "note": "monthly salary"
  }
]
```

保存时建议使用稳定字段顺序：`id`、`date`、`type`、`category`、`amount`、`note`。读取时如果发现顶层不是数组、记录缺字段、字段类型明显错误或金额无法解析，应视为 `invalid ledger file`，不要覆盖原文件。
