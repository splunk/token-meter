function setCapabilityStatBar(id,value){
 const bar=$(id);
 if(!bar)return;
 const normalized=Math.min(1,Math.max(0,Number(value)||0)),label=pct(normalized),fill=bar.querySelector('i');
 if(fill)fill.style.width=`${normalized*100}%`;
 bar.setAttribute('aria-valuenow',String(Math.round(normalized*100)));
 bar.setAttribute('aria-valuetext',label);
}
function renderCurrentOptimization(s){
 const tools=s.tools||{}, activity=tools.activity||{}, optional=s.optional_capabilities||{};
 const observed=activity.observed_unique??tools.unique_used??0, calls=activity.total_calls??tools.total_calls??0;
 const peak=activity.peak_calls_per_execution??tools.peak_calls_per_execution??0;
 $('ov-activity-tools').textContent=f(observed);
 $('ov-activity-note').textContent=`observed types · ${f(calls)} calls · peak ${f(peak)}/execution`;
 const enabled=optional.enabled||0, used=optional.used||0;
 $('ov-optional-use').textContent=`${f(used)}/${f(enabled)}`;
 setCapabilityStatBar('ov-optional-bar',enabled?used/enabled:0);
 $('ov-optional-note').textContent=enabled
  ? `skill packs ${f(optional.skill_packs_used)}/${f(optional.skill_packs_enabled)} · MCPs are read-only`
  : 'no removable groups enabled for this runtime';
 $('ov-unused-packs').textContent=f(optional.unused||0);
 $('ov-unused-packs-note').textContent=optional.unused
  ? 'enabled packs with no activation in this run'
  : 'no unused user-installed packs in this run';
}
function sessionAlert(s){
 const source=s.source||{},capSpend=sessionCapSpend(s);
 if(!capSpend.available)return {type:'',msg:'',key:''};
 if(s.provider==='cursor')return {type:'',msg:'',key:''};
 const budget=dollarInputValue('budget',10);
 if(capSpend.spend>budget) return {type:'budget',msg:`This session passed your ${money(budget)} cap - now ${capSpend.partial?'at least ':''}${money(capSpend.spend)}`,key:`${source.id||s.session}:budget:${budget}`,budget,current:capSpend.spend};
 return {type:'',msg:'',key:''};
}
function notifyAlertForSession(s,opts={}){
 if(!s||pinned) return false;
 const a=sessionAlert(s);
 if(!a.msg) return false;
 const key=opts.forceCurrent?a.key+':current:'+Date.now():a.key;
 return fireNotification('Token Meter session budget',a.msg,key,{requireInteraction:true,renotify:!!opts.forceCurrent,dedupe:!opts.forceCurrent});
}
function renderAlertBanner(alert){
 const banner=$('banner');
 banner.classList.toggle('on',!!alert.msg);
 banner.classList.toggle('budget',alert.type==='budget');
 $('banner-msg').textContent=alert.msg||'';
 if(alert.type==='budget'){
  $('budget-add-btn').textContent='Add budget';
  $('budget-add').setAttribute('aria-label','Budget to add beyond current spend');
 }
}

function sessionStartMessage(s){
 for(const row of (s?.series||[])){
  const message=String(row?.user_message||row?.user_input||'').trim();
  if(message)return message;
 }
 const event=(s?.trace||[]).find(row=>row?.kind==='user'||row?.label==='User message');
 return String(event?.detail||'').trim();
}

function sessionDisplayName(s){
 const id=stateSessionId(s),key=stateSessionKey(s),rows=[...(s?.xsession?.current_sessions||[]),...(s?.xsession?.sessions||[]),...(allSessionInventory||[])];
 // Resumed rollouts and subagents share a logical id; only the same trace file may name the header.
 const row=rows.find(item=>item?.session&&String(item.session)===key)||rows.find(item=>!item?.session&&String(item?.id||'')===String(id||''));
 const name=String(row?.session_name||row?.title||'').trim();
 if(name)return name;
 const start=sessionStartMessage(s);
 return start&&start.length<=88?start:'Session';
}

function sessionDisplayThroughput(session){
 const completed=session?.throughput||{},live=session?.live_throughput||{};
 return live.available?live:completed;
}
function sessionThroughputNote(throughput){
 if(throughput?.basis==='live_end_to_end'){
  const steps=Number(throughput.completed_steps)||0;
  return `live checkpoints · ${f(steps)} completed ${countWord(steps,'step')}`;
 }
 return `${throughput?.basis==='tool_free'?'tool-free':'end-to-end'} · ${f(throughput?.sample_count||0)} timed ${countWord(throughput?.sample_count||0,'sample')} · ${pct(throughput?.timing_coverage)} coverage`;
}

