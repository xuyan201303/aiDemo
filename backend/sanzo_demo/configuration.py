"""Explicit backend assembly; Real mode loads only the installed fixed-wheel adapter.

The authorization document contains configuration, never a credential value.
Merely setting reader mode or having a key in the environment grants no AI use.
"""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re

from sanzo_core.ai_budget import PostgresAIBudget
from .ai import ResponsesConfig, ResponsesExtractor
from .ingestion import IntegrationRuntime
from .mock_reader import MockSDKReader

# Fixed across restarts/processes. No CLI switch manufactures another allowance.
AUTHORIZATION_ID = "sanzo-three-file-mock-sdk-v1"

# Outbound-content authorization only. These hashes never select parsing logic,
# document fields, classifications, or learned answers. Real mode does not read
# the Mock manifest or fixtures to authorize the same approved three documents.
AUTHORIZED_DEMO_SAMPLE_SHA256 = frozenset({
    "637537223ea2d220ba658e1eae7ba6c9174d17f3f1d1e5e5bd85bc712611dd54",
    "9cbb60b4c8a0c2b5970b3ffe2cec2d03a4a5ceff7a59d6de01dec7cafecc618e",
    "b2fdc4622f98064d7115186587407082a0fbc873b59dee8803a5ff51c54bba98",
})


def _real_sdk_reader():
    # Import only in explicit Real mode. Missing/unsupported SDK fails closed;
    # this factory never substitutes MockSDKReader or a recorded SDK response.
    try:
        from .document_sdk_adapter import DocumentSdkAdapter
        return DocumentSdkAdapter()
    except ImportError:
        raise ValueError("SDK_RUNTIME_UNAVAILABLE") from None


@dataclass(frozen=True)
class AIAuthorization:
    model: str
    max_requests: int
    credential_source: dict

    @classmethod
    def parse(cls, raw):
        if isinstance(raw, str):
            try:
                def pairs(values):
                    result = {}
                    for key, value in values:
                        if key in result:
                            raise ValueError("Duplicate configuration key")
                        result[key] = value
                    return result
                raw = json.loads(raw, object_pairs_hook=pairs)
            except (ValueError, TypeError):
                raise ValueError("AI_AUTHORIZATION_CONFIG_INVALID") from None
        if (not isinstance(raw, dict) or set(raw) != {
                "allow_sample_content_to_openai", "model", "max_requests", "credential_source"}
                or raw["allow_sample_content_to_openai"] is not True):
            raise ValueError("AI_AUTHORIZATION_CONFIG_INVALID")
        source = raw["credential_source"]
        if not isinstance(source, dict) or source.get("kind") not in {"environment", "file"}:
            raise ValueError("AI_CREDENTIAL_SOURCE_REQUIRED")
        key = "name" if source["kind"] == "environment" else "path"
        if set(source) != {"kind", key}:
            raise ValueError("AI_CREDENTIAL_SOURCE_INVALID")
        value = source.get(key)
        if not isinstance(value, str) or not value or len(value) > 4096:
            raise ValueError("AI_CREDENTIAL_SOURCE_INVALID")
        if key == "name" and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", value):
            raise ValueError("AI_CREDENTIAL_SOURCE_INVALID")
        if key == "path" and not Path(value).is_absolute():
            raise ValueError("AI_CREDENTIAL_SOURCE_MUST_BE_ABSOLUTE")
        if type(raw["max_requests"]) is not int or not 0 < raw["max_requests"] <= 2147483647:
            raise ValueError("AI_POSITIVE_CUMULATIVE_LIMIT_REQUIRED")
        # Reuse the established bounded model/config checks; no model choice here.
        ResponsesConfig(raw["model"], frozenset(), raw["max_requests"])
        return cls(raw["model"], raw["max_requests"], dict(source))

    def public_config(self):
        return {"allow_sample_content_to_openai": True, "model": self.model,
                "max_requests": self.max_requests, "credential_source": dict(self.credential_source)}

    def key_supplier(self):
        source = dict(self.credential_source)
        def read():
            if source["kind"] == "environment":
                return os.environ.get(source["name"], "")
            # Only this explicitly designated backend file; bounded, no dotenv expansion.
            with Path(source["path"]).open("r", encoding="utf-8") as handle:
                value = handle.read(8193)
            if len(value) > 8192:
                raise ValueError("AI_CREDENTIAL_UNAVAILABLE")
            return value.strip()
        return read


def assemble_runtime(repository, storage, *, reader_mode="REAL_SDK", authorization=None,
                     fixtures_dir=None):
    if reader_mode not in {"MOCK_SDK", "REAL_SDK"}:
        raise ValueError("INVALID_READER_MODE")
    if reader_mode == "REAL_SDK":
        reader = _real_sdk_reader()
        hashes = AUTHORIZED_DEMO_SAMPLE_SHA256
    else:
        reader = MockSDKReader(fixtures_dir)
        hashes = reader.sample_hashes
    extractor = None
    if authorization is not None:
        # Construct a lazy supplier. All-hit files do not read credentials or
        # reserve requests; both modes share the existing durable grant ID.
        config = ResponsesConfig(authorization.model, hashes, authorization.max_requests)
        repository.migrate()
        budget = PostgresAIBudget(repository, AUTHORIZATION_ID,
            model=config.model, max_requests=config.max_requests,
            authorized_sample_sha256=config.authorized_sample_sha256)
        extractor = ResponsesExtractor(config, authorization.key_supplier(), budget=budget)
    return IntegrationRuntime(repository, storage, reader=reader, extractor=extractor,
                              reader_mode=reader_mode)
