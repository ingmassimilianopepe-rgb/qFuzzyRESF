from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import numpy as np
import pytest


@pytest.fixture
def two_rooms():
    """Dense noisy scan of two adjacent 4x5 m rooms, single-face partitions."""
    step = 0.04
    x = np.arange(0, 8 + step / 2, step)
    y = np.arange(0, 5 + step / 2, step)
    z = np.arange(0, 3 + step / 2, step)
    pieces = []
    xx, yy = np.meshgrid(x, y, indexing="ij")
    for level in [0.0, 3.0]:
        pieces.append(np.c_[xx.ravel(), yy.ravel(), np.full(xx.size, level)])
    yy, zz = np.meshgrid(y, z, indexing="ij")
    for wall in [0.0, 4.0, 8.0]:
        pieces.append(np.c_[np.full(yy.size, wall), yy.ravel(), zz.ravel()])
    xx, zz = np.meshgrid(x, z, indexing="ij")
    for wall in [0.0, 5.0]:
        pieces.append(np.c_[xx.ravel(), np.full(xx.size, wall), zz.ravel()])
    points = np.vstack(pieces)
    return points + np.random.default_rng(18).normal(0, 0.001, points.shape)
