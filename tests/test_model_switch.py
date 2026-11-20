"""A session that switches models mid-way shows the model in use now."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import meter
from token_meter.domain.usage import latest_model
from tests.dashboard_layout_support import DashboardLayoutCase

OPUS, SONNET = "claude-opus-4-6", "claude-sonnet-4-6"


def _claude_turn(minute, model, output_tokens):
    stamp = f"2026-07-02T00:{minute:02d}"
    return [
        {"type": "user", "timestamp": f"{stamp}:00.000Z",
         "message": {"role": "user", "content": "synthetic"}},
        {"type": "assistant", "timestamp": f"{stamp}:30.000Z",
         "message": {"id": f"msg-{minute}", "model": model, "content": [],
                     "usage": {"input_tokens": 1000, "output_tokens": output_tokens},
                     "stop_reason": "end_turn"}},
    ]


def _switched_claude_objs():
    # Most tokens on Opus, then a switch to Sonnet for the last two requests.
    objs = []
    for minute in range(8):
        objs += _claude_turn(minute, OPUS, 2000)
    for minute in range(8, 10):
        objs += _claude_turn(minute, SONNET, 200)
    return objs


class LatestModelHelperTests(unittest.TestCase):
    def test_latest_request_wins_regardless_of_volume(self):
        self.assertEqual(latest_model([(1, OPUS), (2, OPUS), (3, SONNET)]), SONNET)

    def test_order_is_by_timestamp_not_position(self):
        self.assertEqual(latest_model([(5, OPUS), (3, SONNET)]), OPUS)

    def test_equal_timestamps_keep_the_later_pair(self):
        self.assertEqual(latest_model([(4, OPUS), (4, SONNET)]), SONNET)

    def test_unknown_and_empty_names_fall_back(self):
        self.assertEqual(latest_model([(1, OPUS), (2, "unknown-model"), (3, "")]), OPUS)
        self.assertEqual(latest_model([(1, None), (2, "unknown")], "fallback"), "fallback")
        self.assertIsNone(latest_model([]))


class ClaudeModelSwitchTests(unittest.TestCase):
    def source(self, path="/tmp/session.jsonl"):
        return {"id": "session", "path": path, "session": os.path.basename(path),
                "provider": "claude", "client": "claude", "label": "Claude",
                "project": "/repo", "mtime": 1, "title": "Switch", "model": None}

    def test_summary_row_reports_the_model_in_use_now(self):
        row = meter.claude_summary(self.source(), _switched_claude_objs())
        self.assertEqual(row["latest_model"], SONNET)
        # Claude summary rows list short names; the dashboard matches both forms.
        self.assertEqual(sorted(row["models"]), ["opus-4-6", "sonnet-4-6"])

    def test_session_state_reports_latest_and_keeps_dominant_primary(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "session.jsonl")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("\n".join(json.dumps(obj) for obj in _switched_claude_objs()))
            state = meter.recompute_claude(self.source(path))
        self.assertEqual(state["latest_model"], SONNET)
        # primary_model stays the dominant model: cache pricing and Work use it.
        self.assertEqual(state["primary_model"], OPUS)
        self.assertEqual({row["model"] for row in state["analyses"]["model_mix"]}, {OPUS, SONNET})

    def test_subagent_requests_are_not_a_model_switch(self):
        # A Haiku subagent running after the main thread must not read as a switch.
        with tempfile.TemporaryDirectory() as folder:
            main = os.path.join(folder, "session.jsonl")
            subagents = os.path.join(folder, "session", "subagents")
            os.makedirs(subagents)
            child = os.path.join(subagents, "agent-a.jsonl")
            main_objs, child_objs = [], []
            for minute in range(4):
                main_objs += _claude_turn(minute, OPUS, 500)
            for minute in range(5, 7):
                child_objs += _claude_turn(minute, "claude-haiku-4-5", 100)
            for path, objs in ((main, main_objs), (child, child_objs)):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("\n".join(json.dumps(obj) for obj in objs))
            source = dict(self.source(main), _trace_paths=[main, child])
            row = meter.claude_summary(source, None)
            state = meter.recompute_claude(source)
        self.assertEqual(row["latest_model"], OPUS)
        self.assertEqual(state["latest_model"], OPUS)

    def test_single_model_session_latest_equals_primary(self):
        objs = []
        for minute in range(3):
            objs += _claude_turn(minute, SONNET, 100)
        row = meter.claude_summary(self.source(), objs)
        self.assertEqual(row["latest_model"], row["primary_model"])


class PiModelSwitchTests(unittest.TestCase):
    def test_pi_session_reports_the_latest_model(self):
        def turn(index, model, output):
            stamp = f"2026-09-04T10:{index:02d}"
            return [
                {"type": "model_change", "id": f"change-{index}", "parentId": None,
                 "timestamp": f"{stamp}:01Z", "provider": "anthropic", "modelId": model},
                {"type": "message", "id": f"user-{index}", "parentId": f"change-{index}",
                 "timestamp": f"{stamp}:02Z", "message": {"role": "user", "content": []}},
                {"type": "message", "id": f"assistant-{index}", "parentId": f"user-{index}",
                 "timestamp": f"{stamp}:05Z", "message": {
                     "role": "assistant", "model": model, "provider": "anthropic",
                     "content": [], "stopReason": "stop",
                     "usage": {"input": 100, "output": output, "cacheRead": 0,
                               "cacheWrite": 0, "totalTokens": 100 + output}}},
            ]
        rows = [{"type": "session", "version": 1, "id": "pi-switch",
                 "timestamp": "2026-09-04T10:00:00Z", "cwd": "/repo"}]
        for index in range(3):
            rows += turn(index, "opus-x", 2000)
        rows += turn(3, "sonnet-y", 50)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "agent"
            directory = root / "sessions" / "--repo--"
            directory.mkdir(parents=True)
            (directory / "pi-switch.jsonl").write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n")
            with mock.patch.object(meter, "PI_AGENT_DIR", str(root)), \
                    mock.patch.object(meter, "_pi_native_adapters", {}), \
                    mock.patch.object(meter, "_RUNTIME_REGISTRY", None):
                source = meter.pi_session_sources()[0]
                state = meter.recompute(source)
                summary = meter.pi_summary(source)
        self.assertEqual(summary["primary_model"], "opus-x")
        self.assertEqual(summary["latest_model"], "sonnet-y")
        self.assertEqual(state["primary_model"], "opus-x")
        self.assertEqual(state["latest_model"], "sonnet-y")


class CurrentSessionCardTests(unittest.TestCase):
    def row(self, now, **extra):
        return {"id": "s1", "provider": "claude", "label": "Claude", "mtime": now - 5,
                "last_activity_ts": now - 5, "primary_model": OPUS,
                "models": [OPUS, SONNET], **extra}

    def test_card_carries_latest_model(self):
        now = 1_800_000_000.0
        card = meter.current_session_summaries([self.row(now, latest_model=SONNET)], now=now)[0]
        self.assertEqual(card["latest_model"], SONNET)
        self.assertEqual(card["primary_model"], OPUS)

    def test_card_counts_every_model_beyond_the_capped_list(self):
        now = 1_800_000_000.0
        models = [f"model-{index}" for index in range(6)]
        card = meter.current_session_summaries(
            [self.row(now, latest_model="model-5", models=models)], now=now)[0]
        self.assertEqual(len(card["models"]), 4)
        self.assertEqual(card["model_count"], 6)

    def test_card_count_ignores_unknown_model_names(self):
        now = 1_800_000_000.0
        card = meter.current_session_summaries(
            [self.row(now, latest_model=OPUS, models=["unknown-model", "opus-4-6"])], now=now)[0]
        self.assertEqual(card["model_count"], 1)

    def test_cached_row_without_latest_falls_back_to_primary(self):
        now = 1_800_000_000.0
        card = meter.current_session_summaries([self.row(now)], now=now)[0]
        self.assertEqual(card["latest_model"], OPUS)


class DashboardModelSwitchTests(DashboardLayoutCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_model_summary_leads_with_latest_and_counts_others(self):
        functions = []
        for name in ("sessionModelSummary", "currentSessionModelName"):
            match = re.search(rf"^function {name}\(.*?^\}}\n", self.page, re.MULTILINE | re.DOTALL)
            self.assertIsNotNone(match, name)
            functions.append(match.group(0))
        script = "".join(functions) + """
