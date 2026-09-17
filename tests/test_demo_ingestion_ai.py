"""MECHANISM_TEST: fabricated HTTP envelopes; no SDK, key files, or real AI calls."""
from dataclasses import FrozenInstanceError
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
import json
import unittest

import httpx

from sanzo_demo.ai import ExtractionError, ResponsesConfig, ResponsesExtractor

SHA = "a" * 64
BLOCKS = [{"block_id": "block-1", "text": "Synthetic quotation, amount 10 JPY"}]
SCHEMA = {
    "type": "object", "properties": {"lines": {"type": "array", "items": {"type": "string"}}},
    "required": ["lines"], "additionalProperties": False,
}
DUMMY_CREDENTIAL = "mechanism-test-placeholder"


def envelope(text='{"lines":["synthetic"]}', **changes):
    result = {
        "id": "resp_mechanism_1", "status": "completed", "model": "explicit-test-model",
        "output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "role": "assistant", "status": "completed",
             "content": [{"type": "output_text", "text": text}]},
        ],
        "usage": {"input_tokens": 12, "output_tokens": 5, "total_tokens": 17},
    }
    result.update(changes)
    return result


class ResponsesMechanismTests(unittest.TestCase):
    def config(self, **kwargs):
        kwargs.setdefault("max_requests", 1)
        return ResponsesConfig("explicit-test-model", frozenset({SHA}), **kwargs)

    def extractor(self, handler, **config):
        return ResponsesExtractor(
            self.config(**config), lambda: DUMMY_CREDENTIAL,
            transport=httpx.MockTransport(handler),
        )

    def expect_error(self, client, code, **inputs):
        values = dict(document_sha256=SHA, blocks=BLOCKS, schema=SCHEMA)
        values.update(inputs)
        with self.assertRaises(ExtractionError) as caught:
            client.extract(**values)
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn(DUMMY_CREDENTIAL, str(caught.exception))
        self.assertNotIn(DUMMY_CREDENTIAL, repr(caught.exception.metadata))
        return caught.exception

    def test_default_zero_budget_blocks_before_credentials_and_transport(self):
        keys = []
        calls = []
        client = ResponsesExtractor(
            ResponsesConfig("explicit-test-model", frozenset({SHA})),
            lambda: keys.append("accessed"),
            transport=httpx.MockTransport(lambda request: calls.append(request)),
        )
        error = self.expect_error(client, "AI_REQUEST_BUDGET_EXHAUSTED")
        self.assertEqual(error.metadata["max_requests"], 0)
        self.assertEqual(error.metadata["request_attempts"], 0)
        self.assertEqual(keys, [])
        self.assertEqual(calls, [])
        self.assertEqual(client.requests_used, 0)

    def test_budget_rejects_negative_fractional_and_boolean_configuration(self):
        for value in (-1, 1.5, True, "2", None):
            with self.subTest(value=value), self.assertRaises(ExtractionError) as caught:
                self.config(max_requests=value)
            self.assertEqual(caught.exception.code, "AI_REQUEST_BUDGET_INVALID")

    def test_success_and_failed_http_attempts_each_consume_one_request(self):
        for failure in (None, "http", "timeout"):
            keys = []
            calls = []
            def supplier():
                keys.append("accessed")
                return DUMMY_CREDENTIAL
            def handler(request):
                calls.append(request)
                if failure == "timeout":
                    raise httpx.ReadTimeout(DUMMY_CREDENTIAL)
                return httpx.Response(500 if failure == "http" else 200, json=envelope())
            client = ResponsesExtractor(self.config(max_requests=1), supplier,
                                        transport=httpx.MockTransport(handler))
            with self.subTest(failure=failure):
                if failure is None:
                    result = client.extract(SHA, BLOCKS, SCHEMA)
                    self.assertEqual(result.metadata["budget_attempt_number"], 1)
                    self.assertEqual(result.metadata["budget_scope"], "process_instance")
                else:
                    self.expect_error(client, "AI_TIMEOUT" if failure == "timeout" else "AI_HTTP_500")
                self.expect_error(client, "AI_REQUEST_BUDGET_EXHAUSTED")
                self.assertEqual(len(keys), 1)
                self.assertEqual(len(calls), 1)
                self.assertEqual(client.requests_used, 1)

    def test_unavailable_credential_releases_reservation_without_network_attempt(self):
        keys = []
        calls = []
        def supplier():
            keys.append("accessed")
            return "" if len(keys) == 1 else DUMMY_CREDENTIAL
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json=envelope())
        client = ResponsesExtractor(self.config(max_requests=1), supplier,
                                    transport=httpx.MockTransport(handler))
        self.expect_error(client, "AI_CREDENTIAL_UNAVAILABLE")
        self.assertEqual(client.requests_used, 0)
        client.extract(SHA, BLOCKS, SCHEMA)
        self.assertEqual(client.requests_used, 1)
        self.assertEqual(len(calls), 1)
        self.expect_error(client, "AI_REQUEST_BUDGET_EXHAUSTED")
        self.assertEqual(len(keys), 2)

    def test_concurrent_reservations_share_one_budget_before_key_lookup(self):
        lock = Lock()
        keys_ready = Event()
        release_keys = Event()
        keys = []
        calls = []
        def supplier():
            with lock:
                keys.append("accessed")
                if len(keys) == 2:
                    keys_ready.set()
            if not release_keys.wait(3):
                raise RuntimeError("mechanism test synchronization timed out")
            return DUMMY_CREDENTIAL
        def handler(request):
            with lock:
                calls.append(request)
            return httpx.Response(200, json=envelope())
        client = ResponsesExtractor(self.config(max_requests=2), supplier,
                                    transport=httpx.MockTransport(handler))
        def invoke():
            try:
                return client.extract(SHA, BLOCKS, SCHEMA).metadata["budget_attempt_number"]
            except ExtractionError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(invoke) for _ in range(8)]
            try:
                self.assertTrue(keys_ready.wait(3))
                self.assertEqual(len(keys), 2)
            finally:
                release_keys.set()
            results = [future.result(timeout=3) for future in futures]
        self.assertEqual(sorted(value for value in results if type(value) is int), [1, 2])
        self.assertEqual(results.count("AI_REQUEST_BUDGET_EXHAUSTED"), 6)
        self.assertEqual(len(keys), 2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(client.requests_used, 2)

    def test_explicit_model_and_sample_authorization_are_immutable(self):
        with self.assertRaises(ExtractionError):
            ResponsesConfig("", frozenset({SHA}))
        with self.assertRaises(ExtractionError):
            ResponsesConfig("model", {"a" * 64})
        with self.assertRaises(FrozenInstanceError):
            self.config().model = "other"
        for invalid in (float("nan"), 0, 301, True):
            with self.subTest(invalid=invalid), self.assertRaises(ExtractionError):
                self.config(timeout_seconds=invalid)

    def test_authorization_and_input_validation_precede_credentials(self):
        keys = []
        client = ResponsesExtractor(self.config(), lambda: keys.append("accessed"))
        self.expect_error(client, "AI_SAMPLE_NOT_AUTHORIZED", document_sha256="b" * 64)
        self.expect_error(client, "AI_INPUT_INVALID", blocks=[])
        self.expect_error(client, "AI_INPUT_INVALID", blocks=[{"text": float("nan")}])
        self.expect_error(client, "AI_SCHEMA_INVALID", schema={"type": "array"})
        self.expect_error(client, "AI_SCHEMA_INVALID", schema={
            "type": "object", "properties": {"remote": {"$ref": "https://example.invalid/schema"}},
        })
        self.expect_error(client, "AI_SCHEMA_INVALID", schema={
            "type": "object", "$id": "https://example.invalid/schema",
        })
        self.assertEqual(keys, [])

    def test_input_characters_and_utf8_bytes_are_bounded_before_credentials(self):
        for bounds in ({"max_input_chars": 20}, {"max_input_bytes": 150}):
            keys = []
            client = ResponsesExtractor(self.config(**bounds), lambda: keys.append("accessed"))
            self.expect_error(client, "AI_INPUT_TOO_LARGE", blocks=[{"text": "費" * 60}])
            self.assertEqual(keys, [])

    def test_fixed_responses_request_preserves_model_schema_data_and_safe_metadata(self):
        requests = []
        def handler(request):
            requests.append(request)
            self.assertEqual(str(request.url), "https://api.openai.com/v1/responses")
            self.assertEqual(request.method, "POST")
            body = json.loads(request.content)
            self.assertEqual(body["model"], "explicit-test-model")
            self.assertIs(body["store"], False)
            self.assertIs(body["stream"], False)
            self.assertEqual(body["metadata"]["purpose"], "quotation_extraction")
            self.assertEqual(body["text"]["format"]["schema"], SCHEMA)
            self.assertTrue(body["text"]["format"]["strict"])
            self.assertEqual(json.loads(body["input"][1]["content"])["blocks"], BLOCKS)
            self.assertIn("untrusted source data", body["input"][0]["content"])
            self.assertNotIn("tools", body)
            self.assertEqual(request.extensions["timeout"]["read"], 45.0)
            return httpx.Response(200, json=envelope(), headers={"x-request-id": "req_mechanism_1"})
        result = self.extractor(handler).extract(SHA, BLOCKS, SCHEMA)
        self.assertEqual(result.payload, {"lines": ["synthetic"]})
        self.assertEqual(len(requests), 1)
        self.assertEqual(result.metadata["execution_kind"], "MECHANISM_TEST")
        self.assertEqual(result.metadata["usage"]["total_tokens"], 17)
        self.assertEqual(result.metadata["request_attempts"], 1)
        self.assertEqual(result.metadata["automatic_retries"], 0)
        self.assertNotIn(DUMMY_CREDENTIAL, repr(result.metadata))
        self.assertEqual(result.metadata["response_id"], "resp_mechanism_1")

    def test_classification_has_separate_prompt_schema_and_call_metadata(self):
        blocks = [{"item_id": "unmatched-item-1", "raw_label": "Synthetic fee",
                   "context": {"equipment": "20FT"}}]
        schema = {
            "type": "object", "properties": {"suggestions": {
                "type": "array", "items": {"type": "object", "properties": {
                    "item_id": {"type": "string"}, "suggested_fee_name": {"type": "string"}},
                    "required": ["item_id", "suggested_fee_name"], "additionalProperties": False}}},
            "required": ["suggestions"], "additionalProperties": False,
        }
        calls = []
        def handler(request):
            body = json.loads(request.content)
            calls.append(body)
            self.assertEqual(body["metadata"]["purpose"], "fee_classification")
            self.assertEqual(body["text"]["format"]["schema"], schema)
            self.assertEqual(json.loads(body["input"][1]["content"])["blocks"], blocks)
            self.assertIn("unmatched item ID", body["input"][0]["content"])
            self.assertNotIn("配送費", body["input"][0]["content"])
            text = json.dumps({"suggestions": [{"item_id": "unmatched-item-1",
                                               "suggested_fee_name": "Synthetic suggestion"}]})
            return httpx.Response(200, json=envelope(text))
        result = self.extractor(handler).extract(SHA, blocks, schema, purpose="fee_classification")
        self.assertEqual(result.metadata["purpose"], "fee_classification")
        self.assertEqual(result.metadata["execution_kind"], "MECHANISM_TEST")
        self.assertEqual(len(calls), 1)

    def test_extraction_prompt_requires_field_evidence_and_no_classification(self):
        def handler(request):
            prompt = json.loads(request.content)["input"][0]["content"]
            self.assertIn("exact substrings", prompt)
            self.assertIn("Do not classify fees", prompt)
            return httpx.Response(200, json=envelope())
        self.extractor(handler).extract(SHA, BLOCKS, SCHEMA)

    def test_bad_purpose_and_classification_extras_precede_credentials(self):
        keys = []
        client = ResponsesExtractor(self.config(), lambda: keys.append("accessed"))
        for purpose in ("arbitrary", "", None, []):
            with self.subTest(purpose=purpose):
                self.expect_error(client, "AI_PURPOSE_INVALID", purpose=purpose)
        self.expect_error(client, "AI_CLASSIFICATION_INPUT_INVALID", purpose="fee_classification")
        self.expect_error(client, "AI_CLASSIFICATION_INPUT_INVALID", purpose="fee_classification",
                          blocks=[{"item_id": "i1", "raw_label": "Synthetic", "context": {},
                                   "human_confirmed_name": "must not be sent"}])
        self.assertEqual(keys, [])

    def test_comparison_candidates_are_untrusted_exact_name_proposals_only(self):
        candidates = [{"name": "Existing reviewed name",
                       "context": {"equipment": "20FT", "transport_mode": None}}]
        blocks = [{"item_id": "mechanism-item", "raw_label": "ORIGINAL TEST NAME",
                   "context": {"equipment": "20FT"}, "comparison_candidates": candidates}]
        def handler(request):
            body = json.loads(request.content)
            self.assertEqual(json.loads(body["input"][1]["content"])["blocks"], blocks)
            prompt = body["input"][0]["content"]
            self.assertIn("semantically matching candidate", prompt)
            self.assertIn("return its fee_id in matched_fee_id", prompt)
            self.assertIn("server resolves the stored canonical name", prompt)
            self.assertIn("remains AI, not HUMAN or MEMORY", prompt)
            return httpx.Response(200, json=envelope())
        result = self.extractor(handler).extract(SHA, blocks, SCHEMA, purpose="fee_classification")
        self.assertEqual(result.metadata["purpose"], "fee_classification")

    def test_candidate_unknown_fields_or_private_data_are_rejected_before_credentials(self):
        invalid = [
            [{"name": "Name", "context": {}, "fee_id": "PRIVATE"}],
            [{"name": "Name", "context": {}, "raw_label": "RAW_QUOTATION"}],
            [{"name": "Name", "context": {"raw_value": "PRIVATE"}}],
            [{"name": "Name", "context": {"equipment": {"private": "data"}}}],
            [{"name": "", "context": {}}],
            [{"name": "Name", "context": {}}] * 51,
            {"name": "Name", "context": {}},
        ]
        for candidates in invalid:
            keys = []
            client = ResponsesExtractor(self.config(), lambda: keys.append("accessed"))
            with self.subTest(candidates=candidates):
                self.expect_error(client, "AI_CLASSIFICATION_INPUT_INVALID",
                    purpose="fee_classification", blocks=[{
                        "item_id": "i1", "raw_label": "Synthetic", "context": {},
                        "comparison_candidates": candidates}])
                self.assertEqual(keys, [])

    def test_provider_model_alias_is_recorded_without_second_request(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json=envelope(model="explicit-test-model-2030-01-01"))
        result = self.extractor(handler).extract(SHA, BLOCKS, SCHEMA)
        self.assertEqual(result.metadata["requested_model"], "explicit-test-model")
        self.assertEqual(result.metadata["actual_model"], "explicit-test-model-2030-01-01")
        self.assertTrue(result.metadata["model_id_differs"])
        self.assertEqual(len(calls), 1)

    def test_http_errors_and_redirect_do_not_retry_or_echo_body(self):
        for status in (301, 400, 401, 403, 404, 429, 500):
            calls = []
            def handler(request):
                calls.append(request)
                return httpx.Response(status, text=DUMMY_CREDENTIAL,
                                      headers={"location": "https://example.invalid/"})
            with self.subTest(status=status):
                self.expect_error(self.extractor(handler), "AI_HTTP_" + str(status))
                self.assertEqual(len(calls), 1)

    def test_timeout_and_transport_failure_do_not_echo_exception_or_retry(self):
        for exception, code in (
            (httpx.ReadTimeout(DUMMY_CREDENTIAL), "AI_TIMEOUT"),
            (httpx.ConnectError(DUMMY_CREDENTIAL), "AI_TRANSPORT_ERROR"),
        ):
            calls = []
            def handler(request):
                calls.append(request)
                raise exception
            with self.subTest(code=code):
                self.expect_error(self.extractor(handler), code)
                self.assertEqual(len(calls), 1)

    def test_missing_or_failing_credential_supplier_is_sanitized(self):
        # Explicit mechanism transport keeps this credential-sanitization check
        # separate from production's mandatory durable-authorization gate.
        def forbidden_transport(request):
            self.fail("invalid credentials must not reach even a mechanism transport")
        transport = httpx.MockTransport(forbidden_transport)
        for supplier in (lambda: "", lambda: "invalid\nheader", lambda: 123):
            self.expect_error(ResponsesExtractor(self.config(), supplier, transport=transport),
                              "AI_CREDENTIAL_UNAVAILABLE")
        def bad_supplier():
            raise OSError(DUMMY_CREDENTIAL)
        self.expect_error(ResponsesExtractor(self.config(), bad_supplier, transport=transport),
                          "AI_CREDENTIAL_UNAVAILABLE")

    def test_incomplete_failed_and_refused_responses_are_not_success(self):
        cases = [
            (envelope(status="incomplete", incomplete_details={"reason": DUMMY_CREDENTIAL}),
             "AI_RESPONSE_INCOMPLETE"),
            (envelope(status="failed", error={"message": DUMMY_CREDENTIAL}),
             "AI_RESPONSE_NOT_COMPLETED"),
            (envelope(error={"message": DUMMY_CREDENTIAL}), "AI_RESPONSE_ERROR"),
            (envelope(output=[{"type": "message", "role": "assistant", "content": [
                {"type": "refusal", "refusal": DUMMY_CREDENTIAL}]}]), "AI_REFUSAL"),
        ]
        for response, code in cases:
            with self.subTest(code=code):
                self.expect_error(self.extractor(lambda request: httpx.Response(200, json=response)), code)

    def test_malformed_json_duplicate_fields_schema_mismatch_are_rejected(self):
        for raw in ("not JSON", '{"lines":[],"lines":["other"]}', '{"lines":NaN}',
                    '{"lines":[123]}', '{"lines":[],"extra":"not allowed"}'):
            with self.subTest(raw=raw):
                self.expect_error(self.extractor(
                    lambda request: httpx.Response(200, json=envelope(raw))),
                    "AI_OUTPUT_SCHEMA_INVALID")
        for raw in (b"invalid", b'{"status":"completed","status":"failed"}'):
            self.expect_error(self.extractor(
                lambda request: httpx.Response(200, content=raw)), "AI_RESPONSE_INVALID_JSON")

    def test_empty_tool_and_multiple_outputs_are_not_accepted_as_json(self):
        for output in ([], [{"type": "function_call", "arguments": "{}"}],
                       envelope()["output"] * 2):
            self.expect_error(self.extractor(
                lambda request: httpx.Response(200, json=envelope(output=output))),
                "AI_UNEXPECTED_OUTPUT" if output and output[0]["type"] == "function_call"
                else "AI_RESPONSE_INVALID")

    def test_schema_snapshot_survives_caller_mutation(self):
        schema = json.loads(json.dumps(SCHEMA))
        def supplier():
            schema["properties"]["lines"] = {"type": "number"}
            return DUMMY_CREDENTIAL
        client = ResponsesExtractor(self.config(), supplier, transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=envelope())))
        self.assertEqual(client.extract(SHA, BLOCKS, schema).payload, {"lines": ["synthetic"]})

    def test_response_size_limit_closes_stream(self):
        class Stream(httpx.SyncByteStream):
            closed = False
            def __iter__(self):
                yield b"a" * 20
                yield b"b" * 20
            def close(self):
                self.closed = True
        stream = Stream()
        client = self.extractor(lambda request: httpx.Response(200, stream=stream),
                                max_response_bytes=30)
        self.expect_error(client, "AI_RESPONSE_TOO_LARGE")
        self.assertTrue(stream.closed)

    def test_untrusted_metadata_is_filtered(self):
        response = envelope(id=DUMMY_CREDENTIAL, model="line\nbreak",
                            usage={"input_tokens": True, "output_tokens": -1,
                                   "total_tokens": 17, "secret": DUMMY_CREDENTIAL})
        result = self.extractor(
            lambda request: httpx.Response(200, json=response,
                                          headers={"x-request-id": DUMMY_CREDENTIAL})
        ).extract(SHA, BLOCKS, SCHEMA)
        self.assertNotIn("response_id", result.metadata)
        self.assertNotIn("request_id", result.metadata)
        self.assertNotIn("actual_model", result.metadata)
        self.assertEqual(result.metadata["usage"], {"total_tokens": 17})
        self.assertNotIn(DUMMY_CREDENTIAL, repr(result.metadata))


if __name__ == "__main__":
    unittest.main(verbosity=2)
