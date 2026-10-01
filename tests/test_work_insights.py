import http.client
import http.server
import json
import os
import tempfile
import threading
import unittest
from unittest import mock

import meter
from token_meter.domain import work as domain
from token_meter.services import work_insights as W


SECRET_TEXT = "please refactor the zebra-kumquat billing module"


def letter_for(prompt, key):
    import re
    match = re.search(rf"^([A-Z]): {re.escape(key)}:", prompt, re.M)
    return match.group(1) if match else key


def jet_response(token, logprob=-0.05, others=()):
    top = [{"token": token, "logprob": logprob}] + [{"token": t, "logprob": lp} for t, lp in others]
    return {"message": {"content": token}, "logprobs": [{"token": token, "logprob": logprob, "top_logprobs": top}]}


class FakeClient:
    """Scripted stand-in for OllamaClient; records prompts it receives."""

    script = []
    responder = None
    prompts = []
    unloads = 0
    digest_error = None

    def __init__(self, url, model):
        self.url, self.model = url, model

    def model_digest(self):
        if FakeClient.digest_error:
            raise FakeClient.digest_error
        return "digest-1"

    def classify(self, prompt, timeout):
        FakeClient.prompts.append(prompt)
        if FakeClient.responder is not None:
            action = FakeClient.responder(prompt)
        else:
            action = FakeClient.script.pop(0) if FakeClient.script else jet_response("A")
        if isinstance(action, Exception):
            raise action
        return action

    def unload(self):
        FakeClient.unloads += 1


