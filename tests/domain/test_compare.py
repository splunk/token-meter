import json
import unittest

from token_meter.domain.compare import (
    MAX_COMPARE_SESSIONS,
    compare_entry,
    compare_sessions,
    matching_sessions,
    normalize_compare_ids,
    title_key,
    trace_key,
)


def make_summary(sid, title="Fix the flaky test", **extra):
    row = {
        "id": sid,
        "path": f"/Users/someone/.claude/projects/x/{sid}.jsonl",
        "title": title,
        "label": "Claude Code",
        "provider": "claude",
        "client": "claude_code",
        "project": "~/repo",
        "start": "2026-09-30 10:00",
        "last": "2026-09-30 10:05",
        "models": ["opus-5-5"],
        "primary_model": "claude-opus-5-5",
        "reasoning_effort": "",
        "usage_basis": "reported",
        "mtime": 100.0,
    }
    row.update(extra)
    return row


def make_state(cost=1.0, turns=10, duration=300, hit=0.9, peak=80000, errors=0,
               calls=12, parts=None, availability=None, series=None, **extra):
    parts = parts or {"input": 0.1, "cache_write": 0.4, "cache_read": 0.2, "output": 0.3}
    state = {
        "availability": availability or {
            "cost": True, "tokens": True, "cache": True, "throughput": True,
            "context": True, "timing": True, "tool_results": True,
        },
        "total_cost": cost,
        "cost": parts,
        "cost_approx": False,
        "total_tokens": 500000,
        "tokens": {"input": 1000, "cache_write": 40000, "cache_read": 450000, "output": 9000},
        "turns": turns,
        "subagent_turns": 0,
        "timing": {"duration_s": duration, "duration_available": True, "wall_duration_s": duration + 60},
        "throughput": {"available": True, "output_tps": 40.0},
        "wait_time": {"available": True, "avg_s": 12.0, "total_s": 120.0},
        "cache": {"hit_ratio": hit, "saved": 2.5, "available": True},
        "context": {"peak": peak, "window": 200000},
        "tools": {"total_calls": calls, "total_errors": errors, "unique_used": 3, "results_available": True},
        "analyses": {
            "reasoning": {"share": 0.1},
            "tool_bloat": [{"name": "Bash", "calls": 8, "tokens": 900}, {"name": "Read", "calls": 4, "tokens": 50}],
        },
        "series": series if series is not None else [
            {"i": i + 1, "cost": cost / turns, "in": 1000, "out": 100,
             "user_message": "SECRET PROMPT", "user_input": "SECRET PROMPT"}
            for i in range(turns)
        ],
        "trace_truncated": False,
    }
    state.update(extra)
    return state


class NormalizeIdsTests(unittest.TestCase):
    def test_splits_dedupes_and_bounds(self):
        ids, error = normalize_compare_ids(" a, b ,a,,c ")
        self.assertEqual(ids, ["a", "b", "c"])
        self.assertIsNone(error)

    def test_rejects_empty_too_many_and_oversized(self):
        self.assertEqual(normalize_compare_ids("only"), (["only"], None))
        self.assertIsNotNone(normalize_compare_ids(" , ")[1])
        many = ",".join(f"s{i}" for i in range(MAX_COMPARE_SESSIONS + 1))
        self.assertIsNotNone(normalize_compare_ids(many)[1])
        self.assertIsNotNone(normalize_compare_ids("a," + "x" * 300)[1])


class TitleKeyTests(unittest.TestCase):
    def test_normalizes_case_space_and_ellipsis(self):
        self.assertEqual(title_key("  Fix   the Flaky test…"), title_key("fix the flaky test"))

    def test_untitled_has_no_key(self):
        self.assertEqual(title_key("(untitled log)"), "")
        self.assertEqual(title_key(""), "")


class CompareEntryTests(unittest.TestCase):
    def test_projection_is_allowlisted_and_content_free(self):
        entry = compare_entry(make_summary("a"), make_state())
        payload = json.dumps(entry)
        self.assertNotIn("SECRET PROMPT", payload)
        self.assertNotIn("/Users/someone", payload)
        self.assertNotIn("path", entry)
        self.assertEqual(entry["cost"], 1.0)
        self.assertEqual(entry["turns"], 10)
        self.assertEqual(entry["duration_s"], 300)
        self.assertEqual(entry["top_tools"][0], {"name": "Bash", "calls": 8})
        self.assertAlmostEqual(entry["series"][-1]["cost"], 1.0)
        self.assertEqual(entry["series"][-1]["tokens"], 11000)

    def test_unavailable_evidence_is_none_not_zero(self):
        state = make_state(availability={"cost": False, "tokens": True, "cache": False,
                                         "throughput": False, "context": False,
                                         "timing": False, "tool_results": False})
        state["timing"]["duration_available"] = False
        state["throughput"]["available"] = False
        state["wait_time"]["available"] = False
        entry = compare_entry(make_summary("a"), state)
        for key in ("cost", "cost_parts", "cost_per_turn", "duration_s", "output_tps",
                    "cache_hit_ratio", "context_peak", "wait_avg_s", "tool_errors"):
            self.assertIsNone(entry[key], key)
        self.assertIsNone(entry["tool_calls"])
        self.assertIsNone(entry["tools_unique"])
        self.assertIsNone(entry["series"][-1]["cost"])
        no_tokens = make_state(availability={"cost": True, "tokens": False})
        entry = compare_entry(make_summary("b"), no_tokens)
        self.assertIsNone(entry["tokens"])
        self.assertIsNone(entry["series"][-1]["tokens"])

    def test_series_is_downsampled(self):
        state = make_state(turns=500)
        entry = compare_entry(make_summary("a"), state)
        self.assertLessEqual(len(entry["series"]), 120)
        self.assertEqual(entry["series"][-1]["i"], 500)
        self.assertAlmostEqual(entry["series"][-1]["cost"], 1.0)


