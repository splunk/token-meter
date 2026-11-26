import unittest
import json
import re
import shutil
import subprocess
from pathlib import Path
from tests.dashboard_layout_support import DashboardLayoutCase
from tests.dashboard_page import DASHBOARD_PAGE


class DashboardSpendLayoutTests(DashboardLayoutCase):
    def test_browser_operational_alerts_are_budget_only(self):
        self.assertNotIn("function isNotifiableInsight(i)", self.page)
        self.assertNotIn("fireNotification('Token insight'", self.page)
        self.assertNotIn("id=spike", self.page)
        self.assertNotIn("tm_spike", self.page)
        self.assertNotIn("s.last_turn_cost>spike", self.page)
        self.assertIn(
            "fireNotification('Token Meter session budget'",
            self.page,
        )
        self.assertIn(
            "fireNotification(isExceeded?'Token Meter budget exceeded':'Token Meter monthly budget'",
            self.page,
        )
        self.assertIn("n.title!=='Token insight'", self.page)
        self.assertIn(
            "String(n.body||'').startsWith('Last execution cost ')",
            self.page,
        )
        settings = self.page[self.page.index("id=view-settings"):]
        rail = self.page[self.page.index("<div class=top aria-label="):self.page.index("<div id=session-depot")]
        for marker in (
            "class=budgetBrowserAlerts", "class=budgetBrowserAlertsCopy",
            "Browser delivery",
        ):
            self.assertNotIn(marker, settings)
        for marker in (
            "id=panel-alerts", "id=notify role=switch", "id=notify-test",
            "id=notify-log", 'aria-label="Toggle browser budget alerts"',
        ):
            self.assertNotIn(marker, self.page)
        self.assertNotIn("id=notify", rail)

    def test_budget_alert_controls_share_the_set_budgets_header(self):
        config = self.page[
            self.page.index('id=budget-config'):
            self.page.index('class="budgetFormGroup budgetRuntimeFields"')
        ]
        header = config[
            config.index("class=budgetConfigHead"):
            config.index("class=budgetFormGroup")
        ]

        self.assertIn("Set budgets</h2>", header)
        self.assertIn("class=budgetAlertInline", header)
        self.assertIn("class=budgetThresholds", header)
        self.assertIn("id=budget-input-threshold-early", header)
        self.assertIn("id=budget-input-threshold-mid", header)
        self.assertIn("id=budget-input-threshold-full", header)
        self.assertIn("class=budgetNotifySwitch", header)
        self.assertIn("class=sw aria-hidden=true", header)
        self.assertIn("id=budget-input-notifications", header)
        self.assertNotIn("Default for sessions without a saved cap.", config)
        self.assertNotIn("Browser delivery", config)

    def test_monthly_budget_alerts_recover_missed_exceeded_state(self):
        for marker in (
            "function budgetExceededAlertState()",
            "tm_monthly_budget_exceeded_alerts",
            "previous=state[status.month]",
            "Array.isArray(previous)?previous",
            "over=Number((status.alert_percent??status.percent)||0)>=1",
            "exceededState=budgetExceededAlertState()",
            "monthExceeded.inApp===true",
            "monthExceeded.browser===true",
            "deliverMissedBrowserAlert",
            "notifyOn&&canNotify&&Notification.permission==='granted'",
            "Token Meter budget exceeded",
            "requireInteraction:isExceeded",
            "pushAppNotice(title,body",
            "state[status.month]=[...new Set([...seen,...crossed])]",
            "inApp:inAppNotified||alertExceeded",
            "browser:browserNotified||deliverMissedBrowserAlert",
        ):
            self.assertIn(marker, self.page)
        self.assertNotIn(
            "status.settings?.native_notifications===false",
            self.page,
        )
        self.assertNotIn(
            "if(!Array.isArray(seen)){state[status.month]=crossed",
            self.page,
        )

    def test_current_summary_keeps_session_budget_slider(self):
        summary = self.page[
            self.page.index("id=panel-summary"):
            self.page.index("<div class=foot>")
        ]
        for marker in (
            "id=session-budget-control", "id=budget-slider type=range",
            "id=budget type=number", "id=session-budget-spend",
            "function syncSessionBudgetControls", "function persistSessionBudget",
            "function queueSessionBudget", "const sessionBudgetWrites=new Map()",
            "'/settings/session-budget'", "s?.session_budget?.budget_usd",
            "$('budget-slider').addEventListener('input'",
        ):
            self.assertIn(marker, self.page)
        self.assertIn("id=budget-slider type=range", summary)
        self.assertIn("id=budget type=number", summary)
        self.assertNotIn("id=panel-alerts", self.page)
        self.assertNotIn("Only budget crossings create alerts.", self.page)
        self.assertNotIn(
            "Set a live-run cap without changing the machine-wide monthly budget.",
            summary,
        )

    def test_settings_default_session_budget_preserves_saved_session_caps(self):
        for marker in (
            "id=budget-input-session-default",
            "Default session budget",
            "function sessionBudgetForSession(s)",
            "defaultSessionBudget=Number(settings.default_session_budget||10)",
            "s?.session_budget?.budget_usd",
            "legacySessionBudgets",
            "default_session_budget:budgetNumber('budget-input-session-default')",
        ):
            self.assertIn(marker, self.page)

    def test_session_budget_writes_are_serialized_and_migration_is_superseded(self):
        budget = self.page[
            self.page.index("const SESSION_BUDGET_SLIDER_MIN"):
            self.page.index("function dollarInputValue")
        ]
        self.assertIn("while(entry.pending)", budget)
        self.assertIn("if(entry.saving)return entry.promise", budget)
        self.assertIn("queueSessionBudget(CURRENT,n)", budget)
        self.assertIn(
            "syncSessionBudgetControls(sessionBudgetForSession(s),s)", budget,
        )

    def test_git_spend_headers_explain_covered_spend(self):
        tip = "Covered spend: estimated spend from sessions with cost evidence in these projects."
        for attr in ("data-delivery-model-sort=covered_cost", "data-delivery-sort=covered_cost"):
            self.assertIn(f'{attr} data-tip="{tip}" aria-description="{tip}">Spend<', self.page)

    def test_spend_route_and_shell_replace_daily_brief(self):
        for marker in (
            "data-label=Spend aria-label=Spend",
            "<span class=tabLabel>Spend</span>",
            "<h1>Spend</h1>",
            "id=s-range", "<option value=today>Today</option>", "<option value=7>7 days</option>",
            "<option value=month>Month</option>", "<option value=custom>Custom</option>",
            "id=s-from", "id=s-to", "id=s-total", "id=s-average",
            "id=s-top-runtime", "id=s-highest-day", "id=s-chart",
            "id=s-chart-tip", "id=s-legend", "id=s-platforms",
            "if(h==='daily')setHashRoute('spend',{replace:true,apply:false})",
            "if(h==='spend'||h==='daily'||h==='spend-budgets'",
            "openTopLevelRoute('spend')",
            "{id:'spend',label:'Spend'",
            "{label:'Open Spend',route:'spend-budgets'}",
            "function renderSpend(xs)",
        ):
            self.assertIn(marker, self.page)
        for removed in (
            "<h1>Daily brief</h1>", "id=d-day-select", "id=d-trend-mode",
            "data-daily-trend=wait", "id=d-sessions", "id=d-providers",
            "renderDaily(",
        ):
            self.assertNotIn(removed, self.page)

    def test_spend_fits_chart_and_restores_range_logs(self):
        for marker in (
            "class=spendEvidenceGrid",
            "Highest-cost logs",
            "id=s-log-day",
            "id=s-log-count",
            "id=s-logs",
            "function spendAxisLabel(",
            "function renderSpendLogs(payload,window)",
            "function loadSpendLogs(window,generatedAt)",
            "fetch(`/spend/logs?from=${encodeURIComponent(window.start)}&to=${encodeURIComponent(window.end)}`",
            "data-spend-session=",
        ):
            self.assertIn(marker, self.page)
        self.assertIn(
            ".spendChart{min-height:270px;margin-top:12px;overflow:hidden",
            self.page,
        )

    def test_spend_evidence_cards_share_one_fixed_height(self):
        """Removing either card from the shared height contract breaks alignment."""
        for marker in (
            ".spendEvidenceGrid{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(300px,.7fr);align-items:stretch",
            ".spendLogsCard,.spendPlatformCard{height:360px;display:flex;min-height:0;flex-direction:column",
            "class=spendPlatformBody id=s-platforms",
            ".spendPlatformBody{min-height:0;flex:1;overflow-y:auto;overflow-x:hidden;scrollbar-gutter:stable",
        ):
            self.assertIn(marker, self.page)

    def test_spend_platform_split_keeps_amounts_inside_narrow_cards(self):
        """Restoring the old wide column minimums clips amounts at laptop width."""
        self.assertIn(
            ".spendPlatformRow{display:grid;grid-template-columns:minmax(78px,.48fr) minmax(72px,1fr) minmax(82px,auto);align-items:center;gap:10px",
            self.page,
        )
        self.assertIn(".spendPlatformValue{min-width:82px", self.page)

    def test_spend_hover_detail_is_transient_and_bars_stay_mounted(self):
        for marker in (
            "class=\"spendChartTip chartTip\"",
            ":where(.chartTip){position:absolute;pointer-events:none",
            ".spendDay:hover .spendBarValue{opacity:1}",
            "function hideSpendTip()",
            "$('s-chart-inner').addEventListener('pointerleave',hideSpendTip)",
            "if(!same){",
            "updateSpendBar(button,rows[index],index,rows.length,axisMax,chartWidth,{peak:index===peakIndex,spansYears})",
        ):
            self.assertIn(marker, self.page)
        self.assertNotIn(
            ".spendDay:hover .spendBarValue,.spendDay:focus-visible .spendBarValue,.spendDay.selected .spendBarValue",
            self.page,
        )
        select_day = self.page.split("function selectSpendDay(day", 1)[1].split(
            "function renderSpend(xs)", 1,
        )[0]
        self.assertNotIn("showSpendTip", select_day)
        render_spend = self.page.split("function renderSpend(xs)", 1)[1].split(
            "function validateSpendCustomDraft", 1,
        )[0]
        self.assertIn(
            "const hovered=$('s-chart-inner').querySelector('.spendDay:hover');",
            render_spend,
        )
        self.assertIn(
            "if(hovered)showSpendTip(hovered.dataset.spendDay,hovered);else hideSpendTip();",
            render_spend,
        )

    def test_spend_chart_uses_roving_focus_without_persistent_day_selection(self):
        logic = self.page.split("// spend-range-logic-start", 1)[1].split(
            "// spend-range-logic-end", 1
        )[0]
        script = logic + """
const rows = [{day:'2026-08-10'}, {day:'2026-08-11'}, {day:'2026-08-12'}];
console.log(JSON.stringify({
  initial: spendDayNavigation(rows, ''),
  moved: spendDayNavigation(rows, '2026-08-11'),
  empty: spendDayNavigation([], ''),
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "initial": {
                "focusDay": "2026-08-12",
                "buttons": [
                    {"day": "2026-08-10", "tabIndex": -1, "selected": False},
                    {"day": "2026-08-11", "tabIndex": -1, "selected": False},
                    {"day": "2026-08-12", "tabIndex": 0, "selected": False},
                ],
            },
            "moved": {
                "focusDay": "2026-08-11",
                "buttons": [
                    {"day": "2026-08-10", "tabIndex": -1, "selected": False},
                    {"day": "2026-08-11", "tabIndex": 0, "selected": False},
                    {"day": "2026-08-12", "tabIndex": -1, "selected": False},
                ],
            },
            "empty": {"focusDay": "", "buttons": []},
        })

    def test_spend_y_axis_and_range_logs_are_bounded(self):
        for marker in (
            "id=s-y-axis",
            "function spendNiceMax(value)",
            "function renderSpendAxis(axisMax)",
            ".spendLogsCard,.spendPlatformCard{height:360px",
            "overflow-y:auto",
            "scrollbar-gutter:stable",
            ".spendLogRow{height:68px",
            ".spendLogValue{grid-column:2/4;grid-row:2",
            ".spendLogRow .tbtn{grid-column:3;grid-row:1",
            "$('s-log-count').textContent=`${f(rows.length)} logs`",
        ):
            self.assertIn(marker, self.page)
        render_logs = self.page.split("function renderSpendLogs(payload,window)", 1)[1].split(
            "function loadSpendLogs", 1,
        )[0]
        self.assertNotIn(".slice(", render_logs)

        logic = self.page.split("// spend-range-logic-start", 1)[1].split(
            "// spend-range-logic-end", 1,
        )[0]
        result = subprocess.run(
            [
                "node", "-e",
                logic + "\nconsole.log(JSON.stringify([0,.7,18,189,200.00222974000008,458.253518,1000].map(spendNiceMax)));",
            ],
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), [1, 1, 20, 200, 240, 500, 1200])
        self.assertIn("flex:1 1 0;min-width:0", self.page)
        self.assertIn(
            ".spendDayLabel{position:absolute;bottom:0;left:50%;width:max-content",
            self.page,
        )
        self.assertIn(
            ".spendDay[data-axis-edge=last] .spendDayLabel{right:0;left:auto;transform:none}",
            self.page,
        )
        self.assertNotIn("inner.style.width=`max(100%", self.page)
        self.assertNotIn(
            ".spendChart{min-height:270px;margin-top:12px;overflow-x:auto",
            self.page,
        )

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_spend_last_month_is_previous_full_calendar_month(self):
        logic = self.page.split("// spend-range-logic-start", 1)[1].split(
            "// spend-range-logic-end", 1,
        )[0]
        script = logic + """
