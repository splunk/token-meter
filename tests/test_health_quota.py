import unittest
import json
import os
import tempfile
from pathlib import Path
from unittest import mock
import meter
from tests.meter_support import isolated_pricing_state


class ProviderQuotaTests(unittest.TestCase):
    def tearDown(self):
        meter.reset_provider_quota_cache()

    def test_pace_forecast_distinguishes_runout_reserve_and_early_window(self):
        now = 1_000_000.0
        base = {"window_seconds": 1000, "reset_at": now + 500}

        runout = meter.quota_pace({**base, "used_percent": 70}, now=now)
        reserve = meter.quota_pace({**base, "used_percent": 30}, now=now)
        on_pace = meter.quota_pace({**base, "used_percent": 50}, now=now)
        early = meter.quota_pace({"window_seconds": 1000, "reset_at": now + 980,
                                  "used_percent": 20}, now=now)

        self.assertEqual(runout["state"], "deficit")
        self.assertFalse(runout["will_last_to_reset"])
        self.assertIn("runs out in", runout["summary"])
        self.assertEqual(reserve["state"], "reserve")
        self.assertTrue(reserve["will_last_to_reset"])
        self.assertEqual(on_pace["state"], "on_pace")
        self.assertIsNone(early)

    def test_codex_parser_preserves_main_and_named_spark_windows(self):
        now = 1_000_000.0
        payload = {
            "rateLimits": {
                "planType": "business",
                "primary": {"usedPercent": 12, "windowDurationMins": 300,
                            "resetsAt": now + 1800},
                "secondary": {"usedPercent": 34, "windowDurationMins": 10080,
                              "resetsAt": now + 3600},
            },
            "rateLimitsByLimitId": {
                "codex-spark": {
                    "limitName": "GPT-5.3-Codex-Spark",
                    "primary": {"usedPercent": 56, "windowDurationMins": 300,
                                "resetsAt": now + 1800},
                    "secondary": {"usedPercent": 78, "windowDurationMins": 10080,
                                  "resetsAt": now + 3600},
                },
            },
        }

        result = meter.parse_codex_quota(payload, now=now)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["plan"], "business")
        self.assertEqual(
            [(row["label"], row["used_percent"]) for row in result["windows"]],
            [("Session", 12.0), ("Weekly", 34.0),
             ("Spark session", 56.0), ("Spark weekly", 78.0)],
        )
        self.assertEqual(result["coverage_note"], "")

    def test_codex_parser_does_not_fabricate_absent_main_windows(self):
        result = meter.parse_codex_quota({
            "rate_limit": {"primary_window": None, "secondary_window": None},
            "additional_rate_limits": [{
                "metered_feature": "codex_spark",
                "limit_name": "GPT-5.3-Codex-Spark",
                "rate_limit": {
                    "primary_window": {"used_percent": 4, "limit_window_seconds": 18000,
                                       "reset_at": 1_001_000},
                    "secondary_window": {"used_percent": 9, "limit_window_seconds": 604800,
                                         "reset_at": 1_002_000},
                },
            }],
        }, source="Codex OAuth API", now=1_000_000)

        self.assertEqual([row["label"] for row in result["windows"]],
                         ["Spark session", "Spark weekly"])
        self.assertIn("Regular Session and Weekly limits were not reported by Codex",
                      result["coverage_note"])
        self.assertIn("missing does not mean 0%", result["coverage_note"])

    def test_codex_app_server_uses_wrapper_safe_launchagent_environment(self):
        process = mock.Mock()
        process.stdin = mock.Mock()
        process.stdout = mock.Mock()
        process.poll.return_value = 0
        selector = mock.Mock()
        with mock.patch.object(meter, "provider_cli_path", return_value="/nvm/bin/codex"), \
                mock.patch.object(meter, "agent_client_environment", return_value={"PATH": "/nvm/bin"}) as environment, \
                mock.patch.object(meter.subprocess, "Popen", return_value=process) as popen, \
                mock.patch.object(meter.selectors, "DefaultSelector", return_value=selector), \
                mock.patch.object(meter, "_rpc_read_response", side_effect=[{}, {"rateLimits": {}}]):
            result = meter.codex_app_server_rate_limits()

        environment.assert_called_once_with("/nvm/bin/codex")
        self.assertEqual(popen.call_args.kwargs["env"], {"PATH": "/nvm/bin"})
        self.assertEqual(result, {"rateLimits": {}})

    def test_claude_parser_maps_reported_and_scoped_windows(self):
        now = 1_000_000.0
        result = meter.parse_claude_quota({
            "five_hour": {"utilization": 8, "resets_at": now + 3600},
            "seven_day": {"utilization": 23, "resets_at": now + 7200},
            "seven_day_opus": {"utilization": 41, "resets_at": now + 7200},
            "limits": [{
                "kind": "weekly_scoped", "is_active": True, "percent": 52,
                "resets_at": now + 7200,
                "scope": {"model": {"display_name": "Sonnet 4.5"}},
            }],
        }, credentials={"subscriptionType": "max"}, now=now)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["plan"], "max")
        self.assertEqual([row["label"] for row in result["windows"]],
                         ["Session", "Weekly", "Opus weekly", "Sonnet 4.5 weekly"])
        self.assertEqual(result["coverage_note"], "")

    def test_claude_scoped_weekly_does_not_hide_missing_regular_weekly_limit(self):
        result = meter.parse_claude_quota({
            "five_hour": {"utilization": 8, "resets_at": 1_001_000},
            "limits": [{
                "kind": "weekly_scoped", "percent": 52, "resets_at": 1_002_000,
                "scope": {"model": {"display_name": "Sonnet"}},
            }],
        }, now=1_000_000)

        self.assertEqual([row["label"] for row in result["windows"]],
                         ["Session", "Sonnet weekly"])
        self.assertIn("Weekly limit was not reported by Claude", result["coverage_note"])

    def test_third_party_claude_auth_is_explicitly_unavailable(self):
        with mock.patch.object(meter, "claude_auth_status", return_value={
            "loggedIn": True, "authMethod": "third_party", "apiProvider": "bedrock",
        }), mock.patch.object(meter, "claude_oauth_credentials") as credentials:
            result = meter.load_claude_quota(now=1_000_000)

        credentials.assert_not_called()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["plan"], "Bedrock")
        self.assertIn("not exposed", result["error"])
        self.assertIn("Session and Weekly limits were not reported by Claude",
                      result["coverage_note"])

    def test_cursor_parser_labels_monthly_individual_cap(self):
        now = 1_000_000.0
        result = meter.parse_cursor_quota({
            "membershipType": "enterprise",
            "billingCycleStart": now - 1000,
            "billingCycleEnd": now + 2000,
            "individualUsage": {
                "plan": {"used": 0, "limit": 0},
                "overall": {"used": 987, "limit": 100000},
            },
        }, now=now)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["windows"][0]["kind"], "monthly")
        self.assertEqual(result["windows"][0]["label"], "Plan")
        self.assertAlmostEqual(result["windows"][0]["used_percent"], 0.99, places=2)
        self.assertEqual(result["windows"][0]["window_seconds"], 3000)
        self.assertIn("Session and Weekly limits were not reported by Cursor",
                      result["coverage_note"])
        self.assertIn("missing does not mean 0%", result["coverage_note"])

    def test_cache_marks_old_provider_data_stale_and_recomputes_pace(self):
        row = meter.quota_provider("codex", "Codex", "ok", "test", windows=[
            meter.quota_window("codex", "codex-session", "session", "Session", 50,
                               window_seconds=1000, reset_at=1_001_000, now=1_000_000),
        ])
        with meter._quota_lock:
            meter._quota_cache["codex"] = {**row, "fetched_at": 1_000_000,
                                            "attempted_at": 1_000_000}

        result = meter.provider_quota_snapshots(
            now=1_000_601, loaders={"codex": mock.Mock()}, start_refresh=False,
        )[0]

        self.assertEqual(result["status"], "stale")
        self.assertTrue(result["stale"])
        self.assertEqual(result["age_seconds"], 601)

    def test_empty_cache_returns_loading_and_starts_only_one_worker(self):
        loader = mock.Mock()
        worker = mock.Mock()
        with mock.patch.object(meter.threading, "Thread", return_value=worker) as thread:
            first = meter.provider_quota_snapshots(now=1_000_000, loaders={"codex": loader})
            second = meter.provider_quota_snapshots(now=1_000_001, loaders={"codex": loader})

        self.assertEqual(first[0]["status"], "loading")
        self.assertEqual(second[0]["status"], "loading")
        thread.assert_called_once()
        worker.start.assert_called_once()

    def test_refresh_retains_last_good_windows_and_sanitizes_unexpected_errors(self):
        good = meter.quota_provider("codex", "Codex", "ok", "test", windows=[
            meter.quota_window("codex", "codex-session", "session", "Session", 44,
                               window_seconds=1000, reset_at=1_001_000, now=1_000_000),
        ])
        meter.refresh_provider_quota("codex", lambda now: good, now=1_000_000)

        def fail(now):
            raise RuntimeError("secret bearer token")

        result = meter.refresh_provider_quota("codex", fail, now=1_000_100)

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["windows"][0]["used_percent"], 44.0)
        self.assertEqual(result["fetched_at"], 1_000_000)
        self.assertEqual(result["attempted_at"], 1_000_100)
        self.assertEqual(result["error"], "Provider quota refresh failed.")
        self.assertNotIn("secret", result["error"])


