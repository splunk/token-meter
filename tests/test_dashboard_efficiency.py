import unittest
import json
import re
import shutil
import subprocess
from tests.dashboard_layout_support import DashboardLayoutCase


class DashboardEfficiencyLayoutTests(DashboardLayoutCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_efficiency_chart_bridges_gaps_marks_isolated_days_and_never_plots_zero(self):
        payload = self.run_chart_gap_harness(r"""
load(['chartGapRuns','chartGapBridge','efficiencyChartPointerIndex','efficiencyChartKeyIndex','efficiencyChartMetricPresentation','efficiencyChartInspection','efficiencyChartAnnouncement','efficiencyChartSmoothPath','renderEfficiencyChart']);
class AttrNode{constructor(){this.attrs={};}setAttribute(name,value){this.attrs[name]=String(value);}}
class InspectNode extends AttrNode{constructor(){super();this.line=new AttrNode();this.dot=new AttrNode();}querySelector(selector){return selector==='line'?this.line:this.dot;}}
class SvgNode extends AttrNode{constructor(parent){super();this.parentElement=parent;this.dataset={};this.inspect=null;}set innerHTML(value){this.html=value;this.inspect=new InspectNode();}get innerHTML(){return this.html;}querySelector(selector){return selector==='.efficiencyChartInspect'?this.inspect:null;}getBoundingClientRect(){return {left:0,width:520};}matches(){return false;}}
const tip={hidden:true,textContent:'',style:{}},status={textContent:''};
const svg=new SvgNode({querySelector(selector){return selector==='.efficiencyChartTip'?tip:status;}});
globalThis.$=()=>svg;globalThis.esc=value=>String(value);globalThis.pct=value=>`${Math.round(value*100)}%`;globalThis.compactNumber=value=>String(Math.round(value));
const values=[null,10,20,null,15,null,30,25,null];
const trend=values.map((value,index)=>({day:`2026-08-${String(index+1).padStart(2,'0')}`,metrics:{output_per_dollar:value}}));
renderEfficiencyChart('chart',trend,'output_per_dollar','Output per covered dollar');
const html=svg.innerHTML,x=index=>9+index*502/8,y=value=>10+70-(value/30)*70;
svg.onfocus();svg.onkeydown({key:'Home',preventDefault(){}});const leading=tip.textContent;
['ArrowRight','ArrowRight','ArrowRight'].forEach(key=>svg.onkeydown({key,preventDefault(){}}));const gap=tip.textContent;
process.stdout.write(JSON.stringify({
 bridges:attrs(html,'line','chartGapBridge'),
 expectedBridges:[[x(2),y(20),x(4),y(15)],[x(4),y(15),x(6),y(30)]].map(row=>row.map(value=>value.toFixed(1))),
 dots:attrs(html,'circle','efficiencyChartDot').map(dot=>[dot.cx,dot.cy]),
 expectedIsolated:[x(4).toFixed(1),y(15).toFixed(1)],
 lines:attrs(html,'path','efficiencyChartLine').map(path=>path.d),
 baseline:(10+70).toFixed(1),
 missingX:[0,3,5,8].map(index=>x(index).toFixed(1)),
 leading,gap,
}));
""")
        self.assertEqual(
            [[b["x1"], b["y1"], b["x2"], b["y2"]] for b in payload["bridges"]],
            payload["expectedBridges"],
        )
        self.assertIn(payload["expectedIsolated"], payload["dots"])
        self.assertEqual(len(payload["lines"]), 2, "only multi-day runs draw a solid line")
        for path in payload["lines"]:
            coordinates = re.findall(r"(-?\d+\.\d)\s(-?\d+\.\d)", path)
            self.assertNotIn(payload["baseline"], [y for _, y in coordinates])
            for missing_x in payload["missingX"]:
                self.assertNotIn(missing_x, [x for x, _ in coordinates])
        self.assertEqual(payload["leading"], "Aug 1 · No data")
        self.assertEqual(payload["gap"], "Aug 4 · No data")

    def test_efficiency_is_the_only_top_level_name_and_route(self):
        for marker in (
            "id=tab-efficiency", "id=view-efficiency", "data-page-signal=efficiency",
            "id=e-project", "id=e-model-picker", "id=e-model-options", "id=e-model-summary",
            "id=e-range", "id=e-output-dollar", "id=e-reasoning-ratio",
            "id=e-output-dollar-chart", "id=e-reasoning-chart",
            "id=e-context-load", "id=e-cache-hit", "id=e-model-table",
            "data-efficiency-sort=cache_hit_ratio",
            "data-efficiency-sort=cost", "data-efficiency-sort=output_per_dollar",
            "function renderEfficiency", "function efficiencyMetrics",
            "function renderEfficiencyChart", "function sortEfficiencyRows",
            "y2=\"${pad.top}\"", "r=\"3.5\"",
            "$('tab-efficiency').onclick=()=>openTopLevelRoute('efficiency')",
            "if(h==='efficiency')", "renderActiveEfficiency()",
            "tm_efficiency_range",
        ):
            self.assertIn(marker, self.page)
        self.assertRegex(
            self.page,
            r"id=tab-efficiency[^>]*>.*?<span class=tabLabel>Efficiency</span>",
        )
        nav_ids = [
            "tab-session", "tab-daily", "tab-models", "tab-efficiency",
            "tab-learn", "tab-capabilities", "tab-settings",
        ]
        positions = [self.page.index(f"id={tab_id}") for tab_id in nav_ids]
        self.assertEqual(positions, sorted(positions))
        efficiency = self.page.split("<div class=view id=view-efficiency>", 1)[1].split(
            "<div class=view id=view-learn>", 1
        )[0]
        self.assertIn("Output / $", efficiency)
        self.assertIn("Reasoning ratio", efficiency)
        self.assertIn("Cache hit ratio", efficiency)
        self.assertNotIn("Output / exec", efficiency)
        self.assertNotIn("output_per_execution", self.page)
        self.assertIn("Mechanical efficiency", efficiency)
        self.assertIn("Spend", efficiency)
        self.assertNotIn("Cache leverage", efficiency)
        self.assertNotIn("Retry waste", efficiency)
        self.assertNotIn("Efficiency score", efficiency)
        self.assertNotIn("recommendation", efficiency.lower())
        for stale in (
            "id=tab-insights", "id=view-insights", "route:'insights'",
            "tm_insights_range", "function renderInsights",
        ):
            self.assertNotIn(stale, self.page)

    def test_efficiency_table_keeps_model_app_and_effort_as_columns(self):
        efficiency = self.page.split("<div class=view id=view-efficiency>", 1)[1].split(
            "<div class=view id=view-learn>", 1
        )[0]
        for header in (">Model<", ">App<", ">Reasoning effort<"):
            self.assertIn(header, efficiency)
        self.assertIn(
            "<td><div class=modelName title=\"${esc(row.model)}\">${esc(row.model)}</div></td><td>${esc(row.runtime)}",
            self.page,
        )

    def test_selected_session_has_a_compact_two_metric_efficiency_module(self):
        summary = self.page.split('<div class="session-panel on" id=panel-summary>', 1)[1].split(
            '<details class=usageDetails', 1
        )[0]
        for marker in (
            "id=session-efficiency", "id=session-output-dollar",
            "id=session-output-dollar-note", "id=session-reasoning-ratio",
            "id=session-reasoning-ratio-note", "function renderSessionEfficiency",
            "function sessionEfficiencySource", "aggregateModelDays(source.model_stats||[])",
            "id=preview-run-efficiency-slot",
            "mountCurrentModule('preview-run-efficiency-slot','session-efficiency')",
            "['session-efficiency-home','session-efficiency']",
        ):
            self.assertIn(marker, self.page)
        self.assertLess(summary.index("id=session-efficiency"), summary.index("id=session-budget-home"))
        session_efficiency = summary.split("id=session-efficiency", 1)[1].split(
            "id=session-budget-home", 1
        )[0]
        self.assertEqual(session_efficiency.count('class="sessionEfficiencyMetric metric"'), 2)
        self.assertIn("Output / $", session_efficiency)
        self.assertIn("Reasoning ratio", session_efficiency)
        self.assertNotIn("<svg", session_efficiency)
        self.assertNotIn("By model", session_efficiency)

    def test_selected_session_efficiency_is_inside_the_run_status_card(self):
        run_surface = self.page.split('id=preview-surface-run', 1)[1].split(
            '<div class=previewKpis', 1
        )[0]
        status_card = run_surface.split('id=preview-status-card', 1)[1].split(
            '</section>', 1
        )[0]
        self.assertIn('id=preview-run-efficiency-slot', status_card)
        self.assertLess(
            status_card.index('id=preview-run-efficiency-slot'),
            status_card.index('class=previewPressure'),
        )
        self.assertIn(
            ".previewStatus .previewRunEfficiencySlot .sessionEfficiency{grid-template-columns:repeat(2",
            self.page,
        )

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_session_efficiency_uses_the_matching_cross_session_summary(self):
        match = re.search(
            r"function sessionEfficiencySource\(.*?\n\}", self.page, re.DOTALL,
        )
        self.assertIsNotNone(match)
        script = r"""
const stateSessionId=state=>state?.source?.id||null;
""" + match.group(0) + r"""
const matched={source:{id:'selected',key:'tright'},model_stats:[],xsession:{sessions:[
  {id:'other',key:'tother',model_stats:[{model:'wrong-id'}]},
  {id:'selected',key:'twrong',model_stats:[{model:'wrong-trace'}]},
  {id:'selected',key:'tright',model_stats:[{model:'right'}],usage_basis:'reported'},
]}};
const direct={source:{id:'direct'},model_stats:[{model:'fallback'}],xsession:{sessions:[]}};
const stale={source:{id:'selected',key:'tmissing'},model_stats:[{model:'fail-closed'}],xsession:{sessions:[
  {id:'selected',key:'twrong',model_stats:[{model:'wrong-trace'}]},
]}};
const attached={source:{id:'selected',key:'tright'},selected_stats:{model_stats:[{model:'attached-exact'}],usage_basis:'reported'},xsession:{sessions:[
  {id:'selected',key:'tright',model_stats:[{model:'stale-cross'}]},
]}};
console.log(JSON.stringify({matched:sessionEfficiencySource(matched),direct:sessionEfficiencySource(direct),stale:sessionEfficiencySource(stale),attached:sessionEfficiencySource(attached)}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        payload = json.loads(result.stdout)
        self.assertEqual(payload["matched"]["model_stats"][0]["model"], "right")
        self.assertEqual(payload["matched"]["usage_basis"], "reported")
        self.assertEqual(payload["direct"]["model_stats"][0]["model"], "fallback")
        self.assertEqual(payload["stale"]["model_stats"][0]["model"], "fail-closed")
        self.assertEqual(payload["attached"]["model_stats"][0]["model"], "attached-exact")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_session_efficiency_recovers_explicit_reasoning_coverage_from_summary_counts(self):
        match = re.search(
            r"function normalizeSessionEfficiencyAggregate\(.*?\n\}",
            self.page,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        script = match.group(0) + r"""
console.log(JSON.stringify({
  exact:normalizeSessionEfficiencyAggregate({
    executions:10,reasoning_executions:8,reasoning_tokens:30,
    reasoning_output_tokens:80,availability:{reasoning_tokens:false},
  }),
  structural:normalizeSessionEfficiencyAggregate({
    executions:10,reasoning_executions:0,reasoning_tokens:0,
    reasoning_output_tokens:0,thinking_executions:4,availability:{},
  }),
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        payload = json.loads(result.stdout)
        self.assertTrue(payload["exact"]["availability"]["reasoning_tokens"])
        self.assertFalse(payload["structural"]["availability"]["reasoning_tokens"])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_efficiency_metrics_are_bounded_and_keep_unavailable_evidence_null(self):
        match = re.search(
            r"function efficiencyMetrics\(row\)\{.*?\n\}",
            self.page,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "dashboard needs one shared efficiency formula")
        script = match.group(0) + r"""
const complete = efficiencyMetrics({
  input_tokens:1000, output_tokens:200, cost:2, executions:2,
  token_covered_executions:2, io_covered_executions:2,
  cost_covered_output_tokens:200, cost_covered_cost:2,
  cache_read_tokens:500, reasoning_tokens:50, reasoning_output_tokens:100,
  reasoning_executions:1, reasoning_unavailable_executions:1,
  attempts:4, retries:1, failed_attempts:1, attempt_samples:1,
  availability:{tokens:true,cost:true,cache:true,reasoning_tokens:true,attempts:true},
});
const missing = efficiencyMetrics({
  input_tokens:100, output_tokens:20, cost:0, executions:0,
  availability:{tokens:true,cost:false,cache:false,reasoning_tokens:false,attempts:false},
});
const partial = efficiencyMetrics({
  input_tokens:110, output_tokens:110, cost:.1, executions:3,
  token_covered_executions:2, io_covered_executions:1,
  cost_covered_output_tokens:10, cost_covered_cost:.1,
  coverage:{tokens:{complete:false}},
  availability:{tokens:true,cost:true,cache:false,reasoning_tokens:false,attempts:false},
});
const coveredZero = efficiencyMetrics({
  input_tokens:10, output_tokens:0, cost:1, executions:1,
  token_covered_executions:1, io_covered_executions:1,
  cost_covered_executions:1, cost_covered_output_tokens:0,
  cost_covered_cost:1,
  availability:{tokens:true,input_tokens:true,output_tokens:true,cost:true,cache:false,reasoning_tokens:false,attempts:false},
});
const inputOnly = efficiencyMetrics({
  input_tokens:10, output_tokens:0, cost:1, executions:1,
  token_covered_executions:0, io_covered_executions:0,
  cost_covered_executions:0,
  cost_covered_output_tokens:0,
  availability:{tokens:true,input_tokens:true,output_tokens:false,cost:true,cache:false,reasoning_tokens:false,attempts:false},
});
const zeroInput = efficiencyMetrics({
  input_tokens:0, output_tokens:10, cost:1, executions:1,
  token_covered_executions:1, io_covered_executions:1,
  cost_covered_executions:1, cost_covered_output_tokens:10,
  cost_covered_cost:1,
  availability:{tokens:true,input_tokens:true,output_tokens:true,cost:true,cache:false,reasoning_tokens:false,attempts:false},
});
const partialPaired = efficiencyMetrics({
  input_tokens:600, output_tokens:15, cost:.1, executions:3,
  token_covered_executions:3, io_covered_executions:2,
  io_covered_input_tokens:100, io_covered_output_tokens:10,
  coverage:{tokens:{complete:false}},
  availability:{tokens:true,input_tokens:true,output_tokens:true,cost:true,cache:false,reasoning_tokens:false,attempts:false},
});
const noPaired = efficiencyMetrics({
  input_tokens:600, output_tokens:15, cost:.1, executions:3,
  token_covered_executions:3, io_covered_executions:0,
  io_covered_input_tokens:0, io_covered_output_tokens:0,
  availability:{tokens:true,input_tokens:true,output_tokens:true,cost:true,cache:false,reasoning_tokens:false,attempts:false},
});
console.log(JSON.stringify({complete,missing,partial,coveredZero,inputOnly,zeroInput,partialPaired,noPaired}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        payload = json.loads(result.stdout)
        self.assertEqual(payload["complete"], {
            "output_per_dollar": 100,
            "reasoning_ratio": 0.5,
            "context_load": 5,
            "cache_hit_ratio": 0.5,
            "retry_waste": 0.25,
            "failure_rate": 0.25,
        })
        self.assertEqual(payload["missing"], {
            "output_per_dollar": None,
            "reasoning_ratio": None,
            "context_load": 5,
            "cache_hit_ratio": None,
            "retry_waste": None,
            "failure_rate": None,
        })
        self.assertEqual(payload["partial"]["output_per_dollar"], 100)
        self.assertIsNone(payload["partial"]["context_load"])
        self.assertEqual(payload["coveredZero"]["output_per_dollar"], 0)
        self.assertIsNone(payload["inputOnly"]["output_per_dollar"])
        self.assertEqual(payload["zeroInput"]["context_load"], 0)
        self.assertEqual(payload["partialPaired"]["context_load"], 10)
        self.assertIsNone(payload["noPaired"]["context_load"])

    def test_efficiency_uses_one_range_for_overall_and_per_model_statistics(self):
        for marker in (
            "const EFFICIENCY_RANGES=['today','yesterday','7','30','90','month','last_month','all']",
            "tm_efficiency_range", "tm_efficiency_project", "efficiencyRangeWindow", "efficiencyModelRows",
            "aggregateModelDays(efficiencyModelRows", "efficiencyMetrics(overall)",
            "efficiencyMetrics(row.window)", "efficiencyTrendDays",
            "Thinking / reasoning coverage", "thinking_executions",
            "token ratio unavailable", "e-reasoning-chart",
            "includes local estimates", "token_covered_executions",
            "Compare similar work · no quality judgment.",
        ):
            self.assertIn(marker, self.page)

        self.assertIn(
            "metric.output_per_dollar===null?'unavailable':data.coverage?.cost?.complete?'covered':'partial'",
            self.page,
        )

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_efficiency_model_filters_keep_runtime_identities_distinct(self):
        match = re.search(
            r"function filterEfficiencyModels\(.*?\n\}", self.page, re.DOTALL,
        )
        self.assertIsNotNone(match, "Efficiency needs to filter by model-runtime ID")
        script = match.group(0) + r"""
const rows=[
  {id:'gpt-5.6::Codex',model:'gpt-5.6',runtime:'Codex'},
  {id:'gpt-5.6::Cursor',model:'gpt-5.6',runtime:'Cursor'},
  {id:'claude-opus::Claude Code',model:'claude-opus',runtime:'Claude Code'},
];
console.log(JSON.stringify({
  all:filterEfficiencyModels(rows,[]).map(row=>row.id),
  selected:filterEfficiencyModels(rows,['gpt-5.6::Cursor']).map(row=>row.id),
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "all": ["gpt-5.6::Codex", "gpt-5.6::Cursor", "claude-opus::Claude Code"],
            "selected": ["gpt-5.6::Cursor"],
        })

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_efficiency_model_toggle_waits_for_the_selected_project_scope(self):
        match = re.search(
            r"function rerenderActiveEfficiency\(.*?\n\}", self.page, re.DOTALL,
        )
        self.assertIsNotNone(match, "Efficiency needs to avoid rendering stale project stats")
        script = match.group(0) + r"""
let activeEfficiencyStats={models:['all-projects']};
let activeEfficiencyScope='';
let efficiencyProject='project-b';
let rendered=0,requested=0;
function renderEfficiency(){rendered++;}
function renderActiveEfficiency(){requested++;}
rerenderActiveEfficiency();
activeEfficiencyScope='project-b';
rerenderActiveEfficiency();
console.log(JSON.stringify({rendered,requested}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {"rendered": 1, "requested": 1})

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_efficiency_effort_reads_default_only_for_apps_that_record_effort(self):
        script = "\n".join(re.search(rf"function {name}\(.*?\n\}}", self.page, re.DOTALL).group(0)
                           for name in ("modelEffortLabel", "efficiencyEffortLabel")) + r"""
console.log(JSON.stringify([
  efficiencyEffortLabel({reasoning_effort:'xhigh',providers:['codex']}),
  efficiencyEffortLabel({providers:['claude'],thinking_executions:9}),
  efficiencyEffortLabel({providers:['codex']}),
  efficiencyEffortLabel({providers:['cursor']}),
  efficiencyEffortLabel({providers:['claude','cursor']}),
]));
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout), ["xhigh", "default", "default", "unavailable", "unavailable"])
        self.assertIn("effort=efficiencyEffortLabel(row)", self.page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_efficiency_chart_delta_compares_latest_two_covered_days(self):
        match = re.search(r"function efficiencyTrendDelta\(.*?\n\}", self.page, re.DOTALL)
        self.assertIsNotNone(match, "Efficiency charts need a comparable-day delta helper")
        script = match.group(0) + r"""
console.log(JSON.stringify({
  up:efficiencyTrendDelta([
    {metrics:{output_per_dollar:100}}, {metrics:{output_per_dollar:null}},
    {metrics:{output_per_dollar:125}},
  ],'output_per_dollar'),
  down:efficiencyTrendDelta([
    {metrics:{reasoning_ratio:.5}}, {metrics:{reasoning_ratio:.4}},
  ],'reasoning_ratio'),
  unavailable:efficiencyTrendDelta([
    {metrics:{output_per_dollar:0}}, {metrics:{output_per_dollar:25}},
  ],'output_per_dollar'),
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "up": {"text": "↑ 25.0% vs prior covered day", "direction": "up"},
            "down": {"text": "↓ 20.0% vs prior covered day", "direction": "down"},
            "unavailable": {"text": "No comparable prior day", "direction": ""},
        })
        self.assertIn('id=e-output-dollar-change', self.page)
        self.assertIn('id=e-reasoning-change', self.page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_efficiency_charts_smooth_daily_segments_and_default_to_thirty_days(self):
        match = re.search(
            r"function efficiencyChartSmoothPath\(.*?\n\}", self.page, re.DOTALL,
        )
        self.assertIsNotNone(
            match, "Efficiency charts need a smooth path helper for daily segments",
        )
        script = match.group(0) + r"""
const rows=[
  {index:0,value:50}, {index:1,value:30}, {index:2,value:10},
];
console.log(JSON.stringify({
  smooth:efficiencyChartSmoothPath(rows,row=>row.index*10,value=>value),
  pair:efficiencyChartSmoothPath(rows.slice(0,2),row=>row.index*10,value=>value),
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "smooth": "M0.0 50.0 C3.3 43.3 6.7 36.7 10.0 30.0 C13.3 23.3 16.7 16.7 20.0 10.0",
            "pair": "M0.0 50.0 L10.0 30.0",
        })
        self.assertIn("let efficiencyRange=localStorage.getItem('tm_efficiency_range')||'30';", self.page)
        self.assertIn("if(!EFFICIENCY_RANGES.includes(efficiencyRange))efficiencyRange='30';", self.page)
        efficiency_range = self.page.split('id=e-range aria-label="Efficiency history range">', 1)[1].split("</select>", 1)[0]
        self.assertIn('<option value=30 selected>30 days</option>', efficiency_range)
        self.assertNotIn('<option value=7 selected>', efficiency_range)
        self.assertIn(
            "renderTrendDelta('e-reasoning-change',reasoningDelta,'down',!comparison);",
            self.page,
        )

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_efficiency_period_comparison_matches_today_yesterday_and_range_windows(self):
        date_key = re.search(r"function dateKeyAgo\(.*\n", self.page)
        self.assertIsNotNone(date_key, "Efficiency windows need date-key arithmetic")
        functions = ["function localDateKey(date){return String(date.getFullYear())+'-'+String(date.getMonth()+1).padStart(2,'0')+'-'+String(date.getDate()).padStart(2,'0');}", date_key.group(0)]
        for name in (
            "calendarMonthWindow", "monthToDateWindow", "modelRangeWindow",
            "efficiencyComparisonWindows", "efficiencyPeriodDelta",
        ):
            match = re.search(rf"function {name}\(.*?\n\}}", self.page, re.DOTALL)
            self.assertIsNotNone(match, f"Efficiency needs {name} for matched comparisons")
            functions.append(match.group(0))
        script = "\n".join(functions) + r"""
const now=new Date(2026,8,1,15,20,0);
const today=efficiencyComparisonWindows('today',now);
const yesterday=efficiencyComparisonWindows('yesterday',now);
const seven=efficiencyComparisonWindows('7',now);
const lastMonth=efficiencyComparisonWindows('last_month',new Date(2026,2,15,9,0,0));
const month=efficiencyComparisonWindows('month',new Date(2026,2,15,9,0,0));
console.log(JSON.stringify({
  month:{first:month.current.days[0],last:month.current.days.at(-1),count:month.current.days.length,priorFirst:month.prior.days[0],priorLast:month.prior.days.at(-1),label:month.label},
  lastMonth:{first:lastMonth.current.days[0],last:lastMonth.current.days.at(-1),count:lastMonth.current.days.length,priorFirst:lastMonth.prior.days[0],priorLast:lastMonth.prior.days.at(-1),label:lastMonth.label},
  all:efficiencyComparisonWindows('all',now),
  today:{current:today.current.days,prior:today.prior.days,label:today.label},
  yesterday:{current:yesterday.current.days,prior:yesterday.prior.days,label:yesterday.label},
  seven:{current:seven.current.days,prior:seven.prior.days,label:seven.label},
  increase:efficiencyPeriodDelta({output_per_dollar:125},{output_per_dollar:100},'output_per_dollar','yesterday'),
  unavailable:efficiencyPeriodDelta({output_per_dollar:25},{output_per_dollar:0},'output_per_dollar','yesterday'),
  currentMissing:efficiencyPeriodDelta({context_load:null},{context_load:254},'context_load','prior 7 days'),
  priorMissing:efficiencyPeriodDelta({context_load:12},{context_load:null},'context_load','prior 7 days'),
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "month": {
                "first": "2026-03-01", "last": "2026-03-15", "count": 15,
                "priorFirst": "2026-02-01", "priorLast": "2026-02-28",
                "label": "last month",
            },
            "lastMonth": {
                "first": "2026-02-01", "last": "2026-02-28", "count": 28,
                "priorFirst": "2026-01-01", "priorLast": "2026-01-31",
                "label": "the month before",
            },
            "all": None,
            "today": {
                "current": ["2026-09-01"], "prior": ["2026-08-31"],
                "label": "yesterday",
            },
            "yesterday": {
                "current": ["2026-08-31"], "prior": ["2026-08-30"],
                "label": "the day before",
            },
            "seven": {
                "current": ["2026-08-26", "2026-08-27", "2026-08-28", "2026-08-29", "2026-08-30", "2026-08-31", "2026-09-01"],
                "prior": ["2026-08-19", "2026-08-20", "2026-08-21", "2026-08-22", "2026-08-23", "2026-08-24", "2026-08-25"],
                "label": "prior 7 days",
            },
            "increase": {"text": "↑ 25.0% vs yesterday", "direction": "up"},
            "unavailable": {"text": "No comparable prior period", "direction": ""},
            "currentMissing": {"text": "No comparable prior period", "direction": ""},
            "priorMissing": {"text": "No comparable prior period", "direction": ""},
        })

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_efficiency_comparison_badge_hides_without_a_matching_period(self):
        tone_match = re.search(
            r"function trendTone\(.*?\n\}", self.page, re.DOTALL,
        )
        render_match = re.search(
            r"function renderTrendDelta\(.*?\n\}", self.page, re.DOTALL,
        )
        self.assertIsNotNone(tone_match)
        self.assertIsNotNone(render_match)
        script = tone_match.group(0) + "\n" + render_match.group(0) + r"""
const badge={hidden:false,dataset:{},primary:{textContent:''},secondary:{textContent:''},querySelector(selector){return selector==='strong'?this.primary:this.secondary;}};
const $=id=>badge;
renderTrendDelta('e-output-dollar-change',{text:'No comparable prior period',direction:''},'up',true);
console.log(JSON.stringify({hidden:badge.hidden,direction:badge.dataset.direction}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {"hidden": True, "direction": ""})

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_efficiency_comparison_badge_keeps_the_prior_day_relation(self):
        tone_match = re.search(
            r"function trendTone\(.*?\n\}", self.page, re.DOTALL,
        )
        match = re.search(
            r"function renderTrendDelta\(.*?\n\}", self.page, re.DOTALL,
        )
        self.assertIsNotNone(tone_match)
        self.assertIsNotNone(match, "Efficiency badges need one renderer for comparison copy")
        script = tone_match.group(0) + "\n" + match.group(0) + r"""
const badge={dataset:{},primary:{textContent:''},secondary:{textContent:''},querySelector(selector){return selector==='strong'?this.primary:this.secondary;}};
const $=id=>badge;
renderTrendDelta('e-output-dollar-change',{text:'↓ 7.5% vs prior covered day',direction:'down'});
const available={primary:badge.primary.textContent,secondary:badge.secondary.textContent,direction:badge.dataset.direction};
renderTrendDelta('e-output-dollar-change',{text:'No comparable prior day',direction:''});
console.log(JSON.stringify({available,unavailable:{primary:badge.primary.textContent,secondary:badge.secondary.textContent,direction:badge.dataset.direction}}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "available": {
                "primary": "↓ 7.5%",
                "secondary": "vs prior covered day",
                "direction": "down",
            },
            "unavailable": {
                "primary": "No comparable prior day",
                "secondary": "",
                "direction": "",
            },
        })

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_efficiency_comparison_tone_respects_each_metric_direction(self):
        match = re.search(
            r"function trendTone\(.*?\n\}", self.page, re.DOTALL,
        )
        self.assertIsNotNone(match, "Efficiency badges need an explicit metric direction rule")
        script = match.group(0) + r"""
console.log(JSON.stringify({
  outputUp:trendTone({direction:'up'},'up'),
  outputDown:trendTone({direction:'down'},'up'),
  contextDown:trendTone({direction:'down'},'down'),
  contextUp:trendTone({direction:'up'},'down'),
  unavailable:trendTone({direction:''},'up'),
  neutral:trendTone({direction:'up'},''),
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "outputUp": "improving",
            "outputDown": "degrading",
            "contextDown": "improving",
            "contextUp": "degrading",
            "unavailable": "",
            "neutral": "",
        })

    def test_efficiency_comparisons_sit_beside_each_value_without_a_box(self):
        efficiency = self.page.split("<div class=view id=view-efficiency>", 1)[1].split(
            "<div class=view id=view-learn>", 1,
        )[0]
        for value_id, comparison_id in (
            ("e-output-dollar", "e-output-dollar-change"),
            ("e-reasoning-ratio", "e-reasoning-change"),
            ("e-context-load", "e-context-load-change"),
            ("e-cache-hit", "e-cache-hit-change"),
        ):
            self.assertRegex(
                efficiency,
                rf'<div class="v mono" id={value_id}>.*?</div><div class=trendDelta id={comparison_id}[^>]*>',
            )
        self.assertEqual(efficiency.count('valueWithDelta"><div class="v mono"'), 4)
        self.assertIn(":where(.valueWithDelta){display:flex;flex-wrap:wrap;align-items:center", self.page)
        self.assertIn(":where(.trendDelta>strong){font-family:var(--font-mono);font-size:var(--fs-20)", self.page)
        self.assertNotIn("efficiencySupportDelta", self.page)
        self.assertNotRegex(self.page, r"\.efficiencyTrendDelta[^{]*\{")
        self.assertIn(":where(.trendDelta){display:grid", self.page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_efficiency_chart_presentation_labels_each_metric(self):
        match = re.search(
            r"function efficiencyChartMetricPresentation\(.*?\n\}", self.page,
            re.DOTALL,
        )
        self.assertIsNotNone(
            match, "Efficiency charts need metric-specific visible labels",
        )
        script = r"""
function compactNumber(value){return value>=1000?(value/1000).toFixed(1).replace(/\.0$/,'')+'K':String(value);}
function pct(value){return (value*100).toFixed(1)+'%';}
function efficiencyDecimal(value){return String(value)+'x';}
""" + match.group(0) + r"""
console.log(JSON.stringify({
  dollar:efficiencyChartMetricPresentation(5200,'output_per_dollar'),
  reasoning:efficiencyChartMetricPresentation(.34,'reasoning_ratio'),
  context:efficiencyChartMetricPresentation(256,'context_load'),
  cache:efficiencyChartMetricPresentation(.87,'cache_hit_ratio'),
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "dollar": {"axis": "5.2K", "detail": "5,200 output tokens / $"},
            "reasoning": {"axis": "34.0%", "detail": "34.0% reasoning"},
            "context": {"axis": "256x", "detail": "256x context / output"},
            "cache": {"axis": "87.0%", "detail": "87.0% input from cache"},
        })

    def test_efficiency_support_cards_include_compact_daily_charts(self):
        efficiency = self.page.split("<div class=view id=view-efficiency>", 1)[1].split(
            "<div class=view id=view-learn>", 1,
        )[0]
        for metric, label in (
            ("context-load", "Daily context load"),
            ("cache-hit", "Daily cache hit ratio"),
        ):
            self.assertIn(
                f'id=e-{metric}-chart viewBox="0 0 520 96" preserveAspectRatio=none role=img tabindex=0 aria-label="{label}"',
                efficiency,
            )
        self.assertIn(".efficiencySupportItem .efficiencyChart{height:56px}", self.page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_efficiency_range_aggregation_keeps_only_efforts_in_range(self):
        match = re.search(
            r"function aggregateModelDays\(rows\)\{.*?\n\}", self.page, re.DOTALL,
        )
        self.assertIsNotNone(match, "Efficiency needs range-scoped model aggregation")
        script = r"""
const MODEL_COUNTER_KEYS=[],MODEL_ARRAY_KEYS=[];
const metricAvailable=()=>false,metricPartial=()=>false,hasLocalEstimate=()=>false;
const usageBasis=()=> 'reported',modelWaitDistribution=()=>({median_wait_s:0,p95_wait_s:0});
const modelMedian=()=>0;
""" + match.group(0) + r"""
console.log(JSON.stringify(aggregateModelDays([
  {reasoning_efforts:['high']}, {reasoning_efforts:['xhigh', 'high']},
]).reasoning_efforts));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), ["high", "xhigh"])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_efficiency_chart_inspection_reports_exact_values_and_missing_days(self):
        index_match = re.search(
            r"function efficiencyChartPointerIndex\(.*?\n\}", self.page, re.DOTALL,
        )
        detail_match = re.search(
            r"function efficiencyChartInspection\(.*?\n\}", self.page, re.DOTALL,
        )
        presentation_match = re.search(
            r"function efficiencyChartMetricPresentation\(.*?\n\}", self.page,
            re.DOTALL,
        )
        self.assertIsNotNone(index_match)
        self.assertIsNotNone(detail_match)
        self.assertIsNotNone(presentation_match)
        script = "\n".join((
            "function compactNumber(value){return String(Math.round(value));}",
            "function pct(value){return (value*100).toFixed(1)+'%';}",
            "function efficiencyDecimal(value){return String(value)+'x';}",
            index_match.group(0), presentation_match.group(0), detail_match.group(0), r"""
const samples=[
  {day:'2026-08-25',index:0,value:5143.4},
  {day:'2026-08-26',index:1,value:null},
  {day:'2026-08-27',index:2,value:.3421},
];
const rect={left:100,width:520};
console.log(JSON.stringify({
  first:efficiencyChartPointerIndex(109,rect,3),
  middle:efficiencyChartPointerIndex(360,rect,3),
  last:efficiencyChartPointerIndex(611,rect,3),
  output:efficiencyChartInspection(samples[0],'output_per_dollar'),
  missing:efficiencyChartInspection(samples[1],'output_per_dollar'),
  reasoning:efficiencyChartInspection(samples[2],'reasoning_ratio'),
}));
""",
        ))
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "first": 0,
            "middle": 1,
            "last": 2,
            "output": {
                "date": "Aug 25",
                "value": "5,143 output tokens / $",
            },
            "missing": {"date": "Aug 26", "value": "No data"},
            "reasoning": {"date": "Aug 27", "value": "34.2% reasoning"},
        })

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_efficiency_chart_hover_survives_live_refresh(self):
        names = (
            "efficiencyChartPointerIndex",
            "efficiencyChartMetricPresentation",
            "efficiencyChartInspection",
            "efficiencyChartAnnouncement",
            "chartGapRuns",
            "chartGapBridge",
            "efficiencyChartSmoothPath",
            "renderEfficiencyChart",
        )
        functions = []
        for name in names:
            match = re.search(
                rf"function {name}\(.*?\n\}}", self.page, re.DOTALL,
            )
            self.assertIsNotNone(match)
            functions.append(match.group(0))
        script = "\n".join((*functions, r"""
class AttrNode {
  constructor(){this.attrs={};}
  setAttribute(name,value){this.attrs[name]=String(value);}
}
class InspectNode extends AttrNode {
  constructor(){super();this.line=new AttrNode();this.dot=new AttrNode();}
  querySelector(selector){return selector==='line'?this.line:this.dot;}
}
class SvgNode extends AttrNode {
  constructor(parent){super();this.parentElement=parent;this.dataset={};this.hovered=false;this.inspect=null;}
  set innerHTML(value){this.html=value;this.inspect=new InspectNode();}
  get innerHTML(){return this.html;}
  querySelector(selector){return selector==='.efficiencyChartInspect'?this.inspect:null;}
  getBoundingClientRect(){return {left:0,width:520};}
  matches(selector){return selector===':hover'&&this.hovered;}
}
const tip={hidden:true,textContent:'',style:{}},status={textContent:''};
const parent={querySelector(selector){return selector==='.efficiencyChartTip'?tip:status;}};
const svg=new SvgNode(parent),$=()=>svg,esc=value=>String(value);
const pct=value=>`${Math.round(value*100)}%`,compactNumber=value=>String(Math.round(value));
const trend=[
  {day:'2026-08-25',metrics:{output_per_dollar:5143.4}},
  {day:'2026-08-26',metrics:{output_per_dollar:7777}},
  {day:'2026-08-27',metrics:{output_per_dollar:9123}},
];
renderEfficiencyChart('chart',trend,'output_per_dollar','Output per covered dollar');
svg.hovered=true;
svg.onpointermove({clientX:260});
const first={hidden:tip.hidden,text:tip.textContent,ratio:svg.dataset.efficiencyHoverRatio};
renderEfficiencyChart('chart',trend,'output_per_dollar','Output per covered dollar');
const rerender={hidden:tip.hidden,text:tip.textContent,visibility:svg.inspect.attrs.visibility};
svg.onpointermove({clientX:510});
const moved={hidden:tip.hidden,text:tip.textContent};
svg.hovered=false;
svg.onpointerleave();
const left={hidden:tip.hidden,ratio:svg.dataset.efficiencyHoverRatio||null,visibility:svg.inspect.attrs.visibility};
svg.hovered=true;
svg.dataset.efficiencyHoverRatio='0.5';
renderEfficiencyChart('chart',[],'output_per_dollar','Output per covered dollar');
const empty={hidden:tip.hidden,ratio:svg.dataset.efficiencyHoverRatio||null};
console.log(JSON.stringify({first,rerender,moved,left,empty}));
"""))
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "first": {
                "hidden": False,
                "text": "Aug 26 · 7,777 output tokens / $",
                "ratio": "0.5",
            },
            "rerender": {
                "hidden": False,
                "text": "Aug 26 · 7,777 output tokens / $",
                "visibility": "visible",
            },
            "moved": {
                "hidden": False,
                "text": "Aug 27 · 9,123 output tokens / $",
            },
            "left": {"hidden": True, "ratio": None, "visibility": "hidden"},
            "empty": {"hidden": True, "ratio": None},
        })

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_efficiency_chart_inspection_is_keyboard_accessible(self):
        match = re.search(
            r"function efficiencyChartKeyIndex\(.*?\n\}", self.page, re.DOTALL,
        )
        self.assertIsNotNone(match)
        script = match.group(0) + r"""
console.log(JSON.stringify({
  left:efficiencyChartKeyIndex('ArrowLeft',2,5),
  leftBound:efficiencyChartKeyIndex('ArrowLeft',0,5),
  right:efficiencyChartKeyIndex('ArrowRight',2,5),
  rightBound:efficiencyChartKeyIndex('ArrowRight',4,5),
  home:efficiencyChartKeyIndex('Home',3,5),
  end:efficiencyChartKeyIndex('End',1,5),
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "left": 1,
            "leftBound": 0,
            "right": 3,
            "rightBound": 4,
            "home": 0,
            "end": 4,
        })
        for marker in (
            "tabindex=0",
            "aria-live=polite",
            "svg.onfocus",
            "svg.onkeydown",
            "svg.onblur",
            "Use Left and Right arrow keys to inspect daily values.",
        ):
            self.assertIn(marker, self.page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_efficiency_chart_accessibility_uses_one_fresh_announcement_path(self):
        match = re.search(
            r"function efficiencyChartAnnouncement\(.*?\n\}", self.page, re.DOTALL,
        )
        self.assertIsNotNone(match)
        script = match.group(0) + r"""
const detail={date:'Aug 26',value:'No data'};
console.log(JSON.stringify({
  keyboard:efficiencyChartAnnouncement(detail,true),
  pointer:efficiencyChartAnnouncement(detail,false),
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "keyboard": "Aug 26 · No data",
            "pointer": "",
        })
        self.assertNotIn("aria-describedby=e-output-dollar-status", self.page)
        self.assertNotIn("aria-describedby=e-reasoning-status", self.page)
        self.assertIn(
            "status.textContent=efficiencyChartAnnouncement(detail,announce)",
            self.page,
        )

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_efficiency_reasoning_cell_falls_back_to_measured_thinking_share(self):
        match = re.search(
            r"function efficiencyReasoningPresentation\(.*?\n\}",
            self.page,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        script = r"""
const f=value=>Math.round(value||0).toLocaleString('en-US');
const pct=value=>Math.round((value||0)*100)+'%';
""" + match.group(0) + r"""
console.log(JSON.stringify({
  exact:efficiencyReasoningPresentation(
    {executions:10,reasoning_executions:8,thinking_executions:8},
    {reasoning_ratio:.375}
  ),
  thinking:efficiencyReasoningPresentation(
    {executions:960,reasoning_executions:0,thinking_executions:402},
    {reasoning_ratio:null}
  ),
  missing:efficiencyReasoningPresentation(
    {executions:4,reasoning_executions:0,thinking_executions:0},
    {reasoning_ratio:null}
  ),
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "exact": {"value": "38%", "note": "8 / 10 exec"},
            "thinking": {
                "value": "42% thinking execs",
                "note": "token ratio unavailable",
            },
            "missing": {"value": "--", "note": "unavailable"},
        })

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_session_efficiency_presentation_preserves_coverage_and_thinking_fallback(self):
        metric_match = re.search(
            r"function efficiencyMetrics\(row\)\{.*?\n\}", self.page, re.DOTALL,
        )
        reasoning_match = re.search(
            r"function efficiencyReasoningPresentation\(.*?\n\}",
            self.page,
            re.DOTALL,
        )
        summary_match = re.search(
            r"function efficiencySummaryPresentation\(.*?\n\}",
            self.page,
            re.DOTALL,
        )
        self.assertIsNotNone(metric_match)
        self.assertIsNotNone(reasoning_match)
        self.assertIsNotNone(summary_match)
        script = r"""
const f=value=>Math.round(value||0).toLocaleString('en-US');
const pct=value=>Math.round((value||0)*100)+'%';
const compactNumber=value=>Math.round(value||0).toLocaleString('en-US');
const hasLocalEstimate=row=>Boolean(row?.estimated);
""" + "\n".join((
            metric_match.group(0), reasoning_match.group(0), summary_match.group(0),
        )) + r"""
const exact=efficiencySummaryPresentation({
  output_tokens:200,cost:2,executions:10,token_covered_executions:10,
  cost_covered_executions:10,cost_covered_output_tokens:200,cost_covered_cost:2,
  reasoning_tokens:30,reasoning_output_tokens:80,reasoning_executions:8,
  thinking_executions:8,availability:{tokens:true,output_tokens:true,cost:true,reasoning_tokens:true},
  coverage:{cost:{complete:true}},
});
const thinking=efficiencySummaryPresentation({
  output_tokens:100,cost:1,executions:10,token_covered_executions:10,
  cost_covered_executions:8,cost_covered_output_tokens:80,cost_covered_cost:1,
  reasoning_executions:0,thinking_executions:4,
  availability:{tokens:true,output_tokens:true,cost:true,reasoning_tokens:false},
  coverage:{cost:{complete:false}},estimated:true,
});
const missing=efficiencySummaryPresentation({
  executions:2,availability:{tokens:false,output_tokens:false,cost:false,reasoning_tokens:false},
});
console.log(JSON.stringify({exact,thinking,missing}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "exact": {
                "output": {"value": "100 / $", "note": "covered"},
                "reasoning": {"value": "38%", "note": "8 / 10 exec"},
            },
            "thinking": {
                "output": {"value": "80 / $", "note": "partial · local est"},
                "reasoning": {
                    "value": "40% thinking execs",
                    "note": "token ratio unavailable · local est",
                },
            },
            "missing": {
                "output": {"value": "--", "note": "unavailable"},
                "reasoning": {"value": "--", "note": "unavailable"},
            },
        })

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_efficiency_only_marks_thinking_token_splits_unavailable_when_uncovered(self):
        match = re.search(
            r"function thinkingUnavailableExecutions\(row\)\{.*?\n\}",
            self.page,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        script = match.group(0) + r"""
console.log(JSON.stringify({
  unavailable:thinkingUnavailableExecutions({thinking_executions:2,thinking_covered_executions:0,reasoning_executions:0}),
  partial:thinkingUnavailableExecutions({thinking_executions:3,thinking_covered_executions:1,reasoning_executions:1}),
  exact:thinkingUnavailableExecutions({thinking_executions:1,thinking_covered_executions:1,reasoning_executions:1}),
  unrelated:thinkingUnavailableExecutions({thinking_executions:1,thinking_covered_executions:0,reasoning_executions:4}),
  codexOnly:thinkingUnavailableExecutions({thinking_executions:0,thinking_covered_executions:0,reasoning_executions:4}),
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "unavailable": 2,
            "partial": 2,
            "exact": 0,
            "unrelated": 1,
            "codexOnly": 0,
        })

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_efficiency_model_table_defaults_to_spend_descending_and_sorts_nulls_last(self):
        metric_match = re.search(
            r"function efficiencyMetrics\(row\)\{.*?\n\}", self.page, re.DOTALL,
        )
        value_match = re.search(
            r"function efficiencySortValue\(row,key\)\{.*?\n\}", self.page, re.DOTALL,
        )
        sort_match = re.search(
            r"function sortEfficiencyRows\(rows,sort=efficiencySort\)\{.*?\n\}",
            self.page, re.DOTALL,
        )
        self.assertIsNotNone(metric_match)
        self.assertIsNotNone(value_match)
        self.assertIsNotNone(sort_match)
        script = "\n".join((
            metric_match.group(0),
            "const modelRuntimeLabel=row=>`${row.model} · ${row.runtime}`;",
            "const efficiencySort={key:'cost',direction:'desc'};",
            value_match.group(0), sort_match.group(0),
            r"""
const rows=[
  {model:'low',runtime:'Claude Code',window:{cost:1,availability:{cost:true}}},
  {model:'missing',runtime:'Claude Code',window:{cost:0,availability:{cost:false}}},
  {model:'high',runtime:'Codex',window:{cost:4,availability:{cost:true}}},
];
console.log(JSON.stringify({
  spend:sortEfficiencyRows(rows).map(row=>row.model),
  efficiency:sortEfficiencyRows(rows,{key:'output_per_dollar',direction:'desc'}).map(row=>row.model),
}));
""",
        ))
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        payload = json.loads(result.stdout)
        self.assertEqual(payload["spend"], ["high", "low", "missing"])
        self.assertEqual(payload["efficiency"][-1], "missing")

    def test_efficiency_model_filter_stacks_above_its_chart(self):
        self.assertIn(
            ".spectrumPageHeadFrame:has(.modelControls){z-index:10}",
            self.page,
        )


if __name__ == "__main__":
    unittest.main()
