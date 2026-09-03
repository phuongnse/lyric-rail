#requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$Plan,
    [switch]$IncludeModels,
    [switch]$SkipToolInstall,
    [switch]$SkipVerification,
    [switch]$Launch,
    [Parameter(DontShow = $true)]
    [switch]$Elevated,
    [Parameter(DontShow = $true)]
    [switch]$InstallToolsOnly,
    [ValidateSet("Auto", "Cpu", "Nvidia")]
    [string]$Acceleration = "Auto"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ToolConfigPath = Join-Path $ProjectRoot "config\windows-dev-tools.json"
if ($env:OS -ne "Windows_NT" -or -not [Environment]::Is64BitOperatingSystem) {
    throw "LyricRail bootstrap supports native 64-bit Windows only."
}
if (-not (Test-Path -LiteralPath $ToolConfigPath -PathType Leaf)) {
    throw "Windows tool declaration is missing: $ToolConfigPath"
}
$ToolConfig = Get-Content -LiteralPath $ToolConfigPath -Raw | ConvertFrom-Json
if ($ToolConfig.schemaVersion -ne 1 -or $ToolConfig.architecture -ne "x64") {
    throw "Unsupported Windows tool declaration schema or architecture."
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Label,
        [string]$WorkingDirectory = $ProjectRoot
    )
    Write-Host "==> $Label"
    Push-Location $WorkingDirectory
    try {
        & $FilePath @Arguments
        $exitCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($exitCode -ne 0) {
        throw "$Label failed with exit code $exitCode."
    }
}

function Get-Winget {
    $command = Get-Command winget.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $command) {
        throw "winget is required. Install or repair Microsoft App Installer, then retry."
    }
    return $command.Source
}

function Test-RealCommand {
    param([string]$Name)
    $command = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $command) { return $false }
    return -not $command.Source.StartsWith(
        (Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps"),
        [System.StringComparison]::OrdinalIgnoreCase
    )
}

function Test-ExecutableVersion {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string]$RequiredPrefix,
        [string[]]$Arguments = @("--version")
    )
    if (-not (Test-Path -LiteralPath $FilePath -PathType Leaf)) { return $false }
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = @(& $FilePath @Arguments 2>$null)
        $exitCode = $LASTEXITCODE
    } catch {
        return $false
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($exitCode -ne 0 -or $output.Count -eq 0) { return $false }
    return ([string]$output[0]).Trim().StartsWith(
        $RequiredPrefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )
}

function Test-CommandVersion {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$RequiredPrefix,
        [string[]]$Arguments = @("--version")
    )
    $command = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $command -or $command.Source.StartsWith(
        (Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps"),
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        return $false
    }
    return Test-ExecutableVersion -FilePath $command.Source -RequiredPrefix $RequiredPrefix -Arguments $Arguments
}

function Test-ExactToolVersionOutput {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Version,
        [Parameter(Mandatory = $true)][string]$Output
    )
    $versionPattern = "^$([Regex]::Escape($Name))\s+$([Regex]::Escape($Version))(?:\s|$)"
    return [bool]($Output -match $versionPattern)
}

function Refresh-CurrentPath {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $entries = @($machine, $user, $env:Path, (Join-Path $env:USERPROFILE ".cargo\bin"), (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links"))
    $env:Path = ($entries | Where-Object { $_ }) -join ';'
}

function Get-SmartAppControlMode {
    $policyPath = "HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy"
    try {
        $policy = Get-ItemProperty -LiteralPath $policyPath -Name "VerifiedAndReputablePolicyState" -ErrorAction Stop
        $state = [int]$policy.VerifiedAndReputablePolicyState
    } catch {
        return "Unknown"
    }
    switch ($state) {
        0 { return "Off" }
        1 { return "Enforced" }
        2 { return "Evaluation" }
        default { return "Unknown ($state)" }
    }
}

function Test-OfficialPython {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
        (Join-Path $env:ProgramFiles "Python312\python.exe")
    )
    $requiredPrefix = "Python $($ToolConfig.packages.python.requiredVersionPrefix)"
    return [bool]($candidates | Where-Object {
        Test-ExecutableVersion -FilePath $_ -RequiredPrefix $requiredPrefix
    } | Select-Object -First 1)
}

function Find-VsWhere {
    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"),
        (Join-Path $env:ProgramFiles "Microsoft Visual Studio\Installer\vswhere.exe")
    )
    return $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
}

