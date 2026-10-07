[CmdletBinding()]
param(
    [switch]$SmokeTest,
    [string]$OpenProbePath = "",
    [switch]$Inline
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($env:OS -ne "Windows_NT") {
    throw "The Token Meter tray requires Windows."
}

if (-not $SmokeTest -and -not $Inline) {
    $Shell = Get-Command powershell.exe -CommandType Application -ErrorAction SilentlyContinue
    if ($Shell) {
        Start-Process -FilePath $Shell.Source `
            -ArgumentList @(
                "-NoLogo", "-NoProfile", "-STA",
                "-ExecutionPolicy", "Bypass",
                "-File", "`"$($MyInvocation.MyCommand.Path)`"",
                "-Inline"
            ) `
            -WindowStyle Hidden
        exit 0
    }
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

public static class TokenMeterExceptionSuppressor {
    // A native C# handler so that PipelineStoppedException from a PowerShell
    // event handler cannot re-trigger during ThreadException handling and crash
    // the process. A PS scriptblock delegate re-throws when the pipeline is
    // already stopped; a C# method does not.
    public static void Handle(
        object sender,
        System.Threading.ThreadExceptionEventArgs e) { }
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

function Load-TraySettings($SettingsPath) {
    $Defaults = @{ panel_visible = $true; panel_x = -1; panel_y = -1 }
    if (-not (Test-Path -LiteralPath $SettingsPath -PathType Leaf)) { return $Defaults }
    try {
        $Loaded = Get-Content -LiteralPath $SettingsPath -Raw -ErrorAction Stop | ConvertFrom-Json
        foreach ($Key in @($Defaults.Keys)) {
            $Prop = $Loaded.PSObject.Properties[$Key]
            if ($null -ne $Prop) { $Defaults[$Key] = $Prop.Value }
        }
    } catch { }
    return $Defaults
}

function Save-TraySettings($SettingsPath, $Settings) {
    $Temporary = "$SettingsPath.tmp-$PID"
    try {
        [System.IO.File]::WriteAllText(
            $Temporary,
            ($Settings | ConvertTo-Json -Compress) + [Environment]::NewLine,
            [System.Text.UTF8Encoding]::new($false)
        )
        Move-Item -LiteralPath $Temporary -Destination $SettingsPath -Force
    } catch {
        Remove-Item -LiteralPath $Temporary -Force -ErrorAction SilentlyContinue
    }
}

function Format-PanelText($State) {
    if (-not $State -or -not [bool](Get-Value $State "ok" $false)) {
        return "Token Meter - waiting for server"
    }
    $Cost = 0.0
    [double]::TryParse(
        [string](Get-Value $State "total_cost" 0),
        [System.Globalization.NumberStyles]::Any,
        [System.Globalization.CultureInfo]::InvariantCulture,
        [ref]$Cost
    ) | Out-Null
    $Tokens = Format-CompactNumber (Get-Value $State "total_tokens" 0)
    $Source = Get-Value $State "source" $null
    $Model = [string](Get-Value $Source "model" "")
    if (-not $Model) { $Model = [string](Get-Value $State "model" "") }
    $Verdict = Get-Value $State "verdict" $null
    $VerdictLabel = [string](Get-Value $Verdict "label" "")
    $Parts = [System.Collections.Generic.List[string]]::new()
    if ($VerdictLabel) { $Parts.Add($VerdictLabel) }
    if ($Model) { $Parts.Add($Model) }
    $Parts.Add("$Tokens tokens")
    $Parts.Add("`$$($Cost.ToString('0.00', [System.Globalization.CultureInfo]::InvariantCulture)) est")
    return ($Parts -join " | ")
}

function New-UsagePanel {
    $Panel = New-Object System.Windows.Forms.Form
    $Panel.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::None
    $Panel.ControlBox = $false
    $Panel.ShowInTaskbar = $false
    $Panel.TopMost = $true
    $Panel.StartPosition = [System.Windows.Forms.FormStartPosition]::Manual
    $Panel.BackColor = [System.Drawing.Color]::FromArgb(31, 41, 55)
    $Panel.ClientSize = New-Object System.Drawing.Size(500, 30)

    $Label = New-Object System.Windows.Forms.Label
    $Label.Font = New-Object System.Drawing.Font("Segoe UI", 9, [System.Drawing.FontStyle]::Regular)
    $Label.ForeColor = [System.Drawing.Color]::White
    $Label.BackColor = $Panel.BackColor
    $Label.TextAlign = [System.Drawing.ContentAlignment]::MiddleCenter
    $Label.AutoEllipsis = $true
    $Label.Dock = [System.Windows.Forms.DockStyle]::Fill
    $Panel.Controls.Add($Label) | Out-Null
    return $Panel, $Label
}

function Set-PanelPosition($Panel, $X, $Y) {
    $Screen = [System.Windows.Forms.Screen]::FromPoint($Panel.Location)
    $WorkArea = $Screen.WorkingArea
    $BottomReserve = if ($WorkArea.Bottom -eq $Screen.Bounds.Bottom) { 48 } else { 0 }
    $ClampedX = [Math]::Max($WorkArea.Left, [Math]::Min($X, $WorkArea.Right - $Panel.Width))
    $ClampedY = [Math]::Max($WorkArea.Top, [Math]::Min($Y, $WorkArea.Bottom - $Panel.Height - $BottomReserve))
    $Panel.Location = New-Object System.Drawing.Point($ClampedX, $ClampedY)
}

function Show-UsagePanel {
    $SavedX = [int]($script:TraySettings["panel_x"])
    $SavedY = [int]($script:TraySettings["panel_y"])
    if ($SavedX -ge 0 -and $SavedY -ge 0) {
        Set-PanelPosition $script:UsagePanel $SavedX $SavedY
    } else {
        $WorkArea = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
        Set-PanelPosition $script:UsagePanel ($WorkArea.Right - $script:UsagePanel.Width - 12) ($WorkArea.Bottom - $script:UsagePanel.Height - 48)
    }
    if (-not $script:UsagePanel.Visible) { $script:UsagePanel.Show() }
}

function Set-PanelText([string]$Text) {
    if ($null -eq $script:UsagePanel -or -not $script:PanelVisible) { return }
    $script:PanelLabel.Text = $Text
    try {
        $Measured = [System.Windows.Forms.TextRenderer]::MeasureText($Text, $script:PanelLabel.Font)
        $NewWidth  = [Math]::Max(280, [Math]::Min(900, $Measured.Width  + 36))
        $NewHeight = [Math]::Max(30,  $Measured.Height + 12)
        $script:UsagePanel.ClientSize = New-Object System.Drawing.Size($NewWidth, $NewHeight)
    } catch { }
}

function Refresh-PanelText {
    $Base = if ($script:CurrentPanelText) { $script:CurrentPanelText } else { "Token Meter - waiting for server" }
    if ($script:PanelHovering) {
        Set-PanelText "$Base | Right-click for options"
    } else {
        Set-PanelText "Token Meter | $Base"
    }
}

function Update-UsagePanel($State) {
    if ($null -eq $script:UsagePanel -or -not $script:PanelVisible) { return }
    $script:CurrentPanelText = Format-PanelText $State
    Refresh-PanelText
}

function Set-PanelHoverText {
    if ($script:PanelHovering) { return }
    $script:PanelHovering = $true
    Refresh-PanelText
}

function Clear-PanelHoverText {
    if (-not $script:PanelHovering) { return }
    $script:PanelHovering = $false
    Refresh-PanelText
}

function Start-UsagePanelDrag($Sender, $EventArgs) {
    if ($EventArgs.Button -eq [System.Windows.Forms.MouseButtons]::Left) {
        $script:UsagePanelDragActive = $true
        $script:UsagePanelDragCursor = [System.Windows.Forms.Control]::MousePosition
        $script:UsagePanelDragOrigin = $script:UsagePanel.Location
        $script:UsagePanel.Capture = $true
    }
}

function Move-UsagePanelDrag {
    if (-not $script:UsagePanelDragActive) { return }
    $Cursor = [System.Windows.Forms.Control]::MousePosition
    Set-PanelPosition $script:UsagePanel `
        ($script:UsagePanelDragOrigin.X + $Cursor.X - $script:UsagePanelDragCursor.X) `
        ($script:UsagePanelDragOrigin.Y + $Cursor.Y - $script:UsagePanelDragCursor.Y)
}

function Stop-UsagePanelDrag {
    if (-not $script:UsagePanelDragActive) { return }
    $script:UsagePanelDragActive = $false
    $script:UsagePanel.Capture = $false
    Set-PanelPosition $script:UsagePanel $script:UsagePanel.Left $script:UsagePanel.Top
    try {
        $script:TraySettings["panel_x"] = $script:UsagePanel.Left
        $script:TraySettings["panel_y"] = $script:UsagePanel.Top
        Save-TraySettings $SettingsPath $script:TraySettings
    } catch { }
}

$RuntimeRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$PidPath = Join-Path $RuntimeRoot "tray.pid"
$StatusPath = Join-Path $RuntimeRoot "tray.status.json"
$SettingsPath = Join-Path $RuntimeRoot "tray-settings.json"
$script:BaseUrl = "http://127.0.0.1:8722"
$script:SelectedSessionId = ""
$script:LastState = $null
$script:Connected = $false
$script:OpenProbePath = $OpenProbePath
$script:TrayExiting = $false
$script:UsagePanelDragActive = $false
$script:UsagePanelDragCursor = $null
$script:UsagePanelDragOrigin = $null
$script:PanelHovering = $false
$script:CurrentPanelText = "Token Meter - waiting for server"

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
        $PanelTextSample = Format-PanelText $Sample
        $ProbePanel, $ProbePanelLabel = New-UsagePanel
        $PanelConstructed = $null -ne $ProbePanel -and $null -ne $ProbePanelLabel
        if ($PanelConstructed) { $ProbePanel.Dispose() }
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
            panel_text = $PanelTextSample
            panel_constructed = $PanelConstructed
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

    $Recommendation = Get-Value $State "recommendation" $null
    $RecommendationLabel = [string](Get-Value $Recommendation "label" "Waiting for guidance")
    $script:GuidanceItem.Text = Limit-Text "Guidance: $RecommendationLabel" 100

    $Activity = Get-Value $State "activity" $null
    $ActivityTitle = [string](Get-Value $Activity "title" "Waiting for activity")
    $script:ActivityItem.Text = Limit-Text "Activity: $ActivityTitle" 100

    Update-RecentSessions $State
    Update-UsagePanel $State
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
    try { Write-TrayStatus $true $script:Connected } catch { }
}

