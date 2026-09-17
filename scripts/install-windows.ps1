[CmdletBinding()]
param(
    [string]$InstallRoot = "",
    [int]$ReadinessTimeoutSeconds = 0,
    [switch]$BackendOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Fail([string]$Message) {
    throw "Token Meter installation failed: $Message"
}

function Write-Utf8File([string]$Path, [string]$Value) {
    [System.IO.File]::WriteAllText(
        $Path,
        $Value,
        [System.Text.UTF8Encoding]::new($false)
    )
}

function Test-Python([string]$Path) {
    if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $false
    }
    try {
        & $Path -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)" 2>$null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Refresh-ProcessPath {
    $MachinePath = [Environment]::GetEnvironmentVariable("Path", [EnvironmentVariableTarget]::Machine)
    $UserPath = [Environment]::GetEnvironmentVariable("Path", [EnvironmentVariableTarget]::User)
    $PathValues = @($MachinePath, $UserPath, $env:Path) |
        Where-Object { $_ } |
        Select-Object -Unique
    $env:Path = [string]::Join(";", [string[]]$PathValues)
}

function Get-PythonFromLauncher([string]$LauncherPath) {
    if (-not $LauncherPath -or
        -not (Test-Path -LiteralPath $LauncherPath -PathType Leaf)) {
        return
    }
    try {
        $Inventory = @(& $LauncherPath -0p 2>$null)
        if ($LASTEXITCODE -ne 0) {
            return
        }
        foreach ($Line in $Inventory) {
            if ([string]$Line -match '([A-Za-z]:\\.*?python(?:\d+(?:\.\d+)?)?\.exe)') {
                $Matches[1].Trim()
            }
        }
    } catch { }
}

function Get-RegisteredPythonCandidates {
    $RegistryRoots = @(
        "Registry::HKEY_CURRENT_USER\Software\Python",
        "Registry::HKEY_LOCAL_MACHINE\Software\Python",
        "Registry::HKEY_LOCAL_MACHINE\Software\WOW6432Node\Python"
    )
    foreach ($RegistryRoot in $RegistryRoots) {
        if (-not (Test-Path -LiteralPath $RegistryRoot -PathType Container)) {
            continue
        }
        Get-ChildItem -LiteralPath $RegistryRoot -ErrorAction SilentlyContinue |
            ForEach-Object {
                Get-ChildItem -LiteralPath $_.PSPath -ErrorAction SilentlyContinue
            } |
            ForEach-Object {
                $InstallPathKey = Join-Path $_.PSPath "InstallPath"
                if (Test-Path -LiteralPath $InstallPathKey -PathType Container) {
                    try {
                        $InstallKey = Get-Item -LiteralPath $InstallPathKey
                        $ExecutablePath = [string]$InstallKey.GetValue("ExecutablePath")
                        if ($ExecutablePath) {
                            $ExecutablePath
                        }
                        $InstallDirectory = [string]$InstallKey.GetValue("")
                        if ($InstallDirectory) {
                            Join-Path $InstallDirectory "python.exe"
                        }
                    } catch { }
                }
            }
    }
}

function Get-PythonCandidates {
    $Candidates = [System.Collections.Generic.List[string]]::new()
    if ($env:TOKEN_METER_PYTHON) {
        $Candidates.Add($env:TOKEN_METER_PYTHON)
    }
    foreach ($Name in @("python3.exe", "python.exe")) {
        $Command = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue
        if ($Command) {
            $Candidates.Add($Command.Source)
        }
    }

    $LauncherCandidates = [System.Collections.Generic.List[string]]::new()
    foreach ($Name in @("py.exe", "pymanager.exe")) {
        $Command = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue
        if ($Command) {
            $LauncherCandidates.Add($Command.Source)
        }
    }
    if ($env:LOCALAPPDATA) {
        $LauncherCandidates.Add((Join-Path $env:LOCALAPPDATA "Programs\Python\Launcher\py.exe"))
    }
    if ($env:WINDIR) {
        $LauncherCandidates.Add((Join-Path $env:WINDIR "py.exe"))
    }
    $LauncherCandidates |
        Select-Object -Unique |
        ForEach-Object { Get-PythonFromLauncher $_ } |
        ForEach-Object { $Candidates.Add($_) }
    Get-RegisteredPythonCandidates |
        ForEach-Object { $Candidates.Add($_) }

    if ($env:LOCALAPPDATA) {
        $LocalPythonBase = Join-Path $env:LOCALAPPDATA "Programs\Python"
        if (Test-Path -LiteralPath $LocalPythonBase -PathType Container) {
            Get-ChildItem -LiteralPath $LocalPythonBase -Filter python.exe -File -Recurse |
                Sort-Object FullName -Descending |
                ForEach-Object { $Candidates.Add($_.FullName) }
        }
    }
    foreach ($ProgramFilesRoot in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if (-not $ProgramFilesRoot -or
            -not (Test-Path -LiteralPath $ProgramFilesRoot -PathType Container)) {
            continue
        }
        Get-ChildItem -LiteralPath $ProgramFilesRoot -Filter "Python*" -Directory |
            ForEach-Object {
                Get-ChildItem -LiteralPath $_.FullName -Filter python.exe -File -Recurse
            } |
            Sort-Object FullName -Descending |
            ForEach-Object { $Candidates.Add($_.FullName) }
    }
    return $Candidates
}

