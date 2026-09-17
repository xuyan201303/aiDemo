"""Pack an explicit file allowlist; never zip a whole repository automatically.

Usage:
  python3 tools/pack_review.py --root /path/to/new-project \
    --allowlist /path/allowed-relative-files.json \
    --output /path/W0_REVIEW.zip --stage-status W0_COMPLETE_PRE_REVIEW

The supplied stage status is preserved, not inferred from successful ZIP creation.
Customer originals, credentials, caches, git internals and symlinks are rejected.
The allowlist is still subject to a human confidentiality review.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tempfile
import zipfile

from verify_bundle import verify

DENY_PARTS={'.git','.venv','venv','node_modules','__pycache__','.pytest_cache'}
DENY_SUFFIXES={'.pdf','.xls','.xlsx','.xlsm','.eml','.msg','.sqlite3','.db','.pem','.key','.p12','.pfx'}
MAX_MEMBER=20*1024*1024
MAX_TOTAL=100*1024*1024


def main() -> None:
    p=argparse.ArgumentParser()
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--allowlist',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--stage-status',required=True)
    a=p.parse_args()
    if not re.fullmatch(r'[A-Z0-9_-]{1,100}',a.stage_status):
        raise ValueError('Stage status must be explicit uppercase machine-readable text')
    root=a.root.resolve(strict=True)
    members=json.loads(a.allowlist.read_text())
    if not isinstance(members,list) or not 1<=len(members)<=2000 or any(not isinstance(x,str) for x in members):
        raise ValueError('Allowlist must be a bounded JSON array of relative file paths')
    if len(members)!=len(set(members)):
        raise ValueError('Duplicate allowlist path')
    if a.output.suffix.lower()!='.zip':
        raise ValueError('Output must end in .zip')
    output=a.output.resolve()
    sha_path=Path(str(output)+'.sha256')
    verify_path=output.with_name(output.stem+'_VERIFICATION.txt')
    for target in (output,sha_path,verify_path):
        if target.exists():
            raise FileExistsError(f'Do not overwrite existing review evidence: {target}')
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='sanzo-review-stage-') as temp:
        staging=Path(temp)/'ReviewBundle'; staging.mkdir()
        total=0
        for name in members:
            rel=PurePosixPath(name)
            if rel.as_posix()!=name or rel.name in {'SHA256SUMS','PACKAGING_STATUS.json'}:
                raise ValueError('Noncanonical or reserved manifest path')
            if rel.is_absolute() or '..' in rel.parts or '\\' in name or ':' in name or any(x in DENY_PARTS for x in rel.parts):
                raise ValueError(f'Unsafe path: {name}')
            if rel.name.startswith('.env') and rel.name!='.env.example':
                raise ValueError('Real environment file is excluded')
            if rel.suffix.lower() in DENY_SUFFIXES:
                raise ValueError(f'Customer original/credential/database file type excluded: {name}')
            source=root.joinpath(*rel.parts)
            if not source.resolve(strict=True).is_relative_to(root) or not source.is_file():
                raise ValueError('Source escapes authorized root or is not a file')
            for part in [source,*source.parents]:
                if part==root: break
                if part.is_symlink(): raise ValueError('Symlinks excluded')
            size=source.stat().st_size
            total+=size
            if size>MAX_MEMBER or total>MAX_TOTAL:
                raise ValueError('Review evidence size budget exceeded')
            dest=staging.joinpath(*rel.parts); dest.parent.mkdir(parents=True,exist_ok=True)
            dest.write_bytes(source.read_bytes())
        metadata={'stage_status':a.stage_status,'status_source':'caller supplied; packaging does not change it',
                  'source_root':str(root),'allowlist_count':len(members),
                  'notice':'Confidentiality must be reviewed; packaging is not a test-result gate.'}
        (staging/'PACKAGING_STATUS.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n')
        files=sorted(x for x in staging.rglob('*') if x.is_file() and x.name!='SHA256SUMS')
        (staging/'SHA256SUMS').write_text(''.join(hashlib.sha256(x.read_bytes()).hexdigest()+'  '+x.relative_to(staging).as_posix()+'\n' for x in files))
        with zipfile.ZipFile(output,'x',compression=zipfile.ZIP_DEFLATED) as z:
            for file in sorted(x for x in staging.rglob('*') if x.is_file()):
                z.write(file,'ReviewBundle/'+file.relative_to(staging).as_posix())
        sha_path.write_text(hashlib.sha256(output.read_bytes()).hexdigest()+'  '+output.name+'\n')
        checked=verify(output,sha_path)
        verify_path.write_text('STAGE_STATUS='+a.stage_status+'\nPACKAGING=VERIFIED\n'+json.dumps(checked,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'stage_status':a.stage_status,'zip':str(output),'sha256':str(sha_path),'verification':str(verify_path)},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
