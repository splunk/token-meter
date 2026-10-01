"""Local work classification: bounded queue, label ledger, pacing, and a loopback Ollama client.

Typed user-turn text and the preceding assistant-reply tail live only in the
bounded in-memory queue and are sent only to a loopback Ollama URL. The ledger stores salted keys, enum labels, confidences,
and reason codes; it never stores text.
"""

import collections
import hashlib
import http.client
import ipaddress
import json
import math
import os
import random
import re
import secrets
import sqlite3
import threading
import time
import urllib.parse


LEDGER_SCHEMA_VERSION = 3
QUEUE_LIMIT = 256
QUEUE_LOW_WATER = 16
REFILL_BATCH = 3
MAX_ITEM_CHARS = 2_000
SKELETON_HEAD = 1_200
SKELETON_TAIL = 500
CONTEXT_CHARS = 600
MAX_ITEM_ATTEMPTS = 3
ITEM_RETRY_DELAYS_S = (60, 600, 3_600)
TRANSPORT_BACKOFF_BASE_S = 5
TRANSPORT_BACKOFF_CAP_S = 600
SETUP_PROBE_S = 60
THROTTLE_WAIT_S = 60
STORAGE_RETRY_S = 300
LOAD_PER_CPU_LIMIT = 0.75
LATENCY_DRIFT_FACTOR = 3.0
LATENCY_WINDOW = 10
ACTIVE_WINDOW_S = 600
UNCLEAR_CONFIDENCE = 0.5
# Per-question Unclear cutoffs, from the live-label audit (work type is right far more often than 0.5 implies).
UNCLEAR_BY_QUESTION = {"work_type": 0.35, "area": 0.5, "correction": 0.5}
# Bump when prompt wording, options, or turn selection changes; stale labels are shown until relabeled.
PROMPT_VERSION = "p2"
MIN_OPENER_WORDS = 3
MIN_GAP_S = 0.25
NUM_CTX = 4_096
KEEP_ALIVE = "2m"
REFILL_INTERVAL_S = 30
LATENCY_BASELINE_ALPHA = 0.02

DEFAULT_MODEL = "token-meter-jet"
DEFAULT_URL = "http://127.0.0.1:11434"
RATE_CHOICES = (5, 10, 20, 40, 60)
DEFAULT_RATE_PER_MINUTE = 5  # Gentle enough for a 4B model on a low-end laptop.
BACKFILL_CHOICES = (30, 90, 365, 0)
MIN_AREAS, MAX_AREAS = 2, 8
MAX_AREA_NAME, MAX_AREA_DESCRIPTION = 40, 160

WORK_TYPES = {
    "debug": "diagnosing or fixing a failure, bug, install or setup error, or unexpected behavior",
    "feature": "building new functionality or changing product behavior or design",
    "refactor": "restructuring, cleanup, or renaming without changing behavior",
    "docs": "writing or editing documentation, posts, slides, messages, or other prose",
    "explore": "understanding, explaining, researching, or planning without making changes",
    "review": "reviewing, testing, evaluating, or verifying existing work",
    "ops": "running git, release, or install commands, or machine upkeep, without changing code",
    "other": "a non-software request, such as personal, financial, or general questions",
}
TURN_TYPES = {
    "correction": "says the previous work is wrong, broken, incomplete, not good enough, "
                  "or not what was asked, or asks to undo it",
    "follow_up": "asks for new or additional work that builds on what was done",
    "approval": "accepts the work, says thanks, or gives the go-ahead to continue",
    "clarification": "answers the assistant's question or supplies requested details or preferences",
    "question": "asks for an explanation or information without requesting changes",
}
COMPLEXITY_LEVELS = (
    "routine: small, well-defined, low-risk change or question",
    "everyday: normal development such as debugging or a multi-file change",
    "complex: ambiguous, cross-cutting, or hard root-cause work",
    "high-impact: architecture, security-sensitive, or broad risky changes",
)
COMPLEXITY_KEYS = ("routine", "everyday", "complex", "high_impact")
DEFAULT_AREAS = (
    {"name": "Product engineering", "description": "building, fixing, and shipping the main product or codebase"},
    {"name": "Agents and tools", "description": "agent workflows, skills, plugins, automation, and developer tooling"},
    {"name": "Writing and publishing", "description": "blogs, posts, slides, reports, documentation, and media"},
    {"name": "Research and evaluation", "description": "experiments, benchmarks, papers, and model evaluation"},
    {"name": "Operations and setup", "description": "machine and environment upkeep not tied to a product codebase"},
    {"name": "Personal", "description": "personal, financial, family, or non-work questions"},
)

JET_SYSTEM = ("You are Jet, a decision model. Read the state and the question, then answer "
              "with exactly one label from the allowed labels.")
JET_TEMPERATURE = {"choice": 1.1224620483093728, "score": 1.155352696872273, "noul": 1.5422108254079405}
SESSION_QUESTIONS = ("work_type", "area", "complexity")
TURN_QUESTIONS = ("correction",)
CORRECTION_QUESTION = ("Is the user telling the assistant that its previous work was wrong, broken, "
                       "or not what they asked for?")

STATE_DISABLED = "disabled"
STATE_SETUP = "setup_needed"
STATE_RUNNING = "running"
STATE_IDLE = "idle"
STATE_PAUSED = "paused"
STATE_THROTTLED = "throttled"
STATE_BACKOFF = "backoff"
STATE_STORAGE = "storage_error"

_CITATION_RE = re.compile(
    r"<oai-mem-citation>.*?(?:</oai-mem-citation>|$)|:codex-file-citation\{[^}]*\}"
    r"|<image\b[^>]*>(?:</image>)?|<system-reminder>.*?(?:</system-reminder>|$)",
    re.S,
)
# Runtime wrappers ("# Files mentioned by the user:", "# In app browser:", "# Chrome tabs:", ...) that end in the request.
_FILES_WRAPPER_RE = re.compile(r"^\s*# [^\n]{1,60}:[ \t]*\n.*?## My request(?: for Codex)?:\s*", re.S)
_ATTACHMENT_REF_RE = re.compile(r"\[(?:Image|File|Pasted text) #\d+[^\]]*\]")
_LOG_LINE_RE = re.compile(r'(^\s{4,}\S|^\s*at |Traceback|^\s*File "|^\d{4}-\d\d-\d\d|[{}\[\];<>=]{3,}|^\s*[\w./-]+:\d+|^\$ |^\s*[|│])')
_OUTLINE_RE = re.compile(r"^\s*(#{1,6}\s|[-*•]\s|\d+[.)]\s)")


class LabelDeleteError(Exception):
    """Deleting the label ledger failed; labels may still be on disk."""


class ClassifierError(Exception):
    """A classified failure carrying only a bounded reason code."""

    def __init__(self, kind, reason):
        super().__init__(reason)
        self.kind = kind
        self.reason = reason


def clean_text(text):
    text = _FILES_WRAPPER_RE.sub("", str(text or ""))
    text = _ATTACHMENT_REF_RE.sub("", text)
    return _CITATION_RE.sub("", text).strip()


