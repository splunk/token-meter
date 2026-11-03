import copy
import json
import os
import tempfile
import unittest

from tests.test_mcp_queries import synthetic_query_service, synthetic_source_and_state
from token_meter.mcp import service as query_service
from token_meter.mcp.evidence_cache import MCPEvidenceCache, listing_fields
from token_meter.mcp.schema import METRICS


def _state_variants():
    """Fixture states covering every record shape the stats aggregation reads."""
    source, base = synthetic_source_and_state(session_id="parity-1")
    later = copy.deepcopy(base["executions"][0])
    later.update({
        "idx": 2, "ts": base["executions"][0]["ts"] + 90_000.0,
        "model": "gpt-5.4", "cost": 0.0, "discount_usd": 0.01,
        "active_s": 3.25, "wait_s": None, "ttft_s": None,
    })
    later["tokens"] = {"input": 0, "cache": 7, "cache_write_1h": 3}
    later["tools"] = [
        {"name": "read_file", "category": "files", "output_tokens": 12},
        {"name": "web.search", "namespace": "web", "output_tokens": None},
        {"name": "", "output_tokens": 5},
        "not-a-tool",
    ]
    multi = copy.deepcopy(base)
    multi["executions"].append(later)

    empty = copy.deepcopy(base)
    empty["executions"] = []

    aggregate_tools = copy.deepcopy(base)
    for execution in aggregate_tools["executions"]:
        execution["tools"] = []
    aggregate_tools["tools"]["by_name"].append({
        "name": "grep", "namespace": "search", "calls": 4, "output_tokens": 30,
    })

    no_cost = copy.deepcopy(multi)
    no_cost["availability"]["cost"] = False
    no_context = copy.deepcopy(multi)
    no_context.pop("context")
    no_context["timing"].pop("start_ts")
    return source, {
        "single": base, "multi": multi, "empty": empty,
        "aggregate_tools": aggregate_tools, "no_cost": no_cost,
        "no_context": no_context,
    }


def _normalized(records, grain):
    names = set(query_service.STAT_ROW_METRICS[grain]) | {"context_latest", "context_peak"}
    return [
        {
            "session_id": record["session_id"],
            "timestamp": record["timestamp"],
            "dimensions": {
                name: record["dimensions"].get(name)
                for name in query_service.STAT_ROW_DIMENSIONS
            },
            "metrics": {name: record["metrics"].get(name) for name in sorted(names)},
        }
        for record in records
    ]


def _legacy_stats(service, metrics, group_by=(), **filters):
    """The pre-cache aggregation path: detailed state straight into records."""
    grain = query_service._metric_grain(metrics, group_by)
    window = {
        "model": filters.get("model", ""),
        "start": query_service._timestamp(filters.get("start"), "start"),
        "end": query_service._timestamp(filters.get("end"), "end"),
    }
    records = []
    for source in service._sources():
        state = service._state(source)
        build = (query_service._tool_records if grain == "tool"
                 else query_service._execution_records)
        records.extend(
            record for record in build(source, state)[0]
            if query_service._record_matches_filters(record, window)
        )
    groups = query_service._aggregate_records(records, metrics, group_by)
    totals = query_service._aggregate_records(records, metrics, ())
    return groups, totals[0] if totals else None


