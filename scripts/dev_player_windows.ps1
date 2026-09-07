#requires -Version 5.1
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$environment = . (Join-Path $PSScriptRoot "windows_dev_environment.ps1") -PassThru
$venvPython = Join-Path $environment.ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    throw "Repository .venv is missing. Run scripts\bootstrap_windows.ps1 first."
}
$env:LYRICRAIL_PYTHON = $venvPython
Push-Location $environment.ProjectRoot
try {
    & $environment.Npm run dev:player
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
