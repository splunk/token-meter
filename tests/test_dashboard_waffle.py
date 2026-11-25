"""The shared waffle: a share-of-total dot grid that never draws a misleading grid for unavailable data."""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from tests.dashboard_page import DASHBOARD_PAGE

PAGE = Path(DASHBOARD_PAGE).read_text()

HARNESS = r"""
const fs=require('fs');
const page=fs.readFileSync(__PAGE__,'utf8');
eval(page.match(/^const esc=.*$/m)[0].replace(/^const /,'var '));
const start=page.indexOf('// waffle-pure-start'),end=page.indexOf('// waffle-pure-end');
if(start<0||end<0)throw Error('missing waffle-pure block');
eval(page.slice(start,end).replace(/^const /gm,'var '));
const paths=html=>[...html.matchAll(/<path class=(\w+) style="color:([^"]*)" d="([^"]*)"\/>/g)].map(m=>({kind:m[1],color:m[2],dots:(m[3].match(/M/g)||[]).length,first:m[3].slice(0,m[3].indexOf('a'))}));
const svgOpen=html=>html.slice(html.indexOf('<svg'),html.indexOf('>',html.indexOf('<svg'))+1);
const caption=html=>(html.match(/<figcaption class=waffleUnit>([^<]*)<\/figcaption>/)||[])[1]||null;
"""


@unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
class WaffleTest(unittest.TestCase):
    def run_js(self, body):
        script = HARNESS.replace("__PAGE__", json.dumps(str(DASHBOARD_PAGE))) + body
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_counts_use_largest_remainders_and_keep_every_real_share(self):
        out = self.run_js(r"""
console.log(JSON.stringify({
 exact:waffleCounts([62,38]),thirds:waffleCounts([1,1,1]),ties:waffleCounts([2,0,1],10),
 sliver:waffleCounts([99.6,0.4]),slivers:waffleCounts([997,1,1,1]),strings:waffleCounts(['3','1'],8),
 missing:waffleCounts([1,null,1]),invalid:[waffleCounts([5,-2]),waffleCounts([5,'x']),waffleCounts([5,'']),waffleCounts([5,undefined])],
 zero:waffleCounts([0,0]),empty:waffleCounts([]),bad:waffleCounts(null),
 sums:Array.from({length:200},(_,i)=>{const values=Array.from({length:1+i%7},(_,k)=>((i*37+k*11)%23)/(k+1));
  const cells=50+i%60,counts=waffleCounts(values,cells);
  if(!counts)return values.every(v=>!v);
  return counts.reduce((a,b)=>a+b,0)===cells&&values.every((v,k)=>!(v>0)||counts[k]>=1);}).every(Boolean),
}));
""")
        self.assertEqual(out["exact"], [62, 38])
        self.assertEqual(out["thirds"], [34, 33, 33])
        self.assertEqual(out["ties"], [7, 0, 3])
        # Every part above 0 keeps one dot, taken from the largest part.
        self.assertEqual(out["sliver"], [99, 1])
        self.assertEqual(out["slivers"], [97, 1, 1, 1])
        self.assertEqual(out["strings"], [6, 2])
        # A missing part means no waffle: the other shares would be overstated.
        self.assertIsNone(out["missing"])
        self.assertEqual(out["invalid"], [None, None, None, None])
        for key in ("zero", "empty", "bad"):
            with self.subTest(case=key):
                self.assertIsNone(out[key])
        self.assertTrue(out["sums"])

    def test_waffle_draws_parts_row_by_row_with_a_unit_caption(self):
        out = self.run_js(r"""
const html=waffleHtml([{value:62,color:'var(--c-output)',label:'Output'},{value:38,color:'var(--provider-claude)',label:'Cost'},
 {value:0,color:'var(--faint)',label:'Idle'}],{unit:'1% of spend',label:'Spend by kind'});
console.log(JSON.stringify({open:html.slice(0,html.indexOf('<svg')),svg:svgOpen(html),paths:paths(html),caption:caption(html),close:html.endsWith('</figure>')}));
""")
        self.assertEqual(out["open"], "<figure class=waffle>")
        self.assertEqual(out["svg"], '<svg class=waffleGrid role=img aria-label="Spend by kind: Output 62.0%, Cost 38.0%, Idle 0%" viewBox="0 0 80 80" width=80 height=80>')
        # Dots fill row by row in 8px cells (radius 3); a zero part draws nothing.
        self.assertEqual(out["paths"], [
            {"kind": "waffleDot", "color": "var(--c-output)", "dots": 62, "first": "M1 4"},
            {"kind": "waffleDot", "color": "var(--provider-claude)", "dots": 38, "first": "M17 52"},
        ])
        self.assertEqual(out["caption"], "Each dot is 1% of spend.")
        self.assertTrue(out["close"])

    def test_unknown_and_estimated_parts_are_rings_that_count(self):
        out = self.run_js(r"""
const html=waffleHtml([{value:70,color:'var(--c-output)',label:'Known'},{value:20,unknown:true},{value:10,color:'var(--c-cost)',label:'Cache',estimate:true}],{unit:'1% of cost'});
const tiny=waffleHtml([{value:99.6,color:'var(--c-output)',label:'Big'},{value:.4,color:'var(--c-cost)',label:'Sliver'}],{unit:'1% of cost'});
console.log(JSON.stringify({svg:svgOpen(html),paths:paths(html),tiny:{svg:svgOpen(tiny),paths:paths(tiny)}}));
""")
        self.assertEqual(out["svg"], '<svg class=waffleGrid role=img aria-label="Known 70.0%, Unknown 20.0%, Cache 10.0% estimated" viewBox="0 0 80 80" width=80 height=80>')
        self.assertEqual(out["paths"], [
            {"kind": "waffleDot", "color": "var(--c-output)", "dots": 70, "first": "M1 4"},
            {"kind": "waffleRing", "color": "var(--line2)", "dots": 20, "first": "M1.5 60"},
            {"kind": "waffleRing", "color": "var(--c-cost)", "dots": 10, "first": "M1.5 76"},
        ])
        # The attribute escapes "<"; a screen reader hears "Sliver <1%".
        self.assertIn('aria-label="Big 99.6%, Sliver &lt;1%"', out["tiny"]["svg"])
        self.assertEqual([path["dots"] for path in out["tiny"]["paths"]], [99, 1])

    def test_no_waffle_without_a_unit_or_with_a_missing_part(self):
        out = self.run_js(r"""
console.log(JSON.stringify([
 waffleHtml([{value:1,color:'var(--c-output)'},{value:null,color:'var(--c-cost)'}],{unit:'1%'}),
 waffleHtml([{value:1,color:'var(--c-output)'}]),
 waffleHtml([{value:1,color:'var(--c-output)'}],{unit:'  '}),
 waffleHtml([{value:0,color:'var(--c-output)'}],{unit:'1%'}),
 waffleHtml([],{unit:'1%'}),waffleHtml(),
]));
""")
        self.assertEqual(out, ["", "", "", "", "", ""])

    def test_grid_geometry_and_escaping(self):
        out = self.run_js(r"""
const small=waffleHtml([{value:1,color:'var(--c-output)'},{value:3,color:'var(--c-cost)'}],{cells:20,cols:5,unit:'5 "units" <x>',label:'A "b"'});
const two=waffleHtml([{value:1,color:'var(--c-output)'}],{cells:2,cols:2,unit:'1 call'});
console.log(JSON.stringify({svg:svgOpen(small),caption:caption(small),d:two.match(/ d="([^"]*)"/)[1]}));
""")
        self.assertEqual(out["svg"], '<svg class=waffleGrid role=img aria-label="A &quot;b&quot;: Part 25.0%, Part 75.0%" viewBox="0 0 40 32" width=40 height=32>')
        self.assertEqual(out["caption"], "Each dot is 5 &quot;units&quot; &lt;x&gt;.")
        self.assertEqual(out["d"], "M1 4a3 3 0 1 0 6 0a3 3 0 1 0 -6 0M9 4a3 3 0 1 0 6 0a3 3 0 1 0 -6 0")

    def test_waffle_is_a_registered_zero_specificity_component(self):
        for rule in (":where(.waffle){display:grid;justify-items:start;gap:4px;margin:0}", ":where(.waffle)[hidden]{display:none}",
                     ":where(.waffle .waffleDot){fill:currentColor}", ":where(.waffle .waffleRing){fill:none;stroke:currentColor;stroke-width:1}",
                     ":where(.waffle>.waffleUnit){color:var(--faint);font-size:var(--fs-12)}"):
            with self.subTest(rule=rule):
                self.assertIn(rule, PAGE)
        self.assertNotRegex(PAGE, r"(?:^|\n|,)\.waffle\b")


if __name__ == "__main__":
    unittest.main()
