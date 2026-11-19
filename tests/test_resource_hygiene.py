"""Long-running server resource hygiene: closed SQLite handles, surviving watchers, fd headroom.

On Python 3.12+ a sqlite3.Connection that is never closed keeps its file descriptor
until cyclic garbage collection, so ``with sqlite3.connect(...)`` (which commits but
does not close) lets a busy server exhaust launchd's 256-descriptor limit. Then
SQLite reports "unable to open database file" and HTTP clients see connection resets.
"""

import contextlib
import os
import pathlib
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

from token_meter.quotas import cursor as cursor_quotas
from token_meter.quotas.base import QuotaUnavailable
from token_meter.runtimes import hermes as hermes_runtime
from token_meter.services import work_insights
from token_meter.web import server as web_server

ROOT = pathlib.Path(__file__).resolve().parents[1]


class _ConnectionTracker:
    """Wrap sqlite3.connect so a test can assert every opened connection was closed."""

    def __init__(self):
        self.opened = []
        self._real = sqlite3.connect

    def __call__(self, *args, **kwargs):
        connection = self._real(*args, **kwargs)
        self.opened.append(connection)
        return connection

    def unclosed(self):
        count = 0
        for connection in self.opened:
            try:
                connection.execute("SELECT 1")
            except sqlite3.ProgrammingError:
                continue
            count += 1
        return count


@contextlib.contextmanager
def tracked_connections(module):
    tracker = _ConnectionTracker()
    with mock.patch.object(module.sqlite3, "connect", tracker):
        yield tracker


class LabelLedgerClosesConnections(unittest.TestCase):
    def test_every_ledger_operation_closes_its_connection(self):
        with tempfile.TemporaryDirectory() as root:
            with tracked_connections(work_insights) as tracker:
                ledger = work_insights.LabelLedger(os.path.join(root, "work.sqlite3"))
                ledger.record_label("t1", "work_type", "s1", "feature", 0.9, "tax", "model", 10)
                ledger.record_failure("t2", "work_type", "s1", "timeout", 10)
                ledger.labeled_keys()
                ledger.session_labels()
                ledger.record_opener("s1", "t1", 2)
                ledger.openers()
                ledger.model_mix()
                ledger.upsert_backlog("s1", 10, 3, 0)
                ledger.next_backlog(5, 20)
                ledger.backlog_pending()
                ledger.backlog_rows(["s1"])
                ledger.remove_backlog("s1")
                ledger.clear()
            self.assertGreater(len(tracker.opened), 10)
            self.assertEqual(tracker.unclosed(), 0)

    def test_ledger_writes_still_commit_and_failures_roll_back(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "work.sqlite3")
            ledger = work_insights.LabelLedger(path)
            ledger.record_label("t1", "work_type", "s1", "feature", 0.9, "tax", "model", 10)
            with self.assertRaises(sqlite3.Error):
                with ledger._connect() as connection:
                    connection.execute("DELETE FROM work_labels")
                    connection.execute("SELECT * FROM no_such_table")
            reopened = work_insights.LabelLedger(path)
            labels, _failures = reopened.labeled_keys()
            self.assertIn(("t1", "work_type"), labels)


