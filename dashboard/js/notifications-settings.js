
const canNotify=('Notification' in window);
const notifySecure=window.isSecureContext||['localhost','127.0.0.1','::1'].includes(location.hostname);
const notifyWanted=localStorage.getItem('tm_notify')==='1';
let notifyOn=notifyWanted && canNotify && notifySecure && Notification.permission==='granted';
if(notifyWanted&&!notifyOn) localStorage.setItem('tm_notify','0');
let notifiedKeys=new Set();
let notifyLog=[];
try{
 notifyLog=JSON.parse(localStorage.getItem('tm_notify_log')||'[]')
  .filter(n=>n&&n.title!=='Token insight'&&!(n.title==='Token Meter alert'&&String(n.body||'').startsWith('Last execution cost ')))
  .slice(0,24);
 localStorage.setItem('tm_notify_log',JSON.stringify(notifyLog));
}catch(e){ notifyLog=[]; }
function notifyStamp(){ return new Date().toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'}); }
function setNotifyState(msg){ if($('notify-state')) $('notify-state').textContent=msg; }
function saveNotifyLog(){ try{ localStorage.setItem('tm_notify_log',JSON.stringify(notifyLog.slice(0,24))); }catch(e){} }
function renderNotifyLog(){
 const box=$('notify-log'); if(!box) return;
 box.innerHTML=notifyLog.length?notifyLog.map(n=>`<div class="logrow ${esc(n.kind||'')}"><div class=h><span>${esc(n.title)}</span><span class=mono>${esc(n.time)}</span></div><div class=b>${esc(n.body)}</div></div>`).join(''):'<div class=subline>no notifications yet</div>';
}
function showAppToast(entry){
 const box=$('toastbox'); if(!box) return;
 const toast=document.createElement('div');
 toast.className='toast '+(entry.kind||'');
 const head=document.createElement('div'); head.className='th';
 const title=document.createElement('span'); title.textContent=entry.title;
 const close=document.createElement('button'); close.type='button'; close.textContent='x'; close.setAttribute('aria-label','Dismiss');
 head.appendChild(title); head.appendChild(close);
 const body=document.createElement('div'); body.className='tb'; body.textContent=entry.body;
 toast.appendChild(head); toast.appendChild(body); box.appendChild(toast);
 close.onclick=()=>toast.remove();
 setTimeout(()=>toast.remove(),entry.sticky?9500:6500);
 while(box.children.length>4) box.firstElementChild.remove();
}
function agentActionToken(){return LATEST?.xsession?.capabilities?.actions?.token||'';}
let SOFTWARE_UPDATE=null,softwareUpdateRequestBusy=false,softwareUpdatePollTimer=0,softwareUpdateTarget='';
function softwareUpdateActionToken(){return SOFTWARE_UPDATE?.actions?.token||agentActionToken();}
function softwareUpdateStamp(value){
 if(!value)return'';
 return new Date(Number(value)*1000).toLocaleString([],{
  month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',
 });
}
function scheduleSoftwareUpdatePoll(delay=1500){
 clearTimeout(softwareUpdatePollTimer);
 softwareUpdatePollTimer=setTimeout(refreshSoftwareUpdateStatus,delay);
}
function renderSoftwareUpdate(status){
 if(!status)return;
 SOFTWARE_UPDATE=status;
 const state=status.state||'waiting',enabled=!!status.enabled;
 $('update-enabled').checked=enabled;
 $('update-auto-install').checked=enabled&&status.auto_install===true;
 $('update-auto-install').disabled=!enabled;
 $('update-check').disabled=!enabled||status.checking||status.updating;
 $('update-check').textContent=status.checking?'Checking…':'Check now';
 const badge={
  disabled:'Checks off',waiting:'Waiting',checking:'Checking',available:'Update ready',
  updating:'Updating',updated:'Updated',current:'Up to date',attention:'Needs attention',
 }[state]||'Unavailable';
 $('update-settings-badge').textContent=badge;
 $('update-settings-status').className=`settingsStatus ${['attention'].includes(state)?'warn':['available','updated','current'].includes(state)?'good':''}`;
 $('update-settings-status').textContent=status.message||'Update status is unavailable.';
 const revisions=status.current_revision
  ? `${status.current_revision.slice(0,12)}${status.latest_revision&&status.latest_revision!==status.current_revision?` → ${status.latest_revision.slice(0,12)}`:''}`
  :'';
 const checked=softwareUpdateStamp(status.checked_at);
 $('update-settings-meta').textContent=[revisions,checked?`last checked ${checked}`:''].filter(Boolean).join(' · ');
 if(softwareUpdateTarget&&state==='attention'){
  softwareUpdateTarget='';
  showAppToast({title:'Update did not complete',body:status.message||'The source checkout needs attention.',kind:'warn',sticky:true});
 }
 const showNotice=Boolean(
  (status.available&&status.can_update)
  || (softwareUpdateTarget&&status.updating)
 );
 $('update-notice').hidden=!showNotice;
 $('update-notice').disabled=!!status.updating;
 $('update-notice').textContent=status.updating?'Updating…':'New update available';
 document.body.classList.toggle('updateReady',showNotice);
 if(softwareUpdateTarget&&['updated','current'].includes(state)
    &&status.current_revision===softwareUpdateTarget){
  try{localStorage.setItem('tm_update_completed',softwareUpdateTarget);}catch(error){}
  softwareUpdateTarget='';
  setTimeout(()=>location.reload(),350);
  return;
 }
 if(status.checking||status.updating||softwareUpdateTarget)scheduleSoftwareUpdatePoll();
 const completed=localStorage.getItem('tm_update_completed')||'';
 if(completed&&status.current_revision===completed){
  localStorage.removeItem('tm_update_completed');
  showAppToast({title:'Token Meter updated',body:`Now running ${completed.slice(0,12)}.`,kind:'good',sticky:true});
 }
}
async function refreshSoftwareUpdateStatus(){
 if(softwareUpdateRequestBusy)return;
 softwareUpdateRequestBusy=true;
 try{
  const response=await fetch('/updates/status',{cache:'no-store'});
  if(!response.ok)throw new Error(`Update status failed (${response.status}).`);
  renderSoftwareUpdate(await response.json());
 }catch(error){
  if(softwareUpdateTarget)scheduleSoftwareUpdatePoll(2000);
 }finally{softwareUpdateRequestBusy=false;}
}
async function postSoftwareUpdate(path,payload={}){
 const token=softwareUpdateActionToken();
 if(!token)throw new Error('Update controls are not ready yet. Wait a moment and try again.');
 const response=await fetch(path,{
  method:'POST',
  headers:{'Content-Type':'application/json','X-Token-Meter-Action':token},
  body:JSON.stringify(payload),
 });
 let result={};
 try{result=await response.json();}catch(error){}
 if(!response.ok||!result.ok)throw new Error(result.error||'Token Meter could not complete the update action.');
 if(result.status)renderSoftwareUpdate(result.status);
 return result;
}
async function saveSoftwareUpdatePreferences(event){
 const enabled=$('update-enabled').checked;
 if(!enabled)$('update-auto-install').checked=false;
 const autoInstall=enabled&&$('update-auto-install').checked;
 $('update-enabled').disabled=true;
 $('update-auto-install').disabled=true;
 try{
  const result=await postSoftwareUpdate('/settings/updates',{enabled,auto_install:autoInstall});
  renderSoftwareUpdate(result.status);
  const changedAutoInstall=event.target.id==='update-auto-install';
  showAppToast({
   title:changedAutoInstall
    ?(autoInstall?'Automatic installation enabled':'Automatic installation disabled')
    :(enabled?'Update checks enabled':'Update checks disabled'),
   body:changedAutoInstall
    ?(autoInstall?'Safe main updates will install automatically.':'Checks continue; available updates stay visible in the menu bar.')
    :(enabled?'Token Meter is checking the tracked main branch now.':'Update checks and automatic installation are off.'),
   kind:enabled?'good':'neutral',
  });
 }catch(error){
  renderSoftwareUpdate(SOFTWARE_UPDATE);
  showAppToast({title:'Update setting could not be saved',body:error.message||String(error),kind:'warn',sticky:true});
 }finally{
  $('update-enabled').disabled=false;
  $('update-auto-install').disabled=!$('update-enabled').checked;
 }
}
$('update-enabled').onchange=saveSoftwareUpdatePreferences;
let menubarSettingsBusy=false;
function renderMenubarSettings(settings){
 if(!settings||menubarSettingsBusy)return;
 $('menubar-cache-timer').querySelectorAll('button').forEach(button=>{
  button.setAttribute('aria-pressed',String(button.dataset.cacheTimer===settings.cache_timer));
 });
}
$('menubar-cache-timer').onclick=async event=>{
 const button=event.target.closest('button[data-cache-timer]');
 if(!button||menubarSettingsBusy)return;
 const status=$('menubar-settings-status'),token=softwareUpdateActionToken();
 if(!token){status.textContent='Settings are not ready yet. Wait a moment and try again.';return;}
 menubarSettingsBusy=true;status.textContent='Saving…';
 try{
  const response=await fetch('/settings/menubar',{method:'POST',headers:{'Content-Type':'application/json','X-Token-Meter-Action':token},body:JSON.stringify({cache_timer:button.dataset.cacheTimer})});
  let result={};try{result=await response.json();}catch(error){}
  if(!response.ok||!result.ok)throw new Error(result.error||'Token Meter could not save the menu bar setting.');
  menubarSettingsBusy=false;renderMenubarSettings(result.menubar);
  status.textContent='Saved';
 }catch(error){
  menubarSettingsBusy=false;status.textContent=error.message;
 }
};
$('update-auto-install').onchange=saveSoftwareUpdatePreferences;
$('update-check').onclick=async()=>{
 const button=$('update-check'),original=button.textContent;
 button.disabled=true;button.textContent='Checking…';
 try{
  await postSoftwareUpdate('/updates/check');
  scheduleSoftwareUpdatePoll();
 }catch(error){
  showAppToast({title:'Update check failed',body:error.message||String(error),kind:'warn',sticky:true});
  button.disabled=false;button.textContent=original;
 }
};
$('update-notice').onclick=async()=>{
 if(!SOFTWARE_UPDATE?.can_update||SOFTWARE_UPDATE.updating)return;
 softwareUpdateTarget=SOFTWARE_UPDATE.latest_revision||'';
 $('update-notice').disabled=true;$('update-notice').textContent='Starting update…';
 try{
  const result=await postSoftwareUpdate('/updates/install');
  renderSoftwareUpdate(result.status);
  scheduleSoftwareUpdatePoll();
 }catch(error){
  softwareUpdateTarget='';
  $('update-notice').disabled=false;$('update-notice').textContent='New update available';
  showAppToast({title:'Update could not start',body:error.message||String(error),kind:'warn',sticky:true});
 }
};
const MODEL_PRICE_FIELDS=['input','output','cache_write','cache_read'];
let modelPricingState=null,modelPricingSelected=new Set(),modelPricingTab='all';
function modelPriceKey(provider,model){return `${provider}:${model}`;}
function renderModelPriceTabs(){
 const rows=modelPricingState?.models||[],providers=new Map();
 for(const row of rows){
  const entry=providers.get(row.provider)||{label:row.provider_label||row.provider,count:0,edited:false};
  entry.count++;if(modelPricingSelected.has(modelPriceKey(row.provider,row.model)))entry.edited=true;
  providers.set(row.provider,entry);
 }
 if(modelPricingTab!=='all'&&!providers.has(modelPricingTab))modelPricingTab='all';
 const tabs=[['all',{label:'All',count:rows.length,edited:modelPricingSelected.size>0}],...providers];
 $('model-price-tabs').innerHTML=rows.length?tabs.map(([id,tab])=>{
  const on=id===modelPricingTab;
  return `<button type=button role=tab id="model-price-tab-${esc(id)}" aria-selected=${on} aria-controls=model-price-table-wrap tabindex=${on?0:-1} data-model-price-tab="${esc(id)}">${esc(tab.label)}<span class=count>${f(tab.count)}</span>${tab.edited?'<span class=edited title="Unsaved edits" aria-label="Unsaved edits"></span>':''}</button>`;
 }).join(''):'';
 $('model-price-tabs').querySelectorAll('[data-model-price-tab]').forEach(button=>{
  button.onclick=()=>selectModelPriceTab(button.dataset.modelPriceTab);
  button.onkeydown=event=>{
   const buttons=[...$('model-price-tabs').querySelectorAll('[data-model-price-tab]')],index=buttons.indexOf(button);
   const next=event.key==='ArrowRight'?index+1:event.key==='ArrowLeft'?index-1:event.key==='Home'?0:event.key==='End'?buttons.length-1:null;
   if(next===null)return;
   event.preventDefault();
   selectModelPriceTab(buttons[(next+buttons.length)%buttons.length].dataset.modelPriceTab);
  };
 });
 applyModelPriceTab();
}
function applyModelPriceTab(){
 for(const root of $('model-pricing-rows').querySelectorAll('[data-model-price-key]'))root.hidden=modelPricingTab!=='all'&&root.dataset.modelPriceProvider!==modelPricingTab;
 const active=$('model-price-tabs').querySelector('[aria-selected=true]');
 if(active)$('model-price-table-wrap').setAttribute('aria-labelledby',active.id);
}
function selectModelPriceTab(tab){
 if(tab===modelPricingTab)return;
 const hadFocus=$('model-price-tabs').contains(document.activeElement);
 modelPricingTab=tab;
 renderModelPriceTabs();
 $('model-price-table-wrap').scrollTop=0;
 if(hadFocus)$('model-price-tabs').querySelector('[aria-selected=true]')?.focus();
}
function modelPriceSelectionState(count){
 const selected=Math.max(0,Number(count)||0),noun=selected===1?'model':'models';
 return {summary:selected?`${selected} ${noun} selected`:'No models selected',button:selected?`Save ${selected} ${noun}`:'Save models',disabled:selected===0};
}
function modelPriceScope(){return document.querySelector('input[name="model-price-scope"]:checked')?.value||'from_now';}
function modelPriceAllHistory(){return modelPriceScope()==='all_history';}
function modelPriceEffectiveFrom(){
 if(modelPriceScope()!=='from_date')return null;
 const raw=$('model-price-effective-from').value;
 if(!raw)throw new Error('Choose the date when these prices should start.');
 const timestamp=new Date(raw).getTime()/1000;
 if(!Number.isFinite(timestamp))throw new Error('Choose a valid effective date and time.');
 if(timestamp>Date.now()/1000+60)throw new Error('The effective date cannot be in the future.');
 return timestamp;
}
function modelPriceScopePayload(scopeOverride=null){
 const scope=scopeOverride||modelPriceScope(),payload={apply_to_all_history:scope==='all_history'};
 if(scope==='from_date')payload.effective_from=modelPriceEffectiveFrom();
 return payload;
}
function modelPriceSource(row){
 const source=row.source||'built-in',periods=Number(row.periods||0);
 const label=source==='built-in'?'':source;
 if(!periods)return label;
 if(row.effective_from==null)return label?`${label} · all history`:'all history';
 const date=new Date(Number(row.effective_from)*1000);
 if(Number.isNaN(date.getTime()))return label;
 const effective=`from ${date.toLocaleString([], {dateStyle:'medium',timeStyle:'short'})}`;
 return label?`${label} · ${effective}`:effective;
}
function updateModelPriceScope(){
 const scope=modelPriceScope(),history=scope==='all_history',fromDate=scope==='from_date';
 $('model-price-scope').classList.toggle('history',history);
 $('model-price-effective-from').disabled=!fromDate;
 $('model-price-scope-note').textContent=history?'All matching session estimates will be recalculated.':(fromDate?'Choose the date; session estimates from then are recalculated and older session estimates keep their prior prices.':'Changes start when you save; older session estimates keep their prior prices.');
}
function confirmModelPriceHistory(action,model){
 if(modelPriceAllHistory())return confirm(`${action} for ${model} across all recorded history? Older cost estimates will be recalculated and the previous price timeline will be replaced.`);
 const effective=modelPriceEffectiveFrom();
 if(effective==null)return true;
 const when=new Date(effective*1000).toLocaleString([], {dateStyle:'medium',timeStyle:'short'});
 return confirm(`${action} for ${model} from ${when}? Sessions at or after that time will be recalculated; earlier sessions keep their prices.`);
}
function modelPriceNumber(value){
 const number=Number(value);
 return Number.isFinite(number)?String(number):'0';
}
function modelPriceValues(root){
 const prices={};
 for(const field of MODEL_PRICE_FIELDS){
  const input=root.querySelector(`[data-price-field="${field}"]`);
  const value=Number(input?.value);
  if(!input||input.value.trim()===''||!Number.isFinite(value)||value<0)throw new Error('Enter a non-negative number for every price.');
  prices[field]=value;
 }
 return prices;
}
function modelPriceRowForRoot(root){
 const key=root?.dataset.modelPriceKey||'';
 return (modelPricingState?.models||[]).find(row=>modelPriceKey(row.provider,row.model)===key);
}
function restoreModelPriceRow(root){
 const row=modelPriceRowForRoot(root),prices=row?.prices||{};
 if(!row)return;
 for(const field of MODEL_PRICE_FIELDS){
  const input=root.querySelector(`[data-price-field="${field}"]`);
  if(input)input.value=modelPriceNumber(prices[field]);
 }
}
function setModelPriceSelected(root,selected,restore=true){
 const key=root?.dataset.modelPriceKey;
 if(!key)return;
 if(selected)modelPricingSelected.add(key);else{
  modelPricingSelected.delete(key);
  if(restore)restoreModelPriceRow(root);
 }
 root.classList.toggle('selected',selected);
 const checkbox=root.querySelector('[data-model-price-select]');
 if(checkbox)checkbox.checked=selected;
 updateModelPriceSelectionState();
}
function updateModelPriceSelectionState(){
 const state=modelPriceSelectionState(modelPricingSelected.size),button=$('model-price-save-changes');
 $('model-price-selection-count').textContent=state.summary;
 button.disabled=state.disabled;button.textContent=state.button;
 renderModelPriceTabs();
}
function renderModelPricing(pricing,force=false){
 if(!pricing)return;
 modelPricingState=pricing;
 $('model-pricing-reviewed').textContent=pricing.reviewed_on||'not recorded';
 const sources=Array.isArray(pricing.sources)?pricing.sources:[];
 $('model-pricing-sources').innerHTML=sources.length?sources.map(source=>`<a href="${esc(source.url||'#')}" target=_blank rel="noreferrer">${esc(source.label||source.provider||'Source')}</a>`).join(' · '):'No source links available';
 const custom=Number(pricing.custom_models||0),retired=Number(pricing.retired_custom_models||0),overrides=Number(pricing.overrides||0);
 const badge=$('model-pricing-badge'),hasCustomPricing=!!(custom||overrides||retired);
 badge.hidden=!hasCustomPricing;
 badge.textContent=hasCustomPricing?`${f(custom)} custom · ${f(overrides)} ${countWord(overrides,'override')}${retired?` · ${f(retired)} retired`:''}`:'';
 if(modelPricingSelected.size&&!force)return;
 const drafts=new Map();
 if(force)for(const root of $('model-pricing-rows').querySelectorAll('[data-model-price-key]')){
  if(modelPricingSelected.has(root.dataset.modelPriceKey)){
   try{drafts.set(root.dataset.modelPriceKey,modelPriceValues(root));}catch(error){}
  }
 }
 const rows=pricing.models||[];
 $('model-pricing-rows').innerHTML=rows.length?rows.map(row=>{
  const prices=row.prices||{},key=modelPriceKey(row.provider,row.model),sourceLabel=modelPriceSource(row),selected=modelPricingSelected.has(key);
  const lifecycleAction=modelPriceLifecycle(row),lifecycle=lifecycleAction?`<button class=modelPriceLifecycle data-model-price-lifecycle type=button>${lifecycleAction.label}</button>`:'';
  const fieldLabels={input:'Input',output:'Output',cache_write:'Cache write',cache_read:'Cached input'};
  return `<tr${selected?' class=selected':''} data-model-price-key="${esc(key)}" data-model-price-provider="${esc(row.provider)}"><td class=modelPriceSelectCell data-label="Select"><input data-model-price-select type=checkbox${selected?' checked':''} aria-label="${esc(`Select ${row.model} for price update`)}"></td><td data-label="Provider"><span class="badge ${esc(row.provider)}">${esc(row.provider_label||row.provider)}</span></td><td data-label="Model id"><div class=modelPriceModel><code>${esc(row.model)}</code>${sourceLabel?`<span class=modelPriceSource>${esc(sourceLabel)}</span>`:''}${lifecycle}</div></td>${MODEL_PRICE_FIELDS.map(field=>`<td class=num data-label="${fieldLabels[field]}"><input class=modelPriceInput data-price-field="${field}" type=number min=0 max=1000000 step=any value="${esc(modelPriceNumber(prices[field]))}" aria-label="${esc(`${row.model} ${field.replace('_',' ')} price`)}"></td>`).join('')}</tr>`;
 }).join(''):'<tr><td colspan=7 class=modelPricingEmpty>No model prices configured.</td></tr>';
 for(const root of $('model-pricing-rows').querySelectorAll('[data-model-price-key]')){
  const draft=drafts.get(root.dataset.modelPriceKey);
  if(draft)for(const field of MODEL_PRICE_FIELDS)root.querySelector(`[data-price-field="${field}"]`).value=modelPriceNumber(draft[field]);
 }
 $('model-pricing-rows').querySelectorAll('.modelPriceInput').forEach(input=>input.addEventListener('input',()=>{
  const root=input.closest('[data-model-price-key]');setModelPriceSelected(root,true,false);$('model-pricing-status').className='settingsStatus warn';$('model-pricing-status').textContent='Unsaved price changes';
 }));
 $('model-pricing-rows').querySelectorAll('[data-model-price-select]').forEach(checkbox=>checkbox.addEventListener('change',()=>{
  const root=checkbox.closest('[data-model-price-key]');setModelPriceSelected(root,checkbox.checked,true);
  $('model-pricing-status').className=modelPricingSelected.size?'settingsStatus warn':'settingsStatus';$('model-pricing-status').textContent=modelPricingSelected.size?'Selected model prices are ready to edit.':'';
 }));
 $('model-pricing-rows').querySelectorAll('[data-model-price-lifecycle]').forEach(button=>button.onclick=()=>applyModelPriceLifecycle(button));
 updateModelPriceSelectionState();
 updateModelPriceScope();
}
async function requestModelPrice(payload,scopeOverride=null){
 const token=agentActionToken();
 if(!token)throw new Error('Settings are not ready yet. Wait for live state and try again.');
 const response=await fetch('/settings/model-pricing',{method:'POST',headers:{'Content-Type':'application/json','X-Token-Meter-Action':token},body:JSON.stringify({...payload,...modelPriceScopePayload(scopeOverride)})});
 const result=await response.json();
 if(!response.ok||!result.ok)throw new Error(result.error||'Could not save model pricing.');
 if(LATEST?.xsession)LATEST.xsession.model_pricing=result.model_pricing;
 return result;
}
function modelPriceRowFor(button){
 return modelPriceRowForRoot(button.closest('[data-model-price-key]'));
}
async function saveModelPriceChanges(){
 const button=$('model-price-save-changes'),keys=[...modelPricingSelected];
 if(!keys.length)return;
 try{
  const changes=keys.map(key=>{
   const row=(modelPricingState?.models||[]).find(item=>modelPriceKey(item.provider,item.model)===key),root=[...$('model-pricing-rows').querySelectorAll('[data-model-price-key]')].find(item=>item.dataset.modelPriceKey===key);
   if(!row||!root)throw new Error('Reload Settings and try saving these model prices again.');
   return {provider:row.provider,model:row.model,prices:modelPriceValues(root)};
  });
  if(!confirmModelPriceHistory(`Apply ${changes.length} price ${countWord(changes.length,'change')}`,'the selected models'))return;
  const original=button.textContent;button.disabled=true;button.textContent='Saving…';
  try{
   const result=await requestModelPrice({changes});
   keys.forEach(key=>modelPricingSelected.delete(key));
   renderModelPricing(result.model_pricing,true);
   $('model-pricing-status').className='settingsStatus good';
   $('model-pricing-status').textContent=result.effective_scope==='all_history'?'Saved · matching session estimates recalculated':(result.effective_scope==='selected_time'?'Saved from the selected date':'Saved from now');
   showAppToast({title:'Model prices updated',body:`Saved ${changes.length} ${countWord(changes.length,'change')}.`,kind:'good'});
   await refreshLiveState();
  }finally{button.textContent=original;updateModelPriceSelectionState();}
 }catch(error){$('model-pricing-status').className='settingsStatus warn';$('model-pricing-status').textContent=error.message||String(error);showAppToast({title:'Model pricing could not be saved',body:error.message||String(error),kind:'warn',sticky:true});}
}
function modelPriceLifecycle(row){
 if(row.custom)return {label:'Remove custom model',scope:row.active?null:'all_history'};
 if(row.overridden||Number(row.periods||0)>0)return {label:'Restore built-in price',scope:null};
 return null;
}
async function applyModelPriceLifecycle(button){
 const row=modelPriceRowFor(button),action=row&&modelPriceLifecycle(row);
 if(!action)return;
 const scopeOverride=action.scope;
 if(!row.custom&&!row.overridden&&modelPriceScope()==='from_now'){
  // Already built-in from now; only All history clears the saved periods.
  const guidance=`${row.model} already uses built-in prices from now. Choose All history, then restore to clear its earlier saved prices.`;
  $('model-pricing-status').className='settingsStatus';
  $('model-pricing-status').textContent=guidance;
  showAppToast({title:'Already on built-in prices',body:guidance});
  return;
 }
 if(row.custom&&!row.active){if(!confirm(`Delete ${row.model} from all saved pricing history?`))return;}
 else if(!confirmModelPriceHistory(row.custom?'Retire this custom price':'Restore built-in pricing',row.model))return;
 const original=button.textContent;button.disabled=true;button.textContent=row.custom?'Removing…':'Restoring…';
 try{
  const result=await requestModelPrice({provider:row.provider,model:row.model,remove:true},scopeOverride);
  modelPricingSelected.delete(modelPriceKey(row.provider,row.model));
  renderModelPricing(result.model_pricing,true);
  $('model-pricing-status').className='settingsStatus good';$('model-pricing-status').textContent=row.custom?'Custom price updated.':'Default price restored.';
  showAppToast({title:row.custom?'Custom price changed':'Default price restored',body:result.effective_scope==='all_history'?(row.custom?`${row.model} was removed from all pricing history.`:`${row.model} uses built-in prices across all history.`):(result.effective_scope==='selected_time'?(row.custom?`${row.model} is retired from the selected time; earlier sessions retain its price.`:`${row.model} uses built-in pricing from the selected time.`):(row.custom?`${row.model} is retired from now; older sessions retain its price.`:`${row.model} uses Token Meter’s built-in price from now.`)),kind:'good'});
  await refreshLiveState();
 }catch(error){$('model-pricing-status').className='settingsStatus warn';$('model-pricing-status').textContent=error.message||String(error);showAppToast({title:'Model pricing could not be changed',body:error.message||String(error),kind:'warn',sticky:true});}
 finally{button.disabled=false;button.textContent=original;}
}
$('model-price-add-form').addEventListener('submit',async event=>{
 event.preventDefault();
 const button=$('model-price-add'),model=$('model-price-model').value.trim();
 try{
  const root=event.currentTarget,prices={
   input:Number($('model-price-input').value),output:Number($('model-price-output').value),
   cache_write:Number($('model-price-cache-write').value),cache_read:Number($('model-price-cache-read').value),
 };
 for(const [field,value] of Object.entries(prices))if(root.querySelector(`#model-price-${field.replaceAll('_','-')}`)?.value.trim()===''||!Number.isFinite(value)||value<0)throw new Error('Enter a non-negative number for every price.');
  if(!confirmModelPriceHistory('Add this custom price',model))return;
  const original=button.textContent;button.disabled=true;button.textContent='Adding…';
  let result;
  try{result=await requestModelPrice({provider:$('model-price-provider').value,model,prices});}
  finally{button.disabled=false;button.textContent=original;}
  renderModelPricing(result.model_pricing,true);
  $('model-price-model').value='';$('model-price-input').value='';$('model-price-output').value='';$('model-price-cache-write').value='0';$('model-price-cache-read').value='0';
  showAppToast({title:'Model price added',body:result.effective_scope==='all_history'?`${model} now prices all matching history.`:(result.effective_scope==='selected_time'?`${model} is priced from the selected time; earlier sessions are unchanged.`:`${model} is priced from now; older sessions are unchanged.`),kind:'good'});
  await refreshLiveState();
 }catch(error){$('model-pricing-status').className='settingsStatus warn';$('model-pricing-status').textContent=error.message||String(error);showAppToast({title:'Model could not be added',body:error.message||String(error),kind:'warn',sticky:true});}
});
document.querySelectorAll('input[name="model-price-scope"]').forEach(input=>input.addEventListener('change',updateModelPriceScope));
$('model-price-save-changes').addEventListener('click',saveModelPriceChanges);
updateModelPriceScope();
