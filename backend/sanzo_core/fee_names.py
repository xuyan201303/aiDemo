"""Explicit stable-identity rename. Existing alias mappings are not reclassified."""
from uuid import uuid4
from .domain import ConflictError, ScopeError, Observation, memory_origin, normalize_label


def append_name(repository, conn, actor, fee_id, expected_version, name, case_id, *, reason="RENAME_FEE"):
    """Caller owns case/catalog locks. A single logical operation appends one name."""
    fee = repository.fee(conn, actor.tenant_id, fee_id)
    if fee is None:
        raise ScopeError("Fee is unavailable")
    if fee["name_version"] != expected_version:
        raise ConflictError("統一名称が更新されています。最新を確認してください")
    normalized = normalize_label(name)
    if not normalized or len(name) > 1024:
        raise ValueError("統一費目名を入力してください")
    duplicate = conn.execute("""SELECT fee_id FROM sanzo_core.fees
        WHERE tenant_id=%s AND normalized_name=%s AND fee_id<>%s AND NOT retired""",
        (actor.tenant_id, normalized, fee_id)).fetchone()
    if duplicate:
        raise ConflictError("同じ名称の別費目があります。自動的には統合できません")
    if fee["display_name"] == name.strip():
        raise ConflictError("名称は変更されていません")
    version = expected_version + 1
    event = str(uuid4())
    conn.execute("""INSERT INTO sanzo_core.fee_name_versions
        (tenant_id,fee_id,name_version,display_name,normalized_name,event_id,actor_id,case_id,reason)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (actor.tenant_id, fee_id, version, name.strip(), normalized, event, actor.actor_id, case_id, reason))
    conn.execute("""UPDATE sanzo_core.fees SET display_name=%s,normalized_name=%s,name_version=%s
        WHERE tenant_id=%s AND fee_id=%s""", (name.strip(), normalized, version, actor.tenant_id, fee_id))
    return {"fee_id": fee_id, "fee_name": name.strip(), "fee_name_version": version, "name_event_id": event}


def rename_row(owner, conn, actor, command, items, digest):
    r = owner.repository
    target = command.expected_fee_id
    if any(i["current_fee_id"] != target for i in items.values()):
        raise ConflictError("同じ確認済み費目の名称だけを変更できます")
    origins = {memory_origin(i["source_ref"], i["input_kind"]) for i in items.values()}
    if len(origins) != 1:
        raise ScopeError("Different extraction scopes cannot share a rename")
    linked_origins = {v['origin'] for v in conn.execute("""SELECT DISTINCT origin FROM sanzo_core.memory_slots
        WHERE tenant_id=%s AND fee_id=%s AND status='ACTIVE'""", (actor.tenant_id, target)).fetchall()}
    if linked_origins - origins:
        raise ScopeError("Rename would cross REAL/Mock learning scope")
    slots = {}
    for iid, item in items.items():
        slot = r.lookup_memory(conn, Observation.from_json(item["observation"]), item["source_ref"], item["input_kind"])
        # A memory conflict limits future automatic classification, not the
        # explicitly selected, already confirmed fee identity. Renaming the
        # catalog must neither repair nor reactivate that memory.
        slots[iid] = slot
    named = append_name(r, conn, actor, target, command.expected_name_version, command.new_fee_name, command.case_id)
    owner.service._checkpoint("after_name")
    owner.service._checkpoint("after_fee_rename")
    # A rename changes the current catalog label for this stable fee identity.
    # Previously adopted item names and versions are historical facts, including
    # the members selected to open this action. The name-version event above is
    # the audit record; do not manufacture per-item correction events here.
    revision = r.advance_case(conn, actor.tenant_id, command.case_id)
    results = []
    for iid in sorted(items):
        item, slot = items[iid], slots[iid]
        reusable = bool(slot and slot['status'] == 'ACTIVE' and slot['fee_id'] == target)
        results.append({
            'item_id': iid, 'item_version': item['version'], 'case_revision': revision,
            'memory_effect': ('ACTIVE' if reusable else
                              'NOT_REUSABLE_MAPPING' if slot and slot['status'] == 'ACTIVE' else
                              slot['status'] if slot else 'NOT_ELIGIBLE_CONTEXT_OR_SOURCE'),
            'memory_action': 'UNCHANGED_FEE_RENAMED',
            'memory_version': slot['version'] if slot else 0,
        })
    owner.service._checkpoint("before_command")
    response = {**named, 'kind': 'ROW_FEE_RENAME', 'intent': 'RENAME_FEE', 'row_id': command.row_id,
                'case_id': command.case_id, 'case_revision': revision, 'confirmation_status': '✓ 確認済み',
                'members': results, 'memory_reusable': all(i['memory_effect'] == 'ACTIVE' for i in results)}
    r.save_command(conn, actor, command, 'CORRECT', digest, command.case_id, response)
    return response
