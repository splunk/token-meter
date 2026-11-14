"""Branch names from agent traces and the per-branch spend and delivery rollup.

Branch names come only from agent traces. They stay on the local dashboard and are
joined to the Git ledger by salted key, so the ledger never needs a ref name.
"""

import collections
import datetime
import math

MAX_BRANCH_CHARS = 200
# Shared mainlines; the repository's own default branch is treated the same way.
TRUNK_BRANCHES = frozenset(("main", "master", "develop", "dev", "trunk"))


def normalize_branch(value):
    """Return a usable trace branch name, or "" for detached, empty, or unsafe values."""
    if not isinstance(value, str):
        return ""
    name = value.strip()
    if not name or name == "HEAD":
        return ""
    if any(ord(character) < 32 or ord(character) == 127 for character in name):
        return ""
    return name[:MAX_BRANCH_CHARS]


def dominant_branch(values):
    """Most common usable branch; a tie goes to the branch seen latest."""
    counts = collections.Counter()
    last_seen = {}
    for index, value in enumerate(values):
        name = normalize_branch(value)
        if name:
            counts[name] += 1
            last_seen[name] = index
    if not counts:
        return ""
    return max(counts, key=lambda name: (counts[name], last_seen[name]))


MAX_BRANCH_ROWS = 500
STALE_SECONDS = 7 * 86_400
HARD_MULTIPLIER = 3.0
HARD_MIN_BRANCHES = 5
HARD_MIN_SPEND = 1.0


def _median(values):
    ordered = sorted(values)
    if not ordered:
        return None
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2.0


def _in(day, window):
    return window is not None and window[0].isoformat() <= day <= window[1].isoformat()


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number > 0 else 0.0


def _window_end(window, now):
    end = window[1] + datetime.timedelta(days=1)
    stamp = datetime.datetime.combine(end, datetime.time()).timestamp() - 1
    return min(float(now), stamp)


def _period_cost(row, window):
    return sum(_number(cost) for day, cost in row["day_cost"].items() if _in(day, window))


def _change_pct(current, previous):
    if current is None or previous is None or previous <= 0:
        return None
    return round((current / previous - 1.0) * 100.0, 2)


def _change_pp(current, previous):
    if current is None or previous is None:
        return None
    return round(100.0 * (current - previous), 2)


def _sessions(rows):
    result = []
    for row in rows or ():
        if not isinstance(row, dict):
            continue
        day_cost = row.get("day_cost") if isinstance(row.get("day_cost"), dict) else {}
        result.append({
            "branch": normalize_branch(row.get("branch")),
            "project": str(row.get("project") or "")[:120],
            "repo_key": str(row.get("repo_key") or ""),
            "provider": str(row.get("provider") or "")[:60],
            "day_cost": {str(day): cost for day, cost in day_cost.items() if isinstance(day, str) and len(day) == 10},
            "cost_available": row.get("cost_available") is not False,
            "child": bool(row.get("child")),
            "started_at": _number(row.get("started_at")) or None,
            "last_activity_at": _number(row.get("last_activity_at")) or None,
        })
    return result


