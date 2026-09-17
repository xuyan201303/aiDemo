"""Fixed-wheel reader using public SDK facts only, with no AI or Mock fallback.

Header binding is an application rule. SDK values never become fee categories.
Unbound context stays unknown; complete-field and same-record F1 gates still run
in structure_facts. Public SDK serializers are retained before interpretation.
"""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
from importlib import metadata
import json
import os
from pathlib import Path
import re
import sys
from zipfile import ZipFile

from .extraction import ReadResult

WHEEL_FILENAME = 'document_processing_sdk-1.8.0-py3-none-any.whl'
WHEEL_SHA256 = 'd791e0602ef138f50a065cd4cc5d3dca801226822b74328230f2faa2c0c75666'
DEFAULT_WHEEL = Path('/Users/kyoen/Projects/sdk') / WHEEL_FILENAME
VERSION = 'document-sdk-full-header-v1'
# Exact complete header labels, not quote labels or expected answers.
HEADERS = {
    '費目表記': 'raw_label', 'DESCRIPTION / 費目表記': 'raw_label',
    '見積単価': 'raw_value', '単価': 'raw_value', '通貨': 'currency',
    '適用条件': 'equipment', '条件': 'equipment',
    '課金単位': 'billing_unit', '単位': 'billing_unit',
    'サービス範囲': 'charge_scope', '付帯条件': 'extra_terms',
    '会社名': 'company', 'フォワーダー': 'company',
    '輸送モード': 'transport_mode',
}
CORE = frozenset(('raw_label', 'raw_value', 'currency'))
ALL_FIELDS = frozenset(HEADERS.values())


def _digest(path, maximum):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('REAL_SDK_PATH_UNSAFE_OR_MISSING')
    result, size = sha256(), 0
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > maximum:
                raise ValueError('REAL_SDK_FILE_SIZE_LIMIT')
            result.update(chunk)
    if not size:
        raise ValueError('REAL_SDK_EMPTY_FILE')
    return result.hexdigest()


def verify_installation(wheel_path):
    """Verify fixed artifact and installed package bytes before importing SDK."""
    if sys.version_info[:2] != (3, 12):
        raise ValueError('REAL_SDK_PYTHON_3_12_REQUIRED')
    wheel = Path(wheel_path)
    if wheel.name != WHEEL_FILENAME or _digest(wheel, 64 * 1024 * 1024) != WHEEL_SHA256:
        raise ValueError('REAL_SDK_FIXED_WHEEL_HASH_MISMATCH')
    try:
        distribution = metadata.distribution('document-processing-sdk')
    except metadata.PackageNotFoundError as exc:
        raise ValueError('REAL_SDK_NOT_INSTALLED') from exc
    if distribution.version != '1.8.0':
        raise ValueError('REAL_SDK_VERSION_MISMATCH')
    installed = Path(distribution.locate_file('document_sdk')).resolve()
    if not installed.is_relative_to(Path(sys.prefix).resolve()):
        raise ValueError('REAL_SDK_ISOLATED_INSTALL_REQUIRED')
    checked = 0
    with ZipFile(wheel) as archive:
        for member in archive.infolist():
            if member.is_dir() or not member.filename.startswith('document_sdk/'):
                continue
            target = Path(distribution.locate_file(member.filename))
            if (target.is_symlink() or not target.resolve().is_relative_to(installed)
                or not target.is_file() or target.read_bytes() != archive.read(member)):
                raise ValueError('REAL_SDK_INSTALLED_FILE_MISMATCH')
            checked += 1
    if not checked:
        raise ValueError('REAL_SDK_INSTALLED_FILE_MISMATCH')
    return {'distribution': 'document-processing-sdk', 'version': distribution.version,
            'wheel_filename': wheel.name, 'wheel_sha256': WHEEL_SHA256,
            'installed_file_check': 'VERIFIED', 'installed_files_checked': checked,
            'python_version': '.'.join(map(str, sys.version_info[:3]))}


