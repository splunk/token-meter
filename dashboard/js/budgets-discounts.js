const DEFAULT_RUNTIME_BUDGET=0;
let defaultSessionBudget=10;
let budgetSettingsDirty=false;
function budgetNumber(id){const raw=$(id).value.trim();return raw===''?0:Number(raw);}
function budgetRuntimeIds(){
 const catalogIds=Object.entries(RUNTIME_CATALOG).filter(([id])=>id!=='unknown-runtime').map(([id])=>id);
 return catalogIds.length?catalogIds:[...document.querySelectorAll('.budgetRuntime[data-runtime-id]')].map(row=>row.dataset.runtimeId);
}
function ensureBudgetRuntimeRows(){
 const list=$('budget-runtimes');
 budgetRuntimeIds().forEach(provider=>{
  if($(`budget-runtime-${provider}`))return;
  const label=runtimeLabel({provider});
  list.insertAdjacentHTML('beforeend',`<div class=budgetRuntime id="budget-runtime-${esc(provider)}" data-runtime-id="${esc(provider)}"><span class=budgetRuntimeName>${esc(label)}</span><span class=budgetRuntimeValue><b class=mono id="budget-runtime-spend-${esc(provider)}">$0.00</b><small id="budget-runtime-share-${esc(provider)}">0% of spend</small></span><span class=budgetRuntimeTrack id="budget-runtime-track-${esc(provider)}"><i></i></span><label class=budgetRuntimeInput><span>Budget</span><input id="budget-input-${esc(provider)}" aria-label="${esc(label)} budget" type=number min=0 max=100000000 step=any value=0></label></div>`);
 });
}
function parsedBudgetThresholds(){
 const values=[...document.querySelectorAll('[data-budget-threshold]')].map(input=>Number(input.value.trim())).filter(value=>Number.isFinite(value));
 return [...new Set(values)].sort((a,b)=>a-b);
}
function paintBudgetSettings(settings){
  settings=settings||{default_session_budget:10,monthly_total:0,allocations:{},thresholds:[80,90,100],native_notifications:true};
  if(budgetSettingsDirty)return;
 defaultSessionBudget=Number(settings.default_session_budget||10);
 $('budget-input-session-default').value=formatBudgetInput(defaultSessionBudget);
 ensureBudgetRuntimeRows();
 budgetRuntimeIds().forEach(provider=>{$(`budget-input-${provider}`).value=Number(settings.allocations?.[provider]??DEFAULT_RUNTIME_BUDGET);});
 const thresholds=settings.thresholds||[80,90,100];
 [$('budget-input-threshold-early'),$('budget-input-threshold-mid'),$('budget-input-threshold-full')].forEach((input,index)=>{input.value=thresholds[index]??[80,90,100][index];});
 $('budget-input-notifications').checked=settings.native_notifications!==false;
}
function budgetAlertState(){
 try{const value=JSON.parse(localStorage.getItem('tm_monthly_budget_alerts')||'{}');return value&&typeof value==='object'?value:{};}catch(error){return {};}
}
function budgetExceededAlertState(){
 try{const value=JSON.parse(localStorage.getItem('tm_monthly_budget_exceeded_alerts')||'{}');return value&&typeof value==='object'?value:{};}catch(error){return {};}
}
function observeBudgetThresholds(status){
 if(!status?.configured)return;
 const state=budgetAlertState(),crossed=status.thresholds_crossed||[],previous=state[status.month];
 const seen=Array.isArray(previous)?previous:(Array.isArray(previous?.thresholds)?previous.thresholds:[]);
 const firstObservation=previous===undefined,over=Number((status.alert_percent??status.percent)||0)>=1;
 const exceededState=budgetExceededAlertState(),monthExceeded=exceededState[status.month]||{};
 const inAppNotified=monthExceeded.inApp===true,browserNotified=monthExceeded.browser===true;
 const fresh=crossed.filter(value=>!seen.includes(value));
 const alertExceeded=over&&!inAppNotified;
 const browserReady=notifyOn&&canNotify&&Notification.permission==='granted';
 const deliverMissedBrowserAlert=over&&!browserNotified&&browserReady;
 const threshold=alertExceeded||deliverMissedBrowserAlert?100:(!firstObservation&&fresh.length?fresh[fresh.length-1]:null);
 const lower=status.lower_bound?'At least ':'',isExceeded=alertExceeded||deliverMissedBrowserAlert;
 if(threshold!==null){
  const title=isExceeded?'Monthly budget exceeded':`Monthly budget reached ${threshold}%`;
  const body=isExceeded
   ? `${lower}${money(status.spend)} of ${money(status.budget)} recorded for ${status.month} (${pct(status.percent)} used).`
   : `${lower}${money(status.spend)} of ${money(status.budget)} recorded for ${status.month}.`;
  if(browserReady)fireNotification(isExceeded?'Token Meter budget exceeded':'Token Meter monthly budget',body,`monthly:${status.month}:${isExceeded?'over':threshold}`,{dedupe:true,requireInteraction:isExceeded});
  else pushAppNotice(title,body,isExceeded?'warn':'neutral',{sticky:isExceeded,status:title});
 }
 state[status.month]=[...new Set([...seen,...crossed])];
 localStorage.setItem('tm_monthly_budget_alerts',JSON.stringify(state));
 exceededState[status.month]={
  inApp:inAppNotified||alertExceeded,
  browser:browserNotified||deliverMissedBrowserAlert,
 };
 localStorage.setItem('tm_monthly_budget_exceeded_alerts',JSON.stringify(exceededState));
}
function renderCurrentBudgetWarning(status){
 const banner=$('current-budget-warning'),rows=status?.exceeded_runtimes||[];
 banner.classList.toggle('on',rows.length>0);
 if(!rows.length){$('current-budget-warning-msg').textContent='';return;}
 const lower=status.lower_bound?'At least ':'';
 $('current-budget-warning-msg').textContent=rows.map(row=>{
  const over=Number(row.over_by||0),verb=over>0?'exceeded':'reached';
  return `⚠︎ ${row.label} monthly budget ${verb}: ${lower}${money(row.spend||0)} spent against ${money(row.allocation||0)} (${pct(row.percent||0)} used).`;
 }).join(' ');
}
$('current-budget-settings').onclick=()=>setHashRoute('spend-budgets');
function budgetMonthLabel(month){
 const match=/^(\d{4})-(\d{2})$/.exec(String(month||''));
 if(!match)return 'This month';
 return new Date(Number(match[1]),Number(match[2])-1,1).toLocaleDateString(undefined,{month:'long',year:'numeric'});
}
function renderBudgets(xs){
 if(!xs)return;
 const status=xs.budget||{},settings=xs.budgets||status.settings||{};
 const configured=!!status.configured,lower=status.lower_bound?'At least ':'',allocations=settings.allocations||{};
 const runtimeExceeded=status.exceeded_runtimes||[],runtimeAttention=runtimeExceeded.length>0;
 const allocatedTotal=Object.values(allocations).reduce((sum,value)=>sum+Number(value||0),0);
 $('budget-spend').textContent=wholeMoney(status.spend||0);
 $('budget-spend-qualifier').hidden=!status.lower_bound;
 $('budget-month').textContent=budgetMonthLabel(status.month);
 $('budget-spend-note').textContent=`${status.active_days||0} active spend ${countWord(status.active_days||0,'day')}`;
 $('budget-total').textContent=configured?wholeMoney(status.budget):'Not set';
 const runtimeNames=budgetRuntimeIds().map(provider=>runtimeLabel({provider})).join(' + ');
 $('budget-total').title=allocatedTotal?runtimeNames:'';
 $('budget-total-note').textContent=allocatedTotal?`across ${Object.values(allocations).filter(value=>Number(value)>0).length} runtime budgets`:configured?'Legacy total · set runtime budgets to replace':'Add runtime budgets to enable tracking';
 $('budget-remaining').textContent=configured?wholeMoney(status.remaining||0):'--';
 $('budget-remaining').className=`v mono${configured&&Number(status.percent||0)>=.8?' warn':''}`;
 $('budget-remaining-note').textContent=status.lower_bound?'Coverage is partial; true remaining may be lower':configured?`${status.days_remaining||0} calendar days left`:'No monthly limit';
 $('budget-projected').textContent=status.projection_ready?wholeMoney(status.projected_spend):'--';
 $('budget-projected').className=`v mono${status.projection_ready&&status.projected_spend>status.budget?' warn':''}`;
 $('budget-projected-note').textContent=status.projection_ready?`${wholeMoney(Math.abs(status.projected_spend-status.budget))} ${status.projected_spend>status.budget?'over':'under'} budget at current pace`:`Shown after ${status.projection_min_active_days||3} active spend days`;
 const ratio=configured?Math.max(0,Number(status.percent||0)):0,progress=$('budget-progress');
 progress.querySelector('i').style.width=Math.min(100,100*ratio)+'%';progress.setAttribute('aria-valuenow',String(Math.round(100*ratio)));progress.classList.toggle('warn',ratio>=.8);
 $('budget-progress-markers').innerHTML=configured?(settings.thresholds||[]).filter(value=>value>0&&value<=100).map(value=>`<span style="left:${value}%"></span>`).join(''):'';
 const attentionState=runtimeAttention?'over':(status.state||'unconfigured');
 const stateLabel=runtimeAttention?`${runtimeExceeded.map(row=>row.label).join(', ')} over budget`:{unconfigured:'Not configured',on_track:'On track',warning:'Threshold crossed',over:'Budget exceeded'}[status.state]||'Current month';
 $('budget-state').textContent=stateLabel;
 $('budget-state').className=`agentAccessBadge chip budgetState ${attentionState}`;
 budgetConfigured=configured;
 paintBudgetEditorToggle();
 $('budget-current-note').textContent=runtimeAttention
  ? runtimeExceeded.map(row=>Number(row.over_by||0)>0
    ? `${row.label} ${pct(row.percent||0)} used · ${wholeMoney(row.over_by||0)} over its runtime budget`
    : `${row.label} ${pct(row.percent||0)} used · runtime budget reached`).join(' · ')
  : configured?`${pct(ratio)} used · ${status.next_threshold?`${status.next_threshold}% next threshold`:'all thresholds crossed'}`:'Add runtime budgets to start tracking';
 $('budget-coverage').className=`budgetCoverage${status.lower_bound?' warn':''}`;
 const alertExcluded=Number(status.alert_excluded_spend||0);
 $('budget-coverage').textContent=(status.lower_bound?'Partial cost coverage: recorded spend is a lower bound.':status.estimated?'Includes labeled local cost estimates.':'')+(alertExcluded>0?` Antigravity length-based estimates (${money(alertExcluded)}) are shown but don't trigger budget notifications.`:'');
 const runtimes=status.runtimes||[],hasAllocations=runtimes.some(row=>Number(row.allocation||0)>0),runtimeSpend=Math.max(0,Number(status.spend||0));
 $('budget-allocation-note').textContent=hasAllocations?`${lower}${wholeMoney(status.spend||0)} / ${wholeMoney(status.budget)}`:'Add runtime budgets';
 ensureBudgetRuntimeRows();
  const runtimeRows=new Map(runtimes.map(row=>[row.provider,row]));
  budgetRuntimeIds().forEach(provider=>{
  const row=runtimeRows.get(provider)||{provider,spend:0,allocation:Number(allocations[provider]||0)};
  const spend=Number(row.spend||0),allocated=Number(row.allocation||0),percent=allocated?spend/allocated:(runtimeSpend?spend/runtimeSpend:0),over=row.exceeded===true||Boolean(allocated&&spend>=allocated);
  const shareLabel=allocated?`${pct(percent)} of budget`:`${pct(percent)} of spend`;
  const color=over?'var(--bad)':providerColor(row.provider);
  const runtime=$(`budget-runtime-${provider}`);
  runtime.style.setProperty('--provider-color',color);
  $(`budget-runtime-spend-${provider}`).textContent=wholeMoney(spend);
  $(`budget-runtime-share-${provider}`).textContent=shareLabel;
  $(`budget-runtime-track-${provider}`).querySelector('i').style.width=`${Math.min(100,100*percent)}%`;
 });
 paintBudgetSettings(settings);
 observeBudgetThresholds(status);
}
let settingsPointerActive=false;
const settingsView=$('view-settings');
settingsView.addEventListener('pointerdown',()=>{settingsPointerActive=true;});
$('spend-budgets').addEventListener('pointerdown',()=>{settingsPointerActive=true;});
window.addEventListener('pointerup',()=>{settingsPointerActive=false;});
window.addEventListener('pointercancel',()=>{settingsPointerActive=false;});
function settingsInteractionActive(){return viewInteractionActive($('view-settings'));}
function budgetInteractionActive(){return viewInteractionActive($('spend-budgets'));}
function viewInteractionActive(root){
 const active=document.activeElement,selection=window.getSelection?.();
 const editing=active&&root.contains(active)&&active.matches('input,textarea,select,[contenteditable=true]');
 const selecting=selection&&!selection.isCollapsed&&selection.rangeCount&&(
  (selection.anchorNode&&root.contains(selection.anchorNode))||(selection.focusNode&&root.contains(selection.focusNode))
 );
 return settingsPointerActive||Boolean(editing)||Boolean(selecting);
}
function renderSettings(xs){
 if(!xs||settingsInteractionActive())return;
 renderModelPricing(xs.model_pricing);
}
function renderSpendView(xs){
 renderSpend(xs);
 if(xs&&!budgetInteractionActive())renderBudgets(xs);
 if(xs?.cost_discounts&&!discountEditInProgress())renderDiscounts(xs.cost_discounts);
}
let budgetConfigured=false;
function paintBudgetEditorToggle(){
 const planJump=$('budget-plan-jump'),open=!$('budget-config').hidden;
 planJump.textContent=open?'Close editor':budgetConfigured?'Edit budgets':'Set budgets';
 planJump.setAttribute('aria-expanded',String(open));
 planJump.setAttribute('aria-label',open?'Close runtime budget editor':budgetConfigured?'Edit runtime budgets':'Set runtime budgets');
}
function setBudgetEditorOpen(open,focus=false){
 const config=$('budget-config');
 config.hidden=!open;
 $('spend-discounts').hidden=!open;
 paintBudgetEditorToggle();
 if(!open)return;
 config.classList.remove('attention');
 void config.offsetWidth;
 config.classList.add('attention');
 setTimeout(()=>config.classList.remove('attention'),1400);
 if(!focus)return;
 const input=config.querySelector('.budgetRuntimeInput input');
 config.scrollIntoView({behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'nearest'});
 input.focus({preventScroll:true});
 if(input.value)input.select();
}
$('budget-plan-jump').onclick=()=>setBudgetEditorOpen($('budget-config').hidden,true);
const DISCOUNT_LISTS={anthropic:['Anthropic','claude'],openai:['OpenAI','codex'],cursor:['Cursor','cursor'],kiro:['Kiro','kiro'],opencode:['OpenCode','opencode']};
const DISCOUNT_BILLED={direct:'The provider',bedrock:'AWS Bedrock',vertex:'Google Vertex',azure:'Microsoft Azure',any:'Anyone'};
const DISCOUNT_OWN_LISTS=new Set(['cursor','kiro','opencode']);
const DISCOUNT_CUE_KEY='tm_discount_cue_dismissed';
let discountTab='all',discountSettings=null,discountPollSeq=0;
function discountRuleState(rule,now=Date.now()/1000){
 if(rule.end!=null&&rule.end<=now)return 'ended';
 if(rule.start!=null&&rule.start>now)return 'starts later';
 return 'active';
}
function discountActiveRules(){return (discountSettings?.rules||[]).filter(rule=>discountRuleState(rule)==='active');}
function discountDay(seconds){return new Date(seconds*1000).toLocaleDateString(undefined,{year:'numeric',month:'short',day:'numeric'});}
// A date input is a local calendar day: send its local midnight with this browser's offset.
function discountLocalMidnightIso(day){
 if(!day)return null;
 const offset=-new Date(`${day}T00:00`).getTimezoneOffset(),sign=offset<0?'-':'+',abs=Math.abs(offset);
 return `${day}T00:00:00${sign}${String(Math.floor(abs/60)).padStart(2,'0')}:${String(abs%60).padStart(2,'0')}`;
}
function discountSay(text,tone=''){const status=$('discount-status');status.className='settingsStatus'+(tone?' '+tone:'');status.textContent=text;}
// Rendering never touches the add row's inputs, so only a half-typed discount pauses it.
let discountFormDirty=false;
function discountEditInProgress(){
 return discountFormDirty&&Boolean(discountSettings)&&viewInteractionActive($('spend-discounts'));
}
function openDiscounts(){
 const settings=LATEST?.cost_discounts||LATEST?.xsession?.cost_discounts;
 if(settings)renderDiscounts(settings);
 setBudgetEditorOpen(true);
 requestAnimationFrame(()=>{$('spend-discounts').scrollIntoView({block:'start',behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});$('d-pct').focus({preventScroll:true});});
}
let discountCueSignature='',discountCardSignature='';
// Live refreshes re-render only when what a reader sees would change, and keep keyboard focus.
function discountFocusKey(){
 const active=document.activeElement;
 if(!active?.dataset)return '';
 return active.dataset.dremove?`dremove:${active.dataset.dremove}`:active.dataset.dtab?`dtab:${active.dataset.dtab}`:active.hasAttribute('data-open-discounts')?'open':'';
}
function restoreDiscountFocus(key){
 if(!key)return;
 const [kind,value]=key.split(':');
 const target=kind==='dremove'?document.querySelector(`[data-dremove="${CSS.escape(value)}"]`):kind==='dtab'?document.querySelector(`[data-dtab="${CSS.escape(value)}"]`):document.querySelector('#discount-cue [data-open-discounts]');
 target?.focus({preventScroll:true});
}
function renderDiscountCue(){
 const cue=$('discount-cue'),active=discountActiveRules();
 if(active.length)localStorage.removeItem(DISCOUNT_CUE_KEY);
 const hidden=!discountSettings||(!active.length&&localStorage.getItem(DISCOUNT_CUE_KEY)==='1');
 const signature=JSON.stringify([hidden,active.map(rule=>[rule.id,rule.price_list,rule.percent])]);
 if(signature===discountCueSignature)return;
 discountCueSignature=signature;
 if(hidden){cue.hidden=true;return;}
 const focus=discountFocusKey();
 cue.hidden=false;cue.classList.toggle('active',active.length>0);
 cue.innerHTML=active.length
  ?`<div class=discountCueText><b>Spend includes your discounts</b><span class=discountCueList>${active.map(rule=>`<span class="badge ${(DISCOUNT_LISTS[rule.price_list]||['','other'])[1]}">${esc((DISCOUNT_LISTS[rule.price_list]||[rule.price_list])[0])} ${esc(rule.percent)}%</span>`).join('')}<span>Costs before discounts are shown beside each total.</span></span></div><div class=discountCueActions><button class=tbtn type=button data-open-discounts>Manage discounts</button></div>`
  :`<div class=discountCueText><b>Spend uses public list prices.</b><span>Pay a negotiated or company rate? Add a discount so spend and budgets match your bill.</span></div><div class=discountCueActions><button class=discountLink type=button data-dismiss-discounts>Not now</button><button class="tbtn go" type=button data-open-discounts>Add a discount</button></div>`;
 cue.querySelector('[data-open-discounts]').onclick=openDiscounts;
 const dismiss=cue.querySelector('[data-dismiss-discounts]');
 if(dismiss)dismiss.onclick=()=>{localStorage.setItem(DISCOUNT_CUE_KEY,'1');renderDiscountCue();};
 restoreDiscountFocus(focus);
}
// After Remove, focus moves to the next row's Remove button, else the previous one, else the heading.
async function removeDiscount(button){
 const buttons=[...$('discount-rows').querySelectorAll('[data-dremove]')],index=buttons.indexOf(button);
 const neighbours=[buttons[index+1],buttons[index-1]].filter(Boolean).map(item=>item.dataset.dremove);
 const removed=await saveDiscountOps([{op:'delete',id:button.dataset.dremove}],button);
 if(!removed)return;
 const next=neighbours.map(id=>document.querySelector(`[data-dremove="${CSS.escape(id)}"]`)).find(Boolean);
 (next||$('discount-title')).focus({preventScroll:true});
}
function discountRuleName(rule){
 return `${(DISCOUNT_LISTS[rule.price_list]||[rule.price_list])[0]} ${rule.percent}% discount`;
}
function renderDiscounts(settings){
 if(settings)discountSettings=settings;
 const rules=discountSettings?.rules||[];
 const invalid=Number(discountSettings?.invalid_rules||0),readOnly=discountSettings?.read_only===true;
 const signature=JSON.stringify([discountSettings?.revision,discountTab,invalid,readOnly,rules.map(rule=>[rule.id,discountRuleState(rule)])]);
 if(signature!==discountCardSignature){
  discountCardSignature=signature;
  const focus=discountFocusKey();
  const counts={all:rules.length};
  rules.forEach(rule=>{counts[rule.price_list]=(counts[rule.price_list]||0)+1;});
  $('discount-tabs').innerHTML=['all',...Object.keys(DISCOUNT_LISTS)].map(id=>`<button type=button role=tab aria-selected=${id===discountTab} data-dtab=${id}>${id==='all'?'All':DISCOUNT_LISTS[id][0]}<span class=count>${counts[id]||0}</span></button>`).join('');
  $('discount-tabs').querySelectorAll('[data-dtab]').forEach(button=>button.onclick=()=>{discountTab=button.dataset.dtab;renderDiscounts();});
  const shown=rules.filter(rule=>discountTab==='all'||rule.price_list===discountTab);
  $('discount-rows').innerHTML=shown.length?shown.map(rule=>{
   const list=DISCOUNT_LISTS[rule.price_list]||[rule.price_list,'other'];
   return `<tr><td data-label="Models from"><span class="badge ${list[1]}">${esc(list[0])}</span></td><td data-label="Billed by">${esc(DISCOUNT_OWN_LISTS.has(rule.price_list)?list[0]:DISCOUNT_BILLED[rule.channel]||rule.channel)}</td><td class="num mono" data-label="Discount">${esc(rule.percent)}%</td><td data-label="From">${rule.start!=null?esc(discountDay(rule.start)):'All past usage'}</td><td data-label="Until">${rule.end!=null?esc(discountDay(rule.end)):'No end'}</td><td data-label="Note"><div class=modelPriceModel>${esc(rule.label||'—')}<span class=modelPriceSource>${discountRuleState(rule)}</span><button class=modelPriceLifecycle type=button data-dremove="${esc(rule.id)}" aria-label="${esc(`Remove ${discountRuleName(rule)}`)}"${readOnly?' disabled':''}>Remove</button></div></td><td data-label="Set by"><span class=chip>${esc(rule.set_by||'')}</span></td></tr>`;
  }).join(''):'<tr><td colspan=7 class=modelPricingEmpty>No discounts yet. Costs use public list prices.</td></tr>';
  $('discount-rows').querySelectorAll('[data-dremove]').forEach(button=>button.onclick=()=>removeDiscount(button));
  const active=discountActiveRules().length;
  $('discount-badge').textContent=active?`${active} active`:'List prices';
  const notes=[];
  if(invalid)notes.push(`${invalid} saved ${invalid===1?'discount is':'discounts are'} invalid and ignored`);
  if(readOnly)notes.push(discountSettings?.read_only_reason==='newer_version'?'Discounts were saved by a newer Token Meter. Update Token Meter to change them.':"Discounts in settings.json can't be changed by this version.");
  $('discount-invalid').hidden=!notes.length;$('discount-invalid').textContent=notes.join(' · ');
  $('d-add').disabled=readOnly;
  document.querySelectorAll('[data-runtime-channel]').forEach(select=>{select.disabled=readOnly;});
  restoreDiscountFocus(focus);
 }
 const channels=discountSettings?.runtime_channels||{};
 document.querySelectorAll('[data-runtime-channel]').forEach(select=>{if(document.activeElement!==select)select.value=channels[select.dataset.runtimeChannel]||'auto';});
 renderDiscountCue();
}
async function pollPricingRevision(target){
 const seq=++discountPollSeq,deadline=Date.now()+10*60*1000;
 while(seq===discountPollSeq&&Date.now()<deadline){
  await new Promise(resolve=>setTimeout(resolve,1500));
  try{
   const health=await (await fetch('/health',{cache:'no-store'})).json();
   if(health.pricing_revision===target){
    if(seq!==discountPollSeq)return;
    discountSay('All costs were recalculated.','good');
    await refreshLiveState();
    if(LATEST?.xsession)renderSpendView(LATEST.xsession);
    return;
   }
  }catch(_error){/* The server may be busy recalculating; keep polling. */}
 }
}
// /state carries discounts read fresh from settings; the snapshot's copy lags while history reprices.
function adoptFreshDiscounts(state){
 if(!state?.cost_discounts)return;
 if(state.xsession)state.xsession.cost_discounts=state.cost_discounts;
 else if(!discountEditInProgress())renderDiscounts(state.cost_discounts);
}
// Typing pauses live renders; clearing every add-row field again resumes them.
function discountAddRowHasInput(){return DISCOUNT_ADD_FIELDS.some(id=>String($(id)?.value||'').trim()!=='');}
const DISCOUNT_ADD_FIELDS=['d-pct','d-start','d-end','d-note'];
$('discount-form').addEventListener('input',()=>{discountFormDirty=discountAddRowHasInput();});
async function reloadDiscounts(){
 const token=agentActionToken();
 if(!token)return;
 try{
  const response=await fetch('/api/v1/cost-discounts',{headers:{'X-Token-Meter-Action':token},cache:'no-store'});
  const settings=await response.json();
  if(response.ok&&settings.ok){if(LATEST)LATEST.cost_discounts=settings;renderDiscounts(settings);}
 }catch(_error){/* Keep the current view; the next live refresh carries fresh discounts. */}
}
async function saveDiscountOps(ops,button=null){
 const token=agentActionToken();
 if(!token){discountSay('Settings are not ready yet. Wait for live state and try again.','warn');return false;}
 const original=button?.textContent;
 if(button){button.disabled=true;button.textContent='Saving…';}
 try{
  const response=await fetch('/settings/cost-discounts',{method:'POST',headers:{'Content-Type':'application/json','X-Token-Meter-Action':token},body:JSON.stringify({ops,expected_revision:discountSettings?.revision})});
  const result=await response.json().catch(()=>({}));
  if(!response.ok||!result.ok){
   discountSay(result.error||'The discount could not be saved.','warn');
   if(response.status===409)await reloadDiscounts();
   return false;
  }
  discountSettings={...(discountSettings||{}),rules:result.rules,revision:result.revision};
  if(LATEST?.xsession)LATEST.xsession.cost_discounts=discountSettings;
  if(LATEST)LATEST.cost_discounts=discountSettings;
  renderDiscounts();
  if(!result.changed){discountSay('No changes to save.');return true;}
  discountSay('Discount saved. Costs are being recalculated.');
  pollPricingRevision(result.pricing_revision);
  return true;
 }catch(error){discountSay(error.message||String(error),'warn');return false;}
 finally{if(button){button.disabled=false;button.textContent=original;}}
}
$('d-list').onchange=()=>{const own=DISCOUNT_OWN_LISTS.has($('d-list').value);$('d-channel').disabled=own;if(own)$('d-channel').value='any';};
$('d-add').onclick=async()=>{
 const percent=Number($('d-pct').value),start=$('d-start').value,end=$('d-end').value;
 if(!(percent>0&&percent<100)){discountSay('Enter a discount above 0% and below 100%.','warn');return;}
 if(start&&end&&start>=end){discountSay('"Until" must be after "From".','warn');return;}
 const saved=await saveDiscountOps([{op:'upsert',rule:{price_list:$('d-list').value,channel:$('d-channel').value,percent,start:discountLocalMidnightIso(start),end:discountLocalMidnightIso(end),label:$('d-note').value.trim()}}],$('d-add'));
 if(saved){discountFormDirty=false;$('d-pct').value='';$('d-start').value='';$('d-end').value='';$('d-note').value='';}
};
document.querySelectorAll('[data-runtime-channel]').forEach(select=>select.onchange=()=>saveDiscountOps([{op:'set_runtime_channels',[select.dataset.runtimeChannel]:select.value}]));
$('discount-rotate').onclick=async()=>{
 if(!confirm('Issue a new API token? Tools using the current token stop working until they read the new one.'))return;
 const token=agentActionToken();
 if(!token){discountSay('Settings are not ready yet. Wait for live state and try again.','warn');return;}
 try{
  const response=await fetch('/api/v1/api-token/rotate',{method:'POST',headers:{'Content-Type':'application/json','X-Token-Meter-Action':token},body:'{}'});
  const result=await response.json().catch(()=>({}));
  if(!response.ok||!result.ok)throw new Error(result.error||'A new token could not be issued.');
  discountSay('New token issued. Tools using the old token must read the new one.','warn');
 }catch(error){discountSay(error.message||String(error),'warn');}
};
