# M11 ToolRegistry、权限策略与工具级 HITL

## 目标

本里程碑建立工具注册、权限分类和工具级 HITL 兜底机制，防止模型绕过 workflow 审批直接调用有副作用工具。该能力为后续 LangChain tool binding、LangGraph interrupt、benchmark 自动审批和 decision trace 打基础。

## 主要变更

- `codeagent/tools/registry.py`：新增 `ToolSpec`、`ToolRegistry` 和默认工具注册表，按 `implement/test/debug/repair` 暴露工具。
- `codeagent/tools/permissions.py`：新增 `ToolPermissionPolicy`、`ToolCallContext` 和 `PermissionDecision`，实现 allow/ask/deny 分类。
- `codeagent/tools/hitl.py`：新增 `ToolCall`、`ApprovalRequest`、`ApprovalDecision` 和 `ToolHITLInterceptor`。
- `codeagent/tools/__init__.py`：导出 M11 公开接口。
- `tests/unit/tools/test_permissions.py`：覆盖权限分类、stage scope、输出目录限制、HITL 决策、benchmark 自动审批和 decision trace。

## 设计决策

- 权限策略先检查 stage scope，再进行 allow/ask/deny 分类，避免 benchmark 自动审批绕过阶段边界。
- `write_report` / `record_artifact` 不是项目源码副作用，但必须限制在 run output directory 内。
- `apply_patch` / `run_shell` 是 side-effect 工具，普通模式返回 `ask`；benchmark 模式可自动 approve，但必须记录 decision trace。
- 工具级 HITL 不直接执行工具，只返回是否执行、最终参数、审批请求或阻断消息，便于后续 workflow 节点接入。

## 验证

- `python -m pytest tests/unit/tools/test_permissions.py -q`：13 passed。
- `python -m pytest tests/unit/tools -q`：42 passed。
- `python -m py_compile codeagent/tools/permissions.py codeagent/tools/registry.py codeagent/tools/hitl.py codeagent/tools/__init__.py`：通过。
- `python -m pytest -q`：107 passed。
- `python -m codeagent --help`：退出码 0。
- `codeagent --help`：退出码 0。

## 复审结果

- Spec review：初次发现 benchmark 自动审批可绕过 stage scope；修复后 PASS。
- Quality review：初次发现空 edit payload 回退原参数、畸形 path 抛异常；修复后 APPROVED。

## 注意事项

- 包含嵌套 pytest 的验证命令不要并行跑，否则 shell-runner 的 10 秒单命令测试超时可能被资源竞争放大。
- M11 只提供工具注册和拦截模型，真正接入 LangChain tools、LangGraph interrupt 和 CLI 审批界面将在后续里程碑完成。
