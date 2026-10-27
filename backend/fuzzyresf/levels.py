"""Evidence-gated floor/ceiling pairing; no fallback to fabricated storeys."""
import numpy as np


def select_level(P, N, PL, audit):
    horizontal = (abs(N[:, 2]) > 0.95) & (PL > 0.3)
    surfaces = []
    for peak in audit['horizontal_peaks']:
        q = P[horizontal & (abs(P[:, 2] - peak['z']) < 0.08)]
        if not len(q):
            continue
        cells = set(map(tuple, np.floor(q[:, :2] / 0.20).astype(np.int64)))
        surfaces.append({'z': float(np.median(q[:, 2])), 'cells': cells,
                         'area': len(cells) * 0.04, 'n': len(q)})
    pairs = []
    for i, lower in enumerate(surfaces):
        for upper in surfaces[i + 1:]:
            a, b = sorted([lower, upper], key=lambda s: s['z'])
            height = b['z'] - a['z']
            if not 1.8 <= height <= 8.0 or min(a['area'], b['area']) < 5.0:
                continue
            shared = a['cells'] & b['cells']
            small_overlap = len(shared) / min(len(a['cells']), len(b['cells']))
            large_overlap = len(shared) / max(len(a['cells']), len(b['cells']))
            if small_overlap < 0.60 or large_overlap < 0.35:
                continue
            # A substantial observed intermediate surface makes a tall pair
            # inconsistent with a single unobstructed storey.
            intermediate = any(
                a['z'] + 0.5 < s['z'] < b['z'] - 0.5
                and s['area'] >= 0.35 * min(a['area'], b['area'])
                and len(s['cells'] & shared) / max(1, len(shared)) >= 0.35
                for s in surfaces)
            if intermediate:
                continue
            pairs.append({'floor_z': a['z'], 'ceiling_z': b['z'],
                          'height_m': height, 'overlap_smaller': small_overlap,
                          'overlap_larger': large_overlap,
                          'score': len(shared) * np.sqrt(small_overlap * large_overlap)})
    pairs.sort(key=lambda s: (-s['score'], s['floor_z']))
    if not pairs:
        ranked = surfaces[:2]
        detail = ''
        if len(ranked) == 2:
            origin_z = audit.get('coordinate_origin_m', [0, 0, 0])[2]
            heights = sorted(s['z'] + origin_z for s in ranked)
            detail = (f' Two dominant elevations: {heights[0]:.3f} and {heights[1]:.3f} m; '
                      f'separation {heights[1] - heights[0]:.3f} m.')
        raise ValueError('No supported floor/ceiling pair: requires a '
                         '1.8–8 m height and overlapping plan evidence.' + detail +
                         ' Building surfaces can still be reconstructed; '
                         'rooms require a surveyed floor and ceiling on one level.')
    best = pairs[0]
    for other in pairs[1:]:
        disjoint = (other['ceiling_z'] <= best['floor_z'] + 0.2
                    or other['floor_z'] >= best['ceiling_z'] - 0.2)
        if disjoint and other['score'] >= 0.85 * best['score']:
            raise ValueError('Multiple plausible interior levels: crop one storey for room reconstruction.')
    return best
