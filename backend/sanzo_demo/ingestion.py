"""Application orchestration. Verified wheel readers and explicit Mock readers share the same fact boundary.

Reader.read(path, document) is this application's internal port, NOT a guessed
public method of document-processing-sdk. Only tests inject mechanism readers.
"""
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from typing import Protocol

from sanzo_core.demo_store import DemoStore
from sanzo_core.domain import DecisionKind, decide, ConflictError, memory_key
from sanzo_core.ingestion_store import IngestionStore, task_field_bindings, task_ai_paused
from .ai import ExtractionError, ResponsesExtractor
from .extraction import CONDITIONS, ReadResult, applicable_value
from .facts import structure_facts, VERSION as FACT_VERSION
from jsonschema import Draft202012Validator
from .files import original_path


CLASSIFICATION_SCHEMA = {
    "type": "object", "properties": {"suggestions": {"type": "array", "items": {
        "type": "object", "properties": {"item_id": {"type": "string"},
            "matched_fee_id": {"type": ["string", "null"]},
            "suggested_fee_name": {"type": ["string", "null"]}},
        "required": ["item_id", "matched_fee_id", "suggested_fee_name"], "additionalProperties": False}}},
    "required": ["suggestions"], "additionalProperties": False,
}


def classification_reason(line, decision):
    """Route a name proposal independently of exact-memory eligibility.

    materialize() has already enforced F1 complete-field values and same-record
    ownership. Its primary source is the raw label's source. A verified label
    may support an unconfirmed name proposal even when other fields need review;
    this function never changes source/context readiness, decisions, or keys.
    """
    source = line.source_ref
    label = source.get("field_sources", {}).get("raw_label", {})
    if (source.get("text_verification") != "VERIFIED"
            or label.get("source_block_id") != line.observation.source_id
            or label.get("original_value") != line.observation.raw_label):
        return None
    if decision.kind == DecisionKind.AI_REQUIRED:
        return decision.reason
    if (decision.kind == DecisionKind.READING_REQUIRED
            and decision.reason == "FEE_NAME_SOURCE_UNVERIFIED"
            and source.get("sdk_table", {}).get("table_availability") == "LIMITED_EVIDENCE"):
        return "PROVISIONAL_NAME_LIMITED_SDK_TABLE"
    if (decision.kind == DecisionKind.REVIEW_REQUIRED
            and decision.reason == "INCOMPLETE_CONTEXT"):
        return "PROVISIONAL_NAME_INCOMPLETE_CONTEXT"
    if (decision.kind == DecisionKind.READING_REQUIRED
            and decision.reason == "SOURCE_OR_STRUCTURE_UNVERIFIED"):
        return "PROVISIONAL_NAME_OTHER_FIELDS_UNVERIFIED"
    # Confirmed reuse and conflicted/revoked memories retain their original
    # outcome; AI must not replace a human category or arbitrate a conflict.
    return None


def comparison_candidates(line, items, documents, authorized_hashes):
    """Bounded same-case display vocabulary, never a confirmed match or a catalog.

    Only saved outputs from currently authorized sample documents and the same
    reading origin may supply names. Return name/conditions only, not raw text,
    prices, identifiers, source metadata, or any tenant-wide fee list.
    """
    obs = line.observation
    if line.source_ref.get("document_sha256") not in authorized_hashes:
        return []
    permitted_documents = {doc["document_id"]: doc for doc in documents
        if doc.get("tenant_id") == obs.tenant_id and doc.get("case_id") == obs.case_id
        and doc.get("sha256") in authorized_hashes}
    def known(value):
        value = applicable_value(value)
        return value.strip() if isinstance(value, str) and value.strip() else None
    from sanzo_core.fee_policy import uses_fee_policy, line_conditions
    def semantic_context(context, source, kind):
        values = line_conditions(context, source) if uses_fee_policy(source, kind) else context
        return {key: known(values.get(key)) for key in CONDITIONS}
    proposed_context = semantic_context(vars(obs.context), line.source_ref, line.input_kind)
    candidates = {}
    for item in items:
        previous = item.get("observation", {})
        source = item.get("source_ref", {})
        document = permitted_documents.get(previous.get("document_id"))
        if (document is None or item.get("tenant_id") != obs.tenant_id
                or item.get("case_id") != obs.case_id
                or previous.get("tenant_id") != obs.tenant_id
                or previous.get("case_id") != obs.case_id
                or item.get("input_kind") != line.input_kind
                or source.get("origin") != line.source_ref.get("origin")
                or source.get("extraction_origin") != line.source_ref.get("extraction_origin")
                or source.get("document_id") != document["document_id"]
                or source.get("document_sha256") != document["sha256"]
                or item.get("current_memory_status") in {"CONFLICTED", "REVOKED"}):
            continue
        if item.get("current_fee_id"):
            if item.get("saved_status") not in {"HUMAN_CONFIRMED", "MEMORY_APPLIED"}:
                continue
            name = item.get("current_fee_name")
        else:
            name = item.get("ai_suggested_name")
        if not isinstance(name, str) or not name.strip() or len(name) > 1024:
            continue
        context = semantic_context(previous.get("context", {}), source, item.get("input_kind"))
        if any(proposed_context[key] is not None and context[key] is not None
               and proposed_context[key] != context[key] for key in CONDITIONS):
            continue
        candidate = {"name": name, "context": context}
        candidates[json.dumps(candidate, ensure_ascii=False, sort_keys=True)] = candidate
        # Do not truncate a candidate set into an apparently unique match.
        if len(candidates) > 50:
            return []
    return [candidates[key] for key in sorted(candidates)]


