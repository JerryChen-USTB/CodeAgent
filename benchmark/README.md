# CodeAgent Benchmark

本目录存放从 `dataset/` 派生并重新整理后的 benchmark 输入，用于评估需求规格说明书中描述的软件工程智能体。主配置文件是 `benchmark.yaml`。

## 输出目录约定

benchmark 的源材料保留在 `benchmark/` 下；实际运行生成的产物统一写入仓库根目录下的 `codeagent_runs/benchmarks/`：

```text
codeagent_runs/
  benchmarks/
    public/
      <timestamp>_<benchmark_id>_<hash>/
        benchmark_report.md
        benchmark_result.json
        case_workspaces/
        case_runs/
        oracle_logs/
    selfbuilt/
      <timestamp>_<benchmark_id>_<hash>/
```

不要再把新的 benchmark 输出写到 `benchmark/codeagent_runs/` 或 `benchmark/selfbuilt/codeagent_runs/` 下。上述位置可能仍然保留早期验证运行留下的历史忽略产物，但新的运行应使用 benchmark YAML 文件中配置的集中式输出根目录。

## 案例目录结构

每个案例目录包含：

- `task_config.yaml`：案例级任务配置。
- `input/`：对智能体可见的需求、缺陷报告、失败测试说明或日志。
- `workspace/`：智能体可以编辑的项目骨架或带缺陷项目。
- `evaluation/`：benchmark runner 使用的 oracle 测试。函数级实现案例应对智能体隐藏该目录。
- `expected_result.json`：成功标准和答案隔离说明。

## 案例复用规则

benchmark 运行过程不得直接修改原始案例目录。runner 应先把被选中的完整案例复制到一个干净的单次运行工作区，然后只允许智能体和测试命令在这份副本上操作。`evaluation/`、`expected_result.json` 等隐藏路径在副本中仍然需要对智能体隐藏，只能由 runner 用于评分。这样可以保证源案例在多次 benchmark 运行之间保持可复用。

如果 `task_config.yaml` 中的命令需要引用案例目录，请使用 `{{CASE_DIR}}` 占位符。benchmark runner 必须在执行前把它替换为干净复制后的案例目录路径。

## 默认启用案例

- `humaneval_000_has_close_elements`
- `humaneval_001_separate_paren_groups`
- `mbpp_002_similar_elements`
- `mbpp_003_is_not_prime`
- `quixbugs_gcd`
- `quixbugs_find_in_sorted`

## 可选案例

- `bugsinpy_black_001`：默认禁用，因为它需要 WSL 和 `codeagent-bugsinpy-py383` conda 环境。若要运行它，请先把原始案例复制到一个干净的 `<copied_case_dir>`，再使用 `-CaseDir <copied_case_dir>` 运行 prepare/test 包装脚本。prepare/test 步骤会在复制后的案例上调用官方 BugsInPy 的 `bugsinpy-checkout`、`bugsinpy-compile` 和 `bugsinpy-test` 脚本。

SWE-bench Lite 尚未转换，因为它需要专门的 harness、仓库检出流程和类似 Docker 的评估环境。

## 变更记录

| 日期 | 变更 | 原因 |
|---|---|---|
| 2026-06-03 | 新增案例复用规则，要求每次运行前复制到干净目录，并支持 `{{CASE_DIR}}` 命令替换。 | 保持原始 benchmark 案例可复用，防止意外污染。 |