class CompareSessionsTests(unittest.TestCase):
    def entries(self, *pairs):
        return [compare_entry(make_summary(sid, **summary), make_state(**state), key=sid)
                for sid, summary, state in pairs]

    def test_letters_best_and_same_prompt(self):
        result = compare_sessions(self.entries(
            ("a", {}, {"cost": 1.0, "duration": 300}),
            ("b", {}, {"cost": 3.0, "duration": 120}),
        ))
        self.assertEqual([s["letter"] for s in result["sessions"]], ["A", "B"])
        self.assertTrue(result["same_title"])
        self.assertEqual(result["best"]["cost"], "a")
        self.assertEqual(result["best"]["duration_s"], "b")
        self.assertEqual(result["sessions"][0]["key"], "a")
        self.assertNotIn("insights", result)

    def test_tied_metric_has_no_best(self):
        result = compare_sessions(self.entries(
            ("a", {}, {"cost": 2.0, "duration": 300}), ("b", {}, {"cost": 2.0, "duration": 100})))
        self.assertNotIn("cost", result["best"])
        self.assertEqual(result["best"]["duration_s"], "b")

    def test_unavailable_cost_is_excluded_not_compared_as_zero(self):
        entries = self.entries(("a", {}, {"cost": 1.0}), ("b", {}, {"cost": 2.0}))
        entries[1]["cost"] = None
        result = compare_sessions(entries)
        self.assertNotIn("cost", result["best"])

    def test_different_titles(self):
        result = compare_sessions(self.entries(
            ("a", {"title": "One thing"}, {}), ("b", {"title": "Another thing"}, {})))
        self.assertFalse(result["same_title"])

    def test_single_session_has_no_best(self):
        result = compare_sessions(self.entries(("a", {}, {})))
        self.assertEqual(result["best"], {})
        self.assertEqual(result["sessions"][0]["letter"], "A")


class TraceKeyTests(unittest.TestCase):
    def test_keys_are_opaque_and_distinct_for_forks_and_repeated_file_names(self):
        parent = {"id": "root", "path": "/x/rollout-1-root.jsonl"}
        fork = {"id": "root", "path": "/x/rollout-2-root_child.jsonl"}
        kiro_a = {"id": "a", "path": "/kiro/ws1/sess-a/messages.jsonl"}
        kiro_b = {"id": "b", "path": "/kiro/ws1/sess-b/messages.jsonl"}
        keys = [trace_key(row) for row in (parent, fork, kiro_a, kiro_b)]
        self.assertEqual(len(set(keys)), 4)
        for key in keys:
            self.assertRegex(key, r"^t[0-9a-f]{16}$")
            self.assertNotIn("/", key)
        self.assertEqual(trace_key(parent), trace_key(dict(parent)))
        self.assertEqual(trace_key({"id": "only-id"}), "only-id")

    def test_key_matches_dashboard_hash_for_non_ascii_paths(self):
        # Mirrors compareKeyFor in page.html; the JS side is checked in test_meter.
        self.assertEqual(trace_key({"path": "/Users/é/日本/x.jsonl"}), trace_key({"path": "/Users/é/日本/x.jsonl"}))
        self.assertNotEqual(trace_key({"path": "/a/b"}), trace_key({"path": "/a/c"}))

    def test_undecodable_file_names_do_not_raise(self):
        key = trace_key({"path": "/home/u/bad-\udcff.jsonl"})
        self.assertRegex(key, r"^t[0-9a-f]{16}$")

    def test_fork_with_same_id_is_still_a_match(self):
        parent_path, fork_path = "/x/rollout-1-root.jsonl", "/x/rollout-2-root_child.jsonl"
        selected = [compare_entry(make_summary("root", path=parent_path), make_state())]
        rows = [make_summary("root", path=parent_path), make_summary("root", path=fork_path)]
        matches = matching_sessions(selected, rows, open_id=lambda row: "open-" + row["path"][-10:])
        self.assertEqual([m["key"] for m in matches], [trace_key({"path": fork_path})])
        self.assertEqual(matches[0]["open_id"], "open-" + fork_path[-10:])


class MatchingSessionsTests(unittest.TestCase):
    def test_matches_same_title_excluding_selected(self):
        selected = [compare_entry(make_summary("a"), make_state())]
        rows = [
            make_summary("a"),
            make_summary("b", mtime=300.0, cost=0.5, turns=4, availability={"cost": True}),
            make_summary("c", title="Something else"),
            make_summary("d", title="FIX the flaky test…", mtime=200.0, availability={"cost": False}),
        ]
        matches = matching_sessions(selected, rows)
        self.assertEqual([m["id"] for m in matches], ["b", "d"])
        self.assertNotIn("path", matches[0])
        self.assertEqual(matches[0]["cost"], 0.5)
        self.assertIsNone(matches[1]["cost"])

    def test_limit(self):
        selected = [compare_entry(make_summary("a"), make_state())]
        rows = [make_summary(f"s{i}", mtime=float(i)) for i in range(40)]
        self.assertEqual(len(matching_sessions(selected, rows, limit=12)), 12)


if __name__ == "__main__":
    unittest.main()
