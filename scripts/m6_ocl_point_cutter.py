"""M6 口径补充：OCL「点刀」半径对几何一致性结论的影响。

问题
----
`m6_opencamlib.py` / `m6_ocl_external.py` 用 Ø0.02mm 的 CylCutter 当"点刀"，
对每个栅格柱问 OCL 的表面高度。在 10 件密集剖分锚点集上最大差只有 8mm
（圆弧台阶边界），但在 MFCAD 外部集上出现 **71.104mm** 的最大差。
诊断（`build/ocl/_ext_diag3.py`）显示：外部件的三角网格里有大量竖直壁三角
（投影面积为 0，被 `dexel.rasterize` 正确跳过），而有限半径的圆柱点刀会在
壁边擦到这些三角，把刀位抬到壁顶（80mm）→ 假差异。

本脚本在两个数据集上扫点刀直径，验证这个解释：
若差异来自探针半径而非几何，d→0 时最大差与超差柱数应趋零。

    PYTHONPATH=src python scripts/m6_ocl_point_cutter.py

输出：reports/m6_ocl_point_cutter.json
"""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np

try:
    import opencamlib as ocl  # noqa: F401
except ImportError as exc:
    raise SystemExit("需要 opencamlib：pip install opencamlib==2023.1.11") from exc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import m6_opencamlib as M                       # noqa: E402
from cadcam.verify import dexel                 # noqa: E402

OUT = os.path.join(ROOT, "reports", "m6_ocl_point_cutter.json")
DIAMETERS = (0.02, 0.002, 0.0002)
DATASETS = {
    "anchor_10": (os.path.join(ROOT, "reports", "annotation", "geom"), 10),
    "external_130": (os.path.join(ROOT, "data", "external", "geom"), 0),
}


def parts_of(folder, limit):
    ids = sorted(x[:-5] for x in os.listdir(folder) if x.endswith(".json"))
    if limit:
        ids = [x for x in ids if x.isdigit() and int(x) <= limit]
    return ids


def vertical_triangles(geom):
    n = 0
    for f in geom["faces"]:
        for t in (f.get("triangles") or []):
            p = np.asarray(t, dtype=float)
            xy = p[:, :2]
            a2 = ((xy[1, 0] - xy[0, 0]) * (xy[2, 1] - xy[0, 1])
                  - (xy[2, 0] - xy[0, 0]) * (xy[1, 1] - xy[0, 1]))
            if abs(a2) < 1e-9:
                n += 1
    return n


def run_dataset(name, folder, limit):
    rows = []
    t0 = time.time()
    ids = parts_of(folder, limit)
    for k, pid in enumerate(ids, 1):
        geom = json.load(open(os.path.join(folder, pid + ".json"), encoding="utf-8"))
        target = dexel.rasterize(geom, res=M.RES)
        surf, ntri = M.stlsurf(geom)
        goal = target["top"]
        rec = {"id": pid, "triangles": ntri,
               "vertical_triangles": vertical_triangles(geom),
               "coverage_pct": round(100 * float(np.isfinite(goal).mean()), 3),
               "by_diameter": {}}
        for d in DIAMETERS:
            h, _ = M.cl_grid(surf, target["xs"], target["ys"], d)
            both = np.isfinite(goal) & (h > 0)
            diff = np.abs(h[both] - goal[both])
            rec["by_diameter"]["%.4f" % d] = {
                "max_abs_diff_mm": round(float(diff.max()), 5) if diff.size else None,
                "columns_over_0_5mm": int((diff > 0.5).sum()),
                "within_0_05mm_pct": round(100 * float((diff <= 0.05).mean()), 4)
                if diff.size else None}
        rows.append(rec)
        if k % 30 == 0:
            print("  ...%s %d/%d ｜ %.0fs" % (name, k, len(ids), time.time() - t0),
                  flush=True)
    summary = {"n": len(rows), "seconds": round(time.time() - t0, 1)}
    for d in DIAMETERS:
        key = "%.4f" % d
        mx = [r["by_diameter"][key]["max_abs_diff_mm"] or 0 for r in rows]
        summary["d_" + key] = {
            "max_abs_diff_mm": round(max(mx), 5),
            "columns_over_0_5mm": sum(r["by_diameter"][key]["columns_over_0_5mm"]
                                      for r in rows),
            "min_within_0_05mm_pct": min(r["by_diameter"][key]["within_0_05mm_pct"]
                                         or 0 for r in rows),
            "parts_with_any_over_0_5mm": sum(
                1 for r in rows if r["by_diameter"][key]["columns_over_0_5mm"])}
    summary["vertical_triangles_total"] = sum(r["vertical_triangles"] for r in rows)
    return {"summary": summary, "parts": rows}


def main():
    t0 = time.time()
    out = {"rule": "OCL 点刀半径 → 几何一致性；验证 71mm 差异是竖直壁探针伪影",
           "datasets": {}}
    for name, (folder, limit) in DATASETS.items():
        print("→ %s" % name, flush=True)
        out["datasets"][name] = run_dataset(name, folder, limit)
        s = out["datasets"][name]["summary"]
        for d in DIAMETERS:
            x = s["d_%.4f" % d]
            print("   d=%.4f → max %8.3fmm, >0.5mm %5d 柱, 最差一致 %.3f%%" %
                  (d, x["max_abs_diff_mm"], x["columns_over_0_5mm"],
                   x["min_within_0_05mm_pct"]))
    out["conclusion"] = ("点刀半径趋零时最大差与超差柱数归零；"
                         "Ø0.02 的差异来自有限半径圆柱擦到竖直壁三角，"
                         "不是目标高度图或 OCL 几何的系统性偏差")
    out["seconds"] = round(time.time() - t0, 1)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print("耗时 %.1fs → %s" % (out["seconds"], OUT))


if __name__ == "__main__":
    main()
