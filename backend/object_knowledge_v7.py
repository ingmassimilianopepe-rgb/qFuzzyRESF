"""Object-knowledge layer for Fuzzy-RESF-BIM v7.

The layer deliberately separates three sources of information:
1. measured geometry and Fuzzy-RESF confidence (primary evidence),
2. repeated dimensions learned from the current scan (building-specific abacus),
3. standards/legal references used only as soft plausibility/compliance metadata.

It never fabricates an object because a standard says that such an object should exist.
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import json
import math
from typing import Iterable

import numpy as np

import bim_reconstruction as base


_REGISTRY_PATH = Path(__file__).with_name("standards_object_priors_v7.json")


def load_registry() -> dict:
    if not _REGISTRY_PATH.exists():
        return {
            "policy": {
                "name": "standards-aware soft object priors",
                "geometry_rule": "standards never override measured geometry",
                "certification": False,
            },
            "information_standards": [],
            "accessibility": {},
            "product_and_component_standards": [],
            "scan_derived_abacus": {"enabled": True},
        }
    return json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))


def _is_exterior_wall(w: base.Wall) -> bool:
    src = str(getattr(w, "source", ""))
    return src.startswith("closed_facade_envelope") or "facade" in src.lower() or "exterior" in src.lower()


def _wall_lookup(walls: Iterable[base.Wall]) -> dict[int, base.Wall]:
    return {int(w.id): w for w in walls}


def _soft_gate(value: float, lo: float, hi: float, shoulder: float = 0.12) -> float:
    """Continuous plausibility membership; 1 inside [lo,hi], decays outside."""
    if lo <= value <= hi:
        return 1.0
    if value < lo:
        return float(math.exp(-0.5 * ((lo - value) / max(shoulder, 1e-6)) ** 2))
    return float(math.exp(-0.5 * ((value - hi) / max(shoulder, 1e-6)) ** 2))


def _opening_key(o: base.Opening, w: base.Wall | None) -> tuple[str, str]:
    kind = "Door" if str(o.kind).lower() == "door" else "Window"
    location = "external" if (w is not None and _is_exterior_wall(w)) else "internal"
    return kind, location


def _cluster_openings(items: list[tuple[base.Opening, base.Wall | None]], kind: str, location: str,
                      width_tol: float, height_tol: float, sill_tol: float) -> list[list[tuple[base.Opening, base.Wall | None]]]:
    members = [(o, w) for o, w in items if _opening_key(o, w) == (kind, location)]
    members.sort(key=lambda x: (float(x[0].width), float(x[0].height), float(x[0].sill)))
    groups: list[list[tuple[base.Opening, base.Wall | None]]] = []
    for item in members:
        o, _ = item
        best = None
        best_cost = 1e9
        for gi, g in enumerate(groups):
            gw = float(np.median([x[0].width for x in g]))
            gh = float(np.median([x[0].height for x in g]))
            gs = float(np.median([x[0].sill for x in g]))
            dw, dh, ds = abs(o.width - gw), abs(o.height - gh), abs(o.sill - gs)
            if dw <= width_tol and dh <= height_tol and (kind == "Door" or ds <= sill_tol):
                cost = dw / max(width_tol, 1e-9) + dh / max(height_tol, 1e-9)
                if kind == "Window":
                    cost += ds / max(sill_tol, 1e-9)
                if cost < best_cost:
                    best, best_cost = gi, cost
        if best is None:
            groups.append([item])
        else:
            groups[best].append(item)
    return groups


def build_opening_abacus(openings: list[base.Opening], walls: list[base.Wall], registry: dict | None = None) -> list[dict]:
    registry = registry or load_registry()
    cfg = registry.get("scan_derived_abacus", {})
    wt = float(cfg.get("max_width_difference_m", 0.16))
    ht = float(cfg.get("max_height_difference_m", 0.18))
    st = float(cfg.get("max_sill_difference_m", 0.18))
    min_n = int(cfg.get("min_cluster_size", 2))
    by = _wall_lookup(walls)
    items = [(o, by.get(int(o.wall_id))) for o in openings]
    entries = []
    tid = 1
    for kind in ("Door", "Window"):
        for location in ("external", "internal"):
            for group in _cluster_openings(items, kind, location, wt, ht, st):
                if len(group) < min_n:
                    continue
                vals_w = np.asarray([x[0].width for x in group], float)
                vals_h = np.asarray([x[0].height for x in group], float)
                vals_s = np.asarray([x[0].sill for x in group], float)
                conf = np.asarray([x[0].confidence for x in group], float)
                entry = {
                    "type_id": f"{kind.upper()}_{location.upper()}_{tid:02d}",
                    "object_class": kind,
                    "location": location,
                    "count": int(len(group)),
                    "width_median_m": float(np.median(vals_w)),
                    "height_median_m": float(np.median(vals_h)),
                    "sill_median_m": float(np.median(vals_s)),
                    "width_mad_m": float(np.median(np.abs(vals_w - np.median(vals_w)))),
                    "height_mad_m": float(np.median(np.abs(vals_h - np.median(vals_h)))),
                    "mean_detection_confidence": float(np.mean(conf)),
                    "source": "scan_derived_repeated_dimension_cluster",
                    "normative_geometry": False,
                }
                if kind == "Door":
                    entry["standards_context"] = ["D.M. 236/1989", "UNI EN 14351-1:2016" if location == "external" else "UNI EN 14351-2:2018"]
                else:
                    entry["standards_context"] = ["UNI EN 14351-1:2016" if location == "external" else "UNI EN 14351-2:2018"]
                entries.append(entry)
                tid += 1
    return entries


def _match_abacus(o: base.Opening, w: base.Wall | None, abacus: list[dict]) -> tuple[dict | None, float]:
    kind, location = _opening_key(o, w)
    best, best_score = None, 0.0
    for a in abacus:
        if a.get("object_class") != kind or a.get("location") != location:
            continue
        sw = max(0.10, 2.0 * float(a.get("width_mad_m", 0.0)) + 0.05)
        sh = max(0.12, 2.0 * float(a.get("height_mad_m", 0.0)) + 0.06)
        ss = max(0.12, 0.18)
        dw = (o.width - float(a["width_median_m"])) / sw
        dh = (o.height - float(a["height_median_m"])) / sh
        ds = (o.sill - float(a["sill_median_m"])) / ss if kind == "Window" else 0.0
        score = float(math.exp(-0.5 * (dw * dw + dh * dh + 0.5 * ds * ds)))
        score *= float(np.clip(a.get("count", 2) / 4.0, 0.5, 1.0))
        if score > best_score:
            best, best_score = a, score
    return best, best_score


def _door_accessibility_score(o: base.Opening, w: base.Wall | None, registry: dict) -> tuple[float, str]:
    acc = registry.get("accessibility", {})
    if not acc:
        return 0.5, "not_assessed"
    threshold = float(acc.get("door_access_min_clear_width_m", 0.80) if (w is not None and _is_exterior_wall(w))
                      else acc.get("other_door_min_clear_width_m", 0.75))
    # Wall-opening width is not certified clear passage. A value below the legal minimum is
    # therefore negative evidence; a value above it is only neutral/compatible, never proof.
    if o.width < threshold - 0.08:
        score = float(np.clip((o.width - 0.45) / max(threshold - 0.08 - 0.45, 1e-6), 0.0, 1.0))
        return score, f"detected_opening_below_DM236_clear_width_proxy_{threshold:.2f}m"
    return 0.72, f"dimension_compatible_but_clear_width_not_certified_{threshold:.2f}m"


def refine_openings_with_abacus(openings: list[base.Opening], walls: list[base.Wall], registry: dict | None = None):
    """Fuse scan evidence with a building-specific object abacus and soft standards context."""
    registry = registry or load_registry()
    abacus = build_opening_abacus(openings, walls, registry)
    by = _wall_lookup(walls)
    blend = float(registry.get("scan_derived_abacus", {}).get("geometry_blend", 0.20))
    out = []
    for o in openings:
        w = by.get(int(o.wall_id))
        kind = "Door" if str(o.kind).lower() == "door" else "Window"
        # Broad scan plausibility only. These are deliberately not labelled as normative sizes.
        if kind == "Door":
            geom = _soft_gate(float(o.width), 0.52, 2.40, 0.16) * _soft_gate(float(o.height), 1.55, 2.75, 0.20)
            std_score, compliance = _door_accessibility_score(o, w, registry)
        else:
            geom = _soft_gate(float(o.width), 0.28, 2.80, 0.18) * _soft_gate(float(o.height), 0.32, 2.35, 0.18)
            geom *= _soft_gate(float(o.sill), 0.15, 1.95, 0.18)
            std_score, compliance = 0.55, "product_standard_context_only_no_nominal_size"
        if geom < 0.12:
            continue
        a, type_score = _match_abacus(o, w, abacus)
        final = float(np.clip(0.72 * o.confidence + 0.16 * type_score + 0.08 * geom + 0.04 * std_score, 0, 0.985))
        if final < 0.42:
            continue
        if a is not None and int(a.get("count", 0)) >= 3:
            dw = abs(o.width - float(a["width_median_m"]))
            dh = abs(o.height - float(a["height_median_m"]))
            if dw <= 0.10 and dh <= 0.10:
                # Mild type stabilization only when repeated scan evidence is already close.
                o.width = float((1.0 - blend) * o.width + blend * float(a["width_median_m"]))
                o.height = float((1.0 - blend) * o.height + blend * float(a["height_median_m"]))
                if kind == "Window" and abs(o.sill - float(a["sill_median_m"])) <= 0.10:
                    o.sill = float((1.0 - blend) * o.sill + blend * float(a["sill_median_m"]))
        o.confidence = final
        o.source = str(o.source) + "+scan_abacus_soft_prior"
        setattr(o, "type_id", a.get("type_id") if a is not None else "UNIQUE_SCAN_INSTANCE")
        setattr(o, "type_prior_score", float(type_score))
        setattr(o, "standards_prior_score", float(std_score))
        setattr(o, "standards_status", compliance)
        out.append(o)
    for i, o in enumerate(out, 1):
        o.id = i
    return out, abacus


def standards_alignment_payload(registry: dict | None = None) -> dict:
    registry = registry or load_registry()
    return {
        "policy": registry.get("policy", {}),
        "information_standards": registry.get("information_standards", []),
        "accessibility": registry.get("accessibility", {}),
        "product_and_component_standards": registry.get("product_and_component_standards", []),
        "important_note": "Standards are not used as a hidden reference BIM. Except for public D.M. 236/1989 accessibility dimensions, no proprietary normative numeric tables are embedded. Product standards are context/metadata unless exact licensed clauses are supplied by the user.",
    }


def object_abacus_payload(abacus: list[dict]) -> dict:
    return {
        "version": "7.0",
        "source": "current_scan_only",
        "types": abacus,
        "meaning": "Repeated dimensions are scan-derived type hypotheses, not manufacturer catalogue matches and not proof of regulatory compliance.",
    }
