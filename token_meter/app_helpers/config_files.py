"""Read-only agent configuration file parsing (TOML and similar)."""

import json
import re


def toml_named_sections(path, table):
    """Read simple enabled state from named TOML sections without a TOML dependency."""
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return {}
    header = re.compile(rf'^\[{re.escape(table)}\.(?:"([^"]+)"|([^\.\]]+))\]\s*$', re.MULTILINE)
    matches = list(header.finditer(text))
    result = {}
    for index, match in enumerate(matches):
        name = (match.group(1) or match.group(2) or "").strip()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end():end]
        enabled_match = re.search(r'^\s*enabled\s*=\s*(true|false)\s*$', body, re.MULTILINE | re.IGNORECASE)
        result[name] = {
            "enabled": enabled_match is None or enabled_match.group(1).lower() == "true",
            "start": match.start(), "body_start": match.end(), "end": end,
        }
    return result


def _toml_line_structure(line, state="normal", array_depth=0):
    """Track TOML string state so root-table scanning never enters a literal."""
    index = 0
    while index < len(line):
        char = line[index]
        if state == "triple-basic":
            if line.startswith('\"\"\"', index):
                state, index = "normal", index + 3
            else:
                index += 1
        elif state == "triple-literal":
            if line.startswith("'''", index):
                state, index = "normal", index + 3
            else:
                index += 1
        elif state == "basic":
            if char == "\\":
                index += 2
            elif char == '"':
                state, index = "normal", index + 1
            else:
                index += 1
        elif state == "literal":
            if char == "'":
                state = "normal"
            index += 1
        else:
            if char == "#":
                break
            if line.startswith('\"\"\"', index):
                state, index = "triple-basic", index + 3
            elif line.startswith("'''", index):
                state, index = "triple-literal", index + 3
            elif char == '"':
                state, index = "basic", index + 1
            elif char == "'":
                state, index = "literal", index + 1
            elif char == "[":
                array_depth, index = array_depth + 1, index + 1
            elif char == "]":
                array_depth, index = max(0, array_depth - 1), index + 1
            else:
                index += 1
    return state, array_depth


def _toml_line_state(line, state="normal"):
    return _toml_line_structure(line, state)[0]


def _toml_is_table_header(line, state, array_depth):
    if state != "normal" or array_depth:
        return False
    return bool(re.match(r"^[ \t]*(?:\[\[.*\]\]|\[.*\])[ \t]*(?:#.*)?(?:\r?\n)?$", line))


def _toml_root_prefix(text):
    offset, state, array_depth = 0, "normal", 0
    for line in text.splitlines(keepends=True):
        if _toml_is_table_header(line, state, array_depth):
            return text[:offset], text[offset:]
        state, array_depth = _toml_line_structure(line, state, array_depth)
        offset += len(line)
    return text, ""


def _toml_comment_index(value):
    index, state = 0, "normal"
    while index < len(value):
        char = value[index]
        if state == "basic":
            if char == "\\":
                index += 2
                continue
            if char == '"':
                state = "normal"
        elif state == "literal":
            if char == "'":
                state = "normal"
        elif char == "#":
            return index
        elif char == '"':
            state = "basic"
        elif char == "'":
            state = "literal"
        index += 1
    return len(value)


def _toml_root_assignment(prefix, key):
    pattern = re.compile(rf"^(?P<before>[ \t]*(?:{re.escape(key)}|\"{re.escape(key)}\")[ \t]*=[ \t]*)(?P<after>.*)$")
    offset, state = 0, "normal"
    for line in prefix.splitlines(keepends=True):
        line_body = line.rstrip("\r\n")
        line_end = line[len(line_body):]
        match = pattern.match(line_body) if state == "normal" else None
        if match:
            after = match.group("after")
            comment_at = _toml_comment_index(after)
            value = after[:comment_at].rstrip()
            suffix = after[len(value):] + line_end
            return {
                "start": offset,
                "end": offset + len(line),
                "before": match.group("before"),
                "value": value,
                "suffix": suffix,
            }
        state = _toml_line_state(line, state)
        offset += len(line)
    return None


def _toml_root_scalar(text, key):
    prefix, _ = _toml_root_prefix(text)
    assignment = _toml_root_assignment(prefix, key)
    if not assignment:
        return None
    value = assignment["value"]
    if value.startswith(('\"\"\"', "'''")):
        return None
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    if len(value) >= 2 and value[0] == "'" and value[-1] == "'":
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        return None


def _toml_root_scalar_text(value):
    return str(int(value)) if isinstance(value, int) else json.dumps(value, ensure_ascii=False)