function Test-MsvcTools {
    $vswhere = Find-VsWhere
    if (-not $vswhere) { return $false }
    $installation = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath | Select-Object -First 1
    $installationVersion = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationVersion | Select-Object -First 1
    $versionParts = ([string]$ToolConfig.packages.visualStudioBuildTools.installVersion).Split('.')
    $requiredPrefix = "$($versionParts[0]).$($versionParts[1])."
    return [bool]$installation -and ([string]$installationVersion).StartsWith(
        $requiredPrefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )
}

function Get-ToolPlan {
    $packages = $ToolConfig.packages
    return @(
        [pscustomobject]@{ Name = "Python 3.12"; Id = $packages.python.id; Version = $packages.python.installVersion; Installed = (Test-OfficialPython) },
        [pscustomobject]@{ Name = "Node.js 24 LTS"; Id = $packages.node.id; Version = $packages.node.installVersion; Installed = (Test-CommandVersion -Name "node.exe" -RequiredPrefix $packages.node.requiredVersionPrefix) },
        [pscustomobject]@{ Name = "Rustup"; Id = $packages.rustup.id; Version = $packages.rustup.installVersion; Installed = (Test-CommandVersion -Name "rustup.exe" -RequiredPrefix "rustup $($packages.rustup.installVersion)") },
        [pscustomobject]@{ Name = "MSVC Build Tools"; Id = $packages.visualStudioBuildTools.id; Version = $packages.visualStudioBuildTools.installVersion; Installed = (Test-MsvcTools) },
        [pscustomobject]@{ Name = "FFmpeg"; Id = $packages.ffmpeg.id; Version = $packages.ffmpeg.installVersion; Installed = ((Test-CommandVersion -Name "ffmpeg.exe" -RequiredPrefix "ffmpeg version $($packages.ffmpeg.installVersion)" -Arguments @("-version")) -and (Test-CommandVersion -Name "ffprobe.exe" -RequiredPrefix "ffprobe version $($packages.ffmpeg.installVersion)" -Arguments @("-version"))) }
    )
}

function Install-WingetPackage {
    param(
        [Parameter(Mandatory = $true)]$Package,
        [string]$Override = ""
    )
    $arguments = @(
        "install", "--exact", "--id", [string]$Package.id,
        "--version", [string]$Package.installVersion,
        "--source", [string]$ToolConfig.wingetSource,
        "--accept-package-agreements", "--accept-source-agreements",
        "--disable-interactivity"
    )
    $scopeProperty = $Package.PSObject.Properties["scope"]
    if ($scopeProperty -and $scopeProperty.Value) {
        $arguments += @("--scope", [string]$scopeProperty.Value)
    }
    if ($Override) { $arguments += @("--override", $Override) }
    Invoke-Checked -FilePath (Get-Winget) -Arguments $arguments -Label "Install $($Package.id)"
}

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

Refresh-CurrentPath
$declaredPlan = Get-ToolPlan
$smartAppControlMode = Get-SmartAppControlMode
if ($Plan) {
    [pscustomobject]@{
        schemaVersion = 1
        projectRoot = $ProjectRoot
        platform = "windows-x86_64"
        mutatesSystem = $false
        includeModels = [bool]$IncludeModels
        acceleration = $Acceleration
        pythonRuntime = $ToolConfig.python.runtime
        smartAppControl = [pscustomobject]@{
            mode = $smartAppControlMode
            nativeBuildCompatible = $smartAppControlMode -ne "Enforced"
        }
        packages = $declaredPlan
        generatedRoots = @(
            (Join-Path $ProjectRoot ".venv"),
            (Join-Path $ProjectRoot ".dev\target-windows")
        )
        profiles = @("frontend", "python", "rust", "security")
    } | ConvertTo-Json -Depth 6
    return
}

if ($smartAppControlMode -eq "Enforced") {
    throw @"
Smart App Control is enforcing and blocks unsigned executables produced by local Rust builds.
LyricRail cannot build or launch its native Tauri development binary while that policy is enforced.
The bootstrap will not disable Windows security or move build output to evade the policy.

If this is your development PC, open Windows Security > App & browser control > Smart App Control
settings, review Microsoft's warning, and choose Off. Alternatively, use a dedicated Windows
development machine or VM whose application-control policy permits local builds. Then run this
bootstrap again.
"@
}

