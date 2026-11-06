function allSessionsView(all,{showChildren=false,app='',project='',rangeStart=0,rangeEnd=0,query=''}={}){
 const baseMatch=s=>{
  if(app&&appFilterGroup(s)!==app)return false;
  if(project&&projectFilterValue(s.project)!==project)return false;
  if(rangeStart&&(s.mtime||0)<rangeStart)return false;
  if(rangeEnd&&(s.mtime||0)>=rangeEnd)return false;
  return true;
 };
 const textMatch=s=>!query||[s.title,s.project,s.provider,s.label,s.child_agent_role,(s.models||[]).join(' ')].join(' ').toLowerCase().includes(query);
 const matching=(all||[]).filter(s=>baseMatch(s)&&textMatch(s));
 const children=matching.filter(s=>s.is_child_session);
 let rows=matching;
 if(!showChildren){
  // A matching child run surfaces its root card, so search still finds it.
  const childRoots=new Set(children.map(childRootId).filter(Boolean));
  rows=(all||[]).filter(s=>!s.is_child_session&&((baseMatch(s)&&textMatch(s))||childRoots.has(String(s.id))));
 }
 // Header figures cover exactly the matching sessions and child runs in both
 // modes; a root card surfaced only by a matching child adds no spend of its own.
 const statsRows=matching;
 const shownIds=new Set(rows.map(s=>String(s.id)));
 const underParent=children.filter(s=>shownIds.has(childRootId(s))).length;
 const matchedSessions=matching.length-children.length;
 return {
  rows,statsRows,showChildren,
  matchedSessions,
  total:(all||[]).filter(s=>!s.is_child_session).length,
  surfacedParents:showChildren?0:Math.max(0,rows.length-matchedSessions),
  children:children.length,
  childCost:children.filter(s=>metricAvailable(s,'cost')).reduce((a,b)=>a+(b.cost||0),0),
  underParent,
  unparented:children.length-underParent,
 };
}
function allSessionsCountText(view){
 const statsRows=view.statsRows;
 const cost=statsRows.reduce((a,b)=>a+(b.cost||0),0),localEstimates=statsRows.filter(row=>hasLocalEstimate(row)&&metricAvailable(row,'cost')).length,costCovered=statsRows.filter(row=>metricAvailable(row,'cost')).length;
 // Hidden subagent runs are still counted in totals, so report how many of the
 // matching runs sit under a shown parent card and how many have no parent.
 const parentNote=view.unparented
  ?`${f(view.underParent)} shown under parent cards · ${f(view.unparented)} with no parent session`
  :'listed under parents';
 const childNote=!view.showChildren&&view.children
  ?` · includes ${f(view.children)} subagent ${countWord(view.children,'run')} (${money(view.childCost)}) counted, ${parentNote}`
  :'';
 // Sessions and child runs are counted separately; spend and cost coverage
 // share one denominator: the matching sessions plus matching child runs.
 const runNote=view.showChildren&&view.children?` + ${f(view.children)} subagent ${countWord(view.children,'run')}`:'';
 const surfacedNote=view.surfacedParents?` · ${f(view.surfacedParents)} parent ${countWord(view.surfacedParents,'card')} shown for matching runs`:'';
 return `${f(view.matchedSessions)} of ${f(view.total)} sessions${runNote} · ${money(cost)} covered spend · ${f(costCovered)} of ${f(statsRows.length)} cost-covered${localEstimates?` · includes ${f(localEstimates)} Cursor local ${countWord(localEstimates,'estimate')}`:''}${childNote}${surfacedNote}`;
}
function renderAllSessions(xs){
 if(!xs)return;
 const generatedAt=Number(xs.generated_at||0);
 const liveSessionIds=new Set((xs.current_sessions||[]).map(sessionRowKey));
 setLogText($('g-hint'),generatedAt?`live · updated ${new Date(generatedAt*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'})}`:'recent activity');
 const all=$('view-session').classList.contains('on')&&sessionScope==='all'&&allSessionInventory?mergeAllSessionInventory(allSessionInventory,xs.sessions):xs.sessions||[];
 renderedAllSessions=new Map(all.map(row=>[sessionRowKey(row),row]));
 // Index every discovered child run, including the ones kept out of the list,
 // so each parent card can render its own children inline.
 indexChildSessions(allSessionInventory&&allSessionInventory.length?allSessionInventory:all);
 renderedAllSessionActions=xs.session_actions||{};
 const apps=[...new Map(all.map(session=>[
  appFilterGroup(session),
  appFilterLabel(session),
 ])).entries()].map(([id,label])=>({id,label})).sort((a,b)=>a.label.localeCompare(b.label,undefined,{sensitivity:'base'}));
 if(globalApp&&!apps.some(app=>app.id===globalApp)){
  globalApp='';
  localStorage.removeItem('tm_global_app');
 }
 setLogSelectOptions($('g-app'),'<option value="">All apps</option>'+apps.map(app=>`<option value="${esc(app.id)}">${esc(app.label)}</option>`).join(''),globalApp);
 const projects=[...new Set(all.map(s=>projectFilterValue(s.project)))].sort((a,b)=>projectFilterLabel(a).localeCompare(projectFilterLabel(b),undefined,{sensitivity:'base'}));
 if(globalProject&&!projects.includes(globalProject)){
 globalProject='';
 localStorage.removeItem('tm_global_project');
 }
 setLogSelectOptions($('g-project'),'<option value="">All projects</option>'+projects.map(project=>`<option value="${esc(project)}">${esc(projectFilterLabel(project))}</option>`).join(''),globalProject);
 paintGlobalFilters();
 const timeLabels=TIME_FILTER_LABELS;
 const appLabel=apps.find(app=>app.id===globalApp)?.label||globalApp;
 const activeFilters=[
  globalSearch.trim()&&['search',`Search: “${globalSearch.trim()}”`],
  globalApp&&['app',`App: ${appLabel}`],
  globalProject&&['project',`Project: ${projectFilterLabel(globalProject)}`],
  globalTime!==DEFAULT_TIME_FILTER_WINDOW&&['time',timeLabels[globalTime]||globalTime],
  workSessionFilter&&['work',`Work: ${workSessionFilter.label}${workSessionFilter.loading?' (loading)':''}`],
 ].filter(Boolean);
 const hideActiveFilters=!activeFilters.length;
 if($('g-active-filters').hidden!==hideActiveFilters)$('g-active-filters').hidden=hideActiveFilters;
 setLogHtml($('g-active-filters'),activeFilters.length
  ? `<span>Filtered by</span>${activeFilters.map(([key,label])=>`<button type=button data-clear-log-filter="${key}" title="Remove ${esc(label)} filter">${esc(label)}<b aria-hidden=true>×</b></button>`).join('')}`
  : '');
 const q=globalSearch.trim().toLowerCase();
 const {start:rangeStart,end:rangeEnd}=timeFilterBounds(globalTime);
 // A Work drill-down narrows the inventory to the trace files behind that Work cell.
 const workRows=workSessionFilter?all.filter(s=>workSessionFilter.keys.has(sessionRowKey(s))):all;
 const view=allSessionsView(workRows,{showChildren:globalShowChildren,app:globalApp,project:globalProject,rangeStart,rangeEnd,query:q});
 let sessions=view.rows;
 // Header stats count every matching run, so hiding subagent rows never removes
 // their spend from the figures that the Hidden control says are still counted.
 renderAllSessionStats(view.statsRows);
 const sorters={
   recent:(a,b)=>(b.mtime||0)-(a.mtime||0),
   cost:(a,b)=>(b.cost||0)-(a.cost||0)||(b.mtime||0)-(a.mtime||0),
   tokens:(a,b)=>(b.tokens||0)-(a.tokens||0)||(b.mtime||0)-(a.mtime||0),
	  executions:(a,b)=>(b.turns||0)-(a.turns||0)||(b.mtime||0)-(a.mtime||0),
	  wait:(a,b)=>Number(b.wait_time?.total_s||0)-Number(a.wait_time?.total_s||0)||(b.mtime||0)-(a.mtime||0),
 };
 sessions=sessions.slice().sort(sorters[globalSort]||sorters.recent);
 if(workSessionFilter?.loading)setLogText($('g-count'),'Loading the sessions behind this Work filter…');
 else if(workSessionFilter?.failed)setLogText($('g-count'),'The sessions behind this Work filter could not be loaded. Clear the filter or try again.');
 else setLogText($('g-count'),allSessionsCountText(view));
 const maxCost=Math.max(.001,...sessions.map(s=>s.cost||0));
 reconcileLogRows(sessions,maxCost,liveSessionIds);
 if(compareIds.length)syncCompareSelectionUi();
}
$('slist').onclick=event=>{
 const compareButton=event.target.closest('[data-compare-toggle]');
 if(compareButton){
  event.stopPropagation();
  if(compareButton.dataset.compareTitle)compareTitles.set(compareButton.dataset.compareToggle,compareButton.dataset.compareTitle);
  toggleCompareId(compareButton.dataset.compareToggle);
  return;
 }
 const childButton=event.target.closest('[data-open-session]');
 if(childButton){
  event.stopPropagation();
  event.preventDefault();
 const childId=childButton.dataset.openSession;
 const childRow=renderedAllSessions.get(childId);
 if(childRow)selectSession(sessionRowKey(childRow));
 return;
 }
 const identityButton=event.target.closest('[data-session-model-identity]');
 if(identityButton){
  event.stopPropagation();
  const row=renderedAllSessions.get(identityButton.closest('[data-id]')?.dataset.id);
  if(row)openSessionModelIdentityDialog(row);
  return;
 }
 const button=event.target.closest('[data-delete-session]');
 if(button){
  event.stopPropagation();
  const row=renderedAllSessions.get(button.dataset.deleteSession);
  if(row)openSessionDeleteDialog(row,renderedAllSessionActions,true,Boolean(LATEST&&sessionRowKey(row)===stateSessionKey(LATEST)));
  return;
 }
 const row=event.target.closest('[data-id]');
 if(row)selectSession(row.dataset.id);
};
$('slist').onkeydown=event=>{
 const row=event.target.closest('[data-id]');
 if(!row||!['Enter',' '].includes(event.key)||event.target.closest('button'))return;
 event.preventDefault();
 selectSession(row.dataset.id);
};
function sessionCoverageCopy(totals,sessionCount,localEstimates){
 const costCovered=Number(totals?.costCovered)||0,tokenCovered=Number(totals?.tokenCovered)||0,estimateNote=localEstimates?` · includes ${f(localEstimates)} Cursor local ${countWord(localEstimates,'estimate')}`:'';
 return {
  costNote:sessionCount?(costCovered?`${f(costCovered)} of ${f(sessionCount)} cost-covered sessions · ${money(Number(totals?.cost||0)/costCovered)}/covered session${estimateNote}`:'cost unavailable for matching sessions'):'no matching sessions',
  tokenTitle:tokenCovered?`${f(tokenCovered)} of ${f(sessionCount)} token-covered sessions${estimateNote}`:'token evidence unavailable',
 };
}
function renderAllSessionStats(sessions){
	 const localEstimates=sessions.filter(row=>hasLocalEstimate(row)&&metricAvailable(row,'cost')).length;
	 const totals=sessions.reduce((sum,row)=>{
	  sum.cost+=Number(row.cost||0);sum.input+=Number(row.input_tokens||0);sum.output+=Number(row.output_tokens||0);sum.executions+=Number(row.turns||0);sum.wait+=Number(row.wait_time?.total_s||0);sum.waitSamples+=Number(row.wait_time?.sample_count||0);sum.costCovered+=metricAvailable(row,'cost')?1:0;sum.tokenCovered+=metricAvailable(row,'tokens')?1:0;
	  return sum;
	 },{cost:0,input:0,output:0,executions:0,wait:0,waitSamples:0,costCovered:0,tokenCovered:0});
 const coverageCopy=sessionCoverageCopy(totals,sessions.length,localEstimates);
 setLogHtml($('lf-cost'),costValueHtml(money(totals.cost),Boolean(totals.costCovered)));
 setLogText($('lf-cost-note'),coverageCopy.costNote);
 setLogText($('lf-input'),totals.tokenCovered?compactNumber(totals.input):'--');setLogAttribute($('lf-input'),'title',totals.tokenCovered?`${f(totals.input)} reported or locally estimated input/context tokens · ${coverageCopy.tokenTitle}`:'input evidence unavailable');
 setLogText($('lf-output'),totals.tokenCovered?compactNumber(totals.output):'--');setLogAttribute($('lf-output'),'title',totals.tokenCovered?`${f(totals.output)} reported or locally estimated output tokens · ${coverageCopy.tokenTitle}`:'output evidence unavailable');
	 setLogText($('lf-executions'),f(totals.executions));
	 setLogText($('lf-executions-note'),sessions.length?`${(totals.executions/sessions.length).toFixed(1)} average / session`:'no matching executions');
	 setLogText($('lf-wait'),totals.waitSamples?waitFmt(totals.wait):'--');
	 setLogText($('lf-wait-note'),totals.waitSamples?`${waitFmt(totals.wait/totals.waitSamples)} average · ${f(totals.waitSamples)} timed ${countWord(totals.waitSamples,'request')}`:'no completed wait timing in filtered sessions');
 const models=new Map();
 sessions.forEach(session=>{
  let rows=session.model_stats||[];
  if(!rows.length&&(session.models||[]).length===1)rows=[{model:session.models[0],cost:session.cost,tokens:session.tokens,input_tokens:session.input_tokens,output_tokens:session.output_tokens,executions:session.turns,availability:session.availability}];
  rows.forEach(item=>{
   const model=item.model||'unknown',runtime=session.runtime||session.label||session.provider||'unknown',key=`${model}::${runtime}`,row=models.get(key)||{model,runtime,cost:0,tokens:0,input:0,output:0,executions:0,sessions:0,costCovered:0,tokenCovered:0,estimated:0};
   row.cost+=Number(item.cost||0);row.tokens+=Number(item.tokens||0);row.input+=Number(item.input_tokens||0);row.output+=Number(item.output_tokens||0);row.executions+=Number(item.executions||0);row.sessions+=1;row.costCovered+=metricAvailable(item,'cost')?1:0;row.tokenCovered+=metricAvailable(item,'tokens')?1:0;row.estimated+=hasLocalEstimate(session)?1:0;models.set(key,row);
  });
 });
 const ranked=[...models.values()].sort((a,b)=>b.cost-a.cost||b.tokens-a.tokens||a.model.localeCompare(b.model));
 setLogText($('lf-model-count'),`${f(ranked.length)} ${countWord(ranked.length,'model')}`);
 setLogHtml($('lf-models'),ranked.length?ranked.slice(0,4).map(row=>`<div class=logModelRow><div><div class=logModelName title="${esc(row.model)} · ${esc(row.runtime)}">${esc(row.model)} · ${esc(row.runtime)}</div><div class=logModelMeta>${f(row.sessions)} ${countWord(row.sessions,'session')} · ${f(row.executions)} execs · ${row.tokenCovered?compactNumber(row.input)+' in · '+compactNumber(row.output)+' out'+(row.estimated?' · includes local estimates':''):'token evidence unavailable'}</div></div><div class=logModelValue><b class=mono>${costValueHtml(money(row.cost),Boolean(row.costCovered))}</b><span>${row.costCovered?pct(totals.cost?row.cost/totals.cost:0)+' of covered cost'+(row.estimated?' · API-rate estimate':''):'billing unavailable'}</span></div></div>`).join('')+(ranked.length>4?`<div class=logModelEmpty>+${f(ranked.length-4)} more ${countWord(ranked.length-4,'model runtime')}</div>`:''):'<div class=logModelEmpty>No model data for these filters</div>');
}

