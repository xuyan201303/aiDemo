"""Deterministic ReadResult -> factual lines, shared by Mock and future real adapters.

Only reader-owned complete fields and explicit record/header applicability are
used. This never calls AI, searches page text for prices, or knows sample answers.
The accepted F1 materializer remains the final complete-value/ownership gate.
"""
from collections import defaultdict
from copy import deepcopy
from dataclasses import replace

from .extraction import CONDITIONS, complete_field_unit, materialize

FIELDS = ('raw_label', 'raw_value', 'currency', 'company', *CONDITIONS)
VERSION = 'complete-reader-fields-v1'


def structure_facts(document, tenant_id, reading):
    reading.validate(document)
    by_record, headers, labels, issues = defaultdict(lambda: defaultdict(list)), defaultdict(list), [], deepcopy(list(reading.issues))
    mapped = set()
    for block in reading.blocks:
        unit = block['source_ref'].get('evidence_unit')
        name = unit.get('field_name') if isinstance(unit, dict) else None
        if name not in FIELDS:
            issues.append({'code': 'EXTRACTION_UNRESOLVED', 'reason': 'FIELD_BOUNDARY_UNVERIFIED',
                           'source_block_id': block['source_id']})
            continue
        try:
            checked, record_key = complete_field_unit(block, name)
        except ValueError as exc:
            issues.append({'code': 'EXTRACTION_UNRESOLVED', 'reason': str(exc),
                           'source_block_id': block['source_id']})
            continue
        if checked['scope'] == 'DOCUMENT':
            for record in set(checked['applies_to_record_ids']):
                headers[(record, name)].append(block)
        else:
            by_record[record_key][name].append(block)
            if name == 'raw_label':
                labels.append((block, record_key, checked['record_id']))
    if not labels:
        return [], issues or [{"code": "EXTRACTION_UNRESOLVED", "reason": "NO_QUOTATION_LINES"}]
    entries = []
    for primary, key, record in labels:
        selected = {}
        ambiguous = False
        for name in FIELDS:
            candidates = by_record[key][name] + headers[(record, name)]
            if len(candidates) > 1:
                ambiguous = True
                issues.append({'code': 'EXTRACTION_UNRESOLVED', 'reason': 'FIELD_RECORD_AMBIGUOUS',
                               'source_block_id': primary['source_id'], 'field': name})
            selected[name] = ({'value': candidates[0]['raw_text'],
                               'source_block_id': candidates[0]['source_id']} if len(candidates) == 1
                              else {'value': None, 'source_block_id': None})
        if ambiguous:
            continue
        entry = {name: selected[name] for name in FIELDS[:4]}
        entry['context'] = {name: selected[name] for name in CONDITIONS}
        entries.append(entry)
        mapped.update(v['source_block_id'] for v in selected.values() if v['source_block_id'])
    # Do not silently drop fields whose record cannot be linked to a label.
    for fields in by_record.values():
        for blocks in fields.values():
            for block in blocks:
                if block['source_id'] not in mapped:
                    issues.append({'code': 'EXTRACTION_UNRESOLVED', 'reason': 'UNRESOLVED_RECORD',
                                   'source_block_id': block['source_id']})
    lines, validation = materialize(document, tenant_id, reading,
                                  {'lines': entries, 'document_issues': []})
    issues.extend(validation)
    revised = []
    for line in lines:
        ref = deepcopy(line.source_ref)
        ref['fact_materialization'] = VERSION
        ref['fact_sources'] = {name: reading.origin for name in
                              ('raw_label', 'amount', 'currency', 'source_ref')}
        ref['source_verification'] = ('DETERMINISTIC_FIELDS_MATCH_MOCK_ORIGINAL'
                                      if reading.origin == 'MOCK_SDK'
                                      else 'DETERMINISTIC_READER_FIELDS')
        revised.append(replace(line, source_ref=ref))
    # Bounds are explicit; no truncation of rows/evidence to pretend success.
    if len(issues) > 1000:
        return [], [{'code': 'EXTRACTION_UNRESOLVED', 'reason': 'ISSUE_LIMIT_EXCEEDED',
                     'issue_count': len(issues)}]
    return revised, issues
