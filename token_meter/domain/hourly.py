"""Bounded private hour counters beside normalized model/day evidence."""

import datetime
import math
import time


HOURLY_DAYS = 90
MAX_HOURS_PER_DAY = 25
HOURLY_SCHEMA = 1
COUNTERS = (
    "cost", "discount_usd", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
    "cache_covered_input_tokens", "reasoning_tokens", "reasoning_output_tokens", "reasoning_executions",
    "reasoning_unavailable_executions", "thinking_executions", "thinking_covered_executions",
    "token_covered_executions", "io_covered_executions", "io_covered_input_tokens", "io_covered_output_tokens",
    "cost_covered_executions", "cost_covered_output_tokens", "cost_covered_cost", "executions",
)


def record_hour(daily, timestamp, before, localtime=time.localtime, today=None):
    """Record only the normalized counter delta, never execution text or arguments."""
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
    row = hours.setdefault(key, {"model": daily["model"], "day": daily["day"], "at": start})
    for field in COUNTERS:
        if field in daily:
            delta = daily[field] - before.get(field, 0)
            row[field] = row.get(field, 0) + delta
