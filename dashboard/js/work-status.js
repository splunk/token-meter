function renderWorkTags(tags){
 const host=$('preview-work-tags');
 if(!tags||!(tags.area||tags.work_type)){host.hidden=true;host.innerHTML='';return;}
 const tagChips=[];
 if(tags.area&&tags.area!=='Pending')tagChips.push(`<span class=workTag title="${tags.area_guess?'Area: the local model\u2019s best guess with low confidence':'Area (local model estimate)'}">${esc(tags.area)}${tags.area_guess?' (likely)':''}</span>`);
 if(tags.work_type)tagChips.push(`<span class=workTag title="Work type (local model estimate)">${esc(WORK_TYPE_LABELS[tags.work_type]||tags.work_type)}</span>`);
 if(tags.complexity)tagChips.push(`<span class=workTag title="Complexity (local model estimate)">${esc(WORK_COMPLEXITY_LABELS[tags.complexity]||tags.complexity)}</span>`);
 if(tags.labeled_turns)tagChips.push(`<span class="workTag${tags.corrections?' pushback':''}" title="Follow-up turns that pushed back on the previous work">${tags.corrections} pushback${tags.corrections===1?'':'s'} / ${tags.labeled_turns}</span>`);
 host.innerHTML=tagChips.join('');host.hidden=!tagChips.length;
}
const WORK_TYPE_LABELS={feature:'Feature work',debug:'Bug fixing',refactor:'Refactoring',test:'Testing',review:'Code review',plan:'Planning & design',explore:'Questions & research',ops:'DevOps & setup',docs:'Docs & writing',other:'Non-software',unclear:'Unclear'};
const WORK_COMPLEXITY_LABELS={routine:'Routine',everyday:'Everyday',complex:'Complex',high_impact:'High impact'};
const WORK_TIER_LABELS={light:'Light models',standard:'Standard models',premium:'Premium models'};
const WORK_UNLABELED_AREAS=['No request text','Outside history','Pending'],WORK_MUTED_AREAS=['Unclear',...WORK_UNLABELED_AREAS];
const WORK_AREA_NAMES={Pending:'Not labeled yet','Outside history':'Outside labeling history'};
const WORK_AREA_TIPS={Unclear:'The local model was not confident about the area.','No request text':'Token Meter has no typed request to label for these sessions: the app does not record one Token Meter can read, or the session never had one.','Outside history':'Older than the labeling history in Settings → Work insights. Raise it to label these.',Pending:'Waiting for the local model.'};
const WORK_AREA_COLORS=['var(--w1)','var(--w2)','var(--w3)','var(--w4)','var(--w5)','var(--w6)','var(--w7)','var(--w8)'];
function workAreaColor(area,areas){
 if(area==='Unclear')return'var(--w-unclear)';
 if(WORK_UNLABELED_AREAS.includes(area))return'var(--w-pending)';
 const index=(areas||[]).indexOf(area);
 return WORK_AREA_COLORS[index]||'var(--w-unclear)';
}
function workPercent(value){return value==null?'--':`${Math.round(value*100)}%`;}
function workRateText(rework){
 if(!rework)return'<span class=workMuted>--</span>';
 const text=workPercent(rework.rate);
 return rework.few_samples?`<span class=workMuted title="Fewer than 20 labeled turns">${text} · few</span>`:text;
}
function workMonthLabel(month,partial){return workMonthName(month,{year:true,partial});}
function workClock(value){
 if(!value||value==='indefinite')return'';
 const when=new Date(Number(value)*1000),today=new Date().toDateString()===when.toDateString();
 return when.toLocaleString([],today?{hour:'2-digit',minute:'2-digit'}:{weekday:'short',hour:'2-digit',minute:'2-digit'});
}
function workDuration(seconds){
 if(!seconds)return'';
 const minutes=Math.max(1,Math.round(seconds/60));
 return minutes<90?`about ${minutes} min left`:`about ${Math.round(minutes/60)} h left`;
}
const WORK_SETUP_ACTIVE=['checking','installing_ollama','starting_ollama','downloading_model','importing'];
let workSetupTimer=0;
function workGigabytes(bytes){const value=Math.max(0,bytes||0);return value<1e9?`${Math.round(value/1e6)} MB`:`${(value/1e9).toFixed(1)} GB`;}
const WORK_SETUP_STEP={checking:'ollama',installing_ollama:'ollama',starting_ollama:'ollama',downloading_model:'model',importing:'prepare'};
const WORK_SETUP_DETAIL={size:'The connection dropped before the download finished. What arrived is kept, and the next try resumes from there.',hash:'The downloaded file didn’t match its checksum, so it was deleted and will be downloaded again.',archive:'Ollama’s download didn’t pass its safety check, so it was deleted.',signature:'Ollama’s download wasn’t signed by Ollama, so it was deleted.',http_status:'The download server turned the request away.',tls:'A secure connection couldn’t be made. A proxy, VPN, or network filter may be blocking it.',timeout:'The connection timed out.',connection:'The connection was lost or couldn’t be opened.',curl:'A second download method also failed.',disk:'The disk filled up during the download.',start_timeout:'Ollama took too long to start.',import_failed:'Adding the model to Ollama failed.'};
// Download speed from successive status polls, smoothed so one slow poll doesn't swing the estimate.
let workSetupRate={bytes:0,at:0,speed:0,state:''};
function workSetupSpeed(setup){
 const now=Date.now(),done=Number(setup.done_bytes||0),rate=workSetupRate;
 if(rate.state!==setup.state||done<rate.bytes){workSetupRate={bytes:done,at:now,speed:0,state:setup.state};return 0;}
 const seconds=(now-rate.at)/1000;
 if(seconds>=2){const sample=(done-rate.bytes)/seconds;rate.speed=rate.speed?rate.speed*.6+sample*.4:sample;rate.bytes=done;rate.at=now;}
 return rate.speed;
}
function workRate(bytesPerSecond){return bytesPerSecond>=1e6?`${(bytesPerSecond/1e6).toFixed(1)} MB/s`:`${Math.max(1,Math.round(bytesPerSecond/1e3))} KB/s`;}
function workSetupCopy(setup){
 if(!setup)return null;
 const progress=setup.total_bytes?`${workGigabytes(setup.done_bytes)} of ${workGigabytes(setup.total_bytes)}`:'';
 let pace='';
 if(['installing_ollama','downloading_model'].includes(setup.state)&&setup.total_bytes){
  const speed=workSetupSpeed(setup),left=Math.max(0,setup.total_bytes-setup.done_bytes);
  if(speed>0)pace=` · ${workRate(speed)} · ${workDuration(left/speed)||'almost done'}`;
  if(speed>0&&speed<1e6)pace+=' · slow connection: this can take a while, and you can leave this page';
 }
 const retry=setup.state==='failed'&&setup.retry_at?`Token Meter tries again on its own at ${workClock(setup.retry_at)}${setup.attempts>1?` (attempt ${setup.attempts+1})`:''}, or select Retry setup now.`:'';
 const why=WORK_SETUP_DETAIL[setup.detail]||'';
 return {
  checking:['Setup','Checking Ollama','Looking for Ollama and the classifier model on this Mac.'],
  installing_ollama:['Setup',`Downloading Ollama ${setup.ollama_version||''}`.trim(),`${progress}${pace} · a pinned, signed build that runs only on this Mac.`],
  starting_ollama:['Setup','Starting Ollama','The first start can take a minute while macOS checks it. Ollama runs in the background on this Mac only.'],
  downloading_model:['Setup','Downloading the labeling model',`${progress}${pace} · the download resumes if the connection drops.`],
  importing:['Setup','Preparing the model','Adding it to Ollama. This can take a few minutes.'],
  failed:['Setup',{network:'The download stopped',verify:'A download failed its check',disk_space:'Not enough disk space',memory:'Not enough memory on this Mac',ollama_start:'Ollama didn’t start',ollama_offline:'Ollama isn’t running',import:'Importing the model failed'}[setup.reason]||'Setup stopped',
   [why,retry||({network:'Check the internet connection, then select Retry setup.',verify:'Select Retry setup to download it again.',disk_space:setup.needed_bytes?`Free up about ${workGigabytes(setup.needed_bytes)}, then select Retry setup.`:'Free up some disk space, then select Retry setup.',memory:'The labeling model needs a Mac with at least about 10 GB of memory, so nothing was downloaded.',ollama_start:'Select Retry setup to try again.',ollama_offline:'Start Ollama on this Mac (open the Ollama app or run ollama serve), then select Retry setup.',import:'Select Retry setup to try again.'}[setup.reason]||'Select Retry setup to try again.')].filter(Boolean).join(' ')],
 }[setup.state]||null;
}
// Which step is done, current, or failed; a failure belongs to the step that was running.
function renderWorkSetupSteps(setup,lastStep){
 const list=$('w-setup-steps'),bar=$('w-setup-bar');
 list.hidden=!setup;bar.hidden=!(setup&&setup.total_bytes&&['installing_ollama','downloading_model'].includes(setup.state));
 if(!setup)return;
 const order=['ollama','model','prepare','label'],current=setup.state==='failed'?(WORK_SETUP_STEP[setup.failed_during]||lastStep):WORK_SETUP_STEP[setup.state];
 const at=Math.max(0,order.indexOf(current));
 list.querySelectorAll('li').forEach((item,index)=>{item.dataset.state=index<at?'done':index===at?(setup.state==='failed'?'failed':'current'):'';});
 if(!bar.hidden){const share=Math.min(100,Math.round(100*setup.done_bytes/setup.total_bytes));bar.querySelector('i').style.width=`${share}%`;bar.setAttribute('aria-valuenow',share);}
}
let workSetupLastStep='ollama';
async function refreshWorkStatus(){
 if(!WORK||!$('view-work').classList.contains('on'))return;
 try{
  const response=await fetch('/work-insights/status',{cache:'no-store'}),payload=await response.json();
  if(!payload.ok)return;
  WORK.status=payload.status;WORK.settings=payload.settings;renderWorkStatus(WORK);
  if(payload.status?.setup?.state==='ready')loadWork();
 }catch(error){}
}
// The charts read only labeled requests, so say so until labeling catches up.
function workProgressNote(settings,status,coverage,hasInsights){
 const labelable=Math.max(0,(coverage.sessions||0)-(coverage.no_text_sessions||0)-(coverage.outside_sessions||0));
 const labeled=coverage.labeled_sessions||0;
 if(!settings.enabled||!hasInsights||(labeled>=labelable&&!status.pending))return null;
 // The status card above already counts what is left; this says what that means for the charts.
 if(!labeled)return['Results appear as labeling finishes','No session in this period is labeled yet, so the charts below are empty for now. They fill in as requests are labeled.'];
 if(labeled>=labelable)return['Results are updating','Every session in this period has labels. Some are being labeled again, so numbers below may shift a little as that finishes.'];
 // One line: the status card above already gives the labeled share.
 return['','Charts are partial until labeling finishes · unlabeled sessions show in gray.'];
}
function renderWorkStatus(payload){
 const status=payload?.status||{},settings=payload?.settings||{},coverage=payload?.insights?.coverage||{};
 const state=status.reason==='delete_pending'?'storage_error':settings.enabled?status.state||'idle':'disabled';
 const labelable=Math.max(0,(coverage.sessions||0)-(coverage.no_text_sessions||0)-(coverage.outside_sessions||0));
 const share=labelable?Math.floor(100*coverage.labeled_sessions/labelable):0;
 // While requests are queued, show what is left; the session share alone reads as done during a relabel.
 const progress=`${status.pending?`${status.pending.toLocaleString()} request${status.pending===1?'':'s'} left to label · charts update as they finish`:`Labeled ${share}% of labelable sessions in this period`}${status.eta_s&&state==='running'?` · ${workDuration(status.eta_s)}`:''}`;
 const copy={
  disabled:['Off','Work insights are off','Turn on local classification in Settings to label sessions.'],
  setup_needed:['Setup',status.reason==='remote_model'?'Cloud model refused':'Classifier model not found',status.reason==='model_missing'?(status.model_change?'Work insights now use Google’s Gemma 4 E4B model (Apache-2.0): about a 5 GB download that needs about 11 GB free while it sets up and about 5.5 GB of memory while it labels. Select Retry setup to download it.':`Looking for “${settings.model||'token-meter-gemma'}”. Select Retry setup to install it.`):status.reason==='remote_model'?'That Ollama model runs on another host. Choose a local model in Settings.':'Start Ollama on this machine, then Token Meter retries automatically.'],
  running:['Running','Labeling sessions',progress],
  idle:['Ready',status.pending?'Waiting to label more requests':'Up to date',progress],
  paused:['Paused','Paused',status.paused_until==='indefinite'?'Paused until you resume.':`Paused until ${workClock(status.paused_until)}.`],
  throttled:['Waiting',{on_battery:'Waiting: on battery power',system_busy:'Waiting: this Mac is busy',model_slow:'Waiting: the model is responding slowly',low_memory:'Waiting: memory is low',not_enough_memory:'Waiting: not enough memory on this Mac'}[status.reason]||'Waiting',status.reason==='not_enough_memory'?'The labeling model needs about 6 GB of memory plus room for macOS and your apps, which this Mac doesn’t have. Work insights stay off the model so your Mac stays responsive.':status.reason==='low_memory'?`The model is unloaded so other apps keep their memory. ${progress} · checks again${status.retry_at?` at ${workClock(status.retry_at)}`:' shortly'}.`:`${progress} · checks again in a minute.`],
  backoff:['Retrying',status.reason==='internal_error'?'Recovering from an internal error':status.reason==='timeout'?'Ollama is slow to answer':'Can’t reach Ollama',`${status.retry_at?`Retrying at ${workClock(status.retry_at)}.`:'Retrying shortly.'} Queued work is kept.${status.error_at?` (${status.error_at})`:''}`],
  storage_error:['Error',status.reason==='delete_pending'?'Deleting labels didn’t finish':'Can’t save labels',status.reason==='delete_pending'?'Labeling is stopped until the delete succeeds. Token Meter retries every five minutes.':'Token Meter retries every five minutes.'],
 }[state]||['Ready','Up to date',progress];
 const setupRelevant=status.setup&&(WORK_SETUP_ACTIVE.includes(status.setup.state)||(status.setup.state==='failed'&&['setup_needed','backoff'].includes(status.state)));
 const setup=settings.enabled&&setupRelevant?status.setup:null,setupCopy=workSetupCopy(setup);
 const shown=setupCopy||copy;
 const off=!settings.enabled;$('w-turn-on').hidden=!off;$('w-open-settings').hidden=off;
 const pill=$('w-state');pill.dataset.state=setupCopy?(setup.state==='failed'?'setup_needed':'running'):state;pill.textContent=shown[0];
 $('w-status-title').textContent=shown[1];$('w-status-detail').textContent=shown[2];
 const note=workProgressNote(settings,status,coverage,Boolean(payload?.insights));
 $('w-progress-note').hidden=!note;
 if(note){$('w-progress-title').textContent=note[0];$('w-progress-title').hidden=!note[0];$('w-progress-detail').textContent=note[1];}
 if(setup&&WORK_SETUP_STEP[setup.state])workSetupLastStep=WORK_SETUP_STEP[setup.state];
 renderWorkSetupSteps(setupCopy?setup:null,workSetupLastStep);
 const setupState=status.setup?.state;
 const serviceNeedsSetup=['setup_needed','backoff'].includes(status.state);
 $('w-setup-retry').hidden=!(settings.enabled&&status.setup&&!WORK_SETUP_ACTIVE.includes(setupState)&&(setupState==='failed'?serviceNeedsSetup:state==='setup_needed'));
 clearTimeout(workSetupTimer);
 if(setup&&(WORK_SETUP_ACTIVE.includes(setup.state)||setup.retry_at))workSetupTimer=setTimeout(refreshWorkStatus,setup.retry_at&&setup.state==='failed'?15000:4000);
 $('w-pause-wrap').hidden=!settings.enabled||state==='paused';
 $('w-resume').hidden=state!=='paused';
}
const WORK_OUTCOME_ORDER=['accepted','recovered','ended_on_pushback','unclear'],OUTCOMES_SHOWN=WORK_OUTCOME_ORDER;
function workIsDay(key){return /^\d{4}-\d{2}-\d{2}$/.test(String(key||''));}
function workMonthName(month,{year=false,partial=false}={}){
 if(workIsDay(month)){
  const day=new Date(`${month}T12:00:00`);if(Number.isNaN(day.getTime()))return month;
  const text=day.toLocaleDateString([],year?{month:'short',day:'numeric',year:'numeric'}:{month:'short',day:'numeric'});
  return partial?`${text} · so far`:text;
 }
 const date=new Date(`${month}-01T00:00:00`);
 if(Number.isNaN(date.getTime()))return month;
 const text=date.toLocaleDateString([],year?{month:'short',year:'numeric'}:{month:'short'});
 return partial?`${text} · so far`:text;
}
let workBreakdownModel='',workBreakdownSort={key:'spend',direction:'desc'};
const WORK_BREAKDOWN_COLUMNS=[
 ['name','Kind of work','Select to sort by name.'],
 ['spend','Spend','Each request\'s share of its session\'s cost.'],
 ['requests','Requests','Messages you typed. Agent : human counts only those from apps that split model calls by request; hover a row for its counts.'],
 ['resolved_rate','Task outcomes','Tasks with a labeled follow-up: accepted, recovered after pushback, ended on pushback, or unclear. Sorts by resolved share.'],
 ['cost_per_resolved','Cost / resolved','Spend on resolved tasks per resolved task.'],
 ['agent_per_human','Agent : human','Agent turns (model calls) per human turn (message you typed). Only apps that split model calls by request count.'],
 ['pushback','Pushback','How often your next message pushed back. Faded below 20 labeled follow-ups.'],
 ['best_model','Best model','Least pushback among models with 20+ labeled follow-ups and 3+ sessions for this kind of work. Sorts by its pushback.'],
 ['worst_model','Worst model','Most pushback among the same rated models. Sorts by its pushback.'],
];
function workBreakdownName(row){return row.key?(WORK_TYPE_LABELS[row.key]||row.key):'Not labeled yet';}
function workBreakdownValue(row,key){
 if(key==='name')return workBreakdownName(row).toLowerCase();
 if(key==='pushback')return row.pushback?row.pushback.rate:null;
 if(key==='best_model'||key==='worst_model')return row[key]?row[key].rate:null;
 return row[key]??null;
}
function sortWorkBreakdown(rows,sort=workBreakdownSort){
 const sign=sort.direction==='asc'?1:-1;
 return [...rows].sort((a,b)=>{const x=workBreakdownValue(a,sort.key),y=workBreakdownValue(b,sort.key);
  if(x==null&&y==null)return b.spend-a.spend;if(x==null)return 1;if(y==null)return -1;
  return (x<y?-1:x>y?1:0)*sign||b.spend-a.spend;});
}
function workCellBar(value,max){
 if(value==null||!(max>0))return '';
 return `<span class=workCellBar><i style="width:${Math.max(2,100*value/max).toFixed(1)}%"></i></span>`;
}
function renderWorkBreakdown(insights){
 const breakdown=insights?.breakdown||{},models=breakdown.models||[],select=$('w-breakdown-model');
 const keys=models.map(item=>`${item.model}␟${item.runtime}`);
 if(workBreakdownModel&&!keys.includes(workBreakdownModel))workBreakdownModel='';
 select.innerHTML='<option value="">All models</option>'+models.map((item,index)=>`<option value="${esc(keys[index])}">${esc(`${item.model} · ${item.runtime}`)}</option>`).join('');
 select.value=workBreakdownModel;
 const chosen=workBreakdownModel?models[keys.indexOf(workBreakdownModel)]:null;
 const overall=chosen?chosen.overall:breakdown.overall,table=$('w-breakdown-table');
 const columns=WORK_BREAKDOWN_COLUMNS.filter(([key])=>!chosen||(key!=='best_model'&&key!=='worst_model'));
 if(chosen&&(workBreakdownSort.key==='best_model'||workBreakdownSort.key==='worst_model'))workBreakdownSort={key:'spend',direction:'desc'};
 const calls=value=>value==null?'--':value.toLocaleString(undefined,{maximumFractionDigits:1});
 const count=value=>(value||0).toLocaleString();
 const ratio=row=>workAgentRatio(row.agent_per_human);
 if(!overall||!overall.requests){$('w-breakdown-note').textContent='';$('w-breakdown-foot').textContent='';table.innerHTML='<tbody><tr><td><div class=workEmpty>No requests in this period.</div></td></tr></tbody>';return;}
 $('w-breakdown-note').textContent=`${chosen?`${chosen.model} · `:''}${money(overall.spend)} over ${count(overall.requests)} request${overall.requests===1?'':'s'} · agent : human ${ratio(overall)} · ${workPercent(overall.resolved_rate)} of judged tasks resolved`;
 const rows=sortWorkBreakdown(chosen?chosen.work_type:breakdown.work_type||[]);
 const max=key=>Math.max(0,...rows.map(row=>workBreakdownValue(row,key)||0));
 const maxSpend=max('spend'),maxRatio=max('agent_per_human');
 const muted=row=>!row.key||row.key==='unclear';
 const outcomeBar=row=>{const judged=WORK_OUTCOME_ORDER.reduce((sum,key)=>sum+((row.outcomes||{})[key]||0),0);
  if(!judged)return `<span class=workMuted title="${esc(`${count(row.tasks)} task${row.tasks===1?'':'s'}, none with a labeled follow-up`)}">--</span>`;
  const present=WORK_OUTCOME_ORDER.filter(key=>(row.outcomes||{})[key]);
  return `<span class=workOutcomeBar role=img aria-label="${esc(present.map(key=>`${WORK_OUTCOME_LABELS[key]} ${row.outcomes[key]}`).join(', '))}" title="${esc(`${count(judged)} of ${count(row.tasks)} tasks judged`)}">${present.map(key=>`<i style="flex:${row.outcomes[key]} 0 0;--swatch:${WORK_OUTCOME_COLORS[key]}" title="${esc(`${WORK_OUTCOME_LABELS[key]} · ${row.outcomes[key]}`)}"></i>`).join('')}</span><small>${workPercent(row.resolved_rate)} resolved</small>`;};
 const modelCell=(row,field)=>{const item=row[field];if(!item)return '<td><span class=workMuted title="Needs two models with enough labeled follow-ups">--</span></td>';
  const drill={model:item.model,model_runtime:item.runtime};if(row.key)drill.work_type=row.key;
  return `<td><button type=button class=workDrillBtn data-drill="${esc(JSON.stringify(drill))}" data-drill-label="${esc(`${workBreakdownName(row)} · ${item.model}`)}" title="${esc(`${item.model} · ${item.runtime} · ${workPercent(item.rate)} pushback over ${count(item.samples)} labeled follow-ups`)}"><span class=workScoreName><b>${esc(item.model)}</b><small>${esc(item.runtime)} · ${workPercent(item.rate)}</small></span></button></td>`;};
 const cells=(row,total)=>{
  const name=workBreakdownName(row),drill=total||!row.key?null:{work_type:row.key,...(chosen?{model:chosen.model,model_runtime:chosen.runtime}:{})};
  const label=total?'<strong>All work</strong>':drill?`<button type=button class=workDrillBtn data-drill="${esc(JSON.stringify(drill))}" data-drill-label="${esc(chosen?`${name} · ${chosen.model}`:name)}">${esc(name)}</button>`:esc(name);
  return `<td>${label}</td>
<td class=num><b>${money(row.spend)}</b>${total?'':`<small> ${workPercent(row.share)}</small>`}${total?'':workCellBar(row.spend,maxSpend)}</td>
<td class=num title="${esc(`${count(row.sessions)} session${row.sessions===1?'':'s'}`)}">${count(row.requests)}${row.few_samples&&!total?'<small class=workCellNote>few</small>':''}</td>
<td class=workOutcomeCell>${outcomeBar(row)}</td>
<td class=num>${row.cost_per_resolved==null?'<span class=workMuted>--</span>':money(row.cost_per_resolved)}</td>
<td class=num title="${esc(row.human_turns?`${count(row.agent_turns)} agent turns, ${count(row.human_turns)} human turns`:'Not recorded by these apps')}"><b>${ratio(row)}</b>${total?'':workCellBar(row.agent_per_human,maxRatio)}</td>
<td class=num>${workRateText(row.pushback)}</td>${chosen?'':modelCell(row,'best_model')+modelCell(row,'worst_model')}`;};
 const head=columns.map(([key,label,tip])=>{const active=workBreakdownSort.key===key;
  return `<th${key==='name'?'':' class=num'} aria-sort="${active?(workBreakdownSort.direction==='asc'?'ascending':'descending'):'none'}"><button class=efficiencySort type=button data-work-sort=${key} title="${esc(tip)}">${esc(label)}<span class=efficiencySortMark aria-hidden=true>${active?(workBreakdownSort.direction==='asc'?'↑':'↓'):''}</span></button></th>`;}).join('');
 table.innerHTML=`<thead><tr>${head}</tr></thead><tbody><tr class=workBreakdownTotal>${cells(overall,true)}</tr>${rows.map(row=>`<tr${muted(row)?' class=workMuted':''}>${cells(row,false)}</tr>`).join('')}</tbody>`;
 const unanswered=overall.requests-overall.human_turns,unjudged=(overall.outcomes||{}).unjudged||0;
 const excludedDetail=[unanswered>0?`${count(unanswered)} of ${count(overall.requests)} requests come from apps that don't split model calls by request, so Agent : human leaves them out.`:'',unjudged?`${count(unjudged)} of ${count(overall.tasks)} tasks had no labeled follow-up and aren't in Task outcomes.`:''].filter(Boolean).join(' ');
 const excludedShort=[unanswered>0?`${count(unanswered)} ${unanswered===1?'request':'requests'}`:'',unjudged?`${count(unjudged)} ${unjudged===1?'task':'tasks'}`:''].filter(Boolean).join(', ');
 $('w-breakdown-foot').innerHTML=excludedShort?`<span class=fieldtip tabindex=0 aria-description="${esc(excludedDetail)}" data-tip="${esc(excludedDetail)}">· ${esc(excludedShort)} excluded</span>`:'';
}
$('w-breakdown-model').addEventListener('change',event=>{workBreakdownModel=event.target.value;if(WORK)renderWorkBreakdown(WORK.insights);});
$('w-breakdown-table').addEventListener('click',event=>{const button=event.target.closest('[data-work-sort]');if(!button)return;event.stopPropagation();const key=button.dataset.workSort;
 workBreakdownSort=workBreakdownSort.key===key?{key,direction:workBreakdownSort.direction==='desc'?'asc':'desc'}:{key,direction:key==='name'||key==='best_model'?'asc':'desc'};if(WORK)renderWorkBreakdown(WORK.insights);});
