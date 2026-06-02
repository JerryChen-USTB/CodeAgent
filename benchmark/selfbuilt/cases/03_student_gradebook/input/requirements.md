# 学生成绩管理系统需求说明

## 1. 项目背景

开发一个面向教师的命令行成绩管理工具。教师可以从 CSV 导入学生成绩，系统计算总评和等级，导出成绩报告，并提供班级统计。

Agent 必须从空 `workspace/` 开始实现完整 Python 项目。

## 2. 技术约束

- 项目语言：Python 3.11+。
- 项目形态：CLI 工具。
- 入口命令：`python -m student_gradebook`。
- 输入格式：CSV。
- 输出格式：CSV 和终端文本。
- 仅允许使用 Python 标准库。

## 3. 输入 CSV

输入 CSV 必须包含以下表头：

```text
student_id,name,homework,midterm,final
```

字段规则：

- `student_id`：非空，文件内唯一。
- `name`：非空。
- `homework`、`midterm`、`final`：0 到 100 的数字。

## 4. 成绩计算

总评公式：

```text
total = homework * 0.30 + midterm * 0.30 + final * 0.40
```

总评保留两位小数，使用正常四舍五入。

等级规则：

- `A`：total >= 90
- `B`：80 <= total < 90
- `C`：70 <= total < 80
- `D`：60 <= total < 70
- `F`：total < 60

## 5. 命令行接口

### 5.1 生成成绩报告

```bash
python -m student_gradebook report --input scores.csv --output report.csv
```

输出 CSV 表头固定为：

```text
student_id,name,homework,midterm,final,total,letter
```

输出行顺序与输入一致。

成功 stdout：

```text
generated report for <n> students
```

### 5.2 班级统计

```bash
python -m student_gradebook stats --input scores.csv
```

输出格式：

```text
students: 3
average_total: 82.50
highest: s1 Ada 95.00
lowest: s2 Bob 70.00
A: 1
B: 1
C: 1
D: 0
F: 0
```

规则：

- `average_total` 是所有学生 total 的平均值，保留两位小数。
- highest / lowest 按 total 比较；如并列，取输入中更早出现的学生。
- 等级分布按 A、B、C、D、F 固定顺序输出。

## 6. 异常处理

命令失败时返回非 0，错误写入 stderr：

- 缺少必要列：包含 `missing column`。
- 学号重复：包含 `duplicate student_id`。
- 成绩不是数字：包含 `invalid score`。
- 成绩不在 0 到 100：包含 `score out of range`。
- 输入 CSV 没有学生记录：包含 `empty gradebook`。

错误信息应尽量包含行号和字段名，便于调试。
