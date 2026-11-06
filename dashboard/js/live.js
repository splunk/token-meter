
function selectSession(id,{updateUrl=true,replace=false,show=true,returnScope=sessionScope}={}){
 id=String(id||'').trim();
 if(!id)return Promise.resolve();
 sessionReturnScope=normalizeSessionScope(returnScope);
 if(updateUrl&&history.pushState)history[replace?'replaceState':'pushState']({sessionScope:sessionReturnScope,sessionListBack:!replace&&sessionListBackAvailable(sessionReturnScope)},'',sessionRoute(id));
 sessionRequest++;
 pinned=id;
 if(show)applyHashRoute();
 return refreshSelectedSession({show,notifyOnError:true});
}
function adoptXsession(holder,state){
 if(state?.xsession_unchanged){
  if(holder.snapshot&&state.xsession_revision===holder.revision){state.xsession=holder.snapshot;return state;}
  holder.snapshot=null;holder.revision='';
  return null;
 }
 holder.snapshot=state?.xsession||null;
 holder.revision=holder.snapshot?String(state?.xsession_revision||''):'';
 return state;
}
async function fetchLiveJson(url,holder,label){
 for(let attempt=0;attempt<2;attempt++){
  const response=await fetch(url+(url.includes('?')?'&':'?')+'xsession='+encodeURIComponent(holder.revision),{cache:'no-store'});
  if(!response.ok)throw new Error(`${label} request failed (${response.status}).`);
  const state=adoptXsession(holder,await response.json());
  if(state)return state;
 }
 throw new Error(`${label} request failed.`);
}
async function refreshSelectedSession({show=true,notifyOnError=false}={}){
 const id=pinned,request=sessionRequest;
 if(!id||selectedPollId===id)return null;
 selectedPollId=id;
 try{
  const state=await fetchLiveJson('/session?id='+encodeURIComponent(id)+'&live=1',LIVE_XSESSION.session,'Session');
  if(request!==sessionRequest||pinned!==id)return null;
  if(!state||!stateSessionId(state))throw new Error('Session was not found.');
  CURRENT=state;
  if(show&&currentPanel!=='sessions'&&$('view-session').classList.contains('on')){
   applyHashRoute();
   renderSession(state);
  }
  return state;
 }catch(error){
  if(request!==sessionRequest||pinned!==id)return null;
  if(notifyOnError){
   pinned=null;
   CURRENT=LATEST;
   if(routedSessionId()===id&&history.replaceState)history.replaceState(null,'',`/#${sessionScopeRoute(sessionReturnScope)}`);
   if(show)applyHashRoute();
   showAppToast({title:'Session could not be opened',body:error.message||String(error),kind:'warn',sticky:true});
  }
  return null;
 }finally{
  if(selectedPollId===id)selectedPollId='';
 }
}
function openCurrentSessions({replace=false}={}){
 return openSessionScope('current',{replace});
}
function followLatestSession({updateUrl=true,replace=false,show=true,preservePanel=false}={}){
 sessionRequest++; pinned=null; CURRENT=LATEST;sessionReturnScope='current';
 if(updateUrl&&history.pushState){
  const panel=preservePanel&&PANEL_KEYS.includes(sessionPanel)?sessionPanel:'summary';
  history[replace?'replaceState':'pushState'](null,'',`/#${panel}`);
 }
 if(show){
  applyHashRoute();
  if(LATEST)renderSession(LATEST);
 }
 return LATEST;
}
function goToLatestSession(){followLatestSession();}