function Find-CompatiblePython {
    return Get-PythonCandidates |
        Select-Object -Unique |
        Where-Object { Test-Python $_ } |
        Select-Object -First 1
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

function Install-Prerequisite([string]$PackageId, [switch]$UserScope) {
    $WinGet = Get-WinGet
    if (-not $WinGet) {
        Fail "WinGet is required to install $PackageId. Install or update Microsoft App Installer and rerun this command."
    }
    $Arguments = @(
        "install", "--exact", "--id", $PackageId, "--source", "winget",
        "--silent", "--accept-package-agreements", "--accept-source-agreements",
        "--disable-interactivity"
    )
    if ($UserScope) {
        $Arguments += @("--scope", "user")
    }
    Write-Host "Installing missing prerequisite: $PackageId"
    & $WinGet.Source @Arguments
    if ($LASTEXITCODE -ne 0) {
        Fail "WinGet could not install $PackageId."
    }
    Refresh-ProcessPath
}

function Stop-InstalledServer([string]$RuntimeRoot) {
    $PidPath = Join-Path $RuntimeRoot "meter.pid"
    if (-not (Test-Path -LiteralPath $PidPath -PathType Leaf)) {
        return
    }
    $ServerPid = 0
    if (-not [int]::TryParse((Get-Content -LiteralPath $PidPath -Raw).Trim(), [ref]$ServerPid)) {
        Remove-Item -LiteralPath $PidPath -Force
        return
    }
    $Process = Get-CimInstance Win32_Process -Filter "ProcessId = $ServerPid" -ErrorAction SilentlyContinue
    if (-not $Process) {
        Remove-Item -LiteralPath $PidPath -Force
        return
    }
    $ExpectedMeter = [System.IO.Path]::GetFullPath((Join-Path $RuntimeRoot "meter.py"))
    if ([string]$Process.CommandLine -notlike "*$ExpectedMeter*") {
        Fail "the saved server process ID belongs to a different program."
    }
    Stop-Process -Id $ServerPid -Force
    try {
        Wait-Process -Id $ServerPid -Timeout 10 -ErrorAction SilentlyContinue
    } catch {
        # The process may have exited between Stop-Process and Wait-Process.
    }
    Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
}

function Stop-InstalledTray([string]$RuntimeRoot) {
    $PidPath = Join-Path $RuntimeRoot "tray.pid"
    if (-not (Test-Path -LiteralPath $PidPath -PathType Leaf)) {
        return
    }
    $TrayPid = 0
    if (-not [int]::TryParse((Get-Content -LiteralPath $PidPath -Raw).Trim(), [ref]$TrayPid)) {
        Remove-Item -LiteralPath $PidPath -Force
        return
    }
    $Process = Get-CimInstance Win32_Process -Filter "ProcessId = $TrayPid" -ErrorAction SilentlyContinue
    if (-not $Process) {
        Remove-Item -LiteralPath $PidPath -Force
        return
    }
    $ExpectedTray = [System.IO.Path]::GetFullPath((Join-Path $RuntimeRoot "scripts\run-tray.ps1"))
    if ([string]$Process.CommandLine -notlike "*$ExpectedTray*") {
        Fail "the saved tray process ID belongs to a different program."
    }
    Stop-Process -Id $TrayPid -Force
    try {
        Wait-Process -Id $TrayPid -Timeout 10 -ErrorAction SilentlyContinue
    } catch { }
    Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $RuntimeRoot "tray.status.json") -Force -ErrorAction SilentlyContinue
}

