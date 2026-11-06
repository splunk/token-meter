import unittest
import shutil
import time
from tests.dashboard_layout_support import DashboardLayoutCase


class DashboardTourLayoutTests(DashboardLayoutCase):
    def test_leaving_the_tour_through_a_link_offers_a_way_back(self):
        self.assertIn('<button class="deckPrimary tourReturn" id=tour-return type=button hidden>', self.page)
        self.assertIn("@media(prefers-reduced-motion:reduce){.tourReturn,.tourReturnArrow{animation:none}}", self.page)
        self.assertIn("@keyframes tourReturnIn{from{transform:translateY(16px) scale(.94)}to{transform:none}}", self.page, "never starts invisible")
        self.assertIn("const TOUR_RETURN_KEY='tm_tour_return';", self.page)
        self.assertIn("$('tour-return').onclick=returnToTour;\nrestoreTourReturn();", self.page)
        self.assertIn("value.host==='learn'?'Back to Learn':'Back to the tour'", self.page)
        self.assertIn("const from={index:deckHosts.learn.index,hash:location.hash,host:'learn'};", self.page)
        self.assertIn("if(tourReturn?.host==='learn'&&(h==='learn'||h==='learn-agent-access'))showTourReturn(null);", self.page)
        self.assertIn("if(route){openDeckRoute(route,target);if(from)showTourReturn(from);}", self.page)
        self.assertIn("$('tour-return').onclick=returnToTour;", self.page)
        self.assertIn("window.addEventListener('popstate',()=>{if(tourReturn&&location.hash===tourReturn.hash)returnToTour();});", self.page)
        self.assertIn(" showTourReturn(null);\n const start=welcomeStartSlide(index);", self.page)

    def test_sessions_all_spend_learn_and_settings_are_first_class_routes(self):
        for marker in (
            "id=session-scope-tabs", "id=session-scope-current", "id=session-scope-all",
            "id=tab-subagents", "id=view-subagents", "id=all-session-history",
            "id=subagent-explorer", "id=tab-daily", "id=view-daily",
            "id=tab-learn", "id=view-learn", "id=tab-settings", "id=view-settings",
        ):
            self.assertIn(marker, self.page)
        for removed in (
            "id=tab-global", "id=view-global", "data-global-panel",
            "id=tab-budgets", "id=view-budgets", "id=tab-logs", "id=view-logs",
        ):
            self.assertNotIn(removed, self.page)
        self.assertIn("const legacyGlobal=h==='global'||h==='global-logs'", self.page)
        self.assertIn("if(h==='logs'||legacyGlobal)", self.page)
        self.assertIn("'/#sessions-all'", self.page)
        self.assertIn("function openSessionScope(scope", self.page)
        self.assertIn("function setSessionScope(scope)", self.page)
        self.assertIn('class="tabs sessionScopeTabs"', self.page)
        self.assertIn('<span class=tabLabel>Current sessions</span>', self.page)
        self.assertIn('<span class=tabLabel>All sessions</span>', self.page)
        self.assertIn('<span class=tabLabel>Subagents</span>', self.page)
        self.assertIn("h==='sessions-subagents'", self.page)
        self.assertIn("live · updated ${new Date(generatedAt*1000).toLocaleTimeString", self.page)
        self.assertLess(self.page.index("id=tab-session"), self.page.index("id=tab-daily"))
        self.assertIn("id=s-range", self.page)
        self.assertNotIn("id=learn-glossary", self.page)
        self.assertIn("Compare: change one thing, run again.", self.page)
        self.assertIn("if(h==='daily')setHashRoute('spend',{replace:true,apply:false})", self.page)
        self.assertIn("if(h==='spend'||h==='daily'||h==='spend-budgets'||h==='spend-discounts'||h==='settings-budgets'||h==='budgets')", self.page)
        self.assertIn("if(h==='learn'||h==='learn-agent-access')", self.page)
        self.assertIn("if(h==='settings-budgets'||h==='budgets')setHashRoute('spend-budgets',{replace:true,apply:false})", self.page)
        self.assertNotIn("setHashRoute('settings-budgets'", self.page)
        self.assertIn("activeTop.scrollIntoView({block:'nearest',inline:'center'})", self.page)

    def test_read_action_opens_tokenomics_before_learn_without_becoming_a_route(self):
        secondary = self.page.split("<div class=navSecondary>", 1)[1].split(
            "</div>", 1
        )[0]
        self.assertIn("id=tab-read", secondary)
        self.assertLess(secondary.index("id=tab-read"), secondary.index("id=tab-learn"))
        self.assertIn(
            "$('tab-read').onclick=()=>window.open(TOKENOMICS_SEARCH_URL,'_blank','noopener')",
            self.page,
        )
        commands = self.page.split("const NAV_COMMANDS=[", 1)[1].split("];", 1)[0]
        self.assertNotIn("id:'read'", commands)
        self.assertNotIn("route:'read'", self.page)

    def test_onboarding_checklist_and_lesson_dialog_are_retired(self):
        current = self.page.split('<div class="view on" id=view-session>', 1)[1].split(
            '<div class=view id=view-models>', 1
        )[0]
        for retired in (
            "id=onboarding-card", "id=onboarding-toggle", "id=onboarding-progress",
            "id=onboarding-next", "id=onboarding-checklist", "id=onboarding-dialog",
            "id=learn-onboarding-status", "id=learn-onboarding-action",
            "First five minutes", "Closing this lesson marks the step complete",
        ):
            self.assertNotIn(retired, self.page)
        self.assertNotIn(">Collapse<", current)
        for retired in (
            "ONBOARDING_STEPS", "function renderOnboarding(", "function commitOnboardingSteps(",
            "function openOnboardingLesson(", "function resumeOrRestartOnboarding(",
            "onboardingCard", "onboardingDialog", "learnOnboarding", "action:'onboarding'",
        ):
            self.assertNotIn(retired, self.page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_tour_registry_has_twelve_page_named_slides_in_four_chapters(self):
        payload = self._run_tour("""
process.stdout.write(JSON.stringify({
 chapters:DECK_CHAPTERS.map(c=>c.title),
 slides:DECK_SLIDES.map(s=>[s.chapter,s.id]),
 complete:DECK_SLIDES.every(s=>['rail','kicker','title'].every(k=>typeof s[k]==='string'&&s[k].trim())&&(s.body===undefined||typeof s.body==='string'&&s.body.trim())&&(!!s.frame!==(typeof s.visual==='function'))),
 frames:DECK_SLIDES.filter(s=>s.frame).every(s=>typeof s.frame.html==='function'&&s.frame.html.length===0&&s.frame.label&&s.frame.alt&&Array.isArray(s.frame.legend)),
 time:[deckTimeLeft(0),deckTimeLeft(6),deckTimeLeft(10),deckTimeLeft(11)],
}));
""")
        self.assertEqual(payload["chapters"], ["The basics", "While you work", "Review and find waste", "Do better"])
        self.assertEqual(payload["slides"], [
            ["basics", "tokens"], ["basics", "prices"],
            ["work", "context"], ["work", "glance"], ["work", "compare"],
            ["review", "month"], ["review", "models"], ["review", "efficiency"], ["review", "tools"], ["review", "helpers"],
            ["better", "agent"], ["better", "start"],
        ])
        self.assertTrue(payload["complete"], "each slide has copy and exactly one visual: a frame or a list")
        self.assertTrue(payload["frames"], "frame markup takes no arguments, so no live value can reach it")
        self.assertEqual(payload["time"], ["about 4 min", "about 2 min left", "under a minute left", "under a minute left"])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_tour_copy_stays_within_slide_word_budgets(self):
        # Less text, more explaining: the frame and its numbered legend carry the idea. Node reports
        # no known platform here, so the longest companion wording is counted.
        payload = self._run_tour("""
const words=text=>String(text||'').split(/\\s+/).filter(word=>/[A-Za-z0-9$]/.test(word)).length;
const sentences=text=>String(text||'').split(/[.!?](?:\\s|$)/).filter(part=>part.trim()).length;
process.stdout.write(JSON.stringify(DECK_SLIDES.map(s=>({id:s.id,title:words(s.title),body:words(s.body),
 bodySentences:sentences(s.body),legend:(s.frame?.legend||[]).map(words),links:(s.links||[]).length,frame:!!s.frame}))));
""")
        self.assertEqual(len(payload), 12)
        for row in payload:
            with self.subTest(slide=row["id"]):
                self.assertLessEqual(row["title"], 8, "headline")
                self.assertLessEqual(row["body"], 14, "body")
                self.assertLessEqual(row["bodySentences"], 1, "body is one sentence or none")
                self.assertLessEqual(row["links"], 1, "at most one action link")
                if row["frame"]:
                    self.assertGreaterEqual(len(row["legend"]), 1)
                    self.assertLessEqual(len(row["legend"]), 3, "at most three legend lines")
                    for count in row["legend"]:
                        self.assertLessEqual(count, 12, "legend line")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_tour_frames_are_labeled_examples_with_matching_callouts(self):
        payload = self._run_tour("""
process.stdout.write(JSON.stringify(frameSlides.map(({slide,index})=>{
 const html=deckVisualHtml(index,'learn');
 const stage=html.split('<div class=conceptFrameStage role=img aria-label="')[1]?.split('<figcaption>')[0]||'';
 const legend=html.split('<figcaption>')[1]||'';
 const markers=[...stage.matchAll(/<mark class=frameCallout(?: data-at=\\w+)? aria-hidden=true>(\\d)<\\/mark>/g)].map(m=>Number(m[1]));
 const keys=[...legend.matchAll(/<li><mark class=frameCallout aria-hidden=true>(\\d)<\\/mark><span>/g)].map(m=>Number(m[1]));
 return {id:slide.id,figure:html.startsWith('<figure class=conceptFrame')&&html.endsWith('</figure>'),
  example:(html.match(/<span class=chip>Example<\\/span>/g)||[]).length,
  head:html.includes('<div class=conceptFrameHead><span>'),alt:/^[^"<>]{12,}"/.test(stage),
  markers:markers.sort(),keys,orderedList:legend.startsWith('<ol class=frameLegend role=list>'),
  allMarkersHidden:(html.match(/class=frameCallout/g)||[]).length===(html.match(/class=frameCallout(?: data-at=\\w+)? aria-hidden=true>/g)||[]).length};
})));
""")
        self.assertEqual(len(payload), 11)
        for row in payload:
            with self.subTest(frame=row["id"]):
                self.assertTrue(row["figure"])
                self.assertEqual(row["example"], 1, "one Example chip per frame, so nobody mistakes it for their numbers")
                self.assertTrue(row["head"])
                self.assertTrue(row["alt"], "the frame is one described image for screen readers")
                self.assertTrue(row["orderedList"], "the legend is an ordered list")
                self.assertTrue(row["allMarkersHidden"], "markers are visual; the legend carries the words")
                count = len(row["keys"])
                self.assertGreaterEqual(count, 1)
                self.assertLessEqual(count, 3)
                self.assertEqual(row["keys"], list(range(1, count + 1)))
                self.assertEqual(row["markers"], list(range(1, count + 1)), "each legend number appears once on the frame")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_tour_frames_never_show_live_numbers_or_talk_comparisons(self):
        payload = self._run_tour("""
const facts=[deckFacts({}),deckFacts(richState),deckFacts(null),deckFacts({xsession:{budget:{configured:true,budget:0}}})];
const all=DECK_SLIDES.map((slide,i)=>facts.map(state=>deckCopyHtml(i,state,'learn')+deckVisualHtml(i,'learn')).join('')).join('');
process.stdout.write(JSON.stringify({
 facts,
 visualsStable:DECK_SLIDES.every((slide,i)=>deckVisualHtml(i,'learn')===deckVisualHtml(i,'learn')),
 copyStable:DECK_SLIDES.map((slide,i)=>new Set(facts.map(state=>deckCopyHtml(i,state,'learn'))).size),
 personal:['2,179','2179','1,792','1792','8,940','8940','83,907','83907','38.59','claude-opus-5-5','gpt-5.6-sol'].filter(value=>all.includes(value)),
 talk:['Yours','From the talk','talkChip','yoursChip','engineer’s laptop','$31.14','6% → 55%','83,907','5.1×'].filter(value=>all.includes(value)),
}));
""")
        self.assertEqual(payload["facts"], [
            {"budgetConfigured": False}, {"budgetConfigured": True},
            {"budgetConfigured": False}, {"budgetConfigured": False},
        ])
        self.assertTrue(payload["visualsStable"])
        self.assertEqual(payload["copyStable"], [1] * 11 + [2], "only the last slide's call to action reads live state")
        self.assertEqual(payload["personal"], [], "frames carry sample values, never the user's numbers")
        self.assertEqual(payload["talk"], [], "the deck explains concepts; no Yours or From the talk comparisons")
        for retired in (
            "learnEvidence", "learnOutputTrend", "figureHtml", "evidenceFigure", "TALK_CHIP", "YOURS_CHIP",
            "TALK_NOTE", "TALK_OUTPUT_MONTHS", "talkChip", "yoursChip", "leverValue", "miniPreview",
            "class=takeaway", "WELCOME_HOST", "deckPreviewHtml",
        ):
            self.assertNotIn(retired, self.page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_both_tour_hosts_render_the_same_slides(self):
        payload = self._run_tour("""
const facts=[deckFacts({}),deckFacts(richState)];
const same=DECK_SLIDES.map((slide,i)=>facts.every(state=>
 deckCopyHtml(i,state,'welcome').replaceAll('welcome-','HOST-')===deckCopyHtml(i,state,'learn').replaceAll('learn-','HOST-')&&
 deckVisualHtml(i,'welcome').replaceAll('welcome-','HOST-')===deckVisualHtml(i,'learn').replaceAll('learn-','HOST-')));
const agent=DECK_SLIDES.findIndex(slide=>slide.id==='agent'),last=DECK_SLIDES.length-1;
process.stdout.write(JSON.stringify({same,agentAnchor:deckVisualHtml(agent,'learn').includes(' id=learn-agent-access>'),
 cta:[deckCopyHtml(last,facts[0],'welcome').includes('>Set a monthly budget<'),deckCopyHtml(last,facts[1],'welcome').includes('>Review your budget<')]}));
""")
        self.assertEqual(payload["same"], [True] * 12)
        self.assertTrue(payload["agentAnchor"])
        self.assertEqual(payload["cta"], [True, True])
        dialog = self.page.split("<dialog class=welcome id=welcome-dialog", 1)[1].split("</dialog>", 1)[0]
        learn = self.page.split("<div class=view id=view-learn>", 1)[1].split("<div class=view id=view-capabilities>", 1)[0]
        self.assertIn('<div class=deck id=welcome-deck data-deck-host=welcome></div>', dialog)
        self.assertIn('<div class="deck deckInline" id=learn-deck data-deck-host=learn></div>', learn)
        for host in (dialog, learn):
            self.assertNotIn("<h2", host, "slide copy lives in DECK_SLIDES, not in host markup")
            self.assertNotIn("conceptFrame", host)
        self.assertIn("function renderWelcome(state=LATEST){paintDeck('welcome',state);}", self.page)
        self.assertIn("if(ensureDeck('learn'))paintDeck('learn',state);", self.page)
        self.assertIn("paintPreview(`${host}-deck-visual`,deckVisualHtml(current.index,host));", self.page)
        for retired in ("WELCOME_PANELS", "data-welcome-panel", "learnQuestion", "learnBlock", "learn-replay-welcome", "welcomeDot"):
            self.assertNotIn(retired, self.page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_tour_keeps_privacy_estimate_and_agent_caveats(self):
        payload = self._run_tour("""
const legend=id=>DECK_SLIDES.find(slide=>slide.id===id).frame.legend.join(' ');
process.stdout.write(JSON.stringify({tokens:legend('tokens'),privacy:DECK_SLIDES.find(slide=>slide.id==='tokens').body,agent:legend('agent'),
 agentBody:DECK_SLIDES.find(slide=>slide.id==='agent').body,glance:DECK_SLIDES.find(slide=>slide.id==='glance').body,
 places:['mac','windows','linux','other'].map(id=>({mac:'menu bar',windows:'notification area',linux:'tray'}[id]||'menu bar or tray'))}));
""")
        self.assertIn("estimate", payload["tokens"])
        self.assertIn("not your bill", payload["tokens"])
        self.assertIn("on your computer", payload["privacy"])
        self.assertIn("never uploaded", payload["privacy"])
        self.assertIn("numbers only, never your prompts or code", payload["agent"])
        self.assertIn("session-budget changes need your OK", payload["agent"])
        self.assertIn("AI provider sees those numbers", payload["agent"])
        self.assertIn("(MCP)", payload["agentBody"])
        self.assertEqual(payload["glance"], "It sits in your menu bar or tray, always on.")
        self.assertIn("const COMPANION_PLACE={mac:'menu bar',windows:'notification area',linux:'tray'}[DECK_PLATFORM]||'menu bar or tray';", self.page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_tour_escapes_every_interpolated_label(self):
        payload = self._run_tour("""
const bad='<img src=x onerror=alert(1)>';
const frame=conceptFrameHtml({kind:'x',label:bad,alt:`"${bad}`,legend:[bad,bad],html:()=>''},'learn');
const habits=HABITS.map(habit=>({...habit}));HABITS.length=0;HABITS.push({icon:'model',title:bad,page:bad,route:'models'});
const list=habitsListHtml();HABITS.length=0;habits.forEach(habit=>HABITS.push(habit));
const copy=DECK_SLIDES.map((slide,i)=>deckCopyHtml(i,deckFacts(richState),'learn')+deckVisualHtml(i,'learn')+deckResumeHtml(i,'learn')).join('');
process.stdout.write(JSON.stringify({frame:frame.includes('<img'),quote:frame.includes('aria-label=""'),list:list.includes('<img'),copy:copy.includes('<img')}));
""")
        self.assertEqual(payload, {"frame": False, "quote": False, "list": False, "copy": False})

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_tour_rail_states_and_skip_chapter_targets(self):
        payload = self._run_tour("""
const rail=deckRailHtml(6);
process.stdout.write(JSON.stringify({
 skip:DECK_SLIDES.map((_,i)=>deckChapterSkipTarget(i)),
 steps:[...rail.matchAll(/data-deck-go=(\\d+) data-state=(\\w+)/g)].map(m=>m[2]),
 chapters:[...rail.matchAll(/class=deckChapter data-state=(\\w+)/g)].map(m=>m[1]),
 current:(rail.match(/aria-current=step/g)||[]).length,
 glyphs:[rail.includes('aria-hidden=true>✓</i>'),rail.includes('aria-hidden=true>●</i>'),rail.includes('aria-hidden=true>○</i>')],
}));
""")
        self.assertEqual(payload["skip"], [2, 2, 5, 5, 5, 10, 10, 10, 10, 10, -1, -1])
        self.assertEqual(payload["steps"], ["done"] * 6 + ["current"] + ["upcoming"] * 5)
        self.assertEqual(payload["chapters"], ["done", "done", "current", "upcoming"])
        self.assertEqual(payload["current"], 1)
        self.assertEqual(payload["glyphs"], [True, True, True])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_welcome_resume_and_auto_show_matrix(self):
        payload = self._run_tour("""
function run(entries,{read=false,write=false,requested=null,act=null}={}){
 stored=new Map(entries);blocked={read,write};
 const auto=welcomeShouldAutoShow(),start=welcomeStartSlide(requested);
 if(act)act();
 let saved=null;try{saved=JSON.parse(stored.get(WELCOME_KEY));}catch(error){}
 const legacyKept=stored.has(LEGACY_ONBOARDING_KEY);
 let after=null;if(!read&&!write){markWelcomeSeen(0);after=welcomeShouldAutoShow();}
 return {auto,start,saved:saved&&{seen:saved.seen,stamped:Number(saved.dismissed_at)>0,last_slide:saved.last_slide},legacyKept,after};
}
const welcome=value=>[[WELCOME_KEY,typeof value==='string'?value:JSON.stringify(value)]];
const legacy=value=>[[LEGACY_ONBOARDING_KEY,JSON.stringify(value)]];
process.stdout.write(JSON.stringify({
 fresh:run([],{act:()=>markWelcomeSeen(0)}),
 progressing:run([],{act:()=>recordWelcomeSlide(4)}),
 resume:run(welcome({seen:true,dismissed_at:1,last_slide:6})),
 midTourReload:run(welcome({seen:false,last_slide:3})),
 explicit:run(welcome({seen:true,dismissed_at:1,last_slide:6}),{requested:2}),
 finished:run(welcome({seen:true,dismissed_at:1,last_slide:0})),
 outOfRange:run(welcome({seen:true,dismissed_at:1,last_slide:99})),
 negative:run(welcome({seen:true,dismissed_at:1,last_slide:-2})),
 fractional:run(welcome({seen:true,dismissed_at:1,last_slide:2.5})),
 corrupt:run(welcome('{nope')),
 seenKeepsSeen:run(welcome({seen:true,dismissed_at:5,last_slide:1}),{act:()=>recordWelcomeSlide(8)}),
 closeAt:run([],{act:()=>markWelcomeSeen(7)}),
 legacyStep:run(legacy({completed:['current'],collapsed:false,completed_at:0})),
 legacyCollapsed:run(legacy({completed:[],collapsed:true,completed_at:0})),
 legacyFinished:run(legacy({completed:[],collapsed:false,completed_at:1700000000000})),
 legacyUntouched:run(legacy({completed:[],collapsed:false,completed_at:0})),
 readBlocked:run([],{read:true,act:()=>{recordWelcomeSlide(3);markWelcomeSeen(3);}}),
 writeBlocked:run([],{write:true,act:()=>{recordWelcomeSlide(3);markWelcomeSeen(3);saveLearnSlide(3);}}),
 learn:(()=>{stored=new Map([[LEARN_SLIDE_KEY,'9']]);blocked={read:false,write:false};const a=readLearnSlide();stored.set(LEARN_SLIDE_KEY,'x');const b=readLearnSlide();blocked.read=true;const c=readLearnSlide();return [a,b,c];})(),
}));
""")
        start = lambda index, offer: {"index": index, "offer": offer}
        self.assertEqual(payload["fresh"]["saved"], {"seen": True, "stamped": True, "last_slide": 0})
        self.assertTrue(payload["fresh"]["auto"])
        self.assertEqual(payload["fresh"]["start"], start(0, False))
        for case in ("fresh", "legacyStep", "legacyUntouched", "corrupt", "resume"):
            with self.subTest(after=case):
                self.assertFalse(payload[case]["after"], "a seen tour never auto-shows again")
        for case in ("legacyStep", "legacyCollapsed", "legacyFinished", "legacyUntouched"):
            with self.subTest(legacy=case):
                self.assertTrue(payload[case]["legacyKept"], "the legacy onboarding key stays readable")
        self.assertEqual(payload["progressing"]["saved"], {"seen": False, "stamped": False, "last_slide": 4})
        self.assertEqual(payload["resume"]["start"], start(6, True))
        self.assertFalse(payload["resume"]["auto"])
        self.assertTrue(payload["midTourReload"]["auto"], "a tour never closed reopens on the next launch")
        self.assertEqual(payload["midTourReload"]["start"], start(3, True))
        self.assertEqual(payload["explicit"]["start"], start(2, False))
        for case in ("finished", "outOfRange", "negative", "fractional", "corrupt"):
            with self.subTest(case=case):
                self.assertEqual(payload[case]["start"], start(0, False))
        self.assertTrue(payload["corrupt"]["auto"])
        self.assertEqual(payload["seenKeepsSeen"]["saved"], {"seen": True, "stamped": True, "last_slide": 8})
        self.assertEqual(payload["closeAt"]["saved"], {"seen": True, "stamped": True, "last_slide": 7})
        for case in ("legacyStep", "legacyCollapsed", "legacyFinished"):
            with self.subTest(case=case):
                self.assertFalse(payload[case]["auto"])
        self.assertTrue(payload["legacyUntouched"]["auto"])
        self.assertEqual(payload["readBlocked"]["auto"], False)
        self.assertEqual(payload["readBlocked"]["start"], start(0, False))
        self.assertEqual(payload["writeBlocked"]["auto"], True)
        self.assertEqual(payload["writeBlocked"]["start"], start(0, False))
        self.assertIsNone(payload["writeBlocked"]["saved"])
        self.assertEqual(payload["learn"], [9, 0, 0])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_tour_controls_page_skip_chapters_and_close_the_overlay(self):
        payload = self._run_tour(f"""
function extract(name){{let s=page.indexOf(`function ${{name}}(`);if(s<0)throw Error(`missing ${{name}}`);let i=page.indexOf('){{',s)+1,d=0;for(;i<page.length;i++){{if(page[i]==='{{')d++;else if(page[i]==='}}'&&--d===0)return page.slice(s,i+1);}}}}
const els={{}};
var document={{activeElement:null,querySelector:()=>null}};
var $=id=>els[id]||(els[id]={{id,dataset:{{}},innerHTML:'',textContent:'',hidden:false,disabled:false,className:'',scrollTop:0,open:false,contains:()=>false,querySelector:()=>null,focus(){{}},scrollIntoView(){{}},close(){{this.open=false;closed.push(this.id);}}}});
var requestAnimationFrame=fn=>fn(),deliveryPayload=null,closed=[],routes=[];
var openTopLevelRoute=route=>routes.push(route),location={{hash:'#sessions'}},tourReturn=null;
eval(['paintPreview','deckShellHtml','ensureDeck','paintDeck','showDeckSlide','openDeckRoute','deckAction','closeWelcome','showTourReturn','deckKey'].map(extract).join('\\n'));
eval(page.slice(page.indexOf('const deckHosts='),page.indexOf(';',page.indexOf('const deckHosts='))+1).replace(/^const /,'var '));
const log=[];
showDeckSlide('welcome',0);log.push(deckHosts.welcome.index);
deckAction('welcome','skip-chapter');log.push(deckHosts.welcome.index);
deckAction('welcome','skip-chapter');log.push(deckHosts.welcome.index);
deckAction('welcome','skip-chapter');log.push(deckHosts.welcome.index);
const skipDisabled=$('welcome-deck-skip-chapter').disabled;
deckAction('welcome','skip-chapter');log.push(deckHosts.welcome.index);
deckAction('welcome','back');log.push(deckHosts.welcome.index);
const recorded=JSON.parse(stored.get(WELCOME_KEY)).last_slide;
const arrows=[];const key=k=>({{key:k,preventDefault(){{}},target:null}});
deckKey('welcome',key('ArrowRight'));arrows.push(deckHosts.welcome.index);deckKey('welcome',key('ArrowLeft'));arrows.push(deckHosts.welcome.index);
showDeckSlide('welcome',13);const doneLabel=[$('welcome-deck-next').textContent,$('welcome-deck-next').className];
$('welcome-dialog').open=true;deckAction('welcome','next');
showDeckSlide('learn',13);const learnLast=$('learn-deck-next').textContent;deckAction('learn','next');
showDeckSlide('welcome',5,{{offer:true}});const offerFoot=$('welcome-deck-foot').hidden,offerStage=$('welcome-deck-stage').innerHTML;
deckAction('welcome','restart');
$('welcome-dialog').open=true;closeWelcome({{route:'settings',target:'agent-access'}});
process.stdout.write(JSON.stringify({{log,skipDisabled,recorded,arrows,doneLabel,closed,learnAfterLast:deckHosts.learn.index,learnLast,offerFoot,offerResume:offerStage.includes('data-deck-action=resume')&&offerStage.includes('data-deck-action=restart'),afterRestart:[deckHosts.welcome.index,deckHosts.welcome.offer],routes,counter:$('welcome-deck-count').textContent}}));
""")
        self.assertEqual(payload["log"], [0, 2, 5, 10, 10, 9])
        self.assertTrue(payload["skipDisabled"])
        self.assertEqual(payload["recorded"], 9)
        self.assertEqual(payload["arrows"], [10, 9])
        self.assertEqual(payload["doneLabel"], ["Done", "tbtn"])
        self.assertEqual(payload["closed"], ["welcome-dialog", "welcome-dialog"])
        self.assertEqual(payload["learnLast"], "Back to start")
        self.assertEqual(payload["learnAfterLast"], 0)
        self.assertTrue(payload["offerFoot"])
        self.assertTrue(payload["offerResume"])
        self.assertEqual(payload["afterRestart"], [0, False])
        self.assertEqual(payload["routes"], ["settings"])
        self.assertEqual(payload["counter"], "1 / 12 · about 4 min")

    def test_welcome_overlay_and_learn_deck_shells(self):
        dialog = self.page.split("<dialog class=welcome id=welcome-dialog", 1)[1].split("</dialog>", 1)[0]
        self.assertIn("aria-modal=true", dialog)
        self.assertIn("aria-labelledby=welcome-label", dialog)
        self.assertIn('<button class=welcomeClose id=welcome-close type=button aria-label="Close welcome"', dialog)
        self.assertIn("aria-hidden=true>&#10005;</span>", dialog)
        self.assertIn("id=welcome-skip type=button>Skip tour</button>", dialog)
        for marker in (
            "$('welcome-close').onclick=()=>closeWelcome();",
            "$('welcome-skip').onclick=()=>closeWelcome();",
            "$('welcome-dialog').addEventListener('cancel',event=>{event.preventDefault();closeWelcome();});",
            "markWelcomeSeen(index>=DECK_SLIDES.length-1?0:index);",
            "if($('welcome-dialog').open){handleWelcomeKey(event);return;}",
            "handleLearnDeckKey(event);",
            "if(welcomeShouldAutoShow())openWelcome(null,{auto:true});",
            "data-deck-action=skip-chapter>Skip chapter</button>",
            "{id:'welcome',label:'Open welcome tour'",
            "if(command.action==='welcome'){const returnFocus=commandReturnFocus;commandReturnFocus=null;openWelcome(null,{returnFocus});return;}",
            "$('learn-play').onclick=()=>openWelcome(deckHosts.learn.index,{fromLearn:true});",
            "if(h==='learn-agent-access')showDeckSlide('learn',DECK_SLIDES.findIndex(slide=>slide.id==='agent'),{animate:false});",
        ):
            self.assertIn(marker, self.page)
        self.assertIn("maybeAutoShowWelcome();", self.page.split("function applyLiveState(state){", 1)[1].split("\n}", 1)[0])
        learn = self.page.split("<div class=view id=view-learn>", 1)[1].split("<div class=view id=view-capabilities>", 1)[0]
        self.assertIn("<h1>Learn</h1>", learn)
        self.assertIn("<p class=spectrumPageSubtitle>Spend tokens like money. Measure what you get back.</p>", learn)
        self.assertIn("id=learn-play type=button>Play as full screen</button>", learn)
        self.assertNotIn("learnSection", learn)
        css = self.page[self.page.index("<style>"):self.page.index("</style>")]
        self.assertIn(".welcome{", css)
        self.assertRegex(css, r"@media\(prefers-reduced-motion:reduce\)\{[^\n]*\.deckEnter")
        self.assertIn(".deck{display:grid;grid-template-columns:212px minmax(0,1fr)", css)
        self.assertIn("const TOKENOMICS_URL='https://www.splunk.com/en_us/products/tokenomics.html';", self.page)
        self.assertIn('target=_blank rel="noopener noreferrer">Read about Tokenomics', self.page)

    def test_learn_omits_removed_model_and_agent_guidance(self):
        for marker in (
            "id=learn-model-guide",
            "Compare model speed without fooling yourself",
            "Should I keep this run going?",
            "Why was the last phase expensive?",
            "What should I change before the next phase?",
        ):
            self.assertNotIn(marker, self.page)


if __name__ == "__main__":
    unittest.main()
