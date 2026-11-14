"""Commit subject, first body line, and content-free changed-file kinds for the local Work model.

Held only in memory until handed to the classifier's queue; paths never leave this module.
"""

import collections
import re

from token_meter.domain.work_evidence import file_kind

MAX_SUBJECT_CHARS = 200
MAX_DETAIL_CHARS = 200
# Plain words for each file kind in the classifier's changed-files line; paths never leave the scan.
_FILE_KIND_WORDS = {"frontend": "UI", "backend": "code", "data": "data", "infra": "infrastructure",
                    "tooling": "scripts", "docs": "docs", "tests": "tests", "config": "config", "media": "images"}
# Git and Gerrit trailers name people or tracking ids, not the change, so they are never the detail line.
# Any "...-by:" key (Co-authored-by, Co-developed-by, Reported-and-tested-by), plus Gerrit and tracker keys.
# Tracker keys count only when an id follows ("Fixes: #12", "Fixes: 1a2b3c4 (...)"), so "Fixes: the crash"
# stays a detail line.
_TRAILER_RE = re.compile(
    r"^([a-z]+(?:-[a-z]+)*-by|reviewed-on|change-id|cc|bug|link):\s"
    r"|^(fixes|closes|resolves|refs):\s+(#?\d+\b|[0-9a-f]{7,40}\b)", re.I)
_QUOTED_PATH_RE = re.compile(r"\\([0-7]{3}|.)")


def _numstat_path(value):
    """The destination path of one numstat entry: unquoted, with renames resolved to the new name."""
    path = value.strip()
    if "{" in path and " => " in path:
        path = re.sub(r"\{[^{}]* => ([^{}]*)\}", r"\1", path).replace("//", "/")
    if " => " in path:
        path = path.split(" => ", 1)[1].strip()  # git quotes each side of a rename on its own
    if len(path) >= 2 and path[0] == path[-1] == '"':
        escapes = {"n": 10, "t": 9, '"': 34, "\\": 92}
        raw = bytearray()
        index = 1
        while index < len(path) - 1:
            match = _QUOTED_PATH_RE.match(path, index)
            if match:
                code = match.group(1)
                raw.append(int(code, 8) if len(code) == 3 else escapes.get(code, ord(code[0]) % 256))
                index = match.end()
            else:
                raw.extend(path[index].encode("utf-8"))
                index += 1
        path = raw.decode("utf-8", "replace")
    return path


def commit_details(output, files_listed=True):
    """Split ``show --numstat --format=%s%x00%b%x00`` output into (subject, first body line, files line).

    The files line is content-free: counts of changed file kinds and lines, never paths or names. When
    the files could not be listed (``files_listed`` False), it says so rather than claiming none changed.
    """
    subject, _sep, rest = str(output or "").partition("\0")
    body, _sep, numstat = rest.partition("\0")
    detail = next((line.strip() for line in body.splitlines()
                   if line.strip() and not _TRAILER_RE.match(line.strip())), "")
    kinds = collections.Counter()
    added = deleted = 0
    for line in numstat.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        kinds[_FILE_KIND_WORDS.get(file_kind(_numstat_path(parts[2])), "other")] += 1
        if parts[0].isdecimal() and parts[1].isdecimal():
            added += int(parts[0])
            deleted += int(parts[1])
    if not files_listed:
        files = "Changed files: not listed."
    elif kinds:
        files = ("Changed files: " + ", ".join(f"{count} {kind}" for kind, count in kinds.most_common())
                 + f" (+{added} / -{deleted} lines).")
    else:
        files = "Changed files: none."
    return (" ".join(subject.split())[:MAX_SUBJECT_CHARS], " ".join(detail.split())[:MAX_DETAIL_CHARS], files)
