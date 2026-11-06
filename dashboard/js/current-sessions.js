const CACHE_RING_TICKS=30,CACHE_RING_SIZE=40;
function cacheRingState(win,now=Date.now()/1000){
 const anchor=Number(win?.anchor_ts),windowS=Number(win?.window_s);
 if(!(anchor>0)||!(windowS>0))return null;
 const elapsed=Math.max(0,now-anchor),maxS=win.max_window_s==null?null:Number(win.max_window_s);
 const left=windowS-elapsed,maxLeft=maxS==null?Infinity:maxS-elapsed;
 const estimate=win.basis!=='recorded',fixed=maxS!=null&&maxS<=windowS;
 // A documented range or minimum is typical, not guaranteed, so past it the cache may still be warm.
 const tone=left>120?'good':left>30?'warn':left>0?'bad':(maxLeft>0||(estimate&&!fixed))?'maybe':'cold';
 return {left,maxLeft,tone,windowS,total:maxS&&maxS>windowS?maxS:windowS,estimate};
}
function cacheClock(seconds){
 const s=Math.max(0,Math.ceil(seconds));
 return s>=600?`${Math.round(s/60)}m`:`${Math.floor(s/60)}:${String(s%60).padStart(2,'0')}`;
}
function cacheWindowLabel(win){
 const s=Number(win.window_s),max=win.max_window_s==null?null:Number(win.max_window_s);
 const span=v=>v>=3600?`${v/3600} h`:`${Math.round(v/60)} min`;
 if(win.basis==='recorded')return `recorded ${span(s)} cache lifetime`;
 const who=win.source==='openai'?'OpenAI':win.source==='anthropic'?'Anthropic':'The provider';
 if(max==null)return `${who} documents at least ${span(s)}; estimate`;
 return max>s?`${who} documents about ${span(s)}–${span(max)}; estimate`:`${who} documents ${span(s)}; estimate`;
}
function cacheRingText(win,state){
 const head=state.left>0
  ?`Cache ${state.tone==='good'?'warm':'cooling'} · ${state.estimate?'about ':''}${cacheClock(state.left)} left`
  :state.tone==='maybe'?'Cache may have expired':state.estimate?'Cache likely expired':'Cache expired';
 const parts=[`${head} (${cacheWindowLabel(win)}).`];
 if(win.prefix_tokens>0&&win.cold_cost_delta!=null){
  const cost=win.cold_cost_delta<.05?`under ${money(.1)}`:`about ${money(win.cold_cost_delta)}`;
  const verb=win.cold_kind==='write'?'re-writes':'re-sends, uncached,';
  parts.push(`${state.left>0?'After that, the next prompt':'The next prompt'} ${verb} ${compactNumber(win.prefix_tokens)} tokens: ${cost} more than a warm prompt (estimate).`);
 }else if(!win.cache_reported){
  parts.push('This agent does not report per-request cache use, so the timer uses the provider’s documented lifetime.');
 }
 return parts.join(' ');
}
function cacheRingTicks(state,size=CACHE_RING_SIZE,count=CACHE_RING_TICKS){
 const center=size/2,outer=center-1,length=3;
 const lit=Math.ceil(Math.max(0,state.left)/state.total*count);
 const remainingMax=Number.isFinite(state.maxLeft)?state.maxLeft:(state.left>0?state.left:0);
 const maybe=Math.max(0,Math.ceil(Math.max(0,remainingMax)/state.total*count)-lit);
 let lines='';
 for(let i=0;i<count;i++){
  const angle=i/count*2*Math.PI-Math.PI/2,fromEnd=count-i;
  const cls=fromEnd<=lit?'':fromEnd<=lit+maybe?'maybe':'off';
  const x1=center+Math.cos(angle)*(outer-length),y1=center+Math.sin(angle)*(outer-length);
  const x2=center+Math.cos(angle)*outer,y2=center+Math.sin(angle)*outer;
  lines+=`<line${cls?` class=${cls}`:''} x1="${x1.toFixed(2)}" y1="${y1.toFixed(2)}" x2="${x2.toFixed(2)}" y2="${y2.toFixed(2)}"/>`;
 }
 return `<svg viewBox="0 0 ${size} ${size}" aria-hidden=true>${lines}</svg>`;
}
function cacheRingFace(state){return state.left>0?cacheClock(state.left):state.tone==='maybe'?'?':'—';}
function cacheRingAttrs(win){
 return `data-cache-window="${esc(JSON.stringify(win))}"`;
}
function cacheRingHtml(row){
 const win=row?.cache_window,state=cacheRingState(win);
 if(!state)return'';
 const text=cacheRingText(win,state);
 return `<span class="cacheRing ${state.tone}" role=img aria-label="${esc(text)}" title="${esc(text)}" ${cacheRingAttrs(win)}>${cacheRingTicks(state)}<b>${cacheRingFace(state)}</b></span>`;
}
function tickCacheRings(){
 document.querySelectorAll('.cacheRing[data-cache-window]').forEach(el=>{
  let win;try{win=JSON.parse(el.dataset.cacheWindow);}catch(error){return;}
  const state=cacheRingState(win);if(!state)return;
  const text=cacheRingText(win,state);
  el.className=`cacheRing ${state.tone}`;
  el.setAttribute('aria-label',text);el.setAttribute('title',text);
  el.innerHTML=`${cacheRingTicks(state)}<b>${cacheRingFace(state)}</b>`;
 });
}
function currentSessionAge(row){
 const seconds=Math.max(0,Math.round(Date.now()/1000-Number(row?.mtime||0)));
 if(seconds<60)return `${seconds}s ago`;
 const minutes=Math.floor(seconds/60);
 if(minutes<60)return `${minutes}m ago`;
 return `${Math.floor(minutes/60)}h ago`;
}
function currentSessionContext(row){
 const context=row?.context||{},latest=Number(context.latest||0),window=Number(context.window||0),ratio=Number(context.latest_pct);
 if(window&&Number.isFinite(ratio))return `${Math.round(Math.max(0,ratio)*100)}%`;
 if(latest)return `${compactNumber(latest)}${context.estimated?' est':''}`;
 return '--';
}
function currentSessionModelName(value){
 const raw=String(value||'').trim();
 if(!raw||raw.toLowerCase()==='unknown')return 'Unknown model';
 const claude=raw.match(/^claude-(opus|sonnet|haiku)-(\d+)-(\d+)(?:-.+)?$/i);
 if(claude)return `Claude ${claude[1][0].toUpperCase()+claude[1].slice(1).toLowerCase()} ${claude[2]}.${claude[3]}`;
 return raw.split(/[-_]+/).filter(Boolean).map(part=>{
  const lower=part.toLowerCase();
  if(lower==='gpt')return 'GPT';
  if(lower==='ai')return 'AI';
  if(/^\d/.test(part))return part;
  return part[0].toUpperCase()+part.slice(1);
 }).join(' ');
}
function loadCurrentSessionOrder(){
 try{
  const value=JSON.parse(localStorage.getItem(CURRENT_SESSION_ORDER_KEY)||'[]');
  return Array.isArray(value)?[...new Set(value.filter(id=>typeof id==='string'&&id).slice(0,CURRENT_SESSION_ORDER_LIMIT))]:[];
 }catch(error){return [];}
}
function saveCurrentSessionOrder(){
 try{localStorage.setItem(CURRENT_SESSION_ORDER_KEY,JSON.stringify(currentSessionOrder.slice(0,CURRENT_SESSION_ORDER_LIMIT)));}catch(error){}
}
function orderedCurrentSessionRows(rows){
 const source=Array.isArray(rows)?rows:[],byId=new Map(),sourceIds=[];
 source.forEach(row=>{
  const id=String(row.id||'');
  if(!id||byId.has(id))return;
  byId.set(id,row);sourceIds.push(id);
 });
 const known=currentSessionOrder.filter(id=>byId.has(id)),seen=new Set(known);
 const unseen=sourceIds.filter(id=>!seen.has(id));
 let next=[...unseen,...known];
 if(currentSessionOrderNeedsNewestMigration&&sourceIds.length){
  const newest=sourceIds[0];
  next=[newest,...next.filter(id=>id!==newest)];
  currentSessionOrderNeedsNewestMigration=false;
  try{localStorage.setItem(CURRENT_SESSION_ORDER_MIGRATION_KEY,'1');}catch(error){}
 }
 if(next.length!==currentSessionOrder.length||next.some((id,index)=>id!==currentSessionOrder[index])){
  currentSessionOrder=next;
  saveCurrentSessionOrder();
 }
 return currentSessionOrder.map(id=>byId.get(id)).filter(Boolean);
}
function currentSessionPositionMessage(id,index,total){
 const row=(LATEST?.xsession?.current_sessions||[]).find(item=>String(item.id)===String(id));
 return `${row?.session_name||row?.project||'Session'} moved to position ${index+1} of ${total}.`;
}
function setCurrentSessionOrder(ids){
 currentSessionOrder=ids;
 saveCurrentSessionOrder();
}
function currentSessionKeyboardMove(allIds,visibleIds,sourceId,delta){
 const ids=[...allIds].map(String),visible=[...visibleIds].map(String),source=String(sourceId);
 const from=visible.indexOf(source);
 if(from<0)return null;
 const destination=Math.max(0,Math.min(visible.length-1,from+delta));
 if(destination===from)return null;
 const target=visible[destination],allFrom=ids.indexOf(source);
 if(allFrom<0)return null;
 ids.splice(allFrom,1);
 const allTarget=ids.indexOf(target);
 if(allTarget<0)return null;
 ids.splice(allTarget+(destination>from?1:0),0,source);
 return {ids,index:destination,total:visible.length};
}
function moveCurrentSessionCard(sourceId,targetId,after=false){
 const ids=orderedCurrentSessionRows(LATEST?.xsession?.current_sessions||[]).map(row=>String(row.id));
 const from=ids.indexOf(String(sourceId)),target=ids.indexOf(String(targetId));
 if(from<0||target<0||from===target)return null;
 const [moved]=ids.splice(from,1);
 let destination=ids.indexOf(String(targetId))+(after?1:0);
 destination=Math.max(0,Math.min(ids.length,destination));
 ids.splice(destination,0,moved);
 setCurrentSessionOrder(ids);
 return {index:destination,total:ids.length};
}
function moveCurrentSessionCardBy(sourceId,delta){
 const orderedRows=orderedCurrentSessionRows(LATEST?.xsession?.current_sessions||[]);
 const result=currentSessionKeyboardMove(
  orderedRows.map(row=>String(row.id)),
  currentSessionFilterView(orderedRows,currentSessionRuntimeFilter,currentSessionActivityFilter).rows.map(row=>String(row.id)),
  sourceId,
  delta,
 );
 if(!result)return null;
 setCurrentSessionOrder(result.ids);
 return {index:result.index,total:result.total};
}
function clearCurrentSessionDropMarkers(){
 $('current-session-grid').querySelectorAll('.dropBefore,.dropAfter').forEach(card=>card.classList.remove('dropBefore','dropAfter'));
 currentSessionDropId='';
 currentSessionDropAfter=false;
}
function currentSessionContextSparkline(row){
 const context=row?.context||{},windowSize=Math.max(0,Number(context.window)||0);
 const samples=(Array.isArray(context.samples)?context.samples:[])
  .map(value=>Math.max(0,Number(value)||0)).filter(value=>Number.isFinite(value));
 const observed=Math.max(0,Number(context.latest)||0),hasObservedSamples=samples.some(value=>value>0);
 if(!observed&&!hasObservedSamples)return {html:'',summary:'Context evidence unavailable.'};
 if(!windowSize)return {
  html:'<span class=currentSessionContextUnavailable aria-hidden=true>window --</span>',
  summary:observed
   ?`Context window unavailable; ${compactNumber(observed)} context tokens observed.`
   :'Context window and latest token count unavailable.',
 };
 if(!samples.length)return {html:'',summary:'Context trajectory unavailable.'};
 const plotted=samples.slice(-24);
 const values=plotted.map(value=>value/windowSize);
 const peak=Math.max(...values),domain=Math.max(1,peak*1.05);
 const width=96,height=22,padX=2,padY=2,plotHeight=height-padY*2,bottom=height-padY,gap=1;
 const barWidth=Math.max(1.5,(width-padX*2-gap*(values.length-1))/values.length);
 const bars=values.map((value,index)=>{
  const normalized=Math.min(domain,Math.max(0,value))/domain;
  const barHeight=Math.max(1.5,normalized*plotHeight);
  const x=padX+index*(barWidth+gap),y=bottom-barHeight;
  const pressure=value>=.85?' high':value>=.7?' watch':'';
  const latest=index===values.length-1?' latest':'';
  return `<rect class="currentSessionContextBar${pressure}${latest}" x="${x.toFixed(2)}" y="${y.toFixed(2)}" width="${barWidth.toFixed(2)}" height="${barHeight.toFixed(2)}" rx=".8"/>`;
 }).join('');
 const latest=values[values.length-1];
 const latestText=pct(latest),peakText=pct(peak);
 const countText=`${plotted.length} recent ${countWord(plotted.length,'execution')}`;
 const summary=`Context across ${countText}: ${latestText} latest, ${peakText} peak.`;
 return {
  summary,
  html:`<svg class=currentSessionContextSpark viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-hidden=true><path class=currentSessionContextBaseline d="M${padX},${bottom} H${width-padX}"/>${bars}</svg>`,
 };
}
function focusCurrentSessionCard(id){
 const card=[...$('current-session-grid').querySelectorAll('[data-current-session-id]')]
  .find(item=>item.dataset.currentSessionId===String(id));
 card?.focus();
}
function currentSessionActivity(row){
 const state=['working','waiting','recent'].includes(row?.activity_state)?row.activity_state:'recent';
 return {state,label:{working:'Working',waiting:'Waiting',recent:'Recent'}[state]};
}
function currentSessionIdsMatch(ids,rows){
 const mounted=[...ids].map(String),expected=(Array.isArray(rows)?rows:[]).map(row=>String(row.id||''));
 return mounted.length===expected.length&&mounted.every((id,index)=>id===expected[index]);
}
function sessionFlowSnapshot(rows,seenIds=[],seenReady=false){
 if(!Array.isArray(rows))return {evidence:'unavailable',mode:'idle',count:0,working:0,waiting:0,recent:0,ids:[],arriving:[]};
 const ids=[],states={working:0,waiting:0,recent:0},known=new Set();
 rows.forEach(row=>{
  const id=String(row?.id||'');
  if(!id||known.has(id))return;
  known.add(id);ids.push(id);
  const state=['working','waiting','recent'].includes(row?.activity_state)?row.activity_state:'recent';
  states[state]++;
 });
 const previous=new Set((Array.isArray(seenIds)?seenIds:[]).map(String));
 return {
  evidence:'available',
  mode:states.working?'working':states.waiting?'waiting':states.recent?'recent':'idle',
  count:ids.length,
  working:states.working,
  waiting:states.waiting,
  recent:states.recent,
  ids,
 arriving:seenReady?ids.filter(id=>!previous.has(id)):[],
 };
}
function pageSignalTiming(reducedMotion=false,now=performance.now()){
 const elapsed=Math.max(0,now-pageSignalClockStartedAt);
 return {heroSpeed:reducedMotion?0:.018,heroFrame:elapsed*.018,borderSpeed:reducedMotion?0:.016,borderFrame:9000+elapsed*.016};
}
function sessionSignalPresentation(snapshot,reducedMotion=false){
 const mode=snapshot?.evidence==='available'&&['working','waiting','recent','idle'].includes(snapshot.mode)?snapshot.mode:'unavailable';
 const strengths={working:1,waiting:.92,recent:.84,idle:.72,unavailable:.62};
 return {...pageSignalTiming(reducedMotion||mode==='unavailable'),mode,heroStrength:strengths[mode]};
}
function loadPageEffectsModule(){
 if(!pageEffectsModulePromise)pageEffectsModulePromise=import('/assets/session-effects.js');
 return pageEffectsModulePromise;
}
function ensurePageSignalHosts(head){
 let field=head.querySelector('.pageSignalField'),heroHost=head.querySelector('.pageDither');
 if(!field){
  field=document.createElement('div');field.className='pageSignalField';field.setAttribute('aria-hidden','true');
  heroHost=document.createElement('div');heroHost.className='pageDither';
  const fallback=document.createElement('div');fallback.className='pageSignalFallback';
  field.append(heroHost,fallback);head.appendChild(field);
 }
 let borderHost=head.querySelector('.pageHeroBorder');
 if(!borderHost){borderHost=document.createElement('div');borderHost.className='pageHeroBorder';borderHost.setAttribute('aria-hidden','true');head.appendChild(borderHost);}
 return {heroHost,borderHost};
}
function pageSignalPresentation(head){
 if(head?.dataset.pageSignal==='sessions'&&latestSessionSignalSnapshot){
  return sessionSignalPresentation(latestSessionSignalSnapshot,pageSignalReducedMotionQuery.matches);
 }
 return {...pageSignalTiming(pageSignalReducedMotionQuery.matches),mode:'ambient',heroStrength:.76};
}
function pageSignalHandle(handle){
 let disposed=false;
 return {
  update(presentation){if(!disposed)handle.update(presentation);},
  dispose(){if(disposed)return;disposed=true;handle.dispose();},
 };
}
function disposeActivePageSignalEffects(){
 activePageSignalGeneration++;
 activePageHeroHandle?.dispose();activePageBorderHandle?.dispose();
 activePageHeroHandle=null;activePageBorderHandle=null;activePageSignalHead=null;activePageSignalMounting=false;
}
async function syncActivePageSignalEffects(){
 let head=document.querySelector('.view.on .spectrumPageHead[data-page-signal]');
 if(head?.closest('#view-session.sessionDetail'))head=null;
 if(!head){disposeActivePageSignalEffects();return;}
 const presentation=pageSignalPresentation(head);
 if(activePageSignalHead===head&&activePageHeroHandle&&activePageBorderHandle){
  activePageHeroHandle.update(presentation);activePageBorderHandle.update(presentation);return;
 }
 if(activePageSignalHead===head&&activePageSignalMounting)return;
 disposeActivePageSignalEffects();activePageSignalHead=head;
 const generation=++activePageSignalGeneration,{heroHost,borderHost}=ensurePageSignalHosts(head);
 heroHost.dataset.pageEffect=pageEffectsDisabled?'fallback':'loading';borderHost.dataset.pageEffect=pageEffectsDisabled?'fallback':'loading';
 if(pageEffectsDisabled)return;
 activePageSignalMounting=true;
 let heroHandle=null,borderHandle=null;
 const mountIsCurrent=()=>generation===activePageSignalGeneration&&activePageSignalHead===head&&head.isConnected;
 try{
  const module=await loadPageEffectsModule();
  if(!mountIsCurrent())return;
  heroHandle=pageSignalHandle(await module.mountDitherField(heroHost,presentation));
  if(!mountIsCurrent()){heroHandle.dispose();return;}
  activePageHeroHandle=heroHandle;
  const mountedBorder=await module.mountPulsingBorder(borderHost,presentation,mountIsCurrent);
  if(!mountedBorder)return;
  borderHandle=pageSignalHandle(mountedBorder);
  if(!mountIsCurrent()){heroHandle.dispose();borderHandle.dispose();return;}
  activePageBorderHandle=borderHandle;
 }catch(error){
  heroHandle?.dispose();borderHandle?.dispose();
  if(activePageHeroHandle===heroHandle)activePageHeroHandle=null;
  if(activePageBorderHandle===borderHandle)activePageBorderHandle=null;
  if(mountIsCurrent()){
   pageEffectsDisabled=true;
   heroHost.replaceChildren();borderHost.replaceChildren();heroHost.dataset.pageEffect='fallback';borderHost.dataset.pageEffect='fallback';
  }
 }finally{
  if(generation===activePageSignalGeneration)activePageSignalMounting=false;
 }
}
function syncSessionSignal(snapshot){
 latestSessionSignalSnapshot=snapshot;
 if(activePageSignalHead?.dataset.pageSignal==='sessions')syncActivePageSignalEffects();
}
function syncSessionFlow(snapshot){
 const head=$('session-flow-head'),label=$('session-flow-label');
 if(!head||!label)return;
 const mode=snapshot.evidence==='available'?snapshot.mode:'unavailable';
 head.dataset.sessionFlow=mode;head.closest('.spectrumPageHeadFrame').dataset.sessionFlow=mode;
 const count=snapshot.count,activeLabel=`${count} active`;
 const nextLabel=snapshot.evidence!=='available'?'Waiting for evidence':!count?'Listening':snapshot.working?`${snapshot.working} working · ${activeLabel}`:activeLabel;
 if(label.textContent!==nextLabel)label.textContent=nextLabel;
 syncSessionSignal(snapshot);
}
pageSignalReducedMotionQuery.addEventListener?.('change',syncActivePageSignalEffects);
function syncCurrentSessionActivity(root,rows){
 const byId=new Map();
 rows.forEach(row=>{const id=String(row.id||'');if(id&&!byId.has(id))byId.set(id,row);});
 root.querySelectorAll('.currentSessionCard[data-current-session-id]').forEach(card=>{
  const row=byId.get(card.dataset.currentSessionId);
  if(!row)return;
  const {state,label}=currentSessionActivity(row),status=card.querySelector('.currentSessionStatus');
  card.classList.remove('activity-working','activity-waiting','activity-recent');
  card.classList.add(`activity-${state}`);
  if(status){status.className=`currentSessionStatus ${state}`;status.textContent=label;}
 });
}
function renderCurrentDaySummary(xs){
 if(!Array.isArray(xs?.daily)){
  // No cross-session payload yet means loading (no note); a payload without daily rows is unavailable.
  const note=xs&&typeof xs==='object'?'Unavailable':'';
  $('current-day-spend').textContent='--';$('current-day-spend-note').textContent=note;
  $('current-day-sessions').textContent='--';$('current-day-sessions-note').textContent=note;
  $('current-day-vs-yesterday').textContent='--';$('current-day-vs-yesterday-note').textContent=note;
  return;
 }
 const days=xs.daily;
 const todayKey=localDayKey(),yesterdayDate=new Date();
 yesterdayDate.setHours(12,0,0,0);
 yesterdayDate.setDate(yesterdayDate.getDate()-1);
 const yesterdayKey=localDayKey(yesterdayDate);
 const today=days.find(row=>row.day===todayKey),yesterday=days.find(row=>row.day===yesterdayKey);
 const todayCostAvailable=Boolean(today)&&metricAvailable(today,'cost'),todayPartial=Boolean(today)&&metricPartial(today,'cost');
 const yesterdayCostAvailable=Boolean(yesterday)&&metricAvailable(yesterday,'cost'),yesterdayPartial=Boolean(yesterday)&&metricPartial(yesterday,'cost');
 setCostValue('current-day-spend',money(today?.cost),todayCostAvailable);
 // The value carries no estimate suffix, so the note always says when today includes local estimates.
 $('current-day-spend-note').textContent=!today?'No recorded activity today':!todayCostAvailable?'Cost unavailable':todayPartial?`Partial billing coverage${hasLocalEstimate(today)?' · includes estimates':''}`:`${Number(today.discount_usd||0)>0?`After discounts · list ${money(Number(today.cost||0)+Number(today.discount_usd||0))}`:'API-equivalent estimate'}${hasLocalEstimate(today)?' · includes local estimates':''}`;
 const todaySessions=Number(today?.sessions||0),yesterdaySessions=Number(yesterday?.sessions||0),sessionDelta=todaySessions-yesterdaySessions;
 $('current-day-sessions').textContent=f(todaySessions);
 $('current-day-sessions-note').textContent=!today?(yesterday?`${f(yesterdaySessions)} yesterday · none recorded today`:'No recorded sessions today'):yesterday?`${f(yesterdaySessions)} yesterday${sessionDelta?` · ${f(Math.abs(sessionDelta))} ${countWord(Math.abs(sessionDelta),'session')} ${sessionDelta>0?'more':'fewer'}`:' · unchanged'}`:'No recorded sessions yesterday';
 let comparison='--',comparisonNote='No recorded activity today';
 if(today&&todayCostAvailable){
  // Partial billing coverage still compares the recorded spend; the note says which day is partial.
  const partial=todayPartial&&yesterdayPartial?' · both days partial':todayPartial?' · today partial':yesterdayPartial?' · yesterday partial':'';
  if(!yesterday){comparison=Number(today.cost||0)?'New':'0%';comparisonNote=`No recorded spend yesterday${todayPartial?' · today partial':''}`;}
  else if(!yesterdayCostAvailable)comparisonNote='Yesterday cost unavailable';
  else{comparison=dailyDeltaLabel(today.cost,yesterday.cost);comparisonNote=`vs ${money(yesterday.cost)}${hasLocalEstimate(yesterday)?' est':''} yesterday${partial}`;}
 }
 $('current-day-vs-yesterday').textContent=comparison;
 $('current-day-vs-yesterday-note').textContent=comparisonNote;
}
function currentSessionsIdleHistory(xs,todayKey=localDayKey()){
 const rows=Array.isArray(xs?.daily)?xs.daily:[];
 const end=new Date(`${todayKey}T12:00:00`);
 const byDay=new Map(rows.map(row=>[row.day,row]));
 const dayKey=date=>`${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;
 const days=Array.from({length:7},(_,index)=>{
  const date=new Date(end);
  date.setDate(end.getDate()-6+index);
  const day=dayKey(date),row=byDay.get(day);
  return {day,sessions:Math.max(0,Number(row?.sessions)||0)};
 });
 const activeRows=days.map(day=>byDay.get(day.day)).filter(row=>row&&Number(row.sessions)>0);
 const availableCostRows=activeRows.filter(row=>row.availability?.cost!==false);
 const runtimes=[...new Set(activeRows.flatMap(row=>(row.providers||[]).map(provider=>String(provider?.provider||'').trim()).filter(Boolean)))].sort();
 return {
  days,
  sessions:days.reduce((total,day)=>total+day.sessions,0),
  cost:availableCostRows.reduce((total,row)=>total+(Number(row.cost)||0),0),
  costAvailable:availableCostRows.length>0,
  costPartial:activeRows.some(row=>row.availability?.cost===false||row.coverage?.cost?.complete===false),
  costEstimated:availableCostRows.some(row=>['local_estimate','mixed'].includes(row.usage_basis)||Number(row.provenance?.estimated_sessions)>0),
  runtimes,
 };
}
function currentSessionsEmptyHtml(xs,todayKey=localDayKey()){
 const history=currentSessionsIdleHistory(xs,todayKey),maxSessions=Math.max(1,...history.days.map(day=>day.sessions));
 const costText=history.costAvailable?`${history.costPartial?'At least ':''}${money(history.cost)}${history.costEstimated?' est':''} tracked`:'Spend unavailable';
 const runtimeText=`${f(history.runtimes.length)} ${countWord(history.runtimes.length,'runtime')}`;
 const historyTitle=history.sessions?`${f(history.sessions)} past ${countWord(history.sessions,'session')}`:'No past sessions yet';
 const historyDetail=history.sessions?`over 7 days · <b>${esc(costText)}</b> · <b>${esc(runtimeText)}</b>`:'Your seven-day activity will build here.';
 const horizonLabel=history.days.map(day=>`${new Date(`${day.day}T12:00:00`).toLocaleDateString([],{weekday:'short'})} ${day.sessions}`).join(', ');
 const horizon=history.days.map(day=>{
  const weekday=new Date(`${day.day}T12:00:00`).toLocaleDateString([],{weekday:'short'});
  const height=day.sessions?Math.max(7,Math.round(62*day.sessions/maxSessions)):0;
  return `<div class=currentSessionsHistoryDay aria-hidden=true><span class=currentSessionsHistoryValue>${f(day.sessions)}</span><i class=currentSessionsHistoryBar style="--activity:${height}px"></i><small data-short="${esc(weekday.slice(0,1))}">${esc(weekday)}</small></div>`;
 }).join('');
 const historyAction=history.sessions?`<button class=currentSessionsEmptyAction type=button data-current-empty-history>Explore ${f(history.sessions)} past ${countWord(history.sessions,'session')}<svg viewBox="0 0 16 16" aria-hidden=true><path d="M3 8h9M9 4l4 4-4 4"/></svg></button>`:'';
 return `<section class="card currentSessionsEmpty"><div class=currentSessionsEmptyInner><h3>Your next live session appears here</h3><p class=currentSessionsEmptyCopy>Start a supported agent. Its live card appears here.</p><div class=currentSessionsArrival role=group aria-label="Placeholder for the next live session card"><div class=currentSessionsArrivalHead><strong>Next live session</strong><span>Listening</span></div><div class=currentSessionsArrivalMetrics><div class=currentSessionsArrivalMetric><span>Cost</span><strong>--</strong></div><div class=currentSessionsArrivalMetric><span>Output / $</span><strong>--</strong></div><div class=currentSessionsArrivalMetric><span>Context in use</span><strong>--</strong></div><div class=currentSessionsArrivalMetric><span>Speed</span><strong>--</strong></div></div></div><div class=currentSessionsHistorySummary><strong>${esc(historyTitle)}</strong><span>${historyDetail}</span></div><div class=currentSessionsHorizon role=img aria-label="Sessions over the last seven days: ${esc(horizonLabel)}">${horizon}</div><div class=currentSessionsEmptyActions>${historyAction}<a class="currentSessionsEmptyAction secondary" href="https://github.com/splunk/token-meter/issues" target=_blank rel="noopener noreferrer">Share feedback<svg viewBox="0 0 16 16" aria-hidden=true><path d="M6 3h7v7M13 3 6 10M11 9v4H3V5h4"/></svg></a></div></div></section>`;
}
function currentSessionsFilteredEmptyHtml(activitySelection){
 const activity=activitySelection==='recent'?'recent':'working';
 return `<section class="card currentSessionsEmpty currentSessionsFilteredEmpty"><div class=currentSessionsEmptyInner><h3>No ${activity} sessions right now</h3><p class=currentSessionsEmptyCopy>Filters are still active. Matching live sessions will appear here automatically.</p></div></section>`;
}
function currentSessionRuntimeView(rows,selection='all'){
 const source=Array.isArray(rows)?[...rows]:[],byId=new Map();
 source.forEach(row=>{
  const id=String(appFilterGroup(row)||'unknown-runtime'),label=String(appFilterLabel(row)||'Unknown Runtime');
  const option=byId.get(id)||{id,label,count:0};
  option.count+=1;byId.set(id,option);
 });
 const options=[...byId.values()].sort((a,b)=>a.label.localeCompare(b.label)||a.id.localeCompare(b.id));
 const active=selection==='all'||options.some(option=>option.id===selection)?selection:'all';
 const visible=active==='all'?source:source.filter(row=>String(appFilterGroup(row)||'unknown-runtime')===active);
 return {options,selection:active,rows:visible,countLabel:active==='all'?`${source.length} active`:`${visible.length} of ${source.length} active`};
}
function currentSessionFilterView(rows,runtimeSelection='all',activitySelection='all'){
 const source=Array.isArray(rows)?[...rows]:[];
 const runtimeView=currentSessionRuntimeView(source,runtimeSelection);
 const activity=['all','working','recent'].includes(activitySelection)?activitySelection:'all';
 const visible=activity==='all'?runtimeView.rows:runtimeView.rows.filter(row=>currentSessionActivity(row).state===activity);
 const filtered=runtimeView.selection!=='all'||activity!=='all';
 return {...runtimeView,activitySelection:activity,rows:visible,countLabel:filtered?`${visible.length} of ${source.length} active`:`${source.length} active`};
}
function currentSessionFilterFocusTarget(view,focusedFilter){
 if(!focusedFilter)return '';
 const ids=['all',...view.options.map(option=>option.id)];
 return ids.includes(focusedFilter)?focusedFilter:view.selection;
}
function renderCurrentSessionRuntimeFilters(view){
 const root=$('current-session-runtime-filters'),signature=JSON.stringify(view.options.map(({id,label})=>[id,label]));
 const focusedFilter=root.contains(document.activeElement)?document.activeElement.dataset.currentRuntimeFilter||'':'';
 let optionsChanged=false;
 root.hidden=!view.options.length;
 $('current-session-runtime-filter-group').hidden=root.hidden;
 if(root.dataset.options!==signature){
  optionsChanged=true;
  root.dataset.options=signature;
  root.innerHTML=`<button type=button data-current-runtime-filter=all>All</button>`+view.options.map(option=>`<button type=button data-current-runtime-filter="${esc(option.id)}">${esc(option.label)}</button>`).join('');
 }
 root.querySelectorAll('[data-current-runtime-filter]').forEach(button=>{
  const selected=button.dataset.currentRuntimeFilter===view.selection;
  button.classList.toggle('on',selected);button.setAttribute('aria-pressed',selected?'true':'false');
 });
 if(optionsChanged&&focusedFilter){
  const target=currentSessionFilterFocusTarget(view,focusedFilter);
  [...root.querySelectorAll('[data-current-runtime-filter]')].find(button=>button.dataset.currentRuntimeFilter===target)?.focus();
 }
}
function renderCurrentSessionActivityFilters(selection){
 const root=$('current-session-activity-filters');
 $('current-session-activity-filter-group').hidden=false;
 root.querySelectorAll('[data-current-activity-filter]').forEach(button=>{
  const selected=button.dataset.currentActivityFilter===selection;
  button.classList.toggle('on',selected);button.setAttribute('aria-pressed',selected?'true':'false');
 });
}
function renderCurrentSessions(state=LATEST){
 renderCurrentDaySummary(state?.xsession);
 const sourceRows=state?.xsession?.current_sessions;
 const flow=sessionFlowSnapshot(sourceRows,currentSessionSeenIds,currentSessionSeenReady);
 syncSessionFlow(flow);
 if(!Array.isArray(sourceRows)){
  $('current-session-runtime-filters').hidden=true;
  $('current-session-runtime-filter-group').hidden=true;
  $('current-session-activity-filters').hidden=true;
  $('current-session-activity-filter-group').hidden=true;
  $('current-session-count-label').textContent='Waiting';
  return;
 }
 const orderedRows=orderedCurrentSessionRows(sourceRows),filterView=currentSessionFilterView(orderedRows,currentSessionRuntimeFilter,currentSessionActivityFilter);
 currentSessionSeenIds=flow.ids;currentSessionSeenReady=true;
 currentSessionRuntimeFilter=filterView.selection;
 currentSessionActivityFilter=filterView.activitySelection;
 renderCurrentSessionRuntimeFilters(filterView);
 $('current-session-activity-filters').hidden=false;
 renderCurrentSessionActivityFilters(filterView.activitySelection);
 const rows=filterView.rows,count=rows.length;
 const arrivingIds=new Set(flow.arriving);
 const baseline=dailyBaseline(state?.xsession?.daily),sessionTypical=baseline.sessionCost;
 const sessionTypicalText=sessionTypical>0?money(sessionTypical)+(baseline.estimated?' est':''):'';
 const focusedId=document.activeElement?.closest?.('[data-current-session-id]')?.dataset.currentSessionId||'';
 $('current-session-count-label').textContent=filterView.countLabel;
 const currentGrid=$('current-session-grid'),interactingCurrentSessionCard=currentGrid.querySelector('.currentSessionSlot:hover,.currentSessionCard:focus,.currentSessionDesktop:focus');
 const mountedCurrentSessionIds=[...currentGrid.querySelectorAll('.currentSessionCard[data-current-session-id]')].map(card=>card.dataset.currentSessionId);
 if(currentSessionDragId||(interactingCurrentSessionCard&&currentSessionIdsMatch(mountedCurrentSessionIds,rows))){syncCurrentSessionActivity(currentGrid,rows);return;}
 currentGrid.innerHTML=count?rows.map(row=>{
  const provider=runtimeId(row);
  const {state:activity,label:activityLabel}=currentSessionActivity(row);
  const costAvailable=row.availability?.cost!==false;
  const estimate=row.cost_approx||['local_estimate','mixed'].includes(row.usage_basis);
  // A folded family with an unmeasured member reports a lower bound.
  const costPartial=costAvailable&&!!row.cost_partial;
  const cost=costValueHtml(money(row.cost)+(estimate?' est':''),costAvailable,false),costLabel=costPartial?'Cost, at least':'Cost';
  const costTipAttrs=costAvailable?'':costUnavailableAttrs();
  const outputPerDollar=row.output_per_dollar==null?'--':compactNumber(row.output_per_dollar);
  const context=currentSessionContext(row);
  const runtime=runtimeLabel(row),model=currentSessionModelName(row.primary_model);
  const effort=String(row.reasoning_effort||'').trim().replaceAll('_',' ');
  const runtimeLine=`${runtime} / ${model}${effort?` ${effort}`:''}`;
  const sessionName=row.session_name||row.project||'Untitled session';
	  const throughput=sessionDisplayThroughput(row),speedAvailable=row.availability?.throughput!==false&&throughput.available;
  const speed=speedAvailable?`${speedFmt(throughput.output_tps)} tok/s${row.token_estimate?' est':''}`:'--';
  const contextSpark=currentSessionContextSparkline(row);
  const outputDollarDescription=row.output_per_dollar==null?'Output per dollar unavailable.':`${compactNumber(row.output_per_dollar)} output tokens per covered dollar.`;
  const costComparable=costAvailable&&!costPartial&&sessionTypical>0;
  const heroCaption=costComparable?typicalCaption(Number(row.cost)||0,sessionTypical,'your typical session',sessionTypicalText):'';
  const heroScale=costComparable?readoutScaleHtml({value:Number(row.cost)||0,typical:sessionTypical,label:`${money(row.cost)} against a typical session of ${sessionTypicalText}`}):'';
  const hints=(row.hints||[]).slice(0,3);
  const hintText=hints.map(hint=>`${hint.title}: ${hint.detail}`).join(' ');
  const cardDescription=`${heroCaption?heroCaption+'. ':''}${contextSpark.summary} ${outputDollarDescription}${costPartial?' Cost is a lower bound: some runs in this session family have no cost evidence.':''}${hints.length?` Suggestion${hints.length===1?'':'s'}: ${hintText}`:''} Drag to reorder. Press ⌥ and an arrow key to move.`;
  const hintsHtml=hints.length?`<div class=currentSessionHints>${hints.map(hint=>`<span class=currentSessionHint data-kind="${esc(hint.kind)}" title="${esc(hint.detail)}"><b>${esc(hint.title)}</b><small>${esc(hint.detail)}</small></span>`).join('')}</div>`:'';
  const arriving=arrivingIds.has(String(row.id))?' is-arriving':'';
  const desktop=desktopSessionTarget(row);
  const desktopLink=desktop?`<a class=currentSessionDesktop href="${esc(desktop.href)}" draggable=false title="${esc(desktop.title)}" aria-label="${esc(`${desktop.label}: ${sessionName}`)}"><svg viewBox="0 0 16 16" aria-hidden=true><path d="M9.5 2.5h4v4M13.5 2.5 7.5 8.5M12 9.5v3a1 1 0 0 1-1 1H3.5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h3"/></svg></a>`:'';
  return `<div class=currentSessionSlot${desktop?' data-desktop':''}><button class="card currentSessionCard provider-${esc(provider)} activity-${activity}${arriving}${costAvailable?'':' fieldtip'}" style="--harness:${esc(runtimeHarness(row))}" type=button draggable=true data-current-session-id="${esc(row.id)}" aria-label="${esc(`Open ${sessionName}`)}" aria-description="${esc(cardDescription)}" aria-keyshortcuts="Alt+ArrowLeft Alt+ArrowRight Alt+ArrowUp Alt+ArrowDown" ${costTipAttrs}><div class=currentSessionTop><div class=currentSessionRuntime>${runtimeMarkSvg(row)}<span>${esc(runtimeLine)}</span><span class=currentSessionProject>${esc(row.project||'No project')}</span></div><div class=currentSessionTopMeta><span class=currentSessionMove><svg class=currentSessionGrip viewBox="0 0 12 16" aria-hidden=true><circle cx="3" cy="3" r="1.2"/><circle cx="9" cy="3" r="1.2"/><circle cx="3" cy="8" r="1.2"/><circle cx="9" cy="8" r="1.2"/><circle cx="3" cy="13" r="1.2"/><circle cx="9" cy="13" r="1.2"/></svg>${currentSessionAge(row)}</span>${cacheRingHtml(row)}<div class="currentSessionStatus ${activity}">${activityLabel}</div></div></div><div class=currentSessionIdentity><h3>${esc(sessionName)}</h3><span class="currentSessionCaps mono" title="${esc(CAPABILITY_SUMMARY_TIP)}">${esc(capabilitySummaryText(row.capabilities))}</span></div><div class=currentSessionHero><div class=currentSessionHeroCost><span class=currentSessionHeroLabel>${costLabel}</span><b class="currentSessionHeroValue mono">${cost}</b></div><div class=currentSessionHeroCompare>${heroScale}${heroCaption?`<span class=readoutCaption>${esc(heroCaption)}</span>`:''}</div></div><div class=currentSessionMetrics><div class="currentSessionMetric metric"><span>Output / $</span><b class=mono>${esc(outputPerDollar)}</b></div><div class="currentSessionMetric metric"><span>Context in use</span><div class=currentSessionContextValue><b class=mono>${esc(context)}</b>${contextSpark.html}</div></div><div class="currentSessionMetric metric"><span>Speed</span><b class=mono>${esc(speed)}</b></div></div>${hintsHtml}</button>${desktopLink}</div>`;
 }).join(''):(filterView.activitySelection==='all'?currentSessionsEmptyHtml(state?.xsession):currentSessionsFilteredEmptyHtml(filterView.activitySelection));
 if(focusedId)requestAnimationFrame(()=>focusCurrentSessionCard(focusedId));
}
function keepCurrentSessionFilterFocusVisible(event){
 const button=event.target.closest('button');
 if(button)button.scrollIntoView({block:'nearest',inline:'nearest'});
}
const currentSessionRuntimeFilters=$('current-session-runtime-filters');
currentSessionRuntimeFilters.addEventListener('focusin',keepCurrentSessionFilterFocusVisible);
currentSessionRuntimeFilters.onclick=event=>{
 const button=event.target.closest('[data-current-runtime-filter]');
 if(!button)return;
 currentSessionRuntimeFilter=button.dataset.currentRuntimeFilter||'all';
 renderCurrentSessions();
};
const currentSessionActivityFilters=$('current-session-activity-filters');
currentSessionActivityFilters.addEventListener('focusin',keepCurrentSessionFilterFocusVisible);
currentSessionActivityFilters.onclick=event=>{
 const button=event.target.closest('[data-current-activity-filter]');
 if(!button)return;
 currentSessionActivityFilter=button.dataset.currentActivityFilter||'all';
 renderCurrentSessions();
};
const currentSessionGrid=$('current-session-grid');
currentSessionGrid.onclick=event=>{
 if(Date.now()<currentSessionSuppressClickUntil){event.preventDefault();return;}
 const historyAction=event.target.closest('[data-current-empty-history]');
 if(historyAction){openSessionScope('all');return;}
 const card=event.target.closest('[data-current-session-id]');
 if(card)selectSession(card.dataset.currentSessionId);
};
currentSessionGrid.addEventListener('dragstart',event=>{
 const card=event.target.closest('[data-current-session-id]');
 if(!card)return;
 currentSessionDragId=card.dataset.currentSessionId;
 card.classList.add('dragging');
 if(event.dataTransfer){
  event.dataTransfer.effectAllowed='move';
  event.dataTransfer.setData('text/plain',currentSessionDragId);
 }
});
currentSessionGrid.addEventListener('dragover',event=>{
 if(!currentSessionDragId)return;
 const card=event.target.closest('[data-current-session-id]');
 if(!card||card.dataset.currentSessionId===currentSessionDragId){clearCurrentSessionDropMarkers();return;}
 event.preventDefault();
 if(event.dataTransfer)event.dataTransfer.dropEffect='move';
 const rect=card.getBoundingClientRect();
 const columns=getComputedStyle(currentSessionGrid).gridTemplateColumns.trim().split(/\s+/).length;
 const after=columns===1?event.clientY>rect.top+rect.height/2:event.clientX>rect.left+rect.width/2;
 if(currentSessionDropId!==card.dataset.currentSessionId||currentSessionDropAfter!==after){
  clearCurrentSessionDropMarkers();
  currentSessionDropId=card.dataset.currentSessionId;
  currentSessionDropAfter=after;
  card.classList.add(after?'dropAfter':'dropBefore');
 }
});
currentSessionGrid.addEventListener('drop',event=>{
 const card=event.target.closest('[data-current-session-id]');
 if(!currentSessionDragId||!currentSessionDropId||!card||
    card.dataset.currentSessionId!==currentSessionDropId)return;
 event.preventDefault();
 const result=moveCurrentSessionCard(currentSessionDragId,currentSessionDropId,currentSessionDropAfter);
 if(result)$('current-session-order-status').textContent=currentSessionPositionMessage(currentSessionDragId,result.index,result.total);
 currentSessionSuppressClickUntil=Date.now()+300;
});
currentSessionGrid.addEventListener('dragend',event=>{
 event.target.closest('[data-current-session-id]')?.classList.remove('dragging');
 clearCurrentSessionDropMarkers();
 currentSessionDragId='';
 currentSessionSuppressClickUntil=Date.now()+300;
 renderCurrentSessions();
});
currentSessionGrid.addEventListener('keydown',event=>{
 if(!event.altKey||event.ctrlKey||event.metaKey||event.shiftKey)return;
 const card=event.target.closest('[data-current-session-id]');
 if(!card)return;
 const columns=Math.max(1,getComputedStyle(currentSessionGrid).gridTemplateColumns.trim().split(/\s+/).length);
 const delta={ArrowLeft:-1,ArrowRight:1,ArrowUp:-columns,ArrowDown:columns}[event.key];
 if(!delta)return;
 event.preventDefault();
 const id=card.dataset.currentSessionId,result=moveCurrentSessionCardBy(id,delta);
 if(!result)return;
 $('current-session-order-status').textContent=currentSessionPositionMessage(id,result.index,result.total);
 renderCurrentSessions();
 requestAnimationFrame(()=>focusCurrentSessionCard(id));
});

