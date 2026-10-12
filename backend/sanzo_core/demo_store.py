"""Thin Demo persistence on the W1 PostgreSQL repository and rule engine.

Real SDK reading is not connected. Uploaded documents remain BLOCKED.
Manual entry records an explicit human confirmation, with no invented locator.
Synthetic input is an internal test seam and must never have an HTTP route.
"""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import PurePosixPath
import re
from typing import Sequence
from uuid import uuid4

from psycopg.types.json import Jsonb

from .domain import (
    Context, DecisionKind, ManualConfirmedLine, Observation, Principal, ScopeError,
    SyntheticLine, decide, memory_key,
)
from .postgres import PostgresRepository

MANUAL_ACKNOWLEDGMENT = "I_CONFIRMED_ORIGINAL_LABEL_AND_CONTEXT_FROM_UPLOADED_DOCUMENT"
CONTEXT_FIELDS = frozenset({
    "transport_mode", "equipment", "billing_unit", "charge_scope", "extra_terms",
})


class DemoStore:
    def __init__(self, repository: PostgresRepository):
        self.repository = repository

    def get_case(self, actor: Principal, case_id: str) -> dict:
        actor.require_case(case_id)
        with self.repository.transaction() as conn:
            return self.repository.case(conn, actor.tenant_id, case_id)

    def list_cases(self, actor: Principal) -> list[dict]:
        actor.require_editor()
        with self.repository.transaction() as conn:
            if actor.allowed_case_ids is not None:
                return conn.execute("""
                    SELECT tenant_id,case_id,name,case_revision,created_at,updated_at
                    FROM sanzo_core.cases WHERE tenant_id=%s AND case_id=ANY(%s)
                    ORDER BY created_at DESC,case_id
                """, (actor.tenant_id, sorted(actor.allowed_case_ids))).fetchall()
            return conn.execute("""
                SELECT tenant_id,case_id,name,case_revision,created_at,updated_at
                FROM sanzo_core.cases WHERE tenant_id=%s ORDER BY created_at DESC,case_id
            """, (actor.tenant_id,)).fetchall()

    def list_fees(self, actor: Principal) -> list[dict]:
        actor.require_editor()
        with self.repository.transaction() as conn:
            return conn.execute("""
                SELECT fee_id,display_name,name_version FROM sanzo_core.fees
                WHERE tenant_id=%s AND NOT retired ORDER BY display_name,fee_id
            """, (actor.tenant_id,)).fetchall()

    def register_document(
        self, actor: Principal, case_id: str, *, document_id: str,
        original_filename: str, media_type: str, size_bytes: int, sha256: str,
        storage_key: str, blocked_reason: str = "SDK_NOT_CONFIGURED",
    ) -> dict:
        actor.require_case(case_id)
        for value in (document_id, original_filename, media_type, storage_key, blocked_reason):
            if not isinstance(value, str) or not value.strip() or len(value) > 1000:
                raise ValueError("Nonempty bounded document metadata required")
        if type(size_bytes) is not int or not 0 < size_bytes <= 20 * 1024 * 1024:
            raise ValueError("Document size must be between 1 byte and 20 MiB")
        if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ValueError("SHA256 digest required")
        path = PurePosixPath(storage_key)
        if path.is_absolute() or ".." in path.parts or "\\" in storage_key:
            raise ValueError("Storage key must be a server-owned relative path")
        if any(ord(c) < 32 for c in original_filename + storage_key):
            raise ValueError("Control characters are not allowed in file metadata")
        with self.repository.transaction() as conn:
            self.repository.case(conn, actor.tenant_id, case_id, lock=True)
            row = conn.execute("""
                INSERT INTO sanzo_core.documents(
                    tenant_id,document_id,case_id,original_filename,media_type,
                    size_bytes,sha256,storage_key,blocked_reason)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING *
            """, (actor.tenant_id, document_id, case_id, original_filename, media_type,
                  size_bytes, sha256, storage_key, blocked_reason)).fetchone()
            self.repository.advance_case(conn, actor.tenant_id, case_id)
            return row

    def _document(self, conn, actor: Principal, document_id: str) -> dict:
        actor.require_editor()
        row = conn.execute("""
            SELECT * FROM sanzo_core.documents WHERE tenant_id=%s AND document_id=%s
        """, (actor.tenant_id, document_id)).fetchone()
        if row is None:
            raise ScopeError("Document not available in authorized scope")
        actor.require_case(row["case_id"])
        return row

    def get_document(self, actor: Principal, document_id: str) -> dict:
        with self.repository.transaction() as conn:
            return self._document(conn, actor, document_id)

    def list_documents(self, actor: Principal, case_id: str) -> list[dict]:
        actor.require_case(case_id)
        with self.repository.transaction() as conn:
            self.repository.case(conn, actor.tenant_id, case_id)
            return conn.execute("""
                SELECT * FROM sanzo_core.documents WHERE tenant_id=%s AND case_id=%s
                ORDER BY created_at,document_id
            """, (actor.tenant_id, case_id)).fetchall()

    def _insert_line(self, conn, actor: Principal, document: dict, line, *, ai_matched_fee_id=None) -> str:
        line.validate()
        obs = line.observation
        if (obs.tenant_id, obs.case_id, obs.document_id) != (
            actor.tenant_id, document["case_id"], document["document_id"]
        ):
            raise ScopeError("Line is not bound to the registered document and case")
        if line.source_ref and line.source_ref.get("document_sha256") != document["sha256"]:
            raise ValueError("Line source hash differs from registered document")
        self.repository.lock_fee_catalog(conn, actor.tenant_id)
        self.repository.insert_observation(conn, line)
        key = memory_key(obs, line.source_ref, line.input_kind)
        # Serialize snapshot resolution with correction/revocation writers.
        if key is not None:
            self.repository.advisory(conn, "memory", actor.tenant_id, key)
        slot = self.repository.lookup_memory(conn, obs, line.source_ref, line.input_kind)
        fee = self.repository.fee(conn, actor.tenant_id, slot["fee_id"]) if slot else None
        decision = decide(obs, obs.unit_price, slot, fee, line.source_ref, line.input_kind)
        if decision.kind == DecisionKind.MEMORY_APPLIED:
            conn.execute("""
                UPDATE sanzo_core.items SET current_fee_id=%s
                WHERE tenant_id=%s AND item_id=%s
            """, (decision.fee_id, actor.tenant_id, obs.item_id))
        conn.execute("""
            INSERT INTO sanzo_core.item_documents(tenant_id,item_id,case_id,document_id)
            VALUES(%s,%s,%s,%s)
        """, (actor.tenant_id, obs.item_id, obs.case_id, obs.document_id))
        conn.execute("""
            INSERT INTO sanzo_core.item_classifications(
                tenant_id,item_id,decision_json,memory_key,memory_version)
            VALUES(%s,%s,%s,%s,%s)
        """, (actor.tenant_id, obs.item_id, Jsonb({**asdict(decision), **({"ai_matched_fee_id": ai_matched_fee_id}
                  if ai_matched_fee_id and decision.kind != DecisionKind.MEMORY_APPLIED else {})}),
              slot["memory_key"] if slot else None, slot["version"] if slot else None))
        return obs.item_id

    def add_manual_line(self, actor: Principal, document_id: str, data: dict) -> dict:
        """Explicit user transcription, not an SDK/AI result.

        Only this action's acknowledgement authorizes a human source confirmation.
        The server obtains the document/hash and constructs source/context gates.
        Unknown context remains unknown and prevents automatic classification reuse.
        """
        actor.require_editor()
        allowed = {
            "raw_label", "unit_price", "currency", "forwarder_id", "context",
            "source_acknowledgment", "raw_value", "quotation_date", "valid_until",
        }
        if not isinstance(data, dict) or set(data) - allowed:
            raise ValueError("Unsupported manual field or client trust flag")
        if data.get("source_acknowledgment") != MANUAL_ACKNOWLEDGMENT:
            raise ValueError("Explicit confirmation of the original document is required")
        context_values = data.get("context")
        if not isinstance(context_values, dict) or set(context_values) != CONTEXT_FIELDS:
            raise ValueError("All five applicability fields must be explicit; unknown is null")
        for value in context_values.values():
            if value is not None and (
                not isinstance(value, str) or not value.strip() or len(value) > 1024
            ):
                raise ValueError("Context must be bounded text or explicit null")
        forwarder = data.get("forwarder_id")
        if forwarder is not None and (
            not isinstance(forwarder, str) or not forwarder.strip() or len(forwarder) > 1024
        ):
            raise ValueError("Company must be bounded text or explicit null")
        with self.repository.transaction() as conn:
            document = self._document(conn, actor, document_id)
            self.repository.case(conn, actor.tenant_id, document["case_id"], lock=True)
            source_id = str(uuid4())
            observation = Observation(
                tenant_id=actor.tenant_id, item_id=str(uuid4()), case_id=document["case_id"],
                document_id=document_id, source_id=source_id, forwarder_id=forwarder,
                raw_label=data.get("raw_label"), unit_price=data.get("unit_price"),
                currency=data.get("currency"), context=Context(**context_values, ready=True),
                source_ready=True,
            )
            if not isinstance(observation.currency, str):
                raise ValueError("Currency must be explicit three-letter text")
            source_ref = {
                "origin": "MANUAL_CONFIRMED", "document_id": document_id,
                "document_revision": 1, "document_sha256": document["sha256"],
                "source_block_ids": [source_id], "kind": "MANUAL_DOCUMENT_CONFIRMATION",
                "locator_status": "UNAVAILABLE", "locator": None,
                "manual_confirmed_by": actor.actor_id,
                "confirmation": MANUAL_ACKNOWLEDGMENT,
            }
            line = ManualConfirmedLine(
                observation=observation, source_ref=source_ref,
                raw_value=data.get("raw_value"), quotation_date=data.get("quotation_date"),
                valid_until=data.get("valid_until"),
            )
            item_id = self._insert_line(conn, actor, document, line)
            self.repository.advance_case(conn, actor.tenant_id, document["case_id"])
            return self._item(conn, actor, item_id)

    def add_synthetic_lines(
        self, actor: Principal, document_id: str, lines: Sequence[SyntheticLine],
    ) -> list[dict]:
        """INJECTED_TEST_CASE only. No application HTTP route may expose this."""
        actor.require_editor()
        if not lines or len(lines) > 500 or any(type(line) is not SyntheticLine for line in lines):
            raise ValueError("Bounded explicitly synthetic lines required")
        with self.repository.transaction() as conn:
            document = self._document(conn, actor, document_id)
            self.repository.case(conn, actor.tenant_id, document["case_id"], lock=True)
            ids = [self._insert_line(conn, actor, document, line) for line in lines]
            self.repository.advance_case(conn, actor.tenant_id, document["case_id"])
            return [self._item(conn, actor, item_id) for item_id in ids]

    def _item(self, conn, actor: Principal, item_id: str) -> dict:
        item = self.repository.item(conn, actor.tenant_id, item_id)
        observation = Observation.from_json(item["observation"])
        actor.require_case(item["case_id"])
        item["observation"] = json.loads(item["observation"])
        # Human applicability changes are an overlay over immutable extraction
        # evidence. Keep the REAL line-evidence gate before applying an explicit
        # correction; an unrelated unverified SDK field must not become known.
        from .fee_policy import uses_fee_policy, line_conditions
        context = dict(item["observation"].get("context") or {})
        effective = (line_conditions(context, item["source_ref"])
                     if uses_fee_policy(item["source_ref"], item["input_kind"])
                     else context.copy())
        overrides = self.repository.condition_overlays(conn, actor.tenant_id, item_id)
        if overrides:
            notes = conn.execute("""SELECT event_id,evidence_note FROM sanzo_core.condition_correction_events
                WHERE tenant_id=%s AND event_id=ANY(%s)""",
                (actor.tenant_id, [entry["event_id"] for entry in overrides.values()])).fetchall()
            by_event = {entry["event_id"]: entry["evidence_note"] for entry in notes}
            for correction in overrides.values():
                correction["evidence_note"] = by_event.get(correction["event_id"])
        for field, correction in overrides.items():
            effective[field] = correction["human_confirmed_value"]
        item["effective_context"] = effective
        item["condition_overrides"] = overrides
        item["future_reuse_eligible"] = not bool(overrides)
        item["condition_review_reason"] = (
            "CONDITION_CORRECTED_FUTURE_REUSE_UNVERIFIED" if overrides else None
        )
        amount_change = conn.execute("""SELECT event_id FROM sanzo_core.correction_events
            WHERE tenant_id=%s AND item_id=%s
              AND before_json->>'current_price' IS DISTINCT FROM after_json->>'unit_price'
            ORDER BY item_version DESC LIMIT 1""", (actor.tenant_id, item_id)).fetchone()
        item["amount_change_event_id"] = amount_change["event_id"] if amount_change else None
        from .quote_confirmations import project_confirmations
        confirmation_events = conn.execute("""
            SELECT event_id,confirmation_kind,field_name,value_snapshot,anchor_sha256,
                   document_id,document_sha256,evidence_note,actor_id,item_version,
                   case_revision,created_at
            FROM sanzo_core.quote_confirmation_events
            WHERE tenant_id=%s AND item_id=%s ORDER BY item_version,event_id
        """, (actor.tenant_id, item_id)).fetchall()
        # Core synthetic observations may predate any uploaded-document record.
        # They retain their original review rules; no human attestation can be
        # made valid without a registered and verified original.
        document = conn.execute("""SELECT sha256 FROM sanzo_core.documents
            WHERE tenant_id=%s AND document_id=%s""",
            (actor.tenant_id, observation.document_id)).fetchone()
        item["quote_confirmations"] = project_confirmations(
            item, confirmation_events, document["sha256"] if document else None)
        row = conn.execute("""
            SELECT decision_json FROM sanzo_core.item_classifications
            WHERE tenant_id=%s AND item_id=%s
        """, (actor.tenant_id, item_id)).fetchone()
        item["initial_classification"] = row["decision_json"] if row else None
        item["ai_matched_fee_id"] = row["decision_json"].get("ai_matched_fee_id") if row else None
        slot = self.repository.lookup_memory(conn, observation, item["source_ref"], item["input_kind"])
        direct = self.repository.slot(conn, actor.tenant_id, memory_key(observation, item["source_ref"], item["input_kind"]))
        item["current_memory_version"] = direct["version"] if direct else 0
        item["memory_resolution"] = {"kind": slot.get("resolution", "DIRECT"), "version": slot["version"],
            "evidence_versions": slot.get("evidence_versions", []),
            "guarded_direct_status": slot.get("guarded_direct_status"),
            "guarded_independent_conflict": slot.get("guarded_independent_conflict"),
            "guarded_independent_revocation": slot.get("guarded_independent_revocation")} if slot else None
        item["current_memory_status"] = slot["status"] if slot else None
        item["current_memory_origin"] = slot["origin"] if slot else None
        # A classification adopted before an applicability correction is an
        # audit fact, but it does not confirm the fee for the corrected
        # condition. Both histories carry the item's monotonic version, so the
        # read projection can establish the order without rewriting either.
        confirmation = conn.execute("""
            SELECT max(item_version) AS item_version
            FROM sanzo_core.correction_events
            WHERE tenant_id=%s AND item_id=%s
              AND response_json->>'memory_effect' <> 'UNCHANGED_AMOUNT_ONLY'
        """, (actor.tenant_id, item_id)).fetchone()["item_version"]
        condition_version = None
        if overrides:
            condition_version = conn.execute("""
                SELECT max(item_version) AS item_version
                FROM sanzo_core.condition_correction_events
                WHERE tenant_id=%s AND item_id=%s
            """, (actor.tenant_id, item_id)).fetchone()["item_version"]
        item["classification_confirmed_after_condition"] = bool(
            confirmation is not None and
            (condition_version is None or confirmation > condition_version)
        )
        item["saved_status"] = (
            "HUMAN_CONFIRMED" if confirmation is not None else
            row["decision_json"]["kind"] if row else "UNCLASSIFIED"
        )
        # Derived provenance for the new deterministic import only. Older saved
        # records retain their original snapshots and are never reclassified.
        if item["source_ref"].get("fact_materialization"):
            item["fact_sources"] = dict(item["source_ref"]["fact_sources"])
            item["classification_source"] = (
                "HUMAN" if confirmation is not None else "MEMORY" if item["saved_status"] == "MEMORY_APPLIED"
                else "AI" if item.get("ai_suggested_name") else "UNRESOLVED")
        return item

    def list_items(self, actor: Principal, case_id: str) -> list[dict]:
        """Read the stored overlay. Never reclassify historical rows on render."""
        actor.require_case(case_id)
        with self.repository.transaction() as conn:
            # Every writer uses the same case lock; this gives a coherent table
            # revision for display/export even when another tab saves concurrently.
            self.repository.case(conn, actor.tenant_id, case_id, lock=True)
            ids = conn.execute("""
                SELECT item_id FROM sanzo_core.items
                WHERE tenant_id=%s AND case_id=%s ORDER BY created_at,item_id
            """, (actor.tenant_id, case_id)).fetchall()
            return [self._item(conn, actor, row["item_id"]) for row in ids]


    def case_snapshot(self, actor: Principal, case_id: str) -> dict:
        """Read one coherent case revision, document set and saved item overlay.

        Upload registration, manual insertion and corrections all take this case
        lock. Memory metadata is advisory for a later edit, whose version is
        checked again atomically by ReviewService.
        """
        actor.require_case(case_id)
        with self.repository.transaction() as conn:
            case = self.repository.case(conn, actor.tenant_id, case_id, lock=True)
            ids = conn.execute("""
                SELECT item_id FROM sanzo_core.items
                WHERE tenant_id=%s AND case_id=%s ORDER BY created_at,item_id
            """, (actor.tenant_id, case_id)).fetchall()
            items = [self._item(conn, actor, row["item_id"]) for row in ids]
            documents = conn.execute("""
                SELECT * FROM sanzo_core.documents WHERE tenant_id=%s AND case_id=%s
                ORDER BY created_at,document_id
            """, (actor.tenant_id, case_id)).fetchall()
            fees = conn.execute("""
                SELECT fee_id,display_name,name_version FROM sanzo_core.fees
                WHERE tenant_id=%s AND NOT retired ORDER BY display_name,fee_id
            """, (actor.tenant_id,)).fetchall()
            from .quote_confirmations import attach_document_review_events
            attach_document_review_events(conn, actor, documents)
            return {"case": case, "items": items, "documents": documents, "fees": fees}
