function budgetAllocationsFromInputs(){
 const allocations={};
 budgetRuntimeIds().forEach(provider=>{allocations[provider]=budgetNumber(`budget-input-${provider}`);});
 return allocations;
}
function previewCalculatedBudget(){
 const total=Object.values(budgetAllocationsFromInputs()).reduce((sum,value)=>sum+(Number.isFinite(value)?Math.max(0,value):0),0);
 $('budget-total').textContent=total?wholeMoney(total):'Not set';
 $('budget-total-note').textContent=total?'draft total':'Add runtime budgets to enable tracking';
}
$('budget-form').addEventListener('input',event=>{budgetSettingsDirty=true;if(event.target.closest('.budgetRuntimeInput'))previewCalculatedBudget();$('budget-settings-status').className='settingsStatus warn';$('budget-settings-status').textContent='Unsaved changes';});
$('budget-form').onsubmit=async event=>{
 event.preventDefault();
 const button=$('budget-save'),allocations=budgetAllocationsFromInputs();
	 const payload={currency:'USD',default_session_budget:budgetNumber('budget-input-session-default'),allocations,thresholds:parsedBudgetThresholds(),native_notifications:$('budget-input-notifications').checked};
 button.disabled=true;button.textContent='Saving…';
 try{
  const response=await fetch('/settings/budgets',{method:'POST',headers:{'Content-Type':'application/json','X-Token-Meter-Action':agentActionToken()},body:JSON.stringify(payload)});
	  const result=await response.json();if(!response.ok||!result.ok)throw new Error(result.error||'Could not save budget settings.');
  budgetSettingsDirty=false;
	  if(LATEST?.xsession){LATEST.xsession.budgets=result.budgets;LATEST.xsession.budget=result.budget;LATEST.xsession.monthly=result.monthly;}
	  renderBudgets(LATEST?.xsession||result);
	  if(CURRENT)loadBudgetForSession(CURRENT);
	  $('budget-settings-status').className='settingsStatus good';$('budget-settings-status').textContent='Saved';
	  const total=Number(result.budgets?.monthly_total||0);
	  const sessionDefault=Number(result.budgets?.default_session_budget||10);
	  showAppToast({title:'Budget settings updated',body:total?`${money(sessionDefault)} per session by default · ${money(total)} per month.`:`${money(sessionDefault)} per session by default · monthly tracking is unconfigured.`,kind:'good'});
 }catch(error){$('budget-settings-status').className='settingsStatus warn';$('budget-settings-status').textContent=error.message||String(error);showAppToast({title:'Budget could not be saved',body:error.message||String(error),kind:'warn',sticky:true});}
 finally{button.disabled=false;button.textContent='Save budgets';}
};
function agentSkillsView(row){
 const skills=row.skills||{}, managed=(skills.managed||0)>0;
 const remove={label:'Remove',enabled:false};
 if(!skills.available)return{text:'Not included in this install',cls:'',buttons:[{label:'Install',enabled:true,disabled:true}]};
 if(!skills.detected)return managed
  ?{text:`${skills.target||'Client'} not found · Token Meter skills still installed`,cls:'warn',buttons:[remove]}
  :{text:`${skills.target||'Client'} not found`,cls:'',buttons:[{label:'Install',enabled:true,disabled:true}]};
 if(skills.state==='installed')return{text:`${skills.total===1?'1 skill installed':`${skills.total} skills installed`} · available in new sessions`,cls:'connected',buttons:[remove]};
 if(skills.state==='outdated')return{text:'Update available',cls:'warn',buttons:[{label:'Update',enabled:true},remove]};
 if(skills.state==='partial'){
  const installable=(skills.missing||[]).length+(skills.outdated||[]).length>0;
  const fresh=!(skills.unmanaged||[]).length&&!(skills.outdated||[]).length&&(skills.missing||[]).length;
  return{text:(skills.unmanaged||[]).length?'Partly installed · a skill you created or edited uses one of the names':fresh?`New skills available · ${skills.installed} of ${skills.total} installed`:'Partly installed',cls:'warn',buttons:installable?[{label:(skills.missing||[]).length?'Install':'Update',enabled:true},remove]:[remove]};
 }
 if(skills.state==='conflict')return{text:'A skill you created or edited already uses this name',cls:'warn',buttons:[{label:'Install',enabled:true,disabled:true}]};
 return{text:'Usage review, budget pacing, and skill hygiene',cls:'',buttons:[{label:'Install',enabled:true}]};
}
function agentSkillsRow(row){
 const view=agentSkillsView(row);
 const buttons=view.buttons.map(b=>`<button class="tbtn ${b.enabled?'':'danger'}" type=button data-agent-skills="${esc(row.id)}" data-agent-enabled="${b.enabled?'true':'false'}" ${b.disabled?'disabled':''}>${b.label}</button>`).join('');
 return `<div class="agentClientPart agentClientSkills"><div><div class=agentClientName>Token Meter skills</div><div class="agentClientState ${view.cls}">${esc(view.text)}</div></div><div class=agentClientActions>${buttons}</div></div>`;
}
function openAgentSkillsDialog(client,enabled){
 const row=(AGENT_ACCESS?.clients||[]).find(item=>item.id===client);
 if(!row?.skills)return;
 const skills=row.skills, folder=client==='codex'?'~/.codex/skills':'~/.claude/skills';
 const skip=new Set([...(skills.unmanaged||[]),...(enabled?[]:(skills.missing||[]))]);
 const keep=enabled?new Set((skills.names||[]).filter(name=>!(skills.missing||[]).includes(name)&&!(skills.outdated||[]).includes(name))):new Set();
 const names=(skills.names||[]).filter(name=>!skip.has(name)&&!keep.has(name));
 const verb=!enabled?'Remove':skills.state==='outdated'?'Update':'Install';
 pendingAgentAccess={kind:'skills',client,enabled,row,verb};
 $('agent-dialog-title').textContent=`${verb} Token Meter skills for ${skills.target}?`;
 $('agent-dialog-sub').textContent=enabled?`Copy Token Meter's skills into ${folder}.`:`Delete only Token Meter's own skill files from ${folder}.`;
 $('agent-dialog-evidence').innerHTML=enabled
  ? '<div>Skills run only when you ask. They read numbers through the <code>tokenmeter</code> MCP, so connect it too.</div><div>A skill with the same name that you created or edited is never changed.</div><div>Token Meter updates its unedited copies when it updates; remove them here at any time.</div>'
  : '<div>Your MCP connection, dashboard, and history stay unchanged.</div><div>Skills you created or edited are never deleted.</div>';
 $('agent-dialog-command-label').textContent=enabled?'Files to write':'Files to delete';
 $('agent-dialog-command').textContent=names.map(name=>`${folder}/${name}/SKILL.md`).join('\n');
 $('agent-dialog-note').textContent='Only unedited files Token Meter installed are managed. Start a new agent session after the change.';
 $('agent-confirm').textContent=verb;
 $('agent-confirm').classList.toggle('danger',!enabled);
 $('agent-dialog').showModal();
}
function renderAgentAccess(status){
 AGENT_ACCESS=status;
 const clients=status?.clients||[];
 $('agent-clients').innerHTML=clients.map(row=>{
  const stateClass=row.connected?'connected':row.conflict?'warn':'';
  const label=row.connected?'Disconnect':row.conflict?'Repair':'Connect';
  const disabled=!row.connected&&!row.available;
  const detail=!row.detected?(row.id==='claude'?row.status:'CLI not found'):!row.available?'Token Meter launcher unavailable':row.status;
  return `<div class=agentClient><div class=agentClientPart><div><div class=agentClientName>${esc(row.label)} MCP</div><div class="agentClientState ${stateClass}">${esc(detail)}${row.connected?' · available in new sessions':''}</div></div><button class="tbtn ${row.connected?'danger':''}" type=button data-agent-client="${esc(row.id)}" data-agent-enabled="${row.connected?'false':'true'}" ${disabled?'disabled':''}>${label}</button></div>${agentSkillsRow(row)}</div>`;
 }).join('')||'<div class=agentClient><div><div class=agentClientName>Status unavailable</div><div class=agentClientState>Refresh Token Meter and try again.</div></div></div>';
 const dismissed=localStorage.getItem('tm_agent_discovery_dismissed')==='1';
 $('agent-discovery').hidden=!!(dismissed||!status?.any_detected||status?.any_connected);
}
async function loadAgentAccess(){
 if(agentAccessLoading)return false;
 agentAccessLoading=true;
 try{
  const response=await fetch('/agent-access/status',{cache:'no-store'});
  if(!response.ok)throw new Error(`Connection status failed (${response.status}).`);
  renderAgentAccess(await response.json());
  return true;
 }catch(error){
  $('agent-clients').innerHTML='<div class=agentClient><div><div class=agentClientName>Could not read connection status</div><div class=agentClientState>Token Meter will retry when you reopen this view.</div></div></div>';
  $('agent-discovery').hidden=true;
  return false;
 }finally{agentAccessLoading=false;}
}
function openAgentAccessDialog(client,enabled){
 const row=(AGENT_ACCESS?.clients||[]).find(item=>item.id===client);
 if(!row)return;
 const repairing=!!(enabled&&row.conflict);
 pendingAgentAccess={client,enabled,row};
 $('agent-dialog-title').textContent=`${repairing?'Repair':enabled?'Connect':'Disconnect'} ${row.label}?`;
 $('agent-dialog-sub').textContent=repairing
  ? 'Replace only the existing user-level tokenmeter entry with the launcher from this Token Meter install.'
  : enabled?'Add Token Meter as a user-level, local stdio MCP server.':'Remove only Token Meter’s exact user-level MCP entry.';
 $('agent-dialog-evidence').innerHTML=repairing
  ? '<div>The existing tokenmeter entry will be removed, recreated with this install’s launcher, and verified.</div><div>No other MCP entries or agent settings are changed.</div><div>Existing agent sessions keep their loaded tool list until restarted.</div>'
  : enabled
  ? '<div>Read-only and on demand; no background agent monitoring.</div><div>Prompts, messages, reasoning, tool arguments, tool results, credentials, config values, and named history are excluded.</div><div>Returned metrics enter this agent’s context and may be processed by its model provider.</div>'
  : '<div>Your Token Meter dashboard and local history stay unchanged.</div><div>Existing agent sessions keep their loaded tool list until restarted.</div>';
 $('agent-dialog-command-label').textContent='Exact local command';
 $('agent-dialog-note').innerHTML='Only Token Meter&rsquo;s exact <code>tokenmeter</code> entry is managed. Start a new agent session after the change.';
 $('agent-dialog-command').textContent=repairing?`${row.disconnect_command}\n${row.connect_command}`:enabled?row.connect_command:row.disconnect_command;
 $('agent-confirm').textContent=repairing?'Repair':enabled?'Connect':'Disconnect';
 $('agent-confirm').classList.toggle('danger',!enabled);
 $('agent-dialog').showModal();
}
$('agent-clients').onclick=async e=>{
 const skillsButton=e.target.closest('[data-agent-skills]');
 if(skillsButton){if(!skillsButton.disabled)openAgentSkillsDialog(skillsButton.dataset.agentSkills,skillsButton.dataset.agentEnabled==='true');return;}
 const button=e.target.closest('[data-agent-client]');
 if(!button||button.disabled)return;
 const client=button.dataset.agentClient;
 if((AGENT_ACCESS?.clients||[]).find(item=>item.id===client)?.blocked){
  if(agentAccessRechecking)return;
  agentAccessRechecking=true;
  let refreshed=false;
  try{refreshed=await loadAgentAccess();}finally{agentAccessRechecking=false;}
  if(!refreshed)return;
  const row=(AGENT_ACCESS?.clients||[]).find(item=>item.id===client);
  if(row?.blocked){
   const action=row.connected?'Disconnect':row.conflict?'Repair':'Connect';
   showAppToast(row.unreadable
    ? {title:'Claude app settings could not be read',body:`Token Meter left the Claude app's settings file unchanged. Fix or replace that file, then click ${action} here again.`,kind:'warn',sticky:true}
    : {title:'Quit the Claude app first',body:`The Claude app rewrites its settings while it is open, so Token Meter cannot change them yet. Quit it with ⌘Q (closing the window is not enough), then click ${action} here again.`,kind:'warn',sticky:true});
   return;
  }
  if(row)openAgentAccessDialog(client,!row.connected);
  return;
 }
 openAgentAccessDialog(client,button.dataset.agentEnabled==='true');
};
$('agent-cancel').onclick=()=>{$('agent-dialog').close();pendingAgentAccess=null;};
$('agent-dialog').addEventListener('cancel',()=>{pendingAgentAccess=null;});
$('agent-confirm').onclick=async()=>{
 if(!pendingAgentAccess)return;
 const token=agentActionToken();
 if(!token){showAppToast({title:'Connection not ready',body:'Wait for Token Meter’s live state, then try again.',kind:'warn'});return;}
 const button=$('agent-confirm'), action=pendingAgentAccess;
 if(action.kind==='skills'){
  button.disabled=true;button.textContent=action.verb==='Update'?'Updating…':action.enabled?'Installing…':'Removing…';
  try{
   const response=await fetch('/agent-skills/toggle',{method:'POST',headers:{'Content-Type':'application/json','X-Token-Meter-Action':token},body:JSON.stringify({client:action.client,enabled:action.enabled})});
   const result=await response.json();
   if(!response.ok||!result.ok)throw new Error(result.error||'Skill change failed.');
   $('agent-dialog').close();pendingAgentAccess=null;
   await loadAgentAccess();
   showAppToast({title:result.partial?'Skills partly installed':action.verb==='Update'?'Skills updated':action.enabled?'Skills installed':'Skills removed',body:result.message||'Start a new agent session for the change to take effect.',kind:result.partial?'warn':action.enabled?'good':'neutral',sticky:true});
  }catch(error){
   await loadAgentAccess();
   showAppToast({title:'Skill change failed',body:error.message||String(error),kind:'warn',sticky:true});
  }finally{button.disabled=false;if(pendingAgentAccess)button.textContent=pendingAgentAccess.verb;}
  return;
 }
 const repairing=!!(action.enabled&&action.row.conflict);
 button.disabled=true;button.textContent=repairing?'Repairing…':action.enabled?'Connecting…':'Disconnecting…';
 try{
  const response=await fetch('/agent-access/toggle',{method:'POST',headers:{'Content-Type':'application/json','X-Token-Meter-Action':token},body:JSON.stringify({client:action.client,enabled:action.enabled,repair:repairing})});
  const result=await response.json();
  if(!response.ok||!result.ok)throw new Error(result.error||'Connection change failed.');
  localStorage.setItem('tm_agent_discovery_dismissed','1');
  $('agent-dialog').close();pendingAgentAccess=null;
  await loadAgentAccess();
  showAppToast({title:result.repaired?'Connection repaired':action.enabled?'Agent connected':'Agent disconnected',body:result.message||'Start a new agent session for the change to take effect.',kind:action.enabled?'good':'neutral',sticky:true});
 }catch(error){
  showAppToast({title:'Connection change failed',body:error.message||String(error),kind:'warn',sticky:true});
 }finally{button.disabled=false;if(pendingAgentAccess)button.textContent=pendingAgentAccess.enabled?(pendingAgentAccess.row.conflict?'Repair':'Connect'):'Disconnect';}
};
$('agent-discovery-open').onclick=()=>{setHashRoute('settings');setTimeout(()=>$('agent-access').scrollIntoView({behavior:'smooth',block:'start'}),30);};
$('agent-discovery-close').onclick=()=>{localStorage.setItem('tm_agent_discovery_dismissed','1');$('agent-discovery').hidden=true;};
function pushAppNotice(title,body,kind='neutral',opts={}){
 const entry={title,body:body||'',kind,time:notifyStamp(),sticky:!!opts.sticky};
 notifyLog.unshift(entry); notifyLog=notifyLog.slice(0,24); saveNotifyLog(); renderNotifyLog(); showAppToast(entry);
 $('notify')?.classList.add('hasnew');
 setNotifyState((opts.status||'Notification logged')+' at '+entry.time+'.');
 return entry;
}
function browserBlockReason(){
 if(!canNotify) return 'Browser notifications are not supported here.';
 if(!notifySecure) return 'Notifications require localhost or a secure origin.';
 if(Notification.permission==='denied') return 'Notifications are blocked in browser or system settings.';
 if(Notification.permission!=='granted') return 'Browser notification permission is not granted.';
 return '';
}
function notifyBlockReason(){
 if(!notifyOn) return 'Browser notifications are off.';
 return browserBlockReason();
}
function paintNotify(){
 let browserReason=browserBlockReason();
 if(notifyOn&&browserReason){
  notifyOn=false;
  localStorage.setItem('tm_notify','0');
 }
 const control=$('notify');
 if(control){control.classList.toggle('on',notifyOn);control.setAttribute('aria-checked',String(notifyOn));}
 browserReason=browserBlockReason();
 if($('notifytxt'))$('notifytxt').textContent=notifyOn?'Budget alerts':(!canNotify?'Unavailable':(Notification.permission==='denied'?'Blocked':'Budget alerts'));
 if(notifyOn) setNotifyState('Browser notifications ready.');
 else setNotifyState(browserReason||'Browser notifications are off.');
}
function fireNotification(title,body,key,opts={}){
 const reason=notifyBlockReason();
 if(reason){
  if(reason!=='Browser notifications are off.'){
   notifyOn=false;
   localStorage.setItem('tm_notify','0');
   paintNotify();
  }
  setNotifyState(reason);
  return false;
 }
 const k=opts.dedupe===false?(key||title)+':'+Date.now():(key||title+body);
 if(notifiedKeys.has(k)) return false;
 try{
  const n=new Notification(title,{body,tag:k,renotify:!!opts.renotify,requireInteraction:!!opts.requireInteraction,timestamp:Date.now()});
  n.onclick=()=>{ try{ window.focus(); n.close(); }catch(e){} };
  notifiedKeys.add(k);
  const entry=pushAppNotice(title,body,opts.kind||'neutral',{sticky:opts.requireInteraction,status:'Browser notification sent'});
  setNotifyState('Browser notification sent at '+entry.time+'.');
  return true;
 }catch(e){
  pushAppNotice('Browser notification failed',`${title}: ${body}`,'warn',{sticky:true,status:'Browser notification failed'});
  setNotifyState('Browser notification failed: '+(e&&e.message?e.message:'browser rejected it')+'.');
  return false;
 }
}
function sendTestNotification(){
 return fireNotification('Token Meter test','Notifications are working for this dashboard.','test',{dedupe:false,requireInteraction:true,renotify:true});
}
function applyPermission(p,after){
 if(p==='granted'){
  notifyOn=true;
  localStorage.setItem('tm_notify','1');
  paintNotify();
  notifiedKeys.clear();
  if(after==='test') sendTestNotification(); else fireNotification('Token Meter','Notifications enabled.','enabled:'+Date.now(),{dedupe:false,renotify:true});
  if(CURRENT&&!pinned) setTimeout(()=>notifyAlertForSession(CURRENT,{forceCurrent:true}),350);
 }
 else{
  notifyOn=false;
  localStorage.setItem('tm_notify','0');
  paintNotify();
  if(p==='denied') pushAppNotice('Browser notifications blocked','Enable notifications in browser or system settings, then toggle again.','warn',{sticky:true,status:'Browser notifications blocked'});
  else pushAppNotice('Browser notifications not enabled','Permission was not granted for browser notifications.','warn',{status:'Permission not granted'});
 }
}
function requestNotify(after){
 if(!canNotify){ setNotifyState('Browser notifications are not supported here.'); pushAppNotice('Browser notifications unavailable','This browser does not support notifications.','warn',{sticky:true,status:'Browser notifications unavailable'}); return; }
 if(!notifySecure){ setNotifyState('Notifications require localhost or a secure origin.'); pushAppNotice('Browser notifications unavailable','Notifications require localhost or a secure origin.','warn',{sticky:true,status:'Browser notifications unavailable'}); return; }
 const req=Notification.requestPermission();
 if(req&&typeof req.then==='function') req.then(p=>applyPermission(p,after)); else applyPermission(req,after);
}
paintNotify();
renderNotifyLog();

const SESSION_BUDGET_SLIDER_MIN=0.5;
const SESSION_BUDGET_SLIDER_BASE_MAX=100;
let legacySessionBudgets={};
const sessionBudgetWrites=new Map();
try{legacySessionBudgets=JSON.parse(localStorage.getItem('tm_session_budgets')||'{}')||{};}catch(e){legacySessionBudgets={};}
localStorage.removeItem('tm_budget');
