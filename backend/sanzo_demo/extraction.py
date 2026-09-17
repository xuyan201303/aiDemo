"""Application-owned ingestion contract; these are NOT SDK public API names.

Only a separately verified SDK adapter may produce real read evidence. AI values
must be grounded in the original block text; unknown amounts remain issues.
"""
from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
import re
import json
from uuid import uuid4

from sanzo_core.domain import Context, Observation, SDKObservedLine, decimal_text

CONDITIONS = ("transport_mode", "equipment", "billing_unit", "charge_scope", "extra_terms")
UNKNOWN_MARKERS = frozenset({"unknown", "unspecified", "tbd", "不明", "未定", "未確認", "未知", "未确认", "待定", "未記載", "未記入"})
def applicable_value(value):
    # Literal missing-value markers are unknown, never an unrestricted scope.
    # Explicit N/A / なし remain separate business values with their raw evidence.
    return None if isinstance(value, str) and value.strip().casefold() in UNKNOWN_MARKERS else value


FIELD = {"type": "object", "properties": {
    "value": {"type": ["string", "null"]},
    "source_block_id": {"type": ["string", "null"]},
}, "required": ["value", "source_block_id"], "additionalProperties": False}
LINE_PROPERTIES = {name: deepcopy(FIELD) for name in ("raw_label", "raw_value", "currency", "company")}
LINE_PROPERTIES["context"] = {"type": "object", "properties": {k: deepcopy(FIELD) for k in CONDITIONS},
                              "required": list(CONDITIONS), "additionalProperties": False}
EXTRACTION_SCHEMA = {
    "type": "object", "properties": {
        "lines": {"type": "array", "items": {"type": "object", "properties": LINE_PROPERTIES,
                   "required": list(LINE_PROPERTIES), "additionalProperties": False}},
        "document_issues": {"type": "array", "items": {"type": "string"}},
    }, "required": ["lines", "document_issues"], "additionalProperties": False,
}


@dataclass(frozen=True)
class ReadResult:
    """Adapter-neutral application envelope, not an assertion about SDK APIs."""
    document_sha256: str
    blocks: tuple[dict, ...]
    sdk_identity: dict
    origin: str = "REAL_SDK"
    issues: tuple[dict, ...] = ()

    def validate(self, document):
        if self.document_sha256 != document["sha256"] or not self.sdk_identity:
            raise ValueError("SDK_EVIDENCE_IDENTITY_MISMATCH")
        if self.origin not in {"REAL_SDK", "MOCK_SDK", "MECHANISM_TEST"}:
            raise ValueError("INVALID_READER_ORIGIN")
        if (not isinstance(self.issues, tuple) or len(self.issues) > 1000
                or any(not isinstance(issue, dict) or not isinstance(issue.get("code"), str)
                       or not issue["code"] or len(issue["code"]) > 200 for issue in self.issues)):
            raise ValueError("INVALID_READER_ISSUES")
        try:
            issue_bytes = json.dumps(self.issues, ensure_ascii=False, allow_nan=False).encode()
        except (ValueError, TypeError) as exc:
            raise ValueError("INVALID_READER_ISSUES") from exc
        if len(issue_bytes) > 256 * 1024:
            raise ValueError("INVALID_READER_ISSUES")
        # An unresolved real document may have no reliable fields. Preserve its
        # explicit reader issues/public output rather than inventing a block.
        if (not self.blocks and not self.issues) or len(self.blocks) > 5000:
            raise ValueError("SDK_BLOCK_LIMIT_OR_EMPTY")
        ids = set()
        for block in self.blocks:
            if set(block) != {"source_id", "raw_text", "source_ref"}:
                raise ValueError("INVALID_SOURCE_BLOCK")
            if not isinstance(block["source_id"], str) or not block["source_id"] or block["source_id"] in ids:
                raise ValueError("DUPLICATE_OR_MISSING_SOURCE_ID")
            ids.add(block["source_id"])
            if not isinstance(block["raw_text"], str) or len(block["raw_text"]) > 120000:
                raise ValueError("INVALID_SOURCE_TEXT")
            source = block["source_ref"]
            if source.get("document_id") != document["document_id"] or source.get("document_sha256") != document["sha256"]:
                raise ValueError("SOURCE_DOCUMENT_MISMATCH")
            if self.origin == "MOCK_SDK":
                if (source.get("origin") != "MOCK_SDK"
                        or self.sdk_identity.get("origin") != "MOCK_SDK"
                        or self.sdk_identity.get("fixture_integrity") != "VERIFIED"
                        or not self.sdk_identity.get("fixture_version")
                        or not re.fullmatch(r"[0-9a-f]{64}", self.sdk_identity.get("fixture_sha256", ""))):
                    raise ValueError("MOCK_FIXTURE_PROVENANCE_REQUIRED")
            elif source.get("origin") == "MOCK_SDK":
                raise ValueError("MOCK_SOURCE_IN_REAL_READING")
            validate_locator(source)


