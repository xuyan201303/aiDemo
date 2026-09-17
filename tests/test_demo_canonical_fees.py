"""P1 mechanism checks. Synthetic evidence and isolated test PostgreSQL only.

No real SDK calls, credentials, live database or OpenAI calls occur here. REAL
origin rows in these tests are explicit policy fixtures, never runtime evidence.
"""
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest import TestCase, skipUnless
from uuid import uuid4

import httpx
from jsonschema import Draft202012Validator
from sanzo_core.canonical_fees import (canonical_candidates, resolve_matches,
    MAX_CANDIDATES, MAX_CURRENT_SLOTS)
from sanzo_core.domain import CorrectionCommand, RevokeCommand, memory_key
from sanzo_demo.ai import ResponsesConfig, ResponsesExtractor, ExtractionError, ExtractionResult
from sanzo_demo.extraction import materialize
from sanzo_demo.ingestion import CLASSIFICATION_SCHEMA
from test_demo_evidence_grounding import field_fixture
from test_demo_ingestion_ai import envelope
import test_demo_ingestion_pipeline as pipeline


def policy_row(label='MECHANISM_DOC_LABEL', fee='fee-human', name='Human canonical', **changes):
    document, reading, payload = field_fixture(overrides={'raw_label': label})
    line = materialize(document, 'canonical-policy-test', reading, payload)[0][0]
    source = deepcopy(line.source_ref)
    source['extraction_origin'] = 'REAL_SDK'
    row = {'tenant_id': line.observation.tenant_id, 'origin': 'REAL_SDK',
        'observation': json.loads(line.observation.to_json()), 'source_ref': source,
        'input_kind': line.input_kind, 'status': 'ACTIVE', 'fee_id': fee,
        'display_name': name, 'human_confirmed': True, 'version': 1,
        'memory_key': memory_key(line.observation, source, line.input_kind)}
    row.update(changes)
    return row


