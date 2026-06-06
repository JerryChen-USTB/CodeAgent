# 个人记账系统验收标准

## AC-01 默认入口启动 TUI

给定空 `workspace/` 中已经由 Agent 实现 `personal_ledger` 包，当执行：

```bash
python -m personal_ledger --file ledger.json
```

则：

- 程序进入交互式文本菜单，而不是要求用户继续输入子命令参数。
- stdout 包含 `个人记账系统`。
- stdout 能看到新增账目、查询流水、统计汇总、编辑账目、删除账目和保存退出六类动作。
- 输入 `6`、`q`、`quit` 或 `exit` 后，程序退出码为 0。
- stdin 到达 EOF 时程序不会无限挂起。

## AC-02 一个会话内新增收入和支出

给定不存在的账本文件，当通过 stdin 输入以下内容：

```text
1
2026-06-01
income
salary
5000
monthly salary
1
2026-06-02
expense
food
23.5
lunch
6
```

则：

- 退出码为 0。
- stdout 包含 `已新增账目 #1: income salary 5000.00`。
- stdout 包含 `已新增账目 #2: expense food 23.50`。
- JSON 文件中保存两条记录。
- 第一条记录的 `type` 为 `income`，`amount` 为 `"5000.00"`。
- 第二条记录的 `type` 为 `expense`，`amount` 为 `"23.50"`。

## AC-03 查询流水和筛选

给定账本中存在以下记录：

- `#1 2026-06-01 income salary 5000.00 monthly salary`
- `#2 2026-06-02 expense food 23.50 lunch`
- `#3 2026-06-03 expense transport 6.75 bus`
- `#4 2026-05-20 expense food 10.00 snack`

当启动程序并在“查询流水”中输入月份 `2026-06`，类型和分类留空时：

- stdout 包含 `#1 2026-06-01 income salary 5000.00 monthly salary`。
- stdout 包含 `#2 2026-06-02 expense food 23.50 lunch`。
- stdout 包含 `#3 2026-06-03 expense transport 6.75 bus`。
- 2026 年 6 月查询结果不应包含 2026 年 5 月记录。
- 输出按日期升序、同日按 ID 升序。

当筛选条件没有匹配记录时：

- stdout 包含 `暂无账目`。

## AC-04 月度统计和余额计算

给定 2026-06 有收入 `5000.00`，支出 `food 23.50` 和 `transport 6.75`，当在“统计汇总”中输入 `2026-06` 时：

- stdout 包含 `收入合计: 5000.00`。
- stdout 包含 `支出合计: 30.25`。
- stdout 包含 `余额: 4969.75`。
- stdout 包含 `food: 23.50`。
- stdout 包含 `transport: 6.75`。
- 分类汇总只统计支出分类，不统计 `salary`。

## AC-05 编辑账目并保存

给定账本中存在 `#1 2026-06-02 expense food 12.00 lunch`，当启动程序并选择“编辑账目”，输入 ID `1`，将分类改为 `groceries`，金额改为 `88.8`，备注改为 `weekly groceries` 时：

- stdout 包含 `已更新账目 #1: expense groceries 88.80`。
- 后续查询流水包含 `#1 2026-06-02 expense groceries 88.80 weekly groceries`。
- 程序退出后，JSON 文件中的记录也已更新。
- 重新启动程序加载同一个文件后，仍能看到更新后的记录。

## AC-06 删除账目并持久化

给定账本中存在 ID 为 3 的账目，当启动程序并选择“删除账目”，输入 ID `3`，确认输入 `y` 时：

- stdout 包含 `已删除账目 #3`。
- 后续查询流水不再显示 ID 为 3 的记录。
- 程序退出后，JSON 文件中不再包含 ID 为 3 的记录。
- 重新启动程序加载同一个文件后，ID 为 3 的记录不应恢复。

## AC-07 数据保存与重新加载

给定用户在一次会话中新增、编辑或删除账目：

- 每次成功操作后都应立即保存 JSON 文件。
- 再次执行 `python -m personal_ledger --file ledger.json` 时，应加载上次保存的数据。
- 文件不存在时视为空账本。
- 新 ID 使用当前最大 ID 加一，不复用已删除 ID。
- 金额在 JSON 中必须保存为两位小数字符串，而不是浮点数。

## AC-08 非法输入和错误恢复

以下场景必须被拒绝，且程序应显示错误后回到主菜单继续运行：

- 新增时日期为 `2026/06/01`，stdout 包含 `invalid date`。
- 统计时月份为 `2026-6`，stdout 包含 `invalid month`。
- 新增或编辑时金额为 `0`、负数或 `abc`，stdout 包含 `invalid amount`。
- 新增或编辑时类型为 `bonus`，stdout 包含 `invalid type`。
- 新增时日期、类型、分类或金额为空，stdout 包含 `required field missing`。
- 编辑或删除不存在的 ID，stdout 包含 `record not found`。
- 输入未知菜单选项，stdout 包含 `unknown option`。

这些表单级错误不应导致程序崩溃，退出码仍应由最终用户退出动作决定。

## AC-09 损坏账本文件处理

给定 `ledger.json` 已存在但内容不是合法 JSON，当执行：

```bash
python -m personal_ledger --file ledger.json
```

则：

- 程序退出码非 0。
- stderr 包含 `invalid ledger file`。
- stdout 不应显示主菜单。
- 程序不应覆盖原始损坏文件。

## AC-10 交互入口存在

oracle 测试会通过类似以下方式驱动程序：

```python
subprocess.run(
    [sys.executable, "-m", "personal_ledger", "--file", "ledger.json"],
    input="1\n2026-06-01\nincome\nsalary\n5000\nmonthly salary\n6\n",
    text=True,
    capture_output=True,
)
```

因此实现必须满足：

- `python -m personal_ledger` 可以作为模块入口运行。
- 默认入口是 TUI 菜单，而不是只打印帮助文本。
- TUI 使用普通 stdin/stdout 交互，不依赖鼠标、全屏终端或外部服务。