def validate_locator(source):
    status, locator = source.get("locator_status"), source.get("locator")
    if status == "UNAVAILABLE":
        if locator is not None:
            raise ValueError("UNVERIFIED_COORDINATES_FORBIDDEN")
        return
    if status not in {"VERIFIED", "PAGE_ONLY"} or not isinstance(locator, dict):
        raise ValueError("INVALID_LOCATOR_STATUS")
    if source.get("kind") == "PDF_PAGE":
        if type(locator.get("page_number")) is not int or locator["page_number"] < 1:
            raise ValueError("INVALID_PDF_PAGE")
        if set(locator) != {"page_number"}:
            raise ValueError("UNVERIFIED_PDF_COORDINATES_FORBIDDEN")
    elif source.get("kind") == "EXCEL_RANGE" and status == "VERIFIED":
        if not isinstance(locator.get("sheet_name"), str) or not locator["sheet_name"]:
            raise ValueError("EXACT_SHEET_REQUIRED")
        if not isinstance(locator.get("A1_range"), str) or not re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]{0,6}(?::[A-Z]{1,3}[1-9][0-9]{0,6})?", locator["A1_range"]):
            raise ValueError("INVALID_CELL_RANGE")
    else:
        raise ValueError("UNSUPPORTED_LOCATOR")


CURRENCY_NAMES = {"円": "JPY", "日本円": "JPY", **{c: c for c in
    ("JPY", "USD", "EUR", "CNY", "GBP", "AUD", "CAD", "CHF", "HKD", "SGD")}}
CURRENCY_AFFIXES = {
    **{name: frozenset({code}) for name, code in CURRENCY_NAMES.items()},
    "￥": frozenset({"JPY", "CNY"}), "¥": frozenset({"JPY", "CNY"}),
    "$": frozenset({"USD", "AUD", "CAD", "HKD", "SGD"}),
    "€": frozenset({"EUR"}), "£": frozenset({"GBP"}),
}
_AFFIX = "|".join(re.escape(v) for v in sorted(CURRENCY_AFFIXES, key=len, reverse=True))
_AMOUNT = re.compile(r"(?P<prefix>" + _AFFIX + r")?\s*"
    r"(?P<number>-?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?)"
    r"\s*(?P<suffix>" + _AFFIX + r")?")


def known_currency(raw):
    code = CURRENCY_NAMES.get(raw.strip()) if isinstance(raw, str) else None
    if code is None:
        raise ValueError("CURRENCY_UNKNOWN")
    return code


def known_amount(raw, currency=None):
    """Parse one entire field/token. Never search a page for a matching number.

    Only outer whitespace, valid comma grouping and declared currency affixes
    are removed. Decimal trailing zeros and signs are not rounded/coerced.
    Ambiguous symbols require the independently grounded current currency.
    """
    if not isinstance(raw, str):
        raise ValueError("AMOUNT_MISSING")
    match = _AMOUNT.fullmatch(raw.strip())
    if match is None:
        raise ValueError("AMOUNT_UNKNOWN_OR_AMBIGUOUS")
    for affix in (match["prefix"], match["suffix"]):
        if affix and currency not in CURRENCY_AFFIXES[affix]:
            raise ValueError("AMOUNT_CURRENCY_MISMATCH")
    try:
        return decimal_text(match["number"].replace(",", ""))
    except ValueError as exc:
        raise ValueError("AMOUNT_UNKNOWN_OR_AMBIGUOUS") from exc


