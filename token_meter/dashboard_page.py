"""Assemble the browser dashboard from its ordered source parts.

The dashboard ships as one HTML page, but its source lives in ``dashboard/``
as small files so each view can be read and edited on its own. The order is
``dashboard/page.manifest``; the served page is the exact concatenation of the
listed parts, so no build step or third-party tool is involved.
"""

import os
import threading

MANIFEST_RELATIVE = os.path.join("dashboard", "page.manifest")
_MAX_PARTS = 200

_cache_lock = threading.Lock()
_cache = {"signature": None, "text": None}


def manifest_path(root):
    return os.path.join(root, MANIFEST_RELATIVE)


def dashboard_root(manifest):
    """Return the repository/runtime root that owns ``manifest``."""
    return os.path.dirname(os.path.dirname(os.path.abspath(manifest)))


def part_names(manifest):
    """Read the ordered part list; reject anything that could leave dashboard/."""
    with open(manifest, encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    names = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("/")
        if (
            os.path.isabs(line)
            or "\\" in line
            or any(part in ("", ".", "..") for part in parts)
        ):
            raise ValueError("dashboard manifest entries must be relative paths inside dashboard/")
        names.append(line)
    if not names or len(names) > _MAX_PARTS:
        raise ValueError("dashboard manifest must list between 1 and %d parts" % _MAX_PARTS)
    if len(set(names)) != len(names):
        raise ValueError("dashboard manifest lists a part twice")
    return names


def part_paths(manifest):
    base = os.path.dirname(os.path.abspath(manifest))
    return [os.path.join(base, *name.split("/")) for name in part_names(manifest)]


def _signature(manifest, paths):
    stamp = []
    for path in [manifest, *paths]:
        info = os.stat(path)
        stamp.append((path, info.st_mtime_ns, info.st_size))
    return tuple(stamp)


def assemble(manifest):
    """Return the full page text; cached until the manifest or any part changes."""
    paths = part_paths(manifest)
    signature = _signature(manifest, paths)
    with _cache_lock:
        if _cache["signature"] == signature:
            return _cache["text"]
    chunks = []
    for path in paths:
        with open(path, encoding="utf-8", newline="") as handle:
            chunks.append(handle.read())
    text = "".join(chunks)
    with _cache_lock:
        _cache["signature"] = signature
        _cache["text"] = text
    return text


def is_manifest(path):
    return bool(path) and os.path.basename(path) == os.path.basename(MANIFEST_RELATIVE)


def read_page(path):
    """Serve either an assembled manifest or an explicit single-file page."""
    if is_manifest(path):
        return assemble(path)
    with open(path, encoding="utf-8", newline="") as handle:
        return handle.read()


def main(argv):
    """Write the assembled page to a file, or to stdout when no path is given."""
    import sys

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    text = assemble(manifest_path(root))
    if len(argv) > 1:
        with open(argv[1], "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv))
