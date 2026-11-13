"""One-day goal charts from real, private model/hour counters."""

import datetime
import json
import math
import time

from token_meter.domain.hourly import COUNTERS, HOURLY_DAYS


def hourly_series(metric, scope, day, rows, metric_available, collect, finish, in_scope, row_models,
                  cache=None, now=None):
    now = time.time() if now is None else now
    date = datetime.date.fromisoformat(day)
    first = datetime.datetime.combine(date, datetime.time()).timestamp()
    last = datetime.datetime.combine(date + datetime.timedelta(days=1), datetime.time()).timestamp()
    through = min(last, int(now))
    if through <= first:
        return []
    ends = list(range(int(first) + 3600, int(through) + 1, 3600))
    if not ends or ends[-1] != through:
        ends.append(through)
    reason = ""
    prepared = []
    if date < datetime.date.fromtimestamp(now) - datetime.timedelta(days=HOURLY_DAYS - 1):
        reason = f"Hourly history covers the most recent {HOURLY_DAYS} days."
    rows = list(rows)
    for row in rows:
        if not in_scope(row, scope):
            continue
        selected = row_models(row, scope)
        days = [d for d in row.get("_model_daily", []) if d.get("day") == day
                and (selected is None or d.get("model") in selected)]
        if not days:
            continue
        hours = []
        for daily in days:
            records = list((daily.get("_hours") or {}).values())
            if daily.get("_hourly_truncated") or any(
                    not math.isclose(sum(float(h.get(field, 0)) for h in records), float(daily[field]),
                                     rel_tol=1e-9, abs_tol=1e-8)
                    for field in COUNTERS if field in daily):
                reason = reason or "Some usage has no complete hourly evidence."
            hours.extend(records)
        prepared.append((row, hours))
    _days, flags = collect(scope, day, day, rows, metric_available)
    if flags["undated_exec"] or (metric in ("output_per_dollar", "frontier_share") and flags["undated_cost"]):
        reason = "Some usage has no timestamp; hourly totals are unavailable."
    if not prepared and not reason:
        reason = "No hourly observations in this period."
    result = []
    cache = {} if cache is None else cache
    bucket_start = first
    for end in ends:
        value = None
        sample_reason = reason
        if not reason:
            key = ("hourly", json.dumps(scope, sort_keys=True), day, end)
            if key not in cache:
                prefix = []
                for original, hours in prepared:
                    known = [h for h in hours if first <= h["at"] < end]
                    cost = sum(h.get("cost", 0) for h in known)
                    prefix.append({**original, "cost": cost, "_day_cost": {day: cost},
                                   "model_stats": known, "_model_daily": known})
                buckets, _prefix_flags = collect(scope, day, day, prefix, metric_available)
                cache[key] = buckets.get(day, {})
            totals = cache[key]
            if totals:
                measurement = finish(metric, totals, flags)
                value = measurement["value"] if measurement["complete"] else None
                sample_reason = measurement["reason"] if not measurement["complete"] else ""
        # Label a closed bucket by its start, including the UTC offset for repeated DST hours.
        label = datetime.datetime.fromtimestamp(bucket_start).astimezone().isoformat(timespec="minutes")
        result.append({"day": day, "at": end, "label": label, "value": value,
                       "through": datetime.datetime.fromtimestamp(end).astimezone().isoformat(timespec="minutes"),
                       "partial": end - bucket_start < 3600,
                       **({"reason": sample_reason} if sample_reason else {})})
        bucket_start = end
    return result
