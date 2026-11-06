
// deck-pure-start: tour storage, slide copy, and frame markup. Nothing here touches the DOM, so tests run it in Node.
const WELCOME_KEY='tm_welcome_v1';
const LEGACY_ONBOARDING_KEY='tm_onboarding_v1';
const LEARN_SLIDE_KEY='tm_learn_slide';
// Reads one stored welcome record. Unreadable storage throws to the caller; corrupt JSON reads as absent.
function readWelcomeStore(key){
 const raw=localStorage.getItem(key);
 if(!raw)return null;
 try{const value=JSON.parse(raw);return value&&typeof value==='object'?value:null;}catch(error){return null;}
}
// First launch only. Anyone who used the retired checklist (a step done, finished, or collapsed) has already met the product.
// When storage cannot be read, a dismissal could not be remembered either, so the welcome stays out of the way.
function welcomeShouldAutoShow(){
 try{
  if(readWelcomeStore(WELCOME_KEY)?.seen===true)return false;
  const legacy=readWelcomeStore(LEGACY_ONBOARDING_KEY);
  if(legacy&&((Array.isArray(legacy.completed)&&legacy.completed.length>0)||legacy.completed===true||legacy.collapsed===true||Number(legacy.completed_at)>0))return false;
  return true;
 }catch(error){return false;}
}
// A stored slide index is trusted only when it names a real slide; anything else starts at the beginning.
function deckSlideIndex(value){
 if(typeof value!=='number'&&!(typeof value==='string'&&value.trim()))return 0;
 const index=Number(value);
 return Number.isInteger(index)&&index>=0&&index<DECK_SLIDES.length?index:0;
}
function readWelcomeProgress(){
 try{return deckSlideIndex(readWelcomeStore(WELCOME_KEY)?.last_slide);}catch(error){return 0;}
}
// An explicit slide (Learn's full-screen button) always wins. Otherwise a tour left partway offers to resume.
function welcomeStartSlide(requested=null){
 if(requested!==null&&requested!==undefined)return {index:deckSlideIndex(requested),offer:false};
 const index=readWelcomeProgress();
 return {index,offer:index>0};
}
function recordWelcomeSlide(index){
 try{
  const previous=readWelcomeStore(WELCOME_KEY),slide=deckSlideIndex(index);
  localStorage.setItem(WELCOME_KEY,JSON.stringify(previous?.seen===true?{seen:true,dismissed_at:Number(previous.dismissed_at)||Date.now(),last_slide:slide}:{seen:false,last_slide:slide}));
 }catch(error){}
}
function markWelcomeSeen(lastSlide=0){
 try{localStorage.setItem(WELCOME_KEY,JSON.stringify({seen:true,dismissed_at:Date.now(),last_slide:deckSlideIndex(lastSlide)}));}catch(error){}
}
function readLearnSlide(){
 try{return deckSlideIndex(localStorage.getItem(LEARN_SLIDE_KEY));}catch(error){return 0;}
}
function saveLearnSlide(index){
 try{localStorage.setItem(LEARN_SLIDE_KEY,String(deckSlideIndex(index)));}catch(error){}
}
const DECK_PLATFORM=(()=>{const platform=String(navigator.userAgentData?.platform||navigator.platform||navigator.userAgent||'');return /mac/i.test(platform)?'mac':/win/i.test(platform)?'windows':/linux|x11|cros/i.test(platform)?'linux':'other';})();
const COMPANION_PLACE={mac:'menu bar',windows:'notification area',linux:'tray'}[DECK_PLATFORM]||'menu bar or tray';
// The only live facts the tour reads change behavior, never what a frame shows: frames always carry sample values.
function deckFacts(state=LATEST){
 const budget=state?.xsession?.budget;
 return {budgetConfigured:!!(budget?.configured&&Number(budget.budget)>0)};
}
const DECK_ARROW='<svg class=deckArrow viewBox="0 0 16 16" aria-hidden=true><path d="M3 8h9M9 4l4 4-4 4"/></svg>';
function levelBarHtml(fill,{label='',tone='',mark=null}={}){
 const at=value=>`${(100*Math.max(0,Math.min(1,value))).toFixed(1)}%`;
 return `<span class=levelBar${tone?` data-tone=${tone}`:''} role=img aria-label="${esc(label)}" style="--fill:${at(fill)}${mark==null?'':`;--mark:${at(mark)}`}"><i></i>${mark==null?'':'<b></b>'}</span>`;
}
// A numbered marker placed on a frame. `at` pins it to a corner or side of its nearest [data-callout] parent; without it, it sits inline.
const calloutHtml=(number,at='')=>`<mark class=frameCallout${at?` data-at=${at}`:''} aria-hidden=true>${number}</mark>`;
// One explained frame: a real dashboard component filled with sample values, numbered markers on it, and a matching numbered legend.
function conceptFrameHtml(frame,host){
 const legend=frame.legend.map((text,index)=>`<li>${calloutHtml(index+1)}<span>${esc(text)}</span></li>`).join('');
 return `<figure class=conceptFrame data-frame=${frame.kind}${frame.anchor?` id=${host}-${frame.anchor}`:''}><div class=conceptFrameHead><span>${esc(frame.label)}</span><span class=chip>Example</span></div><div class=conceptFrameStage role=img aria-label="${esc(frame.alt)}">${frame.html()}</div><figcaption><ol class=frameLegend role=list>${legend}</ol></figcaption></figure>`;
}
// Sample sessions and spend for the frames: round numbers that make each idea obvious. Never the user's data.
const sampleCardHtml=({title,model,cost,metrics,status='working',heroCallout=''})=>`<div class="card currentSessionCard activity-${status}" style="--harness:${providerColor('claude')}"><div class=currentSessionTop><div class=currentSessionRuntime>${runtimeMarkSvg({provider:'claude'})}<span>Claude Code</span><span class=currentSessionProject>website</span></div><div class=currentSessionTopMeta><div class="currentSessionStatus ${status}">${status==='working'?'Working':'Recent'}</div></div></div><div class=currentSessionIdentity><h3>${esc(title)}</h3><span class="currentSessionCaps mono">${esc(model)}</span></div><div class=currentSessionHero><div class=currentSessionHeroCost><span class=currentSessionHeroLabel>Cost</span><b class="currentSessionHeroValue mono">${esc(cost)} ${heroCallout}</b></div></div><div class=currentSessionMetrics>${metrics.map(([label,value,extra='',callout='',labelCallout=''])=>`<div class="currentSessionMetric metric"><span>${esc(label)}${labelCallout?` ${labelCallout}`:''}</span>${extra?`<div class=currentSessionContextValue><b class=mono>${esc(value)}</b>${extra}</div>`:`<b class=mono>${esc(value)}${callout?` ${callout}`:''}</b>`}</div>`).join('')}</div></div>`;
// The real context sparkline: one bar per turn, toned as the chat fills.
const SAMPLE_PRICES=[
 {label:'Re-reading (cache read)',price:.3,color:'var(--c-cache-read)'},
 {label:'Reading new text (input)',price:3,color:'var(--c-input)'},
 {label:'Saving to re-read (cache write)',price:3.75,color:'var(--c-cache-write)'},
 {label:'Writing the answer (output)',price:15,color:'var(--c-output)'},
];
const SAMPLE_SPEND_DAYS=[12,18,9,24,0,0,16,21,14,30,19,22];
const SAMPLE_MODELS=[{name:'Opus',share:.7,cost:'$210.00'},{name:'Sonnet',share:.2,cost:'$60.00'},{name:'Haiku',share:.1,cost:'$30.00'}];
const SAMPLE_OUTPUT_PER_DOLLAR=[2.6,2.8,2.7,3.2,3.1,3.6,3.5,4.1,4.3,4.8,4.7,5.4,5.6,6];
const SAMPLE_HELPER_COST=[6,5.4,4.8,4.1,3.2,2.6,2.1,1.6,1.3,1];
// Frame markup, one function per frame. Each takes no arguments, so no live value can reach it.
function priceFrameHtml(){
 const max=SAMPLE_PRICES.at(-1).price,min=SAMPLE_PRICES[0].price;
 return `<div class=priceLadder>${SAMPLE_PRICES.map((row,index)=>`<div class=priceRow><span class=priceType><i style="--seg:${row.color}"></i>${esc(row.label)}</span>${levelBarHtml(row.price/max)}<b class=mono>${money(row.price)}</b><small class=mono>${esc(`${+(row.price/min).toFixed(1)}×`)}</small>${index===0?calloutHtml(1):index===SAMPLE_PRICES.length-1?calloutHtml(2):''}</div>`).join('')}</div>`;
}
function menuFrameHtml(){
 return `<div class=menuMock><div class=menuMockBar>${calloutHtml(1)}<span class=menuMockMark aria-hidden=true></span><b class=mono>$2.40 est</b><span class=mono>40%</span></div><dl class=menuMockRows><div><dt>This session</dt><dd class=mono>$2.40 est</dd></div><div data-callout><dt>Context</dt><dd class=mono>40%</dd>${calloutHtml(2,'r')}</div><div><dt>Spend today</dt><dd class=mono>$6.40</dd></div></dl></div>`;
}
const SAMPLE_TURN_COSTS=[.05,.07,.09,.12,.15,.19,.23,.28,.34,.41,.5,.62];
function turnsFrameHtml(){
 // Headroom keeps the callout markers above the tallest bars inside the plot.
 const max=Math.max(...SAMPLE_TURN_COSTS)*1.3,last=SAMPLE_TURN_COSTS.length-1,first=SAMPLE_TURN_COSTS[0],end=SAMPLE_TURN_COSTS[last];
 const bars=SAMPLE_TURN_COSTS.map((cost,index)=>`<div class=spendDay><span class="spendBarValue mono"></span><span class=spendDayTrack${index===6||index===last?` data-callout style="--bar-h:${(100*cost/max).toFixed(1)}%"`:''}><span class=spendDayStack style="--spend-height:${(100*cost/max).toFixed(1)}%"><i class=spendStackSegment style="--spend-color:${providerColor('claude')};--segment-share:100%"></i></span>${index===6?calloutHtml(1,'bar'):index===last?calloutHtml(2,'bar'):''}</span><span class=spendDayLabel></span></div>`).join('');
 return `<div class=spendChart><div class=spendPlot><div class=spendChartInner>${bars}</div></div></div><div class=budgetMeterFoot><span>Turn 1 · <b class=mono>${money(first)}</b></span><span>Turn 40 · <b class=mono>${money(end)}</b></span></div><div class=budgetSpend><div class=budgetFigureLine><span class="budgetSpendValue mono">85%</span><span class=budgetOf>of what the AI can hold in mind</span></div><div class=budgetProgress><i style="width:85%"></i></div><div class=budgetMeterFoot><span>Context in use</span><span>${calloutHtml(3)} Nearly full</span></div></div>`;
}
function spendFrameHtml(){
 const max=Math.max(...SAMPLE_SPEND_DAYS);
 const bars=SAMPLE_SPEND_DAYS.map((cost,index)=>`<div class=spendDay><span class="spendBarValue mono"></span><span class=spendDayTrack${index===9?' data-callout':''}><span class=spendDayStack style="--spend-height:${cost?Math.max(3,100*cost/max).toFixed(1):0}%">${cost?`<i class=spendStackSegment style="--spend-color:${providerColor('claude')};--segment-share:100%"></i>`:''}</span>${index===9?calloutHtml(1,'t'):''}</span><span class=spendDayLabel>${index===0?'Oct 1':index===SAMPLE_SPEND_DAYS.length-1?'Oct 12':''}</span></div>`).join('');
 return `<div class=spendChart><div class=spendPlot><div class=spendChartInner>${bars}</div></div></div><div class=budgetSpend><div class=budgetFigureLine><span class="budgetSpendValue mono">$310</span><span class=budgetOf>of <b class=mono>$600</b></span></div><div class=budgetProgress><i style="width:52%"></i><span class=budgetProgressMarkers><span style="left:90%"></span></span></div><div class=budgetMeterFoot><span>Monthly budget · 52% used</span><span>${calloutHtml(2)} Lands near $540</span></div></div>`;
}
function modelsFrameHtml(){
 const colors=modelRankedColors(SAMPLE_MODELS.map(model=>({key:model.name,ids:[`${model.name}::Claude Code`]})));
 return `<div class=modelSpendHead><h2>Where your spend goes</h2><span class=modelSpendTotal>≈ $300.00 est.</span></div><div class=modelSpendBar>${SAMPLE_MODELS.map((model,index)=>`<div class=modelSpendSeg style="flex:${model.share} 1 0;background:${colors.get(model.name)}">${index===0?`<span>${esc(model.name)}</span><b>${Math.round(100*model.share)}%</b>`:''}</div>`).join('')}</div><div class=modelSpendLegend>${SAMPLE_MODELS.map((model,index)=>`<span class=modelSpendChip>${index===0?calloutHtml(1):index===2?calloutHtml(2):''}<i style="background:${colors.get(model.name)}"></i><span class=modelSpendChipName>${esc(model.name)}</span><b class=mono>${esc(model.cost)}</b><em>${Math.round(100*model.share)}%</em></span>`).join('')}</div>`;
}
function efficiencyFrameHtml(){
 const width=520,height=96,pad=8,max=6.6,points=SAMPLE_OUTPUT_PER_DOLLAR.map((value,index)=>[pad+index*(width-2*pad)/(SAMPLE_OUTPUT_PER_DOLLAR.length-1),pad+(height-2*pad)*(1-value/max)]);
 const line=points.map(([x,y],index)=>`${index?'L':'M'}${x.toFixed(1)} ${y.toFixed(1)}`).join(' '),last=points.at(-1),floor=height-pad;
 return `<div class="card efficiencyHeadline"><div class=efficiencyEyebrow>Output / $</div><div class="efficiencyValueRow valueWithDelta"><div class="v mono">6K / $</div><div class=trendDelta data-direction=up data-tone=improving data-callout><strong>↑ 50%</strong><span>vs prior 30 days</span>${calloutHtml(2,'r')}</div></div><div class=efficiencyChartWrap data-callout><svg class=efficiencyChart viewBox="0 0 ${width} ${height}" preserveAspectRatio=none aria-hidden=true><line class=efficiencyChartGrid x1="${pad}" x2="${width-pad}" y1="${floor}" y2="${floor}" vector-effect="non-scaling-stroke"/><path class=efficiencyChartArea d="${line} L${last[0].toFixed(1)} ${floor} L${pad} ${floor} Z"/><path class=efficiencyChartLine d="${line}" vector-effect="non-scaling-stroke"/></svg>${calloutHtml(1,'tr')}<div class=subagentRoleSparkDates><span>Sep 16</span><span>Oct 15</span></div></div></div>`;
}
function toolsFrameHtml(){
 const rows=[['Browser tools','0','12K tokens'],['Design kit','0','4K tokens']];
 return `<div class="card capReviewQueue warn"><div class=capQueueHead><h2>Needs review</h2><span class=capQueueCount>2 packs</span></div><div class=capReviewHead><span class=capReviewCols><span>Pack</span><span>Times used</span><span>Added to each request</span></span></div><div class=capReviewList>${rows.map(([name,use,load],index)=>`<div class=capReviewItem><div class=capReviewRow><div class=capReviewCols><span></span><span class=capReviewName><strong>${esc(name)}</strong></span><span class=capReviewNum>${esc(use)}${index?'':` ${calloutHtml(1)}`}</span><span class=capReviewNum>${esc(load)}${index?'':` ${calloutHtml(2)}`}</span></div></div></div>`).join('')}</div></div>`;
}
function helpersFrameHtml(){
 const width=220,height=62,pad=5,max=Math.max(...SAMPLE_HELPER_COST),x=index=>pad+index*(width-2*pad)/(SAMPLE_HELPER_COST.length-1),y=value=>height-pad-(value/max)*(height-2*pad);
 const line=SAMPLE_HELPER_COST.map((value,index)=>`${x(index).toFixed(1)},${y(value).toFixed(1)}`).join(' ');
 return `<div class=subagentRoleTrendList><div class=subagentRoleTrendRow><div class=subagentRoleIdentity><strong>explorer ${calloutHtml(2)}</strong><small>Claude Code · helper · 20 runs</small></div><div class="subagentRolePrimary good"><span>Cost / run</span><b>$1.00</b><small>was $6.00</small></div><div class=subagentRoleSpark data-callout><svg viewBox="0 0 ${width} ${height}" preserveAspectRatio=none aria-hidden=true><path d="M ${x(0).toFixed(1)} ${height-pad} L ${line.replaceAll(' ',', ')} L ${x(SAMPLE_HELPER_COST.length-1).toFixed(1)} ${height-pad} Z" style="fill:rgb(var(--signal-rgb)/.08)"/><polyline points="${line}" fill=none stroke="var(--accent)" stroke-width="2.2" vector-effect="non-scaling-stroke"/></svg><div class=subagentRoleSparkDates><span>Week 1</span><span>Week 4</span></div>${calloutHtml(1,'tr')}</div></div></div>`;
}
function compareFrameHtml(){
 const runs=[['A','Claude Opus','$0.40','12 steps · 4m'],['B','Claude Haiku','$0.10','11 steps · 3m']];
 return `<div class=compareCards style="grid-template-columns:repeat(2,minmax(0,1fr))">${runs.map(([letter,model,cost,meta],index)=>`<div class="card compareCard" style="--compare-color:${compareColor(index)}"${index?' data-callout':''}><div class=compareCardTop><span class=compareLetter>${letter}</span><span class=compareCardTitle>Fix the login test</span></div><div class=compareCardMeta>${esc(model)}</div><div class="compareCardCost mono">${esc(cost)} est</div><div class=compareCardMeta>${esc(meta)}</div>${index?calloutHtml(1,'tr'):''}</div>`).join('')}</div><div class="card compareSection"><h3>Where the cost went</h3><div class=compareBreakdown>${runs.map(([letter,,cost],index)=>`<div class=compareBreakdownRow${index?' data-callout':''}><span class=compareLetter style="--compare-color:${compareColor(index)}">${letter}</span><div class=compareBreakdownBar style="width:${index?20:100}%"><i style="width:100%;background:${compareColor(index)}"></i></div><b class=mono>${esc(cost)}</b>${index?calloutHtml(2,'r'):''}</div>`).join('')}</div></div>`;
}
function agentFrameHtml(){
 return `<div class=chatMock><p class=chatAsk data-callout>${calloutHtml(1,'l')}Am I on track this month?</p><p class=chatTool>Token Meter → numbers only ${calloutHtml(2)}</p><p class=chatAnswer>Yes. You've spent $310 of your $600 budget. At this pace the month lands near $540.</p><p class=chatTool>Change a session budget? → it asks you first ${calloutHtml(3)}</p></div>`;
}
// A frame-free visual for list slides: rows that each open the page where the habit is measured.
const DECK_ICONS={
 model:'<path d="M5 5h6v6H5zM8 1v4M8 11v4M1 8h4M11 8h4"/>',
 load:'<path d="M2 5l6-3 6 3-6 3zM2 8l6 3 6-3M2 11l6 3 6-3"/>',
 stable:'<path d="M8 2v9M5 5h6M3 10a5 4 0 0 0 10 0"/>',
 done:'<circle cx="8" cy="8" r="6"/><path d="M5.5 8.2l1.8 1.8 3.4-3.6"/>',
 fresh:'<path d="M13 8a5 5 0 1 1-1.5-3.6M13 2v3h-3"/>',
};
const HABITS=[
 {icon:'model',title:'Pick the right model for the job',page:'Models',route:'models'},
 {icon:'load',title:'Load only the tools you need',page:'Tools',route:'capabilities'},
 {icon:'done',title:'Say what “done” looks like',page:'Compare',route:'sessions-compare'},
 {icon:'fresh',title:'Start fresh instead of arguing',page:'All sessions',route:'sessions-all'},
];
function habitsListHtml(){
 return `<ol class=leverList>${HABITS.map(habit=>`<li><button class=leverRow type=button data-deck-route=${habit.route}><svg class=leverIcon viewBox="0 0 16 16" aria-hidden=true>${DECK_ICONS[habit.icon]}</svg><span class=leverName><b>${esc(habit.title)}</b></span><small class=leverPage>${esc(habit.page)}<span class=srOnly>, opens this page</span></small>${DECK_ARROW}</button></li>`).join('')}</ol>`;
}
const TOKENOMICS_URL='https://www.splunk.com/en_us/products/tokenomics.html';
function startListHtml(){
 return `${habitsListHtml()}<div class=teamsRow><span><span class=label>For teams</span><b>Splunk Tokenomics</b></span><a class=tbtn href="${TOKENOMICS_URL}" target=_blank rel="noopener noreferrer">Read about Tokenomics<span aria-hidden=true> ↗</span><span class=srOnly> (opens in a new tab)</span></a></div>`;
}
const DECK_CHAPTERS=[
 {id:'basics',title:'The basics'},
 {id:'work',title:'While you work'},
 {id:'review',title:'Review and find waste'},
 {id:'better',title:'Do better'},
];
// One slide set, two hosts: the welcome overlay and the Learn page render these same entries.
// Word budget per slide: headline ≤ 8, optional body ≤ 14, one to three legend lines of ≤ 12 each, at most one link.
// A frame shows a real dashboard component with sample values and carries an Example chip; list slides show rows instead.
const DECK_SLIDES=[
 {id:'tokens',chapter:'basics',rail:'Tokens',kicker:'Current sessions',title:'You pay for every token the AI uses.',body:'Counted on your computer, never uploaded, and turned into dollars.',
  frame:{kind:'session',label:'Session card',alt:'Example session card: Fix the login page, 1.2 million tokens, $2.40 estimated.',legend:['Tokens are small pieces of words. Every request is counted.','The cost is an estimate from public prices, not your bill.'],
   html:()=>sampleCardHtml({title:'Fix the login page',model:'Claude Sonnet',cost:'$2.40 est',heroCallout:calloutHtml(2),metrics:[['Tokens','1.2M','',calloutHtml(1)],['Context in use','40%'],['Turns','12']]})},
  links:[{label:'Open Current sessions',route:'sessions'}]},
 {id:'prices',chapter:'basics',rail:'Model pricing',kicker:'Model pricing',title:'Not every token costs the same.',
  frame:{kind:'prices',label:'Price per 1M tokens',alt:'Example prices per million tokens: re-reading $0.30, new text $3.00, saving to re-read $3.80, writing the answer $15.00.',legend:['Re-reading what it already saw is the cheapest.','Writing the answer costs about 50 times more.'],html:priceFrameHtml},
  links:[{label:'Open Model pricing',route:'model-pricing'}]},
 {id:'context',chapter:'work',rail:'Current sessions',kicker:'Current sessions',title:'Context in use: long chats cost more.',body:'Each turn, the AI re-reads the whole chat so far.',
  frame:{kind:'context',label:'Cost per turn',alt:'Example chat of 40 turns: each turn costs more, from $0.10 for turn 1 to $0.60 for turn 40, and 85% of what the AI can hold in mind is in use.',legend:['The bars rise as the chat grows. Each turn costs more.','Turn 40 costs about 12 times turn 1.','Nearly full? Start fresh, or ask it to summarize.'],
   html:()=>turnsFrameHtml()},
  links:[{label:'Open Current sessions',route:'sessions'}]},
 {id:'glance',chapter:'work',rail:'Menu bar',kicker:'Menu bar',title:'See the live cost while you work.',body:`It sits in your ${COMPANION_PLACE}, always on.`,
  frame:{kind:'menu',label:`Companion in the ${COMPANION_PLACE}`,alt:'Example companion: this session $2.40 estimated, context 40%, spend today $6.40.',legend:['The live cost of the session you are running.','How much the AI is holding in mind right now.'],html:menuFrameHtml},
  links:[{label:'Open Current sessions',route:'sessions'}]},
 {id:'compare',chapter:'work',rail:'Compare',kicker:'Compare',title:'Compare: change one thing, run again.',body:'Pick two sessions of the same task and see what changed.',
  frame:{kind:'compare',label:'Compare · two runs',alt:'Example comparison of the same task: run A with Claude Opus cost $0.40, run B with Claude Haiku cost $0.10.',legend:['Same task, one change: a lighter model.','B cost 80% less. Keep it if the result holds.'],html:compareFrameHtml},
  links:[{label:'Open Compare',route:'sessions-compare'}]},
 {id:'month',chapter:'review',rail:'Spend',kicker:'Spend',title:'Spend: your month and your budget.',body:'Each day, and where the month will land.',
  frame:{kind:'spend',label:'Spend · this month',alt:'Example month: daily spend bars, $310 spent of a $600 budget, projected to land near $540.',legend:['Each bar is one day of spending.','Where the month should land at this pace.'],html:spendFrameHtml},
  links:[{label:'Open Spend',route:'spend-budgets'}]},
 {id:'models',chapter:'review',rail:'Models',kicker:'Models',title:'Models: one model is often most of it.',
  frame:{kind:'models',label:'Models · spend by model',alt:'Example spend by model: Opus 70%, Sonnet 20%, Haiku 10%.',legend:['This one model is 70% of the spend.','A lighter model can do routine work for less.'],html:modelsFrameHtml},
  links:[{label:'Open Models',route:'models'}]},
 {id:'efficiency',chapter:'review',rail:'Efficiency',kicker:'Efficiency',title:'Efficiency: more work for each dollar.',
  frame:{kind:'efficiency',label:'Efficiency · output per dollar',alt:'Example efficiency: output per dollar rose 50% over 30 days.',legend:['A rising line means more work for each dollar.','The change compared with the 30 days before.'],html:efficiencyFrameHtml},
  links:[{label:'Open Efficiency',route:'efficiency'}]},
 {id:'tools',chapter:'review',rail:'Tools',kicker:'Tools',title:'Tools: unused tools still cost you.',body:'Tool instructions ride along with every request.',
  frame:{kind:'tools',label:'Tools · needs review',alt:'Example review list: Browser tools, used 0 times, adds 12 thousand tokens to every request.',legend:['Never used in your recent sessions.','Still sent with every request. Turn it off.'],html:toolsFrameHtml},
  links:[{label:'Open Tools',route:'capabilities'}]},
 {id:'helpers',chapter:'review',rail:'Subagents',kicker:'Subagents',title:'Subagents: small helper jobs are cheap.',body:'Your agent can hand side jobs to helpers called subagents.',
  frame:{kind:'helpers',label:'Subagents · cost per run',alt:'Example helper: cost per run fell from $6.00 to $1.00 over four weeks.',legend:['Cost per helper run fell from $6.00 to $1.00.','Short, clear jobs keep each run small.'],html:helpersFrameHtml},
  links:[{label:'Open Subagents',route:'subagents'}]},
 {id:'agent',chapter:'better',rail:'Agent connection',kicker:'Agent connection',title:'Ask your AI agent about spending.',body:'Turn on Agent connection, and your agent can ask Token Meter (MCP).',
  frame:{kind:'agent',anchor:'agent-access',label:'Chat with your agent',alt:'Example chat: Am I on track this month? Yes, $310 of $600, landing near $540.',legend:['Ask in plain words, in the chat you already use.','It gets numbers only, never your prompts or code.','Its AI provider sees those numbers; session-budget changes need your OK.'],html:agentFrameHtml},
  links:[{label:'Open Agent connection',route:'settings',target:'agent-access'}]},
 {id:'start',chapter:'better',rail:'Get started',kicker:'Get started',title:'Four habits that lower the bill.',body:'Each habit opens the page that measures it.',visual:startListHtml,
  links:[{label:facts=>facts.budgetConfigured?'Review your budget':'Set a monthly budget',route:'spend-budgets',primary:true}]},
];
function deckCopyHtml(index,facts,host){
 const slide=DECK_SLIDES[index];
 const links=(slide.links||[]).map(link=>`<button class=${link.primary?'deckPrimary':'tbtn'} type=button data-deck-route=${link.route}${link.target?` data-deck-target=${link.target}`:''}>${esc(typeof link.label==='function'?link.label(facts):link.label)}${DECK_ARROW}</button>`).join('');
 return `<span class=deckKicker>${esc(slide.kicker)}</span><h2 id=${host}-deck-title>${esc(slide.title)}</h2>${slide.body?`<p>${esc(slide.body)}</p>`:''}${links?`<div class=deckActions>${links}</div>`:''}`;
}
function deckVisualHtml(index,host){
 const slide=DECK_SLIDES[index];
 return slide.frame?conceptFrameHtml(slide.frame,host):`<div class=deckList>${slide.visual()}</div>`;
}
function deckRailHtml(index){
 return `<ol class=deckChapters>${DECK_CHAPTERS.map((chapter,number)=>{
  const slides=DECK_SLIDES.map((slide,i)=>({slide,i})).filter(row=>row.slide.chapter===chapter.id);
  const first=slides[0].i,last=slides.at(-1).i,state=index>last?'done':index>=first?'current':'upcoming';
  return `<li class=deckChapter data-state=${state}><h3><span class=mono>${number+1}</span>${esc(chapter.title)}</h3><ol>${slides.map(({slide,i})=>{
   const step=i<index?'done':i===index?'current':'upcoming';
   return `<li><button class=deckStep type=button data-deck-go=${i} data-state=${step}${step==='current'?' aria-current=step':''} aria-label="${esc(`${i+1} of ${DECK_SLIDES.length}: ${slide.rail}${step==='done'?', done':''}`)}"><i aria-hidden=true>${step==='done'?'✓':step==='current'?'●':'○'}</i><span>${esc(slide.rail)}</span></button></li>`;
  }).join('')}</ol></li>`;
 }).join('')}</ol>`;
}
// The first slide of the next chapter, or -1 in the last chapter.
function deckChapterSkipTarget(index){
 const chapter=DECK_SLIDES[index]?.chapter;
 return DECK_SLIDES.findIndex((slide,i)=>i>index&&slide.chapter!==chapter);
}
const DECK_SECONDS_PER_SLIDE=18;
function deckTimeLeft(index){
 const seconds=(DECK_SLIDES.length-index)*DECK_SECONDS_PER_SLIDE;
 if(index===0)return `about ${Math.round(seconds/60)} min`;
 return seconds<=60?'under a minute left':`about ${Math.round(seconds/60)} min left`;
}
function deckResumeHtml(index,host){
 return `<section class=deckSlide role=group aria-labelledby=${host}-deck-title><div class=deckCopy><span class=deckKicker>Welcome back</span><h2 id=${host}-deck-title>Pick up where you left off?</h2><p>${esc(`You stopped on slide ${index+1} of ${DECK_SLIDES.length}: ${DECK_SLIDES[index].rail}`)}</p><div class=deckActions><button class=deckPrimary type=button data-deck-action=resume>Resume where you left off${DECK_ARROW}</button><button class=tbtn type=button data-deck-action=restart>Start over</button></div></div></section>`;
}
// deck-pure-end
// Writes preview markup only when it changes, and never while focus is inside it, so polling does not steal focus.
function paintPreview(id,html){
 const root=$(id);
 if(!root||root.dataset.signature===html||root.contains(document.activeElement))return;
 root.dataset.signature=html;
 root.innerHTML=html;
}
let welcomeReturnFocus=null,welcomeAutoChecked=false,welcomeLeaving=false,welcomeFromLearn=false;
const deckHosts={welcome:{index:0,offer:false},learn:{index:readLearnSlide(),offer:false}};
function deckShellHtml(host){
 return `<nav class=deckRail id=${host}-deck-rail aria-label="Tour chapters"></nav><div class=deckMain><div class=deckStage id=${host}-deck-stage></div><footer class=deckFoot id=${host}-deck-foot><button class=deckSkip id=${host}-deck-skip-chapter type=button data-deck-action=skip-chapter>Skip chapter</button><span class="deckCount mono" id=${host}-deck-count aria-hidden=true></span><div class=deckNav><button class=tbtn id=${host}-deck-back type=button data-deck-action=back>Back</button><button class=deckPrimary id=${host}-deck-next type=button data-deck-action=next>Next</button></div></footer><p class=srOnly id=${host}-deck-status aria-live=polite></p></div>`;
}
function ensureDeck(host){
 const root=$(`${host}-deck`);
 if(root.dataset.mounted)return true;
 root.innerHTML=deckShellHtml(host);
 root.dataset.mounted='true';
 return false;
}
function paintDeck(host,state=LATEST){
 const current=deckHosts[host];
 if(current.offer||!$(`${host}-deck-copy`))return;
 paintPreview(`${host}-deck-copy`,deckCopyHtml(current.index,deckFacts(state),host));
 paintPreview(`${host}-deck-visual`,deckVisualHtml(current.index,host));
}
function showDeckSlide(host,index,{animate=true,offer=false}={}){
 ensureDeck(host);
 const current=deckHosts[host],total=DECK_SLIDES.length,rail=$(`${host}-deck-rail`),stage=$(`${host}-deck-stage`);
 const railFocused=rail.contains(document.activeElement),stageFocused=stage.contains(document.activeElement);
 current.index=Math.max(0,Math.min(total-1,Math.trunc(Number(index))||0));
 current.offer=offer;
 const slide=DECK_SLIDES[current.index],last=current.index===total-1;
 rail.innerHTML=deckRailHtml(current.index);
 stage.innerHTML=offer?deckResumeHtml(current.index,host):`<section class="deckSlide${animate?' deckEnter':''}" role=group aria-roledescription=slide aria-labelledby=${host}-deck-title><div class=deckCopy id=${host}-deck-copy></div><div class=deckVisual id=${host}-deck-visual></div></section>`;
 stage.scrollTop=0;
 $(`${host}-deck-foot`).hidden=offer;
 $(`${host}-deck-count`).textContent=`${current.index+1} / ${total} · ${deckTimeLeft(current.index)}`;
 $(`${host}-deck-status`).textContent=offer?`You stopped at slide ${current.index+1} of ${total}. Resume or start over.`:`Slide ${current.index+1} of ${total}: ${slide.rail}`;
 $(`${host}-deck-back`).disabled=current.index===0;
 $(`${host}-deck-skip-chapter`).disabled=deckChapterSkipTarget(current.index)<0;
 const next=$(`${host}-deck-next`);
 next.textContent=last?(host==='welcome'?'Done':'Back to start'):'Next';
 next.className=last?'tbtn':'deckPrimary';
 if(railFocused)rail.querySelector('[aria-current=step]')?.focus();
 else if(stageFocused)next.focus();
 if(offer)return;
 paintDeck(host);
 if(host==='welcome')recordWelcomeSlide(current.index);else saveLearnSlide(current.index);
}
function openDeckRoute(route,target=''){
 openTopLevelRoute(route);
 if(target)requestAnimationFrame(()=>$(target)?.scrollIntoView({block:'start'}));
}
function deckAction(host,action){
 const current=deckHosts[host],last=DECK_SLIDES.length-1;
 if(action==='back')showDeckSlide(host,current.index-1);
 else if(action==='next'){if(current.index<last)showDeckSlide(host,current.index+1);else if(host==='welcome')closeWelcome();else showDeckSlide(host,0);}
 else if(action==='skip-chapter'){const target=deckChapterSkipTarget(current.index);if(target>=0)showDeckSlide(host,target);}
 else if(action==='resume'||action==='restart'){showDeckSlide(host,action==='resume'?current.index:0);$(`${host}-deck-next`).focus();}
}
function renderWelcome(state=LATEST){paintDeck('welcome',state);}
function renderLearn(state=LATEST){
 if(ensureDeck('learn'))paintDeck('learn',state);
 else showDeckSlide('learn',deckHosts.learn.index,{animate:false});
}
function openWelcome(index=null,{auto=false,returnFocus=null,fromLearn=false}={}){
 const dialog=$('welcome-dialog'),other=document.querySelector('dialog[open]');
 if(other&&other!==dialog){if(auto)return false;other.close();}
 showTourReturn(null);
 const start=welcomeStartSlide(index);
 welcomeFromLearn=fromLearn;
 showDeckSlide('welcome',start.index,{animate:false,offer:start.offer});
 if(!dialog.open){welcomeReturnFocus=returnFocus||document.activeElement;dialog.showModal();}
 requestAnimationFrame(()=>(start.offer?dialog.querySelector('[data-deck-action=resume]'):$('welcome-deck-next'))?.focus());
 return true;
}
// Leaving the tour through one of its links keeps a way back to the same slide.
let tourReturn=null;
const TOUR_RETURN_KEY='tm_tour_return';
function showTourReturn(value){
 tourReturn=value;
 $('tour-return').hidden=!value;
 if(value)$('tour-return-label').textContent=value.host==='learn'?'Back to Learn':'Back to the tour';
 try{if(value)sessionStorage.setItem(TOUR_RETURN_KEY,JSON.stringify(value));else sessionStorage.removeItem(TOUR_RETURN_KEY);}catch(error){}
}
// Survives a reload, so the way back is still there after the page refreshes.
function restoreTourReturn(){
 try{const value=JSON.parse(sessionStorage.getItem(TOUR_RETURN_KEY)||'null');if(value&&Number.isInteger(value.index)&&value.index>=0&&value.index<DECK_SLIDES.length)showTourReturn({index:value.index,hash:String(value.hash||''),host:value.host==='learn'?'learn':'welcome'});}catch(error){}
}
function returnToTour(){
 const value=tourReturn;
 showTourReturn(null);
 if(!value)return;
 if(value.host==='learn'){openTopLevelRoute('learn');showDeckSlide('learn',value.index,{animate:false});return;}
 openWelcome(value.index);
}
function closeWelcome({route='',target=''}={}){
 const dialog=$('welcome-dialog');
 welcomeLeaving=!!route;
 const from=dialog.open&&route?{index:deckHosts.welcome.index,hash:location.hash,host:'welcome'}:null;
 if(dialog.open)dialog.close();
 if(route){openDeckRoute(route,target);if(from)showTourReturn(from);}
}
function maybeAutoShowWelcome(){
 if(welcomeAutoChecked)return;
 welcomeAutoChecked=true;
 if(welcomeShouldAutoShow())openWelcome(null,{auto:true});
}
function deckKey(host,event){
 if(event.target?.closest?.('input,select,textarea,[contenteditable]')||event.metaKey||event.ctrlKey||event.altKey)return;
 const current=deckHosts[host];
 if(current.offer)return;
 if(event.key==='ArrowRight'&&current.index<DECK_SLIDES.length-1){event.preventDefault();showDeckSlide(host,current.index+1);}
 else if(event.key==='ArrowLeft'&&current.index>0){event.preventDefault();showDeckSlide(host,current.index-1);}
}
function handleWelcomeKey(event){deckKey('welcome',event);}
// Learn pages with ←/→ only when focus is on the page itself or inside the deck.
function handleLearnDeckKey(event){
 if(!$('view-learn').classList.contains('on')||document.querySelector('dialog[open]'))return;
 const active=document.activeElement;
 if(active&&active!==document.body&&!$('learn-deck').contains(active))return;
 deckKey('learn',event);
}
['welcome','learn'].forEach(host=>$(`${host}-deck`).addEventListener('click',event=>{
 const control=event.target.closest('[data-deck-go],[data-deck-action],[data-deck-route]');
 if(!control)return;
 if(control.dataset.deckGo!==undefined){showDeckSlide(host,Number(control.dataset.deckGo));return;}
 if(control.dataset.deckAction){deckAction(host,control.dataset.deckAction);return;}
 if(host==='welcome')closeWelcome({route:control.dataset.deckRoute,target:control.dataset.deckTarget||''});
 else{const from={index:deckHosts.learn.index,hash:location.hash,host:'learn'};openDeckRoute(control.dataset.deckRoute,control.dataset.deckTarget||'');showTourReturn(from);}
}));
$('welcome-close').onclick=()=>closeWelcome();
$('welcome-skip').onclick=()=>closeWelcome();
$('welcome-dialog').addEventListener('cancel',event=>{event.preventDefault();closeWelcome();});
$('welcome-dialog').addEventListener('close',()=>{
 const index=deckHosts.welcome.index;
 markWelcomeSeen(index>=DECK_SLIDES.length-1?0:index);
 if(welcomeFromLearn&&!welcomeLeaving&&!deckHosts.welcome.offer)showDeckSlide('learn',index,{animate:false});
 welcomeFromLearn=false;
 const target=welcomeReturnFocus;
 welcomeReturnFocus=null;
 if(!welcomeLeaving&&target?.focus&&target.isConnected&&target.offsetParent!==null)target.focus();
 welcomeLeaving=false;
});
$('tour-return').onclick=returnToTour;
restoreTourReturn();
window.addEventListener('popstate',()=>{if(tourReturn&&location.hash===tourReturn.hash)returnToTour();});
$('learn-play').onclick=()=>openWelcome(deckHosts.learn.index,{fromLearn:true});
