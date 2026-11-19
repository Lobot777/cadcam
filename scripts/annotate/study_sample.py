"""生成人工研究用的题目包（可配置题量/种子/抽样口径）。

与 kappa_sample.py 的区别：那个是 30 题的"快速自检包"（固定分层配额），
这个是给真人做研究用的正式包，题量可配（建议 80），按真实家族分布**比例**抽样，
并且额外保证"难题"（多级腔、腔内岛、角圆角、大直径圆腔）被抽到。

产物目录：reports/annotation/study/<tag>/
  samples.json   题目（无标签）
  img/*.png      每面两视角
  key.json       答案键（含 part/face/家族，仅主试可见）
"""

import argparse
import json
import os
import random
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render as R  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GEOM = os.path.join(ROOT, "reports", "annotation", "geom")
LABELS = os.path.join(ROOT, "data", "zenodo_rptu", "labels")
POLARITY = os.path.join(ROOT, "reports", "annotation", "polarity.json")
PARTS = [str(i) for i in range(1, 11)]

# 这些面在自检里被判错/判得最犹豫，正式研究必须覆盖（不是按家族，而是按"难点"）
HARD = {
    ("3", 14), ("3", 15), ("3", 6),          # 凸台 vs 环形腔
    ("9", 16), ("9", 6), ("9", 25),          # Ø4 销 vs 圆角 vs 大圆腔
    ("10", 5), ("10", 3), ("10", 26),        # 沉孔壁 / 两个底面 / 让位侧壁
    ("7", 2), ("7", 7), ("6", 11),           # 岛顶 / 自由腔壁 / Ø60 圆腔
    ("8", 24), ("8", 16), ("5", 12),         # 圆柱凸台 / 方变圆过渡 / D 形凸台
}


def load(part):
    with open(os.path.join(GEOM, "%s.json" % part), encoding="utf-8") as fh:
        geom = json.load(fh)
    with open(os.path.join(LABELS, "%s.json" % part), encoding="utf-8") as fh:
        lab = json.load(fh)
    return geom, lab


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=80)
    ap.add_argument("--seed", type=int, default=20261105)
    ap.add_argument("--tag", default="main")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    with open(POLARITY, encoding="utf-8") as fh:
        polarity = json.load(fh)
    pool = []
    for part in PARTS:
        geom, lab = load(part)
        for idx_s, meta in lab["faces"].items():
            pool.append({"part": part, "face": int(idx_s), "family": meta["family"]})

    hard = [p for p in pool if (p["part"], p["face"]) in HARD]
    rest = [p for p in pool if (p["part"], p["face"]) not in HARD]

    # 先放难题（最多占 1/3），其余按家族比例填满
    n_hard = min(len(hard), max(12, args.n // 3))
    rng.shuffle(hard)
    picked = hard[:n_hard]
    need = args.n - len(picked)

    by_fam = {}
    for p in rest:
        by_fam.setdefault(p["family"], []).append(p)
    total_rest = float(len(rest))
    alloc = {k: int(round(need * len(v) / total_rest)) for k, v in by_fam.items()}
    # 配额修正
    while sum(alloc.values()) > need:
        k = max(alloc, key=lambda x: alloc[x])
        alloc[k] -= 1
    while sum(alloc.values()) < need:
        k = max(by_fam, key=lambda x: len(by_fam[x]) - alloc[x])
        alloc[k] += 1
    for fam, cnt in alloc.items():
        cand = by_fam[fam][:]
        rng.shuffle(cand)
        picked.extend(cand[:cnt])

    picked.sort(key=lambda d: (d["part"], d["face"]))
    rng.shuffle(picked)
    out = os.path.join(ROOT, "reports", "annotation", "study", args.tag)
    os.makedirs(os.path.join(out, "img"), exist_ok=True)

    geoms = {p: json.load(open(os.path.join(GEOM, "%s.json" % p), encoding="utf-8"))
             for p in PARTS}
    tris_cache = {}

    pack, key = [], {}
    for i, item in enumerate(picked, start=1):
        sid = "Q%03d" % i
        part, fidx = item["part"], item["face"]
        geom = geoms[part]
        if part not in tris_cache:
            tris_cache[part] = R._collect(geom)
        tris, face_of = tris_cache[part]
        fmap = {f["index"]: f for f in geom["faces"]}
        face = fmap[fidx]

        v = np.array(face["normal"] or [0, 0, 1], dtype=float)
        n = np.linalg.norm(v)
        v = v / n if n > 1e-9 else np.array([0, 0, 1.0])
        tilt = np.array([0.55, -0.55, 0.45])
        fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.4), facecolor="white")
        for ax, view in zip(axes, [tuple(v + 0.5 * tilt), (0.0, 0.05, 1.0)]):
            R.draw_view(ax, geom, tris, face_of, view, highlight=fidx, label=False,
                        colors={fidx: "#d64541"})
        axes[0].set_title("view A (from face normal)", fontsize=7)
        axes[1].set_title("view B (top)", fontsize=7)
        fig.suptitle("sample %s" % sid, fontsize=9)
        fig.tight_layout()
        fig.savefig(os.path.join(out, "img", "%s.png" % sid), dpi=120, facecolor="white")
        plt.close(fig)

        nbrs = []
        for e in geom["edges"]:
            if e["faces"] and fidx in e["faces"]:
                for other in e["faces"]:
                    if other != fidx and other not in nbrs:
                        nbrs.append(other)
        pol = polarity.get(part, {}).get(str(fidx))
        pack.append({
            "sample_id": sid,
            "part_bbox_mm": geom["bbox"],
            "part_face_count": len(geom["faces"]),
            "face": {"surface": face["surface"], "area_mm2": face["area_mm2"],
                     "normal": [round(x, 3) for x in (face["normal"] or [])],
                     "bbox_mm": face["bbox"],
                     "centroid_mm": [round(x, 2) for x in face["centroid"]],
                     "radial_dot": (None if pol is None else pol["dot"]),
                     "params": {k: v for k, v in (face.get("params") or {}).items()
                                if k in ("radius", "axis", "offset", "semi_angle")}},
            "neighbors": [{"surface": fmap[k]["surface"], "area_mm2": fmap[k]["area_mm2"],
                           "normal": [round(x, 2) for x in (fmap[k]["normal"] or [])],
                           "z_mm": [round(fmap[k]["bbox"][2], 2), round(fmap[k]["bbox"][5], 2)],
                           "centroid_z": round(fmap[k]["centroid"][2], 2)}
                          for k in nbrs[:8] if k in fmap],
        })
        key[sid] = {"part": part, "face": fidx, "family": item["family"],
                    "hard": (part, fidx) in HARD}

    with open(os.path.join(out, "samples.json"), "w", encoding="utf-8") as fh:
        json.dump(pack, fh, ensure_ascii=False, indent=1)
    with open(os.path.join(out, "key.json"), "w", encoding="utf-8") as fh:
        json.dump(key, fh, ensure_ascii=False, indent=1)

    from collections import Counter
    print("题目 %d 道 → %s" % (len(pack), out))
    print("家族分布:", dict(Counter(v["family"] for v in key.values())))
    print("其中难题:", sum(1 for v in key.values() if v["hard"]), "道")


if __name__ == "__main__":
    main()
