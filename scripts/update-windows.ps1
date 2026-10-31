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

# Windows PowerShell 5.1 turns a native command's stderr into a terminating
# error under ErrorActionPreference=Stop, even when it is redirected, so git
# probes that may legitimately fail (detached HEAD, no upstream) run here and
# reach the matching Fail-Update status instead of aborting the helper.
function Get-GitText([string[]]$Arguments) {
    $PreviousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $Output = @(& $Git.Source @Arguments 2>$null)
        $ExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousPreference
    }
    if ($ExitCode -ne 0) {
        return ""
    }
    return (($Output | ForEach-Object { [string]$_ }) -join [Environment]::NewLine).Trim()
}

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
    # The server reads this file while an update runs, and Windows refuses to
    # replace a file another process has open, so retry briefly.
    for ($Attempt = 1; ; $Attempt++) {
        try {
            Move-Item -LiteralPath $Temporary -Destination $StatusPath -Force
            break
        } catch {
            if ($Attempt -ge 50) {
                Remove-Item -LiteralPath $Temporary -Force -ErrorAction SilentlyContinue
                throw
            }
            Start-Sleep -Milliseconds 100
        }
    }
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
# Any stop not handled below must still leave a final phase; otherwise the
# dashboard keeps showing the last in-progress phase as "Updating".
trap {
    try {
        Write-UpdateStatus "failed" "update_interrupted" $script:CurrentRevision $script:LatestRevision $script:PreviousRevision
    } catch { }
    exit 1
}

if (-not (Test-Path -LiteralPath (Join-Path $SourceRoot ".git") -PathType Container) -or
    -not (Test-Path -LiteralPath (Join-Path $SourceRoot "scripts\install-windows.ps1") -PathType Leaf)) {
    Fail-Update "source_unavailable"
}
# The helper inherits the server's working directory, the runtime folder that
# the installer must rename. Windows cannot rename a folder a live process is
# working in, so move this process out of it first.
Set-Location -LiteralPath $SourceRoot
[Environment]::CurrentDirectory = $SourceRoot
# A detached helper has no one to answer a credential prompt.
$env:GIT_TERMINAL_PROMPT = "0"
$env:GCM_INTERACTIVE = "never"
$Git = Get-Command git.exe -CommandType Application -ErrorAction SilentlyContinue |
    Select-Object -First 1
if (-not $Git) {
    Fail-Update "git_unavailable"
}

$Upstream = (Get-GitText @("-C", $SourceRoot, "rev-parse", "--abbrev-ref", "--symbolic-full-name", '@{upstream}'))
if ($LASTEXITCODE -ne 0 -or $Upstream -notlike "*/*") {
    Fail-Update "upstream_unavailable"
}
$Branch = (Get-GitText @("-C", $SourceRoot, "rev-parse", "--abbrev-ref", "HEAD"))
if ($Branch -ne "main" -or $Upstream.Substring($Upstream.LastIndexOf('/') + 1) -ne "main") {
    Fail-Update "unsupported_update_branch"
}
if ($RetryRevision -and $RetryRevision -notmatch '^[0-9a-fA-F]{7,40}$') {
    $RetryRevision = ""
}
$Remote = $Upstream.Substring(0, $Upstream.IndexOf('/'))
Write-UpdateStatus "fetching" "" $script:CurrentRevision $script:LatestRevision $script:PreviousRevision
$null = Get-GitText @("-C", $SourceRoot, "fetch", "--quiet", "--prune", "--no-tags", $Remote)
if ($LASTEXITCODE -ne 0) {
    Fail-Update "fetch_failed"
}

$script:CurrentRevision = (Get-GitText @("-C", $SourceRoot, "rev-parse", "HEAD"))
$script:LatestRevision = (Get-GitText @("-C", $SourceRoot, "rev-parse", '@{upstream}'))
if (-not $script:PreviousRevision) {
    $script:PreviousRevision = $script:CurrentRevision
}
if (-not $script:CurrentRevision -or -not $script:LatestRevision) {
    Fail-Update "inspect_failed"
}
$Dirty = (Get-GitText @("-C", $SourceRoot, "status", "--porcelain"))
if ($Dirty) {
    Fail-Update "dirty_checkout"
}
if ($script:CurrentRevision -eq $script:LatestRevision -and
    $script:CurrentRevision -ne $RetryRevision) {
    Write-UpdateStatus "complete" "" $script:CurrentRevision $script:LatestRevision $script:PreviousRevision
    exit 0
}

if ($script:CurrentRevision -ne $script:LatestRevision) {
    $null = Get-GitText @("-C", $SourceRoot, "merge", "--ff-only", '@{upstream}')
    if ($LASTEXITCODE -ne 0) {
        Fail-Update "diverged_checkout"
    }
    $script:CurrentRevision = (Get-GitText @("-C", $SourceRoot, "rev-parse", "HEAD"))
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
