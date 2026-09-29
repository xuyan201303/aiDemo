"""Read the actual shared-fee scope before an explicit rename confirmation."""
from __future__ import annotations

from sanzo_core.demo_store import DemoStore
from sanzo_core.domain import ScopeError, canonical_json
from .comparison import _context
from .extraction import CONDITIONS, applicable_value


def rename_impact(repository, actor, case_id: str, fee_id: str) -> dict:
    actor.require_case(case_id)
    store = DemoStore(repository)
    with repository.transaction() as conn:
        repository.case(conn, actor.tenant_id, case_id)
        fee = repository.fee(conn, actor.tenant_id, fee_id)
        if fee is None:
            raise ScopeError("Fee not available in authorized scope")
        if actor.allowed_case_ids is None:
            rows = conn.execute("""SELECT item_id,case_id FROM sanzo_core.items
                WHERE tenant_id=%s AND current_fee_id=%s ORDER BY case_id,item_id""",
                (actor.tenant_id, fee_id)).fetchall()
        else:
            rows = conn.execute("""SELECT item_id,case_id FROM sanzo_core.items
                WHERE tenant_id=%s AND current_fee_id=%s AND case_id=ANY(%s)
                ORDER BY case_id,item_id""",
                (actor.tenant_id, fee_id, sorted(actor.allowed_case_ids))).fetchall()
        if not any(row["case_id"] == case_id for row in rows):
            raise ScopeError("Fee not used in the selected case")
        conditions = {}
        for row in rows:
            item = store._item(conn, actor, row["item_id"])
            context = _context(item)
            value = {}
            for field in CONDITIONS:
                current = applicable_value(context.get(field))
                value[field] = current.strip() if isinstance(current, str) and current.strip() else None
            conditions[canonical_json(value)] = value
        return {
            "fee_id": fee_id,
            "display_name": fee["display_name"],
            "name_version": fee["name_version"],
            "conditions": [conditions[key] for key in sorted(conditions)],
            "current_case_item_count": sum(row["case_id"] == case_id for row in rows),
            "other_case_count": len({row["case_id"] for row in rows if row["case_id"] != case_id}),
        }
