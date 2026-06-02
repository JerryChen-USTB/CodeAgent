# 04 LangGraph 工作流设计

## 1. 设计目标

LangGraph 是本项目最核心的系统设计产物。图设计需要满足：

1. 支持用户选择单阶段或连续多阶段执行；
2. 明确实现、测试、调试、修复四个阶段子图；
3. 显式表现测试失败进入调试、修复后回归验证、多轮修复闭环；
4. 显式表现阶段内工具调用循环，而不是简单线性流水线；
5. 显式表现 HITL 审批节点；
6. 支持 SQLite checkpoint、interrupt/resume、streaming 进度输出；
7. 副作用操作必须放在审批节点之后，避免中断恢复导致重复副作用。

## 2. 图设计规则

| 规则 | 说明 |
|---|---|
| R1：主图负责阶段路由 | 主图不写具体业务逻辑，只判断是否进入某阶段、是否跳过、是否失败 |
| R2：阶段逻辑放入子图 | 实现、测试、调试、修复分别用子图表达，便于单独测试和展示 |
| R3：LLM 节点输出结构化数据 | 计划、测试方案、故障定位、修复计划等使用 Pydantic schema |
| R4：工具循环显式化 | `agent_node → need_tool? → tool_node → agent_node` |
| R5：HITL 节点纯审批 | 审批节点只 interrupt，不执行文件写入、patch 应用、shell |
| R6：副作用节点幂等 | `apply_patch`、`run_pytest`、`write_report` 检查产物存在和 call_id |
| R7：所有路由写入 decision_trace | 每次条件边选择都记录摘要原因 |
| R8：修复闭环有上限 | 默认 `max_repair_attempts=3`，达到上限生成失败报告 |

## 3. 主工作流图

```mermaid
flowchart TD
  START([START]) --> LoadConfig[加载 TaskConfig]
  LoadConfig --> Validate[校验阶段连续性/路径/语言/pytest]
  Validate --> InitRun[初始化 RunContext\n输出目录 + SQLite checkpoint]
  InitRun --> ScanProject[扫描项目结构]
  ScanProject --> RouteImpl{包含 implement?}

  RouteImpl -- 是 --> ImplSub[[ImplementationSubgraph]]
  RouteImpl -- 否 --> RouteTest{包含 test?}

  ImplSub --> ImplOK{实现阶段成功?}
  ImplOK -- 否 --> FinalFail[写失败 final_report]
  ImplOK -- 是 --> RouteTest

  RouteTest -- 是 --> TestSub[[TestingSubgraph]]
  RouteTest -- 否 --> RouteDebug{包含 debug?}

  TestSub --> TestOK{测试通过?}
  TestOK -- 是 --> TestPassRoute{后续仅为 debug/repair?}
  TestPassRoute -- 是 --> FinalSuccess[写成功 final_report\n跳过调试修复]
  TestPassRoute -- 否 --> RouteDebug
  TestOK -- 否 --> RouteDebug

  RouteDebug -- 是 --> DebugSub[[DebuggingSubgraph]]
  RouteDebug -- 否 --> TestFailedNoDebug{测试失败?}
  TestFailedNoDebug -- 是 --> FinalFail
  TestFailedNoDebug -- 否 --> RouteRepair{包含 repair?}

  DebugSub --> DebugOK{调试成功?}
  DebugOK -- 否 --> FinalFail
  DebugOK -- 是 --> RouteRepair

  RouteRepair -- 是 --> RepairSub[[RepairSubgraph]]
  RouteRepair -- 否 --> FinalSuccess

  RepairSub --> RepairOK{修复验证通过?}
  RepairOK -- 是 --> FinalSuccess
  RepairOK -- 否 --> CanRetry{repair_attempt < max?}
  CanRetry -- 是 --> DebugSub
  CanRetry -- 否 --> FinalFail

  FinalSuccess --> END([END])
  FinalFail --> END
```

## 4. 实现阶段子图

实现阶段重点是需求解析、实现计划、代码 patch 生成、patch 审批、应用后语法检查。它不是“模型一次生成代码后结束”，而是包含读取/搜索工具循环和检查失败后的修订循环。

