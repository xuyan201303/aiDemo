"""Read-only case-list summary over the same saved snapshot used by detail."""
from __future__ import annotations

from sanzo_core.ingestion_store import ACTIVE
from .review_state import review_facts
from .current_document_review import current_document_reviews


def summarize_case(snapshot: dict) -> dict:
    case = snapshot["case"]
    documents = snapshot.get("documents", [])
    items = snapshot.get("items", [])
    in_progress = any(document.get("state") in ACTIVE for document in documents)
    document_reviews = current_document_reviews(items, documents, snapshot.get("tasks", []))
    # Count document-only issues here; item issues are counted once below.
    pending_documents = sum(bool(review["unresolved_issues"]) for review in document_reviews.values())
    pending_items = sum(review_facts(item)["review_required"] for item in items)
    pending = pending_documents + pending_items
    if not documents:
        status = "資料なし"
    elif in_progress:
        status = "解析中"
    elif pending or not items:
        status = "確認必要"
        if not pending:
            pending = 1  # A processed-looking empty quote still requires review.
    else:
        status = "確認済み"
    return {**case, "document_count": len(documents), "review_count": pending,
            "status": status}


def case_summaries(actor, store, pipeline) -> list[dict]:
    """Keep GET /api/cases an array and preserve each existing case field."""
    result = []
    for case in store.list_cases(actor):
        summary = summarize_case(pipeline.store.case_snapshot(actor, case["case_id"]))
        # The detail snapshot deliberately has only the case revision/name;
        # retain the list query's persisted creation/update timestamps.
        result.append({**case, **summary})
    return result
