"""Shared setup and helpers for the DashboardLayoutTests split."""

import unittest
import json
import subprocess
from pathlib import Path
import meter
from tests.dashboard_page import DASHBOARD_PAGE


class DashboardLayoutCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = Path(DASHBOARD_PAGE).read_text()
        cls.session_effects = (
            Path(meter.__file__).with_name("assets") / "session-effects.js"
        ).read_text()

    CHART_GAP_HARNESS = r"""
const fs=require('fs');
const page=fs.readFileSync(__PAGE__,'utf8');
function extract(name){let start=page.indexOf(`function ${name}(`);if(start<0)throw Error(`missing ${name}`);let i=page.indexOf('{',start),depth=0;for(;i<page.length;i++){if(page[i]==='{')depth++;else if(page[i]==='}'&&--depth===0)return page.slice(start,i+1);}throw Error(`unclosed ${name}`);}
function load(names){names.forEach(name=>eval(`globalThis.${name}=${extract(name)}`));}
function attrs(html,tag,cls){return [...html.matchAll(new RegExp(`<${tag} class=${cls} ([^>]*)/?>`,'g'))].map(match=>Object.fromEntries([...match[1].matchAll(/([\w-]+)="([^"]*)"/g)].map(pair=>[pair[1],pair[2]])));}
"""

    def run_chart_gap_harness(self, body):
        script = self.CHART_GAP_HARNESS.replace(
            "__PAGE__", json.dumps(str(Path(DASHBOARD_PAGE))),
        ) + body
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def _tour_script(self, body):
        """Runs the tour's DOM-free block (storage, slide copy, frame markup) in Node with the page's own formatters."""
        page_path = json.dumps(str(Path(DASHBOARD_PAGE)))
        return f"""
const fs=require('fs');
const page=fs.readFileSync({page_path},'utf8');
const helpers=page.match(/^const (f|compactNumber|money|wholeMoney|pct|countWord|esc)=.*$/gm).join('\\n').replace(/^const /gm,'var ');
eval(helpers);
var LATEST=null;Object.defineProperty(globalThis,'navigator',{{value:{{platform:'',userAgent:''}},configurable:true,writable:true}});
var providerColor=id=>`var(--provider-${{id}})`,runtimeMarkSvg=()=>'<svg class=runtimeMark></svg>',compareColor=i=>['var(--c-a)','var(--c-b)'][i%2];
var modelRankedColors=groups=>new Map(groups.map((group,index)=>[group.key,`shade-${{index}}`]));
var stored=new Map(),blocked={{read:false,write:false}};
var localStorage={{getItem:key=>{{if(blocked.read)throw Error('blocked');return stored.get(key)??null;}},setItem:(key,value)=>{{if(blocked.write)throw Error('quota');stored.set(key,String(value));}}}};
const start=page.indexOf('// deck-pure-start'),end=page.indexOf('// deck-pure-end');
if(start<0||end<0)throw Error('missing deck-pure block');
eval(page.slice(start,end).replace(/^const /gm,'var '));
const richState={{
 total_tokens:82702828,total_cost:38.59,turns:703,provider:'claude',primary_model:'claude-opus-5-5',
 context:{{latest:112214,peak:325998,window:200000,latest_pct:.56}},
 xsession:{{
  total_sessions:2179,model_mix:[{{model:'gpt-5.6-sol',runtime:'Codex',providers:['codex'],cost:4278}}],
  capabilities:{{summary:{{definitions:{{definition_tokens:120000,unused_eager_definition_tokens:83907}}}}}},
  agent_usage:{{totals:{{agents:646,cost_covered_agents:640,known_cost:1001.45}}}},
  budget:{{month:'2026-10',configured:true,budget:8940,spend:1792.7,percent:.2,projection_ready:true,projected_spend:3704.9,state:'on_track'}},
 }},
}};
const frameSlides=DECK_SLIDES.map((slide,index)=>({{slide,index}})).filter(row=>row.slide.frame);
{body}
"""

    def _run_tour(self, body):
        completed = subprocess.run(
            ["node", "-e", self._tour_script(body)], capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    @staticmethod
    def page_function(page, name):
        start = page.index("function {}(".format(name))
        depth = 0
        for index in range(start, len(page)):
            if page[index] == "{":
                depth += 1
            elif page[index] == "}":
                depth -= 1
                if depth == 0:
                    return page[start:index + 1]
        raise AssertionError(name)
