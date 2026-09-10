#!/usr/bin/env python3
"""Native Linux desktop usage widget for Token Meter.

Runs as a separate GTK process from the AppIndicator tray so an overlay
window cannot take down the tray (or systemd Restart=on-failure).
"""
import json
import os
import subprocess
import sys
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

BASE_URL = os.environ.get("TOKEN_METER_URL", "http://127.0.0.1:8722").rstrip("/")
STATE_URL = BASE_URL + "/menubar"
APPLICATION_ID = "com.tokenmeter.usagewidget"
UNKNOWN_RUNTIME = "unknown-runtime"
POLL_SECONDS = 4
COLLAPSED_SIZE = (36, 168)
EXPANDED_SIZE = (300, 520)
PANEL_MARGIN_X = 12
DEFAULT_TOP = 96
DRAG_THRESHOLD = 6

GTK_AVAILABLE = False
Gdk = GLib = Gtk = Gio = None


def usage_widget_chips(payload):
    catalog = payload.get("runtime_catalog") if isinstance(payload, dict) else None
    if not isinstance(catalog, dict):
        catalog = {}
    quotas = payload.get("provider_quotas") if isinstance(payload, dict) else None
    sessions = payload.get("recent_sessions") if isinstance(payload, dict) else None
    chips = []
    seen = set()

    def add(provider_id, label=None):
        provider_id = str(provider_id or "")
        if not provider_id or provider_id == UNKNOWN_RUNTIME or provider_id in seen:
            return
        seen.add(provider_id)
        meta = catalog.get(provider_id) if isinstance(catalog.get(provider_id), dict) else {}
        chips.append({
            "id": provider_id,
            "label": str(meta.get("label") or label or provider_id),
            "has_quota": False,
        })

    for row in quotas or ():
        if isinstance(row, dict):
            add(row.get("id"), row.get("label"))
    for row in sessions or ():
        if isinstance(row, dict):
            add(row.get("provider"), row.get("label"))
    quota_ids = {
        str(row.get("id") or "")
        for row in (quotas or ())
        if isinstance(row, dict)
    }
    for chip in chips:
        chip["has_quota"] = chip["id"] in quota_ids
    return chips


def _quota_windows(row):
    windows = row.get("windows") if isinstance(row, dict) else None
    return [window for window in (windows or ()) if isinstance(window, dict)]


def _hottest_percent(windows):
    hottest = None
    for window in windows:
        used = window.get("used_percent")
        if used is None:
            continue
        try:
            used = float(used)
        except (TypeError, ValueError):
            continue
        if hottest is None or used > hottest:
            hottest = used
    return hottest


def _primary_quota(quotas):
    ranked = []
    for row in quotas:
        windows = _quota_windows(row)
        if not windows:
            continue
        ranked.append(( _hottest_percent(windows) or -1, row, windows))
    if not ranked:
        return None, []
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[0][1], ranked[0][2]


def _chip_label(chips, provider_id, payload):
    for chip in chips:
        if chip["id"] == provider_id:
            return chip["label"]
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    return str(source.get("label") or "Token Meter")


def usage_widget_view(payload, selected_id=""):
    payload = payload if isinstance(payload, dict) else {}
    chips = usage_widget_chips(payload)
    selected_id = str(selected_id or "")
    known = {chip["id"] for chip in chips}
    if selected_id not in known:
        selected_id = ""
    quotas = [
        row for row in (payload.get("provider_quotas") or ())
        if isinstance(row, dict)
    ]
    sessions = [
        row for row in (payload.get("recent_sessions") or ())
        if isinstance(row, dict)
    ]
    if selected_id:
        quotas = [row for row in quotas if str(row.get("id") or "") == selected_id]
        sessions = [row for row in sessions if str(row.get("provider") or "") == selected_id]
        primary = quotas[0] if quotas else None
        windows = _quota_windows(primary) if primary else []
        title = _chip_label(chips, selected_id, payload)
    else:
        primary, windows = _primary_quota(quotas)
        title = (primary or {}).get("label") or _chip_label(
            chips, str((primary or {}).get("id") or ""), payload,
        )
        if not primary:
            title = str((payload.get("source") or {}).get("label") or "Token Meter")
    live = payload.get("live_throughput") if isinstance(payload.get("live_throughput"), dict) else {}
    current_provider = str(payload.get("provider") or "")
    live_ok = bool(live.get("available")) and (
        not selected_id or current_provider == selected_id
    )
    try:
        live_tps = float(live.get("output_tps") or 0) if live_ok else None
    except (TypeError, ValueError):
        live_tps = None
    if live_tps is not None and live_tps <= 0:
        live_tps = None
    return {
        "chips": chips,
        "selected_id": selected_id,
        "title": title,
        "windows": windows,
        "sessions": sessions[:5],
        "hottest": _hottest_percent(windows),
        "quota_available": bool(windows),
        "live_tps": live_tps,
        "ended": bool(payload.get("ended")),
    }


