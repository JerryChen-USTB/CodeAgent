param(
    [string]$CaseDir = "benchmark\cases\bugsinpy_black_001",
    [string]$Project = "black",
    [string]$CondaEnv = "codeagent-bugsinpy-py383",
    [string]$TestTimeout = "300s",
    [switch]$SkipCompile,
    [switch]$AllowTestFailure
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path -Path (Join-Path $PSScriptRoot "..")).Path
$CasePath = Join-Path $RepoRoot $CaseDir
$CaseId = Split-Path -Path $CasePath -Leaf
$ProjectPath = Join-Path $CasePath ("workspace\" + $Project)

if (-not (Test-Path -Path (Join-Path $ProjectPath "bugsinpy_run_test.sh"))) {
    throw "BugsInPy workspace is not prepared. Run scripts\prepare_bugsinpy_wsl_conda.ps1 first."
}

function ConvertTo-WslPath {
    param([string]$Path)

    $Resolved = (Resolve-Path -Path $Path).Path
    $WindowsPathArg = $Resolved -replace "\\", "/"
    $Converted = & wsl -- wslpath -a $WindowsPathArg
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to convert path to WSL path: $Resolved"
    }
    return (($Converted | Select-Object -First 1).Trim())
}

$RepoWsl = ConvertTo-WslPath $RepoRoot
$ProjectWsl = ConvertTo-WslPath $ProjectPath
$SkipCompileFlag = if ($SkipCompile) { "1" } else { "0" }

$BashCommand = @"
set -euo pipefail

source "`$HOME/miniconda3/etc/profile.d/conda.sh"
conda activate "$CondaEnv"

repo_root="$RepoWsl"
source_project_dir="$ProjectWsl"
case_id="$CaseId"
project="$Project"
bugsinpy_bin="`$repo_root/dataset/BugsInPy/framework/bin"
run_root="`$HOME/.cache/codeagent/bugsinpy/`$case_id"
run_workspace="`$run_root/workspace"
project_dir="`$run_workspace/`$project"

dos2unix -q "`$bugsinpy_bin/bugsinpy-checkout" "`$bugsinpy_bin/bugsinpy-compile" "`$bugsinpy_bin/bugsinpy-test" "`$bugsinpy_bin/bugsinpy-info"

if [[ "$SkipCompileFlag" != "1" || ! -d "`$project_dir/env" ]]; then
  rm -rf "`$run_root"
  mkdir -p "`$run_workspace"
  cp -a "`$source_project_dir" "`$run_workspace/"
fi

cd "`$project_dir"

if [[ "$SkipCompileFlag" != "1" ]]; then
  echo "[bugsinpy] official compile"
  bash "`$bugsinpy_bin/bugsinpy-compile" -w "`$project_dir"
else
  echo "[bugsinpy] skip compile; reusing `$project_dir/env"
fi

python setup.py --version >/dev/null

rm -f "`$project_dir/bugsinpy_fail.txt" "`$project_dir/bugsinpy_alltest.txt" "`$project_dir/bugsinpy_singletest.txt"
rm -f "`$source_project_dir/bugsinpy_fail.txt" "`$source_project_dir/bugsinpy_alltest.txt" "`$source_project_dir/bugsinpy_singletest.txt"

echo "[bugsinpy] official test"
set +e
timeout --foreground "$TestTimeout" bash "`$bugsinpy_bin/bugsinpy-test" -w "`$project_dir"
status=`$?
set -e

if [[ "`$status" -ne 0 ]]; then
  echo "[bugsinpy] official test command exited with `$status" >&2
  exit "`$status"
fi

if [[ -s "`$project_dir/bugsinpy_fail.txt" ]]; then
  cp "`$project_dir/bugsinpy_fail.txt" "`$source_project_dir/bugsinpy_fail.txt" || true
  echo "[bugsinpy] relevant test failed" >&2
  cat "`$project_dir/bugsinpy_fail.txt" >&2
  exit 1
fi

echo "[bugsinpy] relevant test passed"
"@

$BashCommand = $BashCommand -replace "`r`n", "`n"
$BashCommand | wsl -- bash -lc "tr -d '\r' | bash -s"
$ExitCode = $LASTEXITCODE

if ($ExitCode -ne 0 -and $AllowTestFailure) {
    Write-Host "[bugsinpy] test failed as expected for the initial buggy version." -ForegroundColor Yellow
    exit 0
}

exit $ExitCode
