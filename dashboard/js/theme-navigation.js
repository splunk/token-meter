
const THEMES=[
 {id:'classic',label:'Classic',tagline:'The original cyan instrument field'},
 {id:'nebula',label:'Nebula',tagline:'Ultraviolet glass with ion-cyan and mint highlights'},
 {id:'monolith',label:'Monolith',tagline:'Graphite and platinum with one vermilion tip'},
 {id:'voltage',label:'Voltage',tagline:'Volt yellow on carbon with cyan and magenta sparks'},
 {id:'phosphor',label:'Phosphor',tagline:'Terminal green with falling-code rain'},
 {id:'omarchy',label:'Omarchy',tagline:'Deep indigo with blue, violet, and sky neon'},
 {id:'jadehorse',label:'Jade Horse',tagline:'Deep pine surfaces with celadon jade and pale green-white text'},
 {id:'ferrous',label:'Ferrous',tagline:'Oxidized red rust with molten amber and coolant-teal sparks'},
 {id:'afterglow',label:'Afterglow',tagline:'Plum dusk with a pink-to-amber sunset horizon'},
];
const THEME_STORAGE_KEY='tm_theme';
const normalizeThemeId=value=>THEMES.some(theme=>theme.id===value)?value:'classic';
const themeLabel=id=>THEMES.find(theme=>theme.id===id)?.label||'Classic';
const resolveTheme=preference=>window.tokenMeterTheme?.resolve(preference)||preference;
let themePreference=(()=>{try{return normalizeThemeId(localStorage.getItem(THEME_STORAGE_KEY));}catch(error){return 'classic';}})();
let activeTheme=document.documentElement.dataset.theme||'classic';
function renderThemePicker(){
 const host=$('theme-picker');
 host.innerHTML=THEMES.map(theme=>`<button type=button class=themeOption aria-pressed=false data-theme-option="${theme.id}" title="${esc(theme.tagline)}" style="--theme-swatch:var(--swatch-${theme.id});--theme-swatch-bg:var(--swatch-${theme.id}-bg)"><span class=themePreview aria-hidden=true><i></i><i></i><i></i></span><span class=themeCopy><b>${esc(theme.label)}</b><small>${esc(theme.tagline)}</small></span></button>`).join('');
 paintThemePicker();
}
function paintThemePicker(){
 [...$('theme-picker').querySelectorAll('[data-theme-option]')].forEach(option=>{
  option.setAttribute('aria-pressed',String(option.dataset.themeOption===themePreference));
 });
 $('theme-current-badge').textContent=themeLabel(activeTheme);
}
function repaintThemedSurfaces(){
 document.querySelector('meta[name=theme-color]')?.setAttribute('content',cssVar('--bg'));
 readChartTokens();
 if(activeModelStats&&activeModelScope===modelProject)renderModelStats(activeModelStats);
 if(activeEfficiencyStats&&activeEfficiencyScope===efficiencyProject)renderEfficiency(activeEfficiencyStats);
 if(CURRENT)drawIO(CURRENT.series||[]);
 if(spendInsightPayload)drawSpendScatter(spendInsightPayload.sessions||[]);
 if(deliveryPayload)drawDeliveryChart(deliveryPayload.days||[]);
 if(compareData)renderComparison();
 syncActivePageSignalEffects();
}
function setTheme(preference,{persist=true,origin=null}={}){
 preference=normalizeThemeId(preference);
 if(persist){try{localStorage.setItem(THEME_STORAGE_KEY,preference);}catch(error){}}
 themePreference=preference;
 const id=resolveTheme(preference);
 if(id===activeTheme){paintThemePicker();return;}
 activeTheme=id;
 paintThemePicker();
 // The latest choice wins even if an earlier transition callback is still queued.
 const commit=()=>{document.documentElement.dataset.theme=activeTheme;repaintThemedSurfaces();};
 if(!document.startViewTransition||pageSignalReducedMotionQuery.matches){commit();return;}
 const root=document.documentElement,generation=setTheme.revealGeneration=(setTheme.revealGeneration||0)+1;
 root.classList?.toggle('themeReveal',!!origin);
 const transition=document.startViewTransition(commit);
 // A pointer pick grows the new theme out of the option that was pressed.
 if(origin&&transition?.ready)transition.ready.then(()=>{
  const radius=Math.hypot(Math.max(origin.x,innerWidth-origin.x),Math.max(origin.y,innerHeight-origin.y));
  root.animate({clipPath:[`circle(0px at ${origin.x}px ${origin.y}px)`,`circle(${Math.ceil(radius)}px at ${origin.x}px ${origin.y}px)`]},{duration:480,easing:cssVar('--ease-in-out'),pseudoElement:'::view-transition-new(root)'});
 }).catch(()=>{});
 transition?.finished?.catch(()=>{}).finally(()=>{if(generation===setTheme.revealGeneration)root.classList.remove('themeReveal');});
}
renderThemePicker();
document.querySelector('meta[name=theme-color]')?.setAttribute('content',getComputedStyle(document.documentElement).getPropertyValue('--bg').trim());
$('theme-picker').onclick=event=>{const option=event.target.closest('[data-theme-option]');if(option)setTheme(option.dataset.themeOption,{origin:event.detail?{x:event.clientX,y:event.clientY}:null});};
window.addEventListener('storage',event=>{if(event.key===THEME_STORAGE_KEY)setTheme(event.newValue,{persist:false});});

