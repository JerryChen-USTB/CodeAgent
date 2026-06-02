# 软件工程智能体 Benchmark 数据集汇报

> 本文档记录本项目用于智能体系统测试的 benchmark 数据集准备情况。获取日期为 2026-06-02，工作目录为 `D:\Projects\CodeAgent`。

## 1. 背景与选择原则

本项目需求规格说明书将系统定位为面向 CLI 的软件工程智能体，重点覆盖“实现 + 测试 + 调试 + 修复”阶段，并要求至少支持 5 个 benchmark 案例、统计成功率并输出测试报告。因此，本次准备的数据集按任务难度和工程真实性分为两类：

1. **函数级代码生成与测试类**：HumanEval、MBPP。用于快速验证 Agent 根据自然语言需求生成 Python 函数、生成/执行单元测试、统计 pass rate 的能力。
2. **缺陷定位与程序修复类**：QuixBugs、BugsInPy、SWE-bench Lite。用于验证 Agent 读取项目、复现失败、定位原因、生成补丁并回归测试的能力。

建议后续实验采用由浅入深的顺序：HumanEval/MBPP -> QuixBugs -> BugsInPy -> SWE-bench Lite。

## 2. 获取情况总览

| 数据集 | 本地路径 | 来源 | 本次快照/版本 | 本地规模 | 获取状态 |
| --- | --- | --- | --- | --- | --- |
| QuixBugs | `dataset/QuixBugs` | <https://github.com/jkoppel/QuixBugs> | Git commit `4257f44b0ff1181dedaedee6a447e133219fcebf` | README 标称 40 个 Python/Java 缺陷程序；本地 452 个文件，约 2.35 MB | 已存在并核验 |
| BugsInPy | `dataset/BugsInPy` | <https://github.com/soarsmu/BugsInPy> | Git commit `11c5f1eea954a42132cfd06bf257766a7963e0fd` | 本地统计 17 个项目、501 个 bug 目录，约 3.09 MB | 已克隆 |
| SWE-bench Lite | `dataset/SWE-bench_Lite` | <https://huggingface.co/datasets/princeton-nlp/SWE-bench_Lite> | Hugging Face sha `6ec7bb89b9342f664a54a6e0a6ea6501d3437cc2`，lastModified `2025-03-03T05:29:31Z` | Parquet：dev 23 条、test 300 条，约 1.19 MB | 已下载 |
| MBPP | `dataset/MBPP` | <https://github.com/google-research/google-research/tree/master/mbpp> | `mbpp/` 子目录最近 commit `f46ca8374b4cddef97ca4208ad986049d74d296a` | `mbpp.jsonl` 974 条；`sanitized-mbpp.json` 427 条，约 0.78 MB | 已下载 |
| HumanEval | `dataset/HumanEval` | <https://github.com/openai/human-eval> | Git commit `6d43fb980f9fee3c892a914eda09951f772ad10d` | `HumanEval.jsonl.gz` 164 条，约 0.14 MB | 已克隆 |

说明：SWE-bench Lite 目前存在 `princeton-nlp/SWE-bench_Lite` 与 `SWE-bench/SWE-bench_Lite` 等入口。为方便后续兼容 SWE-bench 官方 quickstart 中常见的 harness 参数，本次保存的是 `princeton-nlp/SWE-bench_Lite` 快照。后续正式实验必须固定 dataset id 与 sha，避免不同入口或更新导致结果不可比。

## 3. 数据集介绍与用途

### 3.1 QuixBugs

QuixBugs 是一个多语言程序修复 benchmark，包含由 Quixey Challenge 转换而来的经典算法程序。官方 README 说明其包含 40 个程序，分别提供 Python 和 Java 版本，每个程序含一个单行缺陷，并配套通过/失败测试用例与修复参考。

在本项目中，QuixBugs 适合作为调试修复阶段的入门 benchmark：

- 输入：缺陷程序、测试用例、测试失败日志。
- Agent 任务：运行测试、定位错误、解释根因、生成补丁、再次运行测试。
- 指标：修复成功率、失败测试转通过数量、是否引入回归、补丁是否最小。
- 优点：规模小、依赖少、容易在本地跑通完整闭环。
- 注意：`python_programs` 中存在辅助文件和测试辅助文件，实际选取案例时应按官方 40 个缺陷程序筛选；不要把 `correct_python_programs` 直接暴露给 Agent。

