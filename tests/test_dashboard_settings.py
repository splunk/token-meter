import unittest
import json
import re
import shutil
import subprocess
from tests.dashboard_layout_support import DashboardLayoutCase


class DashboardSettingsLayoutTests(DashboardLayoutCase):
    def test_promoted_current_is_dense_complete_and_settings_stay_dedicated(self):
        for marker in (
            "id=tab-session", 'class="view on" id=view-session',
            "id=preview-run-chart-slot",
            "id=preview-run-budget-slot", "id=preview-token-split-slot",
            "id=preview-surface-run",
            "id=session-token-split-home", "id=session-token-split-module",
            "class=\"card previewStartStrip\"", "class=settingsPageLayout",
            "class=\"card settingsMap\"", "data-settings-target=agent-access",
            "function restoreCurrentModules()", "function showCurrentPanel(panel)",
            "function renderCurrentRun(s)",
            "if(h==='preview-settings')", "setHashRoute('settings',{replace:true,apply:false})",
            "const legacyCurrentRoutes={preview:'summary','preview-run':'summary'",
        ):
            self.assertIn(marker, self.page)
        for metric_id in (
            "preview-speed", "preview-wait",
            "preview-context", "preview-executions", "preview-tools",
            "preview-tool-results", "preview-cache-rate", "preview-cache-saved",
            "preview-burn", "preview-cost-task", "preview-started-at",
            "preview-last-at",
        ):
            self.assertIn(f"id={metric_id}", self.page)
        for removed_id in (
            "preview-input", "preview-output", "preview-thinking", "preview-avg-cost",
        ):
            self.assertNotIn(f"id={removed_id}", self.page)
        for unique_id in (
            "iochart", "sembar", "session-token-split-module", "budget", "agent-access",
            "model-pricing-settings", "update-settings",
        ):
            self.assertEqual(
                len(re.findall(rf"\bid={re.escape(unique_id)}(?:\s|>)", self.page)),
                1,
                f"{unique_id} must be moved, not cloned",
            )
        self.assertNotIn("id=tab-preview", self.page)
        self.assertNotIn("id=view-preview", self.page)
        current = self.page.split('<div class="view on" id=view-session>', 1)[1].split(
            "<div class=view id=view-models>", 1
        )[0]
        self.assertNotIn("Settings map", current)
        self.assertNotIn("id=agent-access", current)
        self.assertNotIn("id=frustration-settings", current)
        self.assertNotIn("id=model-pricing-settings", current)
        self.assertNotIn("What needs attention", current)
        self.assertNotIn("Open original Current", current)
        self.assertNotIn("Experimental", current)
        self.assertNotIn("Current preview", current)
        self.assertNotIn("previewKpi fieldtip", current)
        self.assertLess(current.index("id=preview-start"), current.index("id=preview-run-chart-slot"))
        self.assertLess(
            current.index("id=preview-run-chart-slot"),
            current.index("id=preview-token-split-slot"),
        )
        self.assertIn("previewSpeed", current)
        self.assertIn("text-overflow:ellipsis;white-space:nowrap", self.page)
        self.assertIn(
            "if(t!=='session')restoreCurrentModules();",
            self.page,
        )
        self.assertIn(
            "mountCurrentModule('preview-run-chart-slot','session-chart-module')",
            self.page,
        )
        self.assertIn(
            "mountCurrentModule('preview-token-split-slot','session-token-split-module')",
            self.page,
        )
        self.assertIn(
            "['session-token-split-home','session-token-split-module']",
            self.page,
        )
        for marker in (
            "id=current-tabs", "data-current-panel=", "id=preview-activity-slot",
            "id=preview-tools-slot", "id=preview-efficiency-slot",
            "id=preview-alerts-slot", "id=preview-surface-activity",
            "id=preview-surface-tools", "id=preview-surface-insights",
            "id=preview-surface-alerts", "id=session-activity-home",
            "id=session-tools-home", "id=session-insights-home",
            "id=session-alerts-home", "id=panel-activity", "id=panel-tools",
            "id=panel-insights", "id=panel-alerts",
            "mountCurrentModule(`preview-${panel}-slot`,`panel-${panel}`)",
            "const button=event.target.closest('[data-current-panel]');",
        ):
            self.assertNotIn(marker, self.page)
        self.assertIn("const CURRENT_PANEL_KEYS=['sessions','run'];", self.page)
        self.assertIn("const PANEL_KEYS=['summary'];", self.page)
        self.assertNotIn("mountCurrentModule('preview-settings", self.page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_restore_built_in_price_follows_scope_and_stays_reachable(self):
        functions = []
        for name in (
            "modelPriceScopePayload", "requestModelPrice", "updateModelPriceScope",
            "modelPriceLifecycle", "applyModelPriceLifecycle",
        ):
            match = re.search(
                rf"^(?:async )?function {name}\(.*?^\}}\n", self.page,
                re.MULTILINE | re.DOTALL,
            )
            self.assertIsNotNone(match, f"dashboard needs {name}")
            functions.append(match.group(0))
        script = """
const sent=[],confirms=[],scoped=[],toasts=[],statuses=[],notes={};let row,scope='from_now';
const modelPricingSelected=new Set(),LATEST=null,els={};
const $=id=>els[id]||(els[id]={id,textContent:'',className:'',disabled:false,classList:{toggle(){}}});
const modelPriceKey=(p,m)=>p+'/'+m,agentActionToken=()=>'t';
const modelPriceScope=()=>scope,modelPriceEffectiveFrom=()=>null;
const modelPriceRowFor=()=>row,renderModelPricing=()=>{},refreshLiveState=async()=>{};
const showAppToast=toast=>toasts.push(toast.title+': '+toast.body);
const confirm=text=>{confirms.push(text);return true;};
const confirmModelPriceHistory=(action)=>{scoped.push(action);return true;};
const fetch=async(url,options)=>{const body=JSON.parse(options.body);sent.push(body);return {ok:true,json:async()=>({ok:true,effective_scope:body.apply_to_all_history?'all_history':'now',model_pricing:{}})};};
""" + "".join(functions) + """
(async()=>{
 const button={textContent:'',disabled:false};
 const rows={
  overridden:{provider:'codex',model:'gpt-5.6',builtin:true,custom:false,active:true,overridden:true,periods:1},
  leftover:{provider:'codex',model:'gpt-5.6',builtin:true,custom:false,active:true,overridden:false,periods:2},
  plain:{provider:'codex',model:'gpt-5.6',builtin:true,custom:false,active:true,overridden:false,periods:0},
  custom:{provider:'codex',model:'mine',builtin:false,custom:true,active:true,overridden:false,periods:1},
  retired:{provider:'codex',model:'mine',builtin:false,custom:true,active:false,overridden:false,periods:2},
 };
 const lifecycles=Object.fromEntries(Object.entries(rows).map(([k,v])=>[k,modelPriceLifecycle(v)]));
 const run=async(key,selected)=>{scope=selected;row=rows[key];const before=sent.length;await applyModelPriceLifecycle(button);statuses.push([key,selected,sent.length-before,$('model-pricing-status').textContent]);};
 await run('overridden','from_now');
 await run('leftover','from_now');
 await run('leftover','all_history');
 await run('custom','from_now');
 await run('retired','from_now');
 for(scope of ['from_now','from_date','all_history']){updateModelPriceScope();notes[scope]=$('model-price-scope-note').textContent;}
 console.log(JSON.stringify({lifecycles,sent,confirms,scoped,toasts,statuses,notes}));
})();
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        out = json.loads(result.stdout)
        restore = {"label": "Restore built-in price", "scope": None}
        self.assertEqual(out["lifecycles"], {
            "overridden": restore,
            "leftover": restore,
            "plain": None,
            "custom": {"label": "Remove custom model", "scope": None},
            "retired": {"label": "Remove custom model", "scope": "all_history"},
        })
        # Restore follows the selected scope, so a From-now restore keeps older dated prices.
        self.assertEqual(out["sent"], [
            {"provider": "codex", "model": "gpt-5.6", "remove": True,
             "apply_to_all_history": False},
            {"provider": "codex", "model": "gpt-5.6", "remove": True,
             "apply_to_all_history": True},
            {"provider": "codex", "model": "mine", "remove": True,
             "apply_to_all_history": False},
            {"provider": "codex", "model": "mine", "remove": True,
             "apply_to_all_history": True},
        ])
        self.assertEqual(out["scoped"], [
            "Restore built-in pricing", "Restore built-in pricing", "Retire this custom price",
        ])
        self.assertEqual(out["confirms"], ["Delete mine from all saved pricing history?"])
        # A row already built-in from now explains how to clear its saved periods instead of no-op saving.
        self.assertEqual(out["statuses"][1][:3], ["leftover", "from_now", 0])
        self.assertIn("Choose All history, then restore", out["statuses"][1][3])
        self.assertNotIn("From date", out["statuses"][1][3])
        self.assertEqual(out["toasts"][1], "Already on built-in prices: " + out["statuses"][1][3])
        self.assertIn("id=model-pricing-status role=status aria-live=polite", self.page)
        self.assertEqual(out["statuses"][2][:3], ["leftover", "all_history", 1])
        self.assertEqual(out["toasts"][2],
                         "Default price restored: gpt-5.6 uses built-in prices across all history.")
        self.assertEqual(out["notes"]["from_now"],
                         "Changes start when you save; older session estimates keep their prior prices.")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_settings_polling_pauses_while_the_user_is_selecting_or_editing(self):
        functions = []
        for name in ("settingsInteractionActive", "budgetInteractionActive"):
            match = re.search(rf"^function {name}\(\)\{{[^\n]*\}}\n", self.page, re.MULTILINE)
            self.assertIsNotNone(match, f"dashboard needs {name}")
            functions.append(match.group(0))
        for name in ("viewInteractionActive", "renderSettings", "renderSpendView"):
            match = re.search(
                rf"^function {name}\(.*?^\}}\n",
                self.page,
                re.MULTILINE | re.DOTALL,
            )
            self.assertIsNotNone(match, f"dashboard needs {name}")
            functions.append(match.group(0))
        script = """
let settingsPointerActive=false;
let region='settings';
const view={contains:node=>Boolean(node?.inside===region&&region==='settings')};
const budgetView={contains:node=>Boolean(node?.inside===region&&region==='budgets')};
const field={get inside(){return region;},matches:selector=>selector.includes('input')};
const outside={inside:null,matches:()=>false};
let activeElement=outside;
let selection={isCollapsed:true,rangeCount:0,anchorNode:null,focusNode:null};
const document={get activeElement(){return activeElement;}};
const window={getSelection:()=>selection};
const $=id=>id==='view-settings'?view:id==='spend-budgets'?budgetView:null;
let calls=[];
const renderBudgets=()=>calls.push('budgets');
const renderModelPricing=()=>calls.push('pricing');
const renderSpend=()=>calls.push('spend');
function render(){renderSettings(xs);renderSpendView(xs);}
""" + "\n".join(functions) + """
const xs={model_pricing:{}};
const out={};
for(const where of ['settings','budgets']){
 region=where;
 activeElement=field;
 calls=[];render();out[where+'Focused']=[...calls];
 activeElement=outside;
 selection={isCollapsed:false,rangeCount:1,anchorNode:{inside:where},focusNode:{inside:where}};
 calls=[];render();out[where+'Selected']=[...calls];
 selection={isCollapsed:true,rangeCount:0,anchorNode:null,focusNode:null};
 settingsPointerActive=true;
 calls=[];render();out[where+'Dragging']=[...calls];
 settingsPointerActive=false;
}
calls=[];render();out.idle=[...calls];
console.log(JSON.stringify(out));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "settingsFocused": ["spend", "budgets"],
            "settingsSelected": ["spend", "budgets"],
            "settingsDragging": ["spend"],
            "budgetsFocused": ["pricing", "spend"],
            "budgetsSelected": ["pricing", "spend"],
            "budgetsDragging": ["spend"],
            "idle": ["pricing", "spend", "budgets"],
        })

    def test_software_updates_keep_checks_and_install_preferences_independent(self):
        for marker in (
            "data-settings-target=update-settings",
            "settings-updates",
            "id=update-settings",
            "id=update-enabled",
            "id=update-enabled type=checkbox checked",
            "id=update-auto-install type=checkbox checked",
            "Check for updates every 10 minutes",
            "Automatically install available updates",
            "Enabled by default",
            "checks once at startup, then every 10 minutes while it is running",
            "id=update-check",
            "id=update-notice",
            "New update available",
            "/settings/updates",
            "/updates/status",
            "/updates/check",
            "/updates/install",
            "setInterval(refreshSoftwareUpdateStatus,60000)",
            "status.available&&status.can_update",
            "softwareUpdateTarget=SOFTWARE_UPDATE.latest_revision",
            "softwareUpdateTarget&&state==='attention'",
            "setTimeout(()=>location.reload(),350)",
            "Checks fetch revision metadata only",
            "const autoInstall=enabled&&$('update-auto-install').checked",
            "{enabled,auto_install:autoInstall}",
            "$('update-auto-install').disabled=!enabled",
        ):
            self.assertIn(marker, self.page)
        self.assertIn(".updateNotice{position:fixed;right:18px;bottom:18px", self.page)
        self.assertIn(".softwareUpdates{display:grid;gap:8px;padding:12px 16px!important}", self.page)
        self.assertIn(
            ".softwareUpdateActions{display:grid;grid-template-columns:auto minmax(0,1fr) auto",
            self.page,
        )
        self.assertIn(
            "<span class=softwareUpdateMeta id=update-settings-meta></span>",
            self.page,
        )
        self.assertIn(".softwareUpdateActions .tbtn:disabled{opacity:.45;cursor:not-allowed}", self.page)
        self.assertIn("body.classList.toggle('updateReady',showNotice)", self.page)
        self.assertNotIn("setInterval(()=>postSoftwareUpdate('/updates/install'", self.page)
        self.assertIn("'settings-updates':'update-settings'", self.page)
        settings = self.page.split("<div class=view id=view-settings>", 1)[1].split(
            "</div>\n</div>\n<dialog class=commandPalette", 1
        )[0]
        self.assertLess(
            settings.index("data-settings-target=model-pricing-settings"),
            settings.index("data-settings-target=update-settings"),
        )
        self.assertLess(
            settings.index("id=model-pricing-settings"),
            settings.index("id=update-settings"),
        )

    def test_settings_sections_use_matching_titles_and_compact_supporting_copy(self):
        settings = self.page.split("<div class=view id=view-settings>", 1)[1].split(
            "</div>\n</div>\n<dialog class=commandPalette", 1
        )[0]
        for title, target in (
            ("Agent connection", "agent-access"),
            ("Model pricing", "model-pricing-settings"),
            ("Software updates", "update-settings"),
        ):
            self.assertIn(
                f"data-settings-target={target}><b>{title}</b>", settings,
            )
            self.assertIn(f">{title}</h2>", settings)
        self.assertNotIn(">Monthly budget</", settings)
        for removed in (
            "id=agent-access-badge", "class=mcpMark", "class=agentPromise",
            "class=agentTools", "class=agentPrivacy", "id=frustration-term-count",
            "<div class=softwareUpdatePromise",
        ):
            self.assertNotIn(removed, settings)
        self.assertNotIn('href="#learn-agent-access"', settings)
        self.assertIn("frame:{kind:'agent',anchor:'agent-access'", self.page)
        self.assertIn("Connections stay local and read-only.", settings)
        self.assertNotIn("Language signals", settings)
        self.assertIn(
            ".settingsMap:before,.agentAccess:before,.budgetLead:before{display:none}",
            self.page,
        )

    def test_tools_is_in_the_secondary_rail_above_settings(self):
        secondary = self.page.split("<div class=navSecondary>", 1)[1].split(
            "</div>", 1
        )[0]
        self.assertIn("id=tab-capabilities", secondary)
        self.assertLess(secondary.index("id=tab-learn"), secondary.index("id=tab-capabilities"))
        self.assertLess(secondary.index("id=tab-capabilities"), secondary.index("id=tab-settings"))

    def test_agent_access_has_a_dedicated_settings_tab(self):
        for marker in ("id=agent-discovery", "id=agent-access", "id=agent-clients",
                       "id=agent-dialog", "/agent-access/status", "/agent-access/toggle",
                       "class=\"card settingsMap\""):
            self.assertIn(marker, self.page)
        self.assertIn("frame:{kind:'agent',anchor:'agent-access'", self.page)
        self.assertIn("It gets numbers only, never your prompts or code.", self.page)
        for tool in ("mcp__tokenmeter__check", "mcp__tokenmeter__usage",
                     "mcp__tokenmeter__capabilities"):
            self.assertNotIn(tool, self.page)
        self.assertLess(self.page.index("id=view-capabilities"), self.page.index("id=view-settings"))
        self.assertLess(self.page.index("id=view-settings"), self.page.index("id=agent-access"))
        self.assertIn("setHashRoute('settings')", self.page)
        self.assertIn("tm_agent_discovery_dismissed", self.page)
        settings = self.page.split("<div class=view id=view-settings>", 1)[1].split(
            "</div>\n</div>\n<dialog class=commandPalette", 1
        )[0]
        self.assertLess(settings.index("data-settings-target=agent-access"),
                        settings.index("data-settings-target=model-pricing-settings"))
        self.assertLess(settings.index("data-settings-target=model-pricing-settings"),
                        settings.index("data-settings-target=delivery-settings"))
        self.assertLess(settings.index("id=agent-access"),
                        settings.index("id=model-pricing-settings"))
        self.assertLess(settings.index("id=model-pricing-settings"),
                        settings.index("id=delivery-settings"))

    def test_settings_has_no_agent_defaults_surface_or_loader(self):
        settings = self.page.split("<div class=view id=view-settings>", 1)[1].split(
            "</div>\n</div>\n<dialog class=commandPalette", 1
        )[0]
        self.assertNotIn("id=agent-default-settings", settings)
        self.assertNotIn("id=agent-defaults", settings)
        self.assertNotIn("function renderAgentDefaults", self.page)
        self.assertNotIn("function loadAgentDefaultSettings", self.page)
        self.assertNotIn("/settings/agent-defaults", self.page)

    def test_agent_access_status_loads_only_when_settings_is_opened(self):
        startup = self.page[self.page.rindex(
            "setInterval(refreshLiveState,LIVE_STATE_POLL_MS);"
        ):]
        self.assertNotIn("loadAgentAccess();", startup)
        settings_route = self.page.split(
            "if(h==='settings'||h==='model-pricing'", 1
        )[1].split("if(!h||h==='session')", 1)[0]
        self.assertIn("loadAgentAccess();", settings_route)

    def test_agent_access_conflict_has_an_explicit_repair_flow(self):
        for marker in (
            "row.conflict?'Repair':'Connect'",
            "const repairing=!!(enabled&&row.conflict)",
            "Replace only the existing user-level tokenmeter entry",
            "`${row.disconnect_command}\\n${row.connect_command}`",
            "repair:repairing",
            "result.repaired?'Connection repaired'",
        ):
            self.assertIn(marker, self.page)
        self.assertNotIn("Resolve manually", self.page)
        self.assertNotIn("||row.conflict", self.page[self.page.index("const disabled="):self.page.index("const detail=", self.page.index("const disabled="))])


if __name__ == "__main__":
    unittest.main()
