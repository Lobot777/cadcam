"""M3.2 第 2 项：生成并评估 B 路 v2（高度图驱动区域铣）。

对照三路：
  A  = FreeCAD CAM 参考刀路（reports/m3_gcode_ref/）
  B1 = M3.1 矩形 bbox 模板（reports/m3_gcode/）
  B2 = 本脚本生成的高度图刀路（reports/m3_gcode_hm/）

用法：
  PYTHONPATH=src python scripts/m3_heightmap.py --parts 2
  PYTHONPATH=src python scripts/m3_heightmap.py            # 10 件
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

from cadcam.modalities.cam import gcode_gen, heightmap, process  # noqa: E402
from cadcam.modalities.mfr.instances import group               # noqa: E402
from cadcam.verify import dexel                                 # noqa: E402

GEOM = os.path.join(ROOT, "reports", "annotation", "geom")
LABELS = os.path.join(ROOT, "data", "zenodo_rptu", "labels")
OUTDIR = os.path.join(ROOT, "reports", "m3_gcode_hm")
OUT = os.path.join(ROOT, "reports", "m3_heightmap_eval.json")
RES = 0.5


def _metrics(text, target, stock_top, radius_by_op, default_r):
    v = gcode_gen.validate(text)
    ir = v["ir"]
    m = dexel.simulate(ir, target, stock_top, tool_radius_mm=default_r,
                       res=RES, tool_radius_by_op=radius_by_op)
    goal = target["top"]
    mask = np.isfinite(goal)
    gouge = np.where(mask, np.maximum(0.0, goal - m["cur"]), 0.0)
    return {"whitelist_ok": v["ok"], "errors": v["errors"][:3],
            "max_gouge_depth_mm": round(float(gouge.max()), 3),
            "segments": ir["stats"]["segments"],
            "mean_deviation_mm": m["mean_deviation_mm"],
            "p95_deviation_mm": m["p95_deviation_mm"],
            "p99_deviation_mm": m["p99_deviation_mm"],
            "max_deviation_mm": m["max_deviation_mm"],
            "columns_over_0_5mm_pct": m["columns_over_0_5mm_pct"],
            "gouge_volume_mm3": m["gouge_volume_mm3"],
            "residual_volume_mm3": m["residual_volume_mm3"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parts", default=",".join(str(i) for i in range(1, 11)))
    ap.add_argument("--ap", type=float, default=4.0, help="分层切深 mm")
    args = ap.parse_args()
    parts = [x.strip() for x in args.parts.split(",") if x.strip()]
    os.makedirs(OUTDIR, exist_ok=True)
    rows = []
    for part in parts:
        t0 = time.time()
        with open(os.path.join(GEOM, "%s.json" % part), encoding="utf-8") as fh:
            geom = json.load(fh)
        with open(os.path.join(LABELS, "%s.json" % part), encoding="utf-8") as fh:
            lab = json.load(fh)
        labels = [lab["faces"][str(f["index"])]["family"] for f in geom["faces"]]
        plan = process.plan(geom, group(geom, labels,
                                        families={x: x for x in set(labels)}))
        target = dexel.rasterize(geom, res=RES)
        stock_top = plan["stock"]["top_z"]

        text, meta = heightmap.generate(plan, target, res=RES, ap=args.ap)
        with open(os.path.join(OUTDIR, "%s.nc" % part), "w", encoding="utf-8") as fh:
            fh.write(text)
        radius_by_op = {o["label"]: o["tool_d_mm"] / 2.0
                        for o in meta["ops_detail"] if not o.get("empty")}
        b2 = _metrics(text, target, stock_top, radius_by_op, 3.0)

        # B1 模板
        with open(os.path.join(ROOT, "reports", "m3_gcode", "%s.nc" % part),
                  encoding="utf-8") as fh:
            text_b1 = fh.read()
        r_b1 = {o["label"]: o["speeds"]["tool_d_mm"] / 2.0 for o in plan["ops"]}
        b1 = _metrics(text_b1, target, stock_top, r_b1, 3.0)

        row = {"part": part, "B2": b2, "B1": b1,
               "hm_ops": len(meta["ops_detail"]), "hm_levels": len(meta["levels"]),
               "hm_lines": meta["gcode_lines"],
               "gouge_reduction_pct": round(
                   100 * (1 - b2["gouge_volume_mm3"] /
                          max(b1["gouge_volume_mm3"], 1e-9)), 2)
               if b1["gouge_volume_mm3"] > 0 else None,
               "elapsed_s": round(time.time() - t0, 1)}
        rows.append(row)
        print("件 %-3s B2: 过切 %8.1f (B1 %9.1f, -%s%%) 残留 %9.1f | "
              "平均偏差 %.3f P99 %.2f 超0.5mm %5.2f%% | %d 行 %.1fs %s"
              % (part, b2["gouge_volume_mm3"], b1["gouge_volume_mm3"],
                 row["gouge_reduction_pct"], b2["residual_volume_mm3"],
                 b2["mean_deviation_mm"], b2["p99_deviation_mm"],
                 b2["columns_over_0_5mm_pct"], meta["gcode_lines"],
                 row["elapsed_s"], "OK" if b2["whitelist_ok"] else "WHITELIST-FAIL"))

    if len(rows) == 10:
        summary = {
            "version": "M3.2-2", "res_mm": RES, "ap_mm": args.ap,
            "passes": [{"tool_d_mm": 6.0}, {"tool_d_mm": 1.5}],
            "parts": len(rows),
            "all_whitelist_ok": all(r["B2"]["whitelist_ok"] for r in rows),
            "B2_zero_gouge_count": sum(r["B2"]["gouge_volume_mm3"] <= 1.0 for r in rows),
            "B1_total_gouge_mm3": round(sum(r["B1"]["gouge_volume_mm3"] for r in rows), 1),
            "B2_total_gouge_mm3": round(sum(r["B2"]["gouge_volume_mm3"] for r in rows), 1),
            "B2_total_residual_mm3": round(sum(r["B2"]["residual_volume_mm3"] for r in rows), 1),
            "B2_mean_deviation_worst_mm": max(r["B2"]["mean_deviation_mm"] for r in rows),
            "B2_max_deviation_worst_mm": max(r["B2"]["max_deviation_mm"] for r in rows),
            "B2_over_0_5mm_pct_worst": max(r["B2"]["columns_over_0_5mm_pct"] for r in rows),
            "criterion": "ADR-001 dual: max_gouge<=0.5 AND mean_abs_dev<=0.5",
            "accept_dual_criterion_count": sum(
                r["B2"]["max_gouge_depth_mm"] <= 0.5
                and r["B2"]["mean_deviation_mm"] <= 0.5 for r in rows),
            "accept_dual_criterion": all(
                r["B2"]["max_gouge_depth_mm"] <= 0.5
                and r["B2"]["mean_deviation_mm"] <= 0.5 for r in rows),
            "legacy_max_pass_count": sum(r["B2"]["max_deviation_mm"] <= 0.5 for r in rows),
        }
        with open(OUT, "w", encoding="utf-8") as fh:
            json.dump({"summary": summary, "parts": rows}, fh, ensure_ascii=False, indent=1)
        print("\n汇总:", json.dumps(summary, ensure_ascii=False))
        print("wrote", OUT)


if __name__ == "__main__":
    main()
