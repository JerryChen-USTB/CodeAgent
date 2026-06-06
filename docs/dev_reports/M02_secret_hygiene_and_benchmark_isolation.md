# M02 安全预检与 Benchmark 隔离规则

## 目标

完成实现前的安全预检，确保本地密钥和运行产物不会被纳入版本控制，并把 benchmark case 的“原始目录只读、运行前复制到干净副本”规则同步到计划、设计、测试说明和样例配置。

## 主要变更

- 更新 `.gitignore`，忽略 `Software Engineering Project.txt`、`.env`、`.env.*`、密钥文件模式和 `codeagent_runs/`。
- 在 `docs/codex/plans.md`、`docs/codex/prompt.md`、`docs/codex/implement.md`、SRS、设计文档、benchmark README 和测试报告中补齐 clean-copy 规则。
- 将 BugsInPy case 的 `prepare_command` 与 `test_command` 改为 `-CaseDir {{CASE_DIR}}`，由后续 BenchmarkRunner 替换为运行副本目录。
- 明确 `expected_result.json`、`evaluation/`、`oracle_tests/` 属于 runner-only hidden material，不得作为被评测 Agent 的上下文。

## 验证命令

```powershell
git check-ignore "Software Engineering Project.txt" ".env" ".env.local" "codeagent_runs/example"
python -c "import yaml, pathlib; yaml.safe_load(pathlib.Path('benchmark/cases/bugsinpy_black_001/task_config.yaml').read_text(encoding='utf-8')); print('yaml-ok')"
rg -n "-CaseDir benchmark[/\\]cases[/\\]bugsinpy_black_001|bugsinpy-checkout .*benchmark[/\\]cases[/\\]bugsinpy_black_001[/\\]workspace|cd benchmark[/\\]selfbuilt[/\\]cases" docs benchmark --glob '!docs/_backups/**' --glob '!**/oracle_tests/**' --glob '!**/evaluation/**' --glob '!**/expected_result.json' --glob '!benchmark/**/workspace/**'
```

结果：忽略规则命中预期路径；BugsInPy task config YAML 可解析；定向搜索未发现可见命令仍直接把 prepare/test/checkout/cd 指向原始 case 目录。

## 复核结论

子代理质量复核在 2026-06-03 返回 PASS。M02 可关闭，后续从 M03 Python 包脚手架开始实现。

## 已知限制

`docs/_backups/` 中保留了修改前文档快照，仅用于变更追溯；后续检查当前文档时应继续排除该目录。
