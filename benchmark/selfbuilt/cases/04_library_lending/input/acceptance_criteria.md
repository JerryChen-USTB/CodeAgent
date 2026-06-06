# 图书借阅管理系统验收标准

## AC-01 本地 Web 服务启动

给定 Agent 已在空 `workspace/` 中实现 `library_lending` 包，当执行：

```bash
python -m library_lending --db library.db --host 127.0.0.1 --port 8000
```

则：

- 程序启动本地 HTTP 服务。
- 浏览器访问 `http://127.0.0.1:8000/` 能看到 HTML 首页。
- 首页包含 `图书借阅管理系统`。
- 首页能看到添加图书、注册读者、借书、还书、库存和逾期查询入口。
- 实现暴露 `create_server(db_path, host="127.0.0.1", port=0)`，便于 oracle 使用动态端口测试。

## AC-02 添加图书并查看库存

当通过 `POST /books` 提交：

| 字段 | 值 |
| --- | --- |
| `isbn` | `978-1` |
| `title` | `Clean Code` |
| `author` | `Robert Martin` |
| `copies` | `2` |

则：

- HTTP 响应正文包含 `book 978-1 available copies: 2`。
- `GET /books` 响应正文包含 `978-1 Clean Code by Robert Martin copies 2 available 2`。
- 如果同一 ISBN 再入库 1 册，总册数和可借册数应增加。

## AC-03 注册读者

当通过 `POST /readers` 提交：

| 字段 | 值 |
| --- | --- |
| `reader` | `r1` |
| `name` | `Ada` |

则：

- HTTP 响应正文包含 `reader r1 registered`。
- 重复提交同一 `reader` 应更新姓名，不创建重复读者。

## AC-04 借书和库存变化

给定已有图书 `978-1` 共 2 册，读者 `r1` 已注册，当通过 `POST /loans/borrow` 提交：

| 字段 | 值 |
| --- | --- |
| `reader` | `r1` |
| `isbn` | `978-1` |
| `date` | `2026-06-01` |

则：

- 响应正文包含 `borrowed 978-1 by r1 due 2026-06-15`。
- `GET /books` 响应正文包含 `978-1 Clean Code by Robert Martin copies 2 available 1`。
- 同一读者再次借同一本未归还图书应失败，响应正文包含 `already borrowed`。

## AC-05 逾期查询

给定 `r1` 在 `2026-06-01` 借出 `978-1` 且尚未归还：

- `GET /overdue?date=2026-06-14` 不应显示该借阅为逾期。
- `GET /overdue?date=2026-06-20` 响应正文包含 `r1 978-1 due 2026-06-15`。

## AC-06 还书并恢复库存

当通过 `POST /loans/return` 提交：

| 字段 | 值 |
| --- | --- |
| `reader` | `r1` |
| `isbn` | `978-1` |
| `date` | `2026-06-05` |

则：

- 响应正文包含 `returned 978-1 by r1`。
- `GET /books` 显示可借册数恢复。
- 再次查询 `GET /overdue?date=2026-06-20` 时，响应正文包含 `no overdue loans`。

## AC-07 SQLite 持久化

给定使用同一个 `library.db`：

- 第一次启动服务添加图书、读者并完成借阅操作后关闭服务。
- 第二次启动服务仍能从 SQLite 读取之前保存的数据。
- 库存、未归还借阅和逾期查询结果应与关闭前一致。

## AC-08 错误处理

以下错误场景必须返回错误 HTTP 响应，且响应正文包含稳定错误短语：

| 场景 | 稳定错误短语 |
| --- | --- |
| `copies` 为 `0`、负数或非数字 | `invalid copies` |
| 日期为 `2026/06/01` | `invalid date` |
| 未注册读者借书 | `reader not found` |
| 不存在图书借书 | `book not found` |
| 无可借副本 | `no available copies` |
| 同一读者重复借同一本未还图书 | `already borrowed` |
| 归还不存在的未还借阅 | `loan not found` |

这些错误必须显示在 HTML 响应正文中，不能只写入终端日志。

## AC-09 技术约束

- 只使用 Python 标准库。
- 不创建 `requirements.txt` 来引入 Flask、FastAPI、Django 或前端构建依赖。
- 页面可以朴素，但必须能在浏览器中实际操作。
- 隐藏 oracle 会用 HTTP 请求提交表单并检查 HTML 和 SQLite 行为。
