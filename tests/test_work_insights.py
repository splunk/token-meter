import http.client
import http.server
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

import meter
from token_meter.domain import work as domain
from token_meter.services import work_insights as W

REAL_MEMORY_PROBE = W._default_memory_probe
_memory_patch = mock.patch.object(W, "_default_memory_probe", lambda: None)


def setUpModule():
    # Service tests must not depend on how much memory the test machine has free right now.
    _memory_patch.start()


def tearDownModule():
    _memory_patch.stop()


SECRET_TEXT = "please refactor the zebra-kumquat billing module"


WORK_TYPE_Q, AREA_Q, COMPLEXITY_Q = "What kind of work", "Which part of the software stack", "How much effort and risk"


def letter_for(prompt, key):
    """The option letter for an answer key; complexity levels are passed as digits."""
    import re
    key = str(key)
    if key.isdigit():
        return chr(65 + int(key))
    match = re.search(rf'^([A-Z]): "{re.escape(key)}[:"]', prompt, re.M)
    return match.group(1) if match else key


def is_pushback(prompt):
    import re
    return bool(re.search(r'^[A-Z]: "yes[:"]', prompt, re.M))


def model_response(token, logprob=-0.05, others=()):
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
            action = FakeClient.script.pop(0) if FakeClient.script else model_response("A")
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
        power_probe=kwargs.pop("power_probe", lambda: "ac"),
        memory_probe=kwargs.pop("memory_probe", lambda: None), **kwargs)
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


