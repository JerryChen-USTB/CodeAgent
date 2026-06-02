# BugsInPy WSL + conda 运行指南

本文档说明如何为 `bugsinpy_black_001` 样例准备并运行本机测试环境。当前方案不使用 Docker，而是在 WSL Ubuntu 中使用 conda 提供 Python 3.8.3，并直接调用 BugsInPy 官方脚本。

## 1. 当前样例

样例路径：

```text
benchmark/cases/bugsinpy_black_001/
```

该样例来自 BugsInPy：

```text
project = black
bug_id = 1
buggy_commit = 26c9465a22c732ab1e17b0dec578fa3432e9b558
fixed_commit = c0a7582e3d4cc8bec3b7f5a6c52b36880dcb57d7
python_version = 3.8.3
official_test = python -m unittest -q tests.test_black.BlackTestCase.test_works_in_mono_process_only_environment
```

## 2. 为什么必须走官方 checkout

BugsInPy 的 checkout 不是简单 `git checkout buggy_commit`。官方 `bugsinpy-checkout` 会先 checkout fixed commit，把 `bug.info` 中声明的测试文件复制出来，再 checkout buggy commit，并把 fixed 版本中的 regression test 放回 buggy workspace。

因此，官方 `run_test.sh` 指向的测试名可能在纯 buggy commit 中不存在，但在官方 checkout 后的 workspace 中存在。`black` bug 1 就是这种情况。

## 3. 环境准备

当前已在 WSL 中创建 conda 环境：

```text
codeagent-bugsinpy-py383
```

环境内容：

```text
Python 3.8.3
dos2unix
pip/setuptools/wheel
```

如果需要重建环境，运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_bugsinpy_wsl_conda.ps1
```

该脚本会在 WSL 用户目录安装 Miniconda，并通过 `conda-forge` 创建 Python 3.8.3 环境。

## 4. 准备 workspace

运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\prepare_bugsinpy_wsl_conda.ps1 -CaseDir benchmark\cases\bugsinpy_black_001
```

该脚本在 WSL 中执行官方命令：

```bash
bugsinpy-checkout -p black -v 0 -i 1 -w benchmark/cases/bugsinpy_black_001/workspace
```

官方 checkout 生成的可编辑项目目录是：

```text
benchmark/cases/bugsinpy_black_001/workspace/black/
```

## 5. 运行官方测试

验证初始 buggy 版本时运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_bugsinpy_wsl_conda.ps1 -CaseDir benchmark\cases\bugsinpy_black_001 -AllowTestFailure
```

智能体修复后运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_bugsinpy_wsl_conda.ps1 -CaseDir benchmark\cases\bugsinpy_black_001
```

该脚本会先把 `workspace/black/` 复制到 WSL Linux 文件系统下的临时运行目录，避免在 Windows 盘 `/mnt/d` 上创建大量 venv 小文件。随后在 WSL conda 环境中执行官方命令：

```bash
bugsinpy-compile -w ~/.cache/codeagent/bugsinpy/bugsinpy_black_001/workspace/black
bugsinpy-test -w ~/.cache/codeagent/bugsinpy/bugsinpy_black_001/workspace/black
```

实际运行目录形如：

```text
~/.cache/codeagent/bugsinpy/bugsinpy_black_001/workspace/black/
```

由于官方 `bugsinpy-test` 主要通过 `bugsinpy_fail.txt` 记录失败，PowerShell 包装脚本会在官方测试结束后检查该文件，并把结果转换为 benchmark runner 更容易使用的 exit code。失败日志会同步回项目的 `workspace/black/bugsinpy_fail.txt`。

`black` 项目还需要 `_black_version.py`。该文件由项目自己的 `setup.py` 中 `setuptools_scm` 配置生成，因此测试脚本会在官方 compile 后执行一次 `python setup.py --version` 来生成该文件，不手写版本内容。

## 6. 与智能体的关系

智能体输入是普通目录：

```text
benchmark/cases/bugsinpy_black_001/input/
benchmark/cases/bugsinpy_black_001/workspace/black/
```

智能体在 Windows 侧编辑 `workspace/black/`，测试命令通过 `wsl` 进入 WSL。运行测试前，脚本会把当前 `workspace/black/` 同步到 WSL 缓存目录，官方 BugsInPy 脚本在缓存目录中执行。

## 7. 相关文件

```text
scripts/setup_bugsinpy_wsl_conda.ps1
scripts/prepare_bugsinpy_wsl_conda.ps1
scripts/run_bugsinpy_wsl_conda.ps1
benchmark/cases/bugsinpy_black_001/task_config.yaml
```
