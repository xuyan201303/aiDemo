"""Explicit, local fixture reader; never imports or falls back to a real SDK.

The JSON contains only manually checked raw source fields. It supplies no AI
answers or learned classifications. The exact uploaded bytes select the fixture;
the file name is display metadata only. This is an application protocol.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re

from sanzo_demo.extraction import ReadResult

_HASH = re.compile(r"[0-9a-f]{64}")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_FIELDS = frozenset({
    "raw_label", "raw_value", "currency", "company", "transport_mode",
    "equipment", "billing_unit", "charge_scope", "extra_terms",
})
_FORBIDDEN = frozenset({
    "fee_id", "confirmed_fee_id", "confirmed_name", "confirmed_fee_name",
    "suggested_fee_name", "ai_response", "ai_result", "learning_hit",
    "memory_id", "memory_version", "match_result", "target_table",
})
_NOTICE = "デモ用解析データ／SDK未接続"


def _checked_json(data: bytes):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("MOCK_SDK_DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    try:
        return json.loads(data, object_pairs_hook=unique)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("MOCK_SDK_INVALID_FIXTURE_JSON") from exc


def _only_raw(value):
    if isinstance(value, dict):
        if _FORBIDDEN.intersection(value):
            raise ValueError("MOCK_SDK_ANSWER_DATA_FORBIDDEN")
        for nested in value.values():
            _only_raw(nested)
    elif isinstance(value, list):
        for nested in value:
            _only_raw(nested)


class MockSDKReader:
    """Hash-bound raw fixtures, enabled explicitly by the application runtime."""

    reader_mode = "MOCK_SDK"
    is_real_sdk = False
    max_sample_bytes = 25 * 1024 * 1024
    max_fixture_bytes = 2 * 1024 * 1024

    def __init__(self, fixtures_dir: Path | None = None):
        self.fixtures_dir = Path(fixtures_dir) if fixtures_dir is not None else (
            Path(__file__).resolve().parents[2] / "demo_materials" / "fixtures"
        )
        raw = self._read_file("manifest.json")
        manifest = _checked_json(raw)
        if (not isinstance(manifest, dict)
            or set(manifest) != {"schema_version", "origin", "fixture_version", "fixtures"}
            or manifest["schema_version"] != 1 or manifest["origin"] != "MOCK_SDK"
            or not isinstance(manifest["fixture_version"], str)
            or _VERSION.fullmatch(manifest["fixture_version"]) is None
            or not isinstance(manifest["fixtures"], list) or len(manifest["fixtures"]) != 3):
            raise ValueError("MOCK_SDK_INVALID_MANIFEST")
        self.fixture_version = manifest["fixture_version"]
        self.manifest_sha256 = sha256(raw).hexdigest()
        self._entries = {}
        names = set()
        for entry in manifest["fixtures"]:
            if (not isinstance(entry, dict)
                or set(entry) != {"sample_sha256", "sample_filename", "fixture_file", "fixture_sha256"}
                or any(not isinstance(entry[k], str) for k in entry)
                or _HASH.fullmatch(entry["sample_sha256"]) is None
                or _HASH.fullmatch(entry["fixture_sha256"]) is None
                or entry["sample_sha256"] in self._entries
                or entry["fixture_file"] in names
                or not re.fullmatch(r"[A-Za-z0-9_-]+\.raw\.json", entry["fixture_file"])):
                raise ValueError("MOCK_SDK_INVALID_MANIFEST_ENTRY")
            names.add(entry["fixture_file"])
            self._entries[entry["sample_sha256"]] = dict(entry)
        self.sample_hashes = frozenset(self._entries)

    def _read_file(self, filename):
        path = self.fixtures_dir / filename
        if (self.fixtures_dir.is_symlink() or path.is_symlink()
            or not path.is_file() or path.parent.resolve() != self.fixtures_dir.resolve()):
            raise ValueError("MOCK_SDK_FIXTURE_PATH_UNSAFE_OR_MISSING")
        with path.open("rb") as stream:
            data = stream.read(self.max_fixture_bytes + 1)
        if len(data) > self.max_fixture_bytes:
            raise ValueError("MOCK_SDK_FIXTURE_SIZE_LIMIT")
        return data

    def read(self, path, document):
        """Bind current document identity only after both byte hashes pass."""
        sample = Path(path)
        if sample.is_symlink() or not sample.is_file():
            raise ValueError("MOCK_SDK_UPLOAD_PATH_UNSAFE_OR_MISSING")
        digest, size = sha256(), 0
        with sample.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > self.max_sample_bytes:
                    raise ValueError("MOCK_SDK_UPLOAD_SIZE_LIMIT")
                digest.update(chunk)
        document_hash = digest.hexdigest()
        if document_hash != document.get("sha256"):
            raise ValueError("MOCK_SDK_UPLOAD_HASH_MISMATCH")
        entry = self._entries.get(document_hash)
        if entry is None:
            raise ValueError("MOCK_SDK_UNSUPPORTED_FILE: 当前模拟模式仅支持三份指定样例")
        if not isinstance(document.get("document_id"), str) or not document["document_id"]:
            raise ValueError("MOCK_SDK_DOCUMENT_ID_REQUIRED")
        data = self._read_file(entry["fixture_file"])
        if sha256(data).hexdigest() != entry["fixture_sha256"]:
            raise ValueError("MOCK_SDK_FIXTURE_HASH_MISMATCH")
        fixture = _checked_json(data)
        _only_raw(fixture)
        if (not isinstance(fixture, dict)
            or set(fixture) != {"schema_version", "origin", "fixture_version", "sample", "source_verification", "blocks"}
            or fixture["schema_version"] != 1 or fixture["origin"] != "MOCK_SDK"
            or fixture["fixture_version"] != self.fixture_version
            or not isinstance(fixture["sample"], dict)
            or fixture["sample"].get("sha256") != document_hash
            or fixture["sample"].get("filename") != entry["sample_filename"]
            or not isinstance(fixture["blocks"], list) or not fixture["blocks"]):
            raise ValueError("MOCK_SDK_FIXTURE_IDENTITY_MISMATCH")
        proof = {
            "origin": "MOCK_SDK", "extraction_origin": "MOCK_SDK",
            "application_reader": "MockSDKReader", "fixture_version": self.fixture_version,
            "fixture_sha256": entry["fixture_sha256"], "fixture_integrity": "VERIFIED",
            "manifest_sha256": self.manifest_sha256, "real_sdk_execution": "NOT_RUN",
            "notice": _NOTICE,
        }
        blocks = deepcopy(fixture["blocks"])
        for block in blocks:
            if not isinstance(block, dict) or set(block) != {"source_id", "raw_text", "source_ref"}:
                raise ValueError("MOCK_SDK_INVALID_RAW_BLOCK")
            source = block["source_ref"]
            if not isinstance(source, dict) or any(k in source for k in ("document_id", "document_sha256", "origin")):
                raise ValueError("MOCK_SDK_STATIC_DOCUMENT_ID_FORBIDDEN")
            unit = source.get("evidence_unit")
            if not isinstance(unit, dict) or unit.get("field_name") not in _FIELDS:
                raise ValueError("MOCK_SDK_FIELD_BOUNDARY_REQUIRED")
            if ((source.get("kind") == "PDF_PAGE" and unit.get("kind") != "FIXTURE_FIELD")
                or (source.get("kind") == "EXCEL_RANGE" and unit.get("kind") != "EXCEL_CELL")):
                raise ValueError("MOCK_SDK_INVALID_FIELD_KIND")
            source.update(proof)
            source.update(document_id=document["document_id"], document_sha256=document_hash,
                          source_block_ids=[block["source_id"]])
        reading = ReadResult(document_hash, tuple(blocks), proof, origin="MOCK_SDK")
        reading.validate(document)
        return reading
