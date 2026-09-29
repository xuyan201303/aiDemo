"""Selected-member correction with synthetic evidence and isolated PostgreSQL.

No SDK, AI, uploaded customer files, or live Demo database are used here.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from threading import Barrier
from uuid import uuid4
import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from sanzo_core.condition_corrections import ConditionCorrectionCommand, ConditionCorrectionService
from sanzo_core.demo_store import DemoStore
from sanzo_core.domain import ConflictError, Context, DecisionKind, Principal, ScopeError
from sanzo_core.postgres import PostgresRepository
from sanzo_core.service import ReviewService
from sanzo_demo.app import create_app
from sanzo_demo.comparison import comparison_projection
from sanzo_demo.row_confirmation import (
    RowConfirmationCommand, RowConfirmationService, RowMember,
    SingleMemberCorrectionCommand, SingleMemberCorrectionService,
)
import test_postgres as fixture


DSN = os.environ.get("SANZO_TEST_DSN")


class SingleMemberCommandTests(unittest.TestCase):
    def test_requires_single_explicit_target_and_name_version(self):
        base = SingleMemberCorrectionCommand("case", "a" * 64, "item", 1, 1, 0, "key",
                                             target_fee_id="fee")
        base.validate()  # A stale replay is allowed through structural validation.
        with self.assertRaises(ValueError):
            replace(base, target_fee_id=None).validate()
        with self.assertRaises(ValueError):
            replace(base, new_fee_name="other").validate()
        with self.assertRaises(ValueError):
            replace(base, expected_fee_id="different", expected_name_version=1).validate()


@unittest.skipUnless(DSN, "NOT_RUN: independent SANZO_TEST_DSN required")
class SingleMemberPostgresTests(unittest.TestCase):
    add_line = fixture.PostgresIntegrationTests.add_line
    database_snapshot = fixture.PostgresIntegrationTests.snapshot

    def setUp(self):
        self.dsn = DSN
        self.tenant = "single-member-test-" + uuid4().hex
        self.actor = Principal(self.tenant, "test-operator")
        self.repo = PostgresRepository(DSN)
        self.repo.migrate()
        self.service = ReviewService(self.repo)
        self.store = DemoStore(self.repo)
        self.rows = RowConfirmationService(self.repo)
        self.singles = SingleMemberCorrectionService(self.repo)
        self.context = Context("SEA", "20FT", "CONTAINER", "ORIGIN", "STANDARD")
        self.add_line("a", raw_label="O/F 20'", ai_suggested_name="海上運賃",
                      unit_price="1200", currency="USD")
        self.add_line("b", existing_case="case-a", forwarder_id="company-B",
                      raw_label="OCEAN FREIGHT 20'", ai_suggested_name="海上運賃",
                      unit_price="1150", currency="USD")
        self.add_line("c", existing_case="case-a", forwarder_id="company-C",
                      raw_label="LOCAL DELIVERY", ai_suggested_name="海上運賃",
                      unit_price="1180", currency="USD")
        self.add_line("delivery", raw_label="DELIVERY FEE", ai_suggested_name="配送費",
                      unit_price="10", currency="USD")
        self.sea_fee = self.confirm_row("case-a", "海上運賃")["fee_id"]
        self.delivery_fee = self.confirm_row("case-delivery", "配送費")["fee_id"]

    def projected_row(self, case_id="case-a", item_id=None):
        snapshot = self.store.case_snapshot(self.actor, case_id)
        projection = comparison_projection(snapshot["items"], snapshot["fees"])
        row = next((row for row in projection["rows"]
                    if item_id is None or item_id in row["item_ids"]), None)
        return snapshot, row

    def confirm_row(self, case_id, name):
        snapshot, row = self.projected_row(case_id)
        return self.rows.confirm(self.actor, RowConfirmationCommand(
            case_id, row["row_id"], snapshot["case"]["case_revision"],
            tuple(RowMember(item["item_id"], item["version"],
                            item["current_memory_version"]) for item in row["member_items"]),
            uuid4().hex, new_fee_name=name))

    def command(self, *, item_id="item-c", target=None, name=None, case_id="case-a", **changes):
        snapshot, row = self.projected_row(case_id, item_id)
        item = next(item for item in row["member_items"] if item["item_id"] == item_id)
        values = dict(case_id=case_id, row_id=row["row_id"], item_id=item_id,
            expected_case_revision=snapshot["case"]["case_revision"],
            expected_item_version=item["version"],
            expected_memory_version=item["current_memory_version"],
            expected_memory_evidence=(item["memory_resolution"]["evidence_versions"]
                if (item.get("memory_resolution") or {}).get("kind") == "LEGACY_PROJECTION" else None),
            idempotency_key=uuid4().hex, target_fee_id=target,
            new_fee_name=name,
            expected_fee_id=target if target else None,
            expected_name_version=(next(fee["name_version"] for fee in snapshot["fees"]
                if fee["fee_id"] == target) if target else None))
        values.update(changes)
        return SingleMemberCorrectionCommand(**values)

    def item(self, item_id):
        return next(item for item in self.store.list_items(self.actor, "case-a")
                    if item["item_id"] == item_id)

    def test_moves_only_c_and_all_three_aliases_reuse_with_new_prices(self):
        before = {item["item_id"]: item for item in self.store.list_items(self.actor, "case-a")}
        command = self.command(target=self.delivery_fee)
        result = self.singles.correct(self.actor, command)
        self.assertEqual(result["kind"], "SINGLE_MEMBER_CORRECTION")
        self.assertTrue(result["memory_reusable"])
        after = {item["item_id"]: item for item in self.store.list_items(self.actor, "case-a")}
        for item_id in ("item-a", "item-b"):
            self.assertEqual({key: value for key, value in after[item_id].items()
                              if key != "case_revision"},
                             {key: value for key, value in before[item_id].items()
                              if key != "case_revision"})
        self.assertEqual(after["item-c"]["current_fee_id"], self.delivery_fee)
        for field in ("observation", "source_ref", "current_price", "currency"):
            self.assertEqual(after["item-c"][field], before["item-c"][field])
        rows = comparison_projection(list(after.values()), self.store.list_fees(self.actor))["rows"]
        self.assertEqual({tuple(sorted(row["item_ids"])) for row in rows},
                         {("item-a", "item-b"), ("item-c",)})
        for suffix, forwarder, label, expected_fee, price in (
            ("next-a", "company-A", "O/F 20'", self.sea_fee, "1300"),
            ("next-b", "company-B", "OCEAN FREIGHT 20'", self.sea_fee, "1250"),
            ("next-c", "company-C", "LOCAL DELIVERY", self.delivery_fee, "1380"),
        ):
            self.add_line(suffix, forwarder_id=forwarder, raw_label=label, unit_price=price,
                          currency="USD", ai_suggested_name="untrusted suggestion")
            decision = self.service.classify(self.actor, "item-" + suffix)
            self.assertEqual((decision.kind, decision.fee_id, decision.unit_price),
                             (DecisionKind.MEMORY_APPLIED, expected_fee, price))

    def test_shared_learning_key_keeps_c_manual_and_future_quotes_under_review(self):
        # A and the corrected item have the same learning signature, so this
        # signature cannot distinguish their divergent human classifications.
        # The legacy synthetic policy also keys by company, so use A's company.
        self.add_line("shared", existing_case="case-a", forwarder_id="company-A",
                      raw_label="O/F 20'", ai_suggested_name="海上運賃", currency="USD")
        snapshot, row = self.projected_row("case-a", "item-shared")
        member = next(item for item in row["member_items"] if item["item_id"] == "item-shared")
        self.rows.confirm(self.actor, RowConfirmationCommand("case-a", row["row_id"],
            snapshot["case"]["case_revision"],
            (RowMember("item-shared", member["version"], member["current_memory_version"]),),
            uuid4().hex, target_fee_id=self.sea_fee, expected_fee_id=self.sea_fee,
            expected_name_version=next(fee["name_version"] for fee in snapshot["fees"]
                                       if fee["fee_id"] == self.sea_fee)))
        current = self.command(item_id="item-shared", target=self.delivery_fee)
        before = {item["item_id"]: item for item in self.store.list_items(self.actor, "case-a")}
        result = self.singles.correct(self.actor, current)
        self.assertEqual(result["member"]["memory_effect"], "CONFLICTED")
        self.assertEqual(result["member"]["memory_action"], "CONFLICTED_SHARED_KEY")
        self.assertFalse(result["memory_reusable"])
        self.assertEqual(self.item("item-shared")["current_fee_id"], self.delivery_fee)
        for item_id in ("item-a", "item-b", "item-c"):
            self.assertEqual(self.item(item_id)["current_fee_id"], before[item_id]["current_fee_id"])
        # A subsequent ordinary row confirmation must not quietly convert the
        # ambiguous signature back into a global automatic rule.
        snapshot, row = self.projected_row("case-a", "item-a")
        followup = self.rows.confirm(self.actor, RowConfirmationCommand(
            "case-a", row["row_id"], snapshot["case"]["case_revision"],
            tuple(RowMember(member["item_id"], member["version"],
                            member["current_memory_version"])
                  for member in row["member_items"]), uuid4().hex,
            target_fee_id=self.sea_fee, expected_fee_id=self.sea_fee,
            expected_name_version=next(fee["name_version"] for fee in snapshot["fees"]
                                       if fee["fee_id"] == self.sea_fee)))
        self.assertFalse(followup["memory_reusable"])
        self.assertEqual(self.item("item-a")["current_memory_status"], "CONFLICTED")
        self.add_line("future-shared-a", forwarder_id="company-A",
                      raw_label="O/F 20'", unit_price="1300",
                      currency="USD", ai_suggested_name="海上運賃")
        self.add_line("future-shared-c", forwarder_id="company-A",
                      raw_label="O/F 20'", unit_price="1380",
                      currency="USD", ai_suggested_name="配送費")
        for suffix in ("future-shared-a", "future-shared-c"):
            decision = self.service.classify(self.actor, "item-" + suffix)
            self.assertEqual(decision.kind, DecisionKind.REVIEW_REQUIRED)
            self.assertIsNone(decision.fee_id)

    def test_replay_stale_target_row_scope_and_rollback(self):
        command = self.command(target=self.delivery_fee)
        before = self.database_snapshot()
        for bad in (replace(command, row_id="0" * 64),
                    replace(command, item_id="item-delivery"),
                    replace(command, expected_case_revision=command.expected_case_revision - 1),
                    replace(command, expected_item_version=command.expected_item_version + 1),
                    replace(command, expected_memory_version=command.expected_memory_version + 1),
                    replace(command, expected_name_version=command.expected_name_version + 1)):
            with self.assertRaises((ConflictError, ScopeError, ValueError)):
                self.singles.correct(self.actor, bad)
            self.assertEqual(self.database_snapshot(), before)
        def fail(stage):
            if stage == "after_memory":
                raise RuntimeError("INJECTED_TEST_CASE_SINGLE_MEMBER_ROLLBACK")
        with self.assertRaisesRegex(RuntimeError, "INJECTED_TEST_CASE"):
            SingleMemberCorrectionService(self.repo, fail).correct(self.actor, command)
        self.assertEqual(self.database_snapshot(), before)
        result = self.singles.correct(self.actor, command)
        after = self.database_snapshot()
        self.assertEqual(self.singles.correct(self.actor, command), result)
        self.assertEqual(self.database_snapshot(), after)
        with self.assertRaises(ConflictError):
            self.singles.correct(self.actor, replace(command, idempotency_key=command.idempotency_key,
                                                     target_fee_id=self.sea_fee,
                                                     expected_fee_id=self.sea_fee))

    def test_unique_new_fee_created_and_existing_name_requires_selection(self):
        before = self.database_snapshot()
        with self.assertRaises(ConflictError):
            self.singles.correct(self.actor, self.command(name="配送費"))
        self.assertEqual(self.database_snapshot(), before)
        command = self.command(name="港湾追加費")
        result = self.singles.correct(self.actor, command)
        self.assertNotIn(result["fee_id"], {self.sea_fee, self.delivery_fee})
        self.assertEqual(self.item("item-c")["current_fee_id"], result["fee_id"])
        self.add_line("new-name-next", raw_label="LOCAL DELIVERY",
                      forwarder_id="company-C", unit_price="1450", currency="USD")
        decision = self.service.classify(self.actor, "item-new-name-next")
        self.assertEqual((decision.kind, decision.fee_id, decision.unit_price),
                         (DecisionKind.MEMORY_APPLIED, result["fee_id"], "1450"))

    def test_two_concurrent_single_edits_have_one_winner(self):
        first = self.command(target=self.delivery_fee)
        second = replace(first, idempotency_key=uuid4().hex,
                         target_fee_id=None, expected_fee_id=None,
                         expected_name_version=None, new_fee_name="港湾追加費")
        barrier = Barrier(2)
        def save(command):
            barrier.wait(timeout=10)
            try:
                return SingleMemberCorrectionService(PostgresRepository(DSN)).correct(self.actor, command)
            except ConflictError:
                return "CONFLICT"
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(save, (first, second)))
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        self.assertEqual(results.count("CONFLICT"), 1)
        self.assertEqual(self.item("item-c")["version"], 3)

    def test_condition_corrected_member_can_reclassify_without_learning_old_key(self):
        snapshot, row = self.projected_row("case-a", "item-c")
        item = next(item for item in row["member_items"] if item["item_id"] == "item-c")
        condition = ConditionCorrectionCommand(case_id="case-a", row_id=row["row_id"],
            item_id="item-c", expected_case_revision=snapshot["case"]["case_revision"],
            expected_item_version=item["version"],
            expected_memory_version=item["current_memory_version"],
            idempotency_key=uuid4().hex, field="equipment", value="40FT")
        ConditionCorrectionService(self.repo).correct(self.actor, condition)
        command = self.command(target=self.delivery_fee)
        before = self.item("item-c")["current_memory_version"]
        result = self.singles.correct(self.actor, command)
        self.assertEqual(result["member"]["memory_effect"], "NOT_ELIGIBLE_CONDITION_CORRECTED")
        self.assertFalse(result["memory_reusable"])
        self.assertEqual(self.item("item-c")["current_memory_version"], before)
        self.assertEqual(self.item("item-c")["current_fee_id"], self.delivery_fee)
        self.add_line("future-original-condition", forwarder_id="company-C",
                      raw_label="LOCAL DELIVERY", unit_price="1500", currency="USD")
        self.assertEqual(self.service.classify(self.actor, "item-future-original-condition").kind,
                         DecisionKind.REVIEW_REQUIRED)

    def test_http_accepts_only_single_member_fields(self):
        command = self.command(target=self.delivery_fee)
        url = f"/api/cases/case-a/comparison/{command.row_id}/members/item-c/correction"
        body = asdict(command)
        for field in ("case_id", "row_id", "item_id"):
            body.pop(field)
        with tempfile.TemporaryDirectory() as storage, TestClient(
                create_app(DSN, storage, tenant_id=self.tenant)) as client:
            self.assertEqual(client.post(url, json={**body, "unit_price": "1"}).status_code, 422)
            response = client.post(url, json=body)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["member"]["item_id"], "item-c")


if __name__ == "__main__":
    unittest.main()
