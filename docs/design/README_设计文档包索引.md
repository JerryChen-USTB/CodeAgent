# 《基于大语言模型的软件工程智能体》系统设计文档包索引

> 项目：基于大语言模型的软件工程智能体  
> 设计范围：实现 + 测试 + 调试 + 修复  
> 集成方式：CLI  
> 技术主线：LangGraph + LangChain + Python + pytest + SQLite checkpoint  
> 版本：v1.0  
> 日期：2026-06-02

## 1. 文档包说明

本目录是一组面向课程提交和后续实现的 Markdown 系统设计文档。课程题目要求“智能体系统设计方案”包含系统架构图、模块划分、数据流、核心类/接口设计、关键技术选型理由；本设计文档包在此基础上补充了 LangGraph 工作流、HITL、工具权限、错误处理、运行产物和 Benchmark 执行设计。

本文档包不包含测试方案与测试报告；Benchmark 模块只作为系统能力模块和运行机制进行设计，不展开测试用例设计。

## 2. 设计决策基线

| 决策项 | 结论 |
|---|---|
| 支持语言 | MVP 只支持 Python，Java 通过接口预留 |
| 测试框架 | MVP 只支持 pytest |
| 工作流组织 | 采用 LangGraph 主图 + 阶段子图，不采用复杂多 Agent 对话团队 |
| HITL | 工作流级 HITL + LangChain 工具级 HITL 双层机制 |
| 文件修改 | 项目源码和测试文件统一 patch-first；报告和日志直接写输出目录 |
| Checkpoint | SQLite checkpoint，所有运行以 run_id/thread_id 关联 |
| 记录内容 | 不记录隐藏思维链，改为记录可审计推理摘要、工具调用和 decision trace |
| Benchmark | 6 个基准案例：实现+测试 2 个，调试+修复 2 个，全流程 2 个 |
| Git 工作区检查 | 完全不考虑；不依赖 git status、git apply、git commit |
| 模型接入 | OpenAI-compatible API；临时默认模型为 `anthropic/claude-sonnet-4.6`，通过 OpenRouter API Key 调用 |
| IDE 集成 | 不纳入本系统设计范围 |

## 3. 文档清单

| 文件 | 设计产物 | 主要内容 |
|---|---|---|
| `00_系统设计方案总览.md` | 总体设计方案 | 设计目标、范围、交付物映射、总体原则、核心约束 |
| `01_系统架构设计.md` | 系统架构图 | 分层架构、运行视图、框架/自主实现边界、部署视图 |
| `02_模块划分与职责设计.md` | 模块划分 | M1-M12 模块映射、包结构、依赖关系、模块职责 |
| `03_数据流与状态模型设计.md` | 数据流 | 阶段数据流、AgentState、核心数据对象、产物目录、上下文管理 |
| `04_LangGraph工作流设计.md` | LangGraph 图 | 主图、实现/测试/调试/修复子图、总图、路由规则、stream/checkpoint 设计 |
| `05_核心类与接口设计.md` | 核心类/接口 | 领域模型、服务类、工具接口、Adapter 接口、类图 |
| `06_工具调用与HITL设计.md` | 工具调用与 HITL | 工具分类、权限策略、双层 HITL、patch-first、审批数据结构 |
| `07_错误处理与重试设计.md` | 错误处理与重试 | 错误分类、节点重试、工具重试、修复闭环、恢复与降级 |
| `08_关键技术选型与配置设计.md` | 技术选型 | LangGraph、LangChain、OpenAI-compatible、SQLite、pytest、配置文件 |
| `09_运行产物与可复现设计.md` | 产物与可复现 | 输出目录、metadata、transcript、decision trace、resume、报告模板 |
| `10_Benchmark与扩展预留设计.md` | Benchmark/扩展设计 | BenchmarkRunner、benchmark.yaml、成功判定接口、Java 扩展预留 |

## 4. 推荐阅读顺序

1. 先读 `00_系统设计方案总览.md`，确认整体设计方向。
2. 再读 `01_系统架构设计.md` 和 `02_模块划分与职责设计.md`，理解系统组成。
3. 重点读 `04_LangGraph工作流设计.md`，这是本项目最关键的设计产物。
4. 实现前读 `05_核心类与接口设计.md`、`06_工具调用与HITL设计.md`、`07_错误处理与重试设计.md`。
5. 写配置、报告和演示时读 `08_关键技术选型与配置设计.md`、`09_运行产物与可复现设计.md`、`10_Benchmark与扩展预留设计.md`。
