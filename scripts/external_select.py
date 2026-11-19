"""外部数据集筛选：从 MFCAD（及之后 Durham）里挑出可跑本项目全链的零件。

筛选口径（三个都要满足）：

1. **高度层数 3–10**：本项目的主难度因子是层数（`docs/M3-FINAL.md`），
   1–2 层的件测不出区域掩码的价值；
2. **面数 8–60**：太小没有特征，太大超出 10 件锚点集的量级；
3. **最小边 ≥ 6 mm**：刀径要按 bbox 自适应（Ø = min_edge/10），太小则刀具不合理。

标签映射（MFCAD 15 类 → 本项目 family）：

    pocket 0/1/2/3      → pocket
    step  10/11/12      → step
    hole  13/14         → hole
    stock 15            → 不建特征（毛坯面）

    PYTHONPATH=src python scripts/external_select.py --dataset mfcad --limit 0
    PYTHONPATH=src python scripts/external_select.py --dataset mfcad --limit 50   # 试跑
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cadcam.adapters import freecad  # noqa: E402
from cadcam.verify import dexel  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MFCAD = os.path.join(ROOT, "data", "inbox", "MFCAD", "dataset", "step")
DURHAM = os.path.join(ROOT, "data", "inbox", "durham")
OUT_DIR = os.path.join(ROOT, "data", "external")
GEOM_DIR = os.path.join(OUT_DIR, "geom")
CACHE = os.path.join(ROOT, "build", "external_geom")
MANIFEST = os.path.join(OUT_DIR, "MANIFEST.json")

FAMILY = {0: "pocket", 1: "pocket", 2: "pocket", 3: "pocket",
          10: "step", 11: "step", 12: "step",
          13: "hole", 14: "hole"}
MAPPABLE = set(FAMILY)


def _read_face_truth(path):
    import pickle
    with open(path, "rb") as fh:
        return pickle.load(fh)


def _geom(step_path, key):
    """提一次几何（含三角网格），缓存到 build/external_geom。"""
    os.makedirs(CACHE, exist_ok=True)
    out = os.path.join(CACHE, key + ".json")
    if not os.path.exists(out):
        env = dict(os.environ, CADCAM_STEP=os.path.abspath(step_path),
                   CADCAM_OUT=os.path.abspath(out))
        try:
            subprocess.run([freecad.FREECAD_CMD,
                            os.path.join(ROOT, "scripts", "annotate", "geom_worker.py")],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, timeout=180)
        except subprocess.TimeoutExpired:
            return None
    if not os.path.exists(out):
        return None
    try:
        g = json.load(open(out, encoding="utf-8"))
    except Exception:                                   # noqa: BLE001
        return None
    return None if "error" in g else g


def probe(step_path, key, face_truth_path, min_edge=6.0, res=None):
    g = _geom(step_path, key)
    if g is None:
        return None
    bb = g["bbox"]
    size = [float(bb["max"][i]) - float(bb["min"][i]) for i in range(3)]
    if min(size) < min_edge:
        return {"id": key, "reject": "太小", "size": [round(v, 2) for v in size]}
    step = res or max(0.2, min(size) / 40.0)
    t = dexel.rasterize(g, res=step)
    top = t["top"]
    fin = np.isfinite(top)
    levels = np.unique(np.round(top[fin], 1))
    if not 3 <= len(levels) <= 10:
        return {"id": key, "reject": "层数 %d" % len(levels),
                "size": [round(v, 2) for v in size], "levels": int(len(levels))}
    if not 8 <= len(g["faces"]) <= 60:
        return {"id": key, "reject": "面数 %d" % len(g["faces"])}
    fams = {}
    if face_truth_path and os.path.exists(face_truth_path):
        labels = _read_face_truth(face_truth_path)
        for i, lab in enumerate(labels, start=1):
            f = FAMILY.get(int(lab))
            if f:
                fams[str(i)] = {"family": f}
    return {"id": key, "ok": True,
            "size": [round(v, 2) for v in size],
            "faces": len(g["faces"]), "levels": int(len(levels)),
            "volume_mm3": round(float(g.get("volume_mm3") or 0), 1),
            "res": round(step, 3), "labeled_faces": len(fams),
            "geom": os.path.join("geom", key + ".json"),
            "labels": fams}


def scan_mfcad(limit=0):
    files = sorted(glob.glob(os.path.join(MFCAD, "*.step")))
    keep, rejects = [], {}
    t0 = time.time()
    for i, p in enumerate(files[:limit] if limit else files):
        stem = os.path.basename(p)[:-5]
        types = []
        try:
            types = [int(x) for x in stem.split("-")[:-1]]
        except ValueError:
            continue
        if len(types) < 3 or not all(t in MAPPABLE for t in types):
            rejects["不可映射"] = rejects.get("不可映射", 0) + 1
            continue
        r = probe(p, "mfcad_" + stem, p[:-5] + ".face_truth")
        if r is None:
            rejects["几何失败"] = rejects.get("几何失败", 0) + 1
        elif r.get("reject"):
            key = r["reject"].split()[0]
            rejects[key] = rejects.get(key, 0) + 1
        else:
            keep.append(r)
        if (i + 1) % 200 == 0:
            print("  ...%d/%d，已留 %d 件，%.0fs"
                  % (i + 1, len(files), len(keep), time.time() - t0), flush=True)
    return keep, rejects


def scan_durham(limit=0):
    """Durham 解压后的结构未定，这里做通用递归扫描（STEP + 同名标注）。"""
    if not os.path.isdir(DURHAM):
        return [], {"目录不存在": 1}
    files = sorted(glob.glob(os.path.join(DURHAM, "**", "*.st*p"), recursive=True))
    keep, rejects = [], {}
    for i, p in enumerate(files[:limit] if limit else files):
        stem = os.path.splitext(os.path.basename(p))[0]
        r = probe(p, "durham_" + stem, None)
        if r is None:
            rejects["几何失败"] = rejects.get("几何失败", 0) + 1
        elif r.get("reject"):
            key = r["reject"].split()[0]
            rejects[key] = rejects.get(key, 0) + 1
        else:
            keep.append(r)
    return keep, rejects


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="mfcad", choices=("mfcad", "durham"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--target", type=int, default=120, help="留够多少件就停")
    args = ap.parse_args()
    os.makedirs(GEOM_DIR, exist_ok=True)

    keep, rejects = (scan_mfcad(args.limit) if args.dataset == "mfcad"
                     else scan_durham(args.limit))
    if len(keep) > args.target:
        # 按层数分层抽样，保证 3–10 层都有
        by = {}
        for r in keep:
            by.setdefault(r["levels"], []).append(r)
        picked, i = [], 0
        while len(picked) < args.target:
            for lv in sorted(by):
                if i < len(by[lv]) and len(picked) < args.target:
                    picked.append(by[lv][i])
            i += 1
            if i > 200:
                break
        keep = picked
    manifest = {"dataset": args.dataset, "generated_at":
                time.strftime("%Y-%m-%d %H:%M:%S"), "kept": len(keep),
                "rejects": rejects,
                "level_hist": {str(k): sum(1 for r in keep if r["levels"] == k)
                               for k in sorted({r["levels"] for r in keep})},
                "items": keep}
    with open(MANIFEST, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)
    print("\n留用 %d 件｜层数分布 %s" % (len(keep), manifest["level_hist"]))
    print("剔除原因：%s" % rejects)
    print("→ %s" % os.path.relpath(MANIFEST, ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
