const CAPABILITY_SORT_KEYS=new Set(['name','type','runtime','state','use','last','control']);
const CAPABILITY_TYPES=new Set(['all','tool','mcp','skill']);
const CAPABILITY_PAGE_SIZE=100;
let capabilityType='all', capabilityState='all', capabilityRuntime='all', capabilitySearch='';
let capabilityVisibleLimit=CAPABILITY_PAGE_SIZE;
let lastCapabilityReviewRevision='', lastCapabilityInventoryRevision='', capabilityLastGoodAt=0;
let pendingCapabilityTable=null, pendingCapabilityReview=null, capabilityInventorySnapshot=null;
let capabilityInventoryLoading=false, capabilitySearchTimer=null, capabilityEvidenceState='loading';
const selectedCapabilityIds=new Set();
const openCapabilityReviewIds=new Set();
let capabilitySort=localStorage.getItem('tm_capability_sort')||'name';
let capabilitySortDir=localStorage.getItem('tm_capability_sort_dir')||'asc';
if(capabilitySort==='returned')capabilitySort='use';
if(!CAPABILITY_SORT_KEYS.has(capabilitySort))capabilitySort='name';
if(!['asc','desc'].includes(capabilitySortDir))capabilitySortDir='asc';
function capabilityUseCount(row){ return row.type==='skill'?(row.activations||0):(row.calls||0); }
function capabilityConfigurationText(row){
 if(row.type==='tool')return row.configuration||'Unknown';
 if(row.configuration)return row.configuration;
 return row.enabled===true?'Enabled':row.enabled===false?'Disabled':'Unknown';
}
function capabilityEvidenceText(row){
 const used=capabilityUseCount(row);
 if(used)return 'Observed';
 if(row.unmeasurable)return row.measurement==='instruction'?'Instruction-only':'Evidence unavailable';
 if(row.type==='tool'&&row.advertised_sessions)return 'Definition observed';
 return 'No use observed';
}
function capabilityControlText(row){
 if(!row.mutable)return 'read-only';
 const action=row.enabled?'Disable':'Enable';
 return row.type==='skill'?`${action} pack`:action;
}
function capabilitySortValue(row,key){
 if(key==='name')return row.name||row.identity||'';
 if(key==='type')return {tool:'Tool',mcp:'MCP',skill:'Skill'}[row.type]||row.type||'';
 if(key==='runtime')return [row.runtime,row.source,row.plugin_id,row.path].filter(Boolean).join(' · ');
 if(key==='state')return capabilityConfigurationText(row);
 if(key==='use')return Number(capabilityUseCount(row))||0;
 if(key==='last'){
  if(!row.last_used||row.last_used==='Never')return 0;
  const parsed=Date.parse(row.last_used);
  return Number.isFinite(parsed)?parsed:0;
 }
 if(key==='control')return capabilityControlText(row);
 return '';
}
function compareCapabilityRows(a,b){
 const av=capabilitySortValue(a,capabilitySort), bv=capabilitySortValue(b,capabilitySort);
 const cmp=typeof av==='number'&&typeof bv==='number'
  ? av-bv
  : String(av).localeCompare(String(bv),undefined,{numeric:true,sensitivity:'base'});
 return capabilitySortDir==='desc'?-cmp:cmp;
}
function paintCapabilitySort(){
 [...$('c-table').querySelectorAll('th[data-csort]')].forEach(th=>{
  const active=th.dataset.csort===capabilitySort;
  th.classList.toggle('sort-asc',active&&capabilitySortDir==='asc');
  th.classList.toggle('sort-desc',active&&capabilitySortDir==='desc');
  th.setAttribute('aria-sort',active?(capabilitySortDir==='asc'?'ascending':'descending'):'none');
  th.title=`Sort by ${th.textContent.trim()}${active?` (${capabilitySortDir==='asc'?'ascending':'descending'})`:''}`;
 });
 const mobile=$('c-mobile-sort'), choice=`${capabilitySort}:${capabilitySortDir}`;
 if(mobile&&[...mobile.options].some(option=>option.value===choice))mobile.value=choice;
}
function setCapabilitySort(key){
 if(!CAPABILITY_SORT_KEYS.has(key))return;
 capabilityVisibleLimit=CAPABILITY_PAGE_SIZE;
 if(capabilitySort===key)capabilitySortDir=capabilitySortDir==='asc'?'desc':'asc';
 else{
  capabilitySort=key;
  capabilitySortDir=['use','last'].includes(key)?'desc':'asc';
 }
 localStorage.setItem('tm_capability_sort',capabilitySort);
 localStorage.setItem('tm_capability_sort_dir',capabilitySortDir);
 paintCapabilitySort();
 const inventory=currentCapabilityInventory();
 if(inventory)renderCapabilityTable(inventory,{force:true});
}
function capabilityConfigurationClass(row){
 if(row.enabled===null||row.enabled===undefined)return 'unknown';
 return row.enabled?'on':'off';
}
function capabilityEvidenceClass(row){
 if(capabilityUseCount(row))return 'observed';
 if(row.unmeasurable)return 'unknown';
 if(row.type==='tool'&&/Deferred|Mixed/.test(row.state||''))return 'lazy';
 return 'unknown';
}
function capabilityAction(row){
 if(!row.mutable) return '<span class=subline>read-only</span>';
 const next=!row.enabled;
 const label=row.type==='skill'?`${next?'Enable':'Disable'} pack`:(next?'Enable':'Disable');
 const locked=capabilityEvidenceState!=='current';
 return `<button class="capAction ${next?'':'disable'}" data-cap-id="${esc(row.id)}" ${locked?'disabled data-evidence-locked="1" title="Wait for current evidence before changing settings"':''}>${label}</button>`;
}
function currentCapabilitySummary(){return LATEST?.xsession?.capabilities||null;}
function currentCapabilityInventory(){
 const cap=currentCapabilitySummary();
 if(!cap||!capabilityInventorySnapshot||capabilityInventorySnapshot.inventory_revision!==cap.inventory_revision)return null;
 return {...cap,items:capabilityInventorySnapshot.items||[]};
}
function paintCapabilityFilters(){
 [...$('c-type-filter').querySelectorAll('button')].forEach(button=>{
  const on=capabilityType===button.dataset.ctype;
  button.classList.toggle('on',on);
  button.setAttribute('aria-pressed',String(on));
 });
 [...document.querySelectorAll('[data-cap-jump]')].forEach(button=>{
  const on=capabilityType===button.dataset.capJump;
  button.classList.toggle('on',on);
  button.setAttribute('aria-pressed',String(on));
 });
 [...$('c-state-filter').querySelectorAll('button')].forEach(button=>{
  const on=capabilityState===button.dataset.cstate;
  button.classList.toggle('on',on);
  button.setAttribute('aria-pressed',String(on));
 });
 const filters=[];
 if(capabilityType!=='all')filters.push({key:'type',label:{tool:'Tools',mcp:'MCPs',skill:'Skills'}[capabilityType]||capabilityType});
 if(capabilityState!=='all')filters.push({key:'state',label:{enabled:'Configured on',observed:'Observed',unused:'No use observed'}[capabilityState]});
 if(capabilityRuntime!=='all')filters.push({key:'runtime',label:capabilityRuntime});
 if(capabilitySearch.trim())filters.push({key:'search',label:`Search: ${capabilitySearch.trim()}`});
 const active=$('c-active-filters');
 active.hidden=!filters.length;
 active.innerHTML=filters.length?`<span>Active</span>${filters.map(filter=>`<button type=button data-cap-clear="${filter.key}" aria-label="Remove ${esc(filter.label)} filter">${esc(filter.label)} <b aria-hidden=true>×</b></button>`).join('')}`:'';
 $('c-clear-filters').hidden=!filters.length;
 [...active.querySelectorAll('[data-cap-clear]')].forEach(button=>button.onclick=()=>clearCapabilityFilter(button.dataset.capClear));
}
function clearCapabilityFilter(key){
 if(key==='type')capabilityType='all';
 if(key==='state')capabilityState='all';
 if(key==='runtime')capabilityRuntime='all';
 if(key==='search'){capabilitySearch='';$('c-search').value='';}
 capabilityVisibleLimit=CAPABILITY_PAGE_SIZE;
 paintCapabilityFilters();
 const inventory=currentCapabilityInventory();
 if(inventory)renderCapabilityTable(inventory,{force:true});
}
function clearCapabilityFilters(){
 capabilityType='all';capabilityState='all';capabilityRuntime='all';capabilitySearch='';capabilityVisibleLimit=CAPABILITY_PAGE_SIZE;
 $('c-search').value='';
 paintCapabilityFilters();
 const inventory=currentCapabilityInventory();
 if(inventory)renderCapabilityTable(inventory,{force:true});
}
function capabilityPackName(row){
 const value=String(row?.plugin_id||row?.name||'Skill pack');
 return value.split('@')[0]||value;
}
function capabilityGroupFingerprint(row){
 return JSON.stringify([
  row?.id||'',Boolean(row?.enabled),Boolean(row?.used),Boolean(row?.unmeasurable),
  row?.measurement||'',row?.runtime||'',row?.plugin_id||'',row?.setting_path||'',
  Number(row?.scanned_sessions||0),
  Number(row?.unused_eager_definition_tokens||0),[...(row?.members||[])].sort()
 ]);
}
function updateCapabilitySelection(cap){
 const valid=new Set(unusedReviewGroups(cap).map(row=>row.id));
 [...selectedCapabilityIds].forEach(id=>{if(!valid.has(id))selectedCapabilityIds.delete(id);});
 const button=$('c-disable-selected'), count=selectedCapabilityIds.size;
 $('c-selection-bar').hidden=!count;
 $('c-selection-count').textContent=`${f(count)} ${countWord(count,'pack')} selected`;
 button.disabled=!count||capabilityEvidenceState!=='current';
 button.textContent='Disable selected…';
 button.title=capabilityEvidenceState==='current'?'Review exact controls before disabling':'Wait for current evidence before changing settings';
}
function clearCapabilitySelection(){
 selectedCapabilityIds.clear();
 [...$('c-review-list').querySelectorAll('[data-cap-select]')].forEach(input=>{input.checked=false;});
 updateCapabilitySelection(currentCapabilitySummary());
}
function renderCapabilityReviewQueue(cap,{force=false}={}){
 if(!cap)return;
 const groups=unusedReviewGroups(cap), optional=cap?.summary?.optional||{};
 const revision=String(cap.review_revision||JSON.stringify([
  optional.review_candidates||[],groups.map(capabilityGroupFingerprint)
 ]));
 updateCapabilitySelection(cap);
 if(!force&&revision===lastCapabilityReviewRevision)return;
 const queue=$('c-review-list');
 const queueBusy=queue.matches(':hover')||queue.contains(document.activeElement);
 if(queueBusy&&!force){pendingCapabilityReview=cap;return;}
 pendingCapabilityReview=null;
 const panel=$('c-review-queue');
 panel.classList.toggle('warn',groups.length>0);
 panel.classList.toggle('good',groups.length===0);
 $('c-review-title').textContent=groups.length?'Needs review':'Nothing needs review';
 const runtimes=[...new Set(groups.map(row=>row.runtime||'Runtime'))];
 const sharedRuntime=runtimes.length===1?runtimes[0]:'';
 const restartNote=runtime=>`Restart ${runtime} to apply. Re-enable from All capabilities.`;
  $('c-review-detail').textContent=groups.length
  ? `No observed use means review, not proof of disuse.${sharedRuntime?` ${restartNote(sharedRuntime)}`:''}`
  : `${f((optional.instruction_packs||0)+(optional.unknown_evidence_packs||0)+(optional.unscanned_packs||0))} ${countWord((optional.instruction_packs||0)+(optional.unknown_evidence_packs||0)+(optional.unscanned_packs||0),'pack')} excluded for missing use or coverage evidence.`;
 $('c-review-count').textContent=groups.length?`${f(groups.length)} user-installed ${countWord(groups.length,'pack')}`:'Queue clear';
 const head=`<div class=capReviewHead aria-hidden=true><span class=capReviewCols><span>Pack</span><span>Observed use</span><span>Prompt load</span><span>Affected</span><span>Status</span></span><span>Select</span></div>`;
 queue.innerHTML=groups.length?head+groups.map(row=>{
  const members=row.members||[], scanned=Number(row.scanned_sessions||0);
  const overhead=Number(row.unused_eager_definition_tokens||0);
  const runtime=row.runtime||'Runtime', setting=row.setting_path||'runtime plugin settings';
  const name=capabilityPackName(row), affected=`${f(members.length)} ${countWord(members.length,'skill')}`;
  const use=`0 uses · ${f(scanned)} ${countWord(scanned,'log')}`;
  const loadDetail=overhead?`Est. ${f(overhead)} avoidable definition tokens`:'Not measured';
  const load=overhead?`≈${compactNumber(overhead)} tokens`:'—';
  const memberChips=members.map(member=>`<span>${esc(member)}</span>`).join('');
  const label=`${name}, ${runtime}, ${use}, prompt load ${loadDetail.toLowerCase()}, ${affected}, no use observed`;
  return `<article class=capReviewItem data-cap-review-id="${esc(row.id)}"><details class=capReviewRow data-cap-review-open="${esc(row.id)}"${openCapabilityReviewIds.has(row.id)?' open':''}><summary class=capReviewCols aria-label="${esc(label)}"><span class=capReviewName><strong>${esc(name)}</strong><span>${esc(runtime)} · User-installed</span></span><span class=capReviewNum>${esc(use)}</span><span class=capReviewNum title="${esc(loadDetail)}">${esc(load)}</span><span class=capReviewNum>${esc(affected)}</span><span class="stateDot unknown">No use observed</span></summary><div class=capReviewDisclosureBody><dl class=capReviewFacts><div><dt>Observed use</dt><dd>0 activations · ${f(scanned)} ${esc(runtime)} ${countWord(scanned,'log')}</dd></div><div><dt>Prompt-load impact</dt><dd>${esc(loadDetail)}</dd></div><div><dt>Affected</dt><dd>${affected}</dd></div></dl>${sharedRuntime?'':`<p class=capReviewImpact>${esc(restartNote(runtime))}</p>`}<div><b>Affected skills</b><div class=capReviewMembers>${memberChips||'<span>No member names available</span>'}</div></div><div>${overhead?'The avoidable-load estimate is the cumulative runtime-reported definition evidence across the scanned logs, not a per-prompt saving.':'Prompt saving is not measurable from the available evidence; this is only an activation review.'}</div><div><b>Exact control</b><div class=capReviewControl>${esc(row.id)}</div></div><div><b>Setting</b><div class=capReviewControl>${esc(setting)}</div></div></div></details><label class=capSelect><input type=checkbox data-cap-select="${esc(row.id)}" aria-label="Select ${esc(name)}" ${selectedCapabilityIds.has(row.id)?'checked':''}><span>Select</span></label></article>`;
 }).join(''):`<div class=capQueueEmpty><h3>No candidate requires a decision</h3><p>Only packs with reliable activation evidence appear here.</p><button class=tbtn type=button data-cap-browse>Browse all capabilities</button></div>`;
 lastCapabilityReviewRevision=revision;
 [...queue.querySelectorAll('[data-cap-review-open]')].forEach(details=>details.addEventListener('toggle',()=>{
  if(details.open)openCapabilityReviewIds.add(details.dataset.capReviewOpen);else openCapabilityReviewIds.delete(details.dataset.capReviewOpen);
 }));
 [...queue.querySelectorAll('[data-cap-select]')].forEach(input=>input.onchange=()=>{
  if(input.checked)selectedCapabilityIds.add(input.dataset.capSelect);else selectedCapabilityIds.delete(input.dataset.capSelect);
  updateCapabilitySelection(cap);
 });
 queue.querySelector('[data-cap-browse]')?.addEventListener('click',()=>openCapabilityInventory());
 updateCapabilitySelection(cap);
}
function renderCapabilityTable(cap,{force=false}={}){
 if(!cap)return;
 const tableBusy=$('c-table').matches(':hover')||$('c-table').contains(document.activeElement);
 if(tableBusy&&!force){pendingCapabilityTable=cap;return;}
 pendingCapabilityTable=null;
 const all=cap?.items||[];
 const runtimes=[...new Set(all.map(row=>row.runtime).filter(Boolean))].sort((a,b)=>a.localeCompare(b));
 if(capabilityRuntime!=='all'&&!runtimes.includes(capabilityRuntime))capabilityRuntime='all';
 $('c-runtime-filter').innerHTML='<option value="all">All runtimes</option>'+runtimes.map(runtime=>`<option value="${esc(runtime)}">${esc(runtime)}</option>`).join('');
 $('c-runtime-filter').value=capabilityRuntime;
 const q=capabilitySearch.trim().toLowerCase();
 const rows=all.filter(row=>{
  if(capabilityType!=='all'&&row.type!==capabilityType)return false;
  if(capabilityRuntime!=='all'&&row.runtime!==capabilityRuntime)return false;
  if(capabilityState==='enabled'&&row.enabled!==true)return false;
  if(capabilityState==='observed'&&!capabilityUseCount(row))return false;
  if(capabilityState==='unused'&&(row.used||row.unmeasurable))return false;
  if(q&&![row.name,row.identity,row.runtime,row.source,row.plugin_id,row.path,row.state].join(' ').toLowerCase().includes(q))return false;
  return true;
 }).map((row,index)=>({row,index})).sort((a,b)=>compareCapabilityRows(a.row,b.row)||a.index-b.index).map(item=>item.row);
 const visibleRows=rows.slice(0,capabilityVisibleLimit);
 $('c-filter-count').textContent=rows.length===visibleRows.length?`${f(rows.length)} shown · ${f(all.length)} discovered`:`${f(visibleRows.length)} of ${f(rows.length)} matches shown · ${f(all.length)} discovered`;
 $('c-table').querySelector('tbody').innerHTML=rows.length?visibleRows.map(row=>{
  const source=[row.runtime,row.source].filter(Boolean).join(' · ');
  const detail=row.path?`<div class=capPath title="${esc(row.path)}">${esc(row.path)}</div>`:(row.plugin_id?`<div class=capPath>${esc(row.plugin_id)}</div>`:'');
  const identifier=row.type==='skill'?`<div class=capPath title="${esc(row.id)}">${esc(row.id)}</div>`:'';
  const configuration=capabilityConfigurationText(row), evidence=capabilityEvidenceText(row);
  const typeLabel=row.control_type==='skill_pack'?'Skill pack':({tool:'Tool',mcp:'MCP',skill:'Skill'}[row.type]||row.type);
  const use=capabilityUseCount(row);
  const evidenceDetail=row.unmeasurable
   ? (row.measurement==='instruction'?'Use cannot be inferred from instruction text.':'No reliable activation evidence is available.')
   : `${f(use)} ${countWord(use,row.type==='skill'?'activation':'call')}${row.nested_calls?` (${f(row.nested_calls)} ${countWord(row.nested_calls,'code-mode call site')})`:''}${row.type==='skill'?'':` · ${f(row.returned_tokens||0)} returned tokens`}`;
  return `<tr><td class=nm data-label="Capability">${esc(row.name)}${row.identity&&row.identity!==row.name?`<div class=capPath>${esc(row.identity)}</div>`:''}${identifier}</td><td data-label="Type"><span class="chip ${row.type==='mcp'?'mcp':''}">${typeLabel}</span></td><td data-label="Runtime / source">${esc(source)}${detail}</td><td data-label="Configuration"><span class="stateDot ${capabilityConfigurationClass(row)}">${esc(configuration)}</span>${row.type==='mcp'?`<div class=capPath>${row.configured===false?(row.calls?'Observed in traces; not in Codex or Claude config':'Advertised in traces; not in Codex or Claude config'):`Codex ${row.codex_enabled?'on':'off'} · Claude ${row.claude_enabled?'on':'off'}`}</div>`:''}</td><td data-label="Evidence"><div class=capEvidence><strong><span class="stateDot ${capabilityEvidenceClass(row)}">${esc(evidence)}</span></strong><small>${esc(evidenceDetail)}</small></div></td><td data-label="Last observed">${esc(row.last_used||'Never')}</td><td data-label="Control">${capabilityAction(row)}</td></tr>`;
 }).join('')+(visibleRows.length<rows.length?`<tr><td colspan=7 class=capMore><button class=tbtn type=button data-cap-more>Show next ${f(Math.min(CAPABILITY_PAGE_SIZE,rows.length-visibleRows.length))}</button></td></tr>`:''):`<tr><td colspan=7 class=capEmpty>No capabilities match these filters.<button class=tbtn type=button data-cap-clear>Clear filters</button></td></tr>`;
 lastCapabilityInventoryRevision=String(cap.inventory_revision||cap.revision||'');
 paintCapabilitySort();
 paintCapabilityFilters();
 [...$('c-table').querySelectorAll('[data-cap-id]')].forEach(button=>button.onclick=()=>{
  const item=all.find(row=>row.id===button.dataset.capId);
  const group=(cap?.control_groups||[]).find(row=>row.id===(item?.control_id||item?.id));
  requestCapabilityToggle(group?{...item,...group,id:item.id,control_id:group.id}:item,cap);
 });
 $('c-table').querySelector('[data-cap-clear]')?.addEventListener('click',clearCapabilityFilters);
 $('c-table').querySelector('[data-cap-more]')?.addEventListener('click',()=>{capabilityVisibleLimit+=CAPABILITY_PAGE_SIZE;renderCapabilityTable(cap,{force:true});});
}
$('c-table').addEventListener('pointerleave',()=>{if(pendingCapabilityTable)renderCapabilityTable(pendingCapabilityTable,{force:true});});
$('c-table').addEventListener('focusout',()=>requestAnimationFrame(()=>{if(pendingCapabilityTable&&!$('c-table').contains(document.activeElement))renderCapabilityTable(pendingCapabilityTable,{force:true});}));
$('c-review-list').addEventListener('pointerleave',()=>{if(pendingCapabilityReview)renderCapabilityReviewQueue(pendingCapabilityReview,{force:true});});
$('c-review-list').addEventListener('focusout',()=>requestAnimationFrame(()=>{if(pendingCapabilityReview&&!$('c-review-list').contains(document.activeElement))renderCapabilityReviewQueue(pendingCapabilityReview,{force:true});}));
function openCapabilityInventory(type='all'){
 capabilityType=CAPABILITY_TYPES.has(type)?type:'all';
 capabilityVisibleLimit=CAPABILITY_PAGE_SIZE;
 paintCapabilityFilters();
 const inventory=currentCapabilityInventory();
 if(inventory)renderCapabilityTable(inventory,{force:true});else loadCapabilityInventory({force:true});
 $('c-inventory').scrollIntoView({behavior:'smooth',block:'start'});
 requestAnimationFrame(()=>$('c-inventory-title').focus({preventScroll:true}));
}
async function loadCapabilityInventory({force=false}={}){
 if(capabilityInventoryLoading)return;
 const cap=currentCapabilitySummary();
 if(!cap)return;
 if(!force&&capabilityInventorySnapshot&&capabilityInventorySnapshot.inventory_revision===cap.inventory_revision){
  renderCapabilityTable(currentCapabilityInventory());
  return;
 }
 capabilityInventoryLoading=true;
 $('c-filter-count').textContent='Loading inventory…';
 $('c-table').querySelector('tbody').innerHTML='<tr><td colspan=7 class=capEmpty>Loading local capability inventory…</td></tr>';
 try{
  const response=await fetch('/capabilities/inventory',{cache:'no-store'});
  const result=await response.json();
  if(!response.ok||!result.ok||!Array.isArray(result.items))throw new Error('Capability inventory could not be loaded.');
  capabilityInventorySnapshot=result;
  const merged=currentCapabilityInventory();
  if(merged)renderCapabilityTable(merged,{force});
 }catch(error){
  $('c-filter-count').textContent='Inventory unavailable';
  $('c-table').querySelector('tbody').innerHTML='<tr><td colspan=7 class=capEmpty>Inventory could not be loaded.<button class=tbtn type=button data-cap-retry>Retry</button></td></tr>';
  $('c-table').querySelector('[data-cap-retry]')?.addEventListener('click',()=>loadCapabilityInventory({force:true}));
 }finally{capabilityInventoryLoading=false;}
}
let pendingCapabilityAction=null;
function requestCapabilityToggle(row,cap){
 if(!row||!row.mutable||capabilityEvidenceState!=='current')return;
 const enabled=!row.enabled, members=row.members||[row.name], shown=members.slice(0,8);
 const controlId=row.control_id||row.id;
 pendingCapabilityAction={row,controlId,fingerprint:capabilityGroupFingerprint({...row,id:controlId})};
 $('cap-dialog-title').textContent=`${enabled?'Enable':'Disable'} skill pack?`;
 $('cap-dialog-sub').textContent=`${row.runtime} · ${row.plugin_id} · ${row.source||row.origin||'configured plugin'}`;
 $('cap-dialog-evidence').innerHTML=`<div>This changes the entire pack, not only ${esc(row.name)}.</div><div>${f(members.length)} affected ${countWord(members.length,'skill')}: ${esc(shown.join(', '))}${members.length>shown.length?` and ${f(members.length-shown.length)} more`:''}</div>`;
 $('cap-dialog-control').textContent=controlId;
 $('cap-dialog-setting').textContent=`The persisted ${row.setting_path||'runtime setting'} will be read back and verified before success is reported. Restart the IDE or agent afterward.`;
 const button=$('cap-confirm');
 button.disabled=false; button.textContent=`${enabled?'Enable':'Disable'} pack`;
 button.classList.toggle('danger',!enabled);
 $('cap-dialog').showModal();
}
$('cap-cancel').onclick=()=>{$('cap-dialog').close();pendingCapabilityAction=null;};
$('cap-dialog').addEventListener('cancel',()=>{pendingCapabilityAction=null;});
$('cap-confirm').onclick=()=>{
 if(!pendingCapabilityAction)return;
 const {row,controlId,fingerprint}=pendingCapabilityAction, cap=currentCapabilitySummary();
 const group=(cap?.control_groups||[]).find(item=>item.id===controlId);
 if(capabilityEvidenceState!=='current'||!group||capabilityGroupFingerprint(group)!==fingerprint){
  $('cap-dialog').close();pendingCapabilityAction=null;
  showAppToast({title:'Capability state changed',body:'The exact control or its evidence changed while confirmation was open. Reopen it from the current inventory.',kind:'warn',sticky:true});
  return;
 }
 toggleCapability({...row,...group,id:row.id,control_id:controlId},cap,$('cap-confirm'));
};
function applyCapabilitySnapshot(freshCap){
 if(!freshCap)return;
 if(LATEST?.xsession)LATEST.xsession.capabilities=freshCap;
 if(CURRENT?.xsession)CURRENT.xsession.capabilities=freshCap;
 renderCapabilities(freshCap,{force:true});
}
async function toggleCapability(row,cap,confirmButton=null){
 if(!row||!row.mutable||capabilityEvidenceState!=='current')return;
 const enabled=!row.enabled;
 const controlId=row.control_id||row.id;
 const payload={type:row.type,enabled,control_id:controlId};
 const buttons=[...document.querySelectorAll(`[data-cap-id="${CSS.escape(row.id)}"]`)];
 buttons.forEach(button=>{button.disabled=true;button.textContent=enabled?'Enabling…':'Disabling…';});
 if(confirmButton){confirmButton.disabled=true;confirmButton.textContent=enabled?'Enabling…':'Disabling…';}
 try{
  const response=await fetch('/capability/toggle',{method:'POST',headers:{'Content-Type':'application/json','X-Token-Meter-Action':cap.actions.token},body:JSON.stringify(payload)});
  const result=await response.json();
  if(!response.ok||!result.ok)throw new Error(result.error||'Capability change failed.');
  (cap.items||[]).forEach(item=>{
   if(item.id===row.id||item.control_id===controlId){
    item.enabled=enabled; item.state=enabled?'Enabled':'Disabled';
   }
  });
  (cap.control_groups||[]).forEach(group=>{if(group.id===controlId)group.enabled=enabled;});
  let freshCap=result.capabilities||cap;
  if(!result.capabilities)try{
   const fresh=await fetch('/state',{cache:'no-store'}).then(response=>response.json());
   if(fresh?.xsession?.capabilities){adoptFreshDiscounts(fresh);LATEST=fresh;if(!pinned)CURRENT=fresh;freshCap=fresh.xsession.capabilities;}
  }catch(_error){}
  if(result.capabilities)applyCapabilitySnapshot(freshCap);
  if(confirmButton){$('cap-dialog').close();pendingCapabilityAction=null;}
  if(!result.capabilities)renderCapabilities(freshCap,{force:true});
  const verified=result.verified?' Setting verified.':'';
  showAppToast({title:`Skill pack ${enabled?'enabled':'disabled'}`,body:`${row.plugin_id} changed for future sessions.${verified} Restart the IDE or agent to load the change.`,kind:enabled?'neutral':'warn',sticky:true});
 }catch(error){
  buttons.forEach(button=>{button.disabled=false;});
  if(confirmButton){confirmButton.disabled=false;confirmButton.textContent=`${enabled?'Enable':'Disable'} pack`;}
  showAppToast({title:'Capability change failed',body:error.message||String(error),kind:'warn',sticky:true});
  const inventory=currentCapabilityInventory();
  if(inventory)renderCapabilityTable(inventory,{force:true});
 }
}
let pendingBulkDisable=null;
function unusedReviewGroups(cap){
 const ids=new Set(cap?.summary?.optional?.review_candidates||[]);
 return (cap?.control_groups||[]).filter(row=>ids.has(row.id)&&row.reviewable!==false&&row.mutable&&row.enabled&&!row.used&&!row.unmeasurable&&Number(row.scanned_sessions||0)>0);
}
function openSelectedDisableDialog(cap){
 if(capabilityEvidenceState!=='current')return;
 const groups=unusedReviewGroups(cap).filter(row=>selectedCapabilityIds.has(row.id));
 if(!groups.length)return;
 const controlIds=groups.map(row=>row.id);
 pendingBulkDisable={controlIds,fingerprints:Object.fromEntries(groups.map(row=>[row.id,capabilityGroupFingerprint(row)]))};
 const affected=groups.reduce((total,row)=>total+(row.members||[]).length,0);
 const measured=groups.reduce((total,row)=>total+Number(row.unused_eager_definition_tokens||0),0);
 const coverage=[...new Map(groups.map(row=>[row.runtime||'Runtime',Number(row.scanned_sessions||0)])).entries()].map(([runtime,count])=>`${f(count)} ${runtime} ${countWord(count,'log')}`).join(' · ');
 $('bulk-dialog-sub').textContent=`${f(groups.length)} selected ${countWord(groups.length,'skill pack')} will be disabled`;
 $('bulk-dialog-evidence').innerHTML=`<div>${f(affected)} affected ${countWord(affected,'skill')} · evidence from ${esc(coverage)}</div><div>Estimated avoidable prompt load across scanned logs: ${measured?`${f(measured)} definition tokens`:'not measured'}. This is not a per-prompt saving. Restart each affected runtime after the change; packs can be re-enabled from All capabilities.</div><div>MCP servers, runtime packs, built-ins, standalone skills, and used groups are excluded.</div>`;
 $('bulk-dialog-controls').textContent=controlIds.join('\n');
 $('bulk-confirm').disabled=false;
 $('bulk-confirm').textContent='Disable selected';
 $('bulk-dialog').showModal();
}
$('bulk-cancel').onclick=()=>{$('bulk-dialog').close();pendingBulkDisable=null;};
$('bulk-dialog').addEventListener('cancel',()=>{pendingBulkDisable=null;});
$('bulk-confirm').onclick=async()=>{
 if(!pendingBulkDisable)return;
 const {controlIds,fingerprints}=pendingBulkDisable, button=$('bulk-confirm');
 const cap=currentCapabilitySummary(), current=new Map(unusedReviewGroups(cap).map(row=>[row.id,row]));
 const groups=controlIds.map(id=>current.get(id));
 const changed=capabilityEvidenceState!=='current'||groups.some((row,index)=>!row||capabilityGroupFingerprint(row)!==fingerprints[controlIds[index]]);
 if(changed){
  $('bulk-dialog').close();pendingBulkDisable=null;
  renderCapabilityReviewQueue(cap,{force:true});
  showAppToast({title:'Review evidence changed',body:'The selected controls or their evidence changed while confirmation was open. Review the current receipts before trying again.',kind:'warn',sticky:true});
  return;
 }
 button.disabled=true;button.textContent='Disabling…';
 try{
  const response=await fetch('/capability/disable-unused',{method:'POST',headers:{'Content-Type':'application/json','X-Token-Meter-Action':cap.actions.token},body:JSON.stringify({control_ids:controlIds})});
  const result=await response.json();
  if(result.capabilities)applyCapabilitySnapshot(result.capabilities);
  if(!response.ok||!result.ok){
   if(result.partial){
    $('bulk-dialog').close();pendingBulkDisable=null;
    selectedCapabilityIds.clear();
    (result.failures||[]).forEach(row=>{if(row.control_id)selectedCapabilityIds.add(row.control_id);});
    renderCapabilityReviewQueue(currentCapabilitySummary(),{force:true});
    const names=(result.failures||[]).map(row=>row.name).slice(0,4).join(', ');
    showAppToast({title:'Some groups could not be disabled',body:`${f(result.changed)} changed; ${f((result.failures||[]).length)} failed${names?`: ${names}`:''}.`,kind:'warn',sticky:true});
    return;
   }
   throw new Error(result.error||'Unused capabilities could not be disabled.');
  }
  $('bulk-dialog').close();pendingBulkDisable=null;
  selectedCapabilityIds.clear();
  showAppToast({title:'Selected packs disabled',body:`${f(result.changed)} packs changed and verified. Restart the IDE or agent to load the changes.`,kind:'warn',sticky:true});
 }catch(error){
  button.disabled=false;button.textContent='Disable selected';
  showAppToast({title:'Selected capability change failed',body:error.message||String(error),kind:'warn',sticky:true});
 }
};
function setCapabilityAvailability(state){
 capabilityEvidenceState=state;
 if(state!=='current'){
  [...document.querySelectorAll('#c-table [data-cap-id]')].forEach(button=>{button.disabled=true;button.dataset.evidenceLocked='1';});
  $('c-disable-selected').disabled=true;
  let closedConfirmation=false;
  if($('bulk-dialog').open&&!/ing…$/.test($('bulk-confirm').textContent)){$('bulk-dialog').close();pendingBulkDisable=null;closedConfirmation=true;}
  else if($('bulk-dialog').open)$('bulk-confirm').disabled=true;
  if($('cap-dialog').open&&!/ing…$/.test($('cap-confirm').textContent)){$('cap-dialog').close();pendingCapabilityAction=null;closedConfirmation=true;}
  else if($('cap-dialog').open)$('cap-confirm').disabled=true;
  if(closedConfirmation)showAppToast({title:'Confirmation closed',body:'Capability evidence became stale. Wait for a current snapshot, then review the exact control again.',kind:'warn',sticky:true});
 }else{
  [...document.querySelectorAll('#c-table [data-evidence-locked]')].forEach(button=>{delete button.dataset.evidenceLocked;if(!/ing…$/.test(button.textContent))button.disabled=false;});
  updateCapabilitySelection(currentCapabilitySummary());
 }
}
function renderCapabilities(cap,{force=false}={}){
 if(!cap){setCapabilityAvailability('unavailable');return;}
 capabilityLastGoodAt=Date.now();
 setCapabilityAvailability('current');
 const sum=cap.summary||{}, defs=sum.definitions||{}, optional=sum.optional||{};
 const tools=sum.tools||{}, mcps=sum.mcps||{}, skills=sum.skills||{};
 $('c-stat-tools').textContent=f(tools.available||0);
 $('c-stat-tools-detail').textContent=`${f(tools.observed||0)} observed`;
 $('c-stat-mcps').textContent=f(mcps.available||0);
 $('c-stat-mcps-detail').textContent=`${f(mcps.enabled||0)} configured on · ${f(mcps.historically_used||0)} observed`;
 $('c-stat-skills').textContent=f(skills.available||0);
 $('c-stat-skills-detail').textContent=`${f(skills.enabled||0)} enabled · ${f(skills.historically_used||skills.used||0)} activated`;
 const inventoryCount=Number(cap.inventory_count??(cap.items||[]).length);
 $('c-inventory-count').textContent=`${f(inventoryCount)} ${countWord(inventoryCount,'capability','capabilities')}`;
 const eager=defs.eager_definition_tokens||0, unused=defs.unused_eager_definition_tokens||0, deferred=defs.deferred_definition_tokens||0, total=defs.definition_tokens||0, useful=Math.max(0,eager-unused);
 const denom=Math.max(1,total);
 $('c-def-chart').innerHTML=`<i class=used style="width:${100*useful/denom}%"></i><i class=unused style="width:${100*unused/denom}%"></i><i class=deferred style="width:${100*deferred/denom}%"></i>`;
 $('c-def-legend').innerHTML=`<span><i class=dot style="background:var(--good)"></i>useful eager ${f(useful)}</span><span><i class=dot style="background:var(--warn)"></i>unused eager ${f(unused)}</span><span><i class=dot style="background:rgb(var(--signal-hi-rgb)/.42)"></i>deferred ${f(deferred)}</span>`;
 $('c-def-note').textContent=total?`${f(total)} schema tokens · evidence only`:'No prompt-load token evidence';
 renderCapabilityReviewQueue(cap,{force});
 const revision=String(cap.inventory_revision||'');
 if(capabilityInventorySnapshot&&capabilityInventorySnapshot.inventory_revision===revision){
  if(force||lastCapabilityInventoryRevision!==revision)renderCapabilityTable(currentCapabilityInventory(),{force});
 }else loadCapabilityInventory({force});
}
$('c-search').oninput=e=>{
 capabilitySearch=e.target.value;capabilityVisibleLimit=CAPABILITY_PAGE_SIZE;
 clearTimeout(capabilitySearchTimer);
 capabilitySearchTimer=setTimeout(()=>{const inventory=currentCapabilityInventory();if(inventory)renderCapabilityTable(inventory,{force:true});},120);
};
$('c-type-filter').onclick=e=>{if(!e.target.dataset.ctype)return;capabilityType=e.target.dataset.ctype;capabilityVisibleLimit=CAPABILITY_PAGE_SIZE;paintCapabilityFilters();const inventory=currentCapabilityInventory();if(inventory)renderCapabilityTable(inventory,{force:true});};
$('c-state-filter').onclick=e=>{if(!e.target.dataset.cstate)return;capabilityState=e.target.dataset.cstate;capabilityVisibleLimit=CAPABILITY_PAGE_SIZE;paintCapabilityFilters();const inventory=currentCapabilityInventory();if(inventory)renderCapabilityTable(inventory,{force:true});};
$('c-runtime-filter').onchange=e=>{capabilityRuntime=e.target.value;capabilityVisibleLimit=CAPABILITY_PAGE_SIZE;const inventory=currentCapabilityInventory();if(inventory)renderCapabilityTable(inventory,{force:true});};
$('c-clear-filters').onclick=clearCapabilityFilters;
[...document.querySelectorAll('[data-cap-jump]')].forEach(button=>button.onclick=()=>openCapabilityInventory(button.dataset.capJump));
$('c-clear-selection').onclick=clearCapabilitySelection;
$('c-disable-selected').onclick=()=>openSelectedDisableDialog(currentCapabilitySummary());
$('c-table').querySelector('thead').onclick=e=>{const th=e.target.closest('th[data-csort]');if(th)setCapabilitySort(th.dataset.csort);};
$('c-table').querySelector('thead').onkeydown=e=>{if(!['Enter',' '].includes(e.key))return;const th=e.target.closest('th[data-csort]');if(!th)return;e.preventDefault();setCapabilitySort(th.dataset.csort);};
$('c-mobile-sort').onchange=e=>{
 const [key,dir]=e.target.value.split(':');
 if(!CAPABILITY_SORT_KEYS.has(key)||!['asc','desc'].includes(dir))return;
 capabilitySort=key;capabilitySortDir=dir;capabilityVisibleLimit=CAPABILITY_PAGE_SIZE;
 localStorage.setItem('tm_capability_sort',capabilitySort);localStorage.setItem('tm_capability_sort_dir',capabilitySortDir);
 paintCapabilitySort();const inventory=currentCapabilityInventory();if(inventory)renderCapabilityTable(inventory,{force:true});
};
paintCapabilityFilters();
paintCapabilitySort();

