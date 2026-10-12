"""Append-only human checks of this quotation; never SDK facts or learned rules."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re
from uuid import uuid4

from psycopg.types.json import Jsonb

from .domain import ConflictError, Principal, ScopeError, canonical_json, request_digest
from .service import ReviewService

CONDITION_FIELDS = ("equipment", "billing_unit", "charge_scope", "extra_terms")


def confirmation_value(item: dict, kind: str, field: str | None = None) -> dict:
    """Relevant value + evidence, independent of later fee-name/catalog changes."""
    observation = item.get("observation") or {}
    source = item.get("source_ref") or {}
    common = {"item_id": item["item_id"], "document_id": observation.get("document_id"),
              "source_ref": source, "raw_label": observation.get("raw_label")}
    if kind == "CONDITION_VALUE":
        context = item.get("effective_context") or observation.get("context") or {}
        overlay = (item.get("condition_overrides") or {}).get(field) or {}
        return {**common, "field": field, "value": context.get(field),
                "condition_event_id": overlay.get("event_id")}
    if kind == "SOURCE_REVIEW":
        context = item.get("effective_context") or observation.get("context") or {}
        return {**common, "current_price": item.get("current_price"),
                "currency": item.get("currency"), "raw_value": item.get("raw_value"),
                "amount_change_event_id": item.get("amount_change_event_id"),
                "conditions": {name: context.get(name) for name in CONDITION_FIELDS},
                "condition_event_ids": {name: overlay.get("event_id") for name, overlay
                    in (item.get("condition_overrides") or {}).items()}}
    raise ValueError("確認する事項を選択してください")


def confirmation_anchor(item: dict, kind: str, field: str | None = None) -> str:
    return sha256(canonical_json(confirmation_value(item, kind, field)).encode()).hexdigest()


def project_confirmations(item: dict, events: list[dict], document_sha256: str | None) -> dict:
    """Latest attestation for each object, retaining stale attestations as history."""
    result = {"conditions": {}, "source": None}
    for event in events:
        field, kind = event["field_name"], event["confirmation_kind"]
        projected = {**event, "created_at": event["created_at"].isoformat()
                     if hasattr(event["created_at"], "isoformat") else event["created_at"]}
        projected["valid"] = bool(
            event["document_sha256"] == document_sha256
            and event["anchor_sha256"] == confirmation_anchor(item, kind, field))
        if kind == "CONDITION_VALUE":
            result["conditions"][field] = projected
        else:
            result["source"] = projected
    return result


@dataclass(frozen=True, kw_only=True)
class QuoteConfirmationCommand:
    case_id: str
    row_id: str
    item_id: str
    expected_case_revision: int
    expected_item_version: int
    idempotency_key: str
    kind: str
    evidence_note: str
    field: str | None = None
    value: str | None = None

    def validate(self):
        if any(not isinstance(value, str) or not value.strip() or len(value) > 200
               for value in (self.case_id, self.item_id, self.idempotency_key)):
            raise ValueError("案件・明細・保存キーを確認してください")
        if not isinstance(self.row_id, str) or not re.fullmatch(r"[0-9a-f]{64}", self.row_id):
            raise ValueError("比較行を確認してください")
        if (type(self.expected_case_revision) is not int or self.expected_case_revision < 1
                or type(self.expected_item_version) is not int or self.expected_item_version < 1):
            raise ValueError("案件・明細の版を確認してください")
        if (not isinstance(self.evidence_note, str) or not self.evidence_note.strip()
                or len(self.evidence_note) > 2000):
            raise ValueError("確認した原文位置と照合の根拠を2000文字以内で入力してください")
        if self.kind == "CONDITION_VALUE":
            if self.field not in CONDITION_FIELDS or not isinstance(self.value, str) or not self.value.strip():
                raise ValueError("確認する条件と現在の値を選択してください")
            if len(self.value) > 1024:
                raise ValueError("条件は1024文字以内にしてください")
        elif self.kind != "SOURCE_REVIEW" or self.field is not None or self.value is not None:
            raise ValueError("確認する事項を選択してください")


class QuoteConfirmationService:
    def __init__(self, repository, storage_dir: Path, fault_hook=None):
        from .demo_store import DemoStore
        self.repository = repository
        self.store = DemoStore(repository)
        self.storage_dir = Path(storage_dir)
        self.review = ReviewService(repository, fault_hook=fault_hook)

    def confirm(self, actor: Principal, command: QuoteConfirmationCommand) -> dict:
        from sanzo_demo.comparison import comparison_projection, comparison_status
        from sanzo_demo.extraction import applicable_value
        from sanzo_demo.files import original_path
        from sanzo_demo.review_state import review_facts

        actor.require_case(command.case_id)
        command.validate()
        r = self.repository
        digest = request_digest(actor, command)
        with r.transaction() as conn:
            replay = self.review._replay(conn, actor, command, digest, "CORRECT")
            if replay is not None:
                return replay
            case = r.case(conn, actor.tenant_id, command.case_id, lock=True)
            r.lock_fee_catalog(conn, actor.tenant_id)
            if case["case_revision"] != command.expected_case_revision:
                raise ConflictError("案件が更新されています。最新を読み直してください")
            ids = conn.execute("""
                SELECT item_id FROM sanzo_core.items WHERE tenant_id=%s AND case_id=%s
                ORDER BY created_at,item_id
            """, (actor.tenant_id, command.case_id)).fetchall()
            items = [self.store._item(conn, actor, row["item_id"]) for row in ids]
            fees = conn.execute("""SELECT fee_id,display_name,name_version FROM sanzo_core.fees
                WHERE tenant_id=%s AND NOT retired""", (actor.tenant_id,)).fetchall()
            documents = conn.execute("""SELECT * FROM sanzo_core.documents
                WHERE tenant_id=%s AND case_id=%s""", (actor.tenant_id, command.case_id)).fetchall()
            row = next((row for row in comparison_projection(items, fees, documents)["rows"]
                        if row["row_id"] == command.row_id), None)
            if row is None or command.item_id not in row["item_ids"]:
                raise ConflictError("比較行の構成が変わりました。最新を読み直してください")
            locked = r.item(conn, actor.tenant_id, command.item_id, lock=True)
            if locked["case_id"] != command.case_id:
                raise ScopeError("明細が案件に属していません")
            if locked["version"] != command.expected_item_version:
                raise ConflictError("明細が更新されています。最新を読み直してください")
            item = self.store._item(conn, actor, command.item_id)
            document = self.store._document(conn, actor, item["observation"]["document_id"])
            if document["case_id"] != command.case_id:
                raise ScopeError("原文が案件に属していません")
            original_path(self.storage_dir, document)
            source = item.get("source_ref") or {}
            if (source.get("document_id") not in {None, document["document_id"]}
                    or source.get("document_sha256") not in {None, document["sha256"]}):
                raise ValueError("明細の出典と保存原本が一致しないため確認できません")
            if command.kind == "CONDITION_VALUE":
                value = item["effective_context"].get(command.field)
                if not isinstance(applicable_value(value), str) or not value.strip():
                    raise ValueError("条件の値が不足しています。原文に基づく条件修正が必要です")
                if command.value != value:
                    raise ConflictError("現在の値と一致しません。変更は適用条件の修正から保存してください")
            else:
                for value in (item["observation"].get("raw_label"), item.get("current_price"), item.get("currency")):
                    if not isinstance(applicable_value(value), str) or not value.strip():
                        raise ValueError("原文費目・単価・通貨が不足しているため照合を完了できません")
            event_id = str(uuid4())
            item_version = conn.execute("""UPDATE sanzo_core.items
                SET version=version+1,updated_at=current_timestamp
                WHERE tenant_id=%s AND item_id=%s RETURNING version""",
                (actor.tenant_id, command.item_id)).fetchone()["version"]
            revision = r.advance_case(conn, actor.tenant_id, command.case_id)
            conn.execute("""
                INSERT INTO sanzo_core.quote_confirmation_events(
                    tenant_id,event_id,case_id,item_id,document_id,actor_id,
                    confirmation_kind,field_name,value_snapshot,anchor_sha256,
                    document_sha256,evidence_note,item_version,case_revision)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (actor.tenant_id, event_id, command.case_id, command.item_id,
                  document["document_id"], actor.actor_id, command.kind, command.field,
                  Jsonb(confirmation_value(item, command.kind, command.field)),
                  confirmation_anchor(item, command.kind, command.field), document["sha256"],
                  command.evidence_note.strip(), item_version, revision))
            self.review._checkpoint("after_quote_confirmation")
            updated = self.store._item(conn, actor, command.item_id)
            facts = review_facts(updated)
            response = {"kind": "ITEM_QUOTE_CONFIRMATION", "event_id": event_id,
                        "case_id": command.case_id, "row_id": command.row_id,
                        "item_id": command.item_id, "item_version": item_version,
                        "case_revision": revision, "review_facts": facts,
                        "confirmation_status": comparison_status(updated),
                        "review_required": facts["review_required"],
                        "quote_confirmations": updated["quote_confirmations"]}
            self.review._checkpoint("before_quote_confirmation_command")
            r.save_command(conn, actor, command, "CORRECT", digest, command.case_id, response)
            return response


