# M04 CLI 基础与帮助契约

## 目标

建立 CLI 命令骨架，让用户可以看到完整命令面、参数、示例和当前实现状态，同时避免把后续业务工作流误报为已完成。

## 主要文件

- `codeagent/cli/app.py`：注册 `wizard`、`run`、`implement`、`test`、`debug`、`repair`、`benchmark`、`resume` 命令。
- `codeagent/cli/progress.py`：统一输出 planned skeleton 面板。
- `codeagent/cli/wizard.py`：提供向导命令骨架。
- `tests/test_cli_contract.py`：覆盖 root help、子命令 help、无参数 `run` 错误和 benchmark clean-copy 提醒。

## 设计决策

- 所有尚未接入工作流的命令在 help 中标注 `Planned skeleton`，执行时输出 `not implemented yet`，防止过度承诺。
- `benchmark` skeleton 显示 clean-copy 提醒：case 将复制到干净的 per-run 目录后再执行，延续 M02 的 benchmark 隔离规则。
- `run` 无参数时返回友好错误 `Provide --config or --project.`。

## 验证命令

```powershell
python -m pytest -q
python -m codeagent --help
codeagent --help
codeagent run --help
codeagent benchmark --help
```

结果：`pytest` 通过 8 个测试；`python -m codeagent --help`、`codeagent --help`、`codeagent run --help`、`codeagent benchmark --help` 均 exit 0。

## 复核状态

M04 规格复核初次指出 help 缺少 examples、help 文案未标明 skeleton 状态；已修复并通过 re-review。质量复核 APPROVED，未发现 P0/P1/P2 必须修复项。

## 已知限制

为验证 `codeagent --help` 控制台脚本，已执行 `python -m pip install -e .`。安装成功，但当前全局 Python 解释器中存在与其他预装包的依赖冲突提示；后续建议使用项目专用虚拟环境执行真实开发和 benchmark。
