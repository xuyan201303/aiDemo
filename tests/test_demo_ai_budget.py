"""Budget mechanism checks; real PostgreSQL tests never call OpenAI.

SANZO_TEST_DSN must name the project's disposable test DB. No reset, migrations
outside that DB, provider network, SDK import, or credential files are used.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
import subprocess
import sys
from threading import Barrier
import unittest
from uuid import uuid4

import httpx

from sanzo_core.ai_budget import AIAuthorization, AIBudgetError, PostgresAIBudget
from sanzo_core.postgres import PostgresRepository
from sanzo_demo.ai import ExtractionError, ResponsesConfig, ResponsesExtractor

DSN = os.environ.get("SANZO_TEST_DSN")
SHA = "a" * 64
OTHER_SHA = "b" * 64
MODEL = "explicit-mechanism-model"
DUMMY = "mechanism-only-no-real-credential"
BLOCKS = [{"block_id": "synthetic-1", "text": "complete synthetic source field"}]
SCHEMA = {
    "type": "object", "properties": {"lines": {"type": "array", "items": {"type": "string"}}},
    "required": ["lines"], "additionalProperties": False,
}


def envelope():
    return {
        "id": "resp_budget_mechanism", "model": MODEL, "status": "completed",
        "output": [{"type": "message", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": '{"lines":[]}'}]}],
    }


class AIBudgetLocalTests(unittest.TestCase):
    def test_default_network_requires_durable_budget_before_credentials(self):
        keys = []
        extractor = ResponsesExtractor(
            ResponsesConfig(MODEL, frozenset({SHA}), max_requests=3),
            lambda: keys.append("lookup"),
        )
        with self.assertRaises(ExtractionError) as caught:
            extractor.extract(SHA, BLOCKS, SCHEMA)
        self.assertEqual(caught.exception.code, "AI_DURABLE_BUDGET_REQUIRED")
        self.assertEqual(caught.exception.metadata["request_attempts"], 0)
        self.assertEqual(keys, [])

    def test_non_postgres_budget_cannot_enable_network(self):
        with self.assertRaises(ExtractionError) as caught:
            ResponsesExtractor(
                ResponsesConfig(MODEL, frozenset({SHA}), max_requests=3),
                lambda: self.fail("credential lookup"), budget=object(),
            )
        self.assertEqual(caught.exception.code, "AI_DURABLE_BUDGET_REQUIRED")

    def test_authorization_rejects_invalid_identity_cap_and_scope(self):
        valid = dict(authorization_id="fixed-demo-round", model=MODEL, max_requests=2,
                     authorized_sample_sha256=frozenset({SHA}))
        for changes in (
            {"authorization_id": ""}, {"authorization_id": "../grant"},
            {"authorization_id": "grant\n"}, {"max_requests": True},
            {"max_requests": -1}, {"max_requests": 2147483648},
            {"authorized_sample_sha256": frozenset()},
            {"authorized_sample_sha256": frozenset({"not-a-hash"})},
            {"authorized_sample_sha256": {SHA}}, {"model": ""},
        ):
            with self.subTest(changes=changes), self.assertRaises(AIBudgetError):
                AIAuthorization(**{**valid, **changes})

    def test_local_validation_does_not_require_budget_or_touch_credentials(self):
        keys = []
        extractor = ResponsesExtractor(
            ResponsesConfig(MODEL, frozenset({SHA}), max_requests=1),
            lambda: keys.append("lookup"),
        )
        with self.assertRaises(ExtractionError) as caught:
            extractor.extract(OTHER_SHA, BLOCKS, SCHEMA)
        self.assertEqual(caught.exception.code, "AI_SAMPLE_NOT_AUTHORIZED")
        self.assertEqual(keys, [])

    def test_prompt_requests_complete_field_and_same_record_without_answers(self):
        seen = []
        def handler(request):
            prompt = json.loads(request.content)["input"][0]["content"]
            seen.append(prompt)
            self.assertIn("complete source field/cell values", prompt)
            self.assertIn("same source record", prompt)
            self.assertIn("Never use exact substrings", prompt)
            self.assertNotIn("配送費", prompt)
            self.assertNotIn("9,500", prompt)
            return httpx.Response(200, json=envelope())
        extractor = ResponsesExtractor(
            ResponsesConfig(MODEL, frozenset({SHA}), max_requests=1),
            lambda: DUMMY, transport=httpx.MockTransport(handler),
        )
        self.assertEqual(extractor.extract(SHA, BLOCKS, SCHEMA).metadata["execution_kind"],
                         "MECHANISM_TEST")
        self.assertEqual(len(seen), 1)


@unittest.skipUnless(DSN, "NOT_RUN: no dedicated SANZO_TEST_DSN configured")
class AIBudgetPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        PostgresRepository(DSN).migrate()

    def setUp(self):
        self.repo = PostgresRepository(DSN)
        self.authorization_id = "budget-mechanism-" + uuid4().hex

    def budget(self, **changes):
        values = dict(model=MODEL, max_requests=2, authorized_sample_sha256=frozenset({SHA}))
        values.update(changes)
        return PostgresAIBudget(self.repo, self.authorization_id, **values)

    def attempts(self):
        with self.repo.transaction() as conn:
            return conn.execute("""
                SELECT attempt_number,attempt_id,document_sha256,purpose,state
                FROM sanzo_core.ai_request_attempts WHERE authorization_id=%s
                ORDER BY attempt_number
            """, (self.authorization_id,)).fetchall()

    def test_new_instance_and_new_process_resume_same_cap(self):
        budget = self.budget()
        first = budget.consume_request(SHA, "quotation_extraction")
        self.assertEqual(first["budget_attempt_number"], 1)
        self.assertEqual(self.budget().requests_used, 1)
        child = """
