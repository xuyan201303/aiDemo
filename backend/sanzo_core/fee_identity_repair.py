"""Explicit, evidence-bound repair of one mistaken fee-identity branch.

This is an application service, not a migration or automatic name merger. It
accepts an operator-approved exact plan, rejects drift, and appends an immutable
repair record. No identifier, filename, name suffix, price or source is inferred.
"""
from dataclasses import asdict, dataclass
from hashlib import sha256
from uuid import uuid4

from psycopg.types.json import Jsonb

from .domain import (ConflictError, CorrectionCommand, Observation, ScopeError,
                     canonical_json, memory_key, memory_origin, request_digest)
from .fee_names import append_name
from .service import ReviewService


@dataclass(frozen=True)
class RepairMember:
    item_id: str
    expected_item_version: int
    expected_fee_id: str
    prior_correction_event_id: str | None = None


@dataclass(frozen=True)
class RepairAlias:
    memory_key: str
    expected_version: int
    expected_fee_id: str


@dataclass(frozen=True)
class FeeIdentityRepairCommand:
    repair_id: str
    case_id: str
    expected_case_revision: int
    original_fee_id: str
    branch_fee_id: str
    expected_original_name_version: int
    expected_branch_name_version: int
    target_name: str
    expected_origin: str
    members: tuple[RepairMember, ...]
    expected_active_aliases: tuple[RepairAlias, ...]

    def validate(self):
        texts = (self.repair_id, self.case_id, self.original_fee_id,
                 self.branch_fee_id, self.target_name, self.expected_origin)
        if any(not isinstance(v, str) or not v.strip() or len(v) > 1024 for v in texts):
            raise ValueError("Explicit bounded repair identifiers and name required")
        if self.original_fee_id == self.branch_fee_id:
            raise ValueError("Repair requires two explicitly distinct identities")
        versions = [self.expected_case_revision, self.expected_original_name_version,
                    self.expected_branch_name_version]
        if (not 2 <= len(self.members) <= 100 or
                len({m.item_id for m in self.members}) != len(self.members) or
                not 2 <= len(self.expected_active_aliases) <= 1000 or
                len({a.memory_key for a in self.expected_active_aliases}) != len(self.expected_active_aliases)):
            raise ValueError("Exact unique repair members and aliases required")
        identities = {self.original_fee_id, self.branch_fee_id}
        if {m.expected_fee_id for m in self.members} != identities:
            raise ValueError("Both the original and branch must occur in the target case")
        for member in self.members:
            versions.append(member.expected_item_version)
            if not isinstance(member.item_id, str) or not member.item_id:
                raise ValueError("Exact item identifier required")
            if member.expected_fee_id == self.branch_fee_id:
                if not isinstance(member.prior_correction_event_id, str) or not member.prior_correction_event_id:
                    raise ValueError("Each branch item requires its original-to-branch correction evidence")
            elif member.prior_correction_event_id is not None:
                raise ValueError("Original member must not claim a branch correction")
        for alias in self.expected_active_aliases:
            versions.append(alias.expected_version)
            if (not isinstance(alias.memory_key, str) or not alias.memory_key or
                    alias.expected_fee_id not in identities):
                raise ValueError("Exact active alias identity required")
        if any(type(version) is not int or version < 1 for version in versions):
            raise ValueError("Positive expected versions required")


