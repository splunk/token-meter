"""Sessions render guards, the dotted prompt-cache ring, and Frontier-only session figures."""

import json
import math
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from tests.dashboard_page import DASHBOARD_PAGE

PAGE = Path(DASHBOARD_PAGE).read_text()
NODE = shutil.which("node")

HARNESS = r"""
const fs=require('fs');
const page=fs.readFileSync(__PAGE__,'utf8');
function extract(name){
 let start=page.indexOf(`function ${name}(`);
 if(start<0)throw new Error(`missing ${name}`);
 // Skip the parameter list first: destructured defaults hold braces of their own.
 let i=page.indexOf('(',start),parens=0;
 for(;i<page.length;i++){if(page[i]==='(')parens++;else if(page[i]===')'&&--parens===0)break;}
 i=page.indexOf('{',i);let depth=0;
 for(;i<page.length;i++){if(page[i]==='{')depth++;else if(page[i]==='}'&&--depth===0)return page.slice(start,i+1);}
 throw new Error(`unclosed ${name}`);
}
function load(names){names.forEach(name=>eval(`globalThis.${name}=${extract(name)}`));}
function constant(name){const m=page.match(new RegExp(`^const ${name}=(.*);$`,'m'));if(!m)throw new Error(`missing ${name}`);return eval(m[1]);}
// A minimal DOM: elements count their writes; class lists, attributes, and text are plain state.
const writes={};
const count=key=>{writes[key]=(writes[key]||0)+1;};
class ClassList{constructor(){this.set=new Set();} contains(c){return this.set.has(c);} add(...c){c.forEach(x=>this.set.add(x));} remove(...c){c.forEach(x=>this.set.delete(x));} toggle(c,on){if(on===undefined)on=!this.set.has(c);on?this.set.add(c):this.set.delete(c);return on;}}
class El{
 constructor(id='',tag='div'){this.id=id;this.tagName=tag;this.classList=new ClassList();this.dataset={};this.attrs={};this.children=[];this._text='';this._html='';this.hidden=false;this.style={};}
 get textContent(){return this._text;} set textContent(v){this._text=String(v);count(`text:${this.id||this.tagName}`);}
 get innerHTML(){return this._html;} set innerHTML(v){this._html=String(v);count(`html:${this.id||this.tagName}`);}
 get className(){return [...this.classList.set].join(' ');} set className(v){this.classList.set=new Set(String(v).split(/\s+/).filter(Boolean));count(`class:${this.id||this.tagName}`);}
 getAttribute(n){return n in this.attrs?this.attrs[n]:null;} setAttribute(n,v){this.attrs[n]=String(v);count(`attr:${n}`);} removeAttribute(n){if(n in this.attrs){delete this.attrs[n];count(`attr:${n}`);}}
 querySelector(){return null;} querySelectorAll(){return [];} contains(){return false;} closest(){return null;}
}
const nodes={};
const $=id=>nodes[id]||(nodes[id]=new El(id));
const document={activeElement:null,querySelectorAll:()=>[]};
const esc=value=>String(value??'').replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;');
const money=value=>'$'+Number(value||0).toFixed(2),compactNumber=value=>String(value),f=value=>String(value),pct=value=>`${Math.round(value*100)}%`;
const countWord=(n,word,plural=word+'s')=>Number(n)===1?word:plural;
"""

RENDER_HARNESS = HARNESS + r"""
let LATEST=null,currentPanel='sessions',sessionScope='current',branchUiReady=true,themeEpoch=1,spendProjects=[];
let currentSessionSeenIds=[],currentSessionSeenReady=false,currentSessionDragId='',currentSessionRuntimeFilter='all',currentSessionActivityFilter='all';
let currentSessionOrder=[],currentSessionOrderNeedsNewestMigration=false,currentSessionGridHtml='',currentDaySummaryKey=null,currentSessionChipEpoch=-1;
const localStorage={setItem(){},getItem(){return null;}};
const CURRENT_SESSION_ORDER_KEY='k',CURRENT_SESSION_ORDER_LIMIT=64,CURRENT_SESSION_ORDER_MIGRATION_KEY='m',CAPABILITY_SUMMARY_TIP='tip';
const CACHE_RING_TICKS=30,CACHE_RING_SIZE=40;
function renderCurrentDaySummary(){count('daySummary');}
function syncSessionFlow(){count('flow');}
function renderCurrentSessionRuntimeFilters(){} function renderCurrentSessionActivityFilters(){}
function dailyBaseline(){return {sessionCost:0,estimated:false};}
function localDayKey(){return '2026-11-25';}
const appFilterGroup=row=>row.provider,appFilterLabel=row=>row.provider,runtimeId=row=>row.provider,runtimeLabel=row=>row.provider;
const costValueHtml=value=>value,costUnavailableAttrs=()=>'',currentSessionContext=()=>'40%',speedFmt=String;
const sessionModelSummary=()=>({latest:'model',others:[],extra:0,title:''}),currentSessionModelName=value=>value;
const sessionDisplayThroughput=()=>({available:false}),currentSessionContextSparkline=()=>({html:'',summary:'Context.'});
const typicalCaption=()=>'',readoutScaleHtml=()=>'',desktopSessionTarget=()=>null,sessionBranchFor=()=>'';
const capabilitySummaryText=()=>'Skills 1/2',branchChipHtml=()=>'',runtimeHarness=()=>'var(--provider-claude)',runtimeMarkSvg=()=>'';
const currentSessionsEmptyHtml=()=>'<section>empty</section>',currentSessionsFilteredEmptyHtml=()=>'<section>filtered</section>';
const order=[];
function placeCurrentSessionBranchChips(){count('chips');order.push('place');currentSessionChipEpoch=themeEpoch;}
function tickCacheRings(){count('tick');order.push('tick');}
// Animation frames queue up and run only when a test flushes them, like a browser between tasks.
let frames=[];
const requestAnimationFrame=fn=>{frames.push(fn);return frames.length;},flushFrames=()=>{const due=frames;frames=[];due.forEach(fn=>fn());order.push('frame');};
const focusCurrentSessionCard=()=>{};
load(['sessionFlowSnapshot','currentSessionsOverviewVisible','currentSessionGridVisible','currentDaySummaryChanged',
 'renderCurrentSessions','orderedCurrentSessionRows','saveCurrentSessionOrder','currentSessionRuntimeView','currentSessionFilterView',
 'currentSessionActivity','currentSessionIdsMatch','currentSessionBranchSibling','cacheRingState','cacheRingAttrs','cacheRingSkeleton','cacheRingHtml']);
globalThis.CACHE_RING_SKELETON=cacheRingSkeleton();
const row=(id,cost,extra={})=>({id,provider:'claude',session_name:`Task ${id}`,project:'repo',activity_state:'working',cost,mtime:1700000000,
 availability:{cost:true},cache_window:{anchor_ts:1700000000,window_s:300,basis:'recorded'},...extra});
const daily=[{day:'2026-11-25',cost:1,sessions:1}];
const state=rows=>({xsession:{current_sessions:rows,daily}});
const view=$('view-session');
const snapshot=label=>({label,grid:writes['html:current-session-grid']||0,ticks:writes.tick||0,chips:writes.chips||0,day:writes.daySummary||0,flow:writes.flow||0});
"""


