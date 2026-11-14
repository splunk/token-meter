"""Bounded, resumable Git commit annotation scheduling; no text or disk state."""


def resume_backfill(service, root, repo_key, pending, wanted):
    """Deduplicated push ranges resume after the previous scan's fair-sized slice."""
    pushes = list(pending.values())
    walked = 0
    complete = True
    if wanted and pushes:
        start = service._backfill_cursor.get(repo_key, 0) % len(pushes)
        while (walked < len(pushes) and walked < service._repo_backfill_limit
               and service._backfill_walks_left > 0 and service._subjects_left > 0 and wanted):
            entries, index = pushes[(start + walked) % len(pushes)]
            complete = service._backfill_subjects(root, repo_key, entries, index, wanted) and complete
            walked += 1
        service._backfill_cursor[repo_key] = (start + walked) % len(pushes)
    return walked == len(pushes) and complete


def walk_backfill(service, root, repo_key, entries, index, wanted):
    """Retain a numeric offset when one push exceeds the walk or subject budget."""
    if service._backfill_walks_left <= 0 or service._subjects_left <= 0:
        return False
    service._backfill_walks_left -= 1
    entry = entries[index]
    old_oid = entries[index + 1]["oid"] if index + 1 < len(entries) else ""
    key = (repo_key, entry["oid"], old_oid)
    offset = service._backfill_offsets.get(key, 0)
    commits, limited = service._introduced_commits(root, entry["oid"], old_oid, skip=offset)
    if not commits and limited:  # transient Git failure: retry the same page
        return False
    for at, oid in enumerate(commits):
        if not wanted or service._subjects_left <= 0:
            service._backfill_offsets[key] = offset + at
            return False
        object_key = service._hash(oid)
        if object_key in wanted:
            service._send_subject(root, repo_key, object_key, oid, entry["observed_at"], wanted)
    if limited:
        service._backfill_offsets[key] = offset + len(commits)
    else:
        service._backfill_offsets.pop(key, None)
    # Only a whole range read in this pass proves an unmatched key is unavailable.
    return not limited and offset == 0


def queue_local_commits(service, root, repo_key, wanted, limit):
    """Recover recorded owned commits by matching salted keys to bounded local object IDs.

    Ref names and messages are not read here. Only a ledger match may enter the
    existing subject reader; unrelated local commits never expose their text.
    """
    if not wanted or service._subjects_left <= 0:
        return False
    key = (repo_key, "local-objects", "")
    offset = service._backfill_offsets.get(key, 0)
    code, output = service._run_git(root, (
        "rev-list", "--all", "--reflog", f"--max-count={limit + 1}", f"--skip={offset}",
    ))
    if code != 0:
        service._walk_incomplete = True
        return False
    objects = [oid for oid in output.splitlines()
               if len(oid) in (40, 64) and all(c in "0123456789abcdef" for c in oid)]
    for at, oid in enumerate(objects[:limit]):
        if not wanted or service._subjects_left <= 0:
            service._backfill_offsets[key] = offset + at
            return False
        object_key = service._hash(oid)
        if object_key in wanted:
            service._send_subject(root, repo_key, object_key, oid, 0, wanted)
    service._backfill_offsets[key] = offset + limit if len(objects) > limit else 0
    return len(objects) <= limit and offset == 0
