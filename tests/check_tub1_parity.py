#!/usr/bin/env python3

import argparse
import csv
import json
from pathlib import Path

from shapely.geometry import Polygon, box
from shapely.ops import unary_union


def frozen_polygon(row):
    d = float(row["d"])
    a = float(row["a"])
    b = float(row["b"])
    t = float(row["thickness"])
    if row["ori"].strip().lower().startswith("v"):
        return box(d - t / 2.0, a, d + t / 2.0, b)
    return box(a, d - t / 2.0, b, d + t / 2.0)


def generated_polygon(row):
    x1, y1 = float(row["x1"]), float(row["y1"])
    x2, y2 = float(row["x2"]), float(row["y2"])
    t = float(row["thickness"])
    dx, dy = x2 - x1, y2 - y1
    length = (dx * dx + dy * dy) ** 0.5
    if length <= 1e-12:
        return None
    nx, ny = -dy / length, dx / length
    h = t / 2.0
    return Polygon([
        (x1 + nx * h, y1 + ny * h),
        (x2 + nx * h, y2 + ny * h),
        (x2 - nx * h, y2 - ny * h),
        (x1 - nx * h, y1 - ny * h),
    ])


def read_rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--generated", required=True)
    ap.add_argument("--expected", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--threshold", type=float, default=0.95)
    args = ap.parse_args()

    expected_rows = read_rows(args.expected)
    generated_rows = read_rows(args.generated)

    # The frozen paper model contains accepted wall instances only. For qFuzzyRESF,
    # compare the structurally accepted states and keep the all-hypothesis score as
    # a diagnostic so false structural promotion is visible.
    accepted_states = {"Permanent", "OccludedPermanent"}
    accepted_rows = [r for r in generated_rows if r.get("state", "") in accepted_states]

    expected = unary_union([frozen_polygon(r) for r in expected_rows])

    def score(rows):
        polys = [generated_polygon(r) for r in rows]
        polys = [p for p in polys if p is not None and not p.is_empty]
        if not polys:
            return {"precision": 0.0, "recall": 0.0, "f1": 0.0, "iou": 0.0, "area": 0.0}
        pred = unary_union(polys)
        inter = pred.intersection(expected).area
        precision = inter / pred.area if pred.area else 0.0
        recall = inter / expected.area if expected.area else 0.0
        f1 = 2.0 * precision * recall / (precision + recall) if precision + recall else 0.0
        union = pred.union(expected).area
        return {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "iou": inter / union if union else 0.0,
            "area": pred.area,
        }

    accepted_score = score(accepted_rows)
    all_score = score(generated_rows)
    report = {
        "expected_wall_count": len(expected_rows),
        "generated_hypothesis_count": len(generated_rows),
        "generated_accepted_count": len(accepted_rows),
        "expected_union_area_m2": expected.area,
        "accepted_vs_frozen": accepted_score,
        "all_hypotheses_vs_frozen": all_score,
        "pass_threshold_f1": args.threshold,
        "passed": accepted_score["f1"] >= args.threshold,
    }

    Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["passed"] else 3)


if __name__ == "__main__":
    main()