class StatRowParityTests(unittest.TestCase):
    def test_compact_rows_rebuild_the_exact_legacy_records(self):
        source, states = _state_variants()
        for name, state in states.items():
            rows = query_service.session_stat_rows(source, state)
            for grain, build in (
                ("execution", query_service._execution_records),
                ("tool", query_service._tool_records),
            ):
                with self.subTest(state=name, grain=grain):
                    legacy = build(source, copy.deepcopy(state))[0]
                    rebuilt = query_service.expand_stat_rows(rows, grain)
                    self.assertEqual(_normalized(rebuilt, grain), _normalized(legacy, grain))
                    for record in legacy:
                        self.assertTrue(set(record["metrics"]) <= (
                            set(query_service.STAT_ROW_METRICS[grain])
                            | {"context_latest", "context_peak"}
                        ))

    def test_compact_rows_survive_a_json_round_trip(self):
        source, states = _state_variants()
        for name, state in states.items():
            rows = query_service.session_stat_rows(source, state)
            with self.subTest(state=name):
                self.assertEqual(json.loads(json.dumps(rows)), rows)

    def test_stats_output_matches_the_legacy_aggregation(self):
        service = synthetic_query_service()
        _source, variants = _state_variants()
        service.states["session-1"].update(copy.deepcopy(variants["multi"]))
        service.states["session-1"]["source"]["id"] = "session-1"
        execution_metrics = [name for name in METRICS if name not in {
            "tool_calls", "tool_result_tokens", "session_count",
        }]
        queries = [
            {"metrics": execution_metrics[:8], "group_by": ("model",)},
            {"metrics": execution_metrics[8:16], "group_by": ("day", "runtime")},
            {"metrics": execution_metrics[16:], "group_by": ("session_id",)},
            {"metrics": ("cost_usd", "total_tokens"), "group_by": ("model",),
             "start": "2026-08-20T00:00:00+00:00"},
            {"metrics": ("input_tokens",), "model": "gpt-5.4"},
            {"metrics": ("tool_calls", "tool_result_tokens"),
             "group_by": ("tool_category", "tool_name")},
            {"metrics": ("tool_calls",), "group_by": ("model", "day"),
             "end": "2026-08-20T23:59:59+00:00"},
        ]
        for query in queries:
            with self.subTest(query=query):
                result = service.stats(limit=100, **query)
                groups, totals = _legacy_stats(service, **query)
                self.assertEqual(result["totals"], totals["metrics"])
                self.assertEqual(result["coverage"], totals["coverage"])
                key = lambda row: json.dumps(row["dimensions"], sort_keys=True)
                self.assertEqual(
                    sorted(result["groups"], key=key), sorted(groups, key=key),
                )


def _cached_service(cache_path=None, persist=False):
    service = synthetic_query_service()
    calls = {"state": 0, "summary": 0}
    signatures = {"session-1": "a", "session-2": "a"}
    original_summary = service._summary

    def state(source):
        calls["state"] += 1
        return copy.deepcopy(service.states[source["id"]])

    def summary(source):
        calls["summary"] += 1
        return original_summary(source)

    cache = MCPEvidenceCache(
        summary=summary,
        state=state,
        summary_signature=lambda source: ("summary", source["id"], signatures[source["id"]]),
        state_signature=lambda source: ("state", source["id"], signatures[source["id"]]),
        path=cache_path,
        write_text=_write_text,
        persist=lambda: persist,
    )
    service._summary = cache.listing
    service._stat_rows = cache.stat_rows
    return service, cache, calls, signatures


def _write_text(path, text):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


