[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$SourceRoot,
    [Parameter(Mandatory = $true, Position = 1)]
    [string]$StatusPath
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RuntimeRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$SourceRoot = [System.IO.Path]::GetFullPath($SourceRoot)
$StatusPath = [System.IO.Path]::GetFullPath($StatusPath)
$PreviousStatus = @{}
try {
    $Decoded = Get-Content -LiteralPath $StatusPath -Raw | ConvertFrom-Json
    if ($Decoded) {
        foreach ($Property in $Decoded.PSObject.Properties) {
            $PreviousStatus[$Property.Name] = $Property.Value
        }
    }
} catch { }

function Write-UpdateStatus(
    [string]$Phase,
    [string]$ErrorCode,
    [string]$CurrentRevision,
    [string]$LatestRevision,
    [string]$PreviousRevision
) {
    $Now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $Record = [ordered]@{
        phase = $Phase
        error_code = $ErrorCode
        current_revision = $CurrentRevision
        latest_revision = $LatestRevision
        previous_revision = $PreviousRevision
        checked_at = $Now
        available = $Phase -in @("fetching", "installing")
        can_update = $false
        dirty = $false
        ahead = 0
        behind = 0
    }
    if ($Phase -eq "complete") {
        $Record.installed_at = $Now
    } elseif ($PreviousStatus.ContainsKey("installed_at")) {
        $Record.installed_at = $PreviousStatus.installed_at
    }
    if ($Phase -eq "failed") {
        $Record.failed_revision = if ($CurrentRevision) {
            $CurrentRevision
        } elseif ($LatestRevision) {
            $LatestRevision
        } elseif ($PreviousStatus.ContainsKey("failed_revision")) {
            [string]$PreviousStatus.failed_revision
        } else {
            ""
        }
    }
    $Parent = Split-Path -Parent $StatusPath
    New-Item -ItemType Directory -Path $Parent -Force | Out-Null
    $Temporary = "$StatusPath.tmp-$PID"
    [System.IO.File]::WriteAllText(
        $Temporary,
        (($Record | ConvertTo-Json -Depth 3) + [Environment]::NewLine),
        [System.Text.UTF8Encoding]::new($false)
    )
    Move-Item -LiteralPath $Temporary -Destination $StatusPath -Force
}

function Fail-Update([string]$Code) {
    Write-UpdateStatus "failed" $Code $script:CurrentRevision $script:LatestRevision $script:PreviousRevision
    exit 1
}

$script:CurrentRevision = ""
$script:LatestRevision = ""
$RetryRevision = [string]$env:TOKEN_METER_UPDATE_RETRY_REVISION
$script:PreviousRevision = if ($PreviousStatus.ContainsKey("previous_revision")) {
    [string]$PreviousStatus.previous_revision
} else {
    ""
}

if (-not (Test-Path -LiteralPath (Join-Path $SourceRoot ".git") -PathType Container) -or
    -not (Test-Path -LiteralPath (Join-Path $SourceRoot "scripts\install-windows.ps1") -PathType Leaf)) {
    Fail-Update "source_unavailable"
}
$Git = Get-Command git.exe -CommandType Application -ErrorAction SilentlyContinue
if (-not $Git) {
    Fail-Update "git_unavailable"
}

$Upstream = (& $Git.Source -C $SourceRoot rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>$null | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $Upstream -notlike "*/*") {
    Fail-Update "upstream_unavailable"
}
$Branch = (& $Git.Source -C $SourceRoot rev-parse --abbrev-ref HEAD 2>$null | Out-String).Trim()
if ($Branch -ne "main" -or $Upstream.Substring($Upstream.LastIndexOf('/') + 1) -ne "main") {
    Fail-Update "unsupported_update_branch"
}
if ($RetryRevision -and $RetryRevision -notmatch '^[0-9a-fA-F]{7,40}$') {
    $RetryRevision = ""
}
$Remote = $Upstream.Substring(0, $Upstream.IndexOf('/'))
Write-UpdateStatus "fetching" "" $script:CurrentRevision $script:LatestRevision $script:PreviousRevision
& $Git.Source -C $SourceRoot fetch --quiet --prune --no-tags $Remote
if ($LASTEXITCODE -ne 0) {
    Fail-Update "fetch_failed"
}

$script:CurrentRevision = (& $Git.Source -C $SourceRoot rev-parse HEAD 2>$null | Out-String).Trim()
$script:LatestRevision = (& $Git.Source -C $SourceRoot rev-parse '@{upstream}' 2>$null | Out-String).Trim()
if (-not $script:PreviousRevision) {
    $script:PreviousRevision = $script:CurrentRevision
}
if (-not $script:CurrentRevision -or -not $script:LatestRevision) {
    Fail-Update "inspect_failed"
}
$Dirty = (& $Git.Source -C $SourceRoot status --porcelain 2>$null | Out-String).Trim()
if ($Dirty) {
    Fail-Update "dirty_checkout"
}
if ($script:CurrentRevision -eq $script:LatestRevision -and
    $script:CurrentRevision -ne $RetryRevision) {
    Write-UpdateStatus "complete" "" $script:CurrentRevision $script:LatestRevision $script:PreviousRevision
    exit 0
}

if ($script:CurrentRevision -ne $script:LatestRevision) {
    & $Git.Source -C $SourceRoot merge --ff-only '@{upstream}'
    if ($LASTEXITCODE -ne 0) {
        Fail-Update "diverged_checkout"
    }
    $script:CurrentRevision = (& $Git.Source -C $SourceRoot rev-parse HEAD 2>$null | Out-String).Trim()
}
Write-UpdateStatus "installing" "" $script:CurrentRevision $script:LatestRevision $script:PreviousRevision
try {
    $InstallArguments = @{ InstallRoot = $RuntimeRoot }
    $InstallModePath = Join-Path $RuntimeRoot "INSTALL_MODE"
    if ((Test-Path -LiteralPath $InstallModePath -PathType Leaf) -and
        (Get-Content -LiteralPath $InstallModePath -Raw).Trim() -eq "backend-only") {
        $InstallArguments["BackendOnly"] = $true
    }
    & (Join-Path $SourceRoot "scripts\install-windows.ps1") @InstallArguments
} catch {
    Fail-Update "install_failed"
}
Write-UpdateStatus "complete" "" $script:CurrentRevision $script:LatestRevision $script:PreviousRevision