$buildToolsPlan = $declaredPlan | Where-Object { $_.Id -eq [string]$ToolConfig.packages.visualStudioBuildTools.id } | Select-Object -First 1
$requiresElevation = $buildToolsPlan -and -not $buildToolsPlan.Installed
if (-not $SkipToolInstall -and $requiresElevation -and -not $Elevated -and -not (Test-Administrator)) {
    Write-Host "Official MSVC Build Tools require Administrator approval. Requesting one UAC elevation..."
    $argumentList = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", "`"$PSCommandPath`"",
        "-Elevated",
        "-InstallToolsOnly"
    )
    try {
        $child = Start-Process -FilePath "powershell.exe" -Verb RunAs -Wait -PassThru -ArgumentList $argumentList
    } catch {
        throw "Windows tool installation needs UAC approval. Re-run bootstrap and choose Yes. $($_.Exception.Message)"
    }
    if ($child.ExitCode -ne 0) {
        throw "Elevated Windows tool installation failed with exit code $($child.ExitCode)."
    }
    $SkipToolInstall = $true
}

if (-not $SkipToolInstall) {
    $packages = $ToolConfig.packages
    $status = @{}
    foreach ($item in $declaredPlan) { $status[$item.Id] = [bool]$item.Installed }
    if (-not $status[[string]$packages.python.id]) { Install-WingetPackage $packages.python }
    if (-not $status[[string]$packages.node.id]) { Install-WingetPackage $packages.node }
    if (-not $status[[string]$packages.rustup.id]) { Install-WingetPackage $packages.rustup }
    if (-not $status[[string]$packages.visualStudioBuildTools.id]) {
        $override = "--wait --passive --norestart --add $($packages.visualStudioBuildTools.workload) --includeRecommended"
        Install-WingetPackage $packages.visualStudioBuildTools -Override $override
    }
    if (-not $status[[string]$packages.ffmpeg.id]) { Install-WingetPackage $packages.ffmpeg }
}

Refresh-CurrentPath
$unavailableTools = @(Get-ToolPlan | Where-Object { -not $_.Installed })
if ($unavailableTools.Count -gt 0) {
    $descriptions = ($unavailableTools | ForEach-Object { "$($_.Name) $($_.Version)" }) -join ", "
    throw "Required official Windows tools are missing or version-incompatible: $descriptions. Run bootstrap without -SkipToolInstall and review -Plan output."
}

if ($InstallToolsOnly) {
    Write-Host "Official Windows tool installation completed."
    return
}

$environment = . (Join-Path $PSScriptRoot "windows_dev_environment.ps1") -PassThru -AllowMissing
$devRoot = Join-Path $ProjectRoot ".dev"
$venvRoot = Join-Path $ProjectRoot ".venv"
New-Item -ItemType Directory -Force -Path $devRoot | Out-Null

$basePython = $environment.Python
if ($basePython -and $basePython.StartsWith($venvRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    $basePython = $null
}
if (-not $basePython) {
    $baseCandidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
        (Join-Path $env:ProgramFiles "Python312\python.exe")
    )
    $basePython = $baseCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
}
if (-not $basePython) { throw "Official Python 3.12 was not found after bootstrap." }

$venvPython = Join-Path $venvRoot "Scripts\python.exe"
if ((Test-Path -LiteralPath $venvRoot) -and -not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    throw "Existing repository .venv is not a native Windows environment. Move or remove only $venvRoot after preserving anything you own, then run bootstrap again."
}
if (-not (Test-Path -LiteralPath $venvRoot)) {
    Invoke-Checked -FilePath $basePython -Arguments @("-m", "venv", $venvRoot) -Label "Create repository Windows virtual environment"
}
Invoke-Checked -FilePath $venvPython -Arguments @(
    (Join-Path $ProjectRoot "scripts\check_windows_python.py"),
    "--expected-prefix", $venvRoot
) -Label "Validate repository Python 3.12 x64 virtual environment"
Invoke-Checked -FilePath $venvPython -Arguments @(
    "-m", "pip", "install", "--isolated", "--disable-pip-version-check", "--no-input",
    "--upgrade", "--require-hashes", "--only-binary", ":all:",
    "-r", (Join-Path $ProjectRoot "requirements\windows-bootstrap.txt")
) -Label "Install hash-locked Windows packaging tool"
Invoke-Checked -FilePath $venvPython -Arguments @(
    "-m", "pip", "install", "--isolated", "--disable-pip-version-check", "--no-input",
    "--require-hashes", "--only-binary", ":all:", "-r", (Join-Path $ProjectRoot "requirements\process.txt")
) -Label "Install hash-locked engineering process authority"

