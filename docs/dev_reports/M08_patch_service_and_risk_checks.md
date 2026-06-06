# M08 PatchService 与 Patch 风险检查

## 目标

实现不依赖 Git 的统一 diff 服务，支撑后续实现、测试、修复阶段的 patch-first 流程：生成、解析、校验、摘要、风险识别和应用 patch。

## 主要文件

- `codeagent/services/patch_service.py`：PatchService 核心实现。
- `codeagent/services/__init__.py`：服务层导出。
- `codeagent/tools/patch_tools.py`：工具层薄封装。
- `tests/unit/tools/test_patch_service.py`：PatchService 单元测试。

## 关键行为

- 支持由 `FileChange` 生成 unified diff。
- 支持解析 add / modify / delete patch，并验证 hunk header 行数。
- 默认拒绝越界路径、绝对路径、路径穿越、敏感文件和生成目录。
- 拒绝重复 target patch，避免同一文件多个 patch section 覆盖彼此。
- 应用 patch 前会先完成全部 planned changes 的 preflight；执行阶段遇到 `OSError` 时回滚已修改文件。
- 重复应用同一个 patch 时返回 `already_applied=True`，不会再次修改文件。
- 风险报告覆盖删除测试、添加 skip/xfail、硬编码样例、大规模 patch、删除测试断言。

## 对齐检查

已回顾 SRS 中 FR-23、FR-24、FR-26、FR-60、FR-64、NFR-10、NFR-12、NFR-13、NFR-14，以及设计文档中的 PatchService、patch-first、patch 校验规则、HITL 审批交接和 benchmark forbidden patch patterns。

## 验证命令

```powershell
python -m pytest tests/unit/tools/test_patch_service.py -q
python -m pytest -q
python -m codeagent --help
codeagent --help
```

结果：PatchService 单元测试 11 个通过；全量测试 76 个通过；两个 CLI help 命令退出码均为 0。

## 复核状态

规格复核初次发现两个 P2：大规模 patch 未进入 high-risk 报告、hunk header 行数未校验；均已修复并通过 re-review。质量复核初次发现两个 P1 和一个 P2：多文件 partial apply 风险、重复 target 接受、删除测试断言未标风险；均已修复并通过 re-review。

## 已知限制

当前 PatchService 采用保守 unified diff 子集，不支持 rename patch 或二进制 patch；后续工作流接入 HITL 后，`apply_patch` 的调用应由审批节点控制。
