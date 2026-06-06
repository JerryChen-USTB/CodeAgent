param(
    [string]$CaseDir = "",
    [string]$Project = "black",
    [string]$BugId = "1",
    [string]$Version = "0",
    [string]$CondaEnv = "codeagent-bugsinpy-py383",
    [int]$WslCommandTimeoutSeconds = 60
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path -Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($CaseDir)) {
    throw "CaseDir is required. Pass a clean copied benchmark case directory."
}
$CasePath = Join-Path $RepoRoot $CaseDir
$WorkspacePath = Join-Path $CasePath "workspace"

function Test-AllowedCasePath {
    param([string]$Path)

    $Resolved = (Resolve-Path -Path $Path).Path
    $BenchmarkRoot = (Resolve-Path -Path (Join-Path $RepoRoot "benchmark")).Path
    $LegacyRunsRoot = (Join-Path $BenchmarkRoot "codeagent_runs")
    $CentralBenchmarkRunsRoot = (Join-Path $RepoRoot "codeagent_runs\benchmarks")
    $UnderBenchmark = $Resolved.StartsWith($BenchmarkRoot, [System.StringComparison]::OrdinalIgnoreCase)
    $IsCaseTemplate = $Resolved.Contains("\benchmark\cases\", [System.StringComparison]::OrdinalIgnoreCase)
    $IsCaseWorkspace = $Resolved.Contains("\case_workspaces\", [System.StringComparison]::OrdinalIgnoreCase)
    $UnderLegacyRuns = $Resolved.StartsWith($LegacyRunsRoot, [System.StringComparison]::OrdinalIgnoreCase)
    $UnderCentralBenchmarkRuns = $Resolved.StartsWith($CentralBenchmarkRunsRoot, [System.StringComparison]::OrdinalIgnoreCase)
    return (
        ($UnderBenchmark -and $IsCaseTemplate) -or
        (($UnderLegacyRuns -or $UnderCentralBenchmarkRuns) -and $IsCaseWorkspace)
    )
}

function Remove-Tree {
    param([string]$Path)

    if (-not (Test-Path -Path $Path)) {
        return
    }

    $Resolved = (Resolve-Path -Path $Path).Path
    if (-not (Test-AllowedCasePath -Path $Resolved)) {
        throw "Refusing to remove path outside allowed benchmark workspaces: $Resolved"
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
$CaseWsl = ConvertTo-WslPath $CasePath
if (-not (Test-AllowedCasePath -Path $CasePath)) {
    throw "Refusing to prepare path outside allowed benchmark workspaces: $CasePath"
}
Test-WslBashAvailable

Remove-Tree $WorkspacePath
New-Item -ItemType Directory -Force -Path $WorkspacePath | Out-Null

$BashCommand = @"
set -euo pipefail

source "`$HOME/miniconda3/etc/profile.d/conda.sh"
conda activate "$CondaEnv"

repo_root="$RepoWsl"
case_dir="$CaseWsl"
bugsinpy_bin="`$repo_root/dataset/BugsInPy/framework/bin"

if [[ "`$case_dir" != "`$repo_root"/benchmark/cases/* && "`$case_dir" != "`$repo_root"/benchmark/codeagent_runs/*/case_workspaces/* && "`$case_dir" != "`$repo_root"/codeagent_runs/benchmarks/*/case_workspaces/* ]]; then
  echo "Refusing to prepare path outside allowed benchmark workspaces: `$case_dir" >&2
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
$ExitCode = Invoke-WslBash -InputText $BashCommand -TimeoutSeconds $WslCommandTimeoutSeconds
exit $ExitCode
