#!/usr/bin/env python3

import argparse
import math
import os
from pathlib import Path
from urllib.parse import unquote, urljoin
import xml.etree.ElementTree as ET

import numpy as np
import requests

TOKEN = "edzBCDDGkHjSWGC"
WEBDAV = "https://dpv.uvigo.es/public.php/webdav/"
HOST = "https://dpv.uvigo.es"
DAV = "{DAV:}"


def propfind(url, depth=1):
    r = requests.request("PROPFIND", url, headers={"Depth": str(depth)}, auth=(TOKEN, ""), timeout=90)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    items = []
    for resp in root.findall(f"{DAV}response"):
        href = resp.findtext(f"{DAV}href") or ""
        prop = resp.find(f"{DAV}propstat/{DAV}prop")
        if prop is None:
            continue
        rtype = prop.find(f"{DAV}resourcetype")
        is_collection = rtype is not None and rtype.find(f"{DAV}collection") is not None
        size_text = prop.findtext(f"{DAV}getcontentlength") or "0"
        try:
            size = int(size_text)
        except ValueError:
            size = 0
        items.append((href, is_collection, size))
    return items


def discover_tub1_file():
    queue = [(WEBDAV, 0)]
    seen = set()
    candidates = []
    while queue:
        url, level = queue.pop(0)
        if url in seen or level > 5:
            continue
        seen.add(url)
        for href, is_collection, size in propfind(url, 1):
            decoded = unquote(href)
            full = urljoin(HOST, href)
            if full.rstrip("/") == url.rstrip("/"):
                continue
            low = decoded.lower()
            if is_collection:
                if level < 5 and ("tub1" in low or level < 2):
                    queue.append((full, level + 1))
                continue
            if "tub1" not in low:
                continue
            name = Path(decoded).name.lower()
            if any(x in name for x in ("trajectory", "traj", "reference", "ifc")):
                continue
            ext = Path(name).suffix.lower()
            if ext in {".las", ".laz", ".ply", ".xyz", ".pts", ".txt"}:
                rank = {".las": 6, ".laz": 6, ".ply": 5, ".xyz": 4, ".pts": 3, ".txt": 2}[ext]
                bonus = 2 if any(k in name for k in ("cloud", "point", "tub1")) else 0
                candidates.append((rank + bonus, size, full, name))
    if not candidates:
        raise RuntimeError("No TUB1 point-cloud file could be discovered in the public ISPRS share")
    candidates.sort(reverse=True)
    return candidates[0]


def download(url, path):
    with requests.get(url, auth=(TOKEN, ""), stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)


def las_to_xyz(src, dst, max_points):
    import laspy
    with laspy.open(src) as reader:
        total = int(reader.header.point_count)
        stride = max(1, math.ceil(total / max_points))
        global_offset = 0
        written = 0
        with open(dst, "w", encoding="utf-8") as out:
            for chunk in reader.chunk_iterator(1_000_000):
                n = len(chunk)
                ids = np.arange(global_offset, global_offset + n)
                keep = (ids % stride) == 0
                xyz = np.column_stack((chunk.x[keep], chunk.y[keep], chunk.z[keep]))
                np.savetxt(out, xyz, fmt="%.9f %.9f %.9f")
                written += len(xyz)
                global_offset += n
    return total, written


def ascii_to_xyz(src, dst, max_points):
    valid = 0
    with open(src, "r", errors="ignore") as f:
        for line in f:
            parts = line.replace(",", " ").split()
            if len(parts) < 3:
                continue
            try:
                float(parts[0]); float(parts[1]); float(parts[2])
                valid += 1
            except ValueError:
                pass
    stride = max(1, math.ceil(valid / max_points))
    seen = 0
    written = 0
    with open(src, "r", errors="ignore") as f, open(dst, "w", encoding="utf-8") as out:
        for line in f:
            parts = line.replace(",", " ").split()
            if len(parts) < 3:
                continue
            try:
                x, y, z = map(float, parts[:3])
            except ValueError:
                continue
            if seen % stride == 0:
                out.write(f"{x:.9f} {y:.9f} {z:.9f}\n")
                written += 1
            seen += 1
    return valid, written


def ply_to_xyz(src, dst, max_points):
    from plyfile import PlyData
    ply = PlyData.read(src)
    v = ply["vertex"].data
    total = len(v)
    stride = max(1, math.ceil(total / max_points))
    idx = np.arange(0, total, stride)
    xyz = np.column_stack((v["x"][idx], v["y"][idx], v["z"][idx])).astype(float)
    np.savetxt(dst, xyz, fmt="%.9f %.9f %.9f")
    return total, len(xyz)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--max-points", type=int, default=1_500_000)
    args = ap.parse_args()

    work = Path(args.workdir)
    work.mkdir(parents=True, exist_ok=True)
    score, size, url, name = discover_tub1_file()
    src = work / name
    print(f"Selected TUB1 source: {url} ({size} bytes)")
    download(url, src)

    xyz = work / "tub1_sample.xyz"
    ext = src.suffix.lower()
    if ext in {".las", ".laz"}:
        total, written = las_to_xyz(src, xyz, args.max_points)
    elif ext == ".ply":
        total, written = ply_to_xyz(src, xyz, args.max_points)
    else:
        total, written = ascii_to_xyz(src, xyz, args.max_points)

    meta = work / "tub1_download.txt"
    meta.write_text(
        f"url={url}\nsource={src}\nsource_points={total}\nsample={xyz}\nsample_points={written}\n",
        encoding="utf-8",
    )
    print(meta.read_text())


if __name__ == "__main__":
    main()
