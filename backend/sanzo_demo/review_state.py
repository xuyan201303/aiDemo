"""Read-only review facts shared by the Web projection and new XLSX exports.

Fee classification, reusable memory and outstanding quote review are separate
facts. In particular, missing company metadata does not undo a verified REAL
fee-name memory, and a human condition override cannot prove future reuse.
"""
from __future__ import annotations

from sanzo_core.domain import Observation, canonical_json, memory_key
from sanzo_core.fee_policy import LINE_CONDITIONS, uses_fee_policy
from .extraction import CONDITIONS, applicable_value


def _present(value):
    value = applicable_value(value)
    return isinstance(value, str) and bool(value.strip())


def learning_eligible(item: dict) -> bool:
    if item.get("future_reuse_eligible") is False or item.get("condition_overrides"):
        return False
    try:
        observation = Observation.from_json(canonical_json(item["observation"]))
        return memory_key(observation, item.get("source_ref"), item.get("input_kind")) is not None
    except (KeyError, TypeError, ValueError):
        return False


def _grounded_quote_source(item: dict) -> bool:
    """Current factual values, independent of optional document role bindings.

    This reads existing verified field evidence; it never upgrades source_ready
    or qualifies any additional learning record.
    """
    source, observation = item.get("source_ref") or {}, item.get("observation") or {}
    if source.get("text_verification") != "VERIFIED":
        return False
    fields = source.get("field_sources") or {}
    label = fields.get("raw_label") or {}
    if not label.get("record_key"):
        return False
    for name in ("raw_label", "raw_value", "currency"):
        field = fields.get(name) or {}
        unit = field.get("evidence_unit") or {}
        if (not _present(field.get("original_value"))
                or field.get("value") != field.get("original_value")
                or field.get("record_key") != label["record_key"]
                or unit.get("scope") != "LINE" or unit.get("field_name") != name):
            return False
    try:
        from .extraction import known_amount, known_currency
        return (label["original_value"] == observation.get("raw_label")
                and known_currency(fields["currency"]["original_value"]) == item.get("currency")
                and known_amount(fields["raw_value"]["original_value"], item.get("currency"))
                == known_amount(item.get("current_price"), item.get("currency")))
    except (ValueError, TypeError, KeyError):
        return False
def review_facts(item: dict) -> dict:
    observation = item.get("observation") or {}
    # The verified line projection carries fee conditions; readiness and
    # transport metadata remain on the immutable observation.
    context = {**(observation.get("context") or {}), **(item.get("effective_context") or {})}
    real_policy = uses_fee_policy(item.get("source_ref"), item.get("input_kind"))
    confirmations = item.get("quote_confirmations") or {}
    conditions_confirmed = {
        field: (confirmations.get("conditions", {}).get(field) or {}).get("valid") is True
        for field in LINE_CONDITIONS
    }
    source_confirmed = (confirmations.get("source") or {}).get("valid") is True
    stale_source_confirmation = bool(confirmations.get("source")) and not source_confirmed
    current_fee_id = item.get("current_fee_id")
    saved_status = item.get("saved_status")
    initial = item.get("initial_classification") or {}
    memory_status = item.get("current_memory_status")
    if memory_status not in {"ACTIVE", "CONFLICTED", "REVOKED"}:
        memory_status = "NONE"
    eligible = learning_eligible(item)
    overrides = bool(item.get("condition_overrides"))
    current_confirmation = bool(
        current_fee_id and saved_status in {"HUMAN_CONFIRMED", "MEMORY_APPLIED"}
        and (not overrides or item.get("classification_confirmed_after_condition") is True)
    )
    resolution = item.get("memory_resolution") or {}
    # A guarded original reading cannot be learned for the next quotation.
    # An explicit human classification of this quotation is a separate fact.
    future_guard = bool(
        memory_status == "CONFLICTED"
        and resolution.get("kind") == "CONDITION_CORRECTION_GUARD"
        and resolution.get("guarded_direct_status") in {"ACTIVE", "CONFLICTED"}
        and resolution.get("guarded_independent_conflict") is False
        and resolution.get("guarded_independent_revocation") is False
        and current_confirmation and saved_status == "HUMAN_CONFIRMED"
    )

    reasons = []
    learning_notices = []
    metadata_notices = []
    if not current_confirmation:
        if current_fee_id and overrides and saved_status == "HUMAN_CONFIRMED":
            reasons.append("適用条件変更後の費目未確認")
        else:
            reasons.append("費目未確認")
    if resolution.get("kind") == "CONDITION_CORRECTION_GUARD" and (
        resolution.get("guarded_direct_status") == "REVOKED" or
        resolution.get("guarded_independent_revocation") is True
    ):
        reasons.append("確認記録が撤回されています")
    elif memory_status == "CONFLICTED" and not future_guard:
        reasons.append("確認記録が競合しています")
    elif memory_status == "CONFLICTED":
        learning_notices.append("元の適用条件の学習対応が競合：次回自動適用は未確認")
    elif memory_status == "REVOKED":
        reasons.append("確認記録が撤回されています")
    if item.get("future_reuse_eligible") is False or overrides:
        learning_notices.append("適用条件を修正：次回自動適用は未確認")
    required_conditions = LINE_CONDITIONS if real_policy else CONDITIONS
    conditions_ready = real_policy or context.get("ready") is True or all(conditions_confirmed.values())
    if not conditions_ready or any(not _present(context.get(key)) for key in required_conditions):
        reasons.append("適用条件未確認")
    if not _present(observation.get("forwarder_id")):
        (metadata_notices if real_policy else reasons).append(
            "会社未取得：資料名で比較しています" if real_policy else "会社未確認")
    if real_policy and not _present(context.get("transport_mode")):
        metadata_notices.append("資料全体の輸送範囲は未取得：費目の適用条件とは別です")
    if not _present(item.get("current_price")):
        reasons.append("単価未確認")
    if not _present(item.get("currency")):
        reasons.append("通貨未確認")
    source_ready = _grounded_quote_source(item) if real_policy else observation.get("source_ready") is True
    if ((not source_ready and not source_confirmed) or stale_source_confirmation
            or not _present(observation.get("raw_label"))):
        reasons.append("原文未確認")
    locator = (item.get("source_ref") or {}).get("locator_status", "UNAVAILABLE")
    if locator == "UNAVAILABLE" and not source_confirmed:
        reasons.append("原文位置未取得")
    elif locator == "PAGE_ONLY" and not source_confirmed:
        reasons.append("ページ内の明細範囲は未確定")

    return {
        "classification_confirmed": current_confirmation,
        "learning_eligible": eligible,
        "reusable_memory": memory_status,
        "memory_applied": initial.get("kind") == "MEMORY_APPLIED" or saved_status == "MEMORY_APPLIED",
        "review_required": bool(reasons),
        "review_reasons": reasons,
        "learning_notices": learning_notices,
        "metadata_notices": metadata_notices,
        "condition_confirmations": conditions_confirmed,
        "source_confirmed": source_confirmed,
    }
