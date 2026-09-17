"""Read-only comparison projection, shared by HTTP and XLSX. Never authorizes learning."""
from collections import defaultdict
import re
from hashlib import sha256
from sanzo_core.domain import Observation, canonical_json, memory_key
from .extraction import CONDITIONS, applicable_value

def _known(value):
    value = applicable_value(value)
    return value.strip() if isinstance(value, str) and value.strip() else None

def _context(item):
    context = item.get("observation", {}).get("context", {})
    from sanzo_core.fee_policy import uses_fee_policy, line_conditions
    if uses_fee_policy(item.get("source_ref"), item.get("input_kind")):
        return line_conditions(context, item["source_ref"])
    return context

def _identity(item):
    if item.get("current_fee_id"):
        return ("fee", item["current_fee_id"])
    if item.get("ai_matched_fee_id"):
        return ("candidate", item["ai_matched_fee_id"])
    name = _known(item.get("ai_suggested_name"))
    return ("suggestion", name) if name else ("unclassified", item["item_id"])

def _company(item):
    from sanzo_core.fee_policy import uses_fee_policy
    obs = item.get("observation", {})
    if uses_fee_policy(item.get("source_ref"), item.get("input_kind")):
        return obs.get("forwarder_id") or item.get("document_display_label") or "資料 " + obs.get("document_id", item["item_id"])
    return obs.get("forwarder_id") or "会社未確認"

def comparison_groups(items, fees=()):
    names = defaultdict(set)
    saved_ids = {i.get("current_fee_id") for i in items if i.get("current_fee_name") is not None}
    for fee in fees:
        if fee.get("fee_id") and fee["fee_id"] not in saved_ids:
            name = _known(fee.get("display_name") or fee.get("name"))
            if name: names[fee["fee_id"]].add(name)
    for item in items:
        if item.get("current_fee_id") and _known(item.get("current_fee_name")):
            names[item["current_fee_id"]].add(_known(item["current_fee_name"]))
    # First preserve exact, proven fee identity (and duplicate quotes); neither name nor
    # incomplete-context grouping modifies the stored item.
    seeds = {}
    for item in items:
        key = (_identity(item), item.get("current_fee_name_version") if item.get("current_fee_id") else None,
               tuple(_known(_context(item).get(k)) for k in CONDITIONS))
        if _company(item) == "会社未確認": key = ("isolated", item["item_id"])
        seeds.setdefault(key, []).append(item)
    groups = list(seeds.values())
    def compatible(a, b):
        if any(_company(i) == "会社未確認" for i in a+b): return False
        # Partial candidates from the same forwarder do not establish a cross-company row.
        identities = {_identity(i) for i in a+b}
        same_fee = len(identities) == 1 and next(iter(identities))[0] == "fee"
        if not same_fee and {_company(i) for i in a} & {_company(i) for i in b}: return False
        for left in a:
            for right in b:
                for key in CONDITIONS:
                    x, y = _known(_context(left).get(key)), _known(_context(right).get(key))
                    if x is not None and y is not None and x != y: return False
                x, y = _identity(left), _identity(right)
                if (x == y and x[0] == "fee" and left.get("current_fee_name_version") is not None
                        and right.get("current_fee_name_version") is not None
                        and left["current_fee_name_version"] != right["current_fee_name_version"]):
                    return False  # Preserve explicitly adopted historical names.
                if "unclassified" in (x[0], y[0]): return False
                identified = {"fee", "candidate"}
                if x[0] in identified and y[0] in identified:
                    # AI-selected identity groups provisionally; it does not
                    # promote current_fee_id or create any learning record.
                    if x[1] != y[1]: return False
                elif x[0] == y[0]:
                    if x != y: return False
                else:
                    fee, suggestion = (x, y) if x[0] in identified else (y, x)
                    if suggestion[1] not in names[fee[1]]: return False
        return True
    def neighborhoods(current, confirmed_only=False):
        neighbors = [{i} for i in range(len(current))]
        for i in range(len(current)):
            for j in range(i+1, len(current)):
                if confirmed_only:
                    ids = {_identity(item) for item in current[i]+current[j]}
                    if len(ids) != 1 or next(iter(ids))[0] != "fee": continue
                if compatible(current[i], current[j]):
                    neighbors[i].add(j); neighbors[j].add(i)
        partitions = {}
        for i, adjacent in enumerate(neighbors):
            partitions.setdefault(frozenset(adjacent), []).append(i)
        return neighbors, partitions
    # Resolve confirmed identity first, so an unconfirmed suggestion cannot split
    # a compatible confirmed row. Known conflicts still prevent a merge.
    _, confirmed = neighborhoods(groups, confirmed_only=True)
    groups = [[item for n in members for item in groups[n]] for members in confirmed.values()]
    neighbors, partitions = neighborhoods(groups)
    # Equal closed neighborhoods are unambiguous cliques; incompatible candidate
    # bridges remain separate, regardless of input order.
    result = []
    for members in partitions.values():
        rows = [item for n in members for item in groups[n]]
        representative = next((i for i in rows if i.get("current_fee_id")), rows[0])
        ambiguous = not any(i.get("current_fee_id") for i in rows) and any(neighbors[n] - set(members) for n in members)
        result.append({"item": representative, "items": rows,
            "conditions": tuple(next((_known(_context(i).get(k)) for i in rows if _known(_context(i).get(k)) is not None), None) for k in CONDITIONS),
            "has_suggestions": any(not i.get("current_fee_id") and _known(i.get("ai_suggested_name")) for i in rows),
            "has_confirmed": any(i.get("current_fee_id") for i in rows), "ambiguous": bool(ambiguous)})
    return result