def skeleton(text, limit=MAX_ITEM_CHARS, head=SKELETON_HEAD, tail=SKELETON_TAIL):
    """Compress long text to its opening, outline, and ending within ``limit`` characters."""
    if len(text) <= limit:
        return text
    lines, run = [], 0
    for line in text.splitlines():
        if _LOG_LINE_RE.search(line):
            run += 1
            continue
        if run:
            lines.append(f"[{run} lines of pasted log/code]")
            run = 0
        lines.append(line)
    if run:
        lines.append(f"[{run} lines of pasted log/code]")
    body = "\n".join(lines)
    if len(body) <= limit:
        return body
    first, last = body[:head], body[-tail:]
    middle = body[head:-tail].splitlines()
    budget, outline = limit - head - tail - 80, []
    for line in middle:
        if _OUTLINE_RE.match(line):
            snippet = line.strip()[:120]
            if budget - len(snippet) - 1 < 0:
                break
            outline.append(snippet)
            budget -= len(snippet) + 1
    omitted = max(0, len(middle) - len(outline))
    return f"{first}\n[... {omitted} lines omitted; outline follows ...]\n" + "\n".join(outline) + f"\n[...]\n{last}"


# Two adjacent letters in any script; digits, punctuation, and emoji are not words.
_LETTERS_RE = re.compile(r"[^\W\d_]{2}")
_WORD_RE = re.compile(r"[^\W_](?:[\w']*[^\W_])?")
_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff]")

_INJECTED_PREFIXES = (
    "<task-notification>", "<command-", "<local-command", "<bash-", "<user-prompt-submit-hook>",
    "<recommended_plugins>", "<environment_context>", "<heartbeat>", "<subagent_notification>",
    "<turn_aborted>", "<skill>", "<codex_delegation>", "# AGENTS.md", "<system-reminder>",
    "This session is being continued from a previous conversation",
)


def prepare_text(text):
    """Return cleaned, bounded text, or '' for runtime-injected (non-human) messages."""
    cleaned = clean_text(text)
    if cleaned.startswith(_INJECTED_PREFIXES) or not _LETTERS_RE.search(cleaned):
        # Injected wrappers, and upload- or image-only turns with no words of their own.
        return ""
    return skeleton(cleaned)


def is_substantive(text):
    # CJK scripts do not separate words with spaces, so count their characters instead.
    return (len(_WORD_RE.findall(text)) >= MIN_OPENER_WORDS
            or len(_CJK_RE.findall(text)) >= MIN_OPENER_WORDS)


def question_tags(settings):
    """Expected stored tag per question: prompt version, plus the area taxonomy for areas."""
    tags = {name: PROMPT_VERSION for name in ("work_type", "complexity", "correction", "turn")}
    tags["area"] = f"{PROMPT_VERSION}:{taxonomy_hash(settings['areas'])}"
    return tags


def normalize_areas(values):
    if not isinstance(values, list) or not MIN_AREAS <= len(values) <= MAX_AREAS:
        raise ValueError(f"Use between {MIN_AREAS} and {MAX_AREAS} areas.")
    areas, seen = [], set()
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("Each area needs a name and a description.")
        name = " ".join(str(value.get("name") or "").split())
        description = " ".join(str(value.get("description") or "").split())
        if not name or len(name) > MAX_AREA_NAME:
            raise ValueError(f"Area names must be 1 to {MAX_AREA_NAME} characters.")
        if not description or len(description) > MAX_AREA_DESCRIPTION:
            raise ValueError(f"Area descriptions must be 1 to {MAX_AREA_DESCRIPTION} characters.")
        if name.lower() in seen:
            raise ValueError("Area names must be unique.")
        if name.lower() in ("unclear", "pending"):
            raise ValueError("Unclear and Pending are reserved area names.")
        seen.add(name.lower())
        areas.append({"name": name, "description": description})
    return areas


def validate_ollama_url(value):
    """Accept only plain-HTTP loopback URLs with no path, query, or credentials."""
    raw = str(value or "").strip().rstrip("/")
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme != "http" or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("The Ollama URL must be http:// on this machine.")
    if parsed.path not in ("", "/"):
        raise ValueError("The Ollama URL must not include a path.")
    host = (parsed.hostname or "").lower()
    if host == "localhost":
        host = "127.0.0.1"
    else:
        try:
            if not ipaddress.ip_address(host).is_loopback:
                raise ValueError
        except ValueError:
            raise ValueError("The Ollama URL must use 127.0.0.1, localhost, or ::1.") from None
    try:
        port = parsed.port or 11434
    except ValueError:
        raise ValueError("The Ollama URL port is invalid.") from None
    display_host = f"[{host}]" if ":" in host else host
    return f"http://{display_host}:{port}"


def default_settings():
    return {
        "enabled": False,
        "paused_until": None,
        "pause_on_battery": True,
        "rate_per_minute": DEFAULT_RATE_PER_MINUTE,
        "backfill_days": 90,
        "model": DEFAULT_MODEL,
        "ollama_url": DEFAULT_URL,
        "areas": [dict(area) for area in DEFAULT_AREAS],
    }


def normalize_settings(raw):
    """Return complete settings, keeping defaults for any missing or invalid field."""
    settings = default_settings()
    if not isinstance(raw, dict):
        return settings
    if isinstance(raw.get("enabled"), bool):
        settings["enabled"] = raw["enabled"]
    paused = raw.get("paused_until")
    if paused == "indefinite" or (
            isinstance(paused, (int, float)) and not isinstance(paused, bool)
            and math.isfinite(paused) and paused > 0):
        settings["paused_until"] = paused
    if isinstance(raw.get("pause_on_battery"), bool):
        settings["pause_on_battery"] = raw["pause_on_battery"]
    if raw.get("rate_per_minute") in RATE_CHOICES and not isinstance(raw.get("rate_per_minute"), bool):
        settings["rate_per_minute"] = raw["rate_per_minute"]
    if raw.get("backfill_days") in BACKFILL_CHOICES and not isinstance(raw.get("backfill_days"), bool):
        settings["backfill_days"] = raw["backfill_days"]
    model = str(raw.get("model") or "").strip()
    if model and len(model) <= 100 and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", model):
        settings["model"] = model
    try:
        settings["ollama_url"] = validate_ollama_url(raw.get("ollama_url") or DEFAULT_URL)
    except ValueError:
        pass
    try:
        settings["areas"] = normalize_areas(raw.get("areas"))
    except ValueError:
        pass
    return settings


