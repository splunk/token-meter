import unittest
import datetime
import http.client
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock
import meter


class MenubarSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path(meter.__file__).with_name("menubar").joinpath("TokenMeterMenuBar.swift").read_text()

    def test_spend_action_opens_cross_session_spend_route(self):
        self.assertIn('case .spend: return "Spend"', self.source)
        self.assertIn('case .spend:\n                self.openDailyBrief()', self.source)
        self.assertIn('@objc private func openDailyBrief()', self.source)
        self.assertIn('openDashboardPanel("spend", includePinnedSession: false)', self.source)

    def test_dashboard_opens_sessions_unless_a_session_is_pinned(self):
        self.assertIn(
            'tokenMeterDashboardURL = URL(string: "http://127.0.0.1:8722/#sessions")',
            self.source,
        )
        self.assertIn("if pinnedSessionID?.isEmpty == false", self.source)
        self.assertIn('openDashboardPanel("summary")', self.source)
        self.assertIn('openDashboardPanel("sessions", includePinnedSession: false)', self.source)

    def test_model_prices_setting_opens_dashboard_pricing_editor(self):
        self.assertIn('NSMenuItem(title: "Model Prices", action: #selector(openModelPrices)', self.source)
        self.assertIn('@objc private func openModelPrices()', self.source)
        self.assertIn('openDashboardPanel("model-pricing", includePinnedSession: false)', self.source)

    def test_enterprise_tokenomics_is_a_top_level_native_action_below_settings(self):
        self.assertIn(
            'private let tokenMeterEnterpriseTokenomicsURL = URL(string: "https://www.splunk.com/en_us/products/tokenomics.html")!',
            self.source,
        )
        rebuild = self.source[
            self.source.index("    private func rebuildMenu()"):
            self.source.index("    private var activeShortcutKeyCode")
        ]
        self.assertIn(
            'NSMenuItem(title: "Get Enterprise Tokenomics", action: #selector(openEnterpriseTokenomics)',
            rebuild,
        )
        self.assertLess(
            rebuild.index("menu.addItem(settingsItem)"),
            rebuild.index('NSMenuItem(title: "Get Enterprise Tokenomics"'),
        )
        self.assertIn('@objc private func openEnterpriseTokenomics()', self.source)
        self.assertIn('NSWorkspace.shared.open(tokenMeterEnterpriseTokenomicsURL)', self.source)

    def test_native_menu_does_not_advertise_removed_session_trace_view(self):
        for marker in (
            'NSMenuItem(title: "Open Trace"', '#selector(openTrace)',
            '@objc private func openTrace()', 'openDashboardPanel("activity")',
        ):
            self.assertNotIn(marker, self.source)

    def test_output_speed_remains_available_without_a_standing_menu_row(self):
        rebuild = self.source[
            self.source.index("    private func rebuildMenu()"):
            self.source.index("    private var activeShortcutKeyCode")
        ]
        self.assertNotIn('addMetricRow("Output', rebuild)
        self.assertIn(r'· \(outputSpeedLabel) · \(model)', self.source)
        self.assertIn('formatTokenRate(rate)', self.source)
        self.assertIn(
            'return "\\(formatTokenRate(rate)) tok/s\\(estimatedTokens ? " est" : "")"',
            self.source,
        )
        self.assertNotIn('tok/s\\(live)', self.source)
        self.assertIn('print(snapshot.outputSpeedLabel)', self.source)

    def test_selected_session_cost_and_context_are_not_menu_rows(self):
        rebuild = self.source[
            self.source.index("    private func rebuildMenu()"):
            self.source.index("    private var activeShortcutKeyCode")
        ]
        self.assertIn('return "\\(costLabel) · \\(contextLabel) · \\(outputSpeedLabel) · \\(model)"', self.source)
        self.assertNotIn('return "\\(verdict.prefix) \\(formatMoney(totalCost))', self.source)
        self.assertNotIn('addMetricRow("Cost", snapshot.costLabel', rebuild)
        self.assertNotIn('"Context",\n                contextDetail', rebuild)
        self.assertNotIn('addMetricRow("Status"', rebuild)
        self.assertNotIn('"Now"', rebuild)
        self.assertNotIn('"Action"', rebuild)

    def test_cursor_uses_provider_identity_and_estimated_usage_labels(self):
        self.assertIn('catalog[provider] ?? catalog["unknown-runtime"]', self.source)
        self.assertIn('case "runtime.cursor": return "cursorarrow"', self.source)
        self.assertIn('let costAvailable = metricAvailable(availability, "cost")', self.source)
        self.assertIn('let tokensAvailable = metricAvailable(availability, "tokens")', self.source)
        self.assertIn('let estimatedTokens = bool(source["token_estimate"])', self.source)
        self.assertIn('var menuBarTodaySpendLabel: String', self.source)

    def test_live_polling_bypasses_cached_menubar_responses(self):
        self.assertIn('cachePolicy: .reloadIgnoringLocalCacheData', self.source)
        self.assertIn('request.setValue("no-cache", forHTTPHeaderField: "Cache-Control")', self.source)
        self.assertIn('request.setValue("no-cache", forHTTPHeaderField: "Pragma")', self.source)

    def test_native_menu_surfaces_updates_and_installs_without_the_dashboard(self):
        for marker in (
            "struct SoftwareUpdateSnapshot",
            'dict["software_update"] as? [String: Any]',
            'title: "New update available",',
            'action: #selector(installSoftwareUpdate),',
            'NSMenuItem(title: "Updating Token Meter...", action: nil',
            'title: "Update needs attention",',
            'action: #selector(openUpdateSettings),',
            '@objc private func installSoftwareUpdate()',
            'request.httpMethod = "POST"',
            'request.setValue("application/json", forHTTPHeaderField: "Content-Type")',
            'request.setValue(softwareUpdate.actionToken, forHTTPHeaderField: "X-Token-Meter-Action")',
            'request.httpBody = Data("{}".utf8)',
            '@objc private func openUpdateSettings()',
            'tokenMeterUpdateSettingsURL',
        ):
            self.assertIn(marker, self.source)
        rebuild = self.source[
            self.source.index("    private func rebuildMenu()"):
            self.source.index("    private var activeShortcutKeyCode")
        ]
        self.assertLess(
            rebuild.index("addQuickActions()"),
            rebuild.index("addSoftwareUpdateItem()"),
        )
        self.assertLess(
            rebuild.index("addSoftwareUpdateItem()"),
            rebuild.index("addSessionPicker()"),
        )
        self.assertIn("if self.softwareUpdate.isUpdating", self.source)
        self.assertIn('print("native-update=available updating attention")', self.source)

    def test_status_item_owns_a_clean_native_menu(self):
        app = self.source[
            self.source.index("final class TokenMeterMenuBar"):
            self.source.index("private func string(")
        ]
        self.assertIn('private let menu = NSMenu(title: "Token Meter")', app)
        self.assertIn('statusItem.menu = menu', app)
        self.assertIn('menu.delegate = self', app)
        self.assertIn('func menuNeedsUpdate(_ menu: NSMenu)', app)
        self.assertIn('func menuWillOpen(_ menu: NSMenu)', app)
        self.assertIn('func menuDidClose(_ menu: NSMenu)', app)
        self.assertIn('private var menuRefreshPending = false', app)
        self.assertIn('guard !menuIsOpen else', app)
        self.assertIn('func runMenuSmoke() throws', app)
        self.assertIn('TOKEN_METER_MENUBAR_MENU_SMOKE', self.source)
        self.assertIn('native-menu=ready sessions=direct follow-latest=direct', self.source)
        self.assertIn('native-quick-actions=Dashboard,Spend,Tools,Settings height=26', self.source)
        self.assertNotIn('private let popover = NSPopover()', app)
        self.assertNotIn('popover.show(relativeTo: button.bounds', app)
        self.assertIn('NSSegmentedControl', app)

    def test_native_menu_exposes_direct_session_following(self):
        self.assertIn('let selectedSessionID = string(selection["selected_id"])', self.source)
        self.assertIn('let selectedSession = recentSessions.first { $0.id == snapshot.selectedSessionID }', self.source)
        self.assertIn('NSMenuItem(title: "Follow latest", action: #selector(followLatest)', self.source)
        self.assertIn('for session in recentSessions.prefix(5)', self.source)
        self.assertIn('NSMenuItem(title: session.menuTitle, action: #selector(pinSession(_:))', self.source)
        self.assertIn('item.state = pinnedSessionID == session.id ? .on : .off', self.source)
        self.assertIn('item.representedObject = session.id', self.source)
        self.assertIn('@objc private func followLatest()', self.source)
        self.assertIn('@objc private func pinSession(_ sender: NSMenuItem)', self.source)
        self.assertIn('let maximumNameLength = 36', self.source)

    def test_provider_limits_and_budgets_use_separate_native_submenus(self):
        self.assertIn('NSMenuItem(title: "Provider limits (Beta)", action: nil', self.source)
        self.assertIn('description: "Provider limits (Beta)"', self.source)
        self.assertIn('limitsItem.submenu = makeLimitsMenu()', self.source)
        self.assertIn('private func makeLimitsMenu() -> NSMenu', self.source)
        self.assertIn('for window in provider.windows', self.source)
        self.assertIn('"\\(window.label) · \\(window.percentLabel) used"', self.source)
        self.assertIn('coverageNote: string(dict["coverage_note"]) ?? ""', self.source)
        self.assertIn('coverage=\\(coverage)', self.source)
        self.assertIn('case "opencode": return "gearshape.2"', self.source)
        self.assertIn('let budget = double(row["allocation"])', self.source)
        self.assertIn('NSMenuItem(title: "Budgets", action: nil', self.source)
        self.assertIn('budgetsItem.submenu = makeBudgetsMenu()', self.source)
        self.assertIn('private func makeBudgetsMenu() -> NSMenu', self.source)
        self.assertIn('let overallTitle = budget.configured', self.source)
        self.assertIn('"Overall · \\(formatMoney(budget.spend)) of \\(formatMoney(budget.budget)) · \\(Int(budget.percent.rounded()))% used"', self.source)
        self.assertIn('for scope in budget.scopes where scope.id != "overall"', self.source)
        self.assertIn('"\\(scope.label) · \\(formatMoney(scope.spend)) of \\(formatMoney(scope.budget)) · \\(Int(scope.percent.rounded()))% used"', self.source)
        self.assertIn('"\\(scope.label) · Not set"', self.source)
        self.assertIn('action: #selector(openBudgetSettings)', self.source)
        self.assertIn('Native budgets omitted the OpenCode monthly budget.', self.source)
        self.assertIn('overallBudgetItems.count == 1', self.source)
        self.assertIn('budgetActionItems.count == runtimeBudgetScopes.count + 1', self.source)

        limits = self.source[
            self.source.index("    private func makeLimitsMenu()"):
            self.source.index("    private func makeBudgetsMenu()")
        ]
        self.assertNotIn("monthlyBudget", limits)
        self.assertNotIn("budgetScope", limits)
        self.assertNotIn("openBudgetSettings", limits)

    def test_menu_bar_settings_are_visible_and_quota_threshold_is_explicit(self):
        self.assertIn('case .settings: return "Settings"', self.source)
        self.assertIn('case .settings:\n                self.openSettings()', self.source)
        self.assertIn('NSMenuItem(title: "Menu bar settings", action: nil', self.source)
        self.assertIn('settingsItem.submenu = makeSettingsMenu()', self.source)
        self.assertNotIn('NSMenuItem(title: "More", action: nil', self.source)
        self.assertIn('NSMenuItem(title: "Open Settings", action: #selector(openSettings)', self.source)
        self.assertIn('addAction("Quit Token Meter", #selector(quit))', self.source)
        self.assertNotIn('Quit Token Meter Menubar', self.source)
        self.assertIn('NSMenuItem(title: "Quota alert threshold (\\(quotaAlertThreshold)%)"', self.source)
        self.assertIn('NSMenu(title: "Quota alert threshold")', self.source)
        self.assertIn('title: "\\(threshold)% used",', self.source)
        self.assertNotIn('NSMenuItem(title: "Warn at"', self.source)

    def test_configurable_title_and_quota_notifications_have_safe_defaults(self):
        self.assertIn('enum TitleMetric: String, CaseIterable', self.source)
        self.assertIn('metrics = [.cost, .speed]', self.source)
        self.assertIn('for metric in TitleMetric.allCases', self.source)
        self.assertIn('#selector(toggleTitleMetric(_:))', self.source)
        self.assertIn('TitleMetric.allCases.filter(titleMetrics.contains).map(\\.rawValue)', self.source)
        self.assertIn('let presentation = selectedStatusTitlePresentation()', self.source)
        self.assertIn('let title = presentation.accessibilityTitle', self.source)
        self.assertIn('let text = snapshot.menuBarCostLabel', self.source)
        self.assertIn('let text = snapshot.menuBarOutputSpeedLabel', self.source)
        self.assertIn('let text = snapshot.menuBarContextLabel', self.source)
        self.assertIn('let text = snapshot.model', self.source)
        self.assertIn('case .todaySpend:', self.source)
        self.assertIn('let text = snapshot.menuBarTodaySpendLabel', self.source)
        self.assertIn('case .todaySpend: return "Today spend"', self.source)
        self.assertIn(
            'return "\\(String(format: "$%.0f", todaySpendTotalCost))\\(todaySpendEstimated ? " est" : "")"',
            self.source,
        )
        self.assertIn('parts[parts.count - 1].text = "· " + parts[parts.count - 1].text', self.source)
        self.assertIn('metrics.insert(.todaySpend)', self.source)
        self.assertIn('todaySpendTitlePreferenceDefaultsKey', self.source)
        self.assertIn('todaySpendEstimated ? " est" : ""', self.source)
        self.assertIn('metrics = [.limits]', self.source)
        self.assertIn(
            'tokenMeterDefaults.set(true, forKey: todaySpendTitlePreferenceDefaultsKey)',
            self.source,
        )
        self.assertIn('let accessibilityText = limitsStatusTitle()', self.source)
        self.assertIn('symbol: runtimeCatalog[constrained.provider.id]?.symbol', self.source)
        self.assertNotIn('private func compactStatusTitle()', self.source)
        self.assertIn('private func limitsStatusTitle() -> String?', self.source)
        self.assertIn('return "\\(constrained.provider.label) \\(constrained.window.percentLabel)"', self.source)
        self.assertNotIn('constrained.window.compactKind', self.source)
        self.assertIn('Native menu still exposes hover tooltips.', self.source)
        self.assertIn('guard statusItem.button?.toolTip == nil, !menuContainsToolTip(menu)', self.source)
        self.assertIn('private var quotaAlertsEnabled: Bool { notificationFeed?.quotaAlerts ?? true }', self.source)
        self.assertIn('saveQuotaAlertPreferences(enabled: !quotaAlertsEnabled, threshold: quotaAlertThreshold)', self.source)
        self.assertIn('process.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")', self.source)
        self.assertIn('"--", title, body', self.source)

    def test_native_companion_only_claims_and_shows_server_notifications(self):
        for removed in (
            'evaluateQuotaNotifications', 'evaluateBudgetNotifications', 'evaluateLiveHintNotifications',
            'struct QuotaNotificationState', 'struct BudgetNotificationState', 'firedThresholds',
            'quotaObservationEstablished', 'deliveredLiveHintIDs', 'dict["live_hints"]',
        ):
            self.assertNotIn(removed, self.source)
        for marker in (
            'struct NotificationFeed', 'optionalDouble(dict["schema_version"]).map(Int.init) == 1',
            'NotificationFeed.fromJSON(dict["notifications"] as? [String: Any])',
            'private let notificationConsumer = "token-meter-menubar"',
            'body: ["id": event.id, "consumer": notificationConsumer]',
            'if ok, bool(response?["claimed"]) {',
            'showNotification(title: event.title, body: event.body)',
            '"http://127.0.0.1:8722/notifications/claim"', '"http://127.0.0.1:8722/settings/notifications"',
            'body: ["quota_alerts": enabled, "quota_threshold": threshold, "migrate": true]',
            'legacyNotificationDefaultsKeys.forEach(tokenMeterDefaults.removeObject(forKey:))',
            '"TokenMeterQuotaNotificationStates", "TokenMeterBudgetNotificationStates"',
            '"TokenMeterBudgetExceededNotificationMonths", "TokenMeterLiveHintNotificationIDs"',
            'sampleFeed.deliverable.map(\\.id) == ["e5", "e4", "e3", "e2", "e1"]',
            'events.filter(\\.claimable)',
            'action: canEditQuotaAlerts ? #selector(toggleQuotaAlerts(_:)) : nil',
        ):
            self.assertIn(marker, self.source)

    def test_status_item_uses_a_compact_cross_display_hit_target(self):
        self.assertIn('NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)', self.source)
        self.assertIn('private func splunkChevronImage(', self.source)
        self.assertIn('private func statusTitleImage(_ presentation: StatusTitlePresentation) -> NSImage', self.source)
        self.assertIn('private func runtimeMarkImage(symbol: String', self.source)
        self.assertIn('var providerSymbol: String', self.source)
        self.assertIn('var providerAccessibilityText: String', self.source)
        self.assertIn('symbol: presentation.providerSymbol', self.source)
        self.assertIn('let providerSymbol = runtimeCatalog[snapshot.provider]?.symbol ?? "runtime.generic"', self.source)
        self.assertIn('let presentationWithoutLimits = selectedStatusTitlePresentation()', self.source)
        self.assertIn('presentationWithoutLimits.providerSymbol == expectedProviderSymbol', self.source)
        self.assertIn('button.image = splunkChevronImage()', self.source)
        self.assertNotIn('systemSymbolName: "waveform.path.ecg"', self.source)
        self.assertIn('button.imagePosition = .imageOnly', self.source)
        self.assertIn('.foregroundColor: NSColor.black', self.source)
        self.assertIn('image.isTemplate = true', self.source)
        self.assertIn('let titleImage = statusTitleImage(presentation)', self.source)
        self.assertIn('statusItem.length = titleImage.size.width + 8', self.source)
        self.assertIn('button.contentTintColor = nil', self.source)
        self.assertIn('button.setAccessibilityLabel("Token Meter")', self.source)
        self.assertIn('button.setAccessibilityValue(title)', self.source)
        self.assertNotIn('button.toolTip =', self.source)
        self.assertIn('button.contentTintColor = budgetExceeded', self.source)
        self.assertIn('enum StatusDisplayMode: String, CaseIterable', self.source)
        self.assertIn('StatusDisplayMode.text.rawValue', self.source)
        self.assertIn('screen.visibleFrame.width < 1200 ? .icon : .text', self.source)
        self.assertIn('private func selectedStatusTitle() -> String', self.source)
        self.assertIn('let renderedTitle = statusItem.button?.accessibilityValue() as? String', self.source)
        self.assertIn('renderedTitle == expectedTitle', self.source)
        self.assertIn('titleMetrics = Set(TitleMetric.allCases)', self.source)
        self.assertIn('titleMetrics = originalTitleMetrics', self.source)
        self.assertIn('statusItem.autosaveName = statusItemAutosaveName', self.source)
        self.assertIn('"NSStatusItem Preferred Position \\(statusItemAutosaveName)"', self.source)
        self.assertIn('statusItemInitialPreferredPosition = 50', self.source)
        self.assertIn('object(forKey: statusItemPreferredPositionDefaultsKey) == nil', self.source)
        self.assertNotIn('statusTitleColor(for:', self.source)
        self.assertNotIn('NSWorkspace.didActivateApplicationNotification', self.source)

    def test_native_menu_omits_cost_and_context_below_recent_sessions(self):
        rebuild = self.source[
            self.source.index("    private func rebuildMenu()"):
            self.source.index("    private var activeShortcutKeyCode")
        ]
        sessions_position = rebuild.index('addSessionPicker()')
        limits_position = rebuild.index('let limitsItem = NSMenuItem(title: "Provider limits (Beta)"')
        self.assertLess(sessions_position, limits_position)
        self.assertNotIn('addMetricRow("Cost", snapshot.costLabel', rebuild)
        self.assertNotIn('"Context",\n                contextDetail', rebuild)
        self.assertNotIn('addMetricRow("Tokens"', rebuild)
        self.assertNotIn('addMetricRow("Cache"', rebuild)
        self.assertNotIn('addMetricRow("Output', rebuild)
        self.assertNotIn('ExecutionTraceView', rebuild)

    def test_global_toggle_shortcut_is_configurable_and_persisted(self):
        self.assertIn('import Carbon.HIToolbox', self.source)
        self.assertIn('enum MenuBarShortcut: String, CaseIterable', self.source)
        self.assertIn('case .controlOptionT: return "⌃⌥T"', self.source)
        self.assertIn('private let globalShortcutDefaultsKey = "TokenMeterGlobalShortcut"', self.source)
        self.assertIn('RegisterEventHotKey(', self.source)
        self.assertIn('delegate.openMenu()', self.source)
        self.assertIn('NSMenuItem(title: "Keyboard shortcut"', self.source)
        self.assertIn('case .custom: return "Custom…"', self.source)
        self.assertIn('@objc private func configureCustomShortcut(_ sender: NSMenuItem)', self.source)
        self.assertIn('NSEvent.addLocalMonitorForEvents(matching: .keyDown)', self.source)
        self.assertIn('tokenMeterDefaults.set(shortcut.rawValue, forKey: globalShortcutDefaultsKey)', self.source)

    def test_native_menu_header_has_no_splunk_wordmark(self):
        header = self.source[
            self.source.index("    private func addHeader()"):
            self.source.index("    private func addSessionPicker()")
        ]
        self.assertIn('titleText = selectedSession?.identifier ?? snapshot.menuTitle', header)
        self.assertIn('"Following latest"', header)
        self.assertIn('"Following this session"', header)
        self.assertNotIn('splunkWordmarkImage', header)
        self.assertNotIn('logo', header)

    def test_monthly_budget_status_is_native_and_alerts_come_from_the_server(self):
        for marker in (
            'struct MonthlyBudget',
            'let budgetExceeded = monthlyBudget?.anyExceeded == true',
            'description: budgetExceeded ? "Budget alert" : "Budgets"',
            'budgetsItem.submenu = makeBudgetsMenu()',
            'tokenMeterBudgetSettingsURL',
            'action: #selector(openBudgetSettings)',
            'var exceeded: Bool { configured && percent >= 100 }',
            'var anyExceeded: Bool { exceeded || !exceededRuntimeScopes.isEmpty }',
            'if let scope = exceededRuntimeScopes.first',
            'return "\\(scope.label) · \\(Int(scope.percent.rounded()))%"',
            'warning: monthlyBudget?.anyExceeded == true',
            'button.contentTintColor = budgetExceeded',
            'NSColor.systemRed',
            'button.setAccessibilityLabel("Token Meter")',
            'let activeTitle = budget?.anyExceeded == true ? "⚠︎ \\(baseTitle)" : baseTitle',
            'print("budget-state=\\(budget?.compactLabel ?? "unconfigured") exceeded=\\(budget?.anyExceeded == true)")',
        ):
            self.assertIn(marker, self.source)
        self.assertNotIn("· over budget", self.source)
        self.assertNotIn(
            'monthlyBudget?.exceeded == true ? NSColor.systemRed : NSColor.labelColor',
            self.source,
        )
        self.assertNotIn(
            'valueColor: budget.exceeded ? .systemRed : .labelColor',
            self.source,
        )


class TrayLogicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import importlib.util
        tray_path = Path(meter.__file__).with_name("menubar").joinpath("token_meter_tray.py")
        spec = importlib.util.spec_from_file_location("token_meter_tray", tray_path)
        cls.tray = importlib.util.module_from_spec(spec)
        # Never write menubar/__pycache__ into the checkout: installer tests running in
        # parallel copy menubar/ whole and compare it byte for byte with the source.
        previous, sys.dont_write_bytecode = sys.dont_write_bytecode, True
        try:
            spec.loader.exec_module(cls.tray)
        finally:
            sys.dont_write_bytecode = previous

    def test_dashboard_url_opens_sessions_unless_pinned(self):
        self.assertEqual(
            self.tray.dashboard_url("#sessions", pinned_session=None, include_pinned_session=False),
            "http://127.0.0.1:8722/#sessions",
        )
        self.assertEqual(
            self.tray.dashboard_url("#summary", pinned_session="abc"),
            "http://127.0.0.1:8722/sessions/abc#summary",
        )

    def test_status_display_mode_selects_or_hides_the_tray_label(self):
        label = "$1 · 12 tok/s"
        self.assertEqual(self.tray.normalize_status_display_mode(None), "automatic")
        self.assertEqual(self.tray.normalize_status_display_mode("invalid"), "automatic")
        self.assertEqual(self.tray.normalize_status_display_mode({"mode": "icon"}), "automatic")
        self.assertEqual(self.tray.status_display_label(label, "automatic"), label)
        self.assertEqual(self.tray.status_display_label(label, "text"), label)
        self.assertEqual(self.tray.status_display_label(label, "icon"), "")

    def test_update_tray_label_keeps_title_when_icon_mode_hides_label(self):
        tray = object.__new__(self.tray.TokenMeterTray)
        tray.error = ""
        tray.title_metrics = set()
        tray.snapshot = {}
        tray.provider_quotas = []
        tray.monthly_budget = None
        tray.indicator = mock.Mock()
        tray.status_display_mode = "icon"

        tray.update_tray_label()

        tray.indicator.set_label.assert_called_once_with(
            "", "⚠︎ $999.99 · 999 tok/s · 100% ctx",
        )
        tray.indicator.set_title.assert_called_once_with("Token Meter")

    def test_update_tray_label_uses_descriptive_tooltip_but_keeps_compact_status(self):
        tray = object.__new__(self.tray.TokenMeterTray)
        tray.error = ""
        tray.title_metrics = {"cost", "speed", "today_spend"}
        tray.snapshot = {
            "availability": {"cost": True, "throughput": True, "tokens": True},
            "source": {"label": "Claude", "token_estimate": False},
            "total_cost": 1.25,
            "throughput": {"available": True, "output_tps": 12.3},
            "today_spend": {"available": True, "total_cost": 2.75, "estimated": True},
        }
        tray.provider_quotas = []
        tray.monthly_budget = None
        tray.indicator = mock.Mock()
        tray.status_display_mode = "automatic"

        tray.update_tray_label()

        tray.indicator.set_label.assert_called_once_with(
            "$1 · 12 tok/s · $3 est", "⚠︎ $999.99 · 999 tok/s · 100% ctx",
        )
        tray.indicator.set_title.assert_called_once_with(
            "Current session cost: $1.25\nOutput speed: 12.3 tok/s\n"
            "Today's spend across sessions: $2.75 est",
        )

    def test_update_tray_label_explains_offline_status_in_tooltip(self):
        tray = object.__new__(self.tray.TokenMeterTray)
        tray.error = "server unavailable"
        tray.title_metrics = {"cost", "speed"}
        tray.indicator = mock.Mock()
        tray.status_display_mode = "automatic"

        tray.update_tray_label()

        tray.indicator.set_label.assert_called_once_with(
            "TM off", "⚠︎ $999.99 · 999 tok/s · 100% ctx",
        )
        tray.indicator.set_title.assert_called_once_with("Token Meter · Server offline")

    def test_display_mode_selection_persists_and_refreshes_the_tray(self):
        tray = object.__new__(self.tray.TokenMeterTray)
        tray.status_display_mode = "automatic"
        tray.save_state = mock.Mock()
        tray.update_tray_label = mock.Mock()
        tray.refresh_menu_content = mock.Mock()
        item = mock.Mock()
        item.get_active.return_value = True
        item.token_meter_status_display_mode = "icon"

        tray._on_display_mode_toggled(item)

        self.assertEqual(tray.status_display_mode, "icon")
        tray.save_state.assert_called_once_with()
        tray.update_tray_label.assert_called_once_with()
        tray.refresh_menu_content.assert_called_once_with()

    def test_tray_status_title_uses_selected_metrics_and_budget_warning(self):
        state = {
            "availability": {"cost": True, "throughput": True, "cache": True, "tokens": True},
            "source": {"label": "Claude", "token_estimate": False},
            "total_cost": 1.25,
            "model": "claude-test",
            "context": {"latest_pct": 0.42},
            "throughput": {"available": True, "output_tps": 12.3},
        }
        providers = [{
            "id": "claude",
            "label": "Claude",
            "status": "ok",
            "stale": False,
            "windows": [{"id": "weekly", "kind": "weekly", "label": "Weekly", "used_percent": 88}],
        }]
        budget = {
            "configured": True,
            "percent": 110,
            "spend": 11,
            "budget": 10,
            "month": "2026-07",
            "lower_bound": False,
            "scopes": [{
                "id": "overall", "label": "Overall", "spend": 11, "budget": 10, "percent": 110,
            }],
        }
        title = self.tray.tray_status_title(
            state, {"cost", "speed", "limits"}, providers, budget,
        )
        self.assertTrue(title.startswith("⚠︎ "))
        self.assertIn("$1", title)
        self.assertIn("12 tok/s", title)
        self.assertIn("Claude 88% · weekly", title)

    def test_tray_status_title_includes_today_spend_with_native_compact_labels(self):
        state = {
            "availability": {"cost": True, "throughput": True, "tokens": True},
            "source": {"label": "Claude", "token_estimate": False},
            "total_cost": 1.25,
            "cost_approx": True,
            "context": {"latest_pct": 0.42},
            "throughput": {"available": True, "output_tps": 12.3},
            "live_throughput": {"available": True, "output_tps": 24.6},
            "today_spend": {"available": True, "total_cost": 2.75, "estimated": True},
        }
        title = self.tray.tray_status_title(
            state, {"cost", "speed", "context", "today_spend"}, [], None,
        )
        self.assertEqual(title, "$1 · 25 tok/s · 42% · $3 est")
        self.assertNotIn("$1 est", title)

        unavailable_context = dict(state)
        unavailable_context["context"] = {}
        unavailable_title = self.tray.tray_status_title(
            unavailable_context, {"context"}, [], None,
        )
        self.assertEqual(unavailable_title, "--%")

    def test_load_state_persists_today_spend_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tray.json"
            path.write_text(json.dumps({
                "title_metrics": ["cost", "speed"],
                "selected_tab": "run",
            }), encoding="utf-8")
            with mock.patch.object(self.tray, "STATE_PATH", str(path)):
                loaded = self.tray.TokenMeterTray.load_state(object.__new__(
                    self.tray.TokenMeterTray,
                ))
            self.assertIn("today_spend", loaded["title_metrics"])
            persisted = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["title_metrics"], [
                "cost", "speed", "today_spend",
            ])
            self.assertTrue(persisted["today_spend_title_preference"])

    def test_load_state_migrates_and_preserves_status_display_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tray.json"
            path.write_text(json.dumps({
                "today_spend_title_preference": True,
                "title_metrics": ["cost", "speed"],
            }), encoding="utf-8")
            with mock.patch.object(self.tray, "STATE_PATH", str(path)):
                loaded = self.tray.TokenMeterTray.load_state(object.__new__(
                    self.tray.TokenMeterTray,
                ))
            self.assertEqual(loaded["status_display_mode"], "automatic")
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["status_display_mode"],
                "automatic",
            )

            path.write_text(json.dumps({
                "today_spend_title_preference": True,
                "title_metrics": ["cost"],
                "status_display_mode": "icon",
            }), encoding="utf-8")
            with mock.patch.object(self.tray, "STATE_PATH", str(path)):
                loaded = self.tray.TokenMeterTray.load_state(object.__new__(
                    self.tray.TokenMeterTray,
                ))
            self.assertEqual(loaded["status_display_mode"], "icon")

    def test_parse_monthly_budget_preserves_unconfigured_runtime_scopes(self):
        budget = self.tray.parse_monthly_budget({
            "configured": True,
            "month": "2026-07",
            "spend": 6.5,
            "budget": 1000,
            "percent": 0.0065,
            "runtimes": [
                {
                    "provider": "claude", "label": "Claude", "spend": 0,
                    "allocation": 0, "percent": None,
                },
                {
                    "provider": "codex", "label": "Codex", "spend": 6.5,
                    "allocation": 1000, "percent": 0.0065,
                },
            ],
        })
        self.assertEqual([scope["id"] for scope in budget["scopes"]], [
            "overall", "claude", "codex",
        ])
        self.assertEqual(budget["scopes"][1]["percent"], 0)
        self.assertAlmostEqual(budget["scopes"][2]["percent"], 0.65)

    def feed(self, **prefs):
        return {
            "schema_version": 1,
            "events": [
                {"id": f"e{i}", "title": f"t{i}", "body": "b", "created_at": 100 - i, "claimable": i != 6}
                for i in range(7)
            ],
            "prefs": {"source": "saved", "quota_alerts": True, "quota_threshold": 90, **prefs},
            "actions": {"token": "tok"},
        }

    def tray_instance(self, settings=None):
        tray = object.__new__(self.tray.TokenMeterTray)
        tray.settings = dict(settings or {})
        tray.notification_feed = None
        tray.legacy_notification_prefs_handled = False
        tray.pinned_session = None
        tray.selected_tab = "run"
        tray.title_metrics = set(self.tray.DEFAULT_TITLE_METRICS)
        tray.status_display_mode = "automatic"
        return tray

    def test_notification_feed_is_parsed_and_delivered_oldest_first(self):
        feed = self.tray.parse_notification_feed(self.feed())
        self.assertEqual(feed["quota_threshold"], 90)
        self.assertEqual([e["id"] for e in self.tray.deliverable_notifications(feed)],
                         ["e5", "e4", "e3", "e2", "e1"])
        self.assertIsNone(self.tray.parse_notification_feed({"schema_version": 2}))
        self.assertIsNone(self.tray.parse_notification_feed(None))
        self.assertEqual(self.tray.parse_notification_feed(self.feed(quota_threshold=85))["quota_threshold"], 80)

    def test_tray_shows_only_events_it_claimed(self):
        tray = self.tray_instance()
        claimed = {"e5", "e3"}
        calls = []

        def post(path, body, token):
            calls.append((path, body, token))
            return True, {"ok": True, "claimed": body["id"] in claimed}

        with mock.patch.object(self.tray, "post_json", side_effect=post), \
                mock.patch.object(self.tray, "deliver_notification") as deliver:
            tray._handle_notifications(self.tray.parse_notification_feed(self.feed()))
        self.assertEqual([c[1]["consumer"] for c in calls], ["token-meter-tray"] * 5)
        self.assertEqual({c[0] for c in calls}, {"/notifications/claim"})
        self.assertEqual([c.args for c in deliver.call_args_list], [("t5", "b"), ("t3", "b")])
        self.assertTrue(tray.quota_alerts_enabled)
        self.assertEqual(tray.quota_alert_threshold, 90)

    def test_tray_stops_claiming_for_this_poll_after_a_failed_post(self):
        tray = self.tray_instance()
        with mock.patch.object(self.tray, "post_json", return_value=(False, {})) as post, \
                mock.patch.object(self.tray, "deliver_notification") as deliver:
            tray._handle_notifications(self.tray.parse_notification_feed(self.feed()))
        self.assertEqual(post.call_count, 1)
        deliver.assert_not_called()
        self.assertLessEqual(self.tray.NOTIFICATION_POST_TIMEOUT_S, 2)

    def test_tray_migrates_local_quota_prefs_once_then_drops_legacy_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "tray.json")
            tray = self.tray_instance({
                "quota_alerts_enabled": False, "quota_alert_threshold": 95,
                "quota_notification_states": {"codex:primary": {}}, "budget_exceeded_notification_months": [],
            })
            feed = self.tray.parse_notification_feed(self.feed(source="default"))
            feed["events"] = []
            with mock.patch.object(self.tray, "STATE_PATH", path), \
                    mock.patch.object(self.tray, "post_json", return_value=(True, {"ok": True})) as post:
                tray._handle_notifications(feed)
                tray._handle_notifications(feed)
            post.assert_called_once_with("/settings/notifications", {
                "quota_alerts": False, "quota_threshold": 95, "migrate": True,
            }, "tok")
            with open(path, encoding="utf-8") as fh:
                saved = json.load(fh)
            for key in self.tray.LEGACY_NOTIFICATION_KEYS:
                self.assertNotIn(key, saved)
                self.assertNotIn(key, tray.settings)

    def test_tray_keeps_local_prefs_until_the_server_takes_them(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "tray.json")
            tray = self.tray_instance({"quota_alerts_enabled": False, "quota_alert_threshold": 95})
            feed = self.tray.parse_notification_feed(self.feed(source="default"))
            feed["events"] = []
            with mock.patch.object(self.tray, "STATE_PATH", path), \
                    mock.patch.object(self.tray, "post_json", return_value=(False, {})):
                tray._handle_notifications(feed)
                tray.save_state()
            with open(path, encoding="utf-8") as fh:
                saved = json.load(fh)
            self.assertEqual(saved["quota_alerts_enabled"], False)
            self.assertEqual(saved["quota_alert_threshold"], 95)
            self.assertFalse(tray.legacy_notification_prefs_handled)


class TraySourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (
            Path(meter.__file__).with_name("menubar").joinpath("token_meter_tray.py").read_text()
        )

    def test_linux_tray_matches_native_dashboard_routing_and_actions(self):
        for marker in (
            "def open_dashboard(self):",
            'self.open_url("#sessions", include_pinned_session=False)',
            'self.open_url("#summary")',
            "Open Budget Settings",
            "#settings-budgets",
            "Open Settings",
            "#settings",
            "Model Prices",
            "#model-pricing",
            "Budgets",
            "Get Enterprise Tokenomics",
            "TOKENOMICS_URL",
            "open_external_url",
        ):
            self.assertIn(marker, self.source)

    def test_linux_tray_does_not_advertise_removed_session_trace_view(self):
        self.assertNotIn('"Open Trace"', self.source)
        self.assertNotIn('self.open_url("#activity")', self.source)

    def test_linux_tray_exposes_tabs_title_metrics_and_notifications(self):
        for marker in (
            '("overview", "All")',
            '("claude", "Claude")',
            '("today_spend", "Today spend")',
            'DEFAULT_TITLE_METRICS = ("cost", "speed", "today_spend")',
            "_apply_budget_menu",
            "for index, scope in enumerate(scopes):",
            "self.budget_scope_items.append(item)",
            "Menu bar title",
            "Quota notifications",
            "Warn at",
            "parse_notification_feed",
            'NOTIFICATION_CONSUMER = "token-meter-tray"',
            '["notify-send", title, body]',
            "Output speed:",
            "Monthly budget:",
            "Most constrained:",
        ):
            self.assertIn(marker, self.source)

    def test_linux_tray_has_no_local_notification_rules(self):
        for removed in ("def evaluate_quota_notifications", "def evaluate_budget_notifications",
                        "fired_thresholds", '"quota_notification_states": {}'):
            self.assertNotIn(removed, self.source)

    def test_linux_tray_exposes_persisted_status_display_modes(self):
        for marker in (
            '"status_display_mode": "automatic"',
            'self.status_display_mode = normalize_status_display_mode',
            '"status_display_mode": self.status_display_mode',
            'self.display_mode_item, self.display_mode_menu = self._submenu_item("Menu bar display")',
            '("automatic", "Automatic")',
            '("text", "Cost + speed text")',
            '("icon", "Icon only")',
            'def _on_display_mode_toggled(self, item):',
            'status_display_label(label, self.status_display_mode)',
        ):
            self.assertIn(marker, self.source)


