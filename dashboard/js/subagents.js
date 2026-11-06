function mergeAllSessionInventory(inventory,liveSessions){
 // One row per trace file: the server's opaque trace key when present, else the session id.
 const key=row=>row?.key?compareKeyFor(row):sessionRowKey(row);
 const rows=new Map((inventory||[]).map(row=>[key(row),row]));
 (liveSessions||[]).forEach(row=>rows.set(key(row),row));
 return [...rows.values()];
}
function subagentUsageIdentity(row,dimension){
 const runtime=appFilterLabel({provider:row?.runtime});
 const kind=String(row?.kind||''),kindLabel=kind?kind[0].toUpperCase()+kind.slice(1):'All kinds';
 if(dimension==='models')return {label:row?.model||'Unknown model',detail:`${runtime} · ${kindLabel}`};
 if(dimension==='depths')return {label:`Depth ${Number(row?.depth)||0}`,detail:`${runtime} · ${kindLabel}`};
 if(dimension==='kinds')return {label:kindLabel,detail:runtime};
 if(dimension==='roles')return {label:row?.role||'Unreported role',detail:`${runtime} · ${kindLabel} · provider-reported`};
 return {label:runtime,detail:'all child-agent kinds'};
}
function applySubagentPreset(filters,preset){
 const next={...(filters||{})};
 if(preset==='incomplete'){next.status='incomplete';next.signal='all';}
 else if(preset==='attention'){next.status='all';next.signal='attention';}
 else if(preset==='all'){next.status='all';next.signal='all';}
 return next;
}
function subagentPresetForFilters(filters){
 const status=String(filters?.status||'all'),signal=String(filters?.signal||'all');
 if(status==='incomplete'&&signal==='all')return 'incomplete';
 if(status==='all'&&signal==='attention')return 'attention';
 if(status==='all'&&signal==='all')return 'all';
 return 'custom';
}
function toggleSubagentSummaryFilter(filters,key){
 const next={...(filters||{})},filter=String(key||'');
 if(filter==='incomplete'){
  next.status=next.status==='incomplete'?'all':'incomplete';
  next.signal='all';
 }else if(filter){
  next.status='all';
  next.signal=next.signal===filter?'all':filter;
 }
 return next;
}
function paginateSubagentRows(rows,limit){
 rows=Array.isArray(rows)?rows:[];
 const total=rows.length,shown=Math.min(total,Math.max(0,Number(limit)||0));
 return {rows:rows.slice(0,shown),shown,total,hasMore:shown<total,nextLimit:Math.min(total,shown+50)};
}
function subagentActiveFilterItems(filters){
 const items=[];
 if(filters.query)items.push({key:'query',label:'Search',value:filters.query});
 if(filters.role)items.push({key:'role',label:'Role',value:filters.role});
 if(filters.kind)items.push({key:'kind',label:'Kind',value:filters.kind});
 if(filters.runtime)items.push({key:'runtime',label:'App',value:appFilterLabel({provider:filters.runtime})});
 if(filters.project)items.push({key:'project',label:'Project',value:projectFilterLabel(filters.project)});
 if(filters.window!==DEFAULT_TIME_FILTER_WINDOW)items.push({key:'window',label:'Time',value:TIME_FILTER_LABELS[filters.window]||filters.window});
 if(filters.model)items.push({key:'model',label:'Model',value:filters.model});
 if(filters.status!=='all')items.push({key:'status',label:'Status',value:filters.status});
 if(filters.signal!=='all')items.push({key:'signal',label:'Signal',value:filters.signal==='attention'?'Cost/retry signal':String(filters.signal).replaceAll('_',' ')});
 if(filters.sort!=='recent')items.push({key:'sort',label:'Sort',value:filters.sort==='work'?'Work time':filters.sort});
 return items;
}
function filterSubagentInventory(usage,filters,nowSeconds){
 filters=filters||{};nowSeconds=nowSeconds==null?Date.now()/1000:Number(nowSeconds);
 const inventory=Array.isArray(usage?.inventory)?usage.inventory:[],query=String(filters.query||'').trim().toLowerCase();
 const runtime=String(filters.runtime||''),project=String(filters.project||''),model=String(filters.model||''),role=String(filters.role||''),kind=String(filters.kind||''),status=String(filters.status||'all'),signal=String(filters.signal||'all'),window=String(filters.window||'all'),sort=String(filters.sort||'recent');
 const {start:rangeStart,end:rangeEnd}=timeFilterBounds(window,Number(nowSeconds)*1000);
 const rows=inventory.filter(row=>{
  const reasons=Array.isArray(row?.attention)?row.attention:[];
  if(runtime&&String(row?.runtime||'')!==runtime)return false;
  if(project&&String(row?.project||'')!==project)return false;
  if(model&&String(row?.model||'')!==model)return false;
  if(role&&String(row?.role||'')!==role)return false;
  if(kind&&String(row?.kind||'')!==kind)return false;
  if(status!=='all'&&String(row?.activity_state||'unknown')!==status)return false;
  if(signal==='attention'&&!reasons.length)return false;
  if(signal!=='all'&&signal!=='attention'&&!reasons.some(reason=>String(reason?.code||'')===signal))return false;
  if(rangeStart&&Number(row?.last_activity_at||0)<rangeStart)return false;
  if(rangeEnd&&Number(row?.last_activity_at||0)>=rangeEnd)return false;
  if(query&&!['role','label','model','project','runtime','client'].some(key=>String(row?.[key]||'').toLowerCase().includes(query)))return false;
  return true;
 });
 const name=row=>String(row?.role||row?.label||row?.id||'').toLowerCase();
 const sorters={
  recent:(a,b)=>Number(b?.last_activity_at||0)-Number(a?.last_activity_at||0)||name(a).localeCompare(name(b)),
  cost:(a,b)=>Number(b?.cost||0)-Number(a?.cost||0)||Number(b?.last_activity_at||0)-Number(a?.last_activity_at||0),
  tokens:(a,b)=>Number(b?.tokens||0)-Number(a?.tokens||0)||Number(b?.last_activity_at||0)-Number(a?.last_activity_at||0),
  work:(a,b)=>Number(b?.work_time_s!=null)-Number(a?.work_time_s!=null)||Number(b?.work_time_s||0)-Number(a?.work_time_s||0)||Number(b?.last_activity_at||0)-Number(a?.last_activity_at||0),
  name:(a,b)=>name(a).localeCompare(name(b))||String(a?.id||'').localeCompare(String(b?.id||'')),
 };
 rows.sort(sorters[sort]||sorters.recent);
 const truncated=usage?.inventory_truncated===true,inventoryCount=Math.max(inventory.length,Number(usage?.inventory_count)||0),complete=!truncated;
 const reasons=rows.flatMap(row=>Array.isArray(row?.attention)?row.attention:[]),signalCounts={};
 reasons.forEach(reason=>{const code=String(reason?.code||'');if(code)signalCounts[code]=(signalCounts[code]||0)+1;});
 const summary=complete?{
  complete:true,agents:rows.length,parentSessions:new Set(rows.map(row=>String(row?.root_session_id||'')).filter(Boolean)).size,
  incomplete:rows.filter(row=>row?.activity_state==='incomplete').length,working:rows.filter(row=>row?.activity_state==='working').length,
  attention:rows.filter(row=>Array.isArray(row?.attention)&&row.attention.length).length,
  costCovered:rows.filter(row=>row?.cost_available===true).length,cost:rows.reduce((total,row)=>total+(row?.cost_available===true?Number(row?.cost)||0:0),0),
 }: {complete:false,agents:null,parentSessions:null,incomplete:null,working:null,attention:null,costCovered:null,cost:null};
 return {rows,summary,signalCounts,inventoryCount,truncated};
}
function subagentModelDistribution(rows){
 const groups=new Map();
 (Array.isArray(rows)?rows:[]).forEach(row=>{
  const runtime=String(row?.runtime||''),model=String(row?.model||''),key=`${runtime}\n${model}`;
  if(!groups.has(key))groups.set(key,{runtime,model,runs:0,costCovered:0,cost:0});
  const group=groups.get(key);group.runs++;
  if(row?.cost_available===true){group.costCovered++;group.cost+=Number(row.cost)||0;}
 });
 return [...groups.values()].sort((a,b)=>b.runs-a.runs||a.runtime.localeCompare(b.runtime)||a.model.localeCompare(b.model));
}
function renderSubagentModelDistribution(rows,truncated){
 const target=$('subagent-model-distribution');
 if(subagentView!=='sessions'){target.hidden=true;return;}
 target.hidden=false;
 const models=subagentModelDistribution(rows),maximum=Math.max(1,...models.map(item=>item.runs));
 const qualifier=truncated?'Visible runs only · inventory limit reached':'Matching runs';
 target.innerHTML=`<div class=subagentModelHead><h3>Model distribution</h3><small>${esc(qualifier)} · ${f(rows.length)} ${countWord(rows.length,'run')}</small></div>${models.length?`<div class=subagentModelRows>${models.map(item=>{const label=item.model||'Unknown model',app=appFilterLabel({provider:item.runtime})||'Unknown app',coverage=item.costCovered===item.runs?'':`${f(item.costCovered)} / ${f(item.runs)} cost-covered`;return `<div class=subagentModelRow><span class=subagentModelIdentity title="${esc(`${app} · ${label}`)}"><strong>${esc(label)}</strong> · ${esc(app)}</span><span class=subagentModelTrack aria-hidden=true><i style="width:${(item.runs/maximum*100).toFixed(1)}%"></i></span><span class=subagentModelNumber>${f(item.runs)}</span><span class=subagentModelCost>${item.costCovered?esc(money(item.cost)):'--'}${coverage?`<small>${esc(coverage)}</small>`:''}</span></div>`;}).join('')}</div>`:'<div class="subagentResultsEmpty emptyState">No matching models</div>'}`;
}
function renderSubagentExplorerLegacy(usage){
 const inventory=Array.isArray(usage?.inventory)?usage.inventory:[],filters=subagentFilters;
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
 $('subagent-filter-advanced').hidden=!subagentAdvancedOpen;
 $('subagent-filter-more').setAttribute('aria-expanded',String(subagentAdvancedOpen));
 $('subagent-filter-more-count').textContent=advancedCount?String(advancedCount):'';
 const filterItems=subagentActiveFilterItems(filters);
 $('subagent-active-filters').innerHTML=filterItems.length?'<span>Filtered by</span>'+filterItems.map(item=>`<button class=subagentFilterChip type=button data-subagent-clear-filter="${esc(item.key)}" aria-label="Remove ${esc(item.label)} filter"><b>${esc(item.label)}: ${esc(item.value)}</b><i aria-hidden=true>×</i></button>`).join(''):'';
 $('subagent-filter-footer').hidden=!filterItems.length;
 $('subagent-filter-clear').disabled=!filterItems.length;
 const triageResult=filterSubagentInventory(usage,applySubagentPreset(filters,'all')),triageExact=triageResult.summary.complete;
 $('subagent-triage-all').textContent=triageExact?f(triageResult.summary.agents):'--';
 $('subagent-triage-incomplete').textContent=triageExact?f(triageResult.summary.incomplete):'--';
 $('subagent-triage-attention').textContent=triageExact?f(triageResult.summary.attention):'--';
 const activePreset=subagentPresetForFilters(filters);
 [...$('subagent-triage').querySelectorAll('[data-subagent-preset]')].forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.subagentPreset===activePreset)));
 const result=filterSubagentInventory(usage,filters),summary=result.summary,exact=summary.complete;
 $('subagent-explorer-meta').hidden=false;$('subagent-explorer-meta').textContent=result.truncated?`${f(result.rows.length)} visible of ${f(result.inventoryCount)} child runs`:result.rows.length===result.inventoryCount?`${f(result.inventoryCount)} child runs`:`${f(result.rows.length)} of ${f(result.inventoryCount)} child runs`;
 const metric=(label,value,note)=>`<div class="subagentExplorerStat metric"><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></div>`;
 $('subagent-explorer-summary').innerHTML=[
  metric('Matching agents',exact?f(summary.agents):'--',exact?`${f(summary.incomplete)} incomplete · ${f(summary.working)} working`:'exact filtered total withheld'),
  metric('Parent sessions',exact?f(summary.parentSessions):'--','open an agent to inspect its parent'),
  metric('Estimated cost',exact&&summary.costCovered?money(summary.cost):'--','local API-rate estimate'),
  metric('Cost evidence',exact?`${f(summary.costCovered)} / ${f(summary.agents)}`:'--',exact?'agents with estimated cost available':'exact filtered total withheld'),
 ].join('');
 const warning=$('subagent-inventory-warning');warning.hidden=!result.truncated;warning.textContent=result.truncated?'Display limit reached — filtered totals withheld.':'';
 const insights=[
  ['Peer cost outlier','peer_cost_outlier','Covered estimated cost is at least $0.50 and 3× the median of at least three comparable peers.'],
  ['Active cost concentration','active_cost_concentration','A working child has at least $1.00 of covered estimated spend and at least 50% of covered group spend.'],
  ['Retry pressure','retry_pressure','Provider evidence reports at least three retries or failed attempts in the recent execution window.'],
 ];
 $('subagent-insights').innerHTML=insights.map(([label,code,description])=>`<button class=subagentInsight type=button data-subagent-signal="${esc(code)}" aria-pressed="${String(filters.signal===code)}"><strong><span>${esc(label)}</span><b>${triageExact?f(triageResult.signalCounts[code]||0):'--'}</b></strong><p>${esc(description)}</p></button>`).join('');
 if(selectedSubagentId&&!result.rows.some(row=>String(row?.id||'')===selectedSubagentId))selectedSubagentId='';
 const page=paginateSubagentRows(result.rows,subagentVisibleLimit);
 $('subagent-inventory-count').textContent=result.truncated?`${f(page.rows.length)} visible rows from a limited inventory`:`Showing ${f(page.rows.length)} of ${f(page.total)} ${countWord(page.total,'agent')}`;
 const evidenceMetric=(label,value)=>`<div class="subagentEvidenceMetric metric"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`;
 const rows=page.rows.map((row,index)=>{
  const identity=agentIdentityPresentation(row,index),attention=Array.isArray(row.attention)&&row.attention.length,state=String(row.activity_state||'unknown'),model=row.model&&row.model!=='unknown'?row.model:'model unavailable';
  const tokens=row.tokens_available===true?f(row.tokens):'--',cost=row.cost_available===true?money(row.cost):'--',workTime=row.work_time_s==null?'--':durationShort(row.work_time_s),secondary=[identity.secondary,row.kind].filter(Boolean).join(' · '),id=String(row.id||''),selected=id&&id===selectedSubagentId,evidenceId=`subagent-evidence-${index}`;
  let html=`<button class="subagentInventoryRow${attention?' attention':''}${selected?' selected':''}" type=button data-subagent-id="${esc(id)}" aria-expanded="${String(selected)}" aria-controls="${evidenceId}" aria-label="Inspect evidence for ${esc(identity.primary)}"><span class=subagentInventoryIdentity><strong><span>${esc(identity.primary)}</span>${attention?'<span class="agentAttentionBadge chip tone-warn">Needs attention</span>':''}</strong><small>${esc(secondary)}</small></span><span class=subagentInventoryProject title="${esc(row.project||'')}">${esc(projectFilterLabel(row.project)||'No project')}</span><span class=subagentInventoryMetric>${esc(model)}</span><span class="agentState ${esc(state)}">${esc(state)}</span><span class=subagentInventoryMetric>${esc(tokens)}</span><span class=subagentInventoryMetric>${esc(cost)}</span><span class=subagentInventoryMetric>${esc(workTime)}</span></button>`;
  if(selected){
   const reasons=attention?row.attention.map(reason=>`<li><strong>${esc(String(reason?.code||'').replaceAll('_',' '))}:</strong> ${esc(reason?.explanation||'Evidence threshold met.')}</li>`).join(''):'';
   const attempts=Number(row.retries||0)+Number(row.failed_attempts||0),runtime=appFilterLabel({provider:row.runtime});
   html+=`<div class=subagentEvidence id="${evidenceId}" role=region aria-label="Evidence for ${esc(identity.primary)}"><div class=subagentEvidenceHead><div><h4>${esc(identity.primary)}</h4><p>${esc(identity.secondary?`Provider role · nickname ${identity.secondary}`:'Child-agent evidence')}</p></div><button class=subagentEvidenceClose type=button data-subagent-close-detail aria-label="Close agent evidence">×</button></div><div class=subagentEvidenceContext><span>${esc(runtime)}</span><span>${esc(projectFilterLabel(row.project)||'No project')}</span><span>${esc(model)}</span><span>${esc(row.kind||'child')}</span><span>${esc(state)}</span></div><div class=subagentEvidenceGrid>${evidenceMetric('Estimated cost',cost)}${evidenceMetric('Tokens',tokens)}${evidenceMetric('Work time',workTime)}${evidenceMetric('Executions',f(row.executions||0))}${evidenceMetric('Tool calls',f(row.tool_calls||0))}${evidenceMetric('Retry evidence',f(attempts))}</div><div class=subagentEvidenceSignals><strong>${attention?'Why this needs attention':'Attention evidence'}</strong>${attention?`<ul>${reasons}</ul>`:'<p>No deterministic attention signal is present in the available local evidence.</p>'}</div><div class=subagentEvidenceActions><p>Only content-free local metadata is shown here.</p><button class=subagentOpenParent type=button data-subagent-open-parent="${esc(row.root_session_id)}">Open parent session</button></div></div>`;
  }
  return html;
 }).join('');
 $('subagent-inventory').innerHTML=result.rows.length?'<div class=subagentInventoryHeader><span>Agent</span><span>Project</span><span>Model</span><span>Status</span><span>Tokens</span><span>Estimated cost</span><span>Work time</span></div>'+rows:'<div class="subagentInventoryEmpty emptyState">No child agents match these filters.</div>';
 $('subagent-inventory-footer').hidden=!page.hasMore;
 $('subagent-inventory-more').textContent=page.hasMore?`Show ${f(Math.min(SUBAGENT_PAGE_SIZE,page.total-page.shown))} more · ${f(page.total-page.shown)} remaining`:'';
 const fineFilter=Boolean(filters.query||filters.model||filters.status!=='all'||filters.signal!=='all');
 renderSubagentUsage(usage,filters,fineFilter);
}
function groupSubagentSessions(rows,issuesOnly=false,sort='priority'){
 const grouped=new Map();
 (Array.isArray(rows)?rows:[]).forEach(row=>{
  const rootSessionId=String(row?.root_session_id||'');
  if(!rootSessionId)return;
  const group=grouped.get(rootSessionId)||{rootSessionId,rows:[],agents:0,attention:0,incomplete:0,working:0,cost:0,costCovered:0,knownTokens:0,tokenCovered:0,lastActivity:0,workTime:0,workTimeCovered:0,project:String(row?.project||'')};
  group.rows.push(row);group.agents+=1;
  if(Array.isArray(row?.attention)&&row.attention.length)group.attention+=1;
  if(row?.activity_state==='incomplete')group.incomplete+=1;
  if(row?.activity_state==='working')group.working+=1;
  if(row?.cost_available===true){group.cost+=Number(row.cost)||0;group.costCovered+=1;}
  if(row?.tokens_available===true){group.knownTokens+=Number(row.tokens)||0;group.tokenCovered+=1;}
  group.lastActivity=Math.max(group.lastActivity,Number(row?.last_activity_at)||0);
  if(row?.work_time_s!=null){group.workTime+=Number(row.work_time_s)||0;group.workTimeCovered+=1;}
  grouped.set(rootSessionId,group);
 });
 let groups=[...grouped.values()];
 if(issuesOnly){
  groups=groups.filter(group=>group.attention||group.incomplete);
  groups.forEach(group=>group.rows.sort((a,b)=>
   Number(b?.activity_state==='incomplete')-Number(a?.activity_state==='incomplete')||
   Number(Boolean(b?.attention?.length))-Number(Boolean(a?.attention?.length))||
   (Number(b?.cost)||0)-(Number(a?.cost)||0)||
   (Number(b?.last_activity_at)||0)-(Number(a?.last_activity_at)||0)
  ));
 }
 const sorters={
  priority:(a,b)=>b.attention-a.attention||b.incomplete-a.incomplete||b.cost-a.cost||b.lastActivity-a.lastActivity||a.rootSessionId.localeCompare(b.rootSessionId),
  recent:(a,b)=>b.lastActivity-a.lastActivity||b.attention-a.attention||a.rootSessionId.localeCompare(b.rootSessionId),
  cost:(a,b)=>b.cost-a.cost||b.costCovered-a.costCovered||b.lastActivity-a.lastActivity,
  tokens:(a,b)=>b.knownTokens-a.knownTokens||b.lastActivity-a.lastActivity,
  work:(a,b)=>Number(b.workTimeCovered===b.agents)-Number(a.workTimeCovered===a.agents)||b.workTime-a.workTime||b.workTimeCovered-a.workTimeCovered||b.lastActivity-a.lastActivity,
  name:(a,b)=>a.project.localeCompare(b.project)||a.rootSessionId.localeCompare(b.rootSessionId),
 };
 groups.sort(sorters[sort]||sorters.priority);
 return groups;
}
function buildSubagentRoleRows(rows){
 const grouped=new Map();
 (Array.isArray(rows)?rows:[]).forEach(row=>{
  const role=String(row?.role||'').trim();
  if(!role)return;
  const runtime=String(row?.runtime||''),kind=String(row?.kind||'child'),key=`${runtime}\n${kind}\n${role}`;
  const group=grouped.get(key)||{id:key,role,runtime,kind,agents:0,incomplete:0,attention:0,knownCost:0,costCovered:0,knownTokens:0,tokenCovered:0,costs:[],models:new Map()};
  group.agents+=1;
  if(row?.activity_state==='incomplete')group.incomplete+=1;
  if(Array.isArray(row?.attention)&&row.attention.length)group.attention+=1;
  if(row?.cost_available===true){const cost=Number(row.cost)||0;group.knownCost+=cost;group.costCovered+=1;group.costs.push(cost);}
  if(row?.tokens_available===true){group.knownTokens+=Number(row.tokens)||0;group.tokenCovered+=1;}
  const model=String(row?.model||'model unavailable');group.models.set(model,(group.models.get(model)||0)+1);
  grouped.set(key,group);
 });
 return [...grouped.values()].map(group=>{
  const costs=group.costs.slice().sort((a,b)=>a-b),middle=Math.floor(costs.length/2),medianCost=!costs.length?null:costs.length%2?costs[middle]:(costs[middle-1]+costs[middle])/2,p95Cost=!costs.length?null:costs[Math.max(0,Math.ceil(.95*costs.length)-1)];
  const topModel=[...group.models].sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0]))[0]?.[0]||'model unavailable';
  return {id:group.id,role:group.role,runtime:group.runtime,kind:group.kind,agents:group.agents,incomplete:group.incomplete,attention:group.attention,incompleteRate:group.agents?group.incomplete/group.agents:0,attentionRate:group.agents?group.attention/group.agents:0,knownCost:group.knownCost,costCovered:group.costCovered,medianCost,p95Cost,knownTokens:group.knownTokens,tokenCovered:group.tokenCovered,topModel};
 }).sort((a,b)=>b.knownCost-a.knownCost||b.agents-a.agents||a.role.localeCompare(b.role)||a.runtime.localeCompare(b.runtime));
}
function subagentRoleKey(row){return `${String(row?.runtime||'')}\n${String(row?.kind||'')}\n${String(row?.role||'')}`;}
function subagentRoleFineFilters(filters){
 return Boolean(filters?.model||(filters?.status&&filters.status!=='all')||(filters?.signal&&filters.signal!=='all'));
}
function subagentRoleDayRows(usage,filters,nowMs=Date.now()){
 let rows=(Array.isArray(usage?.role_days)?usage.role_days:[]).filter(row=>(!filters?.runtime||row?.runtime===filters.runtime)&&(!filters?.project||row?.project===filters.project));
 const query=String(filters?.query||'').trim().toLowerCase();
 if(query)rows=rows.filter(row=>String(row?.role||'').toLowerCase().includes(query));
 const dayCount={today:1,"7d":7,"30d":30,"90d":90}[filters?.window];
 if(['yesterday','month','last_month'].includes(filters?.window)){
  const range=modelRangeWindow(filters.window,new Date(nowMs));
  rows=rows.filter(row=>range.daySet.has(String(row?.day||'')));
 }else if(dayCount){
  const cutoff=new Date(nowMs);cutoff.setHours(0,0,0,0);cutoff.setDate(cutoff.getDate()-(dayCount-1));
  const year=cutoff.getFullYear(),month=String(cutoff.getMonth()+1).padStart(2,'0'),day=String(cutoff.getDate()).padStart(2,'0'),cutoffKey=`${year}-${month}-${day}`;
  rows=rows.filter(row=>String(row?.day||'')>=cutoffKey);
 }
 return rows.sort((a,b)=>String(a?.day||'').localeCompare(String(b?.day||''))||subagentRoleKey(a).localeCompare(subagentRoleKey(b)));
}
function subagentModelKey(row){return `${String(row?.runtime||'')}\n${String(row?.model||'')}`;}
function subagentModelDayRows(usage,filters,nowMs=Date.now()){
 let rows=(Array.isArray(usage?.model_days)?usage.model_days:[]).filter(row=>(!filters?.runtime||row?.runtime===filters.runtime)&&(!filters?.project||row?.project===filters.project));
 const query=String(filters?.query||'').trim().toLowerCase();
 if(query)rows=rows.filter(row=>String(row?.model||'').toLowerCase().includes(query));
 const dayCount={today:1,"7d":7,"30d":30,"90d":90}[filters?.window];
 if(['yesterday','month','last_month'].includes(filters?.window)){
  const range=modelRangeWindow(filters.window,new Date(nowMs));
  rows=rows.filter(row=>range.daySet.has(String(row?.day||'')));
 }else if(dayCount){
  const cutoff=new Date(nowMs);cutoff.setHours(0,0,0,0);cutoff.setDate(cutoff.getDate()-(dayCount-1));
  const cutoffKey=`${cutoff.getFullYear()}-${String(cutoff.getMonth()+1).padStart(2,'0')}-${String(cutoff.getDate()).padStart(2,'0')}`;
  rows=rows.filter(row=>String(row?.day||'')>=cutoffKey);
 }
 return rows.sort((a,b)=>String(a?.day||'').localeCompare(String(b?.day||''))||subagentModelKey(a).localeCompare(subagentModelKey(b)));
}
function buildSubagentModelTrends(usage,filters,nowMs=Date.now()){
 const selected=selectSubagentUsageScope(usage,filters?.runtime,filters?.window,filters?.project,false);
 if(!selected.usage)return {models:[],days:[]};
 const query=String(filters?.query||'').trim().toLowerCase(),matches=row=>row?.model&&row.model!=='unknown'&&(!query||String(row.model).toLowerCase().includes(query));
 const cohort=usage=>Array.isArray(usage?.model_runtimes)?usage.model_runtimes:[];
 const previous=new Map(cohort(selected.usage.comparison).filter(matches).map(row=>[subagentModelKey(row),row]));
 const relative=(value,baseline)=>value!=null&&baseline!=null&&baseline>0?(value-baseline)/baseline:null;
 const models=cohort(selected.usage).filter(matches).map(row=>{
  const prior=previous.get(subagentModelKey(row)),covered=Number(row?.cost_covered_agents||0),priorCovered=Number(prior?.cost_covered_agents||0);
  const complete=row?.cost_available===true&&covered===Number(row?.agents||0),priorComplete=prior?.cost_available===true&&priorCovered===Number(prior?.agents||0);
  return {...row,costChange:prior&&complete&&priorComplete?relative(Number(row.known_cost||0),Number(prior.known_cost||0)):null,
   averageCostChange:complete&&priorComplete&&covered&&priorCovered?relative(Number(row.known_cost||0)/covered,Number(prior.known_cost||0)/priorCovered):null,
   runChange:prior?Number(row.agents||0)-Number(prior.agents||0):null};
 }).sort((a,b)=>Number(b.known_cost||0)-Number(a.known_cost||0)||Number(b.agents||0)-Number(a.agents||0)||String(a.model).localeCompare(String(b.model)));
 return {models,days:subagentModelDayRows(usage,filters,nowMs),trendTruncated:usage?.model_days_truncated===true};
}
function renderSubagentModelTrends(trends){
 if(!trends?.models?.length)return '';
 const comparisonsAvailable=!trends.trendTruncated,change=value=>value==null?'--':`${value>0?'↑ ':value<0?'↓ ':''}${Math.abs(value*100).toFixed(0)}%`,changeClass=value=>value==null?'':value<0?' good':value>0?' warn':'';
 const rows=trends.models.map(row=>{
  const runs=Number(row.agents||0),covered=Number(row.cost_covered_agents||0),total=covered?money(row.known_cost||0):'--',average=covered?money(Number(row.known_cost||0)/covered):'--';
  const primary=subagentRoleChartMode==='runs'?f(runs):subagentRoleChartMode==='average'?average:total;
  const delta=subagentRoleChartMode==='runs'?(!comparisonsAvailable||row.runChange==null?'':`${row.runChange>0?'+':''}${f(row.runChange)} vs prior`):change(comparisonsAvailable?(subagentRoleChartMode==='average'?row.averageCostChange:row.costChange):null);
  const deltaClass=subagentRoleChartMode==='average'?changeClass(comparisonsAvailable?row.averageCostChange:null):'';
  const label={spend:covered===runs?'Spend':'Covered spend',average:covered===runs?'Cost / run':'Cost / covered run',runs:'Runs'}[subagentRoleChartMode]||'Cost / run';
  const distribution=row.median_cost==null||row.p95_cost==null?'--':`${money(row.median_cost)} / ${money(row.p95_cost)}`,incomplete=Number(row.incomplete_agents||0),app=appFilterLabel({provider:row.runtime});
  return `<div class=subagentRoleTrendRow><div class=subagentRoleIdentity><strong title="${esc(row.model)}">${esc(row.model)}</strong><small>${esc(appFilterLabel({provider:row.runtime}))} · ${esc(`${f(runs)} ${runs===1?'run':'runs'}`)}<br>${esc(`${f(covered)}/${f(runs)} priced`)}</small></div><div class="subagentRolePrimary${deltaClass}"><span>${esc(label)}</span><b>${esc(primary)}</b>${delta&&delta!=='--'?`<small>${esc(delta)}</small>`:''}</div>${renderSubagentTrendSparkline(trends.days,trends.trendTruncated,subagentModelKey,row,row.model)}<div class=subagentRoleMeta><span>Median / P95</span><b>${esc(distribution)}</b>${incomplete?`<span class="subagentRoleSignal warn">${f(incomplete)} incomplete</span>`:''}</div><button class=subagentRoleInspect type=button data-subagent-inspect-model="${esc(row.model)}" data-subagent-inspect-runtime="${esc(row.runtime)}" aria-label="${esc(`View runs for ${row.model} in ${app}`)}">View runs</button></div>`;
 }).join('');
 return `<div class=subagentRoleTrendHead><div><h4>Model trends</h4><p>Child runs by the model they ran on · same metric as above</p></div></div><div class=subagentRoleTrendList>${rows}</div>`;
}
function buildSubagentRoleEconomics(usage,filters,nowMs=Date.now()){
 if(subagentRoleFineFilters(filters))return {reason:'filtered',roles:[],days:[]};
 const selected=selectSubagentUsageScope(usage,filters?.runtime,filters?.window,filters?.project,false);
 if(!selected.usage)return {reason:selected.reason||'unavailable',roles:[],days:[]};
 const query=String(filters?.query||'').trim().toLowerCase(),matchesRole=row=>!query||String(row?.role||'').toLowerCase().includes(query);
 const roles=(Array.isArray(selected.usage.roles)?selected.usage.roles:[]).filter(matchesRole),previousRoles=(Array.isArray(selected.usage.comparison?.roles)?selected.usage.comparison.roles:[]).filter(matchesRole),previousByKey=new Map(previousRoles.map(row=>[subagentRoleKey(row),row]));
 if(!roles.length)return {reason:'empty',roles:[],days:[]};
 const totals=items=>{
  const runs=items.reduce((sum,row)=>sum+Number(row?.agents||0),0),costCovered=items.reduce((sum,row)=>sum+Number(row?.cost_covered_agents||0),0),costComplete=Boolean(runs)&&costCovered===runs&&items.every(row=>row?.cost_available===true),knownCost=items.reduce((sum,row)=>sum+Number(row?.known_cost||0),0);
  return {runs,costCovered,costComplete,cost:costCovered?knownCost:null,averageCost:costCovered?knownCost/costCovered:null,incomplete:items.reduce((sum,row)=>sum+Number(row?.incomplete_agents||0),0),attention:items.reduce((sum,row)=>sum+Number(row?.attention_agents||0),0)};
 };
 const current=totals(roles),previous=totals(previousRoles),hasPrevious=previousRoles.length>0;
 const relative=(value,baseline)=>value!=null&&baseline!=null&&baseline>0?(value-baseline)/baseline:null;
 const comparedRoles=roles.map(row=>{
  const prior=previousByKey.get(subagentRoleKey(row)),currentCovered=Number(row?.cost_covered_agents||0),priorCovered=Number(prior?.cost_covered_agents||0),currentComplete=row?.cost_available===true&&currentCovered===Number(row?.agents||0),priorComplete=prior?.cost_available===true&&priorCovered===Number(prior?.agents||0),currentAverage=currentCovered?Number(row.known_cost||0)/currentCovered:null,priorAverage=priorCovered?Number(prior.known_cost||0)/priorCovered:null;
  return {...row,previousAgents:prior?Number(prior.agents||0):null,previousCost:priorComplete?Number(prior.known_cost||0):null,costChange:prior&&currentComplete&&priorComplete?relative(Number(row.known_cost||0),Number(prior.known_cost||0)):null,averageCostChange:currentComplete&&priorComplete?relative(currentAverage,priorAverage):null,runChange:prior?Number(row.agents||0)-Number(prior.agents||0):null};
 }).sort((a,b)=>Number(b.known_cost||0)-Number(a.known_cost||0)||Number(b.agents||0)-Number(a.agents||0)||String(a.role).localeCompare(String(b.role)));
 return {reason:null,window:String(filters?.window||'all'),...current,previousRuns:hasPrevious?previous.runs:null,previousCost:hasPrevious?previous.cost:null,previousAverageCost:hasPrevious?previous.averageCost:null,previousIncomplete:hasPrevious?previous.incomplete:null,costChange:hasPrevious&&current.costComplete&&previous.costComplete?relative(current.cost,previous.cost):null,averageCostChange:hasPrevious&&current.costComplete&&previous.costComplete?relative(current.averageCost,previous.averageCost):null,runChange:hasPrevious?current.runs-previous.runs:null,roles:comparedRoles,days:subagentRoleDayRows(usage,filters,nowMs),trendTruncated:usage?.role_days_truncated===true,roleDayCount:Number(usage?.role_day_count||0),modelTrends:buildSubagentModelTrends(usage,filters,nowMs)};
}
function renderSubagentRoleSparkline(economics,role){
 return renderSubagentTrendSparkline(economics?.days,economics?.trendTruncated,subagentRoleKey,role,role.role);
}
function renderSubagentTrendSparkline(dayRows,truncated,keyFn,role,name){
 if(truncated)return '<div class=subagentRoleSparkEmpty>Trend unavailable · daily history was truncated</div>';
 const allRows=Array.isArray(dayRows)?dayRows:[],key=keyFn(role),roleRows=allRows.filter(row=>keyFn(row)===key),days=[...new Set(roleRows.map(row=>String(row?.day||'')).filter(Boolean))].sort();
 if(!days.length)return '<div class=subagentRoleSparkEmpty>Trend unavailable</div>';
 const points=new Map();
 roleRows.forEach(row=>{const day=String(row?.day||''),point=points.get(day)||{cost:0,runs:0,complete:true};point.cost+=Number(row?.known_cost||0);point.runs+=Number(row?.agents||0);point.complete=point.complete&&row?.cost_available===true;points.set(day,point);});
 if(subagentRoleChartMode!=='runs'&&[...points.values()].some(point=>!point.complete))return '<div class=subagentRoleSparkEmpty>Spend trend hidden: some runs have no price. Switch to Runs to see the trend.</div>';
 const value=day=>{const point=points.get(day);if(subagentRoleChartMode==='runs')return point.runs;if(subagentRoleChartMode==='average')return point.runs?point.cost/point.runs:0;return point.cost;},values=days.map(value),maximum=Math.max(.000001,...values),width=220,height=62,pad=5;
 const x=index=>days.length===1?width/2:pad+index*(width-2*pad)/(days.length-1),y=item=>height-pad-(item/maximum)*(height-2*pad),line=values.map((item,index)=>`${x(index).toFixed(1)},${y(item).toFixed(1)}`).join(' '),area=days.length>1?`M ${x(0).toFixed(1)} ${height-pad} L ${line.replaceAll(' ',', ')} L ${x(days.length-1).toFixed(1)} ${height-pad} Z`:'';
 const modeLabel={spend:'spend',average:'cost per run',runs:'runs'}[subagentRoleChartMode]||'spend',mark=days.length===1?`<circle cx="${x(0)}" cy="${y(values[0])}" r=3 fill=var(--accent)/>`:`<path d="${area}" style="fill:rgb(var(--signal-rgb)/.08)"/><polyline points="${line}" fill=none stroke=var(--accent) stroke-width=2.2 vector-effect=non-scaling-stroke/>`;
 return `<div class=subagentRoleSpark><svg viewBox="0 0 ${width} ${height}" preserveAspectRatio=none role=img aria-label="${esc(name)} ${esc(modeLabel)} trend">${mark}</svg><div class=subagentRoleSparkDates><span>${esc(days[0])}</span><span>${esc(days[days.length-1])}</span></div></div>`;
}
function renderSubagentRoleEconomics(economics){
 if(economics?.reason==='filtered')return '<div class=subagentRoleAnalyticsEmpty><strong>Role trends unavailable for these row filters</strong><p>Clear Model, Status, and Signal to compare exact role periods. The filtered role table remains available below.</p></div>';
 if(!economics||economics.reason)return `<div class="subagentResultsEmpty emptyState"><div><strong>No provider-reported roles match</strong>${economics?.reason==='unavailable'?'Exact role aggregates are unavailable for this scope.':'Adjust the app, project, or time range, or inspect Sessions for anonymous child agents.'}</div></div>`;
 const change=value=>value==null?'--':`${value>0?'↑ ':value<0?'↓ ':''}${Math.abs(value*100).toFixed(0)}%`,changeClass=value=>value==null?'':value<0?' good':value>0?' warn':'';
 const comparisonsAvailable=!economics.trendTruncated,comparisonUnavailable='Comparison unavailable: daily series truncated';
 const coverage=`${f(economics.costCovered)} / ${f(economics.runs)} cost-covered`,costComplete=economics.costCovered===economics.runs,baseline=!comparisonsAvailable?comparisonUnavailable:economics.previousRuns==null?'':economics.costChange==null?'Comparison unavailable':`${change(economics.costChange)} vs prior`;
 const kpi=(label,value,note,kind='')=>`<div class="subagentRoleKpi metric ${kind}"><span>${esc(label)}</span><strong>${esc(value)}</strong>${note?`<small>${esc(note)}</small>`:''}</div>`;
 const rows=economics.roles.map(row=>{
  const runs=Number(row.agents||0),covered=Number(row.cost_covered_agents||0),roleCoverage=`${f(covered)}/${f(runs)} priced`,total=covered?money(row.known_cost||0):'--',average=covered?money(Number(row.known_cost||0)/covered):'--',incomplete=Number(row.incomplete_agents||0);
  const primary=subagentRoleChartMode==='runs'?f(runs):subagentRoleChartMode==='average'?average:total;
  const delta=subagentRoleChartMode==='runs'?(!comparisonsAvailable||row.runChange==null?'':`${row.runChange>0?'+':''}${f(row.runChange)} vs prior`):change(comparisonsAvailable?(subagentRoleChartMode==='average'?row.averageCostChange:row.costChange):null);
  const deltaClass=subagentRoleChartMode==='average'?changeClass(comparisonsAvailable?row.averageCostChange:null):'';
  const label={spend:covered===runs?'Spend':'Covered spend',average:covered===runs?'Cost / run':'Cost / covered run',runs:'Runs'}[subagentRoleChartMode]||'Cost / run';
  const distribution=row.median_cost==null||row.p95_cost==null?'--':`${money(row.median_cost)} / ${money(row.p95_cost)}`;
  return `<div class=subagentRoleTrendRow><div class=subagentRoleIdentity><strong>${esc(row.role)}</strong><small>${esc(appFilterLabel({provider:row.runtime}))} · ${esc(row.kind||'child')} · ${esc(`${f(runs)} ${runs===1?'run':'runs'}`)}<br>${esc(roleCoverage)}</small></div><div class="subagentRolePrimary${deltaClass}"><span>${esc(label)}</span><b>${esc(primary)}</b>${delta&&delta!=='--'?`<small>${esc(delta)}</small>`:''}</div>${renderSubagentRoleSparkline(economics,row)}<div class=subagentRoleMeta><span>Median / P95</span><b>${esc(distribution)}</b>${incomplete?`<span class="subagentRoleSignal warn">${f(incomplete)} incomplete</span>`:''}</div><button class=subagentRoleInspect type=button data-subagent-inspect-role="${esc(row.role)}" data-subagent-inspect-runtime="${esc(row.runtime)}" data-subagent-inspect-kind="${esc(row.kind)}" aria-label="${esc(`View runs for ${row.role} in ${appFilterLabel({provider:row.runtime})}`)}">View runs</button></div>`;
 }).join('');
 const averageNote=!comparisonsAvailable?comparisonUnavailable:economics.previousRuns==null?'':economics.averageCostChange==null?'Comparison unavailable':`${change(economics.averageCostChange)} vs prior`,runsNote=!comparisonsAvailable?comparisonUnavailable:economics.runChange==null?'':`${economics.runChange>0?'+':''}${f(economics.runChange)} vs prior`;
 return `<div class=subagentRoleEconomics><div class=subagentRoleKpis>${kpi(costComplete?'Cost / run':'Cost / covered run',economics.averageCost==null?'--':money(economics.averageCost),costComplete?averageNote:coverage,changeClass(comparisonsAvailable?economics.averageCostChange:null))}${kpi(costComplete?'Role spend':'Covered role spend',economics.cost==null?'--':money(economics.cost),costComplete?baseline:coverage)}${kpi('Named runs',f(economics.runs),runsNote)}</div><div class=subagentRoleTrendHead><div><h4>Role trends</h4></div><div class="seg subagentRoleChartModes"><button class="${subagentRoleChartMode==='average'?'on':''}" type=button data-subagent-role-chart=average>Cost / run</button><button class="${subagentRoleChartMode==='spend'?'on':''}" type=button data-subagent-role-chart=spend>Spend</button><button class="${subagentRoleChartMode==='runs'?'on':''}" type=button data-subagent-role-chart=runs>Runs</button></div></div><div class=subagentRoleTrendList>${rows}</div>${renderSubagentModelTrends(economics.modelTrends)}<p class=subagentRoleEconomicsFoot>Local API-rate estimates · Cost per run is not a quality score.</p></div>`;
}
function subagentParentSessionMap(){
 const rows=[...(allSessionInventory||[]),...(LATEST?.xsession?.sessions||[]),...(LATEST?.xsession?.current_sessions||[])],result=new Map();
 rows.forEach(row=>{const id=String(row?.id||'');if(id&&!result.has(id))result.set(id,row);});
 return result;
}
function subagentParentPresentation(rootSessionId,project,parentSessions=subagentParentSessionMap()){
 const row=parentSessions.get(String(rootSessionId||'')),title=String(row?.title||row?.session_name||'').trim();
 return {title:title||'Parent session',project:projectFilterLabel(project||row?.project)||'No project',runtime:appFilterLabel(row||{})};
}
