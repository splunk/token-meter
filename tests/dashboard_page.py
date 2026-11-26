"""The assembled dashboard page, for tests that read or execute page source.

The dashboard source lives in ``dashboard/`` parts. Tests that assert on the
served page, or extract functions from it in Node, read this one assembled
copy, so they see exactly what the server sends.
"""

import atexit
import os
import re
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

# A theme is a palette block plus a finish block (see specs/DESIGN.md → Token layers).
# Palettes: `:root,[data-theme=<default>]{…}` then `[data-theme=<id>]{…}`.
# Finishes: `:where(:root){…}` (the default) then `:where([data-theme=<id>],…){…}`.
THEME_BLOCK = re.compile(r"\n(?::root,)?\[data-theme=(\w+)\]\{([^{}]*)\n\}")
FINISH_BLOCK = re.compile(r"\n:where\((:root|\[data-theme=\w+\](?:,\[data-theme=\w+\])*)\)\{([^{}]*)\n\}")
DEFAULT_FINISH = ":root"


def _tokens(body):
    return dict(re.findall(r"--([\w-]+):([^;]+);", body))


def theme_palettes(css):
    """Each theme's palette block tokens, by theme id."""
    return {m.group(1): _tokens(m.group(2)) for m in THEME_BLOCK.finditer(css)}


def theme_finishes(css):
    """Each finish block's tokens, keyed by its :where() selector list (":root" for the default)."""
    return {m.group(1): _tokens(m.group(2)) for m in FINISH_BLOCK.finditer(css)}


def finish_of(theme, finishes):
    """The finish selector a theme takes: the one that lists it, or the default."""
    listed = [selector for selector in finishes if f"[data-theme={theme}]" in selector.split(",")]
    return listed[0] if listed else DEFAULT_FINISH


def resolved_theme_tokens(css):
    """Palette plus finish tokens for every theme (not the stylesheet root)."""
    finishes = theme_finishes(css)
    return {theme: {**finishes.get(finish_of(theme, finishes), {}), **palette}
            for theme, palette in theme_palettes(css).items()}
