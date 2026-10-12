"""Payload-free tool categorization, evidence, and capability review rules."""

import time
from collections import defaultdict


TOOL_OVERSIZED_TOKENS = 8000


def tool_identity(name):
    raw = name or "?"
    clean = raw
    kind = "tool"
    if raw.startswith("mcp__"):
        parts = raw.split("__")
        namespace = parts[1] if len(parts) > 1 and parts[1] else "mcp"
        clean = parts[2] if len(parts) > 2 and parts[2] else raw
        kind = "mcp"
    elif "." in raw:
        namespace = raw.split(".", 1)[0]
        clean = raw.split(".")[-1]
    elif raw in ("exec_command", "write_stdin"):
        namespace = "shell"
    elif raw == "apply_patch":
        namespace = "files"
    elif raw.startswith("web_") or raw.startswith("web"):
        namespace = "web"
    elif raw.startswith("multi_tool"):
        namespace = "orchestration"
    elif raw.startswith("tool_search"):
        namespace = "tool_search"
    else:
        namespace = raw.split("_", 1)[0] if raw and raw != "?" else "unknown"
    return {"name": raw, "display": clean, "namespace": namespace, "kind": kind}


def tool_summary(executions):
    """Aggregate payload-free tool facts across normalized executions."""

    by_name = {}
    by_namespace = {}
    by_skill = defaultdict(int)
    by_execution = []
    for execution in executions:
        row_tools = {}
        for tool in execution.get("tools", []):
            name = tool.get("name") or "?"
            namespace = tool.get("namespace") or "unknown"
            kind = tool.get("kind") or "tool"
            tokens = int(tool.get("output_tokens") or 0)
            output_chars = int(tool.get("output_chars") or 0)
            args_chars = int(tool.get("args_chars") or 0)
            for skill_name in tool.get("skills") or []:
                if skill_name:
                    by_skill[str(skill_name)] += 1
            named = by_name.setdefault(name, {
                "name": name,
                "display": tool.get("display") or name,
                "namespace": namespace,
                "kind": kind,
                "calls": 0,
                "output_tokens": 0,
                "output_chars": 0,
                "args_chars": 0,
                "errors": 0,
                "executions": set(),
            })
            named["calls"] += 1
            named["output_tokens"] += tokens
            named["output_chars"] += output_chars
            named["args_chars"] += args_chars
            named["errors"] += 1 if tool.get("error") else 0
            named["executions"].add(execution["idx"])
            grouped = by_namespace.setdefault(namespace, {
                "namespace": namespace,
                "kind": kind,
                "calls": 0,
                "output_tokens": 0,
                "errors": 0,
                "executions": set(),
            })
            grouped["calls"] += 1
            grouped["output_tokens"] += tokens
            grouped["errors"] += 1 if tool.get("error") else 0
            grouped["executions"].add(execution["idx"])
            key = (namespace, name)
            per_execution = row_tools.setdefault(key, {
                "name": name,
                "display": tool.get("display") or name,
                "namespace": namespace,
                "kind": kind,
                "calls": 0,
                "output_tokens": 0,
                "output_chars": 0,
                "args_chars": 0,
                "errors": 0,
            })
            per_execution["calls"] += 1
            per_execution["output_tokens"] += tokens
            per_execution["output_chars"] += output_chars
            per_execution["args_chars"] += args_chars
            per_execution["errors"] += 1 if tool.get("error") else 0
        if row_tools:
            rows = sorted(
                row_tools.values(),
                key=lambda row: (-row["output_tokens"], -row["calls"], row["name"]),
            )
            by_execution.append({
                "execution": execution["idx"],
                "cost": execution.get("cost", 0.0),
                "tokens": execution.get("tokens", {}).get("total", 0),
                "model": execution.get("model"),
                "context_tokens": execution.get("tokens", {}).get("input", 0),
                "tool_calls": sum(row["calls"] for row in rows),
                "unique_tools": len(rows),
                "namespaces": sorted(set(row["namespace"] for row in rows)),
                "tools": rows,
            })
    by_name_rows = []
    for row in by_name.values():
        row = dict(row)
        row["executions"] = sorted(row["executions"])
        row["execution_count"] = len(row["executions"])
        by_name_rows.append(row)
    by_namespace_rows = []
    for row in by_namespace.values():
        row = dict(row)
        row["executions"] = sorted(row["executions"])
        row["execution_count"] = len(row["executions"])
        by_namespace_rows.append(row)
    total_calls = sum(row["calls"] for row in by_name_rows)
    peak_calls = max((row["tool_calls"] for row in by_execution), default=0)
    peak_unique = max((row["unique_tools"] for row in by_execution), default=0)
    shown_execution_rows = by_execution[-80:]
    return {
        "total_calls": total_calls,
        "total_output_tokens": sum(row["output_tokens"] for row in by_name_rows),
        "total_errors": sum(row["errors"] for row in by_name_rows),
        "unique_used": len(by_name_rows),
        "namespaces_used": len(by_namespace_rows),
        "peak_calls_per_execution": peak_calls,
        "peak_tools_per_execution": peak_unique,
        "execution_rows_total": len(by_execution),
        "execution_rows_shown": len(shown_execution_rows),
        "execution_rows_truncated": len(shown_execution_rows) < len(by_execution),
        "execution_calls_shown": sum(
            row["tool_calls"] for row in shown_execution_rows
        ),
        "skills": [
            {"name": name, "activations": activations}
            for name, activations in sorted(
                by_skill.items(), key=lambda item: (-item[1], item[0])
            )
        ],
        "activity": {
            "scope": "session",
            "observed_unique": len(by_name_rows),
            "total_calls": total_calls,
            "peak_calls_per_execution": peak_calls,
            "namespaces_used": len(by_namespace_rows),
        },
        "by_name": sorted(
            by_name_rows,
            key=lambda row: (-row["output_tokens"], -row["calls"], row["name"]),
        )[:16],
        "by_namespace": sorted(
            by_namespace_rows,
            key=lambda row: (-row["output_tokens"], -row["calls"], row["namespace"]),
        )[:12],
        "by_execution": shown_execution_rows,
    }


