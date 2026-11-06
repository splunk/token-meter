// Data-series palettes for charts and SVG; CSS colors live in the stylesheet :root.
const THEME={
 compare:['#00bceb','#c7a7ff','#ffb457','#66d990'],
 modelOther:'#5E6E7E',
 modelSpeedExtra:['#F3C76A','#7AA7FF','#D4E157','#E6E6E6','#A1887F','#4DB6AC','#CE93D8','#90A4AE'],
};
const $=id=>document.getElementById(id);
const TOKENOMICS_SEARCH_URL='https://www.google.com/search?q=site%3Asplunk.com+tokenomics';
const DELIVERY_RANGES=['today','yesterday','7','30','90','month','last_month'];
const TIME_FILTER_WINDOWS=['today','yesterday','7d','30d','90d','month','last_month','all'];
const TIME_FILTER_LABELS={today:'Today',yesterday:'Yesterday','7d':'7 days','30d':'30 days','90d':'90 days',month:'Month','last_month':'Last month',all:'All history'};
const DEFAULT_TIME_FILTER_WINDOW='30d';
function normalizeTimeFilterWindow(value){return value==='24h'?'today':TIME_FILTER_WINDOWS.includes(value)?value:DEFAULT_TIME_FILTER_WINDOW;}
function timeFilterBounds(window,nowMs=Date.now()){
 const now=new Date(nowMs),stamp=(month,day)=>new Date(now.getFullYear(),month,day).getTime()/1000;
 if(window==='today')return {start:stamp(now.getMonth(),now.getDate()),end:0};
 if(window==='yesterday')return {start:stamp(now.getMonth(),now.getDate()-1),end:stamp(now.getMonth(),now.getDate())};
 if(window==='month')return {start:stamp(now.getMonth(),1),end:0};
 if(window==='last_month')return {start:stamp(now.getMonth()-1,1),end:stamp(now.getMonth(),1)};
 const seconds={'7d':604800,'30d':2592000,'90d':7776000}[window]||0;
 return {start:seconds?nowMs/1000-seconds:0,end:0};
}
const DELIVERY_EVIDENCE_FILTERS=['all','comparable','spend_only','git_only','unavailable'];
let deliveryRange=localStorage.getItem('tm_delivery_range')||'30';
if(!DELIVERY_RANGES.includes(deliveryRange)){deliveryRange='30';localStorage.setItem('tm_delivery_range',deliveryRange);}
let deliveryProject=localStorage.getItem('tm_delivery_project')||'';
let deliveryPayload=null,deliveryRequest=0,deliverySelectedDay='',deliveryLastLoadedAt=0;
let deliverySort={key:'covered_cost',direction:'desc'};
let deliveryModelSort={key:'covered_cost',direction:'desc'};
let deliveryEvidenceFilter='all';
let deliveryIgnorePointerSelection=false;
function stripNativeFieldTipTitles(root=document){
 root.querySelectorAll('.fieldtip[title]').forEach(el=>{if(!el.hasAttribute('aria-description'))el.setAttribute('aria-description',el.getAttribute('title'));el.removeAttribute('title');});
}
stripNativeFieldTipTitles();
function initFieldTipPopup(){
 const popup=document.createElement('div');
 popup.id='fieldtip-popup';popup.setAttribute('role','tooltip');popup.hidden=true;document.body.append(popup);
 let activeFieldTip=null,positionFrame=0;
 const fieldTipTarget=node=>node instanceof Element?node.closest('.fieldtip[data-tip]'):null;
 const hideFieldTip=target=>{
  if(target&&activeFieldTip!==target)return;
  activeFieldTip=null;popup.hidden=true;
 };
 const positionFieldTip=()=>{
  positionFrame=0;
  const target=activeFieldTip,tip=target?.getAttribute('data-tip')?.trim();
  if(!target||!target.isConnected||!tip){hideFieldTip();return;}
  popup.textContent=tip;popup.hidden=false;
  const rect=target.getBoundingClientRect(),margin=12,gap=8,width=popup.offsetWidth,height=popup.offsetHeight;
  const maxLeft=Math.max(margin,window.innerWidth-margin-width),maxTop=Math.max(margin,window.innerHeight-margin-height);
  const left=Math.max(margin,Math.min(rect.left,maxLeft)),below=rect.bottom+gap,above=rect.top-gap-height;
  const top=Math.max(margin,Math.min(below+height<=window.innerHeight-margin||above<margin?below:above,maxTop));
  popup.style.left=`${left}px`;popup.style.top=`${top}px`;
 };
 const scheduleFieldTipPosition=()=>{
  if(positionFrame)cancelAnimationFrame(positionFrame);
  positionFrame=requestAnimationFrame(positionFieldTip);
 };
 document.addEventListener('pointerover',event=>{
  const target=fieldTipTarget(event.target);
  if(target&&!target.contains(event.relatedTarget)){activeFieldTip=target;scheduleFieldTipPosition();}
 });
 document.addEventListener('pointerout',event=>{
  const target=fieldTipTarget(event.target);
  if(target&&!target.contains(event.relatedTarget))hideFieldTip(target);
 });
 document.addEventListener('focusin',event=>{
  const target=fieldTipTarget(event.target);
  if(target){activeFieldTip=target;scheduleFieldTipPosition();}
 });
 document.addEventListener('focusout',event=>{
  const target=fieldTipTarget(event.target);
  if(target&&!target.contains(event.relatedTarget))hideFieldTip(target);
 });
 window.addEventListener('resize',scheduleFieldTipPosition);
 window.addEventListener('scroll',scheduleFieldTipPosition,true);
}
initFieldTipPopup();
const f=n=>Math.round(n||0).toLocaleString();
const compactNumber=n=>{n=Number(n)||0;const a=Math.abs(n);if(a>=1e9)return (n/1e9).toFixed(a>=1e10?0:1).replace(/\.0$/,'')+'B';if(a>=1e6)return (n/1e6).toFixed(a>=1e7?0:1).replace(/\.0$/,'')+'M';if(a>=1e3)return (n/1e3).toFixed(a>=1e4?0:1).replace(/\.0$/,'')+'K';return f(n);};
const money=n=>{n=Number(n)||0;const a=Math.abs(n),d=a>=1000?1:2;if(a>0&&a<.005)return n<0?'-<$0.01':'<$0.01';return (n<0?'-':'')+'$'+a.toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d});};
const wholeMoney=n=>`${Number(n||0)<0?'-':''}$${Math.round(Math.abs(Number(n||0))).toLocaleString('en-US')}`;
const COST_UNAVAILABLE_TIP='Cost is unavailable, not $0. Token Meter can estimate cost only from locally stored usage with an identifiable model and an available configured rate. The local trace may omit billable usage, the model may be unknown or unpriced, or the session may be cloud-hosted without a readable local trace.';
function setCostValue(id,value,available){
 const element=$(id);element.textContent=available?value:'--';
 element.classList.toggle('fieldtip',!available);
 if(available){
  element.removeAttribute('tabindex');element.removeAttribute('aria-description');element.removeAttribute('data-tip');
  return;
 }
 element.setAttribute('tabindex','0');element.setAttribute('aria-description',COST_UNAVAILABLE_TIP);element.setAttribute('data-tip',COST_UNAVAILABLE_TIP);
}
function costUnavailableAttrs(){const safe=esc(COST_UNAVAILABLE_TIP);return `data-tip="${safe}"`;}
function costValueHtml(value,available,focusable=true){
 if(available)return value;
 if(focusable)return `<span ${tipAttrs(COST_UNAVAILABLE_TIP)}>--</span>`;
 const safe=esc(COST_UNAVAILABLE_TIP);return `<span class=fieldtip aria-description="${safe}" data-tip="${safe}">--</span>`;
}
const pct=n=>Math.round((n||0)*100)+'%';
const metricAvailable=(row,metric)=>row?.availability?.[metric]!==false;
const metricText=(row,metric,value,formatter)=>metricAvailable(row,metric)?formatter(value):'--';
const metricPartial=(row,metric)=>row?.coverage?.[metric]?.complete===false;
const partialSuffix=(row,metric)=>metricPartial(row,metric)?' · partial':'';
const usageProvenance=row=>row?.provenance||{};
const usageBasis=row=>usageProvenance(row).usage_basis||row?.usage_basis||(row?.token_estimate?'local_estimate':'reported');
const hasLocalEstimate=row=>['local_estimate','mixed'].includes(usageBasis(row))||row?.provider==='cursor';
const estimateSuffix=row=>usageBasis(row)==='local_estimate'?' est':usageBasis(row)==='mixed'?' incl. est':'';
const runtimeId=session=>String(session?.provider||session?.runtime_id||session?.client||'unknown-runtime');
const runtimeMeta=session=>RUNTIME_CATALOG[runtimeId(session)]||RUNTIME_CATALOG['unknown-runtime']||{label:'Unknown Runtime',symbol:'runtime.generic',color:'runtime-neutral',capabilities:['sessions']};
const runtimeMarkSvg=session=>{
 const symbol=runtimeMeta(session).symbol;
 const mark={
  'runtime.claude':'<path d="M25.7146 63.2153L41.4393 54.3917L41.7025 53.6226L41.4393 53.1976H40.6705L38.0394 53.0359L29.054 52.7929L21.2624 52.4691L13.7134 52.0644L11.8111 51.6594L10.0303 49.3118L10.2123 48.138L11.8111 47.0657L14.0981 47.2681L19.1574 47.6119L26.7467 48.138L32.2516 48.4618L40.4073 49.3118H41.7025L41.8846 48.7857L41.4393 48.4618L41.0955 48.138L33.243 42.8155L24.7432 37.1894L20.2909 33.9513L17.8824 32.3119L16.6684 30.774L16.1422 27.4147L18.328 25.0062L21.2624 25.2088L22.0112 25.4112L24.9861 27.6979L31.3407 32.616L39.6381 38.7273L40.8525 39.7391L41.3381 39.395L41.399 39.1523L40.8525 38.2415L36.3394 30.0858L31.5227 21.7883L29.3775 18.3478L28.811 16.2837C28.6087 15.4334 28.4669 14.7252 28.4669 13.8549L30.9563 10.4753L32.3321 10.0303L35.6515 10.4756L37.0479 11.6897L39.112 16.4052L42.4513 23.8327L47.6321 33.9313L49.15 36.9265L49.9594 39.6991L50.2632 40.5491H50.7894V40.0632L51.2141 34.3766L52.0035 27.3944L52.7726 18.4087L53.0358 15.8793L54.2905 12.8435L56.7795 11.2041L58.7224 12.135L60.3212 14.422L60.0986 15.899L59.1474 22.0718L57.2857 31.7458L56.0713 38.2218H56.7795L57.5892 37.4121L60.8677 33.061L66.3723 26.18L68.801 23.448L71.6342 20.4325L73.4556 18.9957H76.8962L79.4255 22.7601L78.2926 26.6456L74.7509 31.1384L71.8163 34.943L67.607 40.6097L64.9758 45.1431L65.2188 45.5072L65.8464 45.4466L75.358 43.4228L80.4984 42.4917L86.6304 41.4393L89.4033 42.7346L89.7065 44.0502L88.6135 46.7419L82.0566 48.3607L74.3662 49.8989L62.9118 52.6109L62.77 52.7121L62.9321 52.9144L68.0925 53.4L70.2987 53.5214H75.7021L85.7601 54.2702L88.3912 56.0108L89.9697 58.1358L89.7065 59.7545L85.6589 61.8189L80.1949 60.5236L67.4452 57.4881L63.0735 56.3952H62.4665V56.7596L66.1093 60.3213L72.7877 66.3523L81.1461 74.1236L81.5707 76.0462L80.4984 77.5638L79.3649 77.4021L72.0186 71.8772L69.1854 69.3879L62.77 63.9844H62.3453V64.5509L63.8223 66.7164L71.6342 78.4544L72.0389 82.0567L71.4725 83.2308L69.4487 83.939L67.2222 83.534L62.6485 77.1189L57.9333 69.8937L54.1284 63.4177L53.6631 63.6809L51.4167 87.8651L50.3644 89.0995L47.9356 90.0303L45.9121 88.4924L44.8392 86.0031L45.9118 81.0852L47.2071 74.6701L48.2594 69.5699L49.2106 63.2356L49.7773 61.131L49.7367 60.9892L49.2715 61.0498L44.4954 67.607L37.23 77.4224L31.4825 83.5746L30.1063 84.1211L27.7181 82.8864L27.9408 80.6805L29.2763 78.7177L37.2297 68.5988L42.026 62.3248L45.1227 58.7025L45.1024 58.176H44.9204L23.7917 71.8975L20.0274 72.3831L18.4083 70.8655L18.6106 68.3761L19.3798 67.5664L25.7343 63.195L25.7146 63.2153Z" fill="currentColor"/>',
  'runtime.codex':'<path d="M83.7733 42.8087C84.6678 40.1149 84.9771 37.2613 84.6807 34.4385C84.3843 31.6156 83.489 28.8885 82.0544 26.4394C77.6908 18.8436 68.9203 14.9365 60.3548 16.7725C57.9831 14.1344 54.9591 12.1668 51.5864 11.0673C48.2137 9.96772 44.611 9.77498 41.1402 10.5084C37.6694 11.2418 34.4527 12.8755 31.8132 15.2455C29.1736 17.6155 27.204 20.6383 26.1024 24.0103C23.3212 24.5806 20.6938 25.738 18.3958 27.405C16.0977 29.0721 14.1819 31.2104 12.7765 33.6772C8.36538 41.2609 9.3669 50.8267 15.2527 57.3327C14.3549 60.0251 14.0424 62.8782 14.3361 65.7012C14.6298 68.5241 15.523 71.2518 16.9558 73.7017C21.325 81.3002 30.1011 85.207 38.6712 83.3686C40.5554 85.4904 42.8707 87.1858 45.4623 88.3416C48.0539 89.4975 50.8622 90.0871 53.6999 90.0713C62.4793 90.079 70.2575 84.4114 72.9393 76.0515C75.7201 75.4802 78.347 74.3225 80.6449 72.6555C82.9427 70.9886 84.8587 68.8507 86.2649 66.3846C90.6227 58.8145 89.6172 49.3005 83.7733 42.8087ZM53.6999 84.8356C50.1955 84.8411 46.801 83.6129 44.1116 81.3661L44.5848 81.098L60.5123 71.9043C60.9087 71.6718 61.2379 71.3402 61.4674 70.942C61.6969 70.5439 61.8189 70.0929 61.8215 69.6333V47.1769L68.5553 51.072C68.6225 51.1063 68.6694 51.1707 68.6814 51.2456V69.854C68.6641 78.1208 61.9667 84.8183 53.6999 84.8356ZM21.4977 71.0843C19.7402 68.0497 19.1092 64.4925 19.7156 61.0386L20.1885 61.3225L36.1321 70.5165C36.5266 70.748 36.9757 70.87 37.4331 70.87C37.8905 70.87 38.3396 70.748 38.7341 70.5165L58.21 59.2883V67.0628C58.2081 67.1031 58.1973 67.1424 58.1782 67.1779C58.1591 67.2134 58.1322 67.2441 58.0996 67.2678L41.9671 76.5722C34.798 80.7022 25.6388 78.2463 21.4977 71.0843ZM17.3026 36.3898C19.0723 33.3357 21.8655 31.0062 25.1878 29.8138V48.7376C25.1818 49.1949 25.2986 49.6453 25.5261 50.042C25.7535 50.4387 26.0833 50.7671 26.4809 50.9928L45.8622 62.1739L39.1283 66.069C39.0919 66.0883 39.0513 66.0984 39.0101 66.0984C38.9689 66.0984 38.9283 66.0883 38.8919 66.069L22.7908 56.7809C15.6359 52.6337 13.1822 43.4816 17.3026 36.3112V36.3898ZM72.624 49.2426L53.1792 37.9512L59.8976 34.0718C59.9341 34.0524 59.9747 34.0423 60.016 34.0423C60.0573 34.0423 60.0979 34.0524 60.1344 34.0718L76.2355 43.3761C78.6973 44.7966 80.7043 46.8882 82.0221 49.4065C83.3398 51.9249 83.914 54.7661 83.6775 57.5985C83.4411 60.431 82.4038 63.1377 80.6867 65.4027C78.9696 67.6677 76.6436 69.3975 73.9803 70.3901V51.466C73.9663 51.0096 73.834 50.5647 73.5962 50.1749C73.3584 49.7851 73.0234 49.4638 72.624 49.2426ZM79.3261 39.1657L78.8529 38.8815L62.9411 29.6089C62.5442 29.376 62.0924 29.2532 61.6322 29.2532C61.172 29.2532 60.7202 29.376 60.3233 29.6089L40.8629 40.8374V33.0628C40.8587 33.0233 40.8654 32.9834 40.882 32.9473C40.8987 32.9113 40.9248 32.8803 40.9575 32.8579L57.0586 23.5692C59.5263 22.1476 62.3478 21.458 65.193 21.5811C68.0382 21.7042 70.7896 22.6348 73.1253 24.2642C75.461 25.8936 77.2845 28.1543 78.3825 30.782C79.4806 33.4097 79.8077 36.2957 79.3257 39.1025V39.1657H79.3261ZM37.1888 52.9484L30.455 49.069C30.4213 49.0487 30.3925 49.0212 30.3707 48.9884C30.3488 48.9557 30.3345 48.9186 30.3286 48.8797V30.3188C30.3323 27.4714 31.1466 24.6839 32.6761 22.2822C34.2057 19.8805 36.3874 17.9639 38.9661 16.7564C41.5448 15.549 44.4139 15.1005 47.2381 15.4636C50.0622 15.8267 52.7247 16.9862 54.9141 18.8067L54.4409 19.0748L38.5134 28.2686C38.117 28.5011 37.7879 28.8327 37.5584 29.2308C37.329 29.629 37.207 30.0799 37.2045 30.5395L37.1888 52.9487V52.9484ZM40.8472 45.0632L49.5209 40.0643L58.21 45.0635V55.0615L49.5523 60.0608L40.8632 55.0615L40.8472 45.0632Z" fill="currentColor"/>',
  'runtime.cursor':'<path d="M84.0704 28.9353L51.9066 10.4454C50.8738 9.85153 49.5994 9.85153 48.5666 10.4454L16.4043 28.9353C15.536 29.4345 15 30.3576 15 31.3575V68.6425C15 69.6424 15.536 70.5655 16.4043 71.0647L48.5681 89.5546C49.6009 90.1485 50.8753 90.1485 51.9081 89.5546L84.0719 71.0647C84.9402 70.5655 85.4762 69.6424 85.4762 68.6425V31.3575C85.4762 30.3576 84.9402 29.4345 84.0719 28.9353H84.0704ZM82.0501 32.8519L51.0006 86.4003C50.7907 86.7611 50.2366 86.6138 50.2366 86.1958V51.1329C50.2366 50.4322 49.8606 49.7842 49.2506 49.4324L18.7553 31.9017C18.3929 31.6927 18.5409 31.141 18.9606 31.141H81.0595C81.9414 31.141 82.4925 32.0927 82.0516 32.8534H82.0501V32.8519Z" fill="currentColor"/>',
  'runtime.opencode':'<g transform="scale(6.25)"><path d="M6.2 2.5 2 8l4.2 5.5M9.8 2.5 14 8l-4.2 5.5" fill=none stroke=currentColor stroke-width=1.6 stroke-linecap=round stroke-linejoin=round/></g>',
 }[symbol]||'<g transform="scale(6.25)"><circle cx=8 cy=8 r=5.7 fill=none stroke=currentColor stroke-width=1.4/><circle cx=8 cy=8 r=1.6 fill="currentColor"/></g>';
 return `<svg class=runtimeMark viewBox="0 0 100 100" aria-hidden=true focusable=false>${mark}</svg>`;
};
const runtimeBadgeContent=(session,label)=>`${runtimeMarkSvg(session)}<span>${esc(label)}</span>`;
const runtimeLabel=session=>session?.runtime||session?.label||runtimeMeta(session).label;
const PROVIDER_COLOR_IDS=new Set(['claude','codex','cursor','opencode','kiro']);
const providerColor=id=>PROVIDER_COLOR_IDS.has(id)?`var(--provider-${id})`:'var(--provider-other)';
const runtimeHarness=session=>providerColor(runtimeId(session));
const appBadgeClass=session=>PROVIDER_COLOR_IDS.has(runtimeId(session))?runtimeId(session):'neutral';
const appFilterGroup=session=>runtimeId(session);
const appFilterLabel=session=>runtimeMeta(session).label||session?.label||session?.runtime||'Unknown app';
const countWord=(n,one,many=one+'s')=>Number(n)===1?one:many;
const speedFmt=n=>{n=Number(n)||0;if(n<=0)return'--';if(n>=100)return n.toFixed(0);if(n>=10)return n.toFixed(1);return n.toFixed(2);};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// Work page state is declared early: the initial hash route can run before later definitions.
let WORK=null,workRequest=0,workLastLoadedAt=0,workMonths='6',workRuntime='',workProject='';
let workSupported=null;
// Declared before the first route runs: a deep link schedules its scroll during startup.
let pendingSettingsScroll=null;
let workSessionFilter=null,workFilterRequest=0;
let WORK_SETTINGS=null,workAreasDraft=null;
function durationShort(seconds){
 seconds=Math.max(0,Math.round(seconds||0));
 if(seconds<60) return seconds+'s';
 const minutes=Math.floor(seconds/60);
 if(minutes<60) return minutes+'m';
 const hours=Math.floor(minutes/60);
 if(hours<48) return hours+'h '+String(minutes%60).padStart(2,'0')+'m';
 const days=Math.floor(hours/24);
 return days+'d '+String(hours%24).padStart(2,'0')+'h';
}
function waitFmt(seconds){
 seconds=Math.max(0,Number(seconds)||0);
 if(seconds<10)return seconds.toFixed(1).replace(/\.0$/,'')+'s';
 const whole=Math.round(seconds);
 if(whole<60)return whole+'s';
 const minutes=Math.floor(whole/60),secs=whole%60;
 if(minutes<60)return minutes+'m '+String(secs).padStart(2,'0')+'s';
 const hours=Math.floor(minutes/60),rem=minutes%60;
 if(hours<48)return hours+'h '+String(rem).padStart(2,'0')+'m';
 const days=Math.floor(hours/24);
 return days+'d '+String(hours%24).padStart(2,'0')+'h';
}
// Readouts pair one number with a reference point and a plain-language reading.
function typicalComparison(value,typical){
 value=Number(value);typical=Number(typical);
 if(!Number.isFinite(value)||!Number.isFinite(typical)||typical<=0)return {ratio:null,band:''};
 const ratio=value/typical;
 return {ratio,band:value<=0?'none':ratio>=1.15?'above':ratio<=.85?'below':'near'};
}
function typicalCaption(value,typical,noun,typicalText){
 const {ratio,band}=typicalComparison(value,typical);
 if(!band)return '';
 if(band==='none')return `Nothing yet · ${noun} is ${typicalText}`;
 if(band==='above')return `${ratio>=10?Math.round(ratio):ratio.toFixed(1)}× ${noun} of ${typicalText}`;
 if(band==='below')return `${Math.round((1-ratio)*100)}% below ${noun} of ${typicalText}`;
 return `In line with ${noun} of ${typicalText}`;
}
function readoutScaleHtml({value,typical,high=0,label='',toned=true}){
 const {ratio,band}=typicalComparison(value,typical);
 if(!band)return '';
 const top=Math.max(Number(high)||0,Number(value)*1.1,Number(typical)*2);
 const at=Math.min(100,Math.max(0,Number(value)/top*100)),typ=Math.min(100,Number(typical)/top*100);
 const tone=toned&&ratio>=1.5?'high':'';
 return `<span class=readoutScale role=img aria-label="${esc(label)}" data-tone="${tone}" style="--at:${at.toFixed(1)}%;--typ:${typ.toFixed(1)}%"><i class=readoutScaleFill></i><i class=readoutScaleTypical></i><i class=readoutScaleMark></i></span>`;
}
const medianOf=values=>{const sorted=values.filter(Number.isFinite).sort((a,b)=>a-b);if(!sorted.length)return null;const mid=sorted.length>>1;return sorted.length%2?sorted[mid]:(sorted[mid-1]+sorted[mid])/2;};
// Baselines come from up to 30 prior active days; today is excluded so it is compared, not averaged in.
function dailyBaseline(days,todayKey=localDayKey()){
 const start=new Date(`${todayKey}T12:00:00`);start.setDate(start.getDate()-30);
 const startKey=localDayKey(start);
 const prior=(Array.isArray(days)?days:[]).filter(row=>row?.day&&row.day<todayKey&&row.day>=startKey&&Number(row.sessions)>0);
 const costRows=prior.filter(row=>metricAvailable(row,'cost')&&!metricPartial(row,'cost'));
 const costs=costRows.map(row=>Number(row.cost)||0),sessions=prior.map(row=>Number(row.sessions)||0);
 // A median of per-day rates keeps one burst of scripted sessions from swamping the baseline.
 const sessionCosts=costRows.map(row=>(Number(row.cost)||0)/Number(row.sessions));
 return {
  days:prior.length,
  typicalCost:medianOf(costs),highCost:costs.length?Math.max(...costs):0,
  typicalSessions:medianOf(sessions),highSessions:sessions.length?Math.max(...sessions):0,
  sessionCost:medianOf(sessionCosts),
  estimated:costRows.some(row=>hasLocalEstimate(row)),
 };
}
function idleLabel(seconds){
 seconds=Math.max(0,Math.round(seconds||0));
 if(seconds<60) return seconds+'s idle';
 const minutes=Math.floor(seconds/60);
 if(minutes<60) return minutes+'m idle';
 const hours=Math.floor(minutes/60), rem=minutes%60;
 return hours+'h '+String(rem).padStart(2,'0')+'m idle';
}
function dt12(v){
 const s=String(v||'');
 if(/\b[ap]m\b/i.test(s)) return s.toLowerCase();
 return s.replace(/\b(\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2}):(\d{2})\b/,(_,d,h,m,sec)=>{
  const n=Number(h), hh=String(n%12||12).padStart(2,'0');
  return `${d} ${hh}:${m}:${sec} ${n>=12?'pm':'am'}`;
 });
}
function costTipHtml(s,tok){
 if(!metricAvailable(s,'cost'))return `<div class=h>Cost unavailable</div><div class=note>${s.provider==='cursor'?'No supported public rate or input-context evidence was found for this Cursor trace.':'This trace does not expose enough pricing evidence.'}</div>`;
 const cost=s.cost||{};
 const rate=(usd,tokens)=>tokens?money((usd||0)*1e6/tokens):'$0.00';
 const row=(name,tokens,usd)=>tokens||usd?`<div class=r><span class=name>${name}</span><span class=formula>${f(tokens)} x ${rate(usd,tokens)} / 1M</span><span class=amt>${money(usd)}</span></div>`:'';
 const requestRow=(name,usd)=>usd?`<div class=r><span class=name>${name}</span><span class=formula>${f(Math.round(usd/.01))} x $0.01 / request</span><span class=amt>${money(usd)}</span></div>`:'';
 const cursor=s.provider==='cursor';
 const rows=(cursor?[
  row('Input context proxy',tok.input||0,cost.input||0),
  row('Visible model output',tok.output||0,cost.output||0),
 ]:[
  row('Uncached input',tok.input||0,cost.input||0),
  row('Cached input',tok.cache_read||0,cost.cache_read||0),
  row('Cache write',tok.cache_write||0,cost.cache_write||0),
  row('Output',tok.output||0,cost.output||0),
  requestRow('Web searches',cost.server_tools||0),
 ]).join('');
 const note=s.source?.pricing_note||(cursor?'Local Cursor estimate; cache and hidden model work are excluded.':`${s.cost_approx?'Estimated with public API rates. ':''}Cached input is priced separately from uncached input.`);
 return `<div class=h>${cursor?'Local cost estimate':'Cost calculation'}</div>${rows}<div class=total><span>Total</span><span>${money(s.total_cost||0)}${s.cost_approx?' est':''}</span></div><div class=note>${esc(note)}</div>`;
}
let LATEST=null, CURRENT=null, pinned=null, sessionRequest=0, selectedPollId='', RUNTIME_CATALOG={};
// Declared early: the first header render looks up trace titles before the All sessions code runs.
let allSessionInventory=null,allSessionInventoryAt=0,allSessionsRequest=null,renderedAllSessions=new Map(),renderedAllSessionActions={},forceAllSessionRowRefresh=false;
const LIVE_XSESSION={state:{snapshot:null,revision:''},session:{snapshot:null,revision:''}};
let AGENT_ACCESS=null, pendingAgentAccess=null, agentAccessLoading=false, agentAccessRechecking=false;
const CURRENT_SESSION_ORDER_KEY='tm_current_session_order_v1';
const CURRENT_SESSION_ORDER_LIMIT=200;
const CURRENT_SESSION_ORDER_MIGRATION_KEY='tm_current_session_newest_first_v1';
let currentSessionOrder=loadCurrentSessionOrder();
let currentSessionOrderNeedsNewestMigration=localStorage.getItem(CURRENT_SESSION_ORDER_MIGRATION_KEY)!=='1';
let currentSessionRuntimeFilter='all',currentSessionActivityFilter='all';
let currentSessionDragId='',currentSessionDropId='',currentSessionDropAfter=false;
let currentSessionSuppressClickUntil=0;
let currentSessionSeenIds=[],currentSessionSeenReady=false;
const pageSignalReducedMotionQuery=window.matchMedia('(prefers-reduced-motion: reduce)');
const pageSignalClockStartedAt=performance.now();
let pageEffectsModulePromise=null,pageEffectsDisabled=false;
let activePageSignalHead=null,activePageHeroHandle=null,activePageBorderHandle=null;
let activePageSignalMounting=false,activePageSignalGeneration=0;
let latestSessionSignalSnapshot=null;
const usageDetails=$('usage-details');
usageDetails.open=localStorage.getItem('tm_usage_details_open')==='1';
usageDetails.addEventListener('toggle',()=>localStorage.setItem('tm_usage_details_open',usageDetails.open?'1':'0'));