class Clock:
    def __init__(self, now=1_800_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def make_service(tmp, settings=None, **kwargs):
    FakeClient.script, FakeClient.prompts, FakeClient.unloads, FakeClient.digest_error = [], [], 0, None
    FakeClient.responder = None
    # Service tests drive a fake clock; pin the old pace so pacing never waits on it unless a test asks.
    values = W.normalize_settings({"enabled": True, "backfill_days": 0, "pause_on_battery": True,
                                   "rate_per_minute": 20, **(settings or {})})
    clock = kwargs.pop("clock", Clock())

    def sleep(seconds):
        clock.now += seconds

    service = W.WorkInsightsService(
        os.path.join(tmp, "work.sqlite3"), lambda: values, client_factory=FakeClient,
        clock=clock, monotonic=clock, sleep=sleep,
        load_probe=kwargs.pop("load_probe", lambda: 0.1),
        power_probe=kwargs.pop("power_probe", lambda: "ac"), **kwargs)
    return service, values, clock


def turns(*texts, context="Done: I changed the chart."):
    return [{"ts": 1_799_999_000.0 + i, "text": t, "model": "m", "context": context if i else ""}
            for i, t in enumerate(texts)]


def drain(service, limit=200):
    clock = service.clock
    for _ in range(limit):
        wait = service.step()
        if not service.queue and wait > 0:
            break
        if hasattr(clock, "now"):
            clock.now += max(wait, 0.0) + 0.01


class TextPreparationTests(unittest.TestCase):
    def test_strips_runtime_wrappers_and_citations(self):
        raw = ("# Files mentioned by the user:\n## a.png: /x.png\n## My request: fix the chart "
               "<image name=[Image #1] path=\"/x.png\"></image>")
        self.assertEqual(W.prepare_text(raw), "fix the chart")
        self.assertEqual(W.clean_text("ok <oai-mem-citation>MEMORY.md:1</oai-mem-citation>"), "ok")

    def test_wrappers_greetings_and_attachment_only_turns(self):
        wrapped = "# In app browser:\n- tab one\n\n## My request for Codex:\nmake the header sticky"
        self.assertEqual(W.prepare_text(wrapped), "make the header sticky")
        self.assertEqual(W.prepare_text("# Files pasted by the user:\n## a.png: /x\n## My request:\nfix it now"), "fix it now")
        self.assertEqual(W.prepare_text("[Image #1]"), "")
        self.assertEqual(W.prepare_text("This session is being continued from a previous conversation that ran out"), "")
        self.assertFalse(W.is_substantive("hi"))
        self.assertTrue(W.is_substantive("add a dark mode"))

    def test_session_is_labeled_from_the_first_substantive_turn(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        service, values, _ = make_service(tmp.name)
        service.observe("s1", turns("hi", "please add a dark mode toggle", "looks wrong"))
        by_key = {item.turn_key: item.questions for item in service.queue}
        self.assertEqual(by_key[service._turn_key("s1", 1)], W.SESSION_QUESTIONS)
        self.assertEqual(by_key[service._turn_key("s1", 2)], W.TURN_QUESTIONS)
        self.assertNotIn(service._turn_key("s1", 0), by_key)

    def test_current_prompt_labels_win_and_stale_ones_are_relabeled(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        service, values, clock = make_service(tmp.name)
        key = service.session_key("s1")
        old_opener, new_opener = service._turn_key("s1", 0), service._turn_key("s1", 1)
        service.ledger.record_label(old_opener, "work_type", key, "ops", 0.9, "", "d", clock.now)
        service.ledger.record_label(new_opener, "correction", key, "True", 0.9, "", "d", clock.now)
        service._labeled, service._failures = service.ledger.labeled_keys()
        service.labels_version += 1
        self.assertEqual(service.snapshot()[key]["work_type"], "ops")
        added = service.observe("s1", turns("hi", "please add a dark mode toggle"))
        self.assertEqual(added, 1)
        service.ledger.record_label(new_opener, "work_type", key, "feature", 0.9, W.PROMPT_VERSION, "d", clock.now)
        service.labels_version += 1
        entry = service.snapshot()[key]
        self.assertEqual(entry["work_type"], "feature")
        self.assertNotIn("corrections", entry)

    def test_work_type_uses_a_lower_unclear_cutoff_than_area(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        service, values, clock = make_service(tmp.name)
        key = service.session_key("s1")
        tags = W.question_tags(values)
        turn = service._turn_key("s1", 0)
        service.ledger.record_label(turn, "work_type", key, "debug", 0.4, tags["work_type"], "d", clock.now)
        service.ledger.record_label(turn, "area", key, "Personal", 0.4, tags["area"], "d", clock.now)
        service.labels_version += 1
        entry = service.snapshot()[key]
        self.assertEqual((entry["work_type"], entry["area"]), ("debug", "Unclear"))

    def test_skips_injected_messages(self):
        for text in ("<task-notification>done</task-notification>", "# AGENTS.md instructions",
                     "<environment_context>cwd</environment_context>"):
            self.assertEqual(W.prepare_text(text), "")

    def test_skeleton_bounds_long_text_and_keeps_ends(self):
        text = "GOAL: build the report\n" + "\n".join(f"- item {i}" for i in range(400)) + \
               "\n" + "\n".join(f"    at frame{i} (file.py:{i})" for i in range(300)) + "\nFINAL ASK: ship it"
        out = W.skeleton(text)
        self.assertLessEqual(len(out), W.MAX_ITEM_CHARS)
        self.assertTrue(out.startswith("GOAL: build the report"))
        self.assertTrue(out.endswith("FINAL ASK: ship it"))
        self.assertIn("lines omitted", out)
        self.assertEqual(W.skeleton("short"), "short")


class SettingsValidationTests(unittest.TestCase):
    def test_ollama_url_must_be_loopback_http(self):
        self.assertEqual(W.validate_ollama_url("http://127.0.0.1:11434/"), "http://127.0.0.1:11434")
        self.assertEqual(W.validate_ollama_url("http://localhost"), "http://127.0.0.1:11434")
        self.assertEqual(W.validate_ollama_url("http://[::1]:9000"), "http://[::1]:9000")
        for bad in ("https://127.0.0.1:11434", "http://10.0.0.5:11434", "http://example.com",
                    "http://user:pw@127.0.0.1", "http://127.0.0.1/api", "file:///tmp/x"):
            with self.assertRaises(ValueError):
                W.validate_ollama_url(bad)

    def test_normalize_settings_keeps_defaults_for_invalid_fields(self):
        settings = W.normalize_settings({"enabled": "yes", "rate_per_minute": 999,
                                         "ollama_url": "http://evil.example", "areas": [{"name": "x"}]})
        self.assertEqual(settings, W.default_settings())

    def test_default_pace_is_gentle_and_stored_paces_are_kept(self):
        self.assertEqual(W.default_settings()["rate_per_minute"], 5)
        self.assertIn(5, W.RATE_CHOICES)
        for stored in (5, 20, 60):
            self.assertEqual(W.normalize_settings({"rate_per_minute": stored})["rate_per_minute"], stored)
        self.assertEqual(W.normalize_settings({"rate_per_minute": 7})["rate_per_minute"], 5)

    def test_areas_bounds_and_reserved_names(self):
        with self.assertRaises(ValueError):
            W.normalize_areas([{"name": "One", "description": "d"}])
        with self.assertRaises(ValueError):
            W.normalize_areas([{"name": "Unclear", "description": "d"}, {"name": "B", "description": "d"}])
        with self.assertRaises(ValueError):
            W.normalize_areas([{"name": "A", "description": "d"}, {"name": "a", "description": "d"}])
        self.assertEqual(len(W.normalize_areas(list(W.DEFAULT_AREAS))), len(W.DEFAULT_AREAS))


class PromptAndReadoutTests(unittest.TestCase):
    def test_choice_prompt_uses_jet_format(self):
        prompt, labels, keys = W.render_prompt("User's message:\nhi", W.question_for("work_type", W.default_settings()))
        self.assertTrue(prompt.startswith("<state>\nUser's message:\nhi\n</state>"))
        self.assertIn("A: debug:", prompt)
        self.assertEqual(keys[0], "debug")
        self.assertEqual(labels[:2], ["A", "B"])

    def test_readout_softmaxes_label_tokens_only(self):
        _, labels, keys = W.render_prompt("s", W.question_for("work_type", W.default_settings()))
        distribution = W.read_distribution(
            jet_response("A", -0.1, [("B", -2.5), ("Hello", -0.01)]), labels, keys, 1.0)
        self.assertEqual(set(distribution), {"debug", "feature"})
        self.assertGreater(distribution["debug"], 0.8)

    def test_choice_answers_average_both_option_orders(self):
        question = W.question_for("work_type", W.default_settings())
        forward, backward = W.render_prompt("s", question), W.render_prompt("s", question, reverse=True)
        self.assertEqual(backward[2][0], "other")
        value, confidence = W.read_answer(
            [jet_response("A", -0.1, [("B", -1.0)]), jet_response(letter_for(backward[0], "feature"), -0.1,
                                                                     [(letter_for(backward[0], "debug"), -3.0)])],
            question, [(forward[1], forward[2]), (backward[1], backward[2])])
        self.assertEqual(value, "feature")
        self.assertGreater(confidence, 0.55)
        self.assertLess(confidence, 0.7)

    def test_noul_and_score_readouts(self):
        question = W.question_for("correction", W.default_settings())
        _, labels, keys = W.render_prompt("s", question)
        self.assertEqual(W.read_answer([jet_response("Yes", -0.2, [("no", -3)])], question, [(labels, keys)])[0], True)
        question = W.question_for("complexity", W.default_settings())
        _, labels, keys = W.render_prompt("s", question)
        self.assertEqual(W.read_answer([jet_response("2")], question, [(labels, keys)])[0], 2)
        level, _ = W.read_answer([jet_response("0", -1.2, [("3", -0.4)])], question, [(labels, keys)])
        self.assertEqual(level, 2)

    def test_missing_label_token_is_an_item_error(self):
        with self.assertRaises(W.ClassifierError) as caught:
            W.read_distribution(jet_response("Sure"), ["A", "B"], ["x", "y"], 1.0)
        self.assertEqual(caught.exception.kind, "item")


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_disabled_service_queues_nothing(self):
        service, values, _ = make_service(self.tmp.name, {"enabled": False})
        self.assertEqual(service.observe("s1", turns("hello")), 0)
        self.assertEqual(service.step(), 5.0)
        self.assertEqual(service.status()["state"], W.STATE_DISABLED)

    def test_labels_are_stored_without_text(self):
        service, _, _ = make_service(self.tmp.name)
        service.observe("s1", turns(SECRET_TEXT, "no that's wrong, still broken"))
        answers = {"What kind of work": "refactor", "Which area": "Product engineering", "Scale": "1",
                   "previous work was wrong": "yes"}

        def respond(prompt):
            key = next(v for k, v in answers.items() if k in prompt)
            return jet_response(letter_for(prompt, key))

        FakeClient.responder = respond
        drain(service)
        turn_prompt = next(p for p in FakeClient.prompts if "previous work was wrong" in p)
        snapshot = service.snapshot()[service.session_key("s1")]
        self.assertEqual(snapshot["work_type"], "refactor")
        self.assertEqual(snapshot["area"], "Product engineering")
        self.assertEqual(snapshot["complexity"], "everyday")
        self.assertEqual(snapshot["corrections"], 1)
        self.assertIn("Assistant's previous message (end):\nDone: I changed the chart.", turn_prompt)
        with open(os.path.join(self.tmp.name, "work.sqlite3"), "rb") as handle:
            blob = handle.read()
        self.assertNotIn(b"zebra-kumquat", blob)
        self.assertNotIn(b"still broken", blob)
        self.assertNotIn(b"I changed the chart", blob)
        self.assertNotIn(b"s1", blob.replace(b"s1_", b""))
        self.assertEqual(service.observe("s1", turns(SECRET_TEXT, "no that's wrong, still broken")), 0)

    def test_model_missing_enters_setup_state_and_probes_later(self):
        service, _, clock = make_service(self.tmp.name)
        service.observe("s1", turns("hello"))
        FakeClient.digest_error = W.ClassifierError("setup", "model_missing")
        self.assertEqual(service.step(), W.SETUP_PROBE_S)
        self.assertEqual((service.state, service.reason), (W.STATE_SETUP, "model_missing"))
        self.assertLess(service.step(), W.SETUP_PROBE_S)
        FakeClient.digest_error = None
        clock.now += W.SETUP_PROBE_S + 1
        drain(service)
        self.assertEqual(len(service.queue), 0)

    def test_transport_failures_back_off_exponentially_and_keep_items(self):
        service, _, clock = make_service(self.tmp.name)
        service.observe("s1", turns("hello"))
        delays = []
        for _ in range(3):
            FakeClient.script = [W.ClassifierError("transport", "unreachable")]
            with mock.patch.object(W.random, "uniform", return_value=1.0):
                delays.append(service.step())
            clock.now += delays[-1] + 0.01
        self.assertEqual(delays, [5, 10, 20])
        self.assertEqual(service.state, W.STATE_BACKOFF)
        self.assertEqual(len(service.queue), 1)

    def test_item_failures_retry_then_become_terminal_unclear(self):
        service, _, clock = make_service(self.tmp.name, {"areas": list(W.DEFAULT_AREAS)[:2]})
        for attempt in range(W.MAX_ITEM_ATTEMPTS):
            service.observe("s1", [{"ts": clock.now, "text": "hello", "model": "m"}])
            FakeClient.script = [jet_response("Sure")] * 3
            service.pacer.tokens = 1.0
            service.pacer.last = -10
            service.step()
            clock.now += W.ITEM_RETRY_DELAYS_S[-1] + 1
        entry = service.snapshot()[service.session_key("s1")]
        self.assertEqual(entry["work_type"], "unclear")
        self.assertEqual(entry["area"], "Unclear")
        self.assertEqual(service.observe("s1", [{"ts": clock.now, "text": "hello", "model": "m"}]), 0)

    def test_manual_pause_unloads_and_stops_work(self):
        service, values, clock = make_service(self.tmp.name)
        service.observe("s1", turns("hello"))
        service.step()
        values["paused_until"] = "indefinite"
        service.observe("s2", turns("more work"))
        self.assertEqual(service.step(), 5.0)
        self.assertEqual(service.status()["state"], W.STATE_PAUSED)
        self.assertEqual(FakeClient.unloads, 1)
        calls = len(FakeClient.prompts)
        service.step()
        self.assertEqual(len(FakeClient.prompts), calls)
        values["paused_until"] = None
        drain(service)
        self.assertGreater(len(FakeClient.prompts), calls)

    def test_throttles_on_battery_and_load(self):
        power = {"value": "battery"}
        service, values, _ = make_service(self.tmp.name, power_probe=lambda: power["value"])
        service.observe("s1", turns("hello"))
        self.assertEqual(service.step(), W.THROTTLE_WAIT_S)
        self.assertEqual((service.state, service.reason), (W.STATE_THROTTLED, "on_battery"))
        values["pause_on_battery"] = False
        service.load_probe = lambda: 0.95
        service.retry_at = 0
        service.step()
        self.assertEqual(service.reason, "system_busy")

    def test_rate_limit_spaces_every_request(self):
        service, values, clock = make_service(self.tmp.name, {"rate_per_minute": 10})
        stamps = []
        FakeClient.responder = lambda prompt: stamps.append(clock.now) or jet_response("A")
        service.observe("s1", turns("please fix the chart"))
        self.assertEqual(service.step(), 0.0)
        self.assertEqual(len(stamps), 5)
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        self.assertTrue(all(gap >= 6.0 - 1e-6 for gap in gaps), gaps)

    def test_pause_mid_item_stops_before_the_next_request(self):
        service, values, clock = make_service(self.tmp.name)
        calls = []

        def respond(prompt):
            calls.append(prompt)
            values["paused_until"] = "indefinite"
            return jet_response("A")

        FakeClient.responder = respond
        service.observe("s1", turns("hello"))
        service.step()
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(service.queue), 1)

    def test_clear_during_a_request_writes_nothing_afterwards(self):
        service, values, clock = make_service(self.tmp.name)
        FakeClient.responder = lambda prompt: service.clear() or jet_response("A")
        service.observe("s1", turns("hello"))
        service.step()
        self.assertEqual(service.snapshot(), {})
        self.assertEqual(service.status()["labels"], 0)

    def test_disable_clears_queued_text_immediately(self):
        service, values, _ = make_service(self.tmp.name)
        service.observe("s1", turns("hello", "more"))
        self.assertTrue(service.queue)
        values["enabled"] = False
        service.settings_changed()
        self.assertEqual(service.queue, [])

    def test_model_digest_change_is_detected(self):
        service, values, clock = make_service(self.tmp.name)
        service.observe("s1", turns("hello"))
        drain(service)
        version = service.labels_version
        with mock.patch.object(FakeClient, "model_digest", lambda self: "digest-2"):
            service.observe("s2", turns("next request"))
            service.step()
        self.assertEqual(service.model_digest, "digest-2")
        self.assertGreater(service.labels_version, version)

    def test_failed_items_schedule_a_content_free_retry(self):
        service, values, clock = make_service(self.tmp.name)
        FakeClient.responder = lambda prompt: jet_response("Sure")
        service.observe("s1", turns("hello"))
        service.step()
        key = service.session_key("s1")
        self.assertEqual(service.ledger.next_backlog(5, clock.now), [])
        self.assertEqual(service.ledger.next_backlog(5, clock.now + W.ITEM_RETRY_DELAYS_S[0] + 1), [key])

    def test_overflow_is_credited_to_the_session_it_came_from(self):
        service, values, clock = make_service(self.tmp.name)
        with mock.patch.object(W, "QUEUE_LIMIT", 2):
            service.observe("old", [{"ts": clock.now - 5000 + i, "text": f"please do old task {i}", "model": "m"} for i in range(2)])
            service.observe("live", [{"ts": clock.now - 10 + i, "text": f"please do live task {i}", "model": "m"} for i in range(2)])
        with service.ledger._connect() as connection:
            rows = dict(connection.execute("SELECT session_key, pending FROM work_backlog").fetchall())
        self.assertEqual(rows, {service.session_key("old"): 2})

    def test_backlog_rows_that_yield_nothing_are_dropped_after_refill(self):
        service, values, clock = make_service(self.tmp.name, refill=lambda keys: None)
        service.ledger.upsert_backlog(service.session_key("gone"), clock.now, 3, clock.now)
        service.step()
        self.assertEqual(service.ledger.backlog_pending(), 0)

    def test_pacing_wait_does_not_spin_when_wake_is_set(self):
        clock = Clock()
        calls = []
        service = None

        def event_sleep(seconds):
            # Mirror Event.wait: a set event returns immediately without time passing.
            calls.append(seconds)
            if service.wake.is_set():
                return
            clock.now += seconds

        FakeClient.script, FakeClient.prompts, FakeClient.responder, FakeClient.digest_error = [], [], None, None
        values = W.normalize_settings({"enabled": True, "backfill_days": 0, "rate_per_minute": 10})
        service = W.WorkInsightsService(os.path.join(self.tmp.name, "spin.sqlite3"), lambda: values,
                                        client_factory=FakeClient, clock=clock, monotonic=clock,
                                        sleep=event_sleep, load_probe=lambda: 0.1)
        service.observe("s1", turns("hello"))
        service.wake.set()
        service.step()
        self.assertEqual(len(FakeClient.prompts), 5)
        self.assertLess(len(calls), 12)

    def test_multiple_failures_in_one_session_all_keep_a_retry(self):
        service, values, clock = make_service(self.tmp.name)
        FakeClient.responder = lambda prompt: jet_response("Sure")
        session = [{"ts": clock.now - 100 + i, "text": f"turn {i}", "model": "m", "context": "done"} for i in range(3)]
        service.observe("s1", session)
        for _ in range(3):
            service.step()
            clock.now += 30
        key = service.session_key("s1")
        service.observe("s1", session)
        self.assertEqual(service.ledger.next_backlog(5, clock.now + 3_600), [key])
        clock.now += W.ITEM_RETRY_DELAYS_S[0] + 1
        FakeClient.responder = lambda prompt: jet_response(letter_for(prompt, "debug")) \
            if "What kind of work" in prompt else jet_response("A") if "Which area" in prompt \
            else jet_response("1") if "Scale" in prompt else jet_response("no")
        drain(service)
        entry = service.snapshot()[key]
        self.assertEqual(entry.get("correction_labels"), 2)

    def test_digest_is_rechecked_when_the_model_changes(self):
        service, values, clock = make_service(self.tmp.name)
        service.observe("s1", turns("hello"))
        drain(service)
        checks = []
        with mock.patch.object(FakeClient, "model_digest", lambda self: checks.append(self.model) or "d"):
            values["model"] = "other-model"
            service.observe("s2", turns("more"))
            service.step()
        self.assertEqual(set(checks), {"other-model"})

    def test_turn_keys_ignore_text(self):
        service, _, _ = make_service(self.tmp.name)
        self.assertEqual(service._turn_key("s1", 2), service._turn_key("s1", 2))
        self.assertNotEqual(service._turn_key("s1", 2), service._turn_key("s1", 3))

    def test_v1_ledger_is_recreated_not_dropped_in_place(self):
        import sqlite3
        path = os.path.join(self.tmp.name, "old.sqlite3")
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE work_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute("INSERT INTO work_metadata VALUES ('salt', 'old-salt-value')")
            connection.execute("PRAGMA user_version = 1")
        ledger = W.LabelLedger(path)
        self.assertNotEqual(ledger.salt, "old-salt-value")
        with open(path, "rb") as handle:
            self.assertNotIn(b"old-salt-value", handle.read())

    def test_locked_ledger_is_never_deleted(self):
        import sqlite3
        path = os.path.join(self.tmp.name, "locked.sqlite3")
        ledger = W.LabelLedger(path)
        salt = ledger.salt
        holder = sqlite3.connect(path, isolation_level=None)
        holder.execute("BEGIN EXCLUSIVE")
        try:
            with mock.patch.object(W.LabelLedger, "_connect",
                                   lambda self: sqlite3.connect(self.path, timeout=0.05)):
                with self.assertRaises(sqlite3.OperationalError):
                    W.LabelLedger(path)
        finally:
            holder.execute("ROLLBACK")
            holder.close()
        self.assertEqual(W.LabelLedger(path).salt, salt)

    def test_observe_after_clear_with_a_stale_generation_queues_nothing(self):
        service, values, _ = make_service(self.tmp.name)
        original = service._turn_key

        def racing_key(row_id, ordinal):
            key = original(row_id, ordinal)
            if service.generation == 0:
                service.clear()
            return key

        service._turn_key = racing_key
        self.assertEqual(service.observe("s1", turns("hello")), 0)
        self.assertEqual(service.queue, [])

    def test_clear_before_intake_reads_generation_discards_old_salt_keys(self):
        service, values, _ = make_service(self.tmp.name)
        original = service.session_key
        state = {"cleared": False}

        def racing_session_key(row_id):
            key = original(row_id)
            if not state["cleared"]:
                state["cleared"] = True
                service.clear()
            return key

        service.session_key = racing_session_key
        self.assertEqual(service.observe("s1", turns("hello")), 0)
        self.assertEqual(service.queue, [])

    def test_setup_state_persists_with_an_empty_queue(self):
        service, values, clock = make_service(self.tmp.name)
        service.observe("s1", turns("hello"))
        FakeClient.digest_error = W.ClassifierError("setup", "model_missing")
        service.step()
        service.queue.clear()
        service.queued.clear()
        clock.now += W.SETUP_PROBE_S + 1
        service.step()
        self.assertEqual((service.state, service.reason), (W.STATE_SETUP, "model_missing"))

    def test_model_repointed_to_cloud_between_requests_sends_nothing_more(self):
        service, values, clock = make_service(self.tmp.name)
        calls = []

        def respond(prompt):
            calls.append(prompt)
            FakeClient.digest_error = W.ClassifierError("setup", "remote_model")
            return jet_response("A")

        FakeClient.responder = respond
        service.observe("s1", turns("hello"))
        service.step()
        self.assertEqual(len(calls), 1)
        self.assertEqual((service.state, service.reason), (W.STATE_SETUP, "remote_model"))

    def test_an_item_with_several_failed_questions_adds_one_retry(self):
        service, values, clock = make_service(self.tmp.name)
        FakeClient.responder = lambda prompt: jet_response("Sure")
        service.observe("s1", turns("hello"))
        service.step()
        self.assertEqual(service.ledger.backlog_pending(), 1)

    def test_failed_clear_raises_retries_the_delete_and_never_restores_labels(self):
        recovered = []
        service, values, clock = make_service(self.tmp.name, recovered=lambda: recovered.append(1))
        service.observe("s1", turns("hello"))
        drain(service)
        old_salt = service.ledger.salt
        self.assertTrue(service.snapshot())
        with mock.patch.object(W.LabelLedger, "remove", side_effect=PermissionError("denied")):
            with self.assertRaises(W.LabelDeleteError):
                service.clear()
            self.assertIsNone(service.ledger)
            clock.now += W.STORAGE_RETRY_S + 1
            service.step()
            self.assertIsNone(service.ledger)
            self.assertEqual(service.reason, "delete_pending")
        service.step()
        self.assertIsNotNone(service.ledger)
        self.assertNotEqual(service.ledger.salt, old_salt)
        self.assertEqual(service.snapshot(), {})
        self.assertEqual(recovered, [1])

    def test_clear_with_a_closed_ledger_still_deletes_the_files(self):
        service, values, _ = make_service(self.tmp.name)
        service.observe("s1", turns("hello"))
        drain(service)
        path = os.path.join(self.tmp.name, "work.sqlite3")
        service.ledger = None
        service.clear()
        self.assertIsNotNone(service.ledger)
        self.assertEqual(service.ledger.labeled_keys(), ({}, {}))
        self.assertEqual(service.snapshot(), {})
        self.assertFalse(os.path.exists(path + ".delete-pending"))

    def test_a_failed_delete_survives_a_restart(self):
        service, values, clock = make_service(self.tmp.name)
        service.observe("s1", turns("hello"))
        drain(service)
        with mock.patch.object(W.LabelLedger, "remove", side_effect=PermissionError("denied")):
            with self.assertRaises(W.LabelDeleteError):
                service.clear()
            restarted = W.WorkInsightsService(os.path.join(self.tmp.name, "work.sqlite3"), lambda: values,
                                              client_factory=FakeClient, clock=clock, monotonic=clock)
            self.assertIsNone(restarted.ledger)
            self.assertEqual(restarted.status()["reason"], "delete_pending")
        restarted = W.WorkInsightsService(os.path.join(self.tmp.name, "work.sqlite3"), lambda: values,
                                          client_factory=FakeClient, clock=clock, monotonic=clock)
        self.assertIsNotNone(restarted.ledger)
        self.assertEqual(restarted.snapshot(), {})

    def test_a_pending_delete_is_retried_while_disabled_and_shown(self):
        service, values, clock = make_service(self.tmp.name)
        with mock.patch.object(W.LabelLedger, "remove", side_effect=PermissionError("denied")):
            with self.assertRaises(W.LabelDeleteError):
                service.clear()
        values["enabled"] = False
        self.assertEqual(service.status()["state"], W.STATE_STORAGE)
        service.step()
        self.assertFalse(service.delete_pending)
        self.assertEqual(service.status()["state"], W.STATE_DISABLED)

    def test_read_only_directory_delete_never_restores_labels(self):
        service, values, clock = make_service(self.tmp.name)
        service.observe("s1", turns("hello"))
        drain(service)
        real_open = open

        def refuse_marker(path, *args, **kwargs):
            if str(path).endswith(".delete-pending"):
                raise PermissionError("read-only")
            return real_open(path, *args, **kwargs)

        with mock.patch("builtins.open", refuse_marker), \
                mock.patch.object(W.LabelLedger, "remove", side_effect=PermissionError("read-only")):
            with self.assertRaises(W.LabelDeleteError):
                service.clear()
            clock.now += W.STORAGE_RETRY_S + 1
            service.step()
            self.assertIsNone(service.ledger)
            self.assertEqual(service.status()["reason"], "delete_pending")
        service.step()
        self.assertIsNotNone(service.ledger)
        self.assertEqual(service.snapshot(), {})

    def test_successful_delete_with_failed_reopen_is_not_reported_as_failed(self):
        service, values, clock = make_service(self.tmp.name)

        class FlakyLedger(W.LabelLedger):
            def __init__(self, path):
                raise sqlite3_error()

        with mock.patch.object(W, "LabelLedger", FlakyLedger):
            service.clear()
        self.assertFalse(service.delete_pending)
        self.assertEqual(service.status()["reason"], "ledger_unavailable")

    def test_relabeling_under_a_new_prompt_does_not_make_sessions_pending(self):
        service, values, clock = make_service(self.tmp.name)
        key = service.session_key("s1")
        for ordinal in (0, 1):
            question = "work_type" if ordinal == 0 else "correction"
            service.ledger.record_label(service._turn_key("s1", ordinal), question, key,
                                        "feature" if ordinal == 0 else "False", 0.9, "", "d", clock.now)
        service._labeled, service._failures = service.ledger.labeled_keys()
        service.labels_version += 1
        self.assertGreater(service.observe("s1", turns("please add a dark mode", "thanks that works")), 0)
        self.assertNotIn(key, service.pending_session_keys())
        service.observe("s2", turns("please add a dark mode", "thanks that works"))
        self.assertIn(service.session_key("s2"), service.pending_session_keys())

    def test_pending_sessions_are_not_classified(self):
        service, values, clock = make_service(self.tmp.name)
        with mock.patch.object(W, "QUEUE_LIMIT", 0):
            service.observe("s1", turns("please fix the chart", "still broken there", "now it works"))
        self.assertIn(service.session_key("s1"), service.pending_session_keys())

    def test_latency_baseline_adapts(self):
        service, _, _ = make_service(self.tmp.name)
        for _ in range(W.LATENCY_WINDOW):
            service._record_latency(1.0)
        self.assertEqual(service.baseline, 1.0)
        for _ in range(200):
            service._record_latency(2.0)
        self.assertGreater(service.baseline, 1.9)

    def test_queue_overflow_goes_to_content_free_backlog_and_refills(self):
        refilled = []
        service, _, clock = make_service(self.tmp.name, refill=lambda keys: refilled.extend(keys) or keys)
        with mock.patch.object(W, "QUEUE_LIMIT", 2), mock.patch.object(W, "QUEUE_LOW_WATER", 1):
            clock.now += 10_000
            service.observe("big", turns("one", "two", "three", "four"))
            self.assertEqual(len(service.queue), 2)
            self.assertEqual(service.ledger.backlog_pending(), 2)
            service.queue.clear()
            service.queued.clear()
            service.step()
        self.assertIn(service.session_key("big"), refilled)

    def test_area_edit_requeues_only_area(self):
        service, values, _ = make_service(self.tmp.name)
        service.observe("s1", turns("hello"))
        drain(service)
        values["areas"] = [{"name": "Client delivery", "description": "customer work"},
                           {"name": "Personal", "description": "personal"}]
        service.settings_changed()
        self.assertEqual(service.snapshot()[service.session_key("s1")].get("area"), None)
        service.observe("s1", turns("hello"))
        self.assertEqual(service.queue[0].questions, ("area",))

    def test_backfill_horizon_skips_old_sessions(self):
        service, _, clock = make_service(self.tmp.name, {"backfill_days": 30})
        old = [{"ts": clock.now - 40 * 86400, "text": "old", "model": "m"}]
        self.assertEqual(service.observe("old", old), 0)

    def test_worker_exceptions_are_supervised(self):
        service, _, _ = make_service(self.tmp.name)
        stop = threading.Event()
        calls = []

        def boom():
            calls.append(1)
            if len(calls) > 2:
                stop.set()
            raise RuntimeError("secret text inside")

        service.step = boom
        service.run_forever(stop)
        self.assertEqual((service.state, service.reason), (W.STATE_BACKOFF, "internal_error"))

    def test_status_eta_uses_measured_item_rate(self):
        service, values, clock = make_service(self.tmp.name)
        self.assertEqual(service.status()["state"], W.STATE_IDLE)
        for index in range(10):
            service._rate_window.append(clock.now - 60 + index * 6)
        service.queue.extend([None] * 30)
        self.assertAlmostEqual(service.status()["eta_s"], int(30 / (9 / 0.9) * 60), delta=2)
        service.queue.clear()

    def test_clear_removes_labels_and_rotates_salt(self):
        service, _, _ = make_service(self.tmp.name)
        service.observe("s1", turns("hello"))
        drain(service)
        salt = service.ledger.salt
        service.clear()
        self.assertEqual(service.snapshot(), {})
        self.assertNotEqual(service.ledger.salt, salt)


class FakeOllama(http.server.BaseHTTPRequestHandler):
    responses = {}

    def log_message(self, *args):
        pass

    def _reply(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        status, body = FakeOllama.responses.get(self.path, (404, {"error": "not found"}))
        self._reply(status, body)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length") or 0))
        status, body = FakeOllama.responses.get(self.path, (404, {"error": "not found"}))
        self._reply(status, body)


class OllamaClientTests(unittest.TestCase):
    def setUp(self):
        self.server = http.server.HTTPServer(("127.0.0.1", 0), FakeOllama)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def test_classify_and_digest(self):
        FakeOllama.responses = {
            "/api/tags": (200, {"models": [{"name": "token-meter-jet:latest", "digest": "abc123"}]}),
            "/api/chat": (200, jet_response("B")),
        }
        client = W.OllamaClient(self.url, "token-meter-jet")
        self.assertEqual(client.model_digest(), "abc123")
        self.assertEqual(client.classify("p", 5)["message"]["content"], "B")

    def test_missing_model_and_server_errors_are_classified(self):
        FakeOllama.responses = {"/api/tags": (200, {"models": []}),
                                "/api/chat": (500, {"error": "boom"})}
        client = W.OllamaClient(self.url, "token-meter-jet")
        with self.assertRaises(W.ClassifierError) as missing:
            client.model_digest()
        self.assertEqual(missing.exception.kind, "setup")
        with self.assertRaises(W.ClassifierError) as failed:
            client.classify("p", 5)
        self.assertEqual(failed.exception.kind, "transport")

    def test_remote_and_cloud_models_are_refused(self):
        for entry in ({"name": "token-meter-jet:latest", "remote_host": "https://ollama.com"},
                      {"name": "token-meter-jet:latest", "remote_model": "gpt-oss:120b"},
                      {"name": "token-meter-jet-cloud"},
                      {"name": "token-meter-jet:cloud"}):
            FakeOllama.responses = {"/api/tags": (200, {"models": [dict(entry, digest="x")]})}
            with self.assertRaises(W.ClassifierError) as caught:
                name = entry["name"].split(":")[0] if entry["name"].endswith(":latest") else entry["name"]
                W.OllamaClient(self.url, name).model_digest()
            self.assertEqual(caught.exception.reason, "remote_model")

    def test_unreachable_is_transport(self):
        self.server.shutdown()
        self.server.server_close()
        self.addCleanup(lambda: None)
        with self.assertRaises(W.ClassifierError) as caught:
            W.OllamaClient(self.url, "m").version()
        self.assertEqual((caught.exception.kind, caught.exception.reason), ("transport", "unreachable"))


def row(row_id, project="alpha", runtime="Codex", cost=2.0, day="2026-09-10", turns_=3, model="gpt-5.6"):
    return {"id": row_id, "project": project, "runtime": runtime, "provider": "codex", "cost": cost,
            "start": f"{day} 10:00", "_day_cost": {day: cost},
            "model_stats": [{"model": model, "cost": cost, "tokens": 100}],
            "_language_signal_events": {"positive": [{"day": day}] * turns_}}


class DomainTests(unittest.TestCase):
    AREAS = [{"name": "Product engineering", "description": "d"}, {"name": "Personal", "description": "d"}]

    def build(self, rows, labels, **kwargs):
        prices = {"gpt-5.6": 10.0, "cheap": 1.0, "mid": 4.0}
        return domain.build_work_insights(rows, labels, lambda ident: ident.split("\0")[0], self.AREAS,
                                          lambda m, p: prices.get(m), today="2026-09-30", **kwargs)

    def test_allocation_measures_and_pending(self):
        rows = [row("a"), row("b", cost=6.0, turns_=1), row("c", day="2026-08-02")]
        labels = {"a": {"area": "Product engineering", "work_type": "debug", "corrections": 1, "correction_labels": 2},
                  "b": {"area": "Unclear"}}
        out = self.build(rows, labels)
        september = next(b for b in out["allocation"] if b["month"] == "2026-09")
        self.assertEqual(september["turns"], {"Product engineering": 3, "Unclear": 1})
        self.assertEqual(september["spend"]["Unclear"], 6.0)
        self.assertTrue(september["partial"])
        august = next(b for b in out["allocation"] if b["month"] == "2026-08")
        self.assertEqual(august["sessions"], {"Pending": 1})
        self.assertEqual(out["areas"][-2:], ["Unclear", "Pending"])

    def test_child_rows_are_excluded_but_parents_with_children_are_kept(self):
        child = row("kid")
        child["_agent_records"] = [{"parent_id": "p1"}]
        parent = row("parent")
        parent["_agent_records"] = [{"id": "root", "parent_id": None}, {"id": "c", "parent_id": "root"}]
        self.assertTrue(domain.is_child_row(child))
        self.assertFalse(domain.is_child_row(parent))
        out = self.build([child, parent], {})
        self.assertEqual(out["coverage"]["sessions"], 1)

    def test_economics_and_few_samples(self):
        rows = [row("a"), row("b", project="beta", turns_=5)]
        labels = {"a": {"area": "Personal", "work_type": "docs", "corrections": 1, "correction_labels": 2},
                  "b": {"area": "Personal", "work_type": "docs", "corrections": 0, "correction_labels": 4}}
        out = self.build(rows, labels)
        self.assertNotIn("workstreams", out)
        docs = out["economics"][0]
        self.assertEqual((docs["work_type"], docs["sessions"], docs["cost_per_session"]), ("docs", 2, 2.0))
        self.assertTrue(docs["rework"]["few_samples"])
        self.assertAlmostEqual(docs["rework"]["rate"], 1 / 6)

    def test_right_sizing_flags_premium_routine(self):
        rows = [row("a", model="gpt-5.6"), row("b", model="cheap"), row("c", model="mid")]
        labels = {"a": {"complexity": "routine"}, "b": {"complexity": "complex"}, "c": {"complexity": "everyday"}}
        out = self.build(rows, labels)
        cells = {(c["complexity"], c["tier"]): c for c in out["right_sizing"]["cells"]}
        self.assertEqual(cells[("routine", "premium")]["flag"], "possible_overspend")
        self.assertEqual(out["opportunities"][0]["kind"], "premium_routine")
        self.assertNotIn("headlines", out)
        self.assertEqual(cells[("complex", "light")]["sessions"], 1)

    def test_filters_and_period(self):
        rows = [row("a"), row("b", runtime="Claude Code", project="beta", day="2025-01-05")]
        out = self.build(rows, {}, months=3, runtime="Codex")
        self.assertEqual(out["months"], ["2026-07", "2026-08", "2026-09"])
        self.assertEqual(out["filters"]["runtimes"], ["Claude Code", "Codex"])
        self.assertEqual(set(out["filters"]["projects"]), {"alpha", "beta"})


class OutcomeInsightTests(unittest.TestCase):
    AREAS = DomainTests.AREAS

    def build(self, rows, labels, sequences, **kwargs):
        prices = {"gpt-5.6": 10.0, "cheap": 1.0, "mid": 4.0}
        return domain.build_work_insights(
            rows, labels, lambda ident: ident.split("\0")[0], self.AREAS, lambda m, p: prices.get(m), today="2026-09-30",
            corrections_for=lambda ident, n: sequences.get(ident.split("\0")[0], []), **kwargs)

    def test_session_outcome_classification(self):
        self.assertEqual(domain.session_outcome(1, []), "single_shot")
        self.assertEqual(domain.session_outcome(3, []), "pending")
        self.assertEqual(domain.session_outcome(3, [(1, False), (2, False)]), "accepted")
        self.assertEqual(domain.session_outcome(3, [(1, True), (2, False)]), "recovered")
        self.assertEqual(domain.session_outcome(3, [(1, False), (2, True)]), "ended_on_pushback")
        self.assertEqual(domain.session_outcome(3, [(1, True), (2, None)]), "ended_on_pushback")
        self.assertEqual(domain.session_outcome(3, [(1, None), (2, None)]), "unclear")
        self.assertEqual(domain.session_outcome(4, [(1, False), (2, False)]), "accepted")
        self.assertEqual(domain.session_outcome(4, [(1, False), (2, False)], pending=True), "pending")

    def test_value_by_work_type_and_position(self):
        rows = [row("a", cost=4.0, turns_=4), row("b", cost=2.0, turns_=3), row("c", cost=1.0, turns_=1)]
        labels = {"a": {"area": "Personal", "work_type": "debug", "corrections": 1, "correction_labels": 3},
                  "b": {"area": "Personal", "work_type": "debug", "corrections": 0, "correction_labels": 2}}
        sequences = {"a": [(1, False), (2, True), (3, False)], "b": [(1, False), (2, False)]}
        out = self.build(rows, labels, sequences)
        debug = next(e for e in out["economics"] if e["work_type"] == "debug")
        self.assertEqual((debug["judged_sessions"], debug["resolved_sessions"], debug["resolved_rate"]), (2, 2, 1.0))
        self.assertEqual(debug["cost_per_resolved"], 3.0)
        self.assertNotIn("rework_by_position", out)
        self.assertNotIn("choices", out)
        self.assertNotIn("outcomes", out)
        self.assertEqual(out["kpis"]["current"]["ended_spend"], 0.0)

    def test_model_fit_marks_best_only_with_enough_samples(self):
        rows = [row(f"x{i}", model="gpt-5.6") for i in range(3)] + [row(f"y{i}", model="mid") for i in range(3)]
        labels = {f"x{i}": {"work_type": "debug", "corrections": 3, "correction_labels": 10} for i in range(3)}
        labels.update({f"y{i}": {"work_type": "debug", "corrections": 1, "correction_labels": 10} for i in range(3)})
        fit = self.build(rows, labels, {})["model_fit"]
        best = [c for c in fit["cells"] if c["best"]]
        self.assertEqual([(c["model"], c["work_type"]) for c in best], [("mid", "debug")])
        few = {f"z{i}": {"work_type": "debug", "corrections": 0, "correction_labels": 2} for i in range(2)}
        fit = self.build([row("z0", model="gpt-5.6"), row("z1", model="mid")], few, {})["model_fit"]
        self.assertFalse(any(c["best"] for c in fit["cells"]))



class OperatingRhythmTests(unittest.TestCase):
    AREAS = DomainTests.AREAS

    def build(self, rows, labels, sequences):
        prices = {"gpt-5.6": 10.0, "cheap": 1.0}
        return domain.build_work_insights(rows, labels, lambda ident: ident.split("\0")[0], self.AREAS, lambda m, p: prices.get(m),
                                          today="2026-09-30", corrections_for=lambda ident, n: sequences.get(ident.split("\0")[0], []))

    def test_ledger_outcomes_and_period_kpis(self):
        rows, labels, sequences = [], {}, {}
        for i in range(8):
            day = "2026-08-10" if i < 4 else "2026-09-10"
            rows.append(row(f"k{i}", day=day, cost=2.0, turns_=3))
            labels[f"k{i}"] = {"correction_labels": 2, "corrections": 1 if i % 2 else 0}
            sequences[f"k{i}"] = [(1, False), (2, bool(i % 2))]
        out = domain.build_work_insights(rows, labels, lambda ident: ident.split("\0")[0], self.AREAS, lambda m, p: None, months=1,
                                         today="2026-09-30", corrections_for=lambda ident, n: sequences[ident.split("\0")[0]])
        september = out["allocation"][-1]
        self.assertEqual(september["outcomes"], {"accepted": 2, "ended_on_pushback": 2})
        self.assertEqual(september["outcome_spend"]["accepted"], 4.0)
        kpis = out["kpis"]
        self.assertEqual(kpis["previous_months"], ["2026-08"])
        self.assertEqual((kpis["current"]["judged_sessions"], kpis["current"]["resolved_rate"]), (4, 0.5))
        self.assertEqual(kpis["previous"]["judged_sessions"], 4)
        self.assertEqual(kpis["current"]["cost_per_resolved"], 2.0)

    def test_effort_grid_flags_high_effort_routine_work(self):
        a, b = row("a", cost=8.0), row("b", cost=1.0)
        a["reasoning_effort"], b["reasoning_effort"] = "xhigh", "low"
        out = self.build([a, b], {"a": {"complexity": "routine"}, "b": {"complexity": "routine"}}, {})
        effort = out["right_sizing"]["effort"]
        self.assertEqual(effort["efforts"], ["low", "xhigh"])
        flagged = [c for c in effort["cells"] if c["flag"]]
        self.assertEqual([(c["complexity"], c["effort"], c["spend"]) for c in flagged], [("routine", "xhigh", 8.0)])
        self.assertEqual(out["opportunities"][0]["kind"], "effort_routine")

    def find(self, rows, labels, filters, sequences=None, **kwargs):
        prices = {"gpt-5.6": 10.0, "cheap": 1.0, "mid": 4.0}
        return domain.find_sessions(rows, labels, lambda ident: ident.split("\0")[0], self.AREAS, lambda m, p: prices.get(m),
                                    filters, corrections_for=lambda ident, n: (sequences or {}).get(ident.split("\0")[0], []),
                                    **kwargs)

    def test_month_and_area_match_the_allocation_cell(self):
        rows = [row("a", cost=5.0), row("b", cost=9.0), row("c", day="2026-08-01")]
        labels = {"a": {"area": "Personal"}, "b": {"area": "Personal"}, "c": {"area": "Personal"}}
        out = self.find(rows, labels, {"month": "2026-09", "area": "Personal"})
        self.assertEqual([s["id"] for s in out["sessions"]], ["b", "a"])
        self.assertEqual(out["spend"], 14.0)
        self.assertEqual(set(out["sessions"][0]), {
            "id", "session", "title", "runtime", "project", "start", "last", "cost", "turns", "model", "area",
            "work_type", "complexity", "outcome", "corrections", "labeled_turns"})

    def test_outcome_model_and_limit_filters(self):
        rows = [row(f"s{i}", cost=float(i), model="mid" if i % 2 else "gpt-5.6", turns_=3) for i in range(6)]
        labels = {f"s{i}": {"work_type": "debug", "correction_labels": 2, "corrections": 1} for i in range(6)}
        sequences = {f"s{i}": [(1, False), (2, True)] if i < 3 else [(1, True), (2, False)] for i in range(6)}
        ended = self.find(rows, labels, {"outcome": "ended_on_pushback"}, sequences)
        self.assertEqual({s["id"] for s in ended["sessions"]}, {"s0", "s1", "s2"})
        mid = self.find(rows, labels, {"model": "mid", "model_runtime": "Codex"}, sequences)
        self.assertEqual({s["id"] for s in mid["sessions"]}, {"s1", "s3", "s5"})
        limited = self.find(rows, labels, {"work_type": "debug"}, sequences, limit=2)
        self.assertEqual((limited["total"], len(limited["sessions"]), limited["truncated"]), (6, 2, True))

    def test_rollouts_sharing_a_session_id_keep_separate_labels(self):
        first, fork = row("x", cost=5.0), row("x", cost=1.0)
        first["path"], fork["path"] = "/t/first.jsonl", "/t/fork.jsonl"
        first["session"], fork["session"] = "first.jsonl", "fork.jsonl"
        labels = {domain.work_identity(first): {"area": "Personal", "work_type": "debug"},
                  domain.work_identity(fork): {"area": "Personal", "work_type": "docs"}}
        out = domain.find_sessions([first, fork], labels, lambda ident: ident, self.AREAS, lambda m, p: None, {})
        self.assertEqual(sorted(s["work_type"] for s in out["sessions"]), ["debug", "docs"])
        ids = domain.find_sessions([first, fork], labels, lambda ident: ident, self.AREAS, lambda m, p: None, {},
                                   ids_only=True)
        self.assertEqual((ids["keys"], ids["total"]), (["first.jsonl", "fork.jsonl"], 2))
        self.assertNotIn("ids", ids)
        self.assertEqual(sorted(s["session"] for s in out["sessions"]), ["first.jsonl", "fork.jsonl"])

    def test_start_month_and_ids_modes(self):
        rows = [row("a", day="2026-08-30"), row("b", day="2026-09-02")]
        rows[0]["_language_signal_events"] = {"positive": [{"day": "2026-08-30"}, {"day": "2026-09-01"}]}
        labels = {"a": {"area": "Personal"}, "b": {"area": "Personal"}}
        active = self.find(rows, labels, {"month": "2026-09"})
        started = self.find(rows, labels, {"start_month": "2026-09"})
        self.assertEqual({s["id"] for s in active["sessions"]}, {"a", "b"})
        self.assertEqual({s["id"] for s in started["sessions"]}, {"b"})
        ids = self.find(rows, labels, {}, ids_only=True)
        self.assertEqual((set(ids["keys"]), ids["total"]), ({"a", "b"}, 2))
        self.assertNotIn("sessions", ids)

    def test_complexity_group_and_tier(self):
        rows = [row("a", model="gpt-5.6"), row("b", model="cheap"), row("c", model="mid")]
        labels = {"a": {"complexity": "high_impact"}, "b": {"complexity": "complex"}, "c": {"complexity": "routine"}}
        out = self.find(rows, labels, {"complexity": "complex", "tier": "premium"})
        self.assertEqual([s["id"] for s in out["sessions"]], ["a"])


class AppContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = os.path.join(self.tmp.name, "settings.json")

    def test_work_insights_are_macos_only(self):
        meter.set_work_insights_settings({"enabled": True}, self.settings)
        with mock.patch.object(meter, "work_insights_supported", return_value=False):
            meter._work_settings_cache["key"] = None
            self.assertFalse(meter.work_insights_settings(self.settings)["enabled"])
            refused = meter.set_work_insights_settings({"enabled": True}, self.settings)
            self.assertEqual(refused, {"ok": False, "error": "Work insights are available on macOS only."})
            self.assertFalse(meter.work_insights_public_settings(meter.work_insights_settings(self.settings))["supported"])
            self.assertEqual(meter.work_insights_state("6")[1], 404)
            self.assertEqual(meter.work_sessions_state({})[1], 404)
        meter._work_settings_cache["key"] = None
        with mock.patch.object(meter, "work_insights_supported", return_value=True):
            self.assertTrue(meter.work_insights_settings(self.settings)["enabled"])

    def test_platform_gate_follows_the_platform_service(self):
        self.assertEqual(meter.work_insights_supported(), meter._PLATFORM_SERVICES.platform_id == "macos")

    def test_page_hides_work_off_macos_and_numbers_it(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "page.html"),
                  encoding="utf-8") as handle:
            page = handle.read()
        for marker in ('id=tab-work data-label=Work aria-label=Work aria-keyshortcuts="Alt+6" title="Work · Shortcut: Option+6" hidden>',
                       '<span class=tabLabel>Work</span><span class=tabShortcut aria-hidden=true>6</span>',
                       'id=work-insights-settings hidden>',
                       "applyWorkSupport(state?.work_insights_supported);",
                       "if(workSupported===false){location.hash='sessions';return;}",
                       "if(command.id==='work'&&workSupported!==true)return false;",
                       "route:'work',directKey:'Digit6',glyph:'6'"):
            self.assertIn(marker, page)

    def test_setup_script_refuses_other_platforms(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts",
                               "setup-work-classifier"), encoding="utf-8") as handle:
            script = handle.read()
        self.assertIn('if [ "$(uname -s)" != "Darwin" ]; then', script)
        self.assertLess(script.index('uname -s'), script.index("command -v curl"))

    def test_right_sizing_explains_each_flag(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "page.html"),
                  encoding="utf-8") as handle:
            page = handle.read()
        self.assertNotIn("ooks mismatched", page)
        for marker in ("possible_overspend:'premium model on routine work'",
                       "possible_false_economy:'light model on complex work'",
                       "possible_overthinking:'high effort on routine work'",
                       "<span>Spend to review</span>", "<th>What to review</th>"):
            self.assertIn(marker, page)

    def test_settings_round_trip_validation_and_pause(self):
        result = meter.set_work_insights_settings({"enabled": True, "rate_per_minute": 40}, self.settings)
        self.assertTrue(result["ok"])
        self.assertTrue(result["requeue"])
        loaded = meter.work_insights_settings(self.settings)
        self.assertEqual((loaded["enabled"], loaded["rate_per_minute"]), (True, 40))
        self.assertFalse(meter.set_work_insights_settings({"ollama_url": "http://10.1.1.1"}, self.settings)["ok"])
        self.assertFalse(meter.set_work_insights_settings({"unknown": 1}, self.settings)["ok"])
        self.assertFalse(meter.set_work_insights_settings({"pause": "forever"}, self.settings)["ok"])
        self.assertTrue(meter.set_work_insights_settings({"pause": "1h"}, self.settings)["ok"])
        self.assertIsInstance(meter.work_insights_settings(self.settings)["paused_until"], float)
        again = meter.set_work_insights_settings({"pause": "resume"}, self.settings)
        self.assertIsNone(again["work_insights"]["paused_until"])
        with open(self.settings, encoding="utf-8") as handle:
            self.assertIn("work_insights", json.load(handle))

    def test_pause_until_tomorrow_is_six_am(self):
        import datetime
        now = datetime.datetime(2026, 9, 30, 22, 15).timestamp()
        until = datetime.datetime.fromtimestamp(meter._pause_until("tomorrow", now))
        self.assertEqual((until.day, until.hour, until.minute), (1, 6, 0))

    def test_work_payload_is_bounded_and_text_free(self):
        rows = (row("a"),)
        with mock.patch.object(meter, "TOKEN_METER_SETTINGS", self.settings), \
                mock.patch.dict(meter._xsess, {"data": {"ok": True}, "internal_rows": rows}):
            payload, status = meter.work_insights_state("6")
            self.assertEqual(status, 200)
            self.assertEqual(meter.work_insights_state("7")[1], 400)
            for choice in ("1d", "7d", "30d"):
                short, short_status = meter.work_insights_state(choice)
                self.assertEqual((short_status, short["insights"]["grain"]), (200, "day"), choice)
            for bad in ("14d", "2d", "6m", "1d; drop"):
                self.assertEqual(meter.work_insights_state(bad)[1], 400, bad)
            self.assertEqual(meter.work_insights_state("6", project="nope")[1], 404)
        encoded = json.dumps(payload)
        self.assertNotIn("_day_cost", encoded)
        self.assertNotIn("_language_signal_events", encoded)
        self.assertEqual(set(payload), {"ok", "settings", "status", "insights"})

    def test_routes_are_registered(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "token_meter", "app.py"), encoding="utf-8") as handle:
            source = handle.read()
        for route in ('"/work"', '"/settings/work-insights"', '"/work-insights/pause"', '"/work-insights/clear"'):
            self.assertTrue(route in source, route)


