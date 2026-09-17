"""Reader limitations survive F1 and Excel; offline boundary tests, no AI."""
from dataclasses import replace
from copy import deepcopy
from io import BytesIO
import unittest
from openpyxl import load_workbook
from sanzo_demo.facts import structure_facts
from sanzo_demo.export import workbook_bytes
from test_demo_evidence_grounding import field_fixture

class ReaderIssuesTests(unittest.TestCase):
    def test_issue_kept_without_dropping_valid_fields(self):
        doc, reading, _ = field_fixture()
        issue = {"code": "SDK_LIMITATION", "reason": "READING_ORDER_UNRESOLVED"}
        reading = replace(reading, issues=(issue,))
        lines, issues = structure_facts(doc, "ISSUE_TEST", reading)
        self.assertEqual(len(lines), 1)
        self.assertIn(issue, issues)
        issues[0]["reason"] = "changed returned copy"
        self.assertEqual(reading.issues[0]["reason"], "READING_ORDER_UNRESOLVED")

    def test_empty_unresolved_document_preserves_issue(self):
        doc, reading, _ = field_fixture()
        issue = {"code": "EXTRACTION_UNRESOLVED", "reason": "HEADER_AMBIGUOUS"}
        lines, issues = structure_facts(doc, "ISSUE_TEST", replace(reading, blocks=(), issues=(issue,)))
        self.assertEqual(lines, [])
        self.assertEqual(issues, [issue])
        with self.assertRaisesRegex(ValueError, "SDK_BLOCK_LIMIT_OR_EMPTY"):
            replace(reading, blocks=()).validate(doc)

    def test_malformed_or_excessive_reader_issues_rejected(self):
        doc, reading, _ = field_fixture()
        for issues in ([{"code":"x"}], ({"message":"no code"},), ({"code":"x","bad":object()},),
                       ({"code":"x","message":"x" * (256 * 1024)},), tuple({"code":"x"} for _ in range(1001))):
            with self.subTest(kind=str(type(issues))), self.assertRaisesRegex(ValueError, "INVALID_READER_ISSUES"):
                replace(reading, issues=issues).validate(doc)

    def test_export_keeps_exact_sdk_limitation_and_unresolved_reason(self):
        data = workbook_bytes({"name":"ISSUES", "case_revision":1}, [],
            [{"document_id":"d", "original_filename":"synthetic.pdf", "sha256":"a"*64}],
            tasks=[{"document_id":"d", "state":"REVIEW_REQUIRED", "issues":[
                {"code":"SDK_LIMITATION", "reason":"READING_ORDER_UNRESOLVED"},
                {"code":"EXTRACTION_UNRESOLVED", "reason":"COMBINED_SCOPE_TERMS"}]}], reader_mode="REAL_SDK")
        book=load_workbook(BytesIO(data))
        try:
            text=" ".join(str(c.value) for row in book["未確認事項"] for c in row)
            self.assertIn("READING_ORDER_UNRESOLVED",text)
            self.assertIn("COMBINED_SCOPE_TERMS",text)
            self.assertIn("DRAFT",text)
        finally: book.close()
