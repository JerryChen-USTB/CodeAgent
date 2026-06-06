# 自建 Python Benchmark

本目录存放面向课程项目设计的 benchmark 案例，用于评估软件工程智能体。这里的案例与 `benchmark/cases` 下基于公开数据集整理的案例分开管理。

## 输出目录

自建 benchmark 的源材料保留在 `benchmark/selfbuilt/` 下。新的自建 benchmark 运行产物会写入仓库统一的输出根目录：

```text
codeagent_runs/benchmarks/selfbuilt/
```

早期验证运行留下的历史忽略产物可能仍然存在于 `benchmark/selfbuilt/codeagent_runs/` 下，但新的运行应使用 `selfbuilt_benchmark.yaml` 和 `meeting_room_demo_benchmark.yaml` 中声明的 `output_dir`。

## 设计原则

- 每个案例都从空的 `workspace/` 开始。
- 智能体必须根据输入材料从零实现项目。
- `input/` 目录是主要任务上下文。当前每个自建案例都恰好包含四份简体中文材料：PRD、用户故事、设计模型和验收标准。
- `oracle_tests/` 目录对智能体隐藏，只能由 benchmark runner 使用。
- 所有案例都不使用 `expected_result.json`；成功标准声明在 `case.yaml` 中，并由隐藏测试验证。

## 目录结构

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

当前每个案例都采用如下结构：

```text
case/
  case.yaml
  input/
    PRD.md
    user_stories.md
    design_model.md
    acceptance_criteria.md
  workspace/
  oracle_tests/
```

原始 benchmark 副本中的 `workspace/` 应保持为空。runner 必须先把完整案例复制到干净的临时运行目录，然后才能让智能体写代码或运行 oracle 测试。智能体和测试命令只应在复制后的 `workspace/` 中操作；原始案例则保持可复用，供后续 benchmark 运行继续使用。

前三个案例要求实现简单的、按行交互的 TUI，隐藏 oracle 测试可以通过 stdin/stdout 驱动程序。第四个案例要求使用 Python 标准库实现一个可通过本地浏览器访问的 Web UI。第五个案例是 Flask 升级版：它必须同时提供浏览器 Web UI 和稳定的 JSON API。

## 案例概览

| 案例 | 难度 | 类型 | 持久化方式 |
| --- | --- | --- | --- |
| `01_todo_manager` | 入门 | TUI | JSON |
| `02_personal_ledger` | 简单 | TUI | JSON |
| `03_student_gradebook` | 中等 | TUI | JSON |
| `04_library_lending` | 中高 | 标准库 Web UI | SQLite |
| `05_meeting_room_booking` | 高 | Flask Web UI + JSON API | SQLite |

## 手动初始检查

初始 `workspace/` 是空的，因此在智能体实现每个项目之前，oracle 测试理应失败。请在复制后的案例目录上执行这个检查，不要在可复用的原始 benchmark 案例上执行：

```powershell
cd <copied_case_dir>
python -m unittest discover -s oracle_tests
```

只有 benchmark runner/evaluator 应运行这条命令，并且只能在复制后的案例目录中运行。初始状态下，预期失败原因通常是缺少入口模块或缺少包。智能体在复制后的 `workspace/` 中完成案例实现后，同一条命令就是最终验证步骤。

## 变更记录

| 日期 | 变更 | 原因 |
|---|---|---|
| 2026-06-03 | 强化自建案例必须复制到干净运行目录的规则。 | 保持原始空 `workspace/` 和隐藏 oracle 测试在多次 benchmark 运行之间可复用。 |
| 2026-06-05 | 将 `01_todo_manager` 升级为四份更丰富的中文材料，并加入基于 TUI 的隐藏 oracle 测试。 | 让第一个自建案例要求更真实的交互式软件行为，而不是一次一个命令的简单用法。 |
| 2026-06-06 | 将全部五个案例同步为四份中文输入材料；将案例 02 和 03 升级为 TUI，将案例 04 升级为标准库 Web UI，将案例 05 升级为 Flask Web UI + JSON API。 | 保持 benchmark 文档与当前案例材料、案例配置和隐藏 oracle 预期一致。 |
