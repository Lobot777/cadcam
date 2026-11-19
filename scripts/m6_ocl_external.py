"""M6 补充：把 OpenCAMLib 第三方交叉验证从 10 件锚点集扩到 130 件外部集。

背景
----
`scripts/m6_opencamlib.py` 在 10 件 Zenodo STEP 锚点集上证明：
  (1) 目标高度图与 OCL 点刀高度图逐柱一致；
  (2) OCL 独立过切判据复现 B2 ≫ B3；
  (3) OCL 自生成刀路被我们的双线判据接受 9/10。
但 10 件都是**密集 STEP 剖分 + 轴对齐矩形台阶**，只暴露了圆弧边界的半格效应。

本脚本把 (1)(2) 接到 `data/external/`（MFCAD 130 件，缩放到 80mm）上，用另一类
几何（稀疏大三角、斜边台阶）压力测试第三方判据，顺带在 130 件上量化
`heightmap.generate(guard_mm=...)` 的帕累托取舍。

四条口径（全部与 10 件脚本一致）
--------------------------------
  res = 0.5mm；点刀 Ø0.02；相切间隙 0.02mm；G1 按 0.5mm 采样；
  双线判据 max_gouge ≤ 0.5mm 且 mean_deviation ≤ 0.5mm。

重要：外部件的三角网格是「每个面 1–3 个大三角」，顶面斜边处栅格覆盖率
不到 100%。`dexel.rasterize` 把无三角覆盖的柱记为 -inf（空气），生成器和
验证器都跳过；OCL 的连续刀盘却会碰到相邻三角 → 在斜边高台边缘报出深过切。
脚本把过切点分成两类中性记录，不掩盖、也不擅自改判：
  `shallow`  0.01 < d ≤ 0.5mm  —— 半格离散化边界效应
  `deep`     d > 0.5mm         —— 柱心膨胀 vs 连续刀盘的口径差（多在无覆盖柱）

用法
----
    PYTHONPATH=src python scripts/m6_ocl_external.py                 # 全 130 件
    PYTHONPATH=src python scripts/m6_ocl_external.py --limit 5
    PYTHONPATH=src python scripts/m6_ocl_external.py --no-guard     # 跳过 guard 对照

依赖：`opencamlib==2023.1.11`（LGPL-2.1）、`numpy`。
输出：`reports/m6_ocl_external.json`
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np

try:
    import opencamlib as ocl
except ImportError as exc:
    raise SystemExit("需要 opencamlib：pip install opencamlib==2023.1.11") from exc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import m6_opencamlib as M                                          # noqa: E402
from m3_heightmap import _metrics                                  # noqa: E402
from cadcam.modalities.cam import heightmap, process              # noqa: E402
from cadcam.modalities.mfr.instances import group                 # noqa: E402
from cadcam.verify import dexel                                   # noqa: E402

GEOM = os.path.join(ROOT, "data", "external", "geom")
LABELS = os.path.join(ROOT, "data", "external", "labels")
OUT = os.path.join(ROOT, "reports", "m6_ocl_external.json")
ANCHOR = os.path.join(ROOT, "reports", "m6_ocl.json")
RES = M.RES
DEEP_MM = 0.5


def build_case(pid):
    g = json.load(open(os.path.join(GEOM, pid + ".json"), encoding="utf-8"))
    lab = json.load(open(os.path.join(LABELS, pid + ".json"), encoding="utf-8"))
    fam = [lab["faces"].get(str(f["index"]), {}).get("family", "face")
           for f in g["faces"]]
    fams = {x for x in fam if x != "face"}
    if not fams:
        raise ValueError("无可映射特征")
    plan = process.plan(g, group(g, fam, families={x: x for x in fams}))
    if not plan["ops"]:
        raise ValueError("工序为空")
    target = dexel.rasterize(g, res=RES)
    surf, ntri = M.stlsurf(g)
    return g, plan, target, surf, ntri


def geometry_check_ext(surf, target):
    """OCL 点刀高度图 vs 我们的三角形栅格高度图（外部集专用统计）。"""
    xs, ys = target["xs"], target["ys"]
    h_ocl, calls = M.cl_grid(surf, xs, ys, M.POINT_D)
    goal = target["top"]
    mask = np.isfinite(goal)
    hit = h_ocl > 0.0
    union = int((mask | hit).sum())
    both = mask & hit
    diff = np.abs(h_ocl[both] - goal[both])
    return {"columns": int(mask.sum()),
            "coverage_pct": round(100.0 * float(mask.mean()), 3),
            "uncovered_columns": int((~mask).sum()),
            "footprint_iou": round(float((mask & hit).sum() / max(1, union)), 5),
            "compared_columns": int(both.sum()),
            "max_abs_diff_mm": round(float(diff.max()), 5) if diff.size else None,
            "mean_abs_diff_mm": round(float(diff.mean()), 6) if diff.size else None,
            "within_0_02mm_pct": round(100 * float((diff <= 0.02).mean()), 3)
            if diff.size else None,
            "columns_over_0_5mm": int((diff > 0.5).sum()) if diff.size else 0,
            "ocl_cutter_calls": calls}


def oracle_check_ext(surf, text, meta, target, step=RES):
    """第三方过切判据 + 违规点分类（浅/深、有/无覆盖柱）。"""
    radius_of = {o["label"]: o["tool_d_mm"] / 2.0 for o in meta["ops_detail"]}
    points = M.sample_cutting(text, step=step)
    by_radius = {}
    for i, p in enumerate(points):
        by_radius.setdefault(round(float(radius_of.get(p[3], 3.0)), 4), []).append(i)
    xs, ys, goal = target["xs"], target["ys"], target["top"]
    x0, y0, res = target["x0"], target["y0"], target["res"]
    shallow = deep = deep_uncovered = 0
    worst = 0.0
    per_radius = {}
    for r, idx in sorted(by_radius.items(), reverse=True):
        sub = [points[i] for i in idx]
        cutter = ocl.CylCutter(max(0.001, 2.0 * (r - M.TOUCH_EPS)), 80.0)
        bdc = ocl.BatchDropCutter()
        bdc.setSTL(surf)
        bdc.setCutter(cutter)
        for x, y, _z, _op in sub:
            bdc.appendPoint(ocl.CLPoint(float(x), float(y), 0.0))
        bdc.run()
        safe = np.asarray([p.z for p in bdc.getCLPoints()], dtype=float)
        z = np.asarray([p[2] for p in sub], dtype=float)
        d = np.maximum(0.0, safe - z)
        viol = d > M.Z_TOL
        shallow += int(((d > M.Z_TOL) & (d <= DEEP_MM)).sum())
        deep_mask = d > DEEP_MM
        deep += int(deep_mask.sum())
        if deep_mask.any():
            # 等间距栅格：柱心 x0+(i+0.5)*res → i = floor((x-x0)/res)
            px = np.asarray([p[0] for p in sub])[deep_mask]
            py = np.asarray([p[1] for p in sub])[deep_mask]
            ix = np.clip(np.floor((px - x0) / res).astype(int), 0, len(xs) - 1)
            iy = np.clip(np.floor((py - y0) / res).astype(int), 0, len(ys) - 1)
            deep_uncovered += int((~np.isfinite(goal[iy, ix])).sum())
        worst = max(worst, float(d.max()) if d.size else 0.0)
        per_radius["%.2f" % (2 * r)] = {
            "points": len(sub), "violations": int(viol.sum()),
            "deep": int((d > DEEP_MM).sum()),
            "worst_mm": round(float(d.max()), 3) if d.size else 0.0}
    return {"sampled_tool_centers": len(points),
            "gouge_points": shallow + deep,
            "gouge_rate_pct": round(100.0 * (shallow + deep) / max(1, len(points)), 3),
            "shallow_le_0_5mm": shallow,
            "deep_gt_0_5mm": deep,
            "deep_on_uncovered_columns": deep_uncovered,
            "worst_gouge_depth_mm": round(worst, 3),
            "by_tool_d_mm": per_radius,
            "touch_eps_mm": M.TOUCH_EPS,
            "sample_step_mm": step}


def dexel_metrics(text, meta, target, stock_top):
    radius_by_op = {o["label"]: o["tool_d_mm"] / 2.0
                    for o in meta["ops_detail"]}
    m = _metrics(text, target, stock_top, radius_by_op, 3.0)
    return {"max_gouge_depth_mm": m["max_gouge_depth_mm"],
            "mean_deviation_mm": m["mean_deviation_mm"],
            "residual_volume_mm3": m["residual_volume_mm3"],
            "whitelist_ok": m["whitelist_ok"],
            "dual_line_pass": bool(m["max_gouge_depth_mm"] <= 0.5
                                   and m["mean_deviation_mm"] <= 0.5)}


def run_one(pid, with_guard=True):
    g, plan, target, surf, ntri = build_case(pid)
    stock_top = float(plan["stock"]["top_z"])
    goal = target["top"]
    row = {"id": pid, "triangles": ntri,
           "levels": int(len(np.unique(np.round(
               goal[np.isfinite(goal)] / max(0.2, 0.004 * (
                   np.nanmax(goal[np.isfinite(goal)]) -
                   np.nanmin(goal[np.isfinite(goal)]))))))),
           "ops": len(plan["ops"])}
    row["geometry"] = geometry_check_ext(surf, target)
    for tag, kw in (("B2_masked", {"inflate": True}),
                    ("B3_no_inflate", {"inflate": False})):
        text, meta = heightmap.generate(plan, target, res=RES, **kw)
        row[tag] = {"ocl_oracle": oracle_check_ext(surf, text, meta, target),
                    "our_dexel": dexel_metrics(text, meta, target, stock_top),
                    "gcode_lines": len(text.splitlines())}
    if with_guard:
        guard = RES / 2.0
        text, meta = heightmap.generate(plan, target, res=RES,
                                        inflate=True, guard_mm=guard)
        row["B2_half_cell_guard"] = {
            "guard_mm": guard,
            "ocl_oracle": oracle_check_ext(surf, text, meta, target),
            "our_dexel": dexel_metrics(text, meta, target, stock_top),
            "gcode_lines": len(text.splitlines())}
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-guard", action="store_true")
    args = ap.parse_args()
    ids = sorted(x[:-5] for x in os.listdir(GEOM) if x.endswith(".json"))
    if args.limit:
        ids = ids[:args.limit]
    rows, t0 = [], time.time()
    for i, pid in enumerate(ids, 1):
        try:
            r = run_one(pid, with_guard=not args.no_guard)
        except Exception as exc:                        # noqa: BLE001
            r = {"id": pid, "error": "%s: %s" % (type(exc).__name__, str(exc)[:140])}
        rows.append(r)
        if i % 20 == 0 or i == len(ids):
            good = [x for x in rows if "geometry" in x]
            b2 = sum(x["B2_masked"]["ocl_oracle"]["gouge_points"] for x in good)
            print("  ...%d/%d｜B2 过切点累计 %d｜%.0fs" %
                  (i, len(ids), b2, time.time() - t0), flush=True)
    ok = [r for r in rows if "geometry" in r]
    err = [r for r in rows if r.get("error")]

    def total(tag, field, sub="ocl_oracle"):
        return sum(r[tag][sub][field] for r in ok)

    def dual(tag):
        return sum(1 for r in ok if r[tag]["our_dexel"]["dual_line_pass"])

    summary = {
        "n": len(rows), "evaluated": len(ok), "errors": len(err),
        "opencamlib": "2023.1.11 (LGPL-2.1)",
        "res_mm": RES,
        "geometry_min_within_0_02mm_pct": min(
            r["geometry"]["within_0_02mm_pct"] or 0 for r in ok) if ok else None,
        "geometry_mean_within_0_02mm_pct": round(float(np.mean(
            [r["geometry"]["within_0_02mm_pct"] or 0 for r in ok])), 3) if ok else None,
        "geometry_max_abs_diff_mm": max(
            r["geometry"]["max_abs_diff_mm"] or 0 for r in ok) if ok else None,
        "geometry_min_footprint_iou": min(
            r["geometry"]["footprint_iou"] for r in ok) if ok else None,
        "coverage_mean_pct": round(float(np.mean(
            [r["geometry"]["coverage_pct"] for r in ok])), 3) if ok else None,
        "coverage_min_pct": min(r["geometry"]["coverage_pct"] for r in ok) if ok else None,
        "B2_sampled_points": total("B2_masked", "sampled_tool_centers"),
        "B2_gouge_points": total("B2_masked", "gouge_points"),
        "B2_shallow": total("B2_masked", "shallow_le_0_5mm"),
        "B2_deep": total("B2_masked", "deep_gt_0_5mm"),
        "B2_deep_on_uncovered_columns": total("B2_masked", "deep_on_uncovered_columns"),
        "B2_worst_gouge_mm": max(r["B2_masked"]["ocl_oracle"]["worst_gouge_depth_mm"]
                                 for r in ok) if ok else None,
        "B3_sampled_points": total("B3_no_inflate", "sampled_tool_centers"),
        "B3_gouge_points": total("B3_no_inflate", "gouge_points"),
        "B3_deep": total("B3_no_inflate", "deep_gt_0_5mm"),
        "B3_worst_gouge_mm": max(
            r["B3_no_inflate"]["ocl_oracle"]["worst_gouge_depth_mm"]
            for r in ok) if ok else None,
        "B2_our_dual_pass": dual("B2_masked"),
        "B3_our_dual_pass": dual("B3_no_inflate"),
        "seconds": round(time.time() - t0, 1),
    }
    if not args.no_guard:
        summary.update({
            "guard_mm": RES / 2.0,
            "guard_gouge_points": total("B2_half_cell_guard", "gouge_points"),
            "guard_deep": total("B2_half_cell_guard", "deep_gt_0_5mm"),
            "guard_worst_gouge_mm": max(
                r["B2_half_cell_guard"]["ocl_oracle"]["worst_gouge_depth_mm"]
                for r in ok) if ok else None,
            "guard_our_dual_pass": dual("B2_half_cell_guard"),
            "guard_residual_mm3": round(sum(
                r["B2_half_cell_guard"]["our_dexel"]["residual_volume_mm3"] or 0
                for r in ok), 1),
            "B2_residual_mm3": round(sum(
                r["B2_masked"]["our_dexel"]["residual_volume_mm3"] or 0
                for r in ok), 1),
        })
    # 与 10 件锚点集的对照（若报告存在）
    if os.path.isfile(ANCHOR):
        a = json.load(open(ANCHOR, encoding="utf-8"))["summary"]
        summary["anchor_10parts"] = {
            "B2_gouge_points": a.get("B2_ocl_gouge_points"),
            "B2_sampled_points": a.get("B2_sampled_points"),
            "B3_gouge_points": a.get("B3_ocl_gouge_points"),
            "B3_sampled_points": a.get("B3_sampled_points"),
            "note": "锚点集为密集 STEP 剖分；外部集为稀疏大三角、斜边台阶"}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"rule": "OpenCAMLib 独立 CAM 内核 × 130 件外部集；"
                           "几何一致性 + 第三方过切判据 + 半格余量对照",
                   "summary": summary, "parts": rows}, fh, ensure_ascii=False, indent=1)

    print("\n%d/%d 件｜几何逐柱一致 ≥ %.3f%%（最差），覆盖 IoU ≥ %.4f，"
          "最大差 %.3fmm，平均覆盖 %.2f%%"
          % (len(ok), len(rows), summary["geometry_min_within_0_02mm_pct"] or 0,
             summary["geometry_min_footprint_iou"] or 0,
             summary["geometry_max_abs_diff_mm"] or 0,
             summary["coverage_mean_pct"] or 0))
    print("OCL 判过切：B2 %d/%d（浅 %d / 深 %d，其中 %d 在无覆盖柱），"
          "B3 %d/%d（深 %d）"
          % (summary["B2_gouge_points"], summary["B2_sampled_points"],
             summary["B2_shallow"], summary["B2_deep"],
             summary["B2_deep_on_uncovered_columns"],
             summary["B3_gouge_points"], summary["B3_sampled_points"],
             summary["B3_deep"]))
    print("我们自己的双线：B2 %d/%d，B3 %d/%d" %
          (summary["B2_our_dual_pass"], len(ok),
           summary["B3_our_dual_pass"], len(ok)))
    if not args.no_guard:
        print("半格 guard=%.2fmm：过切 %d（深 %d），双线 %d/%d，"
              "残留 %.0f→%.0f mm³"
              % (summary["guard_mm"], summary["guard_gouge_points"],
                 summary["guard_deep"], summary["guard_our_dual_pass"], len(ok),
                 summary["B2_residual_mm3"], summary["guard_residual_mm3"]))
    print("耗时 %.1fs → %s" % (summary["seconds"], OUT))


if __name__ == "__main__":
    main()
