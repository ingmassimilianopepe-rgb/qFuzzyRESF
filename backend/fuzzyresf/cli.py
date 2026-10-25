"""Versioned JSON job interface and transactional result publication.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import argparse
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import signal
import sys
import tempfile
import time


def emit(event, **values):
    print(json.dumps({'event': event, **values}, ensure_ascii=False, allow_nan=False), flush=True)


def progress(percent, stage):
    emit('progress', percent=percent, stage=stage)


def dependencies():
    versions = {}
    for name in ('numpy', 'scipy', 'shapely', 'skimage', 'laspy', 'lazrs', 'ifcopenshell', 'trimesh', 'pytest'):
        module = importlib.import_module(name)
        versions[name] = getattr(module, '__version__', 'installed')
    return versions


def validate_request(request):
    if request.get('schema_version') != 1:
        raise ValueError('schema_version deve essere 1.')
    source = request['input']
    path = Path(source['path']).expanduser().resolve()
    if not path.is_file():
        raise ValueError('File di ingresso non trovato.')
    output = Path(request['output']).expanduser().absolute()
    if output.suffix.lower() != '.ifc':
        raise ValueError('Il file di uscita deve avere estensione .ifc.')
    if not output.parent.is_dir():
        raise ValueError('La cartella di uscita non esiste.')
    diagnostics = output.with_suffix('.fuzzyresf')
    if output.exists() or diagnostics.exists():
        raise ValueError('Output o cartella diagnostica già esistente. Scegliere un nuovo nome; nessun file viene sovrascritto.')
    scale = request.get('meters_per_unit', 1.)
    if isinstance(scale, bool) or not isinstance(scale, (float, int)) or not math.isfinite(scale) or not 1e-6 <= scale <= 1000:
        raise ValueError('Fattore di conversione in metri non valido.')
    profile = request.get('profile', 'monotone')
    if profile not in {'monotone', 'legacy', 'geometry_only'}:
        raise ValueError('Profilo sconosciuto.')
    limits = request.get('limits', {})
    for name, default, minimum, maximum in [('max_voxels', 3_000_000, 500, 20_000_000), ('max_raster_cells', 4_000_000, 1000, 20_000_000)]:
        value = limits.get(name, default)
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(f'{name} fuori intervallo [{minimum}, {maximum}].')
        limits[name] = value
    fmt = source.get('format', path.suffix[1:].lower())
    if fmt not in {'las', 'laz', 'xyz64'}:
        raise ValueError('Formato di ingresso non supportato.')
    return path, output, diagnostics, float(scale), profile, limits, fmt


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False, default=str) + '\n', encoding='utf-8')


def run(request):
    path, output, diagnostics, scale, profile, limits, fmt = validate_request(request)
    versions = dependencies()
    from .cloud import analyse
    from .inference import infer
    from .ifc import build_ifc
    start = time.perf_counter()
    # Same filesystem for atomic hard-link publication. Existing names never replaced.
    with tempfile.TemporaryDirectory(prefix='.qfuzzyresf-', dir=request.get('work_dir', output.parent)) as folder:
        work = Path(folder)
        P, N, PL, audit = analyse(path, fmt, scale, limits['max_voxels'], progress, request['input'].get('count'))
        audit['source'] = request['input'].get('name', path.name)
        audit['cloudcompare'] = request.get('cloudcompare', {})
        warnings = [
            'Ricostruzione pilota del livello principale; gli spazi richiedono verifica manuale.',
            'Spessori monofaccia e chiusure topologiche sono inferiti; porte e finestre non sono ricostruite.',
        ]
        if audit['fourfold_concentration'] < .5:
            warnings.append('Debole struttura Manhattan: pareti curve o oblique possono essere omesse o approssimate.')
        for warning in warnings:
            emit('warning', message=warning)
        inference = infer(P, N, PL, audit, profile, limits['max_raster_cells'], progress)
        del P, N, PL
        write_json(work / 'audit.json', audit)
        write_json(work / 'inference.json', inference)
        progress(75, 'Creazione e validazione IFC4')
        candidate = work / 'model.ifc'
        validation = build_ifc(inference, candidate)
        lengths = validation['space_side_boundary_length_m']
        virtual_fraction = lengths.get('virtual', 0) / max(sum(lengths.values()), 1e-12)
        if virtual_fraction > .3:
            warning = f'{virtual_fraction:.1%} del perimetro laterale degli spazi è virtuale: topologia non validata.'
            warnings.append(warning)
            emit('warning', message=warning)
        write_json(work / 'validation.json', validation)
        summary = {
            'tool_version': '0.1.0', 'method_version': '0.3', 'schema_version': 1,
            'profile': profile, 'source_sha256': audit['sha256'],
            'ifc_sha256': hashlib.sha256(candidate.read_bytes()).hexdigest(),
            'counts': validation['counts'], 'space_area_m2': validation['space_area_m2'],
            'virtual_side_fraction': virtual_fraction, 'warnings': warnings,
            'reference_used': False, 'python': platform.python_version(), 'dependencies': versions,
            'runtime_s': time.perf_counter() - start,
        }
        write_json(work / 'summary.json', summary)
        # mkdir is the exclusive claim on a result name; clean up only our own files.
        diagnostics.mkdir()
        linked = False
        try:
            for name in ('audit.json', 'inference.json', 'validation.json', 'summary.json'):
                shutil.copy2(work / name, diagnostics / name)
            os.link(candidate, output)  # fails if another writer created output
            linked = True
        except BaseException:
            shutil.rmtree(diagnostics)
            raise
        if linked:
            progress(100, 'IFC validato e salvato')
            emit('result', output=str(output), diagnostics=str(diagnostics), **summary)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='qFuzzyRESF: nuvola di punti → IFC4 (pilota)')
    parser.add_argument('--check', action='store_true', help='Verifica dipendenze Python')
    parser.add_argument('--request', type=Path, help='Job JSON schema 1')
    parser.add_argument('--input', type=Path, help='Nuvola LAS/LAZ o XYZ64')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--meters-per-unit', type=float, default=1.)
    parser.add_argument('--profile', choices=['monotone', 'legacy', 'geometry_only'], default='monotone')
    parser.add_argument('--max-voxels', type=int, default=3_000_000)
    args = parser.parse_args(argv)
    def interrupted(signum, frame):
        raise KeyboardInterrupt('Elaborazione annullata')
    signal.signal(signal.SIGTERM, interrupted)
    try:
        if args.check:
            emit('ready', python=sys.executable, versions=dependencies())
            return 0
        if args.request:
            request = json.loads(args.request.read_text(encoding='utf-8'))
        else:
            if not args.input or not args.output:
                parser.error('Indicare --request oppure --input e --output')
            request = {'schema_version': 1, 'input': {'path': str(args.input)}, 'output': str(args.output),
                       'meters_per_unit': args.meters_per_unit, 'profile': args.profile,
                       'limits': {'max_voxels': args.max_voxels}}
        return run(request)
    except KeyboardInterrupt:
        emit('cancelled', message='Elaborazione annullata. Nessun IFC incompleto pubblicato.')
        return 130
    except Exception as exc:
        emit('error', message=f'{type(exc).__name__}: {exc}')
        return 1