$CreatedNew = $false
$Mutex = New-Object System.Threading.Mutex($true, "Local\TokenMeterTray", [ref]$CreatedNew)
if (-not $CreatedNew) {
    $Mutex.Dispose()
    exit 0
}

[System.Windows.Forms.Application]::SetUnhandledExceptionMode(
    [System.Windows.Forms.UnhandledExceptionMode]::CatchException
)
$script:ExceptionHandler = [System.Delegate]::CreateDelegate(
    [System.Threading.ThreadExceptionEventHandler],
    [TokenMeterExceptionSuppressor].GetMethod("Handle")
)
[System.Windows.Forms.Application]::add_ThreadException($script:ExceptionHandler)
[System.Windows.Forms.Application]::EnableVisualStyles()
$script:NotifyIcon = New-Object System.Windows.Forms.NotifyIcon
$script:TrayIcon = New-TokenMeterIcon
$script:NotifyIcon.Icon = $script:TrayIcon
$script:NotifyIcon.Text = "Token Meter - starting"
$script:NotifyIcon.Visible = $true

$script:TraySettings = Load-TraySettings $SettingsPath
$script:PanelVisible = [bool]$script:TraySettings["panel_visible"]
$script:UsagePanel, $script:PanelLabel = New-UsagePanel

$script:UsagePanel.add_MouseDown({ param($s, $e) try { Start-UsagePanelDrag $s $e } catch { } })
$script:UsagePanel.add_MouseMove({ try { Move-UsagePanelDrag } catch { } })
$script:UsagePanel.add_MouseUp({ try { Stop-UsagePanelDrag } catch { } })
$script:UsagePanel.add_MouseEnter({ try { Set-PanelHoverText } catch { } })
$script:UsagePanel.add_MouseLeave({ try { Clear-PanelHoverText } catch { } })
$script:PanelLabel.add_MouseDown({ param($s, $e) try { Start-UsagePanelDrag $s $e } catch { } })
$script:PanelLabel.add_MouseUp({ try { Stop-UsagePanelDrag } catch { } })
$script:PanelLabel.add_MouseEnter({ try { Set-PanelHoverText } catch { } })
$script:PanelLabel.add_MouseLeave({ try { Clear-PanelHoverText } catch { } })
$script:UsagePanel.add_LocationChanged({
    # Only persist position when the user is actively dragging.
    if ($script:UsagePanelDragActive) {
        try {
            $script:TraySettings["panel_x"] = $script:UsagePanel.Left
            $script:TraySettings["panel_y"] = $script:UsagePanel.Top
            Save-TraySettings $SettingsPath $script:TraySettings
        } catch { }
    }
})
$script:UsagePanel.add_FormClosing({
    param($s, $e)
    if ($script:PanelVisible -and -not $script:TrayExiting -and $e.CloseReason -notin @(
        [System.Windows.Forms.CloseReason]::WindowsShutDown,
        [System.Windows.Forms.CloseReason]::TaskManagerClosing
    )) { $e.Cancel = $true }
})
$script:UsagePanel.add_VisibleChanged({
    # Restore panel if Windows hides it unexpectedly (shell transitions, etc.).
    # Direct call is safe: Show() fires VisibleChanged(true) and the guard
    # below returns immediately on that re-entry.
    if (-not $script:TrayExiting -and $script:PanelVisible -and -not $script:UsagePanel.Visible) {
        try { Show-UsagePanel } catch { }
    }
})

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