class MCPEvidenceCacheTests(unittest.TestCase):
    QUERY = {"metrics": ("cost_usd", "total_tokens"), "group_by": ("model",)}

    def test_repeated_stats_reuse_rows_until_the_session_signature_changes(self):
        service, _cache, calls, signatures = _cached_service()
        uncached = synthetic_query_service()

        first = service.stats(**self.QUERY)
        second = service.stats(**self.QUERY)
        self.assertEqual(calls["state"], 2)
        self.assertEqual(first["groups"], second["groups"])
        self.assertEqual(first["groups"], uncached.stats(**self.QUERY)["groups"])

        service.states["session-1"]["executions"][0]["cost"] = 1.0
        self.assertEqual(service.stats(**self.QUERY)["groups"], first["groups"])
        signatures["session-1"] = "b"
        changed = service.stats(**self.QUERY)
        self.assertEqual(calls["state"], 3)
        self.assertAlmostEqual(changed["totals"]["cost_usd"], 1.12)

    def test_sessions_listing_reuses_summary_fields(self):
        service, _cache, calls, _signatures = _cached_service()
        uncached = synthetic_query_service()

        first = service.sessions(scope="all", limit=20)
        service.sessions(scope="all", limit=20)
        self.assertEqual(first["sessions"], uncached.sessions(scope="all", limit=20)["sessions"])
        self.assertEqual(calls["summary"], 2)

    def test_listing_fields_preserve_projection_semantics(self):
        from token_meter.mcp.projections import session_projection

        source, _state = synthetic_source_and_state()
        source.pop("model")
        for summary in (
            {},
            {"models": ["only-model"], "availability": {"cost": False, "other": 1}},
            {"models": ["a", "b", "c"], "terminal": True, "cost": 0.5},
            {"models": [None], "availability": {"unrelated": True}},
            {"primary_model": {"id": "x"}, "availability": ["cost"]},
            {"primary_model": [], "availability": [], "models": ["solo"]},
            {"model_provider": {"bad": 1}, "terminal": "yes", "tokens": {"n": 1}},
            {"primary_model": "m", "tokens": 3, "duration_s": 2.5, "cost": 0.0,
             "discount_usd": 0.1, "extra": {"prompt": "SENTINEL-PRIVATE"}},
        ):
            with self.subTest(summary=summary):
                self.assertEqual(
                    session_projection(source, listing_fields(summary), 1.0),
                    session_projection(source, summary, 1.0),
                )
        self.assertNotIn("extra", listing_fields({"extra": "x"}))

    def test_persisted_cache_warms_a_new_process_without_reparsing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "cache.json")
            service, cache, _calls, _signatures = _cached_service(path, persist=True)
            expected = service.stats(**self.QUERY)
            listing = service.sessions(scope="all", limit=20)
            self.assertTrue(cache.flush())
            self.assertFalse(cache.flush())

            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            self.assertNotIn("SENTINEL-PRIVATE", text)
            self.assertNotIn("/private", text)
            self.assertNotIn("/repo", text)

            fresh, _cache, calls, _signatures = _cached_service(path, persist=True)
            self.assertEqual(fresh.stats(**self.QUERY)["groups"], expected["groups"])
            self.assertEqual(fresh.sessions(scope="all", limit=20)["sessions"],
                             listing["sessions"])
            self.assertEqual(calls, {"state": 0, "summary": 0})

    def test_malformed_or_foreign_cache_files_are_a_cold_cache(self):
        source, states = _state_variants()
        rows = query_service.session_stat_rows(source, states["single"])
        bad_rows = copy.deepcopy(rows)
        bad_rows["execution"][0][1] = 99
        for content in (
            "{not json",
            json.dumps({"schema": 999, "sessions": {}}),
            json.dumps({"schema": 1, "sessions": []}),
            json.dumps({"schema": 1, "sessions": {"k": {
                "stats_signature": "x", "stats": bad_rows,
                "listing_signature": "y", "listing": {"prompt": "x"},
            }}}),
        ):
            with self.subTest(content=content[:40]):
                with tempfile.TemporaryDirectory() as directory:
                    path = os.path.join(directory, "cache.json")
                    _write_text(path, content)
                    service, cache, calls, _signatures = _cached_service(path, persist=True)
                    result = service.stats(**self.QUERY)
                    self.assertEqual(calls["state"], 2)
                    self.assertEqual(result["groups"],
                                     synthetic_query_service().stats(**self.QUERY)["groups"])
                    self.assertEqual(cache._entries.get("k"), None)

    def test_persistence_is_opt_in_and_vanished_sessions_are_forgotten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "cache.json")
            service, cache, _calls, _signatures = _cached_service(path, persist=False)
            service.stats(**self.QUERY)
            self.assertFalse(cache.flush())
            self.assertFalse(os.path.exists(path))

            self.assertEqual(len(cache._entries), 2)
            cache.retain(service._sources()[:1])
            self.assertEqual(len(cache._entries), 1)

    def test_persisted_strings_are_allowlisted_identifiers_without_account_ids(self):
        from token_meter.mcp.projections import safe_identity

        arn = "arn:aws:bedrock:us-west-2:123456789012:application-inference-profile/abc"
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "cache.json")
            service, cache, _calls, _signatures = _cached_service(path, persist=True)
            state = service.states["session-2"]
            state["primary_model"] = arn
            state["executions"][0]["model"] = arn
            expected = service.stats(**self.QUERY)
            service.sessions(scope="all", limit=20)
            self.assertIn(arn, json.dumps(expected))
            self.assertTrue(cache.flush())

            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            stored = json.loads(text)["sessions"]
            self.assertNotIn("123456789012", text)
            self.assertEqual(len(stored), 1)
            strings = []
            for entry in stored.values():
                strings.extend(v for v in entry["listing"].values() if isinstance(v, str))
                for row in entry["stats"]["dimensions"]:
                    strings.extend(v for v in row if isinstance(v, str))
            self.assertTrue(strings)
            for value in strings:
                self.assertEqual(safe_identity(value, 240), value)

            fresh, _cache, calls, _signatures = _cached_service(path, persist=True)
            fresh.states["session-2"] = state
            self.assertEqual(fresh.stats(**self.QUERY)["groups"], expected["groups"])
            self.assertEqual(calls["state"], 1)

    def test_oversized_sessions_and_file_budget_stay_memory_only(self):
        from token_meter.mcp import evidence_cache

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "cache.json")
            service, cache, _calls, _signatures = _cached_service(path, persist=True)
            many = service.states["session-1"]["executions"][0]
            service.states["session-1"]["executions"] = [
                {**copy.deepcopy(many), "idx": index}
                for index in range(evidence_cache.MAX_PERSISTED_ROWS)
            ]
            service.stats(**self.QUERY)
            self.assertTrue(cache.flush())
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(len(json.load(handle)["sessions"]), 1)

            small, cache, _calls, _signatures = _cached_service(path, persist=False)
            small.stats(**self.QUERY)
            cache._persist = lambda: True
            budget = evidence_cache.MAX_PERSISTED_BYTES
            try:
                evidence_cache.MAX_PERSISTED_BYTES = 10
                self.assertTrue(cache.flush())
            finally:
                evidence_cache.MAX_PERSISTED_BYTES = budget
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(json.load(handle), {"schema": 1, "code": "", "sessions": {}})

    def test_a_foreign_schema_with_valid_entries_is_a_cold_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "cache.json")
            service, cache, _calls, _signatures = _cached_service(path, persist=True)
            service.stats(**self.QUERY)
            cache.flush()
            with open(path, encoding="utf-8") as handle:
                stored = json.load(handle)
            stored["schema"] = 2
            _write_text(path, json.dumps(stored))

            fresh, _cache, calls, _signatures = _cached_service(path, persist=True)
            fresh.stats(**self.QUERY)
            self.assertEqual(calls["state"], 2)

    def test_disabled_persistence_never_reads_the_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "cache.json")
            service, cache, _calls, _signatures = _cached_service(path, persist=True)
            service.stats(**self.QUERY)
            cache.flush()

            fresh, cache, calls, _signatures = _cached_service(path, persist=False)
            fresh.stats(**self.QUERY)
            self.assertEqual(calls["state"], 2)

    def test_sessions_without_executions_keep_measured_zero_counts_when_cached(self):
        service, _cache, _calls, _signatures = _cached_service()
        uncached = synthetic_query_service()
        summaries = {row["id"]: uncached._summary(row) for row in uncached._sources()}
        uncached._summary = lambda source: summaries[source["id"]]
        service.sessions(scope="all", limit=20)
        for state in (*service.states.values(), *uncached.states.values()):
            state["executions"] = []
        query = {"metrics": ("execution_count", "context_peak"), "group_by": ("session_id",)}

        for result in (service.stats(**query), service.stats(**query)):
            self.assertEqual(result["totals"]["execution_count"], 0)
            self.assertEqual(result["coverage"]["execution_count"],
                             {"covered": 2, "unavailable": 0})
            self.assertEqual(result["groups"], uncached.stats(**query)["groups"])

    def test_a_different_code_version_is_a_cold_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "cache.json")
            service, cache, _calls, _signatures = _cached_service(path, persist=True)
            cache._code_version = lambda: "c_old"
            expected = service.stats(**self.QUERY)
            self.assertTrue(cache.flush())

            same, cache, calls, _signatures = _cached_service(path, persist=True)
            cache._code_version = lambda: "c_old"
            same.stats(**self.QUERY)
            self.assertEqual(calls["state"], 0)

            upgraded, cache, calls, _signatures = _cached_service(path, persist=True)
            cache._code_version = lambda: "c_new"
            self.assertEqual(upgraded.stats(**self.QUERY)["groups"], expected["groups"])
            self.assertEqual(calls["state"], 2)

    def test_application_fingerprints_package_sources(self):
        import meter

        version = meter.mcp_evidence_code_version()
        self.assertRegex(version, r"^c_[0-9a-f]{16}$")
        self.assertEqual(meter._mcp_evidence_cache._code_version(), version)

    def test_enabling_persistence_fixes_the_code_label_before_any_query(self):
        import meter

        meter.mcp_evidence_code_version.cache_clear()
        try:
            meter.enable_mcp_evidence_persistence()
            self.assertEqual(meter.mcp_evidence_code_version.cache_info().currsize, 1)
        finally:
            meter._mcp_evidence_persist = False

    def test_cloud_resource_identifiers_stay_memory_only(self):
        from token_meter.mcp.evidence_cache import _persistable

        def entry(model):
            return {"listing": {"primary_model": model}, "stats": {"dimensions": []}}

        self.assertTrue(_persistable(entry("claude-opus-5-5")))
        for model in (
            "arn:aws:bedrock:us-west-2:123456789012:application-inference-profile/x",
            "projects/acme-prod/locations/us-central1/publishers/google/models/gemini",
            "https://acme.openai.azure.com/openai/deployments/gpt",
            "subscriptions/0000/resourceGroups/rg",
        ):
            with self.subTest(model=model):
                self.assertFalse(_persistable(entry(model)))

    def test_rejected_identities_become_a_persistable_marker_with_the_same_output(self):
        from token_meter.mcp.evidence_cache import UNSUPPORTED_IDENTITY, _persistable
        from token_meter.mcp.projections import session_projection

        source, _state = synthetic_source_and_state()
        for summary in (
            {"primary_model": "claude-opus-4-1[1m]", "model_provider": "bad provider!"},
            {"models": ["claude-opus-4-1[1m]"]},
            {"primary_model": {"id": "x"}, "availability": ["cost"]},
        ):
            with self.subTest(summary=summary):
                fields = listing_fields(summary)
                for candidate in (source, {**source, "model": None, "model_provider": None}):
                    self.assertEqual(session_projection(candidate, fields, 1.0),
                                     session_projection(candidate, summary, 1.0))
                self.assertTrue(_persistable({"listing": fields, "stats": {}}))
                self.assertNotIn("[1m]", json.dumps(fields))
        self.assertEqual(listing_fields({"primary_model": {"id": "x"}})["primary_model"],
                         UNSUPPORTED_IDENTITY)
        self.assertEqual(listing_fields({"availability": ["cost"]})["availability"],
                         {"unsupported": False})

    def test_paths_and_non_identifiers_are_never_persisted(self):
        from token_meter.mcp.evidence_cache import _persistable

        for value in ("/Users/x/secret", "~/notes", "a/home/b", "bad value!", "x" * 241):
            with self.subTest(value=value):
                self.assertFalse(_persistable({
                    "listing": {}, "stats": {"dimensions": [[value] + [None] * 7]},
                }))

    def test_files_far_over_the_budget_are_not_read(self):
        from token_meter.mcp import evidence_cache

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "cache.json")
            service, cache, _calls, _signatures = _cached_service(path, persist=True)
            service.stats(**self.QUERY)
            cache.flush()
            budget = evidence_cache.MAX_PERSISTED_BYTES
            try:
                evidence_cache.MAX_PERSISTED_BYTES = 10
                fresh, _cache, calls, _signatures = _cached_service(path, persist=True)
                fresh.stats(**self.QUERY)
            finally:
                evidence_cache.MAX_PERSISTED_BYTES = budget
            self.assertEqual(calls["state"], 2)

    def test_overflowing_numbers_in_the_file_are_rejected_per_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "cache.json")
            service, cache, _calls, _signatures = _cached_service(path, persist=True)
            service.stats(**self.QUERY)
            cache.flush()
            with open(path, encoding="utf-8") as handle:
                stored = json.load(handle)
            first = next(iter(stored["sessions"]))
            text = json.dumps(stored).replace(
                json.dumps(stored["sessions"][first]["stats"]["context"]),
                "[1" + "0" * 400 + ", null]", 1,
            )
            _write_text(path, text)

            fresh, cache, calls, _signatures = _cached_service(path, persist=True)
            fresh.stats(**self.QUERY)
            self.assertEqual(calls["state"], 1)
            self.assertEqual(len(cache._entries), 2)

    def test_application_routes_mcp_queries_through_the_evidence_cache(self):
        import meter
        import token_meter_mcp

        self.assertFalse(meter._mcp_evidence_persist)
        queries = meter.application().agent_api._queries
        self.assertEqual(queries._stat_rows, meter._mcp_evidence_cache.stat_rows)
        self.assertEqual(queries._summary, meter._mcp_evidence_cache.listing)
        self.assertEqual(queries._sources, meter.mcp_session_sources)
        self.assertEqual(meter.SESSION_STATE_CACHE_LIMIT, 32)
        with open(token_meter_mcp.__file__, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("meter.enable_mcp_evidence_persistence()", source)
        self.assertIn("meter.flush_mcp_evidence_cache()", source)
        self.assertIn("meter.enable_mcp_parallel_index()", source)
        self.assertEqual(queries._activity_bound, meter.source_activity_bound)
        self.assertEqual(queries._prefetch, meter.prefetch_mcp_evidence)

    def test_stale_reports_unfilled_entries_and_prefilled_rows_are_reused(self):
        service, cache, calls, signatures = _cached_service()
        sources = service._sources()
        listing, rows = cache.stale(sources)
        self.assertEqual([source["id"] for source in listing], ["session-1", "session-2"])
        self.assertEqual([source["id"] for source, _signature in rows],
                         ["session-1", "session-2"])
        self.assertEqual(cache.stale(sources, stats=False)[1], [])

        expected = synthetic_query_service().stats(**self.QUERY)
        for source, signature in rows:
            computed = query_service.session_stat_rows(source, service.states[source["id"]])
            self.assertTrue(cache.store_stat_rows(source, signature, computed))
        self.assertFalse(cache.store_stat_rows(sources[0], rows[0][1], {"bad": 1}))
        self.assertEqual(cache.stale(sources)[1], [])
        self.assertEqual(service.stats(**self.QUERY)["groups"], expected["groups"])
        self.assertEqual(calls["state"], 0)

        signatures["session-1"] = "changed"
        self.assertEqual([source["id"] for source, _ in cache.stale(sources)[1]],
                         ["session-1"])

    def test_a_long_fill_flushes_progress_before_the_call_returns(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "cache.json")
            service, cache, _calls, _signatures = _cached_service(path, persist=True)
            now = [100.0]
            cache._clock = lambda: now[0]
            original = service._stat_rows

            def slow_rows(source):
                now[0] += evidence_cache.FLUSH_INTERVAL_S
                return original(source)

            from token_meter.mcp import evidence_cache
            service._stat_rows = slow_rows
            service.stats(**self.QUERY)
            self.assertTrue(os.path.exists(path))
            with open(path, encoding="utf-8") as handle:
                self.assertTrue(json.load(handle)["sessions"])


def _iso(timestamp):
    import datetime
    return datetime.datetime.fromtimestamp(
        int(timestamp), tz=datetime.timezone.utc,
    ).isoformat()


class FlushTimingTests(unittest.TestCase):
    QUERY = {"metrics": ("cost_usd",), "group_by": ("model",)}

    def test_a_short_call_after_a_quiet_gap_flushes_only_at_the_end(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "cache.json")
            service, cache, _calls, signatures = _cached_service(path, persist=True)
            now = [0.0]
            cache._clock = lambda: now[0]
            writes = []
            original = cache._write_text
            cache._write_text = lambda target, text: (writes.append(1), original(target, text))
            service.stats(**self.QUERY)
            self.assertTrue(cache.flush())
            self.assertEqual(len(writes), 1)
            now[0] += 10 * 3600
            signatures["session-1"] = "changed"
            service.stats(**self.QUERY)
            self.assertEqual(len(writes), 1)
            self.assertTrue(cache.flush())
            self.assertEqual(len(writes), 2)


class OrphanedWriteSweepTests(unittest.TestCase):
    def test_only_dead_writers_temp_files_are_removed(self):
        import meter
        import subprocess

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "mcp-evidence-cache.json")
            dead = subprocess.Popen(["true"])
            dead.wait()
            names = {
                "dead": f".mcp-evidence-cache.json.token-meter-{dead.pid}-abcd",
                "alive": f".mcp-evidence-cache.json.token-meter-{os.getppid()}-abcd",
                "own": f".mcp-evidence-cache.json.token-meter-{os.getpid()}-abcd",
                "other": f".settings.json.token-meter-{dead.pid}-abcd",
                "bad": ".mcp-evidence-cache.json.token-meter-notapid-abcd",
            }
            for name in names.values():
                _write_text(os.path.join(directory, name), "x")
            os.mkdir(os.path.join(directory, f".mcp-evidence-cache.json.token-meter-{dead.pid}-dir"))
            self.assertEqual(meter.sweep_orphaned_atomic_writes(path), 1)
            left = set(os.listdir(directory))
            self.assertNotIn(names["dead"], left)
            for key in ("alive", "own", "other", "bad"):
                self.assertIn(names[key], left)
            self.assertEqual(meter.sweep_orphaned_atomic_writes(os.path.join(directory, "missing", "x")), 0)