def _tool_evidence_row(call, name):
    return {
        "name": name,
        "display": call.get("display") or name,
        "namespace": call.get("namespace") or "unknown",
        "kind": call.get("kind") or "tool",
        "calls": 0,
        "output_tokens": 0,
        "flagged_tokens": 0,
        "errors": 0,
        "oversized_calls": 0,
        "repeat_calls": 0,
        "nested_calls": 0,
        "host_provided": False,
        "last_ts": 0,
        "daily": {},
    }


def _daily_evidence_row(day):
    return {
        "day": day, "calls": 0, "output_tokens": 0,
        "flagged_tokens": 0, "errors": 0, "oversized_calls": 0,
        "repeat_calls": 0, "nested_calls": 0,
    }


def summarize_tool_evidence(calls, catalog=None):
    by_name = {}
    by_skill = {}
    previous_key = None
    previous_ts = 0
    day_tokens = defaultdict(int)
    day_flagged = defaultdict(int)
    totals = {
        "total_calls": 0,
        "total_output_tokens": 0,
        "flagged_tokens": 0,
        "oversized_calls": 0,
        "oversized_tokens": 0,
        "repeat_calls": 0,
        "repeat_tokens": 0,
        "errors": 0,
        "error_tokens": 0,
    }
    for call in calls or []:
        name = call.get("name") or "?"
        if call.get("nested"):
            # Call sites inside a wrapper (Codex code-mode exec) prove use, but the
            # wrapper already carries the session call total and returned tokens.
            row = by_name.setdefault(name, _tool_evidence_row(call, name))
            row["host_provided"] = row["host_provided"] or bool(call.get("host_provided"))
            row["calls"] += 1
            row["nested_calls"] += 1
            ts = int(call.get("ts") or 0)
            row["last_ts"] = max(row["last_ts"], ts)
            if ts:
                day = time.strftime("%Y-%m-%d", time.localtime(ts))
                daily = row["daily"].setdefault(day, _daily_evidence_row(day))
                daily["calls"] += 1
                daily["nested_calls"] += 1
            continue
        tokens = int(call.get("output_tokens") or 0)
        error = bool(call.get("error"))
        oversized = tokens >= TOOL_OVERSIZED_TOKENS
        ts = int(call.get("ts") or 0)
        args_fingerprint = call.get("args_fingerprint") or ""
        call_key = (name, args_fingerprint) if args_fingerprint else None
        repeated = bool(
            call_key
            and previous_key == call_key
            and (not ts or not previous_ts or 0 <= ts - previous_ts <= 300)
        )
        previous_key, previous_ts = call_key, ts
        flagged = bool(oversized or repeated or error)
        day = time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else ""
        if day:
            day_tokens[day] += tokens
            if flagged:
                day_flagged[day] += tokens

        row = by_name.setdefault(name, _tool_evidence_row(call, name))
        row["host_provided"] = row["host_provided"] or bool(call.get("host_provided"))
        row["calls"] += 1
        row["output_tokens"] += tokens
        row["flagged_tokens"] += tokens if flagged else 0
        row["errors"] += 1 if error else 0
        row["oversized_calls"] += 1 if oversized else 0
        row["repeat_calls"] += 1 if repeated else 0
        row["last_ts"] = max(row["last_ts"], ts)
        if day:
            daily = row["daily"].setdefault(day, _daily_evidence_row(day))
            daily["calls"] += 1
            daily["output_tokens"] += tokens
            daily["flagged_tokens"] += tokens if flagged else 0
            daily["errors"] += 1 if error else 0
            daily["oversized_calls"] += 1 if oversized else 0
            daily["repeat_calls"] += 1 if repeated else 0

        totals["total_calls"] += 1
        totals["total_output_tokens"] += tokens
        totals["flagged_tokens"] += tokens if flagged else 0
        totals["oversized_calls"] += 1 if oversized else 0
        totals["oversized_tokens"] += tokens if oversized else 0
        totals["repeat_calls"] += 1 if repeated else 0
        totals["repeat_tokens"] += tokens if repeated else 0
        totals["errors"] += 1 if error else 0
        totals["error_tokens"] += tokens if error else 0

        for skill_name in call.get("skills") or []:
            skill = by_skill.setdefault(
                skill_name,
                {"name": skill_name, "activations": 0, "last_ts": 0},
            )
            skill["activations"] += 1
            skill["last_ts"] = max(skill["last_ts"], ts)

    for row in by_name.values():
        row["daily"] = [row["daily"][day] for day in sorted(row["daily"])]
    totals["tools"] = sorted(
        by_name.values(),
        key=lambda row: (-row["output_tokens"], -row["calls"], row["name"]),
    )
    totals["skills"] = sorted(
        by_skill.values(), key=lambda row: (-row["activations"], row["name"])
    )
    totals["day_tokens"] = dict(day_tokens)
    totals["day_flagged"] = dict(day_flagged)
    totals["catalog"] = list(catalog or [])
    used_names = set(by_name)
    totals["definition_tokens"] = sum(
        int(row.get("definition_tokens") or 0) for row in totals["catalog"]
    )
    totals["eager_definition_tokens"] = sum(
        int(row.get("definition_tokens") or 0)
        for row in totals["catalog"] if not row.get("defer_loading")
    )
    totals["deferred_definition_tokens"] = (
        totals["definition_tokens"] - totals["eager_definition_tokens"]
    )
    totals["unused_eager_definition_tokens"] = sum(
        int(row.get("definition_tokens") or 0)
        for row in totals["catalog"]
        if not row.get("defer_loading") and row.get("name") not in used_names
    )
    return totals


