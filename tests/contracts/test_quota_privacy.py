import io
import unittest
from urllib.error import HTTPError

from token_meter.quotas.base import QuotaUnavailable
from token_meter.quotas.common import MAX_RESPONSE_BYTES, quota_http_json, quota_provider


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, size=-1):
        return self.payload[:size] if size >= 0 else self.payload


class QuotaPrivacyTests(unittest.TestCase):
    def test_http_error_does_not_expose_url_headers_or_response_body(self):
        sentinels = ("secret-token", "/Users/private/quota", "raw-provider-body")
        errors = []

        def opener(request, timeout):
            error = HTTPError(
                request.full_url,
                500,
                "raw-provider-body",
                hdrs=None,
                fp=io.BytesIO(b"/Users/private/quota secret-token"),
            )
            errors.append(error)
            raise error

        with self.assertRaises(QuotaUnavailable) as raised:
            quota_http_json(
                "https://provider.invalid/raw-provider-body",
                headers={"Authorization": "Bearer secret-token"},
                opener=opener,
            )

        message = str(raised.exception)
        self.assertEqual(message, "Provider quota request failed (HTTP 500).")
        for sentinel in sentinels:
            self.assertNotIn(sentinel, message)
        self.assertTrue(errors[0].closed)

    def test_http_response_is_bounded_before_json_parsing(self):
        oversized = b"{" + (b"x" * MAX_RESPONSE_BYTES) + b"}"

        with self.assertRaisesRegex(QuotaUnavailable, "response was too large"):
            quota_http_json(
                "https://provider.invalid/quota",
                opener=lambda request, timeout: _Response(oversized),
            )

    def test_redirects_fail_closed_without_following(self):
        from token_meter.quotas.common import _NoRedirectHandler

        handler = _NoRedirectHandler()
        with self.assertRaises(QuotaUnavailable) as raised:
            handler.redirect_request(None, None, 302, "Found", {}, "https://evil.example/x")
        self.assertEqual(str(raised.exception), "Provider quota request redirected.")
        self.assertNotIn("evil.example", str(raised.exception))

    def test_unavailable_quota_is_not_reported_as_measured_zero(self):
        snapshot = quota_provider(
            "provider",
            "Provider",
            "unavailable",
            "local-auth",
            windows=[],
            error="Provider quota access is unavailable.",
        )

        self.assertEqual(snapshot["status"], "unavailable")
        self.assertEqual(snapshot["provenance"], "unavailable")
        self.assertEqual(snapshot["windows"], [])
        self.assertNotIn("used_percent", snapshot)


if __name__ == "__main__":
    unittest.main()
