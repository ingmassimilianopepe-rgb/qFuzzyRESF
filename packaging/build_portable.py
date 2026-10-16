#!/usr/bin/env python3
"""Assemble a verified GitHub Windows binary ZIP with the v8 setup helpers.

The native binary ZIP must come from the recorded build commit. No native
compilation is performed by this script. Existing installations are untouched.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def build(binary_zip, output, native_commit):
    repo = Path(__file__).resolve().parents[1]
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Choose a new output ZIP; an existing deliverable is not overwritten')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='qfuzzyresf-package-') as temp:
        root = Path(temp)
        with zipfile.ZipFile(binary_zip) as archive:
            for info in archive.infolist():
                destination = (root / info.filename).resolve()
                if not destination.is_relative_to(root):
                    raise ValueError('Archive contains a path outside the package')
            bad = archive.testzip()
            if bad:
                raise ValueError(f'Corrupt binary archive member: {bad}')
            archive.extractall(root)
        app = root / 'CloudCompare'
        if not (app / 'CloudCompare.exe').is_file():
            raise ValueError('Expected CloudCompare/CloudCompare.exe in native ZIP')
        native_files = []
        for name in ('QFUZZYRESF_PLUGIN.dll', 'QLAS_IO_PLUGIN.dll', 'QE57_IO_PLUGIN.dll'):
            matches = list(app.rglob(name))
            if len(matches) != 1:
                raise ValueError(f'Expected exactly one {name}, found {len(matches)}')
            native_files.append(matches[0])
        for file in [app / 'CloudCompare.exe', *native_files]:
            with file.open('rb') as stream:
                if stream.read(2) != b'MZ':
                    raise ValueError(f'Not a Windows PE binary: {file.name}')
                stream.seek(0x3C)
                pe_offset = int.from_bytes(stream.read(4), 'little')
                stream.seek(pe_offset)
                if stream.read(4) != b'PE\0\0' or int.from_bytes(stream.read(2), 'little') != 0x8664:
                    raise ValueError(f'Not Windows x64: {file.name}')
        backend = app / 'qFuzzyRESF' / 'backend'
        # Compare all shipped algorithm files with the exact native build commit.
        expected_paths = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', native_commit, 'backend'], cwd=repo, text=True).splitlines()
        for relative in expected_paths:
            expected = subprocess.check_output(['git', 'show', f'{native_commit}:{relative}'], cwd=repo)
            actual = backend / Path(relative).relative_to('backend')
            if not actual.is_file() or actual.read_bytes().replace(b'\r\n', b'\n') != expected.replace(b'\r\n', b'\n'):
                raise ValueError(f'Native package backend does not match source commit: {relative}')
        for source in (repo / 'packaging/windows').iterdir():
            if source.is_file():
                shutil.copy2(source, root / source.name)
        # Replace the historic quick-start with the runtime-isolated workflow.
        shutil.copy2(repo / 'packaging/windows/LEGGIMI_INSTALLAZIONE.txt', root / 'README_QFUZZYRESF.txt')
        shutil.copytree(repo / 'examples', root / 'examples', ignore=shutil.ignore_patterns('__pycache__'))
        (root / 'docs').mkdir(exist_ok=True)
        shutil.copy2(repo / 'docs/SEMANTIC_PIPELINE.md', root / 'docs/SEMANTIC_PIPELINE.md')
        shutil.copy2(repo / 'LICENSE', root / 'LICENSE_qFuzzyRESF')
        (root / 'SORGENTI.txt').write_text(
            f'qFuzzyRESF source (GPL-3.0-or-later):\nhttps://github.com/ingmassimilianopepe-rgb/qFuzzyRESF/tree/{native_commit}\n\n'
            'CloudCompare source:\nhttps://github.com/CloudCompare/CloudCompare/tree/92adb19a651f5711f7896d0e4351344703ff88c4\n', encoding='utf-8')
        manifest = {'tool': 'qFuzzyRESF v8 for CloudCompare Windows x64', 'native_commit': native_commit,
            'native_zip_sha256': sha256(binary_zip), 'native_binary_validation': 'PE x64 header and archive integrity; native execution requires Windows',
            'files': {str(p.relative_to(root)).replace('\\', '/'): {'bytes': p.stat().st_size, 'sha256': sha256(p)}
                      for p in [app / 'CloudCompare.exe', *native_files, root / 'SETUP_QFUZZYRESF.ps1', root / 'START_QFUZZYRESF.ps1', root / 'TEST_QFUZZYRESF.ps1']}}
        (root / 'BUILD_INFO.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        # Do not publish an incomplete ZIP if packaging fails midway.
        partial = output.with_suffix('.zip.part')
        try:
            with zipfile.ZipFile(partial, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
                for file in sorted(root.rglob('*')):
                    if file.is_file():
                        z.write(file, str(file.relative_to(root)))
            partial.replace(output)
        finally:
            partial.unlink(missing_ok=True)
    digest = sha256(output)
    output.with_suffix('.zip.sha256').write_text(f'{digest}  {output.name}\n', encoding='ascii')
    print(json.dumps({'file': str(output), 'bytes': output.stat().st_size, 'sha256': digest}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary-zip', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--native-commit', required=True)
    args = parser.parse_args()
    build(args.binary_zip, args.output, args.native_commit)
