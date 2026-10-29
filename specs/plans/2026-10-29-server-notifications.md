# Server-owned notifications

Status: approved design, 2026-10-29. Branch `feat/server-notifications`.

## Goal

Move every notification decision out of the native companions and into the
server. The macOS menu bar (`menubar/TokenMeterMenuBar.swift`) and the
Linux tray (`menubar/token_meter_tray.py`) currently each keep their own
threshold rules and "already fired" state. After this change they only claim and
show alerts the server publishes. Any other local consumer (for example a
Ghost tray) can use the same versioned API without reimplementing the rules.

## Decisions

- **Delivery ownership: first claim wins.** The server publishes each alert with
  a stable ID. A client must claim an alert before showing it; only the first
  claim succeeds. Delivery is at most once: a client that crashes after claiming
  does not cause a resend.
- **Rules move unchanged.** The server reproduces the current client behavior
  exactly (below). No new alert kinds, thresholds, or wording.
- **Preferences move to the server.** Quota alerts on/off and the quota
  threshold become a validated `notifications` settings block. Budget
  thresholds and the budget notification switch stay in budget settings.
- **Fired state is not migrated.** The first-sighting rule seeds state without
  alerting, so upgrading does not replay thresholds already crossed.
- **Evaluation is on demand.** Rules run when `/menubar` or `/notifications` is
  served, at most once every 2 seconds. No new background thread; idle CPU is
  unchanged.

## Rules (carried over)

Quota, per fresh provider window `(provider, window)`:

- Thresholds are the user threshold (80, 90, or 95) plus 95 and 100.
- First sighting records `last_used_percent`, `reset_at`, and no fired
  thresholds, without alerting.
- Alerts are suppressed until the server process has seen one fresh quota pass
  (in-memory, like the Swift `quotaObservationEstablished`).
- A reset is `new reset_at > old reset_at + 60`. On reset, fired thresholds
  clear; if alerts are on and usage went from at/above the user threshold to
  below it, publish "`<Provider>` quota reset" / "`<Window>` is back to N% used."
- Otherwise, thresholds with `last < t <= used` that have not fired are
  crossed. Only the highest crossing alerts; all crossings are recorded.
  Severity: 100 exhausted, 95 critical, else warning. Title
  "`<Provider>` quota `<severity>`", body
  "`<Window>` reached N% used; resets in 2h 5m." (or "reset pending" /
  "reset time unavailable").
- When alerts are off, state still advances but nothing alerts.

Budget, when the monthly budget is configured:

- "Overall monthly budget exceeded" fires once per month when overall percent
  is at least 100 and budget notifications are on. Body
  "[At least ]$S of $B recorded for YYYY-MM (N% used)."
- Each scope (`overall` plus each runtime allocation) records first sighting
  per month with already-met thresholds marked fired, without alerting.
- With notifications on, thresholds with `last < t <= percent` not yet fired
  are crossed; the highest alerts, unless the scope is `overall` and the
  exceeded alert already fired this month. Title
  "`<Scope>` monthly budget reached T%". Overall body
  "[At least ]$S of $B recorded for YYYY-MM."; runtime body
  "`<Scope>` reached N% of its allocation."
- Money format: `$%.2f` at or above 1, otherwise `$%.3f`.

Live hints (server already filters by the "Notify me about live suggestions"
setting):

- Each hint ID alerts once, title "Token Meter: `<title>`". Remember the last
  200 IDs.
- The first evaluation, and the first evaluation after hints go from disabled
  to enabled, records current IDs without alerting.
- The Linux tray did not show live hints before; it now does when the setting
  is on. The Windows notification-area client shows no notifications before or
  after this change.

## State

`~/.token-meter/notifications.json` (override `TOKEN_METER_NOTIFICATIONS`),
written with `atomic_write_text` under the existing file lock helper. Runtime
state, not settings.

```json
{"version": 1,
 "quota": {"codex:primary": {"last_used_percent": 82.0, "reset_at": 1761700000, "fired": [80]}},
 "budget": {"overall": {"month": "2026-10", "last_percent": 91.0, "fired": [80, 90]}},
 "budget_exceeded_months": ["2026-09"],
 "hints": {"seen": ["…"], "seeded": true, "enabled": true},
 "events": []}
```

