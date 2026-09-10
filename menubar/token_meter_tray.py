#!/usr/bin/env python3
"""Linux StatusNotifier/AppIndicator companion for Token Meter."""
import json
import os
import signal
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

BASE_URL = os.environ.get("TOKEN_METER_URL", "http://127.0.0.1:8722").rstrip("/")
STATE_URL = BASE_URL + "/menubar"
CONFIG_HOME = os.path.expanduser(os.environ.get("XDG_CONFIG_HOME", "~/.config"))
STATE_PATH = os.path.join(CONFIG_HOME, "token-meter", "tray.json")
MAX_RECENT_SESSIONS = 10
MAX_PROVIDERS = 5
MAX_PROVIDER_WINDOWS = 4
MENU_TABS = (
    ("run", "Run"),
    ("overview", "All"),
    ("claude", "Claude"),
    ("codex", "Codex"),
    ("cursor", "Cursor"),
    ("grok", "Grok"),
)
TITLE_METRICS = (
    ("cost", "Cost"),
    ("speed", "Output speed"),
    ("context", "Context"),
    ("model", "Model"),
    ("limits", "Limits"),
)
DEFAULT_TITLE_METRICS = ("cost", "speed")
QUOTA_THRESHOLDS = (80, 90, 95)
DEFAULT_STATE = {
    "pinned_session": None,
    "selected_tab": "run",
    "title_metrics": list(DEFAULT_TITLE_METRICS),
    "quota_alerts_enabled": True,
    "quota_alert_threshold": 80,
    "quota_notification_states": {},
    "budget_notification_states": {},
    "budget_exceeded_notification_months": [],
    "usage_widget_open": True,
}
WIDGET_APPLICATION_ID = "com.tokenmeter.usagewidget"


def metric_available(availability, metric):
    if not isinstance(availability, dict) or metric not in availability:
        return True
    return bool(availability.get(metric))


def money(value):
    value = float(value or 0)
    if abs(value) >= 1:
        return f"${value:.2f}"
    return f"${value:.3f}"


def compact_scaled(value, suffix):
    if value >= 100:
        pattern = "%.0f"
    elif value >= 10:
        pattern = "%.1f"
    else:
        pattern = "%.2f"
    number = pattern % value
    while "." in number and number.endswith("0"):
        number = number[:-1]
    if number.endswith("."):
        number = number[:-1]
    return number + suffix


def compact_number(value):
    value = int(value or 0)
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    if magnitude >= 1_000_000_000:
        return sign + compact_scaled(magnitude / 1_000_000_000, "B")
    if magnitude >= 1_000_000:
        return sign + compact_scaled(magnitude / 1_000_000, "M")
    if magnitude >= 1_000:
        return sign + compact_scaled(magnitude / 1_000, "k")
    return f"{value:,}"


def format_token_rate(value):
    value = float(value or 0)
    if value >= 100:
        return f"{value:.0f}"
    if value >= 10:
        return f"{value:.1f}"
    return f"{value:.2f}"


def format_compact_duration(seconds):
    seconds = max(0, int(seconds or 0))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    remaining_minutes = minutes % 60
    if hours < 48:
        return f"{hours}h" + (f" {remaining_minutes}m" if remaining_minutes else "")
    days = hours // 24
    remaining_hours = hours % 24
    return f"{days}d" + (f" {remaining_hours}h" if remaining_hours else "")


def percentage(value):
    if value is None:
        return "--"
    return f"{float(value):.0f}%"


def reset_label(reset_at):
    if not reset_at:
        return "reset time unavailable"
    remaining = max(0, int(float(reset_at) - time.time()))
    if remaining <= 0:
        return "reset pending"
    return f"resets in {format_compact_duration(remaining)}"


def provider_name(provider, catalog=None):
    value = str(provider or "unknown-runtime")
    catalog = catalog if isinstance(catalog, dict) else {}
    presentation = catalog.get(value) or catalog.get("unknown-runtime") or {}
    fallback = value.replace("-", " ").replace("_", " ").title()
    return str(presentation.get("label") or fallback or "Unknown Runtime")


def session_identifier(session):
    name = str(session.get("name") or "").strip()
    if name:
        return name
    project = str(session.get("project") or "").strip()
    if project:
        return os.path.basename(os.path.expanduser(project))
    session_id = str(session.get("id") or "")
    return session_id[:12]


def session_menu_title(session, catalog=None):
    provider = str(session.get("provider") or "unknown-runtime")
    catalog = catalog if isinstance(catalog, dict) else {}
    label = (
        provider_name(provider, catalog)
        if provider in catalog else
        str(session.get("label") or provider_name(provider, catalog))
    )
    return f"{label} · {session_identifier(session)}"


def parse_monthly_budget(payload):
    if not isinstance(payload, dict):
        return None
    configured = bool(payload.get("configured"))
    settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
    thresholds = [
        int(value)
        for value in (settings.get("thresholds") or [])
        if isinstance(value, (int, float))
    ]
    if not thresholds:
        thresholds = [80, 90, 100]
    scopes = []
    for row in payload.get("runtimes") or []:
        if not isinstance(row, dict):
            continue
        allocation = float(row.get("allocation") or 0)
        percent = row.get("percent")
        provider = row.get("provider")
        if allocation <= 0 or percent is None or not provider:
            continue
        scopes.append({
            "id": str(provider),
            "label": str(row.get("label") or str(provider).title()),
            "spend": float(row.get("spend") or 0),
            "budget": allocation,
            "percent": float(percent) * 100,
        })
    if configured:
        scopes.insert(0, {
            "id": "overall",
            "label": "Overall",
            "spend": float(payload.get("spend") or 0),
            "budget": float(payload.get("budget") or 0),
            "percent": float(payload.get("percent") or 0) * 100,
        })
    return {
        "month": str(payload.get("month") or ""),
        "configured": configured,
        "spend": float(payload.get("spend") or 0),
        "budget": float(payload.get("budget") or 0),
        "percent": float(payload.get("percent") or 0) * 100,
        "lower_bound": bool(payload.get("lower_bound")),
        "native_notifications": True if settings.get("native_notifications") is None
        else bool(settings.get("native_notifications")),
        "thresholds": thresholds,
        "scopes": scopes,
    }


