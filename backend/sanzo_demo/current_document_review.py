"""Current quotation review over immutable processing history.

Only explicitly understood reader issues can be resolved by current item
facts. Unread rows, unknown warnings and failed processing remain blockers.
"""
from hashlib import sha256
from sanzo_core.domain import canonical_json
from sanzo_core.fee_policy import uses_fee_policy
from sanzo_core.ingestion_store import ACTIVE
from sanzo_core.quote_confirmations import CONDITION_FIELDS, confirmation_value
from .review_state import review_facts


def issue_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "：".join(str(value[key]) for key in
                        ("code", "reason", "limitation", "field", "resolution", "message")
                        if value.get(key)) or str(value)
    return str(value)


def _history(document, tasks):
    matching = [task for task in tasks if task.get("document_id") == document["document_id"]]
    task = max(matching, key=lambda task: (str(task.get("created_at") or task.get("updated_at") or ""),
                                         task.get("task_id") or ""), default={})
    state = (task.get("status") or task.get("state") or document.get("latest_job_state")
             or document.get("state") or "UNKNOWN")
    issues = list(task.get("issues") or document.get("issues") or [])
    code = task.get("error_code") or document.get("error_code")
    if not code and state in {"BLOCKED", "FAILED", "INTERRUPTED"}:
        code = document.get("blocked_reason")
    return state, issues, code, task.get("task_id") or document.get("task_id")


def coverage_issue_supported(issue):
    """Finite known visual-review limitations; never unread/missing row failures."""
    if not isinstance(issue, dict):
        return False
    if issue.get("code") == "SDK_LIMITATION":
        return issue.get("limitation") in {
            "FAMILY_DEFERRED", "OCR_NOT_EXECUTED", "READING_ORDER_UNRESOLVED",
            "ambiguous_hierarchy_parent", "role_requires_review",
        }
    return (issue.get("code") == "EXTRACTION_UNRESOLVED" and (
        (issue.get("reason") == "PDF_COLUMN_NOT_BOUND"
         and issue.get("header_text") == "サービス範囲・付帯条件")
        or (issue.get("reason") == "FIELD_HEADER_NOT_BOUND"
            and issue.get("field") in CONDITION_FIELDS)))


def current_document_reviews(items, documents, tasks=()):
    result = {}
    for document in documents:
        document_id = document["document_id"]
        members = [item for item in items
                   if (item.get("observation") or {}).get("document_id") == document_id]
        facts = {item["item_id"]: review_facts(item) for item in members}
        historical_state, historical_issues, error_code, task_id = _history(document, tasks)
        review_snapshot = {"document_id": document_id, "document_sha256": document.get("sha256"),
            "task_id": task_id, "historical_state": historical_state,
            "historical_issues": historical_issues, "error_code": error_code,
            "members": [{"value": confirmation_value(item, "SOURCE_REVIEW"),
                         "source_confirmation": (item.get("quote_confirmations", {}).get("source") or {}).get("event_id"),
                         "condition_confirmations": {field: event.get("event_id") for field, event
                             in (item.get("quote_confirmations", {}).get("conditions") or {}).items()}}
                        for item in sorted(members, key=lambda item: item["item_id"])]}
        review_sha256 = sha256(canonical_json(review_snapshot).encode()).hexdigest()
        coverage_event = next(iter(reversed(document.get("document_review_events") or [])), None)
        coverage_valid = bool(coverage_event and coverage_event.get("review_sha256") == review_sha256
                              and coverage_event.get("document_sha256") == document.get("sha256"))
        unresolved, resolved, metadata = [], [], []
        for issue in historical_issues:
            resolution = None
            if isinstance(issue, dict):
                if issue.get("code") == "APPLICABILITY_INCOMPLETE":
                    target = facts.get(issue.get("item_id"))
                    # Never use a neighboring item to resolve the original issue.
                    if target is not None and not target["review_required"]:
                        resolution = "当該明細の今回の確認で解決"
                elif (issue.get("code") == "EXTRACTION_UNRESOLVED"
                      and issue.get("reason") == "FIELD_HEADER_NOT_BOUND"
                      and issue.get("field") in {"company", "transport_mode"}
                      and members and all(uses_fee_policy(item.get("source_ref"), item.get("input_kind"))
                                          for item in members)):
                    resolution = "資料単位の参考情報：資料名で比較し、費目条件と分けて表示"
                    metadata.append(issue_text(issue))
            if resolution:
                resolved.append({"issue": issue, "current_resolution": resolution})
            else:
                unresolved.append(issue)
        if error_code:
            unresolved.append({"code": error_code})
        running = historical_state in ACTIVE
        if not running and historical_state not in {"AVAILABLE", "REVIEW_REQUIRED"}:
            unresolved.append({"code": "PROCESSING_NOT_COMPLETE", "message": historical_state})
        if not members and not running:
            unresolved.append({"code": "NO_QUOTATION_LINES", "message": "照合できる明細がありません。原文の情報不足または読取未完了を確認してください。"})
        all_items_complete = bool(members) and all(not fact["review_required"] for fact in facts.values())
        explicit_sources = bool(members) and all(fact["source_confirmed"] for fact in facts.values())
        explicit_conditions = bool(members) and all(
            all(facts[item["item_id"]]["condition_confirmations"][field]
                or bool((item.get("condition_overrides") or {}).get(field))
                for field in CONDITION_FIELDS) for item in members)
        supported = all(coverage_issue_supported(issue) for issue in unresolved)
        coverage_confirmable = bool(not running and all_items_complete and explicit_sources
                                    and explicit_conditions and supported)
        if not supported:
            unavailable = "この資料には未読・欠落・対応範囲外の確認事項があります：" + " / ".join(
                issue_text(issue) for issue in unresolved if not coverage_issue_supported(issue))
        elif not all_items_complete:
            unavailable = "各明細の費目・必要な適用条件・原文の未確認事項を先に処理してください。"
        elif not explicit_sources or not explicit_conditions:
            unavailable = "資料全体の照合には、各明細の原文照合と4つの条件の人工確認が必要です。"
        elif running:
            unavailable = "資料の処理中は照合を保存できません。"
        else:
            unavailable = ""
        if coverage_valid and coverage_confirmable:
            resolved.extend({"issue": issue, "current_resolution": "原文の明細数・範囲と各明細を人工照合",
                             "event_id": coverage_event["event_id"]} for issue in unresolved)
            unresolved = []
        required = bool(unresolved) or any(fact["review_required"] for fact in facts.values())
        current_state = (historical_state if running or historical_state not in {"AVAILABLE", "REVIEW_REQUIRED"}
                         else "REVIEW_REQUIRED" if required else "AVAILABLE")
        result[document_id] = {
            "historical_state": historical_state, "historical_issues": historical_issues,
            "current_state": current_state, "review_required": required,
            "unresolved_issues": unresolved, "resolved_issues": resolved,
            "metadata_notices": metadata,
            "item_count": len(members), "review_sha256": review_sha256,
            "review_value_snapshot": review_snapshot,
            "coverage_confirmable": coverage_confirmable,
            "coverage_unavailable_reason": unavailable,
            "coverage_confirmation": {**coverage_event, "valid": coverage_valid and coverage_confirmable}
                if coverage_event else None,
        }
    return result
