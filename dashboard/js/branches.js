// Sessions › By branch, the shared branch chip, and the session-to-branch lookup.
// Branch names come only from the /logs inventory; the polled /state payload never carries them.
const BRANCH_TRUNK_NAMES=new Set(['main','master','develop','dev','trunk']);
const BRANCH_PREFIX_PATTERN=/^(?:feat|feature|fix|bugfix|hotfix|codex|claude|cursor|chore|docs|refactor|perf|test|tests|build|ci|style|plan|release)\//i;
const BRANCH_ISSUE_PATTERN=/(?:^|[^A-Za-z0-9])([A-Z][A-Z0-9]{1,9}-\d{2,})(?![0-9])/;
const BRANCH_CHIP_ICON='<svg class=branchChipIcon viewBox="0 0 16 16" aria-hidden=true><circle cx="4.5" cy="3.5" r="1.5"/><circle cx="4.5" cy="12.5" r="1.5"/><circle cx="11.5" cy="5" r="1.5"/><path d="M4.5 5v6M11.5 6.5c0 3-7 2.5-7 4.5"/></svg>';
const BRANCH_STATUS_FILTERS=['all','pushed','not_pushed','in_progress','no_git_evidence'];
const BRANCH_STATUS_LABELS={pushed:'Pushed',not_pushed:'Not pushed',in_progress:'In progress',no_git_evidence:'No Git evidence',trunk:'Main',unknown:'--'};
const BRANCH_SORTS=['spend','sessions','last','branch'];
// All history asks the Git service for its all-time range, so older branches still get push status.
const BRANCH_GIT_RANGES={today:'today',yesterday:'yesterday','7d':'7','30d':'30','90d':'90',month:'month',last_month:'last_month',all:'all'};
const BRANCH_SESSION_LIMIT=40;
function isTrunkBranch(name){return BRANCH_TRUNK_NAMES.has(String(name||'').trim());}
function branchIssueKey(name){const match=String(name||'').match(BRANCH_ISSUE_PATTERN);return match?match[1]:'';}
function isAutoWorktreeBranch(name){return /^claude\/[a-z]+-[a-z]+-[0-9a-f]{6}$/.test(String(name||''));}
function branchReadableName(name){
 const raw=String(name||'').trim();
 if(!raw)return 'No branch recorded';
 if(isAutoWorktreeBranch(raw))return 'Unnamed worktree';
 let text=raw;
 while(BRANCH_PREFIX_PATTERN.test(text))text=text.replace(BRANCH_PREFIX_PATTERN,'');
 const key=branchIssueKey(text);
 if(key)text=text.replace(key,' ');
 text=text.replace(/-(?=[0-9a-f]*\d)[0-9a-f]{6}$/,'').replace(/[-_/]+/g,' ').replace(/\s+/g,' ').trim();
 if(!text)return key||raw;
 return text[0].toUpperCase()+text.slice(1);
}
function branchMatchesQuery(name,query){
 const needle=String(query||'').trim().toLowerCase();
 if(!needle)return true;
 const raw=String(name||'');
 return [raw,branchReadableName(raw),branchIssueKey(raw)].join(' ').toLowerCase().includes(needle);
}
// One chip for every surface: a branch icon, the raw name in mono, and an issue key when present.
function branchChipHtml(branch,{project='',interactive=true,issue=true}={}){
 const name=String(branch||'').trim();
 if(!name)return '';
 const key=issue?branchIssueKey(name):'';
 const body=`${BRANCH_CHIP_ICON}<span class=branchChipName>${esc(name)}</span>${key?`<span class=branchChipKey>${esc(key)}</span>`:''}`;
 if(!interactive)return `<span class=branchChip title="${esc(name)}">${body}</span>`;
 return `<button class=branchChip type=button draggable=false data-branch-chip="${esc(name)}" data-branch-project="${esc(project)}" title="${esc(`Show ${name} in Sessions › By branch`)}" aria-label="${esc(`Branch ${name}. Show in Sessions, By branch`)}">${body}</button>`;
}
function sessionBranchIndex(){
 if(branchIndexSource!==allSessionInventory){
  branchIndexSource=allSessionInventory;
  branchIndex=new Map();
  (allSessionInventory||[]).forEach(row=>{
   const name=String(row?.branch||'');
   [row?.key,row?.session,row?.id].forEach(key=>{
    key=String(key||'');
    if(key&&(!branchIndex.has(key)||(!branchIndex.get(key)&&name)))branchIndex.set(key,name);
   });
  });
 }
 return branchIndex;
}
function sessionBranchFor(row){
 if(row?.branch)return String(row.branch);
 const index=sessionBranchIndex();
 for(const key of [row?.key,row?.source?.key,row?.session,row?.id]){
  const value=String(key||'');
  if(value&&index.has(value))return index.get(value);
 }
 return '';
}
function sessionInventoryKnows(row){
 const index=sessionBranchIndex();
 return [row?.key,row?.source?.key,row?.session,row?.id].some(key=>key&&index.has(String(key)));
}
// Load the /logs inventory once for the branch on cards and the session header, and again at most
// once a minute when a visible session is not in it yet.
function ensureBranchInventory(rows=[]){
 if(allSessionsRequest)return;
 const missing=!allSessionInventory||(rows||[]).some(row=>row&&!sessionInventoryKnows(row));
 if(!missing)return;
 const now=Date.now();
 if(now-branchInventoryRequestedAt<(allSessionInventory?60000:15000))return;
 branchInventoryRequestedAt=now;
 loadAllSessions();
}
function branchProjectName(value){
 const text=String(value||'').replace(/\\/g,'/').split(' · ')[0].replace(/\/+$/,'');
 return (text.split('/').pop()||'').toLowerCase();
}
// Rows from /logs carry the repository label the Git page uses (`repo`), so the match is exact.
// Without it, fall back to the same project name, else a branch recorded in exactly one project.
function branchGitMatch(gitRows,branch,project,repo=''){
 if(!Array.isArray(gitRows))return null;
 const candidates=gitRows.filter(row=>row&&row.branch===branch);
 if(repo){const exact=candidates.filter(row=>row.project===repo);if(exact.length===1)return exact[0];}
 const name=branchProjectName(repo||project),same=candidates.filter(row=>branchProjectName(row.project)===name);
 if(same.length===1)return same[0];
 if(!same.length&&candidates.length===1)return candidates[0];
 return null;
}
// One definition on both pages: the range selects which branches appear (any session active in
// it), and each row then covers the whole branch, so Spend is its total across all its sessions
// and the expanded session list sums to it. Main and No branch recorded count sessions active in
// the range. Share is a row's part of the feature-branch total.
function branchSpendView(rows,{query='',app='',project='',rangeStart=0,rangeEnd=0,status='all',showMain=false,sort='spend',gitRows=null,gitWindowStart=0}={}){
 const all=rows||[],roots=new Map(all.filter(row=>row&&!row.is_child_session).map(row=>[String(row.id||''),row]));
 const localDay=stamp=>{const date=new Date(stamp*1000);return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;};
 const span=row=>{const last=Number(row.last_activity_ts||row.mtime)||0;return [Math.max(0,last-(Number(row.wall_duration_s)||0)),last];};
 const active=row=>{const [start,last]=span(row);return (!rangeStart||last>=rangeStart)&&(!rangeEnd||start<rangeEnd);};
 const groups=new Map(),totals={feature:0,trunk:0,noBranch:0,featureSessions:0,trunkSessions:0,noBranchSessions:0,noBranchHidden:Boolean(String(query||'').trim()),top:null};
 all.forEach(row=>{
  if(!row)return;
  if(app&&appFilterGroup(row)!==app)return;
  if(project&&projectFilterValue(row.project)!==project)return;
  const child=Boolean(row.is_child_session),costed=row?.availability?.cost!==false,cost=costed?Number(row.cost)||0:0,inRange=active(row);
  // A child run without its own branch ran on its parent's branch and repository.
  const root=child?roots.get(String(row.root_session_id||'')):null;
  const name=String(row.branch||root?.branch||'').trim(),repo=String(row.repo||(row.branch?'':root?.repo)||'');
  if(!name){
   if(!inRange||totals.noBranchHidden)return;
   totals.noBranch+=cost;if(!child)totals.noBranchSessions++;
   return;
  }
  if(!branchMatchesQuery(name,query))return;
  const trunk=isTrunkBranch(name);
  if(trunk){
   if(!inRange)return;
   totals.trunk+=cost;if(!child)totals.trunkSessions++;
   if(!showMain)return;
  }
  // Worktrees of one repository share its label, so one branch is one row.
  const projectKey=repo||projectFilterValue(row.project),key=`${name}:${projectKey}`;
  let group=groups.get(key);
  if(!group){
   group={key,branch:name,project:String(row.project||''),repo,projectKey,trunk,active:false,spend:0,costCovered:0,sessions:0,apps:new Set(),days:new Set(),last:0,sessionRows:[]};
   groups.set(key,group);
  }
  group.active=group.active||inRange;
  if(costed){group.spend+=cost;group.costCovered++;}
  if(!child){group.sessions++;group.sessionRows.push(row);}
  group.apps.add(appFilterGroup(row));
  const [start,last]=span(row);
  [start,last].forEach(stamp=>{if(stamp>0)group.days.add(localDay(stamp));});
  group.last=Math.max(group.last,last);
 });
 const list=[...groups.values()].filter(group=>group.active).map(group=>{
  const git=group.trunk?null:branchGitMatch(gitRows,group.branch,group.project,group.repo);
  const state=group.trunk?'trunk':!Array.isArray(gitRows)?'unknown':git?String(git.status||'no_git_evidence'):(gitWindowStart&&group.last<gitWindowStart?'unknown':'no_git_evidence');
  return {
   key:group.key,branch:group.branch,readable:branchReadableName(group.branch),issue:branchIssueKey(group.branch),
   project:group.project,repo:group.repo,projectKey:group.projectKey,trunk:group.trunk,
   spend:group.costCovered?group.spend:null,share:null,
   sessions:group.sessions,apps:[...group.apps].sort(),daysActive:group.days.size,last:group.last,
   status:state,git:git&&state!=='no_git_evidence'?git:null,
   sessionRows:group.sessionRows.sort((a,b)=>span(b)[1]-span(a)[1]),
  };
 });
 list.filter(row=>!row.trunk).forEach(row=>{totals.feature+=row.spend||0;totals.featureSessions+=row.sessions;});
 list.forEach(row=>{if(!row.trunk&&row.spend!=null&&totals.feature>0)row.share=row.spend/totals.feature;});
 totals.top=list.filter(row=>!row.trunk&&row.spend!=null).reduce((best,row)=>!best||row.spend>best.spend?row:best,null);
 const spendOf=row=>row.spend==null?-1:row.spend;
 const byName=(a,b)=>a.branch.localeCompare(b.branch,undefined,{sensitivity:'base'})||a.projectKey.localeCompare(b.projectKey);
 // The Branch sort follows the name people read; the raw name breaks ties.
 const byReadable=(a,b)=>a.readable.localeCompare(b.readable,undefined,{sensitivity:'base'})||byName(a,b);
 const sorters={
  spend:(a,b)=>spendOf(b)-spendOf(a)||b.last-a.last||byName(a,b),
  sessions:(a,b)=>b.sessions-a.sessions||spendOf(b)-spendOf(a)||byName(a,b),
  last:(a,b)=>b.last-a.last||byName(a,b),
  branch:byReadable,
 };
 const visible=list.filter(row=>status==='all'||row.status===status).sort(sorters[sort]||sorters.spend);
 return {rows:visible,all:list,total:list.filter(row=>!row.trunk).length,totals};
}