def session_capabilities(evidence, loaded_skills=None, loaded_mcp_servers=None):
    """Count loaded and used skills / MCP servers; unknown loads stay None."""
    evidence = evidence or {}
    used_skills = {row.get("name") for row in evidence.get("skills") or [] if row.get("name")}
    catalog_servers = {
        row.get("namespace") for row in evidence.get("catalog") or []
        if row.get("kind") == "mcp" and row.get("namespace") and not row.get("host_provided")
    }
    catalog_names = {
        row.get("name"): row.get("namespace") for row in evidence.get("catalog") or []
        if row.get("kind") == "mcp" and row.get("name") and row.get("namespace")
        and not row.get("host_provided")
    }
    host_namespaces = {
        row.get("namespace") for row in evidence.get("catalog") or [] if row.get("host_provided")
    }
    used_servers = set()
    for row in evidence.get("tools") or []:
        if row.get("host_provided") or row.get("namespace") in host_namespaces:
            continue
        if row.get("kind") == "mcp" and row.get("namespace"):
            used_servers.add(row["namespace"])
        elif row.get("name") in catalog_names:
            used_servers.add(catalog_names[row["name"]])
    if loaded_mcp_servers is None and catalog_servers:
        loaded_mcp_servers = catalog_servers
    elif loaded_mcp_servers is not None:
        loaded_mcp_servers = set(loaded_mcp_servers) | catalog_servers

    def loaded(names, used):
        return None if names is None else len(set(names) | used)

    return {
        "skills": {"loaded": loaded(loaded_skills, used_skills), "used": len(used_skills)},
        "mcp_servers": {
            "loaded": loaded(loaded_mcp_servers, used_servers), "used": len(used_servers),
        },
    }


