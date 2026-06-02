# 学生成绩管理系统设计模型

## 1. 类图

```mermaid
classDiagram
    class StudentScore {
      +str student_id
      +str name
      +Decimal homework
      +Decimal midterm
      +Decimal final
      +Decimal total
      +str letter
    }

    class GradebookParser {
      +read_csv(path) list~StudentScore~
      +validate_row(row, line_no) None
    }

    class GradeCalculator {
      +calculate_total(score) Decimal
      +letter_for(total) str
    }

    class ReportWriter {
      +write_report(scores, path) None
    }

    class StatisticsService {
      +summarize(scores) Stats
    }

    GradebookParser --> StudentScore
    GradeCalculator --> StudentScore
    ReportWriter --> StudentScore
    StatisticsService --> StudentScore
```

## 2. report 命令活动图

```mermaid
flowchart TD
    A["执行 report --input --output"] --> B["读取 CSV 表头"]
    B --> C{"必要列完整?"}
    C -- "否" --> D["stderr missing column, 返回非 0"]
    C -- "是" --> E["逐行校验成绩"]
    E --> F["计算 total 和 letter"]
    F --> G["写入 report.csv"]
    G --> H["输出 generated report"]
```

## 3. stats 命令活动图

```mermaid
flowchart TD
    A["执行 stats --input"] --> B["读取并校验 CSV"]
    B --> C["计算每个学生 total 和 letter"]
    C --> D["计算平均分"]
    D --> E["查找最高和最低"]
    E --> F["统计 A-F 分布"]
    F --> G["按固定格式输出"]
```

## 4. 模块建议

- `student_gradebook/__main__.py`：CLI 入口。
- `student_gradebook/cli.py`：命令行解析。
- `student_gradebook/core.py`：成绩计算和统计。
- `student_gradebook/csv_io.py`：CSV 读取与写入。