def run_node(script):
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(result.stderr[-4000:])
    return json.loads(result.stdout)


@unittest.skipUnless(NODE, "Node.js is required for dashboard JavaScript")
class CacheRingDotTests(unittest.TestCase):
    def test_skeleton_puts_thirty_dot_starts_on_one_radius(self):
        out = run_node(HARNESS.replace("__PAGE__", json.dumps(str(Path(DASHBOARD_PAGE)))) + r"""
const CACHE_RING_TICKS=30,CACHE_RING_SIZE=40;
load(['cacheRingSkeleton']);
process.stdout.write(JSON.stringify(cacheRingSkeleton()));
""")
        lines = re.findall(r'<line x1="([\d.]+)" y1="([\d.]+)" x2="([\d.]+)" y2="([\d.]+)"/>', out)
        self.assertEqual(len(lines), 30)
        self.assertNotIn("class=", out, "the skeleton bakes in no lit state")
        for x1, y1, x2, y2 in lines:
            self.assertAlmostEqual(math.hypot(float(x1) - 20, float(y1) - 20), 16.5, delta=0.01)
            self.assertAlmostEqual(math.hypot(float(x2) - 20, float(y2) - 20), 19, delta=0.01)
        # The first dot sits at 12 o'clock.
        self.assertEqual(lines[0][:2], ("20.00", "3.50"))

    def test_dots_are_zero_length_round_dashes_lit_three_spent_two(self):
        self.assertIn(".cacheRing line{stroke:var(--ring-tone);stroke-width:3;stroke-linecap:round;stroke-dasharray:0 99;", PAGE)
        self.assertIn(".cacheRing line.off{stroke:var(--line2);stroke-width:2}", PAGE)
        self.assertIn(".cacheRing line.maybe{stroke:var(--warn);opacity:.45}", PAGE, "the native ring draws maybe dots at 0.45")
        self.assertIn("CACHE_RING_TICKS=30", PAGE)

    def test_dot_states_switch_off_clockwise_and_keep_the_maybe_band(self):
        out = run_node(HARNESS.replace("__PAGE__", json.dumps(str(Path(DASHBOARD_PAGE)))) + r"""
const CACHE_RING_TICKS=30;
load(['cacheRingDotStates']);
process.stdout.write(JSON.stringify({
 half:cacheRingDotStates({left:150,maxLeft:Infinity,total:300}),
 range:cacheRingDotStates({left:-30,maxLeft:3300,total:3600}),
 cold:cacheRingDotStates({left:-5,maxLeft:-5,total:300}),
}));
""")
        self.assertEqual(out["half"], ["off"] * 15 + [""] * 15)
        self.assertEqual(out["range"], ["off"] * 2 + ["maybe"] * 28)
        self.assertEqual(out["cold"], ["off"] * 30)

    def test_ticker_writes_only_changed_values_and_only_on_the_current_grid(self):
        out = run_node(HARNESS.replace("__PAGE__", json.dumps(str(Path(DASHBOARD_PAGE)))) + r"""
const CACHE_RING_TICKS=30;
let visible=true;
function currentSessionGridVisible(){return visible;}
const cacheClock=s=>`${Math.floor(Math.ceil(s)/60)}:${String(Math.ceil(s)%60).padStart(2,'0')}`;
load(['cacheRingState','cacheRingDotStates','cacheRingFace','cacheRingText','cacheWindowLabel','currentSessionAge','tickCacheRings']);
const age=new El('age','span');age.dataset.mtime='1000';
const ring=new El('ring','span');ring.dataset.cacheWindow=JSON.stringify({anchor_ts:1000,window_s:300,basis:'recorded'});
const face=new El('face','b'),dots=Array.from({length:30},()=>new El('','line'));
ring.querySelector=selector=>selector==='b'?face:null;ring.querySelectorAll=selector=>selector==='line'?dots:[];
const grid=$('current-session-grid');
grid.querySelectorAll=selector=>selector.startsWith('.currentSessionAge')?[age]:selector.startsWith('.cacheRing')?[ring]:[];
let clock=1150*1000;Date.now=()=>clock;
const total=()=>Object.values(writes).reduce((a,b)=>a+b,0);
tickCacheRings();const first=total();
const state={age:age.textContent,face:face.textContent,className:ring.className,label:ring.getAttribute('aria-label'),
 off:dots.filter(dot=>dot.getAttribute('class')==='off').length,lit:dots.filter(dot=>dot.getAttribute('class')===null).length};
tickCacheRings();const repeat=total()-first;
clock+=1000;tickCacheRings();const nextSecond=total()-first-repeat;
visible=false;clock+=1000;const before=total();tickCacheRings();const hidden=total()-before;
process.stdout.write(JSON.stringify({state,repeat,nextSecond,hidden}));
""")
        self.assertEqual(out["state"]["age"], "2m ago")
        self.assertEqual(out["state"]["face"], "2:30")
        self.assertEqual(out["state"]["className"], "cacheRing good")
        self.assertTrue(out["state"]["label"].startswith("Cache warm · 2:30 left"))
        self.assertEqual((out["state"]["off"], out["state"]["lit"]), (15, 15))
        self.assertEqual(out["repeat"], 0, "an unchanged second writes nothing")
        # One second later only the face and the labels change (aria-label and title).
        self.assertEqual(out["nextSecond"], 3)
        self.assertEqual(out["hidden"], 0, "rings off screen are not ticked")


