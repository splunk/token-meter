
let GOALS=null,goalsLoading=null,goalsLoadedAt=0,goalDraft=null,goalPreviewRequest=0,goalsListModes={},goalModelRequest=0,goalModelTimer=0;
const GOAL_STATUS_LABELS={on_track:'On track',at_risk:'At risk',met:'Met',missed:'Missed',no_result:'No result',ended:'Ended'};
const GOAL_CONFIDENCE={complete:'Complete data',partial:'Partial data',estimated:'Estimated'};
const GOAL_METRIC_HELP={
 tokens:'Processed input plus output tokens, including cache and reasoning once. Usage volume is not a productivity or quality score. Set At most for a cap or At least for a milestone.',
 spend:'Estimated cost of agent work in the period. Lower it with cheaper models, less reasoning, and shorter sessions.',
 output_per_dollar:'Output tokens for each dollar spent. It rises with cheaper models and better cache use. More output is not always better work.',
 cost_per_1k_lines:'What 1,000 pushed lines cost in agent spend, in the repositories your sessions ran in. Lower means more code shipped per dollar. Delivery evidence, not a quality score. Needs at least 50 lines.',
 context_load:'Tokens the agent reads for each token it writes. Lower it by starting fresh sessions and trimming the context you load.',
 reasoning_ratio:'Share of output spent on reasoning. Lower it by choosing a lower reasoning effort when a task does not need it. Sessions from runtimes that do not report reasoning tokens are left out.',
 frontier_share:'Share of spend on frontier models. Lower it by using mid range or efficient models for routine work. Spend on unrecognized models is shown separately.',
 work_pushback:'Share of labeled follow-ups where your next message pushed back, for one kind of work or all, and all models or chosen ones. Lower means the agent got it right more often. Local-model labels, so an estimate; a result needs 20 labeled follow-ups.',
 agent_per_human:'Agent turns (model calls) per message you typed, kept inside a range, for one kind of work or all, and all models or chosen ones. It counts only while pushback stays under the pushback goal with the same kind of work and models, so long runs that needed correcting never count as progress.',
};
const GOAL_PUSHED_SCOPE='All projects includes discovered repositories with both spend and measured Git coverage, across all apps and models. Lines pushed counts additions plus deletions. Sessions outside repository folders are not attributed.';
function goalRepoText(count,names){
 if(!count)return 'no repositories';
 const shown=(names||[]).slice(0,3).join(', '),more=count-Math.min(count,3);
 return `${count} ${count===1?'repository':'repositories'}${shown?` (${shown}${more>0?`, +${more} more`:''})`:''}`;
}
function goalCoverageText(share){return share===null||share===undefined?'':`${share<1&&share>0?'under 1':Math.round(share)}% of your agent spend`;}
function goalUnit(metric){return GOALS?.metrics?.[metric]?.unit||'';}
function goalLabel(metric){return GOALS?.metrics?.[metric]?.label||metric;}
function goalIsWork(metric){return (GOALS?.metrics?.[metric]?.scopes||[]).includes('work_type');}
function goalRepeatText(g){
 if(g.period==='week')return 'weekly';
 if(g.period==='month')return 'monthly';
 const days=Math.round((Date.parse(g.end+'T12:00:00Z')-Date.parse(g.start+'T12:00:00Z'))/864e5)+1;
 if(!Number.isSafeInteger(days)||days<1)return 'each selected period';
 return days===1?'daily':`every ${days} days`;
}
function goalTargetText(t,metric){
 if(t.comparison!=='between')return `${t.comparison==='at_most'?'≤':'≥'} ${goalValue(t.target,metric)}`;
 // A ratio range shares its ": 1", so it reads "32–48 : 1" rather than repeating it.
 const ratio=v=>Number(v).toLocaleString(undefined,{maximumFractionDigits:1});
 return goalUnit(metric)==='agent_ratio'?`${ratio(t.target)}–${ratio(t.target_high)} : 1`:`${goalValue(t.target,metric)} – ${goalValue(t.target_high,metric)}`;
}
function goalValue(value,metric){
 const v=Number(value);
 if(value===null||value===undefined||!Number.isFinite(v))return '--';
 const unit=goalUnit(metric);
 if(unit==='usd')return money(v);
 if(unit==='tokens')return `${compactNumber(v)} tokens`;
 if(unit==='tokens_per_usd')return `${compactNumber(v)} / $`;
 if(unit==='usd_per_1k_lines')return `${money(v)} / 1K lines`;
 if(unit==='percent')return `${v.toFixed(1)}%`;
 if(unit==='agent_ratio')return `${v.toLocaleString(undefined,{maximumFractionDigits:1})} : 1`;
 return efficiencyDecimal(v);
}
const GOAL_HOMES={spend:{route:'spend',section:'spend-goals',back:'Spend'},tokens:{route:'spend',section:'spend-goals',back:'Spend'},cost_per_1k_lines:{route:'git',section:'git-goals',back:'Git'},work_pushback:{route:'work',section:'work-goals',back:'Work'},agent_per_human:{route:'work',section:'work-goals',back:'Work'}};
function goalHome(metric){return GOAL_HOMES[metric]||{route:'efficiency-goals',section:'efficiency-goals',back:'goals'};}
function goalTitle(g){return g.title||`${goalLabel(g.metric)} for ${g.scope_label}`;}
function goalIsCurrent(g){return !g.ended_at&&g.end>=(GOALS?.today||'');}
function goalNotice(text,warn=false){
 document.querySelectorAll('.goalsNotice').forEach(el=>{
 el.textContent=text||'';
 el.hidden=!text;el.classList.toggle('tone-warn',warn);});
}
function goalHistory(g){
 if(!(g.history||[]).length)return '';
 const label=h=>new Date(h.start+'T12:00:00').toLocaleDateString(undefined,{month:'short',day:'numeric'});
 return `<div class=goalHistory aria-label="Previous periods">${g.history.map(h=>`<button type=button class="chip goalStatus ${esc(h.status)}" data-goal-period="${esc(h.start)}" data-goal-id="${esc(g.id)}" title="${esc(h.start+' to '+h.end+' · '+goalValue(h.value,g.metric))}">${esc(label(h))} · ${esc(GOAL_STATUS_LABELS[h.status]||h.status)}</button>`).join('')}</div>`;
}
function goalGuardrail(g){
 const r=g.guardrail;
 if(!r||r.value===null||r.value===undefined)return '';
 const open=goalIsCurrent(g)&&r.metric==='spend';
 return `${goalLabel(r.metric)} ${goalValue(r.value,r.metric)}${open?' so far':''}${r.baseline!==null&&r.baseline!==undefined?` (${(r.baseline_label||'last period').toLowerCase()} ${goalValue(r.baseline,r.metric)})`:''}`;
}
function goalNotes(g){
 const m=g.measurement||{};
 return [g.metric==='cost_per_1k_lines'?`Across ${goalRepoText(m.projects||0,m.project_names)}`:'',
  g.metric==='cost_per_1k_lines'&&m.observations?`${f(m.added)} added · ${f(m.deleted)} deleted`:'',
  g.metric==='cost_per_1k_lines'&&m.counted_share!==null&&m.counted_share!==undefined?`Counts ${goalCoverageText(m.counted_share)}`:'',
  m.unclassified_share?`Unrecognized models ${m.unclassified_share.toFixed(1)}% of spend`:'',
  m.reasoning_excluded?`${m.reasoning_excluded} session${m.reasoning_excluded===1?'':'s'} without reported reasoning left out`:'',
  m.cost_excluded?`${m.cost_excluded} session${m.cost_excluded===1?'':'s'} without reported cost left out`:'',
  g.budget_reference?`Monthly budget ${money(g.budget_reference)}`:'',
  m.guard?`Pushback ${goalValue(m.guard.value,'work_pushback')} vs its goal ≤ ${goalValue(m.guard.target,'work_pushback')}`:''].filter(Boolean);
}
function goalDateRange(g){
 const s=new Date(g.start+'T12:00:00'),e=new Date(g.end+'T12:00:00'),year=new Date((GOALS?.today||g.end)+'T12:00:00').getFullYear();
 const opts=d=>d.getFullYear()===year?{month:'short',day:'numeric'}:{month:'short',day:'numeric',year:'numeric'};
 const end=s.getMonth()===e.getMonth()&&s.getFullYear()===e.getFullYear()?String(e.getDate()):e.toLocaleDateString(undefined,opts(e));
 return `${s.toLocaleDateString(undefined,s.getFullYear()===e.getFullYear()&&s.getFullYear()===year?{month:'short',day:'numeric'}:opts(s))} – ${end}`;
}
function goalHistoryDots(g){
 if(!(g.history||[]).length)return '';
 const label=h=>new Date(h.start+'T12:00:00').toLocaleDateString(undefined,{month:'short',day:'numeric'});
 return `<span class=goalDots>${g.history.map(h=>{const t=`${label(h)}: ${GOAL_STATUS_LABELS[h.status]||h.status}, ${goalValue(h.value,g.metric)}`;return `<i class="goalStatus ${esc(h.status)}" role=img aria-label="${esc(t)}" title="${esc(t)}"></i>`;}).join('')}</span>`;
}
function goalCard(g){
 const m=g.measurement||{},value=m.value,range=g.comparison==='between',target=range?(Number(g.target)+Number(g.target_high))/2:Number(g.target),hasValue=value!==null&&value!==undefined;
 const top=Math.max(hasValue?Number(value):0,target,range?Number(g.target_high):0,Number(m.projected)||0)*1.15||1;
 const at=hasValue?Math.min(100,Number(value)/top*100):0,typ=Math.min(100,target/top*100);
 const tone=g.status==='at_risk'||g.status==='missed'?'high':'';
 const detail=g.status_reason||(m.projected!==undefined?`On pace for ${goalValue(m.projected,g.metric)}`:'');
 const targetText=goalTargetText(g,g.metric);
 const progressLabel=range?`${goalValue(value,g.metric)}, target ${targetText}`:`${goalValue(value,g.metric)} of ${g.comparison==='at_most'?'at most':'at least'} ${goalValue(target,g.metric)}`;
 const confidence=m.confidence&&m.confidence!=='complete'?GOAL_CONFIDENCE[m.confidence]||'':'';
 const meta=[g.title?g.scope_label:'',goalDateRange(g),g.repeat?`Repeats ${goalRepeatText(g)}`:''].filter(Boolean).join(' · ');
 const menu=`<details class=goalMenu><summary class=tbtn aria-label="More actions for ${esc(goalTitle(g))}">⋯</summary><div class="modelPickerMenu goalMenuList" role=group><button type=button data-goal-duplicate="${esc(g.id)}">Duplicate</button>${goalIsCurrent(g)?`<button type=button data-goal-end="${esc(g.id)}">End goal</button>`:''}<button type=button class=goalMenuDanger data-goal-delete="${esc(g.id)}">Delete</button></div></details>`;
 return `<article class="card goalCard" data-status="${esc(g.status)}"><div class=goalCardHead><h4><a class=goalTitleLink href="#efficiency-goals/${esc(g.id)}">${esc(goalTitle(g))}</a></h4><span class="chip goalStatus ${esc(g.status)}">${esc(GOAL_STATUS_LABELS[g.status]||g.status)}</span></div>`
  +`<p class=goalMeta>${esc(meta)}</p>`
  +(g.legacy_direction?'<p class=goalsHelp>Saved target uses an older direction. New targets use At most.</p>':'')
  +`<div class=goalNumbers><strong class=mono>${esc(goalValue(value,g.metric))}</strong><span>Target ${esc(targetText)}</span>${confidence?`<span class=goalConfidence title="${esc(m.reason||'')}">${esc(confidence)}</span>`:''}</div>`
  +`<span class=readoutScale role=img aria-label="${esc(progressLabel)}" data-tone="${tone}" style="--at:${at.toFixed(1)}%;--typ:${typ.toFixed(1)}%"><i class=readoutScaleFill></i><i class=readoutScaleTypical></i><i class=readoutScaleMark></i></span>`
  +`<div class=goalFoot>${detail?`<span class=readoutCaption>${esc(detail)}</span>`:'<span></span>'}${goalHistoryDots(g)}<span class=goalActions><button class=tbtn type=button data-goal-edit="${esc(g.id)}">Edit</button>${menu}</span></div>`
  +`</article>`;
}
const GOAL_SECTIONS={'spend-goals':'spend','git-goals':'delivery cost','efficiency-goals':'efficiency','work-goals':'work'};
function renderGoalSection(section){
 const root=$(section),mode=goalsListModes[section]||'current',noun=GOAL_SECTIONS[section];
 const goals=(GOALS?.goals||[]).filter(g=>goalHome(g.metric).section===section),current=goals.filter(goalIsCurrent),previous=goals.filter(g=>!goalIsCurrent(g));
 root.querySelector('[data-goals-current]').innerHTML=current.length?current.map(goalCard).join(''):`<p class="emptyState goalsEmpty">No current ${esc(noun)} goals.</p>`;
 root.querySelector('[data-goals-previous]').innerHTML=previous.length?previous.map(goalCard).join(''):`<p class="emptyState goalsEmpty">No previous ${esc(noun)} goals.</p>`;
 root.querySelector('[data-goals-current]').hidden=mode!=='current';
 root.querySelector('[data-goals-previous]').hidden=mode!=='previous';
 root.querySelector('[data-goals-toggle]').textContent=mode==='previous'?'Current goals':`Previous goals${previous.length?` (${previous.length})`:''}`;
}
function renderGoals(){
 if(!GOALS)return;
 if(GOALS.dropped&&document.querySelector('.goalsNotice')?.hidden)goalNotice(`${GOALS.dropped} goal${GOALS.dropped===1?'':'s'} from an earlier version can no longer be shown and will be removed the next time you save a goal.`);
 Object.keys(GOAL_SECTIONS).forEach(renderGoalSection);
}
let goalSeedSent=false;
async function seedDefaultGoals(){
 if(goalSeedSent||!GOALS?.seed_pending||!agentActionToken())return;
 goalSeedSent=true;
 const result=await postGoalAction({operation:'seed'});
 if(result.ok)acceptGoalResult(result);
}
async function loadGoals(force=false){
 if(!force&&GOALS&&Date.now()-goalsLoadedAt<30000){renderGoals();return GOALS;}
 if(goalsLoading)return goalsLoading;
 goalsLoading=(async()=>{
  try{
   const data=await (await fetch('/goals',{cache:'no-store'})).json();
   if(data.ok){GOALS=data;goalsLoadedAt=Date.now();renderGoals();seedDefaultGoals();}
   else goalNotice(data.error||'Goals are unavailable right now.',true);
  }catch{goalNotice('Goals are unavailable right now.',true);}
  finally{goalsLoading=null;}
  return GOALS;
 })();
 return goalsLoading;
}
function goalSelectedScope(){
 const work=goalIsWork($('goals-metric').value),chosen=[...(goalDraft?.modelKeys||[])].sort();
 if(work)return {kind:'work_type',key:$('goals-work-type').value,...(chosen.length?{keys:chosen}:{})};
 const keys=[...(goalDraft?.modelKeys||[])].sort();
 if(keys.length===1)return {kind:'model',key:keys[0]};
 if(keys.length)return {kind:'models',key:'',keys};
 return $('goals-app').value?{kind:'agent',key:$('goals-app').value}:{kind:'all',key:''};
}
function renderGoalModelPicker(){
 if(!goalDraft)return;
 const keys=goalDraft.modelKeys||[],enabled=(GOALS.metrics?.[$('goals-metric').value]?.scopes||[]).includes('model')||goalIsWork($('goals-metric').value),picker=$('goals-model-picker'),summary=picker.querySelector('summary');
 summary.setAttribute('aria-disabled',String(!enabled));summary.tabIndex=enabled?0:-1;
 if(!enabled)picker.open=false;
 $('goals-model-summary').textContent=keys.length===0?'All models':keys.length===1?(goalDraft.models.find(m=>m.key===keys[0])?.label||'Model unavailable'):`${keys.length} models selected`;
 const choices=goalDraft.models||[],focused=document.activeElement?.closest('[data-goal-model]')?.dataset.goalModel;
 $('goals-model-options').innerHTML=`<label class="modelOption all"><input type=checkbox data-goal-model=all ${keys.length?'':'checked'} ${enabled?'':'disabled'}><span>All models</span></label>`+choices.map(m=>`<label class=modelOption title="${esc(m.label)}"><input type=checkbox data-goal-model="${esc(m.key)}" ${keys.includes(m.key)?'checked':''} ${enabled?'':'disabled'}><span>${esc(m.label)}</span></label>`).join('');
 if(focused)$('goals-model-options').querySelector(`[data-goal-model="${focused}"]`)?.focus();
 $('goals-model-search-field').hidden=!enabled;
 if(picker.open)requestAnimationFrame(positionGoalModelPicker);
}
function fillGoalScopes(metric,scope={kind:'all',key:''}){
 const work=goalIsWork(metric);
 $('goals-app-field').hidden=work;$('goals-work-field').hidden=!work;
 $('goals-work-type').innerHTML=(GOALS.work_types||[]).map(w=>`<option value="${esc(w.id)}">${esc(w.label)}</option>`).join('');
 if(scope.kind==='work_type'&&scope.key)$('goals-work-type').value=scope.key;
 const scopes=GOALS.metrics?.[metric]?.scopes||['all'],keys=scope.kind==='models'||scope.kind==='work_type'?scope.keys||[]:scope.kind==='model'?[scope.key]:[],known=[...(GOALS.models||[]),...(goalDraft.models||[])],model=known.find(m=>m.key===scope.key),app=scope.kind==='agent'?scope.key:scope.kind==='model'?model?.runtime||'':'';
 $('goals-app').innerHTML='<option value="">All apps</option>'+(scopes.includes('agent')?(GOALS.agents||[]).map(a=>`<option value="${esc(a.id)}">${esc(a.label)}</option>`).join(''):'');
 $('goals-app').value=app;$('goals-app').disabled=!scopes.includes('agent');
 goalDraft.modelKeys=scopes.includes('model')||work?[...keys]:[];
 goalDraft.models=(GOALS.models||[]).filter(m=>!app||m.runtime===app);
 for(const key of goalDraft.modelKeys)if(!goalDraft.models.some(m=>m.key===key))goalDraft.models.push(known.find(m=>m.key===key)||{key,runtime:'',label:'Model unavailable — remove to choose another'});
 $('goals-model-search').value='';renderGoalModelPicker();refreshGoalModels();
}
async function refreshGoalModels(){
 const request=++goalModelRequest;
 if(!goalDraft||!((GOALS.metrics?.[$('goals-metric').value]?.scopes||[]).includes('model')||goalIsWork($('goals-metric').value)))return;
 const params=new URLSearchParams({models:'1',runtime:$('goals-app').value,query:$('goals-model-search').value});
 $('goals-model-note').textContent='Reading observed models…';
 let data;try{data=await(await fetch(`/goals?${params}`,{cache:'no-store'})).json();}catch{data={ok:false};}
 if(request!==goalModelRequest||!goalDraft)return;
 if(!data.ok){$('goals-model-note').textContent='Model choices are unavailable. Existing selection is preserved.';return;}
 const previous=goalDraft.models||[],choices=[...(data.models||[])];
 for(const key of goalDraft.modelKeys||[])if(!choices.some(m=>m.key===key))choices.push(previous.find(m=>m.key===key)||{key,runtime:'',label:'Model unavailable — remove to choose another'});
 goalDraft.models=choices;renderGoalModelPicker();
 $('goals-model-note').textContent=`${data.models.length} of ${data.total} models, by recent spend · up to ${GOALS.max_selected_models||32}`;
}
function positionGoalModelPicker(){
 const picker=$('goals-model-picker'),menu=$('goals-model-menu');if(!picker.open){picker.classList.remove('opensUp');menu.style.maxHeight='';return;}
 const rect=picker.querySelector('summary').getBoundingClientRect(),margin=12,above=Math.max(0,rect.top-margin),below=Math.max(0,window.innerHeight-rect.bottom-margin),preferred=Math.min(360,menu.scrollHeight||360),opensUp=below<preferred&&above>below;
 picker.classList.toggle('opensUp',opensUp);menu.style.maxHeight=`${Math.min(360,opensUp?above:below)}px`;
}
function goalPeriodFields(){
 const choice=$('goals-period').value;
 if(choice==='week'||choice==='month')return {period:choice,start:goalDraft?.anchor||'',end:''};
 if(choice==='custom')return {period:'custom',start:$('goals-start').value,end:$('goals-end').value};
 const count={today:1,next7:7,next30:30,next90:90}[choice]||1;
 return {period:'custom',start:GOALS.today,end:spendShiftDay(GOALS.today,count-1)};
}
function openGoalForm(preset={},duplicate=false,section=goalHome(preset.metric).section){
 if(!GOALS)return;
 const metrics=Object.entries(GOALS.metrics).filter(([id])=>goalHome(id).section===section),metric=preset.metric||metrics[0]?.[0]||'spend';
 goalDraft={id:duplicate?'':preset.id||'',option:'',wantedOption:duplicate?'custom':preset.target_option||'',autoTitle:preset.id?'':(preset.title||''),saved:preset.id?{comparison:preset.comparison,target:preset.target,target_high:preset.target_high}:null,anchor:!duplicate&&preset.id&&preset.period!=='custom'?preset.start:'',returnMode:goalsListModes[section]||'current',section};
 goalDraft.legacyDirection=!duplicate&&!!preset.legacy_direction;
 $('goals-metric').disabled=metrics.length<2;
 $('goals-metric').innerHTML=metrics.map(([id,m])=>`<option value="${esc(id)}">${esc(m.label)}</option>`).join('');
 $('goals-metric').value=metric;
 $('goals-metric-options').innerHTML=metrics.map(([id,m])=>`<button class=goalOption type=button data-goal-metric="${esc(id)}" title="${esc(GOAL_METRIC_HELP[id]||'')}">${esc(m.label)}</button>`).join('');
 fillGoalScopes(metric,preset.scope);
 $('goals-period').value=preset.period||'month';
 $('goals-repeat').checked=!!preset.repeat;
 const today=GOALS.today,length=duplicate&&preset.period==='custom'?Math.round((Date.parse(preset.end+'T12:00:00Z')-Date.parse(preset.start+'T12:00:00Z'))/864e5):29;
 $('goals-start').value=duplicate?today:preset.start||today;$('goals-end').value=duplicate?spendShiftDay(today,length):preset.end||spendShiftDay(today,29);
 $('goals-title').value=preset.title||'';
 $('goals-comparison').value='';$('goals-target').value='';$('goals-target-high').value='';
 $('goals-form-heading').textContent=duplicate?'Duplicate goal':preset.id?'Edit goal':'New goal';
 goalFormError('');
 if(!$('goals-dialog').open)$('goals-dialog').showModal();
 updateGoalFormFields();
 refreshGoalPreview();
 $('goals-form-heading').focus({preventScroll:true});
}
function clearAutoTitle(){if(goalDraft?.autoTitle&&$('goals-title').value===goalDraft.autoTitle){$('goals-title').value='';goalDraft.autoTitle='';}}
function updateGoalFormFields(){
 const metric=$('goals-metric').value,{period}=goalPeriodFields(),locked=$('goals-metric').disabled;
 $('goals-metric-options').querySelectorAll('[data-goal-metric]').forEach(button=>{button.setAttribute('aria-pressed',String(button.dataset.goalMetric===metric));button.disabled=locked&&button.dataset.goalMetric!==metric;});
 $('goals-metric').title=`${GOAL_METRIC_HELP[metric]||''}${metric==='cost_per_1k_lines'?` ${GOAL_PUSHED_SCOPE}`:''}`;
 $('goals-dates').hidden=$('goals-period').value!=='custom';
 $('goals-repeat-field').hidden=false;
 const dates=goalPeriodFields();
 $('goals-repeat-unit').textContent=goalRepeatText({period,start:dates.start||GOALS.today,end:dates.end||GOALS.today});
 $('goals-target').max=goalUnit(metric)==='percent'?'100':goalUnit(metric)==='tokens'?String(Number.MAX_SAFE_INTEGER):'';
 $('goals-target').step=goalUnit(metric)==='tokens'?'1':'any';
 renderGoalPreview();
}
function goalPreviewScope(metric){
 if(metric==='cost_per_1k_lines')return 'All projects';
 if(goalIsWork(metric))return `${$('goals-work-type').selectedOptions[0]?.textContent||'All kinds of work'}${goalDraft?.modelKeys?.length?` · ${$('goals-model-summary').textContent}`:''}`;
 const app=$('goals-app'),appLabel=app.value?app.selectedOptions[0]?.textContent||'':'All apps';
 return goalDraft?.modelKeys?.length?`${app.value?`${appLabel} · `:''}${$('goals-model-summary').textContent}`:appLabel;
}
function goalDraftTarget(){
 if(goalDraft?.option==='custom'){
  const comparison=$('goals-comparison').value,raw=$('goals-target').value,target=Number(raw),high=Number($('goals-target-high').value);
  if(comparison==='between')return raw!==''&&$('goals-target-high').value!==''&&Number.isFinite(target)&&Number.isFinite(high)?{comparison,target,target_high:high,label:'Custom'}:null;
  return comparison&&raw!==''&&Number.isFinite(target)?{comparison,target,label:'Custom'}:null;
 }
 return (goalDraft?.options||[]).find(o=>o.id===goalDraft.option)||null;
}
function renderGoalPreview(){
 if(!goalDraft)return;
 const metric=$('goals-metric').value,p=goalDraft.preview||{loading:true},b=p.baseline||{},t=goalDraftTarget(),{period}=goalPeriodFields(),{kind,key}=goalSelectedScope();
 const now=p.ok?b.value:null,hasNow=now!==null&&now!==undefined;
 const scope=goalPreviewScope(metric),auto=`${goalLabel(metric)} for ${scope}`,title=$('goals-title').value.trim()||auto;
 $('goals-title').placeholder=auto;
 const repeat=$('goals-repeat').checked;
 const meta=[title!==auto?scope:'',p.start&&p.end?goalDateRange({start:p.start,end:p.end}):'',repeat&&p.start&&p.end?`Repeats ${goalRepeatText({period,start:p.start,end:p.end})}; next starts ${spendShiftDay(p.end,1)}`:''].filter(Boolean).join(' · ');
 const cumulative=GOALS.metrics?.[metric]?.cumulative;
 const nowNote=p.loading?'Checking…':!p.ok?(p.error||'Unavailable'):hasNow?[`${b.label||'Previous period'}${cumulative?(b.source==='so_far'?', projected':', scaled'):''}`,b.estimated?'estimated':'',b.complete===false?'partial':''].filter(Boolean).join(' · '):'No data yet';
 const aim=t?(t.comparison==='between'?(t.target+t.target_high)/2:t.target):null,isPercent=goalUnit(metric)==='percent',change=hasNow&&t?(isPercent?aim-now:now>0?(aim-now)/now*100:null):null;
 const readout=(label,value,note,extra='')=>`<div class="metric goalPreviewReadout"${extra}><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></div>`;
 const help=$('goals-metric').title,lead=(GOAL_METRIC_HELP[metric]||'').split(/(?<=\.)\s/)[0];
 const allocations=GOALS?.budget_allocations||{},allocation=metric!=='spend'||period!=='month'?0:kind==='all'?Object.values(allocations).reduce((a,v)=>a+v,0):kind==='agent'?Number(allocations[key]||0):0;
 const notes=[];
 if(allocation>0)notes.push(`Your ${money(allocation)} monthly budget keeps its own alerts`);
 if(metric==='cost_per_1k_lines'&&p.ok){const coverage=goalCoverageText(b.counted_share);notes.push(`Across ${goalRepoText(b.project_count||0,b.projects)}${coverage?` · covers ${coverage}`:''}`);if(!b.project_count||b.counted_share!==null&&b.counted_share!==undefined&&b.counted_share<50)notes.push('Low coverage: check Repository evidence after saving');}
 if(p.ok&&!hasNow)notes.push('No previous usage in scope. Choose Custom to set a target.');
 if(p.suggested_range)notes.push(`Suggested from your previous usage: ${goalValue(p.suggested_range.low,metric)} – ${goalValue(p.suggested_range.high,metric)}. An improvement experiment, not a quality score.`);
 else if(p.ok&&hasNow&&!goalIsWork(metric))notes.push('Hold this level or choose a smaller custom change.');
 if(goalDraft.legacyDirection)notes.push('This saved target uses an older direction. Changing its number or choosing a target applies the current direction.');
 if(metric==='agent_per_human'){// Same rule as the server: the newest pushback goal, not ended, with the same scope, whose dates overlap.
const models=JSON.stringify(goalSelectedScope().keys||[]),ceiling=(GOALS?.goals||[]).filter(g=>g.metric==='work_pushback'&&!g.ended_at&&g.scope?.key===key&&JSON.stringify(g.scope?.keys||[])===models&&(!p.start||g.series_start<=p.end&&(g.repeat||g.end>=p.start))).sort((a,b)=>String(b.created_at).localeCompare(String(a.created_at)))[0];notes.push(ceiling?`Counts while pushback stays ${goalTargetText(ceiling,'work_pushback')} (its pushback goal)`:'Counts only with a pushback goal for the same kind of work and models: add one too');}
 $('goals-preview').innerHTML=`<span class=goalPreviewKicker>Preview</span><div><h4>${esc(title)}</h4>${meta?`<p class=goalMeta>${esc(meta)}</p>`:''}</div>`
  +`<div class=goalPreviewLedger>${readout('Reference',hasNow?goalValue(now,metric):'--',nowNote,!hasNow&&b.reason?` title="${esc(b.reason)}"`:'')}${readout('Target',t?goalTargetText(t,metric).replace(/ : 1$/,''):'--',t?t.label:'Choose below')}${readout('Change',change===null?'--':`${change>0?'+':change<0?'−':''}${Math.abs(change).toFixed(1)}${isPercent?'':'%'}`,isPercent?'percentage points vs reference':'vs reference')}</div>`
  +(hasNow&&t?readoutScaleHtml({value:now,typical:aim,label:`Now ${goalValue(now,metric)}, target ${goalTargetText(t,metric)}`,toned:false}):'')
  +(lead?`<span class=readoutCaption title="${esc(help)}">${esc(lead)}</span>`:'')
  +notes.map(n=>`<span class="goalsHelp goalPreviewNote">${esc(n)}</span>`).join('')
  +(['output_per_dollar','context_load','reasoning_ratio','frontier_share'].includes(metric)?`<a class=goalsHelp href="#learn-efficiency-${metric}" data-goal-learn>How ${esc(goalLabel(metric))} works</a>`:'');
}
async function refreshGoalPreview(){
 const request=++goalPreviewRequest,metric=$('goals-metric').value,{kind,key,keys}=goalSelectedScope(),{period,start,end}=goalPeriodFields();
 const params=new URLSearchParams({preview:'1',metric,scope_kind:kind,scope_key:key||'',period});
 if(keys?.length)params.set('scope_keys',JSON.stringify(keys));
 goalDraft.start='';goalDraft.end='';$('goals-save').disabled=true;
 if(period==='custom'){params.set('start',start);params.set('end',end);}
 else if(goalDraft.anchor)params.set('start',goalDraft.anchor);
 goalDraft.preview={loading:true};renderGoalPreview();
 let data;
 try{data=await (await fetch(`/goals?${params}`,{cache:'no-store'})).json();}catch{data={ok:false,error:'The previous period is unavailable.'};}
 if(request!==goalPreviewRequest||!goalDraft)return;
 goalDraft.options=data.ok?data.options:[];
 goalDraft.start=data.start||'';goalDraft.end=data.end||'';
 $('goals-save').disabled=false;
 const b=data.baseline||{};
 goalDraft.current=data.ok?b.value:null;
 goalDraft.preview=data;
 if(goalDraft.wantedOption){
  const wanted=goalDraft.options.find(o=>o.id===goalDraft.wantedOption),saved=goalDraft.saved;
  // Editing keeps the saved number; an option is reselected only if it still produces that number.
  if(wanted&&(!saved||wanted.target===saved.target&&wanted.comparison===saved.comparison&&wanted.target_high===saved.target_high))goalDraft.option=wanted.id;
  else if(saved){goalDraft.option='custom';$('goals-comparison').value=saved.comparison;$('goals-target').value=saved.target;$('goals-target-high').value=saved.target_high??'';}
  goalDraft.wantedOption='';
 }
 if(goalDraft.option&&goalDraft.option!=='custom'&&!goalDraft.options.some(o=>o.id===goalDraft.option))goalDraft.option='';
 renderGoalOptions();
}
function renderGoalOptions(focus=false){
 const metric=$('goals-metric').value,direction=goalDraft.legacyDirection&&goalDraft.option==='custom'?goalDraft.saved.comparison:GOALS.metrics?.[metric]?.direction||'';
 const chip=(id,label,detail)=>`<button class=goalOption type=button aria-pressed=${goalDraft.option===id?'true':'false'} data-goal-option="${esc(id)}">${esc(label)}<small>${esc(detail)}</small></button>`;
 $('goals-options').innerHTML=goalDraft.options.map(o=>chip(o.id,o.label,goalTargetText(o,metric))).join('')+chip('custom','Custom',direction==='between'?'Your own range':'Your own number');
 $('goals-custom').hidden=goalDraft.option!=='custom';
 // Metrics with only one sensible direction fix Custom's direction too.
 if(!direction&&$('goals-comparison').disabled)$('goals-comparison').value='';
 $('goals-comparison').disabled=!!direction;
 if(direction)$('goals-comparison').value=direction;
 $('goals-target-high').hidden=direction!=='between';
 $('goals-target').setAttribute('aria-label',direction==='between'?'Lower end of the range':'Target value');
 updateGoalTargetNote();
 $('goals-comparison').title=direction==='between'?`Keep ${goalLabel(metric)} inside a range`:direction?`${direction==='at_most'?'Lower':'Higher'} is better for ${goalLabel(metric)}`:'';
 if(focus)$('goals-options').querySelector(`[data-goal-option="${goalDraft.option}"]`)?.focus();
 renderGoalPreview();
}
function chooseGoalOption(id,focus=false){
 if(!goalDraft)return;
 if(id!==goalDraft.option)clearAutoTitle();
 goalDraft.legacyDirection=false;goalDraft.option=id;goalFormError('');renderGoalOptions(focus);
}
function updateGoalTargetNote(){
 const note=$('goals-target-note'),raw=$('goals-target').value,value=Number(raw);
 const unit=goalUnit($('goals-metric').value);
 note.hidden=goalDraft?.option!=='custom';
 note.textContent=unit==='tokens'?(raw!==''&&Number.isSafeInteger(value)&&value>=0?`= ${compactNumber(value)} tokens`:'Whole tokens, e.g. 1000000000 for 1B'):unit==='percent'?'Enter the target percentage, not the change. 40% minus 5 percentage points is 35%; a 10% relative reduction is 36%.':unit==='ratio'?'Input tokens per output token. This is different from context-window fullness.':unit==='tokens_per_usd'?'Output tokens per dollar of paired covered spend.':'Enter the absolute target in the displayed units.';
}
function goalFormError(text){$('goals-form-error').textContent=text;$('goals-form-error').hidden=!text;}
async function postGoalAction(payload){
 const token=agentActionToken();
 if(!token)return {ok:false,error:'Token Meter is still loading. Try again in a moment.'};
 try{
  const response=await fetch('/goals',{method:'POST',headers:{'Content-Type':'application/json','X-Token-Meter-Action':token},body:JSON.stringify(payload)});
  return await response.json();
 }catch{return {ok:false,error:'Token Meter could not save goals.'};}
}
function acceptGoalResult(result){GOALS=result;goalsLoadedAt=Date.now();renderGoals();}
async function saveGoalForm(){
 const metric=$('goals-metric').value,scope=goalSelectedScope(),option=goalDraft.option,{period}=goalPeriodFields();
 let comparison,target,targetHigh;
 if(!option)return goalFormError('Choose a target.');
 if(option==='custom'){
  comparison=$('goals-comparison').value;target=Number($('goals-target').value);
  if(!comparison)return goalFormError('Choose At most or At least.');
  if(comparison==='between'){targetHigh=Number($('goals-target-high').value);if($('goals-target').value===''||$('goals-target-high').value===''||!(target>0)||!(targetHigh>target))return goalFormError('Enter a range whose upper end is above its lower end, both above zero.');}
  if($('goals-target').value===''||!Number.isFinite(target)||target<0||(goalUnit(metric)==='percent'&&target>100))return goalFormError('Enter a valid target.');
  if(goalUnit(metric)==='tokens'&&!Number.isSafeInteger(target))return goalFormError('Enter a whole-token count within the supported range.');
 }else{
  const chosen=goalDraft.options.find(o=>o.id===option);
  if(!chosen)return goalFormError('Choose a target.');
  comparison=chosen.comparison;target=chosen.target;targetHigh=chosen.target_high;
 }
 if(!goalDraft.start)return goalFormError('Choose a valid period.');
 $('goals-save').disabled=true;
 const edited=!!goalDraft.id,returnMode=goalDraft.returnMode,before=new Set((GOALS?.goals||[]).map(g=>g.id));
 const result=await postGoalAction({operation:'save',id:goalDraft.id||undefined,goal:{metric,scope,comparison,target,...(comparison==='between'?{target_high:targetHigh}:{}),target_option:option,period,start:goalDraft.start,end:goalDraft.end,repeat:$('goals-repeat').checked,title:$('goals-title').value.trim()||null}});
 $('goals-save').disabled=false;
 if(!result.ok)return goalFormError(result.error||'Token Meter could not save this goal.');
 const id=edited?goalDraft.id:result.goals.find(g=>!before.has(g.id))?.id||'',saved=result.goals.find(g=>g.id===id);
 const home=goalHome(saved?.metric);
 goalsListModes[home.section]=saved?(goalIsCurrent(saved)?'current':'previous'):edited?returnMode:'current';
 goalDraft=null;$('goals-dialog').close();goalNotice('Goal saved.');acceptGoalResult(result);
 if(edited&&location.hash===`#efficiency-goals/${id}`){loadGoalDetail(id);return;}
 if(location.hash!==`#${home.route}`)setHashRoute(home.route);
 loadGoals().then(()=>requestAnimationFrame(()=>requestAnimationFrame(()=>revealSavedGoal(id,home.section))));
}
function revealSavedGoal(id,section){
 const box=$(section),link=[...box.querySelectorAll('.goalTitleLink')].find(a=>a.getAttribute('href')===`#efficiency-goals/${id}`),card=link?.closest('.goalCard');
 if(!card)return box.scrollIntoView({block:'start'});
 card.scrollIntoView({block:'center'});link.focus({preventScroll:true});
 card.classList.add('is-saved');setTimeout(()=>card.classList.remove('is-saved'),2400);
}
async function goalAction(operation,id,message){
 const result=await postGoalAction({operation,id});
 if(!result.ok)return goalNotice(result.error||'Token Meter could not update this goal.',true);
 acceptGoalResult(result);goalNotice(message);
}
$('goals-cancel').onclick=()=>$('goals-dialog').close();
$('goals-dialog').addEventListener('close',()=>{$('goals-model-picker').open=false;if(!goalDraft)return;goalsListModes[goalDraft.section]=goalDraft.returnMode;goalDraft=null;++goalModelRequest;++goalPreviewRequest;renderGoals();});
$('goals-form').addEventListener('submit',event=>{event.preventDefault();saveGoalForm();});
$('goals-metric').addEventListener('change',()=>{const app=$('goals-app').value;fillGoalScopes($('goals-metric').value,goalSelectedScope());if(!$('goals-app').disabled)$('goals-app').value=app;refreshGoalModels();clearAutoTitle();goalDraft.option='';updateGoalFormFields();refreshGoalPreview();});
$('goals-app').addEventListener('change',()=>{
 const app=$('goals-app').value,before=goalDraft.modelKeys.length;
 if(app)goalDraft.modelKeys=goalDraft.modelKeys.filter(key=>goalDraft.models.find(m=>m.key===key)?.runtime===app);
 goalFormError(before!==goalDraft.modelKeys.length?'Selections from other apps were removed. Review the model selection before saving.':'');
 goalDraft.models=goalDraft.models.filter(m=>!app||m.runtime===app||goalDraft.modelKeys.includes(m.key));
 renderGoalModelPicker();clearAutoTitle();goalDraft.option='';updateGoalFormFields();refreshGoalPreview();refreshGoalModels();
});
$('goals-model-picker').querySelector('summary').addEventListener('click',event=>{if(event.currentTarget.getAttribute('aria-disabled')==='true')event.preventDefault();});
$('goals-model-picker').addEventListener('keydown',event=>{if(event.key==='Escape'){$('goals-model-picker').open=false;$('goals-model-picker').querySelector('summary').focus();}});
$('goals-model-picker').addEventListener('toggle',()=>requestAnimationFrame(positionGoalModelPicker));
window.addEventListener('resize',()=>{if($('goals-model-picker').open)positionGoalModelPicker();});
document.addEventListener('pointerdown',event=>{const picker=$('goals-model-picker');if(picker.open&&!picker.contains(event.target))picker.open=false;});
$('goals-model-options').addEventListener('change',event=>{
 const input=event.target.closest('[data-goal-model]');if(!input||!goalDraft)return;
 const key=input.dataset.goalModel,keys=new Set(goalDraft.modelKeys);
 if(key==='all')keys.clear();else if(input.checked){if(keys.size>=(GOALS.max_selected_models||32)){input.checked=false;goalFormError(`Select at most ${GOALS.max_selected_models||32} models.`);return;}keys.add(key);}else keys.delete(key);
 goalDraft.modelKeys=[...keys].sort();
 const all=$('goals-model-options').querySelector('[data-goal-model="all"]');all.checked=keys.size===0;
 $('goals-model-options').querySelectorAll('[data-goal-model]').forEach(option=>{if(option!==all)option.checked=keys.has(option.dataset.goalModel);});
 $('goals-model-summary').textContent=keys.size===0?'All models':keys.size===1?(goalDraft.models.find(m=>keys.has(m.key))?.label||'Model unavailable'):`${keys.size} models selected`;
 goalFormError('');clearAutoTitle();goalDraft.option='';updateGoalFormFields();refreshGoalPreview();
});
$('goals-model-search').addEventListener('input',()=>{clearTimeout(goalModelTimer);++goalModelRequest;goalModelTimer=setTimeout(refreshGoalModels,200);});
$('goals-target').addEventListener('input',()=>{if(goalDraft?.legacyDirection){goalDraft.legacyDirection=false;$('goals-comparison').value=GOALS.metrics[$('goals-metric').value].direction;}updateGoalTargetNote();renderGoalPreview();});
$('goals-target-high').addEventListener('input',renderGoalPreview);
$('goals-work-type').addEventListener('change',()=>{clearAutoTitle();goalDraft.option='';updateGoalFormFields();refreshGoalPreview();});
['goals-comparison','goals-repeat'].forEach(id=>$(id).addEventListener('change',renderGoalPreview));
$('goals-title').addEventListener('input',renderGoalPreview);
['goals-period','goals-start','goals-end'].forEach(id=>$(id).addEventListener('change',()=>{if(id==='goals-period')goalDraft.anchor='';updateGoalFormFields();refreshGoalPreview();}));
$('goals-metric-options').addEventListener('click',event=>{const button=event.target.closest('[data-goal-metric]');if(!button||button.dataset.goalMetric===$('goals-metric').value)return;$('goals-metric').value=button.dataset.goalMetric;$('goals-metric').dispatchEvent(new Event('change'));});
$('goals-options').addEventListener('click',event=>{const button=event.target.closest('[data-goal-option]');if(!button)return;chooseGoalOption(button.dataset.goalOption,true);if(button.dataset.goalOption==='custom')($('goals-comparison').disabled?$('goals-target'):$('goals-comparison')).focus();});
function handleGoalClick(event){
 const period=event.target.closest('[data-goal-period]');
 if(period){loadGoalDetail(period.dataset.goalId,period.dataset.goalPeriod);return;}
 const toggle=event.target.closest('[data-goals-toggle]');
 if(toggle){const root=event.currentTarget,section=root.id;goalsListModes[section]=goalsListModes[section]==='previous'?'current':'previous';return loadGoals().then(()=>root.querySelector('[data-goals-toggle]')?.focus());}
 const edit=event.target.closest('[data-goal-edit]'),duplicate=event.target.closest('[data-goal-duplicate]'),end=event.target.closest('[data-goal-end]'),remove=event.target.closest('[data-goal-delete]');
 if(edit){const g=GOALS?.goals?.find(goal=>goal.id===edit.dataset.goalEdit);if(g)openGoalForm(g);}
 else if(duplicate){const g=GOALS?.goals?.find(goal=>goal.id===duplicate.dataset.goalDuplicate);if(g)openGoalForm(g,true);}
 else if(end)goalAction('end',end.dataset.goalEnd,'Goal ended.');
 else if(remove)goalAction('delete',remove.dataset.goalDelete,'Goal deleted.');
}
['efficiency-goals','spend-goals','git-goals','work-goals'].forEach(id=>$(id).addEventListener('click',handleGoalClick));
function openGoalFromLink(event){
 const link=event.target.closest('[data-goal-new]');if(!link)return;
 event.preventDefault();
 loadGoals().then(()=>{if(GOALS)openGoalForm({},false,link.dataset.goalNew);});
}
document.addEventListener('click',openGoalFromLink);
document.addEventListener('click',event=>{if(event.target.closest('[data-goal-learn]'))$('goals-dialog').close();});
function closeGoalMenus(event){document.querySelectorAll('.goalMenu[open]').forEach(menu=>{if(!menu.contains(event.target)||event.target.closest('[data-goal-duplicate],[data-goal-end],[data-goal-delete]'))menu.open=false;});}
document.addEventListener('click',closeGoalMenus);
document.addEventListener('keydown',event=>{if(event.key!=='Escape')return;const menu=event.target.closest?.('.goalMenu[open]');if(menu){menu.open=false;menu.querySelector('summary').focus();}});
let goalDetailRequest=0,goalDetailOrigin='',goalDetailData=null;
function releaseGoalChart(){const svg=$('goals-trend-chart');if(svg)chartResizeObserver.unobserve(svg);goalDetailData=null;}
function showGoalDetail(open){
 const wasOpen=!$('goal-detail').hidden;
 if(!open){++goalDetailRequest;releaseGoalChart();}
 $('view-efficiency').classList.toggle('goalDetailOpen',open);
 $('goals-overview').hidden=open;$('goal-detail').hidden=!open;
 if(!open&&wasOpen)requestAnimationFrame(()=>{const link=[...$('goals-overview').querySelectorAll('.goalTitleLink')].find(a=>a.getAttribute('href')===`#efficiency-goals/${goalDetailOrigin}`);(link||$('goals-heading')).focus();});
}
function goalBackLink(g){const home=goalHome(g?.metric);return `<a class=tbtn href="#${home.route}">← Back to ${esc(home.back)}</a>`;}
async function loadGoalDetail(id,period=''){
 goalDetailOrigin=id;
 releaseGoalChart();
 showGoalDetail(true);
 const request=++goalDetailRequest;
 $('goal-detail').innerHTML=`${goalBackLink()}<p class="emptyState goalsEmpty">Loading goal detail…</p>`;
 let data;
 try{if(!GOALS)await loadGoals();data=await (await fetch(`/goals?goal=${encodeURIComponent(id)}${period?`&period_start=${encodeURIComponent(period)}`:''}`,{cache:'no-store'})).json();}
 catch{data={ok:false,error:'Goal detail is unavailable right now.'};}
 if(request!==goalDetailRequest)return;
 if(!data.ok||!GOALS){$('goal-detail').innerHTML=`${goalBackLink()}<p class="emptyState goalsEmpty">${esc(data.error||'Goal detail is unavailable right now.')}</p>`;return;}
 $('goal-detail').innerHTML=renderGoalDetail(data);
 goalDetailData=data;drawGoalTrend();const chart=$('goals-trend-chart');if(chart)chartResizeObserver.observe(chart);
 window.scrollTo(0,0);$('goal-detail-heading')?.focus({preventScroll:true});
}
function goalTrendChart(g,trend){
 const days=(trend||[]).map(p=>p.label?p.label.slice(11):p.day),note=(trend||[]).find(p=>p.reason)?.reason;
 if(!days.length)return '<p class="emptyState goalsEmpty">No chart evidence yet.</p>';
 return `<div class=efficiencyChartWrap><svg class="goalTrend efficiencyChart" id=goals-trend-chart preserveAspectRatio=none role=img tabindex=0 aria-label="${trend.some(p=>p.at!==undefined)?'Hourly':'Daily'} ${esc(goalLabel(g.metric))} to date. Use arrow keys to inspect values." aria-describedby=goals-trend-status></svg><div class="efficiencyChartTip chartTip goalTrendTip" id=goals-trend-tip role=tooltip hidden></div><span class=efficiencyChartStatus id=goals-trend-status aria-live=polite></span></div>`
  +`<div class=goalTrendAxis><span>${esc(days[0])}</span><span>${g.comparison==='between'?`Dashed lines: range ${esc(goalTargetText(g,g.metric))}`:`Dashed line: target ${esc(goalValue(g.target,g.metric))}`}</span><span>${esc(days.at(-1))}</span></div>`
  +(note?`<p class=goalsHelp>${esc(note)}</p>`:'');
}
function goalTrendPosition(samples,index){
 if(samples.length===1)return .5;
 const first=Number(samples[0]?.at),last=Number(samples.at(-1)?.at);
 return Number.isFinite(first)&&last>first?(Number(samples[index].at)-first)/(last-first):index/Math.max(1,samples.length-1);
}
function goalTrendIndex(samples,position){
 if(!samples.length)return 0;
 let closest=0;
 samples.forEach((sample,index)=>{if(Math.abs(goalTrendPosition(samples,index)-position)<Math.abs(goalTrendPosition(samples,closest)-position))closest=index;});
 return closest;
}
function drawGoalTrend(){
 const svg=$('goals-trend-chart');if(!svg||!goalDetailData)return;
 const g=goalDetailData.goal,samples=goalDetailData.trend||[],tip=$('goals-trend-tip'),status=$('goals-trend-status'),width=Math.max(1,Math.round(svg.clientWidth)),height=Math.max(1,Math.round(svg.clientHeight)),target=Number(g.target),targets=g.comparison==='between'?[target,Number(g.target_high)]:[target],known=p=>p.value!==null&&Number.isFinite(p.value),values=samples.filter(known).map(p=>p.value),low=Math.min(0,...targets,...values),high=Math.max(...targets,...values,low+1),span=high-low,pad=10,plot=Math.max(1,width-pad*2),x=index=>pad+plot*goalTrendPosition(samples,index),y=value=>height-pad-(value-low)/span*Math.max(1,height-pad*2);
 svg.setAttribute('viewBox',`0 0 ${width} ${height}`);tip.hidden=true;status.textContent='';
 let html=targets.map(t=>`<line class=goalSparkTarget x1="${pad}" x2="${width-pad}" y1="${y(t)}" y2="${y(t)}"/>`).join(''),segment=[];
 const finish=()=>{if(segment.length===1)html+=`<circle class=efficiencyChartDot cx="${segment[0][0]}" cy="${segment[0][1]}" r=3.5/>`;else if(segment.length)html+=`<polyline class=goalSparkLine points="${segment.map(p=>p.join(',')).join(' ')}"/>`;segment=[];};
 samples.forEach((p,i)=>{if(known(p))segment.push([x(i).toFixed(1),y(p.value).toFixed(1)]);else finish();});finish();
 if(!values.length)html+=`<text class=efficiencyChartEmpty x=10 y="${height/2}">No ${goalDetailData.trend_grain==='hour'?'hourly':'daily'} evidence</text>`;
 svg.innerHTML=html+`<g class=efficiencyChartInspect visibility=hidden><line class=efficiencyChartInspectLine y1="${pad}" y2="${height-pad}"/><circle class=efficiencyChartInspectDot r=4/></g>`;
 const inspect=svg.querySelector('.efficiencyChartInspect'),line=inspect.querySelector('line'),dot=inspect.querySelector('circle');let active=samples.length-1;
 const hide=()=>{inspect.setAttribute('visibility','hidden');tip.hidden=true;status.textContent='';};
 const show=(index,announce=false)=>{const p=samples[index];if(!p)return;active=index;const px=x(index),value=known(p)?goalValue(p.value,g.metric):'No data',label=p.label?`${p.day} ${p.label.slice(11)} – ${(p.through||p.label).slice(11)}`:p.day,text=`${label} · ${value} to date · target ${goalTargetText(g,g.metric)}${p.partial?' · partial hour':''}`;inspect.setAttribute('visibility','visible');line.setAttribute('x1',px);line.setAttribute('x2',px);dot.setAttribute('visibility',known(p)?'visible':'hidden');if(known(p)){dot.setAttribute('cx',px);dot.setAttribute('cy',y(p.value));}tip.textContent=text;tip.hidden=false;const edge=Math.min(width/2,tip.offsetWidth/2+8);tip.style.left=`${Math.max(edge,Math.min(width-edge,px))}px`;if(announce)status.textContent=text;};
 svg.onpointermove=event=>{const rect=svg.getBoundingClientRect(),local=(event.clientX-rect.left)*width/Math.max(1,rect.width);show(goalTrendIndex(samples,Math.max(0,Math.min(1,(local-pad)/plot))));};
 svg.onpointerleave=hide;svg.onblur=hide;svg.onfocus=()=>show(samples.length-1,true);
 svg.onkeydown=event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();show(efficiencyChartKeyIndex(event.key,active,samples.length),true);};
 if(document.activeElement===svg)show(active,true);
}
function goalDriverRows(rows,valueText){
 return rows.map(r=>`<div class=goalDriverRow><span title="${esc(r.label)}">${esc(r.label)}</span><div class=goalDriverBar><i style="width:${Math.max(0,Math.min(100,Number(r.share)||0)).toFixed(1)}%"></i></div><b>${esc(valueText(r))}</b></div>`).join('');
}
function goalDriversCard(g,drivers){
 const metric=g.metric,parts=[];
 if(metric==='cost_per_1k_lines'){
  const repos=drivers.by_repository||[],top=Math.max(0,...repos.map(r=>r.cost));
  parts.push(repos.length?`<h5>By repository</h5>${goalDriverRows(repos.map(r=>({...r,share:top?r.cost/top*100:0})),r=>`${goalValue(r.value,metric)} · ${f(r.lines)} lines · ${money(r.cost)}`)}`:'<p class=goalDriverNote>No repository had both measured Git coverage and agent spend in this period.</p>');
  const coverage=drivers.repository_coverage;
  if(coverage){
   parts.push(`<p class=goalDriverNote>${f(coverage.included)} of ${f(coverage.discovered)} discovered repositories included${g.measurement?.counted_share!==null&&g.measurement?.counted_share!==undefined?` · ${g.measurement.counted_share.toFixed(0)}% of agent spend covered`:''}. All projects includes every eligible discovered repository, including those outside this preview.</p>`);
   if(coverage.omitted)parts.push(`<p class=goalDriverNote>Showing the top ${f(repos.length)} by spend. ${f(coverage.omitted)} more comparable repositories contribute to the total.</p>`);
   parts.push(`<details class=goalsPrevious><summary>Repository evidence</summary><p class=goalDriverNote>${esc(`${coverage.start} to ${coverage.end}. ${coverage.available?(coverage.stale?'Git history is stale.':'Git evidence available.'):'Git history unavailable.'} Repositories require spend evidence and measured Git coverage; membership does not depend on meeting the target.`)}</p>`
    +((coverage.rows||[]).length?`<div class=deliveryTableWrap><table class=tbl><thead><tr><th>Repository</th><th>Evidence</th><th>Spend</th><th>Pushed lines</th></tr></thead><tbody>${coverage.rows.map(r=>{const evidence=deliveryProjectEvidence(r);return `<tr><td>${esc(r.label)}</td><td>${esc(evidence.label)}${r.availability.partial?' · partial':''}</td><td>${r.availability.cost?money(r.cost):'--'}</td><td>${r.availability.code_pushed?f(r.lines):'--'}</td></tr>`;}).join('')}</tbody></table></div>`:'<p class=emptyState>No published repository evidence yet.</p>')
    +((coverage.limits||[]).length?`<p class=goalDriverNote>${esc(coverage.limits.map(code=>({repository_limit:'Repository discovery limit reached.',scan_limited:'Git history scan was limited.',no_push_history:'Some discovered history has no recorded push evidence.'})[code]).join(' '))}</p>`:'')
    +'<p class=goalDriverNote>Zero lines means measured Git history with no changed lines in this period. “--” means unavailable. Discovery follows local agent activity, up to '+f(coverage.repository_limit)+' repositories; it does not scan every repository on disk. Sessions outside repository folders are not attributed. The Git page has its own selected history range.</p><a class=tbtn href="#git">Open Git</a></details>');
  }
 }else if(metric==='tokens'){
  const tokens=drivers.token_drivers||{};
  if((tokens.by_model||[]).length)parts.push(`<h5>By model, share of tokens</h5>${goalDriverRows(tokens.by_model,r=>`${goalValue(r.tokens,metric)} · ${r.share.toFixed(0)}%`)}`);
  if((tokens.by_agent||[]).length>1)parts.push(`<h5>By app</h5>${goalDriverRows(tokens.by_agent,r=>`${goalValue(r.tokens,metric)} · ${r.share.toFixed(0)}%`)}`);
  if(tokens.omitted)parts.push(`<p class=goalDriverNote>${f(tokens.omitted)} more models contribute to the total.</p>`);
 }else{
  const ratio=['output_per_dollar','context_load','reasoning_ratio'].includes(metric);
  if((drivers.by_model||[]).length)parts.push(`<h5>By model, share of spend</h5>${goalDriverRows(drivers.by_model,r=>`${money(r.cost)} · ${r.share.toFixed(0)}%${ratio&&r.value!==null?` · ${goalValue(r.value,metric)}`:''}`)}`);
  if((drivers.by_effort||[]).length)parts.push(`<h5>By reasoning effort</h5>${goalDriverRows(drivers.by_effort.map(r=>({...r,label:r.effort.charAt(0).toUpperCase()+r.effort.slice(1)})),r=>`${money(r.cost)} · ${r.share.toFixed(0)}%`)}`);
  if((drivers.by_agent||[]).length>1)parts.push(`<h5>By app</h5>${goalDriverRows(drivers.by_agent,r=>`${money(r.cost)} · ${r.share.toFixed(0)}%`)}`);
  if(drivers.cache_hit!==null&&drivers.cache_hit!==undefined)parts.push(`<p class=goalDriverNote>${esc(`${drivers.cache_hit.toFixed(0)}% of processed input came from cache.`)}</p>`);
 }
 return `<section class="card goalDetailCard"><h4>What is driving it</h4>${parts.join('')||'<p class=goalDriverNote>No breakdown is available for this period yet.</p>'}</section>`;
}
function goalCoachingCard(tips){
 const hasEstimate=(tips||[]).some(t=>t.estimate);
 return `<section class="card goalDetailCard"><h4>Coaching</h4>${(tips||[]).map(t=>`<article class=goalCoachTip><h5>${esc(t.title)}</h5><p>${esc(t.evidence)}</p><p><strong>Try:</strong> ${esc(t.action)}</p>${t.estimate?`<p class=goalCoachSaving>Could save up to ${esc(money(t.estimate))}<small>${esc(t.estimate_basis||'')}</small></p>`:''}</article>`).join('')}${hasEstimate?'<p class=goalDriverNote>Estimates are upper bounds from your own history. They can overlap, so do not add them together.</p>':''}</section>`;
}
function goalContributorsCard(g,contributors){
 const items=contributors?.items||[],basis=contributors?.basis||'cost';
 if(!items.length)return '';
 if(g.metric==='cost_per_1k_lines')return `<section class="card goalDetailCard"><h4>Biggest contributors</h4><p class=goalDriverNote>Repositories ranked by covered spend, ${esc(contributors.start)} to ${esc(contributors.end)}. ${f(contributors.repositories)} repositories in total; showing ${f(items.length)}. Lines pushed counts additions plus deletions. Rates require at least 50 lines and positive spend.</p><div class=deliveryTableWrap><table class=tbl><thead><tr><th>Repository</th><th>Spend</th><th>Lines pushed</th><th>Cost per 1K pushed lines</th><th>Spend share</th></tr></thead><tbody>${items.map(c=>`<tr><td>${esc(c.label)}${c.partial?' · partial':''}</td><td>${money(c.cost)}</td><td>${f(c.lines)}</td><td>${esc(goalValue(c.value,g.metric))}</td><td>${c.share===null?'--':c.share.toFixed(0)+'%'}</td></tr>`).join('')}</tbody><tfoot><tr><th>All included repositories</th><td>${money(contributors.total_cost)}</td><td>${f(contributors.total_lines)}</td><td>${esc(goalValue(g.measurement?.value,g.metric))}</td><td>${contributors.total_cost>0?'100%':'--'}</td></tr></tfoot></table></div></section>`;
 const amount=c=>basis==='cost'?money(c.cost||0):`${compactNumber(basis==='tokens'?c.tokens:basis==='input tokens'?c.input_tokens:c.reasoning_tokens)} tokens`;
 return `<section class="card goalDetailCard"><h4>Biggest contributors</h4><p class=goalDriverNote>${esc(`Sessions that moved this goal most this period, by ${basis}. ${contributors.sessions} sessions in total.`)}</p><table class=tbl><thead><tr><th>Session</th><th>Last day</th><th>Model</th><th>Effort</th><th>Executions</th><th>${esc(basis==='cost'?'Cost':'Tokens')}</th><th>Share</th><th></th></tr></thead><tbody>${items.map(c=>`<tr><td>${esc(c.title)}<br><small>${esc(c.agent)}</small></td><td>${esc(c.last_day)}</td><td>${esc(c.model)}</td><td>${esc(c.effort||'--')}</td><td>${f(c.executions)}</td><td>${esc(amount(c))}</td><td>${c.share.toFixed(0)}%</td><td><a href="${esc(sessionRoute(c.id))}">Open</a></td></tr>`).join('')}</tbody></table></section>`;
}
function goalReflectionCard(d){
 const g=d.goal,r=d.reflection||{},saved=r.checkin||{},current=r.current_start;
 const back=g.repeat&&current&&current!==g.start?`<button class=tbtn type=button data-goal-period="${esc(current)}" data-goal-id="${esc(g.id)}">Current period</button>`:'';
 if(!r.available)return `<section class="card goalDetailCard"><h4>Goal reflection</h4><p class=goalsHelp>${g.ended_at?'This goal ended early without a final verdict.':'Reflection opens after this period finishes. Select a previous period to review it.'}</p>${back}</section>`;
 const change=r.change===null||r.change===undefined?'No comparable reference':`${r.change>0?'+':''}${Number(r.change).toFixed(1)}${r.change_unit==='%'?'%':' percentage points'} vs reference`;
 const options=(values,selected)=>'<option value="">Choose</option>'+values.map(([value,label])=>`<option value="${value}"${selected===value?' selected':''}>${label}</option>`).join('');
 return `<section class="card goalDetailCard"><h4>Goal reflection</h4><p>${esc(GOAL_STATUS_LABELS[g.status]||g.status)} · ${esc(goalValue(g.measurement.value,g.metric))} against ${esc(goalTargetText(g,g.metric))}.</p><p class=goalsHelp>${esc(change)} · ${esc(GOAL_CONFIDENCE[g.measurement.confidence]||'Partial data')}. Results use current evidence and pricing; your check-in records your own assessment.</p>${back}`
  +`<form id=goal-reflection-form data-id="${esc(g.id)}" data-period="${esc(g.start)}" data-definition="${esc(r.definition)}"><fieldset class=goalsStep><legend>What changed?</legend><div class=goalsOptions>${[['model','Model choice'],['reasoning','Reasoning effort'],['context','Context habits'],['none','No deliberate change']].map(([value,label])=>`<label><input type=checkbox name=reflection-change value="${value}"${(saved.changes||[]).includes(value)?' checked':''}> ${label}</label>`).join('')}</div></fieldset>`
  +`<div class=goalsFields><label class=goalsStep>Did the result meet your needs?<select class=filterSelect name=reflection-outcome required>${options([['useful','Yes'],['not_useful','No'],['unsure','Unsure']],saved.outcome)}</select></label><label class=goalsStep>Next step<select class=filterSelect name=reflection-next required>${options([['keep','Keep this approach'],['adjust','Adjust the goal or approach'],['end','Plan to end this goal']],saved.next_step)}</select></label></div><p class=goalsHelp>Saving this check-in does not change or end the goal.${saved.reviewed_at?` Last saved ${esc(new Date(saved.reviewed_at).toLocaleString())}.`:''}</p><button class=tbtn type=submit>Save check-in</button><p class=goalsHelp role=status data-reflection-status></p></form></section>`;
}
$('goal-detail').addEventListener('submit',async event=>{
 const form=event.target.closest('#goal-reflection-form');if(!form)return;
 event.preventDefault();const button=form.querySelector('[type=submit]'),status=form.querySelector('[data-reflection-status]');button.disabled=true;
 const result=await postGoalAction({operation:'reflect',id:form.dataset.id,period_start:form.dataset.period,definition:form.dataset.definition,reflection:{changes:[...form.querySelectorAll('[name=reflection-change]:checked')].map(input=>input.value),outcome:form.elements.namedItem('reflection-outcome').value,next_step:form.elements.namedItem('reflection-next').value}});
 button.disabled=false;
 if(!result.ok){status.textContent=result.error||'Check-in could not be saved.';return;}
 acceptGoalResult(result);
 if(location.hash===`#efficiency-goals/${form.dataset.id}`&&goalDetailData?.goal?.start===form.dataset.period){await loadGoalDetail(form.dataset.id,form.dataset.period);const saved=$('goal-reflection-form');if(saved){saved.querySelector('[data-reflection-status]').textContent='Check-in saved.';saved.querySelector('[type=submit]').focus();}}
});
function renderGoalDetail(d){
 const g=d.goal,m=g.measurement||{},metric=g.metric,option=(GOALS.metrics[metric]?.options||[]).find(o=>o.id===g.target_option);
 const scope=metric==='cost_per_1k_lines'?`Across ${goalRepoText(m.projects||0,m.project_names)}`:g.scope_label;
 const tiles=[
  ['Current',goalValue(m.value,metric),GOAL_CONFIDENCE[m.confidence]||''],
  [g.comparison==='between'?'Target range':g.comparison==='at_most'?'Target, at most':'Target, at least',g.comparison==='between'?goalTargetText(g,metric):goalValue(g.target,metric),option?.label||'Custom'],
  m.projected!==undefined?['Projected',goalValue(m.projected,metric),'At the current pace']:null,
  g.baseline?.value!==null&&g.baseline?.value!==undefined?[g.baseline.label||'Reference',goalValue(g.baseline.value,metric),`${g.baseline.start} to ${g.baseline.end}`]:null,
 ].filter(Boolean);
 const reason=[g.status_reason,g.progress?.early?'Early in the period':'',goalGuardrail(g)?`Watch: ${goalGuardrail(g)}`:'',...goalNotes(g).filter(n=>!n.startsWith('Across '))].filter(Boolean).join(' · ');
 return `<div class=goalDetailHead>${goalBackLink(g)}<div class=goalDetailTitle><h3 id=goal-detail-heading tabindex=-1>${esc(goalTitle(g))}</h3><p class=goalMeta>${esc(scope)} · ${esc(goalLabel(metric))} · ${esc(g.start)} to ${esc(g.end)}${g.repeat?` · Repeats ${esc(goalRepeatText(g))}`:''}</p></div><span class="chip goalStatus ${esc(g.status)}">${esc(GOAL_STATUS_LABELS[g.status]||g.status)}</span><button class=tbtn type=button data-goal-edit="${esc(g.id)}">Edit</button><button class=tbtn type=button data-goal-duplicate="${esc(g.id)}">Duplicate</button></div>`
  +`<div class=goalDetailSummary>${tiles.map(([label,value,note])=>`<div class="card metric goalDetailMetric"><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></div>`).join('')}</div>`
  +(reason?`<p class=goalDetail>${esc(reason)}</p>`:'')
  +`<section class="card goalDetailCard"><h4>${esc(goalLabel(metric))} to date</h4>${goalTrendChart(g,d.trend)}</section>`
  +`<div class=goalDetailGrid>${goalDriversCard(g,d.drivers||{})}${goalCoachingCard(d.coaching)}</div>`
  +goalReflectionCard(d)
  +(['output_per_dollar','context_load','reasoning_ratio','frontier_share'].includes(metric)?`<p><a href="#learn-efficiency-${metric}">How ${esc(goalLabel(metric))} works</a></p>`:'')
  +(g.history?.length?`<section class="card goalDetailCard"><h4>Previous periods</h4>${goalHistory(g)}</section>`:'')
  +goalContributorsCard(g,d.contributors);
}
