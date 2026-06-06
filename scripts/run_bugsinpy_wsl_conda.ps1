param(
    [string]$CaseDir = "",
    [string]$Project = "black",
    [string]$CondaEnv = "codeagent-bugsinpy-py383",
    [string]$TestTimeout = "300s",
    [int]$WslCommandTimeoutSeconds = 60,
    [switch]$SkipCompile,
    [switch]$AllowTestFailure
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path -Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($CaseDir)) {
    throw "CaseDir is required. Pass a clean copied benchmark case directory."
}
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
    $Converted = Invoke-WslPath -WindowsPathArg $WindowsPathArg
    if ([string]::IsNullOrWhiteSpace($Converted)) {
        throw "Failed to convert path to WSL path: $Resolved"
    }
    return $Converted
}

function Invoke-WslPath {
    param(
        [string]$WindowsPathArg,
        [int]$TimeoutSeconds = 20
    )

    $StartInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $StartInfo.FileName = "wsl"
    $EscapedPath = $WindowsPathArg.Replace('"', '\"')
    $StartInfo.Arguments = "-- wslpath -a `"$EscapedPath`""
    $StartInfo.RedirectStandardOutput = $true
    $StartInfo.RedirectStandardError = $true
    $StartInfo.UseShellExecute = $false
    $StartInfo.CreateNoWindow = $true

    $Process = [System.Diagnostics.Process]::new()
    $Process.StartInfo = $StartInfo
    [void]$Process.Start()
    if (-not $Process.WaitForExit($TimeoutSeconds * 1000)) {
        try { $Process.Kill() } catch {}
        throw "WSL path conversion timed out after $TimeoutSeconds seconds."
    }
    $Stdout = $Process.StandardOutput.ReadToEnd()
    $Stderr = $Process.StandardError.ReadToEnd()
    if ($Process.ExitCode -ne 0) {
        throw "Failed to convert path to WSL path: $Stderr"
    }
    return (($Stdout | Select-Object -First 1).Trim())
}

function Test-WslBashAvailable {
    param([int]$TimeoutSeconds = 20)

    $StartInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $StartInfo.FileName = "wsl"
    $StartInfo.Arguments = "-- bash -lc `"echo CODEAGENT_WSL_READY`""
    $StartInfo.RedirectStandardOutput = $true
    $StartInfo.RedirectStandardError = $true
    $StartInfo.UseShellExecute = $false
    $StartInfo.CreateNoWindow = $true

    $Process = [System.Diagnostics.Process]::new()
    $Process.StartInfo = $StartInfo
    [void]$Process.Start()
    if (-not $Process.WaitForExit($TimeoutSeconds * 1000)) {
        try { $Process.Kill() } catch {}
        throw "WSL bash preflight timed out after $TimeoutSeconds seconds."
    }
    $Stdout = $Process.StandardOutput.ReadToEnd()
    $Stderr = $Process.StandardError.ReadToEnd()
    if ($Process.ExitCode -ne 0 -or -not $Stdout.Contains("CODEAGENT_WSL_READY")) {
        throw "WSL bash preflight failed: $Stderr"
    }
}

function Invoke-WslBash {
    param(
        [string]$InputText,
        [int]$TimeoutSeconds
    )

    $StartInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $StartInfo.FileName = "wsl"
    $StartInfo.Arguments = "-- bash -lc `"tr -d '\r' | bash -s`""
    $StartInfo.RedirectStandardInput = $true
    $StartInfo.RedirectStandardOutput = $true
    $StartInfo.RedirectStandardError = $true
    $StartInfo.UseShellExecute = $false
    $StartInfo.CreateNoWindow = $true

    $Process = [System.Diagnostics.Process]::new()
    $Process.StartInfo = $StartInfo
    [void]$Process.Start()
    $Process.StandardInput.Write($InputText)
    $Process.StandardInput.Close()
    if (-not $Process.WaitForExit($TimeoutSeconds * 1000)) {
        try { $Process.Kill() } catch {}
        throw "WSL bash command timed out after $TimeoutSeconds seconds."
    }
    $Stdout = $Process.StandardOutput.ReadToEnd()
    $Stderr = $Process.StandardError.ReadToEnd()
    if (-not [string]::IsNullOrWhiteSpace($Stdout)) {
        Write-Host $Stdout
    }
    if (-not [string]::IsNullOrWhiteSpace($Stderr)) {
        Write-Host $Stderr
    }
    return $Process.ExitCode
}

$RepoWsl = ConvertTo-WslPath $RepoRoot
$ProjectWsl = ConvertTo-WslPath $ProjectPath
$SkipCompileFlag = if ($SkipCompile) { "1" } else { "0" }
Test-WslBashAvailable

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
$ExitCode = Invoke-WslBash -InputText $BashCommand -TimeoutSeconds $WslCommandTimeoutSeconds

if ($ExitCode -ne 0 -and $AllowTestFailure) {
    Write-Host "[bugsinpy] test failed as expected for the initial buggy version." -ForegroundColor Yellow
    exit 0
}

exit $ExitCode
