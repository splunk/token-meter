import os
import signal
import subprocess
import tempfile
import textwrap
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == "posix", "POSIX shell integration")
class MacMenuBarRunnerTests(unittest.TestCase):
    def test_runner_adopts_older_unstamped_binary_when_compiler_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            root = workspace / "runtime"
            (root / "scripts").mkdir(parents=True)
            (root / "menubar").mkdir()
            (root / "scripts" / "run-menubar").write_bytes(
                (ROOT / "scripts" / "run-menubar").read_bytes()
            )
            source = root / "menubar" / "TokenMeterMenuBar.swift"
            info = root / "menubar" / "Info.plist"
            source.write_text("import AppKit\n")
            info.write_text("plist")
            os.utime(source, (1000, 1000))
            os.utime(info, (1000, 1000))
            contents = root / ".build" / "Token Meter Menu Bar.app" / "Contents"
            (contents / "MacOS").mkdir(parents=True)
            (contents / "Info.plist").write_text("plist")
            legacy_bin = contents / "MacOS" / "token-meter-menubar"
            legacy_bin.write_text("#!/bin/sh\necho legacy-menu-launched\n")
            legacy_bin.chmod(0o755)
            fake_bin = workspace / "bin"
            fake_bin.mkdir()
            for name, content in {
                "uname": "#!/bin/sh\necho Darwin\n",
                "swiftc": "#!/bin/sh\necho 'compiler unavailable' >&2\nexit 1\n",
                "xcrun": "#!/bin/sh\necho '/no/installed/SDKs/MacOSX27.0.sdk'\n",
            }.items():
                executable = fake_bin / name
                executable.write_text(content)
                executable.chmod(0o755)
            env = {**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"}

            checked = subprocess.run(
                ["bash", str(root / "scripts" / "run-menubar"), "--check"],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertTrue((contents / "source.sha256").exists())

            launched = subprocess.run(
                ["bash", str(root / "scripts" / "run-menubar")],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(launched.returncode, 0, launched.stderr)
            self.assertEqual(launched.stdout.strip(), "legacy-menu-launched")

    def test_runner_reuses_compatible_build_until_source_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            root = workspace / "runtime"
            (root / "scripts").mkdir(parents=True)
            (root / "menubar").mkdir()
            (root / "scripts" / "run-menubar").write_bytes(
                (ROOT / "scripts" / "run-menubar").read_bytes()
            )
            (root / "menubar" / "Info.plist").write_text("plist")
            (root / "menubar" / "TokenMeterMenuBar.swift").write_text("import AppKit\n")
            sdk_dir = workspace / "SDKs"
            (sdk_dir / "MacOSX26.5.sdk").mkdir(parents=True)
            (sdk_dir / "MacOSX27.0.sdk").mkdir()
            fake_bin = workspace / "bin"
            fake_bin.mkdir()
            for name, content in {
                "uname": "#!/bin/sh\necho Darwin\n",
                "xcrun": f"#!/bin/sh\necho '{sdk_dir}/MacOSX27.0.sdk'\n",
                "ditto": "#!/bin/sh\ncp \"$1\" \"$2\"\n",
                "swiftc": """#!/bin/bash
sdk=''
output=''
typecheck=0
while (($#)); do
  case "$1" in
    -sdk) sdk="$2"; shift 2 ;;
    -o) output="$2"; shift 2 ;;
    -typecheck) typecheck=1; shift ;;
    *) shift ;;
  esac
done
[[ "$sdk" == *MacOSX26.5.sdk ]] || {
  echo 'SDK is not supported by the compiler' >&2
  exit 1
}
if (( ! typecheck )); then
  printf '#!/bin/sh\\necho menu-bar-launched\\n' > "$output"
  chmod +x "$output"
fi
""",
            }.items():
                executable = fake_bin / name
                executable.write_text(content)
                executable.chmod(0o755)
            env = {**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"}
            env.pop("SDKROOT", None)

            result = subprocess.run(
                ["bash", str(root / "scripts" / "run-menubar")],
                capture_output=True,
                text=True,
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "menu-bar-launched")

            (fake_bin / "swiftc").write_text(
                "#!/bin/sh\necho 'compiler no longer works' >&2\nexit 1\n"
            )
            cached = subprocess.run(
                ["bash", str(root / "scripts" / "run-menubar")],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(cached.returncode, 0, cached.stderr)
            self.assertEqual(cached.stdout.strip(), "menu-bar-launched")

            (root / "menubar" / "TokenMeterMenuBar.swift").write_text(
                "import AppKit\n// changed source\n"
            )
            stale = subprocess.run(
                ["bash", str(root / "scripts" / "run-menubar"), "--check"],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertNotEqual(stale.returncode, 0)


@unittest.skipUnless(os.name == "posix", "POSIX shell integration")
class InstallerOwnershipCheckTests(unittest.TestCase):
    def test_unix_installers_ask_health_whether_they_own_the_server(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("install", "install-linux"):
            script = (root / "scripts" / name).read_text(encoding="utf-8")
            with self.subTest(script=name):
                self.assertIn('ownership_root="$(cd "$INSTALL_ROOT" && pwd -P)"', script)
                self.assertIn('--data-urlencode "runtime=$ownership_root"', script)
                self.assertIn('payload.get("runtime_matches") is not True', script)
                self.assertNotIn("page_path", script)


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
              owned=false; [[ "$*" == *"runtime=$(cd "$TEST_INSTALL_ROOT" 2>/dev/null && pwd -P)"* ]] && owned=true
              printf '{"ok": true, "state_ready": true, "runtime_matches": %s}\n' "$owned"
            else
              printf '{"ok": true}\n'
            fi
            """,
        )
        developer_dir = workspace / "developer"
        (developer_dir / "usr" / "bin").mkdir(parents=True)
        self.write_executable(developer_dir / "usr" / "bin" / "swiftc", "#!/bin/sh\nexit 0\n")
        self.write_executable(
            fake_bin / "xcode-select",
            f"""
            #!/usr/bin/env bash
            [[ "${{1:-}}" == "-p" ]] && printf '%s\\n' {str(developer_dir)!r}
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
            while (($#)); do
              if [[ "$1" == "-o" ]]; then
                printf '#!/bin/sh\nexit 0\n' > "$2"
                chmod +x "$2"
                exit 0
              fi
              shift
            done
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

    def assert_plain_progress(self, stdout, *, menubar, background_build=True):
        # Captured (non-terminal) output gets plain lines: no spinner frames
        # or carriage returns, but a visible start and finish for each wait.
        self.assertNotIn("\r", stdout)
        self.assertIn(
            "Indexing local agent history (first run can take a few minutes)\u2026\n",
            stdout,
        )
        self.assertRegex(stdout, r"\u2713 Server ready \(\d+s\)\n")
        building = (
            "Finishing the menu bar companion\u2026\n" if background_build
            else "Building the menu bar companion (first build compiles Swift, ~1 min)\u2026\n"
        )
        if menubar:
            self.assertIn(building, stdout)
            self.assertRegex(stdout, r"\u2713 Menu bar companion ready \(\d+s\)\n")
            self.assertLess(stdout.index("Server ready"), stdout.index(building))
        self.assertLess(
            stdout.index("Server ready"), stdout.index("Token Meter installation complete.")
        )

    def run_installer_on_terminal(self, relative_path, env):
        import pty

        primary, secondary = pty.openpty()
        try:
            process = subprocess.Popen(
                ["bash", str(ROOT / relative_path)],
                stdin=subprocess.DEVNULL,
                stdout=secondary,
                stderr=subprocess.PIPE,
                env=env,
            )
            os.close(secondary)
            secondary = None
            chunks = []
            while True:
                try:
                    chunk = os.read(primary, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
            stderr = process.stderr.read().decode()
            process.stderr.close()
            returncode = process.wait(timeout=30)
        finally:
            if secondary is not None:
                os.close(secondary)
            os.close(primary)
        return returncode, b"".join(chunks).decode(), stderr

    def test_installers_draw_and_clear_a_spinner_on_a_terminal(self):
        for platform, relative_path in (("Darwin", "scripts/install"), ("Linux", "scripts/install-linux")):
            with self.subTest(installer=relative_path), tempfile.TemporaryDirectory() as tmp:
                workspace = Path(tmp)
                env, _, _ = self.installer_environment(workspace, platform)
                env.pop("BASH_ENV")
                # Hold readiness back for two polls so at least one frame draws.
                polls = workspace / "polls"
                self.write_executable(
                    workspace / "bin" / "curl",
                    f"""
                    #!/usr/bin/env bash
                    if [[ "${{*: -1}}" == */health ]]; then
                      count=$(( $(cat {str(polls)!r} 2>/dev/null || echo 0) + 1 ))
                      echo "$count" > {str(polls)!r}
                      if (( count < 3 )); then exit 7; fi
                      owned=false; [[ "$*" == *"runtime=$(cd "$TEST_INSTALL_ROOT" 2>/dev/null && pwd -P)"* ]] && owned=true
                      printf '{{"ok": true, "state_ready": true, "runtime_matches": %s}}\n' "$owned"
                    else
                      printf '{{"ok": true}}\n'
                    fi
                    """,
                )
                env["TOKEN_METER_READINESS_TIMEOUT_SECONDS"] = "20"

                returncode, output, stderr = self.run_installer_on_terminal(relative_path, env)

                self.assertEqual(returncode, 0, stderr)
                self.assertRegex(output, "\r[\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f] Indexing local agent history \\d+s")
                ready = output.index("\u2713 Server ready (")
                # The spinner line is blanked with spaces and the cursor returned
                # to column 0 before the finish line is printed.
                self.assertRegex(output[:ready], r"\r +\r$")
                self.assertIn("Token Meter installation complete.", output)

    def test_installer_timeout_stops_the_progress_ticker_and_keeps_failure_output(self):
        for platform, relative_path in (("Darwin", "scripts/install"), ("Linux", "scripts/install-linux")):
            with self.subTest(installer=relative_path), tempfile.TemporaryDirectory() as tmp:
                workspace = Path(tmp)
                env, _, _ = self.installer_environment(workspace, platform)
                env.pop("BASH_ENV")
                self.write_executable(workspace / "bin" / "curl", "#!/bin/sh\nexit 7\n")
                env["TOKEN_METER_READINESS_TIMEOUT_SECONDS"] = "2"

                # capture_output only returns once every writer, including a
                # leaked background ticker, has closed the pipe.
                result = subprocess.run(
                    ["bash", str(ROOT / relative_path)],
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=30,
                )

                self.assertEqual(result.returncode, 1)
                self.assertTrue(
                    result.stderr.rstrip().endswith(
                        "Token Meter installation failed: the local server did not finish "
                        "indexing within 2s at http://127.0.0.1:8722/health."
                    ),
                    result.stderr,
                )
                self.assertNotIn("\r", result.stdout)
                self.assertIn("Indexing local agent history", result.stdout)
                self.assertNotIn("Server ready", result.stdout)

    def test_failed_menubar_build_replays_its_output_and_removes_temp_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            env, _, _ = self.installer_environment(workspace, "Darwin")
            env.pop("BASH_ENV")
            temp_dir = workspace / "tmp"
            temp_dir.mkdir()
            env["TMPDIR"] = str(temp_dir)
            self.write_executable(
                workspace / "bin" / "swiftc",
                """
                #!/usr/bin/env bash
                [[ " $* " == *" -typecheck "* ]] && exit 0
                printf 'COMPILER-STDOUT\\n'
                printf 'COMPILER-STDERR\\n' >&2
                exit 1
                """,
            )

            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "install")],
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            failure = "Token Meter installation failed: the native companion cannot build"
            # The compiler's own stdout and stderr are replayed after the spinner
            # line is cleared, and stderr lands before the installer's failure line.
            self.assertIn("COMPILER-STDOUT\n", result.stdout)
            self.assertIn("COMPILER-STDERR", result.stderr)
            self.assertIn(failure, result.stderr)
            self.assertLess(result.stderr.index("COMPILER-STDERR"), result.stderr.index(failure))
            building = result.stdout.index("Finishing the menu bar companion")
            self.assertLess(result.stdout.index("Server ready"), building)
            self.assertLess(building, result.stdout.index("COMPILER-STDOUT"))
            self.assertNotIn("\r", result.stdout)
            self.assertNotIn("Menu bar companion ready", result.stdout)
            self.assertEqual(sorted(temp_dir.glob("token-meter-menubar-check.*")), [])

    def test_menu_bar_builds_while_the_server_indexes(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            env, install_root, _ = self.installer_environment(workspace, "Darwin")
            env.pop("BASH_ENV")
            built = workspace / "built"
            self.write_executable(
                workspace / "bin" / "swiftc",
                f"""
                #!/usr/bin/env bash
                [[ " $* " == *" -typecheck "* ]] && exit 0
                while (($#)); do
                  if [[ "$1" == "-o" ]]; then
                    printf '#!/bin/sh\nexit 0\n' > "$2"
                    chmod +x "$2"
                    touch {str(built)!r}
                    exit 0
                  fi
                  shift
                done
                """,
            )
            # Readiness waits for the compiled companion, so a build that only
            # starts after the server is ready would time out the install.
            self.write_executable(
                workspace / "bin" / "curl",
                f"""
                #!/usr/bin/env bash
                if [[ "${{*: -1}}" == */health ]]; then
                  [[ -e {str(built)!r} ]] || exit 7
                  owned=false; [[ "$*" == *"runtime=$(cd "$TEST_INSTALL_ROOT" 2>/dev/null && pwd -P)"* ]] && owned=true
                  printf '{{"ok": true, "state_ready": true, "runtime_matches": %s}}\n' "$owned"
                else
                  printf '{{"ok": true}}\n'
                fi
                """,
            )
            env["TOKEN_METER_READINESS_TIMEOUT_SECONDS"] = "20"

            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "install")],
                capture_output=True, text=True, env=env, timeout=60,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assert_plain_progress(result.stdout, menubar=True)
            self.assertEqual((install_root / "INSTALL_MODE").read_text(), "full\n")

    def test_missing_toolchain_never_runs_swiftc_before_the_server_is_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            env, install_root, _ = self.installer_environment(workspace, "Darwin")
            env.pop("BASH_ENV")
            # No developer directory: on a real Mac, swiftc would open Apple's
            # Command Line Tools install dialog.
            self.write_executable(
                workspace / "bin" / "xcode-select",
                "#!/bin/sh\necho 'xcode-select: error: unable to get active developer directory' >&2\nexit 2\n",
            )
            ready = workspace / "server-ready"
            early = workspace / "swiftc-before-ready"
            self.write_executable(
                workspace / "bin" / "swiftc",
                f"""
                #!/usr/bin/env bash
                [[ -e {str(ready)!r} ]] || touch {str(early)!r}
                [[ " $* " == *" -typecheck "* ]] && exit 0
                while (($#)); do
                  if [[ "$1" == "-o" ]]; then
                    printf '#!/bin/sh\nexit 0\n' > "$2"
                    chmod +x "$2"
                    exit 0
                  fi
                  shift
                done
                """,
            )
            self.write_executable(
                workspace / "bin" / "curl",
                f"""
                #!/usr/bin/env bash
                if [[ "${{*: -1}}" == */health ]]; then
                  touch {str(ready)!r}
                  owned=false; [[ "$*" == *"runtime=$(cd "$TEST_INSTALL_ROOT" 2>/dev/null && pwd -P)"* ]] && owned=true
                  printf '{{"ok": true, "state_ready": true, "runtime_matches": %s}}\n' "$owned"
                else
                  printf '{{"ok": true}}\n'
                fi
                """,
            )

            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "install")],
                capture_output=True, text=True, env=env, timeout=60,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(early.exists(), "swiftc ran before the server was ready")
            self.assert_plain_progress(result.stdout, menubar=True, background_build=False)
            self.assertEqual((install_root / "INSTALL_MODE").read_text(), "full\n")

    def test_failed_install_stops_the_background_menu_bar_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            env, _, _ = self.installer_environment(workspace, "Darwin")
            env.pop("BASH_ENV")
            temp_dir = workspace / "tmp"
            temp_dir.mkdir()
            env["TMPDIR"] = str(temp_dir)
            compiler_pid = workspace / "compiler.pid"
            self.write_executable(
                workspace / "bin" / "swiftc",
                f"""
                #!/usr/bin/env bash
                [[ " $* " == *" -typecheck "* ]] && exit 0
                echo $$ > {str(compiler_pid)!r}
                exec sleep 60
                """,
            )
            # Readiness keeps failing, but not before the compiler is running,
            # so the install always fails while the build is in flight.
            self.write_executable(
                workspace / "bin" / "curl",
                f"""
                #!/usr/bin/env bash
                for _ in $(seq 1 200); do
                  [[ -s {str(compiler_pid)!r} ]] && break
                  sleep 0.05
                done
                exit 7
                """,
            )
            env["TOKEN_METER_READINESS_TIMEOUT_SECONDS"] = "2"

            started = time.monotonic()
            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "install")],
                capture_output=True, text=True, env=env, timeout=45,
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("did not finish indexing within 2s", result.stderr)
            self.assertLess(time.monotonic() - started, 30)
            pid = int(compiler_pid.read_text())
            try:
                os.kill(pid, 0)
                alive = True
            except ProcessLookupError:
                alive = False
            if alive:
                os.kill(pid, signal.SIGKILL)
            self.assertFalse(alive, "background compiler outlived the failed install")
            self.assertEqual(sorted(temp_dir.glob("token-meter-menubar-check.*")), [])
    def test_linux_uninstall_stops_the_server_without_a_tray_unit(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            fake_bin = workspace / "bin"
            fake_bin.mkdir()
            config = workspace / "config"
            units = config / "systemd" / "user"
            units.mkdir(parents=True)
            (units / "token-meter-server.service").write_text("[Unit]\n")
            calls = workspace / "systemctl.log"
            # Like systemd, refuse the whole command when any listed unit
            # file is missing (backend-only installs have no tray unit).
            self.write_executable(
                fake_bin / "systemctl",
                f"""
                #!/usr/bin/env bash
                printf '%s\\n' "$*" >> {str(calls)!r}
                for arg in "$@"; do
                  if [[ "$arg" == *.service && ! -e {str(units)!r}/"$arg" ]]; then
                    exit 1
                  fi
                done
                exit 0
                """,
            )
            env = {
                **os.environ,
                "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                "XDG_CONFIG_HOME": str(config),
            }

            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "uninstall-systemd-user")],
                capture_output=True, text=True, env=env, timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--user disable --now token-meter-server.service", calls.read_text())
            self.assertFalse((units / "token-meter-server.service").exists())

    def git(self, *arguments, cwd):
        return subprocess.run(
            ["git", *arguments],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def test_macos_full_install_rejects_unbuildable_staged_menu_bar(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            env, install_root, command_log = self.installer_environment(workspace, "Darwin")
            env.pop("BASH_ENV")
            self.write_executable(
                workspace / "bin" / "swiftc",
                "#!/bin/sh\necho 'SDK is not supported by the compiler' >&2\nexit 1\n",
            )
            self.write_executable(
                workspace / "bin" / "xcrun",
                "#!/bin/sh\necho '/no/installed/SDKs/MacOSX27.0.sdk'\n",
            )

            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "install")],
                capture_output=True,
                text=True,
                env=env,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("compatible", result.stderr)
            self.assertTrue(install_root.exists())
            if command_log.exists():
                self.assertNotIn("com.token-meter.menubar.plist", command_log.read_text())

    def test_macos_full_install_rejects_failed_native_build_after_sdk_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            env, _, command_log = self.installer_environment(workspace, "Darwin")
            env.pop("BASH_ENV")
            self.write_executable(
                workspace / "bin" / "swiftc",
                """#!/bin/bash
if [[ "$*" == *-typecheck* ]]; then exit 0; fi
echo 'native source failed to compile' >&2
exit 1
""",
            )

            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "install")],
                capture_output=True,
                text=True,
                env=env,
            )

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("native source failed to compile", result.stderr)
            if command_log.exists():
                self.assertNotIn("com.token-meter.menubar.plist", command_log.read_text())

    def test_macos_full_install_rejects_native_smoke_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            env, _, command_log = self.installer_environment(workspace, "Darwin")
            env.pop("BASH_ENV")
            self.write_executable(
                workspace / "bin" / "swiftc",
                """#!/bin/bash
while (($#)); do
  if [[ "$1" == "-o" ]]; then
    printf '#!/bin/sh\necho "native smoke failed" >&2\nexit 1\n' > "$2"
    chmod +x "$2"
    exit 0
  fi
  shift
done
exit 0
""",
            )

            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "install")],
                capture_output=True,
                text=True,
                env=env,
            )

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("native smoke failed", result.stderr)
            self.assertNotIn(
                f"bootstrap gui/{os.getuid()} "
                + str(Path(env["HOME"]) / "Library" / "LaunchAgents" / "com.token-meter.menubar.plist"),
                command_log.read_text(),
            )

    def test_macos_full_install_adopts_legacy_binary_without_swiftc_after_server_is_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            env, install_root, command_log = self.installer_environment(workspace, "Darwin")
            self.write_executable(
                workspace / "bin" / "swiftc",
                "#!/bin/sh\necho 'compiler unavailable' >&2\nexit 1\n",
            )
            self.write_executable(
                workspace / "bin" / "xcrun",
                "#!/bin/sh\necho '/no/installed/SDKs/MacOSX27.0.sdk'\n",
            )
            contents = install_root / ".build" / "Token Meter Menu Bar.app" / "Contents"
            (contents / "MacOS").mkdir(parents=True)
            source_info = ROOT / "menubar" / "Info.plist"
            contents.joinpath("Info.plist").write_bytes(source_info.read_bytes())
            legacy_bin = contents / "MacOS" / "token-meter-menubar"
            self.write_executable(
                legacy_bin,
                """#!/bin/sh
grep -q 'bootstrap .*com.token-meter.server' "$TEST_COMMAND_LOG" 2>/dev/null || exit 1
exit 0
""",
            )
            source_swift = ROOT / "menubar" / "TokenMeterMenuBar.swift"
            newer = max(source_swift.stat().st_mtime, source_info.stat().st_mtime) + 3600
            os.utime(legacy_bin, (newer, newer))

            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "install")],
                capture_output=True,
                text=True,
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((contents / "source.sha256").exists())
            self.assertIn("com.token-meter.menubar.plist", command_log.read_text())

    def feature_and_main_source_checkout(self, workspace, *, advance_main):
        remote = workspace / "upstream.git"
        checkout = workspace / "checkout"
        self.git("clone", "--bare", "--no-local", str(ROOT), str(remote), cwd=workspace)
        self.git("clone", "--no-local", str(remote), str(checkout), cwd=workspace)
        self.git("config", "user.name", "Token Meter Test", cwd=checkout)
        self.git("config", "user.email", "token-meter-test@example.invalid", cwd=checkout)
        fixture_installer = checkout / "scripts" / "install"
        fixture_installer.write_bytes((ROOT / "scripts" / "install").read_bytes())
        if self.git("status", "--porcelain", cwd=checkout):
            self.git("add", "scripts/install", cwd=checkout)
            self.git("commit", "-m", "test: use candidate installer", cwd=checkout)
            self.git("push", "origin", "main", cwd=checkout)

        self.git("switch", "-c", "feature-install", cwd=checkout)
        (checkout / "installer-feature-marker").write_text("feature\n")
        self.git("add", "installer-feature-marker", cwd=checkout)
        self.git("commit", "-m", "test: feature install", cwd=checkout)
        feature_revision = self.git("rev-parse", "HEAD", cwd=checkout)
        self.git("push", "-u", "origin", "feature-install", cwd=checkout)

        self.git("switch", "main", cwd=checkout)
        if advance_main:
            (checkout / "installer-main-marker").write_text("main\n")
            self.git("add", "installer-main-marker", cwd=checkout)
            self.git("commit", "-m", "test: main install", cwd=checkout)
            self.git("push", "origin", "main", cwd=checkout)
        main_revision = self.git("rev-parse", "HEAD", cwd=checkout)
        self.git("switch", "feature-install", cwd=checkout)
        return checkout, feature_revision, main_revision

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
            self.assert_plain_progress(result.stdout, menubar=True)
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
            self.assert_plain_progress(result.stdout, menubar=False)
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

    def test_macos_main_install_preserves_and_replaces_a_clean_feature_managed_checkout(self):
        cases = (
            (False, False),
            (True, False),
            (True, True),
        )
        for advance_main, dangling_backup_collision in cases:
            with (
                self.subTest(
                    advance_main=advance_main,
                    dangling_backup_collision=dangling_backup_collision,
                ),
                tempfile.TemporaryDirectory() as tmp,
            ):
                workspace = Path(tmp)
                checkout, feature_revision, main_revision = self.feature_and_main_source_checkout(
                    workspace,
                    advance_main=advance_main,
                )
                env, install_root, _ = self.installer_environment(workspace, "Darwin")
                env.pop("TOKEN_METER_SOURCE_ROOT")

                feature_install = subprocess.run(
                    ["bash", str(checkout / "scripts" / "install"), "--backend-only"],
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=30,
                )
                self.assertEqual(feature_install.returncode, 0, feature_install.stderr)

                backup_base = workspace / f"source-diverged-{feature_revision[:12]}"
                if dangling_backup_collision:
                    backup_base.symlink_to(workspace / "missing-backup-target")
                self.git("switch", "main", cwd=checkout)
                main_install = subprocess.run(
                    ["bash", str(checkout / "scripts" / "install"), "--backend-only"],
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=30,
                )

                managed_source = workspace / "source"
                backup_source = Path(
                    f"{backup_base}-2" if dangling_backup_collision else backup_base
                )
                self.assertEqual(main_install.returncode, 0, main_install.stderr)
                self.assertEqual(self.git("rev-parse", "HEAD", cwd=managed_source), main_revision)
                self.assertEqual(self.git("rev-parse", "HEAD", cwd=backup_source), feature_revision)
                self.assertEqual(
                    (install_root / "SOURCE_CHECKOUT").read_text(),
                    f"{managed_source}\n",
                )
                self.assertIn("Preserved the divergent managed checkout at:", main_install.stdout)
                self.assertIn(str(backup_source), main_install.stdout)
                if dangling_backup_collision:
                    self.assertTrue(backup_base.is_symlink())

    def test_macos_failed_replacement_clone_restores_the_managed_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            checkout, feature_revision, _ = self.feature_and_main_source_checkout(
                workspace,
                advance_main=True,
            )
            env, _, _ = self.installer_environment(workspace, "Darwin")
            env.pop("TOKEN_METER_SOURCE_ROOT")
            feature_install = subprocess.run(
                ["bash", str(checkout / "scripts" / "install"), "--backend-only"],
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
            )
            self.assertEqual(feature_install.returncode, 0, feature_install.stderr)

            real_git = subprocess.run(
                ["bash", "-lc", "command -v git"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.write_executable(
                workspace / "bin" / "git",
                f"""
                #!/usr/bin/env bash
                if [[ "${{1:-}}" == "clone" && "${{*: -1}}" == "$TEST_FAIL_CLONE_TARGET" ]]; then
                  exit 97
                fi
                exec {real_git!r} "$@"
                """,
            )
            managed_source = workspace / "source"
            env["TEST_FAIL_CLONE_TARGET"] = str(managed_source)
            self.git("switch", "main", cwd=checkout)
            main_install = subprocess.run(
                ["bash", str(checkout / "scripts" / "install"), "--backend-only"],
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
            )

            self.assertEqual(main_install.returncode, 1)
            self.assertIn("the original checkout was restored", main_install.stderr)
            self.assertEqual(self.git("rev-parse", "HEAD", cwd=managed_source), feature_revision)
            self.assertEqual(list(workspace.glob("source-diverged-*")), [])

    def test_macos_does_not_move_dirty_or_explicit_managed_checkouts(self):
        for checkout_kind in ("dirty-default", "explicit"):
            with self.subTest(checkout_kind=checkout_kind), tempfile.TemporaryDirectory() as tmp:
                workspace = Path(tmp)
                checkout, feature_revision, _ = self.feature_and_main_source_checkout(
                    workspace,
                    advance_main=True,
                )
                env, _, _ = self.installer_environment(workspace, "Darwin")
                if checkout_kind == "explicit":
                    managed_source = workspace / "explicit-source"
                    env["TOKEN_METER_SOURCE_ROOT"] = str(managed_source)
                else:
                    managed_source = workspace / "source"
                    env.pop("TOKEN_METER_SOURCE_ROOT")
                feature_install = subprocess.run(
                    ["bash", str(checkout / "scripts" / "install"), "--backend-only"],
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=30,
                )
                self.assertEqual(feature_install.returncode, 0, feature_install.stderr)

                if checkout_kind == "dirty-default":
                    (managed_source / "local-change").write_text("preserve\n")
                self.git("switch", "main", cwd=checkout)
                main_install = subprocess.run(
                    ["bash", str(checkout / "scripts" / "install"), "--backend-only"],
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=30,
                )

                self.assertEqual(main_install.returncode, 1)
                self.assertEqual(self.git("rev-parse", "HEAD", cwd=managed_source), feature_revision)
                self.assertEqual(list(workspace.glob(f"{managed_source.name}-diverged-*")), [])
                if checkout_kind == "explicit":
                    self.assertIn("has diverged from the installed source", main_install.stderr)
                else:
                    self.assertIn("has local changes", main_install.stderr)
                    self.assertEqual(
                        (managed_source / "local-change").read_text(),
                        "preserve\n",
                    )

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
