"""Shared fixtures for the server, dashboard, and runtime test modules."""

import unittest
import contextlib
import http.client
import json
import sqlite3
import tempfile
import threading
from pathlib import Path
from unittest import mock
import meter


class _TrackingSqliteConnection:
    """Wrap a real sqlite3 connection and record whether close() ever ran."""

    def __init__(self, connection):
        object.__setattr__(self, "_connection", connection)
        object.__setattr__(self, "closed", False)

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def __setattr__(self, name, value):
        setattr(self._connection, name, value)

    def close(self):
        object.__setattr__(self, "closed", True)
        return self._connection.close()

    def __enter__(self):
        self._connection.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        return self._connection.__exit__(exc_type, exc, tb)


def isolated_pricing_state():
    """Isolate module-level repricing state for tests that run the watcher.

    Another test can leave a stale pricing revision or a running recalculation;
    the watcher would then start a real background reprice or skip its
    cross-session refresh. Use as a context manager around ``meter.watcher()``.
    """
    stack = contextlib.ExitStack()
    stack.enter_context(mock.patch.dict(
        meter._pricing_state, {"cache_revision": None, "fingerprint_key": None, "fingerprint": None}))
    stack.enter_context(mock.patch.dict(
        meter._pricing_recalc, {"running": False, "pending": False, "thread": None}))
    stack.enter_context(mock.patch.object(meter, "pricing_watch_tick", return_value=False))
    return stack


class LocalApiHttpBase(unittest.TestCase):
    """A loopback server with temp settings and API token for the local API tests."""

    def setUp(self):
        meter._reset_cost_discount_cache()
        self.addCleanup(meter._reset_cost_discount_cache)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.settings = self.tmp / "settings.json"
        self.token_path = self.tmp / "api-token"
        self.server = meter.TokenMeterHTTPServer(("127.0.0.1", 0), meter.H)
        self.port = self.server.server_port
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()

        def stop():
            self.server.shutdown()
            self.server.server_close()
            thread.join(timeout=5)
        self.addCleanup(stop)
        self.scheduled = mock.Mock(return_value="scheduled")
        for patcher in (
            mock.patch.object(meter, "TOKEN_METER_SETTINGS", str(self.settings)),
            mock.patch.object(meter, "TOKEN_METER_API_TOKEN", str(self.token_path)),
            mock.patch.object(meter, "PORT", self.port),
            mock.patch.object(meter, "schedule_pricing_recalculation", self.scheduled),
            mock.patch.object(meter, "STATE", {"ok": True, "xsession": {"pricing_revision": "p_old"}}),
            mock.patch.dict(meter._pricing_recalc, {"running": False, "pending": False}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.token = meter.ensure_api_token()

    def request(self, method, path, body=None, *, bearer=True, action=False, host=None,
                origin=None, client=None, raw=None, content_type="application/json"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else b"")
        headers = {"Host": host or "127.0.0.1:{}".format(self.port)}
        if data or method == "POST":
            headers.update({"Content-Type": content_type, "Content-Length": str(len(data))})
        if bearer:
            headers["Authorization"] = "Bearer " + (bearer if isinstance(bearer, str) else self.token)
        if action:
            headers["X-Token-Meter-Action"] = meter._ACTION_TOKEN
        if origin:
            headers["Origin"] = origin
        if client is not None:
            headers["X-Token-Meter-Client"] = client
        conn.putrequest(method, path, skip_host=True)
        for key, value in headers.items():
            conn.putheader(key, value)
        conn.endheaders()
        if data:
            conn.send(data)
        response = conn.getresponse()
        text = response.read().decode()
        conn.close()
        try:
            return response.status, json.loads(text), text
        except json.JSONDecodeError:
            return response.status, None, text


_REAL_SCHEDULE = meter.schedule_pricing_recalculation
_REAL_REFRESH = meter.refresh_cross_session_state


class DiscountPricingFixture(unittest.TestCase):
    """Temp settings file with discount rules for pricing behaviour tests."""

    T0 = 1_788_000_000

    def setUp(self):
        meter._reset_cost_discount_cache()
        self.addCleanup(meter._reset_cost_discount_cache)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.path = self.tmp / "settings.json"
        patcher = mock.patch.object(meter, "TOKEN_METER_SETTINGS", str(self.path))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.rules()

    def rules(self, *rules):
        self.path.write_text(json.dumps({"cost_discounts": {"version": 1, "rules": [
            {"id": "d_rule{:04d}".format(index), "price_list": price_list, "channel": channel,
             "percent": percent, "start": start, "end": None}
            for index, (price_list, channel, percent, start) in enumerate(rules)]}}))
        meter._reset_cost_discount_cache()
