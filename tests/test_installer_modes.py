import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == "posix", "POSIX shell integration")
class InstallerModeTests(unittest.TestCase):
    def write_executable(self, path, source):
        path.write_text(textwrap.dedent(source).lstrip())
        path.chmod(0o755)

    def argument_environment(self, workspace, platform="TestOS"):
        fake_bin = workspace / "bin"
        fake_bin.mkdir()
        self.write_executable(
            fake_bin / "uname",
            f"""
            #!/usr/bin/env bash
            printf '%s\\n' {platform!r}
            """,
        )
        return {
            **os.environ,
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        }

    def test_installers_expose_backend_only_help_before_platform_checks(self):
        for relative_path in ("scripts/install", "scripts/install-linux"):
            with self.subTest(installer=relative_path), tempfile.TemporaryDirectory() as tmp:
                workspace = Path(tmp)
                result = subprocess.run(
                    ["bash", str(ROOT / relative_path), "--help"],
                    capture_output=True,
                    text=True,
                    env=self.argument_environment(workspace),
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("--backend-only", result.stdout)

    def test_installers_reject_unknown_options_before_platform_checks(self):
        for relative_path in ("scripts/install", "scripts/install-linux"):
            with self.subTest(installer=relative_path), tempfile.TemporaryDirectory() as tmp:
                workspace = Path(tmp)
                result = subprocess.run(
                    ["bash", str(ROOT / relative_path), "--not-an-install-option"],
                    capture_output=True,
                    text=True,
                    env=self.argument_environment(workspace),
                )

                self.assertEqual(result.returncode, 2)
                self.assertIn("Unknown option: --not-an-install-option", result.stderr)

    def installer_environment(self, workspace, platform):
        fake_bin = workspace / "bin"
        fake_bin.mkdir()
        command_log = workspace / "commands.log"
        install_root = workspace / "runtime"
        home = workspace / "home"
        home.mkdir()
        xdg_config = workspace / "config"
        real_python = subprocess.run(
            ["bash", "-lc", "command -v python3"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        self.write_executable(
            fake_bin / "uname",
            f"""
            #!/usr/bin/env bash
            printf '%s\\n' {platform!r}
            """,
        )
        self.write_executable(
            fake_bin / "python3",
            f"""
            #!/usr/bin/env bash
            if [[ "$*" == *bootstrap_git_delivery* ]]; then
              printf '{{"ok": true}}\\n'
              exit 0
            fi
            if [[ "$*" == *token_meter_tray.py*" --check"* ]]; then
              exit 0
            fi
            exec {real_python!r} "$@"
            """,
        )
        self.write_executable(
            fake_bin / "curl",
            """
            #!/usr/bin/env bash
            if [[ "${*: -1}" == */health ]]; then
              printf '{"ok": true, "state_ready": true, "page_path": "%s/page.html"}\n' "$TEST_INSTALL_ROOT"
            else
              printf '{"ok": true}\n'
            fi
            """,
        )
        self.write_executable(
            fake_bin / "ditto",
            """
            #!/usr/bin/env bash
            if [[ -d "$1" ]]; then
              mkdir -p "$2"
              cp -Rp "$1/." "$2/"
            else
              cp -p "$1" "$2"
            fi
            """,
        )
        self.write_executable(
            fake_bin / "swiftc",
            """
            #!/usr/bin/env bash
            exit 0
            """,
        )
        self.write_executable(
            fake_bin / "launchctl",
            """
            #!/usr/bin/env bash
            printf 'launchctl %s\n' "$*" >> "$TEST_COMMAND_LOG"
            if [[ "${1:-}" == "print" ]]; then
              if [[ "$*" == *com.token-meter.menubar* ]]; then
                printf '%s/scripts/run-menubar\n' "$TEST_INSTALL_ROOT"
              else
                printf '%s/meter.py\n' "$TEST_INSTALL_ROOT"
              fi
            fi
            """,
        )
        self.write_executable(
            fake_bin / "systemctl",
            """
            #!/usr/bin/env bash
            printf 'systemctl %s\n' "$*" >> "$TEST_COMMAND_LOG"
            if [[ "$*" == *"show token-meter-server.service"* ]]; then
              printf '%s/systemd/user/token-meter-server.service\n' "$XDG_CONFIG_HOME"
            elif [[ "$*" == *"show token-meter-tray.service"* ]]; then
              printf '%s/systemd/user/token-meter-tray.service\n' "$XDG_CONFIG_HOME"
            fi
            """,
        )
        self.write_executable(
            fake_bin / "journalctl",
            """
            #!/usr/bin/env bash
            printf 'journalctl %s\n' "$*" >> "$TEST_COMMAND_LOG"
            """,
        )
        bash_env = workspace / "bash-env"
        bash_env.write_text(
            "command() {\n"
            "  if [[ \"${1:-}\" == \"-v\" && \"${2:-}\" == \"swiftc\" ]]; then return 1; fi\n"
            "  builtin command \"$@\"\n"
            "}\n"
        )
        return {
            **os.environ,
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(xdg_config),
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "BASH_ENV": str(bash_env),
            "TOKEN_METER_INSTALL_ROOT": str(install_root),
            "TOKEN_METER_SOURCE_ROOT": str(ROOT),
            "TOKEN_METER_READINESS_TIMEOUT_SECONDS": "2",
            "TEST_COMMAND_LOG": str(command_log),
            "TEST_INSTALL_ROOT": str(install_root),
        }, install_root, command_log

    def test_macos_default_install_keeps_the_native_companion(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            env, install_root, command_log = self.installer_environment(workspace, "Darwin")
            env.pop("BASH_ENV")
            launch_agents = Path(env["HOME"]) / "Library" / "LaunchAgents"

            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "install")],
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((launch_agents / "com.token-meter.server.plist").is_file())
            self.assertTrue((launch_agents / "com.token-meter.menubar.plist").is_file())
            self.assertEqual((install_root / "INSTALL_MODE").read_text(), "full\n")
            self.assertIn(
                "bootstrap gui/", "\n".join(
                    line for line in command_log.read_text().splitlines()
                    if "com.token-meter.menubar" in line
                ),
            )

    def test_linux_default_install_keeps_the_tray_companion(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            env, install_root, command_log = self.installer_environment(workspace, "Linux")
            env.pop("BASH_ENV")
            systemd_dir = Path(env["XDG_CONFIG_HOME"]) / "systemd" / "user"

            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "install-linux")],
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((systemd_dir / "token-meter-server.service").is_file())
            self.assertTrue((systemd_dir / "token-meter-tray.service").is_file())
            self.assertEqual((install_root / "INSTALL_MODE").read_text(), "full\n")
            self.assertIn(
                "is-active --quiet token-meter-tray.service",
                command_log.read_text(),
            )

    def test_macos_backend_only_install_skips_swift_and_removes_companion_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            env, install_root, command_log = self.installer_environment(workspace, "Darwin")
            launch_agents = Path(env["HOME"]) / "Library" / "LaunchAgents"
            launch_agents.mkdir(parents=True)
            (launch_agents / "com.token-meter.menubar.plist").write_text("old companion")

            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "install"), "--backend-only"],
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((launch_agents / "com.token-meter.server.plist").is_file())
            self.assertFalse((launch_agents / "com.token-meter.menubar.plist").exists())
            self.assertEqual((install_root / "INSTALL_MODE").read_text(), "backend-only\n")
            self.assertNotIn("bootstrap gui/", "\n".join(
                line for line in command_log.read_text().splitlines()
                if "com.token-meter.menubar" in line
            ))
            self.assertIn("Backend-only installation: native companion skipped.", result.stdout)

    def test_linux_backend_only_install_skips_tray_check_and_removes_tray_unit(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            env, install_root, command_log = self.installer_environment(workspace, "Linux")
            systemd_dir = Path(env["XDG_CONFIG_HOME"]) / "systemd" / "user"
            systemd_dir.mkdir(parents=True)
            (systemd_dir / "token-meter-tray.service").write_text("old companion")

            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "install-linux"), "--backend-only"],
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((systemd_dir / "token-meter-server.service").is_file())
            self.assertFalse((systemd_dir / "token-meter-tray.service").exists())
            self.assertEqual((install_root / "INSTALL_MODE").read_text(), "backend-only\n")
            self.assertNotIn("is-active --quiet token-meter-tray.service", command_log.read_text())
            self.assertIn("Backend-only installation: native companion skipped.", result.stdout)


if __name__ == "__main__":
    unittest.main()
