# M24 Public Benchmark + Real LLM Pass Report

## 阶段目标

M24 目标是接入 OpenRouter 真实 LLM 调用，让 implement 和 repair 阶段由模型生成结构化计划，并用 public benchmark 验证端到端质量。

## 关键实现

- 新增 `PlanGenerationService`，负责收集可见输入、项目源码与失败证据，调用 `ModelClientFactory`，解析并校验 `ImplementationPlan` / `RepairPlan`。
- CLI `implement` 和 `repair` 路径接入真实 LLM 计划生成，再交给确定性的 `ImplementationService` / `RepairService` 生成补丁、执行命令和写报告。
- Benchmark runner 保持原始 case 可复用：每次运行先复制到干净工作区，隐藏 `evaluation/`、`oracle_tests/`、`expected_result.json` 只由 runner-only evaluator 使用。
- ShellRunner 支持 Windows 长路径命令日志，并在文件名压缩后仍能被 testing/debugging 阶段发现和登记。
- PatchService 保留 UTF-8 BOM 与 CRLF，降低真实 Windows 项目的补丁失败风险。

## Benchmark 结果

- 命令：`codeagent benchmark --config benchmark\benchmark.yaml`
- 最新结果：6/6 通过，success_rate=1.00
- 结果目录：`benchmark/codeagent_runs/benchmark/2026-06-03_053939_990694_codeagent_course_benchmark_b44835`
- 覆盖：HumanEval 实现类、MBPP 实现类、QuixBugs 调试修复类。

## 主要迭代修复

- copied benchmark 位于 `codeagent_runs` 下时，LLM 上下文过滤误把可见输入排除；现在按可见根/项目根的相对路径识别生成目录。
- QuixBugs task command 在 copied project cwd 下仍引用 `workspace/tests`；runner 准备阶段归一化为 `tests`。
- LLM 生成 `workspace/calc.py` 等带项目根前缀路径；计划生成后统一规范为 project-root-relative path。
- implementation 语法检查日志路径超过 Windows 传统 260 字符限制；ShellRunner 使用短哈希日志名并支持长路径写入。
- testing 日志被压缩命名后，debugging 未找到固定日志名而回退到旧输入日志；CLI 现在发现实际 stdout/stderr 日志，并优先使用 testing stage 产物。
- implementation/repair handler 的异常边界已拆分：模型生成失败记录为 `model`，stage 执行失败记录为 `tool`。

## 对齐情况

- SRS：覆盖实现、调试、修复、日志报告、benchmark、模型配置、失败处理与命令日志相关需求。
- 设计 02：保持 CLI、模型、workflow、stage service、benchmark、report 模块边界。
- 设计 05：使用 OpenRouter API Key 环境变量，不把密钥写入业务逻辑或报告。
- 设计 10：保留 clean case copy、runner-only hidden oracle、aggregate benchmark report 与自动审批审计。

## 验证命令

- `python -m pytest tests\unit\tools\test_shell_runner.py tests\integration\test_implementation_stage.py tests\integration\test_cli_run.py tests\unit\agents tests\unit\models tests\unit\tools\test_patch_service.py tests\integration\test_benchmark_runner.py tests\test_cli_contract.py -q` -> 70 passed
- `python -m pytest -q` -> 250 passed
- `python -m compileall -q codeagent` -> passed
- `codeagent benchmark --config benchmark\benchmark.yaml` -> 6/6 passed

## 后续工作

进入 M24A：继续加强 LLM 编排的异常诊断、上下文预算、响应审计和自建 regression benchmark，确保真实模型波动、路径边界、隐藏 oracle 和 malformed response 都有稳定测试覆盖。
