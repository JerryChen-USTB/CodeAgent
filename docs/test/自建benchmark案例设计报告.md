# 自建 Benchmark 案例设计报告

> 日期：2026-06-02  
> 目标目录：`benchmark/selfbuilt/`  
> 覆盖阶段：实现 → 测试 → 调试 → 修复

## 1. 重建设计目标

本次对自建 benchmark 进行了重建。旧版案例中存在以下问题：部分选题过于贴合智能体项目本身，`workspace/` 中预置了代码骨架，且 `input/` 材料不足以支撑需求规格说明书中“实现阶段”的输入要求。

新版自建案例调整为 5 个常规软件项目：

1. 待办事项管理系统。
2. 个人记账系统。
3. 学生成绩管理系统。
4. 图书借阅管理系统。
5. 会议室预约系统。

新版目标是让 Agent 真正从空项目开始，根据充分的需求、PRD、用户故事、设计模型和验收标准完成实现、测试、调试和修复。

## 2. 目录结构

```text
benchmark/selfbuilt/
  README.md
  selfbuilt_benchmark.yaml
  cases/
    01_todo_manager/
    02_personal_ledger/
    03_student_gradebook/
    04_library_lending/
    05_meeting_room_booking/
```

每个案例结构固定为：

```text
case/
  case.yaml
  input/
    requirements.md
    prd.md
    user_stories.yaml
    design_model.md
    acceptance_criteria.md
  workspace/
  oracle_tests/
```

其中：

- `workspace/`：初始完全为空，Agent 需要从零创建项目。
- `input/`：Agent 可见，包含实现阶段所需的主要材料。
- `oracle_tests/`：Agent 不可见，仅供评测器最终验证。
- `case.yaml`：记录阶段、入口、输入材料、隐藏路径、测试命令和成功标准。

## 3. 与需求规格的对应

需求规格说明书要求实现阶段至少具备项目骨架，以及自然语言需求、PRD、用户故事、API 规格或设计模型中的一种。新版自建案例采用更充分的输入形式：

| SRS 输入材料 | 新版自建案例做法 |
| --- | --- |
| 项目骨架 | `workspace/` 作为空项目目录 |
| 自然语言需求 | `input/requirements.md` |
| PRD | `input/prd.md` |
| 用户故事 | `input/user_stories.yaml` |
| 设计模型 | `input/design_model.md`，包含 Mermaid 类图、活动图、状态图或时序图 |
| 验收条件 | `input/acceptance_criteria.md` |

这样可以更真实地考察 Agent 的实现计划生成、需求解析、代码生成、测试设计、失败调试和修复能力。

## 4. 案例清单

| case_id | 难度 | 项目形态 | 主要能力 |
| --- | --- | --- | --- |
| `01_todo_manager` | 入门 | CLI + JSON | 任务增删改查、状态过滤、文件持久化 |
| `02_personal_ledger` | 简单 | CLI + JSON/CSV | 记账、月度汇总、分类统计、CSV 导出 |
| `03_student_gradebook` | 中等 | CLI + CSV | 成绩导入、总评计算、等级转换、班级统计 |
| `04_library_lending` | 中高 | CLI + SQLite | 图书入库、读者注册、借书、还书、逾期查询 |
| `05_meeting_room_booking` | 较高 | Flask API + SQLite | 会议室管理、预约创建、冲突检测、取消预约 |

前 4 个案例只使用 Python 标准库。会议室预约系统需要 Flask，依赖要求写入输入材料，由 Agent 在实现时自行创建 `requirements.txt`。

## 5. 评测方式

每个案例的隐藏测试命令为：

```bash
python -m unittest discover -s oracle_tests
```

初始状态下，因为 `workspace/` 为空，隐藏测试应失败，失败原因通常是入口模块不存在。Agent 完成实现后，评测器应在案例副本中运行隐藏测试，以测试是否满足输入材料中的需求。

建议 runner 流程：

1. 读取 `benchmark/selfbuilt/selfbuilt_benchmark.yaml`。
2. 为每个启用案例复制整个原始 case 到干净独立运行目录，原始 case 作为可复用模板保持不变。
3. 将运行副本中的 `input/` 和空 `workspace/` 提供给 Agent。
4. 在运行副本中继续隐藏 `oracle_tests/`，只允许评测器最终使用。
5. Agent 在运行副本中执行实现、测试、调试、修复流程。
6. 评测器在运行副本中运行 `case.yaml` 中的 `test_command`。
7. 统计通过率并保存测试报告。

## 6. 注意事项

1. 不要在原始 `workspace/` 中预置代码、README、requirements 或测试文件。
2. Agent 可以在运行副本的 `workspace/` 中创建任意项目文件。
3. `oracle_tests/` 不应暴露给 Agent。
4. Flask 案例需要在评测前安装 Agent 生成的依赖。
5. 所有案例都必须在干净副本中运行，避免污染原始 benchmark，并保证同一个 case 可被多次重复评测。

## 7. 结论

新版自建 benchmark 更符合需求规格说明书对实现阶段输入材料的要求，也更适合展示软件工程智能体从空项目开始完成常规软件开发任务的能力。5 个案例覆盖 CLI、文件处理、CSV、SQLite 和 Web API 等常见项目形态，难度递增且主题普通，便于课程展示和批量评测。

## 实现对齐变更记录

| 日期 | 变更 | 原因 | 影响 |
|---|---|---|---|
| 2026-06-03 | 明确自建 benchmark 必须复制整个原始 case 到干净副本后运行，原始空 `workspace/` 和 `oracle_tests/` 保持不变。 | 保证自建案例可重复利用并维持隐藏测试隔离。 | 不改变案例验收标准；后续 runner 需在副本中执行 Agent 和 oracle tests。 |
