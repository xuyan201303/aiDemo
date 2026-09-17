"""W1 business services. Inputs are trusted in-process commands, not HTTP DTOs."""
from __future__ import annotations

from typing import Callable
from uuid import uuid4

from .domain import (
    ConflictError, CorrectionCommand, Observation, Principal, RevokeCommand,
    ScopeError, SyntheticLine, decide, memory_key, normalize_label, request_digest, transition_memory,
    transition_human_reconfirmation,
)
from .postgres import PostgresRepository


class ReviewService:
    """Atomic correction, audit and exact original-label classification memory.

    fault_hook is only an in-process test seam. Tests must label injected failures
    INJECTED_TEST_CASE; there is no route, environment switch or remote control.
    """

    def __init__(self, repository: PostgresRepository,
                 fault_hook: Callable[[str], None] | None = None):
        self.repository = repository
        self._fault_hook = fault_hook

    def _checkpoint(self, stage: str) -> None:
        if self._fault_hook is not None:
            self._fault_hook(stage)

    def create_case(self, actor: Principal, case_id: str, name: str) -> dict:
        actor.require_case(case_id)
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError("Case ID required")
        if not isinstance(name, str) or not name.strip() or len(name) > 1000:
            raise ValueError("Case name required, at most 1000 characters")
        with self.repository.transaction() as conn:
            return self.repository.insert_case(conn, actor.tenant_id, case_id, name.strip())

    def add_observation(self, actor: Principal, line: SyntheticLine) -> dict:
        actor.require_editor()
        line.validate()
        obs = line.observation
        if obs.tenant_id != actor.tenant_id:
            raise ScopeError("Observation is outside the authorized tenant")
        actor.require_case(obs.case_id)
        with self.repository.transaction() as conn:
            self.repository.case(conn, actor.tenant_id, obs.case_id, lock=True)
            self.repository.insert_observation(conn, line)
            self.repository.advance_case(conn, actor.tenant_id, obs.case_id)
            return self.repository.item(conn, actor.tenant_id, obs.item_id)

    def _read_item(self, conn, actor: Principal, item_id: str) -> dict:
        actor.require_editor()
        case_id = self.repository.item_case_id(conn, actor.tenant_id, item_id)
        actor.require_case(case_id)
        return self.repository.item(conn, actor.tenant_id, item_id)

    def get_item(self, actor: Principal, item_id: str) -> dict:
        actor.require_editor()
        with self.repository.transaction() as conn:
            return self._read_item(conn, actor, item_id)

    def classify(self, actor: Principal, item_id: str):
        """Return a new classification decision without rewriting a saved item."""
        actor.require_editor()
        with self.repository.transaction() as conn:
            item = self._read_item(conn, actor, item_id)
            observation = Observation.from_json(item["observation"])
            slot = self.repository.lookup_memory(conn, observation, item["source_ref"], item["input_kind"])
            fee = self.repository.fee(conn, actor.tenant_id, slot["fee_id"]) if slot else None
            return decide(observation, item["current_price"], slot, fee, item["source_ref"], item["input_kind"])

    def _replay(self, conn, actor: Principal, command, digest: str, operation: str):
        # Serialize even an as-yet absent key before reading it. The same key
        # used by two workers cannot both pass the first existence check.
        self.repository.advisory(conn, "idempotency", actor.tenant_id, command.idempotency_key)
        old = self.repository.command(conn, actor.tenant_id, command.idempotency_key)
        if old is None:
            return None
        actor.require_case(old["case_id"])
        if old["request_hash"] != digest or old["operation"] != operation:
            raise ConflictError("Idempotency key reused for another payload or actor")
        return old["response_json"]

    def _locked_item(self, conn, actor: Principal, command) -> dict:
        case_id = self.repository.item_case_id(conn, actor.tenant_id, command.item_id)
        actor.require_case(case_id)
        case = self.repository.case(conn, actor.tenant_id, case_id, lock=True)
        if case["case_revision"] != command.expected_case_revision:
            raise ConflictError("Stale case revision; keep edits and reload")
        self.repository.lock_fee_catalog(conn, actor.tenant_id)
        item = self.repository.item(conn, actor.tenant_id, command.item_id, lock=True)
        if item["version"] != command.expected_item_version:
            raise ConflictError("Stale item version; keep edits and reload")
        return item

    def correct(self, actor: Principal, command: CorrectionCommand) -> dict:
        actor.require_editor()
        command.validate()
        with self.repository.transaction() as conn:
            return self._correct_in_transaction(conn, actor, command)

    def _correct_in_transaction(self, conn, actor: Principal, command: CorrectionCommand,
                                *, human_reconfirm: bool = False) -> dict:
        """Existing correction unit; row-only intent follows aggregate version checks."""
        if type(human_reconfirm) is not bool:
            raise ValueError("Explicit internal human reconfirm intent required")
        actor.require_editor()
        command.validate()
        digest = request_digest(actor, command)
        replay = self._replay(conn, actor, command, digest, "CORRECT")
        if replay is not None:
            return replay
        item = self._locked_item(conn, actor, command)
        observation = Observation.from_json(item["observation"])
        key = memory_key(observation, item["source_ref"], item["input_kind"])
        category_action = command.target_fee_id is not None or command.new_fee_name is not None
        slot = None
        if category_action and key is not None:
            # This lock also protects the first insertion where no slot row
            # exists yet. Every writer takes locks in the same order:
            # idempotency -> case -> item -> memory key -> display name.
            self.repository.advisory(conn, "memory", actor.tenant_id, key)
            slot = self.repository.slot(conn, actor.tenant_id, key)
            if (slot["version"] if slot else 0) != command.expected_memory_version:
                raise ConflictError("Stale memory version; correction was not saved")

        target = item["current_fee_id"]
        fee = None
        if command.target_fee_id is not None:
            fee = self.repository.fee(conn, actor.tenant_id, command.target_fee_id)
            if fee is None:
                raise ScopeError("Fee not available in authorized scope")
            target = fee["fee_id"]
        elif command.new_fee_name is not None:
            normalized = normalize_label(command.new_fee_name)
            self.repository.advisory(conn, "display_name", actor.tenant_id, normalized)
            if self.repository.named_fee_exists(conn, actor.tenant_id, normalized):
                raise ConflictError("Name exists: explicitly select the existing fee ID")
            target = str(uuid4())
            self.repository.insert_fee(
                conn, actor.tenant_id, target, command.new_fee_name.strip(), normalized
            )
            fee = {"fee_id": target, "display_name": command.new_fee_name.strip()}

        price = command.unit_price if command.unit_price is not None else item["current_price"]
        memory_effect = "UNCHANGED_AMOUNT_ONLY"
        memory_action = "UNCHANGED_AMOUNT_ONLY"
        memory_version = slot["version"] if slot else 0
        if category_action and key is None:
            memory_effect = "NOT_ELIGIBLE_CONTEXT_OR_SOURCE"
            memory_action = memory_effect
        elif category_action:
            effective = slot or self.repository.lookup_memory(conn, observation, item["source_ref"], item["input_kind"])
            transition = transition_human_reconfirmation if human_reconfirm else transition_memory
            memory_effect = transition(effective, target, command.replace_existing_mapping)
            memory_action = memory_effect
            if memory_effect == "ACTIVE":
                memory_action = (
                    "ACTIVE_CREATED" if effective is None else
                    "ACTIVE_RECONFIRMED" if effective["status"] != "ACTIVE" else
                    "ACTIVE_REUSED" if effective["fee_id"] == target else "ACTIVE_SUPERSEDED"
                )
            memory_version += 1

        item_version = self.repository.update_item(
            conn, actor.tenant_id, item["item_id"], target, price, refresh_name=category_action
        )
        case_revision = self.repository.advance_case(conn, actor.tenant_id, item["case_id"])
        self._checkpoint("after_item")
        response = {
            "event_id": str(uuid4()), "item_id": item["item_id"],
            "item_version": item_version, "case_revision": case_revision,
            "fee_id": target, "unit_price": price,
            "memory_effect": memory_effect, "memory_version": memory_version,
            "memory_action": memory_action,
        }
        adopted = self.repository.item(conn, actor.tenant_id, item["item_id"])
        response.update(fee_name=adopted["current_fee_name"], fee_name_version=adopted["current_fee_name_version"])
        self.repository.correction_event(conn, actor, item, response)
        if category_action and key is not None:
            self.repository.write_memory(
                conn, actor=actor, item=item, observation=observation, key=key,
                version=memory_version, status=memory_effect, target=target,
                event_id=str(uuid4()), source_event_id=response["event_id"],
                reason="EXPLICIT_ROW_HUMAN_RECONFIRM" if human_reconfirm else "EXPLICIT_ITEM_FEE_CONFIRMATION",
            )
        self._checkpoint("after_memory")
        self._checkpoint("before_command")
        self.repository.save_command(
            conn, actor, command, "CORRECT", digest, item["case_id"], response
        )
        return response

    def revoke_memory(self, actor: Principal, command: RevokeCommand) -> dict:
        """Append a revocation; never modify the old quote or resurrect history."""
        actor.require_editor()
        command.validate(allow_legacy_projection=True)
        digest = request_digest(actor, command)
        with self.repository.transaction() as conn:
            replay = self._replay(conn, actor, command, digest, "REVOKE")
            if replay is not None:
                return replay
            item = self._locked_item(conn, actor, command)
            observation = Observation.from_json(item["observation"])
            key = memory_key(observation, item["source_ref"], item["input_kind"])
            if key is None:
                raise ConflictError("No eligible memory attached to this item")
            self.repository.advisory(conn, "memory", actor.tenant_id, key)
            slot = self.repository.slot(conn, actor.tenant_id, key)
            effective = slot or self.repository.lookup_memory(conn, observation, item["source_ref"], item["input_kind"])
            if effective is None or (slot["version"] if slot else 0) != command.expected_memory_version:
                raise ConflictError("Stale or unavailable memory")
            version = (slot["version"] if slot else 0) + 1
            event_id = str(uuid4())
            self.repository.write_memory(
                conn, actor=actor, item=item, observation=observation, key=key,
                version=version, status="REVOKED", target=None, event_id=event_id,
                source_event_id=None, reason=command.reason.strip(),
            )
            self._checkpoint("after_memory")
            response = {
                "event_id": event_id, "item_id": item["item_id"],
                "item_version": item["version"], "case_revision": item["case_revision"],
                "memory_effect": "REVOKED", "memory_version": version,
            }
            self._checkpoint("before_command")
            self.repository.save_command(
                conn, actor, command, "REVOKE", digest, item["case_id"], response
            )
            return response
