# CodeAgent

CodeAgent 是一个面向 Python 项目的本地命令行软件工程智能体。它基于 LangGraph 和 LangChain 构建，围绕软件开发中的连续阶段执行任务：

```text
实现 implement -> 测试 test -> 调试 debug -> 修复 repair
```

项目目标不是做一个简单的代码生成脚本，而是提供一个可审计、可恢复、带人工审批边界的 Agent 运行时。它可以读取需求文档、设计材料、已有项目代码或失败日志，生成实现计划和补丁，运行测试，分析失败原因，生成修复补丁，并把全过程保存为报告和结构化日志。

## 核心能力

- 命令行运行：支持 `wizard`、`run`、阶段子命令、`benchmark` 和 `resume`。
- 连续工作流：支持 `implement -> test -> debug -> repair` 的完整闭环，也支持合法的连续阶段子集。
- 结构化 LLM 输出：实现计划、测试计划、调试分析、修复计划和补丁草案均使用 Pydantic schema 校验。
- patch-first 修改机制：项目文件不会由模型直接写入，所有代码修改先生成补丁，审批和校验后再应用。
- 人工审批：普通运行默认人工审批计划、补丁和命令；自动审批和 benchmark 自动审批都会写入审计记录。
- 测试执行与解析：支持 pytest、unittest 和 Python 语法检查类命令的受限执行与结果解析。
- 调试和修复闭环：测试失败后可进入调试定位，再生成修复补丁并回归验证。
- 运行产物审计：每次运行都会保存 metadata、配置、日志、审批记录、阶段报告、最终报告和 checkpoint。
- benchmark 支持：支持公开样例和自建样例的隔离运行、隐藏 oracle 评测和成功率统计。

## 技术栈

- Python 3.11+
- Typer / Rich / prompt-toolkit：CLI 和交互式终端体验
- Pydantic：配置和结构化输出校验
- LangGraph：主工作流、阶段路由、checkpoint
- LangChain / langchain-openai：OpenAI-compatible 模型调用
- OpenRouter：默认模型接入通道
- SQLite：checkpoint 存储
- pytest / unittest：测试运行与结果解析

当前默认模型配置位于 `codeagent/config/defaults.py`：

```text
provider: openai_compatible
base_url: https://openrouter.ai/api/v1
model_name: google/gemini-3.5-flash
api_key_env: OPENROUTER_API_KEY
```

## 项目结构

```text
CodeAgent/
  codeagent/          核心 Python 包
  benchmark/          benchmark 配置与案例模板
  dataset/            原始数据集快照，主要用于追溯和后续扩展
  docs/               需求、设计、测试、开发报告和交接文档
  scripts/            BugsInPy / WSL / conda 辅助脚本
  tests/              单元测试和集成测试
  codeagent_runs/     本地运行产物目录，默认不提交到 Git
  pyproject.toml      包配置和依赖声明
  README.md           项目说明
```

`codeagent/` 内部主要模块：

```text
codeagent/
  cli/          CLI 命令、wizard、审批控制台、进度展示、resume
  config/       配置 schema、默认值、加载器和阶段校验
  workflow/     LangGraph 主图、路由、状态、checkpoint
  stages/       implementation/testing/debugging/repair 四阶段服务
  agents/       Prompt 与 LLM 结构化生成逻辑
  models/       模型工厂、密钥解析和结构化输出辅助
  context/      项目扫描、文件读取、代码搜索、敏感路径过滤
  services/     patch 等跨阶段服务
  tools/        shell、patch、pytest 解析、权限和 HITL 工具
  reports/      报告、artifact index、JSONL 审计日志
  benchmark/    benchmark runner、case loader、evaluator、报告
```

## 安装

在仓库根目录执行：

```powershell
python -m pip install -e ".[dev]"
```

安装后可以查看命令帮助：

```powershell
python -m codeagent --help
```

如果使用 console script，也可以运行：

```powershell
codeagent --help
```

## 配置模型密钥

CodeAgent 默认从环境变量读取 OpenRouter API Key。不要把密钥写入配置文件、文档或 Git。

Windows PowerShell 示例：

```powershell
[Environment]::SetEnvironmentVariable("OPENROUTER_API_KEY", "<your-key>", "User")
```

设置后请重新打开终端，让新环境变量生效。

运行产物只记录环境变量名，不记录真实密钥值。

## 快速使用

### 查看帮助

```powershell
python -m codeagent --help
```

### 使用向导

```powershell
python -m codeagent wizard
```

`wizard` 会打开中文任务表单，用于选择项目目录、阶段、输入材料、模型和审批模式。适合首次体验或需要人工审批的演示场景。

### 使用配置文件运行

```powershell
python -m codeagent run --config path\to\task.yaml
```

任务配置通常包含：

- 阶段列表
- 项目路径
- 输入材料路径
- 测试命令
- 模型配置
- 审批模式
- Agent 可见路径和隐藏路径

示例结构：

