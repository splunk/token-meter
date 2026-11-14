import unittest
import json
import re
import shutil
import subprocess
from tests.dashboard_layout_support import DashboardLayoutCase


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
            "id=current-day-vs-yesterday",
            "Spend today",
            "Sessions today",
            "Vs yesterday",
            "function renderCurrentDaySummary(xs)",
            "if(!Array.isArray(xs?.daily))",
            "const todayKey=localDayKey(),yesterdayDate=new Date()",
            "days.find(row=>row.day===todayKey)",
            "days.find(row=>row.day===yesterdayKey)",
            'href="https://www.splunk.com/en_us/products/tokenomics.html"',
            'target=_blank rel="noopener noreferrer"',
            'aria-label="Get Enterprise Tokenomics from Splunk (opens in a new tab)"',
            ">Get Enterprise Tokenomics<",
            "id=current-month-spend",
            ">View month spend<",
        ):
            self.assertIn(marker, self.page)
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


if __name__ == "__main__":
    unittest.main()
