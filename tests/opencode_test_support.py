"""Shared setup and helpers for the OpenCodeTests split."""

import unittest
import json
import sqlite3
import time
from pathlib import Path
from unittest import mock
import meter


class OpenCodeCase(unittest.TestCase):
    """OpenCode is discovered from its read-only SQLite database."""

    def _build_db(self, root):
        db_path = Path(root) / "opencode.db"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE session (id TEXT PRIMARY KEY, project_id TEXT, parent_id TEXT, "
                     "slug TEXT, directory TEXT, title TEXT, version TEXT, time_created INTEGER, "
                     "time_updated INTEGER, time_archived INTEGER, agent TEXT, model TEXT, cost REAL, "
                     "tokens_input INTEGER, tokens_output INTEGER, tokens_reasoning INTEGER, "
                     "tokens_cache_read INTEGER, tokens_cache_write INTEGER)")
        conn.execute("CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, "
                     "time_updated INTEGER, data TEXT)")
        conn.execute("CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, "
                     "time_created INTEGER, time_updated INTEGER, data TEXT)")
        conn.execute("CREATE TABLE todo (id TEXT PRIMARY KEY, session_id TEXT)")
        return conn

    def _session_row(self, sid, directory, title, model, cost, inp, out, rea, cr, cw,
                     updated, parent_id=None, archived=None, agent="build",
                     provider_id="opencode-go"):
        return {
            "id": sid, "project_id": "proj", "parent_id": parent_id, "slug": sid,
            "directory": directory, "title": title, "version": "1.18.14",
            "time_created": updated - 1000, "time_updated": updated,
            "time_archived": archived, "agent": agent,
            "model": json.dumps({"id": model, "providerID": provider_id, "variant": "high"}),
            "cost": cost, "tokens_input": inp, "tokens_output": out,
            "tokens_reasoning": rea, "tokens_cache_read": cr, "tokens_cache_write": cw,
        }

    def _message(self, mid, sid, data, created):
        return (mid, sid, created, created, json.dumps(data))

    def _pricing_state(self, root, model, provider_id, cache_read=5000,
                       prices=None, cost=0.0123, messages=True):
        root = Path(root)
        base = 1_784_548_900_000
        conn = self._build_db(root)
        row = self._session_row("ses_priced", "/repo", "Priced", model,
                                cost, 1000, 200, 0, cache_read, 0,
                                base, provider_id=provider_id)
        conn.execute("INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     tuple(row.values()))
        if messages:
            conn.execute("INSERT INTO message VALUES (?,?,?,?,?)", (
                "msg_priced", "ses_priced", base, base + 500, json.dumps({
                    "role": "assistant", "modelID": model, "providerID": provider_id,
                    "time": {"created": base, "completed": base + 500},
                    "tokens": {"input": 1000, "output": 200, "reasoning": 0,
                               "cache": {"read": cache_read, "write": 0}},
                    "cost": cost, "finish": "stop",
                }),
            ))
        conn.commit()
        conn.close()
        settings = Path(root) / "settings.json"
        settings.write_text(json.dumps({"model_pricing": prices or {}}))
        with mock.patch.object(meter, "OPENCODE_DB", str(root / "opencode.db")), \
                mock.patch.object(meter, "TOKEN_METER_SETTINGS", str(settings)), \
                mock.patch.object(meter, "_opencode_native_adapters", {}):
            source = meter.opencode_session_sources()[0]
            return meter.recompute_opencode(source)

    def _insert_sessions(self, conn, rows):
        for row in rows:
            conn.execute(
                "INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (row["id"], row["project_id"], row["parent_id"], row["slug"],
                 row["directory"], row["title"], row["version"], row["time_created"],
                 row["time_updated"], row["time_archived"], row["agent"], row["model"],
                 row["cost"], row["tokens_input"], row["tokens_output"],
                 row["tokens_reasoning"], row["tokens_cache_read"],
                 row["tokens_cache_write"]),
            )

    def _child_agent_row(self, source):
        """Return the single spawned agent record a child session emits."""
        meter._opencode_compatibility.cache_clear() \
            if hasattr(meter._opencode_compatibility, "cache_clear") else None
        row = meter.session_summary(source)
        records = row.get("_agent_records") or []
        self.assertEqual(len(records), 1, records)
        return records[0]

    def _assistant(self, conn, mid, sid, cost, created, inp=10, out=5):
        conn.execute("INSERT INTO message VALUES (?,?,?,?,?)", (
            mid, sid, created, created + 500, json.dumps({
                "role": "assistant", "modelID": "model-a", "providerID": "opencode-go",
                "time": {"created": created, "completed": created + 500},
                "tokens": {"input": inp, "output": out, "reasoning": 0,
                           "cache": {"read": 0, "write": 0}},
                "cost": cost,
            }),
        ))

    def _family_db(self, root, base, rows, messages=True):
        """Insert sessions plus one assistant message carrying each row's cost."""
        conn = self._build_db(root)
        self._insert_sessions(conn, rows)
        if messages:
            for index, row in enumerate(rows):
                self._assistant(conn, "m-" + row["id"], row["id"], row["cost"],
                                base + index)
        conn.commit()
        conn.close()
        return root / "opencode.db"

    def _cross(self, db_path, sources=None):
        saved = dict(meter._xsess)
        try:
            meter._xsess["data"], meter._xsess["at"] = None, 0
            meter._summary_cache.clear()
            with mock.patch.object(meter, "OPENCODE_DB", str(db_path)), \
                    mock.patch.object(meter, "capability_inventory", return_value={}):
                sources = meter.opencode_session_sources() if sources is None else sources
                cross = meter.cross_session(sources=sources)
                family_rows = list(meter._xsess.get("sessions") or [])
            return sources, cross, family_rows
        finally:
            meter._xsess.clear()
            meter._xsess.update(saved)
            meter._summary_cache.clear()

    def _sample_messages(self):
        base = 1_784_548_800_000
        return [
            self._message("msg1", "ses_top", {
                "role": "assistant", "agent": "build", "modelID": "deepseek-v4-pro",
                "providerID": "opencode-go",
                "tokens": {"input": 1000, "output": 200, "reasoning": 50,
                           "cache": {"write": 0, "read": 300}},
                "cost": 0.01, "finish": "tool-calls",
                "time": {"created": base, "completed": base + 5000},
            }, base),
            self._message("msg2", "ses_top", {
                "role": "assistant", "agent": "build", "modelID": "deepseek-v4-pro",
                "providerID": "opencode-go",
                "tokens": {"input": 500, "output": 100, "reasoning": 0,
                           "cache": {"write": 0, "read": 0}},
                "cost": 0.005, "finish": "turn_complete",
                "time": {"created": base + 60_000, "completed": base + 70_000},
            }, base + 60_000),
            self._message("msg3", "ses_top", {"role": "user", "text": "hi"}, base + 10_000),
        ]