```mermaid
flowchart TD
  I_START([Implementation START]) --> I_Load[读取需求材料/技术约束/验收条件]
  I_Load --> I_Profile[读取 ProjectProfile]
  I_Profile --> I_Summary[生成需求摘要与验收映射]
  I_Summary --> I_Plan[生成 implementation_plan.md]
  I_Plan --> I_PlanCheck{计划是否覆盖需求?}
  I_PlanCheck -- 否 --> I_RevisePlan[根据缺口修订计划]
  I_RevisePlan --> I_Plan
  I_PlanCheck -- 是 --> I_Coder[代码修改 Agent 节点]

  I_Coder --> I_NeedTool{需要工具?}
  I_NeedTool -- 读取文件 --> I_Read[read_file]
  I_NeedTool -- 搜索代码 --> I_Search[search_code]
  I_NeedTool -- 扫描目录 --> I_Scan[scan_project]
  I_Read --> I_Coder
  I_Search --> I_Coder
  I_Scan --> I_Coder

  I_NeedTool -- 生成补丁 --> I_Patch[生成 implementation/patch.diff]
  I_Patch --> I_PatchCheck[检查 patch 范围/格式/敏感路径]
  I_PatchCheck --> I_Approval{人工审批 patch}
  I_Approval -- reject/respond --> I_Coder
  I_Approval -- edit --> I_Patch
  I_Approval -- approve --> I_Apply[apply_patch 副作用节点]

  I_Apply --> I_Syntax[运行 python -m py_compile 或轻量检查]
  I_Syntax --> I_SyntaxOK{语法/轻量检查通过?}
  I_SyntaxOK -- 否且未超限 --> I_Coder
  I_SyntaxOK -- 否且超限 --> I_Fail[写 implementation failed result]
  I_SyntaxOK -- 是 --> I_Report[写 implementation_report.md\nchanged_files.json]
  I_Report --> I_END([Implementation END])
  I_Fail --> I_END
```

### 实现阶段关键状态字段

| 字段 | 说明 |
|---|---|
| `implementation_result.plan_path` | 实现计划路径 |
| `pending_patch.path` | 待审批 patch 路径 |
| `pending_patch.summary` | 修改文件、增删行摘要 |
| `implementation_result.changed_files` | 应用后的变更清单 |
| `implementation_result.syntax_check` | 语法/轻量检查结果 |
| `implementation_result.status` | succeeded/failed/cancelled |

## 5. 测试阶段子图

测试阶段必须先生成测试方案，人工审核后才生成 pytest 测试文件。测试文件仍采用 patch-first。测试命令执行前也必须审批；Benchmark 模式可以由配置自动批准。

```mermaid
flowchart TD
  T_START([Testing START]) --> T_Target[分析测试目标\n需求 + 实现报告 + 代码摘要]
  T_Target --> T_Plan[生成 testing/test_plan.md]
  T_Plan --> T_Review{人工审核测试方案}
  T_Review -- reject/respond --> T_Plan
  T_Review -- edit --> T_RevisePlan[结合用户意见修订方案]
  T_RevisePlan --> T_Plan
  T_Review -- approve --> T_Writer[测试生成 Agent 节点]

  T_Writer --> T_NeedTool{需要工具?}
  T_NeedTool -- 读取源码 --> T_Read[read_file]
  T_NeedTool -- 搜索函数 --> T_Search[search_code]
  T_Read --> T_Writer
  T_Search --> T_Writer

  T_NeedTool -- 生成测试补丁 --> T_TestPatch[生成 testing/test_patch.diff]
  T_TestPatch --> T_PatchCheck[检查只修改 tests/ 或允许测试路径]
  T_PatchCheck --> T_PatchApproval{人工审批测试 patch}
  T_PatchApproval -- reject/respond --> T_Writer
  T_PatchApproval -- edit --> T_TestPatch
  T_PatchApproval -- approve --> T_ApplyTest[apply_patch 副作用节点]

  T_ApplyTest --> T_CmdPrepare[生成/确认 pytest 命令]
  T_CmdPrepare --> T_CmdApproval{人工审批测试命令}
  T_CmdApproval -- reject --> T_SkipRun[标记测试未执行]
  T_CmdApproval -- edit --> T_CmdPrepare
  T_CmdApproval -- approve --> T_Run[run_shell: pytest]

  T_Run --> T_Parse[parse_pytest_result]
  T_Parse --> T_Report[写 test_report.json/md\nstdout/stderr]
  T_Report --> T_Result{测试通过?}
  T_Result -- 是 --> T_PASS[Testing succeeded]
  T_Result -- 否 --> T_FAIL[Testing failed\n带失败摘要]
  T_SkipRun --> T_FAIL
  T_PASS --> T_END([Testing END])
  T_FAIL --> T_END
```

