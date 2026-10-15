#!/usr/bin/env python3
"""Deterministic synthetic input. Labels are ground truth, NOT model predictions."""
import argparse
import json
from pathlib import Path
import numpy as np


def generate(output, seed=2026):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    chunks, tags = [], []
    # Two openings in the y=0 wall; returns on their frames carry known labels.
    for y in (0., 4.):
        x, z = rng.uniform(0, 6, 15000), rng.uniform(0, 3, 15000)
        keep = np.ones(len(x), dtype=bool)
        label = np.zeros(len(x), dtype=int)
        if y == 0:
            door = (x > .8) & (x < 1.8) & (z < 2.1)
            window = (x > 3.4) & (x < 4.6) & (z > 1.0) & (z < 2.2)
            door_frame = (x > .62) & (x < 1.98) & (z < 2.28)
            window_frame = (x > 3.22) & (x < 4.78) & (z > .82) & (z < 2.38)
            label[door_frame] = 1
            label[window_frame] = 2
            keep &= ~(door | window)
        chunks.append(np.column_stack((x[keep], y + rng.normal(0, .002, keep.sum()), z[keep])))
        tags.append(label[keep])
    for x in (0., 6.):
        n = 10000
        chunks.append(np.column_stack((x + rng.normal(0, .002, n), rng.uniform(0, 4, n), rng.uniform(0, 3, n))))
        tags.append(np.zeros(n, dtype=int))
    for z in (0., 3.):
        n = 18000
        chunks.append(np.column_stack((rng.uniform(0, 6, n), rng.uniform(0, 4, n), z + rng.normal(0, .001, n))))
        tags.append(np.full(n, 3))
    # A vertical furniture panel must not silently become an architectural wall.
    n = 5000
    chunks.append(np.column_stack((rng.uniform(2, 4, n), 2 + rng.normal(0, .002, n), rng.uniform(0, 1.6, n))))
    tags.append(np.full(n, 4))
    points, labels = np.vstack(chunks), np.concatenate(tags)
    angle = np.radians(17)
    points[:, :2] = points[:, :2] @ np.array([[np.cos(angle), np.sin(angle)], [-np.sin(angle), np.cos(angle)]])
    np.savetxt(output / "room.xyz", points, fmt="%.6f")
    probabilities = {}
    for name, tag in (("wall", 0), ("door", 1), ("window", 2), ("horizontal", 3), ("furniture", 4)):
        probabilities[name] = np.where(labels == tag, .98, .001)
    np.savez_compressed(output / "semantics.npz", **probabilities)
    request = {"version": "8.0-stepwise-semantic", "input": "room.xyz", "semantics": "semantics.npz",
        "output": "run_v8", "coordinate_origin": [0, 0, 0], "storeys": [[0, 3]],
        "voxel_m": .03, "raster_cell_m": .10, "export_ifc": True,
        "dataset_note": "Synthetic geometry and ground truth labels; not a trained classifier or real scan benchmark"}
    (output / "request.json").write_text(json.dumps(request, indent=2), encoding="utf-8")
    print(output / "request.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    generate(args.output)
