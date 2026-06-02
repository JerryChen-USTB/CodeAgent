param(
    [string]$CondaEnv = "codeagent-bugsinpy-py383",
    [string]$PythonVersion = "3.8.3"
)

$ErrorActionPreference = "Stop"

$BashCommand = @"
set -euo pipefail

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required in WSL to install Miniconda" >&2
  exit 2
fi

if [ ! -x "`$HOME/miniconda3/bin/conda" ]; then
  cd /tmp
  if [ ! -f Miniconda3-latest-Linux-x86_64.sh ]; then
    curl -fsSLo Miniconda3-latest-Linux-x86_64.sh https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
  fi
  bash Miniconda3-latest-Linux-x86_64.sh -b -p "`$HOME/miniconda3"
fi

source "`$HOME/miniconda3/etc/profile.d/conda.sh"

if conda env list | grep -q "^$CondaEnv[[:space:]]"; then
  echo "[conda] env exists: $CondaEnv"
else
  conda create -y --override-channels -c conda-forge -n "$CondaEnv" python="$PythonVersion" pip setuptools wheel dos2unix
fi

conda run -n "$CondaEnv" python --version
conda run -n "$CondaEnv" dos2unix --version | head -1
"@

$BashCommand = $BashCommand -replace "`r`n", "`n"
$BashCommand | wsl -- bash -lc "tr -d '\r' | bash -s"
exit $LASTEXITCODE