### 测试阶段路由输出

| 测试状态 | 后续阶段包含 debug? | 主图行为 |
|---|---:|---|
| 全部通过 | 是/否 | 记录成功；若后续只有 debug/repair，则跳过并结束成功 |
| 失败 | 是 | 进入 DebuggingSubgraph |
| 失败 | 否 | 写失败 final_report |
| 用户拒绝执行命令 | 是 | 可进入调试的静态日志路径，但置信度较低 |
| 用户取消 | 任意 | 停止运行，写 cancelled |

## 6. 调试阶段子图

调试阶段包含复现、日志摘要、源码搜索、可疑位置排序、根因分析、修复建议。若没有测试命令，则跳过自动复现，进入基于日志/源码的静态分析路径。

```mermaid
flowchart TD
  D_START([Debugging START]) --> D_Collect[收集 test_report / stderr / failing tests]
  D_Collect --> D_HasCmd{有测试命令?}
  D_HasCmd -- 是 --> D_CmdApproval{审批复现命令}
  D_CmdApproval -- approve --> D_Reproduce[run_shell: 复现失败]
  D_CmdApproval -- edit --> D_HasCmd
  D_CmdApproval -- reject --> D_Static[静态分析路径]
  D_HasCmd -- 否 --> D_Static

  D_Reproduce --> D_Parse[解析复现日志]
  D_Static --> D_Parse
  D_Parse --> D_FailureSummary[生成 failure_summary.md]
  D_FailureSummary --> D_Debugger[调试 Agent 节点]

  D_Debugger --> D_NeedTool{需要工具?}
  D_NeedTool -- 搜索错误关键词 --> D_Search[search_code]
  D_NeedTool -- 读取候选文件 --> D_Read[read_file]
  D_NeedTool -- 读取测试文件 --> D_ReadTest[read_file tests]
  D_Search --> D_Debugger
  D_Read --> D_Debugger
  D_ReadTest --> D_Debugger

  D_NeedTool -- 输出定位 --> D_Fault[生成 fault_localization.json]
  D_Fault --> D_RankCheck{Top-N 是否有解释?}
  D_RankCheck -- 否 --> D_Debugger
  D_RankCheck -- 是 --> D_Root[生成 root_cause.md]
  D_Root --> D_RepairPlan[生成 repair_plan.md]
  D_RepairPlan --> D_Report[写 debug_report.md\ndebug_trace.jsonl]
  D_Report --> D_OK{根因置信度足够?}
  D_OK -- 是 --> D_PASS[Debugging succeeded]
  D_OK -- 否 --> D_WARN[Debugging partial\n进入修复但标记风险]
  D_PASS --> D_END([Debugging END])
  D_WARN --> D_END
```

### 调试阶段置信度

| 情况 | 置信度影响 |
|---|---|
| 能复现失败且日志稳定 | 提高 |
| 失败测试名称和源码函数能匹配 | 提高 |
| 只有用户提供日志，无测试命令 | 降低 |
| 候选位置超过 5 个且缺少明确证据 | 降低 |
| 根因只来自推测，没有工具证据 | 降低 |

## 7. 修复阶段子图

修复阶段根据调试产物生成最终修复计划和最小 patch。patch 审批后应用，再运行 pytest。失败后返回主图，由主图判断是否继续调试/修复闭环。