class HealthStateTests(unittest.TestCase):
    def test_health_uses_cached_inventory_without_discovering_sessions(self):
        inventory = {
            "ready": True,
            "sources": (),
            "count": 2400,
            "clients": {"codex": 2300, "claude_code": 100},
            "updated_at": 1,
        }
        with mock.patch.object(meter, "STATE", {"source": {"id": "ready"}}), \
                mock.patch.object(meter, "_SOURCE_INVENTORY", inventory), \
                mock.patch.object(meter, "page_path", return_value="/runtime/page.html"), \
                mock.patch.object(
                    meter, "all_session_sources",
                    side_effect=AssertionError("health must not discover sessions"),
                ):
            payload, status = meter.health_state()

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["state_ready"])
        self.assertTrue(payload["inventory_ready"])
        self.assertEqual(payload["sources"], 2400)
        self.assertEqual(payload["source_clients"], {"codex": 2300, "claude_code": 100})

    def test_health_never_exposes_local_paths(self):
        page = os.path.join(os.path.expanduser("~"), "Library", "Token Meter", "runtime", "page.html")
        candidates = [page, "/opt/token-meter/page.html"]
        for found in (page, None):
            with self.subTest(page_found=bool(found)), \
                    mock.patch.object(meter, "STATE", {}), \
                    mock.patch.object(meter, "page_path", return_value=found), \
                    mock.patch.object(meter, "PAGE_CANDIDATES", candidates):
                payload, status = meter.health_state()
            self.assertNotIn("page_path", payload)
            self.assertNotIn("page_candidates", payload)
            text = json.dumps(payload)
            for fragment in (os.path.expanduser("~"), "page.html", "/opt/token-meter"):
                self.assertNotIn(fragment, text)
            self.assertEqual(payload["page_ready"], bool(found))
            self.assertEqual(status, 200 if found else 503)

    def test_health_answers_runtime_ownership_without_revealing_the_path(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = os.path.join(directory, "runtime")
            os.makedirs(runtime)
            page = os.path.join(runtime, "page.html")
            cases = (
                (runtime, page, True),
                (runtime + os.sep, page, True),
                (os.path.join(directory, "other"), page, False),
                (runtime + "-old", page, False),
                (runtime, None, False),
                ("relative/runtime", page, False),
                ("x" * 5000, page, False),
            )
            for expected, found, owned in cases:
                with self.subTest(expected=expected[:40], found=bool(found)), \
                        mock.patch.object(meter, "STATE", {}), \
                        mock.patch.object(meter, "page_path", return_value=found):
                    payload, _ = meter.health_state(expected_runtime=expected)
                self.assertIs(payload["runtime_matches"], owned)
                self.assertNotIn(directory, json.dumps(payload))
            with mock.patch.object(meter, "STATE", {}), \
                    mock.patch.object(meter, "page_path", return_value=page):
                self.assertNotIn("runtime_matches", meter.health_state()[0])

    def test_health_route_reads_one_runtime_query_value(self):
        def get(query):
            handler = object.__new__(meter.H)
            handler.path = "/health" + query
            handler.headers = {"Host": "127.0.0.1:8722"}
            handler._send = mock.Mock()
            handler.send_error = mock.Mock()
            with mock.patch.object(meter, "health_state", return_value=({"ok": True}, 200)) as health:
                handler.do_GET()
            return health.call_args

        self.assertEqual(get(""), mock.call(expected_runtime=None))
        self.assertEqual(get("?runtime=%2Fopt%2Ftm"), mock.call(expected_runtime="/opt/tm"))
        self.assertEqual(get("?runtime=a&runtime=b"), mock.call(expected_runtime=""))

    def test_health_marks_undiscovered_inventory_unavailable_instead_of_zero(self):
        inventory = {
            "ready": False, "sources": (), "count": None, "clients": {}, "updated_at": None,
        }
        with mock.patch.object(meter, "STATE", {}), \
                mock.patch.object(meter, "_SOURCE_INVENTORY", inventory), \
                mock.patch.object(meter, "page_path", return_value="/runtime/page.html"):
            payload, status = meter.health_state()

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["state_ready"])
        self.assertFalse(payload["inventory_ready"])
        self.assertIsNone(payload["sources"])
        self.assertEqual(payload["source_clients"], {})

    def test_current_state_returns_loading_without_competing_with_watcher(self):
        inventory = {
            "ready": True, "sources": (), "count": 5000, "clients": {"codex": 5000},
            "updated_at": 1,
        }
        with mock.patch.object(meter, "STATE", {}), \
                mock.patch.object(meter, "_SOURCE_INVENTORY", inventory), \
                mock.patch.object(
                    meter, "newest_source",
                    side_effect=AssertionError("state request must not rediscover sessions"),
                ), \
                mock.patch.object(
                    meter, "recompute",
                    side_effect=AssertionError("state request must not rebuild history"),
                ):
            payload = meter.current_state()

        self.assertTrue(payload["loading"])
        self.assertIn("5,000 local sessions", payload["message"])

    def test_watcher_publishes_ready_empty_state_when_no_logs_exist(self):
        published = []
        inventory_updates = []
        with mock.patch.object(meter, "STATE", {}), \
                mock.patch.object(meter, "all_session_sources", return_value=[]), \
                mock.patch.object(
                    meter, "publish_source_inventory",
                    side_effect=lambda sources: inventory_updates.append(list(sources)),
                ), \
                mock.patch.object(
                    meter, "refresh_cross_session_state",
                    return_value={"sessions": [], "total_sessions": 0},
                ), \
                mock.patch.object(meter, "publish", side_effect=published.append), \
                mock.patch.object(meter.time, "monotonic", return_value=3), \
                mock.patch.object(meter.time, "sleep", side_effect=StopIteration), \
                isolated_pricing_state():
            with self.assertRaises(StopIteration):
                meter.watcher()

        self.assertEqual(inventory_updates, [[]])
        self.assertEqual(len(published), 1)
        self.assertFalse(published[0]["loading"])
        self.assertEqual(published[0]["xsession"]["total_sessions"], 0)

    def test_watcher_test_survives_leftover_repricing_state(self):
        for leftover in ({"running": True}, {"cache_revision": "p_stale"}):
            with self.subTest(leftover=leftover), \
                    mock.patch.dict(meter._pricing_recalc, {"running": leftover.get("running", False)}), \
                    mock.patch.dict(meter._pricing_state, {"cache_revision": leftover.get("cache_revision")}):
                self.test_watcher_publishes_ready_empty_state_when_no_logs_exist()


