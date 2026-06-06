# M09 ShellRunner 与测试命令执行

## 目标

实现安全的测试命令执行基础：只有已批准且符合 allowlist 的命令才能运行，并保存 stdout、stderr、退出码、耗时、超时状态和可审计 operation record。

## 主要文件

- `codeagent/runtime/commands.py`：命令审批、策略判定结果、ShellResult、operation record 数据结构。
- `codeagent/tools/shell_tools.py`：ShellRunner、命令 allowlist、路径参数校验和日志落盘。
- `codeagent/runtime/__init__.py`：运行时导出。
- `tests/unit/tools/test_shell_runner.py`：ShellRunner 单元测试。

## 关键行为

- 只执行已批准命令；拒绝审批或 policy deny 时 fail-closed。
- 使用 `shell=False`，不经 shell 字符串解释。
- 支持 direct `pytest` 以及 `python -m pytest`、`python -m unittest`、`python -m py_compile`。
- 命令 cwd 必须是目录；path-like 参数必须 resolve 在 cwd 内，禁止 cwd 外绝对路径和 `..` 穿越。
- 拒绝高风险 pytest options：`--override-ini`、`-o`、`-o=...`、`-p`、`-p...`、`--pyargs`。
- 完整 stdout/stderr 落盘，返回给上层的 `ShellResult.stdout` / `stderr` 是可截断预览，并包含截断元数据。
- operation record JSON 记录审批、policy、exit_code、timeout、duration、日志路径和截断元数据。
- benchmark 自动审批通过 `CommandApproval.benchmark_auto_approve()` 记录 `auto=true` 和 reason。

## 对齐检查

已回顾 SRS 中 FR-25、FR-26、FR-27、FR-28、SH-01~SH-05、NFR-11、NFR-17、UC-03、UC-05，以及设计文档中的 ShellRunner、shell 命令审批、命令限制、benchmark 自动审批和日志保存要求。

## 验证命令

```powershell
python -m pytest tests/unit/tools/test_shell_runner.py -q
python -m pytest -q
python -m codeagent --help
codeagent --help
```

结果：ShellRunner 单元测试 10 个通过；全量测试 86 个通过；两个 CLI help 命令退出码均为 0。

## 复核状态

规格复核初次发现 FR-28 长日志截断缺口；已修复并通过 re-review。质量复核初次发现 cwd 外路径参数可执行和 pytest option 绕过；已修复 path argument 校验与高风险 option 拒绝，并通过 re-review。

## 已知限制

当前只允许 pytest、unittest、py_compile 相关命令；更复杂的项目命令、依赖安装或自定义 benchmark 命令将在后续权限策略和 benchmark runner 阶段按配置显式放行。
