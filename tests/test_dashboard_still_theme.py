"""Still themes (`--motion:still`, Frontier): no ambient motion, no header shaders, one still header field."""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from tests.dashboard_page import DASHBOARD_PAGE

PAGE = Path(DASHBOARD_PAGE).read_text()
THEME_BLOCK = re.compile(r"\n(?::root,)?\[data-theme=(\w+)\]\{([^{}]*)\n\}")
THEMES = {m.group(1): dict(re.findall(r"--([\w-]+):([^;]+);", m.group(2))) for m in THEME_BLOCK.finditer(PAGE)}
# The values every theme other than a still one declares; each reproduces the look the theme had before.
NEUTRAL = {
    "motion": "ambient", "atmos": "1", "edge-rgb": "var(--signal-rgb)", "edge-hi-rgb": "var(--signal-hi-rgb)",
    "kicker": "var(--signal-hi)", "title-fill": "var(--title-shimmer)",
    "font-readout": "var(--font-mono)", "font-label": "var(--font-sans)",
    "data-mask": "none", "scale-mask": "var(--dither-50) 0 0/4px 4px", "track-texture": "none",
    "callout": "var(--spectrum-active)", "callout-glow": "var(--spectrum-glow)",
    "head-surface": "var(--spectrum-head)", "head-border": "var(--spectrum-border-strong)",
    "head-shadow": "var(--spectrum-depth-focus)", "foil": "var(--spectrum-foil)",
    "rail-surface": "var(--spectrum-rail)", "rail-surface-narrow": "var(--spectrum-rail-narrow)",
    "rail-border": "var(--spectrum-border)", "glass": "blur(22px) saturate(1.25)",
    "glass-palette": "blur(24px) saturate(1.3)", "glass-tray": "blur(14px)",
    "nav-on": "var(--spectrum-active)", "nav-on-fg": "var(--on-accent)", "nav-on-shadow": "var(--spectrum-glow)",
    "nav-on-hover": "var(--spectrum-active-hover)", "nav-mark": "transparent",
    "seg-on": "var(--spectrum-active)", "seg-on-fg": "var(--on-accent)",
    "button-surface": "var(--spectrum-button)", "button-surface-hover": "var(--spectrum-button-hover)",
    "figures": "bars", "fill-min": "2px",
    "font-title": "var(--font-display)", "fw-title": "var(--fw-semibold)", "title-condense": "1", "title-tighten": "1",
    "fw-label": "var(--fw-heavy)", "fw-readout": "var(--fw-heavy)",
}

