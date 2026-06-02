# 图书借阅管理系统需求说明

## 1. 项目背景

开发一个小型图书借阅管理 CLI，供班级图书角或小型阅览室管理图书库存、读者注册、借书、还书和逾期查询。

Agent 必须从空 `workspace/` 开始创建完整 Python 项目。

## 2. 技术约束

- 项目语言：Python 3.11+。
- 项目形态：CLI 工具。
- 入口命令：`python -m library_lending`。
- 持久化方式：SQLite。
- 仅允许使用 Python 标准库。
- 数据库文件通过 `--db library.db` 指定。

## 3. 核心实体

### 3.1 图书 Book

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `isbn` | str | 图书唯一编号，非空 |
| `title` | str | 书名，非空 |
| `author` | str | 作者，非空 |
| `copies` | int | 馆藏册数，正整数 |

### 3.2 读者 Reader

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `reader_id` | str | 读者唯一编号，非空 |
| `name` | str | 读者姓名，非空 |

### 3.3 借阅 Loan

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | int | 自动生成 |
| `isbn` | str | 图书编号 |
| `reader_id` | str | 读者编号 |
| `borrowed_at` | str | 借出日期，`YYYY-MM-DD` |
| `due_at` | str | 应还日期，借出日期后 14 天 |
| `returned_at` | str/null | 归还日期，未归还为空 |

## 4. 命令行接口

所有命令格式：

```bash
python -m library_lending --db library.db <command> [options]
```

### 4.1 初始化数据库

```bash
python -m library_lending --db library.db init
```

创建所需表。重复执行不应报错。

输出：

```text
database ready
```

### 4.2 添加图书

```bash
python -m library_lending --db library.db add-book --isbn 978-1 --title "Clean Code" --author "Robert Martin" --copies 2
```

规则：

- ISBN 不存在时新增图书。
- ISBN 已存在时，增加 copies，并更新 title/author 为最新输入值。

输出：

```text
book 978-1 available copies: 2
```

### 4.3 注册读者

```bash
python -m library_lending --db library.db add-reader --reader r1 --name "Ada"
```

输出：

```text
reader r1 registered
```

重复注册同一 reader_id 应更新姓名，不创建重复读者。

### 4.4 借书

```bash
python -m library_lending --db library.db borrow --reader r1 --isbn 978-1 --date 2026-06-01
```

规则：

- 图书和读者必须存在。
- 同一读者不能同时借阅同一本未归还图书。
- 可借册数 = copies - 当前未归还借阅数。
- 可借册数为 0 时拒绝借阅。
- due_at = borrowed_at + 14 天。

输出：

```text
borrowed 978-1 by r1 due 2026-06-15
```

### 4.5 还书

```bash
python -m library_lending --db library.db return --reader r1 --isbn 978-1 --date 2026-06-05
```

规则：

- 只能归还当前未归还的借阅。
- 归还后 returned_at 设置为指定日期。

输出：

```text
returned 978-1 by r1
```

### 4.6 查看库存

```bash
python -m library_lending --db library.db books
```

每行格式：

```text
<isbn> <title> by <author> copies <copies> available <available>
```

按 ISBN 升序。

### 4.7 查看逾期

```bash
python -m library_lending --db library.db overdue --date 2026-06-20
```

输出所有未归还且 due_at 早于指定日期的借阅，每行：

```text
<reader_id> <isbn> due <due_at>
```

没有逾期时输出 `no overdue loans`。

## 5. 异常处理

失败时退出码非 0，stderr 包含以下关键词：

- 图书不存在：`book not found`
- 读者不存在：`reader not found`
- 无可借副本：`no available copies`
- 重复借同一本未还图书：`already borrowed`
- 没有可归还借阅：`loan not found`
- 日期格式错误：`invalid date`
- copies 非正整数：`invalid copies`