def budget_exceeded_scopes(budget):
    if not budget:
        return []
    return [
        scope for scope in budget.get("scopes") or []
        if scope.get("id") != "overall" and float(scope.get("percent") or 0) >= 100
    ]


def budget_any_exceeded(budget):
    if not budget or not budget.get("configured"):
        return False
    if float(budget.get("percent") or 0) >= 100:
        return True
    return bool(budget_exceeded_scopes(budget))


def budget_compact_label(budget):
    if not budget or not budget.get("configured"):
        return "Budget not set"
    runtime = budget_exceeded_scopes(budget)
    if runtime:
        scope = runtime[0]
        return f"⚠︎ {scope['label']} · {int(round(scope['percent']))}%"
    if float(budget.get("percent") or 0) >= 100:
        return f"⚠︎ Overall · {int(round(budget['percent']))}%"
    prefix = "≥" if budget.get("lower_bound") else ""
    return f"{prefix}{int(round(budget['percent']))}% budget"


def budget_tooltip(budget):
    if not budget or not budget.get("configured"):
        return "Monthly budget is not configured."
    prefix = "At least " if budget.get("lower_bound") else ""
    lines = [
        f"{prefix}{money(budget['spend'])} of {money(budget['budget'])} "
        f"recorded for {budget['month']}."
    ]
    for scope in budget_exceeded_scopes(budget):
        lines.append(
            f"{scope['label']}: {money(scope['spend'])} of {money(scope['budget'])} "
            f"({int(round(scope['percent']))}%)."
        )
    return "\n".join(lines)


def provider_is_fresh(provider):
    if not isinstance(provider, dict):
        return False
    return provider.get("status") == "ok" and not provider.get("stale")


def highest_quota_window(provider):
    windows = provider.get("windows") or []
    best = None
    for window in windows:
        if not isinstance(window, dict):
            continue
        used = window.get("used_percent")
        if used is None:
            continue
        if best is None or float(used) > float(best.get("used_percent") or 0):
            best = window
    return best


def most_constrained_quota(providers):
    best = None
    for provider in providers or []:
        if not provider_is_fresh(provider):
            continue
        for window in provider.get("windows") or []:
            if not isinstance(window, dict) or window.get("used_percent") is None:
                continue
            candidate = (provider, window)
            if best is None or float(window["used_percent"]) > float(best[1]["used_percent"]):
                best = candidate
    return best


def window_compact_kind(window):
    kind = str((window or {}).get("kind") or "extra")
    if kind == "session":
        return "session"
    if kind == "weekly":
        return "weekly"
    if kind == "monthly":
        return "monthly"
    return str((window or {}).get("label") or "quota").lower()


def limits_status_title(providers):
    constrained = most_constrained_quota(providers)
    if not constrained:
        return None
    provider, window = constrained
    label = provider.get("label") or str(provider.get("id") or "Provider").title()
    return f"{label} {percentage(window.get('used_percent'))} · {window_compact_kind(window)}"


def snapshot_labels(state):
    availability = state.get("availability") or {}
    source = state.get("source") or {}
    context = state.get("context") or {}
    cache = state.get("cache") or {}
    throughput = state.get("throughput") or {}
    cost_available = metric_available(availability, "cost")
    tokens_available = metric_available(availability, "tokens")
    cache_available = metric_available(availability, "cache")
    throughput_available = metric_available(availability, "throughput")
    estimated_cost = bool(state.get("cost_approx") or source.get("approximate_cost"))
    estimated_tokens = bool(source.get("token_estimate"))
    context_pct = context.get("latest_pct")
    cost_label = (
        f"{money(state.get('total_cost'))}{' est' if estimated_cost else ''}"
        if cost_available else "--"
    )
    if context_pct is None:
        context_label = "--% ctx"
    else:
        pct = float(context_pct)
        if pct <= 1:
            pct *= 100
        context_label = f"{int(round(pct))}% ctx"
    if throughput_available and throughput.get("available") and throughput.get("output_tps"):
        output_speed_label = (
            f"{format_token_rate(throughput['output_tps'])} tok/s"
            f"{' est' if estimated_tokens else ''}"
        )
    else:
        output_speed_label = "-- tok/s"
    if cache_available and int(cache.get("total") or 0) > 0:
        share = int(round(float(cache.get("input_share") or 0) * 100))
        cache_label = f"{share}% input cached - {compact_number(cache.get('total'))}"
    elif cache_available:
        cache_label = "no cache yet"
    else:
        cache_label = "unavailable"
    tokens_label = (
        f"{compact_number(state.get('total_tokens'))}{' est' if estimated_tokens else ''}"
        if tokens_available else "--"
    )
    return {
        "model": str(state.get("model") or source.get("model") or "unknown"),
        "cost_label": cost_label,
        "context_label": context_label,
        "output_speed_label": output_speed_label,
        "cache_label": cache_label,
        "tokens_label": tokens_label,
        "estimated_cost": estimated_cost,
        "estimated_tokens": estimated_tokens,
        "cost_available": cost_available,
        "tokens_available": tokens_available,
        "cache_available": cache_available,
        "throughput_available": throughput_available,
        "last_turn_cost": money(state.get("last_turn_cost")) if cost_available else "--",
        "turns": int(state.get("turns") or 0),
        "context_tokens": compact_number(context.get("latest")),
        "context_window": compact_number(context.get("window")),
        "pricing_note": str(source.get("pricing_note") or ""),
        "provider_label": str(source.get("label") or state.get("provider") or "Current session"),
        "project_label": str(source.get("project") or "Current session"),
    }


def idle_label(state, pinned):
    if state.get("ended"):
        base = "pinned log"
    else:
        idle_seconds = int(state.get("idle_s") or 0)
        if idle_seconds < 60:
            base = f"live - {idle_seconds}s idle"
        else:
            base = f"live - {idle_seconds // 60}m idle"
    return f"pinned · {base}" if pinned else base


