import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == "posix", "POSIX shell integration")
class LinuxUpdateIntegrationTests(unittest.TestCase):
    def git(self, *args, cwd):
        return subprocess.run(
            ["git", *args], cwd=cwd, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

    def create_checkout(self, workspace):
        remote = workspace / "remote.git"
        seed = workspace / "seed"
        source = workspace / "source"
        self.git("init", "--bare", "--initial-branch=main", str(remote), cwd=workspace)
        seed.mkdir()
        self.git("init", "--initial-branch=main", cwd=seed)
        install_script = seed / "scripts" / "install-linux"
        install_script.parent.mkdir()
        install_script.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf '%s\\n' \"$TOKEN_METER_INSTALL_ROOT\" > \"$INSTALL_LOG\"\n"
            "touch \"$INSTALL_MARKER\"\n"
        )
        install_script.chmod(0o755)
        (seed / "version.txt").write_text("one\n")
        self.git("add", ".", cwd=seed)
        self.git(
            "-c", "user.name=Token Meter Test",
            "-c", "user.email=test@example.invalid",
            "commit", "-m", "initial", cwd=seed,
        )
        self.git("remote", "add", "origin", str(remote), cwd=seed)
        self.git("push", "--set-upstream", "origin", "main", cwd=seed)
        self.git("clone", "--branch", "main", str(remote), str(source), cwd=workspace)
        return seed, source

    def add_remote_update(self, seed):
        (seed / "version.txt").write_text("two\n")
        self.git("add", "version.txt", cwd=seed)
        self.git(
            "-c", "user.name=Token Meter Test",
            "-c", "user.email=test@example.invalid",
            "commit", "-m", "update", cwd=seed,
        )
        self.git("push", "origin", "main", cwd=seed)

    def run_update(self, runtime, source, status_path, **extra_env):
        fake_bin = status_path.parent / "bin"
        fake_bin.mkdir(exist_ok=True)
        fake_uname = fake_bin / "uname"
        fake_uname.write_text("#!/usr/bin/env bash\nprintf 'Linux\\n'\n")
        fake_uname.chmod(0o755)
        return subprocess.run(
            [str(runtime / "scripts" / "update"), str(source), str(status_path)],
            cwd=ROOT, check=False, capture_output=True, text=True,
            env={
                **os.environ,
                "HOME": str(status_path.parent),
                "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                **extra_env,
            },
        )

    def copy_dispatcher_runtime(self, workspace, with_helper=True):
        runtime = workspace / "runtime"
        scripts = runtime / "scripts"
        scripts.mkdir(parents=True)
        shutil.copy2(ROOT / "scripts" / "update", scripts / "update")
        if with_helper:
            shutil.copy2(ROOT / "scripts" / "update-linux", scripts / "update-linux")
        return runtime

    def test_fast_forwards_and_invokes_linux_installer_through_dispatcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            marker = workspace / "installer-marker"
            install_log = workspace / "installer.log"
            seed, source = self.create_checkout(workspace)
            self.add_remote_update(seed)
            runtime = self.copy_dispatcher_runtime(workspace)
            status_path = workspace / "status.json"

            result = self.run_update(
                runtime, source, status_path,
                INSTALL_LOG=str(install_log), INSTALL_MARKER=str(marker),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(marker.is_file())
            self.assertEqual(install_log.read_text().strip(), str(runtime))
            self.assertEqual((source / "version.txt").read_text(), "two\n")
            self.assertEqual(json.loads(status_path.read_text())["phase"], "complete")

    def test_safety_failures_leave_source_unchanged(self):
        for case in ("dirty", "diverged", "non_main"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                workspace = Path(tmp)
                seed, source = self.create_checkout(workspace)
                runtime = self.copy_dispatcher_runtime(workspace)
                expected_error = {
                    "dirty": "dirty_checkout",
                    "diverged": "diverged_checkout",
                    "non_main": "unsupported_update_branch",
                }[case]
                if case == "dirty":
                    (source / "version.txt").write_text("local change\n")
                elif case == "diverged":
                    (source / "version.txt").write_text("local commit\n")
                    self.git("add", "version.txt", cwd=source)
                    self.git(
                        "-c", "user.name=Token Meter Test",
                        "-c", "user.email=test@example.invalid",
                        "commit", "-m", "local commit", cwd=source,
                    )
                    self.add_remote_update(seed)
                else:
                    self.git("checkout", "-b", "feature", "--track", "origin/main", cwd=source)
                before = self.git("rev-parse", "HEAD", cwd=source).stdout.strip()
                before_status = self.git("status", "--porcelain", cwd=source).stdout
                before_version = (source / "version.txt").read_text()
                status_path = workspace / "status.json"

                result = self.run_update(runtime, source, status_path)

                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(
                    json.loads(status_path.read_text())["error_code"], expected_error,
                )
                self.assertEqual(
                    self.git("rev-parse", "HEAD", cwd=source).stdout.strip(), before,
                )
                self.assertEqual(
                    self.git("status", "--porcelain", cwd=source).stdout, before_status,
                )
                self.assertEqual((source / "version.txt").read_text(), before_version)

    def test_missing_or_nonexecutable_linux_helper_records_failed_status(self):
        for executable in (False, True):
            with self.subTest(executable=executable), tempfile.TemporaryDirectory() as tmp:
                workspace = Path(tmp)
                _, source = self.create_checkout(workspace)
                runtime = self.copy_dispatcher_runtime(workspace, with_helper=executable)
                if executable:
                    (runtime / "scripts" / "update-linux").chmod(0o644)
                status_path = workspace / "status.json"

                result = self.run_update(runtime, source, status_path)

                self.assertNotEqual(result.returncode, 0)
                status = json.loads(status_path.read_text())
                self.assertEqual(status["phase"], "failed")
                self.assertEqual(status["error_code"], "linux_helper_unavailable")
                self.assertFalse(status["available"])


if __name__ == "__main__":
    unittest.main()