@unittest.skipUnless(NODE, "Node.js is required for dashboard JavaScript")
class CurrentSessionRenderGuardTests(unittest.TestCase):
    def run_render(self, body):
        return run_node(RENDER_HARNESS.replace("__PAGE__", json.dumps(str(Path(DASHBOARD_PAGE)))) + body)

    def test_card_markup_has_no_time_derived_text(self):
        out = self.run_render(r"""
view.classList.add('on');LATEST=state([row('a',1.5)]);
renderCurrentSessions();
process.stdout.write(JSON.stringify($('current-session-grid').innerHTML));
""")
        self.assertIn('<span class=currentSessionAge data-mtime="1700000000"></span>', out)
        self.assertNotIn(" ago", out)
        ring = re.search(r"<span class=cacheRing [^>]*>.*?</span>", out).group(0)
        self.assertIn("role=img", ring)
        self.assertIn("data-cache-window=", ring)
        self.assertNotIn("aria-label", ring)
        self.assertNotIn("class=off", ring)
        self.assertIn("<b></b>", ring)

    def test_unchanged_polls_rebuild_nothing_and_changes_rebuild_once(self):
        out = self.run_render(r"""
view.classList.add('on');
const steps=[];
LATEST=state([row('a',1.5),row('b',2)]);renderCurrentSessions();steps.push(snapshot('first'));
renderCurrentSessions();steps.push(snapshot('same state'));
LATEST=state([row('a',1.5),row('b',2)]);renderCurrentSessions();steps.push(snapshot('equal new payload'));
LATEST=state([row('a',1.75),row('b',2)]);renderCurrentSessions();steps.push(snapshot('cost changed'));
themeEpoch++;renderCurrentSessions();steps.push(snapshot('theme commit'));
process.stdout.write(JSON.stringify(steps));
""")
        grid = [step["grid"] for step in out]
        self.assertEqual(grid, [1, 1, 1, 2, 2])
        # The ticker runs after every visible render; chips re-place only after a rebuild or a theme commit.
        self.assertEqual([step["ticks"] for step in out], [1, 2, 3, 4, 5])
        self.assertEqual([step["chips"] for step in out], [1, 1, 1, 2, 3], "frames not yet flushed: one placement per rebuild or theme commit")
        # The today strip redraws only when its day rows change, not on every poll.
        self.assertEqual([step["day"] for step in out], [1, 1, 1, 1, 1])

    def test_off_route_polls_draw_nothing_and_a_route_change_into_sessions_renders(self):
        out = self.run_render(r"""
const steps=[];
LATEST=state([row('a',1.5)]);
renderCurrentSessions(LATEST);steps.push(snapshot('on Spend'));
LATEST=state([row('a',1.75)]);renderCurrentSessions(LATEST);steps.push(snapshot('still on Spend'));
view.classList.add('on');currentPanel='run';renderCurrentSessions(LATEST);steps.push(snapshot('session detail'));
currentPanel='sessions';sessionScope='all';renderCurrentSessions(LATEST);steps.push(snapshot('All sessions'));
// The router's route change into Current sessions calls renderCurrentSessions() with no argument.
sessionScope='current';renderCurrentSessions();steps.push(snapshot('route change into Sessions'));
process.stdout.write(JSON.stringify({steps,html:$('current-session-grid').innerHTML,seen:currentSessionSeenIds}));
""")
        steps = {step["label"]: step for step in out["steps"]}
        for label in ("on Spend", "still on Spend", "session detail"):
            self.assertEqual((steps[label]["grid"], steps[label]["day"], steps[label]["flow"]), (0, 0, 0), label)
        # Other Sessions scopes still show the today strip and header status, never the hidden grid.
        self.assertEqual((steps["All sessions"]["grid"], steps["All sessions"]["day"], steps["All sessions"]["flow"]), (0, 1, 1))
        self.assertEqual(steps["route change into Sessions"]["grid"], 1)
        self.assertIn("$1.75", out["html"], "the route change draws the latest data")
        # Arrivals were tracked while off screen, so returning does not replay them.
        self.assertNotIn("is-arriving", out["html"])
        self.assertEqual(out["seen"], ["a"])

    def test_chips_are_placed_after_the_ticker_and_again_on_the_next_frame(self):
        out = self.run_render(r"""
view.classList.add('on');
LATEST=state([row('a',1.5)]);renderCurrentSessions();
const firstRender=[...order],queued=frames.length;flushFrames();
const afterFrame=writes.chips;order.length=0;
renderCurrentSessions();const unchangedQueued=frames.length;
process.stdout.write(JSON.stringify({firstRender,queued,afterFrame,unchangedQueued,order}));
""")
        # The ticker fills ages and rings before the chips measure the cards, and the chips settle once more on the
        # next animation frame; an unchanged poll schedules nothing.
        self.assertEqual(out["firstRender"], ["tick", "place"])
        self.assertEqual(out["queued"], 1)
        self.assertEqual(out["afterFrame"], 2)
        self.assertEqual(out["unchangedQueued"], 0)
        self.assertEqual(out["order"], ["tick"])

    def test_a_theme_commit_places_chips_on_the_next_frame(self):
        self.assertIn("new MutationObserver(()=>requestAnimationFrame(()=>placeCurrentSessionBranchChips())).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});", PAGE)

    def test_render_path_is_wired_to_the_router_and_one_second_ticker(self):
        self.assertIn("setInterval(()=>{if(!document.hidden)tickCacheRings();},1000);", PAGE)
        route = PAGE[PAGE.index("if(h==='sessions'||h==='sessions-all'||h==='sessions-compare'||h==='sessions-branches'||h==='current-sessions'){"):]
        self.assertIn("showCurrentPanel('sessions');\n renderCurrentSessions();", route[:800])