def complete_field_unit(block, name):
    """Trusted-reader evidence only; never a public SDK API or AI declaration.

    raw_text must be a complete SDK field or a single original Excel cell.
    A PDF page/line text block alone does not establish a field boundary. An
    actual adapter must derive these units and record IDs from verified SDK
    structure; adding metadata to an AI-selected substring is not validation.
    Shared header fields need an explicit finite set of applicable record IDs.
    """
    source = block["source_ref"]
    unit = source.get("evidence_unit")
    if not isinstance(unit, dict) or unit.get("field_name") != name:
        raise ValueError("FIELD_BOUNDARY_UNVERIFIED")
    scope = unit.get("scope")
    record = unit.get("record_id")
    if not isinstance(record, str) or not record.strip() or len(record) > 1024:
        raise ValueError("FIELD_RECORD_UNVERIFIED")
    if scope == "DOCUMENT":
        targets = unit.get("applies_to_record_ids")
        if (name in {"raw_label", "raw_value"} or not isinstance(targets, list)
            or not 0 < len(targets) <= 500 or any(
                not isinstance(v, str) or not v.strip() or len(v) > 1024 for v in targets)):
            raise ValueError("FIELD_APPLICABILITY_UNVERIFIED")
    elif scope != "LINE":
        raise ValueError("FIELD_RECORD_UNVERIFIED")
    locator = source.get("locator") or {}
    if ((unit.get("kind") == "SDK_FIELD" or (
            unit.get("kind") == "FIXTURE_FIELD" and source.get("origin") == "MOCK_SDK"))
        and source.get("kind") == "PDF_PAGE"
        and source.get("locator_status") in {"VERIFIED", "PAGE_ONLY"}):
        location = ("PDF_PAGE", locator["page_number"])
    elif (unit.get("kind") == "EXCEL_CELL" and source.get("kind") == "EXCEL_RANGE"
          and source.get("locator_status") == "VERIFIED"):
        # A multi-cell range cannot certify one complete scalar field. Bind
        # line cells to the same physical row as well as the trusted record ID.
        cell = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]{0,6})", locator.get("A1_range", ""))
        if cell is None:
            raise ValueError("COMPLETE_SINGLE_CELL_REQUIRED")
        col = 0
        for char in cell[1]:
            col = col * 26 + ord(char) - ord("A") + 1
        if col > 16384 or int(cell[2]) > 1048576:
            raise ValueError("COMPLETE_SINGLE_CELL_REQUIRED")
        location = ("EXCEL_CELL", locator["sheet_name"], int(cell[2]))
    else:
        raise ValueError("FIELD_BOUNDARY_UNVERIFIED")
    raw = block["raw_text"]
    if not raw.strip() or len(raw) > 1024:
        raise ValueError("COMPLETE_FIELD_VALUE_REQUIRED")
    return unit, (*location, record)


def record_locations(reading):
    """Resolve header applicability without treating reused page-local IDs as global."""
    locations, field_roles, ambiguous = {}, set(), set()
    field_names = {"raw_label", "raw_value", "currency", "company", *CONDITIONS}
    for block in reading.blocks:
        unit = block["source_ref"].get("evidence_unit")
        if not isinstance(unit, dict) or unit.get("scope") != "LINE":
            continue
        name = unit.get("field_name")
        if not isinstance(name, str) or name not in field_names:
            continue
        try:
            _, key = complete_field_unit(block, name)
        except ValueError:
            continue
        locations.setdefault(unit["record_id"], set()).add(key)
        role = (key, name)
        if role in field_roles:
            # Duplicate complete fields cannot be assigned to one physical
            # quotation merely because an adapter reused the same record ID.
            ambiguous.add(key)
        field_roles.add(role)
    return locations, ambiguous


def require_same_record(evidence, locations, ambiguous):
    primary = evidence["raw_label"]
    if primary["record_key"] in ambiguous:
        raise ValueError("FIELD_RECORD_AMBIGUOUS")
    record = primary["evidence_unit"]["record_id"]
    for value in evidence.values():
        unit = value["evidence_unit"]
        if unit["scope"] == "LINE":
            if value["record_key"] != primary["record_key"]:
                raise ValueError("FIELD_RECORD_MISMATCH")
        else:
            targets = unit["applies_to_record_ids"]
            if (record not in targets or any(len(locations.get(target, ())) != 1 for target in targets)
                or locations.get(record) != {primary["record_key"]}
                or any(locations.get(target, set()) & ambiguous for target in targets)):
                raise ValueError("FIELD_APPLICABILITY_MISMATCH")


