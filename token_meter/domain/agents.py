"""Privacy-safe relationships and rollups for provider-corrected agent work.

Runtime adapters own identity, attribution, deduplication, and pricing.  This
module only joins explicitly supplied opaque relationships and projects a
small, content-free schema for the browser dashboard.
"""

import math
import statistics
import time
from collections import defaultdict


AGENT_KINDS = frozenset({"root", "spawned", "internal"})
ACTIVITY_STATES = frozenset({
    "working", "waiting", "recent", "incomplete", "complete", "unknown",
})
MAX_AGENT_USAGE_INVENTORY = 1000
MAX_AGENT_ROLE_DAYS = 4000
PUBLIC_AGENT_FIELDS = (
    "id", "parent_id", "session_id", "runtime", "client", "kind", "depth",
    "label", "role", "model", "activity_state", "started_at", "ended_at",
    "last_activity_at", "input_tokens", "output_tokens", "cache_read_tokens",
    "cache_write_tokens", "reasoning_tokens", "tokens", "cost", "executions",
    "attempts", "retries", "failed_attempts", "tool_calls", "work_time_s",
)


def _bounded_text(value, limit):
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    return text[:limit]


def _opaque_id(value):
    return _bounded_text(value, 240)


def _project_key(value):
    return str(value or "").strip()[:1000]


def _nonnegative_number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _nonnegative_int(value):
    number = _nonnegative_number(value)
    return int(number) if number is not None else 0


def _optional_timestamp(value):
    return _nonnegative_number(value)


def _normalize_record(raw, owner_session_id, owner_project):
    if not isinstance(raw, dict):
        return None
    agent_id = _opaque_id(raw.get("id"))
    kind = str(raw.get("kind") or "")
    runtime = _bounded_text(raw.get("runtime"), 40)
    if not agent_id or kind not in AGENT_KINDS or not runtime:
        return None

    parent_id = _opaque_id(raw.get("parent_id")) or None
    session_id = _opaque_id(raw.get("session_id")) or None
    cost_number = _nonnegative_number(raw.get("cost"))
    token_number = _nonnegative_number(raw.get("tokens"))
    cost_available = raw.get("cost_available") is True and cost_number is not None
    tokens_available = (
        raw.get("tokens_available") is True and token_number is not None
    )
    activity_state = str(raw.get("activity_state") or "unknown")
    if activity_state not in ACTIVITY_STATES:
        activity_state = "unknown"
    reported_depth = _nonnegative_number(raw.get("depth"))
    work_time_s = _nonnegative_number(raw.get("work_time_s"))
    normalized = {
        "id": agent_id,
        "parent_id": parent_id,
        "session_id": session_id,
        "runtime": runtime,
        "client": _bounded_text(raw.get("client"), 40) or runtime,
        "kind": kind,
        "depth": int(reported_depth) if reported_depth is not None else None,
        "label": _bounded_text(raw.get("label"), 80),
        "role": _bounded_text(raw.get("role"), 64) or None,
        "model": _bounded_text(raw.get("model"), 120) or "unknown",
        "activity_state": activity_state,
        "started_at": _optional_timestamp(raw.get("started_at")),
        "ended_at": _optional_timestamp(raw.get("ended_at")),
        "last_activity_at": _optional_timestamp(raw.get("last_activity_at")),
        "input_tokens": _nonnegative_int(raw.get("input_tokens")),
        "output_tokens": _nonnegative_int(raw.get("output_tokens")),
        "cache_read_tokens": _nonnegative_int(raw.get("cache_read_tokens")),
        "cache_write_tokens": _nonnegative_int(raw.get("cache_write_tokens")),
        "reasoning_tokens": _nonnegative_int(raw.get("reasoning_tokens")),
        "tokens": int(token_number) if token_number is not None else None,
        "tokens_available": tokens_available,
        "cost": cost_number,
        "cost_available": cost_available,
        "executions": _nonnegative_int(raw.get("executions")),
        "attempts": _nonnegative_int(raw.get("attempts")),
        "retries": _nonnegative_int(raw.get("retries")),
        "failed_attempts": _nonnegative_int(raw.get("failed_attempts")),
        "tool_calls": _nonnegative_int(raw.get("tool_calls")),
        "work_time_s": work_time_s,
        "_owner_session_id": owner_session_id,
        "_owner_project": owner_project,
    }
    return normalized