class DynamicCatalogTests(unittest.TestCase):
    def test_flattens_namespace_and_legacy_functions(self):
        catalog = meter.normalize_dynamic_tools([
            {
                "type": "namespace",
                "name": "codex_app",
                "tools": [
                    {"name": "open_page", "deferLoading": False, "description": "Open a page"},
                    {"name": "create_thread", "deferLoading": True, "description": "Create a thread"},
                ],
            },
            {"name": "mcp__jira__search", "namespace": "jira", "deferLoading": True},
        ])
        self.assertEqual(
            [row["name"] for row in catalog],
            ["mcp__codex_app__open_page", "mcp__codex_app__create_thread", "mcp__jira__search"],
        )
        self.assertEqual(catalog[0]["namespace"], "codex_app")
        self.assertEqual(catalog[2]["namespace"], "jira")
        self.assertEqual(catalog[2]["kind"], "mcp")
        self.assertEqual(meter.catalog_counts(catalog), {"advertised": 3, "eager": 1, "deferred": 2})

    def test_embedded_image_bytes_are_not_counted_as_text(self):
        chars = meter.observable_output_chars({"image_url": "data:image/png;base64," + "A" * 10000})
        self.assertLess(chars, 100)


class ClaudeDesktopDiscoveryTests(unittest.TestCase):
    def test_default_discovery_scans_standard_and_third_party_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            standard = Path(tmp) / "Claude"
            third_party = Path(tmp) / "Claude-3p"
            standard_metadata = standard / "claude-code-sessions" / "account" / "workspace" / "local_standard.json"
            third_party_metadata = third_party / "local-agent-mode-sessions" / "account" / "workspace" / "local_bedrock.json"
            standard_metadata.parent.mkdir(parents=True)
            third_party_metadata.parent.mkdir(parents=True)
            standard_metadata.write_text(json.dumps({"cliSessionId": "standard-cli"}))
            third_party_metadata.write_text(json.dumps({"cliSessionId": "bedrock-cli"}))

            with mock.patch.object(meter, "CLAUDE_DESKTOP_DATA_ROOTS", [str(standard), str(third_party)]):
                paths = set(meter.claude_desktop_metadata_paths())

        self.assertEqual(paths, {str(standard_metadata), str(third_party_metadata)})

    def test_default_discovery_recurses_below_known_session_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            standard = Path(tmp) / "Claude"
            third_party = Path(tmp) / "Claude-3p"
            nested_metadata = (
                standard / "local-agent-mode-sessions" / "account" / "workspace" /
                "additional-level" / "local_nested.json"
            )
            unrelated_metadata = standard / "unrelated" / "local_ignore.json"
            nested_metadata.parent.mkdir(parents=True)
            unrelated_metadata.parent.mkdir(parents=True)
            nested_metadata.write_text(json.dumps({"cliSessionId": "nested-cli"}))
            unrelated_metadata.write_text(json.dumps({"cliSessionId": "ignore-cli"}))

            with mock.patch.object(meter, "CLAUDE_DESKTOP_DATA_ROOTS", [str(standard), str(third_party)]):
                paths = set(meter.claude_desktop_metadata_paths())

        self.assertEqual(paths, {str(nested_metadata)})

    def test_default_discovery_prunes_the_non_session_skills_plugin_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            standard = Path(tmp) / "Claude"
            valid_metadata = (
                standard / "local-agent-mode-sessions" / "account" /
                "workspace" / "extra" / "local_session.json"
            )
            plugin_metadata = (
                standard / "local-agent-mode-sessions" / "skills-plugin" /
                "bundle" / "nested" / "local_not-a-session.json"
            )
            valid_metadata.parent.mkdir(parents=True)
            plugin_metadata.parent.mkdir(parents=True)
            valid_metadata.write_text(json.dumps({"cliSessionId": "session"}))
            plugin_metadata.write_text(json.dumps({"cliSessionId": "plugin"}))

            with mock.patch.object(
                    meter, "CLAUDE_DESKTOP_DATA_ROOTS", [str(standard)]):
                paths = set(meter.claude_desktop_metadata_paths())

        self.assertEqual(paths, {str(valid_metadata)})

    def test_indexes_desktop_metadata_by_cli_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "account" / "workspace" / "local_desktop-session.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({
                "sessionId": "local_desktop-session",
                "cliSessionId": "cli-session-id",
                "cwd": "/tmp/project",
                "title": "Desktop project task",
                "model": "claude-fable-5",
                "lastActivityAt": 123456,
            }))
            idx = meter.claude_desktop_index(tmp)
        self.assertIn("cli-session-id", idx)
        self.assertEqual(idx["cli-session-id"]["label"], "Claude Desktop")
        self.assertEqual(idx["cli-session-id"]["desktop_session_id"], "local_desktop-session")
        self.assertEqual(idx["cli-session-id"]["cwd"], "/tmp/project")
        self.assertEqual(idx["cli-session-id"]["title"], "Desktop project task")

    def test_desktop_resume_id_is_the_cli_uuid_of_project_transcripts(self):
        from token_meter.runtimes.claude import ClaudeRuntimeAdapter
        cli_id = "0f9f0062-51be-4131-acfa-51034ef23f99"
        cowork_id = "45892917-f18f-4f00-b89e-5e332838e372"
        with tempfile.TemporaryDirectory() as tmp:
            projects = Path(tmp) / "projects" / "-tmp-project"
            projects.mkdir(parents=True)
            (projects / f"{cli_id}.jsonl").write_text("{}\n")
            (projects / "not-a-uuid.jsonl").write_text("{}\n")
            agent_root = Path(tmp) / "Claude" / "local-agent-mode-sessions" / "account" / "org"
            trace = agent_root / "local_cowork" / ".claude" / "projects" / "outputs" / f"{cowork_id}.jsonl"
            trace.parent.mkdir(parents=True)
            trace.write_text("{}\n")
            (agent_root / "local_cowork.json").write_text(json.dumps({
                "sessionId": "local_cowork", "cliSessionId": cowork_id,
                "cwd": str(agent_root / "local_cowork" / "outputs"), "lastActivityAt": 1,
            }))
            adapter = ClaudeRuntimeAdapter(Path(tmp) / "projects", [Path(tmp) / "Claude"])
            records = {row["id"]: row for row in adapter.discover_legacy(None)}
        self.assertEqual(records[cli_id]["desktop_resume_id"], cli_id)
        self.assertIsNone(records["not-a-uuid"]["desktop_resume_id"])
        self.assertIsNone(records[cowork_id].get("desktop_resume_id"))

    def test_discovers_no_project_agent_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "local-agent-mode-sessions" / "account" / "org"
            metadata = root / "local_agent-session.json"
            trace = root / "local_agent-session" / ".claude" / "projects" / "outputs" / "cli-agent-id.jsonl"
            trace.parent.mkdir(parents=True)
            metadata.write_text(json.dumps({
                "sessionId": "local_agent-session",
                "cliSessionId": "cli-agent-id",
                "cwd": str(root / "local_agent-session" / "outputs"),
                "title": "No-project task",
                "lastActivityAt": 123456,
            }))
            trace.write_text("{}\n")
            idx = meter.claude_desktop_index(tmp)
            sources = meter.claude_local_agent_sources(idx)
        self.assertEqual(idx["cli-agent-id"]["source_kind"], "agent")
        self.assertEqual(idx["cli-agent-id"]["project"], "No project")
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["client"], "claude_desktop")
        self.assertEqual(sources[0]["title"], "No-project task")

    def test_agent_uses_user_selected_folder_as_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "local-agent-mode-sessions" / "account" / "org"
            metadata = root / "local_agent-session.json"
            metadata.parent.mkdir(parents=True)
            metadata.write_text(json.dumps({
                "sessionId": "local_agent-session",
                "cliSessionId": "cli-agent-id",
                "cwd": str(root / "local_agent-session" / "outputs"),
                "userSelectedFolders": ["/tmp/selected-project"],
                "lastActivityAt": 123456,
            }))
            idx = meter.claude_desktop_index(tmp)

        self.assertEqual(idx["cli-agent-id"]["cwd"], "/tmp/selected-project")
        self.assertEqual(idx["cli-agent-id"]["project"], "/tmp/selected-project")


