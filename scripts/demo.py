"""M6 演示：一件零件从几何摘要一路走到「可交付的刀路 + 判定」，全程约 20 秒。

不依赖 FreeCAD：几何用 `reports/annotation/geom/*.json` 的三角网格缓存，
所以第一次拿到仓库的人不用装内核就能跑通。

    PYTHONPATH=src python scripts/demo.py            # 默认 10 号件（10 个高度层）
    PYTHONPATH=src python scripts/demo.py --part 2

产物：reports/demo.json
"""
from __future__ import annotations

import argparse
import json
import os

from cadcam.modalities.cam import gcode_gen, process
from cadcam.modalities.mfr.instances import group
from cadcam.orchestrator import EpisodicMemory, run_task
from cadcam.verify import dexel, dfm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEOM = os.path.join(ROOT, "reports", "annotation", "geom")
LABELS = os.path.join(ROOT, "data", "zenodo_rptu", "labels")


def _rule(title):
    print("\n" + "─" * 66)
    print(title)
    print("─" * 66)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", default="10")
    args = ap.parse_args()

    with open(os.path.join(GEOM, "%s.json" % args.part), encoding="utf-8") as fh:
        geom = json.load(fh)
    with open(os.path.join(LABELS, "%s.json" % args.part), encoding="utf-8") as fh:
        labels = json.load(fh)

    _rule("① 几何摘要（M0/M2）")
    bb = geom["bbox"]
    size = [bb["max"][k] - bb["min"][k] for k in range(3)]
    print("面 %d ｜ 边 %d ｜ 三角 %d ｜ 包围盒 %.1f × %.1f × %.1f mm ｜ 体积 %.0f mm³"
          % (len(geom["faces"]), len(geom.get("edges", [])),
             sum(len(f.get("triangles", [])) for f in geom["faces"]),
             size[0], size[1], size[2], geom.get("volume_mm3", 0)))

    _rule("② 面级特征（M2：GNN 输出的 family 标签）")
    fam = [labels["faces"][str(f["index"])]["family"] for f in geom["faces"]]
    inst = group(geom, fam, families={x: x for x in set(fam)})
    for i in inst:
        print("  %-18s 面 %-2d  bbox z∈[%.1f, %.1f]"
              % (i.get("family"), len(i.get("faces", [])),
                 i["bbox"]["min"][2], i["bbox"]["max"][2]))

    _rule("③ 工艺计划（M3：工艺库钳制 + 工序 DAG）")
    plan = process.plan(geom, inst)
    print("毛坯顶面 z = %.1f（成品 %.1f + 余量 %.1f）｜工序 %d 道"
          % (plan["stock"]["top_z"], plan["stock"]["finished_top_z"],
             plan["stock"]["allowance_mm"], len(plan["ops"])))
    for op in plan["ops"]:
        print("  %-4s %-11s Ø%-4.1f  z %.1f → %.1f  %s"
              % (op["id"], op["op"], op["speeds"]["tool_d_mm"],
                 op["z_top"], op["z_bottom"],
                 "越界参数 %d" % len(plan["violations"]) if plan["violations"] else ""))

    _rule("④ 后置处理 + 白名单校验（约束 G-code）")
    text = gcode_gen.generate(plan)
    v = gcode_gen.validate(text, stock=plan["stock"])
    print("行数 %d｜解析段 %d｜白名单 %s"
          % (len(text.splitlines()), v["ir"]["stats"]["segments"],
             "通过" if v["ok"] else "报错 %d 条" % len(v["errors"])))

    _rule("⑤ 材料去除仿真（Z-dexel 0.5mm）+ DFM")
    target = dexel.rasterize(geom, res=0.5)
    r_by_op = {op["label"]: op["speeds"]["tool_d_mm"] / 2.0 for op in plan["ops"]}
    m = dexel.simulate(v["ir"], target, plan["stock"]["top_z"], tool_radius_mm=3.0,
                       res=0.5, tool_radius_by_op=r_by_op,
                       op_faces={op["label"]: op.get("faces", []) for op in plan["ops"]})
    print("过切 %8.1f mm³ ｜残留 %8.1f mm³ ｜平均偏差 %.3f mm ｜P99 %.1f mm"
          % (m["gouge_volume_mm3"], m["residual_volume_mm3"],
             m["mean_deviation_mm"], m["p99_deviation_mm"]))
    for e in dexel.errors(m, op_ids={op["label"]: op["id"] for op in plan["ops"]}):
        print("  错误 %-11s %s" % (e["code"], e.get("cause", "")))
    for e in dfm.check(plan)[:3]:
        print("  DFM %-14s %s" % (e["code"], e.get("cause", "")))

    _rule("⑥ 编排层（M4：状态机 + 黑板 + 有界修复）")
    task = {"part": args.part, "family": "demo", "geom": geom, "labels": labels,
            "allowance": 1.0}
    r = run_task(task, EpisodicMemory(), max_repair=3)
    print("阶段流：%s" % " → ".join(r["phases"]))
    print("修复次数 %d ｜最终策略 %s ｜结论 %s"
          % (r["rounds"], r["strategies"], r["status"]))
    if r["first_metrics"]["max_gouge_depth_mm"] > 0.5:
        print("  首轮（包围盒计划）过切 %.1f mm，修复后 %.3f mm"
              % (r["first_metrics"]["max_gouge_depth_mm"],
                 r["metrics"]["max_gouge_depth_mm"]))
    print("\n判定：最大过切 %.3f mm（阈值 0.5）｜平均偏差 %.3f mm（阈值 0.5）→ %s"
          % (r["metrics"]["max_gouge_depth_mm"],
             r["metrics"]["mean_deviation_mm"],
             "通过" if r["pass"] else "不通过（已按块上报）"))

    out = os.path.join(ROOT, "reports", "demo.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"part": args.part, "faces": len(geom["faces"]),
                   "instances": len(inst), "ops": len(plan["ops"]),
                   "gcode_lines": len(text.splitlines()),
                   "metrics_bbox": {k: m[k] for k in
                                    ("max_deviation_mm", "mean_deviation_mm",
                                     "gouge_volume_mm3", "residual_volume_mm3")},
                   "orchestration": {k: r[k] for k in
                                     ("status", "rounds", "strategies",
                                      "phases", "pass")},
                   "final_metrics": r["metrics"]}, fh, ensure_ascii=False, indent=1)
    print("\n→ reports/demo.json")


if __name__ == "__main__":
    main()
