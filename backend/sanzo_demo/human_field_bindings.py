"""Explicit human confirmation of roles for complete reader-owned header cells.

Only company and transport roles may be supplied. Values, prices and source
locations are never accepted from the browser. Confirmation is local to this
uploaded document and remains HUMAN provenance, not SDK semantic recognition.
"""
from copy import deepcopy
from dataclasses import replace

from sanzo_core.ingestion_store import validate_field_bindings
from .extraction import complete_field_unit, record_locations


def apply_human_bindings(reading, document, actor, bindings):
    bindings = validate_field_bindings(bindings)
    if not bindings:
        return reading
    actor.require_case(document["case_id"])
    if reading.origin != "REAL_SDK":
        raise ValueError("HUMAN_FIELD_BINDING_REQUIRES_REAL_SDK")
    reading.validate(document)
    candidates = reading.sdk_identity.get("field_binding_candidates", [])
    if not isinstance(candidates, list) or len(candidates) > 5000:
        raise ValueError("INVALID_FIELD_BINDING_CANDIDATES")
    candidate_map = {}
    for candidate in candidates:
        if not isinstance(candidate, dict) or set(candidate) != {"source_id", "raw_text", "source_ref"}:
            raise ValueError("INVALID_FIELD_BINDING_CANDIDATE")
        source_id = candidate["source_id"]
        if not isinstance(source_id, str) or not source_id or source_id in candidate_map:
            raise ValueError("AMBIGUOUS_FIELD_BINDING_CANDIDATE")
        candidate_map[source_id] = candidate
    locations, ambiguous = record_locations(reading)
    label_records = {}
    for block in reading.blocks:
        unit = block["source_ref"].get("evidence_unit", {})
        if unit.get("field_name") == "raw_label" and unit.get("scope") == "LINE":
            checked, key = complete_field_unit(block, "raw_label")
            label_records.setdefault(checked["record_id"], set()).add(key)
    source_ids = {block["source_id"] for block in reading.blocks}
    additions = []
    confirmations = []
    selected_cells = set()
    resolved_roles = set()
    for field_name, source_id in sorted(bindings.items()):
        if source_id not in candidate_map or source_id in source_ids:
            raise ValueError("FIELD_BINDING_SOURCE_UNAVAILABLE")
        if any(block["source_ref"].get("evidence_unit", {}).get("field_name") == field_name
               for block in reading.blocks):
            raise ValueError("FIELD_BINDING_CANNOT_OVERRIDE_FACT")
        block = deepcopy(candidate_map[source_id])
        if not isinstance(block["raw_text"], str) or not block["raw_text"].strip() or len(block["raw_text"]) > 1024:
            raise ValueError("FIELD_BINDING_COMPLETE_VALUE_REQUIRED")
        ref = block["source_ref"]
        if not isinstance(ref, dict):
            raise ValueError("INVALID_FIELD_BINDING_SOURCE")
        if (ref.get("origin") != "REAL_SDK" or ref.get("document_id") != document["document_id"]
                or ref.get("document_sha256") != document["sha256"]):
            raise ValueError("FIELD_BINDING_DOCUMENT_MISMATCH")
        if (ref.get("kind") != "EXCEL_RANGE" or ref.get("locator_status") != "VERIFIED"
                or ref.get("text_verification") != "VERIFIED"):
            raise ValueError("FIELD_BINDING_COMPLETE_CELL_REQUIRED")
        unit = ref.get("evidence_unit")
        if (not isinstance(unit, dict) or unit.get("kind") != "EXCEL_CELL"
                or unit.get("scope") != "DOCUMENT" or "field_name" in unit):
            raise ValueError("FIELD_BINDING_UNBOUND_HEADER_REQUIRED")
        unit["field_name"] = field_name
        checked, header_key = complete_field_unit(block, field_name)
        cell_identity = (ref["locator"]["sheet_name"], ref["locator"]["A1_range"])
        if cell_identity in selected_cells:
            raise ValueError("HUMAN_FIELD_BINDING_DUPLICATE_CELL")
        selected_cells.add(cell_identity)
        resolved_roles.add((cell_identity[0], field_name))
        targets = checked["applies_to_record_ids"]
        if len(set(targets)) != len(targets):
            raise ValueError("FIELD_BINDING_APPLICABILITY_UNVERIFIED")
        for target in targets:
            keys = label_records.get(target, set())
            if (len(keys) != 1 or locations.get(target) != keys or keys & ambiguous
                    or next(iter(keys))[:2] != header_key[:2]):
                raise ValueError("FIELD_BINDING_APPLICABILITY_UNVERIFIED")
        provenance = {"origin": "HUMAN", "confirmed_by": actor.actor_id,
                      "document_id": document["document_id"],
                      "document_sha256": document["sha256"],
                      "source_id": source_id, "field_name": field_name}
        unit["role_origin"] = "HUMAN"
        unit["role_confirmed_by"] = actor.actor_id
        ref["role_origin"] = "HUMAN"
        ref["human_field_confirmation"] = provenance
        additions.append(block)
        confirmations.append(provenance)
    identity = deepcopy(reading.sdk_identity)
    identity["human_field_bindings"] = confirmations
    identity["human_field_confirmations"] = confirmations
    issues = []
    for original in reading.issues:
        issue = deepcopy(original)
        if (issue.get("code") == "EXTRACTION_UNRESOLVED"
                and issue.get("reason") == "FIELD_HEADER_NOT_BOUND"
                and (issue.get("sheet_name"), issue.get("field")) in resolved_roles):
            issue.update(resolution="HUMAN_BOUND", resolved_by=actor.actor_id)
        issues.append(issue)
    result = replace(reading, blocks=(*reading.blocks, *additions), sdk_identity=identity,
                     issues=tuple(issues))
    result.validate(document)
    return result