function drawWorkTrend(svg,points,{value,axis,tip,average,averageText,empty,floor,daily}){
 // Drawn in pixels at the chart's own width, so labels stay at the 11px floor in a half-width card.
 const W=Math.max(320,Math.round(svg.parentElement.clientWidth||560));
 if(!points.length){svg.setAttribute('viewBox',`0 0 ${W} 120`);svg.innerHTML=`<text x=${W/2} y=60 text-anchor=middle class=workAxisText>${esc(empty)}</text>`;return;}
 const H=260,left=40,right=12,top=16,bottom=28,max=Math.max(floor,...points.map(value))*1.15,fill=`${svg.id}-fill`;
 svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
 const x=index=>points.length===1?(left+W-right)/2:left+(W-left-right)*index/(points.length-1),y=v=>top+(H-top-bottom)*(1-v/max);
 const path=points.map((point,index)=>`${index?'L':'M'}${x(index).toFixed(1)},${y(value(point)).toFixed(1)}`).join('');
 const area=points.length>1?`<path class=workArea style="fill:url(#${fill})" d="${path}L${x(points.length-1).toFixed(1)},${y(0).toFixed(1)}L${x(0).toFixed(1)},${y(0).toFixed(1)}Z"></path>`:'';
 const grid=[0,max/2,max].map(v=>`<line class=workGridLine x1=${left} x2=${W-right} y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}"></line><text class=workAxisText x=${left-6} y="${(y(v)+3).toFixed(1)}" text-anchor=end>${esc(axis(v))}</text>`).join('');
 const avg=average==null?'':`<line class=workTrendAvg x1=${left} x2=${W-right} y1="${y(average).toFixed(1)}" y2="${y(average).toFixed(1)}"></line><text class=workTrendAvgText x=${W-right} y="${(y(average)-5).toFixed(1)}" text-anchor=end>${esc(averageText)}</text>`;
 const label=point=>daily?workMonthName(point.week,{year:false}):`Week of ${workMonthName(point.week,{year:false})}`;
 const dots=points.map((point,index)=>`<circle class="workPoint${point.few_samples?' few':''}" cx="${x(index).toFixed(1)}" cy="${y(value(point)).toFixed(1)}" r=4><title>${esc(`${label(point)} · ${tip(point)}${point.few_samples?' (few samples)':''}`)}</title></circle>`).join('');
 svg.innerHTML=`<defs><linearGradient id=${fill} x1=0 y1=0 x2=0 y2=1><stop offset=0 style="stop-color:var(--signal);stop-opacity:.28"/><stop offset=1 style="stop-color:var(--signal);stop-opacity:0"/></linearGradient></defs>${grid}${area}<path class=workLine d="${path}"></path>${dots}${avg}<text class=workAxisText x=${left} y=${H-6}>${esc(workMonthName(points[0].week,{year:false}))}</text><text class=workAxisText x=${W-right} y=${H-6} text-anchor=end>${esc(workMonthName(points[points.length-1].week,{year:false}))}</text>`;
}
function renderWorkRework(insights){
 const rework=insights?.rework||{},select=$('w-rework-model'),series=rework.weekly_by_model||[];
 const keys=series.map(item=>`${item.model}␟${item.runtime}`);
 if(select.value&&!keys.includes(select.value))select.value='';
 const current=select.value;
 select.innerHTML='<option value="">All models</option>'+series.map((item,index)=>`<option value="${esc(keys[index])}">${esc(`${item.model} · ${item.runtime}`)}</option>`).join('');
 select.value=current;
 const chosen=current?series[keys.indexOf(current)]:null,weeks=(chosen?chosen.weeks:rework.weekly||[]).slice(rework.grain==='day'?-31:-26);
 const samples=weeks.reduce((sum,week)=>sum+week.samples,0),hits=weeks.reduce((sum,week)=>sum+week.rate*week.samples,0),daily=rework.grain==='day';
 const average=samples?hits/samples:0;
 $('w-rework-note').textContent=weeks.length?`${workPercent(average)} of ${samples.toLocaleString()} follow-ups · ${daily?'daily':'weekly'}`:'Waiting for labeled follow-ups';
 drawWorkTrend($('w-rework-trend'),weeks,{value:week=>week.rate,axis:v=>`${Math.round(v*100)}%`,tip:week=>`${workPercent(week.rate)} of ${week.samples} turns`,
  average,averageText:`avg ${workPercent(average)}`,empty:'No labeled follow-ups in this period yet',floor:.1,daily});
}
function workAgentRatio(value){return value==null?'--':`${value.toLocaleString(undefined,{maximumFractionDigits:1})} : 1`;}
function renderWorkAgentTrend(insights){
 const models=insights?.breakdown?.models||[],select=$('w-agent-model'),keys=models.map(item=>`${item.model}␟${item.runtime}`);
 if(select.value&&!keys.includes(select.value))select.value='';
 const current=select.value;
 select.innerHTML='<option value="">All models</option>'+models.map((item,index)=>`<option value="${esc(keys[index])}">${esc(`${item.model} · ${item.runtime}`)}</option>`).join('');
 select.value=current;
 const chosen=current?models[keys.indexOf(current)]:null;
 const trend=(chosen?chosen.agent_trend:insights?.breakdown?.agent_trend)||{},daily=trend.grain==='day',points=(trend.points||[]).slice(daily?-31:-26);
 const human=points.reduce((sum,point)=>sum+point.human_turns,0),agent=points.reduce((sum,point)=>sum+point.agent_turns,0),average=human?agent/human:null;
 $('w-agent-note').textContent=points.length?`${workAgentRatio(average)} over ${human.toLocaleString()} human turns · ${daily?'daily':'weekly'}`:'Waiting for requests with model calls';
 drawWorkTrend($('w-agent-trend'),points,{value:point=>point.agent_per_human,axis:v=>v.toLocaleString(undefined,{maximumFractionDigits:0}),
  tip:point=>`${workAgentRatio(point.agent_per_human)} · ${point.agent_turns.toLocaleString()} agent / ${point.human_turns.toLocaleString()} human turns`,
  average,averageText:`avg ${workAgentRatio(average)}`,empty:'No requests with model calls in this period yet',floor:1,daily});
}
function openWorkSessions(filters,label){
 const query=new URLSearchParams({months:workMonths});
 if(workRuntime)query.set('runtime',workRuntime);
 const project=filters.project||workProject;if(project)query.set('project',project);
 Object.entries(filters).forEach(([key,value])=>{if(key!=='project'&&value!=null&&value!=='')query.set(key,value);});
 query.set('label',label||'Work');
 location.hash=`sessions-all?work=1&${query}`;
}
function renderWorkSizing(insights){
 const cells=insights?.right_sizing?.cells||[],grid=$('w-sizing');
 if(!insights?.right_sizing?.tiers_known){grid.innerHTML='<div class=workEmpty style="grid-column:1/-1">Model price tiers are unavailable for these sessions.</div>';return;}
 const tiers=['light','standard','premium'],groups=['routine','everyday','complex'];
 const head=`<div role=row style="display:contents"><div class=workSizingHead role=columnheader></div>${tiers.map(tier=>`<div class=workSizingHead role=columnheader>${esc(WORK_TIER_LABELS[tier])}</div>`).join('')}</div>`;
 grid.innerHTML=head+groups.map(group=>`<div role=row style="display:contents"><div class=workSizingRowHead role=rowheader>${group==='complex'?'Complex+':esc(WORK_COMPLEXITY_LABELS[group])}</div>${tiers.map(tier=>{
  const cell=cells.find(item=>item.complexity===group&&item.tier===tier)||{requests:0,spend:0};
  const flag=cell.flag==='possible_overspend'?'<span class=workFlag><span aria-hidden=true>▲</span>Possible overspend (estimate)</span>':cell.flag==='possible_false_economy'?'<span class=workFlag><span aria-hidden=true>▲</span>Possible false economy (estimate)</span>':'';
  const body=`<strong class=mono>${cell.requests?money(cell.spend):'--'}</strong><span>${cell.requests} request${cell.requests===1?'':'s'}</span>${cell.rework?`<span>Rework ${workRateText(cell.rework)}</span>`:''}${flag}`;
  return `<div class=workSizingCell role=cell data-flag="${esc(cell.flag||'')}">${cell.requests?`<button type=button class="workDrillBtn workSizingBtn" data-drill="${esc(JSON.stringify({complexity:group,tier}))}" data-drill-label="${esc(`${group==='complex'?'Complex+':WORK_COMPLEXITY_LABELS[group]} · ${WORK_TIER_LABELS[tier]}`)}">${body}</button>`:body}</div>`;
 }).join('')}</div>`).join('');
}
function renderWorkEffort(insights){
 const effort=insights?.right_sizing?.effort,table=$('w-effort-table');
 if(!effort||!(effort.efforts||[]).length){table.innerHTML='<tbody><tr><td><div class=workEmpty>No reasoning-effort data for these sessions.</div></td></tr></tbody>';return;}
 const groups=['routine','everyday','complex'],cell=(group,level)=>(effort.cells||[]).find(item=>item.complexity===group&&item.effort===level);
 table.innerHTML=`<thead><tr><th>Complexity</th>${effort.efforts.map(level=>`<th class=num>${esc(level)}</th>`).join('')}</tr></thead><tbody>${groups.map(group=>`<tr><td>${group==='complex'?'Complex+':esc(WORK_COMPLEXITY_LABELS[group])}</td>${effort.efforts.map(level=>{const item=cell(group,level);if(!item||!item.requests)return '<td class="num workMuted">--</td>';return `<td class="num mono workFitCell"><button type=button class=workDrillBtn data-drill="${esc(JSON.stringify({complexity:group,effort:level}))}" data-drill-label="${esc(`${group==='complex'?'Complex+':WORK_COMPLEXITY_LABELS[group]} · ${level} effort`)}">${money(item.spend)}${item.flag?'<span class=workFitBest style="color:var(--warn);border-color:rgb(var(--orange-rgb)/.5)">▲ Overthinking?</span>':''}<small>${item.requests} · rework ${item.rework?workRateText(item.rework):'--'}</small></button></td>`;}).join('')}</tr>`).join('')}</tbody>`;
}
const WORK_TIER_COLORS={light:'var(--w-tier-light)',standard:'var(--w-tier-standard)',premium:'var(--w-tier-premium)'};
const WORK_EFFORT_BANDS=[{key:'default',label:'Default',levels:['default'],note:'not recorded'},{key:'normal',label:'Low–medium',levels:['low','medium']},{key:'high',label:'High',levels:['high']},{key:'extra',label:'Extra high+',levels:['xhigh','max','ultra']}];
const WORK_EFFORT_BAND_COLORS={default:'var(--w-unclear)',normal:'var(--w-tier-light)',high:'var(--w-tier-standard)',extra:'var(--w-tier-premium)'};
const WORK_EFFORT_COLORS={low:'var(--w-e1)',medium:'var(--w-e2)',high:'var(--w-e3)',xhigh:'var(--w-e4)',max:'var(--w-e5)',ultra:'var(--w-e6)'};
const WORK_GROUP_LABELS={routine:'Routine',everyday:'Everyday',complex:'Complex+'};
function workMeter(rate,max,{few=false,color=''}={}){
 if(rate==null)return '<span class=workMuted>--</span>';
 return `<span class=workMeter data-few="${few}"${few?' title="Fewer than 20 labeled turns"':''}><i><b style="width:${(100*Math.min(1,rate/Math.max(1e-9,max))).toFixed(1)}%${color?`;--meter:${color}`:''}"></b></i><em class=mono>${workPercent(rate)}</em></span>`;
}
let workScoreSort={key:'spend',direction:'desc'};
const WORK_SCORE_COLUMNS=[['model','Model'],['tasks','Tasks'],['spend','Spend'],['pushback','Pushback'],['resolved_rate','Resolved'],['cost_per_resolved','Per resolved'],
 ['kinds','Best / weakest at','Kinds of work where this model drew the least (best) or most (weakest) pushback among models with 20+ labeled follow-ups and 3+ sessions. Sorts by how many it is best at.']];