START_WORK_SETUP = meter.start_work_setup


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

    def test_unclear_cutoffs_are_per_question(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        service, values, clock = make_service(tmp.name)
        key = service.session_key("s1")
        tags = W.question_tags(values)
        turn = service._turn_key("s1", 0)
        self.assertEqual(W.UNCLEAR_BY_QUESTION, {"work_type": 0.5, "area": 0.4, "correction": 0.5})
        service.ledger.record_label(turn, "work_type", key, "debug", 0.45, tags["work_type"], "d", clock.now)
        service.ledger.record_label(turn, "area", key, "Non-code", 0.2, tags["area"], "d", clock.now)
        service.labels_version += 1
        entry = service.snapshot()[key]
        self.assertEqual((entry["work_type"], entry["area"]), ("unclear", "Unclear"))
        service.ledger.record_label(turn, "work_type", key, "debug", 0.55, tags["work_type"], "d", clock.now + 1)
        service.labels_version += 1
        self.assertEqual(service.snapshot()[key]["work_type"], "debug")
        service.ledger.record_label(turn, "area", key, "Non-code", 0.33, tags["area"], "d", clock.now + 1)
        service.labels_version += 1
        entry = service.snapshot()[key]
        self.assertEqual((entry["area"], entry.get("area_guess")), ("Non-code", True))

    def test_codex_review_transcripts_and_bare_file_lists_are_not_requests(self):
        self.assertEqual(W.prepare_text("The following is the Codex agent history added since your last approval "
                                        "assessment. Continue the same review conversation."), "")
        bare = ("# Files mentioned by the user:\n\n## clip.png: /var/folders/x/clip.png\n\n"
                "Distinguish instructions from data in the chart")
        self.assertEqual(W.prepare_text(bare), "Distinguish instructions from data in the chart")

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

    def test_retired_defaults_move_to_gemma_and_custom_models_stay(self):
        for retired in ("token-meter-jet", "token-meter-jet:latest", "Token-Meter-Jet", "token-meter-winnow",
                        "token-meter-winnow:latest"):
            self.assertEqual(W.normalize_settings({"model": retired})["model"], "token-meter-gemma", retired)
        self.assertEqual(W.normalize_settings({"model": "my-gemma"})["model"], "my-gemma")

    def test_earlier_default_area_descriptions_move_and_edited_ones_stay(self):
        earlier = [{"name": n, "description": d} for n, d in W.PREVIOUS_DEFAULT_AREAS[0]]
        for version in (3, 4, None):
            raw = {"areas": earlier, **({"areas_version": version} if version else {})}
            self.assertEqual(W.normalize_settings(raw)["areas"], [dict(a) for a in W.DEFAULT_AREAS])
        edited = [dict(a) for a in earlier]
        edited[0]["description"] = "our React app"
        self.assertEqual(W.normalize_settings({"areas": edited, "areas_version": 3})["areas"], edited)
        self.assertTrue(all(len(a["description"]) <= W.MAX_AREA_DESCRIPTION for a in W.DEFAULT_AREAS))

    def test_areas_bounds_and_reserved_names(self):
        with self.assertRaises(ValueError):
            W.normalize_areas([{"name": "One", "description": "d"}])
        with self.assertRaises(ValueError):
            W.normalize_areas([{"name": "Unclear", "description": "d"}, {"name": "B", "description": "d"}])
        with self.assertRaises(ValueError):
            W.normalize_areas([{"name": "A", "description": "d"}, {"name": "a", "description": "d"}])
        self.assertEqual(len(W.normalize_areas(list(W.DEFAULT_AREAS))), len(W.DEFAULT_AREAS))


class MemoryGuardTests(unittest.TestCase):
    GB = 1024 ** 3

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def service(self, memory):
        service, values, clock = make_service(tempfile.mkdtemp(dir=self.tmp.name))
        service.memory_probe = lambda: memory[0]
        service.observe("s1", turns("please fix the chart"))
        return service, clock

    def test_the_model_is_not_loaded_without_room_for_it(self):
        memory = [(7 * self.GB, 24 * self.GB, 1)]  # 7 GB free: below 6 GB for the model + 2.4 GB headroom
        service, clock = self.service(memory)
        wait = service.step()
        self.assertEqual((service.state, service.reason, wait), (W.STATE_THROTTLED, "low_memory", 60))
        self.assertEqual(FakeClient.prompts, [])
        memory[0] = (12 * self.GB, 24 * self.GB, 2)  # macOS warns, but there is room: labeling goes on
        service.retry_at = 0
        service.step()
        self.assertGreater(len(FakeClient.prompts), 0)
        self.assertEqual(service.low_memory_strikes, 0)

    def test_a_loaded_model_is_unloaded_under_critical_pressure_or_below_the_headroom(self):
        for short in ((20 * self.GB, 24 * self.GB, W.PRESSURE_CRITICAL), (2 * self.GB, 24 * self.GB, 1)):
            memory = [(12 * self.GB, 24 * self.GB, 1)]
            service, clock = self.service(memory)
            service.step()
            self.assertGreater(len(FakeClient.prompts), 0)
            service.observe("s2", turns("add a tooltip"))
            service.loaded = True  # the model is still loaded while work is queued
            memory[0] = short
            unloads = FakeClient.unloads
            service.step()
            self.assertEqual((service.state, service.reason), (W.STATE_THROTTLED, "low_memory"), short)
            self.assertFalse(service.loaded)
            self.assertEqual(FakeClient.unloads, unloads + 1)
            # A loaded model only needs the headroom: 3 GB free keeps it running.
            memory[0] = (3 * self.GB, 24 * self.GB, 2)
            service.loaded = True
            self.assertFalse(service._memory_short())

    def test_repeated_shortfalls_wait_longer_up_to_a_cap(self):
        memory = [(1 * self.GB, 24 * self.GB, 4)]
        service, clock = self.service(memory)
        waits = []
        for _ in range(7):
            service.retry_at = 0
            waits.append(service.step())
        self.assertEqual(waits, [60, 120, 240, 480, 900, 900, 900])

    def test_memory_is_checked_before_every_call_within_an_item(self):
        memory = [(12 * self.GB, 24 * self.GB, 1)]
        service, clock = self.service(memory)

        def respond(prompt):
            if len(FakeClient.prompts) == 2:  # a large app starts while the item is half done
                memory[0] = (20 * self.GB, 24 * self.GB, W.PRESSURE_CRITICAL)
            return model_response("A")

        FakeClient.responder = respond
        wait = service.step()
        self.assertEqual((service.reason, wait, len(FakeClient.prompts)), ("low_memory", 60, 2))
        self.assertEqual(FakeClient.unloads, 1)
        self.assertEqual(len(service.queue), 1)  # the item stays queued; its other questions run later
        memory[0] = (12 * self.GB, 24 * self.GB, 1)
        service.retry_at = 0
        service.step()
        self.assertEqual(service.queue, [])

    def test_loading_the_model_does_not_trip_the_guard_for_the_rest_of_the_item(self):
        # 9 GB free before the model loads, 3.5 GB once it is in memory: above the 2.4 GB headroom.
        service, values, clock = make_service(tempfile.mkdtemp(dir=self.tmp.name))
        service.memory_probe = lambda: ((3.5 if FakeClient.prompts else 9) * self.GB, 24 * self.GB, 1)
        service.observe("s1", turns("please fix the chart"))
        service.step()
        self.assertNotEqual(service.reason, "low_memory")
        self.assertEqual(len(FakeClient.prompts), 4)
        self.assertEqual(service.queue, [])

    def test_an_early_wake_keeps_the_scheduled_recheck(self):
        memory = [(1 * self.GB, 24 * self.GB, 1)]
        service, clock = self.service(memory)
        self.assertEqual(service.step(), 60)
        clock.now += 20
        self.assertEqual(service.step(), 40)  # woken by queue activity: no new strike
        self.assertEqual(service.low_memory_strikes, 1)
        clock.now += 40
        self.assertEqual(service.step(), 120)

    def test_a_model_left_resident_by_a_restart_is_unloaded_only_if_ollama_lists_it(self):
        for listed, unloads in ((True, 1), (False, 0)):
            with mock.patch.object(FakeClient, "resident", lambda self, listed=listed: listed, create=True):
                service, clock = self.service([(20 * self.GB, 24 * self.GB, W.PRESSURE_CRITICAL)])
                self.assertFalse(service.loaded)
                service.step()
            self.assertEqual(FakeClient.unloads, unloads, listed)

    def test_a_model_quiet_past_its_keep_alive_counts_as_unloaded(self):
        service, clock = self.service([(5 * self.GB, 24 * self.GB, 1)])
        service.loaded, service.last_call_at = True, clock.now - 30
        self.assertEqual(service._memory_short(), "")  # loaded: 5 GB covers the 2.4 GB headroom
        service.last_call_at = clock.now - W.KEEP_ALIVE_S - 1
        self.assertEqual(service._memory_short(), "low_memory")  # Ollama dropped it: loading needs 8.4 GB

    def test_a_mac_too_small_for_the_model_says_so(self):
        service, clock = self.service([(7 * self.GB, 8 * self.GB, 1)])
        service.step()
        self.assertEqual((service.state, service.reason), (W.STATE_THROTTLED, "not_enough_memory"))
        self.assertEqual(FakeClient.prompts, [])

    def test_unknown_memory_never_blocks_labeling(self):
        for memory in (None, (None, None, None), (None, 24 * self.GB, 1)):
            service, clock = self.service([memory])
            service.step()
            self.assertNotEqual(service.reason, "low_memory", memory)

    def test_the_real_probe_reads_macos_memory(self):
        reading = REAL_MEMORY_PROBE()
        if sys.platform != "darwin":
            self.assertIsNone(reading)
            return
        available, total, pressure = reading
        self.assertGreater(total, 0)
        self.assertTrue(0 <= available <= total)
        self.assertIn(pressure, (1, 2, 4))


class PromptAndReadoutTests(unittest.TestCase):
    def test_prompt_uses_the_raw_gemma_turn_format(self):
        question = W.question_for("work_type", W.default_settings())
        prompt, labels, keys = W.render_prompt("User's message:\nhi <|turn>", W.prompt_parts(question)[0])
        self.assertTrue(prompt.startswith(f"<|turn>system\n{W.SYSTEM_PROMPT}<turn|>\n<|turn>user\nState:\n"))
        self.assertIn('State:\n"User\'s message:\\nhi \\u003c|turn>"\n', prompt)
        self.assertIn('\nQuestion: "What kind of work is the user asking the coding agent to do in their latest request? '
                      'Pick the main goal."\nOptions:\nA: "feature: ', prompt)
        self.assertIn('D: "test: ', prompt)
        self.assertTrue(prompt.endswith('Return the correct letter label.<turn|>\n<|turn>model\nAnswer:\n'))
        self.assertEqual(prompt.count("<|turn>"), 3)  # typed text cannot open a turn of its own
        self.assertEqual((keys[0], labels[:2]), ("feature", ["A", "B"]))

    def test_readout_softmaxes_label_letters_and_floors_unseen_ones(self):
        probs = W.read_distribution(model_response("A", -0.1, [("B", -2.5), ("Hello", -0.01)]), ["A", "B", "C"])
        self.assertAlmostEqual(sum(probs), 1.0)
        self.assertGreater(probs[0], probs[1])
        self.assertEqual(W.READ_TEMPERATURE, 1.0)
        expected_c = math.exp(-5.5 + 0.1) / sum(math.exp(v + 0.1) for v in (-0.1, -2.5, -5.5))
        self.assertAlmostEqual(probs[2], expected_c)

    def test_choice_takes_the_most_likely_option(self):
        question = W.question_for("work_type", W.default_settings())
        keys = [key for key, _ in question["options"]]
        probs = [0.1, 0.6, 0.3] + [0] * (len(keys) - 3)
        self.assertEqual(W.read_answer(question, [probs], keys), ("debug", 0.6))

    def test_area_asks_both_option_orders_and_averages_them(self):
        settings = W.default_settings()
        question = W.question_for("area", settings)
        forward, backward = W.prompt_parts(question)
        self.assertEqual(backward["options"], list(reversed(forward["options"])))
        keys = [key for key, _ in question["options"]]
        rendered = [W.render_prompt("User's message:\nfix the chart", part) for part in (forward, backward)]
        self.assertEqual(rendered[1][2], list(reversed(keys)))
        first = [0.5, 0.3, 0.2, 0, 0, 0, 0]
        second = list(reversed([0.1, 0.7, 0.2, 0, 0, 0, 0]))  # as the reversed prompt reports it
        aligned = W.align([first, second], rendered, keys)
        self.assertEqual(aligned[1], [0.1, 0.7, 0.2, 0, 0, 0, 0])
        with mock.patch.object(W, "AREA_PRIOR", {}):
            plain = W.question_for("area", settings)
        self.assertIsNone(plain["prior"])
        value, confidence = W.read_answer(plain, aligned, keys)
        self.assertEqual(value, keys[1])
        self.assertAlmostEqual(confidence, 0.5)

    def test_area_prior_evens_out_favoured_default_areas_only(self):
        settings = W.default_settings()
        keys = [area["name"] for area in settings["areas"]]
        prior = dict.fromkeys(keys, 0.1)
        prior[keys[0]] = 0.4
        with mock.patch.object(W, "AREA_PRIOR", prior), mock.patch.object(W, "AREA_PRIOR_ALPHA", 1.0):
            question = W.question_for("area", settings)
            self.assertEqual(question["prior"], [prior[k] for k in keys])
            # 0.45 / 0.4 is below 0.35 / 0.1: the favoured area needs a much stronger answer to win.
            value, confidence = W.read_answer(question, [[0.45, 0.35, 0.2, 0, 0, 0, 0]], keys)
            self.assertEqual(value, keys[1])
            self.assertAlmostEqual(confidence, 3.5 / (1.125 + 3.5 + 2.0))
            custom = dict(settings, areas=[{"name": "Mobile", "description": "iOS app"},
                                           {"name": "Web", "description": "site"}])
            self.assertIsNone(W.question_for("area", custom)["prior"])
            edited = dict(settings, areas=[dict(settings["areas"][0], description="changed")] + settings["areas"][1:])
            self.assertIsNone(W.question_for("area", edited)["prior"])

    def test_shipped_area_prior_covers_every_default_area(self):
        self.assertEqual(set(W.AREA_PRIOR), {area["name"] for area in W.DEFAULT_AREAS})
        self.assertAlmostEqual(sum(W.AREA_PRIOR.values()), 1.0, places=3)
        self.assertIsNotNone(W.question_for("area", W.default_settings())["prior"])

    def test_pushback_checks_see_invented_worked_examples_first(self):
        question = W.question_for("correction", W.default_settings())
        part = W.prompt_parts(question)[1]
        prompt, labels, keys = W.render_prompt("User's latest message:\nstill broken", part)
        self.assertEqual(keys, [False, True])
        self.assertEqual(prompt.count("<|turn>user\n"), len(W.PUSHBACK_SHOTS) + 1)
        self.assertEqual(prompt.count("<|turn>model\nAnswer:\n"), len(W.PUSHBACK_SHOTS) + 1)
        answers = [line[-1] for line in prompt.split("<turn|>") if line.endswith(("Answer:\nA", "Answer:\nB"))]
        self.assertEqual(answers, ["AB"[shot[1][1]] for shot in W.PUSHBACK_SHOTS])
        self.assertTrue(prompt.endswith('Return the correct letter label.<turn|>\n<|turn>model\nAnswer:\n'))
        self.assertIn('State:\n"User\'s latest message:\\nstill broken"\n', prompt)
        self.assertEqual(len(W.PUSHBACK_SHOTS), 6)
        self.assertTrue(all(len(answers) == len(W.PUSHBACK_CHECKS) for _state, answers in W.PUSHBACK_SHOTS))

    def test_complexity_reads_the_expected_level_against_the_cutoffs(self):
        question = W.question_for("complexity", W.default_settings())
        self.assertEqual((W.COMPLEXITY_TEMPERATURE, W.COMPLEXITY_CUTOFFS), (2.0, (0.95, 1.35, 2.5)))
        # Flattened at temperature 2, the expected levels are 0.41, 0.88, 0.96, 1.29, 1.56, 2.12, and 2.59.
        for probs, level in (([0.9, 0.08, 0.02, 0], 0), ([0.6, 0.3, 0.08, 0.02], 0), ([0.4, 0.5, 0.08, 0.02], 1),
                             ([0.1, 0.7, 0.15, 0.05], 1), ([0.05, 0.3, 0.6, 0.05], 2), ([0.02, 0.08, 0.3, 0.6], 2),
                             ([0, 0.02, 0.08, 0.9], 3)):
            self.assertEqual(W.read_answer(question, [probs], [0, 1, 2, 3])[0], level, probs)

    def test_pushback_combines_four_checks_at_the_fitted_threshold(self):
        question = W.question_for("correction", W.default_settings())
        self.assertEqual(len(W.prompt_parts(question)), 4)
        logit = lambda p: math.log(p / (1 - p))
        for yes in ([0.9] * 4, [0.1] * 4, [0.3, 0.8, 0.2, 0.6], [0.2, 0.6, 0.1, 0.1], [0.5, 0.5, 0.5, 0.5]):
            score = W.PUSHBACK_WEIGHTS[0] + sum(w * logit(p) for w, p in zip(W.PUSHBACK_WEIGHTS[1:], yes))
            value, confidence = W.read_answer(question, [[1 - p, p] for p in yes], [False, True])
            self.assertEqual(value, 1 / (1 + math.exp(-score)) >= W.PUSHBACK_THRESHOLD, yes)
            self.assertGreaterEqual(confidence, 0.5)

    def test_pushback_state_names_what_the_agent_changed_last_turn(self):
        state = W.pushback_state("still broken", "Done: fixed it.", "fix the chart", "Files the agent changed: page.html.")
        self.assertEqual(state, "The user's earlier request:\nfix the chart\n\n"
                                "In its last turn, the assistant changed these files: page.html.\n\n"
                                "Assistant's previous message (end):\nDone: fixed it.\n\n"
                                "User's latest message:\nstill broken")
        self.assertIn("In its last turn, the assistant changed no files.",
                      W.pushback_state("ok", "c", "", "The agent changed no files; it committed, pushed, or opened pull requests."))
        self.assertEqual(W.pushback_state("ok", "", "", ""), "User's latest message:\nok")

    def test_missing_label_token_is_an_item_error(self):
        with self.assertRaises(W.ClassifierError) as caught:
            W.read_distribution(model_response("Sure"), ["A", "B"])
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
        answers = {WORK_TYPE_Q: "refactor", AREA_Q: "Backend & APIs", COMPLEXITY_Q: "1"}

        def respond(prompt):
            if is_pushback(prompt):
                return model_response(letter_for(prompt, "yes"))
            key = next(v for k, v in answers.items() if k in prompt)
            return model_response(letter_for(prompt, key))

        FakeClient.responder = respond
        drain(service)
        turn_prompt = next(p for p in FakeClient.prompts if "previous work was wrong" in p)
        snapshot = service.snapshot()[service.session_key("s1")]
        self.assertEqual(snapshot["work_type"], "refactor")
        self.assertEqual(snapshot["area"], "Backend & APIs")
        self.assertEqual(snapshot["complexity"], "everyday")
        self.assertEqual(snapshot["corrections"], 1)
        self.assertIn(W._json("Assistant's previous message (end):\nDone: I changed the chart.")[1:-1], turn_prompt)
        type_prompt = next(p for p in FakeClient.prompts if WORK_TYPE_Q in p)
        self.assertIn(W._json("\n\nArea of this request: Backend & APIs")[1:-1], type_prompt)
        with open(os.path.join(self.tmp.name, "work.sqlite3"), "rb") as handle:
            blob = handle.read()
        self.assertNotIn(b"zebra-kumquat", blob)
        self.assertNotIn(b"still broken", blob)
        self.assertNotIn(b"I changed the chart", blob)
        self.assertNotIn(b"s1", blob.replace(b"s1_", b""))
        self.assertEqual(service.observe("s1", turns(SECRET_TEXT, "no that's wrong, still broken")), 0)

    def test_work_type_hint_is_the_first_order_area_and_the_stored_area_is_corrected(self):
        service, values, clock = make_service(self.tmp.name)
        names = [area["name"] for area in values["areas"]]
        prior = dict.fromkeys(names, 0.1)
        prior["Frontend & UI"] = 0.4

        def respond(prompt):
            if AREA_Q in prompt:
                forward = W._json("Frontend & UI: ")[:-1] in prompt.split("Options:\nA: ", 1)[1][:40]
                return model_response(letter_for(prompt, "Frontend & UI" if forward else "Backend & APIs"))
            return model_response(letter_for(prompt, "feature")) if WORK_TYPE_Q in prompt else model_response("A")

        FakeClient.responder = respond
        with mock.patch.object(W, "AREA_PRIOR", prior), mock.patch.object(W, "AREA_PRIOR_ALPHA", 1.0):
            service.observe("s1", turns("add a tooltip to the spend chart"))
            drain(service)
        area_prompts = [p for p in FakeClient.prompts if AREA_Q in p]
        self.assertEqual(len(area_prompts), 2)
        type_prompt = next(p for p in FakeClient.prompts if WORK_TYPE_Q in p)
        self.assertIn(W._json("\n\nArea of this request: Frontend & UI")[1:-1], type_prompt)
        self.assertEqual(service.snapshot()[service.session_key("s1")]["area"], "Backend & APIs")

    def test_area_hints_work_type_and_is_asked_again_only_as_a_hint(self):
        service, values, clock = make_service(self.tmp.name)
        tags = W.question_tags(values)
        key = service.session_key("s1")
        opener = service._turn_key("s1", 0)
        service.ledger.record_label(opener, "area", key, "Docs & writing", 0.9, tags["area"], "d", clock.now - 50)
        service._labeled, service._failures = service.ledger.labeled_keys()
        service.observe("s1", turns("write the release notes for the new chart"))
        self.assertEqual([item.questions for item in service.queue], [("area", "work_type", "complexity")])
        FakeClient.responder = lambda prompt: model_response(letter_for(prompt, "Frontend & UI")) \
            if AREA_Q in prompt else model_response(letter_for(prompt, "docs")) if WORK_TYPE_Q in prompt \
            else model_response("A")
        drain(service)
        type_prompt = next(p for p in FakeClient.prompts if WORK_TYPE_Q in p)
        self.assertIn(W._json("\n\nArea of this request: Frontend & UI")[1:-1], type_prompt)
        rows = {(r["turn_key"], r["question"]): r for r in service.ledger.session_labels()[0]}
        self.assertEqual(rows[(opener, "area")]["value"], "Docs & writing")  # the hint is not stored
        self.assertEqual(len([p for p in FakeClient.prompts if AREA_Q in p]), 1)  # the hint needs one order
        self.assertEqual(rows[(opener, "work_type")]["value"], "docs")

    def test_pushback_checks_see_what_the_agent_changed_in_its_previous_turn(self):
        service, _, _ = make_service(self.tmp.name)
        session = turns("please fix the chart colors", "i dont see any change")
        session[0]["evidence"] = "Files the agent changed: page.html."
        service.observe("s1", session)
        drain(service)
        checks = [p for p in FakeClient.prompts if is_pushback(p)]
        self.assertEqual(len(checks), 4)
        for prompt in checks:
            self.assertIn(W._json("In its last turn, the assistant changed these files: page.html.")[1:-1], prompt)
            self.assertIn(W._json("The user's earlier request:\nplease fix the chart colors")[1:-1], prompt)

    def test_pushback_evidence_comes_from_the_turn_just_before_even_if_it_was_skipped(self):
        service, values, clock = make_service(self.tmp.name)
        session = turns("please fix the chart colors", "also restyle the legend", "nothing changed")
        session[0]["evidence"] = "Files the agent changed: a.html."
        session[1]["evidence"] = "Files the agent changed: b.html."
        tags = W.question_tags(values)
        key, middle = service.session_key("s1"), service._turn_key("s1", 1)
        for question in ("area", "work_type", "complexity", "correction"):
            service.ledger.record_label(middle, question, key, "x", 0.9, tags[question], "d", clock.now)
        service._labeled, service._failures = service.ledger.labeled_keys()
        service.observe("s1", session)
        last = next(item for item in service.queue if item.turn_key == service._turn_key("s1", 2))
        self.assertIn("changed these files: b.html.", last.state[1])
        self.assertNotIn("a.html", last.state[1])

    def test_a_failed_hint_only_area_records_nothing_and_work_type_still_runs(self):
        service, values, clock = make_service(self.tmp.name)
        tags = W.question_tags(values)
        key, opener = service.session_key("s1"), service._turn_key("s1", 0)
        service.ledger.record_label(opener, "area", key, "Docs & writing", 0.9, tags["area"], "d", clock.now - 50)
        service._labeled, service._failures = service.ledger.labeled_keys()
        service.observe("s1", turns("write the release notes for the new chart"))
        FakeClient.responder = lambda prompt: model_response("Sure") if AREA_Q in prompt \
            else model_response(letter_for(prompt, "docs")) if WORK_TYPE_Q in prompt else model_response("A")
        drain(service)
        type_prompt = next(p for p in FakeClient.prompts if WORK_TYPE_Q in p)
        self.assertNotIn("Area of this request", type_prompt)
        self.assertNotIn((opener, "area"), service._failures)
        rows = {(r["turn_key"], r["question"]): r["value"] for r in service.ledger.session_labels()[0]}
        self.assertEqual((rows[(opener, "area")], rows[(opener, "work_type")]), ("Docs & writing", "docs"))

    def test_the_model_is_unloaded_as_soon_as_the_queue_is_empty(self):
        service, _, _ = make_service(self.tmp.name)
        self.assertEqual(service.step(), 5.0)
        self.assertEqual(FakeClient.unloads, 0)  # nothing was loaded, so nothing to unload
        service.observe("s1", turns("please fix the chart colors"))
        drain(service)
        self.assertEqual((FakeClient.unloads, service.loaded), (1, False))
        service.step()
        self.assertEqual(FakeClient.unloads, 1)  # already unloaded; no repeat request

    def test_ready_backlog_keeps_the_model_loaded_between_refills(self):
        service, _, clock = make_service(self.tmp.name, refill=lambda keys: 0)
        service.observe("s1", turns("please fix the chart colors"))
        service.ledger.upsert_backlog(service.session_key("s2"), clock.now, 3, clock.now - 1)
        service._last_refill = service.monotonic()  # the next refill is still up to 30 s away
        drain(service)
        self.assertEqual((FakeClient.unloads, service.loaded), (0, True))
        service.ledger.remove_backlog(service.session_key("s2"))
        service.step()
        self.assertEqual((FakeClient.unloads, service.loaded), (1, False))

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
            FakeClient.script = [model_response("Sure")] * 3
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
        FakeClient.responder = lambda prompt: stamps.append(clock.now) or model_response("A")
        service.observe("s1", turns("please fix the chart"))
        self.assertEqual(service.step(), 0.0)
        self.assertEqual(len(stamps), 4)  # area in both option orders, then work type and complexity
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        self.assertTrue(all(gap >= 6.0 - 1e-6 for gap in gaps), gaps)

    def test_pause_mid_item_stops_before_the_next_request(self):
        service, values, clock = make_service(self.tmp.name)
        calls = []

        def respond(prompt):
            calls.append(prompt)
            values["paused_until"] = "indefinite"
            return model_response("A")

        FakeClient.responder = respond
        service.observe("s1", turns("hello"))
        service.step()
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(service.queue), 1)

    def test_clear_during_a_request_writes_nothing_afterwards(self):
        service, values, clock = make_service(self.tmp.name)
        FakeClient.responder = lambda prompt: service.clear() or model_response("A")
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
        FakeClient.responder = lambda prompt: model_response("Sure")
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
        self.assertEqual(len(FakeClient.prompts), 4)
        self.assertLess(len(calls), 12)

    def test_multiple_failures_in_one_session_all_keep_a_retry(self):
        service, values, clock = make_service(self.tmp.name)
        FakeClient.responder = lambda prompt: model_response("Sure")
        session = [{"ts": clock.now - 100 + i, "text": f"turn {i}", "model": "m", "context": "done"} for i in range(3)]
        service.observe("s1", session)
        for _ in range(3):
            service.step()
            clock.now += 30
        key = service.session_key("s1")
        service.observe("s1", session)
        self.assertEqual(service.ledger.next_backlog(5, clock.now + 3_600), [key])
        clock.now += W.ITEM_RETRY_DELAYS_S[0] + 1
        FakeClient.responder = lambda prompt: model_response(letter_for(prompt, "debug")) \
            if WORK_TYPE_Q in prompt else model_response("A") if AREA_Q in prompt \
            else model_response("B") if COMPLEXITY_Q in prompt else model_response(letter_for(prompt, "no"))
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
            return model_response("A")

        FakeClient.responder = respond
        service.observe("s1", turns("hello"))
        service.step()
        self.assertEqual(len(calls), 1)
        self.assertEqual((service.state, service.reason), (W.STATE_SETUP, "remote_model"))

    def test_an_item_with_several_failed_questions_adds_one_retry(self):
        service, values, clock = make_service(self.tmp.name)
        FakeClient.responder = lambda prompt: model_response("Sure")
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
            "/api/tags": (200, {"models": [{"name": "token-meter-gemma:latest", "digest": "abc123"}]}),
            "/api/generate": (200, model_response("B")),
        }
        client = W.OllamaClient(self.url, "token-meter-gemma")
        self.assertEqual(client.model_digest(), "abc123")
        self.assertEqual(client.classify("p", 5)["message"]["content"], "B")

    def test_missing_model_and_server_errors_are_classified(self):
        FakeOllama.responses = {"/api/tags": (200, {"models": []}),
                                "/api/generate": (500, {"error": "boom"})}
        client = W.OllamaClient(self.url, "token-meter-gemma")
        with self.assertRaises(W.ClassifierError) as missing:
            client.model_digest()
        self.assertEqual(missing.exception.kind, "setup")
        with self.assertRaises(W.ClassifierError) as failed:
            client.classify("p", 5)
        self.assertEqual(failed.exception.kind, "transport")

    def test_remote_and_cloud_models_are_refused(self):
        for entry in ({"name": "token-meter-gemma:latest", "remote_host": "https://ollama.com"},
                      {"name": "token-meter-gemma:latest", "remote_model": "gpt-oss:120b"},
                      {"name": "token-meter-gemma-cloud"},
                      {"name": "token-meter-gemma:cloud"}):
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
            "_work_turn_days": [day] * turns_}


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
        self.assertEqual(out["areas"][-4:], ["Unclear", "No request text", "Outside history", "Pending"])

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
        sequences = {"a": [(1, True), (2, False)], "b": [(1, False), (2, False), (3, False), (4, False)]}
        out = self.build(rows, labels, corrections_for=lambda ident, n: sequences.get(ident.split("\0")[0], []))
        self.assertNotIn("workstreams", out)
        docs = out["economics"][0]
        # One task per session here: every request inherits its session's labels.
        self.assertEqual((docs["work_type"], docs["tasks"], docs["cost_per_task"]), ("docs", 2, 2.0))
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
        self.assertEqual(cells[("complex", "light")]["requests"], 3)

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
        self.assertEqual((debug["judged_tasks"], debug["resolved_tasks"], debug["resolved_rate"]), (2, 2, 1.0))
        self.assertEqual(debug["cost_per_resolved"], 3.0)
        self.assertNotIn("rework_by_position", out)
        self.assertNotIn("choices", out)
        self.assertNotIn("outcomes", out)
        self.assertNotIn("kpis", out)

    def test_model_fit_marks_best_only_with_enough_samples(self):
        rows = [row(f"x{i}", model="gpt-5.6", turns_=11) for i in range(3)] + \
            [row(f"y{i}", model="mid", turns_=11) for i in range(3)]
        labels = {f"x{i}": {"work_type": "debug", "corrections": 3, "correction_labels": 10} for i in range(3)}
        labels.update({f"y{i}": {"work_type": "debug", "corrections": 1, "correction_labels": 10} for i in range(3)})
        sequences = {f"x{i}": [(n, n in (2, 5, 8)) for n in range(1, 11)] for i in range(3)}
        sequences.update({f"y{i}": [(n, n == 4) for n in range(1, 11)] for i in range(3)})
        fit = self.build(rows, labels, sequences)["model_fit"]
        best = [c for c in fit["cells"] if c["best"]]
        self.assertEqual([(c["model"], c["work_type"]) for c in best], [("mid", "debug")])
        few = {f"z{i}": {"work_type": "debug", "corrections": 0, "correction_labels": 2} for i in range(2)}
        fit = self.build([row("z0", model="gpt-5.6"), row("z1", model="mid")], few,
                         {f"z{i}": [(1, False), (2, False)] for i in range(2)})["model_fit"]
        self.assertFalse(any(c["best"] for c in fit["cells"]))



