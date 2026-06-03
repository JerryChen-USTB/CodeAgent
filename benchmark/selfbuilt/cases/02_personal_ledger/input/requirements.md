# 个人记账系统需求说明

## 1. 项目背景

开发一个本地个人记账 CLI，帮助用户记录收入和支出，按月份和分类查看汇总，并导出 CSV 账单。系统面向单个用户，不需要登录、联网或图形界面。

Agent 必须从空 `workspace/` 开始创建完整 Python 项目。

## 2. 技术约束

- 项目语言：Python 3.11+。
- 项目形态：CLI 工具。
- 入口命令：`python -m personal_ledger`。
- 持久化方式：JSON 文件。
- 导出格式：CSV。
- 仅允许使用 Python 标准库。

## 3. 记录模型

一条账目记录包含：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | int | 自动生成，文件内唯一 |
| `date` | str | 日期，格式 `YYYY-MM-DD` |
| `type` | str | `income` 或 `expense` |
| `category` | str | 非空分类，如 salary、food、transport |
| `amount` | str | 正数金额，保存为两位小数字符串 |
| `note` | str | 可选备注，默认空字符串 |

金额计算必须避免浮点误差，建议使用 `decimal.Decimal`。

## 4. 命令行接口

所有命令都通过 `--file` 指定账本文件：

```bash
python -m personal_ledger --file ledger.json <command> [options]
```

### 4.1 添加记录

```bash
python -m personal_ledger --file ledger.json add --date 2026-06-01 --type expense --category food --amount 23.50 --note "lunch"
```

成功输出：

```text
added record #1: expense food 23.50
```

规则：

- `date` 必填，格式 `YYYY-MM-DD`。
- `type` 必填，只能是 `income` 或 `expense`。
- `category` 必填，去除首尾空白后不能为空。
- `amount` 必填，必须大于 0，保存为两位小数字符串。
- `note` 可选。

### 4.2 列出记录

```bash
python -m personal_ledger --file ledger.json list
python -m personal_ledger --file ledger.json list --month 2026-06
```

规则：

- `--month` 可选，格式 `YYYY-MM`。
- 输出按日期升序、同日按 ID 升序。
- 没有记录时输出 `no records`。

每行格式：

```text
#<id> <date> <type> <category> <amount> <note>
```

### 4.3 月度汇总

```bash
python -m personal_ledger --file ledger.json summary --month 2026-06
```

输出格式：

```text
income: 5000.00
expense: 1234.50
balance: 3765.50
food: 320.00
transport: 88.00
```

规则：

- `income` 是当月所有收入合计。
- `expense` 是当月所有支出合计。
- `balance = income - expense`。
- 分类汇总只统计支出分类，按分类名升序输出。

### 4.4 CSV 导出

```bash
python -m personal_ledger --file ledger.json export --output ledger.csv
```

CSV 表头固定为：

```text
id,date,type,category,amount,note
```

导出顺序保持 stored/addition order，即 JSON 文件中记录的存储/添加顺序，不受 `list` 的日期排序规则影响。

## 5. 数据文件规则

- 文件不存在时视为空账本。
- JSON 顶层为数组。
- 保存时使用 UTF-8、两个空格缩进、稳定字段。
- 新 ID 使用当前最大 ID 加一，不复用已删除或历史 ID。

## 6. 异常处理

命令失败时返回非 0，错误写入 stderr：

- 日期格式错误：包含 `invalid date`。
- 月份格式错误：包含 `invalid month`。
- 金额小于等于 0 或不能解析：包含 `invalid amount`。
- 分类为空：包含 `category is required`。
- JSON 文件损坏：包含 `invalid ledger file`。

## 7. CSV Export Ordering Clarification

- CSV export must preserve the ledger's stored record order, which is the order records were added and persisted in the JSON file.
- Do not re-sort CSV rows by date or category during export.
