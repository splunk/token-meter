import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Fake timers, display frames (60 Hz, timestamps trailing real time by one frame), animations,
# and shader handles; the clock source runs unmodified.
CLOCK_HARNESS = r"""
const fs=require('fs'),root=process.argv[1];
const src=fs.readFileSync(root+'/dashboard/js/ambient-clock.js','utf8');
const clock=src.slice(0,src.indexOf('// end-ambient-clock'));
let now=0,timers=[],frames=[],nextId=1,listeners={};
const performance={now:()=>now};
function setTimeout(fn,ms){const id=nextId++;timers.push({id,fn,at:now+Math.max(0,ms)});return id;}
function clearTimeout(id){timers=timers.filter(t=>t.id!==id);}
function requestAnimationFrame(fn){const id=nextId++;frames.push({id,fn});return id;}
function cancelAnimationFrame(id){frames=frames.filter(f=>f.id!==id);}
let vsyncs=0;
function advance(ms){
 const end=now+ms;
 while(now<end){
  let nextVsync=(Math.floor(now/16.667)+1)*16.667;
  if(nextVsync<=now)nextVsync+=16.667;
  timers.sort((a,b)=>a.at-b.at);
  const t=timers[0];
  if(t&&t.at<=nextVsync&&t.at<=end){timers.shift();now=Math.max(now,t.at);t.fn();continue;}
  if(nextVsync>end){now=end;break;}
  now=nextVsync;vsyncs++;
  const due=frames;frames=[];due.forEach(f=>f.fn(now-16.667));
 }
}
function makeAnimation(name,currentTime){
 return {animationName:name,currentTime,playState:'running',pause(){this.playState='paused';}};
}
let animations=[];
const document={hidden:false,getAnimations:()=>animations,addEventListener:(type,fn)=>{(listeners[type]=listeners[type]||[]).push(fn);}};
const pageSignalReducedMotionQuery={matches:false,addEventListener:(type,fn)=>{(listeners['motion-'+type]=listeners['motion-'+type]||[]).push(fn);}};
let lowPowerActive=false,ambientMotionIdle=false;
const ambientMotionStopped=()=>lowPowerActive||ambientMotionIdle;
const ticks={hero:[],border:[]};
let activePageHeroHandle={tick:dt=>ticks.hero.push(dt)},activePageBorderHandle={tick:dt=>ticks.border.push(dt)};
eval(clock.replace(/^const /gm,'var ').replace(/^let /gm,'var '));
const sum=a=>a.reduce((x,y)=>x+y,0);
"""


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class AmbientClockTests(unittest.TestCase):
    def run_js(self, body):
        result = subprocess.run(["node", "-e", CLOCK_HARNESS + body, str(ROOT)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_steps_every_ambient_motion_together_at_the_ambient_rate(self):
        out = self.run_js(r"""
const ring=makeAnimation('ringSpin',1200),foil=makeAnimation('foilDrift',300),enter=makeAnimation('viewEnter',10);
animations=[ring,foil,enter];
syncAmbientClock();advance(2000);
console.log(JSON.stringify({fps:AMBIENT_FPS,heroTicks:ticks.hero.length,borderTicks:ticks.border.length,
 advanced:Math.round(sum(ticks.hero)),ring:Math.round(ring.currentTime),foil:Math.round(foil.currentTime),
 ringState:ring.playState,enter:[enter.playState,enter.currentTime],frames:vsyncs}));
""")
        self.assertEqual(out["fps"], 20)
        # 2 s at 20 fps: one shared step per frame for shaders and CSS alike, never 60 a second.
        self.assertGreaterEqual(out["heroTicks"], 36)
        self.assertLessEqual(out["heroTicks"], 41)
        self.assertEqual(out["heroTicks"], out["borderTicks"])
        # Each animation keeps its own phase and advances by the same clock time the shaders saw.
        self.assertAlmostEqual(out["ring"] - 1200, out["advanced"], delta=1)
        self.assertAlmostEqual(out["foil"] - 300, out["advanced"], delta=1)
        self.assertEqual(out["ringState"], "paused")
        # Non-ambient motion (view entrances, toasts) is left to the browser.
        self.assertEqual(out["enter"], ["running", 10])

    def test_stopping_holds_every_frame_and_resuming_does_not_jump(self):
        out = self.run_js(r"""
const ring=makeAnimation('ringSpin',0);animations=[ring];
syncAmbientClock();advance(1000);
const before=ring.currentTime,heroBefore=ticks.hero.length;
const held={};
for(const [name,stop,start] of [
 ['lowPower',()=>{lowPowerActive=true;},()=>{lowPowerActive=false;}],
 ['idle',()=>{ambientMotionIdle=true;},()=>{ambientMotionIdle=false;}],
 ['hidden',()=>{document.hidden=true;},()=>{document.hidden=false;}],
 ['reducedMotion',()=>{pageSignalReducedMotionQuery.matches=true;},()=>{pageSignalReducedMotionQuery.matches=false;}],
]){
 const at=ring.currentTime,count=ticks.hero.length;
 stop();syncAmbientClock();advance(5000);
 held[name]={moved:ring.currentTime-at,ticks:ticks.hero.length-count,timers:timers.length,frames:frames.length};
 start();syncAmbientClock();advance(60);
 held[name].firstStep=ticks.hero[count]??null;
}
console.log(JSON.stringify({held,advancedBefore:before,ticksBefore:heroBefore}));
""")
        for name, held in out["held"].items():
            with self.subTest(stop=name):
                self.assertEqual(held["moved"], 0)
                self.assertEqual(held["ticks"], 0)
                self.assertEqual(held["timers"], 0)
                self.assertEqual(held["frames"], 0)
                # The first step after a pause is zero: motion resumes where it held, with no jump.
                self.assertEqual(held["firstStep"], 0)

    def test_adopts_new_ambient_animations_and_drops_finished_ones(self):
        out = self.run_js(r"""
syncAmbientClock();advance(500);
const pulse=makeAnimation('sessionFlowStatusPulse',40),sheen=makeAnimation('ionSheen',0);
const target={getAnimations:opts=>opts&&opts.subtree?[pulse,sheen]:[]};
listeners.animationstart.forEach(fn=>fn({animationName:'ionSheen',target}));
const sheenAdopted=ambientClockAnimations.has(sheen);
listeners.animationstart.forEach(fn=>fn({animationName:'sessionFlowStatusPulse',target}));
advance(500);
const pulseMoved=pulse.currentTime-40,adopted=ambientClockAnimations.size;
pulse.playState='idle';advance(100);
console.log(JSON.stringify({sheenAdopted,sheenState:sheen.playState,pulseMoved:Math.round(pulseMoved),adopted,after:ambientClockAnimations.size,
 listening:['animationstart','visibilitychange','motion-change'].map(k=>(listeners[k]||[]).length)}));
""")
        self.assertFalse(out["sheenAdopted"])
        self.assertEqual(out["sheenState"], "running")
        self.assertGreater(out["pulseMoved"], 400)
        self.assertEqual(out["adopted"], 1)
        self.assertEqual(out["after"], 0)
        self.assertEqual(out["listening"], [1, 1, 1])

    def test_stopped_clock_does_not_hold_animations_from_live_re_renders(self):
        out = self.run_js(r"""
syncAmbientClock();advance(200);
lowPowerActive=true;syncAmbientClock();
const created=[];
for(let poll=0;poll<50;poll++){
 created.forEach(a=>{a.playState='idle';});
 const ring=makeAnimation('cacheRingPulse',0);created.push(ring);
 listeners.animationstart.forEach(fn=>fn({animationName:'cacheRingPulse',target:{getAnimations:()=>[ring]}}));
 advance(1000);
}
const whileStopped=ambientClockAnimations.size,last=created[created.length-1];
animations=[last];lowPowerActive=false;syncAmbientClock();advance(200);
console.log(JSON.stringify({whileStopped,afterResume:ambientClockAnimations.size,lastAdopted:ambientClockAnimations.has(last),lastState:last.playState}));
""")
        # Nothing is adopted while stopped; the resume sweep adopts only the live ring.
        self.assertEqual(out["whileStopped"], 0)
        self.assertEqual(out["afterResume"], 1)
        self.assertTrue(out["lastAdopted"])
        self.assertEqual(out["lastState"], "paused")

    def test_stopping_drops_animations_that_went_idle(self):
        out = self.run_js(r"""
const ring=makeAnimation('ringSpin',0);animations=[ring];syncAmbientClock();advance(200);
ring.playState='idle';ambientMotionIdle=true;syncAmbientClock();
console.log(JSON.stringify({size:ambientClockAnimations.size}));
""")
        self.assertEqual(out["size"], 0)

    def test_clock_rests_with_nothing_to_step_and_wakes_for_new_motion(self):
        out = self.run_js(r"""
activePageHeroHandle=null;activePageBorderHandle=null;
syncAmbientClock();advance(2000);
const rested={timers:timers.length,frames:frames.length};
const pulse=makeAnimation('sessionFlowStatusPulse',40);
listeners.animationstart.forEach(fn=>fn({animationName:'sessionFlowStatusPulse',target:{getAnimations:()=>[pulse]}}));
advance(1000);
const moved=Math.round(pulse.currentTime-40),armed=timers.length+frames.length;
pulse.playState='idle';advance(200);
const restedAgain={timers:timers.length,frames:frames.length,adopted:ambientClockAnimations.size};
activePageHeroHandle={tick:dt=>ticks.hero.push(dt)};scheduleAmbientClock();advance(500);
console.log(JSON.stringify({rested,moved,armed,restedAgain,heroTicks:ticks.hero.length,heroFirst:ticks.hero[0]}));
""")
        # Nothing adopted and no shader mounted: the clock does not re-arm.
        self.assertEqual(out["rested"], {"timers": 0, "frames": 0})
        # An ambient animation that starts wakes it, and it steps that animation.
        self.assertGreater(out["moved"], 900)
        self.assertLessEqual(out["moved"], 1000)
        self.assertEqual(out["armed"], 1)
        self.assertEqual(out["restedAgain"], {"timers": 0, "frames": 0, "adopted": 0})
        # A shader mount wakes it too; the first step after resting is zero, so nothing jumps.
        self.assertGreaterEqual(out["heroTicks"], 8)
        self.assertEqual(out["heroFirst"], 0)

    def test_clock_covers_every_infinite_ambient_animation_in_the_stylesheets(self):
        import re
        clock = (ROOT / "dashboard/js/ambient-clock.js").read_text()
        names = set(re.search(r"AMBIENT_ANIMATIONS=new Set\(\[([^\]]*)\]\)", clock).group(1).replace("'", "").split(","))
        css = "".join(p.read_text() for p in sorted((ROOT / "dashboard/styles").glob("*.css")))
        infinite = set(re.findall(r"animation:([A-Za-z]+)[^;}]*\binfinite\b", css))
        self.assertTrue(infinite)
        # Longhand loops would slip past the shorthand scan; keep looping motion on the shorthand.
        self.assertNotIn("animation-iteration-count:infinite", css.replace(" ", ""))
        self.assertEqual(infinite - names, set(), "a looping animation is not on the ambient clock")

    def test_low_power_resync_also_syncs_the_clock(self):
        live = (ROOT / "dashboard/js/live.js").read_text()
        body = live[live.index("function syncAmbientMotion(){"):live.index("\n}", live.index("function syncAmbientMotion(){"))]
        self.assertIn("syncAmbientClock();", body)
        manifest = (ROOT / "dashboard/page.manifest").read_text().split()
        self.assertLess(manifest.index("js/ambient-clock.js"), manifest.index("js/live.js"))


ADAPTER_HARNESS = r"""
const fs=require('fs'),root=process.argv[1];
const src=fs.readFileSync(root+'/assets/session-effects.js','utf8');
const start=src.indexOf('function mountHandle(');
const body=src.slice(start,src.indexOf('\n}\n',start)+2);
const mounts=[];
class ShaderMount{constructor(el,shader,uniforms,options,speed,frame){this.speed=speed;this.frame=frame;this.isInViewport=true;this.renders=[];mounts.push(this);}
 setFrame(f){this.frame=f;this.renders.push(f);} setUniforms(){} dispose(){}}
const WEBGL_OPTIONS={};
eval(body);
const element={dataset:{},ownerDocument:{hidden:false}};
"""


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class ShaderTickTests(unittest.TestCase):
    def test_shader_mount_advances_only_on_the_ambient_clock(self):
        script = ADAPTER_HARNESS + r"""
const handle=mountHandle(element,'',{},{speed:.02,frame:100},1000);
const mount=mounts[0];
handle.tick(50);const afterTick=mount.frame;
handle.tick(0);handle.tick(-5);
mount.isInViewport=false;handle.tick(50);mount.isInViewport=true;
element.ownerDocument.hidden=true;handle.tick(50);element.ownerDocument.hidden=false;
handle.setSpeed(0);handle.tick(50);
handle.setSpeed(.02);handle.tick(100);
console.log(JSON.stringify({vendorSpeed:mount.speed,afterTick,renders:mount.renders,final:mount.frame}));
"""
        result = subprocess.run(["node", "-e", script, str(ROOT)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        # The vendor mount never runs its own 60 fps loop.
        self.assertEqual(out["vendorSpeed"], 0)
        self.assertAlmostEqual(out["afterTick"], 101)
        # Zero or negative steps, off-screen, hidden, and speed 0 draw nothing.
        self.assertEqual(len(out["renders"]), 2)
        self.assertAlmostEqual(out["final"], 103)

    def test_disposing_a_shader_releases_its_webgl_context(self):
        script = ADAPTER_HARNESS + r"""
const log=[];
const handle=mountHandle(element,'',{},{speed:.02,frame:0},1000);
const mount=mounts[0];
mount.gl={getExtension:name=>name==='WEBGL_lose_context'?{loseContext:()=>log.push('lose')}:null};
mount.dispose=()=>log.push('vendor');
const ready=element.dataset.pageEffect;
handle.dispose();
const bare=mountHandle({dataset:{},ownerDocument:{hidden:false}},'',{},{speed:0,frame:0},1000);
let bareError=null;try{bare.dispose();}catch(error){bareError=String(error);}
console.log(JSON.stringify({log,ready,after:element.dataset.pageEffect??null,bareError}));
"""
        result = subprocess.run(["node", "-e", script, str(ROOT)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        # The vendor dispose runs first, then the context is lost at once instead of at garbage collection.
        self.assertEqual(out["log"], ["vendor", "lose"])
        self.assertEqual(out["ready"], "ready")
        self.assertIsNone(out["after"])
        self.assertIsNone(out["bareError"])

    def test_shaders_skip_multisampling(self):
        import re
        effects = (ROOT / "assets/session-effects.js").read_text()
        options = re.search(r"const WEBGL_OPTIONS = \{(.*?)\};", effects, re.S).group(1)
        self.assertIn("antialias: false,", options)
        self.assertNotIn("antialias: true", effects)

    def test_both_page_effects_expose_tick(self):
        effects = (ROOT / "assets/session-effects.js").read_text()
        self.assertEqual(effects.count("tick: handle.tick,"), 2)
        sessions = (ROOT / "dashboard/js/current-sessions.js").read_text()
        self.assertIn("tick(dt){if(!disposed)handle.tick?.(dt);}", sessions)


if __name__ == "__main__":
    unittest.main()