def taxonomy_hash(areas):
    payload = json.dumps([[a["name"], a["description"]] for a in areas], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def is_paused(settings, now):
    until = settings.get("paused_until")
    return until == "indefinite" or (isinstance(until, (int, float)) and until > now)


# ---------------------------------------------------------------- questions

def _choice(instructions, options):
    return {"type": "choice", "instructions": instructions, "options": list(options.items())}


def question_for(name, settings):
    if name == "work_type":
        return _choice("What kind of work is the user asking for?", WORK_TYPES)
    if name == "area":
        return _choice("Which area of the user's work does this request belong to?",
                       {a["name"]: a["description"] for a in settings["areas"]})
    if name == "complexity":
        return {"type": "score", "instructions": "How complex and high-stakes is this task for a coding agent?",
                "levels": list(COMPLEXITY_LEVELS)}
    if name == "turn":
        return _choice("How does the user's latest message relate to the assistant's previous work?", TURN_TYPES)
    if name == "correction":
        return {"type": "noul", "instructions": CORRECTION_QUESTION}
    raise KeyError(name)


def _labels(n):
    return [chr(65 + index) for index in range(n)]


def render_prompt(state, question, reverse=False):
    """Render Jet's native prompt. Returns (user message, labels, answer keys)."""
    if question["type"] == "choice":
        options = question["options"][::-1] if reverse else question["options"]
        labels = _labels(len(options))
        lines = "\n".join(f"{label}: {key}: {desc}" for label, (key, desc) in zip(labels, options))
        body = (f"Question: {question['instructions']}\nOptions:\n{lines}\n"
                f"Answer with the label of the best option ({labels[0]}-{labels[-1]}).")
        keys = [key for key, _ in options]
    elif question["type"] == "noul":
        labels, keys = ["no", "yes"], [False, True]
        return f"<state>\n{state}\n</state>\n\nQuestion: {question['instructions']}\nAnswer yes or no.", labels, keys
    else:
        labels = [str(index) for index in range(len(question["levels"]))]
        lines = "\n".join(f"{label}: {desc}" for label, desc in zip(labels, question["levels"]))
        body = (f"Question: {question['instructions']}\nScale (lowest to highest):\n{lines}\n"
                f"Answer with the level number (0-{len(labels) - 1}).")
        keys = list(range(len(labels)))
    return f"<state>\n{state}\n</state>\n\n{body}", labels, keys


def read_distribution(response, labels, keys, temperature):
    """Softmax over label tokens found in the first position's top logprobs."""
    positions = response.get("logprobs") if isinstance(response, dict) else None
    if not isinstance(positions, list) or not positions or not isinstance(positions[0], dict):
        raise ClassifierError("item", "no_logprobs")
    found = {}
    for candidate in positions[0].get("top_logprobs") or []:
        if not isinstance(candidate, dict):
            continue
        token = str(candidate.get("token") or "").strip()
        if labels == ["no", "yes"]:
            token = token.lower()
        value = candidate.get("logprob")
        if token in labels and token not in found and isinstance(value, (int, float)) and math.isfinite(value):
            found[token] = float(value)
    if not found:
        raise ClassifierError("item", "no_label_token")
    peak = max(found.values())
    weights = {token: math.exp((value - peak) / temperature) for token, value in found.items()}
    total = sum(weights.values())
    return {keys[labels.index(token)]: weight / total for token, weight in weights.items()}


def read_answer(responses, question, labels_keys):
    """Combine one or two readouts into (value, confidence).

    Choice questions average the original and reversed option orders to cancel
    position bias; score questions use the probability-weighted level.
    """
    temperature = JET_TEMPERATURE[question["type"]]
    distributions = [read_distribution(response, labels, keys, temperature)
                     for response, (labels, keys) in zip(responses, labels_keys)]
    merged = {}
    for distribution in distributions:
        for key, value in distribution.items():
            merged[key] = merged.get(key, 0.0) + value / len(distributions)
    if question["type"] == "score":
        expected = sum(level * value for level, value in merged.items())
        level = min(len(question["levels"]) - 1, max(0, int(round(expected))))
        return level, merged.get(level, 0.0)
    best = max(merged, key=merged.get)
    return best, merged[best]


# ---------------------------------------------------------------- Ollama client

def is_remote_model(entry):
    """True for Ollama entries that proxy inference to another host (for example cloud models)."""
    names = [str(entry.get(key) or "").lower() for key in ("name", "model")]
    return bool(entry.get("remote_host") or entry.get("remote_model")
                or any(re.search(r"(^|[-:])cloud($|[-:])", name) for name in names))


class OllamaClient:
    """Minimal loopback client. Raises ClassifierError with bounded reason codes."""

    def __init__(self, url, model):
        parsed = urllib.parse.urlsplit(validate_ollama_url(url))
        self.host = parsed.hostname
        self.port = parsed.port or 11434
        self.model = model

    def _request(self, method, path, payload=None, timeout=10.0):
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        connection = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        try:
            try:
                connection.request(method, path, body=body, headers={"content-type": "application/json"})
                response = connection.getresponse()
                data = response.read(2 * 1024 * 1024 + 1)
            except (ConnectionError, TimeoutError, OSError, http.client.HTTPException):
                raise ClassifierError("transport", "unreachable") from None
        finally:
            connection.close()
        if len(data) > 2 * 1024 * 1024:
            raise ClassifierError("transport", "oversized_response")
        if 300 <= response.status < 400:
            raise ClassifierError("setup", "redirect_refused")
        text = data.decode("utf-8", "replace")
        if response.status == 404 or (response.status >= 400 and "not found" in text.lower()):
            raise ClassifierError("setup", "model_missing")
        if response.status >= 500:
            raise ClassifierError("transport", "server_error")
        if response.status >= 400:
            raise ClassifierError("item", "rejected")
        try:
            return json.loads(text)
        except ValueError:
            raise ClassifierError("item", "invalid_json") from None

    def version(self):
        return self._request("GET", "/api/version", timeout=2.0)

    def model_digest(self):
        tags = self._request("GET", "/api/tags", timeout=5.0)
        for model in (tags.get("models") or []) if isinstance(tags, dict) else []:
            names = {str(model.get("name") or ""), str(model.get("model") or "")}
            if self.model in names or f"{self.model}:latest" in names:
                if is_remote_model(model):
                    raise ClassifierError("setup", "remote_model")
                return str(model.get("digest") or "")[:24] or "unknown"
        raise ClassifierError("setup", "model_missing")

    def classify(self, prompt, timeout):
        payload = {
            "model": self.model, "stream": False, "think": False, "keep_alive": KEEP_ALIVE,
            "messages": [{"role": "system", "content": JET_SYSTEM}, {"role": "user", "content": prompt}],
            "logprobs": True, "top_logprobs": 20,
            "options": {"temperature": 0, "num_predict": 1, "num_ctx": NUM_CTX},
        }
        return self._request("POST", "/api/chat", payload, timeout=timeout)

    def unload(self):
        try:
            self._request("POST", "/api/generate", {"model": self.model, "keep_alive": 0}, timeout=5.0)
        except ClassifierError:
            pass


# ---------------------------------------------------------------- ledger

_TABLES = {
    "work_labels": ("turn_key", "question", "session_key", "value", "confidence",
                    "taxonomy", "model", "labeled_at"),
    "work_failures": ("turn_key", "question", "session_key", "attempts", "reason",
                      "next_at", "terminal"),
    "work_backlog": ("session_key", "newest_ts", "pending", "added_at"),
    "work_openers": ("session_key", "turn_key", "follow_ups"),
    "work_metadata": ("key", "value"),
}


class LabelLedger:
    """SQLite store of salted keys, enum labels, and reason codes. Never stores text."""

    def __init__(self, path):
        self.path = os.fspath(path)
        self.salt = ""
        self.initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self):
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        if self._incompatible():
            # Recreate rather than DROP TABLE so freed pages with old keys do not linger in the file.
            self._remove_files()
        with self._connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS work_labels (
                turn_key TEXT NOT NULL, question TEXT NOT NULL, session_key TEXT NOT NULL,
                value TEXT NOT NULL, confidence REAL NOT NULL, taxonomy TEXT NOT NULL,
                model TEXT NOT NULL, labeled_at INTEGER NOT NULL,
                PRIMARY KEY (turn_key, question))""")
            connection.execute("""CREATE TABLE IF NOT EXISTS work_failures (
                turn_key TEXT NOT NULL, question TEXT NOT NULL, session_key TEXT NOT NULL,
                attempts INTEGER NOT NULL, reason TEXT NOT NULL, next_at INTEGER NOT NULL,
                terminal INTEGER NOT NULL, PRIMARY KEY (turn_key, question))""")
            connection.execute("""CREATE TABLE IF NOT EXISTS work_backlog (
                session_key TEXT PRIMARY KEY, newest_ts INTEGER NOT NULL,
                pending INTEGER NOT NULL, added_at INTEGER NOT NULL)""")
            # Which turn is each session's opener (by position) and how many classifiable follow-ups it has.
            connection.execute("""CREATE TABLE IF NOT EXISTS work_openers (
                session_key TEXT PRIMARY KEY, turn_key TEXT NOT NULL, follow_ups INTEGER NOT NULL)""")
            connection.execute("""CREATE TABLE IF NOT EXISTS work_metadata (
                key TEXT PRIMARY KEY, value TEXT NOT NULL)""")
            connection.execute("CREATE INDEX IF NOT EXISTS work_labels_session ON work_labels (session_key)")
            stored = connection.execute("SELECT value FROM work_metadata WHERE key = 'salt'").fetchone()
            if stored is None:
                self.salt = secrets.token_hex(32)
                connection.execute("INSERT INTO work_metadata (key, value) VALUES ('salt', ?)", (self.salt,))
            else:
                self.salt = str(stored["value"])
            connection.execute(f"PRAGMA user_version = {LEDGER_SCHEMA_VERSION}")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _incompatible(self):
        if not os.path.exists(self.path):
            return False
        try:
            with self._connect() as connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if 0 < version < LEDGER_SCHEMA_VERSION:
                    return True
                for table, expected in _TABLES.items():
                    columns = tuple(row[1] for row in connection.execute(f'PRAGMA table_info("{table}")'))
                    if columns and columns != expected:
                        return True
        except sqlite3.OperationalError:
            # Locked, unopenable, or I/O errors are transient: surface them, never delete the ledger.
            raise
        except sqlite3.DatabaseError:
            return True
        return False

    def _remove_files(self):
        LabelLedger.remove(self.path)

    @staticmethod
    def remove(path):
        for suffix in ("", "-wal", "-shm", "-journal"):
            try:
                os.remove(path + suffix)
            except FileNotFoundError:
                pass

    def record_label(self, turn_key, question, session_key, value, confidence, taxonomy, model, now):
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO work_labels VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (turn_key, question) DO UPDATE SET value = excluded.value,
                   confidence = excluded.confidence, taxonomy = excluded.taxonomy,
                   model = excluded.model, labeled_at = excluded.labeled_at""",
                (turn_key, question, session_key, str(value), float(confidence), taxonomy, model, int(now)),
            )
            connection.execute("DELETE FROM work_failures WHERE turn_key = ? AND question = ?", (turn_key, question))

    def record_failure(self, turn_key, question, session_key, reason, now):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT attempts FROM work_failures WHERE turn_key = ? AND question = ?",
                (turn_key, question)).fetchone()
            attempts = (int(row["attempts"]) if row else 0) + 1
            terminal = attempts >= MAX_ITEM_ATTEMPTS
            delay = ITEM_RETRY_DELAYS_S[min(attempts - 1, len(ITEM_RETRY_DELAYS_S) - 1)]
            connection.execute(
                """INSERT INTO work_failures VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (turn_key, question) DO UPDATE SET attempts = excluded.attempts,
                   reason = excluded.reason, next_at = excluded.next_at, terminal = excluded.terminal""",
                (turn_key, question, session_key, attempts, reason, int(now + delay), int(terminal)),
            )
        return attempts, terminal

    def labeled_keys(self):
        with self._connect() as connection:
            labels = {(r["turn_key"], r["question"]): r["taxonomy"]
                      for r in connection.execute("SELECT turn_key, question, taxonomy FROM work_labels")}
            failures = {(r["turn_key"], r["question"]): (int(r["next_at"]), bool(r["terminal"]))
                        for r in connection.execute("SELECT turn_key, question, next_at, terminal FROM work_failures")}
        return labels, failures

    def session_labels(self):
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT turn_key, session_key, question, value, confidence, taxonomy, model, labeled_at "
                "FROM work_labels ORDER BY rowid").fetchall()
            failures = connection.execute(
                "SELECT turn_key, session_key, question FROM work_failures WHERE terminal = 1").fetchall()
        return [dict(row) for row in rows], [dict(row) for row in failures]

    def openers(self):
        with self._connect() as connection:
            return {r["session_key"]: (r["turn_key"], int(r["follow_ups"]))
                    for r in connection.execute("SELECT session_key, turn_key, follow_ups FROM work_openers")}

    def record_opener(self, session_key, turn_key, follow_ups):
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO work_openers VALUES (?, ?, ?) ON CONFLICT (session_key)
                   DO UPDATE SET turn_key = excluded.turn_key, follow_ups = excluded.follow_ups""",
                (session_key, turn_key, int(follow_ups)))

    def model_mix(self):
        with self._connect() as connection:
            return {r["model"]: int(r["n"]) for r in connection.execute(
                "SELECT model, COUNT(*) AS n FROM work_labels GROUP BY model")}

    def upsert_backlog(self, session_key, newest_ts, pending, eligible_at, merge=False):
        """Record content-free pending work; ``added_at`` holds the earliest refill time.

        ``merge`` adds to an existing row (overflow from another parse, a failed item);
        otherwise the row is replaced with a parse's authoritative view of the session.
        """
        update = ("pending = work_backlog.pending + excluded.pending, added_at = MIN(added_at, excluded.added_at)"
                  if merge else "pending = excluded.pending, added_at = excluded.added_at")
        with self._connect() as connection:
            connection.execute(
                f"""INSERT INTO work_backlog VALUES (?, ?, ?, ?) ON CONFLICT (session_key)
                    DO UPDATE SET newest_ts = MAX(newest_ts, excluded.newest_ts), {update}""",
                (session_key, int(newest_ts or 0), int(pending), int(eligible_at)))

    def remove_backlog(self, session_key):
        with self._connect() as connection:
            connection.execute("DELETE FROM work_backlog WHERE session_key = ?", (session_key,))

    def next_backlog(self, limit, now):
        with self._connect() as connection:
            return [r["session_key"] for r in connection.execute(
                "SELECT session_key FROM work_backlog WHERE added_at <= ? ORDER BY newest_ts DESC LIMIT ?",
                (int(now), int(limit)))]

    def backlog_pending(self):
        with self._connect() as connection:
            row = connection.execute("SELECT COALESCE(SUM(pending), 0) AS n FROM work_backlog").fetchone()
        return int(row["n"])

    def clear(self):
        self._remove_files()
        self.initialize()


# ---------------------------------------------------------------- pacing

class Pacer:
    """Token bucket with a minimum gap; clock is injectable for tests."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.tokens = 1.0
        self.updated = clock()
        self.last = 0.0

    def wait_time(self, per_minute):
        now = self.clock()
        rate = per_minute / 60.0
        self.tokens = min(1.0, self.tokens + (now - self.updated) * rate)
        self.updated = now
        gap = max(0.0, MIN_GAP_S - (now - self.last))
        need = 0.0 if self.tokens >= 1.0 else (1.0 - self.tokens) / rate
        return max(gap, need)

    def consume(self):
        self.tokens -= 1.0
        self.last = self.clock()


