"""Privacy-bounded aggregation of locally observed successful Git pushes."""

import datetime
import hashlib
import math
import os
import re
import secrets
import sqlite3
import subprocess
import threading


LEDGER_SCHEMA_VERSION = 1
GIT_TIMEOUT_SECONDS = 10
MAX_GIT_OUTPUT_BYTES = 512 * 1024
MAX_REPOSITORIES = 50
MAX_REMOTE_REFS = 256
MAX_REFLOG_ENTRIES = 2_000
MAX_COMMITS_PER_PUSH = 2_000
MAX_COMMITS_PER_SCAN = 5_000
MAX_QUERY_PROJECTS = 500
MAX_QUERY_DAYS = 366
_GIT_OID_LENGTHS = frozenset((40, 64))
_MUTATING_OR_NETWORK_GIT_VERBS = frozenset({
    "fetch", "pull", "push", "checkout", "switch", "reset", "prune",
    "ls-remote", "remote-update", "update-ref", "merge", "rebase",
    "add", "commit", "am", "cherry-pick", "stash", "clean", "gc",
    "alias", "filter-branch", "remote",
})
_GIT_VERB_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]*$")


def git_argv(root, args):
    """Build a git command that ignores local aliases and hooks."""
    if not args:
        raise ValueError("Unsupported Git operation")
    verb = str(args[0])
    if verb.startswith("-") or not _GIT_VERB_RE.fullmatch(verb):
        raise ValueError("Unsupported Git operation")
    if _MUTATING_OR_NETWORK_GIT_VERBS.intersection(args):
        raise ValueError("Unsupported Git operation")
    return [
        "git",
        "-c", "core.hooksPath=/dev/null",
        "-c", "core.fsmonitor=",
        "-c", "alias.{}=".format(verb),
        "-C", root,
        *args,
    ]


def git_subprocess_environment():
    """Keep git from reading system/user config or prompting."""
    return {
        "PATH": os.environ.get("PATH") or os.defpath,
        "HOME": os.path.expanduser("~"),
        "LC_ALL": "C",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
    }
_REFLOG_TIMESTAMP_RE = re.compile(r"@\{([0-9]{1,20})\}$")
_LEDGER_TABLE_COLUMNS = {
    "delivery_observations": (
        "repo_key", "object_key", "observed_at", "day", "added", "deleted",
    ),
    "delivery_seen": ("repo_key", "object_key"),
    "delivery_project_mappings": ("project_key", "repo_key"),
    "delivery_repository_coverage": (
        "repo_key", "measured", "partial", "checked_at",
    ),
    "delivery_metadata": ("key", "value"),
}


def _valid_oid(value):
    value = str(value or "").strip().lower()
    return (
        len(value) in _GIT_OID_LENGTHS
        and all(character in "0123456789abcdef" for character in value)
    )


