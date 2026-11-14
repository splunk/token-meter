import unittest
import datetime
import http.client
import json
import os
import re
import subprocess
import time
from pathlib import Path
from unittest import mock
import meter
from tests.dashboard_page import DASHBOARD_PAGE


class ToolUsageEvidenceRealityTests(unittest.TestCase):
    """Tools-tab usage evidence must match what the raw traces contain."""

    @staticmethod
    def inventory(waste, *, codex_mcp=None, claude_mcp=None, skills=None):
        with mock.patch.object(meter, "codex_mcp_states", return_value=codex_mcp or {}), \
                mock.patch.object(meter, "claude_mcp_states", return_value=claude_mcp or {}), \
                mock.patch.object(meter, "discovered_skills",
                                  side_effect=lambda usage=None: [dict(row) for row in (skills or [])]), \
                mock.patch.object(meter, "claude_desktop_index", return_value={}), \
                mock.patch.object(meter, "claude_local_agent_sources", return_value=[]):
            return meter.capability_inventory(waste)

    @staticmethod
    def codex_exec(code, call_id="exec-1"):
        return {"timestamp": "2026-10-01T00:00:00.000Z", "payload": {
            "type": "custom_tool_call", "name": "exec", "call_id": call_id, "input": code,
        }}

    def test_codex_exec_records_nested_mcp_and_app_tool_calls(self):
        code = (
            "const a = await tools.mcp__sharepoint__search({q: 'x'});\n"
            "await tools.mcp__sharepoint__search({q: 'y'});\n"
            "await tools.codex_app__load_workspace_dependencies({});\n"
            "await tools.exec_command({cmd: 'ls'});\n"
            "await tools.web__run({q: 'z'});\n"
        )
        calls = meter.codex_tool_call_evidence([self.codex_exec(code)])
        nested = {row["name"]: row for row in calls if row.get("nested")}
        self.assertEqual(
            set(nested),
            {"mcp__sharepoint__search", "mcp__codex_app__load_workspace_dependencies"},
        )
        self.assertEqual(nested["mcp__sharepoint__search"]["kind"], "mcp")
        self.assertEqual(nested["mcp__sharepoint__search"]["namespace"], "sharepoint")
        evidence = meter.summarize_tool_evidence(calls)
        rows = {row["name"]: row for row in evidence["tools"]}
        self.assertEqual(rows["mcp__sharepoint__search"]["calls"], 2)
        self.assertEqual(rows["exec"]["calls"], 1)
        self.assertEqual(evidence["total_calls"], 1)

    def test_codex_app_namespace_keeps_one_canonical_identity(self):
        from token_meter.runtimes.codex import codex_mcp_tool_name
        self.assertEqual(
            codex_mcp_tool_name("read_thread", "codex_app"), "mcp__codex_app__read_thread",
        )
        self.assertEqual(
            codex_mcp_tool_name("read_thread", "mcp__codex_app"), "mcp__codex_app__read_thread",
        )
        for builtin in ("collaboration", "clock", "web", "multi_agent_v1"):
            self.assertEqual(codex_mcp_tool_name("wait_agent", builtin), "wait_agent")

    def test_codex_dynamic_catalog_matches_code_mode_calls(self):
        from token_meter.runtimes.codex import _catalog
        catalog = _catalog([
            {"name": "codex_app", "tools": [
                {"name": "wait_threads", "description": "wait"},
                {"name": "share_thread", "description": "share"},
            ]},
            {"name": "exec_command", "description": "flat"},
        ])
        names = {row["name"]: row for row in catalog}
        normalized = meter.normalize_dynamic_tools([
            {"name": "codex_app", "tools": [{"name": "wait_threads"}]},
            {"name": "exec_command"},
        ])
        self.assertEqual(
            [(row["name"], row["kind"]) for row in normalized],
            [("mcp__codex_app__wait_threads", "mcp"), ("exec_command", "tool")],
        )
        self.assertIn("mcp__codex_app__wait_threads", names)
        self.assertEqual(names["mcp__codex_app__wait_threads"]["kind"], "mcp")
        self.assertEqual(names["mcp__codex_app__wait_threads"]["namespace"], "codex_app")
        self.assertIn("exec_command", names)
        calls = meter.codex_tool_call_evidence([
            self.codex_exec("await tools.mcp__codex_app__wait_threads({});"),
        ])
        evidence = meter.summarize_tool_evidence(calls, catalog)
        share = names["mcp__codex_app__share_thread"]["definition_tokens"]
        flat = names["exec_command"]["definition_tokens"]
        self.assertEqual(evidence["unused_eager_definition_tokens"], share + flat)

    def test_duplicated_claude_transcript_copies_count_each_tool_once(self):
        def assistant(block_id, name="mcp__workspace__bash"):
            return {
                "type": "assistant", "timestamp": "2026-10-01T00:00:00Z",
                "message": {"id": "msg-1", "usage": {"output_tokens": 5}, "content": [
                    {"type": "tool_use", "id": block_id, "name": name, "input": {}},
                ]},
            }
        objs = [assistant("tool-1"), assistant("tool-1"), assistant("tool-2")]
        calls = meter.claude_tool_call_evidence(objs)
        self.assertEqual(len(calls), 2)

    def test_claude_tool_rows_keep_their_own_runtime(self):
        call = {**meter.tool_identity("Bash"), "output_tokens": 1, "ts": 100,
                "args_fingerprint": "x", "error": False}
        rows = [
            {"id": "a", "provider": "claude", "runtime": "Claude-3P", "project": "/r",
             "_tool_evidence": meter.summarize_tool_evidence([call])},
            {"id": "b", "provider": "claude", "runtime": "Claude Code", "project": "/r",
             "_tool_evidence": meter.summarize_tool_evidence([call, call])},
        ]
        waste = meter.global_tool_waste(rows)
        bash = {row["runtime"]: row["calls"] for row in waste["inventory_tools"]
                if row["name"] == "Bash"}
        self.assertEqual(bash, {"Claude-3P": 1, "Claude Code": 2})

    def test_desktop_code_tab_sessions_cover_claude_plugin_packs(self):
        skill = {
            "id": "skill:claude:custom@personal:custom", "name": "custom",
            "runtime": "Claude", "source": "User-installed plugin",
            "plugin_id": "custom@personal", "mutable": True, "enabled": True,
            "used": False, "reviewable": True, "measurement": "measurable",
            "unmeasurable": False, "activations": 0, "last_used": "Never",
            "setting_path": "~/.claude/settings.json",
        }
        code_tab = os.path.join(meter.CLAUDE_PROJECTS, "-repo", "session.jsonl")
        cowork = "/tmp/Claude-3p/local-agent-mode-sessions/x/.claude/projects/s/a.jsonl"
        waste = meter.global_tool_waste([
            {"id": "tab", "provider": "claude", "runtime": "Claude-3P",
             "path": code_tab, "project": "/r", "_tool_evidence": {}},
            {"id": "cli", "provider": "claude", "runtime": "Claude Code",
             "path": code_tab.replace("session", "cli"), "project": "/r", "_tool_evidence": {}},
            {"id": "cowork", "provider": "claude", "runtime": "Claude-3P",
             "path": cowork, "project": "/r", "_tool_evidence": {}},
        ])
        capabilities = self.inventory(waste, skills=[skill])
        self.assertEqual(capabilities["control_groups"][0]["scanned_sessions"], 2)

    def test_skill_activations_stay_with_the_runtime_that_used_them(self):
        evidence = meter.summarize_tool_evidence([{
            **meter.tool_identity("exec"), "output_tokens": 0, "ts": 100,
            "args_fingerprint": "x", "error": False, "skills": ["brainstorming"],
        }])
        cowork = "/tmp/Claude-3p/local-agent-mode-sessions/x/.claude/projects/s/a.jsonl"
        waste = meter.global_tool_waste([
            {"id": "codex", "provider": "codex", "runtime": "Codex", "project": "/r",
             "path": "/tmp/codex.jsonl", "_tool_evidence": evidence},
            {"id": "cowork", "provider": "claude", "runtime": "Claude-3P", "project": "/r",
             "path": cowork, "_tool_evidence": evidence},
        ])
        rows = [
            {"name": "brainstorming", "runtime": "Codex", "measurement": "measurable"},
            {"name": "brainstorming", "runtime": "Claude", "measurement": "measurable"},
            {"name": "brainstorming", "runtime": "Claude Desktop", "measurement": "measurable"},
        ]
        meter.invalidate_discovered_skill_cache()
        try:
            with mock.patch.object(meter, "_scan_discovered_skills", return_value=rows):
                skills = meter.discovered_skills(waste["skills"])
        finally:
            meter.invalidate_discovered_skill_cache()
        used = {row["runtime"]: row["activations"] for row in skills}
        self.assertEqual(used, {"Codex": 1, "Claude": 0, "Claude Desktop": 1})

    def test_skill_usage_is_not_truncated_and_rejects_template_names(self):
        self.assertEqual(
            meter.skill_names_from_value(
                "cat skills/${n}/SKILL.md skills/{name}/SKILL.md "
                "skills/token-meter-*/SKILL.md skills/docx/SKILL.md"
            ),
            ["docx"],
        )
        calls = [{**meter.tool_identity("exec"), "output_tokens": 0, "ts": 100,
                  "args_fingerprint": str(index), "error": False,
                  "skills": [f"skill-{index:03d}"]} for index in range(120)]
        waste = meter.global_tool_waste([{
            "id": "codex", "provider": "codex", "runtime": "Codex", "project": "/r",
            "_tool_evidence": meter.summarize_tool_evidence(calls),
        }])
        self.assertEqual(len(waste["skills"]), 120)

    def test_mcp_without_configuration_is_not_reported_disabled(self):
        waste = {"inventory_tools": [{
            "id": "mcp__workspace__bash", "name": "mcp__workspace__bash",
            "display": "bash", "kind": "mcp", "namespace": "workspace",
            "mcp_server": "workspace", "runtime": "Claude-3P", "providers": ["claude"],
            "calls": 9, "output_tokens": 10, "last_ts": 100, "last_used": "2026-10-01",
        }]}
        row = next(item for item in self.inventory(waste)["items"] if item["type"] == "mcp")
        self.assertEqual(row["configuration"], "Not in config")
        self.assertEqual(row["state"], "Not in config")
        self.assertIsNone(row["enabled"])
        self.assertEqual(row["runtime"], "Claude")

    def test_codex_host_tool_groups_are_not_mcp_disable_candidates(self):
        from token_meter.domain.tools import session_capabilities
        catalog = meter.normalize_dynamic_tools([
            {"name": "codex_app", "tools": [{"name": "navigate_to_codex_page"}]},
            {"name": "plugin_management", "tools": [{"name": "uninstall_plugin"}]},
        ])
        self.assertTrue(all(row.get("host_provided") for row in catalog))
        rows = [{
            "id": f"s{index}", "provider": "codex", "runtime": "Codex", "project": "/r",
            "path": f"/tmp/s{index}.jsonl",
            "_tool_evidence": meter.summarize_tool_evidence([], catalog),
        } for index in range(6)]
        waste = meter.global_tool_waste(rows)
        self.assertEqual(
            [row["name"] for row in waste["inventory_tools"] if row["recommendation"] == "disable"],
            [],
        )
        self.assertNotIn("MCP disable candidate", [row.get("title") for row in waste["insights"]])
        self.assertEqual(
            session_capabilities(rows[0]["_tool_evidence"])["mcp_servers"],
            {"loaded": None, "used": 0},
        )
        items = self.inventory(waste)["items"]
        self.assertEqual([row["name"] for row in items if row["type"] == "mcp"], [])
        tools = {row["identity"]: row for row in items if row["type"] == "tool"}
        self.assertEqual(tools["mcp__codex_app__navigate_to_codex_page"]["runtime"], "Codex")

    def test_codex_host_tool_group_calls_are_host_provided(self):
        calls = meter.codex_tool_call_evidence([
            self.codex_exec("await tools.mcp__codex_app__wait_threads({});"),
            {"timestamp": "2026-10-01T00:00:01Z", "payload": {
                "type": "function_call", "name": "read_thread", "namespace": "codex_app",
                "call_id": "c2", "arguments": "{}",
            }},
        ])
        hosted = {row["name"]: row.get("host_provided") for row in calls if row["kind"] == "mcp"}
        self.assertEqual(hosted, {
            "mcp__codex_app__wait_threads": True, "mcp__codex_app__read_thread": True,
        })

    def test_advertised_only_mcp_has_runtime_and_no_call_claim(self):
        catalog = [{"name": "mcp__jira__search", "namespace": "jira", "kind": "mcp",
                    "defer_loading": True, "definition_tokens": 10}]
        waste = meter.global_tool_waste([{
            "id": "s", "provider": "codex", "runtime": "Codex", "project": "/r",
            "_tool_evidence": meter.summarize_tool_evidence([], catalog),
        }])
        row = next(item for item in self.inventory(waste)["items"] if item["type"] == "mcp")
        self.assertEqual((row["name"], row["runtime"], row["calls"]), ("jira", "Codex", 0))
        script = Path(DASHBOARD_PAGE).read_text()
        self.assertIn("Advertised in traces; not in Codex or Claude config", script)

    def test_nested_calls_are_daily_and_labelled_as_call_sites(self):
        calls = meter.codex_tool_call_evidence([
            self.codex_exec("await tools.mcp__sharepoint__search({});"),
        ])
        evidence = meter.summarize_tool_evidence(calls)
        row = next(item for item in evidence["tools"] if item["name"] == "mcp__sharepoint__search")
        self.assertEqual(row["nested_calls"], 1)
        self.assertEqual([day["calls"] for day in row["daily"]], [1])
        waste = meter.global_tool_waste([{
            "id": "s", "provider": "codex", "runtime": "Codex", "project": "/r",
            "_tool_evidence": evidence,
        }])
        mcp = next(item for item in self.inventory(waste)["items"] if item["type"] == "mcp")
        self.assertEqual(mcp["nested_calls"], 1)
        script = Path(DASHBOARD_PAGE).read_text()
        self.assertIn("code-mode call site", script)

    def test_host_tools_keep_non_disable_advice(self):
        call = {**meter.tool_identity("mcp__codex_app__read_thread"), "output_tokens": 30000,
                "ts": 100, "args_fingerprint": "x", "error": False, "host_provided": True}
        waste = meter.global_tool_waste([{
            "id": "s", "provider": "codex", "runtime": "Codex", "project": "/r",
            "_tool_evidence": meter.summarize_tool_evidence([call]),
        }])
        row = next(item for item in waste["inventory_tools"]
                   if item["name"] == "mcp__codex_app__read_thread")
        self.assertEqual(row["recommendation"], "narrow_results")

    def test_host_tools_get_no_project_scope_advice(self):
        def call(index):
            return {**meter.tool_identity("mcp__codex_app__wait_threads"), "output_tokens": 1,
                    "ts": 100 + index, "args_fingerprint": str(index), "error": False,
                    "host_provided": True}
        waste = meter.global_tool_waste([{
            "id": "s", "provider": "codex", "runtime": "Codex", "project": "/r",
            "_tool_evidence": meter.summarize_tool_evidence([call(i) for i in range(6)]),
        }])
        row = next(item for item in waste["inventory_tools"]
                   if item["name"] == "mcp__codex_app__wait_threads")
        self.assertEqual(row["recommendation"], "keep")
        self.assertEqual(row["reason"], "Provided by the runtime itself; not a configurable MCP server.")

    def test_session_catalog_host_groups_are_not_used_mcp_servers(self):
        from token_meter.domain.tools import session_capabilities
        catalog = meter.normalize_dynamic_tools([
            {"name": "newapp", "tools": [{"name": "open"}]},
        ])
        evidence = meter.summarize_tool_evidence(meter.codex_tool_call_evidence([
            self.codex_exec("await tools.mcp__newapp__open({});"),
        ]), catalog)
        self.assertEqual(
            session_capabilities(evidence)["mcp_servers"], {"loaded": None, "used": 0},
        )

    def test_builder_recap_tool_total_excludes_code_mode_call_sites(self):
        from token_meter.domain.builder_recap import _period_rollup
        evidence = meter.summarize_tool_evidence(meter.codex_tool_call_evidence([
            self.codex_exec("await tools.mcp__sharepoint__search({});"),
        ]))
        day = datetime.date.fromtimestamp(evidence["tools"][0]["last_ts"] or 1790812800)
        rollup = _period_rollup([{
            "id": "s", "provider": "codex", "runtime": "Codex", "_tool_evidence": evidence,
        }], day, day)
        self.assertEqual(rollup["tool_calls"], 1)
        self.assertEqual(sorted(rollup["tools"].values()), [1, 1])

    def test_codex_multi_part_mcp_namespace_keeps_server(self):
        from token_meter.runtimes.codex import codex_mcp_tool_name
        self.assertEqual(codex_mcp_tool_name("x", "mcp__a__b"), "mcp__a__x")
        self.assertEqual(codex_mcp_tool_name("x", "foo__bar"), "x")

    def test_tools_page_renders_unconfigured_mcp_without_off_claims(self):
        script = Path(DASHBOARD_PAGE).read_text()
        start = script.index("function capabilityConfigurationText(row){")
        body = script[start:script.index("\n}", start)]
        self.assertNotIn("row.enabled?'Enabled':'Disabled'", body.replace(" ", ""))
        self.assertIn("row.configured===false", script)
        for handler in ("$('c-type-filter').onclick", "$('c-state-filter').onclick"):
            line = script[script.index(handler):].split("\n", 1)[0]
            self.assertIn("paintCapabilityFilters()", line)

    def test_codex_sanitized_mcp_names_match_configured_server(self):
        waste = {"inventory_tools": [{
            "id": "mcp__ghost_mcp_proxy__slack", "name": "mcp__ghost_mcp_proxy__slack",
            "display": "slack", "kind": "mcp", "namespace": "ghost_mcp_proxy",
            "mcp_server": "ghost_mcp_proxy", "runtime": "Codex", "providers": ["codex"],
            "calls": 4, "output_tokens": 0, "last_ts": 100, "last_used": "2026-10-01",
        }]}
        items = self.inventory(waste, codex_mcp={"ghost-mcp-proxy": True})["items"]
        mcps = {row["name"]: row for row in items if row["type"] == "mcp"}
        self.assertEqual(set(mcps), {"ghost-mcp-proxy"})
        self.assertEqual(mcps["ghost-mcp-proxy"]["calls"], 4)
        self.assertTrue(mcps["ghost-mcp-proxy"]["used"])
        self.assertEqual(mcps["ghost-mcp-proxy"]["configuration"], "Enabled")


