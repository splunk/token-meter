// A subagent run spends against its root session's cap, which the server names.
function budgetSessionKey(s){return String(s?.session_budget?.id||s?.source?.id||s?.session||'unknown');}
function saveLegacySessionBudgets(){try{localStorage.setItem('tm_session_budgets',JSON.stringify(legacySessionBudgets));}catch(e){}}
function sessionBudgetForSession(s){
 const saved=parseFloat(s?.session_budget?.budget_usd);
 if(Number.isFinite(saved)&&saved>0)return saved;
 const legacy=parseFloat(legacySessionBudgets[budgetSessionKey(s)]);
 if(Number.isFinite(legacy)&&legacy>0)return legacy;
 return defaultSessionBudget;
}
function sessionBudgetSource(s){return s?.session_budget?.source||'legacy';}
// A session family (root plus child runs) shares one cap; the server reports
// its combined spend, which is a lower bound when any member lacks evidence.
function sessionCapSpend(s){
 const b=s?.session_budget;
 if(b&&Object.prototype.hasOwnProperty.call(b,'cost_partial')){
  const spend=Number(b.spend_usd),available=b.cost_available!==false&&b.spend_usd!=null&&Number.isFinite(spend);
  return {available,spend:available?spend:0,partial:available&&!!b.cost_partial};
 }
 return {available:metricAvailable(s||{},'cost'),spend:Number(s?.total_cost)||0,partial:false};
}
async function persistSessionBudget(s,value,{migrating=false}={}){
 const sessionId=budgetSessionKey(s),response=await fetch('/settings/session-budget',{method:'POST',headers:{'Content-Type':'application/json','X-Token-Meter-Action':agentActionToken()},body:JSON.stringify({session_id:sessionId,budget_usd:value})});
 const result=await response.json();
 if(!response.ok||!result.ok)throw new Error(result.error||'Could not save session budget.');
 if(s)s.session_budget=result.session_budget;
 if(migrating){delete legacySessionBudgets[sessionId];saveLegacySessionBudgets();}
 return result.session_budget;
}
function queueSessionBudget(s,value,{migrating=false}={}){
 const sessionId=budgetSessionKey(s);
 let entry=sessionBudgetWrites.get(sessionId);
 if(!entry){entry={pending:false,saving:false,write:null,promise:null};sessionBudgetWrites.set(sessionId,entry);}
 entry.write={value,migrating};entry.pending=true;
 if(entry.saving)return entry.promise;
 entry.saving=true;
 entry.promise=(async()=>{
  let finalError=null;
  while(entry.pending){
   const write=entry.write;entry.pending=false;
   try{await persistSessionBudget(s,write.value,{migrating:write.migrating});finalError=null;}
   catch(error){finalError=error;}
  }
  entry.saving=false;
  if(sessionBudgetWrites.get(sessionId)===entry)sessionBudgetWrites.delete(sessionId);
  if(finalError)throw finalError;
 })();
 return entry.promise;
}
function migrateLegacySessionBudget(s){
 const sessionId=budgetSessionKey(s),legacy=parseFloat(legacySessionBudgets[sessionId]);
 if(sessionBudgetSource(s)!=='default'||!Number.isFinite(legacy)||legacy<=0)return;
 queueSessionBudget(s,legacy,{migrating:true}).then(()=>{if(s===CURRENT)syncSessionBudgetControls(sessionBudgetForSession(s),s);}).catch(()=>{});
}
function sessionBudgetSliderMax(s,value){
 const reference=Math.max(defaultSessionBudget,Number(value)||0,sessionCapSpend(s).spend);
 return Math.max(SESSION_BUDGET_SLIDER_BASE_MAX,Math.ceil(reference*1.5/10)*10);
}
function syncSessionBudgetControls(value,s=CURRENT){
 const n=Math.max(0.5,Number(value)||defaultSessionBudget);
 const slider=$('budget-slider'),max=sessionBudgetSliderMax(s,n);
 $('budget').value=formatBudgetInput(n);
 slider.max=String(max);
 slider.value=String(Math.min(max,Math.max(SESSION_BUDGET_SLIDER_MIN,n)));
 const min=Number(slider.min)||SESSION_BUDGET_SLIDER_MIN;
 slider.style.setProperty('--budget-fill',`${Math.max(0,Math.min(100,100*(Number(slider.value)-min)/(max-min||1)))}%`);
  $('budget-slider-min').textContent=money(min);
 $('budget-slider-max').textContent=money(max);
 const capSpend=sessionCapSpend(s);
 $('session-budget-spend').textContent=capSpend.available?`Session spend ${capSpend.partial?'at least ':''}${money(capSpend.spend)}${s?.cost_approx?' est':''}`:'Session spend unavailable';
}
function loadBudgetForSession(s){
 syncSessionBudgetControls(sessionBudgetForSession(s),s);
 migrateLegacySessionBudget(s);
}
let sessionBudgetStatusTimer=0;
function saveBudgetForSession(value){
 if(!CURRENT)return;
 const n=parseFloat(value);
 if(!Number.isFinite(n)||n<=0)return;
 syncSessionBudgetControls(n,CURRENT);
 queueSessionBudget(CURRENT,n).then(()=>{const status=$('session-budget-status');status.textContent='Saved';clearTimeout(sessionBudgetStatusTimer);sessionBudgetStatusTimer=setTimeout(()=>{status.textContent='';},2500);}).catch(error=>showAppToast({title:'Session budget could not be saved',body:error.message||String(error),kind:'warn',sticky:true}));
 return n;
}
$('budget').value=String(defaultSessionBudget);
$('budget').addEventListener('input',()=>saveBudgetForSession($('budget').value));
$('budget').addEventListener('change',()=>{if(CURRENT)renderSession(CURRENT);});
$('budget-slider').addEventListener('input',()=>saveBudgetForSession($('budget-slider').value));
$('budget-slider').addEventListener('change',()=>{if(CURRENT)renderSession(CURRENT);});
function dollarInputValue(id,fallback=1e9){
 const n=parseFloat($(id)?.value);
 return Number.isFinite(n)&&n>0?n:fallback;
}
function formatBudgetInput(n){
 n=Math.max(0,Math.ceil((n||0)*2)/2);
 return Number.isInteger(n)?String(n):n.toFixed(1);
}
function setBudgetCap(n){
 const value=formatBudgetInput(n);
 return saveBudgetForSession(value)||parseFloat(value);
}
function extendBudget(){
 if(!CURRENT) return;
 const add=parseFloat($('budget-add').value);
 if(!Number.isFinite(add)||add<=0){ setNotifyState('Enter a positive budget amount.'); return; }
 const currentCost=sessionCapSpend(CURRENT).spend;
 const currentCap=dollarInputValue('budget',0);
 const next=setBudgetCap(Math.max(currentCap,currentCost)+add);
 setNotifyState('Budget raised to '+money(next)+'.');
 pushAppNotice('Budget updated','Session budget raised to '+money(next)+'.','neutral',{status:'Budget updated'});
 renderSession(CURRENT);
}
$('budget-add-btn').onclick=extendBudget;
$('budget-add').addEventListener('keydown',e=>{ if(e.key==='Enter') extendBudget(); });