def _record_key(record):
    return tuple(
        (key, record.get(key))
        for key in PUBLIC_AGENT_FIELDS
    ) + (
        ("tokens_available", record.get("tokens_available")),
        ("cost_available", record.get("cost_available")),
    )


def _coverage(available_count, total, complete_label):
    if total <= 0 or available_count <= 0:
        return "unavailable"
    if available_count == total:
        return complete_label
    return "partial"


def _totals(records):
    records = list(records)
    known_cost = sum(
        record["cost"] for record in records if record["cost"] is not None
    )
    known_tokens = sum(
        record["tokens"] for record in records if record["tokens"] is not None
    )
    # A record whose owning session dropped child evidence (for example a
    # runtime run cap) cannot make any total that includes it complete.
    incomplete = any(record.get("_coverage_partial") for record in records)
    cost_available = bool(records) and not incomplete and all(
        record["cost_available"] for record in records
    )
    tokens_available = bool(records) and not incomplete and all(
        record["tokens_available"] for record in records
    )
    return {
        "agents": len(records),
        "tokens": known_tokens if tokens_available else None,
        "known_tokens": known_tokens,
        "tokens_available": tokens_available,
        "cost": known_cost if cost_available else None,
        "known_cost": known_cost,
        "cost_available": cost_available,
    }


def _attention(records, totals):
    children = [record for record in records if record["kind"] != "root"]
    reasons_by_id = defaultdict(list)
    if totals["cost_available"] and totals["known_cost"] > 0:
        for record in children:
            cost = record["cost"]
            share = cost / totals["known_cost"]
            if (
                record["activity_state"] == "working"
                and cost >= 1.0
                and share >= 0.5
            ):
                reasons_by_id[record["id"]].append({
                    "code": "active_cost_concentration",
                    "explanation": (
                        "Active child has at least $1.00 of covered estimated "
                        "spend and at least 50% of covered group spend."
                    ),
                })

        cohorts = defaultdict(list)
        for record in children:
            cohorts[(
                record["runtime"], record["model"], record["depth"],
                record["kind"],
            )].append(record)
        for cohort in cohorts.values():
            if len(cohort) < 4:
                continue
            for record in cohort:
                peer_costs = [
                    peer["cost"] for peer in cohort if peer["id"] != record["id"]
                ]
                if not peer_costs:
                    continue
                peer_median = statistics.median(peer_costs)
                if (
                    record["cost"] >= 0.5
                    and peer_median > 0
                    and record["cost"] >= 3 * peer_median
                ):
                    reasons_by_id[record["id"]].append({
                        "code": "peer_cost_outlier",
                        "explanation": (
                            "Covered estimated cost is at least $0.50 and 3x "
                            "the median of at least three comparable peers."
                        ),
                    })

    for record in children:
        if record["retries"] >= 3 or record["failed_attempts"] >= 3:
            reasons_by_id[record["id"]].append({
                "code": "retry_pressure",
                "explanation": (
                    "Provider evidence reports at least 3 retries or failed "
                    "attempts in the available recent execution window."
                ),
            })

    return [{
        "agent_id": record["id"],
        "level": "needs_attention",
        "reasons": reasons_by_id[record["id"]],
    } for record in records if reasons_by_id.get(record["id"])]


def _public_agent(record, totals, now, attention_ids):
    projected = {
        key: record.get(key) for key in PUBLIC_AGENT_FIELDS
    }
    projected["tokens"] = (
        record["tokens"] if record["tokens_available"] else None
    )
    if not record["tokens_available"]:
        for field in (
            "input_tokens", "output_tokens", "cache_read_tokens",
            "cache_write_tokens", "reasoning_tokens",
        ):
            projected[field] = None
    projected["tokens_available"] = record["tokens_available"]
    projected["cost"] = record["cost"] if record["cost_available"] else None
    projected["cost_available"] = record["cost_available"]
    projected["group_cost_share"] = (
        record["cost"] / totals["known_cost"]
        if totals["cost_available"] and totals["known_cost"] > 0 else None
    )
    projected["navigable"] = bool(record.get("session_id"))
    projected["attention_level"] = (
        "needs_attention" if record["id"] in attention_ids else None
    )
    return projected