def run_header_title(state):
    source = state.get("source") or {}
    label = str(source.get("label") or state.get("provider") or "Token Meter")
    project = str(source.get("project") or state.get("project") or "").strip()
    if not project:
        return label
    return f"{label} - {os.path.basename(os.path.expanduser(project))}"


def tray_status_title(state, title_metrics, providers, budget, offline=False):
    if offline:
        return "TM off"
    labels = snapshot_labels(state)
    parts = []
    for metric in TITLE_METRICS:
        key = metric[0]
        if key not in title_metrics:
            continue
        if key == "cost":
            parts.append(labels["cost_label"])
        elif key == "speed":
            parts.append(labels["output_speed_label"])
        elif key == "context":
            parts.append(labels["context_label"])
        elif key == "model":
            parts.append(labels["model"])
        elif key == "limits":
            limits = limits_status_title(providers)
            if limits:
                parts.append(limits)
    base = " · ".join(parts) if parts else "TM"
    if budget_any_exceeded(budget):
        return f"⚠︎ {base}"
    return base


def evaluate_quota_notifications(providers, settings, *, established):
    states = dict(settings.get("quota_notification_states") or {})
    enabled = bool(settings.get("quota_alerts_enabled", True))
    threshold = int(settings.get("quota_alert_threshold") or 80)
    notifications = []
    observed_fresh = False
    for provider in providers or []:
        if not provider_is_fresh(provider):
            continue
        observed_fresh = True
        provider_id = str(provider.get("id") or "")
        provider_label = provider.get("label") or provider_id.title()
        for window in provider.get("windows") or []:
            if not isinstance(window, dict) or window.get("used_percent") is None:
                continue
            window_id = str(window.get("id") or "")
            key = f"{provider_id}:{window_id}"
            used = float(window["used_percent"])
            reset_at = window.get("reset_at")
            previous = states.get(key) or {
                "last_used_percent": used,
                "reset_at": reset_at,
                "fired_thresholds": [],
            }
            fired = set(previous.get("fired_thresholds") or [])
            old_reset = previous.get("reset_at")
            reset_advanced = (
                old_reset is not None and reset_at is not None
                and float(reset_at) > float(old_reset) + 60
            )
            if reset_advanced:
                if (
                    enabled and established
                    and float(previous.get("last_used_percent") or 0) >= threshold
                    and used < threshold
                ):
                    notifications.append((
                        f"{provider_label} quota reset",
                        f"{window.get('label') or 'Quota'} is back to {percentage(used)} used.",
                    ))
                fired = set()
            elif enabled and established:
                crossed = [
                    value for value in sorted(set([threshold, 95, 100]))
                    if float(previous.get("last_used_percent") or 0) < value <= used
                    and value not in fired
                ]
                if crossed:
                    hit = max(crossed)
                    severity = (
                        "exhausted" if hit >= 100 else
                        "critical" if hit >= 95 else "warning"
                    )
                    notifications.append((
                        f"{provider_label} quota {severity}",
                        f"{window.get('label') or 'Quota'} reached {percentage(used)} used; "
                        f"{reset_label(reset_at)}.",
                    ))
                    fired.update(crossed)
            states[key] = {
                "last_used_percent": used,
                "reset_at": reset_at,
                "fired_thresholds": sorted(fired),
            }
    settings["quota_notification_states"] = states
    settings["quota_observation_established"] = bool(observed_fresh or established)
    return notifications


def evaluate_budget_notifications(budget, settings):
    if not budget or not budget.get("configured"):
        return []
    notifications = []
    month = budget["month"]
    exceeded_months = set(settings.get("budget_exceeded_notification_months") or [])
    if (
        budget.get("native_notifications")
        and float(budget.get("percent") or 0) >= 100
        and month not in exceeded_months
    ):
        prefix = "At least " if budget.get("lower_bound") else ""
        notifications.append((
            "Overall monthly budget exceeded",
            f"{prefix}{money(budget['spend'])} of {money(budget['budget'])} "
            f"recorded for {month} ({int(round(budget['percent']))}% used).",
        ))
        exceeded_months.add(month)
        settings["budget_exceeded_notification_months"] = sorted(exceeded_months)
    states = dict(settings.get("budget_notification_states") or {})
    for scope in budget.get("scopes") or []:
        scope_id = scope["id"]
        previous = states.get(scope_id)
        if not previous or previous.get("month") != month:
            states[scope_id] = {
                "month": month,
                "last_percent": float(scope.get("percent") or 0),
                "fired_thresholds": [
                    value for value in budget.get("thresholds") or []
                    if float(scope.get("percent") or 0) >= value
                ],
            }
            continue
        if budget.get("native_notifications"):
            crossed = [
                value for value in budget.get("thresholds") or []
                if float(previous.get("last_percent") or 0) < value
                <= float(scope.get("percent") or 0)
                and value not in set(previous.get("fired_thresholds") or [])
            ]
            overall_already = (
                scope_id == "overall"
                and float(budget.get("percent") or 0) >= 100
                and month in exceeded_months
            )
            if crossed and not overall_already:
                hit = max(crossed)
                prefix = "At least " if budget.get("lower_bound") else ""
                if scope_id == "overall":
                    body = (
                        f"{prefix}{money(budget['spend'])} of {money(budget['budget'])} "
                        f"recorded for {month}."
                    )
                else:
                    body = (
                        f"{scope['label']} reached {int(round(scope['percent']))}% "
                        "of its allocation."
                    )
                notifications.append((
                    f"{scope['label']} monthly budget reached {hit}%",
                    body,
                ))
            fired = set(previous.get("fired_thresholds") or [])
            fired.update(crossed)
            previous["fired_thresholds"] = sorted(fired)
        previous["last_percent"] = float(scope.get("percent") or 0)
        states[scope_id] = previous
    settings["budget_notification_states"] = states
    return notifications


