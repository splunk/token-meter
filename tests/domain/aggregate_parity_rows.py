"""Deterministic synthetic session rows for aggregate parity goldens.

This module must not import ``token_meter`` so the same rows can be fed to a
reference build when the golden is regenerated.
"""

import copy
import hashlib
import json
import random

GOLDEN_NOW = 1792000000.0
RUNTIMES = ("Claude Code", "Codex", "Cursor", "OpenCode")
PROVIDERS = {"Claude Code": "claude", "Codex": "codex", "Cursor": "cursor", "OpenCode": "opencode"}
MODELS = ("claude-opus-4-8", "claude-fable-5", "gpt-5.5", "composer-2", "<synthetic>")
DAYS = tuple("2026-0{}-{:02d}".format(month, day) for month in (7, 8) for day in (3, 11, 19, 27))


def _availability(rng):
    choice = rng.random()
    if choice < 0.15:
        return None
    if choice < 0.3:
        return {"cost": False, "tokens": True, "input_tokens": True,
                "output_tokens": rng.random() < 0.5, "cache": False}
    if choice < 0.4:
        return {"cost": False, "tokens": False, "cache": False}
    return {"cost": True, "tokens": True, "input_tokens": True,
            "output_tokens": True, "cache": rng.random() < 0.8}


def _model_stats(rng, model, explicit):
    executions = rng.randint(0, 9)
    input_tokens = rng.randint(0, 90000)
    output_tokens = rng.randint(0, 9000)
    cost = rng.random() * 3.7
    stats = {
        "model": model, "executions": executions,
        "input_tokens": input_tokens, "output_tokens": output_tokens,
        "cache_read_tokens": rng.randint(0, 50000),
        "cache_write_tokens": rng.randint(0, 9000),
        "reasoning_tokens": rng.randint(0, 3000),
        "reasoning_output_tokens": rng.randint(0, 2000),
        "reasoning_executions": rng.randint(0, executions),
        "thinking_executions": rng.randint(0, executions),
        "thinking_covered_executions": rng.randint(0, executions),
        "cost": cost, "tokens": input_tokens + output_tokens,
    }
    if rng.random() < 0.4:
        stats["cache_write_5m_tokens"] = rng.randint(0, 4000)
        stats["cache_write_1h_tokens"] = rng.randint(0, 4000)
    if explicit:
        stats.update({
            "token_covered_executions": rng.randint(0, executions),
            "io_covered_executions": rng.randint(0, executions),
            "io_covered_input_tokens": rng.randint(0, input_tokens),
            "io_covered_output_tokens": rng.randint(0, output_tokens),
            "cost_covered_executions": rng.randint(0, executions),
            "cost_covered_output_tokens": rng.randint(0, output_tokens),
            "cost_covered_cost": cost * rng.random(),
            "cache_covered_input_tokens": rng.randint(0, input_tokens),
            "availability": {"tokens": True, "output_tokens": rng.random() < 0.7,
                             "cost": rng.random() < 0.6},
        })
    return stats


