"""Read-only review facts shared by the Web projection and new XLSX exports.

Fee classification, reusable memory and outstanding quote review are separate
facts. In particular, missing company metadata does not undo a verified REAL
fee-name memory, and a human condition override cannot prove future reuse.
"""
from __future__ import annotations

from sanzo_core.domain import Observation, canonical_json, memory_key
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


def review_facts(item: dict) -> dict:
    observation = item.get("observation") or {}
    # The verified line projection carries fee conditions; readiness and
    # transport metadata remain on the immutable observation.
    context = {**(observation.get("context") or {}), **(item.get("effective_context") or {})}
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
    if context.get("ready") is not True or any(not _present(context.get(key)) for key in CONDITIONS):
        reasons.append("適用条件未確認")
    if not _present(observation.get("forwarder_id")):
        reasons.append("会社未確認")
    if not _present(item.get("current_price")):
        reasons.append("単価未確認")
    if not _present(item.get("currency")):
        reasons.append("通貨未確認")
    if observation.get("source_ready") is not True or not _present(observation.get("raw_label")):
        reasons.append("原文未確認")
    locator = (item.get("source_ref") or {}).get("locator_status", "UNAVAILABLE")
    if locator == "UNAVAILABLE":
        reasons.append("原文位置未取得")
    elif locator == "PAGE_ONLY":
        reasons.append("ページ内の明細範囲は未確定")

    return {
        "classification_confirmed": current_confirmation,
        "learning_eligible": eligible,
        "reusable_memory": memory_status,
        "memory_applied": initial.get("kind") == "MEMORY_APPLIED" or saved_status == "MEMORY_APPLIED",
        "review_required": bool(reasons),
        "review_reasons": reasons,
        "learning_notices": learning_notices,
    }
