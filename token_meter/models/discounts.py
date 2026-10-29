"""Pure contracted-discount rules: validation, lookup, revision, channel mapping.

A discount is a percentage off list price for one price list (whose public
rates priced the request) and one billing channel (who invoices it), valid over
a half-open ``[start, end)`` interval of Unix seconds. This module performs no
I/O; persistence and application live in the application layer.
"""

import bisect
import datetime
import hashlib
import json
import math
import re
import time


PRICE_LISTS = ("anthropic", "openai", "cursor", "kiro", "opencode")
CHANNELS = ("direct", "bedrock", "vertex", "azure")
ANY_CHANNEL = "any"
RULE_CHANNELS = CHANNELS + (ANY_CHANNEL,)
# A request may also resolve to a channel no rule can name; it matches only
# ``any`` rules.
REQUEST_CHANNELS = CHANNELS + ("other", "unknown")
# Vendors that set the price you pay: their lists are matched only by their
# own rules, and those rules always cover every channel.
FIXED_CHANNEL_PRICE_LISTS = ("cursor", "kiro", "opencode")
VENDOR_PRICE_LISTS = ("anthropic", "openai")
RUNTIME_CHANNEL_KEYS = ("claude_code", "claude_desktop")
RUNTIME_CHANNEL_CHOICES = ("auto", "direct", "bedrock", "vertex")
SOURCES = ("dashboard", "api")
DEFAULT_SET_BY = {"dashboard": "Settings", "api": "Another tool"}

MAX_RULES_PER_SCOPE = 64
MAX_RULES = 256
MAX_LABEL_CHARS = 60
MAX_EXTERNAL_ID_CHARS = 64
MAX_SET_BY_CHARS = 40

RULE_ID_RE = re.compile(r"^d_[a-z0-9]{8,40}$")
EXTERNAL_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
SET_BY_RE = re.compile(r"^[A-Za-z0-9._ -]{1,40}$")
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_ISO_OFFSET_RE = re.compile(r"(?:[Zz]|[+-]\d{2}:?\d{2})$")

PRICE_LIST_LABELS = {
    "anthropic": "Anthropic", "openai": "OpenAI", "cursor": "Cursor",
    "kiro": "Kiro", "opencode": "OpenCode",
}
CHANNEL_LABELS = {
    "direct": "billed by the provider", "bedrock": "billed by AWS Bedrock",
    "vertex": "billed by Google Vertex", "azure": "billed by Microsoft Azure",
    "any": "billed by anyone",
}
_RULE_FIELDS = (
    "id", "external_id", "price_list", "channel", "percent", "start", "end",
    "label", "source", "set_by", "updated_at",
)
# Fields a person or tool chooses; the rest are server bookkeeping.
CONTENT_FIELDS = ("external_id", "price_list", "channel", "percent", "start", "end", "label")


class CostBreakdown(dict):
    """A priced breakdown whose values are already discounted.

    It is a plain ``dict`` to every existing caller (``sum(values())`` keeps
    working); ``discount_usd`` is the amount removed and ``list_usd`` the
    undiscounted total, so ``list_usd == sum(values()) + discount_usd``.
    """

    __slots__ = ("discount_usd", "list_usd", "discount_rule_id")

    def __init__(self, values=(), discount_usd=0.0, list_usd=None, discount_rule_id=None):
        super().__init__(values)
        self.discount_usd = float(discount_usd or 0.0)
        self.list_usd = (
            float(list_usd) if list_usd is not None
            else sum(float(value or 0) for value in self.values()) + self.discount_usd
        )
        self.discount_rule_id = discount_rule_id

    def __reduce__(self):
        return (
            CostBreakdown,
            (dict(self), self.discount_usd, self.list_usd, self.discount_rule_id),
        )


def scope_label(price_list, channel):
    """Plain-language name for one (price list, channel) scope."""
    name = PRICE_LIST_LABELS.get(price_list, str(price_list))
    if price_list in FIXED_CHANNEL_PRICE_LISTS:
        return name
    return "{} {}".format(name, CHANNEL_LABELS.get(channel, str(channel)))


