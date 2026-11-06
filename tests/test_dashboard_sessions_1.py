import unittest
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from tests.dashboard_page import DASHBOARD_PAGE
from tests.dashboard_layout_support import DashboardLayoutCase


class DashboardSessionsLayoutTests1(DashboardLayoutCase):
    def test_current_detail_excludes_all_secondary_panels(self):
        self.assertNotIn("data-panel=efficiency", self.page)
        self.assertNotIn("id=panel-efficiency", self.page)
        self.assertIn("const PANEL_KEYS=['summary'];", self.page)
        self.assertNotIn("efficiency:'summary'", self.page)

    def test_sessions_surfaces_include_agent_activity_and_usage_statistics(self):
        for expected in (
            "id=agent-activity",
            "id=agent-activity-tree",
            "id=agent-attention-detail",
            "id=view-subagents",
            "id=subagent-explorer",
            "id=subagent-filter-status",
            "id=subagent-filter-signal",
            "id=subagent-view-tabs",
            "id=subagent-compact-summary",
            "id=subagent-workspace",
            "id=subagent-results",
            "id=subagent-inspector",
            "renderAgentActivity(s);",
            "renderSubagentExplorer(",
            "Median / P95",
            "groupSubagentSessions(",
            "buildSubagentRoleRows(",
        ):
            self.assertIn(expected, self.page)
        self.assertIn("Cost color compares children in this session", self.page)
        self.assertIn("Cost and retry evidence", self.page)
        self.assertIn("Work time", self.page)
        self.assertIn("completed prompt-to-response time", self.page)
        self.assertNotIn("<span>Elapsed</span>", self.page)
        self.assertNotIn("evidenceMetric('Elapsed'", self.page)

    def test_session_detail_back_control_markup_names_origin_list(self):
        view = self.page.index("id=view-session")
        back = self.page.index("id=session-back-row")
        title = self.page.index("id=session-page-title")
        self.assertLess(view, back)
        self.assertLess(back, title)
        self.assertIn(
            '<div class=sessionBackRow id=session-back-row data-current-detail>'
            '<button class="tbtn sessionBack" id=session-back type=button aria-label="Back to Current sessions">'
            '<span aria-hidden=true>←</span><span id=session-back-label>Current sessions</span></button></div>',
            self.page,
        )
        self.assertIn("$('session-back').onclick=openSessionBack;", self.page)
        self.assertIn("if(!overview)renderSessionBack();", self.page)
        self.assertIn(".sessionBackRow{display:flex;margin-bottom:10px}", self.page)
        self.assertIn("sessionListBack:!replace&&sessionListBackAvailable(sessionReturnScope)", self.page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_session_detail_back_returns_to_origin_list(self):
        script = f"""
const fs=require('fs');
const page=fs.readFileSync({json.dumps(str(Path(DASHBOARD_PAGE)))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('){{',start)+1,depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
const SESSION_SCOPES=['current','all','compare','subagents'];
const calls=[];
let location={{pathname:'/',hash:'#sessions-all'}};
const history={{state:null,back(){{calls.push(['back']);}},pushState(state,title,url){{this.state=state;const [path,hash]=String(url).split('#');location={{pathname:path||'/',hash:hash?'#'+hash:''}};calls.push(['push',url]);}},replaceState(state){{this.state=state;}}}};
const elements={{}};
const $=id=>elements[id]||(elements[id]={{textContent:'',attrs:{{}},setAttribute(name,value){{this.attrs[name]=value;}}}});
let sessionScope='all',sessionReturnScope='current',sessionRequest=0,pinned=null;
const applyHashRoute=()=>{{}},refreshSelectedSession=()=>Promise.resolve();
const openSessionScope=scope=>calls.push(['scope',scope]);
eval(['routedSessionId','sessionRoute','normalizeSessionScope','sessionScopeRoute','sessionScopeLabel','sessionListBackAvailable','renderSessionBack','openSessionBack','selectSession'].map(extract).join('\\n'));
const result={{}};
selectSession('abc',{{returnScope:'all'}});
renderSessionBack();
result.fromList={{state:history.state,label:$('session-back-label').textContent,aria:$('session-back').attrs['aria-label']}};
openSessionBack();
result.fromListCalls=calls.splice(0);
location={{pathname:'/',hash:'#spend'}};
selectSession('def');
openSessionBack();
result.fromSpend={{state:history.state,calls:calls.splice(0)}};
selectSession('ghi',{{returnScope:'compare'}});
result.sessionToSession=history.state;
calls.splice(0);
location={{pathname:'/sessions/deep#summary',hash:'#summary'}};
history.state=null;sessionReturnScope='current';
renderSessionBack();
openSessionBack();
result.deepLink={{label:$('session-back-label').textContent,aria:$('session-back').attrs['aria-label'],calls:calls.splice(0)}};
result.labels=['current','all','compare','subagents','bogus'].map(sessionScopeLabel);
eval(extract('applyLocationRoute'));
elements['view-session']={{classList:{{contains:()=>true}}}};
location={{pathname:'/sessions/xyz',hash:'#summary'}};
history.state={{sessionScope:'compare',sessionListBack:true}};
sessionScope='current';sessionReturnScope='current';
applyLocationRoute();
renderSessionBack();
result.reload={{scope:sessionReturnScope,label:$('session-back-label').textContent}};
openSessionBack();
result.reloadCalls=calls.splice(0);
process.stdout.write(JSON.stringify(result));
"""
        completed = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)

        self.assertEqual(payload["fromList"]["state"], {"sessionScope": "all", "sessionListBack": True})
        self.assertEqual(payload["reload"], {"scope": "compare", "label": "Compare"})
        self.assertEqual(payload["reloadCalls"], [["back"]])
        self.assertEqual(payload["fromList"]["label"], "All sessions")
        self.assertEqual(payload["fromList"]["aria"], "Back to All sessions")
        self.assertEqual(payload["fromListCalls"], [["push", "/sessions/abc#summary"], ["back"]])
        self.assertEqual(payload["fromSpend"]["state"], {"sessionScope": "all", "sessionListBack": False})
        self.assertEqual(payload["fromSpend"]["calls"], [["push", "/sessions/def#summary"], ["scope", "all"]])
        self.assertEqual(payload["sessionToSession"], {"sessionScope": "compare", "sessionListBack": False})
        self.assertEqual(payload["deepLink"]["label"], "Current sessions")
        self.assertEqual(payload["deepLink"]["aria"], "Back to Current sessions")
        self.assertEqual(payload["deepLink"]["calls"], [["scope", "current"]])
        self.assertEqual(
            payload["labels"],
            ["Current sessions", "All sessions", "Compare", "Subagents", "Current sessions"],
        )

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_session_compare_keys_forked_traces_and_routes_compare_scope(self):
        self.assertIn("id=session-scope-compare", self.page)
        self.assertIn("id=session-compare role=tabpanel", self.page)
        self.assertIn("fetch(`/session/compare?ids=${encodeURIComponent(key)}`", self.page)
        self.assertIn("{id:'compare-sessions',label:'Compare sessions'", self.page)
        script = f"""
const fs=require('fs');
const page=fs.readFileSync({json.dumps(str(Path(DASHBOARD_PAGE)))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
const SESSION_SCOPES=['current','all','compare','subagents'];
eval(extract('normalizeSessionScope'));eval(extract('sessionScopeRoute'));eval(extract('compareKeyFor'));
eval(extract('mergeAllSessionInventory'));eval(extract('compareDelta'));
const parent={{id:'root',key:'tparent',title:'Same'}};
const fork={{id:'root',key:'tfork',title:'Same'}};
const merged=mergeAllSessionInventory([parent,fork],[{{...fork,cost:2}}]);
process.stdout.write(JSON.stringify({{
 route:sessionScopeRoute('compare'),
 keys:[compareKeyFor(parent),compareKeyFor(fork),compareKeyFor({{id:'only'}}),compareKeyFor({{id:'o',key:'topencode'}}),compareKeyFor({{key:'tkiro'}}),compareKeyFor({{}}),compareKeyFor({{id:7}})],
 merged:merged.length,
 liveWins:merged.find(row=>row.key===fork.key).cost,
 cheaper:compareDelta({{lower:true}},0.5,1),
 costlier:compareDelta({{lower:true}},3,1),
 neutral:compareDelta({{}},2.5,2),
 points:compareDelta({{lower:false,points:true}},0.9,0.6),
 missing:compareDelta({{lower:true}},null,1),
}}));
"""
        payload = json.loads(subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        ).stdout)
        self.assertEqual(payload["route"], "sessions-compare")
        self.assertEqual(payload["keys"], ["tparent", "tfork", "only", "topencode", "tkiro", "", "7"])
        self.assertEqual(payload["merged"], 2)
        self.assertEqual(payload["liveWins"], 2)
        self.assertIn("better", payload["cheaper"])
        self.assertIn("−50%", payload["cheaper"])
        self.assertIn("worse", payload["costlier"])
        self.assertIn("3.0×", payload["costlier"])
        self.assertNotIn("better", payload["neutral"])
        self.assertNotIn("worse", payload["neutral"])
        self.assertIn("+30 pts", payload["points"])
        self.assertIn("better", payload["points"])
        self.assertEqual(payload["missing"], "")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_compare_clears_stale_missing_notice_when_selection_empties(self):
        script = f"""
const fs=require('fs');
const page=fs.readFileSync({json.dumps(str(Path(DASHBOARD_PAGE)))},'utf8');
function extract(name){{let start=page.indexOf(`async function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('){{',start)+1,depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
let compareIds=[],compareData={{sessions:[]}},compareError='',compareNotice='1 selected session could not be loaded right now.',compareMissing=['tgone'],compareLoadedKey='tgone',compareLoading=false,compareRequestSeq=0,rendered=0;
function renderComparison(){{rendered++;}}
eval(extract('loadComparison'));
loadComparison().then(()=>process.stdout.write(JSON.stringify({{compareNotice,compareMissing,compareData,rendered}})));
"""
        payload = json.loads(subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        ).stdout)
        self.assertEqual(payload, {"compareNotice": "", "compareMissing": [], "compareData": None, "rendered": 1})

    def test_sessions_all_uses_exact_coverage_copy_without_ambiguous_suffixes(self):
        start = self.page.index("function renderAllSessionStats(")
        end = self.page.index("function currentSessionAge(", start)
        block = self.page[start:end]
        self.assertIn("sessionCoverageCopy", block)
        self.assertNotIn("?' partial'", block)
        self.assertNotIn(" incl. est", block)

        start = self.page.index("function renderSubagentUsage(")
        end = self.page.index("function renderAllSessions(", start)
        subagent_block = self.page[start:end]
        self.assertIn("group_cost_covered_sessions", subagent_block)
        self.assertIn("parent sessions have complete group cost", subagent_block)
        self.assertNotIn("group-cost coverage", subagent_block)

    def test_current_output_card_shows_trace_backed_output_speed(self):
        for marker in ("id=preview-speed", "s.throughput||{}", "speedFmt(throughput.output_tps)",
                       "tool-free", "end-to-end", "reasoning and thinking output",
                       "external tool-result tokens"):
            self.assertIn(marker, self.page)
        self.assertIn(".previewSpeed .v{color:var(--accent)", self.page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_unavailable_cost_value_explains_why_it_is_not_zero(self):
        match = re.search(
            r"const COST_UNAVAILABLE_TIP=.*?^function setCostValue\(.*?^\}\n",
            self.page,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(match, "dashboard needs an unavailable-cost helper")
        script = """
const element={classList:{values:new Set(),toggle(name,on){on?this.values.add(name):this.values.delete(name);}},attributes:{},setAttribute(name,value){this.attributes[name]=String(value);},removeAttribute(name){delete this.attributes[name];}};
const $=id=>element;
""" + match.group(0) + """
setCostValue('cost','$1.25',true);
const available={text:element.textContent,classes:[...element.classList.values],attributes:{...element.attributes}};
setCostValue('cost','$1.25',false);
console.log(JSON.stringify({available,unavailable:{text:element.textContent,classes:[...element.classList.values],attributes:{...element.attributes}}}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        rendered = json.loads(result.stdout)
        self.assertEqual(rendered["available"], {
            "text": "$1.25", "classes": [], "attributes": {},
        })
        self.assertEqual(rendered["unavailable"]["text"], "--")
        self.assertEqual(rendered["unavailable"]["classes"], ["fieldtip"])
        self.assertIn("not $0", rendered["unavailable"]["attributes"]["data-tip"])
        self.assertIn("locally stored usage", rendered["unavailable"]["attributes"]["aria-description"])

    def test_cross_session_cost_placeholders_share_the_unavailable_cost_explanation(self):
        for renderer, helper in (
            ("function logRowContent(s){", "costValueHtml("),
            ("function renderAllSessionStats(sessions){", "costValueHtml("),
            ("function renderCurrentDaySummary(xs){", "setCostValue("),
            ("function renderSpend(xs){", "setCostValue("),
            ("function renderCurrentSessions(state=LATEST){", "costValueHtml("),
        ):
            section = self.page.split(renderer, 1)[1].split("\nfunction ", 1)[0]
            self.assertIn(helper, section, renderer)

    def test_current_session_cost_tooltip_does_not_add_a_nested_tab_stop(self):
        session_cards = self.page.split("function renderCurrentSessions(state=LATEST){", 1)[1].split(
            "const currentSessionGrid=$('current-session-grid');", 1
        )[0]
        self.assertIn("costValueHtml(money(row.cost)+(estimate?' est':''),costAvailable,false),costLabel=costPartial?'Cost, at least':'Cost'", session_cards)
        self.assertIn("const costTipAttrs=costAvailable?'':costUnavailableAttrs();", session_cards)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_active_session_surfaces_prefer_live_throughput(self):
        match = re.search(
            r"function sessionDisplayThroughput\(session\)\{.*?\n\}",
            self.page,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "dashboard needs one shared speed selector")
        script = match.group(0) + """
const completed = {throughput:{available:true,output_tps:85.4},live_throughput:{available:false}};
const active = {throughput:{available:true,output_tps:85.4},live_throughput:{available:true,output_tps:41.2}};
console.log(JSON.stringify({
  completed: sessionDisplayThroughput(completed).output_tps,
  active: sessionDisplayThroughput(active).output_tps,
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "completed": 85.4,
            "active": 41.2,
        })
        self.assertGreaterEqual(self.page.count("sessionDisplayThroughput(s)"), 2)
        self.assertIn("sessionDisplayThroughput(row)", self.page)

    def test_session_detail_is_one_run_surface_without_subtabs(self):
        for marker in (
            "id=current-tabs", "data-current-panel=", "id=preview-surface-activity",
            "id=preview-surface-tools", "id=preview-surface-insights",
            "id=preview-surface-alerts", "id=panel-activity", "id=panel-tools",
            "id=panel-insights", "id=panel-alerts", "function renderTrace(",
            "function renderTools(",
        ):
            self.assertNotIn(marker, self.page)
        self.assertIn("const PANEL_KEYS=['summary'];", self.page)
        self.assertIn("const CURRENT_PANEL_KEYS=['sessions','run'];", self.page)
        for removed_route in ("activity", "tools", "alerts"):
            self.assertIn(f"{removed_route}:'summary'", self.page)
        self.assertNotIn("insights:'summary'", self.page)
        self.assertNotIn("efficiency:'summary'", self.page)

    def test_codex_session_detail_links_to_the_desktop_thread(self):
        for marker in (
            'id=session-desktop-link',
            'aria-label="Open this session in the Codex desktop app"',
            'function desktopSessionTarget(session)',
            "provider==='codex'",
            'codex://threads/${encodeURIComponent(id)}',
            'function renderDesktopSessionLink(session)',
            'link.hidden=!target',
            "link.removeAttribute('href')",
            'renderDesktopSessionLink(s);',
        ):
            self.assertIn(marker, self.page)

    def test_claude_sessions_link_to_the_desktop_code_session(self):
        for marker in (
            "provider==='claude'",
            "/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i",
            'claude://resume?session=${encodeURIComponent(resume)}',
            "label:'Open in Claude'",
            "label:'Open in Codex'",
            'link.textContent=target.label',
        ):
            self.assertIn(marker, self.page)

    def test_current_session_cards_offer_a_sibling_desktop_link(self):
        render = self.page[self.page.index("function renderCurrentSessions("):]
        render = render[:render.index("\nfunction ")]
        self.assertIn("desktopSessionTarget(row)", render)
        self.assertIn('<div class=currentSessionSlot', render)
        self.assertIn('class=currentSessionDesktop', render)
        self.assertIn('draggable=false', render)
        self.assertIn("</button>${desktopLink}</div>", render)
        self.assertIn(".currentSessionSlot{", self.page)
        self.assertIn(".currentSessionDesktop{", self.page)

    def test_old_current_summary_is_only_a_hidden_renderer_depot(self):
        for marker in (
            "id=session-depot hidden", "id=usage-details", "tm_usage_details_open",
            "id=cost", "id=input-tok", "id=output-tok", "id=output-tps",
            "id=ov-duration", "id=ov-context",
        ):
            self.assertIn(marker, self.page)
        self.assertNotIn("class=view id=session-depot", self.page)
        self.assertNotIn("class=\"view on\" id=session-depot", self.page)

    def test_execution_chart_marks_a_single_observed_value(self):
        chart = self.page[
            self.page.index("function drawIO(series)"):
            self.page.index("function renderOverview(s)")
        ]
        self.assertIn("const validPoints=[]", chart)
        self.assertIn("validPoints.length===1", chart)
        self.assertIn("const singlePoint=", chart)

    def test_builder_recap_is_a_separate_document(self):
        primary = self.page.split("<div class=navPrimary>", 1)[1].split("</div>", 1)[0]
        performance = re.search(
            r'<a[^>]+id=tab-performance[^>]*>.*?</a>', primary, re.S
        )
        self.assertIsNotNone(performance)
        anchor = performance.group(0)
        for marker in (
            "class=tab", "href=/performance", "data-label=Performance",
            "aria-label=Performance", "<span class=tabLabel>Performance</span>",
        ):
            self.assertIn(marker, anchor)
        self.assertNotIn("aria-keyshortcuts", anchor)
        self.assertNotIn("tabShortcut", anchor)
        self.assertLess(primary.index("id=tab-efficiency"), primary.index("id=tab-git"))
        self.assertLess(primary.index("id=tab-git"), primary.index("id=tab-performance"))
        efficiency = self.page.split("<div class=view id=view-efficiency>", 1)[1].split(
            "<div class=view id=view-git>", 1
        )[0]
        self.assertNotIn("id=e-builder-recap", efficiency)
        for rejected in (
            "id=view-performance-card", "performance-card", ".recapPageHead",
            "const builderRecapState=", "function drawBuilderRecap(",
            "recap-canvas", "recap-download",
        ):
            self.assertNotIn(rejected, self.page)

    def test_language_signals_are_removed_from_the_dashboard(self):
        for removed in (
            "id=model-frustration", "id=frustration-settings", "id=positive-terms",
            "id=f-chart", "id=f-collapse", "User language signals", "Language signals",
            "/settings/language-signals", "renderFrustration", "language_signals",
            "frustrationSection", "signalPanel", "tm_language_signals_collapsed",
        ):
            self.assertNotIn(removed, self.page)
        self.assertIn("if(h==='models'||h==='frustration'){", self.page)
        self.assertIn("if(h==='frustration')setHashRoute('models',{replace:true,apply:false});", self.page)
        self.assertIn(".signalEmpty{", self.page)
        self.assertIn(
            "body.spectrumApp :is(.sessionStart,.toast,.capReview,.agentDiscovery,.previewStartStrip,.modelScopeStatus){border-left-width:1px}",
            self.page,
        )

    def test_selected_session_token_split_fills_the_chart_column_gap(self):
        run_grid = self.page.split('<div class=previewRunGrid>', 1)[1].split(
            '<aside class=previewDecision>', 1
        )[0]
        self.assertIn('class=previewChartColumn', run_grid)
        self.assertIn('id=preview-run-chart-slot', run_grid)
        self.assertIn('id=preview-token-split-slot', run_grid)
        self.assertLess(
            run_grid.index('id=preview-run-chart-slot'),
            run_grid.index('id=preview-token-split-slot'),
        )
        self.assertIn(
            ".previewChartColumn{display:grid;grid-template-rows:minmax(0,1fr) auto;gap:12px;min-width:0}",
            self.page,
        )

    def test_review_fixes_keep_focus_targets_and_timestamps(self):
        # The inventory heading receives programmatic focus, so it must be focusable.
        self.assertIn("<h2 id=c-inventory-title tabindex=-1>All capabilities</h2>", self.page)
        self.assertIn("$('c-inventory-title').focus(", self.page)
        # Detail view keeps the date/time line break instead of collapsing it.
        self.assertIn(
            "body.sessionRoute #view-session.sessionDetail .previewTimes b{overflow:visible;white-space:pre-line;line-height:1.35}",
            self.page,
        )
        self.assertIn("$('preview-started-at').textContent=(dt12(timing.start_local)||'--').replace(' ','\\n');", self.page)
        # The welcome tour opened from the palette returns focus to where the palette opened.
        self.assertIn("const returnFocus=commandReturnFocus;commandReturnFocus=null;openWelcome(null,{returnFocus});", self.page)
        self.assertNotIn("WELCOME_PANELS[2].title=", self.page)

    def test_selected_session_columns_share_top_and_bottom_edges(self):
        decision = self.page.split('<aside class=previewDecision>', 1)[1].split('</aside>', 1)[0]
        # The right column opens with a heading row matching the left column's
        # "Execution Profile" heading, so the first cards start on one line.
        self.assertTrue(decision.lstrip().startswith(
            '<div class=section-h><h2>Session cost</h2><span class=hint>estimate</span></div>'
        ))
        self.assertLess(decision.index('class=section-h'), decision.index('id=preview-status-card'))
        self.assertLess(decision.index('id=preview-status-card'), decision.index('id=preview-run-budget-slot'))
        for rule in (
            # Both columns stretch to the row height; the first card in each
            # column absorbs slack so the last cards end on one line.
            ".previewRunGrid{display:grid;grid-template-columns:minmax(0,1.75fr) minmax(300px,.65fr);gap:12px;align-items:stretch}",
            ".previewChartColumn{display:grid;grid-template-rows:minmax(0,1fr) auto;gap:12px;min-width:0}",
            ".previewChartSlot{display:grid}",
            ".previewChartSlot #session-chart-module{display:grid;grid-template-rows:auto minmax(0,1fr);gap:12px}",
            ".previewChartSlot .section-h,.previewDecision>.section-h{margin:0}",
            ".previewDecision{display:grid;grid-template-columns:minmax(0,1fr);grid-template-rows:auto minmax(0,1fr) auto;gap:12px}",
            # The wider 18px inset must not push the budget slider past the
            # narrow 1024px right column.
            ".previewRunBudgetSlot .sessionBudgetTrack{grid-template-columns:52px minmax(0,1fr) 60px}",
            ".previewStatus{display:flex;flex-direction:column;",
            ".previewStatus .previewPressure{margin-block:auto}",
            # Right-column cards share the left cards' 18px inner edge.
            "body.sessionRoute #view-session.sessionDetail .previewRunBudgetSlot .sessionBudget{padding:18px;",
            # Context pressure uses the same label treatment as Started/Last activity.
            ".previewPressureHead>span{color:var(--faint);font-size:var(--fs-11);text-transform:uppercase;letter-spacing:.04em}",
        ):
            self.assertIn(rule, self.page)
        self.assertNotIn("#view-session.sessionDetail .previewRunGrid{gap:14px}", self.page)
        self.assertNotIn(".previewChartSlot .section-h{margin:0 0 10px}", self.page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_saved_non_folder_project_filters_migrate_to_other_local_sessions(self):
        value_match = re.search(r"function projectFilterValue\(.*?\n\}", self.page, re.DOTALL)
        filter_match = re.search(r"function storedModelFilterId\(.*?\n\}", self.page, re.DOTALL)
        migration_match = re.search(r"function migrateStoredProjectFilters\(.*?\n\}", self.page, re.DOTALL)
        self.assertIsNotNone(value_match, "Project filters need folder-only values")
        self.assertIsNotNone(filter_match, "Stored project filter values need a safe aggregate-ID boundary")
        self.assertIsNotNone(migration_match, "Stored project filters need a grouping migration")
        script = "const OTHER_LOCAL_SESSIONS_PROJECT='__other_local_sessions__';\n" + value_match.group(0) + "\n" + filter_match.group(0) + "\n" + migration_match.group(0) + r"""
const storage=new Map([
 ['tm_model_project','CLI'],
 ['tm_efficiency_project','No project'],
 ['tm_global_project','~/Documents/Codex/2026-08-31/task'],
 ['tm_model_project_filters',JSON.stringify({CLI:['gpt::Codex','gpt-5.6::Codex::xhigh','/Users/alice/private-project','gpt::Codex\t','gpt::Codex\u0000']})],
 ['tm_efficiency_project_filters',JSON.stringify({'No project':['gpt::Codex']})],
]);
globalThis.localStorage={getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,String(value)),removeItem:key=>storage.delete(key)};
['tm_model_project','tm_efficiency_project','tm_global_project'].forEach(key=>migrateStoredProjectFilters(key));
migrateStoredProjectFilters('tm_model_project_filters',true);
migrateStoredProjectFilters('tm_efficiency_project_filters',true);
console.log(JSON.stringify(Object.fromEntries(storage)));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        stored = json.loads(result.stdout)
        other = "__other_local_sessions__"
        self.assertEqual(stored["tm_model_project"], other)
        self.assertEqual(stored["tm_efficiency_project"], other)
        self.assertEqual(stored["tm_global_project"], other)
        self.assertEqual(json.loads(stored["tm_model_project_filters"]), {other: ["gpt::Codex", "gpt-5.6::Codex::xhigh"]})
        self.assertEqual(json.loads(stored["tm_efficiency_project_filters"]), {other: ["gpt::Codex"]})

    def test_session_card_hover_preserves_the_live_card_node(self):
        for marker in (
            "currentGrid=$('current-session-grid'),interactingCurrentSessionCard=currentGrid.querySelector('.currentSessionSlot:hover,.currentSessionCard:focus,.currentSessionDesktop:focus');",
            "const mountedCurrentSessionIds=[...currentGrid.querySelectorAll('.currentSessionCard[data-current-session-id]')].map(card=>card.dataset.currentSessionId);",
            "if(currentSessionDragId||(interactingCurrentSessionCard&&currentSessionIdsMatch(mountedCurrentSessionIds,rows))){syncCurrentSessionActivity(currentGrid,rows);return;}",
            "card.classList.remove('activity-working','activity-waiting','activity-recent');",
            "body.spectrumApp.sessionRoute #view-session .currentSessionCard:hover{transform:none}",
        ):
            self.assertIn(marker, self.page)

    def test_session_chart_only_offers_linear_and_cumulative_scales(self):
        scale_controls = self.page.split('<div class=seg id=scale>', 1)[1].split(
            "</div>", 1
        )[0]
        self.assertIn("data-scale=linear", scale_controls)
        self.assertIn("data-scale=cumulative", scale_controls)
        self.assertNotIn("data-scale=sqrt", scale_controls)
        self.assertNotIn("data-scale=log", scale_controls)
        self.assertIn("const CHART_SCALES=['linear','cumulative'];", self.page)
        self.assertIn("if(!CHART_SCALES.includes(chartScale))", self.page)
        self.assertIn("localStorage.setItem('tm_chart_scale',chartScale);", self.page)
        self.assertNotIn("if(scale==='sqrt')", self.page)
        self.assertNotIn("if(scale==='log')", self.page)

    def test_session_chart_supports_cumulative_cost_and_token_views(self):
        for marker in (
            "data-scale=cumulative",
            "Show running estimated cost or token totals through each execution.",
            "const CHART_SCALES=['linear','cumulative'];",
            "if(!CHART_SCALES.includes(chartScale))",
            "b.setAttribute('aria-pressed',selected?'true':'false');",
            "function cumulativeChartParts(parts,mode)",
            "const keys=mode==='cost'?['cost']:['fresh','read','write','cache','total','out'];",
            "keys.forEach(key=>{sums[key]+=Math.max(0,Number(part[key]||0));});",
            "const cumulativeTokens=mode==='tokens'&&chartScale==='cumulative';",
            "const cumulativeCost=mode==='cost'&&chartScale==='cumulative';",
            "const cumulativeValues=cumulativeTokens||cumulativeCost;",
            "parts=cumulativeValues?cumulativeChartParts(rawParts,mode):rawParts",
            "running estimated cost through each execution",
            "running token totals through each execution",
            "cumulative cost",
            "cumulative usd",
            "cumulative tokens",
            "const tokenPrefix=cumulativeTokens?'cumulative ':'';",
        ):
            self.assertIn(marker, self.page)
        self.assertIn("rawParts.map(p=>p.tools)", self.page)
        self.assertIn("rawParts.map(p=>p.reason)", self.page)

    def test_execution_overview_separates_activity_from_removable_optimization(self):
        for marker in ("id=ov-activity-tools", "id=ov-optional-use", "id=ov-unused-packs"):
            self.assertIn(marker, self.page)
        self.assertIn("renderCurrentOptimization(s)", self.page)
        self.assertIn("function setCapabilityStatBar(id,value)", self.page)
        self.assertLess(
            self.page.index("function setCapabilityStatBar(id,value)"),
            self.page.index("setCapabilityStatBar('ov-optional-bar'"),
        )
        self.assertIn("if(!bar)return;", self.page)
        self.assertIn("bar.setAttribute('aria-valuetext',label);", self.page)
        self.assertNotIn("renderCapabilityUsage('ov-cap'", self.page)
        self.assertIn("Default tools and MCP servers are read-only evidence", self.page)

    def test_non_current_views_keep_visible_copy_terse(self):
        expected_subtitles = {
            "models": [],
            "capabilities": ["Tools, MCP servers, and skills."],
            "settings": ["Connections, pricing, and updates."],
        }
        boundaries = (
            ("models", "daily"),
            ("capabilities", "settings"),
            ("settings", None),
        )
        for view, next_view in boundaries:
            section = self.page.split(f"id=view-{view}", 1)[1]
            section = section.split(
                f"id=view-{next_view}" if next_view else "<dialog class=welcome",
                1,
            )[0]
            paragraphs = [
                re.sub(r"<[^>]+>", "", value).strip()
                for value in re.findall(r"<p(?:\s[^>]*)?>(.*?)</p>", section, re.S)
            ]
            self.assertEqual(
                [value for value in paragraphs if value],
                expected_subtitles[view],
                f"{view} should carry only its concise page subtitle",
            )
            self.assertNotIn("class=foot", section)

        spend = self.page.split("id=view-daily", 1)[1].split("id=view-learn", 1)[0]
        self.assertNotIn("Estimated agent spend over time.", spend)
        total_note = self.page.split("$('s-total-note').textContent=", 1)[1].split(";\n", 1)[0]
        self.assertIn("'API-equivalent estimate'", total_note)
        self.assertIn("Estimate after discounts", total_note)
        self.assertIn("<p id=s-chart-note>Line = average</p>", spend)
        self.assertNotIn("class=foot", spend)

        for marker in (
            'aria-description="Observed model output divided by attributable timing.',
            'aria-description="Set a budget for each runtime',
            'aria-description="Give Codex and Claude read-only, on-demand access',
            "#fieldtip-popup{position:fixed",
            "No use observed",
        ):
            self.assertIn(marker, self.page)

    def test_all_sessions_supports_app_project_and_time_range_filters(self):
        for marker in ("id=g-app", "id=g-project", "id=g-time", "App filter",
                       "Projects filter", "Time range filter", "class=logsToolbarStatus", "id=g-active-filters",
                       "id=g-clear", "id=lf-cost", "id=lf-input", "id=lf-output", "id=lf-models"):
            self.assertIn(marker, self.page)
        clear_wrapper = re.search(r"<div class=filterClear>(.*?)</div>", self.page)
        self.assertIsNotNone(clear_wrapper)
        self.assertIn("id=g-clear", clear_wrapper.group(1))
        self.assertNotIn("id=g-count", clear_wrapper.group(1))
        self.assertLess(self.page.index("id=g-clear"), self.page.index("id=g-sort"))
        for value in ("value=today", "value=yesterday", "value=7d", "value=30d", "value=90d", "value=month", "value=last_month"):
            self.assertIn(value, self.page)
        self.assertIn("allSessionsView(workRows,{showChildren:globalShowChildren,app:globalApp,project:globalProject,rangeStart,rangeEnd,query:q})", self.page)
        self.assertIn("if(app&&appFilterGroup(s)!==app)return false;", self.page)
        self.assertIn("const appFilterGroup=session=>runtimeId(session)", self.page)
        self.assertIn("const appFilterLabel=session=>runtimeMeta(session).label", self.page)
        self.assertIn("['claude_code','claude_desktop'].includes(globalApp)", self.page)
        self.assertIn("if(project&&projectFilterValue(s.project)!==project)return false;", self.page)
        self.assertIn("Other local sessions", self.page)
        self.assertIn("const {start:rangeStart,end:rangeEnd}=timeFilterBounds(globalTime);", self.page)
        self.assertIn("tm_global_app", self.page)
        self.assertIn("tm_global_project", self.page)
        self.assertIn("tm_global_time", self.page)
        self.assertIn("renderAllSessionStats(view.statsRows)", self.page)
        self.assertIn("session.model_stats", self.page)
        self.assertIn("let globalTime=localStorage.getItem('tm_global_time')||DEFAULT_TIME_FILTER_WINDOW;", self.page)
        self.assertIn("globalSearch='';globalApp='';globalProject='';globalTime=DEFAULT_TIME_FILTER_WINDOW", self.page)
        self.assertIn("if(key==='time'){globalTime=DEFAULT_TIME_FILTER_WINDOW;", self.page)
        self.assertIn("globalTime!==DEFAULT_TIME_FILTER_WINDOW&&['time',", self.page)
        self.assertIn("['tm_global_search','tm_global_app','tm_global_project','tm_global_time']", self.page)
        self.assertIn("fetch('/logs',{cache:'no-store'})", self.page)
        self.assertIn("allSessionInventory?mergeAllSessionInventory(allSessionInventory,xs.sessions)", self.page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_all_sessions_keep_one_row_per_trace_file(self):
        script = "\n".join(
            self.page_function(self.page, name)
            for name in ("sessionRowKey", "compareKeyFor", "mergeAllSessionInventory")
        ) + """
const inventory=[
 {id:'task-1',session:'rollout-a.jsonl',cost:0.91},
 {id:'task-1',session:'rollout-b.jsonl',cost:111.57},
 {id:'task-1',session:'rollout-c.jsonl',cost:0.16},
 {id:'legacy'},
];
const live=[{id:'task-1',session:'rollout-b.jsonl',cost:112.0}];
const merged=mergeAllSessionInventory(inventory,live);
console.log(JSON.stringify({
 keys:merged.map(sessionRowKey),
 cost:merged.reduce((sum,row)=>sum+(row.cost||0),0),
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        merged = json.loads(result.stdout)
        self.assertEqual(merged["keys"], [
            "rollout-a.jsonl", "rollout-b.jsonl", "rollout-c.jsonl", "legacy",
        ])
        self.assertAlmostEqual(merged["cost"], 0.91 + 112.0 + 0.16)

    def test_every_session_entry_point_addresses_one_trace_file(self):
        for marker in (
            "function sessionRowKey(row){return String(row?.session||row?.id||'');}",
            "function stateSessionKey(state){return String(state?.session||stateSessionId(state)||'');}",
            "renderedAllSessions=new Map(all.map(row=>[sessionRowKey(row),row]));",
            "const pinnedKey=pinned?(renderedAllSessions.has(pinned)?pinned:(stateSessionId(CURRENT)===pinned?stateSessionKey(CURRENT):'')):'';",
            "const key=sessionRowKey(s),rowKey=compareKeyFor(s),active=pinned?key===pinnedKey:Boolean(LATEST&&key===stateSessionKey(LATEST));",
            "row.dataset.id=key;",
            "data-delete-session=\"${esc(sessionRowKey(s))}\"",
            "body:JSON.stringify({session_id:target.id,trace:target.session||''})",
            "const workRows=workSessionFilter?all.filter(s=>workSessionFilter.keys.has(sessionRowKey(s))):all;",
            "keys:new Set(payload.keys.map(String))",
            "data-spend-session=\"${esc(sessionRowKey(row))}\"",
            "data-spend-insight-session=\"${esc(sessionRowKey(row))}\"",
            "href=\"${esc(sessionRoute(sessionRowKey(row)))}\"",
            "<span class=\"badge subagentTag\">Subagent</span>",
                ".srow .subagentTag{",
                "sessionDeleteAvailable(s,renderedAllSessionActions),s.subagent,s.client,",
        ):
            self.assertIn(marker, self.page)
        self.assertNotIn("workSessionFilter.ids.has(String(s.id))", self.page)
        self.assertNotIn("rows.set(String(row.id),row)", self.page)
        self.assertNotIn("renderedAllSessions=new Map(all.map(row=>[String(row.id),row]));", self.page)

    def test_current_and_all_sessions_share_the_defined_app_badge_helper(self):
        self.assertIn("const appBadgeClass=session=>", self.page)
        self.assertIn("'badge app '+appBadgeClass(s)", self.page)
        self.assertIn("${appBadgeClass(s)}", self.page)
        self.assertNotIn("providerBadgeClass", self.page)

    def test_current_sessions_keep_opencode_identity(self):
        self.assertIn(
            "const provider=runtimeId(row)",
            self.page,
        )
        self.assertIn('class="card currentSessionCard provider-${esc(provider)}', self.page)
        self.assertIn("Object.entries(RUNTIME_CATALOG).filter(([id])=>id!=='unknown-runtime')", self.page)

    def test_session_delete_actions_require_confirmation_and_use_trash_endpoint(self):
        for marker in ("id=session-delete", "data-delete-session", "id=session-delete-dialog",
                       "id=session-delete-confirm", "Move to Trash", "/session/delete"):
            self.assertIn(marker, self.page)
        self.assertIn("openSessionDeleteDialog", self.page)
        self.assertIn("event.stopPropagation()", self.page)
        self.assertIn("X-Token-Meter-Action", self.page)
        self.assertIn("Provider metadata and configuration are not changed", self.page)
        self.assertIn("function sessionDeleteAvailable(target,actions)", self.page)
        self.assertIn("actions?.read_only_providers", self.page)
        self.assertIn("status.alert_percent??status.percent", self.page)
        self.assertIn("don't trigger budget notifications", self.page)
        self.assertIn("!sessionDeleteAvailable(target,actions)", self.page)

    def test_token_chart_fills_use_the_same_colors_as_legend_and_tooltip(self):
        self.assertIn("const band=(top,bottom,col,opacity)=>", self.page)
        self.assertIn(
            "band(cacheRead,zeros,CHART.cacheRead,.18)+"
            "band(cacheTop,cacheRead,CHART.cacheWrite,.18)",
            self.page,
        )
        self.assertIn('fill-opacity="${opacity}"', self.page)
        self.assertNotIn("cacheReadFill", self.page)
        self.assertNotIn("cacheWriteFill", self.page)

    def test_selected_session_stays_pinned_while_other_live_state_changes(self):
        self.assertIn("function followLatestSession", self.page)
        self.assertIn("function refreshSelectedSession", self.page)
        self.assertIn(
            "fetchLiveJson('/session?id='+encodeURIComponent(id)+'&live=1',LIVE_XSESSION.session,'Session')",
            self.page,
        )
        self.assertLess(
            self.page.index("const LIVE_XSESSION="),
            self.page.index("applyLocationRoute();"),
        )
        self.assertLess(
            self.page.index("selectedPollId=''"),
            self.page.index("applyLocationRoute();"),
        )
        self.assertIn("const SELECTED_SESSION_POLL_MS=2000", self.page)
        self.assertIn(
            "setInterval(()=>refreshSelectedSession({show:true}),SELECTED_SESSION_POLL_MS)",
            self.page,
        )
        self.assertIn("pinned=id;\n if(show)applyHashRoute();\n return refreshSelectedSession", self.page)
        self.assertIn(
            "const sessionSurfaceActive=$('view-session').classList.contains('on');\n"
            " if(!pinned&&sessionSurfaceActive&&currentPanel!=='sessions')renderSession(LATEST);",
            self.page,
        )
        self.assertNotIn("if(LATEST&&id===stateSessionId(LATEST))", self.page)
        self.assertNotIn("if(pinned&&pinned===stateSessionId(LATEST))", self.page)

    def test_sessions_overview_is_bounded_routed_and_accessible(self):
        for marker in (
            "id=preview-surface-sessions",
            "id=current-session-grid",
            "function renderCurrentSessions",
            "function openCurrentSessions",
            "'/#sessions'",
            "data-current-session-id",
            '<h1 id=session-page-title>Sessions</h1>',
            'class=currentSessionsCount id=current-session-count-label',
            "filterView.countLabel",
            "Working",
            "Waiting",
            "Recent",
            "provider-claude",
            "provider-codex",
            "provider-cursor",
        ):
            self.assertIn(marker, self.page)
        self.assertIn("const CURRENT_PANEL_KEYS=['sessions','run'];",
                      self.page)
        self.assertNotIn("data-current-panel=sessions", self.page)
        self.assertNotIn('id=current-tabs', self.page)
        self.assertRegex(self.page, r"id=tab-session[^>]*>.*?<span class=tabLabel>Sessions</span>")
        self.assertIn("if(h==='sessions'||h==='sessions-all'||h==='sessions-compare'||h==='current-sessions')", self.page)
        self.assertIn("history.replaceState(null,'','/#sessions')", self.page)
        self.assertIn("function currentSessionModelName", self.page)
        self.assertIn("row.session_name||row.project||'Untitled session'", self.page)
        self.assertIn("`${runtime} / ${model}${effort?` ${effort}`:''}`", self.page)
        self.assertIn("<span>Speed</span>", self.page)
        self.assertIn("speedFmt(throughput.output_tps)} tok/s", self.page)
        self.assertIn("<span>Output / $</span>", self.page)
        self.assertIn("row.output_per_dollar==null?'--':compactNumber(row.output_per_dollar)", self.page)
        self.assertIn(
            "const outputPerDollar=row.output_per_dollar==null?'--':compactNumber(row.output_per_dollar);",
            self.page,
        )
        self.assertNotIn(
            "const outputPerDollar=row.output_per_dollar==null?'--':compactNumber(row.output_per_dollar)+(estimate||row.token_estimate?' est':'');",
            self.page,
        )
        self.assertIn(".currentSessionMetrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr))", self.page)
        self.assertIn(
            "body.sessionRoute #view-session .currentSessionMetrics{grid-template-columns:.9fr 1.2fr 1fr;gap:0;margin-top:14px;padding-top:14px",
            self.page,
        )
        self.assertIn(
            "body.sessionRoute #view-session .currentSessionMetric{padding:0 14px",
            self.page,
        )
        self.assertNotIn("id=unpin", self.page)
        self.assertNotIn("Back to current sessions", self.page)
        self.assertNotIn("Back to all sessions", self.page)
        self.assertNotIn("$('unpin')", self.page)
        self.assertNotIn("Reorder: drag or ⌥ + arrows", self.page)
        self.assertNotIn("currentSessionsMoveHint", self.page)
        self.assertNotIn("currentSessionOpen", self.page)
        self.assertNotIn(">Open →</span>", self.page)

    def test_current_session_activity_filters_are_accessible_and_wired(self):
        for marker in (
            'id=current-session-activity-filters role=group aria-label="Filter current sessions by activity"',
            'data-current-activity-filter=all aria-pressed=true>All</button>',
            'data-current-activity-filter=working aria-pressed=false>Working</button>',
            'data-current-activity-filter=recent aria-pressed=false>Recent</button>',
            "let currentSessionRuntimeFilter='all',currentSessionActivityFilter='all';",
            "function renderCurrentSessionActivityFilters(selection)",
            "currentSessionFilterView(orderedRows,currentSessionRuntimeFilter,currentSessionActivityFilter)",
            "renderCurrentSessionActivityFilters(filterView.activitySelection);",
            "currentSessionActivityFilter=button.dataset.currentActivityFilter||'all';",
            "currentSessionsFilteredEmptyHtml(filterView.activitySelection)",
            "function keepCurrentSessionFilterFocusVisible(event)",
            "button.scrollIntoView({block:'nearest',inline:'nearest'});",
            "currentSessionRuntimeFilters.addEventListener('focusin',keepCurrentSessionFilterFocusVisible);",
            "currentSessionActivityFilters.addEventListener('focusin',keepCurrentSessionFilterFocusVisible);",
        ):
            self.assertIn(marker, self.page)
        self.assertIn(
            "$('current-session-activity-filters').hidden=true;",
            self.page,
        )
        for marker in (
            ".currentSessionFilterGroups{display:flex;align-items:center;gap:10px;min-width:0;overflow-x:auto",
            ".currentSessionFilterGroup[hidden]{display:none}",
            "body.sessionRoute #view-session .currentSessionRuntimeFilters,body.sessionRoute #view-session .currentSessionActivityFilters{display:flex;min-width:0}",
            "body.sessionRoute #view-session .currentSessionFilterGroups{display:grid;width:100%;max-width:none;overflow:visible}",
            "body.sessionRoute #view-session .currentSessionFilterGroup{min-width:0;max-width:100%;overflow-x:auto;overflow-y:hidden;scrollbar-width:thin}",
        ):
            self.assertIn(marker, self.page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_current_session_runtime_view_discovers_filters_and_preserves_valid_selection(self):
        match = re.search(
            r"^function currentSessionRuntimeView\(.*?^\}\n",
            self.page,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(match, "dashboard needs currentSessionRuntimeView")
        rows = [
            {"id": "claude-code", "provider": "claude"},
            {"id": "codex", "provider": "codex"},
            {"id": "claude-desktop", "provider": "claude"},
        ]
        script = """
const appFilterGroup=row=>row.provider;
const appFilterLabel=row=>({claude:'Claude',codex:'Codex',cursor:'Cursor'})[row.provider];
""" + match.group(0) + "\nconst rows=" + json.dumps(rows) + ";\n" + """
const all=currentSessionRuntimeView(rows,'all');
const claude=currentSessionRuntimeView(rows,'claude');
rows.push({id:'cursor',provider:'cursor'});
const emerged=currentSessionRuntimeView(rows,'claude');
const vanished=currentSessionRuntimeView(rows.filter(row=>row.provider!=='claude'),'claude');
console.log(JSON.stringify({all,claude,emerged,vanished}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        rendered = json.loads(result.stdout)
        self.assertEqual(rendered["all"], {
            "options": [
                {"id": "claude", "label": "Claude", "count": 2},
                {"id": "codex", "label": "Codex", "count": 1},
            ],
            "selection": "all",
            "rows": rows[:3],
            "countLabel": "3 active",
        })
        self.assertEqual(
            [row["id"] for row in rendered["claude"]["rows"]],
            ["claude-code", "claude-desktop"],
        )
        self.assertEqual(rendered["claude"]["countLabel"], "2 of 3 active")
        self.assertEqual(rendered["emerged"]["selection"], "claude")
        self.assertEqual(
            rendered["emerged"]["options"],
            [
                {"id": "claude", "label": "Claude", "count": 2},
                {"id": "codex", "label": "Codex", "count": 1},
                {"id": "cursor", "label": "Cursor", "count": 1},
            ],
        )
        self.assertEqual(rendered["vanished"]["selection"], "all")
        self.assertEqual(rendered["vanished"]["countLabel"], "2 active")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_current_session_filter_view_composes_runtime_and_sticky_activity_selection(self):
        names = (
            "currentSessionActivity",
            "currentSessionRuntimeView",
            "currentSessionFilterView",
        )
        functions = []
        for name in names:
            match = re.search(
                rf"^function {name}\(.*?^\}}\n",
                self.page,
                re.MULTILINE | re.DOTALL,
            )
            self.assertIsNotNone(match, f"dashboard needs {name}")
            functions.append(match.group(0))
        rows = [
            {"id": "claude-working", "provider": "claude", "activity_state": "working"},
            {"id": "claude-recent", "provider": "claude", "activity_state": "recent"},
            {"id": "codex-working", "provider": "codex", "activity_state": "working"},
            {"id": "codex-waiting", "provider": "codex", "activity_state": "waiting"},
        ]
        script = """
const appFilterGroup=row=>row.provider;
const appFilterLabel=row=>({claude:'Claude',codex:'Codex'})[row.provider];
""" + "\n".join(functions) + "\nconst rows=" + json.dumps(rows) + ";\n" + """
const codexWorking=currentSessionFilterView(rows,'codex','working');
const claudeRecent=currentSessionFilterView(rows,'claude','recent');
const zeroWorking=currentSessionFilterView(
 rows.filter(row=>row.activity_state!=='working'),
 'all',
 'working',
);
const invalidActivity=currentSessionFilterView(rows,'all','waiting');
console.log(JSON.stringify({codexWorking,claudeRecent,zeroWorking,invalidActivity}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        rendered = json.loads(result.stdout)
        self.assertEqual(
            [row["id"] for row in rendered["codexWorking"]["rows"]],
            ["codex-working"],
        )
        self.assertEqual(rendered["codexWorking"]["selection"], "codex")
        self.assertEqual(rendered["codexWorking"]["activitySelection"], "working")
        self.assertEqual(rendered["codexWorking"]["countLabel"], "1 of 4 active")
        self.assertEqual(
            [row["id"] for row in rendered["claudeRecent"]["rows"]],
            ["claude-recent"],
        )
        self.assertEqual(rendered["zeroWorking"]["rows"], [])
        self.assertEqual(rendered["zeroWorking"]["activitySelection"], "working")
        self.assertEqual(rendered["zeroWorking"]["countLabel"], "0 of 2 active")
        self.assertEqual(rendered["invalidActivity"]["activitySelection"], "all")
        self.assertEqual(rendered["invalidActivity"]["countLabel"], "4 active")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_current_sessions_filtered_empty_copy_describes_the_selected_activity(self):
        match = re.search(
            r"^function currentSessionsFilteredEmptyHtml\(.*?^\}\n",
            self.page,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(match, "dashboard needs currentSessionsFilteredEmptyHtml")
        script = match.group(0) + """
console.log(JSON.stringify({
 working:currentSessionsFilteredEmptyHtml('working'),
 recent:currentSessionsFilteredEmptyHtml('recent'),
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        rendered = json.loads(result.stdout)
        self.assertIn("No working sessions right now", rendered["working"])
        self.assertIn("No recent sessions right now", rendered["recent"])
        for html in rendered.values():
            self.assertIn("Filters are still active", html)
            self.assertNotIn("Your next live session appears here", html)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_current_session_keyboard_move_reorders_the_visible_runtime_sequence(self):
        match = re.search(
            r"^function currentSessionKeyboardMove\(.*?^\}\n",
            self.page,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(match, "dashboard needs currentSessionKeyboardMove")
        script = match.group(0) + """
const moved=currentSessionKeyboardMove(
 ['claude-1','codex-1','claude-2'],
 ['claude-1','claude-2'],
 'claude-1',
 1,
);
console.log(JSON.stringify(moved));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "ids": ["codex-1", "claude-2", "claude-1"],
            "index": 1,
            "total": 2,
        })

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_current_session_keyboard_move_uses_the_combined_filter_sequence(self):
        names = (
            "currentSessionActivity",
            "currentSessionKeyboardMove",
            "currentSessionRuntimeView",
            "currentSessionFilterView",
            "moveCurrentSessionCardBy",
        )
        functions = []
        for name in names:
            match = re.search(
                rf"^function {name}\(.*?^\}}\n",
                self.page,
                re.MULTILINE | re.DOTALL,
            )
            self.assertIsNotNone(match, f"dashboard needs {name}")
            functions.append(match.group(0))
        script = """
const appFilterGroup=row=>row.provider;
const appFilterLabel=row=>({claude:'Claude',codex:'Codex'})[row.provider];
const LATEST={xsession:{current_sessions:[
 {id:'claude-working',provider:'claude',activity_state:'working'},
 {id:'codex-working',provider:'codex',activity_state:'working'},
 {id:'claude-recent',provider:'claude',activity_state:'recent'},
]}};
const currentSessionRuntimeFilter='claude';
const currentSessionActivityFilter='working';
const orderedCurrentSessionRows=rows=>rows;
let saved=null;
const setCurrentSessionOrder=ids=>{saved=ids};
""" + "\n".join(functions) + """
const result=moveCurrentSessionCardBy('claude-working',1);
console.log(JSON.stringify({result,saved}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {"result": None, "saved": None})

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_current_session_ids_match_detects_a_stale_filtered_grid(self):
        match = re.search(
            r"^function currentSessionIdsMatch\(.*?^\}\n",
            self.page,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(match, "dashboard needs currentSessionIdsMatch")
        script = match.group(0) + """
const stable=currentSessionIdsMatch(
 ['claude-1','claude-2'],
 [{id:'claude-1'},{id:'claude-2'}],
);
const stale=currentSessionIdsMatch(
 ['claude-1','claude-2'],
 [{id:'codex-1'}],
);
console.log(JSON.stringify({stable,stale}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        rendered = json.loads(result.stdout)
        self.assertTrue(rendered["stable"])
        self.assertFalse(rendered["stale"])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_current_session_filter_focus_survives_emergence_and_falls_back_on_disappearance(self):
        match = re.search(
            r"^function currentSessionFilterFocusTarget\(.*?^\}\n",
            self.page,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(match, "dashboard needs currentSessionFilterFocusTarget")
        script = match.group(0) + """
const emerged=currentSessionFilterFocusTarget(
 {selection:'claude',options:[{id:'claude'},{id:'codex'},{id:'cursor'}]},
 'claude',
);
const vanished=currentSessionFilterFocusTarget(
 {selection:'all',options:[{id:'codex'}]},
 'claude',
);
console.log(JSON.stringify({emerged,vanished}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        rendered = json.loads(result.stdout)
        self.assertEqual(rendered["emerged"], "claude")
        self.assertEqual(rendered["vanished"], "all")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_session_flow_snapshot_uses_real_activity_and_marks_only_new_arrivals(self):
        match = re.search(
            r"^function sessionFlowSnapshot\(.*?^\}\n",
            self.page,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(match, "dashboard needs a pure sessionFlowSnapshot")
        script = match.group(0) + """
const unavailable=sessionFlowSnapshot(undefined,[],false);
const first=sessionFlowSnapshot([
 {id:'working',activity_state:'working'},
 {id:'waiting',activity_state:'waiting'},
 {id:'recent',activity_state:'recent'},
],[],false);
const next=sessionFlowSnapshot([
 {id:'waiting',activity_state:'waiting'},
 {id:'arriving',activity_state:'working'},
],first.ids,true);
const idle=sessionFlowSnapshot([],next.ids,true);
console.log(JSON.stringify({unavailable,first,next,idle}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        rendered = json.loads(result.stdout)
        self.assertEqual(rendered["unavailable"], {
            "evidence": "unavailable", "mode": "idle", "count": 0,
            "working": 0, "waiting": 0, "recent": 0,
            "ids": [], "arriving": [],
        })
        self.assertEqual(rendered["first"], {
            "evidence": "available", "mode": "working", "count": 3,
            "working": 1, "waiting": 1, "recent": 1,
            "ids": ["working", "waiting", "recent"], "arriving": [],
        })
        self.assertEqual(rendered["next"], {
            "evidence": "available", "mode": "working", "count": 2,
            "working": 1, "waiting": 1, "recent": 0,
            "ids": ["waiting", "arriving"], "arriving": ["arriving"],
        })
        self.assertEqual(rendered["idle"], {
            "evidence": "available", "mode": "idle", "count": 0,
            "working": 0, "waiting": 0, "recent": 0,
            "ids": [], "arriving": [],
        })

    def test_current_day_summary_compares_partial_days_without_estimate_suffix(self):
        script = f"""
const fs=require('fs');const page=fs.readFileSync({json.dumps(str(Path(DASHBOARD_PAGE)))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
const nodes={{}};const $=id=>nodes[id]||(nodes[id]={{textContent:'',classList:{{toggle(){{}},add(){{}},remove(){{}}}},setAttribute(){{}},removeAttribute(){{}},dataset:{{}}}});
const money=v=>'$'+Number(v||0).toFixed(2),f=v=>String(v),countWord=(n,w)=>n===1?w:w+'s';
const metricAvailable=(row,k)=>row.availability?.[k]!==false,metricPartial=(row,k)=>Boolean(row.partial?.[k]);
const hasLocalEstimate=row=>row.basis==='mixed',estimateSuffix=row=>row.basis==='mixed'?' incl. est':'';
const setCostValue=(id,text)=>{{$(id).textContent=text;}};
eval(['localDayKey','dailyDeltaLabel','renderCurrentDaySummary'].map(extract).join('\\n'));
const day=offset=>{{const d=new Date();d.setHours(12,0,0,0);d.setDate(d.getDate()-offset);return localDayKey(d);}};
const out={{}};
for(const [name,todayPartial,yesterdayPartial] of [['both',true,true],['today',true,false],['yesterday',false,true],['none',false,false]]){{
 renderCurrentDaySummary({{daily:[
  {{day:day(0),cost:57.25,sessions:4,basis:'mixed',partial:{{cost:todayPartial}}}},
  {{day:day(1),cost:50,sessions:14,partial:{{cost:yesterdayPartial}}}}]}});
 out[name]={{spend:$('current-day-spend').textContent,spendNote:$('current-day-spend-note').textContent,vs:$('current-day-vs-yesterday').textContent,note:$('current-day-vs-yesterday-note').textContent}};
}}
const run=(daily,key)=>{{renderCurrentDaySummary({{daily}});out[key]={{vs:$('current-day-vs-yesterday').textContent,note:$('current-day-vs-yesterday-note').textContent,spendNote:$('current-day-spend-note').textContent}};}};
run([{{day:day(0),cost:5,sessions:1,partial:{{cost:true}}}}],'noYesterday');
run([{{day:day(0),cost:5,sessions:1}},{{day:day(1),cost:4,sessions:1,availability:{{cost:false}}}}],'yesterdayUnavailable');
run([{{day:day(1),cost:4,sessions:1}}],'noActivityToday');
run([{{day:day(0),cost:5,sessions:1}},{{day:day(1),cost:4,sessions:1,basis:'mixed'}}],'yesterdayEstimated');
process.stdout.write(JSON.stringify(out));
"""
        out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
        for case in (out[name] for name in ("both", "today", "yesterday", "none")):
            self.assertEqual(case["spend"], "$57.25")
            self.assertNotEqual(case["vs"], "--")
        self.assertEqual(out["both"]["note"], "vs $50.00 yesterday · both days partial")
        self.assertEqual(out["today"]["note"], "vs $50.00 yesterday · today partial")
        self.assertEqual(out["yesterday"]["note"], "vs $50.00 yesterday · yesterday partial")
        self.assertEqual(out["none"]["note"], "vs $50.00 yesterday")
        self.assertEqual(out["none"]["vs"], out["both"]["vs"])
        # Estimates stay disclosed now that the value has no suffix.
        self.assertEqual(out["both"]["spendNote"], "Partial billing coverage · includes estimates")
        self.assertEqual(out["none"]["spendNote"], "API-equivalent estimate · includes local estimates")
        self.assertEqual(out["yesterdayEstimated"]["note"], "vs $4.00 est yesterday")
        self.assertEqual((out["noYesterday"]["vs"], out["noYesterday"]["note"]),
                         ("New", "No recorded spend yesterday · today partial"))
        self.assertEqual((out["yesterdayUnavailable"]["vs"], out["yesterdayUnavailable"]["note"]),
                         ("--", "Yesterday cost unavailable"))
        self.assertEqual((out["noActivityToday"]["vs"], out["noActivityToday"]["note"]),
                         ("--", "No recorded activity today"))


if __name__ == "__main__":
    unittest.main()