function routedSessionId(){
 const match=(location.pathname||'').match(/^\/sessions\/([^/]+)\/?$/);
 if(!match)return null;
 try{return decodeURIComponent(match[1]);}catch(e){return null;}
}
function sessionRoute(id){return `/sessions/${encodeURIComponent(id)}#summary`;}
function stateSessionId(state){return state?.source?.id||String(state?.session||'').split('.')[0]||null;}
function sessionRowKey(row){return String(row?.session||row?.id||'');}
function stateSessionKey(state){return String(state?.session||stateSessionId(state)||'');}
const DESKTOP_RESUME_ID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
function desktopSessionTarget(session){
 const source=session?.source||session||{},provider=String(source.provider||session?.provider||'').toLowerCase();
 if(provider==='codex'){
  const id=String(source.id||session?.id||stateSessionId(session)||'').trim();
  return id?{href:`codex://threads/${encodeURIComponent(id)}`,label:'Open in Codex',title:'Open this session in the Codex desktop app'}:null;
 }
 if(provider==='claude'){
  const resume=String(source.desktop_resume_id||session?.desktop_resume_id||'');
  return DESKTOP_RESUME_ID.test(resume)?{href:`claude://resume?session=${encodeURIComponent(resume)}`,label:'Open in Claude',title:'Open this session in the Claude desktop app'}:null;
 }
 return null;
}
function renderDesktopSessionLink(session){
 const link=$('session-desktop-link'),target=desktopSessionTarget(session);
 link.hidden=!target;
 if(target){
  link.href=target.href;
  link.textContent=target.label;
  link.title=target.title;
  link.setAttribute('aria-label',target.title);
 }else{
  link.removeAttribute('href');
  link.removeAttribute('title');
 }
}
function setHashRoute(route,{replace=false,apply=true}={}){
 const url=`${location.pathname}${location.search||''}#${route}`;
 if(history.pushState){
  history[replace?'replaceState':'pushState'](null,'',url);
  if(apply)applyHashRoute();
 }else location.hash=route;
}
function openSelectedSessionRoute(route='summary'){
 if(pinned&&history.pushState){
  history.pushState({sessionScope:sessionReturnScope},'',sessionRoute(pinned));
  applyHashRoute();
  return;
 }
 setHashRoute(route);
}
const SESSION_SCOPES=['current','all','compare','subagents'];
let sessionScope='current',sessionReturnScope='current';
const COMPARE_MAX=4,COMPARE_LETTERS='ABCD',COMPARE_COLORS=THEME.compare;
function loadCompareIds(){
 try{
  const stored=JSON.parse(localStorage.getItem('tm_compare_ids')||'[]');
  return Array.isArray(stored)?[...new Set(stored.map(String).filter(id=>id&&id.length<=240))].slice(0,COMPARE_MAX):[];
 }catch(error){return [];}
}
let compareIds=loadCompareIds(),compareBaseline=localStorage.getItem('tm_compare_baseline')||'';
let compareChartMetric=localStorage.getItem('tm_compare_chart')==='tokens'?'tokens':'cost';
let compareMissing=[],compareData=null,compareError='',compareLoading=false,compareRequestSeq=0,compareLoadedKey='',compareLoadedAt=0,compareNotice='';
const compareTitles=new Map();
let compareUiReady=false;
function normalizeSessionScope(scope){return SESSION_SCOPES.includes(scope)?scope:'current';}
function sessionScopeRoute(scope){scope=normalizeSessionScope(scope);return scope==='all'?'sessions-all':scope==='compare'?'sessions-compare':scope==='subagents'?'sessions-subagents':'sessions';}
function sessionScopeLabel(scope){return {all:'All sessions',compare:'Compare',subagents:'Subagents'}[normalizeSessionScope(scope)]||'Current sessions';}
function sessionListBackAvailable(scope){return !routedSessionId()&&(location.hash||'').replace(/^#/,'')===sessionScopeRoute(scope);}
function renderSessionBack(){
 const label=sessionScopeLabel(sessionReturnScope);
 $('session-back-label').textContent=label;
 $('session-back').setAttribute('aria-label',`Back to ${label}`);
}
function openSessionBack(){
 const scope=normalizeSessionScope(sessionReturnScope),state=history.state;
 if(state?.sessionListBack&&normalizeSessionScope(state.sessionScope)===scope&&history.back){history.back();return;}
 openSessionScope(scope);
}
function sessionScopeCopy(scope){
 return {
  all:['Local session history.','Search, filter, and compare local session history.'],
  compare:['Selected sessions side by side.','Compare up to four selected sessions and see which is cheapest and fastest.'],
  subagents:['Investigate child-agent spend, completion, and role patterns.','Filter child agents, inspect incomplete work, and open their parent sessions.'],
 }[scope]||['Last 30 minutes','Select a card to inspect it. Drag a card or press ⌥ and an arrow key to reorder.'];
}
function renderSessionScope(){
 const all=sessionScope==='all',subagents=sessionScope==='subagents',compare=sessionScope==='compare',overview=currentPanel==='sessions';
 $('view-session').classList.toggle('sessionAll',all);
 $('view-session').classList.toggle('sessionSubagents',subagents);
 $('view-session').classList.toggle('sessionCompare',compare);
 $('current-sessions-overview').hidden=all||subagents||compare;
 $('all-session-history').hidden=!all;
 $('session-compare').hidden=!compare;
 if($('subagent-explorer').parentElement===$('preview-surface-sessions'))$('subagent-explorer').hidden=!subagents;
 [...$('session-scope-tabs').querySelectorAll('[data-session-scope]')].forEach(button=>{
  const selected=button.dataset.sessionScope===sessionScope;
  button.setAttribute('aria-selected',String(selected));
  button.classList.toggle('on',selected);
  button.tabIndex=selected?0:-1;
 });
 if(overview){
  const [description,help]=sessionScopeCopy(sessionScope);
  $('current-description').textContent=description;
  $('session-page-title').setAttribute('aria-description',help);
 }
 if(compareUiReady)syncCompareSelectionUi();
 if(compareUiReady&&compare&&overview){
  renderComparison();
  loadComparison();
  if(!allSessionInventory)loadAllSessions();
 }
 if(all&&LATEST){
  renderAllSessions(LATEST.xsession);
  loadAllSessions();
 }
 if(subagents&&LATEST){renderSubagentExplorer(LATEST.xsession?.agent_usage);loadAllSessions();}
}
function setSessionScope(scope){
 sessionScope=normalizeSessionScope(scope);
 renderSessionScope();
}
function openSessionScope(scope,{replace=false}={}){
 if(scope==='subagents')mountSubagentExplorer('sessions');
 sessionRequest++;
 pinned=null;
 CURRENT=LATEST;
 sessionScope=normalizeSessionScope(scope);
 sessionReturnScope=sessionScope;
 const route=sessionScopeRoute(sessionScope);
 if(history.pushState)history[replace?'replaceState':'pushState'](null,'',`/#${route}`);
 else location.hash=route;
 applyHashRoute();
 return sessionScope==='all'?allSessionInventory||[]:sessionScope==='compare'?compareData?.sessions||[]:sessionScope==='subagents'?LATEST?.xsession?.agent_usage?.inventory||[]:LATEST?.xsession?.current_sessions||[];
}

