import unittest
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from unittest import mock
import meter
from tests.dashboard_page import DASHBOARD_PAGE
from tests.dashboard_layout_support import DashboardLayoutCase


class DashboardShellLayoutTests(DashboardLayoutCase):
    def test_dashboard_uses_splunk_favicon_and_bundled_wordmark(self):
        self.assertIn('rel=icon type="image/svg+xml"', self.page)
        self.assertIn("data:image/svg+xml", self.page)
        self.assertIn("M47.03 36.16v-7.58L10 10", self.page)
        self.assertIn('class=logo role=img aria-label="Splunk by Cisco"', self.page)
        self.assertIn('/assets/brand/logo-splunk-by-cisco-rgb-w.png', self.page)
        self.assertIn('name=theme-color content="#07090c"', self.page)

    def test_agent_activity_work_time_header_aligns_with_right_aligned_values(self):
        self.assertIn(
            ".agentTreeHeader .fieldtip{justify-content:flex-end}",
            self.page,
        )

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_browser_agent_attention_adds_cap_and_live_growth_only_with_cost_evidence(self):
        script = f"""
const fs=require('fs');
const page=fs.readFileSync({json.dumps(str(Path(DASHBOARD_PAGE)))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
eval(page.match(/^const money=.*$/m)[0].replace('const money','globalThis.money'));
eval(extract('deriveAgentAttention'));
const group={{root_session_id:'root',totals:{{cost:2,cost_available:true}},agents:[
 {{id:'root',kind:'root',cost:0.5,cost_available:true,activity_state:'recent'}},
 {{id:'child',kind:'spawned',cost:1.5,cost_available:true,activity_state:'working'}}
],attention:[{{agent_id:'child',level:'needs_attention',reasons:[{{code:'retry_pressure',explanation:'3 retries'}}]}}]}};
const covered=deriveAgentAttention(group,1,new Map([['root:child',1.0]]));
const partial=deriveAgentAttention({{...group,totals:{{cost:null,cost_available:false}},agents:[group.agents[0],{{...group.agents[1],cost:null,cost_available:false}}]}},1,new Map([['root:child',1.0]]));
process.stdout.write(JSON.stringify({{covered,partial}}));
"""

        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        payload = json.loads(result.stdout)
        covered = {
            row["agent_id"]: [reason["code"] for reason in row["reasons"]]
            for row in payload["covered"]
        }
        partial = {
            row["agent_id"]: [reason["code"] for reason in row["reasons"]]
            for row in payload["partial"]
        }
        self.assertEqual(covered["root"], ["cap_exceeded"])
        self.assertEqual(covered["child"], [
            "retry_pressure", "cap_exceeded", "observed_live_growth",
        ])
        self.assertEqual(partial["child"], ["retry_pressure"])
        self.assertNotIn("root", partial)

    def test_current_header_keeps_one_line_session_start_message_visible(self):
        self.assertIn('class="card previewStartStrip"', self.page)
        self.assertIn("id=preview-start", self.page)
        self.assertIn("function sessionStartMessage(s)", self.page)
        self.assertIn("$('preview-start').textContent=startMessage", self.page)
        current = self.page.split('<div class="view on" id=view-session>', 1)[1].split(
            "<div class=view id=view-models>", 1
        )[0]
        self.assertLess(current.index("id=preview-start"), current.index("id=preview-run-chart-slot"))
        self.assertIn("text-overflow:ellipsis;white-space:nowrap", self.page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_project_filter_groups_non_folder_labels(self):
        match = re.search(r"function projectFilterValue\(.*?\n\}", self.page, re.DOTALL)
        self.assertIsNotNone(match, "Project filters need folder-only values")
        script = "const OTHER_LOCAL_SESSIONS_PROJECT='__other_local_sessions__';\n" + match.group(0) + r"""
console.log(JSON.stringify([
  projectFilterValue('~/Documents/github/token-meter'),
  projectFilterValue('/repo/a'), projectFilterValue('CLI'),
  projectFilterValue('No project'), projectFilterValue('/private/tmp/run'),
  projectFilterValue('~/Documents/Codex/2026-08-31/task'),
]));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), [
            "~/Documents/github/token-meter", "/repo/a",
            "__other_local_sessions__", "__other_local_sessions__",
            "__other_local_sessions__",
            "__other_local_sessions__",
        ])

    def test_fieldtips_are_portaled_and_viewport_bound(self):
        for marker in (
            "#fieldtip-popup{position:fixed",
            "function initFieldTipPopup()",
            "popup.setAttribute('role','tooltip')",
            "const maxLeft=Math.max(margin,window.innerWidth-margin-width)",
            "below+height<=window.innerHeight-margin||above<margin?below:above",
            "document.addEventListener('pointerover'",
            "window.addEventListener('scroll',scheduleFieldTipPosition,true)",
        ):
            self.assertIn(marker, self.page)
        self.assertNotIn("content:attr(data-tip)", self.page)

    def test_primary_navigation_and_command_palette_share_the_same_workflow_order(self):
        tab_ids = [
            "tab-session", "tab-daily", "tab-models", "tab-subagents",
            "tab-efficiency", "tab-work", "tab-git", "tab-performance", "tab-learn",
            "tab-capabilities", "tab-settings",
        ]
        positions = [self.page.index(f"id={tab_id}") for tab_id in tab_ids]
        self.assertEqual(positions, sorted(positions))
        for marker in (
            "id=command-palette", "id=command-search",
            "const NAV_COMMANDS=[",
            "key==='k'", "event.key==='Escape'", "event.key==='ArrowDown'",
            "event.key==='Enter'",
            "class=tabs aria-label=\"Primary navigation\"",
            "button.setAttribute('aria-current','page')",
        ):
            self.assertIn(marker, self.page)
        self.assertIn("if(command.latest)goToLatestSession()", self.page)
        self.assertIn("action:'selected-session'", self.page)
        self.assertIn("function openSelectedSessionRoute(route='summary')", self.page)
        self.assertIn("history.pushState({sessionScope:sessionReturnScope},'',sessionRoute(pinned))", self.page)
        self.assertIn("if(command.action==='selected-session'){openSelectedSessionRoute(command.route);return;}", self.page)
        self.assertIn("else openTopLevelRoute(command.route)", self.page)
        self.assertNotIn("shortcut:'⌥", self.page)
        self.assertNotIn("id=command-trigger", self.page)
        self.assertNotIn("Local evidence", self.page)
        self.assertNotIn("class=pulse", self.page)
        self.assertNotIn("@keyframes p{", self.page)
        self.assertNotIn("id=live", self.page)
        self.assertNotIn("id=livetxt", self.page)
        self.assertNotIn("$('live')", self.page)
        self.assertNotIn("$('livetxt')", self.page)
        self.assertNotIn("id=command-alt-key", self.page)
        self.assertNotIn("class=commandShortcut", self.page)

    def test_top_level_pages_have_no_numbered_shortcuts(self):
        for tab_id in (
            "session", "daily", "models", "subagents", "efficiency",
            "work", "git", "learn", "capabilities", "settings",
        ):
            match = re.search(rf'<button[^>]+id=tab-{tab_id}[^>]*>.*?</button>', self.page)
            self.assertIsNotNone(match, tab_id)
            button = match.group(0)
            self.assertNotIn("aria-keyshortcuts", button)
            self.assertNotIn("Shortcut", button)
            self.assertNotIn("tabShortcut", button)
        for marker in ("directKey", "tabShortcut", "Shortcut: Option+", "event.code"):
            self.assertNotIn(marker, self.page)
        perf = Path(__file__).resolve().parents[1].joinpath("performance.html").read_text(encoding="utf-8")
        for marker in ("aria-keyshortcuts", "data-shortcut-digit", "railShortcut", "Shortcut: Option+", "event.code"):
            self.assertNotIn(marker, perf)

    def test_all_sessions_live_updates_preserve_mounted_rows(self):
        for marker in (
            "const logHtmlCache=new WeakMap()",
            "function setLogText(element,value)",
            "function setLogAttribute(element,name,value)",
            "function setLogHtml(element,html)",
            "function logRowRenderKey(s,active)",
            "function reconcileLogRows(sessions,maxCost,liveSessionIds=new Set())",
            "const interactingLogRow=forceAllSessionRowRefresh?null:root.querySelector('.srow:hover,.srow:focus-within');",
            "if(interactingLogRow)return;",
            "function mergeAllSessionInventory(inventory,liveSessions)",
            "const key=row=>row?.key?compareKeyFor(row):sessionRowKey(row);",
            "(liveSessions||[]).forEach(row=>rows.set(key(row),row))",
            "const liveSessionIds=new Set((xs.current_sessions||[]).map(sessionRowKey));",
            "const live=liveSessionIds.has(key)",
            "const hasChildren=childAgentsFor(s).length>0;",
            "const compareIndex=compareIds.indexOf(rowKey);",
            "className=`srow${active?' active':''}${live?' live':''}${hasChildren?' hasChildren':''}${compareIndex>=0?' compareSelected':''}`",
            ".srow.active,.srow.live{border-color:rgb(var(--signal-rgb)/.62)",
            "const existing=new Map([...root.children]",
            "if(row.className!==className)row.className=className",
            "if(row.getAttribute('aria-label')!==ariaLabel)",
            "if(row._logRenderKey!==renderKey)",
            "else root.insertBefore(row,cursor)",
            "reconcileLogRows(sessions,maxCost,liveSessionIds)",
            "$('slist').onclick=event=>",
            "$('slist').onkeydown=event=>",
            "const inventoryStale=!allSessionInventory",
            "if(inventoryStale)loadAllSessions()",
            "else renderAllSessions(LATEST.xsession)",
        ):
            self.assertIn(marker, self.page)
        self.assertIn(
            "body.spectrumApp.sessionRoute #view-session .srow:hover{transform:none}",
            self.page,
        )
        self.assertNotIn("$('slist').innerHTML=sessions.length?sessions.map", self.page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_session_header_names_the_opened_trace_not_a_sibling(self):
        script = "let allSessionInventory=null;\n" + "\n".join(
            self.page_function(self.page, name)
            for name in ("stateSessionId", "stateSessionKey", "sessionStartMessage", "sessionDisplayName")
        ) + """
const state={session:'rollout-child.jsonl',source:{id:'task-1'},xsession:{current_sessions:[],sessions:[
 {id:'task-1',session:'rollout-root.jsonl',title:'Root title'},
 {id:'task-1',session:'rollout-child.jsonl',title:'Child title'}]}};
const sibling={...state,session:'rollout-other.jsonl'};
// Older traces fall outside the recent rows but are in the All sessions inventory the user opened them from.
allSessionInventory=[{id:'task-1',session:'rollout-old.jsonl',title:'Old title'}];
const older={...state,session:'rollout-old.jsonl'};
console.log(JSON.stringify([sessionDisplayName(state),sessionDisplayName(sibling),sessionDisplayName(older)]));
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout), ["Child title", "Session", "Old title"])

    def test_all_sessions_inventory_is_declared_before_the_first_header_render(self):
        declaration = self.page.index("let allSessionInventory=null")
        self.assertLess(declaration, self.page.index("function showCurrentPanel("))
        self.assertLess(declaration, self.page.index("function applyHashRoute(){"))

    def test_custom_fieldtips_remove_delayed_native_tooltips_globally(self):
        self.assertIn("root.querySelectorAll('.fieldtip[title]')", self.page)
        self.assertIn("stripNativeFieldTipTitles();", self.page)
        self.assertIn('return `class=fieldtip tabindex=0 aria-description="${safe}" data-tip="${safe}"`', self.page)
        self.assertNotIn('return `class=fieldtip tabindex=0 title="${safe}"', self.page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_page_signal_presentations_share_one_animation_timeline(self):
        functions = []
        for name in ("pageSignalTiming", "sessionSignalPresentation", "pageSignalPresentation"):
            match = re.search(
                rf"^function {name}\(.*?^\}}\n",
                self.page,
                re.MULTILINE | re.DOTALL,
            )
            self.assertIsNotNone(match, f"dashboard needs {name}")
            functions.append(match.group(0))
        script = """
let pageSignalClockStartedAt=1000;
let clockNow=11000;
const performance={now:()=>clockNow};
""" + "\n".join(functions) + """
let latestSessionSignalSnapshot={evidence:'available',mode:'working'};
const pageSignalReducedMotionQuery={matches:false};
const signals=['sessions','spend','models','capabilities','efficiency','learn','settings'];
const routes=Object.fromEntries(signals.map(signal=>[
 signal,pageSignalPresentation({dataset:{pageSignal:signal}}),
]));
const states={};
for(const mode of ['working','waiting','recent','idle']){
 states[mode]=sessionSignalPresentation({evidence:'available',mode},false);
}
states.unavailable=sessionSignalPresentation({evidence:'unavailable',mode:'idle'},false);
latestSessionSignalSnapshot={evidence:'unavailable',mode:'idle'};
const unavailable=pageSignalPresentation({dataset:{pageSignal:'sessions'}});
clockNow=17000;
latestSessionSignalSnapshot={evidence:'available',mode:'working'};
const continued=Object.fromEntries(signals.map(signal=>[
 signal,pageSignalPresentation({dataset:{pageSignal:signal}}),
]));
pageSignalReducedMotionQuery.matches=true;
const reduced=Object.fromEntries(signals.map(signal=>[
 signal,pageSignalPresentation({dataset:{pageSignal:signal}}),
]));
console.log(JSON.stringify({routes,states,unavailable,continued,reduced}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        rendered = json.loads(result.stdout)
        timing = {
            "heroSpeed": 0.018,
            "heroFrame": 180,
            "borderSpeed": 0.016,
            "borderFrame": 9160,
        }
        for presentation in rendered["routes"].values():
            self.assertEqual(
                {key: presentation[key] for key in timing},
                timing,
            )
        states = rendered["states"]
        for mode in ("working", "waiting", "recent", "idle"):
            self.assertEqual({key: states[mode][key] for key in timing}, timing)
        self.assertGreater(states["working"]["heroStrength"], states["idle"]["heroStrength"])
        self.assertEqual(rendered["unavailable"]["heroSpeed"], 0)
        self.assertEqual(rendered["unavailable"]["borderSpeed"], 0)
        self.assertEqual(rendered["unavailable"]["heroFrame"], timing["heroFrame"])
        self.assertEqual(rendered["unavailable"]["borderFrame"], timing["borderFrame"])
        continued_timing = {
            "heroSpeed": 0.018,
            "heroFrame": 288,
            "borderSpeed": 0.016,
            "borderFrame": 9256,
        }
        for presentation in rendered["continued"].values():
            self.assertEqual(
                {key: presentation[key] for key in continued_timing},
                continued_timing,
            )
        for presentation in rendered["reduced"].values():
            self.assertEqual(presentation["heroSpeed"], 0)
            self.assertEqual(presentation["borderSpeed"], 0)
            self.assertEqual(presentation["heroFrame"], continued_timing["heroFrame"])
            self.assertEqual(presentation["borderFrame"], continued_timing["borderFrame"])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_page_signal_route_change_disposes_a_partially_mounted_effect(self):
        page_path = Path(DASHBOARD_PAGE)
        script = f"""
const fs=require('fs');
const page=fs.readFileSync({json.dumps(str(page_path))},'utf8');
function extract(name){{
 let start=page.indexOf(`function ${{name}}(`);
 if(start<0)throw new Error(name);
 if(page.slice(start-6,start)==='async ')start-=6;
 let i=page.indexOf('{{',start),depth=0;
 for(;i<page.length;i++){{
  if(page[i]==='{{')depth++;
  else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);
 }}
}}
class El{{
 constructor(name){{this.name=name;this.dataset={{}};this.children=[];this.isConnected=true;this.className='';}}
 querySelector(selector){{return this.children.find(child=>selector.includes(child.className))||null;}}
 appendChild(child){{this.children.push(child);return child;}}
 append(...children){{this.children.push(...children);}}
 setAttribute(){{}}
 replaceChildren(){{this.children=[];}}
 closest(selector){{return selector.includes('sessionDetail')?null:this;}}
}}
let currentHead=null;
const document={{querySelector(){{return currentHead;}},createElement(){{return new El('created');}}}};
let activePageSignalHead=null,activePageHeroHandle=null,activePageBorderHandle=null;
let activePageSignalMounting=false,activePageSignalGeneration=0,pageEffectsDisabled=false;
let live=[];
const borderResolvers=[];
function handle(label){{
 live.push(label);
 return {{update(){{}},dispose(){{live=live.filter(value=>value!==label);}}}};
}}
let currentMount='A';
function loadPageEffectsModule(){{
 return Promise.resolve({{
  mountDitherField(){{return Promise.resolve(handle(`hero:${{currentMount}}`));}},
  mountPulsingBorder(){{
   const label=`border:${{currentMount}}`;
   return new Promise(resolve=>borderResolvers.push(()=>resolve(handle(label))));
  }},
 }});
}}
function pageSignalPresentation(){{return {{}};}}
eval(extract('ensurePageSignalHosts'));
eval(extract('disposeActivePageSignalEffects'));
if(page.includes('function pageSignalHandle('))eval(extract('pageSignalHandle'));
eval(extract('syncActivePageSignalEffects'));
(async()=>{{
 const a=new El('A'),b=new El('B');
 a.dataset.pageSignal='models';b.dataset.pageSignal='spend';
 currentHead=a;currentMount='A';
 const mountA=syncActivePageSignalEffects();
 await Promise.resolve();await Promise.resolve();await Promise.resolve();
 const afterAHero=[...live];
 currentHead=b;currentMount='B';
 const mountB=syncActivePageSignalEffects();
 await Promise.resolve();await Promise.resolve();await Promise.resolve();
 const afterRouteChange=[...live];
 borderResolvers.shift()();
 await Promise.resolve();await Promise.resolve();
 const afterStaleBorder=[...live];
 borderResolvers.shift()();
 await Promise.all([mountA,mountB]);
 console.log(JSON.stringify({{afterAHero,afterRouteChange,afterStaleBorder,complete:live}}));
}})().catch(error=>{{console.error(error);process.exitCode=1;}});
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        rendered = json.loads(result.stdout)
        self.assertEqual(rendered["afterAHero"], ["hero:A"])
        self.assertEqual(rendered["afterRouteChange"], ["hero:B"])
        self.assertEqual(rendered["afterStaleBorder"], ["hero:B"])
        self.assertEqual(rendered["complete"], ["hero:B", "border:B"])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_stale_same_header_mount_cannot_clear_the_new_ready_marker(self):
        page_path = Path(DASHBOARD_PAGE)
        script = f"""
const fs=require('fs');
const page=fs.readFileSync({json.dumps(str(page_path))},'utf8');
function extract(name){{
 let start=page.indexOf(`function ${{name}}(`);
 if(start<0)throw new Error(name);
 if(page.slice(start-6,start)==='async ')start-=6;
 let i=page.indexOf('{{',start),depth=0;
 for(;i<page.length;i++){{
  if(page[i]==='{{')depth++;
  else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);
 }}
}}
class El{{
 constructor(name){{this.name=name;this.dataset={{}};this.children=[];this.isConnected=true;this.className='';}}
 querySelector(selector){{
  const wanted=selector.slice(1);
  for(const child of this.children){{
   if(child.className===wanted)return child;
   const nested=child.querySelector(selector);
   if(nested)return nested;
  }}
  return null;
 }}
 appendChild(child){{this.children.push(child);return child;}}
 append(...children){{this.children.push(...children);}}
 setAttribute(){{}}
 replaceChildren(){{this.children=[];}}
 closest(selector){{return selector.includes('sessionDetail')?null:this;}}
}}
let currentHead=null,currentMount='',live=[],borderQueue=[];
const document={{querySelector(){{return currentHead;}},createElement(){{return new El('created');}}}};
let activePageSignalHead=null,activePageHeroHandle=null,activePageBorderHandle=null;
let activePageSignalMounting=false,activePageSignalGeneration=0,pageEffectsDisabled=false;
function handle(label,host){{
 live.push(label);host.dataset.pageEffect='ready';let disposed=false;
 return {{
  update(){{}},
  dispose(){{
   if(!disposed){{disposed=true;live=live.filter(value=>value!==label);}}
   delete host.dataset.pageEffect;
  }},
 }};
}}
function loadPageEffectsModule(){{
 return Promise.resolve({{
  mountDitherField(host){{return Promise.resolve(handle(`hero:${{currentMount}}`,host));}},
  mountPulsingBorder(host){{
   const label=`border:${{currentMount}}`;
   return new Promise(resolve=>borderQueue.push({{label,resolve:()=>resolve(handle(label,host))}}));
  }},
 }});
}}
function pageSignalPresentation(){{return {{}};}}
eval(extract('ensurePageSignalHosts'));
eval(extract('disposeActivePageSignalEffects'));
if(page.includes('function pageSignalHandle('))eval(extract('pageSignalHandle'));
eval(extract('syncActivePageSignalEffects'));
const ticks=async(count=5)=>{{while(count--)await Promise.resolve();}};
(async()=>{{
 const a=new El('A'),b=new El('B');
 a.dataset.pageSignal='models';b.dataset.pageSignal='spend';
 currentHead=a;currentMount='A1';const mountA1=syncActivePageSignalEffects();await ticks();
 currentHead=b;currentMount='B';const mountB=syncActivePageSignalEffects();await ticks();
 currentHead=a;currentMount='A2';const mountA2=syncActivePageSignalEffects();await ticks();
 const aHero=a.querySelector('.pageDither');
 const before={{live:[...live],heroState:aHero.dataset.pageEffect||null}};
 borderQueue.find(row=>row.label==='border:A1').resolve();await mountA1;await ticks();
 const afterStale={{live:[...live],heroState:aHero.dataset.pageEffect||null}};
 borderQueue.find(row=>row.label==='border:B').resolve();await mountB;await ticks();
 borderQueue.find(row=>row.label==='border:A2').resolve();await mountA2;
 console.log(JSON.stringify({{before,afterStale,complete:{{live,heroState:aHero.dataset.pageEffect||null}}}}));
}})().catch(error=>{{console.error(error);process.exitCode=1;}});
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        rendered = json.loads(result.stdout)
        self.assertEqual(rendered["before"], {
            "live": ["hero:A2"], "heroState": "ready",
        })
        self.assertEqual(rendered["afterStale"], {
            "live": ["hero:A2"], "heroState": "ready",
        })
        self.assertEqual(rendered["complete"], {
            "live": ["hero:A2", "border:A2"], "heroState": "ready",
        })

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_shared_border_readiness_cancels_stale_mounts_before_construction(self):
        page_path = Path(DASHBOARD_PAGE)
        script = f"""
