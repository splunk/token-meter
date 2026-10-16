"""Write one synthetic, content-free Codex session so CI installs index real data."""

import json
from pathlib import Path


ROWS = [
    {"timestamp": "2026-01-02T10:00:00Z", "type": "session_meta",
     "payload": {"id": "ci-sample-session", "cwd": "/ci/project", "model_provider": "openai"}},
    {"timestamp": "2026-01-02T10:00:01Z", "type": "turn_context",
     "payload": {"model": "gpt-5.5", "cwd": "/ci/project"}},
    {"timestamp": "2026-01-02T10:00:02Z", "type": "event_msg",
     "payload": {"type": "task_started"}},
    {"timestamp": "2026-01-02T10:00:03Z", "type": "event_msg",
     "payload": {"type": "token_count", "info": {"last_token_usage": {
         "input_tokens": 1200, "output_tokens": 80, "cached_input_tokens": 400}}}},
    {"timestamp": "2026-01-02T10:00:05Z", "type": "event_msg",
     "payload": {"type": "task_complete", "duration_ms": 3000}},
]


def main():
    folder = Path.home() / ".codex" / "sessions" / "2026" / "01" / "02"
    folder.mkdir(parents=True, exist_ok=True)
    trace = folder / "rollout-ci-sample-session.jsonl"
    trace.write_text("".join(json.dumps(row) + "\n" for row in ROWS), encoding="utf-8")
    print(f"Wrote {trace}")


if __name__ == "__main__":
    main()
