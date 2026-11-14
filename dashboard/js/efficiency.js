
const MODEL_COUNTER_KEYS=['input_tokens','output_tokens','io_covered_input_tokens','io_covered_output_tokens','cost_covered_output_tokens','cost_covered_cost','executions','token_covered_executions','io_covered_executions','cost_covered_executions','cost','cache_read_tokens','cache_write_tokens','cache_covered_input_tokens','reasoning_tokens','reasoning_output_tokens','reasoning_executions','reasoning_unavailable_executions','thinking_executions','thinking_covered_executions','attempts','retries','failed_attempts','attempt_samples','timed_output_tokens','timed_seconds','timed_samples','tool_free_output_tokens','tool_free_seconds','tool_free_samples','ttft_total_s','ttft_samples','wait_seconds','wait_samples','user_pause_seconds'];
const MODEL_ARRAY_KEYS=['wait_durations_s','workload_peak_inputs','workload_outputs','workload_tool_calls','workload_model_calls','workload_cache_ratios'];
const EFFICIENCY_RANGES=['today','yesterday','7','30','90','month','last_month','all'];
function loadEfficiencyFilters(project=''){
 if(project){
  const stored=localStorage.getItem('tm_efficiency_project_filters');
  try{const scoped=JSON.parse(stored||'{}'),values=scoped?.[project];return Array.isArray(values)?[...new Set(values.filter(value=>typeof value==='string'&&value))]:[];}catch(error){return [];}
 }
 try{const stored=JSON.parse(localStorage.getItem('tm_efficiency_filters')||'[]');return Array.isArray(stored)?[...new Set(stored.filter(value=>typeof value==='string'&&value))]:[];}catch(error){return [];}
}
function saveActiveEfficiencyFilters(){
 if(!efficiencyProject)localStorage.setItem('tm_efficiency_filters',JSON.stringify(efficiencyFilters));
 else{
  let scoped={};try{const stored=JSON.parse(localStorage.getItem('tm_efficiency_project_filters')||'{}');if(stored&&typeof stored==='object'&&!Array.isArray(stored))scoped=stored;}catch(error){}
  scoped[efficiencyProject]=efficiencyFilters;localStorage.setItem('tm_efficiency_project_filters',JSON.stringify(scoped));
 }
}
let efficiencyProject=migrateStoredProjectFilters('tm_efficiency_project');
let efficiencyFilters=loadEfficiencyFilters(efficiencyProject);
let efficiencyRange=localStorage.getItem('tm_efficiency_range')||'30';
if(!EFFICIENCY_RANGES.includes(efficiencyRange))efficiencyRange='30';
let efficiencySort={key:'cost',direction:'desc'};
let activeEfficiencyStats=null,activeEfficiencyScope='',activeEfficiencyGeneration=0;
let efficiencyProjectRequest=0,efficiencyProjectLoadingKey='';
const efficiencyProjectCache=new Map();
// model-color-logic-start
const MODEL_COLORS={
 codex:['#6F9CFF','#8EC5FF','#4C86F0','#B6CFFF','#647DB4'],
 claude:['#E3825C','#F4A26E','#B55332','#FBC6A6','#CF8B76'],
 'claude-3p':['#A46AF4','#AD88F0','#7459F0','#B799E5','#9A35F6'],
 cursor:['#3CC6C0','#65D6A6','#A0D56E','#2E9C8E','#BEE0D4'],
 kiro:['#E3CC5C','#C9A43A','#EFE0A0','#B8B850','#CCE236'],
 opencode:['#FF8FB8','#FF5E93','#FFC2D8','#E86FA0','#E236A9'],
 fallback:['#A9B8C7','#8FB1C5','#B3A8D8','#83C8BD','#7D8AA0'],
};
const MODEL_RUNTIME_COLOR_FAMILIES={
 codex:'codex','claude code':'claude','claude-3p':'claude-3p',cursor:'cursor',kiro:'kiro',opencode:'opencode',
};
const MODEL_OUTPUT_BAR_FILL='#536879',MODEL_OUTPUT_BAR_STROKE='#7890A3';
function modelRuntimeColorFamily(identity){
 const parts=String(identity||'').split('::'),runtime=(parts.length>1?parts[1]:parts[0]).trim().toLowerCase();
 return MODEL_RUNTIME_COLOR_FAMILIES[runtime]||'fallback';
}
function modelColor(name,names){
 const family=modelRuntimeColorFamily(name),palette=MODEL_COLORS[family]||MODEL_COLORS.fallback;
 const siblings=[...new Set((names||[]).filter(identity=>modelRuntimeColorFamily(identity)===family))].sort((a,b)=>a.localeCompare(b));
 return palette[Math.max(0,siblings.indexOf(name))%palette.length];
}
// Ranked groups (top five) take shades in rank order per runtime, so the visible top five never share a color.
function modelRankedColors(groups){
 const used=new Map();
 return new Map(groups.map(group=>{const family=modelRuntimeColorFamily(group.ids?.[0]||group.key),palette=MODEL_COLORS[family]||MODEL_COLORS.fallback,index=used.get(family)||0;used.set(family,index+1);return [group.key,palette[index%palette.length]];}));
}
function modelTrendPaint(seriesColor){return {barFill:MODEL_OUTPUT_BAR_FILL,barStroke:MODEL_OUTPUT_BAR_STROKE,line:seriesColor};}
// model-color-logic-end
function localDateKey(date){return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;}
function dateKeyAgo(days,now=new Date()){const date=new Date(now);date.setHours(12,0,0,0);date.setDate(date.getDate()-days);return localDateKey(date);}
function calendarMonthWindow(monthsAgo,now=new Date()){
 const start=new Date(now.getFullYear(),now.getMonth()-monthsAgo,1,12,0,0,0),days=[];
 for(const date=new Date(start);date.getMonth()===start.getMonth();date.setDate(date.getDate()+1))days.push(localDateKey(date));
 return {days,daySet:new Set(days)};
}
function monthToDateWindow(now=new Date()){
 const days=Array.from({length:now.getDate()},(_,index)=>dateKeyAgo(now.getDate()-1-index,now));
 return {days,daySet:new Set(days)};
}
function modelRangeWindow(range,now=new Date()){
 if(range==='all')return {days:null,daySet:null};
 if(range==='month')return monthToDateWindow(now);
 if(range==='last_month')return calendarMonthWindow(1,now);
 if(range==='today'||range==='yesterday'){
  const day=dateKeyAgo(range==='today'?0:1,now);
  return {days:[day],daySet:new Set([day])};
 }
 const count=Number(range),days=Array.from({length:count},(_,index)=>dateKeyAgo(count-1-index,now));
 return {days,daySet:new Set(days)};
}
function modelDayInRange(day,window){return !window.daySet||window.daySet.has(day);}
function modelRangeLabel(range){return range==='today'?'Today':range==='yesterday'?'Yesterday':range==='month'?'Month':range==='last_month'?'Last month':range==='all'?'all history':`${range}d`;}
function modelWaitDistribution(values){
 const sorted=(values||[]).map(Number).filter(value=>value>0).sort((a,b)=>a-b),count=sorted.length;
 if(!count)return {median_wait_s:0,p95_wait_s:0};
 const middle=Math.floor(count/2),median=count%2?sorted[middle]:(sorted[middle-1]+sorted[middle])/2;
 return {median_wait_s:median,p95_wait_s:sorted[Math.min(count-1,Math.max(0,Math.ceil(count*.95)-1))]};
}
function modelMedian(values){
 const sorted=(values||[]).map(Number).filter(value=>Number.isFinite(value)&&value>=0).sort((a,b)=>a-b),count=sorted.length;
 if(!count)return 0;const middle=Math.floor(count/2);return count%2?sorted[middle]:(sorted[middle-1]+sorted[middle])/2;
}
function aggregateModelDays(rows){
	 const total=MODEL_COUNTER_KEYS.reduce((out,key)=>(out[key]=0,out),{});
	 rows=rows||[];const tokenCovered=rows.filter(row=>metricAvailable(row,'tokens')).length,inputCovered=rows.filter(row=>metricAvailable(row,'input_tokens')).length,outputCovered=rows.filter(row=>metricAvailable(row,'output_tokens')).length,costCovered=rows.filter(row=>metricAvailable(row,'cost')).length,cacheCovered=rows.filter(row=>metricAvailable(row,'cache')).length,reasoningCovered=rows.filter(row=>row.availability?.reasoning_tokens===true).length,attemptCovered=rows.filter(row=>row.availability?.attempts===true).length,tokenPartial=rows.some(row=>metricPartial(row,'tokens')),costPartial=rows.some(row=>metricPartial(row,'cost')||(row.cost_covered_executions!==undefined&&Number(row.cost_covered_executions||0)<Number(row.executions||0))),cachePartial=rows.some(row=>metricPartial(row,'cache')),reasoningPartial=rows.some(row=>row.coverage?.reasoning_tokens?.complete===false),attemptPartial=rows.some(row=>row.coverage?.attempts?.complete===false);
	 const estimated=rows.some(hasLocalEstimate),reported=rows.some(row=>['reported','mixed'].includes(usageBasis(row)));total.usage_basis=estimated?(reported?'mixed':'local_estimate'):'reported';total.provenance={usage_basis:total.usage_basis};
	 total.availability={tokens:tokenCovered>0,input_tokens:inputCovered>0,output_tokens:outputCovered>0,cost:costCovered>0,cache:cacheCovered>0,reasoning_tokens:reasoningCovered>0,attempts:attemptCovered>0,throughput:outputCovered>0};
	 total.coverage={tokens:{complete:tokenCovered===rows.length&&!tokenPartial,covered_rows:tokenCovered,total_rows:rows.length},cost:{complete:costCovered===rows.length&&!costPartial,covered_rows:costCovered,total_rows:rows.length},cache:{complete:cacheCovered===rows.length&&!cachePartial,covered_rows:cacheCovered,total_rows:rows.length},reasoning_tokens:{complete:reasoningCovered===rows.length&&!reasoningPartial,covered_rows:reasoningCovered,total_rows:rows.length},attempts:{complete:attemptCovered===rows.length&&!attemptPartial,covered_rows:attemptCovered,total_rows:rows.length}};
	 rows.forEach(row=>MODEL_COUNTER_KEYS.forEach(key=>total[key]+=Number(row[key]||0)));
	 total.reasoning_efforts=[...new Set(rows.flatMap(row=>row.reasoning_efforts||[]))].sort((a,b)=>String(a).localeCompare(String(b))).slice(0,8);
	 MODEL_ARRAY_KEYS.forEach(key=>total[key]=rows.flatMap(row=>(row[key]||[]).map(Number).filter(value=>Number.isFinite(value)&&value>=0)));
	 total.max_wait_s=Math.max(0,...rows.map(row=>Number(row.max_wait_s||0)));
 const toolFree=total.tool_free_samples>0&&total.tool_free_seconds>0;
 const timed=total.timed_samples>0&&total.timed_seconds>0;
 const seconds=toolFree?total.tool_free_seconds:total.timed_seconds;
 const speedOutput=toolFree?total.tool_free_output_tokens:total.timed_output_tokens;
 total.available=Boolean((toolFree||timed)&&seconds>0&&speedOutput>0);
 total.output_tps=total.available?speedOutput/seconds:0;
 total.throughput_basis=toolFree?'tool_free':timed?'end_to_end':'unavailable';
 total.throughput_samples=toolFree?total.tool_free_samples:total.timed_samples;
 total.timing_coverage=total.output_tokens?Math.min(1,speedOutput/total.output_tokens):0;
	 total.avg_ttft_ms=total.ttft_samples?total.ttft_total_s*1000/total.ttft_samples:0;
	 total.avg_wait_s=total.wait_samples?total.wait_seconds/total.wait_samples:0;
	 Object.assign(total,modelWaitDistribution(total.wait_durations_s));
	 total.median_peak_input_tokens=modelMedian(total.workload_peak_inputs);
	 total.median_workload_output_tokens=modelMedian(total.workload_outputs);
	 total.median_tool_calls=modelMedian(total.workload_tool_calls);
	 total.median_model_calls=modelMedian(total.workload_model_calls);
	 total.median_cache_ratio=modelMedian(total.workload_cache_ratios);
 return total;
}
function mergeModelDays(models,window){
 const days=new Map();
 (models||[]).forEach(model=>(model.daily||[]).forEach(day=>{
  if(!modelDayInRange(day.day,window))return;
	  const target=days.get(day.day)||{day:day.day};
	  MODEL_COUNTER_KEYS.forEach(key=>target[key]=Number(target[key]||0)+Number(day[key]||0));
	  MODEL_ARRAY_KEYS.forEach(key=>target[key]=[...(target[key]||[]),...(day[key]||[])]);
	  target._metricRows=Number(target._metricRows||0)+1;target._tokenCovered=Number(target._tokenCovered||0)+(metricAvailable(day,'tokens')?1:0);target._inputCovered=Number(target._inputCovered||0)+(metricAvailable(day,'input_tokens')?1:0);target._outputCovered=Number(target._outputCovered||0)+(metricAvailable(day,'output_tokens')?1:0);target._costCovered=Number(target._costCovered||0)+(metricAvailable(day,'cost')?1:0);target._cacheCovered=Number(target._cacheCovered||0)+(metricAvailable(day,'cache')?1:0);target._reasoningCovered=Number(target._reasoningCovered||0)+(day.availability?.reasoning_tokens===true?1:0);target._attemptCovered=Number(target._attemptCovered||0)+(day.availability?.attempts===true?1:0);target._tokenComplete=target._tokenComplete!==false&&day.coverage?.tokens?.complete!==false;target._costComplete=target._costComplete!==false&&day.coverage?.cost?.complete!==false;target._cacheComplete=target._cacheComplete!==false&&day.coverage?.cache?.complete!==false;target._reasoningComplete=target._reasoningComplete!==false&&day.coverage?.reasoning_tokens?.complete!==false;target._attemptComplete=target._attemptComplete!==false&&day.coverage?.attempts?.complete!==false;
	  target._estimated=Boolean(target._estimated||hasLocalEstimate(day));target._reported=Boolean(target._reported||['reported','mixed'].includes(usageBasis(day)));target.usage_basis=target._estimated?(target._reported?'mixed':'local_estimate'):'reported';target.provenance={usage_basis:target.usage_basis};
	  target.availability={tokens:target._tokenCovered>0,input_tokens:target._inputCovered>0,output_tokens:target._outputCovered>0,cost:target._costCovered>0,cache:target._cacheCovered>0,reasoning_tokens:target._reasoningCovered>0,attempts:target._attemptCovered>0,throughput:target._outputCovered>0};target.coverage={tokens:{complete:target._tokenCovered===target._metricRows&&target._tokenComplete},cost:{complete:target._costCovered===target._metricRows&&target._costComplete},cache:{complete:target._cacheCovered===target._metricRows&&target._cacheComplete},reasoning_tokens:{complete:target._reasoningCovered===target._metricRows&&target._reasoningComplete},attempts:{complete:target._attemptCovered===target._metricRows&&target._attemptComplete}};
	  target.max_wait_s=Math.max(Number(target.max_wait_s||0),Number(day.max_wait_s||0));
  days.set(day.day,target);
 }));
 return [...days.values()].sort((a,b)=>a.day.localeCompare(b.day));
}
function efficiencyMetrics(row){
 const data=row||{},availability=data.availability||{},input=Math.max(0,Number(data.input_tokens)||0),output=Math.max(0,Number(data.output_tokens)||0),coveredOutput=data.cost_covered_output_tokens===undefined?output:Math.max(0,Number(data.cost_covered_output_tokens)||0),cost=Math.max(0,Number(data.cost)||0),coveredCost=data.cost_covered_cost===undefined?cost:Math.max(0,Number(data.cost_covered_cost)||0),executions=Math.max(0,Number(data.executions)||0),coveredExecutions=data.token_covered_executions===undefined?executions:Math.max(0,Number(data.token_covered_executions)||0),ioCoveredExecutions=data.io_covered_executions===undefined?coveredExecutions:Math.max(0,Number(data.io_covered_executions)||0),costCoveredExecutions=data.cost_covered_executions===undefined?executions:Math.max(0,Number(data.cost_covered_executions)||0),reasoning=Math.max(0,Number(data.reasoning_tokens)||0),reasoningOutput=Math.max(0,Number(data.reasoning_output_tokens)||0),cacheRead=Math.max(0,Number(data.cache_read_tokens)||0),cacheInput=data.cache_covered_input_tokens===undefined?input:Math.max(0,Number(data.cache_covered_input_tokens)||0),attempts=Math.max(0,Number(data.attempts)||0),retries=Math.max(0,Number(data.retries)||0),failed=Math.max(0,Number(data.failed_attempts)||0),tokensAvailable=availability.tokens===true,inputAvailable=availability.input_tokens===true||(availability.input_tokens===undefined&&tokensAvailable),outputAvailable=availability.output_tokens===true||(availability.output_tokens===undefined&&tokensAvailable),reasoningAvailable=availability.reasoning_tokens===true,cacheAvailable=availability.cache===true,attemptsAvailable=availability.attempts===true,ioComplete=ioCoveredExecutions>=executions&&data.coverage?.tokens?.complete!==false,pairedTotals=data.io_covered_input_tokens!==undefined&&data.io_covered_output_tokens!==undefined,pairedInput=Math.max(0,Number(data.io_covered_input_tokens)||0),pairedOutput=Math.max(0,Number(data.io_covered_output_tokens)||0);
 return {output_per_dollar:outputAvailable&&availability.cost===true&&costCoveredExecutions>0&&coveredCost>0?coveredOutput/coveredCost:null,reasoning_ratio:reasoningAvailable&&reasoningOutput>0?Math.min(1,reasoning/reasoningOutput):null,context_load:inputAvailable&&outputAvailable?(pairedTotals?(ioCoveredExecutions>0&&pairedOutput>0?pairedInput/pairedOutput:null):(ioComplete&&output>0?input/output:null)):null,cache_hit_ratio:tokensAvailable&&cacheAvailable&&cacheInput>0?Math.min(1,cacheRead/cacheInput):null,retry_waste:attemptsAvailable&&attempts>0?Math.min(1,retries/attempts):null,failure_rate:attemptsAvailable&&attempts>0?Math.min(1,failed/attempts):null};
}
function thinkingUnavailableExecutions(row){
 const data=row||{},observed=Math.max(0,Number(data.thinking_executions)||0),covered=data.thinking_covered_executions===undefined?Math.min(observed,Math.max(0,Number(data.reasoning_executions)||0)):Math.max(0,Number(data.thinking_covered_executions)||0);
 return Math.max(0,observed-covered);
}
function efficiencyRangeWindow(){return modelRangeWindow(efficiencyRange);}
function filterEfficiencyModels(models,filters){
 const allSelected=!(filters||[]).length;
 return (models||[]).filter(model=>allSelected||(filters||[]).includes(model.id));
}
function efficiencyModelRows(models,window){
 return (models||[]).map(row=>{const daily=window.daySet?(row.daily||[]).filter(day=>modelDayInRange(day.day,window)):[row];return {...row,window:aggregateModelDays(daily)};}).filter(row=>Number(row.window.executions||0)>0||Number(row.window.input_tokens||0)>0||Number(row.window.output_tokens||0)>0||Number(row.window.cost||0)>0).sort((a,b)=>Number(b.window.cost||0)-Number(a.window.cost||0)||Number(b.window.output_tokens||0)-Number(a.window.output_tokens||0)||modelRuntimeLabel(a).localeCompare(modelRuntimeLabel(b)));
}
function efficiencySortValue(row,key){
 const data=row?.window||{},metric=efficiencyMetrics(data);
 if(key==='model')return modelRuntimeLabel(row);
 if(key==='cost')return data.availability?.cost===true?Number(data.cost)||0:null;
 const value=metric[key];return value===null||!Number.isFinite(value)?null:value;
}
function sortEfficiencyRows(rows,sort=efficiencySort){
 const direction=sort.direction==='asc'?1:-1;
 return [...(rows||[])].sort((a,b)=>{const left=efficiencySortValue(a,sort.key),right=efficiencySortValue(b,sort.key);if(left===null&&right===null)return modelRuntimeLabel(a).localeCompare(modelRuntimeLabel(b));if(left===null)return 1;if(right===null)return -1;const compared=typeof left==='string'?left.localeCompare(right):left-right;return compared?compared*direction:modelRuntimeLabel(a).localeCompare(modelRuntimeLabel(b));});
}
function efficiencyTrendDays(rows,window){
 const grouped=new Map((window.days||[]).map(day=>[day,[]]));
 (rows||[]).forEach(row=>(row.daily||[]).forEach(day=>{if(!modelDayInRange(day.day,window))return;if(!grouped.has(day.day))grouped.set(day.day,[]);grouped.get(day.day).push(day);}));
 return [...grouped].sort(([a],[b])=>a.localeCompare(b)).map(([day,items])=>({day,metrics:efficiencyMetrics(aggregateModelDays(items))}));
}
function efficiencyTrendDelta(trend,key){
 const values=(trend||[]).map(row=>row?.metrics?.[key]).filter(value=>value!==null&&value!==undefined&&Number.isFinite(Number(value))).map(Number);
 if(values.length<2||values.at(-2)<=0)return {text:'No comparable prior day',direction:''};
 const change=(values.at(-1)-values.at(-2))/values.at(-2);
 if(!Number.isFinite(change))return {text:'No comparable prior day',direction:''};
 const direction=change>0?'up':change<0?'down':'flat',symbol=direction==='up'?'↑':direction==='down'?'↓':'→';
 return {text:`${symbol} ${Math.abs(change*100).toFixed(1)}% vs prior covered day`,direction};
}
function efficiencyComparisonWindows(range,now=new Date()){
 if(range==='today')return {current:modelRangeWindow('today',now),prior:modelRangeWindow('yesterday',now),label:'yesterday'};
 if(range==='yesterday')return {current:modelRangeWindow('yesterday',now),prior:{days:[dateKeyAgo(2,now)],daySet:new Set([dateKeyAgo(2,now)])},label:'the day before'};
 if(range==='month')return {current:monthToDateWindow(now),prior:calendarMonthWindow(1,now),label:'last month'};
 if(range==='last_month')return {current:calendarMonthWindow(1,now),prior:calendarMonthWindow(2,now),label:'the month before'};
 const count=Number(range);
 if(!Number.isInteger(count)||count<=0)return null;
 const priorDays=Array.from({length:count},(_,index)=>dateKeyAgo(count*2-1-index,now));
 return {current:modelRangeWindow(String(count),now),prior:{days:priorDays,daySet:new Set(priorDays)},label:`prior ${count} days`};
}
function efficiencyPeriodDelta(current,prior,key,label){
 if(current?.[key]==null||prior?.[key]==null)return {text:'No comparable prior period',direction:''};
 const latest=Number(current[key]),baseline=Number(prior[key]);
 if(!Number.isFinite(latest)||!Number.isFinite(baseline)||latest<0||baseline<=0)return {text:'No comparable prior period',direction:''};
 const change=(latest-baseline)/baseline;
 if(!Number.isFinite(change))return {text:'No comparable prior period',direction:''};
 const direction=change>0?'up':change<0?'down':'flat',symbol=direction==='up'?'↑':direction==='down'?'↓':'→';
 return {text:`${symbol} ${Math.abs(change*100).toFixed(1)}% vs ${label}`,direction};
}
// Shared rise/fall readout under a number: <div class=trendDelta><strong>↑ 8.5%</strong><span>vs prior 30 days</span></div>
function percentTrendDelta(percent,label){
 const value=Number(percent);
 if(percent===null||percent===undefined||percent===''||!Number.isFinite(value))return {text:'No comparable prior period',direction:''};
 const direction=Math.abs(value)<.05?'flat':value>0?'up':'down',symbol=direction==='up'?'↑':direction==='down'?'↓':'→';
 return {text:`${symbol} ${Math.abs(value).toFixed(1)}% vs ${label}`,direction};
}
function trendTone(delta,improvingDirection=''){
 const direction=delta?.direction||'';
 return direction&&improvingDirection?(direction===improvingDirection?'improving':'degrading'):'';
}
function renderTrendDelta(target,delta,improvingDirection='',hidden=false){
 const change=typeof target==='string'?$(target):target,parts=delta.direction?delta.text.split(' vs '):[delta.text,''];
 change.hidden=hidden;change.querySelector('strong').textContent=parts[0];change.querySelector('span').textContent=parts[1]?`vs ${parts[1]}`:'';change.dataset.direction=delta.direction;change.dataset.tone=trendTone(delta,improvingDirection);
}
function efficiencyChartPointerIndex(clientX,rect,count){
 if(count<=1)return 0;
 const rectWidth=Math.max(1,Number(rect?.width)||0),localX=Math.max(0,Math.min(rectWidth,Number(clientX)-(Number(rect?.left)||0))),viewX=localX/rectWidth*520;
 return Math.max(0,Math.min(count-1,Math.round((viewX-9)/502*(count-1))));
}
function efficiencyChartKeyIndex(key,index,count){
 const last=Math.max(0,count-1),current=Math.max(0,Math.min(last,index));
 if(key==='Home')return 0;
 if(key==='End')return last;
 if(key==='ArrowLeft')return Math.max(0,current-1);
 if(key==='ArrowRight')return Math.min(last,current+1);
 return current;
}
function efficiencyChartMetricPresentation(value,key){
 if(key==='reasoning_ratio')return {axis:pct(value),detail:`${pct(value)} reasoning`};
 if(key==='context_load')return {axis:efficiencyDecimal(value),detail:`${efficiencyDecimal(value)} context / output`};
 if(key==='cache_hit_ratio')return {axis:pct(value),detail:`${pct(value)} input from cache`};
 return {axis:compactNumber(value),detail:`${Math.round(value).toLocaleString('en-US')} output tokens / $`};
}
function efficiencyChartInspection(sample,key){
 const parts=String(sample?.day||'').split('-').map(Number),months=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'],date=parts.length===3&&months[parts[1]-1]?`${months[parts[1]-1]} ${parts[2]}`:String(sample?.day||'Unknown day'),value=sample?.value;
 if(value===null||!Number.isFinite(value))return {date,value:'No data'};
 return {date,value:efficiencyChartMetricPresentation(value,key).detail};
}
function efficiencyChartAnnouncement(detail,announce){
 return announce?`${detail.date} · ${detail.value}`:'';
}
function chartGapRuns(points){
 const segments=[];let segment=[];
 (points||[]).forEach(point=>{if(segment.length&&point.index!==segment.at(-1).index+1){segments.push(segment);segment=[];}segment.push(point);});
 if(segment.length)segments.push(segment);
 return {segments,bridges:segments.slice(1).map((run,index)=>[segments[index].at(-1),run[0]])};
}
function chartGapBridge(x1,y1,x2,y2,color=''){return `<line class=chartGapBridge x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}"${color?` style="stroke:${color};stroke-opacity:.55"`:''}/>`;}
function efficiencyChartSmoothPath(rows,x,y){
 const points=(rows||[]).map(row=>({x:Number(x(row)),y:Number(y(row.value))})),format=value=>value.toFixed(1);
 if(!points.length)return '';
 if(points.length===1)return `M${format(points[0].x)} ${format(points[0].y)}`;
 if(points.length===2)return `M${format(points[0].x)} ${format(points[0].y)} L${format(points[1].x)} ${format(points[1].y)}`;
 const intervals=points.slice(1).map((point,index)=>point.x-points[index].x),slopes=points.slice(1).map((point,index)=>(point.y-points[index].y)/intervals[index]),tangents=points.map((point,index)=>index===0?slopes[0]:index===points.length-1?slopes.at(-1):0);
 for(let index=1;index<points.length-1;index++){const previous=slopes[index-1],next=slopes[index],previousWidth=intervals[index-1],nextWidth=intervals[index];if(previous===0||next===0||previous*next<=0)tangents[index]=0;else{const left=2*nextWidth+previousWidth,right=nextWidth+2*previousWidth;tangents[index]=(left+right)/(left/previous+right/next);}}
 let path=`M${format(points[0].x)} ${format(points[0].y)}`;
 for(let index=0;index<points.length-1;index++){const start=points[index],end=points[index+1],width=intervals[index];path+=` C${format(start.x+width/3)} ${format(start.y+tangents[index]*width/3)} ${format(end.x-width/3)} ${format(end.y-tangents[index+1]*width/3)} ${format(end.x)} ${format(end.y)}`;}
 return path;
}
function renderEfficiencyChart(id,trend,key,label){
 const svg=$(id),tip=svg.parentElement?.querySelector('.efficiencyChartTip'),status=svg.parentElement?.querySelector('.efficiencyChartStatus'),hoverRatio=Number(svg.dataset.efficiencyHoverRatio),width=Math.max(120,Math.round(svg.clientWidth||520)),height=Math.max(40,Math.round(svg.clientHeight||96)),pad={left:9,right:9,top:10,bottom:16},samples=(trend||[]).map((row,index)=>({day:row.day,index,value:row.metrics?.[key]})),valid=samples.filter(row=>row.value!==null&&Number.isFinite(row.value));
 if(tip)tip.hidden=true;
 if(status)status.textContent='';
 if(!samples.length){delete svg.dataset.efficiencyHoverRatio;svg.setAttribute('viewBox',`0 0 ${width} ${height}`);svg.innerHTML=`<text class=efficiencyChartEmpty x=10 y="${Math.round(height/2+4)}">No daily evidence</text>`;svg.setAttribute('aria-label',`${label}: unavailable`);svg.onpointermove=null;svg.onpointerleave=null;svg.onfocus=null;svg.onkeydown=null;svg.onblur=null;return;}
 const maxValue=key==='reasoning_ratio'||key==='cache_hit_ratio'?1:valid.length?Math.max(1,...valid.map(row=>row.value)):1,plotWidth=width-pad.left-pad.right,plotHeight=height-pad.top-pad.bottom,x=row=>pad.left+(samples.length<=1?plotWidth/2:row.index*plotWidth/(samples.length-1)),y=value=>pad.top+plotHeight-(Math.max(0,value)/maxValue)*plotHeight;
 const last=valid.at(-1),{segments,bridges}=chartGapRuns(valid);
 const paths=bridges.map(([from,to])=>chartGapBridge(x(from),y(from.value),x(to),y(to.value))).join('')+segments.map(rows=>{if(rows.length===1)return rows[0]===last?'':`<circle class=efficiencyChartDot cx="${x(rows[0]).toFixed(1)}" cy="${y(rows[0].value).toFixed(1)}" r="2.5"/>`;const line=efficiencyChartSmoothPath(rows,x,y),area=`${line} L${x(rows.at(-1)).toFixed(1)} ${(pad.top+plotHeight).toFixed(1)} L${x(rows[0]).toFixed(1)} ${(pad.top+plotHeight).toFixed(1)} Z`;return `<path class=efficiencyChartArea d="${area}"/><path class=efficiencyChartLine d="${line}"/>`;}).join('');
 const format=value=>efficiencyChartMetricPresentation(value,key).axis,firstDay=samples[0]?.day?.slice(5)||'',lastDay=samples.at(-1)?.day?.slice(5)||'',latest=last?`<circle class=efficiencyChartDot cx="${x(last).toFixed(1)}" cy="${y(last.value).toFixed(1)}" r="3.5"/>`:'',empty=valid.length?'':`<text class=efficiencyChartEmpty x=10 y="${Math.round(height/2+4)}">No daily evidence</text>`,maxLabel=valid.length?`<text class=efficiencyChartLabel x="${width-pad.right}" y="${pad.top+8}" text-anchor=end>${esc(format(maxValue))}</text>`:'';
 svg.setAttribute('viewBox',`0 0 ${width} ${height}`);
 svg.innerHTML=`<line class=efficiencyChartGrid x1="${pad.left}" x2="${width-pad.right}" y1="${pad.top}" y2="${pad.top}"/><line class=efficiencyChartGrid x1="${pad.left}" x2="${width-pad.right}" y1="${pad.top+plotHeight}" y2="${pad.top+plotHeight}"/>${paths}${latest}${empty}<text class=efficiencyChartLabel x="${pad.left}" y="${height-2}">${esc(firstDay)}</text><text class=efficiencyChartLabel x="${width-pad.right}" y="${height-2}" text-anchor=end>${esc(lastDay)}</text>${maxLabel}<g class=efficiencyChartInspect visibility=hidden><line class=efficiencyChartInspectLine y1="${pad.top}" y2="${pad.top+plotHeight}"/><circle class=efficiencyChartInspectDot r="4"/></g>`;
 const instruction='Use Left and Right arrow keys to inspect daily values.',summaryLabel=`${last?`${label}: ${valid.length} daily values, latest ${format(last.value)}`:`${label}: unavailable`}. ${instruction}`;
 svg.setAttribute('aria-label',summaryLabel);
 let activeIndex=samples.length-1;
 const inspect=svg.querySelector('.efficiencyChartInspect'),line=inspect?.querySelector('line'),dot=inspect?.querySelector('circle'),hideInspection=()=>{inspect?.setAttribute('visibility','hidden');if(tip)tip.hidden=true;};
 const showInspection=(index,localX,announce=false)=>{const sample=samples[index],detail=efficiencyChartInspection(sample,key),pointX=x(sample);inspect?.setAttribute('visibility','visible');line?.setAttribute('x1',pointX.toFixed(1));line?.setAttribute('x2',pointX.toFixed(1));if(dot){if(sample.value===null||!Number.isFinite(sample.value))dot.setAttribute('visibility','hidden');else{dot.setAttribute('visibility','visible');dot.setAttribute('cx',pointX.toFixed(1));dot.setAttribute('cy',y(sample.value).toFixed(1));}}const text=`${detail.date} · ${detail.value}`;if(tip){const rect=svg.getBoundingClientRect(),edge=Math.min(105,rect.width/2);tip.textContent=text;tip.style.left=`${Math.max(edge,Math.min(rect.width-edge,localX))}px`;tip.hidden=false;}if(status)status.textContent=efficiencyChartAnnouncement(detail,announce);};
 svg.onpointermove=event=>{const rect=svg.getBoundingClientRect(),localX=Math.max(0,Math.min(rect.width,event.clientX-rect.left));svg.dataset.efficiencyHoverRatio=String(localX/Math.max(1,rect.width));activeIndex=efficiencyChartPointerIndex(event.clientX,rect,samples.length);showInspection(activeIndex,localX);};
 svg.onpointerleave=()=>{delete svg.dataset.efficiencyHoverRatio;hideInspection();svg.setAttribute('aria-label',summaryLabel);};
 svg.onfocus=()=>{const rect=svg.getBoundingClientRect();activeIndex=samples.length-1;showInspection(activeIndex,rect.width*x(samples[activeIndex])/width,true);};
 svg.onkeydown=event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();activeIndex=efficiencyChartKeyIndex(event.key,activeIndex,samples.length);const rect=svg.getBoundingClientRect();showInspection(activeIndex,rect.width*x(samples[activeIndex])/width,true);};
 svg.onblur=()=>{hideInspection();svg.setAttribute('aria-label',summaryLabel);if(status)status.textContent='';};
 if(svg.matches(':hover')&&Number.isFinite(hoverRatio)){const rect=svg.getBoundingClientRect(),localX=Math.max(0,Math.min(rect.width,rect.width*hoverRatio));activeIndex=efficiencyChartPointerIndex(rect.left+localX,rect,samples.length);showInspection(activeIndex,localX);}
}
function updateEfficiencySortHeaders(){
 document.querySelectorAll('[data-efficiency-sort]').forEach(button=>{const active=button.dataset.efficiencySort===efficiencySort.key,th=button.closest('th'),mark=button.querySelector('.efficiencySortMark');th?.setAttribute('aria-sort',active?(efficiencySort.direction==='asc'?'ascending':'descending'):'none');if(mark)mark.textContent=active?(efficiencySort.direction==='asc'?'↑':'↓'):'';});
}
function efficiencyDecimal(value,suffix='x'){
 if(value===null||!Number.isFinite(value))return '--';
 const digits=value>=100?0:value>=10?1:2;
 return value.toFixed(digits).replace(/\.0+$/,'')+suffix;
}
function efficiencyCoverageNote(overall,metric,label){
 const coverage=overall.coverage?.[metric]||{},covered=Number(coverage.covered_rows||0),total=Number(coverage.total_rows||0);
 if(!covered)return `${label} unavailable`;
 return `${label} · ${f(covered)} / ${f(total)} models${coverage.complete?'':' · partial'}`;
}
function efficiencyEstimateNote(row){
 const basis=usageBasis(row);
 return basis==='mixed'?' · includes local estimates':basis==='local_estimate'?' · local estimate':'';
}
function efficiencyReasoningPresentation(data,metric){
 const executions=Math.max(0,Number(data?.executions)||0),reasoningExecutions=Math.max(0,Number(data?.reasoning_executions)||0),thinkingExecutions=Math.min(executions,Math.max(0,Number(data?.thinking_executions)||0));
 if(metric?.reasoning_ratio!==null&&Number.isFinite(metric?.reasoning_ratio))return {value:pct(metric.reasoning_ratio),note:`${f(reasoningExecutions)} / ${f(executions)} exec`};
 if(thinkingExecutions>0&&executions>0)return {value:`${pct(thinkingExecutions/executions)} thinking execs`,note:'token ratio unavailable'};
 return {value:'--',note:'unavailable'};
}
function efficiencySummaryPresentation(data){
 const metric=efficiencyMetrics(data),reasoning=efficiencyReasoningPresentation(data,metric),estimate=hasLocalEstimate(data)?' · local est':'';
 const outputAvailable=metric.output_per_dollar!==null&&Number.isFinite(metric.output_per_dollar),outputNote=outputAvailable?(data?.coverage?.cost?.complete===false?'partial':'covered'):'unavailable';
 return {output:{value:outputAvailable?`${compactNumber(metric.output_per_dollar)} / $`:'--',note:outputNote+(outputAvailable?estimate:'')},reasoning:{value:reasoning.value,note:reasoning.note+(reasoning.value!=='--'?estimate:'')}};
}
function sessionEfficiencySource(s){
 if(Array.isArray(s?.selected_stats?.model_stats))return s.selected_stats;
 const id=stateSessionId(s),key=String(s?.source?.key||''),rows=s?.xsession?.sessions||[];
 const summary=key?rows.find(row=>String(row?.key||'')===key):rows.find(row=>String(row?.id||'')===String(id||''));
 return summary||s||{};
}
function normalizeSessionEfficiencyAggregate(overall){
 const data=overall||{},reasoningAvailable=Number(data.reasoning_executions||0)>0&&Number(data.reasoning_output_tokens||0)>0;
 return {...data,availability:{...(data.availability||{}),reasoning_tokens:reasoningAvailable}};
}
function renderSessionEfficiency(s){
 const source=sessionEfficiencySource(s),overall=normalizeSessionEfficiencyAggregate(aggregateModelDays(source.model_stats||[]));
 if(hasLocalEstimate(source)||hasLocalEstimate(s)){overall.usage_basis=usageBasis(source)||usageBasis(s);overall.provenance={usage_basis:overall.usage_basis};}
 const presentation=efficiencySummaryPresentation(overall);
 $('session-output-dollar').textContent=presentation.output.value;
 $('session-output-dollar-note').textContent=presentation.output.note;
 $('session-reasoning-ratio').textContent=presentation.reasoning.value;
 $('session-reasoning-ratio-note').textContent=presentation.reasoning.note;
}
function renderEfficiency(stats){
 const allModels=(stats?.models||[]).map(row=>({...row,id:row.id||row.model,runtime:row.runtime||(row.providers||[]).join(' + ')||'unknown runtime'})),window=efficiencyRangeWindow();
 efficiencyFilters=migrateModelRuntimeFilters(efficiencyFilters,allModels);saveActiveEfficiencyFilters();
 const allSelected=!efficiencyFilters.length,selected=filterEfficiencyModels(allModels,efficiencyFilters),options=allModels.slice().sort((a,b)=>modelRuntimeLabel(a).localeCompare(modelRuntimeLabel(b))),rows=efficiencyModelRows(selected,window),efficiencyModelRowsForRange=rows.map(row=>row.window),overall=aggregateModelDays(efficiencyModelRowsForRange),metrics=efficiencyMetrics(overall),rangeLabel=modelRangeLabel(efficiencyRange);
 const pickerOptions=`<label class="modelOption all"><input type=checkbox data-efficiency-model-choice=all ${allSelected?'checked':''}><span>All model/runtime/budget rows</span></label>`+options.map(row=>{const label=modelRuntimeLabel(row);return `<label class=modelOption title="${esc(label)}"><input type=checkbox data-efficiency-model-choice=model value="${esc(row.id)}" ${efficiencyFilters.includes(row.id)?'checked':''}><span>${esc(label)}</span></label>`;}).join('');
 setLogHtml($('e-model-options'),pickerOptions);
 if($('e-model-picker').open)requestAnimationFrame(positionEfficiencyPickerMenu);
 const selectionLabel=allSelected?'All model runtimes':selected.length===1?modelRuntimeLabel(selected[0]):`${selected.length} model runtimes selected`;
 $('e-model-summary').textContent=selectionLabel;$('e-model-summary').title=allSelected?'All model runtimes':selected.map(modelRuntimeLabel).join(', ');
 $('e-range').value=efficiencyRange;
 $('e-output-dollar').textContent=metrics.output_per_dollar===null?'--':`${compactNumber(metrics.output_per_dollar)} / $`;
 $('e-output-dollar-coverage').textContent=efficiencyCoverageNote(overall,'cost','Coverage')+efficiencyEstimateNote(overall);
 $('e-reasoning-ratio').textContent=metrics.reasoning_ratio===null?'--':pct(metrics.reasoning_ratio);
 const reasoningCovered=Number(overall.reasoning_executions||0),reasoningTotal=Number(overall.executions||0),thinkingUnavailable=efficiencyModelRowsForRange.reduce((total,row)=>total+thinkingUnavailableExecutions(row),0),thinkingNote=thinkingUnavailable?` · ${f(thinkingUnavailable)} thinking exec · token split unavailable`:'';
 $('e-reasoning-coverage').textContent=(reasoningCovered?`Thinking / reasoning coverage · ${f(reasoningCovered)} / ${f(reasoningTotal)} exec${thinkingNote}`:thinkingUnavailable?`${f(thinkingUnavailable)} thinking exec · token split unavailable`:'Thinking / reasoning coverage unavailable')+efficiencyEstimateNote(overall);
 const comparison=efficiencyComparisonWindows(efficiencyRange),priorOverall=comparison?aggregateModelDays(efficiencyModelRows(selected,comparison.prior).map(row=>row.window)):null,priorMetrics=priorOverall?efficiencyMetrics(priorOverall):null,comparisonDelta=key=>comparison?efficiencyPeriodDelta(metrics,priorMetrics,key,comparison.label):{text:'No comparable prior period',direction:''},trend=efficiencyTrendDays(rows,window),outputDelta=comparisonDelta('output_per_dollar'),reasoningDelta=comparisonDelta('reasoning_ratio'),contextDelta=comparisonDelta('context_load'),cacheHitDelta=comparisonDelta('cache_hit_ratio');
 renderTrendDelta('e-output-dollar-change',outputDelta,'up',!comparison);renderTrendDelta('e-reasoning-change',reasoningDelta,'down',!comparison);renderTrendDelta('e-context-load-change',contextDelta,'down',!comparison);renderTrendDelta('e-cache-hit-change',cacheHitDelta,'up',!comparison);
 renderEfficiencyChart('e-output-dollar-chart',trend,'output_per_dollar','Output per covered dollar');renderEfficiencyChart('e-reasoning-chart',trend,'reasoning_ratio','Reasoning ratio');renderEfficiencyChart('e-context-load-chart',trend,'context_load','Context load');renderEfficiencyChart('e-cache-hit-chart',trend,'cache_hit_ratio','Cache hit ratio');
 $('e-context-load').textContent=efficiencyDecimal(metrics.context_load);
 const ioCovered=Number(overall.io_covered_executions||0),ioTotal=Number(overall.executions||0);
 $('e-context-load-note').textContent=metrics.context_load===null?(overall.availability?.input_tokens===true&&overall.availability?.output_tokens===true?'no executions with paired input/output':'input/output evidence unavailable'):ioCovered<ioTotal?`input per output token · paired coverage ${f(ioCovered)} / ${f(ioTotal)} exec`:'input tokens per output token';
 $('e-cache-hit').textContent=metrics.cache_hit_ratio===null?'--':pct(metrics.cache_hit_ratio);
 $('e-cache-hit-note').textContent=metrics.cache_hit_ratio===null?'cache evidence unavailable':`${compactNumber(overall.cache_read_tokens||0)} cached of ${compactNumber(overall.cache_covered_input_tokens??overall.input_tokens??0)} input`+efficiencyEstimateNote(overall);
 $('e-model-meta').textContent=`${f(rows.length)} models${efficiencyEstimateNote(overall)}`;updateEfficiencySortHeaders();
	 const sortedRows=sortEfficiencyRows(rows);$('e-model-table').querySelector('tbody').innerHTML=sortedRows.length?sortedRows.map(row=>{const data=row.window,metric=efficiencyMetrics(row.window),reasoning=efficiencyReasoningPresentation(data,metric),tokenExecutions=Number(data.token_covered_executions===undefined?data.executions:data.token_covered_executions)||0,ioExecutions=Number(data.io_covered_executions===undefined?tokenExecutions:data.io_covered_executions)||0,contextCoverage=ioExecutions>=Number(data.executions||0)&&data.coverage?.tokens?.complete!==false?'covered':'partial',estimate=hasLocalEstimate(row)?' · local est':'',effort=efficiencyEffortLabel(row);return `<tr><td><div class=modelName title="${esc(row.model)}">${esc(row.model)}</div></td><td>${esc(row.runtime)}${estimate}</td><td>${esc(effort)}</td><td class="num efficiencyMetric"><strong class=mono>${data.availability?.cost===false?'--':money(data.cost)}</strong><span>${data.availability?.cost===false?'unavailable':data.coverage?.cost?.complete?'covered':'partial'}</span></td><td class="num efficiencyMetric"><strong class=mono>${metric.output_per_dollar===null?'--':compactNumber(metric.output_per_dollar)+' / $'}</strong><span>${metric.output_per_dollar===null?'unavailable':data.coverage?.cost?.complete?'covered':'partial'}</span></td><td class="num efficiencyMetric"><strong class=mono>${reasoning.value}</strong><span>${reasoning.note}</span></td><td class="num efficiencyMetric"><strong class=mono>${efficiencyDecimal(metric.context_load)}</strong><span>${contextCoverage}</span></td><td class="num efficiencyMetric"><strong class=mono>${metric.cache_hit_ratio===null?'--':pct(metric.cache_hit_ratio)}</strong><span>${metric.cache_hit_ratio===null?'unavailable':'cache read'}</span></td></tr>`;}).join(''):'<tr><td colspan=8><div class="modelEmpty emptyState">No model activity in this range</div></td></tr>';
}
function paintEfficiencyProjects(stats){
 const projects=(stats?.projects||[]).filter(project=>typeof project==='string'&&project);
 if(efficiencyProject&&!projects.includes(efficiencyProject)){efficiencyProject='';efficiencyFilters=loadEfficiencyFilters('');localStorage.removeItem('tm_efficiency_project');}
 setLogSelectOptions($('e-project'),'<option value="">All projects</option>'+projects.map(project=>`<option value="${esc(project)}">${esc(projectFilterLabel(project))}</option>`).join(''),efficiencyProject);
 $('e-project').title=stats?.projects_truncated?'Showing the first 500 discovered projects.':'';
}
function setEfficiencyProjectLoading(loading){if(loading)$('e-project').setAttribute('aria-busy','true');else $('e-project').removeAttribute('aria-busy');}
function commitEfficiencyStats(stats,scope,generation){activeEfficiencyStats=stats;activeEfficiencyScope=scope;activeEfficiencyGeneration=generation;setEfficiencyProjectLoading(false);renderEfficiency(stats);}
async function renderActiveEfficiency(){
 if(!LATEST)return;
 const machine=LATEST.xsession?.model_stats||{models:[],projects:[]},generation=Number(LATEST.xsession?.generated_at||0);paintEfficiencyProjects(machine);
 if(!efficiencyProject){efficiencyProjectRequest++;efficiencyProjectLoadingKey='';setEfficiencyProjectLoading(false);commitEfficiencyStats(machine,'',generation);return;}
 const project=efficiencyProject,key=`${generation}\n${project}`,cached=efficiencyProjectCache.get(key);
 if(cached){commitEfficiencyStats(cached,project,generation);return;}
 if(efficiencyProjectLoadingKey===key)return;
 setEfficiencyProjectLoading(true);efficiencyProjectLoadingKey=key;const request=++efficiencyProjectRequest;
 try{
  const response=await fetch('/model-stats?project='+encodeURIComponent(project),{cache:'no-store'}),payload=await response.json();
  if(!response.ok||!payload.ok)throw new Error('Project model stats are unavailable.');
  if(request!==efficiencyProjectRequest||project!==efficiencyProject)return;
  const responseGeneration=Number(payload.generated_at||generation);efficiencyProjectCache.set(key,payload.model_stats);efficiencyProjectCache.set(`${responseGeneration}\n${project}`,payload.model_stats);commitEfficiencyStats(payload.model_stats,project,responseGeneration);
 }catch(error){if(request===efficiencyProjectRequest&&project===efficiencyProject)setEfficiencyProjectLoading(false);}
 finally{if(efficiencyProjectLoadingKey===key){efficiencyProjectLoadingKey='';setEfficiencyProjectLoading(false);}}
}
function rerenderActiveEfficiency(){
 if(activeEfficiencyStats&&activeEfficiencyScope===efficiencyProject)renderEfficiency(activeEfficiencyStats);
 else renderActiveEfficiency();
}
$('e-range').value=efficiencyRange;
$('e-range').onchange=event=>{efficiencyRange=EFFICIENCY_RANGES.includes(event.target.value)?event.target.value:'30';localStorage.setItem('tm_efficiency_range',efficiencyRange);if(LATEST)renderActiveEfficiency();};
$('e-project').addEventListener('change',event=>{efficiencyProject=event.target.value;efficiencyFilters=loadEfficiencyFilters(efficiencyProject);if(efficiencyProject)localStorage.setItem('tm_efficiency_project',efficiencyProject);else localStorage.removeItem('tm_efficiency_project');efficiencyProjectRequest++;efficiencyProjectLoadingKey='';renderActiveEfficiency();});
$('e-model-options').addEventListener('change',event=>{const input=event.target.closest('input[data-efficiency-model-choice]');if(!input)return;if(input.dataset.efficiencyModelChoice==='all')efficiencyFilters=[];else if(input.checked)efficiencyFilters=[...new Set([...efficiencyFilters,input.value])];else efficiencyFilters=efficiencyFilters.filter(id=>id!==input.value);saveActiveEfficiencyFilters();rerenderActiveEfficiency();});
$('e-model-picker').addEventListener('keydown',event=>{if(event.key==='Escape')$('e-model-picker').removeAttribute('open');});
document.addEventListener('pointerdown',event=>{const picker=$('e-model-picker');if(picker.open&&!picker.contains(event.target))picker.removeAttribute('open');});
function positionEfficiencyPickerMenu(){const picker=$('e-model-picker'),menu=$('e-model-options');if(!picker.open){picker.classList.remove('opensUp');menu.style.maxHeight='';return;}const trigger=picker.querySelector('summary'),rect=trigger.getBoundingClientRect(),margin=12,above=Math.max(0,rect.top-margin),below=Math.max(0,window.innerHeight-rect.bottom-margin),preferred=Math.min(360,menu.scrollHeight||360),opensUp=below<preferred&&above>below,available=opensUp?above:below;picker.classList.toggle('opensUp',opensUp);menu.style.maxHeight=`${Math.min(360,available)}px`;}
$('e-model-picker').addEventListener('toggle',()=>requestAnimationFrame(positionEfficiencyPickerMenu));
window.addEventListener('resize',()=>{if($('e-model-picker').open)positionEfficiencyPickerMenu();});
document.querySelectorAll('[data-efficiency-sort]').forEach(button=>button.onclick=()=>{const key=button.dataset.efficiencySort;if(efficiencySort.key===key)efficiencySort={key,direction:efficiencySort.direction==='desc'?'asc':'desc'};else efficiencySort={key,direction:key==='model'?'asc':'desc'};if(activeEfficiencyStats)renderEfficiency(activeEfficiencyStats);});