function Copy-Directory([string]$Source, [string]$Destination) {
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    Get-ChildItem -LiteralPath $Source -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $Destination -Recurse -Force
    }
}

if ($env:OS -ne "Windows_NT") {
    Fail "the Windows installer requires Windows."
}

$SourceRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if (-not $InstallRoot) {
    $InstallRoot = $env:TOKEN_METER_INSTALL_ROOT
}
if (-not $InstallRoot) {
    if (-not $env:LOCALAPPDATA) {
        Fail "LOCALAPPDATA is unavailable."
    }
    $InstallRoot = Join-Path $env:LOCALAPPDATA "Token Meter\runtime"
}
$InstallRoot = [System.IO.Path]::GetFullPath($InstallRoot)
$InstallParent = Split-Path -Parent $InstallRoot
$PathRoot = [System.IO.Path]::GetPathRoot($InstallRoot).TrimEnd('\', '/')
if (-not $InstallParent -or $InstallRoot.TrimEnd('\', '/') -eq $PathRoot -or
    (Split-Path -Leaf $InstallRoot) -ne "runtime") {
    Fail "the runtime path must be a specific directory named runtime."
}
if (Test-Path -LiteralPath $InstallRoot -PathType Container) {
    $ExistingEntries = @(Get-ChildItem -LiteralPath $InstallRoot -Force)
    $RecognizedRuntime = (
        (Test-Path -LiteralPath (Join-Path $InstallRoot "meter.py") -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $InstallRoot "page.html") -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $InstallRoot "scripts\start-token-meter.ps1") -PathType Leaf)
    )
    if ($ExistingEntries.Count -gt 0 -and -not $RecognizedRuntime) {
        Fail "the runtime path contains files that do not belong to Token Meter."
    }
}

if ($ReadinessTimeoutSeconds -le 0) {
    $ConfiguredTimeout = 0
    if ($env:TOKEN_METER_READINESS_TIMEOUT_SECONDS) {
        [int]::TryParse($env:TOKEN_METER_READINESS_TIMEOUT_SECONDS, [ref]$ConfiguredTimeout) | Out-Null
    }
    $ReadinessTimeoutSeconds = if ($ConfiguredTimeout -gt 0) { $ConfiguredTimeout } else { 600 }
}

$null = Refresh-ProcessPath
$Git = Get-UsableGit
if (-not $Git) {
    Install-Prerequisite "Git.MinGit"
    $Git = Get-UsableGit
    if (-not $Git) {
        Fail "Git could not be verified after WinGet reported success. Start a new terminal and rerun this command."
    }
}

$PythonExe = Find-CompatiblePython
if (-not $PythonExe) {
    Install-Prerequisite "Python.Python.3.14" -UserScope
    $PythonExe = Find-CompatiblePython
    if (-not $PythonExe) {
        Fail "Python could not be verified after WinGet reported success. Start a new terminal and rerun this command."
    }
}
$PythonExe = [System.IO.Path]::GetFullPath($PythonExe)
$PythonwCandidate = Join-Path (Split-Path -Parent $PythonExe) "pythonw.exe"
$PythonwExe = if (Test-Path -LiteralPath $PythonwCandidate -PathType Leaf) {
    $PythonwCandidate
} else {
    $PythonExe
}

$RuntimeManifest = Join-Path $SourceRoot "runtime-manifest.txt"
if (-not (Test-Path -LiteralPath $RuntimeManifest -PathType Leaf)) {
    Fail "the repository checkout is incomplete: runtime-manifest.txt is missing."
}
Push-Location $SourceRoot
try {
    & $PythonExe -X utf8 -m token_meter.packaging manifest $RuntimeManifest
    if ($LASTEXITCODE -ne 0) {
        Fail "the runtime packaging manifest is invalid or incomplete."
    }
} finally {
    Pop-Location
}

