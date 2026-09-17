"""SQLite-only offline reference for atomic corrections + persistent learning.

Not production authentication, migrations, PostgreSQL isolation tests, SDK parsing,
AI extraction, OCR, web UI, Excel generation, or cloud deployment.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from .domain import (
    Actor, ConflictError, Correction, Decision, DecisionKind, Observation,
    ScopeError, canonical_json, normalize_label,
)

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS items (
    tenant_id TEXT NOT NULL, item_id TEXT NOT NULL, observation TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version>=1), current_fee_id TEXT,
    current_price TEXT NOT NULL, PRIMARY KEY(tenant_id,item_id)
);
CREATE TABLE IF NOT EXISTS fees (
    tenant_id TEXT NOT NULL, fee_id TEXT NOT NULL, display_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL, PRIMARY KEY(tenant_id,fee_id),
    UNIQUE(tenant_id,normalized_name)
);
CREATE TABLE IF NOT EXISTS memory_slots (
    tenant_id TEXT NOT NULL, memory_key TEXT NOT NULL, version INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('ACTIVE','CONFLICTED','REVOKED')),
    fee_id TEXT, evidence_event_id TEXT NOT NULL,
    CHECK((status='ACTIVE' AND fee_id IS NOT NULL) OR (status!='ACTIVE' AND fee_id IS NULL)),
    PRIMARY KEY(tenant_id,memory_key),
    FOREIGN KEY(tenant_id,fee_id) REFERENCES fees(tenant_id,fee_id)
);
CREATE TABLE IF NOT EXISTS correction_events (
    tenant_id TEXT NOT NULL, event_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL, actor_id TEXT NOT NULL, item_id TEXT NOT NULL,
    before_json TEXT NOT NULL, after_json TEXT NOT NULL, response_json TEXT NOT NULL,
    PRIMARY KEY(tenant_id,event_id), UNIQUE(tenant_id,idempotency_key)
);
CREATE TABLE IF NOT EXISTS memory_events (
    tenant_id TEXT NOT NULL, event_id TEXT NOT NULL, memory_key TEXT NOT NULL,
    memory_version INTEGER NOT NULL, status TEXT NOT NULL, target_fee_id TEXT,
    actor_id TEXT NOT NULL, source_event_id TEXT, reason TEXT NOT NULL,
    PRIMARY KEY(tenant_id,event_id), UNIQUE(tenant_id,memory_key,memory_version)
);
"""