class GitDeliveryLedger:
    """Persist hashed object identity and numeric delivery evidence only."""

    def __init__(self, path, salt=""):
        self.path = os.fspath(path)
        self.salt = str(salt or "")
        self.initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self):
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with self._connect() as connection:
            incompatible = False
            for table, expected in _LEDGER_TABLE_COLUMNS.items():
                columns = tuple(
                    row[1] for row in connection.execute(
                        'PRAGMA table_info("{}")'.format(table)
                    )
                )
                if columns and columns != expected:
                    incompatible = True
                    break
            if incompatible:
                for table in _LEDGER_TABLE_COLUMNS:
                    connection.execute('DROP TABLE IF EXISTS "{}"'.format(table))
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS delivery_observations (
                    repo_key TEXT NOT NULL,
                    object_key TEXT NOT NULL,
                    observed_at INTEGER NOT NULL,
                    day TEXT NOT NULL,
                    added INTEGER NOT NULL,
                    deleted INTEGER NOT NULL,
                    PRIMARY KEY (repo_key, object_key)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS delivery_seen (
                    repo_key TEXT NOT NULL,
                    object_key TEXT NOT NULL,
                    PRIMARY KEY (repo_key, object_key)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS delivery_project_mappings (
                    project_key TEXT PRIMARY KEY,
                    repo_key TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS delivery_repository_coverage (
                    repo_key TEXT PRIMARY KEY,
                    measured INTEGER NOT NULL,
                    partial INTEGER NOT NULL,
                    checked_at INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS delivery_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS delivery_observations_repo_day
                ON delivery_observations (repo_key, day)
                """
            )
            stored_salt = connection.execute(
                "SELECT value FROM delivery_metadata WHERE key = 'salt'"
            ).fetchone()
            if stored_salt is None:
                self.salt = self.salt or secrets.token_hex(32)
                connection.execute(
                    "INSERT INTO delivery_metadata (key, value) VALUES ('salt', ?)",
                    (self.salt,),
                )
            else:
                self.salt = str(stored_salt["value"])
            connection.execute("PRAGMA user_version = {}".format(LEDGER_SCHEMA_VERSION))

    @staticmethod
    def _line_count(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Changed text lines must be non-negative integers.")
        if not math.isfinite(value) or int(value) != value or value < 0:
            raise ValueError("Changed text lines must be non-negative integers.")
        return int(value)

    @staticmethod
    def _timestamp(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Observed time must be a finite timestamp.")
        if not math.isfinite(value) or value < 0:
            raise ValueError("Observed time must be a finite timestamp.")
        return int(value)

    def record(self, repo_key, object_key, observed_at, added, deleted):
        if not isinstance(repo_key, str) or not repo_key:
            raise ValueError("Repository key is required.")
        if not isinstance(object_key, str) or not object_key:
            raise ValueError("Object key is required.")
        observed_at = self._timestamp(observed_at)
        added = self._line_count(added)
        deleted = self._line_count(deleted)
        day = datetime.datetime.fromtimestamp(observed_at).date().isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO delivery_observations
                    (repo_key, object_key, observed_at, day, added, deleted)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (repo_key, object_key, observed_at, day, added, deleted),
            )
            connection.execute(
                "INSERT OR IGNORE INTO delivery_seen (repo_key, object_key) VALUES (?, ?)",
                (repo_key, object_key),
            )
        return cursor.rowcount == 1

    def mark_seen(self, repo_key, object_key):
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO delivery_seen (repo_key, object_key) VALUES (?, ?)",
                (repo_key, object_key),
            )
        return cursor.rowcount == 1

    def has_seen(self, repo_key, object_key):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM delivery_seen WHERE repo_key = ? AND object_key = ?",
                (repo_key, object_key),
            ).fetchone()
        return row is not None

    def rows(self):
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT repo_key, object_key, observed_at, day, added, deleted
                FROM delivery_observations
                ORDER BY observed_at, repo_key, object_key
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def daily_rows(self, repo_keys, start_day, end_day):
        """Return date-bounded daily aggregates for a bounded repository set."""
        repo_keys = tuple(dict.fromkeys(
            value for value in repo_keys
            if isinstance(value, str) and value
        ))[:MAX_REPOSITORIES]
        try:
            start = datetime.date.fromisoformat(str(start_day))
            end = datetime.date.fromisoformat(str(end_day))
        except ValueError:
            return []
        if not repo_keys or start > end:
            return []
        placeholders = ", ".join("?" for _value in repo_keys)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT repo_key, day, SUM(added) AS added, SUM(deleted) AS deleted
                FROM delivery_observations
                WHERE repo_key IN ({}) AND day BETWEEN ? AND ?
                GROUP BY repo_key, day
                ORDER BY day, repo_key
                """.format(placeholders),
                (*repo_keys, start.isoformat(), end.isoformat()),
            ).fetchall()
        return [dict(row) for row in rows]

    def map_project(self, project_key, repo_key):
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO delivery_project_mappings
                    (project_key, repo_key) VALUES (?, ?)
                """,
                (project_key, repo_key),
            )

    def repo_key_for_project(self, project_key):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT repo_key FROM delivery_project_mappings WHERE project_key = ?",
                (project_key,),
            ).fetchone()
        return str(row["repo_key"]) if row else ""

    def set_repository_coverage(self, repo_key, measured, checked_at, partial=False):
        checked_at = self._timestamp(checked_at)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO delivery_repository_coverage
                    (repo_key, measured, partial, checked_at)
                VALUES (?, ?, ?, ?)
                """,
                (repo_key, int(bool(measured)), int(bool(partial)), checked_at),
            )

    def repository_coverage(self, repo_key):
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT measured, partial, checked_at
                FROM delivery_repository_coverage WHERE repo_key = ?
                """,
                (repo_key,),
            ).fetchone()
        if row is None:
            return {"measured": False, "partial": True, "checked_at": None}
        return {
            "measured": bool(row["measured"]),
            "partial": bool(row["partial"]),
            "checked_at": int(row["checked_at"]),
        }

    def set_last_checked(self, value):
        value = self._timestamp(value)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO delivery_metadata (key, value)
                VALUES ('last_checked', ?)
                """,
                (str(value),),
            )

    def last_checked(self):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM delivery_metadata WHERE key = 'last_checked'"
            ).fetchone()
        try:
            return int(row["value"]) if row else None
        except (TypeError, ValueError):
            return None

    def baseline_at(self):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM delivery_metadata WHERE key = 'baseline_at'"
            ).fetchone()
        try:
            return int(row["value"]) if row else None
        except (TypeError, ValueError):
            return None

    def clear(self, baseline_at):
        baseline_at = self._timestamp(baseline_at)
        with self._connect() as connection:
            connection.execute("DELETE FROM delivery_observations")
            connection.execute("DELETE FROM delivery_seen")
            connection.execute("DELETE FROM delivery_project_mappings")
            connection.execute("DELETE FROM delivery_repository_coverage")
            connection.execute("DELETE FROM delivery_metadata WHERE key = 'last_checked'")
            connection.execute(
                """
                INSERT OR REPLACE INTO delivery_metadata (key, value)
                VALUES ('baseline_at', ?)
                """,
                (str(baseline_at),),
            )