CHART_HARNESS = HARNESS + r"""
const metricAvailable=constant('metricAvailable'),svgTextPx=constant('svgTextPx'),CHART_MODES=constant('CHART_MODES'),IO_CALLS_CAPTION=constant('IO_CALLS_CAPTION'),IO_CALLS_CAPTION_NO_CACHE=constant('IO_CALLS_CAPTION_NO_CACHE');
let CURRENT=null,chartMode='context',chartScale='linear',themeEpoch=1,figures='bars';
let ioChartKey='',ioChartShowExecution=null,ioChartHideExecution=null,ioCallsKey='';
const CHART={ink:'INK',input:'IN',output:'OUT',tool:'TOOL',side:'SIDE',cacheRead:'CR',cacheWrite:'CW',cost:'COST',context:'CTX',reason:'R',costFill:'CF',axis:'AX',grid:'GR',lbl:'LBL',tip:'TIP',tipLine:'TL'};
const cssVar=name=>name==='--bad'?'BAD':name==='--figures'?figures:'';
const tint=(name,alpha)=>`tint(${name},${alpha})`;
const node=()=>({setAttribute(){},addEventListener(){},style:{}});
const svg=new El('iochart','svg');svg.clientWidth=800;svg.clientHeight=244;
svg.getBoundingClientRect=()=>({width:svg.clientWidth,height:svg.clientHeight});svg.querySelector=()=>node();
// The chart's wrapper and the calls field around it.
const created=[];
const wrap={previousElementSibling:null,before(element){created.push(element);this.previousElementSibling=element;element.remove=()=>{this.previousElementSibling=null;};}};
svg.closest=selector=>selector==='.chartwrap'?wrap:null;
nodes.iochart=svg;
document.createElement=tag=>{const element=new El('',tag);element.addEventListener=()=>{};element.classList.contains=c=>element.className.split(' ').includes(c);return element;};
load(['ioChartInputKey','drawIO','chartLegendHtml','legendField','tipAttrs','chartParts','cumulativeChartParts','integerTicks','ticks','scaledY','moneyTick','syncIoCallsField','ioCallsField','drawStepsChart','requestStepGroups']);
const series=n=>Array.from({length:n},(_,i)=>({i:i+1,in:1000+i*100,out:50,cost:.5+i/10,cache_read:i?900:0,cache_write:i?100:950,tools:2,context_pct:.2+i/100}));
const draws=()=>writes['html:iochart']||0;
"""


