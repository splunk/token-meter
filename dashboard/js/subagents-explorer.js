function subagentGroupHtml(group,index,parentSessions,issuesOnly=false){
 const parent=subagentParentPresentation(group.rootSessionId,group.project,parentSessions),selected=group.rows.some(row=>String(row?.id||'')===selectedSubagentId),open=selected||expandedSubagentRoot===group.rootSessionId||(expandedSubagentRoot===null&&index<1);
 const cost=group.costCovered?money(group.cost):'--',tokens=group.tokenCovered?compactNumber(group.knownTokens):'--',workTime=group.workTimeCovered===group.agents?durationShort(group.workTime):'--';
 const badges=[group.incomplete?`<span class="subagentSessionBadge chip tone-warn">${f(group.incomplete)} incomplete</span>`:'',group.working?`<span class="subagentSessionBadge chip">${f(group.working)} working</span>`:''].filter(Boolean).join('');
 const visibleRows=issuesOnly?group.rows.filter(row=>row?.activity_state==='incomplete'||Array.isArray(row?.attention)&&row.attention.length):group.rows;
 const maximumChildCost=Math.max(0,...group.rows.filter(row=>row?.cost_available===true).map(row=>Number(row.cost)||0));
 const children=visibleRows.map((row,rowIndex)=>{
  const identity=agentIdentityPresentation(row,rowIndex),state=String(row?.activity_state||'unknown'),model=row?.model&&row.model!=='unknown'?row.model:'model unavailable',costValue=row?.cost_available===true?money(row.cost):'--',tokenValue=row?.tokens_available===true?compactNumber(row.tokens):'tokens --',workTimeValue=row?.work_time_s==null?'work time --':`work time ${durationShort(row.work_time_s)}`,id=String(row?.id||''),isSelected=id&&id===selectedSubagentId,heat=subagentCostHeat(row.cost,maximumChildCost,row.cost_available);
  return `<button class="subagentChildRow${isSelected?' selected':''}" type=button data-subagent-id="${esc(id)}"${isSelected?' aria-current=true':''} aria-label="Inspect ${esc(identity.primary)}"><span class=subagentChildIdentity><strong><span>${esc(identity.primary)}</span></strong><small>${esc([identity.secondary,row.kind,tokenValue,workTimeValue].filter(Boolean).join(' · '))}</small></span><span class=subagentChildMetric>${esc(model)}</span><span class="agentState ${esc(state)}">${esc(state)}</span><span class="subagentChildMetric${heat?' subagentCostHeat':''}"${heat?` style="${heat}" title="Relative child cost in this session"`:''}>${esc(costValue)}</span></button>`;
 }).join('');
 const hiddenSiblings=group.agents-visibleRows.length,footer=issuesOnly&&hiddenSiblings?`<button class=subagentSiblingLink type=button data-subagent-show-session="${esc(group.rootSessionId)}">View ${f(hiddenSiblings)} other ${hiddenSiblings===1?'child':'children'} in Sessions</button>`:'',spawnedRunMetric=issuesOnly?'':`<span class=subagentSessionMetric><span>Spawned runs</span><b>${f(group.agents)}</b></span>`;
 return `<details class=subagentSessionGroup data-subagent-root="${esc(group.rootSessionId)}"${open?' open':''}><summary aria-label="${esc(`${parent.title}, ${group.agents} child agents, ${group.incomplete} incomplete`)}"><div class=subagentSessionIdentity><strong><span title="${esc(parent.title)}">${esc(parent.title)}</span>${badges}</strong><small title="${esc(parent.project)} · ${f(group.agents)} ${countWord(group.agents,'child agent')}">${esc(parent.project)} · ${f(group.agents)} ${countWord(group.agents,'child agent')}</small></div><div class=subagentSessionMetrics>${spawnedRunMetric}<span class=subagentSessionMetric><span>Child cost</span><b>${esc(cost)}</b></span><span class=subagentSessionMetric><span>Tokens</span><b>${esc(tokens)}</b></span><span class=subagentSessionMetric><span>Work time</span><b>${esc(workTime)}</b></span></div></summary><div class=subagentChildHeader><span>Agent</span><span>Model</span><span>Status</span><span>Est. cost</span></div>${children}${footer}</details>`;
}
function renderSubagentInspector(row,allRows,parentSessions=subagentParentSessionMap()){
 const inspector=$('subagent-inspector');
 if(!row){inspector.innerHTML='<div class="subagentInspectorEmpty emptyState"><div><svg viewBox="0 0 40 40" aria-hidden=true><circle cx="20" cy="11" r="5"/><circle cx="10" cy="29" r="4"/><circle cx="30" cy="29" r="4"/><path d="M20 16v6M10 25v-4h20v4"/></svg><strong>Select a child agent</strong><p>See its parent, cost, work time, and available retry evidence.</p></div></div>';return;}
 const identity=agentIdentityPresentation(row,0),parent=subagentParentPresentation(row.root_session_id,row.project,parentSessions),siblings=(allRows||[]).filter(item=>String(item?.root_session_id||'')===String(row.root_session_id||'')),siblingIndex=Math.max(0,siblings.findIndex(item=>String(item?.id||'')===String(row.id||'')))+1;
 const state=String(row.activity_state||'unknown'),model=row.model&&row.model!=='unknown'?row.model:'model unavailable',cost=row.cost_available===true?money(row.cost):'--',tokens=row.tokens_available===true?f(row.tokens):'--',workTime=row.work_time_s==null?'--':durationShort(row.work_time_s),attempts=Number(row.retries||0)+Number(row.failed_attempts||0),attention=Array.isArray(row.attention)&&row.attention.length;
 const peers=(allRows||[]).filter(item=>item?.cost_available===true&&item?.runtime===row.runtime&&item?.model===row.model&&item?.kind===row.kind&&Number(item?.depth||0)===Number(row.depth||0)).map(item=>Number(item.cost)||0).sort((a,b)=>a-b),middle=Math.floor(peers.length/2),peerMedian=!peers.length?null:peers.length%2?peers[middle]:(peers[middle-1]+peers[middle])/2,comparison=row.cost_available===true&&peerMedian>0&&peers.length>1?`${(Number(row.cost)/peerMedian).toFixed(1)}× cohort median · ${f(peers.length)} comparable agents`:'Comparable cost baseline unavailable';
 const reasons=attention?`<ul>${row.attention.map(reason=>`<li><strong>${esc(String(reason?.code||'').replaceAll('_',' '))}:</strong> ${esc(reason?.explanation||'Evidence threshold met.')}</li>`).join('')}</ul>`:'';
 const metric=(label,value)=>`<div class=subagentInspectorMetric><span>${esc(label)}</span><b>${esc(value)}</b></div>`;
 inspector.innerHTML=`<div class=subagentInspectorHead><div><h3>${esc(identity.primary)}</h3><p>${esc(identity.secondary?`Provider role · nickname ${identity.secondary}`:'Child-agent evidence')}</p></div><button class=subagentInspectorClose type=button data-subagent-close-detail aria-label="Close agent inspector">×</button></div><div class=subagentInspectorParent><span>Parent session</span><strong title="${esc(parent.title)}">${esc(parent.title)}</strong><small>${esc(parent.project)} · child ${f(siblingIndex)} of ${f(siblings.length)}</small></div><div class=subagentInspectorTags><span>${esc(appFilterLabel({provider:row.runtime}))}</span><span>${esc(model)}</span><span>${esc(row.kind||'child')}</span><span>${esc(state)}</span></div><div class=subagentInspectorGrid>${metric('Estimated cost',cost)}${metric('Tokens',tokens)}${metric('Work time',workTime)}${metric('Executions',f(row.executions||0))}${metric('Tool calls',f(row.tool_calls||0))}${metric('Retry evidence',f(attempts))}</div><div class=subagentInspectorSignals><strong>Cost and retry evidence</strong>${reasons}<p>${esc(comparison)}</p></div><button class="subagentOpenParent subagentInspectorAction" type=button data-subagent-open-parent="${esc(row.root_session_id)}">Open parent session</button>`;
}
function renderSubagentRoles(rows){
 const roles=buildSubagentRoleRows(rows);
 if(!roles.length)return '<div class="subagentResultsEmpty emptyState"><div><strong>No provider-reported roles match</strong>Adjust the filters or inspect Sessions for anonymous child agents.</div></div>';
 return `<div class=subagentRoleTable><div class=subagentRoleHeader><span>Role</span><span>Runs</span><span>Total spend</span><span>Incomplete</span><span>Median / P95</span><span>Tokens</span></div>${roles.map(row=>{const coverage=row.costCovered===row.agents?'complete':`${f(row.costCovered)} / ${f(row.agents)} covered`,tokenValue=row.tokenCovered?compactNumber(row.knownTokens):'--',totalSpend=row.costCovered?money(row.knownCost):'--';return `<div class=subagentRoleRow><div class=subagentRoleIdentity><strong>${esc(row.role)}</strong><small>${esc(appFilterLabel({provider:row.runtime}))} · ${esc(row.kind)} · ${esc(row.topModel)} · ${esc(coverage)}</small></div><div class=subagentRoleMetric><span>Runs</span><b>${f(row.agents)}</b></div><div class=subagentRoleMetric><span>Total spend</span><b>${esc(totalSpend)}</b></div><div class="subagentRoleMetric${row.incomplete?' warn':''}"><span>Incomplete</span><b>${pct(row.incompleteRate)}</b></div><div class=subagentRoleMetric><span>Median / P95</span><b>${row.medianCost==null?'--':`${money(row.medianCost)} / ${money(row.p95Cost)}`}</b></div><div class=subagentRoleMetric><span>Tokens</span><b>${esc(tokenValue)}</b></div></div>`;}).join('')}</div>`;
}
function renderSubagentExplorer(usage){
 const inventory=Array.isArray(usage?.inventory)?usage.inventory:[],filters=subagentFilters;
 if($('subagent-explorer').parentElement===$('subagent-page-home')){subagentView='roles';if(filters.role||filters.kind){filters.role='';filters.kind='';persistSubagentFilter('role');persistSubagentFilter('kind');}}
 else if(subagentView==='roles')subagentView='sessions';
 localStorage.setItem('tm_subagent_view',subagentView);
 const values=key=>[...new Set(inventory.map(row=>String(row?.[key]||'')).filter(Boolean))].sort((a,b)=>a.localeCompare(b,undefined,{sensitivity:'base'}));
 const runtimes=values('runtime'),projects=values('project'),models=values('model');
 if(filters.runtime&&!runtimes.includes(filters.runtime))filters.runtime='';
 if(filters.project&&!projects.includes(filters.project))filters.project='';
 if(filters.model&&!models.includes(filters.model))filters.model='';
 setLogSelectOptions($('subagent-filter-app'),'<option value="">All apps</option>'+runtimes.map(value=>`<option value="${esc(value)}">${esc(appFilterLabel({provider:value}))}</option>`).join(''),filters.runtime);
 setLogSelectOptions($('subagent-filter-project'),'<option value="">All projects</option>'+projects.map(value=>`<option value="${esc(value)}">${esc(projectFilterLabel(value))}</option>`).join(''),filters.project);
 setLogSelectOptions($('subagent-filter-model'),'<option value="">All models</option>'+models.map(value=>`<option value="${esc(value)}">${esc(value)}</option>`).join(''),filters.model);
 $('subagent-filter-search').value=filters.query;$('subagent-filter-status').value=filters.status;$('subagent-filter-signal').value=filters.signal;$('subagent-filter-time').value=filters.window;$('subagent-filter-sort').value=filters.sort;
 const advancedCount=[filters.model,filters.status!=='all',filters.signal!=='all',filters.sort!=='recent'].filter(Boolean).length;
 $('subagent-filter-advanced').hidden=!subagentAdvancedOpen;$('subagent-filter-more').setAttribute('aria-expanded',String(subagentAdvancedOpen));$('subagent-filter-more-count').textContent=advancedCount?String(advancedCount):'';
 const filterItems=subagentActiveFilterItems(filters);
 $('subagent-active-filters').innerHTML=filterItems.length?'<span>Filtered by</span>'+filterItems.map(item=>`<button class=subagentFilterChip type=button data-subagent-clear-filter="${esc(item.key)}" aria-label="Remove ${esc(item.label)} filter"><b>${esc(item.label)}: ${esc(item.value)}</b><i aria-hidden=true>×</i></button>`).join(''):'';
 $('subagent-filter-footer').hidden=!filterItems.length;
 $('subagent-filter-clear').disabled=!filterItems.length;
 const contextFilters={...filters,status:'all',signal:'all'},context=filterSubagentInventory(usage,contextFilters),result=filterSubagentInventory(usage,filters),summary=result.summary,exact=summary.complete,parentSessions=subagentParentSessionMap();
 const roleQuery=String(filters.query||'').trim().toLowerCase(),roleVisibleRows=context.rows.filter(row=>!roleQuery||String(row?.role||'').toLowerCase().includes(roleQuery));
 const issueGroups=groupSubagentSessions(context.rows,true,'priority'),sessionGroups=groupSubagentSessions(context.rows,false,filters.sort),roleRows=buildSubagentRoleRows(roleVisibleRows);
 $('subagent-view-issues-count').textContent=context.summary.complete?f(issueGroups.length):'--';$('subagent-view-sessions-count').textContent=context.summary.complete?f(sessionGroups.length):'--';$('subagent-view-roles-count').textContent=context.summary.complete?f(roleRows.length):'--';
 [...$('subagent-view-tabs').querySelectorAll('[data-subagent-view]')].forEach(button=>{const active=button.dataset.subagentView===subagentView;button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;if(active)$('subagent-results-panel').setAttribute('aria-labelledby',subagentView==='roles'?'subagent-results-title':button.id);});
 $('subagent-explorer-meta').hidden=false;$('subagent-explorer-meta').textContent=result.truncated?`${f(result.rows.length)} visible of ${f(result.inventoryCount)} child runs`:result.rows.length===result.inventoryCount?`${f(result.inventoryCount)} child runs`:`${f(result.rows.length)} of ${f(result.inventoryCount)} child runs`;
 const compact=(label,value,kind='',filter='')=>filter?`<button class="subagentCompactMetric actionable ${kind}" type=button data-subagent-summary-filter="${esc(filter)}" aria-pressed="${String(filter==='incomplete'?filters.status==='incomplete':filters.signal===filter)}"><span>${esc(label)}</span><b>${esc(value)}</b></button>`:`<span class="subagentCompactMetric ${kind}"><span>${esc(label)}</span><b>${esc(value)}</b></span>`;
 const signalLabels={peer_cost_outlier:'cost outliers',active_cost_concentration:'active concentrations',retry_pressure:'retry pressure'};
 const signalPills=Object.entries(context.signalCounts).filter(([,count])=>count>0).map(([code,count])=>compact(signalLabels[code]||code.replaceAll('_',' '),f(count),'warn',code));
 const incompletePill=compact('Incomplete',context.summary.complete?f(context.summary.incomplete):'--',context.summary.incomplete?'warn':'dim','incomplete');
  $('subagent-compact-summary').innerHTML=(subagentView==='roles'?[incompletePill]:[incompletePill,compact('Matching child cost',exact&&summary.costCovered?money(summary.cost):'--'),compact('Cost evidence',exact?`${f(summary.costCovered)} / ${f(summary.agents)}`:'--'),...signalPills]).join('');
  const warning=$('subagent-inventory-warning');warning.hidden=!result.truncated;warning.textContent=result.truncated?'Display limit reached — filtered totals withheld.':'';
  const unresolved=Number(usage?.totals?.unresolved_agents)||0,unresolvedCost=Number(usage?.totals?.unresolved_known_cost)||0,coverageNote=$('subagent-coverage-note');
  coverageNote.hidden=!unresolved;
  const coverageDetail='The parent session record is missing from the local history. These child runs are counted in global totals, but the hierarchy and spend-share rollups leave them out.';
  coverageNote.innerHTML=unresolved
   ?`<span class=fieldtip tabindex=0 aria-description="${esc(coverageDetail)}" data-tip="${esc(coverageDetail)}"><b>${f(unresolved)}</b> ${countWord(unresolved,'child run')}${unresolvedCost?` (${money(unresolvedCost)})`:''} ${unresolved===1?'has':'have'} no parent session; left out of the rollups above.</span>`
   :'';
 if(selectedSubagentId&&!result.rows.some(row=>String(row?.id||'')===selectedSubagentId))selectedSubagentId='';
 const copy={issues:['Issues','Incomplete runs and cost/retry signals.'],sessions:['Parent sessions','Child runs grouped by parent. Cost color is relative within each session.'],roles:['Roles','']}[subagentView];
 $('subagent-results-title').textContent=copy[0];$('subagent-results-description').textContent=copy[1];
 $('subagent-filter-search').placeholder=subagentView==='roles'?'Find a role or model':'Role, nickname, model, or project';
 const roleView=subagentView==='roles';$('subagent-workspace').classList.toggle('roleView',roleView);$('subagent-inspector').hidden=roleView;
 renderSubagentModelDistribution(result.rows,result.truncated);
 if(subagentView==='roles'){
  const economics=buildSubagentRoleEconomics(usage,filters),filteredRoleRows=result.rows.filter(row=>!roleQuery||String(row?.role||'').toLowerCase().includes(roleQuery)),roles=economics.reason?buildSubagentRoleRows(filteredRoleRows):economics.roles;$('subagent-results-count').textContent=`${f(roles.length)} ${countWord(roles.length,'role')}`;$('subagent-results').innerHTML=economics.reason==='filtered'?renderSubagentRoleEconomics(economics)+renderSubagentRoles(filteredRoleRows):renderSubagentRoleEconomics(economics);
 }else{
  const groups=groupSubagentSessions(result.rows,subagentView==='issues',subagentView==='issues'?'priority':filters.sort),agentCount=groups.reduce((total,group)=>total+group.agents,0);
  const signalCount=groups.reduce((total,group)=>total+group.attention,0),incompleteCount=groups.reduce((total,group)=>total+group.incomplete,0);
  $('subagent-results-count').textContent=subagentView==='issues'?`${f(groups.length)} ${countWord(groups.length,'session')} · ${f(incompleteCount)} incomplete · ${f(signalCount)} with cost/retry signals`:`${f(groups.length)} ${countWord(groups.length,'session')} · ${f(agentCount)} ${countWord(agentCount,'agent')}`;
  $('subagent-results').innerHTML=groups.length?groups.map((group,index)=>subagentGroupHtml(group,index,parentSessions,subagentView==='issues')).join(''):`<div class="subagentResultsEmpty emptyState"><div><strong>${subagentView==='issues'?'No matching runs':'No parent sessions match'}</strong>${subagentView==='issues'?'No incomplete children or cost/retry signals match these filters.':'Adjust the filters to restore child-agent sessions.'}</div></div>`;
 }
 const selected=result.rows.find(row=>String(row?.id||'')===selectedSubagentId)||null;
 renderSubagentInspector(selected,result.rows,parentSessions);
}
function selectSubagentUsageScope(usage,runtime,window,project,hasTextFilter){
 if(hasTextFilter)return {usage:null,reason:'filtered'};
 if(!usage||typeof usage!=='object')return {usage:null,reason:'unavailable'};
 runtime=String(runtime||'');
 window=String(window||'all');
 project=String(project||'');
 const scopes=Array.isArray(usage.scopes)?usage.scopes:[];
 if(scopes.length){
  const selected=scopes.find(item=>String(item?.runtime||'')===runtime&&String(item?.window||'all')===window&&String(item?.project||'')===project);
  if(selected)return {usage:selected,reason:null};
  return {usage:null,reason:usage.scope_truncated===true?'unavailable':'empty'};
 }
 return !runtime&&window==='all'&&!project?{usage,reason:null}:{usage:null,reason:'empty'};
}
function renderSubagentUsage(usage,filters=subagentFilters,hasRowFilters=false){
 const module=$('subagent-usage'),baseAgentCount=Number(usage?.totals?.agents)||0;
 if(!usage||baseAgentCount<1){module.hidden=true;return;}
 module.hidden=false;
 [...$('subagent-usage-dimension').querySelectorAll('[data-subagent-dimension]')].forEach(button=>{
  const active=button.dataset.subagentDimension===subagentUsageDimension;
  button.classList.toggle('on',active);
  button.setAttribute('aria-pressed',String(active));
 });
 [...$('subagent-usage-sort').querySelectorAll('[data-subagent-sort]')].forEach(button=>{
  const active=button.dataset.subagentSort===subagentUsageSort;
  button.classList.toggle('on',active);
  button.setAttribute('aria-pressed',String(active));
 });
 const selection=selectSubagentUsageScope(usage,filters.runtime,filters.window,filters.project,hasRowFilters);
 if(!selection.usage){
  $('subagent-usage-summary').innerHTML='';
  const message=selection.reason==='filtered'
   ?'Usage patterns are shown when role, nickname, model, status, and signal filters are clear.'
   :selection.reason==='empty'
    ?'No child-agent usage matches the selected project, app, and activity window.'
    :'Subagent statistics are unavailable for this filter.';
  $('subagent-usage-rows').innerHTML=`<div class="subagentUsageEmpty emptyState">${message}</div>`;
  return;
 }
 const scoped=selection.usage,totals=scoped.totals||{},agentCount=Number(totals.agents)||0;
 if(!agentCount){
  $('subagent-usage-summary').innerHTML='';
  $('subagent-usage-rows').innerHTML='<div class="subagentUsageEmpty emptyState">No child-agent usage matches the selected app and activity window.</div>';
  return;
 }
 const costSummary=totals.cost_available===true
  ? money(totals.cost)
  : Number(totals.known_cost)>0?`${money(totals.known_cost)} known`:'--';
 const tokenSummary=totals.tokens_available===true
  ? f(totals.tokens)
  : Number(totals.known_tokens)>0?`${f(totals.known_tokens)} known`:'--';
 const metric=(label,value,note)=>`<div class="subagentUsageStat metric"><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></div>`;
 const share=totals.agent_spend_share==null?'--':pct(totals.agent_spend_share),coveredGroups=Number(totals.group_cost_covered_sessions)||0,totalGroups=Number(totals.parent_sessions)||0,shareNote=coveredGroups===totalGroups?'of covered group spend':`${f(coveredGroups)} of ${f(totalGroups)} parent sessions have complete group cost`;
 const median=totals.median_cost==null?'--':money(totals.median_cost),p95=totals.p95_cost==null?'cost distribution unavailable':`P95 ${money(totals.p95_cost)} API-rate estimate`;
 const outputPerDollar=totals.output_per_dollar==null?'--':compactNumber(totals.output_per_dollar);
 const input=totals.input_tokens==null?'--':f(totals.input_tokens),output=totals.output_tokens==null?'--':f(totals.output_tokens),cache=totals.cache_read_tokens==null?'--':f(Number(totals.cache_read_tokens||0)+Number(totals.cache_write_tokens||0));
 $('subagent-usage-summary').innerHTML=[
  metric('Child agents',f(agentCount),`${f(totals.parent_sessions||0)} ${countWord(totals.parent_sessions||0,'parent session')}`),
  metric('Estimated cost',costSummary,totals.cost_available===true?'complete cost coverage':`${f(totals.cost_covered_agents||0)} of ${f(agentCount)} cost-covered`),
  metric('Tokens',tokenSummary,`${input} in · ${output} out · ${cache} cache`),
  metric('Child spend share',share,shareNote),
  metric('Median / agent',median,p95),
  metric('Output / $',outputPerDollar,totals.output_per_dollar==null?'complete output and cost required':'covered output tokens per dollar'),
  metric('Needs attention',f(totals.attention_agents||0),'server evidence signals'),
 ].join('');
 let rows=Array.isArray(scoped[subagentUsageDimension])?[...scoped[subagentUsageDimension]]:[];
 if(!rows.length){
  $('subagent-usage-rows').innerHTML=`<div class="subagentUsageEmpty emptyState">${subagentUsageDimension==='roles'?'No provider-reported roles are available.':'No child-agent usage is available for this grouping.'}</div>`;
  return;
 }
 const rowMetric=row=>({cost:Number(row?.known_cost)||0,tokens:Number(row?.known_tokens)||0,agents:Number(row?.agents)||0,output:Number(row?.output_per_dollar)||0}[subagentUsageSort]||0);
 rows.sort((left,right)=>rowMetric(right)-rowMetric(left)||String(left.id||'').localeCompare(String(right.id||'')));
 const maximum=Math.max(1,...rows.map(rowMetric));
 $('subagent-usage-rows').innerHTML=rows.map(row=>{
  const identity=subagentUsageIdentity(row,subagentUsageDimension),agents=Number(row.agents)||0,width=100*rowMetric(row)/maximum;
  const tokens=row.tokens_available===true?f(row.tokens):Number(row.known_tokens)>0?`${f(row.known_tokens)} known`:'--';
  const cost=row.cost_available===true?money(row.cost):Number(row.known_cost)>0?`${money(row.known_cost)} known`:'--';
  const efficiency=row.output_per_dollar==null?'--':compactNumber(row.output_per_dollar);
  return `<div class=subagentUsageRow><div class=subagentUsageIdentity><strong>${esc(identity.label)}</strong><small>${esc(identity.detail)}</small></div><div class=subagentUsageBar aria-hidden=true><i style="width:${Math.max(0,Math.min(100,width))}%"></i></div><div class=subagentUsageValue><span>Agents</span><b>${f(agents)}</b></div><div class=subagentUsageValue><span>Tokens</span><b>${esc(tokens)}</b></div><div class=subagentUsageValue><span>Cost</span><b>${esc(cost)}</b></div><div class=subagentUsageValue><span>Output / $</span><b>${esc(efficiency)}</b></div></div>`;
 }).join('');
}
$('subagent-usage-dimension')?.addEventListener('click',event=>{
 const dimension=event.target.closest('[data-subagent-dimension]')?.dataset.subagentDimension;
 if(!['runtimes','models','depths','kinds','roles'].includes(dimension))return;
 subagentUsageDimension=dimension;
 localStorage.setItem('tm_subagent_usage_dimension',dimension);
 renderSubagentExplorer(LATEST?.xsession?.agent_usage);
});
$('subagent-usage-sort')?.addEventListener('click',event=>{
 const sort=event.target.closest('[data-subagent-sort]')?.dataset.subagentSort;
 if(!['cost','tokens','agents','output'].includes(sort))return;
 subagentUsageSort=sort;
 localStorage.setItem('tm_subagent_usage_sort',sort);
 renderSubagentExplorer(LATEST?.xsession?.agent_usage);
});
const subagentFilterBindings={
 'subagent-filter-app':['runtime','tm_subagent_filter_app'],
 'subagent-filter-project':['project','tm_subagent_filter_project'],
 'subagent-filter-model':['model','tm_subagent_filter_model'],
 'subagent-filter-status':['status','tm_subagent_filter_status'],
 'subagent-filter-signal':['signal','tm_subagent_filter_signal'],
 'subagent-filter-time':['window','tm_subagent_filter_time'],
 'subagent-filter-sort':['sort','tm_subagent_filter_sort'],
};
const subagentFilterStorage={query:'tm_subagent_filter_search',role:'tm_subagent_filter_role',kind:'tm_subagent_filter_kind',runtime:'tm_subagent_filter_app',project:'tm_subagent_filter_project',model:'tm_subagent_filter_model',status:'tm_subagent_filter_status',signal:'tm_subagent_filter_signal',window:'tm_subagent_filter_time',sort:'tm_subagent_filter_sort'};
const subagentFilterDefaults={query:'',role:'',kind:'',runtime:'',project:'',model:'',status:'all',signal:'all',window:DEFAULT_TIME_FILTER_WINDOW,sort:'recent'};
function persistSubagentFilter(key){
 const storage=subagentFilterStorage[key],value=subagentFilters[key],fallback=subagentFilterDefaults[key];
 if(!storage)return;
 if(value===fallback)localStorage.removeItem(storage);else localStorage.setItem(storage,value);
}
function resetSubagentResultView(){subagentVisibleLimit=SUBAGENT_PAGE_SIZE;selectedSubagentId='';}
function normalizedSubagentNavigationState(view,filters){
 const allowed={status:['all','working','complete','incomplete','waiting','recent','unknown'],signal:['all','attention','peer_cost_outlier','active_cost_concentration','retry_pressure'],window:['today','yesterday','7d','30d','90d','month','last_month','all'],sort:['recent','cost','tokens','work','name']},rawSource=filters&&typeof filters==='object'?(filters.window==='24h'?{...filters,window:'today'}:filters):{},source=rawSource.sort==='elapsed'?{...rawSource,sort:'work'}:rawSource;
 const normalized={...subagentFilterDefaults};
 ['query','role','kind','runtime','project','model'].forEach(key=>normalized[key]=String(source[key]??subagentFilterDefaults[key]??''));
 Object.entries(allowed).forEach(([key,values])=>normalized[key]=values.includes(source[key])?source[key]:subagentFilterDefaults[key]);
 return {view:['issues','sessions','roles'].includes(view)?view:'roles',filters:normalized};
}
function restoreSubagentNavigationState(state){
 if(!state||typeof state!=='object')return false;
 const snapshot=normalizedSubagentNavigationState(state.view,state.filters);
 subagentView=snapshot.view;subagentFilters=snapshot.filters;localStorage.setItem('tm_subagent_view',subagentView);
 Object.keys(subagentFilterDefaults).forEach(persistSubagentFilter);expandedSubagentRoot=null;resetSubagentResultView();
 return true;
}
function openSubagentModelRuns(model,runtime){
 const current=normalizedSubagentNavigationState(subagentView,subagentFilters),next=normalizedSubagentNavigationState('sessions',{...subagentFilters,query:'',role:'',kind:'',model:String(model||''),runtime:String(runtime||subagentFilters.runtime||'')});
 history.replaceState({...history.state,subagent:current},'',location.href);
 history.pushState({...history.state,subagent:next},'',`/#sessions-subagents`);
 restoreSubagentNavigationState(next);
}
function openSubagentRoleRuns(role,runtime,kind){
 const current=normalizedSubagentNavigationState(subagentView,subagentFilters),next=normalizedSubagentNavigationState('sessions',{...subagentFilters,query:'',role:String(role||''),kind:String(kind||''),runtime:String(runtime||subagentFilters.runtime||'')});
 history.replaceState({...history.state,subagent:current},'',location.href);
 history.pushState({...history.state,subagent:next},'',`/#sessions-subagents`);
 restoreSubagentNavigationState(next);
}
window.addEventListener('popstate',event=>{
 if(!['subagents','sessions-subagents'].includes((location.hash||'').replace(/^#/,''))||!restoreSubagentNavigationState(event.state?.subagent))return;
 renderSubagentExplorer(LATEST?.xsession?.agent_usage);
});
$('subagent-filter-search').addEventListener('input',event=>{
 subagentFilters.query=event.target.value;persistSubagentFilter('query');resetSubagentResultView();renderSubagentExplorer(LATEST?.xsession?.agent_usage);
});
Object.entries(subagentFilterBindings).forEach(([id,[key,storage]])=>$(id).addEventListener('change',event=>{
 subagentFilters[key]=event.target.value;persistSubagentFilter(key);resetSubagentResultView();renderSubagentExplorer(LATEST?.xsession?.agent_usage);
}));
$('subagent-filter-more').onclick=()=>{
 subagentAdvancedOpen=!subagentAdvancedOpen;
 renderSubagentExplorer(LATEST?.xsession?.agent_usage);
 if(subagentAdvancedOpen)requestAnimationFrame(()=>$('subagent-filter-advanced').querySelector('select')?.focus());
};
$('subagent-view-tabs').onclick=event=>{
 const view=event.target.closest('[data-subagent-view]')?.dataset.subagentView;
 if(!['issues','sessions','roles'].includes(view))return;
 subagentView=view;localStorage.setItem('tm_subagent_view',view);selectedSubagentId='';expandedSubagentRoot=null;renderSubagentExplorer(LATEST?.xsession?.agent_usage);
};
$('subagent-view-tabs').onkeydown=event=>{
 if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
 event.preventDefault();
 const buttons=[...$('subagent-view-tabs').querySelectorAll('[data-subagent-view]')].filter(button=>button.dataset.subagentView!=='roles'),index=buttons.indexOf(event.target.closest('[data-subagent-view]'));
 if(index<0)return;
 const next=event.key==='Home'?0:event.key==='End'?buttons.length-1:(index+(event.key==='ArrowRight'?1:-1)+buttons.length)%buttons.length;
 buttons[next].focus();buttons[next].click();
};
$('subagent-compact-summary').onclick=event=>{
 const filter=event.target.closest('[data-subagent-summary-filter]')?.dataset.subagentSummaryFilter;
 if(!filter)return;
 subagentFilters=toggleSubagentSummaryFilter(subagentFilters,filter);
 persistSubagentFilter('status');persistSubagentFilter('signal');resetSubagentResultView();
 renderSubagentExplorer(LATEST?.xsession?.agent_usage);
};
$('subagent-active-filters').onclick=event=>{
 const key=event.target.closest('[data-subagent-clear-filter]')?.dataset.subagentClearFilter;
 if(!(key in subagentFilterDefaults))return;
 subagentFilters[key]=subagentFilterDefaults[key];persistSubagentFilter(key);resetSubagentResultView();
 renderSubagentExplorer(LATEST?.xsession?.agent_usage);
};
$('subagent-filter-clear').onclick=()=>{
 subagentFilters={...subagentFilterDefaults};
 ['tm_subagent_filter_search','tm_subagent_filter_role','tm_subagent_filter_kind',...Object.values(subagentFilterBindings).map(([,storage])=>storage)].forEach(key=>localStorage.removeItem(key));
 subagentAdvancedOpen=false;
 resetSubagentResultView();
 renderSubagentExplorer(LATEST?.xsession?.agent_usage);
};
$('subagent-results').onclick=event=>{
 const chart=event.target.closest('[data-subagent-role-chart]')?.dataset.subagentRoleChart;
 if(['spend','average','runs'].includes(chart)){subagentRoleChartMode=chart;localStorage.setItem('tm_subagent_role_chart',chart);renderSubagentExplorer(LATEST?.xsession?.agent_usage);return;}
 const roleButton=event.target.closest('[data-subagent-inspect-role]');
 if(roleButton){openSubagentRoleRuns(roleButton.dataset.subagentInspectRole,roleButton.dataset.subagentInspectRuntime,roleButton.dataset.subagentInspectKind);applyHashRoute();return;}
 const modelButton=event.target.closest('[data-subagent-inspect-model]');
 if(modelButton){openSubagentModelRuns(modelButton.dataset.subagentInspectModel,modelButton.dataset.subagentInspectRuntime);applyHashRoute();return;}
 const root=event.target.closest('[data-subagent-show-session]')?.dataset.subagentShowSession;
 if(root){
  subagentView='sessions';localStorage.setItem('tm_subagent_view',subagentView);selectedSubagentId='';expandedSubagentRoot=root;renderSubagentExplorer(LATEST?.xsession?.agent_usage);
  const group=[...$('subagent-results').querySelectorAll('[data-subagent-root]')].find(item=>item.dataset.subagentRoot===root);group?.scrollIntoView({block:'start'});return;
 }
 const row=event.target.closest('[data-subagent-id]');
 if(!row)return;
 selectedSubagentId=selectedSubagentId===row.dataset.subagentId?'':row.dataset.subagentId;
 renderSubagentExplorer(LATEST?.xsession?.agent_usage);
};
$('subagent-results').addEventListener('toggle',event=>{
 const group=event.target;if(!(group instanceof HTMLDetailsElement)||!group.matches('[data-subagent-root]'))return;
 if(group.open)expandedSubagentRoot=group.dataset.subagentRoot;else if(expandedSubagentRoot===group.dataset.subagentRoot)expandedSubagentRoot='';
},true);
$('subagent-inspector').onclick=event=>{
 const root=event.target.closest('[data-subagent-open-parent]')?.dataset.subagentOpenParent;
 if(root){selectSession(root,{returnScope:'subagents'});return;}
 if(event.target.closest('[data-subagent-close-detail]')){selectedSubagentId='';renderSubagentExplorer(LATEST?.xsession?.agent_usage);}
};