New-Item -ItemType Directory -Path $InstallParent -Force | Out-Null
$ManagedSourceRoot = if ($env:TOKEN_METER_SOURCE_ROOT) {
    [System.IO.Path]::GetFullPath($env:TOKEN_METER_SOURCE_ROOT)
} else {
    Join-Path $InstallParent "source"
}
$UpdateSourceRoot = $SourceRoot
$SourceUpstream = (& $Git.Source -C $SourceRoot rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>$null | Out-String).Trim()
if ($SourceUpstream -like "*/*" -and $SourceRoot -ne $ManagedSourceRoot) {
    $Slash = $SourceUpstream.IndexOf('/')
    $SourceRemote = $SourceUpstream.Substring(0, $Slash)
    $SourceBranch = $SourceUpstream.Substring($Slash + 1)
    $SourceRemoteUrl = (& $Git.Source -C $SourceRoot remote get-url $SourceRemote 2>$null | Out-String).Trim()
    if ($SourceRemoteUrl -and $SourceBranch) {
        if (-not (Test-Path -LiteralPath $ManagedSourceRoot)) {
            & $Git.Source clone --quiet --no-local $SourceRoot $ManagedSourceRoot
            if ($LASTEXITCODE -ne 0) {
                Fail "the managed update checkout could not be created."
            }
        }
        if (-not (Test-Path -LiteralPath (Join-Path $ManagedSourceRoot ".git") -PathType Container)) {
            Fail "the managed update checkout is not a Git repository."
        }
        $ManagedDirty = (& $Git.Source -C $ManagedSourceRoot status --porcelain 2>$null | Out-String).Trim()
        if ($ManagedDirty) {
            Fail "the managed update checkout has local changes."
        }
        $SourceRevision = (& $Git.Source -C $SourceRoot rev-parse HEAD 2>$null | Out-String).Trim()
        if ($SourceRevision) {
            & $Git.Source -C $ManagedSourceRoot fetch --quiet --no-tags $SourceRoot $SourceRevision
            if ($LASTEXITCODE -ne 0) {
                Fail "the managed update checkout could not read the installed revision."
            }
            & $Git.Source -C $ManagedSourceRoot merge --quiet --ff-only FETCH_HEAD
            if ($LASTEXITCODE -ne 0) {
                Fail "the managed update checkout has diverged from the installed source."
            }
        }
        $ManagedRemoteUrl = (& $Git.Source -C $ManagedSourceRoot remote get-url origin 2>$null | Out-String).Trim()
        if ($ManagedRemoteUrl -ne $SourceRemoteUrl) {
            $ManagedRemoteIsSource = $false
            try {
                $ManagedRemoteIsSource = [System.IO.Path]::GetFullPath($ManagedRemoteUrl) -eq $SourceRoot
            } catch { }
            if ($ManagedRemoteIsSource) {
                & $Git.Source -C $ManagedSourceRoot remote set-url origin $SourceRemoteUrl
            } else {
                Fail "the managed update checkout belongs to a different upstream."
            }
        }
        $ManagedBranch = (& $Git.Source -C $ManagedSourceRoot rev-parse --abbrev-ref HEAD 2>$null | Out-String).Trim()
        if ($ManagedBranch) {
            & $Git.Source -C $ManagedSourceRoot config "branch.$ManagedBranch.remote" origin
            & $Git.Source -C $ManagedSourceRoot config "branch.$ManagedBranch.merge" "refs/heads/$SourceBranch"
        }
        $UpdateSourceRoot = $ManagedSourceRoot
    }
}

Write-Host "Installing Token Meter from:"
Write-Host "  $SourceRoot"
Write-Host "Runtime:"
Write-Host "  $InstallRoot"

$InstallNonce = [Guid]::NewGuid().ToString("N")
$StagingRoot = "$InstallRoot.installing-$InstallNonce"
$BackupRoot = "$InstallRoot.previous-$InstallNonce"
foreach ($TemporaryRoot in @($StagingRoot, $BackupRoot)) {
    $ResolvedTemporaryParent = [System.IO.Path]::GetFullPath((Split-Path -Parent $TemporaryRoot))
    if ($ResolvedTemporaryParent -ne [System.IO.Path]::GetFullPath($InstallParent)) {
        Fail "a temporary runtime path escaped the installation directory."
    }
    if (Test-Path -LiteralPath $TemporaryRoot) {
        Fail "a unique temporary runtime path already exists."
    }
}