```mermaid
flowchart TD
  R_START([Repair START]) --> R_Load[读取 root_cause / repair_plan / fault_localization]
  R_Load --> R_Plan[生成 repair_plan.final.md]
  R_Plan --> R_Repairer[修复 Agent 节点]

  R_Repairer --> R_NeedTool{需要工具?}
  R_NeedTool -- 读取源码 --> R_Read[read_file]
  R_NeedTool -- 搜索上下文 --> R_Search[search_code]
  R_Read --> R_Repairer
  R_Search --> R_Repairer

  R_NeedTool -- 生成修复补丁 --> R_Patch[生成 repair/repair.patch.diff]
  R_Patch --> R_Risk[检查 patch 风险\n删除测试/跳过断言/硬编码]
  R_Risk --> R_Approval{人工审批修复 patch}
  R_Approval -- reject/respond --> R_Repairer
  R_Approval -- edit --> R_Patch
  R_Approval -- approve --> R_Apply[apply_patch 副作用节点]

  R_Apply --> R_CmdApproval{审批回归测试命令}
  R_CmdApproval -- edit --> R_CmdApproval
  R_CmdApproval -- reject --> R_NoVerify[无法验证修复]
  R_CmdApproval -- approve --> R_Run[run_shell: pytest]
  R_Run --> R_Parse[parse_pytest_result]
  R_Parse --> R_Report[写 repair_report.md\nafter_test.log]
  R_Report --> R_Verify{测试通过?}
  R_Verify -- 是 --> R_PASS[Repair succeeded]
  R_Verify -- 否 --> R_FAIL[Repair failed\n返回主图判断是否重试]
  R_NoVerify --> R_FAIL
  R_PASS --> R_END([Repair END])
  R_FAIL --> R_END
```

## 8. 阶段内通用工具调用循环

各阶段的 Agent 节点共享一种受控工具循环：

```mermaid
flowchart LR
  A[Agent 节点\nLLM + prompt + state] --> B{模型是否请求工具?}
  B -- 否 --> C[输出结构化结果]
  B -- 是 --> D[ToolRegistry 检查工具名/参数]
  D --> E{权限策略}
  E -- allow --> F[执行只读工具]
  E -- ask --> G[工具级 HITL interrupt]
  E -- deny --> H[返回拒绝 ToolMessage]
  G -- approve/edit --> I[执行有副作用工具]
  G -- reject/respond --> H
  F --> J[ToolResult 回填 state]
  I --> J
  H --> J
  J --> A
```

工具循环必须设置最大工具调用次数，默认建议：单个 Agent 节点最多 12 次工具调用；同一工具连续失败 2 次后要求模型换策略或进入失败处理。

## 9. 总合并图

以下图把四个阶段内部关键闭环合并展示，适合在最终系统设计方案中作为“总 LangGraph 图”的 Mermaid 图。

