"""把外部数据集（MFCAD）的几何缩放到与 10 件锚点集同一量级，并生成 labels。

为什么要缩放：MFCAD 的件只有 10 mm 量级（体积 ~800 mm³），而我们的刀具是 Ø6、
验收阈值是 0.5 mm。原尺寸下刀具比零件还大，指标没有可比性。
统一缩放到长边 80 mm 后，刀径、栅格、阈值都能直接沿用锚点集的口径。

缩放只改几何缓存里的坐标（bbox / triangles / centroid / area / volume），
STEP 原件不动 —— dexel 与 G-code 都只看这份缓存。

    PYTHONPATH=src python scripts/external_scale.py
    PYTHONPATH=src python scripts/external_scale.py --target-long 80
"""
from __future__ import annotations

import argparse
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "data", "external", "MANIFEST.json")
SRC = os.path.join(ROOT, "build", "external_geom")
OUT_GEOM = os.path.join(ROOT, "data", "external", "geom")
OUT_LAB = os.path.join(ROOT, "data", "external", "labels")


def _scale_point(p, s):
    return [round(float(p[0]) * s, 4), round(float(p[1]) * s, 4), round(float(p[2]) * s, 4)]


def transform(g, s):
    """按比例 s 缩放几何缓存（长度 ×s，面积 ×s²，体积 ×s³）。

    注意两种 bbox 形状：顶层是 {"min","max"}，面级是 6 元 list
    （xmin,ymin,zmin,xmax,ymax,zmax）。
    """
    for f in g["faces"]:
        if f.get("triangles"):
            f["triangles"] = [[_scale_point(p, s) for p in tri] for tri in f["triangles"]]
        if f.get("centroid"):
            f["centroid"] = _scale_point(f["centroid"], s)
        bb = f.get("bbox")
        if isinstance(bb, dict):
            bb["min"] = _scale_point(bb["min"], s)
            bb["max"] = _scale_point(bb["max"], s)
        elif isinstance(bb, (list, tuple)) and len(bb) == 6:
            f["bbox"] = [round(float(v) * s, 4) for v in bb]
        if f.get("area_mm2") is not None:
            f["area_mm2"] = round(float(f["area_mm2"]) * s * s, 4)
    for e in g.get("edges", []):
        if e.get("midpoint"):
            e["midpoint"] = _scale_point(e["midpoint"], s)
        if e.get("length_mm") is not None:
            e["length_mm"] = round(float(e["length_mm"]) * s, 4)
    bb = g.get("bbox")
    if isinstance(bb, dict):
        bb["min"] = _scale_point(bb["min"], s)
        bb["max"] = _scale_point(bb["max"], s)
    if g.get("volume_mm3") is not None:
        g["volume_mm3"] = round(float(g["volume_mm3"]) * s ** 3, 2)
    if g.get("area_mm2") is not None:
        g["area_mm2"] = round(float(g["area_mm2"]) * s * s, 4)
    g["scale_applied"] = round(s, 6)
    return g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target-long", type=float, default=80.0)
    args = ap.parse_args()
    man = json.load(open(MANIFEST, encoding="utf-8"))
    os.makedirs(OUT_GEOM, exist_ok=True)
    os.makedirs(OUT_LAB, exist_ok=True)
    done, skipped = 0, []
    for item in man["items"]:
        key = item["id"]
        src = os.path.join(SRC, key + ".json")
        if not os.path.exists(src):
            skipped.append(key)
            continue
        g = json.load(open(src, encoding="utf-8"))
        size = [item["size"][i] for i in range(3)]
        s = args.target_long / max(size)
        g = transform(g, s)
        g["id"] = key.replace("mfcad_", "")
        g["source"] = "MFCAD/%s" % key.replace("mfcad_", "")
        g["scale_from_size"] = size
        with open(os.path.join(OUT_GEOM, g["id"] + ".json"), "w", encoding="utf-8") as fh:
            json.dump(g, fh, ensure_ascii=False)
        with open(os.path.join(OUT_LAB, g["id"] + ".json"), "w", encoding="utf-8") as fh:
            json.dump({"faces": item["labels"],
                       "note": "MFCAD face_truth 映射：pocket 0-3 / step 10-12 / hole 13-14"},
                      fh, ensure_ascii=False)
        done += 1
    print("缩放 %d 件（长边 %.0f mm）｜跳过 %d" % (done, args.target_long, len(skipped)))
    print("→ %s" % os.path.relpath(OUT_GEOM, ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