def comparison_status(item, ambiguous=False):
    status = item.get("saved_status") or (item.get("initial_classification") or {}).get("kind")
    if ambiguous or status in {"CONFLICTED", "REVOKED"} or item.get("current_memory_status") in {"CONFLICTED", "REVOKED"}:
        return "⚠ 確認必要"
    if item.get("current_fee_id"):
        return "✓ 確認済み"
    if item.get("ai_suggested_name") and (item.get("observation", {}).get("source_ready") is True or learning_eligible(item)):
        return "● AI仮判定"
    return "⚠ 確認必要"

def row_identity(item_ids):
    return sha256(canonical_json(sorted(item_ids)).encode()).hexdigest()

def learning_eligible(item):
    try:
        obs = Observation.from_json(canonical_json(item["observation"]))
        return memory_key(obs, item.get("source_ref"), item.get("input_kind")) is not None
    except (KeyError, TypeError, ValueError):
        return False

def row_confirmation_status(group):
    # A confirmed row name is distinct from each member's reusable memory.
    if group["ambiguous"]:
        return "⚠ 確認必要"
    if all(i.get("current_fee_id") for i in group["items"]):
        return "✓ 確認済み"
    if all(comparison_status(i) in {"✓ 確認済み", "● AI仮判定"} for i in group["items"]):
        return "● AI仮判定"
    return "⚠ 確認必要"

