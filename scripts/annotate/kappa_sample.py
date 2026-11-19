"""生成 κ 校验用的盲标包：30 个面，只给几何与图，不给任何标签。

抽样：按家族分层（stock 8 / hole 7 / boss 5 / pocket 4 / step 3 / fillet 2 / slot 1），
层内随机、固定随机种子，保证可复现。

盲标约束：包内任何文件都不得含 family / mfcad_class / feature id。
子智能体只被要求读 blind/ 目录；标注结果写到 blind/../subagent.json。
"""

import json
import os
import random
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render as R  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GEOM = os.path.join(ROOT, "reports", "annotation", "geom")
LABELS = os.path.join(ROOT, "data", "zenodo_rptu", "labels")
OUT = os.path.join(ROOT, "reports", "annotation", "kappa", "blind")

PLAN = {"stock": 8, "hole": 7, "boss": 5, "pocket": 4, "step": 3, "fillet": 2, "slot": 1}
SEED = 20261104


def load(part):
    with open(os.path.join(GEOM, "%s.json" % part), encoding="utf-8") as fh:
        geom = json.load(fh)
    with open(os.path.join(LABELS, "%s.json" % part), encoding="utf-8") as fh:
        lab = json.load(fh)
    return geom, lab


def main():
    rng = random.Random(SEED)
    parts = [str(i) for i in range(1, 11)]
    pool = {k: [] for k in PLAN}
    for part in parts:
        geom, lab = load(part)
        for idx_s, meta in lab["faces"].items():
            fam = meta["family"]
            if fam in pool:
                pool[fam].append((part, int(idx_s)))
    picked = []
    for fam, n in PLAN.items():
        cand = pool[fam][:]
        rng.shuffle(cand)
        # 层内先按零件去重，避免 8 个 stock 全来自同一件
        by_part = {}
        for item in cand:
            by_part.setdefault(item[0], []).append(item)
        order = sorted(by_part, key=lambda p: rng.random())
        take = []
        while len(take) < n and any(by_part[p] for p in order):
            for p in order:
                if by_part[p] and len(take) < n:
                    take.append(by_part[p].pop(0))
        picked.append((fam, take))

    os.makedirs(os.path.join(OUT, "img"), exist_ok=True)
    samples = []
    sid = 0
    for fam, items in picked:
        for part, idx in items:
            sid += 1
            samples.append({"sample_id": "S%02d" % sid, "_part": part, "_face": idx,
                            "_true_family": fam})

    # 渲染 + 写几何描述
    geoms = {}
    for s in samples:
        part = s["_part"]
        if part not in geoms:
            geoms[part] = json.load(open(os.path.join(GEOM, "%s.json" % part), encoding="utf-8"))
    by_part = {}
    for s in samples:
        by_part.setdefault(s["_part"], []).append(s)

    for part, group in by_part.items():
        geom = geoms[part]
        tris, face_of = R._collect(geom)
        fmap = {f["index"]: f for f in geom["faces"]}
        for s in group:
            face = fmap[s["_face"]]
            nv = face["normal"] or [0, 0, 1]
            import numpy as np
            v = np.array(nv, dtype=float)
            n = np.linalg.norm(v)
            v = v / n if n > 1e-9 else np.array([0, 0, 1.0])
            tilt = np.array([0.55, -0.55, 0.45])
            fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.4), facecolor="white")
            for ax, view in zip(axes, [tuple(v + 0.5 * tilt), (0.0, 0.05, 1.0)]):
                R.draw_view(ax, geom, tris, face_of, view, highlight=s["_face"], label=False,
                            colors={s["_face"]: "#d64541"})
            axes[0].set_title("view A (from face normal)", fontsize=7)
            axes[1].set_title("view B (top)", fontsize=7)
            fig.suptitle("sample %s" % s["sample_id"], fontsize=9)
            fig.tight_layout()
            fig.savefig(os.path.join(OUT, "img", "%s.png" % s["sample_id"]), dpi=120,
                        facecolor="white")
            plt.close(fig)

    # 几何描述（无标签）
    pack = []
    for s in samples:
        geom = geoms[s["_part"]]
        fmap = {f["index"]: f for f in geom["faces"]}
        face = fmap[s["_face"]]
        # 1-hop 邻接摘要：邻面的面型/面积/法向
        nbrs = []
        for e in geom["edges"]:
            if e["faces"] and s["_face"] in e["faces"]:
                for other in e["faces"]:
                    if other != s["_face"] and other not in nbrs:
                        nbrs.append(other)
        nbr_desc = []
        for k in nbrs[:8]:
            g = fmap.get(k)
            if g:
                nbr_desc.append({"surface": g["surface"], "area_mm2": g["area_mm2"],
                                 "normal": [round(x, 2) for x in (g["normal"] or [])]})
        pack.append({
            "sample_id": s["sample_id"],
            "part_bbox_mm": geom["bbox"],
            "part_face_count": len(geom["faces"]),
            "face": {
                "surface": face["surface"],
                "area_mm2": face["area_mm2"],
                "normal": [round(x, 3) for x in (face["normal"] or [])],
                "bbox_mm": face["bbox"],
                "centroid_mm": [round(x, 2) for x in face["centroid"]],
                "params": {k: v for k, v in (face.get("params") or {}).items()
                           if k in ("radius", "axis", "offset", "semi_angle")},
            },
            "neighbors": nbr_desc,
        })
    with open(os.path.join(OUT, "samples.json"), "w", encoding="utf-8") as fh:
        json.dump(pack, fh, ensure_ascii=False, indent=1)

    key = {s["sample_id"]: {"part": s["_part"], "face": s["_face"],
                            "family": s["_true_family"]} for s in samples}
    with open(os.path.join(ROOT, "reports", "annotation", "kappa", "answer_key.json"),
              "w", encoding="utf-8") as fh:
        json.dump(key, fh, ensure_ascii=False, indent=1)
    print("samples=%d  images=%d" % (len(samples), len(os.listdir(os.path.join(OUT, "img")))))
    from collections import Counter
    print(Counter(s["_true_family"] for s in samples))


if __name__ == "__main__":
    main()