def _coordinate(value):
    match = re.fullmatch(r'([A-Z]{1,3})([1-9][0-9]{0,6})', value or '')
    if not match:
        raise ValueError('REAL_SDK_INVALID_CELL_COORDINATE')
    column = 0
    for char in match[1]:
        column = column * 26 + ord(char) - 64
    return int(match[2]), column


def _merged(cell, ranges):
    row, column = _coordinate(cell)
    for value in ranges:
        start, _, end = value.partition(':')
        r1, c1 = _coordinate(start)
        r2, c2 = _coordinate(end or start)
        if r1 <= row <= r2 and c1 <= column <= c2 and (r1, c1) != (r2, c2):
            return True
    return False


def _header(rows):
    """Require one unambiguous full core-header row in a sheet/table."""
    candidates = []
    for index, cells in rows.items():
        roles = defaultdict(list)
        for column, cell in cells.items():
            raw = cell.get('value') if 'value' in cell else cell.get('text')
            if isinstance(raw, str) and raw.strip() in HEADERS:
                roles[HEADERS[raw.strip()]].append(column)
        if CORE <= roles.keys():
            candidates.append((index, roles))
    if len(candidates) != 1:
        return None, 'HEADER_MISSING' if not candidates else 'MULTIPLE_HEADER_ROWS'
    index, roles = candidates[0]
    if any(len(columns) != 1 for columns in roles.values()):
        return None, 'HEADER_ROLE_AMBIGUOUS'
    return (index, {name: cols[0] for name, cols in roles.items()}), None