def comparison_projection(items, fees=(), documents=()):
    # Filename is display metadata only; duplicate filenames keep distinct document columns.
    labels = {d["document_id"]: d["original_filename"] for d in documents}
    counts = {name: list(labels.values()).count(name) for name in labels.values()}
    from sanzo_core.fee_policy import uses_fee_policy
    items = [{**i, "document_display_label": (labels.get(i.get("observation", {}).get("document_id")) +
        (" [" + i["observation"]["document_id"] + "]" if counts.get(labels.get(i.get("observation", {}).get("document_id")), 0) > 1 else ""))
        if i.get("observation", {}).get("document_id") in labels else i.get("document_display_label")} if uses_fee_policy(i.get("source_ref"), i.get("input_kind")) and not i.get("observation", {}).get("forwarder_id") else i for i in items]
    companies = sorted({_company(i) for i in items})
    fee_names = {f["fee_id"]: f.get("display_name") or f.get("name") for f in fees if f.get("fee_id")}
    rows = []
    for group in comparison_groups(items, fees):
        representative = group["item"]
        rename_allowed = (bool(representative.get("current_fee_id")) and not group["ambiguous"]
            and all(i.get("current_fee_id") == representative["current_fee_id"]
                    and i.get("current_memory_status") not in {"CONFLICTED", "REVOKED"}
                    for i in group["items"]))
        label = "費目未確認" if group["ambiguous"] else representative.get("current_fee_name") or fee_names.get(representative.get("current_fee_id")) or representative.get("ai_suggested_name") or "費目未確認"
        rows.append({"row_id": row_identity([i["item_id"] for i in group["items"]]),
            "confirmation_status": row_confirmation_status(group),
            "rename_allowed": rename_allowed,
            "rename_fee_id": representative.get("current_fee_id") if rename_allowed else None,
            "rename_name_version": representative.get("effective_fee_name_version") if rename_allowed else None,
            "member_items": [{**i, "forwarder": _company(i), "learning_eligible": learning_eligible(i)} for i in group["items"]],
            "label": label, "equipment": group["conditions"][1],
            "ambiguous": group["ambiguous"], "item_ids": [i["item_id"] for i in group["items"]],
            "fee_identity": representative.get("current_fee_id"),
            "cells": {company: [{"item_id": i["item_id"], "price": i["current_price"], "currency": i["currency"],
                "status": comparison_status(i, group["ambiguous"]),
                "confirmation_origin": i.get("saved_status"),
                "needs_review": _context(i).get("ready") is not True or any(_known(_context(i).get(k)) is None for k in CONDITIONS) or i.get("observation", {}).get("source_ready") is not True or group["ambiguous"],
                "raw_label": i.get("observation", {}).get("raw_label"),
                "ai_suggested_name": i.get("ai_suggested_name"), "source_ref": i.get("source_ref")}
                for i in group["items"] if _company(i) == company] for company in companies}})
    for row in rows:
        dims = {}
        equipment = _known(row["equipment"])
        if equipment and equipment not in {"共通", "N/A", "なし"}:
            dims["equipment"] = equipment
        siblings = [r for r in rows if r["label"] == row["label"]]
        own = next(i for i in items if i["item_id"] == row["item_ids"][0])
        unit = _known(_context(own).get("billing_unit"))
        sibling_units = {_known(_context(i).get("billing_unit")) for i in items
                         if any(i["item_id"] in r["item_ids"] for r in siblings)}
        if unit and len(sibling_units - {None}) > 1:
            dims["billing_unit"] = unit
        row["distinguishing_conditions"] = dims
        row["differentiating_conditions"] = dims
        # Compatibility fields remain available to old audit consumers; ordinary
        # Web/Excel price cells use the price-only canonical projection.
        row["cells_by_forwarder"] = {company: [{k: cell[k] for k in ("price", "currency", "item_id", "source_ref")} for cell in cells] for company, cells in row["cells"].items()}
        visible = [v for v in dims.values() if not re.search(r"(?<![A-Za-z0-9])" + re.escape(v) + r"(?![A-Za-z0-9])", row["label"], re.IGNORECASE)]
        row["display_name"] = row["label"] + ("（" + "・".join(visible) + "）" if visible else "")
    by_id = {i["item_id"]: i for i in items}
    def position(row):
        def item_position(item_id):
            item = by_id[item_id]
            source = item.get("source_ref") or {}
            locator = source.get("locator") or {}
            match = re.match(r"\$?[A-Za-z]+\$?([1-9][0-9]*)", locator.get("A1_range", ""))
            return (_company(item), item.get("observation", {}).get("document_id") or "",
                    locator.get("sheet_name") or "", int(match[1]) if match else locator.get("page_number") or 0, item_id)
        return min(item_position(i) for i in row["item_ids"])
    rows.sort(key=position)
    return {"version": 1, "companies": companies, "rows": rows}
