"""Runtime-neutral quota normalization and bounded HTTP handling."""

import datetime
import json
import math
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPHandler, HTTPSHandler, HTTPRedirectHandler, Request, build_opener

from .base import QuotaUnavailable


DEFAULT_HTTP_TIMEOUT_S = 8.0
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def _compact_text(value, limit=180):
    value = " ".join(str(value or "").split())
    return value[:limit - 1] + "…" if len(value) > limit else value


def quota_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def quota_timestamp(value):
    number = quota_number(value)
    if number is not None:
        if number > 10_000_000_000:
            number /= 1000.0
        return number if number > 0 else None
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.timezone.utc)
        return parsed.timestamp()
    except (ValueError, OverflowError):
        return None


def quota_compact_duration(seconds):
    seconds = max(0, int(seconds or 0))
    if seconds < 60:
        return "{}s".format(seconds)
    minutes = seconds // 60
    if minutes < 60:
        return "{}m".format(minutes)
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return "{}h{}".format(hours, " {}m".format(minutes) if minutes else "")
    days, hours = divmod(hours, 24)
    return "{}d{}".format(days, " {}h".format(hours) if hours else "")


def quota_pace(window, now=None):
    """Compare provider-reported use with even consumption across a real window."""

    now = float(now if now is not None else time.time())
    used = quota_number((window or {}).get("used_percent"))
    duration = quota_number((window or {}).get("window_seconds"))
    reset_at = quota_timestamp((window or {}).get("reset_at"))
    if used is None or duration is None or duration <= 0 or reset_at is None:
        return None
    remaining_time = reset_at - now
    if remaining_time <= 0 or remaining_time > duration:
        return None
    elapsed = duration - remaining_time
    if elapsed <= 0 or elapsed / duration < 0.03:
        return None

    actual = max(0.0, min(100.0, used))
    expected = max(0.0, min(100.0, elapsed / duration * 100.0))
    delta = actual - expected
    if delta > 4:
        state = "deficit"
        lead = "{:.0f}% in deficit".format(abs(delta))
    elif delta < -4:
        state = "reserve"
        lead = "{:.0f}% in reserve".format(abs(delta))
    else:
        state = "on_pace"
        lead = "on pace"

    runs_out_at = None
    will_last = False
    if actual >= 100:
        summary = "exhausted"
        runs_out_at = now
    elif actual <= 0:
        will_last = True
        summary = "{} · lasts until reset".format(lead)
    else:
        rate = actual / elapsed
        run_out_in = (100.0 - actual) / rate if rate > 0 else None
        if run_out_in is None or run_out_in >= remaining_time:
            will_last = True
            summary = "{} · lasts until reset".format(lead)
        else:
            runs_out_at = now + run_out_in
            summary = "{} · runs out in {}".format(
                lead, quota_compact_duration(run_out_in)
            )
    return {
        "state": state,
        "expected_used_percent": round(expected, 1),
        "delta_percent": round(delta, 1),
        "will_last_to_reset": will_last,
        "runs_out_at": round(runs_out_at, 3) if runs_out_at is not None else None,
        "summary": summary,
    }


def quota_window(provider, window_id, kind, label, used_percent,
                 window_seconds=None, reset_at=None, now=None):
    used = quota_number(used_percent)
    if used is None:
        return None
    duration = quota_number(window_seconds)
    reset = quota_timestamp(reset_at)
    row = {
        "id": str(window_id or "{}-{}".format(provider, kind)),
        "kind": str(kind or "extra"),
        "label": str(label or kind or "Quota"),
        "used_percent": round(max(0.0, min(100.0, used)), 2),
        "window_seconds": int(duration) if duration is not None and duration > 0 else None,
        "reset_at": round(reset, 3) if reset is not None else None,
    }
    row["pace"] = quota_pace(row, now=now)
    return row


def quota_provider(provider, label, status, source, windows=None, plan="", error="",
                   coverage_note=""):
    return {
        "id": provider,
        "label": label,
        "status": status,
        "plan": str(plan or ""),
        "source": source,
        "provenance": "provider_reported" if windows else "unavailable",
        "windows": [row for row in (windows or []) if row],
        "error": _compact_text(error, 180),
        "coverage_note": _compact_text(coverage_note, 180),
    }


def quota_coverage_note(provider, missing, qualifier=""):
    missing = [str(value).strip() for value in (missing or []) if str(value).strip()]
    if not missing:
        return ""
    names = missing[0] if len(missing) == 1 else " and ".join(missing)
    subject = " ".join(value for value in (str(qualifier or "").strip(), names) if value)
    subject = subject[:1].upper() + subject[1:]
    noun = "limit" if len(missing) == 1 else "limits"
    verb = "was" if len(missing) == 1 else "were"
    return "{} {} {} not reported by {}; missing does not mean 0%.".format(
        subject, noun, verb, provider
    )


def quota_slug(value):
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return slug[:64] or "quota"


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise QuotaUnavailable("Provider quota request redirected.")


def _quota_opener():
    return build_opener(_NoRedirectHandler, HTTPHandler, HTTPSHandler)


def quota_http_json(url, headers=None, timeout=DEFAULT_HTTP_TIMEOUT_S, opener=None):
    request = Request(url, headers=headers or {}, method="GET")
    try:
        response = (opener or _quota_opener().open)(request, timeout=timeout)
        with response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise QuotaUnavailable("Provider quota response was too large.")
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise QuotaUnavailable("Provider returned an invalid quota response.")
        return value
    except QuotaUnavailable:
        raise
    except HTTPError as exc:
        code = exc.code
        exc.close()
        if code in (401, 403):
            raise QuotaUnavailable("Provider authentication needs to be refreshed.") from None
        if code == 429:
            raise QuotaUnavailable(
                "Provider quota service is rate limited; Token Meter will retry."
            ) from None
        raise QuotaUnavailable(
            "Provider quota request failed (HTTP {}).".format(code)
        ) from None
    except (URLError, TimeoutError):
        raise QuotaUnavailable("Provider quota request could not connect.") from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise QuotaUnavailable("Provider returned an invalid quota response.") from None