function workScoreValue(row,key){
 if(key==='model')return `${row.model} ${row.runtime}`.toLowerCase();
 if(key==='pushback')return row.rework?row.rework.rate:null;
 if(key==='kinds')return (row.best_at||[]).length*100-(row.worst_at||[]).length;
 return row[key]??null;
}
function sortWorkScore(rows,sort=workScoreSort){
 const sign=sort.direction==='asc'?1:-1;
 return [...rows].sort((a,b)=>{const x=workScoreValue(a,sort.key),y=workScoreValue(b,sort.key);
  if(x==null&&y==null)return b.spend-a.spend;if(x==null)return 1;if(y==null)return -1;
  return (x<y?-1:x>y?1:0)*sign||b.spend-a.spend;});
}
function renderWorkScorecard(insights){
 const rows=insights?.model_scorecard||[],table=$('w-scorecard');
 if(!rows.length){table.innerHTML='<tbody><tr><td><div class=workEmpty>Waiting for sessions in this period.</div></td></tr></tbody>';return;}
 // Colors follow spend rank, so a model keeps its color however the table is sorted.
 const identities=rows.map(row=>`${row.model}::${row.runtime}`);
 const maxPush=Math.max(.05,...rows.map(row=>row.rework?.rate||0)),ranked=rows.filter(row=>row.cost_per_resolved!=null&&row.judged_tasks>=5&&(row.judged_sessions??0)>=3);
 const lowestCost=ranked.length>=2?Math.min(...ranked.map(row=>row.cost_per_resolved)):null;
 const kindChips=(label,list,tone)=>list&&list.length?`<div class=workScoreKinds><small>${label}</small>${list.map(kind=>`<span class="chip${tone?` ${tone}`:''}">${esc(WORK_TYPE_LABELS[kind]||kind)}</span>`).join('')}</div>`:'';
 const kinds=row=>kindChips('Best',row.best_at,'')+kindChips('Weakest',row.worst_at,'tone-warn')||'<span class=workMuted>--</span>';
 const head=WORK_SCORE_COLUMNS.map(([key,label,tip])=>{const active=workScoreSort.key===key;
  return `<th aria-sort="${active?(workScoreSort.direction==='asc'?'ascending':'descending'):'none'}"><button class=efficiencySort type=button data-work-score-sort=${key}${tip?` title="${esc(tip)}"`:''}>${esc(label)}<span class=efficiencySortMark aria-hidden=true>${active?(workScoreSort.direction==='asc'?'↑':'↓'):''}</span></button></th>`;}).join('');
 table.innerHTML=`<thead><tr>${head}</tr></thead><tbody>${sortWorkScore(rows).map(row=>{
  const few=!row.rework||row.rework.few_samples,best=lowestCost!=null&&ranked.includes(row)&&row.cost_per_resolved===lowestCost,thin=row.judged_tasks<5,identity=`${row.model}::${row.runtime}`;
  return `<tr><td><button type=button class=workDrillBtn data-drill="${esc(JSON.stringify({model:row.model,model_runtime:row.runtime}))}" data-drill-label="${esc(`${row.model} · ${row.runtime}`)}"><i class=workScoreDot style="--swatch:${modelColor(identity,identities)}"></i><span class=workScoreName><b>${esc(row.model)}</b><small>${esc(row.runtime)}${row.tier?` · ${esc(WORK_TIER_LABELS[row.tier]||row.tier)}`:''}</small></span></button></td><td><span class=mono>${row.tasks.toLocaleString()}</span></td><td><span class=mono>${money(row.spend)}</span></td><td><span>${row.rework?workMeter(row.rework.rate,maxPush,{few}):'<span class=workMuted>--</span>'}</span></td><td><span class=mono>${row.resolved_rate==null?'<span class=workMuted>--</span>':thin?`<span class=workMuted title="${esc(`${row.judged_tasks} judged task${row.judged_tasks===1?'':'s'}`)}">${workPercent(row.resolved_rate)} · few</span>`:workPercent(row.resolved_rate)}</span></td><td><span class=mono${best?' style="color:var(--good)"':''}>${row.cost_per_resolved==null?'<span class=workMuted>--</span>':money(row.cost_per_resolved)}${best?'<em class=workLowest>lowest</em>':''}</span></td><td>${kinds(row)}</td></tr>`;
 }).join('')}</tbody>`;
}
$('w-scorecard').addEventListener('click',event=>{const button=event.target.closest('[data-work-score-sort]');if(!button)return;event.stopPropagation();const key=button.dataset.workScoreSort;
 workScoreSort=workScoreSort.key===key?{key,direction:workScoreSort.direction==='desc'?'asc':'desc'}:{key,direction:key==='model'?'asc':'desc'};if(WORK)renderWorkScorecard(WORK.insights);});