def sqlite3_error():
    import sqlite3
    return sqlite3.OperationalError("unable to open")


class SurfaceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "page.html"), encoding="utf-8") as handle:
            cls.page = handle.read()
        with open(os.path.join(root, "menubar", "TokenMeterMenuBar.swift"), encoding="utf-8") as handle:
            cls.swift = handle.read()
        cls.root = root

    def test_work_tab_sits_between_efficiency_and_git_with_option_6(self):
        rail = [self.page.index(f"id=tab-{name}") for name in ("efficiency", "work", "git")]
        self.assertEqual(rail, sorted(rail))
        button = self.page[self.page.index("id=tab-work"):].split("</button>", 1)[0]
        self.assertIn('aria-keyshortcuts="Alt+6"', button)
        self.assertIn("{id:'work',label:'Work'", self.page)
        self.assertIn("if(h==='work'){", self.page)

    def test_work_page_shows_estimates_unclear_and_pending(self):
        for marker in ("id=view-work", "<h2>Where the spend went</h2>", "<h3>How sessions ended</h3>",
                       "Pushback over time", ">Cost per resolved session</h2>",
                       "Model choices", "id=w-scorecard", ">Right-sizing</h2>",
                       "id=w-tier-mix", "id=w-effort-mix", "id=w-savings", "Possible saving",
                       "<option value=1d>1 day</option><option value=7d>1 week</option><option value=30d>1 month</option>",
                       "id=w-kpis",
                       "text goes only to Ollama on this machine", "'var(--w-pending)'", "'var(--w-unclear)'"):
            self.assertTrue(marker in self.page, marker)
        for removed in ("Value by kind of work", "Spend in, outcomes out", "id=w-headlines", "Better fit by kind of work",
                        "data-measure=", "id=w-ledger"):
            self.assertNotIn(removed, self.page)

    def test_every_work_element_the_script_uses_exists(self):
        import re as _re
        markup = set(_re.findall(r"\bid=(w-[a-z0-9-]+)", self.page))
        used = set(_re.findall(r"\$\('(w-[a-z0-9-]+)'\)", self.page))
        self.assertTrue(used)
        self.assertEqual(sorted(used - markup), [])

    def test_work_uses_the_dashboard_font(self):
        self.assertIn("#view-work .mono,#view-work .num{font-family:inherit;font-variant-numeric:tabular-nums}", self.page)
        work_css = [line for line in self.page.split("\n") if line.startswith(".work")]
        self.assertFalse([line for line in work_css if "ui-monospace" in line])

    def test_work_palette_uses_validated_product_hues(self):
        for marker in ("--w1:#079bc2;--w2:#c17a01;--w3:#c36b95;--w4:#af851e;--w5:#9979cd;--w6:#d66555;--w7:#5f8adf;--w8:#05a386",
                       "--w-tier-light:#026e8b;--w-tier-standard:#0594ba;--w-tier-premium:#02bceb",
                       "--w-accepted:var(--good);--w-recovered:var(--warn);--w-ended:var(--bad)",
                       ".workLine{fill:none;stroke:var(--spectrum-cyan)",
                       ".workGrid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:10px;align-items:stretch}"):
            self.assertIn(marker, self.page)
        self.assertNotIn("--w1:#3987e5", self.page)

    def test_sizing_and_scorecard_numbers_match_their_labels(self):
        for marker in ("const flagged=insights?.right_sizing?.flagged||{}",
                       "slice(rework.grain==='day'?-31:-26)",
                       "lowestCost=ranked.length>=2?",
                       "!insights?.right_sizing?.tiers_known?'model price tiers are unavailable'",
                       "sessions started in this period",
                       "no routine work on premium models"):
            self.assertIn(marker, self.page)
        self.assertNotIn("rows.reduce((sum,item)=>sum+(item.spend||0),0)", self.page)

    def test_settings_card_explains_what_text_is_read(self):
        self.assertIn("id=work-insights-settings", self.page)
        self.assertIn("reads the prompts you typed, plus the last few lines of the assistant reply", self.page)
        self.assertIn("cloud models are refused", self.page)
        self.assertIn("Token Meter stores labels, never text", self.page)
        self.assertIn("./scripts/setup-work-classifier", self.page)

    def test_work_state_is_declared_before_the_initial_route_runs(self):
        declaration = self.page.index("let WORK=null,workRequest=0")
        self.assertLess(self.page.index("let workSupported=null;"), self.page.index("function applyHashRoute(){"))
        session_filter = self.page.index("let workSessionFilter=null")
        route = self.page.index("function applyHashRoute(){")
        self.assertLess(declaration, route)
        self.assertLess(session_filter, route)
        self.assertIn("if(h.startsWith('work-sessions')||h.startsWith('sessions-all?work=')){", self.page)
        self.assertIn("const workRows=workSessionFilter?all.filter(s=>workSessionFilter.keys.has(sessionRowKey(s))):all;", self.page)
        self.assertIn("if(key==='work'){workSessionFilter=null;workFilterRequest++;}", self.page)
        self.assertNotIn("w-drawer", self.page)

    def test_menu_bar_offers_pause_and_resume(self):
        for marker in ('"Pause work insights"', '"Resume work insights"', '/work-insights/pause"',
                       'dict["work_insights"]', '("Until tomorrow", "tomorrow")'):
            self.assertTrue(marker in self.swift, marker)

    def test_setup_script_is_executable_and_uses_int4_import(self):
        path = os.path.join(self.root, "scripts", "setup-work-classifier")
        self.assertTrue(os.access(path, os.X_OK))
        with open(path, encoding="utf-8") as handle:
            script = handle.read()
        self.assertIn('ollama create "$MODEL_NAME" -q int4', script)
        self.assertIn("file_digest", script)
        self.assertIn('COMMIT="fbc3d2daa679e0d4bd9f99c9912b6496d5a41f0a"', script)
        self.assertNotIn("sudo", script)


class AppIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = os.path.join(self.tmp.name, "settings.json")
        self.db = os.path.join(self.tmp.name, "work.sqlite3")

    def test_disabled_status_shows_a_pending_delete_and_the_watcher_helper_finishes_it(self):
        marker = self.db + ".delete-pending"
        open(marker, "w").close()
        open(self.db, "w").close()
        with mock.patch.object(meter, "TOKEN_METER_SETTINGS", self.settings), \
                mock.patch.object(meter, "TOKEN_METER_WORK_INSIGHTS_DB", self.db), \
                mock.patch.object(meter, "_work_service_instance", None):
            status = meter.work_insights_status()
            self.assertEqual((status["state"], status["reason"]), ("storage_error", "delete_pending"))
            meter.finish_work_insights_delete()
            self.assertEqual(meter.work_insights_status()["state"], "disabled")
        self.assertFalse(os.path.exists(self.db))
        self.assertFalse(os.path.exists(marker))

    def test_disabled_feature_creates_no_ledger(self):
        with mock.patch.object(meter, "TOKEN_METER_SETTINGS", self.settings), \
                mock.patch.object(meter, "TOKEN_METER_WORK_INSIGHTS_DB", self.db), \
                mock.patch.object(meter, "_work_service_instance", None):
            self.assertEqual(meter.work_insights_status()["state"], "disabled")
            meter.clear_work_insights()
            meter.notify_work_insights()
        self.assertFalse(os.path.exists(self.db))

    def test_thread_local_turns_are_cleared_when_a_summarizer_raises(self):
        class Boom:
            def summarize_legacy(self, source, conn=None):
                meter.analyze_language_signal_turns([{"ts": 1, "text": SECRET_TEXT, "model": "m"}])
                raise RuntimeError("parse failure")

        registry = mock.Mock()
        registry.get.return_value = Boom()
        source = {"path": os.path.join(self.tmp.name, "x.jsonl"), "provider": "codex", "id": "x"}
        with mock.patch.object(meter, "work_insights_settings", return_value=W.normalize_settings({"enabled": True})), \
                mock.patch.object(meter, "runtime_registry", return_value=registry), \
                mock.patch.object(meter, "source_revision_signature", return_value="sig"):
            with self.assertRaises(RuntimeError):
                meter.session_summary(source)
        self.assertIsNone(getattr(meter._WORK_TURNS, "turns", None))

    def post(self, path, body, token=True):
        server = meter.TokenMeterHTTPServer(("127.0.0.1", 0), meter.H)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            data = json.dumps(body)
            headers = {"Content-Type": "application/json", "Content-Length": str(len(data))}
            if token:
                headers["X-Token-Meter-Action"] = meter._ACTION_TOKEN
            conn.request("POST", path, body=data, headers=headers)
            response = conn.getresponse()
            payload = json.loads(response.read())
            conn.close()
            return response.status, payload
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_http_clear_reports_a_failed_delete(self):
        service = mock.Mock()
        service.clear.side_effect = W.LabelDeleteError("delete_failed")
        with mock.patch.object(meter, "TOKEN_METER_SETTINGS", self.settings), \
                mock.patch.object(meter, "_work_service_instance", service):
            status, payload = self.post("/work-insights/clear", {"confirm": True})
        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertNotIn("delete_failed", payload["error"])

    def test_work_sessions_route_validates_filters(self):
        rows = (row("a"),)
        with mock.patch.object(meter, "TOKEN_METER_SETTINGS", self.settings), \
                mock.patch.object(meter, "_work_service_instance", None), \
                mock.patch.dict(meter._xsess, {"data": {"ok": True}, "internal_rows": rows}):
            ok, status = meter.work_sessions_state({"months": ["6"], "work_type": ["debug"]})
            self.assertEqual(status, 200)
            self.assertEqual(ok["filters"], {"work_type": "debug"})
            for bad in ({"months": ["7"]}, {"work_type": ["nope"]}, {"month": ["2026-9"]},
                        {"outcome": ["x"]}, {"work_type": ["debug", "feature"]}):
                self.assertEqual(meter.work_sessions_state(bad)[1], 400, bad)
            self.assertEqual(meter.work_sessions_state({"area": ["Nowhere"]})[1], 404)
            self.assertEqual(meter.work_sessions_state({"project": ["missing"]})[1], 404)
            everything, _ = meter.work_sessions_state({"months": ["0"]})
        self.assertEqual(everything["total"], 1)
        self.assertNotIn("/", everything["sessions"][0]["project"])

    def test_post_routes_validate_over_http(self):
        with mock.patch.object(meter, "TOKEN_METER_SETTINGS", self.settings), \
                mock.patch.object(meter, "TOKEN_METER_WORK_INSIGHTS_DB", self.db), \
                mock.patch.object(meter, "_work_service_instance", None):
            self.assertEqual(self.post("/settings/work-insights", {"ollama_url": "http://10.0.0.2"})[0], 400)
            self.assertEqual(self.post("/settings/work-insights", {"bogus": 1})[0], 400)
            self.assertEqual(self.post("/work-insights/pause", {"duration": "forever"})[0], 400)
            self.assertEqual(self.post("/work-insights/clear", {"confirm": False})[0], 400)
            self.assertEqual(self.post("/work-insights/pause", {"duration": "1h"}, token=False)[0], 403)
            status, payload = self.post("/work-insights/pause", {"duration": "1h"})
            self.assertEqual(status, 200)
            self.assertIsInstance(payload["work_insights"]["paused_until"], float)
        self.assertFalse(os.path.exists(self.db))


