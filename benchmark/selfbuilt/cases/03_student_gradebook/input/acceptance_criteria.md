# 学生成绩管理系统验收标准

## AC-01 生成报告

给定输入：

```csv
student_id,name,homework,midterm,final
s1,Ada,100,90,95
s2,Bob,70,70,70
```

执行 report 后：

- 退出码为 0。
- stdout 包含 `generated report for 2 students`。
- 输出 CSV 表头为 `student_id,name,homework,midterm,final,total,letter`。
- Ada 的 total 为 `95.00`，letter 为 `A`。
- Bob 的 total 为 `70.00`，letter 为 `C`。

## AC-02 班级统计

执行 stats 后：

- 输出 `students: <n>`。
- 输出 `average_total: <value>`。
- 输出 highest 和 lowest。
- A、B、C、D、F 五个等级行全部出现。

## AC-03 等级边界

- total 90.00 是 A。
- total 80.00 是 B。
- total 70.00 是 C。
- total 60.00 是 D。
- total 59.99 是 F。

## AC-04 异常输入

以下情况必须失败并返回非 0：

- 缺少 `final` 列。
- `student_id` 重复。
- 成绩为 `abc`。
- 成绩为 `101` 或 `-1`。
- CSV 只有表头没有学生记录。
