# M15 LangGraph Main Graph 与 Routing

## 目标

本里程碑实现 LangGraph 主工作流骨架和确定性路由层，让后续 implementation/testing/debugging/repair 子图可以按统一入口接入，并先用 mocked stage handlers 验证阶段组合、失败分支、修复重试和流式事件形状。

## 主要变更

- 新增 `codeagent/workflow/routing.py`：实现 `StageRouter` 和 `RouteDecision`。
- 新增 `codeagent/workflow/main_graph.py`：基于 `langgraph.graph.StateGraph` 构建 entry、stage、route、final 节点。
- 新增 `codeagent/workflow/factory.py`：提供 `WorkflowFactory`，支持注入 mocked 或真实 stage handler。
- 新增 `codeagent/workflow/events.py`：实现 `stream_workflow_events()`，将 LangGraph raw stream updates 转成 CLI 可消费事件。
- 扩展 `codeagent/workflow/state.py`：增加 `decision_trace`、`next_node`、`final_status`、`repair_attempt`、`max_repair_attempts`。
- 新增 `tests/unit/workflow/test_routing.py`，覆盖路由决策、LangGraph mocked graph、repair retry loop、stream event adapter 和未知 state key 拒绝。

## 需求与设计对齐

- 对齐 FR-13/FR-14：阶段结果通过 `AgentState.stage_results` 传递，路由状态保持 checkpoint-safe。
- 对齐 FR-17：`stream_workflow_events()` 提供节点完成、路由决策、阶段结果、最终状态等事件。
- 对齐 FR-84/FR-87 与 NFR-09：测试失败进入 debug；缺少可继续条件或非完成状态不会盲目进入下一阶段。
- 对齐 NFR-18/NFR-22：repair loop 使用 `repair_attempt` 和 `max_repair_attempts` 控制，主图保持可扩展 stage handler 边界。
- 对齐设计 04/05/07：主图显式包含 entry route、route_after_*、final_success/final_failed/final_cancelled、测试失败到 debug、repair 失败回 debug 直至上限。

## 关键设计决策

- 路由判断集中在 `StageRouter.decide_*()`，`route_*()` 只返回节点名，便于测试和 LangGraph conditional edges 复用。
- 主图不直接把条件函数副作用当作审计记录，而是通过 route 节点写入 `decision_trace` 后再 conditional edge 跳转。
- 只有 `succeeded` 可进入下一阶段；`failed`、`cancelled`、`skipped`、`pending`、`running` 均明确路由到失败或取消终态。
- `repair` 节点每次运行后递增 `repair_attempt`，失败且未达上限时回到 `debugging`，达到上限进入 `final_failed`。
- stage handler 只能返回 `AgentState` 已声明字段；未知 key 立即抛错，避免 LangGraph 静默丢弃跨阶段数据。
- streaming adapter 按每次完成节点输出 stage_result，不按 stage 名去重，确保 debug/repair 循环的多次尝试都可见。

## 验证

- `python -m pytest tests/unit/workflow/test_routing.py -q`：11 passed。
- `python -m pytest tests/unit/workflow -q`：24 passed。
- `python -m compileall -q codeagent/workflow`：通过。
- `python -m codeagent --help`：退出码 0。
- `python -m pytest -q`：151 passed。

## 复查结果

- 规格审阅初次发现 skipped/pending/running 被当作成功，以及缺少 streaming event adapter；已补红灯测试并修复，规格复查 PASS。
- 质量审阅发现 stream adapter 会吞掉 repair/debug retry 的后续 stage_result，以及 stage handler 未知 key 会被 LangGraph 静默丢弃；已补回归测试并修复，质量复查 APPROVED。

## 限制与后续

- 当前 stage handler 是可注入 mock/default skeleton，尚未接入真实 implementation/testing/debugging/repair 子图；后续 M17~M20 会替换这些 handler。
- M16 将补 SQLite checkpoint、interrupt/resume，使当前主图具备持久化恢复能力。