def format_percent(percent):
    value = float(percent)
    return ("%d" % value) if value.is_integer() else ("%g" % value)


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _clean_number(value):
    """Store integral seconds as int so canonical JSON is stable."""
    return int(value) if float(value).is_integer() else float(value)


def parse_timestamp(value, field="Date"):
    """Accept Unix seconds or ISO-8601 with an explicit offset; ``None`` is open."""
    if value is None:
        return None
    number = _number(value)
    if number is not None:
        if number < 0:
            raise ValueError("{} must not be before 1970.".format(field))
        return _clean_number(number)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if len(text) > 40 or "T" not in text.upper() or not _ISO_OFFSET_RE.search(text):
            raise ValueError(
                "{} must be Unix seconds or an ISO-8601 time with a time zone offset.".format(field)
            )
        normalized = text[:-1] + "+00:00" if text[-1] in "Zz" else text
        try:
            parsed = datetime.datetime.fromisoformat(normalized)
        except ValueError:
            raise ValueError(
                "{} must be Unix seconds or an ISO-8601 time with a time zone offset.".format(field)
            ) from None
        if parsed.tzinfo is None:
            raise ValueError("{} needs a time zone offset.".format(field))
        stamp = parsed.timestamp()
        if stamp < 0:
            raise ValueError("{} must not be before 1970.".format(field))
        return _clean_number(stamp)
    raise ValueError(
        "{} must be Unix seconds or an ISO-8601 time with a time zone offset.".format(field)
    )


