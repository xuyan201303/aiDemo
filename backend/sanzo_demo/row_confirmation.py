"""Row-name confirmation composes existing item corrections in one transaction.

No new persistence store: the existing CORRECT command ledger stores a typed
row command/aggregate response, and ordinary per-item events/memory keep scope.
"""
from dataclasses import dataclass
from hashlib import sha256

from sanzo_core.demo_store import DemoStore
from sanzo_core.domain import (ConflictError, CorrectionCommand, Observation, ScopeError,
    canonical_json, memory_key, normalize_label, request_digest)
from sanzo_core.service import ReviewService
from .comparison import comparison_projection


@dataclass(frozen=True)
class RowMember:
    item_id: str
    expected_item_version: int
    expected_memory_version: int
    expected_memory_evidence: list[dict] | None = None


@dataclass(frozen=True)
class RowConfirmationCommand:
    case_id: str
    row_id: str
    expected_case_revision: int
    members: tuple[RowMember, ...]
    idempotency_key: str
    target_fee_id: str | None = None
    new_fee_name: str | None = None
    replace_existing_mapping: bool = False
    intent: str = "CONFIRM_MEMBERS"
    expected_fee_id: str | None = None
    expected_name_version: int | None = None

    @property
    def item_id(self):
        # Existing ledger requires an anchor item; response lists every member.
        return sorted(m.item_id for m in self.members)[0]

    def validate(self):
        if self.intent not in {"CONFIRM_MEMBERS", "RENAME_FEE", "RECLASSIFY_MEMBERS"}:
            raise ValueError("Unknown confirmation intent")
        if self.intent == "RENAME_FEE":
            if (not self.expected_fee_id or type(self.expected_name_version) is not int
                    or self.expected_name_version < 1 or self.new_fee_name is None
                    or self.target_fee_id is not None or self.replace_existing_mapping):
                raise ValueError("Explicit fee identity and name version required for rename")
        elif self.expected_fee_id is not None or self.expected_name_version is not None:
            if (not isinstance(self.expected_fee_id, str) or not self.expected_fee_id.strip()
                    or self.expected_fee_id != self.target_fee_id
                    or type(self.expected_name_version) is not int or self.expected_name_version < 1):
                raise ValueError("選択した統一費目と名称の版を確認してください")
        # A fully absent pair remains structurally valid for exact replay of a
        # previously successful command. Fresh existing-target saves require
        # it below, after replay and under the same catalog lock as rename.
        if not 1 <= len(self.members) <= 100 or len({m.item_id for m in self.members}) != len(self.members):
            raise ValueError("行の明細が重複または不足しています")
        if not isinstance(self.case_id, str) or not self.case_id or not isinstance(self.row_id, str) or len(self.row_id) != 64:
            raise ValueError("比較行を確認してください")
        if (self.target_fee_id is None) == (self.new_fee_name is None):
            raise ValueError("統一費目を一つ指定してください")
        for member in self.members:
            self.item_command(member, self.expected_case_revision, self.idempotency_key).validate()
            evidence = member.expected_memory_evidence
            if evidence is not None:
                if not isinstance(evidence, list) or not 1 <= len(evidence) <= 1000:
                    raise ValueError("確認記録の版情報が不正です")
                for entry in evidence:
                    if (not isinstance(entry, dict) or set(entry) != {"memory_key", "version", "status", "fee_id", "evidence_event_id"}
                            or type(entry["version"]) is not int or entry["version"] < 1
                            or entry["status"] not in {"ACTIVE", "CONFLICTED", "REVOKED"}
                            or not isinstance(entry["memory_key"], str) or not entry["memory_key"]
                            or not isinstance(entry["evidence_event_id"], str) or not entry["evidence_event_id"]
                            or (entry["fee_id"] is not None and not isinstance(entry["fee_id"], str))):
                        raise ValueError("確認記録の版情報が不正です")

    def item_command(self, member, revision, key, *, target=None, memory_version=None):
        return CorrectionCommand(item_id=member.item_id,
            expected_case_revision=revision, expected_item_version=member.expected_item_version,
            expected_memory_version=member.expected_memory_version if memory_version is None else memory_version,
            idempotency_key=key, target_fee_id=target or self.target_fee_id,
            new_fee_name=self.new_fee_name if target is None and self.target_fee_id is None else None,
            replace_existing_mapping=self.replace_existing_mapping)


