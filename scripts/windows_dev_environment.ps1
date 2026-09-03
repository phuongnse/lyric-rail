#requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$PassThru,
    [switch]$AllowMissing
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ($env:OS -ne "Windows_NT") {
    throw "LyricRail Windows development requires native Windows PowerShell."
}
if ($ProjectRoot.StartsWith("\\wsl", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "The repository must be opened from a native Windows path, not a WSL share."
}

function Add-ProcessPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $normalized = [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
    $entries = @($env:Path -split ';' | Where-Object { $_ })
    if ($entries | Where-Object { $_.TrimEnd('\') -ieq $normalized }) { return }
    $env:Path = "$normalized;$env:Path"
}

function Set-DeduplicatedProcessPath {
    param([string[]]$Sources)
    $seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    $unique = [System.Collections.Generic.List[string]]::new()
    foreach ($source in $Sources) {
        if ([string]::IsNullOrWhiteSpace($source)) { continue }
        foreach ($entry in ($source -split ';')) {
            $value = $entry.Trim().Trim('"')
            if (-not $value) { continue }
            $key = if ($value.Length -gt 3) { $value.TrimEnd('\') } else { $value }
            if ($seen.Add($key)) { $unique.Add($value) }
        }
    }
    $env:Path = $unique -join ';'
}

function Refresh-ProcessPath {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $current = $env:Path
    Set-DeduplicatedProcessPath -Sources @($current, $machine, $user)
    Add-ProcessPath (Join-Path $env:USERPROFILE ".cargo\bin")
    Add-ProcessPath (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links")
    Add-ProcessPath (Join-Path $env:ProgramFiles "nodejs")
}

function Find-VsWhere {
    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"),
        (Join-Path $env:ProgramFiles "Microsoft Visual Studio\Installer\vswhere.exe")
    )
    return $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}

function Import-MsvcEnvironment {
    param([switch]$Optional)
    $vswhere = Find-VsWhere
    if (-not $vswhere) {
        if ($Optional) { return $null }
        throw "Visual Studio vswhere.exe is missing. Run scripts\bootstrap_windows.ps1."
    }
    $installation = (& $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath | Select-Object -First 1)
    if (-not $installation) {
        if ($Optional) { return $null }
        throw "MSVC x64 tools are missing. Run scripts\bootstrap_windows.ps1."
    }
    $devCommand = Join-Path $installation "Common7\Tools\VsDevCmd.bat"
    if (-not (Test-Path -LiteralPath $devCommand)) {
        throw "VsDevCmd.bat is missing from $installation"
    }
    $command = "`"$devCommand`" -no_logo -arch=x64 -host_arch=x64 >nul && set"
    $lines = & $env:ComSpec /d /s /c $command
    if ($LASTEXITCODE -ne 0) {
        throw "VsDevCmd failed with exit code $LASTEXITCODE."
    }
    foreach ($line in $lines) {
        $separator = $line.IndexOf('=')
        if ($separator -le 0) { continue }
        $name = $line.Substring(0, $separator)
        $value = $line.Substring($separator + 1)
        Set-Item -Path "Env:$name" -Value $value
    }
    return $installation
}

function Resolve-Application {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [string[]]$Candidates = @(),
        [switch]$Optional
    )
    foreach ($candidate in $Candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    $command = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command -and -not $command.Source.StartsWith((Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps"), [System.StringComparison]::OrdinalIgnoreCase)) {
        return $command.Source
    }
    if ($Optional) { return $null }
    throw "$Name is unavailable. Run scripts\bootstrap_windows.ps1."
}

function Find-OfficialPython {
    $venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) { return $venvPython }
    $registryCandidates = @(
        "HKCU:\Software\Python\PythonCore\3.12\InstallPath",
        "HKLM:\Software\Python\PythonCore\3.12\InstallPath",
        "HKLM:\Software\WOW6432Node\Python\PythonCore\3.12\InstallPath"
    )
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
        (Join-Path $env:ProgramFiles "Python312\python.exe")
    )
    foreach ($key in $registryCandidates) {
        if (-not (Test-Path $key)) { continue }
        $properties = Get-ItemProperty $key
        $executableProperty = $properties.PSObject.Properties["ExecutablePath"]
        $defaultProperty = $properties.PSObject.Properties["(default)"]
        if ($executableProperty -and $executableProperty.Value) {
            $candidates += [string]$executableProperty.Value
        } elseif ($defaultProperty -and $defaultProperty.Value) {
            $candidates += (Join-Path ([string]$defaultProperty.Value) "python.exe")
        }
    }
    return Resolve-Application -Name "python.exe" -Candidates $candidates -Optional:$AllowMissing
}

Refresh-ProcessPath
$visualStudio = Import-MsvcEnvironment -Optional:$AllowMissing
Refresh-ProcessPath

$python = Find-OfficialPython
$venvRoot = Join-Path $ProjectRoot ".venv"
if ($python -and $python.StartsWith($venvRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $validationOutput = @(& $python (Join-Path $PSScriptRoot "check_windows_python.py") --expected-prefix $venvRoot 2>&1)
        $validationExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($validationExitCode -ne 0) {
        if ($AllowMissing) {
            $python = $null
        } else {
            $details = ($validationOutput | ForEach-Object { [string]$_ }) -join " "
            throw "Repository .venv is not Python 3.12 x64 for native Windows. $details"
        }
    }
}
$node = Resolve-Application -Name "node.exe" -Candidates @((Join-Path $env:ProgramFiles "nodejs\node.exe")) -Optional:$AllowMissing
$npm = Resolve-Application -Name "npm.cmd" -Candidates @((Join-Path $env:ProgramFiles "nodejs\npm.cmd")) -Optional:$AllowMissing
$rustup = Resolve-Application -Name "rustup.exe" -Candidates @((Join-Path $env:USERPROFILE ".cargo\bin\rustup.exe")) -Optional:$AllowMissing
$cargo = Resolve-Application -Name "cargo.exe" -Candidates @((Join-Path $env:USERPROFILE ".cargo\bin\cargo.exe")) -Optional:$AllowMissing
$ffmpeg = Resolve-Application -Name "ffmpeg.exe" -Candidates @() -Optional:$AllowMissing
$ffprobe = Resolve-Application -Name "ffprobe.exe" -Candidates @() -Optional:$AllowMissing

$env:LYRICRAIL_HOME = $ProjectRoot
$env:CARGO_TARGET_DIR = Join-Path $ProjectRoot ".dev\target-windows"
if ($python) { $env:LYRICRAIL_PYTHON = $python; Add-ProcessPath (Split-Path $python) }
if ($node) { Add-ProcessPath (Split-Path $node) }
if ($npm) { Add-ProcessPath (Split-Path $npm) }
if ($ffmpeg) { $env:LYRICRAIL_FFMPEG = $ffmpeg; Add-ProcessPath (Split-Path $ffmpeg) }
if ($ffprobe) { $env:LYRICRAIL_FFPROBE = $ffprobe; Add-ProcessPath (Split-Path $ffprobe) }

$result = [pscustomobject]@{
    ProjectRoot = $ProjectRoot
    Python = $python
    Node = $node
    Npm = $npm
    Rustup = $rustup
    Cargo = $cargo
    Ffmpeg = $ffmpeg
    Ffprobe = $ffprobe
    VisualStudio = $visualStudio
    CargoTargetDirectory = $env:CARGO_TARGET_DIR
}

if ($PassThru) { return $result }
Write-Host "LyricRail Windows development environment is ready."
Write-Host "  Python: $python"
Write-Host "  Node:   $node"
Write-Host "  Cargo:  $cargo"
Write-Host "  MSVC:   $visualStudio"
Write-Host "  FFmpeg: $ffmpeg"
