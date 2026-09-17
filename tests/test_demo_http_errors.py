"""Offline non-200 Responses boundaries. No real credentials or API calls."""
import json
import unittest
from datetime import datetime, timezone
import httpx
from sanzo_demo.ai import ResponsesConfig, ResponsesExtractor, ExtractionError
from sanzo_demo.http_errors import read_http_error

SHA = "a" * 64
SCHEMA = {"type":"object","properties":{},"required":[],"additionalProperties":False}

class HTTPDiagnosticsTests(unittest.TestCase):
    def run_error(self, body, headers=None, status=429, limit=4096):
        calls=[]
        def handler(request):
            calls.append(1)
            return httpx.Response(status,content=body,headers=headers or {})
        client=ResponsesExtractor(ResponsesConfig("approved-test-model",frozenset({SHA}),max_requests=1,max_response_bytes=limit),
            lambda:"offline-placeholder",transport=httpx.MockTransport(handler))
        with self.assertRaises(ExtractionError) as caught:
            client.extract(SHA,[{"text":"synthetic only"}],SCHEMA)
        self.assertEqual(caught.exception.code,"AI_HTTP_"+str(status))
        self.assertEqual(calls,[1])
        self.assertEqual(client.requests_used,1)
        with self.assertRaises(ExtractionError) as exhausted:
            client.extract(SHA,[{"text":"synthetic only"}],SCHEMA)
        self.assertEqual(exhausted.exception.code,"AI_REQUEST_BUDGET_EXHAUSTED")
        self.assertEqual(calls,[1])
        return caught.exception.metadata

    def test_rate_limit_and_cooldown(self):
        m=self.run_error(json.dumps({"error":{"code":"rate_limit_exceeded","type":"rate_limit_error","message":"NEVER_LOG_THIS_QUOTATION"}}).encode(),
            {"retry-after":"3","x-request-id":"req_offline_429","authorization":"NEVER_LOG_HEADER"})
        self.assertEqual(m["failure_category"],"TEMPORARY_RATE_LIMIT")
        self.assertEqual(m["provider_error_code"],"rate_limit_exceeded")
        self.assertEqual(m["retry_after_seconds"],3)
        self.assertIn("retry_not_before",m)
        self.assertEqual(m["request_id"],"req_offline_429")
        self.assertNotIn("NEVER_LOG",repr(m))
        self.assertEqual(m["automatic_retries"],0)

    def test_account_quota(self):
        for code in ("insufficient_quota","billing_hard_limit_reached"):
            with self.subTest(code=code):
                m=self.run_error(json.dumps({"error":{"code":code,"type":"insufficient_quota"}}).encode())
                self.assertEqual(m["failure_category"],"ACCOUNT_QUOTA")

    def test_unknown_never_guesses_from_message_or_retry_header(self):
        for body in (b"not JSON",b'{"error":{"message":"rate limit quota credits"}}',
                     b'{"error":{"code":"new_vendor_error","type":"new_type"}}'):
            with self.subTest(body=body):
                m=self.run_error(body,{"retry-after":"2"})
                self.assertEqual(m["failure_category"],"UNKNOWN_429")

    def test_untrusted_codes_and_types_are_not_echoed(self):
        marker="sk-proj-DO_NOT_ECHO_SECRET"
        m=self.run_error(json.dumps({"error":{"code":marker,"type":"customer quotation","message":marker}}).encode())
        self.assertNotIn(marker,repr(m))
        self.assertNotIn("customer quotation",repr(m))
        self.assertEqual(m["failure_category"],"UNKNOWN_429")

    def test_conflicting_diagnostics_are_unknown(self):
        m=self.run_error(b'{"error":{"code":"rate_limit_exceeded","type":"insufficient_quota"}}')
        self.assertEqual(m["failure_category"],"UNKNOWN_429")

    def test_oversized_response_preserves_http_error_without_body(self):
        m=self.run_error(b'{"error":{"code":"insufficient_quota","message":"'+b"x"*3000+b'"}}',limit=100)
        self.assertEqual(m["error_body_status"],"TOO_LARGE")
        self.assertEqual(m["failure_category"],"UNKNOWN_429")

    def test_duplicate_json_does_not_choose_one_reason(self):
        m=self.run_error(b'{"error":{"code":"insufficient_quota","code":"rate_limit_exceeded"}}')
        self.assertEqual(m["failure_category"],"UNKNOWN_429")

    def test_other_http_status_remains_http_error(self):
        m=self.run_error(b'{"error":{"code":"model_not_found","type":"invalid_request_error"}}',status=404)
        self.assertEqual(m["failure_category"],"HTTP_ERROR")
        self.assertEqual(m["provider_error_code"],"model_not_found")

    def test_retry_after_http_date_and_invalid_values(self):
        now=datetime(2026,9,11,10,0,0,tzinfo=timezone.utc)
        for value,seconds in [("Fri, 11 Sep 2026 10:00:05 GMT",5),("0",0),("-1",None),("NaN",None),("1.5",None),("Fri, 11 Sep 2026 09:00:00 GMT",None),("junk",None)]:
            with self.subTest(value=value):
                r=httpx.Response(429,content=b'{}',headers={"retry-after":value})
                m=read_http_error(r,4096,now=now)
                self.assertEqual(m.get("retry_after_seconds"),seconds)

    def test_rate_headers_only_derive_wait_for_exhausted_limit(self):
        body=b'{"error":{"code":"rate_limit_exceeded"}}'
        m=self.run_error(body,{"x-ratelimit-remaining-tokens":"0","x-ratelimit-reset-tokens":"1m2s","x-ratelimit-limit-tokens":"1000"})
        self.assertEqual(m["retry_after_seconds"],62)
        m=self.run_error(body,{"x-ratelimit-remaining-tokens":"50","x-ratelimit-reset-tokens":"1m2s"})
        self.assertNotIn("retry_after_seconds",m)

    def test_malicious_headers_ignored(self):
        m=self.run_error(b'{}',{"retry-after":"DO_NOT_ECHO","x-ratelimit-reset-tokens":"SECRET","x-ratelimit-limit-tokens":"NaN"})
        self.assertNotIn("DO_NOT_ECHO",repr(m));self.assertNotIn("SECRET",repr(m))
