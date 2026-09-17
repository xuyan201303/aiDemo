"""Cumulative OpenAI allowance in the existing PostgreSQL repository.

This stores only the explicitly approved authorization identity, model, sample
hashes, cap, and append-only consumption records. No credential, raw document,
prompt, or provider response belongs here. Every consumption commits before
calling a credential supplier or transport; uncertain/crashed calls never refund.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any
from uuid import uuid4

from psycopg.types.json import Jsonb

from .postgres import PostgresRepository

_HASH = re.compile(r"[0-9a-f]{64}")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,119}")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")
_PURPOSES = frozenset({"quotation_extraction", "fee_classification"})


class AIBudgetError(RuntimeError):
    """Fixed codes only; database exception text/DSN cannot escape."""
    def __init__(self, code: str, metadata: dict[str, Any] | None = None):
        self.code = code
        self.metadata = dict(metadata or {})
        super().__init__(code)


@dataclass(frozen=True)
class AIAuthorization:
    authorization_id: str
    model: str
    max_requests: int
    authorized_sample_sha256: frozenset[str]

    def __post_init__(self):
        if not isinstance(self.authorization_id, str) or not _ID.fullmatch(self.authorization_id):
            raise AIBudgetError("AI_AUTHORIZATION_ID_INVALID")
        if not isinstance(self.model, str) or not _MODEL.fullmatch(self.model):
            raise AIBudgetError("AI_MODEL_REQUIRED")
        if type(self.max_requests) is not int or not 0 <= self.max_requests <= 2147483647:
            raise AIBudgetError("AI_REQUEST_BUDGET_INVALID")
        if (not isinstance(self.authorized_sample_sha256, frozenset)
                or not self.authorized_sample_sha256
                or any(not isinstance(value, str) or not _HASH.fullmatch(value)
                       for value in self.authorized_sample_sha256)):
            raise AIBudgetError("AI_AUTHORIZATION_INVALID")


class PostgresAIBudget:
    """Same authorization ID resumes the same immutable grant across processes.

    Creating a new authorization ID requires a new user authorization. Startup
    must take a stable configured ID, never generate one or replace it on restart.
    The cap is shared across both purposes, workers, threads, and process restarts.
    There is deliberately no reset, increase, replacement, or refund API.
    """
    def __init__(
        self, repo: PostgresRepository, authorization_id: str, *, model: str,
        max_requests: int, authorized_sample_sha256: frozenset[str],
    ):
        self.repo = repo
        self.authorization = AIAuthorization(
            authorization_id, model, max_requests, authorized_sample_sha256,
        )
        self._ensure_authorization()

    def _check_record(self, record: dict | None):
        grant = self.authorization
        if record is None or (
            record["authorization_id"] != grant.authorization_id
            or record["provider"] != "openai"
            or record["model"] != grant.model
            or record["max_requests"] != grant.max_requests
            or record["authorized_sample_sha256"] != sorted(grant.authorized_sample_sha256)
        ):
            raise AIBudgetError("AI_AUTHORIZATION_CONFIG_CONFLICT")

    def assert_configuration(
        self, *, model: str, max_requests: int,
        authorized_sample_sha256: frozenset[str],
    ) -> None:
        grant = self.authorization
        if (model != grant.model or max_requests != grant.max_requests
                or authorized_sample_sha256 != grant.authorized_sample_sha256):
            raise AIBudgetError("AI_AUTHORIZATION_CONFIG_CONFLICT")

    def _ensure_authorization(self):
        grant = self.authorization
        try:
            with self.repo.transaction() as conn:
                self.repo.advisory(conn, "ai-authorization", grant.authorization_id)
                conn.execute("""
                    INSERT INTO sanzo_core.ai_authorizations(
                        authorization_id,provider,model,authorized_sample_sha256,max_requests)
                    VALUES(%s,'openai',%s,%s,%s) ON CONFLICT(authorization_id) DO NOTHING
                """, (grant.authorization_id, grant.model,
                      Jsonb(sorted(grant.authorized_sample_sha256)), grant.max_requests))
                self._check_record(conn.execute("""
                    SELECT * FROM sanzo_core.ai_authorizations WHERE authorization_id=%s
                """, (grant.authorization_id,)).fetchone())
        except AIBudgetError:
            raise
        except Exception:
            raise AIBudgetError("AI_BUDGET_STORAGE_UNAVAILABLE") from None

    def _metadata(self, record: dict) -> dict:
        return {
            "budget_scope": "postgres_authorization",
            "authorization_id": self.authorization.authorization_id,
            "max_requests": record["max_requests"],
            "requests_used": record["requests_used"],
            "request_attempts": 0,
        }

    def snapshot(self) -> dict:
        try:
            with self.repo.transaction() as conn:
                record = conn.execute("""
                    SELECT * FROM sanzo_core.ai_authorizations WHERE authorization_id=%s
                """, (self.authorization.authorization_id,)).fetchone()
                self._check_record(record)
                return self._metadata(record)
        except AIBudgetError:
            raise
        except Exception:
            raise AIBudgetError("AI_BUDGET_STORAGE_UNAVAILABLE") from None

    @property
    def requests_used(self) -> int:
        return self.snapshot()["requests_used"]

    def consume_request(self, document_sha256: str, purpose: str) -> dict:
        grant = self.authorization
        if (not isinstance(document_sha256, str) or not _HASH.fullmatch(document_sha256)
                or document_sha256 not in grant.authorized_sample_sha256):
            raise AIBudgetError("AI_SAMPLE_NOT_AUTHORIZED")
        if not isinstance(purpose, str) or purpose not in _PURPOSES:
            raise AIBudgetError("AI_PURPOSE_INVALID")
        try:
            with self.repo.transaction() as conn:
                record = conn.execute("""
                    SELECT * FROM sanzo_core.ai_authorizations
                    WHERE authorization_id=%s FOR UPDATE
                """, (grant.authorization_id,)).fetchone()
                self._check_record(record)
                if record["requests_used"] >= grant.max_requests:
                    raise AIBudgetError("AI_REQUEST_BUDGET_EXHAUSTED", self._metadata(record))
                number = record["requests_used"] + 1
                attempt_id = uuid4().hex
                conn.execute("""
                    INSERT INTO sanzo_core.ai_request_attempts(
                        authorization_id,attempt_number,attempt_id,document_sha256,purpose)
                    VALUES(%s,%s,%s,%s,%s)
                """, (grant.authorization_id, number, attempt_id, document_sha256, purpose))
                record = conn.execute("""
                    UPDATE sanzo_core.ai_authorizations SET requests_used=requests_used+1
                    WHERE authorization_id=%s RETURNING *
                """, (grant.authorization_id,)).fetchone()
                metadata = self._metadata(record)
                metadata.update({
                    "budget_attempt_number": number, "budget_attempt_id": attempt_id,
                    "budget_allowance_consumed": True,
                })
            # Return only after the transaction context has committed and closed.
            return metadata
        except AIBudgetError:
            raise
        except Exception:
            # A commit outcome can be uncertain; never compensate or retry.
            raise AIBudgetError("AI_BUDGET_STORAGE_UNAVAILABLE") from None