class DocumentSdkAdapter:
    """Application reader port. No source repository, OCR provider, or AI client."""
    reader_mode = 'REAL_SDK'
    is_real_sdk = True
    max_file_bytes = 25 * 1024 * 1024
    max_public_output_bytes = 4 * 1024 * 1024
    max_cells = 20000
    max_sheets = 10

    def __init__(self, wheel_path=None):
        selected = wheel_path if wheel_path is not None else os.environ.get("SANZO_SDK_WHEEL_PATH", str(DEFAULT_WHEEL))
        self.installation = verify_installation(selected)

    def read(self, path, document):
        path = Path(path)
        digest = _digest(path, self.max_file_bytes)
        if digest != document.get('sha256'):
            raise ValueError('REAL_SDK_UPLOAD_HASH_MISMATCH')
        if not isinstance(document.get('document_id'), str) or not document['document_id']:
            raise ValueError('REAL_SDK_DOCUMENT_ID_REQUIRED')
        identity = {**self.installation, 'origin': 'REAL_SDK', 'application_reader': type(self).__name__,
                    'adapter_version': VERSION, 'real_sdk_execution': 'EXECUTED',
                    'public_outputs': {}, 'public_output_json': {}, 'public_calls': [], 'limitations': []}
        blocks, issues = [], []
        output_size = 0

        def call(label, function, *args, **kwargs):
            nonlocal output_size
            record = {'api': label, 'status': 'RUNNING'}
            identity['public_calls'].append(record)
            try:
                result = function(*args, **kwargs)
                raw = (result.to_json_bytes() if hasattr(result, 'to_json_bytes') else
                       result.model_dump_json(indent=2).encode('utf-8'))
                output_size += len(raw)
                if output_size > self.max_public_output_bytes:
                    raise ValueError('REAL_SDK_PUBLIC_OUTPUT_SIZE_LIMIT')
                value = json.loads(raw)
                candidate_outputs = {**identity['public_outputs'], label: value}
                candidate_json = {**identity['public_output_json'], label: raw.decode('utf-8')}
                # Keep exact public serializer bytes as text alongside decoded
                # objects. Reserve room for application blocks under DB's 8 MiB.
                if len(json.dumps({'outputs': candidate_outputs, 'json': candidate_json},
                                  ensure_ascii=False, separators=(',', ':')).encode('utf-8')) > 6 * 1024 * 1024:
                    raise ValueError('REAL_SDK_PUBLIC_OUTPUT_SIZE_LIMIT')
                identity['public_outputs'] = candidate_outputs
                identity['public_output_json'] = candidate_json
                record.update(status='SUCCESS', sha256=sha256(raw).hexdigest(), size_bytes=len(raw),
                              serializer='to_json_bytes' if hasattr(result, 'to_json_bytes') else 'model_dump_json(indent=2)')
                return value
            except Exception as exc:
                record.update(status='FAILED', error_type=type(exc).__name__)
                # SDK exception messages can contain source content; retain type only.
                raise

        try:
            suffix = path.suffix.lower()
            if suffix == '.xlsx':
                self._excel(path, document, call, blocks, issues, identity)
            elif suffix == '.pdf':
                self._pdf(path, document, call, blocks, issues, identity)
            else:
                raise ValueError('REAL_SDK_UNSUPPORTED_FORMAT')
        except Exception as exc:
            # Preserve partial public calls and issue evidence. Never retry or Mock.
            code = str(exc) if isinstance(exc, ValueError) and str(exc).startswith('REAL_SDK_') else 'REAL_SDK_READ_FAILED'
            issues.append({'code': code, 'error_type': type(exc).__name__})
            blocks.clear()
        if _digest(path, self.max_file_bytes) != digest:
            raise ValueError('REAL_SDK_ORIGINAL_CHANGED')
        if len(blocks) > 5000:
            raise ValueError('REAL_SDK_BLOCK_LIMIT')
        if len(json.dumps({'sdk_identity': identity, 'blocks': blocks}, ensure_ascii=False,
                          separators=(',', ':')).encode('utf-8')) > 7 * 1024 * 1024:
            blocks.clear()
            issues.append({'code': 'REAL_SDK_READ_RESULT_SIZE_LIMIT'})
        result = ReadResult(digest, tuple(blocks), identity, origin='REAL_SDK', issues=tuple(issues))
        result.validate(document)
        return result

    @staticmethod
    def _fingerprint(value, document):
        actual = value.get('file', {}).get('fingerprint', {})
        if actual.get('algorithm') != 'sha256' or actual.get('value') != document['sha256']:
            raise ValueError('REAL_SDK_FINGERPRINT_MISMATCH')

    @staticmethod
    def _issue(issues, reason, **details):
        issues.append({'code': 'EXTRACTION_UNRESOLVED', 'reason': reason, **details})

    @staticmethod
    def _block(document, source_id, text, source, field, record):
        source = {**source, 'document_id': document['document_id'], 'document_sha256': document['sha256'],
                  'origin': 'REAL_SDK', 'extraction_origin': 'REAL_SDK', 'text_verification': 'VERIFIED',
                  'adapter_version': VERSION, 'source_block_ids': [source_id],
                  'evidence_unit': {'kind': 'EXCEL_CELL' if source['kind'] == 'EXCEL_RANGE' else 'SDK_FIELD',
                                    'scope': 'LINE', 'field_name': field, 'record_id': record}}
        return {'source_id': source_id, 'raw_text': text, 'source_ref': source}

    def _excel(self, path, document, call, blocks, issues, identity):
        from document_sdk.excel import inspect_workbook, read_sheet, inspect_workbook_semantic_details
        inspection = call('document_sdk.excel.inspect_workbook', inspect_workbook, path)
        self._fingerprint(inspection, document)
        sheets = inspection['worksheets']
        if not 0 < len(sheets) <= self.max_sheets:
            raise ValueError('REAL_SDK_SHEET_LIMIT')
        if sum(s['max_row'] * s['max_column'] for s in sheets) > self.max_cells:
            raise ValueError('REAL_SDK_CELL_LIMIT')
        semantic = call('document_sdk.excel.inspect_workbook_semantic_details', inspect_workbook_semantic_details, path)
        self._fingerprint(semantic, document)
        physical = defaultdict(list)
        for cell in semantic['physical_cells']:
            physical[(cell['sheet_name'], cell['coordinate'])].append(cell)
        if not semantic.get('complete'):
            self._issue(issues, 'SDK_SEMANTIC_SOURCE_INCOMPLETE')
        for sheet in sheets:
            name = sheet['name']
            data = call('document_sdk.excel.read_sheet:' + name, read_sheet, path, name,
                        max_cells=self.max_cells, formula_view='both')
            self._fingerprint(data, document)
            rows = defaultdict(dict)
            for cell in data['cells']:
                row, column = _coordinate(cell['coordinate'])
                if column in rows[row]:
                    raise ValueError('REAL_SDK_DUPLICATE_CELL')
                rows[row][column] = cell
            binding, error = _header(rows)
            if error:
                self._issue(issues, error, sheet_name=name)
                continue
            header_row, roles = binding
            headers = rows[header_row]
            if any(_merged(headers[col]['coordinate'], sheet['merged_ranges']) for col in roles.values()):
                self._issue(issues, 'MERGED_HEADER_UNRESOLVED', sheet_name=name)
                continue
            for row_number, row in sorted(rows.items()):
                if row_number <= header_row:
                    continue
                # Every nonempty row inside the bound columns is handled or reported.
                present = [role for role, col in roles.items() if col in row]
                if not present:
                    continue
                if not any(role in present for role in CORE):
                    self._issue(issues, 'UNBOUND_ROW', sheet_name=name, row_number=row_number)
                    continue
                record = f'excel:{name}:row:{row_number}'
                if 'raw_label' not in present:
                    self._issue(issues, 'ROW_LABEL_MISSING', sheet_name=name, row_number=row_number)
                for role, column in roles.items():
                    cell = row.get(column)
                    if cell is None:
                        self._issue(issues, 'FIELD_MISSING', sheet_name=name, row_number=row_number, field=role)
                        continue
                    address = cell['coordinate']
                    reason = None
                    if _merged(address, sheet['merged_ranges']):
                        reason = 'MERGED_FIELD_UNRESOLVED'
                    elif cell.get('formula') or cell.get('formula_text') or cell.get('external_reference') or cell.get('requires_recalculation'):
                        reason = 'FORMULA_VALUE_UNCONFIRMED'
                    records = physical[(name, address)]
                    if len(records) != 1:
                        reason = 'PHYSICAL_CELL_AMBIGUOUS_OR_MISSING'
                    value = cell.get('value')
                    text = value if isinstance(value, str) else None
                    if type(value) in (int, float) and len(records) == 1:
                        source_cell = records[0]
                        if (source_cell.get('formula_present') or source_cell.get('raw_type') not in ('n', None)
                            or not source_cell.get('cached_value_present') or not isinstance(source_cell.get('raw_cached_value'), str)):
                            reason = 'NUMERIC_LEXICAL_SOURCE_UNVERIFIED'
                        else:
                            text = source_cell['raw_cached_value']
                    if not isinstance(text, str) or not text.strip() or len(text) > 1024:
                        reason = reason or 'COMPLETE_FIELD_VALUE_UNAVAILABLE'
                    sdk_source = cell.get('source', {})
                    if (sdk_source.get('sheet_name') != name or sdk_source.get('cell_range') != address
                        or sdk_source.get('file_id') != data['file']['file_id']):
                        reason = 'SDK_CELL_SOURCE_MISMATCH'
                    if reason:
                        self._issue(issues, reason, sheet_name=name, cell=address, field=role)
                        continue
                    source_id = f'excel:{name}:{address}'
                    source = {'kind': 'EXCEL_RANGE', 'locator_status': 'VERIFIED',
                              'locator': {'sheet_name': name, 'A1_range': address},
                              'sdk_source': sdk_source, 'sdk_value': value,
                              'sdk_physical_cell': records[0],
                              'header_binding': {'rule': VERSION, 'header_cell': headers[column]['coordinate'],
                                                 'header_text': headers[column]['value']}}
                    blocks.append(self._block(document, source_id, text, source, role, record))
            record_ids = sorted({b['source_ref']['evidence_unit']['record_id'] for b in blocks
                                 if b['source_ref'].get('locator', {}).get('sheet_name') == name
                                 and b['source_ref']['evidence_unit']['field_name'] == 'raw_label'})
            identity.setdefault('record_ids_by_sheet', {})[name] = record_ids
            candidates = identity.setdefault('field_binding_candidates', [])
            # These complete source cells are only candidates. The application
            # requires an explicit HUMAN role assignment; no issuer guessing.
            if record_ids:
                for candidate_row, row in sorted(rows.items()):
                    if candidate_row >= header_row or len(row) != 1:
                        continue
                    cell = next(iter(row.values()))
                    address, text = cell['coordinate'], cell.get('value')
                    records = physical[(name, address)]
                    if (not isinstance(text, str) or not text.strip() or len(text) > 1024
                        or cell.get('formula') or cell.get('formula_text') or cell.get('external_reference')
                        or cell.get('requires_recalculation') or len(records) != 1
                        or records[0].get('formula_present')):
                        continue
                    sdk_source = cell.get('source', {})
                    if (sdk_source.get('sheet_name') != name or sdk_source.get('cell_range') != address
                        or sdk_source.get('file_id') != data['file']['file_id']):
                        continue
                    merged = []
                    for merge in sheet['merged_ranges']:
                        start, _, end = merge.partition(':')
                        if start == address:
                            if _coordinate(start)[0] != _coordinate(end or start)[0]:
                                merged = None
                                break
                            merged.append(merge)
                    if merged is None or len(merged) > 1:
                        continue
                    source_id = f'excel:{name}:{address}'
                    source = {'document_id': document['document_id'], 'document_sha256': document['sha256'],
                              'origin': 'REAL_SDK', 'extraction_origin': 'REAL_SDK', 'kind': 'EXCEL_RANGE', 'locator_status': 'VERIFIED',
                              'locator': {'sheet_name': name, 'A1_range': address},
                              'text_verification': 'VERIFIED', 'sdk_source': sdk_source,
                              'sdk_physical_cell': records[0], 'sdk_merged_ranges': merged,
                              'source_block_ids': [source_id], 'adapter_version': VERSION,
                              'evidence_unit': {'kind': 'EXCEL_CELL', 'scope': 'DOCUMENT',
                                                'record_id': f'excel:{name}:header:{address}',
                                                'applies_to_record_ids': record_ids}}
                    candidates.append({'source_id': source_id, 'raw_text': text, 'source_ref': source})
                    if len(candidates) > 100:
                        raise ValueError('REAL_SDK_BINDING_CANDIDATE_LIMIT')
            for role in sorted(ALL_FIELDS - roles.keys()):
                self._issue(issues, 'FIELD_HEADER_NOT_BOUND', sheet_name=name, field=role)

    def _pdf(self, path, document, call, blocks, issues, identity):
        from document_sdk.pdf import (analyze_pdf, PdfProcessingLimits, PdfAnalysisMode, UnifiedPdfAnalysisOptions)
        limits = PdfProcessingLimits(max_file_bytes=self.max_file_bytes, max_pages=10,
                                    max_native_text_pages=10, max_native_text_characters=200000)
        value = call('document_sdk.pdf.analyze_pdf', analyze_pdf, path,
                     options=UnifiedPdfAnalysisOptions(mode=PdfAnalysisMode.ADVANCED_LOCAL, limits=limits))
        if value.get('source_sha256') != document['sha256']:
            raise ValueError('REAL_SDK_FINGERPRINT_MISMATCH')
        identity.update(availability=value.get('availability'), limitations=value.get('limitations', []),
                        processing_mode=value.get('processing_mode'), pdf_preview='PAGE_ONLY_NO_PUBLIC_CELL_BBOX')
        for limitation in value.get('limitations', []):
            issues.append({'code': 'SDK_LIMITATION', 'limitation': limitation})
        self._pdf_tables(value, document, blocks, issues)

    def _pdf_tables(self, value, document, blocks, issues):
        seen = set()
        pages = {p['page_number'] for p in value['pages']}
        for table in value['tables']:
            table_id, page = table['table_id'], table['page_number']
            if table_id in seen or page not in pages:
                raise ValueError('REAL_SDK_TABLE_IDENTITY_INVALID')
            seen.add(table_id)
            if table.get('availability') != 'AVAILABLE':
                self._issue(issues, 'PDF_TABLE_UNAVAILABLE', table_id=table_id, page_number=page)
                continue
            rows = defaultdict(dict)
            ambiguous = False
            for cell in table['cells']:
                row, column = cell['row_index'], cell['column_index']
                if (type(row) is not int or type(column) is not int or row < 0 or column < 0
                    or row >= table['row_count'] or column >= table['column_count'] or column in rows[row]):
                    ambiguous = True
                rows[row][column] = cell
            if ambiguous:
                self._issue(issues, 'PDF_TABLE_MEMBERSHIP_AMBIGUOUS', table_id=table_id)
                continue
            binding, error = _header(rows)
            if error:
                self._issue(issues, error, table_id=table_id)
                continue
            header_row, roles = binding
            if any(rows[header_row][col].get('availability') != 'AVAILABLE'
                   or rows[header_row][col].get('row_span') != 1 or rows[header_row][col].get('column_span') != 1
                   for col in roles.values()):
                self._issue(issues, 'PDF_HEADER_BOUNDARY_UNVERIFIED', table_id=table_id)
                continue
            for row_number in range(header_row + 1, table['row_count']):
                row = rows.get(row_number, {})
                if not row:
                    self._issue(issues, 'PDF_ROW_MISSING', table_id=table_id, row_index=row_number)
                record = f'pdf:{page}:{table_id}:row:{row_number}'
                for role, column in roles.items():
                    cell = row.get(column)
                    if (cell is None or cell.get('availability') != 'AVAILABLE'
                        or cell.get('row_span') != 1 or cell.get('column_span') != 1
                        or not isinstance(cell.get('text'), str) or not cell['text'].strip() or len(cell['text']) > 1024):
                        self._issue(issues, 'PDF_FIELD_BOUNDARY_UNVERIFIED', table_id=table_id,
                                    row_index=row_number, column_index=column, field=role)
                        continue
                    source_id = f'{record}:column:{column}'
                    source = {'kind': 'PDF_PAGE', 'locator_status': 'PAGE_ONLY',
                              'locator': {'page_number': page},
                              'sdk_table': {'table_id': table_id, 'row_index': row_number, 'column_index': column,
                                            'row_span': cell['row_span'], 'column_span': cell['column_span'],
                                            'availability': cell['availability']},
                              'header_binding': {'rule': VERSION, 'row_index': header_row,
                                                 'column_index': column, 'header_text': rows[header_row][column]['text']}}
                    blocks.append(self._block(document, source_id, cell['text'], source, role, record))
                unbound = sorted(column for column in row if column not in roles.values())
                for column in unbound:
                    self._issue(issues, 'PDF_COLUMN_NOT_BOUND', table_id=table_id, row_index=row_number,
                                column_index=column, header_text=rows[header_row].get(column, {}).get('text'))
            for role in sorted(ALL_FIELDS - roles.keys()):
                self._issue(issues, 'FIELD_HEADER_NOT_BOUND', table_id=table_id, field=role)
        if not seen:
            self._issue(issues, 'NO_PUBLIC_TABLES')
