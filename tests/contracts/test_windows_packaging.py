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
