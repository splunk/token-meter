function applyHashRoute(){
 const h=(location.hash||'').replace(/^#/,'');
 // Back on Learn by any route (button, browser Back, or the nav) means the way back is used up.
 if(tourReturn?.host==='learn'&&(h==='learn'||h==='learn-agent-access'))showTourReturn(null);
 const panel=PANEL_ALIAS[h]||h;
 const legacyGlobal=h==='global'||h==='global-logs'||/^global-(overview|evidence)$/.test(h);
 if(h==='subagents'||h==='sessions-subagents'){
  if(pinned){sessionRequest++;pinned=null;CURRENT=LATEST;}
  mountSubagentExplorer(h==='subagents'?'roles':'sessions');
  if(h==='sessions-subagents'){
   setSessionScope('subagents');
   showTab('session');
   showCurrentPanel('sessions');
  }else showTab('subagents');
  if(LATEST)renderSubagentExplorer(LATEST.xsession?.agent_usage);
  return;
 }
 if(h==='sessions'||h==='sessions-all'||h==='sessions-compare'||h==='sessions-branches'||h==='current-sessions'){
 const scope=h==='sessions-all'?'all':h==='sessions-compare'?'compare':h==='sessions-branches'?'branches':'current',route=sessionScopeRoute(scope);
 if((routedSessionId()||h==='current-sessions')&&history.replaceState)history.replaceState(null,'',`/#${route}`);
 if(pinned){sessionRequest++;pinned=null;CURRENT=LATEST;}
 setSessionScope(scope);
 showTab('session');
 showCurrentPanel('sessions');
 renderCurrentSessions();
 return;
}
if(h==='logs'||legacyGlobal){
 if(history.replaceState)history.replaceState(null,'','/#sessions-all');
 setSessionScope('all');
 showTab('session');
 showCurrentPanel('sessions');
 return;
 }
	 if(h==='daily')setHashRoute('spend',{replace:true,apply:false});
	 if(h==='settings-budgets'||h==='budgets')setHashRoute('spend-budgets',{replace:true,apply:false});
	 if(h==='spend'||h==='daily'||h==='spend-budgets'||h==='spend-discounts'||h==='settings-budgets'||h==='budgets'){
  showTab('spend');
  if(LATEST)renderSpendView(LATEST.xsession);
  if(h==='spend-discounts')openDiscounts();
  else if(h!=='spend'&&h!=='daily'){
   setBudgetEditorOpen(true);
   requestAnimationFrame(()=>$('spend-budgets').scrollIntoView({block:'start'}));
  }
  loadGoals();
	  return;
	 }
 if(h==='preview-settings'){
  setHashRoute('settings',{replace:true,apply:false});
  showTab('settings');
  if(LATEST)renderSettings(LATEST.xsession);
  loadAgentAccess();
  return;
 }
 const legacyCurrentRoutes={preview:'summary','preview-run':'summary','preview-activity':'summary','preview-tools':'summary'};
 if(legacyCurrentRoutes[h]){
  const canonical=legacyCurrentRoutes[h];
  setHashRoute(canonical,{replace:true,apply:false});
  showTab('session');
  showCurrentPanel(canonical);
  return;
 }
	 if(h==='models'||h==='frustration'){
	  if(h==='frustration')setHashRoute('models',{replace:true,apply:false});
	  showTab('models');
	  if(LATEST)renderActiveModelStats();
	  return;
	 }
 if(h==='goals')setHashRoute('efficiency-goals',{replace:true,apply:false});
 if(h.startsWith('efficiency-goals/')){
  showTab('efficiency');
  if(LATEST)renderActiveEfficiency();
  loadGoalDetail(decodeURIComponent(h.slice('efficiency-goals/'.length)));
  return;
 }
 if(h==='goals'||h==='efficiency-goals'){
  showGoalDetail(false);
  showTab('efficiency');
  if(LATEST)renderActiveEfficiency();
  loadGoals().then(()=>requestAnimationFrame(()=>$('efficiency-goals').scrollIntoView({block:'start'})));
  return;
 }
 if(h==='efficiency'){
  if(routedSessionId()){
   showTab('session');
   showCurrentPanel('summary');
  }else{
   showGoalDetail(false);
   showTab('efficiency');
   if(LATEST)renderActiveEfficiency();
   loadGoals();
  }
  return;
 }
 if(h==='work'){
  if(workSupported===false){location.hash='sessions';return;}
  showTab('work');
  loadWork();
  loadGoals();
  return;
 }
 if(h.startsWith('work-sessions')||h.startsWith('sessions-all?work=')){
  // The sessions behind a Work cell, shown in All sessions with a removable Work filter.
  applyWorkSessionFilter(new URLSearchParams(h.split('?')[1]||''));
  return;
 }
 if(h==='delivery')setHashRoute('git',{replace:true,apply:false});
 if(h==='git'||h==='delivery'){
  showTab('git');
  loadDelivery();
  loadGoals();
  return;
 }
 if(h==='capabilities'){
  showTab('capabilities');
  if(LATEST)renderCapabilities(LATEST.xsession?.capabilities);
  return;
 }
 if(h==='learn'||h==='learn-agent-access'||h.startsWith('learn-efficiency-')){
  showTab('learn');
  if(h==='learn-agent-access')showDeckSlide('learn',DECK_SLIDES.findIndex(slide=>slide.id==='agent'),{animate:false});
  else renderLearn(LATEST);
  if(h.startsWith('learn-efficiency-'))requestAnimationFrame(()=>{const target=$(h);if(target){target.scrollIntoView({block:'start'});target.focus({preventScroll:true});}});
  return;
 }
 if(h==='settings-agent-defaults'){
  setHashRoute('settings',{replace:true,apply:false});
  showTab('settings');
  if(LATEST)renderSettings(LATEST.xsession);
  loadAgentAccess();
  return;
 }
 if(h==='settings'||h==='model-pricing'||h==='settings-updates'||h==='settings-appearance'||h==='settings-work-insights'){
  showTab('settings');
  if(LATEST)renderSettings(LATEST.xsession);
  const card={'model-pricing':'model-pricing-settings','settings-updates':'update-settings','settings-work-insights':'work-insights-settings','settings-appearance':'appearance-settings'}[h];
  if(card)scrollToSettingsCard(card);
  else pendingSettingsScroll=null; // plain #settings: drop a scroll an earlier link left waiting
  loadWorkSettings();
  loadAgentAccess();
  return;
 }
if(!h||h==='session'){
  setSessionScope('current');
  showTab('session');
  if(routedSessionId()){
   showCurrentPanel('summary');
   setHashRoute('summary',{replace:true,apply:false});
  }else{
   showCurrentPanel('sessions');
   setHashRoute('sessions',{replace:true,apply:false});
  }
  return;
 }
 if(PANEL_KEYS.includes(panel)){
  showTab('session');
  showCurrentPanel(panel);
  if(h!==panel)setHashRoute(panel,{replace:true,apply:false});
 }
else{
  setSessionScope('current');
 showTab('session');
 showCurrentPanel('sessions');
 setHashRoute('sessions',{replace:true,apply:false});
 }
}
function applyLocationRoute(){
 applyHashRoute();
 const id=routedSessionId();
 if(id){sessionReturnScope=normalizeSessionScope(history.state?.sessionScope);selectSession(id,{updateUrl:false,show:$('view-session').classList.contains('on'),returnScope:sessionReturnScope});return;}
 if(pinned){
  pinned=null;
  if(LATEST&&$('view-session').classList.contains('on'))renderSession(LATEST);
 }
}
function mountSubagentExplorer(destination){
 const roles=destination==='roles',home=roles?$('subagent-page-home'):$('preview-surface-sessions'),explorer=$('subagent-explorer');
 if(explorer.parentElement!==home)home.appendChild(explorer);
 explorer.hidden=false;
 explorer.setAttribute('role',roles?'region':'tabpanel');
 if(roles){explorer.setAttribute('aria-label','Subagent roles');explorer.removeAttribute('aria-labelledby');}
 else{explorer.removeAttribute('aria-label');explorer.setAttribute('aria-labelledby','session-scope-subagents');}
 $('subagent-model-distribution').hidden=roles;
}
applyLocationRoute();
window.addEventListener('hashchange',applyHashRoute);
window.addEventListener('popstate',applyLocationRoute);
