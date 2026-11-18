import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r"""
const fs=require('fs'),root=process.argv[1];
const live=fs.readFileSync(root+'/dashboard/js/live.js','utf8');
const core=fs.readFileSync(root+'/dashboard/js/core.js','utf8');
const settings=fs.readFileSync(root+'/dashboard/js/notifications-settings.js','utf8');
const part=(s,a,b)=>{const i=s.indexOf(a);if(i<0)throw Error('missing '+a);const j=s.indexOf(b,i);if(j<0)throw Error('missing '+b);return s.slice(i,j);};
let now=0,timers=[],nextTimer=1,polls=[],shaderSyncs=0;
function setTimeout(fn,ms){const id=nextTimer++;timers.push({id,fn,at:now+ms,ms});return id;}
function clearTimeout(id){timers=timers.filter(t=>t.id!==id);}
function advance(ms){const end=now+ms;for(;;){timers.sort((a,b)=>a.at-b.at);const t=timers[0];if(!t||t.at>end)break;timers.shift();now=t.at;t.fn();}now=end;}
const classes=new Set();
const document={hidden:false,documentElement:{classList:{toggle:(name,on)=>on?classes.add(name):classes.delete(name)}}};
const nodes={};
function $(id){return nodes[id]||(nodes[id]={textContent:'',buttons:[],querySelectorAll(){return this.buttons;}});}
['auto','on','off'].forEach(mode=>$('power-low-power-mode').buttons.push({dataset:{lowPowerMode:mode},pressed:'',setAttribute(k,v){this.pressed=v;}}));
function refreshLiveState(){polls.push(['state',now]);}
function refreshSelectedSession(){polls.push(['session',now]);}
function syncActivePageSignalEffects(){shaderSyncs++;}
eval(part(core,'let lowPowerActive=','// end-low-power').replace(/^let /gm,'var ').replace(/^const /gm,'var '));
eval(part(live,'const LIVE_STATE_POLL_MS','// Deep links into Settings').replace(/^const /gm,'var ').replace(/^let /gm,'var '));
var powerSettingsBusy=false;eval(part(settings,'function powerSettingsSummary(','$(\'power-low-power-mode\').onclick'));
const pressed=()=>$('power-low-power-mode').buttons.filter(b=>b.pressed==='true').map(b=>b.dataset.lowPowerMode);
const count=kind=>polls.filter(p=>p[0]===kind).length;
"""


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class DashboardLowPowerTests(unittest.TestCase):
    def run_js(self, body):
        result = subprocess.run(
            ["node", "-e", HARNESS + body, str(ROOT)], text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_polls_slow_in_low_power_and_stop_while_hidden(self):
        out = self.run_js(r"""
restartLivePolls();advance(10000);
const normal={state:count('state'),session:count('session')};
polls=[];applyPowerState({mode:'auto',active:true,reason:'battery',power_source:'battery'});
advance(30000);
const low={state:count('state'),session:count('session'),classes:[...classes].sort(),shaderSyncs};
polls=[];document.hidden=true;restartLivePolls();advance(60000);
const hidden={polls:polls.length,timers:timers.length};
document.hidden=false;restartLivePolls();polls=[];
applyPowerState({mode:'off',active:false,reason:null,power_source:'battery'});advance(5000);
console.log(JSON.stringify({normal,low,hidden,back:{state:count('state'),classes:[...classes]}}));
""")
        self.assertEqual(out["normal"], {"state": 10, "session": 5})
        self.assertEqual(out["low"]["state"], 6)
        self.assertEqual(out["low"]["session"], 5)
        self.assertEqual(out["low"]["classes"], ["ambientPaused", "lowPower"])
        self.assertEqual(out["low"]["shaderSyncs"], 1)
        self.assertEqual(out["hidden"], {"polls": 0, "timers": 0})
        self.assertEqual(out["back"], {"state": 5, "classes": []})

    def test_unchanged_power_state_does_not_restart_timers_or_shaders(self):
        out = self.run_js(r"""
restartLivePolls();const before=timers.map(t=>t.id).join();
applyPowerState({mode:'auto',active:false,reason:null,power_source:'ac'});
applyPowerState(null);applyPowerState('bad');
console.log(JSON.stringify({same:before===timers.map(t=>t.id).join(),shaderSyncs,active:lowPowerActive}));
""")
        self.assertEqual(out, {"same": True, "shaderSyncs": 0, "active": False})

    def test_background_window_pauses_ambient_motion_after_grace_and_focus_resumes(self):
        out = self.run_js(r"""
pauseAmbientMotionSoon();advance(AMBIENT_BLUR_PAUSE_MS-1);
const early=classes.has('ambientPaused');advance(1);
const paused=classes.has('ambientPaused'),stopped=ambientMotionStopped(),low=classes.has('lowPower');
resumeAmbientMotion();
const resumed=!classes.has('ambientPaused');
pauseAmbientMotionSoon();advance(1000);resumeAmbientMotion();advance(AMBIENT_BLUR_PAUSE_MS);
console.log(JSON.stringify({early,paused,stopped,low,resumed,quickReturn:classes.has('ambientPaused'),polls:polls.length}));
""")
        self.assertEqual(out, {"early": False, "paused": True, "stopped": True, "low": False,
                               "resumed": True, "quickReturn": False, "polls": 0})

    def test_settings_control_reflects_mode_and_reason(self):
        out = self.run_js(r"""
const rows=[];
for(const power of [
 {mode:'auto',active:true,reason:'battery',power_source:'battery'},
 {mode:'auto',active:true,reason:'system',power_source:'ac'},
 {mode:'auto',active:false,reason:null,power_source:'ac'},
 {mode:'auto',active:false,reason:null,power_source:null},
 {mode:'on',active:true,reason:'setting',power_source:'ac'},
 {mode:'off',active:false,reason:null,power_source:'battery'},
]){applyPowerState(power);rows.push([pressed(),$('power-settings-summary').textContent]);}
console.log(JSON.stringify(rows));
""")
        self.assertEqual(out, [
            [["auto"], "On now: running on battery."],
            [["auto"], "On now: the system Low Power Mode is on."],
            [["auto"], "Off now: plugged in."],
            [["auto"], "Off now: this computer does not report its power source."],
            [["on"], "On now."],
            [["off"], "Off. Full animation and refresh rate."],
        ])

    def test_shader_presentation_and_css_follow_the_ambient_pause(self):
        page_js = (ROOT / "dashboard/js/current-sessions.js").read_text()
        self.assertEqual(page_js.count("pageSignalReducedMotionQuery.matches||ambientMotionStopped()"), 2)
        css = (ROOT / "dashboard/styles/shell.css").read_text()
        rule = css.split("html.ambientPaused body.spectrumApp:after", 1)[1].split("}", 1)[0]
        self.assertIn("animation-play-state:paused", rule)
        for selector in (".spectrumPageHeadFrame:before", ".spectrumPageHeadFrame:after",
                         ".spectrumPageHead h1", ".top:before", ".cacheRing.bad", ".sessionFlowStatus i"):
            self.assertIn(selector, rule)


if __name__ == "__main__":
    unittest.main()