const CHART_SCALES=['linear','cumulative'];
let chartScale=localStorage.getItem('tm_chart_scale')||'linear';
if(!CHART_SCALES.includes(chartScale)){
 chartScale='linear';
 localStorage.setItem('tm_chart_scale',chartScale);
}
function paintScale(){
 [...$('scale').querySelectorAll('button')].forEach(b=>{
  const selected=b.dataset.scale===chartScale;
  b.classList.toggle('on',selected);
  b.setAttribute('aria-pressed',selected?'true':'false');
 });
}
$('scale').onclick=e=>{ if(e.target.dataset.scale){ chartScale=e.target.dataset.scale; localStorage.setItem('tm_chart_scale',chartScale); paintScale(); if(CURRENT) drawIO(CURRENT.series||[]); } };
paintScale();

const CHART_MODES=['tokens','cost','context','tools','steps','wait'];
const requestedChartMode=new URLSearchParams(location.search).get('chart');
let chartMode=CHART_MODES.includes(requestedChartMode)?requestedChartMode:'tokens';
if(chartMode==='cache'){
 chartMode='tokens';
 localStorage.setItem('tm_chart_mode',chartMode);
}
if(!CHART_MODES.includes(chartMode)){
 chartMode='tokens';
 localStorage.setItem('tm_chart_mode',chartMode);
}
function paintChartMode(){
 [...$('chart-mode').querySelectorAll('button')].forEach(b=>b.classList.toggle('on',b.dataset.chart===chartMode));
}
$('chart-mode').onclick=e=>{ if(e.target.dataset.chart){ chartMode=e.target.dataset.chart; localStorage.setItem('tm_chart_mode',chartMode); paintChartMode(); if(CURRENT) drawIO(CURRENT.series||[]); } };
paintChartMode();