import json,os,sys
from sanzo_core.ai_budget import PostgresAIBudget
from sanzo_core.postgres import PostgresRepository
budget=PostgresAIBudget(PostgresRepository(os.environ["SANZO_TEST_DSN"]),sys.argv[1],
 model=sys.argv[2],max_requests=2,authorized_sample_sha256=frozenset({"a"*64}))
metadata=budget.consume_request("a"*64,"fee_classification")
print(json.dumps(metadata))
"""
        result = subprocess.run(
            [sys.executable, "-B", "-c", child, self.authorization_id, MODEL],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        metadata = json.loads(result.stdout)
        self.assertEqual(metadata["budget_attempt_number"], 2)
        self.assertEqual(metadata["budget_scope"], "postgres_authorization")
        with self.assertRaises(AIBudgetError) as caught:
            self.budget().consume_request(SHA, "quotation_extraction")
        self.assertEqual(caught.exception.code, "AI_REQUEST_BUDGET_EXHAUSTED")
        self.assertEqual(caught.exception.metadata["requests_used"], 2)
        self.assertEqual(len(self.attempts()), 2)

    def test_process_crash_after_commit_does_not_restore_allowance(self):
        self.budget(max_requests=1)
        child = """
import os,sys
from sanzo_core.ai_budget import PostgresAIBudget
from sanzo_core.postgres import PostgresRepository
budget=PostgresAIBudget(PostgresRepository(os.environ["SANZO_TEST_DSN"]),sys.argv[1],
 model=sys.argv[2],max_requests=1,authorized_sample_sha256=frozenset({"a"*64}))
