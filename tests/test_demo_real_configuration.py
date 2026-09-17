"""Real/Mock assembly mechanisms: no credential reads, AI calls, or DB mutation.

Real SDK execution is tested separately against the fixed wheel and uploads.
These factory tests substitute only SDK construction / grant initialization.
"""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

from sanzo_core.ai_budget import PostgresAIBudget
from sanzo_core.postgres import PostgresRepository
from sanzo_demo.ai import ResponsesExtractor
from sanzo_demo.configuration import (
    AIAuthorization, AUTHORIZATION_ID, AUTHORIZED_DEMO_SAMPLE_SHA256,
    _real_sdk_reader, assemble_runtime,
)
from sanzo_demo.mock_reader import MockSDKReader

FIXTURES = Path(__file__).resolve().parents[1] / "demo_materials/fixtures"


class RealConfigurationTests(unittest.TestCase):
    def test_real_without_authorization_needs_no_key_or_fixture(self):
        repository, reader = Mock(spec=PostgresRepository), object()
        with patch("sanzo_demo.configuration._real_sdk_reader", return_value=reader):
            with patch("sanzo_demo.configuration.MockSDKReader", side_effect=AssertionError):
                with patch.object(AIAuthorization, "key_supplier", side_effect=AssertionError):
                    runtime = assemble_runtime(repository, "/unused", reader_mode="REAL_SDK",
                                               fixtures_dir=Path("/nonexistent/fixtures"))
        self.assertIs(runtime.reader, reader)
        self.assertIsNone(runtime.extractor)
        self.assertFalse(runtime.allow_mechanism_tests)
        self.assertEqual(repository.mock_calls, [])

    def test_missing_real_adapter_never_falls_back(self):
        with patch.dict(sys.modules, {"sanzo_demo.document_sdk_adapter": None}):
            with self.assertRaisesRegex(ValueError, "^SDK_RUNTIME_UNAVAILABLE$"):
                _real_sdk_reader()

    def test_real_authorization_uses_same_fixed_durable_grant_and_lazy_key(self):
        authorization = AIAuthorization.parse({
            "allow_sample_content_to_openai": True, "model": "mechanism-approved-model",
            "max_requests": 7,
            "credential_source": {"kind": "environment", "name": "NONSECRET_TEST_SOURCE"},
        })
        repository, reader = Mock(spec=PostgresRepository), object()
        supplier = Mock(side_effect=AssertionError("Assembly must not read credentials"))
        with patch("sanzo_demo.configuration._real_sdk_reader", return_value=reader):
            with patch("sanzo_demo.configuration.MockSDKReader", side_effect=AssertionError):
                with patch.object(PostgresAIBudget, "_ensure_authorization", autospec=True):
                    with patch.object(AIAuthorization, "key_supplier", return_value=supplier):
                        runtime = assemble_runtime(repository, "/unused", reader_mode="REAL_SDK",
                                                   authorization=authorization)
        self.assertIs(type(runtime.extractor), ResponsesExtractor)
        self.assertIsNone(runtime.extractor._transport)
        self.assertIs(type(runtime.extractor._budget), PostgresAIBudget)
        grant = runtime.extractor._budget.authorization
        self.assertEqual(grant.authorization_id, AUTHORIZATION_ID)
        self.assertEqual(grant.authorization_id, "sanzo-three-file-mock-sdk-v1")
        self.assertEqual(grant.max_requests, 7)
        self.assertEqual(grant.model, authorization.model)
        self.assertEqual(grant.authorized_sample_sha256, AUTHORIZED_DEMO_SAMPLE_SHA256)
        self.assertEqual(AUTHORIZED_DEMO_SAMPLE_SHA256, MockSDKReader(FIXTURES).sample_hashes)
        self.assertIs(runtime.extractor._key_supplier, supplier)
        supplier.assert_not_called()
        repository.migrate.assert_called_once_with()
        repository.transaction.assert_not_called()

    def test_unavailable_real_sdk_does_not_initialize_or_change_budget(self):
        repository = Mock(spec=PostgresRepository)
        with patch("sanzo_demo.configuration._real_sdk_reader",
                   side_effect=ValueError("SDK_RUNTIME_UNAVAILABLE")):
            with patch("sanzo_demo.configuration.PostgresAIBudget", side_effect=AssertionError):
                with self.assertRaisesRegex(ValueError, "SDK_RUNTIME_UNAVAILABLE"):
                    assemble_runtime(repository, "/unused", reader_mode="REAL_SDK",
                                     authorization=object())
        self.assertEqual(repository.mock_calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