class DashboardThemeTests(unittest.TestCase):
    """Theme choice is a browser preference applied before first paint and offered in Settings and ⌘K."""

    def setUp(self):
        self.page = Path(DASHBOARD_PAGE).read_text(encoding="utf-8")

    def run_boot(self, stored, throws=False):
        boot = re.search(r"<script id=theme-boot>(.*?)</script>", self.page, re.S).group(1)
        script = (
            "const document={documentElement:{dataset:{theme:'classic'}}};const window={};"
            f"const localStorage={{getItem(){{if({json.dumps(throws)})throw new Error('blocked');return {json.dumps(stored)};}}}};"
            f"{boot};console.log(document.documentElement.dataset.theme);"
        )
        return subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout.strip()

    def test_boot_script_applies_only_known_stored_themes(self):
        self.assertIn("<html lang=en data-theme=classic>", self.page)
        self.assertLess(self.page.index("<script id=theme-boot>"), self.page.index("<style>"))
        self.assertEqual(self.run_boot("voltage"), "voltage")
        self.assertEqual(self.run_boot("monolith"), "monolith")
        self.assertEqual(self.run_boot("nebula"), "nebula")
        self.assertEqual(self.run_boot("not-a-theme"), "classic")
        self.assertEqual(self.run_boot(None), "classic")
        self.assertEqual(self.run_boot("voltage", throws=True), "classic")

    def test_random_theme_is_retired_and_stored_value_falls_back_to_classic(self):
        self.assertEqual(self.run_boot("random"), "classic")
        self.assertNotIn("THEME_RANDOM", self.page)
        self.assertNotIn("theme-random", self.page)
        self.assertNotIn("Randomize daily", self.page)

    def test_every_registered_theme_boots_and_the_picker_uses_a_four_column_grid(self):
        for theme in ("classic", "nebula", "monolith", "voltage", "phosphor", "omarchy", "jadehorse", "ferrous", "afterglow"):
            self.assertEqual(self.run_boot(theme), theme)
        self.assertIn(".themePicker{display:grid;grid-template-columns:repeat(4,minmax(0,1fr))", self.page)

    def test_theme_registry_normalizes_and_feeds_the_palette(self):
        start = self.page.index("const THEMES=[")
        registry = self.page[start:self.page.index("let activeTheme=", start)]
        script = registry + (
            "console.log(JSON.stringify({ids:THEMES.map(t=>t.id),bad:normalizeThemeId('x'),ok:normalizeThemeId('voltage'),"
            "label:themeLabel('monolith'),key:THEME_STORAGE_KEY,random:normalizeThemeId('random')}));"
        )
        result = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(result, {"ids": ["classic", "nebula", "monolith", "voltage", "phosphor", "omarchy", "jadehorse", "ferrous", "afterglow"], "bad": "classic", "ok": "voltage",
                                  "label": "Monolith", "key": "tm_theme", "random": "classic"})
        self.assertIn("...THEMES.map(theme=>({id:`theme-${theme.id}`,label:`Theme · ${theme.label}`", self.page)
        self.assertIn("if(command.action==='theme'){setTheme(command.theme);return;}", self.page)
        self.assertIn("route:'settings-appearance'", self.page)

    def test_rapid_theme_changes_keep_page_and_storage_in_sync(self):
        start = self.page.index("function setTheme(")
        body = self.page[start:self.page.index("\n}\n", start) + 2]
        script = (
            "const THEMES=[{id:'nebula'},{id:'monolith'},{id:'voltage'}];"
            "const normalizeThemeId=v=>THEMES.some(t=>t.id===v)?v:'nebula';const THEME_STORAGE_KEY='tm_theme';"
            "const resolveTheme=v=>v;let themePreference='nebula';"
            "let stored=null,repaints=0,queued=[];const localStorage={setItem(k,v){stored=v;}};"
            "const pageSignalReducedMotionQuery={matches:false};"
            "const document={documentElement:{dataset:{theme:'nebula'}},startViewTransition(cb){queued.push(cb);return {};}};"
            "function paintThemePicker(){}function repaintThemedSurfaces(){repaints++;}"
            "let activeTheme='nebula';" + body +
            "setTheme('voltage');setTheme('monolith');setTheme('nebula');queued.forEach(cb=>cb());"
            "console.log(JSON.stringify({page:document.documentElement.dataset.theme,stored,active:activeTheme,repaints}));"
        )
        result = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(result["page"], "nebula")
        self.assertEqual(result["stored"], "nebula")
        self.assertEqual(result["active"], "nebula")
        self.assertGreaterEqual(result["repaints"], 1)

    def test_performance_page_follows_the_dashboard_theme(self):
        perf = Path(__file__).resolve().parents[1].joinpath("performance.html").read_text(encoding="utf-8")
        boot = re.compile(r"<script id=theme-boot>.*?</script>", re.S)
        self.assertEqual(boot.search(perf).group(0), boot.search(self.page).group(0))
        self.assertIn("<html lang=en data-theme=classic>", perf)
        block = re.compile(r"\n((?::root,)?\[data-theme=(\w+)\])\{([^{}]*)\}")
        dashboard = {m.group(2): dict(re.findall(r"--([\w-]+):([^;]+);", m.group(3))) for m in block.finditer(self.page)}
        performance = {m.group(2): dict(re.findall(r"--([\w-]+):([^;]+);", m.group(3))) for m in block.finditer(perf)}
        self.assertEqual(sorted(performance), sorted(dashboard))
        self.assertIn(":root,[data-theme=classic]{", perf)
        for theme, tokens in performance.items():
            with self.subTest(theme=theme):
                self.assertTrue(tokens)
                shared = {k: v for k, v in tokens.items() if not k.startswith("card-")}
                self.assertEqual(shared, {k: dashboard[theme][k] for k in shared})
                card = sorted(k for k in tokens if k.startswith("card-"))
                self.assertEqual(card, ["card-a-rgb", "card-b-rgb", "card-c-rgb", "card-foot", "card-glow-rgb",
                                        "card-hero", "card-hi-rgb", "card-idle-rgb", "card-paper-a-rgb",
                                        "card-paper-b-rgb"])
        style = perf[perf.index("<style>"):perf.index("</style>")]
        chrome = block.sub("", style)
        self.assertNotIn("#00bceb", chrome)
        self.assertNotIn("rgba(0,188,235", chrome)
        script = perf[perf.index("<script>"):perf.rindex("</script>")]
        draw = script[script.index("function cardPalette("):script.index("function setStatus(")]
        draw = draw.replace(draw[draw.index("const CLASSIC_CARD="):draw.index("\n", draw.index("const CLASSIC_CARD="))], "")
        for literal in ("#00bceb", "#4054d6", "#8ee9f2", "#8f6ce6", "rgba(0,188,235", "rgba(64,84,214", "rgba(127,219,242", "#0d2c3d"):
            with self.subTest(card_literal=literal):
                self.assertNotIn(literal, draw)
        self.assertIn("window.addEventListener('storage',event=>{", script)

    def test_performance_rail_lists_the_same_destinations_as_the_dashboard(self):
        perf = Path(__file__).resolve().parents[1].joinpath("performance.html").read_text(encoding="utf-8")
        def groups(source, primary, secondary, label):
            top = source[source.index(f"<div class={primary}>"):source.index(f"<div class={secondary}>")]
            bottom = source[source.index(f"<div class={secondary}>"):source.index("</nav>", source.index(f"<div class={secondary}>"))]
            return re.findall(label, top), re.findall(label, bottom)
        dashboard = groups(self.page, "navPrimary", "navSecondary", r'class="?tab[^>]*data-label=(\w+)')
        performance = groups(perf, "railPrimary", "railSecondary", r"<span class=railLabel>(\w+)</span>")
        self.assertEqual(performance, dashboard)
        self.assertEqual(dashboard[1][0], "Read")
        tokenomics = re.search(r"const TOKENOMICS_SEARCH_URL='([^']+)'", self.page).group(1)
        self.assertIn(f'id=tab-read href="{tokenomics}" target=_blank rel="noopener noreferrer"', perf)

    def test_appearance_settings_section_and_route(self):
        self.assertIn('<nav class="card settingsMap" aria-label="Settings sections">\n    <button type=button data-settings-target=appearance-settings><b>Appearance</b></button>', self.page)
        content = self.page.index("<div class=settingsPageContent>")
        self.assertEqual(self.page.index("<section", content), self.page.index('<section class="card pad appearanceSettings"', content))
        self.assertIn('<section class="card pad appearanceSettings" id=appearance-settings>', self.page)
        self.assertIn("<div class=themePicker id=theme-picker role=group aria-labelledby=appearance-title></div>", self.page)
        self.assertIn("h==='settings-appearance'", self.page)
        self.assertIn("option.setAttribute('aria-pressed',String(option.dataset.themeOption===themePreference));", self.page)
        self.assertIn("window.addEventListener('storage',event=>{if(event.key===THEME_STORAGE_KEY)", self.page)

    def test_theme_switch_repaints_token_driven_surfaces(self):
        start = self.page.index("function repaintThemedSurfaces(){")
        body = self.page[start:self.page.index("\n}", start)]
        for marker in ("readChartTokens();", "drawIO(", "renderModelStats(", "renderEfficiency(",
                       "drawSpendScatter(", "drawDeliveryChart(", "syncActivePageSignalEffects();", "meta[name=theme-color]"):
            with self.subTest(marker=marker):
                self.assertIn(marker, body)
        effects = Path(__file__).resolve().parents[1].joinpath("assets", "session-effects.js").read_text(encoding="utf-8")
        self.assertIn("getComputedStyle(element)", effects)
        for token in ("--fx-front-rgb", "--fx-back-rgb", "--fx-edge-a-rgb", "--fx-edge-b-rgb"):
            self.assertIn(f'"{token}"', effects)

    def motion_source(self, start, end):
        return self.page[self.page.index(start):self.page.index(end)]

    def test_readouts_count_only_meaningful_numeric_changes(self):
        source = self.motion_source("const READOUT_PATTERN=", "// Readouts count toward")
        script = (
            "let clock=0;const performance={now:()=>clock};const frames=[];const requestAnimationFrame=cb=>frames.push(cb);"
            "const cssVar=()=>'480ms';" + source +
            "const el={textContent:''};const ran=(a,b)=>{frames.length=0;tweenReadout(el,a,b);return frames.length>0;};"
            "const out={placeholder:ran('—','$4.20'),word:ran('Sonnet 3.7','Sonnet 4.5'),suffix:ran('12K','3M'),"
            "tiny:ran('$100.00','$100.40'),real:ran('$100.00','$140.00')};clock=2000;out.quiet=ran('$140.00','$200.00');"
            "clock=20000;out.later=ran('$140.00','$200.00');"
            "const other={textContent:''};frames.length=0;tweenReadout(other,'$1,000','$2,000');clock=50000;frames.shift()(clock);out.final=other.textContent;"
            "console.log(JSON.stringify(out));"
        )
        result = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(result, {"placeholder": False, "word": False, "suffix": False, "tiny": False, "real": True,
                                  "quiet": False, "later": True, "final": "$2,000"})

    def test_no_glow_follows_the_cursor(self):
        # A highlight moving behind text under the pointer made cards hard to read, so cards stay still.
        self.assertNotIn("isSpotlit", self.page)
        self.assertNotIn("--spot", self.page)
        self.assertNotIn("spotlight", self.page.lower().replace("spotlights", ""))

    def test_chart_reveal_runs_once_per_route_change(self):
        source = self.motion_source("const chartsRevealedThisVisit=", "const READOUT_SELECTOR=")
        script = (
            "let animations=0;const svg={id:'iochart',childElementCount:3,getClientRects:()=>[1],animate(){animations++;}};"
            "const $=()=>svg;const motionAllowed=()=>true;const cssVar=()=>'ease';const CHART_REVEAL_IDS=['iochart'];"
            "class MutationObserver{observe(){}}" + source +
            "const session={dataset:{entered:'1'},children:[]},spend={dataset:{entered:'1'},children:[]};"
            "playRouteMotion(session);playRouteMotion(session);revealChart(svg);playRouteMotion(spend);playRouteMotion(session);"
            "console.log(animations);"
        )
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(result, "3")


