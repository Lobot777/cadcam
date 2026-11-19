"""B3 基线：等高线刀路**不做刀半径膨胀**（标准 CAM 的常见做法）。

用来回答一个问题：B2 的零过切，是因为"知道哪里要保留"，还是因为
"按刀半径把保留区膨胀了"？

- B2（`inflate=True`）：可行域 = 对 {goal > L} 按刀半径膨胀后取补 → 几何上不可能过切；
- B3（`inflate=False`）：只排除 {goal > L} 本身，刀心可以贴到台阶边缘 →
  实际切除会多出一个刀半径。

两者唯一的差别就是那一圈膨胀，其余（分层、步距、刀具、后处理）完全相同。

    PYTHONPATH=src python scripts/m3_b3_baseline.py
    PYTHONPATH=src python scripts/m3_b3_baseline.py --parts 1,2,10
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from m3_heightmap import _metrics, LABELS, GEOM              # noqa: E402
from cadcam.modalities.cam import heightmap, process         # noqa: E402
from cadcam.modalities.mfr.instances import group            # noqa: E402
from cadcam.verify import dexel                              # noqa: E402

OUT = os.path.join(ROOT, "reports", "m3_b3_baseline.json")
RES = 0.5


def run_part(pid):
    geom = json.load(open(os.path.join(GEOM, "%d.json" % pid), encoding="utf-8"))
    lab = json.load(open(os.path.join(LABELS, "%d.json" % pid), encoding="utf-8"))
    fam = [lab["faces"][str(f["index"])]["family"] for f in geom["faces"]]
    inst = group(geom, fam, families={x: x for x in set(fam)})
    plan = process.plan(geom, inst)
    target = dexel.rasterize(geom, res=RES)
    out = {"part": pid, "levels": int(len(np.unique(np.round(
        target["top"][np.isfinite(target["top"])], 1))))}
    for tag, inflate in (("B2_masked", True), ("B3_no_inflate", False)):
        text, meta = heightmap.generate(plan, target, res=RES, inflate=inflate)
        # 刀径要按**高度图刀路自己的工序标签**来取（HM1_L1 粗刀 / HM2_L1 精刀），
        # 用 plan["ops"] 的标签会对不上，全部退化成默认 Ø6 → 假过切。
        radius_by_op = {op["label"]: op["tool_d_mm"] / 2.0
                        for op in meta["ops_detail"]}
        m = _metrics(text, target, plan["stock"]["top_z"], radius_by_op, 3.0)
        out[tag] = {"max_gouge_depth_mm": m["max_gouge_depth_mm"],
                    "mean_deviation_mm": m["mean_deviation_mm"],
                    "columns_over_0_5mm_pct": m.get("columns_over_0_5mm_pct"),
                    "lines": len(text.splitlines()),
                    "segments": m["segments"]}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parts", default="1,2,3,4,5,6,7,8,9,10")
    args = ap.parse_args()
    parts = [int(x) for x in args.parts.split(",") if x.strip()]
    rows = []
    t0 = time.time()
    for p in parts:
        r = run_part(p)
        rows.append(r)
        b2, b3 = r["B2_masked"], r["B3_no_inflate"]
        print("件%-3s 层%-3d B2 过切 %-6s | B3 过切 %-6s（×%.1f）｜B3 偏差 %.3f"
              % (p, r["levels"], b2["max_gouge_depth_mm"], b3["max_gouge_depth_mm"],
                 (b3["max_gouge_depth_mm"] / b2["max_gouge_depth_mm"])
                 if b2["max_gouge_depth_mm"] > 1e-9 else float("inf"),
                 b3["mean_deviation_mm"]))
    ok_b2 = sum(1 for r in rows if r["B2_masked"]["max_gouge_depth_mm"] <= 0.5)
    ok_b3 = sum(1 for r in rows if r["B3_no_inflate"]["max_gouge_depth_mm"] <= 0.5)
    summary = {"n": len(rows), "pass_b2": ok_b2, "pass_b3": ok_b3,
               "b2_max_gouge": max(r["B2_masked"]["max_gouge_depth_mm"] for r in rows),
               "b3_max_gouge": max(r["B3_no_inflate"]["max_gouge_depth_mm"] for r in rows),
               "b2_mean_dev": round(sum(r["B2_masked"]["mean_deviation_mm"] for r in rows) / len(rows), 3),
               "b3_mean_dev": round(sum(r["B3_no_inflate"]["mean_deviation_mm"] for r in rows) / len(rows), 3),
               "seconds": round(time.time() - t0, 1)}
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"rule": "唯一的差别是掩码是否按刀半径膨胀，其余完全相同",
                   "summary": summary, "parts": rows}, fh, ensure_ascii=False, indent=1)
    print("\nB2 零过切 %d/%d（最坏 %.3fmm）｜B3 合格 %d/%d（最坏 %.3fmm）"
          % (ok_b2, len(rows), summary["b2_max_gouge"],
             ok_b3, len(rows), summary["b3_max_gouge"]))
    print("均偏差：B2 %.3f ｜ B3 %.3f" % (summary["b2_mean_dev"], summary["b3_mean_dev"]))
    print("→ %s" % os.path.relpath(OUT, ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