const agentCostObservations=new Map();
let selectedAgentActivityId='';
function deriveAgentAttention(group,budget,previousCosts=new Map()){
 const reasonsByAgent=new Map();
 const addReason=(agentId,reason)=>{
  if(!agentId||!reason?.code)return;
  const reasons=reasonsByAgent.get(agentId)||[];
  if(!reasons.some(item=>item.code===reason.code))reasons.push({code:String(reason.code),explanation:String(reason.explanation||'')});
  reasonsByAgent.set(agentId,reasons);
 };
 for(const item of group?.attention||[]){
  for(const reason of item?.reasons||[])addReason(item?.agent_id,reason);
 }
 const covered=group?.totals?.cost_available===true&&Number.isFinite(Number(group?.totals?.cost));
 if(covered){
  const cap=Number(budget),rootId=String(group?.root_session_id||'');
  if(Number.isFinite(cap)&&cap>0&&Number(group.totals.cost)>cap){
   const root=(group?.agents||[]).find(agent=>agent?.kind==='root');
   if(root)addReason(root.id,{code:'cap_exceeded',explanation:`Covered estimated group spend is above this session's browser-local ${money(cap)} cap.`});
  }
  for(const agent of group?.agents||[]){
   if(agent?.kind==='root'||agent?.cost_available!==true||!Number.isFinite(Number(agent?.cost)))continue;
   const cost=Number(agent.cost);
   if(Number.isFinite(cap)&&cap>0&&cost>cap)addReason(agent.id,{code:'cap_exceeded',explanation:`Covered estimated child-agent spend is above this session's browser-local ${money(cap)} cap.`});
   if(agent.activity_state==='working'){
    const key=`${rootId}:${agent.id}`,previous=Number(previousCosts.get(key));
    if(Number.isFinite(previous)&&cost>previous&&cost-previous>=.25)addReason(agent.id,{code:'observed_live_growth',explanation:'Covered estimated cost increased by at least $0.25 while this child was reported working.'});
    previousCosts.set(key,cost);
   }
  }
 }
 return (group?.agents||[]).filter(agent=>reasonsByAgent.has(agent?.id)).map(agent=>({
  agent_id:agent.id,level:'needs_attention',reasons:reasonsByAgent.get(agent.id),
 }));
}
function agentIdentityPresentation(agent,index){
 if(agent?.kind==='root')return {primary:'Main session',secondary:''};
 const role=String(agent?.role||'').trim(),label=String(agent?.label||'').trim();
 if(role)return {primary:role,secondary:label&&label!==role?label:''};
 return {primary:label||`Agent ${index+1}`,secondary:''};
}
function agentDisplayLabel(agent,index){
 return agentIdentityPresentation(agent,index).primary;
}
function subagentCostHeat(cost,maxCost,available){
 const value=Number(cost),maximum=Number(maxCost);
 if(available!==true||!Number.isFinite(value)||value<0||!Number.isFinite(maximum)||maximum<=0)return '';
 const intensity=Math.max(0,Math.min(1,value/maximum));
 return `--cost-heat:hsl(${Math.round(190-160*intensity)} 88% 69%);--cost-fill:${Math.round(100*intensity)}%`;
}
function renderAgentActivity(s){
 const module=$('agent-activity'),group=s?.agent_group,agents=Array.isArray(group?.agents)?group.agents:[];
 if(!group||agents.length<2){
  module.hidden=true;
  selectedAgentActivityId='';
  return;
 }
 module.hidden=false;
 const attention=deriveAgentAttention(group,sessionBudgetForSession(s),agentCostObservations),attentionByAgent=new Map(attention.map(item=>[String(item.agent_id),item]));
 if(!agents.some(agent=>String(agent.id)===String(selectedAgentActivityId)))selectedAgentActivityId=group.selected_agent_id||agents[0].id;
 const selected=agents.find(agent=>String(agent.id)===String(selectedAgentActivityId))||agents[0];
 selectedAgentActivityId=selected.id;
 const selectedIndex=Math.max(0,agents.indexOf(selected)),selectedLabel=agentDisplayLabel(selected,selectedIndex);
 const childAgents=agents.filter(agent=>agent.kind!=='root'),childTotals=group.child_totals||{},knownChildCost=Number(childTotals.known_cost)||0;
 const maximumChildCost=Math.max(0,...childAgents.filter(agent=>agent.cost_available===true).map(agent=>Number(agent.cost)||0));
 const childCostCovered=childTotals.cost_available===true&&Number.isFinite(Number(childTotals.cost));
 const totals=group.totals||{},coverage=group.coverage||{},hidden=Number(group.hidden_agent_count)||0;
 $('agent-activity-context').textContent=selected.kind==='root'
  ? `${f(childAgents.length+hidden)} ${countWord(childAgents.length+hidden,'child agent')} under this session`
  : `Main session → ${selectedLabel}`;
 $('agent-activity-coverage').textContent=[coverage.relationships==='complete'?'':`Relationships ${coverage.relationships||'unavailable'}`,['complete','estimated'].includes(coverage.cost)?'':`cost ${coverage.cost||'unavailable'}`,coverage.tokens==='complete'?'':`tokens ${coverage.tokens||'unavailable'}`,hidden?`${f(hidden)} hidden (limit 100)`:''].filter(Boolean).join(' · ');
 const metric=(label,value,note)=>`<div class="agentActivityMetric metric"><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></div>`;
 $('agent-activity-metrics').innerHTML=[
  metric('Group cost',totals.cost_available?money(totals.cost):'--',totals.cost_available?'API-rate estimate · all agents cost-covered':totals.known_cost?`${money(totals.known_cost)} covered cost; some agents unavailable`:'cost unavailable'),
  metric('Child cost',childCostCovered?money(childTotals.cost):'--',childCostCovered?'API-rate estimate · root session excluded':knownChildCost?`${money(knownChildCost)} covered child cost; some agents unavailable`:'child cost unavailable'),
  metric('Tokens',totals.tokens_available?f(totals.tokens):'--',totals.tokens_available?'all agents token-covered':totals.known_tokens?`${f(totals.known_tokens)} covered tokens; some agents unavailable`:'token evidence unavailable'),
  metric('Agents',f(totals.agents||agents.length),`${f(childAgents.length+hidden)} children · ${f(attention.length)} ${countWord(attention.length,'signal')}`),
 ].join('');
 const rows=agents.map((agent,index)=>{
  const identity=agentIdentityPresentation(agent,index),label=identity.primary,selectedRow=String(agent.id)===String(selectedAgentActivityId);
  const model=agent.model&&agent.model!=='unknown'?agent.model:'model unavailable',state=agent.activity_state||'unknown';
  const tokenValue=agent.tokens_available===true?f(agent.tokens):'--',costValue=agent.cost_available===true?money(agent.cost):'--';
  const share=agent.group_cost_share==null?'--':pct(agent.group_cost_share),workTime=agent.work_time_s==null?'--':durationShort(agent.work_time_s);
  const secondary=identity.secondary?`${identity.secondary} · `:'',kind=agent.kind==='root'?'session':agent.kind;
  const destination=agent.navigable&&agent.session_id?` data-agent-session="${esc(agent.session_id)}"`:'';
  const heat=agent.kind==='root'?'':subagentCostHeat(agent.cost,maximumChildCost,agent.cost_available);
  return `<button class="agentRow${selectedRow?' selected':''}" type=button role=treeitem aria-level="${Math.max(1,Number(agent.depth||0)+1)}" aria-selected="${selectedRow?'true':'false'}" style="--agent-depth:${Math.max(0,Number(agent.depth)||0)}" data-agent-id="${esc(agent.id)}"${destination}><span class=agentIdentity><strong><span>${esc(label)}</span></strong><small>${esc(secondary+kind)}</small></span><span class=agentMetric>${esc(model)}</span><span class="agentState ${esc(state)}">${esc(state)}</span><span class=agentMetric>${esc(tokenValue)}</span><span class="agentMetric${heat?' subagentCostHeat':''}"${heat?` style="${heat}" title="Relative child cost in this session"`:''}>${esc(costValue)}</span><span class=agentMetric>${esc(share)}</span><span class=agentMetric>${esc(workTime)}</span></button>`;
 }).join('');
 $('agent-activity-tree').innerHTML='<div class=agentTreeHeader><span>Agent</span><span>Model</span><span>Status</span><span>Tokens</span><span class=fieldtip tabindex=0 aria-description="Cost color compares children in this session; it does not judge their work." data-tip="Cost color compares children in this session; it does not judge their work.">Cost</span><span>Share</span><span class=fieldtip tabindex=0 data-tip="Cumulative completed prompt-to-response time. Includes reasoning and tool use; excludes gaps between prompts.">Work time</span></div>'+rows;
 const selectedAttention=attentionByAgent.get(String(selected.id));
 $('agent-attention-detail').hidden=!selectedAttention;
 $('agent-attention-detail').innerHTML=selectedAttention
  ? `<b>${esc(selectedLabel)} · Cost and retry evidence</b><ul>${selectedAttention.reasons.map(reason=>`<li>${esc(reason.explanation)}</li>`).join('')}</ul>`
  : '';
}
$('agent-activity-tree').addEventListener('click',event=>{
 const row=event.target.closest('[data-agent-id]');
 if(!row||!CURRENT)return;
 selectedAgentActivityId=row.dataset.agentId;
 const destination=row.dataset.agentSession,currentId=String(stateSessionId(CURRENT)||'');
 if(destination&&destination!==currentId){selectSession(destination,{returnScope:sessionReturnScope});return;}
 renderAgentActivity(CURRENT);
});

