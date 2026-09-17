[CmdletBinding()]
param(
    [switch]$SmokeTest,
    [string]$OpenProbePath = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($env:OS -ne "Windows_NT") {
    throw "The Token Meter tray requires Windows."
}

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public static class TokenMeterNativeWindow {
    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool SetProcessDpiAwarenessContext(IntPtr value);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool SetProcessDPIAware();
}

public static class TokenMeterNativeIcon {
    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool DestroyIcon(IntPtr handle);
}
"@

$script:DpiAwareness = "Unavailable"
try {
    $PerMonitorV2 = [IntPtr](-4)
    if ([TokenMeterNativeWindow]::SetProcessDpiAwarenessContext($PerMonitorV2)) {
        $script:DpiAwareness = "PerMonitorV2"
    }
} catch {
    $script:DpiAwareness = "Unavailable"
}
if ($script:DpiAwareness -ne "PerMonitorV2") {
    try {
        if ([TokenMeterNativeWindow]::SetProcessDPIAware()) {
            $script:DpiAwareness = "SystemAware"
        }
    } catch {
        $script:DpiAwareness = "Unavailable"
    }
}

# DPI awareness must be selected before WinForms creates any controls.
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$script:DarkMenuRenderer = [System.Windows.Forms.ToolStripProfessionalRenderer]::new()
$script:LightMenuRenderer = [System.Windows.Forms.ToolStripSystemRenderer]::new()

function Get-WindowsAppTheme {
    try {
        $Theme = Get-ItemProperty -LiteralPath (
            "Registry::HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        ) -Name "AppsUseLightTheme" -ErrorAction Stop
        if ([int]$Theme.AppsUseLightTheme -eq 0) {
            return "dark"
        }
    } catch {
        # Windows defaults apps to the light theme when the value is absent.
    }
    return "light"
}

function Set-TrayMenuItemTheme($Items, $Background, $Foreground, $Disabled, $Renderer) {
    foreach ($Item in @($Items)) {
        $Item.BackColor = $Background
        $Item.ForeColor = if ($Item.Enabled) { $Foreground } else { $Disabled }
        if ($Item -is [System.Windows.Forms.ToolStripMenuItem] -and $Item.DropDownItems.Count -gt 0) {
            $Item.DropDown.BackColor = $Background
            $Item.DropDown.ForeColor = $Foreground
            $Item.DropDown.Renderer = $Renderer
            Set-TrayMenuItemTheme $Item.DropDownItems $Background $Foreground $Disabled $Renderer
        }
    }
}

function Set-TrayMenuTheme($Menu) {
    $Theme = Get-WindowsAppTheme
    if ($Theme -eq "dark") {
        $Background = [System.Drawing.Color]::FromArgb(32, 32, 32)
        $Foreground = [System.Drawing.Color]::FromArgb(245, 245, 245)
        $Disabled = [System.Drawing.Color]::FromArgb(166, 166, 166)
        $Renderer = $script:DarkMenuRenderer
    } else {
        $Background = [System.Drawing.SystemColors]::Menu
        $Foreground = [System.Drawing.SystemColors]::MenuText
        $Disabled = [System.Drawing.SystemColors]::GrayText
        $Renderer = $script:LightMenuRenderer
    }
    $Menu.BackColor = $Background
    $Menu.ForeColor = $Foreground
    $Menu.Renderer = $Renderer
    Set-TrayMenuItemTheme $Menu.Items $Background $Foreground $Disabled $Renderer
    return $Theme
}

function Limit-Text([string]$Value, [int]$Maximum) {
    $Value = ([string]$Value -replace '\s+', ' ').Trim()
    if ($Value.Length -le $Maximum) {
        return $Value
    }
    return $Value.Substring(0, [Math]::Max(0, $Maximum - 3)) + "..."
}

function Get-Value($Object, [string]$Name, $Default = $null) {
    if ($null -eq $Object) {
        return $Default
    }
    $Property = $Object.PSObject.Properties[$Name]
    if ($null -eq $Property -or $null -eq $Property.Value) {
        return $Default
    }
    return $Property.Value
}

function Get-RuntimeLabel($State, [string]$Provider) {
    $Catalog = Get-Value $State "runtime_catalog" $null
    $Runtime = Get-Value $Catalog $Provider $null
    if ($null -eq $Runtime) {
        $Runtime = Get-Value $Catalog "unknown-runtime" $null
    }
    $Label = [string](Get-Value $Runtime "label" "")
    if ($Label) {
        return $Label
    }
    if (-not $Provider) {
        return "Unknown Runtime"
    }
    return ([System.Globalization.CultureInfo]::InvariantCulture.TextInfo.ToTitleCase(
        ($Provider -replace '[-_]+', ' ')
    ))
}

function Get-UsageWidgetChips($State) {
    $Chips = @()
    $Seen = New-Object 'System.Collections.Generic.HashSet[string]'
    foreach ($Row in @(Get-Value $State "provider_quotas" @())) {
        $Id = [string](Get-Value $Row "id" "")
        if (-not $Id -or $Id -eq "unknown-runtime" -or -not $Seen.Add($Id)) {
            continue
        }
        $Chips += [pscustomobject]@{
            id = $Id
            label = Get-RuntimeLabel $State $Id
        }
    }
    foreach ($Row in @(Get-Value $State "recent_sessions" @())) {
        $Id = [string](Get-Value $Row "provider" "")
        if (-not $Id -or $Id -eq "unknown-runtime" -or -not $Seen.Add($Id)) {
            continue
        }
        $Chips += [pscustomobject]@{
            id = $Id
            label = Get-RuntimeLabel $State $Id
        }
    }
    return @($Chips)
}

function Get-UsageWidgetView($State, [string]$ProviderId) {
    $Chips = @(Get-UsageWidgetChips $State)
    if ($ProviderId -and -not ($Chips | Where-Object { $_.id -eq $ProviderId })) {
        $ProviderId = ""
    }
    $Quotas = @(Get-Value $State "provider_quotas" @())
    $Sessions = @(Get-Value $State "recent_sessions" @())
    if ($ProviderId) {
        $Quotas = @($Quotas | Where-Object { [string](Get-Value $_ "id" "") -eq $ProviderId })
        $Sessions = @($Sessions | Where-Object { [string](Get-Value $_ "provider" "") -eq $ProviderId })
    }
    $Windows = @()
    $Title = "Token Meter"
    if ($Quotas.Count -gt 0) {
        $Primary = $Quotas[0]
        if (-not $ProviderId) {
            $Primary = $Quotas | Sort-Object {
                (@(Get-Value $_ "windows" @()) | Measure-Object -Property used_percent -Maximum).Maximum
            } -Descending | Select-Object -First 1
        }
        $Windows = @(Get-Value $Primary "windows" @())
        $Title = [string](Get-Value $Primary "label" (Get-RuntimeLabel $State ([string](Get-Value $Primary "id" ""))))
    } elseif ($ProviderId) {
        $Title = Get-RuntimeLabel $State $ProviderId
    }
    $Hottest = $null
    foreach ($Window in $Windows) {
        $Used = [double](Get-Value $Window "used_percent" 0)
        if ($null -eq $Hottest -or $Used -gt $Hottest) { $Hottest = $Used }
    }
    $Live = Get-Value $State "live_throughput" $null
    $LiveOk = [bool](Get-Value $Live "available" $false) -and (
        -not $ProviderId -or [string](Get-Value $State "provider" "") -eq $ProviderId
    )
    return [pscustomobject]@{
        chips = $Chips
        selectedId = $ProviderId
        title = $Title
        windows = $Windows
        sessions = @($Sessions | Select-Object -First 5)
        hottest = $Hottest
        liveTps = if ($LiveOk) { [double](Get-Value $Live "output_tps" 0) } else { 0 }
    }
}

function New-TokenMeterIcon {
    $Bitmap = New-Object System.Drawing.Bitmap(
        32,
        32,
        [System.Drawing.Imaging.PixelFormat]::Format32bppArgb
    )
    $Graphics = [System.Drawing.Graphics]::FromImage($Bitmap)
    $Path = New-Object System.Drawing.Drawing2D.GraphicsPath
    $Brush = $null
    $Pen = $null
    try {
        $Graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
        $Graphics.Clear([System.Drawing.Color]::Transparent)

        $Path.AddArc(0, 0, 14, 14, 180, 90)
        $Path.AddArc(18, 0, 14, 14, 270, 90)
        $Path.AddArc(18, 18, 14, 14, 0, 90)
        $Path.AddArc(0, 18, 14, 14, 90, 90)
        $Path.CloseFigure()

        $Bounds = New-Object System.Drawing.Rectangle(0, 0, 32, 32)
        $Brush = New-Object System.Drawing.Drawing2D.LinearGradientBrush(
            $Bounds,
            [System.Drawing.Color]::FromArgb(0, 188, 235),
            [System.Drawing.Color]::FromArgb(127, 219, 242),
            45.0
        )
        $Blend = New-Object System.Drawing.Drawing2D.ColorBlend(3)
        $Blend.Colors = [System.Drawing.Color[]]@(
            [System.Drawing.Color]::FromArgb(0, 188, 235),
            [System.Drawing.Color]::FromArgb(27, 160, 225),
            [System.Drawing.Color]::FromArgb(127, 219, 242)
        )
        $Blend.Positions = [single[]]@(0.0, 0.55, 1.0)
        $Brush.InterpolationColors = $Blend
        $Graphics.FillPath($Brush, $Path)

        $Pen = New-Object System.Drawing.Pen(
            [System.Drawing.Color]::FromArgb(6, 36, 48),
            2.1
        )
        $Pen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
        $Pen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
        foreach ($Line in @(
            @(7.0, 18.0, 7.0, 15.0),
            @(10.0, 18.0, 10.0, 11.0),
            @(13.0, 18.0, 13.0, 14.0),
            @(16.0, 18.0, 16.0, 8.0),
            @(19.0, 18.0, 19.0, 14.0),
            @(22.0, 18.0, 22.0, 11.0),
            @(25.0, 18.0, 25.0, 15.0),
            @(6.5, 22.5, 25.5, 22.5)
        )) {
            $Graphics.DrawLine(
                $Pen,
                [single]$Line[0],
                [single]$Line[1],
                [single]$Line[2],
                [single]$Line[3]
            )
        }

        $Handle = $Bitmap.GetHicon()
        try {
            return [System.Drawing.Icon]([System.Drawing.Icon]::FromHandle($Handle).Clone())
        } finally {
            [TokenMeterNativeIcon]::DestroyIcon($Handle) | Out-Null
        }
    } finally {
        if ($Pen) { $Pen.Dispose() }
        if ($Brush) { $Brush.Dispose() }
        $Path.Dispose()
        $Graphics.Dispose()
        $Bitmap.Dispose()
    }
}

function Format-CompactNumber($Value) {
    $Number = 0.0
    if (-not [double]::TryParse(
        [string]$Value,
        [System.Globalization.NumberStyles]::Any,
        [System.Globalization.CultureInfo]::InvariantCulture,
        [ref]$Number
    )) {
        return "0"
    }
    if ($Number -ge 1000000) {
        return ($Number / 1000000).ToString("0.0M", [System.Globalization.CultureInfo]::InvariantCulture)
    }
    if ($Number -ge 1000) {
        return ($Number / 1000).ToString("0.0K", [System.Globalization.CultureInfo]::InvariantCulture)
    }
    return $Number.ToString("0", [System.Globalization.CultureInfo]::InvariantCulture)
}

function Format-TrayText($State) {
    if (-not $State -or -not [bool](Get-Value $State "ok" $false)) {
        return "Token Meter - waiting for the local server"
    }
    $Cost = 0.0
    [double]::TryParse(
        [string](Get-Value $State "total_cost" 0),
        [System.Globalization.NumberStyles]::Any,
        [System.Globalization.CultureInfo]::InvariantCulture,
        [ref]$Cost
    ) | Out-Null
    $Verdict = Get-Value $State "verdict" $null
    $VerdictLabel = [string](Get-Value $Verdict "label" "Live")
    return Limit-Text (
        "Token Meter - `$$($Cost.ToString('0.00', [System.Globalization.CultureInfo]::InvariantCulture)) est - $VerdictLabel"
    ) 63
}

function Format-MenuStatus($State) {
    if (-not $State -or -not [bool](Get-Value $State "ok" $false)) {
        return "Waiting for local usage data"
    }
    $Cost = 0.0
    [double]::TryParse(
        [string](Get-Value $State "total_cost" 0),
        [System.Globalization.NumberStyles]::Any,
        [System.Globalization.CultureInfo]::InvariantCulture,
        [ref]$Cost
    ) | Out-Null
    $Tokens = Format-CompactNumber (Get-Value $State "total_tokens" 0)
    return "`$$($Cost.ToString('0.00', [System.Globalization.CultureInfo]::InvariantCulture)) est | $Tokens tokens"
}

$RuntimeRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$PidPath = Join-Path $RuntimeRoot "tray.pid"
$StatusPath = Join-Path $RuntimeRoot "tray.status.json"
$script:BaseUrl = "http://127.0.0.1:8722"
$script:SelectedSessionId = ""
$script:LastState = $null
$script:UsageProviderId = ""
$script:UsageChipRects = @()
$script:Connected = $false
$script:OpenProbePath = $OpenProbePath

function Write-TrayStatus([bool]$Ready, [bool]$Connected) {
    $Record = [ordered]@{
        ready = $Ready
        connected = $Connected
        pid = $PID
        updated_at = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    }
    $Temporary = "$StatusPath.tmp-$PID"
    [System.IO.File]::WriteAllText(
        $Temporary,
        (($Record | ConvertTo-Json -Compress) + [Environment]::NewLine),
        [System.Text.UTF8Encoding]::new($false)
    )
    Move-Item -LiteralPath $Temporary -Destination $StatusPath -Force
}

function Screen-ForUsageWidget($Form) {
    $Anchor = if ($Form -and $Form.Location) { $Form.Location } else { [System.Windows.Forms.Cursor]::Position }
    return [System.Windows.Forms.Screen]::FromPoint($Anchor)
}

function Snap-UsageWidget($Form) {
    if (-not $Form -or $Form.IsDisposed) {
        return
    }
    $Work = (Screen-ForUsageWidget $Form).WorkingArea
    $Mid = $Form.Left + [int]($Form.Width / 2)
    $X = if ($Mid -ge ($Work.Left + [int]($Work.Width / 2))) { $Work.Right - $Form.Width } else { $Work.Left }
    $Y = [Math]::Max($Work.Top, [Math]::Min($Form.Top, $Work.Bottom - $Form.Height))
    $Form.Location = New-Object System.Drawing.Point $X, $Y
}

function Place-UsageWidget($Form, [int]$Width, [int]$Height) {
    $Work = (Screen-ForUsageWidget $Form).WorkingArea
    $KeepRight = (-not $Form) -or ($Form.Left + [int]($Form.Width / 2) -ge ($Work.Left + [int]($Work.Width / 2)))
    $Form.Size = New-Object System.Drawing.Size $Width, $Height
    $X = if ($KeepRight) { $Work.Right - $Width } else { $Work.Left }
    $Y = if ($Form.Top -gt $Work.Top) { $Form.Top } else { $Work.Top + 96 }
    $Y = [Math]::Max($Work.Top, [Math]::Min($Y, $Work.Bottom - $Height))
    $Form.Location = New-Object System.Drawing.Point $X, $Y
}

function Toggle-UsageWidgetExpanded {
    if (-not $script:UsageForm -or $script:UsageForm.IsDisposed) {
        return
    }
    $script:UsageExpanded = -not $script:UsageExpanded
    if ($script:UsageExpanded) {
        Place-UsageWidget $script:UsageForm 300 520
    } else {
        Place-UsageWidget $script:UsageForm 36 168
    }
    $script:UsageForm.Invalidate()
}

function Handle-UsageWidgetClick($Event) {
    if ($script:UsageExpanded) {
        foreach ($Chip in @($script:UsageChipRects)) {
            if ($Chip.Rect.Contains($Event.Location)) {
                $script:UsageProviderId = [string]$Chip.id
                $script:UsageForm.Invalidate()
                return
            }
        }
    }
    Toggle-UsageWidgetExpanded
}

function Draw-UsageWidget($Graphics, $Form) {
    $Graphics.Clear([System.Drawing.Color]::FromArgb(11, 16, 22))
    $Accent = [System.Drawing.Color]::FromArgb(0, 188, 235)
    $Text = [System.Drawing.Color]::FromArgb(246, 248, 251)
    $Dim = [System.Drawing.Color]::FromArgb(168, 179, 193)
    $Warn = [System.Drawing.Color]::FromArgb(255, 180, 87)
    $Bad = [System.Drawing.Color]::FromArgb(255, 111, 111)
    $TextBrush = New-Object System.Drawing.SolidBrush $Text
    $DimBrush = New-Object System.Drawing.SolidBrush $Dim
    $AccentBrush = New-Object System.Drawing.SolidBrush $Accent
    $ChipBrush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(18, 188, 235, 40))
    $Small = New-Object System.Drawing.Font "Segoe UI", 8.5
    $Bold = New-Object System.Drawing.Font "Segoe UI", 10, [System.Drawing.FontStyle]::Bold
    $View = Get-UsageWidgetView $script:LastState ([string]$script:UsageProviderId)
    $script:UsageChipRects = @()
    if (-not $script:UsageExpanded) {
        $Graphics.FillRectangle($AccentBrush, 0, 0, 4, $Form.Height)
        $Format = New-Object System.Drawing.StringFormat
        $Format.FormatFlags = [System.Drawing.StringFormatFlags]::DirectionVertical
        $Graphics.DrawString("LIMITS", $Small, $DimBrush, 10, 24, $Format)
        $Hottest = if ($null -eq $View.hottest) { 0 } else { [double]$View.hottest }
        $Graphics.DrawString(("{0:0}%" -f $Hottest), $Bold, $AccentBrush, 4, 110)
        $Small.Dispose(); $Bold.Dispose(); $TextBrush.Dispose(); $DimBrush.Dispose(); $AccentBrush.Dispose(); $ChipBrush.Dispose()
        return
    }
    $X = 12
    $Y = 10
    foreach ($Chip in @([pscustomobject]@{ id = ""; label = "All" }) + @($View.chips)) {
        $Label = [string]$Chip.label
        $Width = [math]::Max(36, [int]$Graphics.MeasureString($Label, $Small).Width + 12)
        $Rect = New-Object System.Drawing.Rectangle $X, $Y, $Width, 18
        $script:UsageChipRects += [pscustomobject]@{ id = [string]$Chip.id; Rect = $Rect }
        if ([string]$Chip.id -eq [string]$View.selectedId) {
            $Graphics.FillRectangle($ChipBrush, $Rect)
            $Graphics.DrawRectangle((New-Object System.Drawing.Pen $Accent), $Rect)
            $Graphics.DrawString($Label, $Small, $TextBrush, $X + 5, $Y + 2)
        } else {
            $Graphics.DrawRectangle((New-Object System.Drawing.Pen ([System.Drawing.Color]::FromArgb(60, 70, 82))), $Rect)
            $Graphics.DrawString($Label, $Small, $DimBrush, $X + 5, $Y + 2)
        }
        $X += $Width + 6
        if ($X -gt 250) { $X = 12; $Y += 22 }
    }
    $Y += 26
    $Graphics.DrawString(([string]$View.title).ToUpper(), $Small, $AccentBrush, 12, $Y)
    if ([double]$View.liveTps -gt 0) {
        $TokMin = [double]$View.liveTps * 60
        $Graphics.DrawString(("LIVE {0:0} tok/min" -f $TokMin), $Small, (New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(102, 217, 144))), 160, $Y)
    }
    $Y += 22
    $Windows = @($View.windows)
    if ($Windows.Count -eq 0) {
        $Graphics.DrawString("No provider limits reported.", $Small, $DimBrush, 12, $Y)
        $Y += 20
    }
    foreach ($Window in $Windows) {
        $Label = [string](Get-Value $Window "label" "Limit")
        $Used = [math]::Max(0, [math]::Min(100, [double](Get-Value $Window "used_percent" 0)))
        $Fill = if ($Used -ge 95) { $Bad } elseif ($Used -ge 80) { $Warn } else { $Accent }
        $Graphics.DrawString($Label, $Small, $TextBrush, 12, $Y)
        $Graphics.DrawString(("{0:0}%" -f $Used), $Small, $TextBrush, 240, $Y)
        $Y += 16
        $Graphics.FillRectangle((New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(36, 42, 50))), 12, $Y, 276, 7)
        $Graphics.FillRectangle((New-Object System.Drawing.SolidBrush $Fill), 12, $Y, [int](276 * $Used / 100), 7)
        $Y += 18
    }
    $Y += 8
    $Graphics.DrawString("RECENT", $Small, $DimBrush, 12, $Y)
    $Y += 18
    foreach ($Row in @($View.sessions)) {
        $Name = Limit-Text ([string](Get-Value $Row "name" "Session")) 28
        $Label = Limit-Text ([string](Get-Value $Row "label" (Get-Value $Row "provider" ""))) 12
        $Graphics.DrawString($Name, $Small, $TextBrush, 12, $Y)
        $Graphics.DrawString($Label, $Small, $DimBrush, 210, $Y)
        $Y += 18
    }
    $Small.Dispose(); $Bold.Dispose(); $TextBrush.Dispose(); $DimBrush.Dispose(); $AccentBrush.Dispose(); $ChipBrush.Dispose()
}