class WindowedStatsTests(unittest.TestCase):
    QUERY = {"metrics": ("cost_usd", "total_tokens", "session_count"),
             "group_by": ("model",)}

    def _service(self, old_bound):
        service, _cache, calls, _signatures = _cached_service()
        prefetched = []
        bounds = {"session-1": None, "session-2": old_bound}
        service._activity_bound = lambda source: bounds[source["id"]]
        service._prefetch = lambda sources, stats: prefetched.append(
            ([source["id"] for source in sources], stats)
        )
        return service, calls, prefetched

    def test_start_skips_sources_whose_activity_cannot_reach_it(self):
        from token_meter.mcp.service import ACTIVITY_BOUND_SLACK_S

        reference = synthetic_query_service()
        execution_ts = max(
            execution["ts"] for state in reference.states.values()
            for execution in state["executions"]
        )
        # session-2's newest activity is its last execution; session-1 is
        # unbounded and has moved past ``start``.
        later = 3 * ACTIVITY_BOUND_SLACK_S

        def shift(states):
            for item in (*states["session-1"]["executions"], *states["session-1"]["trace"]):
                item["ts"] += later

        shift(reference.states)
        start = _iso(execution_ts + ACTIVITY_BOUND_SLACK_S + 2)
        for query in (
            {**self.QUERY, "start": start},
            {"metrics": ("tool_calls",), "group_by": ("tool_name",), "start": start},
        ):
            with self.subTest(query=query):
                service, calls, prefetched = self._service(execution_ts)
                shift(service.states)
                result = service.stats(**query)
                self.assertTrue(result["groups"])
                self.assertEqual(calls["state"], 1)
                self.assertEqual(calls["summary"], 1)
                self.assertEqual(prefetched, [(["session-1"], True)])
                expected = reference.stats(**query)
                self.assertEqual(result["groups"], expected["groups"])
                self.assertEqual(result["totals"], expected["totals"])

        listing, _calls, prefetched = self._service(execution_ts)
        shift(listing.states)
        sessions = listing.sessions(scope="all", start=start)
        self.assertEqual(sessions["sessions"],
                         reference.sessions(scope="all", start=start)["sessions"])
        self.assertEqual(prefetched, [(["session-1"], False)])

    def test_a_bound_inside_the_slack_or_without_start_keeps_the_source(self):
        from token_meter.mcp.service import ACTIVITY_BOUND_SLACK_S

        reference = synthetic_query_service()
        start_ts = 1_787_254_200
        start = _iso(start_ts)
        for bound, query in (
            (start_ts - ACTIVITY_BOUND_SLACK_S + 1, {**self.QUERY, "start": start}),
            (0.0, self.QUERY),
        ):
            with self.subTest(bound=bound, query=query):
                service, calls, prefetched = self._service(bound)
                result = service.stats(**query)
                self.assertEqual(calls["state"], 2)
                self.assertEqual(prefetched, [(["session-1", "session-2"], True)])
                self.assertEqual(result["groups"], reference.stats(**query)["groups"])

    def test_prefetch_sees_only_sources_the_query_filters_keep(self):
        service, _calls, prefetched = self._service(None)
        service.stats(**self.QUERY, session_id="session-2")
        service.sessions(scope="all", runtime="claude")
        self.assertEqual(prefetched, [(["session-2"], True), ([], False)])


if __name__ == "__main__":
    unittest.main()
