"""Real wheel/isolated DB; only price-free name classification is offline.

Human alias choices below are semantic test inputs, never extracted SDK facts.
Live model quality and browser acceptance are not established by these tests.
"""
from copy import deepcopy
from io import BytesIO
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sanzo_demo.ai import ExtractionResult
from sanzo_demo.app import create_app
from sanzo_demo.comparison import comparison_projection
from sanzo_demo.ingestion import IntegrationRuntime
from sanzo_demo.review_state import learning_eligible
from sanzo_core.quote_confirmations import QuoteConfirmationCommand, QuoteConfirmationService
import test_demo_fee_policy as fee_helpers
import test_demo_real_sdk_pipeline as helpers

# Reused human choices from the established fee-policy regression.
NAMES = {"O/F 20'": "Ocean", "O/F 40'": "Ocean", "DOC FEE": "Document",
         "通関手数料": "Customs", "配送手数料": "Delivery",
         "OCEAN FREIGHT 20'": "Ocean", "OCEAN FREIGHT 40'": "Ocean",
         "DOCUMENTATION FEE": "Document", "CUSTOMS CLEARANCE": "Customs",
         "DELIVERY CHARGE": "Delivery"}


class OfflineNameClassifier:
    def __init__(self):
        from hashlib import sha256
        self.config = SimpleNamespace(authorized_sample_sha256=frozenset(
            sha256(p.read_bytes()).hexdigest() for p in helpers.SAMPLES.iterdir()
            if p.suffix in {'.xlsx', '.pdf'}))
        self.calls = []

    def extract(self, digest, blocks, schema, *, purpose):
        assert purpose == 'fee_classification'
        self.calls.append(deepcopy(blocks))
        suggestions = []
        for block in blocks:
            assert not {'amount', 'price', 'currency', 'raw_value'} & block.keys()
            name = NAMES[block['raw_label']]
            offered = [c for c in block.get('canonical_candidates', []) if c['name'] == name]
            suggestions.append(dict(item_id=block['item_id'], suggested_fee_name=name,
                                    matched_fee_id=offered[0]['fee_id'] if len(offered) == 1 else None))
        return ExtractionResult({'suggestions': suggestions},
            {'execution_kind': 'MECHANISM_TEST', 'actual_api_calls': 0, 'purpose': purpose})


