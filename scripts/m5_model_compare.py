"""跨模型对照：同一套任务、同一份错误起点，比"各家开源量化模型"。

两个子实验（都对每个模型跑一遍）：

  A 工单落地：30 条难工单 → 契约调用 → 特征去除体积 IoU
  B 纠错能力：给定**同一份错误方案**（型腔放正中），三种反馈各 2 次，看能否改对

    PYTHONPATH=src python scripts/m5_model_compare.py
    PYTHONPATH=src python scripts/m5_model_compare.py --models qwen3:8b,qwen3:14b
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from cadcam.agents.llm import LLMClient, LLMError

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from m5_hard2 import make_extra_tasks  # noqa: E402
from m5_nl_eval import (_iou_paths, contract_messages, execute,  # noqa: E402
                        make_hard_tasks)
from m5_repair_eval import diagnose, messages as repair_messages  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "build", "model_compare")
OUT = os.path.join(ROOT, "reports", "m5_model_compare.json")
DEFAULT_MODELS = ["qwen3:8b", "qwen3:14b", "llama3.1:8b", "glm4:9b", "gemma3:12b"]
SUCCESS_IOU = 0.90

# 受控的错误起点：H01 要求左上角 (20,60)，这里故意放在正中 (40,40)
WRONG_START = [
    {"tool": "cad.create_box", "args": {"length": 80, "width": 80, "height": 50}},
    {"tool": "cad.pocket", "args": {"center_x": 40, "center_y": 40,
                                    "width": 30, "height": 30, "depth": 12}},
]


def task_by_id(tid):
    return [t for t in make_hard_tasks() if t["id"] == tid][0]


def ensure_gold(task, root):
    """把标准答案构建成 STEP，按**答案指纹**缓存。

    早先只用 gold.step 一个固定文件名，改了标准答案后仍复用旧文件 ——
    E12 就是这样把 10 个云端模型全判成 0.50 的。签名后改答案会自动重建。
    """
    import hashlib
    os.makedirs(root, exist_ok=True)
    sig = hashlib.md5(json.dumps(task["calls"], sort_keys=True,
                                 ensure_ascii=False).encode()).hexdigest()[:8]
    gold = os.path.join(root, "gold-%s.step" % sig)
    if not os.path.exists(gold):
        g = execute(task["calls"], os.path.join(root, "goldsrc-%s" % sig))
        if not g["step"]:
            raise RuntimeError("标准答案构建失败：%s" % task["id"])
        os.replace(g["step"], gold)
    return gold


def run_orders(client, tasks, max_tokens, root, model_tag):
    rows = []
    for t in tasks:
        sub = os.path.join(root, model_tag, t["id"])
        os.makedirs(sub, exist_ok=True)
        gold = ensure_gold(t, os.path.join(WORK, "gold", t["id"]))
        try:
            data, resp = client.complete_json(contract_messages(t["text"]),
                                              {"required": ["calls"],
                                               "properties": {"calls": {}}},
                                              max_tokens=max_tokens)
            calls = data.get("calls") or []
            err = None
        except LLMError as exc:
            calls, resp, err = [], None, str(exc)[:160]
        res = execute(calls, os.path.join(sub, "run"))
        m = _iou_paths(gold, res["step"], t["size"]) if res["step"] else {}
        rows.append({"id": t["id"], "tag": t.get("tag"), "exec_ok": res["exec_ok"],
                     "n_calls": len(calls), "legal": res["legal"],
                     "iou": m.get("iou"), "removed_iou": m.get("removed_iou"),
                     "error": err,
                     "prompt_tokens": (resp.prompt_tokens if resp else 0),
                     "completion_tokens": (resp.completion_tokens if resp else 0)})
    ok = [r for r in rows if r["exec_ok"]]
    rem = [r["removed_iou"] for r in ok if r["removed_iou"] is not None]
    return {"rows": rows, "exec": len(ok), "n": len(rows),
            "mean_removed_iou": round(sum(rem) / len(rem), 3) if rem else None,
            "removed_ge_0_9": sum(1 for v in rem if v >= SUCCESS_IOU),
            "legal_rate": round(sum(r["legal"] for r in rows)
                                / max(1, sum(r["n_calls"] for r in rows)), 3),
            "tokens": [sum(r["prompt_tokens"] for r in rows),
                       sum(r["completion_tokens"] for r in rows)]}


def run_repair(client, model_tag, repeats, max_tokens):
    task = task_by_id("H01")
    root = os.path.join(WORK, model_tag, "repair")
    os.makedirs(root, exist_ok=True)
    gold = ensure_gold(task, os.path.join(WORK, "gold", "H01"))
    gold_vol = None
    from cadcam.adapters import freecad
    gold_vol = float(freecad.geometry(gold)["volume_mm3"])

    start = execute(WRONG_START, os.path.join(root, "start"))
    start_iou = (_iou_paths(gold, start["step"], task["size"]).get("removed_iou")
                 if start["step"] else None)
    fb_struct = diagnose(start["step"], task["calls"], task["size"], gold_vol)
    fb_coords = fb_struct + "\n正确做法：型腔中心应在 (20, 60)，尺寸 30×30，深 12。"
    variants = {"vague": "上一版几何不对。", "structured": fb_struct,
                "structured_coords": fb_coords}

    out = {"start_removed_iou": start_iou, "variants": {}}
    for kind, fb in variants.items():
        ious = []
        for k in range(repeats):
            try:
                data, _ = client.complete_json(
                    repair_messages(task["text"], WRONG_START, fb),
                    {"required": ["calls"], "properties": {"calls": {}}},
                    max_tokens=max_tokens)
            except LLMError:
                ious.append(None)
                continue
            r = execute(data.get("calls") or [], os.path.join(root, "%s_%d" % (kind, k)))
            m = _iou_paths(gold, r["step"], task["size"]) if r["step"] else {}
            ious.append(m.get("removed_iou"))
        good = [v for v in ious if v is not None]
        out["variants"][kind] = {
            "ious": ious,
            "solved": sum(1 for v in good if v >= SUCCESS_IOU),
            "n": len(good),
            "mean": round(sum(good) / len(good), 3) if good else None}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS))
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--max-tokens", type=int, default=900)
    ap.add_argument("--skip-orders", action="store_true")
    ap.add_argument("--skip-repair", action="store_true")
    ap.add_argument("--provider", default="ollama", choices=("ollama", "cloud"),
                    help="ollama=本地量化模型；cloud=WorkBuddy 云服务（按账号积分计费）")
    ap.add_argument("--out", default="", help="结果文件（默认 reports/m5_model_compare.json）")
    ap.add_argument("--set", default="hard", choices=("hard", "extra"),
                    help="hard=30 条主任务集；extra=20 条更难的第二套（B14）")
    args = ap.parse_args()

    global OUT
    if args.out:
        OUT = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    os.makedirs(WORK, exist_ok=True)
    tasks = (make_extra_tasks() if args.set == "extra"
             else make_hard_tasks())[:args.limit]
    report = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
              "provider": args.provider, "set": args.set,
              "tasks": len(tasks), "success_iou": SUCCESS_IOU,
              "wrong_start": "H01 的型腔被放在正中 (40,40)，正确是 (20,60)",
              "models": {}}
    if os.path.exists(OUT):
        try:
            with open(OUT, encoding="utf-8") as fh:
                report["models"] = json.load(fh).get("models", {})
        except Exception:                       # noqa: BLE001
            pass

    for model in [m.strip() for m in args.models.split(",") if m.strip()]:
        client = (LLMClient(base_url="", model=model, api="cloud",
                            retries=3, timeout=300.0)
                  if args.provider == "cloud" else LLMClient(model=model))
        ok, have = client.available()
        if not ok:
            print("[跳过] %s：服务不可用" % model)
            continue
        if model not in [str(x) for x in have] and not any(model in str(x) for x in have):
            print("[跳过] %s：尚未下载（已装：%s）" % (model, have))
            continue
        print("\n=== %s ===" % model)
        entry = report["models"].get(model, {})
        t0 = time.time()
        if not args.skip_orders:
            o = run_orders(client, tasks, args.max_tokens,
                           os.path.join(WORK, "orders"), model.replace(":", "_"))
            entry["orders"] = o
            print("  工单：执行 %d/%d｜均特征IoU %s｜≥0.9 %d｜合法率 %s"
                  % (o["exec"], o["n"], o["mean_removed_iou"], o["removed_ge_0_9"],
                     o["legal_rate"]))
        if args.set == "extra":
            print("  （extra 集不跑纠错专项：错起点来自 HARD 的 H01）")
        elif not args.skip_repair:
            r = run_repair(client, model.replace(":", "_"), args.repeats,
                           args.max_tokens)
            entry["repair"] = r
            print("  纠错（起点 IoU %.2f）：vague %d/%d｜struct %d/%d｜+coords %d/%d"
                  % (r["start_removed_iou"] or 0,
                     r["variants"]["vague"]["solved"], r["variants"]["vague"]["n"],
                     r["variants"]["structured"]["solved"], r["variants"]["structured"]["n"],
                     r["variants"]["structured_coords"]["solved"],
                     r["variants"]["structured_coords"]["n"]))
        entry["seconds"] = round(time.time() - t0, 1)
        if args.provider == "cloud":
            entry["quota"] = (client.meter.last_usage or {}).get("quota")
            entry["meter"] = client.meter.summary()
        report["models"][model] = entry
        with open(OUT, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1)

    print("\n→ %s" % os.path.relpath(OUT, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