def deliver_notification(title, body):
    if os.environ.get("TOKEN_METER_TRAY_SMOKE") == "1":
        return
    try:
        subprocess.Popen(
            ["notify-send", title, body],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        pass


def dashboard_url(panel, pinned_session=None, include_pinned_session=True):
    if include_pinned_session and pinned_session:
        return f"{BASE_URL}/sessions/{quote(pinned_session, safe='')}#{panel.lstrip('#')}"
    return f"{BASE_URL}/#{panel.lstrip('#')}"


GTK_AVAILABLE = False
try:
    import gi
    gi.require_version("Gtk", "3.0")
    try:
        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import AyatanaAppIndicator3 as AppIndicator
    except (ValueError, ImportError):
        gi.require_version("AppIndicator3", "0.1")
        from gi.repository import AppIndicator3 as AppIndicator
    from gi.repository import Gio, GLib, Gtk
    GTK_AVAILABLE = True
except (ImportError, ValueError):
    AppIndicator = None
    GLib = Gtk = Gio = None


def usage_widget_script():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "token_meter_widget.py")


def _dbus_session_call(method, params, reply_type):
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    return bus.call_sync(
        "org.freedesktop.DBus",
        "/org/freedesktop/DBus",
        "org.freedesktop.DBus",
        method,
        params,
        reply_type,
        Gio.DBusCallFlags.NONE,
        500,
        None,
    )


def usage_widget_running():
    if Gio is None or GLib is None:
        return False
    try:
        reply = _dbus_session_call(
            "NameHasOwner",
            GLib.Variant("(s)", (WIDGET_APPLICATION_ID,)),
            GLib.VariantType("(b)"),
        )
        return bool(reply.unpack()[0])
    except Exception:
        return False


def usage_widget_pid():
    if Gio is None or GLib is None:
        return None
    try:
        reply = _dbus_session_call(
            "GetConnectionUnixProcessID",
            GLib.Variant("(s)", (WIDGET_APPLICATION_ID,)),
            GLib.VariantType("(u)"),
        )
        return int(reply.unpack()[0])
    except Exception:
        return None


def spawn_usage_widget(args=None):
    """Start, raise, or stop the separate desktop widget process. Never embed it here."""
    if os.environ.get("TOKEN_METER_TRAY_SMOKE") == "1":
        return
    if args and "--quit" in args:
        pid = usage_widget_pid()
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        return
    env = os.environ.copy()
    if (
        env.get("XDG_SESSION_TYPE") == "wayland"
        and env.get("DISPLAY")
        and not env.get("GDK_BACKEND")
    ):
        env["GDK_BACKEND"] = "x11"
    try:
        subprocess.Popen(
            [sys.executable, usage_widget_script()],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=env,
        )
    except OSError as exc:
        print(f"Token Meter could not open the usage widget: {exc}", file=sys.stderr)


def gtk_requirements_message():
    return (
        "Token Meter's Linux tray needs GTK 3, PyGObject, and Ayatana AppIndicator.\n"
        "Debian/Ubuntu: sudo apt install python3-gi gir1.2-ayatanaappindicator3-0.1\n"
        "Fedora: sudo dnf install python3-gobject libayatana-appindicator-gtk3\n"
        "Arch: sudo pacman -S python-gobject libayatana-appindicator"
    )


