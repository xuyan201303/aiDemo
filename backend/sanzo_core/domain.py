"""W1 domain contracts, reusing the unchanged framework's pure reference rules.

W1 accepts trusted synthetic input; the local Demo adds explicit human source
confirmation. Principal/source_ready/Context.ready remain internal, never HTTP DTOs.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import hashlib
import re
from typing import Any

from quote_framework.domain import (
    Actor, ConflictError, Context, Correction, Decision, DecisionKind,
    Observation, ScopeError, canonical_json, decimal_text, normalize_label,
)


@dataclass(frozen=True)
class Principal(Actor):
    """Trusted caller; None grants the framework's same-tenant shared-case scope."""
    allowed_case_ids: frozenset[str] | None = None

    def require_editor(self) -> None:
        super().require_editor()
        if not isinstance(self.tenant_id, str) or not self.tenant_id.strip():
            raise ScopeError("Trusted tenant required")
        if not isinstance(self.actor_id, str) or not self.actor_id.strip():
            raise ScopeError("Trusted actor required")
        if self.allowed_case_ids is not None:
            if not isinstance(self.allowed_case_ids, frozenset) or any(
                not isinstance(v, str) or not v for v in self.allowed_case_ids
            ):
                raise ScopeError("Invalid case authorization scope")

    def require_case(self, case_id: str) -> None:
        self.require_editor()
        if self.allowed_case_ids is not None and case_id not in self.allowed_case_ids:
            raise ScopeError("Case not available in authorized scope")


def _version(value: int, minimum: int = 1) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError("Expected version must be an integer in range")


@dataclass(frozen=True, kw_only=True)
class CorrectionCommand(Correction):
    expected_case_revision: int

    def validate(self) -> None:
        super().validate()
        _version(self.expected_case_revision)
        if not isinstance(self.item_id, str) or not self.item_id.strip():
            raise ValueError("Item ID required")
        if not isinstance(self.idempotency_key, str):
            raise ValueError("Idempotency key must be text")


@dataclass(frozen=True)
class RevokeCommand:
    item_id: str
    expected_case_revision: int
    expected_item_version: int
    expected_memory_version: int
    idempotency_key: str
    reason: str

    def validate(self, *, allow_legacy_projection: bool = False) -> None:
        _version(self.expected_case_revision)
        _version(self.expected_item_version)
        _version(self.expected_memory_version, minimum=0 if allow_legacy_projection else 1)
        if not isinstance(self.item_id, str) or not self.item_id.strip():
            raise ValueError("Item ID required")
        if not isinstance(self.idempotency_key, str) or not self.idempotency_key or len(self.idempotency_key) > 200:
            raise ValueError("Idempotency key required")
        if not isinstance(self.reason, str) or not self.reason.strip() or len(self.reason) > 2000:
            raise ValueError("Explicit revocation reason required")


@dataclass(frozen=True)
class SyntheticLine:
    """Known decimal input only; missing/INCLUDED values must not become zero.

    Real extraction, amount-status extraction and source verification belong to W2.
    Optional source_ref=None represents missing evidence and requires source_ready=False.
    """
    observation: Observation
    source_ref: dict[str, Any] | None
    ai_suggested_name: str | None = None
    raw_value: str | None = None
    quotation_date: str | None = None
    valid_until: str | None = None
    input_kind: str = "SYNTHETIC_STRUCTURED"

    def validate(self) -> None:
        if self.input_kind != "SYNTHETIC_STRUCTURED":
            raise ValueError("W1 accepts explicitly synthetic structured input only")
        if not isinstance(self.observation, Observation):
            raise ValueError("Framework Observation required")
        self.observation.validate()
        if self.raw_value is not None and not isinstance(self.raw_value, str):
            raise ValueError("raw_value must remain text")
        if self.ai_suggested_name is not None:
            normalize_label(self.ai_suggested_name)
        for value in (self.quotation_date, self.valid_until):
            if value is not None:
                if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    raise ValueError("Date must be ISO date text")
                date.fromisoformat(value)
        if self.source_ref is None:
            if self.observation.source_ready:
                raise ValueError("Missing source cannot be marked ready")
            return
        if not isinstance(self.source_ref, dict) or self.source_ref.get("synthetic") is not True:
            raise ValueError("Source must explicitly identify synthetic evidence")
        if self.source_ref.get("document_id") != self.observation.document_id:
            raise ValueError("Source belongs to another document")
        blocks = self.source_ref.get("source_block_ids")
        if not isinstance(blocks, list) or not all(isinstance(x, str) and x for x in blocks):
            raise ValueError("Source block identifiers required")
        if self.observation.source_ready and self.observation.source_id not in blocks:
            raise ValueError("Observation source does not belong to supplied evidence")
        canonical_json(self.source_ref)  # Reject non-JSON structured input.



