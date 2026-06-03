# M22 非交互运行与阶段子命令汇报

## 目标

将 `run --config`、`run --project --stages` 以及 `implement/test/debug/repair` 阶段子命令接入统一的 `TaskConfig` 归一化和 LangGraph 主工作流执行路径。

## 主要变更

- `codeagent/config/cli_mapping.py`
  - 新增 CLI 参数到 `TaskConfig(mode="run")` 的统一映射入口。
  - 支持 config 文件、run 参数、单阶段子命令参数和输入材料映射。
- `codeagent/cli/executor.py`
  - 新增 CLI workflow executor：初始化 run context，构建 `WorkflowFactory`，流式渲染事件，写 final report。
  - testing 阶段支持命令式执行：通过 `ShellRunner` 白名单策略运行用户提供的 pytest/unittest/py_compile 命令，并解析测试结果。
  - debugging 阶段复用 `DebuggingService`，支持静态失败日志或非交互命令复现。
  - implementation/repair 在缺少结构化计划时写明确 failed 报告，避免 skeleton 成功。
- `codeagent/cli/app.py`
  - `run/implement/test/debug/repair` 从 skeleton 改为真实执行入口。
  - 保留 `benchmark/resume` 当前既有行为，后续里程碑继续接入。
- `codeagent/config/loader.py`
  - `project_path` 现在必须是已存在目录。
- `examples/task.yaml`
  - 新增 debug-only 示例，使用公开静态失败日志，不调用 LLM、不修改项目文件。

## 设计决策

- 所有非交互 CLI 命令走同一 `TaskConfig` 路径，避免命令参数和配置文件行为分叉。
- CLI executor 不使用 LangGraph 默认 skeleton handler；未具备结构化计划的阶段必须失败并写报告。
- M22 可以真实运行 testing/debugging 的命令和静态日志分析；implementation/repair 的 LLM 计划生成仍是后续工作。
- `codeagent run --config examples/task.yaml` 使用静态日志示例验证完整 graph、stream、run dir 和 final report，不消耗 OpenRouter token。

## 使用方式

```bash
codeagent run --config examples/task.yaml
codeagent run --project ./repo --stages debug --test-cmd "pytest -q"
codeagent debug --project ./repo --log failing.log --output-dir codeagent_runs
codeagent test --project ./repo --test-cmd "pytest -q"
```

## 验证记录

- `python -m pytest tests\integration\test_cli_run.py -q` -> 5 passed。
- `python -m pytest tests\integration\test_cli_run.py tests\test_cli_contract.py -q` -> 9 passed。
- `python -m pytest tests\integration\test_cli_run.py tests\integration\test_cli_wizard.py tests\test_cli_contract.py tests\unit\config tests\unit\workflow tests\unit\reports tests\unit\runtime tests\unit\tools -q` -> 140 passed。
- `python -m pytest -q` -> 229 passed。
- `python -m compileall -q codeagent` -> passed。
- `codeagent run --config examples\task.yaml` -> succeeded。
- `python -m codeagent run --config examples\task.yaml` -> succeeded。
- `python -m codeagent --help` and `codeagent --help` -> passed。

## 审查结论

- 规格审查：PASS。
- 质量审查：APPROVED，未发现剩余 P0/P1/P2 问题。

## 限制与后续

- M22 不进行真实 LLM 计划生成，因此不会消费 OpenRouter token。
- M23 将实现 benchmark runner，并必须继续遵守原始 case 复制到干净运行目录后再执行的规则。
- 在最终 example/benchmark 前仍需受控执行真实 OpenRouter LLM smoke，且不得打印或持久化密钥。
