# M10 Pytest 与通用测试结果解析

## 目标

本里程碑实现测试命令输出的归一化解析能力，为后续 Testing、Debugging、Repair workflow 提供稳定的 `TestResult` 输入。MVP 以 pytest 为主，同时兼容当前 benchmark 中使用的 `python -m unittest discover` 输出。

## 主要变更

- `codeagent/adapters/test_result.py`：新增 `TestResult`、`TestFailure`，记录通过/失败/错误/跳过计数、失败用例、错误摘要、超时、解析置信度、命令、退出码和日志路径。
- `codeagent/adapters/pytest_adapter.py`：解析 pytest summary、失败/错误 nodeid、超时和未知格式 fallback。
- `codeagent/adapters/unittest_adapter.py`：解析 unittest `Ran N tests`、`OK`、`FAILED (...)`、`FAIL:` / `ERROR:` 块。
- `codeagent/tools/pytest_tools.py`：提供 `parse_test_result()` 和 `parse_shell_result()` 分发入口。
- `tests/unit/tools/test_test_result_parser.py`：覆盖 pytest、unittest、超时、未知格式、ShellResult 元数据和截断日志回读。

## 设计决策

- 使用轻量 dataclass 作为当前通用结果模型，方便后续写入 JSON 报告和 stage result。
- parser 在格式未知时返回低置信 fallback，不编造通过数或失败数。
- `parse_shell_result()` 在 `ShellResult.stdout/stderr` 是截断预览时，会回读完整 stdout/stderr log 文件，避免测试摘要被截断后影响路由。
- unittest 支持是 benchmark 兼容层，不扩大 Agent 可见范围；隐藏评测目录和预期结果仍只允许 runner 使用。

## 使用方式

```python
from codeagent.tools.pytest_tools import parse_test_result, parse_shell_result

result = parse_test_result(
    framework="pytest",
    stdout="1 passed in 0.01s\n",
    stderr="",
    exit_code=0,
)
```

## 验证

- `python -m pytest tests/unit/tools/test_test_result_parser.py -q`：8 passed。
- `python -m py_compile codeagent/adapters/test_result.py codeagent/adapters/pytest_adapter.py codeagent/adapters/unittest_adapter.py codeagent/tools/pytest_tools.py`：通过。
- `python -m pytest -q`：94 passed。
- `python -m codeagent --help`：退出码 0。
- `codeagent --help`：退出码 0。

## 复审结果

- Spec review：PASS。
- Quality review：初次发现 P1 问题，截断预览会导致完整日志中的 summary 丢失。
- Quality re-review：APPROVED。

## 限制与后续

- pytest/unittest parser 目前覆盖 CLI 文本输出，不解析 JUnit XML 或 coverage 报告。
- 更丰富的报告写入、workflow 路由和 ToolRegistry 集成将在后续里程碑接入。
