"""Verify a framework/review ZIP without extracting or trusting member paths.

Usage: python3 tools/verify_bundle.py /path/package.zip /path/package.zip.sha256
"""
from __future__ import annotations
import argparse
import hashlib
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile


def verify(archive: Path, checksum: Path) -> dict:
    expected = checksum.read_text(encoding='utf-8').strip().split()[0]
    if not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise ValueError('Malformed external hash')
    actual = hashlib.sha256(archive.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError('External ZIP SHA-256 mismatch')
    with zipfile.ZipFile(archive) as z:
        members = z.infolist()
        names=[m.filename for m in members]
        if len(names) != len(set(names)):
            raise ValueError('Duplicate ZIP member')
        if sum(m.file_size for m in members) > 100 * 1024 * 1024:
            raise ValueError('Bundle exceeds this verifier\'s 100 MiB limit')
        for m in members:
            p=PurePosixPath(m.filename)
            if p.is_absolute() or '..' in p.parts or '\\' in m.filename or ':' in m.filename:
                raise ValueError(f'Unsafe member path: {m.filename}')
            if stat.S_ISLNK(m.external_attr >> 16):
                raise ValueError(f'Symlink member rejected: {m.filename}')
        bad=z.testzip()
        if bad:
            raise ValueError(f'CRC failed: {bad}')
        manifests=[n for n in names if n.endswith('/SHA256SUMS') or n=='SHA256SUMS']
        if len(manifests)!=1:
            raise ValueError('Exactly one internal SHA256SUMS is required')
        manifest=manifests[0]
        prefix=manifest[:-len('SHA256SUMS')]
        expected_names=set()
        for line in z.read(manifest).decode('utf-8').splitlines():
            digest, relative=line.split('  ',1)
            if not re.fullmatch(r'[0-9a-f]{64}',digest):
                raise ValueError('Malformed internal hash')
            p=PurePosixPath(relative)
            if p.is_absolute() or '..' in p.parts or '\\' in relative or ':' in relative:
                raise ValueError('Unsafe manifest path')
            member=prefix+relative
            if member in expected_names:
                raise ValueError('Duplicate manifest member')
            expected_names.add(member)
            if hashlib.sha256(z.read(member)).hexdigest()!=digest:
                raise ValueError(f'Internal SHA mismatch: {relative}')
        actual_names={m.filename for m in members if not m.is_dir() and m.filename!=manifest}
        if actual_names!=expected_names:
            raise ValueError('Manifest coverage differs from archive files')
    return {'zip_sha256': actual, 'members_verified':len(expected_names),
            'crc':'PASS','internal_sha':'PASS','external_sha':'PASS','member_safety':'PASS'}

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('archive',type=Path)
    p.add_argument('checksum',type=Path)
    args=p.parse_args()
    import json
    print(json.dumps(verify(args.archive,args.checksum),indent=2))
