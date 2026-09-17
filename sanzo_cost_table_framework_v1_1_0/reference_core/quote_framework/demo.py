"""Synthetic OFFLINE demo. No Excel/PDF parsing and no real AI call is performed."""
from __future__ import annotations
from dataclasses import asdict, replace
import json
from pathlib import Path
import tempfile

from .domain import Actor, Context, Correction, DecisionKind, Observation
from .repository import ReferenceStore


def main() -> None:
    actor = Actor("demo-tenant", "demo-editor")
    context = Context("ROAD", "N/A", "PER_DELIVERY", "DELIVERY_ONLY", "N/A")
    first = Observation("demo-tenant", "item-1", "case-1", "doc-1", "block-1",
                        "forwarder-A", "送货钱", "10", "CNY", context)
    with tempfile.TemporaryDirectory(prefix="sanzo-framework-demo-") as directory:
        path = Path(directory) / "reference.sqlite3"
        store = ReferenceStore(path)
        store.add_observation(first)
        decision1 = store.classify(actor, "item-1")
        # A test double stands in for an incorrect AI candidate. It does NOT call OpenAI.
        fake_ai_candidate = "配送料" if decision1.kind == DecisionKind.AI_REQUIRED else None
        saved = store.correct(actor, Correction("item-1", 1, 0, "demo-save-1", new_fee_name="配送费"))
        store.close()
        # Reopening a fresh connection demonstrates persistence beyond the first process object.
        store = ReferenceStore(path)
        second = replace(first, item_id="item-2", case_id="case-2", document_id="doc-2",
                         source_id="block-2", unit_price="20")
        store.add_observation(second)
        decision2 = store.classify(actor, "item-2")
        third = replace(second, item_id="item-3", case_id="case-3", raw_label="运输服务费")
        store.add_observation(third)
        decision3 = store.classify(actor, "item-3")
        report = {
            "status": "OFFLINE_REFERENCE_DEMO_ONLY",
            "fixtures": "synthetic; not client quotations",
            "real_sdk_calls": 0, "real_openai_calls": 0,
            "first": {"raw": first.raw_label, "decision": decision1.kind.value,
                      "stub_ai_candidate": fake_ai_candidate, "human_saved": "配送费"},
            "save": saved,
            "second_after_reopen": {**asdict(decision2), "classification_ai_calls": 0},
            "third_new_label": {**asdict(decision3), "classification_ai_calls": "not executed; only decision emitted"},
            "limits": "Does not measure AI accuracy or total document-stage AI usage.",
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        store.close()


if __name__ == "__main__":
    main()
