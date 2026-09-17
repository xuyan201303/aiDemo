"""PostgreSQL persistence for W1; no SDK, AI, network API or SQLite fallback."""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
import hashlib
from pathlib import Path
from typing import Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .domain import ConflictError, ScopeError, canonical_json, memory_key, memory_origin


class PostgresRepository:
    """Each unit of work owns one connection and one atomic transaction."""

    def __init__(self, dsn: str):
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("Explicit project PostgreSQL DSN required")
        self._dsn = dsn

    @contextmanager
    def transaction(self) -> Iterator[psycopg.Connection]:
        # psycopg's connection context commits on success, rolls back on any
        # exception, and closes the owned connection on both paths.
        try:
            with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
                yield conn
        except psycopg.errors.UniqueViolation as exc:
            raise ConflictError("Concurrent or duplicate business identifier") from exc

    @staticmethod
    def advisory(conn, namespace: str, *identity: str) -> None:
        value = canonical_json(["sanzo_core", namespace, *identity])
        key = int.from_bytes(hashlib.sha256(value.encode()).digest()[:8],
                             byteorder="big", signed=True)
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (key,))

    def migrate(self) -> dict:
        """Apply packaged SQL exactly once; reject changed/unknown migrations."""
        directory = Path(__file__).with_name("migrations")
        files = sorted(directory.glob("[0-9][0-9][0-9]_*.sql"))
        if not files:
            raise RuntimeError("No packaged PostgreSQL migrations")
        with self.transaction() as conn:
            self.advisory(conn, "migrations")
            conn.execute("CREATE SCHEMA IF NOT EXISTS sanzo_core")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sanzo_core.schema_migrations (
                    version text PRIMARY KEY, sha256 text NOT NULL,
                    applied_at timestamptz NOT NULL DEFAULT current_timestamp
                )
            """)
            rows = conn.execute(
                "SELECT version, sha256 FROM sanzo_core.schema_migrations"
            ).fetchall()
            recorded = {r["version"]: r["sha256"] for r in rows}
            available = {p.name for p in files}
            if set(recorded) - available:
                raise ConflictError("Database contains unknown migration versions")
            applied = []
            for path in files:
                content = path.read_bytes()
                digest = hashlib.sha256(content).hexdigest()
                if path.name in recorded:
                    if recorded[path.name] != digest:
                        raise ConflictError("Applied migration checksum changed")
                    continue
                conn.execute(content.decode("utf-8"), prepare=False)
                conn.execute(
                    "INSERT INTO sanzo_core.schema_migrations(version,sha256) VALUES(%s,%s)",
                    (path.name, digest),
                )
                applied.append(path.name)
            return {"applied": applied, "versions": [p.name for p in files]}

    @staticmethod
    def lock_fee_catalog(conn, tenant_id):
        # Same ordering for import/name/correction writers; held through commit.
        PostgresRepository.advisory(conn, "fee-catalog", tenant_id)

    @staticmethod
    def case(conn, tenant_id: str, case_id: str, *, lock: bool = False) -> dict:
        query = """SELECT tenant_id,case_id,name,case_revision
                   FROM sanzo_core.cases WHERE tenant_id=%s AND case_id=%s"""
        row = conn.execute(query + (" FOR UPDATE" if lock else ""),
                           (tenant_id, case_id)).fetchone()
        if row is None:
            raise ScopeError("Case not available in authorized scope")
        return row

    @staticmethod
    def insert_case(conn, tenant_id: str, case_id: str, name: str) -> dict:
        return conn.execute("""
            INSERT INTO sanzo_core.cases(tenant_id,case_id,name) VALUES(%s,%s,%s)
            RETURNING tenant_id,case_id,name,case_revision
        """, (tenant_id, case_id, name)).fetchone()

    @staticmethod
    def item_case_id(conn, tenant_id: str, item_id: str) -> str:
        row = conn.execute(
            "SELECT case_id FROM sanzo_core.items WHERE tenant_id=%s AND item_id=%s",
            (tenant_id, item_id),
        ).fetchone()
        if row is None:
            raise ScopeError("Item not available in authorized scope")
        return row["case_id"]

    @staticmethod
    def item(conn, tenant_id: str, item_id: str, *, lock: bool = False) -> dict:
        query = """
            SELECT i.tenant_id,i.item_id,i.case_id,i.observation,i.source_ref,
                   i.ai_suggested_name,i.raw_value,i.quotation_date,i.valid_until,
                   i.input_kind,i.currency,i.version,i.current_fee_id,
                   i.current_price_text AS current_price,i.current_fee_name_snapshot AS current_fee_name,
                   i.current_fee_name_version,f.display_name AS effective_fee_name,
                   f.name_version AS effective_fee_name_version,
                   c.case_revision
            FROM sanzo_core.items i
            JOIN sanzo_core.cases c ON c.tenant_id=i.tenant_id AND c.case_id=i.case_id
            LEFT JOIN sanzo_core.fees f ON f.tenant_id=i.tenant_id AND f.fee_id=i.current_fee_id
            WHERE i.tenant_id=%s AND i.item_id=%s
        """
        row = conn.execute(query + (" FOR UPDATE OF i" if lock else ""),
                           (tenant_id, item_id)).fetchone()
        if row is None:
            raise ScopeError("Item not available in authorized scope")
        row["observation"] = canonical_json(row["observation"])
        for field in ("quotation_date", "valid_until"):
            if row[field] is not None:
                row[field] = row[field].isoformat()
        return row

    @staticmethod
    def advance_case(conn, tenant_id: str, case_id: str) -> int:
        return conn.execute("""
            UPDATE sanzo_core.cases SET case_revision=case_revision+1,
                updated_at=current_timestamp WHERE tenant_id=%s AND case_id=%s
            RETURNING case_revision
        """, (tenant_id, case_id)).fetchone()["case_revision"]

    @staticmethod
    def insert_observation(conn, line) -> None:
        import json
        obs = line.observation
        conn.execute("""
            INSERT INTO sanzo_core.items(
                tenant_id,item_id,case_id,observation,source_ref,ai_suggested_name,
                raw_value,quotation_date,valid_until,input_kind,currency,
                current_price,current_price_text)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (obs.tenant_id, obs.item_id, obs.case_id, Jsonb(json.loads(obs.to_json())),
              Jsonb(line.source_ref) if line.source_ref is not None else None,
              line.ai_suggested_name, line.raw_value, line.quotation_date,
              line.valid_until, line.input_kind, obs.currency,
              Decimal(obs.unit_price), obs.unit_price))

    @staticmethod
    def slot(conn, tenant_id: str, key: str | None) -> dict | None:
        if key is None:
            return None
        return conn.execute("""
            SELECT tenant_id,memory_key,version,status,fee_id,evidence_event_id,origin
            FROM sanzo_core.memory_slots WHERE tenant_id=%s AND memory_key=%s
        """, (tenant_id, key)).fetchone()

    def lookup_memory(self, conn, observation, source_ref, input_kind):
        """Read-only compatibility; new policy wins, legacy never gets rewritten."""
        from .domain import Observation
        from .fee_policy import uses_fee_policy, signature, PREFIX
        key = memory_key(observation, source_ref, input_kind)
        current = self.slot(conn, observation.tenant_id, key)
        if current is not None or key is None or not uses_fee_policy(source_ref, input_kind):
            return current
        wanted = signature(observation, source_ref, input_kind)
        rows = conn.execute("""
            SELECT s.tenant_id,s.memory_key,s.version,s.status,s.fee_id,s.evidence_event_id,s.origin,
                   i.observation,i.source_ref,i.input_kind
            FROM sanzo_core.memory_slots s
            JOIN sanzo_core.memory_versions v USING(tenant_id,memory_key,version)
            JOIN sanzo_core.items i ON i.tenant_id=v.tenant_id AND i.item_id=v.item_id
            WHERE s.tenant_id=%s AND s.origin='REAL_SDK' AND s.memory_key NOT LIKE %s
              AND EXISTS (SELECT 1 FROM sanzo_core.memory_events e
                  JOIN sanzo_core.correction_events c ON c.tenant_id=e.tenant_id AND c.event_id=e.source_event_id
                  WHERE e.tenant_id=s.tenant_id AND e.memory_key=s.memory_key AND c.origin='REAL_SDK')
            ORDER BY s.memory_key
        """, (observation.tenant_id, PREFIX + "%")).fetchall()
        matching = [row for row in rows if signature(Observation.from_json(canonical_json(row["observation"])),
            row["source_ref"], row["input_kind"]) == wanted]
        if not matching:
            return None
        evidence = [{k: row[k] for k in ("memory_key", "version", "status", "fee_id", "evidence_event_id")} for row in matching]
        unambiguous = all(row["status"] == "ACTIVE" for row in matching) and len({r["fee_id"] for r in matching}) == 1
        first = matching[0]
        return {**{k: first[k] for k in ("tenant_id", "memory_key", "version", "evidence_event_id", "origin")},
                "status": "ACTIVE" if unambiguous else "CONFLICTED",
                "fee_id": first["fee_id"] if unambiguous else None,
                "resolution": "LEGACY_PROJECTION", "evidence_versions": evidence}

    @staticmethod
    def canonical_fee_candidates(conn, tenant_id: str, authorized_hashes) -> list[dict]:
        """Cross-case current HUMAN REAL vocabulary; no memory or fee writes."""
        from .canonical_fees import canonical_candidates, MAX_CURRENT_SLOTS
        if not authorized_hashes:
            return []
        rows = conn.execute("""
            SELECT s.tenant_id,s.memory_key,s.version,s.status,s.fee_id,s.origin,
                   i.observation,i.source_ref,i.input_kind,f.display_name,
                   (e.status='ACTIVE' AND e.memory_version=s.version
                    AND e.target_fee_id=s.fee_id AND e.origin='REAL_SDK'
                    AND c.origin='REAL_SDK' AND c.after_json->>'fee_id'=s.fee_id
                    AND c.item_id=v.item_id) AS human_confirmed
            FROM sanzo_core.memory_slots s
            JOIN sanzo_core.memory_versions v USING(tenant_id,memory_key,version)
            JOIN sanzo_core.items i ON i.tenant_id=v.tenant_id AND i.item_id=v.item_id
            LEFT JOIN sanzo_core.fees f ON f.tenant_id=s.tenant_id AND f.fee_id=s.fee_id
            LEFT JOIN sanzo_core.memory_events e ON e.tenant_id=s.tenant_id
                AND e.event_id=s.evidence_event_id AND e.memory_key=s.memory_key
            LEFT JOIN sanzo_core.correction_events c ON c.tenant_id=e.tenant_id
                AND c.event_id=e.source_event_id
            WHERE s.tenant_id=%s AND s.origin='REAL_SDK' AND (f.retired IS NULL OR NOT f.retired)
            ORDER BY s.memory_key LIMIT %s
        """, (tenant_id, MAX_CURRENT_SLOTS + 1)).fetchall()
        return canonical_candidates(rows, tenant_id, authorized_hashes)

    @staticmethod
    def fee(conn, tenant_id: str, fee_id: str | None) -> dict | None:
        if fee_id is None:
            return None
        return conn.execute("""
            SELECT tenant_id,fee_id,display_name,normalized_name,name_version FROM sanzo_core.fees
            WHERE tenant_id=%s AND fee_id=%s AND NOT retired
        """, (tenant_id, fee_id)).fetchone()

    @staticmethod
    def named_fee_exists(conn, tenant_id: str, normalized: str) -> bool:
        return conn.execute("""
            SELECT 1 FROM sanzo_core.fees WHERE tenant_id=%s AND normalized_name=%s AND NOT retired LIMIT 1
        """, (tenant_id, normalized)).fetchone() is not None

    @staticmethod
    def insert_fee(conn, tenant_id: str, fee_id: str, name: str, normalized: str) -> None:
        conn.execute("""
            INSERT INTO sanzo_core.fees(tenant_id,fee_id,display_name,normalized_name,origin)
            VALUES(%s,%s,%s,%s,'EXPLICIT_ITEM_CONFIRMATION')
        """, (tenant_id, fee_id, name, normalized))

    @staticmethod
    def update_item(conn, tenant_id: str, item_id: str, fee_id: str | None, price: str, *, refresh_name=False) -> int:
        if refresh_name and fee_id is not None:
            conn.execute("""UPDATE sanzo_core.items i SET current_fee_name_snapshot=f.display_name,
                current_fee_name_version=f.name_version,current_fee_id=f.fee_id FROM sanzo_core.fees f
                WHERE i.tenant_id=%s AND i.item_id=%s AND f.tenant_id=i.tenant_id AND f.fee_id=%s""",
                (tenant_id, item_id, fee_id))
        return conn.execute("""
            UPDATE sanzo_core.items SET current_fee_id=%s,current_price=%s,
                current_price_text=%s,version=version+1,updated_at=current_timestamp
            WHERE tenant_id=%s AND item_id=%s RETURNING version
        """, (fee_id, Decimal(price), price, tenant_id, item_id)).fetchone()["version"]

    @staticmethod
    def correction_event(conn, actor, item: dict, response: dict) -> None:
        conn.execute("""
            INSERT INTO sanzo_core.correction_events(
                tenant_id,event_id,case_id,item_id,actor_id,item_version,
                case_revision,before_json,after_json,response_json,origin)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (actor.tenant_id, response["event_id"], item["case_id"], item["item_id"],
              actor.actor_id, response["item_version"], response["case_revision"],
              Jsonb(item), Jsonb({"fee_id": response["fee_id"],
                  "unit_price": response["unit_price"], "version": response["item_version"],
                  "case_revision": response["case_revision"],
                  **({"fee_name": response["fee_name"], "fee_name_version": response["fee_name_version"]}
                     if "fee_name" in response else {})}), Jsonb(response),
              memory_origin(item["source_ref"], item["input_kind"])))

    @staticmethod
    def write_memory(conn, *, actor, item: dict, observation, key: str,
                     version: int, status: str, target: str | None,
                     event_id: str, source_event_id: str | None, reason: str) -> None:
        origin = memory_origin(item["source_ref"], item["input_kind"])
        if key != memory_key(observation, item["source_ref"], item["input_kind"]):
            raise ValueError("MEMORY_SOURCE_SCOPE_MISMATCH")
        from .fee_policy import uses_fee_policy, signature, name_evidence
        simplified = uses_fee_policy(item["source_ref"], item["input_kind"])
        context_value = signature(observation, item["source_ref"], item["input_kind"]) if simplified else observation.context.signature()
        source_value = name_evidence(observation, item["source_ref"]) if simplified else item["source_ref"]
        active = target if status == "ACTIVE" else None
        conn.execute("""
            INSERT INTO sanzo_core.memory_versions(
                tenant_id,memory_key,version,status,fee_id,target_fee_id,actor_id,
                item_id,raw_label,context_json,source_ref,reason,origin)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (actor.tenant_id, key, version, status, active, target, actor.actor_id,
              item["item_id"], observation.raw_label, Jsonb(context_value),
              Jsonb(source_value) if source_value is not None else None, reason, origin))
        conn.execute("""
            INSERT INTO sanzo_core.memory_events(
                tenant_id,event_id,memory_key,memory_version,status,target_fee_id,
                actor_id,source_event_id,reason,origin) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (actor.tenant_id, event_id, key, version, status, target,
              actor.actor_id, source_event_id, reason, origin))
        conn.execute("""
            INSERT INTO sanzo_core.memory_slots(
                tenant_id,memory_key,version,status,fee_id,evidence_event_id,origin)
            VALUES(%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(tenant_id,memory_key) DO UPDATE SET
                version=excluded.version,status=excluded.status,
                fee_id=excluded.fee_id,evidence_event_id=excluded.evidence_event_id,origin=excluded.origin
        """, (actor.tenant_id, key, version, status, active, event_id, origin))

    @staticmethod
    def command(conn, tenant_id: str, key: str) -> dict | None:
        return conn.execute("""
            SELECT request_hash,response_json,case_id,item_id,operation
            FROM sanzo_core.commands WHERE tenant_id=%s AND idempotency_key=%s
        """, (tenant_id, key)).fetchone()

    @staticmethod
    def save_command(conn, actor, command, operation: str, digest: str,
                     case_id: str, response: dict) -> None:
        conn.execute("""
            INSERT INTO sanzo_core.commands(
                tenant_id,idempotency_key,operation,actor_id,case_id,item_id,
                request_hash,response_json) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
        """, (actor.tenant_id, command.idempotency_key, operation, actor.actor_id,
              case_id, command.item_id, digest, Jsonb(response)))
