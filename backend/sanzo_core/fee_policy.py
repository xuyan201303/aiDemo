"""Versioned REAL fee-name memory, separate from document-profile readiness.

Only already F1-grounded fields enter this policy. No SDK/AI/DB operations here.
Unknown line conditions are explicit nulls in an exact signature, not wildcards.
"""
from hashlib import sha256
from copy import deepcopy
from .domain import canonical_json, normalize_label

POLICY = "fee-name-v2"
PREFIX = POLICY + ":"
LINE_CONDITIONS = ("equipment", "billing_unit", "charge_scope", "extra_terms")


def verified_source(source):
    """Readable cell text does not establish a limited table's authority."""
    return (source.get("text_verification") == "VERIFIED"
            and source.get("sdk_table", {}).get("table_availability", "AVAILABLE") == "AVAILABLE")


def uses_fee_policy(source, input_kind):
    return (input_kind == "SDK_OBSERVED" and isinstance(source, dict)
            and source.get("origin") == "SDK_OBSERVED"
            and source.get("extraction_origin") == "REAL_SDK")


def line_conditions(context, source):
    """Keep only complete verified fields belonging to this fee record."""
    result = {}
    fields = (source or {}).get("field_sources", {})
    primary = fields.get("raw_label", {})
    for name in LINE_CONDITIONS:
        value = context.get(name)
        entry = fields.get(name, {})
        unit = entry.get("evidence_unit", {})
        if (isinstance(value, str) and value.strip()
                and entry.get("value") == value and entry.get("original_value") == value
                and unit.get("scope") == "LINE" and unit.get("field_name") == name
                and entry.get("record_key") == primary.get("record_key")
                and unit.get("record_id") == primary.get("evidence_unit", {}).get("record_id")):
            result[name] = normalize_label(value)
        else:
            result[name] = None
    return result


def signature(observation, source, input_kind):
    if not uses_fee_policy(source, input_kind):
        return None
    field = source.get("field_sources", {}).get("raw_label", {})
    unit = field.get("evidence_unit", {})
    if (not verified_source(source)
            or source.get("document_id") != observation.document_id
            or field.get("original_value") != observation.raw_label
            or field.get("value") != observation.raw_label
            or field.get("source_block_id") != observation.source_id
            or unit.get("scope") != "LINE" or unit.get("field_name") != "raw_label"
            or not unit.get("record_id") or not field.get("record_key")
            or field["record_key"][-1] != unit["record_id"]
            or source.get("evidence_unit") != unit
            or observation.source_id not in source.get("source_block_ids", [])):
        return None
    return {"policy": POLICY, "normalization": "nfc-whitespace-v1",
            "tenant": observation.tenant_id, "raw_label": normalize_label(observation.raw_label),
            "conditions": line_conditions(vars(observation.context), source)}


def key(observation, source, input_kind):
    value = signature(observation, source, input_kind)
    return PREFIX + sha256(canonical_json(value).encode()).hexdigest() if value else None


def name_evidence(observation, source):
    """Do not copy amount/currency SDK cells into name memory. Full originals
    remain on the immutable quote and correction event, referenced by item ID.
    """
    conditions = line_conditions(vars(observation.context), source)
    fields = source["field_sources"]
    return {"policy": POLICY, "origin": "REAL_SDK",
            "document_id": observation.document_id,
            "document_sha256": source["document_sha256"],
            "kind": source["kind"], "locator": deepcopy(source["locator"]),
            "locator_status": source.get("locator_status"),
            "fields": {name: {k: deepcopy(entry[k]) for k in
                ("source_block_id", "original_value", "record_key", "evidence_unit") if k in entry}
                for name, entry in fields.items()
                if name == "raw_label" or name in conditions and conditions[name] is not None}}
