"""B10：NL 工单 → 契约调用 → **有界多轮修复**（结构化反馈 vs 模糊反馈）。

B9 只跑了单轮：20 条常规工单 20/20、特征 IoU 0.985。这一轮把任务调难（3–5 个特征、
方位/对称/壁厚表述），让首轮真的会错，然后看两件事：

  1. 失败能不能**改对**（agent 的"会改"）；
  2. **结构化反馈**（逐特征"该处仍有余料" + 切除体积差）比"结果不对，请重做"强多少。

这正是 M5 里没测出差异的那个问题（当时修复只有两个显然选项，三组行为完全一样）。

    PYTHONPATH=src python scripts/m5_repair_eval.py --rounds 3
    PYTHONPATH=src python scripts/m5_repair_eval.py --arm vague
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

from cadcam.adapters import freecad
from cadcam.agents.llm import LLMClient, LLMError

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from m5_nl_eval import (TOOL_DOC, _iou_paths, contract_messages,  # noqa: E402
                        execute)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "build", "repair_eval")
OUT = os.path.join(ROOT, "reports", "m5_repair_eval.json")
SUCCESS_IOU = 0.90

SYS = ("你是 CAD 编程助手。只能通过给定的工具接口建模，不允许输出 Python 脚本。"
       "只输出一个 JSON 对象。 /no_think")

# 10 条更难的工单：(id, 毛坯, 工单, 期望调用)
TASKS = [
    ("R1", [80, 80, 50], "80×80×50 的料，把左上角（x 小、y 大那一侧）切掉一块 30×30、深 12。",
     [("box", 80, 80, 50), ("pocket", 15, 65, 30, 30, 12)]),
    ("R2", [80, 80, 50], "80×80×50，铣型腔 40×40，深度 8，中心坐标 x=20, y=20。",
     [("box", 80, 80, 50), ("pocket", 20, 20, 40, 40, 8)]),
    ("R3", [100, 80, 50], "100×80×50：两个 30×20 深 8 的腔，关于 x=50 对称，y 都在 40，"
                          "中心分别距中线 25。",
     [("box", 100, 80, 50), ("pocket", 25, 40, 30, 20, 8), ("pocket", 75, 40, 30, 20, 8)]),
    ("R4", [100, 100, 60], "100×100×60，中央铣 50×50 深 20 的大腔，四角各钻一个 Ø8 通孔，"
                           "孔心距两边都是 10。",
     [("box", 100, 100, 60), ("pocket", 50, 50, 50, 50, 20),
      ("hole", 10, 10, 8), ("hole", 90, 10, 8), ("hole", 10, 90, 8), ("hole", 90, 90, 8)]),
    ("R5", [90, 90, 45], "90×90×45，型腔 40×40 居中、深 10；另在 (20,20) 和 (70,70) 各钻 Ø6 盲孔，深 12。",
     [("box", 90, 90, 45), ("pocket", 45, 45, 40, 40, 10),
      ("hole", 20, 20, 6, 12), ("hole", 70, 70, 6, 12)]),
    ("R6", [120, 80, 50], "120×80×50，沿 x 方向开两道 20×60 深 6 的槽，中心分别在 x=35 和 x=85，y 居中。",
     [("box", 120, 80, 50), ("pocket", 35, 40, 20, 60, 6), ("pocket", 85, 40, 20, 60, 6)]),
    ("R7", [100, 100, 50], "100×100×50，中间 60×60 深 15 的腔；四角各钻 Ø10 的孔（从顶面算深 25），"
                           "孔心距腔边 10。",
     [("box", 100, 100, 50), ("pocket", 50, 50, 60, 60, 15),
      ("hole", 30, 30, 10, 25), ("hole", 70, 30, 10, 25),
      ("hole", 30, 70, 10, 25), ("hole", 70, 70, 10, 25)]),
    ("R8", [80, 60, 40], "80×60×40，四边各留 10 的壁厚，中间全部铣掉深 15。",
     [("box", 80, 60, 40), ("pocket", 40, 30, 60, 40, 15)]),
    ("R9", [100, 80, 50], "100×80×50，先在中央开 40×40 深 5 的浅腔，再在腔底中央钻 Ø12 通孔。",
     [("box", 100, 80, 50), ("pocket", 50, 40, 40, 40, 5), ("hole", 50, 40, 12)]),
    ("R10", [90, 70, 55], "90×70×55，深 1.2 cm、60×40 的腔居中；四角 M8 通孔（Ø8），距两边各 8。",
     [("box", 90, 70, 55), ("pocket", 45, 35, 60, 40, 12),
      ("hole", 8, 8, 8), ("hole", 82, 8, 8), ("hole", 8, 62, 8), ("hole", 82, 62, 8)]),
]

PROBE_PY = '''import os, json
import FreeCAD as App
import Part
shape = Part.read(os.environ["SHAPE"])
pts = json.loads(os.environ["POINTS"])
out = []
for p in pts:
    inside = shape.isInside(App.Vector(p["x"], p["y"], p["z"]), 1e-6, True)
    out.append(bool(inside))
print("PROBEJSON" + json.dumps(out))
'''


def gold_calls(raw):
    seq = []
    for c in raw:
        if c[0] == "box":
            seq.append({"tool": "cad.create_box",
                        "args": {"length": float(c[1]), "width": float(c[2]),
                                 "height": float(c[3])}})
        elif c[0] == "pocket":
            seq.append({"tool": "cad.pocket",
                        "args": {"center_x": float(c[1]), "center_y": float(c[2]),
                                 "width": float(c[3]), "height": float(c[4]),
                                 "depth": float(c[5])}})
        else:
            args = {"x": float(c[1]), "y": float(c[2]), "diameter": float(c[3])}
            if len(c) > 4:
                args["depth"] = float(c[4])
            else:
                args["through"] = True
            seq.append({"tool": "cad.hole", "args": args})
    return seq


def _norm(c):
    """把 {tool,args} 形式归一成 ("pocket", ...) 元组，方便两种调用式共用。"""
    if not isinstance(c, dict):
        return c
    a = c.get("args") or {}
    tool = str(c.get("tool", "")).split(".")[-1]
    if tool == "pocket":
        return ("pocket", a.get("center_x"), a.get("center_y"), a.get("width"),
                a.get("height"), a.get("depth"))
    if tool == "create_box":
        return ("box", a.get("length"), a.get("width"), a.get("height"))
    if tool == "hole":
        if a.get("through"):
            return ("hole", a.get("x"), a.get("y"), a.get("diameter"))
        return ("hole", a.get("x"), a.get("y"), a.get("diameter"), a.get("depth"))
    return c


def probes(raw, size):
    raw = [_norm(c) for c in raw]
    """每个特征取探测点：腔取中心、孔取中心；返回 [(标签, 点)]。"""
    H = size[2]
    out = []
    for c in raw:
        if not isinstance(c, tuple) or not c:
            continue
        if c[0] == "pocket":
            out.append(("型腔 #%d（中心 (%.0f, %.0f)，%.0f×%.0f，深 %.0f）"
                        % (len(out) + 1, c[1], c[2], c[3], c[4], c[5]),
                        {"x": c[1], "y": c[2], "z": H - c[5] / 2.0}))
        elif c[0] == "hole":
            depth = min(c[4] if len(c) > 4 else H, H)
            # 取孔底上方 15% 深处：避免落在腔的已挖空区，能区分"只挖了腔没钻孔"
            z = max(1.0, H - depth + max(1.0, depth * 0.15))
            out.append(("孔 #%d（Ø%.0f @ (%.0f, %.0f)）"
                        % (len([1 for o in out if o[0].startswith("孔")]) + 1,
                           c[3], c[1], c[2]),
                        {"x": c[1], "y": c[2], "z": z}))
    return out


def probe(step, points):
    if not os.path.exists(os.path.join(WORK, "_probe.py")):
        with open(os.path.join(WORK, "_probe.py"), "w", encoding="utf-8") as fh:
            fh.write(PROBE_PY)
    env = dict(os.environ, SHAPE=step, POINTS=json.dumps(points))
    p = subprocess.run([freecad.FREECAD_CMD, os.path.join(WORK, "_probe.py")],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=180, env=env)
    for line in (p.stdout or "").splitlines():
        if line.startswith("PROBEJSON"):
            return json.loads(line[len("PROBEJSON"):])
    return [None] * len(points)


def diagnose(step, raw, size, gold_vol):
    """结构化诊断：逐特征看"该处还有没有料"，外加切除体积差。"""
    pr = probes(raw, size)
    inside = probe(step, [p for _, p in pr])
    lines = []
    for (label, _), ins in zip(pr, inside):
        state = "仍有余料（没切到）" if ins else "已切除"
        lines.append("- %s：%s" % (label, state))
    try:
        vol = freecad.geometry(step)["volume_mm3"]
        stock = float(size[0] * size[1] * size[2])
        cut, cut_gold = stock - vol, stock - gold_vol
        diff = cut_gold - cut
        if abs(diff) < 1.0:
            lines.append("- 切除体积 %.0f mm³，与目标相当（%.0f mm³），但位置/形状不符"
                         % (cut, cut_gold))
        else:
            lines.append("- 切除体积 %.0f mm³，目标 %.0f mm³，%s %.0f mm³"
                         % (cut, cut_gold, "少切" if diff > 0 else "多切", abs(diff)))
    except Exception:                             # noqa: BLE001
        lines.append("- 切除体积：无法测量（实体无效）")
    return "\n".join(lines)


def messages(text, history, feedback):
    body = (TOOL_DOC + "\n\n工单：" + text)
    if history:
        body += "\n\n你上一版的调用序列：\n" + json.dumps(history, ensure_ascii=False)
        body += "\n\n检查结果：\n" + feedback
        body += "\n\n请给出**修正后**的完整调用序列（从 create_box 开始）。"
    body += ("\n\n输出 JSON：{\"calls\": [{\"tool\": ..., \"args\": {...}}, ...]}。"
             "第一个调用必须是 cad.create_box；坐标用绝对 mm；不要解释。")
    return [{"role": "system", "content": SYS}, {"role": "user", "content": body}]


def run_arm(client, arm, rounds, max_tokens, limit=0):
    rows = []
    for tid, size, text, raw in (TASKS[:limit] if limit else TASKS):
        root = os.path.join(WORK, arm, tid)
        os.makedirs(root, exist_ok=True)
        gold = os.path.join(root, "gold.step")
        gold_vol = None
        if not os.path.exists(gold):
            g = execute(gold_calls(raw), os.path.join(root, "goldsrc"))
            if not g["step"]:
                print("  %s 标准答案构建失败，跳过" % tid)
                continue
            os.replace(g["step"], gold)
        gold_vol = float(freecad.geometry(gold)["volume_mm3"])

        history, feedback, best = None, None, {"iou": -1}
        row = {"id": tid, "arm": arm, "rounds": [], }
        t0 = time.time()
        for r in range(rounds):
            try:
                data, resp = client.complete_json(messages(text, history, feedback),
                                                  {"required": ["calls"],
                                                   "properties": {"calls": {}}},
                                                  max_tokens=max_tokens)
                calls = data.get("calls") or []
            except LLMError as exc:
                row["error"] = str(exc)[:160]
                break
            res = execute(calls, os.path.join(root, "r%d" % r))
            m = (_iou_paths(gold, res["step"], size) if res["step"]
                 else {"iou": None, "removed_iou": None})
            row["rounds"].append({"r": r + 1, "calls": calls, "n_calls": len(calls),
                                  "exec_ok": res["exec_ok"],
                                  "iou": m.get("iou"),
                                  "removed_iou": m.get("removed_iou"),
                                  "prompt_tokens": resp.prompt_tokens,
                                  "completion_tokens": resp.completion_tokens})
            cur = m.get("removed_iou")
            prev = best.get("removed_iou")
            if cur is not None and (prev is None or cur > prev):
                best = {"removed_iou": m.get("removed_iou"), "iou": m.get("iou"),
                        "step": res["step"], "calls": calls}
            if (m.get("removed_iou") or 0.0) >= SUCCESS_IOU:
                break
            history = calls
            if arm == "structured" and res["step"]:
                feedback = diagnose(res["step"], raw, size, gold_vol)
            else:
                feedback = "上一版几何不对。"
        first = row["rounds"][0]["removed_iou"] if row["rounds"] else None
        row.update({"initial_removed_iou": first,
                    "best_removed_iou": best.get("removed_iou"),
                    "rounds_used": len(row["rounds"]),
                    "solved": (best.get("removed_iou") or 0) >= SUCCESS_IOU,
                    "seconds": round(time.time() - t0, 1)})
        rows.append(row)
        print("  %-4s 首轮 %-6s → 最好 %-6s（%d 轮）%s"
              % (tid, None if first is None else round(first, 3),
                 None if row["best_removed_iou"] is None else round(row["best_removed_iou"], 3),
                 row["rounds_used"], "✓" if row["solved"] else ""))
    return rows


SEM_VARIANTS = ("vague", "structured", "structured_coords")


def _sem_feedback(kind, step, raw, size, gold_vol):
    if kind == "vague":
        return "上一版几何不对。"
    base = diagnose(step, raw, size, gold_vol)
    if kind == "structured":
        return base
    # 追加"应该在哪"：把标准答案的坐标直接告诉模型
    lines = [base, "正确做法应为："]
    for c in raw:
        if not isinstance(c, tuple) or not c:
            continue
        if c[0] == "pocket":
            lines.append("- 型腔：中心 (%.0f, %.0f)，%.0f×%.0f，深 %.0f"
                         % (c[1], c[2], c[3], c[4], c[5]))
        elif c[0] == "hole":
            lines.append("- 孔：Ø%.0f @ (%.0f, %.0f)" % (c[3], c[1], c[2]))
    return "\n".join(lines)


def semantic_test(client, tid, repeats, max_tokens):
    """同一份错误的首轮方案，换三种反馈各重试 N 次——检验"语义误读能否被反馈纠正"。"""
    task = [t for t in TASKS if t[0] == tid][0]
    _, size, text, raw = task
    root = os.path.join(WORK, "semantic", tid)
    os.makedirs(root, exist_ok=True)
    gold = os.path.join(WORK, "structured", tid, "gold.step")
    if not os.path.exists(gold):
        print("缺少标准答案，先跑 --arm structured")
        return None
    gold_vol = float(freecad.geometry(gold)["volume_mm3"])

    # 起点：用 structured 组存下来的首轮方案；没有就现生成一次
    plan = None
    rep = os.path.join(ROOT, "reports", "m5_repair_eval.json")
    if os.path.exists(rep):
        with open(rep, encoding="utf-8") as fh:
            data = json.load(fh)
        for row in data.get("arms", {}).get("structured", {}).get("rows", []):
            if row["id"] == tid and row["rounds"]:
                plan = row["rounds"][0]["calls"]
    if plan is None:
        data, _ = client.complete_json(messages(text, None, None),
                                       {"required": ["calls"], "properties": {"calls": {}}},
                                       max_tokens=max_tokens)
        plan = data.get("calls") or []

    res = execute(plan, os.path.join(root, "start"))
    start_iou = (_iou_paths(gold, res["step"], size).get("removed_iou")
                 if res["step"] else None)
    print("  起点方案 removed IoU = %s" % start_iou)

    out = {"id": tid, "start_removed_iou": start_iou, "variants": {}}
    for kind in SEM_VARIANTS:
        fb = _sem_feedback(kind, res["step"], raw, size, gold_vol) if res["step"] else "上一版几何不对。"
        ious = []
        for k in range(repeats):
            try:
                data, _ = client.complete_json(messages(text, plan, fb),
                                               {"required": ["calls"],
                                                "properties": {"calls": {}}},
                                               max_tokens=max_tokens)
            except LLMError:
                continue
            r2 = execute(data.get("calls") or [], os.path.join(root, "%s_%d" % (kind, k)))
            m = _iou_paths(gold, r2["step"], size) if r2["step"] else {}
            ious.append(m.get("removed_iou"))
        ok = [v for v in ious if v is not None]
        out["variants"][kind] = {"ious": ious,
                                 "solved": sum(1 for v in ok if v >= SUCCESS_IOU),
                                 "n": len(ok),
                                 "mean": round(sum(ok) / len(ok), 3) if ok else None}
        print("  %-18s 成功 %d/%d｜均在 %.3f"
              % (kind, out["variants"][kind]["solved"], len(ok),
                 out["variants"][kind]["mean"] or 0.0))
    return out


def summarize(rows, arm):
    first = [r["initial_removed_iou"] for r in rows if r["initial_removed_iou"] is not None]
    best = [r["best_removed_iou"] for r in rows if r["best_removed_iou"] is not None]
    return {"arm": arm, "n": len(rows),
            "solved": sum(r["solved"] for r in rows),
            "solved_rate": round(sum(r["solved"] for r in rows) / max(1, len(rows)), 3),
            "mean_initial_iou": round(sum(first) / len(first), 3) if first else None,
            "mean_best_iou": round(sum(best) / len(best), 3) if best else None,
            "mean_rounds": round(sum(r["rounds_used"] for r in rows) / max(1, len(rows)), 2),
            "prompt_tokens": sum(x.get("prompt_tokens", 0) for r in rows for x in r["rounds"]),
            "completion_tokens": sum(x.get("completion_tokens", 0) for r in rows for x in r["rounds"])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", default="both", choices=("structured", "vague", "both"))
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--max-tokens", type=int, default=900)
    ap.add_argument("--model", default="qwen3:8b")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 条（调试用）")
    ap.add_argument("--semantic", default="", help="对某条失败任务做反馈强度专项，如 R1")
    ap.add_argument("--repeats", type=int, default=3)
    args = ap.parse_args()

    client = LLMClient(model=args.model)
    ok, models = client.available()
    print("模型 %s｜%s" % (args.model, "可用" if ok else "不可用：%s" % models[0]))
    if not ok:
        return 2
    os.makedirs(WORK, exist_ok=True)

    out = {"model": args.model, "rounds_max": args.rounds,
           "success_iou": SUCCESS_IOU, "tasks": len(TASKS),
           "rule": "结构化反馈=逐特征余料检查+切除体积差；模糊反馈=只说不对",
           "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "arms": {}}
    if os.path.exists(OUT):
        try:
            with open(OUT, encoding="utf-8") as fh:
                out["arms"] = json.load(fh).get("arms", {})
        except Exception:                         # noqa: BLE001
            pass

    if args.semantic:
        print("\n→ 语义误读专项：%s，每种反馈重复 %d 次" % (args.semantic, args.repeats))
        sem = semantic_test(client, args.semantic, args.repeats, args.max_tokens)
        out["semantic"] = sem
        with open(OUT, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=1)
        print("→ %s" % os.path.relpath(OUT, ROOT))
        return 0

    for arm in (["structured", "vague"] if args.arm == "both" else [args.arm]):
        print("\n→ %s 反馈" % arm)
        rows = run_arm(client, arm, args.rounds, args.max_tokens, args.limit)
        out["arms"][arm] = {"summary": summarize(rows, arm), "rows": rows}
        print(json.dumps(out["arms"][arm]["summary"], ensure_ascii=False))

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print("\n→ %s" % os.path.relpath(OUT, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
