function workFlagReason(cell){
 return {possible_overspend:'premium model on routine work',possible_false_economy:'light model on complex work',possible_overthinking:'high effort on routine work'}[cell?.flag]||'worth reviewing';
}
function workSuggestion(item){
 const work=WORK_TYPE_LABELS[item.work_type]||item.work_type||'';
 switch(item.kind){
  case 'premium_routine':{
   const from=(item.from_models||[]).map(entry=>entry.model).join(', ');
   const to=(item.to_models||[]).map(entry=>`${entry.model} in ${entry.runtime}`).join(' or ');
   return{title:to?`Try ${to} for routine work`:'Use a cheaper model for routine work',detail:`${item.requests.toLocaleString()} routine request${item.requests===1?'':'s'} ran on your most expensive models${from?` (${from})`:''}.${to?` ${item.to_models.length===1?'That model is':'Those are'} your most-used mid-priced model${item.to_models.length===1?'':'s'} in the same app.`:''}`,drill:{complexity:item.complexity,tier:item.tier}};
  }
  case 'family_upgrade':return{title:`Use ${item.to_model} instead of ${item.model}`,detail:`A newer version in ${item.runtime}: ${Math.round(100*(1-item.to_price/item.from_price))}% cheaper per token. It resolved ${workPercent(item.to_rate)} of your tasks, vs ${workPercent(item.from_rate)} on ${item.model}.`,drill:{model:item.model,model_runtime:item.runtime}};
  case 'effort_routine':return{title:`Lower reasoning effort on routine work`,detail:`${item.requests.toLocaleString()} routine request${item.requests===1?'':'s'} used ${item.effort} effort.`,drill:{complexity:item.complexity,effort:item.effort}};
  case 'light_complex':return{title:'Use a stronger model for complex work',detail:`Complex work on light models got pushback on ${workPercent(item.rework?.rate)} of follow-ups.`,drill:{complexity:item.complexity,tier:item.tier}};
  case 'switch_model':{
   const level={routine:'routine',everyday:'everyday',complex:'complex'}[item.complexity]||'',app=item.to_runtime&&item.to_runtime!==item.runtime?` in ${item.to_runtime}`:'';
   return{title:`Try ${item.to_model}${app} for ${level} ${item.work_type==='other'?'non-software requests':work.toLowerCase()}`,detail:`On ${level} requests: ${workPercent(item.to_rate)} resolved at ${money(item.to_cost)} per resolved task, vs ${workPercent(item.from_rate)} at ${money(item.from_cost)} on ${item.model} (${item.runtime})${item.to_pushback!=null&&item.from_pushback!=null?`; pushback ${workPercent(item.to_pushback)} vs ${workPercent(item.from_pushback)}`:''}.${item.to_untested_harder?` Keep ${item.model} for harder work: ${item.to_model} hasn’t been used on it yet.`:''}`,drill:{work_type:item.work_type,complexity:item.complexity,model:item.model,model_runtime:item.runtime}};
  }
  case 'long_threads':return{title:'Start a fresh session sooner',detail:`Sessions with ${item.threshold_turns}+ requests cost ${item.ratio.toFixed(1)}× more per request than short ones, because each request re-sends the growing context.`,drill:{tag:'long_thread'}};
  default:return{title:item.kind,detail:'',drill:null};
 }
}
function workSuggestionBasis(item){
 const [count,unit]=item.requests!=null?[item.requests,'request']:item.tasks!=null?[item.tasks,'task']:[item.sessions||0,'session'];
 return `${count.toLocaleString()} ${unit}${count===1?'':'s'}`;
}
const WORK_MODEL_SUGGESTIONS=['family_upgrade','switch_model','premium_routine','light_complex'];
function workDollars(value){return value>0&&value<1?money(value):`$${Math.round(value||0).toLocaleString()}`;}
function workModelSwap(item){
 const short=name=>String(name||'').replace(/^claude-/,'');
 switch(item.kind){
  case 'family_upgrade':return{from:short(item.model),to:short(item.to_model),note:`resolves ${item.to_rate>=item.from_rate?'as often':'about as often'} · ${workPercent(item.from_rate)} → ${workPercent(item.to_rate)}`};
  case 'switch_model':return{from:short(item.model),to:short(item.to_model),note:`${item.complexity?`${item.complexity} work · `:''}${workPercent(item.from_rate)} → ${workPercent(item.to_rate)}`};
  case 'premium_routine':return{from:'premium models',to:short(item.to_models?.[0]?.model)||'mid-priced',note:`routine work · ${(item.requests||0).toLocaleString()} requests`};
  case 'light_complex':return{from:'light models',to:'a stronger model',note:`complex work · ${workPercent(item.rework?.rate)} pushback`};
  default:return{from:'',to:'',note:''};
 }
}
function workSparkline(points){
 const usable=points.filter(point=>point.rate!=null);
 if(usable.length<2)return '';
 const W=300,H=44,max=Math.max(...usable.map(point=>point.rate),0.01),x=index=>(W*index/(usable.length-1)).toFixed(1),y=rate=>(H-3-(H-8)*rate/max).toFixed(1);
 const line=usable.map((point,index)=>`${x(index)},${y(point.rate)}`).join(' '),last=usable[usable.length-1];
 return `<svg class=workSuggestSpark viewBox="0 0 ${W} ${H+10}" role=img aria-label="Pushback rate by week"><polyline fill=none stroke="var(--warn)" stroke-width=2 stroke-linejoin=round points="${line}"/><circle cx="${x(usable.length-1)}" cy="${y(last.rate)}" r=3.5 fill="var(--warn)"/><text x=0 y="${H+9}" fill="var(--faint)" font-size=9>${esc(usable[0].label)}</text><text x="${W-2}" y="${H+9}" fill="var(--faint)" font-size=9 text-anchor=end>${esc(last.label)}</text></svg>`;
}
// Effort by model: one model at a time, because a level such as "high" means different things on different models.
let workEffortModel=localStorage.getItem('tm_work_effort_model')||'',workEffortGridHtml='';
const WORK_EFFORT_ROWS={routine:'Routine',everyday:'Everyday',complex:'Complex+'};
function workEffortRate(rework){return rework?`${Math.round(rework.rate*100)}%`:'';}
function workEffortVerdict(verdict){
 const row=esc(WORK_EFFORT_ROWS[verdict.complexity]||verdict.complexity),evidence=`${workEffortRate({rate:verdict.lower_rate})} vs ${workEffortRate({rate:verdict.higher_rate})} pushback`;
 const title=esc(`From ${verdict.lower_samples} and ${verdict.higher_samples} labeled follow-ups, 99% confidence`);
 if(verdict.kind==='lower')return `<li data-kind=lower title="${title}"><b>${row}: ${esc(verdict.higher)} → ${esc(verdict.lower)} looks safe</b> · ${evidence} · saves ${money(verdict.saving)}</li>`;
 return `<li data-kind=raise title="${title}"><b>${row}: raise ${esc(verdict.lower)} → ${esc(verdict.higher)}</b> · ${evidence}</li>`;
}
function renderWorkEffortModels(insights){
 const data=insights?.effort_by_model||{},models=data.models||[],box=$('w-effort-models');
 box.hidden=!insights||!models.length; // only models with labeled requests
 if(!models.length)return;
 // JSON rather than a NUL-joined key: the HTML parser rewrites NUL inside attributes.
 const key=model=>JSON.stringify([model.model,model.runtime]);
 const chosen=models.find(model=>key(model)===workEffortModel)||models[0];
 $('w-effort-model').innerHTML=models.map(model=>`<option value="${esc(key(model))}"${model===chosen?' selected':''}>${esc(model.model)} · ${esc(model.runtime)}</option>`).join('');
 const efforts=chosen.efforts||[],grid=$('w-effort-grid');
 // One number per cell (pushback); cost per request sits once per effort column.
 const columnCost=effort=>{let spend=0,requests=0;(chosen.rows||[]).forEach(row=>{const cell=(row.cells||[]).find(item=>item.effort===effort);if(cell&&cell.cost_per_request!=null){spend+=cell.spend;requests+=cell.requests;}});return requests?spend/requests:null;};
 grid.style.gridTemplateColumns=`110px repeat(${efforts.length},minmax(72px,1fr))`;
 const head=`<div role=row style="display:contents"><div class=workSizingHead role=columnheader></div>${efforts.map(effort=>{const cost=columnCost(effort);const tip=esc('No effort recorded: the app\'s default level');return `<div class="workSizingHead workEffortHead${effort==='default'?' fieldtip':''}" role=columnheader${effort==='default'?` tabindex=0 aria-description="${tip}" data-tip="${tip}"`:''}>${esc(effort)}${cost!=null?`<small>${money(cost)}/req</small>`:''}</div>`;}).join('')}</div>`;
 const html=head+(chosen.rows||[]).map(row=>`<div role=row style="display:contents"><div class=workSizingRowHead role=rowheader>${esc(WORK_EFFORT_ROWS[row.complexity]||row.complexity)}</div>${(row.cells||[]).map(cell=>{
  if(!cell.requests)return '<div class=workEffortCell data-empty role=cell aria-label="No requests">·</div>';
  const cost=cell.cost_per_request!=null?`${money(cell.cost_per_request)}/request`:'cost unavailable';
  const label=`${WORK_EFFORT_ROWS[row.complexity]||row.complexity} at ${cell.effort}: `;
  if(!cell.rework||cell.rework.few_samples){const detail=`${label}too few labeled follow-ups (${cell.rework?.samples||0}) · ${f(cell.requests)} requests · ${cost}`;return `<div class=workEffortCell data-few role=cell tabindex=0 data-cell="${esc(row.complexity+':'+cell.effort)}" title="${esc(detail)}" aria-label="${esc(detail)}" data-detail="${esc(detail)}">·</div>`;}
  const heat=Math.min(.32,cell.rework.rate*.8).toFixed(3);
  const detail=`${label}${workEffortRate(cell.rework)} pushback · ${f(cell.rework.samples)} labeled follow-ups · ${f(cell.requests)} requests · ${cost}`;
  return `<div class=workEffortCell role=cell tabindex=0 data-cell="${esc(row.complexity+':'+cell.effort)}" style="--heat:${heat}" title="${esc(detail)}" aria-label="${esc(detail)}" data-detail="${esc(detail)}">${workEffortRate(cell.rework)}</div>`;
 }).join('')}</div>`).join('');
 // The Work poll re-renders every few seconds: leave unchanged cells alone, and keep focus on the same cell when they do change.
 if(html!==workEffortGridHtml){
  const focused=grid.contains?.(document.activeElement)?document.activeElement.dataset.cell:'';
  grid.innerHTML=workEffortGridHtml=html;
  const again=focused&&[...grid.querySelectorAll('[data-cell]')].find(node=>node.dataset.cell===focused);
  if(again){again.focus();$('w-effort-detail').textContent=again.dataset.detail;}
  else $('w-effort-detail').textContent='';
 }
 const verdicts=chosen.verdicts||[];
 $('w-effort-verdicts').innerHTML=verdicts.map(workEffortVerdict).join('');
 $('w-effort-note').textContent=`Faint dots: too few follow-ups${data.unrecorded_spend>0?` · ${money(data.unrecorded_spend)} without a recorded effort left out`:''}`;
}
// A cell's evidence for keyboard, touch, and pointer alike: focusing, tapping, or hovering shows it under the table.
['focusin','mouseover'].forEach(type=>$('w-effort-grid').addEventListener(type,event=>{
 const cell=event.target.closest('[data-detail]');
 if(cell)$('w-effort-detail').textContent=cell.dataset.detail;
}));
$('w-effort-model').addEventListener('change',event=>{
 workEffortModel=event.target.value;localStorage.setItem('tm_work_effort_model',workEffortModel);
 $('w-effort-detail').textContent='';
 if(WORK?.insights)renderWorkEffortModels(WORK.insights);
});
function renderWorkSuggestCards(insights){
 const rows=insights?.recommendations||[],box=$('w-suggest-cards');
 const drill=item=>{const tip=workSuggestion(item);return tip.drill?` data-drill="${esc(JSON.stringify(tip.drill))}" data-drill-label="${esc(tip.title)}"`:'';};
 // Models
 const models=rows.filter(item=>WORK_MODEL_SUGGESTIONS.includes(item.kind)).sort((a,b)=>(b.saving||0)-(a.saving||0));
 const best=models.find(item=>(item.saving||0)>0);
 const modelRows=models.slice(0,3).map(item=>{const swap=workModelSwap(item);return `<button type=button class=workDrillBtn${drill(item)} title="${esc(swap.note)}"><span class=workSwap>${esc(swap.from)}<b>→</b>${esc(swap.to)}</span><span class=workSwapSave>${item.saving>0?workDollars(item.saving):'--'}</span></button>`;}).join('');
 const modelCard=`<div class=workSuggestCard><div class=workSuggestEyebrow><i style="background:var(--good)"></i>Model suggestions</div>${models.length?`<div class=workSuggestHero>${best?`${workDollars(best.saving)}<small title="Savings can overlap">biggest saving</small>`:`${models.length}<small>model suggestion${models.length===1?'':'s'}</small>`}</div><div class=workSuggestRows>${modelRows}</div><div class=workSuggestFoot><span>${models.length>3?`+${models.length-3} more`:''}</span><button type=button data-open-sizing-tables>See all</button></div>`:'<div class=workSuggestEmpty>No model changes suggested for this period.</div>'}</div>`;
 // Reasoning
 // Several high levels can be flagged at once (for example xhigh and max): count them together.
 const efforts=rows.filter(item=>item.kind==='effort_routine').sort((a,b)=>(b.spend||0)-(a.spend||0));
 // The drill filter takes a comma-separated effort list; the card shows the levels joined with "or".
 const effort=efforts.length?{...efforts[0],requests:efforts.reduce((sum,item)=>sum+(item.requests||0),0),spend:efforts.reduce((sum,item)=>sum+(item.spend||0),0),effort:efforts.map(item=>item.effort).join(','),label:efforts.map(item=>item.effort).join(' or ')}:null;
 const cells=(insights?.right_sizing?.effort?.cells||[]).filter(cell=>cell.complexity==='routine'&&cell.spend>0).sort((a,b)=>b.spend-a.spend);
 const routineSpend=cells.reduce((sum,cell)=>sum+cell.spend,0),shades=['var(--reason)','var(--w-routine-2)','var(--w-routine-3)','var(--w-routine-4)'];
 const shown=cells.slice(0,3),rest=routineSpend-shown.reduce((sum,cell)=>sum+cell.spend,0);
 const parts=[...shown.map(cell=>({label:cell.effort||'unknown',spend:cell.spend})),...(rest>0?[{label:'other',spend:rest}]:[])];
 const bar=routineSpend?`<div class=workSuggestBar role=img aria-label="Routine spend by reasoning effort">${parts.map((part,index)=>`<span style="flex:${part.spend.toFixed(4)};background:${shades[index]}"></span>`).join('')}</div><div class=workSuggestLegend>${parts.map((part,index)=>`<span><i style="background:${shades[index]}"></i>${esc(part.label)}</span>`).join('')}</div>`:'';
 const effortCard=`<div class=workSuggestCard><div class=workSuggestEyebrow><i style="background:var(--reason)"></i>Reasoning suggestions</div>${effort?`<div class=workSuggestHero>${(effort.requests||0).toLocaleString()}<small>routine request${effort.requests===1?'':'s'} ran at ${esc(effort.label)} effort</small></div>`:`<div class=workSuggestEmpty>No reasoning changes suggested for this period.</div>`}${bar}<div class=workSuggestFoot><span></span>${effort&&drill(effort)?`<button type=button${drill(effort)}>Open requests</button>`:''}</div></div>`;
 // Pushback
 const rework=insights?.rework||{},overall=rework.overall||{};
 const weekly=(rework.weekly||[]).filter(point=>point.samples>=20).map(point=>({rate:point.rate,label:workMonthName(point.week,{year:false})}));
 // Least and most need 50+ judged follow-ups each and a gap bigger than noise (two-proportion check, about 95%).
 const ranked=(rework.models||[]).filter(model=>model.samples>=50&&model.rate!=null&&model.model&&model.model!=='unknown-model').sort((a,b)=>a.rate-b.rate);
 const lo=ranked[0],hi=ranked[ranked.length-1],pooled=ranked.length>1?(lo.rate*lo.samples+hi.rate*hi.samples)/(lo.samples+hi.samples):0;
 const distinct=ranked.length>1&&hi.rate-lo.rate>1.96*Math.sqrt(pooled*(1-pooled)*(1/lo.samples+1/hi.samples));
 const chip=(label,model)=>model?`<div><span>${label}</span><b title="${esc(model.model)} · ${esc(model.runtime)}">${esc(model.model.replace(/^claude-/,''))} <s>${workPercent(model.rate)}</s></b></div>`:'';
 const pushCard=`<div class=workSuggestCard><div class=workSuggestEyebrow><i style="background:var(--warn)"></i>Pushback</div>${overall.samples?`<div class=workSuggestHero>${workPercent(overall.rate)}<small>of ${overall.samples.toLocaleString()} follow-ups pushed back${overall.few_samples?' · few samples':''}</small></div>${workSparkline(weekly)}${distinct?`<div class=workSuggestChips>${chip('Least pushback',lo)}${chip('Most pushback',hi)}</div>`:''}<div class=workSuggestFoot><span></span><button type=button data-scroll-work=w-module-rework>Pushback over time</button></div>`:'<div class=workSuggestEmpty>No labeled follow-ups in this period yet.</div>'}</div>`;
 box.innerHTML=modelCard+effortCard+pushCard;
}
function renderWorkOpportunities(insights){
 const rows=insights?.recommendations||[],table=$('w-opportunities');
 renderWorkSuggestCards(insights);
 if(!rows.length){table.innerHTML='';return;}
 table.innerHTML=`<thead><tr><th>Suggestion</th><th>Based on</th><th>Spend</th><th>Possible saving</th></tr></thead><tbody>${rows.map(item=>{
  const tip=workSuggestion(item);
  const label=tip.drill?`<button type=button class=workDrillBtn data-drill="${esc(JSON.stringify(tip.drill))}" data-drill-label="${esc(tip.title)}"><span class=workIssueKind aria-hidden=true>▲</span><span class=workSuggestion><b>${esc(tip.title)}</b><small>${esc(tip.detail)}</small></span></button>`:esc(tip.title);
  return `<tr><td>${label}</td><td><span class=mono>${esc(workSuggestionBasis(item))}</span></td><td><span class=mono>${money(item.spend)}</span></td><td><span class=mono>${item.saving!=null&&item.saving>0?`<span style="color:var(--good)">${money(item.saving)}</span> est`:'<span class=workMuted>--</span>'}</span></td></tr>`;
 }).join('')}</tbody>`;
}
function renderWorkFilters(insights){
 const filters=insights?.filters||{};
 $('w-runtime').innerHTML='<option value="">All apps</option>'+(filters.runtimes||[]).map(value=>`<option value="${esc(value)}"${value===workRuntime?' selected':''}>${esc(value)}</option>`).join('');
 const projects=filters.projects||[];
 if(workProject&&!projects.includes(workProject))projects.unshift(workProject);
 $('w-project').innerHTML='<option value="">All projects</option>'+projects.map(value=>`<option value="${esc(value)}"${value===workProject?' selected':''}>${esc(deliveryProjectName(value))}</option>`).join('');
}
const WORK_OUTCOME_LABELS={accepted:'Accepted without pushback',recovered:'Recovered after pushback',ended_on_pushback:'Ended on pushback',unclear:'Unclear',single_shot:'Single request',pending:'Not labeled yet'};
const WORK_OUTCOME_COLORS={accepted:'var(--w-accepted)',recovered:'var(--w-recovered)',ended_on_pushback:'var(--w-ended)',unclear:'var(--w-unclear)',single_shot:'var(--w-single)',pending:'var(--w-pending)'};
function renderWorkFit(insights){
 const fit=insights?.model_fit||{},models=fit.models||[],table=$('w-fit-table');
 if(!models.length||!(fit.work_types||[]).length){table.innerHTML='<tbody><tr><td><div class=workEmpty>Waiting for labeled sessions.</div></td></tr></tbody>';return;}
 const cell=(work,model)=>(fit.cells||[]).find(item=>item.work_type===work&&item.model===model.model&&item.runtime===model.runtime);
 table.innerHTML=`<thead><tr><th>Work type</th>${models.map(model=>`<th class=num title="${esc(model.runtime)} · ${model.tasks} tasks">${esc(model.model)}<br><small class=workMuted>${esc(model.runtime)}</small></th>`).join('')}</tr></thead><tbody>${fit.work_types.map(work=>`<tr><td>${esc(WORK_TYPE_LABELS[work]||work)}</td>${models.map(model=>{const item=cell(work,model);if(!item||!item.tasks)return '<td class="num workMuted">--</td>';return `<td class="num mono workFitCell"><button type=button class=workDrillBtn data-drill="${esc(JSON.stringify({work_type:work,model:model.model,model_runtime:model.runtime}))}" data-drill-label="${esc(`${WORK_TYPE_LABELS[work]||work} · ${model.model}`)}">${workRateText(item.rework)}${item.best?'<span class=workFitBest>Best</span>':''}<small>${item.tasks} · ${money(item.cost_per_task)}/task</small></button></td>`;}).join('')}</tr>`).join('')}</tbody>`;
}
$('view-work').addEventListener('click',event=>{
 const tables=event.target.closest('[data-open-sizing-tables]');
 if(tables){const details=$('w-sizing-tables');details.open=true;details.scrollIntoView({behavior:'smooth',block:'start'});return;}
 const scroll=event.target.closest('[data-scroll-work]');
 if(scroll){$(scroll.dataset.scrollWork)?.scrollIntoView({behavior:'smooth',block:'start'});return;}
 const single=event.target.closest('[data-open-work-session]');
 if(single){selectSession(single.dataset.openWorkSession,{returnScope:'all'});return;}
 const target=event.target.closest('[data-drill]');if(!target)return;
 let filters={};try{filters=JSON.parse(target.dataset.drill);}catch(error){return;}
 openWorkSessions(filters,target.dataset.drillLabel);
});
let workResizeTimer=0;
window.addEventListener('resize',()=>{clearTimeout(workResizeTimer);workResizeTimer=setTimeout(()=>{if(WORK&&$('view-work').classList.contains('on')){renderWorkRework(WORK.insights);renderWorkAgentTrend(WORK.insights);}},150);});
function workHours(seconds){const minutes=Math.round((seconds||0)/60);return minutes>=90?`${(minutes/60).toFixed(minutes%60?1:0)} h`:`${minutes} min`;}
const WORK_BANDS={night:['Night','midnight–6 am'],morning:['Morning','6 am–noon'],afternoon:['Afternoon','noon–6 pm'],evening:['Evening','6 pm–midnight']};
const WORK_TAG_INFO={
 marathon:['Marathon',t=>`More than ${workHours(t.marathon_s)} of active time`,'var(--w5)'],
 big_spend:['Big spender',t=>`Top 10% by cost, over ${money(t.big_spend)}`,'var(--w2)'],
 team:['Subagent team',t=>`${t.team_children} or more subagent runs`,'var(--w7)'],
 overkill:['Overkill',()=>'Routine work on a premium model or high effort','var(--warn)'],
 underpowered:['Underpowered',()=>'Complex work on a light model that got pushback','var(--warn)'],
 rescued:['Rescued',()=>'Back on track after a pushback','var(--w-recovered)'],
 stuck:['Ended on pushback',()=>'The last follow-up was a pushback','var(--bad)'],
 one_shot:['One-shot',()=>'Done in a single request','var(--good)'],
 long_thread:['Long thread',()=>'30 or more requests in one session','var(--w1)'],
};
function renderWorkTagCards(insights){
 const tags=insights?.tags||{},items=tags.items||[],thresholds=tags.thresholds||{},host=$('w-tags');
 const sessions=insights?.coverage?.sessions||0;
 $('w-tags-note').textContent=items.length?`${sessions.toLocaleString()} sessions`:'';
 host.innerHTML=items.length?items.map(item=>{
  const [name,describe,tone]=WORK_TAG_INFO[item.tag]||[item.tag,()=>'','var(--w-unclear)'];
  const stats=[money(item.spend),item.share!=null?`${workPercent(item.share)} of spend`:'',item.resolved_rate!=null&&item.judged_sessions>=5?`${workPercent(item.resolved_rate)} resolved`:''].filter(Boolean).join(' · ');
  return `<button type=button class=workTagTile style="--tone:${tone}" data-drill="${esc(JSON.stringify({tag:item.tag}))}" data-drill-label="${esc(name)}"><span class=workTagName><i aria-hidden=true></i>${esc(name)}</span><strong class=num>${item.sessions.toLocaleString()}</strong><span class=workTagStats>${esc(stats)}</span><small>${esc(describe(thresholds))}</small></button>`;
 }).join(''):'<div class=workEmpty>No sessions in this period yet.</div>';
}
function renderWorkRhythm(insights){
 const rhythm=insights?.rhythm||{},cells=rhythm.cells||[],heat=$('w-heat'),bands=rhythm.bands||[];
 const days=['Mon','Tue','Wed','Thu','Fri','Sat','Sun'],max=Math.max(1,...cells.map(cell=>cell.sessions));
 const lookup=new Map(cells.map(cell=>[`${cell.weekday}:${cell.hour}`,cell]));
 const busiest=cells.length?cells.reduce((a,b)=>b.sessions>a.sessions?b:a):null;
 const hourText=hour=>new Date(2000,0,1,hour).toLocaleTimeString([],{hour:'numeric'});
 $('w-rhythm-note').textContent=busiest?`${(rhythm.sessions||0).toLocaleString()} sessions · most start on ${days[busiest.weekday]} around ${new Date(2000,0,1,busiest.hour).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'})}`:'';
 heat.innerHTML=cells.length?days.map((day,weekday)=>`<span>${day}</span>${Array.from({length:24},(_,hour)=>{const cell=lookup.get(`${weekday}:${hour}`);return cell?`<i data-v style="--v:${(0.2+0.8*cell.sessions/max).toFixed(2)}" title="${esc(`${day} ${hourText(hour)} · ${cell.sessions} session${cell.sessions===1?'':'s'} · ${money(cell.spend)}`)}"></i>`:'<i></i>';}).join('')}`).join('')+`<span></span>${[0,6,12,18].map(hour=>`<span class=h>${esc(hourText(hour))}</span>`).join('')}`:'<div class=workEmpty style="grid-column:1/-1">No session start times in this period.</div>';
 const total=bands.reduce((sum,band)=>sum+band.sessions,0),maxRate=Math.max(.05,...bands.map(band=>band.pushback?.rate||0));
 $('w-bands').innerHTML=total?bands.filter(band=>band.sessions).map(band=>{const [name,range]=WORK_BANDS[band.band]||[band.band,''];const few=!band.pushback||band.pushback.few_samples;return `<div class=workBand data-few="${few}"><span>${esc(name)}<small>${esc(range)}</small></span><span class=workBarTrack><i style="width:${band.pushback?(100*band.pushback.rate/maxRate).toFixed(1):0}%"></i></span><b class=num>${band.pushback?workPercent(band.pushback.rate):'--'}</b><small>${band.sessions.toLocaleString()} session${band.sessions===1?'':'s'} · ${money(band.spend)}${few&&band.pushback?' · few follow-ups':''}</small></div>`;}).join(''):'';
 const solid=bands.filter(band=>band.pushback&&!band.pushback.few_samples);
 let note='';
 if(solid.length>=2){
  const high=solid.reduce((a,b)=>b.pushback.rate>a.pushback.rate?b:a),low=solid.reduce((a,b)=>b.pushback.rate<a.pushback.rate?b:a);
  note=low.pushback.rate>0&&high.pushback.rate/low.pushback.rate>=1.3?`Pushback is ${(high.pushback.rate/low.pushback.rate).toFixed(1)}× higher in the ${WORK_BANDS[high.band][0].toLowerCase()} than in the ${WORK_BANDS[low.band][0].toLowerCase()}.`:'Pushback is about the same at every time of day.';
 }else if(total)note='Needs at least 20 labeled follow-ups in two parts of the day to compare.';
 $('w-band-note').textContent=note;
}
function renderWork(payload){
 WORK=payload;renderWorkStatus(payload);
 const insights=payload?.insights,areas=(payload?.settings?.areas||[]).map(area=>area.name);
 renderWorkFilters(insights);renderWorkBreakdown(insights);renderWorkTagCards(insights);renderWorkRhythm(insights);
 renderWorkRework(insights);renderWorkAgentTrend(insights);
 renderWorkScorecard(insights);renderWorkEffortModels(insights);renderWorkFit(insights);renderWorkMix(insights);renderWorkOpportunities(insights);renderWorkSizing(insights);renderWorkEffort(insights);
}
async function loadWork(){
 const request=++workRequest,query=new URLSearchParams({months:workMonths});
 if(workRuntime)query.set('runtime',workRuntime);if(workProject)query.set('project',workProject);
 try{
  const response=await fetch(`/work?${query}`,{cache:'no-store'});
  const payload=await response.json();if(request!==workRequest)return;
  if(!response.ok||!payload.ok){
   if(response.status===404&&(workProject||workRuntime)){workProject='';workRuntime='';loadWork();return;}
   throw new Error(payload.error||'Work insights unavailable');
  }
  workLastLoadedAt=Date.now();renderWork(payload);
 }catch(error){
  if(request!==workRequest)return;
  $('w-state').dataset.state='storage_error';$('w-state').textContent='Error';
  $('w-status-title').textContent='Work insights are unavailable';$('w-status-detail').textContent='Token Meter will try again shortly.';
 }
}
async function postWorkAction(path,payload){
 const token=agentActionToken();
 if(!token)throw new Error('Controls are not ready yet. Wait a moment and try again.');
 const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Token-Meter-Action':token},body:JSON.stringify(payload)});
 let result={};try{result=await response.json();}catch(error){}
 if(!response.ok||!result.ok)throw new Error(result.error||'Token Meter could not save that change.');
 return result;
}
$('w-rework-model').addEventListener('change',()=>{if(WORK)renderWorkRework(WORK.insights);});
$('w-agent-model').addEventListener('change',()=>{if(WORK)renderWorkAgentTrend(WORK.insights);});
$('w-months').addEventListener('change',event=>{workMonths=event.target.value;loadWork();});
$('w-runtime').addEventListener('change',event=>{workRuntime=event.target.value;loadWork();});
$('w-project').addEventListener('change',event=>{workProject=event.target.value;loadWork();});
$('w-pause').addEventListener('change',async event=>{
 const duration=event.target.value;if(!duration)return;
 try{const result=await postWorkAction('/work-insights/pause',{duration});if(WORK){WORK.status=result.status;WORK.settings=result.work_insights;renderWorkStatus(WORK);}}
 catch(error){$('w-status-detail').textContent=error.message;}
 finally{event.target.value='';}
});
$('w-resume').addEventListener('click',async()=>{
 try{const result=await postWorkAction('/work-insights/pause',{duration:'resume'});if(WORK){WORK.status=result.status;WORK.settings=result.work_insights;renderWorkStatus(WORK);}loadWork();}
 catch(error){$('w-status-detail').textContent=error.message;}
});
$('w-open-settings').addEventListener('click',()=>setHashRoute('settings-work-insights'));
$('w-turn-on').addEventListener('click',()=>setHashRoute('settings-work-insights'));
$('w-setup-retry').addEventListener('click',async()=>{
 try{const result=await postWorkAction('/work-insights/setup',{start:true});if(WORK){WORK.status=result.status;renderWorkStatus(WORK);}}
 catch(error){$('w-status-detail').textContent=error.message;}
});
function renderWorkSettings(payload,{force=false}={}){
 if(!payload)return;
 applyWorkSupport(payload.settings?.supported);  // known here long before the first full state on a cold start
 WORK_SETTINGS=payload;
 const settings=payload.settings||{},status=payload.status||{};
 const badge=status.reason==='delete_pending'?'Delete pending':({disabled:'Off',setup_needed:'Setup needed',running:'Running',idle:'Up to date',paused:'Paused',throttled:'Waiting',backoff:'Retrying',storage_error:'Error'}[settings.enabled?status.state:'disabled']||'On');
 $('work-settings-badge').textContent=badge;
 if(!force&&settingsInteractionActive()){updateWorkSaveState();return;}
 $('work-enable-cta').hidden=Boolean(settings.enabled);
 $('work-enabled').checked=Boolean(settings.enabled);$('work-battery').checked=settings.pause_on_battery!==false;$('work-live-notify').checked=settings.live_notifications!==false;$('work-live-notify').disabled=!settings.enabled;$('work-live-notify').closest('label').title=settings.enabled?'':'Turn on labeling to get live-suggestion notifications.';
 $('work-model').value=settings.model||'';$('work-url').value=settings.ollama_url||'';
 $('work-rate').value=String(settings.rate_per_minute||'auto');$('work-backfill').value=String(settings.backfill_days??90);
 workAreasDraft=(settings.areas||[]).map(area=>({...area}));renderWorkAreas();
}
function workSettingsDraft(){
 return {model:$('work-model').value.trim(),ollama_url:$('work-url').value.trim(),areas:(workAreasDraft||[]).map(area=>({name:area.name.split(/\s+/).join(' ').trim(),description:area.description.split(/\s+/).join(' ').trim()}))};
}
// Save covers the model, address, and areas; it is live only when one of them differs from what is saved.
function workSettingsDirty(){
 const saved=WORK_SETTINGS?.settings||{},draft=workSettingsDraft();
 const savedAreas=(saved.areas||[]).map(area=>({name:area.name,description:area.description}));
 return draft.model!==(saved.model||'')||draft.ollama_url!==(saved.ollama_url||'')||JSON.stringify(draft.areas)!==JSON.stringify(savedAreas);
}
const WORK_UNSAVED_NOTE=' Other changes are not saved yet.';
function updateWorkSaveState(){
 const dirty=Boolean(WORK_SETTINGS)&&workSettingsDirty(),line=$('work-settings-status');
 $('work-save').disabled=!dirty;
 if(dirty&&(line.textContent===''||line.textContent==='Saved'))line.textContent='Unsaved changes';
 else if(!dirty&&(line.textContent==='Unsaved changes'||line.textContent.endsWith(WORK_UNSAVED_NOTE)))line.textContent='';
}
function renderWorkAreas(){
 const limits=WORK_SETTINGS?.settings?.limits||{min_areas:2,max_areas:8,name:40,description:200},areas=workAreasDraft||[];
 $('work-areas').innerHTML=areas.map((area,index)=>`<div class=workAreaRow><i class=workSwatch style="--swatch:${index<8?`var(--w${index+1})`:'var(--w-unclear)'}"></i><input data-area-index=${index} data-area-field=name value="${esc(area.name)}" maxlength=${limits.name} aria-label="Area ${index+1} name"><input data-area-index=${index} data-area-field=description value="${esc(area.description)}" maxlength=${limits.description} aria-label="Area ${index+1} description"><button class="tbtn clearFilters" type=button data-area-remove=${index} ${areas.length<=limits.min_areas?'disabled':''} aria-label="Remove area ${index+1}">Remove</button></div>`).join('');
 $('work-areas-count').textContent=`${areas.length} of ${limits.max_areas} · changing areas relabels them in the background`;
 $('work-area-add').disabled=areas.length>=limits.max_areas;
 updateWorkSaveState();
}
async function loadWorkSettings(){
 try{const response=await fetch('/work-insights/status',{cache:'no-store'});const payload=await response.json();if(payload.ok)renderWorkSettings(payload);}catch(error){}
}
async function saveWorkSettings(payload,message){
 // A toggle saves at once; keep unsaved model, address, and area edits that it does not include.
 const kept=!('model' in payload)&&workSettingsDirty()?{draft:workSettingsDraft(),areas:'areas' in payload||payload.reset_areas?null:workAreasDraft}:null;
 $('work-settings-status').textContent='Saving…';
 try{
  const result=await postWorkAction('/settings/work-insights',payload);
  renderWorkSettings({settings:result.work_insights,status:result.status},{force:true});
  if(kept){$('work-model').value=kept.draft.model;$('work-url').value=kept.draft.ollama_url;if(kept.areas)workAreasDraft=kept.areas;renderWorkAreas();}
  const unsaved=kept&&workSettingsDirty();
  $('work-settings-status').textContent=unsaved?`${message||'Saved.'}${WORK_UNSAVED_NOTE}`:message||'Saved';
  workLastLoadedAt=0;
 }catch(error){$('work-settings-status').textContent=error.message;}
}
$('work-areas').addEventListener('input',event=>{const input=event.target.closest('[data-area-index]');if(!input||!workAreasDraft)return;workAreasDraft[Number(input.dataset.areaIndex)][input.dataset.areaField]=input.value;updateWorkSaveState();});
['work-model','work-url'].forEach(id=>$(id).addEventListener('input',updateWorkSaveState));
$('work-areas').addEventListener('click',event=>{const button=event.target.closest('[data-area-remove]');if(!button||!workAreasDraft)return;workAreasDraft.splice(Number(button.dataset.areaRemove),1);renderWorkAreas();});
$('work-area-add').addEventListener('click',()=>{if(!workAreasDraft)return;workAreasDraft.push({name:'',description:''});renderWorkAreas();$('work-areas').querySelector('.workAreaRow:last-child input')?.focus();});
$('work-enable-button').addEventListener('click',()=>{const box=$('work-enabled');box.checked=true;box.dispatchEvent(new Event('change'));$('work-enable-cta').hidden=true;box.focus();});
$('work-enabled').addEventListener('change',event=>saveWorkSettings({enabled:event.target.checked},event.target.checked?'On. Labeling starts in the background.':'Off. Labeling stopped.'));
$('work-battery').addEventListener('change',event=>saveWorkSettings({pause_on_battery:event.target.checked}));
$('work-live-notify').addEventListener('change',event=>saveWorkSettings({live_notifications:event.target.checked}));
$('work-rate').addEventListener('change',event=>saveWorkSettings({rate_per_minute:event.target.value==='auto'?'auto':Number(event.target.value)}));
$('work-backfill').addEventListener('change',event=>saveWorkSettings({backfill_days:Number(event.target.value)}));
$('work-save').addEventListener('click',()=>{if(workSettingsDirty())saveWorkSettings(workSettingsDraft());});
$('work-areas-reset').addEventListener('click',()=>saveWorkSettings({reset_areas:true},'Default areas restored.'));
$('work-pause').addEventListener('change',async event=>{
 const duration=event.target.value;if(!duration)return;
 try{const result=await postWorkAction('/work-insights/pause',{duration});renderWorkSettings({settings:result.work_insights,status:result.status},{force:true});$('work-settings-status').textContent=duration==='resume'?'Resumed.':'Paused.';}
 catch(error){$('work-settings-status').textContent=error.message;}
 finally{event.target.value='';}
});
$('work-clear').addEventListener('click',async()=>{
 if(!window.confirm('Delete all work labels? They are rebuilt in the background if work insights stay on.'))return;
 try{await postWorkAction('/work-insights/clear',{confirm:true});$('work-settings-status').textContent='Labels deleted.';workLastLoadedAt=0;loadWorkSettings();}
 catch(error){$('work-settings-status').textContent=error.message;}
});
async function applyWorkSessionFilter(params){
 const query=new URLSearchParams();
 ['months','runtime','project','month','start_month','area','work_type','complexity','tier','effort','outcome','model','model_runtime','tag'].forEach(key=>{if(params.get(key))query.set(key,params.get(key));});
 query.set('ids','1');
 const label=params.get('label')||'Work',request=++workFilterRequest;
 if(history.replaceState)history.replaceState(null,'','/#sessions-all');
 setSessionScope('all');showTab('session');showCurrentPanel('sessions');
 workSessionFilter={label,keys:new Set(),loading:true};
 renderCurrentSessions();if(LATEST)renderAllSessions(LATEST.xsession);
 try{
  const response=await fetch(`/work/sessions?${query}`,{cache:'no-store'}),payload=await response.json();
  if(request!==workFilterRequest)return;
  if(!response.ok||!payload.ok)throw new Error(payload.error||'Work filter unavailable');
  workSessionFilter={label,keys:new Set(payload.keys.map(String)),total:payload.total,truncated:payload.truncated};
 }catch(error){if(request!==workFilterRequest)return;workSessionFilter={label:`${label} (unavailable)`,keys:new Set(),failed:true};}
 if(LATEST)renderAllSessions(LATEST.xsession);
}
async function loadDelivery(){
 const request=++deliveryRequest,query=new URLSearchParams({range:deliveryRange});if(deliveryProject)query.set('project',deliveryProject);
 try{
  const response=await fetch(`/git-delivery?${query}`,{cache:'no-store'});if(!response.ok)throw new Error('Git data unavailable');
  const payload=await response.json();if(request!==deliveryRequest)return;
  deliveryLastLoadedAt=Date.now();renderDelivery(payload);
  clearTimeout(deliveryRetryTimer);
  if(!payload?.coverage?.last_checked&&deliveryLoadRetries<8){deliveryLoadRetries++;deliveryRetryTimer=setTimeout(loadDelivery,1500);}else deliveryLoadRetries=0;
 }catch(error){
  if(request!==deliveryRequest)return;
  const coverageBar=$('d-coverage-bar');coverageBar.style.setProperty('--delivery-coverage',0);coverageBar.classList.add('partial');coverageBar.removeAttribute('aria-valuenow');coverageBar.setAttribute('aria-valuetext','Spend coverage unavailable');
  $('d-coverage-percent').textContent='--';$('d-coverage').classList.add('partial');$('d-coverage').textContent='Git evidence unavailable';
  $('d-model-note').textContent='';$('d-model-table').querySelector('tbody').innerHTML='<tr><td colspan=5><div class="deliveryEmpty emptyState">Git evidence unavailable.</div></td></tr>';$('d-project-table').querySelector('tbody').innerHTML='<tr><td colspan=4><div class="deliveryEmpty emptyState">Git evidence unavailable.</div></td></tr>';
  $('d-push-yield').textContent='--';$('d-push-yield-note').textContent='Waiting for output tokens.';$('d-rework').textContent='--';
	  $('d-signals').innerHTML='<div class="deliverySignalEmpty emptyState">Git evidence unavailable.</div>';
	  $('d-shape').innerHTML='<div class="deliveryShapeEmpty emptyState">Git evidence unavailable.</div>';
	  $('d-evidence-spend').textContent='--';setDeliveryEvidenceRatio('Git evidence unavailable.','Git evidence unavailable. Ratios require overlapping spend and Git evidence.');$('d-evidence-freshness').textContent='Git scan unavailable.';
	  ['comparable','spend','git','unavailable'].forEach(key=>{$(`d-evidence-count-${key}`).textContent='--';});
	 }
}
$('d-coverage').addEventListener('click',()=>setDeliveryEvidencePanel($('d-evidence-panel').hidden));
$('d-evidence-close').addEventListener('click',()=>setDeliveryEvidencePanel(false));
document.querySelectorAll('[data-delivery-evidence-filter]').forEach(button=>button.addEventListener('click',()=>{setDeliveryEvidenceFilter(button.dataset.deliveryEvidenceFilter);setDeliveryEvidencePanel(false);}));
$('d-evidence-filter').addEventListener('change',event=>setDeliveryEvidenceFilter(event.target.value,{scroll:false}));
$('d-day-prev').addEventListener('click',()=>selectAdjacentDeliveryDay(-1));
$('d-day-next').addEventListener('click',()=>selectAdjacentDeliveryDay(1));
$('d-day-close').addEventListener('click',dismissGitChartInspector);
$('d-chart-hits').addEventListener('pointerleave',event=>{if(event.pointerType!=='touch')hideDeliveryChartTip();});
$('d-project').addEventListener('change',event=>{deliveryProject=event.target.value;deliverySelectedDay='';deliveryEvidenceFilter='all';localStorage.setItem('tm_delivery_project',deliveryProject);loadDelivery();});
$('d-range').addEventListener('change',event=>{deliveryRange=DELIVERY_RANGES.includes(event.target.value)?event.target.value:'30';deliverySelectedDay='';localStorage.setItem('tm_delivery_range',deliveryRange);loadDelivery();});
$('d-project-table').querySelector('thead').addEventListener('click',event=>{
 const button=event.target.closest('[data-delivery-sort]');if(!button)return;
 const key=button.dataset.deliverySort;deliverySort=deliverySort.key===key?{key,direction:deliverySort.direction==='asc'?'desc':'asc'}:{key,direction:key==='project'?'asc':'desc'};renderDeliveryTable(deliveryPayload?.project_rows||[]);
});
$('d-model-table').querySelector('thead').addEventListener('click',event=>{
 const button=event.target.closest('[data-delivery-model-sort]');if(!button)return;
 const key=button.dataset.deliveryModelSort;deliveryModelSort=deliveryModelSort.key===key?{key,direction:deliveryModelSort.direction==='asc'?'desc':'asc'}:{key,direction:key==='model'?'asc':'desc'};renderDeliveryModelTable(deliveryPayload?.model_rows||[],deliveryPayload?.model_coverage);
});
$('d-mobile-sort').addEventListener('change',event=>{
 const [key,direction]=event.target.value.split(':');
 if(!['project','covered_cost','changed_lines','spend_per_1k'].includes(key)||!['asc','desc'].includes(direction))return;
 deliverySort={key,direction};renderDeliveryTable(deliveryPayload?.project_rows||[]);
});
$('delivery-clear').addEventListener('click',async()=>{
 if(!window.confirm('Clear Token Meter Git evidence? This removes only Token Meter totals and starts a new baseline. Your repositories are not changed.'))return;
 const status=$('delivery-clear-status'),token=agentActionToken();status.textContent='Clearing…';
 try{
  const response=await fetch('/git-delivery/clear',{method:'POST',headers:{'Content-Type':'application/json','X-Token-Meter-Action':token},body:JSON.stringify({confirm:true})});
  const payload=await response.json();if(!response.ok||!payload.ok)throw new Error('clear failed');
  deliveryPayload=null;deliverySelectedDay='';deliveryLastLoadedAt=0;status.textContent='Git evidence cleared. New pushes start a fresh baseline.';loadDelivery();
 }catch(error){status.textContent='Git evidence could not be cleared.';}
});
