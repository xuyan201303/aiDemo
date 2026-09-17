"""Configuration/mode MECHANISM_TEST; no SDK, credentials, business processing, or AI.

Factory checks suppress only PostgreSQL grant initialization. ResponsesExtractor
and MockSDKReader are the real application classes; no AI output is substituted.
Durable PostgreSQL behavior has separate real-database budget tests.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

import httpx

from sanzo_core.ai_budget import PostgresAIBudget
from sanzo_core.postgres import PostgresRepository
from sanzo_demo.ai import ExtractionError, ResponsesConfig, ResponsesExtractor
from sanzo_demo.configuration import AIAuthorization, AUTHORIZATION_ID, assemble_runtime
from sanzo_demo.ingestion import IntegrationRuntime
from sanzo_demo.mock_reader import MockSDKReader

PROJECT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT / "demo_materials" / "fixtures"
DUMMY_MODEL = "explicit-mechanism-model"
STORAGE = "/private/tmp/sanzo-configuration-mechanism-no-upload"


def configuration(**changes):
    value = {
        "allow_sample_content_to_openai": True,
        "model": DUMMY_MODEL,
        "max_requests": 4,
        "credential_source": {"kind": "environment", "name": "SANZO_TEST_NON_SECRET_NAME"},
    }
    value.update(changes)
    return value


class MockConfigurationTests(unittest.TestCase):
    def test_explicit_configuration_parses_without_credential_or_file_access(self):
        raw = configuration()
        authorization = AIAuthorization.parse(json.dumps(raw))
        self.assertEqual(authorization.public_config(), raw)
        self.assertEqual(authorization.model, DUMMY_MODEL)
        self.assertEqual(authorization.max_requests, 4)
        file_authorization = AIAuthorization.parse(configuration(credential_source={
            "kind": "file", "path": "/private/tmp/nonexistent-mechanism-credential-source",
        }))
        self.assertEqual(file_authorization.credential_source["kind"], "file")
        self.assertTrue(callable(file_authorization.key_supplier()))
        # Constructing a supplier does not open this nonexistent path.
        with self.assertRaises(FrozenInstanceError):
            authorization.model = "different"

    def test_configuration_copies_source_and_public_mapping(self):
        raw = configuration()
        authorization = AIAuthorization.parse(raw)
        raw["credential_source"]["name"] = "CHANGED_ORIGINAL_MAPPING"
        returned = authorization.public_config()
        returned["credential_source"]["name"] = "CHANGED_PUBLIC_MAPPING"
        self.assertEqual(authorization.credential_source["name"], "SANZO_TEST_NON_SECRET_NAME")

    def test_permission_model_positive_cap_and_known_keys_are_required(self):
        for raw in (
            None, {}, "not-json", "[]",
            configuration(allow_sample_content_to_openai=False),
            configuration(allow_sample_content_to_openai=1),
            configuration(max_requests=0), configuration(max_requests=-1),
            configuration(max_requests=True), configuration(max_requests="3"),
            configuration(model=""), configuration(unknown="field"),
            {key: value for key, value in configuration().items() if key != "model"},
        ):
            with self.subTest(raw=raw), self.assertRaises((ValueError, ExtractionError)):
                AIAuthorization.parse(raw)

    def test_key_values_and_extra_credential_fields_are_rejected_without_echo(self):
        dummy = "never-use-this-mechanism-key-value"
        for raw in (
            configuration(api_key=dummy),
            configuration(credential_source={"kind": "environment", "name": "SANZO_KEY",
                                             "value": dummy}),
            configuration(credential_source={"kind": "key", "value": dummy}),
            configuration(credential_source={"kind": "file", "path": "/tmp/example", "key": dummy}),
            configuration(credential_source={"kind": "environment", "name": "invalid-name"}),
            configuration(credential_source={"kind": "file", "path": "relative-key-file"}),
            configuration(credential_source={"kind": "environment", "name": ""}),
        ):
            with self.subTest(kind=raw["credential_source"].get("kind")):
                with self.assertRaises(ValueError) as caught:
                    AIAuthorization.parse(raw)
                self.assertNotIn(dummy, str(caught.exception))

    def test_duplicate_authorization_json_keys_are_not_last_wins_permission(self):
        raw = json.dumps(configuration())
        for duplicate in (
            '"allow_sample_content_to_openai": false, ',
            '"model": "unapproved-earlier-model", ',
            '"max_requests": 1, ',
        ):
            with self.subTest(field=duplicate.split(":")[0]), self.assertRaises(ValueError):
                AIAuthorization.parse("{" + duplicate + raw[1:])

    def test_default_real_mode_never_constructs_mock_reader_or_falls_back(self):
        # Factory mechanism: suppress SDK construction only, not business calls.
        reader = object()
        with patch("sanzo_demo.configuration.MockSDKReader",
                   side_effect=AssertionError("REAL must never load fixture reader")):
            with patch("sanzo_demo.configuration._real_sdk_reader", return_value=reader):
                runtime = assemble_runtime(object(), STORAGE, fixtures_dir=FIXTURES)
        self.assertEqual(runtime.reader_mode, "REAL_SDK")
        self.assertIs(runtime.reader, reader)
        self.assertIsNone(runtime.extractor)

    def test_real_mode_with_ai_authorization_rejects_unavailable_sdk_without_fallback(self):
        authorization = AIAuthorization.parse(configuration())
        with patch("sanzo_demo.configuration.MockSDKReader",
                   side_effect=AssertionError("REAL must never fallback")):
            with patch("sanzo_demo.configuration._real_sdk_reader",
                       side_effect=ValueError("SDK_RUNTIME_UNAVAILABLE")):
                with self.assertRaisesRegex(ValueError, "^SDK_RUNTIME_UNAVAILABLE$"):
                    assemble_runtime(object(), STORAGE, authorization=authorization,
                                     reader_mode="REAL_SDK", fixtures_dir=FIXTURES)

    def test_mock_mode_without_ai_authorization_initializes_only_fixture_reader(self):
        repository = Mock(spec=PostgresRepository)
        with patch("sanzo_demo.configuration.ResponsesExtractor",
                   side_effect=AssertionError("no authorization must construct no AI client")):
            runtime = assemble_runtime(repository, STORAGE, reader_mode="MOCK_SDK",
                                       fixtures_dir=FIXTURES)
        self.assertIsInstance(runtime.reader, MockSDKReader)
        self.assertIsNone(runtime.extractor)
        self.assertFalse(runtime.allow_mechanism_tests)
        capabilities = runtime.capabilities()
        self.assertTrue(capabilities["reader"]["ready"])
        self.assertFalse(capabilities["sdk"]["ready"])
        self.assertFalse(capabilities["ai"]["ready"])
        self.assertEqual(capabilities["source_notice"], "デモ用解析データ／SDK未接続")
        self.assertEqual(len(capabilities["missing_conditions"]), 1)
        self.assertEqual(repository.mock_calls, [])

    def test_mock_factory_does_not_initialize_business_processing_or_read_samples(self):
        with patch.object(MockSDKReader, "read",
                          side_effect=AssertionError("startup must not process samples")):
            runtime = assemble_runtime(object(), STORAGE, reader_mode="MOCK_SDK",
                                       fixtures_dir=FIXTURES)
        self.assertEqual(len(runtime.reader.sample_hashes), 3)

    def test_mock_mode_disallows_mechanism_flag_fake_ai_and_custom_transport(self):
        reader = MockSDKReader(FIXTURES)
        def forbidden(*args, **kwargs):
            self.fail("mode isolation must not call credentials or transport")
        mechanism_ai = ResponsesExtractor(
            ResponsesConfig(DUMMY_MODEL, reader.sample_hashes, max_requests=1),
            forbidden, transport=httpx.MockTransport(forbidden),
        )
        for changes in (
            {"allow_mechanism_tests": True},
            {"extractor": object()},
            {"extractor": mechanism_ai},
        ):
            with self.subTest(keys=sorted(changes)):
                with self.assertRaisesRegex(ValueError, "^MOCK_MODE_REQUIRES_REAL_AI_CLIENT$"):
                    IntegrationRuntime(object(), STORAGE, reader=reader,
                                       reader_mode="MOCK_SDK", **changes)
        with self.assertRaisesRegex(ValueError, "^MOCK_READER_REQUIRED$"):
            IntegrationRuntime(object(), STORAGE, reader=object(), reader_mode="MOCK_SDK")

    def test_unknown_mode_is_rejected_without_constructing_reader(self):
        with patch("sanzo_demo.configuration.MockSDKReader",
                   side_effect=AssertionError("invalid mode must not load fixtures")):
            for mode in ("", "AUTO", "mock", "REAL_SDK_WITH_MOCK_FALLBACK"):
                with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, "^INVALID_READER_MODE$"):
                    assemble_runtime(object(), STORAGE, reader_mode=mode, fixtures_dir=FIXTURES)

    def test_authorized_factory_assembles_actual_api_client_and_fixed_durable_grant(self):
        # Only grant initialization is suppressed in this pure factory check.
        # No substitute reader, AI class, AI result, or learning path is involved.
        authorization = AIAuthorization.parse(configuration())
        repository = Mock(spec=PostgresRepository)
        supplier = Mock(side_effect=AssertionError("assembly must not read a credential"))
        with patch.object(PostgresAIBudget, "_ensure_authorization", autospec=True) as initialize:
            with patch.object(AIAuthorization, "key_supplier", return_value=supplier):
                runtime = assemble_runtime(
                    repository, STORAGE, reader_mode="MOCK_SDK",
                    authorization=authorization, fixtures_dir=FIXTURES,
                )
        self.assertIsInstance(runtime.reader, MockSDKReader)
        self.assertIs(type(runtime.extractor), ResponsesExtractor)
        self.assertIsNone(runtime.extractor._transport)
        self.assertFalse(runtime.allow_mechanism_tests)
        self.assertIs(type(runtime.extractor._budget), PostgresAIBudget)
        grant = runtime.extractor._budget.authorization
        self.assertEqual(grant.authorization_id, AUTHORIZATION_ID)
        self.assertEqual(grant.model, DUMMY_MODEL)
        self.assertEqual(grant.max_requests, 4)
        self.assertEqual(grant.authorized_sample_sha256, runtime.reader.sample_hashes)
        self.assertEqual(runtime.extractor.config.authorized_sample_sha256, runtime.reader.sample_hashes)
        self.assertEqual(runtime.extractor._key_supplier, supplier)
        repository.migrate.assert_called_once_with()
        repository.transaction.assert_not_called()
        initialize.assert_called_once_with(runtime.extractor._budget)
        supplier.assert_not_called()

    def test_sdk_import_is_forbidden_while_mock_runtime_initializes_in_fresh_process(self):
        child = """
import importlib.abc,json,sys
class ForbidSDK(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname == "document_sdk" or fullname.startswith("document_sdk."):
   raise AssertionError("real SDK import forbidden in Mock mode")
  return None
sys.meta_path.insert(0,ForbidSDK())
from sanzo_demo.configuration import assemble_runtime
runtime=assemble_runtime(object(),sys.argv[1],reader_mode="MOCK_SDK",fixtures_dir=sys.argv[2])
assert runtime.reader is not None
assert runtime.extractor is None
assert not any(n == "document_sdk" or n.startswith("document_sdk.") for n in sys.modules)
print(json.dumps({"reader_mode":runtime.reader_mode,"ai_constructed":False,"sdk_imported":False}))
"""
        result = subprocess.run(
            [sys.executable, "-B", "-c", child, STORAGE, str(FIXTURES)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "reader_mode": "MOCK_SDK", "ai_constructed": False, "sdk_imported": False,
        })


if __name__ == "__main__":
    unittest.main(verbosity=2)
