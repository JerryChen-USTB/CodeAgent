# Benchmark 样例整理报告

> 整理日期：2026-06-02  
> 目标目录：`benchmark/`  
> 依据文档：`docs/analysis/《基于大语言模型的软件工程智能体》需求规格说明书_v0.1.md`

## 1. 整理目标

原始数据集中的样本不能直接作为本项目智能体的输入。根据需求规格说明书，本项目的智能体需要通过任务配置读取阶段、输入材料、项目路径、测试命令和输出要求。因此，本次将部分数据集样例整理为统一的 benchmark case 目录，使其可以被后续 `codeagent benchmark --config benchmark/benchmark.yaml` 这类命令批量读取。

本次重点支持两类任务：

1. **实现 + 测试**：从 HumanEval、MBPP 中抽取函数级编程任务，提供需求文档和待实现项目骨架，评测测试放在 `evaluation/` 中。
2. **测试 + 调试 + 修复**：从 QuixBugs 中抽取缺陷程序，提供 buggy 项目、失败测试说明、初始失败日志和测试命令。

SWE-bench Lite 暂未整理为可运行样例，因为它需要专门的 harness、真实仓库 checkout、Docker/隔离环境和更高运行成本。BugsInPy 整理了一个默认禁用的真实项目样例，目前已通过 WSL + conda 接入官方 `bugsinpy-checkout`、`bugsinpy-compile` 和 `bugsinpy-test`。

## 2. 目录结构

当前生成的 benchmark 目录如下：

```text
benchmark/
  benchmark.yaml
  index.json
  README.md
  cases/
    humaneval_000_has_close_elements/
    humaneval_001_separate_paren_groups/
    mbpp_002_similar_elements/
    mbpp_003_is_not_prime/
    quixbugs_gcd/
    quixbugs_find_in_sorted/
    bugsinpy_black_001/
```

每个案例目录遵循统一结构：

```text
case_id/
  task_config.yaml
  input/
  workspace/
  evaluation/
  expected_result.json
```

其中：

- `task_config.yaml`：案例级任务配置，包含阶段、输入材料、项目路径、测试命令和可见性规则。
- `input/`：智能体可读取的需求、bug 报告、失败测试说明、失败日志等。
- `workspace/`：智能体需要实现或修复的项目目录。
- `evaluation/`：benchmark runner 使用的评测测试。函数级实现案例中，该目录应对 Agent 隐藏。
- `expected_result.json`：成功标准、来源信息和答案隔离说明。

## 3. 已启用样例

| case_id | 来源数据集 | 任务类型 | 阶段 | 说明 |
| --- | --- | --- | --- | --- |
| `humaneval_000_has_close_elements` | HumanEval | `implementation_testing` | 实现 + 测试 | 根据函数签名和 docstring 实现 `has_close_elements` |
| `humaneval_001_separate_paren_groups` | HumanEval | `implementation_testing` | 实现 + 测试 | 实现括号组拆分函数 |
| `mbpp_002_similar_elements` | MBPP sanitized | `implementation_testing` | 实现 + 测试 | 实现两个列表/元组共享元素查找 |
| `mbpp_003_is_not_prime` | MBPP sanitized | `implementation_testing` | 实现 + 测试 | 实现非素数判断函数 |
| `quixbugs_gcd` | QuixBugs | `test_debug_repair` | 测试 + 调试 + 修复 | 修复 GCD 递归参数错误 |
| `quixbugs_find_in_sorted` | QuixBugs | `test_debug_repair` | 测试 + 调试 + 修复 | 修复二分查找递归边界错误 |

主配置文件 `benchmark/benchmark.yaml` 中默认启用以上 6 个案例，满足需求规格说明书中“至少 5 个 benchmark 案例”的要求。

## 4. 可选样例

`bugsinpy_black_001` 来自 BugsInPy 的 `black` 项目 bug 1，目前在 `benchmark.yaml` 中 `enabled: false`。

暂时禁用原因：

- 该样例声明 Python 版本为 3.8.3，和当前本地 Python 3.12 环境不完全一致。
- 真实项目依赖安装和测试运行需要 Linux/Bash 环境；当前使用 WSL，并用 conda 提供 Python 3.8.3。

当前项目已改为优先使用 BugsInPy 官方 checkout 流程。由于本项目运行在 Windows 上，官方 Bash 脚本通过 WSL 执行，Python 版本由 conda 环境 `codeagent-bugsinpy-py383` 提供：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\prepare_bugsinpy_wsl_conda.ps1 -CaseDir benchmark\cases\bugsinpy_black_001
```

官方 `bugsinpy-checkout` 会先 checkout fixed commit 复制 regression test，再 checkout buggy commit，并把测试文件放回 buggy workspace。因此，`run_test.sh` 中的测试名可能在纯 buggy commit 中不存在，但在官方 checkout 后的 workspace 中存在。

官方 checkout 会在 `benchmark/cases/bugsinpy_black_001/workspace/black/` 生成项目目录，并包含 `bugsinpy_bug.info`、`bugsinpy_run_test.sh`、`bugsinpy_requirements.txt` 等 BugsInPy 官方辅助文件。测试运行通过 `scripts/run_bugsinpy_wsl_conda.ps1` 在 WSL conda 环境中执行官方 compile/test。为避免 WSL 在 Windows 盘 `/mnt/d` 上创建大量 venv 小文件，测试脚本会先把 workspace 复制到 WSL Linux 文件系统的临时运行目录，再调用官方脚本：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_bugsinpy_wsl_conda.ps1 -CaseDir benchmark\cases\bugsinpy_black_001
```