class ReadOnlyReadersCloseConnections(unittest.TestCase):
    def test_hermes_reader_closes_its_connections(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "state.db")
            with contextlib.closing(sqlite3.connect(path)) as seed:
                seed.execute("CREATE TABLE sessions (id TEXT, model TEXT)")
                seed.execute("CREATE TABLE session_model_usage (session_id TEXT, model TEXT)")
                seed.execute("INSERT INTO sessions VALUES ('a', 'm')")
                seed.commit()
            adapter = hermes_runtime.HermesRuntimeAdapter(path)
            with tracked_connections(hermes_runtime) as tracker:
                adapter._columns()
                adapter._rows()
                adapter._model_usage_rows("a")
            self.assertGreaterEqual(len(tracker.opened), 3)
            self.assertEqual(tracker.unclosed(), 0)

    def test_cursor_quota_auth_closes_its_connection(self):
        opened = []

        def factory():
            connection = sqlite3.connect(":memory:")
            connection.execute("CREATE TABLE ItemTable (key TEXT, value TEXT)")
            opened.append(connection)
            return connection

        with self.assertRaises(QuotaUnavailable):
            cursor_quotas.auth_session(factory, (sqlite3.Error,))
        self.assertEqual(len(opened), 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            opened[0].execute("SELECT 1")


_SCENARIO = r"""
import gc, http.server, os, resource, socket, sys, threading
sys.path.insert(0, sys.argv[1])
from token_meter.services.work_insights import LabelLedger
# The reported outage: a launchd-sized descriptor budget and a busy ledger between GC passes.
# Unclosed connections fail here first ("unable to open database file"); the request proves the
# server still has descriptors left to answer.
resource.setrlimit(resource.RLIMIT_NOFILE, (64, resource.getrlimit(resource.RLIMIT_NOFILE)[1]))
gc.disable()
ledger = LabelLedger(os.path.join(sys.argv[2], "work.sqlite3"))
for index in range(300):
    ledger.record_label("t%d" % index, "work_type", "s", "feature", 0.9, "tax", "model", 1)
    ledger.backlog_pending()

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        ledger.backlog_pending()
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
    def log_message(self, *args): pass

server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
with socket.create_connection(server.server_address, timeout=5) as client:
    client.sendall(b"GET / HTTP/1.0\r\n\r\n")
    print(client.recv(64).split(b"\r\n")[0].decode())
"""


@unittest.skipIf(os.name != "posix", "descriptor limits are POSIX")
class DescriptorExhaustionScenario(unittest.TestCase):
    def test_busy_ledger_under_launchd_limit_keeps_writing_and_serving(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as root:
            result = subprocess.run(
                [sys.executable, "-c", _SCENARIO, str(ROOT), root],
                capture_output=True, text=True, timeout=60,
            )
        self.assertEqual(result.returncode, 0, result.stderr[-400:])
        self.assertIn("200", result.stdout)


class BackgroundSupervision(unittest.TestCase):
    def test_crashed_background_target_is_restarted(self):
        calls = []
        done = threading.Event()

        def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise sqlite3.OperationalError("unable to open database file")
            done.set()

        logged = []
        thread = threading.Thread(
            target=web_server.supervise,
            args=(flaky,),
            kwargs={"retry_s": 0.01, "log": logged.append},
            daemon=True,
        )
        thread.start()
        self.assertTrue(done.wait(5))
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(logged), 1)
        self.assertIn("flaky", logged[0])
        self.assertIn("OperationalError", logged[0])
        self.assertNotIn("unable to open", logged[0])

    def test_repeated_failures_back_off_and_log_once_per_exception_class(self):
        clock = {"now": 0.0}
        sleeps, logged = [], []
        failures = [OSError, OSError, OSError, ValueError, OSError]

        def failing():
            if not failures:
                return
            clock["now"] += 1
            raise failures.pop(0)()

        web_server.supervise(
            failing, retry_s=30, max_retry_s=120, log=logged.append,
            sleep=sleeps.append, clock=lambda: clock["now"],
        )
        self.assertEqual(sleeps, [30, 60, 120, 120, 120])
        self.assertEqual([line.split(" stopped on ")[1].split(";")[0] for line in logged],
                         ["OSError", "ValueError", "OSError"])

    def test_a_long_healthy_run_resets_the_backoff(self):
        clock = {"now": 0.0}
        sleeps = []
        runs = [1, 1, 500, 1]

        def failing():
            if not runs:
                return
            clock["now"] += runs.pop(0)
            raise OSError()

        web_server.supervise(
            failing, retry_s=30, max_retry_s=120, log=lambda line: None,
            sleep=sleeps.append, clock=lambda: clock["now"],
        )
        self.assertEqual(sleeps, [30, 60, 30, 60])


class DescriptorHeadroom(unittest.TestCase):
    # A fake resource module keeps these checks identical on POSIX and Windows hosts.
    def test_no_resource_module_is_a_no_op(self):
        with mock.patch.object(web_server, "resource", None):
            web_server.raise_descriptor_limit()

    def test_serve_local_supervises_background_targets(self):
        started = []
        server = mock.Mock()
        server.serve_forever.side_effect = KeyboardInterrupt
        with mock.patch.object(web_server, "resource", None), \
                mock.patch.object(web_server.threading, "Thread") as thread:
            web_server.serve_local(
                handler_class=object, server_class=lambda *args: server, port=0,
                background=(started.append,), output=lambda message: None,
            )
        self.assertIs(thread.call_args.kwargs["target"], web_server.supervise)
        thread.return_value.start.assert_called_once_with()

    def test_raises_a_low_soft_limit_within_the_hard_limit(self):
        calls = []
        limits = {"value": (256, 10240)}
        fake = mock.Mock(
            RLIMIT_NOFILE=7,
            RLIM_INFINITY=-1,
            getrlimit=lambda kind: limits["value"],
            setrlimit=lambda kind, value: calls.append((kind, value)),
        )
        with mock.patch.object(web_server, "resource", fake):
            web_server.raise_descriptor_limit()
            limits["value"] = (256, -1)
            web_server.raise_descriptor_limit()
            limits["value"] = (256, 1024)
            web_server.raise_descriptor_limit()
            limits["value"] = (8192, 10240)
            web_server.raise_descriptor_limit()
        self.assertEqual(calls, [
            (7, (web_server.DESCRIPTOR_TARGET, 10240)),
            (7, (web_server.DESCRIPTOR_TARGET, -1)),
            (7, (1024, 1024)),
        ])

    def test_limit_failures_are_ignored(self):
        fake = mock.Mock(
            RLIMIT_NOFILE=7,
            RLIM_INFINITY=-1,
            getrlimit=lambda kind: (256, 10240),
            setrlimit=mock.Mock(side_effect=ValueError("nope")),
        )
        with mock.patch.object(web_server, "resource", fake):
            web_server.raise_descriptor_limit()


class WorkServiceWiring(unittest.TestCase):
    def test_a_failed_git_ledger_open_leaves_labels_off_and_is_retried(self):
        import meter
        git = mock.Mock()
        with tempfile.TemporaryDirectory() as root, \
                mock.patch.object(meter, "TOKEN_METER_WORK_INSIGHTS_DB", os.path.join(root, "work.sqlite3")), \
                mock.patch.object(meter, "_work_service_instance", None), \
                mock.patch.object(meter, "_work_git_wiring_retry_at", 0.0), \
                mock.patch.object(meter, "git_delivery_service",
                                  side_effect=[sqlite3.OperationalError("unable to open database file"), git]) as opener:
            service = meter.work_insights_service()
            self.assertIsNone(service.commit_label_sink)
            self.assertIs(meter.work_insights_service(), service)
            self.assertEqual(opener.call_count, 1)  # retry waits out the pause
            meter._work_git_wiring_retry_at = 0.0
            self.assertIs(meter.work_insights_service(), service)
            self.assertIs(service.commit_label_sink, git.ledger.record_commit_label)
            self.assertIs(git.commit_classifier, service)
            meter.work_insights_service()
            self.assertEqual(opener.call_count, 2)  # wired once, never reopened


if __name__ == "__main__":
    unittest.main()
