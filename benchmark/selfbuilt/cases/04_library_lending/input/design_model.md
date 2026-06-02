# 图书借阅管理系统设计模型

## 1. 类图

```mermaid
classDiagram
    class Book {
      +str isbn
      +str title
      +str author
      +int copies
    }

    class Reader {
      +str reader_id
      +str name
    }

    class Loan {
      +int id
      +str isbn
      +str reader_id
      +date borrowed_at
      +date due_at
      +date? returned_at
    }

    class LibraryRepository {
      +init_db() None
      +upsert_book(book) None
      +upsert_reader(reader) None
      +create_loan(loan) None
      +close_loan(reader_id, isbn, returned_at) None
      +list_books() list~BookStatus~
      +list_overdue(date) list~Loan~
    }

    class LibraryService {
      +add_book(...)
      +add_reader(...)
      +borrow(...)
      +return_book(...)
      +books()
      +overdue(date)
    }

    Book "1" --> "*" Loan
    Reader "1" --> "*" Loan
    LibraryService --> LibraryRepository
```

## 2. 借书流程

```mermaid
flowchart TD
    A["执行 borrow 命令"] --> B["校验日期"]
    B --> C["查询读者"]
    C --> D{"读者存在?"}
    D -- "否" --> E["reader not found"]
    D -- "是" --> F["查询图书"]
    F --> G{"图书存在?"}
    G -- "否" --> H["book not found"]
    G -- "是" --> I["检查是否已借同书未还"]
    I --> J{"重复借阅?"}
    J -- "是" --> K["already borrowed"]
    J -- "否" --> L["计算 available"]
    L --> M{"available > 0?"}
    M -- "否" --> N["no available copies"]
    M -- "是" --> O["创建 Loan, due_at=borrowed_at+14"]
    O --> P["输出 borrowed ... due ..."]
```

## 3. 借阅状态机

```mermaid
stateDiagram-v2
    [*] --> active: borrow
    active --> returned: return
    active --> overdue: current_date > due_at
    overdue --> returned: return
    returned --> [*]
```

## 4. 模块建议

- `library_lending/__main__.py`：CLI 入口。
- `library_lending/cli.py`：命令行解析和输出。
- `library_lending/service.py`：业务规则。
- `library_lending/repository.py`：SQLite 访问。