function renderCurrentRun(s){
	 const source=s.source||{},throughput=sessionDisplayThroughput(s),wait=s.wait_time||{},context=s.context||{},tools=s.tools||{},cache=s.cache||{},timing=s.timing||{};
 const costAvailable=metricAvailable(s,'cost'),cacheAvailable=metricAvailable(s,'cache'),throughputAvailable=metricAvailable(s,'throughput'),cacheSavingsAvailable=costAvailable&&cacheAvailable&&cache.savings_available!==false;
 const contextPct=Math.max(0,Math.min(1,Number(context.latest_pct)||0)),toolResultTokens=tools.total_output_tokens||0;
 const alert=sessionAlert(s),capSpend=sessionCapSpend(s),frozen=!!s.ended,cursorEstimate=s.provider==='cursor',cap=dollarInputValue('budget',Infinity),costPerTask=s.analyses?.cost_per_task||{};
 $('preview-provider').innerHTML=runtimeBadgeContent(s,source.label||s.provider||'source');
 $('preview-provider').className='badge app '+appBadgeClass(s);
 $('preview-project').textContent=s.project||source.project||'';
 if(branchUiReady)renderSessionBranch(s);
 $('preview-model').textContent=s.primary_model||'model unavailable';
 renderWorkTags(s.work_tags);
 renderDesktopSessionLink(s);
 $('preview-state').textContent=pinned?'Pinned':(frozen?'Complete':'Live');
 $('preview-idle').textContent=frozen?'frozen':idleLabel(s.idle_s);
 setCostValue('preview-cost',money(s.total_cost)+(s.cost_approx?' est':''),costAvailable);
 $('preview-cost-note').textContent=alert.msg?alert.msg:(capSpend.available&&Number.isFinite(cap)?`${capSpend.partial?'At most ':''}${money(Math.max(0,cap-capSpend.spend))} before the ${money(cap)} session cap`:source.pricing_note||'Cost evidence unavailable.');
 $('preview-status-card').classList.toggle('warn',!!alert.msg);
 $('preview-context-pct').textContent=context.window?pct(contextPct):'--';
 $('preview-context-bar').setAttribute('aria-valuenow',String(Math.round(contextPct*100)));
 $('preview-context-bar').querySelector('i').style.width=`${Math.round(contextPct*100)}%`;
 $('preview-started-at').textContent=(dt12(timing.start_local)||'--').replace(' ','\n');
 $('preview-last-label').textContent=frozen?'Completed':'Last activity';
 $('preview-last-at').textContent=(dt12(timing.end_local)||'--').replace(' ','\n');
 $('preview-speed').textContent=throughputAvailable&&throughput.available?`${speedFmt(throughput.output_tps)} tok/s`:'--';
	 $('preview-speed-note').textContent=throughputAvailable&&throughput.available?sessionThroughputNote(throughput):'timing unavailable';
 $('preview-wait').textContent=wait.available?waitFmt(wait.total_s):'--';
 $('preview-wait-note').textContent=wait.available?`${waitFmt(wait.avg_s)} average · ${f(wait.sample_count)} completed`:'wait unavailable';
 $('preview-context').textContent=context.latest?f(context.latest):'--';
 $('preview-context-note').textContent=context.window?`${pct(contextPct)} of ${f(context.window)} window`:'window unavailable';
 $('preview-executions').textContent=f(s.turns||0);
 $('preview-executions-note').textContent=s.primary_model||'model unavailable';
 $('preview-tools').textContent=f(tools.total_calls||0);
 $('preview-tools-note').textContent=`${f(tools.activity?.observed_unique??tools.unique_used??0)} observed types`;
 $('preview-tool-results').textContent=f(toolResultTokens);
 $('preview-tool-results-note').textContent=`${f(tools.total_calls||0)} calls · estimated returned tokens`;
 $('preview-cache-rate').textContent=cacheAvailable?pct(cache.input_share||0):'--';
 $('preview-cache-rate-note').textContent=cacheAvailable?`${f(cache.total||0)} cached · ${f(cache.fresh||0)} fresh`:'cache accounting unavailable';
 $('preview-cache-saved').textContent=cacheSavingsAvailable?money(cache.saved||s.cache_saved||0):'--';
 $('preview-cache-saved-note').textContent=cacheSavingsAvailable?`${money(cache.cost||0)} cache cost · est. avoided spend`:'cache savings unavailable';
 $('preview-burn').textContent=costAvailable&&timing.duration_available?money(s.burn_usd_min)+(cursorEstimate?' est':''):'--';
 $('preview-burn-note').textContent=costAvailable&&timing.duration_available?`${f(s.burn_tok_min)} tokens/min · active`:(timing.duration_available?'billing unavailable':'execution timing unavailable');
 setCostValue('preview-cost-task',money(costPerTask.per_task||0)+(cursorEstimate?' est':''),costAvailable);
 $('preview-cost-task-note').textContent=costAvailable?`${f(costPerTask.completed||0)} completed tasks${cursorEstimate?'':' · est.'}`:'billing unavailable';
 const startMessage=sessionStartMessage(s)||(s.provider==='pi'?'Pi message content is not captured.':'No user message captured.');
 $('preview-start').textContent=startMessage;
 $('preview-start').dataset.tip=startMessage;
 $('preview-start').setAttribute('aria-description',startMessage);
}

