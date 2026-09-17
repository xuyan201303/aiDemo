"""F1 field grounding tests: synthetic application evidence only.

No SDK public API is inferred and no original PDF/Excel, model, or database is
used. evidence_unit is the application's trusted adapter-side contract; the AI
can only cite value/source_block_id and cannot manufacture these boundaries.
"""
from copy import deepcopy
from dataclasses import replace
import unittest

from jsonschema import Draft202012Validator

from sanzo_demo.extraction import EXTRACTION_SCHEMA, ReadResult, materialize

FIELDS = ("raw_label", "raw_value", "currency", "company",
          "transport_mode", "equipment", "billing_unit", "charge_scope", "extra_terms")
CONTEXT = FIELDS[4:]


def field_fixture(kind="PDF", *, amount="12000", currency="JPY", overrides=None):
    """Each block is one complete SDK field or original Excel cell, not a page."""
    doc = {"document_id": "MECHANISM_F1_DOCUMENT", "case_id": "MECHANISM_F1_CASE", "sha256": "a" * 64}
    values = {
        "raw_label": "配送手数料", "raw_value": amount, "currency": currency,
        "company": "MECHANISM_COMPANY_A", "transport_mode": "SEA", "equipment": "20FT",
        "billing_unit": "PER_CONTAINER", "charge_scope": "共通", "extra_terms": "N/A",
    }
    values.update(overrides or {})
    blocks, entries = [], {}
    for index, field_name in enumerate(FIELDS):
        source_id = "MECHANISM_FIELD_" + field_name
        unit = {"kind": "EXCEL_CELL" if kind == "EXCEL" else "SDK_FIELD",
                "field_name": field_name, "record_id": "MECHANISM_LINE_A", "scope": "LINE"}
        locator = ({"sheet_name": "MECHANISM_見積", "A1_range": chr(65 + index) + "7"}
                   if kind == "EXCEL" else {"page_number": 1})
        source_ref = {
            "document_id": doc["document_id"], "document_sha256": doc["sha256"],
            "source_block_ids": [source_id], "kind": "EXCEL_RANGE" if kind == "EXCEL" else "PDF_PAGE",
            "locator_status": "VERIFIED" if kind == "EXCEL" else "PAGE_ONLY",
            "locator": locator, "text_verification": "VERIFIED", "evidence_unit": unit,
        }
        blocks.append({"source_id": source_id, "raw_text": values[field_name], "source_ref": source_ref})
        entries[field_name] = {"value": values[field_name], "source_block_id": source_id}
    entry = {field_name: entries[field_name] for field_name in FIELDS[:4]}
    entry["context"] = {field_name: entries[field_name] for field_name in CONTEXT}
    reading = ReadResult(doc["sha256"], tuple(blocks),
                         {"test_adapter": "MECHANISM_F1_COMPLETE_FIELD_FIXTURE"}, origin="MECHANISM_TEST")
    return doc, reading, {"lines": [entry], "document_issues": []}


def changed_block(reading, field_name, change):
    blocks = deepcopy(reading.blocks)
    block = next(block for block in blocks
                 if block["source_ref"]["evidence_unit"]["field_name"] == field_name)
    change(block)
    return replace(reading, blocks=blocks)