def memory_origin(source_ref: dict | None, input_kind: str | None) -> str:
    """Persist source scope without reinterpreting existing non-mock memory.

    MOCK_SDK is an application fixture source, never a real SDK attestation.
    Its three markers must agree, including when reading a persisted item.
    """
    source = source_ref or {}
    markers = (input_kind == "MOCK_SDK", source.get("origin") == "MOCK_SDK",
               source.get("extraction_origin") == "MOCK_SDK")
    if any(markers):
        if not all(markers):
            raise ValueError("MOCK_SOURCE_MODE_MISMATCH")
        if (not isinstance(source.get("fixture_version"), str)
                or not source["fixture_version"].strip()
                or len(source["fixture_version"]) > 200
                or not isinstance(source.get("fixture_sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", source["fixture_sha256"])
                or source.get("fixture_integrity") != "VERIFIED"):
            raise ValueError("MOCK_FIXTURE_PROOF_REQUIRED")
        return "MOCK_SDK"
    extraction_origin = source.get("extraction_origin")
    if extraction_origin in {"REAL_SDK", "MECHANISM_TEST"}:
        return extraction_origin
    if input_kind in {"SDK_OBSERVED", "MANUAL_CONFIRMED", "SYNTHETIC_STRUCTURED"}:
        return input_kind
    return "LEGACY"


def memory_key(observation: Observation, source_ref: dict | None = None,
               input_kind: str | None = None) -> str | None:
    """REAL uses fee-name-v2; reference/Mock keep their isolated v1 rules.

    Price, currency, company and document roles are not REAL fee-name keys.
    Required complete label evidence and exact fee-line conditions are checked
    separately from document readiness. Existing keys are never rewritten.
    """
    origin = memory_origin(source_ref, input_kind)
    from .fee_policy import uses_fee_policy, key as fee_key
    if uses_fee_policy(source_ref, input_kind):
        return fee_key(observation, source_ref, input_kind)
    key = observation.memory_key()
    if key is None:
        return None
    return "MOCK_SDK:v1:" + key if origin == "MOCK_SDK" else key


def request_digest(actor: Principal, command: CorrectionCommand | RevokeCommand) -> str:
    identity = asdict(actor)
    if identity.get("allowed_case_ids") is not None:
        identity["allowed_case_ids"] = sorted(identity["allowed_case_ids"])
    command_payload = asdict(command)
    if command_payload.get("intent") == "CONFIRM_MEMBERS" and command_payload.get("expected_fee_id") is None and command_payload.get("expected_name_version") is None:
        for key in ("intent", "expected_fee_id", "expected_name_version"):
            command_payload.pop(key, None)
    # Absent optional legacy evidence must retain the deployed row-request
    # digest, so a lost-response retry still returns its original result.
    # Supplied evidence remains part of the digest and cannot be changed.
    for member in command_payload.get("members", ()):
        if isinstance(member, dict) and member.get("expected_memory_evidence") is None:
            member.pop("expected_memory_evidence", None)
    payload = {"actor": identity, "command_type": type(command).__name__, "command": command_payload}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def transition_memory(slot: dict | None, target: str, replace: bool) -> str:
    """Reference-compatible transition; version/explicit intent checked by service."""
    if type(replace) is not bool or not target:
        raise ValueError("Explicit target and boolean replacement required")
    if slot is None:
        return "ACTIVE"
    if slot["status"] not in ("ACTIVE", "CONFLICTED", "REVOKED"):
        raise ValueError("Invalid persisted memory state")
    if replace:
        return "ACTIVE"
    if slot["status"] in ("CONFLICTED", "REVOKED"):
        return slot["status"]
    return "ACTIVE" if slot["fee_id"] == target else "CONFLICTED"


def transition_human_reconfirmation(slot: dict | None, target: str, replace_revoked: bool) -> str:
    """Latest explicit row confirmation supersedes its own name mapping.

    The row service must first lock and validate all item and memory versions.
    This intent is separate from the reference item-correction contract; a
    revoked mapping still needs the existing explicit recovery choice.
    """
    previous = transition_memory(slot, target, replace_revoked)
    if (slot and not replace_revoked
            and any(e.get("status") == "REVOKED" for e in slot.get("evidence_versions", []))):
        # Carry explicit legacy revocation into the new direct slot as REVOKED.
        # CONFLICTED would lose this reason and let the next ordinary save
        # accidentally recover it without the existing explicit revoke choice.
        return "REVOKED"
    if slot is None or slot["status"] in ("ACTIVE", "CONFLICTED"):
        return "ACTIVE"
    return previous


def decide(observation: Observation, current_price: str, slot: dict | None, fee: dict | None,
           source_ref=None, input_kind=None) -> Decision:
    """Classification only. It cannot write prices, confirmation history or AI results."""
    values = {"unit_price": current_price, "currency": observation.currency}
    from .fee_policy import uses_fee_policy, key as fee_key
    if uses_fee_policy(source_ref, input_kind):
        if fee_key(observation, source_ref, input_kind) is None:
            return Decision(DecisionKind.READING_REQUIRED, "FEE_NAME_SOURCE_UNVERIFIED", **values)
        if slot is None:
            return Decision(DecisionKind.AI_REQUIRED, "NO_CONFIRMED_FEE_NAME", **values)
        if slot["status"] != "ACTIVE":
            return Decision(DecisionKind.REVIEW_REQUIRED, "MEMORY_" + slot["status"], **values)
        if fee is None:
            return Decision(DecisionKind.REVIEW_REQUIRED, "CONFIRMED_FEE_UNAVAILABLE", **values)
        return Decision(DecisionKind.MEMORY_APPLIED,
            "LEGACY_CONFIRMED_FEE_SIGNATURE" if slot.get("resolution") == "LEGACY_PROJECTION" else "CONFIRMED_FEE_SIGNATURE",
            fee_id=fee["fee_id"], fee_name=fee["display_name"], memory_version=slot["version"], **values)
    if not observation.source_ready:
        return Decision(DecisionKind.READING_REQUIRED, "SOURCE_OR_STRUCTURE_UNVERIFIED", **values)
    if not observation.context.complete():
        return Decision(DecisionKind.REVIEW_REQUIRED, "INCOMPLETE_CONTEXT", **values)
    if not observation.forwarder_id:
        return Decision(DecisionKind.AI_REQUIRED, "UNKNOWN_FORWARDER_NO_EXACT_REUSE", **values)
    if slot is None:
        return Decision(DecisionKind.AI_REQUIRED, "NO_CONFIRMED_MAPPING", **values)
    if slot["status"] != "ACTIVE":
        return Decision(DecisionKind.REVIEW_REQUIRED, "MEMORY_" + slot["status"],
                        memory_version=slot["version"], **values)
    if fee is None or fee["fee_id"] != slot["fee_id"]:
        raise RuntimeError("Broken memory/category binding")
    return Decision(DecisionKind.MEMORY_APPLIED, "EXACT_CONFIRMED_CONTEXT",
                    fee_id=fee["fee_id"], fee_name=fee["display_name"],
                    memory_version=slot["version"], **values)


@dataclass(frozen=True)
class ManualConfirmedLine:
    """Trusted internal representation of an explicit human source confirmation.

    This is not SDK extraction. The HTTP layer must not accept source_ready,
    Context.ready, or arbitrary source_ref from a client.
    """
    observation: Observation
    source_ref: dict[str, Any]
    raw_value: str | None = None
    quotation_date: str | None = None
    valid_until: str | None = None
    ai_suggested_name: str | None = None
    input_kind: str = "MANUAL_CONFIRMED"

    def validate(self) -> None:
        if self.input_kind != "MANUAL_CONFIRMED":
            raise ValueError("Manual input must remain explicitly marked")
        if not isinstance(self.observation, Observation):
            raise ValueError("Framework Observation required")
        self.observation.validate()
        if self.ai_suggested_name is not None:
            raise ValueError("Manual input cannot manufacture an AI suggestion")
        if self.raw_value is not None and not isinstance(self.raw_value, str):
            raise ValueError("raw_value must remain text")
        for value in (self.quotation_date, self.valid_until):
            if value is not None:
                if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    raise ValueError("Date must be ISO date text")
                date.fromisoformat(value)
        source = self.source_ref
        if not isinstance(source, dict) or source.get("origin") != "MANUAL_CONFIRMED":
            raise ValueError("Explicit manual source confirmation required")
        if source.get("document_id") != self.observation.document_id:
            raise ValueError("Source belongs to another document")
        if source.get("source_block_ids") != [self.observation.source_id]:
            raise ValueError("Server source binding required")
        if not re.fullmatch(r"[0-9a-f]{64}", source.get("document_sha256", "")):
            raise ValueError("Registered document hash required")
        if not source.get("manual_confirmed_by"):
            raise ValueError("Human confirmation actor required")
        if source.get("locator_status") != "UNAVAILABLE" or source.get("locator") is not None:
            raise ValueError("Manual entry must not invent source coordinates")
        canonical_json(source)


@dataclass(frozen=True)
class SDKObservedLine:
    """Application observation, not a declaration of the SDK public API.

    Constructed only after the ingestion service validates SDK blocks and AI
    field citations. No HTTP endpoint accepts this internal type.
    """
    observation: Observation
    source_ref: dict[str, Any]
    raw_value: str | None = None
    ai_suggested_name: str | None = None
    quotation_date: str | None = None
    valid_until: str | None = None
    input_kind: str = "SDK_OBSERVED"

    def validate(self):
        if self.input_kind not in {"SDK_OBSERVED", "MOCK_SDK"} or not isinstance(self.observation, Observation):
            raise ValueError("VERIFIED_OBSERVATION_REQUIRED")
        self.observation.validate()
        source = self.source_ref
        expected_origin = "MOCK_SDK" if self.input_kind == "MOCK_SDK" else "SDK_OBSERVED"
        if not isinstance(source, dict) or source.get("origin") != expected_origin:
            raise ValueError("SDK_SOURCE_REQUIRED")
        memory_origin(source, self.input_kind)
        if source.get("document_id") != self.observation.document_id:
            raise ValueError("SOURCE_DOCUMENT_MISMATCH")
        if not re.fullmatch(r"[0-9a-f]{64}", source.get("document_sha256", "")):
            raise ValueError("SOURCE_DIGEST_REQUIRED")
        ids = source.get("source_block_ids")
        if not isinstance(ids, list) or self.observation.source_id not in ids:
            raise ValueError("SOURCE_BLOCK_BINDING_REQUIRED")
        if source.get("locator_status") not in {"VERIFIED", "PAGE_ONLY", "UNAVAILABLE"}:
            raise ValueError("EXPLICIT_LOCATOR_STATUS_REQUIRED")
        if self.ai_suggested_name is not None:
            normalize_label(self.ai_suggested_name)
        if not isinstance(self.raw_value, str) or not self.raw_value:
            raise ValueError("ORIGINAL_AMOUNT_TEXT_REQUIRED")
        for value in (self.quotation_date, self.valid_until):
            if value is not None:
                date.fromisoformat(value)
        canonical_json(source)