class SDKReaderPort(Protocol):
    def read(self, path: Path, document: dict) -> ReadResult: ...


class IntegrationRuntime:
    def __init__(self, repository, storage_dir, *, reader=None, extractor=None,
                 allow_mechanism_tests=False, reader_mode="REAL_SDK"):
        if reader_mode not in {"REAL_SDK", "MOCK_SDK"}:
            raise ValueError("INVALID_READER_MODE")
        if reader_mode == "MOCK_SDK":
            from .mock_reader import MockSDKReader
            if not isinstance(reader, MockSDKReader):
                raise ValueError("MOCK_READER_REQUIRED")
            if allow_mechanism_tests or (extractor is not None and (
                    not isinstance(extractor, ResponsesExtractor) or extractor._transport is not None)):
                raise ValueError("MOCK_MODE_REQUIRES_REAL_AI_CLIENT")
        self.reader_mode = reader_mode
        self.store = IngestionStore(repository)
        self.demo = DemoStore(repository)
        self.storage = Path(storage_dir)
        self.reader = reader
        self.extractor = extractor
        self.allow_mechanism_tests = allow_mechanism_tests

    def capabilities(self):
        missing = []
        if self.reader is None:
            missing.append("実 SDK reader は未接続です。自動的にデモ用解析には切り替えません")
        if self.extractor is None:
            missing.append("OpenAI への三つの虚構サンプル送信許可・モデル・累計上限・本機認証元が未確認です")
        mock = self.reader_mode == "MOCK_SDK"
        budget = getattr(self.extractor, "_budget", None)
        return {
            "reader_mode": self.reader_mode, "ai_mode": "OPENAI_API",
            "reader": {"ready": self.reader is not None, "mode": self.reader_mode,
                       "supported_sha256": sorted(self.reader.sample_hashes) if mock else None},
            "sdk": {"ready": self.reader is not None and not mock and not self.allow_mechanism_tests,
                    "reason": "SDK_NOT_CONNECTED_MOCK_MODE" if mock else (
                        None if self.reader else "SDK_PATH_AND_PUBLIC_CONTRACT_NOT_VERIFIED")},
            "ai": {"ready": self.extractor is not None, "mode": "OPENAI_API",
                   "reason": None if self.extractor else "OPENAI_AUTHORIZATION_NOT_CONFIRMED",
                   "budget": budget.snapshot() if budget is not None else None},
            "source_notice": "デモ用解析データ／SDK未接続" if mock else None,
            "missing_conditions": missing,
        }

    def review_field_sources(self, actor, document_id):
        """Read current SDK cells for a human role choice; no AI or new facts."""
        if self.reader_mode != "REAL_SDK" or not getattr(self.reader, "is_real_sdk", False):
            raise ValueError("REAL_SDK_FIELD_REVIEW_UNAVAILABLE")
        document = self.demo.get_document(actor, document_id)
        reading = self.reader.read(original_path(self.storage, document), document)
        reading.validate(document)
        original_path(self.storage, document)
        bound = {b["source_ref"].get("evidence_unit", {}).get("field_name") for b in reading.blocks}
        roles = [name for name in ("company", "transport_mode") if name not in bound]
        return {"document_id": document_id, "document_sha256": document["sha256"],
                "origin": reading.origin, "missing_roles": roles,
                "candidates": reading.sdk_identity.get("field_binding_candidates", []),
                "sdk_calls": reading.sdk_identity.get("public_calls", []),
                "reader_issues": list(reading.issues), "ai_requests": 0}

    def submit(self, actor, document_id, key, field_bindings=None, pause_ai=False):
        task = self.store.start_task(actor, document_id, key, field_bindings=field_bindings, pause_ai=pause_ai)
        if task["replayed"]:
            return task
        missing = self.capabilities()["missing_conditions"]
        if self.reader is None:
            return self.store.transition(actor, task["task_id"], "BLOCKED",
                error_code="INTEGRATION_PREREQUISITES_MISSING",
                metadata={"missing_conditions": missing, "sdk_calls": 0, "ai_calls": 0})
        return task

    def run(self, actor, task_id):
        task = self.store.get_task(actor, task_id)
        if task["state"] != "QUEUED":
            return task
        # Claim before error handling: losing a duplicate worker race must not
        # mark the winning worker's task FAILED.
        try:
            self.store.transition(actor, task_id, "READING")
        except ConflictError:
            return self.store.get_task(actor, task_id)
        evidence, ai_metadata, evidence_saved = None, {}, False
        try:
            document = self.demo.get_document(actor, task["document_id"])
            path = original_path(self.storage, document)
            reading = self.reader.read(path, document)
            if not isinstance(reading, ReadResult):
                raise ValueError("READER_DID_NOT_RETURN_APPLICATION_CONTRACT")
            reading.validate(document)
            # A failed HUMAN binding must not erase an actual SDK execution.
            evidence = {"origin": reading.origin, "document_sha256": reading.document_sha256,
                        "sdk_identity": reading.sdk_identity, "sources": list(reading.blocks),
                        "reader_issues": list(reading.issues)}
            bindings = task_field_bindings(task)
            if bindings:
                from .human_field_bindings import apply_human_bindings
                reading = apply_human_bindings(reading, document, actor, bindings)
            if (reading.origin == "MOCK_SDK") != (self.reader_mode == "MOCK_SDK"):
                raise ValueError("READER_MODE_ORIGIN_MISMATCH")
            if reading.origin == "MECHANISM_TEST" and not self.allow_mechanism_tests:
                raise ValueError("MECHANISM_READER_DISABLED")
            evidence = {
                "origin": reading.origin, "document_sha256": reading.document_sha256,
                "sdk_identity": reading.sdk_identity, "sources": list(reading.blocks),
                "reader_issues": list(reading.issues),
            }
            self.store.transition(actor, task_id, "EXTRACTING", metadata={
                "read_evidence": evidence,
                "read_output_sha256": sha256(json.dumps(evidence, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                "ai_call_requested": False, "fact_materialization": FACT_VERSION,
                "reader_mode": self.reader_mode, "real_sdk_calls": 1 if reading.origin == "REAL_SDK" else 0,
                "fixture_integrity": reading.sdk_identity.get("fixture_integrity") if reading.origin == "MOCK_SDK" else None,
            })
            evidence_saved = True
            # Facts never pass through OpenAI. F1 checks complete reader fields.
            lines, issues = structure_facts(document, actor.tenant_id, reading)
            ai_metadata = {"calls": [], "quotation_extraction_requests": 0,
                           "fact_materialization": FACT_VERSION, "memory_lookup_results": {}}
            expected_labels = {b["source_id"] for b in reading.blocks
                if b["source_ref"].get("evidence_unit", {}).get("field_name") == "raw_label"}
            actual_labels = {line.observation.source_id for line in lines}
            for source_id in sorted(expected_labels - actual_labels):
                issues.append({"code": "SOURCE_LINE_NOT_EXTRACTED", "source_block_id": source_id})
            unknown = []
            classification_reasons = {}
            with self.store.repository.transaction() as conn:
                for line in lines:
                    obs = line.observation
                    slot = self.store.repository.lookup_memory(conn, obs, line.source_ref, line.input_kind)
                    fee = self.store.repository.fee(conn, actor.tenant_id, slot["fee_id"]) if slot else None
                    decision = decide(obs, obs.unit_price, slot, fee, line.source_ref, line.input_kind)
                    ai_metadata["memory_lookup_results"][obs.item_id] = {
                        "decision": decision.kind.value, "reason": decision.reason,
                        "memory_key": slot["memory_key"] if slot else memory_key(obs, line.source_ref, line.input_kind),
                        "memory_resolution": slot.get("resolution", "DIRECT") if slot else None,
                        "memory_evidence_versions": slot.get("evidence_versions", []) if slot else [],
                        "memory_version": slot["version"] if slot else None,
                        "learning_event_id": slot["evidence_event_id"] if slot else None,
                    }
                    reason = classification_reason(line, decision)
                    if reason is not None:
                        classification_reasons[obs.item_id] = reason
                        from sanzo_core.fee_policy import uses_fee_policy, line_conditions
                        semantic_context = line_conditions(vars(obs.context), line.source_ref) if uses_fee_policy(line.source_ref, line.input_kind) else vars(obs.context)
                        unknown.append({"item_id": obs.item_id, "raw_label": obs.raw_label,
                                        "context": {k: semantic_context.get(k) for k in
                                            ("transport_mode", "equipment", "billing_unit", "charge_scope", "extra_terms")}})
            ai_metadata["classification_requested_reasons"] = classification_reasons
            ai_metadata["classification_requested_item_ids"] = []
            ai_metadata["classification_skipped_item_ids"] = [line.observation.item_id for line in lines
                if line.observation.item_id not in classification_reasons]
            if unknown and task_ai_paused(task):
                return self.store.transition(actor, task_id, "BLOCKED", error_code="AI_PROCESSING_PAUSED",
                    metadata={"reader_mode": self.reader_mode, "read_completed": True,
                              "ai_calls": 0, "ai_metadata": ai_metadata})
            if unknown and self.extractor is None:
                return self.store.transition(actor, task_id, "BLOCKED",
                    error_code="OPENAI_AUTHORIZATION_NOT_CONFIRMED",
                    metadata={"reader_mode": self.reader_mode,
                              "mock_read_completed": reading.origin == "MOCK_SDK",
                              "real_sdk_calls": int(reading.origin == "REAL_SDK"), "ai_calls": 0,
                              "ai_metadata": ai_metadata,
                              "missing_conditions": self.capabilities()["missing_conditions"]})
            if unknown:
                authorized_hashes = getattr(getattr(self.extractor, "config", None),
                                            "authorized_sample_sha256", frozenset())
                if authorized_hashes:
                    previous_items = self.demo.list_items(actor, document["case_id"])
                    case_documents = self.demo.list_documents(actor, document["case_id"])
                    by_item_id = {line.observation.item_id: line for line in lines}
                    for item in unknown:
                        candidates = comparison_candidates(by_item_id[item["item_id"]],
                            previous_items, case_documents, authorized_hashes)
                        if candidates:
                            item["comparison_candidates"] = candidates
                offered_by_item = {}
                if reading.origin == "REAL_SDK" and authorized_hashes:
                    with self.store.repository.transaction() as conn:
                        catalog = self.store.repository.canonical_fee_candidates(
                            conn, actor.tenant_id, authorized_hashes)
                    for item in unknown:
                        if catalog:
                            item["canonical_candidates"] = catalog
                        offered_by_item[item["item_id"]] = catalog
                ai_metadata["canonical_candidate_ids"] = {
                    item_id: [candidate["fee_id"] for candidate in candidates]
                    for item_id, candidates in offered_by_item.items()}
                ai_metadata["comparison_candidate_counts"] = {
                    item["item_id"]: len(item.get("comparison_candidates", [])) for item in unknown}
                ai_metadata["classification_requested_item_ids"] = [item["item_id"] for item in unknown]
                # Only price-free semantics for unknown IDs leave this boundary.
                proposal = self.extractor.extract(document["sha256"], unknown, CLASSIFICATION_SCHEMA,
                                                  purpose="fee_classification")
                ai_metadata["calls"].append(proposal.metadata)
                if proposal.metadata.get("execution_kind") == "MECHANISM_TEST" and not self.allow_mechanism_tests:
                    raise ValueError("MECHANISM_AI_DISABLED")
                if not Draft202012Validator(CLASSIFICATION_SCHEMA).is_valid(proposal.payload):
                    raise ValueError("AI_CLASSIFICATION_INVALID")
                suggestions = proposal.payload.get("suggestions")
                if not isinstance(suggestions, list):
                    raise ValueError("AI_CLASSIFICATION_INVALID")
                ids = [item.get("item_id") for item in suggestions if isinstance(item, dict)]
                if len(ids) != len(set(ids)) or set(ids) != {item["item_id"] for item in unknown}:
                    raise ValueError("AI_CLASSIFICATION_ID_MISMATCH")
                from sanzo_core.canonical_fees import resolve_matches
                current_catalog = []
                if any(item["matched_fee_id"] is not None for item in suggestions):
                    with self.store.repository.transaction() as conn:
                        current_catalog = self.store.repository.canonical_fee_candidates(
                            conn, actor.tenant_id, authorized_hashes)
                names, matches = resolve_matches(suggestions, offered_by_item, current_catalog)
                ai_metadata["canonical_matches"] = matches
                # Candidate IDs are unconfirmed proposals, never current fees/memory.
                revised = []
                for line in lines:
                    name = names.get(line.observation.item_id)
                    if name is not None:
                        line = replace(line, ai_suggested_name=name)
                        line.validate()
                    revised.append(line)
                lines = revised
            ai_metadata["classification_requested_item_ids"] = [item["item_id"] for item in unknown]
            ai_metadata["classification_skipped_item_ids"] = [line.observation.item_id for line in lines
                if line.observation.item_id not in {item["item_id"] for item in unknown}]
            # Check the original again before committing a result produced asynchronously.
            original_path(self.storage, document)
            self.store.transition(actor, task_id, "SAVING", metadata={"ai_metadata": ai_metadata})
            return self.store.finish(actor, task_id, lines, issues, evidence, ai_metadata,
                canonical_authorized_hashes=getattr(getattr(self.extractor, "config", None),
                    "authorized_sample_sha256", frozenset()))
        except ExtractionError as exc:
            return self.store.transition(actor, task_id, "FAILED", error_code=exc.code,
                metadata={"ai_metadata": ai_metadata, "failed_ai_attempt": exc.metadata,
                          "read_completed": evidence is not None,
                          "sdk_read_completed": evidence is not None and evidence["origin"] == "REAL_SDK",
                          "mock_read_completed": evidence is not None and evidence["origin"] == "MOCK_SDK"})
        except Exception as exc:
            # No exception text, paths, source content or credentials leak via error strings.
            safe_codes = {
                "READER_DID_NOT_RETURN_APPLICATION_CONTRACT", "MECHANISM_READER_DISABLED",
                "MECHANISM_AI_DISABLED", "AI_CLASSIFICATION_INVALID", "AI_CLASSIFICATION_ID_MISMATCH",
                "AI_CANONICAL_ID_NOT_OFFERED", "AI_CANONICAL_CANDIDATE_NO_LONGER_ACTIVE",
                "SDK_EVIDENCE_IDENTITY_MISMATCH", "INVALID_READER_ORIGIN", "SDK_BLOCK_LIMIT_OR_EMPTY",
                "INVALID_SOURCE_BLOCK", "DUPLICATE_OR_MISSING_SOURCE_ID", "INVALID_SOURCE_TEXT", "INVALID_READER_ISSUES",
                "SOURCE_DOCUMENT_MISMATCH", "UNVERIFIED_COORDINATES_FORBIDDEN",
                "INVALID_LOCATOR_STATUS", "INVALID_PDF_PAGE", "UNVERIFIED_PDF_COORDINATES_FORBIDDEN",
                "MOCK_SDK_UNSUPPORTED_FILE: 当前模拟模式仅支持三份指定样例",
                "MOCK_SDK_UPLOAD_HASH_MISMATCH", "MOCK_SDK_FIXTURE_HASH_MISMATCH",
                "MOCK_SDK_FIXTURE_IDENTITY_MISMATCH", "MOCK_SDK_FIXTURE_PATH_UNSAFE_OR_MISSING",
                "MOCK_SDK_INVALID_FIELD_KIND", "MOCK_SDK_FIELD_BOUNDARY_REQUIRED",
                "READER_MODE_ORIGIN_MISMATCH", "MOCK_FIXTURE_PROVENANCE_REQUIRED",
                "MOCK_SOURCE_IN_REAL_READING", "MOCK_FILE_NOT_SUPPORTED",
                "MOCK_DOCUMENT_HASH_MISMATCH", "MOCK_FIXTURE_HASH_MISMATCH",
                "EXACT_SHEET_REQUIRED", "INVALID_CELL_RANGE", "UNSUPPORTED_LOCATOR", "INVALID_LINE_COUNT",
            }
            code = str(exc) if type(exc) is ValueError and str(exc) in safe_codes else "PROCESSING_FAILED"
            return self.store.transition(actor, task_id, "FAILED", error_code=code[:200],
                metadata={"read_completed": evidence is not None,
                          "sdk_read_completed": evidence is not None and evidence["origin"] == "REAL_SDK",
                          "mock_read_completed": evidence is not None and evidence["origin"] == "MOCK_SDK", "ai_metadata": ai_metadata,
                          **({"read_evidence": evidence} if evidence is not None and not evidence_saved else {})})