class FieldGroundingTests(unittest.TestCase):
    def assert_not_verified(self, doc, reading, payload):
        try:
            lines, issues = materialize(doc, "MECHANISM_F1_TENANT", reading, payload)
        except ValueError:
            return
        # Both explicit rejection and visibly unverified observations are safe.
        # Merely having a warning while retaining an exact learning key is not.
        self.assertTrue(issues, "Invalid field evidence must be reported")
        for line in lines:
            self.assertIsNone(line.observation.memory_key(),
                              "Invalid field evidence must never authorize exact learning")

    def assert_verified(self, doc, reading, payload, *, price="12000", original_amount=None):
        lines, issues = materialize(doc, "MECHANISM_F1_TENANT", reading, payload)
        self.assertEqual(issues, [])
        self.assertEqual(len(lines), 1)
        line = lines[0]
        self.assertTrue(line.observation.source_ready)
        self.assertTrue(line.observation.context.complete())
        self.assertIsNotNone(line.observation.memory_key())
        self.assertEqual(line.observation.unit_price, price)
        if original_amount is not None:
            self.assertEqual(line.raw_value, original_amount)
        return line

    def test_amount_12000_cannot_ground_1200(self):
        doc, reading, payload = field_fixture()
        payload["lines"][0]["raw_value"]["value"] = "1200"
        self.assert_not_verified(doc, reading, payload)

    def test_complete_original_label_cannot_ground_its_prefix(self):
        doc, reading, payload = field_fixture()
        payload["lines"][0]["raw_label"]["value"] = "配送"
        self.assert_not_verified(doc, reading, payload)

    def test_complete_condition_cannot_ground_its_suffix(self):
        doc, reading, payload = field_fixture()
        payload["lines"][0]["context"]["charge_scope"]["value"] = "通"
        self.assert_not_verified(doc, reading, payload)

    def test_complete_number_on_page_is_not_a_line_amount(self):
        doc, reading, payload = field_fixture()
        reading = changed_block(reading, "raw_value", lambda block: (
            block.update(raw_text="MECHANISM page text: total 12000 / date 2026"),
            block["source_ref"].pop("evidence_unit"),
        ))
        self.assert_not_verified(doc, reading, payload)

    def test_same_complete_amount_from_another_line_is_not_evidence_for_this_line(self):
        doc, reading, payload = field_fixture()
        original = next(block for block in reading.blocks
                        if block["source_ref"]["evidence_unit"]["field_name"] == "raw_value")
        foreign = deepcopy(original)
        foreign["source_id"] = "MECHANISM_LINE_B_SAME_AMOUNT"
        foreign["source_ref"]["source_block_ids"] = [foreign["source_id"]]
        foreign["source_ref"]["evidence_unit"]["record_id"] = "MECHANISM_LINE_B"
        reading = replace(reading, blocks=reading.blocks + (foreign,))
        payload["lines"][0]["raw_value"]["source_block_id"] = foreign["source_id"]
        self.assert_not_verified(doc, reading, payload)

    def test_same_record_id_on_another_pdf_page_cannot_supply_price(self):
        doc, reading, payload = field_fixture()
        reading = changed_block(reading, "raw_value",
                                lambda block: block["source_ref"]["locator"].update(page_number=2))
        self.assert_not_verified(doc, reading, payload)

    def test_excel_price_on_another_sheet_cannot_supply_this_record(self):
        doc, reading, payload = field_fixture("EXCEL")
        reading = changed_block(reading, "raw_value",
                                lambda block: block["source_ref"]["locator"].update(sheet_name="OTHER_SHEET"))
        self.assert_not_verified(doc, reading, payload)

    def test_arbitrary_complete_numeric_field_cannot_be_used_as_price(self):
        doc, reading, payload = field_fixture()
        reading = changed_block(reading, "raw_value",
            lambda block: block["source_ref"]["evidence_unit"].update(field_name="invoice_number"))
        self.assert_not_verified(doc, reading, payload)

    def test_repeated_numeric_tokens_are_not_one_complete_sdk_amount_field(self):
        doc, reading, payload = field_fixture()
        reading = changed_block(reading, "raw_value", lambda block: block.update(raw_text="12000 12000"))
        self.assert_not_verified(doc, reading, payload)

    def test_entire_legacy_page_cannot_prove_all_field_boundaries(self):
        doc, reading, payload = field_fixture()
        page = deepcopy(reading.blocks[0])
        page["raw_text"] = " | ".join(block["raw_text"] for block in reading.blocks)
        page["source_ref"].pop("evidence_unit")
        reading = replace(reading, blocks=(page,))
        for key in FIELDS[:4]:
            payload["lines"][0][key]["source_block_id"] = page["source_id"]
        for key in CONTEXT:
            payload["lines"][0]["context"][key]["source_block_id"] = page["source_id"]
        self.assert_not_verified(doc, reading, payload)

    def test_full_pdf_sdk_fields_from_same_record_are_valid(self):
        self.assert_verified(*field_fixture(), original_amount="12000")

    def test_full_excel_original_cell_values_from_same_record_are_valid(self):
        self.assert_verified(*field_fixture("EXCEL"), original_amount="12000")

    def test_multi_cell_range_cannot_claim_one_excel_cell_value(self):
        doc, reading, payload = field_fixture("EXCEL")
        reading = changed_block(reading, "raw_value",
                                lambda block: block["source_ref"]["locator"].update(A1_range="B7:C7"))
        self.assert_not_verified(doc, reading, payload)

    def test_declared_currency_affixes_and_valid_comma_grouping_preserve_original(self):
        for original in ("12,000", "￥12,000", "JPY 12,000", "12,000円"):
            with self.subTest(original=original):
                self.assert_verified(*field_fixture(amount=original), original_amount=original)

    def test_ai_canonical_amount_must_equal_declared_normalization_of_whole_field(self):
        for original in ("12,000", "￥12,000", "JPY 12,000", "12,000円"):
            with self.subTest(original=original):
                doc, reading, payload = field_fixture(amount=original)
                payload["lines"][0]["raw_value"]["value"] = "12000"
                self.assert_verified(doc, reading, payload, original_amount=original)

    def test_currency_affix_must_agree_with_current_currency_field(self):
        doc, reading, payload = field_fixture(amount="USD 12,000", currency="JPY")
        self.assert_not_verified(doc, reading, payload)

    def test_decimal_equality_does_not_authorize_undeclared_tail_zero_removal(self):
        doc, reading, payload = field_fixture(amount="12,000.00")
        payload["lines"][0]["raw_value"]["value"] = "12000"
        self.assert_not_verified(doc, reading, payload)

    def test_grouping_normalization_keeps_explicit_decimal_trailing_zeros(self):
        doc, reading, payload = field_fixture(amount="12,000.00")
        payload["lines"][0]["raw_value"]["value"] = "12000.00"
        self.assert_verified(doc, reading, payload, price="12000.00", original_amount="12,000.00")

    def test_invalid_grouping_cannot_be_normalized_into_a_price(self):
        doc, reading, payload = field_fixture(amount="1,20,00")
        payload["lines"][0]["raw_value"]["value"] = "12000"
        self.assert_not_verified(doc, reading, payload)

    def test_document_scope_requires_explicit_record_applicability(self):
        doc, reading, payload = field_fixture()
        reading = changed_block(reading, "company",
            lambda block: block["source_ref"]["evidence_unit"].update(
                scope="DOCUMENT", record_id="MECHANISM_DOCUMENT_SCOPE"))
        self.assert_not_verified(doc, reading, payload)

    def test_document_scope_cannot_supply_label_or_amount(self):
        doc, reading, payload = field_fixture()
        reading = changed_block(reading, "raw_value",
            lambda block: block["source_ref"]["evidence_unit"].update(
                scope="DOCUMENT", applies_to_record_ids=["MECHANISM_LINE_A"]))
        self.assert_not_verified(doc, reading, payload)

    def test_document_scope_company_with_explicit_applicability_is_valid(self):
        doc, reading, payload = field_fixture()
        reading = changed_block(reading, "company",
            lambda block: block["source_ref"]["evidence_unit"].update(
                scope="DOCUMENT", record_id="MECHANISM_DOCUMENT_SCOPE",
                applies_to_record_ids=["MECHANISM_LINE_A"]))
        self.assert_verified(doc, reading, payload, original_amount="12000")

    def shared_header_fixture(self, location, *, unique_record_ids=False):
        kind = "PDF" if location == "page" else "EXCEL"
        doc, reading, payload = field_fixture(kind)
        first = list(deepcopy(reading.blocks))
        second = list(deepcopy(reading.blocks))
        second_record_id = "MECHANISM_LINE_B" if unique_record_ids else "MECHANISM_LINE_A"
        source_map = {}
        for block in second:
            old_id = block["source_id"]
            block["source_id"] += "_SECOND_RECORD"
            source_map[old_id] = block["source_id"]
            source = block["source_ref"]
            source["source_block_ids"] = [block["source_id"]]
            source["evidence_unit"]["record_id"] = second_record_id
            if location == "page":
                source["locator"]["page_number"] = 2
            elif location == "sheet":
                source["locator"]["sheet_name"] = "MECHANISM_OTHER_SHEET"
            else:
                source["locator"]["A1_range"] = source["locator"]["A1_range"][:-1] + "8"
            if not unique_record_ids and source["evidence_unit"]["field_name"] == "company":
                block["raw_text"] = "MECHANISM_COMPANY_B"
        entry = payload["lines"][0]
        for name in FIELDS[:4]:
            entry[name]["source_block_id"] = source_map[entry[name]["source_block_id"]]
        for name in CONTEXT:
            entry["context"][name]["source_block_id"] = source_map[entry["context"][name]["source_block_id"]]
        header = next(block for block in first
                      if block["source_ref"]["evidence_unit"]["field_name"] == "company")
        header["source_ref"]["evidence_unit"].update(
            scope="DOCUMENT", record_id="MECHANISM_HEADER",
            applies_to_record_ids=(["MECHANISM_LINE_A", "MECHANISM_LINE_B"]
                                   if unique_record_ids else ["MECHANISM_LINE_A"]))
        # This first-record header must not apply to another physical record
        # simply because an adapter reused its unqualified record_id.
        entry["company"] = {"value": header["raw_text"], "source_block_id": header["source_id"]}
        return doc, replace(reading, blocks=tuple(first + second)), payload

    def test_shared_header_cannot_cross_pdf_pages_with_duplicate_record_id(self):
        self.assert_not_verified(*self.shared_header_fixture("page"))

    def test_shared_header_cannot_cross_excel_sheets_with_duplicate_record_id(self):
        self.assert_not_verified(*self.shared_header_fixture("sheet"))

    def test_shared_header_cannot_cross_excel_rows_with_duplicate_record_id(self):
        self.assert_not_verified(*self.shared_header_fixture("row"))

    def test_shared_header_target_list_cannot_include_nonexistent_record(self):
        doc, reading, payload = field_fixture()
        reading = changed_block(reading, "company",
            lambda block: block["source_ref"]["evidence_unit"].update(
                scope="DOCUMENT", record_id="MECHANISM_HEADER",
                applies_to_record_ids=["MECHANISM_LINE_A", "MISSING_RECORD"]))
        self.assert_not_verified(doc, reading, payload)

    def test_shared_header_can_apply_to_explicit_unique_records_across_locations(self):
        for location in ("page", "sheet", "row"):
            with self.subTest(location=location):
                self.assert_verified(*self.shared_header_fixture(location, unique_record_ids=True),
                                     original_amount="12000")

    def test_same_page_duplicate_record_fields_cannot_be_mixed_as_one_line(self):
        doc, reading, payload = field_fixture()
        duplicate = []
        for name in ("raw_label", "raw_value"):
            block = deepcopy(next(b for b in reading.blocks
                                  if b["source_ref"]["evidence_unit"]["field_name"] == name))
            block["source_id"] += "_OTHER_PHYSICAL_LINE"
            block["source_ref"]["source_block_ids"] = [block["source_id"]]
            if name == "raw_label":
                block["raw_text"] = "MECHANISM_OTHER_FEE"
                payload["lines"][0][name] = {
                    "value": block["raw_text"], "source_block_id": block["source_id"]}
            duplicate.append(block)
        # Same page, reused ID and the same price token cannot disambiguate two
        # different complete label/amount fields. This needs adapter review.
        self.assert_not_verified(doc, replace(reading, blocks=reading.blocks + tuple(duplicate)), payload)

    def test_unknown_condition_stays_unknown_despite_complete_field_evidence(self):
        for marker in ("UNKNOWN", "不明"):
            with self.subTest(marker=marker):
                doc, reading, payload = field_fixture(overrides={"equipment": marker})
                lines, issues = materialize(doc, "MECHANISM_F1_TENANT", reading, payload)
                self.assertEqual(len(lines), 1)
                self.assertTrue(issues)
                line = lines[0]
                self.assertIsNone(line.observation.context.equipment)
                self.assertFalse(line.observation.context.complete())
                self.assertIsNone(line.observation.memory_key())
                self.assertEqual(line.observation.context.extra_terms, "N/A")
                self.assertEqual(line.source_ref["field_sources"]["equipment"]["value"], marker)

    def test_ai_schema_cannot_accept_boundaries_offsets_or_source_metadata(self):
        doc, reading, payload = field_fixture()
        payload["lines"][0]["raw_value"]["evidence_unit"] = {
            "kind": "SDK_FIELD", "field_name": "raw_value", "record_id": "MECHANISM_LINE_A", "scope": "LINE"}
        self.assertFalse(Draft202012Validator(EXTRACTION_SCHEMA).is_valid(payload))


if __name__ == "__main__":
    unittest.main(verbosity=2)