@unittest.skipUnless(NODE, "Node.js is required for dashboard JavaScript")
class SessionDetailChartTests(unittest.TestCase):
    def run_chart(self, body):
        return run_node(CHART_HARNESS.replace("__PAGE__", json.dumps(str(Path(DASHBOARD_PAGE)))) + body)

    def test_unchanged_inputs_skip_the_redraw(self):
        out = self.run_chart(r"""
CURRENT={provider:'claude',availability:{},series:series(12)};
const steps=[];
drawIO(CURRENT.series);steps.push(draws());
CURRENT={...CURRENT,series:series(12)};drawIO(CURRENT.series);steps.push(draws());
svg.clientWidth=640;drawIO(CURRENT.series);steps.push(draws());
themeEpoch++;drawIO(CURRENT.series);steps.push(draws());
chartMode='cost';drawIO(CURRENT.series);steps.push(draws());
CURRENT={...CURRENT,series:series(13)};drawIO(CURRENT.series);steps.push(draws());
process.stdout.write(JSON.stringify(steps));
""")
        # An equal fresh payload draws nothing; width, theme, mode, and a new execution each draw once.
        self.assertEqual(out, [1, 1, 2, 3, 4, 5])

    def test_high_context_line_uses_its_legend_color(self):
        out = self.run_chart(r"""
CURRENT={provider:'claude',availability:{},series:series(6)};
drawIO(CURRENT.series);
process.stdout.write(JSON.stringify(svg.innerHTML));
""")
        lines = re.findall(r'<line x1="[\d.]+" y1="([\d.]+)" x2="[\d.]+" y2="[\d.]+" stroke="([^"]+)" stroke-dasharray="5 5"', out)
        self.assertEqual([stroke for _, stroke in lines], ["BAD", "CW"], "85% is --bad, 70% the warn-colored line")
        self.assertNotIn('stroke="COST" stroke-dasharray="5 5"', out)
        self.assertIn("legendField('85% high','var(--bad)'", PAGE)

    def test_only_measured_bars_wide_and_tall_enough_take_the_data_mask(self):
        out = self.run_chart(r"""
chartMode='cost';
const run=(current,n)=>{ioChartKey='';CURRENT={...current,series:series(n)};drawIO(CURRENT.series);const html=svg.innerHTML;return {bars:(html.match(/<rect[^>]*rx="2"/g)||[]).length,dotted:(html.match(/<rect class=ioDots /g)||[]).length};};
process.stdout.write(JSON.stringify({
 measured:run({provider:'claude',availability:{}},12),
 estimate:run({provider:'claude',availability:{},cost_approx:true},12),
 cursor:run({provider:'cursor',availability:{}},12),
 thin:run({provider:'claude',availability:{}},120),
}));
""")
        self.assertEqual(out["measured"]["dotted"], out["measured"]["bars"])
        self.assertEqual(out["estimate"]["dotted"], 0)
        self.assertEqual(out["cursor"]["dotted"], 0)
        self.assertEqual(out["thin"]["dotted"], 0, "bars under 8px wide stay solid")
        self.assertIn(".previewChartSlot .chart .ioDots,body.sessionRoute #view-session.sessionDetail .previewChartSlot .chart .ioBand{-webkit-mask:var(--data-mask);mask:var(--data-mask);-webkit-mask-position:50% 100%;mask-position:50% 100%}", PAGE)
        # Cache bands take the same dots; a flat theme lifts them so the dotted band reads like the solid one.
        self.assertIn("const bandDots=!(CURRENT?.provider==='cursor'||CURRENT?.token_estimate===true);", PAGE)
        self.assertIn('<path${bandDots?\' class=ioBand\':\'\'} d="${d} Z" fill="${col}" fill-opacity="${opacity}"/>', PAGE)
        self.assertIn(".previewChartSlot .chart .ioBand{fill-opacity:calc(.18 + .2*(1 - var(--atmos)))}", PAGE)

    def test_cache_bands_and_step_bars_follow_the_dot_rules(self):
        out = self.run_chart(r"""
const run=(mode,current,n)=>{chartMode=mode;ioChartKey='';CURRENT={...current,series:series(n)};drawIO(CURRENT.series);const html=svg.innerHTML;
 return {bands:(html.match(/<path class=ioBand /g)||[]).length,solidBands:(html.match(/<path d="[^"]*Z" fill="[^"]*" fill-opacity/g)||[]).length,
  dottedBars:(html.match(/<rect class=ioDots [^>]*\/>/g)||[]).filter(rect=>!rect.includes('stroke')).length,outlinedBars:(html.match(/<rect x=[^>]*stroke-width="1"/g)||[]).length};};
process.stdout.write(JSON.stringify({
 measured:run('tokens',{provider:'claude',availability:{}},12),
 cursor:run('tokens',{provider:'cursor',availability:{}},12),
 estimated:run('tokens',{provider:'claude',availability:{},token_estimate:true},12),
 stepsBars:run('steps',{provider:'claude',availability:{}},12),
 steps:(figures='dots',run('steps',{provider:'claude',availability:{}},12)),
}));
""")
        self.assertEqual(out["measured"]["bands"], 2)
        self.assertEqual(out["measured"]["solidBands"], 0)
        # Estimated token traces keep solid bands, so a dot always means measured evidence.
        for name in ("cursor", "estimated"):
            with self.subTest(trace=name):
                self.assertEqual(out[name]["bands"], 0)
                self.assertEqual(out[name]["solidBands"], 2)
        # Steps bars dot in their series color and drop the outline in a dot theme; other themes keep the outline.
        self.assertEqual(out["stepsBars"]["dottedBars"], 0)
        self.assertGreater(out["stepsBars"]["outlinedBars"], 0)
        self.assertGreater(out["steps"]["dottedBars"], 0)
        self.assertEqual(out["steps"]["outlinedBars"], 0)


