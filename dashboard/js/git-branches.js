// Git › Branches: three scored delivery metrics, a hard-to-finish count, and the branch table.
// Cost to ship, unshipped spend, rework, and the hard-to-finish spend share each have one healthy
// direction, so their deltas pass improving = down. Pushed-code volume and ratios elsewhere stay neutral.
const GIT_BRANCH_VISIBLE_ROWS=5;
const GIT_BRANCH_HARD_MIN=5;
let gitBranchSort={key:'spend',direction:'desc'},gitBranchQuery='',gitBranchPayload=null,gitBranchUnavailable='';
function pointsTrendDelta(points,label){
 const value=Number(points);
 if(points===null||points===undefined||points===''||!Number.isFinite(value))return {text:'No comparable prior period',direction:''};
 const direction=Math.abs(value)<.05?'flat':value>0?'up':'down',symbol=direction==='up'?'↑':direction==='down'?'↓':'→';
 return {text:`${symbol} ${Math.abs(value).toFixed(1)} pts vs ${label}`,direction};
}
function gitBranchMetricCards(metrics,label){
 const none={text:'No comparable prior period',direction:''},m=metrics||{};
 const cost=m.cost_to_ship||{},unshipped=m.unshipped_share||{},rework=m.rework_share||{},hard=m.hard_branches||{};
 const lines=cost.median_pushed_lines,branches=Number(cost.branches)||0;
 return [
  {id:'cost',label:'Cost to ship',improving:'down',
   value:cost.available?money(cost.value):'--',
   meta:cost.available?(lines==null?`${f(branches)} ${countWord(branches,'branch','branches')} · pushed lines unavailable`:`median ${f(lines)} pushed lines · ${f(branches)} ${countWord(branches,'branch','branches')}`):'',
   reason:cost.available?'':'No branch had its first push in this period.',
   delta:cost.available?percentTrendDelta(cost.comparison?.change_pct,label):none},
  {id:'unshipped',label:'Unshipped spend',improving:'down',
   value:unshipped.available?pct(unshipped.value):'--',
   meta:unshipped.available?`in progress ${pct(unshipped.in_progress_share)} · ${money(unshipped.in_progress_spend)}`:'',
   reason:unshipped.available?'':'No Git evidence for these projects, or no branch spend in this period.',
   delta:unshipped.available?pointsTrendDelta(unshipped.comparison?.change_pp,label):none},
  {id:'rework',label:'Rework after push',improving:'down',
   value:rework.available?pct(rework.value):'--',
   meta:rework.available?`Estimate${rework.spend!=null?` · ${money(rework.spend)}`:''}`:'',
   reason:rework.available?'':'No spend on pushed branches in this period.',
   delta:rework.available?pointsTrendDelta(rework.comparison?.change_pp,label):none},
  // Scored on its spend share, the figure its delta compares.
  {id:'hard',label:'Hard to finish',improving:'down',
   value:hard.available&&hard.count!=null?f(hard.count):'--',
   meta:hard.available?`${hard.spend_share==null?'--':pct(hard.spend_share)} of spend · ${f(hard.eligible)} ${countWord(hard.eligible,'branch','branches')}`:'',
   reason:hard.available?'':`Needs ${GIT_BRANCH_HARD_MIN} pushed branches with $1 or more spend; ${f(hard.eligible)} so far.`,
   delta:hard.available?pointsTrendDelta(hard.comparison?.change_pp,label):none},
 ];
}
function gitBranchSortValue(row,key){
 if(key==='branch')return String(row?.branch||'');
 if(key==='status')return BRANCH_STATUS_LABELS[row?.status]||'';
 if(key==='lines'){
  if(row?.added==null&&row?.deleted==null)return null;
  return (Number(row.added)||0)+(Number(row.deleted)||0);
 }
 if(key==='spend'&&row?.availability?.cost!==true)return null;
 if(key==='spend_per_commit'&&row?.availability?.spend_per_commit!==true)return null;
 const value=row?.[key];
 if(value===null||value===undefined)return null;
 const number=Number(value);
 return Number.isFinite(number)?number:null;
}
function gitBranchRowsView(rows,{query='',sort={key:'spend',direction:'desc'}}={}){
 const direction=sort?.direction==='asc'?1:-1,key=sort?.key||'spend';
 const byName=(a,b)=>String(a?.branch||'').localeCompare(String(b?.branch||''),undefined,{sensitivity:'base'})||String(a?.project||'').localeCompare(String(b?.project||''));
 return (rows||[]).filter(row=>row&&branchMatchesQuery(row.branch,query)).sort((left,right)=>{
  const a=gitBranchSortValue(left,key),b=gitBranchSortValue(right,key);
  if(a===null&&b===null)return byName(left,right);
  if(a===null)return 1;
  if(b===null)return -1;
  const compared=typeof a==='string'?a.localeCompare(b,undefined,{sensitivity:'base'}):a-b;
  return compared?compared*direction:byName(left,right);
 });
}
function gitBranchDate(stamp){
 const value=Number(stamp)||0;
 return value?new Date(value*1000).toLocaleDateString([],{month:'short',day:'numeric'}):'--';
}
function gitBranchRowHtml(row,typical){
 const available=row?.availability||{},git=available.git===true,perCommit=available.spend_per_commit===true;
 const commitsShown=available.commits===true||(git&&row.status!=='pushed'&&row.commits!=null);
 const hard=row.hard&&perCommit&&typical>0?`<span class="chip tone-warn">${(Number(row.spend_per_commit)/typical).toFixed(1)}× your typical per commit</span>`:'';
 const lines=git&&available.commits===true&&Number(row.pushes)>0&&row.added!=null?`<span title="${esc(`${f(row.added)} added · ${f(row.deleted)} deleted`)}">+${f(row.added)} −${f(row.deleted)}</span>`:'--';
 return `<tr><td data-label="Branch"><div class=deliveryBranchName><b title="${esc(row.branch)}">${esc(branchReadableName(row.branch))}</b>${branchChipHtml(row.branch,{project:row.project})}${hard}</div></td><td data-label="Status"><span class=chip>${esc(BRANCH_STATUS_LABELS[row.status]||'--')}</span></td><td class="num mono" data-label="Spend">${available.cost===true?money(row.spend):'--'}</td><td class="num mono" data-label="Sessions">${f(row.sessions)}</td><td class="num mono" data-label="Pushes">${git&&row.pushes!=null?f(row.pushes):'--'}</td><td class="num mono" data-label="Commits">${commitsShown?f(row.commits):'--'}</td><td class="num mono" data-label="Pushed lines">${lines}</td><td class="num mono" data-label="Spend / commit">${perCommit?money(row.spend_per_commit):'--'}</td><td class="num mono" data-label="Last push">${esc(gitBranchDate(row.last_push))}</td></tr>`;
}
// Five rows show, then the table scrolls inside its own frame under a sticky header.
function fitGitBranchRows(){
 const wrap=$('d-branch-wrap'),head=wrap?.querySelector('thead');
 if(!wrap||!head)return;
 const rows=[...wrap.querySelectorAll('tbody tr')];
 if(rows.length<=GIT_BRANCH_VISIBLE_ROWS){wrap.style.removeProperty('max-height');return;}
 const height=head.getBoundingClientRect().height+rows.slice(0,GIT_BRANCH_VISIBLE_ROWS).reduce((sum,row)=>sum+row.getBoundingClientRect().height,0);
 if(height>0)wrap.style.maxHeight=`${Math.ceil(height)}px`;
}
function renderGitBranchTable(){
 const payload=gitBranchPayload,rows=Array.isArray(payload?.rows)?payload.rows:[],body=$('d-branch-table').querySelector('tbody');
 const visible=gitBranchRowsView(rows,{query:gitBranchQuery,sort:gitBranchSort});
 const typical=Number(payload?.metrics?.hard_branches?.median_spend_per_commit)||0;
 const empty=gitBranchUnavailable||(!rows.length?'No branches recorded in this period. Claude Code and Codex record the branch each session ran on.':'No branches match this search.');
 body.innerHTML=visible.length?visible.map(row=>gitBranchRowHtml(row,typical)).join(''):`<tr><td colspan=9><div class="deliveryEmpty emptyState">${esc(empty)}</div></td></tr>`;
 const total=Number(payload?.total_rows)||rows.length;
 $('d-branch-count').textContent=payload?`${f(visible.length)} of ${f(total)} ${countWord(total,'branch','branches')}${payload.truncated?` · first ${f(rows.length)} shown`:''}`:'';
 $('d-branch-table').querySelectorAll('th').forEach(header=>{
  const button=header.querySelector('[data-delivery-branch-sort]'),active=button?.dataset.deliveryBranchSort===gitBranchSort.key;
  header.setAttribute('aria-sort',active?(gitBranchSort.direction==='asc'?'ascending':'descending'):'none');
  const mark=button?.querySelector('.deliverySortMark');if(mark)mark.textContent=active?(gitBranchSort.direction==='asc'?'↑':'↓'):'';
 });
 fitGitBranchRows();
}
function renderGitBranches(branches,unavailable=''){
 gitBranchPayload=branches&&typeof branches==='object'?branches:null;
 gitBranchUnavailable=unavailable||(gitBranchPayload?'':'Branch evidence unavailable.');
 const label=deliveryRange==='month'?'same days last month':efficiencyComparisonWindows(deliveryRange)?.label||'prior period';
 gitBranchMetricCards(gitBranchPayload?.metrics,label).forEach(card=>{
  const value=$(`d-branch-${card.id}`);
  value.textContent=card.value;
  value.classList.toggle('fieldtip',Boolean(card.reason));
  if(card.reason){value.dataset.tip=card.reason;value.setAttribute('aria-description',card.reason);value.tabIndex=0;}
  else{delete value.dataset.tip;value.removeAttribute('aria-description');value.tabIndex=-1;}
  $(`d-branch-${card.id}-note`).textContent=card.reason||card.meta;
  renderTrendDelta(`d-branch-${card.id}-change`,card.delta,card.improving,card.value==='--');
 });
 const trunk=gitBranchPayload?.trunk?.share,none=gitBranchPayload?.no_branch?.share;
 $('d-branch-caption').textContent=trunk==null&&none==null
  ?'Not counted: spend on main and sessions with no branch recorded.'
  :`Not counted: ${trunk==null?'--':pct(trunk)} of this period\u2019s spend on main and ${none==null?'--':pct(none)} with no branch recorded.`;
 renderGitBranchTable();
}
$('d-branch-search').addEventListener('input',event=>{gitBranchQuery=event.target.value;$('d-branch-wrap').scrollTop=0;renderGitBranchTable();});
$('d-branch-table').querySelector('thead').addEventListener('click',event=>{
 const button=event.target.closest('[data-delivery-branch-sort]');
 if(!button)return;
 const key=button.dataset.deliveryBranchSort;
 gitBranchSort=gitBranchSort.key===key?{key,direction:gitBranchSort.direction==='asc'?'desc':'asc'}:{key,direction:key==='branch'||key==='status'?'asc':'desc'};
 $('d-branch-wrap').scrollTop=0;
 renderGitBranchTable();
});
window.addEventListener('resize',()=>{if($('view-git').classList.contains('on'))fitGitBranchRows();});
