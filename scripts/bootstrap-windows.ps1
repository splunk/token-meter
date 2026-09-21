[CmdletBinding()]
param(
    [string]$SourceRoot = "",
    [string]$InstallRoot = "",
    [switch]$NoOpenDashboard,
    [switch]$BackendOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepositoryUrl = "https://github.com/splunk/token-meter.git"
$RepositoryBranch = "main"
$DashboardUrl = "http://127.0.0.1:8722"

function Fail([string]$Message) {
    throw "Token Meter bootstrap failed: $Message"
}

function Refresh-ProcessPath {
    $MachinePath = [Environment]::GetEnvironmentVariable("Path", [EnvironmentVariableTarget]::Machine)
    $UserPath = [Environment]::GetEnvironmentVariable("Path", [EnvironmentVariableTarget]::User)
    $PathValues = @($MachinePath, $UserPath, $env:Path) |
        Where-Object { $_ } |
        Select-Object -Unique
    $env:Path = [string]::Join(";", [string[]]$PathValues)
}

function Get-UsableGit {
    $Command = Get-Command git.exe -CommandType Application -ErrorAction SilentlyContinue
    if (-not $Command) {
        return $null
    }
    try {
        & $Command.Source --version *> $null
        if ($LASTEXITCODE -eq 0) {
            return $Command
        }
    } catch { }
    return $null
}

function Get-WinGet {
    return Get-Command winget.exe -CommandType Application -ErrorAction SilentlyContinue
}

function Install-GitPrerequisite {
    $WinGet = Get-WinGet
    if (-not $WinGet) {
        Fail "WinGet is required to install Git.MinGit. Install or update Microsoft App Installer and rerun this command."
    }
    $Arguments = @(
        "install", "--exact", "--id", "Git.MinGit", "--source", "winget",
        "--silent", "--accept-package-agreements", "--accept-source-agreements",
        "--disable-interactivity"
    )
    Write-Host "Prerequisites: installing Git.MinGit"
    & $WinGet.Source @Arguments
    if ($LASTEXITCODE -ne 0) {
        Fail "WinGet could not install Git.MinGit."
    }
    Refresh-ProcessPath
}

function Invoke-GitText {
    param(
        $GitCommand,
        [string[]]$Arguments,
        [string]$FailureMessage
    )
    $Output = @(& $GitCommand.Source @Arguments 2>$null)
    if ($LASTEXITCODE -ne 0) {
        Fail $FailureMessage
    }
    return (($Output | ForEach-Object { [string]$_ }) -join [Environment]::NewLine).Trim()
}

function Invoke-Git {
    param(
        $GitCommand,
        [string[]]$Arguments,
        [string]$FailureMessage
    )
    & $GitCommand.Source @Arguments
    if ($LASTEXITCODE -ne 0) {
        Fail $FailureMessage
    }
}

function Assert-ManagedCheckout {
    param(
        $GitCommand,
        [string]$CheckoutRoot
    )
    if (-not (Test-Path -LiteralPath (Join-Path $CheckoutRoot ".git") -PathType Container)) {
        Fail "the source path is not a Git checkout. Choose an empty source path or move the existing directory."
    }
    $Origin = Invoke-GitText $GitCommand @(
        "-C", $CheckoutRoot, "remote", "get-url", "origin"
    ) "the source checkout has no readable origin remote."
    if ($Origin -ne $RepositoryUrl) {
        Fail "the source checkout belongs to a different origin. Move it or choose another source path."
    }
    $Branch = Invoke-GitText $GitCommand @(
        "-C", $CheckoutRoot, "rev-parse", "--abbrev-ref", "HEAD"
    ) "the source checkout branch could not be read."
    if ($Branch -ne $RepositoryBranch) {
        Fail "the source checkout must be on main."
    }
    $Upstream = Invoke-GitText $GitCommand @(
        "-C", $CheckoutRoot, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"
    ) "the source checkout must track origin/main."
    if ($Upstream -ne "origin/main") {
        Fail "the source checkout must track origin/main."
    }
    $Status = Invoke-GitText $GitCommand @(
        "-C", $CheckoutRoot, "status", "--porcelain", "--untracked-files=all"
    ) "the source checkout state could not be read."
    if ($Status) {
        Fail "the source checkout has local changes. Commit, stash, or move them before rerunning."
    }
    $Head = Invoke-GitText $GitCommand @(
        "-C", $CheckoutRoot, "rev-parse", "HEAD"
    ) "the source checkout has no valid revision."
    if (-not $Head) {
        Fail "the source checkout has no valid revision."
    }
}

function Test-PathContains {
    param(
        [string]$ParentPath,
        [string]$CandidatePath
    )
    $Prefix = $ParentPath.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    return $CandidatePath.StartsWith($Prefix, [StringComparison]::OrdinalIgnoreCase)
}

if ($env:OS -ne "Windows_NT") {
    Fail "this bootstrap requires Windows."
}
if (-not $env:LOCALAPPDATA) {
    Fail "LOCALAPPDATA is unavailable."
}

if (-not $SourceRoot) {
    $SourceRoot = $env:TOKEN_METER_SOURCE_ROOT
}
if (-not $SourceRoot) {
    $SourceRoot = Join-Path $env:LOCALAPPDATA "Token Meter\source"
}
if (-not $InstallRoot) {
    $InstallRoot = $env:TOKEN_METER_INSTALL_ROOT
}
if (-not $InstallRoot) {
    $InstallRoot = Join-Path $env:LOCALAPPDATA "Token Meter\runtime"
}

$SourceRoot = [System.IO.Path]::GetFullPath($SourceRoot)
$InstallRoot = [System.IO.Path]::GetFullPath($InstallRoot)
$SourceParent = Split-Path -Parent $SourceRoot
$InstallParent = Split-Path -Parent $InstallRoot
$SourcePathRoot = [System.IO.Path]::GetPathRoot($SourceRoot).TrimEnd('\', '/')
$InstallPathRoot = [System.IO.Path]::GetPathRoot($InstallRoot).TrimEnd('\', '/')
if (-not $SourceParent -or $SourceRoot.TrimEnd('\', '/') -eq $SourcePathRoot -or
    (Split-Path -Leaf $SourceRoot) -eq "runtime") {
    Fail "the source path must be a specific directory not named runtime."
}
if (-not $InstallParent -or $InstallRoot.TrimEnd('\', '/') -eq $InstallPathRoot -or
    (Split-Path -Leaf $InstallRoot) -ne "runtime") {
    Fail "the runtime path must be a specific directory named runtime."
}
if ($SourceRoot -eq $InstallRoot -or
    (Test-PathContains $SourceRoot $InstallRoot) -or
    (Test-PathContains $InstallRoot $SourceRoot)) {
    Fail "the source and runtime paths must be separate sibling directories."
}

Write-Host "Prerequisites: checking Git"
Refresh-ProcessPath
$Git = Get-UsableGit
if (-not $Git) {
    Install-GitPrerequisite
    $Git = Get-UsableGit
    if (-not $Git) {
        Fail "Git could not be verified after WinGet reported success. Start a new terminal and rerun this command."
    }
}

Write-Host "Source: preparing managed checkout"
if (Test-Path -LiteralPath $SourceRoot) {
    Assert-ManagedCheckout $Git $SourceRoot
    Invoke-Git $Git @(
        "-C", $SourceRoot, "fetch", "--quiet", "--prune", "--no-tags", "origin"
    ) "the source checkout could not fetch origin. Check the network connection and rerun."
    Invoke-Git $Git @(
        "-C", $SourceRoot, "merge", "--quiet", "--ff-only", "origin/main"
    ) "the source checkout has diverged from origin/main. Resolve it manually before rerunning."
    Assert-ManagedCheckout $Git $SourceRoot
} else {
    New-Item -ItemType Directory -Path $SourceParent -Force | Out-Null
    $StagingRoot = "$SourceRoot.installing-$([Guid]::NewGuid().ToString('N'))"
    $StagingParent = [System.IO.Path]::GetFullPath((Split-Path -Parent $StagingRoot))
    if ($StagingParent -ne [System.IO.Path]::GetFullPath($SourceParent) -or
        (Test-Path -LiteralPath $StagingRoot)) {
        Fail "a safe temporary source path could not be created."
    }
    try {
        Invoke-Git $Git @(
            "clone", "--quiet", "--branch", $RepositoryBranch, "--single-branch",
            $RepositoryUrl, $StagingRoot
        ) "the source checkout could not be downloaded. Check the network connection and rerun."
        Assert-ManagedCheckout $Git $StagingRoot
        if (Test-Path -LiteralPath $SourceRoot) {
            Fail "the source path was created by another process. Rerun after inspecting it."
        }
        [System.IO.Directory]::Move($StagingRoot, $SourceRoot)
    } finally {
        if (Test-Path -LiteralPath $StagingRoot) {
            Remove-Item -LiteralPath $StagingRoot -Recurse -Force
        }
    }
}

$Wrapper = Join-Path $SourceRoot "scripts\install-windows.cmd"
if (-not (Test-Path -LiteralPath $Wrapper -PathType Leaf)) {
    Fail "the managed source is incomplete: scripts\install-windows.cmd is missing."
}

Write-Host "Runtime: delegating verified installation"
Write-Host "Managed source: $SourceRoot"
$PreviousSourceOverride = $env:TOKEN_METER_SOURCE_ROOT
try {
    $env:TOKEN_METER_SOURCE_ROOT = $SourceRoot
    $InstallerArguments = @("-InstallRoot", $InstallRoot)
    if ($BackendOnly) {
        $InstallerArguments += "-BackendOnly"
    }
    & $Wrapper @InstallerArguments
    if ($LASTEXITCODE -ne 0) {
        Fail "the Token Meter runtime installer failed."
    }
} finally {
    $env:TOKEN_METER_SOURCE_ROOT = $PreviousSourceOverride
}

if (-not $NoOpenDashboard) {
    Start-Process $DashboardUrl | Out-Null
}