const NAV_COMMANDS=[
 {id:'sessions',label:'Sessions',description:'Choose a recently active local session',section:'Navigate',action:'sessions'},
 {id:'all-sessions',label:'All sessions',description:'Search, filter, and open recorded session history',aliases:'logs',section:'Navigate',route:'sessions-all',glyph:'All'},
 {id:'compare-sessions',label:'Compare sessions',description:'Put selected sessions side by side',aliases:'diff same prompt runs',section:'Navigate',route:'sessions-compare',glyph:'A|B'},
 {id:'spend',label:'Spend',description:'Review spend across days and runtimes',section:'Navigate',route:'spend'},
 {id:'models',label:'Models',description:'Compare model spend, speed, and cache efficiency',section:'Navigate',route:'models'},
 {id:'subagents',label:'Subagents',description:'Track custom-agent spend and inspect child runs',section:'Navigate',route:'subagents'},
 {id:'efficiency',label:'Efficiency',description:'Review token efficiency overall and by model runtime',section:'Navigate',route:'efficiency'},
 {id:'work',label:'Work',description:'See where spend went, how sessions ended, and right-sizing',section:'Navigate',route:'work'},
 {id:'git',label:'Git',description:'Compare locally observed pushed code with covered AI spend',section:'Navigate',route:'git'},
 {id:'learn',label:'Learn',description:'Learn what drives cost and how to measure it',section:'Navigate',route:'learn'},
 {id:'capabilities',label:'Tools',description:'Inspect tools, MCPs, and optional skill packs',section:'Navigate',route:'capabilities'},
 {id:'settings',label:'Settings',description:'Configure pricing, analytics, connections, and updates',section:'Navigate',route:'settings'},
 {id:'spend-budgets',label:'Spend · Monthly budget',description:'Set runtime budgets and track their monthly total',section:'Navigate',route:'spend-budgets',glyph:'$'},
 {id:'spend-discounts',label:'Spend · Discounts',description:'Add the discount you pay so spend and budgets match your bill',section:'Navigate',route:'spend-discounts',glyph:'$'},
 {id:'session-run',label:'Session · Run',description:'Review cost, context, and pace for the selected session',section:'Selected session',route:'summary',action:'selected-session',glyph:'R'},
 {id:'appearance',label:'Settings · Appearance',description:'Choose the dashboard theme',aliases:'theme colors look',section:'Appearance',route:'settings-appearance',glyph:'◐'},
 ...THEMES.map(theme=>({id:`theme-${theme.id}`,label:`Theme · ${theme.label}`,description:theme.tagline,aliases:'appearance theme colors look',section:'Appearance',action:'theme',theme:theme.id,glyph:'◑'})),
 {id:'welcome',label:'Open welcome tour',description:'Replay the 12-slide tour of Token Meter pages',aliases:'getting started onboarding intro help tour',section:'Help',action:'welcome',glyph:'?'},
];
let visibleCommands=[],commandIndex=0,commandReturnFocus=null;
function setCommandIndex(index,{scroll=true}={}){
 const items=[...$('command-list').querySelectorAll('[data-command-index]')];
 if(!items.length){commandIndex=0;$('command-search').removeAttribute('aria-activedescendant');return;}
 commandIndex=(index+items.length)%items.length;
 items.forEach((item,i)=>{const selected=i===commandIndex;item.classList.toggle('selected',selected);item.setAttribute('aria-selected',String(selected));});
 const active=items[commandIndex];
 $('command-search').setAttribute('aria-activedescendant',active.id);
 if(scroll)active.scrollIntoView({block:'nearest'});
}
function renderCommandPalette(query=''){
 const terms=query.trim().toLowerCase().split(/\s+/).filter(Boolean);
 visibleCommands=NAV_COMMANDS.filter(command=>{
  if(command.id==='work'&&workSupported!==true)return false;
  const haystack=`${command.label} ${command.description} ${command.aliases||''} ${command.section}`.toLowerCase();
  return terms.every(term=>haystack.includes(term));
 });
 let section='';
 $('command-list').innerHTML=visibleCommands.length?visibleCommands.map((command,index)=>{
  const heading=command.section!==section?`<div class=commandGroup role=presentation>${esc(command.section)}</div>`:'';
  section=command.section;
  return `${heading}<button class=commandItem id="command-option-${index}" type=button role=option aria-selected=false data-command-index="${index}"><span class=commandGlyph>${esc(command.action==='theme'&&command.theme===themePreference?'✓':command.glyph||'→')}</span><span class=commandCopy><span class=commandLabel>${esc(command.label)}</span><span class=commandDescription>${esc(command.description)}</span></span></button>`;
 }).join(''):'<div class="commandEmpty emptyState">No matching commands</div>';
 setCommandIndex(0,{scroll:false});
}
function openCommandPalette(){
 const dialog=$('command-palette'),other=document.querySelector('dialog[open]');
 if(other&&other!==dialog)return false;
 if(dialog.open)return true;
 commandReturnFocus=document.activeElement;
 $('command-search').value='';
 renderCommandPalette();
 dialog.showModal();
 requestAnimationFrame(()=>$('command-search').focus());
 return true;
}
function closeCommandPalette(){if($('command-palette').open)$('command-palette').close();}
function runNavigationCommand(command){
 if(!command)return;
 closeCommandPalette();
 if(command.action==='welcome'){const returnFocus=commandReturnFocus;commandReturnFocus=null;openWelcome(null,{returnFocus});return;}
 if(command.action==='theme'){setTheme(command.theme);return;}
 if(command.action==='sessions'){openCurrentSessions();return;}
 if(command.action==='selected-session'){openSelectedSessionRoute(command.route);return;}
 if(command.latest)goToLatestSession();
 else openTopLevelRoute(command.route);
}
$('command-search').addEventListener('input',event=>renderCommandPalette(event.target.value));
$('command-list').onclick=event=>{const item=event.target.closest('[data-command-index]');if(item)runNavigationCommand(visibleCommands[Number(item.dataset.commandIndex)]);};
$('command-palette').addEventListener('click',event=>{if(event.target===$('command-palette'))closeCommandPalette();});
$('command-palette').addEventListener('close',()=>{
 const target=commandReturnFocus;
 commandReturnFocus=null;
 if(target?.focus&&target.offsetParent!==null)target.focus();
});
document.addEventListener('keydown',event=>{
 const palette=$('command-palette'),key=String(event.key||'').toLowerCase();
 if($('welcome-dialog').open){handleWelcomeKey(event);return;}
 if((event.metaKey||event.ctrlKey)&&!event.altKey&&!event.shiftKey&&key==='k'){
  event.preventDefault();
  if(palette.open)closeCommandPalette();else openCommandPalette();
  return;
 }
 if(palette.open){
  if(event.key==='Escape'){event.preventDefault();closeCommandPalette();}
  else if(event.key==='ArrowDown'){event.preventDefault();setCommandIndex(commandIndex+1);}
  else if(event.key==='ArrowUp'){event.preventDefault();setCommandIndex(commandIndex-1);}
  else if(event.key==='Enter'&&!event.isComposing){event.preventDefault();runNavigationCommand(visibleCommands[commandIndex]);}
  return;
 }
 handleLearnDeckKey(event);
});

