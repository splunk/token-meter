
function localDayKey(date=new Date()){
 const y=date.getFullYear(),m=String(date.getMonth()+1).padStart(2,'0'),d=String(date.getDate()).padStart(2,'0');
 return `${y}-${m}-${d}`;
}
function dayLabel(day){
 if(!day)return 'No date';
 return new Date(`${day}T12:00:00`).toLocaleDateString([],{weekday:'short',month:'short',day:'numeric',year:'numeric'});
}
function shortDayLabel(day){
 if(!day)return '--';
 return new Date(`${day}T12:00:00`).toLocaleDateString([],{month:'short',day:'numeric'});
}
function dailyDeltaLabel(current,baseline){
 current=Number(current||0);baseline=Number(baseline||0);
 if(!baseline)return current?'New':'0%';
 const value=Math.round(100*(current-baseline)/baseline);
 return value>0?`+${value}%`:value<0?`\u2212${Math.abs(value)}%`:'0%';
}
// spend-range-logic-start
const SPEND_RUNTIME_COLORS={claude:'var(--provider-claude)',codex:'var(--provider-codex)',cursor:'var(--provider-cursor)',opencode:'var(--provider-opencode)',kiro:'var(--provider-kiro)',grok:'var(--provider-grok)',unknown:'var(--provider-other)'};
const SPEND_RUNTIME_ORDER=['claude','codex','cursor','opencode','kiro','grok','unknown'];
const SPEND_RUNTIME_LABELS={claude:'Claude',codex:'Codex',cursor:'Cursor',opencode:'OpenCode',kiro:'Kiro',grok:'Grok',unknown:'Unknown'};
function spendDayDate(day){
 const match=/^(\d{4})-(\d{2})-(\d{2})$/.exec(String(day||''));
 if(!match)return null;
 const date=new Date(Number(match[1]),Number(match[2])-1,Number(match[3]),12,0,0,0);
 return date.getFullYear()===Number(match[1])&&date.getMonth()===Number(match[2])-1&&date.getDate()===Number(match[3])?date:null;
}
function spendDayKey(date){return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;}
function spendShiftDay(day,amount){const date=spendDayDate(day);if(!date)return '';date.setDate(date.getDate()+amount);return spendDayKey(date);}
function spendRangeWindow(range,from='',to='',now=new Date(),earliest=''){
 const end=spendDayKey(now);
 if(range==='all'){
  const start=spendDayDate(earliest)&&earliest<end?earliest:end;
  return {valid:true,start,end,dayCount:Math.round((spendDayDate(end)-spendDayDate(start))/86400000)+1,error:''};
 }
 if(range==='yesterday'){const day=spendShiftDay(end,-1);return {valid:true,start:day,end:day,dayCount:1,error:''};}
 if(range==='month'){
  const startDate=new Date(now.getFullYear(),now.getMonth(),1,12,0,0,0),endDate=spendDayDate(end);
  return {valid:true,start:spendDayKey(startDate),end,dayCount:Math.round((endDate-startDate)/86400000)+1,error:''};
 }
 if(range==='last_month'){
  const startDate=new Date(now.getFullYear(),now.getMonth()-1,1,12,0,0,0),endDate=new Date(now.getFullYear(),now.getMonth(),0,12,0,0,0);
  return {valid:true,start:spendDayKey(startDate),end:spendDayKey(endDate),dayCount:endDate.getDate(),error:''};
 }
 if(range==='custom'){
  const startDate=spendDayDate(from),endDate=spendDayDate(to);
  if(!startDate||!endDate)return {valid:false,start:from,end:to,dayCount:0,error:'Choose both From and To dates.'};
  if(from>to)return {valid:false,start:from,end:to,dayCount:0,error:'From must be on or before To.'};
  const dayCount=Math.round((endDate-startDate)/86400000)+1;
  return {valid:true,start:from,end:to,dayCount,error:''};
 }
 const dayCount=range==='today'?1:Number(range);
 const count=[1,7,30,90].includes(dayCount)?dayCount:7;
 return {valid:true,start:spendShiftDay(end,1-count),end,dayCount:count,error:''};
}
function spendPreviousWindow(window){
 const end=spendShiftDay(window.start,-1),start=spendShiftDay(end,1-window.dayCount);
 return {valid:true,start,end,dayCount:window.dayCount,error:''};
}
function spendCalendarRows(days,window){
 if(!window?.valid)return [];
 const byDay=new Map((days||[]).filter(row=>spendDayDate(row?.day)).map(row=>[row.day,row]));
 const rows=[];
 for(let day=window.start,guard=0;day&&day<=window.end&&guard<10000;day=spendShiftDay(day,1),guard++){
  const source=byDay.get(day);
  rows.push(source?{...source,cost:Number(source.cost||0),providers:source.providers||[],hasActivity:true}:{day,cost:0,providers:[],coverage:{},provenance:{},usage_basis:'unavailable',availability:{cost:true},hasActivity:false});
 }
 return rows;
}
function spendRuntimeKey(provider){
 const value=String(provider||'').toLowerCase().replace(/[^a-z0-9]/g,'');
 return SPEND_RUNTIME_ORDER.slice(0,-1).find(key=>value.includes(key))||'unknown';
}
function spendRuntimeIdentity(provider){
 const raw=String(provider||'unknown').trim()||'unknown',key=spendRuntimeKey(raw);
 return {id:key==='unknown'?`unknown:${raw.toLowerCase()}`:key,key,label:key==='unknown'&&raw.toLowerCase()!=='unknown'?raw:SPEND_RUNTIME_LABELS[key]};
}
function spendRuntimeTotals(rows){
 const totals=new Map();
 (rows||[]).forEach(day=>(day.providers||[]).forEach(provider=>{
  const identity=spendRuntimeIdentity(provider.provider),current=totals.get(identity.id)||{...identity,cost:0,estimated:false,partial:false,unavailable:false};
  current.cost+=Number(provider.cost||0);
  current.estimated=current.estimated||['local_estimate','mixed'].includes(provider.usage_basis)||['local_estimate','mixed'].includes(provider.provenance?.usage_basis);
  current.unavailable=current.unavailable||provider.availability?.cost===false;
  current.partial=current.partial||provider.coverage?.cost?.complete===false||provider.availability?.cost===false;
  totals.set(identity.id,current);
 }));
 return [...totals.values()].sort((a,b)=>SPEND_RUNTIME_ORDER.indexOf(a.key)-SPEND_RUNTIME_ORDER.indexOf(b.key)||a.label.localeCompare(b.label));
}
// Four equal axis divisions on a round step, so every tick reads as a plain number.
function spendNiceMax(value){
 value=Math.max(0,Number(value)||0);
 if(!value||!Number.isFinite(value))return 1;
 const step=value*1.02/4,magnitude=10**Math.floor(Math.log10(step));
 const factor=[1,1.25,1.5,2,2.5,3,4,5,6,8,10].find(item=>(item!==1.25||magnitude>=10)&&item*magnitude>=step*(1-1e-12));
 return Math.max(1,Number((4*factor*magnitude).toPrecision(12)));
}
const SPEND_GRANULARITIES=['day','week','month'];
function normalizeSpendGranularity(value){return SPEND_GRANULARITIES.includes(value)?value:'day';}
function spendBucketStart(day,granularity){
 const date=spendDayDate(day);if(!date)return '';
 if(granularity==='week')date.setDate(date.getDate()-(date.getDay()+6)%7);
 else if(granularity==='month')date.setDate(1);
 return spendDayKey(date);
}
function spendBucketEnd(start,granularity){
 if(granularity==='week')return spendShiftDay(start,6);
 if(granularity==='month'){const date=spendDayDate(start);return date?spendDayKey(new Date(date.getFullYear(),date.getMonth()+1,0,12,0,0,0)):'';}
 return start;
}
function spendIsEstimated(row){return ['local_estimate','mixed'].includes(row?.usage_basis)||['local_estimate','mixed'].includes(row?.provenance?.usage_basis);}
function spendBucketRows(rows,granularity,today=''){
 granularity=normalizeSpendGranularity(granularity);
 if(granularity==='day')return (rows||[]).map(row=>({...row,granularity,start:row.day,end:row.day,bucketStart:row.day,bucketEnd:row.day,days:1,fullDays:1,clipped:false,inProgress:row.day===today,incomplete:row.day===today}));
 const buckets=[],byKey=new Map();
 (rows||[]).forEach(row=>{
  const key=spendBucketStart(row.day,granularity);if(!key)return;
  let bucket=byKey.get(key);
  if(!bucket){
   const bucketEnd=spendBucketEnd(key,granularity);
   bucket={day:row.day,granularity,start:row.day,end:row.day,bucketStart:key,bucketEnd,days:0,fullDays:Math.round((spendDayDate(bucketEnd)-spendDayDate(key))/86400000)+1,cost:0,providers:[],hasActivity:false,availability:{cost:true},coverage:{cost:{complete:true}},usage_basis:'',provenance:{}};
   byKey.set(key,bucket);buckets.push(bucket);
  }
  bucket.end=row.day;bucket.days+=1;bucket.cost+=Number(row.cost||0);
  bucket.providers.push(...(row.providers||[]));
  if(!row.hasActivity)return;
  bucket.hasActivity=true;
  if(row.availability?.cost===false)bucket.availability.cost=false;
  if(row.coverage?.cost?.complete===false)bucket.coverage.cost.complete=false;
  if(spendIsEstimated(row))bucket.usage_basis='mixed';
 });
 return buckets.map(bucket=>{
  const clipped=bucket.days<bucket.fullDays,inProgress=Boolean(today)&&bucket.start<=today&&today<=bucket.bucketEnd;
  return {...bucket,clipped,inProgress,incomplete:clipped||inProgress};
 });
}
function spendGranularityOptions(rows,today=''){
 return Object.fromEntries(SPEND_GRANULARITIES.map(granularity=>{
  if(granularity==='day')return [granularity,true];
  const buckets=spendBucketRows(rows,granularity,today);
  return [granularity,buckets.length>=2&&buckets.some(bucket=>!bucket.incomplete)];
 }));
}
function spendAutoGranularity(dayCount){return dayCount>186?'month':dayCount>35?'week':'day';}
function spendResolveGranularity(choice,rows,today=''){
 const options=spendGranularityOptions(rows,today),order=['month','week','day'];
 const wanted=SPEND_GRANULARITIES.includes(choice)?choice:spendAutoGranularity((rows||[]).length);
 return {granularity:order.slice(order.indexOf(wanted)).find(granularity=>options[granularity])||'day',options};
}
function spendAverageBasis(buckets){
 return (buckets||[]).filter(row=>row.granularity==='day'||!row.incomplete);
}
function spendAverageReference(rows,axisMax){
 const costs=(rows||[]).map(row=>{const value=Number(row?.cost||0);return Number.isFinite(value)&&value>0?value:0;});
 if(!costs.length)return {value:0,ratio:0};
 const value=costs.reduce((sum,cost)=>sum+cost,0)/costs.length,max=Number(axisMax)||0;
 return {value,ratio:max>0?Math.min(1,Math.max(0,value/max)):0};
}
function spendDayNavigation(rows,focusDay=''){
 const days=(rows||[]).map(row=>row.day),resolved=days.includes(focusDay)?focusDay:(days.at(-1)||'');
 return {focusDay:resolved,buttons:days.map(day=>({day,tabIndex:day===resolved?0:-1,selected:false}))};
}
function spendSessionMetricValue(row,metric){
 let value=null;
 if(metric==='cost'&&row?.availability?.cost===true)value=Number(row.cost);
 else if(metric==='input'&&(row?.availability?.input_tokens===true||row?.availability?.tokens===true))value=Number(row.input_tokens);
 else if(metric==='executions'&&row?.turns!==null&&row?.turns!==undefined)value=Number(row.turns);
 else if(metric==='active'&&row?.duration_available===true)value=Number(row.duration_s);
 return Number.isFinite(value)&&value>=0?value:null;
}
function spendQuantile(values,quantile){
 const sorted=(values||[]).filter(value=>Number.isFinite(value)).slice().sort((a,b)=>a-b);
 if(!sorted.length)return 0;
 const position=(sorted.length-1)*quantile,lower=Math.floor(position),upper=Math.ceil(position),weight=position-lower;
 return sorted[lower]+(sorted[upper]-sorted[lower])*weight;
}
function spendDistribution(values){
 const clean=(values||[]).filter(value=>Number.isFinite(value));
 if(!clean.length)return {count:0,p10:0,p25:0,median:0,mean:0,p75:0,p90:0};
 return {count:clean.length,p10:spendQuantile(clean,.1),p25:spendQuantile(clean,.25),median:spendQuantile(clean,.5),mean:clean.reduce((sum,value)=>sum+value,0)/clean.length,p75:spendQuantile(clean,.75),p90:spendQuantile(clean,.9)};
}
function spendSessionAnalytics(rows){
 rows=rows||[];
 const coveredCosts=rows.map(row=>spendSessionMetricValue(row,'cost')).filter(value=>value!==null).sort((a,b)=>b-a),total=coveredCosts.reduce((sum,value)=>sum+value,0),topCount=coveredCosts.length?Math.ceil(coveredCosts.length*.1):0,topCost=coveredCosts.slice(0,topCount).reduce((sum,value)=>sum+value,0);
 return {
  concentration:{covered:coveredCosts.length,total,top_count:topCount,top_cost:topCost,top_share:total?topCost/total:0},
  distributions:{
   cost:spendDistribution(rows.map(row=>spendSessionMetricValue(row,'cost')).filter(value=>value!==null)),
   input:spendDistribution(rows.map(row=>spendSessionMetricValue(row,'input')).filter(value=>value!==null)),
   executions:spendDistribution(rows.map(row=>spendSessionMetricValue(row,'executions')).filter(value=>value!==null)),
   active:spendDistribution(rows.map(row=>spendSessionMetricValue(row,'active')).filter(value=>value!==null)),
  },
 };
}
function spendScatterRows(rows,metric){
 const valueMetric=metric==='input'?'input':'cost';
 return (rows||[]).filter(row=>{
  const active=spendSessionMetricValue(row,'active'),value=spendSessionMetricValue(row,valueMetric);
  return active!==null&&active>0&&value!==null&&value>=0;
 });
}
function normalizeSpendRangeChoice(value){return ['today','yesterday','7','30','90','month','last_month','all','custom'].includes(value)?value:'30';}
// spend-range-logic-end
const SPEND_RANGE_KEY='tm_spend_range_v1';
const SPEND_SCATTER_METRIC_KEY='tm_spend_scatter_metric_v1';
let spendGranularityChoice='auto',spendChartRows=[];
let spendRangeChoice='30',spendCustomFrom='',spendCustomTo='',spendFocusDay='',spendInspectDay='',spendCurrentRows=[],spendCurrentWindow=null,spendCurrentSource=[],spendResizeTimer=0,spendLogsRequestKey='',spendLogsRenderedRange='',spendLogsRequestSeq=0,spendInsightPayload=null,spendInsightRenderKey='',spendScatterMetric=localStorage.getItem(SPEND_SCATTER_METRIC_KEY)==='input'?'input':'cost';
try{
 const saved=JSON.parse(localStorage.getItem(SPEND_RANGE_KEY)||'{}');
 spendRangeChoice=normalizeSpendRangeChoice(saved.range);
 spendCustomFrom=String(saved.from||'');spendCustomTo=String(saved.to||'');
}catch(error){}
const SPEND_PROJECTS_KEY='tm_spend_projects_v1',SPEND_PROJECT_LIMIT=50;
let spendProjects=(()=>{try{const saved=JSON.parse(localStorage.getItem(SPEND_PROJECTS_KEY)||'[]');return Array.isArray(saved)?[...new Set(saved.filter(project=>typeof project==='string'&&project))].slice(0,SPEND_PROJECT_LIMIT):[];}catch(error){return [];}})();
let spendProjectDays=null,spendProjectDaysKey='',spendProjectDaysSeq=0,spendProjectOptions=[],spendProjectQuery='',spendProjectOptionsHtml='',spendLogsPayload=null,spendEarliestDay='';
function persistSpendProjects(){try{if(spendProjects.length)localStorage.setItem(SPEND_PROJECTS_KEY,JSON.stringify(spendProjects));else localStorage.removeItem(SPEND_PROJECTS_KEY);}catch(error){}}
function spendProjectName(project){return project===OTHER_LOCAL_SESSIONS_PROJECT?'Other local sessions':String(project).replace(/[\\/]+$/,'').split(/[\\/]/).pop()||project;}
function spendProjectSummary(){return !spendProjects.length?'All projects':spendProjects.length===1?spendProjectName(spendProjects[0]):`${f(spendProjects.length)} projects`;}
function spendFilterLogs(payload){
 if(!spendProjects.length||!payload?.sessions)return payload;
 const keep=new Set(spendProjects),sessions=payload.sessions.filter(row=>keep.has(row.project_key));
 return {...payload,sessions,total_sessions:sessions.length,total_cost:sessions.reduce((sum,row)=>sum+Number(row.cost||0),0)};
}
function paintSpendProjectOptions(sessions){
 if(sessions){
  const byProject=new Map();
  sessions.forEach(row=>{if(row.project_key)byProject.set(row.project_key,(byProject.get(row.project_key)||0)+Number(row.cost||0));});
  spendProjectOptions=[...byProject].map(([key,cost])=>({key,cost})).sort((a,b)=>b.cost-a.cost||a.key.localeCompare(b.key));
 }
 const listed=new Set(spendProjectOptions.map(option=>option.key)),query=spendProjectQuery.trim().toLowerCase();
 const shown=[...spendProjectOptions,...spendProjects.filter(key=>!listed.has(key)).map(key=>({key,cost:0}))].filter(option=>!query||projectFilterLabel(option.key).toLowerCase().includes(query));
 const html=`<label class="modelOption all"><input type=checkbox data-spend-project=all ${spendProjects.length?'':'checked'}><span>All projects</span></label>`+shown.map(option=>`<label class="modelOption spendProjectOption" title="${esc(projectFilterLabel(option.key))}"><input type=checkbox data-spend-project="${esc(option.key)}" ${spendProjects.includes(option.key)?'checked':''}><span>${esc(spendProjectName(option.key))}</span><small>${money(option.cost)}</small></label>`).join('')+(shown.length?'':'<div class=emptyState>No matching projects</div>');
 if(html!==spendProjectOptionsHtml){$('s-project-options').innerHTML=html;spendProjectOptionsHtml=html;}
 $('s-project-summary').textContent=spendProjectSummary();
}
function loadSpendProjectDays(key){
 spendProjectDaysKey=key;
 const request=++spendProjectDaysSeq;
 fetch(`/spend/days?${spendProjects.map(project=>`project=${encodeURIComponent(project)}`).join('&')}`,{cache:'no-store'}).then(response=>response.json()).then(payload=>{
  if(request!==spendProjectDaysSeq)return;
  if(!payload?.ok)throw new Error(payload?.error||'Project spend could not be loaded.');
  spendProjectDays={key,days:payload.days||[]};if(LATEST)renderSpend(LATEST.xsession);
 }).catch(()=>{if(request===spendProjectDaysSeq)spendProjectDaysKey='';});
}
function persistSpendRange(){try{localStorage.setItem(SPEND_RANGE_KEY,JSON.stringify({range:spendRangeChoice,from:spendCustomFrom,to:spendCustomTo}));}catch(error){}}
function openMonthSpend(){
 spendRangeChoice='month';spendGranularityChoice='auto';persistSpendRange();setHashRoute('spend');
}
$('current-month-spend').onclick=openMonthSpend;
function spendRangeLabel(window){return window.dayCount===1?dayLabel(window.start):`${dayLabel(window.start)} – ${dayLabel(window.end)}`;}
function spendPct(value){value=Number(value||0);return value>0&&value<.01?'<1%':pct(value);}
function spendEvidence(rows){
 const active=rows.filter(row=>row.hasActivity),unavailable=active.some(row=>row.availability?.cost===false);
 const partial=active.some(row=>row.coverage?.cost?.complete===false||row.availability?.cost===false);
 const estimated=active.some(row=>['local_estimate','mixed'].includes(row.usage_basis)||['local_estimate','mixed'].includes(row.provenance?.usage_basis));
 return {active:active.length,unavailable,partial,estimated,allUnavailable:Boolean(active.length)&&active.every(row=>row.availability?.cost===false)};
}
function spendDayDetails(row){
 const runtimes=spendRuntimeTotals([row]),available=row.availability?.cost!==false,total=Number(row.cost||0);
 if(!row.hasActivity)return {totalText:money(0),note:'no recorded activity',runtimes};
 if(!available&&!total)return {totalText:'Unavailable',note:'recorded activity has no cost evidence',runtimes};
 const partial=row.coverage?.cost?.complete===false||!available,estimated=['local_estimate','mixed'].includes(row.usage_basis)||['local_estimate','mixed'].includes(row.provenance?.usage_basis);
 return {totalText:money(total),note:[partial?'partial coverage':'',estimated?'estimated':''].filter(Boolean).join(' · '),runtimes};
}
const SPEND_GRANULARITY_COPY={
 day:{unit:'day',total:'Daily total',note:'Line = average'},
 week:{unit:'wk',total:'Weekly total',note:'Line = average'},
 month:{unit:'mo',total:'Monthly total',note:'Line = average'},
};
function spendMonthLabel(day,style='long'){return new Date(`${day}T12:00:00`).toLocaleDateString([],style==='long'?{month:'long',year:'numeric'}:{month:'short'});}
function spendBucketLabel(row){
 if(row.granularity==='week')return `Week of ${shortDayLabel(row.bucketStart)}`;
 if(row.granularity==='month')return spendMonthLabel(row.bucketStart);
 return dayLabel(row.day);
}
function spendBucketNote(row){
 if(row.granularity==='day')return row.inProgress?'today, still in progress':'';
 const unit=row.granularity==='week'?'week':'month';
 if(row.clipped)return `partial ${unit} · ${row.days} of ${row.fullDays} days in range${row.inProgress?' · in progress':''}`;
 return row.inProgress?`${unit} in progress`:'';
}
function spendDayAria(row){
 const details=spendDayDetails(row),parts=details.runtimes.filter(runtime=>runtime.cost>0||runtime.unavailable).map(runtime=>`${runtime.label} ${runtime.unavailable&&!runtime.cost?'cost unavailable':money(runtime.cost)}${runtime.estimated?' estimated':''}${runtime.partial?' partial':''}`),bucketNote=spendBucketNote(row);
 return `${spendBucketLabel(row)}, ${details.totalText}${details.note?`, ${details.note}`:''}${bucketNote?`, ${bucketNote}`:''}${parts.length?`, ${parts.join(', ')}`:''}`;
}
function spendAxisText(row,spansYears){
 if(row.granularity==='month'){const label=spendMonthLabel(row.bucketStart,'short');return spansYears||row.bucketStart.endsWith('-01-01')?`${label} ’${row.bucketStart.slice(2,4)}`:label;}
 return shortDayLabel(row.granularity==='week'?row.bucketStart:row.day);
}
function spendAxisLabel(row,index,count,chartWidth,spansYears=false){
 const maxLabels=Math.max(2,Math.floor(Math.max(1,chartWidth)/54));
 const step=Math.max(1,Math.ceil(Math.max(1,count-1)/Math.max(1,maxLabels-1)));
 return index===0||index===count-1||(index%step===0&&count-1-index>=step)?spendAxisText(row,spansYears):'';
}
function spendBarBody(row,index,count,axisMax,chartWidth,spansYears){
 const runtimes=spendRuntimeTotals([row]).filter(runtime=>runtime.cost>0),total=Number(row.cost||0),height=total?Math.max(1.5,100*total/axisMax):0;
 const segments=runtimes.map(runtime=>`<i class=spendStackSegment data-estimated="${runtime.estimated}" data-partial="${runtime.partial}" style="--spend-color:${SPEND_RUNTIME_COLORS[runtime.key]};--segment-share:${total?100*runtime.cost/total:0}%"></i>`).join('');
 const axisLabel=spendAxisLabel(row,index,count,chartWidth,spansYears);
 return `<span class=spendDayTrack><span class="spendBarValue mono">${total?money(total):''}</span>${total?`<span class=spendDayStack style="--spend-height:${height}%">${segments}</span>`:''}</span><span class=spendDayLabel>${esc(axisLabel)}</span>`;
}
function renderSpendAxis(axisMax){
 const ticks=[1,.75,.5,.25,0].map(ratio=>`<span>${money(axisMax*ratio)}</span>`).join('');
 if($('s-y-axis')._spendTicks!==ticks){$('s-y-axis').innerHTML=ticks;$('s-y-axis')._spendTicks=ticks;}
}
function renderSpendAverage(rows,axisMax){
 const line=$('s-average-line'),reference=spendAverageReference(spendAverageBasis(rows),axisMax),track=$('s-chart-inner').querySelector('.spendDayTrack');
 if(!track||reference.value<=0){line.hidden=true;return;}
 const trackRect=track.getBoundingClientRect(),plotRect=line.parentElement.getBoundingClientRect(),unit=SPEND_GRANULARITY_COPY[rows[0]?.granularity||'day'].unit;
 const top=trackRect.top-plotRect.top+(1-reference.ratio)*trackRect.height,label=`avg ${money(reference.value)}/${unit}`;
 line.style.top=`${Math.round(top*10)/10}px`;line.hidden=false;
 const value=line.querySelector('span');if(value.textContent!==label)value.textContent=label;
}
function spendPaintNavigation(){
 const state=spendDayNavigation(spendChartRows,spendFocusDay);spendFocusDay=state.focusDay;
 [...$('s-chart-inner').querySelectorAll('[data-spend-day]')].forEach((button,index)=>{
  const item=state.buttons[index];
  button.classList.toggle('selected',Boolean(item?.selected));button.tabIndex=item?.tabIndex??-1;button.removeAttribute('aria-pressed');
 });
}
function updateSpendBar(button,row,index,count,axisMax,chartWidth,{peak=false,spansYears=false}={}){
 const edge=count>1?(index===0?'first':index===count-1?'last':''):'';
 button.dataset.spendDay=row.day;
 button.setAttribute('aria-label',spendDayAria(row));
 if(edge)button.dataset.axisEdge=edge;else delete button.dataset.axisEdge;
 button.dataset.peak=String(peak);if(peak&&index<=2)button.dataset.peakEdge='first';else if(peak&&index>=count-3)button.dataset.peakEdge='last';else delete button.dataset.peakEdge;
 button.dataset.incomplete=String(Boolean(row.incomplete));
 const body=spendBarBody(row,index,count,axisMax,chartWidth,spansYears);
 if(button._spendBody!==body){button.innerHTML=body;button._spendBody=body;}
}
function reconcileSpendBars(rows){
 const granularity=rows[0]?.granularity||'day',inner=$('s-chart-inner'),keys=`${granularity}:${rows.map(row=>row.day).join('|')}`,costs=rows.map(row=>Number(row.cost||0)),axisMax=spendNiceMax(Math.max(0,...costs)),chartWidth=Math.max(1,inner.clientWidth||$('s-chart').clientWidth||1);
 const existing=[...inner.querySelectorAll('[data-spend-day]')],same=inner.dataset.keys===keys&&existing.length===rows.length;
 const peakCost=Math.max(0,...costs),peakIndex=rows.length>1&&peakCost>0?costs.indexOf(peakCost):-1,spansYears=Boolean(rows.length)&&rows[0].bucketStart.slice(0,4)!==rows.at(-1).bucketStart.slice(0,4);
 inner.dataset.granularity=granularity;inner.dataset.dense=String(rows.length>45);inner.dataset.values=rows.length<=14?'shown':'hover';
 renderSpendAxis(axisMax);
 if(!same){
  inner.innerHTML=rows.map(row=>`<button class=spendDay type=button data-spend-day="${esc(row.day)}"></button>`).join('');
  inner.dataset.keys=keys;
 }
 [...inner.querySelectorAll('[data-spend-day]')].forEach((button,index)=>updateSpendBar(button,rows[index],index,rows.length,axisMax,chartWidth,{peak:index===peakIndex,spansYears}));
 renderSpendAverage(rows,axisMax);
 spendPaintNavigation();
}
function setSpendLogsHtml(html){
 const list=$('s-logs');
 if(list._spendHtml===html)return;
 const scrollTop=list.scrollTop;
 list.innerHTML=html;list._spendHtml=html;list.scrollTop=scrollTop;
}
function spendInsightMetricFormat(metric,value){
 if(metric==='cost')return money(value);
 if(metric==='input')return compactNumber(value);
 if(metric==='active')return waitFmt(value);
 const rounded=Math.round(Number(value||0)*10)/10;
 return Number.isInteger(rounded)?rounded.toLocaleString():rounded.toLocaleString(undefined,{maximumFractionDigits:1});
}
function spendShapeRow(metric,label,note,distribution,totalRows){
 if(!distribution.count)return `<div class=spendShapeRow><div class=spendShapeIdentity><b>${esc(label)}</b><small>${esc(note)}</small></div><div class=spendShapeCoverage>0 / ${f(totalRows)}</div><div class=spendShapeRail></div><div class=spendShapeValues><span data-label=P10>--</span><span data-label=Median>--</span><span data-label=Mean>--</span><span data-label=P90>--</span></div></div>`;
 const span=Math.max(.000001,distribution.p90-distribution.p10),position=value=>Math.max(0,Math.min(100,100*(value-distribution.p10)/span));
 const style=`--p25:${position(distribution.p25)}%;--p75:${position(distribution.p75)}%;--median:${position(distribution.median)}%;--mean:${position(distribution.mean)}%`;
 const format=value=>spendInsightMetricFormat(metric,value);
 return `<div class=spendShapeRow><div class=spendShapeIdentity><b>${esc(label)}</b><small>${esc(note)}</small></div><div class=spendShapeCoverage>${f(distribution.count)} / ${f(totalRows)}</div><div class=spendShapeRail style="${style}"><i class=spendShapeIqr></i><i class=spendShapeMedian title="Median ${esc(format(distribution.median))}"></i><i class=spendShapeMean title="Mean ${esc(format(distribution.mean))}"></i></div><div class=spendShapeValues><span data-label=P10 title="10th percentile">${esc(format(distribution.p10))}</span><span data-label=Median>${esc(format(distribution.median))}</span><span data-label=Mean>${esc(format(distribution.mean))}</span><span data-label=P90 title="90th percentile">${esc(format(distribution.p90))}</span></div></div>`;
}
function renderSpendShape(distributions,totalRows){
 $('s-shape').innerHTML=`<div class=spendShapeHeader><span>Measure</span><span>Coverage</span><span>Middle 50% · median / mean</span><span><i>P10</i><i>Median</i><i>Mean</i><i>P90</i></span></div>${spendShapeRow('cost','Period spend','selected range',distributions.cost,totalRows)}${spendShapeRow('input','Processed input','full session',distributions.input,totalRows)}${spendShapeRow('executions','Executions','full session',distributions.executions,totalRows)}${spendShapeRow('active','Active time','full session',distributions.active,totalRows)}`;
}
function spendScatterTipHtml(row){
 const selected=spendSessionMetricValue(row,spendScatterMetric),active=spendSessionMetricValue(row,'active'),estimated=['local_estimate','mixed'].includes(row.usage_basis)||['local_estimate','mixed'].includes(row.provenance?.usage_basis);
 return `<b title="${esc(row.title||'Untitled session')}">${esc(row.title||'Untitled session')}</b><span><em>${spendScatterMetric==='input'?'Processed input':'Period spend'}</em><strong class=mono>${esc(spendInsightMetricFormat(spendScatterMetric,selected))}${spendScatterMetric==='cost'&&estimated?' est':''}</strong></span><span><em>Active time</em><strong class=mono>${esc(waitFmt(active))}</strong></span><span><em>Executions</em><strong class=mono>${f(row.turns)}</strong></span><span><em>${esc(row.label||row.provider||'Runtime')}</em><strong>${esc(row.project||'No project')}</strong></span>`;
}
function hideSpendScatterTip(){$('s-scatter-tip').hidden=true;}
function showSpendScatterTip(row,target,event){
 const tip=$('s-scatter-tip'),wrap=tip.parentElement,wrapRect=wrap.getBoundingClientRect(),targetRect=target.getBoundingClientRect();
 tip.innerHTML=spendScatterTipHtml(row);tip.hidden=false;
 const pointerX=event?.clientX||targetRect.left+targetRect.width/2,pointerY=event?.clientY||targetRect.top;
 tip.style.left=Math.max(6,Math.min(wrapRect.width-tip.offsetWidth-6,pointerX-wrapRect.left+12))+'px';
 tip.style.top=Math.max(6,Math.min(wrapRect.height-tip.offsetHeight-6,pointerY-wrapRect.top-tip.offsetHeight-10))+'px';
}
function drawSpendScatter(rows,analytics=spendSessionAnalytics(rows)){
 const svg=$('s-scatter-svg'),points=spendScatterRows(rows,spendScatterMetric),valueMetric=spendScatterMetric==='input'?'input':'cost',metricLabel=spendScatterMetric==='input'?'processed input':'selected-period spend';
 [...$('s-scatter-metric').querySelectorAll('[data-scatter-metric]')].forEach(button=>{const on=button.dataset.scatterMetric===spendScatterMetric;button.classList.toggle('on',on);button.setAttribute('aria-pressed',String(on));});
 $('s-scatter-coverage').textContent=`${f(points.length)} of ${f((rows||[]).length)} sessions plotted`;$('s-scatter-missing').hidden=points.length>=(rows||[]).length;
 svg.setAttribute('aria-label',`${metricLabel} by full-session active time for ${points.length} sessions. Each point opens the session.`);
 hideSpendScatterTip();
 const VW=Math.max(320,Math.round(svg.clientWidth||1040)),VH=Math.max(270,Math.round(svg.clientHeight||340)),mobile=VW<560,ML=mobile?54:66,MR=mobile?18:28,MT=26,MB=48,PW=VW-ML-MR,PH=VH-MT-MB;
 svg.setAttribute('viewBox',`0 0 ${VW} ${VH}`);
 if(!points.length){svg.innerHTML=`<text x="${VW/2}" y="${VH/2}" fill="${CHART.lbl}" text-anchor="middle" font-size="12">No sessions have both ${esc(metricLabel)} and active-time evidence</text>`;return;}
 const xMax=Math.max(1,...points.map(row=>spendSessionMetricValue(row,'active'))),yMax=Math.max(1,...points.map(row=>spendSessionMetricValue(row,valueMetric))),xLog=Math.log1p(xMax),yLog=Math.log1p(yMax),x=value=>ML+PW*Math.log1p(value)/xLog,y=value=>MT+PH-PH*Math.log1p(value)/yLog;
 let html='';
 for(let index=0;index<=4;index++){
  const ratio=index/4,gx=ML+PW*ratio,gy=MT+PH-PH*ratio,xValue=Math.expm1(xLog*ratio),yValue=Math.expm1(yLog*ratio);
  html+=`<line x1="${gx}" x2="${gx}" y1="${MT}" y2="${MT+PH}" stroke="${CHART.grid}"/><line x1="${ML}" x2="${ML+PW}" y1="${gy}" y2="${gy}" stroke="${CHART.grid}"/><text x="${gx}" y="${VH-24}" text-anchor="middle" fill="${CHART.lbl}" font-size="11">${esc(waitFmt(xValue))}</text><text x="${ML-8}" y="${gy+3}" text-anchor="end" fill="${CHART.lbl}" font-size="11">${esc(spendInsightMetricFormat(valueMetric,yValue))}</text>`;
 }
 const activeMedian=analytics.distributions.active.median,valueMedian=analytics.distributions[valueMetric].median;
 if(activeMedian>0)html+=`<line x1="${x(activeMedian)}" x2="${x(activeMedian)}" y1="${MT}" y2="${MT+PH}" stroke="${tint('signal',.52)}" stroke-dasharray="4 5"/><text x="${Math.min(ML+PW-4,x(activeMedian)+5)}" y="${MT+11}" fill="${CHART.lbl}" font-size="11">median time</text>`;
 if(valueMedian>0)html+=`<line x1="${ML}" x2="${ML+PW}" y1="${y(valueMedian)}" y2="${y(valueMedian)}" stroke="${tint('signal',.52)}" stroke-dasharray="4 5"/><text x="${ML+5}" y="${Math.max(MT+10,y(valueMedian)-5)}" fill="${CHART.lbl}" font-size="11">median</text>`;
 const ranked=(rows||[]).filter(row=>spendSessionMetricValue(row,'cost')!==null).slice().sort((a,b)=>spendSessionMetricValue(b,'cost')-spendSessionMetricValue(a,'cost')),topIds=new Set(ranked.slice(0,analytics.concentration.top_count).map(sessionRowKey));
 points.slice().sort((a,b)=>Number(topIds.has(sessionRowKey(a)))-Number(topIds.has(sessionRowKey(b)))).forEach(row=>{
  const active=spendSessionMetricValue(row,'active'),value=spendSessionMetricValue(row,valueMetric),runtime=spendRuntimeKey(row.provider),top=topIds.has(sessionRowKey(row)),label=`Open ${row.title||'untitled session'}: ${spendInsightMetricFormat(valueMetric,value)} ${metricLabel}, ${waitFmt(active)} active time`;
  html+=`<a class=spendScatterLink href="${esc(sessionRoute(sessionRowKey(row)))}" data-spend-insight-session="${esc(sessionRowKey(row))}" aria-label="${esc(label)}"><circle class=spendScatterPoint cx="${x(active)}" cy="${y(value)}" r="${top?6.5:5.5}" style="fill:${SPEND_RUNTIME_COLORS[runtime]}" fill-opacity="${top ? .92 : .7}" stroke="${top?'var(--signal-hi)':CHART.ink}" stroke-width="${top?2.5:1.5}" vector-effect="non-scaling-stroke"><title>${esc(label)}</title></circle></a>`;
 });
 html+=`<text x="${ML}" y="13" fill="${CHART.lbl}" font-size="11">${esc(metricLabel)}</text><text x="${ML+PW/2}" y="${VH-6}" text-anchor="middle" fill="${CHART.lbl}" font-size="11">full-session active time · log scale</text>`;
 svg.innerHTML=html;
 [...svg.querySelectorAll('[data-spend-insight-session]')].forEach(link=>{
  const row=points.find(item=>sessionRowKey(item)===link.dataset.spendInsightSession);
  link.onpointerenter=event=>showSpendScatterTip(row,link,event);link.onpointermove=event=>showSpendScatterTip(row,link,event);link.onpointerleave=hideSpendScatterTip;link.onfocus=()=>showSpendScatterTip(row,link);link.onblur=hideSpendScatterTip;
 });
}
function renderSpendSessionInsights(payload,window){
 $('s-insights-range').textContent=spendRangeLabel(window);
 if(payload?.loading||payload?.error){
  const statusKey=`${window.start}|${window.end}|${payload?.error||'loading'}`;spendInsightPayload=null;if(statusKey===spendInsightRenderKey)return;spendInsightRenderKey=statusKey;$('s-concentration').textContent='--';$('s-concentration-note').textContent=payload?.error||'Loading range evidence…';$('s-concentration-bar').querySelector('i').style.width='0%';$('s-concentration-bar').setAttribute('aria-valuenow','0');$('s-concentration-sessions').innerHTML='';$('s-shape').innerHTML=`<div class="spendShapeEmpty emptyState">${esc(payload?.error||'Loading session distributions…')}</div>`;drawSpendScatter([]);return;
 }
 spendInsightPayload=payload;
 const rows=payload?.sessions||[],renderKey=`${window.start}|${window.end}|`+rows.map(row=>[row.id,row.title,row.project,row.cost,row.input_tokens,row.turns,row.duration_s,row.duration_available,row.duration_basis,row.usage_basis,row.availability?.cost,row.availability?.input_tokens].join('~')).join('|');
 if(renderKey===spendInsightRenderKey)return;
 spendInsightRenderKey=renderKey;
 const focused=document.activeElement?.closest?.('[data-spend-insight-session]'),focusedId=focused?.dataset.spendInsightSession||'',focusedScatter=Boolean(focused?.closest?.('#s-scatter-svg')),analytics=spendSessionAnalytics(rows),concentration=analytics.concentration,share=concentration.top_share;
 $('s-concentration').textContent=concentration.covered?spendPct(share):'--';
 $('s-concentration-note').textContent=concentration.covered?`${f(concentration.top_count)} of ${f(concentration.covered)} cost-covered ${countWord(concentration.covered,'session')} account for ${money(concentration.top_cost)} of ${money(concentration.total)}.${rows.some(hasLocalEstimate)?' Includes local estimates.':''}`:'No cost-covered sessions in this period.';
 $('s-concentration-bar').querySelector('i').style.width=Math.max(0,Math.min(100,100*share))+'%';$('s-concentration-bar').setAttribute('aria-valuenow',String(Math.round(100*share)));
 const topRows=rows.filter(row=>spendSessionMetricValue(row,'cost')!==null).slice().sort((a,b)=>spendSessionMetricValue(b,'cost')-spendSessionMetricValue(a,'cost')).slice(0,Math.min(3,concentration.top_count));
 $('s-concentration-sessions').innerHTML=topRows.map(row=>{const cost=spendSessionMetricValue(row,'cost'),estimated=['local_estimate','mixed'].includes(row.usage_basis)||['local_estimate','mixed'].includes(row.provenance?.usage_basis);return `<button class=spendImpactRow data-spend-insight-session="${esc(sessionRowKey(row))}" type=button title="Open ${esc(row.title||'Untitled session')}"><span class=spendImpactName>${esc(row.title||'Untitled session')}</span><span class=spendImpactValue><b class=mono>${money(cost)}${estimated?' est':''}</b><small>${spendPct(concentration.total?cost/concentration.total:0)} of covered spend</small></span></button>`;}).join('');
 renderSpendShape(analytics.distributions,rows.length);drawSpendScatter(rows,analytics);
 if(focusedId)requestAnimationFrame(()=>{const root=focusedScatter?$('s-scatter-svg'):$('s-concentration-sessions'),target=[...root.querySelectorAll('[data-spend-insight-session]')].find(element=>element.dataset.spendInsightSession===focusedId);target?.focus({preventScroll:true});});
}
function renderSpendLogs(payload,window){
 if(payload?.sessions){paintSpendProjectOptions(payload.sessions);payload=spendFilterLogs(payload);}
 const rows=payload?.sessions||[],rangeTotal=Number(payload?.total_cost||0);
 $('s-log-day').textContent=spendRangeLabel(window);
 renderSpendSessionInsights(payload,window);
 if(payload?.loading){$('s-log-count').textContent='';setSpendLogsHtml('<div class="spendLogEmpty emptyState">Loading range logs…</div>');return;}
 if(payload?.error){$('s-log-count').textContent='';setSpendLogsHtml(`<div class="spendLogEmpty emptyState">${esc(payload.error)}</div>`);return;}
 $('s-log-count').textContent=`${f(rows.length)} logs`;
 if(!rows.length){setSpendLogsHtml('<div class="spendLogEmpty emptyState">No spend-contributing logs in this range.</div>');return;}
 const maxCost=Math.max(.001,...rows.map(row=>Number(row.cost||0)));
 setSpendLogsHtml(rows.map((row,index)=>{
  const available=metricAvailable(row,'cost'),estimated=hasLocalEstimate(row),cost=Number(row.cost||0),share=available&&rangeTotal?pct(cost/rangeTotal):'0%',days=Math.max(1,Number(row.active_days)||1);
  return `<div class=spendLogRow><div class=spendLogRank>${index+1}</div><div class=spendLogMain><div class=spendLogName title="${esc(row.title||'Untitled session')}">${esc(row.title||'Untitled session')}</div><div class=spendLogMeta>${esc(row.project||'No project')} · ${esc(row.label||row.provider||'Unknown runtime')} · ${f(days)} ${countWord(days,'day')}${estimated?' · local est':''}</div><div class=spendLogTrack><i style="width:${available?100*cost/maxCost:0}%"></i></div></div><div class=spendLogValue><strong class=mono>${costValueHtml(money(cost)+estimateSuffix(row),available)}</strong><span>${available?`${share} of range spend`:'cost unavailable'}</span></div><button class=tbtn data-spend-session="${esc(sessionRowKey(row))}" type=button>Open</button></div>`;
 }).join(''));
}
function loadSpendLogs(window,generatedAt){
 const rangeKey=`${window.start}|${window.end}`,requestKey=`${rangeKey}|${generatedAt||0}`;
 if(requestKey===spendLogsRequestKey)return;
 spendLogsRequestKey=requestKey;
 const request=++spendLogsRequestSeq;
 if(rangeKey!==spendLogsRenderedRange)renderSpendLogs({loading:true},window);
 fetch(`/spend/logs?from=${encodeURIComponent(window.start)}&to=${encodeURIComponent(window.end)}`,{cache:'no-store'}).then(response=>{
  if(!response.ok)throw new Error(`Range logs could not be loaded (${response.status}).`);
  return response.json();
 }).then(payload=>{
  if(request!==spendLogsRequestSeq)return;
  if(!payload?.ok)throw new Error(payload?.error||'Range logs could not be loaded.');
  spendLogsPayload=payload;spendLogsRenderedRange=rangeKey;renderSpendLogs(payload,window);
 }).catch(error=>{
  if(request!==spendLogsRequestSeq)return;
  spendLogsRequestKey='';spendLogsRenderedRange=rangeKey;renderSpendLogs({error:error.message||String(error)},window);
 });
}
function hideSpendTip(){
 spendInspectDay='';$('s-chart-tip').hidden=true;
}
function showSpendTip(day,button=$('s-chart-inner').querySelector(`[data-spend-day="${day}"]`)){
 const row=spendChartRows.find(item=>item.day===day),tip=$('s-chart-tip');
 if(!row||!button){hideSpendTip();return;}
 spendInspectDay=day;
 const details=spendDayDetails(row),total=Number(row.cost||0),note=[details.note,spendBucketNote(row)].filter(Boolean).join(' · ');
 const span=row.granularity==='day'?'':`<div class=spendTipSpan>${esc(row.start===row.end?dayLabel(row.start):`${shortDayLabel(row.start)} – ${shortDayLabel(row.end)}`)}</div>`;
 const shownRuntimes=details.runtimes.filter(runtime=>runtime.cost>0||runtime.unavailable),runtimeRows=shownRuntimes.length?shownRuntimes.map(runtime=>`<div class=spendTipRuntime style="--spend-color:${SPEND_RUNTIME_COLORS[runtime.key]}"><span><i></i>${esc(runtime.label)}</span><b class=mono>${costValueHtml(money(runtime.cost),!(runtime.unavailable&&!runtime.cost))}</b><small>${runtime.unavailable?'unavailable':total?spendPct(runtime.cost/total):'0%'}${runtime.estimated?' · est':''}${runtime.partial?' · partial':''}</small></div>`).join(''):'<div class=spendTipEmpty>No runtime spend recorded.</div>';
 const html=`<div class=spendTipDate>${esc(spendBucketLabel(row))}</div>${span}<div class=spendTipTotal><span>${SPEND_GRANULARITY_COPY[row.granularity].total}</span><b class=mono>${esc(details.totalText)}</b></div>${note?`<div class=spendTipNote>${esc(note)}</div>`:''}<div class=spendTipRuntimes>${runtimeRows}</div>`;
 if(tip._spendHtml!==html){tip.innerHTML=html;tip._spendHtml=html;}
 tip.hidden=false;
 const card=tip.closest('.spendChartCard'),barRect=button.getBoundingClientRect(),cardRect=card.getBoundingClientRect();
 const left=Math.max(8,Math.min(cardRect.width-tip.offsetWidth-8,barRect.left-cardRect.left+barRect.width/2-tip.offsetWidth/2));
 tip.style.left=`${left}px`;tip.style.top=`${Math.max(8,Math.min(cardRect.height-tip.offsetHeight-8,Math.max(54,barRect.top-cardRect.top-tip.offsetHeight-8)))}px`;
}
function selectSpendDay(day,{focus=false}={}){
 if(!spendChartRows.some(row=>row.day===day))return;
 spendFocusDay=day;spendPaintNavigation();
 const button=$('s-chart-inner').querySelector(`[data-spend-day="${day}"]`);
 if(focus)button?.focus();
}
function renderSpend(xs){
 const allSource=xs?.spend?.days||xs?.daily||[],today=spendDayKey(new Date()),availableDays=allSource.map(row=>row.day).filter(day=>spendDayDate(day)).sort();
 const earliest=availableDays[0]||today;spendEarliestDay=earliest;
 const projectKey=spendProjects.length?`${spendProjects.join('\n')}|${xs?.generated_at||0}`:'';
 if(projectKey&&projectKey!==spendProjectDaysKey)loadSpendProjectDays(projectKey);
 const projectDaysReady=!spendProjects.length||Boolean(spendProjectDays&&spendProjectDays.key.split('|')[0]===projectKey.split('|')[0]),source=!spendProjects.length?allSource:projectDaysReady?spendProjectDays.days:[];
 paintSpendProjectOptions();
 let window=spendRangeWindow(spendRangeChoice,spendCustomFrom,spendCustomTo,new Date(),earliest);
 const savedCustomInvalid=spendRangeChoice==='custom'&&(!window.valid||window.start<earliest||window.end>today);
 if(savedCustomInvalid){spendRangeChoice='30';spendGranularityChoice='auto';window=spendRangeWindow('30');persistSpendRange();}
 spendCurrentSource=source;spendCurrentWindow=window;spendCurrentRows=spendCalendarRows(source,window);
 $('s-range').value=spendRangeChoice;
 $('s-custom').hidden=spendRangeChoice!=='custom';
 ['s-from','s-to'].forEach(id=>{$(id).min=earliest;$(id).max=today;});
 if(!$('s-range-error').textContent){$('s-from').value=spendCustomFrom;$('s-to').value=spendCustomTo;}
	 const rows=spendCurrentRows,total=rows.reduce((sum,row)=>sum+Number(row.cost||0),0),dailyAverage=window.dayCount?total/window.dayCount:0,evidence=spendEvidence(rows),runtimeTotals=spendRuntimeTotals(rows),topRuntime=runtimeTotals.reduce((best,row)=>!best||row.cost>best.cost?row:best,null),highest=rows.reduce((best,row)=>!best||Number(row.cost||0)>Number(best.cost||0)?row:best,null);
 const rangeText=spendRangeLabel(window),periodUnavailable=evidence.allUnavailable&&!total;
 const periodDiscount=source.filter(row=>row.day>=window.start&&row.day<=window.end).reduce((sum,row)=>sum+Number(row.discount_usd||0),0);
 setCostValue('s-total',money(total),!periodUnavailable);
 $('s-total-note').textContent=periodUnavailable?'Cost unavailable for recorded activity':`${rangeText}${spendProjects.length?` · ${spendProjectSummary()}`:''} · ${periodDiscount>0?`Estimate after discounts · list ${money(total+periodDiscount)}`:'API-equivalent estimate'}${evidence.partial?' · lower bound':''}`;
	 setCostValue('s-average',money(dailyAverage),!periodUnavailable);
 if(!projectDaysReady){$('s-total').textContent='--';$('s-average').textContent='--';$('s-total-note').textContent=`${rangeText} · Loading ${spendProjectSummary()}…`;}
 const previousWindow=spendPreviousWindow(window),previousRows=spendCalendarRows(source,previousWindow),previousEvidence=source.some(row=>row.day>=previousWindow.start&&row.day<=previousWindow.end),previousCoverage=spendEvidence(previousRows),previousTotal=previousRows.reduce((sum,row)=>sum+Number(row.cost||0),0);
 $('s-average-note').textContent=periodUnavailable?'Daily average unavailable':previousEvidence&&!evidence.partial&&!previousCoverage.partial?`${dailyDeltaLabel(total,previousTotal)} vs preceding ${window.dayCount}d`:`${window.dayCount} calendar ${countWord(window.dayCount,'day')}${evidence.partial||previousCoverage.partial?' · comparison withheld for partial coverage':''}`;
 $('s-top-runtime').textContent=periodUnavailable?'Unavailable':topRuntime&&topRuntime.cost?topRuntime.label:'No spend';
 $('s-top-runtime-note').textContent=topRuntime&&topRuntime.cost?`${money(topRuntime.cost)} · ${spendPct(total?topRuntime.cost/total:0)}${topRuntime.estimated?' · est':''}${topRuntime.partial?' · partial':''}`:evidence.active?'No covered runtime cost':'No recorded activity';
 $('s-highest-day').textContent=periodUnavailable?'Unavailable':highest&&highest.cost?shortDayLabel(highest.day):'No spend';
 $('s-highest-day-note').textContent=highest&&highest.cost?`${money(highest.cost)} · ${dayLabel(highest.day)}`:evidence.active?'No covered daily cost':'No recorded activity';
 const visibleRuntimes=runtimeTotals.filter(runtime=>runtime.cost||runtime.unavailable);
 $('s-legend').innerHTML=visibleRuntimes.map(runtime=>`<span style="--spend-color:${SPEND_RUNTIME_COLORS[runtime.key]}"><i></i>${esc(runtime.label)}</span>`).join('')+(visibleRuntimes.some(runtime=>runtime.estimated)?'<span class=spendEstimateKey><i></i>Local estimate</span>':'')+(evidence.partial?'<span class=spendPartialKey><i></i>Partial coverage</span>':'');
 const {granularity,options:granularityOptions}=spendResolveGranularity(spendGranularityChoice,rows,today);
 [...$('s-granularity').querySelectorAll('[data-spend-granularity]')].forEach(button=>{
  const value=button.dataset.spendGranularity,on=value===granularity;
  button.classList.toggle('on',on);button.setAttribute('aria-pressed',String(on));button.setAttribute('aria-disabled',String(!granularityOptions[value]));
  button.title=granularityOptions[value]?'':`Needs a range with at least two ${value}s, one of them complete`;
 });
 $('s-chart-note').textContent=SPEND_GRANULARITY_COPY[granularity].note;
 spendChartRows=spendBucketRows(rows,granularity,today);
 if(spendChartRows.some(row=>row.incomplete&&row.hasActivity))$('s-legend').insertAdjacentHTML('beforeend',`<span class=spendIncompleteKey><i></i>${granularity==='day'?'In progress':'Partial or in progress'}</span>`);
 reconcileSpendBars(spendChartRows);
	 $('s-chart').setAttribute('aria-label',`${rangeText}. ${periodUnavailable?'Spend unavailable':`${money(total)} estimated spend, daily average ${money(dailyAverage)}`}${evidence.partial?', partial coverage':''}. Use arrow keys to inspect ${granularity}s.`);
 $('s-platforms').innerHTML=visibleRuntimes.length?`<div class=spendPlatformRows>${visibleRuntimes.map(runtime=>`<div class=spendPlatformRow style="--spend-color:${SPEND_RUNTIME_COLORS[runtime.key]}"><div class=spendPlatformName><i></i><span>${esc(runtime.label)}</span></div><div class=spendPlatformTrack><i style="width:${total?100*runtime.cost/total:0}%"></i></div><div class="spendPlatformValue mono">${costValueHtml(money(runtime.cost),!(runtime.unavailable&&!runtime.cost))}<span>${runtime.unavailable?'unavailable':total?spendPct(runtime.cost/total):'0%'}${runtime.estimated?' · est':''}${runtime.partial?' · partial':''}</span></div></div>`).join('')}</div>`:`<div class="spendEmpty emptyState">${evidence.active?'Recorded activity has no runtime cost evidence.':'No spend recorded in this range.'}</div>`;
 loadSpendLogs(window,xs?.generated_at);
 const hovered=$('s-chart-inner').querySelector('.spendDay:hover');
 if(hovered)showSpendTip(hovered.dataset.spendDay,hovered);else hideSpendTip();
}
function validateSpendCustomDraft(){
 const from=$('s-from').value,to=$('s-to').value,window=spendRangeWindow('custom',from,to),earliest=spendEarliestDay||spendDayKey(new Date()),today=spendDayKey(new Date());
 let error=window.error;
 if(window.valid&&from<earliest)error=`From cannot be earlier than ${dayLabel(earliest)}.`;
 if(window.valid&&to>today)error='To cannot be later than today.';
 const valid=!error;
 $('s-range-error').textContent=error;['s-from','s-to'].forEach(id=>$(id).setAttribute('aria-invalid',String(!valid)));
 if(!valid)return;
 spendCustomFrom=from;spendCustomTo=to;spendRangeChoice='custom';spendGranularityChoice='auto';persistSpendRange();renderSpend(LATEST?.xsession);
}
$('s-range').onchange=event=>{
 const range=normalizeSpendRangeChoice(event.target.value);
 $('s-range-error').textContent='';['s-from','s-to'].forEach(id=>$(id).setAttribute('aria-invalid','false'));
 if(range==='custom'){
  const fallback=spendCurrentWindow?.valid?spendCurrentWindow:spendRangeWindow('30');
  if(!spendCustomFrom)spendCustomFrom=fallback.start;if(!spendCustomTo)spendCustomTo=fallback.end;
 }
 spendRangeChoice=range;spendGranularityChoice='auto';persistSpendRange();if(LATEST)renderSpend(LATEST.xsession);
};
function setSpendProjects(projects){
 spendProjects=[...new Set(projects)].slice(0,SPEND_PROJECT_LIMIT);persistSpendProjects();
 spendProjectDays=null;spendProjectDaysKey='';spendProjectDaysSeq++;hideSpendTip();
 if(LATEST)renderSpend(LATEST.xsession);else paintSpendProjectOptions();
 if(spendLogsPayload?.ok&&spendCurrentWindow&&spendLogsPayload.from===spendCurrentWindow.start&&spendLogsPayload.to===spendCurrentWindow.end)renderSpendLogs(spendLogsPayload,spendCurrentWindow);
}
$('s-project-options').addEventListener('change',event=>{
 const input=event.target.closest('[data-spend-project]');if(!input)return;
 const project=input.dataset.spendProject;
 setSpendProjects(project==='all'?[]:input.checked?[...spendProjects,project]:spendProjects.filter(value=>value!==project));
});
$('s-project-search').addEventListener('input',event=>{spendProjectQuery=event.target.value;paintSpendProjectOptions();});
$('s-from').onchange=validateSpendCustomDraft;$('s-to').onchange=validateSpendCustomDraft;
$('s-granularity').onclick=event=>{
 const button=event.target.closest('[data-spend-granularity]');if(!button||button.getAttribute('aria-disabled')==='true')return;
 spendGranularityChoice=normalizeSpendGranularity(button.dataset.spendGranularity);
 hideSpendTip();if(LATEST)renderSpend(LATEST.xsession);
};
function handleSpendInspect(event){
 const button=event.target.closest('[data-spend-day]');if(!button)return;
 if(spendInspectDay===button.dataset.spendDay&&!$('s-chart-tip').hidden)return;
 showSpendTip(button.dataset.spendDay,button);
}
$('s-chart-inner').addEventListener('pointerover',handleSpendInspect);
$('s-chart-inner').addEventListener('pointerleave',hideSpendTip);
$('s-chart-inner').onclick=event=>{const button=event.target.closest('[data-spend-day]');if(button)selectSpendDay(button.dataset.spendDay,{focus:true});};
$('s-logs').onclick=event=>{const button=event.target.closest('[data-spend-session]');if(button)selectSession(button.dataset.spendSession);};
$('s-scatter-metric').onclick=event=>{const button=event.target.closest('[data-scatter-metric]');if(!button)return;spendScatterMetric=button.dataset.scatterMetric==='input'?'input':'cost';localStorage.setItem(SPEND_SCATTER_METRIC_KEY,spendScatterMetric);drawSpendScatter(spendInsightPayload?.sessions||[]);};
function openSpendInsightSession(event){const button=event.target.closest('[data-spend-insight-session]');if(!button)return;event.preventDefault();selectSession(button.dataset.spendInsightSession);}
$('s-concentration-sessions').onclick=openSpendInsightSession;$('s-scatter-svg').onclick=openSpendInsightSession;
$('s-chart-inner').onkeydown=event=>{
 if(!['ArrowLeft','ArrowRight','Home','End','Escape'].includes(event.key))return;
 if(event.key==='Escape'){hideSpendTip();return;}
 event.preventDefault();const buttons=[...$('s-chart-inner').querySelectorAll('[data-spend-day]')],current=event.target.closest('[data-spend-day]'),index=Math.max(0,buttons.indexOf(current));
 const next=event.key==='Home'?0:event.key==='End'?buttons.length-1:Math.max(0,Math.min(buttons.length-1,index+(event.key==='ArrowRight'?1:-1)));
 if(buttons[next])selectSpendDay(buttons[next].dataset.spendDay,{focus:true});
};
