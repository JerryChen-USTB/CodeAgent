# BugsInPy: black bug 1 metadata case

This optional case comes from BugsInPy project `black`, bug id `1`.

It is disabled in the default benchmark because BugsInPy requires dynamic checkout of the real upstream project and a dependency environment compatible with Python 3.8.3.

Prepare the editable workspace with BugsInPy official checkout through WSL + conda:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\prepare_bugsinpy_wsl_conda.ps1 -CaseDir <copied_case_dir>
```

The wrapper runs BugsInPy `bugsinpy-checkout` directly in WSL. This matters because BugsInPy first checks out the fixed commit to copy the regression test file, then checks out the buggy commit and restores that test file into the buggy workspace.

After preparation, the agent should read and edit:

```text
<copied_case_dir>/workspace/black/
```

The official relevant test for this bug is the command in `bugsinpy_run_test.sh`:

```bash
python -m unittest -q tests.test_black.BlackTestCase.test_works_in_mono_process_only_environment
```

The benchmark test command runs BugsInPy official compile and test scripts:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_bugsinpy_wsl_conda.ps1 -CaseDir <copied_case_dir>
```