def attach_document_review_events(conn, actor, documents):
    """Attach immutable history for the pure current-document projection."""
    ids = [document["document_id"] for document in documents]
    rows = conn.execute("""SELECT * FROM sanzo_core.document_review_events
        WHERE tenant_id=%s AND document_id=ANY(%s) ORDER BY case_revision,event_id""",
        (actor.tenant_id, ids)).fetchall() if ids else []
    for document in documents:
        document["document_review_events"] = [
            {**event, "created_at": event["created_at"].isoformat()}
            for event in rows if event["document_id"] == document["document_id"]]
    return documents


@dataclass(frozen=True, kw_only=True)
class DocumentReviewCommand:
    case_id: str
    document_id: str
    expected_case_revision: int
    expected_review_sha256: str
    confirmed_item_count: int
    evidence_note: str
    idempotency_key: str

    def validate(self):
        if any(not isinstance(value, str) or not value.strip() or len(value) > 200
               for value in (self.case_id, self.document_id, self.idempotency_key)):
            raise ValueError("案件・資料・保存キーを確認してください")
        if type(self.expected_case_revision) is not int or self.expected_case_revision < 1:
            raise ValueError("案件の版を確認してください")
        if (not isinstance(self.expected_review_sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", self.expected_review_sha256)):
            raise ValueError("資料と確認対象の版を確認してください")
        if type(self.confirmed_item_count) is not int or self.confirmed_item_count < 1:
            raise ValueError("原文で照合した明細数を入力してください")
        if (not isinstance(self.evidence_note, str) or not self.evidence_note.strip()
                or len(self.evidence_note) > 2000):
            raise ValueError("原文との件数・範囲の照合根拠を2000文字以内で入力してください")


class DocumentReviewService:
    def __init__(self, repository, storage_dir: Path, fault_hook=None):
        from .demo_store import DemoStore
        self.repository, self.store = repository, DemoStore(repository)
        self.storage_dir = Path(storage_dir)
        self.review = ReviewService(repository, fault_hook=fault_hook)

    def confirm(self, actor: Principal, command: DocumentReviewCommand):
        from sanzo_demo.current_document_review import current_document_reviews
        from sanzo_demo.files import original_path

        actor.require_case(command.case_id)
        command.validate()
        r, digest = self.repository, request_digest(actor, command)
        with r.transaction() as conn:
            r.advisory(conn, "document-review-idempotency", actor.tenant_id, command.idempotency_key)
            previous = conn.execute("""SELECT case_id,request_hash,response_json
                FROM sanzo_core.document_review_commands WHERE tenant_id=%s AND idempotency_key=%s""",
                (actor.tenant_id, command.idempotency_key)).fetchone()
            if previous:
                actor.require_case(previous["case_id"])
                if previous["request_hash"] != digest:
                    raise ConflictError("保存キーが別の要求または操作人に使用されています")
                return previous["response_json"]
            case = r.case(conn, actor.tenant_id, command.case_id, lock=True)
            r.lock_fee_catalog(conn, actor.tenant_id)
            if case["case_revision"] != command.expected_case_revision:
                raise ConflictError("案件が更新されています。最新を読み直してください")
            document = self.store._document(conn, actor, command.document_id)
            if document["case_id"] != command.case_id:
                raise ScopeError("資料が案件に属していません")
            original_path(self.storage_dir, document)
            ids = conn.execute("""SELECT item_id FROM sanzo_core.items
                WHERE tenant_id=%s AND case_id=%s ORDER BY item_id""",
                (actor.tenant_id, command.case_id)).fetchall()
            items = [self.store._item(conn, actor, row["item_id"]) for row in ids]
            tasks = conn.execute("""SELECT * FROM sanzo_core.ingestion_tasks
                WHERE tenant_id=%s AND case_id=%s ORDER BY created_at,task_id""",
                (actor.tenant_id, command.case_id)).fetchall()
            for task in tasks:
                task["issues"] = (task.get("result_json") or {}).get("issues", [])
            latest = next((task for task in reversed(tasks)
                           if task["document_id"] == command.document_id), None)
            if latest:
                document["state"], document["issues"] = latest["state"], latest["issues"]
            attach_document_review_events(conn, actor, [document])
            current = current_document_reviews(items, [document], tasks)[command.document_id]
            if current["review_sha256"] != command.expected_review_sha256:
                raise ConflictError("資料・明細・確認事項が更新されています。最新を読み直してください")
            if command.confirmed_item_count != current["item_count"]:
                raise ValueError("原文で照合した明細数と現在の明細数が一致しません。読取漏れを確認してください")
            if not current["coverage_confirmable"]:
                raise ValueError(current["coverage_unavailable_reason"])
            event_id = str(uuid4())
            revision = r.advance_case(conn, actor.tenant_id, command.case_id)
            conn.execute("""INSERT INTO sanzo_core.document_review_events(
                tenant_id,event_id,case_id,document_id,actor_id,document_sha256,
                review_sha256,value_snapshot,confirmed_item_count,evidence_note,case_revision)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (actor.tenant_id, event_id, command.case_id, command.document_id,
                 actor.actor_id, document["sha256"], current["review_sha256"],
                 Jsonb(current["review_value_snapshot"]), command.confirmed_item_count,
                 command.evidence_note.strip(), revision))
            self.review._checkpoint("after_document_review")
            attach_document_review_events(conn, actor, [document])
            review = current_document_reviews(items, [document], tasks)[command.document_id]
            response = {"kind": "DOCUMENT_COVERAGE_CONFIRMATION", "event_id": event_id,
                        "case_id": command.case_id, "document_id": command.document_id,
                        "case_revision": revision, "current_document_review": review,
                        "review_required": review["review_required"]}
            self.review._checkpoint("before_document_review_command")
            conn.execute("""INSERT INTO sanzo_core.document_review_commands(
                tenant_id,idempotency_key,actor_id,case_id,document_id,request_hash,response_json)
                VALUES(%s,%s,%s,%s,%s,%s,%s)""", (actor.tenant_id, command.idempotency_key,
                actor.actor_id, command.case_id, command.document_id, digest, Jsonb(response)))
            return response