console.log(JSON.stringify([
  spendRangeWindow('last_month','','',new Date(2026,2,15,9,0,0)),
  spendRangeWindow('last_month','','',new Date(2026,0,1,0,5,0)),
  normalizeSpendRangeChoice('last_month'),
]));
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [
            {"valid": True, "start": "2026-02-01", "end": "2026-02-28", "dayCount": 28, "error": ""},
            {"valid": True, "start": "2025-12-01", "end": "2025-12-31", "dayCount": 31, "error": ""},
            "last_month",
        ])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_spend_trend_groups_by_monday_week_and_calendar_month(self):
        """Clipped edge buckets and the bucket holding today must not count as full periods."""
        logic = self.page.split("// spend-range-logic-start", 1)[1].split(
            "// spend-range-logic-end", 1,
        )[0]
        script = logic + """
const now = new Date(2026, 9, 16, 12, 0, 0);
const window = spendRangeWindow('custom', '2026-09-24', '2026-10-16', now);
const rows = spendCalendarRows([
  {day:'2026-09-24', cost:2, providers:[{provider:'claude', cost:2}]},
  {day:'2026-09-30', cost:3, providers:[{provider:'codex', cost:3}], usage_basis:'local_estimate'},
  {day:'2026-10-05', cost:5, providers:[{provider:'claude', cost:5}], coverage:{cost:{complete:false}}},
  {day:'2026-10-16', cost:1, providers:[{provider:'claude', cost:1}]},
], window);
const pick = bucket => ({day:bucket.day, start:bucket.bucketStart, end:bucket.end, days:bucket.days, fullDays:bucket.fullDays, cost:bucket.cost, clipped:bucket.clipped, inProgress:bucket.inProgress, estimated:spendIsEstimated(bucket), complete:bucket.coverage.cost.complete});
const weeks = spendBucketRows(rows, 'week', '2026-10-16');
const days = spendBucketRows(rows, 'day', '2026-10-16');
console.log(JSON.stringify({
  weeks: weeks.map(pick),
  months: spendBucketRows(rows, 'month', '2026-10-16').map(pick),
  dayCount: days.length,
  todayIncomplete: days.filter(row => row.incomplete).map(row => row.day),
  weekAverageBasis: spendAverageBasis(weeks).map(row => row.day),
  dayAverageBasis: spendAverageBasis(days).length,
  sevenDays: spendGranularityOptions(spendCalendarRows([], spendRangeWindow('7', '', '', now))),
  thirtyDays: spendGranularityOptions(spendCalendarRows([], spendRangeWindow('30', '', '', now))),
  ninetyDays: spendGranularityOptions(spendCalendarRows([], spendRangeWindow('90', '', '', now))),
  normalized: ['day', 'week', 'month', 'year', null].map(normalizeSpendGranularity),
  auto: [1, 7, 30, 35, 36, 90, 186, 187, 400].map(spendAutoGranularity),
  resolved: [
    spendResolveGranularity('auto', spendCalendarRows([], spendRangeWindow('90', '', '', now))).granularity,
    spendResolveGranularity('day', spendCalendarRows([], spendRangeWindow('90', '', '', now))).granularity,
    spendResolveGranularity('month', spendCalendarRows([], spendRangeWindow('30', '', '', now))).granularity,
    spendResolveGranularity('week', spendCalendarRows([], spendRangeWindow('7', '', '', now))).granularity,
    spendResolveGranularity('auto', spendCalendarRows([], spendRangeWindow('30', '', '', now))).granularity,
  ],
}));
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["weeks"], [
            {"day": "2026-09-24", "start": "2026-09-21", "end": "2026-09-27", "days": 4, "fullDays": 7, "cost": 2, "clipped": True, "inProgress": False, "estimated": False, "complete": True},
            {"day": "2026-09-28", "start": "2026-09-28", "end": "2026-10-04", "days": 7, "fullDays": 7, "cost": 3, "clipped": False, "inProgress": False, "estimated": True, "complete": True},
            {"day": "2026-10-05", "start": "2026-10-05", "end": "2026-10-11", "days": 7, "fullDays": 7, "cost": 5, "clipped": False, "inProgress": False, "estimated": False, "complete": False},
            {"day": "2026-10-12", "start": "2026-10-12", "end": "2026-10-16", "days": 5, "fullDays": 7, "cost": 1, "clipped": True, "inProgress": True, "estimated": False, "complete": True},
        ])
        self.assertEqual(payload["months"], [
            {"day": "2026-09-24", "start": "2026-09-01", "end": "2026-09-30", "days": 7, "fullDays": 30, "cost": 5, "clipped": True, "inProgress": False, "estimated": True, "complete": True},
            {"day": "2026-10-01", "start": "2026-10-01", "end": "2026-10-16", "days": 16, "fullDays": 31, "cost": 6, "clipped": True, "inProgress": True, "estimated": False, "complete": False},
        ])
        self.assertEqual(payload["dayCount"], 23)
        self.assertEqual(payload["todayIncomplete"], ["2026-10-16"])
        self.assertEqual(payload["weekAverageBasis"], ["2026-09-28", "2026-10-05"])
        self.assertEqual(payload["dayAverageBasis"], 23)
        self.assertEqual(payload["sevenDays"], {"day": True, "week": False, "month": False})
        self.assertEqual(payload["thirtyDays"], {"day": True, "week": True, "month": False})
        self.assertEqual(payload["ninetyDays"], {"day": True, "week": True, "month": True})
        self.assertEqual(payload["normalized"], ["day", "week", "month", "day", "day"])
        self.assertEqual(payload["auto"], ["day", "day", "day", "day", "week", "week", "week", "month", "month"])
        self.assertEqual(payload["resolved"], ["week", "day", "week", "day", "day"])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_spend_trend_options_ticks_and_in_progress_week(self):
        """A week still in progress is no basis for an average, so it cannot make Week selectable."""
        logic = self.page.split("// spend-range-logic-start", 1)[1].split(
            "// spend-range-logic-end", 1,
        )[0]
        script = logic + """
