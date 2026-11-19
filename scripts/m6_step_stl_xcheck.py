"""M6 补充：STEP → FreeCAD Mesh → 二进制 STL → OpenCAMLib 的独立几何链路。

回答的问题
----------
`m6_opencamlib.py` 里 OCL 的曲面是我们用**自己的 geom JSON 缓存**逐三角
`addTriangle` 构造的。审稿人可以问：万一 JSON 缓存或解析代码本身有偏差，
「第三方验证」不就还是自证吗？

本脚本换一条几何来源：
    Zenodo STEP ──OCC/FreeCAD──> Part.Shape.tessellate(0.05)
                ──Mesh.Mesh──> 二进制 STL 文件
                ──ocl.STLReader──> OCL STLSurf ──> 点刀高度图 / 过切判据
全程不读 `reports/annotation/geom/*.json` 里的三角形，也不调用
`dexel.rasterize` 之外的自定义三角解析（rasterize 只作为被比对的一方）。

**独立性边界（论文必须照抄）**：读 STEP 的 B-rep 内核仍是 OCC（与主链相同），
所以这不是「第二个 CAD 内核」。独立的是三角化器 / 文件序列化 / 几何读取链路，
它检验的是「我们的几何缓存与 rasterize 有没有引入系统性偏差」。

用法
----
    PYTHONPATH=src python scripts/m6_step_stl_xcheck.py
    PYTHONPATH=src python scripts/m6_step_stl_xcheck.py --parts 1,2 --force-stl

依赖：FreeCAD 1.1.4 `freecadcmd`、opencamlib 2023.1.11、numpy。
输出：`reports/m6_step_stl_xcheck.json`、`reports/m6_step_stl/*.stl`
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
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
from cadcam.modalities.cam import heightmap, process              # noqa: E402
from cadcam.modalities.mfr.instances import group                 # noqa: E402
from cadcam.verify import dexel                                   # noqa: E402

STL_DIR = os.path.join(ROOT, "reports", "m6_step_stl")
MANIFEST = os.path.join(STL_DIR, "manifest.json")
MACRO = os.path.join(ROOT, "scripts", "_freecad_stl_export.py")
OUT = os.path.join(ROOT, "reports", "m6_step_stl_xcheck.json")
FREECAD_CANDIDATES = [
    r"E:\FreeCAD_1.1.4-Windows-x86_64-py311\bin\freecadcmd.exe",
    r"E:\FREECAD\bin\FreeCADCmd.exe",
]


def find_freecad():
    for p in FREECAD_CANDIDATES:
        if os.path.isfile(p):
            return p
    raise SystemExit("找不到 freecadcmd；请修改 FREECAD_CANDIDATES")


def ensure_stl(force=False):
    if os.path.isfile(MANIFEST) and not force:
        return json.load(open(MANIFEST, encoding="utf-8"))
    os.makedirs(STL_DIR, exist_ok=True)
    env = dict(os.environ, CADCAM_ROOT=ROOT, CADCAM_OUTDIR=STL_DIR)
    proc = subprocess.run([find_freecad(), MACRO], env=env,
                          capture_output=True, text=True, timeout=300)
    if "STL_EXPORT_OK" not in (proc.stdout or ""):
        raise RuntimeError("freecadcmd 导出失败：%s %s" %
                           ((proc.stdout or "")[-500:], (proc.stderr or "")[-500:]))
    return json.load(open(MANIFEST, encoding="utf-8"))


def read_stl(path):
    surf = ocl.STLSurf()
    ocl.STLReader(path, surf)
    return surf


def height_of(surf, target):
    h, calls = M.cl_grid(surf, target["xs"], target["ys"], M.POINT_D)
    return h, calls


def run_part(pid, manifest):
    geom = json.load(open(os.path.join(M.GEOM, "%d.json" % pid), encoding="utf-8"))
    lab = json.load(open(os.path.join(M.LABELS, "%d.json" % pid), encoding="utf-8"))
    fam = [lab["faces"][str(f["index"])]["family"] for f in geom["faces"]]
    plan = process.plan(geom, group(geom, fam, families={x: x for x in set(fam)}))
    target = dexel.rasterize(geom, res=M.RES)

    surf_json, ntri_json = M.stlsurf(geom)
    stl_rec = next(x for x in manifest["parts"] if x["part"] == pid)
    stl_path = os.path.join(STL_DIR, stl_rec["stl"])
    surf_stl = read_stl(stl_path)

    h_json, _ = height_of(surf_json, target)
    h_stl, calls_stl = height_of(surf_stl, target)
    both = (h_json > 0) & (h_stl > 0)
    d_two = np.abs(h_json[both] - h_stl[both])
    geo = M.geometry_check(surf_stl, target)

    row = {"part": pid, "stl_facets": stl_rec["facets"],
           "json_triangles": ntri_json,
           "bbox_match": [round(v, 3) for v in geom["bbox"]["min"]] ==
                         [round(v, 3) for v in stl_rec["bbox_min"]] and
                         [round(v, 3) for v in geom["bbox"]["max"]] ==
                         [round(v, 3) for v in stl_rec["bbox_max"]],
           "two_ocl_surfaces": {
               "compared_columns": int(both.sum()),
               "max_abs_diff_mm": round(float(d_two.max()), 6) if d_two.size else None,
               "mean_abs_diff_mm": round(float(d_two.mean()), 6) if d_two.size else None,
               "columns_over_0_5mm": int((d_two > 0.5).sum()) if d_two.size else 0,
               "identical_columns_pct": round(100 * float((d_two <= 0.01).mean()), 4)
               if d_two.size else None},
           "stl_vs_rasterize": {"max_abs_diff_mm": geo["max_abs_diff_mm"],
                                "mean_abs_diff_mm": geo["mean_abs_diff_mm"],
                                "within_0_01mm_pct": geo["within_0_01mm_pct"],
                                "columns_over_0_5mm": geo["columns_over_0_5mm"],
                                "footprint_iou": geo["footprint_iou"],
                                "ocl_cutter_calls": calls_stl}}
    for tag, inflate in (("B2_masked", True), ("B3_no_inflate", False)):
        text, meta = heightmap.generate(plan, target, res=M.RES, inflate=inflate)
        o = M.oracle_check(surf_stl, text, meta)
        row[tag] = {"sampled_tool_centers": o["sampled_tool_centers"],
                    "gouge_points": o["gouge_points"],
                    "gouge_rate_pct": o["gouge_rate_pct"],
                    "worst_gouge_depth_mm": o["worst_gouge_depth_mm"]}
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parts", default=",".join(str(i) for i in range(1, 11)))
    ap.add_argument("--force-stl", action="store_true")
    args = ap.parse_args()
    parts = [int(x) for x in args.parts.split(",") if x.strip()]
    t0 = time.time()
    manifest = ensure_stl(force=args.force_stl)
    rows = []
    for pid in parts:
        try:
            r = run_part(pid, manifest)
        except Exception as exc:                        # noqa: BLE001
            r = {"part": pid, "error": "%s: %s" % (type(exc).__name__, str(exc)[:140])}
        rows.append(r)
        if "error" in r:
            print("  件%-3s ERROR %s" % (pid, r["error"]))
        else:
            print("  件%-3s STL面片%-5d 两曲面一致 %.3f%% (差>0.5 %2d柱/最大 %.1fmm) ｜ "
                  "STL vs rasterize 差>0.5 %3d柱 ｜ OCL判过切 B2 %5d B3 %5d"
                  % (pid, r["stl_facets"],
                     r["two_ocl_surfaces"]["identical_columns_pct"],
                     r["two_ocl_surfaces"]["columns_over_0_5mm"],
                     r["two_ocl_surfaces"]["max_abs_diff_mm"] or 0,
                     r["stl_vs_rasterize"]["columns_over_0_5mm"],
                     r["B2_masked"]["gouge_points"],
                     r["B3_no_inflate"]["gouge_points"]))
    ok = [r for r in rows if "error" not in r]
    summary = {
        "n": len(ok), "errors": len(rows) - len(ok),
        "kernel": manifest["kernel"],
        "tessellation": manifest["tessellation"],
        "independence_scope": "独立三角化/序列化/读取链路；B-rep 内核仍为 OCC，非第二 CAD 内核",
        "bbox_all_match": all(r["bbox_match"] for r in ok) if ok else False,
        "two_ocl_surfaces_min_identical_pct": min(
            r["two_ocl_surfaces"]["identical_columns_pct"] or 0 for r in ok) if ok else None,
        "two_ocl_surfaces_max_abs_diff_mm": max(
            r["two_ocl_surfaces"]["max_abs_diff_mm"] or 0 for r in ok) if ok else None,
        "two_ocl_surfaces_total_columns_over_0_5mm": sum(
            r["two_ocl_surfaces"]["columns_over_0_5mm"] for r in ok),
        "stl_vs_rasterize_max_abs_diff_mm": max(
            r["stl_vs_rasterize"]["max_abs_diff_mm"] or 0 for r in ok) if ok else None,
        "stl_vs_rasterize_total_columns_over_0_5mm": sum(
            r["stl_vs_rasterize"]["columns_over_0_5mm"] for r in ok),
        "B2_gouge_points": sum(r["B2_masked"]["gouge_points"] for r in ok),
        "B3_gouge_points": sum(r["B3_no_inflate"]["gouge_points"] for r in ok),
        "B2_sampled_points": sum(r["B2_masked"]["sampled_tool_centers"] for r in ok),
        "B3_sampled_points": sum(r["B3_no_inflate"]["sampled_tool_centers"] for r in ok),
        "seconds": round(time.time() - t0, 1),
    }
    # 与 JSON 缓存链路的对照
    anchor_path = os.path.join(ROOT, "reports", "m6_ocl.json")
    if os.path.isfile(anchor_path):
        a = json.load(open(anchor_path, encoding="utf-8"))["summary"]
        summary["json_cache_chain"] = {
            "B2_gouge_points": a.get("B2_ocl_gouge_points"),
            "B3_gouge_points": a.get("B3_ocl_gouge_points"),
            "B2_sampled_points": a.get("B2_sampled_points"),
            "B3_sampled_points": a.get("B3_sampled_points")}
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"rule": "STEP→FreeCAD Mesh→binary STL→OCL STLReader 独立几何链路",
                   "summary": summary, "parts": rows}, fh, ensure_ascii=False, indent=1)
    print("\n%d 件｜两条 OCL 曲面逐柱一致 ≥ %.2f%%、最大差 %.4fmm；"
          "STL vs rasterize 最大差 %.4fmm、差>0.5mm 共 %d 柱"
          % (len(ok), summary["two_ocl_surfaces_min_identical_pct"] or 0,
             summary["two_ocl_surfaces_max_abs_diff_mm"] or 0,
             summary["stl_vs_rasterize_max_abs_diff_mm"] or 0,
             summary["stl_vs_rasterize_total_columns_over_0_5mm"]))
    print("OCL(STL) 判过切：B2 %d/%d，B3 %d/%d｜耗时 %.1fs → %s"
          % (summary["B2_gouge_points"], summary["B2_sampled_points"],
             summary["B3_gouge_points"], summary["B3_sampled_points"],
             summary["seconds"], OUT))


if __name__ == "__main__":
    main()