FIELD_HARNESS = r"""
const fs=require('fs');
const page=fs.readFileSync(__PAGE__,'utf8');
function extract(name){
 let start=page.indexOf(`function ${name}(`);
 if(start<0)throw new Error(name);
 if(page.slice(start-6,start)==='async ')start-=6;
 let i=page.indexOf('{',start),depth=0;
 for(;i<page.length;i++){if(page[i]==='{')depth++;else if(page[i]==='}'&&--depth===0)return page.slice(start,i+1);}
}
// A minimal DOM: a header's fallback host takes the header's size; canvases record what they draw.
let fills=[],paints=0;
class El{
 constructor(className=''){this.className=className;this.children=[];this.parent=null;this.dataset={};this.isConnected=true;this.w=0;this.h=0;}
 get clientWidth(){return this.className==='pageSignalFallback'?this.parent?.parent?.w||0:this.w;}
 get clientHeight(){return this.className==='pageSignalFallback'?this.parent?.parent?.h||0:this.h;}
 appendChild(child){child.parent=this;this.children.push(child);return child;}
 append(...children){children.forEach(child=>this.appendChild(child));}
 remove(){if(this.parent){this.parent.children=this.parent.children.filter(child=>child!==this);this.parent=null;}}
 setAttribute(){}
 replaceChildren(){this.children=[];}
 closest(){return null;}
 contains(node){for(let n=node;n;n=n.parent)if(n===this)return true;return false;}
 *walk(){for(const child of this.children){yield child;yield* child.walk();}}
 querySelector(selector){const wanted=selector.replace(/^canvas/,'').slice(1);for(const node of this.walk())if(node.className===wanted)return node;return null;}
}
class Canvas extends El{
 constructor(){super();this.width=300;this.height=150;this.transform=null;}
 getContext(){const canvas=this;return {setTransform(...args){canvas.transform=args;},set fillStyle(value){this.style=value;},get fillStyle(){return this.style;},fill(path){fills.push({style:this.style,dots:path.arcs});}};}
}
class Path2D{constructor(){this.arcs=[];this.moves=[];} moveTo(x,y){this.moves.push([x,y]);} arc(x,y,r,a0,a1){this.arcs.push([x,y,r,a0,+a1.toFixed(4)]);}}
let timers=[],now=0,nextTimer=1;
function setTimeout(fn,ms){const id=nextTimer++;timers.push({id,fn,at:now+ms});return id;}
function clearTimeout(id){timers=timers.filter(timer=>timer.id!==id);}
function advance(ms){const end=now+ms;for(;;){timers.sort((a,b)=>a.at-b.at);const timer=timers[0];if(!timer||timer.at>end)break;timers.shift();now=timer.at;timer.fn();}now=end;}
const observers=[];
class ResizeObserver{constructor(callback){this.callback=callback;this.targets=new Set();observers.push(this);} observe(target){this.targets.add(target);} disconnect(){this.targets.clear();} fire(){if(this.targets.size)this.callback([...this.targets].map(target=>({target})));}}
const window={devicePixelRatio:3,ResizeObserver};
const heads=[];let currentHead=null;
const document={
 documentElement:{dataset:{theme:'frontier'}},
 querySelector(){return currentHead;},
 querySelectorAll(selector){return heads.flatMap(head=>[...head.walk()]).filter(node=>selector==='canvas.stillField'&&node.className==='stillField');},
 createElement(tag){return tag==='canvas'?new Canvas():new El();},
};
const tint=(name,alpha)=>`tint(${name},${alpha})`;
let loads=0;
function loadPageEffectsModule(){loads++;return Promise.resolve({mountDitherField:()=>Promise.resolve({update(){},dispose(){}}),mountPulsingBorder:()=>Promise.resolve({update(){},dispose(){}})});}
function pageSignalPresentation(){return {};}
function scheduleAmbientClock(){}
let activePageSignalHead=null,activePageHeroHandle=null,activePageBorderHandle=null;
let activePageSignalMounting=false,activePageSignalGeneration=0,pageEffectsDisabled=false;
let themeMotionStill=true,stillSignalHead=null,stillSignalObserver=null,stillSignalTimer=0;
eval(page.slice(page.indexOf('// dither-pure-start'),page.indexOf('// dither-pure-end')).replace(/^const /gm,'var '));
['ensurePageSignalHosts','disposeActivePageSignalEffects','pageSignalHandle','paintStillSignalField',
 'repaintStillSignalField','syncStillSignalField','syncActivePageSignalEffects'].forEach(name=>eval(`globalThis.${name}=${extract(name)}`));
const canvases=()=>document.querySelectorAll('canvas.stillField');
"""


BAYER = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]


def expected_dots(width, height, cell, density):
    """The talk deck's field(), computed independently: cells from the top-left, Bayer 4x4 thresholds."""
    dots = []
    j = 0
    while j * cell < height:
        i = 0
        while i * cell < width:
            x, y = i * cell + cell / 2, j * cell + cell / 2
            if density(x / width, y / height) > (BAYER[j & 3][i & 3] + .5) / 16:
                dots.append([x, y])
            i += 1
        j += 1
    return dots


def still_grain(u, v):
    return .95 * min(1, max(0, (u - .3) * 1.6)) * (1 - .5 * v)


