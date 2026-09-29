"""Audited human condition overlay; original reading and source evidence are immutable.

This service changes only the current item's equipment or billing unit. It
invalidates an ACTIVE rule for the exact original signature when that signature
has become ambiguous, but never creates a rule for the corrected condition.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from uuid import uuid4

from .demo_store import DemoStore
from .domain import (ConflictError, Observation, Principal, ScopeError,
                     canonical_json, memory_key, request_digest)
from .postgres import PostgresRepository
from .service import ReviewService


@dataclass(frozen=True, kw_only=True)
class ConditionCorrectionCommand:
    case_id: str
    row_id: str
    item_id: str
    expected_case_revision: int
    expected_item_version: int
    expected_memory_version: int
    idempotency_key: str
    field: str
    value: str | None
    expected_memory_evidence: list[dict] | None = None

    def validate(self) -> None:
        if not isinstance(self.case_id, str) or not self.case_id.strip():
            raise ValueError("案件を確認してください")
        if not isinstance(self.row_id, str) or not re.fullmatch(r"[0-9a-f]{64}", self.row_id):
            raise ValueError("比較行を確認してください")
        if not isinstance(self.item_id, str) or not self.item_id.strip() or len(self.item_id) > 200:
            raise ValueError("明細を確認してください")
        if (type(self.expected_case_revision) is not int or self.expected_case_revision < 1
                or type(self.expected_item_version) is not int or self.expected_item_version < 1
                or type(self.expected_memory_version) is not int or self.expected_memory_version < 0):
            raise ValueError("明細・案件・学習記録の版を確認してください")
        if (not isinstance(self.idempotency_key, str) or not self.idempotency_key
                or len(self.idempotency_key) > 200):
            raise ValueError("保存キーを確認してください")
        if self.field not in {"equipment", "billing_unit"}:
            raise ValueError("修正できる条件は機材と課金単位です")
        if self.value is not None and (
            not isinstance(self.value, str) or not self.value.strip() or len(self.value) > 1024
        ):
            raise ValueError("条件は文字列または未確認を指定してください")
        evidence = self.expected_memory_evidence
        if evidence is not None:
            if not isinstance(evidence, list) or not 1 <= len(evidence) <= 1000:
                raise ValueError("確認記録の版情報が不正です")
            for entry in evidence:
                if (not isinstance(entry, dict)
                    or set(entry) != {"memory_key", "version", "status", "fee_id", "evidence_event_id"}
                    or not isinstance(entry["memory_key"], str) or not entry["memory_key"]
                    or type(entry["version"]) is not int or entry["version"] < 1
                    or entry["status"] not in {"ACTIVE", "CONFLICTED", "REVOKED"}
                    or (entry["fee_id"] is not None and not isinstance(entry["fee_id"], str))
                    or not isinstance(entry["evidence_event_id"], str) or not entry["evidence_event_id"]):
                    raise ValueError("確認記録の版情報が不正です")


class ConditionCorrectionService:
    def __init__(self, repository: PostgresRepository, fault_hook=None):
        self.repository = repository
        self.store = DemoStore(repository)
        self.review = ReviewService(repository, fault_hook=fault_hook)

    def correct(self, actor: Principal, command: ConditionCorrectionCommand) -> dict:
        actor.require_case(command.case_id)
        command.validate()
        digest = request_digest(actor, command)
        r = self.repository
        with r.transaction() as conn:
            replay = self.review._replay(conn, actor, command, digest, "CORRECT")
            if replay is not None:
                return replay
            case = r.case(conn, actor.tenant_id, command.case_id, lock=True)
            r.lock_fee_catalog(conn, actor.tenant_id)
            if case["case_revision"] != command.expected_case_revision:
                raise ConflictError("案件が更新されています。最新を読み直してください")
            # Resolve the canonical row after the case/catalog locks. One
            # member must belong to this exact row, but no sibling is edited.
            from sanzo_demo.comparison import comparison_projection
            ids = conn.execute("""
                SELECT item_id FROM sanzo_core.items WHERE tenant_id=%s AND case_id=%s
                ORDER BY created_at,item_id
            """, (actor.tenant_id, command.case_id)).fetchall()
            items = [self.store._item(conn, actor, row["item_id"]) for row in ids]
            fees = conn.execute("""
                SELECT fee_id,display_name,name_version FROM sanzo_core.fees
                WHERE tenant_id=%s AND NOT retired
            """, (actor.tenant_id,)).fetchall()
            documents = conn.execute("""
                SELECT document_id,original_filename FROM sanzo_core.documents
                WHERE tenant_id=%s AND case_id=%s
            """, (actor.tenant_id, command.case_id)).fetchall()
            row = next((row for row in comparison_projection(items, fees, documents)["rows"]
                        if row["row_id"] == command.row_id), None)
            if row is None or command.item_id not in row["item_ids"]:
                raise ConflictError("比較行の構成が変わりました。最新を読み直してください")
            item = r.item(conn, actor.tenant_id, command.item_id, lock=True)
            if item["case_id"] != command.case_id:
                raise ScopeError("明細が案件に属していません")
            if item["version"] != command.expected_item_version:
                raise ConflictError("明細が更新されています。最新を読み直してください")
            observation = Observation.from_json(item["observation"])
            key = memory_key(observation, item["source_ref"], item["input_kind"])
            if key is not None:
                r.advisory(conn, "memory", actor.tenant_id, key)
            direct = r.slot(conn, actor.tenant_id, key)
            if (direct["version"] if direct else 0) != command.expected_memory_version:
                raise ConflictError("確認記録が更新されています。最新を読み直してください")
            effective_memory = direct or r.lookup_memory(
                conn, observation, item["source_ref"], item["input_kind"])
            if effective_memory and effective_memory.get("resolution") == "LEGACY_PROJECTION":
                for legacy_key in sorted(e["memory_key"] for e in effective_memory["evidence_versions"]):
                    r.advisory(conn, "memory", actor.tenant_id, legacy_key)
                effective_memory = r.lookup_memory(
                    conn, observation, item["source_ref"], item["input_kind"])
                current = effective_memory.get("evidence_versions") if effective_memory else None
                if (not effective_memory or effective_memory.get("resolution") != "LEGACY_PROJECTION"
                        or command.expected_memory_evidence is None
                        or canonical_json(current) != canonical_json(command.expected_memory_evidence)):
                    raise ConflictError("確認記録が更新されています。最新を読み直してください")
            elif command.expected_memory_evidence is not None:
                raise ConflictError("確認記録が更新されています。最新を読み直してください")

            before = self.store._item(conn, actor, command.item_id)
            original_value = before["observation"]["context"].get(command.field)
            before_effective = before["effective_context"].get(command.field)
            value = command.value.strip() if command.value is not None else None
            if before_effective == value:
                raise ValueError("現在の有効値と同じです")
            item_version = conn.execute("""
                UPDATE sanzo_core.items SET version=version+1,updated_at=current_timestamp
                WHERE tenant_id=%s AND item_id=%s RETURNING version
            """, (actor.tenant_id, command.item_id)).fetchone()["version"]
            revision = r.advance_case(conn, actor.tenant_id, command.case_id)
            event_id = str(uuid4())
            conn.execute("""
                INSERT INTO sanzo_core.condition_correction_events(
                    tenant_id,event_id,case_id,item_id,actor_id,field_name,
                    original_value,before_effective_value,human_confirmed_value,
                    original_memory_key,item_version,case_revision)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (actor.tenant_id, event_id, command.case_id, command.item_id,
                  actor.actor_id, command.field, original_value, before_effective,
                  value, key, item_version, revision))
            conn.execute("""
                INSERT INTO sanzo_core.item_condition_overlays(
                    tenant_id,item_id,field_name,original_value,human_confirmed_value,event_id)
                VALUES(%s,%s,%s,%s,%s,%s)
                ON CONFLICT(tenant_id,item_id,field_name) DO UPDATE SET
                    human_confirmed_value=excluded.human_confirmed_value,
                    event_id=excluded.event_id,updated_at=current_timestamp
            """, (actor.tenant_id, command.item_id, command.field,
                  original_value, value, event_id))
            self.review._checkpoint("after_condition_overlay")

            # The original extracted signature can no longer authorize
            # automatic reuse if an ACTIVE memory exists for it. Constrain the
            # invalidation to this exact key. Other signatures are untouched.
            memory_version = direct["version"] if direct else 0
            memory_action = "UNCHANGED"
            if (key is not None and original_value != value and effective_memory
                    and effective_memory["status"] == "ACTIVE"):
                memory_version += 1
                r.write_memory(conn, actor=actor, item=item, observation=observation,
                    key=key, version=memory_version, status="CONFLICTED",
                    target=effective_memory.get("fee_id"), event_id=str(uuid4()),
                    source_event_id=None,
                    reason="HUMAN_CONDITION_CORRECTION_INVALIDATES_ORIGINAL_SIGNATURE")
                memory_action = "ORIGINAL_SIGNATURE_CONFLICTED"
            self.review._checkpoint("after_condition_memory")
            updated = self.store._item(conn, actor, command.item_id)
            response = {
                "kind": "ITEM_CONDITION_CORRECTION", "event_id": event_id,
                "case_id": command.case_id, "row_id": command.row_id,
                "item_id": command.item_id, "item_version": item_version,
                "case_revision": revision, "field": command.field,
                "original_value": original_value,
                "before_effective_value": before_effective,
                "human_confirmed_value": value, "effective_value": value,
                "condition_overrides": updated["condition_overrides"],
                "effective_context": updated["effective_context"],
                "future_reuse_eligible": False,
                "condition_review_reason": updated["condition_review_reason"],
                "memory_version": memory_version,
                "memory_status": updated["current_memory_status"],
                "memory_action": memory_action,
            }
            self.review._checkpoint("before_condition_command")
            r.save_command(conn, actor, command, "CORRECT", digest, command.case_id, response)
            return response
