"""A-E: real isolated PostgreSQL with explicitly synthetic reader/HTTP substitutes.

No real OpenAI credentials or API calls. The live browser rehearsal is separate.
"""
from copy import deepcopy
from dataclasses import replace
import json
import unittest
from uuid import uuid4
import httpx
from fastapi.testclient import TestClient
from sanzo_core.ai_budget import PostgresAIBudget
from sanzo_core.domain import CorrectionCommand
from sanzo_demo.ai import ResponsesConfig, ResponsesExtractor, ExtractionResult
from sanzo_demo.app import create_app
from sanzo_demo.ingestion import IntegrationRuntime
import test_demo_ingestion_pipeline as pipeline
from test_demo_ingestion_ai import envelope

@unittest.skipUnless(pipeline.DSN, 'NOT_RUN: isolated PostgreSQL required')
class MemoryFirstPostgresTests(unittest.TestCase):
    setUpClass = classmethod(lambda cls: pipeline.PostgresRepository(pipeline.DSN).migrate())
    setUp = pipeline.IngestionPipelinePostgresTests.setUp
    document = pipeline.IngestionPipelinePostgresTests.document
    rows = pipeline.IngestionPipelinePostgresTests.rows
    run_document = pipeline.IngestionPipelinePostgresTests.run_document

    def five(self, price='8000'):
        doc = self.document(price=price)
        reading = self.readings[doc['sha256']]
        blocks = []
        for index in range(5):
            for original in reading.blocks:
                b = deepcopy(original)
                b['source_id'] += '_'+str(index)
                ref = b['source_ref']; ref['source_block_ids'] = [b['source_id']]
                ref['evidence_unit']['record_id'] += '_'+str(index)
                if ref['evidence_unit']['field_name'] == 'raw_label':
                    b['raw_text'] = '配送手数料' if index == 0 else 'SYNTHETIC_UNKNOWN_'+str(index)
                blocks.append(b)
        self.readings[doc['sha256']] = replace(reading, blocks=tuple(blocks))
        return doc

    def learn(self, count):
        doc = self.five()
        self.assertEqual(self.run_document(doc)['state'], 'REVIEW_REQUIRED')
        original = self.rows(doc)
        self.assertEqual(len(original), 5)
        for item in original[:count]:
            fresh = next(i for i in self.rows(doc) if i['item_id'] == item['item_id'])
            self.service.correct(self.actor, CorrectionCommand(
                item_id=fresh['item_id'], expected_case_revision=fresh['case_revision'],
                expected_item_version=fresh['version'], expected_memory_version=fresh['current_memory_version'],
                idempotency_key=uuid4().hex, new_fee_name='配送費' if fresh['observation']['raw_label']=='配送手数料' else 'HUMAN_'+fresh['observation']['raw_label']))
        return doc

    def test_A_all_five_hits_zero_ai_even_without_client(self):
        self.learn(5)
        second = self.five('9500')
        self.extractor.calls.clear()
        self.runtime.extractor = None
        task = self.run_document(second)
        self.assertEqual(task['state'], 'AVAILABLE')
        self.assertEqual(self.extractor.calls, [])
        self.assertEqual(task['result_json']['ai_metadata']['calls'], [])
        self.assertTrue(all(i['classification_source']=='MEMORY' for i in self.rows(second)))

    def test_B_three_hits_two_unknowns_one_price_free_batch(self):
        self.learn(3)
        second = self.five('9500'); self.extractor.calls.clear()
        task = self.run_document(second)
        items = self.rows(second)
        unknown = {i['item_id'] for i in items if i['classification_source']=='AI'}
        known = {i['item_id'] for i in items if i['classification_source']=='MEMORY'}
        self.assertEqual((len(known),len(unknown)), (3,2))
        self.assertEqual(len(self.extractor.calls), 1)
        call = self.extractor.calls[0]
        self.assertEqual(call['purpose'], 'fee_classification')
        self.assertEqual({i['item_id'] for i in call['blocks']}, unknown)
        self.assertTrue(all(set(i)=={'item_id','raw_label','context'} for i in call['blocks']))
        self.assertNotIn('9500', json.dumps(call['blocks']))
        metadata=task['result_json']['ai_metadata']
        self.assertEqual(set(metadata['classification_skipped_item_ids']), known)
        self.assertTrue(all(metadata['memory_lookup_results'][i]['decision']=='MEMORY_APPLIED' for i in known))

    def test_C_second_delivery_9500_memory_first_old_8000_unchanged(self):
        first = self.learn(5)
        before = self.demo.case_snapshot(self.actor, first['case_id'])
        second = self.five('9500'); self.extractor.calls.clear()
        self.run_document(second)
        item = next(i for i in self.rows(second) if i['observation']['raw_label']=='配送手数料')
        self.assertEqual((item['current_fee_name'], item['current_price'], item['currency']), ('配送費','9500','JPY'))
        self.assertEqual(item['classification_source'], 'MEMORY')
        self.assertEqual(item['fact_sources']['amount'], 'MECHANISM_TEST')
        self.assertEqual(item['source_ref']['field_sources']['raw_value']['original_value'], '9500')
        self.assertEqual(item['source_ref']['document_sha256'], second['sha256'])
        self.assertEqual(self.extractor.calls, [])
        self.assertEqual(self.demo.case_snapshot(self.actor, first['case_id']), before)

    def test_D_real_client_schema_rejects_ai_price_injection(self):
        doc = self.five('9500')
        def handler(request):
            payload = json.loads(request.content)
            values = json.loads(payload['input'][1]['content'])
            # Inspect the actual HTTP boundary, with no price facts in its input.
            self.assertNotIn('9500', json.dumps(values))
            rows = values['blocks'] if isinstance(values, dict) else values
            response = {'suggestions':[{'item_id':i['item_id'],'matched_fee_id':None,'suggested_fee_name':'UNCONFIRMED','amount':'9000'} for i in rows]}
            return httpx.Response(200, json=envelope(json.dumps(response)))
        self.runtime.extractor = ResponsesExtractor(
            ResponsesConfig('explicit-test-model', frozenset({doc['sha256']}),max_requests=1),
            lambda:'MECHANISM_ONLY_NOT_A_KEY', transport=httpx.MockTransport(handler))
        task=self.run_document(doc)
        self.assertEqual(task['state'],'FAILED',task)
        self.assertEqual(task['error_code'],'AI_OUTPUT_SCHEMA_INVALID',task)
        self.assertEqual(self.rows(doc),[])
        reading=next(e['metadata']['read_evidence'] for e in task['events'] if e['state']=='EXTRACTING')
        self.assertEqual({b['raw_text'] for b in reading['sources'] if b['source_ref']['evidence_unit']['field_name']=='raw_value'}, {'9500'})
        self.assertEqual(self.runtime.extractor.requests_used,1)

    def test_E_entire_http_process_zero_requests_and_no_credential_read_at_zero_budget(self):
        self.learn(5); doc=self.five('9500')
        hashes=frozenset({doc['sha256']});grant='MECHANISM_'+uuid4().hex
        budget=PostgresAIBudget(self.repo,grant,model='explicit-test-model',max_requests=0,authorized_sample_sha256=hashes)
        before=budget.snapshot()
        extractor=ResponsesExtractor(ResponsesConfig('explicit-test-model',hashes,max_requests=0),
            lambda:self.fail('No credential access for a memory-only file'),
            budget=budget,transport=httpx.MockTransport(lambda r:self.fail('No HTTP requests')))
        runtime=IntegrationRuntime(self.repo,self.storage,reader=self.reader,extractor=extractor,allow_mechanism_tests=True)
        with TestClient(create_app(pipeline.DSN,self.storage,tenant_id=self.actor.tenant_id,integrations=runtime)) as client:
            response=client.post('/api/documents/'+doc['document_id']+'/process',json={'idempotency_key':uuid4().hex})
            self.assertEqual(response.status_code,202)
            task=client.get('/api/tasks/'+response.json()['task_id']).json()
        self.assertEqual(task['state'],'AVAILABLE',task)
        self.assertEqual(task['result_json']['ai_metadata']['quotation_extraction_requests'],0)
        self.assertEqual(task['result_json']['ai_metadata']['calls'],[])
        reopened=PostgresAIBudget(pipeline.PostgresRepository(pipeline.DSN),grant,model='explicit-test-model',max_requests=0,authorized_sample_sha256=hashes)
        self.assertEqual(reopened.snapshot(),before)