class OperatingRhythmTests(unittest.TestCase):
    AREAS = DomainTests.AREAS

    def build(self, rows, labels, sequences):
        prices = {"gpt-5.6": 10.0, "cheap": 1.0}
        return domain.build_work_insights(rows, labels, lambda ident: ident.split("\0")[0], self.AREAS, lambda m, p: prices.get(m),
                                          today="2026-09-30", corrections_for=lambda ident, n: sequences.get(ident.split("\0")[0], []))

    def test_ledger_outcomes_by_start_month(self):
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
            "work_type", "complexity", "outcome", "corrections", "labeled_turns", "tags", "area_guess",
            "match_cost", "match_requests"})

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
        rows[0]["_work_turn_days"] = ["2026-08-30", "2026-09-01"]
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
        self.assertLess(script.index('uname -s'), script.index("command -v python3"))

    def test_right_sizing_explains_each_flag(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "page.html"),
                  encoding="utf-8") as handle:
            page = handle.read()
        self.assertNotIn("ooks mismatched", page)
        for marker in ("possible_overspend:'premium model on routine work'",
                       "possible_false_economy:'light model on complex work'",
                       "possible_overthinking:'high effort on routine work'",
                       "<th>Suggestion</th>", "Model suggestions", "Reasoning suggestions"):
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

    def write_raw_settings(self, work):
        with open(self.settings, "w", encoding="utf-8") as handle:
            json.dump({"work_insights": work}, handle)

    def test_legacy_model_names_are_refused_when_saved(self):
        result = meter.set_work_insights_settings({"model": "token-meter-jet:latest"}, self.settings)
        self.assertFalse(result["ok"])
        self.assertIn("no longer supported", result["error"])
        self.assertTrue(meter.set_work_insights_settings({"model": "my-gemma"}, self.settings)["ok"])

    def test_a_retired_model_waits_for_consent_before_the_new_download(self):
        self.write_raw_settings({"enabled": True, "model": "token-meter-jet"})
        job = mock.Mock()
        job.start.return_value = True
        with mock.patch.object(meter, "TOKEN_METER_SETTINGS", self.settings), \
                mock.patch.object(meter, "work_insights_supported", return_value=True), \
                mock.patch.object(meter, "work_setup", return_value=job):
            self.assertTrue(meter.work_model_change_pending())
            self.assertFalse(START_WORK_SETUP(automatic=True))
            job.start.assert_not_called()
            # A pause, a pace change, or edited areas are not agreement to the new download.
            areas = [dict(a) for a in W.DEFAULT_AREAS][:3]
            # The Settings form's Save sends the unchanged model field along with the edits.
            save_form = {"model": "token-meter-gemma", "ollama_url": "http://127.0.0.1:11434", "areas": areas}
            for change in ({"pause": "indefinite"}, {"rate_per_minute": 20}, {"live_notifications": False},
                           {"backfill_days": 30}, {"areas": areas}, {"reset_areas": True}, save_form):
                self.assertTrue(meter.set_work_insights_settings(change, self.settings)["ok"], change)
                self.assertTrue(meter.work_model_change_pending(), change)
                self.assertEqual(meter.work_insights_settings(self.settings)["model"], "token-meter-gemma")
            self.assertFalse(START_WORK_SETUP(automatic=True))
            job.start.assert_not_called()
            # Choosing a different model is.
            self.assertTrue(meter.set_work_insights_settings({"model": "my-gemma"}, self.settings)["ok"])
            self.assertFalse(meter.work_model_change_pending())
            self.assertTrue(START_WORK_SETUP(automatic=True))
            job.start.assert_called_once()

    def test_a_saved_winnow_model_also_waits_for_consent(self):
        self.write_raw_settings({"enabled": True, "model": "token-meter-winnow"})
        job = mock.Mock()
        job.start.return_value = True
        with mock.patch.object(meter, "TOKEN_METER_SETTINGS", self.settings), \
                mock.patch.object(meter, "work_insights_supported", return_value=True), \
                mock.patch.object(meter, "work_setup", return_value=job):
            self.assertEqual(meter.work_insights_settings(self.settings)["model"], "token-meter-gemma")
            self.assertTrue(meter.work_model_change_pending())
            self.assertFalse(START_WORK_SETUP(automatic=True))
            job.start.assert_not_called()
            refused = meter.set_work_insights_settings({"model": "token-meter-winnow"}, self.settings)
            self.assertFalse(refused["ok"])
            self.assertIn("no longer supported", refused["error"])

    def test_turning_work_insights_on_agrees_to_the_new_model(self):
        self.write_raw_settings({"enabled": False, "model": "token-meter-jet"})
        with mock.patch.object(meter, "TOKEN_METER_SETTINGS", self.settings), \
                mock.patch.object(meter, "work_insights_supported", return_value=True):
            self.assertTrue(meter.work_model_change_pending())
            self.assertTrue(meter.set_work_insights_settings({"enabled": True}, self.settings)["ok"])
            self.assertFalse(meter.work_model_change_pending())

    def test_pause_until_tomorrow_is_six_am(self):
        import datetime
        now = datetime.datetime(2026, 9, 30, 22, 15).timestamp()
        until = datetime.datetime.fromtimestamp(meter._pause_until("tomorrow", now))
        self.assertEqual((until.day, until.hour, until.minute), (1, 6, 0))

    def test_work_accepts_the_project_key_other_pages_filter_by(self):
        rows = (row("a", project="/Users/me/code/alpha"), row("b", project="/Users/me/code/beta"),
                row("c", project="~/.codex/sessions"))
        labels = {"/Users/me/code/alpha": "alpha · 1a2b", "/Users/me/code/beta": "beta · 3c4d"}
        with mock.patch.object(meter, "TOKEN_METER_SETTINGS", self.settings), \
                mock.patch.object(meter, "delivery_project_label", side_effect=lambda key: labels.get(key, "")), \
                mock.patch.dict(meter._xsess, {"data": {"ok": True}, "internal_rows": rows}):
            by_key, key_status = meter.work_insights_state("6", project="/Users/me/code/alpha")
            by_label, label_status = meter.work_insights_state("6", project="alpha · 1a2b")
            other, other_status = meter.work_insights_state("6", project=meter.OTHER_LOCAL_SESSIONS_PROJECT)
        self.assertEqual((key_status, label_status, other_status), (200, 200, 200))
        self.assertEqual(by_key["insights"], by_label["insights"])
        self.assertEqual(by_key["insights"]["coverage"]["sessions"], 1)
        self.assertEqual(other["insights"]["coverage"]["sessions"], 1)
        self.assertNotIn("/Users/me", json.dumps(by_key))

    def test_work_payload_is_bounded_and_text_free(self):
        rows = (row("a"),)
        with mock.patch.object(meter, "TOKEN_METER_SETTINGS", self.settings), \
                mock.patch.dict(meter._xsess, {"data": {"ok": True}, "internal_rows": rows}):
            payload, status = meter.work_insights_state("6")
            self.assertEqual(status, 200)
            self.assertEqual(meter.work_insights_state("7")[1], 400)
            for choice in ("1d", "7d", "30d", "90d"):
                short, short_status = meter.work_insights_state(choice)
                self.assertEqual((short_status, short["insights"]["grain"]), (200, "day"), choice)
            for bad in ("14d", "2d", "6m", "1d; drop"):
                self.assertEqual(meter.work_insights_state(bad)[1], 400, bad)
            self.assertEqual(meter.work_insights_state("6", project="nope")[1], 404)
        encoded = json.dumps(payload)
        self.assertNotIn("_day_cost", encoded)
        self.assertNotIn("_work_turn_days", encoded)
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
        for marker in ("id=view-work", ">Where the spend went</h2>", "<h3>How sessions ended</h3>",
                       "Pushback over time", ">Cost per resolved task</h2>", "split by request", "left to label · charts update as they finish",
                       "Model choices", "id=w-scorecard", ">Right-sizing</h2>",
                       "id=w-tier-mix", "id=w-effort-mix", "id=w-suggest-cards", "Possible saving",
                       "<option value=1d>1 day</option><option value=7d>1 week</option><option value=30d>1 month</option>",
                       "id=w-module-tags", "id=w-module-rhythm", "id=w-opportunities",
                       "text goes only to Ollama on this machine", "'var(--w-pending)'", "'var(--w-unclear)'"):
            self.assertTrue(marker in self.page, marker)
        self.assertLess(self.page.index("id=w-module-sizing"), self.page.index("id=w-module-allocation"))
        for removed in ("Value by kind of work", "Spend in, outcomes out", "id=w-headlines", "Better fit by kind of work",
                        "id=w-kpis", "id=w-highlights",
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
        for marker in ("--w1:#079bc2; --w2:#c17a01; --w3:#c36b95; --w4:#af851e; --w5:#9979cd; --w6:#d66555; --w7:#5f8adf; --w8:#05a386;",
                       "--w-tier-light:#05a386; --w-tier-standard:#5f8adf; --w-tier-premium:#c17a01;",
                       "--w-accepted:var(--good); --w-recovered:var(--warn); --w-ended:var(--bad);",
                       ".workLine{fill:none;stroke:var(--cyan)",
                       ".workGrid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:10px;align-items:stretch}"):
            self.assertIn(marker, self.page)
        self.assertNotIn("--w1:#3987e5", self.page)

    def test_sizing_and_scorecard_numbers_match_their_labels(self):
        for marker in ("biggest possible saving",
                       "slice(rework.grain==='day'?-31:-26)",
                       "lowestCost=ranked.length>=2?",
                       "sessions started in this period",
                       "No model changes suggested for this period."):
            self.assertIn(marker, self.page)
        self.assertNotIn("rows.reduce((sum,item)=>sum+(item.spend||0),0)", self.page)

    def test_settings_card_explains_what_text_is_read(self):
        self.assertIn("id=work-insights-settings", self.page)
        self.assertIn("reads the prompts you typed, the last few lines of the assistant reply", self.page)
        self.assertIn("the names of the files the agent changed for each request", self.page)
        self.assertIn("It never reads file contents or tool output", self.page)
        self.assertIn("cloud models are refused", self.page)
        self.assertIn("the names of files you uploaded", self.page)
        self.assertIn("Token Meter stores labels and counts, never text, file names, or commands", self.page)
        self.assertIn("Turning this on sets everything up in the background", self.page)
        self.assertIn("'/work-insights/setup'", self.page)

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

    def test_setup_script_is_executable_and_imports_the_pinned_gguf(self):
        path = os.path.join(self.root, "scripts", "setup-work-classifier")
        self.assertTrue(os.access(path, os.X_OK))
        with open(path, encoding="utf-8") as handle:
            script = handle.read()
        self.assertIn("exec python3 -m token_meter.services.work_setup", script)
        self.assertNotIn("sudo", script)
        with open(os.path.join(self.root, "token_meter", "services", "work_setup.py"), encoding="utf-8") as handle:
            module = handle.read()
        self.assertIn('[cli, "create", model, "-f", os.path.join(folder, "Modelfile")]', module)
        self.assertNotIn('"-q"', module)
        self.assertIn('MODEL_COMMIT = "4b4a2c1d584be7264f87aac328a1bc739ce81b6c"', module)
        self.assertNotIn("sudo", module)


class AppIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for name in ("start_work_setup", "stop_managed_ollama"):
            patcher = mock.patch.object(meter, name, return_value=False)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(meter, "_work_setup_instance", None)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.dict(os.environ, {"TOKEN_METER_OLLAMA_DIR": os.path.join(self.tmp.name, "ollama"),
                                               "TOKEN_METER_LAUNCH_AGENTS_DIR": os.path.join(self.tmp.name, "agents")})
        patcher.start()
        self.addCleanup(patcher.stop)
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
                meter.capture_work_turns([{"ts": 1, "text": SECRET_TEXT, "model": "m"}])
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

    def test_setup_route_records_consent_and_status_flags_the_model_change(self):
        with open(self.settings, "w", encoding="utf-8") as handle:
            json.dump({"work_insights": {"enabled": True, "model": "token-meter-jet"}}, handle)
        job = mock.Mock()
        job.status.return_value = {"state": "idle"}
        with mock.patch.object(meter, "TOKEN_METER_SETTINGS", self.settings), \
                mock.patch.object(meter, "TOKEN_METER_WORK_INSIGHTS_DB", self.db), \
                mock.patch.object(meter, "_work_service_instance", None), \
                mock.patch.object(meter, "work_insights_supported", return_value=True), \
                mock.patch.object(meter, "work_setup", return_value=job):
            self.assertTrue(meter.work_insights_status()["model_change"])
            status, payload = self.post("/work-insights/setup", {"start": True})
            self.assertEqual(status, 200)
            self.assertFalse(payload["status"]["model_change"])
        with open(self.settings, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["work_insights"]["model"], "token-meter-gemma")
        meter.start_work_setup.assert_called()

    def test_a_settings_save_only_starts_setup_through_the_consent_gate(self):
        with open(self.settings, "w", encoding="utf-8") as handle:
            json.dump({"work_insights": {"enabled": True, "model": "token-meter-jet"}}, handle)
        with mock.patch.object(meter, "TOKEN_METER_SETTINGS", self.settings), \
                mock.patch.object(meter, "TOKEN_METER_WORK_INSIGHTS_DB", self.db), \
                mock.patch.object(meter, "_work_service_instance", None), \
                mock.patch.object(meter, "work_insights_supported", return_value=True):
            meter.start_work_setup.reset_mock()
            status, _payload = self.post("/settings/work-insights", {"rate_per_minute": 20})
            self.assertEqual(status, 200)
            meter.start_work_setup.assert_called_once_with(automatic=True)
            self.assertTrue(meter.work_model_change_pending())

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
            for name in ("No request text", "Outside history", "Pending", "Unclear"):
                self.assertEqual(meter.work_sessions_state({"months": ["6"], "area": [name]})[1], 200, name)
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
        self.assertEqual(set(tags), {"area", "area_guess", "work_type", "complexity", "corrections", "labeled_turns"})
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
        answers = {WORK_TYPE_Q: "debug", AREA_Q: "Backend & APIs", COMPLEXITY_Q: "0"}

        def respond(prompt):
            if is_pushback(prompt):
                return model_response(letter_for(prompt, "no"))
            key = next(v for k, v in answers.items() if k in prompt)
            return model_response(letter_for(prompt, key))

        FakeClient.responder = respond
        service.observe("s1", turns("fix it", "ok"))
        drain(service)
        key = service.session_key("s1")
        self.assertEqual(service.snapshot()[key]["work_type"], "debug")
        answers.update({WORK_TYPE_Q: "feature", AREA_Q: "Developer tooling & agents", COMPLEXITY_Q: "2"})
        clock.now += 60
        service.observe("s1", turns("fix it", "ok", "please build a new agent tool for the release flow"))
        drain(service)
        entry = service.snapshot()[key]
        self.assertEqual((entry["work_type"], entry["area"], entry["complexity"]),
                         ("feature", "Developer tooling & agents", "complex"))

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

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_right_sizing_cards_show_models_reasoning_and_pushback(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        insights = {
            "recommendations": [
                {"kind": "family_upgrade", "model": "gpt-5.6-sol", "to_model": "gpt-6-sol", "runtime": "Codex",
                 "tasks": 438, "spend": 3672.5, "saving": 1836.2, "from_rate": 0.88, "to_rate": 0.875,
                 "from_price": 20, "to_price": 10},
                {"kind": "switch_model", "model": "gpt-5.6-sol", "to_model": "gpt-5.6-terra", "runtime": "Codex",
                 "complexity": "everyday", "work_type": "feature", "tasks": 73, "spend": 932.7, "saving": 548.4,
                 "from_rate": 0.80, "to_rate": 0.91, "from_cost": 15.7, "to_cost": 4.5},
                {"kind": "family_upgrade", "model": "claude-opus-4-8", "to_model": "claude-opus-5-5", "runtime": "Claude",
                 "tasks": 120, "spend": 670.2, "saving": 134.0, "from_rate": 0.86, "to_rate": 0.91,
                 "from_price": 25, "to_price": 20},
                {"kind": "premium_routine", "complexity": "routine", "requests": 313, "spend": 230.9, "saving": 128.3,
                 "from_models": [{"model": "gpt-5.6-sol"}], "to_models": [{"model": "gpt-5.6-terra", "runtime": "Codex"}]},
                {"kind": "effort_routine", "complexity": "routine", "effort": "xhigh", "requests": 153, "spend": 117.9},
                {"kind": "effort_routine", "complexity": "routine", "effort": "max", "requests": 2, "spend": 0.2},
            ],
            "right_sizing": {"effort": {"cells": [
                {"complexity": "routine", "effort": "xhigh", "spend": 117.9},
                {"complexity": "routine", "effort": "high", "spend": 45.4},
                {"complexity": "routine", "effort": "medium", "spend": 16.1},
                {"complexity": "routine", "effort": "low", "spend": 0.6},
                {"complexity": "complex", "effort": "xhigh", "spend": 900.0}]}},
            "rework": {"grain": "week", "overall": {"rate": 0.149, "samples": 1989, "few_samples": False},
                       "weekly": [{"week": "2026-09-07", "rate": 0.14, "samples": 260},
                                  {"week": "2026-09-14", "rate": 0.09, "samples": 97},
                                  {"week": "2026-10-05", "rate": 0.06, "samples": 3}],
                       "models": [{"model": "gpt-5.6-terra", "runtime": "Codex", "rate": 0.18, "samples": 159},
                                  {"model": "claude-opus-5", "runtime": "Claude", "rate": 0.13, "samples": 31},
                                  {"model": "unknown-model", "runtime": "Codex", "rate": 0.0, "samples": 28},
                                  {"model": "tiny", "runtime": "Codex", "rate": 0.01, "samples": 5},
                                  {"model": "", "runtime": "Codex", "rate": 0.0, "samples": 40}]},
        }
        script = f"""
const fs=require('fs');const page=fs.readFileSync({json.dumps(os.path.join(root, 'page.html'))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
const nodes={{}};const $=id=>nodes[id]||(nodes[id]={{innerHTML:''}});
const esc=v=>String(v??'').replace(/[&<>"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}})[c]);
const money=v=>'$'+Number(v).toFixed(2);const WORK_TYPE_LABELS={{}};const workMonthName=month=>month;
eval(page.slice(page.indexOf('const WORK_MODEL_SUGGESTIONS='),page.indexOf('function workModelSwap(')).replace(/^const /,'var '));
eval(['workPercent','workSuggestion','workModelSwap','workSparkline','renderWorkSuggestCards'].map(extract).join('\\n'));
const out={{}};
renderWorkSuggestCards({json.dumps(insights)});out.main=nodes['w-suggest-cards'].innerHTML;
const withModels=models=>Object.assign({json.dumps(insights)},{{rework:Object.assign({json.dumps(insights)}.rework,{{models}})}});
renderWorkSuggestCards(withModels([{{model:'claude-opus-4-8',runtime:'Claude',rate:0.139,samples:409}},{{model:'gpt-5.6-sol',runtime:'Codex',rate:0.157,samples:1181}}]));out.close=nodes['w-suggest-cards'].innerHTML;
renderWorkSuggestCards(withModels([{{model:'claude-opus-4-8',runtime:'Claude',rate:0.08,samples:400}},{{model:'gpt-5.6-terra',runtime:'Codex',rate:0.25,samples:300}},{{model:'gpt-5.6-sol',runtime:'Codex',rate:0.15,samples:1100}}]));out.clear=nodes['w-suggest-cards'].innerHTML;
process.stdout.write(JSON.stringify(out));
"""
        outs = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
        html = outs["main"]
        cards = html.split("<div class=workSuggestCard>")[1:]
        # A 14% vs 16% gap on these samples is noise: no chips. A 8% vs 25% gap is real: chips show.
        self.assertNotIn("Least pushback", outs["close"])
        self.assertIn("Least pushback</span><b title=\"claude-opus-4-8 · Claude\">opus-4-8 <s>8%</s>", outs["clear"])
        self.assertIn("Most pushback</span><b title=\"gpt-5.6-terra · Codex\">gpt-5.6-terra <s>25%</s>", outs["clear"])
        self.assertEqual(len(cards), 3)
        model, reasoning, pushback = cards
        self.assertIn("$1,836<small>biggest possible saving · 4 suggestions", model)
        self.assertEqual(model.count("class=workDrillBtn"), 3)  # the top three by saving
        self.assertIn("gpt-5.6-sol<b>→</b>gpt-6-sol", model)
        self.assertIn("opus-4-8<b>→</b>opus-5-5", model)
        self.assertNotIn("premium models", model)  # fourth by saving, listed under See all
        self.assertIn("+1 more · savings can overlap", model)
        self.assertIn("155<small>routine requests ran at xhigh or max effort", reasoning)
        self.assertIn("&quot;effort&quot;:&quot;xhigh,max&quot;", reasoning)  # the drill filter, not the label
        self.assertIn("xhigh 66%", reasoning)  # routine spend only; complex spend is left out
        self.assertIn("other 0%", reasoning)
        self.assertIn("$118 of $180 routine spend with effort recorded", reasoning)
        self.assertIn("15%<small>of 1,989 follow-ups pushed back", pushback)
        self.assertEqual(pushback.count("<text "), 2)  # weeks under 20 follow-ups are left off the trend
        # Only one model has 50+ judged follow-ups here, so there is no least/most to show.
        self.assertNotIn("Least pushback", pushback)
        self.assertNotIn("unknown-model", pushback)

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
            if WORK_TYPE_Q in prompt:
                return model_response(letter_for(prompt, "feature" if real else "ops"))
            if AREA_Q in prompt:
                return model_response(letter_for(prompt, "Developer tooling & agents" if real else "Non-code"))
            if COMPLEXITY_Q in prompt:
                return model_response(letter_for(prompt, "2" if real else "0"))
            return model_response(letter_for(prompt, "no"))

        FakeClient.responder = respond
        return service, clock

    def assert_real_opener(self, service):
        entry = service.snapshot()[service.session_key("s1")]
        self.assertEqual((entry["work_type"], entry["area"], entry["complexity"]),
                         ("feature", "Developer tooling & agents", "complex"))

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
            for question, value in (("work_type", "ops"), ("area", "Non-code"), ("complexity", "routine")):
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
        FakeClient.responder = lambda prompt: (model_response(letter_for(prompt, "yes"))
                                               if is_pushback(prompt) and "wrong!!" in prompt.split("User's latest")[-1]
                                               else base(prompt))
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
        self.assertEqual((entry["work_type"], entry["area"]), ("ops", "Non-code"))

    def test_a_permanently_failed_opener_reads_unclear_not_the_fallback(self):
        service, clock = self.service()
        service.observe("s1", turns("hi"))
        drain(service)
        self.assertEqual(service.snapshot()[service.session_key("s1")]["work_type"], "ops")
        base = FakeClient.responder
        FakeClient.responder = lambda prompt: model_response("Sure") if self.RELEASE in prompt else base(prompt)
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
        self.assertEqual([b["sessions_total"] for b in out["allocation"]], [0, 0, 1])
        found = domain.find_sessions(rows, {}, lambda ident: ident.split("\0")[0], self.AREAS, lambda m, p: None,
                                     {}, months=3, today="2026-09-30")
        self.assertEqual([s["id"] for s in found["sessions"]], ["sep"])

    def test_months_without_data_are_empty_not_replaced_by_older_data(self):
        out = self.build([row("jun", day="2026-06-10")], {}, "2026-09-30", months=3)
        self.assertEqual(out["months"], ["2026-07", "2026-08", "2026-09"])
        self.assertEqual(out["coverage"]["sessions"], 0)
        # Without a today, the window ends at the latest data month; All history keeps data months only.
        self.assertEqual(self.build([row("jun", day="2026-06-10")], {}, "", months=3)["months"],
                         ["2026-04", "2026-05", "2026-06"])
        everything = self.build([row("a", day="2026-02-10"), row("b", day="2026-06-10")], {}, "2026-09-30", months=0)
        self.assertEqual(everything["months"], ["2026-02", "2026-06"])

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
        self.assertEqual(domain.parse_period("90d"), ("day", 90))
        self.assertEqual(domain.parse_period("6"), ("month", 6))
        self.assertEqual(domain.parse_period("0"), ("month", 0))
        self.assertEqual(domain.parse_period("month"), ("day", "month"))
        for bad in ("2d", "14d", "5", "-1", "d", "", "7 d", "months", None):
            self.assertIsNone(domain.parse_period(bad))

    def test_week_uses_seven_daily_buckets(self):
        rows = [row("today", day="2026-09-30", cost=3.0), row("monday", day="2026-09-28"),
                row("lastweek", day="2026-09-21", cost=5.0), row("old", day="2026-08-01")]
        out = self.build(rows, {}, "7d")
        self.assertEqual(out["grain"], "day")
        self.assertEqual(out["months"], [f"2026-09-{d}" for d in range(24, 31)])
        by_day = {b["month"]: b for b in out["allocation"]}
        self.assertEqual(by_day["2026-09-30"]["spend_total"], 3.0)
        self.assertTrue(by_day["2026-09-30"]["partial"])
        self.assertEqual(by_day["2026-09-25"]["spend_total"], 0)
        self.assertEqual(out["coverage"]["sessions"], 2)
        self.assertEqual(out["rework"]["grain"], "day")

    def test_one_day_is_today(self):
        out = self.build([row("a", day="2026-09-30"), row("b", day="2026-09-29")], {}, "1d")
        self.assertEqual(out["months"], ["2026-09-30"])
        self.assertEqual(out["coverage"]["sessions"], 1)

    def test_this_month_is_daily_from_the_first_through_today(self):
        rows = [row("today", day="2026-09-30"), row("first", day="2026-09-01"), row("august", day="2026-08-31")]
        out = self.build(rows, {}, "month")
        self.assertEqual(out["grain"], "day")
        self.assertEqual(out["months"], [f"2026-09-{d:02d}" for d in range(1, 31)])
        self.assertEqual(out["coverage"]["sessions"], 2)
        early = self.build([], {}, "month", today="2026-10-01")
        self.assertEqual(early["months"], ["2026-10-01"])
        found = domain.find_sessions(rows, {}, lambda ident: ident.split("\0")[0], self.AREAS, lambda m, p: 10.0,
                                     {}, months="month", today="2026-09-30")
        self.assertEqual(sorted(s["id"] for s in found["sessions"]), ["first", "today"])

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
        self.assertEqual((codex["tasks"], codex["spend"], codex["judged_tasks"]), (2, 6.0, 2))
        self.assertEqual((codex["resolved_rate"], codex["cost_per_resolved"]), (0.5, 4.0))
        self.assertEqual(codex["rework"]["rate"], 0.5)
        # The table and the Pushback card credit each request to the model that answered it, so they agree.
        rework_models = {(m["model"], m["runtime"]): m for m in out["rework"]["models"]}
        self.assertEqual((codex["rework"]["rate"], codex["rework"]["samples"]),
                         (rework_models[("gpt-5.6", "Codex")]["rate"], rework_models[("gpt-5.6", "Codex")]["samples"]))
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

    def build(self, rows, labels, prices, sequences=None):
        return domain.build_work_insights(rows, labels, lambda ident: ident.split("\0")[0], DomainTests.AREAS,
                                          lambda m, p: prices.get(m), today="2026-09-30",
                                          corrections_for=lambda ident, n: (sequences or {}).get(ident.split("\0")[0], []))

    def test_light_models_on_high_impact_work_count_with_unknown_tier_and_effort_left_out(self):
        rows = [row("hi", model="cheap", cost=4.0, turns_=21), row("nontier", model="mystery", cost=7.0),
                row("std", model="mid", cost=2.0, turns_=21), row("prem", model="gpt-5.6", cost=3.0, turns_=21)]
        sequences = {"hi": [(n, n <= 9) for n in range(1, 21)],
                     "std": [(n, False) for n in range(1, 21)], "prem": [(n, False) for n in range(1, 21)]}
        rows[1]["reasoning_effort"] = "xhigh"
        labels = {"hi": {"area": "Personal", "complexity": "high_impact", "corrections": 9, "correction_labels": 20},
                  "nontier": {"area": "Personal", "complexity": "everyday"},
                  "std": {"area": "Personal", "complexity": "complex", "corrections": 0, "correction_labels": 20},
                  "prem": {"area": "Personal", "complexity": "complex", "corrections": 0, "correction_labels": 20}}
        out = self.build(rows, labels, {"cheap": 1.0, "mid": 4.0, "gpt-5.6": 10.0}, sequences)
        self.assertIn("light_complex", {item["kind"] for item in out["opportunities"]})
        flagged = out["right_sizing"]["flagged"]
        self.assertEqual((flagged["sessions"], flagged["spend"], flagged["labeled_spend"]), (1, 4.0, 16.0))

    def test_no_complexity_labels_reports_no_share(self):
        flagged = self.build([row("a")], {"a": {"area": "Personal"}}, {"gpt-5.6": 10.0})["right_sizing"]["flagged"]
        self.assertEqual((flagged["sessions"], flagged["spend"], flagged["share"]), (0, 0, None))


class TagHighlightRhythmTests(unittest.TestCase):
    AREAS = DomainTests.AREAS
    PRICES = {"gpt-5.6": 10.0, "cheap": 1.0, "mid": 4.0}

    def build(self, rows, labels, sequences=None, **kwargs):
        return domain.build_work_insights(
            rows, labels, lambda ident: ident.split("\0")[0], self.AREAS, lambda m, p: self.PRICES.get(m),
            today="2026-09-30", corrections_for=lambda ident, n: (sequences or {}).get(ident.split("\0")[0], []),
            **kwargs)

    def find(self, rows, labels, filters, sequences=None):
        return domain.find_sessions(
            rows, labels, lambda ident: ident.split("\0")[0], self.AREAS, lambda m, p: self.PRICES.get(m), filters,
            corrections_for=lambda ident, n: (sequences or {}).get(ident.split("\0")[0], []), today="2026-09-30")

    def population(self):
        rows = []
        for i in range(10):
            r = row(f"s{i}", cost=float(i + 1), day=f"2026-09-{i + 1:02d}")
            r["duration_s"], r["duration_available"] = 600 * (i + 1), True
            rows.append(r)
        rows[9]["duration_s"] = 4 * 3600
        return rows

    def test_relative_tags_use_period_thresholds(self):
        rows = self.population()
        out = self.build(rows, {})
        tags = {t["tag"]: t for t in out["tags"]["items"]}
        self.assertEqual(tags["big_spend"]["sessions"], 1)
        self.assertEqual(tags["big_spend"]["spend"], 10.0)
        self.assertEqual(tags["marathon"]["sessions"], 1)
        self.assertEqual((out["tags"]["thresholds"]["marathon_s"], out["tags"]["thresholds"]["big_spend"]), (5400, 9.0))
        few = self.build(rows[:3], {})
        self.assertNotIn("big_spend", {t["tag"] for t in few["tags"]["items"]})

    def test_fit_outcome_and_team_tags(self):
        routine, complex_light, parent = row("r", model="gpt-5.6"), row("c", model="cheap"), row("p", model="mid")
        parent["_agent_records"] = [{"id": "root", "parent_id": None}, {"id": "k0", "parent_id": "root"}]
        children = []
        for n in (1, 2):
            child = row(f"child{n}", model="mid")
            child["_agent_records"] = [{"id": f"k{n}", "parent_id": "root" if n == 1 else "k1"}]
            children.append(child)
        labels = {"r": {"complexity": "routine", "correction_labels": 2, "corrections": 0},
                  "c": {"complexity": "complex", "correction_labels": 2, "corrections": 1}}
        sequences = {"r": [(1, False), (2, False)], "c": [(1, True), (2, False)]}
        out = self.find([routine, complex_light, parent, *children], labels, {}, sequences)
        tags = {s["id"]: s["tags"] for s in out["sessions"]}
        self.assertEqual(set(tags), {"r", "c", "p"})
        self.assertIn("overkill", tags["r"])
        self.assertEqual(tags["c"], ["underpowered", "rescued"])
        self.assertIn("team", tags["p"])
        only = self.find([routine, complex_light, parent, *children], labels, {"tag": "team"}, sequences)
        self.assertEqual([s["id"] for s in only["sessions"]], ["p"])

    def test_rhythm_counts_starts_by_weekday_hour_and_band(self):
        a, b = row("a", day="2026-09-28"), row("b", day="2026-09-28", cost=3.0)
        a["start"], b["start"] = "2026-09-28 09:15", "2026-09-28 23:40"
        rhythm = self.build([a, b], {"b": {"corrections": 1, "correction_labels": 2}})["rhythm"]
        self.assertEqual(rhythm["sessions"], 2)
        self.assertEqual([(c["weekday"], c["hour"], c["sessions"]) for c in rhythm["cells"]], [(0, 9, 1), (0, 23, 1)])
        bands = {b["band"]: b for b in rhythm["bands"]}
        self.assertEqual(bands["evening"]["spend"], 3.0)
        self.assertEqual(bands["evening"]["pushback"]["rate"], 0.5)
        self.assertIsNone(bands["night"]["pushback"])

    def sessions(self, model, count, cost, complexity="everyday", work_type="debug", prefix=None, runtime="Codex"):
        rows, labels, sequences = [], {}, {}
        for i in range(count):
            key = f"{prefix or model}{i}"
            rows.append(row(key, model=model, cost=cost, runtime=runtime))
            labels[key] = {"work_type": work_type, "complexity": complexity, "correction_labels": 2, "corrections": 0}
            sequences[key] = [(1, False), (2, False)]
        return rows, labels, sequences

    def combine(self, *parts):
        rows, labels, sequences = [], {}, {}
        for r, l, q in parts:
            rows += r
            labels.update(l)
            sequences.update(q)
        return rows, labels, sequences

    def test_recommendations_suggest_a_cheaper_model_that_resolves_as_often(self):
        rows, labels, sequences = self.combine(self.sessions("gpt-5.6", 12, 10.0), self.sessions("cheap", 10, 2.0))
        recs = self.build(rows, labels, sequences)["recommendations"]
        switch = next(r for r in recs if r["kind"] == "switch_model")
        self.assertEqual((switch["model"], switch["to_model"], switch["work_type"], switch["complexity"]),
                         ("gpt-5.6", "cheap", "debug", "everyday"))
        self.assertEqual(switch["saving"], 8.0 * 12)
        # A cheap model that only handled routine requests is not compared with everyday ones.
        for key in labels:
            if key.startswith("cheap"):
                labels[key]["complexity"] = "routine"
        recs = self.build(rows, labels, sequences)["recommendations"]
        self.assertFalse(any(r["kind"] == "switch_model" for r in recs))

    def test_switch_needs_enough_judged_sessions_and_a_cheaper_per_token_model(self):
        few = self.combine(self.sessions("gpt-5.6", 12, 10.0), self.sessions("cheap", 4, 2.0))
        self.assertFalse(any(r["kind"] == "switch_model" for r in self.build(*few)["recommendations"]))
        thin_baseline = self.combine(self.sessions("gpt-5.6", 8, 10.0), self.sessions("cheap", 6, 2.0))
        self.assertFalse(any(r["kind"] == "switch_model" for r in self.build(*thin_baseline)["recommendations"]))
        # "mid" costs less per resolved session here only because its sessions were small; it is not cheaper per token.
        pricier = self.combine(self.sessions("cheap", 12, 10.0), self.sessions("mid", 10, 2.0))
        self.assertFalse(any(r["kind"] == "switch_model" for r in self.build(*pricier)["recommendations"]))

    def test_routine_work_is_never_pointed_at_a_premium_model(self):
        self.PRICES = dict(self.PRICES, premium2=9.0, six=6.0)
        parts = self.combine(self.sessions("gpt-5.6", 12, 10.0, "routine"), self.sessions("premium2", 10, 2.0, "routine"),
                             self.sessions("cheap", 1, 1.0, "routine"), self.sessions("mid", 1, 1.0, "routine"),
                             self.sessions("six", 1, 1.0, "routine"))
        out = self.build(*parts)
        tiers = {(c["complexity"], c["tier"]) for c in out["right_sizing"]["cells"] if c["requests"]}
        self.assertIn(("routine", "premium"), tiers)
        recs = out["recommendations"]
        self.assertFalse(any(r["kind"] == "switch_model" and r["to_model"] == "premium2" for r in recs))

    def test_family_upgrade_suggests_the_cheaper_version_of_the_same_model(self):
        self.PRICES = {"claude-opus-4-8": 25.0, "claude-opus-5-5": 20.0, "claude-sonnet-5": 10.0}
        parts = self.combine(self.sessions("claude-opus-4-8", 10, 10.0, runtime="Claude"),
                             self.sessions("claude-opus-5-5", 5, 10.0, runtime="Claude", work_type="feature"),
                             self.sessions("claude-sonnet-5", 5, 1.0, runtime="Claude"))
        recs = self.build(*parts)["recommendations"]
        family = [r for r in recs if r["kind"] == "family_upgrade"]
        self.assertEqual([(r["model"], r["to_model"]) for r in family], [("claude-opus-4-8", "claude-opus-5-5")])
        self.assertAlmostEqual(family[0]["saving"], 100.0 * (1 - 20 / 25))
        thin = self.combine(self.sessions("claude-opus-4-8", 6, 10.0, runtime="Claude"),
                            self.sessions("claude-opus-5-5", 5, 10.0, runtime="Claude"))
        self.assertFalse(any(r["kind"] == "family_upgrade" for r in self.build(*thin)["recommendations"]))

    def test_family_upgrade_never_points_at_an_older_version(self):
        self.PRICES = {"gpt-5.6": 20.0, "gpt-5.4": 15.0}
        parts = self.combine(self.sessions("gpt-5.6", 10, 10.0), self.sessions("gpt-5.4", 10, 10.0))
        self.assertFalse(any(r["kind"] == "family_upgrade" for r in self.build(*parts)["recommendations"]))

    def test_model_family_ignores_versions_and_vendor_prefixes(self):
        self.assertEqual(domain.model_family("claude-opus-4-8"), domain.model_family("claude-opus-5-5"))
        self.assertEqual(domain.model_family("gpt-5.6-sol"), domain.model_family("gpt-6-sol"))
        self.assertNotEqual(domain.model_family("gpt-5.6-sol"), domain.model_family("gpt-5.6-terra"))
        self.assertEqual(domain.model_family("anthropic.claude-haiku-4-5-20251001-v1:0"),
                         domain.model_family("claude-haiku-4-5-20251001"))
        self.assertEqual(domain.model_family("us.anthropic.claude-opus-4-8-v1:0"), "claude-opus")
        self.assertEqual(domain.model_family("claude-opus-4-8[1m]"), "claude-opus")
        self.assertEqual((domain.model_family("claude-opus-4-8@20250101"), domain.model_version("claude-opus-4-8@20250101")),
                         ("claude-opus", (4, 8)))
        self.assertEqual(domain.model_version("claude-opus-4-8[1m]"), (4, 8))
        self.assertEqual(domain.model_version("claude-haiku-4-5-20251001"), (4, 5))
        self.assertGreater(domain.model_version("claude-opus-5"), domain.model_version("claude-opus-4-8"))
        self.assertLess(domain.model_version("gpt-5.4"), domain.model_version("gpt-5.6"))

    def test_routine_on_premium_names_the_models_to_move_from_and_to(self):
        parts = self.combine(self.sessions("gpt-5.6", 4, 5.0, "routine"), self.sessions("mid", 2, 1.0),
                             self.sessions("cheap", 2, 1.0))
        item = next(r for r in self.build(*parts)["recommendations"] if r["kind"] == "premium_routine")
        self.assertEqual(item["from_models"], [{"model": "gpt-5.6", "runtime": "Codex"}])
        self.assertEqual(item["to_models"], [{"model": "mid", "runtime": "Codex"}])
        other_app = self.combine(self.sessions("gpt-5.6", 4, 5.0, "routine"),
                                 self.sessions("mid", 2, 1.0, runtime="Claude"), self.sessions("cheap", 2, 1.0))
        item = next(r for r in self.build(*other_app)["recommendations"] if r["kind"] == "premium_routine")
        self.assertEqual(item["to_models"], [])

    def test_recommendations_flag_long_threads_that_cost_more_per_request(self):
        rows = [row(f"s{i}", cost=1.0, turns_=5) for i in range(5)]
        rows += [row(f"l{i}", cost=30.0, turns_=30) for i in range(5)]
        recs = self.build(rows, {})["recommendations"]
        long = next(r for r in recs if r["kind"] == "long_threads")
        self.assertEqual((long["sessions"], long["spend"]), (5, 150.0))
        self.assertAlmostEqual(long["ratio"], 5.0)
        self.assertAlmostEqual(long["saving"], 150.0 - 150 * 0.2)
        tags = {t["tag"]: t for t in self.build(rows, {})["tags"]["items"]}
        self.assertEqual(tags["long_thread"]["sessions"], 5)

    def test_tag_filter_is_validated_by_the_endpoint(self):
        self.assertIn("tag", domain.DRILL_FILTERS)
        self.assertEqual(set(meter.WORK_DRILL_ENUMS["tag"]), set(domain.TAG_ORDER))


class TaxonomyV3Tests(unittest.TestCase):
    def test_previous_default_areas_move_to_the_developer_defaults(self):
        old = [{"name": n, "description": "old"} for n in W.PREVIOUS_DEFAULT_AREA_NAMES[0]]
        self.assertEqual(W.normalize_settings({"areas": old})["areas"], [dict(a) for a in W.DEFAULT_AREAS])
        custom = [{"name": "Mobile", "description": "iOS app"}, {"name": "Web", "description": "site"}]
        self.assertEqual(W.normalize_settings({"areas": custom})["areas"], custom)

    def test_request_labels_are_relabeled_and_pushback_labels_stay(self):
        tags = W.question_tags(W.default_settings())
        self.assertEqual((tags["work_type"], tags["complexity"], tags["correction"]), ("g1", "g1", "g1"))
        self.assertTrue(tags["area"].startswith("g1:"))

    def test_developer_work_types(self):
        self.assertEqual(list(W.WORK_TYPES), ["feature", "debug", "refactor", "test", "review", "plan", "explore",
                                              "ops", "docs", "other"])
        self.assertEqual(set(domain.WORK_TYPE_ORDER), set(W.WORK_TYPES) | {"unclear"})
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "page.html"),
                  encoding="utf-8") as handle:
            page = handle.read()
        for key in W.WORK_TYPES:
            self.assertIn(f"{key}:'", page[page.index("const WORK_TYPE_LABELS="):][:400])


