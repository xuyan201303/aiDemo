"""Project-owned contracts. These are NOT document_sdk public API declarations."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
import hashlib
import json
import unicodedata


class ConflictError(ValueError):
    """Stale version or idempotency key reused with another request."""


class ScopeError(PermissionError):
    """Resource not available inside the authorized tenant."""


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def normalize_label(value: str) -> str:
    """v1: NFC + whitespace only; preserve punctuation, numbers, and case."""
    if not isinstance(value, str) or not value.strip() or len(value) > 1024:
        raise ValueError("A nonempty raw fee label of at most 1024 characters is required")
    return " ".join(unicodedata.normalize("NFC", value).split())


def decimal_text(value: str) -> str:
    """Reference accepts already disambiguated decimal strings, not raw locale text."""
    if not isinstance(value, str) or len(value) > 64 or not value:
        raise ValueError("Price must be an explicit decimal string")
    import re
    if not re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", value):
        raise ValueError("Ambiguous or non-canonical decimal input")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("Invalid decimal") from exc
    if not parsed.is_finite() or len(parsed.as_tuple().digits) > 30:
        raise ValueError("Price exceeds reference limits")
    return value  # Do not round, recalculate, or overwrite the original decimal representation.


@dataclass(frozen=True)
class Context:
    """None means UNKNOWN. 'N/A' is an explicit, verified not-applicable value.

    The ready flag is an internal validation result, NEVER trusted from HTTP clients.
    All five dimensions are required for exact reuse in this conservative v1 reference.
    More flexible production predicates require a versioned policy change and tests.
    """
    transport_mode: str | None
    equipment: str | None
    billing_unit: str | None
    charge_scope: str | None
    extra_terms: str | None
    ready: bool = True

    def __post_init__(self) -> None:
        if type(self.ready) is not bool:
            raise ValueError("Context.ready must be an internal boolean")
        for value in (self.transport_mode, self.equipment, self.billing_unit, self.charge_scope, self.extra_terms):
            if value is not None and not isinstance(value, str):
                raise ValueError("Context values must be text or explicit unknown")

    def complete(self) -> bool:
        return self.ready and all(
            isinstance(value, str) and bool(value.strip())
            for value in (
                self.transport_mode, self.equipment, self.billing_unit,
                self.charge_scope, self.extra_terms,
            )
        )

    def signature(self) -> dict[str, str]:
        if not self.complete():
            raise ValueError("Unknown or unvalidated context is not a wildcard")
        return {
            name: normalize_label(value)
            for name, value in asdict(self).items()
            if name != "ready"
        }


@dataclass(frozen=True)
class Observation:
    tenant_id: str
    item_id: str
    case_id: str
    document_id: str
    source_id: str
    forwarder_id: str | None
    raw_label: str
    unit_price: str
    currency: str
    context: Context
    source_ready: bool = True

    def validate(self) -> None:
        for key in (self.tenant_id, self.item_id, self.case_id, self.document_id, self.source_id):
            if not isinstance(key, str) or not key.strip():
                raise ValueError("Stable server-controlled IDs are required")
        if type(self.source_ready) is not bool:
            raise ValueError("source_ready must be an internal boolean")
        if not isinstance(self.context, Context):
            raise ValueError("Context contract required")
        normalize_label(self.raw_label)
        decimal_text(self.unit_price)
        if len(self.currency) != 3 or not self.currency.isascii() or not self.currency.isupper() or not self.currency.isalpha():
            raise ValueError("Currency must be a validated uppercase three-letter code")

    def memory_key(self) -> str | None:
        # Price, currency, case_id, item_id, and document_id deliberately do not enter this key.
        # Currency validation still applies to the CURRENT quotation independently.
        if not self.source_ready or not self.forwarder_id or not self.context.complete():
            return None
        payload = {
            "tenant": self.tenant_id,
            "forwarder": self.forwarder_id,
            "raw_label": normalize_label(self.raw_label),
            "context": self.context.signature(),
            "normalization_version": "nfc-whitespace-v1",
            "policy_version": "exact-context-v1",
        }
        return hashlib.sha256(canonical_json(payload).encode()).hexdigest()

    def to_json(self) -> str:
        return canonical_json(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> Observation:
        value = json.loads(raw)
        value["context"] = Context(**value["context"])
        return cls(**value)


@dataclass(frozen=True)
class Actor:
    """Injected by a future verified authentication layer, not by a request payload."""
    tenant_id: str
    actor_id: str
    can_edit: bool = True

    def require_editor(self) -> None:
        if type(self.can_edit) is not bool:
            raise ScopeError("Invalid permission representation")
        if not self.tenant_id or not self.actor_id or not self.can_edit:
            raise ScopeError("An authorized editor is required")


class DecisionKind(str, Enum):
    MEMORY_APPLIED = "MEMORY_APPLIED"
    AI_REQUIRED = "AI_REQUIRED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    READING_REQUIRED = "READING_REQUIRED"


@dataclass(frozen=True)
class Decision:
    kind: DecisionKind
    reason: str
    unit_price: str
    currency: str
    fee_id: str | None = None
    fee_name: str | None = None
    memory_version: int = 0


@dataclass(frozen=True)
class Correction:
    item_id: str
    expected_item_version: int
    expected_memory_version: int
    idempotency_key: str
    target_fee_id: str | None = None
    new_fee_name: str | None = None
    unit_price: str | None = None
    replace_existing_mapping: bool = False

    def validate(self) -> None:
        if type(self.expected_item_version) is not int or type(self.expected_memory_version) is not int:
            raise ValueError("Versions must be integers, not booleans or strings")
        if type(self.replace_existing_mapping) is not bool:
            raise ValueError("Explicit replacement flag must be a boolean")
        if self.expected_item_version < 1 or self.expected_memory_version < 0:
            raise ValueError("Invalid version")
        if not self.idempotency_key or len(self.idempotency_key) > 200:
            raise ValueError("An idempotency key is required")
        if self.target_fee_id is not None and self.new_fee_name is not None:
            raise ValueError("Choose an existing fee OR create one, not both")
        if self.new_fee_name is not None:
            normalize_label(self.new_fee_name)
        if self.target_fee_id == "":
            raise ValueError("Empty target fee ID")
        if self.unit_price is not None:
            decimal_text(self.unit_price)
        if self.target_fee_id is None and self.new_fee_name is None and self.unit_price is None:
            raise ValueError("Empty correction")
        if self.replace_existing_mapping and self.target_fee_id is None and self.new_fee_name is None:
            raise ValueError("Memory replacement requires a fee correction")