const now = new Date(2026, 9, 11, 12, 0, 0);
const rows = spendCalendarRows([], spendRangeWindow('custom', '2026-09-30', '2026-10-11', now));
const yearRows = spendCalendarRows([], spendRangeWindow('custom', '2025-12-29', '2026-01-11', new Date(2026, 0, 11, 12)));
console.log(JSON.stringify({
  withToday: spendGranularityOptions(rows, '2026-10-11'),
  withoutToday: spendGranularityOptions(rows),
  fellBack: spendResolveGranularity('week', rows, '2026-10-11').granularity,
  year: spendBucketRows(yearRows, 'month').map(row => [row.bucketStart, row.days, row.fullDays]),
  yearWeeks: spendBucketRows(yearRows, 'week').map(row => row.bucketStart),
  ticks: [3.95, 5, 40, 95, 3000].map(spendNiceMax),
}));
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["withToday"], {"day": True, "week": False, "month": False})
        self.assertEqual(payload["withoutToday"], {"day": True, "week": True, "month": False})
        self.assertEqual(payload["fellBack"], "day")
        self.assertEqual(payload["year"], [["2025-12-01", 3, 31], ["2026-01-01", 11, 31]])
        self.assertEqual(payload["yearWeeks"], ["2025-12-29", "2026-01-05"])
        self.assertEqual(payload["ticks"], [6, 6, 50, 100, 3200])

    def test_spend_trend_override_resets_whenever_the_range_changes(self):
        """A manual Day on 30 days must not follow the user onto All history."""
        for handler in (
            "spendCustomFrom=from;spendCustomTo=to;spendRangeChoice='custom';spendGranularityChoice='auto';",
            "spendRangeChoice=range;spendGranularityChoice='auto';persistSpendRange()",
            "if(savedCustomInvalid){spendRangeChoice='30';spendGranularityChoice='auto';",
            "spendRangeChoice='month';spendGranularityChoice='auto';persistSpendRange()",
        ):
            self.assertIn(handler, self.page)
        self.assertIn("aria-disabled", self.page.split("function renderSpend(xs)", 1)[1])
        self.assertNotIn("button.disabled=!granularityOptions", self.page)

    def test_spend_trend_granularity_control_drives_chart(self):
        for marker in (
            '<div class=seg id=s-granularity role=group aria-label="Group spend by">',
            "data-spend-granularity=day>Day</button>",
            "data-spend-granularity=week>Week</button>",
            "data-spend-granularity=month>Month</button>",
            "let spendGranularityChoice='auto',spendChartRows=[];",
            "spendChartRows=spendBucketRows(rows,granularity,today);",
            "reconcileSpendBars(spendChartRows);",
            "<div class=spendGrid aria-hidden=true></div>",
            "button.setAttribute('aria-disabled',String(!granularityOptions[value]));",
        ):
            self.assertIn(marker, self.page)
        render_spend = self.page.split("function renderSpend(xs)", 1)[1].split(
            "function validateSpendCustomDraft", 1,
        )[0]
        self.assertIn("spendResolveGranularity(spendGranularityChoice,rows,today)", render_spend)
        self.assertNotIn("SPEND_GRANULARITY_KEY", self.page)
        self.assertIn(".spendChartInner[data-granularity] .spendStackSegment{opacity:.82", self.page)
        self.assertIn(".spendChartInner[data-granularity] .spendStackSegment+.spendStackSegment{border-bottom:1px solid", self.page)
        self.assertIn("<span class=spendDayStack style=\"--spend-height:${height}%\">${segments}</span>", self.page)
        self.assertNotIn("spendTotalKey", self.page)
        grid_rule = self.page.split("\n.spendGrid{", 1)[1].split("}", 1)[0]
        self.assertIn("height:calc(var(--spend-chart-h) - var(--spend-day-head) - var(--spend-day-foot) - 2*var(--spend-day-gap))", grid_rule)
        self.assertNotIn("inset:", grid_rule)
        self.assertIn("--spend-chart-h:260px", self.page)
        self.assertEqual(self.page.count("height:var(--spend-chart-h)"), 2)
        self.assertIn("(index%step===0&&count-1-index>=step)", self.page)
        self.assertIn("[data-peak=true][data-peak-edge=first] .spendBarValue{align-self:flex-start}", self.page)
        show_tip = self.page.split("function showSpendTip(", 1)[1].split("function selectSpendDay", 1)[0]
        self.assertIn("details.runtimes.filter(runtime=>runtime.cost>0||runtime.unavailable)", show_tip)
        self.assertIn("Math.min(cardRect.height-tip.offsetHeight-8", show_tip)
        day_rule = self.page.split("\n.spendDay{", 1)[1].split("}", 1)[0]
        self.assertIn("grid-template-columns:minmax(0,1fr)", day_rule)
        self.assertIn(".spendChartInner[data-dense=true]{--spend-bar-share:52%}", self.page)
        self.assertIn("inner.dataset.dense=String(rows.length>45);", self.page)
        stack_rule = self.page.split(".spendDayStack{", 1)[1].split("}", 1)[0]
        self.assertNotIn("border-radius", stack_rule)
        self.assertIn("width:min(var(--spend-bar-max,24px),var(--spend-bar-share,64%))", stack_rule)
        self.assertNotIn("transparent 0 44px", self.page.split("\n.spendDayTrack{", 1)[1].split("}", 1)[0])

    def test_spend_average_reference_uses_every_calendar_day(self):
        """Dropping zero-spend days makes the chart line disagree with the KPI."""
        logic = self.page.split("// spend-range-logic-start", 1)[1].split(
            "// spend-range-logic-end", 1,
        )[0]
        script = logic + """
const rows = [
  {day:'2026-08-01', cost:1},
  {day:'2026-08-02', cost:2},
  {day:'2026-08-03', cost:0},
  {day:'2026-08-04', cost:1},
];
console.log(JSON.stringify({
  reference: spendAverageReference(rows, 4),
  empty: spendAverageReference([], 4),
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "reference": {"value": 1, "ratio": 0.25},
            "empty": {"value": 0, "ratio": 0},
        })

    def test_spend_uses_exact_calendar_ranges_and_stacked_runtime_bars(self):
        for marker in (
            "// spend-range-logic-start",
            "const SPEND_RUNTIME_COLORS={claude:'var(--provider-claude)',codex:'var(--provider-codex)',cursor:'var(--provider-cursor)',opencode:'var(--provider-opencode)',kiro:'var(--provider-kiro)',grok:'var(--provider-grok)',unknown:'var(--provider-other)'};",
            "function spendRangeWindow(range,from='',to='',now=new Date(),earliest='')",
            "<select class=filterSelect id=s-range aria-label=\"Spend history range\"><option value=today>Today</option><option value=yesterday>Yesterday</option><option value=7>7 days</option><option value=30 selected>30 days</option><option value=90>90 days</option><option value=month>Month</option><option value=last_month>Last month</option><option value=all>All history</option><option value=custom>Custom</option></select>",
            "$('s-range').value=spendRangeChoice;",
            "$('s-range').onchange=event=>{",
            "function normalizeSpendRangeChoice(value)",
            "function spendCalendarRows(days,window)",
            "function spendRuntimeKey(provider)",
            "function spendRuntimeTotals(rows)",
            "class=spendStackSegment",
            "data-spend-day=",
            "function spendDayNavigation(rows,focusDay='')",
            "function showSpendTip(",
            "addEventListener('pointerover',handleSpendInspect)",
            "xs?.spend?.days||xs?.daily||[]",
        ):
            self.assertIn(marker, self.page)

        logic = self.page.split("// spend-range-logic-start", 1)[1].split(
            "// spend-range-logic-end", 1
        )[0]
        script = logic + """
