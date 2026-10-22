# OCS-in-a-Kit installer launcher for Windows PowerShell.
# Usage: powershell -ExecutionPolicy Bypass -File install.ps1 [options]
$ErrorActionPreference = "Stop"
$script = Join-Path $PSScriptRoot "ocs_kit_installer.py"
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 $script @args
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python $script @args
} else {
    Write-Host "Python 3.8 or newer is required. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH')."
    exit 1
}
exit $LASTEXITCODE
