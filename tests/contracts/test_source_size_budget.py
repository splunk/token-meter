"""Source-file size budget.

Large single files are expensive for coding agents: they cannot be read whole,
so agents search them repeatedly and every result stays in context. New source
files stay under ``NEW_FILE_LIMIT``. Files that are already larger carry a
ceiling in ``LEGACY_CEILINGS``. Lower a ceiling, or delete the entry once a file is
split below the limit. Never raise a ceiling or add a new entry.
"""

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
NEW_FILE_LIMIT = 80_000
SOURCE_SUFFIXES = {".py", ".html", ".js", ".css", ".swift", ".ps1", ".sh"}
EXEMPT_PREFIXES = ("assets/vendor/", "tests/fixtures/")

# Ceilings are about 5% above each file's size when the budget was introduced,
# so normal maintenance still fits. This dict may only shrink.
LEGACY_CEILINGS = {
    "tests/test_meter.py": 1_500_000,
    "token_meter/app.py": 663_000,
    "tests/test_work_insights.py": 236_000,
    "menubar/TokenMeterMenuBar.swift": 148_000,
    "tests/test_git_delivery.py": 141_000,
    "docs/talk/index.html": 123_000,
    "token_meter/services/work_insights.py": 111_000,
    "token_meter/runtimes/claude.py": 110_000,
    "token_meter/domain/aggregates.py": 104_000,
    "token_meter/runtimes/codex.py": 97_000,
    "token_meter/runtimes/opencode.py": 96_000,
    "token_meter/domain/work.py": 85_000,
}


def tracked_sources():
    try:
        listed = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout.decode("utf-8").split("\0")
    except (OSError, subprocess.CalledProcessError):
        return None
    paths = []
    for name in listed:
        if not name or name.startswith(EXEMPT_PREFIXES):
            continue
        path = ROOT / name
        is_script = name.startswith("scripts/") and "." not in Path(name).name
        if (path.suffix in SOURCE_SUFFIXES or is_script) and path.is_file():
            paths.append(name)
    return paths


class SourceSizeBudgetTests(unittest.TestCase):
    def setUp(self):
        self.sources = tracked_sources()
        if self.sources is None:
            self.skipTest("git ls-files is unavailable")

    def test_source_files_stay_within_their_size_budget(self):
        over = []
        for name in self.sources:
            size = (ROOT / name).stat().st_size
            limit = LEGACY_CEILINGS.get(name, NEW_FILE_LIMIT)
            if size > limit:
                over.append(f"{name}: {size} bytes > {limit}")
        self.assertEqual(
            over,
            [],
            "Split the file instead of raising its budget; see specs/ARCHITECTURE.md.",
        )

    def test_legacy_ceilings_name_only_files_that_still_need_them(self):
        tracked = set(self.sources)
        stale = []
        for name, ceiling in LEGACY_CEILINGS.items():
            if name not in tracked:
                stale.append(f"{name}: no longer tracked; delete its ceiling")
            elif (ROOT / name).stat().st_size <= NEW_FILE_LIMIT:
                stale.append(f"{name}: now within the default limit; delete its ceiling")
            elif ceiling <= NEW_FILE_LIMIT:
                stale.append(f"{name}: ceiling is not above the default limit")
        self.assertEqual(stale, [])


if __name__ == "__main__":
    unittest.main()
