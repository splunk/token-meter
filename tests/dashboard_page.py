"""The assembled dashboard page, for tests that read or execute page source.

The dashboard source lives in ``dashboard/`` parts. Tests that assert on the
served page, or extract functions from it in Node, read this one assembled
copy, so they see exactly what the server sends.
"""

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from token_meter import dashboard_page  # noqa: E402

DASHBOARD_MANIFEST = dashboard_page.manifest_path(str(ROOT))


def page_text():
    return dashboard_page.assemble(DASHBOARD_MANIFEST)


def _write_assembled_page():
    directory = tempfile.mkdtemp(prefix="token-meter-page-")
    atexit.register(shutil.rmtree, directory, True)
    path = os.path.join(directory, "page.html")
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(page_text())
    return path


DASHBOARD_PAGE = _write_assembled_page()