let globalSearch=localStorage.getItem('tm_global_search')||'';
let globalSort=localStorage.getItem('tm_global_sort')||'recent';
let globalApp=localStorage.getItem('tm_global_app')||'';
if(['claude_code','claude_desktop'].includes(globalApp)){
 globalApp='claude';
 localStorage.setItem('tm_global_app',globalApp);
}
let globalProject=localStorage.getItem('tm_global_project')||'';
let globalTime=localStorage.getItem('tm_global_time')||DEFAULT_TIME_FILTER_WINDOW;
let globalShowChildren=localStorage.getItem('tm_global_children')==='show';
let childSessionMap=new Map();
let subagentUsageDimension=localStorage.getItem('tm_subagent_usage_dimension')||'runtimes';
if(!['runtimes','models','depths','kinds','roles'].includes(subagentUsageDimension))subagentUsageDimension='runtimes';
let subagentUsageSort=localStorage.getItem('tm_subagent_usage_sort')||'cost';
if(!['cost','tokens','agents','output'].includes(subagentUsageSort))subagentUsageSort='cost';
let subagentView=localStorage.getItem('tm_subagent_view')||'roles';
if(!['issues','sessions','roles'].includes(subagentView))subagentView='roles';
let subagentRoleChartMode=localStorage.getItem('tm_subagent_role_chart')||'average';
if(!['spend','average','runs'].includes(subagentRoleChartMode))subagentRoleChartMode='average';
let subagentFilters={
 query:localStorage.getItem('tm_subagent_filter_search')||'',
 role:localStorage.getItem('tm_subagent_filter_role')||'',
 kind:localStorage.getItem('tm_subagent_filter_kind')||'',
 runtime:localStorage.getItem('tm_subagent_filter_app')||'',
 project:localStorage.getItem('tm_subagent_filter_project')||'',
 model:localStorage.getItem('tm_subagent_filter_model')||'',
 status:localStorage.getItem('tm_subagent_filter_status')||'all',
 signal:localStorage.getItem('tm_subagent_filter_signal')||'all',
 window:localStorage.getItem('tm_subagent_filter_time')||DEFAULT_TIME_FILTER_WINDOW,
 sort:localStorage.getItem('tm_subagent_filter_sort')||'recent',
};
if(subagentFilters.sort==='elapsed'){
 subagentFilters.sort='work';
 localStorage.setItem('tm_subagent_filter_sort','work');
}
if(subagentFilters.window==='24h'){
 subagentFilters.window='today';
 localStorage.setItem('tm_subagent_filter_time','today');
}
if(!TIME_FILTER_WINDOWS.includes(subagentFilters.window)){
 subagentFilters.window=DEFAULT_TIME_FILTER_WINDOW;
 localStorage.removeItem('tm_subagent_filter_time');
}
const SUBAGENT_PAGE_SIZE=50;
let subagentVisibleLimit=SUBAGENT_PAGE_SIZE,selectedSubagentId='',expandedSubagentRoot=null;
let subagentAdvancedOpen=Boolean(subagentFilters.model||subagentFilters.status!=='all'||subagentFilters.signal!=='all'||subagentFilters.sort!=='recent');
const logHtmlCache=new WeakMap();
function setLogText(element,value){
 value=String(value??'');
 if(element.textContent!==value)element.textContent=value;
}
function setLogAttribute(element,name,value){
 value=String(value??'');
 if(element.getAttribute(name)!==value)element.setAttribute(name,value);
}
function setLogHtml(element,html){
 if(logHtmlCache.get(element)===html)return;
 element.innerHTML=html;
 logHtmlCache.set(element,html);
}
function setLogSelectOptions(element,html,value){
 setLogHtml(element,html);
 if(element.value!==value)element.value=value;
}
async function loadAllSessions(){
 if(allSessionsRequest)return allSessionsRequest;
 allSessionsRequest=fetch('/logs',{cache:'no-store'}).then(response=>{
  if(!response.ok)throw new Error(`All sessions request failed (${response.status}).`);
  return response.json();
 }).then(payload=>{
  allSessionInventory=payload.sessions||[];
  allSessionInventoryAt=Number(payload.generated_at||0);
  if(LATEST&&$('view-session').classList.contains('on')&&sessionScope==='all')renderAllSessions(LATEST.xsession);
  if(LATEST&&($('view-subagents').classList.contains('on')||($('view-session').classList.contains('on')&&sessionScope==='subagents')))renderSubagentExplorer(LATEST.xsession?.agent_usage);
  return allSessionInventory;
 }).catch(()=>allSessionInventory||[]).finally(()=>{allSessionsRequest=null;});
 return allSessionsRequest;
}
function loadProjectModelFilterMap(){
 const stored=localStorage.getItem('tm_model_project_filters');
 if(!stored)return {};
 try{
  const parsed=JSON.parse(stored);
  return parsed&&typeof parsed==='object'&&!Array.isArray(parsed)?parsed:{};
 }catch(error){return {};}
}
function loadModelFilters(project=''){
 if(project){
  const scoped=loadProjectModelFilterMap(),values=scoped[project];
  return Array.isArray(values)?[...new Set(values.filter(value=>typeof value==='string'&&value))]:[];
 }
 const stored=localStorage.getItem('tm_model_filters');
 if(stored!==null){try{const parsed=JSON.parse(stored);if(Array.isArray(parsed))return [...new Set(parsed.filter(value=>typeof value==='string'&&value))];}catch(error){}}
 const legacy=localStorage.getItem('tm_model_filter')||'';
 return legacy?[legacy]:[];
}
function saveActiveModelFilters(){
 if(!modelProject)localStorage.setItem('tm_model_filters',JSON.stringify(modelFilters));
 else{
  const scoped=loadProjectModelFilterMap();
  scoped[modelProject]=modelFilters;
  localStorage.setItem('tm_model_project_filters',JSON.stringify(scoped));
 }
 localStorage.removeItem('tm_model_filter');
}
let modelProject=localStorage.getItem('tm_model_project')||'';
let modelFilters=loadModelFilters(modelProject);
const MODEL_RANGES=['today','yesterday','7','30','90','month','last_month','all'];
let modelRange=localStorage.getItem('tm_model_range')||'30';
let modelTrendMetric=localStorage.getItem('tm_model_trend_metric')||'speed';
const MODEL_MIX_KEYS=['cost','output','executions'];
let modelMixMetric=localStorage.getItem('tm_model_mix_metric');if(!MODEL_MIX_KEYS.includes(modelMixMetric))modelMixMetric='cost';
const MODEL_SORT_KEYS=['model','cost','cost_per_exec','cache','executions','output','speed','wait','pushback'];
let modelSort=(()=>{try{const stored=JSON.parse(localStorage.getItem('tm_model_sort')||'null');if(stored&&MODEL_SORT_KEYS.includes(stored.key)&&['asc','desc'].includes(stored.direction))return {key:stored.key,direction:stored.direction};}catch(error){}return {key:'cost',direction:'desc'};})();
const modelExpandedGroups=new Set();
const MODEL_SPEED_MAX_SERIES=8;
let modelSpeedSelection=(()=>{try{const stored=JSON.parse(localStorage.getItem('tm_model_speed_models')||'null');return Array.isArray(stored)?[...new Set(stored.filter(value=>typeof value==='string'&&value))].slice(0,MODEL_SPEED_MAX_SERIES):null;}catch(error){return null;}})();
let activeModelStats=null,activeModelScope='',activeModelGeneration=0;
let modelProjectRequest=0,modelProjectLoadingKey='';
const modelProjectCache=new Map();
const OTHER_LOCAL_SESSIONS_PROJECT='__other_local_sessions__';
function projectFilterValue(value){
 const project=String(value||'').trim().replaceAll('\\','/');
 return project&&(project.startsWith('/')&&!project.startsWith('/private/')||project.startsWith('~/')&&!project.startsWith('~/.codex/')&&!project.startsWith('~/Library/')&&!project.startsWith('~/Documents/Codex/')||/^[A-Za-z]:\//.test(project))?project:OTHER_LOCAL_SESSIONS_PROJECT;
}
function projectFilterLabel(value){return value===OTHER_LOCAL_SESSIONS_PROJECT?'Other local sessions':value;}
function storedModelFilterId(value){return typeof value==='string'&&value.length<=200&&!/[\\/\x00-\x1F\x7F]/.test(value)&&/^[^:]+::[^:]+(?:::[^:]+)?$/.test(value);}
function migrateStoredProjectFilters(key,scoped=false){
 const stored=localStorage.getItem(key);if(!stored)return scoped?undefined:'';
 if(!scoped){const project=projectFilterValue(stored);if(project!==stored)localStorage.setItem(key,project);return project;}
 let parsed={};try{const value=JSON.parse(stored);if(value&&typeof value==='object'&&!Array.isArray(value))parsed=value;}catch(error){return;}
 const grouped={};Object.entries(parsed).forEach(([project,values])=>{if(!Array.isArray(values))return;const group=projectFilterValue(project);grouped[group]=[...new Set([...(grouped[group]||[]),...values.filter(storedModelFilterId)])];});
 if(JSON.stringify(grouped)!==JSON.stringify(parsed))localStorage.setItem(key,JSON.stringify(grouped));
}
migrateStoredProjectFilters('tm_model_project_filters',true);
migrateStoredProjectFilters('tm_efficiency_project_filters',true);
globalProject=migrateStoredProjectFilters('tm_global_project');
modelProject=migrateStoredProjectFilters('tm_model_project');
modelFilters=loadModelFilters(modelProject);
if(!['recent','cost','tokens','executions','wait'].includes(globalSort)) globalSort='recent';
if(globalTime!==normalizeTimeFilterWindow(globalTime)){globalTime=normalizeTimeFilterWindow(globalTime);if(globalTime===DEFAULT_TIME_FILTER_WINDOW)localStorage.removeItem('tm_global_time');else localStorage.setItem('tm_global_time',globalTime);}
if(!MODEL_RANGES.includes(modelRange)){modelRange='30';localStorage.setItem('tm_model_range',modelRange);}
if(!['speed','wait','avg_input','avg_output'].includes(modelTrendMetric))modelTrendMetric='speed';
$('m-range').value=modelRange;
function modelScopeLabel(project=modelProject){return project?projectFilterLabel(project):'All projects';}
function paintModelProjects(stats){
 const projects=(stats?.projects||[]).filter(project=>typeof project==='string'&&project);
 if(modelProject&&!projects.includes(modelProject)){
  modelProject='';modelFilters=loadModelFilters('');
  localStorage.removeItem('tm_model_project');
 }
 const projectSelect=$('m-project'),projectOptions='<option value="">All projects</option>'+projects.map(project=>`<option value="${esc(project)}">${esc(projectFilterLabel(project))}</option>`).join('');
 setLogSelectOptions(projectSelect,projectOptions,modelProject);
 const projectTitle=stats?.projects_truncated?'Showing the first 500 discovered projects.':'';
 if(projectSelect.title!==projectTitle)projectSelect.title=projectTitle;
}
function setModelProjectLoading(loading){
 const projectSelect=$('m-project');
 if(loading)projectSelect.setAttribute('aria-busy','true');
 else projectSelect.removeAttribute('aria-busy');
}
function commitModelStats(stats,scope,generation){
 activeModelStats=stats;activeModelScope=scope;activeModelGeneration=generation;
 setModelProjectLoading(false);
 $('m-project-status').textContent='';$('m-project-status').classList.remove('warn');
 renderModelStats(stats);
}
async function renderActiveModelStats(){
 if(!LATEST)return;
 const machine=LATEST.xsession?.model_stats||{models:[],projects:[]},generation=Number(LATEST.xsession?.generated_at||0);
 paintModelProjects(machine);
 if(!modelProject){
  modelProjectRequest++;modelProjectLoadingKey='';
  setModelProjectLoading(false);
  commitModelStats(machine,'',generation);
  return;
 }
 const project=modelProject,key=`${generation}\n${project}`,cached=modelProjectCache.get(key);
 if(cached){commitModelStats(cached,project,generation);return;}
 if(modelProjectLoadingKey===key)return;
 $('m-project-status').textContent='';
 $('m-project-status').classList.remove('warn');
 setModelProjectLoading(true);
 modelProjectLoadingKey=key;
 const request=++modelProjectRequest;
 try{
  const response=await fetch('/model-stats?project='+encodeURIComponent(project),{cache:'no-store'});
  const payload=await response.json();
  if(!response.ok||!payload.ok)throw new Error('Project model stats are unavailable.');
  if(request!==modelProjectRequest||project!==modelProject)return;
  const responseGeneration=Number(payload.generated_at||generation);
  modelProjectCache.set(key,payload.model_stats);
  modelProjectCache.set(`${responseGeneration}\n${project}`,payload.model_stats);
  commitModelStats(payload.model_stats,project,responseGeneration);
 }catch(error){
  if(request!==modelProjectRequest||project!==modelProject)return;
  $('m-project-status').textContent=`Could not load ${projectFilterLabel(project)}${activeModelStats?` · still showing ${modelScopeLabel(activeModelScope)}`:''}`;
  $('m-project-status').classList.add('warn');
 }finally{
  if(modelProjectLoadingKey===key){modelProjectLoadingKey='';setModelProjectLoading(false);}
 }
}
function rerenderActiveModelStats(){
 if(activeModelStats&&activeModelScope===modelProject)renderModelStats(activeModelStats);
 else renderActiveModelStats();
}
$('m-project').addEventListener('change',event=>{
 modelProject=event.target.value;
 modelFilters=loadModelFilters(modelProject);
 if(modelProject)localStorage.setItem('tm_model_project',modelProject);else localStorage.removeItem('tm_model_project');
 modelProjectRequest++;modelProjectLoadingKey='';
 renderActiveModelStats();
});
$('m-model-options').addEventListener('change',event=>{
 const input=event.target.closest('input[data-model-choice]');if(!input)return;
 if(input.dataset.modelChoice==='all')modelFilters=[];
 else if(input.checked)modelFilters=[...new Set([...modelFilters,input.value])];
 else modelFilters=modelFilters.filter(name=>name!==input.value);
 saveActiveModelFilters();
 if(LATEST)rerenderActiveModelStats();
});
$('m-model-picker').addEventListener('keydown',event=>{if(event.key==='Escape')$('m-model-picker').removeAttribute('open');});
document.addEventListener('pointerdown',event=>{const picker=$('m-model-picker');if(picker.open&&!picker.contains(event.target))picker.removeAttribute('open');});
function positionModelPickerMenu(){
 const picker=$('m-model-picker'),menu=$('m-model-options');
 if(!picker.open){picker.classList.remove('opensUp');menu.style.maxHeight='';return;}
 const trigger=picker.querySelector('summary'),rect=trigger.getBoundingClientRect(),margin=12;
 const above=Math.max(0,rect.top-margin),below=Math.max(0,window.innerHeight-rect.bottom-margin),preferred=Math.min(360,menu.scrollHeight||360);
 const opensUp=below<preferred&&above>below,available=opensUp?above:below;
 picker.classList.toggle('opensUp',opensUp);
 menu.style.maxHeight=`${Math.min(360,available)}px`;
}
$('m-model-picker').addEventListener('toggle',()=>requestAnimationFrame(positionModelPickerMenu));
window.addEventListener('resize',()=>{if($('m-model-picker').open)positionModelPickerMenu();});
$('m-mix-metric').onclick=event=>{const metric=event.target.closest('[data-model-mix]')?.dataset.modelMix;if(!MODEL_MIX_KEYS.includes(metric))return;modelMixMetric=metric;localStorage.setItem('tm_model_mix_metric',metric);if(LATEST)rerenderActiveModelStats();};
$('m-table').querySelector('thead').onclick=event=>{const key=event.target.closest('[data-model-sort]')?.dataset.modelSort;if(!MODEL_SORT_KEYS.includes(key))return;modelSort=modelSort.key===key?{key,direction:modelSort.direction==='asc'?'desc':'asc'}:{key,direction:key==='model'?'asc':'desc'};localStorage.setItem('tm_model_sort',JSON.stringify(modelSort));if(LATEST)rerenderActiveModelStats();};
$('m-table').querySelector('tbody').onclick=event=>{const button=event.target.closest('[data-model-expand]');if(!button)return;const key=button.dataset.modelExpand;if(modelExpandedGroups.has(key))modelExpandedGroups.delete(key);else modelExpandedGroups.add(key);if(LATEST)rerenderActiveModelStats();};
$('m-spend').onclick=event=>{
 if(event.target.closest('[data-model-clear]')){modelFilters=[];saveActiveModelFilters();if(LATEST)rerenderActiveModelStats();return;}
 const ids=event.target.closest('[data-model-ids]')?.dataset.modelIds;if(!ids)return;
 modelFilters=ids.split('\n').filter(Boolean);saveActiveModelFilters();if(LATEST)rerenderActiveModelStats();
};
$('m-speedchart-options').addEventListener('change',event=>{
 const input=event.target.closest('input[data-model-speed-choice]');if(!input)return;
 const boxes=[...$('m-speedchart-options').querySelectorAll('input[data-model-speed-choice]')],visible=new Set(boxes.map(box=>box.dataset.modelSpeedChoice));
 const hidden=(modelSpeedSelection||[]).filter(key=>!visible.has(key)),checked=boxes.filter(box=>box.checked).map(box=>box.dataset.modelSpeedChoice);
 const next=[...new Set([...checked,...hidden])].slice(0,MODEL_SPEED_MAX_SERIES);
 modelSpeedSelection=checked.length?next:null;
 if(modelSpeedSelection)localStorage.setItem('tm_model_speed_models',JSON.stringify(modelSpeedSelection));else localStorage.removeItem('tm_model_speed_models');
 if(LATEST)rerenderActiveModelStats();
});
$('m-speedchart-reset').onclick=()=>{modelSpeedSelection=null;localStorage.removeItem('tm_model_speed_models');if(LATEST)rerenderActiveModelStats();};
$('m-speedchart-picker').addEventListener('keydown',event=>{if(event.key==='Escape')$('m-speedchart-picker').removeAttribute('open');});
document.addEventListener('pointerdown',event=>{const picker=$('m-speedchart-picker');if(picker.open&&!picker.contains(event.target))picker.removeAttribute('open');});
$('m-diagnostics').open=localStorage.getItem('tm_model_diagnostics_open')==='1';
$('m-diagnostics').addEventListener('toggle',()=>localStorage.setItem('tm_model_diagnostics_open',$('m-diagnostics').open?'1':'0'));
$('m-range').addEventListener('change',e=>{modelRange=e.target.value;localStorage.setItem('tm_model_range',modelRange);if(LATEST)rerenderActiveModelStats();});
$('m-metric').onclick=event=>{const metric=event.target.dataset.modelMetric;if(!metric)return;modelTrendMetric=metric;localStorage.setItem('tm_model_trend_metric',metric);if(LATEST)rerenderActiveModelStats();};
function paintGlobalSort(){
 [...$('g-sort').querySelectorAll('button')].forEach(b=>b.classList.toggle('on',b.dataset.gsort===globalSort));
 const label={recent:'recent activity',cost:'highest cost',tokens:'most tokens',executions:'most executions'}[globalSort]||'recent activity';
 $('g-hint').textContent=label;
}
function paintGlobalChildren(){
 [...$('g-children').querySelectorAll('button')].forEach(b=>{
  const on=(b.dataset.gchildren==='show')===globalShowChildren;
  b.classList.toggle('on',on);
  b.setAttribute('aria-pressed',on?'true':'false');
 });
}
$('g-children').onclick=event=>{
 const button=event.target.closest('[data-gchildren]');
 if(!button)return;
 globalShowChildren=button.dataset.gchildren==='show';
 localStorage.setItem('tm_global_children',globalShowChildren?'show':'hide');
 paintGlobalChildren();
 if(LATEST)renderAllSessions(LATEST.xsession);
};
$('g-search').value=globalSearch;
$('g-time').value=globalTime;
paintGlobalChildren();
function paintGlobalFilters(){
 const disabled=!(globalSearch.trim()||globalApp||globalProject||globalTime!==DEFAULT_TIME_FILTER_WINDOW);
 if($('g-clear').disabled!==disabled)$('g-clear').disabled=disabled;
}
$('g-search').addEventListener('input',e=>{ globalSearch=e.target.value; localStorage.setItem('tm_global_search',globalSearch); paintGlobalFilters(); if(LATEST) renderAllSessions(LATEST.xsession); });
$('g-app').addEventListener('change',e=>{ globalApp=e.target.value; localStorage.setItem('tm_global_app',globalApp); paintGlobalFilters(); if(LATEST) renderAllSessions(LATEST.xsession); });
$('g-project').addEventListener('change',e=>{ globalProject=e.target.value; localStorage.setItem('tm_global_project',globalProject); paintGlobalFilters(); if(LATEST) renderAllSessions(LATEST.xsession); });
$('g-time').addEventListener('change',e=>{ globalTime=e.target.value; localStorage.setItem('tm_global_time',globalTime); paintGlobalFilters(); if(LATEST) renderAllSessions(LATEST.xsession); });
$('g-clear').onclick=()=>{
 globalSearch='';globalApp='';globalProject='';globalTime=DEFAULT_TIME_FILTER_WINDOW;
 $('g-search').value='';$('g-app').value='';$('g-project').value='';$('g-time').value=DEFAULT_TIME_FILTER_WINDOW;
 ['tm_global_search','tm_global_app','tm_global_project','tm_global_time'].forEach(key=>localStorage.removeItem(key));
 paintGlobalFilters();
 if(LATEST)renderAllSessions(LATEST.xsession);
};
$('g-active-filters').onclick=event=>{
 const key=event.target.closest('button[data-clear-log-filter]')?.dataset.clearLogFilter;
 if(!key)return;
 if(key==='search'){globalSearch='';$('g-search').value='';localStorage.removeItem('tm_global_search');}
 if(key==='app'){globalApp='';$('g-app').value='';localStorage.removeItem('tm_global_app');}
 if(key==='project'){globalProject='';$('g-project').value='';localStorage.removeItem('tm_global_project');}
 if(key==='time'){globalTime=DEFAULT_TIME_FILTER_WINDOW;$('g-time').value=DEFAULT_TIME_FILTER_WINDOW;localStorage.removeItem('tm_global_time');}
 if(key==='work'){workSessionFilter=null;workFilterRequest++;}
 paintGlobalFilters();
 if(LATEST)renderAllSessions(LATEST.xsession);
};
$('g-sort').onclick=e=>{ if(e.target.dataset.gsort){ globalSort=e.target.dataset.gsort; localStorage.setItem('tm_global_sort',globalSort); paintGlobalSort(); if(LATEST) renderAllSessions(LATEST.xsession); } };
paintGlobalSort();
paintGlobalFilters();