def iso_timestamp(value):
    """Render stored Unix seconds as UTC ISO-8601, or ``None`` when open."""
    if value is None:
        return None
    return datetime.datetime.fromtimestamp(
        float(value), datetime.timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_client_name(value, source="api"):
    """Return a display-safe caller name, or the default for its source."""
    text = " ".join(str(value or "").split())
    if text and SET_BY_RE.fullmatch(text):
        return text
    return DEFAULT_SET_BY.get(source, "Another tool")


def normalize_rule(raw):
    """Validate one stored or submitted rule; raise ValueError with a short message."""
    if not isinstance(raw, dict):
        raise ValueError("Each discount must be an object.")
    rule_id = raw.get("id")
    if not isinstance(rule_id, str) or not RULE_ID_RE.fullmatch(rule_id):
        raise ValueError("Discount id is invalid.")
    price_list = str(raw.get("price_list") or "").strip().lower()
    if price_list not in PRICE_LISTS:
        raise ValueError("Choose Anthropic, OpenAI, Cursor, Kiro, or OpenCode.")
    channel = raw.get("channel")
    channel = ANY_CHANNEL if channel is None else str(channel).strip().lower()
    if channel not in RULE_CHANNELS:
        raise ValueError("Choose who bills you: the provider, AWS Bedrock, Google Vertex, Microsoft Azure, or anyone.")
    if price_list in FIXED_CHANNEL_PRICE_LISTS and channel != ANY_CHANNEL:
        raise ValueError("{} discounts apply however it is billed.".format(PRICE_LIST_LABELS[price_list]))
    percent = _number(raw.get("percent"))
    if percent is None:
        raise ValueError("Discount percent must be a number.")
    percent = round(percent, 2)
    if not 0 < percent < 100:
        raise ValueError("Discount percent must be more than 0 and less than 100.")
    start = parse_timestamp(raw.get("start"), "Start date")
    end = parse_timestamp(raw.get("end"), "End date")
    if start is not None and end is not None and not start < end:
        raise ValueError("A discount must end after it starts.")
    rule = {
        "id": rule_id,
        "price_list": price_list,
        "channel": channel,
        "percent": _clean_number(percent),
        "start": start,
        "end": end,
    }
    external_id = raw.get("external_id")
    if external_id not in (None, ""):
        if not isinstance(external_id, str) or not EXTERNAL_ID_RE.fullmatch(external_id):
            raise ValueError("External id must be 1–64 letters, digits, or . _ : -.")
        rule["external_id"] = external_id
    label = raw.get("label")
    if label not in (None, ""):
        if not isinstance(label, str):
            raise ValueError("Discount note must be text.")
        label = label.strip()
        if len(label) > MAX_LABEL_CHARS or not label.isprintable():
            raise ValueError("Discount note must be at most 60 printable characters on one line.")
        if label:
            rule["label"] = label
    source = raw.get("source") or "dashboard"
    if source not in SOURCES:
        raise ValueError("Discount source is invalid.")
    rule["source"] = source
    set_by = raw.get("set_by")
    if set_by in (None, ""):
        set_by = DEFAULT_SET_BY[source]
    if not isinstance(set_by, str) or not SET_BY_RE.fullmatch(set_by):
        raise ValueError("Discount owner name is invalid.")
    rule["set_by"] = set_by
    updated_at = raw.get("updated_at")
    if updated_at is not None:
        updated_at = _number(updated_at)
        if updated_at is None or updated_at < 0:
            raise ValueError("Discount update time is invalid.")
        updated_at = _clean_number(updated_at)
    rule["updated_at"] = updated_at
    return {field: rule[field] for field in _RULE_FIELDS if field in rule}


def _sort_key(rule):
    return (
        PRICE_LISTS.index(rule["price_list"]), RULE_CHANNELS.index(rule["channel"]),
        -math.inf if rule["start"] is None else rule["start"], rule["id"],
    )


def _overlaps(left, right):
    left_start = -math.inf if left["start"] is None else left["start"]
    left_end = math.inf if left["end"] is None else left["end"]
    right_start = -math.inf if right["start"] is None else right["start"]
    right_end = math.inf if right["end"] is None else right["end"]
    return left_start < right_end and right_start < left_end


def _clash_message(existing):
    return "{} already has {}% for overlapping dates.".format(
        scope_label(existing["price_list"], existing["channel"]),
        format_percent(existing["percent"]),
    )


def normalize_rules(raw_rules):
    """Validate a complete rule set strictly; return rules in canonical order."""
    if raw_rules is None:
        raw_rules = []
    if not isinstance(raw_rules, list):
        raise ValueError("Discounts must be a list.")
    if len(raw_rules) > MAX_RULES:
        raise ValueError("Use at most {} discounts.".format(MAX_RULES))
    rules = [normalize_rule(raw) for raw in raw_rules]
    ids = set()
    external_ids = set()
    scopes = {}
    for rule in rules:
        if rule["id"] in ids:
            raise ValueError("Two discounts share one id.")
        ids.add(rule["id"])
        external_id = rule.get("external_id")
        if external_id is not None:
            if external_id in external_ids:
                raise ValueError("Two discounts share the external id {}.".format(external_id))
            external_ids.add(external_id)
        scope = scopes.setdefault((rule["price_list"], rule["channel"]), [])
        if len(scope) >= MAX_RULES_PER_SCOPE:
            raise ValueError("{} can have at most {} discounts.".format(
                scope_label(rule["price_list"], rule["channel"]), MAX_RULES_PER_SCOPE,
            ))
        for existing in scope:
            if _overlaps(existing, rule):
                raise ValueError(_clash_message(existing))
        scope.append(rule)
    return sorted(rules, key=_sort_key)


def normalize_rules_lenient(raw_rules):
    """Keep every individually valid stored rule; return ``(rules, invalid_count)``.

    A hand-edited file never disables valid rules: an invalid entry, a duplicate,
    or an entry clashing with an earlier valid one is skipped and counted.
    """
    if not isinstance(raw_rules, list):
        return [], (0 if raw_rules is None else 1)
    kept = []
    invalid = 0
    for raw in raw_rules:
        if len(kept) >= MAX_RULES:
            invalid += 1
            continue
        try:
            normalize_rules([*kept, normalize_rule(raw)])
        except ValueError:
            invalid += 1
            continue
        kept.append(normalize_rule(raw))
    return sorted(kept, key=_sort_key), invalid


def normalize_runtime_channels(raw, strict=False):
    """Return a complete ``runtime_channels`` mapping; ``auto`` when absent."""
    channels = {key: "auto" for key in RUNTIME_CHANNEL_KEYS}
    if raw is None:
        return channels
    if not isinstance(raw, dict):
        if strict:
            raise ValueError("Claude billing choices must be an object.")
        return channels
    for key, value in raw.items():
        if key not in RUNTIME_CHANNEL_KEYS:
            if strict:
                raise ValueError("Unknown Claude app.")
            continue
        value = str(value or "").strip().lower()
        if value not in RUNTIME_CHANNEL_CHOICES:
            if strict:
                raise ValueError("Choose automatic, the provider, AWS Bedrock, or Google Vertex.")
            continue
        channels[key] = value
    return channels


def build_index(rules):
    """Precompute per-scope sorted intervals for bisect lookups."""
    grouped = {}
    for rule in rules or ():
        grouped.setdefault((rule["price_list"], rule["channel"]), []).append(rule)
    index = {}
    for scope, items in grouped.items():
        items.sort(key=lambda rule: -math.inf if rule["start"] is None else rule["start"])
        index[scope] = (
            [(-math.inf if rule["start"] is None else float(rule["start"])) for rule in items],
            [(
                math.inf if rule["end"] is None else float(rule["end"]),
                float(rule["percent"]) / 100.0,
                rule["id"],
            ) for rule in items],
        )
    return index


def _lookup(entry, ts):
    starts, values = entry
    position = bisect.bisect_right(starts, ts) - 1
    if position < 0:
        return None
    end, fraction, rule_id = values[position]
    return (fraction, rule_id) if ts < end else None


def discount_for(index, price_list, channel, ts=None):
    """Return ``(fraction, rule_id)`` for one request, or ``None`` for list price.

    The exact channel wins, then ``any``. ``ts`` of ``None`` means now.
    """
    if not index:
        return None
    ts = time.time() if ts is None else float(ts)
    if channel and channel != ANY_CHANNEL:
        entry = index.get((price_list, channel))
        if entry is not None:
            found = _lookup(entry, ts)
            if found is not None:
                return found
    entry = index.get((price_list, ANY_CHANNEL))
    return _lookup(entry, ts) if entry is not None else None


def revision(rules, runtime_channels=None):
    """Stable fingerprint of the rule set and Claude channel declarations."""
    canonical = json.dumps(
        {
            "rules": list(rules or ()),
            "runtime_channels": normalize_runtime_channels(runtime_channels),
        },
        sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    )
    return "r_" + hashlib.sha256(canonical.encode("ascii")).hexdigest()[:16]


def channel_from_provider_name(name):
    """Map an explicit runtime provider name onto a billing channel."""
    value = str(name or "").strip().lower()
    if not value:
        return "unknown"
    if "bedrock" in value:
        return "bedrock"
    if "vertex" in value:
        return "vertex"
    if "azure" in value:
        return "azure"
    if value in ("anthropic", "openai"):
        return "direct"
    return "other"


def claude_channel_from_request_id(request_id):
    """Infer a Claude channel from a request id's shape only, never its value."""
    if not isinstance(request_id, str):
        return None
    if request_id.startswith("req_"):
        return "direct"
    if _UUID_RE.fullmatch(request_id):
        return "bedrock"
    return None


def vendor_price_list(model, provider=None):
    """Return the discountable vendor list for a runtime-estimated request.

    Only Anthropic and OpenAI list prices are discountable for runtimes that
    price requests themselves; other vendors return ``None``.
    """
    provider = str(provider or "").strip().lower()
    if provider in VENDOR_PRICE_LISTS:
        return provider
    model = str(model or "").strip().lower()
    if "claude" in model or "anthropic" in model:
        return "anthropic"
    name = model.rsplit("/", 1)[-1]
    if "openai" in model or name.startswith(("gpt-", "o1", "o3", "o4", "codex")):
        return "openai"
    return None