console.log(JSON.stringify([
 sessionModelSummary('claude-sonnet-4-6',['claude-opus-4-6','claude-sonnet-4-6','unknown-model']),
 sessionModelSummary('claude-sonnet-4-6',['claude-sonnet-4-6']),
 sessionModelSummary('',[]),
 sessionModelSummary('claude-sonnet-4-6',['opus-4-6','sonnet-4-6']),
 sessionModelSummary('model-5',['model-0','model-1','model-2','model-3'],6),
 sessionModelSummary('claude-opus-4-6',['opus-4-6'],1),
 sessionModelSummary('model-9',[],3),
]));
"""
        switched, single, empty, short, capped, unknown_dropped, list_missing = json.loads(subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        ).stdout)
        self.assertEqual(switched["latest"], "claude-sonnet-4-6")
        self.assertEqual(switched["others"], ["claude-opus-4-6"])
        self.assertEqual(switched["title"], "Now Claude Sonnet 4.6; also used Claude Opus 4.6")
        self.assertEqual(single["others"], [])
        self.assertEqual(single["title"], "Claude Sonnet 4.6")
        self.assertEqual(empty["title"], "")
        self.assertEqual(empty["latest"], "")
        self.assertEqual(short["others"], ["opus-4-6"])
        self.assertEqual(short["extra"], 1)
        self.assertEqual(short["title"], "Now Claude Sonnet 4.6; also used Claude Opus 4.6")
        self.assertEqual(capped["extra"], 5)
        self.assertIn("Model 3 and 1 other model", capped["title"])
        self.assertEqual(unknown_dropped["extra"], 0)
        self.assertEqual(list_missing["title"], "Now Model 9; also used 2 other models")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_model_names_drop_provider_prefixes(self):
        match = re.search(r"^function currentSessionModelName\(.*?^\}\n", self.page,
                          re.MULTILINE | re.DOTALL)
        script = match.group(0) + """