class LocalServerBindTests(unittest.TestCase):
    def test_binding_the_local_server_skips_reverse_dns(self):
        # A stalled resolver must not delay listen(): http.server would call
        # socket.getfqdn here, which can block for ~35s on some networks.
        with mock.patch("socket.getfqdn", side_effect=AssertionError("reverse DNS on bind")):
            server = meter.TokenMeterHTTPServer(("127.0.0.1", 0), meter.H)
        try:
            self.assertEqual(server.server_name, "127.0.0.1")
            self.assertEqual(server.server_port, server.server_address[1])
        finally:
            server.server_close()


class DeclutterCopyContractTests(unittest.TestCase):
    """Copy moved out of view keeps its behavior: notes appear only when they say something."""

    @classmethod
    def setUpClass(cls):
        cls.page = Path(DASHBOARD_PAGE).read_text()

    def run_node(self, script):
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    def statement(self, start, end=";\n"):
        return start + self.page.split(start, 1)[1].split(end, 1)[0] + ";"

    def test_scatter_missing_note_is_hidden_until_points_are_missing(self):
        self.assertIn(".spendScatterLegend span[hidden]{display:none}", self.page)
        self.assertIn("<span id=s-scatter-missing hidden>Requires active-time and selected-metric evidence.</span>", self.page)
        self.assertIn("$('s-scatter-missing').hidden=points.length>=(rows||[]).length;", self.page)

    def test_session_budget_shows_saved_briefly_after_a_successful_save(self):
        self.assertIn("<span id=session-budget-status role=status aria-live=polite></span>", self.page)
        self.assertNotIn("Saved for this session on this device", self.page)
        body = self.page.split("let sessionBudgetStatusTimer=0;\n", 1)[1].split("\n}\n", 1)[0] + "\n}"
        script = """
const nodes={'session-budget-status':{textContent:''}};const $=id=>nodes[id];
let CURRENT={id:'s'};const timers=[];function setTimeout(fn,ms){timers.push([fn,ms]);return timers.length;}function clearTimeout(){}
function syncSessionBudgetControls(){}function showAppToast(){}
async function queueSessionBudget(){return true;}
let sessionBudgetStatusTimer=0;
""" + body + """
(async()=>{saveBudgetForSession('12');await new Promise(r=>setImmediate(r));
const saved=nodes['session-budget-status'].textContent,delay=timers[0][1];timers[0][0]();
process.stdout.write(JSON.stringify({saved,delay,after:nodes['session-budget-status'].textContent}));})();
"""
        self.assertEqual(self.run_node(script), {"saved": "Saved", "delay": 2500, "after": ""})

    def test_agent_activity_coverage_is_empty_when_complete_and_lists_gaps(self):
        statement = self.statement("$('agent-activity-coverage').textContent=")
        script = """
const nodes={'agent-activity-coverage':{textContent:'x'}};const $=id=>nodes[id];const f=n=>String(n);
const run=(coverage,hidden)=>{""" + statement + """return nodes['agent-activity-coverage'].textContent;};
process.stdout.write(JSON.stringify([
 run({relationships:'complete',cost:'estimated',tokens:'complete'},0),
 run({relationships:'complete',cost:'complete',tokens:'complete'},0),
 run({relationships:'partial',cost:'partial',tokens:'unavailable'},3),
]));
"""
        self.assertEqual(self.run_node(script), [
            "", "", "Relationships partial · cost partial · tokens unavailable · 3 hidden (limit 100)",
        ])

    def test_model_spend_note_marks_unpriced_runtimes_as_excluded_not_zero(self):
        body = "function renderModelSpend(" + self.page.split("function renderModelSpend(", 1)[1].split("\nfunction ", 1)[0]
        script = """
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const nodes={};const $=id=>nodes[id]||(nodes[id]={textContent:'',innerHTML:''});
const setLogHtml=(el,html)=>{el.innerHTML=html;};const f=n=>String(n);const countWord=(n,one,many=one+'s')=>Number(n)===1?one:many;
const money=n=>'$'+Number(n).toFixed(2);const pct=n=>Math.round(n*100)+'%';const modelRangeLabel=()=>'30 days';let modelRange='30';
const metricPartial=()=>false;const modelRankedColors=()=>new Map();const MODEL_OTHER_COLOR='x';
const modelBoardValue=(group,key)=>group.window.cost==null?null:group.window.cost;
""" + body + """
const groups=[{key:'a',model:'a',runtime:'Codex',ids:['a'],window:{cost:2}},{key:'b',model:'b',runtime:'Codex',ids:['b'],window:{cost:null}},{key:'c',model:'c',runtime:'Codex',ids:['c'],window:{cost:null}}];
renderModelSpend(groups,groups,[groups[0]],true);
process.stdout.write(JSON.stringify(nodes['m-spend-note'].innerHTML));
"""
        html = self.run_node(script)
        self.assertIn("<span class=fieldtip tabindex=0", html)
        self.assertIn('data-tip="2 model runtimes without cost data are excluded, not counted as $0."', html)
        self.assertIn('aria-description="2 model runtimes without cost data are excluded, not counted as $0."', html)
        self.assertIn(">2 unpriced, excluded</span>", html)

    NODE_EXTRACT = r"""
const fs=require('fs');const page=fs.readFileSync(process.argv[1],'utf8');
function extract(name){let start=page.indexOf(`async function ${name}(`);if(start<0)start=page.indexOf(`function ${name}(`);if(start<0)throw new Error('missing '+name);let i=page.indexOf('{',start),depth=0;for(;i<page.length;i++){if(page[i]==='{')depth++;else if(page[i]==='}'&&--depth===0)return page.slice(start,i+1);}}
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const f=n=>Number(n||0).toLocaleString('en-US');const money=n=>'$'+Number(n||0).toFixed(2);
class Node{constructor(){this.textContent='';this.innerHTML='';this.hidden=false;this.dataset={};this.options=[];this.classList={add(){},remove(){},toggle(){}};this.style={setProperty(){}};this.body=null;}
 querySelector(sel){if(sel==='tbody'){return this.body||(this.body=new Node());}return null;}querySelectorAll(){return [];}setAttribute(){}removeAttribute(){}}
const nodes={};const $=id=>nodes[id]||(nodes[id]=new Node());
"""

    def run_page_node(self, script):
        page = str(Path(DASHBOARD_PAGE))
        result = subprocess.run(["node", "-e", self.NODE_EXTRACT + script, page],
                                capture_output=True, text=True)
        if result.returncode:
            errors = [line for line in result.stderr.splitlines() if "Error" in line]
            self.fail(errors[0] if errors else result.stderr.strip() or "node failed")
        return json.loads(result.stdout)

    def test_today_cards_say_unavailable_only_when_daily_rows_are_missing(self):
        out = self.run_page_node(r"""
const localDayKey=(d=new Date())=>d.toISOString().slice(0,10);const metricAvailable=()=>true,metricPartial=()=>false,hasLocalEstimate=()=>false;
const setCostValue=(id,text)=>{$(id).textContent=text;};const countWord=(n,one,many=one+'s')=>Number(n)===1?one:many;
const dailyDeltaLabel=()=>'+0%';const renderTrendDelta=()=>'';
eval(extract('renderCurrentDaySummary'));
const ids=['current-day-spend-note','current-day-sessions-note','current-day-vs-yesterday-note'];const notes=()=>ids.map(id=>$(id).textContent);
renderCurrentDaySummary(undefined);const loading=notes();
renderCurrentDaySummary({current_sessions:[]});const missing=notes();
renderCurrentDaySummary({daily:[{day:localDayKey(),cost:1.5,sessions:2}]});const arrived=notes();
process.stdout.write(JSON.stringify({loading,missing,arrived}));
""")
        self.assertEqual(out["loading"], ["", "", ""])
        self.assertEqual(out["missing"], ["Unavailable", "Unavailable", "Unavailable"])
        self.assertNotIn("Unavailable", out["arrived"])
        self.assertTrue(all(out["arrived"]))

    def test_git_load_failure_marks_both_tables_unavailable_and_success_replaces_them(self):
        out = self.run_page_node(r"""
let deliveryRequest=0,deliveryRange='30',deliveryProject='',deliveryLastLoadedAt=0,deliveryRetryTimer=0,deliveryLoadRetries=0;
let deliveryEvidenceFilter='all',deliverySort={key:'covered_cost',direction:'desc'},deliveryModelSort={key:'covered_cost',direction:'desc'};
const modelEffortLabel=row=>row?.effort||'';const renderGitBranches=()=>{};const clearTimeout=()=>{};const setTimeout=()=>0;const setDeliveryEvidenceRatio=()=>{};const DELIVERY_EVIDENCE_FILTERS=['all','comparable','spend_only','git_only','unavailable'];const deliveryEvidenceFilterLabel=()=>'';
eval(['loadDelivery','renderDeliveryTable','renderDeliveryModelTable','sortedDeliveryModelRows','deliveryModelSortValue','deliveryFilteredProjectRows','sortedDeliveryRows'].map(extract).join('\n'));
function renderDelivery(payload){renderDeliveryModelTable(payload.model_rows,payload.model_coverage);renderDeliveryTable(payload.project_rows);}
let respond;globalThis.fetch=async()=>respond();
(async()=>{
 $('d-model-note').textContent='120 / 300 lines attributed';
 respond=()=>{throw new Error('offline');};await loadDelivery();
 const failed={note:$('d-model-note').textContent,model:$('d-model-table').querySelector('tbody').innerHTML,project:$('d-project-table').querySelector('tbody').innerHTML};
 respond=()=>({ok:true,json:async()=>({coverage:{last_checked:1},model_coverage:{available:true,comparable_lines:10,attributed_lines:10},model_rows:[{model:'gpt-x',runtime:'Codex',covered_cost:1,attributed_lines:10}],project_rows:[]})});
 await loadDelivery();
 const ok={note:$('d-model-note').textContent,model:$('d-model-table').querySelector('tbody').innerHTML,project:$('d-project-table').querySelector('tbody').innerHTML};
 process.stdout.write(JSON.stringify({failed,ok}));
})();
""")
        failed, ok = out["failed"], out["ok"]
        self.assertEqual(failed["note"], "")
        self.assertIn("<td colspan=5><div class=\"deliveryEmpty emptyState\">Git evidence unavailable.</div>", failed["model"])
        self.assertIn("<td colspan=4><div class=\"deliveryEmpty emptyState\">Git evidence unavailable.</div>", failed["project"])
        self.assertEqual(ok["note"], "10 / 10 lines attributed")
        self.assertIn("gpt-x", ok["model"])
        self.assertNotIn("Git evidence unavailable.", ok["model"] + ok["project"])
        self.assertIn("No projects match this filter.", ok["project"])

    def test_scatter_missing_note_follows_plotted_points(self):
        out = self.run_page_node(r"""
let spendScatterMetric='cost';const CHART={lbl:'l',grid:'g'};const tint=()=>'t';const hideSpendScatterTip=()=>{};
const sessionRowKey=row=>row.id;const spendRuntimeKey=p=>p;const spendInsightMetricFormat=v=>String(v);const sessionRoute=id=>'#'+id;
const providerColor=()=>'c';const waitFmt=v=>String(v);const svgTextPx=(svg,w,h,px)=>px;const SPEND_RUNTIME_COLORS=new Proxy({},{get:()=>'c'});
$('s-scatter-svg').clientWidth=1040;$('s-scatter-svg').clientHeight=340;$('s-scatter-metric');
eval(['drawSpendScatter','spendScatterRows','spendSessionMetricValue'].map(extract).join('\n'));
const analytics={distributions:{active:{median:0},cost:{median:0}},concentration:{top_count:0}};
const row=(id,timed)=>({id,provider:'codex',title:id,cost:1,availability:{cost:true},duration_available:timed,duration_s:60});
drawSpendScatter([row('a',true),row('b',false)],analytics);const partial=$('s-scatter-missing').hidden;
drawSpendScatter([row('a',true),row('b',true)],analytics);const complete=$('s-scatter-missing').hidden;
process.stdout.write(JSON.stringify({partial,complete,coverage:$('s-scatter-coverage').textContent}));
""")
        self.assertEqual(out, {"partial": False, "complete": True, "coverage": "2 of 2 sessions plotted"})

    def test_work_breakdown_footnote_is_a_short_count_with_the_reason_in_a_tooltip(self):
        start = " const excludedDetail="
        block = start + self.page.split(start, 1)[1].split("\n}\n", 1)[0]
        script = """
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const nodes={'w-breakdown-foot':{innerHTML:'x'}};const $=id=>nodes[id];const count=n=>String(n);
const run=(unanswered,unjudged)=>{const overall={requests:10,tasks:8};""" + block + """;return nodes['w-breakdown-foot'].innerHTML;};
process.stdout.write(JSON.stringify([run(3,2),run(0,0)]));
"""
        both, none = self.run_node(script)
        self.assertEqual(none, "")
        self.assertIn("<span class=fieldtip tabindex=0", both)
        self.assertIn(">· 3 requests, 2 tasks excluded</span>", both)
        self.assertIn("3 of 10 requests come from apps that don&#39;t split model calls by request", both)
        self.assertIn("2 of 8 tasks had no labeled follow-up", both)


if __name__ == "__main__":
    unittest.main()
