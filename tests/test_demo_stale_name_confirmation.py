"""F01: visible existing-fee name versions are part of human confirmation.

Dedicated SANZO_TEST_DSN only. Fixtures are explicitly synthetic; no SDK or
OpenAI is used. All business rows are scoped to each test's unique tenant.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from threading import Event
import tempfile
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from sanzo_core.domain import ConflictError, Principal, request_digest
from sanzo_core.postgres import PostgresRepository
from sanzo_demo.app import create_app
from sanzo_demo.row_confirmation import RowConfirmationService
import test_demo_fee_rename_r3 as fixture


@unittest.skipUnless(fixture.DSN, "NOT_RUN: isolated real PostgreSQL required")
class StaleNameConfirmationTests(unittest.TestCase):
    # Explicit helper assignment avoids rediscovering/rerunning the R3 suite.
    for _name in ("new_case", "prepared_line", "commit_line", "import_line", "snapshot",
                  "projection", "current", "fee", "name_versions", "slot", "command",
                  "rename_command", "rename", "export_values", "assert_old_case_preserved",
                  "database_snapshot"):
        locals()[_name] = getattr(fixture.FeeRenamePostgresR3Tests, _name)

    def setUp(self):
        fixture.FeeRenamePostgresR3Tests.setUp(self)
        self.new_case("window-a")
        self.new_case("window-b")
        self.a = self.import_line("window-a", "A", "DOC FEE", "8000")
        self.b = self.import_line("window-b", "B", "DOCUMENTATION FEE", "7500.00")
        self.repo_a = PostgresRepository(fixture.DSN)
        self.repo_b = PostgresRepository(fixture.DSN)
        self.window_a = RowConfirmationService(self.repo_a)
        self.window_b = RowConfirmationService(self.repo_b)
        self.assertIsNot(self.repo_a, self.repo_b)

    def confirm_command(self, case="window-b", *, name_version=1, fee_id=None,
                        intent="CONFIRM_MEMBERS", unversioned=False, **changes):
        fee_id = fee_id or self.fee_id
        values = dict(new_fee_name=None, target_fee_id=fee_id, intent=intent,
                      expected_fee_id=fee_id, expected_name_version=name_version)
        values.update(changes)
        command = self.command(case, **values)
        if unversioned:
            # Explicit old-client request; do not let helper modernization attach
            # an unseen current version and accidentally erase this regression.
            command = replace(command, expected_fee_id=None, expected_name_version=None)
        return command

    def change_a(self, name=fixture.NEW_NAME):
        return self.window_a.confirm(self.actor, self.rename_command("window-a", name))

    def assert_rejected_without_writes(self, command, *, service=None, missing_pair=False):
        before = self.database_snapshot()
        with self.assertRaises((ValueError, ConflictError) if missing_pair else ConflictError):
            (service or self.window_b).confirm(self.actor, command)
        self.assertEqual(self.database_snapshot(), before,
                         "Rejected stale confirmation must write no item/case/memory/history/command/name rows")

    def http_post(self, command):
        payload = asdict(command)
        case_id, row_id = payload.pop("case_id"), payload.pop("row_id")
        with tempfile.TemporaryDirectory(prefix="sanzo-f01-http-") as directory:
            app = create_app(fixture.DSN, directory, tenant_id=self.tenant)
            with TestClient(app) as client:
                return client.post(f"/api/cases/{case_id}/comparison/{row_id}/confirmation", json=payload)

    def test_A_red_unversioned_stale_confirmation_cannot_silently_adopt_new_name(self):
        stale = self.confirm_command(unversioned=True)
        visible = self.current(self.b)
        self.assertEqual((visible["current_fee_name"], visible["current_fee_name_version"]), (fixture.OLD_NAME, 1))
        self.change_a()
        before = self.database_snapshot()
        try:
            response = self.window_b.confirm(self.actor, stale)
        except (ValueError, ConflictError):
            response = None
        if response is not None:
            changed = self.current(self.b)
            self.fail("STALE_CONFIRM_SILENT_ADOPTION: B displayed %r/v%d but saved %r/v%d; "
                      "item_version %d->%d; memory_version %d->%d; response=%r" %
                      (visible["current_fee_name"], visible["current_fee_name_version"],
                       changed["current_fee_name"], changed["current_fee_name_version"],
                       visible["version"], changed["version"], visible["current_memory_version"],
                       changed["current_memory_version"], response))
        self.assertEqual(self.database_snapshot(), before)

    def test_A_versioned_stale_confirmation_returns_409_and_zero_writes(self):
        stale = self.confirm_command()
        self.change_a()
        before = self.database_snapshot()
        response = self.http_post(stale)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(self.current(self.b)["current_fee_name"], fixture.OLD_NAME)

    def test_B_fresh_existing_name_pair_saves_unchanged_price(self):
        old = self.current(self.b)
        result = self.window_b.confirm(self.actor, self.confirm_command())
        self.assertEqual(result["fee_id"], self.fee_id)
        current = self.current(self.b)
        self.assertEqual((current["current_fee_name"], current["current_fee_name_version"]), (fixture.OLD_NAME, 1))
        for key in ("observation", "source_ref", "currency", "current_price", "raw_value"):
            self.assertEqual(current[key], old[key], key)
        self.assertTrue(result["memory_reusable"])

    def test_B_explicit_refresh_to_current_name_version_can_save(self):
        stale = self.confirm_command()
        self.change_a()
        self.assert_rejected_without_writes(stale)
        refreshed = self.confirm_command(name_version=2)
        result = self.window_b.confirm(self.actor, refreshed)
        self.assertEqual(result["fee_id"], self.fee_id)
        current = self.current(self.b)
        self.assertEqual((current["current_fee_name"], current["current_fee_name_version"]), (fixture.NEW_NAME, 2))
        self.assertEqual(current["current_price"], "7500.00")
        # New isolated-case import uses explicit synthetic facts through the
        # ordinary persistence entry; neither actual SDK nor AI is invoked.
        self.new_case("after-explicit-refresh")
        later = self.import_line("after-explicit-refresh", "B", "DOCUMENTATION FEE", "9500")
        self.assertEqual(later["input_kind"], "SYNTHETIC_STRUCTURED")
        self.assertEqual((later["current_fee_id"], later["current_fee_name"],
                          later["current_fee_name_version"]), (self.fee_id, fixture.NEW_NAME, 2))
        self.assertEqual(later["initial_classification"]["kind"], "MEMORY_APPLIED")
        self.assertEqual(later["saved_status"], "MEMORY_APPLIED")
        self.assertEqual((later["current_price"], later["currency"], later["raw_value"]),
                         ("9500", "JPY", "9500"))
        self.assertEqual(later["observation"]["unit_price"], "9500")
        self.assertNotEqual(later["source_ref"]["document_id"], current["source_ref"]["document_id"])
        self.assertEqual(self.current(self.b)["current_price"], "7500.00")
        self.assertEqual(self.current(self.b)["version"], current["version"])

    def test_C_existing_target_reclassification_requires_its_visible_name_version(self):
        self.new_case("different-target")
        self.import_line("different-target", "C", "OTHER CONCEPT RAW", "123")
        target = self.rows.confirm(self.actor, self.command("different-target", new_fee_name="Other X"))["fee_id"]
        stale = self.confirm_command(fee_id=target, intent="RECLASSIFY_MEMBERS")
        self.rows.confirm(self.actor, self.command("different-target", new_fee_name="Other Y",
            intent="RENAME_FEE", expected_fee_id=target, expected_name_version=1))
        self.assert_rejected_without_writes(stale)
        self.assertEqual(self.current(self.b)["current_fee_id"], self.fee_id)
        current = self.confirm_command(fee_id=target, name_version=2, intent="RECLASSIFY_MEMBERS")
        self.window_b.confirm(self.actor, current)
        self.assertEqual((self.current(self.b)["current_fee_id"], self.current(self.b)["current_fee_name"]), (target, "Other Y"))
        self.assertEqual(self.current(self.a)["current_fee_id"], self.fee_id)

    def test_D_X_to_Y_to_X_does_not_make_old_version_valid_again(self):
        stale = self.confirm_command()
        self.change_a()
        self.change_a(fixture.OLD_NAME)
        self.assertEqual((self.fee()["display_name"], self.fee()["name_version"]), (fixture.OLD_NAME, 3))
        self.assert_rejected_without_writes(stale)
        self.window_b.confirm(self.actor, self.confirm_command(name_version=3))
        self.assertEqual(self.current(self.b)["current_fee_name_version"], 3)

    def test_E_missing_pair_new_request_is_rejected_even_without_concurrent_rename(self):
        self.assert_rejected_without_writes(self.confirm_command(unversioned=True), missing_pair=True)

    def test_E_mismatched_fee_identity_cannot_bypass_target_name_version(self):
        stale = self.confirm_command(expected_fee_id="other-identity")
        before = self.database_snapshot()
        response = self.http_post(stale)
        self.assertIn(response.status_code, (409, 422), response.text)
        self.assertEqual(self.database_snapshot(), before)

    def test_F_exact_successful_request_replays_original_response_after_rename(self):
        command = self.confirm_command()
        first = self.window_b.confirm(self.actor, command)
        self.change_a()
        before = self.database_snapshot()
        self.assertEqual(self.window_b.confirm(self.actor, command), first)
        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(self.current(self.b)["current_fee_name"], fixture.OLD_NAME)

    def test_F_legacy_successful_unversioned_ledger_replay_survives_new_guard(self):
        current = self.confirm_command()
        first = self.window_b.confirm(self.actor, current)
        legacy = replace(current, expected_fee_id=None, expected_name_version=None,
                         idempotency_key="INJECTED_TEST_CASE_LEGACY_SUCCESS_" + uuid4().hex)
        # Historical fixture: an old-format successful command references the
        # actual saved response. No production ledger is modified or rewritten.
        with self.repo.transaction() as conn:
            self.repo.save_command(conn, self.actor, legacy, "CORRECT",
                                   request_digest(self.actor, legacy), legacy.case_id, first)
        self.change_a()
        before = self.database_snapshot()
        self.assertEqual(self.window_b.confirm(self.actor, legacy), first)
        self.assertEqual(self.database_snapshot(), before)

    def test_F_same_key_different_expected_name_version_is_not_replay(self):
        command = self.confirm_command()
        self.window_b.confirm(self.actor, command)
        self.change_a()
        self.assert_rejected_without_writes(replace(command, expected_name_version=2))

    def test_G_two_members_stale_rejection_has_no_partial_save(self):
        other = self.import_line("window-b", "B", "DOCUMENTATION FEE", "7700")
        stale = self.confirm_command()
        self.assertEqual(len(stale.members), 2)
        originals = {i["item_id"]: self.current(i) for i in (self.b, other)}
        self.change_a()
        self.assert_rejected_without_writes(stale)
        for item in (self.b, other):
            now = self.current(item)
            old = originals[item["item_id"]]
            for key in ("version", "current_fee_name", "current_fee_name_version", "current_price", "source_ref"):
                self.assertEqual(now[key], old[key], key)

    def test_G_name_guard_preserves_existing_case_item_memory_version_checks(self):
        original = self.confirm_command()
        bad_member = replace(original.members[0], expected_item_version=999)
        bad_memory = replace(original.members[0], expected_memory_version=999)
        for command in (replace(original, expected_case_revision=999),
                        replace(original, members=(bad_member,)), replace(original, members=(bad_memory,))):
            with self.subTest(command=command):
                self.assert_rejected_without_writes(command)

    def test_H_two_connections_waiting_for_rename_lock_cannot_adopt_unseen_name(self):
        stale = self.confirm_command(unversioned=True)
        rename_written, release_rename = Event(), Event()
        b_attempted, b_acquired, release_b = Event(), Event(), Event()
        observed = {}
        class RenameRepository(PostgresRepository):
            def lock_fee_catalog(inner, conn, tenant):
                observed["rename_pid"] = conn.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"]
                return super().lock_fee_catalog(conn, tenant)
        class ConfirmRepository(PostgresRepository):
            def lock_fee_catalog(inner, conn, tenant):
                observed["confirm_pid"] = conn.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"]
                b_attempted.set()
                super().lock_fee_catalog(conn, tenant)
                b_acquired.set()
                if not release_b.wait(timeout=15):
                    raise RuntimeError("INJECTED_TEST_CASE release confirmation timeout")
        def pause_name(stage):
            if stage == "after_fee_rename":
                rename_written.set()
                if not release_rename.wait(timeout=15):
                    raise RuntimeError("INJECTED_TEST_CASE release rename timeout")
        rename = self.rename_command("window-a")
        writer = RowConfirmationService(RenameRepository(fixture.DSN), fault_hook=pause_name)
        confirmer = RowConfirmationService(ConfirmRepository(fixture.DSN))
        with ThreadPoolExecutor(2) as executor:
            write_future = executor.submit(writer.confirm, self.actor, rename)
            try:
                self.assertTrue(rename_written.wait(timeout=10))
                confirm_future = executor.submit(confirmer.confirm, self.actor, stale)
                self.assertTrue(b_attempted.wait(timeout=10))
                self.assertFalse(b_acquired.is_set(), "B must wait while rename owns the name lock")
                self.assertNotEqual(observed["rename_pid"], observed["confirm_pid"])
                print("REAL_PG_TWO_CONNECTIONS", observed, "B_waited_for_catalog_lock=True", flush=True)
                release_rename.set()
                write_future.result(timeout=10)
                self.assertTrue(b_acquired.wait(timeout=10))
                after_rename = self.database_snapshot()
                release_b.set()
                with self.assertRaises((ValueError, ConflictError)):
                    confirm_future.result(timeout=10)
                self.assertEqual(self.database_snapshot(), after_rename)
            finally:
                release_rename.set()
                release_b.set()
        self.assertEqual((self.current(self.b)["current_fee_name"], self.current(self.b)["current_fee_name_version"]), (fixture.OLD_NAME, 1))

    def test_E_malformed_or_partial_name_pair_is_422_with_no_business_writes(self):
        command = self.confirm_command()
        base = asdict(command)
        case_id, row_id = base.pop("case_id"), base.pop("row_id")
        variants = [
            {"expected_name_version": True}, {"expected_name_version": 1.0},
            {"expected_name_version": "1"}, {"expected_name_version": 0},
            {"expected_name_version": -1}, {"expected_name_version": None},
            {"expected_fee_id": None}, {"expected_fee_id": ""},
            {"expected_fee_id": True}, {"expected_fee_id": 123},
            {"expected_fee_id": "different-stable-identity"},
            {"expected_fee_id": None, "expected_name_version": None},
        ]
        with tempfile.TemporaryDirectory(prefix="sanzo-f01-invalid-http-") as directory:
            with TestClient(create_app(fixture.DSN, directory, tenant_id=self.tenant)) as client:
                before = self.database_snapshot()
                for changes in variants:
                    with self.subTest(changes=changes):
                        response = client.post(f"/api/cases/{case_id}/comparison/{row_id}/confirmation",
                                               json={**base, **changes})
                        self.assertEqual(response.status_code, 422, response.text)
                        self.assertEqual(self.database_snapshot(), before)