### 3.2 BugsInPy

BugsInPy 面向真实 Python 项目的缺陷复现与调试研究，仓库中包含框架脚本、项目元数据、bug id 与测试信息。当前本地快照包含 17 个项目、501 个 bug 目录，项目包括 `pandas`、`matplotlib`、`scrapy`、`youtube-dl`、`black` 等。

在本项目中，BugsInPy 适合作为中等难度的真实项目修复 benchmark：

- 输入：项目名、bug id、buggy/fixed 版本、复现测试命令。
- Agent 任务：通过 `bugsinpy-checkout` 获取 buggy 版本，运行 `bugsinpy-test` 复现失败，定位并生成修复。
- 指标：FAIL_TO_PASS 是否通过、原有通过测试是否保持通过、修复尝试轮数、运行耗时。
- 优点：比 QuixBugs 更接近真实项目，有完整命令行工具。
- 注意：部分项目依赖旧 Python 或系统依赖，建议优先在 Linux/WSL + conda 环境中运行；初期可只选择 5-10 个依赖简单的 bug 作为课程演示子集。

### 3.3 SWE-bench Lite

SWE-bench Lite 是 SWE-bench 的轻量版本，用真实 GitHub issue 和对应 PR 构造软件工程修复任务。每条样本通常包含 `repo`、`instance_id`、`base_commit`、`problem_statement`、`patch`、`test_patch`、`FAIL_TO_PASS`、`PASS_TO_PASS` 等字段。

在本项目中，SWE-bench Lite 适合作为最终挑战 benchmark：

- 输入：issue 描述、仓库名、base commit、失败测试和回归测试。
- Agent 任务：理解 issue，检出仓库，定位相关代码，修改并运行指定测试。
- 指标：SWE-bench 常用 resolved rate、FAIL_TO_PASS、PASS_TO_PASS、patch apply 成功率。
- 优点：最接近“真实软件工程智能体”任务，能检验上下文管理、文件搜索、补丁生成和回归验证能力。
- 注意：正式评测通常需要 SWE-bench harness 和 Docker；`patch` 是黄金答案，不能泄露给求解 Agent；运行成本高，课程阶段建议先抽取少量实例。

### 3.4 MBPP

MBPP（Mostly Basic Python Programming Problems）包含基础 Python 编程题，每条样本提供自然语言题目、参考代码、测试列表等。本次保存了 Google Research `mbpp/` 子目录中的 `mbpp.jsonl` 和 `sanitized-mbpp.json`。

在本项目中，MBPP 适合作为实现 + 测试阶段的主要函数级 benchmark：

- 输入：题目描述、函数约束、可选样例测试。
- Agent 任务：生成 Python 函数、生成或补充 pytest 测试、执行测试并修复失败。
- 指标：pass@1、pass@k、测试通过率、生成代码语法错误率。
- 优点：数量较多、题目短，适合批量评估和调参。
- 注意：`code` 字段是参考解，不应进入 Agent prompt；`sanitized-mbpp.json` 更适合作为标准评测入口。

### 3.5 HumanEval

HumanEval 是 OpenAI 发布的代码生成 benchmark，包含 164 个手写 Python 函数问题。每条样本包含 `task_id`、`prompt`、`canonical_solution`、`test` 和 `entry_point`，常用于评估模型根据函数签名和 docstring 生成正确代码的能力。

在本项目中，HumanEval 适合作为最小闭环 smoke test：

- 输入：`prompt` 中的函数签名和文档字符串。
- Agent 任务：生成函数实现，拼接官方测试，隔离执行。
- 指标：pass@1、pass@k、执行超时率、异常率。
- 优点：数据小、评测标准清晰，适合 CI 或每次改动后的快速回归。
- 注意：需要在沙箱、临时目录或容器中执行模型生成代码；`canonical_solution` 只能用于离线核验，不能泄露给 Agent。

## 4. 建议的项目使用方式

### 4.1 Benchmark 分层

