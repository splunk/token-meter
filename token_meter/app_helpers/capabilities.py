"""Skill and capability inventory helpers."""

import glob
import hashlib
import json
import os
import re
import secrets

from token_meter.domain.tools import capability_control_groups as _domain_capability_control_groups
from token_meter.app_helpers.common import SKILL_NAME_RE


SKILL_PATH_RE = re.compile(r"(?:^|[/\\])([^/\\\s'\"]+)[/\\]SKILL\.md(?:\b|$)", re.IGNORECASE)


_ACTION_TOKEN = secrets.token_urlsafe(24)


_SKILL_EMPTY_CAPABILITY_VALUES = frozenset({"", "false", "null", "none", "~"})


def _skill_frontmatter_key(value):
    return str(value or "").strip().casefold().replace("_", "-")


def _skill_frontmatter(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read(65536)
    except OSError:
        return {}
    lines = raw.splitlines()
    if not lines or lines[0].lstrip("\ufeff").strip() != "---":
        return {}
    closing = next((idx for idx, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    if closing is None:
        return {}
    fm = {}
    current_key = None
    for raw_line in lines[1:closing]:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if raw_line[:1].isspace():
            if current_key and fm.get(current_key) == "":
                fm[current_key] = "<nested-value>"
            continue
        m = re.match(r"^([A-Za-z0-9_.@+-]+)\s*:\s*(.*)", raw_line.rstrip())
        if m:
            key = m.group(1).strip()
            val = re.sub(r"\s+#.*$", "", m.group(2)).strip()
            normalized = val.casefold()
            if normalized == "true":
                fm[key] = True
            elif normalized == "false":
                fm[key] = False
            else:
                fm[key] = val
            current_key = key
        else:
            current_key = None
    return fm


def _skill_capability_value_is_nonempty(value):
    if value is True:
        return True
    if value is False or value is None:
        return False
    text = str(value).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        text = text[1:-1].strip()
    if text.casefold() in _SKILL_EMPTY_CAPABILITY_VALUES:
        return False
    if re.fullmatch(r"\[\s*\]|\{\s*\}", text):
        return False
    return True


def skill_names_from_value(value, tool_name=""):
    """Infer path-based loads and explicit native Skill-tool activations."""
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        text = str(value or "")
    names = {
        match.group(1) for match in SKILL_PATH_RE.finditer(text)
        if SKILL_NAME_RE.fullmatch(match.group(1))
    }
    tool_leaf = str(tool_name or "").rsplit(".", 1)[-1].casefold()
    direct = value.get("skill") if tool_leaf == "skill" and isinstance(value, dict) else None
    if isinstance(direct, str):
        direct = direct.strip()
        if SKILL_NAME_RE.fullmatch(direct):
            names.add(direct.rsplit(":", 1)[-1])
    return sorted(names)


def _skill_dir_names(*roots):
    names = set()
    for root in roots:
        for path in glob.glob(os.path.join(root, "*", "SKILL.md")):
            names.add(os.path.basename(os.path.dirname(path)))
    return names


def skill_identity(runtime, name, origin_id, plugin_id=""):
    """Return a stable identity that cannot collide across runtimes or origins."""
    runtime_key = re.sub(r"[^a-z0-9]+", "-", str(runtime or "unknown").lower()).strip("-") or "unknown"
    owner = str(plugin_id or origin_id or "unknown").strip()
    return f"skill:{runtime_key}:{owner}:{name}"


def capability_control_groups(mcp_items, skill_items):
    return _domain_capability_control_groups(mcp_items, skill_items)


def capability_content_revision(*parts):
    """Return a stable revision for JSON-safe capability evidence."""
    encoded = json.dumps(
        parts, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


def capability_summary_payload(capabilities):
    """Return decision evidence without the heavy inventory rows."""
    capabilities = capabilities or {}
    payload = {key: value for key, value in capabilities.items() if key != "items"}
    payload["inventory_count"] = len(capabilities.get("items") or [])
    if isinstance(payload.get("claude_desktop"), dict):
        payload["claude_desktop"] = {
            key: value for key, value in payload["claude_desktop"].items() if key != "roots"
        }
    return payload


def capability_action_capability():
    return {
        "token": _ACTION_TOKEN,
        "skill_pack_toggle": True,
    }


def _capability_scan_label(coverage):
    scanned = int(coverage.get("sessions_scanned") or 0)
    total = coverage.get("sessions_total")
    return f"{scanned} of {total}" if total is not None else f"{scanned}; discovery still running"