let branchSearch=localStorage.getItem('tm_branch_search')||'';
let branchApp=localStorage.getItem('tm_branch_app')||'';
let branchProject=localStorage.getItem('tm_branch_project')||'';
let branchTime=normalizeTimeFilterWindow(localStorage.getItem('tm_branch_time')||DEFAULT_TIME_FILTER_WINDOW);
let branchStatus=BRANCH_STATUS_FILTERS.includes(localStorage.getItem('tm_branch_status'))?localStorage.getItem('tm_branch_status'):'all';
let branchShowMain=localStorage.getItem('tm_branch_main')==='show';
let branchSort=BRANCH_SORTS.includes(localStorage.getItem('tm_branch_sort'))?localStorage.getItem('tm_branch_sort'):'spend';
let branchExpanded='',branchFocusTarget=null,branchFocusWidened=false;
let branchGit={range:'',at:0,rows:null,start:0,loading:''};
function saveBranchFilters(){
 const save=(key,value,fallback)=>{if(value===fallback)localStorage.removeItem(key);else localStorage.setItem(key,value);};
 save('tm_branch_search',branchSearch,'');save('tm_branch_app',branchApp,'');save('tm_branch_project',branchProject,'');
 save('tm_branch_time',branchTime,DEFAULT_TIME_FILTER_WINDOW);save('tm_branch_status',branchStatus,'all');
 save('tm_branch_main',branchShowMain?'show':'hide','hide');save('tm_branch_sort',branchSort,'spend');
}
async function loadBranchGitStatus(){
 const range=BRANCH_GIT_RANGES[branchTime]||'30';
 if(branchGit.loading===range||(branchGit.range===range&&Date.now()-branchGit.at<60000))return;
 branchGit.loading=range;
 let rows=null;
 try{
  const response=await fetch(`/git-delivery?range=${encodeURIComponent(range)}`,{cache:'no-store'});
  const payload=response.ok?await response.json():null;
  rows=payload?.ok&&Array.isArray(payload?.branches?.rows)?payload.branches.rows:null;
 }catch(error){rows=null;}
 const days={today:1,yesterday:2,'7':7,'30':30,'90':90}[range]||0;
 branchGit={range,at:Date.now(),rows,start:days?Date.now()/1000-days*86400:0,loading:''};
 if(sessionScope==='branches')renderBranchSpend();
}
function branchWhen(stamp,withTime=false){
 const value=Number(stamp)||0;
 return value?new Date(value*1000).toLocaleString([],withTime?{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}:{month:'short',day:'numeric'}):'--';
}
function branchGitCell(row){
 if(row.status==='pushed'&&row.git&&Number(row.git.pushes)>0&&row.git.added!=null)return `<span class=mono title="${esc(`${f(row.git.added)} added · ${f(row.git.deleted)} deleted in ${f(row.git.pushes)} ${countWord(row.git.pushes,'push','pushes')}`)}">+${f(row.git.added)} −${f(row.git.deleted)}</span>`;
 if(row.status==='unknown')return '<span class=branchMuted title="Push status is unavailable for this branch.">--</span>';
 return `<span class=chip>${esc(BRANCH_STATUS_LABELS[row.status]||'--')}</span>`;
}
function branchRowHtml(row,index){
 const open=branchExpanded===row.key,panel=`b-sessions-${index}`;
 const apps=row.apps.map(id=>appFilterLabel({provider:id}));
 const dots=`<span class=branchApps role=img aria-label="${esc(apps.join(', '))}" title="${esc(apps.join(', '))}">${row.apps.map(id=>`<i class=branchAppDot style="--branch-app:${providerColor(id)}"></i>`).join('')}</span>`;
 const projectName=branchProjectName(row.repo||row.project)||'local';
 const main=`<tr class="branchRow${open?' open':''}" data-branch-row="${esc(row.key)}"><td><button class=branchToggle type=button aria-expanded=${open}${open?` aria-controls=${panel}`:''} data-branch-toggle="${esc(row.key)}"><span class=branchIdentity><b class=branchName>${esc(row.readable)}</b>${row.issue?`<span class=chip>${esc(row.issue)}</span>`:''}</span>${branchChipHtml(row.branch,{interactive:false,issue:false})}</button></td><td class=branchProject title="${esc(row.repo||row.project)}">${esc(projectName)}</td><td>${dots}</td><td class="num mono">${f(row.sessions)}</td><td class="num mono">${f(row.daysActive)}</td><td class="num mono" title="${esc(branchWhen(row.last,true))}">${esc(branchWhen(row.last))}</td><td class=num>${branchGitCell(row)}</td><td class="num mono">${row.spend==null?'--':money(row.spend)}</td><td class="num mono">${row.share==null?'--':pct(row.share)}</td></tr>`;
 if(!open)return main;
 const shown=row.sessionRows.slice(0,BRANCH_SESSION_LIMIT),more=row.sessionRows.length-shown.length;
 const sessions=shown.map(session=>`<button class=branchSession type=button data-branch-session="${esc(sessionRowKey(session))}" aria-label="${esc(`Open ${session.title||session.session_name||'session'}`)}"><span class=branchSessionTitle>${esc(session.title||session.session_name||'Untitled session')}</span><span>${esc(appFilterLabel(session))}</span><span class=mono>${esc(session.last||session.start||'')}</span><span class="num mono">${metricAvailable(session,'cost')?money(session.cost)+(session.cost_approx?' est':''):'--'}</span></button>`).join('');
 return `${main}<tr class=branchSessionsRow id=${panel}><td colspan=9><div class=branchSessions aria-label="${esc(`Sessions on ${row.branch}`)}">${sessions||'<div class=emptyState>No sessions in this range.</div>'}${more>0?`<div class=branchMuted>${f(more)} more ${countWord(more,'session')} not shown</div>`:''}</div></td></tr>`;
}
// Counts what is listed, including main rows when Show main is on.
function branchCountText(view){
 const listed=view.rows.length,total=view.all.length;
 return `${f(listed)} of ${f(total)} ${countWord(total,'branch','branches')} · ${money(view.totals.feature)} on feature branches`;
}
function branchActiveFilters(appLabel){
 return [
  branchSearch.trim()&&['search',`Search: “${branchSearch.trim()}”`],
  branchApp&&['app',`App: ${appLabel}`],
  branchProject&&['project',`Project: ${projectFilterLabel(branchProject)}`],
  branchTime!==DEFAULT_TIME_FILTER_WINDOW&&['time',TIME_FILTER_LABELS[branchTime]||branchTime],
  branchStatus!=='all'&&['status',`Status: ${BRANCH_STATUS_LABELS[branchStatus]}`],
  branchShowMain&&['main','Main shown'],
 ].filter(Boolean);
}
function renderBranchTotals(view){
 const {totals}=view;
 setLogText($('b-total-feature'),money(totals.feature));
 setLogText($('b-total-feature-note'),`${f(view.total)} ${countWord(view.total,'branch','branches')} · ${f(totals.featureSessions)} ${countWord(totals.featureSessions,'session')}`);
 setLogText($('b-total-main'),money(totals.trunk));
 setLogText($('b-total-main-note'),`${f(totals.trunkSessions)} ${countWord(totals.trunkSessions,'session')} active in range`);
 setLogText($('b-total-none'),totals.noBranchHidden?'--':money(totals.noBranch));
 setLogText($('b-total-none-note'),totals.noBranchHidden?'Search matches branches only':`${f(totals.noBranchSessions)} ${countWord(totals.noBranchSessions,'session')} active in range`);
 setLogText($('b-total-top'),totals.top?totals.top.readable:'--');
 setLogAttribute($('b-total-top'),'title',totals.top?totals.top.branch:'');
 setLogText($('b-total-top-note'),totals.top?`${money(totals.top.spend)} · ${pct(totals.top.share)} of feature-branch spend`:'No feature branch in this range');
}
function renderBranchSpend(){
 if(!branchUiReady)return;
 const inventory=allSessionInventory;
 if(!inventory){
  setLogHtml($('b-list'),'<div class="emptyState branchEmpty">Loading session history&hellip;</div>');
  setLogText($('b-count'),'');
  return;
 }
 const apps=[...new Map(inventory.map(row=>[appFilterGroup(row),appFilterLabel(row)])).entries()].map(([id,label])=>({id,label})).sort((a,b)=>a.label.localeCompare(b.label,undefined,{sensitivity:'base'}));
 if(branchApp&&!apps.some(app=>app.id===branchApp)){branchApp='';saveBranchFilters();}
 setLogSelectOptions($('b-app'),'<option value="">All apps</option>'+apps.map(app=>`<option value="${esc(app.id)}">${esc(app.label)}</option>`).join(''),branchApp);
 const projects=[...new Set(inventory.map(row=>projectFilterValue(row.project)))].sort((a,b)=>projectFilterLabel(a).localeCompare(projectFilterLabel(b),undefined,{sensitivity:'base'}));
 if(branchProject&&!projects.includes(branchProject)){branchProject='';saveBranchFilters();}
 setLogSelectOptions($('b-project'),'<option value="">All projects</option>'+projects.map(project=>`<option value="${esc(project)}">${esc(projectFilterLabel(project))}</option>`).join(''),branchProject);
 if($('b-search').value!==branchSearch)$('b-search').value=branchSearch;
 if($('b-time').value!==branchTime)$('b-time').value=branchTime;
 if($('b-status').value!==branchStatus)$('b-status').value=branchStatus;
 [...$('b-sort').querySelectorAll('[data-bsort]')].forEach(button=>{const on=button.dataset.bsort===branchSort;button.classList.toggle('on',on);button.setAttribute('aria-pressed',String(on));});
 [...$('b-main').querySelectorAll('[data-bmain]')].forEach(button=>{const on=(button.dataset.bmain==='show')===branchShowMain;button.classList.toggle('on',on);button.setAttribute('aria-pressed',String(on));});
 const range=BRANCH_GIT_RANGES[branchTime]||'30',gitReady=branchGit.range===range,{start,end}=timeFilterBounds(branchTime);
 const view=branchSpendView(inventory,{query:branchSearch,app:branchApp,project:branchProject,rangeStart:start,rangeEnd:end,status:branchStatus,showMain:branchShowMain,sort:branchSort,gitRows:gitReady?branchGit.rows:null,gitWindowStart:gitReady?branchGit.start:0});
 if(branchFocusTarget&&!branchFocusTarget.trunk&&!branchFocusWidened&&branchTime!=='all'&&!view.rows.some(row=>row.branch===branchFocusTarget.branch)){
  branchFocusWidened=true;branchTime='all';saveBranchFilters();renderBranchSpend();return;
 }
 const filters=branchActiveFilters(apps.find(app=>app.id===branchApp)?.label||branchApp);
 $('b-clear').disabled=!filters.length;
 $('b-active-filters').hidden=!filters.length;
 setLogHtml($('b-active-filters'),filters.length?`<span>Filtered by</span>${filters.map(([key,label])=>`<button type=button data-clear-branch-filter="${key}" title="Remove ${esc(label)} filter">${esc(label)}<b aria-hidden=true>×</b></button>`).join('')}`:'');
 const gitNote=!gitReady?' · reading Git…':branchGit.rows?'':' · push status unavailable';
 setLogText($('b-count'),`${branchCountText(view)}${gitNote}`);
 renderBranchTotals(view);
 const anyBranch=inventory.some(row=>row?.branch);
 const head='<thead><tr><th scope=col>Branch</th><th scope=col>Project</th><th scope=col>Apps</th><th class=num scope=col>Sessions</th><th class=num scope=col>Days</th><th class=num scope=col>Last activity</th><th class=num scope=col><span class=fieldtip tabindex=0 data-tip="Lines added and deleted in this branch&rsquo;s pushes, from this Mac&rsquo;s Git reflogs. Branches without a push show their status." aria-description="Lines added and deleted in this branch&rsquo;s pushes, from this Mac&rsquo;s Git reflogs. Branches without a push show their status.">Pushed lines</span></th><th class=num scope=col><span class=fieldtip tabindex=0 data-tip="Total spend across all of this branch&rsquo;s sessions, including sessions before this range. Main rows count only sessions active in this range." aria-description="Total spend across all of this branch&rsquo;s sessions, including sessions before this range. Main rows count only sessions active in this range.">Spend</span></th><th class=num scope=col><span class=fieldtip tabindex=0 data-tip="This branch&rsquo;s part of the feature-branch total above." aria-description="This branch&rsquo;s part of the feature-branch total above.">Share</span></th></tr></thead>';
 const body=view.rows.length?view.rows.map(branchRowHtml).join(''):'';
 setLogHtml($('b-list'),!anyBranch
  ?'<div class="emptyState branchEmpty"><strong>No branches recorded yet</strong><p>Claude Code and Codex record the Git branch each session ran on. Apps that do not record one count as No branch recorded.</p></div>'
  :view.rows.length?`<table class="tbl branchTable">${head}<tbody>${body}</tbody></table>`
  :'<div class="emptyState branchEmpty"><strong>No branches match these filters</strong><p>Clear filters or choose a longer time range.</p></div>');
 if(!gitReady)loadBranchGitStatus();
 if(branchFocusTarget)focusBranchTarget(view);
}
function focusBranchTarget(view){
 const target=branchFocusTarget;
 branchFocusTarget=null;branchFocusWidened=false;
 if(target.trunk){
  requestAnimationFrame(()=>{const tile=$('b-total-main-tile');tile.scrollIntoView({block:'center'});tile.focus({preventScroll:true});});
  return;
 }
 const name=branchProjectName(target.project),matches=view.rows.filter(row=>row.branch===target.branch);
 const row=matches.find(item=>branchProjectName(item.project)===name)||matches[0];
 if(!row)return;
 if(branchExpanded!==row.key){branchExpanded=row.key;renderBranchSpend();}
 requestAnimationFrame(()=>{
  const button=[...$('b-list').querySelectorAll('[data-branch-toggle]')].find(item=>item.dataset.branchToggle===row.key);
  if(!button)return;
  button.closest('tr').scrollIntoView({block:'center'});
  button.focus({preventScroll:true});
 });
}
function openBranchSpend(){
 renderBranchSpend();
 if(!allSessionInventory)loadAllSessions();
 loadBranchGitStatus();
}
function openBranchChip(name,project=''){
 name=String(name||'').trim();
 if(!name)return;
 const trunk=isTrunkBranch(name);
 // Clear the filters that could hide the branch; a trunk branch turns on its rows.
 branchSearch='';branchApp='';branchProject='';branchStatus='all';
 if(trunk)branchShowMain=true;
 saveBranchFilters();
 branchFocusTarget={branch:name,project,trunk};branchFocusWidened=false;
 if(currentPanel==='sessions'&&sessionScope==='branches'&&$('view-session').classList.contains('on'))renderBranchSpend();
 else openSessionScope('branches');
}
function handleBranchChipClick(event){
 const chip=event.target instanceof Element?event.target.closest('[data-branch-chip]'):null;
 if(!chip)return;
 event.preventDefault();
 event.stopPropagation();
 openBranchChip(chip.dataset.branchChip,chip.dataset.branchProject||'');
}
document.addEventListener('click',handleBranchChipClick,true);
function renderSessionBranch(s=CURRENT){
 const target=$('preview-branch');
 if(!target)return;
 const lookup=s?{key:s.source?.key,session:s.session,id:stateSessionId(s)}:null;
 const branch=lookup?sessionBranchFor(lookup):'';
 setLogHtml(target,branch?branchChipHtml(branch,{project:s.project||s.source?.project||''}):'');
 if(target.hidden!==!branch)target.hidden=!branch;
 if(lookup&&!branch)ensureBranchInventory([lookup]);
}
// Re-render the surfaces that show a branch once the /logs inventory arrives.
function refreshBranchSurfaces(){
 if(!LATEST||!$('view-session').classList.contains('on'))return;
 if(currentPanel==='sessions'&&sessionScope==='current')renderCurrentSessions(LATEST);
 else if(currentPanel==='sessions'&&sessionScope==='branches')renderBranchSpend();
 else if(currentPanel!=='sessions'&&CURRENT)renderSessionBranch(CURRENT);
}
function placeCurrentSessionBranchChips(root=$('current-session-grid')){
 root.querySelectorAll('.currentSessionSlot').forEach(slot=>{
  const chip=slot.querySelector(':scope>.branchChip'),anchor=slot.querySelector('.currentSessionBranchAnchor');
  if(!chip||!anchor)return;
  const frame=slot.getBoundingClientRect(),box=anchor.getBoundingClientRect();
  chip.style.left=`${Math.round(box.left-frame.left)}px`;chip.style.top=`${Math.round(box.top-frame.top)}px`;
  chip.style.width=`${Math.ceil(box.width)}px`;chip.style.height=`${Math.round(box.height)}px`;
  chip.dataset.placed='';
 });
}
if(window.ResizeObserver)new ResizeObserver(()=>placeCurrentSessionBranchChips()).observe($('current-session-grid'));
$('current-session-grid').addEventListener('animationend',()=>placeCurrentSessionBranchChips());
document.fonts?.ready?.then(()=>placeCurrentSessionBranchChips());