class CanonicalPolicyTests(TestCase):
    def candidates(self, rows):
        return canonical_candidates(rows, 'canonical-policy-test', frozenset({'a' * 64}))

    def test_only_current_tenant_active_human_real_authorized_source(self):
        allowed = policy_row()
        rows = [allowed,
            policy_row('AI', 'ai', human_confirmed=False),
            policy_row('REVOKED', 'revoked', status='REVOKED'),
            policy_row('CONFLICTED', 'conflicted', status='CONFLICTED'),
            policy_row('MOCK', 'mock', origin='MOCK_SDK'),
            policy_row('OTHER', 'other', tenant_id='other-tenant')]
        unapproved = policy_row('PRIVATE', 'private')
        unapproved['source_ref']['document_sha256'] = 'b' * 64
        rows.append(unapproved)
        self.assertEqual(self.candidates(rows), [{'fee_id': 'fee-human', 'name': 'Human canonical'}])

    def test_direct_active_supersede_hides_old_legacy_fee(self):
        legacy = policy_row(fee='old', name='Old', memory_key='legacy-old')
        latest = policy_row(fee='new', name='New', version=2)
        self.assertEqual(self.candidates([legacy, latest]), [{'fee_id': 'new', 'name': 'New'}])

    def test_direct_conflict_revoke_shadows_old_legacy_active(self):
        for status in ('CONFLICTED', 'REVOKED'):
            with self.subTest(status=status):
                old = policy_row(memory_key='legacy-old')
                newest = policy_row(status=status, fee_id=None)
                self.assertEqual(self.candidates([old, newest]), [])

    def test_legacy_conflicting_active_targets_or_revoked_peer_excluded(self):
        first = policy_row(fee='one', memory_key='legacy-one')
        other = policy_row(fee='two', memory_key='legacy-two')
        self.assertEqual(self.candidates([first, other]), [])
        self.assertEqual(self.candidates([first, {**other, 'status': 'REVOKED'}]), [])
        self.assertEqual(self.candidates([first, {**other, 'fee_id': 'one'}]),
                         [{'fee_id': 'one', 'name': 'Human canonical'}])

    def test_unverified_source_never_supplies_candidate(self):
        row = policy_row()
        row['source_ref']['text_verification'] = 'UNVERIFIED'
        self.assertEqual(self.candidates([row]), [])

    def test_unverified_direct_shadow_never_reintroduces_legacy_or_new_name(self):
        old = policy_row(fee='old', memory_key='legacy-old')
        latest = policy_row(fee='new', name='Unverified new name')
        latest['source_ref']['text_verification'] = 'UNVERIFIED'
        self.assertEqual(self.candidates([old, latest]), [])

    def test_dedup_deterministic_bounded_and_oversize_scan_fail_closed(self):
        rows = [policy_row('LABEL_' + str(i), 'fee-' + str(i), 'HUMAN ' + str(i))
                for i in range(MAX_CANDIDATES + 2)]
        expected = self.candidates(rows)
        self.assertEqual(len(expected), MAX_CANDIDATES)
        self.assertEqual(self.candidates(list(reversed(rows))), expected)
        self.assertEqual(self.candidates([rows[0]] * (MAX_CURRENT_SLOTS + 1)), [])
        rows = [policy_row('A'), policy_row('B')]
        self.assertEqual(len(self.candidates(rows)), 1)

    def test_ids_must_be_offered_for_this_item_not_another_or_foreign(self):
        candidates = [{'fee_id': 'fee-one', 'name': 'Canonical'}]
        for offered, target in (({}, 'fee-one'), ({'another': candidates}, 'fee-one'),
                                ({'item': candidates}, 'foreign-fee')):
            with self.subTest(offered=offered, target=target):
                with self.assertRaisesRegex(ValueError, 'AI_CANONICAL_ID_NOT_OFFERED'):
                    resolve_matches([{'item_id': 'item', 'matched_fee_id': target,
                                      'suggested_fee_name': 'Drift'}], offered, candidates)

    def test_database_canonical_name_overrides_model_drift(self):
        candidates = [{'fee_id': 'fee-one', 'name': 'Stored human name'}]
        names, matches = resolve_matches([{'item_id': 'item', 'matched_fee_id': 'fee-one',
            'suggested_fee_name': 'Model drift'}], {'item': candidates}, candidates)
        self.assertEqual(names, {'item': 'Stored human name'})
        self.assertEqual(matches['item']['fee_id'], 'fee-one')

    def test_candidate_revoked_while_provider_working_is_rejected(self):
        candidates = [{'fee_id': 'fee-one', 'name': 'Canonical'}]
        with self.assertRaisesRegex(ValueError, 'AI_CANONICAL_CANDIDATE_NO_LONGER_ACTIVE'):
            resolve_matches([{'item_id': 'item', 'matched_fee_id': 'fee-one',
                'suggested_fee_name': 'Drift'}], {'item': candidates}, [])

    def test_no_match_may_suggest_new_name_or_null_neither_is_memory(self):
        names, matches = resolve_matches([
            {'item_id': 'one', 'matched_fee_id': None, 'suggested_fee_name': 'New fee'},
            {'item_id': 'two', 'matched_fee_id': None, 'suggested_fee_name': None}], {}, [])
        self.assertEqual(names, {'one': 'New fee', 'two': None})
        self.assertEqual(matches, {})

    def test_R1_F01_REAL_same_case_temporary_vocabulary_retains_scope_and_mode_filters(self):
        from test_demo_ingestion_pipeline import ComparisonCandidateBoundaryTests
        from sanzo_demo.ingestion import comparison_candidates
        def fixture():
            line, item, doc, allowed = ComparisonCandidateBoundaryTests().fixture()
            line.source_ref['extraction_origin'] = 'REAL_SDK'
            item['source_ref']['extraction_origin'] = 'REAL_SDK'
            return line, item, doc, allowed
        line, item, doc, allowed = fixture()
        self.assertEqual(len(comparison_candidates(line, [item], [doc], allowed)), 1)
        for defect in ('foreign_tenant', 'foreign_case', 'unauthorized_original', 'different_mode',
                       'conflicted', 'revoked', 'known_equipment_conflict'):
            line, item, doc, allowed = fixture()
            if defect == 'foreign_tenant': item['tenant_id'] = 'ANOTHER_TENANT'
            elif defect == 'foreign_case': item['case_id'] = 'ANOTHER_CASE'
            elif defect == 'unauthorized_original': allowed = frozenset({'a' * 64})
            elif defect == 'different_mode': item['source_ref']['extraction_origin'] = 'MECHANISM_TEST'
            elif defect == 'conflicted': item['current_memory_status'] = 'CONFLICTED'
            elif defect == 'revoked': item['current_memory_status'] = 'REVOKED'
            else:
                item['observation']['context']['equipment'] = '40FT'
                item['source_ref']['field_sources']['equipment'].update(value='40FT', original_value='40FT')
            with self.subTest(defect=defect):
                self.assertEqual(comparison_candidates(line, [item], [doc], allowed), [])

    def test_R1_F01_prompt_prioritizes_canonical_then_temporary_then_new_or_null(self):
        from sanzo_demo.ai import _PROMPTS
        prompt = _PROMPTS['fee_classification']
        self.assertIn('Suitable HUMAN canonical candidates have first priority', prompt)
        self.assertIn('If no suitable HUMAN canonical candidate fits, comparison_candidates', prompt)
        self.assertIn('reuse the exact wording unchanged, with matched_fee_id=null', prompt)
        self.assertIn('If neither source fits', prompt)
        self.assertIn('Never pick the first candidate just because of order', prompt)
        self.assertIn('no canonical identity, confirmation or Memory', prompt)

    def test_schema_requires_nullable_identity_and_forbids_facts(self):
        validator = Draft202012Validator(CLASSIFICATION_SCHEMA)
        valid = {'suggestions': [{'item_id': 'one', 'matched_fee_id': None, 'suggested_fee_name': 'New'}]}
        self.assertTrue(validator.is_valid(valid))
        for field in ('amount', 'price', 'unit_price', 'currency', 'source_ref'):
            invalid = deepcopy(valid); invalid['suggestions'][0][field] = 'not allowed'
            self.assertFalse(validator.is_valid(invalid))
        del valid['suggestions'][0]['matched_fee_id']
        self.assertFalse(validator.is_valid(valid))

    def test_http_serialized_input_has_only_unknown_semantics_and_canonical_identity(self):
        candidates = [{'fee_id': 'human-one', 'name': 'Stored human name'}]
        block = {'item_id': 'unknown-only', 'raw_label': 'NEW RAW LABEL', 'context': {'equipment': '20FT'},
                 'canonical_candidates': candidates}
        calls = []
        def handler(request):
            body = json.loads(request.content)
            semantic = json.loads(body['input'][1]['content'])['blocks']
            self.assertEqual(semantic, [block])
            forbidden = {'amount', 'price', 'currency', 'source_ref', 'unit_price'}
            def check(value):
                if isinstance(value, dict):
                    self.assertFalse(forbidden & set(value))
                    for child in value.values(): check(child)
                elif isinstance(value, list):
                    for child in value: check(child)
            check(semantic)
            self.assertEqual(body['text']['format']['schema'], CLASSIFICATION_SCHEMA)
            calls.append(body['metadata']['purpose'])
            return httpx.Response(200, json=envelope(json.dumps({'suggestions': [{
                'item_id': 'unknown-only', 'matched_fee_id': 'human-one',
                'suggested_fee_name': 'Drift'}]})))
        extractor = ResponsesExtractor(ResponsesConfig('mechanism-only', frozenset({'a' * 64}), max_requests=1),
            lambda: 'MECHANISM_ONLY_NOT_A_KEY', transport=httpx.MockTransport(handler))
        extractor.extract('a' * 64, [block], CLASSIFICATION_SCHEMA, purpose='fee_classification')
        self.assertEqual(calls, ['fee_classification'])
        self.assertEqual(extractor.requests_used, 1)  # Local substitute count, no real call.

    def test_candidate_facts_rejected_before_credentials_or_budget(self):
        touched = []
        extractor = ResponsesExtractor(ResponsesConfig('mechanism-only', frozenset({'a' * 64}), max_requests=1),
            lambda: touched.append(True), transport=httpx.MockTransport(lambda _: None))
        for field in ('amount', 'price', 'currency', 'source_ref', 'unit_price'):
            block = {'item_id': 'unknown', 'raw_label': 'RAW', 'context': {},
                'canonical_candidates': [{'fee_id': 'one', 'name': 'Human', field: 'FORBIDDEN'}]}
            with self.assertRaises(ExtractionError) as failure:
                extractor.extract('a' * 64, [block], CLASSIFICATION_SCHEMA, purpose='fee_classification')
            self.assertEqual(failure.exception.code, 'AI_CLASSIFICATION_INPUT_INVALID')
        self.assertEqual(touched, [])
        self.assertEqual(extractor.requests_used, 0)


