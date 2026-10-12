import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from token_meter.packaging import load_manifest, manifest_source_files
from token_meter.quotas import anthropic as anthropic_quotas
from token_meter.quotas import openai as openai_quotas


ROOT = Path(__file__).resolve().parents[2]
WINDOWS_POWERSHELL_SCRIPTS = (
    "scripts/bootstrap-windows.ps1",
    "scripts/install-windows.ps1",
    "scripts/start-token-meter.ps1",
    "scripts/run-tray.ps1",
    "scripts/update-windows.ps1",
    "scripts/uninstall-windows.ps1",
)
WINDOWS_CMD_SCRIPTS = (
    "scripts/install-windows.cmd",
    "scripts/run-token-meter-mcp.cmd",
)
WINDOWS_SCRIPTS = WINDOWS_POWERSHELL_SCRIPTS + WINDOWS_CMD_SCRIPTS
WINDOWS_GIT_CONSUMERS = (
    "scripts/bootstrap-windows.ps1",
    "scripts/install-windows.ps1",
    "scripts/update-windows.ps1",
)


def git_lookup_expression(script):
    """Return the script's own git.exe lookup statement joined into one line."""
    lines = script.splitlines()
    index = next(
        position
        for position, line in enumerate(lines)
        if "Get-Command git.exe" in line
    )
    statement = [lines[index][lines[index].index("Get-Command git.exe"):].strip()]
    while statement[-1].endswith("|"):
        index += 1
        statement.append(lines[index].strip())
    return " ".join(statement)


