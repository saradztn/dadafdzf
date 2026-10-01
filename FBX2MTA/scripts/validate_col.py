#!/usr/bin/env python3
"""Standalone COL validator - parses the .col with DragonFF's OWN col module
(gtaLib/col.py from the official Parik27/DragonFF) and verifies:

  1. file exists and size > 0                      (no empty/fake COL)
  2. valid COL structure (parses cleanly, magic header)
  3. at least one model with a real SA version (COLL/COL2/COL3)
  4. bounds: finite, min <= max, non-zero volume
  5. collision geometry EXISTS: mesh faces > 0 (or spheres/boxes > 0)
  6. vertices valid: finite coordinates, real 3D positions
  7. face indices valid: all in [0, vert_count), faces non-degenerate

Writes a JSON report next to the file and exits 0 (valid) / 1 (invalid).

Usage:
    python3 validate_col.py <file.col> [--report out.json]
"""
import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                 "dragonff", "DragonFF")))
from gtaLib.col import coll  # noqa: E402  (DragonFF's own GTA COL parser)


def _finite(*vals):
    return all(math.isfinite(float(v)) for v in vals)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--report", default="")
    args = ap.parse_args()
    path = args.file
    report = {"file": path, "valid": False, "errors": [], "checks": {},
              "info": {}}

    # 1. exists + non-empty
    if not os.path.exists(path):
        report["errors"].append("file does not exist")
        _finish(report, args.report)
        return
    size = os.path.getsize(path)
    if size == 0:
        report["errors"].append("file is empty (0 bytes) - fake/empty COL")
        _finish(report, args.report)
        return
    report["info"]["size_bytes"] = size

    # 2. parse with DragonFF's col module
    try:
        c = coll()
        c.load_file(path)
    except Exception as e:
        report["errors"].append(f"COL structure invalid - parse error: {e}")
        _finish(report, args.report)
        return
    models = c.models
    if not models:
        report["errors"].append("no COL models found in file")
        _finish(report, args.report)
        return

    m = models[0]
    report["info"]["models"] = len(models)
    report["info"]["model_name"] = m.model_name
    report["info"]["version"] = m.version

    # 3. real GTA SA version
    if m.version not in (1, 2, 3):
        report["errors"].append(f"unsupported COL version: {m.version}")
        _finish(report, args.report)
        return

    # 4. bounds
    b = m.bounds
    bounds_ok = False
    try:
        # bound vectors come back as plain (x, y, z) float tuples
        bmin, bmax = b.min, b.max
        mn = (float(bmin[0]), float(bmin[1]), float(bmin[2]))
        mx = (float(bmax[0]), float(bmax[1]), float(bmax[2]))
        ok = (_finite(*mn, *mx)
              and mn[0] <= mx[0] and mn[1] <= mx[1] and mn[2] <= mx[2])
        if ok:
            vol = (mx[0] - mn[0]) * (mx[1] - mn[1]) * (mx[2] - mn[2])
            if vol <= 0:
                report["errors"].append(f"bounds degenerate (volume {vol:.4f})")
            else:
                bounds_ok = True
                report["info"]["bounds_min"] = list(mn)
                report["info"]["bounds_max"] = list(mx)
                report["info"]["bounds_size"] = [mx[0] - mn[0],
                                                 mx[1] - mn[1],
                                                 mx[2] - mn[2]]
        else:
            report["errors"].append("bounds invalid (non-finite or min > max)")
    except Exception as e:
        report["errors"].append(f"bounds missing/unreadable: {e}")
    report["checks"]["bounds"] = bounds_ok

    # 5. collision geometry exists
    nv = len(m.mesh_verts)
    nf = len(m.mesh_faces)
    ns = len(m.spheres)
    nb = len(m.boxes)
    report["info"]["mesh_vertices"] = nv
    report["info"]["mesh_triangles"] = nf
    report["info"]["spheres"] = ns
    report["info"]["boxes"] = nb
    if nf == 0 and ns == 0 and nb == 0:
        report["errors"].append("no collision geometry (0 faces, 0 spheres, 0 boxes)")
        _finish(report, args.report)
        return
    report["checks"]["geometry_exists"] = True

    # 6. vertices valid
    bad_v = 0
    span = [0.0, 0.0, 0.0]
    for v in m.mesh_verts:
        # vertices come back as (x, y, z) float tuples (dequantized /128)
        x, y, z = float(v[0]), float(v[1]), float(v[2])
        if not _finite(x, y, z):
            bad_v += 1
            continue
        span[0] = max(span[0], abs(x)); span[1] = max(span[1], abs(y)); span[2] = max(span[2], abs(z))
    if bad_v:
        report["errors"].append(f"{bad_v} vertex coordinate(s) are NaN/Infinity")
    if nv and max(span) == 0.0:
        report["errors"].append("all collision vertices are at (0,0,0)")
    report["checks"]["vertices_valid"] = bad_v == 0 and max(span) > 0.0
    report["info"]["vertex_extent"] = span

    # 7. face indices valid + non-degenerate
    bad_i = 0
    deg = 0
    for f in m.mesh_faces:
        a, b2, cidx = int(f.a), int(f.b), int(f.c)
        if not (0 <= a < nv and 0 <= b2 < nv and 0 <= cidx < nv):
            bad_i += 1
            continue
        if a == b2 or b2 == cidx or a == cidx:
            deg += 1
    if bad_i:
        report["errors"].append(f"{bad_i} face(s) with out-of-range vertex index")
    if deg:
        report["errors"].append(f"{deg} degenerate face(s) (duplicate indices)")
    report["checks"]["face_indices_valid"] = bad_i == 0 and deg == 0
    report["info"]["degenerate_faces"] = deg

    if bounds_ok:
        report["checks"]["structure"] = True

    report["valid"] = not report["errors"]
    _finish(report, args.report)


def _finish(report, report_path):
    out_path = report_path or os.path.splitext(report["file"])[0] + ".validation.json"
    try:
        with open(out_path, "w") as f:
            json.dump(report, f, indent=2)
    except Exception:
        pass
    if report["valid"]:
        i = report["info"]
        print(f"COL VALIDATION PASSED: {os.path.basename(report['file'])} "
              f"({i.get('size_bytes', 0)} bytes, {i.get('mesh_triangles', 0)} "
              f"collision triangles, {i.get('mesh_vertices', 0)} vertices)")
        sys.exit(0)
    print("COL VALIDATION FAILED: " + "; ".join(report["errors"]))
    sys.exit(1)


if __name__ == "__main__":
    main()