class ReferenceStore:
    def __init__(self, path: str | Path):
        self.conn = sqlite3.connect(str(path), isolation_level=None, timeout=2.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def add_observation(self, observation: Observation) -> None:
        """Internal ingestion entry, not an unauthenticated network endpoint."""
        observation.validate()
        self.conn.execute(
            "INSERT INTO items VALUES (?,?,?,1,NULL,?)",
            (observation.tenant_id, observation.item_id, observation.to_json(), observation.unit_price),
        )

    def get_item(self, actor: Actor, item_id: str) -> dict:
        actor.require_editor()  # reference has editors only; read roles are future API concerns
        row = self.conn.execute(
            "SELECT * FROM items WHERE tenant_id=? AND item_id=?", (actor.tenant_id, item_id)
        ).fetchone()
        if row is None:
            raise ScopeError("Item not available in authorized scope")
        return dict(row)

    def _slot(self, tenant_id: str, memory_key: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM memory_slots WHERE tenant_id=? AND memory_key=?",
            (tenant_id, memory_key),
        ).fetchone()

    def classify(self, actor: Actor, item_id: str) -> Decision:
        item = self.get_item(actor, item_id)
        observation = Observation.from_json(item["observation"])
        values = {"unit_price": item["current_price"], "currency": observation.currency}
        if not observation.source_ready:
            return Decision(DecisionKind.READING_REQUIRED, "SOURCE_OR_STRUCTURE_UNVERIFIED", **values)
        if not observation.context.complete():
            return Decision(DecisionKind.REVIEW_REQUIRED, "INCOMPLETE_CONTEXT", **values)
        if not observation.forwarder_id:
            return Decision(DecisionKind.AI_REQUIRED, "UNKNOWN_FORWARDER_NO_EXACT_REUSE", **values)
        key = observation.memory_key()
        assert key is not None
        slot = self._slot(actor.tenant_id, key)
        if slot is None:
            return Decision(DecisionKind.AI_REQUIRED, "NO_CONFIRMED_MAPPING", **values)
        if slot["status"] != "ACTIVE":
            return Decision(
                DecisionKind.REVIEW_REQUIRED, f"MEMORY_{slot['status']}",
                memory_version=slot["version"], **values,
            )
        fee = self.conn.execute(
            "SELECT * FROM fees WHERE tenant_id=? AND fee_id=?",
            (actor.tenant_id, slot["fee_id"]),
        ).fetchone()
        if fee is None:
            raise RuntimeError("Broken memory/category binding")
        return Decision(
            DecisionKind.MEMORY_APPLIED, "EXACT_CONFIRMED_CONTEXT",
            fee_id=fee["fee_id"], fee_name=fee["display_name"],
            memory_version=slot["version"], **values,
        )

    def correct(self, actor: Actor, command: Correction) -> dict:
        actor.require_editor()
        command.validate()
        digest = hashlib.sha256(canonical_json({
            "actor": asdict(actor), "command": asdict(command)
        }).encode()).hexdigest()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            old_request = self.conn.execute(
                "SELECT * FROM correction_events WHERE tenant_id=? AND idempotency_key=?",
                (actor.tenant_id, command.idempotency_key),
            ).fetchone()
            if old_request is not None:
                if old_request["request_hash"] != digest:
                    raise ConflictError("Idempotency key reused for another payload or actor")
                response = json.loads(old_request["response_json"])
                self.conn.execute("COMMIT")
                return response
            item = self.get_item(actor, command.item_id)
            if item["version"] != command.expected_item_version:
                raise ConflictError("Stale item version; keep UI edits and reload")
            observation = Observation.from_json(item["observation"])
            key = observation.memory_key()
            category_action = command.target_fee_id is not None or command.new_fee_name is not None
            slot = self._slot(actor.tenant_id, key) if key and category_action else None
            if key and category_action:
                actual_version = slot["version"] if slot else 0
                if actual_version != command.expected_memory_version:
                    raise ConflictError("Stale memory version; correction was not saved")
            target = item["current_fee_id"]
            if command.target_fee_id is not None:
                exists = self.conn.execute(
                    "SELECT fee_id FROM fees WHERE tenant_id=? AND fee_id=?",
                    (actor.tenant_id, command.target_fee_id),
                ).fetchone()
                if exists is None:
                    raise ScopeError("Fee not available in authorized scope")
                target = command.target_fee_id
            elif command.new_fee_name is not None:
                normalized = normalize_label(command.new_fee_name)
                exists = self.conn.execute(
                    "SELECT fee_id FROM fees WHERE tenant_id=? AND normalized_name=?",
                    (actor.tenant_id, normalized),
                ).fetchone()
                if exists is not None:
                    raise ConflictError("Name exists: explicitly select the existing fee ID")
                target = str(uuid4())
                self.conn.execute(
                    "INSERT INTO fees VALUES (?,?,?,?)",
                    (actor.tenant_id, target, command.new_fee_name.strip(), normalized),
                )
            event_id = str(uuid4())
            price = command.unit_price if command.unit_price is not None else item["current_price"]
            new_item_version = item["version"] + 1
            self.conn.execute(
                "UPDATE items SET current_fee_id=?,current_price=?,version=? WHERE tenant_id=? AND item_id=?",
                (target, price, new_item_version, actor.tenant_id, command.item_id),
            )
            memory_effect = "UNCHANGED_AMOUNT_ONLY"
            memory_version = slot["version"] if slot else 0
            if category_action and key is None:
                memory_effect = "NOT_ELIGIBLE_CONTEXT_OR_SOURCE"
            elif category_action:
                assert key is not None and target is not None
                memory_version += 1
                status = "ACTIVE"
                if slot is not None and not command.replace_existing_mapping:
                    if slot["status"] in ("CONFLICTED", "REVOKED"):
                        status = slot["status"]
                    elif slot["fee_id"] != target:
                        status = "CONFLICTED"
                active_target = target if status == "ACTIVE" else None
                self.conn.execute(
                    """INSERT INTO memory_slots VALUES (?,?,?,?,?,?)
                    ON CONFLICT(tenant_id,memory_key) DO UPDATE SET
                    version=excluded.version,status=excluded.status,fee_id=excluded.fee_id,
                    evidence_event_id=excluded.evidence_event_id""",
                    (actor.tenant_id, key, memory_version, status, active_target, event_id),
                )
                memory_effect = status
                self.conn.execute(
                    "INSERT INTO memory_events VALUES (?,?,?,?,?,?,?,?,?)",
                    (actor.tenant_id, str(uuid4()), key, memory_version, status,
                     target, actor.actor_id, event_id, "EXPLICIT_ITEM_FEE_CONFIRMATION"),
                )
            response = {
                "event_id": event_id, "item_id": command.item_id,
                "item_version": new_item_version, "fee_id": target,
                "unit_price": price, "memory_effect": memory_effect,
                "memory_version": memory_version,
            }
            after = {"fee_id": target, "unit_price": price, "version": new_item_version}
            self.conn.execute(
                "INSERT INTO correction_events VALUES (?,?,?,?,?,?,?,?,?)",
                (actor.tenant_id, event_id, command.idempotency_key, digest, actor.actor_id,
                 command.item_id, canonical_json(item), canonical_json(after), canonical_json(response)),
            )
            self.conn.execute("COMMIT")
            return response
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def revoke_memory(self, actor: Actor, item_id: str, expected_version: int, reason: str) -> None:
        """Reference-only corrective operation. Does not alter any historical quote price."""
        actor.require_editor()
        if not reason.strip():
            raise ValueError("Revocation reason required")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            item = self.get_item(actor, item_id)
            key = Observation.from_json(item["observation"]).memory_key()
            slot = self._slot(actor.tenant_id, key) if key else None
            if slot is None or slot["version"] != expected_version:
                raise ConflictError("Stale or unavailable memory")
            event_id = str(uuid4())
            self.conn.execute(
                "UPDATE memory_slots SET version=?,status='REVOKED',fee_id=NULL,evidence_event_id=? WHERE tenant_id=? AND memory_key=?",
                (expected_version + 1, event_id, actor.tenant_id, key),
            )
            self.conn.execute(
                "INSERT INTO memory_events VALUES (?,?,?,?,?,?,?,?,?)",
                (actor.tenant_id, event_id, key, expected_version + 1, "REVOKED", None,
                 actor.actor_id, None, reason),
            )
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
