[CmdletBinding()]
param(
    [int]$ReadinessTimeoutSeconds = 0
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Get-Health {
    try {
        return Invoke-RestMethod -Uri "http://127.0.0.1:8722/health" -TimeoutSec 3
    } catch {
        return $null
    }
}

function Test-OwnedHealth($Health, [string]$ExpectedPage) {
    if (-not $Health -or -not $Health.page_path) {
        return $false
    }
    try {
        return [System.IO.Path]::GetFullPath([string]$Health.page_path) -eq $ExpectedPage
    } catch {
        return $false
    }
}

$RuntimeRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$MeterPath = [System.IO.Path]::GetFullPath((Join-Path $RuntimeRoot "meter.py"))
$ExpectedPage = [System.IO.Path]::GetFullPath((Join-Path $RuntimeRoot "page.html"))
$PythonRecord = Join-Path $RuntimeRoot "PYTHON_EXECUTABLE"
$WindowedPythonRecord = Join-Path $RuntimeRoot "PYTHON_WINDOWED_EXECUTABLE"
$PidPath = Join-Path $RuntimeRoot "meter.pid"
$OutputLog = Join-Path $RuntimeRoot "meter.log"
$ErrorLog = Join-Path $RuntimeRoot "meter.err.log"
$TrayScript = [System.IO.Path]::GetFullPath((Join-Path $RuntimeRoot "scripts\run-tray.ps1"))
$TrayPidPath = Join-Path $RuntimeRoot "tray.pid"
$TrayStatusPath = Join-Path $RuntimeRoot "tray.status.json"
$TrayOutputLog = Join-Path $RuntimeRoot "tray.log"
$TrayErrorLog = Join-Path $RuntimeRoot "tray.err.log"
$InstallModePath = Join-Path $RuntimeRoot "INSTALL_MODE"
$BackendOnly = $false
if (Test-Path -LiteralPath $InstallModePath -PathType Leaf) {
    $BackendOnly = (Get-Content -LiteralPath $InstallModePath -Raw).Trim() -eq "backend-only"
}

if ($ReadinessTimeoutSeconds -le 0) {
    $ConfiguredTimeout = 0
    if ($env:TOKEN_METER_READINESS_TIMEOUT_SECONDS) {
        [int]::TryParse($env:TOKEN_METER_READINESS_TIMEOUT_SECONDS, [ref]$ConfiguredTimeout) | Out-Null
    }
    $ReadinessTimeoutSeconds = if ($ConfiguredTimeout -gt 0) { $ConfiguredTimeout } else { 600 }
}

if (-not (Test-Path -LiteralPath $PythonRecord -PathType Leaf)) {
    throw "Token Meter's Python runtime record is missing. Reinstall Token Meter."
}
$PythonExe = (Get-Content -LiteralPath $PythonRecord -Raw).Trim()
if (Test-Path -LiteralPath $WindowedPythonRecord -PathType Leaf) {
    $WindowedPythonExe = (Get-Content -LiteralPath $WindowedPythonRecord -Raw).Trim()
    if (Test-Path -LiteralPath $WindowedPythonExe -PathType Leaf) {
        $PythonExe = $WindowedPythonExe
    }
}
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "Token Meter's configured Python executable is unavailable. Reinstall Token Meter."
}

$Health = Get-Health
if ($Health -and -not (Test-OwnedHealth $Health $ExpectedPage)) {
    throw "A different server owns http://127.0.0.1:8722."
}

$Process = $null
if (-not $Health) {
    if (Test-Path -LiteralPath $PidPath -PathType Leaf) {
        $SavedPid = 0
        if ([int]::TryParse((Get-Content -LiteralPath $PidPath -Raw).Trim(), [ref]$SavedPid)) {
            $SavedProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $SavedPid" -ErrorAction SilentlyContinue
            if ($SavedProcess -and [string]$SavedProcess.CommandLine -like "*$MeterPath*") {
                $Process = Get-Process -Id $SavedPid -ErrorAction SilentlyContinue
            }
        }
        if (-not $Process) {
            Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
        }
    }
    if (-not $Process) {
        $PreviousUtf8 = $env:PYTHONUTF8
        $env:PYTHONUTF8 = "1"
        try {
            $Process = Start-Process -FilePath $PythonExe `
                -ArgumentList @("-X", "utf8", "`"$MeterPath`"") `
                -WorkingDirectory $RuntimeRoot `
                -RedirectStandardOutput $OutputLog `
                -RedirectStandardError $ErrorLog `
                -WindowStyle Hidden `
                -PassThru
        } finally {
            if ($null -eq $PreviousUtf8) {
                Remove-Item Env:PYTHONUTF8 -ErrorAction SilentlyContinue
            } else {
                $env:PYTHONUTF8 = $PreviousUtf8
            }
        }
        [System.IO.File]::WriteAllText($PidPath, "$($Process.Id)`r`n", [System.Text.UTF8Encoding]::new($false))
    }
}