```yaml
schema_version: 1
task_id: demo-task
title: Demo task
stages:
  - implement
  - test
project_path: path/to/project
output_dir: codeagent_runs/demo
language: python
test_framework: pytest
input_materials:
  - material_type: requirements
    path: path/to/requirements.md
    required: true
    multi: false
test_command:
  command: "python -m pytest -q"
  timeout_seconds: 120
```

### 直接使用阶段子命令

```powershell
python -m codeagent implement --project path\to\project --requirements path\to\requirements.md
python -m codeagent test --project path\to\project --test-cmd "python -m pytest -q"
python -m codeagent debug --project path\to\project --test-cmd "python -m pytest -q" --log path\to\failing.log
python -m codeagent repair --project path\to\project --test-cmd "python -m pytest -q"
```

阶段子命令适合单独验证某一阶段。完整任务更推荐使用 `run --config` 或 `wizard`。

## Benchmark

CodeAgent 提供两类 benchmark：

```text
benchmark/
  benchmark.yaml                         公开/通用 benchmark 配置
  selfbuilt/selfbuilt_benchmark.yaml     自建 5 case benchmark 配置
  selfbuilt/meeting_room_demo_benchmark.yaml
```

运行公开/通用 benchmark：

```powershell
python -m codeagent benchmark --config benchmark\benchmark.yaml
```

运行自建单 case 演示 benchmark：

```powershell
python -m codeagent benchmark --config benchmark\selfbuilt\meeting_room_demo_benchmark.yaml
```

运行完整自建 benchmark：

```powershell
python -m codeagent benchmark --config benchmark\selfbuilt\selfbuilt_benchmark.yaml
```

Benchmark 运行时会把原始 case 复制到干净的运行副本中，Agent 只操作副本。隐藏目录如 `oracle_tests`、`evaluation`、`expected_result.json` 不会暴露给 Agent，只供 runner 最终评测使用。

注意：公开/通用 benchmark 仍需要按当前环境继续验证和固化结果；BugsInPy 样例默认禁用，依赖 WSL、conda、Python 3.8.3 和官方 BugsInPy 脚本。

## 运行产物

普通运行默认写入：

```text
codeagent_runs/<run_id>/
```

重要产物包括：

```text
metadata.json
task_config.yaml
transcript.jsonl
decision_trace.jsonl
workflow.log
workflow_events.jsonl
artifacts_index.json
checkpoints.sqlite
final_report.md
implementation/
testing/
debugging/
repair/
```

建议排错时优先查看：

- `final_report.md`：最终摘要和阶段结果。
- `workflow.log`：人类可读的完整时间线。
- `workflow_events.jsonl`：机器可读事件流。
- `decision_trace.jsonl`：人工或自动审批记录。
- 各阶段的 `stage_result.json` 和 `stage_report.md`。
- 各阶段的 `llm_calls/`：LLM prompt、response、解析输出和校验记录。

## Resume

查看已有运行：

```powershell
python -m codeagent resume --run-id <run_id>
```

如果运行停在 pending interrupt，可以通过 `--decision-json` 提交决策继续：

```powershell
python -m codeagent resume --run-id <run_id> --decision-json '{"interrupt_id":"...","decision_type":"approve"}'
```

如果运行已经完成，`resume` 主要用于查看运行摘要和产物位置。

## 开发与测试

安装开发依赖后，可以运行轻量 smoke：

```powershell
python -m pytest -q tests\test_package_smoke.py tests\test_cli_contract.py
```

运行全量测试：

```powershell
python -m pytest -q
```

如果近期移除了目录或工具，请同步检查测试中是否仍有过期导入或路径引用。

## 安全与限制

- 当前实现不是生产级沙箱。Shell 命令有白名单和路径限制，但不能替代容器隔离。
- 默认只支持 Python 项目和 pytest/unittest 相关测试流程。
- 不会自动提交 Git，也不会替开发者管理工作区状态。
- LLM 输出存在波动，benchmark 结果需要记录模型、配置、时间和运行产物。
- 隐藏 benchmark 材料必须保持隔离，不能放入 Agent 可见输入或 prompt。
- Flask、自定义依赖和真实项目 benchmark 可能需要额外环境准备。

## 相关文档

- `docs/CodeAgent 项目工作交接文档.md`：面向接手团队的详细交接教程。
- `docs/题目设计.md`：课程题目与交付要求。
- `docs/analysis/`：需求规格说明。
- `docs/design/`：系统设计文档包。
- `docs/test/`：测试与 benchmark 相关说明。
- `docs/dev_reports/`：开发过程和优化记录。
- `docs/optimization/优化任务看板.md`：后续优化任务摘要。

## 当前建议

如果是第一次接手项目，建议按以下顺序开始：

1. 安装依赖。
2. 查看 `python -m codeagent --help`。
3. 运行轻量 smoke 测试。
4. 配置 `OPENROUTER_API_KEY`。
5. 用一个自建 case 副本跑一次真实 LLM 任务。
6. 再按需运行自建 benchmark 或公开 benchmark。

更完整的上手路径请阅读 `docs/CodeAgent 项目工作交接文档.md`。