该命令内部调用官方 `bugsinpy-compile` 和 `bugsinpy-test`。初始 buggy 版本应为预期失败；智能体修复后去掉 `-AllowTestFailure`，官方相关测试通过即可判定样例修复成功。

补充说明：`black` 项目需要由 `setuptools_scm` 生成 `_black_version.py`。当前 runner 在官方 compile 后执行 `python setup.py --version` 触发项目原生版本文件生成，再进入官方 test。

## 5. 与需求规格的对应关系

本次整理后的 case 可以映射到需求规格中的关键输入输出规则：

| 需求规格内容 | 本次实现方式 |
| --- | --- |
| FR-73 Benchmark 配置 | `benchmark/benchmark.yaml` 汇总所有案例 |
| FR-74 案例批量运行 | 每个案例提供独立 `task_config.yaml` |
| FR-75 成功率统计 | `expected_result.json` 给出每例成功标准，后续 runner 可统计 |
| FR-76 案例产物隔离 | 每个案例使用独立目录 |
| 实现阶段输入 | `input/requirements.md` + `workspace/solution.py` |
| 测试阶段输入 | `test_command.command` + `evaluation/` 或 `workspace/tests/` |
| 调试阶段输入 | `input/before_test.log`、`input/failure_tests.md`、buggy workspace |
| 修复阶段输入 | buggy project、测试命令、失败日志和 bug 报告 |

## 6. 校验情况

已对 6 个启用案例执行初始测试命令，结果均为预期失败：

| case_id | 初始测试结果 | 说明 |
| --- | --- | --- |
| `humaneval_000_has_close_elements` | exit code 1 | 骨架函数未实现，符合预期 |
| `humaneval_001_separate_paren_groups` | exit code 1 | 骨架函数未实现，符合预期 |
| `mbpp_002_similar_elements` | exit code 1 | 骨架函数未实现，符合预期 |
| `mbpp_003_is_not_prime` | exit code 1 | 骨架函数未实现，符合预期 |
| `quixbugs_gcd` | exit code 1 | 原始缺陷导致递归错误，符合预期 |
| `quixbugs_find_in_sorted` | exit code 1 | 原始缺陷导致部分测试失败，符合预期 |
| `bugsinpy_black_001` | exit code 1 | 官方 BugsInPy 相关测试在 buggy 版本失败，符合预期；该样例默认禁用 |

校验过程中未发现 `ImportError`、`ModuleNotFoundError` 或 `SyntaxError`，说明当前案例的项目路径、测试路径和导入关系是可用的。

## 7. 使用建议

建议后续 benchmark runner 的执行逻辑如下：

1. 读取 `benchmark/benchmark.yaml`。
2. 过滤 `enabled: true` 的案例。
3. 对每个案例读取 `task_config.yaml`。
4. 将 `agent_visibility.visible_paths` 提供给 Agent。
5. 按 `stages` 执行实现、测试、调试和修复流程。
6. 执行 `test_command.command` 验证结果。
7. 根据 `expected_result.json` 生成每例结果。
8. 汇总生成 `benchmark_summary.md` 和 `benchmark_summary.json`。

函数级案例中，`evaluation/` 应作为隐藏评测材料；QuixBugs 修复案例中，`workspace/tests/` 是复现失败所需输入，可以对 Agent 可见，但应禁止 Agent 修改测试文件。

## 8. 注意事项

1. HumanEval 的 `canonical_solution`、MBPP 的 `code`、QuixBugs 的 `correct_python_programs` 没有写入 Agent 可见输入，避免答案泄露。
2. 当前案例全部使用 Python 标准库 `unittest`，避免依赖 pytest。
3. 当前 `workspace/` 是可编辑目录，运行 Agent 前建议复制到临时 run 目录，避免 benchmark 原始样例被直接改坏。
4. QuixBugs 的 `before_test.log` 是初始失败日志，只用于调试输入；最终结果应以重新执行测试命令为准。
5. BugsInPy 已具备 WSL + conda 运行入口，但默认仍禁用；正式批量评测时建议由 runner 为每次实验复制独立 workspace，避免修复过程污染原始样例。
6. SWE-bench Lite 更适合在后续专用 harness 中接入，不建议在当前轻量 benchmark 中强行执行。

## 9. 结论

本次已将 HumanEval、MBPP、QuixBugs 和部分 BugsInPy 样例整理为符合项目输入形式的 benchmark 目录。其中 6 个案例默认启用，可直接作为课程项目智能体的第一批测试数据；1 个 BugsInPy 案例已接入官方 checkout、compile 和 test 流程，并通过 WSL + conda 提供运行环境，作为真实项目修复扩展入口保留。SWE-bench Lite 暂缓整理，以免在当前阶段引入过高的环境和评测复杂度。