function renderSession(s){
 CURRENT=s;
 loadBudgetForSession(s);
 renderCurrentBudgetWarning(s.xsession?.budget||LATEST?.xsession?.budget);
 const frozen=!!s.ended, source=s.source||{};
 $('s-provider').innerHTML=runtimeBadgeContent(s,source.label||s.provider||'source');
 $('s-provider').className='badge app '+appBadgeClass(s);
 $('s-repo').textContent=s.project||source.project||'';
 $('s-id').textContent=s.session||source.id||'';
 $('s-pricing').textContent=source.pricing_note||'';
 const modelIdentity=source.model_identity||{},modelIdentityAction=$('session-model-identity-action');
 const assignedModel=modelIdentity.source==='user_assigned';
 const modelIdentityAssignable=!!modelIdentity.assignable&&!!modelIdentity.key;
 modelIdentityAction.hidden=!modelIdentityAssignable;
 modelIdentityAction.style.display=modelIdentityAssignable?'inline-flex':'none';
 modelIdentityAction.textContent=assignedModel?'Change model':(modelIdentity.kind==='missing_model'?'Add model':'Assign model');
 modelIdentityAction.onclick=modelIdentityAction.hidden?null:()=>openSessionModelIdentityDialog({
  label:source.label||s.provider,project:s.project||source.project||'',
  title:sessionDisplayName(s),primary_model:s.primary_model,
  models:s.primary_model?[s.primary_model]:[],model_identity:modelIdentity,
 });
 const startMessage=sessionStartMessage(s);
 $('session-start').hidden=!startMessage;
 $('session-start-text').textContent=startMessage;
 const sessionActions=s.xsession?.session_actions||{}, deleteButton=$('session-delete');
 const deleteAvailable=sessionDeleteAvailable(source,sessionActions);
 deleteButton.hidden=!source.id||!deleteAvailable;
 deleteButton.disabled=!deleteAvailable;
 deleteButton.title=deleteAvailable?'Move this session log to the system Trash.':'Session deletion is unavailable for this provider.';
 $('frozen').classList.toggle('on',frozen);
 const costAvailable=metricAvailable(s,'cost'),tokensAvailable=metricAvailable(s,'tokens'),inputAvailable=metricAvailable(s,'input_tokens'),outputAvailable=metricAvailable(s,'output_tokens'),cacheAvailable=metricAvailable(s,'cache'),throughputAvailable=metricAvailable(s,'throughput'),cursorEstimate=s.provider==='cursor';
 setCostValue('cost',money(s.total_cost)+(s.cost_approx?' est':''),costAvailable);
 const tok=s.tokens||{}, sem=s.semantic||{}, tools=s.tools||{}, cache=s.cache||{}, latestCache=cache.latest||{}, timing=s.timing||{};
 const cacheSavingsAvailable=costAvailable&&cacheAvailable&&cache.savings_available!==false;
 $('cost-tip').innerHTML=costTipHtml(s,tok);
 const inputTokens=(tok.input||0)+(tok.cache_read||0)+(tok.cache_write||0), outputTokens=tok.output||0;
	 const thinkingTokens=sem.reasoning||0, toolCallTokens=tools.total_output_tokens||0, throughput=sessionDisplayThroughput(s);
	 const speedPart=throughputAvailable&&throughput.available?` · <b>${speedFmt(throughput.output_tps)}</b> ${cursorEstimate?'visible out tok/s est':'out tok/s'}`:'';
	 $('sub').innerHTML=`<b>${inputAvailable?f(inputTokens)+(cursorEstimate?' est':''):'--'}</b> in · <b>${outputAvailable?f(outputTokens)+(cursorEstimate?' est':''):'--'}</b> out${speedPart} · <b>${outputAvailable?f(thinkingTokens)+(cursorEstimate?' est':''):'--'}</b> thinking · <b>${f(toolCallTokens)}</b> estimated tool results · <b>${f(s.turns)}</b> executions`;
 $('input-tok').textContent=inputAvailable?f(inputTokens)+(cursorEstimate?' est':''):'--';
 $('input-toksm').textContent=inputAvailable?(cursorEstimate?`${f(tok.input||0)} context proxy · cache unknown`:`${f(tok.input||0)} uncached · ${f((tok.cache_read||0)+(tok.cache_write||0))} cache`):'input evidence unavailable';
 $('cache-hit').textContent=cacheAvailable?pct(cache.input_share||0):'--';
 $('cache-hitsm').textContent=cacheAvailable?`${f(cache.total||0)} cached · ${f(cache.fresh||0)} uncached`:'cache accounting unavailable';
 $('cache-tok').textContent=cacheAvailable?f(cache.total||0):'--';
	 $('cache-toksm').textContent=cacheAvailable?`${f(cache.read||0)} read · ${f(cache.write||0)} write · latest ${f(latestCache.tokens||0)}`:'cache accounting unavailable';
	 $('output-tok').textContent=outputAvailable?f(outputTokens)+(cursorEstimate?' est':''):'--';
	 $('output-tps').textContent=throughputAvailable&&throughput.available?speedFmt(throughput.output_tps):'--';
	 $('output-toksm').textContent=throughputAvailable&&throughput.available?`${sessionThroughputNote(throughput)}${cursorEstimate?' · visible text estimate':''}`:(outputAvailable?`of ${f(s.total_tokens)} total · timing unavailable${cursorEstimate?' · visible text estimate':''}`:'output evidence unavailable');
 $('think-tok').textContent=outputAvailable?f(thinkingTokens)+(cursorEstimate?' est':''):'--';
 $('think-toksm').textContent=cursorEstimate?`${f(s.analyses?.reasoning?.think_turns||0)} reasoning exec · hidden reasoning excluded`:outputAvailable?f(s.analyses?.reasoning?.think_turns||0)+' reasoning exec':`${f(s.analyses?.reasoning?.think_turns||0)} reasoning exec · duration only`;
 $('toolcall-tok').textContent=f(toolCallTokens);
 $('toolcall-toksm').textContent=f(tools.total_calls||0)+' calls returned';
 $('cache-saved').textContent=cacheSavingsAvailable?money(cache.saved||s.cache_saved||0):'--';
 $('cache-savedsm').textContent=cacheSavingsAvailable?`${money(cache.cost||0)} cache cost`:'cache savings unavailable';
 $('burn').textContent=costAvailable&&timing.duration_available?money(s.burn_usd_min)+(cursorEstimate?' est':''):'--';
 $('burnsm').textContent=costAvailable&&timing.duration_available?`${f(s.burn_tok_min)} tok/min · active${cursorEstimate?' · local proxy':''}`:(timing.duration_available?'active timing available · billing unavailable':'execution timing unavailable');
 setCostValue('cpt',money(s.analyses?.cost_per_task?.per_task||0)+(cursorEstimate?' est':''),costAvailable);
 $('cptsm').textContent=costAvailable?f(s.analyses?.cost_per_task?.completed||0)+` completed${cursorEstimate?' · local proxy':''}`:'billing unavailable';
 renderOverview(s); renderCurrentOptimization(s); renderCurrentRun(s); renderSessionEfficiency(s); renderAgentActivity(s);

 const semanticAvailable=s.semantic_available!==false,semTot=Object.values(sem).reduce((a,b)=>a+(b||0),0)||1;
 const segs=[['reasoning','var(--reason)'],['output','var(--output)'],['retrieval','var(--retrieval)'],['coordination','var(--coord)']];
 $('semhint').textContent=semanticAvailable?'':'semantic data unavailable';
 $('sembar').innerHTML=tokensAvailable&&semanticAvailable?segs.map(([k,c])=>{const w=100*(sem[k]||0)/semTot; if(w<0.6)return''; return `<span style="width:${w}%;background:${c}">${w>8?f(sem[k]):''}</span>`;}).join(''):'';
 drawIO(s.series||[]);

 const liveView=!pinned;

 const alert=sessionAlert(s);
 $('cost').classList.toggle('alert',!!alert.msg);
 renderAlertBanner(alert);
 if(alert.msg&&liveView) notifyAlertForSession(s);
}

