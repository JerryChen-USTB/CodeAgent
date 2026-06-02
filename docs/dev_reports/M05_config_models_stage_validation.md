# M05 配置模型与阶段校验

## 目标

实现 CodeAgent 的配置层基础：用 Pydantic 定义任务、模型、运行时、权限和 benchmark 配置模型，并校验阶段选择、路径、语言与测试框架。

## 主要文件

- `codeagent/config/defaults.py`：集中默认值。
- `codeagent/config/validators.py`：阶段别名归一化和连续性校验。
- `codeagent/config/schema.py`：Pydantic v2 配置模型。
- `codeagent/config/loader.py`：YAML/JSON 配置读取、相对路径解析和路径校验。
- `tests/unit/config/`：阶段校验和 loader 单元测试。

## 关键行为

- 支持 `implementation/testing/debugging` 等 benchmark 别名，统一归一为 `implement/test/debug`。
- 阶段必须按 `implement -> test -> debug -> repair` 顺序，且多阶段选择必须连续。
- 支持 YAML 和 JSON task config / benchmark config。
- 支持课程 task config 的扁平字段，也支持当前 benchmark case 的 `project.path`、`workspace.path`、嵌套 `test_command` 等字段。
- 仅允许 `language=python`，测试框架允许 `pytest` 和当前 benchmark 使用的 `unittest`。
- 只解析隐藏路径名称和路径，不读取 `oracle_tests/`、`evaluation/`、`expected_result.json` 内容。

## 验证命令

```powershell
python -m pytest tests/unit/config -q
python -m pytest -q
```

结果：config 单元测试 32 个通过；全量测试 40 个通过。

## 复核状态

M05 规格复核 PASS。已采纳非阻塞建议，补充 JSON benchmark config 单测。质量复核指出 evaluator-only 元数据可能通过 `TaskConfig.extra` 泄漏，以及 `stages: null` 报错不清晰；已修复并通过质量复审。

## 已知限制

`..` 越界、benchmark 可见路径 allowlist 和敏感文件拒读属于 M07 context/sensitive filtering 的范围；M05 只负责基本存在性和结构校验。