class GitDeliveryService:
    """Read successful-push reflogs without contacting or mutating a remote."""

    def __init__(self, ledger_path, runner=None, now=None, salt=""):
        self.ledger = GitDeliveryLedger(ledger_path, salt)
        self._runner = runner or self._subprocess_runner
        self._now = now or (lambda: datetime.datetime.now().timestamp())
        self._salt = self.ledger.salt
        self._baseline_at = self.ledger.baseline_at()
        self._scan_lock = threading.Lock()
        self._project_repo_keys = {}
        self._last_coverage = {
            "repositories": 0,
            "measured": 0,
            "partial": 0,
            "codes": [],
            "last_checked": self.ledger.last_checked(),
        }

    @staticmethod
    def _subprocess_runner(argv, timeout):
        return subprocess.run(
            argv,
            check=False,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=git_subprocess_environment(),
        )

    @staticmethod
    def _result_parts(result):
        if isinstance(result, dict):
            code = result.get("returncode")
            stdout = result.get("stdout", "")
        else:
            code = getattr(result, "returncode", None)
            stdout = getattr(result, "stdout", "")
        return code, str(stdout or "")[:MAX_GIT_OUTPUT_BYTES]

    def _run_git(self, root, args):
        try:
            argv = git_argv(root, args)
        except ValueError:
            raise
        try:
            result = self._runner(argv, timeout=GIT_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired):
            return None, ""
        return self._result_parts(result)

    def _hash(self, value):
        payload = "{}\0{}".format(self._salt, value).encode("utf-8", "replace")
        return hashlib.sha256(payload).hexdigest()

    def project_suffix(self, value):
        """Return a compact per-machine opaque project discriminator."""
        return self._hash(str(value or ""))[:6]

    def clear(self):
        """Forget observations and baseline reflog history already present."""
        with self._scan_lock:
            baseline_at = int(self._now())
            self.ledger.clear(baseline_at)
            self._baseline_at = baseline_at
            self._project_repo_keys = {}
            self._last_coverage = {
                "repositories": 0,
                "measured": 0,
                "partial": 0,
                "codes": ["history_cleared"],
                "last_checked": None,
            }

    @staticmethod
    def _identity(value):
        value = str(value or "").strip()
        if not value or len(value) > 320 or any(ord(character) < 32 for character in value):
            return ""
        return value.casefold()

    def _identity_for(self, root):
        code, output = self._run_git(root, ("config", "--get", "user.email"))
        identity = self._identity(output) if code == 0 else ""
        if identity:
            return identity
        code, output = self._run_git(
            root, ("config", "--global", "--get", "user.email"),
        )
        return self._identity(output) if code == 0 else ""

    @staticmethod
    def _remote_refs(output):
        refs = []
        for line in str(output or "").splitlines():
            ref = line.strip()
            if (
                ref.startswith("refs/remotes/")
                and not ref.endswith("/HEAD")
                and len(ref) <= 1024
            ):
                refs.append(ref)
        return tuple(dict.fromkeys(refs))

    @staticmethod
    def _reflog_entries(output):
        entries = []
        for line in str(output or "").splitlines():
            parts = line.split("\0")
            if len(parts) != 3 or not _valid_oid(parts[0]):
                continue
            match = _REFLOG_TIMESTAMP_RE.search(parts[2].strip())
            if not match:
                continue
            entries.append({
                "oid": parts[0].strip().lower(),
                "subject": parts[1].strip(),
                "observed_at": int(match.group(1)),
            })
        return entries

    @staticmethod
    def _numstat(output):
        added = deleted = 0
        for line in str(output or "").splitlines():
            left, separator, rest = line.partition("\t")
            middle, separator2, _path = rest.partition("\t")
            if not separator or not separator2 or left == "-" or middle == "-":
                continue
            if left.isdecimal() and middle.isdecimal():
                added += int(left)
                deleted += int(middle)
        return added, deleted

    def _introduced_commits(self, root, new_oid, old_oid):
        args = [
            "rev-list", "--topo-order", "--reverse",
            "--max-count={}".format(MAX_COMMITS_PER_PUSH + 1), new_oid,
        ]
        if _valid_oid(old_oid):
            args.append("^{}".format(old_oid))
        code, output = self._run_git(root, tuple(args))
        if code != 0:
            return (), True
        commits = [
            line.strip().lower() for line in output.splitlines()
            if _valid_oid(line.strip())
        ]
        limited = len(commits) > MAX_COMMITS_PER_PUSH
        return tuple(commits[:MAX_COMMITS_PER_PUSH]), limited

    def _scan_candidate(self, candidate, checked_at, remaining):
        root = candidate.get("root") if isinstance(candidate, dict) else ""
        if not isinstance(root, str) or not root or len(root) > 4096:
            return 0, 0, "repository_unavailable", False, True, remaining
        code, output = self._run_git(root, ("rev-parse", "--show-toplevel"))
        resolved = str(output or "").strip().splitlines()[0] if output else ""
        if code != 0 or not os.path.isabs(resolved) or len(resolved) > 4096:
            return 0, 0, "repository_unavailable", False, True, remaining
        resolved = os.path.normpath(resolved)
        repo_key = self._hash(resolved)
        source_root = str(candidate.get("root") or "")
        self.ledger.map_project(self._hash(source_root), repo_key)
        project = candidate.get("project") if isinstance(candidate, dict) else ""
        if isinstance(project, str) and project:
            self._project_repo_keys[project] = repo_key
        if repo_key in self._active_repo_keys:
            return 0, 0, "coalesced", True, False, remaining
        self._active_repo_keys.add(repo_key)
        identity = self._identity_for(resolved)
        if not identity:
            self.ledger.set_repository_coverage(repo_key, False, checked_at, partial=True)
            return 0, 0, "identity_unavailable", False, True, remaining

        code, output = self._run_git(
            resolved, ("for-each-ref", "--format=%(refname)", "refs/remotes"),
        )
        if code != 0:
            self.ledger.set_repository_coverage(repo_key, False, checked_at, partial=True)
            return 0, 0, "remote_refs_unavailable", False, True, remaining
        refs = list(self._remote_refs(output))
        refs_limited = len(refs) > MAX_REMOTE_REFS
        refs = refs[:MAX_REMOTE_REFS]
        if not refs:
            self.ledger.set_repository_coverage(repo_key, False, checked_at, partial=True)
            return 0, 0, "no_remote_tracking_refs", False, True, remaining

        added = deleted = 0
        partial = refs_limited
        saw_push_history = False
        for ref in refs:
            code, output = self._run_git(resolved, (
                "reflog", "show", "--date=unix",
                "--format=%H%x00%gs%x00%gd",
                "--max-count={}".format(MAX_REFLOG_ENTRIES + 1), ref,
            ))
            if code != 0:
                partial = True
                continue
            entries = self._reflog_entries(output)
            if len(entries) > MAX_REFLOG_ENTRIES:
                partial = True
                entries = entries[:MAX_REFLOG_ENTRIES]
            for index, entry in enumerate(entries):
                if entry["subject"] != "update by push":
                    continue
                if self._baseline_at is not None and entry["observed_at"] <= self._baseline_at:
                    continue
                saw_push_history = True
                old_oid = entries[index + 1]["oid"] if index + 1 < len(entries) else ""
                commits, limited = self._introduced_commits(
                    resolved, entry["oid"], old_oid,
                )
                partial = partial or limited
                for oid in commits:
                    if remaining <= 0:
                        partial = True
                        break
                    remaining -= 1
                    object_key = self._hash(oid)
                    if self.ledger.has_seen(repo_key, object_key):
                        continue
                    code, author = self._run_git(
                        resolved, ("show", "-s", "--format=%ae%x00%P", oid),
                    )
                    if code != 0:
                        partial = True
                        continue
                    author_parts = str(author or "").strip().split("\0", 1)
                    author_email = self._identity(author_parts[0] if author_parts else "")
                    parents = author_parts[1].split() if len(author_parts) > 1 else []
                    if author_email != identity or len(parents) > 1:
                        self.ledger.mark_seen(repo_key, object_key)
                        continue
                    code, numstat = self._run_git(
                        resolved, ("show", "--numstat", "--format=", oid),
                    )
                    if code != 0:
                        partial = True
                        continue
                    line_added, line_deleted = self._numstat(numstat)
                    if self.ledger.record(
                        repo_key, object_key, entry["observed_at"],
                        line_added, line_deleted,
                    ):
                        added += line_added
                        deleted += line_deleted
                if remaining <= 0:
                    break
            if remaining <= 0:
                break

        measured = saw_push_history
        partial = partial or not measured
        self.ledger.set_repository_coverage(
            repo_key, measured, checked_at, partial=partial,
        )
        code_name = "scan_limited" if saw_push_history and partial else (
            "ready" if saw_push_history else "no_push_history"
        )
        return added, deleted, code_name, measured, partial, remaining

    def scan(self, candidates):
        """Inspect local successful-push reflogs in one non-overlapping scan."""
        if not self._scan_lock.acquire(blocking=False):
            return {
                "ok": True,
                "busy": True,
                "new_added": 0,
                "new_deleted": 0,
                "new_changed_lines": 0,
                "coverage": dict(self._last_coverage),
            }
        try:
            checked_at = int(self._now())
            self._baseline_at = self.ledger.baseline_at()
            candidate_rows = list(candidates or ())
            eligible = candidate_rows[:MAX_REPOSITORIES]
            repositories_limited = len(candidate_rows) > MAX_REPOSITORIES
            self._active_repo_keys = set()
            remaining = MAX_COMMITS_PER_SCAN
            added = deleted = measured = partial_count = 0
            codes = set()
            for candidate in eligible:
                item_added, item_deleted, code, item_measured, item_partial, remaining = (
                    self._scan_candidate(candidate, checked_at, remaining)
                )
                added += item_added
                deleted += item_deleted
                codes.add(code)
                if code != "coalesced":
                    measured += int(item_measured)
                    partial_count += int(item_partial or not item_measured)
            if repositories_limited:
                codes.add("repository_limit")
                partial_count += 1
            self._last_coverage = {
                "repositories": len(eligible),
                "measured": measured,
                "partial": partial_count,
                "codes": sorted(codes),
                "last_checked": checked_at,
            }
            self.ledger.set_last_checked(checked_at)
            return {
                "ok": True,
                "new_added": added,
                "new_deleted": deleted,
                "new_changed_lines": added + deleted,
                "coverage": dict(self._last_coverage),
            }
        finally:
            self._scan_lock.release()

    def _windows(self, range_key):
        today = datetime.datetime.fromtimestamp(float(self._now())).date()
        lengths = {"today": 1, "yesterday": 1, "7": 7, "30": 30, "90": 90}
        if range_key == "all":
            return (
                (today - datetime.timedelta(days=MAX_QUERY_DAYS - 1), today),
                None,
            )
        if range_key not in lengths:
            return None
        length = lengths[range_key]
        end = today if range_key != "yesterday" else today - datetime.timedelta(days=1)
        start = end - datetime.timedelta(days=length - 1)
        previous_end = start - datetime.timedelta(days=1)
        previous_start = previous_end - datetime.timedelta(days=length - 1)
        return (start, end), (previous_start, previous_end)

    @staticmethod
    def _in_window(day, window):
        if window is None or not isinstance(day, str) or len(day) != 10:
            return False
        start, end = window
        return (start is None or day >= start.isoformat()) and day <= end.isoformat()

    def _project_repo_mapping(self, projects, candidates):
        source_projects = sorted({
            value for value in projects if isinstance(value, str) and value
        })[:MAX_QUERY_PROJECTS]
        source_repo_keys = {}
        for candidate in candidates or ():
            if not isinstance(candidate, dict):
                continue
            root = candidate.get("root")
            project = candidate.get("project")
            if not isinstance(root, str) or project not in source_projects:
                continue
            source_repo_keys[project] = (
                self._project_repo_keys.get(project)
                or self.ledger.repo_key_for_project(self._hash(root))
            )
        canonical_by_repo = {}
        for label, repo_key in source_repo_keys.items():
            if repo_key:
                canonical_by_repo[repo_key] = min(
                    canonical_by_repo.get(repo_key, label), label,
                )
        canonical_by_source = {
            label: canonical_by_repo.get(repo_key, label)
            for label, repo_key in source_repo_keys.items()
        }
        labels = sorted(
            set(canonical_by_source.values())
            | (set(source_projects) - set(source_repo_keys))
        )
        repo_by_label = {
            label: repo_key for repo_key, label in canonical_by_repo.items()
        }
        return labels, canonical_by_source, repo_by_label

    @staticmethod
    def _percent_change(current, previous):
        if current is None or previous is None or previous <= 0:
            return None
        return round((float(current) / float(previous) - 1.0) * 100.0, 2)

    def query(self, project, range_key, spend_rows, projects, candidates=()):
        """Return a bounded content-free Git projection."""
        windows = self._windows(range_key)
        if windows is None:
            return {"ok": False, "error": "A valid history range is required."}
        current_window, previous_window = windows
        labels, canonical_by_source, repo_by_label = self._project_repo_mapping(
            projects, candidates,
        )
        requested = canonical_by_source.get(project, project) if project else ""
        if requested and requested not in labels:
            return {"ok": False, "error": "Project was not found."}
        selected = [requested] if requested else labels
        selected_set = set(selected)

        selected_repo_labels = {
            repo_key: label for label, repo_key in repo_by_label.items()
            if label in selected_set and repo_key
        }
        starts = [
            window[0] for window in (current_window, previous_window)
            if window is not None and window[0] is not None
        ]
        if range_key != "all":
            starts.append(current_window[0] - datetime.timedelta(days=6))
        query_start = min(starts)
        query_end = current_window[1]
        delivery = {}
        for row in self.ledger.daily_rows(
            selected_repo_labels,
            query_start.isoformat(),
            query_end.isoformat(),
        ):
            label = selected_repo_labels.get(row["repo_key"], "")
            key = (label, row["day"])
            target = delivery.setdefault(key, {"added": 0, "deleted": 0})
            target["added"] += int(row["added"])
            target["deleted"] += int(row["deleted"])

        spending = {}
        for row in spend_rows or ():
            if not isinstance(row, dict):
                continue
            source_label = row.get("project")
            label = canonical_by_source.get(source_label, source_label)
            day = row.get("day")
            if label not in selected_set or not isinstance(day, str) or len(day) != 10:
                continue
            target = spending.setdefault((label, day), {
                "covered_cost": 0.0,
                "rows": 0,
                "covered_rows": 0,
                "efficiency_covered_cost": 0.0,
                "covered_output_tokens": 0,
                "output_covered_rows": 0,
                "output_partial": False,
                "reasoning_tokens": 0,
                "reasoning_output_tokens": 0,
                "reasoning_covered_rows": 0,
                "reasoning_partial": False,
            })
            available = row.get("cost_available") is True
            target["rows"] += 1
            if available:
                target["covered_rows"] += 1
                try:
                    cost = float(row.get("covered_cost") or 0)
                except (TypeError, ValueError):
                    cost = 0.0
                if math.isfinite(cost) and cost >= 0:
                    target["covered_cost"] += cost
            if row.get("output_available") is True:
                target["output_covered_rows"] += 1
                try:
                    efficiency_cost = float(
                        row.get("efficiency_covered_cost") or 0
                    )
                except (TypeError, ValueError):
                    efficiency_cost = 0.0
                if math.isfinite(efficiency_cost) and efficiency_cost >= 0:
                    target["efficiency_covered_cost"] += efficiency_cost
                try:
                    output_tokens = int(row.get("covered_output_tokens") or 0)
                except (TypeError, ValueError):
                    output_tokens = 0
                target["covered_output_tokens"] += max(0, output_tokens)
            target["output_partial"] = (
                target["output_partial"]
                or row.get("output_partial") is True
            )
            if row.get("reasoning_available") is True:
                target["reasoning_covered_rows"] += 1
                try:
                    reasoning_output = int(
                        row.get("reasoning_output_tokens") or 0
                    )
                    reasoning_tokens = int(row.get("reasoning_tokens") or 0)
                except (TypeError, ValueError):
                    reasoning_output = reasoning_tokens = 0
                reasoning_output = max(0, reasoning_output)
                target["reasoning_output_tokens"] += reasoning_output
                target["reasoning_tokens"] += min(
                    reasoning_output, max(0, reasoning_tokens),
                )
            target["reasoning_partial"] = (
                target["reasoning_partial"]
                or row.get("reasoning_partial") is True
            )

        coverage_by_project = {
            label: self.ledger.repository_coverage(repo_by_label.get(label, ""))
            for label in selected
        }
        baseline_at = self.ledger.baseline_at()
        baseline_day = (
            datetime.datetime.fromtimestamp(baseline_at).date()
            if baseline_at is not None else None
        )

        def aggregate(window):
            rows = []
            baseline_partial = bool(
                baseline_day is not None
                and window is not None
                and (window[0] is None or window[0] <= baseline_day)
            )
            for label in selected:
                line_rows = [
                    value for (name, day), value in delivery.items()
                    if name == label and self._in_window(day, window)
                ]
                cost_rows = [
                    value for (name, day), value in spending.items()
                    if name == label and self._in_window(day, window)
                ]
                added = sum(value["added"] for value in line_rows)
                deleted = sum(value["deleted"] for value in line_rows)
                covered_cost = sum(value["covered_cost"] for value in cost_rows)
                code_available = coverage_by_project[label]["measured"]
                cost_available = bool(cost_rows) and any(
                    value["covered_rows"] for value in cost_rows
                )
                cost_partial = any(
                    value["covered_rows"] < value["rows"] for value in cost_rows
                )
                efficiency_cost = sum(
                    value["efficiency_covered_cost"] for value in cost_rows
                )
                covered_output = sum(
                    value["covered_output_tokens"] for value in cost_rows
                )
                output_available = any(
                    value["output_covered_rows"] for value in cost_rows
                )
                output_partial = any(
                    value["output_covered_rows"] < value["rows"]
                    or value["output_partial"]
                    for value in cost_rows
                )
                reasoning_tokens = sum(
                    value["reasoning_tokens"] for value in cost_rows
                )
                reasoning_output = sum(
                    value["reasoning_output_tokens"] for value in cost_rows
                )
                reasoning_available = any(
                    value["reasoning_covered_rows"] for value in cost_rows
                ) and reasoning_output > 0
                reasoning_partial = any(
                    value["reasoning_covered_rows"] < value["rows"]
                    or value["reasoning_partial"]
                    for value in cost_rows
                )
                comparable = code_available and cost_available
                changed_lines = added + deleted
                spend_per_1k = (
                    1000.0 * covered_cost / changed_lines
                    if comparable and changed_lines > 0 else None
                )
                output_per_dollar = (
                    covered_output / efficiency_cost
                    if output_available and efficiency_cost > 0 else None
                )
                delivery_yield = (
                    1000.0 * changed_lines / covered_output
                    if comparable and output_available and covered_output > 0
                    else None
                )
                reasoning_ratio = (
                    reasoning_tokens / reasoning_output
                    if reasoning_available else None
                )
                rows.append({
                    "project": label,
                    "covered_cost": covered_cost,
                    "added": added,
                    "deleted": deleted,
                    "changed_lines": changed_lines,
                    "spend_per_1k": spend_per_1k,
                    "efficiency": {
                        "covered_cost": efficiency_cost,
                        "covered_output_tokens": covered_output,
                        "changed_lines": changed_lines if output_available else 0,
                        "output_per_dollar": output_per_dollar,
                        "delivery_yield": delivery_yield,
                        "reasoning_ratio": reasoning_ratio,
                        "reasoning_tokens": reasoning_tokens,
                        "reasoning_output_tokens": reasoning_output,
                        "availability": {
                            "output": output_available,
                            "output_per_dollar": output_per_dollar is not None,
                            "delivery_yield": delivery_yield is not None,
                            "reasoning_ratio": reasoning_ratio is not None,
                            "partial": output_partial,
                            "reasoning_partial": reasoning_partial,
                        },
                    },
                    "availability": {
                        "cost": cost_available,
                        "code_pushed": code_available,
                        "spend_per_1k": spend_per_1k is not None,
                        "partial": (
                            coverage_by_project[label]["partial"]
                            or cost_partial
                            or baseline_partial
                        ),
                    },
                })
            comparable_rows = [
                row for row in rows
                if row["availability"]["cost"] and row["availability"]["code_pushed"]
            ]
            measured_rows = [
                row for row in rows if row["availability"]["code_pushed"]
            ]
            changed_lines = sum(row["changed_lines"] for row in measured_rows)
            covered_cost = sum(row["covered_cost"] for row in comparable_rows)
            added = sum(row["added"] for row in measured_rows)
            deleted = sum(row["deleted"] for row in measured_rows)
            comparable_changed_lines = sum(
                row["changed_lines"] for row in comparable_rows
            )
            spend_per_1k = (
                1000.0 * covered_cost / comparable_changed_lines
                if comparable_changed_lines > 0 else None
            )
            driver_rows = [
                row for row in comparable_rows
                if row["efficiency"]["availability"]["output"]
            ]
            driver_cost = sum(
                row["efficiency"]["covered_cost"] for row in driver_rows
            )
            driver_output = sum(
                row["efficiency"]["covered_output_tokens"] for row in driver_rows
            )
            driver_lines = sum(row["changed_lines"] for row in driver_rows)
            output_per_dollar = (
                driver_output / driver_cost if driver_cost > 0 else None
            )
            delivery_yield = (
                1000.0 * driver_lines / driver_output
                if driver_output > 0 else None
            )
            reasoning_rows = [
                row for row in driver_rows
                if row["efficiency"]["availability"]["reasoning_ratio"]
            ]
            reasoning_tokens = sum(
                row["efficiency"]["reasoning_tokens"] for row in reasoning_rows
            )
            reasoning_output = sum(
                row["efficiency"]["reasoning_output_tokens"]
                for row in reasoning_rows
            )
            reasoning_ratio = (
                reasoning_tokens / reasoning_output
                if reasoning_output > 0 else None
            )
            return rows, {
                "covered_cost": covered_cost,
                "added": added,
                "deleted": deleted,
                "changed_lines": changed_lines,
                "comparable_changed_lines": comparable_changed_lines,
                "spend_per_1k": spend_per_1k,
                "efficiency": {
                    "covered_cost": driver_cost,
                    "covered_output_tokens": driver_output,
                    "changed_lines": driver_lines,
                    "output_per_dollar": output_per_dollar,
                    "delivery_yield": delivery_yield,
                    "reasoning_ratio": reasoning_ratio,
                    "reasoning_tokens": reasoning_tokens,
                    "reasoning_output_tokens": reasoning_output,
                    "availability": {
                        "output": bool(driver_rows),
                        "output_per_dollar": output_per_dollar is not None,
                        "delivery_yield": delivery_yield is not None,
                        "reasoning_ratio": reasoning_ratio is not None,
                        "partial": (
                            len(driver_rows) < len(comparable_rows)
                            or any(
                                row["efficiency"]["availability"]["partial"]
                                for row in driver_rows
                            )
                        ),
                        "reasoning_partial": (
                            len(reasoning_rows) < len(driver_rows)
                            or any(
                                row["efficiency"]["availability"][
                                    "reasoning_partial"
                                ]
                                for row in reasoning_rows
                            )
                        ),
                    },
                },
                "availability": {
                    "cost": bool(comparable_rows),
                    "code_pushed": bool(measured_rows),
                    "spend_per_1k": spend_per_1k is not None,
                    "partial": (
                        len(measured_rows) < len(selected)
                        or any(row["availability"]["partial"] for row in rows)
                    ),
                },
            }

        current_rows, overall = aggregate(current_window)
        previous_rows, previous = aggregate(previous_window) if previous_window else ([], None)
        previous_by_project = {row["project"]: row for row in previous_rows}
        for row in current_rows:
            prior = previous_by_project.get(row["project"])
            row["comparison"] = {
                "code_pushed_pct": self._percent_change(
                    row["changed_lines"], prior["changed_lines"] if prior else None,
                ),
                "spend_per_1k_pct": self._percent_change(
                    row["spend_per_1k"], prior["spend_per_1k"] if prior else None,
                ),
            }
        current_rows.sort(key=lambda row: (-row["covered_cost"], row["project"]))

        comparison = {
            "code_pushed_pct": self._percent_change(
                overall["changed_lines"], previous["changed_lines"] if previous else None,
            ),
            "spend_per_1k_pct": self._percent_change(
                overall["spend_per_1k"], previous["spend_per_1k"] if previous else None,
            ),
            "output_per_dollar_pct": self._percent_change(
                overall["efficiency"]["output_per_dollar"],
                previous["efficiency"]["output_per_dollar"] if previous else None,
            ),
            "delivery_yield_pct": self._percent_change(
                overall["efficiency"]["delivery_yield"],
                previous["efficiency"]["delivery_yield"] if previous else None,
            ),
            "reasoning_ratio_pp": (
                round(
                    100.0 * (
                        overall["efficiency"]["reasoning_ratio"]
                        - previous["efficiency"]["reasoning_ratio"]
                    ),
                    2,
                )
                if previous
                and overall["efficiency"]["reasoning_ratio"] is not None
                and previous["efficiency"]["reasoning_ratio"] is not None
                else None
            ),
        }
        comparison["available"] = all(
            comparison[key] is not None
            for key in ("code_pushed_pct", "spend_per_1k_pct")
        )

        span = (current_window[1] - current_window[0]).days + 1
        known_days = [
            (current_window[0] + datetime.timedelta(days=index)).isoformat()
            for index in range(min(span, MAX_QUERY_DAYS))
        ]
        comparable_projects = {
            row["project"] for row in current_rows
            if row["availability"]["cost"] and row["availability"]["code_pushed"]
        }
        measured_projects = {
            row["project"] for row in current_rows
            if row["availability"]["code_pushed"]
        }
        days = []
        for day in known_days:
            added = sum(
                delivery.get((label, day), {}).get("added", 0)
                for label in measured_projects
            )
            deleted = sum(
                delivery.get((label, day), {}).get("deleted", 0)
                for label in measured_projects
            )
            covered_cost = sum(
                spending.get((label, day), {}).get("covered_cost", 0.0)
                for label in comparable_projects
            )
            changed_lines = added + deleted
            comparable_changed_lines = sum(
                delivery.get((label, day), {}).get("added", 0)
                + delivery.get((label, day), {}).get("deleted", 0)
                for label in comparable_projects
            )
            output_projects = {
                label for label in comparable_projects
                if spending.get((label, day), {}).get("output_covered_rows", 0)
            }
            efficiency_cost = sum(
                spending.get((label, day), {}).get(
                    "efficiency_covered_cost", 0.0,
                )
                for label in output_projects
            )
            covered_output = sum(
                spending.get((label, day), {}).get("covered_output_tokens", 0)
                for label in output_projects
            )
            driver_changed_lines = sum(
                delivery.get((label, day), {}).get("added", 0)
                + delivery.get((label, day), {}).get("deleted", 0)
                for label in output_projects
            )
            reasoning_projects = {
                label for label in output_projects
                if spending.get((label, day), {}).get(
                    "reasoning_covered_rows", 0,
                )
            }
            reasoning_tokens = sum(
                spending.get((label, day), {}).get("reasoning_tokens", 0)
                for label in reasoning_projects
            )
            reasoning_output = sum(
                spending.get((label, day), {}).get(
                    "reasoning_output_tokens", 0,
                )
                for label in reasoning_projects
            )
            spend_per_1k = (
                1000.0 * covered_cost / comparable_changed_lines
                if comparable_changed_lines > 0 else None
            )
            output_per_dollar = (
                covered_output / efficiency_cost
                if efficiency_cost > 0 else None
            )
            delivery_yield = (
                1000.0 * driver_changed_lines / covered_output
                if covered_output > 0 else None
            )
            reasoning_ratio = (
                reasoning_tokens / reasoning_output
                if reasoning_output > 0 else None
            )
            rolling_start = max(
                query_start,
                datetime.date.fromisoformat(day) - datetime.timedelta(days=6),
            )
            rolling_days = [
                (rolling_start + datetime.timedelta(days=index)).isoformat()
                for index in range(
                    (datetime.date.fromisoformat(day) - rolling_start).days + 1
                )
            ]
            rolling_cost = sum(
                spending.get((label, rolling_day), {}).get("covered_cost", 0.0)
                for label in comparable_projects
                for rolling_day in rolling_days
            )
            rolling_lines = sum(
                delivery.get((label, rolling_day), {}).get("added", 0)
                + delivery.get((label, rolling_day), {}).get("deleted", 0)
                for label in comparable_projects
                for rolling_day in rolling_days
            )
            rolling_spend_per_1k = (
                1000.0 * rolling_cost / rolling_lines
                if len(rolling_days) == 7 and rolling_lines > 0 else None
            )
            days.append({
                "day": day,
                "covered_cost": covered_cost,
                "added": added,
                "deleted": deleted,
                "changed_lines": changed_lines,
                "comparable_changed_lines": comparable_changed_lines,
                "spend_per_1k": spend_per_1k,
                "rolling_spend_per_1k": rolling_spend_per_1k,
                "efficiency": {
                    "output_per_dollar": output_per_dollar,
                    "delivery_yield": delivery_yield,
                    "reasoning_ratio": reasoning_ratio,
                    "availability": {
                        "output_per_dollar": output_per_dollar is not None,
                        "delivery_yield": delivery_yield is not None,
                        "reasoning_ratio": reasoning_ratio is not None,
                    },
                },
                "availability": {
                    "code_pushed": bool(measured_projects),
                    "cost": bool(comparable_projects),
                    "spend_per_1k": spend_per_1k is not None,
                    "rolling_spend_per_1k": rolling_spend_per_1k is not None,
                },
            })

        coverage = dict(self._last_coverage)
        available_spend = sum(
            row["covered_cost"] for row in current_rows
            if row["availability"]["cost"]
        )
        covered_spend = overall["covered_cost"]
        coverage.update({
            "selected_repositories": len(selected),
            "measured_repositories": sum(
                int(coverage_by_project[label]["measured"]) for label in selected
            ),
            "comparable_repositories": len(comparable_projects),
            "available_spend": available_spend,
            "covered_spend": covered_spend,
            "spend_coverage": (
                covered_spend / available_spend if available_spend > 0 else None
            ),
        })
        return {
            "ok": True,
            "generated_at": int(self._now()),
            "range": range_key,
            "projects": labels,
            "days": days,
            "overall": overall,
            "previous": previous,
            "comparison": comparison,
            "project_rows": current_rows,
            "coverage": coverage,
        }
