import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=60)


class ShowHelperTests(unittest.TestCase):
    def test_prints_a_dashboard_function_with_its_location(self):
        result = run(sys.executable, "scripts/show", "renderTrendDelta")
        self.assertEqual(result.returncode, 0, result.stderr)
        first, *body = result.stdout.splitlines()
        self.assertRegex(first, r"^dashboard/js/[\w-]+\.js:\d+-\d+$")
        self.assertIn("function renderTrendDelta(", body[0])
        self.assertTrue(body[-1].strip().endswith("}"))

    def test_prints_a_python_definition_from_one_file(self):
        result = run(sys.executable, "scripts/show", "token_meter/dashboard_page.py", "part_names")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("token_meter/dashboard_page.py:"))
        self.assertIn("def part_names(manifest):", result.stdout)

    def test_missing_name_fails_without_output(self):
        result = run(sys.executable, "scripts/show", "noSuchDashboardFunctionAnywhere")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")


@unittest.skipUnless(shutil.which("node"), "Node.js is required to parse dashboard JavaScript")
class CheckPageTests(unittest.TestCase):
    def test_assembles_and_parses_the_dashboard(self):
        result = run("bash", "scripts/check-page")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "js ok")


if __name__ == "__main__":
    unittest.main()
