import unittest
import http.client
import json
import shutil
import subprocess
import time
from pathlib import Path
import meter
from tests.dashboard_page import DASHBOARD_PAGE
from tests.dashboard_layout_support import DashboardLayoutCase


class DashboardSubagentLayoutTests(DashboardLayoutCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_subagent_explorer_prefers_roles_and_filters_status_and_signals(self):
        script = f"""
const fs=require('fs');
const page=fs.readFileSync({json.dumps(str(Path(DASHBOARD_PAGE)))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
eval(extract('agentIdentityPresentation'));
eval(extract('timeFilterBounds'));eval(extract('filterSubagentInventory'));
const usage={{inventory_count:3,inventory_truncated:false,inventory:[
 {{id:'a',root_session_id:'root-a',project:'/token-meter',runtime:'codex',model:'gpt',role:'token_meter_reviewer',label:'Heisenberg',activity_state:'complete',last_activity_at:100,tokens:10,tokens_available:true,cost:1,cost_available:true,work_time_s:30,attention:[]}},
 {{id:'b',root_session_id:'root-a',project:'/token-meter',runtime:'codex',model:'gpt',role:null,label:'Gauss',activity_state:'incomplete',last_activity_at:200,tokens:20,tokens_available:true,cost:4,cost_available:true,work_time_s:60,attention:[{{code:'peer_cost_outlier',explanation:'3x peers'}}]}},
 {{id:'c',root_session_id:'root-c',project:'/luna-3',runtime:'claude',model:'opus',role:null,label:'',activity_state:'working',last_activity_at:300,tokens:null,tokens_available:false,cost:null,cost_available:false,work_time_s:null,attention:[]}},
]}};
const result=filterSubagentInventory(usage,{{query:'gauss',runtime:'codex',project:'/token-meter',model:'gpt',status:'incomplete',signal:'peer_cost_outlier',window:'all',sort:'cost'}},400);
const workSorted=filterSubagentInventory(usage,{{query:'',runtime:'',project:'',model:'',status:'all',signal:'all',window:'all',sort:'work'}},400);
process.stdout.write(JSON.stringify({{
 role:agentIdentityPresentation(usage.inventory[0],0),
 nickname:agentIdentityPresentation(usage.inventory[1],1),
 fallback:agentIdentityPresentation(usage.inventory[2],2),
 result,workSorted,
}}));
"""
        payload = json.loads(subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        ).stdout)

        self.assertEqual(payload["role"], {
            "primary": "token_meter_reviewer", "secondary": "Heisenberg",
        })
        self.assertEqual(payload["nickname"], {
            "primary": "Gauss", "secondary": "",
        })
        self.assertEqual(payload["fallback"], {
            "primary": "Agent 3", "secondary": "",
        })
        self.assertEqual([row["id"] for row in payload["result"]["rows"]], ["b"])
        self.assertEqual(payload["result"]["summary"]["incomplete"], 1)
        self.assertEqual(payload["result"]["summary"]["attention"], 1)
        self.assertEqual(payload["result"]["summary"]["parentSessions"], 1)
        self.assertTrue(payload["result"]["summary"]["complete"])
        self.assertEqual(
            [row["id"] for row in payload["workSorted"]["rows"]],
            ["b", "a", "c"],
        )

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_last_month_time_filter_bounds_sessions_subagents_and_role_days(self):
        script = f"""
const fs=require('fs');
const page=fs.readFileSync({json.dumps(str(Path(DASHBOARD_PAGE)))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
const appFilterGroup=s=>s.provider,projectFilterValue=p=>p||'';
eval(['timeFilterBounds','agentIdentityPresentation','filterSubagentInventory','childRootId','localDateKey','dateKeyAgo','calendarMonthWindow','monthToDateWindow','modelRangeWindow','subagentRoleKey','subagentRoleDayRows','subagentModelKey','subagentModelDayRows','normalizedSubagentNavigationState'].map(extract).join('\\n'));
eval(page.slice(page.indexOf('function allSessionsView('),page.indexOf('function allSessionsCountText(')));
const DEFAULT_TIME_FILTER_WINDOW='30d';
const subagentFilterDefaults={{query:'',role:'',kind:'',runtime:'',project:'',model:'',status:'all',signal:'all',window:DEFAULT_TIME_FILTER_WINDOW,sort:'recent'}};
const now=new Date(2026,9,5,12).getTime(),at=(...parts)=>new Date(...parts).getTime()/1000;
const bounds=timeFilterBounds('last_month',now);
const stamps={{current:at(2026,9,1,0,0,1),lastStart:at(2026,8,1),lastEnd:at(2026,8,30,23,59,59),prior:at(2026,7,31,23,59,59)}};
const inventory=Object.entries(stamps).map(([id,last])=>({{id,root_session_id:'root',runtime:'codex',label:id,activity_state:'complete',last_activity_at:last,attention:[]}}));
const agents=filterSubagentInventory({{inventory}},{{window:'last_month',status:'all',signal:'all',sort:'recent'}},now/1000).rows.map(row=>row.id).sort();
const sessions=allSessionsView(Object.entries(stamps).map(([id,mtime])=>({{id,provider:'codex',title:id,cost:1,mtime}})),{{rangeStart:bounds.start,rangeEnd:bounds.end}}).rows.map(row=>row.id).sort();
const roleRow=day=>({{day,runtime:'codex',kind:'spawned',role:'reviewer',project:''}});
const roleDays=subagentRoleDayRows({{role_days:['2026-10-01','2026-09-30','2026-09-01','2026-08-31'].map(roleRow)}},{{window:'last_month'}},now).map(row=>row.day);
const monthBounds=timeFilterBounds('month',now);
const monthAgents=filterSubagentInventory({{inventory}},{{window:'month',status:'all',signal:'all',sort:'recent'}},now/1000).rows.map(row=>row.id).sort();
const monthSessions=allSessionsView(Object.entries(stamps).map(([id,mtime])=>({{id,provider:'codex',title:id,cost:1,mtime}})),{{rangeStart:monthBounds.start,rangeEnd:monthBounds.end}}).rows.map(row=>row.id).sort();
const monthRoleDays=subagentRoleDayRows({{role_days:['2026-10-06','2026-10-05','2026-10-01','2026-09-30'].map(roleRow)}},{{window:'month'}},now).map(row=>row.day);
const modelRow=day=>({{day,runtime:'codex',model:'gpt-5',project:''}});
const lastMonthModelDays=subagentModelDayRows({{model_days:['2026-10-01','2026-09-30','2026-09-01','2026-08-31'].map(modelRow)}},{{window:'last_month'}},now).map(row=>row.day);
const monthModelDays=subagentModelDayRows({{model_days:['2026-10-05','2026-10-01','2026-09-30'].map(modelRow)}},{{window:'month'}},now).map(row=>row.day);
process.stdout.write(JSON.stringify({{
 agents,sessions,roleDays,monthAgents,monthSessions,monthRoleDays,lastMonthModelDays,monthModelDays,
 monthRestored:normalizedSubagentNavigationState('roles',{{window:'month'}}).filters.window,
 restored:normalizedSubagentNavigationState('roles',{{window:'last_month'}}).filters.window,
 rolling:timeFilterBounds('7d',now).end,
}}));
"""
        payload = json.loads(subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        ).stdout)
        self.assertEqual(payload["agents"], ["lastEnd", "lastStart"])
        self.assertEqual(payload["sessions"], ["lastEnd", "lastStart"])
        self.assertEqual(payload["roleDays"], ["2026-09-01", "2026-09-30"])
        self.assertEqual(payload["restored"], "last_month")
        self.assertEqual(payload["rolling"], 0)
        self.assertEqual(self.page.count(
            "<option value=today>Today</option><option value=yesterday>Yesterday</option>"
            "<option value=7d>7 days</option><option value=30d selected>30 days</option>"
            "<option value=90d>90 days</option><option value=month>Month</option>"
            "<option value=last_month>Last month</option><option value=all>All history</option></select>"
        ), 3)
        self.assertNotIn("value=24h", self.page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_calendar_time_filters_bound_sessions_subagents_and_role_days(self):
        script = f"""
const fs=require('fs');
const page=fs.readFileSync({json.dumps(str(Path(DASHBOARD_PAGE)))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
const appFilterGroup=s=>s.provider,projectFilterValue=p=>p||'';
eval(page.slice(page.indexOf('const TIME_FILTER_WINDOWS='),page.indexOf('const DELIVERY_EVIDENCE_FILTERS=')).replace(/^const /gm,'var '));
eval(['agentIdentityPresentation','filterSubagentInventory','childRootId','localDateKey','dateKeyAgo','calendarMonthWindow','monthToDateWindow','modelRangeWindow','subagentRoleKey','subagentRoleDayRows','normalizedSubagentNavigationState'].map(extract).join('\\n'));
eval(page.slice(page.indexOf('function allSessionsView('),page.indexOf('function allSessionsCountText(')));
const subagentFilterDefaults={{query:'',role:'',kind:'',runtime:'',project:'',model:'',status:'all',signal:'all',window:DEFAULT_TIME_FILTER_WINDOW,sort:'recent'}};
const now=new Date(2026,9,5,12).getTime(),at=(...parts)=>new Date(...parts).getTime()/1000;
const stamps={{today:at(2026,9,5,0,0,1),yesterdayStart:at(2026,9,4),yesterdayEnd:at(2026,9,4,23,59,59),monthStart:at(2026,9,1),lastMonth:at(2026,8,30,23,59,59)}};
const inventory=Object.entries(stamps).map(([id,last])=>({{id,root_session_id:'root',runtime:'codex',label:id,activity_state:'complete',last_activity_at:last,attention:[]}}));
const result={{}};
for(const window of ['today','yesterday','month']){{
 const bounds=timeFilterBounds(window,now);
 result[window]={{
  agents:filterSubagentInventory({{inventory}},{{window,status:'all',signal:'all',sort:'recent'}},now/1000).rows.map(row=>row.id).sort(),
  sessions:allSessionsView(Object.entries(stamps).map(([id,mtime])=>({{id,provider:'codex',title:id,cost:1,mtime}})),{{rangeStart:bounds.start,rangeEnd:bounds.end}}).rows.map(row=>row.id).sort(),
  roleDays:subagentRoleDayRows({{role_days:['2026-10-05','2026-10-04','2026-10-01','2026-09-30'].map(day=>({{day,runtime:'codex',kind:'spawned',role:'reviewer',project:''}}))}},{{window}},now).map(row=>row.day),
 }};
}}
process.stdout.write(JSON.stringify({{
 result,
 migrated:normalizeTimeFilterWindow('24h'),invalid:normalizeTimeFilterWindow('bogus'),
 restored:normalizedSubagentNavigationState('roles',{{window:'24h'}}).filters.window,
 restoredMonth:normalizedSubagentNavigationState('roles',{{window:'month'}}).filters.window,
 labels:TIME_FILTER_LABELS,
}}));
"""
        payload = json.loads(subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        ).stdout)
        result = payload["result"]
        self.assertEqual(result["today"]["agents"], ["today"])
        self.assertEqual(result["today"]["sessions"], ["today"])
        self.assertEqual(result["today"]["roleDays"], ["2026-10-05"])
        self.assertEqual(result["yesterday"]["agents"], ["yesterdayEnd", "yesterdayStart"])
        self.assertEqual(result["yesterday"]["sessions"], ["yesterdayEnd", "yesterdayStart"])
        self.assertEqual(result["yesterday"]["roleDays"], ["2026-10-04"])
        self.assertEqual(result["month"]["agents"], ["monthStart", "today", "yesterdayEnd", "yesterdayStart"])
        self.assertEqual(result["month"]["roleDays"], ["2026-10-01", "2026-10-04", "2026-10-05"])
        self.assertEqual(payload["migrated"], "today")
        self.assertEqual(payload["invalid"], "30d")
        self.assertEqual(payload["restored"], "today")
        self.assertEqual(payload["restoredMonth"], "month")
        self.assertEqual(payload["labels"]["month"], "Month")
        self.assertNotIn("24h", payload["labels"])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_subagent_explorer_withholds_filtered_totals_when_inventory_is_truncated(self):
        script = f"""
const fs=require('fs');
const page=fs.readFileSync({json.dumps(str(Path(DASHBOARD_PAGE)))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
eval(extract('timeFilterBounds'));eval(extract('filterSubagentInventory'));
const result=filterSubagentInventory({{inventory_count:1001,inventory_truncated:true,inventory:[{{id:'a',root_session_id:'root',runtime:'codex',label:'Agent A',activity_state:'complete',last_activity_at:100,attention:[]}}]}},{{query:'agent',runtime:'',project:'',model:'',status:'all',signal:'all',window:'all',sort:'recent'}},200);
process.stdout.write(JSON.stringify(result));
"""
        payload = json.loads(subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        ).stdout)
        self.assertFalse(payload["summary"]["complete"])
        self.assertIsNone(payload["summary"]["agents"])
        self.assertEqual(payload["inventoryCount"], 1001)
        self.assertEqual(len(payload["rows"]), 1)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_subagent_triage_preserves_context_and_pages_large_results(self):
        script = f"""
const fs=require('fs');
const page=fs.readFileSync({json.dumps(str(Path(DASHBOARD_PAGE)))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
eval(extract('applySubagentPreset'));
eval(extract('subagentPresetForFilters'));
eval(extract('paginateSubagentRows'));
const base={{query:'reviewer',runtime:'codex',project:'/token-meter',model:'gpt-5',status:'complete',signal:'retry_pressure',window:'7d',sort:'cost'}};
const incomplete=applySubagentPreset(base,'incomplete');
const attention=applySubagentPreset(base,'attention');
const all=applySubagentPreset(base,'all');
const rows=Array.from({{length:123}},(_,id)=>({{id}}));
process.stdout.write(JSON.stringify({{
 incomplete,attention,all,
 presets:[subagentPresetForFilters(incomplete),subagentPresetForFilters(attention),subagentPresetForFilters(all),subagentPresetForFilters(base)],
 first:paginateSubagentRows(rows,50),
 complete:paginateSubagentRows(rows,150),
}}));
"""
        payload = json.loads(subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        ).stdout)

        for state in (payload["incomplete"], payload["attention"], payload["all"]):
            self.assertEqual(state["runtime"], "codex")
            self.assertEqual(state["project"], "/token-meter")
            self.assertEqual(state["window"], "7d")
            self.assertEqual(state["query"], "reviewer")
            self.assertEqual(state["model"], "gpt-5")
            self.assertEqual(state["sort"], "cost")
        self.assertEqual(payload["incomplete"]["status"], "incomplete")
        self.assertEqual(payload["incomplete"]["signal"], "all")
        self.assertEqual(payload["attention"]["status"], "all")
        self.assertEqual(payload["attention"]["signal"], "attention")
        self.assertEqual(payload["all"]["status"], "all")
        self.assertEqual(payload["all"]["signal"], "all")
        self.assertEqual(payload["presets"], [
            "incomplete", "attention", "all", "custom",
        ])
        self.assertEqual(len(payload["first"]["rows"]), 50)
        self.assertEqual(payload["first"]["shown"], 50)
        self.assertEqual(payload["first"]["total"], 123)
        self.assertTrue(payload["first"]["hasMore"])
        self.assertEqual(payload["first"]["nextLimit"], 100)
        self.assertEqual(len(payload["complete"]["rows"]), 123)
        self.assertFalse(payload["complete"]["hasMore"])
        self.assertEqual(payload["complete"]["nextLimit"], 123)

    def test_subagent_explorer_has_progressive_filters_and_evidence_inspector(self):
        for expected in (
            "id=subagent-view-tabs",
            "aria-controls=subagent-results-panel",
            "data-subagent-view=issues",
            "data-subagent-view=sessions",
            "data-subagent-view=roles",
            "id=subagent-filter-more",
            "id=subagent-filter-advanced",
            "id=subagent-active-filters",
            "id=subagent-workspace",
            "id=subagent-results",
            "id=subagent-results-panel role=tabpanel",
            "id=subagent-inspector",
            "data-subagent-open-parent",
            "data-subagent-show-session",
            "subagentWorkspace.roleView",
            "expandedSubagentRoot=root",
            "subagentInspector",
        ):
            self.assertIn(expected, self.page)
        for removed in (
            "id=subagent-triage",
            "id=subagent-insights",
            "id=subagent-explorer-summary",
            "id=subagent-inventory-more",
        ):
            self.assertNotIn(removed, self.page)
        self.assertIn("id=view-subagents", self.page)
        self.assertIn("function renderSubagentInspector(", self.page)
        self.assertIn("const incompletePill=compact('Incomplete'", self.page)
        self.assertNotIn("compact('Needs review'", self.page)

    def test_subagent_workspace_orders_roles_sessions_issues_and_defaults_to_roles(self):
        tablist_start = self.page.index('id=subagent-view-tabs')
        tablist_end = self.page.index('</div>', tablist_start)
        tablist = self.page[tablist_start:tablist_end]
        self.assertLess(tablist.index('data-subagent-view=roles'), tablist.index('data-subagent-view=sessions'))
        self.assertLess(tablist.index('data-subagent-view=sessions'), tablist.index('data-subagent-view=issues'))
        self.assertIn('data-subagent-view=roles aria-selected=true', tablist)
        self.assertIn("localStorage.getItem('tm_subagent_view')||'roles'", self.page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_subagent_work_sort_uses_user_facing_label_and_migrates_elapsed_preference(self):
        script = f"""
const fs=require('fs');
const page=fs.readFileSync({json.dumps(str(Path(DASHBOARD_PAGE)))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
const stored=new Map([['tm_subagent_filter_sort','elapsed']]),writes=[];
const localStorage={{getItem:key=>stored.get(key)??null,setItem:(key,value)=>{{stored.set(key,String(value));writes.push([key,String(value)]);}}}};
let subagentFilters;
eval(page.slice(page.indexOf('const TIME_FILTER_WINDOWS='),page.indexOf('function normalizeTimeFilterWindow(')).replace(/^const /gm,'var '));
const initStart=page.indexOf('let subagentFilters={{');
const initEnd=page.indexOf('const SUBAGENT_PAGE_SIZE',initStart);
eval(page.slice(initStart,initEnd).replace('let subagentFilters=','subagentFilters='));
const subagentFilterDefaults={{query:'',role:'',kind:'',runtime:'',project:'',model:'',status:'all',signal:'all',window:DEFAULT_TIME_FILTER_WINDOW,sort:'recent'}};
eval(extract('normalizedSubagentNavigationState'));
eval(extract('subagentActiveFilterItems'));
const restored=normalizedSubagentNavigationState('sessions',{{sort:'elapsed'}});
const chip=subagentActiveFilterItems({{...subagentFilterDefaults,sort:'work'}}).find(item=>item.key==='sort');
process.stdout.write(JSON.stringify({{saved:subagentFilters.sort,stored:stored.get('tm_subagent_filter_sort'),writes,restored:restored.filters.sort,chip}}));
"""
        payload = json.loads(subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        ).stdout)

        self.assertEqual(payload["saved"], "work")
        self.assertEqual(payload["stored"], "work")
        self.assertIn(["tm_subagent_filter_sort", "work"], payload["writes"])
        self.assertEqual(payload["restored"], "work")
        self.assertEqual(payload["chip"], {
            "key": "sort", "label": "Sort", "value": "Work time",
        })

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_subagent_time_filter_defaults_to_thirty_days_and_labels_all_history(self):
        script = f"""
const fs=require('fs');
const page=fs.readFileSync({json.dumps(str(Path(DASHBOARD_PAGE)))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
eval(page.slice(page.indexOf('const TIME_FILTER_WINDOWS='),page.indexOf('function normalizeTimeFilterWindow(')).replace(/^const /gm,'var '));
const initStart=page.indexOf('let subagentFilters={{'),initEnd=page.indexOf('const SUBAGENT_PAGE_SIZE',initStart);
const defaultsLine=page.slice(page.indexOf('const subagentFilterDefaults='),page.indexOf('function persistSubagentFilter(')).replace(/^const /,'var ');
const persistSource=extract('persistSubagentFilter'),chipsSource=extract('subagentActiveFilterItems');
function load(entries){{
 const stored=new Map(entries);
 const localStorage={{getItem:key=>stored.get(key)??null,setItem:(key,value)=>stored.set(key,String(value)),removeItem:key=>stored.delete(key)}};
 let subagentFilters;
 eval(page.slice(initStart,initEnd).replace('let subagentFilters=','subagentFilters='));
 eval(defaultsLine);
 const subagentFilterStorage={{window:'tm_subagent_filter_time'}};
 eval(persistSource);
 const loaded=subagentFilters.window;
 subagentFilters.window='all';persistSubagentFilter('window');const keptAll=stored.get('tm_subagent_filter_time')??null;
 subagentFilters.window='30d';persistSubagentFilter('window');const atDefault=stored.get('tm_subagent_filter_time')??null;
 return {{loaded,keptAll,atDefault,defaults:subagentFilterDefaults}};
}}
const fresh=load([]),bogus=load([['tm_subagent_filter_time','bogus']]),saved=load([['tm_subagent_filter_time','all']]);
const subagentFilterDefaults=fresh.defaults;
const appFilterLabel=row=>row.provider,projectFilterLabel=value=>value;
eval(chipsSource);
const chip=window=>subagentActiveFilterItems({{...subagentFilterDefaults,window}}).find(item=>item.key==='window')||null;
process.stdout.write(JSON.stringify({{
 fresh:fresh.loaded,bogus:bogus.loaded,saved:saved.loaded,keptAll:fresh.keptAll,atDefault:fresh.atDefault,
 defaultChip:chip('30d'),allChip:chip('all'),sevenChip:chip('7d'),
}}));
"""
        payload = json.loads(subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        ).stdout)
        self.assertEqual(payload["fresh"], "30d")
        self.assertEqual(payload["bogus"], "30d")
        self.assertEqual(payload["saved"], "all")
        self.assertEqual(payload["keptAll"], "all")
        self.assertIsNone(payload["atDefault"])
        self.assertIsNone(payload["defaultChip"])
        self.assertEqual(payload["allChip"], {"key": "window", "label": "Time", "value": "All history"})
        self.assertEqual(payload["sevenChip"], {"key": "window", "label": "Time", "value": "7 days"})

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_subagent_summary_tags_toggle_incomplete_and_attention_filters(self):
        script = f"""
const fs=require('fs');
const page=fs.readFileSync({json.dumps(str(Path(DASHBOARD_PAGE)))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)return null;let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}return null;}}
const source=extract('toggleSubagentSummaryFilter');
if(!source)process.stdout.write(JSON.stringify({{missing:true}}));
else{{
 eval(source);
 const start={{query:'reviewer',runtime:'codex',project:'/token-meter',model:'gpt',status:'all',signal:'all',window:'7d',sort:'cost'}};
 process.stdout.write(JSON.stringify({{
  missing:false,
  incomplete:toggleSubagentSummaryFilter(start,'incomplete'),
  incompleteOff:toggleSubagentSummaryFilter(toggleSubagentSummaryFilter(start,'incomplete'),'incomplete'),
  attention:toggleSubagentSummaryFilter(start,'attention'),
  outlier:toggleSubagentSummaryFilter(start,'peer_cost_outlier'),
 }}));
}}
"""
        payload = json.loads(subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        ).stdout)

        self.assertFalse(payload.get("missing", False))
        self.assertEqual(payload["incomplete"]["status"], "incomplete")
        self.assertEqual(payload["incomplete"]["signal"], "all")
        self.assertEqual(payload["incompleteOff"]["status"], "all")
        self.assertEqual(payload["attention"]["signal"], "attention")
        self.assertEqual(payload["attention"]["status"], "all")
        self.assertEqual(payload["outlier"]["signal"], "peer_cost_outlier")
        self.assertEqual(payload["outlier"]["query"], "reviewer")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_subagent_role_drilldown_adds_restorable_browser_history(self):
        script = f"""
const fs=require('fs');
const page=fs.readFileSync({json.dumps(str(Path(DASHBOARD_PAGE)))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
const calls=[];
const history={{state:{{sessionScope:'all'}},replaceState(state,title,url){{this.state=state;calls.push(['replace',state.subagent,url]);}},pushState(state,title,url){{this.state=state;location.href=String(url);location.hash='#'+String(url).split('#')[1];calls.push(['push',state.subagent,url]);}}}};
const location={{href:'http://127.0.0.1:8722/#subagents',hash:'#subagents'}};
const localStorage={{setItem(){{}},removeItem(){{}}}};
const subagentFilterDefaults={{query:'',runtime:'',project:'',model:'',status:'all',signal:'all',window:'all',sort:'recent'}};
let subagentView='roles',subagentFilters={{...subagentFilterDefaults,project:'/token-meter',window:'30d'}},selectedSubagentId='child',expandedSubagentRoot='root';
const persistSubagentFilter=()=>{{}},resetSubagentResultView=()=>{{}};
eval(['normalizedSubagentNavigationState','restoreSubagentNavigationState','openSubagentRoleRuns'].map(extract).join('\\n'));
openSubagentRoleRuns('token_meter_reviewer','codex','spawned');
const drilldown={{view:subagentView,filters:subagentFilters}};
restoreSubagentNavigationState(calls[0][1]);
process.stdout.write(JSON.stringify({{calls,drilldown,restored:{{view:subagentView,filters:subagentFilters}},sessionScope:history.state.sessionScope}}));
"""
        completed = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)

        self.assertEqual([call[0] for call in payload["calls"]], ["replace", "push"])
        self.assertEqual(payload["calls"][0][1]["view"], "roles")
        self.assertEqual(payload["calls"][0][1]["filters"]["project"], "/token-meter")
        self.assertEqual(payload["drilldown"]["view"], "sessions")
        self.assertEqual(payload["calls"][1][2], "/#sessions-subagents")
        self.assertEqual(payload["drilldown"]["filters"]["role"], "token_meter_reviewer")
        self.assertEqual(payload["drilldown"]["filters"]["kind"], "spawned")
        self.assertEqual(payload["restored"]["view"], "roles")
        self.assertEqual(payload["restored"]["filters"]["window"], "30d")
        self.assertEqual(payload["sessionScope"], "all")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_subagents_have_a_primary_page_and_canonical_return_route(self):
        self.assertIn("id=tab-subagents", self.page)
        self.assertIn("id=view-subagents", self.page)
        self.assertIn("id=session-scope-subagents", self.page)
        script = f"""
const fs=require('fs');
const page=fs.readFileSync({json.dumps(str(Path(DASHBOARD_PAGE)))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
const SESSION_SCOPES=['current','all','subagents'];
eval(extract('normalizeSessionScope'));
eval(extract('sessionScopeRoute'));
process.stdout.write(JSON.stringify({{subagents:sessionScopeRoute('subagents'),all:sessionScopeRoute('all'),current:sessionScopeRoute('current')}}));
"""
        payload = json.loads(subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        ).stdout)
        self.assertEqual(payload, {
            "subagents": "sessions-subagents", "all": "sessions-all", "current": "sessions",
        })

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_subagent_role_runs_model_distribution_is_exact_and_runtime_scoped(self):
        script = f"""
const fs=require('fs');
const page=fs.readFileSync({json.dumps(str(Path(DASHBOARD_PAGE)))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
eval(extract('timeFilterBounds'));eval(extract('filterSubagentInventory'));
eval(extract('subagentModelDistribution'));
const rows=[
 {{id:'1',runtime:'codex',kind:'spawned',role:'token_meter_reviewer',model:'gpt-x',cost:2,cost_available:true}},
 {{id:'2',runtime:'codex',kind:'spawned',role:'token_meter_reviewer',model:'gpt-x',cost_available:false}},
 {{id:'3',runtime:'claude',kind:'spawned',role:'token_meter_reviewer',model:'gpt-x',cost:3,cost_available:true}},
 {{id:'4',runtime:'codex',role:'tester',label:'token_meter_reviewer helper',model:'gpt-y',cost:9,cost_available:true}},
 {{id:'5',runtime:'codex',kind:'internal',role:'token_meter_reviewer',model:'gpt-y',cost:11,cost_available:true}},
];
const result=filterSubagentInventory({{inventory:rows}},{{role:'token_meter_reviewer',kind:'spawned',status:'all',signal:'all',window:'all',sort:'recent'}});
process.stdout.write(JSON.stringify({{ids:result.rows.map(row=>row.id),models:subagentModelDistribution(result.rows)}}));
"""
        completed = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["ids"], ["1", "2", "3"])
        self.assertEqual(len(payload["models"]), 2)
        self.assertEqual(payload["models"][0]["runtime"], "codex")
        self.assertEqual(payload["models"][0]["runs"], 2)
        self.assertEqual(payload["models"][0]["costCovered"], 1)
        self.assertEqual(payload["models"][0]["cost"], 2)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_subagent_session_rows_keep_spawned_run_count(self):
        script = f"""
const fs=require('fs');
const page=fs.readFileSync({json.dumps(str(Path(DASHBOARD_PAGE)))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
const selectedSubagentId='',expandedSubagentRoot='not-this-root';
const esc=value=>String(value),f=value=>String(value),countWord=(value,word)=>value===1?word:`${{word}}s`,money=value=>`$${{Number(value).toFixed(2)}}`,compactNumber=value=>String(value),currentSessionAge=()=> 'now',durationShort=value=>`${{value}}s`;
const agentIdentityPresentation=(row)=>({{primary:row.role||row.label,secondary:''}}),subagentParentPresentation=()=>({{title:'Parent run',project:'token-meter'}});
eval(extract('groupSubagentSessions'));
eval(extract('subagentCostHeat'));
eval(extract('subagentGroupHtml'));
const rows=[
 {{id:'a',role:'reviewer',kind:'spawned',activity_state:'complete',model:'gpt',cost:1,cost_available:true,tokens:10,tokens_available:true,work_time_s:600,attention:[]}},
 {{id:'b',role:'tester',kind:'spawned',activity_state:'incomplete',model:'gpt',cost:2,cost_available:true,tokens:20,tokens_available:true,work_time_s:null,attention:[]}},
];
rows.forEach(row=>{{row.root_session_id='root';row.project='/token-meter';}});
const html=subagentGroupHtml(groupSubagentSessions(rows,false,'work')[0],0,new Map(),false);
process.stdout.write(JSON.stringify({{html}}));
"""
        payload = json.loads(subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        ).stdout)
        self.assertIn("Spawned runs", payload["html"])
        self.assertIn("<b>2</b>", payload["html"])
        self.assertIn("Work time", payload["html"])
        self.assertIn("600s", payload["html"])
        self.assertIn("work time --", payload["html"])
        self.assertIn("subagentCostHeat", payload["html"])
        self.assertIn("--cost-fill:100%", payload["html"])
        self.assertNotIn("Need review", payload["html"])
        self.assertNotIn("Needs attention", payload["html"])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_subagent_cost_color_is_relative_and_unavailable_cost_is_neutral(self):
        script = f"""
const fs=require('fs');
const page=fs.readFileSync({json.dumps(str(Path(DASHBOARD_PAGE)))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
eval(extract('subagentCostHeat'));
process.stdout.write(JSON.stringify({{low:subagentCostHeat(1,10,true),high:subagentCostHeat(10,10,true),missing:subagentCostHeat(null,10,false)}}));
"""
        completed = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertIn("--cost-fill:10%", result["low"])
        self.assertIn("--cost-fill:100%", result["high"])
        self.assertNotEqual(result["low"], result["high"])
        self.assertEqual(result["missing"], "")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_role_name_search_keeps_exact_cost_trend_and_comparison(self):
        script = f"""
const fs=require('fs');
const page=fs.readFileSync({json.dumps(str(Path(DASHBOARD_PAGE)))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
eval(['selectSubagentUsageScope','subagentRoleKey','subagentRoleFineFilters','subagentRoleDayRows','buildSubagentRoleEconomics','buildSubagentModelTrends','subagentModelDayRows','subagentModelKey'].map(extract).join('\\n'));
const reviewer={{runtime:'codex',kind:'spawned',role:'token_meter_reviewer',agents:2,known_cost:4,cost_available:true,cost_covered_agents:2}};
const tester={{runtime:'codex',kind:'spawned',role:'token_meter_tester',agents:1,known_cost:9,cost_available:true,cost_covered_agents:1}};
const usage={{scopes:[{{runtime:'',project:'',window:'7d',roles:[reviewer,tester],comparison:{{roles:[{{...reviewer,agents:1,known_cost:3,cost_covered_agents:1}},tester]}}}}],role_days:[{{...reviewer,day:'2026-09-27'}},{{...tester,day:'2026-09-27'}}]}};
const filters={{query:'reviewer',runtime:'',project:'',window:'7d',model:'',status:'all',signal:'all'}};
process.stdout.write(JSON.stringify(buildSubagentRoleEconomics(usage,filters,Date.parse('2026-09-28T12:00:00'))));
"""
        completed = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertIsNone(result["reason"])
        self.assertEqual(result["runs"], 2)
        self.assertEqual(result["cost"], 4)
        self.assertEqual(result["previousCost"], 3)
        self.assertEqual([row["role"] for row in result["roles"]], ["token_meter_reviewer"])
        self.assertEqual([row["role"] for row in result["days"]], ["token_meter_reviewer"])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_subagent_role_table_reports_total_covered_spend(self):
        script = f"""
const fs=require('fs');
const page=fs.readFileSync({json.dumps(str(Path(DASHBOARD_PAGE)))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
const esc=value=>String(value),f=value=>String(value),pct=value=>`${{Math.round(value*100)}}%`,money=value=>`$${{Number(value).toFixed(2)}}`,compactNumber=value=>String(value),appFilterLabel=({{provider}})=>provider;
eval(extract('buildSubagentRoleRows'));
eval(extract('renderSubagentRoles'));
const html=renderSubagentRoles([
 {{runtime:'codex',kind:'spawned',role:'token_meter_reviewer',model:'gpt',activity_state:'complete',cost:1,cost_available:true,tokens:100,tokens_available:true,attention:[]}},
 {{runtime:'codex',kind:'spawned',role:'token_meter_reviewer',model:'gpt',activity_state:'complete',cost:3,cost_available:true,tokens:200,tokens_available:true,attention:[]}},
 {{runtime:'codex',kind:'spawned',role:'token_meter_tester',model:'gpt',activity_state:'complete',cost:null,cost_available:false,tokens:50,tokens_available:true,attention:[]}},
]);
process.stdout.write(JSON.stringify({{html}}));
"""
        payload = json.loads(subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        ).stdout)
        self.assertIn("Total spend", payload["html"])
        self.assertIn("$4.00", payload["html"])
        self.assertIn("token_meter_tester", payload["html"])
        self.assertIn("<b>--</b>", payload["html"])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_subagent_role_economics_compares_spend_volume_and_cost_per_run(self):
        script = f"""
const fs=require('fs');
const page=fs.readFileSync({json.dumps(str(Path(DASHBOARD_PAGE)))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
eval(['selectSubagentUsageScope','subagentRoleKey','subagentRoleFineFilters','subagentRoleDayRows','buildSubagentRoleEconomics','buildSubagentModelTrends','subagentModelDayRows','subagentModelKey'].map(extract).join('\\n'));
const current=[
 {{id:'reviewer::codex::spawned',runtime:'codex',kind:'spawned',role:'reviewer',agents:2,known_cost:6,cost:6,cost_available:true,cost_covered_agents:2,known_tokens:600,tokens:600,tokens_available:true,token_covered_agents:2,median_cost:3,p95_cost:4,incomplete_agents:1,attention_agents:1}},
 {{id:'tester::codex::spawned',runtime:'codex',kind:'spawned',role:'tester',agents:1,known_cost:2,cost:2,cost_available:true,cost_covered_agents:1,known_tokens:200,tokens:200,tokens_available:true,token_covered_agents:1,median_cost:2,p95_cost:2,incomplete_agents:0,attention_agents:0}},
];
const previous=[
 {{id:'reviewer::codex::spawned',runtime:'codex',kind:'spawned',role:'reviewer',agents:1,known_cost:8,cost:8,cost_available:true,cost_covered_agents:1,median_cost:8,p95_cost:8,incomplete_agents:0,attention_agents:0}},
];
const usage={{scopes:[{{window:'7d',runtime:'codex',project:'/repo',roles:current,comparison:{{roles:previous}}}}],role_day_count:2,role_days_truncated:false,role_days:[
 {{day:'2026-09-24',project:'/repo',runtime:'codex',kind:'spawned',role:'reviewer',agents:2,known_cost:6,cost_available:true,cost_covered_agents:2}},
 {{day:'2026-09-23',project:'/repo',runtime:'codex',kind:'spawned',role:'tester',agents:1,known_cost:2,cost_available:true,cost_covered_agents:1}},
]}};
const base={{query:'',runtime:'codex',project:'/repo',model:'',status:'all',signal:'all',window:'7d',sort:'recent'}};
const result=buildSubagentRoleEconomics(usage,base,Date.parse('2026-09-24T12:00:00'));
const partial=buildSubagentRoleEconomics({{...usage,scopes:[{{...usage.scopes[0],roles:[{{...current[0],known_cost:3,cost:null,cost_available:false,cost_covered_agents:1}}]}}]}},base,Date.parse('2026-09-24T12:00:00'));
const uncovered=buildSubagentRoleEconomics({{...usage,scopes:[{{...usage.scopes[0],roles:[{{...current[0],known_cost:0,cost:null,cost_available:false,cost_covered_agents:0}}]}}]}},base,Date.parse('2026-09-24T12:00:00'));
const filtered=buildSubagentRoleEconomics(usage,{{...base,status:'incomplete'}},Date.parse('2026-09-24T12:00:00'));
process.stdout.write(JSON.stringify({{result,partial,uncovered,filtered}}));
"""
        completed = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)

        result = payload["result"]
        self.assertEqual(result["runs"], 3)
        self.assertEqual(result["cost"], 8)
        self.assertAlmostEqual(result["averageCost"], 8 / 3)
        self.assertEqual(result["previousRuns"], 1)
        self.assertEqual(result["previousCost"], 8)
        self.assertEqual(result["costChange"], 0)
        self.assertAlmostEqual(result["averageCostChange"], -2 / 3)
        reviewer = next(row for row in result["roles"] if row["role"] == "reviewer")
        self.assertEqual(reviewer["costChange"], -0.25)
        self.assertEqual(reviewer["runChange"], 1)
        self.assertEqual(payload["partial"]["cost"], 3)
        self.assertEqual(payload["partial"]["costCovered"], 1)
        self.assertEqual(payload["partial"]["averageCost"], 3)
        self.assertIsNone(payload["partial"]["costChange"])
        self.assertIsNone(payload["partial"]["averageCostChange"])
        self.assertIsNone(payload["uncovered"]["cost"])
        self.assertIsNone(payload["uncovered"]["averageCost"])
        self.assertEqual(payload["filtered"]["reason"], "filtered")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_subagent_role_economics_renders_a_clear_chart_for_each_role(self):
        script = f"""
const fs=require('fs');
const page=fs.readFileSync({json.dumps(str(Path(DASHBOARD_PAGE)))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
const esc=value=>String(value),f=value=>String(value),pct=value=>`${{Math.round(value*100)}}%`,money=value=>`$${{Number(value).toFixed(2)}}`,appFilterLabel=({{provider}})=>provider;
let subagentRoleChartMode='average';
eval(['subagentRoleKey','renderSubagentRoleSparkline','renderSubagentRoleEconomics','renderSubagentTrendSparkline','renderSubagentModelTrends','subagentModelKey'].map(extract).join('\\n'));
const economics={{reason:null,window:'7d',runs:3,cost:8,averageCost:8/3,costCovered:3,previousRuns:1,previousCost:8,costChange:0,averageCostChange:-2/3,runChange:2,incomplete:1,attention:1,previousIncomplete:0,roles:[
 {{id:'reviewer::codex::spawned',runtime:'codex',kind:'spawned',role:'reviewer',agents:2,known_cost:6,cost:6,cost_available:true,cost_covered_agents:2,known_tokens:600,tokens_available:true,token_covered_agents:2,median_cost:3,p95_cost:4,incomplete_agents:1,attention_agents:1,costChange:-.25,runChange:1,averageCostChange:-.625}},
 {{id:'tester::codex::spawned',runtime:'codex',kind:'spawned',role:'tester',agents:1,known_cost:2,cost:2,cost_available:true,cost_covered_agents:1,known_tokens:200,tokens_available:true,token_covered_agents:1,median_cost:2,p95_cost:2,incomplete_agents:0,attention_agents:0,costChange:.2,runChange:0,averageCostChange:.2}},
],days:[
 {{day:'2026-09-23',runtime:'codex',kind:'spawned',role:'reviewer',agents:1,known_cost:4,cost_available:true,cost_covered_agents:1}},
 {{day:'2026-09-24',runtime:'codex',kind:'spawned',role:'reviewer',agents:2,known_cost:6,cost_available:true,cost_covered_agents:2}},
 {{day:'2026-09-23',runtime:'codex',kind:'spawned',role:'tester',agents:1,known_cost:1,cost_available:true,cost_covered_agents:1}},
 {{day:'2026-09-24',runtime:'codex',kind:'spawned',role:'tester',agents:1,known_cost:2,cost_available:true,cost_covered_agents:1}},
]}};
const partial=renderSubagentRoleEconomics({{...economics,runs:2,cost:3,averageCost:3,costCovered:1,costChange:null,averageCostChange:null}});
const allHistory=renderSubagentRoleEconomics({{...economics,window:'all',previousRuns:null,previousCost:null,costChange:null,averageCostChange:null,runChange:null,roles:economics.roles.map(row=>({{...row,costChange:null,averageCostChange:null,runChange:null}}))}});
process.stdout.write(JSON.stringify({{html:renderSubagentRoleEconomics(economics),partial,allHistory}}));
"""
        completed = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)

        html = payload["html"]
        self.assertIn("Role trends", html)
        self.assertIn("Cost / run", html)
        self.assertEqual(html.count("<svg"), 2)
        self.assertIn('aria-label="reviewer cost per run trend"', html)
        self.assertIn('aria-label="tester cost per run trend"', html)
        self.assertLess(html.index('subagentRoleKpi'), html.index('Role spend'))
        self.assertLess(html.index('Cost / run'), html.index('Role spend'))
        self.assertIn('2026-09-23', html)
        self.assertIn('2026-09-24', html)
        self.assertNotIn('>1 review<', html)
        self.assertIn("data-subagent-inspect-role=", html)
        self.assertIn("reviewer", html)
        self.assertIn("tester", html)
        self.assertIn("View runs", html)
        self.assertNotIn("What changed", html)
        self.assertNotIn("Role spend over time", html)
        self.assertIn("Covered role spend", payload["partial"])
        self.assertIn("Cost / covered run", payload["partial"])
        self.assertIn("$3.00", payload["partial"])
        self.assertIn("1 / 2 cost-covered", payload["partial"])
        self.assertIn("Named runs", payload["partial"])
        self.assertNotIn("No comparable baseline", payload["allHistory"])
        self.assertNotIn("<small>--</small>", payload["allHistory"])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_subagent_role_economics_withholds_truncated_period_insights(self):
        script = f"""
const fs=require('fs');
const page=fs.readFileSync({json.dumps(str(Path(DASHBOARD_PAGE)))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
const esc=value=>String(value),f=value=>String(value),pct=value=>`${{Math.round(value*100)}}%`,money=value=>`$${{Number(value).toFixed(2)}}`,appFilterLabel=({{provider}})=>provider;
let subagentRoleChartMode='spend';
eval(['subagentRoleKey','renderSubagentRoleSparkline','renderSubagentRoleEconomics','renderSubagentTrendSparkline','renderSubagentModelTrends','subagentModelKey'].map(extract).join('\\n'));
const economics={{reason:null,window:'7d',runs:2,cost:6,averageCost:3,costCovered:2,previousRuns:1,previousCost:2,costChange:2,averageCostChange:.5,runChange:1,incomplete:0,attention:0,previousIncomplete:0,trendTruncated:true,roles:[
 {{runtime:'codex',kind:'spawned',role:'reviewer',agents:2,known_cost:6,cost_available:true,cost_covered_agents:2,median_cost:3,p95_cost:4,incomplete_agents:0,attention_agents:0,costChange:2,runChange:1}},
],days:[{{day:'2026-09-24',runtime:'codex',kind:'spawned',role:'reviewer',agents:2,known_cost:6,cost_available:true}}]}};
const spend=renderSubagentRoleEconomics(economics);
subagentRoleChartMode='runs';
const runs=renderSubagentRoleEconomics(economics);
process.stdout.write(JSON.stringify({{spend,runs}}));
"""
        completed = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        for html in payload.values():
            self.assertIn("Trend unavailable", html)
            self.assertIn("daily history was truncated", html)
            self.assertNotIn("What changed", html)
            self.assertNotIn("Higher spend for reviewer", html)
        self.assertNotIn("+1 vs prior", payload["runs"])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_subagent_role_charts_do_not_hide_lower_spend_roles(self):
        script = f"""
const fs=require('fs');
const page=fs.readFileSync({json.dumps(str(Path(DASHBOARD_PAGE)))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
const esc=value=>String(value);
let subagentRoleChartMode='spend';
eval(['subagentRoleKey','renderSubagentRoleSparkline','renderSubagentTrendSparkline'].map(extract).join('\\n'));
const roles=Array.from({{length:6}},(_,index)=>({{runtime:'codex',kind:'spawned',role:`role_${{index+1}}`}}));
const days=roles.map((row,index)=>({{day:'2026-09-24',...row,agents:1,known_cost:6-index,cost_available:true}}));
process.stdout.write(roles.map(row=>renderSubagentRoleSparkline({{trendTruncated:false,days}},row)).join(''));
"""
        completed = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.count("<svg"), 6)
        self.assertIn('aria-label="role_5 spend trend"', completed.stdout)
        self.assertIn('aria-label="role_6 spend trend"', completed.stdout)
        self.assertNotIn("Other roles", completed.stdout)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_subagent_workspace_groups_parent_sessions_and_prioritizes_issues(self):
        script = f"""
const fs=require('fs');
const page=fs.readFileSync({json.dumps(str(Path(DASHBOARD_PAGE)))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
eval(extract('groupSubagentSessions'));
const rows=[
 {{id:'quiet',root_session_id:'root-quiet',project:'/quiet',activity_state:'complete',last_activity_at:300,cost:10,cost_available:true,attention:[]}},
 {{id:'incomplete',root_session_id:'root-incomplete',project:'/work',activity_state:'incomplete',last_activity_at:200,cost:4,cost_available:true,attention:[]}},
 {{id:'attention-2',root_session_id:'root-attention',project:'/work',activity_state:'complete',last_activity_at:90,cost:null,cost_available:false,attention:[]}},
 {{id:'attention',root_session_id:'root-attention',project:'/work',activity_state:'complete',last_activity_at:100,cost:1,cost_available:true,attention:[{{code:'peer_cost_outlier'}}]}},
];
process.stdout.write(JSON.stringify({{issues:groupSubagentSessions(rows,true),sessions:groupSubagentSessions(rows,false)}}));
"""
        payload = json.loads(subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        ).stdout)

        self.assertEqual(
            [row["rootSessionId"] for row in payload["issues"]],
            ["root-attention", "root-incomplete"],
        )
        self.assertEqual(len(payload["sessions"]), 3)
        attention = payload["issues"][0]
        self.assertEqual(attention["agents"], 2)
        self.assertEqual(attention["attention"], 1)
        self.assertEqual(attention["incomplete"], 0)
        self.assertEqual(attention["costCovered"], 1)
        self.assertEqual(attention["cost"], 1)
        self.assertEqual(
            [row["id"] for row in attention["rows"]],
            ["attention", "attention-2"],
        )

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_subagent_roles_preserve_runtime_kind_and_calculate_review_metrics(self):
        script = f"""
const fs=require('fs');
const page=fs.readFileSync({json.dumps(str(Path(DASHBOARD_PAGE)))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
eval(extract('buildSubagentRoleRows'));
const rows=[
 {{id:'a',runtime:'codex',kind:'spawned',role:'token_meter_reviewer',model:'gpt-a',activity_state:'complete',cost:1,cost_available:true,tokens:100,tokens_available:true,attention:[]}},
 {{id:'b',runtime:'codex',kind:'spawned',role:'token_meter_reviewer',model:'gpt-a',activity_state:'incomplete',cost:3,cost_available:true,tokens:300,tokens_available:true,attention:[{{code:'retry_pressure'}}]}},
 {{id:'c',runtime:'claude',kind:'spawned',role:'token_meter_reviewer',model:'opus',activity_state:'complete',cost:8,cost_available:true,tokens:800,tokens_available:true,attention:[]}},
 {{id:'d',runtime:'codex',kind:'spawned',role:null,model:'gpt-a',activity_state:'complete',cost:5,cost_available:true,tokens:500,tokens_available:true,attention:[]}},
];
process.stdout.write(JSON.stringify(buildSubagentRoleRows(rows)));
"""
        payload = json.loads(subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        ).stdout)

        self.assertEqual(len(payload), 2)
        claude, codex = payload
        self.assertEqual((claude["runtime"], claude["role"]), ("claude", "token_meter_reviewer"))
        self.assertEqual((codex["runtime"], codex["kind"]), ("codex", "spawned"))
        self.assertEqual(codex["agents"], 2)
        self.assertEqual(codex["incomplete"], 1)
        self.assertEqual(codex["attention"], 1)
        self.assertEqual(codex["incompleteRate"], 0.5)
        self.assertEqual(codex["attentionRate"], 0.5)
        self.assertEqual(codex["medianCost"], 2)
        self.assertEqual(codex["p95Cost"], 3)
        self.assertEqual(codex["knownTokens"], 400)
        self.assertEqual(codex["topModel"], "gpt-a")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_subagent_usage_scope_supports_project_filters_and_blocks_text_search(self):
        script = f"""
const fs=require('fs');
const page=fs.readFileSync({json.dumps(str(Path(DASHBOARD_PAGE)))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
eval(extract('selectSubagentUsageScope'));
const usage={{totals:{{agents:9}},scope_truncated:false,scopes:[
 {{window:'all',runtime:'',project:'',totals:{{agents:9}}}},
 {{window:'7d',runtime:'codex',project:'~/Documents/github/token-meter',totals:{{agents:2}}}},
]}};
process.stdout.write(JSON.stringify({{
 supported:selectSubagentUsageScope(usage,'codex','7d','~/Documents/github/token-meter',false),
 blocked:selectSubagentUsageScope(usage,'codex','7d','~/Documents/github/token-meter',true),
 missing:selectSubagentUsageScope(usage,'codex','7d','~/Documents/github/luna-3',false),
 truncated:selectSubagentUsageScope({{...usage,scope_truncated:true}},'codex','7d','~/Documents/github/luna-3',false),
}}));
"""

        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        payload = json.loads(result.stdout)
        self.assertIsNotNone(payload["supported"]["usage"])
        self.assertEqual(payload["supported"]["usage"]["totals"]["agents"], 2)
        self.assertIsNone(payload["supported"]["reason"])
        self.assertIsNone(payload["blocked"]["usage"])
        self.assertEqual(payload["blocked"]["reason"], "filtered")
        self.assertIsNone(payload["missing"]["usage"])
        self.assertEqual(payload["missing"]["reason"], "empty")
        self.assertIsNone(payload["truncated"]["usage"])
        self.assertEqual(payload["truncated"]["reason"], "unavailable")


if __name__ == "__main__":
    unittest.main()