console.log(JSON.stringify(['claude-opus-5-5','anthropic/claude-sonnet-4-5',
 'us.anthropic.claude-opus-4-6-v1:0','claude-sonnet-4-5-20250929','gpt-5.1-codex','','unknown-model','sonnet-5-5']
 .map(currentSessionModelName)));
"""
        names = json.loads(subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        ).stdout)
        self.assertEqual(names, ["Claude Opus 5.5", "Claude Sonnet 4.5", "Claude Opus 4.6",
                                 "Claude Sonnet 4.5", "GPT 5.1 Codex", "Unknown model",
                                 "Unknown model", "Claude Sonnet 5.5"])

    def test_surfaces_prefer_latest_model(self):
        self.assertIn(
            "sessionModelSummary(row.latest_model||row.primary_model,row.models,row.model_count)",
            self.page)
        # An empty model leaves the hint blank rather than saying "Unknown model".
        self.assertIn("((s.latest_model||s.primary_model)?' · '+currentSessionModelName(s.latest_model||s.primary_model):'')", self.page)
        self.assertIn("$('ov-model').textContent=(s.latest_model||s.primary_model)?currentSessionModelName(", self.page)
        self.assertIn("sessionModelSummary(s.latest_model||s.primary_model,", self.page)
        # The model has its own card row so it never truncates behind the runtime label.
        self.assertIn("<span class=currentSessionModel>${esc(model)}", self.page)
        self.assertIn("<small>+${modelSummary.extra} more</small>", self.page)
        self.assertIn(".currentSessionModel{display:block;", self.page)
        self.assertIn("currentSessionModelName(modelSummary.latest)+(modelSummary.extra?", self.page)
        # Cards carry no native tooltips; the accessible description always names the model.
        self.assertIn("${modelSummary.title?` Model: ${modelSummary.title}.`:''}", self.page)
        self.assertIn(".currentSessionRuntime span:not(.currentSessionProject){flex:0 0 auto;max-width:60%}", self.page)


if __name__ == "__main__":
    unittest.main()