@unittest.skipUnless(NODE, "Node.js is required for dashboard JavaScript")
class FrontierCallsFieldTests(unittest.TestCase):
    def run_field(self, body):
        return run_node(CHART_HARNESS.replace("__PAGE__", json.dumps(str(Path(DASHBOARD_PAGE)))) + body)

    def test_dots_follow_input_size_cache_rewrites_and_evidence(self):
        out = self.run_field(r"""
const rows=[
 {in:100,cache_read:0,cache_write:900},   // re-wrote the cache: orange
 {in:1000,cache_read:900,cache_write:10}, // carried the most context: largest blue dot
 {in:250,cache_read:200,cache_write:0},
 {in:400,cache_read:300,cache_write:5},
];
const field=ioCallsField(rows,{width:800,available:i=>i!==3});
const radii=d=>[...d.matchAll(/a([\d.]+) /g)].map(m=>+m[1]).filter((r,i)=>i%2===0);
process.stdout.write(JSON.stringify({field:{...field,paths:undefined},output:radii(field.paths.output),cost:radii(field.paths.cost),faint:radii(field.paths.faint),
 estimate:Object.fromEntries(Object.entries(ioCallsField(rows,{width:800,estimate:true}).paths).map(([k,v])=>[k,radii(v).length])),
 none:[ioCallsField([],{width:800}),ioCallsField(rows,{width:0})]}));
""")
        field = out["field"]
        self.assertEqual((field["count"], field["rewrote"], field["unavailable"], field["pitch"], field["cols"], field["height"]), (4, 1, 1, 8, 100, 8))
        # Area follows the input carried, never a zero dot; the largest call gets the full 45% of the pitch.
        self.assertEqual(max(out["output"]), 3.6)
        self.assertAlmostEqual(out["output"][1], 3.6 * math.sqrt(250 / 1000), places=1)
        self.assertGreater(min(out["output"] + out["cost"]), 0)
        self.assertEqual(len(out["cost"]), 1)
        self.assertEqual(out["faint"], [2.16], "a call without token evidence is a faint ring")
        # Estimated token counts draw rings in their tone, so a dot always means measured evidence.
        self.assertEqual(out["estimate"], {"output": 0, "cost": 0, "neutral": 0, "outputRing": 3, "costRing": 1, "neutralRing": 0, "faint": 0})
        self.assertEqual(out["none"], [None, None])

    def test_large_sessions_shrink_the_pitch_to_stay_about_120px_tall(self):
        out = self.run_field(r"""
const rows=n=>Array.from({length:n},(_,i)=>({in:100+i}));
process.stdout.write(JSON.stringify([1924,5000,40000].map(n=>{const f=ioCallsField(rows(n),{width:807});return [f.pitch,f.cols,f.height];})));
""")
        pitch, cols, height = out[0]
        self.assertLess(pitch, 8)
        self.assertLessEqual(height, 120)
        self.assertLessEqual(out[1][2], 120)
        self.assertEqual(out[2][0], 3, "the pitch never drops below 3px; very long sessions grow taller instead")

    def test_field_exists_only_in_frontier_and_never_for_session_aggregates(self):
        out = self.run_field(r"""
const html=()=>wrap.previousElementSibling?wrap.previousElementSibling.innerHTML:'';
const steps={};
CURRENT={provider:'claude',availability:{},series:series(10)};
drawIO(CURRENT.series);steps.classic={created:created.length,field:!!wrap.previousElementSibling};
figures='dots';themeEpoch++;drawIO(CURRENT.series);steps.frontier={created:created.length,html:html()};
const before=writes['html:']||0;chartMode='tools';drawIO(CURRENT.series);steps.modeChange=(writes['html:']||0)-before;
CURRENT={...CURRENT,execution_coverage:{basis:'session_aggregate'}};drawIO(CURRENT.series);steps.aggregate=!!wrap.previousElementSibling;
CURRENT={...CURRENT,execution_coverage:{}};drawIO(CURRENT.series);figures='bars';themeEpoch++;drawIO(CURRENT.series);steps.backToClassic=!!wrap.previousElementSibling;
process.stdout.write(JSON.stringify(steps));
""")
        self.assertEqual(out["classic"], {"created": 0, "field": False}, "existing themes get no field and no markup")
        self.assertEqual(out["frontier"]["created"], 1)
        self.assertIn('role=img aria-label="10 model calls, 1 re-wrote the cache"', out["frontier"]["html"])
        self.assertIn("<figcaption class=waffleUnit>Each dot is one model call. Size shows the context it carried. Orange calls re-wrote the cache.</figcaption>", out["frontier"]["html"])
        self.assertIn('style="color:var(--c-cost)"', out["frontier"]["html"])
        self.assertIn('style="color:var(--c-output)"', out["frontier"]["html"])
        self.assertEqual(out["modeChange"], 0, "a chart mode change does not redraw the field")
        self.assertFalse(out["aggregate"])
        self.assertFalse(out["backToClassic"])

    def test_without_cache_evidence_calls_take_a_neutral_tone_and_say_so(self):
        # Cursor reports availability.cache=false and zero cache fields: no call may claim a cache rewrite.
        out = self.run_field(r"""
const rows=[{in:100,cache_read:0,cache_write:900},{in:1000,cache_read:900,cache_write:10}];
const field=ioCallsField(rows,{width:800,cache:false}),ringed=ioCallsField(rows,{width:800,cache:false,estimate:true});
const kinds=f=>Object.fromEntries(Object.entries(f.paths).filter(([,v])=>v).map(([k,v])=>[k,(v.match(/M/g)||[]).length]));
figures='dots';
CURRENT={provider:'cursor',availability:{cache:false},series:series(10)};drawIO(CURRENT.series);
const html=wrap.previousElementSibling.innerHTML;
process.stdout.write(JSON.stringify({rewrote:field.rewrote,kinds:kinds(field),ringed:kinds(ringed),html}));
""")
        self.assertEqual(out["rewrote"], 0)
        self.assertEqual(out["kinds"], {"neutral": 2})
        self.assertEqual(out["ringed"], {"neutralRing": 2})
        html = out["html"]
        self.assertIn('aria-label="10 model calls, cache evidence unavailable (estimated token counts)"', html)
        self.assertNotIn("var(--c-cost)", html)
        self.assertNotIn("var(--c-output)", html)
        self.assertIn('style="color:var(--dim)"', html)
        self.assertIn("<figcaption class=waffleUnit>Each dot is one model call. Size shows the context it carried.</figcaption>", html)



