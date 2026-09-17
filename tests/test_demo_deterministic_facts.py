"""Offline reader-fact contract. No AI, SDK repository or network access."""
from copy import deepcopy
from dataclasses import replace
import unittest
from sanzo_demo.facts import structure_facts
from sanzo_core.domain import memory_key
from test_demo_evidence_grounding import field_fixture, changed_block

class DeterministicFactsTests(unittest.TestCase):
    def read(self, **kwargs):
        doc, reading, _ = field_fixture(**kwargs)
        return doc, reading

    def test_complete_fields_without_model_and_full_currency_affixes(self):
        for kind in ('PDF', 'EXCEL'):
            for amount in ('9500', '9,500', 'JPY 9,500', '9,500円'):
                with self.subTest(kind=kind, amount=amount):
                    doc, reading = self.read(kind=kind, amount=amount)
                    lines, issues = structure_facts(doc, 'FACT_TEST', reading)
                    self.assertEqual(issues, [])
                    self.assertEqual(len(lines), 1)
                    item = lines[0]
                    self.assertEqual(item.observation.unit_price, '9500')
                    self.assertEqual(item.observation.raw_label, '配送手数料')
                    self.assertEqual(item.raw_value, amount)
                    self.assertIsNone(item.ai_suggested_name)
                    self.assertEqual(item.source_ref['fact_sources']['amount'], 'MECHANISM_TEST')

    def test_unbounded_page_is_unresolved_not_sent_for_extraction(self):
        doc, reading = self.read()
        blocks = deepcopy(reading.blocks)
        for b in blocks: b['source_ref'].pop('evidence_unit')
        lines, issues = structure_facts(doc, 'FACT_TEST', replace(reading, blocks=blocks))
        self.assertFalse(lines)
        self.assertTrue(issues)
        self.assertTrue(all(i['code'] == 'EXTRACTION_UNRESOLVED' for i in issues))

    def test_other_record_same_amount_cannot_supply_missing_price(self):
        doc, reading = self.read(amount='9500')
        reading = changed_block(reading, 'raw_value', lambda b:
            b['source_ref']['evidence_unit'].update(record_id='OTHER_RECORD'))
        lines, issues = structure_facts(doc, 'FACT_TEST', reading)
        self.assertFalse(lines)
        self.assertTrue(issues)

    def test_duplicate_or_conflicting_field_is_not_arbitrarily_selected(self):
        for field in ('raw_value', 'raw_label', 'equipment'):
            doc, reading = self.read()
            b = deepcopy(next(b for b in reading.blocks if b['source_ref']['evidence_unit']['field_name'] == field))
            b['source_id'] += '_DUP'; b['source_ref']['source_block_ids'] = [b['source_id']]
            lines, issues = structure_facts(doc, 'FACT_TEST', replace(reading, blocks=(*reading.blocks, b)))
            self.assertFalse(lines)
            self.assertTrue(issues)

    def test_missing_condition_stays_unknown_without_learning(self):
        doc, reading = self.read()
        reading = replace(reading, blocks=tuple(b for b in reading.blocks if b['source_ref']['evidence_unit']['field_name'] != 'transport_mode'))
        lines, issues = structure_facts(doc, 'FACT_TEST', reading)
        self.assertEqual(len(lines), 1)
        self.assertIsNone(lines[0].observation.context.transport_mode)
        self.assertIsNone(memory_key(lines[0].observation, lines[0].source_ref, lines[0].input_kind))
        self.assertTrue(issues)

    def test_unknown_amount_or_currency_not_generated(self):
        for field, raw in (('raw_value', '未定'), ('raw_value', '9500 or 9000'), ('currency', '不明')):
            doc, reading = self.read()
            reading = changed_block(reading, field, lambda b: b.update(raw_text=raw))
            lines, issues = structure_facts(doc, 'FACT_TEST', reading)
            self.assertFalse(lines)
            self.assertTrue(issues)