class ReviewFixTests(unittest.TestCase):
    def test_area_label_from_an_earlier_prompt_version_still_counts(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        service, values, clock = make_service(tmp.name)
        key, turn = service.session_key("s1"), service._turn_key("s1", 0)
        old_tag = f"p2:{W.taxonomy_hash(values['areas'])}"
        service.ledger.record_label(turn, "area", key, "Non-code", 0.9, old_tag, "d", clock.now)
        service.ledger.record_label(turn, "work_type", key, "review", 0.9, "p2", "d", clock.now)
        service.labels_version += 1
        entry = service.snapshot()[key]
        self.assertEqual((entry["area"], entry["work_type"]), ("Non-code", "review"))
        other = service._turn_key("s2", 0)
        service.ledger.record_label(other, "area", service.session_key("s2"), "Non-code", 0.9, "p2:0000", "d", clock.now)
        service.labels_version += 1
        self.assertNotIn("area", service.snapshot().get(service.session_key("s2"), {}))

    def test_a_deliberate_save_of_the_old_default_names_is_kept(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "settings.json")
        old = [{"name": n, "description": "mine"} for n in W.PREVIOUS_DEFAULT_AREA_NAMES[0]]
        with mock.patch.object(meter, "_work_settings_cache", {"key": None, "value": None}):
            self.assertTrue(meter.set_work_insights_settings({"areas": old}, path)["ok"])
            self.assertEqual(meter.work_insights_settings(path)["areas"], old)
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["work_insights"]["areas_version"], W.AREAS_VERSION)

    def test_settings_writes_are_serialized(self):
        self.assertIsInstance(meter._work_settings_write_lock, type(threading.Lock()))
        with mock.patch.object(meter, "_set_work_insights_settings", return_value={"ok": True}) as inner:
            with meter._work_settings_write_lock:
                worker = threading.Thread(target=meter.set_work_insights_settings, args=({},))
                worker.start()
                worker.join(0.2)
                self.assertTrue(worker.is_alive())
                inner.assert_not_called()
            worker.join(2)
        inner.assert_called_once()

    def test_turning_off_cancels_setup_and_stops_the_managed_ollama(self):
        setup = mock.Mock()
        with mock.patch.object(meter, "work_setup", return_value=setup), \
                mock.patch.object(meter, "work_insights_supported", return_value=True):
            meter.stop_managed_ollama()
        setup.cancel.assert_called_once()
        setup.stop_agent.assert_called_once()
        self.assertLess(setup.method_calls.index(mock.call.cancel()), setup.method_calls.index(mock.call.stop_agent()))

    def test_long_thread_suggestion_counts_every_tagged_session(self):
        rows = [row(f"s{i}", cost=1.0, turns_=5) for i in range(5)]
        rows += [row(f"l{i}", cost=30.0, turns_=30) for i in range(5)] + [row("free", cost=0.0, turns_=40)]
        out = domain.build_work_insights(rows, {}, lambda ident: ident.split("\0")[0], DomainTests.AREAS,
                                         lambda m, p: None, today="2026-09-30")
        long = next(r for r in out["recommendations"] if r["kind"] == "long_threads")
        tag = next(t for t in out["tags"]["items"] if t["tag"] == "long_thread")
        self.assertEqual((long["sessions"], tag["sessions"]), (6, 6))