$script:TogglePanelItem = New-Object System.Windows.Forms.ToolStripMenuItem
$script:TogglePanelItem.Text = if ($script:PanelVisible) { "Hide usage panel" } else { "Show usage panel" }
$script:TogglePanelItem.add_Click({
    try {
        $script:PanelVisible = -not $script:PanelVisible
        $script:TraySettings["panel_visible"] = $script:PanelVisible
        Save-TraySettings $SettingsPath $script:TraySettings
        if ($script:PanelVisible) {
            Update-UsagePanel $script:LastState
            Show-UsagePanel
            $script:TogglePanelItem.Text = "Hide usage panel"
        } else {
            $script:PanelHovering = $false
            $script:UsagePanel.Hide()
            $script:TogglePanelItem.Text = "Show usage panel"
        }
    } catch { }
})
$Menu.Items.Add($script:TogglePanelItem) | Out-Null

$Quit = New-Object System.Windows.Forms.ToolStripMenuItem
$Quit.Text = "Quit tray widget"
$Quit.add_Click({ $script:Context.ExitThread() })
$Menu.Items.Add($Quit) | Out-Null

Set-TrayMenuTheme $Menu | Out-Null
$script:NotifyIcon.ContextMenuStrip = $Menu
$script:UsagePanel.ContextMenuStrip = $Menu
$script:PanelLabel.ContextMenuStrip = $Menu
$script:NotifyIcon.add_MouseClick({ Invoke-TrayMouseClick $_ })
$Menu.add_Opening({
    Invoke-TrayRefresh
    Set-TrayMenuTheme $Menu | Out-Null
})

$Timer = New-Object System.Windows.Forms.Timer
$Timer.Interval = 10000
$Timer.add_Tick({ try { Invoke-TrayRefresh } catch { } })
$script:Context = New-Object System.Windows.Forms.ApplicationContext

try {
    [System.IO.File]::WriteAllText($PidPath, "$PID`r`n", [System.Text.UTF8Encoding]::new($false))
    if ($script:PanelVisible) { Show-UsagePanel }
    Invoke-TrayRefresh
    $Timer.Start()
    [System.Windows.Forms.Application]::Run($script:Context)
} finally {
    $script:TrayExiting = $true
    $Timer.Stop()
    $Timer.Dispose()
    $script:NotifyIcon.Visible = $false
    $script:NotifyIcon.Dispose()
    $script:TrayIcon.Dispose()
    $Menu.Dispose()
    if ($null -ne $script:UsagePanel) {
        $script:UsagePanel.Visible = $false
        $script:UsagePanel.Dispose()
    }
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