let pendingSessionDelete=null;
function sessionDeleteAvailable(target,actions){
 const provider=String(target?.provider||target?.client||'').toLowerCase();
 return Boolean(actions?.available)&&!(actions?.read_only_providers||[]).map(String).map(value=>value.toLowerCase()).includes(provider);
}
function sessionDeleteTargetFromState(state){
 const id=stateSessionId(state),source=state?.source||{},key=stateSessionKey(state);
 if(!id)return null;
 const row=(state?.xsession?.sessions||[]).find(item=>sessionRowKey(item)===key);
 return row||{id,session:state?.session||'',title:sessionStartMessage(state)||id,project:state?.project||source.project||'',label:source.label||state?.provider||'session',provider:source.provider||state?.provider||''};
}
function openSessionDeleteDialog(target,actions,returnToAll=false,isLive=false){
 if(!target?.id||!sessionDeleteAvailable(target,actions))return;
 pendingSessionDelete={target,actions,returnToAll,isLive};
 $('session-delete-sub').textContent=`${target.label||target.provider||'Session'} · ${target.project||'No project'}`;
 $('session-delete-evidence').innerHTML=`<div><b>${esc(target.title||'(untitled session)')}</b></div><div>This removes the underlying local JSONL session record from Token Meter.</div>`;
 $('session-delete-id').textContent=target.id;
 $('session-delete-note').textContent=isLive?'This appears to be the live session. Stop its agent first to prevent further writes. The session record will be moved to the system Trash; provider metadata and configuration are not changed.':'The session record will be moved to the system Trash and can be recovered there. Provider metadata and configuration are not changed.';
 $('session-delete-confirm').disabled=false;
 $('session-delete-confirm').textContent='Move to Trash';
 $('session-delete-dialog').showModal();
}
$('session-delete').onclick=()=>{
 const target=sessionDeleteTargetFromState(CURRENT),actions=CURRENT?.xsession?.session_actions||{};
 if(target)openSessionDeleteDialog(target,actions,sessionReturnScope==='all',Boolean(!pinned&&LATEST&&sessionRowKey(target)===stateSessionKey(LATEST)));
};
$('session-delete-cancel').onclick=()=>{$('session-delete-dialog').close();pendingSessionDelete=null;};
$('session-delete-dialog').addEventListener('cancel',()=>{pendingSessionDelete=null;});
$('session-delete-confirm').onclick=async()=>{
 if(!pendingSessionDelete)return;
 const {target,actions,returnToAll}=pendingSessionDelete,button=$('session-delete-confirm');
 button.disabled=true;button.textContent='Moving…';
 try{
  const response=await fetch('/session/delete',{method:'POST',headers:{'Content-Type':'application/json','X-Token-Meter-Action':actions.token},body:JSON.stringify({session_id:target.id,trace:target.session||''})});
  const result=await response.json();
  if(!response.ok||!result.ok)throw new Error(result.error||'Could not delete session.');
  $('session-delete-dialog').close();pendingSessionDelete=null;sessionRequest++;pinned=null;
  const stateResponse=await fetch('/state',{cache:'no-store'}),state=await stateResponse.json();
  adoptFreshDiscounts(state);LATEST=state;CURRENT=state;
  if(returnToAll||!stateSessionId(state)){
   sessionScope='all';sessionReturnScope='all';
   if(history.replaceState)history.replaceState(null,'','/#sessions-all');
   applyHashRoute();
   if(state.xsession)renderAllSessions(state.xsession);
	  }else{
	   if(history.replaceState)history.replaceState(null,'','/#sessions');
	   applyHashRoute();
  }
  showAppToast({title:'Session moved to Trash',body:`${target.title||target.id} was removed from Token Meter.`,kind:'warn',sticky:true});
 }catch(error){
  button.disabled=false;button.textContent='Move to Trash';
  showAppToast({title:'Session deletion failed',body:error.message||String(error),kind:'warn',sticky:true});
 }
};