class UnlabeledReasonTests(unittest.TestCase):
    def build(self, rows, labels, **kwargs):
        return domain.build_work_insights(rows, labels, lambda ident: ident.split("\0")[0], DomainTests.AREAS,
                                          lambda m, p: None, today="2026-09-30", **kwargs)

    def test_sessions_without_text_or_outside_history_are_not_pending(self):
        silent = row("silent", day="2026-09-20", turns_=0)
        old = row("old", day="2026-07-01")
        waiting = row("waiting", day="2026-09-25")
        out = self.build([silent, old, waiting], {}, label_since="2026-07-02")
        sessions = {}
        for bucket in out["allocation"]:
            for area, count in bucket["sessions"].items():
                sessions[area] = sessions.get(area, 0) + count
        self.assertEqual(sessions, {"No request text": 1, "Outside history": 1, "Pending": 1})
        self.assertEqual((out["coverage"]["no_text_sessions"], out["coverage"]["outside_sessions"]), (1, 1))
        found = domain.find_sessions([silent, old, waiting], {}, lambda ident: ident.split("\0")[0], DomainTests.AREAS,
                                     lambda m, p: None, {"area": "Outside history"}, today="2026-09-30",
                                     label_since="2026-07-02")
        self.assertEqual([s["id"] for s in found["sessions"]], ["old"])

    def test_a_session_resumed_inside_the_history_is_not_outside_it(self):
        resumed = row("resumed", day="2026-06-01")
        resumed["_work_turn_days"] = ["2026-06-01", "2026-09-25"]
        out = self.build([resumed], {}, label_since="2026-07-02", months=6)
        self.assertEqual(out["coverage"]["outside_sessions"], 0)

    def test_reserved_names_cannot_be_custom_areas(self):
        for name in ("No request text", "Outside history", "pending"):
            with self.assertRaises(ValueError):
                W.normalize_areas([{"name": name, "description": "x"}, {"name": "Web", "description": "y"}])

    def test_spend_without_a_daily_split_still_counts_toward_its_area(self):
        quiet = row("quiet", day="2026-09-20", turns_=0, cost=0.75)
        quiet["_day_cost"] = {}
        out = self.build([quiet], {})
        self.assertEqual(sum(b["spend"].get("No request text", 0) for b in out["allocation"]), 0.75)

    def test_all_history_setting_keeps_old_sessions_pending(self):
        out = self.build([row("old", day="2026-07-01")], {}, label_since="")
        self.assertEqual(out["allocation"][-3]["sessions"], {"Pending": 1})

    def test_new_area_names_are_valid_drill_filters(self):
        for name in domain.UNLABELED:
            self.assertIn(name, ("No request text", "Outside history", "Pending"))
        self.assertEqual(meter._work_label_since({"backfill_days": 0}), "")
        self.assertEqual(len(meter._work_label_since({"backfill_days": 90})), 10)