def materialize(document, tenant_id, reading: ReadResult, payload: dict):
    """Return validated rows and explicit issues. Does not persist or learn."""
    reading.validate(document)
    by_id = {b["source_id"]: b for b in reading.blocks}
    locations, ambiguous = record_locations(reading)
    entries = payload.get("lines")
    if not isinstance(entries, list) or len(entries) > 500:
        raise ValueError("INVALID_LINE_COUNT")
    issues, lines = [], []
    for index, entry in enumerate(entries):
        evidence = {}
        currency = None

        def field(name, value):
            if not isinstance(value, dict) or set(value) != {"value", "source_block_id"}:
                raise ValueError("INVALID_FIELD")
            text, source_id = value["value"], value["source_block_id"]
            if text is None and source_id is None:
                return None
            if not isinstance(text, str) or not text.strip() or len(text) > 1024 or source_id not in by_id:
                raise ValueError("FIELD_EVIDENCE_MISSING")
            block = by_id[source_id]
            unit, record_key = complete_field_unit(block, name)
            original = block["raw_text"]
            if name == "raw_value":
                matches = known_amount(text, currency) == known_amount(original, currency)
            elif name == "currency":
                matches = known_currency(text) == known_currency(original)
            else:
                # Exact complete value, permitting outer whitespace only.
                matches = text.strip() == original.strip()
            if not matches:
                raise ValueError("AI_VALUE_NOT_IN_SDK_ORIGINAL")
            evidence[name] = {"value": text, "original_value": original,
                              "source_block_id": source_id, "evidence_unit": deepcopy(unit),
                              "record_key": record_key}
            return original

        try:
            raw_label = field("raw_label", entry["raw_label"])
            currency_original = field("currency", entry["currency"])
            currency = known_currency(currency_original)
            raw_value = field("raw_value", entry["raw_value"])
            company = applicable_value(field("company", entry["company"]))
            context = {key: applicable_value(field(key, entry["context"][key])) for key in CONDITIONS}
            if raw_label is None:
                raise ValueError("ORIGINAL_LABEL_MISSING")
            require_same_record(evidence, locations, ambiguous)
            price = known_amount(raw_value, currency)
            primary_id = evidence["raw_label"]["source_block_id"]
            source = deepcopy(by_id[primary_id]["source_ref"])
            source.update(origin="MOCK_SDK" if reading.origin == "MOCK_SDK" else "SDK_OBSERVED", document_revision=1,
                          source_block_ids=list(dict.fromkeys(v["source_block_id"] for v in evidence.values())),
                          field_sources=evidence)
            if reading.origin == "MOCK_SDK":
                source.update(extraction_origin="MOCK_SDK",
                    fixture_version=reading.sdk_identity["fixture_version"],
                    fixture_sha256=reading.sdk_identity["fixture_sha256"],
                    fixture_integrity=reading.sdk_identity["fixture_integrity"],
                    source_verification="AI_FIELDS_MATCH_MOCK_ORIGINAL")
            observation = Observation(tenant_id=tenant_id, item_id=str(uuid4()), case_id=document["case_id"],
                document_id=document["document_id"], source_id=primary_id, forwarder_id=company,
                raw_label=raw_label, unit_price=price, currency=currency,
                context=Context(**context, ready=all(
                    context[k] is not None and k in evidence and by_id[evidence[k]["source_block_id"]]["source_ref"].get("text_verification") == "VERIFIED"
                    for k in CONDITIONS)),
                source_ready=all(k in evidence and by_id[evidence[k]["source_block_id"]]["source_ref"].get("text_verification") == "VERIFIED"
                    for k in ("raw_label", "raw_value", "currency", "company")))
            line = SDKObservedLine(observation=observation, source_ref=source, raw_value=raw_value,
                                   ai_suggested_name=None, input_kind="MOCK_SDK" if reading.origin == "MOCK_SDK" else "SDK_OBSERVED")
            line.validate()
            lines.append(line)
            if not observation.context.complete() or not company or not observation.source_ready:
                issues.append({"line_index": index, "code": "APPLICABILITY_INCOMPLETE", "item_id": observation.item_id})
        except (ValueError, KeyError, TypeError) as exc:
            issues.append({"line_index": index, "code": str(exc) if isinstance(exc, ValueError) else "INVALID_LINE_STRUCTURE"})
    for issue in payload.get("document_issues", []):
        if isinstance(issue, str) and issue.strip():
            issues.append({"code": "AI_DOCUMENT_REVIEW_NOTE", "message": issue[:2000]})
    if not lines and not issues:
        issues.append({"code": "NO_QUOTATION_LINES"})
    return lines, issues
