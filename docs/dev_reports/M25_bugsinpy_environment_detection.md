# M25 BugsInPy 环境检测与 Blocker 报告

## 阶段目标

M25 的目标是让 BugsInPy 可选案例不再静默跳过：环境就绪时使用干净 case 副本运行官方 prepare/test wrapper，环境缺失时在 benchmark 报告中形成明确 blocker，并保持 hidden material 与原始 case 可复用边界。

## 关键实现

- 新增 `BugsInPyEnvironmentDetector`，检测 WSL path conversion、WSL bash、conda profile、`codeagent-bugsinpy-py383`、Python 3.8.3、`dos2unix` 和 BugsInPy 官方脚本。
- `BenchmarkResult` 新增 `blocked_cases` 与 `blockers`，JSON 和 Markdown 报告都会显示 disabled optional case 或环境缺失 case。
- `BenchmarkRunner` 对 `execution_environment.recommended: wsl_conda` 的 case 做 preflight；环境缺失时生成 `final_status=blocked`，不进入普通 workflow，也不算作 silent skip。
- `prepare_command` 现在会保留并替换 `{{CASE_DIR}}` 为 copied case 路径；runner 只允许受控的 `prepare_bugsinpy_wsl_conda.ps1`，拒绝其它 prepare 命令。
- BugsInPy PowerShell wrapper 现在要求显式传入 `-CaseDir`，允许 `benchmark/codeagent_runs/**/case_workspaces/**` 干净副本，拒绝 repo 外路径，并给 WSL path/bash 调用加超时 blocker。
- `benchmark/cases/bugsinpy_black_001/task_config.yaml` 的 prepare/run wrapper 命令改为 copied case 路径，并为正式 benchmark 配置更长的 WSL 命令超时。

## 当前机器结果

- standalone detector：`available=False`
- blocker：WSL path conversion 返回空仓库路径
- manual wrapper smoke：复制 BugsInPy case 到 `benchmark/codeagent_runs/manual_m25/20260603-150516/case_workspaces/bugsinpy_black_001` 后运行 wrapper，返回 `WSL bash command timed out after 60 seconds`
- public benchmark：6 个 enabled case 真实 OpenRouter 调用全部通过，BugsInPy 作为 optional blocker 进入报告
- 最新结果目录：`benchmark/codeagent_runs/benchmark/2026-06-03_070800_221548_codeagent_course_benchmark_adfcde`

## 验证命令

- `python -m pytest tests\unit\benchmark tests\integration\test_benchmark_runner.py tests\test_cli_contract.py tests\unit\config -q` -> 60 passed
- `python -m pytest tests\unit\config tests\unit\tools\test_shell_runner.py -q` -> 43 passed
- `python -m pytest -q` -> 269 passed
- `python -m compileall -q codeagent scripts` -> passed
- `python -m compileall -q codeagent` -> passed
- `python -m codeagent benchmark --config benchmark\benchmark.yaml` -> 6/6 enabled passed, `blocked=1`

## 后续关注

M26 将进入自建 benchmark 全量运行和最终文档收敛。若后续机器具备 WSL + conda + BugsInPy 官方 checkout，应将 `bugsinpy_black_001` 显式启用，验证 prepare wrapper、官方 compile/test wrapper、Agent 修复流程和 source snapshot 证据。