class FeeIdentityRepairService:
    def __init__(self, repository, fault_hook=None):
        self.repository = repository
        self.service = ReviewService(repository, fault_hook=fault_hook)

    def apply(self, actor, command: FeeIdentityRepairCommand):
        actor.require_editor()
        actor.require_case(command.case_id)
        command.validate()
        digest = request_digest(actor, command)
        r = self.repository
        child_keys = {m.item_id: "fee-repair-member:" + sha256(canonical_json(
            [actor.tenant_id, command.repair_id, m.item_id]).encode()).hexdigest()
            for m in command.members}
        with r.transaction() as conn:
            r.advisory(conn, "fee-identity-repair", actor.tenant_id, command.repair_id)
            replay = conn.execute("""SELECT request_hash,result FROM sanzo_core.fee_identity_repair_events
                WHERE tenant_id=%s AND repair_id=%s""", (actor.tenant_id, command.repair_id)).fetchone()
            if replay:
                if replay["request_hash"] != digest:
                    raise ConflictError("Repair ID was used for a different approved plan")
                return replay["result"]
            for key in sorted(child_keys.values()):
                r.advisory(conn, "idempotency", actor.tenant_id, key)
            case = r.case(conn, actor.tenant_id, command.case_id, lock=True)
            if case["case_revision"] != command.expected_case_revision:
                raise ConflictError("Repair case changed after approval")
            r.lock_fee_catalog(conn, actor.tenant_id)
            fees = {identity: r.fee(conn, actor.tenant_id, identity)
                    for identity in (command.original_fee_id, command.branch_fee_id)}
            if any(fee is None for fee in fees.values()):
                raise ScopeError("Repair identities unavailable in this tenant")
            original, branch = fees[command.original_fee_id], fees[command.branch_fee_id]
            if (original["name_version"] != command.expected_original_name_version or
                    branch["name_version"] != command.expected_branch_name_version or
                    branch["display_name"] != command.target_name.strip()):
                raise ConflictError("Repair fee names changed after approval")
            ids = conn.execute("""SELECT item_id FROM sanzo_core.items WHERE tenant_id=%s
                AND case_id=%s AND current_fee_id IN (%s,%s)""", (actor.tenant_id, command.case_id,
                command.original_fee_id, command.branch_fee_id)).fetchall()
            if {row["item_id"] for row in ids} != {m.item_id for m in command.members}:
                raise ConflictError("Repair target membership changed after approval")
            items, keys, proofs = {}, {}, []
            for member in sorted(command.members, key=lambda m: m.item_id):
                item = r.item(conn, actor.tenant_id, member.item_id, lock=True)
                if (item["case_id"] != command.case_id or item["version"] != member.expected_item_version or
                        item["current_fee_id"] != member.expected_fee_id):
                    raise ConflictError("Repair item changed after approval")
                if memory_origin(item["source_ref"], item["input_kind"]) != command.expected_origin:
                    raise ScopeError("Repair cannot cross source origins")
                obs = Observation.from_json(item["observation"])
                key = memory_key(obs, item["source_ref"], item["input_kind"])
                if key is None:
                    raise ConflictError("Repair source no longer eligible for its proven mapping")
                items[member.item_id], keys[member.item_id] = item, key
                if member.prior_correction_event_id is not None:
                    event = conn.execute("""SELECT * FROM sanzo_core.correction_events
                        WHERE tenant_id=%s AND event_id=%s""",
                        (actor.tenant_id, member.prior_correction_event_id)).fetchone()
                    if (not event or event["case_id"] != command.case_id or event["item_id"] != member.item_id or
                            event["item_version"] != member.expected_item_version or
                            event["origin"] != command.expected_origin or
                            event["before_json"].get("current_fee_id") != command.original_fee_id or
                            event["after_json"].get("fee_id") != command.branch_fee_id):
                        raise ConflictError("Prior correction does not prove this unchanged identity branch")
                    proofs.append({"item_id": member.item_id, "event_id": event["event_id"],
                                   "before_fee_id": command.original_fee_id, "after_fee_id": command.branch_fee_id})
            for key in sorted({a.memory_key for a in command.expected_active_aliases} | set(keys.values())):
                r.advisory(conn, "memory", actor.tenant_id, key)
            active = conn.execute("""SELECT s.memory_key,s.version,s.fee_id,s.origin,s.evidence_event_id,
                e.source_event_id,
                (e.status='ACTIVE' AND e.target_fee_id=s.fee_id AND e.origin=s.origin
                 AND c.origin=s.origin AND c.after_json->>'fee_id'=s.fee_id
                 AND c.item_id=v.item_id) AS human_confirmed
                FROM sanzo_core.memory_slots s
                JOIN sanzo_core.memory_versions v USING(tenant_id,memory_key,version)
                LEFT JOIN sanzo_core.memory_events e ON e.tenant_id=s.tenant_id
                    AND e.event_id=s.evidence_event_id AND e.memory_key=s.memory_key AND e.memory_version=s.version
                LEFT JOIN sanzo_core.correction_events c ON c.tenant_id=e.tenant_id AND c.event_id=e.source_event_id
                WHERE s.tenant_id=%s AND s.status='ACTIVE'
                AND s.fee_id IN (%s,%s) ORDER BY s.memory_key""",
                (actor.tenant_id, command.original_fee_id, command.branch_fee_id)).fetchall()
            expected = {(a.memory_key, a.expected_version, a.expected_fee_id) for a in command.expected_active_aliases}
            if {(a["memory_key"], a["version"], a["fee_id"]) for a in active} != expected:
                raise ConflictError("Active aliases changed; revoked, conflicted or reclassified mappings are not restored")
            if any(a["origin"] != command.expected_origin for a in active):
                raise ScopeError("Repair would cross REAL/Mock learning scope")
            if any(not a["human_confirmed"] for a in active):
                raise ConflictError("Every repaired identity alias requires current human confirmation evidence")
            proof_ids = {proof["event_id"] for proof in proofs}
            if any(a["fee_id"] == command.branch_fee_id and a["source_event_id"] not in proof_ids for a in active):
                raise ConflictError("Branch alias has a newer confirmation outside the approved prior evidence")
            by_key = {a["memory_key"]: a for a in active}
            for iid, item in items.items():
                slot = by_key.get(keys[iid])
                if slot is None or slot["fee_id"] != item["current_fee_id"]:
                    raise ConflictError("Target item mapping is no longer its approved ACTIVE alias")
            branch_keys = {a["memory_key"] for a in active if a["fee_id"] == command.branch_fee_id}
            represented = {keys[iid] for iid, item in items.items() if item["current_fee_id"] == command.branch_fee_id}
            if branch_keys != represented:
                raise ConflictError("Branch has aliases outside the approved case repair evidence")
            evidence = {"approved_plan": asdict(command), "prior_corrections": proofs,
                        "active_aliases_before": active, "fees_before": list(fees.values()),
                        "items_before": list(items.values())}
            # Retirement is explicit and audited below; historical fee references remain.
            conn.execute("UPDATE sanzo_core.fees SET retired=true WHERE tenant_id=%s AND fee_id=%s",
                         (actor.tenant_id, command.branch_fee_id))
            named = append_name(r, conn, actor, command.original_fee_id,
                command.expected_original_name_version, command.target_name, command.case_id,
                reason="EXPLICIT_EVIDENCED_IDENTITY_REPAIR")
            self.service._checkpoint("after_name")
            results, rewritten = [], set()
            revision = command.expected_case_revision
            for iid in sorted(items):
                item, key = items[iid], keys[iid]
                slot = by_key[key]
                if key in branch_keys and key not in rewritten:
                    result = self.service._correct_in_transaction(conn, actor, CorrectionCommand(
                        item_id=iid, expected_item_version=item["version"],
                        expected_case_revision=revision, expected_memory_version=slot["version"],
                        idempotency_key=child_keys[iid], target_fee_id=command.original_fee_id),
                        human_reconfirm=True)
                    rewritten.add(key)
                else:
                    version = r.update_item(conn, actor.tenant_id, iid, command.original_fee_id,
                                            item["current_price"], refresh_name=True)
                    revision = r.advance_case(conn, actor.tenant_id, command.case_id)
                    self.service._checkpoint("after_item")
                    current_slot = r.slot(conn, actor.tenant_id, key)
                    result = {**named, "item_id": iid, "item_version": version,
                        "case_revision": revision, "unit_price": item["current_price"], "event_id": str(uuid4()),
                        "memory_effect": "ACTIVE", "memory_action": "UNCHANGED_IDENTITY_REPAIR_SNAPSHOT",
                        "memory_version": current_slot["version"]}
                    r.correction_event(conn, actor, item, result)
                revision = result["case_revision"]
                results.append(result)
            self.service._checkpoint("before_repair_audit")
            result = {**named, "kind": "EXPLICIT_FEE_IDENTITY_REPAIR", "repair_id": command.repair_id,
                      "case_id": command.case_id, "case_revision": revision,
                      "retired_branch_fee_id": command.branch_fee_id, "members": results,
                      "rewritten_alias_keys": sorted(rewritten)}
            conn.execute("""INSERT INTO sanzo_core.fee_identity_repair_events
                (tenant_id,repair_id,request_hash,original_fee_id,branch_fee_id,case_id,actor_id,evidence,result)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (actor.tenant_id, command.repair_id, digest,
                command.original_fee_id, command.branch_fee_id, command.case_id, actor.actor_id,
                Jsonb(evidence), Jsonb(result)))
            return result