def build_agent_graph(session_rows, *, now=None, max_agents=100):
    """Build groups plus the records whose parent could not be resolved.

    A child whose parent session was never discovered is reported here instead
    of being silently dropped, so callers can disclose the coverage gap.
    """
    now = float(time.time() if now is None else now)
    max_agents = max(1, min(100, int(max_agents or 100)))
    candidates = defaultdict(list)
    invalid_owner_ids = set()
    for session_row in session_rows or []:
        if not isinstance(session_row, dict):
            continue
        owner_session_id = _opaque_id(session_row.get("id"))
        owner_project = _project_key(session_row.get("project"))
        coverage_partial = session_row.get("_agent_records_partial") is True
        for raw in session_row.get("_agent_records") or []:
            record = _normalize_record(raw, owner_session_id, owner_project)
            if record is None:
                invalid_owner_ids.add(owner_session_id)
                continue
            if coverage_partial:
                record["_coverage_partial"] = True
            candidates[record["id"]].append(record)

    records = {}
    ambiguous_ids = set()
    for agent_id, options in candidates.items():
        unique = {_record_key(option): option for option in options}
        if len(unique) != 1:
            ambiguous_ids.add(agent_id)
            invalid_owner_ids.update(
                option["_owner_session_id"] for option in options
            )
            continue
        records[agent_id] = next(iter(unique.values()))

    resolution_cache = {}

    def resolve(agent_id, path=()):
        if agent_id in resolution_cache:
            return resolution_cache[agent_id]
        record = records.get(agent_id)
        if record is None or agent_id in ambiguous_ids or agent_id in path:
            return None
        if record["kind"] == "root":
            result = (agent_id, 0) if not record["parent_id"] else None
            resolution_cache[agent_id] = result
            return result
        parent_id = record["parent_id"]
        if not parent_id or parent_id == agent_id:
            resolution_cache[agent_id] = None
            return None
        parent_result = resolve(parent_id, path + (agent_id,))
        if parent_result is None:
            resolution_cache[agent_id] = None
            return None
        result = (parent_result[0], parent_result[1] + 1)
        resolution_cache[agent_id] = result
        return result

    by_root = defaultdict(list)
    unresolved = []
    for agent_id, record in records.items():
        resolved = resolve(agent_id)
        if resolved is None:
            unresolved.append(record)
            continue
        root_id, depth = resolved
        normalized = dict(record)
        normalized["depth"] = depth
        by_root[root_id].append(normalized)

    groups = []
    for root_id, members in by_root.items():
        if len(members) < 2:
            continue
        root = records[root_id]
        root_session_id = root.get("session_id") or root.get("_owner_session_id")
        if not root_session_id:
            continue
        # A session that dropped child evidence has at least one upstream run
        # this group cannot see; count it so coverage reads "partial".
        missing_upstream_runs = 1 if any(
            member.get("_coverage_partial") for member in members
        ) else 0
        coverage_total = len(members) + missing_upstream_runs
        members.sort(key=lambda item: (item["depth"], item["id"]))
        totals = _totals(members)
        attention = _attention(members, totals)
        attention_ids = {item["agent_id"] for item in attention}
        visible = members[:max_agents]
        owner_ids = {member["_owner_session_id"] for member in members}
        relationship_partial = any(
            record["_owner_session_id"] in owner_ids for record in unresolved
        ) or bool(owner_ids & invalid_owner_ids)
        session_ids = {
            member["session_id"]: member["id"]
            for member in members if member.get("session_id")
        }
        groups.append({
            "root_session_id": root_session_id,
            "_project": root.get("_owner_project") or "",
            "selected_agent_id": root_id,
            "coverage": {
                "relationships": (
                    "partial" if relationship_partial else "complete"
                ),
                "tokens": _coverage(
                    sum(1 for item in members if item["tokens_available"]),
                    coverage_total, "complete",
                ),
                "cost": _coverage(
                    sum(1 for item in members if item["cost_available"]),
                    coverage_total, "estimated",
                ),
            },
            "totals": totals,
            "child_totals": _totals(
                item for item in members if item["kind"] != "root"
            ),
            "agents": [
                _public_agent(item, totals, now, attention_ids)
                for item in visible
            ],
            "attention": attention,
            "hidden_agent_count": len(members) - len(visible),
            "_all_agents": members,
            "_session_ids": session_ids,
        })
    groups.sort(key=lambda group: group["root_session_id"])
    return groups, unresolved


def build_agent_groups(session_rows, *, now=None, max_agents=100):
    """Build bounded, cycle-free groups from adapter-supplied agent records."""
    return build_agent_graph(
        session_rows, now=now, max_agents=max_agents,
    )[0]


