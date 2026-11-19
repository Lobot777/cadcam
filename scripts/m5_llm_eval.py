"""M5 实验：把 LLM 接到两个决策点，看它到底值不值。

四组（每组 20 个任务 = 10 件 × 2 种毛坯余量，冷记忆，避免记忆混淆）：

  det                 确定性策略（M4 arm B 的复现对照）
  llm_plan            LLM 选规划策略 + 确定性修复
  llm_repair_struct   LLM 读**结构化错误对象**选修复动作
  llm_repair_text     LLM 读**同一信息的散文版**选修复动作（C3 的对照）

    PYTHONPATH=src python scripts/m5_llm_eval.py
    PYTHONPATH=src python scripts/m5_llm_eval.py --arms det,llm_plan
    PYTHONPATH=src python scripts/m5_llm_eval.py --model qwen3:8b --base-url ...
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from cadcam.agents.llm import LLMClient, LLMError
from cadcam.agents.roles_llm import LLMPlanner, LLMRepairer
from cadcam.orchestrator import EpisodicMemory, run_task
from cadcam.orchestrator.roles import Planner, Repairer
from cadcam.verify import dexel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEOM = os.path.join(ROOT, "reports", "annotation", "geom")
LABELS = os.path.join(ROOT, "data", "zenodo_rptu", "labels")
OUT = os.path.join(ROOT, "reports", "m5_llm_eval.json")
ALLOWANCES = (1.0, 2.5)
ORACLE_MIN_LEVELS = 3        # ≥3 层用 bbox 必过切（M4 arm A 实测），故 oracle 取 heightmap


def load_tasks():
    import numpy as np
    tasks = []
    for n in range(1, 11):
        with open(os.path.join(GEOM, "%d.json" % n), encoding="utf-8") as fh:
            geom = json.load(fh)
        with open(os.path.join(LABELS, "%d.json" % n), encoding="utf-8") as fh:
            labels = json.load(fh)
        top = dexel.rasterize(geom, res=0.5)["top"]
        finite = np.isfinite(top)
        levels = int(np.unique(np.round(top[finite], 2)).size)
        for a in ALLOWANCES:
            tasks.append({"part": str(n), "family": "levels_%d" % levels,
                          "geom": geom, "labels": labels,
                          "allowance": a, "levels": levels})
    return tasks


def oracle(levels):
    return "heightmap" if levels >= ORACLE_MIN_LEVELS else "bbox"


def run_arm(name, tasks, client, planner_factory, repairer_factory):
    rows, records = [], []
    t0 = time.time()
    for t in tasks:
        rec = []
        planner = planner_factory(rec) if planner_factory else Planner()
        repairer = repairer_factory(rec) if repairer_factory else Repairer()
        try:
            r = run_task(t, EpisodicMemory(), max_repair=3,
                         planner=planner, repairer=repairer)
            status, err = r["status"], None
        except LLMError as exc:                 # 计划层彻底失败：记为任务失败，不崩
            r, status, err = None, "LLM_ERROR", str(exc)[:160]
        rows.append({
            "part": t["part"], "allowance": t["allowance"], "levels": t["levels"],
            "status": status, "pass": bool(r and r["pass"]),
            "rounds": (r or {}).get("rounds", 0),
            "strategy": (r or {}).get("strategies"),
            "first_max_gouge": ((r or {}).get("first_metrics") or {}).get("max_gouge_depth_mm"),
            "final_max_gouge": ((r or {}).get("metrics") or {}).get("max_gouge_depth_mm"),
            "final_mean_dev": ((r or {}).get("metrics") or {}).get("mean_deviation_mm"),
            "oracle": oracle(t["levels"]),
            "agrees_oracle": ((r or {}).get("strategies") == oracle(t["levels"])),
            "error": err,
        })
        records.extend(rec)

    n = len(rows)
    calls = [x for x in records if "prompt_tokens" in x]
    fallbacks = [x for x in records if x.get("fallback")]
    picked = [x["decision"].get("strategy") for x in calls if x.get("role") == "planner"]
    summary = {
        "arm": name, "n": n,
        "pass": sum(r["pass"] for r in rows),
        "pass_rate": round(sum(r["pass"] for r in rows) / n, 3),
        "mean_rounds": round(sum(r["rounds"] for r in rows) / n, 3),
        "escalated": sum(r["status"] == "ESCALATE" for r in rows),
        "llm_calls": len(calls),
        "llm_fallbacks": len(fallbacks),
        "fallback_rate": round(len(fallbacks) / max(1, len(calls)), 3),
        "prompt_tokens": sum(x.get("prompt_tokens", 0) for x in calls),
        "completion_tokens": sum(x.get("completion_tokens", 0) for x in calls),
        "mean_llm_seconds": round(sum(x.get("seconds", 0.0) for x in calls)
                                  / max(1, len(calls)), 2),
        "wall_seconds": round(time.time() - t0, 1),
        "oracle_agreement": round(sum(r["agrees_oracle"] for r in rows) / n, 3),
        "rescued_by_repair": sum(1 for r in rows
                                 if r["pass"] and (r["first_max_gouge"] or 0) > 0.5),
        "strategy_picks": {s: picked.count(s) for s in set(picked)} if picked else {},
    }
    return summary, rows, records


def program_cost(tasks):
    """两条策略的程序长度与估算机床时间（只生成文本，不做仿真）。"""
    from cadcam.modalities.cam import gcode_gen, heightmap, process
    from cadcam.modalities.mfr.instances import group
    rows = []
    for t in tasks:
        fam = [t["labels"]["faces"][str(f["index"])]["family"] for f in t["geom"]["faces"]]
        inst = group(t["geom"], fam, families={x: x for x in set(fam)})
        plan = process.plan(t["geom"], inst, allowance=t["allowance"])
        bbox_lines = len(gcode_gen.generate(plan).splitlines())
        target = dexel.rasterize(t["geom"], res=0.5)
        hm_lines = len(heightmap.generate(plan, target, res=0.5)[0].splitlines())
        rows.append({"part": t["part"], "allowance": t["allowance"],
                     "bbox_lines": bbox_lines, "heightmap_lines": hm_lines,
                     "ratio": round(hm_lines / max(1, bbox_lines), 2)})
    tot_b = sum(r["bbox_lines"] for r in rows)
    tot_h = sum(r["heightmap_lines"] for r in rows)
    return {"rows": rows, "total_bbox_lines": tot_b,
            "total_heightmap_lines": tot_h,
            "mean_ratio": round(sum(r["ratio"] for r in rows) / len(rows), 2),
            "note": "机床时间按每 100 行约 30 秒估算（提示词里给模型的口径）"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3:8b")
    ap.add_argument("--base-url", default="http://127.0.0.1:11434/v1")
    ap.add_argument("--arms",
                    default="det,llm_plan,llm_plan_cost,llm_repair_struct,"
                            "llm_repair_text,llm_repair_vague")
    args = ap.parse_args()

    client = LLMClient(base_url=args.base_url, model=args.model)
    ok, models = client.available()
    print("LLM 端点 %s｜模型 %s" % (args.base_url, args.model))
    if ok:
        print("  可用模型：%s" % ", ".join(str(m) for m in models[:8]))
    else:
        print("  不可用：%s" % models[0])

    tasks = load_tasks()
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    need_llm = any(a.startswith("llm") for a in arms)
    if need_llm and not ok:
        print("需要 LLM 的组被跳过。先启动后端（ollama serve）再跑。")
        arms = [a for a in arms if not a.startswith("llm")]
    if not arms:
        return 2

    table, detail, recs = [], {}, {}
    for arm in arms:
        print("\n→ %s …" % arm, flush=True)
        if arm == "det":
            s, rows, rec = run_arm(arm, tasks, client, None, None)
        elif arm == "llm_plan":
            s, rows, rec = run_arm(arm, tasks, client,
                                   lambda r: LLMPlanner(client, r), None)
        elif arm == "llm_repair_struct":
            s, rows, rec = run_arm(arm, tasks, client, None,
                                   lambda r: LLMRepairer(client, "structured", r))
        elif arm == "llm_plan_cost":
            s, rows, rec = run_arm(arm, tasks, client,
                                   lambda r: LLMPlanner(client, r, cost_pressure=True),
                                   None)
        elif arm == "llm_repair_vague":
            s, rows, rec = run_arm(arm, tasks, client, None,
                                   lambda r: LLMRepairer(client, "vague", r))
        elif arm == "llm_repair_text":
            s, rows, rec = run_arm(arm, tasks, client, None,
                                   lambda r: LLMRepairer(client, "text", r))
        else:
            print("  未知组 %s，跳过" % arm)
            continue
        table.append(s)
        detail[arm] = rows
        recs[arm] = rec
        print("  通过 %d/%d (%.3f)｜轮次 %.2f｜调用 %d｜回落 %d｜%s"
              % (s["pass"], s["n"], s["pass_rate"], s["mean_rounds"],
                 s["llm_calls"], s["llm_fallbacks"], s["strategy_picks"] or ""))

    cost = program_cost(tasks)
    report = {"model": client.model, "base_url": client.base_url,
              "api": client.api,
              "program_cost": cost,
              "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
              "task_set": "10 件 × 2 种毛坯余量 = 20 任务，冷记忆",
              "criterion": "ADR-001 双线（最大过切 ≤0.5mm 且 平均绝对偏差 ≤0.5mm）",
              "oracle_rule": "高度层 ≥3 用 heightmap，≤2 用 bbox（依据 M4 arm A 实测）",
              "arms": table, "rows": detail,
              "llm_client_meter": client.meter.summary()}
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)

    print("\n%-20s %-8s %-8s %-8s %-8s %-8s" % ("组", "通过", "通过率", "均轮次", "调用", "回落"))
    for s in table:
        print("%-20s %d/%-6d %-8.3f %-8.2f %-8d %d"
              % (s["arm"], s["pass"], s["n"], s["pass_rate"],
                 s["mean_rounds"], s["llm_calls"], s["llm_fallbacks"]))
    print("\n程序长度：bbox 合计 %d 行｜heightmap 合计 %d 行｜平均 %.2f×"
          % (cost["total_bbox_lines"], cost["total_heightmap_lines"],
             cost["mean_ratio"]))
    print("→ %s" % os.path.relpath(OUT, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