const PANEL_KEYS=['summary'];
const PANEL_ALIAS={overview:'summary',flow:'summary',analysis:'summary',settings:'summary',activity:'summary',tools:'summary',alerts:'summary'};
const CURRENT_PANEL_KEYS=['sessions','run'];
const CURRENT_PANEL_ROUTES={sessions:'sessions',run:'summary'};
const storedSessionPanel=PANEL_ALIAS[localStorage.getItem('tm_session_panel')]||localStorage.getItem('tm_session_panel')||'summary';
let sessionPanel=PANEL_KEYS.includes(storedSessionPanel)?storedSessionPanel:'summary';
let currentPanel=sessionPanel==='summary'?'run':sessionPanel;
function restoreCurrentModules(){
 const homes=[
  ['session-chart-home','session-chart-module'],
  ['session-efficiency-home','session-efficiency'],
  ['session-budget-home','session-budget-control'],
  ['session-token-split-home','session-token-split-module'],
 ];
 homes.forEach(([homeId,moduleId])=>{const home=$(homeId),module=$(moduleId);if(home&&module&&module.parentElement!==home)home.appendChild(module);});
}
function mountCurrentModule(slotId,moduleId){
 const slot=$(slotId),module=$(moduleId);
 if(slot&&module&&module.parentElement!==slot)slot.appendChild(module);
}
function showCurrentPanel(panel){
 panel=PANEL_ALIAS[panel]||panel;
 if(panel==='summary')panel='run';
 if(!CURRENT_PANEL_KEYS.includes(panel))panel='run';
 currentPanel=panel;
 if(panel!=='sessions'){
  sessionPanel=CURRENT_PANEL_ROUTES[panel];
  localStorage.setItem('tm_session_panel',sessionPanel);
 }
 const overview=panel==='sessions';
 $('view-session').classList.toggle('sessionOverview',overview);
 $('view-session').classList.toggle('sessionDetail',!overview);
 document.querySelectorAll('[data-current-detail]').forEach(element=>element.classList.toggle('currentOverviewHidden',overview));
 if(!overview)renderSessionBack();
 $('session-page-title').textContent=overview?'Sessions':sessionDisplayName(CURRENT);
 const pageHelp=overview?sessionScopeCopy(sessionScope)[1]:'Review this session.';
 $('session-page-title').setAttribute('aria-description',pageHelp);
 $('session-page-title').dataset.tip=pageHelp;
 $('current-description').textContent=overview?sessionScopeCopy(sessionScope)[0]:(pinned?'Pinned run · updates every two seconds.':'Live run · updates with the trace.');
 CURRENT_PANEL_KEYS.forEach(key=>$(`preview-surface-${key}`).classList.toggle('on',key===panel));
 restoreCurrentModules();
 if(panel==='sessions'){
  renderCurrentSessions();
  renderSessionScope();
 }else if(panel==='run'){
 mountCurrentModule('preview-run-chart-slot','session-chart-module');
  mountCurrentModule('preview-run-efficiency-slot','session-efficiency');
  mountCurrentModule('preview-run-budget-slot','session-budget-control');
  mountCurrentModule('preview-token-split-slot','session-token-split-module');
  if(CURRENT)requestAnimationFrame(()=>drawIO(CURRENT.series||[]));
 }
}

