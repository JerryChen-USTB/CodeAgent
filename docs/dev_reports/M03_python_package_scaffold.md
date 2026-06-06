# M03 Python 包脚手架与依赖基线

## 目标

建立最小可运行的 Python 包结构，让项目可以被导入、可以通过 `python -m codeagent --help` 显示 CLI 帮助，并提供基础测试目录与依赖声明。

## 主要文件

- `pyproject.toml`：声明构建后端、项目元数据、运行依赖、开发依赖和 `codeagent` 控制台入口。
- `codeagent/__init__.py`：提供包版本读取与源码运行 fallback。
- `codeagent/__main__.py`：支持 `python -m codeagent`。
- `codeagent/cli/app.py`：最小 Typer + Rich CLI 应用。
- `tests/test_package_smoke.py`：覆盖 import smoke、项目 metadata 和 CLI help。
- `examples/README.md`：建立稳定 examples 目录。

## 依赖决策

官方 LangChain/LangGraph 文档当前推荐安装 `langgraph`、`langchain` 和 provider integration 包；项目依赖因此对齐到 v1 生态：

- `langchain>=1.0,<2.0`
- `langchain-openai>=1.0,<2.0`
- `langgraph>=1.0,<2.0`
- `langgraph-checkpoint-sqlite>=3.1,<4.0`
- `openai>=2.26,<3.0`

参考文档：

- https://docs.langchain.com/oss/python/langgraph/install
- https://docs.langchain.com/oss/python/langchain/install
- https://reference.langchain.com/python/langgraph/checkpoints/

## 验证命令

```powershell
python -m pytest -q
python -m codeagent --help
python -m pip install -e . --dry-run
```

结果：`pytest` 通过 4 个测试；CLI help 和 `--version` 正常显示；editable install dry-run 可解析并显示会安装 CodeAgent 与 v1 LangChain/LangGraph 依赖。

## 复核结论

M03 规格复核返回 PASS。代码质量复核指出 `plans.md` 中 M03 状态和运行日志滞后；后续额外验证还发现 `--version` 没有触发 Typer callback。两项均已修复并重新验证。

## 已知限制

当前全局 Python 解释器存在若干预装包版本冲突，`python -m pip check` 会失败。M03 未执行实际安装，仅做 dry-run；后续真实运行建议使用项目专用虚拟环境。
