
function capabilityCounts(capabilities,key){
 const counts=capabilities?.[key]||{},loaded=Number.isInteger(counts.loaded)?counts.loaded:null;
 return {loaded,used:Math.max(0,Number(counts.used)||0),basis:loaded==null?'unavailable':counts.basis==='configured'?'configured':'session'};
}
function capabilityRatio(counts){
 return `${f(counts.used)}/${counts.loaded==null?'—':f(counts.loaded)}${counts.basis==='configured'?'*':''}`;
}
function capabilitySummaryText(capabilities){
 return `Skills ${capabilityRatio(capabilityCounts(capabilities,'skills'))} · MCP ${capabilityRatio(capabilityCounts(capabilities,'mcp_servers'))}`;
}
const CAPABILITY_SUMMARY_TIP='Used / loaded skills and MCP servers. * means configured now, not recorded by this session. — means unavailable.';
function logRowRenderKey(s,active){
 // Child subagent rows are part of this row's content, so a change in the
 // children must invalidate the cached innerHTML.
 return JSON.stringify([active,compareIds.indexOf(compareKeyFor(s)),compareIds.length>=COMPARE_MAX,sessionDeleteAvailable(s,renderedAllSessionActions),s.subagent,s.client,s.provider,s.label,s.title,s.project,s.branch,s.last,s.start,s.wait_time,s.turns,s.cost,s.availability,s.models,s.model_identity,s.tokens,s.provenance,s.usage_basis,s.token_estimate,s.throughput,s.cost_approx,s.capabilities,childAgentsFor(s).map(c=>[c.id,c.cost,c.tokens,c.title,c.last,c.child_agent_role,c.child_depth,c.availability,c.models])]);
}
function logRowContent(s){
 const rowCost=metricAvailable(s,'cost'),localEstimate=hasLocalEstimate(s),avg=rowCost&&s.turns?money((s.cost||0)/s.turns)+estimateSuffix(s):'--';
 const wait=s.wait_time||{},waitText=wait.available?`${waitFmt(wait.total_s)} wait · ${waitFmt(wait.avg_s)} avg`:'wait unavailable';
 const identity=s.model_identity||{},unresolvedIdentity=identity.source==='unresolved',assignedIdentity=identity.source==='user_assigned',identityLabel={verified_parent:' · parent verified',verified_history:' · verified history',user_assigned:' · assigned'}[identity.source]||'';
 const candidates=Array.isArray(identity.candidates)?identity.candidates:[];
 const unresolvedModelText=identity.kind==='auto_review'?'Auto-review · model unconfirmed':(candidates.length?`${candidates.join(', ')} · configured alias candidate`:`${identity.observed_label||'Hermes model'} · unconfirmed`);
 const modelText=unresolvedIdentity?unresolvedModelText:`${(s.models||[]).join(', ')||'--'}${identityLabel}`,throughput=s.throughput||{},speed=throughput.available?` · ${speedFmt(throughput.output_tps)} ${localEstimate?'visible out tok/s est':'out tok/s'}`:'';
 const identityAction=identity.assignable&&identity.key?`<button class=sessionModelAssign data-session-model-identity="${esc(identity.key)}" type=button>${assignedIdentity?'Change model':(identity.kind==='missing_model'?'Add model':'Assign model')}</button>`:'';
 return `${compareToggleHtml(s)}<div><span class="badge app ${appBadgeClass(s)}">${runtimeMarkSvg(s)}${esc(s.label||s.provider)}</span></div>
  <div class=sMain><div class=t>${s.subagent?'<span class="badge subagentTag">Subagent</span> ':''}${esc(s.title)}</div><div class=meta title="${esc(`${waitText} · ${avg}/exec${speed} · ${CAPABILITY_SUMMARY_TIP}`)}">${esc(s.project||'local')} · ${esc(s.last||s.start||'')} · ${esc(capabilitySummaryText(s.capabilities))}${s.branch?` ${branchChipHtml(s.branch,{project:s.project})}`:''}</div><div class=sbar><i></i></div>${childAgentSubsection(s)}</div>
  <div class="badge models">${esc(modelText)}</div>
  <div class=smetrics><div class="c mono" title="${esc(`${rowCost?(localEstimate?'local estimate':'cost'):(unresolvedIdentity?'model unconfirmed · billing unavailable':'billing unavailable')} · ${f(s.turns)} execs`)}">${costValueHtml(money(s.cost)+(s.cost_approx?' est':''),rowCost)}</div>${identityAction}</div>
  <div class=sactions>${sessionDeleteAvailable(s,renderedAllSessionActions)?`<button class="sessionDelete sessionDeleteIcon" data-delete-session="${esc(sessionRowKey(s))}" type=button aria-label="Delete session" title="Delete session"><svg viewBox="0 0 24 24" aria-hidden=true><path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/></svg></button>`:''}</div>`;
}
const CHILD_SECTION_LIMIT=6;
function childAgentsFor(s){
 const children=childSessionMap.get(String(s?.id||''));
 return Array.isArray(children)?children:[];
}
function childRootId(row){return row?.is_child_session?String(row.root_session_id||''):'';}
function indexChildSessions(rows){
 // Every descendant is keyed by its root session, so a nested run still renders
 // under the root card when its intermediate parent is itself a hidden child.
 childSessionMap=new Map();
 (rows||[]).forEach(row=>{
  if(!row?.is_child_session)return;
  const key=childRootId(row);
  if(!key)return;
  if(!childSessionMap.has(key))childSessionMap.set(key,[]);
  childSessionMap.get(key).push(row);
 });
 childSessionMap.forEach(list=>list.sort((a,b)=>(b.cost||0)-(a.cost||0)||(b.mtime||0)-(a.mtime||0)));
 return childSessionMap;
}
function childAgentSubsection(s){
 const children=childAgentsFor(s);
 if(!children.length)return '';
 const shown=children.slice(0,CHILD_SECTION_LIMIT);
 const hidden=children.length-shown.length;
 const coveredCost=children.filter(row=>metricAvailable(row,'cost'));
 const totalCost=coveredCost.reduce((a,b)=>a+(b.cost||0),0);
 const coveredTokens=children.filter(row=>metricAvailable(row,'tokens'));
 const totalTokens=coveredTokens.reduce((a,b)=>a+(b.tokens||0),0);
 const stats=[
  `<b>${f(children.length)}</b> ${countWord(children.length,'run')}`,
  coveredCost.length?`<b>${money(totalCost)}</b> covered spend`:'spend unavailable',
  totalTokens?`<b>${f(totalTokens)}</b> tok`:'',
 ].filter(Boolean).join(' · ');
 const rows=shown.map(child=>{
  const childCost=metricAvailable(child,'cost');
  const childTokens=metricAvailable(child,'tokens');
  const role=child.child_agent_role?`<span class=subagentRole>${esc(child.child_agent_role)}</span>`:'';
  const roleText=child.child_agent_role||'Unreported role';
  const title=child.title||'Subagent run';
  const depth=Number(child.child_depth)||1;
  const detail=[roleText,depth>1?`nested depth ${f(depth)}`:'',(child.models||[]).join(', ')||child.primary_model||'--',child.last||''].filter(Boolean).join(' · ');
  const costHtml=childCost
   ?(child.cost>0?costValueHtml(money(child.cost)+(child.cost_approx?' est':''),true):`<span class=subagentZero title="Measured free-tier price">${money(0)}</span>`)
   :'<span class=subagentZero>cost unavailable</span>';
  return `<button class=subagentRow type=button data-open-session="${esc(sessionRowKey(child))}" aria-label="Open subagent run ${esc(title)}">
 <span class=subagentMain><span class=t>${role}${esc(title)}</span><span class=meta>${esc(detail)}</span></span>
 <span class=subagentTokens>${childTokens?f(child.tokens)+' tok':'tokens --'}</span>
 <span class=subagentCost>${costHtml}</span></button>`;
 }).join('');
 const more=hidden>0?`<div class=subagentMore>${f(hidden)} more ${countWord(hidden,'run')} not shown</div>`:'';
 return `<div class=subagents><div class=subagentsHead><span class=subagentsTitle>Subagents <b>${f(children.length)}</b></span><span class=subagentsStats>${stats}</span></div><div class=subagentList>${rows}</div>${more}</div>`;
}
function reconcileLogRows(sessions,maxCost,liveSessionIds=new Set()){
 const root=$('slist');
 const interactingLogRow=forceAllSessionRowRefresh?null:root.querySelector('.srow:hover,.srow:focus-within');
 if(interactingLogRow)return;
 forceAllSessionRowRefresh=false;
 if(!sessions.length){
  setLogHtml(root,'<div class="signalEmpty emptyState">No sessions match the selected filters</div>');
  return;
 }
 logHtmlCache.delete(root);
 const existing=new Map([...root.children].filter(row=>row.dataset.rowKey).map(row=>[row.dataset.rowKey,row]));
 const desired=new Set();
 let cursor=root.firstElementChild;
 const pinnedKey=pinned?(renderedAllSessions.has(pinned)?pinned:(stateSessionId(CURRENT)===pinned?stateSessionKey(CURRENT):'')):'';
 sessions.forEach(s=>{
  const key=sessionRowKey(s),rowKey=compareKeyFor(s),active=pinned?key===pinnedKey:Boolean(LATEST&&key===stateSessionKey(LATEST));
  desired.add(rowKey);
  let row=existing.get(rowKey);
  if(!row){
   row=document.createElement('div');
   row.dataset.id=key;
   row.dataset.rowKey=rowKey;
   row.setAttribute('role','button');
   row.tabIndex=0;
  }
  const live=liveSessionIds.has(key),renderKey=logRowRenderKey(s,active),restoreDeleteFocus=document.activeElement?.dataset.deleteSession===key;
  const hasChildren=childAgentsFor(s).length>0;
  const compareIndex=compareIds.indexOf(rowKey);
  const className=`srow${active?' active':''}${live?' live':''}${hasChildren?' hasChildren':''}${compareIndex>=0?' compareSelected':''}`,ariaLabel=`Open ${s.title}`;
  if(compareIndex>=0)row.style.setProperty('--compare-color',COMPARE_COLORS[compareIndex]);else row.style.removeProperty('--compare-color');
  if(row.className!==className)row.className=className;
  if(row.getAttribute('aria-label')!==ariaLabel)row.setAttribute('aria-label',ariaLabel);
  if(row._logRenderKey!==renderKey){
   row.innerHTML=logRowContent(s);
   row._logRenderKey=renderKey;
   if(restoreDeleteFocus)requestAnimationFrame(()=>row.querySelector('[data-delete-session]')?.focus({preventScroll:true}));
  }
  const bar=row.querySelector('.sbar i'),width=metricAvailable(s,'cost')?Math.max(2,100*(s.cost||0)/maxCost):0,widthValue=`${width}%`;
  if(bar&&bar.style.width!==widthValue)bar.style.width=widthValue;
  if(row===cursor)cursor=cursor.nextElementSibling;
  else root.insertBefore(row,cursor);
 });
 [...root.children].forEach(row=>{if(!desired.has(row.dataset.rowKey||''))row.remove();});
}
function compareColor(index){return COMPARE_COLORS[index%COMPARE_COLORS.length];}
function compareKeyFor(row){return String(row?.key||row?.id||'');}
function compareToggleHtml(s){
 const id=compareKeyFor(s),index=compareIds.indexOf(id),selected=index>=0,full=!selected&&compareIds.length>=COMPARE_MAX;
 const label=selected?`Remove ${s.title||'session'} from comparison`:full?`Comparison is full (${COMPARE_MAX} sessions)`:`Add ${s.title||'session'} to comparison`;
 return `<button class=compareToggle data-compare-toggle="${esc(id)}" data-compare-title="${esc(s.title||'')}" type=button aria-pressed="${selected}"${full?' aria-disabled=true':''} aria-label="${esc(label)}" title="${esc(selected?'Remove from comparison':full?`Comparison is full (${COMPARE_MAX})`:'Add to comparison')}"${selected?` style="--compare-color:${compareColor(index)}"`:''}><svg viewBox="0 0 16 16" aria-hidden=true><path d="M3.5 8.5l3 3 6-7"/></svg></button>`;
}
function compareTitle(key){
 if(!compareTitles.has(key)){
  const row=(compareData?.sessions||[]).find(item=>item.key===key)||(compareData?.matches||[]).find(item=>item.key===key)||(allSessionInventory||[]).find(item=>compareKeyFor(item)===key);
  if(row?.title)compareTitles.set(key,row.title);
 }
 return compareTitles.get(key)||'Session';
}
function setCompareIds(ids,{reload=true}={}){
 compareIds=[...new Set(ids.map(String).filter(Boolean))].slice(0,COMPARE_MAX);
 localStorage.setItem('tm_compare_ids',JSON.stringify(compareIds));
 if(!compareIds.includes(compareBaseline)){
  compareBaseline=compareIds[0]||'';
  localStorage.setItem('tm_compare_baseline',compareBaseline);
 }
 syncCompareSelectionUi();
 if(reload&&$('view-session').classList.contains('on')&&sessionScope==='compare')loadComparison();
}
function toggleCompareId(id){
 id=String(id||'');
 if(!id)return;
 if(compareIds.includes(id))setCompareIds(compareIds.filter(item=>item!==id));
 else if(compareIds.length<COMPARE_MAX){compareTitle(id);setCompareIds([...compareIds,id]);}
}
function syncCompareSelectionUi(){
 const count=compareIds.length,full=count>=COMPARE_MAX,badge=$('compare-tab-count');
 badge.hidden=!count;
 badge.textContent=count?String(count):'';
 badge.setAttribute('aria-label',`${count} selected`);
 document.querySelectorAll('#slist [data-compare-toggle]').forEach(button=>{
  const id=button.dataset.compareToggle,index=compareIds.indexOf(id),selected=index>=0,row=button.closest('.srow');
  button.setAttribute('aria-pressed',String(selected));
  if(!selected&&full)button.setAttribute('aria-disabled','true');else button.removeAttribute('aria-disabled');
  const title=button.dataset.compareTitle||'session';
  button.setAttribute('aria-label',selected?`Remove ${title} from comparison`:full?`Comparison is full (${COMPARE_MAX} sessions)`:`Add ${title} to comparison`);
  button.title=selected?'Remove from comparison':full?`Comparison is full (${COMPARE_MAX})`:'Add to comparison';
  if(selected){button.style.setProperty('--compare-color',compareColor(index));row?.style.setProperty('--compare-color',compareColor(index));}
  else{button.style.removeProperty('--compare-color');row?.style.removeProperty('--compare-color');}
  row?.classList.toggle('compareSelected',selected);
 });
 const tray=$('compare-tray');
 tray.hidden=!count;
 if(!count){setLogHtml(tray,'');return;}
 const note=count<2?'Select one more session, or open Compare to find runs with the same title.':full?`Comparison is full (${COMPARE_MAX}).`:'';
 setLogHtml(tray,`<div class=compareTrayItems>${compareIds.map((id,index)=>`<span class=compareChip><b class=compareLetter style="--compare-color:${compareColor(index)}">${COMPARE_LETTERS[index]}</b><span title="${esc(compareTitle(id))}">${esc(compareTitle(id))}</span><button type=button data-compare-remove="${esc(id)}" aria-label="Remove ${esc(compareTitle(id))} from comparison">×</button></span>`).join('')}</div><div class=compareTrayActions>${note?`<span class=compareTrayNote>${esc(note)}</span>`:''}<button class=tbtn type=button data-compare-open>Compare ${count}</button><button class="tbtn compareClear" type=button data-compare-clear>Clear</button></div>`);
}
$('compare-tray').onclick=event=>{
 const remove=event.target.closest('[data-compare-remove]');
 if(remove){setCompareIds(compareIds.filter(id=>id!==remove.dataset.compareRemove));return;}
 if(event.target.closest('[data-compare-open]')){openSessionScope('compare');return;}
 if(event.target.closest('[data-compare-clear]'))setCompareIds([]);
};
async function loadComparison({force=false}={}){
 const key=compareIds.join(',');
 if(!compareIds.length){compareData=null;compareError='';compareNotice='';compareMissing=[];compareLoadedKey='';renderComparison();return;}
 if(!force&&key===compareLoadedKey&&(compareData||compareError))return;
 const request=++compareRequestSeq;
 compareLoading=true;
 if(key!==compareLoadedKey){compareError='';compareNotice='';compareMissing=[];renderComparison();}
 try{
  const response=await fetch(`/session/compare?ids=${encodeURIComponent(key)}`,{cache:'no-store'});
  const payload=await response.json().catch(()=>({}));
  if(request!==compareRequestSeq)return;
  const missing=Array.isArray(payload.missing)?payload.missing:[];
  compareMissing=missing;
  if(!response.ok||!payload.ok){
   compareData=null;
   compareError=payload.error||`Comparison request failed (${response.status}).`;
  }else{
   compareError='';
   compareData=payload;
   [...(payload.sessions||[]),...(payload.matches||[])].forEach(row=>compareTitles.set(row.key,row.title));
  }
  compareNotice=missing.length&&payload.ok?`${missing.length} selected ${countWord(missing.length,'session')} could not be loaded right now.`:'';
  compareLoadedKey=key;
  compareLoadedAt=Date.now();
 }catch(error){
  if(request!==compareRequestSeq)return;
  compareError='Token Meter could not load the comparison. Check that the local server is running.';
  compareLoadedKey=key;
 }finally{
  if(request===compareRequestSeq){compareLoading=false;renderComparison();}
 }
}
const COMPARE_ROWS=[
 {group:'Spend'},
 {key:'cost',label:'Cost',fmt:(v,s)=>money(v)+(s.cost_approx||s.usage_basis!=='reported'?' est':''),lower:true,tip:COST_UNAVAILABLE_TIP},
 {key:'cost_per_turn',label:'Cost per execution',fmt:v=>money(v),lower:true},
 {key:'cache_saved',label:'Saved by cache',fmt:v=>money(v),lower:false},
 {group:'Tokens'},
 {key:'tokens',label:'Total tokens',fmt:compactNumber,lower:true},
 {key:'fresh_input_tokens',label:'Fresh input',fmt:compactNumber,lower:true},
 {key:'cache_read_tokens',label:'Cache reads',fmt:compactNumber},
 {key:'cache_write_tokens',label:'Cache writes',fmt:compactNumber,lower:true},
 {key:'output_tokens',label:'Output',fmt:compactNumber},
 {key:'cache_hit_ratio',label:'Cache hit rate',fmt:pct,lower:false,points:true},
 {key:'tokens_per_turn',label:'Tokens per execution',fmt:compactNumber,lower:true},
 {group:'Work'},
 {key:'turns',label:'Executions',fmt:f,lower:true},
 {key:'subagent_turns',label:'Subagent executions',fmt:f},
 {key:'duration_s',label:'Active time',fmt:waitFmt,lower:true},
 {key:'wall_duration_s',label:'Wall time',fmt:waitFmt,lower:true},
 {key:'wait_avg_s',label:'Average wait',fmt:waitFmt,lower:true},
 {key:'output_tps',label:'Output speed',fmt:v=>`${speedFmt(v)} tok/s`,lower:false},
 {key:'context_peak',label:'Peak context',fmt:(v,s)=>compactNumber(v)+(s.context_window?` · ${pct(v/s.context_window)}`:''),lower:true},
 {key:'reasoning_share',label:'Reasoning share',fmt:pct,points:true},
 {group:'Tools'},
 {key:'tool_calls',label:'Tool calls',fmt:f},
 {key:'tool_errors',label:'Tool errors',fmt:f,lower:true},
 {key:'tools_unique',label:'Distinct tools',fmt:f},
 {key:'top_tools',label:'Most used',text:s=>(s.top_tools||[]).slice(0,3).map(t=>`${t.name} ${f(t.calls)}`).join(' · ')||'none'},
];
const COMPARE_SETUP_ROWS=[
 {label:'App',text:s=>s.label||s.provider||'--'},
 {label:'Model',text:s=>s.primary_model||(s.models||[]).join(', ')||'--'},
 {label:'Reasoning effort',text:s=>s.reasoning_effort||'default'},
 {label:'Project',text:s=>projectFilterLabel(s.project||'')||'local'},
 {label:'Started',text:s=>s.start||'--',same:true},
];
function compareDelta(row,value,base){
 if(value==null||base==null||row.text)return '';
 if(row.points){
  const diff=Math.round(100*(value-base));
  if(!diff)return '<span class=compareDelta>same</span>';
  const better=row.lower===undefined?'':(diff<0)===row.lower?' better':' worse';
  return `<span class="compareDelta${better}">${diff>0?'+':'−'}${Math.abs(diff)} pts</span>`;
 }
 if(value===base)return '<span class=compareDelta>same</span>';
 if(!base)return '';
 const ratio=(value-base)/Math.abs(base),better=row.lower===undefined?'':(ratio<0)===row.lower?' better':' worse';
 const text=Math.abs(ratio)>=1.95?`${(value/base).toFixed(1)}×`:`${ratio>0?'+':'−'}${Math.round(Math.abs(ratio)*100)}%`;
 return `<span class="compareDelta${better}">${text}</span>`;
}
function compareTableHtml(sessions,best){
 const baseline=sessions.find(s=>s.key===compareBaseline)||sessions[0];
 const head=`<thead><tr><th scope=col>Metric</th>${sessions.map((s,i)=>`<th scope=col><span class=compareLetter style="--compare-color:${compareColor(i)}">${s.letter}</span>${s.key===baseline.key?'Baseline':'vs '+esc(baseline.letter)}</th>`).join('')}</tr></thead>`;
 const setup=COMPARE_SETUP_ROWS.map(row=>{
  const values=sessions.map(row.text),differs=!row.same&&new Set(values).size>1;
  return `<tr${differs?' class=differs':''}><td>${esc(row.label)}</td>${values.map(value=>`<td class=text title="${esc(value)}">${esc(value)}</td>`).join('')}</tr>`;
 }).join('');
 const body=COMPARE_ROWS.map(row=>{
  if(row.group)return `<tr class=compareGroup><th colspan=${sessions.length+1} scope=colgroup>${esc(row.group)}</th></tr>`;
  if(row.text)return `<tr><td>${esc(row.label)}</td>${sessions.map(s=>`<td class=text title="${esc(row.text(s))}">${esc(row.text(s))}</td>`).join('')}</tr>`;
  if(sessions.every(s=>s[row.key]==null))return '';
  return `<tr><td>${esc(row.label)}</td>${sessions.map(s=>{
   const value=s[row.key];
   if(value==null)return `<td class="mono compareUnavailable"${row.key==='cost'?` ${tipAttrs(COST_UNAVAILABLE_TIP)}`:' title="Not reported for this session"'}>--</td>`;
   const isBest=best[row.key]===s.key,delta=s.key===baseline.key?'':compareDelta(row,value,baseline[row.key]);
   return `<td class=mono>${esc(row.fmt(value,s))}${isBest?'<span class=compareBest>best</span>':''}${delta}</td>`;
  }).join('')}</tr>`;
 }).join('');
 return `<div class=compareTableWrap><table class=compareTable><caption class=compareCaption>Metric comparison against the baseline session</caption>${head}<tbody><tr class=compareGroup><th colspan=${sessions.length+1} scope=colgroup>Setup</th></tr>${setup}${body}</tbody></table></div>`;
}
function compareChartHtml(sessions){
 const metric=compareChartMetric==='cost'&&sessions.some(s=>s.cost!=null)?'cost':'tokens';
 const lines=sessions.map((s,i)=>({s,i,points:(s.series||[]).filter(p=>p[metric]!=null)})).filter(line=>line.points.length);
 const toggle=`<div class=seg role=group aria-label="Chart metric"><button type=button data-compare-chart=cost aria-pressed="${metric==='cost'}" class="${metric==='cost'?'on':''}">Cost</button><button type=button data-compare-chart=tokens aria-pressed="${metric==='tokens'}" class="${metric==='tokens'?'on':''}">Tokens</button></div>`;
 const head=`<div class=compareSectionHead><h3>Cumulative ${metric==='cost'?'cost':'tokens'} by execution</h3>${toggle}</div>`;
 if(!lines.length)return `<section class="card compareSection">${head}<div class="signalEmpty emptyState">No execution series is available for these sessions.</div></section>`;
 const VW=1040,VH=250,ML=64,MR=74,MT=14,MB=30,PW=VW-ML-MR,PH=VH-MT-MB;
 const maxX=Math.max(1,...lines.map(line=>line.points[line.points.length-1].i)),maxY=Math.max(metric==='cost'?.001:1,...lines.map(line=>Math.max(...line.points.map(p=>p[metric]))));
 const x=v=>ML+PW*(v/maxX),y=v=>MT+PH*(1-v/maxY),fmt=metric==='cost'?money:compactNumber;
 let svg='';
 for(let k=0;k<=4;k++){const yy=MT+PH*k/4;svg+=`<line x1="${ML}" x2="${ML+PW}" y1="${yy}" y2="${yy}" stroke="${CHART.grid}"/><text x="${ML-8}" y="${yy+4}" text-anchor="end" fill="${CHART.lbl}" font-size="11">${esc(fmt(maxY*(1-k/4)))}</text>`;}
 const ticks=Math.min(8,maxX);
 for(let k=0;k<=ticks;k++){const v=Math.round(maxX*k/ticks);svg+=`<text x="${x(v)}" y="${VH-10}" text-anchor="middle" fill="${CHART.lbl}" font-size="11">${f(v)}</text>`;}
 lines.forEach(line=>{
  const pts=[{i:0,[metric]:0},...line.points],color=compareColor(line.i),last=line.points[line.points.length-1];
  svg+=`<path d="${pts.map((p,k)=>`${k?'L':'M'}${x(p.i).toFixed(1)},${y(p[metric]).toFixed(1)}`).join(' ')}" fill="none" stroke="${color}" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"/><circle cx="${x(last.i)}" cy="${y(last[metric])}" r="4" fill="${color}" stroke="${CHART.ink}" stroke-width="2"/><text x="${Math.min(VW-4,x(last.i)+8)}" y="${y(last[metric])+4}" fill="${color}" font-size="11" font-weight="700"${x(last.i)+8>VW-MR?' text-anchor="end"':''}>${line.s.letter} ${esc(fmt(last[metric]))}</text>`;
 });
 const label=lines.map(line=>`${line.s.letter}: ${fmt(line.points[line.points.length-1][metric])} over ${f(line.points[line.points.length-1].i)} executions`).join('; ');
 return `<section class="card compareSection">${head}<svg class=compareChart viewBox="0 0 ${VW} ${VH}" role=img aria-label="${esc(`Cumulative ${metric} by execution. ${label}`)}">${svg}<text x="${ML+PW}" y="${VH-10}" text-anchor="end" fill="${CHART.lbl}" font-size="11" dx="${MR-4}">execution</text></svg><div class=compareLegend>${lines.map(line=>`<span><i style="--compare-color:${compareColor(line.i)}"></i>${line.s.letter} · ${esc(line.s.title)}</span>`).join('')}</div></section>`;
}
function compareBreakdownHtml(sessions){
 const rows=sessions.map((s,i)=>({s,i})).filter(row=>row.s.cost_parts&&row.s.cost>0);
 if(!rows.length)return '';
 const parts=[['input','Fresh input','var(--c-input)'],['cache_write','Cache writes','var(--c-cache-write)'],['cache_read','Cache reads','var(--c-cache-read)'],['output','Output','var(--c-output)'],['server_tools','Server tools','var(--faint)']];
 const max=Math.max(...rows.map(row=>row.s.cost));
 return `<section class="card compareSection"><h3>Where the cost went</h3><div class=compareBreakdown>${rows.map(({s,i})=>`<div class=compareBreakdownRow><span class=compareLetter style="--compare-color:${compareColor(i)}">${s.letter}</span><div class=compareBreakdownBar role=img aria-label="${esc(`${s.letter}: `+parts.filter(([key])=>s.cost_parts[key]>0).map(([key,label])=>`${label} ${money(s.cost_parts[key])}`).join(', '))}" style="width:${Math.max(4,100*s.cost/max)}%">${parts.filter(([key])=>s.cost_parts[key]>0).map(([key,label,color])=>`<i style="width:${100*s.cost_parts[key]/s.cost}%;background:${color}" title="${esc(`${label}: ${money(s.cost_parts[key])}`)}"></i>`).join('')}</div><b class=mono>${esc(money(s.cost))}</b></div>`).join('')}</div><div class=compareLegend>${parts.filter(([key])=>rows.some(row=>row.s.cost_parts[key]>0)).map(([,label,color])=>`<span><i class=part style="background:${color}"></i>${label}</span>`).join('')}</div></section>`;
}
function compareMatchesHtml(matches,sessions){
 const full=compareIds.length>=COMPARE_MAX,same=sessions.length&&sessions.every(s=>s.title_key&&s.title_key===sessions[0].title_key);
 const heading=same||sessions.length===1?'Other runs with the same title':'Other runs with a matching title';
 const body=(matches||[]).length?`<div class=compareMatches>${matches.map(m=>`<div class=compareMatch><div style="min-width:0"><div class=t title="${esc(m.title)}">${esc(m.title)}</div><div class=meta>${esc(m.label)} · ${esc((m.models||[]).join(', ')||'model unknown')} · ${esc(m.last||m.start||'')} · ${f(m.turns)} execs · ${m.cost==null?'cost unavailable':esc(money(m.cost))}</div></div><div class=compareMatchActions><button class=tbtn type=button data-compare-open-session="${esc(m.open_id||m.id)}">Open</button><button class=tbtn type=button data-compare-add="${esc(m.key)}"${full?' disabled title="Comparison is full"':''}>Add</button></div></div>`).join('')}</div>`:'<div class="signalEmpty emptyState">No other recorded sessions share these titles yet. Run the same prompt again and the new session will appear here.</div>';
 return `<section class="card compareSection"><h3>${heading}</h3>${body}</section>`;
}
function renderComparison(){
 const root=$('compare-body');
 if(!root)return;
 const notice=compareNotice?`<div class=compareNote role=status>${esc(compareNotice)} <button class="tbtn compareClear" type=button data-compare-drop-missing>Remove ${compareMissing.length>1?'them':'it'}</button> <button class="tbtn compareClear" type=button data-compare-retry>Try again</button></div>`:'';
 if(!compareIds.length){
  root.innerHTML=`${notice}<section class="card compareEmpty emptyState"><h3>No sessions selected</h3><p>Pick up to ${COMPARE_MAX} sessions in All sessions.</p><button class=tbtn type=button data-compare-pick>Choose sessions</button></section>`;
  return;
 }
 if(compareError){root.innerHTML=`${notice}<section class="card compareEmpty emptyState"><h3>Comparison unavailable</h3><p>${esc(compareError)}</p><button class=tbtn type=button data-compare-retry>Try again</button>${compareMissing.length?' <button class="tbtn compareClear" type=button data-compare-drop-missing>Remove unavailable</button>':''}</section>`;return;}
 if(!compareData||compareLoadedKey!==compareIds.join(',')&&compareLoading){root.innerHTML=`${notice}<section class="card compareEmpty emptyState"><p>Loading comparison…</p></section>`;return;}
 const sessions=compareData.sessions||[],best=compareData.best||{};
 if(!sessions.some(s=>s.key===compareBaseline))compareBaseline=sessions[0]?.key||'';
 const cards=`<div class=compareCards style="grid-template-columns:repeat(${Math.max(2,sessions.length)},minmax(0,1fr))">${sessions.map((s,i)=>{
  const baseline=s.key===compareBaseline;
  return `<article class="card compareCard" style="--compare-color:${compareColor(i)}"><div class=compareCardTop><span class=compareLetter>${s.letter}</span><span class="badge app ${appBadgeClass(s)}">${runtimeMarkSvg(s)}${esc(s.label||s.provider)}</span>${baseline&&sessions.length>1?'<span class=compareBaselineTag>Baseline</span>':''}</div><div class=compareCardTitle title="${esc(s.title)}">${esc(s.title)}</div><div class=compareCardMeta>${esc(s.primary_model||(s.models||[]).join(', ')||'model unknown')} · ${esc(s.start||'')}</div><div class="compareCardCost mono">${s.cost==null?costValueHtml('',false):esc(money(s.cost)+(s.cost_approx||s.usage_basis!=='reported'?' est':''))}</div><div class=compareCardMeta>${f(s.turns)} execs · ${s.duration_s==null?'time unavailable':esc(waitFmt(s.duration_s))+' active'} · ${s.tokens==null?'tokens unavailable':esc(compactNumber(s.tokens))+' tok'}</div><div class=compareCardActions><button type=button data-compare-open-session="${esc(s.open_id||s.id)}">Open</button>${sessions.length>1?`<button type=button data-compare-baseline="${esc(s.key)}"${baseline?' disabled':''}>${baseline?'Baseline':'Set as baseline'}</button>`:''}<button type=button data-compare-remove="${esc(s.key)}">Remove</button></div></article>`;
 }).join('')}</div>`;
 if(sessions.length<2){
  root.innerHTML=`${notice}${cards}<section class="card compareEmpty emptyState"><h3>Add one more session</h3><p>Pick another session in All sessions, or add a matching run below, to see side-by-side metrics.</p><button class=tbtn type=button data-compare-pick>Choose sessions</button></section>${compareMatchesHtml(compareData.matches,sessions)}`;
  return;
 }
 const winner=(key,label,fmt)=>{
  const index=sessions.findIndex(s=>s.key===best[key]),s=sessions[index],known=sessions.filter(row=>row[key]!=null);
  const value=s?`<span class=compareLetter style="--compare-color:${compareColor(index)}">${s.letter}</span>${esc(fmt(s))}`:known.length>=2?'Tied':'--';
  const note=s?esc(s.title):known.length>=2?`All at ${esc(fmt(known[0]))}`:'Not reported for enough sessions';
  return `<div class=metric><span>${label}</span><strong>${value}</strong><small${s?` title="${esc(s.title)}"`:''}>${note}</small></div>`;
 };
 const winners=`<section class="card compareSection"><div class=compareWinners>${winner('cost','Cheapest',s=>money(s.cost)+(s.cost_approx||s.usage_basis!=='reported'?' est':''))}${winner('duration_s','Fastest',s=>waitFmt(s.duration_s)+' active')}</div></section>`;
 const table=`<section class="card compareSection"><div class=compareSectionHead><h3>Side by side</h3><span class=allSessionsHint>Deltas are against baseline ${esc((sessions.find(s=>s.key===compareBaseline)||sessions[0]).letter)} · unavailable evidence shows --</span></div>${compareTableHtml(sessions,best)}</section>`;
 root.innerHTML=notice+cards+winners+table+compareChartHtml(sessions)+compareBreakdownHtml(sessions)+compareMatchesHtml(compareData.matches,sessions);
}
$('session-compare').onclick=event=>{
 const target=event.target.closest('button');
 if(!target||target.disabled)return;
 if(target.id==='compare-pick'||target.hasAttribute('data-compare-pick')){openSessionScope('all');return;}
 if(target.id==='compare-clear'){compareNotice='';setCompareIds([]);return;}
 if(target.hasAttribute('data-compare-retry')){loadComparison({force:true});return;}
 if(target.hasAttribute('data-compare-drop-missing')){const drop=new Set(compareMissing);compareNotice='';compareMissing=[];setCompareIds(compareIds.filter(id=>!drop.has(id)));return;}
 if(target.dataset.compareRemove){compareNotice='';setCompareIds(compareIds.filter(id=>id!==target.dataset.compareRemove));return;}
 if(target.dataset.compareAdd){compareNotice='';toggleCompareId(target.dataset.compareAdd);return;}
 if(target.dataset.compareBaseline){compareBaseline=target.dataset.compareBaseline;localStorage.setItem('tm_compare_baseline',compareBaseline);renderComparison();return;}
 if(target.dataset.compareChart){compareChartMetric=target.dataset.compareChart;localStorage.setItem('tm_compare_chart',compareChartMetric);renderComparison();return;}
 if(target.dataset.compareOpenSession)selectSession(target.dataset.compareOpenSession,{returnScope:'compare'});
};
compareUiReady=true;
renderSessionScope();