const fs=require('fs');
const page=fs.readFileSync({json.dumps(str(page_path))},'utf8');
function extract(name){{
 let start=page.indexOf(`function ${{name}}(`);
 if(start<0)throw new Error(name);
 if(page.slice(start-6,start)==='async ')start-=6;
 let i=page.indexOf('{{',start),depth=0;
 for(;i<page.length;i++){{
  if(page[i]==='{{')depth++;
  else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);
 }}
}}
class El{{
 constructor(name){{this.name=name;this.dataset={{}};this.children=[];this.isConnected=true;this.className='';}}
 querySelector(selector){{
  const wanted=selector.slice(1);
  for(const child of this.children){{
   if(child.className===wanted)return child;
   const nested=child.querySelector(selector);
   if(nested)return nested;
  }}
  return null;
 }}
 appendChild(child){{this.children.push(child);return child;}}
 append(...children){{this.children.push(...children);}}
 setAttribute(){{}}
 replaceChildren(){{this.children=[];}}
 closest(selector){{return selector.includes('sessionDetail')?null:this;}}
}}
let currentHead=null,currentMount='',live=[];
let releaseSharedBorder;
const sharedBorderReady=new Promise(resolve=>{{releaseSharedBorder=resolve;}});
const document={{querySelector(){{return currentHead;}},createElement(){{return new El('created');}}}};
let activePageSignalHead=null,activePageHeroHandle=null,activePageBorderHandle=null;
let activePageSignalMounting=false,activePageSignalGeneration=0,pageEffectsDisabled=false;
function handle(label,host){{
 live.push(label);host.dataset.pageEffect='ready';host.paperShaderMount=label;let disposed=false;
 return {{
  update(){{}},
  dispose(){{
   if(disposed)throw new Error(`double dispose ${{label}}`);
   disposed=true;live=live.filter(value=>value!==label);
   delete host.dataset.pageEffect;delete host.paperShaderMount;
  }},
 }};
}}
function loadPageEffectsModule(){{
 return Promise.resolve({{
  mountDitherField(host){{return Promise.resolve(handle(`hero:${{currentMount}}`,host));}},
  async mountPulsingBorder(host,presentation,isCurrent){{
   const label=`border:${{currentMount}}`;
   await sharedBorderReady;
   if(isCurrent&&!isCurrent())return null;
   return handle(label,host);
  }},
 }});
}}
function pageSignalPresentation(){{return {{}};}}
eval(extract('ensurePageSignalHosts'));
eval(extract('disposeActivePageSignalEffects'));
if(page.includes('function pageSignalHandle('))eval(extract('pageSignalHandle'));
eval(extract('syncActivePageSignalEffects'));
const ticks=async(count=8)=>{{while(count--)await Promise.resolve();}};
(async()=>{{
 const a=new El('A'),b=new El('B');
 a.dataset.pageSignal='models';b.dataset.pageSignal='spend';
 currentHead=a;currentMount='A1';const mountA1=syncActivePageSignalEffects();await ticks();
 currentHead=b;currentMount='B';const mountB=syncActivePageSignalEffects();await ticks();
 currentHead=a;currentMount='A2';const mountA2=syncActivePageSignalEffects();await ticks();
 const aHero=a.querySelector('.pageDither'),aBorder=a.querySelector('.pageHeroBorder');
 releaseSharedBorder();
 await Promise.all([mountA1,mountB,mountA2]);await ticks();
 console.log(JSON.stringify({{
  live,
  heroState:aHero.dataset.pageEffect||null,
  borderState:aBorder.dataset.pageEffect||null,
  borderOwner:aBorder.paperShaderMount||null,
  disabled:pageEffectsDisabled,
 }}));
}})().catch(error=>{{console.error(error);process.exitCode=1;}});
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        rendered = json.loads(result.stdout)
        self.assertEqual(rendered, {
            "live": ["hero:A2", "border:A2"],
            "heroState": "ready",
            "borderState": "ready",
            "borderOwner": "border:A2",
            "disabled": False,
        })

    def test_top_level_pages_share_one_dither_header_system(self):
        for marker in (
            "data-page-signal=sessions",
            "data-page-signal=spend",
            "data-page-signal=models",
            "data-page-signal=subagents",
            "data-page-signal=capabilities",
            "data-page-signal=learn",
            "data-page-signal=settings",
            "class=spectrumPageHeadFrame",
            "class=\"spectrumPageHeadFrame hasPageActions\"",
            "class=spectrumPageSubtitle",
            "class=spectrumPageActions",
            "function ensurePageSignalHosts(head)",
            "function syncActivePageSignalEffects()",
            "document.querySelector('.view.on .spectrumPageHead[data-page-signal]')",
            "activePageHeroHandle?.dispose()",
            "activePageBorderHandle?.dispose()",
            "requestAnimationFrame(syncActivePageSignalEffects)",
            "function syncSessionFlow(snapshot)",
            "import('/assets/session-effects.js')",
            "if(label.textContent!==nextLabel)",
            "data-session-flow",
            ".currentSessionCard.is-arriving",
            "@keyframes currentSessionArrive",
            "@media(prefers-reduced-motion:reduce)",
        ):
            self.assertIn(marker, self.page)
        for rejected_marker in (
            "sessionFlowField",
            "sessionFlowLine",
            "sessionFlowPacket",
            "currentSessionFlow",
            "sessionAmbient",
            "sessionGodRays",
            "session-god-rays",
            "sessionPrimaryBorder",
            "session-primary-border",
            "syncPrimarySessionBorder",
            "primaryWorkingSessionId",
            "neuro-noise",
            "neuroNoise",
            "currentSessionStateShift",
            "<animateMotion",
            "offset-path:path",
        ):
            self.assertNotIn(rejected_marker, self.page)

    def test_top_level_headers_use_fixed_shared_heights_and_concise_subtitles(self):
        for marker in (
            ".spectrumPageHead{position:relative;isolation:isolate;display:flex;width:100%;max-width:none;height:138px",
            "@media(max-width:900px){.spectrumPageHead{height:126px",
            "@media(max-width:520px){.spectrumPageHead{height:116px",
            ".spectrumPageSubtitle{max-width:52ch;margin:6px 0 0;color:var(--dim);font-size:var(--fs-12);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
            ".spectrumPageActions{position:absolute;z-index:5;right:30px;bottom:24px",
            ".spectrumPageActions{position:static;display:flex;width:100%;max-width:none",
            "Last 30 minutes",
            "Track the cost of your custom agents.",
            "Tools, MCP servers, and skills.",
            "What the spend went into.",
            "Pushed code &times; covered spend &middot; <span class=fieldtip id=d-caveat",
            "Spend tokens like money. Measure what you get back.",
            "Connections, pricing, and updates.",
        ):
            self.assertIn(marker, self.page)
        self.assertEqual(self.page.count("data-page-signal="), 10)
        self.assertEqual(self.page.count("class=spectrumPageSubtitle"), 7)
        for removed in ("Live local traces · last 30 minutes.", "Estimated agent spend over time.", "Cost, speed, and context.", "Local token efficiency."):
            self.assertNotIn(removed, self.page)
        self.assertNotIn(".spectrumPageHead{position:relative;isolation:isolate;display:flex;width:100%;max-width:none;min-height:138px", self.page)

    def test_shared_header_effect_adapter_exposes_generic_mounts(self):
        for marker in (
            "orderedDitherRainFragmentShader",
            "export async function mountDitherField",
            "export async function mountPulsingBorder(element, presentation, isCurrent = () => true)",
            "if (!isCurrent()) return null;",
        ):
            self.assertIn(marker, self.session_effects)
        self.assertNotIn("export async function mountSessionHero", self.session_effects)
        self.assertNotIn("export async function mountSessionBorder", self.session_effects)

    def test_desktop_action_headers_keep_titles_inside_their_text_slot(self):
        titles = re.findall(
            r'class="spectrumPageHeadFrame hasPageActions".*?<h1(?:\s[^>]*)?>([^<]+)</h1>',
            self.page,
            re.DOTALL,
        )
        self.assertEqual(
            titles, ["Sessions", "Subagents", "Models", "Spend", "Efficiency", "Work", "Git", "Learn"],
        )
        self.assertTrue(all(len(title) <= 12 for title in titles))

    def test_mobile_header_action_rows_scroll_instead_of_stacking(self):
        for marker in (
            ".spectrumPageActions .modelControls{display:grid;width:100%;grid-template-columns:repeat(3,minmax(0,1fr))",
        ):
            self.assertIn(marker, self.page)
        self.assertNotIn("data-spend-range", self.page)
        self.assertNotIn(".spendRangeControls .seg", self.page)

    def test_session_shader_border_tracks_the_hero_edges(self):
        for marker in (
            "u_marginLeft: 0.002",
            "u_marginRight: 0.002",
            "u_marginTop: 0.002",
            "u_marginBottom: 0.002",
        ):
            self.assertIn(marker, self.session_effects)
        self.assertNotIn("u_marginLeft: 0.014", self.session_effects)
        self.assertNotIn("u_marginTop: 0.024", self.session_effects)

    def test_session_dither_uses_downward_ordered_pixel_streams(self):
        for marker in (
            "orderedDitherRainFragmentShader",
            "const int bayer4x4[16]",
            "float fallingPhase",
            "cell.y + u_time * speed",
            "float streamHead",
        ):
            self.assertIn(marker, self.session_effects)
        self.assertNotIn("DitheringShapes.", self.session_effects)

    def test_collapsed_navigation_keeps_session_scope_labels_visible(self):
        self.assertIn(
            "body.spectrumApp .top .brandCopy,body.spectrumApp .top .tabLabel{display:none}",
            self.page,
        )
        self.assertIn("body.spectrumApp .top .brandCopy{display:grid}", self.page)
        self.assertIn("body.spectrumApp .top .tabLabel{display:inline}", self.page)
        self.assertNotIn(".brandCopy,.tabLabel,.tabShortcut{display:none}", self.page)

    def test_redundant_hover_tips_are_removed_from_dashboard_labels(self):
        for marker in (
            '<h1 class=fieldtip id=session-page-title',
            '<h1 class=fieldtip tabindex=0 aria-description="Local AI app logs',
            '<h1 class=fieldtip tabindex=0 aria-description="Compare model runtimes',
            '<h1 class=fieldtip tabindex=0 aria-description="What changed',
            '<h1 class=fieldtip tabindex=0 aria-description="A practical review loop',
            '<h1 class=fieldtip tabindex=0 aria-description="Review local tool',
            '<h1 class=fieldtip tabindex=0 aria-description="Machine-wide controls',
            'class="currentSessionsCount fieldtip"',
            'class="previewStartText fieldtip"',
            'class="card previewKpi fieldtip',
            'class="learnShortcut fieldtip"',
            '<h2 class=fieldtip tabindex=0 aria-description="Where the day’s covered spend',
        ):
            self.assertNotIn(marker, self.page)
        for marker in (
            '<h1 id=session-page-title>Sessions</h1>',
            '<h2 class=workSrOnly>All sessions</h2>',
            '<h1>Models</h1>',
            '<h1>Spend</h1>',
            '<h1>Learn</h1>',
            '<h1>Tools</h1>',
            '<h1>Settings</h1>',
            '<div class="card previewKpi metric">',
            'data-tip="Observed model output divided by attributable timing.',
            'data-tip="Budget minus observed spend.',
        ):
            self.assertIn(marker, self.page)

    def test_sessions_use_the_distilled_token_meter_spectrum_system(self):
        for marker in (
            "shared-spectrum-system-v1",
            "session-spectrum-field-v2",
            "THESIS: Sessions are live instruments",
            "<body class=spectrumApp>",
            ":root,[data-theme=classic]{",
            "--signal:#00bceb",
            "--signal-mid:#1ba0e1",
            "--signal-hi:#7fdbf2",
            "--aura:#c7a7ff",
            "--orange:#ffb457",
            "body.sessionRoute{",
            "--session-signal:var(--signal)",
            'class="previewHead spectrumPageHead sessionFlowHead"',
            'class="previewHeadCopy spectrumPageHeadCopy"',
            ".spectrumPageHead:before",
            ".spectrumPageHead{position:relative;isolation:isolate;display:flex;width:100%;max-width:none;height:138px;align-items:flex-start",
            ".currentSessionCard:before{content:none}",
            ".currentSessionCard.activity-working",
            ".currentSessionGrip",
            "@media(prefers-reduced-motion:reduce)",
            "document.body.classList.toggle('sessionRoute',t==='session');",
            "$('view-session').classList.toggle('sessionOverview',overview);",
            "$('view-session').classList.toggle('sessionDetail',!overview);",
            "$('session-page-title').textContent=overview?'Sessions':sessionDisplayName(CURRENT);",
            "function sessionDisplayName(s)",
            "activity-${activity}",
            "<svg class=currentSessionGrip",
            'font-family:"Tektur Local"',
            'src:url("/assets/fonts/Tektur-Variable.ttf")',
            "filterView.countLabel",
            "concept-roll seed a5c0fdde",
        ):
            self.assertIn(marker, self.page)
        self.assertNotIn("id=current-eyebrow", self.page)
        self.assertNotIn("miraiVerticalNote", self.page)
        self.assertNotIn("miraiGaugeScale", self.page)
        self.assertNotIn("直近30分", self.page)
        self.assertNotIn("稼働状況", self.page)
        self.assertNotIn("session-solar-field-v1", self.page)
        self.assertNotIn("body.sessionRoute #view-session.sessionDetail .previewHead{min-height:152px}", self.page)
        self.assertNotIn("body.sessionRoute #view-session.sessionDetail .previewStatus:before", self.page)
        self.assertNotIn("body.sessionRoute #view-session.sessionDetail .previewStatus:after", self.page)

    def test_top_level_views_share_the_spectrum_design_primitives(self):
        for marker in (
            "shared-spectrum-system-v1",
            "spectrum-professional-finish-v2",
            "--spectrum-card:",
            "--spectrum-edge-light:",
            "--spectrum-edge-hover:",
            "--spectrum-depth-focus:",
            "--spectrum-active-hover:",
            "body.spectrumApp .card{",
            "body.spectrumApp .tbtn{",
            "body.spectrumApp .seg{",
            "body.spectrumApp .tab.on,body.spectrumApp .seg button.on",
            "body.spectrumApp :is(input:not([type=checkbox]):not([type=range]),select,textarea,.modelPicker>summary)",
            "body.spectrumApp .spectrumPageHead:after",
            "--spectrum-page:radial-gradient",
            "--spectrum-card:radial-gradient",
            "--spectrum-control:radial-gradient",
            "--spectrum-active:linear-gradient",
            '<div class="previewHead spectrumPageHead sessionFlowHead"',
            '<header class="capPageHead spectrumPageHead"',
            '<div class="modelHead spectrumPageHead"',
            '<div class="dailyHead spectrumPageHead"',
            '<div class="learnHead spectrumPageHead"',
            '<div class="learnHead settingsPageHead spectrumPageHead"',
            "<h1>Tools</h1>",
            "<span class=allSessionsHint id=g-hint>Recent activity</span>",
            "vertical-navigation-rail-v1",
            "body.spectrumApp .wrap{--navigation-rail-width:184px",
            "grid-template-columns:var(--navigation-rail-width) minmax(0,1320px)",
            "body.spectrumApp .top{grid-column:1;grid-row:1;position:sticky",
            "body.spectrumApp .top .tabs{display:flex;min-width:0;max-width:100%;overflow:visible;flex:1;flex-direction:column",
            "body.spectrumApp .top .tab{display:grid;width:100%;min-height:42px",
            "class=tabIcon",
            "class=navPrimary",
            "class=navSecondary",
            ".navPrimary,.navSecondary{display:flex;flex-direction:column;gap:4px}",
            ".navSecondary{margin-top:auto",
            "@media(max-width:1180px){body.spectrumApp .wrap{--navigation-rail-width:68px",
            "@media(max-width:760px){body.spectrumApp .wrap{display:block",
            "body.spectrumApp .top .tabs{grid-column:1/-1;grid-row:2;min-width:0;width:100%;overflow-x:auto;flex:none;flex-direction:row",
            ".navPrimary,.navSecondary{display:contents}",
            "@media(max-width:760px){#view-models{overflow-x:clip}}",
            "body.spectrumApp .settingsMap{grid-template-columns:repeat(6,minmax(0,1fr))",
        ):
            self.assertIn(marker, self.page)
        for marker in (
            "data-settings-target=budget-settings><span>01",
            "data-settings-target=agent-access><span>02",
            "data-settings-target=model-pricing-settings><span>03",
            "data-settings-target=frustration-settings><span>04",
            "data-settings-target=update-settings><span>05",
            "&#8249;",
            "&#8250;",
        ):
            self.assertNotIn(marker, self.page)

    def test_handler_swallows_browser_disconnects(self):
        source = Path(meter.IMPLEMENTATION_FILE).read_text()
        self.assertIn("except (BrokenPipeError, ConnectionResetError):", source)

    def test_dashboard_live_updates_do_not_hold_event_stream_connections(self):
        self.assertNotIn("new EventSource('/events')", self.page)
        self.assertIn("const LIVE_STATE_POLL_MS=1000", self.page)
        self.assertIn("setInterval(refreshLiveState,LIVE_STATE_POLL_MS)", self.page)
        self.assertIn("if(statePollBusy)return", self.page)
        poll = self.page.split("async function refreshLiveState(){", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("applyLiveState(await fetchLiveJson('/state',LIVE_XSESSION.state,'State'))", poll)
        source = Path(meter.IMPLEMENTATION_FILE).read_text()
        state_route = source.index('elif req_path == "/state":')
        self.assertIn("dashboard_state_body(", source[state_route:state_route + 400])
        session_route = source.index('elif req_path == "/session":')
        self.assertIn(
            "dashboard_state_body(st or {}, known)",
            source[session_route:state_route],
        )
        events = source.index('elif req_path == "/events":')
        self.assertIn("self.send_response(204)", source[events:events + 700])

    def test_http_server_tolerates_browser_connection_bursts(self):
        self.assertTrue(meter.TokenMeterHTTPServer.daemon_threads)
        self.assertGreaterEqual(meter.TokenMeterHTTPServer.request_queue_size, 32)

    def test_dynamic_responses_cannot_be_reused_from_an_http_cache(self):
        handler = mock.Mock()
        meter.H._send(handler, "{}", "application/json")

        handler.send_header.assert_any_call("Cache-Control", "no-store, max-age=0")
        handler.send_header.assert_any_call("Pragma", "no-cache")
        handler.send_header.assert_any_call("Expires", "0")


if __name__ == "__main__":
    unittest.main()