class StillThemeTokenTest(unittest.TestCase):
    def test_existing_themes_carry_the_neutral_values(self):
        self.assertIn("frontier", THEMES)
        for theme, tokens in THEMES.items():
            if theme == "frontier":
                continue
            with self.subTest(theme=theme):
                self.assertEqual({name: tokens[name].strip() for name in NEUTRAL}, NEUTRAL)

    def test_frontier_is_still_flat_and_dot_filled(self):
        frontier = {name: value.strip() for name, value in THEMES["frontier"].items()}
        self.assertEqual(frontier["motion"], "still")
        self.assertEqual(frontier["figures"], "dots")
        self.assertEqual(frontier["atmos"], "0")
        self.assertEqual(frontier["glow"], "0")
        # Frameless header, flat rail and selected states, no glass, no display face.
        self.assertEqual({name: frontier[name] for name in ("head-surface", "head-border", "head-shadow", "glass",
                                                             "glass-palette", "glass-tray", "nav-on-shadow")},
                         {"head-surface": "none", "head-border": "transparent", "head-shadow": "none", "glass": "none",
                          "glass-palette": "none", "glass-tray": "none", "nav-on-shadow": "none"})
        self.assertEqual((frontier["rail-surface"], frontier["nav-on"], frontier["seg-on"]), ("var(--bg)", "var(--panel3)", "var(--panel3)"))
        self.assertEqual((frontier["font-title"], frontier["title-condense"]), ("var(--font-sans)", "0"))
        # The header grain is the only page texture.
        self.assertEqual(frontier["page-texture"], "none")
        self.assertEqual(frontier["data-mask"], "var(--dot-mask) 0 0/4px 4px")
        self.assertEqual(frontier["scale-mask"], "var(--dot-mask) 0 0/4px 4px")
        self.assertEqual(frontier["track-texture"], "var(--dot-track)")
        self.assertEqual((frontier["font-readout"], frontier["font-label"]), ("var(--font-sans)", "var(--font-mono)"))
        self.assertEqual(frontier["kicker"], "var(--c-cost)")
        # Value blue (readable as text: 4.62:1 on panel3), cost orange.
        self.assertEqual((frontier["signal"], frontier["c-output"], frontier["c-cost"]), ("#438ce9", "#438ce9", "#e8622c"))

    def test_startup_and_theme_changes_read_the_theme_motion(self):
        live = PAGE[PAGE.index("window.addEventListener('blur',pauseAmbientMotionSoon);"):]
        self.assertIn("readThemeMotion();\nsyncAmbientMotion();", live)
        self.assertIn("themeMotionStill=cssVar('--motion')==='still';", PAGE)
        self.assertIn("themeFigures=cssVar('--figures')==='dots'?'dots':'bars';", PAGE)


@unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
class StillHeaderFieldTest(unittest.TestCase):
    def run_js(self, body):
        script = FIELD_HARNESS.replace("__PAGE__", json.dumps(str(DASHBOARD_PAGE))) + body
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_dither_dots_follow_the_deck_field(self):
        out = self.run_js(r"""
console.log(JSON.stringify({
 ramp:ditherDots({w:96,h:40,cell:8,r:2,dens:(u,v)=>u*(1-v)}),
 full:ditherDots({w:24,h:16,cell:8,dens:()=>1}).length,
 none:ditherDots({w:24,h:16,cell:8,dens:()=>0}).length,
 radius:ditherDots({w:10,h:10,cell:10,dens:()=>1})[0].r,
 empty:[ditherDots({w:0,h:10,dens:()=>1}).length,ditherDots().length],
 args:(()=>{const seen=[];ditherDots({w:20,h:10,cell:10,dens:(...a)=>{seen.push(a);return 0;}});return seen;})(),
}));
""")
        self.assertEqual([[dot["x"], dot["y"]] for dot in out["ramp"]], expected_dots(96, 40, 8, lambda u, v: u * (1 - v)))
        self.assertEqual({dot["r"] for dot in out["ramp"]}, {2})
        # Full density fills every cell, zero density none; the radius defaults to the deck's cell*.34.
        self.assertEqual((out["full"], out["none"]), (6, 0))
        self.assertAlmostEqual(out["radius"], 3.4)
        self.assertEqual(out["empty"], [0, 0])
        # dens gets u, v (0..1 at cell centers) and the center itself.
        self.assertEqual(out["args"], [[.25, .5, 5, 5], [.75, .5, 15, 5]])

    def test_still_field_is_one_neutral_ordered_dither_grain(self):
        out = self.run_js(r"""
const head=new El('spectrumPageHead');head.w=612;head.h=138;heads.push(head);
ensurePageSignalHosts(head);
const host=head.querySelector('.pageSignalFallback');
paintStillSignalField(host);
const canvas=host.querySelector('canvas.stillField');
const drawn={size:[canvas.width,canvas.height],transform:canvas.transform,fills,theme:canvas.dataset.theme};
fills=[];head.w=0;paintStillSignalField(host);
console.log(JSON.stringify({drawn,emptyFills:fills.length,emptyRemoved:!host.querySelector('canvas.stillField')}));
""")
        drawn = out["drawn"]
        # Device pixel ratio 3 is capped at 2.
        self.assertEqual(drawn["size"], [1224, 276])
        self.assertEqual(drawn["transform"], [2, 0, 0, 2, 0, 0])
        # One neutral fill: decorative dots never spend the value blue or the cost orange.
        self.assertEqual([fill["style"] for fill in drawn["fills"]], ["tint(line,0.16)"])
        expected = expected_dots(612, 138, 10, still_grain)
        self.assertTrue(expected)
        self.assertEqual([dot[:2] for dot in drawn["fills"][0]["dots"]], expected)
        self.assertEqual({(dot[2], dot[3], dot[4]) for dot in drawn["fills"][0]["dots"]}, {(1.3, 0, 6.2832)})
        self.assertGreater(min(x / 612 for x, _ in expected), .3)
        self.assertEqual(drawn["theme"], "frontier")
        # A header with no size draws nothing and leaves no canvas behind.
        self.assertEqual(out["emptyFills"], 0)
        self.assertTrue(out["emptyRemoved"])

    def test_still_theme_never_loads_shaders_and_keeps_one_field(self):
        out = self.run_js(r"""
(async()=>{
 const a=new El('spectrumPageHead'),b=new El('spectrumPageHead');
 a.w=600;a.h=120;b.w=500;b.h=100;heads.push(a,b);
 // Shaders mounted under an ambient theme are still live when the theme turns still.
 let disposed=0;
 activePageHeroHandle={dispose(){disposed++;}};activePageBorderHandle={dispose(){disposed++;}};activePageSignalHead=a;
 currentHead=a;await syncActivePageSignalEffects();
 const first={disposed,loads,canvases:canvases().length,inA:!!a.querySelector('canvas.stillField'),fills:fills.length,head:activePageSignalHead};
 await syncActivePageSignalEffects();
 const same={fills:fills.length};
 currentHead=b;await syncActivePageSignalEffects();
 const routed={canvases:canvases().length,inA:!!a.querySelector('canvas.stillField'),inB:!!b.querySelector('canvas.stillField'),fills:fills.length,
  observing:[...stillSignalObserver.targets].map(head=>head===b)};
 // The first observation reports the size it was drawn at: no repaint. A real resize repaints once, debounced.
 observers[0].fire();advance(200);
 const unchanged={fills:fills.length};
 b.w=520;observers[0].fire();advance(100);observers[0].fire();advance(149);
 const debouncing={fills:fills.length};
 advance(1);
 const resized={fills:fills.length,size:b.querySelector('canvas.stillField').dataset.size};
 document.documentElement.dataset.theme='frontier-night';await syncActivePageSignalEffects();
 const retheme={fills:fills.length,canvases:canvases().length};
 currentHead=null;await syncActivePageSignalEffects();
 const noHead={canvases:canvases().length,fills:fills.length};
 currentHead=b;await syncActivePageSignalEffects();
 // Back to an ambient theme: the field goes and the shaders mount again.
 themeMotionStill=false;await syncActivePageSignalEffects();
 const ambient={canvases:canvases().length,loads,observing:stillSignalObserver.targets.size,stillHead:stillSignalHead,hero:!!activePageHeroHandle,border:!!activePageBorderHandle};
 console.log(JSON.stringify({first,same,routed,unchanged,debouncing,resized,retheme,noHead,ambient}));
})().catch(error=>{console.error(error);process.exitCode=1;});
""")
        self.assertEqual(out["first"], {"disposed": 2, "loads": 0, "canvases": 1, "inA": True, "fills": 1, "head": None})
        # Repeated syncs (route polls, reduced-motion changes) do not redraw.
        self.assertEqual(out["same"], {"fills": 1})
        self.assertEqual(out["routed"], {"canvases": 1, "inA": False, "inB": True, "fills": 2, "observing": [True]})
        self.assertEqual(out["unchanged"], {"fills": 2})
        self.assertEqual(out["debouncing"], {"fills": 2})
        self.assertEqual(out["resized"], {"fills": 3, "size": "520x100"})
        self.assertEqual(out["retheme"], {"fills": 4, "canvases": 1})
        self.assertEqual(out["noHead"], {"canvases": 0, "fills": 4})
        self.assertEqual(out["ambient"], {"canvases": 0, "loads": 1, "observing": 0, "stillHead": None,
                                          "hero": True, "border": True})

    def test_theme_epoch_counts_every_token_read(self):
        charts = (Path(__file__).resolve().parents[1] / "dashboard/js/charts.js").read_text()
        block = charts[charts.index("const CHART={};"):charts.index("readChartTokens();\n") + len("readChartTokens();\n")]
        script = ("const cssVar=()=>'',tint=()=>'';" + block.replace("const CHART", "var CHART").replace("let themeEpoch", "var themeEpoch")
                  + "const atLoad=themeEpoch;readChartTokens();console.log(JSON.stringify({atLoad,afterCommit:themeEpoch}));")
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"atLoad": 1, "afterCommit": 2})
        repaint = PAGE[PAGE.index("function repaintThemedSurfaces(){"):]
        self.assertIn("readChartTokens();", repaint[:repaint.index("\n}")])

    def test_titles_and_accessibility_rules_follow_the_theme_tokens(self):
        import re
        css = PAGE[PAGE.index("<style>"):PAGE.index("</style>")]
        # Every rule that names the display face is followed by one that names the title voice.
        display = re.findall(r"\n([^{}\n]+)\{[^}]*font-family:var\(--font-display\)", css)
        title = re.findall(r"\n([^{}\n]+)\{[^}]*font-family:var\(--font-title\)", css)
        self.assertEqual(len(display), 8)
        self.assertEqual(sorted(display), sorted(title))
        self.assertIn("@media(prefers-contrast:more){body{--data-mask:none;--track-texture:none}}", css)
        self.assertIn("body.spectrumApp .top .tab.on:before{content:\"\";position:absolute;", css)
        self.assertIn("background:var(--nav-mark);pointer-events:none}", css)
        self.assertIn(":where(.metric)>:is(.v,strong,b).mono{font-family:var(--font-readout)}", css)

    def test_still_field_rules_replace_the_css_dots(self):
        self.assertIn(".pageSignalFallback:has(.stillField){background:none}", PAGE)
        self.assertIn(".stillField{position:absolute;inset:0;display:block;width:100%;height:100%}", PAGE)


if __name__ == "__main__":
    unittest.main()