function Hide-UsageWidget {
    if ($script:UsageForm -and -not $script:UsageForm.IsDisposed) {
        $script:UsageForm.Hide()
    }
}

function Show-UsageWidget {
    if ($script:OpenProbePath) {
        [System.IO.File]::WriteAllText(
            $script:OpenProbePath,
            "$($script:BaseUrl)/#widget",
            [System.Text.UTF8Encoding]::new($false)
        )
        return
    }
    if ($script:UsageForm -and -not $script:UsageForm.IsDisposed) {
        $script:UsageForm.Show()
        $script:UsageForm.Invalidate()
        return
    }
    $Form = New-Object System.Windows.Forms.Form
    $Form.Text = "Token Meter"
    $Form.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::None
    $Form.TopMost = $true
    $Form.ShowInTaskbar = $true
    $Form.StartPosition = [System.Windows.Forms.FormStartPosition]::Manual
    $Form.BackColor = [System.Drawing.Color]::FromArgb(11, 16, 22)
    $script:UsageExpanded = $false
    $script:UsageProviderId = [string]$script:UsageProviderId
    $script:UsageChipRects = @()
    $script:UsageDrag = $null
    Place-UsageWidget $Form 36 168
    $Form.add_Paint({ param($Sender, $Event) Draw-UsageWidget $Event.Graphics $Sender })
    $Form.add_MouseDown({
        param($Sender, $Event)
        if ($Event.Button -eq [System.Windows.Forms.MouseButtons]::Left) {
            $script:UsageDrag = [pscustomobject]@{
                Start = $Event.Location
                Origin = $Sender.Location
                Moved = $false
            }
        }
    })
    $Form.add_MouseMove({
        param($Sender, $Event)
        if (-not $script:UsageDrag) { return }
        $Dx = $Event.X - $script:UsageDrag.Start.X
        $Dy = $Event.Y - $script:UsageDrag.Start.Y
        if (-not $script:UsageDrag.Moved -and [Math]::Abs($Dx) + [Math]::Abs($Dy) -lt 6) {
            return
        }
        $script:UsageDrag.Moved = $true
        $Sender.Location = New-Object System.Drawing.Point (
            ($script:UsageDrag.Origin.X + $Dx),
            ($script:UsageDrag.Origin.Y + $Dy)
        )
    })
    $Form.add_MouseUp({
        param($Sender, $Event)
        $Dragged = $script:UsageDrag -and $script:UsageDrag.Moved
        $script:UsageDrag = $null
        if ($Dragged) {
            Snap-UsageWidget $Sender
            return
        }
        Handle-UsageWidgetClick $Event
    })
    $script:UsageForm = $Form
    $Form.Show()
}

