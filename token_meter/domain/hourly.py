"""Bounded private hour counters beside normalized model/day evidence."""

import datetime
import math
import time


HOURLY_DAYS = 90
MAX_HOURS_PER_DAY = 25
HOURLY_SCHEMA = 2
COUNTERS = (
    "cost", "discount_usd", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
    "cache_covered_input_tokens", "reasoning_tokens", "reasoning_output_tokens", "reasoning_executions",
    "reasoning_unavailable_executions", "thinking_executions", "thinking_covered_executions",
    "token_covered_executions", "io_covered_executions", "io_covered_input_tokens", "io_covered_output_tokens",
    "cost_covered_executions", "cost_covered_output_tokens", "cost_covered_cost", "executions",
)


def counter_snapshot(daily):
    """The counters an execution may change, read before it is added."""
    return {field: daily[field] for field in COUNTERS if field in daily}


def record_hour(daily, timestamp, before, localtime=time.localtime, today=None):
    """Record only the normalized counter delta, never execution text or arguments.

    Each hour is a compact JSON-safe list aligned with ``COUNTERS``; ``None`` marks a
    counter the day did not carry yet, so optional coverage fields stay distinguishable from 0.
    """
    today = today or datetime.date.today()
    try:
        day = datetime.date.fromisoformat(daily["day"])
        stamp = float(timestamp)
        if not math.isfinite(stamp) or not today - datetime.timedelta(days=HOURLY_DAYS - 1) <= day <= today:
            return
        local = localtime(stamp)
        start = math.floor(stamp) - local.tm_min * 60 - local.tm_sec
    except (KeyError, ValueError, TypeError, OverflowError, OSError):
        return
    hours = daily.setdefault("_hours", {})
    key = str(start)
    if key not in hours and len(hours) >= MAX_HOURS_PER_DAY:
        daily["_hourly_truncated"] = True
        return
    row = hours.get(key)
    if row is None:
        row = hours[key] = [None] * len(COUNTERS)
    for index, field in enumerate(COUNTERS):
        if field in daily:
            row[index] = (row[index] or 0) + daily[field] - before.get(field, 0)


def hour_records(daily):
    """Expand one model/day's compact hours into model-stat shaped rows."""
    model, day = daily.get("model"), daily.get("day")
    records = []
    for start, values in (daily.get("_hours") or {}).items():
        record = {"model": model, "day": day, "at": int(start)}
        record.update((field, value) for field, value in zip(COUNTERS, values) if value is not None)
        records.append(record)
    return records
