"""第三方交叉验证：用 CAMotics 独立仿真同一份 G-code，比对材料去除体积。

为什么要做：我们的验证器（Z-dexel）是自己写的，审稿人有理由问"凭什么信它"。
CAMotics 是独立的开源 G-code 仿真器（表面网格 + 切削仿真，与 dexel 完全不同的算法），
把同一份 G-code 喂给它，比较**被切后工件的体积**。

判据是体积相对误差，不是几何 IoU —— 两个实现的高度场分辨率与边界处理不同，
体积是一阶量，能稳定反映"切掉的材料是否一致"。

    PYTHONPATH=src python scripts/m3_camsim_xcheck.py
    PYTHONPATH=src python scripts/m3_camsim_xcheck.py --parts 1,2
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import subprocess
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from cadcam.modalities.cam import gcode_gen, heightmap, process  # noqa: E402
from cadcam.modalities.mfr.instances import group               # noqa: E402
from cadcam.verify import dexel                                 # noqa: E402

CAMSIM = r"C:\Program Files (x86)\CAMotics\camsim.exe"
GEOM = os.path.join(ROOT, "reports", "annotation", "geom")
LABELS = os.path.join(ROOT, "data", "zenodo_rptu", "labels")
WORK = os.path.join(ROOT, "build", "camsim")
OUT = os.path.join(ROOT, "reports", "m3_camsim_xcheck.json")
RES = 0.5


def stl_volume(path):
    """二进制 STL 的封闭体积（散度定理，不依赖任何 CAD 库）。"""
    with open(path, "rb") as fh:
        fh.read(80)
        n = struct.unpack("<I", fh.read(4))[0]
        raw = fh.read(n * 50)
    arr = np.frombuffer(raw, dtype=np.uint8).reshape(n, 50)
    tri = arr[:, 12:48].copy().view("<f4").reshape(n, 3, 3).astype(np.float64)
    v = np.einsum("ij,ij->i", tri[:, 0], np.cross(tri[:, 1], tri[:, 2])) / 6.0
    return float(abs(v.sum())), n


def dexel_volume(plan, target, text, radius_by_op):
    ir = gcode_gen.validate(text)["ir"]
    m = dexel.simulate(ir, target, plan["stock"]["top_z"], tool_radius_mm=3.0,
                       res=RES, tool_radius_by_op=radius_by_op)
    cur = m["cur"]
    bot = plan["stock"]["bottom_z"]
    mask = np.isfinite(cur)
    vol = float(np.where(mask, cur - bot, 0.0).sum()) * (RES ** 2)
    return vol, m


def run_part(pid):
    g = json.load(open(os.path.join(GEOM, "%d.json" % pid), encoding="utf-8"))
    lab = json.load(open(os.path.join(LABELS, "%d.json" % pid), encoding="utf-8"))
    fam = [lab["faces"][str(f["index"])]["family"] for f in g["faces"]]
    inst = group(g, fam, families={x: x for x in set(fam)})
    plan = process.plan(g, inst)
    target = dexel.rasterize(g, res=RES)
    text, meta = heightmap.generate(plan, target, res=RES)
    radius_by_op = {o["label"]: o["tool_d_mm"] / 2.0 for o in meta["ops_detail"]}

    d = os.path.join(WORK, "part%d" % pid)
    os.makedirs(d, exist_ok=True)
    nc = os.path.join(d, "part.nc")
    with open(nc, "w", encoding="utf-8") as fh:
        fh.write(text)
    st = plan["stock"]
    tools = {}
    for i, o in enumerate(meta["ops_detail"], start=1):
        tools[str(i)] = {"units": "metric", "shape": "cylindrical",
                         "length": 80.0, "diameter": float(o["tool_d_mm"])}
    proj = {"units": "metric", "resolution-mode": "manual", "resolution": RES,
            "tools": tools,
            "workpiece": {"automatic": False, "margin": 0,
                          "bounds": {"min": [st["min"][0], st["min"][1], st["bottom_z"]],
                                     "max": [st["max"][0], st["max"][1], st["top_z"]]}},
            "files": ["part.nc"]}
    with open(os.path.join(d, "part.camotics"), "w", encoding="utf-8") as fh:
        json.dump(proj, fh)

    out_stl = os.path.join(d, "part.stl")
    t0 = time.time()
    p = subprocess.run([CAMSIM, "--resolution", str(RES), "part.camotics", "part.stl"],
                       cwd=d, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600)
    secs = time.time() - t0
    if not os.path.exists(out_stl) or os.path.getsize(out_stl) < 200:
        return {"part": pid, "error": (p.stdout or p.stderr or "")[-200:]}

    v_cam, ntri = stl_volume(out_stl)
    v_dex, m = dexel_volume(plan, target, text, radius_by_op)
    rel = abs(v_cam - v_dex) / max(1.0, v_dex)
    return {"part": pid,
            "stock_volume_mm3": round((st["max"][0] - st["min"][0])
                                      * (st["max"][1] - st["min"][1])
                                      * (st["top_z"] - st["bottom_z"]), 1),
            "camotics_volume_mm3": round(v_cam, 1),
            "dexel_volume_mm3": round(v_dex, 1),
            "rel_diff": round(rel, 5),
            "triangles": ntri,
            "camotics_seconds": round(secs, 2),
            "dexel_max_gouge_mm": round(float(np.max(np.where(
                np.isfinite(target["top"]),
                np.maximum(0.0, target["top"] - m["cur"]), 0.0))), 3),
            "gcode_lines": len(text.splitlines())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parts", default="1,2,3,4,5,6,7,8,9,10")
    args = ap.parse_args()
    os.makedirs(WORK, exist_ok=True)
    parts = [int(x) for x in args.parts.split(",") if x.strip()]
    rows = []
    for p in parts:
        r = run_part(p)
        rows.append(r)
        if r.get("error"):
            print("件%-3s 失败：%s" % (p, r["error"][:100]))
        else:
            print("件%-3s CAMotics %9.1f ｜ dexel %9.1f ｜ 差 %.2f%% ｜ %d 三角形"
                  % (p, r["camotics_volume_mm3"], r["dexel_volume_mm3"],
                     100 * r["rel_diff"], r["triangles"]))
    ok = [r for r in rows if not r.get("error")]
    if ok:
        rel = [r["rel_diff"] for r in ok]
        summary = {"n": len(ok), "mean_rel_diff": round(float(np.mean(rel)), 5),
                   "max_rel_diff": round(float(np.max(rel)), 5),
                   "p95_rel_diff": round(float(np.percentile(rel, 95)), 5),
                   "note": "两个实现算法不同（表面网格切削 vs Z-dexel），"
                           "比的是被切后工件的体积"}
        with open(OUT, "w", encoding="utf-8") as fh:
            json.dump({"rule": "同一份 G-code，两个独立仿真器",
                       "summary": summary, "parts": rows}, fh, ensure_ascii=False, indent=1)
        print("\n%d 件｜体积相对差 均值 %.2f%%｜最大 %.2f%%｜P95 %.2f%%"
              % (len(ok), 100 * summary["mean_rel_diff"],
                 100 * summary["max_rel_diff"], 100 * summary["p95_rel_diff"]))
        print("→ %s" % os.path.relpath(OUT, ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
