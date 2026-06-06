# 图书借阅管理系统 PRD

## 1. 产品概述

图书借阅管理系统是一款面向班级图书角、小型阅览室和团队共享书架的本地 Web 应用。管理员在本机启动服务后，用浏览器打开页面，完成图书入库、读者注册、借书、还书、库存查看和逾期查询。

本案例要求智能体从空 `workspace/` 开始实现完整 Python 项目。最终成品必须能在浏览器中运行，而不是只提供一次一个命令的 CLI。默认技术方案限定为 Python 标准库：使用 `http.server` 提供本地 HTTP 服务，使用 `sqlite3` 持久化数据，不使用 Flask、FastAPI、Django、Node.js、前端构建工具或第三方依赖。

## 2. 产品目标

- 让管理员通过浏览器页面完成主要借阅操作。
- 使用本地 SQLite 数据库保存图书、读者和借阅记录。
- 启动服务时自动初始化数据库表，文件不存在时自动创建。
- 页面使用普通 HTML 表单和链接，不依赖 JavaScript 框架。
- 支持标准 HTTP 请求驱动，便于真实浏览器演示，也便于隐藏 oracle 使用 `urllib` 自动测试。
- 保留原业务规则：固定 14 天借期、库存检查、重复借阅限制和逾期查询。

## 3. 默认启动方式

用户运行：

```bash
python -m library_lending --db library.db --host 127.0.0.1 --port 8000
```

程序应启动本地 Web 服务，并在 stdout 输出可访问地址，例如：

```text
Library Lending Manager running at http://127.0.0.1:8000/
```

随后用户在浏览器打开：

```text
http://127.0.0.1:8000/
```

首页必须包含标题 `图书借阅管理系统`，并提供进入图书入库、读者注册、借书、还书、库存列表和逾期查询的入口。

## 4. 用户角色

| 角色 | 目标 |
| --- | --- |
| 图书管理员 | 维护图书库存、注册读者、办理借书和还书 |
| 班级负责人 | 查看当前库存、未归还借阅和逾期情况 |
| 读者 | 通过管理员完成借阅和归还，不需要自助登录 |

## 5. 功能需求

### F-01 启动和数据库初始化

系统启动时应：

- 解析 `--db`、`--host`、`--port` 参数。
- 使用指定 SQLite 文件保存数据。
- 自动创建 `books`、`readers`、`loans` 等必要表。
- 若数据库文件不存在，应自动创建。
- 若端口为 `0`，应允许系统分配可用端口，便于自动化测试。

实现中还必须暴露：

```python
create_server(db_path, host="127.0.0.1", port=0)
```

该函数返回一个可调用 `serve_forever()`、`shutdown()`、`server_close()` 的标准库 HTTP server 实例。隐藏 oracle 会优先导入这个函数并用动态端口启动服务。

### F-02 首页

`GET /` 应返回 HTML 页面，页面至少包含：

- `图书借阅管理系统`
- 添加图书入口或表单
- 注册读者入口或表单
- 借书入口或表单
- 还书入口或表单
- 库存列表入口
- 逾期查询入口

页面可以朴素，但应能在浏览器中直接使用。不要只返回 JSON，也不要要求用户手写 HTTP 请求。

### F-03 添加图书

`POST /books` 使用表单字段：

| 字段 | 规则 |
| --- | --- |
| `isbn` | 必填，图书唯一编号 |
| `title` | 必填，书名 |
| `author` | 必填，作者 |
| `copies` | 必填，正整数 |

业务规则：

- ISBN 不存在时新增图书。
- ISBN 已存在时增加馆藏册数，并更新 title、author 为最新输入值。
- 成功响应正文必须包含稳定短语：`book <isbn> available copies: <available>`。

示例：

```text
book 978-1 available copies: 2
```

### F-04 注册读者

`POST /readers` 使用表单字段：

| 字段 | 规则 |
| --- | --- |
| `reader` | 必填，读者唯一编号 |
| `name` | 必填，读者姓名 |

重复注册同一 `reader` 应更新姓名，不创建重复读者。成功响应正文必须包含：

```text
reader r1 registered
```

### F-05 借书

`POST /loans/borrow` 使用表单字段：

| 字段 | 规则 |
| --- | --- |
| `reader` | 已注册读者编号 |
| `isbn` | 已入库图书编号 |
| `date` | 借出日期，格式 `YYYY-MM-DD` |

业务规则：

- 图书和读者必须存在。
- 同一读者不能同时借阅同一本未归还图书。
- 可借册数 = `copies - 当前未归还借阅数`。
- 可借册数为 0 时拒绝借阅。
- 应还日期 `due_at = borrowed_at + 14 天`。

成功响应正文必须包含：

```text
borrowed 978-1 by r1 due 2026-06-15
```

### F-06 还书

`POST /loans/return` 使用表单字段：

| 字段 | 规则 |
| --- | --- |
| `reader` | 读者编号 |
| `isbn` | 图书编号 |
| `date` | 归还日期，格式 `YYYY-MM-DD` |

系统只能归还当前未归还的借阅。归还成功后，`returned_at` 设置为指定日期，可借库存恢复。成功响应正文必须包含：

```text
returned 978-1 by r1
```

### F-07 查看库存

`GET /books` 返回 HTML 库存页，按 ISBN 升序显示全部图书。每本书必须包含稳定库存文本：

```text
<isbn> <title> by <author> copies <copies> available <available>
```

示例：

```text
978-1 Clean Code by Robert Martin copies 2 available 1
```

### F-08 查看逾期

`GET /overdue?date=2026-06-20` 返回 HTML 逾期页。系统显示所有未归还且 `due_at` 早于指定日期的借阅，每条记录包含：

```text
<reader_id> <isbn> due <due_at>
```

没有逾期时，响应正文必须包含：

```text
no overdue loans
```

## 6. 数据模型

### Book

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `isbn` | str | 图书唯一编号，非空 |
| `title` | str | 书名，非空 |
| `author` | str | 作者，非空 |
| `copies` | int | 馆藏册数，正整数 |

### Reader

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `reader_id` | str | 读者唯一编号，非空 |
| `name` | str | 读者姓名，非空 |

### Loan

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | int | 自动生成 |
| `isbn` | str | 图书编号 |
| `reader_id` | str | 读者编号 |
| `borrowed_at` | str | 借出日期，`YYYY-MM-DD` |
| `due_at` | str | 应还日期，借出日期后 14 天 |
| `returned_at` | str/null | 归还日期，未归还为空 |

## 7. 错误处理

表单级错误应返回 HTML 响应，响应正文必须包含稳定错误短语：

| 场景 | 稳定错误短语 |
| --- | --- |
| copies 不是正整数 | `invalid copies` |
| 日期格式错误 | `invalid date` |
| 图书不存在 | `book not found` |
| 读者不存在 | `reader not found` |
| 无可借副本 | `no available copies` |
| 同一读者重复借同一本未归还图书 | `already borrowed` |
| 没有可归还借阅 | `loan not found` |

推荐状态码：校验错误 400，不存在 404，业务冲突 409。oracle 主要检查 HTTP 响应正文中的稳定短语和错误状态。

## 8. 非目标

- 不做罚款计算。
- 不做预约排队。
- 不做读者自助登录。
- 不做多用户权限。
- 不要求复杂视觉设计、前端框架或 JavaScript 单页应用。
- 不要求 JSON API；可以额外提供，但不能替代 HTML 页面和表单。