$('b-search').addEventListener('input',event=>{branchSearch=event.target.value;saveBranchFilters();renderBranchSpend();});
$('b-app').addEventListener('change',event=>{branchApp=event.target.value;saveBranchFilters();renderBranchSpend();});
$('b-project').addEventListener('change',event=>{branchProject=event.target.value;saveBranchFilters();renderBranchSpend();});
$('b-time').addEventListener('change',event=>{branchTime=normalizeTimeFilterWindow(event.target.value);saveBranchFilters();renderBranchSpend();});
$('b-status').addEventListener('change',event=>{branchStatus=BRANCH_STATUS_FILTERS.includes(event.target.value)?event.target.value:'all';saveBranchFilters();renderBranchSpend();});
$('b-main').onclick=event=>{const button=event.target.closest('[data-bmain]');if(!button)return;branchShowMain=button.dataset.bmain==='show';saveBranchFilters();renderBranchSpend();};
$('b-sort').onclick=event=>{const button=event.target.closest('[data-bsort]');if(!button)return;branchSort=button.dataset.bsort;saveBranchFilters();renderBranchSpend();};
$('b-clear').onclick=()=>{
 branchSearch='';branchApp='';branchProject='';branchTime=DEFAULT_TIME_FILTER_WINDOW;branchStatus='all';branchShowMain=false;
 saveBranchFilters();renderBranchSpend();
};
$('b-active-filters').onclick=event=>{
 const key=event.target.closest('[data-clear-branch-filter]')?.dataset.clearBranchFilter;
 if(!key)return;
 if(key==='search')branchSearch='';
 if(key==='app')branchApp='';
 if(key==='project')branchProject='';
 if(key==='time')branchTime=DEFAULT_TIME_FILTER_WINDOW;
 if(key==='status')branchStatus='all';
 if(key==='main')branchShowMain=false;
 saveBranchFilters();renderBranchSpend();
};
$('b-list').onclick=event=>{
 const session=event.target.closest('[data-branch-session]');
 if(session){selectSession(session.dataset.branchSession,{returnScope:'branches'});return;}
 const row=event.target.closest('[data-branch-row]');
 if(!row)return;
 const key=row.dataset.branchRow,focused=document.activeElement?.dataset?.branchToggle===key;
 branchExpanded=branchExpanded===key?'':key;
 renderBranchSpend();
 if(focused)[...$('b-list').querySelectorAll('[data-branch-toggle]')].find(item=>item.dataset.branchToggle===key)?.focus();
};
branchUiReady=true;
if(sessionScope==='branches'&&currentPanel==='sessions'&&$('view-session').classList.contains('on'))openBranchSpend();
