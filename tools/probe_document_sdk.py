"""Bounded fixed-wheel public-API probe. No application runtime or AI is imported.

Run with the isolated Python 3.12 interpreter and -I -B. SDK results are saved
using their public serializers, before any application field interpretation.
"""
from __future__ import annotations
import argparse
from hashlib import sha256
from importlib import metadata
import inspect
import json
import os
from pathlib import Path
import sys
import time
import traceback
from zipfile import ZipFile

WHEEL = Path(os.environ.get('SANZO_SDK_WHEEL_PATH', str(
    Path(__file__).resolve().parents[1] / 'sdk' / 'document_processing_sdk-1.8.0-py3-none-any.whl')))
EXPECTED_SHA = 'd1dee21ff4b03c555cc1884270062bebc7267f14dec0ade287f5133fb3081ba0'
PROJECT = Path(__file__).resolve().parents[1]
SAMPLES = ('01_A社_初回見積.xlsx', '02_B社_初回見積.pdf', '03_C社_次回見積.xlsx')
FORBIDDEN = '/Users/kyoen/Projects/document-processing-sdk'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sample', choices=SAMPLES, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve(); out.mkdir(parents=True, exist_ok=False)
    audit = {'network_attempts_blocked': 0, 'forbidden_repo_attempts_blocked': 0,
             'credential_file_attempts_blocked': 0, 'subprocess_attempts_blocked': 0}

    def guard(event, values):
        if event in {'socket.connect', 'socket.connect_ex', 'socket.getaddrinfo', 'socket.bind', 'socket.sendto'}:
            audit['network_attempts_blocked'] += 1
            raise PermissionError('NETWORK_DISABLED_FOR_WHEEL_PROBE')
        if event == 'subprocess.Popen':
            audit['subprocess_attempts_blocked'] += 1
            raise PermissionError('SUBPROCESS_DISABLED_FOR_WHEEL_PROBE')
        if event in {'open', 'os.listdir', 'os.scandir', 'os.chdir'} and values:
            value = values[0]
            if isinstance(value, (str, bytes, os.PathLike)):
                name = os.path.abspath(os.fsdecode(value))
                if name == FORBIDDEN or name.startswith(FORBIDDEN + os.sep):
                    audit['forbidden_repo_attempts_blocked'] += 1
                    raise PermissionError('SDK_REPOSITORY_FORBIDDEN')
                if Path(name).name == '.env' or Path(name).name.startswith('.env.'):
                    audit['credential_file_attempts_blocked'] += 1
                    raise PermissionError('CREDENTIAL_FILE_READ_FORBIDDEN')
    sys.addaudithook(guard)
    manifest = {'sample': args.sample, 'python': sys.version, 'executable': sys.executable,
                'public_calls': [], 'audit': audit, 'status': 'RUNNING', 'openai_calls': 0}

    def save_call(label, function, *positional, **kwargs):
        started = time.monotonic()
        record = {'name': label, 'public_api': function.__module__ + '.' + function.__name__,
                  'signature': str(inspect.signature(function))}
        manifest['public_calls'].append(record)
        try:
            result = function(*positional, **kwargs)
            if hasattr(result, 'to_json_bytes'):
                content = result.to_json_bytes()
            elif hasattr(result, 'model_dump_json'):
                content = result.model_dump_json(indent=2).encode('utf-8')
            elif isinstance(result, tuple):
                content = ('[\n' + ',\n'.join(x.model_dump_json(indent=2) for x in result) + '\n]').encode('utf-8')
            else:
                raise TypeError('UNSUPPORTED_PUBLIC_RESULT_SERIALIZER')
            destination = out/(label+'.json');destination.write_bytes(content)
            record.update(status='SUCCESS', result_type=type(result).__module__+'.'+type(result).__name__,
                          output=destination.name, sha256=sha256(content).hexdigest(), size_bytes=len(content))
            return result
        except Exception as exc:
            record.update(status='FAIL', error_type=type(exc).__module__+'.'+type(exc).__name__,
                          public_error=str(exc))
            (out/(label+'_failure.txt')).write_text(traceback.format_exc())
            return None
        finally:
            record['duration_seconds'] = time.monotonic()-started

    try:
        assert sys.version_info[:2] == (3, 12), 'PYTHON_3_12_REQUIRED'
        assert not WHEEL.is_symlink() and sha256(WHEEL.read_bytes()).hexdigest()==EXPECTED_SHA, 'WHEEL_HASH_MISMATCH'
        distribution=metadata.distribution('document-processing-sdk')
        assert distribution.version=='1.8.0', 'SDK_VERSION_MISMATCH'
        location=Path(distribution.locate_file('document_sdk')).resolve()
        assert location.is_relative_to(Path(sys.prefix).resolve()), 'SDK_MUST_BE_INSTALLED_IN_ISOLATED_VENV'
        direct=json.loads(distribution.read_text('direct_url.json'))
        assert direct['url']==WHEEL.as_uri(), 'SDK_FIXED_WHEEL_ORIGIN_REQUIRED'
        checked = 0
        with ZipFile(WHEEL) as archive:
            for member in archive.infolist():
                if member.is_dir() or not member.filename.startswith('document_sdk/'):
                    continue
                installed = Path(distribution.locate_file(member.filename))
                assert not installed.is_symlink() and installed.resolve().is_relative_to(location), 'FOREIGN_SDK_MEMBER'
                assert installed.read_bytes() == archive.read(member), 'SDK_INSTALLED_FILE_MISMATCH'
                checked += 1
        assert checked, 'SDK_INSTALLED_MEMBERS_REQUIRED'
        from document_sdk.pdf import analyze_pdf_local_first
        assert callable(analyze_pdf_local_first)
        manifest['installation']={'version':distribution.version,'module_path':str(location),'wheel_sha256':EXPECTED_SHA,'direct_url':direct,
                                  'installed_file_check':'VERIFIED','installed_files_checked':checked,
                                  'local_first_import':'PASS'}
        sample=PROJECT/'demo_materials/samples'/args.sample
        assert not sample.is_symlink() and sample.stat().st_size <= 25*1024*1024
        digest=sha256(sample.read_bytes()).hexdigest()
        declared=json.loads((PROJECT/'demo_materials/fixtures/manifest.json').read_text())
        # The third original was renamed; preserve the sealed manifest bytes.
        manifest_name = '03_A社_次回見積.xlsx' if args.sample == '03_C社_次回見積.xlsx' else args.sample
        entry=next(e for e in declared['fixtures'] if e['sample_filename']==manifest_name)
        assert digest==entry['sample_sha256'], 'SAMPLE_BASELINE_HASH_MISMATCH'
        manifest['sample_sha256']=digest
        if sample.suffix=='.xlsx':
            from document_sdk.excel import (inspect_workbook, read_sheet, detect_table_regions,
                                           TableDetectionOptions, inspect_workbook_semantic_details)
            info=save_call('inspect_workbook',inspect_workbook,sample)
            if info is not None:
                assert len(info.worksheets)<=10, 'SHEET_PROBE_LIMIT'
                for index,sheet in enumerate(info.worksheets,1):
                    save_call(f'sheet_{index}_read',read_sheet,sample,sheet.name,max_cells=20000,formula_view='both')
                    save_call(f'sheet_{index}_table_regions',detect_table_regions,sample,sheet.name,
                              options=TableDetectionOptions(max_cells=20000))
                save_call('semantic_source_details',inspect_workbook_semantic_details,sample)
        else:
            from document_sdk.pdf import (inspect_pdf,extract_native_text,analyze_pdf,
                PdfProcessingLimits,PdfAnalysisMode,UnifiedPdfAnalysisOptions)
            limits=PdfProcessingLimits(max_file_bytes=25*1024*1024,max_pages=10,
                max_native_text_pages=10,max_native_text_characters=200000)
            manifest['pdf_options']={'mode':'ADVANCED_LOCAL','limits':limits.model_dump(mode='json'),
                                     'hybrid_provider':None,'ocr_enabled':False}
            save_call('inspect_pdf',inspect_pdf,sample,limits=limits)
            save_call('native_text',extract_native_text,sample,limits=limits)
            save_call('advanced_local',analyze_pdf,sample,
                      options=UnifiedPdfAnalysisOptions(mode=PdfAnalysisMode.ADVANCED_LOCAL,limits=limits))
        assert sha256(sample.read_bytes()).hexdigest()==digest, 'ORIGINAL_CHANGED'
        modules={n:str(Path(m.__file__).resolve()) for n,m in sys.modules.items()
                 if n=='document_sdk' or n.startswith('document_sdk.') if getattr(m,'__file__',None)}
        assert all(Path(path).is_relative_to(location) for path in modules.values()), 'FOREIGN_SDK_MODULE'
        manifest['sdk_module_count']=len(modules)
        manifest['sdk_imports_from_fixed_install_only']=True
        manifest['original_unchanged']=True
        manifest['status']='SUCCESS' if all(c['status']=='SUCCESS' for c in manifest['public_calls']) else 'PARTIAL'
    except Exception as exc:
        manifest.update(status='FAIL',error_type=type(exc).__name__,error=str(exc))
        (out/'probe_failure.txt').write_text(traceback.format_exc())
    finally:
        (out/'MANIFEST.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'sample':args.sample,'status':manifest['status'],'calls':len(manifest['public_calls']),'audit':audit},ensure_ascii=False))
    return 0 if manifest['status']=='SUCCESS' else 1

if __name__=='__main__':
    raise SystemExit(main())