def build_rows(count=120, seed=20261023):
    rng = random.Random(seed)
    rows = []
    for index in range(count):
        runtime = rng.choice(RUNTIMES)
        provider = PROVIDERS[runtime]
        models = rng.sample(MODELS, rng.randint(1, 3))
        explicit = rng.random() < 0.5
        estimate = rng.random() < 0.2
        # The tail is one model with no cost or token evidence at all.
        unavailable_tail = index >= count - 3
        if unavailable_tail:
            models, explicit, estimate = ["legacy-unavailable"], False, False
        model_stats = [_model_stats(rng, model, explicit) for model in models]
        model_daily = []
        day_cost = {}
        for stats in model_stats:
            for day in rng.sample(DAYS, rng.randint(1, 3)):
                daily = dict(_model_stats(rng, stats["model"], explicit), day=day)
                model_daily.append(daily)
                day_cost[day] = day_cost.get(day, 0.0) + daily["cost"]
        performance = []
        waits = []
        for _ in range(rng.randint(0, 6)):
            day = rng.choice(DAYS)
            duration = rng.random() * 40
            performance.append({
                "model": rng.choice(models), "day": day,
                "ts": GOLDEN_NOW - rng.randint(0, 9_000_000),
                "duration_s": duration, "generation_s": duration * rng.random(),
                "ttft_s": rng.random() * 3, "input_tokens": rng.randint(0, 40000),
                "peak_input_tokens": rng.randint(0, 80000),
                "context_tokens": rng.randint(0, 80000),
                "output_tokens": rng.randint(0, 4000),
                "tool_calls": rng.choice((0, 0, 1, 3)), "model_calls": rng.randint(1, 4),
                "cache_read_tokens": rng.randint(0, 30000),
                "attempts": rng.randint(0, 3), "retries": rng.randint(0, 2),
                "failed_attempts": rng.randint(0, 1),
            })
            waits.append({
                "model": rng.choice((*models, "mixed")), "day": day,
                "duration_s": rng.random() * 90 - 5,
                "user_pause_s": rng.random() * 10, "ttft_s": rng.random(),
                "context_tokens": rng.randint(0, 60000),
                "tool_calls": rng.randint(0, 4), "model_calls": rng.randint(1, 3),
            })
        cost = sum(stats["cost"] for stats in model_stats)
        tokens = sum(stats["tokens"] for stats in model_stats)
        rows.append({
            "id": "session-{}".format(index % 111),
            "path": "/synthetic/{}/{}.jsonl".format(provider, index),
            "provider": provider, "runtime": runtime, "label": runtime,
            "project": rng.choice(("/work/alpha", "/work/beta", "~/scratch", "No project")),
            "title": "Synthetic {}".format(index),
            "mtime": GOLDEN_NOW - rng.randint(0, 9_000_000), "turns": rng.randint(0, 40),
            "cost": cost, "tokens": tokens,
            "input_tokens": sum(stats["input_tokens"] for stats in model_stats),
            "output_tokens": sum(stats["output_tokens"] for stats in model_stats),
            "token_estimate": estimate,
            "availability": (
                {"cost": False, "tokens": False, "input_tokens": False,
                 "output_tokens": False, "cache": False}
                if unavailable_tail else _availability(rng)
            ),
            "primary_model": models[0],
            "model_stats": model_stats,
            "_model_daily": model_daily,
            "_performance_samples": performance,
            "_wait_samples": waits,
            "_model_cost": {stats["model"]: stats["cost"] for stats in model_stats},
            "_model_tok": {stats["model"]: stats["tokens"] for stats in model_stats},
            "_day_cost": day_cost,
        })
    return rows


def _finalize_throughput(row):
    seconds = float(row.get("tool_free_seconds") or row.get("timed_seconds") or 0)
    output = int(row.get("tool_free_output_tokens") or row.get("timed_output_tokens") or 0)
    row["output_tps"] = output / seconds if seconds > 0 else 0
    row["throughput_samples"] = int(row.get("timed_samples") or 0)
    return row


def _pace_groups(groups):
    return {key: copy.deepcopy(samples) for key, samples in sorted(groups.items())}


def compute(aggregates, usage, rows=None):
    """Run every provenance-bearing aggregate and return canonical JSON per section."""
    rows = build_rows() if rows is None else rows
    result = {
        "cross_session": aggregates.aggregate_cross_session_rows(copy.deepcopy(rows)),
        "daily": aggregates.daily_summaries(copy.deepcopy(rows), limit=None),
        "monthly": aggregates.monthly_summaries(copy.deepcopy(rows)),
        "model_stats": aggregates.aggregate_model_stats(
            copy.deepcopy(rows),
            throughput_finalizer=_finalize_throughput,
            matched_pace=_pace_groups,
        ),
        "usage_provenance": usage.usage_provenance(copy.deepcopy(rows)),
        "provenance_shapes": [
            usage.make_usage_provenance(*arguments)
            for arguments in (
                ((), (), ()),
                ({"a", "b", "c"}, {"b", "x"}, None, 1.5, 7),
                (["a", "b", "a"], ("b",), ["a", "b", "z"], 0.25, 3),
                (frozenset({"a", "b"}), frozenset({"a"}), frozenset({"a"})),
                ({"a"}, None, set()),
                ({"a", "b"}, {"a", "b"}, {"a", "b"}),
            )
        ],
    }
    return {
        section: json.dumps(value, sort_keys=True, default=sorted)
        for section, value in result.items()
    }