let pendingSessionModelIdentity=null;
function sessionModelIdentitySourceLabel(identity){
 if(identity?.kind==='missing_model')return identity.source==='user_assigned'?'User-assigned Claude model':'Underlying Hermes model unavailable';
 return ({user_assigned:'User-assigned model',unresolved:'Underlying model unavailable'}[identity?.source]||'Saved model evidence');
}
function sessionModelIdentityPresentation(identity){
 const hermes=identity?.kind==='missing_model'&&identity?.runtime==='hermes';
 return hermes
  ? {provider:'hermes',fieldLabel:'Claude model ID',placeholder:'claude-sonnet-5',noun:'Claude model ID',observedLabel:'Bedrock application profile'}
  : {provider:'codex',fieldLabel:'Codex / OpenAI model ID',placeholder:'gpt-model-version',noun:'Codex / OpenAI model ID',observedLabel:''};
}
function openSessionModelIdentityDialog(target){
 const identity=target?.model_identity||{};
 if(!identity.assignable||!identity.key)return;
 const presentation=sessionModelIdentityPresentation(identity),candidates=Array.isArray(identity.candidates)?identity.candidates:[];
 pendingSessionModelIdentity={target,identity,presentation};
 $('session-model-identity-sub').textContent=`${target.label||target.provider||'Codex'} · ${target.project||'No project'}`;
 const observedLabel=identity.observed_label||presentation.observedLabel;
 const observed=observedLabel?` Observed: ${observedLabel}.`:'';
 const candidateText=candidates.length?` Configured public ${candidates.length===1?'alias':'aliases'}: ${candidates.join(', ')}.`:'';
 $('session-model-identity-evidence').innerHTML=`<div><b>${esc(target.title||'(untitled session)')}</b></div><div>${esc(sessionModelIdentitySourceLabel(identity))}.${esc(observed+candidateText)} Token Meter could not verify an exact billable model from the available local aggregate.</div>`;
 $('session-model-identity-model-label').textContent=presentation.fieldLabel;
 $('session-model-identity-model').placeholder=presentation.placeholder;
 $('session-model-identity-candidates').innerHTML=candidates.map(model=>`<option value="${esc(model)}"></option>`).join('');
 $('session-model-identity-model').value=identity.source==='user_assigned'?(identity.assigned_model||target.primary_model||(target.models||[])[0]||''):(candidates.length===1?candidates[0]:'');
 $('session-model-identity-remove').hidden=identity.source!=='user_assigned';
 $('session-model-identity-save').disabled=false;
 $('session-model-identity-save').textContent='Save model';
 $('session-model-identity-dialog').showModal();
 requestAnimationFrame(()=>$('session-model-identity-model').focus());
}
function closeSessionModelIdentityDialog(){
 $('session-model-identity-dialog').close();pendingSessionModelIdentity=null;
}
async function requestSessionModelIdentity(payload){
 const token=agentActionToken();
 if(!token)throw new Error('Settings are not ready yet. Wait for live state and try again.');
 const response=await fetch('/settings/session-model-identity',{method:'POST',headers:{'Content-Type':'application/json','X-Token-Meter-Action':token},body:JSON.stringify(payload)});
 const result=await response.json();
 if(!response.ok||!result.ok)throw new Error(result.error||'Could not save the session model.');
 return result;
}
$('session-model-identity-cancel').onclick=closeSessionModelIdentityDialog;
$('session-model-identity-dialog').addEventListener('cancel',()=>{pendingSessionModelIdentity=null;});
$('session-model-identity-save').onclick=async()=>{
 const pending=pendingSessionModelIdentity,button=$('session-model-identity-save');
 if(!pending)return;
 const model=$('session-model-identity-model').value.trim();
 if(!model){$('session-model-identity-model').focus();showAppToast({title:'Model ID required',body:`Enter the exact ${pending.presentation.noun} used for this saved session.`,kind:'warn'});return;}
 button.disabled=true;button.textContent='Saving…';
 try{
  await requestSessionModelIdentity({session_key:pending.identity.key,provider:pending.presentation.provider,model});
  closeSessionModelIdentityDialog();
  forceAllSessionRowRefresh=true;
  await refreshLiveState();
  if(pinned)await refreshSelectedSession({show:true});
  showAppToast({title:'Session model assigned',body:'Only this saved session changed. Model prices were not changed.',kind:'good'});
 }catch(error){
  button.disabled=false;button.textContent='Save model';
  showAppToast({title:'Session model could not be saved',body:error.message||String(error),kind:'warn',sticky:true});
 }
};
$('session-model-identity-remove').onclick=async()=>{
 const pending=pendingSessionModelIdentity,button=$('session-model-identity-remove');
 if(!pending)return;
 button.disabled=true;button.textContent='Clearing…';
 try{
  await requestSessionModelIdentity({session_key:pending.identity.key,remove:true});
  closeSessionModelIdentityDialog();
  forceAllSessionRowRefresh=true;
  await refreshLiveState();
  if(pinned)await refreshSelectedSession({show:true});
  showAppToast({title:'Session model assignment cleared',body:'This saved session remains unavailable until exact local evidence is present.',kind:'good'});
 }catch(error){
  button.disabled=false;button.textContent='Clear assignment';
  showAppToast({title:'Session model could not be cleared',body:error.message||String(error),kind:'warn',sticky:true});
 }
};