COMPARE_HARNESS = HARNESS + r"""
let compareBodyHtml='',compareData=null,compareChartInset=34,compareChartMetric='cost';
const CHART={grid:'GR',lbl:'LBL',ink:'INK'};
const COMPARE_COLORS=['A','B','C','D'],compareColor=i=>COMPARE_COLORS[i%4];
const svgTextPx=constant('svgTextPx');
load(['renderComparison','compareChartWidth','compareChartLines','compareChartPlot']);
"""


@unittest.skipUnless(NODE, "Node.js is required for dashboard JavaScript")
class CompareRenderTests(unittest.TestCase):
    def run_compare(self, body):
        return run_node(COMPARE_HARNESS.replace("__PAGE__", json.dumps(str(Path(DASHBOARD_PAGE)))) + body)

    def test_aria_live_region_is_written_only_when_its_markup_changes(self):
        out = self.run_compare(r"""
let markup='<section>A</section>';
function comparisonHtml(){return markup;}
function redrawCompareChart(){count('redraw');}
renderComparison();renderComparison();
markup='<section>B</section>';renderComparison();
process.stdout.write(JSON.stringify({writes:writes['html:compare-body'],redraws:writes.redraw,html:$('compare-body').innerHTML}));
""")
        self.assertEqual(out, {"writes": 2, "redraws": 3, "html": "<section>B</section>"})
        self.assertIn("<div id=compare-body aria-live=polite></div>", PAGE)

    def test_cumulative_chart_draws_in_a_pixel_viewbox_from_the_static_container(self):
        out = self.run_compare(r"""
$('compare-body').clientWidth=889;
const sessions=[{letter:'A',title:'a',cost:2,series:[{i:1,cost:1},{i:2,cost:2}]},{letter:'B',title:'b',cost:1,series:[{i:1,cost:.5},{i:3,cost:1}]}];
const plot=compareChartPlot(compareChartLines(sessions),compareChartWidth());
process.stdout.write(JSON.stringify({VW:plot.VW,VH:plot.VH,sizes:[...new Set([...plot.inner.matchAll(/font-size="([\d.]+)"/g)].map(m=>m[1]))]}));
""")
        self.assertEqual((out["VW"], out["VH"]), (855, 250))
        self.assertEqual(out["sizes"], ["11"])
        compare = PAGE[PAGE.index("function compareChartHtml(sessions){"):PAGE.index("function compareBreakdownHtml(sessions){")]
        self.assertNotIn("VW=1040", compare)
        self.assertIn("svgTextPx(svg,width,250)", compare)
        self.assertIn("new ResizeObserver(()=>{clearTimeout(compareChartTimer);compareChartTimer=setTimeout(redrawCompareChart,120);}).observe($('compare-body'))", compare)


class FrontierFigureMarkupTests(unittest.TestCase):
    def test_token_split_color_sits_behind_its_label(self):
        self.assertIn("return `<span style=\"width:${w}%;--seg:${c}\">${w>8?`<b>${f(sem[k])}</b>`:''}</span>`;", PAGE)
        self.assertIn('#sembar span:before{content:"";position:absolute;inset:0;z-index:-1;background:var(--seg)}', PAGE)
        self.assertIn("@container (min-width:8px){#sembar span:before{-webkit-mask:var(--data-mask);mask:var(--data-mask)}}", PAGE)
        self.assertIn("#sembar span>b{padding:0 4px;border-radius:var(--radius-2xs);background:var(--seg);font-weight:inherit}", PAGE)
        self.assertIn(".usageSplit .legend .dot{-webkit-mask:var(--data-mask);mask:var(--data-mask)}", PAGE)

    def test_compare_breakdown_masks_only_wide_segments_and_matches_its_legend(self):
        self.assertIn('<i style="width:${100*s.cost_parts[key]/s.cost}%;--seg:${color}"', PAGE)
        self.assertIn('.compareBreakdownBar i:before{content:"";position:absolute;inset:0;background:var(--seg)}', PAGE)
        self.assertIn("@container (min-width:8px){.compareBreakdownBar i:before{-webkit-mask:var(--data-mask);mask:var(--data-mask)}}", PAGE)
        self.assertIn(".compareLegend i.part{-webkit-mask:var(--data-mask);mask:var(--data-mask)}", PAGE)

    def test_thin_bars_keep_solid_fills_and_take_dotted_tracks(self):
        for rule in (
            ".sbar{background:rgb(var(--tint-rgb)/.045) var(--track-texture)}",
            "body.sessionRoute #view-session.sessionDetail .previewPressureBar{border-color:rgb(var(--edge-rgb)/.2);background:rgb(var(--bg-rgb)/.72) var(--track-texture)}",
            "linear-gradient(90deg,var(--accent) 0 var(--budget-fill),rgb(var(--tint-rgb)/.075) var(--budget-fill) 100%),var(--track-texture)}",
        ):
            self.assertIn(rule, PAGE)
        self.assertNotRegex(PAGE, r"\.sbar i\{[^}]*mask")
        self.assertNotRegex(PAGE, r"previewPressureBar i\{[^}]*mask")
        self.assertNotIn("currentSessionContextSpark", PAGE[PAGE.index("const PIXEL_CHARTS="):PAGE.index("const PIXEL_CHARTS=") + 400])



