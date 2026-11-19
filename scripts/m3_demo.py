"""M3.1 端到端验收：10 件锚点集，特征 → 工序 → G-code → 仿真 → 结构化错误。

用法：
    PYTHONPATH=src python scripts/m3_demo.py
    PYTHONPATH=src python scripts/m3_demo.py --parts 1,2 --res 0.5

产物：
    reports/m3_gcode/<n>.nc          生成的受限 G-code
    reports/m3_acceptance.json       逐件指标 + 汇总
"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from cadcam.modalities.cam import gcode_gen, process          # noqa: E402
from cadcam.modalities.mfr.instances import group            # noqa: E402
from cadcam.verify import dexel, dfm                          # noqa: E402

GEOM = os.path.join(ROOT, "reports", "annotation", "geom")
LABELS = os.path.join(ROOT, "data", "zenodo_rptu", "labels")
GCODE_DIR = os.path.join(ROOT, "reports", "m3_gcode")
OUT = os.path.join(ROOT, "reports", "m3_acceptance.json")


def instances_for(geom, lab):
    labels = [lab["faces"][str(f["index"])]["family"] for f in geom["faces"]]
    return group(geom, labels, families={x: x for x in set(labels)})


def simulate_text(text, target, plan, res, tool_r, radius_by_op=None,
                  op_faces=None, op_ids=None):
    v = gcode_gen.validate(text, stock=plan["stock"])
    m = dexel.simulate(v["ir"], target, plan["stock"]["top_z"],
                       tool_radius_mm=tool_r, res=res,
                       tool_radius_by_op=radius_by_op, op_faces=op_faces)
    return v, m, dexel.errors(m, op_ids=op_ids)


def run_part(part, res):
    with open(os.path.join(GEOM, "%s.json" % part), encoding="utf-8") as fh:
        geom = json.load(fh)
    with open(os.path.join(LABELS, "%s.json" % part), encoding="utf-8") as fh:
        lab = json.load(fh)

    inst = instances_for(geom, lab)
    plan = process.plan(geom, inst)
    text = gcode_gen.generate(plan)
    with open(os.path.join(GCODE_DIR, "%s.nc" % part), "w", encoding="utf-8") as fh:
        fh.write(text)

    v = gcode_gen.validate(text, stock=plan["stock"])
    target = dexel.rasterize(geom, res=res)
    tool_r = plan["ops"][0]["speeds"]["tool_d_mm"] / 2.0 if plan["ops"] else 3.0
    radius_by_op = {op["label"]: op["speeds"]["tool_d_mm"] / 2.0 for op in plan["ops"]}
    op_faces = {op["label"]: op.get("faces", []) for op in plan["ops"]}
    op_ids = {op["label"]: op["id"] for op in plan["ops"]}
    _, metrics, sim_errors = simulate_text(
        text, target, plan, res, tool_r, radius_by_op, op_faces, op_ids)
    dfm_errors = dfm.check(plan)

    # 注入已知缺陷，证明验证器不是空跑
    defect = {}
    for kind in ("gouge", "residual"):
        bad = gcode_gen.inject_defect(text, kind, 1.0)
        bv, bm, berr = simulate_text(
            bad, target, plan, res, tool_r, radius_by_op, op_faces, op_ids)
        defect[kind] = {
            "detected": any(e["code"] == ("E_GOUGE" if kind == "gouge" else "E_RESIDUAL")
                            for e in berr),
            "volume_mm3": (bm["gouge_volume_mm3"] if kind == "gouge"
                           else bm["residual_volume_mm3"]),
            "delta_vs_clean_mm3": round(
                (bm["gouge_volume_mm3"] - metrics["gouge_volume_mm3"]) if kind == "gouge"
                else (bm["residual_volume_mm3"] - metrics["residual_volume_mm3"]), 2),
            "syntax_ok": bv["ok"],
        }
    bad_col = gcode_gen.inject_defect(text, "collision",
                                      stock_top=plan["stock"]["top_z"])
    cv = gcode_gen.validate(bad_col, stock=plan["stock"])
    defect["collision"] = {
        "detected": any(e["code"] == "E_COLLISION" for e in cv["errors"]),
        "clean_collisions": sum(e["code"] == "E_COLLISION" for e in v["errors"]),
    }

    return {
        "part": part,
        "instances": len(inst),
        "ops": len(plan["ops"]),
        "skipped_instances": len(plan["skipped"]),
        "gcode_lines": len(text.splitlines()),
        "whitelist_ok": v["ok"],
        "syntax_errors": v["errors"],
        "parse_segments": v["ir"]["stats"]["segments"],
        "parse_unknown_codes": v["ir"]["unknown_codes"],
        "tool_ids": sorted({op["tool"] for op in plan["ops"]}),
        "dfm_errors": dfm_errors,
        "simulation": {k: val for k, val in metrics.items()
                       if k not in ("cur", "op_attribution")},
        "simulation_errors": sim_errors,
        "defect_injection": defect,
        "target_coverage": round(target["coverage"], 4),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parts", default=",".join(str(i) for i in range(1, 11)))
    ap.add_argument("--res", type=float, default=0.5)
    args = ap.parse_args()
    os.makedirs(GCODE_DIR, exist_ok=True)

    rows = []
    for part in args.parts.split(","):
        row = run_part(part.strip(), args.res)
        rows.append(row)
        print("件 %-3s 实例 %2d → 工序 %2d | G-code %3d 行 | 白名单 %s | 段 %3d | "
              "过切 %8.1f 残留 %9.1f mm³ | DFM %d | 缺陷检出 %s/%s/%s"
              % (row["part"], row["instances"], row["ops"], row["gcode_lines"],
                 "OK" if row["whitelist_ok"] else "FAIL", row["parse_segments"],
                 row["simulation"]["gouge_volume_mm3"],
                 row["simulation"]["residual_volume_mm3"],
                 len(row["dfm_errors"]),
                 "Y" if row["defect_injection"]["gouge"]["detected"] else "N",
                 "Y" if row["defect_injection"]["residual"]["detected"] else "N",
                 "Y" if row["defect_injection"]["collision"]["detected"] else "N"))

    summary = {
        "version": "M3.2", "res_mm": args.res,
        "parts": len(rows),
        "all_whitelist_ok": all(r["whitelist_ok"] for r in rows),
        "total_ops": sum(r["ops"] for r in rows),
        "total_gcode_lines": sum(r["gcode_lines"] for r in rows),
        "total_parse_segments": sum(r["parse_segments"] for r in rows),
        "gouge_defect_detected": sum(r["defect_injection"]["gouge"]["detected"] for r in rows),
        "residual_defect_detected": sum(r["defect_injection"]["residual"]["detected"] for r in rows),
        "collision_defect_detected": sum(r["defect_injection"]["collision"]["detected"] for r in rows),
        "clean_collisions": sum(r["defect_injection"]["collision"]["clean_collisions"] for r in rows),
        "errors_with_op_id": sum(any("op_id" in e for e in r["simulation_errors"])
                                 for r in rows),
        "total_dfm_errors": sum(len(r["dfm_errors"]) for r in rows),
        "target_coverage_min": min(r["target_coverage"] for r in rows),
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"summary": summary, "parts": rows}, fh, ensure_ascii=False, indent=1)
    print("\n汇总:", json.dumps(summary, ensure_ascii=False))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
