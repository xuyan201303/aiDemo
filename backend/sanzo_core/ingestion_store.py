"""Atomic ingestion into the existing quotation and learning storage.

SDK/AI calls belong outside these transactions. The caller passes validated,
application-owned observations and bounded provenance, never an SDK public API.
Original documents, observations, decisions and attempt events stay immutable.
"""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
from typing import Callable
from uuid import uuid4

from psycopg.types.json import Jsonb

from .demo_store import DemoStore
from .domain import ConflictError, Principal, ScopeError, canonical_json, memory_key
from .postgres import PostgresRepository

ACTIVE = frozenset({"QUEUED", "READING", "EXTRACTING", "SAVING"})
COMPLETE = frozenset({"AVAILABLE", "REVIEW_REQUIRED"})
TERMINAL = COMPLETE | {"FAILED", "BLOCKED", "INTERRUPTED"}
TRANSITIONS = {
    "QUEUED": {"READING", "BLOCKED", "FAILED", "INTERRUPTED"},
    "READING": {"EXTRACTING", "SAVING", "BLOCKED", "FAILED", "INTERRUPTED"},
    "EXTRACTING": {"SAVING", "BLOCKED", "FAILED", "INTERRUPTED"},
    "SAVING": {"FAILED", "BLOCKED", "INTERRUPTED"},
}


def _text(value, name, maximum=200):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(name + " must be bounded nonempty text")
    return value


def _json(value, name, maximum=2 * 1024 * 1024):
    if not isinstance(value, (dict, list)):
        raise ValueError(name + " must be a JSON object or list")
    encoded = canonical_json(value)
    if len(encoded.encode("utf-8")) > maximum:
        raise ValueError(name + " exceeds evidence limit")
    # Attempts store token counts and request IDs, never credential values.
    forbidden = {"api_key", "authorization", "password", "secret", "access_token", "refresh_token"}
    def check(node):
        if isinstance(node, dict):
            for key, child in node.items():
                if key.lower() in forbidden:
                    raise ValueError("Credentials must not be stored in ingestion metadata")
                check(child)
        elif isinstance(node, list):
            for child in node:
                check(child)
    check(value)
    return value


def validate_field_bindings(value):
    """Optional role choices contain source IDs only, never factual overrides."""
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - {"company", "transport_mode"}:
        raise ValueError("INVALID_HUMAN_FIELD_BINDINGS")
    if any(not isinstance(v, str) or not v.strip() or len(v) > 1024 for v in value.values()):
        raise ValueError("INVALID_HUMAN_FIELD_BINDING_SOURCE_ID")
    if len(set(value.values())) != len(value):
        raise ValueError("HUMAN_FIELD_BINDING_DUPLICATE_SOURCE")
    return dict(value)


def task_field_bindings(task):
    """Read immutable first-attempt metadata; restarts do not invent approval."""
    queued = [event for event in task.get("events", [])
              if event.get("version") == 1 and event.get("state") == "QUEUED"]
    if len(queued) != 1:
        raise ValueError("TASK_FIELD_BINDING_EVENT_MISSING")
    metadata = queued[0].get("metadata") or {}
    bindings = validate_field_bindings(metadata.get("field_bindings"))
    if bindings and (metadata.get("field_bindings_origin") != "HUMAN"
                     or metadata.get("field_bindings_confirmed_by") != task.get("actor_id")
                     or metadata.get("document_sha256") != task.get("document_sha256")):
        raise ValueError("TASK_FIELD_BINDING_PROVENANCE_MISMATCH")
    return bindings


def task_ai_paused(task):
    """A per-request no-AI restriction survives process interruption/restart."""
    queued = [event for event in task.get("events", [])
              if event.get("version") == 1 and event.get("state") == "QUEUED"]
    if len(queued) != 1:
        raise ValueError("TASK_AI_PAUSE_EVENT_MISSING")
    paused = (queued[0].get("metadata") or {}).get("pause_ai", False)
    if type(paused) is not bool:
        raise ValueError("INVALID_TASK_AI_PAUSE")
    return paused