class TokenMeterTray:
    """Grouped GTK menu built once and updated in place."""

    def __init__(self):
        self.settings = self.load_state()
        self.pinned_session = self.settings.get("pinned_session")
        self.selected_tab = self.settings.get("selected_tab") or "run"
        self.title_metrics = set(self.settings.get("title_metrics") or DEFAULT_TITLE_METRICS)
        self.quota_alerts_enabled = bool(self.settings.get("quota_alerts_enabled", True))
        self.quota_alert_threshold = int(self.settings.get("quota_alert_threshold") or 80)
        self.quota_observation_established = bool(
            self.settings.get("quota_observation_established")
        )
        self.usage_widget_open = bool(self.settings.get("usage_widget_open", True))
        self.snapshot = {}
        self.monthly_budget = None
        self.provider_quotas = []
        self.error = "Waiting for Token Meter"
        self.menu_open = False
        self.pending_refresh = False
        self.indicator = AppIndicator.Indicator.new(
            "token-meter", "utilities-system-monitor",
            AppIndicator.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.indicator.set_title("Token Meter")
        self.menu = Gtk.Menu()
        self.menu.connect("show", self._on_menu_show)
        self.menu.connect("hide", self._on_menu_hide)
        self.connection_item = self._metric_item("")
        self.tab_separator = Gtk.SeparatorMenuItem()
        self.view_item, self.view_menu = self._submenu_item("View")
        self.tab_items = {}
        tab_group = None
        for tab_id, tab_label in MENU_TABS:
            item = Gtk.RadioMenuItem.new_with_label_from_widget(tab_group, tab_label)
            tab_group = item
            item.token_meter_tab = tab_id
            item.connect("toggled", self._on_tab_toggled)
            self.tab_items[tab_id] = item
            self.view_menu.append(item)
        self.run_header = self._metric_item("")
        self.run_items = {
            key: self._metric_item("")
            for key in (
                "model", "cost", "context", "output_speed", "tokens",
                "cache", "last_execution", "monthly_budget",
            )
        }
        self.sessions_separator = Gtk.SeparatorMenuItem()
        self.sessions_item, self.sessions_menu = self._submenu_item("Recent sessions")
        self.follow_latest_item = self._action_item(
            "Follow Latest", lambda *_: self.set_pinned(None),
        )
        self.sessions_menu.append(self.follow_latest_item)
        self.session_items = []
        for _ in range(MAX_RECENT_SESSIONS):
            item = Gtk.MenuItem(label="")
            item.token_meter_session_id = None
            item.connect("activate", self._on_session_activate)
            self.session_items.append(item)
            self.sessions_menu.append(item)
        self.overview_separator = Gtk.SeparatorMenuItem()
        self.overview_items = {
            "signal": self._metric_item(""),
            "footer": self._metric_item(""),
        }
        self.overview_provider_items = [self._metric_item("") for _ in range(MAX_PROVIDERS)]
        self.provider_detail_separator = Gtk.SeparatorMenuItem()
        self.provider_detail_items = {
            "header": self._metric_item(""),
            "empty": self._metric_item(""),
            "coverage": self._metric_item(""),
            "footer": self._metric_item(""),
        }
        self.provider_detail_windows = []
        for _ in range(MAX_PROVIDER_WINDOWS):
            self.provider_detail_windows.append({
                "separator": Gtk.SeparatorMenuItem(),
                "quota": self._metric_item(""),
                "reset": self._metric_item(""),
                "pace": self._metric_item(""),
            })
        self.actions_separator = Gtk.SeparatorMenuItem()
        self.usage_widget_item = Gtk.CheckMenuItem(label="Usage widget")
        self.usage_widget_item.connect("toggled", self._on_usage_widget_toggled)
        self.action_items = {
            "Open Dashboard": self._action_item(
                "Open Dashboard", lambda *_: self.open_dashboard(),
            ),
            "Open Spend": self._action_item(
                "Open Spend",
                lambda *_: self.open_url("#spend", include_pinned_session=False),
            ),
            "Open Budget Settings": self._action_item(
                "Open Budget Settings",
                lambda *_: self.open_url("#settings-budgets", include_pinned_session=False),
            ),
            "Open Tools": self._action_item(
                "Open Tools",
                lambda *_: self.open_url("#capabilities", include_pinned_session=False),
            ),
        }
        self.settings_item, self.settings_menu = self._submenu_item("Settings")
        self.model_prices_item = self._action_item(
            "Model Prices",
            lambda *_: self.open_url("#model-pricing", include_pinned_session=False),
        )
        self.settings_menu.append(self.model_prices_item)
        self.settings_menu.append(Gtk.SeparatorMenuItem())
        self.title_metrics_item, self.title_metrics_menu = self._submenu_item("Menu bar title")
        self.title_metric_items = {}
        for metric_id, metric_label in TITLE_METRICS:
            item = Gtk.CheckMenuItem(label=metric_label)
            item.token_meter_metric = metric_id
            item.connect("toggled", self._on_title_metric_toggled)
            self.title_metric_items[metric_id] = item
            self.title_metrics_menu.append(item)
        self.title_metrics_item.set_submenu(self.title_metrics_menu)
        self.settings_menu.append(self.title_metrics_item)
        self.quota_alerts_item = Gtk.CheckMenuItem(label="Quota notifications")
        self.quota_alerts_item.connect("toggled", self._on_quota_alerts_toggled)
        self.settings_menu.append(self.quota_alerts_item)
        self.threshold_item, self.threshold_menu = self._submenu_item("Warn at")
        self.threshold_items = {}
        threshold_group = None
        for threshold in QUOTA_THRESHOLDS:
            item = Gtk.RadioMenuItem.new_with_label_from_widget(threshold_group, f"{threshold}%")
            threshold_group = item
            item.token_meter_threshold = threshold
            item.connect("toggled", self._on_threshold_toggled)
            self.threshold_items[threshold] = item
            self.threshold_menu.append(item)
        self.threshold_item.set_submenu(self.threshold_menu)
        self.settings_menu.append(self.threshold_item)
        self.quit_separator = Gtk.SeparatorMenuItem()
        self.quit_item = self._action_item("Quit Token Meter Tray", lambda *_: Gtk.main_quit())
        self._assemble_menu()
        self.indicator.set_menu(self.menu)
        self.poll()
        GLib.timeout_add_seconds(2, self.poll)
        if self.usage_widget_open:
            GLib.idle_add(self._start_usage_widget)

    def _submenu_item(self, label):
        item = Gtk.MenuItem(label=label)
        item.set_reserve_indicator(True)
        submenu = Gtk.Menu()
        item.set_submenu(submenu)
        return item, submenu

    def _metric_item(self, label):
        row = Gtk.MenuItem(label=label)
        row.set_sensitive(False)
        return row

    def _action_item(self, label, callback):
        row = Gtk.MenuItem(label=label)
        row.connect("activate", callback)
        return row

    def _assemble_menu(self):
        self.menu.append(self.connection_item)
        self.menu.append(self.tab_separator)
        self.menu.append(self.view_item)
        self.menu.append(self.run_header)
        for key in self.run_items:
            self.menu.append(self.run_items[key])
        self.menu.append(self.sessions_separator)
        self.menu.append(self.sessions_item)
        self.menu.append(self.overview_separator)
        self.menu.append(self.overview_items["signal"])
        for item in self.overview_provider_items:
            self.menu.append(item)
        self.menu.append(self.overview_items["footer"])
        self.menu.append(self.provider_detail_separator)
        self.menu.append(self.provider_detail_items["header"])
        self.menu.append(self.provider_detail_items["empty"])
        for window in self.provider_detail_windows:
            self.menu.append(window["separator"])
            self.menu.append(window["quota"])
            self.menu.append(window["reset"])
            self.menu.append(window["pace"])
        self.menu.append(self.provider_detail_items["coverage"])
        self.menu.append(self.provider_detail_items["footer"])
        self.menu.append(self.actions_separator)
        self.menu.append(self.usage_widget_item)
        for item in self.action_items.values():
            self.menu.append(item)
        self.menu.append(self.settings_item)
        self.menu.append(self.quit_separator)
        self.menu.append(self.quit_item)
        self.menu.show_all()

    @staticmethod
    def _set_visible(widget, visible):
        if visible:
            widget.show()
        else:
            widget.hide()

    def _on_menu_show(self, _menu):
        self.menu_open = True
        self._sync_usage_widget_item()

    def _on_menu_hide(self, _menu):
        self.menu_open = False
        if self.pending_refresh:
            self.pending_refresh = False
            GLib.idle_add(self._apply_menu_content)

    def load_state(self):
        try:
            with open(STATE_PATH, encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, ValueError):
            value = {}
        if not isinstance(value, dict):
            value = {}
        merged = dict(DEFAULT_STATE)
        merged.update(value)
        if not merged.get("title_metrics"):
            merged["title_metrics"] = list(DEFAULT_TITLE_METRICS)
        threshold = int(merged.get("quota_alert_threshold") or 80)
        if threshold not in QUOTA_THRESHOLDS:
            merged["quota_alert_threshold"] = 80
        return merged

    def save_state(self):
        payload = {
            "pinned_session": self.pinned_session,
            "selected_tab": self.selected_tab,
            "title_metrics": [key for key, _ in TITLE_METRICS if key in self.title_metrics],
            "quota_alerts_enabled": self.quota_alerts_enabled,
            "quota_alert_threshold": self.quota_alert_threshold,
            "quota_notification_states": self.settings.get("quota_notification_states") or {},
            "budget_notification_states": self.settings.get("budget_notification_states") or {},
            "budget_exceeded_notification_months": (
                self.settings.get("budget_exceeded_notification_months") or []
            ),
            "quota_observation_established": self.quota_observation_established,
            "usage_widget_open": self.usage_widget_open,
        }
        try:
            os.makedirs(os.path.dirname(STATE_PATH), mode=0o700, exist_ok=True)
            temporary = STATE_PATH + ".tmp"
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            os.chmod(temporary, 0o600)
            os.replace(temporary, STATE_PATH)
            self.settings.update(payload)
        except OSError as exc:
            print(f"Token Meter could not save tray state: {exc}", file=sys.stderr)

    def fetch(self):
        url = STATE_URL
        if self.pinned_session:
            url += "?session=" + quote(str(self.pinned_session), safe="")
        request = Request(url, headers={"Cache-Control": "no-cache", "Pragma": "no-cache"})
        with urlopen(request, timeout=5) as response:
            if response.status != 200:
                raise URLError(f"HTTP {response.status}")
            return json.load(response)

    def poll(self):
        try:
            payload = self.fetch()
            if not isinstance(payload, dict):
                raise ValueError("unreadable response")
            if (payload.get("selection") or {}).get("missing"):
                self.pinned_session = None
                self.save_state()
            self.snapshot = payload
            self.provider_quotas = payload.get("provider_quotas") or []
            self.monthly_budget = parse_monthly_budget(payload.get("budget"))
            self.error = ""
            self._evaluate_notifications()
        except (OSError, ValueError, URLError) as exc:
            self.snapshot = {}
            self.provider_quotas = []
            self.monthly_budget = None
            self.error = str(exc)
        self.update_tray_label()
        self.refresh_menu_content()
        return True

    def _evaluate_notifications(self):
        quota_notes = evaluate_quota_notifications(
            self.provider_quotas,
            self.settings,
            established=self.quota_observation_established,
        )
        self.quota_observation_established = bool(
            self.settings.get("quota_observation_established")
        )
        budget_notes = evaluate_budget_notifications(self.monthly_budget, self.settings)
        changed = bool(quota_notes or budget_notes)
        if changed:
            self.save_state()
        for title, body in quota_notes + budget_notes:
            deliver_notification(title, body)

    def update_tray_label(self):
        if self.error:
            title = tray_status_title({}, self.title_metrics, [], None, offline=True)
            self.indicator.set_label("TM off", "TM off")
        else:
            title = tray_status_title(
                self.snapshot, self.title_metrics, self.provider_quotas, self.monthly_budget,
            )
            self.indicator.set_label(title, "⚠︎ $999.99 · 999 tok/s · 100% ctx")
        self.indicator.set_title(title)

    def open_url(self, path="", include_pinned_session=True):
        url = dashboard_url(path, self.pinned_session, include_pinned_session)
        try:
            subprocess.Popen(
                ["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            print(f"Token Meter could not open {url}: {exc}", file=sys.stderr)

    def open_dashboard(self):
        if self.pinned_session:
            self.open_url("#summary")
        else:
            self.open_url("#sessions", include_pinned_session=False)

    def set_pinned(self, session_id):
        self.pinned_session = session_id
        self.save_state()
        self.menu_open = False
        self.pending_refresh = False
        self.poll()

    def set_selected_tab(self, tab_id):
        if tab_id not in dict(MENU_TABS):
            return
        self.selected_tab = tab_id
        self.save_state()
        self.refresh_menu_content()

    def _on_tab_toggled(self, item):
        if not item.get_active():
            return
        tab_id = getattr(item, "token_meter_tab", None)
        if tab_id:
            self.set_selected_tab(tab_id)

    def _on_title_metric_toggled(self, item):
        metric = getattr(item, "token_meter_metric", None)
        if not metric:
            return
        if item.get_active():
            self.title_metrics.add(metric)
        else:
            self.title_metrics.discard(metric)
        self.save_state()
        self.update_tray_label()
        self.refresh_menu_content()

    def _on_quota_alerts_toggled(self, item):
        self.quota_alerts_enabled = item.get_active()
        self.save_state()

    def _sync_usage_widget_item(self):
        if usage_widget_running():
            self.usage_widget_open = True
        if self.usage_widget_item.get_active() != bool(self.usage_widget_open):
            self.usage_widget_item.handler_block_by_func(self._on_usage_widget_toggled)
            self.usage_widget_item.set_active(bool(self.usage_widget_open))
            self.usage_widget_item.handler_unblock_by_func(self._on_usage_widget_toggled)

    def _start_usage_widget(self):
        if self.usage_widget_open:
            spawn_usage_widget()
        return False

    def _on_usage_widget_toggled(self, item):
        want = bool(item.get_active())
        self.usage_widget_open = want
        self.save_state()
        running = usage_widget_running()
        if want and not running:
            spawn_usage_widget()
        elif not want and running:
            spawn_usage_widget(["--quit"])
        self.refresh_menu_content()

    def _on_threshold_toggled(self, item):
        if not item.get_active():
            return
        threshold = getattr(item, "token_meter_threshold", None)
        if threshold in QUOTA_THRESHOLDS:
            self.quota_alert_threshold = threshold
            self.save_state()
            self.refresh_menu_content()

    def _on_session_activate(self, item):
        session_id = getattr(item, "token_meter_session_id", None)
        if session_id:
            self.set_pinned(session_id)

    def _set_item_label(self, item, label):
        item.set_label(label)

    def refresh_menu_content(self):
        if self.menu_open:
            self.pending_refresh = True
            return
        self._apply_menu_content()

    def _hide_run_section(self):
        self._set_visible(self.run_header, False)
        for item in self.run_items.values():
            self._set_visible(item, False)
        self._set_visible(self.sessions_separator, False)
        self._set_visible(self.sessions_item, False)

    def _hide_overview_section(self):
        self._set_visible(self.overview_separator, False)
        self._set_visible(self.overview_items["signal"], False)
        for item in self.overview_provider_items:
            self._set_visible(item, False)
        self._set_visible(self.overview_items["footer"], False)

    def _hide_provider_detail_section(self):
        self._set_visible(self.provider_detail_separator, False)
        self._set_visible(self.provider_detail_items["header"], False)
        self._set_visible(self.provider_detail_items["empty"], False)
        self._set_visible(self.provider_detail_items["coverage"], False)
        self._set_visible(self.provider_detail_items["footer"], False)
        for window in self.provider_detail_windows:
            for key in window:
                self._set_visible(window[key], False)

    def _apply_menu_content(self):
        for tab_id, item in self.tab_items.items():
            item.set_active(tab_id == self.selected_tab)
        self.quota_alerts_item.set_active(self.quota_alerts_enabled)
        self._sync_usage_widget_item()
        for threshold, item in self.threshold_items.items():
            item.set_active(threshold == self.quota_alert_threshold)
        for metric_id, item in self.title_metric_items.items():
            item.set_active(metric_id in self.title_metrics)

        if self.error:
            self._set_visible(self.connection_item, True)
            self._set_item_label(self.connection_item, f"Connection:  {self.error}")
            self._hide_run_section()
            self._hide_overview_section()
            self._hide_provider_detail_section()
            self._set_visible(self.tab_separator, False)
            self._set_visible(self.view_item, False)
            return False

        self._set_visible(self.connection_item, False)
        self._set_visible(self.tab_separator, True)
        self._set_visible(self.view_item, True)
        self._hide_run_section()
        self._hide_overview_section()
        self._hide_provider_detail_section()

        if self.selected_tab == "run":
            self._apply_run_tab()
        elif self.selected_tab == "overview":
            self._apply_overview_tab()
        else:
            self._apply_provider_tab(self.selected_tab)
        return False

    def _apply_run_tab(self):
        state = self.snapshot
        labels = snapshot_labels(state)
        self._set_visible(self.run_header, True)
        self._set_item_label(self.run_header, f"Run:  {run_header_title(state)} · {idle_label(state, bool(self.pinned_session))}")
        run_rows = {
            "model": f"Model:  {labels['model']}",
            "cost": f"Cost:  {labels['cost_label']}",
            "context": (
                f"Context:  {labels['context_label']} · "
                f"{labels['context_tokens']} / {labels['context_window']}"
            ),
            "output_speed": f"Output speed:  {labels['output_speed_label']}",
            "tokens": f"Tokens:  {labels['tokens_label']} · {labels['turns']} execs",
            "cache": f"Cache:  {labels['cache_label']}",
            "last_execution": (
                f"Last execution:  {labels['last_turn_cost']}"
                f"{' est' if labels['estimated_cost'] else ''}"
            ),
            "monthly_budget": f"Monthly budget:  {budget_compact_label(self.monthly_budget)}",
        }
        for key, label in run_rows.items():
            self._set_visible(self.run_items[key], True)
            self._set_item_label(self.run_items[key], label)

        sessions = state.get("recent_sessions") or []
        show_sessions = bool(sessions)
        self._set_visible(self.sessions_separator, show_sessions)
        self._set_visible(self.sessions_item, show_sessions)
        self._set_visible(self.follow_latest_item, show_sessions)
        for index, item in enumerate(self.session_items):
            if not show_sessions or index >= len(sessions[:MAX_RECENT_SESSIONS]):
                item.token_meter_session_id = None
                self._set_visible(item, False)
                continue
            session = sessions[index]
            session_id = str(session.get("id") or "")
            if not session_id:
                item.token_meter_session_id = None
                self._set_visible(item, False)
                continue
            marker = "✓ " if session_id == self.pinned_session else ""
            self._set_item_label(
                item,
                f"{marker}{session_menu_title(session, state.get('runtime_catalog'))}",
            )
            item.token_meter_session_id = session_id
            self._set_visible(item, True)

    def _apply_overview_tab(self):
        providers = self.provider_quotas
        self._set_visible(self.overview_separator, True)
        constrained = most_constrained_quota(providers)
        if constrained:
            provider, window = constrained
            label = provider.get("label") or str(provider.get("id") or "Provider").title()
            self._set_visible(self.overview_items["signal"], True)
            self._set_item_label(
                self.overview_items["signal"],
                "Most constrained:  "
                f"{label} · {window.get('label') or 'Quota'} · "
                f"{percentage(window.get('used_percent'))} used",
            )
        elif providers and all(row.get("status") != "loading" for row in providers):
            self._set_visible(self.overview_items["signal"], True)
            self._set_item_label(
                self.overview_items["signal"],
                "Provider limits:  No fresh provider-reported quota is available.",
            )
        ranked = sorted(
            providers,
            key=lambda row: (
                float((highest_quota_window(row) or {}).get("used_percent") or -1)
                if provider_is_fresh(row) else -1,
                str(row.get("label") or row.get("id") or ""),
            ),
            reverse=True,
        )
        for index, item in enumerate(self.overview_provider_items):
            if index >= len(ranked[:MAX_PROVIDERS]):
                self._set_visible(item, False)
                continue
            provider = ranked[index]
            window = highest_quota_window(provider)
            label = provider.get("label") or str(provider.get("id") or "Provider").title()
            if window:
                suffix = ""
                if provider.get("stale"):
                    suffix = " · stale"
                elif provider.get("status") == "error":
                    suffix = " · last good"
                value = f"{percentage(window.get('used_percent'))} · {window.get('label') or 'Quota'}{suffix}"
            else:
                value = "loading…" if provider.get("status") == "loading" else "unavailable"
            self._set_item_label(item, f"{label}:  {value}")
            self._set_visible(item, True)
        if not providers:
            self._set_visible(self.overview_items["signal"], True)
            self._set_item_label(
                self.overview_items["signal"],
                "Provider limits:  Loading provider quotas.",
            )
        self._set_visible(self.overview_items["footer"], bool(providers))
        if providers:
            self._set_item_label(
                self.overview_items["footer"],
                "Only provider-reported limits are shown · refreshes every minute",
            )

    def _apply_provider_tab(self, provider_id):
        provider = next(
            (row for row in self.provider_quotas if str(row.get("id") or "") == provider_id),
            None,
        )
        self._set_visible(self.provider_detail_separator, True)
        if not provider:
            self._set_visible(self.provider_detail_items["header"], True)
            self._set_item_label(
                self.provider_detail_items["header"],
                f"{provider_id.title()}:  Loading provider quotas.",
            )
            return
        label = provider.get("label") or provider_id.title()
        plan = str(provider.get("plan") or "").strip()
        status = provider.get("status") or "unavailable"
        age_seconds = provider.get("age_seconds")
        if provider.get("stale"):
            freshness = f"Stale · {format_compact_duration(age_seconds)} old" if age_seconds else "Stale"
        elif age_seconds is None:
            freshness = "Loading" if status == "loading" else "Not refreshed"
        elif int(age_seconds) < 10:
            freshness = "Updated now"
        else:
            freshness = f"Updated {format_compact_duration(age_seconds)} ago"
        subtitle = " · ".join(part for part in (plan or None, freshness) if part)
        self._set_visible(self.provider_detail_items["header"], True)
        self._set_item_label(self.provider_detail_items["header"], f"{label}:  {subtitle}")
        windows = provider.get("windows") or []
        self._set_visible(self.provider_detail_items["empty"], not windows)
        if not windows:
            message = provider.get("error") or "No provider-reported quota window is available."
            self._set_item_label(self.provider_detail_items["empty"], f"Quota unavailable:  {message}")
        stale = bool(provider.get("stale") or provider.get("status") == "error")
        for index, window in enumerate(self.provider_detail_windows):
            if index >= len(windows[:MAX_PROVIDER_WINDOWS]):
                for key in window:
                    self._set_visible(window[key], False)
                continue
            data = windows[index]
            pace = data.get("pace") or {}
            suffix = " · stale" if stale else ""
            self._set_visible(window["separator"], True)
            self._set_visible(window["quota"], True)
            self._set_item_label(
                window["quota"],
                f"{data.get('label') or 'Quota'}:  {percentage(data.get('used_percent'))}{suffix}",
            )
            self._set_visible(window["reset"], True)
            self._set_item_label(window["reset"], f"Reset:  {reset_label(data.get('reset_at'))}")
            show_pace = bool(pace.get("summary"))
            self._set_visible(window["pace"], show_pace)
            if show_pace:
                self._set_item_label(window["pace"], f"Pace:  {pace['summary']}")
        coverage = provider.get("coverage_note")
        self._set_visible(self.provider_detail_items["coverage"], bool(coverage))
        if coverage:
            self._set_item_label(self.provider_detail_items["coverage"], f"Coverage:  {coverage}")
        provenance = "Provider-reported" if provider.get("provenance") == "provider_reported" else "Unavailable"
        source = provider.get("source") or "Provider account"
        footer = f"{provenance} · {source} · {freshness}"
        if provider.get("status") == "error" and windows:
            footer = "Last refresh failed · showing last good values · " + footer
        self._set_visible(self.provider_detail_items["footer"], True)
        self._set_item_label(self.provider_detail_items["footer"], footer)


def run_smoke():
    request = Request(STATE_URL, headers={"Cache-Control": "no-cache", "Pragma": "no-cache"})
    with urlopen(request, timeout=5) as response:
        payload = json.load(response)
    settings = dict(DEFAULT_STATE)
    labels = snapshot_labels(payload)
    budget = parse_monthly_budget(payload.get("budget"))
    providers = payload.get("provider_quotas") or []
    title = tray_status_title(payload, set(DEFAULT_TITLE_METRICS), providers, budget)
    print(labels["output_speed_label"])
    print(f"active-title={title}")
    print(
        "budget-state="
        f"{budget_compact_label(budget)} exceeded={budget_any_exceeded(budget)}"
    )
    print(
        "tab=Run title-metrics="
        + ",".join(label for key, label in TITLE_METRICS if key in DEFAULT_TITLE_METRICS)
    )
    print(
        f"quota-alerts={'on' if settings['quota_alerts_enabled'] else 'off'} "
        f"warn-at={settings['quota_alert_threshold']}%"
    )
    for provider in providers:
        windows = ",".join(
            f"{window.get('label')}={percentage(window.get('used_percent'))}"
            for window in (provider.get("windows") or [])
        ) or "none"
        coverage = provider.get("coverage_note") or "complete"
        label = provider.get("label") or provider.get("id")
        print(
            f"quota={label} status={provider.get('status')} "
            f"windows={windows} coverage={coverage}"
        )


if __name__ == "__main__":
    if "--check" in sys.argv:
        raise SystemExit(0)
    if "--smoke" in sys.argv:
        try:
            run_smoke()
        except (OSError, ValueError, URLError) as exc:
            print(f"Token Meter tray smoke failed: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        raise SystemExit(0)
    if not GTK_AVAILABLE:
        print(gtk_requirements_message(), file=sys.stderr)
        raise SystemExit(1)
    TokenMeterTray()
    Gtk.main()
