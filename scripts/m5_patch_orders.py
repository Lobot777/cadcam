"""改写某一道工单后，只补跑这一条并回收结果（不重跑整个任务集）。

用途：题目表述改了（如 H06 的「对称」→「给定边距 + 空隙」），其余题目未动，
没必要把 15 个模型 × 30 条重跑一遍。

    PYTHONPATH=src python scripts/m5_patch_orders.py --task H06
    PYTHONPATH=src python scripts/m5_patch_orders.py --task H06 --cloud-only

它会：
1. 用**新的**工单文本重新问一遍；
2. 复用已有的标准答案 STEP（标准调用没变，gold 不变）；
3. 覆盖 json 里那一行，并重算 exec / mean_removed_iou / removed_ge_0_9。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from cadcam.agents.llm import LLMClient, LLMError

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from m5_hard2 import make_extra_tasks  # noqa: E402
from m5_model_compare import WORK, ensure_gold  # noqa: E402
from m5_nl_eval import _iou_paths, contract_messages, execute, make_hard_tasks  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL_JSON = os.path.join(ROOT, "reports", "m5_model_compare.json")
CLOUD_JSON = os.path.join(ROOT, "reports", "m5_cloud_compare.json")
EXTRA_LOCAL = os.path.join(ROOT, "reports", "m5_extra_local.json")
EXTRA_CLOUD = os.path.join(ROOT, "reports", "m5_extra_compare.json")
SUCCESS_IOU = 0.90


def patch(path, task, max_tokens, cloud):
    if not os.path.exists(path):
        print("[跳过] 没有 %s" % os.path.relpath(path, ROOT))
        return
    with open(path, encoding="utf-8") as fh:
        rep = json.load(fh)
    gold = ensure_gold(task, os.path.join(WORK, "gold", task["id"]))
    for model, entry in rep.get("models", {}).items():
        orders = entry.get("orders") or {}
        if not orders.get("rows"):
            continue
        # 判断这个模型属于哪一侧：云端条目带 meter / quota 字段
        is_cloud = "meter" in entry or "quota" in entry
        if is_cloud != cloud:
            continue
        client = (LLMClient(base_url="", model=model, api="cloud", retries=3, timeout=600.0)
                  if cloud else LLMClient(model=model))
        sub = os.path.join(WORK, "orders", model.replace(":", "_"), task["id"], "patched")
        try:
            data, resp = client.complete_json(
                contract_messages(task["text"]),
                {"required": ["calls"], "properties": {"calls": {}}},
                max_tokens=max_tokens)
            calls, err = data.get("calls") or [], None
        except LLMError as exc:
            calls, resp, err = [], None, str(exc)[:160]
        res = execute(calls, sub)
        m = _iou_paths(gold, res["step"], task["size"]) if res["step"] else {}
        row = {"id": task["id"], "tag": task.get("tag"),
               "exec_ok": res["exec_ok"], "n_calls": len(calls), "legal": res["legal"],
               "iou": m.get("iou"), "removed_iou": m.get("removed_iou"), "error": err,
               "prompt_tokens": (resp.prompt_tokens if resp else 0),
               "completion_tokens": (resp.completion_tokens if resp else 0)}
        for i, old in enumerate(orders["rows"]):
            if old["id"] == task["id"]:
                orders["rows"][i] = row
                break
        ok = [r for r in orders["rows"] if r["exec_ok"]]
        rem = [r["removed_iou"] for r in ok if r["removed_iou"] is not None]
        orders["exec"] = len(ok)
        orders["mean_removed_iou"] = round(sum(rem) / len(rem), 3) if rem else None
        orders["removed_ge_0_9"] = sum(1 for v in rem if v >= SUCCESS_IOU)
        print("  %-24s IoU %-6s exec %s → 均 %s（≥0.9 %d/%d）"
              % (model, row["removed_iou"], row["exec_ok"],
                 orders["mean_removed_iou"], orders["removed_ge_0_9"], len(rem)))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, ensure_ascii=False, indent=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="H06")
    ap.add_argument("--max-tokens", type=int, default=2000)
    ap.add_argument("--cloud-only", action="store_true")
    ap.add_argument("--local-only", action="store_true")
    ap.add_argument("--set", default="hard", choices=("hard", "extra"))
    args = ap.parse_args()

    local_p, cloud_p = ((EXTRA_LOCAL, EXTRA_CLOUD) if args.set == "extra"
                        else (LOCAL_JSON, CLOUD_JSON))
    spec = make_extra_tasks() if args.set == "extra" else make_hard_tasks()
    task = [t for t in spec if t["id"] == args.task][0]
    print("补跑 %s：%s" % (task["id"], task["text"]))
    if not args.cloud_only:
        print("\n== 本地 ==")
        patch(local_p, task, args.max_tokens, cloud=False)
    if not args.local_only:
        print("\n== 云端 ==")
        patch(cloud_p, task, args.max_tokens, cloud=True)
    print("\n下一步：PYTHONPATH=src python scripts/m5_cloud_report.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