def fetch_menubar():
    request = Request(STATE_URL, headers={"Cache-Control": "no-cache", "Pragma": "no-cache"})
    with urlopen(request, timeout=5) as response:
        if response.status != 200:
            raise URLError(f"HTTP {response.status}")
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("unreadable response")
    return payload


def session_url(session_id):
    return f"{BASE_URL}/sessions/{quote(str(session_id or ''), safe='')}#summary"


def compact_number(value):
    value = float(value or 0)
    magnitude = abs(value)
    if magnitude >= 1_000_000:
        text = f"{value / 1_000_000:.1f}".rstrip("0").rstrip(".")
        return text + "M"
    if magnitude >= 1_000:
        text = f"{value / 1_000:.1f}".rstrip("0").rstrip(".")
        return text + "K"
    if magnitude >= 10:
        return f"{value:.0f}"
    return f"{value:.1f}".rstrip("0").rstrip(".")


def percent_label(value):
    if value is None:
        return "--"
    return f"{float(value):.0f}%"


def widget_state_path():
    config_home = os.path.expanduser(os.environ.get("XDG_CONFIG_HOME") or "~/.config")
    return os.path.join(config_home, "token-meter", "widget.json")


def load_widget_position():
    try:
        with open(widget_state_path(), encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def save_widget_position(payload):
    path = widget_state_path()
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        temporary = path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except OSError:
        pass


def usage_widget_size(expanded):
    if expanded:
        return (
            COLLAPSED_SIZE[0] + EXPANDED_SIZE[0] + 2 * PANEL_MARGIN_X,
            max(EXPANDED_SIZE[1], 280),
        )
    return COLLAPSED_SIZE


def usage_widget_anchor(x, width, monitor):
    mx, _my, mw, _mh = monitor
    midpoint = float(x) + float(width) / 2.0
    return "right" if midpoint >= mx + mw / 2.0 else "left"


def usage_widget_dock_x(anchor, width, monitor):
    """X origin that keeps the drawn width flush with the docked wall."""
    mx, _my, mw, _mh = [int(v) for v in (monitor or (0, 0, 1920, 1080))]
    width = max(1, int(width or COLLAPSED_SIZE[0]))
    if str(anchor) == "left":
        return mx
    return mx + mw - width


def usage_widget_snap(x, y, width, height, monitor):
    """Pin the drawer to the nearer left or right edge; keep the drop's Y."""
    mx, my, mw, mh = [int(v) for v in (monitor or (0, 0, 1920, 1080))]
    width = max(1, int(width or COLLAPSED_SIZE[0]))
    height = max(1, int(height or COLLAPSED_SIZE[1]))
    anchor = usage_widget_anchor(x, width, (mx, my, mw, mh))
    snap_y = max(my, min(int(y), my + mh - min(height, mh)))
    return {
        "x": usage_widget_dock_x(anchor, width, (mx, my, mw, mh)),
        "y": snap_y,
        "width": width,
        "height": height,
        "anchor": anchor,
    }


def usage_widget_frame(expanded, position=None, monitor=None):
    """Keep the docked wall flush; left opens rightward, right opens leftward."""
    mx, my, mw, mh = monitor or (0, 0, 1920, 1080)
    mx, my, mw, mh = int(mx), int(my), int(mw), int(mh)
    width, height = usage_widget_size(expanded)
    height = min(height, max(120, mh - 24))
    if not isinstance(position, dict) or position.get("x") is None:
        y = my + DEFAULT_TOP
        anchor = "right"
    else:
        anchor = str(position.get("anchor") or "right")
        if anchor not in ("left", "right"):
            anchor = "right"
        y = int(position["y"]) if position.get("y") is not None else my + DEFAULT_TOP
    x = usage_widget_dock_x(anchor, width, (mx, my, mw, mh))
    y = max(my, min(y, my + mh - min(height, mh)))
    return {
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "anchor": anchor,
    }


def configure_backend():
    if os.environ.get("GDK_BACKEND"):
        return
    if os.environ.get("XDG_SESSION_TYPE") == "wayland" and os.environ.get("DISPLAY"):
        os.environ["GDK_BACKEND"] = "x11"


if __name__ == "__main__":
    configure_backend()

try:
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, Gio, GLib, Gtk
    GTK_AVAILABLE = True
except (ImportError, ValueError):
    pass


if GTK_AVAILABLE:
    CSS = b"""
    window.usage-widget { background: #0b1016; }
    .usage-tab { background: #111820; color: #ffb457; padding: 8px 0; }
    .usage-title { color: #ffb457; font-weight: 700; letter-spacing: 1px; }
    .usage-live { color: #66d990; }
    .usage-dim { color: #8b98a8; }
    .usage-fg { color: #f6f8fb; }
    .usage-chip { padding: 2px 8px; border-radius: 999px; background: rgba(255,255,255,0.04); color: #a8b3c1; }
    .usage-chip:checked { background: rgba(0,188,235,0.16); color: #f6f8fb; }
    progress, trough { min-height: 7px; border-radius: 99px; }
    """

    class UsageWidgetWindow(Gtk.ApplicationWindow):
        def __init__(self, application):
            super().__init__(application=application, title="Token Meter")
            self.selected_id = ""
            self.expanded = False
            self.payload = {}
            self.error = ""
            self.position = load_widget_position()
            self._press = None
            self._dragging = False
            self._pinning = False
            self.set_decorated(False)
            self.set_keep_above(True)
            self.set_skip_taskbar_hint(True)
            self.set_skip_pager_hint(True)
            self.set_accept_focus(True)
            self.set_resizable(False)
            self.set_default_size(*COLLAPSED_SIZE)
            self.set_position(Gtk.WindowPosition.NONE)
            try:
                self.set_type_hint(Gdk.WindowTypeHint.NORMAL)
            except Exception:
                pass
            self.get_style_context().add_class("usage-widget")
            provider = Gtk.CssProvider()
            provider.load_from_data(CSS)
            screen = Gdk.Screen.get_default()
            if screen is not None:
                Gtk.StyleContext.add_provider_for_screen(
                    screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
                )
            self.connect("delete-event", self._on_delete)
            self.connect("realize", self._on_realize)
            self.connect("map-event", self._on_map)
            self.connect("size-allocate", self._on_size_allocate)
            self.body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
            self.add(self.body)
            self.tab = Gtk.EventBox()
            self.tab.get_style_context().add_class("usage-tab")
            self.tab.set_size_request(*COLLAPSED_SIZE)
            self.tab.set_visible_window(True)
            self._bind_drag(self.tab, toggle=True)
            self.tab_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            self.tab_box.set_valign(Gtk.Align.CENTER)
            self.tab_percent = Gtk.Label(label="--")
            self.tab_percent.get_style_context().add_class("usage-title")
            self.tab_caption = Gtk.Label(label="LIMITS")
            self.tab_caption.set_angle(90)
            self.tab_caption.get_style_context().add_class("usage-dim")
            self.tab_box.pack_start(self.tab_percent, False, False, 0)
            self.tab_box.pack_start(self.tab_caption, False, False, 0)
            self.tab.add(self.tab_box)
            self.panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            self.panel.set_margin_top(10)
            self.panel.set_margin_bottom(12)
            self.panel.set_margin_start(12)
            self.panel.set_margin_end(12)
            self.panel.set_size_request(*EXPANDED_SIZE)
            self.panel.set_no_show_all(True)
            self.panel.hide()
            self.chip_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
            self.chip_box.set_homogeneous(False)
            self.head = Gtk.EventBox()
            self.head.set_visible_window(False)
            self._bind_drag(self.head, toggle=False)
            head_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            self.title_label = Gtk.Label(label="TOKEN METER")
            self.title_label.set_xalign(0)
            self.title_label.get_style_context().add_class("usage-title")
            self.live_label = Gtk.Label(label="")
            self.live_label.set_xalign(1)
            self.live_label.get_style_context().add_class("usage-live")
            head_row.pack_start(self.title_label, True, True, 0)
            head_row.pack_end(self.live_label, False, False, 0)
            self.head.add(head_row)
            self.bars = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            self.sessions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            self.panel.pack_start(self.chip_box, False, False, 0)
            self.panel.pack_start(self.head, False, False, 0)
            self.panel.pack_start(self.bars, False, False, 0)
            recent = Gtk.Label(label="RECENT")
            recent.set_xalign(0)
            recent.get_style_context().add_class("usage-dim")
            self.panel.pack_start(recent, False, False, 0)
            self.panel.pack_start(self.sessions, False, False, 0)
            self.body.pack_start(self.tab, False, False, 0)
            self.body.pack_start(self.panel, True, True, 0)
            self.refresh()
            GLib.timeout_add_seconds(POLL_SECONDS, self.refresh)
            self._apply_frame()

        def _bind_drag(self, widget, toggle):
            widget.add_events(
                Gdk.EventMask.BUTTON_PRESS_MASK
                | Gdk.EventMask.BUTTON_RELEASE_MASK
                | Gdk.EventMask.BUTTON_MOTION_MASK
                | Gdk.EventMask.POINTER_MOTION_HINT_MASK
            )
            widget.connect("button-press-event", self._on_drag_press)
            widget.connect("motion-notify-event", self._on_drag_motion)
            widget.connect(
                "button-release-event",
                lambda w, event: self._on_drag_release(w, event, toggle),
            )
            try:
                widget.connect("enter-notify-event", self._on_drag_cursor)
            except TypeError:
                pass

        def _on_drag_cursor(self, widget, _event):
            window = widget.get_window() or self.get_window()
            display = Gdk.Display.get_default()
            if window is None or display is None:
                return False
            try:
                window.set_cursor(Gdk.Cursor.new_from_name(display, "grab"))
            except Exception:
                pass
            return False

        def _backend_x11(self):
            display = Gdk.Display.get_default()
            name = display.get_name() if display is not None else ""
            backend = os.environ.get("GDK_BACKEND") or ""
            return name.startswith(":") or backend == "x11"

        def _monitor_geometry(self, monitor):
            geo = monitor.get_geometry()
            return (geo.x, geo.y, geo.width, geo.height)

        def _monitor_at(self, x, y):
            display = Gdk.Display.get_default()
            monitor = display.get_primary_monitor() or display.get_monitor(0)
            try:
                at_point = display.get_monitor_at_point(int(x), int(y))
                if at_point is not None:
                    monitor = at_point
            except Exception:
                pass
            return self._monitor_geometry(monitor)

        def _monitor(self):
            display = Gdk.Display.get_default()
            x = y = 0
            try:
                x, y = self._window_origin()
            except Exception:
                pass
            saved = self.position if isinstance(self.position, dict) else {}
            if not x and not y and saved.get("x") is not None:
                x = int(saved.get("x") or 0)
                y = int(saved.get("y") or 0)
            if not x and not y:
                try:
                    seat = display.get_default_seat()
                    _screen, x, y = seat.get_pointer().get_position()
                except Exception:
                    pass
            return self._monitor_at(x, y)

        def _on_delete(self, *_args):
            self.hide()
            return True

        def _on_realize(self, *_args):
            gdk_win = self.get_window()
            if gdk_win is None:
                return
            try:
                if self._backend_x11():
                    gdk_win.set_override_redirect(True)
                gdk_win.set_keep_above(True)
            except Exception:
                pass

        def _on_map(self, *_args):
            GLib.idle_add(self._apply_frame)
            GLib.timeout_add(80, self._apply_frame)
            GLib.timeout_add(400, self._apply_frame)
            return False

        def _on_size_allocate(self, _widget, allocation):
            if self._dragging or self._pinning:
                return
            if int(getattr(allocation, "width", 0) or 0) <= 1:
                return
            GLib.idle_add(self._pin_drawn_size)

        def _on_drag_press(self, _widget, event):
            if getattr(event, "button", 0) != 1:
                return False
            try:
                win_x, win_y = self._window_origin()
            except Exception:
                win_x, win_y = 0, 0
            self._press = (event.x_root, event.y_root, win_x, win_y, event.time)
            self._dragging = False
            return True

        def _on_drag_motion(self, _widget, event):
            if self._press is None:
                return False
            if not (event.state & Gdk.ModifierType.BUTTON1_MASK):
                return False
            dx = event.x_root - self._press[0]
            dy = event.y_root - self._press[1]
            if not self._dragging and abs(dx) + abs(dy) < DRAG_THRESHOLD:
                return False
            self._dragging = True
            if self._backend_x11():
                self._move_to(int(self._press[2] + dx), int(self._press[3] + dy))
            else:
                try:
                    self.begin_move_drag(
                        1, int(event.x_root), int(event.y_root), event.time,
                    )
                except Exception:
                    self._move_to(int(self._press[2] + dx), int(self._press[3] + dy))
                self._press = None
            return True

        def _on_drag_release(self, _widget, event, toggle):
            if getattr(event, "button", 0) != 1:
                return False
            dragged = self._dragging
            self._press = None
            self._dragging = False
            if dragged:
                try:
                    x, y = self._window_origin()
                    width, height = self._drawn_size()
                except Exception:
                    return True
                snapped = usage_widget_snap(x, y, width, height, self._monitor_at(x, y))
                self.position = snapped
                save_widget_position(self.position)
                self._apply_frame()
                return True
            if toggle:
                self.expanded = not self.expanded
                self._apply_frame()
            return True

        def _drawn_size(self):
            gdk_win = self.get_window()
            if gdk_win is not None:
                try:
                    width, height = gdk_win.get_width(), gdk_win.get_height()
                    if width > 1 and height > 1:
                        return int(width), int(height)
                except Exception:
                    pass
            return (
                int(self.get_allocated_width() or COLLAPSED_SIZE[0]),
                int(self.get_allocated_height() or COLLAPSED_SIZE[1]),
            )

        def _window_origin(self):
            gdk_win = self.get_window()
            if gdk_win is not None:
                try:
                    origin = gdk_win.get_origin()
                    if isinstance(origin, tuple) and len(origin) >= 2:
                        return int(origin[-2]), int(origin[-1])
                except Exception:
                    pass
            try:
                x, y = self.get_position()
                return int(x), int(y)
            except Exception:
                return (
                    int((self.position or {}).get("x") or 0),
                    int((self.position or {}).get("y") or 0),
                )

        def _move_to(self, x, y, width=None, height=None):
            drawn_w, drawn_h = self._drawn_size()
            width = int(width if width is not None else drawn_w)
            height = int(height if height is not None else drawn_h)
            self.move(x, y)
            gdk_win = self.get_window()
            if gdk_win is not None:
                try:
                    gdk_win.move(x, y)
                    gdk_win.move_resize(x, y, width, height)
                except Exception:
                    pass

        def _pin_drawn_size(self):
            if self._dragging or self._pinning:
                return False
            width, height = self._drawn_size()
            if width <= 1:
                return False
            x, y = self._window_origin()
            monitor = self._monitor_at(x, y)
            anchor = str((self.position or {}).get("anchor") or "right")
            if anchor not in ("left", "right"):
                anchor = "right"
            pinned_x = usage_widget_dock_x(anchor, width, monitor)
            if abs(int(x) - pinned_x) <= 1:
                return False
            self._pinning = True
            try:
                self._move_to(pinned_x, y, width, height)
                self.position = {
                    "x": pinned_x,
                    "y": int(y),
                    "width": width,
                    "height": height,
                    "anchor": anchor,
                }
                save_widget_position(self.position)
            finally:
                self._pinning = False
            return False

        def _dock_order(self, anchor):
            if anchor == "left":
                wanted = (self.panel, self.tab)
            else:
                wanted = (self.tab, self.panel)
            current = tuple(self.body.get_children())
            if current == wanted:
                return
            for child in current:
                self.body.remove(child)
            for child in wanted:
                expand = child is self.panel
                self.body.pack_start(child, expand, expand, 0)

        def _apply_frame(self):
            if self._dragging:
                return False
            monitor = self._monitor()
            frame = usage_widget_frame(self.expanded, self.position, monitor)
            self._dock_order(frame["anchor"])
            if self.expanded:
                self.panel.show()
            else:
                self.panel.hide()
            self.set_size_request(frame["width"], frame["height"])
            drawn_w, drawn_h = self._drawn_size()
            if self.expanded:
                width = max(frame["width"], drawn_w)
                height = max(frame["height"], drawn_h)
            else:
                width, height = frame["width"], frame["height"]
            self.resize(width, height)
            x = usage_widget_dock_x(frame["anchor"], width, monitor)
            y = frame["y"]
            self.position = {
                "x": x,
                "y": y,
                "width": width,
                "anchor": frame["anchor"],
            }
            self._move_to(x, y, width, height)
            save_widget_position(self.position)
            GLib.idle_add(self._pin_drawn_size)
            return False

        def _on_chip(self, button):
            if not button.get_active():
                return
            provider_id = getattr(button, "token_meter_provider", "")
            if provider_id == self.selected_id:
                return
            self.selected_id = provider_id
            self._render_body()

        def _clear(self, box):
            for child in list(box.get_children()):
                box.remove(child)

        def _rebuild_chips(self, chips, selected_id):
            current = [
                getattr(child, "token_meter_provider", None)
                for child in self.chip_box.get_children()
            ]
            expected = [""] + [chip["id"] for chip in chips]
            if current != expected:
                self._clear(self.chip_box)
                group = None
                for provider_id, label in [("", "All")] + [
                    (chip["id"], chip["label"]) for chip in chips
                ]:
                    button = Gtk.RadioButton.new_with_label_from_widget(group, label)
                    group = button
                    button.set_mode(False)
                    button.token_meter_provider = provider_id
                    button.get_style_context().add_class("usage-chip")
                    button.connect("toggled", self._on_chip)
                    self.chip_box.pack_start(button, False, False, 0)
                self.chip_box.show_all()
            for button in self.chip_box.get_children():
                want = getattr(button, "token_meter_provider", "") == selected_id
                if button.get_active() != want:
                    button.handler_block_by_func(self._on_chip)
                    button.set_active(want)
                    button.handler_unblock_by_func(self._on_chip)

        def _bar_row(self, window):
            used = window.get("used_percent")
            try:
                used = max(0.0, min(100.0, float(used or 0)))
            except (TypeError, ValueError):
                used = 0.0
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            name = Gtk.Label(label=str(window.get("label") or "Limit"))
            name.set_xalign(0)
            name.get_style_context().add_class("usage-fg")
            pct = Gtk.Label(label=percent_label(used))
            pct.set_xalign(1)
            pct.get_style_context().add_class("usage-fg")
            meta.pack_start(name, True, True, 0)
            meta.pack_end(pct, False, False, 0)
            bar = Gtk.ProgressBar()
            bar.set_fraction(used / 100.0)
            row.pack_start(meta, False, False, 0)
            row.pack_start(bar, False, False, 0)
            return row

        def _session_row(self, session):
            button = Gtk.Button()
            button.set_relief(Gtk.ReliefStyle.NONE)
            inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            name = Gtk.Label(label=str(session.get("name") or "Session"))
            name.set_xalign(0)
            name.get_style_context().add_class("usage-fg")
            mark = Gtk.Label(label=str(session.get("label") or session.get("provider") or ""))
            mark.set_xalign(1)
            mark.get_style_context().add_class("usage-dim")
            inner.pack_start(name, True, True, 0)
            inner.pack_end(mark, False, False, 0)
            button.add(inner)
            session_id = str(session.get("id") or "")
            button.connect("clicked", lambda *_args, sid=session_id: self._open_session(sid))
            return button

        def _open_session(self, session_id):
            if not session_id:
                return
            try:
                subprocess.Popen(
                    ["xdg-open", session_url(session_id)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except OSError:
                pass

        def _render_body(self):
            view = usage_widget_view(self.payload, self.selected_id)
            self.selected_id = view["selected_id"]
            self._rebuild_chips(view["chips"], self.selected_id)
            self.title_label.set_text(str(view["title"] or "Token Meter").upper())
            if view["live_tps"] is not None and not view["ended"]:
                self.live_label.set_text(
                    "LIVE " + compact_number(view["live_tps"] * 60) + " tok/min"
                )
            else:
                self.live_label.set_text("")
            self.tab_percent.set_text(percent_label(view["hottest"]))
            self._clear(self.bars)
            if self.error:
                err = Gtk.Label(label=self.error)
                err.set_xalign(0)
                err.set_line_wrap(True)
                err.get_style_context().add_class("usage-dim")
                self.bars.pack_start(err, False, False, 0)
            elif view["quota_available"]:
                for window in view["windows"]:
                    self.bars.pack_start(self._bar_row(window), False, False, 0)
            else:
                empty = Gtk.Label(label="No provider limits reported.")
                empty.set_xalign(0)
                empty.get_style_context().add_class("usage-dim")
                self.bars.pack_start(empty, False, False, 0)
            self.bars.show_all()
            self._clear(self.sessions)
            if view["sessions"]:
                for session in view["sessions"]:
                    self.sessions.pack_start(self._session_row(session), False, False, 0)
            else:
                empty = Gtk.Label(label="No recent sessions.")
                empty.set_xalign(0)
                empty.get_style_context().add_class("usage-dim")
                self.sessions.pack_start(empty, False, False, 0)
            self.sessions.show_all()
            if not self._dragging:
                GLib.idle_add(self._apply_frame)

        def refresh(self):
            try:
                self.payload = fetch_menubar()
                self.error = ""
            except (OSError, ValueError, URLError) as exc:
                self.payload = {}
                self.error = str(exc) or "Waiting for Token Meter"
            self._render_body()
            return True

    class UsageWidgetApplication(Gtk.Application):
        def __init__(self):
            super().__init__(
                application_id=APPLICATION_ID,
                flags=Gio.ApplicationFlags.FLAGS_NONE,
            )
            self.window = None

        def do_activate(self):
            if self.window is None:
                self.window = UsageWidgetWindow(self)
                self.add_window(self.window)
            self.window.show_all()
            self.window._apply_frame()
            self.window.present()
            GLib.idle_add(self.window._apply_frame)


def gtk_requirements_message():
    return (
        "Token Meter's usage widget needs GTK 3 and PyGObject.\n"
        "Debian/Ubuntu: sudo apt install python3-gi\n"
        "Fedora: sudo dnf install python3-gobject\n"
        "Arch: sudo pacman -S python-gobject"
    )


def run_smoke():
    fixture = os.environ.get("TOKEN_METER_WIDGET_FIXTURE")
    if fixture:
        payload = json.loads(fixture)
    else:
        payload = fetch_menubar()
    selected = os.environ.get("TOKEN_METER_WIDGET_PROVIDER") or ""
    print(json.dumps(usage_widget_view(payload, selected), sort_keys=True))


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    if "--check" in argv:
        return 0
    if "--smoke" in argv or os.environ.get("TOKEN_METER_WIDGET_SMOKE") == "1":
        try:
            run_smoke()
        except (OSError, ValueError, URLError, json.JSONDecodeError) as exc:
            print(f"Token Meter widget smoke failed: {exc}", file=sys.stderr)
            return 1
        return 0
    if not GTK_AVAILABLE:
        print(gtk_requirements_message(), file=sys.stderr)
        return 1
    configure_backend()
    GLib.set_prgname("token-meter-widget")
    try:
        Gdk.set_program_class("token-meter-widget")
    except Exception:
        pass
    app = UsageWidgetApplication()
    return app.run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
