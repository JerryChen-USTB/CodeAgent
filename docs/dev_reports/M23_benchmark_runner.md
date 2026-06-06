# M23 BenchmarkRunner、CaseLoader、Evaluator 与 Aggregator

## 目标

实现可批量执行 benchmark case 的基础设施：加载 `benchmark.yaml`，把每个原始 case 复制到干净的本次运行目录，执行 Agent 工作流，运行 runner-only oracle 评估，并输出聚合 JSON/Markdown 报告。

## 主要改动

- `codeagent/benchmark/`：新增 case loader、runner、evaluator、report writer 和数据结构。
- `codeagent/cli/app.py`：将 `codeagent benchmark --config ...` 从占位命令接入真实 runner。
- `codeagent/cli/executor.py`：修复 `test -> debug` 时优先使用本轮测试生成日志，避免被外部旧日志误导。
- `codeagent/models/secrets.py`：在进程环境缺失时支持读取 Windows 用户级 `OPENROUTER_API_KEY`，不打印、不记录 secret 值。
- `tests/integration/test_benchmark_runner.py`：覆盖 clean copy、隐藏 oracle、嵌套 hidden path、单 case 失败隔离、聚合报告和 auto approval trace。

## 设计决策

- 原始 benchmark case 目录只作为可复用模板；每次运行都复制到 `case_workspaces/<case_id>`，Agent、测试命令和 evaluator 只操作复制目录。
- 隐藏 `evaluation/`、`oracle_tests/`、`expected_result.json` 不暴露给 Agent。若 case 的 `test_command` 指向隐藏 oracle，runner 会把 Agent-visible 命令替换为可见 `python -m py_compile ...` smoke，evaluator 再在复制后的 case 根目录中 runner-only 执行 oracle。
- 单个 case 的配置、路径或执行失败会记录为该 case 失败，不中断后续 case，也仍然写出聚合报告。
- OpenRouter key 只从环境变量来源读取；当前已验证用户级环境变量可被项目代码读取并完成真实 LLM smoke。
- Agent-visible smoke 命令生成时会排除所有 hidden roots 下的 Python 文件，即使隐藏 oracle 嵌套在 `workspace/private_tests` 这类项目目录内部，也不会进入 `task_config.yaml`、命令日志或 Agent 命令。

## 使用方式

```powershell
python -m codeagent benchmark --config benchmark\benchmark.yaml
codeagent benchmark --config benchmark\benchmark.yaml
```

输出目录示例：

```text
benchmark/codeagent_runs/benchmark/<benchmark_run_id>/
  case_workspaces/<case_id>/
  case_runs/<case_id>/<agent_run_id>/
  oracle_logs/<case_id>/
  benchmark_result.json
  benchmark_report.md
```

## 验证记录

- `python -m pytest tests\integration\test_benchmark_runner.py -q`：8 passed。
- `python -m pytest tests\integration\test_benchmark_runner.py tests\integration\test_cli_run.py tests\test_cli_contract.py tests\unit\models\test_model_factory.py -q`：24 passed。
- `python -m compileall -q codeagent`：通过。
- `python -m codeagent benchmark --config benchmark\benchmark.yaml`：完成 6 个 case，写出聚合报告，当前 success_rate=0.00。失败原因是实现/修复阶段还未接入 LLM 计划生成；benchmark runner、clean copy 和 oracle 执行链路已工作。
- OpenRouter smoke：使用用户级 `OPENROUTER_API_KEY`、`ModelClientFactory` 和 OpenRouter 真实调用，返回 `CODEAGENT_OPENROUTER_SMOKE_OK`。
- 评审修复：子代理发现 `py_compile` smoke 可能扫描嵌套 hidden `.py`；已新增回归并修复，目标单测和 M23 相关套件重新通过。

## 已知限制

- M23 只完成 benchmark 执行和评分基础设施；HumanEval/MBPP/QuixBugs 要真正通过，还需要后续里程碑把 LLM 计划生成和 repair plan 生成接入工作流。
- 聚合报告当前以总成功率和 case 明细为主，按类别统计、耗时指标和更细粒度评分可在后续 benchmark 优化中增强。