function Open-TokenMeterUrl([string]$Url) {
    if ($script:OpenProbePath) {
        [System.IO.File]::WriteAllText(
            $script:OpenProbePath,
            $Url,
            [System.Text.UTF8Encoding]::new($false)
        )
        return
    }
    Start-Process -FilePath $Url | Out-Null
}

function Current-DashboardUrl {
    if ($script:SelectedSessionId) {
        $Escaped = [Uri]::EscapeDataString($script:SelectedSessionId)
        return "$($script:BaseUrl)/sessions/$Escaped#summary"
    }
    return "$($script:BaseUrl)/#sessions"
}

function Invoke-TrayMouseClick($EventArgs) {
    if ($EventArgs.Button -eq [System.Windows.Forms.MouseButtons]::Left) {
        Open-TokenMeterUrl (Current-DashboardUrl)
    }
}

if ($SmokeTest) {
    $Sample = [pscustomobject]@{
        ok = $true
        total_cost = 1.25
        total_tokens = 125000
        verdict = [pscustomobject]@{ label = "Healthy" }
        runtime_catalog = [pscustomobject]@{
            kiro = [pscustomobject]@{ label = "Kiro" }
            "unknown-runtime" = [pscustomobject]@{ label = "Unknown Runtime" }
        }
    }
    $OwnProbe = -not [bool]$script:OpenProbePath
    if ($OwnProbe) {
        $script:OpenProbePath = Join-Path $env:TEMP "token-meter-tray-click-$PID.txt"
    }
    Remove-Item -LiteralPath $script:OpenProbePath -Force -ErrorAction SilentlyContinue
    $Probe = New-Object System.Windows.Forms.NotifyIcon
    $ProbeIcon = New-TokenMeterIcon
    $ProbeMenu = New-Object System.Windows.Forms.ContextMenuStrip
    $ProbeDisabledItem = New-Object System.Windows.Forms.ToolStripMenuItem
    $ProbeDisabledItem.Text = "Token Meter"
    $ProbeDisabledItem.Enabled = $false
    $ProbeMenu.Items.Add($ProbeDisabledItem) | Out-Null
    $ProbeTheme = Set-TrayMenuTheme $ProbeMenu
    $IconBitmap = $null
    try {
        $Probe.Icon = $ProbeIcon
        $Probe.Text = Format-TrayText $Sample
        $Probe.add_MouseClick({ Invoke-TrayMouseClick $_ })
        $Probe.Visible = $true
        [System.Windows.Forms.Application]::DoEvents()

        $Click = New-Object System.Windows.Forms.MouseEventArgs(
            [System.Windows.Forms.MouseButtons]::Left,
            1,
            0,
            0,
            0
        )
        $MouseClickMethod = $Probe.GetType().GetMethod(
            "OnMouseClick",
            [System.Reflection.BindingFlags]::Instance -bor [System.Reflection.BindingFlags]::NonPublic
        )
        $MouseClickMethod.Invoke($Probe, [object[]]@($Click.PSObject.BaseObject)) | Out-Null
        $OpenedUrl = if (Test-Path -LiteralPath $script:OpenProbePath -PathType Leaf) {
            (Get-Content -LiteralPath $script:OpenProbePath -Raw).Trim()
        } else { "" }

        $IconBitmap = $ProbeIcon.ToBitmap()
        $BrandPixel = $IconBitmap.GetPixel(4, 4)
        [ordered]@{
            ok = $Probe.Visible -and $OpenedUrl -eq "$($script:BaseUrl)/#sessions"
            platform = "windows"
            widget = "NotifyIcon"
            icon = "TokenMeterSpectrum"
            icon_brand_color = $BrandPixel.B -gt 100 -and $BrandPixel.G -gt 100
            left_click_url = $OpenedUrl
            tooltip = $Probe.Text
            menu_status = Format-MenuStatus $Sample
            known_runtime_label = Get-RuntimeLabel $Sample "kiro"
            unknown_runtime_label = Get-RuntimeLabel $Sample "future-runtime"
            dpi_awareness = $script:DpiAwareness
            theme = $ProbeTheme
        } | ConvertTo-Json -Compress
    } finally {
        $Probe.Visible = $false
        $Probe.Dispose()
        if ($IconBitmap) { $IconBitmap.Dispose() }
        $ProbeIcon.Dispose()
        $ProbeMenu.Dispose()
        if ($OwnProbe) {
            Remove-Item -LiteralPath $script:OpenProbePath -Force -ErrorAction SilentlyContinue
        }
    }
    return
}