class WindowsPackagingContracts(unittest.TestCase):
    def test_windows_quick_start_is_one_bootstrap_command(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        contributing = (ROOT / "specs" / "CONTRIBUTING.md").read_text(encoding="utf-8")
        match = re.search(r"^### Windows\n(?P<section>.*?)(?=^### )", readme, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(match, "README Windows Quick Start section is missing")
        section = match.group("section")
        blocks = re.findall(r"```powershell\n(?P<body>.*?)\n```", section, re.DOTALL)
        self.assertEqual(len(blocks), 1, "Windows Quick Start must have one command block")
        command_lines = [line for line in blocks[0].splitlines() if line.strip()]
        self.assertEqual(len(command_lines), 1, "Windows bootstrap must be one copy/paste command")
        command = command_lines[0]

        for marker in (
            "powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command",
            "$env:TEMP",
            "Invoke-WebRequest",
            "bootstrap-windows.ps1",
            "-OutFile $p",
            "& $p",
            "Remove-Item -LiteralPath $p",
        ):
            self.assertIn(marker, command)
        self.assertNotIn("Invoke-Expression", command)
        self.assertNotIn("git clone", section)
        self.assertNotIn("Set-Location", section)
        self.assertIn("Git and Python", section)
        self.assertIn("WinGet", section)
        self.assertIn("App Installer", section)
        self.assertIn(r".\scripts\install-windows.cmd", section)
        self.assertIn(r".\scripts\install-windows.cmd", contributing)

    def test_windows_bootstrap_owns_prerequisites_source_and_delegation(self):
        bootstrap_path = ROOT / "scripts" / "bootstrap-windows.ps1"
        self.assertTrue(bootstrap_path.is_file(), "Windows bootstrap is missing")
        bootstrap = bootstrap_path.read_text(encoding="utf-8")

        for marker in (
            "https://github.com/splunk/token-meter.git",
            "Install-GitPrerequisite",
            '"Git.MinGit"',
            "git.exe",
            '"status", "--porcelain"',
            "fetch",
            "merge",
            "--ff-only",
            ".installing-",
            "install-windows.cmd",
            "NoOpenDashboard",
            "http://127.0.0.1:8722",
        ):
            self.assertIn(marker, bootstrap)
        for forbidden in ("reset --hard", "clean -f", "Invoke-Expression", '"--force"'):
            self.assertNotIn(forbidden, bootstrap)

    def test_local_windows_wrapper_applies_bypass_and_forwards_exit_status(self):
        wrapper_path = ROOT / "scripts" / "install-windows.cmd"
        self.assertTrue(wrapper_path.is_file(), "Windows installer wrapper is missing")
        wrapper = wrapper_path.read_text(encoding="utf-8")

        self.assertIn(
            r"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe",
            wrapper,
        )
        self.assertIn("-ExecutionPolicy Bypass", wrapper)
        self.assertIn(r'"%~dp0install-windows.ps1" %*', wrapper)
        self.assertIn("exit /b", wrapper)

    def test_windows_powershell_entrypoints_use_process_scoped_policy_bypass(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        contributing = (ROOT / "specs" / "CONTRIBUTING.md").read_text(encoding="utf-8")
        installer = (ROOT / "scripts" / "install-windows.ps1").read_text(encoding="utf-8")

        self.assertIn(
            "powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command",
            readme,
        )
        self.assertIn(r".\scripts\install-windows.cmd", contributing)
        self.assertIn(
            '-NoLogo -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File',
            installer,
        )
        self.assertIn(
            'powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File',
            readme,
        )

    def test_installer_discovers_python_after_a_windows_install_updates_path(self):
        installer = (ROOT / "scripts" / "install-windows.ps1").read_text(encoding="utf-8")

        for marker in (
            'GetEnvironmentVariable("Path", [EnvironmentVariableTarget]::Machine)',
            'GetEnvironmentVariable("Path", [EnvironmentVariableTarget]::User)',
            'Registry::HKEY_CURRENT_USER\\Software\\Python',
            'Registry::HKEY_LOCAL_MACHINE\\Software\\Python',
            'Registry::HKEY_LOCAL_MACHINE\\Software\\WOW6432Node\\Python',
            'Get-PythonFromLauncher',
            '"py.exe"',
            '$env:ProgramFiles',
        ):
            self.assertIn(marker, installer)

    def test_windows_installer_acquires_only_missing_exact_prerequisites(self):
        installer = (ROOT / "scripts" / "install-windows.ps1").read_text(encoding="utf-8")

        self.assertIn('Install-Prerequisite "Git.MinGit"', installer)
        self.assertIn(
            'Install-Prerequisite "Python.Python.3.14" -UserScope',
            installer,
        )
        for flag in (
            '"--exact"',
            '"--source"',
            '"winget"',
            '"--silent"',
            '"--accept-package-agreements"',
            '"--accept-source-agreements"',
            '"--disable-interactivity"',
        ):
            self.assertIn(flag, installer)
        self.assertNotIn('"--force"', installer)
        self.assertIn("Get-UsableGit", installer)
        self.assertIn("Find-CompatiblePython", installer)
        self.assertIn(
            "could not be verified after WinGet reported success",
            installer,
        )

    def test_windows_scripts_accept_only_one_git_executable(self):
        for relative_path in WINDOWS_GIT_CONSUMERS:
            script = (ROOT / relative_path).read_text(encoding="utf-8")
            lookups = [
                script[match.start():match.start() + 160]
                for match in re.finditer(r"Get-Command git\.exe", script)
            ]
            self.assertTrue(lookups, f"{relative_path} never resolves git.exe")
            for lookup in lookups:
                self.assertIn(
                    "Select-Object -First 1",
                    lookup,
                    f"{relative_path} accepts every PATH match for git.exe",
                )

    @unittest.skipUnless(os.name == "nt", "Windows-native PATH resolution")
    def test_windows_git_lookup_survives_several_git_executables_on_path(self):
        """Git for Windows publishes git.exe in both cmd\\ and bin\\, and both are
        on PATH. An unfiltered Get-Command returns every match, so .Source becomes
        an array and the version probe fails as if Git were missing."""
        shell = shutil.which("pwsh") or shutil.which("powershell.exe")
        self.assertTrue(shell)

        for relative_path in WINDOWS_GIT_CONSUMERS:
            expression = git_lookup_expression(
                (ROOT / relative_path).read_text(encoding="utf-8")
            )
            with tempfile.TemporaryDirectory() as temporary_directory:
                directories = []
                for index in range(3):
                    directory = Path(temporary_directory) / str(index)
                    directory.mkdir()
                    (directory / "git.exe").write_bytes(b"")
                    directories.append(directory)

                synthetic_path = os.pathsep.join(map(str, directories))
                harness = Path(temporary_directory) / "probe.ps1"
                harness.write_text(
                    f"$env:Path = '{synthetic_path}'\n"
                    f"$Resolved = {expression}\n"
                    'Write-Output "count=$(@($Resolved).Count)"\n'
                    'Write-Output "source=$($Resolved.Source)"\n',
                    encoding="utf-8",
                )

                result = subprocess.run(
                    [
                        shell, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", str(harness),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("count=1", result.stdout, f"{relative_path}: {result.stdout}")
                self.assertIn(
                    f"source={directories[0] / 'git.exe'}",
                    result.stdout,
                    f"{relative_path} ignores PATH precedence: {result.stdout}",
                )

    def test_manifest_is_the_only_windows_staging_inventory(self):
        files = set(manifest_source_files(ROOT, load_manifest(ROOT / "runtime-manifest.txt")))
        self.assertTrue(set(WINDOWS_SCRIPTS).issubset(files))
        installer = (ROOT / "scripts" / "install-windows.ps1").read_text(encoding="utf-8")
        self.assertIn("runtime-manifest.txt", installer)
        self.assertIn("token_meter.packaging manifest", installer)
        self.assertIn("token_meter.packaging parity", installer)
        self.assertNotIn("$RequiredPaths", installer)
        self.assertNotIn("$RuntimeDirectory", installer)

    def test_windows_lifecycle_is_per_user_owned_and_local_only(self):
        installer = (ROOT / "scripts" / "install-windows.ps1").read_text(encoding="utf-8")
        starter = (ROOT / "scripts" / "start-token-meter.ps1").read_text(encoding="utf-8")
        uninstaller = (ROOT / "scripts" / "uninstall-windows.ps1").read_text(encoding="utf-8")
        for marker in (
            'Join-Path $env:LOCALAPPDATA "Token Meter\\runtime"',
            "HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run",
            '$InstallNonce = [Guid]::NewGuid().ToString("N")',
            "the runtime path contains files that do not belong to Token Meter",
            "Stop-InstalledServer",
            "Stop-InstalledTray",
        ):
            self.assertIn(marker, installer)
        self.assertIn("http://127.0.0.1:8722/health", starter)
        self.assertIn("Test-OwnedHealth", starter)
        self.assertIn("Remove-ItemProperty", uninstaller)
        self.assertIn("belongs to a different", uninstaller)
        self.assertNotIn("sudo", installer.lower())

    def test_windows_backend_only_mode_skips_the_tray_and_survives_updates(self):
        bootstrap = (ROOT / "scripts" / "bootstrap-windows.ps1").read_text(encoding="utf-8")
        installer = (ROOT / "scripts" / "install-windows.ps1").read_text(encoding="utf-8")
        starter = (ROOT / "scripts" / "start-token-meter.ps1").read_text(encoding="utf-8")
        updater = (ROOT / "scripts" / "update-windows.ps1").read_text(encoding="utf-8")

        self.assertIn("[switch]$BackendOnly", bootstrap)
        self.assertIn('$InstallerArguments += "-BackendOnly"', bootstrap)
        self.assertIn("[switch]$BackendOnly", installer)
        self.assertIn('$InstallMode = if ($BackendOnly) { "backend-only" } else { "full" }', installer)
        self.assertIn('Join-Path $StagingRoot "INSTALL_MODE"', installer)
        self.assertIn("if (-not $BackendOnly) {", installer)
        self.assertIn('Backend-only installation: notification-area companion skipped.', installer)

        mode_read = starter.index('Join-Path $RuntimeRoot "INSTALL_MODE"')
        backend_return = starter.index("if ($BackendOnly) {")
        tray_start = starter.index("$TrayProcess = $null")
        self.assertLess(mode_read, backend_return)
        self.assertLess(backend_return, tray_start)

        self.assertIn('Join-Path $RuntimeRoot "INSTALL_MODE"', updater)
        self.assertIn('$InstallArguments["BackendOnly"] = $true', updater)

    def test_windows_docs_explain_the_backend_only_switch(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        guide = (ROOT / "specs" / "USER_GUIDE.md").read_text(encoding="utf-8")

        for document in (readme, guide):
            self.assertIn("-BackendOnly", document)
            self.assertIn("notification-area companion", document)

    def test_windows_tray_uses_runtime_catalog_for_known_and_unknown_labels(self):
        tray = (ROOT / "scripts" / "run-tray.ps1").read_text(encoding="utf-8")
        self.assertIn("function Get-RuntimeLabel", tray)
        self.assertIn('Get-Value $State "runtime_catalog"', tray)
        self.assertIn('Get-Value $Catalog "unknown-runtime"', tray)
        self.assertNotIn('switch ($Provider)', tray)
        self.assertIn("System.Windows.Forms.NotifyIcon", tray)
        self.assertIn("known_runtime_label", tray)
        self.assertIn("unknown_runtime_label", tray)

    def test_windows_tray_negotiates_dpi_before_winforms_and_tracks_live_theme(self):
        tray = (ROOT / "scripts" / "run-tray.ps1").read_text(encoding="utf-8")

        for marker in (
            "SetProcessDpiAwarenessContext",
            "SetProcessDPIAware",
            "PerMonitorV2",
            "function Get-WindowsAppTheme",
            "AppsUseLightTheme",
            "function Set-TrayMenuTheme",
            "ToolStripProfessionalRenderer",
            "SystemColors]::GrayText",
            "dpi_awareness",
            "theme",
        ):
            self.assertIn(marker, tray)
        self.assertLess(
            tray.index("SetProcessDpiAwarenessContext"),
            tray.index("Add-Type -AssemblyName System.Windows.Forms"),
        )
        opening = tray.split("$Menu.add_Opening", 1)[1].split("$Timer =", 1)[0]
        self.assertIn("Set-TrayMenuTheme", opening)
        self.assertEqual(tray.count("Add-Type -TypeDefinition"), 1)

    def test_windows_extension_does_not_add_os_dispatch_to_runtime_parsers(self):
        branch = re.compile(
            r"\b(?:if|elif|match|case)\b[^\n]{0,160}\b(?:win32|windows|os\.name\s*==\s*['\"]nt)",
            re.IGNORECASE,
        )
        for path in (ROOT / "token_meter" / "runtimes").glob("*.py"):
            self.assertNotRegex(path.read_text(encoding="utf-8"), branch, path)

    def test_provider_cli_processes_receive_no_window_flag_when_available(self):
        subprocess_module = mock.Mock()
        subprocess_module.CREATE_NO_WINDOW = 0x08000000
        subprocess_module.TimeoutExpired = subprocess.TimeoutExpired
        subprocess_module.PIPE = subprocess.PIPE
        subprocess_module.DEVNULL = subprocess.DEVNULL
        subprocess_module.run.return_value = mock.Mock(stdout="{}", returncode=0)

        anthropic_quotas.auth_status(
            lambda name: r"C:\bin\claude.exe", lambda path: {}, subprocess_module,
        )

        self.assertEqual(
            subprocess_module.run.call_args.kwargs["creationflags"], 0x08000000
        )

        process = mock.Mock()
        process.stdin = mock.Mock()
        process.stdout = mock.Mock()
        process.poll.return_value = 0
        subprocess_module.Popen.return_value = process
        selector = mock.Mock()
        selectors_module = mock.Mock()
        selectors_module.DefaultSelector.return_value = selector
        selectors_module.EVENT_READ = 1
        openai_quotas.app_server_rate_limits(
            lambda name: r"C:\bin\codex.exe", lambda path: {},
            lambda process, selector, request_id, timeout: {},
            subprocess_module, selectors_module, 1,
        )
        self.assertEqual(
            subprocess_module.Popen.call_args.kwargs["creationflags"], 0x08000000
        )

    def test_windows_tray_self_backgrounds_unless_inline(self):
        tray = (ROOT / "scripts" / "run-tray.ps1").read_text(encoding="utf-8")
        starter = (ROOT / "scripts" / "start-token-meter.ps1").read_text(encoding="utf-8")
        self.assertIn("[switch]$Inline", tray)
        self.assertIn("-not $Inline", tray)
        self.assertIn("-WindowStyle Hidden", tray)
        self.assertIn("exit 0", tray)
        self.assertIn('"-Inline"', starter)

    def test_windows_tray_panel_functions_and_settings_exist(self):
        tray = (ROOT / "scripts" / "run-tray.ps1").read_text(encoding="utf-8")
        for marker in (
            "function Format-PanelText",
            "function New-UsagePanel",
            "function Set-PanelPosition",
            "function Update-UsagePanel",
            "function Load-TraySettings",
            "function Save-TraySettings",
            "tray-settings.json",
            "panel_visible",
            "panel_x",
            "panel_y",
            "Hide usage panel",
            "Show usage panel",
            "System.Windows.Forms.Form",
            "FormBorderStyle]::None",
            "ShowInTaskbar = $false",
            "TopMost = $true",
            "WorkingArea",
            "function Show-UsagePanel",
            "function Start-UsagePanelDrag",
            "function Move-UsagePanelDrag",
            "function Stop-UsagePanelDrag",
            "$script:UsagePanelDragActive",
            "UsagePanel.Capture",
            "UsagePanel.ContextMenuStrip",
            "PanelLabel.ContextMenuStrip",
            "add_FormClosing",
            "add_VisibleChanged",
            "TrayExiting",
            "panel_text",
            "panel_constructed",
            "SetUnhandledExceptionMode",
            "TokenMeterExceptionSuppressor",
            "CreateDelegate",
            "add_ThreadException",
        ):
            self.assertIn(marker, tray, f"run-tray.ps1 missing: {marker!r}")

    def test_windows_tray_preferences_survive_a_runtime_swap(self):
        tray = (ROOT / "scripts" / "run-tray.ps1").read_text(encoding="utf-8")
        installer = (ROOT / "scripts" / "install-windows.ps1").read_text(encoding="utf-8")

        self.assertIn('Join-Path $RuntimeParent "tray-settings.json"', tray)
        self.assertNotIn('Join-Path $RuntimeRoot "tray-settings.json"', tray)
        self.assertIn("function Migrate-LegacyTraySettings", installer)
        self.assertIn('Join-Path $InstallParent "tray-settings.json"', installer)
        self.assertIn(
            "Migrate-LegacyTraySettings $InstallRoot $PersistentTraySettingsPath",
            installer,
        )
        self.assertLess(
            installer.index("Migrate-LegacyTraySettings $InstallRoot $PersistentTraySettingsPath"),
            installer.index("Move-Item -LiteralPath $InstallRoot -Destination $BackupRoot"),
        )

    def test_windows_tray_panel_uses_the_target_screen_for_saved_positions(self):
        tray = (ROOT / "scripts" / "run-tray.ps1").read_text(encoding="utf-8")

        self.assertIn("has_panel_position", tray)
        self.assertIn("$TargetPoint = New-Object System.Drawing.Point($X, $Y)", tray)
        self.assertIn("Screen]::FromPoint($TargetPoint)", tray)
        self.assertIn('if ([bool]$script:TraySettings["has_panel_position"])', tray)
        self.assertIn('$script:TraySettings["has_panel_position"] = $true', tray)
        resize = tray.index("function Set-PanelText")
        refresh = tray.index("function Refresh-PanelText")
        self.assertIn(
            "Set-PanelPosition $script:UsagePanel $script:UsagePanel.Left $script:UsagePanel.Top",
            tray[resize:refresh],
        )
        self.assertNotIn("add_LocationChanged", tray)

    def test_windows_tray_drag_uses_the_cursor_screen_to_cross_monitors(self):
        tray = (ROOT / "scripts" / "run-tray.ps1").read_text(encoding="utf-8")
        move_drag = tray[
            tray.index("function Move-UsagePanelDrag"):
            tray.index("function Stop-UsagePanelDrag")
        ]

        self.assertIn("$Cursor = [System.Windows.Forms.Control]::MousePosition", move_drag)
        self.assertIn(
            "$CursorScreen = [System.Windows.Forms.Screen]::FromPoint($Cursor)",
            move_drag,
        )
        self.assertIn("$CursorScreen", move_drag)
        self.assertIn(
            "function Set-PanelPosition($Panel, $X, $Y, $TargetScreen = $null)",
            tray,
        )
        self.assertIn("if ($null -eq $TargetScreen)", tray)

    def test_windows_tray_hover_guidance_uses_a_tooltip_without_resizing_the_panel(self):
        tray = (ROOT / "scripts" / "run-tray.ps1").read_text(encoding="utf-8")
        refresh = tray[
            tray.index("function Refresh-PanelText"):
            tray.index("function Update-UsagePanel")
        ]

        self.assertNotIn("Right-click for options", tray)
        self.assertNotIn("PanelHovering", tray)
        self.assertNotIn("MouseEnter", tray)
        self.assertNotIn("MouseLeave", tray)
        self.assertNotIn("PanelHover", refresh)
        self.assertIn("$PanelTooltipText = \"Token Meter: right-click for options\"", tray)
        self.assertIn("New-Object System.Windows.Forms.ToolTip", tray)
        self.assertIn("$script:PanelToolTip.ShowAlways = $true", tray)
        self.assertIn(
            "$script:PanelToolTip.SetToolTip($script:UsagePanel, $PanelTooltipText)",
            tray,
        )
        self.assertIn(
            "$script:PanelToolTip.SetToolTip($script:PanelLabel, $PanelTooltipText)",
            tray,
        )

    def test_windows_tray_records_only_sanitized_ui_exception_types(self):
        tray = (ROOT / "scripts" / "run-tray.ps1").read_text(encoding="utf-8")

        self.assertIn('$TrayUiLog = Join-Path $RuntimeRoot "tray.ui.log"', tray)
        self.assertIn("function Write-TrayUiException", tray)
        self.assertIn("TokenMeterExceptionSuppressor]::Configure($TrayUiLog)", tray)
        self.assertIn("catch { Write-TrayUiException $_.Exception }", tray)
        diagnostic_start = tray.index("function Write-TrayUiException")
        diagnostic_end = tray.index("function Write-TrayStatus")
        diagnostic = tray[diagnostic_start:diagnostic_end]
        self.assertIn("[TokenMeterExceptionSuppressor]::Record($Exception)", diagnostic)
        recorder_start = tray.index("public static class TokenMeterExceptionSuppressor")
        recorder = tray[recorder_start:tray.index('"@', recorder_start)]
        for marker in (".Message", "StackTrace", "exception.ToString()", "Exception.ToString()"):
            self.assertNotIn(marker, recorder)
            self.assertNotIn(marker, diagnostic)

    def test_windows_tray_ui_log_is_bounded_and_separate_from_redirected_stderr(self):
        tray = (ROOT / "scripts" / "run-tray.ps1").read_text(encoding="utf-8")
        launcher = (ROOT / "scripts" / "start-token-meter.ps1").read_text(encoding="utf-8")
        recorder_start = tray.index("public static class TokenMeterExceptionSuppressor")
        recorder = tray[recorder_start:tray.index('"@', recorder_start)]

        self.assertIn('-RedirectStandardError $TrayErrorLog', launcher)
        self.assertNotIn('Join-Path $RuntimeRoot "tray.err.log"', tray)
        self.assertNotIn("tray.ui.log", launcher)
        self.assertIn("private const long MaxLogBytes = 65536;", recorder)
        self.assertIn("info.Length + line.Length > MaxLogBytes", recorder)
        self.assertIn("File.WriteAllText(logPath, line", recorder)
        self.assertEqual(tray.count("AppendAllText"), 1)

    def test_windows_tray_logs_each_server_outage_once(self):
        tray = (ROOT / "scripts" / "run-tray.ps1").read_text(encoding="utf-8")
        refresh = tray[
            tray.index("function Invoke-TrayRefresh"):
            tray.index("$CreatedNew = $false")
        ]
        success, failure = refresh.split("} catch {", 1)

        self.assertIn("$script:RefreshFailureLogged = $false", success)
        self.assertIn("if (-not $script:RefreshFailureLogged) {", failure)
        self.assertIn("$script:RefreshFailureLogged = $true", failure)
        self.assertLess(
            failure.index("if (-not $script:RefreshFailureLogged)"),
            failure.index("Write-TrayUiException $_.Exception"),
        )
        self.assertIn(
            "$Timer.add_Tick({ try { Invoke-TrayRefresh } catch { Write-TrayUiException $_.Exception } })",
            tray,
        )

    def test_windows_installer_settings_migration_cannot_abort_install(self):
        installer = (ROOT / "scripts" / "install-windows.ps1").read_text(encoding="utf-8")
        migrate = installer[
            installer.index("function Migrate-LegacyTraySettings"):
            installer.index('if ($env:OS -ne "Windows_NT")')
        ]

        self.assertIn("} catch {", migrate)
        self.assertIn("panel will use defaults", migrate)
        self.assertNotIn("throw", migrate)
        self.assertNotIn("Fail ", migrate)

    def test_windows_tray_panel_labels_missing_cost_as_unavailable(self):
        tray = (ROOT / "scripts" / "run-tray.ps1").read_text(encoding="utf-8")

        panel_start = tray.index("function Format-PanelText")
        panel_end = tray.index("function New-UsagePanel")
        panel = tray[panel_start:panel_end]
        self.assertIn("cost unavailable", panel)
        self.assertIn('Get-Value $State "total_cost" $null', panel)
        self.assertIn("tokens unavailable", panel)
        self.assertIn('$RawTokens = Get-Value $State "total_tokens" $null', panel)
        self.assertNotIn('Get-Value $State "total_tokens" 0', panel)
        self.assertIn("panel_text_unavailable = Format-PanelText", tray)

    def test_windows_tray_panel_places_the_catalog_runtime_after_its_verdict(self):
        tray = (ROOT / "scripts" / "run-tray.ps1").read_text(encoding="utf-8")
        panel_start = tray.index("function Format-PanelText")
        panel_end = tray.index("function New-UsagePanel")
        panel = tray[panel_start:panel_end]

        provider = '$Provider = [string](Get-Value $Source "provider" "")'
        runtime = "$RuntimeLabel = Get-RuntimeLabel $State $Provider"
        self.assertIn(provider, panel)
        self.assertIn(
            '$Provider = [string](Get-Value $State "provider" "")',
            panel,
        )
        self.assertIn(runtime, panel)
        self.assertIn("if ($VerdictLabel) { $Parts.Add($VerdictLabel) }", panel)
        self.assertIn("$Parts.Add($RuntimeLabel)", panel)
        self.assertLess(panel.index("$Parts.Add($VerdictLabel)"), panel.index("$Parts.Add($RuntimeLabel)"))

    def test_windows_tray_panel_uses_nonblank_text_when_refresh_fails(self):
        tray = (ROOT / "scripts" / "run-tray.ps1").read_text(encoding="utf-8")
        panel_start = tray.index("function New-UsagePanel")
        panel_end = tray.index("function Set-PanelPosition")
        panel = tray[panel_start:panel_end]
        refresh_start = tray.index("function Invoke-TrayRefresh")
        refresh_end = tray.index("$CreatedNew = $false")
        refresh = tray[refresh_start:refresh_end]

        self.assertIn('$Label.Text = "Token Meter - waiting for local usage data"', panel)
        self.assertIn('Set-PanelText "Token Meter | local server unavailable"', refresh)
        self.assertIn("Write-TrayUiException $_.Exception", refresh)

    def test_windows_docs_explain_the_tray_panel_and_inline_launch_mode(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        contributing = (ROOT / "specs" / "CONTRIBUTING.md").read_text(encoding="utf-8")

        for document in (readme, contributing):
            self.assertIn("floating usage panel", document)
            self.assertRegex(document, r"persist(?:s)?\s+across installs and updates")
            self.assertIn("-Inline", document)

    @unittest.skipUnless(os.name == "nt", "Windows-native PowerShell validation")
    def test_powershell_scripts_parse_and_tray_smoke_on_windows(self):
        shell = shutil.which("pwsh") or shutil.which("powershell.exe")
        self.assertTrue(shell)
        for relative in WINDOWS_POWERSHELL_SCRIPTS:
            command = [
                shell, "-NoLogo", "-NoProfile", "-Command",
                "[void][scriptblock]::Create([IO.File]::ReadAllText($args[0]))",
                str(ROOT / relative),
            ]
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
        smoke = subprocess.run(
            [shell, "-NoLogo", "-NoProfile", "-File",
             str(ROOT / "scripts" / "run-tray.ps1"), "-SmokeTest"],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(smoke.returncode, 0, smoke.stderr)
        self.assertIn('"known_runtime_label":"Kiro"', smoke.stdout)
        self.assertIn('"unknown_runtime_label":"Unknown Runtime"', smoke.stdout)
        self.assertRegex(smoke.stdout, r'"dpi_awareness":"(?:PerMonitorV2|SystemAware|Unavailable)"')
        self.assertRegex(smoke.stdout, r'"theme":"(?:light|dark)"')
        payload = json.loads(smoke.stdout.strip().splitlines()[-1])
        self.assertIn("125.0K tokens", payload["panel_text"])
        self.assertIn("$1.25 est", payload["panel_text"])
        self.assertIn("tokens unavailable", payload["panel_text_unavailable"])
        self.assertIn("cost unavailable", payload["panel_text_unavailable"])
        self.assertTrue(payload["panel_constructed"])

    @unittest.skipUnless(os.name == "nt", "Windows-native CMD validation")
    def test_local_windows_wrapper_executes_stub_and_preserves_exit_code(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            wrapper = temporary_root / "install-windows.cmd"
            shutil.copy2(ROOT / "scripts" / "install-windows.cmd", wrapper)
            (temporary_root / "install-windows.ps1").write_text(
                "param([string]$Sentinel)\n"
                "[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'sentinel.txt'), $Sentinel)\n"
                "exit 23\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                ["cmd.exe", "/d", "/c", str(wrapper), "forwarded-value"],
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(result.returncode, 23, result.stderr)
            self.assertEqual(
                (temporary_root / "sentinel.txt").read_text(encoding="utf-8"),
                "forwarded-value",
            )


if __name__ == "__main__":
    unittest.main()