def find_agent_group(groups, session_id):
    """Return a selected-session copy of the group containing ``session_id``."""
    session_id = _opaque_id(session_id)
    for group in groups or []:
        selected_agent_id = (group.get("_session_ids") or {}).get(session_id)
        if selected_agent_id:
            selected = dict(group)
            selected["selected_agent_id"] = selected_agent_id
            return selected
    return None


def _detailed_totals(records):
    records = list(records)
    totals = _totals(records)
    token_covered = [
        record for record in records if record["tokens_available"]
    ]
    cost_covered = [
        record for record in records if record["cost_available"]
    ]
    token_fields = (
        "input_tokens", "output_tokens", "cache_read_tokens",
        "cache_write_tokens", "reasoning_tokens",
    )
    totals.update({
        "cost_covered_agents": len(cost_covered),
        "token_covered_agents": len(token_covered),
        "executions": sum(record["executions"] for record in records),
    })
    for field in token_fields:
        known = sum(
            record[field] for record in records
            if record["tokens"] is not None
        )
        totals[field] = known if totals["tokens_available"] else None
        totals["known_" + field] = known

    costs = sorted(record["cost"] for record in cost_covered)
    cost_complete = totals["cost_available"]
    totals["cost_per_agent"] = (
        totals["known_cost"] / len(records)
        if cost_complete and records else None
    )
    totals["median_cost"] = (
        statistics.median(costs) if cost_complete and costs else None
    )
    totals["p95_cost"] = (
        costs[max(0, math.ceil(0.95 * len(costs)) - 1)]
        if cost_complete and costs else None
    )
    totals["output_per_dollar"] = (
        totals["known_output_tokens"] / totals["known_cost"]
        if (
            totals["tokens_available"] and cost_complete
            and totals["known_cost"] > 0
        ) else None
    )
    return totals


def _cohort_rows(records, key_fn, identity_fn):
    grouped = defaultdict(list)
    for record in records:
        grouped[key_fn(record)].append(record)
    rows = []
    for key, members in grouped.items():
        row = identity_fn(key)
        row.update(_detailed_totals(members))
        rows.append(row)
    rows.sort(key=lambda row: (
        -(row["known_cost"] if row["cost_available"] else -1), row["id"],
    ))
    return rows


def _role_rows(records, attention_ids):
    grouped = defaultdict(list)
    for record in records:
        if record.get("role"):
            grouped[(
                record["runtime"], record["role"], record["kind"],
            )].append(record)
    rows = []
    for (runtime, role, kind), members in grouped.items():
        row = {
            "id": f"{role}::{runtime}::{kind}",
            "role": role,
            "runtime": runtime,
            "kind": kind,
            **_detailed_totals(members),
            "complete_agents": sum(
                record["activity_state"] == "complete" for record in members
            ),
            "incomplete_agents": sum(
                record["activity_state"] == "incomplete" for record in members
            ),
            "working_agents": sum(
                record["activity_state"] == "working" for record in members
            ),
            "attention_agents": sum(
                record["id"] in attention_ids for record in members
            ),
        }
        rows.append(row)
    rows.sort(key=lambda row: (
        -(row["known_cost"] if row["cost_available"] else -1), row["id"],
    ))
    return rows