class SwitchGuardTests(unittest.TestCase):
    AREAS, PRICES = TagHighlightRhythmTests.AREAS, TagHighlightRhythmTests.PRICES
    build, sessions, combine = (TagHighlightRhythmTests.build, TagHighlightRhythmTests.sessions,
                                TagHighlightRhythmTests.combine)

    def test_a_cheaper_model_with_more_pushback_is_not_suggested(self):
        rows, labels, sequences = self.combine(self.sessions("gpt-5.6", 12, 10.0), self.sessions("cheap", 10, 2.0))
        for key in labels:
            if key.startswith("cheap"):
                labels[key]["corrections"] = 1
                sequences[key] = [(1, True), (2, False)]  # rescued, so still resolved, but with pushback
        self.assertFalse(any(r["kind"] == "switch_model" for r in self.build(rows, labels, sequences)["recommendations"]))

    def test_suggestion_says_when_the_cheaper_model_has_not_seen_harder_work(self):
        parts = self.combine(self.sessions("gpt-5.6", 12, 10.0), self.sessions("cheap", 10, 2.0))
        switch = next(r for r in self.build(*parts)["recommendations"] if r["kind"] == "switch_model")
        self.assertTrue(switch["to_untested_harder"])
        harder = self.combine(parts, self.sessions("cheap", 1, 2.0, "complex", prefix="hard"))
        switch = next(r for r in self.build(*harder)["recommendations"] if r["kind"] == "switch_model")
        self.assertFalse(switch["to_untested_harder"])


