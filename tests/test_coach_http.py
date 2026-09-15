import http.client
import json
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

import meter


class FakeCoachService:
    def __init__(self):
        self.calls = []
        self.cancel_calls = 0

    def state(self):
        self.calls.append(("state", None))
        return {
            "ok": True,
            "agent": {"available": True, "status": "ready"},
            "goal": None,
            "current": None,
            "progress": None,
            "weekly": {},
            "next_review_at": None,
        }

    def ask(self, payload):
        self.calls.append(("ask", payload))
        return {
            "ok": True,
            "reply": {
                "message": "Use the Efficiency view.",
                "evidence": [],
                "navigation": {"route": "efficiency", "label": "Open Efficiency"},
                "goal_draft": None,
            },
        }

    def cancel(self):
        self.cancel_calls += 1
        self.calls.append(("cancel", None))
        return {"ok": True, "changed": self.cancel_calls == 1}

    def save_goal(self, value):
        self.calls.append(("save_goal", value))
        return {"ok": True, "changed": True, "goal": value}

    def set_weekly_enabled(self, enabled):
        self.calls.append(("weekly_enabled", enabled))
        return {"ok": True, "changed": True, "goal": {"weekly_enabled": enabled}}

    def clear_goal(self):
        self.calls.append(("clear", None))
        return {"ok": True, "changed": True, "goal": None}

    def run_weekly(self, manual=False):
        self.calls.append(("run_weekly", manual))
        return {"ok": True, "ran": True, "recommendation": "keep_course"}