$resolvedAcceleration = $Acceleration
if ($resolvedAcceleration -eq "Auto") {
    $resolvedAcceleration = if (Test-RealCommand "nvidia-smi.exe") { "Nvidia" } else { "Cpu" }
}
$runtime = $ToolConfig.python.runtime
$expectedBackend = $resolvedAcceleration.ToLowerInvariant()
$buildTag = if ($resolvedAcceleration -eq "Nvidia") {
    [string]$runtime.nvidiaBuildTag
} else {
    [string]$runtime.cpuBuildTag
}
$onnxVersion = if ($resolvedAcceleration -eq "Nvidia") {
    [string]$runtime.onnxGpuVersion
} else {
    [string]$runtime.onnxCpuVersion
}
$accelerationCheckArguments = @(
    (Join-Path $ProjectRoot "scripts\check_windows_acceleration.py"),
    "--expected", $expectedBackend,
    "--torch-version", [string]$runtime.torchVersion,
    "--torchaudio-version", [string]$runtime.torchaudioVersion,
    "--torchvision-version", [string]$runtime.torchvisionVersion,
    "--onnx-version", $onnxVersion,
    "--build-tag", $buildTag
)
if ($resolvedAcceleration -eq "Nvidia") {
    $accelerationCheckArguments += @("--cuda-major", [string]$runtime.nvidiaCudaMajor)
}

function Invoke-AccelerationCheck {
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = @(& $venvPython @accelerationCheckArguments 2>&1)
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    return [pscustomobject]@{
        Passed = $exitCode -eq 0
        Output = $output
    }
}

$accelerationCheck = Invoke-AccelerationCheck
if (-not $accelerationCheck.Passed) {
    Write-Host "Existing Python runtime does not match requested $resolvedAcceleration acceleration; installing the pinned coherent runtime."
    Invoke-Checked -FilePath $venvPython -Arguments @(
        "-m", "pip", "uninstall", "--yes", "onnxruntime", "onnxruntime-gpu"
    ) -Label "Remove mutually exclusive ONNX Runtime distributions before backend transition"
    $pytorchIndex = if ($resolvedAcceleration -eq "Nvidia") {
        [string]$runtime.nvidiaIndexUrl
    } else {
        [string]$runtime.cpuIndexUrl
    }
    Invoke-Checked -FilePath $venvPython -Arguments @(
        "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
        "--force-reinstall", "--only-binary", ":all:", "--index-url", $pytorchIndex,
        "torch==$($runtime.torchVersion)+$buildTag",
        "torchaudio==$($runtime.torchaudioVersion)+$buildTag",
        "torchvision==$($runtime.torchvisionVersion)+$buildTag"
    ) -Label "Install pinned $resolvedAcceleration PyTorch runtime"
    $onnxDistribution = if ($resolvedAcceleration -eq "Nvidia") { "onnxruntime-gpu" } else { "onnxruntime" }
    Invoke-Checked -FilePath $venvPython -Arguments @(
        "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
        "--only-binary", ":all:",
        "-c", (Join-Path $ProjectRoot "requirements\constraints-tested.txt"),
        "$onnxDistribution==$onnxVersion"
    ) -Label "Install pinned $resolvedAcceleration ONNX Runtime"
}
$extras = @($ToolConfig.python.baseExtras)
$extras += if ($resolvedAcceleration -eq "Nvidia") {
    [string]$ToolConfig.python.gpuSeparationExtra
} else {
    [string]$ToolConfig.python.cpuSeparationExtra
}
$projectRequirement = "$ProjectRoot[$($extras -join ',')]"
Invoke-Checked -FilePath $venvPython -Arguments @(
    "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
    "-c", (Join-Path $ProjectRoot "requirements\constraints-tested.txt"),
    "--editable", $projectRequirement, "pip-audit"
) -Label "Install LyricRail Python development and processing dependencies"

$accelerationCheck = Invoke-AccelerationCheck
if (-not $accelerationCheck.Passed) {
    $details = ($accelerationCheck.Output | ForEach-Object { [string]$_ }) -join " "
    throw "$resolvedAcceleration acceleration runtime validation failed. $details"
}
$accelerationReport = (($accelerationCheck.Output | Select-Object -Last 1) | ConvertFrom-Json)