class LiveHintTests(unittest.TestCase):
    PRICES = {"gpt-5.6": 10.0, "cheap": 1.0, "mid": 4.0, "claude-opus-4-8": 25.0, "claude-opus-5-5": 20.0}

    def hints(self, rows, current, labels=None, corrections=None):
        return domain.live_session_hints(
            rows, current, labels or {}, lambda ident: ident.split("\0")[0], lambda m, p: self.PRICES.get(m),
            corrections_for=(lambda ident, n: corrections.get(ident.split("\0")[0], [])) if corrections else None)

    def test_live_hints_follow_the_right_sizing_rules(self):
        live = row("live", model="gpt-5.6", turns_=31)
        live["session"], live["reasoning_effort"] = "live.jsonl", "xhigh"
        others = [row("m", model="mid"), row("c", model="cheap")]
        labels = {"live": {"complexity": "routine", "correction_labels": 4, "corrections": 2}}
        corrections = {"live": [(1, False), (2, True), (3, True)]}
        out = self.hints([live, *others], [{"session": "live.jsonl"}], labels, corrections)
        kinds = [hint["kind"] for hint in out["live.jsonl"]]
        self.assertEqual(kinds, ["pushback_streak", "premium_routine", "effort_routine"])
        self.assertEqual(out["live.jsonl"][1]["to_model"], "mid")
        self.assertTrue(all(hint["title"] and hint["detail"] for hint in out["live.jsonl"]))

    def test_long_session_needs_no_labels_and_model_switches_stay_on_the_work_page(self):
        live = row("live", model="claude-opus-4-8", runtime="Claude", turns_=30)
        live["session"] = "live.jsonl"
        newer = [row(f"n{i}", model="claude-opus-5-5", runtime="Claude") for i in range(5)]
        out = self.hints([live, *newer], [{"session": "live.jsonl"}])
        self.assertEqual([hint["kind"] for hint in out["live.jsonl"]], ["long_thread"])

    def test_resumed_sessions_use_their_newest_file(self):
        old, new = row("x", turns_=40), row("x", turns_=2)
        old["session"] = new["session"] = "x.jsonl"
        old["mtime"], new["mtime"] = 100, 200
        self.assertEqual(self.hints([new, old], [{"session": "x.jsonl"}]), {})

    def test_menu_bar_notifications_are_bounded_and_respect_the_setting(self):
        current = [{"session": "a.jsonl", "runtime": "Codex",
                    "hints": [{"kind": "long_thread", "title": "Long session", "detail": "30 requests so far."}]}]
        with mock.patch.object(meter, "work_insights_supported", return_value=True), \
                mock.patch.object(meter, "work_insights_settings", return_value=W.normalize_settings({})):
            self.assertEqual(meter.live_hint_notifications(current), [], "off until Work insights is on")
        with mock.patch.object(meter, "work_insights_supported", return_value=True), \
                mock.patch.object(meter, "work_insights_settings",
                                  return_value=W.normalize_settings({"enabled": True})):
            first = meter.live_hint_notifications(current)
            self.assertEqual(first, meter.live_hint_notifications(current))
            self.assertEqual(set(first[0]), {"id", "title", "body"})
            self.assertNotIn("a.jsonl", json.dumps(first))
        with mock.patch.object(meter, "work_insights_supported", return_value=True), \
                mock.patch.object(meter, "work_insights_settings",
                                  return_value=W.normalize_settings({"enabled": True, "live_notifications": False})):
            self.assertEqual(meter.live_hint_notifications(current), [])

    def test_live_notification_setting_is_validated_and_public(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "settings.json")
        with mock.patch.object(meter, "_work_settings_cache", {"key": None, "value": None}):
            self.assertFalse(meter.set_work_insights_settings({"live_notifications": "yes"}, path)["ok"])
            self.assertTrue(meter.set_work_insights_settings({"live_notifications": False}, path)["ok"])
            self.assertFalse(meter.work_insights_public_settings(meter.work_insights_settings(path))["live_notifications"])

    def test_surfaces_render_hints_completion_and_notifications(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "page.html"), encoding="utf-8") as handle:
            page = handle.read()
        with open(os.path.join(root, "menubar", "TokenMeterMenuBar.swift"), encoding="utf-8") as handle:
            swift = handle.read()
        for marker in ("class=currentSessionHints", "id=work-live-notify", "live_notifications:event.target.checked"):
            self.assertIn(marker, page)
        self.assertNotIn("Do subagents finish?", page)
        for marker in ("evaluateLiveHintNotifications", '"TokenMeterLiveHintNotificationIDs"', 'dict["live_hints"]',
                       'dict["live_hints_enabled"]', "if enabled && !liveHintsWereEnabled { liveHintsSeeded = false }"):
            self.assertIn(marker, swift)