function Select-RecentSession($Sender) {
    $script:SelectedSessionId = [string]$Sender.Tag
    Invoke-TrayRefresh
}

function Follow-LatestSession {
    $script:SelectedSessionId = ""
    Invoke-TrayRefresh
}

function Update-RecentSessions($State) {
    $script:RecentMenu.DropDownItems.Clear()

    $Follow = New-Object System.Windows.Forms.ToolStripMenuItem
    $Follow.Text = "Follow latest"
    $Follow.Checked = -not [bool]$script:SelectedSessionId
    $Follow.add_Click({ Follow-LatestSession })
    $script:RecentMenu.DropDownItems.Add($Follow) | Out-Null
    $script:RecentMenu.DropDownItems.Add((New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null

    $Rows = @(Get-Value $State "recent_sessions" @())
    if ($Rows.Count -eq 0) {
        $Empty = New-Object System.Windows.Forms.ToolStripMenuItem
        $Empty.Text = "No recent sessions"
        $Empty.Enabled = $false
        $script:RecentMenu.DropDownItems.Add($Empty) | Out-Null
        return
    }

    foreach ($Row in $Rows) {
        $Id = [string](Get-Value $Row "id" "")
        if (-not $Id) {
            continue
        }
        $Provider = [string](Get-Value $Row "provider" "")
        $Label = if ($Provider) {
            Get-RuntimeLabel $State $Provider
        } else {
            [string](Get-Value $Row "label" "Session")
        }
        $Name = [string](Get-Value $Row "name" "Untitled")
        $Item = New-Object System.Windows.Forms.ToolStripMenuItem
        $Item.Text = Limit-Text "$Label - $Name" 80
        $Item.Tag = $Id
        $Item.Checked = $script:SelectedSessionId -eq $Id
        $Item.add_Click({ Select-RecentSession $this })
        $script:RecentMenu.DropDownItems.Add($Item) | Out-Null
    }
}

function Update-TrayMenu($State) {
    $script:NotifyIcon.Text = Format-TrayText $State
    $script:StatusItem.Text = Format-MenuStatus $State
    if ($script:UsageForm -and -not $script:UsageForm.IsDisposed) {
        $script:UsageForm.Invalidate()
    }

    $Recommendation = Get-Value $State "recommendation" $null
    $RecommendationLabel = [string](Get-Value $Recommendation "label" "Waiting for guidance")
    $script:GuidanceItem.Text = Limit-Text "Guidance: $RecommendationLabel" 100

    $Activity = Get-Value $State "activity" $null
    $ActivityTitle = [string](Get-Value $Activity "title" "Waiting for activity")
    $script:ActivityItem.Text = Limit-Text "Activity: $ActivityTitle" 100

    Update-RecentSessions $State
}

function Invoke-TrayRefresh {
    try {
        $Url = "$($script:BaseUrl)/menubar"
        if ($script:SelectedSessionId) {
            $Url += "?id=$([Uri]::EscapeDataString($script:SelectedSessionId))"
        }
        $State = Invoke-RestMethod -Uri $Url -TimeoutSec 4
        $Selection = Get-Value $State "selection" $null
        if ([bool](Get-Value $Selection "missing" $false)) {
            $script:SelectedSessionId = ""
            $State = Invoke-RestMethod -Uri "$($script:BaseUrl)/menubar" -TimeoutSec 4
        }
        $script:LastState = $State
        $script:Connected = $true
        Update-TrayMenu $State
    } catch {
        $script:Connected = $false
        $script:NotifyIcon.Text = "Token Meter - local server unavailable"
        $script:StatusItem.Text = "Local server unavailable"
        $script:GuidanceItem.Text = "Guidance: reconnecting"
        $script:ActivityItem.Text = "Activity: unavailable"
    }
    Write-TrayStatus $true $script:Connected
}

$CreatedNew = $false
$Mutex = New-Object System.Threading.Mutex($true, "Local\TokenMeterTray", [ref]$CreatedNew)
if (-not $CreatedNew) {
    $Mutex.Dispose()
    exit 0
}

[System.Windows.Forms.Application]::EnableVisualStyles()
$script:NotifyIcon = New-Object System.Windows.Forms.NotifyIcon
$script:TrayIcon = New-TokenMeterIcon
$script:NotifyIcon.Icon = $script:TrayIcon
$script:NotifyIcon.Text = "Token Meter - starting"
$script:NotifyIcon.Visible = $true

$Menu = New-Object System.Windows.Forms.ContextMenuStrip
$TitleItem = New-Object System.Windows.Forms.ToolStripMenuItem
$TitleItem.Text = "Token Meter"
$TitleItem.Enabled = $false
$TitleItem.Font = New-Object System.Drawing.Font($TitleItem.Font, [System.Drawing.FontStyle]::Bold)
$Menu.Items.Add($TitleItem) | Out-Null

$script:StatusItem = New-Object System.Windows.Forms.ToolStripMenuItem
$script:StatusItem.Text = "Waiting for local usage data"
$script:StatusItem.Enabled = $false
$Menu.Items.Add($script:StatusItem) | Out-Null

$script:GuidanceItem = New-Object System.Windows.Forms.ToolStripMenuItem
$script:GuidanceItem.Text = "Guidance: loading"
$script:GuidanceItem.Enabled = $false
$Menu.Items.Add($script:GuidanceItem) | Out-Null

$script:ActivityItem = New-Object System.Windows.Forms.ToolStripMenuItem
$script:ActivityItem.Text = "Activity: loading"
$script:ActivityItem.Enabled = $false
$Menu.Items.Add($script:ActivityItem) | Out-Null
$Menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null

$OpenWidget = New-Object System.Windows.Forms.ToolStripMenuItem
$OpenWidget.Text = "Usage widget"
$OpenWidget.CheckOnClick = $true
$OpenWidget.add_Click({
    if ($OpenWidget.Checked) {
        Show-UsageWidget
    } else {
        Hide-UsageWidget
    }
})
$Menu.Items.Add($OpenWidget) | Out-Null

$OpenDashboard = New-Object System.Windows.Forms.ToolStripMenuItem
$OpenDashboard.Text = "Open dashboard"
$OpenDashboard.add_Click({ Open-TokenMeterUrl (Current-DashboardUrl) })
$Menu.Items.Add($OpenDashboard) | Out-Null

$OpenDaily = New-Object System.Windows.Forms.ToolStripMenuItem
$OpenDaily.Text = "Open Spend"
$OpenDaily.add_Click({ Open-TokenMeterUrl "$($script:BaseUrl)/#spend" })
$Menu.Items.Add($OpenDaily) | Out-Null

$OpenBudgets = New-Object System.Windows.Forms.ToolStripMenuItem
$OpenBudgets.Text = "Open monthly budgets"
$OpenBudgets.add_Click({ Open-TokenMeterUrl "$($script:BaseUrl)/#settings-budgets" })
$Menu.Items.Add($OpenBudgets) | Out-Null

$script:RecentMenu = New-Object System.Windows.Forms.ToolStripMenuItem
$script:RecentMenu.Text = "Recent sessions"
$Menu.Items.Add($script:RecentMenu) | Out-Null
$Menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null

$Refresh = New-Object System.Windows.Forms.ToolStripMenuItem
$Refresh.Text = "Refresh now"
$Refresh.add_Click({ Invoke-TrayRefresh })
$Menu.Items.Add($Refresh) | Out-Null

$Quit = New-Object System.Windows.Forms.ToolStripMenuItem
$Quit.Text = "Quit tray widget"
$Quit.add_Click({ $script:Context.ExitThread() })
$Menu.Items.Add($Quit) | Out-Null

Set-TrayMenuTheme $Menu | Out-Null
$script:NotifyIcon.ContextMenuStrip = $Menu
$script:NotifyIcon.add_MouseClick({ Invoke-TrayMouseClick $_ })
$Menu.add_Opening({
    Invoke-TrayRefresh
    Set-TrayMenuTheme $Menu | Out-Null
})

$Timer = New-Object System.Windows.Forms.Timer
$Timer.Interval = 10000
$Timer.add_Tick({ Invoke-TrayRefresh })
$script:Context = New-Object System.Windows.Forms.ApplicationContext

try {
    [System.IO.File]::WriteAllText($PidPath, "$PID`r`n", [System.Text.UTF8Encoding]::new($false))
    Invoke-TrayRefresh
    $Timer.Start()
    if (-not $SmokeTest) {
        Show-UsageWidget
        $OpenWidget.Checked = $true
    }
    [System.Windows.Forms.Application]::Run($script:Context)
} finally {
    $Timer.Stop()
    $Timer.Dispose()
    $script:NotifyIcon.Visible = $false
    $script:NotifyIcon.Dispose()
    $script:TrayIcon.Dispose()
    $Menu.Dispose()
    $script:Context.Dispose()
    foreach ($Path in @($PidPath, $StatusPath)) {
        if (Test-Path -LiteralPath $Path -PathType Leaf) {
            $SavedPid = 0
            $Value = if ($Path -eq $PidPath) {
                (Get-Content -LiteralPath $Path -Raw).Trim()
            } else {
                try { [string](Get-Value (Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json) "pid" 0) } catch { "0" }
            }
            if ([int]::TryParse($Value, [ref]$SavedPid) -and $SavedPid -eq $PID) {
                Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
            }
        }
    }
    $Mutex.ReleaseMutex()
    $Mutex.Dispose()
}