let statePollBusy=false;
const LIVE_STATE_POLL_MS=1000;
const SELECTED_SESSION_POLL_MS=2000;
// Deep links into Settings: a card can still be hidden (Work insights wait for the server to say they are
// supported) or move down as cards above it fill in, so the scroll waits for it and settles once.
function scrollToSettingsCard(id){
 pendingSettingsScroll={id,since:Date.now(),until:Date.now()+60000,top:null,stable:0,timer:0};
 requestAnimationFrame(trySettingsScroll);
}
function trySettingsScroll(){
 const pending=pendingSettingsScroll;
 if(!pending)return;
 pending.timer=0;
 if(Date.now()>pending.until||!$('view-settings').classList.contains('on')){pendingSettingsScroll=null;return;}
 const card=$(pending.id);
 if(!card||card.hidden||!card.getClientRects().length)return; // shown later: applyWorkSupport retries
 const top=card.getBoundingClientRect().top;
 if(pending.top!==null&&Math.abs(top-pending.top)<4){
  if(++pending.stable>=2){pendingSettingsScroll=null;return;} // still for two checks in a row: settled
 }else{
  pending.stable=0;card.scrollIntoView({block:'start'});
 }
 pending.top=card.getBoundingClientRect().top;
 pending.timer=setTimeout(trySettingsScroll,600);
}
// The reader takes over once they scroll or click themselves; the keypress or click that opened the link doesn't count.
const SETTINGS_SCROLL_KEYS=['ArrowUp','ArrowDown','PageUp','PageDown','Home','End',' '];
['wheel','touchstart','mousedown','keydown'].forEach(type=>window.addEventListener(type,event=>{
 const pending=pendingSettingsScroll;
 if(!pending||Date.now()-pending.since<300)return;
 if(type==='keydown'&&!SETTINGS_SCROLL_KEYS.includes(event.key))return;
 clearTimeout(pending.timer);pendingSettingsScroll=null;
},{passive:true}));
function applyWorkSupport(supported){
 if(typeof supported!=='boolean'||supported===workSupported)return;
 workSupported=supported;
 $('tab-work').hidden=!supported;$('work-insights-settings').hidden=!supported;$('d-type-card').hidden=!supported;
 if(supported&&pendingSettingsScroll&&!pendingSettingsScroll.timer)requestAnimationFrame(trySettingsScroll);
 if(!supported&&/^#?(work|sessions-all\?work=)/.test(location.hash.replace(/^#/,'')))location.hash='sessions';
}
function applyLiveState(state){
 RUNTIME_CATALOG=state?.runtime_catalog||RUNTIME_CATALOG;
 defaultSessionBudget=Number(state?.xsession?.budgets?.default_session_budget||10);
 adoptFreshDiscounts(state);LATEST=state;
 applyWorkSupport(state?.work_insights_supported);
 renderMenubarSettings(state?.menubar_settings);
 renderCurrentSessions(LATEST);
 const sessionSurfaceActive=$('view-session').classList.contains('on');
 if(!pinned&&sessionSurfaceActive&&currentPanel!=='sessions')renderSession(LATEST);
 if(sessionSurfaceActive&&currentPanel==='sessions'&&sessionScope==='all'){
  const inventoryStale=!allSessionInventory||allSessionInventoryAt!==Number(LATEST.xsession?.generated_at||0);
  if(inventoryStale)loadAllSessions();
  else renderAllSessions(LATEST.xsession);
 }
 if(sessionSurfaceActive&&currentPanel==='sessions'&&sessionScope==='compare'&&compareIds.length&&!compareLoading&&Date.now()-compareLoadedAt>15000)loadComparison({force:true});
 if(sessionSurfaceActive&&currentPanel==='sessions'&&sessionScope==='subagents'){
  const inventoryStale=!allSessionInventory||allSessionInventoryAt!==Number(LATEST.xsession?.generated_at||0);
  renderSubagentExplorer(LATEST.xsession?.agent_usage);
  if(inventoryStale)loadAllSessions();
 }
 if($('view-subagents').classList.contains('on')){
  const inventoryStale=!allSessionInventory||allSessionInventoryAt!==Number(LATEST.xsession?.generated_at||0);
  renderSubagentExplorer(LATEST.xsession?.agent_usage);
  if(inventoryStale)loadAllSessions();
 }
	 if($('view-models').classList.contains('on'))renderActiveModelStats();
	 if($('view-daily').classList.contains('on'))renderSpendView(LATEST.xsession);
 if($('view-capabilities').classList.contains('on'))renderCapabilities(LATEST.xsession?.capabilities);
	 if($('view-efficiency').classList.contains('on'))renderActiveEfficiency();
	 if(['view-daily','view-efficiency','view-git','view-work'].some(id=>$(id).classList.contains('on'))&&Date.now()-goalsLoadedAt>30000)loadGoals(true);
	 if($('view-git').classList.contains('on')&&!deliveryLoading&&Date.now()-deliveryLastLoadedAt>60000)loadDelivery();
	 if($('view-work').classList.contains('on')&&!workLoading&&Date.now()-workLastLoadedAt>15000)loadWork();
	 if($('view-settings').classList.contains('on'))renderSettings(LATEST.xsession);
 if($('view-learn').classList.contains('on'))renderLearn(LATEST);
 if($('welcome-dialog').open)renderWelcome(LATEST);
 maybeAutoShowWelcome();
}
async function refreshLiveState(){
 if(statePollBusy)return;
 statePollBusy=true;
 try{
  applyLiveState(await fetchLiveJson('/state',LIVE_XSESSION.state,'State'));
 }catch(error){
  if($('view-capabilities').classList.contains('on')){
   if(capabilityLastGoodAt)setCapabilityAvailability('stale');
   else setCapabilityAvailability('unavailable');
  }
 }
 finally{statePollBusy=false;}
}
setInterval(refreshLiveState,LIVE_STATE_POLL_MS);
setInterval(()=>refreshSelectedSession({show:true}),SELECTED_SESSION_POLL_MS);
setInterval(refreshSoftwareUpdateStatus,60000);
setInterval(tickCacheRings,1000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden){refreshLiveState();refreshSelectedSession({show:true});}});
refreshLiveState();
refreshSoftwareUpdateStatus();
