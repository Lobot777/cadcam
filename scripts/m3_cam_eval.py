"""M3.2 第 1 项验收：FreeCAD CAM 参考刀路（A 路）的几何复现误差。

口径（写死，避免事后挑好看的定义）：
  目标   = STEP 三角网格栅格化出的高度图 target_top（0.5mm）
  A 路   = FreeCAD CAM MillFace 生成的刀路，经 ngc.parse + dexel.simulate
  B 路   = M3.1 模板刀路，同样仿真，作为对照
  验收线（ADR-001，2026-11-05 用户确认）：双线，不再用 |cur-goal| 的 max 单独判定
    硬约束 = max(goal - cur) ≤ 0.5mm        （过切不可逆）
    精度   = mean|cur - goal| ≤ 0.5mm       （体积误差/投影面积）
  max / P99 / 超差柱占比只作报告量，不设阈值（离散化伪影，见 M3-ACCEPTANCE-CRITERION.md）

真实 .ngc 不参与几何对齐：其机床坐标系（件1 X∈[-3,84]、Z∈[-7,15]）
与 STEP（XY∈[-40,40]、Z∈[2,52]）不一致，见 docs/M3-CAM-REFERENCE.md。
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from cadcam.modalities.cam import gcode_gen, process  # noqa: E402
from cadcam.modalities.mfr.instances import group     # noqa: E402
from cadcam.verify import dexel                       # noqa: E402

GEOM = os.path.join(ROOT, "reports", "annotation", "geom")
LABELS = os.path.join(ROOT, "data", "zenodo_rptu", "labels")
REF_DIR = os.path.join(ROOT, "reports", "m3_gcode_ref")
B_DIR = os.path.join(ROOT, "reports", "m3_gcode")
META = os.path.join(ROOT, "reports", "cam_reference_meta.json")
OUT = os.path.join(ROOT, "reports", "m3_cam_eval.json")
ACCEPT_MM = 0.5
RES = 0.5


def metrics(text, target, stock_top, radius_by_op, default_r):
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
            "max_deviation_mm": m["max_deviation_mm"],
            "mean_deviation_mm": m["mean_deviation_mm"],
            "p95_deviation_mm": m["p95_deviation_mm"],
            "p99_deviation_mm": m["p99_deviation_mm"],
            "columns_over_0_5mm_pct": m["columns_over_0_5mm_pct"],
            "gouge_volume_mm3": m["gouge_volume_mm3"],
            "residual_volume_mm3": m["residual_volume_mm3"],
            "gouge_columns": m["gouge_columns"],
            "residual_columns": m["residual_columns"]}


def main():
    with open(META, encoding="utf-8") as fh:
        meta = {p["part"]: p for p in json.load(fh)["parts"]}
    rows = []
    for part in [str(i) for i in range(1, 11)]:
        with open(os.path.join(GEOM, "%s.json" % part), encoding="utf-8") as fh:
            geom = json.load(fh)
        with open(os.path.join(LABELS, "%s.json" % part), encoding="utf-8") as fh:
            lab = json.load(fh)
        target = dexel.rasterize(geom, res=RES)
        stock_top = float(geom["bbox"]["max"][2]) + 1.0

        info = meta[part]
        # A 路：每道工序用 CAM 实际刀径
        radius_a = {}
        for op in info.get("ops_detail", []):
            d = op.get("tool_d_mm") or 5.0
            radius_a[op["label"]] = d / 2.0
        with open(os.path.join(REF_DIR, "%s.nc" % part), encoding="utf-8") as fh:
            text_a = fh.read()
        a = metrics(text_a, target, stock_top, radius_a, 2.5)

        # B 路：M3.1 模板
        labels = [lab["faces"][str(f["index"])]["family"] for f in geom["faces"]]
        plan = process.plan(geom, group(geom, labels, families={x: x for x in set(labels)}))
        radius_b = {op["label"]: op["speeds"]["tool_d_mm"] / 2.0 for op in plan["ops"]}
        with open(os.path.join(B_DIR, "%s.nc" % part), encoding="utf-8") as fh:
            text_b = fh.read()
        b = metrics(text_b, target, stock_top, radius_b, 3.0)

        row = {"part": part, "cam_up_faces": info["up_faces"],
               "cam_ops": info["ops"], "cam_gcode_lines": info["gcode_lines"],
               "A": a, "B": b,
               "improvement": {
                   "max_dev_mm": round(b["max_deviation_mm"] - a["max_deviation_mm"], 3),
                   "gouge_mm3": round(b["gouge_volume_mm3"] - a["gouge_volume_mm3"], 1),
               },
               "max_gouge_depth_mm": a["max_gouge_depth_mm"],
               # 报告量，只报不判
               "accept_le_0_5mm_legacy_max": a["max_deviation_mm"] <= ACCEPT_MM,
               "acceptance_v2": {
                   "safety_max_gouge_le_0_5mm": a["max_gouge_depth_mm"] <= ACCEPT_MM,
                   "accuracy_mean_le_0_5mm": a["mean_deviation_mm"] <= ACCEPT_MM,
                   "pass": (a["max_gouge_depth_mm"] <= ACCEPT_MM
                            and a["mean_deviation_mm"] <= ACCEPT_MM),
               }}
        rows.append(row)
        print("件 %-3s A段 %4d | 偏差 平均%6.3f P99%6.3f max%6.1f 超0.5mm %5.2f%% | "
              "过切 %8.1f | B max%6.1f | 验收 %s"
              % (part, a["segments"], a["mean_deviation_mm"], a["p99_deviation_mm"],
                 a["max_deviation_mm"], a["columns_over_0_5mm_pct"],
                 a["gouge_volume_mm3"], b["max_deviation_mm"],
                 "PASS" if row["acceptance_v2"]["pass"] else "FAIL"))

    summary = {
        "version": "M3.2-1", "res_mm": RES, "accept_mm": ACCEPT_MM,
        "freecad": ["1", "1", "4"], "parts": len(rows),
        "all_whitelist_ok": all(r["A"]["whitelist_ok"] for r in rows),
        "criterion": "ADR-001 dual: max_gouge<=0.5 AND mean_abs_dev<=0.5",
        "pass_count": sum(r["acceptance_v2"]["pass"] for r in rows),
        "safety_pass_count": sum(r["acceptance_v2"]["safety_max_gouge_le_0_5mm"] for r in rows),
        "accuracy_pass_count": sum(r["acceptance_v2"]["accuracy_mean_le_0_5mm"] for r in rows),
        "legacy_max_pass_count": sum(r["accept_le_0_5mm_legacy_max"] for r in rows),
        "A_max_deviation_worst_mm": max(r["A"]["max_deviation_mm"] for r in rows),
        "A_p99_worst_mm": max(r["A"]["p99_deviation_mm"] for r in rows),
        "A_mean_worst_mm": max(r["A"]["mean_deviation_mm"] for r in rows),
        "A_over_0_5mm_pct_worst": max(r["A"]["columns_over_0_5mm_pct"] for r in rows),
        "A_total_gouge_mm3": round(sum(r["A"]["gouge_volume_mm3"] for r in rows), 1),
        "B_total_gouge_mm3": round(sum(r["B"]["gouge_volume_mm3"] for r in rows), 1),
        "gouge_reduction_pct": round(
            100 * (1 - sum(r["A"]["gouge_volume_mm3"] for r in rows)
                   / max(sum(r["B"]["gouge_volume_mm3"] for r in rows), 1e-9)), 2),
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"summary": summary, "parts": rows}, fh, ensure_ascii=False, indent=1)
    print("\n汇总:", json.dumps(summary, ensure_ascii=False))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
