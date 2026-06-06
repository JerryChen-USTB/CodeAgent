# M07 项目上下文工具与敏感过滤

## 目标

实现只读项目上下文工具：扫描项目结构、读取文本文件、搜索代码，并默认跳过敏感文件、生成目录和 benchmark 隐藏路径。

## 主要文件

- `codeagent/context/sensitive_filter.py`：敏感路径、生成目录、visible/hidden allowlist 判定。
- `codeagent/context/file_reader.py`：安全读取文本并截断长内容。
- `codeagent/context/path_utils.py`：逐目录安全遍历，遇到不可访问目录时跳过该目录并继续 sibling。
- `codeagent/context/code_search.py`：安全关键词搜索。
- `codeagent/context/scanner.py`：扫描 Python 项目结构并记录 skipped paths。
- `tests/unit/context/`：覆盖过滤、读取、搜索和扫描。

## 关键行为

- 默认拒绝 `.env`、`.pem`、`.key`、`.p12`、`.pfx`、`.crt`、`.cer`，以及文件名中包含 `secret`、`token`、`credential` 的路径。
- 默认跳过 `.git`、venv、`__pycache__`、build/dist、node_modules、`codeagent_runs` 等生成目录。
- FileReader 在读取前调用 `ensure_allowed()`，不会读取被拒路径内容。
- CodeSearcher 在读取前跳过 denied paths，并限制文件大小和结果数量。
- Scanner/Searcher 使用逐目录遍历；某个 sibling 目录不可访问时不会丢掉已经发现的正常文件，也不会递归进入 denied 目录子树。
- CodeSearcher 对显式 hidden/denied 搜索根直接返回空结果；benchmark case 根目录作为 visible roots 的父目录时仍可用于搜索可见子目录。
- benchmark 模式可通过 `visible_roots` / `hidden_roots` 限制 Agent 只看 `input/` 和运行副本 `workspace/`，不看 `evaluation/` / `oracle_tests/` / `expected_result.json`。

## 对齐检查

已回顾 SRS 中 FR-20、FR-21、FR-22、FR-28、NFR-13、DR-02，以及设计文档中的 `scan_project`、`read_file`、`search_code` 和 benchmark hidden path 隔离规则。

## 验证命令

```powershell
python -m pytest tests/unit/context -q
python -m pytest -q
```

结果：context 单元测试 13 个通过；全量测试 65 个通过。实际仓库 scanner/search smoke 只输出计数，`hidden_match_paths` 为 0。

## 复核状态

M07 规格复核初次指出 `.crt` / `.cer` 证书文件未被默认拒绝；已修复并通过 re-review。质量复核初次指出 `sorted(root.rglob("*"))` 会在中途 `OSError` 时丢失已发现路径；已改为逐目录安全遍历、denied 目录剪枝和 hidden-root 直接拒绝，并补充回归测试。质量 re-review 已 APPROVED，无 P0/P1/P2 问题。

## 已知限制

当前搜索是关键词逐行匹配，不做 AST 或 ripgrep 级索引；后续调试和上下文管理阶段可扩展为符号级搜索和摘要。