def capability_control_groups(_mcp_items, skill_items):
    groups = []
    packs = {}
    for row in skill_items or []:
        if not row.get("mutable") or not row.get("plugin_id"):
            continue
        key = (row.get("runtime") or "unknown", row.get("plugin_id"))
        pack = packs.setdefault(key, {
            "id": "skill_pack:{}:{}".format(key[0], key[1]),
            "control_type": "skill_pack",
            "item_id": row.get("id"),
            "type": "skill",
            "name": key[1],
            "runtime": key[0],
            "plugin_id": key[1],
            "enabled": False,
            "used": False,
            "mutable": True,
            "calls": 0,
            "activations": 0,
            "members": set(),
            "returned_tokens": 0,
            "used_member_names": set(),
            "last_used": "Never",
            "definition_tokens": 0,
            "eager_definition_tokens": 0,
            "deferred_definition_tokens": 0,
            "unused_eager_definition_tokens": 0,
            "origin": row.get("origin") or "user_plugin",
            "origin_id": row.get("origin_id") or key[1],
            "source": row.get("source") or key[1],
            "reviewable": bool(row.get("reviewable", True)),
            "setting_path": row.get("setting_path") or "",
            "member_ids": set(),
            "measurable_members": 0,
            "instruction_members": 0,
            "unknown_members": 0,
            "measurement": "unknown",
            "unmeasurable": False,
        })
        name = row.get("name") or "?"
        pack["members"].add(name)
        pack["member_ids"].add(row.get("id"))
        pack["enabled"] = pack["enabled"] or bool(row.get("enabled"))
        pack["used"] = pack["used"] or bool(row.get("used"))
        if row.get("used"):
            pack["used_member_names"].add(name)
        pack["activations"] += int(row.get("activations") or 0)
        measurement = str(
            row.get("measurement")
            or ("unknown" if row.get("unmeasurable") else "measurable")
        )
        if row.get("used"):
            measurement = "measurable"
        if measurement not in ("measurable", "instruction", "unknown"):
            measurement = "unknown"
        pack["{}_members".format(measurement)] += 1
        last_used = row.get("last_used") or "Never"
        if (
            last_used != "Never"
            and (pack["last_used"] == "Never" or last_used > pack["last_used"])
        ):
            pack["last_used"] = last_used
    for pack in packs.values():
        members = sorted(pack.pop("members"))
        used_members = pack.pop("used_member_names")
        pack["members"] = members
        pack["member_ids"] = sorted(value for value in pack["member_ids"] if value)
        pack["member_count"] = len(members)
        pack["used_members"] = len(used_members)
        if pack["used"] or pack["measurable_members"] == pack["member_count"]:
            pack["measurement"] = "measurable"
        elif pack["instruction_members"] == pack["member_count"]:
            pack["measurement"] = "instruction"
        else:
            pack["measurement"] = "unknown"
        pack["unmeasurable"] = pack["measurement"] != "measurable"
        groups.append(pack)
    return sorted(
        groups,
        key=lambda row: (row["control_type"], row["runtime"], row["name"]),
    )


def optional_capability_summary(control_groups):
    enabled = [
        row for row in (control_groups or [])
        if row.get("enabled") and row.get("mutable") and row.get("reviewable", True)
    ]
    used = [row for row in enabled if row.get("used")]
    unscanned = [
        row for row in enabled
        if (
            not row.get("used")
            and not row.get("unmeasurable")
            and row.get("scanned_sessions") is not None
            and int(row.get("scanned_sessions") or 0) <= 0
        )
    ]
    unused = [
        row for row in enabled
        if not row.get("used") and not row.get("unmeasurable") and row not in unscanned
    ]
    unmeasurable = [
        row for row in enabled if row.get("unmeasurable") and not row.get("used")
    ]
    instruction = [
        row for row in unmeasurable if row.get("measurement") == "instruction"
    ]
    unknown = [row for row in unmeasurable if row.get("measurement") != "instruction"]
    mcp_enabled = [row for row in enabled if row.get("control_type") == "mcp"]
    skill_enabled = [row for row in enabled if row.get("control_type") == "skill_pack"]
    avoidable_tokens = sum(
        int(row.get("unused_eager_definition_tokens") or 0) for row in unused
    )
    eager_unused = [
        row for row in unused
        if int(row.get("unused_eager_definition_tokens") or 0) > 0
    ]
    deferred_unused = [
        row for row in unused
        if int(row.get("deferred_definition_tokens") or 0) > 0
        and int(row.get("unused_eager_definition_tokens") or 0) == 0
    ]
    return {
        "scope": "all_sessions",
        "enabled": len(enabled),
        "used": len(used),
        "unused": len(unused),
        "unmeasurable_packs": len(unmeasurable),
        "instruction_packs": len(instruction),
        "unknown_evidence_packs": len(unknown),
        "unscanned_packs": len(unscanned),
        "utilization": len(used) / len(enabled) if enabled else 0.0,
        "mcp_enabled": len(mcp_enabled),
        "mcp_used": sum(1 for row in mcp_enabled if row.get("used")),
        "skill_packs_enabled": len(skill_enabled),
        "skill_packs_used": sum(1 for row in skill_enabled if row.get("used")),
        "review_candidates": [row["id"] for row in unused],
        "review_candidate_names": [row["name"] for row in unused],
        "avoidable_eager_definition_tokens": avoidable_tokens,
        "overhead_measured_groups": sum(
            1 for row in enabled if int(row.get("definition_tokens") or 0) > 0
        ),
        "eager_unused_groups": len(eager_unused),
        "deferred_unused_groups": len(deferred_unused),
        "unmeasured_unused_groups": sum(
            1 for row in unused if not row.get("definition_tokens")
        ),
    }