def _metrics(branches, sessions, window, now):
    """The four branch delivery metrics for one period; unavailable evidence is None, never 0."""
    as_of = _window_end(window, now)
    period_spend = sum(_period_cost(row, window) for row in sessions if row["cost_available"])
    shipped = [branch for branch in branches if branch["evidence"] and branch["measured"]]

    first_pushed = [
        branch for branch in shipped
        if _in(datetime.date.fromtimestamp(branch["evidence"]["first_push"]).isoformat(), window)
        and branch["cost_available"]
    ]
    cost_to_ship = _median([branch["total_spend"] for branch in first_pushed])
    lines = _median([branch["evidence"]["added"] + branch["evidence"]["deleted"] for branch in first_pushed])

    stale_spend = live_spend = 0.0
    measured_any = False
    for branch in branches:
        if not branch["measured"]:
            continue
        measured_any = True
        evidence = branch["evidence"]
        if evidence and evidence["first_push"] <= as_of:
            continue
        spend = sum(_period_cost(row, window) for row in branch["rows"] if row["cost_available"])
        last = max((min(row["last_activity_at"] or 0.0, as_of) for row in branch["rows"]
                    if (row["started_at"] or 0.0) <= as_of), default=0.0)
        if last and as_of - last >= STALE_SECONDS:
            stale_spend += spend
        else:
            live_spend += spend
    share_ready = measured_any and period_spend > 0

    rework = pushed_spend = 0.0
    for branch in shipped:
        first = branch["evidence"]["first_push"]
        if first > as_of:
            continue
        for row in branch["rows"]:
            if not row["cost_available"]:
                continue
            spend = _period_cost(row, window)
            pushed_spend += spend
            if (row["started_at"] or 0.0) > first:
                rework += spend

    eligible = [
        branch for branch in shipped
        if branch["evidence"]["first_push"] <= as_of and branch["evidence"]["commits"] > 0
        and branch["evidence"]["complete"] and branch["cost_available"]
        and branch["total_spend"] >= HARD_MIN_SPEND
        and any(_period_cost(row, window) > 0 for row in branch["rows"])
    ]
    hard_ready = len(eligible) >= HARD_MIN_BRANCHES
    typical = _median([branch["total_spend"] / branch["evidence"]["commits"] for branch in eligible])
    hard = [
        branch for branch in eligible
        if hard_ready and branch["total_spend"] / branch["evidence"]["commits"] >= HARD_MULTIPLIER * typical
    ]
    eligible_spend = sum(branch["total_spend"] for branch in eligible)
    return {
        "period_spend": period_spend,
        "cost_to_ship": {
            "value": cost_to_ship, "available": cost_to_ship is not None, "branches": len(first_pushed),
            "median_pushed_lines": lines,
        },
        "unshipped_share": {
            "value": stale_spend / period_spend if share_ready else None, "available": share_ready,
            "spend": stale_spend if measured_any else None,
            "in_progress_spend": live_spend if measured_any else None,
            "in_progress_share": live_spend / period_spend if share_ready else None,
            "stale_days": STALE_SECONDS // 86_400,
        },
        "rework_share": {
            "value": rework / pushed_spend if pushed_spend > 0 else None, "available": pushed_spend > 0,
            "estimate": "session_level", "spend": rework if pushed_spend > 0 else None,
            "pushed_spend": pushed_spend if pushed_spend > 0 else None,
        },
        "hard_branches": {
            "available": hard_ready, "count": len(hard) if hard_ready else None,
            "spend_share": (sum(branch["total_spend"] for branch in hard) / eligible_spend
                            if hard_ready and eligible_spend > 0 else None),
            "eligible": len(eligible), "median_spend_per_commit": typical if hard_ready else None,
            "multiplier": HARD_MULTIPLIER,
        },
        "_hard": {id(branch) for branch in hard},
    }


def _compare(current, previous):
    prior = previous or {}
    for name, change in (("cost_to_ship", _change_pct), ("unshipped_share", _change_pp),
                         ("rework_share", _change_pp)):
        before = (prior.get(name) or {}).get("value")
        current[name]["comparison"] = {
            ("change_pct" if change is _change_pct else "change_pp"): change(current[name]["value"], before),
        }
    before = prior.get("hard_branches") or {}
    current["hard_branches"]["comparison"] = {
        "change_pp": _change_pp(current["hard_branches"]["spend_share"], before.get("spend_share")),
    }


