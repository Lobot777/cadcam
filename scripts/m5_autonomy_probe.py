"""离线抬升刀路端点；不检查完整刀盘或线段内部，不能作为安全投影或修复上界。"""
from __future__ import annotations

import copy
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from cadcam.modalities.cam import gcode_gen, process  # noqa: E402
from cadcam.modalities.mfr.instances import group  # noqa: E402
from cadcam.verify import dexel  # noqa: E402

GEOM = os.path.join(ROOT, "reports", "annotation", "geom")
LABELS = os.path.join(ROOT, "data", "zenodo_rptu", "labels")
GCODE = os.path.join(ROOT, "reports", "m5_direct_gcode")
OUT = os.path.join(ROOT, "reports", "m5_autonomy_probe.json")
RES = 0.5
TOOL_R = 3.0


def goal_at(target, x, y):
    ix = int(np.argmin(abs(target["xs"] - x)))
    iy = int(np.argmin(abs(target["ys"] - y)))
    return float(target["top"][iy, ix])


def metrics(ir, target, stock_top):
    m = dexel.simulate(ir, target, stock_top, tool_radius_mm=TOOL_R, res=RES)
    goal = target["top"]
    mask = np.isfinite(goal)
    gouge = np.where(mask, np.maximum(0.0, goal - m["cur"]), 0.0)
    return {"max_gouge_mm": round(float(gouge.max()), 3),
            "mean_deviation_mm": round(float(m["mean_deviation_mm"]), 3),
            "gouge_volume_mm3": round(float(m["gouge_volume_mm3"]), 1),
            "residual_volume_mm3": round(float(m["residual_volume_mm3"]), 1),
            "dual_line_pass": bool(float(gouge.max()) <= 0.5
                                   and m["mean_deviation_mm"] <= 0.5)}


def project(ir, target):
    """只按端点最近的目标柱抬高 z，不计算刀具半径与段内净空。"""
    ir = copy.deepcopy(ir)
    safe = {}
    for s in ir["segments"]:
        for key in ("from", "to"):
            x, y = float(s[key][0]), float(s[key][1])
            g = goal_at(target, x, y)
            k = (round(x, 4), round(y, 4))
            if np.isfinite(g):
                safe[k] = max(safe.get(k, -1e9), g)
    raised = total = 0
    for s in ir["segments"]:
        for key in ("from", "to"):
            p = s[key]
            k = (round(float(p[0]), 4), round(float(p[1]), 4))
            total += 1
            if k in safe and p[2] < safe[k]:
                p[2] = float(safe[k])
                raised += 1
    return ir, raised, total


def plan_of(geom, labels):
    fam = [labels["faces"][str(f["index"])]["family"] for f in geom["faces"]]
    inst = group(geom, fam, families={x: x for x in set(fam)})
    return process.plan(geom, inst)


def run():
    rows = []
    for pid in range(1, 11):
        path = os.path.join(GCODE, "part%d.nc" % pid)
        if not os.path.isfile(path):
            continue
        with open(os.path.join(GEOM, "%d.json" % pid), encoding="utf-8") as fh:
            geom = json.load(fh)
        with open(os.path.join(LABELS, "%d.json" % pid), encoding="utf-8") as fh:
            labels = json.load(fh)
        plan = plan_of(geom, labels)
        stock_top = float(plan["stock"]["top_z"])
        target = dexel.rasterize(geom, res=RES)
        with open(path, encoding="utf-8") as fh:
            ir = gcode_gen.validate(fh.read(), stock=plan["stock"])["ir"]
        before = metrics(ir, target, stock_top)
        p_ir, raised, total = project(ir, target)
        after = metrics(p_ir, target, stock_top)
        rows.append({"part": pid, "endpoints_raised_pct":
                     round(100.0 * raised / max(1, total), 1),
                     "before": before, "after_projection": after})
        print("件%-3d 抬高 %5.1f%% 端点 ｜ 过切 %6.2f→%6.2f 均差 %6.2f→%6.2f "
              "残留 %9.1f→%9.1f ｜ 双线 %s→%s"
              % (pid, 100.0 * raised / max(1, total),
                 before["max_gouge_mm"], after["max_gouge_mm"],
                 before["mean_deviation_mm"], after["mean_deviation_mm"],
                 before["residual_volume_mm3"],
                 after["residual_volume_mm3"],
                 "PASS" if before["dual_line_pass"] else "FAIL",
                 "PASS" if after["dual_line_pass"] else "FAIL"))

    n = max(1, len(rows))
    summary = {
        "n": len(rows),
        "raw_dual_line_pass": sum(r["before"]["dual_line_pass"] for r in rows),
        "projected_dual_line_pass": sum(
            r["after_projection"]["dual_line_pass"] for r in rows),
        "mean_residual_before_mm3": round(float(np.mean(
            [r["before"]["residual_volume_mm3"] for r in rows])), 1) if rows else None,
        "mean_residual_after_mm3": round(float(np.mean(
            [r["after_projection"]["residual_volume_mm3"] for r in rows])), 1)
        if rows else None,
        "mean_max_gouge_before_mm": round(float(np.mean(
            [r["before"]["max_gouge_mm"] for r in rows])), 3) if rows else None,
        "mean_max_gouge_after_mm": round(float(np.mean(
            [r["after_projection"]["max_gouge_mm"] for r in rows])), 3)
        if rows else None,
        "parts_with_zero_gouge_after": sum(
            r["after_projection"]["max_gouge_mm"] <= 0.001 for r in rows),
        "conclusion": ("端点抬升后仍存在过切和残留；该固定 XY 启发式未救回这十件程序。"
                       "未测试新增覆盖刀路或模型重规划，不构成安全保证或修复能力上界。"),
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"rule": "离线端点抬升诊断：仅将 z 抬至最近目标柱的高度；"
                           "不新增刀路、不改 XY，不检查完整刀具足迹或线段内部。"
                           "没有模型重写调用，不是安全投影或修复上界。",
                   "criterion": "ADR-001 双线：max_gouge<=0.5mm 且 mean_abs_dev<=0.5mm",
                   "summary": summary, "parts": rows}, fh,
                  ensure_ascii=False, indent=1)
    print("\n%d 件｜双线 %d/%d → %d/%d；平均残留 %.1f → %.1f mm³；"
          "投影后零过切件 %d；%s"
          % (n, summary["raw_dual_line_pass"], n,
             summary["projected_dual_line_pass"], n,
             summary["mean_residual_before_mm3"],
             summary["mean_residual_after_mm3"],
             summary["parts_with_zero_gouge_after"],
             "固定 XY 的端点抬升无效；未测试新增覆盖或模型重规划"))
    return summary


if __name__ == "__main__":
    run()
