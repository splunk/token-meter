
const cssVar=name=>getComputedStyle(document.documentElement).getPropertyValue(name).trim();
// viewBox font size that renders at least `px` CSS pixels in a scaled SVG; never shrinks text.
const svgTextPx=(svg,VW,VH,px=11)=>{const box=svg.getBoundingClientRect(),scale=Math.min(box.width/VW||1,box.height/VH||1);return +(px/Math.min(1,scale)).toFixed(1);};
const tint=(name,alpha)=>`rgb(${cssVar(`--${name}-rgb`)}/${alpha})`;
const CHART={};
function readChartTokens(){Object.assign(CHART,{
 ink:cssVar('--bg'),input:cssVar('--c-input'),output:cssVar('--c-output'),tool:cssVar('--toolcall'),side:cssVar('--signal-mid'),
 cacheRead:cssVar('--c-cache-read'),cacheWrite:cssVar('--c-cache-write'),cost:cssVar('--c-cost'),context:cssVar('--c-context'),
 reason:cssVar('--reason'),muted:tint('tint',.26),
 costFill:cssVar('--chart-cost-fill'),
 axis:cssVar('--chart-axis'),grid:cssVar('--chart-grid'),lbl:cssVar('--chart-label'),
 tip:cssVar('--chart-tip'),tipHead:cssVar('--chart-tip-head'),tipLine:cssVar('--chart-tip-line')
});}
readChartTokens();
function scaledY(v,maxV){
 if(maxV<=0)return 1;
 return Math.max(0,v)/maxV;
}
function integerTicks(maxV){
 const top=Math.max(1,Math.ceil(maxV||1));
 if(top<=6) return Array.from({length:top+1},(_,i)=>i);
 const raw=top/4, pow=10**Math.floor(Math.log10(raw));
 const n=raw/pow;
 const step=(n<=1?1:n<=2?2:n<=5?5:10)*pow;
 const hi=Math.ceil(top/step)*step, out=[];
 for(let v=0;v<=hi;v+=step) out.push(v);
 return out;
}
function ticks(maxV){
 return [0,.25,.5,.75,1].map(x=>x*maxV);
}
function moneyTick(n){
 return money(n);
}
function tipAttrs(text){
 const safe=esc(text);
 return `class=fieldtip tabindex=0 aria-description="${safe}" data-tip="${safe}"`;
}
function legendField(label,color,tip){
 return `<span ${tipAttrs(tip)}><i class=dot style="background:${color}"></i>${esc(label)}</span>`;
}
function chartLegendHtml(mode,cumulative=false){
 const cursor=CURRENT?.provider==='cursor';
 const cumulativeLabel=cumulative?'cumulative ':'';
 const cumulativeTip=cumulative?' Running total through each execution.':'';
 const eventMarks=[
  legendField('user message','var(--fg)','Execution points with a captured user message preview. The diamond marker sits on the active chart series.'),
  legendField('notable tools','var(--toolcall)','Executions with unusually high tool-call count for this log/thread.'),
  legendField('notable reasoning','var(--reason)','Executions with unusually high reasoning tokens for this log/thread.'),
  legendField('coordination','var(--coord)','Subagent or coordination executions when the log marks them separately.'),
 ].join('');
 if(mode==='context') return [
  legendField('context','var(--c-context)','Input tokens as a share of the model context window for each execution.'),
  legendField('70% watch','var(--warn)','Watch threshold: context is getting large enough to consider summarizing or narrowing tool output.'),
  legendField('85% high','var(--bad)','High threshold: context is close to the model window limit.'),
 ].join('')+eventMarks;
 if(mode==='tokens') return [
  legendField(cumulativeLabel+(cursor?'input context est':'input total'),'var(--c-input)',(cursor?'One trace-visible context snapshot per Cursor execution; this is a local proxy, not billable input.':'Total input/context tokens for each execution, including uncached input and prompt-cache tokens.')+cumulativeTip),
  legendField(cumulativeLabel+(cursor?'visible output est':'output'),'var(--c-output)',(cursor?'Model-authored text persisted in Cursor bubbles, estimated at four characters per token.':'Model output tokens produced by the execution.')+cumulativeTip),
  legendField(cumulativeLabel+'cache read','var(--c-cache-read)','Input tokens served from the provider prompt cache. These are usually cheaper than uncached input.'+cumulativeTip),
  legendField(cumulativeLabel+'cache write','var(--c-cache-write)','Input tokens written into the provider prompt cache for possible reuse later. Some providers may report zero.'+cumulativeTip),
  legendField(cumulativeLabel+'uncached input','var(--c-input)','Input tokens charged as normal input because they were new or did not hit prompt cache.'+cumulativeTip),
 ].join('')+eventMarks;
 if(mode==='tools') return [
  legendField('tool calls','var(--toolcall)','Number of tool calls observed in each execution.'),
 ].join('')+eventMarks;
 if(mode==='steps') return [
  legendField('steps','var(--toolcall)','Number of executions grouped under each captured user message.'),
  legendField('request cost','var(--c-cost)','Total estimated cost for the executions in that request.'),
  legendField('request message','var(--fg)','The user message that started the request, shown in hover.'),
 ].join('');
 if(mode==='wait') return [
  legendField('wait time','var(--accent)','Wall-clock time from the user prompt until the completed response, including reasoning and tool use.'),
  legendField('request message','var(--fg)','The captured user message associated with the timed request, when available.'),
 ].join('');
 if(mode==='cost') return legendField(cumulative?(cursor?'cumulative local cost est':'cumulative cost'):(cursor?'local execution estimate':'execution cost'),'var(--c-cost)',(cursor?'Context-and-visible-output proxy priced with the persisted model variant; cache and hidden model work are excluded.':'Estimated USD cost for this execution using the configured public API rates.')+cumulativeTip)+eventMarks;
 return chartLegendHtml('context');
}
function chartParts(s){
 let read=+(s.cache_read||0), write=+(s.cache_write||0);
 const legacyCache=+(s.cache||0);
 if(!read&&!write&&legacyCache) read=legacyCache;
 const cache=read+write, rawFresh=s.fresh_input;
 const fresh=Number.isFinite(+rawFresh)?Math.max(0,+rawFresh):Math.max(0,+(s.in||0)-cache);
 const total=Math.max(+(s.in||0),fresh+cache);
 const ctx=s.context_pct===undefined||s.context_pct===null?null:+s.context_pct;
 return {fresh,read,write,cache,total,out:+(s.out||0),cost:+(s.cost||0),context:ctx,reason:+(s.reasoning||0),tools:+(s.tools||0)};
}
function cumulativeChartParts(parts,mode){
 const keys=mode==='cost'?['cost']:['fresh','read','write','cache','total','out'];
 const sums=Object.fromEntries(keys.map(key=>[key,0]));
 return parts.map(part=>{
  keys.forEach(key=>{sums[key]+=Math.max(0,Number(part[key]||0));});
  return {...part,...sums};
 });
}
function requestStepGroups(series){
 const groups=[];
 let cur=null;
 (series||[]).forEach((s,idx)=>{
  const msg=String(s.user_message||s.user_input||'').trim();
  const exec=s.i??idx+1;
  if(msg||!cur){
   cur={i:groups.length+1,message:msg||'No user message captured',hasMessage:!!msg,start:exec,end:exec,steps:0,cost:0,input:0,output:0,tools:0,peakContext:null};
   groups.push(cur);
  }
  const p=chartParts(s);
  cur.steps++;
  cur.end=exec;
  cur.cost+=p.cost;
  cur.input+=p.total;
  cur.output+=p.out;
  cur.tools+=p.tools;
  if(p.context!==null&&Number.isFinite(p.context)) cur.peakContext=Math.max(cur.peakContext??0,p.context);
 });
 return groups;
}
function drawStepsChart(series){
 const svg=$('iochart'), tip=$('chart-tip'), VW=Math.max(320,Math.round(svg.clientWidth||1040)), VH=Math.max(160,Math.round(svg.clientHeight||244)), MR=16, MT=14, MB=42;
 svg.setAttribute('viewBox',`0 0 ${VW} ${VH}`);
 const groups=requestStepGroups(series);
 if(!groups.length){svg.innerHTML='<text x='+Math.round(VW/2)+' y='+Math.round(VH/2)+' text-anchor=middle fill="'+CHART.lbl+'">no user requests yet</text>'; if(tip)tip.style.display='none'; return;}
 const n=groups.length, rawMax=Math.max(1,...groups.map(g=>g.steps)), tickVals=integerTicks(rawMax), maxV=tickVals[tickVals.length-1];
 const fmtY=v=>f(v), widestTick=tickVals.reduce((a,v)=>Math.max(a,String(fmtY(v)).length),0);
 const ML=Math.min(98,Math.max(58,widestTick*7+18)), PW=VW-ML-MR, PH=VH-MT-MB;
 const x=i=>ML+(n<=1?PW/2:i/(n-1)*PW);
 const y=v=>MT+PH-(Math.max(0,v)/maxV)*PH;
 const T=`font:${svgTextPx(svg,VW,VH)}px var(--font-mono);fill:${CHART.lbl}`;
 let grid='',ylbl='';
 tickVals.forEach(v=>{ const yy=y(v).toFixed(1); grid+=`<line x1=${ML} y1=${yy} x2=${VW-MR} y2=${yy} stroke="${CHART.grid}" stroke-dasharray="3 4"/>`; ylbl+=`<text x=${ML-8} y=${(+yy+4).toFixed(1)} text-anchor=end style="${T}">${fmtY(v)}</text>`; });
 const axes=`<line x1=${ML} y1=${MT} x2=${ML} y2=${MT+PH} stroke="${CHART.axis}"/><line x1=${ML} y1=${MT+PH} x2=${VW-MR} y2=${MT+PH} stroke="${CHART.axis}"/>`;
 let xlbl=''; [0,n-1,Math.floor((n-1)/2),Math.floor((n-1)/4),Math.floor(3*(n-1)/4)].forEach(i=>{ if(i>=0&&i<n) xlbl+=`<text x=${x(i).toFixed(1)} y=${VH-16} text-anchor=middle style="${T}">#${groups[i].i}</text>`; });
 const bw=Math.max(8,Math.min(34,PW/Math.max(1,n)/1.8));
 const bars=groups.map((g,i)=>{ const yy=y(g.steps), h=MT+PH-yy; return `<rect x="${(x(i)-bw/2).toFixed(1)}" y="${yy.toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(1,h).toFixed(1)}" rx="3" fill="${tint('signal',.28)}" stroke="${CHART.tool}" stroke-width="1"/>`; }).join('');
 let d=''; groups.forEach((g,i)=>{ d+=(i?' L':'M')+x(i).toFixed(1)+' '+y(g.steps).toFixed(1); });
 const line=d?`<path d="${d}" fill="none" stroke="${CHART.tool}" stroke-width="2.6" stroke-linejoin="round" stroke-linecap="round"/>`:'';
 const costMax=Math.max(.001,...groups.map(g=>g.cost));
 const costMarks=groups.map((g,i)=>{ const h=4+Math.min(14,(g.cost/costMax)*14); return `<circle cx="${x(i).toFixed(1)}" cy="${(MT+PH+7).toFixed(1)}" r="${h/2}" fill="${CHART.cost}" opacity=".78"/>`; }).join('');
 const titles=`<text x=${(ML+PW/2).toFixed(1)} y=${VH-1} text-anchor=middle style="${T}">user request</text><text x=14 y=${(MT+PH/2).toFixed(1)} text-anchor=middle transform="rotate(-90 14 ${(MT+PH/2).toFixed(1)})" style="${T}">steps</text>`;
 const hover=`<rect id=ch-hit x=${ML} y=${MT} width=${PW} height=${PH} fill="transparent" pointer-events="all"/><g id=chart-hover opacity=0 style="pointer-events:none"><line id=ch-x y1=${MT} y2=${MT+PH} stroke="${CHART.tipLine}" stroke-dasharray="4 4"/><circle id=ch-a r=4.8 fill="${CHART.tool}" stroke="${CHART.ink}" stroke-width=2/></g>`;
 svg.innerHTML=grid+axes+ylbl+xlbl+bars+line+costMarks+titles+hover;
 const hoverGroup=svg.querySelector('#chart-hover'), hit=svg.querySelector('#ch-hit');
 const setAttr=(id,attrs)=>{const el=svg.querySelector(id); Object.entries(attrs).forEach(([k,v])=>el.setAttribute(k,v));};
 function hoverAt(clientX,clientY){
  const pt=svg.createSVGPoint(); pt.x=clientX; pt.y=clientY;
  const curPt=pt.matrixTransform(svg.getScreenCTM().inverse());
  const idx=Math.max(0,Math.min(n-1,Math.round((curPt.x-ML)/PW*(n-1))));
  const g=groups[idx], xx=x(idx), yy=y(g.steps);
  hoverGroup.setAttribute('opacity','1');
  setAttr('#ch-x',{x1:xx.toFixed(1),x2:xx.toFixed(1)});
  setAttr('#ch-a',{cx:xx.toFixed(1),cy:yy.toFixed(1),opacity:1});
  if(tip){
   const sx=svg.clientWidth/VW, sy=svg.clientHeight/VH, w=360, h=244;
   const left=Math.min(Math.max(xx*sx+14,8),Math.max(8,svg.clientWidth-w-8));
   const top=Math.min(Math.max(yy*sy-108,8),Math.max(8,svg.clientHeight-h-8));
   tip.style.display='block'; tip.style.left=left+'px'; tip.style.top=top+'px';
   const row=(cls,name,val)=>`<div class="r ${cls}"><span>${name}</span><b>${val}</b></div>`;
   const execRange=g.start===g.end?`#${g.start}`:`#${g.start}-${g.end}`;
   const messageBlock=g.hasMessage?`<div class=prompt><span>user message</span><b>${esc(g.message)}</b></div>`:'';
   const est=CURRENT?.provider==='cursor'?' est':'';
   tip.innerHTML=`<div class=h>request #${g.i}</div>${row('tools','steps',f(g.steps))}${row('in','executions',execRange)}${row('cost','cost',metricAvailable(CURRENT,'cost')?money(g.cost)+est:'--')}${row('in',est?'input context':'input total',metricAvailable(CURRENT,'input_tokens')?f(g.input)+est:'--')}${row('out',est?'visible output':'output',metricAvailable(CURRENT,'output_tokens')?f(g.output)+est:'--')}${row('tools','tool calls',f(g.tools))}${row('context','peak context',g.peakContext===null?'--':pct(g.peakContext))}${messageBlock}`;
  }
 }
 hit.addEventListener('mousemove',e=>hoverAt(e.clientX,e.clientY));
 hit.addEventListener('mouseleave',()=>{hoverGroup.setAttribute('opacity','0'); if(tip)tip.style.display='none';});
 hit.addEventListener('touchmove',e=>{ if(e.touches[0]) hoverAt(e.touches[0].clientX,e.touches[0].clientY); },{passive:true});
}
function drawWaitChart(samples,series){
 const svg=$('iochart'),tip=$('chart-tip'),VW=Math.max(320,Math.round(svg.clientWidth||1040)),VH=Math.max(160,Math.round(svg.clientHeight||244)),MR=16,MT=14,MB=42;
 svg.setAttribute('viewBox',`0 0 ${VW} ${VH}`);
 samples=samples||[];
 if(!samples.length){svg.innerHTML='<text x='+Math.round(VW/2)+' y='+Math.round(VH/2)+' text-anchor=middle fill="'+CHART.lbl+'">no completed wait timing yet</text>';if(tip)tip.style.display='none';return;}
 const groups=requestStepGroups(series||[]),n=samples.length,rawMax=Math.max(1,...samples.map(row=>Number(row.duration_s||0))),tickVals=ticks(rawMax),maxV=rawMax;
 const fmtY=value=>waitFmt(value),widestTick=tickVals.reduce((width,value)=>Math.max(width,fmtY(value).length),0);
 const ML=Math.min(110,Math.max(62,widestTick*7+18)),PW=VW-ML-MR,PH=VH-MT-MB;
 const x=index=>ML+(n<=1?PW/2:index/(n-1)*PW),y=value=>MT+PH-PH*(Math.max(0,Number(value||0))/maxV);
 const T=`font:${svgTextPx(svg,VW,VH)}px var(--font-mono);fill:${CHART.lbl}`;
 let grid='',labels='';
 tickVals.forEach(value=>{const yy=y(value).toFixed(1);grid+=`<line x1=${ML} y1=${yy} x2=${VW-MR} y2=${yy} stroke="${CHART.grid}" stroke-dasharray="3 4"/>`;labels+=`<text x=${ML-8} y=${(+yy+4).toFixed(1)} text-anchor=end style="${T}">${fmtY(value)}</text>`;});
 const axes=`<line x1=${ML} y1=${MT} x2=${ML} y2=${MT+PH} stroke="${CHART.axis}"/><line x1=${ML} y1=${MT+PH} x2=${VW-MR} y2=${MT+PH} stroke="${CHART.axis}"/>`;
 const barWidth=Math.max(8,Math.min(34,PW/Math.max(1,n)/1.8));
 const bars=samples.map((row,index)=>{const yy=y(row.duration_s),height=MT+PH-yy;return `<rect x="${(x(index)-barWidth/2).toFixed(1)}" y="${yy.toFixed(1)}" width="${barWidth.toFixed(1)}" height="${Math.max(1,height).toFixed(1)}" rx="3" fill="${tint('signal',.28)}" stroke="${CHART.context}" stroke-width="1.2"/>`;}).join('');
 const line=samples.length>1?`<path d="${samples.map((row,index)=>`${index?'L':'M'}${x(index).toFixed(1)},${y(row.duration_s).toFixed(1)}`).join(' ')}" fill="none" stroke="${CHART.context}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>`:'';
 const every=Math.max(1,Math.ceil(n/8));
 let xlabels='';samples.forEach((row,index)=>{if(index===n-1||index%every===0)xlabels+=`<text x=${x(index).toFixed(1)} y=${VH-16} text-anchor=middle style="${T}">#${index+1}</text>`;});
 const titles=`<text x=${(ML+PW/2).toFixed(1)} y=${VH-1} text-anchor=middle style="${T}">user request</text><text x=14 y=${(MT+PH/2).toFixed(1)} text-anchor=middle transform="rotate(-90 14 ${(MT+PH/2).toFixed(1)})" style="${T}">wait time</text>`;
 const hover=`<rect id=ch-hit x=${ML} y=${MT} width=${PW} height=${PH} fill="transparent" pointer-events="all"/><g id=chart-hover opacity=0 style="pointer-events:none"><line id=ch-x y1=${MT} y2=${MT+PH} stroke="${CHART.tipLine}" stroke-dasharray="4 4"/><circle id=ch-a r=4.8 fill="${CHART.context}" stroke="${CHART.ink}" stroke-width=2/></g>`;
 svg.innerHTML=grid+axes+labels+xlabels+bars+line+titles+hover;
 const hoverGroup=svg.querySelector('#chart-hover'),hit=svg.querySelector('#ch-hit'),cross=svg.querySelector('#ch-x'),dot=svg.querySelector('#ch-a');
 function hoverAt(clientX,clientY){
  const point=svg.createSVGPoint();point.x=clientX;point.y=clientY;
  const local=point.matrixTransform(svg.getScreenCTM().inverse()),index=Math.max(0,Math.min(n-1,Math.round((local.x-ML)/PW*(n-1))));
  const row=samples[index],xx=x(index),yy=y(row.duration_s),group=groups[index];
  hoverGroup.setAttribute('opacity','1');cross.setAttribute('x1',xx);cross.setAttribute('x2',xx);dot.setAttribute('cx',xx);dot.setAttribute('cy',yy);
  if(tip){const rect=svg.parentElement.getBoundingClientRect(),message=group?.hasMessage?`<div class=prompt><span>user message</span><b>${esc(group.message)}</b></div>`:'',cursor=CURRENT?.provider==='cursor';tip.innerHTML=`<div class=h>request #${index+1}</div><div class="r context"><span>wait time</span><b>${waitFmt(row.duration_s)}</b></div><div class=r><span>model</span><b>${esc(row.model||'unknown')}</b></div><div class="r tools"><span>tool calls</span><b>${f(row.tool_calls)}</b></div><div class="r out"><span>${cursor?'visible output est':'output'}</span><b>${metricAvailable(CURRENT,'output_tokens')?f(row.output_tokens)+' tok'+(cursor?' est':''):'--'}</b></div><div class=r><span>timing</span><b>${esc(row.timing_basis||'observed')}</b></div>${message}`;tip.style.display='block';tip.style.left=Math.min(rect.width-tip.offsetWidth-8,Math.max(8,clientX-rect.left+12))+'px';tip.style.top=Math.max(8,clientY-rect.top-tip.offsetHeight-10)+'px';}
 }
 hit.addEventListener('mousemove',event=>hoverAt(event.clientX,event.clientY));
 hit.addEventListener('mouseleave',()=>{hoverGroup.setAttribute('opacity','0');if(tip)tip.style.display='none';});
 hit.addEventListener('touchmove',event=>{if(event.touches[0])hoverAt(event.touches[0].clientX,event.touches[0].clientY);},{passive:true});
}
function drawIO(series){
 const svg=$('iochart'), VW=Math.max(320,Math.round(svg.clientWidth||1040)), VH=Math.max(160,Math.round(svg.clientHeight||244)), MR=16, MT=14, MB=42;
 svg.setAttribute('viewBox',`0 0 ${VW} ${VH}`);
 const tip=$('chart-tip');
 const mode=CHART_MODES.includes(chartMode)?chartMode:'context';
 const cumulativeTokens=mode==='tokens'&&chartScale==='cumulative';
 const cumulativeCost=mode==='cost'&&chartScale==='cumulative';
 const cumulativeValues=cumulativeTokens||cumulativeCost;
 const legend=$('chart-legend'); if(legend) legend.innerHTML=chartLegendHtml(mode,cumulativeValues);
 const profileHint=$('exec-profile-hint'), activity=CURRENT?.tools?.activity||{};
 const executionCoverage=CURRENT?.execution_coverage||{},hermesAggregate=executionCoverage.basis==='session_aggregate';
 if(profileHint) profileHint.textContent=hermesAggregate
  ? `${f(executionCoverage.recorded_executions||0)} recorded executions · one Hermes session aggregate · per-execution token breakdown unavailable`
  : mode==='tools'
  ? `${f(activity.total_calls)} total calls · ${f(activity.observed_unique)} observed types · peak ${f(activity.peak_calls_per_execution)}/execution`
  : mode==='wait'
   ? `${f(CURRENT?.wait_time?.sample_count||0)} completed timed ${countWord(CURRENT?.wait_time?.sample_count||0,'request')} · lower is better`
   : cumulativeCost
    ? 'running estimated cost through each execution'
   : cumulativeTokens
    ? 'running token totals through each execution'
   : 'per execution or request';
 if(mode==='wait'){drawWaitChart(CURRENT?.wait_time?.samples||[],series);return;}
 const n=series.length; if(!n){svg.innerHTML='<text x='+Math.round(VW/2)+' y='+Math.round(VH/2)+' text-anchor=middle fill="'+CHART.lbl+'">no executions yet</text>'; if(tip)tip.style.display='none'; return;}
 if(mode==='steps'){ drawStepsChart(series); return; }
 if((mode==='tokens'&&!metricAvailable(CURRENT,'tokens'))||(mode==='cost'&&!metricAvailable(CURRENT,'cost'))){svg.innerHTML='<text x='+Math.round(VW/2)+' y='+Math.round(VH/2)+' text-anchor=middle fill="'+CHART.lbl+'">'+(mode==='cost'?'cost estimate':'token evidence')+' unavailable in this trace</text>';if(tip)tip.style.display='none';return;}
 const rawParts=series.map(chartParts), parts=cumulativeValues?cumulativeChartParts(rawParts,mode):rawParts;
 const maxCache=Math.max(10,...parts.map(p=>Math.max(p.total,p.cache,p.fresh,p.out)));
 const maxCost=Math.max(.001,...parts.map(p=>p.cost));
 const maxTools=Math.max(1,...parts.map(p=>p.tools));
 const rawMaxV=mode==='cost'?maxCost:(mode==='context'?1:(mode==='tools'?maxTools:maxCache));
 const tickVals=mode==='tools'?integerTicks(rawMaxV):ticks(rawMaxV);
 const maxV=mode==='tools'?tickVals[tickVals.length-1]:rawMaxV;
 const fmtY=v=>mode==='cost'?moneyTick(v):(mode==='context'?pct(v):f(v));
 const widestTick=tickVals.reduce((a,v)=>Math.max(a,String(fmtY(v)).length),0);
 const ML=Math.min(98,Math.max(58,widestTick*7+18)), PW=VW-ML-MR, PH=VH-MT-MB;
 const x=i=>ML+(n<=1?PW/2:i/(n-1)*PW);
 const y=v=>MT+PH-scaledY(v,maxV)*PH;
 const T=`font:${svgTextPx(svg,VW,VH)}px var(--font-mono);fill:${CHART.lbl}`;
 let grid='',ylbl='';
 tickVals.forEach(v=>{ const yy=y(v).toFixed(1); grid+=`<line x1=${ML} y1=${yy} x2=${VW-MR} y2=${yy} stroke="${CHART.grid}" stroke-dasharray="3 4"/>`; ylbl+=`<text x=${ML-8} y=${(+yy+4).toFixed(1)} text-anchor=end style="${T}">${fmtY(v)}</text>`; });
 const axes=`<line x1=${ML} y1=${MT} x2=${ML} y2=${MT+PH} stroke="${CHART.axis}"/><line x1=${ML} y1=${MT+PH} x2=${VW-MR} y2=${MT+PH} stroke="${CHART.axis}"/>`;
 const xTickIndexes=[...new Set([0,n-1,Math.floor((n-1)/2),Math.floor((n-1)/4),Math.floor(3*(n-1)/4)])];
 let xlbl=''; xTickIndexes.forEach(i=>{ if(i>=0&&i<n) xlbl+=`<text x=${x(i).toFixed(1)} y=${VH-16} text-anchor=middle style="${T}">${series[i].i}</text>`; });
 const lineVals=(vals,col,w=2.4,dash='')=>{
  let d='',open=false;
  const validPoints=[];
  vals.forEach((v,i)=>{ if(v===null||v===undefined||Number.isNaN(v)){open=false;return;} validPoints.push([i,v]); d+=(open?' L':'M')+x(i).toFixed(1)+' '+y(v).toFixed(1); open=true; });
  const line=d?`<path d="${d}" fill="none" stroke="${col}" stroke-width="${w}" stroke-linejoin="round" stroke-linecap="round"${dash?` stroke-dasharray="${dash}"`:''}/>`:'';
  const singlePoint=validPoints.length===1?`<circle cx="${x(validPoints[0][0]).toFixed(1)}" cy="${y(validPoints[0][1]).toFixed(1)}" r="3.8" fill="${col}" stroke="${CHART.ink}" stroke-width="1.6"/>`:'';
  return line+singlePoint;
 };
 const band=(top,bottom,col,opacity)=>{
  let d='';
  top.forEach((v,i)=>{ d+=(i?' L':'M')+x(i).toFixed(1)+' '+y(v||0).toFixed(1); });
  for(let i=bottom.length-1;i>=0;i--) d+=' L'+x(i).toFixed(1)+' '+y(bottom[i]||0).toFixed(1);
  return `<path d="${d} Z" fill="${col}" fill-opacity="${opacity}"/>`;
 };
 const bars=(vals,col)=>{
  const bw=Math.max(2,Math.min(22,PW/Math.max(1,n)/1.85));
  return vals.map((v,i)=>{ const yy=y(v||0), h=MT+PH-yy; return `<rect x="${(x(i)-bw/2).toFixed(1)}" y="${yy.toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(1,h).toFixed(1)}" rx="2" fill="${col}"/>`; }).join('');
 };
 const zeros=parts.map(()=>0), fresh=parts.map(p=>p.fresh), inputTotals=parts.map(p=>p.total);
 const cacheRead=parts.map(p=>p.read), cacheTop=parts.map(p=>p.cache), output=parts.map(p=>p.out), costs=parts.map(p=>p.cost), contexts=parts.map(p=>p.context), toolCalls=parts.map(p=>p.tools);
 const nonZeroSorted=(vals)=>vals.filter(v=>v>0).sort((a,b)=>a-b);
 const percentile=(vals,q,fallback=0)=>{ vals=nonZeroSorted(vals); return vals.length?vals[Math.min(vals.length-1,Math.floor((vals.length-1)*q))]:fallback; };
 const toolCut=Math.max(3,percentile(rawParts.map(p=>p.tools),.85,0));
 const reasonCut=Math.max(1000,percentile(rawParts.map(p=>p.reason),.85,0));
 const primaryFor=p=>{
  if(mode==='tokens') return {value:p.total,color:CHART.input};
  if(mode==='cost') return {value:p.cost,color:CHART.cost};
  if(mode==='context') return {value:p.context,color:CHART.context};
  if(mode==='tools') return {value:p.tools,color:CHART.tool};
  return {value:p.total,color:CHART.input};
 };
 let plot='';
 if(mode==='context'){
  plot+=`<rect x="${ML}" y="${y(1).toFixed(1)}" width="${PW}" height="${(y(.85)-y(1)).toFixed(1)}" fill="${tint('red',.08)}"/><rect x="${ML}" y="${y(.85).toFixed(1)}" width="${PW}" height="${(y(.7)-y(.85)).toFixed(1)}" fill="${tint('orange',.07)}"/>`;
  plot+=`<line x1="${ML}" y1="${y(.85).toFixed(1)}" x2="${VW-MR}" y2="${y(.85).toFixed(1)}" stroke="${CHART.cost}" stroke-dasharray="5 5" opacity=".75"/><line x1="${ML}" y1="${y(.7).toFixed(1)}" x2="${VW-MR}" y2="${y(.7).toFixed(1)}" stroke="${CHART.cacheWrite}" stroke-dasharray="5 5" opacity=".75"/>`;
  plot+=contexts.some(v=>v!==null)?lineVals(contexts,CHART.context,2.7):`<text x=${Math.round(VW/2)} y=${Math.round(VH/2)} text-anchor=middle fill="${CHART.lbl}">context data unavailable</text>`;
 }else if(mode==='tokens'){
  plot+=band(cacheRead,zeros,CHART.cacheRead,.18)+band(cacheTop,cacheRead,CHART.cacheWrite,.18);
  plot+=lineVals(inputTotals,CHART.input,2.7)+lineVals(output,CHART.output,2.1)+lineVals(cacheTop,CHART.cacheRead,1.7,'4 4')+lineVals(fresh,CHART.input,1.5,'5 4');
 }else if(mode==='tools'){
  plot+=bars(toolCalls,tint('signal',.24))+lineVals(toolCalls,CHART.tool,2.6);
 }else{
  plot+=bars(costs,CHART.costFill)+lineVals(costs,CHART.cost,2.4);
 }
 let marks='';
 series.forEach((s,i)=>{
  const p=parts[i], xx=x(i), y0=MT+PH+6;
  if(p.tools>=toolCut) marks+=`<line x1="${(xx-3).toFixed(1)}" y1="${(y0-7).toFixed(1)}" x2="${(xx-3).toFixed(1)}" y2="${y0.toFixed(1)}" stroke="${CHART.tool}" stroke-width="2.2" stroke-linecap="round" opacity=".75"/>`;
  if(p.reason>=reasonCut) marks+=`<line x1="${(xx+3).toFixed(1)}" y1="${(y0-7).toFixed(1)}" x2="${(xx+3).toFixed(1)}" y2="${y0.toFixed(1)}" stroke="${CHART.reason}" stroke-width="2.2" stroke-linecap="round" opacity=".75"/>`;
  if(s.side) marks+=`<circle cx="${xx.toFixed(1)}" cy="${(y0-3).toFixed(1)}" r="3" fill="${CHART.side}" opacity=".85"/>`;
  if(s.user_message||s.user_input){
   const u=primaryFor(p);
   if(u.value!==null&&u.value!==undefined&&!Number.isNaN(u.value)){
    const yy=y(u.value);
    marks+=`<path d="M ${xx.toFixed(1)} ${(yy-5).toFixed(1)} L ${(xx+5).toFixed(1)} ${yy.toFixed(1)} L ${xx.toFixed(1)} ${(yy+5).toFixed(1)} L ${(xx-5).toFixed(1)} ${yy.toFixed(1)} Z" fill="${CHART.tip}" stroke="${u.color}" stroke-width="2" opacity=".96"/>`;
   }
  }
 });
 const yTitle=mode==='cost'?(cumulativeCost?'cumulative usd':'usd'):(mode==='context'?'context':(mode==='tools'?'tool calls':(cumulativeTokens?'cumulative tokens':'tokens')));
 const titles=`<text x=${(ML+PW/2).toFixed(1)} y=${VH-1} text-anchor=middle style="${T}">execution</text><text x=14 y=${(MT+PH/2).toFixed(1)} text-anchor=middle transform="rotate(-90 14 ${(MT+PH/2).toFixed(1)})" style="${T}">${yTitle}</text>`;
 const hover=`<rect id=ch-hit x=${ML} y=${MT} width=${PW} height=${PH} fill="transparent" pointer-events="all"/><g id=chart-hover opacity=0 style="pointer-events:none">
  <line id=ch-x y1=${MT} y2=${MT+PH} stroke="${CHART.tipLine}" stroke-dasharray="4 4"/>
  <circle id=ch-a r=4.8 fill="${CHART.input}" stroke="${CHART.ink}" stroke-width=2/>
  <circle id=ch-b r=4.4 fill="${CHART.output}" stroke="${CHART.ink}" stroke-width=2/>
 </g>`;
 svg.innerHTML=grid+axes+ylbl+xlbl+plot+marks+titles+hover;
 const hoverGroup=svg.querySelector('#chart-hover'), hit=svg.querySelector('#ch-hit');
 const setAttr=(id,attrs)=>{const el=svg.querySelector(id); Object.entries(attrs).forEach(([k,v])=>el.setAttribute(k,v));};
 function hoverAt(clientX,clientY){
  const pt=svg.createSVGPoint(); pt.x=clientX; pt.y=clientY;
  const cur=pt.matrixTransform(svg.getScreenCTM().inverse());
  const idx=Math.max(0,Math.min(n-1,Math.round((cur.x-ML)/PW*(n-1))));
  const s=series[idx], p=parts[idx], xx=x(idx);
  const primaryInfo=primaryFor(p);
  let primary=primaryInfo.value, primaryColor=primaryInfo.color, secondary=p.out, secondaryColor=CHART.output;
  if(mode!=='tokens') secondary=null;
  const yPrimary=primary===null||primary===undefined?MT+PH:y(primary), ySecondary=secondary===null||secondary===undefined?null:y(secondary);
  hoverGroup.setAttribute('opacity','1');
  setAttr('#ch-x',{x1:xx.toFixed(1),x2:xx.toFixed(1)});
  setAttr('#ch-a',{cx:xx.toFixed(1),cy:yPrimary.toFixed(1),fill:primaryColor,opacity:primary===null||primary===undefined?0:1});
  setAttr('#ch-b',{cx:xx.toFixed(1),cy:(ySecondary??yPrimary).toFixed(1),fill:secondaryColor,opacity:ySecondary===null?0:1});
  if(tip){
   const sx=svg.clientWidth/VW, sy=svg.clientHeight/VH, w=360, h=272;
   const left=Math.min(Math.max(xx*sx+14,8),Math.max(8,svg.clientWidth-w-8));
   const top=Math.min(Math.max(Math.min(yPrimary,ySecondary??yPrimary)*sy-112,8),Math.max(8,svg.clientHeight-h-8));
   tip.style.display='block';
   tip.style.left=left+'px';
   tip.style.top=top+'px';
   const row=(cls,name,val)=>`<div class="r ${cls}"><span>${name}</span><b>${val}</b></div>`;
   const userMessage=s.user_message||s.user_input||'';
   const markers=[userMessage?'user message':null,p.tools?`${f(p.tools)} tool calls`:null,p.reason?`${f(p.reason)} reasoning`:null,s.side?'coordination':null].filter(Boolean).join(' / ');
   const prompt=userMessage?esc(userMessage):'--';
   const promptBlock=userMessage?`<div class=prompt><span>user message</span><b>${prompt}</b></div>`:'';
   const cursor=CURRENT?.provider==='cursor',est=cursor?' est':'';
   const costLabel=cumulativeCost?'cumulative cost':'cost';
   const common=[
    row('context','context',p.context===null?'--':pct(p.context)),
    row('cost',costLabel,metricAvailable(CURRENT,'cost')?money(p.cost)+est:'--'),
   ];
   const tokenPrefix=cumulativeTokens?'cumulative ':'';
   const tokenRows=[
    row('in',tokenPrefix+(cursor?'input context':'input total'),metricAvailable(CURRENT,'input_tokens')?f(p.total)+est:'--'),
    row('out',tokenPrefix+(cursor?'visible output':'output'),metricAvailable(CURRENT,'output_tokens')?f(p.out)+est:'--'),
    row('fresh',tokenPrefix+'uncached input',metricAvailable(CURRENT,'cache')?f(p.fresh):'--'),
    row('cacheRead',tokenPrefix+'cache read',metricAvailable(CURRENT,'cache')?f(p.read):'--'),
    row('cacheWrite',tokenPrefix+'cache write',metricAvailable(CURRENT,'cache')?f(p.write):'--'),
   ];
   const toolRows=[
    row('tools','tool calls',f(p.tools)),
    row('in',cursor?'input context':'input total',metricAvailable(CURRENT,'input_tokens')?f(p.total)+est:'--'),
    row('out',cursor?'visible output':'output',metricAvailable(CURRENT,'output_tokens')?f(p.out)+est:'--'),
   ];
   const rows=mode==='tools'?[...toolRows,...common]:mode==='tokens'?[...tokenRows,row('tools','tool calls',f(p.tools)),...common]:mode==='context'?[row('context','context',p.context===null?'--':pct(p.context)),row('in',cursor?'input context':'input total',metricAvailable(CURRENT,'input_tokens')?f(p.total)+est:'--'),row('tools','tool calls',f(p.tools)),row('cost','cost',metricAvailable(CURRENT,'cost')?money(p.cost)+est:'--')]:[row('cost',costLabel,metricAvailable(CURRENT,'cost')?money(p.cost)+est:'--'),row('in',cursor?'input context':'input total',metricAvailable(CURRENT,'input_tokens')?f(p.total)+est:'--'),row('out',cursor?'visible output':'output',metricAvailable(CURRENT,'output_tokens')?f(p.out)+est:'--'),row('tools','tool calls',f(p.tools))];
   const heading=s.aggregate?`session aggregate · ${f(CURRENT?.execution_coverage?.recorded_executions||0)} executions`:`execution #${esc(s.i??idx+1)}`;
   tip.innerHTML=`<div class=h>${heading}</div>${rows.join('')}${markers?`<div class=r><span>markers</span><b>${esc(markers)}</b></div>`:''}${promptBlock}`;
  }
 }
 hit.addEventListener('mousemove',e=>hoverAt(e.clientX,e.clientY));
 hit.addEventListener('mouseleave',()=>{hoverGroup.setAttribute('opacity','0'); if(tip)tip.style.display='none';});
 hit.addEventListener('touchmove',e=>{ if(e.touches[0]) hoverAt(e.touches[0].clientX,e.touches[0].clientY); },{passive:true});
}