def build_branch_rollup(session_rows, evidence, defaults, key_for, measured_repos,
                        window, previous_window, now, max_rows=MAX_BRANCH_ROWS):
    """Join trace branches to salted ledger branch evidence and score the selected period.

    ``key_for(repo_key, name)`` computes the ledger's salted branch key, so names are never
    looked up by value in the ledger. Trunk branches and sessions without a branch are reported
    only as aggregates.
    """
    sessions = _sessions(session_rows)
    measured_repos = set(measured_repos or ())
    trunk = {"spend": 0.0, "sessions": 0}
    no_branch = {"spend": 0.0, "sessions": 0}
    grouped = {}
    for row in sessions:
        spend = _period_cost(row, window) if row["cost_available"] else 0.0
        active = spend > 0 or any(_in(day, window) for day in row["day_cost"])
        name, repo_key = row["branch"], row["repo_key"]
        key = key_for(repo_key, name) if name and repo_key else ""
        if not name or name in TRUNK_BRANCHES or (key and defaults.get(repo_key) == key):
            target = no_branch if not name else trunk
            if active:
                target["spend"] += spend
                target["sessions"] += 1
            continue
        branch = grouped.setdefault((row["project"], name), {
            "name": name, "project": row["project"], "repo_key": repo_key,
            "measured": bool(repo_key) and repo_key in measured_repos,
            "evidence": (evidence or {}).get((repo_key, key)) if key else None,
            "rows": [], "total_spend": 0.0, "cost_available": False,
        })
        branch["rows"].append(row)
        if row["cost_available"]:
            branch["cost_available"] = True
            branch["total_spend"] += sum(_number(cost) for cost in row["day_cost"].values())
    branches = list(grouped.values())
    for branch in branches:
        if not branch["measured"]:
            branch["evidence"] = None

    current = _metrics(branches, sessions, window, now)
    previous = _metrics(branches, sessions, previous_window, now) if previous_window else None
    hard_ids = current.pop("_hard")
    if previous:
        previous.pop("_hard")
    _compare(current, previous)
    period_spend = current.pop("period_spend")
    if previous:
        previous.pop("period_spend")

    # The range selects which branches appear (any session day in range). Each row then describes
    # the whole branch: Spend is its total across all of its sessions, so Spend / commit, cost to
    # ship, and the dashboard's session list all share one base. Period shares stay day-sliced.
    rows = []
    for branch in branches:
        if not any(_in(day, window) for row in branch["rows"] for day in row["day_cost"]):
            continue
        all_rows = branch["rows"]
        evidence, measured = branch["evidence"], branch["measured"]
        last = max((row["last_activity_at"] or 0.0 for row in branch["rows"]), default=0.0) or None
        if not measured:
            status = "no_git_evidence"
        elif evidence:
            status = "pushed"
        elif last and float(now) - last < STALE_SECONDS:
            status = "in_progress"
        else:
            status = "not_pushed"
        spend = branch["total_spend"] if branch["cost_available"] else None
        commits = evidence["commits"] if evidence else (0 if measured else None)
        per_commit = (branch["total_spend"] / commits
                      if evidence and evidence["complete"] and commits and branch["cost_available"] else None)
        rows.append({
            "branch": branch["name"],
            "project": branch["project"],
            "spend": spend,
            "total_spend": branch["total_spend"] if branch["cost_available"] else None,
            "sessions": sum(1 for row in all_rows if not row["child"]),
            "apps": sorted({row["provider"] for row in all_rows if row["provider"]}),
            "days_active": len({day for row in all_rows for day in row["day_cost"]}),
            "last_activity": last,
            "pushes": evidence["pushes"] if evidence else (0 if measured else None),
            "commits": commits,
            "added": evidence["added"] if evidence else (0 if measured else None),
            "deleted": evidence["deleted"] if evidence else (0 if measured else None),
            "first_push": evidence["first_push"] if evidence else None,
            "last_push": evidence["last_push"] if evidence else None,
            "spend_per_commit": per_commit,
            "status": status,
            "hard": id(branch) in hard_ids,
            "availability": {
                "cost": branch["cost_available"],
                "git": measured,
                "commits": bool(evidence and evidence["complete"]),
                "spend_per_commit": per_commit is not None,
            },
        })
    rows.sort(key=lambda row: (-(row["spend"] or 0.0), row["branch"], row["project"]))
    share = (lambda value: value / period_spend if period_spend > 0 else None)
    return {
        "available": bool(rows) or trunk["sessions"] > 0,
        "rows": rows[:max_rows],
        "total_rows": len(rows),
        "truncated": len(rows) > max_rows,
        "period_spend": period_spend,
        "metrics": current,
        "previous": previous,
        "trunk": dict(trunk, share=share(trunk["spend"])),
        "no_branch": dict(no_branch, share=share(no_branch["spend"])),
    }
