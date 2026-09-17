"""Bounded HUMAN canonical vocabulary. It never learns or does fuzzy matching."""
from collections import defaultdict
from .domain import Observation, canonical_json
from .fee_policy import PREFIX, key

MAX_CANDIDATES = 100
MAX_CURRENT_SLOTS = 2000


def canonical_candidates(rows, tenant_id, authorized_hashes):
    """Current REAL slots only; direct new-policy slots shadow all legacy slots.

    An oversized scan fails closed instead of hiding part of a conflict group.
    Only authorized document-derived names may leave this Demo's boundary.
    """
    if not authorized_hashes or len(rows) > MAX_CURRENT_SLOTS:
        return []
    current = {row["memory_key"]: row for row in rows
               if row.get("tenant_id") == tenant_id and row.get("origin") == "REAL_SDK"}
    groups = defaultdict(list)
    verified_projections = {}
    for row in current.values():
        try:
            observation = Observation.from_json(canonical_json(row["observation"]))
            if observation.tenant_id != tenant_id:
                continue
            projected = key(observation, row["source_ref"], row["input_kind"])
        except (ValueError, KeyError, TypeError):
            continue
        if projected is not None:
            verified_projections[row["memory_key"]] = projected
            groups[projected].append(row)
    fees = {}
    for projected, group in sorted(groups.items()):
        if projected in current:
            effective = ([current[projected]]
                if verified_projections.get(projected) == projected else [])
        else:
            effective = [row for row in group if not row["memory_key"].startswith(PREFIX)]
        if (not effective or any(row.get("status") != "ACTIVE"
                or not row.get("human_confirmed") or not row.get("fee_id")
                for row in effective)
                or len({row["fee_id"] for row in effective}) != 1):
            continue
        for row in effective:
            if row.get("source_ref", {}).get("document_sha256") not in authorized_hashes:
                continue
            name = row.get("display_name")
            if not isinstance(name, str) or not name.strip() or len(name) > 1024:
                continue
            fees[row["fee_id"]] = {"fee_id": row["fee_id"], "name": name}
    return [fees[identity] for identity in sorted(fees)[:MAX_CANDIDATES]]


def resolve_matches(suggestions, offered_by_item, current_candidates):
    """Only an ID offered to this item may resolve to a current database name."""
    current = {candidate["fee_id"]: candidate["name"] for candidate in current_candidates}
    names, matches = {}, {}
    for suggestion in suggestions:
        item_id = suggestion["item_id"]
        identity = suggestion["matched_fee_id"]
        if identity is None:
            names[item_id] = suggestion["suggested_fee_name"]
            continue
        if identity not in {candidate["fee_id"] for candidate in offered_by_item.get(item_id, [])}:
            raise ValueError("AI_CANONICAL_ID_NOT_OFFERED")
        if identity not in current:
            raise ValueError("AI_CANONICAL_CANDIDATE_NO_LONGER_ACTIVE")
        names[item_id] = current[identity]
        matches[item_id] = {"fee_id": identity, "name": current[identity]}
    return names, matches
