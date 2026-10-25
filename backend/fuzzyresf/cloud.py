"""Bounded-memory input, 5 cm representative voxels and local PCA normals.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import hashlib
import math
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree
from scipy.signal import find_peaks

CHUNK = 250_000


def chunks(path, fmt):
    if fmt == 'xyz64':
        if path.stat().st_size % 24:
            raise ValueError('XYZ64 troncato: servono triple float64 little-endian.')
        with path.open('rb') as handle:
            while True:
                points = np.fromfile(handle, dtype='<f8', count=CHUNK * 3)
                if not len(points):
                    break
                yield points.reshape(-1, 3)
    elif fmt in {'las', 'laz'}:
        import laspy
        with laspy.open(path) as cloud:
            for points in cloud.chunk_iterator(CHUNK):
                yield np.column_stack((points.x, points.y, points.z))
    else:
        raise ValueError('Formato non supportato: usare LAS/LAZ o esportare da CloudCompare.')


def analyse(path, fmt, meters_per_unit, max_voxels, progress, expected_count=None):
    path = Path(path)
    count = 0
    low = np.full(3, np.inf)
    high = -low
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b''):
            digest.update(block)
    progress(5, 'Controllo coordinate e unità')
    for points in chunks(path, fmt):
        points *= meters_per_unit
        if not np.isfinite(points).all():
            raise ValueError('La nuvola contiene coordinate NaN/Inf; pulirla prima di proseguire.')
        count += len(points)
        low = np.minimum(low, points.min(0))
        high = np.maximum(high, points.max(0))
    if count < 500:
        raise ValueError('Nuvola insufficiente: almeno 500 punti richiesti.')
    if expected_count is not None and count != expected_count:
        raise ValueError('Il conteggio punti non corrisponde al contratto di esportazione.')
    extent = high - low
    if not np.isfinite(extent).all() or np.any(extent > 10000):
        raise ValueError('Estensione eccessiva: verificare unità e ritagliare un edificio.')
    # Keep metric arithmetic close to origin while storing the exact world offset.
    origin = np.floor(low)
    local_low = low - origin
    dims = np.ceil(extent / .05).astype(np.int64) + 1
    if math.prod(map(int, dims)) >= np.iinfo(np.int64).max:
        raise ValueError('Griglia voxel oltre il limite int64.')
    keys = np.empty(0, dtype=np.int64)
    representatives = np.empty((0, 3), dtype=np.float64)
    processed = 0
    for points in chunks(path, fmt):
        points = points * meters_per_unit - origin
        ijk = np.floor((points - local_low) / .05).astype(np.int64)
        key = (ijk[:, 0] * dims[1] + ijk[:, 1]) * dims[2] + ijk[:, 2]
        new_keys, first = np.unique(key, return_index=True)
        unseen = ~np.isin(new_keys, keys, assume_unique=True)
        if len(keys) + int(unseen.sum()) > max_voxels:
            raise ValueError(f'Limite di {max_voxels:,} voxel superato. Ritagliare un livello o aumentare il limite con RAM adeguata.')
        joined = np.concatenate((keys, new_keys[unseen]))
        order = np.argsort(joined)
        representatives = np.concatenate((representatives, points[first[unseen]]))[order]
        keys = joined[order]
        processed += len(points)
        progress(10 + int(20 * processed / count), 'Campionamento voxel 5 cm')
    if len(representatives) < 500:
        raise ValueError('Supporto spaziale insufficiente dopo il campionamento voxel.')
    tree = cKDTree(representatives)
    normals = np.empty_like(representatives)
    planarity = np.empty(len(representatives))
    for start in range(0, len(representatives), 20_000):
        stop = start + 20_000
        _, ids = tree.query(representatives[start:stop], k=20, workers=2)
        neighbours = representatives[ids]
        neighbours -= neighbours.mean(1)[:, None, :]
        cov = np.einsum('nki,nkj->nij', neighbours, neighbours) / 20
        eigenvalues, eigenvectors = np.linalg.eigh(cov)
        normals[start:stop] = eigenvectors[:, :, 0]
        planarity[start:stop] = (eigenvalues[:, 1] - eigenvalues[:, 0]) / np.maximum(eigenvalues[:, 2], 1e-15)
        progress(30 + int(20 * min(stop, len(representatives)) / len(representatives)), 'Normali PCA e planarità')
    horizontal = (abs(normals[:, 2]) > .95) & (planarity > .3)
    vertical = (abs(normals[:, 2]) < .2) & (planarity > .3)
    edges = np.arange(local_low[2] - .02, high[2] - origin[2] + .04001, .02)
    hist = np.histogram(representatives[horizontal, 2], edges)[0]
    peaks, _ = find_peaks(hist, distance=30, prominence=max(1, hist.max() * .02))
    peaks = sorted(peaks, key=lambda i: hist[i], reverse=True)
    angles = np.arctan2(normals[vertical, 1], normals[vertical, 0])
    concentration = float(abs(np.mean(np.exp(4j * angles)))) if len(angles) else 0.
    audit = {
        'source': path.name, 'sha256': digest.hexdigest(), 'actual_count': count,
        'meters_per_unit': meters_per_unit, 'coordinate_origin_m': origin.tolist(),
        'actual_min_m': low.tolist(), 'actual_max_m': high.tolist(),
        'occupied_voxels_5cm': len(keys), 'voxel_m': .05,
        'horizontal_peaks': [{'z': float((edges[i] + edges[i+1]) / 2), 'voxel_count_bin': int(hist[i])} for i in peaks[:6]],
        'fourfold_concentration': concentration,
        'horizontal_planar': int(horizontal.sum()), 'vertical_planar': int(vertical.sum()),
        'reference_used': False,
    }
    return representatives, normals, planarity, audit
