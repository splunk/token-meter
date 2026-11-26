"""Frontier-only Work figures and the Work page's 15 s render guard (split from test_work_insights.py)."""

import json
import re
import subprocess
import unittest

from token_meter.domain import work as domain
from tests.dashboard_page import DASHBOARD_PAGE


class WorkFrontierFigureTests(unittest.TestCase):
    """The Frontier-only outcome pair and the 15 s render guard (labels and counts only)."""

    @classmethod
    def setUpClass(cls):
        with open(DASHBOARD_PAGE, encoding="utf-8") as handle:
            cls.page = handle.read()

    def run_node(self, body):
        harness = f"""
const fs=require('fs');const page=fs.readFileSync({json.dumps(DASHBOARD_PAGE)},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('){{',start)+1,depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
const helpers=page.match(/^const (f|countWord|esc|money)=.*$/gm).join('\\n').replace(/^const /gm,'var ');eval(helpers);
eval(page.slice(page.indexOf('// waffle-pure-start'),page.indexOf('// waffle-pure-end')).replace(/^const /gm,'var '));
eval(page.match(/^const WORK_OUTCOME_PAIR_MIN_SESSIONS=.*$/m)[0].replace(/^const /,'var '));
"""
        completed = subprocess.run(["node", "-e", harness + body], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def test_trend_areas_take_the_theme_data_mask(self):
        # Dots in Frontier, like the Efficiency trend areas; --data-mask is none elsewhere.
        self.assertIn('<path class=workArea style="fill:url(#${fill})"', self.page)
        self.assertIn(".workArea{-webkit-mask:var(--data-mask-up);mask:var(--data-mask-up)}", self.page)
        # Dots cover less than a solid fill: a flat theme (--atmos:0) lifts the gradient; others keep .28 to 0.
        self.assertIn('stop-opacity:calc(.28 + .24*(1 - var(--atmos)))', self.page)
        self.assertIn('stop-opacity:calc(.14*(1 - var(--atmos)))', self.page)

    def test_remaining_bars_take_the_dot_rules(self):
        page = self.page
        # Pushback bands dot only rows with enough samples; routine spend and its legend dot their fills.
        self.assertIn(".workBand:not([data-few=true]) .workBarTrack i{min-width:var(--dot-floor);-webkit-mask:var(--data-mask);mask:var(--data-mask)}", page)
        self.assertIn("${band.pushback?.rate>0?`<i style=", page)
        self.assertIn(".workSuggestBar span{min-width:var(--dot-floor)}", page)
        self.assertIn(".workSuggestBar span,.workSuggestLegend i{-webkit-mask:var(--data-mask);mask:var(--data-mask)}", page)
        # Models spend share: the color moves to a masked ::before; label plates stay solid.
        self.assertIn('style="flex:${portion} 1 0;--seg:${segment.color}"', page)
        self.assertNotIn('style="flex:${portion} 1 0;background:${segment.color}"', page)
        self.assertIn("@container (min-width:8px){:is(#view-models,.conceptFrame) .modelSpendSeg:not([data-estimated=true]):before{-webkit-mask:var(--data-mask);mask:var(--data-mask)}}", page)
        self.assertIn(":is(#view-models,.conceptFrame) .modelSpendSeg>:is(span,b){margin:0 -4px;padding:0 4px;", page)
        # Share bars skip zero segments and floor the rest at one dot in a dot theme.
        self.assertIn(".filter(([,count])=>count>0)", page)
        self.assertIn(".defBar i{display:block;height:100%;min-width:var(--dot-floor);", page)
        self.assertIn(".goalDriverBar i{display:block;height:100%;min-width:var(--dot-floor);", page)
        self.assertIn("<i class=currentSessionsHistoryBar${height>=8?' data-dots':''}", page)

    def test_outcome_tags_never_overlap(self):
        # The pair adds one-shot, rescued, and ended-on-pushback sessions, so a session may carry at most one.
        context = {"long_run": None, "big_spend": None, "team_children": 3}
        session = {"duration": 0, "turns": 2, "children": 0, "requests": [], "tasks": []}
        outcomes = ("accepted", "recovered", "ended_on_pushback", "unclear", "single_shot", "pending", "unjudged")
        for outcome in outcomes:
            with self.subTest(outcome=outcome):
                tags = set(domain.session_tags(session, context, outcome)) & {"one_shot", "rescued", "stuck"}
                self.assertLessEqual(len(tags), 1)

    def test_pair_needs_enough_sessions_and_judged_pushback(self):
        payload = self.run_node("""
eval(extract('workOutcomePairParts'));
const items=[{tag:'one_shot',sessions:60,spend:30,share:.3,judged_sessions:0},{tag:'rescued',sessions:15,spend:20,share:.2,judged_sessions:15},
 {tag:'stuck',sessions:5,spend:10,share:.1,judged_sessions:5},{tag:'marathon',sessions:9,spend:50,share:.5,judged_sessions:9}];
const insights=(sessions,list=items)=>({coverage:{sessions},tags:{items:list}});
process.stdout.write(JSON.stringify({
 ready:workOutcomePairParts(insights(100)),
 few:workOutcomePairParts(insights(99)),
 unjudged:workOutcomePairParts(insights(100,items.map(item=>item.tag==='stuck'?{...item,sessions:4,judged_sessions:4}:item))),
 inconsistent:workOutcomePairParts(insights(100,items.map(item=>item.tag==='one_shot'?{...item,sessions:90}:item))),
 noRescue:workOutcomePairParts(insights(100,items.filter(item=>item.tag!=='rescued').map(item=>item.tag==='stuck'?{...item,sessions:20,judged_sessions:20}:item))),
 unpriced:workOutcomePairParts(insights(100,items.map(item=>({...item,share:null})))),
 missing:workOutcomePairParts({coverage:{sessions:500}}),
}));
""")
        ready = payload["ready"]
        self.assertEqual(ready["sessions"], 100)
        self.assertAlmostEqual(ready["totalSpend"], 100)
        self.assertEqual([part["kind"] for part in ready["parts"]], ["one_shot", "rescued", "ended", "unjudged"])
        self.assertEqual([part["sessions"] for part in ready["parts"]], [60, 15, 5, 20])
        self.assertEqual([round(part["spend"], 6) for part in ready["parts"]], [30, 20, 10, 40])
        self.assertEqual([part["color"] for part in ready["parts"]],
                         ["var(--c-output)", "var(--c-cost)", "var(--fg)", "var(--faint)"])
        # Each judged session has exactly one outcome tag, so the remainder is pending or unclear: not yet judged.
        self.assertEqual([part.get("unknown", False) for part in ready["parts"]], [False, False, False, True])
        self.assertEqual(ready["parts"][3]["label"], "Not yet judged")
        self.assertIsNone(payload["few"], "under 100 covered sessions a 1% dot would be part of a session")
        self.assertIsNone(payload["unjudged"], "under 20 judged pushback sessions the split is noise")
        self.assertIsNone(payload["inconsistent"])
        self.assertEqual([part["sessions"] for part in payload["noRescue"]["parts"]], [60, 0, 20, 20])
        self.assertIsNone(payload["unpriced"]["totalSpend"])
        self.assertIsNone(payload["unpriced"]["parts"][3]["spend"], "unknown spend is never a measured zero")
        self.assertIsNone(payload["missing"])

    def test_pair_is_frontier_only_and_stands_in_its_own_card(self):
        payload = self.run_node("""
eval(['workPercent','workOutcomePairParts','renderWorkOutcomePair'].map(extract).join('\\n'));
const insertSection=(html,store)=>{const node={innerHTML:'',id:html.match(/ id=([\\w-]+)/)[1],className:html.match(/class="([^"]+)"/)[1],label:html.match(/aria-labelledby=([\\w-]+)/)[1]};node.remove=()=>{delete store[node.id];};store[node.id]=node;};
const nodes={};nodes['w-module-tags']={insertAdjacentHTML(position,html){if(position==='afterend')insertSection(html,nodes);}};
const $=id=>nodes[id]||null,setLogHtml=(element,html)=>{element.innerHTML=html;};
const items=[{tag:'one_shot',sessions:60,spend:30,share:.3,judged_sessions:0},{tag:'rescued',sessions:15,spend:20,share:.2,judged_sessions:15},{tag:'stuck',sessions:5,spend:10,share:.1,judged_sessions:5}];
const insights={coverage:{sessions:100},tags:{items}};
let themeFigures='bars';renderWorkOutcomePair(insights);const bars=Boolean(nodes['w-module-outcomes']);
themeFigures='dots';renderWorkOutcomePair(insights);const card=nodes['w-module-outcomes'],html=card.innerHTML;
const unpriced={coverage:{sessions:100},tags:{items:items.map(item=>({...item,share:null}))}};
renderWorkOutcomePair(unpriced);const spendless=nodes['w-module-outcomes'].innerHTML;
renderWorkOutcomePair({coverage:{sessions:10},tags:{items}});const removed=!nodes['w-module-outcomes'];
process.stdout.write(JSON.stringify({bars,id:card.id,cls:card.className,label:card.label,html,spendless,removed}));
""")
        self.assertFalse(payload["bars"], "existing themes get no new markup")
        self.assertEqual((payload["id"], payload["cls"], payload["label"]),
                         ("w-module-outcomes", "card workOutcomePair", "w-outcomes-title"))
        html = payload["html"]
        self.assertEqual(html.count("<figure class=waffle>"), 2)
        self.assertIn("<h3>Sessions</h3>", html)
        self.assertIn("<h3>Spend</h3>", html)
        self.assertIn("Each dot is 1% of sessions.", html)
        self.assertIn("Each dot is 1% of spend.", html)
        self.assertIn('aria-label="Sessions by how sessions ended: One-shot 60.0%, Rescued after pushback 15.0%, Ended on pushback 5.0%, Not yet judged 20.0%"', html)
        self.assertIn("<tr data-kind=rescued><td><span class=workOutcomeName><i aria-hidden=true></i>Rescued after pushback</span></td><td>15%</td><td>20%</td></tr>", html)
        # Without a priced share the spend figure is left out and its column reads --, never 0%.
        self.assertEqual(payload["spendless"].count("<figure class=waffle>"), 1)
        self.assertIn("<td>20%</td><td>--</td>", payload["spendless"])
        self.assertTrue(payload["removed"])
        # Only fixed labels and counts: the card's whole visible text.
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()
        self.assertEqual(text, "How sessions ended 100 sessions in this period · one outcome per session Sessions "
                               "Each dot is 1% of sessions. Spend Each dot is 1% of spend. Outcome Sessions Spend "
                               "One-shot 60% 30% Rescued after pushback 15% 20% Ended on pushback 5% 10% Not yet judged 20% 40%")
        # The remainder draws hollow rings, never a filled outcome.
        self.assertIn('<path class=waffleRing style="color:var(--line2)"', html)
        self.assertIn("<tr data-kind=unjudged>", html)

    def test_unchanged_insights_skip_the_fifteen_second_repaint(self):
        payload = self.run_node("""
const calls=[];let WORK=null,themeEpoch=1;
['renderWorkStatus','renderWorkFilters','renderWorkBreakdown','renderWorkTagCards','renderWorkOutcomePair','renderWorkRhythm','renderWorkRework','renderWorkAgentTrend','renderWorkScorecard','renderWorkEffortModels','renderWorkFit','renderWorkMix','renderWorkOpportunities','renderWorkSizing','renderWorkEffort'].forEach(name=>{globalThis[name]=()=>calls.push(name);});
const $=()=>({viewBox:{baseVal:{width:560}}}),workTrendWidth=()=>560;  // trends already at their card's width
eval(page.match(/^let workRenderedKey='';$/m)[0].replace(/^let /,'var '));eval(extract('renderWork'));
const payload=(n,status='idle')=>({status:{state:status},settings:{areas:[{name:'Billing'}]},insights:{coverage:{sessions:n}}});
const modules=()=>calls.filter(name=>name==='renderWorkBreakdown').length,statuses=()=>calls.filter(name=>name==='renderWorkStatus').length;
renderWork(payload(5));renderWork(payload(5,'running'));const same=[modules(),statuses()];
themeEpoch++;renderWork(payload(5));const theme=modules();
renderWork(payload(6));const changed=modules();
process.stdout.write(JSON.stringify({same,theme,changed,work:WORK.insights.coverage.sessions}));
""")
        self.assertEqual(payload["same"], [1, 2], "the status card repaints every load; unchanged insights do not")
        self.assertEqual(payload["theme"], 2)
        self.assertEqual(payload["changed"], 3)
        self.assertEqual(payload["work"], 6)


    def test_a_trend_drawn_at_another_width_redraws_when_the_insights_are_unchanged(self):
        # A resize while Work is hidden leaves the trends at the old width; the next load must redraw them
        # at the card's width (labels would otherwise scale below 11px), and only them.
        payload = self.run_node("""
const calls=[];let WORK=null,themeEpoch=1;
['renderWorkStatus','renderWorkFilters','renderWorkBreakdown','renderWorkTagCards','renderWorkOutcomePair','renderWorkRhythm','renderWorkScorecard','renderWorkEffortModels','renderWorkFit','renderWorkMix','renderWorkOpportunities','renderWorkSizing','renderWorkEffort'].forEach(name=>{globalThis[name]=()=>calls.push(name);});
const svgs={'w-rework-trend':{width:0,parent:1100},'w-agent-trend':{width:0,parent:1100}};
const node=id=>({id,viewBox:{baseVal:{get width(){return svgs[id].width;}}},parentElement:{get clientWidth(){return svgs[id].parent;}}});
const $=id=>svgs[id]?node(id):null;
const draw=id=>{calls.push(id);svgs[id].width=workTrendWidth($(id));};
globalThis.renderWorkRework=()=>draw('w-rework-trend');globalThis.renderWorkAgentTrend=()=>draw('w-agent-trend');
eval(page.match(/^const workTrendWidth=.*$/m)[0].replace(/^const /,'var '));
eval(page.match(/^let workRenderedKey='';$/m)[0].replace(/^let /,'var '));eval(extract('renderWork'));
const payload={settings:{areas:[]},insights:{coverage:{sessions:5}}};
const trendDraws=()=>calls.filter(name=>name.endsWith('-trend')).length,modules=()=>calls.filter(name=>name==='renderWorkBreakdown').length;
renderWork(payload);const first=[trendDraws(),modules(),svgs['w-rework-trend'].width];
renderWork(payload);const same=[trendDraws(),modules()];
svgs['w-rework-trend'].parent=700;svgs['w-agent-trend'].parent=700;
renderWork(payload);const resized=[trendDraws(),modules(),svgs['w-rework-trend'].width,svgs['w-agent-trend'].width];
svgs['w-agent-trend'].parent=650;
renderWork(payload);const one=calls.slice(-2);
process.stdout.write(JSON.stringify({first,same,resized,one}));
""")
        self.assertEqual(payload["first"], [2, 1, 1100])
        self.assertEqual(payload["same"], [2, 1], "an unchanged width draws nothing")
        self.assertEqual(payload["resized"], [4, 1, 700, 700], "stale trends redraw; the other modules do not")
        self.assertEqual(payload["one"], ["renderWorkStatus", "w-agent-trend"], "only the stale trend redraws")

if __name__ == "__main__":
    unittest.main()
