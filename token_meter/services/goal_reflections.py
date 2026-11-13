"""Bounded personal check-ins; no free text or frozen measurement snapshots."""

import datetime
import hashlib
import json
import re


MAX_REFLECTIONS = 6
CHANGES = ("model", "reasoning", "context", "none")
OUTCOMES = ("useful", "not_useful", "unsure")
NEXT_STEPS = ("keep", "adjust", "end")


def definition_key(goal):
    fields = ("metric", "scope", "comparison", "target", "target_high", "period", "start", "end")
    body = json.dumps({key: goal.get(key) for key in fields}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def _choices(value):
    if not isinstance(value, dict):
        raise ValueError("Choose the reflection answers.")
    changes = value.get("changes")
    if not isinstance(changes, list) or not 1 <= len(changes) <= len(CHANGES) \
            or any(not isinstance(choice, str) or choice not in CHANGES for choice in changes) \
            or ("none" in changes and len(changes) != 1):
        raise ValueError("Choose what changed, or No deliberate change.")
    if value.get("outcome") not in OUTCOMES or value.get("next_step") not in NEXT_STEPS:
        raise ValueError("Choose an outcome and next step.")
    return {"changes": sorted(set(changes)), "outcome": value["outcome"], "next_step": value["next_step"]}


def clean_reflections(_goal, records):
    result = []
    if not isinstance(records, list):
        return result
    for record in records[-MAX_REFLECTIONS:]:
        try:
            if not isinstance(record, dict) or not isinstance(record.get("definition"), str) \
                    or not re.fullmatch(r"[0-9a-f]{64}", record["definition"]):
                continue
            first, last = (datetime.date.fromisoformat(record[key]) for key in ("start", "end"))
            reviewed = record["reviewed_at"]
            if first > last or (last - first).days >= 366 or not isinstance(reviewed, str) or len(reviewed) > 40:
                continue
            datetime.datetime.fromisoformat(reviewed)
            choices = _choices(record)
        except (KeyError, ValueError, TypeError):
            continue
        result.append({"definition": record["definition"], "start": first.isoformat(), "end": last.isoformat(),
                       "reviewed_at": reviewed, **choices})
    return result


def save_reflection(goal, action, first, last):
    identity = definition_key(goal)
    if action.get("definition") != identity:
        raise ValueError("This goal changed. Reopen its reflection before saving.")
    choices = _choices(action.get("reflection"))
    records = clean_reflections(goal, goal.get("reflections"))
    old = next((row for row in records if row["definition"] == identity
                and (row["start"], row["end"]) == (first, last)), None)
    if old and all(old[key] == value for key, value in choices.items()):
        return records
    records = [row for row in records if row["definition"] != identity or (row["start"], row["end"]) != (first, last)]
    records.append({"definition": identity, "start": first, "end": last,
                    "reviewed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), **choices})
    return sorted(records, key=lambda row: row["start"])[-MAX_REFLECTIONS:]