Bounds: 50 events, 24-hour event lifetime, 24 exceeded months, 200 hint IDs.
A corrupt or unreadable file starts from empty state (first sighting again).

## Event

```json
{"id": "budget:overall:2026-10:90", "kind": "budget", "severity": "warning",
 "title": "Overall monthly budget reached 90%",
 "body": "$412.10 of $450.00 recorded for 2026-10.",
 "link": "#spend-budgets", "created_at": 1761700000,
 "claimed": false, "claimed_by": null}
```

IDs: `quota:<provider>:<window>:<reset_at|none>:<threshold>`,
`quota-reset:<provider>:<window>:<reset_at>`, `budget:<scope>:<month>:<threshold>`,
`budget-exceeded:<month>`, `hint:<id>`. Kinds: `quota`, `quota_reset`,
`budget`, `budget_exceeded`, `live_hint`. Links: quota `#sessions`, budget
`#spend-budgets`, hint `#sessions`. Events carry only labels, amounts,
percentages, months, and links; hint text is passed through as already exposed
in `/menubar`.

## API

`GET /notifications` (also embedded as `notifications` in `/menubar`):

```json
{"schema_version": 1,
 "events": [ newest first ],
 "prefs": {"source": "default|saved", "quota_alerts": true, "quota_threshold": 80,
           "budget_alerts": true, "budget_thresholds": [80, 90, 100],
           "live_hints": false},
 "actions": {"token": "…", "claim": "/notifications/claim",
             "settings": "/settings/notifications"}}
```

`POST /notifications/claim`, body `{"id": str, "consumer": str}`:

- Existing POST guard: local origin, JSON content type, action token, size.
- `consumer` matches `[a-z0-9][a-z0-9._-]{0,47}`; otherwise 400.
- Under the lock: unknown, already claimed, or older than 15 minutes returns
  `{"ok": true, "claimed": false, "reason": "unknown|claimed|expired"}`.
  Otherwise mark claimed, save, then return `{"ok": true, "claimed": true}`.

`POST /settings/notifications`, body
`{"quota_alerts": bool, "quota_threshold": 80|90|95, "migrate": bool?}`:

- Validated and idempotent; writes `settings["notifications"]`.
- `migrate: true` applies only when no `notifications` block is saved yet, so
  the first client to migrate local preferences wins.

`/menubar` drops the separate `live_hints` and `live_hints_enabled` fields; the
staged installer ships server and clients together.

## Clients

Swift and Linux Python tray:

- Remove evaluation code, state structs, and persisted state keys
  (`TokenMeterQuotaNotificationStates`, `TokenMeterBudgetNotificationStates`,
  `TokenMeterBudgetExceededNotificationMonths`,
  `TokenMeterLiveHintNotificationIDs`; tray `quota_notification_states`,
  `budget_notification_states`, `budget_exceeded_notification_months`,
  `quota_observation_established`).
- After each `/menubar` poll, take unclaimed events oldest first (at most 5),
  POST a claim, and show only when `claimed` is true. Consumers:
  `token-meter-menubar`, `token-meter-tray`.
- Quota toggle and threshold menus read `prefs` and write
  `/settings/notifications`.
- Migration: when `prefs.source == "default"` and local quota preferences
  exist, POST them once with `migrate: true`, then delete the local keys and
  old state keys.
- A payload without `notifications` shows nothing (no local fallback).

## Validation

- Notification state is read and written only by the running server
  (`_notification_persist`, set in `main()`), so imports and tests never touch
  the real `~/.token-meter`.
- Unit tests for every rule above, bounds, ID stability, corrupt state, and
  the 2-second throttle.
- Endpoint tests: claim first-wins, saved before response, concurrent claims,
  unknown/expired/claimed reasons, invalid consumer, missing token and foreign
  origin; settings validation, idempotence, and `migrate`.
- Contract: `schema_version`, no local paths in payloads.
- Source tests: no evaluation logic or state keys remain in either client.
- Full suite, `py_compile`, shell syntax, Swift compile and smoke, install,
  live `/health`, `/menubar`, `/notifications`, a live claim, a real macOS
  notification, and staged-runtime parity.
- Not verifiable here: Linux `notify-send` delivery on a Linux host; covered by
  unit tests only.

## Docs

`specs/ARCHITECTURE.md`: notification ownership and the API.
`README.md`: notifications are decided by the server; companions show them.