@unittest.skipUnless(helpers.DSN, 'isolated PostgreSQL required')
class SDK18CompatibilityTests(unittest.TestCase):
    setUpClass = classmethod(helpers.RealSDKPipelineTests.setUpClass.__func__)
    setUp = helpers.RealSDKPipelineTests.setUp
    new_case = helpers.RealSDKPipelineTests.new_case
    upload = helpers.RealSDKPipelineTests.upload
    process = helpers.RealSDKPipelineTests.process
    items = helpers.RealSDKPipelineTests.items
    slot_count = helpers.RealSDKPipelineTests.slot_count
    read_evidence = helpers.RealSDKPipelineTests.read_evidence
    current = fee_helpers.FeePolicyPostgresTests.current
    command = fee_helpers.FeePolicyPostgresTests.command
    confirm = fee_helpers.FeePolicyPostgresTests.confirm

    def test_available_memory_then_limited_candidates_confirmation_and_new_prices(self):
        classifier = OfflineNameClassifier()
        self.runtime = IntegrationRuntime(self.repo, self.storage, reader=self.reader,
            extractor=classifier, reader_mode='REAL_SDK', allow_mechanism_tests=True)
        first = self.upload(helpers.FIRST)
        first_task = self.process(first)
        for item in self.items(first):
            self.assertEqual(self.confirm(item, NAMES[item['observation']['raw_label']])['memory_effect'], 'ACTIVE')
        old = self.demo.case_snapshot(self.actor, first['case_id'])
        case = self.new_case()
        a = self.upload(helpers.FIRST, case_id=case)
        self.process(a)
        self.assertTrue(all(i['classification_source'] == 'MEMORY' for i in self.items(a)))
        classifier.calls.clear()
        b = self.upload(helpers.PDF, case_id=case)
        task = self.process(b)
        evidence = self.read_evidence(task)
        public = evidence['sdk_identity']['public_outputs']['document_sdk.pdf.analyze_pdf']
        table = public['tables'][0]
        self.assertEqual(table['availability'], 'LIMITED_EVIDENCE')
        candidates = self.items(b)
        # Count is derived from the actual SDK grid, never from injected rows.
        self.assertEqual(len(candidates), table['row_count'] - 1)
        self.assertEqual(len(classifier.calls), 1)
        self.assertEqual(len(classifier.calls[0]), len(candidates))
        self.assertTrue(all(i['ai_matched_fee_id'] and i['current_fee_id'] is None for i in candidates))
        self.assertTrue(all(not learning_eligible(i) for i in candidates))
        snapshot = self.demo.case_snapshot(self.actor, case)
        projection = comparison_projection(snapshot['items'], snapshot['fees'], snapshot['documents'])
        self.assertEqual(len(projection['rows']), len(self.items(a)))
        self.assertTrue(all(len(row['member_items']) == 2 for row in projection['rows']))
        slots = self.slot_count()
        item = candidates[0]
        immutable = deepcopy(item['source_ref'])
        self.assertEqual(self.confirm(item, NAMES[item['observation']['raw_label']])['memory_effect'],
                         'NOT_ELIGIBLE_CONTEXT_OR_SOURCE')
        snapshot = self.demo.case_snapshot(self.actor, case)
        row = next(r for r in comparison_projection(snapshot['items'], snapshot['fees'], snapshot['documents'])['rows']
                   if item['item_id'] in r['item_ids'])
        current = self.current(item)
        command = QuoteConfirmationCommand(case_id=case, row_id=row['row_id'], item_id=item['item_id'],
            expected_case_revision=snapshot['case']['case_revision'], expected_item_version=current['version'],
            idempotency_key=uuid4().hex, kind='SOURCE_REVIEW', field=None, value=None,
            evidence_note='Synthetic PDF page 1: human checked original label and current amount')
        QuoteConfirmationService(self.repo, self.storage).confirm(self.actor, command)
        refreshed = self.current(item)
        self.assertTrue(refreshed['quote_confirmations']['source']['valid'])
        self.assertEqual(refreshed['source_ref'], immutable)
        self.assertFalse(learning_eligible(refreshed))
        self.assertEqual(self.slot_count(), slots)
        classifier.calls.clear()
        repeat = self.upload(helpers.PDF)
        self.process(repeat)
        self.assertTrue(all(i['classification_source'] != 'MEMORY' for i in self.items(repeat)))
        third = self.upload(helpers.SECOND)
        third_task = self.process(third)
        self.assertTrue(all(i['classification_source'] == 'MEMORY' for i in self.items(third)))
        delivery = next(i for i in self.items(third) if i['observation']['raw_label'] == '配送手数料')
        self.assertEqual(delivery['current_price'], '9500')
        self.assertEqual(delivery['source_ref']['field_sources']['raw_value']['original_value'], '9500')
        self.assertEqual(self.demo.case_snapshot(self.actor, first['case_id']), old)
        self.assertEqual(next(i for i in old['items'] if i['observation']['raw_label'] == '配送手数料')['current_price'], '8000')
        with TestClient(create_app(helpers.DSN, self.storage, tenant_id=self.actor.tenant_id, integrations=self.runtime)) as client:
            self.assertEqual(client.get(f"/api/documents/{b['document_id']}/preview", params={'page': 1}).status_code, 200)
            exported = client.get(f"/api/cases/{case}/export.xlsx")
            self.assertEqual(exported.status_code, 200)
        book = load_workbook(BytesIO(exported.content), read_only=True)
        try:
            snapshot = self.demo.case_snapshot(self.actor, case)
            final_projection = comparison_projection(snapshot['items'], snapshot['fees'], snapshot['documents'])
            expected_main = [[r['display_name']] + [
                '\n'.join(f"{c['price']} {c['currency']}" for c in r['cells_by_forwarder'][company]) or '—'
                for company in final_projection['companies']] for r in final_projection['rows']]
            actual_main = list(book['原価単価一覧表'].values)
            width = 1 + len(final_projection['companies'])
            # openpyxl pads to the four-cell title row's worksheet width.
            self.assertTrue(all(value is None for row in actual_main[2:] for value in row[width:]))
            self.assertEqual(list(actual_main[2][:width]), ['統一費目'] + final_projection['companies'])
            self.assertEqual([list(row[:width]) for row in actual_main[3:]], expected_main)
            rows = list(book['明細と出典'].values)[1:]
            for quote in self.demo.list_items(self.actor, case):
                found = next(r for r in rows if r[0] == quote['item_id'])
                self.assertEqual(found[4:6], (quote['current_price'], quote['currency']))
        finally:
            book.close()
        out = os.environ.get('SDK18_EVIDENCE')
        if out:
            for name, actual_task in [('A', first_task), ('third', third_task)]:
                Path(out, name + '_SDK_OUTPUTS.json').write_text(json.dumps(
                    self.read_evidence(actual_task)['sdk_identity']['public_output_json'], ensure_ascii=False, indent=2))
            Path(out, 'B_PUBLIC_OUTPUT.json').write_text(evidence['sdk_identity']['public_output_json']['document_sdk.pdf.analyze_pdf'])
            Path(out, 'B_comparison.xlsx').write_bytes(exported.content)
            Path(out, 'business_proof.json').write_text(json.dumps(dict(
                table_state=table['availability'], cells=len(table['cells']), candidates=len(candidates),
                rows=len(projection['rows']), sdk_limitations=public['limitations'],
                corrected=refreshed, third=delivery, actual_external_ai_calls=0), ensure_ascii=False, indent=2, default=str))
