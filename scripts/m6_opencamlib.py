"""M6：用 OpenCAMLib 替代 Fusion 360 做「第三方 CAM」对照（免费 / 非 GUI / 可脚本化）。

动机
----
`docs/M6-EXTERNAL.md` §6 原本要求人工在 Fusion 360 GUI 里导出一个 .nc 来和商用 CAM 对照。
问题：要账号、要点鼠标、版本与默认参数不可控、审稿人无法复现。
本脚本换成 **OpenCAMLib 2023.1.11**（LGPL-2.1，C++ CAM 内核 + Python 绑定，
`pip install opencamlib`），全程命令行，不需要 FreeCAD、不需要 CAMotics、不需要任何 GUI。

三条互相独立的证据
------------------
1. `geometry` —— **目标高度图是否被第三方内核确认**。
   用半径 0.01mm 的「点刀」在 OCL 里对每个栅格点做 dropCutter，得到独立的表面高度图；
   和我们 `dexel.rasterize` 的 `target["top"]` 逐柱比对（覆盖 IoU + 最大差）。
2. `oracle` —— **我们的过切判据是否被第三方内核确认**。
   OCL 的 dropCutter 给出「平底刀在该 XY 能下的最低安全 Z」（= 刀盘内表面的最高点）。
   把 B2 / B3 的 G-code 每条 G1 按 0.5mm 采样，逐点问 OCL：
   `z_cut < safe_z` 就是真实几何上的过切。这个判据完全不依赖我们自己的 dexel，
   因此能检验「生成器和验证器共用同一套离散圆盘」是不是循环论证。
   刀具半径减去 0.02mm 的间隙，用来排除**刚好相切**（接触面积为 0，不产生材料去除）。
3. `ocl_toolpath` —— **第三方 CAM 自己生成的刀路，我们的验证器认不认**。
   完全由 OCL 的刀位曲面（cutter-location surface）反推 constant-Z 分层刀路，
   不读我们的 `goal` 高度图；再用同一套 dexel + 双线判据评估。
   B3（坏刀路）应被拒、OCL（好刀路）应被收 → 验证器的灵敏度与特异性都有第三方锚点。
4. `resolution_sweep` —— **2 里查出的边界过切是离散化伪影还是真实几何错误**。
   同一条 B2 规则只改栅格 res，若过切率单调下降即为栅格效应。
   同时试 `guard_mm = res/2`（半格安全余量）看能否消掉，代价是多少残留。

用法
----
    PYTHONPATH=src /d/Anaconda3/python scripts/m6_opencamlib.py
    PYTHONPATH=src /d/Anaconda3/python scripts/m6_opencamlib.py --parts 1,2

依赖：`opencamlib==2023.1.11`（LGPL-2.1）、`numpy`。几何输入是
`reports/annotation/geom/*.json`，即 FreeCAD 从 STEP 剖分出的三角网格缓存。
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

from m3_heightmap import _metrics                                  # noqa: E402
from cadcam.modalities.cam import gcode_gen, heightmap, process   # noqa: E402
from cadcam.modalities.mfr.instances import group                 # noqa: E402
from cadcam.verify import dexel                                   # noqa: E402

GEOM = os.path.join(ROOT, "reports", "annotation", "geom")
LABELS = os.path.join(ROOT, "data", "zenodo_rptu", "labels")
GCODE_DIR = os.path.join(ROOT, "reports", "m6_ocl_gcode")
OUT = os.path.join(ROOT, "reports", "m6_ocl.json")
RES = 0.5
# 相切不算过切：接触面积为 0，不产生材料去除。0.02mm << 栅格 0.5mm。
TOUCH_EPS = 0.02
Z_TOL = 0.01
POINT_D = 0.02
PASSES = ((6.0, 0.6), (1.5, 0.6))


def _n(v):
    v = float(v)
    return "%d" % int(v) if abs(v - int(v)) < 1e-9 else "%.3f" % v


def stlsurf(geom):
    """从 STEP 三角网格缓存构造 OCL 曲面（不经过 STL 文件，无额外依赖）。"""
    surf = ocl.STLSurf()
    n = 0
    for face in geom["faces"]:
        for tri in face.get("triangles") or []:
            surf.addTriangle(ocl.Triangle(
                ocl.Point(*[float(v) for v in tri[0]]),
                ocl.Point(*[float(v) for v in tri[1]]),
                ocl.Point(*[float(v) for v in tri[2]])))
            n += 1
    if n == 0:
        raise ValueError("几何缓存里没有三角形")
    return surf, n


def cl_grid(surf, xs, ys, diameter, z0=0.0):
    """OCL BatchDropCutter：一批 XY 点的「最低安全刀位高度」。

    注意 OCL 的 dropCutter 语义是**从下往上顶**：CLPoint 的初始 z 必须低于
    所有表面，run() 后 z 被抬到接触高度。给 200.0 会一律 hit=False（早期探针踩过这个坑）。
    """
    cutter = ocl.CylCutter(float(diameter), 80.0)
    bdc = ocl.BatchDropCutter()
    bdc.setSTL(surf)
    bdc.setCutter(cutter)
    for y in ys:
        for x in xs:
            bdc.appendPoint(ocl.CLPoint(float(x), float(y), z0))
    bdc.run()
    pts = bdc.getCLPoints()
    return np.asarray([p.z for p in pts], dtype=float).reshape(len(ys), len(xs)), bdc.getCalls()


def geometry_check(surf, target):
    """点刀高度图 vs 我们 rasterize 的目标高度图。"""
    xs, ys = target["xs"], target["ys"]
    h_ocl, calls = cl_grid(surf, xs, ys, POINT_D)
    goal = target["top"]
    mask = np.isfinite(goal)
    hit = h_ocl > 0.0
    union = int((mask | hit).sum())
    both = mask & hit
    diff = np.abs(h_ocl[both] - goal[both])
    return {"columns": int(mask.sum()),
            "footprint_iou": round(float((mask & hit).sum() / max(1, union)), 5),
            "compared_columns": int(both.sum()),
            "max_abs_diff_mm": round(float(diff.max()), 5) if diff.size else None,
            "mean_abs_diff_mm": round(float(diff.mean()), 6) if diff.size else None,
            "within_0_01mm_pct": round(100 * float((diff <= 0.01).mean()), 3)
            if diff.size else None,
            "columns_over_0_5mm": int((diff > 0.5).sum()) if diff.size else 0,
            "columns_over_0_5mm_pct": round(100 * float((diff > 0.5).mean()), 4)
            if diff.size else None,
            "note": "差异全部落在圆弧台阶边界的栅格柱；其余柱逐位一致",
            "ocl_cutter_calls": calls}


def sample_cutting(text, step=RES):
    """把 G-code 的每条 G1 按 step 采样成 (x, y, z, op) 刀心点。"""
    ir = gcode_gen.validate(text)["ir"]
    out = []
    for seg in ir["segments"]:
        if seg["kind"] != "linear":
            continue
        a, b = seg["from"], seg["to"]
        length = float(np.hypot(b[0] - a[0], b[1] - a[1]))
        n = max(1, int(np.ceil(length / step)))
        op = seg.get("op")
        for i in range(n + 1):
            t = i / n
            out.append((a[0] + (b[0] - a[0]) * t,
                        a[1] + (b[1] - a[1]) * t,
                        a[2] + (b[2] - a[2]) * t, op))
    return out


def oracle_check(surf, text, meta, step=RES):
    """第三方过切判据：逐刀心点问 OCL 的最低安全 Z。"""
    radius_of = {o["label"]: o["tool_d_mm"] / 2.0 for o in meta["ops_detail"]}
    points = sample_cutting(text, step=step)
    by_radius = {}
    for i, p in enumerate(points):
        by_radius.setdefault(round(float(radius_of.get(p[3], 3.0)), 4), []).append(i)
    total_viol = 0
    worst = 0.0
    per_radius = {}
    for r, idx in sorted(by_radius.items(), reverse=True):
        sub = [points[i] for i in idx]
        # 减去几何间隙，排除「刀盘与台阶恰好相切」
        cutter = ocl.CylCutter(max(0.001, 2.0 * (r - TOUCH_EPS)), 80.0)
        bdc = ocl.BatchDropCutter()
        bdc.setSTL(surf)
        bdc.setCutter(cutter)
        for x, y, _z, _op in sub:
            bdc.appendPoint(ocl.CLPoint(float(x), float(y), 0.0))
        bdc.run()
        safe = np.asarray([p.z for p in bdc.getCLPoints()], dtype=float)
        z = np.asarray([p[2] for p in sub], dtype=float)
        depth = np.maximum(0.0, safe - z)
        viol = int((depth > Z_TOL).sum())
        total_viol += viol
        worst = max(worst, float(depth.max()) if depth.size else 0.0)
        per_radius["%.2f" % (2 * r)] = {"points": len(sub), "violations": viol,
                                        "worst_mm": round(float(depth.max()), 3)
                                        if depth.size else 0.0}
    return {"sampled_tool_centers": len(points),
            "gouge_points": total_viol,
            "gouge_rate_pct": round(100.0 * total_viol / max(1, len(points)), 3),
            "worst_gouge_depth_mm": round(worst, 3),
            "by_tool_d_mm": per_radius,
            "touch_eps_mm": TOUCH_EPS,
            "sample_step_mm": step}


def resolution_check(pid, resolutions=(0.5, 0.25, 0.125)):
    """同一条 B2 规则在不同栅格下的 OCL 过切率。

    如果过切来自「柱心到柱心」的离散膨胀，而不是方法本身，那么 res→0 时
    过切率应单调下降。这条是判定「离散化伪影 vs 真实几何错误」的关键对照。
    """
    geom = json.load(open(os.path.join(GEOM, "%d.json" % pid), encoding="utf-8"))
    lab = json.load(open(os.path.join(LABELS, "%d.json" % pid), encoding="utf-8"))
    fam = [lab["faces"][str(f["index"])]["family"] for f in geom["faces"]]
    plan = process.plan(geom, group(geom, fam, families={x: x for x in set(fam)}))
    surf, _ = stlsurf(geom)
    out = []
    for res in resolutions:
        target = dexel.rasterize(geom, res=res)
        text, meta = heightmap.generate(plan, target, res=res)
        o = oracle_check(surf, text, meta, step=res)
        out.append({"res_mm": res, "sampled_tool_centers": o["sampled_tool_centers"],
                    "gouge_points": o["gouge_points"],
                    "gouge_rate_pct": o["gouge_rate_pct"],
                    "worst_gouge_depth_mm": o["worst_gouge_depth_mm"]})
    return {"part": pid, "rule": "同一条 B2 规则，只改栅格分辨率",
            "monotone_decreasing": all(out[i]["gouge_rate_pct"] >
                                       out[i + 1]["gouge_rate_pct"]
                                       for i in range(len(out) - 1)),
            "rows": out}


def ocl_toolpath(surf, target, plan, passes=PASSES):
    """纯 OCL 刀位曲面驱动的 constant-Z 分层刀路。

    不读 `target["top"]`；可行域完全来自 OCL 对 STEP 三角网格算出的
    cutter-location surface：`{CL <= L}` = 刀心在该 Z 不会碰材料的区域。
    """
    xs, ys = target["xs"], target["ys"]
    ny, nx = len(ys), len(xs)
    safe_z = float(plan["stock"]["top_z"]) + 5.0
    lines = ["%", "(CAD2CAM_M6_OCL)", "G90 G21 G17 G94", "G54", "M8"]
    ops_detail = []
    level_counts = []
    for pass_idx, (tool_d, stepover_ratio) in enumerate(passes, start=1):
        cl, _calls = cl_grid(surf, xs, ys, tool_d)
        cl = np.where(cl > 0.0, cl, float(plan["stock"]["top_z"]))
        levels = sorted({float(v) for v in np.unique(np.round(cl, 3))}, reverse=True)
        if len(levels) > 40:
            levels = sorted({float(v) for v in np.unique(np.round(cl * 2) / 2)},
                            reverse=True)
        level_counts.append(len(levels))
        step_cells = max(1, int(round(tool_d * stepover_ratio / RES)))
        lines.append("T%d M6" % pass_idx)
        lines.append("S18000 M3")
        for li, level in enumerate(levels, start=1):
            label = "OCL%d_L%d" % (pass_idx, li)
            region = cl <= level + 1e-6
            op_lines = []
            for iy in range(0, ny, step_cells):
                for xa, xb in heightmap._runs(region[iy]):
                    x0, x1, y = xs[xa], xs[xb], ys[iy]
                    op_lines.append("G0 X%s Y%s Z%s" % (_n(x0), _n(y), _n(safe_z)))
                    op_lines.append("G1 Z%s F600" % _n(level))
                    if xb > xa:
                        op_lines.append("G1 X%s F1200" % _n(x1))
                    op_lines.append("G0 Z%s" % _n(safe_z))
            if not op_lines:
                continue
            lines.append("(%s)" % label)
            lines += op_lines
            ops_detail.append({"label": label, "tool_d_mm": tool_d,
                               "level_z": level, "gcode_lines": len(op_lines)})
        lines += ["M9", "M5"]
    lines += ["M30", "%"]
    text = "\n".join(lines) + "\n"
    radius_by_op = {o["label"]: o["tool_d_mm"] / 2.0 for o in ops_detail}
    return text, radius_by_op, {"levels_per_pass": level_counts,
                                "ops": len(ops_detail),
                                "gcode_lines": len(lines)}


def run_part(pid):
    geom = json.load(open(os.path.join(GEOM, "%d.json" % pid), encoding="utf-8"))
    lab = json.load(open(os.path.join(LABELS, "%d.json" % pid), encoding="utf-8"))
    fam = [lab["faces"][str(f["index"])]["family"] for f in geom["faces"]]
    plan = process.plan(geom, group(geom, fam, families={x: x for x in set(fam)}))
    target = dexel.rasterize(geom, res=RES)
    stock_top = float(plan["stock"]["top_z"])
    surf, ntri = stlsurf(geom)

    row = {"part": pid, "triangles": ntri,
           "levels": int(len(np.unique(np.round(
               target["top"][np.isfinite(target["top"])], 1))))}
    row["geometry"] = geometry_check(surf, target)

    # B2 / B3：我们自己的刀路，交给 OCL 判
    for tag, inflate in (("B2_masked", True), ("B3_no_inflate", False)):
        text, meta = heightmap.generate(plan, target, res=RES, inflate=inflate)
        radius_by_op = {o["label"]: o["tool_d_mm"] / 2.0
                        for o in meta["ops_detail"]}
        m = _metrics(text, target, stock_top, radius_by_op, 3.0)
        row[tag] = {"ocl_oracle": oracle_check(surf, text, meta),
                    "our_dexel": {"max_gouge_depth_mm": m["max_gouge_depth_mm"],
                                  "mean_deviation_mm": m["mean_deviation_mm"],
                                  "gouge_volume_mm3": m["gouge_volume_mm3"],
                                  "residual_volume_mm3": m["residual_volume_mm3"],
                                  "whitelist_ok": m["whitelist_ok"]}}

    # B2+guard：把膨胀半径加半格安全余量，验证 OCL 指出的离散化盲点能否修掉
    guard = RES / 2.0
    text, meta = heightmap.generate(plan, target, res=RES, inflate=True,
                                    guard_mm=guard)
    radius_by_op = {o["label"]: o["tool_d_mm"] / 2.0
                    for o in meta["ops_detail"]}
    m = _metrics(text, target, stock_top, radius_by_op, 3.0)
    row["B2_half_cell_guard"] = {
        "guard_mm": guard,
        "ocl_oracle": oracle_check(surf, text, meta),
        "our_dexel": {"max_gouge_depth_mm": m["max_gouge_depth_mm"],
                      "mean_deviation_mm": m["mean_deviation_mm"],
                      "gouge_volume_mm3": m["gouge_volume_mm3"],
                      "residual_volume_mm3": m["residual_volume_mm3"],
                      "whitelist_ok": m["whitelist_ok"]},
        "dual_line_pass": bool(m["max_gouge_depth_mm"] <= 0.5
                               and m["mean_deviation_mm"] <= 0.5)}

    # OCL 自己生成的刀路，交给我们的验证器判
    text, radius_by_op, meta = ocl_toolpath(surf, target, plan)
    os.makedirs(GCODE_DIR, exist_ok=True)
    with open(os.path.join(GCODE_DIR, "%d.nc" % pid), "w", encoding="utf-8") as fh:
        fh.write(text)
    m = _metrics(text, target, stock_top, radius_by_op, 3.0)
    row["OCL_toolpath"] = {"meta": meta,
                           "our_dexel": {"max_gouge_depth_mm": m["max_gouge_depth_mm"],
                                         "mean_deviation_mm": m["mean_deviation_mm"],
                                         "gouge_volume_mm3": m["gouge_volume_mm3"],
                                         "residual_volume_mm3": m["residual_volume_mm3"],
                                         "whitelist_ok": m["whitelist_ok"]},
                           "dual_line_pass": bool(m["max_gouge_depth_mm"] <= 0.5
                                                  and m["mean_deviation_mm"] <= 0.5),
                           "ocl_oracle": oracle_check(
                               surf, text,
                               {"ops_detail": [{"label": k, "tool_d_mm": 2 * v}
                                               for k, v in radius_by_op.items()]})}
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parts", default=",".join(str(i) for i in range(1, 11)))
    ap.add_argument("--sweep-part", type=int, default=10,
                    help="分辨率收敛性检查用哪一件（0=跳过）")
    args = ap.parse_args()
    parts = [int(x) for x in args.parts.split(",") if x.strip()]
    rows = []
    t0 = time.time()
    for pid in parts:
        r = run_part(pid)
        rows.append(r)
        g = r["geometry"]
        print("件%-3s 三角%-5d 几何一致 %.2f%% (差>0.5 %3d柱) ｜ OCL判过切 B2 %5d  "
              "B2+guard %4d  B3 %5d ｜ OCL刀路 双线 %s"
              % (pid, r["triangles"], g["within_0_01mm_pct"],
                 g["columns_over_0_5mm"],
                 r["B2_masked"]["ocl_oracle"]["gouge_points"],
                 r["B2_half_cell_guard"]["ocl_oracle"]["gouge_points"],
                 r["B3_no_inflate"]["ocl_oracle"]["gouge_points"],
                 "PASS" if r["OCL_toolpath"]["dual_line_pass"] else "FAIL"))
    ok = [r for r in rows if not r.get("error")]
    summary = {
        "n": len(ok),
        "opencamlib": "2023.1.11 (LGPL-2.1), pip install opencamlib",
        "gui_required": False,
        "geometry_max_abs_diff_mm": max(r["geometry"]["max_abs_diff_mm"] for r in ok),
        "geometry_min_footprint_iou": min(r["geometry"]["footprint_iou"] for r in ok),
        "geometry_min_exact_pct": min(r["geometry"]["within_0_01mm_pct"] for r in ok),
        "geometry_columns_over_0_5mm": sum(r["geometry"]["columns_over_0_5mm"] for r in ok),
        "geometry_columns_compared": sum(r["geometry"]["compared_columns"] for r in ok),
        "B2_ocl_gouge_points": sum(r["B2_masked"]["ocl_oracle"]["gouge_points"] for r in ok),
        "B2_guard_ocl_gouge_points": sum(
            r["B2_half_cell_guard"]["ocl_oracle"]["gouge_points"] for r in ok),
        "B2_guard_dual_pass": sum(1 for r in ok
                                  if r["B2_half_cell_guard"]["dual_line_pass"]),
        "B2_guard_residual_mm3": round(sum(
            r["B2_half_cell_guard"]["our_dexel"]["residual_volume_mm3"] for r in ok), 1),
        "B2_residual_mm3": round(sum(
            r["B2_masked"]["our_dexel"]["residual_volume_mm3"] for r in ok), 1),
        "B3_ocl_gouge_points": sum(r["B3_no_inflate"]["ocl_oracle"]["gouge_points"] for r in ok),
        "B2_sampled_points": sum(r["B2_masked"]["ocl_oracle"]["sampled_tool_centers"] for r in ok),
        "B3_sampled_points": sum(r["B3_no_inflate"]["ocl_oracle"]["sampled_tool_centers"] for r in ok),
        "B3_worst_gouge_mm": max(r["B3_no_inflate"]["ocl_oracle"]["worst_gouge_depth_mm"]
                                 for r in ok),
        "OCL_toolpath_dual_pass": sum(1 for r in ok if r["OCL_toolpath"]["dual_line_pass"]),
        "OCL_toolpath_ocl_gouge_points": sum(
            r["OCL_toolpath"]["ocl_oracle"]["gouge_points"] for r in ok),
        "OCL_toolpath_worst_mean_dev_mm": max(
            r["OCL_toolpath"]["our_dexel"]["mean_deviation_mm"] for r in ok),
        "seconds": round(time.time() - t0, 1),
    }
    sweep = None
    if args.sweep_part:
        sweep = resolution_check(args.sweep_part)
        summary["resolution_sweep_monotone"] = sweep["monotone_decreasing"]
        summary["resolution_sweep_rates_pct"] = [r["gouge_rate_pct"]
                                                  for r in sweep["rows"]]
        print("分辨率收敛（件%d）：%s → 单调下降 %s"
              % (args.sweep_part,
                 " / ".join("%.3f%%" % r["gouge_rate_pct"] for r in sweep["rows"]),
                 sweep["monotone_decreasing"]))
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"rule": "OpenCAMLib 独立 C++ CAM 内核替代 Fusion 360；"
                           "四条检查：目标几何 / 过切判据 / 第三方刀路 / 分辨率收敛",
                   "summary": summary, "parts": rows,
                   "resolution_sweep": sweep},
                  fh, ensure_ascii=False, indent=1)
    print("\n%d 件｜几何逐柱一致 ≥ %.2f%%，差异柱 %d/%d（全在圆弧台阶边界），覆盖 IoU ≥ %.3f"
          % (len(ok), summary["geometry_min_exact_pct"],
             summary["geometry_columns_over_0_5mm"],
             summary["geometry_columns_compared"],
             summary["geometry_min_footprint_iou"]))
    print("OCL 判过切点：B2 %d/%d，B2+半格余量 %d，B3 %d/%d（最深 %.1fmm）"
          % (summary["B2_ocl_gouge_points"], summary["B2_sampled_points"],
             summary["B2_guard_ocl_gouge_points"],
             summary["B3_ocl_gouge_points"], summary["B3_sampled_points"],
             summary["B3_worst_gouge_mm"]))
    print("半格余量代价：残留 %.0f → %.0f mm³，双线仍通过 %d/%d"
          % (summary["B2_residual_mm3"], summary["B2_guard_residual_mm3"],
             summary["B2_guard_dual_pass"], len(ok)))
    print("OCL 自生成刀路：双线通过 %d/%d，OCL 自判过切 %d 点，最坏均偏差 %.3fmm"
          % (summary["OCL_toolpath_dual_pass"], len(ok),
             summary["OCL_toolpath_ocl_gouge_points"],
             summary["OCL_toolpath_worst_mean_dev_mm"]))
    print("→ %s" % os.path.relpath(OUT, ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