budget.consume_request("a"*64,"quotation_extraction")
os._exit(9)
"""
        result = subprocess.run(
            [sys.executable, "-B", "-c", child, self.authorization_id, MODEL],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 9)
        self.assertEqual(self.budget(max_requests=1).requests_used, 1)
        self.assertEqual(self.attempts()[0]["state"], "ALLOWANCE_CONSUMED")

    def test_concurrent_workers_share_one_total_for_both_purposes(self):
        self.budget(max_requests=3)
        barrier = Barrier(8)
        def run(index):
            budget = self.budget(max_requests=3)
            barrier.wait(timeout=10)
            try:
                return budget.consume_request(
                    SHA, "quotation_extraction" if index % 2 else "fee_classification",
                )["budget_attempt_number"]
            except AIBudgetError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(run, range(8)))
        self.assertEqual(sorted(value for value in results if type(value) is int), [1, 2, 3])
        self.assertEqual(results.count("AI_REQUEST_BUDGET_EXHAUSTED"), 5)
        self.assertEqual(self.budget(max_requests=3).requests_used, 3)
        self.assertEqual(len(self.attempts()), 3)

    def test_existing_authorization_rejects_model_cap_or_sample_scope_changes(self):
        self.budget().consume_request(SHA, "quotation_extraction")
        for changes in (
            {"model": "other-model"}, {"max_requests": 3}, {"max_requests": 1},
            {"authorized_sample_sha256": frozenset({SHA, OTHER_SHA})},
            {"authorized_sample_sha256": frozenset({OTHER_SHA})},
        ):
            with self.subTest(changes=changes), self.assertRaises(AIBudgetError) as caught:
                self.budget(**changes)
            self.assertEqual(caught.exception.code, "AI_AUTHORIZATION_CONFIG_CONFLICT")
        self.assertEqual(self.budget().requests_used, 1)

    def test_database_rejects_reset_raise_delete_and_attempt_mutation(self):
        budget = self.budget()
        budget.consume_request(SHA, "quotation_extraction")
        queries = (
            "UPDATE sanzo_core.ai_authorizations SET requests_used=0 WHERE authorization_id=%s",
            "UPDATE sanzo_core.ai_authorizations SET max_requests=99 WHERE authorization_id=%s",
            "DELETE FROM sanzo_core.ai_authorizations WHERE authorization_id=%s",
            "UPDATE sanzo_core.ai_request_attempts SET purpose='fee_classification' WHERE authorization_id=%s",
            "DELETE FROM sanzo_core.ai_request_attempts WHERE authorization_id=%s",
        )
        for query in queries:
            with self.subTest(query=query), self.assertRaises(Exception):
                with self.repo.transaction() as conn:
                    conn.execute(query, (self.authorization_id,))
        self.assertEqual(budget.requests_used, 1)
        self.assertEqual(len(self.attempts()), 1)

    def test_failed_transaction_does_not_leave_counter_or_journal_half_saved(self):
        budget = self.budget()
        class FailingRepository(PostgresRepository):
            @contextmanager
            def transaction(self):
                with super().transaction() as conn:
                    yield conn
                    raise RuntimeError("injected failure before commit")
        budget.repo = FailingRepository(DSN)
        with self.assertRaises(AIBudgetError) as caught:
            budget.consume_request(SHA, "quotation_extraction")
        self.assertEqual(caught.exception.code, "AI_BUDGET_STORAGE_UNAVAILABLE")
        self.assertEqual(self.budget().requests_used, 0)
        self.assertEqual(self.attempts(), [])

    def test_failed_http_attempt_remains_consumed_and_no_retry_after_restart(self):
        calls = []
        budget = self.budget(max_requests=1)
        def handler(request):
            calls.append("attempt")
            return httpx.Response(429, text=DUMMY)
        def extractor():
            return ResponsesExtractor(
                ResponsesConfig(MODEL, frozenset({SHA}), max_requests=1),
                lambda: DUMMY, budget=self.budget(max_requests=1),
                transport=httpx.MockTransport(handler),
            )
        with self.assertRaises(ExtractionError) as caught:
            extractor().extract(SHA, BLOCKS, SCHEMA)
        error = caught.exception
        self.assertEqual(error.code, "AI_HTTP_429")
        self.assertEqual(error.metadata["request_attempts"], 1)
        self.assertEqual(error.metadata["budget_allowance_consumed"], True)
        self.assertEqual(error.metadata["budget_scope"], "postgres_authorization")
        self.assertEqual(error.metadata["execution_kind"], "MECHANISM_TEST")
        self.assertNotIn(DUMMY, repr(error.metadata))
        with self.assertRaises(ExtractionError) as exhausted:
            extractor().extract(SHA, BLOCKS, SCHEMA)
        self.assertEqual(exhausted.exception.code, "AI_REQUEST_BUDGET_EXHAUSTED")
        self.assertEqual(len(calls), 1)
        self.assertEqual(budget.requests_used, 1)

    def test_credential_failure_is_consumed_without_claiming_http_attempt(self):
        calls, keys = [], []
        budget = self.budget(max_requests=1)
        def key():
            keys.append("lookup")
            raise RuntimeError(DUMMY)
        extractor = ResponsesExtractor(
            ResponsesConfig(MODEL, frozenset({SHA}), max_requests=1), key,
            budget=budget, transport=httpx.MockTransport(lambda r: calls.append(r)),
        )
        with self.assertRaises(ExtractionError) as caught:
            extractor.extract(SHA, BLOCKS, SCHEMA)
        self.assertEqual(caught.exception.code, "AI_CREDENTIAL_UNAVAILABLE")
        self.assertEqual(caught.exception.metadata["request_attempts"], 0)
        self.assertEqual(caught.exception.metadata["budget_allowance_consumed"], True)
        self.assertNotIn(DUMMY, repr(caught.exception.metadata))
        self.assertEqual(budget.requests_used, 1)
        with self.assertRaises(ExtractionError):
            extractor.extract(SHA, BLOCKS, SCHEMA)
        self.assertEqual(keys, ["lookup"])
        self.assertEqual(calls, [])

    def test_successful_response_journal_and_metadata_agree(self):
        budget = self.budget()
        extractor = ResponsesExtractor(
            ResponsesConfig(MODEL, frozenset({SHA}), max_requests=2),
            lambda: DUMMY, budget=budget,
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=envelope())),
        )
        metadata = extractor.extract(SHA, BLOCKS, SCHEMA).metadata
        self.assertEqual(extractor.requests_used, 1)
        self.assertEqual(metadata["budget_attempt_id"], self.attempts()[0]["attempt_id"])
        self.assertEqual(metadata["document_sha256"], self.attempts()[0]["document_sha256"])
        self.assertEqual(metadata["purpose"], self.attempts()[0]["purpose"])
        self.assertEqual(metadata["request_attempts"], 1)
        self.assertNotIn(DUMMY, repr(self.attempts()))

    def test_extractor_mismatched_config_and_unapproved_inputs_consume_nothing(self):
        budget = self.budget()
        with self.assertRaises(ExtractionError) as caught:
            ResponsesExtractor(
                ResponsesConfig(MODEL, frozenset({SHA}), max_requests=3),
                lambda: self.fail("credential access"), budget=budget,
            )
        self.assertEqual(caught.exception.code, "AI_AUTHORIZATION_CONFIG_CONFLICT")
        for sha, purpose, code in (
            (OTHER_SHA, "quotation_extraction", "AI_SAMPLE_NOT_AUTHORIZED"),
            (SHA, "unknown-purpose", "AI_PURPOSE_INVALID"),
        ):
            with self.assertRaises(AIBudgetError) as error:
                budget.consume_request(sha, purpose)
            self.assertEqual(error.exception.code, code)
        self.assertEqual(budget.requests_used, 0)
        self.assertEqual(self.attempts(), [])

    def test_zero_budget_prevents_credentials_and_network(self):
        budget = self.budget(max_requests=0)
        keys = []
        extractor = ResponsesExtractor(
            ResponsesConfig(MODEL, frozenset({SHA}), max_requests=0),
            lambda: keys.append("lookup"), budget=budget,
        )
        with self.assertRaises(ExtractionError) as caught:
            extractor.extract(SHA, BLOCKS, SCHEMA)
        self.assertEqual(caught.exception.code, "AI_REQUEST_BUDGET_EXHAUSTED")
        self.assertEqual(keys, [])
        self.assertEqual(budget.requests_used, 0)
        self.assertEqual(self.attempts(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