def _usage_body(entries):
    records = [record for record, _group in entries]
    groups = {}
    for _record, group in entries:
        groups[group["root_session_id"]] = group
    totals = _detailed_totals(records)
    complete_groups = [
        group for group in groups.values()
        if (group.get("totals") or {}).get("cost_available") is True
    ]
    covered_group_cost = sum(
        float(group["totals"].get("cost") or 0)
        for group in complete_groups
    )
    complete_group_ids = {
        group["root_session_id"] for group in complete_groups
    }
    covered_child_cost = sum(
        float(record.get("cost") or 0)
        for record, group in entries
        if group["root_session_id"] in complete_group_ids
        and record.get("cost_available") is True
    )
    attention_ids = {
        item.get("agent_id")
        for group in groups.values()
        for item in group.get("attention") or ()
    }
    totals.update({
        "parent_sessions": len(groups),
        "covered_group_cost": covered_group_cost,
        "covered_child_cost": covered_child_cost,
        "group_cost_covered_sessions": len(complete_groups),
        "agent_spend_share": (
            covered_child_cost / covered_group_cost
            if covered_group_cost > 0 else None
        ),
        "group_cost_coverage": _coverage(
            len(complete_groups), len(groups), "estimated",
        ),
        "attention_agents": sum(
            1 for record in records if record["id"] in attention_ids
        ),
    })
    return {
        "totals": totals,
        "runtimes": _cohort_rows(
            records,
            lambda record: record["runtime"],
            lambda runtime: {"id": runtime, "runtime": runtime},
        ),
        "models": _cohort_rows(
            records,
            lambda record: (
                record["runtime"], record["model"], record["kind"],
            ),
            lambda key: {
                "id": f"{key[1]}::{key[0]}::{key[2]}",
                "runtime": key[0],
                "model": key[1],
                "kind": key[2],
            },
        ),
        "depths": _cohort_rows(
            records,
            lambda record: (
                record["runtime"], record["depth"], record["kind"],
            ),
            lambda key: {
                "id": f"{key[1]}::{key[0]}::{key[2]}",
                "runtime": key[0],
                "depth": key[1],
                "kind": key[2],
            },
        ),
        "kinds": _cohort_rows(
            records,
            lambda record: (record["runtime"], record["kind"]),
            lambda key: {
                "id": f"{key[1]}::{key[0]}",
                "runtime": key[0],
                "kind": key[1],
            },
        ),
        "roles": _role_rows(records, attention_ids),
    }


def _agent_activity_timestamp(record):
    for key in ("last_activity_at", "ended_at", "started_at"):
        value = record.get(key)
        if value is not None:
            return float(value)
    return None


def _local_month_start(now, months_ago):
    local = time.localtime(now)
    month_index = local.tm_year * 12 + local.tm_mon - 1 - months_ago
    return time.mktime((
        month_index // 12, month_index % 12 + 1, 1, 0, 0, 0, 0, 0, -1,
    ))


def _local_day_start(now, days_ago):
    local = time.localtime(now)
    return time.mktime((
        local.tm_year, local.tm_mon, local.tm_mday - days_ago, 0, 0, 0, 0, 0, -1,
    ))


def _inventory_row(record, group, attention, now):
    return {
        "id": record["id"],
        "root_session_id": group["root_session_id"],
        "project": str(group.get("_project") or ""),
        "runtime": record["runtime"],
        "client": record["client"],
        "kind": record["kind"],
        "depth": record["depth"],
        "label": record["label"],
        "role": record["role"],
        "model": record["model"],
        "activity_state": record["activity_state"],
        "last_activity_at": record["last_activity_at"],
        "tokens": record["tokens"] if record["tokens_available"] else None,
        "tokens_available": record["tokens_available"],
        "cost": record["cost"] if record["cost_available"] else None,
        "cost_available": record["cost_available"],
        "work_time_s": record["work_time_s"],
        "executions": record["executions"],
        "attempts": record["attempts"],
        "retries": record["retries"],
        "failed_attempts": record["failed_attempts"],
        "tool_calls": record["tool_calls"],
        "attention": [
            {
                "code": str(reason.get("code") or "")[:80],
                "explanation": str(reason.get("explanation") or "")[:320],
            }
            for reason in attention.get(record["id"], ())
            if isinstance(reason, dict)
        ][:8],
    }


