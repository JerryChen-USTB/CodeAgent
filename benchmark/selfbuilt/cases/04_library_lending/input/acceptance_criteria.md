# 图书借阅管理系统验收标准

## AC-01 初始化和入库

执行：

```bash
python -m library_lending --db library.db init
python -m library_lending --db library.db add-book --isbn 978-1 --title "Clean Code" --author "Robert Martin" --copies 2
```

则：

- init 输出 `database ready`。
- add-book 输出 `book 978-1 available copies: 2`。
- books 显示 copies 2 和 available 2。

## AC-02 借书和库存变化

注册读者后借出一本书：

- borrow 输出 due 日期为借出日期后 14 天。
- books 中 available 从 2 变为 1。
- 同一读者再次借同一本未归还图书失败，stderr 包含 `already borrowed`。

## AC-03 还书

执行 return 后：

- stdout 包含 `returned 978-1 by r1`。
- books 中 available 恢复。
- 再次 return 同一本书失败，stderr 包含 `loan not found`。

## AC-04 逾期查询

给定借出日期为 2026-06-01，则 due_at 为 2026-06-15：

- `overdue --date 2026-06-14` 不显示该借阅。
- `overdue --date 2026-06-20` 显示 `r1 978-1 due 2026-06-15`。

## AC-05 异常输入

以下情况必须失败并返回非 0：

- 未注册读者借书。
- 不存在图书借书。
- 无可借副本借书。
- 日期格式错误。
- copies 为 0 或负数。
