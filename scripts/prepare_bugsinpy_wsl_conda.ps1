param(
    [string]$CaseDir = "benchmark\cases\bugsinpy_black_001",
    [string]$Project = "black",
    [string]$BugId = "1",
    [string]$Version = "0",
    [string]$CondaEnv = "codeagent-bugsinpy-py383"
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path -Path (Join-Path $PSScriptRoot "..")).Path
$CasePath = Join-Path $RepoRoot $CaseDir
$WorkspacePath = Join-Path $CasePath "workspace"

function Remove-Tree {
    param([string]$Path)

    if (-not (Test-Path -Path $Path)) {
        return
    }

    $Resolved = (Resolve-Path -Path $Path).Path
    $Allowed = (Resolve-Path -Path (Join-Path $RepoRoot "benchmark")).Path
    if (-not $Resolved.StartsWith($Allowed, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove path outside benchmark: $Resolved"
    }

    Get-ChildItem -LiteralPath $Resolved -Recurse -Force -ErrorAction SilentlyContinue |
        ForEach-Object {
            try {
                $_.Attributes = 'Normal'
            } catch {}
        }
    Remove-Item -LiteralPath $Resolved -Recurse -Force
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
$CaseWsl = ConvertTo-WslPath $CasePath

Remove-Tree $WorkspacePath
New-Item -ItemType Directory -Force -Path $WorkspacePath | Out-Null

$BashCommand = @"
set -euo pipefail

source "`$HOME/miniconda3/etc/profile.d/conda.sh"
conda activate "$CondaEnv"

repo_root="$RepoWsl"
case_dir="$CaseWsl"
bugsinpy_bin="`$repo_root/dataset/BugsInPy/framework/bin"

if [[ "`$case_dir" != "`$repo_root"/benchmark/cases/* ]]; then
  echo "Refusing to prepare path outside benchmark cases: `$case_dir" >&2
  exit 2
fi

dos2unix -q "`$bugsinpy_bin/bugsinpy-checkout" "`$bugsinpy_bin/bugsinpy-compile" "`$bugsinpy_bin/bugsinpy-test" "`$bugsinpy_bin/bugsinpy-info"

mkdir -p "`$case_dir/workspace"

bash "`$bugsinpy_bin/bugsinpy-checkout" \
  -p "$Project" \
  -v "$Version" \
  -i "$BugId" \
  -w "`$case_dir/workspace"

test -f "`$case_dir/workspace/$Project/bugsinpy_run_test.sh"
echo "[bugsinpy] workspace ready: `$case_dir/workspace/$Project"
"@

$BashCommand = $BashCommand -replace "`r`n", "`n"
$BashCommand | wsl -- bash -lc "tr -d '\r' | bash -s"
exit $LASTEXITCODE
