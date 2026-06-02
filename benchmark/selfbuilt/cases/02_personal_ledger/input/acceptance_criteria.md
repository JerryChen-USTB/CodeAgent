# 个人记账系统验收标准

## AC-01 添加记录

执行添加支出命令后：

- 退出码为 0。
- stdout 包含 `added record #1: expense food 23.50`。
- JSON 文件中保存一条金额为 `"23.50"` 的记录。

## AC-02 列出记录

给定多个不同日期和月份的记录：

- `list` 输出全部记录。
- `list --month 2026-06` 只输出 2026 年 6 月记录。
- 输出按日期升序，同日按 ID 升序。

## AC-03 月度汇总

给定 2026-06 有收入 5000.00，支出 food 23.50 和 transport 6.75：

- `income: 5000.00`
- `expense: 30.25`
- `balance: 4969.75`
- 分类行包含 `food: 23.50` 和 `transport: 6.75`。

## AC-04 CSV 导出

执行 export 后：

- 创建目标 CSV。
- 表头精确等于 `id,date,type,category,amount,note`。
- 行顺序与 list 一致。

## AC-05 异常输入

以下场景必须失败且返回非 0：

- `--date 2026/06/01`
- `--month 2026-6`
- `--amount 0`
- `--amount abc`
- `--category "   "`
- 损坏的 JSON 文件
