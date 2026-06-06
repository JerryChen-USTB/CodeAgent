# M06 运行上下文、输出目录与产物索引

## 目标

创建每次运行的独立目录和基础审计文件，为后续 workflow、checkpoint、报告和 benchmark 聚合提供稳定落点。

## 主要文件

- `codeagent/runtime/run_context.py`：生成 `run_id`、创建目录树、写入 metadata、规范化 task config 和 checkpoint 占位库。
- `codeagent/reports/artifact_store.py`：维护 `artifacts_index.json`，支持记录、查找、按阶段查找、写入和加载。
- `codeagent/reports/transcript.py`：追加 timestamped JSONL 事件。
- `tests/unit/runtime/`：覆盖运行目录、metadata、artifact store 和 JSONL recorder。

## 关键行为

- 每次运行创建唯一 `codeagent_runs/<run_id>/`，不覆盖已有目录。
- 初始化根文件：`metadata.json`、`task_config.yaml`、`checkpoints.sqlite`、`transcript.jsonl`、`decision_trace.jsonl`、`artifacts_index.json`、`final_report.md`。
- 初始化目录：`implementation/`、`testing/`、`debugging/`、`repair/`、`benchmark/`。
- metadata 只记录模型配置和 `api_key_env`，不记录 API key 明文。
- unknown secret-like 字段不会进入 metadata 或规范化 task config。

## 验证命令

```powershell
python -m pytest tests/unit/runtime -q
python -m pytest -q
```

结果：runtime 单元测试 12 个通过；全量测试 52 个通过。

## 复核状态

M06 规格复核初次未确认 artifact/JSONL 测试覆盖；已补充初始化索引和未知 secret-like 字段回归测试，并通过 re-review。质量复核指出 artifact path 不能把 run_dir 外路径静默退化为 basename；已改为 fail-closed，并补充 out-of-run absolute path、`..` traversal、run_dir 内 absolute path 回归测试。质量复审 APPROVED。

## 已知限制

手动 smoke 期间曾创建忽略目录 `codeagent_runs/_smoke_tmp`，Windows 对该目录枚举/清理出现超时。该目录被 Git 忽略，不影响提交；后续应优先使用 pytest `tmp_path` 执行 smoke。
