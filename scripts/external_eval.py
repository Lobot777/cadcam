"""外部验证集评测：130 件 MFCAD 缩放件跑完整 M3 链（工序 → G-code → dexel → 双线判据）。

判据与锚点集**完全相同**（ADR-001 双线）：
    安全线  max_gouge_depth ≤ 0.5 mm
    精度线  mean_abs_deviation ≤ 0.5 mm

为什么可以沿用：外部件已按长边缩放到 80 mm（`scripts/external_scale.py`），
与 10 件锚点集同一量级，刀径 Ø6 / Ø1.5 与 0.5 mm 阈值都成立。

    PYTHONPATH=src python scripts/external_eval.py
    PYTHONPATH=src python scripts/external_eval.py --limit 20
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

GEOM = os.path.join(ROOT, "data", "external", "geom")
LABELS = os.path.join(ROOT, "data", "external", "labels")
OUT = os.path.join(ROOT, "reports", "m3_external_eval.json")
RES = 0.5
GOUGE_LIMIT = 0.5
MEAN_LIMIT = 0.5


def run_one(pid):
    g = json.load(open(os.path.join(GEOM, pid + ".json"), encoding="utf-8"))
    lab = json.load(open(os.path.join(LABELS, pid + ".json"), encoding="utf-8"))
    fam = [lab["faces"].get(str(f["index"]), {}).get("family", "face")
           for f in g["faces"]]
    fams = {x for x in fam if x != "face"}
    if not fams:
        return {"id": pid, "error": "无可映射特征"}
    inst = group(g, fam, families={x: x for x in fams})
    plan = process.plan(g, inst)
    if not plan["ops"]:
        return {"id": pid, "error": "工序为空"}
    target = dexel.rasterize(g, res=RES)
    if target["coverage"] < 0.5:
        return {"id": pid, "error": "高度图覆盖不足"}
    text, meta = heightmap.generate(plan, target, res=RES)
    radius_by_op = {o["label"]: o["tool_d_mm"] / 2.0 for o in meta["ops_detail"]}
    v = gcode_gen.validate(text)
    if not v["ok"]:
        return {"id": pid, "error": "白名单失败：%s" % v["errors"][:1]}
    m = dexel.simulate(v["ir"], target, plan["stock"]["top_z"],
                       tool_radius_mm=3.0, res=RES, tool_radius_by_op=radius_by_op)
    goal = target["top"]
    mask = np.isfinite(goal)
    gouge = np.where(mask, np.maximum(0.0, goal - m["cur"]), 0.0)
    max_g = float(gouge.max())
    mean_d = float(m["mean_deviation_mm"])
    return {"id": pid,
            "faces": len(g["faces"]),
            # 层数按**相对容差**统计：缩放后的件高度差可能只有 0.3mm，
            # 用固定 0.1mm 会把同一个面拆成几十层。
            "levels": int(len(np.unique(np.round(
                goal[mask] / max(0.2, 0.004 * (np.nanmax(goal[mask]) - np.nanmin(goal[mask]))))))),
            "ops": len(plan["ops"]),
            "lines": len(text.splitlines()),
            "max_gouge_depth_mm": round(max_g, 3),
            "mean_deviation_mm": round(mean_d, 3),
            "p99_deviation_mm": round(float(m["p99_deviation_mm"]), 3),
            "residual_volume_mm3": round(float(m["residual_volume_mm3"]), 1),
            "safety_pass": max_g <= GOUGE_LIMIT,
            "accuracy_pass": mean_d <= MEAN_LIMIT,
            "pass": max_g <= GOUGE_LIMIT and mean_d <= MEAN_LIMIT}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    ids = sorted(x[:-5] for x in os.listdir(GEOM) if x.endswith(".json"))
    if args.limit:
        ids = ids[:args.limit]
    rows, t0 = [], time.time()
    for i, pid in enumerate(ids, 1):
        try:
            r = run_one(pid)
        except Exception as exc:                        # noqa: BLE001
            r = {"id": pid, "error": "%s: %s" % (type(exc).__name__, str(exc)[:120])}
        rows.append(r)
        if i % 20 == 0:
            ok = sum(1 for x in rows if x.get("pass"))
            print("  ...%d/%d｜通过 %d｜%.0fs" % (i, len(ids), ok, time.time() - t0),
                  flush=True)
    ok = [r for r in rows if r.get("pass")]
    err = [r for r in rows if r.get("error")]
    good = [r for r in rows if "max_gouge_depth_mm" in r]
    summary = {
        "n": len(rows), "evaluated": len(good), "pass": len(ok),
        "pass_rate": round(len(ok) / max(1, len(good)), 3),
        "errors": len(err),
        "error_reasons": {k: sum(1 for r in err if r["error"].startswith(k))
                          for k in {r["error"].split("：")[0].split(":")[0] for r in err}},
        "safety_pass": sum(1 for r in good if r["safety_pass"]),
        "accuracy_pass": sum(1 for r in good if r["accuracy_pass"]),
        "max_gouge_worst": max((r["max_gouge_depth_mm"] for r in good), default=None),
        "mean_dev_worst": max((r["mean_deviation_mm"] for r in good), default=None),
        "seconds": round(time.time() - t0, 1),
    }
    by_level = {}
    for r in good:
        b = by_level.setdefault(str(r["levels"]), {"n": 0, "pass": 0,
                                                   "gouge": [], "mean": []})
        b["n"] += 1
        b["pass"] += int(r["pass"])
        b["gouge"].append(r["max_gouge_depth_mm"])
        b["mean"].append(r["mean_deviation_mm"])
    for b in by_level.values():
        b["gouge_max"] = round(max(b["gouge"]), 3)
        b["mean_avg"] = round(sum(b["mean"]) / len(b["mean"]), 3)
        b.pop("gouge"); b.pop("mean")
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"criterion": "ADR-001 双线：max_gouge ≤0.5 且 mean ≤0.5",
                   "dataset": "MFCAD（130 件，长边缩放到 80mm）",
                   "summary": summary, "by_level": dict(sorted(by_level.items(), key=lambda kv: int(kv[0]))),
                   "parts": rows}, fh, ensure_ascii=False, indent=1)
    print("\n评测 %d 件｜通过 %d（%.1f%%）｜出错 %d"
          % (summary["evaluated"], summary["pass"], 100 * summary["pass_rate"],
             summary["errors"]))
    print("安全线 %d｜精度线 %d｜最坏过切 %s mm｜最坏均偏差 %s mm"
          % (summary["safety_pass"], summary["accuracy_pass"],
             summary["max_gouge_worst"], summary["mean_dev_worst"]))
    print("按层数：", {k: "%d/%d" % (v["pass"], v["n"]) for k, v in by_level.items()})
    print("→ %s" % os.path.relpath(OUT, ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
