# M26 自建 Benchmark 通过与最终开发文档

## 目标

M26 的目标是用真实 OpenRouter LLM 调用跑完 5 个自建 benchmark case，修复真实运行中暴露的工程问题，并补齐最终开发者文档。

## 主要变更

- 新增 `codeagent/filesystem.py`，统一封装 Windows 长路径读写能力。
- 加固 implementation、patch、report、shell、benchmark oracle 等路径写入和执行链路。
- 将 implementation 阶段语法检查从 `py_compile` 改为内部 `compile()`，避免深路径下写 `__pycache__` 失败。
- Benchmark oracle 运行时为隐藏测试注入 workspace `PYTHONPATH`，让 runner-only oracle 能导入 Agent 在 workspace 中生成的包。
- 强化 LLM plan path normalization，避免空 workspace 下生成 `workspace/workspace/...`。
- 为 `02_personal_ledger` 的可见需求补充 CSV export 顺序澄清，使可见规范与隐藏 oracle 收敛。
- 将后续默认 LLM 临时切换为 `anthropic/claude-sonnet-4.6`，降低 OpenRouter 成本。
- 扩展 `README.md`，补充安装、OpenRouter Key、CLI、输出目录、resume、benchmark 和排障说明。

## 关键设计决策

- 原始 benchmark case 始终作为可复用模板，runner 只在 clean per-run copy 中执行 Agent、oracle 和日志写入。
- 隐藏 oracle 源码不进入 Agent prompt，也不作为人工实现依据；只使用 runner 产生的失败摘要和日志定位系统行为问题。
- 长路径能力作为基础设施能力下沉到公共 filesystem helper，而不是在各 stage 中复制私有实现。
- Sonnet 4.6 是临时成本控制默认值；历史 Opus 4.8 smoke 记录保留为历史证据。

## 验证记录

- `python -m pytest tests\unit\config tests\unit\models\test_model_factory.py -q` -> 38 passed。
- `python -m codeagent --help` -> succeeded。
- Controlled OpenRouter smoke with default `ModelConfig()` -> `anthropic/claude-sonnet-4.6` returned expected marker。
- `python -m codeagent benchmark --config benchmark\selfbuilt\selfbuilt_benchmark.yaml` -> 5/5 passed, `success_rate=1.00`, `blocked=0`。
- Latest Sonnet selfbuilt aggregate: `benchmark/selfbuilt/codeagent_runs/benchmark/2026-06-03_085139_493426_codeagent_selfbuilt_python_benchmark_3bc92c/benchmark_result.json`。
- Earlier full regression before Sonnet switch: `python -m pytest -q` -> 277 passed；`python -m compileall -q codeagent tests` -> passed。

## 已知限制

- BugsInPy 在当前机器仍受 WSL/conda/Python 3.8.3 环境阻塞，M25 已记录为 explicit blocker。
- README 中的 example task 入口依赖仓库示例配置；后续可补更丰富的演示脚本和截图。
- 当前默认模型是成本控制临时值，若恢复高能力模型，应同步更新 `codeagent/config/defaults.py` 与 `docs/codex/plans.md`。