class ToolEvidenceTests(unittest.TestCase):
    def test_tool_summary_reconciles_types_calls_and_per_execution_peak(self):
        executions = [
            {"idx": 1, "tools": [{"name": "exec", "namespace": "exec"}]},
            {"idx": 2, "tools": [
                {"name": "exec", "namespace": "exec"},
                {"name": "wait", "namespace": "wait", "skills": ["browser"]},
            ]},
        ]
        summary = meter.tool_summary(executions)
        self.assertEqual(summary["activity"]["observed_unique"], 2)
        self.assertEqual(summary["activity"]["total_calls"], 3)
        self.assertEqual(summary["activity"]["peak_calls_per_execution"], 2)
        self.assertEqual(summary["skills"], [{"name": "browser", "activations": 1}])
        self.assertFalse(summary["execution_rows_truncated"])

    def test_tool_summary_discloses_latest_execution_window(self):
        executions = [{"idx": idx, "tools": [{"name": "exec", "namespace": "exec"}]}
                      for idx in range(1, 82)]
        summary = meter.tool_summary(executions)
        self.assertEqual(summary["total_calls"], 81)
        self.assertEqual(summary["execution_rows_total"], 81)
        self.assertEqual(summary["execution_rows_shown"], 80)
        self.assertEqual(summary["execution_calls_shown"], 80)
        self.assertTrue(summary["execution_rows_truncated"])

    def test_flags_tokens_once_across_oversize_repeat_and_error_rules(self):
        calls = [
            {**meter.tool_identity("exec_command"), "output_tokens": 9000, "ts": 100,
             "args_fingerprint": "same", "error": False},
            {**meter.tool_identity("exec_command"), "output_tokens": 100, "ts": 110,
             "args_fingerprint": "same", "error": False},
            {**meter.tool_identity("mcp__jira__search"), "output_tokens": 50, "ts": 120,
             "args_fingerprint": "jira", "error": True},
        ]
        evidence = meter.summarize_tool_evidence(calls)
        self.assertEqual(evidence["total_output_tokens"], 9150)
        self.assertEqual(evidence["flagged_tokens"], 9150)
        self.assertEqual(evidence["oversized_calls"], 1)
        self.assertEqual(evidence["repeat_calls"], 1)
        self.assertEqual(evidence["errors"], 1)

    def test_global_aggregation_finds_reported_unused_mcp(self):
        catalog = [{
            "name": "mcp__salesforce__query", "namespace": "salesforce", "kind": "mcp",
            "defer_loading": False, "definition_tokens": 100,
        }]
        rows = []
        for idx in range(5):
            calls = []
            if idx == 0:
                calls = [{**meter.tool_identity("exec_command"), "output_tokens": 9000, "ts": 100,
                          "args_fingerprint": "one", "error": False}]
            rows.append({
                "id": f"s{idx}", "path": f"/tmp/s{idx}", "provider": "codex",
                "project": "/repo", "_tool_evidence": meter.summarize_tool_evidence(calls, catalog),
            })
        waste = meter.global_tool_waste(rows)
        self.assertEqual(waste["total_calls"], 1)
        self.assertEqual(waste["oversized_calls"], 1)
        candidate = next(row for row in waste["by_name"] if row["name"] == "mcp__salesforce__query")
        self.assertEqual(candidate["recommendation"], "disable")
        self.assertEqual(candidate["advertised_sessions"], 5)
        self.assertEqual(candidate["sessions_used"], 0)

    def test_tracks_eager_definition_tax_and_skill_trace_references(self):
        calls = [{**meter.tool_identity("exec_command"), "output_tokens": 10, "ts": 100,
                  "args_fingerprint": "x", "skills": ["execution-plan"]}]
        catalog = [
            {"name": "unused_eager", "namespace": "app", "kind": "tool",
             "defer_loading": False, "definition_tokens": 120},
            {"name": "later", "namespace": "app", "kind": "tool",
             "defer_loading": True, "definition_tokens": 80},
        ]
        evidence = meter.summarize_tool_evidence(calls, catalog)
        self.assertEqual(evidence["definition_tokens"], 200)
        self.assertEqual(evidence["unused_eager_definition_tokens"], 120)
        self.assertEqual(evidence["skills"][0]["name"], "execution-plan")

    def test_plain_tool_names_stay_separate_by_runtime(self):
        call = {**meter.tool_identity("read_file"), "output_tokens": 10, "ts": 100,
                "args_fingerprint": "x", "error": False}
        rows = [
            {"id": "codex", "provider": "codex", "runtime": "Codex", "project": "/repo",
             "_tool_evidence": meter.summarize_tool_evidence([call])},
            {"id": "cursor", "provider": "cursor", "runtime": "Cursor", "project": "/repo",
             "_tool_evidence": meter.summarize_tool_evidence([call])},
        ]
        waste = meter.global_tool_waste(rows)
        tools = [row for row in waste["inventory_tools"] if row["name"] == "read_file"]
        self.assertEqual({row["id"] for row in tools}, {"Codex::read_file", "Cursor::read_file"})
        self.assertEqual({row["runtime"] for row in tools}, {"Codex", "Cursor"})
        self.assertEqual(waste["provider_sessions"], {"codex": 1, "cursor": 1})
        self.assertEqual(waste["runtime_sessions"], {"Codex": 1, "Cursor": 1})

    def test_skill_name_is_inferred_from_skill_descriptor_path(self):
        value = {"cmd": "sed -n '1,80p' /tmp/skills/execution-plan/SKILL.md"}
        self.assertEqual(meter.skill_names_from_value(value), ["execution-plan"])

    def test_native_skill_tool_input_records_the_invoked_skill(self):
        value = {"skill": "documentation-skills:sharepoint-docs", "args": "find the policy"}
        self.assertEqual(
            meter.skill_names_from_value(value, "Skill"),
            ["sharepoint-docs"],
        )
        self.assertEqual(meter.skill_names_from_value(value, "OtherTool"), [])

    def test_native_claude_skill_call_excludes_its_pack_from_review_candidates(self):
        objs = [{
            "type": "assistant", "timestamp": "2026-08-05T00:00:00Z",
            "message": {
                "id": "message-1", "content": [{
                    "type": "tool_use", "id": "tool-1", "name": "Skill",
                    "input": {"skill": "sharepoint-docs"},
                }],
            },
        }]
        calls = meter.claude_tool_call_evidence(objs)
        evidence = meter.summarize_tool_evidence(calls)
        waste = meter.global_tool_waste([{
            "id": "claude-session", "provider": "claude", "runtime": "Claude",
            "project": "/repo", "_tool_evidence": evidence,
        }])
        usage = next(row for row in waste["skills"] if row["name"] == "sharepoint-docs")
        groups = meter.capability_control_groups([], [{
            "id": "sharepoint-docs", "name": "sharepoint-docs", "runtime": "Claude",
            "plugin_id": "documentation-skills@skills-marketplace", "mutable": True,
            "enabled": True, "reviewable": True, "used": bool(usage),
            "activations": usage["activations"], "last_used": usage["last_used"],
        }])
        summary = meter.optional_capability_summary(groups)

        self.assertEqual(calls[0]["skills"], ["sharepoint-docs"])
        self.assertEqual(summary["used"], 1)
        self.assertEqual(summary["review_candidates"], [])

    def test_skill_frontmatter_parses_name_description_only(self):
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("---\nname: caveman\ndescription: Ultra-compressed communication mode\n---\n\nbody")
            path = f.name
        try:
            fm = meter._skill_frontmatter(path)
            self.assertEqual(fm, {"name": "caveman", "description": "Ultra-compressed communication mode"})
        finally:
            os.unlink(path)

    def test_skill_frontmatter_parses_allowed_tools(self):
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("---\nname: firewall-manager\nallowed-tools: Bash, Read\n---\n\nbody")
            path = f.name
        try:
            fm = meter._skill_frontmatter(path)
            self.assertEqual(fm["allowed-tools"], "Bash, Read")
        finally:
            os.unlink(path)

    def test_skill_frontmatter_returns_empty_for_missing_file(self):
        fm = meter._skill_frontmatter("/nonexistent/path/SKILL.md")
        self.assertEqual(fm, {})

    def test_skill_has_measurable_capabilities_with_allowed_tools(self):
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("---\nname: firewall\ndescription: Manage firewall\nallowed-tools: Bash\n---\n\nbody")
            path = f.name
        try:
            self.assertTrue(meter._skill_has_measurable_capabilities(path))
        finally:
            os.unlink(path)

    def test_skill_without_capability_metadata_has_unknown_measurement(self):
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("---\nname: caveman\ndescription: Be terse\n---\n\nbody")
            path = f.name
        try:
            self.assertFalse(meter._skill_has_measurable_capabilities(path))
            self.assertEqual(meter._skill_measurability(path), "unknown")
        finally:
            os.unlink(path)

    def test_skill_requires_mcp_block_is_measurable(self):
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("---\nname: sourcegraph-search\nrequires-mcp:\n  - sourcegraph\n---\n\nbody")
            path = f.name
        try:
            self.assertEqual(meter._skill_measurability(path), "measurable")
            self.assertTrue(meter._skill_has_measurable_capabilities(path))
        finally:
            os.unlink(path)

    def test_skill_allowed_tools_block_is_measurable(self):
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("---\nname: firewall\nallowed-tools:\n  - Bash\n  - Read\n---\n\nbody")
            path = f.name
        try:
            self.assertEqual(meter._skill_measurability(path), "measurable")
        finally:
            os.unlink(path)

    def test_skill_empty_allowed_tools_is_instruction_only(self):
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("---\nname: caveman\nallowed-tools: []\n---\n\nbody")
            path = f.name
        try:
            self.assertEqual(meter._skill_measurability(path), "instruction")
            self.assertFalse(meter._skill_has_measurable_capabilities(path))
        finally:
            os.unlink(path)

    def test_instruction_only_skill_pack_excluded_from_review_candidates(self):
        skill_items = [
            {"id": "skill:claude:caveman:caveman", "name": "caveman", "runtime": "Claude",
             "plugin_id": "caveman@skills-marketplace", "mutable": True, "enabled": True,
             "used": False, "activations": 0, "measurement": "instruction", "reviewable": True},
        ]
        groups = meter.capability_control_groups([], skill_items)
        self.assertEqual(groups[0]["unmeasurable"], True)
        self.assertEqual(groups[0]["measurement"], "instruction")
        summary = meter.optional_capability_summary(groups)
        self.assertEqual(summary["enabled"], 1)
        self.assertEqual(summary["unused"], 0)
        self.assertEqual(summary["unmeasurable_packs"], 1)
        self.assertEqual(summary["instruction_packs"], 1)
        self.assertEqual(summary["unknown_evidence_packs"], 0)
        self.assertEqual(summary["review_candidates"], [])

    def test_unknown_skill_pack_excluded_from_review_candidates(self):
        skill_items = [
            {"id": "skill:claude:unknown:unknown", "name": "unknown", "runtime": "Claude",
             "plugin_id": "unknown@skills-marketplace", "mutable": True, "enabled": True,
             "used": False, "activations": 0, "measurement": "unknown", "reviewable": True},
        ]
        groups = meter.capability_control_groups([], skill_items)
        summary = meter.optional_capability_summary(groups)
        self.assertEqual(groups[0]["measurement"], "unknown")
        self.assertTrue(groups[0]["unmeasurable"])
        self.assertEqual(summary["unused"], 0)
        self.assertEqual(summary["unknown_evidence_packs"], 1)
        self.assertEqual(summary["review_candidates"], [])

    def test_mixed_measurement_pack_is_not_a_review_candidate(self):
        skill_items = [
            {"id": "instruction", "name": "caveman", "runtime": "Claude",
             "plugin_id": "mixed@skills-marketplace", "mutable": True, "enabled": True,
             "used": False, "activations": 0, "measurement": "instruction", "reviewable": True},
            {"id": "tool", "name": "sourcegraph", "runtime": "Claude",
             "plugin_id": "mixed@skills-marketplace", "mutable": True, "enabled": True,
             "used": False, "activations": 0, "measurement": "measurable", "reviewable": True},
        ]
        groups = meter.capability_control_groups([], skill_items)
        summary = meter.optional_capability_summary(groups)
        self.assertEqual(groups[0]["measurement"], "unknown")
        self.assertTrue(groups[0]["unmeasurable"])
        self.assertEqual(summary["unused"], 0)
        self.assertEqual(summary["review_candidates"], [])

    def test_measurable_skill_pack_remains_review_candidate(self):
        skill_items = [
            {"id": "skill:codex:browser:browser", "name": "browser", "runtime": "Codex",
             "plugin_id": "browser@personal", "mutable": True, "enabled": True,
             "used": False, "activations": 0, "measurement": "measurable", "reviewable": True},
        ]
        groups = meter.capability_control_groups([], skill_items)
        self.assertEqual(groups[0]["unmeasurable"], False)
        self.assertEqual(groups[0]["measurement"], "measurable")
        summary = meter.optional_capability_summary(groups)
        self.assertEqual(summary["unused"], 1)
        self.assertEqual(summary["unmeasurable_packs"], 0)
        self.assertEqual(len(summary["review_candidates"]), 1)

    def test_used_skill_pack_never_unmeasurable(self):
        skill_items = [
            {"id": "skill:claude:caveman:caveman", "name": "caveman", "runtime": "Claude",
             "plugin_id": "caveman@skills-marketplace", "mutable": True, "enabled": True,
             "used": True, "activations": 3, "measurement": "instruction", "reviewable": True},
        ]
        groups = meter.capability_control_groups([], skill_items)
        summary = meter.optional_capability_summary(groups)
        self.assertEqual(groups[0]["measurement"], "measurable")
        self.assertEqual(summary["used"], 1)
        self.assertEqual(summary["unused"], 0)
        self.assertEqual(summary["unmeasurable_packs"], 0)
        self.assertEqual(len(summary["review_candidates"]), 0)

    def test_token_meter_diagnostics_are_accounted_but_never_recommended_for_cleanup(self):
        calls = [{**meter.tool_identity("mcp__tokenmeter__check"), "output_tokens": 50000,
                  "ts": 100, "args_fingerprint": "one", "error": False}]
        rows = [{
            "id": "s1", "path": "/tmp/s1", "provider": "codex", "project": "/repo",
            "_tool_evidence": meter.summarize_tool_evidence(calls),
        }]
        waste = meter.global_tool_waste(rows)
        diagnostic = next(row for row in waste["by_name"] if row["namespace"] == "tokenmeter")
        self.assertTrue(diagnostic["diagnostic"])
        self.assertEqual(diagnostic["recommendation"], "keep")
        self.assertFalse(any("tokenmeter" in row.get("key", "") for row in waste["insights"]))


if __name__ == "__main__":
    unittest.main()