class RowConfirmationService:
    def __init__(self, repository, fault_hook=None):
        self.repository = repository
        self.service = ReviewService(repository, fault_hook=fault_hook)
        self.store = DemoStore(repository)

    def confirm(self, actor, command):
        actor.require_case(command.case_id)
        command.validate()
        digest = request_digest(actor, command)
        r = self.repository
        with r.transaction() as conn:
            # Typed request digest separates this row correction from an item
            # correction even when the caller accidentally reuses the same key.
            replay = self.service._replay(conn, actor, command, digest, "CORRECT")
            if replay is not None:
                return replay
            members = sorted(command.members, key=lambda m: m.item_id)
            child_keys = {m.item_id: "row-member:" + sha256(canonical_json([actor.tenant_id, command.idempotency_key, m.item_id]).encode()).hexdigest() for m in members}
            for m in members:
                r.advisory(conn, "idempotency", actor.tenant_id, child_keys[m.item_id])
            case = r.case(conn, actor.tenant_id, command.case_id, lock=True)
            r.lock_fee_catalog(conn, actor.tenant_id)
            if case["case_revision"] != command.expected_case_revision:
                raise ConflictError("案件が更新されています。入力を保持して最新を読み直してください")
            if command.target_fee_id is not None:
                if command.expected_fee_id is None or command.expected_name_version is None:
                    raise ValueError("選択した統一費目の最新の名称を確認してから保存してください")
                # The supplied identity is structurally bound to target_fee_id.
                # Resolve its effective name version only after waiting for any
                # concurrent rename, and before member, history or Memory writes.
                selected_fee = r.fee(conn, actor.tenant_id, command.target_fee_id)
                if selected_fee is None:
                    raise ScopeError("Fee not available in authorized scope")
                if selected_fee["name_version"] != command.expected_name_version:
                    raise ConflictError("統一費目の名称が更新されています。入力を保持して最新の名称を確認してください")
            ids = conn.execute("SELECT item_id FROM sanzo_core.items WHERE tenant_id=%s AND case_id=%s ORDER BY item_id",
                               (actor.tenant_id, command.case_id)).fetchall()
            items = [self.store._item(conn, actor, row["item_id"]) for row in ids]
            fees = conn.execute("SELECT fee_id,display_name FROM sanzo_core.fees WHERE tenant_id=%s", (actor.tenant_id,)).fetchall()
            documents = conn.execute("SELECT document_id,original_filename FROM sanzo_core.documents WHERE tenant_id=%s AND case_id=%s", (actor.tenant_id, command.case_id)).fetchall()
            row = next((row for row in comparison_projection(items, fees, documents)["rows"] if row["row_id"] == command.row_id), None)
            if row is None or set(row["item_ids"]) != {m.item_id for m in members}:
                raise ConflictError("比較行の構成が変わりました。行全体を再確認してください")
            keys = {}
            locked_items = {}
            for m in members:
                item = r.item(conn, actor.tenant_id, m.item_id, lock=True)
                if item["version"] != m.expected_item_version:
                    raise ConflictError("明細が更新されています。最新を読み直してください")
                keys[m.item_id] = memory_key(Observation.from_json(item["observation"]), item["source_ref"], item["input_kind"])
                locked_items[m.item_id] = item
            # Prelock every memory key before fee creation; deterministic order
            # also covers concurrent rows/cases and duplicate raw aliases.
            for key in sorted(set(keys.values()) - {None}):
                r.advisory(conn, "memory", actor.tenant_id, key)
            # A projected legacy mapping has no direct v2 slot yet. Version 0
            # alone cannot prove that the user saw its latest human evidence.
            # Lock actual observed legacy keys, then reread and compare the
            # complete snapshot. The request cannot introduce trusted keys.
            legacy_keys = set()
            for m in members:
                item = locked_items[m.item_id]
                effective = r.lookup_memory(conn, Observation.from_json(item["observation"]), item["source_ref"], item["input_kind"])
                if effective and effective.get("resolution") == "LEGACY_PROJECTION":
                    legacy_keys.update(e["memory_key"] for e in effective["evidence_versions"])
            for key in sorted(legacy_keys):
                r.advisory(conn, "memory", actor.tenant_id, key)
            memory_versions = {}
            for m in members:
                key = keys[m.item_id]
                slot = r.slot(conn, actor.tenant_id, key)
                version = slot["version"] if slot else 0
                if version != m.expected_memory_version:
                    raise ConflictError("確認記録が更新されています。最新を読み直してください")
                item = locked_items[m.item_id]
                effective = slot or r.lookup_memory(conn, Observation.from_json(item["observation"]), item["source_ref"], item["input_kind"])
                expected = m.expected_memory_evidence
                if effective and effective.get("resolution") == "LEGACY_PROJECTION":
                    current = effective["evidence_versions"]
                    if expected is None or canonical_json(current) != canonical_json(expected):
                        raise ConflictError("確認記録が更新されています。最新を読み直してください")
                elif expected is not None:
                    raise ConflictError("確認記録が更新されています。最新を読み直してください")
                if key is not None:
                    memory_versions[key] = version
            if command.intent == "RENAME_FEE":
                from sanzo_core.fee_names import rename_row
                return rename_row(self, conn, actor, command, locked_items, digest)
            if command.new_fee_name is not None:
                r.advisory(conn, "display_name", actor.tenant_id, normalize_label(command.new_fee_name))
            target = command.target_fee_id
            revision = command.expected_case_revision
            results = []
            for m in members:
                key = keys[m.item_id]
                result = self.service._correct_in_transaction(conn, actor, command.item_command(
                    m, revision, child_keys[m.item_id], target=target,
                    memory_version=memory_versions.get(key, 0)), human_reconfirm=True)
                target, revision = result["fee_id"], result["case_revision"]
                if key is not None:
                    memory_versions[key] = result["memory_version"]
                results.append(result)
            response = {"kind": "ROW_FEE_CONFIRMATION", "row_id": command.row_id,
                "case_id": command.case_id, "case_revision": revision, "fee_id": target,
                "confirmation_status": "✓ 確認済み", "members": results,
                "memory_reusable": all(member["memory_effect"] == "ACTIVE" for member in results)}
            r.save_command(conn, actor, command, "CORRECT", digest, command.case_id, response)
            return response
