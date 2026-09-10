"""xAI / Grok Build account quota parsing and bounded acquisition."""

import json
import time

from .base import QuotaUnavailable
from .common import (
    quota_coverage_note,
    quota_number,
    quota_provider,
    quota_slug,
    quota_timestamp,
    quota_window,
)


BILLING_URL = "https://cli-chat-proxy.grok.com/v1/billing?format=credits"
PERIOD_KINDS = {
    "USAGE_PERIOD_TYPE_WEEKLY": ("weekly", "Weekly"),
    "USAGE_PERIOD_TYPE_MONTHLY": ("monthly", "Monthly"),
}


def _money_val(value):
    if isinstance(value, dict):
        return quota_number(value.get("val") if "val" in value else value.get("value"))
    return quota_number(value)


def oauth_token(auth_path, now=None):
    try:
        with open(auth_path, encoding="utf-8") as stream:
            data = json.load(stream)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        raise QuotaUnavailable("Grok is not signed in locally.") from None
    if not isinstance(data, dict):
        raise QuotaUnavailable("Grok is not signed in locally.")
    current = float(now if now is not None else time.time())
    expired = False
    for value in data.values():
        if not isinstance(value, dict):
            continue
        token = str(value.get("key") or "").strip()
        if not token:
            continue
        expires_at = quota_timestamp(value.get("expires_at"))
        if expires_at is not None and expires_at <= current + 60:
            expired = True
            continue
        return token
    if expired:
        raise QuotaUnavailable("Grok authentication needs to be refreshed.")
    raise QuotaUnavailable("Grok is not signed in locally.")


def parse_quota(payload, now=None):
    root = payload.get("config") if isinstance(payload.get("config"), dict) else payload
    if not isinstance(root, dict):
        root = {}
    period = root.get("currentPeriod") if isinstance(root.get("currentPeriod"), dict) else {}
    period_type = str(period.get("type") or "")
    kind, label = PERIOD_KINDS.get(period_type, ("extra", "Usage"))
    start = quota_timestamp(period.get("start") or root.get("billingPeriodStart"))
    end = quota_timestamp(period.get("end") or root.get("billingPeriodEnd"))
    duration = end - start if start is not None and end is not None and end > start else None
    if duration is None and kind == "weekly":
        duration = 7 * 24 * 60 * 60
    if duration is None and kind == "monthly":
        duration = 30 * 24 * 60 * 60
    windows, seen = [], set()

    def add(row):
        if row and row["id"] not in seen:
            seen.add(row["id"])
            windows.append(row)

    percent = quota_number(root.get("creditUsagePercent"))
    if percent is not None:
        add(quota_window(
            "grok", "grok-{}".format(kind), kind, label, percent,
            window_seconds=duration, reset_at=end, now=now,
        ))
    products = [
        row for row in (root.get("productUsage") or []) if isinstance(row, dict)
    ]
    for index, product in enumerate(products):
        used = quota_number(product.get("usagePercent"))
        name = str(product.get("product") or "").strip() or "Product {}".format(index + 1)
        if used is None:
            continue
        if percent is not None and abs(used - percent) < 0.05:
            continue
        add(quota_window(
            "grok", "grok-product-{}".format(quota_slug(name)), "extra", name, used,
            window_seconds=duration, reset_at=end, now=now,
        ))
    cap = _money_val(root.get("onDemandCap"))
    used_on_demand = _money_val(root.get("onDemandUsed"))
    if cap is not None and cap > 0 and used_on_demand is not None:
        add(quota_window(
            "grok", "grok-on-demand", "extra", "On-demand",
            used_on_demand / cap * 100.0,
            window_seconds=duration, reset_at=end, now=now,
        ))
    missing = []
    if not any(row.get("kind") == "session" for row in windows):
        missing.append("Session")
    if not any(row.get("kind") == "weekly" for row in windows):
        missing.append("Weekly")
    coverage_note = quota_coverage_note("Grok", missing)
    if not windows:
        return quota_provider(
            "grok", "Grok", "unavailable", "Grok CLI billing",
            error="This Grok account does not report quota windows.",
            coverage_note=coverage_note,
        )
    return quota_provider(
        "grok", "Grok", "ok", "Grok CLI billing", windows=windows,
        plan="Grok Build", coverage_note=coverage_note,
    )


def load_quota(token_loader, http_json, now=None, opener=None):
    token = token_loader(now=now)
    payload = http_json(
        BILLING_URL,
        headers={
            "Authorization": "Bearer {}".format(token),
            "Accept": "application/json",
            "User-Agent": "TokenMeter/0.1",
        },
        opener=opener,
    )
    return parse_quota(payload, now=now)