function renderOverview(s){
 const ex=s.executions||[], avg=s.turns?s.total_cost/s.turns:0, peak=Math.max(0,...(s.series||[]).map(x=>x.in||0)),costAvailable=metricAvailable(s,'cost'),estimate=s.provider==='cursor'?' est':'';
 const timing=s.timing||{}, wait=s.wait_time||{}, context=s.context||{}, tools=s.tools||{};
 $('exec-hint').textContent=(s.source?.label||s.provider||'')+' · '+(s.primary_model||'');
 $('cost-start').textContent=dt12(timing.start_local)||'--';
 $('cost-end-label').textContent=timing.end_label||'Last activity';
 $('cost-end').textContent=dt12(timing.end_local)||'--';
 $('ov-duration').textContent=wait.available?waitFmt(wait.total_s):'--';
 $('ov-idle').textContent=wait.available?`${waitFmt(wait.avg_s)} avg · ${f(wait.sample_count)} completed ${countWord(wait.sample_count,'request')}`:'completed wait timing unavailable';
 $('ov-context').textContent=f(context.latest||0);
 $('ov-contextsm').textContent=context.window?`${pct(context.latest_pct)} of ${f(context.window)} window${s.provider==='cursor'?' · context snapshot':''}`:'latest input tokens';
 $('ov-loaded-label').textContent=tools.loaded_known?'Catalog advertised':'Tool types observed';
 $('ov-loaded').textContent=f(tools.loaded||0);
 $('ov-loadedsm').textContent=tools.loaded_known?`${f(tools.eager||0)} eager · ${f(tools.deferred||0)} deferred · partial catalog`:`${f(tools.unique_used||0)} observed · catalog unavailable`;
 $('ov-exec').textContent=f(s.turns); $('ov-model').textContent=s.primary_model||'';
 for(const [key,id] of [['skills','ov-skills'],['mcp_servers','ov-mcp']]){
  const counts=capabilityCounts(s.capabilities,key);
  $(id).textContent=capabilityRatio(counts);
  $(id+'sm').textContent=counts.loaded==null?'loaded list not recorded':`${f(counts.used)} used of ${f(counts.loaded)} ${counts.basis==='configured'?'configured now':'loaded'}`;
 }
 setCostValue('ov-avg',money(avg)+estimate,costAvailable);
 $('ov-tools').textContent=f(tools.total_calls||0); $('ov-tooltok').textContent=f(tools.total_output_tokens||0)+' returned tok';
 $('ov-peak').textContent=f(peak);
 $('ov-peak').textContent=f(context.peak||peak);
 $('ov-peak').nextElementSibling.textContent=context.window?`${pct(context.peak_pct)} of model window`:'max input tokens';
 setCostValue('ov-big',money(s.biggest_turn?.cost||0)+estimate,costAvailable); $('ov-bigsm').textContent=costAvailable?(s.biggest_turn?'execution #'+s.biggest_turn.idx+(estimate?' · local estimate':''):''):'billing unavailable';
}