def aggregate_agent_usage(
    groups, *, unresolved=(), now=None, max_inventory=MAX_AGENT_USAGE_INVENTORY,
    max_role_days=MAX_AGENT_ROLE_DAYS,
):
    """Aggregate child-agent usage without folding root-session work into it."""
    now = float(time.time() if now is None else now)
    entries = []
    seen = set()
    for group in groups or []:
        for record in group.get("_all_agents") or []:
            if record["kind"] == "root" or record["id"] in seen:
                continue
            seen.add(record["id"])
            entries.append((record, group))

    result = _usage_body(entries)
    # Records whose parent could not be resolved are counted in totals but have
    # no group, so the rollup must disclose them rather than look complete.
    unresolved_records = [
        record for record in (unresolved or ()) if isinstance(record, dict)
    ]
    result["totals"]["unresolved_agents"] = len(unresolved_records)
    result["totals"]["unresolved_known_cost"] = round(sum(
        float(record.get("cost") or 0)
        for record in unresolved_records
        if record.get("cost_available") is True
    ), 6)
    max_inventory = max(
        1, min(MAX_AGENT_USAGE_INVENTORY, int(max_inventory or 1)),
    )
    attention = {
        item.get("agent_id"): item.get("reasons") or ()
        for _record, group in entries
        for item in group.get("attention") or ()
        if isinstance(item, dict)
    }
    role_day_groups = defaultdict(list)
    for record, group in entries:
        timestamp = _agent_activity_timestamp(record)
        if not record.get("role") or timestamp is None:
            continue
        key = (
            time.strftime("%Y-%m-%d", time.localtime(timestamp)),
            str(group.get("_project") or ""),
            record["runtime"], record["kind"], record["role"],
        )
        role_day_groups[key].append(record)
    role_days = []
    for (day, project, runtime, kind, role), members in role_day_groups.items():
        role_days.append({
            "day": day,
            "project": project,
            "runtime": runtime,
            "kind": kind,
            "role": role,
            **_detailed_totals(members),
            "complete_agents": sum(
                record["activity_state"] == "complete" for record in members
            ),
            "incomplete_agents": sum(
                record["activity_state"] == "incomplete" for record in members
            ),
            "working_agents": sum(
                record["activity_state"] == "working" for record in members
            ),
            "attention_agents": sum(
                record["id"] in attention for record in members
            ),
        })
    role_days.sort(key=lambda row: (
        row["day"], row["runtime"], row["kind"], row["role"],
        row["project"],
    ), reverse=True)
    max_role_days = max(1, min(
        MAX_AGENT_ROLE_DAYS, int(max_role_days or 1),
    ))
    result["role_days"] = role_days[:max_role_days]
    result["role_day_count"] = len(role_days)
    result["role_days_truncated"] = len(role_days) > max_role_days
    inventory = [
        _inventory_row(record, group, attention, now)
        for record, group in entries
    ]
    inventory.sort(key=lambda row: (
        -(float(row["last_activity_at"])
          if row["last_activity_at"] is not None else -1.0),
        row["id"],
    ))
    result["inventory"] = inventory[:max_inventory]
    result["inventory_count"] = len(inventory)
    result["inventory_truncated"] = len(inventory) > max_inventory
    today = _local_day_start(now, 0)
    yesterday = _local_day_start(now, 1)
    windows = [
        ("all", None, None),
        ("today", (today, math.inf),
         (yesterday, min(yesterday + (now - today), today))),
        ("yesterday", (yesterday, today), (_local_day_start(now, 2), yesterday)),
    ]
    for window, seconds in (
        ("7d", 604_800), ("30d", 2_592_000), ("90d", 7_776_000),
    ):
        windows.append((
            window, (now - seconds, math.inf),
            (now - (2 * seconds), now - seconds),
        ))
    this_month = _local_month_start(now, 0)
    last_month = _local_month_start(now, 1)
    windows.append((
        "month", (this_month, math.inf),
        (last_month, min(last_month + (now - this_month), this_month)),
    ))
    windows.append((
        "last_month", (last_month, this_month),
        (_local_month_start(now, 2), last_month),
    ))
    scopes = []

    def in_bounds(entry, bounds):
        stamp = _agent_activity_timestamp(entry[0])
        return stamp is not None and bounds[0] <= stamp < bounds[1]

    def append_scopes(project, project_entries):
        runtimes = sorted({
            record["runtime"] for record, _group in project_entries
        })
        for window, bounds, prior in windows:
            window_entries = [
                entry for entry in project_entries
                if bounds is None or in_bounds(entry, bounds)
            ]
            for runtime in ("", *runtimes):
                scoped_entries = [
                    entry for entry in window_entries
                    if not runtime or entry[0]["runtime"] == runtime
                ]
                scope = {
                    "window": window,
                    "runtime": runtime,
                    "project": project,
                    **_usage_body(scoped_entries),
                }
                if prior is not None:
                    comparison_entries = [
                        entry for entry in project_entries
                        if in_bounds(entry, prior) and (
                            not runtime or entry[0]["runtime"] == runtime
                        )
                    ]
                    scope["comparison"] = _usage_body(comparison_entries)
                scopes.append(scope)

    append_scopes("", entries)
    projects = sorted({
        str(group.get("_project") or "")
        for _record, group in entries if group.get("_project")
    })
    for project in projects:
        append_scopes(project, [
            entry for entry in entries
            if str(entry[1].get("_project") or "") == project
        ])
    result["scopes"] = scopes
    return result