New-Item -ItemType Directory -Path $StagingRoot -Force | Out-Null
foreach ($ManifestLine in Get-Content -LiteralPath $RuntimeManifest -Encoding UTF8) {
    $Line = ([string]$ManifestLine).Trim()
    if (-not $Line -or $Line.StartsWith("#")) {
        continue
    }
    $Parts = $Line -split '\s+', 2
    $EntryKind = $Parts[0]
    $RelativePath = $Parts[1]
    $SourcePath = Join-Path $SourceRoot $RelativePath
    $InstallPath = Join-Path $StagingRoot $RelativePath
    switch ($EntryKind) {
        { $_ -in @("required", "optional") } {
            if (-not (Test-Path -LiteralPath $SourcePath -PathType Leaf)) {
                if ($EntryKind -eq "required") {
                    Fail "the required runtime path is missing: $RelativePath"
                }
                continue
            }
            New-Item -ItemType Directory -Path (Split-Path -Parent $InstallPath) -Force | Out-Null
            Copy-Item -LiteralPath $SourcePath -Destination $InstallPath -Force
        }
        "python-tree" {
            Get-ChildItem -LiteralPath $SourcePath -Filter *.py -File -Recurse |
                Sort-Object FullName |
                ForEach-Object {
                    $PackageRelative = $_.FullName.Substring($SourceRoot.Length).TrimStart([char[]]"\/")
                    $PackageDestination = Join-Path $StagingRoot $PackageRelative
                    New-Item -ItemType Directory -Path (Split-Path -Parent $PackageDestination) -Force | Out-Null
                    Copy-Item -LiteralPath $_.FullName -Destination $PackageDestination -Force
                }
        }
        "tree" {
            Copy-Directory $SourcePath $InstallPath
        }
        default {
            Fail "the runtime packaging manifest contains an unsupported entry."
        }
    }
}
Push-Location $SourceRoot
try {
    & $PythonExe -X utf8 -m token_meter.packaging parity `
        $SourceRoot $StagingRoot $RuntimeManifest
    if ($LASTEXITCODE -ne 0) {
        Fail "the staged runtime does not match the source manifest."
    }
} finally {
    Pop-Location
}
Write-Utf8File (Join-Path $StagingRoot "SOURCE_CHECKOUT") ($UpdateSourceRoot + [Environment]::NewLine)
Write-Utf8File (Join-Path $StagingRoot "PYTHON_EXECUTABLE") ($PythonExe + [Environment]::NewLine)
Write-Utf8File (Join-Path $StagingRoot "PYTHON_WINDOWED_EXECUTABLE") ($PythonwExe + [Environment]::NewLine)
$InstallMode = if ($BackendOnly) { "backend-only" } else { "full" }
Write-Utf8File (Join-Path $StagingRoot "INSTALL_MODE") ($InstallMode + [Environment]::NewLine)

$Commit = (& $Git.Source -C $SourceRoot rev-parse --short HEAD 2>$null | Out-String).Trim()
if ($Commit) {
    $DirtyPaths = @(
        & $Git.Source -C $SourceRoot status --short --untracked-files=all 2>$null |
            Where-Object { $_ -and $_ -notmatch '^\?\? plan\.md$' }
    )
    if ($DirtyPaths.Count -gt 0) {
        $Commit = "$Commit+local"
    }
    Write-Utf8File (Join-Path $StagingRoot "INSTALLED_REVISION") ($Commit + [Environment]::NewLine)
}

$RunKey = "Registry::HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run"
$RunName = "Token Meter"
$PreviousRunValue = $null
if (Test-Path -LiteralPath $RunKey) {
    $PreviousRunProperties = Get-ItemProperty -LiteralPath $RunKey -Name $RunName -ErrorAction SilentlyContinue
    if ($PreviousRunProperties) {
        $PreviousRunValue = $PreviousRunProperties.$RunName
    }
}
$Swapped = $false
try {
    if (Test-Path -LiteralPath $InstallRoot) {
        Stop-InstalledTray $InstallRoot
        Stop-InstalledServer $InstallRoot
        Move-Item -LiteralPath $InstallRoot -Destination $BackupRoot
    }
    Move-Item -LiteralPath $StagingRoot -Destination $InstallRoot
    $Swapped = $true

    $DeliveryBootstrapJson = ""
    Push-Location $InstallRoot
    try {
        $DeliveryBootstrapJson = (
            & $PythonExe -X utf8 -c `
                'import json; from token_meter.app import bootstrap_git_delivery; print(json.dumps(bootstrap_git_delivery()))' `
                2>$null | Out-String
        ).Trim()
    } finally {
        Pop-Location
    }
    if ($DeliveryBootstrapJson -match '"ok"\s*:\s*true') {
        Write-Host "Indexed available local Git delivery history."
    } else {
        Write-Host "Local Git delivery history will remain partial until a readable repository is observed."
    }

    $PowerShellCommand = Get-Command powershell.exe -CommandType Application -ErrorAction Stop
    $PowerShellExe = $PowerShellCommand.Source
    $StartScript = Join-Path $InstallRoot "scripts\start-token-meter.ps1"
    $StartupCommand = "`"$PowerShellExe`" -NoLogo -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$StartScript`""
    New-Item -Path $RunKey -Force | Out-Null
    New-ItemProperty -LiteralPath $RunKey -Name $RunName -PropertyType String -Value $StartupCommand -Force | Out-Null

    & $StartScript -ReadinessTimeoutSeconds $ReadinessTimeoutSeconds

    $Health = Invoke-RestMethod -Uri "http://127.0.0.1:8722/health" -TimeoutSec 5
    if (-not $Health.ok -or -not $Health.state_ready) {
        Fail "the local server did not become ready."
    }
    $ExpectedPage = [System.IO.Path]::GetFullPath((Join-Path $InstallRoot "page.html"))
    $ActualPage = [System.IO.Path]::GetFullPath([string]$Health.page_path)
    if ($ActualPage -ne $ExpectedPage) {
        Fail "a different Token Meter installation owns port 8722."
    }
    if (-not $BackendOnly) {
        $null = Invoke-RestMethod -Uri "http://127.0.0.1:8722/menubar" -TimeoutSec 5
        $TrayStatusPath = Join-Path $InstallRoot "tray.status.json"
        if (-not (Test-Path -LiteralPath $TrayStatusPath -PathType Leaf)) {
            Fail "the Windows tray widget did not publish its status."
        }
        $TrayStatus = Get-Content -LiteralPath $TrayStatusPath -Raw | ConvertFrom-Json
        if (-not $TrayStatus.ready -or -not $TrayStatus.connected) {
            Fail "the Windows tray widget did not become ready."
        }
        $TrayProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$TrayStatus.pid)" -ErrorAction SilentlyContinue
        $ExpectedTray = [System.IO.Path]::GetFullPath((Join-Path $InstallRoot "scripts\run-tray.ps1"))
        if (-not $TrayProcess -or [string]$TrayProcess.CommandLine -notlike "*$ExpectedTray*") {
            Fail "the Windows tray widget process could not be verified."
        }
    }
    $SavedRunValue = (Get-ItemProperty -LiteralPath $RunKey -Name $RunName).$RunName
    if ($SavedRunValue -ne $StartupCommand) {
        Fail "automatic login startup could not be verified."
    }

    if (Test-Path -LiteralPath $BackupRoot) {
        Remove-Item -LiteralPath $BackupRoot -Recurse -Force
    }
} catch {
    if ($Swapped -and (Test-Path -LiteralPath $InstallRoot)) {
        try { Stop-InstalledTray $InstallRoot } catch { }
        try { Stop-InstalledServer $InstallRoot } catch { }
        Remove-Item -LiteralPath $InstallRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $BackupRoot) {
        Move-Item -LiteralPath $BackupRoot -Destination $InstallRoot
    }
    if ($null -eq $PreviousRunValue) {
        Remove-ItemProperty -LiteralPath $RunKey -Name $RunName -ErrorAction SilentlyContinue
    } else {
        New-ItemProperty -LiteralPath $RunKey -Name $RunName -PropertyType String -Value $PreviousRunValue -Force | Out-Null
    }
    throw
} finally {
    if (Test-Path -LiteralPath $StagingRoot) {
        Remove-Item -LiteralPath $StagingRoot -Recurse -Force
    }
}

$PythonArchitecture = (& $PythonExe -c "import platform; print(platform.machine())" | Out-String).Trim()
Write-Host ""
Write-Host "Token Meter installation complete."
Write-Host "Dashboard: http://127.0.0.1:8722"
if ($BackendOnly) {
    Write-Host "Backend-only installation: notification-area companion skipped."
} else {
    Write-Host "Tray widget: running"
}
Write-Host "Starts automatically after you log in."
Write-Host "Runtime: $InstallRoot"
Write-Host "Python: $PythonExe ($PythonArchitecture)"
if ($Commit) {
    Write-Host "Installed commit: $Commit"
}
Write-Host "Uninstall: powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File `"$InstallRoot\scripts\uninstall-windows.ps1`""