# ---------------------------------------------------------------- service

Item = collections.namedtuple("Item", "priority seq session_key turn_key questions state newest_ts")


class WorkInsightsService:
    """Owns the queue, backlog, worker loop, and ledger for work classification."""

    def __init__(self, ledger_path, settings_provider, client_factory=OllamaClient,
                 clock=time.time, monotonic=time.monotonic, sleep=None,
                 load_probe=None, power_probe=None, refill=None, recovered=None):
        self.ledger_path = ledger_path
        self.settings_provider = settings_provider
        self.client_factory = client_factory
        self.clock = clock
        self.monotonic = monotonic
        self.load_probe = load_probe or _default_load_probe
        self.power_probe = power_probe or (lambda: None)
        self.refill = refill
        self.recovered = recovered
        self.lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.wake = threading.Event()
        self.sleep = sleep or (lambda seconds: self.wake.wait(seconds))
        self.queue = []
        self.queued = set()
        self.seq = 0
        self.pacer = Pacer(monotonic)
        self.latencies = collections.deque(maxlen=LATENCY_WINDOW)
        self.baseline = None
        self.transport_failures = 0
        self.state = STATE_DISABLED
        self.reason = ""
        self.retry_at = 0.0
        self.model_digest = ""
        self.loaded = False
        self.labels_version = 0
        self.generation = 0
        self._snapshot = None
        self.ledger = None
        self._labeled = {}
        self._failures = {}
        self._openers = {}
        self._recent_added = set()
        self._last_refill = None
        self._rate_window = collections.deque(maxlen=50)
        self._turn_values = {}
        self._corrections_memo = (None, {})
        self._delete_requested = False
        self._open_ledger()

    # ---- ledger lifecycle

    @property
    def _delete_marker(self):
        return self.ledger_path + ".delete-pending"

    @property
    def delete_pending(self):
        return self._delete_requested or os.path.exists(self._delete_marker)

    def _finish_pending_delete(self):
        """Complete a requested delete before any reopen.

        The request is held in memory and, when the directory allows it, in a marker file that
        survives restarts; either one blocks reopening until the files are gone.
        """
        with self.write_lock:
            if not self.delete_pending:
                return True
            try:
                LabelLedger.remove(self.ledger_path)
                _remove_if_present(self._delete_marker)
            except OSError:
                self.ledger = None
                self.state, self.reason = STATE_STORAGE, "delete_pending"
                return False
            self._delete_requested = False
            return True

    def _open_ledger(self):
        if not self._finish_pending_delete():
            return
        try:
            self.ledger = LabelLedger(self.ledger_path)
            self._labeled, self._failures = self.ledger.labeled_keys()
            self._openers = self.ledger.openers()
        except (sqlite3.Error, OSError):
            self.ledger = None
            self.state, self.reason = STATE_STORAGE, "ledger_unavailable"

    def session_key(self, row_id):
        salt = self.ledger.salt if self.ledger else ""
        return hashlib.sha256(f"{salt}\0session\0{row_id}".encode("utf-8")).hexdigest()[:24]

    def _turn_key(self, row_id, ordinal):
        # Position only: a content hash next to the stored salt would let short texts be guessed.
        salt = self.ledger.salt if self.ledger else ""
        return hashlib.sha256(f"{salt}\0turn\0{row_id}\0{ordinal}".encode("utf-8")).hexdigest()[:32]

    def _bump(self):
        with self.lock:
            self.labels_version += 1

    # ---- intake (called from the parse hook; must stay cheap)

    def _needs(self, turn_key, question, tags, now):
        stored = self._labeled.get((turn_key, question))
        if stored is not None and stored == tags.get(question):
            return False
        failure = self._failures.get((turn_key, question))
        if failure and (failure[1] or failure[0] > now):
            return False
        return True

    def forget(self, row_id):
        """Drop any backlog row for a session that no longer yields classifiable turns."""
        if self.ledger is not None and self.settings_provider()["enabled"]:
            self._remove_backlog(self.session_key(row_id))

    def observe(self, row_id, turns, last_ts=0.0):
        """Queue unlabeled work for one parsed session. Text is kept only in the bounded queue."""
        settings = self.settings_provider()
        generation = self.generation
        if not settings["enabled"] or self.ledger is None:
            return 0
        session_key = self.session_key(row_id)
        if not turns:
            self._remove_backlog(session_key)
            return 0
        now = self.clock()
        prepared = [prepare_text(turn.get("text") or "") for turn in turns]
        # Label the session from its first substantive request, not a greeting.
        opener_index = next((i for i, text in enumerate(prepared) if text and is_substantive(text)),
                            next((i for i, text in enumerate(prepared) if text), None))
        opener_key = self._turn_key(row_id, opener_index) if opener_index is not None else None
        follow_ups = sum(1 for text in prepared[opener_index + 1:] if text) if opener_key else 0
        horizon = settings["backfill_days"]
        newest = max([float(t.get("ts") or 0) for t in turns] + [float(last_ts or 0)])
        if horizon and newest and newest < now - horizon * 86400:
            # Record the opener so older labeled sessions keep a follow-up count, but queue nothing.
            if opener_key:
                self._note_opener(session_key, opener_key, follow_ups, generation)
            self._remove_backlog(session_key)
            return 0
        tags = question_tags(settings)
        active = bool(newest and newest >= now - ACTIVE_WINDOW_S)
        items = []
        retry_count, retry_at = 0, None
        for ordinal, turn in enumerate(turns):
            text = prepared[ordinal]
            if not text or opener_index is None or ordinal < opener_index:
                continue
            turn_key = self._turn_key(row_id, ordinal)
            opener = ordinal == opener_index
            questions = SESSION_QUESTIONS if opener else TURN_QUESTIONS
            waiting = [f[0] for f in (self._failures.get((turn_key, q)) for q in questions)
                       if f and not f[1] and f[0] > now]
            if waiting:
                retry_count += 1
                retry_at = min(waiting) if retry_at is None else min(retry_at, *waiting)
            wanted = tuple(q for q in questions if self._needs(turn_key, q, tags, now))
            if not wanted:
                continue
            context = clean_text(turn.get("context") or "")[-CONTEXT_CHARS:]
            state = (f"User's message:\n{text}" if opener or not context else
                     f"Assistant's previous message (end):\n{context}\n\nUser's latest message:\n{text}")
            items.append((turn_key, wanted, state, float(turn.get("ts") or newest or 0)))
        if opener_key and not self._note_opener(session_key, opener_key, follow_ups, generation):
            return 0
        if not items and not retry_count:
            self._remove_backlog(session_key)
            return 0
        added = 0
        overflow = collections.defaultdict(lambda: [0, 0.0])
        with self.lock:
            if generation != self.generation:
                return 0  # Keys were computed with a salt that clear() just rotated.
            room = QUEUE_LIMIT - len(self.queue)
            for turn_key, wanted, state, ts in items:
                if turn_key in self.queued:
                    continue
                if room <= 0 and not active:
                    overflow[session_key][0] += 1
                    overflow[session_key][1] = max(overflow[session_key][1], ts)
                    continue
                self.seq += 1
                self.queue.append(Item(0 if active else 1, self.seq, session_key, turn_key, wanted, state, ts))
                self.queued.add(turn_key)
                room -= 1
                added += 1
            if len(self.queue) > QUEUE_LIMIT:
                self.queue.sort(key=lambda i: (i.priority, -i.newest_ts, i.seq))
                dropped = self.queue[QUEUE_LIMIT:]
                del self.queue[QUEUE_LIMIT:]
                for item in dropped:
                    self.queued.discard(item.turn_key)
                    overflow[item.session_key][0] += 1
                    overflow[item.session_key][1] = max(overflow[item.session_key][1], item.newest_ts)
            if added or retry_count or session_key in overflow:
                # Refill keeps a backlog row only for sessions that queued or rescheduled work.
                self._recent_added.add(session_key)
        with self.write_lock:
            if generation != self.generation:
                return 0
            self._write_backlog(session_key, overflow, retry_count, retry_at, newest, now)
        if added:
            self.wake.set()
        return added

    def _note_opener(self, session_key, opener_key, follow_ups, generation):
        """Record the session's opener by position; returns False when a clear() fenced this intake.

        Queued work for a turn whose role changed (an earlier fallback opener, or a follow-up that
        is now the opener) is dropped so it is re-queued for its current role only.
        """
        with self.lock:
            if generation != self.generation:
                return False
            for item in [i for i in self.queue if i.session_key == session_key
                         and (i.turn_key == opener_key) != (i.questions[0] in SESSION_QUESTIONS)]:
                self.queue.remove(item)
                self.queued.discard(item.turn_key)
        if self._openers.get(session_key) == (opener_key, follow_ups):
            return True
        with self.write_lock:
            if generation != self.generation:
                return False
            try:
                self.ledger.record_opener(session_key, opener_key, follow_ups)
            except sqlite3.Error:
                self.state, self.reason = STATE_STORAGE, "ledger_write_failed"
                return True
            with self.lock:
                self._openers[session_key] = (opener_key, follow_ups)
                self.labels_version += 1
        return True

    def _write_backlog(self, session_key, overflow, retry_count, retry_at, newest, now):
        try:
            own = overflow.pop(session_key, None)
            for key, (count, ts) in overflow.items():
                self.ledger.upsert_backlog(key, ts or newest, count, now, merge=True)
            if own:
                self.ledger.upsert_backlog(session_key, own[1] or newest, own[0] + retry_count, now)
            elif retry_count:
                self.ledger.upsert_backlog(session_key, newest, retry_count, retry_at)
            else:
                self.ledger.remove_backlog(session_key)
        except sqlite3.Error:
            self.state, self.reason = STATE_STORAGE, "ledger_write_failed"

    def _remove_backlog(self, session_key):
        try:
            self.ledger.remove_backlog(session_key)
        except (sqlite3.Error, AttributeError):
            pass

    # ---- worker

    def _next_item(self):
        with self.lock:
            if not self.queue:
                return None
            self.queue.sort(key=lambda i: (i.priority, -i.newest_ts, i.seq))
            return self.queue[0]

    def _finish_item(self, item):
        with self.lock:
            try:
                self.queue.remove(item)
            except ValueError:
                pass
            self.queued.discard(item.turn_key)

    def _maybe_refill(self):
        if self.refill is None or self.ledger is None:
            return
        now = self.monotonic()
        if self._last_refill is not None and now - self._last_refill < REFILL_INTERVAL_S:
            return
        with self.lock:
            if len(self.queue) > QUEUE_LOW_WATER:
                return
        self._last_refill = now
        try:
            keys = self.ledger.next_backlog(REFILL_BATCH, self.clock())
        except sqlite3.Error:
            return
        if not keys:
            return
        with self.lock:
            self._recent_added.clear()
        self.refill(keys)
        with self.lock:
            requeued = set(self._recent_added)
        for key in keys:
            # A re-load that queued nothing means the session has no reachable pending work.
            if key not in requeued:
                self._remove_backlog(key)

    def _throttle_reason(self, settings):
        load = self.load_probe()
        if load is not None and load > LOAD_PER_CPU_LIMIT:
            return "system_busy"
        if settings["pause_on_battery"] and self.power_probe() == "battery":
            return "on_battery"
        if self.baseline and len(self.latencies) >= LATENCY_WINDOW:
            median = sorted(self.latencies)[len(self.latencies) // 2]
            if median > LATENCY_DRIFT_FACTOR * self.baseline:
                self.latencies.clear()
                return "model_slow"
        return ""

    def _set(self, state, reason="", retry_in=0.0):
        self.state, self.reason = state, reason
        self.retry_at = self.clock() + retry_in if retry_in else 0.0

    def _unload(self, settings):
        if self.loaded:
            self.client_factory(settings["ollama_url"], settings["model"]).unload()
            self.loaded = False

    def _may_send(self, generation):
        """Gate every model request on enablement, pause, clear, and the rate limit."""
        while True:
            settings = self.settings_provider()
            if (not settings["enabled"] or is_paused(settings, self.clock())
                    or generation != self.generation):
                return False
            wait = self.pacer.wait_time(settings["rate_per_minute"])
            if wait <= 0:
                return True
            self.sleep(wait)
            # Queue activity sets ``wake``; clear it so the next wait sleeps instead of spinning.
            self.wake.clear()

    def _check_digest(self, client, settings):
        """Re-read the local model entry (cheap); refuses remote or cloud models before any request."""
        try:
            digest = client.model_digest()
        except ClassifierError as error:
            return self._fail_global(error)
        if digest != self.model_digest:
            self.model_digest = digest
            self._bump()
        return None

    def step(self):
        """Do at most one item. Returns seconds the loop should wait next."""
        settings = self.settings_provider()
        generation = self.generation
        now = self.clock()
        if not settings["enabled"]:
            with self.lock:
                self.queue.clear()
                self.queued.clear()
            if self.delete_pending:
                self._finish_pending_delete()
                return STORAGE_RETRY_S if self.delete_pending else 5.0
            self._set(STATE_DISABLED)
            return 5.0
        if self.ledger is None:
            self._open_ledger()
            if self.ledger is None:
                return STORAGE_RETRY_S
            self._set(STATE_IDLE)
            if self.recovered is not None:
                # Sessions parsed during the outage were not queued; re-parse them now.
                self.recovered()
        if is_paused(settings, now):
            self._unload(settings)
            self._set(STATE_PAUSED, "manual")
            return 5.0
        if self.state in (STATE_BACKOFF, STATE_SETUP, STATE_STORAGE) and now < self.retry_at:
            return min(5.0, self.retry_at - now)
        client = self.client_factory(settings["ollama_url"], settings["model"])
        self._maybe_refill()
        item = self._next_item()
        if item is None:
            if self.state in (STATE_SETUP, STATE_BACKOFF):
                failed = self._check_digest(client, settings)
                if failed is not None:
                    return failed
            self.loaded = False
            self._set(STATE_IDLE)
            return 5.0
        reason = self._throttle_reason(settings)
        if reason:
            self._unload(settings)
            self._set(STATE_THROTTLED, reason, THROTTLE_WAIT_S)
            return THROTTLE_WAIT_S
        self._set(STATE_RUNNING)
        tags = question_tags(settings)
        retry_scheduled = False
        for question_name in item.questions:
            if not self._needs(item.turn_key, question_name, tags, self.clock()):
                continue
            question = question_for(question_name, settings)
            orders = (False, True) if question["type"] == "choice" else (False,)
            rendered = [render_prompt(item.state, question, reverse) for reverse in orders]
            timeout = min(60.0, 10.0 + len(rendered[0][0]) / 1000.0)
            try:
                responses = []
                for prompt, *_ in rendered:
                    if not self._may_send(generation):
                        return 0.0
                    # A cheap local /api/tags lookup before every request, so a name re-pointed
                    # to a remote or cloud model is refused before any text is sent to it.
                    failed = self._check_digest(client, settings)
                    if failed is not None:
                        return failed
                    self.pacer.consume()
                    started = self.monotonic()
                    responses.append(client.classify(prompt, timeout))
                    self._record_latency((self.monotonic() - started) / (1.0 + len(prompt) / 1000.0))
                value, confidence = read_answer(responses, question, [(r[1], r[2]) for r in rendered])
            except ClassifierError as error:
                if error.kind in ("transport", "setup"):
                    return self._fail_global(error)
                retry_scheduled = self._fail_item(item, question_name, error.reason, generation,
                                                  schedule_retry=not retry_scheduled) or retry_scheduled
                continue
            self.loaded = True
            if question_name == "complexity":
                value = COMPLEXITY_KEYS[int(value)]
            if not self._store(item, question_name, value, confidence, tags[question_name], generation):
                return STORAGE_RETRY_S
        self._finish_item(item)
        self.transport_failures = 0
        self._rate_window.append(self.clock())
        return 0.0

    def _record_latency(self, seconds):
        """Track per-1k-character latency; the baseline adapts slowly so only sudden slowdowns throttle."""
        self.latencies.append(seconds)
        if self.baseline is None:
            if len(self.latencies) >= LATENCY_WINDOW:
                self.baseline = sorted(self.latencies)[len(self.latencies) // 2]
                self.latencies.clear()
        else:
            self.baseline += LATENCY_BASELINE_ALPHA * (seconds - self.baseline)

    def _store(self, item, question, value, confidence, tag, generation):
        with self.write_lock:
            if generation != self.generation:
                return True
            try:
                self.ledger.record_label(item.turn_key, question, item.session_key, value, confidence,
                                         tag, self.model_digest, self.clock())
            except sqlite3.Error:
                self._set(STATE_STORAGE, "ledger_write_failed", STORAGE_RETRY_S)
                return False
        with self.lock:
            self._labeled[(item.turn_key, question)] = tag
            self._failures.pop((item.turn_key, question), None)
            self.labels_version += 1
        return True

    def _fail_item(self, item, question, reason, generation, schedule_retry=True):
        """Record a failed question; returns True when a retry row was scheduled for the item."""
        with self.write_lock:
            if generation != self.generation:
                return False
            try:
                attempts, terminal = self.ledger.record_failure(
                    item.turn_key, question, item.session_key, reason, self.clock())
                delay = ITEM_RETRY_DELAYS_S[min(attempts - 1, len(ITEM_RETRY_DELAYS_S) - 1)]
                with self.lock:
                    self._failures[(item.turn_key, question)] = (int(self.clock() + delay), terminal)
                    if terminal:
                        self.labels_version += 1
                if not terminal and schedule_retry:
                    # One retry row per item, so pending stays in item units.
                    self.ledger.upsert_backlog(item.session_key, item.newest_ts, 1,
                                               self.clock() + delay, merge=True)
                    return True
            except sqlite3.Error:
                self._set(STATE_STORAGE, "ledger_write_failed", STORAGE_RETRY_S)
            return False

    def _fail_global(self, error):
        if error.kind == "setup":
            self.model_digest = ""
            self._set(STATE_SETUP, error.reason, SETUP_PROBE_S)
            return SETUP_PROBE_S
        self.transport_failures += 1
        delay = min(TRANSPORT_BACKOFF_CAP_S, TRANSPORT_BACKOFF_BASE_S * 2 ** (self.transport_failures - 1))
        delay *= random.uniform(0.8, 1.2)
        self._set(STATE_BACKOFF, error.reason, delay)
        return delay

    def run_forever(self, stop=None):
        backoff = 1.0
        while stop is None or not stop.is_set():
            try:
                wait = self.step()
                backoff = 1.0
            except Exception:  # Supervisor: never let the worker die; record only a reason code.
                self._set(STATE_BACKOFF, "internal_error", backoff)
                wait, backoff = backoff, min(300.0, backoff * 2)
            if wait > 0:
                # Clear after waking so a settings change that arrives mid-step is not lost.
                self.sleep(wait)
                self.wake.clear()

    # ---- queries

    def status(self):
        settings = self.settings_provider()
        with self.lock:
            queued = len(self.queue)
        try:
            backlog = self.ledger.backlog_pending() if self.ledger else 0
            mix = self.ledger.model_mix() if self.ledger else {}
        except sqlite3.Error:
            backlog, mix = 0, {}
        state = self.state
        if self.delete_pending:
            state = STATE_STORAGE
        elif not settings["enabled"]:
            state = STATE_DISABLED
        elif is_paused(settings, self.clock()):
            state = STATE_PAUSED
        elif state == STATE_DISABLED:
            state = STATE_IDLE
        pending = queued + backlog
        recent = [t for t in self._rate_window if t >= self.clock() - 300]
        elapsed = (recent[-1] - recent[0]) / 60.0 if len(recent) >= 5 else 0.0
        # Session openers need several model calls per item, so assume a third of the call rate until measured.
        per_minute = (len(recent) - 1) / elapsed if elapsed > 0 else float(settings["rate_per_minute"]) / 3
        return {
            "state": state,
            "reason": ("delete_pending" if self.delete_pending else
                       self.reason if state == self.state else ("manual" if state == STATE_PAUSED else "")),
            "pending": pending,
            "queued": queued,
            "eta_s": int(pending / per_minute * 60) if pending and per_minute else 0,
            "retry_at": self.retry_at or None,
            "paused_until": settings["paused_until"],
            "model": settings["model"],
            "model_versions": len(mix),
            "labels": sum(mix.values()),
        }

    def snapshot(self):
        """Return {session_key: labels} for aggregation; cached by labels_version.

        Session labels come from the turn last recorded as the session's opener (its first
        substantive request, by position), whatever order the labels were written in. Until that
        turn has a label, and for sessions without an opener record, the newest label is used.
        Labels from the current prompt version win; older ones are shown until relabeled.
        """
        version = self.labels_version
        cached = self._snapshot
        if cached and cached[0] == version:
            return cached[1]
        settings = self.settings_provider()
        tags = question_tags(settings)
        area_hash = taxonomy_hash(settings["areas"])
        sessions = {}
        if self.ledger is None:
            return sessions
        try:
            rows, terminal = self.ledger.session_labels()
            recorded = self.ledger.openers()
        except sqlite3.Error:
            return sessions

        def usable(row):
            if row["question"] == "area":
                # Current tag, or a pre-versioning label for the same areas.
                return row["taxonomy"] in (tags["area"], area_hash)
            return True

        # A recorded opener replaces other turns' labels only once it has its own outcome, so a
        # session whose opener moved but was never relabeled (e.g. past the backfill horizon)
        # keeps its earlier labels instead of losing them.
        opener_labeled = {
            (row["session_key"], row["question"]) for row in rows
            if row["question"] != "correction" and usable(row)
            and (recorded.get(row["session_key"]) or (None,))[0] == row["turn_key"]
        } | {  # A permanently failed opener is settled too: it reads Unclear, not the fallback.
            (row["session_key"], row["question"]) for row in terminal
            if row["question"] != "correction"
            and (recorded.get(row["session_key"]) or (None,))[0] == row["turn_key"]
        }

        def superseded(row):
            opener = recorded.get(row["session_key"])
            return (row["question"] != "correction" and opener is not None
                    and row["turn_key"] != opener[0]
                    and (row["session_key"], row["question"]) in opener_labeled)

        def cutoff(question):
            return UNCLEAR_BY_QUESTION.get(question, UNCLEAR_CONFIDENCE)

        best, turns = {}, {}
        for row in rows:
            if not usable(row) or superseded(row):
                continue
            current = row["taxonomy"] == tags.get(row["question"])
            key = (row["session_key"], row["question"]) if row["question"] != "correction" else row["turn_key"]
            target = turns if row["question"] == "correction" else best
            rank = (current, int(row["labeled_at"] or 0))
            if key not in target or rank >= target[key][0]:
                target[key] = (rank, row)
        openers = {row["turn_key"] for (_rank, row) in best.values() if row["question"] == "work_type"}
        openers.update(turn_key for turn_key, _follow_ups in recorded.values())
        for (_session, question), (_current, row) in best.items():
            entry = sessions.setdefault(row["session_key"], {})
            unclear = float(row["confidence"]) < cutoff(question)
            if question == "area":
                entry["area"] = "Unclear" if unclear else row["value"]
            elif question == "complexity":
                entry["complexity"] = row["value"]
            elif question == "work_type":
                entry["work_type"] = "unclear" if unclear else row["value"]
        turn_values = {}
        for turn_key, (_current, row) in turns.items():
            if turn_key in openers:
                continue  # A stale correction on what is now the session's opening request.
            entry = sessions.setdefault(row["session_key"], {})
            unclear = float(row["confidence"]) < cutoff("correction")
            entry["correction_labels"] = entry.get("correction_labels", 0) + 1
            turn_values[turn_key] = None if unclear else row["value"] == "True"
            if row["value"] == "True" and not unclear:
                entry["corrections"] = entry.get("corrections", 0) + 1
        for row in terminal:
            entry = sessions.setdefault(row["session_key"], {})
            if row["question"] == "correction":
                if row["turn_key"] not in turns:
                    entry["correction_labels"] = entry.get("correction_labels", 0) + 1
                    turn_values[row["turn_key"]] = None
            elif row["question"] in ("area", "work_type") and not superseded(row):
                entry.setdefault(row["question"], "Unclear" if row["question"] == "area" else "unclear")
        for session_key, entry in sessions.items():
            if session_key in recorded:
                entry["follow_ups"] = recorded[session_key][1]
        self._turn_values = turn_values
        self._snapshot = (version, sessions, turn_values)
        return sessions

    def pending_session_keys(self):
        """Sessions with never-labeled work still queued or backlogged (content-free).

        Relabeling under a newer prompt version does not make a session pending: its
        existing labels keep classifying it until they are replaced.
        """
        with self.lock:
            queued = [(item.session_key, item.turn_key, item.questions) for item in self.queue]
        # Outcomes depend only on follow-up pushback labels, so only those make a session pending.
        keys = {session for session, turn, questions in queued
                if "correction" in questions and (turn, "correction") not in self._labeled}
        if self.ledger is not None:
            labeled = self.snapshot()
            try:
                with self.ledger._connect() as connection:
                    keys.update(r["session_key"] for r in connection.execute("SELECT session_key FROM work_backlog")
                                if not labeled.get(r["session_key"], {}).get("correction_labels"))
            except sqlite3.Error:
                pass
        return keys

    def session_corrections(self, row_id, count):
        """Ordered (ordinal, pushback) pairs for a session's labeled follow-up turns.

        ``pushback`` is True, False, or None when the label was low-confidence.
        """
        self.snapshot()
        cached_snapshot = self._snapshot
        version = cached_snapshot[0] if cached_snapshot else None
        values = cached_snapshot[2] if cached_snapshot else {}
        memo_key = (row_id, int(count))
        if self._corrections_memo[0] != version:
            self._corrections_memo = (version, {})
        cached = self._corrections_memo[1].get(memo_key)
        if cached is not None:
            return cached
        with self.lock:
            opener_key = (self._openers.get(self.session_key(row_id)) or (None,))[0]
        found = []
        for ordinal in range(max(0, int(count))):
            key = self._turn_key(row_id, ordinal)
            if key == opener_key:
                found = []  # Labels on turns before the current opener are no longer follow-ups.
            elif ordinal and key in values:
                found.append((ordinal, values[key]))
        self._corrections_memo[1][memo_key] = found
        return found

    def clear(self):
        with self.write_lock:
            with self.lock:
                self.generation += 1
                self.queue.clear()
                self.queued.clear()
                self._labeled, self._failures = {}, {}
                self._openers = {}
                self._recent_added.clear()
                self.labels_version += 1
            self.model_digest = ""
            failed = False
            self._turn_values = {}
            self._corrections_memo = (None, {})
            # Record the request in memory first; the content-free marker makes it survive a
            # restart when the directory is writable.
            self._delete_requested = True
            self.ledger = None
            try:
                with open(self._delete_marker, "w", encoding="utf-8"):
                    pass
            except OSError:
                pass
            try:
                LabelLedger.remove(self.ledger_path)
                _remove_if_present(self._delete_marker)
                self._delete_requested = False
            except OSError:
                self._set(STATE_STORAGE, "delete_pending", STORAGE_RETRY_S)
                failed = True
            if not failed:
                try:
                    self.ledger = LabelLedger(self.ledger_path)
                    self._set(STATE_IDLE)
                except (sqlite3.Error, OSError):
                    # The labels are gone; only reopening failed, which step() retries.
                    self._set(STATE_STORAGE, "ledger_unavailable", STORAGE_RETRY_S)
            with self.lock:
                # Fence again after the salt rotated: intake that began mid-clear is discarded.
                self.generation += 1
                self.queue.clear()
                self.queued.clear()
        if failed:
            raise LabelDeleteError("delete_failed")

    def settings_changed(self):
        settings = self.settings_provider()
        with self.lock:
            self.labels_version += 1
            if not settings["enabled"]:
                self.queue.clear()
                self.queued.clear()
        self.wake.set()


def _remove_if_present(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def _default_load_probe():
    try:
        return os.getloadavg()[0] / max(1, os.cpu_count() or 1)
    except (AttributeError, OSError):
        return None
