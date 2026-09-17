"""Anthropic account quota parsing and bounded acquisition."""

import json
import time

from .base import QuotaUnavailable
from .common import quota_coverage_note, quota_provider, quota_timestamp, quota_window


def _window(field, raw, kind, label, duration, now=None):
    if not isinstance(raw, dict):
        return None
    return quota_window(
        "claude", "claude-{}".format(field.replace("_", "-")), kind, label,
        raw.get("utilization"), window_seconds=duration,
        reset_at=raw.get("resets_at"), now=now,
    )


def parse_quota(payload, credentials=None, now=None):
    windows, seen = [], set()

    def add(row):
        if row and row["id"] not in seen:
            seen.add(row["id"])
            windows.append(row)

    main_session = _window(
        "five_hour", payload.get("five_hour"), "session", "Session", 5 * 60 * 60,
        now=now,
    )
    main_weekly = _window(
        "seven_day", payload.get("seven_day"), "weekly", "Weekly", 7 * 24 * 60 * 60,
        now=now,
    )
    add(main_session)
    add(main_weekly)
    missing_main = [
        label for label, window in (("Session", main_session), ("Weekly", main_weekly))
        if not window
    ]
    coverage_note = quota_coverage_note("Claude", missing_main)
    named = (
        ("seven_day_opus", "Opus weekly"),
        ("seven_day_sonnet", "Sonnet weekly"),
        ("seven_day_routines", "Routines weekly"),
        ("seven_day_cowork", "Routines weekly"),
    )
    for field, label in named:
        add(_window(
            field, payload.get(field), "weekly", label, 7 * 24 * 60 * 60, now=now
        ))
    for index, limit in enumerate(payload.get("limits") or []):
        if not isinstance(limit, dict) or limit.get("kind") != "weekly_scoped":
            continue
        scope = limit.get("scope") if isinstance(limit.get("scope"), dict) else {}
        model = scope.get("model") if isinstance(scope.get("model"), dict) else {}
        name = model.get("display_name") or model.get("id") or "Scoped {}".format(index + 1)
        add(quota_window(
            "claude", "claude-weekly-{}".format(_slug(name)), "weekly",
            "{} weekly".format(name), limit.get("percent"),
            window_seconds=7 * 24 * 60 * 60,
            reset_at=limit.get("resets_at"), now=now,
        ))
    metadata = credentials or {}
    plan = metadata.get("subscriptionType") or metadata.get("rateLimitTier") or ""
    if not windows:
        return quota_provider(
            "claude", "Claude", "unavailable", "Claude OAuth API", plan=plan,
            error="This Claude account does not report quota windows.",
            coverage_note=coverage_note,
        )
    return quota_provider(
        "claude", "Claude", "ok", "Claude OAuth API", windows=windows, plan=plan,
        coverage_note=coverage_note,
    )


def _slug(value):
    from .common import quota_slug
    return quota_slug(value)


def auth_status(provider_cli_path, agent_environment, subprocess_module, timeout=3.0):
    executable = provider_cli_path("claude")
    if not executable:
        return {}
    try:
        result = subprocess_module.run(
            [executable, "auth", "status", "--json"], capture_output=True,
            text=True, timeout=timeout, check=False,
            env=agent_environment(executable),
            creationflags=getattr(subprocess_module, "CREATE_NO_WINDOW", 0),
        )
        value = json.loads(result.stdout or "{}")
        return value if isinstance(value, dict) else {}
    except (subprocess_module.TimeoutExpired, OSError, json.JSONDecodeError):
        return {}


def oauth_credentials(credentials_path, auth_status_value, subprocess_module,
                      timeout=3.0, now_fn=time.time):
    data = None
    try:
        with open(credentials_path, "rb") as stream:
            data = stream.read(1024 * 1024 + 1)
    except (FileNotFoundError, OSError):
        pass
    if data is None:
        status = auth_status_value or {}
        if (
            not status.get("loggedIn")
            or str(status.get("authMethod") or "").lower() == "third_party"
        ):
            raise QuotaUnavailable("Claude OAuth credentials are not available.")
        try:
            result = subprocess_module.run(
                ["/usr/bin/security", "find-generic-password", "-s",
                 "Claude Code-credentials", "-w"],
                capture_output=True, timeout=timeout, check=False,
                creationflags=getattr(subprocess_module, "CREATE_NO_WINDOW", 0),
            )
        except (subprocess_module.TimeoutExpired, OSError):
            raise QuotaUnavailable(
                "Claude OAuth credentials are not available."
            ) from None
        if result.returncode != 0:
            raise QuotaUnavailable("Claude OAuth credentials are not available.")
        data = result.stdout
    if len(data) > 1024 * 1024:
        raise QuotaUnavailable("Claude OAuth credentials are invalid.")
    try:
        root = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise QuotaUnavailable("Claude OAuth credentials are invalid.") from None
    oauth = root.get("claudeAiOauth") if isinstance(root, dict) else None
    if not isinstance(oauth, dict) or not oauth.get("accessToken"):
        raise QuotaUnavailable("Claude OAuth credentials are not available.")
    expires_at = quota_timestamp(oauth.get("expiresAt"))
    if expires_at is not None and expires_at <= now_fn() + 60:
        raise QuotaUnavailable("Claude OAuth credentials need to be refreshed.")
    scopes = oauth.get("scopes") or []
    if scopes and "user:profile" not in scopes:
        raise QuotaUnavailable("Claude OAuth credentials do not include quota access.")
    return oauth


def load_quota(auth_status_loader, credentials_loader, http_json, now=None, opener=None):
    status = auth_status_loader()
    if str(status.get("authMethod") or "").lower() == "third_party":
        provider = str(status.get("apiProvider") or "third-party")
        return quota_provider(
            "claude", "Claude", "unavailable", "Claude third-party authentication",
            plan=provider.title(),
            error="Claude account quotas are not exposed for this provider.",
            coverage_note=quota_coverage_note("Claude", ["Session", "Weekly"]),
        )
    credentials = credentials_loader(auth_status=status)
    payload = http_json(
        "https://api.anthropic.com/api/oauth/usage",
        headers={
            "Authorization": "Bearer {}".format(credentials["accessToken"]),
            "Accept": "application/json",
            "Content-Type": "application/json",
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": "TokenMeter/0.1",
        },
        opener=opener,
    )
    return parse_quota(payload, credentials=credentials, now=now)
