"""M3 复现误差验收口径：实证决策分析。

不站队、不挑好看的数，回答四个问题：

  Q1  10mm 的 max 偏差是「切多了(过切, 报废)」还是「切少了(残留, 可补刀)」？
  Q2  超 0.5mm 的柱分布在哪？是不是集中在台阶边界 / 刀具进不去的窄缝？
  Q3  max 随栅格分辨率变化剧烈吗？剧烈 ⇒ 是离散化伪影，不适合做验收指标。
  Q4  如果补一把更细的刀，max 能不能也达标？能 ⇒ 口径争论只是刀具选择问题。

输出 reports/m3_criterion_analysis.json，供 docs/M3-ACCEPTANCE-CRITERION.md 引用。
用法：PYTHONPATH=src python scripts/m3_criterion_analysis.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import deque

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from cadcam.modalities.cam import gcode_gen, heightmap, process  # noqa: E402
from cadcam.modalities.mfr.instances import group               # noqa: E402
from cadcam.verify import dexel                                 # noqa: E402

GEOM = os.path.join(ROOT, "reports", "annotation", "geom")
LABELS = os.path.join(ROOT, "data", "zenodo_rptu", "labels")
REF_DIR = os.path.join(ROOT, "reports", "m3_gcode_ref")
B1_DIR = os.path.join(ROOT, "reports", "m3_gcode")
OUT = os.path.join(ROOT, "reports", "m3_criterion_analysis.json")
PARTS = [str(i) for i in range(1, 11)]
SENS_PARTS = ("1", "8", "10")


def load(part, res):
    with open(os.path.join(GEOM, "%s.json" % part), encoding="utf-8") as fh:
        geom = json.load(fh)
    with open(os.path.join(LABELS, "%s.json" % part), encoding="utf-8") as fh:
        lab = json.load(fh)
    labels = [lab["faces"][str(f["index"])]["family"] for f in geom["faces"]]
    plan = process.plan(geom, group(geom, labels,
                                    families={x: x for x in set(labels)}))
    return plan, dexel.rasterize(geom, res=res)


def simulate(text, target, plan, res, radius_by_op, default_r):
    v = gcode_gen.validate(text)
    return dexel.simulate(v["ir"], target, plan["stock"]["top_z"],
                          tool_radius_mm=default_r, res=res,
                          tool_radius_by_op=radius_by_op)


def _pad_shift(arr, dy, dx, fill):
    """带边界填充的平移，避免 np.roll 环绕产生假边界。"""
    out = np.full_like(arr, fill, dtype=float)
    ny, nx = arr.shape
    ys0, ys1 = max(0, dy), min(ny, ny + dy)
    yd0, yd1 = max(0, -dy), min(ny, ny - dy)
    xs0, xs1 = max(0, dx), min(nx, nx + dx)
    xd0, xd1 = max(0, -dx), min(nx, nx - dx)
    out[ys0:ys1, xs0:xs1] = arr[yd0:yd1, xd0:xd1]
    return out


def decompose(cur, target, res):
    """把偏差拆成过切 / 残留，并刻画超差柱的空间位置。"""
    goal = target["top"]
    mask = np.isfinite(goal)
    gouge = np.where(mask, np.maximum(0.0, goal - cur), 0.0)
    resid = np.where(mask, np.maximum(0.0, cur - goal), 0.0)
    dev = np.where(mask, np.abs(cur - goal), 0.0)
    valid = dev[mask]
    over = (dev > 0.5) & mask

    # 台阶边界：目标高度在 4 邻域发生跳变的柱
    g = np.where(mask, goal, np.nan)
    edge = np.zeros(mask.shape, dtype=bool)
    for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        nb = _pad_shift(g, dy, dx, np.nan)
        diff = np.abs(g - nb)
        edge |= np.nan_to_num(diff, nan=0.0) > 0.1
    # 边界带 = 边界膨胀 1 格（0.5mm，等于当前最小刀半径的量级）
    band = edge.copy()
    for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (1, -1), (-1, 1), (-1, -1)):
        band |= _pad_shift(edge.astype(float), dy, dx, 0.0) > 0.5

    # 超差柱连通簇（4 邻域 BFS），看是连成大片还是零散孤立点
    seen = np.zeros(mask.shape, dtype=bool)
    sizes = []
    for y, x in zip(*np.where(over)):
        if seen[y, x]:
            continue
        q, seen[y, x], size = deque([(y, x)]), True, 0
        while q:
            cy, cx = q.popleft()
            size += 1
            for ny2, nx2 in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
                if (0 <= ny2 < over.shape[0] and 0 <= nx2 < over.shape[1]
                        and over[ny2, nx2] and not seen[ny2, nx2]):
                    seen[ny2, nx2] = True
                    q.append((ny2, nx2))
        sizes.append(size)
    sizes.sort(reverse=True)

    cell = res * res
    return {
        "columns": int(mask.sum()),
        "mean_abs_deviation_mm": round(float(valid.mean()), 4),
        "p95_mm": round(float(np.percentile(valid, 95)), 3),
        "p99_mm": round(float(np.percentile(valid, 99)), 3),
        "max_mm": round(float(valid.max()), 3),
        "max_gouge_depth_mm": round(float(gouge.max()), 3),
        "max_residual_height_mm": round(float(resid.max()), 3),
        "gouge_volume_mm3": round(float(gouge.sum() * cell), 2),
        "residual_volume_mm3": round(float(resid.sum() * cell), 2),
        "over_0_5mm_columns": int(over.sum()),
        "over_0_5mm_pct": round(100.0 * float(over.sum()) / max(int(mask.sum()), 1), 3),
        "over_0_5mm_gouge_columns": int(((gouge > 0.5) & mask).sum()),
        "over_0_5mm_residual_columns": int(((resid > 0.5) & mask).sum()),
        "over_0_5mm_in_edge_band_pct": round(
            100.0 * float((over & band).sum()) / max(int(over.sum()), 1), 2)
            if over.any() else None,
        "over_0_5mm_clusters": len(sizes),
        "largest_cluster_columns": sizes[0] if sizes else 0,
        "largest_cluster_share_pct": round(100.0 * sizes[0] / max(int(over.sum()), 1), 2)
            if over.any() else None,
        # 体积误差 / 投影面积 = 平均绝对偏差，物理上就是「平均少切/多切了多厚」
        "equivalent_uniform_layer_mm": round(float(valid.mean()), 4),
    }


def b2(plan, target, res, passes=None, ap=4.0):
    kwargs = {"res": res, "ap": ap}
    if passes is not None:
        kwargs["passes"] = passes
    text, meta = heightmap.generate(plan, target, **kwargs)
    r_by_op = {o["label"]: o["tool_d_mm"] / 2.0
               for o in meta["ops_detail"] if not o.get("empty")}
    m = simulate(text, target, plan, res, r_by_op, 3.0)
    return m, text, meta


def main():
    out = {"res_mm": 0.5, "parts": {}, "sensitivity": {}, "fine_tool": {}}

    # ---- Q1 / Q2：三路全量 10 件 ----
    print("== Q1/Q2 三路偏差分解（0.5mm） ==")
    for part in PARTS:
        plan, target = load(part, 0.5)
        with open(os.path.join(REF_DIR, "%s.nc" % part), encoding="utf-8") as fh:
            ta = fh.read()
        ma = simulate(ta, target, plan, 0.5, {}, 2.5)
        with open(os.path.join(B1_DIR, "%s.nc" % part), encoding="utf-8") as fh:
            tb = fh.read()
        rb = {o["label"]: o["speeds"]["tool_d_mm"] / 2.0 for o in plan["ops"]}
        mb = simulate(tb, target, plan, 0.5, rb, 3.0)
        mc, _, _ = b2(plan, target, 0.5)
        row = {"A": decompose(ma["cur"], target, 0.5),
               "B1": decompose(mb["cur"], target, 0.5),
               "B2": decompose(mc["cur"], target, 0.5)}
        out["parts"][part] = row
        print("  件 %-2s | A max_gouge %5.1f max_resid %5.1f | B1 %5.1f/%5.1f | "
              "B2 gouge %4.1f resid %5.1f 超差 %.2f%% (其中残留柱 %d)"
              % (part, row["A"]["max_gouge_depth_mm"], row["A"]["max_residual_height_mm"],
                 row["B1"]["max_gouge_depth_mm"], row["B1"]["max_residual_height_mm"],
                 row["B2"]["max_gouge_depth_mm"], row["B2"]["max_residual_height_mm"],
                 row["B2"]["over_0_5mm_pct"], row["B2"]["over_0_5mm_residual_columns"]))

    b2rows = [r["B2"] for r in out["parts"].values()]
    out["summary"] = {
        "B2_zero_gouge_parts": sum(r["max_gouge_depth_mm"] <= 1e-6 for r in b2rows),
        "B2_all_max_is_residual": all(
            r["max_gouge_depth_mm"] <= 1e-6 and r["max_mm"] > 0 for r in b2rows),
        "B2_worst_mean_mm": max(r["mean_abs_deviation_mm"] for r in b2rows),
        "B2_worst_max_mm": max(r["max_mm"] for r in b2rows),
        "B2_worst_over_pct": max(r["over_0_5mm_pct"] for r in b2rows),
        "B2_worst_edge_band_pct": max(
            (r["over_0_5mm_in_edge_band_pct"] or 0) for r in b2rows),
        "B2_worst_largest_cluster_share_pct": max(
            (r["largest_cluster_share_pct"] or 0) for r in b2rows),
    }

    # ---- Q3：分辨率敏感性 ----
    print("\n== Q3 栅格分辨率敏感性 ==")
    for part in SENS_PARTS:
        plan05, t05 = load(part, 0.5)
        plan10, t10 = load(part, 1.0)
        m05, _, _ = b2(plan05, t05, 0.5)
        m10, _, _ = b2(plan10, t10, 1.0)
        d05, d10 = decompose(m05["cur"], t05, 0.5), decompose(m10["cur"], t10, 1.0)
        out["sensitivity"][part] = {"res_0_5": d05, "res_1_0": d10}
        print("  件 %-2s | res0.5 mean %.3f max %5.1f 超差 %5.2f%% | "
              "res1.0 mean %.3f max %5.1f 超差 %5.2f%%"
              % (part, d05["mean_abs_deviation_mm"], d05["max_mm"], d05["over_0_5mm_pct"],
                 d10["mean_abs_deviation_mm"], d10["max_mm"], d10["over_0_5mm_pct"]))

    # ---- Q4：补细刀的上界实验 ----
    print("\n== Q4 加 Ø0.5 精刀（分辨率上界，非可加工刀路） ==")
    for part in SENS_PARTS:
        plan, target = load(part, 0.5)
        t0 = time.time()
        m2, _, meta2 = b2(plan, target, 0.5)
        m3, text3, meta3 = b2(plan, target, 0.5,
                              passes=[(6.0, 0.6), (1.5, 0.6), (0.5, 0.6)])
        d2, d3 = decompose(m2["cur"], target, 0.5), decompose(m3["cur"], target, 0.5)
        out["fine_tool"][part] = {
            "two_passes": d2, "three_passes": d3,
            "gcode_lines_2": meta2["gcode_lines"],
            "gcode_lines_3": meta3["gcode_lines"],
            "elapsed_s": round(time.time() - t0, 1),
        }
        print("  件 %-2s | 2遍 mean %.3f max %5.1f 残留 %7.1f | "
              "3遍 mean %.3f max %5.1f 残留 %7.1f | 行数 %d→%d"
              % (part, d2["mean_abs_deviation_mm"], d2["max_mm"], d2["residual_volume_mm3"],
                 d3["mean_abs_deviation_mm"], d3["max_mm"], d3["residual_volume_mm3"],
                 meta2["gcode_lines"], meta3["gcode_lines"]))

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print("\n汇总:", json.dumps(out["summary"], ensure_ascii=False))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