function showTab(t){
 if(t!=='session')restoreCurrentModules();
 if(t!=='git')dismissGitChartInspector();
 const spend=t==='spend'||t==='daily';
 document.body.classList.toggle('sessionRoute',t==='session');
 $('tab-session').classList.toggle('on',t==='session');
	 $('tab-models').classList.toggle('on',t==='models');
 $('tab-subagents').classList.toggle('on',t==='subagents');
 $('tab-daily').classList.toggle('on',spend);
 $('tab-capabilities').classList.toggle('on',t==='capabilities');
 $('tab-efficiency').classList.toggle('on',t==='efficiency');
 $('tab-work').classList.toggle('on',t==='work');
 $('tab-git').classList.toggle('on',t==='git');
 $('tab-learn').classList.toggle('on',t==='learn');
 $('tab-settings').classList.toggle('on',t==='settings');
 $('view-session').classList.toggle('on',t==='session');
	 $('view-models').classList.toggle('on',t==='models');
 $('view-subagents').classList.toggle('on',t==='subagents');
 $('view-daily').classList.toggle('on',spend);
 $('view-capabilities').classList.toggle('on',t==='capabilities');
 $('view-efficiency').classList.toggle('on',t==='efficiency');
 $('view-work').classList.toggle('on',t==='work');
 $('view-git').classList.toggle('on',t==='git');
 $('view-learn').classList.toggle('on',t==='learn');
 $('view-settings').classList.toggle('on',t==='settings');
 [...document.querySelectorAll('.top .tab')].forEach(button=>{
  if(button.id===(spend?'tab-daily':`tab-${t}`))button.setAttribute('aria-current','page');
  else button.removeAttribute('aria-current');
 });
 const activeTop=$(spend?'tab-daily':`tab-${t}`);
 if(window.matchMedia('(max-width:760px)').matches&&activeTop?.scrollIntoView)requestAnimationFrame(()=>activeTop.scrollIntoView({block:'nearest',inline:'center'}));
 requestAnimationFrame(syncActivePageSignalEffects);
 const activeView=$(spend?'view-daily':`view-${t}`);
 requestAnimationFrame(()=>playRouteMotion(activeView));
}
function openTopLevelRoute(route){
 window.scrollTo(0,0);
 if(routedSessionId()&&history.pushState){history.pushState(null,'',`/#${route}`);applyHashRoute();return;}
 setHashRoute(route);
}
$('tab-session').onclick=()=>{window.scrollTo(0,0);openCurrentSessions();};
$('tab-models').onclick=()=>openTopLevelRoute('models');
$('tab-subagents').onclick=()=>openTopLevelRoute('subagents');
$('tab-daily').onclick=()=>openTopLevelRoute('spend');
$('tab-capabilities').onclick=()=>openTopLevelRoute('capabilities');
$('tab-efficiency').onclick=()=>openTopLevelRoute('efficiency');
$('tab-work').onclick=()=>openTopLevelRoute('work');
$('tab-git').onclick=()=>openTopLevelRoute('git');
$('tab-read').onclick=()=>window.open(TOKENOMICS_SEARCH_URL,'_blank','noopener');
$('tab-learn').onclick=()=>openTopLevelRoute('learn');
$('tab-settings').onclick=()=>openTopLevelRoute('settings');
$('session-back').onclick=openSessionBack;
$('session-scope-tabs').onclick=event=>{
 const button=event.target.closest('[data-session-scope]');
 if(button)openSessionScope(button.dataset.sessionScope);
};
$('session-scope-tabs').onkeydown=event=>{
 if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
 event.preventDefault();
 const buttons=[...$('session-scope-tabs').querySelectorAll('[data-session-scope]')];
 const index=buttons.indexOf(event.target.closest('[data-session-scope]'));
 if(index<0)return;
 const next=event.key==='Home'?0:event.key==='End'?buttons.length-1:(index+(event.key==='ArrowRight'?1:-1)+buttons.length)%buttons.length;
 buttons[next].focus();
 openSessionScope(buttons[next].dataset.sessionScope);
};
$('view-settings').onclick=event=>{
 const targetButton=event.target.closest('[data-settings-target]');
 if(!targetButton)return;
 if(targetButton.dataset.settingsTarget==='update-settings'){setHashRoute('settings-updates');return;}
 if(targetButton.dataset.settingsTarget==='work-insights-settings'){setHashRoute('settings-work-insights');return;}
 if(targetButton.dataset.settingsTarget==='appearance-settings'){setHashRoute('settings-appearance');return;}
 $(targetButton.dataset.settingsTarget)?.scrollIntoView({behavior:'smooth',block:'start'});
};

showCurrentPanel(currentPanel);