```mermaid
flowchart TD
  S([START]) --> Cfg[TaskConfig + ProjectProfile]
  Cfg --> Stages{阶段选择}

  Stages -->|implement| IA[实现: 需求摘要/验收映射]
  IA --> IB[实现: 计划生成]
  IB --> IC[实现: 读文件/搜索工具循环]
  IC --> ID[实现: 生成 patch]
  ID --> IE{审批实现 patch}
  IE -->|reject/edit| IC
  IE -->|approve| IF[应用 patch]
  IF --> IG{语法/轻量检查}
  IG -->|失败且未超限| IC
  IG -->|失败超限| FFail[失败报告]
  IG -->|通过| TRoute{包含 test?}

  Stages -->|test only| TA[测试: 目标分析]
  TRoute -->|是| TA
  TRoute -->|否| DRoute{包含 debug?}
  TA --> TB[测试: 生成 test_plan]
  TB --> TC{审批测试方案}
  TC -->|reject/edit| TB
  TC -->|approve| TD[测试: 读源码/搜索工具循环]
  TD --> TE[生成 test_patch]
  TE --> TF{审批测试 patch}
  TF -->|reject/edit| TD
  TF -->|approve| TG[应用测试 patch]
  TG --> TH{审批 pytest 命令}
  TH -->|edit| TH
  TH -->|reject| TFail[测试未执行/失败]
  TH -->|approve| TI[运行 pytest]
  TI --> TJ[解析 test_report]
  TJ --> TK{测试通过?}
  TK -->|通过| FSuccess[成功报告]
  TK -->|失败| DRoute
  TFail --> DRoute

  Stages -->|debug only| DA[调试: 收集失败信息]
  DRoute -->|是| DA
  DRoute -->|否| RRoute{包含 repair?}
  DA --> DB{可复现?}
  DB -->|运行命令| DC[审批并运行复现]
  DB -->|仅日志| DD[静态日志分析]
  DC --> DE[失败摘要]
  DD --> DE
  DE --> DF[搜索/读取源码工具循环]
  DF --> DG[故障定位 fault_localization]
  DG --> DH[根因分析 root_cause]
  DH --> DI[修复建议 repair_plan]
  DI --> RRoute

  Stages -->|repair only| RA[修复: 读取调试/失败输入]
  RRoute -->|是| RA
  RRoute -->|否| FSuccess
  RA --> RB[生成修复计划]
  RB --> RC[读取/搜索工具循环]
  RC --> RD[生成 repair.patch]
  RD --> RE[风险检查]
  RE --> RF{审批修复 patch}
  RF -->|reject/edit| RC
  RF -->|approve| RG[应用修复 patch]
  RG --> RH{审批回归命令}
  RH -->|reject| RFail[修复无法验证]
  RH -->|approve| RI[运行 pytest]
  RI --> RJ[解析结果]
  RJ --> RK{验证通过?}
  RK -->|通过| FSuccess
  RK -->|失败| RL{未达最大修复轮数?}
  RL -->|是| DA
  RL -->|否| FFail
  RFail --> RL

  FSuccess --> E([END])
  FFail --> E
```

## 10. 条件路由函数设计

```python
def route_after_testing(state: AgentState) -> str:
    result = state["testing_result"]
    if result["status"] == "cancelled":
        return "final_cancelled"
    if result["status"] == "succeeded" and result["failed"] == 0:
        return "final_success"
    if "debug" in state["selected_stages"]:
        return "debugging"
    return "final_failed"


def route_after_repair(state: AgentState) -> str:
    result = state["repair_result"]
    if result["success"]:
        return "final_success"
    if state.get("repair_attempt", 0) < state.get("max_repair_attempts", 3):
        return "debugging"
    return "final_failed"
```

## 11. Checkpoint 与 thread_id

- 每次运行生成唯一 `run_id`；
- LangGraph `thread_id` 使用 `run_id`；
- SQLite 文件放在 `codeagent_runs/<run_id>/checkpoints.sqlite`；
- 所有 interrupt 恢复必须使用同一个 `thread_id`；
- `resume --run-id <run_id>` 会读取 `task_config.yaml` 和 checkpoint，继续执行等待中的 interrupt 或失败后的可恢复节点。

示例：

```python
config = {"configurable": {"thread_id": run_id}}
for event in graph.stream(initial_state, config=config, stream_mode=["updates", "custom", "messages"]):
    render_event(event)

# 人工审批后
for event in graph.stream(Command(resume=human_decision), config=config, stream_mode=["updates", "custom"]):
    render_event(event)
```

## 12. Streaming 事件设计

| event type | 用途 | CLI 示例 |
|---|---|---|
| `updates` | 节点完成后的状态更新 | `[测试阶段] test_plan 已生成` |
| `custom` | 节点内部进度 | `[工具] 正在搜索 "calculate_total"` |
| `messages` | 可选显示模型输出 token | `[Agent] 正在解释失败原因...` |
| `checkpoints` | 调试或 resume 使用 | `[checkpoint] 已保存到 run_id=...` |
| `debug` | 开发模式使用 | 仅 debug 日志，不默认展示给用户 |

## 13. 中断恢复与副作用安全

审批节点设计为纯节点：

```text
approve_patch_node: interrupt(diff + summary) → HumanDecision
apply_patch_node: 根据 HumanDecision 执行 apply_patch
```

禁止这样设计：

```text
bad_node: 先写文件 → interrupt → 恢复后继续
```

因为从 interrupt 恢复时节点可能重新执行，副作用可能重复发生。所有副作用必须拆到审批后的独立节点，并用 `operation_id` 保证幂等。