@skipUnless(pipeline.DSN, 'NOT_RUN: dedicated disposable PostgreSQL required')
class CanonicalPostgresTests(TestCase):
    setUpClass = classmethod(pipeline.IngestionPipelinePostgresTests.setUpClass.__func__)
    setUp = pipeline.IngestionPipelinePostgresTests.setUp
    run_document = pipeline.IngestionPipelinePostgresTests.run_document
    rows = pipeline.IngestionPipelinePostgresTests.rows

    def doc(self, label, price='10', *, case_id=None, company=None):
        document = pipeline.IngestionPipelinePostgresTests.document(self, price=price, case_id=case_id)
        reading = self.readings[document['sha256']]
        blocks = deepcopy(reading.blocks)
        for block in blocks:
            block['source_ref']['extraction_origin'] = 'REAL_SDK'
            if block['source_ref']['evidence_unit']['field_name'] == 'raw_label':
                block['raw_text'] = label
            if company and block['source_ref']['evidence_unit']['field_name'] == 'company':
                block['raw_text'] = company
        # Explicit synthetic REAL-policy fixture, never evidence of a real SDK run.
        self.readings[document['sha256']] = replace(reading, blocks=blocks, origin='REAL_SDK')
        return document

    def classifier(self, response):
        owner = self
        class FakeClassifier:
            def __init__(self):
                self.config = SimpleNamespace(authorized_sample_sha256=frozenset(owner.readings))
                self.calls = []
            def extract(self, digest, blocks, schema, *, purpose):
                self.calls.append(deepcopy(blocks))
                return ExtractionResult({'suggestions': [response(block) for block in blocks]},
                    {'execution_kind': 'MECHANISM_TEST', 'actual_api_calls': 0, 'purpose': purpose})
        client = FakeClassifier(); self.runtime.extractor = client
        return client

    def confirm(self, document, name):
        item = self.rows(document)[0]
        response = self.service.correct(self.actor, CorrectionCommand(item_id=item['item_id'],
            expected_case_revision=item['case_revision'], expected_item_version=item['version'],
            expected_memory_version=item['current_memory_version'], idempotency_key=uuid4().hex,
            new_fee_name=name))
        self.assertEqual(response['memory_effect'], 'ACTIVE')
        return response

    def seed(self):
        document = self.doc('FIRST RAW LABEL')
        self.classifier(lambda b: {'item_id': b['item_id'], 'matched_fee_id': None, 'suggested_fee_name': 'AI ONLY'})
        self.assertEqual(self.run_document(document)['state'], 'REVIEW_REQUIRED')
        response = self.confirm(document, 'Stored canonical name')
        return document, response

    def counts(self):
        with self.repo.transaction() as conn:
            return {table: conn.execute('SELECT count(*) AS n FROM sanzo_core.' + table + ' WHERE tenant_id=%s',
                (self.actor.tenant_id,)).fetchone()['n'] for table in ('memory_slots', 'memory_versions', 'memory_events')}

    def cold_same_case(self, first_company, second_company):
        # Explicit mechanism REAL-policy fixtures, not a live SDK/AI rehearsal.
        first = self.doc('RAW ' + first_company, company=first_company)
        self.classifier(lambda b: {'item_id': b['item_id'], 'matched_fee_id': None,
                                  'suggested_fee_name': 'First unconfirmed wording'})
        self.assertEqual(self.run_document(first)['state'], 'REVIEW_REQUIRED')
        second = self.doc('RAW ' + second_company, price='20', case_id=first['case_id'], company=second_company)
        classifier = self.classifier(lambda b: {'item_id': b['item_id'], 'matched_fee_id': None,
                                  'suggested_fee_name': 'Second unconfirmed wording'})
        result = self.run_document(second)
        self.assertEqual(result['state'], 'REVIEW_REQUIRED', result)
        request = classifier.calls[0][0]
        self.assertNotIn('canonical_candidates', request)
        self.assertIn('comparison_candidates', request)
        self.assertEqual([v['name'] for v in request['comparison_candidates']], ['First unconfirmed wording'])
        self.assertTrue(all(set(v) == {'name', 'context'} for v in request['comparison_candidates']))
        items = self.rows(second)
        second_item = next(i for i in items if i['observation']['document_id'] == second['document_id'])
        self.assertEqual(second_item['current_price'], '20')
        self.assertIsNone(second_item['current_fee_id'])
        self.assertIsNone(second_item['ai_matched_fee_id'])
        self.assertEqual(second_item['classification_source'], 'AI')
        self.assertEqual(self.demo.list_fees(self.actor), [])
        self.assertEqual(self.counts(), {'memory_slots': 0, 'memory_versions': 0, 'memory_events': 0})
        meta = result['result_json']['ai_metadata']
        self.assertEqual(meta['quotation_extraction_requests'], 0)
        self.assertEqual(meta['canonical_candidate_ids'], {second_item['item_id']: []})
        self.assertEqual(meta['comparison_candidate_counts'], {second_item['item_id']: 1})
        self.assertEqual(len(classifier.calls), 1)
        for forbidden in ('amount', 'price', 'currency', 'unit_price', 'source_ref'):
            self.assertNotIn('"' + forbidden + '"', json.dumps(classifier.calls))

    def test_R1_F01_cold_REAL_A_then_B_retains_same_case_vocabulary_without_canonical(self):
        self.cold_same_case('MECHANISM_FORWARDER_A', 'MECHANISM_FORWARDER_B')

    def test_R1_F01_cold_REAL_B_then_A_retains_same_case_vocabulary_without_canonical(self):
        self.cold_same_case('MECHANISM_FORWARDER_B', 'MECHANISM_FORWARDER_A')

    def test_R1_F01_temporary_vocabulary_cannot_grant_matched_fee_id(self):
        first = self.doc('FIRST RAW')
        self.classifier(lambda b: {'item_id': b['item_id'], 'matched_fee_id': None,
                                  'suggested_fee_name': 'Unconfirmed temporary wording'})
        self.run_document(first)
        second = self.doc('SECOND RAW', case_id=first['case_id'])
        classifier = self.classifier(lambda b: {'item_id': b['item_id'], 'matched_fee_id': 'Unconfirmed temporary wording',
                                                'suggested_fee_name': 'Unconfirmed temporary wording'})
        result = self.run_document(second)
        self.assertIn('comparison_candidates', classifier.calls[0][0])
        self.assertNotIn('canonical_candidates', classifier.calls[0][0])
        self.assertEqual((result['state'], result['error_code']), ('FAILED', 'AI_CANONICAL_ID_NOT_OFFERED'))
        self.assertEqual(len(self.rows(second)), 1)  # Only the first document's saved item.
        self.assertEqual(self.counts(), {'memory_slots': 0, 'memory_versions': 0, 'memory_events': 0})

    def test_R1_F01_canonical_identity_wins_over_temporary_wording(self):
        learned, saved = self.seed()
        first = self.doc('UNCONFIRMED FIRST RAW')
        self.classifier(lambda b: {'item_id': b['item_id'], 'matched_fee_id': None,
                                  'suggested_fee_name': 'Temporary alternative wording'})
        self.run_document(first)
        second = self.doc('UNCONFIRMED SECOND RAW', case_id=first['case_id'])
        before = self.counts()
        classifier = self.classifier(lambda b: {'item_id': b['item_id'], 'matched_fee_id': saved['fee_id'],
                                  'suggested_fee_name': 'Temporary alternative wording'})
        result = self.run_document(second)
        self.assertEqual(result['state'], 'REVIEW_REQUIRED', result)
        request = classifier.calls[0][0]
        self.assertEqual(request['canonical_candidates'], [{'fee_id': saved['fee_id'], 'name': 'Stored canonical name'}])
        self.assertEqual([v['name'] for v in request['comparison_candidates']], ['Temporary alternative wording'])
        item = next(i for i in self.rows(second) if i['observation']['document_id'] == second['document_id'])
        self.assertEqual(item['ai_suggested_name'], 'Stored canonical name')
        self.assertEqual(item['ai_matched_fee_id'], saved['fee_id'])
        self.assertIsNone(item['current_fee_id'])
        self.assertEqual(item['classification_source'], 'AI')
        self.assertEqual(self.counts(), before)

    def test_cross_case_candidate_database_name_keeps_ai_unconfirmed_no_memory_or_fact_change(self):
        first, saved = self.seed()
        second = self.doc('DIFFERENT UNKNOWN LABEL', price='9500')
        before = self.counts()
        classifier = self.classifier(lambda b: {'item_id': b['item_id'],
            'matched_fee_id': b['canonical_candidates'][0]['fee_id'], 'suggested_fee_name': 'Wording drift'})
        result = self.run_document(second)
        self.assertEqual(result['state'], 'REVIEW_REQUIRED', result)
        item = self.rows(second)[0]
        self.assertEqual(item['ai_suggested_name'], 'Stored canonical name')
        self.assertEqual(item['ai_matched_fee_id'], saved['fee_id'])
        self.assertIsNone(item['current_fee_id'])
        self.assertEqual((item['current_price'], item['currency'], item['classification_source']), ('9500', 'JPY', 'AI'))
        self.assertEqual(self.counts(), before)
        self.assertEqual(len(classifier.calls), 1)
        self.assertEqual(classifier.calls[0][0]['canonical_candidates'],
                         [{'fee_id': saved['fee_id'], 'name': 'Stored canonical name'}])
        self.assertNotEqual(first['case_id'], second['case_id'])
        self.assertEqual(result['result_json']['ai_metadata']['quotation_extraction_requests'], 0)

    def test_memory_hit_bypasses_catalog_and_classifier_new_price_remains(self):
        first, saved = self.seed()
        second = self.doc('FIRST RAW LABEL', price='9500')
        calls = self.classifier(lambda b: self.fail('AI must not run for a hit'))
        from unittest.mock import patch
        with patch.object(self.repo, 'canonical_fee_candidates', side_effect=AssertionError('No catalog lookup needed')):
            result = self.run_document(second)
        self.assertEqual(result['state'], 'AVAILABLE', result)
        self.assertEqual(calls.calls, [])
        item = self.rows(second)[0]
        self.assertEqual((item['current_fee_id'], item['current_price'], item['classification_source']),
                         (saved['fee_id'], '9500', 'MEMORY'))
        self.assertEqual(self.rows(first)[0]['current_price'], '10')

    def test_unsent_id_rejected_no_items_no_new_memory(self):
        self.seed(); second = self.doc('UNKNOWN SECOND')
        before = self.counts()
        self.classifier(lambda b: {'item_id': b['item_id'], 'matched_fee_id': 'NOT_OFFERED',
                                  'suggested_fee_name': 'Pretend canonical'})
        result = self.run_document(second)
        self.assertEqual((result['state'], result['error_code']), ('FAILED', 'AI_CANONICAL_ID_NOT_OFFERED'))
        self.assertEqual(self.rows(second), [])
        self.assertEqual(self.counts(), before)

    def test_revoked_after_provider_response_before_finish_is_not_persisted(self):
        first, saved = self.seed()
        second = self.doc('DIFFERENT UNKNOWN LABEL', price='9500')
        self.classifier(lambda b: {'item_id': b['item_id'],
            'matched_fee_id': b['canonical_candidates'][0]['fee_id'], 'suggested_fee_name': 'Drift'})
        transition = self.runtime.store.transition
        def revoke_before_finish(actor, task_id, status, **kwargs):
            result = transition(actor, task_id, status, **kwargs)
            if status == 'SAVING':
                item = self.rows(first)[0]
                self.service.revoke_memory(self.actor, RevokeCommand(item['item_id'],
                    item['case_revision'], item['version'], item['current_memory_version'],
                    uuid4().hex, 'Explicit test revocation between response and finish'))
            return result
        from unittest.mock import patch
        with patch.object(self.runtime.store, 'transition', side_effect=revoke_before_finish):
            result = self.run_document(second)
        self.assertEqual((result['state'], result['error_code']),
                         ('FAILED', 'AI_CANONICAL_CANDIDATE_NO_LONGER_ACTIVE'))
        self.assertEqual(self.rows(second), [])
        self.assertEqual(self.rows(first)[0]['current_price'], '10')
        self.assertEqual(self.counts(), {'memory_slots': 1, 'memory_versions': 2, 'memory_events': 2})

    def test_renamed_after_ai_response_before_finish_cannot_restore_old_canonical_name(self):
        from sanzo_demo.row_confirmation import RowConfirmationCommand, RowConfirmationService, RowMember
        from sanzo_demo.comparison import comparison_projection
        from unittest.mock import patch
        first, saved = self.seed()
        second = self.doc('UNKNOWN AFTER OLD CANONICAL RESPONSE', price='9500')
        self.classifier(lambda b: {'item_id': b['item_id'],
            'matched_fee_id': b['canonical_candidates'][0]['fee_id'], 'suggested_fee_name': 'Drift'})
        transition = self.runtime.store.transition
        before = self.counts()
        def rename_before_finish(actor, task_id, status, **kwargs):
            result = transition(actor, task_id, status, **kwargs)
            if status == 'SAVING':
                item = self.rows(first)[0]
                row = comparison_projection([item])['rows'][0]
                RowConfirmationService(self.repo).confirm(self.actor, RowConfirmationCommand(
                    case_id=item['case_id'], row_id=row['row_id'], expected_case_revision=item['case_revision'],
                    members=(RowMember(item['item_id'], item['version'], item['current_memory_version']),),
                    idempotency_key=uuid4().hex, new_fee_name='Newest server canonical name',
                    intent='RENAME_FEE', expected_fee_id=saved['fee_id'], expected_name_version=1))
            return result
        with patch.object(self.runtime.store, 'transition', side_effect=rename_before_finish):
            result = self.run_document(second)
        self.assertEqual((result['state'], result['error_code']), ('FAILED', 'AI_CANONICAL_CANDIDATE_NO_LONGER_ACTIVE'))
        self.assertEqual(self.rows(second), [])
        self.assertEqual(self.rows(first)[0]['current_fee_name'], 'Newest server canonical name')
        self.assertEqual(self.rows(first)[0]['current_fee_id'], saved['fee_id'])
        self.assertEqual(self.counts(), before)

    def test_new_free_suggestion_and_ai_only_prior_case_do_not_make_catalog_or_memory(self):
        first = self.doc('UNKNOWN FIRST')
        classifier = self.classifier(lambda b: {'item_id': b['item_id'], 'matched_fee_id': None,
                                                'suggested_fee_name': 'AI ONLY'})
        self.run_document(first)
        second = self.doc('UNKNOWN SECOND')
        classifier = self.classifier(lambda b: {'item_id': b['item_id'], 'matched_fee_id': None,
                                                'suggested_fee_name': 'NEW FREE NAME'})
        result = self.run_document(second)
        self.assertEqual(result['state'], 'REVIEW_REQUIRED')
        self.assertNotIn('canonical_candidates', classifier.calls[0][0])
        self.assertEqual(self.counts(), {'memory_slots': 0, 'memory_versions': 0, 'memory_events': 0})
        self.assertEqual(self.rows(second)[0]['ai_suggested_name'], 'NEW FREE NAME')