class MenubarSessionTests(unittest.TestCase):
    def test_menubar_state_projects_today_cross_session_spend(self):
        today = datetime.date.today().isoformat()
        source = {
            "id": "live", "provider": "codex", "label": "Codex",
            "path": "/tmp/live.jsonl", "session": "live.jsonl", "project": "/repo",
        }
        state = {
            "provider": "codex", "source": source, "session": "live.jsonl",
            "project": "/repo", "context": {}, "cache": {}, "executions": [], "insights": [],
        }
        cross = {
            "daily": [{
                "day": today, "cost": 1.25,
                "availability": {"cost": True},
                "provenance": {"estimated_cost": 0.25},
            }],
        }
        with mock.patch.object(meter, "STATE", state), \
                mock.patch.object(meter, "cached_session_sources", return_value=([source], True)), \
                mock.patch.object(meter, "provider_quota_snapshots", return_value=[]), \
                mock.patch.dict(meter._xsess, {"data": cross}, clear=False):
            payload = meter.menubar_state()

        self.assertEqual(payload["today_spend"], {
            "available": True,
            "total_cost": 1.25,
            "estimated": True,
        })

    def test_menubar_today_spend_keeps_unavailable_cost_unavailable(self):
        today = datetime.date.today()

        projection = meter.menubar_today_spend({"daily": [{
            "day": today.isoformat(), "cost": 0,
            "availability": {"cost": False},
        }]}, today=today)

        self.assertEqual(projection, {
            "available": False,
            "total_cost": None,
            "estimated": False,
        })

    def test_recommendations_always_target_the_single_run_surface(self):
        states = (
            {"context": {"latest_pct": 0.9}, "executions": [], "insights": []},
            {"context": {}, "last_turn_cost": 0, "executions": [],
             "insights": [{"kind": "warn", "text": "Check this signal"}]},
        )
        for state in states:
            with self.subTest(state=state):
                self.assertEqual(meter.menubar_recommendation(state)["target"], "summary")

    def test_menubar_reuses_watcher_state_for_the_live_selected_session(self):
        source = {
            "id": "live", "provider": "codex", "label": "Codex",
            "path": "/tmp/live.jsonl", "session": "live.jsonl",
            "project": "/repo", "mtime": 1,
        }
        state = {
            "provider": "codex", "source": source, "session": "live.jsonl",
            "project": "/repo", "context": {}, "cache": {},
            "executions": [], "insights": [],
        }
        with mock.patch.object(meter, "STATE", state), \
                mock.patch.object(
                    meter, "cached_session_sources", return_value=([source], True),
                ), \
                mock.patch.object(
                    meter, "recompute",
                    side_effect=AssertionError("menu poll must reuse watcher state"),
                ), \
                mock.patch.object(meter, "provider_quota_snapshots", return_value=[]):
            payload = meter.menubar_state("live")

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["selection"]["selected_id"], "live")

    def test_context_pulse_is_bounded_numeric_only_and_does_not_leak_trace_content(self):
        state = {
            "executions": [
                {"context_pct": -0.5, "detail": "private prompt"},
                {"context_pct": 0.251234, "tool_input": "private arguments"},
                {"context_pct": 1.5, "path": "/private/logs/trace.jsonl"},
                {"context_pct": None},
                {"context_pct": "not a number"},
                {"context_pct": True},
            ],
        }
        pulse = meter.menubar_context_pulse(state, limit=2)

        self.assertEqual(pulse, [0.2512, 1.0])
        encoded = json.dumps(pulse)
        self.assertNotIn("private", encoded)
        self.assertNotIn("trace.jsonl", encoded)

    def test_recent_sessions_are_limited_and_keep_an_older_pin_visible(self):
        sources = [{
            "id": f"s{idx}", "provider": "codex" if idx % 2 else "claude",
            "label": "Codex" if idx % 2 else "Claude Code", "path": f"/tmp/s{idx}.jsonl",
            "session": f"s{idx}.jsonl", "project": f"/repo/project-{idx}", "mtime": idx,
            "title": f"Task {idx}",
        } for idx in range(7)]

        latest = meter.menubar_recent_sessions(sources)
        pinned = meter.menubar_recent_sessions(sources, selected_id="s0")

        self.assertEqual([row["id"] for row in latest], ["s6", "s5", "s4", "s3", "s2"])
        self.assertEqual([row["id"] for row in pinned], ["s0", "s6", "s5", "s4", "s3"])
        self.assertEqual(pinned[0]["name"], "Task 0")
        self.assertNotIn("project", pinned[0])

    def test_recent_sessions_use_sanitized_summary_name_when_discovery_has_no_title(self):
        sources = [{
            "id": "claude-session", "provider": "claude", "label": "Claude Code",
            "path": "/tmp/claude-session.jsonl", "session": "claude-session.jsonl",
            "project": "/repo/token-meter", "mtime": 10, "title": None,
        }]
        summaries = [{
            "id": "claude-session", "provider": "claude",
            "session_name": "Default model configuration opus 5",
        }]

        recent = meter.menubar_recent_sessions(sources, summaries=summaries)

        self.assertEqual(recent[0]["name"], "Default model configuration opus 5")

    def test_menubar_state_joins_recent_session_names_from_cross_session_summaries(self):
        source = {
            "id": "claude-session", "provider": "claude", "label": "Claude Code",
            "path": "/tmp/claude-session.jsonl", "session": "claude-session.jsonl",
            "project": "/repo/token-meter", "mtime": 10, "title": None,
        }
        state = {
            "provider": "claude", "source": source, "session": "claude-session.jsonl",
            "project": "/repo/token-meter", "context": {}, "cache": {}, "trace": [],
            "insights": [], "executions": [], "throughput": {}, "live_throughput": {},
        }
        cross = {
            "current_sessions": [{
                "id": "claude-session", "provider": "claude",
                "session_name": "Default model configuration opus 5",
            }],
            "sessions": [],
        }
        with mock.patch.object(meter, "STATE", state), \
                mock.patch.object(meter, "cached_session_sources", return_value=([source], True)), \
                mock.patch.object(meter, "provider_quota_snapshots", return_value=[]), \
                mock.patch.dict(meter._xsess, {"data": cross}, clear=False):
            payload = meter.menubar_state()

        self.assertEqual(
            payload["recent_sessions"][0]["name"],
            "Default model configuration opus 5",
        )

    def test_menubar_state_uses_requested_session_and_marks_pin(self):
        sources = [{
            "id": "pinned", "provider": "codex", "label": "Codex", "path": "/tmp/pinned.jsonl",
            "session": "pinned.jsonl", "project": "/repo/pinned-project", "mtime": 1, "title": "Pinned task",
        }]
        state = {
            "provider": "codex", "source": sources[0], "session": "pinned.jsonl",
            "project": "/repo/pinned-project", "context": {}, "cache": {}, "trace": [], "insights": [],
            "executions": [{"idx": 1, "model": "gpt-5.6-sol", "context_pct": 0.2}],
            "throughput": {"available": True, "output_tps": 42.5, "basis": "end_to_end",
                           "sample_count": 2, "timing_coverage": 0.75},
            "live_throughput": {"available": True, "output_tps": 18.25,
                                "basis": "live_end_to_end", "completed_steps": 3,
                                "measured_output_tokens": 365, "measured_seconds": 20},
        }
        with mock.patch.object(meter, "STATE", {"source": {"id": "live"}}), \
                mock.patch.object(meter, "cached_session_sources", return_value=(sources, True)), \
                mock.patch.object(meter, "cached_session_state", return_value=state), \
                mock.patch.object(meter, "provider_quota_snapshots", return_value=[]):
            payload = meter.menubar_state("pinned")

        self.assertTrue(payload["selection"]["pinned"])
        self.assertFalse(payload["selection"]["missing"])
        self.assertEqual(payload["selection"]["selected_id"], "pinned")
        self.assertEqual(payload["recent_sessions"][0]["name"], "Pinned task")
        self.assertEqual(payload["model"], "gpt-5.6-sol")
        self.assertEqual(payload["provider_quotas"], [])
        self.assertEqual(payload["context_pulse"], [0.2])
        self.assertEqual(payload["project"], "pinned-project")
        self.assertEqual(payload["source"]["project"], "pinned-project")
        self.assertNotIn("/repo/pinned-project", json.dumps(payload))
        self.assertEqual(payload["throughput"], {
            "available": True, "output_tps": 42.5, "basis": "end_to_end",
            "sample_count": 2, "timing_coverage": 0.75,
        })
        self.assertEqual(payload["live_throughput"], {
            "available": True, "output_tps": 18.25, "basis": "live_end_to_end",
            "completed_steps": 3, "measured_output_tokens": 365,
            "measured_seconds": 20,
        })

    def test_cold_start_does_not_rebuild_all_history_for_each_menu_poll(self):
        with mock.patch.object(meter, "STATE", {}), \
                mock.patch.object(meter, "cached_session_sources", return_value=([], False)), \
                mock.patch.object(
                    meter, "all_session_sources",
                    side_effect=AssertionError("menu polling must not discover sessions"),
                ), \
                mock.patch.object(meter, "recompute") as recompute, \
                mock.patch.object(meter, "provider_quota_snapshots", return_value=[]):
            payload = meter.menubar_state()

        recompute.assert_not_called()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["provider_quotas"], [])

    def test_cold_start_does_not_recompute_a_persisted_pinned_session(self):
        sources = [{
            "id": "pinned", "provider": "codex", "label": "Codex",
            "path": "/tmp/pinned.jsonl", "session": "pinned.jsonl",
            "project": "/repo", "mtime": 1,
        }]
        with mock.patch.object(meter, "STATE", {}), \
                mock.patch.object(meter, "cached_session_sources", return_value=(sources, True)), \
                mock.patch.object(meter, "recompute") as recompute, \
                mock.patch.object(meter, "provider_quota_snapshots", return_value=[]):
            payload = meter.menubar_state("pinned")

        recompute.assert_not_called()
        self.assertTrue(payload["selection"]["pinned"])
        self.assertEqual(payload["selection"]["selected_id"], "pinned")
        self.assertFalse(payload["selection"]["missing"])

    def test_menubar_uses_published_monthly_budget_when_cross_cache_rotates(self):
        budget = {
            "month": "2026-07", "configured": True, "budget": 1000,
            "spend": 420, "percent": 0.42, "settings": {
                "monthly_total": 1000, "allocations": {}, "thresholds": [80, 90, 100],
                "native_notifications": True,
            },
        }
        with mock.patch.object(meter, "STATE", {"xsession": {"budget": budget}}), \
                mock.patch.object(meter, "cached_session_sources", return_value=([], True)), \
                mock.patch.object(meter, "provider_quota_snapshots", return_value=[]), \
                mock.patch.dict(meter._xsess, {"data": None}, clear=False):
            payload = meter.menubar_state()
        self.assertEqual(payload["budget"], budget)


if __name__ == "__main__":
    unittest.main()