def digests(sections):
    """Canonical-JSON SHA-256 per section; floats serialize with exact repr."""
    return {
        section: hashlib.sha256(text.encode("utf-8")).hexdigest()
        for section, text in sorted(sections.items())
    }


def build_agent_sessions(count=40, seed=7_2026):
    """Root sessions with child agents across runtimes, projects, roles, and windows."""
    rng = random.Random(seed)
    sessions = []
    for index in range(count):
        runtime = rng.choice(("codex", "claude", "opencode"))
        project = rng.choice(("/work/alpha", "/work/beta", None))
        root_id = "root-{}".format(index)
        started = GOLDEN_NOW - rng.randint(0, 80 * 86400)
        records = [{
            "id": root_id, "parent_id": None, "session_id": "session-{}".format(index),
            "kind": "root", "runtime": runtime, "client": runtime.title(),
            "model": rng.choice(MODELS), "depth": 0, "cost": rng.random(),
            "cost_available": True, "tokens": rng.randint(0, 9000), "tokens_available": True,
            "activity_state": "complete", "role": None, "label": "",
            "started_at": started, "ended_at": started + 60, "last_activity_at": started + 60,
            "executions": 2, "attempts": 2, "retries": 0, "failed_attempts": 0,
            "tool_calls": 1, "work_time_s": None,
        }]
        parents = [(root_id, 0)]
        for child in range(rng.randint(0, 5)):
            parent_id, depth = rng.choice(parents)
            child_id = "{}-child-{}".format(root_id, child)
            stamp = started + rng.randint(0, 20 * 86400)
            if rng.random() < 0.2:
                stamp = GOLDEN_NOW - rng.randint(0, 3600)
            cost_available = rng.random() < 0.8
            tokens_available = rng.random() < 0.85
            records.append({
                "id": child_id, "parent_id": parent_id if rng.random() < 0.95 else "missing-parent",
                "session_id": "session-{}-{}".format(index, child),
                "kind": rng.choice(("spawned", "subagent", "fork")),
                "runtime": runtime, "client": runtime.title(),
                "model": rng.choice(MODELS), "depth": depth + 1,
                "cost": rng.random() * 2, "cost_available": cost_available,
                "tokens": rng.randint(0, 40000), "tokens_available": tokens_available,
                "input_tokens": rng.randint(0, 30000), "output_tokens": rng.randint(0, 9000),
                "cache_read_tokens": rng.randint(0, 20000), "cache_write_tokens": rng.randint(0, 5000),
                "reasoning_tokens": rng.randint(0, 2000),
                "activity_state": rng.choice(("complete", "incomplete", "working", "recent")),
                "role": rng.choice((None, "explorer", "reviewer", "tester")),
                "label": "", "started_at": stamp - 120, "ended_at": stamp,
                "last_activity_at": stamp, "executions": rng.randint(1, 9),
                "attempts": rng.randint(1, 4), "retries": rng.randint(0, 2),
                "failed_attempts": rng.randint(0, 1), "tool_calls": rng.randint(0, 30),
                "work_time_s": rng.random() * 600,
            })
            parents.append((child_id, depth + 1))
        row = {"id": "session-{}".format(index), "_agent_records": records}
        if project is not None:
            row["project"] = project
        sessions.append(row)
    return sessions


def compute_agent_usage(agents):
    groups, unresolved = agents.build_agent_graph(build_agent_sessions(), now=GOLDEN_NOW)
    usage = agents.aggregate_agent_usage(groups, unresolved=unresolved, now=GOLDEN_NOW)
    return json.dumps(usage, sort_keys=True, default=sorted)
