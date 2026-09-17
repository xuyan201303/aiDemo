"""Integration boundaries only. No real SDK signatures or credentials are assumed."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol

from .domain import Observation


class IntegrationNotReady(RuntimeError):
    pass


@dataclass(frozen=True)
class Candidate:
    proposed_fee_name: str | None
    reason: str
    source_ids: tuple[str, ...]
    # Intentionally no price output and no claim that the proposal is human-confirmed.


class ClassificationPort(Protocol):
    def propose(self, item: Observation, related_confirmations: tuple[dict, ...]) -> Candidate: ...


class DocumentReadPort(Protocol):
    """Our app contract, NOT a promise that document_sdk already exposes read_document."""
    def read_document(self, document_id: str, private_object_key: str) -> dict: ...


class UnconfiguredSdkAdapter:
    def read_document(self, document_id: str, private_object_key: str) -> dict:
        raise IntegrationNotReady("Verify real document_sdk interfaces in W0 before binding")


class UnconfiguredOpenAIAdapter:
    def propose(self, item: Observation, related_confirmations: tuple[dict, ...]) -> Candidate:
        raise IntegrationNotReady("No external requests configured or permitted in this reference")