| 层级 | 数据集 | 目标 |
| --- | --- | --- |
| L0 快速冒烟 | HumanEval 5-10 条、MBPP sanitized 5-10 条 | 检查 CLI、模型调用、代码写入、测试执行和报告产物是否跑通 |
| L1 函数级批量 | HumanEval 全量、MBPP sanitized | 统计实现阶段的 pass rate 和失败类型 |
| L2 小型修复 | QuixBugs Python 子集 | 验证调试、定位、补丁和回归闭环 |
| L3 真实项目修复 | BugsInPy 子集 | 验证项目级依赖、测试命令执行、日志分析 |
| L4 仓库级 issue 修复 | SWE-bench Lite 子集 | 验证真实 issue 理解、多文件修改和标准化评测能力 |

### 4.2 Benchmark Runner 输出建议

建议后续为每个样本生成统一产物目录，例如：

```text
codeagent_runs/<run_id>/<dataset>/<case_id>/
  task_config.yaml
  transcript.jsonl
  generated_solution.patch
  test_stdout.log
  test_stderr.log
  result.json
  report.md
```

`result.json` 至少包含：

- `dataset`、`case_id`、`task_type`
- `status`: `passed` / `failed` / `timeout` / `error`
- `attempts`
- `fail_to_pass`
- `pass_to_pass`
- `duration_seconds`
- `patch_path`
- `error_summary`

### 4.3 课程展示建议

课程验收要求至少 5 个 benchmark 案例。建议选择：

1. HumanEval 2 条：展示实现 + 测试的最小闭环。
2. MBPP 1 条：展示自然语言题目到函数实现。
3. QuixBugs 1 条：展示算法缺陷修复。
4. BugsInPy 或 SWE-bench Lite 1 条：展示真实项目或真实 issue 的工程能力。

这样既满足数量要求，也能体现从函数级到项目级的能力递进。

## 5. 注意事项与风险

1. **答案泄露风险**：HumanEval 的 `canonical_solution`、MBPP 的 `code`、QuixBugs 的 `correct_python_programs`、SWE-bench 的 `patch` 都是黄金答案，评测时必须从 Agent 可见上下文中隔离。
2. **执行安全风险**：模型生成代码和第三方项目测试都可能执行任意代码，必须设置临时目录、超时、资源限制，必要时使用 Docker。
3. **环境复现风险**：BugsInPy 与 SWE-bench Lite 对依赖版本、系统库和 Python 版本要求更高，Windows 上可能遇到脚本兼容问题；BugsInPy 当前建议使用 WSL + conda，SWE-bench Lite 后续再接入专用 harness。
4. **评测成本风险**：SWE-bench Lite 虽然是轻量版，但仍涉及仓库检出、环境构建和测试执行，不适合作为每次开发的快速回归。
5. **数据污染风险**：HumanEval、MBPP 等经典 benchmark 可能已出现在部分模型训练数据中，报告中应说明该限制，并结合 BugsInPy/SWE-bench Lite 做更接近真实工程的补充评估。
6. **版本可比性风险**：所有实验报告必须记录数据集路径、commit/sha、样本 id 和运行命令；若后续更新数据集，应重新生成版本记录。
7. **测试充分性风险**：函数级 benchmark 的测试不一定覆盖所有边界条件，Agent 生成代码即使通过测试也不代表完全正确；可在项目中加入自生成补充测试或变异测试作为扩展。

## 6. 当前目录结构

```text
dataset/
  BugsInPy/
  HumanEval/
  MBPP/
    README.md
    mbpp.jsonl
    sanitized-mbpp.json
  QuixBugs/
  SWE-bench_Lite/
    README.md
    data/
      dev-00000-of-00001.parquet
      test-00000-of-00001.parquet
```

## 7. 结论

本次已完成 5 个目标 benchmark 数据集的本地准备，覆盖函数级代码生成、小型算法修复、真实 Python 项目修复和真实 GitHub issue 修复。后续开发中，应先围绕 HumanEval、MBPP 和 QuixBugs 建立统一 benchmark runner 与报告格式，再逐步接入 BugsInPy 和 SWE-bench Lite，以控制环境复杂度和运行成本。
