function filledModelDays(rows,window){
 if(!window.days)return rows;
 const byDay=new Map(rows.map(row=>[row.day,row]));
 return window.days.map(day=>byDay.get(day)||{day});
}
function modelEffortLabel(row){
 const effort=String(row?.reasoning_effort||'').trim().replaceAll('_',' ');
 if(effort)return effort;
 return Number(row?.thinking_executions||0)>0?'thinking unavailable':'unavailable';
}
// Claude Code and Codex leave effort empty when the app's default is in use; other apps never record it.
function efficiencyEffortLabel(row){
 const providers=row?.providers||[];
 if(!String(row?.reasoning_effort||'').trim()&&providers.length&&providers.every(p=>p==='claude'||p==='codex'))return 'default';
 return modelEffortLabel(row);
}
function modelRuntimeLabel(row){return `${row.model} · ${row.runtime||'unknown runtime'} · ${modelEffortLabel(row)}`;}
function migrateModelRuntimeFilters(filters,models){
 const ids=new Set(models.map(row=>row.id)),migrated=[];
 (filters||[]).forEach(value=>{if(ids.has(value)){migrated.push(value);return;}const legacy=models.filter(row=>`${row.model}::${row.runtime||'unknown runtime'}`===value);if(legacy.length){migrated.push(...legacy.map(row=>row.id));return;}const candidates=models.filter(row=>row.model===value).sort((a,b)=>Number(b.output_tokens||0)-Number(a.output_tokens||0));if(candidates.length)migrated.push(candidates[0].id);});
 return [...new Set(migrated)];
}
function buildModelTrend(models,window,names){
 const byModel=models.map(model=>({id:model.id,model:modelRuntimeLabel(model),color:modelColor(model.id,names),estimated:hasLocalEstimate(model),byDay:new Map((model.daily||[]).filter(row=>modelDayInRange(row.day,window)).map(row=>[row.day,row]))}));
 const days=window.days||[...new Set(byModel.flatMap(model=>[...model.byDay.keys()]))].sort();
 return {days,series:byModel.map(model=>({id:model.id,model:model.model,color:model.color,estimated:model.estimated,points:days.map(day=>{const row=model.byDay.get(day)||{day};return {...row,perf:aggregateModelDays([row])};})}))};
}
function modelTokensPerExecution(row,key){
 const executions=Number(row.executions||0);
 return executions?Number(row[key]||0)/executions:0;
}
const MODEL_TREND_METRICS={
 speed:{note:'observed tok/s',axis:'tok/s',format:speedFmt,value:row=>Number(row.perf.output_tps||0),available:row=>Boolean(row.perf.available)},
 wait:{note:'median wait',axis:'median wait',format:waitFmt,value:row=>Number(row.perf.median_wait_s||0),available:row=>Number(row.perf.wait_samples||0)>0},
 avg_input:{note:'avg input / execution',axis:'input / exec',format:compactNumber,value:row=>modelTokensPerExecution(row,'input_tokens'),available:row=>metricAvailable(row,'tokens')&&Number(row.executions||0)>0},
 avg_output:{note:'avg output / execution',axis:'output / exec',format:compactNumber,value:row=>modelTokensPerExecution(row,'output_tokens'),available:row=>metricAvailable(row,'tokens')&&Number(row.executions||0)>0},
};
function modelTrendRowHasUsage(row){
 return Number(row?.input_tokens||0)>0||Number(row?.output_tokens||0)>0||Number(row?.executions||0)>0;
}
function drawModelTrend(chart){
 const svg=$('m-chart'),tip=$('m-chart-tip'),VW=Math.max(320,Math.round(svg.clientWidth||1040)),VH=Math.max(160,Math.round(svg.clientHeight||270)),ML=62,MR=72,MT=18,MB=38,PW=VW-ML-MR,PH=VH-MT-MB;svg.setAttribute('viewBox',`0 0 ${VW} ${VH}`);
 const days=chart.days||[],series=chart.series||[],metric=MODEL_TREND_METRICS[modelTrendMetric]||MODEL_TREND_METRICS.speed;
 const hasData=series.some(model=>model.points.some(row=>Number(row.input_tokens||0)||Number(row.output_tokens||0)||Number(row.executions||0)||metric.available(row)));
 if(!days.length||!series.length||!hasData){svg.innerHTML=`<text x="${VW/2}" y="${VH/2}" fill="${CHART.lbl}" text-anchor="middle" font-size="${svgTextPx(svg,VW,VH,13)}">No model history in this window</text>`;tip.style.display='none';return;}
 const lineValue=metric.value,lineAvailable=metric.available;
 const maxOutput=Math.max(1,...series.flatMap(model=>model.points.map(row=>Number(row.output_tokens||0)))),maxLine=Math.max(1,...series.flatMap(model=>model.points.filter(lineAvailable).map(lineValue)));
 const step=PW/Math.max(1,days.length),groupW=Math.max(2,Math.min(30,step*.72)),barW=Math.max(.8,groupW/series.length);
 const x=index=>ML+step*(index+.5),outY=value=>MT+PH-PH*(Number(value||0)/maxOutput),lineY=value=>MT+PH-PH*(Number(value||0)/maxLine),lineFormat=metric.format;
 let html='';
 for(let i=0;i<=4;i++){const y=MT+PH*i/4;html+=`<line x1="${ML}" x2="${ML+PW}" y1="${y}" y2="${y}" stroke="${CHART.grid}" stroke-width="1"/><text x="${ML-8}" y="${y+4}" text-anchor="end" fill="${CHART.lbl}" font-size="${svgTextPx(svg,VW,VH)}">${compactNumber(maxOutput*(1-i/4))}</text><text x="${ML+PW+8}" y="${y+4}" fill="${CHART.lbl}" font-size="${svgTextPx(svg,VW,VH)}">${lineFormat(maxLine*(1-i/4))}</text>`;}
 days.forEach((day,index)=>series.forEach((model,modelIndex)=>{const row=model.points[index],y=outY(row.output_tokens),h=MT+PH-y,barX=x(index)-groupW/2+modelIndex*barW,paint=modelTrendPaint(model.color);html+=`<rect x="${barX}" y="${y}" width="${Math.max(.8,barW-.5)}" height="${Math.max(0,h)}" rx="2" fill="${paint.barFill}" stroke="${paint.barStroke}" stroke-opacity=".78"/>`;}));
 series.forEach(model=>{const paint=modelTrendPaint(model.color),{segments,bridges}=chartGapRuns(model.points.flatMap((row,index)=>lineAvailable(row)?[{index,x:x(index),y:lineY(lineValue(row))}]:[]));bridges.forEach(([from,to])=>{html+=chartGapBridge(from.x,from.y,to.x,to.y,paint.line);});segments.filter(segment=>segment.length>1).forEach(segment=>{html+=`<path d="${segment.map((point,index)=>`${index?'L':'M'}${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(' ')}" fill="none" stroke="${paint.line}" stroke-width="2.5" ${model.estimated?'stroke-dasharray="6 4"':''} stroke-linecap="round" stroke-linejoin="round"/>`;});model.points.forEach((row,index)=>{if(lineAvailable(row))html+=`<circle cx="${x(index)}" cy="${lineY(lineValue(row))}" r="3.8" fill="${paint.line}" stroke="${CHART.ink}" stroke-width="2"/>`;});});
 const labelEvery=Math.max(1,Math.ceil(days.length/8));
 days.forEach((day,index)=>{const remaining=days.length-1-index,showLabel=index===days.length-1||(index%labelEvery===0&&remaining>=Math.max(2,Math.ceil(labelEvery/2)));if(showLabel)html+=`<text x="${x(index)}" y="${VH-12}" text-anchor="middle" fill="${CHART.lbl}" font-size="${svgTextPx(svg,VW,VH)}">${esc(day.slice(5))}</text>`;html+=`<rect data-model-day="${esc(day)}" x="${ML+step*index}" y="${MT}" width="${step}" height="${PH}" fill="transparent" pointer-events="all"/>`;});
 html+=`<text x="8" y="12" fill="${CHART.lbl}" font-size="${svgTextPx(svg,VW,VH)}">output</text><text x="${VW-8}" y="12" text-anchor="end" fill="${CHART.lbl}" font-size="${svgTextPx(svg,VW,VH)}">${esc(metric.axis)}</text>`;
 svg.innerHTML=html;
 svg.setAttribute('aria-label',`Daily model ${metric.note} and output token volume`);
 const cancelTipHide=()=>{if(tip._hideTimer){clearTimeout(tip._hideTimer);tip._hideTimer=0;}};
 const hideTip=()=>{cancelTipHide();tip.style.display='none';};
 const scheduleTipHide=()=>{cancelTipHide();tip._hideTimer=setTimeout(hideTip,180);};
 const show=(event,index)=>{
  cancelTipHide();
  const rect=$('m-chart').parentElement.getBoundingClientRect(),day=days[index],rows=series.map(model=>({model:model.model,color:model.color,row:model.points[index]})).filter(item=>modelTrendRowHasUsage(item.row)),dayChanged=tip.dataset.day!==day;
  tip.innerHTML=`<div class=h>${esc(day)}</div>`+(rows.length?'':'<div class=r><span>No data</span></div>')+rows.map(item=>{
   const outputAvailable=metricAvailable(item.row,'tokens');
   const selectedAvailable=metric.available(item.row);
   return `<div class=modelTipSeries><div class=modelTipName title="${esc(item.model)}"><i class=modelSwatch style="background:${item.color}"></i>${esc(item.model)}</div><div class=modelTipMetrics><span><small>output</small><b>${outputAvailable?compactNumber(item.row.output_tokens||0):'--'}</b></span><span><small>${esc(metric.note)}</small><b>${selectedAvailable?metric.format(metric.value(item.row)):'--'}</b></span></div></div>`;
  }).join('');
  tip.dataset.day=day;tip.style.display='block';
  const pointerX=event.clientX-rect.left,rightX=pointerX+12,leftX=pointerX-tip.offsetWidth-12;
  tip.style.left=(rightX+tip.offsetWidth<=rect.width-8?rightX:Math.max(8,leftX))+'px';tip.style.top=Math.max(8,event.clientY-rect.top-tip.offsetHeight-10)+'px';if(dayChanged)tip.scrollTop=0;
 };
 [...svg.querySelectorAll('[data-model-day]')].forEach((hit,index)=>{hit.onpointerenter=event=>show(event,index);hit.onpointermove=event=>show(event,index);hit.onpointerleave=scheduleTipHide;});
 tip.onpointerenter=cancelTipHide;tip.onpointerleave=scheduleTipHide;tip.onfocus=cancelTipHide;tip.onblur=scheduleTipHide;
}
function setModelKpiHelp(labelId,detail){
 const label=$(labelId);if(!label)return;
 const definition=label.dataset.helpDefinition||label.dataset.tip||'';
 if(!label.dataset.helpDefinition)label.dataset.helpDefinition=definition;
 const copy=[definition,detail].filter(Boolean).join(' ');
 label.dataset.tip=copy;label.setAttribute('aria-description',copy);
}
function renderMatchedPace(stats,selected){
 const value=$('m-change'),note=$('m-change-note');note.className='subline modelDelta';value.title='';note.title='';
 if(selected.length!==2){value.textContent='--';note.textContent='Select 2 models';note.title='Matched pace compares exactly two model-runtime histories.';return;}
 if(selected.some(hasLocalEstimate)){value.textContent='--';note.textContent='Unavailable for local estimates';note.title='Cursor local input-context and visible-output estimates are excluded from matched pace.';return;}
 const ids=new Set(selected.map(row=>row.id)),comparison=(stats?.matched_pace?.windows?.[modelRange]||[]).find(row=>ids.has(row.a_id)&&ids.has(row.b_id));
 if(!comparison){value.textContent='--';note.textContent='No pairwise timing';note.title='The selected histories do not contain comparable completed timing samples in this window.';return;}
 if(!comparison.available){value.textContent='--';note.textContent='Not enough matched evidence';note.title=`${comparison.reason} · ${f(comparison.a_samples)}/${f(comparison.b_samples)} timed. Token Meter withholds the verdict until there are at least 20 matched pairs covering 30% of the smaller eligible history.`;return;}
 const byId=new Map(selected.map(row=>[row.id,row])),ratio=Number(comparison.pace_ratio||0),low=Number(comparison.ci_low||0),high=Number(comparison.ci_high||0),uncertain=low<=1&&high>=1;
 const evidence=`${f(comparison.matched_pairs)} matched · ${pct(comparison.coverage)} coverage · 95% CI ${low.toFixed(2)}–${high.toFixed(2)}×`;
 if(uncertain){value.textContent='≈ same';note.textContent='No clear difference';value.title=`Observed ratio ${ratio.toFixed(2)}×; confidence interval includes parity.`;note.title=`${evidence}. The 95% confidence interval crosses 1.00, so the observed histories do not provide a clear faster-runtime verdict.`;return;}
 const fasterId=ratio>1?comparison.a_id:comparison.b_id,factor=ratio>1?ratio:1/ratio,faster=byId.get(fasterId);
 value.textContent=`${factor.toFixed(2)}×`;note.classList.add('up');note.textContent=`${modelRuntimeLabel(faster)} faster`;note.title=`${note.textContent} · ${evidence}`;
}
function setModelKpiValue(id,value,qualifier){
 const el=$(id);el.textContent=value;
 if(qualifier.trim()){const tag=document.createElement('small');tag.className='modelValueQual';tag.textContent=qualifier.trim();el.append(tag);}
}
const MODEL_OTHER_COLOR=THEME.modelOther;
const MODEL_MIX_METRICS={
 cost:{label:'Cost',unit:'est. cost',value:day=>metricAvailable(day,'cost')?Number(day.cost||0):0,format:value=>money(value)},
 output:{label:'Output',unit:'output tokens',value:day=>metricAvailable(day,'tokens')?Number(day.output_tokens||0):0,format:compactNumber},
 executions:{label:'Executions',unit:'executions',value:day=>Number(day.executions||0),format:value=>f(Math.round(value))},
};
function modelGroupKey(row){return `${row.model}::${row.runtime||'unknown runtime'}`;}
function modelGroupRows(selected,rangeWindow){
 const groups=new Map();
 selected.forEach(model=>{
  const daily=(model.daily||[]).filter(day=>modelDayInRange(day.day,rangeWindow)),window=aggregateModelDays(daily);
  if(!(window.input_tokens||window.output_tokens||window.executions||window.wait_samples||window.cost))return;
  const key=modelGroupKey(model),group=groups.get(key)||{key,model:model.model,runtime:model.runtime,variants:[],daily:[]};
  group.variants.push({...model,window});group.daily.push(...daily);groups.set(key,group);
 });
 return [...groups.values()].map(group=>({...group,window:aggregateModelDays(group.daily),ids:group.variants.map(row=>row.id),estimated:group.variants.some(hasLocalEstimate)}));
}
function modelBoardValue(row,key){
 const data=row.window||{},costAvailable=data.availability?.cost===true;
 if(key==='model')return `${row.model} · ${row.runtime}`;
 if(key==='cost')return costAvailable?Number(data.cost||0):null;
 if(key==='cost_per_exec'){
  if(!costAvailable)return null;
  const covered=Number(data.cost_covered_executions||0);
  if(covered>0)return (Number(data.cost_covered_cost||0)||Number(data.cost||0))/covered;
  return Number(data.executions||0)>0?Number(data.cost||0)/Number(data.executions):null;
 }
 if(key==='cache')return efficiencyMetrics(data).cache_hit_ratio;
 if(key==='executions')return Number(data.executions||0);
 if(key==='output')return metricAvailable(data,'tokens')?Number(data.output_tokens||0):null;
 if(key==='speed')return data.available?Number(data.output_tps||0):null;
 if(key==='wait')return data.wait_samples?Number(data.median_wait_s||0):null;
 if(key==='pushback')return row.variants?modelPushbackFor(row)?.rate??null:null;
 return null;
}
function sortModelBoard(rows,sort=modelSort){
 const direction=sort.direction==='asc'?1:-1,label=row=>`${row.model} · ${row.runtime} · ${row.reasoning_effort||''}`;
 return [...(rows||[])].sort((a,b)=>{const left=modelBoardValue(a,sort.key),right=modelBoardValue(b,sort.key);if(left===null&&right===null)return label(a).localeCompare(label(b));if(left===null)return 1;if(right===null)return -1;const compared=typeof left==='string'?left.localeCompare(right):left-right;return compared?compared*direction:label(a).localeCompare(label(b));});
}
function modelSpendRanking(groups){
 const label=row=>`${row.model} · ${row.runtime}`;
 return [...(groups||[])].sort((a,b)=>{const left=modelBoardValue(a,'cost'),right=modelBoardValue(b,'cost');if(left!==right&&(left===null||right===null))return left===null?1:-1;if(left!==null&&right!==null&&right!==left)return right-left;return (Number(modelBoardValue(b,'output'))||0)-(Number(modelBoardValue(a,'output'))||0)||label(a).localeCompare(label(b));});
}
function renderModelSpend(groups,ranking,top,allSelected){
 const costed=groups.filter(group=>modelBoardValue(group,'cost')!==null),uncosted=groups.length-costed.length,total=costed.reduce((sum,group)=>sum+Number(group.window.cost||0),0),partial=costed.some(group=>metricPartial(group.window,'cost'));
 $('m-spend-total').textContent=total>0?`≈ ${money(total)} est. · ${modelRangeLabel(modelRange)}${partial?' · partial':''}`:'Cost unavailable';
 const topKeys=new Set(top.map(group=>group.key)),topColor=modelRankedColors(top),segments=top.filter(group=>Number(modelBoardValue(group,'cost'))>0).map((group,index)=>({label:group.model,runtime:group.runtime,cost:Number(group.window.cost||0),color:topColor.get(group.key),ids:group.ids}));
 const rest=ranking.filter(group=>!topKeys.has(group.key)&&Number(modelBoardValue(group,'cost'))>0),restCost=rest.reduce((sum,group)=>sum+Number(group.window.cost||0),0);
 if(restCost>0)segments.push({label:`Other (${f(rest.length)})`,runtime:'',cost:restCost,color:MODEL_OTHER_COLOR,ids:[]});
 const share=segment=>total>0?segment.cost/total:0,name=segment=>segment.runtime?`${segment.label} · ${segment.runtime}`:segment.label;
 setLogHtml($('m-spend-bar'),total>0?segments.map(segment=>{const portion=share(segment),title=`${name(segment)} · ≈ ${money(segment.cost)} est. · ${pct(portion)}`,inner=portion>=.1?`<span>${esc(segment.label)}</span><b>${pct(portion)}</b>`:'';return segment.ids.length?`<button class=modelSpendSeg type=button style="flex:${portion} 1 0;background:${segment.color}" data-model-ids="${esc(segment.ids.join('\n'))}" title="${esc(title)} · click to focus" aria-label="${esc(title)}. Focus this model.">${inner}</button>`:`<div class=modelSpendSeg role=img style="flex:${portion} 1 0;background:${segment.color}" title="${esc(title)}" aria-label="${esc(title)}">${inner}</div>`;}).join(''):'<div class="modelSpendEmpty emptyState">No estimated cost in this window</div>');
 setLogHtml($('m-spend-legend'),segments.map(segment=>`<${segment.ids.length?`button type=button data-model-ids="${esc(segment.ids.join('\n'))}"`:'span'} class=modelSpendChip title="${esc(name(segment))}"><i style="background:${segment.color}"></i><span class=modelSpendChipName>${esc(segment.label)}</span>${segment.runtime?`<small>${esc(segment.runtime)}</small>`:''}<b class=mono>${money(segment.cost)}</b><em>${pct(share(segment))}</em></${segment.ids.length?'button':'span'}>`).join(''));
 const notes=[];
 if(uncosted){const detail=`${f(uncosted)} ${countWord(uncosted,'model runtime')} without cost data ${uncosted===1?'is':'are'} excluded, not counted as $0.`;notes.push(`<span class=fieldtip tabindex=0 aria-description="${esc(detail)}" data-tip="${esc(detail)}">${esc(`${f(uncosted)} unpriced, excluded`)}</span>`);}
 if(costed.some(group=>group.estimated))notes.push(esc('Includes Cursor local estimates.'));
 setLogHtml($('m-spend-note'),notes.join(' ')+(allSelected?'':' <button class=modelLinkBtn type=button data-model-clear>Show all models</button>'));
}
function drawModelMix(groups,top,rangeWindow){
 const svg=$('m-mix'),tip=$('m-mix-tip'),VW=Math.max(320,Math.round(svg.clientWidth||1040)),VH=Math.max(160,Math.round(svg.clientHeight||250)),ML=62,MR=18,MT=16,MB=34,PW=VW-ML-MR,PH=VH-MT-MB,metric=MODEL_MIX_METRICS[modelMixMetric];svg.setAttribute('viewBox',`0 0 ${VW} ${VH}`);
 const days=rangeWindow.days||[...new Set(groups.flatMap(group=>group.daily.map(day=>day.day)))].sort();
 const topIndex=new Map(top.map((group,index)=>[group.key,index])),topColor=modelRankedColors(top),series=[...top.map(group=>({label:`${group.model} · ${group.runtime}`,color:topColor.get(group.key),values:new Map()})),{label:'Other',color:MODEL_OTHER_COLOR,values:new Map()}];
 groups.forEach(group=>{const target=topIndex.has(group.key)?series[topIndex.get(group.key)]:series[series.length-1];group.daily.forEach(day=>target.values.set(day.day,(target.values.get(day.day)||0)+metric.value(day)));});
 const totals=days.map(day=>series.reduce((sum,item)=>sum+(item.values.get(day)||0),0)),max=Math.max(0,...totals);
 [...$('m-mix-metric').querySelectorAll('button')].forEach(button=>button.classList.toggle('on',button.dataset.modelMix===modelMixMetric));
 $('m-mix-note').textContent=`Top ${f(top.length)} by ${top.every(group=>Number(modelBoardValue(group,'cost'))>0)?'spend':top.some(group=>Number(modelBoardValue(group,'cost'))>0)?'spend, then output':'output'}${series[series.length-1].values.size?' + Other':''}`;
 if(!days.length||max<=0){svg.innerHTML=`<text x="${VW/2}" y="${VH/2}" fill="${CHART.lbl}" text-anchor="middle" font-size="${svgTextPx(svg,VW,VH,13)}">${esc(modelMixMetric==='cost'?'No estimated cost in this window. Try Output or Executions.':'No model activity in this window')}</text>`;tip.style.display='none';return;}
 const step=PW/Math.max(1,days.length),barW=Math.max(1,Math.min(34,step*.7)),x=index=>ML+step*(index+.5),h=value=>PH*value/max;
 let html='';
 for(let i=0;i<=4;i++){const y=MT+PH*i/4;html+=`<line x1="${ML}" x2="${ML+PW}" y1="${y}" y2="${y}" stroke="${CHART.grid}" stroke-width="1"/><text x="${ML-8}" y="${y+4}" text-anchor="end" fill="${CHART.lbl}" font-size="${svgTextPx(svg,VW,VH)}">${esc(metric.format(max*(1-i/4)))}</text>`;}
 days.forEach((day,index)=>{let base=MT+PH;series.forEach(item=>{const value=item.values.get(day)||0;if(value<=0)return;const height=h(value);base-=height;html+=`<rect x="${(x(index)-barW/2).toFixed(1)}" y="${base.toFixed(1)}" width="${barW.toFixed(1)}" height="${Math.max(0,height-.6).toFixed(1)}" fill="${item.color}" fill-opacity=".9"/>`;});});
 const labelEvery=Math.max(1,Math.ceil(days.length/8));
 days.forEach((day,index)=>{const remaining=days.length-1-index,showLabel=index===days.length-1||(index%labelEvery===0&&remaining>=Math.max(2,Math.ceil(labelEvery/2)));if(showLabel)html+=`<text x="${x(index)}" y="${VH-10}" text-anchor="middle" fill="${CHART.lbl}" font-size="${svgTextPx(svg,VW,VH)}">${esc(day.slice(5))}</text>`;html+=`<rect data-mix-day="${index}" x="${ML+step*index}" y="${MT}" width="${step}" height="${PH}" fill="transparent" pointer-events="all"/>`;});
 svg.innerHTML=html;svg.setAttribute('aria-label',`Daily ${metric.unit} stacked by model`);
 const hide=()=>{tip.style.display='none';};
 const show=(event,index)=>{
  const rows=series.map(item=>({label:item.label,color:item.color,value:item.values.get(days[index])||0})).filter(item=>item.value>0);
  if(!rows.length){hide();return;}
  tip.innerHTML=`<div class=h>${esc(days[index])} · ${esc(metric.format(totals[index]))}</div>`+rows.map(item=>`<div class=r><span><i class=modelSwatch style="background:${item.color}"></i>${esc(item.label)}</span><b>${esc(metric.format(item.value))} · ${pct(item.value/totals[index])}</b></div>`).join('');
  tip.style.display='block';
  const rect=svg.parentElement.getBoundingClientRect(),pointerX=event.clientX-rect.left,rightX=pointerX+12,leftX=pointerX-tip.offsetWidth-12;
  tip.style.left=(rightX+tip.offsetWidth<=rect.width-8?rightX:Math.max(8,leftX))+'px';tip.style.top=Math.max(8,event.clientY-rect.top-tip.offsetHeight-10)+'px';
 };
 [...svg.querySelectorAll('[data-mix-day]')].forEach(hit=>{const index=Number(hit.dataset.mixDay);hit.onpointerenter=event=>show(event,index);hit.onpointermove=event=>show(event,index);hit.onpointerleave=hide;});
}
const MODEL_PUSHBACK_PERIODS={today:'1d','7':'7d','30':'30d','90':'90d',month:'month',all:'0'},MODEL_PUSHBACK_TTL_MS=60000,MODEL_PUSHBACK_MAX_MODELS=20;
let modelPushback={key:'',state:'idle',rates:new Map(),at:0,loading:false},modelPushbackRequest=0;
function modelPushbackKey(){return `${modelRange}\u0000${modelProject}`;}
function modelPushbackFor(row){return modelPushback.rates.get(`${row.model}\u0000${row.runtime}`)||null;}
async function loadModelPushback(){
 const key=modelPushbackKey(),period=MODEL_PUSHBACK_PERIODS[modelRange];
 if(modelPushback.key===key&&(modelPushback.loading||modelPushback.state==='unsupported_range'||modelPushback.state==='unsupported'||Date.now()-modelPushback.at<MODEL_PUSHBACK_TTL_MS))return;
 if(!period){modelPushback={key,state:'unsupported_range',rates:new Map(),at:Date.now(),loading:false};return;}
 const request=++modelPushbackRequest,query=new URLSearchParams({months:period});
 if(modelProject)query.set('project',modelProject);
 modelPushback=modelPushback.key===key?{...modelPushback,loading:true}:{key,state:'loading',rates:new Map(),at:0,loading:true};
 let next={key,state:'unavailable',rates:new Map(),at:Date.now(),loading:false};
 try{
  const response=await fetch(`/work?${query}`,{cache:'no-store'}),payload=await response.json();
  if(response.ok&&payload.ok&&payload.insights){
   const models=payload.insights.rework?.models||[];
   next={key,state:payload.settings?.enabled?'ready':'off',rates:new Map(models.map(item=>[`${item.model}\u0000${item.runtime}`,item])),capped:models.length>=MODEL_PUSHBACK_MAX_MODELS,at:Date.now(),loading:false};
  }else if(response.status===404&&!/project/i.test(payload.error||''))next={...next,state:'unsupported'};
 }catch(error){}
 if(request!==modelPushbackRequest)return;
 modelPushback=next;
 if(LATEST&&modelPushbackKey()===key)rerenderActiveModelStats();
}
function modelPushbackCell(row){
 if(!row.variants)return '<td class="num modelMuted"></td>';
 const item=modelPushbackFor(row),span=modelRange==='all'?' · last 24 months':'';
 if(item){
  const tip=`${f(Math.round(item.rate*item.samples))} of ${f(item.samples)} labeled follow-ups pushed back${span}${item.few_samples?' · few samples, treat as a rough signal':''} · local model estimate`;
  return `<td class="num mono${item.few_samples?' modelMuted':''}" title="${esc(tip)}">${pct(item.rate)}${item.few_samples?'*':''}</td>`;
 }
 const state=modelPushback.state;
 const reason=state==='unsupported_range'?'Pushback is not available for Yesterday or Last month. Choose another History range.':state==='unsupported'?'Work insights are not available on this system.':state==='loading'?'Loading pushback.':state==='off'?'Turn on Work insights in Settings to label follow-ups.':state==='unavailable'?'Pushback is unavailable right now. It retries shortly.':modelPushback.capped?`Not among the ${MODEL_PUSHBACK_MAX_MODELS} models with the most labeled follow-ups in this window.`:'No labeled follow-ups for this model in this window.';
 return `<td class="num modelMuted" title="${esc(reason)}">--</td>`;
}
function modelBoardCells(row,color,leaderCost,totalCost){
 const data=row.window,cost=modelBoardValue(row,'cost'),perExec=modelBoardValue(row,'cost_per_exec'),cache=modelBoardValue(row,'cache'),output=modelBoardValue(row,'output'),speed=modelBoardValue(row,'speed'),wait=modelBoardValue(row,'wait'),partial=metricPartial(data,'cost'),covered=Number(data.cost_covered_executions||0),perExecPartial=partial||(covered>0&&covered<Number(data.executions||0)),est=row.runtime==='Cursor'?' est':'';
 const spend=cost===null?`<td class="num modelMuted" title="Cost unavailable for this runtime">--</td>`:`<td class="num modelSpendCell"><span class=modelSpendTrack><i style="width:${leaderCost>0?Math.max(2,100*cost/leaderCost):0}%;background:${color}"></i></span><b class=mono>${money(cost)}${partial?'*':''}</b><small>${totalCost>0?pct(cost/totalCost):''}</small></td>`;
 const speedTip=data.available?`${speedFmt(data.output_tps)} tok/s average · ${data.throughput_basis==='tool_free'?'tool-free':'end-to-end'} timing · ${f(data.throughput_samples)} timed ${countWord(data.throughput_samples,'sample')} · ${pct(data.timing_coverage)} of output covered`:'No attributable output timing in this window.';
 const waitTip=data.wait_samples?`Prompt-to-completed-response wait. ${f(data.wait_samples)} timed ${countWord(data.wait_samples,'request')} · ${waitFmt(data.avg_wait_s)} average · ${waitFmt(data.p95_wait_s)} p95`:'No model-attributable completed waits in this window.';
 return `${spend}<td class="num mono">${perExec===null?'--':money(perExec)+(perExecPartial?'*':'')}</td><td class="num mono">${cache===null?'--':pct(cache)}</td><td class="num mono">${f(data.executions)}</td><td class="num mono">${output===null?'--':compactNumber(output)+est}</td><td class="num mono modelSpeedCell" title="${esc(speedTip)}">${speed===null?'--':speedFmt(speed)+est}</td><td class="num mono" title="${esc(waitTip)}">${wait===null?'--':waitFmt(wait)}</td>${modelPushbackCell(row)}`;
}
function modelBoardTip(row){
 const data=row.window,rowTokens=metricAvailable(data,'tokens'),cursorEstimate=row.runtime==='Cursor',basis=data.throughput_basis==='tool_free'?'tool-free':data.throughput_basis==='end_to_end'?'end-to-end':'unavailable',ttftNote=data.ttft_samples?`${Math.round(data.avg_ttft_ms)}ms avg TTFT`:'TTFT unavailable in these traces';
 const workload=`Typical workload (approximate shape, not semantic difficulty): ${compactNumber(data.median_peak_input_tokens)} median peak context · ${rowTokens?compactNumber(data.median_workload_output_tokens):'--'} median output · ${compactNumber(data.median_tool_calls)} median tools · ${compactNumber(data.median_model_calls)} median model calls${cursorEstimate?' · Cursor input/output are local proxies':''}.`;
 const pace=data.available?`Observed output pace ${speedFmt(data.output_tps)} tok/s (${basis} · ${f(data.throughput_samples)} ${countWord(data.throughput_samples,'sample')} · ${pct(data.timing_coverage)} coverage · ${ttftNote}); diagnostic, not a model verdict.`:'No attributable output timing in this window.';
 return `${row.model} · ${row.runtime}. Input ${rowTokens?compactNumber(data.input_tokens):'--'}. ${workload} ${pace}`;
}
const MODEL_SPEED_EXTRA_COLORS=THEME.modelSpeedExtra;
function modelSpeedBasis(window){return Number(window?.tool_free_samples||0)>0&&Number(window?.tool_free_seconds||0)>0?'tool_free':'timed';}
function modelSpeedSeries(daily,days,basis){
 const outKey=basis==='tool_free'?'tool_free_output_tokens':'timed_output_tokens',secKey=basis==='tool_free'?'tool_free_seconds':'timed_seconds',sampleKey=basis==='tool_free'?'tool_free_samples':'timed_samples';
 const byDay=new Map();
 (daily||[]).forEach(day=>{const target=byDay.get(day.day)||{out:0,sec:0,samples:0};target.out+=Number(day[outKey]||0);target.sec+=Number(day[secKey]||0);target.samples+=Number(day[sampleKey]||0);byDay.set(day.day,target);});
 return days.map((day,index)=>{
  const own=byDay.get(day);if(!own||own.sec<=0||own.out<=0)return null;
  let out=0,sec=0;for(let offset=-1;offset<=1;offset++){const near=byDay.get(days[index+offset]);if(near&&near.sec>0){out+=near.out;sec+=near.sec;}}
  return {index,day,value:sec>0?out/sec:0,raw:own.out/own.sec,samples:own.samples};
 }).filter(Boolean);
}
function modelCalendarDays(values){
 const sorted=[...new Set((values||[]).filter(value=>/^\d{4}-\d{2}-\d{2}$/.test(value)))].sort();
 if(!sorted.length)return [];
 const out=[],cursor=new Date(`${sorted[0]}T12:00:00`),last=sorted[sorted.length-1];
 while(out.length<3700){const key=localDateKey(cursor);out.push(key);if(key>=last)break;cursor.setDate(cursor.getDate()+1);}
 return out;
}
function modelSpeedDefaultKeys(ranking){return ranking.filter(group=>group.window.available).slice(0,5).map(group=>group.key);}
function modelSmoothPath(points){
 if(points.length<2)return '';
 const n=points.length,dx=[],slope=[],tangent=new Array(n).fill(0);
 for(let i=0;i<n-1;i++){dx[i]=points[i+1][0]-points[i][0];slope[i]=dx[i]?(points[i+1][1]-points[i][1])/dx[i]:0;}
 tangent[0]=slope[0];tangent[n-1]=slope[n-2];
 for(let i=1;i<n-1;i++)tangent[i]=slope[i-1]*slope[i]<=0?0:(slope[i-1]+slope[i])/2;
 for(let i=0;i<n-1;i++){if(slope[i]===0){tangent[i]=0;tangent[i+1]=0;continue;}const a=tangent[i]/slope[i],b=tangent[i+1]/slope[i],h=a*a+b*b;if(h>9){const t=3/Math.sqrt(h);tangent[i]=t*a*slope[i];tangent[i+1]=t*b*slope[i];}}
 let d=`M${points[0][0].toFixed(1)},${points[0][1].toFixed(1)}`;
 for(let i=0;i<n-1;i++){const [x0,y0]=points[i],[x1,y1]=points[i+1],third=dx[i]/3;d+=` C${(x0+third).toFixed(1)},${(y0+tangent[i]*third).toFixed(1)} ${(x1-third).toFixed(1)},${(y1-tangent[i+1]*third).toFixed(1)} ${x1.toFixed(1)},${y1.toFixed(1)}`;}
 return d;
}
function renderModelSpeedChart(groups,ranking,top,rangeWindow){
 const svg=$('m-speedchart-svg'),tip=$('m-speedchart-tip'),VW=Math.max(320,Math.round(svg.clientWidth||1040)),VH=Math.max(160,Math.round(svg.clientHeight||250)),ML=56,MR=18,MT=30,MB=34,PW=VW-ML-MR,PH=VH-MT-MB;svg.setAttribute('viewBox',`0 0 ${VW} ${VH}`);
 const timed=ranking.filter(group=>group.window.available),byKey=new Map(timed.map(group=>[group.key,group])),defaults=modelSpeedDefaultKeys(ranking);
 let keys=(modelSpeedSelection||defaults).filter(key=>byKey.has(key)),fallback=false;
 if(modelSpeedSelection&&!keys.length&&defaults.length){keys=defaults;fallback=true;}
 const topColor=modelRankedColors(top);let extra=0;
 const colorFor=key=>topColor.get(key)||MODEL_SPEED_EXTRA_COLORS[(extra++)%MODEL_SPEED_EXTRA_COLORS.length];
 const days=rangeWindow.days||modelCalendarDays(groups.flatMap(group=>group.daily.map(day=>day.day)));
 const series=keys.map(key=>{const group=byKey.get(key);return {key,label:`${group.model} · ${group.runtime}`,color:colorFor(key),estimated:group.estimated,points:modelSpeedSeries(group.daily,days,modelSpeedBasis(group.window))};});
 const custom=Boolean(modelSpeedSelection)&&!fallback;
 $('m-speedchart-summary').textContent=custom?`${f(keys.length)} ${countWord(keys.length,'model')} selected`:'Top 5 by spend';
 $('m-speedchart-reset').disabled=!modelSpeedSelection;
 const atMax=keys.length>=MODEL_SPEED_MAX_SERIES;
 setLogHtml($('m-speedchart-options'),timed.length?timed.map(group=>{const checked=keys.includes(group.key);return `<label class=modelOption title="${esc(group.model)} · ${esc(group.runtime)}"><input type=checkbox data-model-speed-choice="${esc(group.key)}" ${checked?'checked':''} ${!checked&&atMax?'disabled':''}><span>${esc(group.model)} · ${esc(group.runtime)} · ${esc(speedFmt(group.window.output_tps))} tok/s</span></label>`;}).join(''):'<div class="modelEmpty emptyState">No timed models in this window</div>');
 setLogHtml($('m-speedchart-legend'),series.map(item=>`<span class=modelSpeedChip><i style="background:${item.color}"></i>${esc(item.label)}${item.estimated?' <small>est</small>':''}</span>`).join(''));
 $('m-speedchart-note').textContent=`3-day avg output tok/s · ${custom?'your selection':`top ${f(keys.length)} by spend`}${fallback?' · your chosen models have no timing here':''}`;
 const values=series.flatMap(item=>item.points.map(point=>point.value)),max=values.length?Math.max(...values)*1.1:0;
 if(!series.length||max<=0){svg.innerHTML=`<text x="${VW/2}" y="${VH/2}" fill="${CHART.lbl}" text-anchor="middle" font-size="${svgTextPx(svg,VW,VH,13)}">No timed output in this window</text>`;tip.style.display='none';return;}
 const step=PW/Math.max(1,days.length),x=index=>ML+step*(index+.5),y=value=>MT+PH-PH*value/max;
 let html='';
 for(let i=0;i<=4;i++){const yy=MT+PH*i/4;html+=`<line x1="${ML}" x2="${ML+PW}" y1="${yy}" y2="${yy}" stroke="${CHART.grid}" stroke-width="1"/><text x="${ML-8}" y="${yy+4}" text-anchor="end" fill="${CHART.lbl}" font-size="${svgTextPx(svg,VW,VH)}">${esc(speedFmt(max*(1-i/4))==='--'?'0':speedFmt(max*(1-i/4)))}</text>`;}
 series.forEach(item=>{const {segments,bridges}=chartGapRuns(item.points);bridges.forEach(([from,to])=>{html+=chartGapBridge(x(from.index),y(from.value),x(to.index),y(to.value),item.color);});segments.forEach(segment=>{const pts=segment.map(point=>[x(point.index),y(point.value)]);if(pts.length>1)html+=`<path d="${modelSmoothPath(pts)}" fill="none" stroke="${item.color}" stroke-width="2.4" ${item.estimated?'stroke-dasharray="6 4"':''} stroke-linecap="round" stroke-linejoin="round"/>`;else html+=`<circle cx="${pts[0][0].toFixed(1)}" cy="${pts[0][1].toFixed(1)}" r="3.4" fill="${item.estimated?'none':item.color}" stroke="${item.color}" stroke-width="1.6"/>`;});});
 const labelEvery=Math.max(1,Math.ceil(days.length/8));
 days.forEach((day,index)=>{const remaining=days.length-1-index,showLabel=index===days.length-1||(index%labelEvery===0&&remaining>=Math.max(2,Math.ceil(labelEvery/2)));if(showLabel)html+=`<text x="${x(index)}" y="${VH-10}" text-anchor="middle" fill="${CHART.lbl}" font-size="${svgTextPx(svg,VW,VH)}">${esc(day.slice(5))}</text>`;html+=`<rect data-speed-day="${index}" x="${ML+step*index}" y="${MT}" width="${step}" height="${PH}" fill="transparent" pointer-events="all"/>`;});
 html+=`<text x="8" y="12" fill="${CHART.lbl}" font-size="${svgTextPx(svg,VW,VH)}">tok/s</text>`;
 svg.innerHTML=html;
 const hide=()=>{tip.style.display='none';};
 const show=(event,index)=>{
  const rows=series.map(item=>({item,point:item.points.find(point=>point.index===index)})).filter(row=>row.point);
  tip.innerHTML=`<div class=h>${esc(days[index])}</div>`+(rows.length?'':'<div class=r><span>No data</span></div>')+rows.map(({item,point})=>`<div class=r><span><i class=modelSwatch style="background:${item.color}"></i>${esc(item.label)}</span><b>${esc(speedFmt(point.value))} tok/s · ${f(point.samples)} ${countWord(point.samples,'sample')}</b></div>`).join('');
  tip.style.display='block';
  const rect=svg.parentElement.getBoundingClientRect(),pointerX=event.clientX-rect.left,rightX=pointerX+12,leftX=pointerX-tip.offsetWidth-12;
  tip.style.left=(rightX+tip.offsetWidth<=rect.width-8?rightX:Math.max(8,leftX))+'px';tip.style.top=Math.max(8,event.clientY-rect.top-tip.offsetHeight-10)+'px';
 };
 [...svg.querySelectorAll('[data-speed-day]')].forEach(hit=>{const index=Number(hit.dataset.speedDay);hit.onpointerenter=event=>show(event,index);hit.onpointermove=event=>show(event,index);hit.onpointerleave=hide;});
}
function renderModelBoard(groups,ranking,top){
 loadModelPushback();
 const rank=new Map(ranking.map((group,index)=>[group.key,index+1])),topColor=modelRankedColors(top);
 const costs=groups.map(group=>modelBoardValue(group,'cost')).filter(value=>value!==null),leaderCost=Math.max(0,...costs),totalCost=costs.reduce((sum,value)=>sum+value,0);
 [...$('m-table').querySelectorAll('th')].forEach(th=>{const button=th.querySelector('[data-model-sort]');if(!button){th.removeAttribute('aria-sort');return;}const active=button.dataset.modelSort===modelSort.key;th.setAttribute('aria-sort',active?(modelSort.direction==='asc'?'ascending':'descending'):'none');button.classList.toggle('on',active);button.dataset.direction=active?modelSort.direction:'';});
 const html=sortModelBoard(groups).map(group=>{
  const color=topColor.get(group.key)||MODEL_OTHER_COLOR,expandable=group.variants.length>1,expanded=expandable&&modelExpandedGroups.has(group.key),only=group.variants[0],tip=modelBoardTip(group);
  const subline=expandable?`${f(group.variants.length)} reasoning efforts`:`${modelEffortLabel(only)} · ${f(only.logs)} ${countWord(only.logs,'log')}`;
  const toggle=expandable?`<button class=modelExpand type=button data-model-expand="${esc(group.key)}" aria-expanded=${expanded} aria-label="${expanded?'Hide':'Show'} reasoning efforts for ${esc(group.model)} · ${esc(group.runtime)}">${expanded?'▾':'▸'}</button>`:'<span class=modelExpandSpacer></span>';
  let rowHtml=`<tr class=modelGroupRow><td class="modelRankCol mono">${f(rank.get(group.key)||0)}</td><td class="fieldtip modelCellTip modelNameCell" tabindex=0 data-tip="${esc(tip)}" aria-description="${esc(tip)}"><div class=modelNameLine>${toggle}<i class=modelSwatch style="background:${color}"></i><div class=modelName title="${esc(group.model)}">${esc(group.model)}</div></div><div class=modelRuntime>${esc(group.runtime)}${group.estimated?' est':''} · ${esc(subline)}</div></td>${modelBoardCells(group,color,leaderCost,totalCost)}</tr>`;
  if(expanded)rowHtml+=sortModelBoard(group.variants).map(variant=>`<tr class=modelVariantRow><td></td><td class="fieldtip modelCellTip" tabindex=0 data-tip="${esc(modelBoardTip(variant))}" aria-description="${esc(modelBoardTip(variant))}"><div class=modelVariantName>${esc(modelEffortLabel(variant))}<small>${f(variant.logs)} ${countWord(variant.logs,'log')}</small></div></td>${modelBoardCells(variant,color,leaderCost,totalCost)}</tr>`).join('');
  return rowHtml;
 }).join('');
 setLogHtml($('m-table').querySelector('tbody'),html||`<tr><td colspan=10><div class="modelEmpty emptyState">No model activity in this window</div></td></tr>`);
}
const MODEL_FOCUS_KEYS=['modelExpand','modelIds','modelSort','modelMix','modelSpeedChoice'];
function modelFocusKey(){
 const el=document.activeElement;if(!el||!el.dataset||!$('view-models').contains(el))return null;
 const key=MODEL_FOCUS_KEYS.find(name=>name in el.dataset);if(!key)return null;
 const container=el.closest('#m-spend-bar,#m-spend-legend,#m-table,#m-mix-metric,#m-speedchart-options')||$('view-models');
 return {el,key,value:el.dataset[key],containerId:container.id};
}
function restoreModelFocus(focus){
 if(!focus||focus.el.isConnected)return;
 const container=$(focus.containerId)||$('view-models'),attr='data-'+focus.key.replace(/[A-Z]/g,char=>'-'+char.toLowerCase());
 const target=[...container.querySelectorAll(`[${attr}]`)].find(el=>el.dataset[focus.key]===focus.value);
 if(target)target.focus({preventScroll:true});
}
function renderModelStats(stats){
 const focus=modelFocusKey();
 stats=stats||{models:[]};
 const allModels=(stats.models||[]).map(row=>({...row,id:row.id||row.model,runtime:row.runtime||(row.providers||[]).join(' + ')||'unknown runtime'})),names=allModels.map(row=>row.id).sort((a,b)=>a.localeCompare(b));
 modelFilters=migrateModelRuntimeFilters(modelFilters,allModels);saveActiveModelFilters();
 const allSelected=!modelFilters.length,selected=allModels.filter(row=>allSelected||modelFilters.includes(row.id)),rangeWindow=modelRangeWindow(modelRange),options=allModels.slice().sort((a,b)=>modelRuntimeLabel(a).localeCompare(modelRuntimeLabel(b)));
 const modelPickerOptions=`<label class="modelOption all"><input type=checkbox data-model-choice=all ${allSelected?'checked':''}><span>All model runtimes</span></label>`+options.map(row=>`<label class=modelOption title="${esc(modelRuntimeLabel(row))}"><input type=checkbox data-model-choice=model value="${esc(row.id)}" ${modelFilters.includes(row.id)?'checked':''}><span>${esc(modelRuntimeLabel(row))}</span></label>`).join('');
 setLogHtml($('m-model-options'),modelPickerOptions);
 if($('m-model-picker').open)requestAnimationFrame(positionModelPickerMenu);
 const selectionLabel=allSelected?'All model runtimes':selected.length===1?modelRuntimeLabel(selected[0]):`${selected.length} model runtimes selected`;$('m-model-summary').textContent=selectionLabel;$('m-model-summary').title=allSelected?'All model runtimes':selected.map(modelRuntimeLabel).join(', ');$('m-range').value=modelRange;
	 const total=aggregateModelDays(mergeModelDays(selected,rangeWindow)),chart=buildModelTrend(selected,rangeWindow,names);
	 const groups=modelGroupRows(selected,rangeWindow),ranking=modelSpendRanking(groups),top=ranking.slice(0,5);
	 renderModelSpend(groups,ranking,top,allSelected);
	 drawModelMix(groups,top,rangeWindow);
	 renderModelSpeedChart(groups,ranking,top,rangeWindow);
	 renderModelBoard(groups,ranking,top);
	 [...$('m-metric').querySelectorAll('button')].forEach(button=>button.classList.toggle('on',button.dataset.modelMetric===modelTrendMetric));
	 $('m-speed-label').textContent=total.throughput_basis==='tool_free'?'Tool-free output pace':'Observed output pace';
	 setModelKpiValue('m-speed',total.available?speedFmt(total.output_tps)+' tok/s':'--',total.available?estimateSuffix(total):'');
	 const speedDetail=total.available?`${total.throughput_basis==='tool_free'?'Tool-free':'End-to-end'} timing · ${f(total.throughput_samples)} timed ${countWord(total.throughput_samples,'sample')} · ${pct(total.timing_coverage)} output coverage.`:'No attributable timing in this window.';
	 $('m-speed-note').textContent=total.available?`${pct(total.timing_coverage)} output covered`:'Timing unavailable';
	 setModelKpiHelp('m-speed-label',speedDetail);
	 $('m-coverage-bar').style.width=Math.max(0,Math.min(100,100*total.timing_coverage))+'%';
	 $('m-coverage').setAttribute('aria-valuenow',String(Math.round(100*total.timing_coverage)));
	 $('m-wait').textContent=total.wait_samples?waitFmt(total.median_wait_s):'--';
	 const waitDetail=total.wait_samples?`${f(total.wait_samples)} timed · ${waitFmt(total.avg_wait_s)} average · ${waitFmt(total.p95_wait_s)} p95${total.user_pause_seconds?` · ${waitFmt(total.user_pause_seconds)} human pause excluded`:''}.`:'No model-attributable completed waits.';
	 $('m-wait-note').textContent=total.wait_samples?`${f(total.wait_samples)} timed`:'Timing unavailable';
	 setModelKpiHelp('m-wait-label',waitDetail);
	 renderMatchedPace(stats,selected);
	 setModelKpiHelp('m-change-label',$('m-change-note').title||$('m-change-note').textContent);
	 $('m-trend-note').textContent=`Bars: daily output tokens · Lines: ${(MODEL_TREND_METRICS[modelTrendMetric]||MODEL_TREND_METRICS.speed).note} per model · ${modelScopeLabel()} · ${allSelected?'all runtimes':`${f(selected.length)} selected`} · ${modelRangeLabel(modelRange)}`;
	 drawModelTrend(chart);
	 restoreModelFocus(focus);
}

