# OPT05 自测脚手架缺陷识别与修复回退保护

## 1. 背景

在 Todo Manager 真实半交互运行中，testing 阶段生成了单元测试和 CLI 集成测试。单元测试通过，但 CLI 集成测试批量失败，后续 debugging/repair 试图把失败解释为产品目录结构问题，repair 又被风险检查拒绝。

本次用户已说明后续 API Key 问题不用处理，因此本轮只修复已知系统问题：Agent 不应为了错误的自测脚手架去修改产品代码。

## 2. 根因

生成的 `tests/test_cli.py` 中包含如下模式：

```python
PROJECT_ROOT = pathlib.Path(__file__).parent.parent / "project"
```

测试文件已经位于项目根目录下的 `tests/`，`parent.parent` 本身就是项目根目录，再拼接 `"project"` 会得到不存在的嵌套目录。随后 `subprocess.run(..., cwd=str(PROJECT_ROOT))` 在启动子进程前就抛出 `NotADirectoryError`，产品 CLI 根本没有被执行。

因此这不是产品实现缺陷，而是 testing 阶段生成测试脚手架的缺陷。

## 3. 修复内容

### 3.1 TestingPatchDraft 质量门

`TestingService` 新增测试脚手架静态质量检查：

- 识别 `Path(__file__).parent.parent / "project"`。
- 识别 `parents[1] / "project"`。
- 同步覆盖 `"workspace"` 后缀。
- 修复 Windows 路径归一化，保证 `tests\test_cli.py` 也会进入内容检查。

命中后 testing 阶段会在应用补丁前失败，并提示重新生成测试补丁。

### 3.2 Debugging 阶段分流

`DebuggingService` 新增“生成测试脚手架缺陷”识别：

- 失败包含 `NotADirectoryError`、`WinError 267` 或 `No such file or directory`。
- 失败证据包含 `subprocess` 和 `cwd`。
- traceback 或失败用例来源指向生成测试文件。

命中后 debugging 写出 `fault_localization.json`、`root_cause.md`、`repair_plan.md`、`debug_report.md`，但阶段结果为失败，并在 `workflow.log` 记录 `debugging_test_harness_failure`。这样主流程不会继续进入 repair。

### 3.3 生成提示词

`PlanGenerationService` 的 TestingPatchDraft prompt 明确要求：

- subprocess CLI 测试的 cwd 必须是真实存在的项目目录。
- 禁止通过给 `__file__` 父目录追加硬编码 `project/workspace` 来推导 cwd。
- 推荐从配置的项目根目录运行测试，并用 `sys.executable -m <module>` 调用 CLI。

## 4. 验证

已执行：

```powershell
python -m py_compile codeagent\agents\plan_generation.py codeagent\stages\testing_service.py codeagent\stages\debugging_service.py
python -m pytest tests\integration\test_testing_stage.py tests\integration\test_debugging_stage.py tests\unit\agents\test_plan_generation.py -q
python -m pytest tests\integration\test_cli_run.py tests\integration\test_repair_stage.py -q
```

结果：

- 相关 testing/debugging/plan generation 测试：`51 passed`。
- CLI run 与 repair 集成测试：`29 passed`。

本轮没有重跑全量 benchmark，也没有再次调用真实 LLM；修复依据来自已保存的真实 Todo Manager run 产物和终端日志，并用自动化回归覆盖该失败形态。

## 5. 影响与剩余限制

- 正常产品缺陷仍会进入 debugging -> repair。
- 自测脚手架在子进程启动前失败时，会停止 repair，避免错误修改产品代码。
- 当前自动识别覆盖典型 cwd 路径错误。若未来出现更隐蔽的测试脚手架缺陷，例如测试断言本身错误但执行路径正常，仍需要继续增强 testing patch 审查和调试分类。
