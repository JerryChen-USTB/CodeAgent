# 个人记账系统设计模型

## 1. 类图

```mermaid
classDiagram
    class LedgerRecord {
      +int id
      +str date
      +str type
      +str category
      +str amount
      +str note
    }

    class LedgerRepository {
      +load() list~LedgerRecord~
      +save(records) None
      +next_id(records) int
    }

    class LedgerService {
      +add_record(date, type, category, amount, note) LedgerRecord
      +list_records(month) list~LedgerRecord~
      +summarize(month) Summary
      +export_csv(output_path) None
    }

    class CliController {
      +main(argv) int
    }

    LedgerRepository --> LedgerRecord
    LedgerService --> LedgerRepository
    CliController --> LedgerService
```

## 2. 月度汇总流程

```mermaid
flowchart TD
    A["用户执行 summary --month"] --> B["校验月份格式 YYYY-MM"]
    B --> C["读取 ledger.json"]
    C --> D["筛选指定月份记录"]
    D --> E["分别累加 income 和 expense"]
    E --> F["按支出 category 分组汇总"]
    F --> G["计算 balance"]
    G --> H["按固定格式输出"]
```

## 3. 数据流

```mermaid
flowchart LR
    CLI["CLI 命令"] --> Service["LedgerService"]
    Service --> Repo["LedgerRepository"]
    Repo --> Json["ledger.json"]
    Service --> Csv["ledger.csv"]
```

## 4. 模块建议

- `personal_ledger/__main__.py`：命令行入口。
- `personal_ledger/cli.py`：参数解析。
- `personal_ledger/core.py`：业务逻辑和金额计算。
- `personal_ledger/storage.py`：JSON/CSV 文件处理。
