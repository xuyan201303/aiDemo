"""业务核心验证，不是真实文件／AI／Web Demo。"""
import argparse
from dataclasses import asdict
import json
from uuid import uuid4
from .domain import Principal, Context, Observation, SyntheticLine, CorrectionCommand, DecisionKind
from .postgres import PostgresRepository
from .service import ReviewService

def demonstrate(dsn):
    from psycopg.conninfo import conninfo_to_dict
    if conninfo_to_dict(dsn).get("dbname") != "sanzo_w1_test":
        raise ValueError("CLI requires the dedicated sanzo_w1_test database")
    repository = PostgresRepository(dsn)
    repository.migrate()
    service = ReviewService(repository)
    tenant = "synthetic-cli-" + uuid4().hex
    actor = Principal(tenant, "synthetic-reviewer")
    context = Context("ROAD", "N/A", "PER_DELIVERY", "DELIVERY_ONLY", "N/A")
    def add(case, item, price, day, suggestion=None):
        service.create_case(actor, case, "SYNTHETIC " + case)
        obs = Observation(tenant, item, case, "synthetic-" + case, "block-" + item,
                          "synthetic-company-A", "送货钱", price, "CNY", context)
        service.add_observation(actor, SyntheticLine(
            obs, {"synthetic": True, "document_id": obs.document_id, "source_block_ids": [obs.source_id]},
            ai_suggested_name=suggestion, quotation_date=day))
    add("first-case", "first-line", "10", "2026-09-01", "配送料")
    assert service.classify(actor, "first-line").kind == DecisionKind.AI_REQUIRED
    before = service.get_item(actor, "first-line")
    saved = service.correct(actor, CorrectionCommand(
        item_id="first-line", expected_item_version=before["version"],
        expected_memory_version=0, idempotency_key="first-confirmation",
        new_fee_name="配送费", expected_case_revision=before["case_revision"]))
    service = ReviewService(PostgresRepository(dsn))
    add("next-case", "next-line", "20", "2026-09-11")
    result = service.classify(actor, "next-line")
    new = service.get_item(actor, "next-line")
    assert result.kind == DecisionKind.MEMORY_APPLIED
    assert result.fee_id == saved["fee_id"] and result.fee_name == "配送费"
    assert result.unit_price == "20" and new["current_price"] == "20"
    assert service.get_item(actor, "first-line")["current_price"] == "10"
    return {"label": __doc__, "input_kind": "SYNTHETIC_STRUCTURED",
            "original_label": "送货钱", "unconfirmed_synthetic_suggestion": "配送料",
            "human_confirmed_name": "配送费", "first_saved": saved,
            "next_case_decision": asdict(result),
            "next_quotation_date": new["quotation_date"], "next_source_ref": new["source_ref"],
            "real_sdk_calls": 0, "real_ai_calls": 0}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", required=True, help="Explicit dedicated sanzo_w1_test connection")
    args = parser.parse_args()
    print(json.dumps(demonstrate(args.dsn), ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
