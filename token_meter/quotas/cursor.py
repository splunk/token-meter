"""Cursor account quota parsing and bounded acquisition."""

import base64
import contextlib
import json
import re
import time

from .base import QuotaUnavailable
from .common import (
    quota_coverage_note,
    quota_number,
    quota_provider,
    quota_timestamp,
    quota_window,
)


def auth_session(connection_factory, database_errors, now=None):
    try:
        with contextlib.closing(connection_factory()) as connection:
            row = connection.execute(
                "SELECT value FROM ItemTable WHERE key = ? LIMIT 1",
                ("cursorAuth/accessToken",),
            ).fetchone()
    except database_errors:
        raise QuotaUnavailable("Cursor is not signed in locally.") from None
    if not row:
        raise QuotaUnavailable("Cursor is not signed in locally.")
    access_token = row[0]
    if isinstance(access_token, memoryview):
        access_token = access_token.tobytes()
    if isinstance(access_token, bytes):
        access_token = access_token.decode("utf-8", "replace")
    if not isinstance(access_token, str) or not access_token.strip():
        raise QuotaUnavailable("Cursor is not signed in locally.")
    try:
        parts = access_token.split(".")
        encoded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded.encode("ascii")))
        user_id = str(payload.get("sub") or "").split("|")[-1]
        expires_at = quota_timestamp(payload.get("exp"))
    except (IndexError, ValueError, UnicodeError, json.JSONDecodeError):
        raise QuotaUnavailable("Cursor local authentication is invalid.") from None
    if not user_id or not re.fullmatch(r"[A-Za-z0-9._-]+", user_id):
        raise QuotaUnavailable("Cursor local authentication is invalid.")
    current = float(now if now is not None else time.time())
    if expires_at is None or expires_at <= current + 60:
        raise QuotaUnavailable("Cursor authentication needs to be refreshed.")
    return access_token, user_id


def parse_quota(payload, now=None):
    individual = (
        payload.get("individualUsage")
        if isinstance(payload.get("individualUsage"), dict) else {}
    )
    team = payload.get("teamUsage") if isinstance(payload.get("teamUsage"), dict) else {}
    candidates = (
        (individual.get("plan"), "Plan"),
        (individual.get("overall"), "Plan"),
        (team.get("pooled"), "Team plan"),
    )
    selected, label = None, "Plan"
    for value, candidate_label in candidates:
        if not isinstance(value, dict):
            continue
        used, limit = quota_number(value.get("used")), quota_number(value.get("limit"))
        if used is not None and limit is not None and limit > 0:
            selected, label = (used, limit), candidate_label
            break
    plan = str(payload.get("membershipType") or payload.get("limitType") or "")
    coverage_note = quota_coverage_note("Cursor", ["Session", "Weekly"])
    if not selected:
        reason = (
            "Cursor reports an unlimited plan without a usage cap."
            if payload.get("isUnlimited")
            else "This Cursor account does not report a capped plan window."
        )
        return quota_provider(
            "cursor", "Cursor", "unavailable", "Cursor account API", plan=plan,
            error=reason, coverage_note=coverage_note,
        )
    start = quota_timestamp(payload.get("billingCycleStart"))
    end = quota_timestamp(payload.get("billingCycleEnd"))
    duration = end - start if start is not None and end is not None and end > start else None
    used, limit = selected
    window = quota_window(
        "cursor", "cursor-plan", "monthly", label, used / limit * 100.0,
        window_seconds=duration, reset_at=end, now=now,
    )
    return quota_provider(
        "cursor", "Cursor", "ok", "Cursor account API", windows=[window], plan=plan,
        coverage_note=coverage_note,
    )


def load_quota(auth_session_loader, http_json, now=None, opener=None):
    access_token, user_id = auth_session_loader(now=now)
    payload = http_json(
        "https://cursor.com/api/usage-summary",
        headers={
            "Accept": "application/json",
            "Cookie": "WorkosCursorSessionToken={}%3A%3A{}".format(
                user_id, access_token
            ),
            "User-Agent": "TokenMeter/0.1",
        },
        opener=opener,
    )
    return parse_quota(payload, now=now)