class ReservedAreaMigrationTests(unittest.TestCase):
    def test_a_stored_area_with_a_reserved_name_is_renamed_not_reset(self):
        stored = [{"name": "Mobile", "description": "iOS"}, {"name": "Outside history", "description": "old"}]
        areas = W.normalize_settings({"areas": stored, "areas_version": W.AREAS_VERSION})["areas"]
        self.assertEqual([a["name"] for a in areas], ["Mobile", "Outside history (area)"])
        with self.assertRaises(ValueError):
            W.normalize_areas([{"name": "Not labeled yet", "description": "x"}, {"name": "Web", "description": "y"}])


class SubagentModelTrendTests(unittest.TestCase):
    def test_model_days_are_aggregated_projected_and_rendered(self):
        from token_meter.domain import agents
        from token_meter import projections
        record = {"id": "c1", "kind": "spawned", "parent_id": "root", "runtime": "codex", "model": "gpt-5.6-terra",
                  "role": "tester", "activity_state": "complete", "cost": 2.0, "cost_available": True,
                  "tokens": 10, "tokens_available": True, "last_activity_at": 1_790_000_000, "retries": 0,
                  "failed_attempts": 0, "executions": 1, "depth": 1}
        record = agents._normalize_record(record, "r", "p")
        usage = agents.aggregate_agent_usage([{"_all_agents": [record], "root_session_id": "r", "_project": "p"}],
                                             now=1_790_000_100)
        self.assertEqual([(row["model"], row["agents"]) for row in usage["model_days"]], [("gpt-5.6-terra", 1)])
        projected = projections.agent_usage_projection(usage)
        self.assertEqual(projected["model_days"][0]["model"], "gpt-5.6-terra")
        self.assertFalse(projected["model_days_truncated"])
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "page.html"),
                  encoding="utf-8") as handle:
            page = handle.read()
        for marker in ("<h4>Model trends</h4>", "data-subagent-inspect-model", "function openSubagentModelRuns(",
                       "modelTrends:buildSubagentModelTrends(usage,filters,nowMs)", "<h4>Role trends</h4>"):
            self.assertIn(marker, page)


class AreaGuessTests(unittest.TestCase):
    def test_low_confidence_area_guesses_count_in_their_area_and_are_tracked(self):
        sure, guess = row("sure", cost=4.0), row("guess", cost=6.0)
        labels = {"sure": {"area": "Personal"}, "guess": {"area": "Personal", "area_guess": True}}
        out = domain.build_work_insights([sure, guess], labels, lambda ident: ident.split("\0")[0], DomainTests.AREAS,
                                         lambda m, p: None, today="2026-09-30")
        september = next(b for b in out["allocation"] if b["month"] == "2026-09")
        self.assertEqual(september["spend"]["Personal"], 10.0)
        self.assertEqual((september["guess_spend"], september["guess_sessions"]), ({"Personal": 6.0}, {"Personal": 1}))


class RightSizingColourTests(unittest.TestCase):
    def test_effort_is_folded_into_three_bands_and_drills_accept_a_level_list(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "page.html"),
                  encoding="utf-8") as handle:
            page = handle.read()
        self.assertIn("{key:'extra',label:'Extra high+',levels:['xhigh','max','ultra']}", page)
        rows = [row("a"), row("b")]
        rows[0]["reasoning_effort"], rows[1]["reasoning_effort"] = "xhigh", "max"
        out = domain.find_sessions(rows, {}, lambda ident: ident.split("\0")[0], DomainTests.AREAS, lambda m, p: None,
                                   {"effort": "xhigh,max,ultra"}, today="2026-09-30")
        self.assertEqual(out["total"], 2)
        with mock.patch.object(meter, "TOKEN_METER_SETTINGS", os.path.join(tempfile.mkdtemp(), "s.json")), \
                mock.patch.object(meter, "_work_service_instance", None), \
                mock.patch.dict(meter._xsess, {"data": {"ok": True}, "internal_rows": tuple(rows)}):
            self.assertEqual(meter.work_sessions_state({"months": ["6"], "effort": ["xhigh,max"]})[1], 200)
            self.assertEqual(meter.work_sessions_state({"months": ["6"], "effort": ["xhigh,bogus"]})[1], 400)
