"""M4 验收：三组对照跑 20 个 T3 任务。

任务 = 10 件几何 × 2 种毛坯余量（1.0 / 2.5 mm）。
注：原计划写的是「T3 子集 20 件」，数据集现只有 10 件带几何与面标注，
因此改为 10 件 × 2 工艺变体 = 20 个任务，换算口径见 docs/M4-PLAN.md §2。

三组：
  A 单轮不修（max_repair=0）——「工业常规做法」的代理
  B 有界修复 + 冷记忆（每任务新建记忆）
  C 有界修复 + 热记忆（20 个任务顺序跑，记忆累积）

    PYTHONPATH=src python scripts/m4_run.py
"""
from __future__ import annotations

import json
import os
import time

from cadcam.orchestrator import EpisodicMemory, run_task
from cadcam.verify import dexel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEOM = os.path.join(ROOT, "reports", "annotation", "geom")
LABELS = os.path.join(ROOT, "data", "zenodo_rptu", "labels")
OUT = os.path.join(ROOT, "reports", "m4_run.json")
MEM = os.path.join(ROOT, "reports", "m4_memory.json")

ALLOWANCES = (1.0, 2.5)


def family_of(levels):
    if levels <= 2:
        return "levels_le_2"
    if levels <= 4:
        return "levels_3_4"
    return "levels_ge_5"


def load_parts():
    parts = []
    for n in range(1, 11):
        with open(os.path.join(GEOM, "%d.json" % n), encoding="utf-8") as fh:
            geom = json.load(fh)
        with open(os.path.join(LABELS, "%d.json" % n), encoding="utf-8") as fh:
            labels = json.load(fh)
        target = dexel.rasterize(geom, res=0.5)
        top = target["top"]
        import numpy as np
        levels = int(np.unique(np.round(top[np.isfinite(top)], 2)).size)
        parts.append({"part": str(n), "geom": geom, "labels": labels,
                      "levels": levels, "family": family_of(levels)})
    return parts


def tasks(parts):
    out = []
    for p in parts:
        for a in ALLOWANCES:
            out.append({"part": p["part"], "family": p["family"],
                        "geom": p["geom"], "labels": p["labels"],
                        "allowance": a, "levels": p["levels"]})
    return out


def arm(name, tsks, max_repair, shared_memory):
    mem = EpisodicMemory()
    rows = []
    t0 = time.time()
    for t in tsks:
        use = mem if shared_memory else EpisodicMemory()
        r = run_task(t, use, max_repair=max_repair)
        rows.append({"part": r["task"]["part"], "allowance": r["task"]["allowance"],
                     "family": r["task"]["family"], "pass": r["pass"],
                     "rounds": r["rounds"], "status": r["status"],
                     "strategy": r["strategies"],
                     "hint": r["task"]["strategy_hint"],
                     "max_gouge": r["metrics"]["max_gouge_depth_mm"],
                     "mean_dev": r["metrics"]["mean_deviation_mm"],
                     "first_max_gouge": r["first_metrics"]["max_gouge_depth_mm"],
                     "signature": r["signature"]})
    n = len(rows)
    summary = {
        "arm": name,
        "n": n,
        "pass": sum(r["pass"] for r in rows),
        "pass_rate": round(sum(r["pass"] for r in rows) / n, 3),
        "mean_rounds": round(sum(r["rounds"] for r in rows) / n, 3),
        "max_rounds": max(r["rounds"] for r in rows),
        "escalated": sum(r["status"] == "ESCALATE" for r in rows),
        "first_pass": sum(r["first_max_gouge"] <= 0.5 for r in rows),
        "seconds": round(time.time() - t0, 1),
        "memory": mem.summary(),
        "by_family": _by_family(rows),
    }
    return summary, rows, mem


def _by_family(rows):
    out = {}
    for r in rows:
        b = out.setdefault(r["family"], {"n": 0, "pass": 0, "rounds": 0})
        b["n"] += 1
        b["pass"] += int(r["pass"])
        b["rounds"] += r["rounds"]
    for b in out.values():
        b["pass_rate"] = round(b["pass"] / b["n"], 3)
        b["mean_rounds"] = round(b["rounds"] / b["n"], 3)
    return out


def main():
    parts = load_parts()
    tsks = tasks(parts)
    print("任务 %d 个（%d 件 × %d 种余量）｜零件族：%s"
          % (len(tsks), len(parts), len(ALLOWANCES),
             ", ".join(sorted({p["family"] for p in parts}))))

    a, ra, _ = arm("A_no_repair", tsks, 0, False)
    b, rb, _ = arm("B_repair_cold", tsks, 3, False)
    c, rc, cmem = arm("C_repair_warm", tsks, 3, True)

    print("%-16s %-8s %-10s %-8s %-10s %s"
          % ("组", "通过", "通过率", "均轮次", "首轮能过", "耗时"))
    for s in (a, b, c):
        print("%-16s %d/%-5d %-10.3f %-8.2f %-10d %.1fs"
              % (s["arm"], s["pass"], s["n"], s["pass_rate"],
                 s["mean_rounds"], s["first_pass"], s["seconds"]))

    report = {"task_definition": "10 件 × 2 种毛坯余量 = 20 个任务",
              "acceptance": "ADR-001 双线（最大过切 ≤0.5mm 且 平均绝对偏差 ≤0.5mm）",
              "arms": [a, b, c],
              "rows": {"A_no_repair": ra, "B_repair_cold": rb, "C_repair_warm": rc},
              "family_note": "零件族由目标高度层数派生（≤2 / 3–4 / ≥5 层）"}
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
    print("→", os.path.relpath(OUT, ROOT))

    cmem.save(MEM)
    print("C 组记忆：%s → %s" % (json.dumps(c["memory"], ensure_ascii=False),
                                os.path.relpath(MEM, ROOT)))


if __name__ == "__main__":
    main()