class IngestionStore:
    def __init__(self, repository: PostgresRepository, fault_hook: Callable | None = None):
        self.repository = repository
        self.demo = DemoStore(repository)
        self.fault_hook = fault_hook or (lambda stage: None)

    def _task(self, conn, actor: Principal, task_id: str, *, lock=False):
        actor.require_editor()
        row = conn.execute(
            "SELECT * FROM sanzo_core.ingestion_tasks WHERE tenant_id=%s AND task_id=%s",
            (actor.tenant_id, task_id),
        ).fetchone()
        if row is None:
            raise ScopeError("Task not available in authorized scope")
        actor.require_case(row["case_id"])
        if lock:
            self.repository.case(conn, actor.tenant_id, row["case_id"], lock=True)
            row = conn.execute(
                "SELECT * FROM sanzo_core.ingestion_tasks WHERE tenant_id=%s AND task_id=%s FOR UPDATE",
                (actor.tenant_id, task_id),
            ).fetchone()
        return row

    def _result(self, conn, task, *, replayed=False, deduplicated=False):
        value = dict(task)
        value["events"] = conn.execute(
            """SELECT version,state,error_code,metadata,created_at
               FROM sanzo_core.ingestion_events WHERE tenant_id=%s AND task_id=%s ORDER BY version""",
            (task["tenant_id"], task["task_id"]),
        ).fetchall()
        value["replayed"] = replayed
        value["deduplicated"] = deduplicated
        return value

    def _advance(self, conn, actor, task, state, error_code=None, metadata=None, result=None):
        version = task["version"] + 1
        row = conn.execute(
            """UPDATE sanzo_core.ingestion_tasks SET state=%s,version=%s,error_code=%s,
               result_json=%s,updated_at=current_timestamp WHERE tenant_id=%s AND task_id=%s
               RETURNING *""",
            (state, version, error_code, Jsonb(result) if result is not None else None,
             actor.tenant_id, task["task_id"]),
        ).fetchone()
        conn.execute(
            """INSERT INTO sanzo_core.ingestion_events(tenant_id,task_id,version,state,error_code,metadata)
               VALUES(%s,%s,%s,%s,%s,%s)""",
            (actor.tenant_id, task["task_id"], version, state, error_code, Jsonb(metadata or {})),
        )
        self.repository.advance_case(conn, actor.tenant_id, task["case_id"])
        return row

    def start_task(self, actor: Principal, document_id: str, idempotency_key: str,
                   field_bindings=None, pause_ai=False) -> dict:
        actor.require_editor()
        _text(idempotency_key, "Idempotency key")
        field_bindings = validate_field_bindings(field_bindings)
        if type(pause_ai) is not bool:
            raise ValueError("INVALID_TASK_AI_PAUSE")
        with self.repository.transaction() as conn:
            self.repository.advisory(conn, "ingestion-command", actor.tenant_id, idempotency_key)
            doc = self.demo._document(conn, actor, document_id)
            identity = asdict(actor)
            if identity["allowed_case_ids"] is not None:
                identity["allowed_case_ids"] = sorted(identity["allowed_case_ids"])
            request = {"actor": identity, "document_id": document_id, "document_sha256": doc["sha256"]}
            # Preserve legacy empty-binding digests and already saved requests.
            if field_bindings:
                request["field_bindings"] = field_bindings
            if pause_ai:
                request["pause_ai"] = True
            digest = sha256(canonical_json(request).encode("utf-8")).hexdigest()
            command = conn.execute(
                """SELECT request_hash,task_id FROM sanzo_core.ingestion_commands
                   WHERE tenant_id=%s AND idempotency_key=%s""",
                (actor.tenant_id, idempotency_key),
            ).fetchone()
            if command:
                if command["request_hash"] != digest:
                    raise ConflictError("Same processing request key has different content")
                return self._result(conn, self._task(conn, actor, command["task_id"]), replayed=True)
            self.repository.case(conn, actor.tenant_id, doc["case_id"], lock=True)
            existing = conn.execute(
                """SELECT * FROM sanzo_core.ingestion_tasks WHERE tenant_id=%s AND document_id=%s
                   AND (state=ANY(%s) OR result_json IS NOT NULL)
                   ORDER BY created_at DESC,task_id LIMIT 1""",
                (actor.tenant_id, document_id, sorted(ACTIVE)),
            ).fetchone()
            queued_metadata = {"document_sha256": doc["sha256"]}
            if pause_ai:
                queued_metadata["pause_ai"] = True
            if field_bindings:
                queued_metadata.update(field_bindings=field_bindings, field_bindings_origin="HUMAN",
                                       field_bindings_confirmed_by=actor.actor_id)
            if existing is None:
                task = conn.execute(
                    """INSERT INTO sanzo_core.ingestion_tasks
                       (tenant_id,task_id,case_id,document_id,document_sha256,actor_id,state)
                       VALUES(%s,%s,%s,%s,%s,%s,'QUEUED') RETURNING *""",
                    (actor.tenant_id, str(uuid4()), doc["case_id"], document_id, doc["sha256"], actor.actor_id),
                ).fetchone()
                conn.execute(
                    """INSERT INTO sanzo_core.ingestion_events(tenant_id,task_id,version,state,metadata)
                       VALUES(%s,%s,1,'QUEUED',%s)""",
                    (actor.tenant_id, task["task_id"], Jsonb(queued_metadata)),
                )
                self.repository.advance_case(conn, actor.tenant_id, doc["case_id"])
            else:
                task = existing
                existing_result = self._result(conn, task)
                if task_field_bindings(existing_result) != field_bindings:
                    raise ConflictError("Document processing already has different human field bindings")
                if task_ai_paused(existing_result) != pause_ai:
                    raise ConflictError("Document processing already has a different AI pause restriction")
            conn.execute(
                """INSERT INTO sanzo_core.ingestion_commands(tenant_id,idempotency_key,request_hash,task_id)
                   VALUES(%s,%s,%s,%s)""",
                (actor.tenant_id, idempotency_key, digest, task["task_id"]),
            )
            return self._result(conn, task, replayed=existing is not None, deduplicated=existing is not None)

    def transition(self, actor: Principal, task_id: str, status: str, error_code=None, metadata=None):
        _text(status, "Task status")
        if error_code is not None:
            _text(error_code, "Error code")
        # Real reader evidence may contain the bounded public SDK output as
        # well as field blocks. Match finish()'s existing 8 MiB evidence limit;
        # unrelated task metadata retains the original 2 MiB cap.
        maximum = (8 * 1024 * 1024 if isinstance(metadata, dict)
                   and isinstance(metadata.get("read_evidence"), dict) else 2 * 1024 * 1024)
        metadata = _json(metadata or {}, "Task metadata", maximum=maximum)
        with self.repository.transaction() as conn:
            task = self._task(conn, actor, task_id, lock=True)
            if status not in TRANSITIONS.get(task["state"], set()):
                raise ConflictError("Invalid or already completed processing transition")
            row = self._advance(conn, actor, task, status, error_code, metadata)
            return self._result(conn, row)

    def finish(self, actor: Principal, task_id: str, lines: list, issues: list[dict],
               read_evidence: dict, ai_metadata: dict, *, canonical_authorized_hashes=frozenset()) -> dict:
        if not isinstance(lines, list) or len(lines) > 500:
            raise ValueError("At most 500 validated observation lines required")
        if not isinstance(issues, list) or len(issues) > 1000:
            raise ValueError("Bounded issue list required")
        _json(issues, "Issues")
        _json(read_evidence, "Read evidence", 8 * 1024 * 1024)
        _json(ai_metadata, "AI metadata")
        sources = read_evidence.get("sources")
        if not isinstance(sources, list) or len(sources) > 5000:
            raise ValueError("Bounded SDK source evidence is required")
        if read_evidence.get("origin") not in {"REAL_SDK", "MECHANISM_TEST", "MOCK_SDK"}:
            raise ValueError("Actual SDK, explicit MOCK_SDK, or injected mechanism evidence required")
        source_ids = set()
        for source in sources:
            if not isinstance(source, dict):
                raise ValueError("Source must be an object")
            source_id = _text(source.get("source_id"), "Source ID", 1024)
            if source_id in source_ids:
                raise ValueError("Duplicate source identifier")
            source_ids.add(source_id)
            if not isinstance(source.get("raw_text"), str):
                raise ValueError("Source raw text required")
            if not isinstance(source.get("source_ref"), dict):
                raise ValueError("Source reference required")
        expected_input = "MOCK_SDK" if read_evidence["origin"] == "MOCK_SDK" else "SDK_OBSERVED"
        for line in lines:
            if line.input_kind != expected_input:
                raise ValueError("Read origin and observed input mode must agree")
            line.validate()
            if not set(line.source_ref["source_block_ids"]) <= source_ids:
                raise ValueError("Line references a source outside this SDK result")
            if line.observation.source_id not in line.source_ref["source_block_ids"]:
                raise ValueError("Observation is not bound to its source")
        if len({line.observation.item_id for line in lines}) != len(lines):
            raise ValueError("Duplicate observed item identifier")
        with self.repository.transaction() as conn:
            task = self._task(conn, actor, task_id, lock=True)
            self.repository.lock_fee_catalog(conn, actor.tenant_id)
            if task["state"] != "SAVING":
                raise ConflictError("Only a SAVING task can commit an import")
            doc = self.demo._document(conn, actor, task["document_id"])
            if read_evidence.get("document_sha256") != doc["sha256"]:
                raise ValueError("SDK evidence does not belong to the registered original")
            canonical_matches = ai_metadata.get("canonical_matches", {})
            if canonical_matches:
                current_catalog = {candidate["fee_id"]: candidate["name"] for candidate in
                    self.repository.canonical_fee_candidates(conn, actor.tenant_id, canonical_authorized_hashes)}
                for line in lines:
                    match = canonical_matches.get(line.observation.item_id)
                    if match and (match["fee_id"] not in current_catalog
                            or line.ai_suggested_name != current_catalog[match["fee_id"]]):
                        raise ValueError("AI_CANONICAL_CANDIDATE_NO_LONGER_ACTIVE")
            for source in sources:
                ref = source["source_ref"]
                if ref.get("document_id") != doc["document_id"] or ref.get("document_sha256") != doc["sha256"]:
                    raise ValueError("Source evidence belongs to another original")
                if ref.get("source_block_ids") != [source["source_id"]]:
                    raise ValueError("Source evidence must identify its own block")
                conn.execute(
                    """INSERT INTO sanzo_core.ingestion_sources
                       (tenant_id,task_id,source_id,raw_text,source_ref) VALUES(%s,%s,%s,%s,%s)""",
                    (actor.tenant_id, task_id, source["source_id"], source["raw_text"], Jsonb(ref)),
                )
            # All memory locks are acquired in key order to avoid opposite-row
            # deadlocks between imports in different cases sharing several keys.
            keys = sorted({memory_key(line.observation, line.source_ref, line.input_kind) for line in lines
                           if memory_key(line.observation, line.source_ref, line.input_kind) is not None})
            for key in keys:
                self.repository.advisory(conn, "memory", actor.tenant_id, key)
            item_ids = []
            for line in lines:
                item_ids.append(self.demo._insert_line(conn, actor, doc, line,
                    ai_matched_fee_id=ai_metadata.get("canonical_matches", {}).get(
                        line.observation.item_id, {}).get("fee_id")))
                self.fault_hook("after_item")
            items = [self.demo._item(conn, actor, item_id) for item_id in item_ids]
            matches = []
            for line, item in zip(lines, items):
                key = memory_key(line.observation, line.source_ref, line.input_kind)
                slot = self.repository.lookup_memory(conn, line.observation, line.source_ref, line.input_kind)
                matches.append({
                    "item_id": item["item_id"], "decision": item["initial_classification"],
                    "memory_key": slot["memory_key"] if slot else None,
                    "memory_resolution": slot.get("resolution", "DIRECT") if slot else None,
                    "memory_evidence_versions": slot.get("evidence_versions", []) if slot else [],
                    "memory_version": slot["version"] if slot else None,
                    "learning_event_id": slot["evidence_event_id"] if slot else None,
                    "memory_origin": slot["origin"] if slot else None,
                })
            result = {
                "item_ids": item_ids, "issues": issues, "read_evidence": read_evidence,
                "ai_metadata": ai_metadata, "classification_results": matches,
            }
            status = ("REVIEW_REQUIRED" if issues or not items
                      or any(item["saved_status"] != "MEMORY_APPLIED" for item in items)
                      else "AVAILABLE")
            self.fault_hook("before_finish")
            row = self._advance(conn, actor, task, status, metadata={
                "item_count": len(item_ids), "issue_count": len(issues),
                "read_origin": read_evidence["origin"],
            }, result=result)
            return self._result(conn, row)

    def get_task(self, actor: Principal, task_id: str) -> dict:
        with self.repository.transaction() as conn:
            return self._result(conn, self._task(conn, actor, task_id))

    def list_tasks(self, actor: Principal, case_id: str) -> list[dict]:
        actor.require_case(case_id)
        with self.repository.transaction() as conn:
            self.repository.case(conn, actor.tenant_id, case_id)
            rows = conn.execute(
                """SELECT * FROM sanzo_core.ingestion_tasks WHERE tenant_id=%s AND case_id=%s
                   ORDER BY created_at,task_id""", (actor.tenant_id, case_id),
            ).fetchall()
            return [self._result(conn, row) for row in rows]

    def get_task_sources(self, actor: Principal, task_id: str) -> list[dict]:
        with self.repository.transaction() as conn:
            self._task(conn, actor, task_id)
            return conn.execute(
                """SELECT source_id,raw_text,source_ref FROM sanzo_core.ingestion_sources
                   WHERE tenant_id=%s AND task_id=%s ORDER BY source_id""",
                (actor.tenant_id, task_id),
            ).fetchall()

    def document_states(self, actor: Principal, case_id: str) -> dict:
        actor.require_case(case_id)
        with self.repository.transaction() as conn:
            self.repository.case(conn, actor.tenant_id, case_id, lock=True)
            docs = conn.execute(
                """SELECT document_id FROM sanzo_core.documents WHERE tenant_id=%s AND case_id=%s""",
                (actor.tenant_id, case_id),
            ).fetchall()
            result = {}
            for doc in docs:
                task = conn.execute(
                    """SELECT * FROM sanzo_core.ingestion_tasks WHERE tenant_id=%s AND document_id=%s
                       ORDER BY created_at DESC,task_id DESC LIMIT 1""",
                    (actor.tenant_id, doc["document_id"]),
                ).fetchone()
                data = (task["result_json"] or {}) if task else {}
                result[doc["document_id"]] = {
                    "state": task["state"] if task else "RECEIVED",
                    "task_id": task["task_id"] if task else None,
                    "error_code": task["error_code"] if task else None,
                    "issues": data.get("issues", []), "item_ids": data.get("item_ids", []),
                }
            return result

    def interrupt_unfinished(self, actor: Principal) -> list[str]:
        """Call only once during startup of the single owner-managed local app."""
        actor.require_editor()
        with self.repository.transaction() as conn:
            rows = conn.execute(
                """SELECT task_id,case_id FROM sanzo_core.ingestion_tasks
                   WHERE tenant_id=%s AND state=ANY(%s) ORDER BY created_at,task_id""",
                (actor.tenant_id, sorted(ACTIVE)),
            ).fetchall()
        interrupted = []
        for row in rows:
            if actor.allowed_case_ids is not None and row["case_id"] not in actor.allowed_case_ids:
                continue
            try:
                self.transition(actor, row["task_id"], "INTERRUPTED",
                                error_code="APP_RESTARTED_BEFORE_COMPLETION")
                interrupted.append(row["task_id"])
            except ConflictError:
                # A completed attempt is immutable and remains completed.
                pass
        return interrupted

    def case_snapshot(self, actor: Principal, case_id: str) -> dict:
        """One case lock binds task/document status to the same saved item revision."""
        actor.require_case(case_id)
        with self.repository.transaction() as conn:
            case = self.repository.case(conn, actor.tenant_id, case_id, lock=True)
            item_ids = conn.execute(
                """SELECT item_id FROM sanzo_core.items WHERE tenant_id=%s AND case_id=%s
                   ORDER BY created_at,item_id""", (actor.tenant_id, case_id),
            ).fetchall()
            items = [self.demo._item(conn, actor, row["item_id"]) for row in item_ids]
            docs = conn.execute(
                """SELECT * FROM sanzo_core.documents WHERE tenant_id=%s AND case_id=%s
                   ORDER BY created_at,document_id""", (actor.tenant_id, case_id),
            ).fetchall()
            task_rows = conn.execute(
                """SELECT * FROM sanzo_core.ingestion_tasks WHERE tenant_id=%s AND case_id=%s
                   ORDER BY created_at,task_id""", (actor.tenant_id, case_id),
            ).fetchall()
            tasks = [self._result(conn, row) for row in task_rows]
            latest = {task["document_id"]: task for task in tasks}
            for doc in docs:
                task = latest.get(doc["document_id"])
                doc["legacy_state"] = doc["state"]
                doc["state"] = task["state"] if task else "RECEIVED"
                doc["task_id"] = task["task_id"] if task else None
                doc["blocked_reason"] = task["error_code"] if task else None
                result = (task["result_json"] or {}) if task else {}
                doc["issues"] = result.get("issues", [])
                doc["imported_item_ids"] = result.get("item_ids", [])
            fees = conn.execute(
                """SELECT fee_id,display_name,name_version FROM sanzo_core.fees
                   WHERE tenant_id=%s AND NOT retired ORDER BY display_name,fee_id""", (actor.tenant_id,),
            ).fetchall()
            from .quote_confirmations import attach_document_review_events
            attach_document_review_events(conn, actor, docs)
            return {"case": case, "items": items, "documents": docs, "fees": fees, "tasks": tasks}
