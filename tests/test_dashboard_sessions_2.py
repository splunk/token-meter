import unittest
import json
import re
import shutil
import subprocess
import meter
from tests.dashboard_layout_support import DashboardLayoutCase


class DashboardSessionsLayoutTests2(DashboardLayoutCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_readouts_compare_against_prior_days_and_never_invent_baselines(self):
        functions = []
        for name in ("localDayKey", "typicalComparison", "typicalCaption", "readoutScaleHtml", "dailyBaseline"):
            match = re.search(rf"^function {name}\(.*?^\}}\n", self.page, re.MULTILINE | re.DOTALL)
            self.assertIsNotNone(match, f"dashboard needs {name}")
            functions.append(match.group(0))
        medians = re.search(r"^const medianOf=.*$", self.page, re.MULTILINE)
        self.assertIsNotNone(medians)
        script = """
const esc=s=>String(s??'');
const money=n=>'$'+Number(n||0).toFixed(2);
const pct=n=>Math.round((n||0)*100)+'%';
const waitFmt=n=>Math.round(n)+'s';
const metricAvailable=(row,metric)=>row?.availability?.[metric]!==false;
const metricPartial=(row,metric)=>row?.coverage?.[metric]?.complete===false;
const hasLocalEstimate=row=>row?.usage_basis==='local_estimate';
""" + medians.group(0) + "\n" + "".join(functions) + """
const days=[
 {day:'2026-10-13',sessions:9,cost:50},
 {day:'2026-10-12',sessions:2,cost:2},
 {day:'2026-10-11',sessions:4,cost:4},
 {day:'2026-10-10',sessions:6,cost:6,coverage:{cost:{complete:false}}},
 {day:'2026-10-09',sessions:0,cost:0},
 {day:'2026-08-01',sessions:30,cost:900},
];
const baseline=dailyBaseline(days,'2026-10-13');
const burst=dailyBaseline([
 {day:'2026-10-12',sessions:1000,cost:200},
 {day:'2026-10-11',sessions:10,cost:120},
 {day:'2026-10-10',sessions:5,cost:50},
],'2026-10-13');
console.log(JSON.stringify({
 baseline,
 burst,
 empty:dailyBaseline([], '2026-10-13'),
 above:typicalCaption(6,2,'your typical day','$2.00'),
 below:typicalCaption(1,4,'your typical day','$4.00'),
 near:typicalCaption(2.1,2,'your typical day','$2.00'),
 none:typicalCaption(0,2,'your typical day','$2.00'),
 missing:typicalCaption(3,null,'your typical day',''),
 scale:readoutScaleHtml({value:6,typical:2,label:'x'}),
 calm:readoutScaleHtml({value:6,typical:2,toned:false}),
 noScale:readoutScaleHtml({value:6,typical:0}),
}));
"""
        result = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
        # Today, the partial-cost day, idle days, and days older than 30 days stay out of the baseline.
        self.assertEqual(result["baseline"]["days"], 3)
        self.assertEqual(result["baseline"]["typicalCost"], 3)
        self.assertEqual(result["baseline"]["highCost"], 4)
        self.assertEqual(result["baseline"]["typicalSessions"], 4)
        self.assertEqual(result["baseline"]["sessionCost"], 1)
        # A day of many tiny scripted sessions must not swamp the per-session baseline.
        self.assertEqual(result["burst"]["sessionCost"], 10)
        self.assertIsNone(result["empty"]["typicalCost"])
        self.assertIsNone(result["empty"]["sessionCost"])
        self.assertEqual(result["above"], "3.0× your typical day of $2.00")
        self.assertEqual(result["below"], "75% below your typical day of $4.00")
        self.assertEqual(result["near"], "In line with your typical day of $2.00")
        self.assertEqual(result["none"], "Nothing yet · your typical day is $2.00")
        self.assertEqual(result["missing"], "")
        self.assertIn('data-tone="high"', result["scale"])
        self.assertIn("--at:90.9%;--typ:30.3%", result["scale"])
        self.assertIn('data-tone=""', result["calm"])
        self.assertEqual(result["noScale"], "")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_empty_current_sessions_render_live_arrival_and_real_seven_day_history(self):
        functions = []
        for name in ("currentSessionsIdleHistory", "currentSessionsEmptyHtml"):
            match = re.search(
                rf"^function {name}\(.*?^\}}\n",
                self.page,
                re.MULTILINE | re.DOTALL,
            )
            self.assertIsNotNone(match, f"dashboard needs {name}")
            functions.append(match.group(0))
        fixture = {"daily": [
            {"day": "2026-08-18", "sessions": 1, "cost": 1.25,
             "providers": [{"provider": "codex"}],
             "availability": {"cost": True},
             "coverage": {"cost": {"complete": True}},
             "usage_basis": "local_estimate"},
            {"day": "2026-08-17", "sessions": 5, "cost": 2.0,
             "providers": [{"provider": "codex"}, {"provider": "claude"}],
             "availability": {"cost": True},
             "coverage": {"cost": {"complete": True}},
             "usage_basis": "reported"},
            {"day": "2026-08-12", "sessions": 2, "cost": 50.0,
             "providers": [{"provider": "claude"}],
             "availability": {"cost": False},
             "coverage": {"cost": {"complete": False}},
             "usage_basis": "unavailable"},
            {"day": "2026-08-11", "sessions": 99, "cost": 99.0,
             "providers": [{"provider": "outside-window"}],
             "availability": {"cost": True},
             "coverage": {"cost": {"complete": True}},
             "usage_basis": "reported"},
        ]}
        script = """
const f=value=>String(value);
const money=value=>'$'+Number(value).toFixed(2);
const esc=value=>String(value);
const countWord=(count,singular,plural=singular+'s')=>Number(count)===1?singular:plural;
""" + "\n".join(functions) + "\nconst xs=" + json.dumps(fixture) + ";\n" + """
const history=currentSessionsIdleHistory(xs,'2026-08-18');
const html=currentSessionsEmptyHtml(xs,'2026-08-18');
const firstRunHtml=currentSessionsEmptyHtml({daily:[]},'2026-08-18');
console.log(JSON.stringify({history,html,firstRunHtml}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        rendered = json.loads(result.stdout)
        self.assertEqual(rendered["history"], {
            "days": [
                {"day": "2026-08-12", "sessions": 2},
                {"day": "2026-08-13", "sessions": 0},
                {"day": "2026-08-14", "sessions": 0},
                {"day": "2026-08-15", "sessions": 0},
                {"day": "2026-08-16", "sessions": 0},
                {"day": "2026-08-17", "sessions": 5},
                {"day": "2026-08-18", "sessions": 1},
            ],
            "sessions": 8,
            "cost": 3.25,
            "costAvailable": True,
            "costPartial": True,
            "costEstimated": True,
            "runtimes": ["claude", "codex"],
        })
        html = rendered["html"]
        for marker in (
            "Your next live session appears here",
            "Next live session",
            "Cost",
            "Output / $",
            "Context in use",
            "Speed",
            "8 past sessions",
            "At least $3.25 est tracked",
            "2 runtimes",
            "data-current-empty-history",
            "Explore 8 past sessions",
            'href="https://github.com/splunk/token-meter/issues"',
            "Share feedback",
        ):
            self.assertIn(marker, html)
        self.assertIn("No past sessions yet", rendered["firstRunHtml"])
        self.assertIn("Your seven-day activity will build here.", rendered["firstRunHtml"])
        self.assertNotIn("data-current-empty-history", rendered["firstRunHtml"])

    def test_session_cards_use_compact_readable_metrics(self):
        for marker in (
            ".currentSessionGrid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}",
            ".currentSessionGrid{gap:10px}",
            ".currentSessionCard{min-height:190px;padding:16px 18px 14px}",
            ".currentSessionIdentity h3{font-size:var(--fs-18)}",
            ".currentSessionMetric b{margin-top:4px;font-size:var(--fs-18)}",
            "font-variant-numeric:tabular-nums",
            ".currentSessionMetric b.mono{font-size:var(--fs-18)}",
            ".currentSessionMetric b,.currentSessionMetric b.mono{font-size:var(--fs-20)}",
            "@media(max-width:700px){.currentSessionGrid{grid-template-columns:1fr;gap:8px}",
            ".currentSessionCard{min-height:184px;padding:14px 14px 12px}",
            ".currentSessionMetric b,.currentSessionMetric b.mono{font-size:var(--fs-16)",
            "@media(max-width:700px){.currentSessionMetric b,.currentSessionMetric b.mono{font-size:var(--fs-18)}",
        ):
            self.assertIn(marker, self.page)

    def test_session_cards_show_context_pressure_or_window_fallback(self):
        for marker in (
            "function currentSessionContextSparkline(row)",
            "Array.isArray(context.samples)",
            "if(!windowSize)return {",
            "currentSessionContextUnavailable",
            "window --",
            "Context window unavailable;",
            "Context window and latest token count unavailable.",
            "const plotted=samples.slice(-24)",
            "const values=plotted.map(value=>value/windowSize)",
            "const width=96,height=22",
            "currentSessionContextBar",
            "value>=.85?' high':value>=.7?' watch'",
            "<svg class=currentSessionContextSpark",
            "class=currentSessionContextValue",
            "const contextSpark=currentSessionContextSparkline(row)",
            "${contextSpark.html}",
            "<span>Context in use</span>",
            ">Context in use</div><div class=\"v mono\" id=ov-context>",
            ">Context in use</div><div class=\"v mono\" id=preview-context>",
            "cacheSavingsAvailable?money(cache.saved||s.cache_saved||0):'--'",
            "const cardDescription=`${heroCaption?heroCaption+'. ':''}${contextSpark.summary}",
        ):
            self.assertIn(marker, self.page)
        self.assertNotIn("const values=windowSize?plotted.map(value=>value/windowSize):plotted", self.page)
        self.assertNotIn("currentSessionContextCaption", self.page)
        self.assertNotIn("currentSessionContextGuide", self.page)

    def test_session_cards_do_not_have_native_hover_tooltips(self):
        session_cards = self.page.split("function renderCurrentSessions(state=LATEST){", 1)[1].split(
            "const currentSessionGrid=$('current-session-grid');", 1
        )[0]
        for marker in (
            'class=currentSessionRuntime title=',
            '<h3 title=',
            '<p title=',
            '<b class=mono title=',
            "const contextNote=",
        ):
            self.assertNotIn(marker, session_cards)
        self.assertIn('aria-description="${esc(cardDescription)}"', session_cards)

    def test_run_status_card_has_no_decorative_context_circle(self):
        self.assertNotIn("#view-session.sessionDetail .previewStatus:before", self.page)
        self.assertNotIn("--context-pressure", self.page)
        self.assertNotIn(".style.setProperty('--context-pressure'", self.page)
        self.assertIn('id=preview-context-bar', self.page)
        self.assertIn("$('preview-context-bar').querySelector('i').style.width=", self.page)

    def test_sessions_is_default_new_cards_lead_and_known_cards_keep_manual_order(self):
        for marker in (
            "$('tab-session').onclick=()=>{window.scrollTo(0,0);openCurrentSessions();};",
            "label:'Sessions'",
            "action:'sessions'}",
            "setHashRoute('sessions',{replace:true,apply:false})",
            "const CURRENT_SESSION_ORDER_KEY='tm_current_session_order_v1'",
            "const CURRENT_SESSION_ORDER_MIGRATION_KEY='tm_current_session_newest_first_v1'",
            "function orderedCurrentSessionRows(rows)",
            "function currentSessionActivity(row)",
            "function syncCurrentSessionActivity(root,rows)",
            "if(!Array.isArray(sourceRows))",
            "function moveCurrentSessionCard(sourceId,targetId,after=false)",
            "function moveCurrentSessionCardBy(sourceId,delta)",
            "function focusCurrentSessionCard(id)",
            "draggable=true",
            "event.dataTransfer.effectAllowed='move'",
            "currentSessionGrid.addEventListener('dragover'",
            "currentSessionGrid.addEventListener('drop'",
            "currentSessionGrid.addEventListener('dragend'",
            "currentSessionGrid.addEventListener('keydown'",
            "event.altKey",
            "Press ⌥ and an arrow key to move.",
            "id=current-session-order-status",
        ):
            self.assertIn(marker, self.page)
        self.assertIn("if(!id||byId.has(id))return;", self.page)
        self.assertIn(
            "const known=currentSessionOrder.filter(id=>byId.has(id)),seen=new Set(known);",
            self.page,
        )
        self.assertIn(
            "if(currentSessionDragId||(interactingCurrentSessionCard&&currentSessionIdsMatch(mountedCurrentSessionIds,rows))){syncCurrentSessionActivity(currentGrid,rows);return;}",
            self.page,
        )
        self.assertIn(
            "const unseen=sourceIds.filter(id=>!seen.has(id));\n"
            " let next=[...unseen,...known];",
            self.page,
        )
        self.assertIn(
            "if(currentSessionOrderNeedsNewestMigration&&sourceIds.length){\n"
            "  const newest=sourceIds[0];\n"
            "  next=[newest,...next.filter(id=>id!==newest)];",
            self.page,
        )
        self.assertNotIn("next.push(id);seen.add(id)", self.page)
        limit = re.search(r"const CURRENT_SESSION_ORDER_LIMIT=(\d+);", self.page)
        self.assertIsNotNone(limit)
        self.assertEqual(int(limit.group(1)), meter.CURRENT_SESSION_LIMIT)
        self.assertIn(".slice(0,CURRENT_SESSION_ORDER_LIMIT))]:[];", self.page)
        self.assertIn("JSON.stringify(currentSessionOrder.slice(0,CURRENT_SESSION_ORDER_LIMIT))", self.page)
        self.assertNotIn("slice(0,64))]:[];", self.page)

    def test_cursor_provenance_is_integrated_across_dashboard_views(self):
        for marker in (
            "const hasLocalEstimate", "const estimateSuffix",
            "local token proxies are not comparable", "stroke-dasharray",
            "id=c-runtime-filter", "Input context proxy",
            "visible text estimate", "hasLocalEstimate(row)",
        ):
            self.assertIn(marker, self.page)


if __name__ == "__main__":
    unittest.main()
