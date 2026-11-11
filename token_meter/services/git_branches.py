"""Per-branch push evidence for the Git ledger under salted keys; never a ref or branch name.

A push to a remote-tracking ref other than a trunk records the salted branch key, the push
time, and that branch's own commits: non-merge, authored by the repository's Git identity, and
not reachable from the remote's trunk refs as they stood at push time. A commit counts once
per branch key. This evidence is kept apart from the per-repository totals, which it never
changes. Branch and ref names live only in memory during a scan.
"""

import os

from token_meter.domain.branches import TRUNK_BRANCHES, build_branch_rollup

MAX_BRANCH_PUSHES_PER_SCAN = 500
MAX_BRANCH_COMMIT_READS_PER_SCAN = 2_000
MAX_BRANCH_COMMITS_PER_PUSH = 2_000
MAX_DONE_PUSHES = 100_000
MAX_QUERY_REPOSITORIES = 50

TABLE_COLUMNS = {
    "delivery_branch_pushes": ("repo_key", "branch_key", "push_key", "pushed_at", "complete"),
    "delivery_branch_commits": ("repo_key", "branch_key", "object_key", "pushed_at", "added", "deleted"),
    "delivery_branch_defaults": ("repo_key", "branch_key"),
}


def create_tables(connection):
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS delivery_branch_pushes (
            repo_key TEXT NOT NULL,
            branch_key TEXT NOT NULL,
            push_key TEXT NOT NULL,
            pushed_at INTEGER NOT NULL,
            complete INTEGER NOT NULL,
            PRIMARY KEY (repo_key, branch_key, push_key)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS delivery_branch_commits (
            repo_key TEXT NOT NULL,
            branch_key TEXT NOT NULL,
            object_key TEXT NOT NULL,
            pushed_at INTEGER NOT NULL,
            added INTEGER NOT NULL,
            deleted INTEGER NOT NULL,
            PRIMARY KEY (repo_key, branch_key, object_key)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS delivery_branch_defaults (
            repo_key TEXT PRIMARY KEY,
            branch_key TEXT NOT NULL
        )
        """
    )


def clear(connection):
    for table in TABLE_COLUMNS:
        connection.execute('DELETE FROM "{}"'.format(table))


def coalesce(connection, legacy_key, canonical_key):
    """Move hashed branch evidence from a legacy repository key to its canonical key."""
    for table, columns in TABLE_COLUMNS.items():
        rest = ", ".join(columns[1:])
        connection.execute(
            'INSERT OR IGNORE INTO "{0}" (repo_key, {1}) SELECT ?, {1} FROM "{0}" WHERE repo_key = ?'.format(
                table, rest),
            (canonical_key, legacy_key),
        )
        connection.execute('DELETE FROM "{}" WHERE repo_key = ?'.format(table), (legacy_key,))


def branch_key(service, repo_key, name):
    """Salted per-repository key for a branch name; the same salt as every other ledger key."""
    return service._hash("branch\0{}\0{}".format(repo_key, name))


def has_push(ledger, repo_key, key, push_key):
    with ledger._transaction() as connection:
        row = connection.execute(
            "SELECT 1 FROM delivery_branch_pushes WHERE repo_key = ? AND branch_key = ? AND push_key = ?",
            (repo_key, key, push_key),
        ).fetchone()
    return row is not None


def record_push(ledger, repo_key, key, push_key, pushed_at, complete, commits):
    """Atomically store one branch push and its own commits as (object_key, added, deleted)."""
    pushed_at = ledger._timestamp(pushed_at)
    rows = [
        (repo_key, key, object_key, pushed_at, ledger._line_count(added), ledger._line_count(deleted))
        for object_key, added, deleted in commits
    ]
    with ledger._transaction() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO delivery_branch_pushes VALUES (?, ?, ?, ?, ?)",
            (repo_key, key, push_key, pushed_at, int(bool(complete))),
        )
        connection.executemany("INSERT OR IGNORE INTO delivery_branch_commits VALUES (?, ?, ?, ?, ?, ?)", rows)


def set_default(ledger, repo_key, key):
    with ledger._transaction() as connection:
        connection.execute("INSERT OR REPLACE INTO delivery_branch_defaults VALUES (?, ?)", (repo_key, key))


def _bounded_keys(repo_keys):
    return tuple(dict.fromkeys(
        value for value in repo_keys or () if isinstance(value, str) and value
    ))[:MAX_QUERY_REPOSITORIES]


def branch_evidence(ledger, repo_keys):
    """Return {(repo_key, branch_key): pushes, first/last push, commits, lines, complete}."""
    keys = _bounded_keys(repo_keys)
    if not keys:
        return {}
    marks = ", ".join("?" for _key in keys)
    with ledger._transaction() as connection:
        pushes = connection.execute(
            "SELECT repo_key, branch_key, COUNT(*) AS pushes, MIN(pushed_at) AS first_push, "
            "MAX(pushed_at) AS last_push, MIN(complete) AS complete FROM delivery_branch_pushes "
            "WHERE repo_key IN ({}) GROUP BY repo_key, branch_key".format(marks), keys,
        ).fetchall()
        commits = {
            (row["repo_key"], row["branch_key"]): row for row in connection.execute(
                "SELECT repo_key, branch_key, COUNT(*) AS commits, SUM(added) AS added, "
                "SUM(deleted) AS deleted FROM delivery_branch_commits WHERE repo_key IN ({}) "
                "GROUP BY repo_key, branch_key".format(marks), keys,
            ).fetchall()
        }
    result = {}
    for row in pushes:
        key = (row["repo_key"], row["branch_key"])
        counted = commits.get(key)
        result[key] = {
            "pushes": int(row["pushes"]),
            "first_push": int(row["first_push"]),
            "last_push": int(row["last_push"]),
            "commits": int(counted["commits"]) if counted else 0,
            "added": int(counted["added"] or 0) if counted else 0,
            "deleted": int(counted["deleted"] or 0) if counted else 0,
            "complete": bool(row["complete"]),
        }
    return result


def default_branch_keys(ledger, repo_keys):
    keys = _bounded_keys(repo_keys)
    if not keys:
        return {}
    with ledger._transaction() as connection:
        rows = connection.execute(
            "SELECT repo_key, branch_key FROM delivery_branch_defaults WHERE repo_key IN ({})".format(
                ", ".join("?" for _key in keys)), keys,
        ).fetchall()
    return {row["repo_key"]: row["branch_key"] for row in rows}


def _budget(service):
    """Per-scan-pass limits, reset whenever the service starts a new pass."""
    budget = getattr(service, "_branch_budget", None)
    if not budget or budget["pass"] != service._scan_pass:
        budget = {"pass": service._scan_pass, "pushes": MAX_BRANCH_PUSHES_PER_SCAN,
                  "reads": MAX_BRANCH_COMMIT_READS_PER_SCAN}
        service._branch_budget = budget
    return budget


def _oids(output):
    lines = (line.strip() for line in str(output or "").splitlines())
    return [line.lower() for line in lines
            if len(line) in (40, 64) and all(character in "0123456789abcdefABCDEF" for character in line)]


def _split_ref(ref):
    """(remote, branch) of a remote-tracking ref; the remote is its first path segment."""
    remote, _sep, name = str(ref or "")[len("refs/remotes/"):].partition("/")
    if not str(ref or "").startswith("refs/remotes/") or not remote or not name:
        return "", ""
    return remote, name


def _remote_default(canonical, remote):
    """The remote's default branch from its local HEAD symref file, read without running Git."""
    if not remote or ".." in remote or "\\" in remote:
        return ""
    path = os.path.join(canonical, ".git", "refs", "remotes", *remote.split("/"), "HEAD")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            value = handle.read(1024).strip()
    except OSError:
        return ""
    prefix = "ref: refs/remotes/{}/".format(remote)
    return value[len(prefix):] if value.startswith(prefix) and len(value) > len(prefix) else ""


def _remote_ref_exists(canonical, ref):
    """Whether a remote-tracking ref exists, from its loose file or packed-refs, without running Git."""
    git_dir = os.path.join(canonical, ".git")
    if os.path.isfile(os.path.join(git_dir, *ref.split("/"))):
        return True
    try:
        with open(os.path.join(git_dir, "packed-refs"), "r", encoding="utf-8", errors="replace") as handle:
            return any(line.rstrip("\n").split(" ", 1)[-1] == ref for line in handle if not line.startswith(("#", "^")))
    except OSError:
        return False


class BranchScan:
    """Collects one repository's branch pushes during a scan; names stay in this object's memory."""

    def __init__(self, service, canonical, repo_key, identity, refs):
        self.service, self.repo_key, self.identity = service, repo_key, identity
        self.pushes = []
        self.histories = {}
        self.defaults = {}
        for remote in dict.fromkeys(_split_ref(ref)[0] for ref in refs):
            if remote:
                self.defaults[remote] = _remote_default(canonical, remote)
        self.trunk_refs = {ref for ref in refs if self._is_trunk(ref)}
        # Trunk refs that exist but were not selected this pass (more than the ref budget): a push
        # on that remote waits for a pass that reads them instead of being stored incomplete.
        self.unread_trunks = set()
        for remote, default in self.defaults.items():
            for name in dict.fromkeys(sorted(TRUNK_BRANCHES) + ([default] if default else [])):
                ref = "refs/remotes/{}/{}".format(remote, name)
                if ref not in self.trunk_refs and ".." not in name and _remote_ref_exists(canonical, ref):
                    self.unread_trunks.add(ref)

    def _is_trunk(self, ref):
        remote, name = _split_ref(ref)
        return bool(remote) and (name in TRUNK_BRANCHES or name == self.defaults.get(remote))

    def observe_ref(self, ref, entries):
        """Keep a trunk ref's reflog so each push can exclude the trunk as it stood then."""
        if ref in self.trunk_refs:
            self.histories[ref] = entries

    def push(self, ref, entry, old_oid):
        self.pushes.append((ref, entry, old_oid))

    def record(self, root):
        record_pushes(self, root)


def _base_at(history, pushed_at):
    """The trunk tip as of a push: its newest reflog entry at or before then, else "" (unknown).

    A later tip may already contain the branch through a merge, which would count zero commits.
    """
    for entry in history:
        if entry["observed_at"] <= pushed_at:
            return entry["oid"]
    return ""


def _commit_evidence(service, root, repo_key, oid, identity, budget):
    """(state, added, deleted): counted lines reuse the ledger; others read Git once."""
    object_key = service._hash(oid)
    with service.ledger._transaction() as connection:
        row = connection.execute(
            "SELECT added, deleted FROM delivery_observations WHERE repo_key = ? AND object_key = ?",
            (repo_key, object_key),
        ).fetchone()
        seen = row is not None or connection.execute(
            "SELECT 1 FROM delivery_seen WHERE repo_key = ? AND object_key = ?", (repo_key, object_key),
        ).fetchone() is not None
    if row is not None:
        return "own", int(row["added"]), int(row["deleted"])
    if seen:
        return "excluded", 0, 0
    if budget["reads"] <= 0:
        return "later", 0, 0
    budget["reads"] -= 1
    code, output = service._run_git(root, ("show", "--numstat", "--format=%ae%x00%P%x00", oid))
    if code != 0:
        return "later", 0, 0
    email, _sep, rest = output.partition("\0")
    parents, _sep, numstat = rest.partition("\0")
    if service._identity(email) != identity or len(parents.split()) != 1:
        return "excluded", 0, 0
    added, deleted = service._numstat(numstat)
    return "own", added, deleted


def record_pushes(scan, root):
    """Record branch evidence for the pushes one repository scan collected."""
    service, repo_key = scan.service, scan.repo_key
    done = getattr(service, "_branch_done", None)
    if done is None or len(done) > MAX_DONE_PUSHES:
        done = service._branch_done = set()
    budget = _budget(service)
    for remote, default in scan.defaults.items():
        if default:
            set_default(service.ledger, repo_key, branch_key(service, repo_key, default))
    for ref, entry, old_oid in scan.pushes:
        marker = (repo_key, ref, entry["oid"], old_oid)
        if marker in done:
            continue
        remote, name = _split_ref(ref)
        if not remote or scan._is_trunk(ref):
            done.add(marker)
            continue
        trunks = [trunk for trunk in scan.trunk_refs if _split_ref(trunk)[0] == remote]
        if any(trunk not in scan.histories for trunk in trunks) or any(
                _split_ref(trunk)[0] == remote for trunk in scan.unread_trunks):
            continue  # a trunk reflog was not read this pass; retry rather than overcount
        if budget["pushes"] <= 0:
            return
        key = branch_key(service, repo_key, name)
        push_key = service._hash("push\0{}\0{}".format(entry["oid"], old_oid))
        if has_push(service.ledger, repo_key, key, push_key):
            done.add(marker)
            continue
        budget["pushes"] -= 1
        bases = [_base_at(scan.histories[trunk], entry["observed_at"]) for trunk in sorted(trunks)]
        excludes = [oid for oid in dict.fromkeys(([old_oid] if old_oid else []) + bases) if oid]
        if not excludes or not all(bases):
            # The trunk as of this push is unknown, or there is neither a trunk nor a previous tip:
            # the branch's own commits cannot be told apart, so the push is stored as incomplete.
            record_push(service.ledger, repo_key, key, push_key, entry["observed_at"], False, ())
            done.add(marker)
            continue
        code, output = service._run_git(root, (
            "rev-list", "--no-merges", "--max-count={}".format(MAX_BRANCH_COMMITS_PER_PUSH + 1),
            *("^{}".format(oid) for oid in excludes), entry["oid"],
        ))
        if code != 0:
            continue
        oids = _oids(output)
        commits = []
        for oid in oids[:MAX_BRANCH_COMMITS_PER_PUSH]:
            state, added, deleted = _commit_evidence(service, root, repo_key, oid, scan.identity, budget)
            if state == "later":
                return  # retried next pass; nothing for this push is stored yet
            if state == "own":
                commits.append((service._hash(oid), added, deleted))
        complete = len(oids) <= MAX_BRANCH_COMMITS_PER_PUSH
        record_push(service.ledger, repo_key, key, push_key, entry["observed_at"], complete, commits)
        done.add(marker)


def branch_payload(service, sessions, candidates, range_key, project=""):
    """Dashboard-only per-branch rollup for one /git-delivery range; None for an invalid range.

    Session rows carry trace branch names and project labels. Each is matched to the ledger by
    computing its salted key, and no repository or branch key leaves this function.
    """
    windows = service._windows(range_key)
    if windows is None:
        return None
    rows = [row for row in sessions or () if isinstance(row, dict)]
    labels = sorted({candidate["project"] for candidate in candidates or () if isinstance(candidate, dict)
                     and isinstance(candidate.get("project"), str)})
    _labels, canonical_by_source, repo_by_label = service._project_repo_mapping(labels, candidates)
    requested = canonical_by_source.get(project, project) if project else ""
    joined = []
    for row in rows:
        label = canonical_by_source.get(row.get("project"), row.get("project"))
        if requested and label != requested:
            continue
        joined.append(dict(row, project=label, repo_key=repo_by_label.get(label, "")))
    repo_keys = sorted({row["repo_key"] for row in joined if row["repo_key"]})
    measured = {key for key in repo_keys if service.ledger.repository_coverage(key)["measured"]}
    return build_branch_rollup(
        joined, branch_evidence(service.ledger, repo_keys), default_branch_keys(service.ledger, repo_keys),
        lambda repo_key, name: branch_key(service, repo_key, name), measured,
        windows[0], windows[1], service._now(),
    )


def repository_labels(service, source_labels, candidates):
    """Map each session project label to the canonical repository label the Git page uses.

    Worktrees of one repository share a repository key, so they map to one label; no key leaves.
    """
    labels = sorted({candidate["project"] for candidate in candidates or () if isinstance(candidate, dict)
                     and isinstance(candidate.get("project"), str)})
    _labels, canonical_by_source, _repo_by_label = service._project_repo_mapping(labels, candidates)
    return {label: canonical_by_source.get(label, label) for label in source_labels or () if label}