const now = new Date(2026, 7, 12, 12, 0, 0);
const today = spendRangeWindow('today', '', '', now);
const seven = spendRangeWindow('7', '', '', now);
const thirty = spendRangeWindow('30', '', '', now);
const month = spendRangeWindow('month', '', '', now);
const monthFirst = spendRangeWindow('month', '', '', new Date(2026, 8, 1, 12, 0, 0));
const january = spendRangeWindow('month', '', '', new Date(2027, 0, 9, 12, 0, 0));
const custom = spendRangeWindow('custom', '2026-08-01', '2026-08-03', now);
const yesterday = spendRangeWindow('yesterday', '', '', now);
const ninety = spendRangeWindow('90', '', '', now);
const allHistory = spendRangeWindow('all', '', '', now, '2026-05-01');
const allEmpty = spendRangeWindow('all', '', '', now, '');
const invalid = spendRangeWindow('custom', '2026-08-04', '2026-08-03', now);
const rows = spendCalendarRows([
  {day:'2026-08-03',cost:3,providers:[{provider:'claude',cost:3}]},
  {day:'2026-08-01',cost:2,providers:[{provider:'codex',cost:2}]},
], custom);
console.log(JSON.stringify({
  today, seven, thirty, month, monthFirst, january, custom, invalid, yesterday, ninety, allHistory, allEmpty,
  savedRanges: ['today','yesterday','7','30','90','month','last_month','all','custom','unexpected'].map(normalizeSpendRangeChoice),
  rowDays: rows.map(row=>row.day),
  rowCosts: rows.map(row=>row.cost),
  keys: ['Claude Code','codex','Cursor IDE','OpenCode','Kiro CLI','other'].map(spendRuntimeKey),
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        payload = json.loads(result.stdout)
        self.assertEqual(payload["today"]["start"], "2026-08-12")
        self.assertEqual(payload["today"]["end"], "2026-08-12")
        self.assertEqual(payload["seven"]["start"], "2026-08-06")
        self.assertEqual(payload["seven"]["end"], "2026-08-12")
        self.assertEqual(payload["seven"]["dayCount"], 7)
        self.assertEqual(payload["thirty"]["start"], "2026-07-14")
        self.assertEqual(payload["thirty"]["dayCount"], 30)
        self.assertEqual(payload["month"], {
            "valid": True, "start": "2026-08-01", "end": "2026-08-12",
            "dayCount": 12, "error": "",
        })
        self.assertEqual(payload["monthFirst"], {
            "valid": True, "start": "2026-09-01", "end": "2026-09-01",
            "dayCount": 1, "error": "",
        })
        self.assertEqual(payload["january"], {
            "valid": True, "start": "2027-01-01", "end": "2027-01-09",
            "dayCount": 9, "error": "",
        })
        self.assertEqual(
            payload["savedRanges"],
            ["today", "yesterday", "7", "30", "90", "month", "last_month", "all", "custom", "30"],
        )
        self.assertEqual(payload["yesterday"], {
            "valid": True, "start": "2026-08-11", "end": "2026-08-11",
            "dayCount": 1, "error": "",
        })
        self.assertEqual(payload["ninety"]["start"], "2026-05-15")
        self.assertEqual(payload["ninety"]["dayCount"], 90)
        self.assertEqual(payload["allHistory"], {
            "valid": True, "start": "2026-05-01", "end": "2026-08-12",
            "dayCount": 104, "error": "",
        })
        self.assertEqual(payload["allEmpty"]["start"], "2026-08-12")
        self.assertEqual(payload["allEmpty"]["dayCount"], 1)
        self.assertEqual(payload["custom"]["dayCount"], 3)
        self.assertFalse(payload["invalid"]["valid"])
        self.assertEqual(payload["rowDays"], [
            "2026-08-01", "2026-08-02", "2026-08-03",
        ])
        self.assertEqual(payload["rowCosts"], [2, 0, 3])
        self.assertEqual(payload["keys"], [
            "claude", "codex", "cursor", "opencode", "kiro", "unknown",
        ])

    def test_spend_session_analytics_excludes_unavailable_evidence(self):
        logic = self.page.split("// spend-range-logic-start", 1)[1].split(
            "// spend-range-logic-end", 1
        )[0]
        script = logic + """
const rows = [
  {id:'a', cost:40, input_tokens:1000, turns:1, duration_s:10,
   duration_available:true, availability:{cost:true,input_tokens:true}},
  {id:'b', cost:30, input_tokens:2000, turns:2, duration_s:20,
   duration_available:true, availability:{cost:true,input_tokens:true}},
  {id:'c', cost:20, input_tokens:3000, turns:3, duration_s:30,
   duration_available:true, availability:{cost:true,input_tokens:true}},
  {id:'d', cost:10, input_tokens:4000, turns:4, duration_s:999,
   duration_available:false, availability:{cost:true,input_tokens:true}},
  {id:'e', cost:99, input_tokens:5000, turns:5, duration_s:50,
   duration_available:true, availability:{cost:false,input_tokens:true}},
];
const analytics = spendSessionAnalytics(rows);
console.log(JSON.stringify({
  concentration: analytics.concentration,
  cost: analytics.distributions.cost,
  input: analytics.distributions.input,
  executions: analytics.distributions.executions,
  active: analytics.distributions.active,
  spendPoints: spendScatterRows(rows, 'cost').map(row => row.id),
  inputPoints: spendScatterRows(rows, 'input').map(row => row.id),
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        payload = json.loads(result.stdout)
        self.assertEqual(payload["concentration"], {
            "covered": 4, "total": 100, "top_count": 1,
            "top_cost": 40, "top_share": 0.4,
        })
        self.assertEqual(payload["cost"], {
            "count": 4, "p10": 13, "p25": 17.5, "median": 25,
            "mean": 25, "p75": 32.5, "p90": 37,
        })
        self.assertEqual(payload["input"], {
            "count": 5, "p10": 1400, "p25": 2000, "median": 3000,
            "mean": 3000, "p75": 4000, "p90": 4600,
        })
        self.assertEqual(payload["executions"], {
            "count": 5, "p10": 1.4, "p25": 2, "median": 3,
            "mean": 3, "p75": 4, "p90": 4.6,
        })
        self.assertEqual(payload["active"], {
            "count": 4, "p10": 13, "p25": 17.5, "median": 25,
            "mean": 27.5, "p75": 35, "p90": 44,
        })
        self.assertEqual(payload["spendPoints"], ["a", "b", "c"])
        self.assertEqual(payload["inputPoints"], ["a", "b", "c", "e"])

    def test_spend_route_exposes_concentration_shape_and_openable_outliers(self):
        spend = self.page.split("id=view-daily", 1)[1].split("id=view-learn", 1)[0]
        for marker in (
            "id=s-concentration", "id=s-concentration-bar", "id=s-shape",
            "id=s-scatter", "id=s-scatter-svg", "data-scatter-metric=cost",
            "data-scatter-metric=input", "data-spend-insight-session=",
        ):
            self.assertIn(marker, spend if marker.startswith("id=s-") or marker.startswith("data-scatter") else self.page)
        self.assertIn("function renderSpendSessionInsights(payload,window)", self.page)
        self.assertIn("selectSession(button.dataset.spendInsightSession)", self.page)

    def test_spend_monthly_budget_derives_total_from_runtime_budgets(self):
        for marker in (
            "id=spend-budgets", "class=spendBudgetSection", "class=spendBudgetEditor",
            "id=budget-spend", "id=budget-total", "id=budget-remaining",
            "id=budget-projected", "id=budget-runtimes",
            "id=budget-progress-markers", "id=budget-allocation-note",
            "id=budget-plan-jump",
            "class=budgetDashboard", "class=budgetLeadHeadActions",
            "class=\"card budgetLead\"",
            "class=\"card pad budgetRuntimeCard budgetConfig\"",
            "class=budgetLeadBody",
            "class=budgetLeadTop", "class=budgetFigureLine",
            "id=budget-spend-qualifier hidden>At least</span>",
            "class=budgetStats", "class=budgetMeterFoot", "id=budget-month",
            "$('budget-spend-qualifier').hidden=!status.lower_bound",
            "$('budget-spend').textContent=wholeMoney(status.spend||0)",
            "const wholeMoney=n=>",
            "function budgetMonthLabel(month)",
            "class=budgetRuntimeValue",
            "class=budgetRuntimeTrack",
            "class=budgetFormGroup", "class=budgetAlertInline",
            "class=\"budgetForm budgetInlineForm\"",
            "class=budgetRuntimeHead", "class=budgetRuntimeInput",
            "id=budget-input-claude", "id=budget-input-codex",
            "id=budget-input-cursor", "id=budget-input-opencode",
            "id=budget-input-threshold-early",
            "id=budget-runtime-spend-claude",
            "id=budget-runtime-spend-codex",
            "id=budget-runtime-spend-cursor",
            "id=budget-runtime-spend-opencode",
            "id=budget-runtime-track-claude",
            "id=budget-runtime-track-opencode",
            "/settings/budgets", "tm_monthly_budget_alerts",
            "Partial cost coverage: recorded spend is a lower bound.",
            "The sum of the registered runtime budgets.",
            "Runtime budgets are added to calculate the monthly total.",
            "function ensureBudgetRuntimeRows()",
            "planJump.textContent=open?'Close editor':budgetConfigured?'Edit budgets':'Set budgets'",
            "planJump.setAttribute('aria-expanded',String(open))",
            "aria-expanded=false aria-controls=budget-config>Set budgets</button>",
            "$('budget-plan-jump').onclick=()=>setBudgetEditorOpen($('budget-config').hidden,true)",
            "config.scrollIntoView({behavior:",
            "const input=config.querySelector('.budgetRuntimeInput input')",
            "input.focus({preventScroll:true})",
            ".budgetConfig[hidden]{display:none}",
            ".spendBudgetSection .budgetRuntimeFields{grid-column:2;grid-row:2}",
            "function budgetAllocationsFromInputs()",
            "function previewCalculatedBudget()",
            "const payload={currency:'USD',default_session_budget:",
            "per session by default · ${money(total)} per month.",
            "Save budgets",
            "Set budgets</h2>",
            "Spent this month</span><span>Budget (USD)",
            "DEFAULT_RUNTIME_BUDGET=0",
            "value=0",
        ):
            self.assertIn(marker, self.page)
        self.assertNotIn("class=budgetHero", self.page)
        self.assertNotIn("class=budgetGrid", self.page)
        self.assertNotIn("budgetConfigInitialized", self.page)
        self.assertNotIn("<details class=\"card budgetConfig\"", self.page)
        self.assertNotIn("<h2>Budget plan</h2>", self.page)
        self.assertNotIn("<span class=budgetRuntimeName>Unallocated</span>", self.page)
        self.assertNotIn("$('budget-runtimes').innerHTML", self.page)
        self.assertNotIn("id=tab-budgets", self.page)
        self.assertNotIn("id=view-budgets", self.page)
        self.assertNotIn("no runtime allocation", self.page)
        self.assertNotIn("id=budget-input-total", self.page)
        self.assertNotIn("Monthly total (USD)", self.page)
        self.assertNotIn("Runtime allocations cannot exceed", self.page)
        self.assertNotIn("budget-runtime-meta-", self.page)
        self.assertNotIn("const meta=allocated?", self.page)
        self.assertEqual(self.page.count(" id=budget-config hidden>"), 1)
        for removed in ("class=budgetSpendSummary", "class=budgetSpendLimit", "class=budgetReadouts>"):
            self.assertNotIn(removed, self.page)
        for removed in (
            "id=budget-bars", "id=budget-history-note", ">Monthly spend</h2>",
            "class=budgetDetailGrid", "budgetHistory", "budgetChartInner",
            "data-settings-target=budget-settings",
        ):
            self.assertNotIn(removed, self.page)
        self.assertNotRegex(self.page, r"id=budget-settings[\s>]")
        spend = self.page.split("id=view-daily", 1)[1].split("id=view-efficiency", 1)[0]
        self.assertLess(spend.index("<h1>Spend</h1>"), spend.index("id=spend-budgets"))
        self.assertLess(spend.index("id=spend-budgets"), spend.index("class=spendKpis"))
        self.assertLess(spend.index("class=\"card budgetLead\""), spend.index("id=budget-config"))
        settings = self.page.split("id=view-settings", 1)[1]
        self.assertNotIn("id=budget-form", settings)

    def test_current_surfaces_runtime_budget_overruns(self):
        for marker in (
            "id=current-budget-warning",
            "id=current-budget-warning-msg",
            "id=current-budget-settings",
            "Open monthly budget",
            "$('current-budget-settings').onclick=()=>setHashRoute('spend-budgets')",
            "function renderCurrentBudgetWarning(status)",
            "status?.exceeded_runtimes||[]",
            "renderCurrentBudgetWarning(s.xsession?.budget||LATEST?.xsession?.budget)",
            "runtimeAttention?`${runtimeExceeded.map(row=>row.label).join(', ')} over budget`",
            "row.exceeded===true",
        ):
            self.assertIn(marker, self.page)

    def test_spend_uses_session_routing_without_restoring_daily_wait_health(self):
        self.assertNotIn("Tool-result health", self.page)
        self.assertNotIn("Tool results need attention", self.page)
        self.assertNotIn("function openDailySession", self.page)
        self.assertNotIn("data-daily-session", self.page)
        self.assertIn("function renderSpend(xs)", self.page)
        self.assertIn("data-spend-session=", self.page)
        self.assertIn("if(row)selectSession(row.dataset.id)", self.page)

    def test_sessions_overview_shows_exact_day_spend_summary_and_tokenomics_link(self):
        for marker in (
            'class=currentDaySummary aria-label="Today\'s usage summary"',
            "id=current-day-spend",
            "id=current-day-sessions",
            "id=current-day-spend-change",
            "id=current-day-sessions-change",
            "Spend today",
            "Sessions today",
            "Month spend",
            "function renderCurrentDaySummary(xs)",
            "if(!Array.isArray(xs?.daily))",
            "const todayKey=localDayKey(),yesterdayDate=new Date()",
            "days.find(row=>row.day===todayKey)",
            "days.find(row=>row.day===yesterdayKey)",
            'href="https://www.splunk.com/en_us/products/tokenomics.html"',
            'target=_blank rel="noopener noreferrer"',
            'aria-label="Get Enterprise Tokenomics from Splunk (opens in a new tab)"',
            ">Get Enterprise Tokenomics<",
            "<button class=\"currentDayMetric currentDayMonth\" id=current-month-spend type=button",
            "id=current-month-spend-value",
        ):
            self.assertIn(marker, self.page)
        for removed in ("id=current-day-vs-yesterday", "Vs yesterday", ">View month spend<"):
            self.assertNotIn(removed, self.page)
        self.assertLess(self.page.index("class=currentDaySummary"), self.page.index("id=current-session-grid"))
        self.assertIn("renderCurrentDaySummary(state?.xsession);", self.page)
        self.assertIn(".currentDayReadouts{display:grid;grid-template-columns:repeat(3,minmax(0,1fr))", self.page)
        self.assertIn(".currentDayMetric:nth-child(3){grid-column:1/-1", self.page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_sessions_month_spend_action_selects_and_persists_month_before_routing(self):
        match = re.search(
            r"^function persistSpendRange\(\)[^\n]*\n"
            r"^function openMonthSpend\(\).*?^\}\n",
            self.page,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(match, "dashboard needs a month-spend action")
        script = """
const SPEND_RANGE_KEY='tm_spend_range_v1';
let spendRangeChoice='7',spendCustomFrom='2026-08-01',spendCustomTo='2026-08-03';
const writes=[],routes=[];
const localStorage={setItem(key,value){writes.push([key,JSON.parse(value)]);}};
const setHashRoute=route=>routes.push(route);
""" + match.group(0) + """
openMonthSpend();
console.log(JSON.stringify({spendRangeChoice,writes,routes}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "spendRangeChoice": "month",
            "writes": [["tm_spend_range_v1", {
                "range": "month",
                "from": "2026-08-01",
                "to": "2026-08-03",
            }]],
            "routes": ["spend"],
        })



# Spend's script with a recording DOM: every element write is counted, so a test can tell a skipped pass from a redraw.
SPEND_HARNESS = r"""
const fs=require('fs');
const page=fs.readFileSync(__PAGE__,'utf8');
function extract(name){let start=page.indexOf(`function ${name}(`);if(start<0)throw Error(`missing ${name}`);let i=page.indexOf('{',start),depth=0;for(;i<page.length;i++){if(page[i]==='{')depth++;else if(page[i]==='}'&&--depth===0)return page.slice(start,i+1);}throw Error(`unclosed ${name}`);}
(0,eval)(page.match(/^const (f|compactNumber|money|wholeMoney|pct|countWord|esc|metricAvailable|metricPartial|usageProvenance|usageBasis|hasLocalEstimate|estimateSuffix|COST_UNAVAILABLE_TIP)=.*$/gm).join('\n').replace(/^const /gm,'var '));
(0,eval)(['costValueHtml','setCostValue'].map(extract).join('\n'));
(0,eval)(page.slice(page.indexOf('// waffle-pure-start'),page.indexOf('// waffle-pure-end')).replace(/^const /gm,'var '));
var writes=0;
class El{
 constructor(id){this.id=id;this.dataset={};this.attrs={};this.found={};this.kids={};this.writes=0;this._text='';this._html='';this.hidden=false;this.value='';this.title='';this.clientWidth=0;this.clientHeight=0;
  const el=this,count=()=>{writes++;el.writes++;};this.count=count;
  this.style={props:{},setProperty(k,v){this.props[k]=String(v);count();},getPropertyValue(k){return this.props[k]||'';}};
  this.classList={set:new Set(),toggle(c,on){count();if(on===undefined)on=!this.set.has(c);on?this.set.add(c):this.set.delete(c);return on;},contains(c){return this.set.has(c);},add(c){count();this.set.add(c);},remove(c){count();this.set.delete(c);}};}
 get textContent(){return this._text;} set textContent(v){this._text=String(v);this.count();}
 get innerHTML(){return this._html;} set innerHTML(v){this._html=String(v);this.count();}
 setAttribute(k,v){this.attrs[k]=String(v);this.count();} getAttribute(k){return k in this.attrs?this.attrs[k]:null;} removeAttribute(k){if(k in this.attrs){delete this.attrs[k];this.count();}} hasAttribute(k){return k in this.attrs;}
 querySelector(sel){return sel==='i'||sel==='span'?(this.kids[sel]||=new El(sel)):this.found[sel]||null;} querySelectorAll(){return [];}
 closest(sel){return this.found['closest:'+sel]||null;} addEventListener(){} insertAdjacentHTML(){this.count();}
}
const els=new Map();
var $=id=>{if(!els.has(id))els.set(id,new El(id));return els.get(id);};
var document={activeElement:null,createElement:()=>new El('created'),querySelector:()=>null,querySelectorAll:()=>[]};
var localStorage={store:{},getItem(k){return this.store[k]??null;},setItem(k,v){this.store[k]=String(v);},removeItem(k){delete this.store[k];}};
var fetch=()=>new Promise(()=>{}),requestAnimationFrame=()=>0,LATEST=null,themeEpoch=1,themeFigures='bars';
var OTHER_LOCAL_SESSIONS_PROJECT='__other_local_sessions__',projectFilterLabel=value=>value,setHashRoute=()=>{},selectSession=()=>{};
var sessionRowKey=row=>row.id,sessionRoute=id=>'#'+id,CHART={lbl:'lbl',grid:'grid',ink:'ink'},tint=()=>'tint',waitFmt=String;
(0,eval)(fs.readFileSync(__SPEND__,'utf8').replace(/^(const|let) /gm,'var '));
const card=$('card');$('s-concentration-sessions').found['closest:.spendConcentrationCard']=card;
$('s-concentration-sessions').before=node=>{card.found['.spendConcentrationWaffles']=node;node.remove=()=>{delete card.found['.spendConcentrationWaffles'];};};
const dayOffset=offset=>{const date=new Date();date.setHours(12,0,0,0);date.setDate(date.getDate()-offset);return spendDayKey(date);};
"""


@unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
class SpendHiddenElementTests(unittest.TestCase):
    def test_every_hidden_spend_element_with_a_display_rule_stays_hidden(self):
        # A class rule that sets display overrides the [hidden] UA rule, so a dismissed discount cue left an
        # empty bordered strip in the budget card. Each such class needs its own [hidden]{display:none}.
        page = Path(DASHBOARD_PAGE).read_text()
        css = re.sub(r"/\*.*?\*/", "", page[page.index("<style>"):page.index("</style>")], flags=re.S)
        markup = (Path(__file__).resolve().parents[1] / "dashboard" / "markup" / "spend.html").read_text()
        offenders = []
        for tag in re.findall(r"<[a-z]+\b[^>]*\s(?<![-\w])hidden(?=[\s>])[^>]*>", markup):
            classes = re.search(r'class=(?:"([^"]+)"|([\w-]+))', tag)
            for name in (classes.group(1) or classes.group(2)).split() if classes else []:
                shown = re.search(r"(?:^|\n)\." + re.escape(name) + r"\{[^}]*display:(?!none)", css)
                guarded = re.search(r"(?:\." + re.escape(name) + r"|:where\(\." + re.escape(name) + r"\))\[hidden\]\{display:none", css)
                if shown and not guarded:
                    offenders.append(name)
        self.assertEqual(offenders, [])
        self.assertIn(".discountCue[hidden]{display:none}", css)


class SpendFrontierFigureTests(DashboardLayoutCase):
    def run_spend(self, body):
        script = (SPEND_HARNESS + body).replace("__PAGE__", json.dumps(str(DASHBOARD_PAGE))).replace(
            "__SPEND__", json.dumps(str(Path(__file__).resolve().parents[1] / "dashboard/js/spend.js")))
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_dot_themes_dot_only_measured_trend_segments_at_least_8px(self):
        out = self.run_spend(r"""
const row=(extra={})=>({day:'2026-11-10',granularity:'day',cost:200,hasActivity:true,providers:[{provider:'claude',cost:190},{provider:'codex',cost:10}],...extra});
const body=(r,count=30,width=1100)=>spendBarBody(r,0,count,400,width,false);
const segments=html=>[...html.matchAll(/<i class=spendStackSegment [^>]*>/g)].map(m=>m[0]);
const results={};
themeFigures='bars';spendTrackHeight=208;results.bars=segments(body(row()));
themeFigures='dots';results.dots=segments(body(row()));
results.estimated=segments(body(row({providers:[{provider:'claude',cost:190,usage_basis:'local_estimate'},{provider:'codex',cost:10}]})));
results.partial=segments(body(row({providers:[{provider:'claude',cost:190,coverage:{cost:{complete:false}}},{provider:'codex',cost:10}]})));
results.inProgress=segments(body(row({incomplete:true})));
results.narrow=segments(body(row(),120,1100));
spendTrackHeight=0;results.unmeasured=segments(body(row()));
console.log(JSON.stringify(results));
""")
        # Other themes keep today's markup byte for byte.
        self.assertEqual(out["bars"], [
            '<i class=spendStackSegment data-estimated="false" data-partial="false" style="--spend-color:var(--provider-claude);--segment-share:95%">',
            '<i class=spendStackSegment data-estimated="false" data-partial="false" style="--spend-color:var(--provider-codex);--segment-share:5%">',
        ])
        # 95% of a 104px bar is dotted; the 5.2px Codex sliver stays solid so it cannot vanish between dot rows.
        self.assertEqual(out["dots"], [
            '<i class=spendStackSegment data-estimated="false" data-partial="false" data-dots style="--spend-color:var(--provider-claude);--segment-share:95%">',
            out["bars"][1],
        ])
        # Estimates, partial coverage, a bucket still in progress, bars under 8px wide, and an unmeasured track stay solid.
        for case in ("estimated", "partial", "inProgress", "narrow", "unmeasured"):
            with self.subTest(case=case):
                self.assertFalse([segment for segment in out[case] if "data-dots" in segment])

    def test_trend_masks_follow_the_dot_rules(self):
        for rule in (
            ".spendStackSegment[data-dots]:not([data-estimated=true]):not([data-partial=true]){-webkit-mask:var(--data-mask-up);mask:var(--data-mask-up)}",
            ".spendDay::before{content:\"\";position:absolute;z-index:-1;",
            "background:var(--track-texture);background-position:50% 100%;pointer-events:none}",
            ".spendPlot{isolation:isolate}",
            ".spendLegend>span:not([class])>i{-webkit-mask:var(--data-mask);mask:var(--data-mask)}",
            ".spendLogTrack{background:rgb(var(--tint-rgb)/.045) var(--track-texture)}",
            ".spendPlatformTrack{background:rgb(var(--tint-rgb)/.045) var(--track-texture)}",
        ):
            with self.subTest(rule=rule[:60]):
                self.assertIn(rule, self.page)
        # The stack keeps its incomplete-bucket outline: it is never masked.
        self.assertNotRegex(self.page, r"\.spendDayStack[^{]*\{[^}]*mask:")

    def test_average_line_is_placed_by_css_without_reading_layout(self):
        self.assertIn(".spendAverageLine{top:calc(var(--spend-day-head) + var(--spend-day-gap) + (1 - var(--spend-average,0))*(var(--spend-chart-h) - var(--spend-day-head) - var(--spend-day-foot) - 2*var(--spend-day-gap)))}", self.page)
        average = self.page_function(self.page, "renderSpendAverage")
        self.assertNotIn("getBoundingClientRect", average)
        self.assertNotIn("style.top", average)
        out = self.run_spend(r"""
$('s-chart-inner').found['.spendDayTrack']=new El('track');
const line=$('s-average-line'),rows=[{granularity:'day',cost:1},{granularity:'day',cost:3}];
renderSpendAverage(rows,4);const first={ratio:line.style.getPropertyValue('--spend-average'),label:line.querySelector('span').textContent,hidden:line.hidden};
writes=0;renderSpendAverage(rows,4);const again=writes;
renderSpendAverage([{granularity:'day',cost:0}],4);
console.log(JSON.stringify({first,again,emptyHidden:line.hidden}));
""")
        self.assertEqual(out["first"], {"ratio": "0.5", "label": "avg $2.00/day", "hidden": False})
        self.assertEqual(out["again"], 0)
        self.assertTrue(out["emptyHidden"])

    def test_repeated_polls_skip_the_spend_pass_until_something_it_draws_changes(self):
        out = self.run_spend(r"""
$('s-chart-inner').clientWidth=1100;
const days=[0,1,2,3].map(offset=>({day:dayOffset(offset),cost:10+offset,providers:[{provider:'claude',cost:10+offset}],coverage:{cost:{complete:true}},availability:{cost:true}}));
const xs={generated_at:1,spend:{days}};
const pass=()=>{writes=0;renderSpend(xs);return writes;};
const first=pass(),legend=$('s-legend').writes,platforms=$('s-platforms').writes;
const repeat=pass();
themeEpoch++;const epoch=pass();
const legendAfter=$('s-legend').writes,platformsAfter=$('s-platforms').writes;
$('s-chart-inner').clientWidth=900;const resized=pass();
spendGranularityChoice='week';const grouping=pass();
xs.spend.days=days.map(row=>({...row,cost:row.cost+1}));const evidence=pass();
console.log(JSON.stringify({first,repeat,epoch,legend,legendAfter,platforms,platformsAfter,resized,grouping,evidence}));
""")
        self.assertGreater(out["first"], 0)
        # Nothing it draws changed: the poll does no DOM work at all.
        self.assertEqual(out["repeat"], 0)
        # A theme commit redraws, but unchanged legend and platform markup is not rewritten.
        self.assertGreater(out["epoch"], 0)
        self.assertEqual((out["legendAfter"], out["platformsAfter"]), (out["legend"], out["platforms"]))
        # A new size, a grouping choice, or new evidence each redraw.
        for case in ("resized", "grouping", "evidence"):
            with self.subTest(case=case):
                self.assertGreater(out[case], 0)

    def test_dot_themes_draw_concentration_as_a_waffle_pair(self):
        out = self.run_spend(r"""
const rows=Array.from({length:20},(_,i)=>({id:'s'+i,cost:i<2?50:1,availability:{cost:true},usage_basis:'reported'}));
const last={concentration:spendSessionAnalytics(rows).concentration,rows};
const figure=()=>card.found['.spendConcentrationWaffles']||null;
const paths=html=>[...html.matchAll(/<path class=(\w+) style="color:([^"]*)" d="([^"]*)"\/>/g)].map(m=>[m[1],m[2],(m[3].match(/M/g)||[]).length]);
const labels=html=>[...html.matchAll(/aria-label="([^"]*)"/g)].map(m=>m[1]);
themeFigures='bars';renderSpendConcentrationFigure(last);const bars={figure:Boolean(figure()),attr:card.dataset.figure||null,writes:card.writes};
themeFigures='dots';renderSpendConcentrationFigure(last);const html=figure().innerHTML;
const dots={attr:card.dataset.figure,paths:paths(html),labels:labels(html),captions:[...html.matchAll(/<figcaption class=waffleUnit>([^<]*)<\/figcaption>/g)].map(m=>m[1]),titles:[...html.matchAll(/<span class=label aria-hidden=true>(\w+)<\/span>/g)].map(m=>m[1])};
writes=0;renderSpendConcentrationFigure(last);const repeat=writes;
const estimatedRows=rows.map((row,i)=>i===0?{...row,usage_basis:'local_estimate'}:row);
renderSpendConcentrationFigure({concentration:spendSessionAnalytics(estimatedRows).concentration,rows:estimatedRows});const estimated=paths(figure().innerHTML);
const few=rows.slice(0,9);renderSpendConcentrationFigure({concentration:spendSessionAnalytics(few).concentration,rows:few});const fewSessions={figure:Boolean(figure()),attr:card.dataset.figure||null};
renderSpendConcentrationFigure(last);themeFigures='bars';renderSpendConcentrationFigure(last);const back={figure:Boolean(figure()),attr:card.dataset.figure||null};
console.log(JSON.stringify({bars,dots,repeat,estimated,fewSessions,back}));
""")
        # Other themes never get the figure: no markup, no attribute, no write.
        self.assertEqual(out["bars"], {"figure": False, "attr": None, "writes": 0})
        dots = out["dots"]
        self.assertEqual(dots["attr"], "waffle")
        # 2 of 20 sessions are the top decile (10 dots); they hold $100 of $118 (85 dots). The rest is --faint.
        self.assertEqual(dots["paths"], [
            ["waffleDot", "var(--c-cost)", 10], ["waffleDot", "var(--faint)", 90],
            ["waffleDot", "var(--c-cost)", 85], ["waffleDot", "var(--faint)", 15],
        ])
        self.assertEqual(dots["labels"], ["Share of sessions: Top decile 10.0%, Other sessions 90.0%", "Share of spend: Top decile 84.7%, Other sessions 15.3%"])
        self.assertEqual(dots["captions"], ["Each dot is 1% of sessions.", "Each dot is 1% of spend."])
        self.assertEqual(dots["titles"], ["Sessions", "Spend"])
        self.assertEqual(out["repeat"], 0)
        # A local estimate in the top decile draws that part as rings, never as measured dots.
        self.assertEqual(out["estimated"][2][0], "waffleRing")
        # Fewer than 10 covered sessions, or switching back to a bars theme, removes the figure and keeps the bar.
        self.assertEqual(out["fewSessions"], {"figure": False, "attr": None})
        self.assertEqual(out["back"], {"figure": False, "attr": None})
        for rule in (
            ".spendConcentrationCard[data-figure=waffle] :is(.spendConcentrationBar,.spendConcentrationLegend){display:none}",
            ".spendConcentrationValue{font-family:var(--font-readout);font-weight:var(--fw-readout)}",
        ):
            self.assertIn(rule, self.page)
        self.assertNotIn("spendConcentrationWaffles", self.page.split("id=view-daily", 1)[1].split("id=view-efficiency", 1)[0])


    def gauge_zone_dots(self, low, high, start, end):
        """The deck's dial computed independently: 6px cells over the 224x112 half box, Bayer 4x4 thresholds."""
        import math
        bayer = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]
        dots = []
        for j in range(19):
            for i in range(38):
                x, y = i * 6 + 3, j * 6 + 3
                dx, dy = x - 112, 112 - y
                distance, at = math.hypot(dx, dy), 1 - math.atan2(dy, dx) / math.pi
                if distance < 76 or distance > 112 or at < low or at >= high:
                    continue
                if start + (end - start) * (at - low) / (high - low) > (bayer[j & 3][i & 3] + .5) / 16:
                    dots.append([x + 15, y + 15])
        return dots

    def test_dot_theme_budget_gauge_draws_the_deck_dial(self):
        out = self.run_spend(r"""
(0,eval)(page.slice(page.indexOf('// dither-pure-start'),page.indexOf('// dither-pure-end')).replace(/^const /gm,'var '));
(0,eval)(fs.readFileSync(__SPEND__.replace('spend.js','budgets-discounts.js'),'utf8').match(/\/\/ budget-gauge-pure-start[\s\S]*\/\/ budget-gauge-pure-end/)[0].replace(/^(const|let) /gm,'var '));
const parse=html=>({
 svg:html.slice(0,html.indexOf('>')+1),
 zones:[...html.matchAll(/<path class=budgetGaugeZone style="color:([^"]*)" d="([^"]*)"\/>/g)].map(m=>({color:m[1],dots:[...m[2].matchAll(/M([\d.]+) ([\d.]+)a([\d.]+)/g)].map(d=>[+d[1]+ +d[3],+d[2]])})),
 rim:[...(html.match(/class=budgetGaugeRim d="([^"]*)"/)||[,''])[1].matchAll(/M([\d.]+) ([\d.]+)a([\d.]+)/g)].map(d=>+d[3]),
 needle:(html.match(/<line class=budgetGaugeNeedle x1="([\d.]+)" y1="([\d.]+)" x2="([\d.]+)" y2="([\d.]+)"\/>/)||[]).slice(1).map(Number),
 hubs:[...html.matchAll(/<circle class=budgetGaugeHub cx="([\d.]+)" cy="([\d.]+)" r="([\d.]+)"\/>/g)].map(m=>m.slice(1).map(Number)),
 projection:(html.match(/<circle class=budgetGaugeProjection cx="([\d.]+)" cy="([\d.]+)" r="4"\/>/)||[]).slice(1).map(Number),
});
const status={configured:true,budget:1000,spend:150,percent:.15,projection_ready:true,projected_spend:180};
console.log(JSON.stringify({
 track:parse(budgetGaugeHtml({ratio:.15,threshold:.7,projected:.18})),
 over:parse(budgetGaugeHtml({ratio:1.3,threshold:.7,projected:null})),
 input:budgetGaugeInput(status,[70,90,100],.15),
 none:[budgetGaugeInput({...status,configured:false},[70],.15),budgetGaugeInput({...status,budget:0},[70],.15),budgetGaugeInput({...status,spend:null},[70],.15)],
 unprojected:budgetGaugeInput({...status,projection_ready:false},[],.15),
}));
""")
        track = out["track"]
        self.assertEqual(track["svg"], "<svg class=budgetGauge viewBox=\"0 0 254 135\" width=254 height=135 aria-hidden=true focusable=false>")
        # Value blue from 0 to the first alert threshold, cost orange from there to 100%, each denser toward its end.
        self.assertEqual([zone["color"] for zone in track["zones"]], ["var(--c-output)", "var(--c-cost)"])
        self.assertEqual(track["zones"][0]["dots"], self.gauge_zone_dots(0, .7, .35, .75))
        self.assertEqual(track["zones"][1]["dots"], self.gauge_zone_dots(.7, 1.0001, .7, 1))
        # 21 rim ticks, every fifth larger.
        self.assertEqual(track["rim"], [2.5 if k % 5 == 0 else 1.5 for k in range(21)])
        # The --fg needle points at spent / budget; the hollow rim dot marks the projected month.
        import math
        self.assertEqual(track["needle"], [127, 127, round(127 + 104 * math.cos(math.pi * .85), 1), round(127 - 104 * math.sin(math.pi * .85), 1)])
        self.assertEqual(track["projection"], [round(127 + 121 * math.cos(math.pi * .82), 1), round(127 - 121 * math.sin(math.pi * .82), 1)])
        self.assertEqual(track["hubs"], [[127, 127, 6]])
        # Over budget the needle holds at 100% with a cap dot; no projection, no hollow dot.
        self.assertEqual(out["over"]["needle"], [127, 127, 231, 127])
        self.assertEqual(out["over"]["hubs"], [[231, 127, 4.5], [127, 127, 6]])
        self.assertEqual(out["over"]["projection"], [])
        self.assertEqual(out["input"], {"ratio": .15, "threshold": .7, "projected": .18})
        # No budget, or no spend evidence: no gauge, so the page keeps the bar and its evidence text.
        self.assertEqual(out["none"], [None, None, None])
        self.assertEqual(out["unprojected"], {"ratio": .15, "threshold": 1, "projected": None})

    def test_budget_gauge_markup_exists_only_in_dot_themes_and_redraws_on_change(self):
        out = self.run_spend(r"""
(0,eval)(page.slice(page.indexOf('// dither-pure-start'),page.indexOf('// dither-pure-end')).replace(/^const /gm,'var '));
(0,eval)(fs.readFileSync(__SPEND__.replace('spend.js','budgets-discounts.js'),'utf8').match(/\/\/ budget-gauge-pure-start[\s\S]*?function renderBudgets\(/)[0].replace(/function renderBudgets\($/,'').replace(/^(const|let) /gm,'var '));
const progress=$('budget-progress'),host=$('budget-spend-host');progress.parentElement=host;
let svg=null;progress.insertAdjacentHTML=(where,html)=>{progress.count();svg={html,remove(){svg=null;}};Object.defineProperty(svg,'outerHTML',{set(value){progress.count();svg.html=value;}});};
progress.found['.budgetGauge']=null;Object.defineProperty(progress.found,'.budgetGauge',{get:()=>svg});
const gauge=ratio=>({ratio,threshold:.7,projected:.2});
const step=input=>{writes=0;renderBudgetFigure(input);return {writes,figure:host.dataset.figure||null,svg:Boolean(svg)};};
console.log(JSON.stringify({
 bars:step(null),
 first:step(gauge(.15)),
 same:step(gauge(.15)),
 tiny:step(gauge(.1504)),
 moved:step(gauge(.2)),
 epoch:(themeEpoch++,step(gauge(.2))),
 back:step(null),
}));
""")
        # A bars theme never writes: no attribute, no gauge.
        self.assertEqual(out["bars"], {"writes": 0, "figure": None, "svg": False})
        self.assertEqual(out["first"], {"writes": 1, "figure": "gauge", "svg": True})
        # Unchanged inputs, or a change below a tenth of a percent, do not redraw.
        self.assertEqual(out["same"]["writes"], 0)
        self.assertEqual(out["tiny"]["writes"], 0)
        self.assertEqual(out["moved"]["writes"], 1)
        self.assertEqual(out["epoch"]["writes"], 1)
        self.assertEqual(out["back"], {"writes": 0, "figure": None, "svg": False})
        for rule in (
            ".budgetSpend[data-figure=gauge] .budgetProgress>:is(i,.budgetProgressMarkers){display:none}",
            ".spendBudgetSection .budgetSpend[data-figure=gauge] .budgetProgress{order:-1;",
            ".spendBudgetSection .budgetSpendValue{font-family:var(--font-readout);font-weight:var(--fw-readout)}",
            ".budgetStats dd.mono{font-family:var(--font-readout);font-weight:var(--fw-readout)}",
            ".budgetStats dt{font-family:var(--font-label);font-weight:var(--fw-label)}",
            ".budgetRuntimeTrack{background:rgb(var(--tint-rgb)/.05) var(--track-texture)}",
        ):
            self.assertIn(rule, self.page)
        # The wrapper keeps its progressbar role and values; the gauge itself is hidden from assistive tech.
        self.assertIn('id=budget-progress role=progressbar aria-label="Monthly budget used" aria-valuemin=0 aria-valuemax=100 aria-valuenow=0>', self.page)
        self.assertNotIn("budgetGauge", self.page.split("id=view-daily", 1)[1].split("id=view-efficiency", 1)[0])


if __name__ == "__main__":
    unittest.main()