class CoachHttpContractTests(unittest.TestCase):
    def setUp(self):
        self.service = FakeCoachService()

    def request(self, method, path, payload=None, headers=None, raw_body=None):
        server = meter.TokenMeterHTTPServer(("127.0.0.1", 0), meter.H)
        server.timeout = 1
        worker = threading.Thread(target=server.handle_request, daemon=True)
        worker.start()
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=2,
        )
        body = raw_body if raw_body is not None else (
            json.dumps(payload) if payload is not None else None
        )
        request_headers = dict(headers or {})
        if body is not None:
            request_headers.setdefault("Content-Type", "application/json")
            request_headers.setdefault("Content-Length", str(len(body.encode("utf-8"))))
        try:
            with mock.patch.object(meter, "coach_service", return_value=self.service, create=True):
                connection.request(method, path, body=body, headers=request_headers)
                response = connection.getresponse()
                return response.status, response.read().decode("utf-8")
        finally:
            connection.close()
            worker.join(timeout=2)
            server.server_close()

    def action_headers(self):
        return {"X-Token-Meter-Action": meter._ACTION_TOKEN}

    def test_state_returns_action_token_only_on_the_dashboard_transport(self):
        meter._coach_wake.clear()
        status, body = self.request("GET", "/coach/state")

        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(payload["actions"], {"token": meter._ACTION_TOKEN})
        self.assertNotIn("actions", self.service.state())
        self.assertTrue(meter._coach_wake.is_set())

    def test_chat_goal_and_weekly_routes_dispatch_bounded_actions(self):
        goal = {
            "metric": "cost_per_execution", "target_percent": 20,
            "window_days": 7, "runtime": "codex", "review_weekday": 0,
            "weekly_enabled": True,
        }
        cases = (
            ("/coach/ask", {
                "message": "Where can I save tokens?", "history": [],
                "page": {"route": "efficiency"},
            }, "ask"),
            ("/coach/goal", {"action": "activate", "goal": goal}, "save_goal"),
            ("/coach/goal", {"action": "weekly", "enabled": False}, "weekly_enabled"),
            ("/coach/goal", {"action": "clear"}, "clear"),
            ("/coach/weekly", {"manual": True}, "run_weekly"),
        )

        for path, payload, expected_call in cases:
            with self.subTest(path=path, action=payload.get("action")):
                status, body = self.request(
                    "POST", path, payload, headers=self.action_headers(),
                )
                self.assertEqual(status, 200, body)
                self.assertTrue(json.loads(body)["ok"])
                self.assertEqual(self.service.calls[-1][0], expected_call)

    def test_cancel_route_stops_only_active_coach_run(self):
        status, body = self.request(
            "POST", "/coach/cancel", {}, headers=self.action_headers(),
        )

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"ok": True, "changed": True})
        self.assertEqual(self.service.calls[-1], ("cancel", None))

        status, body = self.request(
            "POST", "/coach/cancel", {}, headers=self.action_headers(),
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"ok": True, "changed": False})
        self.assertEqual(self.service.cancel_calls, 2)

    def test_cancel_route_keeps_all_protected_post_guards(self):
        cases = (
            ({}, {}, None, 403),
            ({**self.action_headers(), "Origin": "https://example.com"}, {}, None, 403),
            ({**self.action_headers(), "Content-Type": "text/plain"}, None, "{}", 415),
            (self.action_headers(), None, "{bad", 400),
            (self.action_headers(), None, "x" * 8_193, 400),
        )

        for headers, payload, raw, expected in cases:
            with self.subTest(expected=expected, headers=headers):
                status, _body = self.request(
                    "POST", "/coach/cancel", payload, headers=headers, raw_body=raw,
                )
            self.assertEqual(status, expected)
            self.assertEqual(self.service.calls, [])

    def test_coach_evidence_route_uses_warm_service_and_existing_guards(self):
        agent_api = mock.Mock()
        agent_api.check.return_value = {
            "ok": True, "data_scope": "matched_current_run",
            "caller": {"project": "/private/tmp/coach"},
        }
        caller = {"runtime": "token-meter-coach", "project": "/private/tmp/coach"}
        with mock.patch.object(meter, "application", return_value=SimpleNamespace(agent_api=agent_api)):
            status, body = self.request(
                "POST", "/coach/evidence",
                {"tool": "check", "arguments": {"focus": "continue"}, "caller": caller},
                headers={**self.action_headers(), "X-Token-Meter-Coach-Evidence": meter._COACH_EVIDENCE_TOKEN},
            )
        self.assertEqual(status, 200)
        projection = json.loads(body)
        self.assertEqual(projection["data_scope"], "matched_current_run")
        self.assertNotIn("caller", projection)
        agent_api.check.assert_called_once_with(focus="continue", caller=caller)

        for headers, payload, status_code in (
            ({**self.action_headers(), "X-Token-Meter-Coach-Evidence": meter._COACH_EVIDENCE_TOKEN}, {"tool": "trace", "arguments": {"session_id": "x"}}, 400),
            ({**self.action_headers(), "X-Token-Meter-Coach-Evidence": meter._COACH_EVIDENCE_TOKEN}, {"tool": "usage", "arguments": {"window": "7d", "secret": True}}, 400),
            ({}, {"tool": "usage", "arguments": {}}, 403),
        ):
            with self.subTest(payload=payload):
                status, _body = self.request("POST", "/coach/evidence", payload, headers=headers)
                self.assertEqual(status, status_code)

    def test_coach_evidence_rejects_preflight_failures_before_any_dispatch(self):
        valid = {"tool": "usage", "arguments": {"window": "7d", "focus": "spend"}}
        cases = (
            ({**self.action_headers(), "Origin": "https://example.com"}, valid, None, 403),
            ({**self.action_headers(), "Content-Type": "text/plain"}, None, "hello", 415),
            (self.action_headers(), None, "{bad", 400),
            (self.action_headers(), None, "x" * 8_193, 400),
        )

        for headers, payload, raw, expected in cases:
            with self.subTest(expected=expected, headers=headers), mock.patch.object(
                meter, "application"
            ) as application:
                status, _body = self.request(
                    "POST", "/coach/evidence", payload, headers=headers, raw_body=raw,
                )
            self.assertEqual(status, expected)
            application.assert_not_called()
            self.assertEqual(self.service.calls, [])

    def test_coach_posts_keep_origin_content_type_token_and_size_guards(self):
        valid = {"message": "hello", "history": [], "page": {"route": "sessions"}}
        cases = (
            ({}, valid, None, 403),
            ({**self.action_headers(), "Origin": "https://example.com"}, valid, None, 403),
            ({**self.action_headers(), "Content-Type": "text/plain"}, None, "hello", 415),
            (self.action_headers(), None, "", 400),
            (self.action_headers(), None, "{bad", 400),
            (self.action_headers(), None, "x" * 65_537, 400),
        )

        for headers, payload, raw, expected in cases:
            with self.subTest(expected=expected, headers=headers):
                status, _body = self.request(
                    "POST", "/coach/ask", payload, headers=headers, raw_body=raw,
                )
                self.assertEqual(status, expected)

    def test_chat_transport_accepts_the_full_bounded_six_turn_history(self):
        payload = {
            "message": "m" * 2_000,
            "history": [
                {"role": "user" if index % 2 == 0 else "assistant", "content": "h" * 2_000}
                for index in range(6)
            ],
            "page": {"route": "efficiency"},
        }

        status, body = self.request(
            "POST", "/coach/ask", payload, headers=self.action_headers(),
        )

        self.assertEqual(status, 200, body)
        self.assertEqual(self.service.calls[-1], ("ask", payload))

    def test_non_chat_coach_posts_keep_the_smaller_body_limit(self):
        status, _body = self.request(
            "POST", "/coach/goal", headers=self.action_headers(),
            raw_body="x" * 8_193,
        )

        self.assertEqual(status, 400)

    def test_unknown_coach_route_is_not_accepted(self):
        status, _body = self.request(
            "POST", "/coach/unknown", {}, headers=self.action_headers(),
        )
        self.assertEqual(status, 404)

    def test_user_facing_agent_errors_name_tok(self):
        expected = {
            "agent_failed": "Codex could not complete Tok's request.",
            "busy": "Tok is already working on another request.",
            "cancelled": "Tok stopped this request.",
            "cli_missing": "Install the Codex CLI to use Tok.",
            "invalid_output": "Codex returned an answer outside Tok's contract.",
            "invalid_request": "Tok could not understand that request.",
            "output_too_large": "The Codex answer exceeded Tok's response limit.",
            "timeout": "Codex did not finish Tok's request in time.",
        }

        for code, message in expected.items():
            with self.subTest(code=code):
                self.assertEqual(meter.coach_http_error({"error_code": code})["error"], message)


if __name__ == "__main__":
    unittest.main()