@unittest.skipUnless(NODE, "Node.js is required for dashboard JavaScript")
class BranchChipPlacementTests(unittest.TestCase):
    def test_chips_read_every_anchor_before_writing_and_skip_unchanged_values(self):
        out = run_node(HARNESS.replace("__PAGE__", json.dumps(str(Path(DASHBOARD_PAGE)))) + r"""
let themeEpoch=7,currentSessionChipEpoch=-1;
load(['placeCurrentSessionBranchChips']);
const log=[];
const style=()=>{const values={};return new Proxy(values,{get:(t,k)=>t[k]??'',set:(t,k,v)=>{log.push(`write ${k}`);t[k]=v;return true;}});};
const slot=(i)=>{
 const chip={style:style(),dataset:{}},anchor={getBoundingClientRect(){log.push(`read anchor ${i}`);return {left:20+i,top:40,width:80.4,height:18};}};
 return {chip,getBoundingClientRect(){log.push(`read slot ${i}`);return {left:i,top:0};},querySelector:sel=>sel===':scope>.branchChip'?chip:sel==='.currentSessionBranchAnchor'?anchor:null};
};
const slots=[slot(0),slot(1),slot(2)];
const grid={getClientRects:()=>[{}],querySelectorAll:()=>slots};
placeCurrentSessionBranchChips(grid);
const first=[...log];log.length=0;
placeCurrentSessionBranchChips(grid);
process.stdout.write(JSON.stringify({first,second:log,epoch:currentSessionChipEpoch,placed:slots.map(s=>'placed' in s.chip.dataset),left:slots[2].chip.style.left}));
""")
        first = out["first"]
        last_read = max(i for i, step in enumerate(first) if step.startswith("read"))
        first_write = min(i for i, step in enumerate(first) if step.startswith("write"))
        self.assertLess(last_read, first_write, "all layout reads come before any style write")
        self.assertEqual(sum(step.startswith("write") for step in first), 12)
        self.assertEqual([step for step in out["second"] if step.startswith("write")], [], "unchanged positions are not rewritten")
        self.assertEqual(out["epoch"], 7)
        self.assertEqual(out["placed"], [True, True, True])
        self.assertEqual(out["left"], "20px")

    def test_a_hidden_grid_keeps_its_chips_and_places_them_once_shown(self):
        out = run_node(HARNESS.replace("__PAGE__", json.dumps(str(Path(DASHBOARD_PAGE)))) + r"""
let themeEpoch=3,currentSessionChipEpoch=-1;
load(['placeCurrentSessionBranchChips']);
const log=[];
// Hidden, every box measures as zero; shown, the anchor sits 12px into the slot.
let shown=false;
const zero={left:0,top:0,width:0,height:0};
const chip={style:{left:'12px',top:'40px',width:'81px',height:'18px'},dataset:{placed:''}};
const anchor={getBoundingClientRect(){log.push('read anchor');return shown?{left:112,top:40,width:80.4,height:18}:zero;}};
const slot={getBoundingClientRect(){log.push('read slot');return shown?{left:100,top:0}:zero;},querySelector:sel=>sel===':scope>.branchChip'?chip:sel==='.currentSessionBranchAnchor'?anchor:null};
const grid={getClientRects:()=>shown?[{}]:[],querySelectorAll:()=>[slot]};
themeEpoch=4;placeCurrentSessionBranchChips(grid);
const hidden={reads:log.length,style:{...chip.style},epoch:currentSessionChipEpoch};
shown=true;chip.style.top='0px';placeCurrentSessionBranchChips(grid);
process.stdout.write(JSON.stringify({hidden,shown:{style:{...chip.style},epoch:currentSessionChipEpoch}}));
""")
        self.assertEqual(out["hidden"]["reads"], 0, "a hidden grid is not measured")
        self.assertEqual(out["hidden"]["style"], {"left": "12px", "top": "40px", "width": "81px", "height": "18px"}, "the last positions stay")
        self.assertEqual(out["hidden"]["epoch"], -1, "the theme is not recorded as placed while hidden")
        self.assertEqual(out["shown"]["style"], {"left": "12px", "top": "40px", "width": "81px", "height": "18px"})
        self.assertEqual(out["shown"]["epoch"], 4)

    def test_grid_places_chips_after_a_rebuild_or_a_theme_commit_only(self):
        render = PAGE[PAGE.index("function renderCurrentSessions(state=LATEST){"):PAGE.index("// The card is one button, so its branch chip")]
        self.assertIn("if(branchUiReady&&(rebuilt||currentSessionChipEpoch!==themeEpoch)){\n  placeCurrentSessionBranchChips(currentGrid);\n  requestAnimationFrame(()=>placeCurrentSessionBranchChips(currentGrid));", render)
        self.assertIn("document.fonts?.addEventListener?.('loadingdone',()=>placeCurrentSessionBranchChips());", PAGE)


if __name__ == "__main__":
    unittest.main()