$Deadline = [DateTime]::UtcNow.AddSeconds($ReadinessTimeoutSeconds)
$Ready = $false
do {
    $Health = Get-Health
    if ($Health -and -not (Test-OwnedHealth $Health $ExpectedPage)) {
        throw "A different server owns http://127.0.0.1:8722."
    }
    if ($Health -and $Health.ok -and $Health.state_ready) {
        $Ready = $true
        break
    }
    if ($Process) {
        $Process.Refresh()
        if ($Process.HasExited) {
            $Detail = if (Test-Path -LiteralPath $ErrorLog) {
                (Get-Content -LiteralPath $ErrorLog -Tail 20 | Out-String).Trim()
            } else { "" }
            throw "Token Meter exited before becoming ready. $Detail"
        }
    }
    Start-Sleep -Milliseconds 500
} while ([DateTime]::UtcNow -lt $Deadline)

if (-not $Ready) {
    throw "Token Meter did not finish indexing within $ReadinessTimeoutSeconds seconds."
}

if ($BackendOnly) {
    return
}

if (-not (Test-Path -LiteralPath $TrayScript -PathType Leaf)) {
    throw "Token Meter's Windows tray launcher is missing. Reinstall Token Meter."
}

$TrayProcess = $null
if (Test-Path -LiteralPath $TrayPidPath -PathType Leaf) {
    $SavedTrayPid = 0
    if ([int]::TryParse((Get-Content -LiteralPath $TrayPidPath -Raw).Trim(), [ref]$SavedTrayPid)) {
        $SavedTrayProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $SavedTrayPid" -ErrorAction SilentlyContinue
        if ($SavedTrayProcess) {
            if ([string]$SavedTrayProcess.CommandLine -notlike "*$TrayScript*") {
                throw "The saved tray process ID belongs to a different program."
            }
            $TrayProcess = Get-Process -Id $SavedTrayPid -ErrorAction SilentlyContinue
        }
    }
    if (-not $TrayProcess) {
        Remove-Item -LiteralPath $TrayPidPath -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $TrayStatusPath -Force -ErrorAction SilentlyContinue
    }
}

if (-not $TrayProcess) {
    $PowerShellCommand = Get-Command powershell.exe -CommandType Application -ErrorAction Stop
    $TrayProcess = Start-Process -FilePath $PowerShellCommand.Source `
        -ArgumentList @(
            "-NoLogo",
            "-NoProfile",
            "-STA",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            "`"$TrayScript`""
        ) `
        -WorkingDirectory $RuntimeRoot `
        -RedirectStandardOutput $TrayOutputLog `
        -RedirectStandardError $TrayErrorLog `
        -WindowStyle Hidden `
        -PassThru
}

$TrayDeadline = [DateTime]::UtcNow.AddSeconds(20)
$TrayReady = $false
do {
    $TrayProcess.Refresh()
    if ($TrayProcess.HasExited) {
        $TrayDetail = if (Test-Path -LiteralPath $TrayErrorLog -PathType Leaf) {
            (Get-Content -LiteralPath $TrayErrorLog -Tail 20 | Out-String).Trim()
        } else { "" }
        throw "Token Meter's tray widget exited before becoming ready. $TrayDetail"
    }
    if (Test-Path -LiteralPath $TrayStatusPath -PathType Leaf) {
        try {
            $TrayStatus = Get-Content -LiteralPath $TrayStatusPath -Raw | ConvertFrom-Json
            if ($TrayStatus.ready -and $TrayStatus.connected -and [int]$TrayStatus.pid -eq $TrayProcess.Id) {
                $TrayReady = $true
                break
            }
        } catch { }
    }
    Start-Sleep -Milliseconds 250
} while ([DateTime]::UtcNow -lt $TrayDeadline)

if (-not $TrayReady) {
    throw "Token Meter's tray widget did not connect to the local server."
}
