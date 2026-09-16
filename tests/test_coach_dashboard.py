import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

import meter


class CoachDashboardContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = Path(meter._SOURCE_ROOT, "page.html").read_text(encoding="utf-8")

    def run_coach_node(self, functions, body, setup=""):
        """Execute the shipped Coach functions with only browser boundaries doubled."""
        script = r"""
const fs=require('fs');
const page=fs.readFileSync(__PAGE__,'utf8');
function extract(name){
 const plain=page.indexOf(`function ${name}(`),asyncStart=page.indexOf(`async function ${name}(`);
 const start=asyncStart>=0&&(plain<0||asyncStart<plain)?asyncStart:plain;
 if(start<0)throw new Error(`missing production function ${name}`);
 let i=page.indexOf('(',start),parens=0,quote='',escaped=false;
 for(;i<page.length;i++){
  const char=page[i];
  if(quote){if(escaped)escaped=false;else if(char==='\\')escaped=true;else if(char===quote)quote='';continue;}
  if(char==='"'||char==="'"||char==='`'){quote=char;continue;}
  if(char==='(')parens++;else if(char===')'&&--parens===0){i=page.indexOf('{',i);break;}
 }
 let depth=0;quote='';escaped=false;
 for(;i<page.length;i++){
  const char=page[i];
  if(quote){if(escaped)escaped=false;else if(char==='\\')escaped=true;else if(char===quote)quote='';continue;}
  if(char==='"'||char==="'"||char==='`'){quote=char;continue;}
  if(char==='{')depth++;else if(char==='}'&&--depth===0)return page.slice(start,i+1);
 }
 throw new Error(`unterminated production function ${name}`);
}
function extractConst(name){
 const start=page.indexOf(`const ${name}=`);if(start<0)throw new Error(`missing production constant ${name}`);
 const end=page.indexOf(';\n',start);if(end<0)throw new Error(`unterminated production constant ${name}`);
 return page.slice(start,end+1);
}
class ClassList{
 constructor(node){this.node=node;}
 values(){return this.node.className.split(/\s+/).filter(Boolean);}
 contains(name){return this.values().includes(name);}
 add(...names){this.node.className=[...new Set([...this.values(),...names])].join(' ');}
 remove(...names){this.node.className=this.values().filter(name=>!names.includes(name)).join(' ');}
 toggle(name,force){const add=force===undefined?!this.contains(name):Boolean(force);add?this.add(name):this.remove(name);return add;}
}
class Node{
 constructor(tag='div',id=''){this.tagName=tag.toUpperCase();this.id=id;this.children=[];this.hidden=false;this.disabled=false;this.className='';this.textContent='';this.value='';this.parentNode=null;this.style={};this.attributes={};this.dataset={};this.focused=false;this.classList=new ClassList(this);}
 append(...children){children.forEach(child=>{child.parentNode=this;this.children.push(child);});}
 appendChild(child){this.append(child);return child;}
 replaceChildren(...children){this.children.forEach(child=>child.parentNode=null);this.children=[];this.append(...children);}
 remove(){if(!this.parentNode)return;const at=this.parentNode.children.indexOf(this);if(at>=0)this.parentNode.children.splice(at,1);this.parentNode=null;}
 setAttribute(name,value=''){this.attributes[name]=String(value);if(name==='id')this.id=String(value);}
 getAttribute(name){return Object.prototype.hasOwnProperty.call(this.attributes,name)?this.attributes[name]:null;}
 hasAttribute(name){return Object.prototype.hasOwnProperty.call(this.attributes,name);}
 removeAttribute(name){delete this.attributes[name];}
 toggleAttribute(name,force){const add=force===undefined?!this.hasAttribute(name):Boolean(force);if(add)this.setAttribute(name,'');else this.removeAttribute(name);return add;}
 focus(){allNodes().forEach(node=>node.focused=false);this.focused=true;document.activeElement=this;}
 scrollIntoView(){}
 closest(selector){let node=this;while(node){if(matches(node,selector))return node;node=node.parentNode;}return null;}
 querySelectorAll(selector){return descendants(this).filter(node=>selector.split(',').some(part=>matches(node,part.trim())));}
 querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
 get childElementCount(){return this.children.length;}
 get lastElementChild(){return this.children.at(-1)||null;}
}
function matches(node,selector){
 if(selector.startsWith('.'))return node.classList.contains(selector.slice(1));
 if(selector.startsWith('#'))return node.id===selector.slice(1);
 if(selector==='[tabindex]')return node.hasAttribute('tabindex');
 if(selector==='[hidden]')return node.hidden||node.hasAttribute('hidden');
 if(selector==='dialog[open]')return node.tagName==='DIALOG'&&node.hasAttribute('open');
 return node.tagName===selector.toUpperCase();
}
function descendants(root){const result=[];for(const child of root.children){result.push(child,...descendants(child));}return result;}
const ids=['coach-messages','coach-stop','coach-send','coach-weekly-run','coach-activity','coach-input','coach-panel','coach-trigger','coach-context-label','coach-goal-sheet','coach-goal-rail','coach-goal-empty','coach-goal-card','coach-weekly-card','coach-goal-label','coach-goal-meta','coach-goal-progress','coach-goal-progress-bar','coach-goal-baseline','coach-goal-current','coach-goal-coverage','coach-weekly-toggle','coach-goal-next-review','coach-weekly-recommendation','coach-weekly-kicker','coach-weekly-evidence','coach-weekly-meta','coach-weekly-status','coach-form','coach-close','coach-backdrop','coach-goal-clear','coach-notice','coach-notice-text','coach-notice-action','coach-trigger-dot','coach-view-chat','view-session','view-daily','view-models','view-efficiency','view-git','view-learn','view-capabilities','view-settings'];
const nodes=Object.fromEntries(ids.map(id=>[id,new Node(id==='coach-input'?'textarea':id.includes('button')?'button':'div',id)]));
nodes['coach-stop'].hidden=true;nodes['coach-goal-sheet'].hidden=true;nodes['coach-goal-rail'].hidden=true;
const goalButton=new Node('button');goalButton.setAttribute('tabindex','0');nodes['coach-goal-sheet'].append(goalButton);
const wrap=new Node('main');wrap.className='wrap';
const bodyNode=new Node('body');
const documentListeners={};
const windowListeners={};
function roots(){return [bodyNode,wrap,...Object.values(nodes)];}
function allNodes(){const seen=new Set(),result=[];for(const root of roots())for(const node of [root,...descendants(root)])if(!seen.has(node)){seen.add(node);result.push(node);}return result;}
global.document={
 body:bodyNode,hidden:false,activeElement:null,
 createElement:tag=>new Node(tag),
 querySelector:selector=>selector==='.wrap'?wrap:allNodes().find(node=>matches(node,selector))||null,
 querySelectorAll:selector=>allNodes().filter(node=>selector.split(',').some(part=>matches(node,part.trim()))),
 addEventListener:(type,handler)=>{(documentListeners[type]??=[]).push(handler);},
 dispatch:(type,event={})=>(documentListeners[type]||[]).forEach(handler=>handler(event)),
};
global.window={addEventListener:(type,handler)=>{(windowListeners[type]??=[]).push(handler);},dispatch:(type,event={})=>(windowListeners[type]||[]).forEach(handler=>handler(event))};
global.$=id=>nodes[id];
global.location={hash:'',pathname:'/',search:''};
global.localStorage={values:{},getItem(key){return this.values[key]??null;},setItem(key,value){this.values[key]=String(value);}};
global.requestAnimationFrame=callback=>{callback();return 1;};
let viewport=1400,reducedMotion=false;
const mediaListeners=[];
global.matchMedia=query=>({matches:query.includes('prefers-reduced-motion')?reducedMotion:(viewport>=1024&&viewport<=1180),addEventListener:(type,handler)=>mediaListeners.push(handler)});
let clock=1000,nextTimer=1;
const intervals=new Map(),timeouts=new Map();
Date.now=()=>clock;
global.setInterval=(callback,delay)=>{const id=nextTimer++;intervals.set(id,{callback,delay});return id;};
global.clearInterval=id=>intervals.delete(id);
global.setTimeout=(callback,delay)=>{const id=nextTimer++;timeouts.set(id,{callback,at:clock+delay});return id;};
global.clearTimeout=id=>timeouts.delete(id);
function advance(milliseconds){clock+=milliseconds;for(const [id,timer] of [...timeouts])if(timer.at<=clock){timeouts.delete(id);timer.callback();}}
function runInterval(id){intervals.get(id)?.callback();}
const fetchCalls=[],fetchQueue=[];
global.fetch=(url,options={})=>{fetchCalls.push({url,options});if(!fetchQueue.length)throw new Error(`unexpected fetch ${url}`);const next=fetchQueue.shift();return typeof next==='function'?next(url,options):Promise.resolve(next);};
function response(payload,ok=true){return {ok,json:async()=>payload};}
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
const program=[
 'var COACH_STATE=null,coachMessages=[],coachDraft=null,coachBusy=false,coachTurn=null,coachTurnSerial=0,coachPollTimer=0,coachStopTimer=0,coachVisualTimer=0,coachStatusNotice=null,coachStatusTimer=0,coachNarratedWeeklyAt;',
 extractConst('COACH_ACTIONS'),extractConst('COACH_ACTIVITY_COPY'),extractConst('COACH_TOOL_COPY'),extractConst('COACH_PATIENCE_MS'),extractConst('COACH_RECOMMENDATIONS'),extractConst('COACH_WEEKDAYS'),extractConst('COACH_ROUTE_LABELS'),
 __SETUP__,...__FUNCTIONS__.map(extract),__BODY__
].join('\n');
eval(program);
"""
        replacements = {
            "__PAGE__": json.dumps(str(Path(meter._SOURCE_ROOT, "page.html"))),
            "__SETUP__": json.dumps(setup),
            "__FUNCTIONS__": json.dumps(functions),
            "__BODY__": json.dumps(body),
        }
        for marker, value in replacements.items():
            script = script.replace(marker, value)
        return subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        ).stdout.strip()

    def test_context_lens_replaces_tabs_and_permanent_status_badges(self):
        for marker in (
            "id=coach-trigger",
            "id=coach-panel",
            "aria-hidden=true inert",
            'role="complementary"',
            'aria-labelledby=coach-title',
            'id=coach-activity role=status aria-live=polite',
            'id=coach-goal-rail',
            'id=coach-goal-sheet',
            'id=coach-context-label',
            'id=coach-stop',
            "plan.direct?'coachAction direct':'coachAction'",
            'id=coach-input maxlength=2000',
            "Messages and selected metrics go to Codex/OpenAI.",
            "Chat clears on refresh.",
        ):
            self.assertIn(marker, self.page)
        for removed in (
            "data-coach-tab=chat", "data-coach-tab=goal",
            "data-coach-tab=weekly", "id=coach-agent-state",
        ):
            self.assertNotIn(removed, self.page)
        self.assertIn("toggleAttribute('inert',!open)", self.page)

    def test_hidden_coach_surfaces_keep_author_level_display_none(self):
        self.assertIn("id=coach-goal-rail type=button hidden", self.page)
        self.assertRegex(self.page, r"#coach-panel\s+\[hidden\]\{display:none\}")

    def test_tok_is_the_consistent_user_facing_identity(self):
        for marker in (
            'aria-label="Open Tok, Master of tokens"',
            '<span class=coachTriggerName>Tok</span>',
            '<h2 id=coach-title>Tok</h2>',
            'aria-label="Close Tok"',
            "<div class=coachEyebrow>Master of tokens</div>",
            "Ask what to change. I answer from your local evidence and point at the control.",
            'aria-label="Message Tok"',
            "meta.textContent=role==='user'?'You':'Tok'",
            "Tok drafts it as something measurable for you to approve.",
        ):
            self.assertIn(marker, self.page)

        for stale_label in (
            '>Token Meter Coach<',
            '<span>Coach</span>',
            'aria-label="Message Token Meter Coach"',
        ):
            self.assertNotIn(stale_label, self.page)

    def test_tok_emblem_has_motion_and_a_reduced_motion_fallback(self):
        for marker in (
            'class=coachMarkOrbit',
            'class=coachMarkCore',
            'class=coachMarkSpark',
            '@keyframes tokOrbit',
            '@keyframes tokCorePulse',
            '.coachMarkOrbit,.coachMarkSpark{animation:none}',
            '.coachReducedMotion .coachMarkOrbit,.coachReducedMotion .coachMarkSpark,.coachReducedMotion .coachMarkCore{animation:none}',
            '.coachReducedMotion .coachContextFocus{outline:none;transition:none}',
        ):
            self.assertIn(marker, self.page)

    def test_layout_docks_wide_and_overlays_at_laptop_width(self):
        for marker in (
            "body.coachOpen #coach-panel{transform:translateX(0)",
            "@media(min-width:1181px)",
            "body.coachOpen .wrap{margin-right:414px",
            "@media(min-width:1024px) and (max-width:1180px)",
            "#coach-panel{width:min(404px,calc(100vw - 24px))",
            "body.coachOpen .coachBackdrop{display:block}",
            ".coachEmpty[hidden],.coachCard[hidden]{display:none}",
            "function syncCoachSurfaceMode",
            "document.querySelector('.wrap').toggleAttribute('inert',overlay)",
        ):
            self.assertIn(marker, self.page)

    def test_review_regressions_keep_header_timer_and_retry_truthful(self):
        for marker in (
            'class=coachTitleGroup><div class=coachTitleRow><h2 id=coach-title>Tok</h2><p id=coach-context-label>Sessions</p></div>',
            'let coachBusy=false,coachTurn=null,coachTurnSerial=0,coachPollTimer=0,coachStopTimer=0,coachVisualTimer=0;',
            'clearInterval(coachVisualTimer)', 'function coachResumeVisualTimer()',
            "button.onclick=()=>coachRetryTurn(turn);", 'function coachRetryTurn(turn)',
        ):
            self.assertIn(marker, self.page)

    def test_hidden_turn_pauses_only_visual_timer_and_empty_state_is_one_sentence(self):
        self.assertIn("function coachPauseVisualTimer()", self.page)
        self.assertIn("function coachResumeVisualTimer()", self.page)
        self.assertIn("document.addEventListener('visibilitychange'", self.page)
        self.assertIn("document.addEventListener('visibilitychange',coachHandleVisibilityChange)", self.page)
        self.assertIn("if(document.hidden){coachPauseVisualTimer();return;}", self.page)
        self.assertNotIn('<h3>One clear move from your token signals.</h3>', self.page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_history_and_navigation_helpers_are_bounded_and_allowlisted(self):
        script = f"""
const fs=require('fs');
const page=fs.readFileSync({json.dumps(str(Path(meter._SOURCE_ROOT, 'page.html')))},'utf8');
function extract(name){{
 let start=page.indexOf(`function ${{name}}(`);if(start<0)throw new Error(name);
 let i=page.indexOf('{{',start),depth=0;
 for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}
}}
eval(extract('coachRequestHistory'));
eval(extract('coachRouteTarget'));
const messages=Array.from({{length:9}},(_,i)=>({{role:i%2?'assistant':'user',content:'x'.repeat(2100)+i}}));
console.log(JSON.stringify({{
 history:coachRequestHistory(messages),
 efficiency:coachRouteTarget('efficiency'),
 budgets:coachRouteTarget('settings-budgets'),
 rejected:coachRouteTarget('javascript:alert(1)'),
}}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        payload = json.loads(result.stdout)
        self.assertEqual(len(payload["history"]), 6)
        self.assertTrue(all(len(row["content"]) == 2000 for row in payload["history"]))
        self.assertEqual(payload["history"][0]["role"], "assistant")
        self.assertEqual(payload["efficiency"], "efficiency")
        self.assertEqual(payload["budgets"], "settings-budgets")
        self.assertIsNone(payload["rejected"])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_node_harness_executes_live_turn_row_and_stage_behavior(self):
        """A broken begin/apply implementation must fail this executable DOM harness."""
        script = f"""
const fs=require('fs'),page=fs.readFileSync({json.dumps(str(Path(meter._SOURCE_ROOT, 'page.html')))},'utf8');
function extract(n){{let s=page.indexOf(`function ${{n}}(`),i=page.indexOf('{{',s),d=0;if(s<0)throw Error(n);for(;i<page.length;i++){{if(page[i]==='{{')d++;else if(page[i]==='}}'&&!--d)return page.slice(s,i+1)}}}}
class Node{{constructor(){{this.children=[];this.hidden=false;this.className='';this.textContent='';this.parentNode=null;this.style={{}}}}append(...xs){{xs.forEach(x=>{{x.parentNode=this;this.children.push(x)}})}}replaceChildren(...xs){{this.children=[];this.append(...xs)}}remove(){{this.parentNode?.children.splice(this.parentNode.children.indexOf(this),1)}}setAttribute(){{}}}}
const nodes={{'coach-messages':new Node(),'coach-stop':new Node(),'coach-send':new Node(),'coach-weekly-run':new Node(),'coach-activity':new Node(),'view-session':new Node()}};
global.document={{createElement:()=>new Node(),querySelectorAll:()=>[],querySelector:()=>null}};global.$=id=>nodes[id];global.setInterval=(fn)=>{{global.tick=fn;return 7}};global.clearInterval=()=>{{}};global.setTimeout=(fn)=>{{global.stopTick=fn;return 8}};global.Date={{now:()=>1000}};
let coachBusy=false,coachTurn=null,coachTurnSerial=0,coachPollTimer=0,coachStopTimer=0,coachVisualTimer=0,coachMessages=[];const COACH_ACTIVITY_COPY={{opening_codex:'Opening Codex',reading_token_meter:'Reading Token Meter',checking_evidence:'Checking the evidence'}},COACH_TOOL_COPY={{usage:'Reading your usage history'}},COACH_PATIENCE_MS=25000;
function coachAppendMessage(role,content){{nodes['coach-messages'].append(Object.assign(new Node(),{{role,content}}))}}function coachSetBusy(v){{coachBusy=v;nodes['coach-send'].disabled=v}}function coachSetLive(){{}}function coachCurrentRoute(){{return 'sessions'}}
eval(extract('coachShowStop'));eval(extract('coachRetireStaleRetry'));eval(extract('coachClearTurn'));eval(extract('coachRenderActivityNote'));eval(extract('coachActivityStageLabel'));eval(extract('coachResumeVisualTimer'));eval(extract('coachBeginTurn'));eval(extract('coachApplyActivity'));
const id=coachBeginTurn('hello');coachApplyActivity({{stage:'opening_codex',cancellable:true}},id);const activity=nodes['coach-messages'].children.at(-1);
const stageLabel=activity.children[1].children[0];
if(coachMessages.length!==1||stageLabel.textContent!=='Opening Codex'||!activity.children[0].className.includes('coachActivityOrbit')||nodes['coach-send'].disabled!==true||nodes['coach-input']?.disabled)throw Error('live-turn contract');
console.log('live harness ok');
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), "live harness ok")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_live_turn_polling_and_terminal_resolution_mutate_one_real_row(self):
        output = self.run_coach_node(
            [
                "coachSetLive", "coachAppendMessage", "coachSetBusy",
                "coachShowStop", "coachClearTurn", "coachRenderActivityNote",
                "coachActivityStageLabel", "coachRetireStaleRetry", "coachPauseVisualTimer", "coachResumeVisualTimer",
                "coachBeginTurn", "coachApplyActivity", "coachPollActivity",
                "coachResolveTurn", "coachRouteTarget", "coachGoalSummary",
            ],
            r"""
(async()=>{
 const turnId=coachBeginTurn('Show me the strongest signal');
 const row=coachTurn.activity.row;
 if(coachMessages.length!==1||coachMessages[0].role!=='user')throw new Error('user history was not recorded once');
 if(nodes['coach-messages'].children.length!==2||row.className!=='coachActivityMessage')throw new Error('transient row was not inserted immediately');
 if(row.children[0].getAttribute('aria-hidden')!=='true')throw new Error('indeterminate orbit became assistive noise');
 if(row.children[2].getAttribute('aria-hidden')!=='true')throw new Error('visual timer became assistive status noise');
 fetchQueue.push(response({ok:true,agent:{activity:{stage:'opening_codex',cancellable:false}}}));
 await coachPollActivity(turnId);
 fetchQueue.push(response({ok:true,agent:{activity:{stage:'checking_evidence',cancellable:true}}}));
 await coachPollActivity(turnId);
 if(coachTurn.activity.row!==row||nodes['coach-messages'].children.filter(node=>node.className==='coachActivityMessage').length!==1)throw new Error('polling replaced the working row');
 if(row.children[1].children[0].textContent!=='Checking the evidence')throw new Error('latest observed stage was not rendered');
 fetchQueue.push(response({ok:true,agent:{activity:{stage:'reading_token_meter',tool:'usage',cancellable:true,reads:2}}}));
 await coachPollActivity(turnId);
 if(row.children[1].children[0].textContent!=='Reading your usage history')throw new Error('allowlisted tool did not sharpen the stage copy');
 if(row.children[1].children[1].hidden||!row.children[1].children[1].textContent.includes('2 Token Meter readings complete'))throw new Error('observed evidence reads were not surfaced');
 if(coachMessages.length!==1)throw new Error('working status leaked into request history');
 coachResolveTurn({message:'Retries are the strongest covered signal.'},turnId);
 if(row.parentNode!==null||coachTurn!==null||coachBusy)throw new Error('terminal success did not clear the live turn');
 if(coachMessages.length!==2||coachMessages[1].content!=='Retries are the strongest covered signal.')throw new Error('terminal answer did not replace the row in history');
 console.log('terminal lifecycle ok');
})().catch(error=>{console.error(error);process.exitCode=1;});
""",
            setup="function renderCoachState(){} function coachCurrentRoute(){return 'sessions';} function setHashRoute(){} function activateCoachGoal(){}",
        )
        self.assertEqual(output, "terminal lifecycle ok")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_send_executes_immediately_keeps_textarea_enabled_and_resolves_error(self):
        output = self.run_coach_node(
            [
                "coachRequestHistory", "coachCurrentRoute", "coachPageContext",
                "coachActionToken", "coachErrorMessage", "coachSetLive",
                "coachAppendMessage", "coachSetBusy", "coachShowStop", "coachClearTurn",
                "coachRenderActivityNote", "coachActivityStageLabel", "coachRetireStaleRetry", "coachPauseVisualTimer", "coachResumeVisualTimer", "coachBeginTurn",
                "coachResolveTurn", "coachFailTurn", "sendCoachMessage", "coachRouteTarget",
                "coachGoalSummary",
            ],
            r"""
(async()=>{
 nodes['coach-input'].value='  First question  ';
 const ask=deferred();fetchQueue.push(()=>ask.promise);
 const pending=sendCoachMessage();
 if(nodes['coach-input'].value!==''||nodes['coach-input'].disabled)throw new Error('textarea was disabled or not cleared');
 if(!nodes['coach-send'].disabled||coachMessages.length!==1)throw new Error('submit did not begin synchronously');
 if(nodes['coach-messages'].children.at(-1).className!=='coachActivityMessage')throw new Error('activity row was not immediate');
 ask.resolve(response({ok:true,reply:{message:'One covered answer.'}}));await pending;
 if(nodes['coach-send'].disabled||coachMessages.at(-1).content!=='One covered answer.')throw new Error('success did not restore the composer');
 nodes['coach-input'].value='Second question';fetchQueue.push(()=>Promise.reject(new Error('Tok could not reach the local service.')));
 await sendCoachMessage();
 const failed=nodes['coach-messages'].lastElementChild;
 if(failed.className!=='coachMessage error')throw new Error('a delivery failure was rendered in Tok\'s voice');
 if(failed.querySelector('.coachMessageText')?.textContent!=='Tok could not reach the local service.')throw new Error('failure text was not shown in the conversation');
 if(!failed.querySelector('.coachInlineButton'))throw new Error('failure did not offer retry');
 if(coachMessages.some(row=>row.content==='Tok could not reach the local service.'))throw new Error('failure text leaked into request history');
 if(coachBusy||nodes['coach-send'].disabled||!nodes['coach-stop'].hidden)throw new Error('failure did not restore the composer');
 console.log('send lifecycle ok');
})().catch(error=>{console.error(error);process.exitCode=1;});
""",
            setup="function routedSessionId(){return '';} function setHashRoute(){} function activateCoachGoal(){} function coachPollActivity(){} function loadCoachState(){} function coachStatus(){}",
        )
        self.assertEqual(output, "send lifecycle ok")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_delayed_stop_retries_in_the_original_answer_position_without_duplicate_user_or_live_label(self):
        output = self.run_coach_node(
            [
                "coachRequestHistory", "coachCurrentRoute", "coachPageContext",
                "coachActionToken", "coachErrorMessage", "coachSetLive",
                "coachAppendMessage", "coachSetBusy", "coachShowStop", "coachClearTurn",
                "coachRenderActivityNote", "coachActivityStageLabel", "coachRetireStaleRetry", "coachPauseVisualTimer", "coachResumeVisualTimer", "coachBeginTurn",
                "coachApplyActivity", "coachResolveTurn", "coachStopTurn",
                "coachRetryTurn", "sendCoachMessage", "coachRouteTarget",
                "coachGoalSummary",
            ],
            r"""
(async()=>{
 COACH_STATE={actions:{token:'action-token'}};
 const turnId=coachBeginTurn('Try this again');coachApplyActivity({stage:'opening_codex',cancellable:true},turnId);
 advance(7999);if(!nodes['coach-stop'].hidden)throw new Error('stop appeared before eight seconds');
 advance(1);if(nodes['coach-stop'].hidden)throw new Error('cancellable stop did not appear at eight seconds');
 const cancel=deferred();fetchQueue.push(()=>cancel.promise);
 const stopping=coachStopTurn();const duplicate=coachStopTurn();
 await Promise.resolve();
 if(fetchCalls.filter(call=>call.url==='/coach/cancel').length!==1)throw new Error('cancel was not idempotent');
 cancel.resolve(response({ok:true,changed:true}));await stopping;await duplicate;
 const stopped=nodes['coach-messages'].lastElementChild;
 if(stopped.querySelector('.coachMessageText')?.textContent!=='Stopped'||coachBusy)throw new Error('stopped terminal row missing');
 coachResolveTurn({message:'late answer'},turnId);
 if(nodes['coach-messages'].lastElementChild!==stopped||coachMessages.some(row=>row.content==='late answer'))throw new Error('late ask response replaced Stopped');
 const retry=stopped.querySelector('.coachInlineButton');
 fetchQueue.push(response({ok:true,reply:{message:'Retry complete.'}}));retry.onclick();
 await Promise.resolve();await Promise.resolve();await Promise.resolve();
 const askCall=fetchCalls.find(call=>call.url==='/coach/ask');
 if(!askCall)throw new Error('retry did not resubmit');
 const sent=JSON.parse(askCall.options.body);
 if(sent.history.length!==0||coachMessages.filter(row=>row.role==='user'&&row.content==='Try this again').length!==1)throw new Error('retry duplicated the stopped user message in history');
 if(nodes['coach-messages'].children.filter(node=>node.className==='coachMessage user').length!==1)throw new Error('retry appended a duplicate visible user message');
 if(nodes['coach-messages'].children.filter(node=>node.className==='coachActivityMessage').length!==0)throw new Error('successful retry left a transient activity row');
 if(nodes['coach-messages'].children.length!==2||nodes['coach-messages'].lastElementChild.querySelector('.coachMessageText')?.textContent!=='Retry complete.')throw new Error('successful retry did not replace Stopped in the original answer position');
 if(nodes['coach-activity'].textContent)throw new Error('terminal retry left duplicate live status text');
 console.log('retry position ok');
})().catch(error=>{console.error(error);process.exitCode=1;});
""",
            setup="function routedSessionId(){return '';} function setHashRoute(){} function activateCoachGoal(){} function coachPollActivity(){}",
        )
        self.assertEqual(output, "retry position ok")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_stop_requires_confirmed_cancellation_and_keeps_a_racing_answer(self):
        # Break caught: a failed cancel renders a false terminal state and
        # suppresses an answer that completes while cancellation is pending.
        output = self.run_coach_node(
            [
                "coachActionToken", "coachSetLive", "coachAppendMessage", "coachSetBusy", "coachShowStop", "coachClearTurn",
                "coachRenderActivityNote", "coachActivityStageLabel", "coachRetireStaleRetry", "coachPauseVisualTimer", "coachResumeVisualTimer", "coachBeginTurn",
                "coachApplyActivity", "coachResolveTurn", "coachStopTurn",
                "coachRouteTarget", "coachGoalSummary",
            ],
            r"""
(async()=>{
 COACH_STATE={actions:{token:'action-token'}};
 const first=coachBeginTurn('Keep the late answer');coachApplyActivity({stage:'opening_codex',cancellable:true},first);
 fetchQueue.push(response({ok:false,error:'still running'},false));await coachStopTurn();
 if(nodes['coach-messages'].children.some(node=>node.querySelector('.coachMessageText')?.textContent==='Stopped'))throw new Error('failed cancel rendered Stopped');
 if(!coachBusy||coachTurn?.id!==first||nodes['coach-stop'].hidden)throw new Error('failed cancel did not restore the in-flight turn');
 if(!nodes['coach-activity'].textContent.includes('try again'))throw new Error('failed cancel did not give retryable state');
 coachResolveTurn({message:'The answer won the race.'},first);
 if(coachMessages.at(-1).content!=='The answer won the race.'||coachBusy||nodes['coach-send'].disabled)throw new Error('late answer was lost after failed stop');
 const second=coachBeginTurn('Stop successfully');coachApplyActivity({stage:'opening_codex',cancellable:true},second);
 fetchQueue.push(response({ok:true,changed:true}));await coachStopTurn();
 if(nodes['coach-messages'].lastElementChild.querySelector('.coachMessageText')?.textContent!=='Stopped')throw new Error('confirmed cancel did not render Stopped');
 if(coachBusy||nodes['coach-send'].disabled)throw new Error('confirmed cancel did not restore Send');
 console.log('stop confirmation ok');
})().catch(error=>{console.error(error);process.exitCode=1;});
""",
            setup="function coachRouteTarget(){return null;}",
        )
        self.assertEqual(output, "stop confirmation ok")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_stop_keeps_the_turn_when_cancel_reports_unchanged(self):
        # Break caught: an idle/completion-race cancellation response is treated
        # as confirmation and suppresses the answer that is still arriving.
        output = self.run_coach_node(
            [
                "coachActionToken", "coachSetLive", "coachAppendMessage", "coachSetBusy", "coachShowStop", "coachClearTurn",
                "coachRenderActivityNote", "coachActivityStageLabel", "coachRetireStaleRetry", "coachPauseVisualTimer", "coachResumeVisualTimer", "coachBeginTurn",
                "coachApplyActivity", "coachResolveTurn", "coachStopTurn",
                "coachRouteTarget", "coachGoalSummary",
            ],
            r"""
(async()=>{
 COACH_STATE={actions:{token:'action-token'}};
 const turnId=coachBeginTurn('Keep completion race');coachApplyActivity({stage:'opening_codex',cancellable:true},turnId);
 fetchQueue.push(response({ok:true,changed:false}));await coachStopTurn();
 if(nodes['coach-messages'].children.some(node=>node.querySelector('.coachMessageText')?.textContent==='Stopped'))throw new Error('unchanged cancel rendered Stopped');
 if(!coachBusy||coachTurn?.id!==turnId||nodes['coach-stop'].hidden)throw new Error('unchanged cancel did not keep the active turn usable');
 if(!nodes['coach-activity'].textContent.includes('try again'))throw new Error('unchanged cancel did not explain retry');
 coachResolveTurn({message:'Completion won the race.'},turnId);
 if(coachMessages.at(-1).content!=='Completion won the race.'||coachBusy||nodes['coach-send'].disabled)throw new Error('unchanged cancel suppressed the racing answer');
 console.log('unchanged cancellation ok');
})().catch(error=>{console.error(error);process.exitCode=1;});
""",
            setup="function coachRouteTarget(){return null;}",
        )
        self.assertEqual(output, "unchanged cancellation ok")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_goal_sheet_escape_focus_and_open_state_preserve_the_live_turn(self):
        output = self.run_coach_node(
            [
                "coachCurrentRoute", "renderCoachContext", "syncCoachSurfaceMode",
                "setCoachOpen", "setCoachSheet", "coachFormatSnapshot",
                "coachGoalSummary", "coachReadinessNotice", "coachRenderNotice",
                "coachFocusActiveSurface", "renderCoachState", "coachSetLive",
                "coachAppendMessage", "coachSetBusy", "coachShowStop", "coachClearTurn",
                "coachRenderActivityNote", "coachActivityStageLabel", "coachRetireStaleRetry", "coachPauseVisualTimer", "coachResumeVisualTimer", "coachBeginTurn",
                "coachHandleEscape", "coachRouteTarget",
            ],
            r"""
COACH_STATE={};renderCoachState();
if(!nodes['coach-goal-rail'].hidden)throw new Error('goal rail appeared without a goal');
COACH_STATE={goal:{label:'Lower retry rate',runtime:'all',target_percent:20,window_days:30,weekly_enabled:false,review_weekday:0,baseline:{as_of:null}},current:null,progress:null,weekly:{}};
renderCoachState();if(nodes['coach-goal-rail'].hidden)throw new Error('active goal rail stayed hidden');
const turnId=coachBeginTurn('Keep working while closed'),row=coachTurn.activity.row;
setCoachOpen(true);setCoachOpen(false);setCoachOpen(true,{focus:false});
if(coachTurn.id!==turnId||coachTurn.activity.row!==row||row.parentNode!==nodes['coach-messages'])throw new Error('close and reopen lost the live row');
setCoachSheet('goal',true);if(nodes['coach-goal-sheet'].hidden||document.activeElement!==goalButton)throw new Error('goal sheet did not open with focus');
let prevented=0;coachHandleEscape({key:'Escape',preventDefault(){prevented++;}});
if(!nodes['coach-goal-sheet'].hidden||document.activeElement!==nodes['coach-input']||!bodyNode.classList.contains('coachOpen'))throw new Error('Escape did not close the sheet first');
coachHandleEscape({key:'Escape',preventDefault(){prevented++;}});
if(bodyNode.classList.contains('coachOpen')||document.activeElement!==nodes['coach-trigger']||prevented!==2)throw new Error('second Escape did not close Tok and restore focus');
console.log('sheet and open state ok');
""",
            setup="function loadCoachState(){} function coachPollActivity(){} function money(value){return String(value);} function compactNumber(value){return String(value);}",
        )
        self.assertEqual(output, "sheet and open state ok")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_overlay_boundary_and_route_focus_are_executed(self):
        output = self.run_coach_node(
            [
                "coachCurrentRoute", "renderCoachContext", "syncCoachSurfaceMode",
                "coachSetLive", "coachAppendMessage", "coachSetBusy",
                "coachShowStop", "coachClearTurn", "coachRenderActivityNote",
                "coachActivityStageLabel", "coachRetireStaleRetry", "coachPauseVisualTimer", "coachResumeVisualTimer",
                "coachBeginTurn", "coachApplyActivity", "coachRouteTarget",
                "coachGoalSummary",
            ],
            r"""
viewport=1023;syncCoachSurfaceMode(true);if(wrap.hasAttribute('inert'))throw new Error('sub-1024 layout became inert');
viewport=1024;syncCoachSurfaceMode(true);if(!wrap.hasAttribute('inert'))throw new Error('1024 overlay did not become inert');
viewport=1180;syncCoachSurfaceMode(true);if(!wrap.hasAttribute('inert'))throw new Error('1180 overlay did not remain inert');
viewport=1181;syncCoachSurfaceMode(true);if(wrap.hasAttribute('inert'))throw new Error('docked layout stayed inert');
location.hash='#spend';const turnId=coachBeginTurn('Read this page');coachApplyActivity({stage:'reading_token_meter',cancellable:false},turnId);
if(!nodes['view-daily'].classList.contains('coachContextFocus'))throw new Error('current route was not focused');
location.hash='#models';renderCoachContext();
if(nodes['view-daily'].classList.contains('coachContextFocus')||nodes['coach-context-label'].textContent!=='Models')throw new Error('route change did not clear focus');
console.log('surface bounds ok');
""",
            setup="function coachPollActivity(){} function setHashRoute(){} function activateCoachGoal(){}",
        )
        self.assertEqual(output, "surface bounds ok")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_compact_reply_bounds_evidence_and_labels_the_action_locally(self):
        # Break caught: agent text becomes a control label, or a goal draft and
        # an action both compete for the single next step.
        output = self.run_coach_node(
            ["coachRouteTarget", "coachGoalSummary", "coachAppendMessage"],
            r"""
const evidence=[1,2,3,4].map(value=>({label:`Signal ${value}`,value:String(value)}));
coachAppendMessage('assistant','Bounded answer',{evidence,action:{kind:'review_skill_packs',subject:'skill-ops@skills-marketplace'}});
const answer=nodes['coach-messages'].lastElementChild;
if(answer.querySelectorAll('details').length!==1||answer.querySelectorAll('.coachEvidenceRow').length!==3)throw new Error('evidence was not one collapsed line capped at three');
if(answer.querySelectorAll('button').length!==1)throw new Error('answer exposed more than one action');
const control=answer.querySelector('.coachAction');
if(control.querySelector('b').textContent!=='Open skill packs')throw new Error('action label was not generated locally');
if(!control.className.includes('direct'))throw new Error('a Token Meter control was not marked direct');
if(control.querySelector('span').textContent!=='skill-ops@skills-marketplace')throw new Error('named subject was dropped');
coachAppendMessage('assistant','Unknown code',{evidence,action:{kind:'delete_everything',subject:'x'}});
if(nodes['coach-messages'].lastElementChild.querySelector('.coachAction'))throw new Error('an unknown action code produced a control');
coachAppendMessage('assistant','Draft answer',{evidence,action:{kind:'compare_models',subject:null},goal_draft:{label:'Lower retries',runtime:'all',target_percent:10,window_days:14,weekly_enabled:false,review_weekday:0}});
const draft=nodes['coach-messages'].lastElementChild;
if(draft.querySelectorAll('button').length!==1||draft.querySelector('.coachAction'))throw new Error('goal draft did not suppress the action');
console.log('reply bounds ok');
""",
            setup="function setHashRoute(){} function activateCoachGoal(){}",
        )
        self.assertEqual(output, "reply bounds ok")

    def test_only_levers_token_meter_operates_are_marked_direct(self):
        # Break caught: a view-only action is styled as a control, promising a
        # button that does not exist on that page.
        block = self.page.split("const COACH_ACTIONS={", 1)[1].split("\n};", 1)[0]
        direct = {
            name for name, body in re.findall(r"^\s*([a-z_]+):\{(.*)\},?$", block, re.MULTILINE)
            if "direct:true" in body
        }
        self.assertEqual(direct, {"review_skill_packs", "set_monthly_budget"})
        for view in ("narrow_tool_output", "compare_models", "reduce_context", "reduce_reasoning"):
            self.assertNotIn("direct", block.split(f"{view}:{{", 1)[1].split("}", 1)[0])
        self.assertIn(".coachAction:not(.direct)", self.page)

    def test_skill_ranks_levers_by_whether_the_user_can_change_them(self):
        skill = Path(
            meter._SOURCE_ROOT,
            "token_meter/coach/workspace/.agents/skills/token-meter-coach/SKILL.md",
        ).read_text(encoding="utf-8")
        normalized = " ".join(skill.split())
        for marker in (
            "Rank by whether the user can actually change it",
            "**Token Meter changes it.**",
            "**The user changes their own setup.**",
            "**Token Meter can only show it.**",
            "A flagged tool with `actionable` false is never a valid recommendation.",
        ):
            self.assertIn(" ".join(marker.split()), normalized)

    def test_every_action_code_maps_to_a_real_route_and_local_label(self):
        from token_meter.coach.codex import ACTION_KINDS

        block = self.page.split("const COACH_ACTIONS={", 1)[1].split("\n};", 1)[0]
        self.assertEqual(set(re.findall(r"^\s*([a-z_]+):", block, re.MULTILINE)), set(ACTION_KINDS))
        for route in re.findall(r"route:'([a-z-]+)'", block):
            self.assertIn(f"'{route}'", self.page.split("function coachRouteTarget", 1)[1][:400])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_visibility_pauses_only_visual_time_and_reduced_motion_is_applied(self):
        output = self.run_coach_node(
            [
                "coachSetLive", "coachAppendMessage", "coachSetBusy",
                "coachShowStop", "coachClearTurn", "coachRenderActivityNote",
                "coachActivityStageLabel", "coachRetireStaleRetry", "coachPauseVisualTimer", "coachResumeVisualTimer",
                "coachBeginTurn", "coachHandleVisibilityChange",
                "syncCoachMotionPreference", "coachRouteTarget", "coachGoalSummary",
            ],
            r"""
const turnId=coachBeginTurn('Stay alive'),row=coachTurn.activity.row,started=coachTurn.activity.started,poll=coachPollTimer,visual=coachVisualTimer;
document.hidden=true;coachHandleVisibilityChange();
if(coachVisualTimer!==0||intervals.has(visual)||coachPollTimer!==poll||!intervals.has(poll))throw new Error('hidden state changed more than the visual timer');
if(coachTurn.id!==turnId||coachTurn.activity.row!==row||coachTurn.activity.started!==started)throw new Error('hidden state replaced the active turn');
advance(5000);document.hidden=false;coachHandleVisibilityChange();
if(!coachVisualTimer||coachTurn.id!==turnId||coachTurn.activity.timer.textContent!=='5s')throw new Error('visible state did not resume the same elapsed turn');
syncCoachMotionPreference({matches:true});if(!bodyNode.classList.contains('coachReducedMotion'))throw new Error('reduced motion was not applied');
syncCoachMotionPreference({matches:false});if(bodyNode.classList.contains('coachReducedMotion'))throw new Error('motion preference did not restore');
console.log('visibility and motion ok');
""",
            setup="function coachPollActivity(){} function setHashRoute(){} function activateCoachGoal(){}",
        )
        self.assertEqual(output, "visibility and motion ok")

    def test_chat_content_is_ephemeral_and_rendered_as_text(self):
        self.assertIn("let coachMessages=[]", self.page)
        self.assertIn("message.textContent=content", self.page)
        self.assertIn("label.textContent=row.label", self.page)
        self.assertNotIn("tm_coach_messages", self.page)
        self.assertNotIn("sessionStorage", self.page)

    def test_keyboard_behavior_focuses_the_visible_surface(self):
        self.assertIn("event.key==='Enter'&&!event.shiftKey", self.page)
        self.assertIn("setCoachSheet('goal',true)", self.page)
        self.assertIn("setCoachSheet('chat',true)", self.page)
        self.assertIn("$('coach-trigger').focus()", self.page)
        self.assertIn("document.addEventListener('keydown',coachHandleEscape)", self.page)

    def test_live_turn_contract_is_truthful_and_stale_safe(self):
        for marker in (
            'function coachBeginTurn(content,retryRow=null,history=[])', 'function coachApplyActivity(activity,turnId)',
            'function coachResolveTurn(reply,turnId)', 'function coachStopTurn()',
            "const COACH_ACTIVITY_COPY={", "opening_codex:'Opening Codex'",
            "reading_token_meter:'Reading Token Meter'", "checking_evidence:'Checking the evidence'",
            "fetch('/coach/cancel'", "setTimeout(()=>", "8000",
            "timer.setAttribute('aria-hidden','true')", "coachMessages.push({role:'user',content})",
            "if(turnId!==coachTurn?.id||coachTurn?.stopped)return;",
            "document.querySelectorAll('.coachContextFocus')",
        ):
            self.assertIn(marker, self.page)
        self.assertNotIn("$('coach-input').disabled=coachBusy", self.page)

    def test_live_status_is_screen_reader_only_not_a_second_visible_lifecycle_label(self):
        self.assertIn('id=coach-activity role=status aria-live=polite', self.page)
        self.assertIn('.coachLive{position:absolute;width:1px;height:1px;', self.page)

    def test_panel_body_fills_the_panel_so_the_composer_stays_at_the_bottom(self):
        # Break caught: a leftover tab row in the grid template leaves the body
        # in an auto row and strands the composer in the middle of the panel.
        self.assertIn("grid-template-rows:auto minmax(0,1fr);color:var(--fg)}", self.page)
        self.assertNotIn("grid-template-rows:auto auto minmax(0,1fr)", self.page)
        self.assertNotIn(".coachTabs", self.page)
        self.assertIn(".coachMessages{display:grid;gap:10px;margin-top:auto}", self.page)

    def test_goal_sheet_is_one_scroll_with_a_visible_way_back(self):
        self.assertIn("class=coachBack type=button data-coach-go-chat", self.page)
        self.assertIn("$('coach-view-chat').classList.toggle('on',!showGoal)", self.page)
        self.assertNotIn("id=coach-view-weekly", self.page)
        self.assertNotIn("coachGoalWeekly", self.page)

    def test_clearing_a_goal_confirms_in_the_dashboard_dialog_not_a_native_prompt(self):
        self.assertIn("id=coach-clear-dialog", self.page)
        self.assertIn("function confirmCoachGoalClear()", self.page)
        self.assertIn("await confirmCoachGoalClear()", self.page)
        self.assertNotIn("confirm('Clear this goal", self.page)

    def test_live_row_carries_an_indeterminate_signal_with_a_static_fallback(self):
        for marker in (
            "orbit.className='coachActivityOrbit'",
            "orbit.setAttribute('aria-hidden','true')",
            "@keyframes coachSpin",
            ".coachReducedMotion .coachActivityOrbit{animation:none",
            "const COACH_TOOL_COPY={",
            "usage:'Reading your usage history'",
            "const COACH_PATIENCE_MS=25000",
            "Token Meter ${turn.activity.reads===1?'reading':'readings'} complete",
            "function coachRenderActivityNote(turn)",
            "Tok is still working.",
        ):
            self.assertIn(marker, self.page)

    def test_stop_replaces_send_in_place_instead_of_stacking_a_second_control(self):
        self.assertIn("function coachShowStop(show)", self.page)
        self.assertIn("$('coach-stop').hidden=!show;$('coach-send').hidden=Boolean(show)", self.page)
        self.assertIn("id=coach-stop type=button hidden aria-label=\"Stop Tok\"", self.page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_failures_and_missing_codex_reach_a_visible_notice_not_only_the_live_region(self):
        # Break caught: every Coach error is announced into the clipped live
        # region, so a sighted user sees no failure and no way to recover.
        output = self.run_coach_node(
            [
                "coachSetLive", "coachReadinessNotice", "coachRenderNotice",
                "coachStatus", "coachFormatSnapshot", "coachFormatMoment",
                "coachGoalSummary", "renderCoachState", "coachRouteTarget",
            ],
            r"""
COACH_STATE={agent:{available:false,status:'cli_missing'},goal:null,weekly:{}};renderCoachState();
if(nodes['coach-notice'].hidden||!nodes['coach-notice-text'].textContent.includes('Codex CLI'))throw new Error('missing Codex was never surfaced visibly');
if(!nodes['coach-notice'].className.includes('warn'))throw new Error('readiness notice lost its tone');
if(nodes['coach-trigger-dot'].hidden||!nodes['coach-trigger-dot'].classList.contains('warn'))throw new Error('closed trigger gave no attention signal');
let retried=0;coachStatus('Goal could not be saved.',{tone:'bad',action:{label:'Try again',onClick(){retried++;}}});
if(nodes['coach-notice'].hidden||!nodes['coach-notice'].className.includes('bad'))throw new Error('a failure did not become a visible notice');
if(nodes['coach-notice-action'].hidden)throw new Error('a recoverable failure offered no action');
nodes['coach-notice-action'].onclick();if(retried!==1)throw new Error('notice action did not run');
if(nodes['coach-activity'].textContent!=='Goal could not be saved.')throw new Error('failure was not announced');
coachStatus('');
if(!nodes['coach-notice-action'].hidden||nodes['coach-notice'].hidden)throw new Error('clearing a failure did not fall back to readiness');
COACH_STATE={agent:{available:true,status:'ready'},goal:null,weekly:{}};renderCoachState();
if(!nodes['coach-notice'].hidden||!nodes['coach-trigger-dot'].hidden)throw new Error('a healthy Tok still showed status furniture');
COACH_STATE={agent:{available:true,status:'ready'},goal:null,weekly:{last_error:'timeout'}};renderCoachState();
if(nodes['coach-trigger-dot'].hidden)throw new Error('a failed weekly review raised no attention dot');
console.log('notice surface ok');
""",
            setup="function money(v){return String(v);} function compactNumber(v){return String(v);}",
        )
        self.assertEqual(output, "notice surface ok")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_completed_weekly_review_enters_the_conversation_once(self):
        output = self.run_coach_node(
            ["coachRouteTarget", "coachGoalSummary", "coachAppendMessage",
             "coachFormatSnapshot", "announceCoachWeekly"],
            r"""
COACH_STATE={goal:{label:'Lower cost per execution'},weekly:{recommendation:'reduce_retries',snapshot:{metric:'cost_per_execution',value:0.4,unit:'USD/execution',coverage:'complete'}}};
announceCoachWeekly();
const said=nodes['coach-messages'].lastElementChild;
if(!said.querySelector('.coachMessageText').textContent.includes('Reduce retries before optimizing anything else'))throw new Error('weekly recommendation never reached the conversation');
if(coachMessages.at(-1)?.role!=='assistant')throw new Error('weekly message was not kept in conversation memory');
if(said.querySelectorAll('.coachEvidenceRow').length!==1)throw new Error('weekly message dropped its measured evidence');
COACH_STATE={goal:{label:'Lower cost per execution'},weekly:{}};
const before=nodes['coach-messages'].children.length;announceCoachWeekly();
if(nodes['coach-messages'].children.length!==before)throw new Error('an empty review still spoke');
console.log('weekly narration ok');
""",
            setup="function setHashRoute(){} function activateCoachGoal(){} function money(v){return '$'+String(v);} function compactNumber(v){return String(v);}",
        )
        self.assertEqual(output, "weekly narration ok")

    def test_activity_tool_copy_matches_the_shared_mcp_allowlist(self):
        # Break caught: a stage-copy table drifts from the tool allowlist and
        # silently degrades stages to the generic label.
        from token_meter.coach.codex import MCP_TOOLS
        from token_meter.coach.service import _ACTIVITY_TOOLS

        self.assertEqual(_ACTIVITY_TOOLS, frozenset(MCP_TOOLS))
        block = self.page.split("const COACH_TOOL_COPY={", 1)[1].split("};", 1)[0]
        copy_keys = set(re.findall(r"^\s*([a-z_]+):", block, re.MULTILINE))
        self.assertEqual(copy_keys, set(MCP_TOOLS))

    def test_elapsed_note_states_only_time_and_observed_readings(self):
        # Break caught: an elapsed-time line invents a cause such as "deeper
        # analysis", which the design forbids because nothing observed it.
        self.assertNotIn("Deeper analysis", self.page)
        self.assertNotIn("signals read", self.page)
        for claim in ("takes longer", "almost done", "should finish", "usually"):
            self.assertNotIn(claim, self.page.split("coachRenderActivityNote", 1)[1][:600])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_patience_note_appears_only_after_the_threshold_without_a_boundary(self):
        output = self.run_coach_node(
            [
                "coachSetLive", "coachAppendMessage", "coachSetBusy", "coachShowStop",
                "coachClearTurn", "coachRenderActivityNote", "coachActivityStageLabel",
                "coachRetireStaleRetry", "coachPauseVisualTimer", "coachResumeVisualTimer",
                "coachBeginTurn", "coachRouteTarget", "coachGoalSummary",
            ],
            r"""
const turnId=coachBeginTurn('Wait for the threshold');const note=coachTurn.activity.note;
advance(24999);coachRenderActivityNote(coachTurn);
if(!note.hidden)throw new Error('elapsed note appeared before the threshold');
advance(1);coachRenderActivityNote(coachTurn);
if(note.hidden||note.textContent!=='Tok is still working.')throw new Error('threshold note wrong: '+note.textContent);
coachTurn.activity.reads=2;coachRenderActivityNote(coachTurn);
if(note.textContent!=='2 Token Meter readings complete \u00b7 Tok is still working.')throw new Error('reading count wrong: '+note.textContent);
if(nodes['coach-activity'].textContent==='Tok is still working.')throw new Error('elapsed note was announced as a stage change');
console.log('patience note ok');
""",
            setup="function coachPollActivity(){} function setHashRoute(){} function activateCoachGoal(){}",
        )
        self.assertEqual(output, "patience note ok")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_a_failure_notice_clears_once_fresh_state_disproves_it(self):
        # Break caught: a bad notice outlives its failure and permanently hides
        # the missing-Codex prerequisite.
        output = self.run_coach_node(
            [
                "coachErrorMessage", "coachSetLive", "coachReadinessNotice",
                "coachRenderNotice", "coachStatus", "coachFormatSnapshot",
                "coachFormatMoment", "coachGoalSummary", "renderCoachState",
                "coachAnnounceNewWeekly", "loadCoachState", "coachRouteTarget",
            ],
            r"""
(async()=>{
 fetchQueue.push(response({ok:false,error:'Tok is unavailable.'},false));
 if(await loadCoachState())throw new Error('a failed load reported success');
 if(nodes['coach-notice'].hidden||!nodes['coach-notice'].className.includes('bad'))throw new Error('failure was not visible');
 fetchQueue.push(response({ok:true,agent:{available:true,status:'ready'},goal:null,weekly:{}}));
 if(!await loadCoachState())throw new Error('a healthy load reported failure');
 if(!nodes['coach-notice'].hidden)throw new Error('stale failure survived fresh evidence: '+nodes['coach-notice-text'].textContent);
 fetchQueue.push(response({ok:false,error:'Tok is unavailable.'},false));await loadCoachState();
 fetchQueue.push(response({ok:true,agent:{available:false,status:'cli_missing'},goal:null,weekly:{}}));await loadCoachState();
 if(!nodes['coach-notice-text'].textContent.includes('Codex CLI'))throw new Error('a stale failure suppressed the readiness notice');
 console.log('notice recovery ok');
})().catch(error=>{console.error(error);process.exitCode=1;});
""",
            setup="function money(v){return String(v);} function compactNumber(v){return String(v);} function announceCoachWeekly(){} function runCoachWeekly(){}",
        )
        self.assertEqual(output, "notice recovery ok")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_retry_survives_later_conversation_and_retires_when_superseded(self):
        # Break caught: a weekly narration or any later turn silently kills an
        # enabled Retry button.
        output = self.run_coach_node(
            [
                "coachRequestHistory", "coachCurrentRoute", "coachPageContext",
                "coachActionToken", "coachErrorMessage", "coachSetLive",
                "coachAppendMessage", "coachSetBusy", "coachShowStop", "coachClearTurn",
                "coachRenderActivityNote", "coachActivityStageLabel",
                "coachRetireStaleRetry", "coachPauseVisualTimer",
                "coachResumeVisualTimer", "coachBeginTurn", "coachResolveTurn",
                "coachFailTurn", "coachRetryTurn", "sendCoachMessage",
                "coachRouteTarget", "coachGoalSummary",
            ],
            r"""
(async()=>{
 COACH_STATE={actions:{token:'action-token'}};
 nodes['coach-input'].value='Ask once';
 fetchQueue.push(()=>Promise.reject(new Error('Tok could not reach the local service.')));
 await sendCoachMessage();
 const failed=nodes['coach-messages'].lastElementChild,retry=failed.querySelector('.coachInlineButton');
 coachMessages.push({role:'assistant',content:'Weekly review for Lower cost per execution: reduce retries.'});
 fetchQueue.push(response({ok:true,reply:{message:'Recovered answer.'}}));
 retry.onclick();await Promise.resolve();await Promise.resolve();await Promise.resolve();
 const ask=fetchCalls.find(call=>call.url==='/coach/ask');
 if(!ask)throw new Error('retry did nothing after a weekly narration');
 if(JSON.parse(ask.options.body).history.length!==0)throw new Error('retry replayed a polluted history');
 if(coachMessages.filter(row=>row.content==='Ask once').length!==1)throw new Error('retry duplicated the user message');
 nodes['coach-input'].value='A later question';
 fetchQueue.push(()=>Promise.reject(new Error('Tok could not reach the local service.')));
 await sendCoachMessage();
 const stale=nodes['coach-messages'].children.find(node=>node!==nodes['coach-messages'].lastElementChild&&node.className==='coachMessage error')?.querySelector('.coachInlineButton');
 if(stale&&!stale.disabled)throw new Error('a superseded Retry stayed enabled while doing nothing');
 console.log('retry lifecycle ok');
})().catch(error=>{console.error(error);process.exitCode=1;});
""",
            setup="function routedSessionId(){return '';} function setHashRoute(){} function activateCoachGoal(){} function coachPollActivity(){} function loadCoachState(){} function coachStatus(){}",
        )
        self.assertEqual(output, "retry lifecycle ok")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_reopening_with_the_goal_sheet_open_focuses_the_visible_surface(self):
        # Break caught: focus() targets the hidden composer, stranding keyboard
        # focus outside the panel.
        output = self.run_coach_node(
            [
                "coachCurrentRoute", "renderCoachContext", "syncCoachSurfaceMode",
                "coachFocusActiveSurface", "setCoachOpen", "setCoachSheet",
                "coachRouteTarget",
            ],
            r"""
setCoachOpen(true);if(document.activeElement!==nodes['coach-input'])throw new Error('chat open did not focus the composer');
setCoachSheet('goal',true);setCoachOpen(false);setCoachOpen(true);
if(document.activeElement===nodes['coach-trigger'])throw new Error('reopening with the sheet open left focus on the trigger');
if(document.activeElement!==goalButton)throw new Error('reopening did not focus the visible sheet');
setCoachSheet('chat',true);if(document.activeElement!==nodes['coach-input'])throw new Error('returning to chat did not focus the composer');
console.log('focus surface ok');
""",
            setup="function loadCoachState(){} function renderCoachState(){}",
        )
        self.assertEqual(output, "focus surface ok")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_an_unconfirmed_cancel_cannot_resurrect_a_turn_that_already_failed(self):
        # Break caught: a 90s timeout races a Stop press, the failed turn is
        # marked busy again with no timers left, and the composer wedges until
        # the page is reloaded.
        output = self.run_coach_node(
            [
                "coachActionToken", "coachSetLive", "coachAppendMessage", "coachSetBusy",
                "coachShowStop", "coachClearTurn", "coachRenderActivityNote",
                "coachActivityStageLabel", "coachRetireStaleRetry",
                "coachPauseVisualTimer", "coachResumeVisualTimer", "coachBeginTurn",
                "coachApplyActivity", "coachResolveTurn", "coachFailTurn",
                "coachStopTurn", "coachRetryTurn", "coachRouteTarget", "coachGoalSummary",
            ],
            r"""
(async()=>{
 COACH_STATE={actions:{token:'action-token'}};
 const turnId=coachBeginTurn('Race the timeout');coachApplyActivity({stage:'opening_codex',cancellable:true},turnId);
 advance(8000);
 const cancel=deferred();fetchQueue.push(()=>cancel.promise);
 const stopping=coachStopTurn();
 coachFailTurn('Tok did not finish in time.',turnId);
 cancel.resolve(response({ok:true,changed:false}));await stopping;
 if(coachBusy)throw new Error('an unconfirmed cancel re-armed busy on an already-failed turn');
 if(nodes['coach-send'].hidden||nodes['coach-send'].disabled)throw new Error('the composer stayed unusable');
 if(!nodes['coach-stop'].hidden)throw new Error('a dead Stop control was left visible');
 const failed=nodes['coach-messages'].lastElementChild;
 if(failed.className!=='coachMessage error')throw new Error('the failure row was overwritten');
 if(intervals.size||timeouts.size)throw new Error('turn timers survived the terminal state');
 console.log('cancel race ok');
})().catch(error=>{console.error(error);process.exitCode=1;});
""",
            setup="function routedSessionId(){return '';} function setHashRoute(){} function activateCoachGoal(){} function coachPollActivity(){} function loadCoachState(){} function coachStatus(){} function sendCoachMessage(){}",
        )
        self.assertEqual(output, "cancel race ok")

    def test_goal_and_weekly_controls_use_protected_local_routes(self):
        for marker in (
            "fetch('/coach/state'",
            "fetch('/coach/ask'",
            "fetch('/coach/goal'",
            "fetch('/coach/weekly'",
            "'X-Token-Meter-Action':coachActionToken()",
            "function activateCoachGoal",
            "function toggleCoachWeekly",
            "function clearCoachGoal",
            "function runCoachWeekly",
            "const COACH_WEEKDAYS=['Mon','Tue','Wed','Thu','Fri','Sat','Sun']",
            "Review ${COACH_WEEKDAYS[goal.review_weekday]}",
        ):
            self.assertIn(marker, self.page)


if __name__ == "__main__":
    unittest.main()
