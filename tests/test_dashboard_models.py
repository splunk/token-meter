import unittest
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from tests.dashboard_page import DASHBOARD_PAGE
from tests.dashboard_layout_support import DashboardLayoutCase


class DashboardModelsLayoutTests(DashboardLayoutCase):
    def test_model_stats_is_a_first_class_top_level_route(self):
        for marker in ("id=tab-models", "id=view-models", "id=m-speed", "id=m-chart",
                       "id=m-table", "renderModelStats", "aggregateModelDays"):
            self.assertIn(marker, self.page)
        self.assertRegex(self.page, r"id=tab-models[^>]*>.*?<span class=tabLabel>Models</span>")
        self.assertIn("if(routedSessionId()&&history.pushState)", self.page)
        self.assertIn("$('tab-models').onclick=()=>openTopLevelRoute('models')", self.page)
        self.assertIn("if(h==='models'||h==='frustration')", self.page)
        self.assertLess(self.page.index("id=tab-session"), self.page.index("id=tab-models"))
        self.assertNotIn("Timing evidence", self.page)
        diagnostics = self.page.split("id=m-diagnostics", 1)[1].split("</details>", 1)[0]
        self.assertIn("cross-runtime output pace remains diagnostic rather than a model verdict.", diagnostics)
        self.assertIn("<summary><span>Speed &amp; timing diagnostics</span></summary>", diagnostics)
        self.assertIn("<tr><td colspan=10><div class=\"modelEmpty emptyState\">No model activity in this window</div>", self.page)

    def test_models_page_is_a_ranked_spend_leaderboard(self):
        models = self.page.split("id=view-models", 1)[1].split("id=view-daily", 1)[0]
        order = [models.index(marker) for marker in (
            "id=m-spend", "id=m-mix", "id=m-speedchart", "id=m-table", "id=m-diagnostics",
        )]
        self.assertEqual(order, sorted(order))
        for marker in (
            "Where your spend goes", "Model mix over time", "data-model-mix=cost",
            "data-model-mix=output", "data-model-mix=executions",
            "Speed &amp; timing diagnostics",
        ):
            self.assertIn(marker, models)
        diagnostics = models.split("id=m-diagnostics", 1)[1]
        speed_block = diagnostics.split("id=m-change-label", 1)[0]
        self.assertIn("id=m-speed-label", speed_block)
        self.assertIn("cross-runtime output pace remains diagnostic rather than a model verdict.", speed_block)
        for marker in ("id=m-speed", "id=m-change", "id=m-wait", "id=m-chart", "id=m-metric"):
            self.assertIn(marker, diagnostics)
        self.assertNotIn("id=m-input", models)
        self.assertNotIn("Logs (all)", models)
        table_head = models.split("id=m-table><thead>", 1)[1].split("</thead>", 1)[0]
        self.assertEqual(table_head.count("<th"), 10)
        self.assertEqual(
            [key for key in ("model", "cost", "cost_per_exec", "cache", "executions", "output", "speed", "wait", "pushback")
             if f"data-model-sort={key} data-tip=" in table_head],
            ["model", "cost", "cost_per_exec", "cache", "executions", "output", "speed", "wait", "pushback"],
        )
        self.assertIn("does not change with the History filter", table_head)
        self.assertNotIn("<span class=\"fieldtip modelHelp\" tabindex=0", table_head)
        self.assertEqual(table_head.count('<button class="modelSortBtn fieldtip modelHelp" type=button'), 9)
        for marker in (
            "const MODEL_SORT_KEYS=['model','cost','cost_per_exec','cache','executions','output','speed','wait','pushback'];",
            "localStorage.setItem('tm_model_speed_models',JSON.stringify(modelSpeedSelection))",
            "localStorage.removeItem('tm_model_speed_models')",
            "renderModelSpeedChart(groups,ranking,top,rangeWindow);",
            "localStorage.setItem('tm_model_sort',JSON.stringify(modelSort))",
            "localStorage.setItem('tm_model_mix_metric',metric)",
            "localStorage.setItem('tm_model_diagnostics_open'",
            "without cost data ${uncosted===1?'is':'are'} excluded, not counted as $0.",
            "th.setAttribute('aria-sort'",
            "aria-expanded=${expanded}",
            "data-model-clear",
            "setLogHtml($('m-table').querySelector('tbody'),",
            "const focus=modelFocusKey();", "restoreModelFocus(focus);",
            "perExecPartial?'*':''",
            '<div class=modelSpendBar id=m-spend-bar role=group',
        ):
            self.assertIn(marker, self.page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_models_leaderboard_groups_variants_and_never_counts_missing_cost_as_zero(self):
        script = f"""
const fs=require('fs');
const page=fs.readFileSync({json.dumps(str(Path(DASHBOARD_PAGE)))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
function constLine(name){{const start=page.indexOf(`const ${{name}}=`);if(start<0)throw Error(`missing ${{name}}`);return page.slice(start,page.indexOf('\\n',start));}}
['MODEL_COUNTER_KEYS','MODEL_ARRAY_KEYS','metricAvailable','metricPartial','usageProvenance','usageBasis','hasLocalEstimate'].forEach(name=>eval(constLine(name).replace(/^const /,'globalThis.')));
['modelWaitDistribution','modelMedian','modelDayInRange','aggregateModelDays','efficiencyMetrics','modelGroupKey','modelGroupRows','modelBoardValue','sortModelBoard','modelSpendRanking'].forEach(name=>eval(`globalThis.${{name}}=${{extract(name)}}`));
globalThis.modelSort={{key:'cost',direction:'desc'}};
const day=(d,extra)=>({{day:d,availability:{{tokens:true,cost:true,cache:true}},executions:10,output_tokens:100,input_tokens:1000,cache_read_tokens:800,cache_covered_input_tokens:1000,wait_samples:1,wait_seconds:5,wait_durations_s:[5],...extra}});
const models=[
 {{id:'sol::Codex::high',model:'sol',runtime:'Codex',reasoning_effort:'high',logs:3,daily:[day('2026-09-30',{{cost:6}}),day('2026-01-01',{{cost:100}})]}},
 {{id:'sol::Codex::xhigh',model:'sol',runtime:'Codex',reasoning_effort:'xhigh',logs:2,daily:[day('2026-09-30',{{cost:4}})]}},
 {{id:'sol::Cursor',model:'sol',runtime:'Cursor',logs:1,daily:[day('2026-09-30',{{cost:0,availability:{{tokens:true,cost:false}}}})]}},
 {{id:'opus::Claude-3P',model:'opus',runtime:'Claude-3P',logs:5,daily:[day('2026-09-30',{{cost:7,executions:2}})]}},
 {{id:'part::Codex',model:'part',runtime:'Codex',logs:1,daily:[day('2026-09-30',{{cost:6,executions:10,cost_covered_executions:6,cost_covered_cost:6}})]}},
 {{id:'big::Cursor',model:'big',runtime:'Cursor',logs:1,daily:[day('2026-09-30',{{output_tokens:999999,availability:{{tokens:true,cost:false}}}})]}},
];
const window={{days:['2026-09-30'],daySet:new Set(['2026-09-30'])}};
const groups=modelGroupRows(models,window),byKey=Object.fromEntries(groups.map(g=>[g.key,g]));
const ranking=modelSpendRanking(groups);
process.stdout.write(JSON.stringify({{
 keys:groups.map(g=>g.key).sort(),
 solVariants:byKey['sol::Codex'].variants.length,
 solCost:modelBoardValue(byKey['sol::Codex'],'cost'),
 cursorCost:modelBoardValue(byKey['sol::Cursor'],'cost'),
 opusPerExec:modelBoardValue(byKey['opus::Claude-3P'],'cost_per_exec'),
 partialPerExec:modelBoardValue(byKey['part::Codex'],'cost_per_exec'),
 speedNone:modelBoardValue(byKey['sol::Codex'],'speed'),
 solCache:modelBoardValue(byKey['sol::Codex'],'cache'),
 ranking:ranking.map(g=>g.key),
 byExecAsc:sortModelBoard(groups,{{key:'executions',direction:'asc'}}).map(g=>g.key),
 byCostAsc:sortModelBoard(groups,{{key:'cost',direction:'asc'}}).map(g=>g.key),
}}));
"""
        payload = json.loads(subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        ).stdout)
        self.assertEqual(payload["keys"], ["big::Cursor", "opus::Claude-3P", "part::Codex", "sol::Codex", "sol::Cursor"])
        self.assertEqual(payload["solVariants"], 2)
        self.assertEqual(payload["solCost"], 10)
        self.assertIsNone(payload["cursorCost"])
        self.assertEqual(payload["opusPerExec"], 3.5)
        self.assertAlmostEqual(payload["solCache"], 0.8)
        self.assertEqual(payload["partialPerExec"], 1.0)
        self.assertIsNone(payload["speedNone"])
        self.assertEqual(payload["ranking"], ["sol::Codex", "opus::Claude-3P", "part::Codex", "big::Cursor", "sol::Cursor"])
        self.assertEqual(payload["byExecAsc"][0], "opus::Claude-3P")
        self.assertEqual(set(payload["byCostAsc"][-2:]), {"sol::Cursor", "big::Cursor"})

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_models_speed_series_is_time_weighted_smoothed_and_defaults_to_timed_top_five(self):
        script = f"""
const fs=require('fs');
const page=fs.readFileSync({json.dumps(str(Path(DASHBOARD_PAGE)))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
['localDateKey','modelCalendarDays','modelSpeedBasis','modelSpeedSeries','modelSpeedDefaultKeys','modelSmoothPath'].forEach(name=>eval(`globalThis.${{name}}=${{extract(name)}}`));
const days=['d1','d2','d3','d4','d5'];
const daily=[
 {{day:'d1',timed_output_tokens:100,timed_seconds:10,timed_samples:1}},
 {{day:'d2',timed_output_tokens:900,timed_seconds:30,timed_samples:3}},
 {{day:'d4',timed_output_tokens:50,timed_seconds:5,timed_samples:1,tool_free_output_tokens:40,tool_free_seconds:2,tool_free_samples:1}},
 {{day:'d5',timed_output_tokens:0,timed_seconds:0,timed_samples:0}},
];
const series=modelSpeedSeries(daily,days,'timed');
const toolFree=modelSpeedSeries(daily,days,'tool_free');
const ranking=[1,2,3,4,5,6,7].map(n=>({{key:'g'+n,window:{{available:n!==2}}}}));
const path=modelSmoothPath([[0,10],[50,20],[100,15]]);
process.stdout.write(JSON.stringify({{
 series,toolFree,
 basisTimed:modelSpeedBasis({{tool_free_samples:0,tool_free_seconds:0}}),
 basisTool:modelSpeedBasis({{tool_free_samples:2,tool_free_seconds:4}}),
 defaults:modelSpeedDefaultKeys(ranking),
 path,single:modelSmoothPath([[0,1]]),
 calendar:modelCalendarDays(['2026-05-03','2026-05-01','bad','2026-05-01']),
 sparse:modelSpeedSeries([{{day:'2026-05-01',timed_output_tokens:100,timed_seconds:10}},{{day:'2026-05-03',timed_output_tokens:900,timed_seconds:30}}],modelCalendarDays(['2026-05-01','2026-05-03']),'timed'),
}}));
"""
        payload = json.loads(subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        ).stdout)
        self.assertEqual([point["day"] for point in payload["series"]], ["d1", "d2", "d4"])
        d1, d2, d4 = payload["series"]
        self.assertAlmostEqual(d1["value"], 1000 / 40)
        self.assertAlmostEqual(d2["value"], 1000 / 40)
        self.assertAlmostEqual(d4["value"], 50 / 5)
        self.assertAlmostEqual(d2["raw"], 30)
        self.assertEqual(d2["samples"], 3)
        self.assertEqual([point["day"] for point in payload["toolFree"]], ["d4"])
        self.assertAlmostEqual(payload["toolFree"][0]["value"], 20)
        self.assertEqual(payload["basisTimed"], "timed")
        self.assertEqual(payload["basisTool"], "tool_free")
        self.assertEqual(payload["defaults"], ["g1", "g3", "g4", "g5", "g6"])
        self.assertTrue(payload["path"].startswith("M0.0,10.0 C"))
        self.assertEqual(payload["path"].count(" C"), 2)
        self.assertEqual(payload["single"], "")
        self.assertEqual(payload["calendar"], ["2026-05-01", "2026-05-02", "2026-05-03"])
        self.assertEqual([round(point["value"], 6) for point in payload["sparse"]], [10.0, 30.0])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_chart_gap_runs_split_daily_points_and_bridge_only_interior_gaps(self):
        payload = self.run_chart_gap_harness(r"""
load(['chartGapRuns','chartGapBridge']);
const runs=chartGapRuns([{index:1},{index:2},{index:4},{index:6},{index:7}]);
process.stdout.write(JSON.stringify({
 segments:runs.segments.map(run=>run.map(point=>point.index)),
 bridges:runs.bridges.map(pair=>pair.map(point=>point.index)),
 edges:chartGapRuns([{index:3},{index:4}]),
 empty:chartGapRuns([]),
 bridge:chartGapBridge(1,2.25,30,40),
}));
""")
        self.assertEqual(payload["segments"], [[1, 2], [4], [6, 7]])
        self.assertEqual(payload["bridges"], [[2, 4], [4, 6]])
        self.assertEqual(payload["edges"]["bridges"], [])
        self.assertEqual(len(payload["edges"]["segments"]), 1)
        self.assertEqual(payload["empty"], {"segments": [], "bridges": []})
        self.assertEqual(
            payload["bridge"],
            '<line class=chartGapBridge x1="1.0" y1="2.3" x2="30.0" y2="40.0"/>',
        )
        self.assertIn(":where(.chartGapBridge){", self.page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_model_speed_and_trend_charts_bridge_gaps_instead_of_smoothing_through_them(self):
        payload = self.run_chart_gap_harness(r"""
load(['chartGapRuns','chartGapBridge','modelSpeedSeries','modelSpeedBasis','modelSpeedDefaultKeys','modelSmoothPath','renderModelSpeedChart','modelTrendRowHasUsage','modelTrendPaint','modelTokensPerExecution','drawModelTrend']);
eval(page.slice(page.indexOf('const metricAvailable='),page.indexOf('\n',page.indexOf('const metricAvailable='))).replace(/^const /,'globalThis.'));
const nodes={};
function node(id){return nodes[id]||(nodes[id]={id,style:{},dataset:{},textContent:'',innerHTML:'',offsetWidth:100,offsetHeight:40,setAttribute(){},parentElement:{getBoundingClientRect(){return {left:0,top:0,width:1040};}},querySelectorAll:()=>[]});}
Object.assign(globalThis,{$:node,esc:value=>String(value),f:value=>String(value),countWord:(n,word)=>word,setLogHtml(){},speedFmt:value=>value.toFixed(1),waitFmt:String,compactNumber:value=>String(Math.round(value)),modelScopeLabel:()=>'all',modelRangeLabel:()=>'8 days',modelRange:'8',modelSpeedSelection:null,MODEL_SPEED_MAX_SERIES:8,MODEL_SPEED_EXTRA_COLORS:['c2'],modelRankedColors:top=>new Map(top.map(group=>[group.key,'c1'])),CHART:{lbl:'lbl',grid:'grid',ink:'ink'},svgTextPx:()=>12,MODEL_OUTPUT_BAR_FILL:'bar',MODEL_OUTPUT_BAR_STROKE:'barStroke',modelTrendMetric:'speed'});
const start=page.indexOf('const MODEL_TREND_METRICS=');eval('globalThis.MODEL_TREND_METRICS='+page.slice(start+'const MODEL_TREND_METRICS='.length,page.indexOf('\n};',start)+3));
const days=['d0','d1','d2','d3','d4','d5','d6','d7'],timed=[1,2,4,6];
const daily=timed.map(index=>({day:days[index],timed_output_tokens:100*(index+1),timed_seconds:10,timed_samples:1}));
const group={key:'g',model:'m',runtime:'Codex',estimated:false,daily,window:{available:true,output_tps:10}};
const speedHits=[];node('m-speedchart-svg').querySelectorAll=()=>days.map((_,index)=>{const hit={dataset:{speedDay:String(index)}};speedHits.push(hit);return hit;});
renderModelSpeedChart([group],[group],[group],{days});
const speed=node('m-speedchart-svg').innerHTML,speedTip=node('m-speedchart-tip');
speedHits[3].onpointerenter({clientX:10,clientY:10});const speedGapTip=speedTip.style.display==='block'?speedTip.innerHTML:'hidden';
const trendHits=[];node('m-chart').querySelectorAll=()=>days.map(()=>{const hit={};trendHits.push(hit);return hit;});
const point=index=>timed.includes(index)?{day:days[index],executions:1,output_tokens:100,availability:{tokens:true},perf:{available:true,output_tps:index+1}}:{day:days[index],perf:{available:false}};
drawModelTrend({days,series:[{id:'g',model:'m · Codex',color:'c1',estimated:false,points:days.map((_,index)=>point(index))}]});
const trend=node('m-chart').innerHTML,trendTip=node('m-chart-tip');
trendHits[0].onpointerenter({clientX:10,clientY:10});const trendGapTip=trendTip.style.display==='block'?trendTip.innerHTML:'hidden';
process.stdout.write(JSON.stringify({
 speedBridges:attrs(speed,'line','chartGapBridge').length,
 speedCurves:(speed.match(/<path d="M[^"]*"/g)||[]).map(path=>(path.match(/ C/g)||[]).length),
 speedDots:(speed.match(/<circle /g)||[]).length,
 speedGapTip,
 trendBridges:attrs(trend,'line','chartGapBridge').length,
 trendLines:(trend.match(/<path d="M[^"]*"/g)||[]).map(path=>(path.match(/ L/g)||[]).length),
 trendDots:(trend.match(/<circle /g)||[]).length,
 trendGapTip,
}));
""")
        self.assertEqual(payload["speedBridges"], 2)
        self.assertEqual(payload["speedCurves"], [1], "only the d1-d2 run is a solid curve")
        self.assertEqual(payload["speedDots"], 2, "isolated d4 and d6 render as dots")
        self.assertIn("No data", payload["speedGapTip"])
        self.assertIn("d3", payload["speedGapTip"])
        self.assertEqual(payload["trendBridges"], 2)
        self.assertEqual(payload["trendLines"], [1])
        self.assertEqual(payload["trendDots"], 4)
        self.assertIn("No data", payload["trendGapTip"])
        self.assertIn("d0", payload["trendGapTip"])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_models_focus_restore_keeps_the_focused_control_and_scopes_to_its_container(self):
        script = f"""
const fs=require('fs');
const page=fs.readFileSync({json.dumps(str(Path(DASHBOARD_PAGE)))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
const start=page.indexOf('const MODEL_FOCUS_KEYS=');eval('globalThis.MODEL_FOCUS_KEYS='+page.slice(start+'const MODEL_FOCUS_KEYS='.length,page.indexOf(';',start)));
eval('globalThis.modelFocusKey='+extract('modelFocusKey'));eval('globalThis.restoreModelFocus='+extract('restoreModelFocus'));
function node(id,dataset,parent){{const el={{id,dataset:dataset||{{}},parent,children:[],isConnected:true,label:id,
 contains(other){{for(let cur=other;cur;cur=cur.parent)if(cur===this)return true;return false;}},
 closest(sel){{const ids=sel.split(',').map(x=>x.trim().slice(1));for(let cur=this;cur;cur=cur.parent)if(ids.includes(cur.id))return cur;return null;}},
 querySelectorAll(sel){{const attr=sel.slice(1,-1),key=attr.slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase());const out=[];const walk=n=>{{n.children.forEach(c=>{{if(key in c.dataset)out.push(c);walk(c);}});}};walk(this);return out;}},
 focus(){{document.activeElement=this;}}}};if(parent)parent.children.push(el);byId[id]=el;return el;}}
const byId={{}};globalThis.document={{activeElement:null}};globalThis.$=id=>byId[id]||null;
const view=node('view-models');const bar=node('m-spend-bar',{{}},view),legend=node('m-spend-legend',{{}},view),table=node('m-table',{{}},view);
const seg=node('seg',{{modelIds:'a::Codex::high\\na::Codex::xhigh'}},bar),chip=node('chip',{{modelIds:'a::Codex::high\\na::Codex::xhigh'}},legend);
const expand=node('expand-old',{{modelExpand:'a::Codex'}},table);
const results={{}};
chip.focus();let focus=modelFocusKey();restoreModelFocus(focus);results.chipUnchanged=document.activeElement.label;
focus=modelFocusKey();chip.isConnected=false;legend.children=[];const chip2=node('chip-new',{{modelIds:chip.dataset.modelIds}},legend);restoreModelFocus(focus);results.chipRebuilt=document.activeElement.label;
expand.focus();focus=modelFocusKey();expand.isConnected=false;table.children=[];node('expand-new',{{modelExpand:'a::Codex'}},table);restoreModelFocus(focus);results.expandRebuilt=document.activeElement.label;
const outside=node('outside',{{modelIds:'x'}},null);outside.focus();results.outsideKey=modelFocusKey();
process.stdout.write(JSON.stringify(results));
"""
        payload = json.loads(subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        ).stdout)
        self.assertEqual(payload["chipUnchanged"], "chip")
        self.assertEqual(payload["chipRebuilt"], "chip-new")
        self.assertEqual(payload["expandRebuilt"], "expand-new")
        self.assertIsNone(payload["outsideKey"])

    def test_models_table_labels_effort_qualified_runtime_rows(self):
        self.assertIn(
            "`${modelEffortLabel(only)} · ${f(only.logs)} ${countWord(only.logs,'log')}`",
            self.page,
        )
        self.assertIn("<div class=modelVariantName>${esc(modelEffortLabel(variant))}<small>${f(variant.logs)} ${countWord(variant.logs,'log')}</small>", self.page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_legacy_model_runtime_filter_expands_to_effort_rows(self):
        match = re.search(
            r"function migrateModelRuntimeFilters\(.*?\n\}", self.page, re.DOTALL,
        )
        self.assertIsNotNone(match, "Legacy model filters need effort-row migration")
        script = match.group(0) + r"""
const rows=[
  {id:'gpt-5.6::Codex::high',model:'gpt-5.6',runtime:'Codex'},
  {id:'gpt-5.6::Codex::xhigh',model:'gpt-5.6',runtime:'Codex'},
  {id:'gpt-5.6::Codex::unavailable',model:'gpt-5.6',runtime:'Codex'},
  {id:'gpt-5.6::Cursor::unavailable',model:'gpt-5.6',runtime:'Cursor'},
];
console.log(JSON.stringify({
  legacy:migrateModelRuntimeFilters(['gpt-5.6::Codex'],rows),
  unavailable:migrateModelRuntimeFilters(['gpt-5.6::Codex::unavailable'],rows),
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "legacy": [
                "gpt-5.6::Codex::high", "gpt-5.6::Codex::xhigh",
                "gpt-5.6::Codex::unavailable",
            ],
            "unavailable": ["gpt-5.6::Codex::unavailable"],
        })

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_model_effort_label_keeps_known_and_unavailable_budgets_separate(self):
        match = re.search(r"function modelEffortLabel\(.*?\n\}", self.page, re.DOTALL)
        self.assertIsNotNone(match, "Model rows need an explicit effort-group label")
        script = match.group(0) + r"""
console.log(JSON.stringify({
  known:modelEffortLabel({reasoning_effort:'xhigh'}),
  opus:modelEffortLabel({thinking_executions:2}),
  missing:modelEffortLabel({}),
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "known": "xhigh",
            "opus": "thinking unavailable",
            "missing": "unavailable",
        })

    def test_model_day_merge_preserves_partial_source_coverage(self):
        merge_match = re.search(
            r"function mergeModelDays\(models,window\)\{.*?\n\}",
            self.page,
            re.DOTALL,
        )
        aggregate_match = re.search(
            r"function aggregateModelDays\(rows\)\{.*?\n\}",
            self.page,
            re.DOTALL,
        )
        self.assertIsNotNone(merge_match, "dashboard needs the shared day merge")
        self.assertIsNotNone(aggregate_match, "dashboard needs the shared day aggregate")
        script = r"""
const MODEL_COUNTER_KEYS=['cost','input_tokens','output_tokens','executions'];
const MODEL_ARRAY_KEYS=[];
const modelDayInRange=()=>true;
const metricAvailable=(row,key)=>row.availability?.[key]===true;
const metricPartial=(row,key)=>row.coverage?.[key]?.complete===false;
const hasLocalEstimate=()=>false;
const usageBasis=()=> 'reported';
const modelWaitDistribution=()=>({median_wait_s:0,p95_wait_s:0});
const modelMedian=()=>0;
""" + aggregate_match.group(0) + "\n" + merge_match.group(0) + r"""
const rows=mergeModelDays([{daily:[{
  day:'2026-08-27',cost:1,input_tokens:10,output_tokens:2,executions:2,
  availability:{tokens:true,input_tokens:true,output_tokens:false,cost:true,cache:true},
  coverage:{
    tokens:{complete:false},cost:{complete:false},cache:{complete:false},
  },
}]}],{});
const selectedSession=aggregateModelDays([{
  cost:1,input_tokens:10,output_tokens:4,executions:2,
  cost_covered_executions:1,cost_covered_output_tokens:2,cost_covered_cost:.5,
  availability:{tokens:true,input_tokens:true,output_tokens:true,cost:true,cache:false},
}]);
console.log(JSON.stringify({merged:rows[0],total:aggregateModelDays(rows),selectedSession}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        payload = json.loads(result.stdout)
        coverage = payload["merged"]["coverage"]
        self.assertFalse(coverage["tokens"]["complete"])
        self.assertFalse(coverage["cost"]["complete"])
        self.assertFalse(coverage["cache"]["complete"])
        self.assertTrue(payload["merged"]["availability"]["input_tokens"])
        self.assertFalse(payload["merged"]["availability"]["output_tokens"])
        self.assertTrue(payload["total"]["availability"]["input_tokens"])
        self.assertFalse(payload["total"]["availability"]["output_tokens"])
        self.assertFalse(payload["selectedSession"]["coverage"]["cost"]["complete"])

    def test_model_stats_supports_multi_model_comparison(self):
        for marker in (
            "id=m-model-picker", "id=m-model-options", "id=m-model-summary",
            "tm_model_filters", "MODEL_COLORS", "buildModelTrend",
            "bars show output", "observed tok/s",
            "modelTipMetrics",
            "Typical wait", "median wait", "human pause excluded",
            "modelWaitDistribution", "wait_durations_s", "p95_wait_s",
            "Matched pace", "renderMatchedPace", "modelRuntimeLabel",
            "migrateModelRuntimeFilters", "Typical workload (approximate shape, not semantic difficulty)", "median_peak_input_tokens",
            "95% CI", "Select 2 models", "TTFT unavailable",
        ):
            self.assertIn(marker, self.page)
        self.assertNotIn('id=m-model aria-label="Models filter"', self.page)
        self.assertNotIn("Speed change", self.page)

    def test_model_colors_encode_runtime_family_and_keep_volume_neutral(self):
        """Flattening runtime families or recoloring volume bars breaks chart meaning."""
        start = self.page.find("// model-color-logic-start")
        end = self.page.find("// model-color-logic-end")
        logic = self.page[start:end] if start >= 0 and end > start else ""
        script = logic + """
const names = [
  'gpt-5.6-sol::Codex',
  'gpt-5.6-terra::Codex',
  'codex-auto-review::Codex',
  'gpt-5.6-luna::Codex',
  'claude-opus-4-8::Claude Code',
  'claude-sonnet-5::Claude Code',
  'claude-haiku-4-5::Claude-3P',
  'claude-opus-4-8::Claude-3P',
  'claude-sonnet-5::Claude-3P',
  'composer-2.5::Cursor',
  'claude-sonnet-4-6::Kiro',
  'qwen3::OpenCode',
  'other::Unknown',
];
console.log(JSON.stringify({
  codex: [modelColor(names[2], names), modelColor(names[3], names), modelColor(names[0], names), modelColor(names[1], names)],
  claude: [modelColor(names[4], names), modelColor(names[5], names)],
  thirdParty: [modelColor(names[6], names), modelColor(names[7], names), modelColor(names[8], names)],
  cursor: modelColor(names[9], names),
  kiro: modelColor(names[10], names),
  openCode: modelColor(names[11], names),
  fallback: modelColor(names[12], names),
  stable: modelColor(names[0], names) === modelColor(names[0], [...names].reverse()),
  paint: modelTrendPaint('#FFAA64'),
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "codex": ["#6F9CFF", "#8EC5FF", "#4C86F0", "#B6CFFF"],
            "claude": ["#E3825C", "#F4A26E"],
            "thirdParty": ["#A46AF4", "#AD88F0", "#7459F0"],
            "cursor": "#3CC6C0",
            "kiro": "#E3CC5C",
            "openCode": "#FF8FB8",
            "fallback": "#A9B8C7",
            "stable": True,
            "paint": {
                "barFill": "#536879",
                "barStroke": "#7890A3",
                "line": "#FFAA64",
            },
        })

    def test_ranked_model_groups_get_distinct_family_shades(self):
        """Top-ranked groups of one runtime must not share a color in Models charts."""
        start = self.page.find("// model-color-logic-start")
        end = self.page.find("// model-color-logic-end")
        logic = self.page[start:end] if start >= 0 and end > start else ""
        script = logic + """
const groups = [
  {key: 'a', ids: ['gpt-5.6-sol::Codex']},
  {key: 'b', ids: ['claude-opus-4-8::Claude Code']},
  {key: 'c', ids: ['gpt-5.6-terra::Codex']},
  {key: 'd', ids: ['gpt-5.6-luna::Codex']},
  {key: 'e', ids: ['gpt-6-sol::Codex']},
];
const colors = modelRankedColors(groups);
console.log(JSON.stringify(groups.map(group => colors.get(group.key))));
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        colors = json.loads(result.stdout)
        self.assertEqual(colors, ["#6F9CFF", "#E3825C", "#8EC5FF", "#4C86F0", "#B6CFFF"])
        self.assertEqual(len(set(colors)), 5)
        single_runtime = logic + """
const groups = ['a', 'b', 'c', 'd', 'e'].map(key => ({key, ids: [`model-${key}::Claude Code`]}));
const colors = modelRankedColors(groups);
console.log(JSON.stringify(groups.map(group => colors.get(group.key))));
"""
        result = subprocess.run(["node", "-e", single_runtime], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        colors = json.loads(result.stdout)
        self.assertEqual(colors[0], "#E3825C")
        self.assertEqual(len(set(colors)), 5)

    def test_model_trend_omits_legend_and_limits_hover_to_relevant_metrics(self):
        self.assertNotIn("id=m-legend", self.page)
        self.assertNotIn("$('m-legend')", self.page)
        for marker in (
            "function modelTrendRowHasUsage(row)",
            "Number(row?.input_tokens||0)>0",
            "Number(row?.output_tokens||0)>0",
            "Number(row?.executions||0)>0",
            ".filter(item=>modelTrendRowHasUsage(item.row))",
            "(rows.length?'':'<div class=r><span>No data</span></div>')",
        ):
            self.assertIn(marker, self.page)
        self.assertIn(
            '<div class=modelTipName title="${esc(item.model)}"><i class=modelSwatch '
            'style="background:${item.color}"></i>${esc(item.model)}</div>',
            self.page,
        )
        model_trend = self.page.split("function drawModelTrend(chart){", 1)[1].split(
            "function renderMatchedPace", 1
        )[0]
        for marker in (
            "const outputAvailable=metricAvailable(item.row,'tokens')",
            "const selectedAvailable=metric.available(item.row)",
            "<small>output</small>", "<small>${esc(metric.note)}</small>",
            "${outputAvailable?compactNumber(item.row.output_tokens||0):'--'}",
            "${selectedAvailable?metric.format(metric.value(item.row)):'--'}",
        ):
            self.assertIn(marker, model_trend)
        for marker in (
            "<small>input</small>", "<small>executions</small>",
            "<small>avg input</small>", "<small>avg output</small>",
            "<small>tok/s</small>", "<small>typical wait</small>",
        ):
            self.assertNotIn(marker, model_trend)

    def test_model_stats_supports_project_scoped_average_io_trends(self):
        for marker in (
            "id=m-project", "tm_model_project", "tm_model_project_filters",
            "/model-stats?project=", "renderActiveModelStats",
            "modelProjectRequest", "modelProjectLoadingKey",
            "data-model-metric=avg_input", "data-model-metric=avg_output",
            "MODEL_TREND_METRICS", "modelTokensPerExecution",
            "Model trends", "avg input / execution", "avg output / execution",
            "input / exec", "output / exec",
            "Daily model ${metric.note} and output token volume",
        ):
            self.assertIn(marker, self.page)
        self.assertIn("modelProjectCache.set(key,payload.model_stats)", self.page)
        self.assertIn("request!==modelProjectRequest||project!==modelProject", self.page)
        self.assertNotIn(
            "$('m-trend-title').textContent=modelTrendMetric==='wait'",
            self.page,
        )

    def test_model_filter_menus_stay_stable_during_live_refreshes(self):
        for marker in (
            "setLogSelectOptions(projectSelect,projectOptions,modelProject)",
            "setLogHtml($('m-model-options'),modelPickerOptions)",
            "function positionModelPickerMenu()",
            "picker.classList.toggle('opensUp',opensUp)",
            "$('m-model-picker').addEventListener('toggle',()=>requestAnimationFrame(positionModelPickerMenu))",
            ":is(.modelHead,.efficiencyHead).spectrumPageHead{overflow:hidden;z-index:2}",
            ".spectrumPageHeadFrame:has(.modelControls){z-index:10}",
            ".modelPicker.opensUp .modelPickerMenu{top:auto;bottom:calc(100% + 6px)}",
        ):
            self.assertIn(marker, self.page)

    def test_model_project_background_refresh_does_not_shift_the_page(self):
        for marker in (
            "function setModelProjectLoading(loading)",
            "projectSelect.setAttribute('aria-busy','true')",
            "setModelProjectLoading(false);",
            "$('m-project-status').textContent='';",
        ):
            self.assertIn(marker, self.page)
        self.assertNotIn("Loading ${project}", self.page)

    def test_model_trend_hover_panel_is_scrollable_and_pointer_stable(self):
        model_trend = self.page.split("function drawModelTrend(chart){", 1)[1].split(
            "function renderMatchedPace", 1
        )[0]
        for marker in (
            '#m-chart-tip{pointer-events:auto;overscroll-behavior:contain;',
            "scrollbar-gutter:stable",
            '#m-chart-tip .h{position:sticky;top:0',
            'id=m-chart-tip tabindex=0 aria-label="Model details for hovered day"',
            "const scheduleTipHide=()=>",
            "setTimeout(hideTip,180)",
            "hit.onpointerleave=scheduleTipHide",
            "tip.onpointerenter=cancelTipHide",
            "tip.onpointerleave=scheduleTipHide",
            "rightX+tip.offsetWidth<=rect.width-8",
            "if(dayChanged)tip.scrollTop=0",
        ):
            self.assertIn(marker, self.page)
        self.assertNotIn("hit.onpointerleave=()=>tip.style.display='none'", model_trend)

    def test_wait_time_is_first_class_across_current_logs_and_models(self):
        for marker in (
            "data-chart=wait", "drawWaitChart", "data-gsort=wait",
            "id=lf-wait", "id=m-wait", "id=m-metric", "data-model-metric=wait",
            "Prompt-to-completed-response", "lower is better",
        ):
            self.assertIn(marker, self.page)
        self.assertIn("wait_time?.total_s", self.page)
        self.assertIn("CURRENT?.wait_time?.samples", self.page)

    def test_models_history_supports_exact_local_days(self):
        history = self.page.split(
            'id=m-range aria-label="Models history range"', 1
        )[1].split("</select>", 1)[0]
        expected = (
            '<option value=today>Today</option>',
            '<option value=yesterday>Yesterday</option>',
            '<option value=7>7 days</option>',
            '<option value=30 selected>30 days</option>',
            '<option value=90>90 days</option>',
            '<option value=month>Month</option>',
            '<option value=last_month>Last month</option>',
            '<option value=all>All history</option>',
        )
        for option in expected:
            self.assertIn(option, history)
        self.assertEqual([history.index(option) for option in expected], sorted(
            history.index(option) for option in expected
        ))
        for marker in (
            "const MODEL_RANGES=['today','yesterday','7','30','90','month','last_month','all'];",
            "const EFFICIENCY_RANGES=['today','yesterday','7','30','90','month','last_month','all'];",
            "const DELIVERY_RANGES=['today','yesterday','7','30','90','month','last_month'];",
            "let deliveryRange=localStorage.getItem('tm_delivery_range')||'30';",
            "if(!DELIVERY_RANGES.includes(deliveryRange)){deliveryRange='30';localStorage.setItem('tm_delivery_range',deliveryRange);}",
            "if(!MODEL_RANGES.includes(modelRange))",
            "function modelRangeWindow(range,now=new Date())",
            "if(range==='today'||range==='yesterday')",
            "function modelDayInRange(day,window)",
            "mergeModelDays(selected,rangeWindow)",
            "buildModelTrend(selected,rangeWindow,names)",
            ".filter(day=>modelDayInRange(day.day,rangeWindow))",
            "modelRangeLabel(modelRange)",
        ):
            self.assertIn(marker, self.page)

    def test_model_pricing_is_editable_and_supports_new_models_in_settings(self):
        for marker in (
            "id=model-pricing-settings", "id=model-pricing-rows",
            "id=model-price-scope", "id=model-price-scope-from-now",
            "id=model-price-scope-from-date", "id=model-price-scope-all-history",
            "value=from_now", "value=from_date", "value=all_history",
            "id=model-price-effective-from", "id=model-price-scope-note",
            "id=model-pricing-reviewed", "id=model-pricing-sources",
            "id=model-price-add-form", "id=model-price-provider",
            "id=model-price-model", "id=model-price-input",
            "id=model-price-output", "id=model-price-cache-write",
            "id=model-price-cache-read", "id=model-price-save-changes",
            "id=model-price-save-changes type=button disabled",
            "data-model-price-select", "data-model-price-lifecycle",
            "function renderModelPricing",
            "function modelPriceEffectiveFrom", "function updateModelPriceScope",
            "function confirmModelPriceHistory",
            "function saveModelPriceChanges", "/settings/model-pricing",
            "apply_to_all_history", "From now", "From date", "All history",
            "Save models", "Restore built-in price", "Remove custom model",
            "changes",
        ):
            self.assertIn(marker, self.page)
        self.assertIn("USD per 1 million tokens", self.page)
        self.assertIn("By default, a change starts now and older sessions retain their earlier prices.", self.page)
        self.assertIn("<span id=model-price-scope-note></span>", self.page)
        self.assertIn("Choose the date", self.page)
        self.assertIn("older session estimates", self.page)
        self.assertIn(".modelPriceScope.history", self.page)
        self.assertIn("badge.hidden=!hasCustomPricing", self.page)
        self.assertIn("source==='built-in'?'':source", self.page)
        self.assertIn("sourceLabel?`<span class=modelPriceSource>", self.page)
        self.assertIn("row.overridden", self.page)
        self.assertIn("pricing.sources", self.page)
        self.assertIn("pricing.reviewed_on", self.page)
        self.assertIn("modelPriceSelectionState(modelPricingSelected.size)", self.page)
        self.assertIn("@media(max-width:700px){.modelPriceTableWrap{overflow:visible", self.page)
        self.assertIn(".modelPriceTable{display:block;min-width:0", self.page)
        self.assertIn("data-label=", self.page)
        self.assertNotIn("data-model-price-save", self.page)
        self.assertNotIn("Built-in defaults", self.page)
        self.assertLess(
            self.page.index("id=model-pricing-settings"),
            self.page.index("id=delivery-settings"),
        )
        self.assertNotIn("id=cursor-source-status", self.page)
        self.assertNotIn("Local trace source", self.page)
        self.assertNotIn("function renderCursorSourceStatus", self.page)
        self.assertIn("h==='settings-budgets'||h==='budgets'", self.page)
        self.assertIn("'model-pricing':'model-pricing-settings'", self.page)
        self.assertIn("if(card)scrollToSettingsCard(card);", self.page)

    def test_model_pricing_groups_rows_by_provider_tabs_in_a_bounded_scroll(self):
        pricing = self.page.split("id=model-pricing-settings", 1)[1].split(
            "id=delivery-settings", 1,
        )[0]
        self.assertIn('id=model-price-tabs role=tablist', pricing)
        self.assertIn('id=model-price-table-wrap', pricing)
        self.assertLess(
            pricing.index("id=model-price-tabs"),
            pricing.index("id=model-pricing-rows"),
        )
        for marker in (
            "function renderModelPriceTabs",
            "function applyModelPriceTab",
            "function selectModelPriceTab",
            'data-model-price-provider="${esc(row.provider)}"',
            "root.dataset.modelPriceProvider!==modelPricingTab",
            "modelPricingSelected.has(modelPriceKey(row.provider,row.model))",
            "event.key==='ArrowRight'",
        ):
            self.assertIn(marker, self.page)
        self.assertRegex(
            self.page,
            r"\.modelPriceTableWrap\{overflow:auto;max-height:min\(460px,58vh\)",
        )
        self.assertIn(".modelPriceTable tr[hidden]{display:none!important}", self.page)

    def test_model_pricing_uses_explicit_row_selection_without_an_actions_column(self):
        pricing = self.page.split("id=model-pricing-settings", 1)[1].split(
            "id=delivery-settings", 1,
        )[0]
        for marker in (
            "Apply to selected models",
            "id=model-price-selection-count",
            "No models selected",
        ):
            self.assertIn(marker, pricing)
        for marker in (
            "data-model-price-select",
            "data-model-price-lifecycle",
            "function setModelPriceSelected",
            "function updateModelPriceSelectionState",
            "modelPricingSelected",
            "setModelPriceSelected(root,true,false)",
            "keys=[...modelPricingSelected]",
            "Restore built-in price",
            "Remove custom model",
            "Select ${row.model} for price update",
        ):
            self.assertIn(marker, self.page)
        self.assertNotIn("<th class=num>Actions</th>", pricing)
        self.assertNotIn('data-label="Action"', pricing)
        self.assertNotIn("class=modelPriceActions", self.page)
        self.assertIn(
            ".modelPriceTable tr{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))",
            self.page,
        )

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_model_price_selection_copy_tracks_the_selected_count(self):
        match = re.search(
            r"function modelPriceSelectionState\(count\)\{.*?\n\}",
            self.page,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "dashboard needs one selection-copy helper")
        script = match.group(0) + """
console.log(JSON.stringify([
  modelPriceSelectionState(0),
  modelPriceSelectionState(1),
  modelPriceSelectionState(3),
]));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), [
            {"summary": "No models selected", "button": "Save models", "disabled": True},
            {"summary": "1 model selected", "button": "Save 1 model", "disabled": False},
            {"summary": "3 models selected", "button": "Save 3 models", "disabled": False},
        ])

    def test_models_and_git_keep_secondary_copy_in_accessible_help(self):
        models = self.page.split("id=view-models", 1)[1].split("id=view-daily", 1)[0]
        for marker in (
            "id=m-change-label", "id=m-wait-label",
            "function setModelKpiHelp(labelId,detail)",
            "setModelKpiHelp('m-speed-label',speedDetail)",
            "setModelKpiHelp('m-wait-label',waitDetail)",
            ".modelCellTip.fieldtip{display:table-cell}",
            "class=\"fieldtip modelCellTip modelNameCell\" tabindex=0 data-tip=\"${esc(tip)}\" aria-description=\"${esc(tip)}\"",
        ):
            self.assertIn(marker, self.page)

        git = self.page.split("id=view-git", 1)[1].split("id=view-learn", 1)[0]
        hover_copy = (
            "Period-specific spend and Git coverage; unavailable is not zero.",
            "Added + deleted text lines per day. Select a day for spend.",
            "Conversion, typical days, and outliers. Statistics only · no quality judgment.",
            "Local Git only: hashed keys and line counts, never filenames or messages. Spend is estimated. Few lines make ratios noisy. Not a quality score.",
            "Ranked local Git and covered-spend observations.",
            "Median, range, and high day · ratios require 50+ pushed lines.",
        )
        for copy in hover_copy:
            self.assertIn(f'aria-description="{copy}"', git)
            self.assertIn(f'data-tip="{copy}"', git)
            self.assertNotIn(f"<p>{copy}</p>", git)
        # The local-only, privacy, estimate, and noise caveats live once in header help.
        self.assertNotIn("class=deliveryFoot", git)
        self.assertIn('class="fieldtip" id=d-evidence-ratio tabindex=0', git)
        self.assertIn("function setDeliveryEvidenceRatio(text,detail)", self.page)
        self.assertIn(
            "setDeliveryEvidenceRatio(selected?`${comparable} / ${selected} comparable`",
            self.page,
        )
        self.assertIn("ratio.dataset.tip=detail", self.page)

    def test_model_comparison_has_tooltips(self):
        for marker in (
            "modelHelp", "id=m-coverage", 'aria-label="Output timing coverage"',
            "same model can appear more than once",
            "A ratio above 1 favors the named faster runtime",
            "The median is primary because the average can be pulled upward",
            "95% confidence interval", "Matched pace",
            "model runtime", "Observed output pace", "Typical workload (approximate shape, not semantic difficulty)",
            "Typical wait", "semantic difficulty",
            "The 95% confidence interval crosses 1.00",
            "$('m-coverage').setAttribute('aria-valuenow'",
            "stripNativeFieldTipTitles", "removeAttribute('title')",
            "aria-description",
        ):
            self.assertIn(marker, self.page)
        self.assertIn('<div class=filterControl><span>Models</span>', self.page)
        self.assertIn('<label class=filterControl><span>History</span>', self.page)
        self.assertNotIn("Select model-runtime histories, not just model names.", self.page)
        self.assertNotIn("Limits tokens, executions, workload summaries", self.page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_model_board_pushback_reads_work_labels_for_the_same_window(self):
        script = f"""
const fs=require('fs');const page=fs.readFileSync({json.dumps(str(Path(DASHBOARD_PAGE)))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
const constLine=name=>page.slice(page.indexOf(`const ${{name}}=`),page.indexOf(';',page.indexOf(`const ${{name}}=`))+1);
const esc=s=>String(s),f=v=>String(v),pct=v=>Math.round(v*100)+'%';
let modelRange='30',modelProject='p1',LATEST=null,rerenders=0,urls=[],payload={{}};
const rerenderActiveModelStats=()=>{{rerenders++;}};
const fetch=async url=>{{urls.push(url);const status=globalThis.statusOverride||200;return {{ok:status<400,status,json:async()=>payload}};}};
eval(constLine('MODEL_PUSHBACK_PERIODS').replace(/^const /,'var '));
let modelPushback={{key:'',state:'idle',rates:new Map()}},modelPushbackRequest=0;
eval(['modelPushbackKey','modelPushbackFor','modelPushbackCell','modelBoardValue'].map(extract).join('\\n')+'\\nasync '+extract('loadModelPushback'));
const group=(model,runtime)=>({{model,runtime,variants:[{{}}],window:{{}}}});
(async()=>{{
 const out={{}};
 payload={{ok:true,settings:{{enabled:true}},insights:{{rework:{{models:[{{model:'opus',runtime:'Claude Code',rate:.14,samples:409,few_samples:false}},{{model:'mini',runtime:'Codex',rate:.25,samples:8,few_samples:true}}]}}}}}};
 LATEST={{}};await loadModelPushback();
 out.url=urls[0];out.rerenders=rerenders;
 out.ready=modelPushbackCell(group('opus','Claude Code'));
 out.few=modelPushbackCell(group('mini','Codex'));
 out.otherRuntime=modelPushbackCell(group('opus','Codex'));
 out.variant=modelPushbackCell({{model:'opus',runtime:'Claude Code',window:{{}}}});
 out.sortValue=modelBoardValue(group('opus','Claude Code'),'pushback');
 await loadModelPushback();out.cached=urls.length;
 modelRange='yesterday';await loadModelPushback();
 out.unsupported=modelPushbackCell(group('opus','Claude Code'));out.unsupportedFetches=urls.length;
 modelRange='90';payload={{ok:true,settings:{{enabled:false}},insights:{{rework:{{models:[]}}}}}};await loadModelPushback();
 out.url90=urls[urls.length-1];out.off=modelPushbackCell(group('opus','Claude Code'));
 // Turning Work insights on shows up after the cache expires instead of sticking.
 payload={{ok:true,settings:{{enabled:true}},insights:{{rework:{{models:[{{model:'opus',runtime:'Claude Code',rate:.5,samples:40,few_samples:false}}]}}}}}};
 await loadModelPushback();out.beforeExpiry=urls.length;
 modelPushback.at-=MODEL_PUSHBACK_TTL_MS+1;await loadModelPushback();
 out.afterExpiry=urls.length;out.refreshed=modelPushbackCell(group('opus','Claude Code'));
 modelRange='30';modelProject='';modelPushback.key='';
 payload={{ok:false,error:'Work insights are available on macOS only.'}};
 globalThis.statusOverride=404;await loadModelPushback();out.unsupportedHost=modelPushbackCell(group('opus','Claude Code'));
 process.stdout.write(JSON.stringify(out));
}})();
"""
        out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(out["url"], "/work?months=30d&project=p1")
        self.assertEqual(out["rerenders"], 1)
        self.assertEqual(out["ready"], '<td class="num mono" title="57 of 409 labeled follow-ups pushed back · local model estimate">14%</td>')
        self.assertIn(">25%*</td>", out["few"])
        self.assertIn("modelMuted", out["few"])
        # Models are scoped by runtime: the same name in another client has its own history.
        self.assertIn(">--</td>", out["otherRuntime"])
        self.assertEqual(out["variant"], '<td class="num modelMuted"></td>')
        self.assertEqual(out["sortValue"], 0.14)
        self.assertEqual(out["cached"], 1)
        self.assertEqual(out["unsupportedFetches"], 1)
        self.assertIn("not available for Yesterday or Last month", out["unsupported"])
        self.assertEqual(out["url90"], "/work?months=90d&project=p1")
        self.assertIn("Turn on Work insights", out["off"])
        self.assertEqual(out["afterExpiry"], out["beforeExpiry"] + 1)
        self.assertIn(">50%</td>", out["refreshed"])
        self.assertIn("not available on this system", out["unsupportedHost"])


if __name__ == "__main__":
    unittest.main()
