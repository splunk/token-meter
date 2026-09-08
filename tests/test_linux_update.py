import json
import os
import platform
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(platform.system() == "Linux", "Linux update integration")
class LinuxUpdateIntegrationTests(unittest.TestCase):
    def test_fast_forwards_and_invokes_linux_installer(self):
        def git(*args, cwd):
            return subprocess.run(
                ["git", *args], cwd=cwd, check=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            remote = workspace / "remote.git"
            seed = workspace / "seed"
            source = workspace / "source"
            marker = workspace / "installer-marker"
            install_log = workspace / "installer.log"

            git("init", "--bare", "--initial-branch=main", str(remote), cwd=workspace)
            seed.mkdir()
            git("init", "--initial-branch=main", cwd=seed)
            install_script = seed / "scripts" / "install-linux"
            install_script.parent.mkdir()
            install_script.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                f"printf '%s\\n' \"$TOKEN_METER_INSTALL_ROOT\" > {install_log}\n"
                f"touch {marker}\n"
            )
            install_script.chmod(0o755)
            (seed / "version.txt").write_text("one\n")
            git("add", ".", cwd=seed)
            git(
                "-c", "user.name=Token Meter Test",
                "-c", "user.email=test@example.invalid",
                "commit", "-m", "initial", cwd=seed,
            )
            git("remote", "add", "origin", str(remote), cwd=seed)
            git("push", "--set-upstream", "origin", "main", cwd=seed)
            git("clone", "--branch", "main", str(remote), str(source), cwd=workspace)

            (seed / "version.txt").write_text("two\n")
            git("add", "version.txt", cwd=seed)
            git(
                "-c", "user.name=Token Meter Test",
                "-c", "user.email=test@example.invalid",
                "commit", "-m", "update", cwd=seed,
            )
            git("push", "origin", "main", cwd=seed)

            status_path = workspace / "status.json"
            result = subprocess.run(
                [str(ROOT / "scripts" / "update-linux"), str(source), str(status_path)],
                cwd=ROOT, check=False, capture_output=True, text=True,
                env={**os.environ, "HOME": str(workspace)},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(marker.is_file())
            self.assertEqual(install_log.read_text().strip(), str(ROOT))
            self.assertEqual((source / "version.txt").read_text(), "two\n")
            self.assertEqual(json.loads(status_path.read_text())["phase"], "complete")


if __name__ == "__main__":
    unittest.main()
