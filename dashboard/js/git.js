

function deliveryProjectName(value){return String(value||'').split(' · ')[0]||'Local project';}
function deliveryPercent(value){
 const number=Number(value);
 return Number.isFinite(number)?`${Math.abs(number).toFixed(Math.abs(number)>=10?0:1)}%`:'';
}
function deliveryComparisonText(comparison){
 const code=Number(comparison?.code_pushed_pct),intensity=Number(comparison?.spend_per_1k_pct);
 if(!comparison?.available||!Number.isFinite(code)||!Number.isFinite(intensity))return 'No comparable prior period';
 const codeText=Math.abs(code)<.05?'the same code volume':`${deliveryPercent(code)} ${code>0?'more':'less'} code`;
 const costText=Math.abs(intensity)<.05?'unchanged cost intensity':`${deliveryPercent(intensity)} ${intensity<0?'lower':'higher'} cost intensity`;
 return `<span>${esc(codeText)}</span><span class=deliveryComparisonDivider aria-hidden=true>&middot;</span><span>${esc(costText)}</span>`;
}
function deliveryRatio(value){
 if(value===null||value===undefined||value==='')return '--';
 const number=Number(value);
 if(!Number.isFinite(number))return '--';
 if(number>=1000)return compactNumber(number);
 return number.toFixed(number>=10?1:2).replace(/(\.\d*?)0+$/,'$1').replace(/\.$/,'');
}
function deliveryMultiple(value){
 const number=Number(value);
 return Number.isFinite(number)?`${number.toFixed(number>=10?0:1)}×`:'--';
}
function deliveryQuantile(sorted,share){
 if(!sorted.length)return null;
 if(sorted.length===1)return sorted[0];
 const position=(sorted.length-1)*share,lower=Math.floor(position),upper=Math.ceil(position);
 return lower===upper?sorted[lower]:sorted[lower]+(sorted[upper]-sorted[lower])*(position-lower);
}
function deliveryDistribution(values){
 const sorted=(values||[]).map(Number).filter(value=>Number.isFinite(value)).sort((a,b)=>a-b);
 if(!sorted.length)return null;
 return {count:sorted.length,min:sorted[0],max:sorted[sorted.length-1],p25:deliveryQuantile(sorted,.25),median:deliveryQuantile(sorted,.5),p75:deliveryQuantile(sorted,.75)};
}
function deliveryShapeRange(distribution){
 if(!distribution)return null;
 const stable=distribution.count>=5;
 return {label:stable?'Middle half':'Observed range',low:stable?distribution.p25:distribution.min,high:stable?distribution.p75:distribution.max};
}
const DELIVERY_MIN_RATIO_LINES=50;
const DELIVERY_SHAPE_METRICS=[
 {key:'spend_per_1k',label:'Spend / 1K',format:value=>money(value)},
 {key:'changed_lines',label:'Lines / push day',format:value=>compactNumber(value)},
 {key:'delivery_yield',label:'Push yield',format:value=>deliveryRatio(value)},
];
function deliveryRatioDay(row){
 return (Number(row?.comparable_changed_lines)||0)>=DELIVERY_MIN_RATIO_LINES;
}
function deliveryIntensityDays(payload){
 const days=Array.isArray(payload?.days)?payload.days:[];
 return days.filter(row=>row?.availability?.spend_per_1k===true&&deliveryRatioDay(row)&&Number.isFinite(Number(row.spend_per_1k)));
}
function deliveryDaySeries(payload,key){
 const days=Array.isArray(payload?.days)?payload.days:[];
 if(key==='changed_lines')return days.filter(row=>row?.availability?.code_pushed===true&&(Number(row.changed_lines)||0)>0).map(row=>Number(row.changed_lines));
 if(key==='delivery_yield')return days.filter(row=>row?.efficiency?.availability?.delivery_yield===true&&deliveryRatioDay(row)).map(row=>Number(row.efficiency.delivery_yield));
 return deliveryIntensityDays(payload).map(row=>Number(row.spend_per_1k));
}
function deliveryShapeRows(payload){
 return DELIVERY_SHAPE_METRICS.map(metric=>({...metric,distribution:deliveryDistribution(deliveryDaySeries(payload,metric.key))}));
}
function deliverySignals(payload){
 const overall=payload?.overall||{},comparison=payload?.comparison||{},coverage=payload?.coverage||{};
 const days=Array.isArray(payload?.days)?payload.days:[],available=overall?.availability||{};
 const efficiency=overall?.efficiency||{},efficiencyAvailable=efficiency?.availability||{};
 const rows=[];
 const intensityChange=Number(comparison?.spend_per_1k_pct),codeChange=Number(comparison?.code_pushed_pct);
 if(comparison?.available===true&&Number.isFinite(intensityChange)&&Number.isFinite(codeChange)){
  const flat=Math.abs(intensityChange)<.05;
  rows.push({key:'intensity_trend',kind:'neutral',priority:10,
   title:flat?'Cost intensity held steady':`Cost intensity ${intensityChange<0?'fell':'rose'} ${deliveryPercent(intensityChange)}`,
   text:''});
 }
 const yieldValue=Number(efficiency?.delivery_yield),yieldChange=Number(comparison?.delivery_yield_pct);
 if(efficiencyAvailable?.delivery_yield===true&&Number.isFinite(yieldValue)){
  const known=Number.isFinite(yieldChange);
  rows.push({key:'push_yield',kind:'neutral',priority:20,
   title:`${deliveryRatio(yieldValue)} pushed lines per 1K output tokens`,
   text:known?'':'No prior period to compare.'});
 }
 const intensityDays=deliveryIntensityDays(payload);
 if(intensityDays.length>=4){
  const distribution=deliveryDistribution(intensityDays.map(row=>Number(row.spend_per_1k)));
  const peak=intensityDays.reduce((top,row)=>Number(row.spend_per_1k)>Number(top.spend_per_1k)?row:top);
  const multiple=distribution.median>0?Number(peak.spend_per_1k)/distribution.median:null;
	  if(multiple&&multiple>=1.6)rows.push({key:'peak_day',kind:'warn',priority:30,action:'day',value:peak.day,
	   title:`${deliveryDateLabel(peak.day)}: ${deliveryMultiple(multiple)} the typical Spend / 1K`,
	   text:`${money(peak.spend_per_1k)} vs a ${money(distribution.median)} median over ${distribution.count} ${countWord(distribution.count,'day')}.`});
 }
 const quiet=days.filter(row=>Number(row?.covered_cost)>0&&(Number(row?.changed_lines)||0)===0);
 if(quiet.length&&days.length){
  const quietCost=quiet.reduce((total,row)=>total+(Number(row.covered_cost)||0),0);
  rows.push({key:'quiet_days',kind:quiet.length>=Math.max(2,Math.round(days.length*.4))?'warn':'neutral',priority:35,
   title:`${quiet.length} of ${days.length} ${countWord(days.length,'day')} had spend but no pushes`,
   text:`${money(quietCost)} spent. Work may land in a later push.`});
 }
 const added=Number(overall?.added),deleted=Number(overall?.deleted),changed=Number(overall?.changed_lines);
 if(available?.code_pushed===true&&Number.isFinite(changed)&&changed>0&&Number.isFinite(deleted)&&Number.isFinite(added)){
  rows.push({key:'deleted_share',kind:'neutral',priority:40,
   title:`${deliveryPercent(100*deleted/changed)} of pushed lines were deletions`,
   text:`${f(added)} added, ${f(deleted)} deleted. Normal editing, not defects.`});
 }
 const projectRows=Array.isArray(payload?.project_rows)?payload.project_rows:[];
 const costed=projectRows.filter(row=>row?.availability?.cost===true&&Number(row?.covered_cost)>0);
 if(costed.length>1){
  const total=costed.reduce((sum,row)=>sum+Number(row.covered_cost),0);
  const top=costed.reduce((best,row)=>Number(row.covered_cost)>Number(best.covered_cost)?row:best);
  const share=total>0?Number(top.covered_cost)/total:0;
	  if(share>=.6)rows.push({key:'concentration',kind:'neutral',priority:50,action:'project',value:top.project,
	   title:`${deliveryPercent(100*share)} of covered spend sits in ${deliveryProjectName(top.project)}`,
	   text:`${money(top.covered_cost)} of ${money(total)} across ${costed.length} ${countWord(costed.length,'project')}.`});
 }
 const selectedCount=Number(coverage?.selected_repositories)||0,comparable=Number(coverage?.comparable_repositories)||0;
 if(selectedCount&&comparable<selectedCount){
	  rows.push({key:'coverage',kind:'neutral',priority:60,action:'coverage',value:'',
	   title:`${comparable} of ${selectedCount} ${countWord(selectedCount,'project')} have both spend and Git`,
	   text:'Ratios use only these; the rest may shift them.'});
 }
 const ranked=rows.sort((left,right)=>left.priority-right.priority||left.key.localeCompare(right.key));
 const coverageRow=ranked.find(row=>row.key==='coverage');
 if(!coverageRow)return ranked.slice(0,5);
	 return ranked.filter(row=>row.key!=='coverage').slice(0,4).concat(coverageRow);
}
function deliveryProjectEvidence(row){
 const cost=row?.availability?.cost===true,code=row?.availability?.code_pushed===true;
 if(cost&&code)return {key:'comparable',label:'Comparable',detail:'Spend + Git'};
 if(cost)return {key:'spend_only',label:'Spend only',detail:'Git unavailable'};
 if(code)return {key:'git_only',label:'Git only',detail:'Spend unavailable'};
 return {key:'unavailable',label:'No period evidence',detail:'Spend + Git unavailable'};
}
function deliveryFilteredProjectRows(rows,filter=deliveryEvidenceFilter){
 const normalized=DELIVERY_EVIDENCE_FILTERS.includes(filter)?filter:'all';
 return (rows||[]).filter(row=>normalized==='all'||deliveryProjectEvidence(row).key===normalized);
}
function selectDeliveryProject(project){
 const projects=Array.isArray(deliveryPayload?.projects)?deliveryPayload.projects:[];
 if(!projects.includes(project))return;
	 deliveryProject=project;deliverySelectedDay='';deliveryEvidenceFilter='all';localStorage.setItem('tm_delivery_project',deliveryProject);
 const select=$('d-project');if(select)select.value=deliveryProject;
 loadDelivery();
}
function deliverySortValue(row,key){
 if(key==='project')return deliveryProjectName(row?.project).toLowerCase();
 const value=Number(row?.[key]);
 if(key==='changed_lines'&&row?.availability?.code_pushed!==true)return null;
 if(key==='covered_cost'&&row?.availability?.cost!==true)return null;
 if(key==='spend_per_1k'&&row?.availability?.spend_per_1k!==true)return null;
 return Number.isFinite(value)?value:null;
}
function sortedDeliveryRows(rows){
 const direction=deliverySort.direction==='asc'?1:-1;
 return [...(rows||[])].sort((left,right)=>{
  const a=deliverySortValue(left,deliverySort.key),b=deliverySortValue(right,deliverySort.key);
  if(a===null&&b===null)return deliveryProjectName(left.project).localeCompare(deliveryProjectName(right.project));
  if(a===null)return 1;if(b===null)return -1;
  const compared=typeof a==='string'?a.localeCompare(b):a-b;
  return compared?compared*direction:deliveryProjectName(left.project).localeCompare(deliveryProjectName(right.project));
 });
}
function renderDeliveryTable(rows){
	 rows=rows||[];
	 const visible=deliveryFilteredProjectRows(rows),body=$('d-project-table').querySelector('tbody'),sorted=sortedDeliveryRows(visible);
	 const totalCost=rows.filter(row=>row?.availability?.cost===true).reduce((total,row)=>total+(Number(row.covered_cost)||0),0);
	 const evidenceCounts={comparable:0,spend_only:0,git_only:0,unavailable:0};
	 rows.forEach(row=>{evidenceCounts[deliveryProjectEvidence(row).key]++;});
	 const evidenceSelect=$('d-evidence-filter');
	 if(evidenceSelect){
	  const labels={all:'All projects',comparable:'Comparable',spend_only:'Spend only',git_only:'Git only',unavailable:'No period evidence'};
	  [...evidenceSelect.options].forEach(option=>{const count=option.value==='all'?rows.length:evidenceCounts[option.value];option.textContent=`${labels[option.value]} (${count})`;});
	  evidenceSelect.value=deliveryEvidenceFilter;
	 }
	 body.innerHTML=sorted.length?sorted.map(row=>{
	  const codeAvailable=row?.availability?.code_pushed===true,costAvailable=row?.availability?.cost===true,ratioAvailable=row?.availability?.spend_per_1k===true;
	  const project=deliveryProjectName(row.project);
	  const cost=Number(row.covered_cost)||0,share=costAvailable&&totalCost>0?Math.max(0,Math.min(100,100*cost/totalCost)):0;
	  const shareBar=costAvailable&&totalCost>0?`<span class=deliverySpendShare style="--share:${share.toFixed(2)}" title="${esc(`${deliveryPercent(share)} of covered spend in this period`)}"><i aria-hidden=true></i></span>`:'';
	  const added=Number(row.added)||0,deleted=Number(row.deleted)||0,changed=added+deleted;
	  const composition=codeAvailable&&changed>0?`<span class=deliveryComposition title="${esc(`${f(added)} added · ${f(deleted)} deleted`)}"><i aria-hidden=true style="width:${(100*added/changed).toFixed(2)}%"></i><i class=deleted aria-hidden=true style="width:${(100*deleted/changed).toFixed(2)}%"></i></span>`:'';
	  return `<tr><td title="${esc(row.project)}" data-label="Project"><button class=deliveryProjectLink type=button data-delivery-project="${esc(row.project)}">${esc(project)}</button></td><td class=num data-label="Spend">${costAvailable?money(cost):'--'}${shareBar}</td><td class="num mono" data-label="Pushed lines">${codeAvailable?f(row.changed_lines):'--'}${composition}</td><td class="num mono" data-label="Spend / 1K">${ratioAvailable?money(row.spend_per_1k):'--'}</td></tr>`;
	 }).join(''):'<tr><td colspan=4><div class="deliveryEmpty emptyState">No projects match this filter.</div></td></tr>';
	 body.querySelectorAll('[data-delivery-project]').forEach(button=>button.addEventListener('click',()=>selectDeliveryProject(button.dataset.deliveryProject)));
 $('d-project-table').querySelectorAll('th').forEach(header=>{
  const button=header.querySelector('[data-delivery-sort]'),active=button?.dataset.deliverySort===deliverySort.key;
  header.setAttribute('aria-sort',active?(deliverySort.direction==='asc'?'ascending':'descending'):'none');
  const mark=button?.querySelector('.deliverySortMark');if(mark)mark.textContent=active?(deliverySort.direction==='asc'?'↑':'↓'):'';
 });
 const mobileSort=$('d-mobile-sort'),mobileChoice=`${deliverySort.key}:${deliverySort.direction}`;
 if(mobileSort&&[...mobileSort.options].some(option=>option.value===mobileChoice))mobileSort.value=mobileChoice;
}
function deliveryModelSortValue(row,key){
 if(key==='model')return String(row?.model||'').toLowerCase();
 const value=row?.[key];
 if(value===null||value===undefined)return null;
 const number=Number(value);
 return Number.isFinite(number)?number:null;
}
function sortedDeliveryModelRows(rows){
 const direction=deliveryModelSort.direction==='asc'?1:-1;
 return [...(rows||[])].sort((left,right)=>{
  const a=deliveryModelSortValue(left,deliveryModelSort.key),b=deliveryModelSortValue(right,deliveryModelSort.key);
  if(a===null&&b===null)return String(left.model).localeCompare(String(right.model));
  if(a===null)return 1;if(b===null)return -1;
  const compared=typeof a==='string'?a.localeCompare(b):a-b;
  return compared?compared*direction:String(left.model).localeCompare(String(right.model));
 });
}
function renderDeliveryModelTable(rows,coverage){
 rows=Array.isArray(rows)?rows:[];
 const body=$('d-model-table').querySelector('tbody'),sorted=sortedDeliveryModelRows(rows);
 const comparableLines=Number(coverage?.comparable_lines)||0,attributedLines=Number(coverage?.attributed_lines)||0;
 $('d-model-note').textContent=coverage?.available?`${f(attributedLines)} / ${f(comparableLines)} lines attributed${coverage?.truncated?' · top 50':''}`:'No model lines in this period.';
 body.innerHTML=sorted.length?sorted.map(row=>`<tr><td data-label="Model"><div class=modelName title="${esc(row.model)}">${esc(row.model)}</div></td><td data-label="App">${esc(row.runtime||'unknown runtime')}</td><td data-label="Effort">${esc(modelEffortLabel(row))}</td><td class="num mono" data-label="Spend">${money(Number(row.covered_cost)||0)}</td><td class="num mono" data-label="Est. lines">${f(Math.round(Number(row.attributed_lines)||0))}</td></tr>`).join(''):`<tr><td colspan=5><div class="deliveryEmpty emptyState">${comparableLines>0?'No model spend in comparable projects.':'Needs a project with both pushes and spend.'}</div></td></tr>`;
 $('d-model-table').querySelectorAll('th').forEach(header=>{
  const button=header.querySelector('[data-delivery-model-sort]'),active=button?.dataset.deliveryModelSort===deliveryModelSort.key;
  header.setAttribute('aria-sort',active?(deliveryModelSort.direction==='asc'?'ascending':'descending'):'none');
  const mark=button?.querySelector('.deliverySortMark');if(mark)mark.textContent=active?(deliveryModelSort.direction==='asc'?'↑':'↓'):'';
 });
}
function deliveryDateLabel(day,compact=false){
 const date=new Date(`${day}T12:00:00`);
 if(Number.isNaN(date.getTime()))return day;
 return date.toLocaleDateString(undefined,compact?{weekday:'short'}:{month:'short',day:'numeric'});
}
function deliveryFreshnessLabel(timestamp){
 const value=Number(timestamp),date=new Date(value*1000);
 if(!value||Number.isNaN(date.getTime()))return 'Git scan pending.';
 return `Last Git scan ${date.toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})}`;
}
function setDeliveryEvidencePanel(open){
 const panel=$('d-evidence-panel'),trigger=$('d-coverage');if(!panel||!trigger)return;
 panel.hidden=!open;trigger.setAttribute('aria-expanded',open?'true':'false');
 if(open)panel.scrollIntoView({block:'nearest'});
}
function setDeliveryEvidenceFilter(filter,{scroll=true}={}){
 deliveryEvidenceFilter=DELIVERY_EVIDENCE_FILTERS.includes(filter)?filter:'all';
 renderDeliveryTable(deliveryPayload?.project_rows||[]);
 if(scroll)$('d-project-table')?.scrollIntoView({block:'start'});
}
function setDeliveryEvidenceRatio(text,detail){
 const ratio=$('d-evidence-ratio');ratio.textContent=text;ratio.dataset.tip=detail;ratio.setAttribute('aria-description',detail);
}
function renderDeliveryEvidence(payload){
 const coverage=payload?.coverage||{},rows=Array.isArray(payload?.project_rows)?payload.project_rows:[];
 const counts={comparable:0,spend_only:0,git_only:0,unavailable:0};
 rows.forEach(row=>{counts[deliveryProjectEvidence(row).key]++;});
 const spendCoverage=Number(coverage.spend_coverage),available=coverage.spend_coverage!==null&&coverage.spend_coverage!==undefined&&Number.isFinite(spendCoverage);
 $('d-evidence-spend').textContent=available?`${Math.round(100*Math.max(0,Math.min(1,spendCoverage)))}%`:'--';
 $('d-evidence-count-comparable').textContent=f(counts.comparable);
 $('d-evidence-count-spend').textContent=f(counts.spend_only);
 $('d-evidence-count-git').textContent=f(counts.git_only);
 $('d-evidence-count-unavailable').textContent=f(counts.unavailable);
 const selected=Number(coverage.selected_repositories)||rows.length,comparable=Number(coverage.comparable_repositories)||0;
 const ratioDetail=selected?`${comparable} of ${selected} ${countWord(selected,'project')} can compare ratios; every ratio on this page uses only that overlap.`:'No selected projects have period evidence yet.';
 setDeliveryEvidenceRatio(selected?`${comparable} / ${selected} comparable`:'No comparable projects',ratioDetail);
 $('d-evidence-freshness').textContent=deliveryFreshnessLabel(coverage.last_checked);
}
function deliveryCommitsMeasured(row){
 const value=row?.commits;
 return typeof value==='number'&&Number.isFinite(value)&&value>=0;
}
function renderDeliveryDayInspector(row,index,rows){
 const inspector=$('d-day-inspector');if(!inspector)return;
 const ratioAvailable=row?.availability?.spend_per_1k===true,rollingAvailable=row?.availability?.rolling_spend_per_1k===true,costAvailable=row?.availability?.cost===true,codeAvailable=row?.availability?.code_pushed===true;
 const lowVolume=ratioAvailable&&(Number(row?.comparable_changed_lines)||0)<DELIVERY_MIN_RATIO_LINES;
 $('d-day-title').textContent=deliveryDateLabel(row.day);
 $('d-day-note').textContent=lowVolume?'Under 50 lines · ratio is noisy.':'';
 const dayCommitsAvailable=codeAvailable&&deliveryCommitsMeasured(row);
 $('d-day-metrics').innerHTML=`<div class="deliveryDayMetric metric"><span>Pushed lines</span><b>${codeAvailable?f(row.changed_lines):'--'}</b></div><div class="deliveryDayMetric metric"><span>Commits</span><b>${dayCommitsAvailable?f(row.commits):'--'}</b></div><div class="deliveryDayMetric metric"><span>Covered spend</span><b>${costAvailable?money(row.covered_cost):'--'}</b></div><div class="deliveryDayMetric metric"><span>Day spend / 1K</span><b>${ratioAvailable?money(row.spend_per_1k):'--'}</b></div><div class="deliveryDayMetric metric"><span>7-day spend / 1K</span><b>${rollingAvailable?money(row.rolling_spend_per_1k):'--'}</b></div>`;
 $('d-day-prev').disabled=index<=0;$('d-day-next').disabled=index>=rows.length-1;
 inspector.hidden=false;
}
function selectAdjacentDeliveryDay(offset){
 const rows=deliveryPayload?.days||[],index=rows.findIndex(row=>row.day===deliverySelectedDay),next=index+offset;
 if(index<0||next<0||next>=rows.length)return;
 selectDeliveryDay(rows[next].day);
}
function dismissGitChartInspector(){
 deliverySelectedDay='';
 const tip=$('d-chart-tip');if(tip)tip.hidden=true;
 $('d-chart-hits')?.querySelectorAll('button[aria-pressed=true]').forEach(button=>button.setAttribute('aria-pressed','false'));
 const inspector=$('d-day-inspector');if(inspector)inspector.hidden=true;
}
function hideDeliveryChartTip(){
 const tip=$('d-chart-tip');if(tip)tip.hidden=true;
}
function selectDeliveryDay(day,button){
 const rows=deliveryPayload?.days||[],index=rows.findIndex(row=>row.day===day);
 if(index<0)return;
 deliverySelectedDay=day;
 const row=rows[index];
 const tip=$('d-chart-tip');
	 tip.innerHTML=`<strong>${esc(deliveryDateLabel(day))}</strong><div class=deliveryTipRow><span>Pushed lines</span><b>${f(row.changed_lines)}</b></div><div class=deliveryTipRow><span>Added</span><b>+${f(row.added)}</b></div><div class=deliveryTipRow><span>Deleted</span><b>-${f(row.deleted)}</b></div><div class=deliveryTipRow><span>Commits</span><b>${deliveryCommitsMeasured(row)?f(row.commits):'--'}</b></div>`;
 const width=$('d-daily-chart')?.viewBox?.baseVal?.width||1040,count=Math.max(1,rows.length),{left,right}=DELIVERY_CHART_INSET,center=100*(left+((width-left-right)/count)*(index+.5))/width;
 tip.style.left=`${Math.max(11,Math.min(89,center)).toFixed(2)}%`;tip.style.top='66px';tip.hidden=false;
	 $('d-chart-hits').querySelectorAll('button').forEach(item=>item.setAttribute('aria-pressed',item.dataset.deliveryDay===day?'true':'false'));
	 renderDeliveryDayInspector(row,index,rows);
 if(button?.focus&&document.activeElement===button)button.scrollIntoView({block:'nearest',inline:'nearest'});
}
function selectDeliveryDayFromPointer(day,button){
 if(deliveryIgnorePointerSelection)return;
 selectDeliveryDay(day,button);
}
const DELIVERY_CHART_INSET={left:58,right:40,top:38,bottom:40};
function drawDeliveryChart(rows){
 rows=Array.isArray(rows)?rows:[];
 const svg=$('d-daily-chart'),hits=$('d-chart-hits'),VW=Math.max(320,Math.round(svg.clientWidth||1040)),VH=Math.max(200,Math.round(svg.clientHeight||310));
 svg.setAttribute('viewBox',`0 0 ${VW} ${VH}`);
	 if(!rows.length){svg.innerHTML=`<text class=axis x=${Math.round(VW/2)} y=${Math.round(VH/2)} text-anchor=middle>No Git evidence in this period</text>`;hits.innerHTML='';dismissGitChartInspector();return;}
 const left=DELIVERY_CHART_INSET.left,right=VW-DELIVERY_CHART_INSET.right,barTop=DELIVERY_CHART_INSET.top,barBottom=VH-DELIVERY_CHART_INSET.bottom,plotWidth=right-left;
 const lineMax=Math.max(1,...rows.map(row=>Number(row.changed_lines)||0));
 const step=plotWidth/Math.max(rows.length,1);
 const x=index=>left+step*(index+.5);
 const barWidth=Math.max(3,Math.min(34,step*.42));
 const labelEvery=Math.max(1,Math.ceil(rows.length/7));
 let markup='';
 [0,.5,1].forEach(share=>{const y=barBottom-share*(barBottom-barTop),value=lineMax*share;markup+=`<line class=grid x1=${left} y1=${y} x2=${right} y2=${y} /><text class=axis x=${left-9} y=${y+3} text-anchor=end>${esc(compactNumber(value))}</text>`;});
 rows.forEach((row,index)=>{
  const center=x(index),added=Number(row.added)||0,deleted=Number(row.deleted)||0,total=Math.max(0,added+deleted),height=total/lineMax*(barBottom-barTop),deletedHeight=total?height*(deleted/total):0,addedHeight=height-deletedHeight;
  if(deletedHeight>0)markup+=`<rect class=deleted x=${(center-barWidth/2).toFixed(1)} y=${(barBottom-deletedHeight).toFixed(1)} width=${barWidth.toFixed(1)} height=${deletedHeight.toFixed(1)} rx=1 />`;
  if(addedHeight>0)markup+=`<rect class=added x=${(center-barWidth/2).toFixed(1)} y=${(barBottom-height).toFixed(1)} width=${barWidth.toFixed(1)} height=${addedHeight.toFixed(1)} rx=2 />`;
  if(index%labelEvery===0||index===rows.length-1)markup+=`<text class=axis x=${center.toFixed(1)} y=${VH-11} text-anchor=middle>${esc(deliveryDateLabel(row.day,rows.length<=7))}</text>`;
 });
 svg.innerHTML=markup;
 hits.style.left=`${left}px`;hits.style.right=`${VW-right}px`;hits.style.top=`${barTop}px`;hits.style.bottom='9px';hits.style.setProperty('--delivery-days',rows.length);
 hits.innerHTML=rows.map(row=>`<button type=button data-delivery-day="${esc(row.day)}" aria-pressed=false aria-label="${esc(`${deliveryDateLabel(row.day)}, ${f(row.changed_lines)} pushed text lines, ${f(row.added)} added and ${f(row.deleted)} deleted`)}"></button>`).join('');
 hits.querySelectorAll('button').forEach(button=>{
  button.addEventListener('pointerenter',()=>selectDeliveryDayFromPointer(button.dataset.deliveryDay,button));
  button.addEventListener('focus',()=>selectDeliveryDay(button.dataset.deliveryDay,button));
  button.addEventListener('click',()=>selectDeliveryDay(button.dataset.deliveryDay,button));
 });
	 if(deliverySelectedDay&&rows.some(row=>row.day===deliverySelectedDay))selectDeliveryDay(deliverySelectedDay);else dismissGitChartInspector();
}
function renderDeliverySignals(payload){
 const list=$('d-signals'),rows=deliverySignals(payload);
	 list.innerHTML=rows.length?rows.map(row=>{
	  const body=`<i aria-hidden=true></i><div class=deliverySignalBody><b>${esc(row.title)}</b>${row.text?`<span>${esc(row.text)}</span>`:''}</div>`;
	  const classes=`deliverySignal ${row.kind==='warn'?'warn':row.kind==='good'?'good':''}`;
	  return row.action?`<button class="${classes}" type=button data-delivery-signal-action="${esc(row.action)}" data-delivery-signal-value="${esc(row.value||'')}">${body}</button>`:`<div class="${classes}">${body}</div>`;
	 }).join(''):'<div class="deliverySignalEmpty emptyState">No overlapping Git and spend in this period.</div>';
	 list.querySelectorAll('[data-delivery-signal-action]').forEach(button=>button.addEventListener('click',event=>{
	  event.stopPropagation();
	  const action=button.dataset.deliverySignalAction,value=button.dataset.deliverySignalValue;
	  if(action==='day'){deliveryIgnorePointerSelection=true;$('d-chart-wrap')?.scrollIntoView({block:'center'});selectDeliveryDay(value);}
	  else if(action==='project')selectDeliveryProject(value);
	  else if(action==='coverage')setDeliveryEvidencePanel(true);
	 }));
}
function renderDeliveryShape(payload){
 const host=$('d-shape'),rows=deliveryShapeRows(payload);
 if(!rows.some(row=>row.distribution)){host.innerHTML='<div class="deliveryShapeEmpty emptyState">No daily data in this period.</div>';return;}
 const header='<div class=deliveryShapeHeader><span></span><span></span><span><span>Median</span><span>Range</span><span>High</span></span></div>';
 host.innerHTML=header+rows.map(row=>{
  const distribution=row.distribution;
  if(!distribution)return `<div class=deliveryShapeRow><span class=deliveryShapeIdentity><b>${esc(row.label)}</b><small>No data</small></span><span class=deliveryShapeRail></span><span class=deliveryShapeValues><span>--</span><span>--</span><span>--</span></span></div>`;
  const range=deliveryShapeRange(distribution),stable=range.label==='Middle half';
  const fence=stable?Math.max(distribution.p75+1.5*(distribution.p75-distribution.p25),distribution.median*1.25,distribution.p75):distribution.max;
  const span=Math.min(distribution.max,fence)||1,beyond=stable&&distribution.max>span*1.0001;
  const percent=value=>`${Math.max(0,Math.min(100,100*value/span)).toFixed(2)}%`;
  const rail=`<span class=deliveryShapeRail role=img aria-label="${esc(`${row.label}: median ${row.format(distribution.median)}, ${range.label.toLowerCase()} ${row.format(range.low)} to ${row.format(range.high)}, high day ${row.format(distribution.max)}${beyond?' beyond the rail scale':''} across ${distribution.count} ${countWord(distribution.count,'day')}`)}"><i class=deliveryShapeIqr style="--p25:${percent(range.low)};--p75:${percent(range.high)}"></i><i class=deliveryShapeMedian style="--median:${percent(distribution.median)}"></i><i class="deliveryShapeHigh${beyond?' beyond':''}" style="--p90:${percent(distribution.max)}"></i></span>`;
  return `<div class=deliveryShapeRow><span class=deliveryShapeIdentity><b>${esc(row.label)}</b><small>${distribution.count} ${countWord(distribution.count,'day')} · ${esc(range.label.toLowerCase())}</small></span>${rail}<span class=deliveryShapeValues><span data-label="Median">${esc(row.format(distribution.median))}</span><span data-label="${esc(range.label)}">${esc(`${row.format(range.low)}–${row.format(range.high)}`)}</span><span data-label="High day">${esc(row.format(distribution.max))}</span></span></div>`;
 }).join('');
}
document.addEventListener('click',event=>{
 const target=event.target instanceof Element?event.target:null;
	 if(!target?.closest('#d-chart-wrap')&&!target?.closest('#d-day-inspector'))dismissGitChartInspector();
});
document.addEventListener('pointermove',()=>{deliveryIgnorePointerSelection=false;},{passive:true});
document.addEventListener('keydown',event=>{
 if(event.key==='Escape')dismissGitChartInspector();
});
function renderDelivery(payload){
 deliveryPayload=payload||{};
 const projects=Array.isArray(payload?.projects)?payload.projects:[],select=$('d-project');
 const selected=projects.includes(deliveryProject)?deliveryProject:'';
 if(selected!==deliveryProject){deliveryProject=selected;localStorage.setItem('tm_delivery_project',deliveryProject);}
 select.innerHTML='<option value="">All projects</option>'+projects.map(project=>`<option value="${esc(project)}">${esc(deliveryProjectName(project))}</option>`).join('');select.value=deliveryProject;
 const rangeSelect=$('d-range');rangeSelect.value=deliveryRange;
 $('d-period-label').textContent=rangeSelect.selectedOptions[0]?.textContent||'Selected period';
 const overall=payload?.overall||{},available=overall?.availability||{},coverage=payload?.coverage||{};
 $('d-code-pushed').textContent=available.code_pushed===true?compactNumber(overall.changed_lines):'--';
 $('d-added').textContent=available.code_pushed===true?`+${f(overall.added)}`:'--';
 $('d-deleted').textContent=available.code_pushed===true?`-${f(overall.deleted)}`:'--';
 $('d-spend-lines').textContent=available.spend_per_1k===true?money(overall.spend_per_1k):'--';
 $('d-spend-note').innerHTML=available.cost===true?`<b>${money(overall.covered_cost)}</b> covered spend`:'Waiting for coverage.';
 const efficiency=overall?.efficiency||{},yieldAvailable=efficiency?.availability?.delivery_yield===true;
 // The Git service compares month-to-date with the same days of last month, not the whole month.
 const comparison=payload?.comparison||{},comparisonLabel=deliveryRange==='month'?'same days last month':efficiencyComparisonWindows(deliveryRange)?.label||'prior period';
 const gitDelta=key=>comparison.available?percentTrendDelta(comparison[key],comparisonLabel):{text:'No comparable prior period',direction:''};
 // Git deltas stay neutral: pushed-code evidence is never scored as better or worse.
 renderTrendDelta('d-code-pushed-change',gitDelta('code_pushed_pct'),'',available.code_pushed!==true);
 renderTrendDelta('d-spend-lines-change',gitDelta('spend_per_1k_pct'),'',available.spend_per_1k!==true);
 $('d-push-yield').textContent=yieldAvailable?deliveryRatio(efficiency.delivery_yield):'--';
 $('d-push-yield-note').textContent=yieldAvailable?'lines / 1K output':'Waiting for output tokens.';
 renderTrendDelta('d-push-yield-change',gitDelta('delivery_yield_pct'),'',!yieldAvailable);
 const changedLines=Number(overall?.changed_lines)||0;
 $('d-rework').textContent=available.code_pushed===true&&changedLines>0?deliveryPercent(100*(Number(overall.deleted)||0)/changedLines):'--';
 $('d-comparison').innerHTML=deliveryComparisonText(payload?.comparison||{});
 const selectedCount=Number(coverage.selected_repositories)||0,measured=Number(coverage.measured_repositories)||0,comparable=Number(coverage.comparable_repositories)||0,checked=Number(coverage.last_checked)||0,days=Array.isArray(payload?.days)?payload.days:[];
 const coverageNode=$('d-coverage');
 const spendCoverage=Number(coverage.spend_coverage),spendCoverageAvailable=coverage.spend_coverage!==null&&coverage.spend_coverage!==undefined&&Number.isFinite(spendCoverage);
 const coveragePercent=spendCoverageAvailable?Math.max(0,Math.min(100,Math.round(100*spendCoverage))):0,coverageBar=$('d-coverage-bar');
 const evidencePartial=!spendCoverageAvailable||coveragePercent<100||comparable<selectedCount||available.partial===true;
 coverageBar.style.setProperty('--delivery-coverage',coveragePercent);
 coverageBar.classList.toggle('partial',evidencePartial);
 if(spendCoverageAvailable){coverageBar.setAttribute('aria-valuenow',coveragePercent);coverageBar.removeAttribute('aria-valuetext');}
 else{coverageBar.removeAttribute('aria-valuenow');coverageBar.setAttribute('aria-valuetext','Spend coverage unavailable');}
 $('d-coverage-percent').textContent=spendCoverageAvailable?`${coveragePercent}%`:'--';
 const activeDays=days.filter(row=>(Number(row?.changed_lines)||0)>0).length;
 $('d-active-days').textContent=checked&&available.code_pushed===true&&days.length?`${activeDays} / ${days.length}`:'--';
 const commitsAvailable=available.code_pushed===true&&deliveryCommitsMeasured(overall);
 $('d-commits').textContent=commitsAvailable?f(overall.commits):'--';
 $('d-repo-evidence').textContent=checked&&selectedCount?`${measured} / ${selectedCount}`:'--';
 $('d-comparable-evidence').textContent=checked?`${comparable} ${countWord(comparable,'project')}`:'--';
 coverageNode.classList.toggle('partial',evidencePartial||!checked);
 coverageNode.textContent=!checked?'Scanning local Git':selectedCount?(evidencePartial?'Partial evidence':'Evidence ready'):'No Git projects found';
	 drawDeliveryChart(days);renderDeliveryModelTable(payload?.model_rows||[],payload?.model_coverage);renderDeliveryTable(payload?.project_rows||[]);
	 renderDeliveryEvidence(payload);renderDeliverySignals(payload);renderDeliveryShape(payload);
}
let deliveryRetryTimer=0,deliveryLoadRetries=0;