$environment = . (Join-Path $PSScriptRoot "windows_dev_environment.ps1") -PassThru
Invoke-Checked -FilePath $environment.Npm -Arguments @("ci") -Label "Install locked frontend dependencies"

$rust = $ToolConfig.rust
Invoke-Checked -FilePath $environment.Rustup -Arguments @(
    "toolchain", "install", [string]$rust.stableToolchain,
    "--profile", "minimal", "--component", "clippy", "--component", "rustfmt",
    "--target", [string]$rust.target
) -Label "Install pinned stable Rust toolchain"
Invoke-Checked -FilePath $environment.Rustup -Arguments @("default", [string]$rust.stableToolchain) -Label "Select pinned stable Rust toolchain"
Invoke-Checked -FilePath $environment.Rustup -Arguments @(
    "toolchain", "install", [string]$rust.nightlyToolchain, "--profile", "minimal"
) -Label "Install pinned nightly Rust toolchain"

function Ensure-CargoTool {
    param([string]$Name, [string]$Version)
    $binary = Join-Path $env:USERPROFILE ".cargo\bin\$Name.exe"
    $installed = $false
    if (Test-Path -LiteralPath $binary -PathType Leaf) {
        $previousErrorActionPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            $versionOutput = ((& $binary --version 2>$null) | Select-Object -First 1) -join ""
            $exitCode = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $previousErrorActionPreference
        }
        $installed = $exitCode -eq 0 -and (Test-ExactToolVersionOutput -Name $Name -Version $Version -Output $versionOutput)
    }
    if (-not $installed) {
        Invoke-Checked -FilePath $environment.Cargo -Arguments @(
            "install", $Name, "--version", $Version, "--locked"
        ) -Label "Install $Name $Version"
    }
}
Ensure-CargoTool -Name "cargo-audit" -Version ([string]$rust.cargoAuditVersion)
Ensure-CargoTool -Name "cargo-fuzz" -Version ([string]$rust.cargoFuzzVersion)
Invoke-Checked -FilePath $environment.Cargo -Arguments @(
    "fetch", "--locked", "--target", [string]$rust.target
) -Label "Fetch locked Rust dependencies"

if ($IncludeModels) {
    Invoke-Checked -FilePath $venvPython -Arguments @((Join-Path $ProjectRoot "scripts\install_models.py")) -Label "Download and verify pinned processing models"
}

$processctl = Join-Path $venvRoot "Scripts\processctl.exe"
if (-not $SkipVerification) {
    Invoke-Checked -FilePath $processctl -Arguments @(
        "adoption", "check", "--project-root", $ProjectRoot,
        "--requirements-lock", (Join-Path $ProjectRoot "requirements\process.txt")
    ) -Label "Validate engineering-process adoption"
    foreach ($profile in @("frontend", "python", "rust", "security")) {
        Invoke-Checked -FilePath $processctl -Arguments @(
            "doctor", "--project-root", $ProjectRoot, "--profile", $profile
        ) -Label "Check $profile profile on Windows"
        Invoke-Checked -FilePath $processctl -Arguments @(
            "verify", "--project-root", $ProjectRoot, "--profile", $profile
        ) -Label "Verify $profile profile on Windows"
    }
}

$receipt = [pscustomobject]@{
    schemaVersion = 1
    completedAt = [DateTimeOffset]::Now.ToString("o")
    platform = "windows-x86_64"
    projectRoot = $ProjectRoot
    python = (& $venvPython --version 2>&1) -join " "
    node = (& $environment.Node --version 2>&1) -join " "
    rust = (& $environment.Cargo --version 2>&1) -join " "
    ffmpeg = (& $environment.Ffmpeg -version 2>&1 | Select-Object -First 1) -join " "
    acceleration = $resolvedAcceleration
    accelerationEvidence = $accelerationReport.state
    modelsInstalled = [bool]$IncludeModels
    profilesVerified = -not $SkipVerification
    cargoTargetDirectory = $env:CARGO_TARGET_DIR
}
$receiptPath = Join-Path $devRoot "windows-bootstrap.json"
$receipt | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $receiptPath -Encoding UTF8
Write-Host "Windows-native LyricRail environment is ready: $receiptPath"

if ($Launch) {
    & (Join-Path $PSScriptRoot "dev_player_windows.ps1")
    exit $LASTEXITCODE
}