function workMixRows(groups,levels,cellFor,colorFor,labelFor,drillFor,flagged){
 return groups.map(group=>{
  const cells=levels.map(level=>({level,cell:cellFor(group,level)})).filter(item=>item.cell&&item.cell.spend>0);
  const total=cells.reduce((sum,item)=>sum+item.cell.spend,0);
  if(!total)return `<div class=workMixRow><span class=workMixLabel>${esc(WORK_GROUP_LABELS[group])}</span><div class=workStack></div><span class="workMixTotal workMuted">--</span></div>`;
  return `<div class=workMixRow><span class=workMixLabel>${esc(WORK_GROUP_LABELS[group])}</span><div class=workStack>${cells.map(({level,cell})=>{const share=cell.spend/total;return `<button type=button style="flex:${share.toFixed(4)} 0 0;--seg:${colorFor(level)}" data-flag="${flagged(cell)}" data-drill="${esc(JSON.stringify(drillFor(group,level)))}" data-drill-label="${esc(`${WORK_GROUP_LABELS[group]} · ${labelFor(level)}`)}" title="${esc(`${WORK_GROUP_LABELS[group]} · ${labelFor(level)}: ${money(cell.spend)} · ${workPercent(share)} · ${cell.requests} request${cell.requests===1?'':'s'}${cell.rework?` · pushback ${workPercent(cell.rework.rate)}`:''}${flagged(cell)?` · ${workFlagReason(cell)}`:''}`)}" aria-label="${esc(`${WORK_GROUP_LABELS[group]}, ${labelFor(level)}, ${money(cell.spend)}, ${workPercent(share)} of spend`)}">${share>=.14?`<span>${workPercent(share)}</span>`:''}</button>`;}).join('')}</div><span class=workMixTotal>${money(total)}</span></div>`;
 }).join('');
}
function renderWorkMix(insights){
 const sizing=insights?.right_sizing||{},groups=['routine','everyday','complex'],tiers=['light','standard','premium'];
 const tierHost=$('w-tier-mix'),effortHost=$('w-effort-mix');
 if(!sizing.tiers_known){tierHost.innerHTML='<div class=workEmpty>Model price tiers are unavailable for these sessions.</div>';}
 else{
  const cell=(group,tier)=>(sizing.cells||[]).find(item=>item.complexity===group&&item.tier===tier);
  tierHost.innerHTML=workMixRows(groups,tiers,cell,tier=>WORK_TIER_COLORS[tier],tier=>WORK_TIER_LABELS[tier],(group,tier)=>({complexity:group,tier}),item=>Boolean(item.flag))+`<div class=workMixLegend>${tiers.map(tier=>`<span><i style="--swatch:${WORK_TIER_COLORS[tier]}"></i>${esc(WORK_TIER_LABELS[tier])}</span>`).join('')}<span><i class=flag></i>Premium on routine · light on complex</span></div>`;
 }
 const effort=sizing.effort||{},levels=effort.efforts||[];
 if(!levels.length){effortHost.innerHTML='<div class=workEmpty>No reasoning-effort data for these sessions.</div>';}
 else{
  // Effort levels are folded into readable groups (unrecorded ones into Default); the tables below keep every level.
  const bands=WORK_EFFORT_BANDS.map(band=>({...band,levels:band.levels.filter(level=>levels.includes(level))})).filter(band=>band.levels.length);
  const cell=(group,key)=>{
   const band=bands.find(item=>item.key===key),members=(effort.cells||[]).filter(item=>item.complexity===group&&band.levels.includes(item.effort));
   if(!members.length)return null;
   const samples=members.reduce((sum,item)=>sum+(item.rework?.samples||0),0),hits=members.reduce((sum,item)=>sum+(item.rework?(item.rework.rate*item.rework.samples):0),0);
   return {spend:members.reduce((sum,item)=>sum+item.spend,0),requests:members.reduce((sum,item)=>sum+item.requests,0),flag:members.some(item=>item.flag)?'possible_overthinking':'',rework:samples?{rate:hits/samples,samples}:null,levels:members.filter(item=>item.requests).map(item=>item.effort)};
  };
  effortHost.innerHTML=workMixRows(groups,bands.map(band=>band.key),cell,key=>WORK_EFFORT_BAND_COLORS[key],key=>bands.find(band=>band.key===key).label,(group,key)=>({complexity:group,effort:(cell(group,key)?.levels||[]).join(',')}),item=>Boolean(item.flag))+`<div class=workMixLegend>${bands.map(band=>`<span><i style="--swatch:${WORK_EFFORT_BAND_COLORS[band.key]}"></i>${esc(band.label)}<small>${esc(band.note||band.levels.join(', '))}</small></span>`).join('')}<span><i class="flag effort"></i>High effort on routine work</span></div>`;
 }
}