class SessionTagProjectionTests(unittest.TestCase):
    def test_tags_are_enums_and_counts_only(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        service, values, _ = make_service(tmp.name)
        source = {"id": "sess-1", "path": "/traces/one.jsonl"}
        service.observe(meter._work_identity(source), turns(SECRET_TEXT, "that's wrong"))
        drain(service)
        with mock.patch.object(meter, "work_insights_service", return_value=service), \
                mock.patch.object(meter, "work_insights_settings", return_value=values):
            tags = meter.work_session_tags(source)
            payload = meter.dashboard_state_payload({"source": dict(source)})
            other = meter.work_session_tags({"id": "sess-1", "path": "/traces/fork.jsonl"})
        self.assertIsNone(other)
        self.assertEqual(set(tags), {"area", "work_type", "complexity", "corrections", "labeled_turns"})
        self.assertEqual(payload["work_tags"], tags)
        self.assertNotIn("zebra", json.dumps(payload))
        self.assertNotIn("/traces", json.dumps(payload["work_tags"]))


class ReviewRegressionTests(unittest.TestCase):
    """Regressions for defects found in independent review of the Work redesign."""

    AREAS = DomainTests.AREAS

    def build(self, rows, labels, sequences=None, **kwargs):
        prices = {"gpt-5.6": 10.0, "cheap": 1.0, "mid": 4.0}
        return domain.build_work_insights(
            rows, labels, lambda ident: ident.split("\0")[0], self.AREAS, lambda m, p: prices.get(m),
            today="2026-09-30", corrections_for=lambda ident, n: (sequences or {}).get(ident.split("\0")[0], []),
            **kwargs)

    def test_labeled_session_without_classifiable_follow_ups_is_single_shot(self):
        self.assertEqual(domain.session_outcome(3, [], labeled=True, follow_ups=0), "single_shot")
        self.assertEqual(domain.session_outcome(3, [], labeled=True, pending=True), "pending")
        rows = [row("hi", turns_=2), row("never", turns_=3), row("queued", turns_=3)]
        labels = {"hi": {"area": "Personal", "work_type": "feature", "follow_ups": 0},
                  "queued": {"area": "Personal", "work_type": "feature"}}
        out = self.build(rows, labels, pending_keys={"queued"})
        self.assertEqual(out["allocation"][-1]["outcomes"], {"single_shot": 1, "pending": 2})
        found = domain.find_sessions(rows, labels, lambda ident: ident.split("\0")[0], self.AREAS,
                                     lambda m, p: None, {"outcome": "single_shot"},
                                     corrections_for=lambda ident, n: [], pending_keys={"queued"})
        self.assertEqual([s["id"] for s in found["sessions"]], ["hi"])

    def test_previous_kpi_window_is_the_calendar_months_before(self):
        rows, labels, sequences = [], {}, {}
        for i, day in enumerate(["2026-07-10", "2026-07-11", "2026-09-10", "2026-09-11"]):
            rows.append(row(f"k{i}", day=day, turns_=3))
            labels[f"k{i}"] = {"work_type": "debug", "correction_labels": 2}
            sequences[f"k{i}"] = [(1, False), (2, False)]
        kpis = self.build(rows, labels, sequences, months=1)["kpis"]
        self.assertEqual(kpis["previous_months"], ["2026-08"])
        self.assertIsNone(kpis["previous"])
        kpis = self.build(rows, labels, sequences, months=3)["kpis"]
        self.assertEqual(kpis["previous_months"], ["2026-04", "2026-05", "2026-06"])
        kpis = self.build(rows, labels, sequences, months=0)["kpis"]
        self.assertEqual((kpis["previous_months"], kpis["previous"]), ([], None))
        january = [row("j", day="2026-01-05", turns_=3)]
        kpis = domain.build_work_insights(january, {"j": {"work_type": "debug"}}, lambda ident: ident.split("\0")[0],
                                          self.AREAS, lambda m, p: None, months=3, today="2026-01-31")["kpis"]
        self.assertEqual(kpis["previous_months"], ["2025-08", "2025-09", "2025-10"])

    def test_non_latin_requests_are_classifiable(self):
        for text in ("修复登录页面的错误", "исправь ошибку в форме входа"):
            self.assertEqual(W.prepare_text(text), text)
            self.assertTrue(W.is_substantive(text), text)
        for text in ("!!! ???", "🎉🎉", "12345", "... 42 ..."):
            self.assertEqual(W.prepare_text(text), "", text)
        self.assertFalse(W.is_substantive("hey"))
        self.assertFalse(W.is_substantive("修复"))

    def test_opener_moving_later_in_one_prompt_version_uses_the_new_opener(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        service, values, clock = make_service(tmp.name)
        answers = {"What kind of work": "debug", "Which area": "Product engineering", "Scale": "0",
                   "previous work was wrong": "no"}

        def respond(prompt):
            key = next(v for k, v in answers.items() if k in prompt)
            return jet_response(key if key.isdigit() or key in ("yes", "no") else letter_for(prompt, key))

        FakeClient.responder = respond
        service.observe("s1", turns("fix it", "ok"))
        drain(service)
        key = service.session_key("s1")
        self.assertEqual(service.snapshot()[key]["work_type"], "debug")
        answers.update({"What kind of work": "feature", "Which area": "Agents and tools", "Scale": "2"})
        clock.now += 60
        service.observe("s1", turns("fix it", "ok", "please build a new agent tool for the release flow"))
        drain(service)
        entry = service.snapshot()[key]
        self.assertEqual((entry["work_type"], entry["area"], entry["complexity"]),
                         ("feature", "Agents and tools", "complex"))

    def test_a_turn_that_became_the_opener_is_not_a_correction(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        service, values, clock = make_service(tmp.name)
        key, tag = service.session_key("s1"), W.PROMPT_VERSION
        first, second = service._turn_key("s1", 0), service._turn_key("s1", 1)
        service.ledger.record_label(first, "work_type", key, "debug", 0.9, tag, "d", clock.now)
        service.ledger.record_label(second, "correction", key, "True", 0.9, tag, "d", clock.now)
        service.ledger.record_label(second, "work_type", key, "feature", 0.9, tag, "d", clock.now + 60)
        service.labels_version += 1
        entry = service.snapshot()[key]
        self.assertEqual(entry["work_type"], "feature")
        self.assertNotIn("corrections", entry)
        self.assertNotIn("correction_labels", entry)

    def test_work_sessions_rejects_repeated_period_and_mode_and_impossible_months(self):
        rows = (row("a"),)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch.object(meter, "TOKEN_METER_SETTINGS", os.path.join(tmp.name, "settings.json")), \
                mock.patch.object(meter, "_work_service_instance", None), \
                mock.patch.dict(meter._xsess, {"data": {"ok": True}, "internal_rows": rows}):
            for bad in ({"months": ["3", "12"]}, {"ids": ["1", "1"]}, {"runtime": ["Codex", "Claude Code"]},
                        {"project": ["alpha", "beta"]}):
                payload, status = meter.work_sessions_state(bad)
                self.assertEqual((status, payload.get("error")), (400, "Use each filter once."), bad)
            for bad in ({"month": ["2026-13"]}, {"month": ["2026-00"]}, {"start_month": ["2026-13"]},
                        {"start_month": ["2026-00"]}):
                self.assertEqual(meter.work_sessions_state(bad)[1], 400, bad)
            self.assertEqual(meter.work_sessions_state({"month": ["2026-12"]})[1], 200)
            self.assertEqual(meter.work_sessions_state({"months": ["7d"], "start_month": ["2026-09-29"]})[1], 200)
            for bad in ({"months": ["7d"], "month": ["2026-09"]}, {"months": ["7d"], "start_month": ["2026-02-30"]},
                        {"months": ["1d"], "month": ["2026-9-1"]}, {"months": ["6"], "month": ["2026-09-29"]},
                        {"months": ["14d"]}):
                self.assertEqual(meter.work_sessions_state(bad)[1], 400, bad)

    def test_page_kpi_comparison_uses_the_reported_previous_months(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "page.html"),
                  encoding="utf-8") as handle:
            page = handle.read()
        kpis = page[page.index("function renderWorkKpis("):page.index("function renderWorkSpend(")]
        self.assertNotIn("months before", kpis)
        self.assertIn("kpis?.previous_months", kpis)
        self.assertIn("judged_sessions?kpis.previous:null", kpis)

    def test_page_work_filter_ignores_stale_responses(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "page.html"),
                  encoding="utf-8") as handle:
            page = handle.read()
        apply = page[page.index("async function applyWorkSessionFilter("):page.index("async function loadDelivery(")]
        self.assertIn("request=++workFilterRequest", apply)
        self.assertEqual(apply.count("if(request!==workFilterRequest)return;"), 2)
        self.assertLess(page.index("workFilterRequest=0"), page.index("function applyHashRoute(){"))


class OpenerPositionAndWindowTests(unittest.TestCase):
    """Regressions for the second correctness review: opener identity, calendar windows, outcomes, drill cells."""

    AREAS = DomainTests.AREAS
    RELEASE = "please build a new agent tool for the release flow"

    def service(self, settings=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        service, values, clock = make_service(tmp.name, settings)

        def respond(prompt):
            real = self.RELEASE in prompt
            if "What kind of work" in prompt:
                return jet_response(letter_for(prompt, "feature" if real else "ops"))
            if "Which area" in prompt:
                return jet_response(letter_for(prompt, "Agents and tools" if real else "Personal"))
            if "Scale" in prompt:
                return jet_response("2" if real else "0")
            return jet_response("no")

        FakeClient.responder = respond
        return service, clock

    def assert_real_opener(self, service):
        entry = service.snapshot()[service.session_key("s1")]
        self.assertEqual((entry["work_type"], entry["area"], entry["complexity"]),
                         ("feature", "Agents and tools", "complex"))

    def test_fallback_opener_queued_before_the_real_opener_does_not_win(self):
        service, clock = self.service()
        service.observe("s1", turns("hi"))
        service.observe("s1", turns("hi", self.RELEASE))
        # The real opener is newer, so the worker would label it first and the fallback afterwards.
        drain(service)
        self.assert_real_opener(service)

    def test_opener_labels_are_chosen_by_position_not_write_time(self):
        service, clock = self.service()
        service.observe("s1", turns("hi", self.RELEASE))
        drain(service)
        key, fallback = service.session_key("s1"), service._turn_key("s1", 0)
        for offset in (0, 60):  # An equal whole-second write, then a later one (an in-flight stale item).
            for question, value in (("work_type", "ops"), ("area", "Personal"), ("complexity", "routine")):
                service.ledger.record_label(fallback, question, key, value, 0.9,
                                            W.question_tags(service.settings_provider())[question], "d",
                                            clock.now + offset)
            service.labels_version += 1
            self.assert_real_opener(service)
        # The opener survives a restart without re-observing the session.
        reopened = W.WorkInsightsService(service.ledger_path, service.settings_provider, client_factory=FakeClient,
                                         clock=clock, monotonic=clock, sleep=lambda s: None)
        self.assert_real_opener(reopened)

    def test_moving_the_opener_drops_the_queued_fallback_opener(self):
        service, clock = self.service()
        service.observe("s1", turns("hi"))
        service.observe("s1", turns("hi", self.RELEASE))
        by_key = {item.turn_key: item.questions for item in service.queue}
        self.assertEqual(by_key, {service._turn_key("s1", 1): W.SESSION_QUESTIONS})

    def test_pushback_labels_before_a_moved_opener_no_longer_count(self):
        service, clock = self.service()
        base = FakeClient.responder
        FakeClient.responder = lambda prompt: (jet_response("yes") if "Answer yes or no" in prompt
                                               and "wrong!!" in prompt else base(prompt))
        service.observe("s1", turns("hi", "wrong!!"))
        drain(service)
        self.assertEqual(service.session_corrections("s1", 2), [(1, True)])
        service.observe("s1", turns("hi", "wrong!!", self.RELEASE, "wrong!! again"))
        drain(service)
        self.assert_real_opener(service)
        self.assertEqual(service.session_corrections("s1", 4), [(3, True)])

    def test_sessions_past_the_backfill_horizon_still_record_their_opener(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        service, _values, clock = make_service(tmp.name, {"backfill_days": 30})
        clock.now = 1_799_999_000.0 + 40 * 86400
        self.assertEqual(service.observe("s1", turns(self.RELEASE, "thanks, looks good")), 0)
        self.assertFalse(service.queue)
        self.assertEqual(service._openers[service.session_key("s1")],
                         (service._turn_key("s1", 0), 1))

    def test_past_horizon_opener_move_keeps_the_labels_it_cannot_replace(self):
        service, clock = self.service({"backfill_days": 30})
        clock.now = 1_799_999_100.0
        service.observe("s1", turns("hi"))
        drain(service)
        self.assertEqual(service.snapshot()[service.session_key("s1")]["work_type"], "ops")
        clock.now += 40 * 86400
        self.assertEqual(service.observe("s1", turns("hi", self.RELEASE)), 0)
        self.assertEqual(service._openers[service.session_key("s1")][0], service._turn_key("s1", 1))
        entry = service.snapshot()[service.session_key("s1")]
        self.assertEqual((entry["work_type"], entry["area"]), ("ops", "Personal"))

    def test_a_permanently_failed_opener_reads_unclear_not_the_fallback(self):
        service, clock = self.service()
        service.observe("s1", turns("hi"))
        drain(service)
        self.assertEqual(service.snapshot()[service.session_key("s1")]["work_type"], "ops")
        base = FakeClient.responder
        FakeClient.responder = lambda prompt: jet_response("Sure") if self.RELEASE in prompt else base(prompt)
        for _attempt in range(W.MAX_ITEM_ATTEMPTS):
            service.observe("s1", turns("hi", self.RELEASE))
            drain(service)
            clock.now += W.ITEM_RETRY_DELAYS_S[-1] + 1
        entry = service.snapshot()[service.session_key("s1")]
        self.assertEqual((entry["work_type"], entry["area"]), ("unclear", "Unclear"))

    def build(self, rows, labels, today, **kwargs):
        return domain.build_work_insights(rows, labels, lambda ident: ident.split("\0")[0], self.AREAS,
                                          lambda m, p: None, today=today, **kwargs)

    def test_current_window_is_calendar_months_ending_this_month(self):
        rows = [row("may", day="2026-05-10"), row("sep", day="2026-09-10")]
        out = self.build(rows, {}, "2026-09-30", months=3)
        self.assertEqual(out["months"], ["2026-07", "2026-08", "2026-09"])
        self.assertEqual(out["kpis"]["previous_months"], ["2026-04", "2026-05", "2026-06"])
        self.assertEqual(out["kpis"]["previous"]["sessions"], 1)
        self.assertEqual([b["sessions_total"] for b in out["allocation"]], [0, 0, 1])
        found = domain.find_sessions(rows, {}, lambda ident: ident.split("\0")[0], self.AREAS, lambda m, p: None,
                                     {}, months=3, today="2026-09-30")
        self.assertEqual([s["id"] for s in found["sessions"]], ["sep"])

    def test_months_without_data_are_empty_not_replaced_by_older_data(self):
        out = self.build([row("jun", day="2026-06-10")], {}, "2026-09-30", months=3)
        self.assertEqual(out["months"], ["2026-07", "2026-08", "2026-09"])
        self.assertEqual(out["coverage"]["sessions"], 0)
        self.assertIsNone(out["kpis"]["current"]["resolved_rate"])
        self.assertEqual(out["kpis"]["previous"]["sessions"], 1)
        # Without a today, the window ends at the latest data month; All history keeps data months only.
        self.assertEqual(self.build([row("jun", day="2026-06-10")], {}, "", months=3)["months"],
                         ["2026-04", "2026-05", "2026-06"])
        everything = self.build([row("a", day="2026-02-10"), row("b", day="2026-06-10")], {}, "2026-09-30", months=0)
        self.assertEqual((everything["months"], everything["kpis"]["previous_months"]), (["2026-02", "2026-06"], []))

    def test_labeled_session_with_never_labeled_follow_ups_is_unclear_not_single_shot(self):
        self.assertEqual(domain.session_outcome(3, [], labeled=True, follow_ups=0), "single_shot")
        self.assertEqual(domain.session_outcome(3, [], labeled=True, follow_ups=2), "unclear")
        self.assertEqual(domain.session_outcome(3, [], labeled=True), "unclear")  # Follow-up count unknown.
        self.assertEqual(domain.session_outcome(3, [], labeled=True, follow_ups=2, pending=True), "pending")
        service, clock = self.service()
        service.observe("s1", turns(self.RELEASE, "[Image #1]"))
        service.observe("s2", turns(self.RELEASE, "that is wrong, undo it"))
        drain(service)
        snapshot = service.snapshot()
        self.assertEqual(snapshot[service.session_key("s1")]["follow_ups"], 0)
        self.assertEqual(snapshot[service.session_key("s2")]["follow_ups"], 1)

    def test_page_drill_cells_keep_table_semantics_with_inner_buttons(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "page.html"),
                  encoding="utf-8") as handle:
            page = handle.read()
        work = page[page.index("function renderWorkSizing("):page.index("function renderWork(payload)")]
        self.assertNotIn("role=button", work)
        self.assertNotIn("tabindex=0 data-drill", work)
        self.assertEqual(work.count('<td class="num mono workFitCell"><button type=button class=workDrillBtn data-drill='), 2)
        self.assertIn('<div class=workSizingCell role=cell', work)
        self.assertIn('<button type=button class="workDrillBtn workSizingBtn" data-drill=', work)
        self.assertNotIn("rect[data-drill]", page)  # The column chart is gone; every drill target is a button.
        self.assertIn(".workDrillBtn:focus-visible{", page)


if __name__ == "__main__":
    unittest.main()


class ShortRangeTests(unittest.TestCase):
    """1 day / 1 week / 1 month History choices use daily buckets and compare with the span before."""

    AREAS = DomainTests.AREAS

    def build(self, rows, labels, months, today="2026-09-30"):
        return domain.build_work_insights(rows, labels, lambda ident: ident.split("\0")[0], self.AREAS,
                                          lambda m, p: 10.0, months=months, today=today)

    def test_parse_period_accepts_only_supported_choices(self):
        self.assertEqual(domain.parse_period("1d"), ("day", 1))
        self.assertEqual(domain.parse_period("7d"), ("day", 7))
        self.assertEqual(domain.parse_period("30d"), ("day", 30))
        self.assertEqual(domain.parse_period("6"), ("month", 6))
        self.assertEqual(domain.parse_period("0"), ("month", 0))
        for bad in ("2d", "14d", "5", "-1", "d", "", "7 d", None):
            self.assertIsNone(domain.parse_period(bad))

    def test_week_uses_seven_daily_buckets_and_the_week_before(self):
        rows = [row("today", day="2026-09-30", cost=3.0), row("monday", day="2026-09-28"),
                row("lastweek", day="2026-09-21", cost=5.0), row("old", day="2026-08-01")]
        out = self.build(rows, {}, "7d")
        self.assertEqual(out["grain"], "day")
        self.assertEqual(out["months"], [f"2026-09-{d}" for d in range(24, 31)])
        self.assertEqual(out["kpis"]["previous_months"], [f"2026-09-{d}" for d in range(17, 24)])
        by_day = {b["month"]: b for b in out["allocation"]}
        self.assertEqual(by_day["2026-09-30"]["spend_total"], 3.0)
        self.assertTrue(by_day["2026-09-30"]["partial"])
        self.assertEqual(by_day["2026-09-25"]["spend_total"], 0)
        self.assertEqual(out["kpis"]["current"]["sessions"], 2)
        self.assertEqual(out["kpis"]["previous"]["sessions"], 1)
        self.assertEqual(out["rework"]["grain"], "day")

    def test_one_day_is_today_against_yesterday(self):
        out = self.build([row("a", day="2026-09-30"), row("b", day="2026-09-29")], {}, "1d")
        self.assertEqual(out["months"], ["2026-09-30"])
        self.assertEqual(out["kpis"]["previous_months"], ["2026-09-29"])

    def test_month_range_crosses_a_month_boundary(self):
        out = self.build([], {}, "30d", today="2026-03-01")
        self.assertEqual((out["months"][0], out["months"][-1], len(out["months"])), ("2026-01-31", "2026-03-01", 30))

    def test_drill_down_by_day_matches_the_daily_bucket(self):
        rows = [row("a", day="2026-09-30"), row("b", day="2026-09-29"), row("c", day="2026-09-01")]
        found = domain.find_sessions(rows, {}, lambda ident: ident.split("\0")[0], self.AREAS, lambda m, p: 10.0,
                                     {"start_month": "2026-09-29"}, months="7d", today="2026-09-30")
        self.assertEqual([s["id"] for s in found["sessions"]], ["b"])
        window = domain.find_sessions(rows, {}, lambda ident: ident.split("\0")[0], self.AREAS, lambda m, p: 10.0,
                                      {}, months="1d", today="2026-09-30")
        self.assertEqual([s["id"] for s in window["sessions"]], ["a"])


class ModelScorecardTests(unittest.TestCase):
    def test_scorecard_scopes_models_by_runtime_and_reports_resolution(self):
        rows = [row("a", model="gpt-5.6", cost=4.0, turns_=3), row("b", model="gpt-5.6", cost=2.0, turns_=3),
                row("c", model="gpt-5.6", runtime="Cursor", cost=1.0, turns_=1)]
        labels = {"a": {"area": "Personal", "work_type": "debug", "correction_labels": 2, "corrections": 0},
                  "b": {"area": "Personal", "work_type": "debug", "correction_labels": 2, "corrections": 2}}
        sequences = {"a": [(1, False), (2, False)], "b": [(1, True), (2, True)]}
        out = domain.build_work_insights(rows, labels, lambda ident: ident.split("\0")[0], DomainTests.AREAS,
                                         lambda m, p: 10.0, today="2026-09-30",
                                         corrections_for=lambda ident, count: sequences.get(ident.split("\0")[0], []))
        card = {(r["model"], r["runtime"]): r for r in out["model_scorecard"]}
        codex = card[("gpt-5.6", "Codex")]
        self.assertEqual((codex["sessions"], codex["spend"], codex["judged_sessions"]), (2, 6.0, 2))
        self.assertEqual((codex["resolved_rate"], codex["cost_per_resolved"]), (0.5, 4.0))
        self.assertEqual(codex["rework"]["rate"], 0.5)
        cursor = card[("gpt-5.6", "Cursor")]
        self.assertIsNone(cursor["resolved_rate"])
        self.assertIsNone(cursor["cost_per_resolved"])
        self.assertEqual([r["model"] for r in out["model_scorecard"]][:1], ["gpt-5.6"])


class FlaggedSpendTests(unittest.TestCase):
    def test_a_session_flagged_for_tier_and_effort_counts_once(self):
        rows = [row("both", model="gpt-5.6", cost=10.0), row("a", model="cheap", cost=1.0),
                row("b", model="mid", cost=1.0)]
        rows[0]["reasoning_effort"] = "xhigh"
        labels = {key: {"area": "Personal", "complexity": "routine"} for key in ("both", "a", "b")}
        prices = {"gpt-5.6": 10.0, "cheap": 1.0, "mid": 4.0}
        out = domain.build_work_insights(rows, labels, lambda ident: ident.split("\0")[0], DomainTests.AREAS,
                                         lambda m, p: prices.get(m), today="2026-09-30")
        kinds = {item["kind"] for item in out["opportunities"]}
        self.assertEqual(kinds, {"premium_routine", "effort_routine"})
        flagged = out["right_sizing"]["flagged"]
        self.assertEqual((flagged["sessions"], flagged["spend"], flagged["labeled_spend"]), (1, 10.0, 12.0))
        self.assertAlmostEqual(flagged["share"], 10 / 12)

    def build(self, rows, labels, prices):
        return domain.build_work_insights(rows, labels, lambda ident: ident.split("\0")[0], DomainTests.AREAS,
                                          lambda m, p: prices.get(m), today="2026-09-30")

    def test_light_models_on_high_impact_work_count_with_unknown_tier_and_effort_left_out(self):
        rows = [row("hi", model="cheap", cost=4.0), row("nontier", model="mystery", cost=7.0),
                row("std", model="mid", cost=2.0), row("prem", model="gpt-5.6", cost=3.0)]
        rows[1]["reasoning_effort"] = "xhigh"
        labels = {"hi": {"area": "Personal", "complexity": "high_impact", "corrections": 9, "correction_labels": 20},
                  "nontier": {"area": "Personal", "complexity": "everyday"},
                  "std": {"area": "Personal", "complexity": "complex", "corrections": 0, "correction_labels": 20},
                  "prem": {"area": "Personal", "complexity": "complex", "corrections": 0, "correction_labels": 20}}
        out = self.build(rows, labels, {"cheap": 1.0, "mid": 4.0, "gpt-5.6": 10.0})
        self.assertIn("light_complex", {item["kind"] for item in out["opportunities"]})
        flagged = out["right_sizing"]["flagged"]
        self.assertEqual((flagged["sessions"], flagged["spend"], flagged["labeled_spend"]), (1, 4.0, 16.0))

    def test_no_complexity_labels_reports_no_share(self):
        flagged = self.build([row("a")], {"a": {"area": "Personal"}}, {"gpt-5.6": 10.0})["right_sizing"]["flagged"]
        self.assertEqual((flagged["sessions"], flagged["spend"], flagged["share"]), (0, 0, None))
