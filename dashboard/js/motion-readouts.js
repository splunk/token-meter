// Pixel-viewBox charts redraw when their box changes (resize, or first shown after a hidden render)
// so text and marks stay undistorted. Redrawing sets viewBox to the box, so this settles.
let chartResizeTimer=0;
const PIXEL_CHARTS={spend:['s-scatter-svg'],git:['d-daily-chart'],models:['m-chart','m-mix','m-speedchart-svg'],goals:['goals-trend-chart'],efficiency:['e-output-dollar-chart','e-reasoning-chart','e-context-load-chart','e-cache-hit-chart'],session:['iochart']};
const chartViewBoxStale=id=>{const svg=$(id);if(!svg||!svg.clientWidth||!svg.clientHeight)return false;const box=svg.viewBox.baseVal;return Math.abs(box.width-Math.round(svg.clientWidth))>1||Math.abs(box.height-Math.round(svg.clientHeight))>1;};
const chartResizeObserver=new ResizeObserver(()=>{
 clearTimeout(chartResizeTimer);
 chartResizeTimer=setTimeout(()=>{
  if(activeModelStats&&activeModelScope===modelProject&&PIXEL_CHARTS.models.some(chartViewBoxStale))renderModelStats(activeModelStats);
  if(activeEfficiencyStats&&activeEfficiencyScope===efficiencyProject&&PIXEL_CHARTS.efficiency.some(chartViewBoxStale))renderEfficiency(activeEfficiencyStats);
  if(CURRENT&&PIXEL_CHARTS.session.some(chartViewBoxStale))drawIO(CURRENT.series||[]);
  if(spendInsightPayload&&PIXEL_CHARTS.spend.some(chartViewBoxStale))drawSpendScatter(spendInsightPayload.sessions||[]);
  if(deliveryPayload&&PIXEL_CHARTS.git.some(chartViewBoxStale))drawDeliveryChart(deliveryPayload.days||[]);
  if(goalDetailData&&PIXEL_CHARTS.goals.some(chartViewBoxStale))drawGoalTrend();
 },120);
});
Object.values(PIXEL_CHARTS).flat().forEach(id=>{const svg=$(id);if(svg)chartResizeObserver.observe(svg);});
// spectrum-motion-v1: motion that explains a change, never on poll redraws or keyboard-only paths.
const motionAllowed=()=>!pageSignalReducedMotionQuery.matches&&!document.hidden;
const CHART_REVEAL_IDS=Object.values(PIXEL_CHARTS).flat();
const chartsRevealedThisVisit=new Set();
function revealChart(svg){
 if(!svg||chartsRevealedThisVisit.has(svg.id)||!svg.childElementCount||!svg.getClientRects().length)return;
 chartsRevealedThisVisit.add(svg.id);
 if(!motionAllowed()||!svg.animate)return;
 // Time runs left to right, so the series draws in that direction.
 svg.animate([{clipPath:'inset(0 100% 0 0)'},{clipPath:'inset(0 0 0 0)'}],{duration:560,easing:cssVar('--ease-in-out')});
}
const chartRevealObserver=new MutationObserver(records=>records.forEach(record=>revealChart(record.target)));
CHART_REVEAL_IDS.forEach(id=>{const svg=$(id);if(svg)chartRevealObserver.observe(svg,{childList:true});});
let motionRouteView=null;
function playRouteMotion(view){
 if(view===motionRouteView)return;
 motionRouteView=view;
 chartsRevealedThisVisit.clear();
 CHART_REVEAL_IDS.forEach(id=>revealChart($(id)));
 if(!view||view.dataset.entered)return;
 view.dataset.entered='1';
 if(!motionAllowed())return;
 [...view.children].forEach((child,index)=>child.style.setProperty('--enter-i',String(Math.min(index,6))));
 view.classList.add('viewEnter');
 setTimeout(()=>view.classList.remove('viewEnter'),900);
}
const READOUT_SELECTOR=':is(.metric>.v,.metric>strong,.metric>b,.previewCost,.previewKpi .v,.budgetSpendValue,.efficiencyHeadline .v,.ov .v,.stat .v)';
const READOUT_PATTERN=/^([$<≈~]?)(\d[\d,]*(?:\.\d+)?)(\s?[A-Za-z%]{0,3})$/;
const READOUT_MIN_SHARE=.01,READOUT_QUIET_MS=10000;
const readoutText=new WeakMap(),readoutTweens=new WeakMap(),readoutLastTween=new WeakMap();
function parseReadout(text){
 const match=READOUT_PATTERN.exec(String(text).trim());
 if(!match)return null;
 return {prefix:match[1],suffix:match[3],value:Number(match[2].replace(/,/g,'')),decimals:(match[2].split('.')[1]||'').length,grouped:match[2].includes(',')};
}
function formatReadout(shape,value){
 return shape.prefix+value.toLocaleString('en-US',{minimumFractionDigits:shape.decimals,maximumFractionDigits:shape.decimals,useGrouping:shape.grouped})+shape.suffix;
}
function tweenReadout(element,fromText,toText){
 // Placeholders mean unavailable evidence: they switch instantly instead of counting up from a fake zero.
 const to=parseReadout(toText),from=parseReadout(fromText);
 if(!to||!from||from.prefix!==to.prefix||from.suffix!==to.suffix||from.value===to.value)return;
 // Live ticks stay quiet: only a meaningful change counts, at most once per element every 10s.
 if(Math.abs(to.value-from.value)<READOUT_MIN_SHARE*Math.max(Math.abs(from.value),Math.abs(to.value)))return;
 const now=performance.now();
 if(now-(readoutLastTween.get(element)??-Infinity)<READOUT_QUIET_MS)return;
 readoutLastTween.set(element,now);
 const started=now,duration=parseFloat(cssVar('--dur-data'))||480;
 const step=now=>{
  if(readoutTweens.get(element)!==step)return;
  const progress=Math.min(1,(now-started)/duration),eased=1-Math.pow(1-progress,3);
  const text=progress>=1?toText:formatReadout(to,from.value+(to.value-from.value)*eased);
  readoutText.set(element,text);
  element.textContent=text;
  if(progress<1)requestAnimationFrame(step);else readoutTweens.delete(element);
 };
 readoutTweens.set(element,step);
 requestAnimationFrame(step);
}
// Readouts count toward their new value so the size of a change is visible, not just its result.
const readoutObserver=new MutationObserver(records=>{
 records.forEach(record=>{
  const element=record.type==='characterData'?record.target.parentElement:record.target;
  if(!element?.matches?.(READOUT_SELECTOR))return;
  const next=element.textContent,previous=readoutText.get(element);
  if(next===previous)return;
  readoutText.set(element,next);
  readoutTweens.delete(element);
  if(previous===undefined||element.childElementCount||!motionAllowed()||element.closest('[aria-live]')||!element.getClientRects().length)return;
  tweenReadout(element,previous,next);
 });
});
document.querySelectorAll(READOUT_SELECTOR).forEach(element=>readoutText.set(element,element.textContent));
readoutObserver.observe(document.body,{subtree:true,childList:true,characterData:true});
window.addEventListener('resize',()=>{
 clearTimeout(spendResizeTimer);
 spendResizeTimer=setTimeout(()=>{
  if(!$('view-daily').classList.contains('on')||!spendChartRows.length)return;
  reconcileSpendBars(spendChartRows);
  const hovered=$('s-chart-inner').querySelector('.spendDay:hover');
  if(hovered)showSpendTip(hovered.dataset.spendDay,hovered);else hideSpendTip();
  if(spendInsightPayload)drawSpendScatter(spendInsightPayload.sessions||[]);
 },100);
});


