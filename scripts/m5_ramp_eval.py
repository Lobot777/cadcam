"""B18：非轴向特征——斜底槽（ramp bottom）。

为什么加这个：第二套 20 条仍是"矩形 + 圆柱"的排列组合，题再难也只是同一类特征。
斜底槽的槽底是**斜面**，是 Z 高度场里"每列高度不同"的第一类特征，
用它检验两件事：

1. **契约层能不能表达**——新增 `cad.pocket_ramp`，深度用"两端深度"而非单一 depth；
2. **模型能不能读对**——"从 X=30 到 X=70，深 5 渐变到 12"需要判断方向、端点、
   以及 depth 是**从顶面算**（不是从上一级腔底算）。

技术前提已验证（`build/_ramp_probe.py`）：Z-dexel 的高度图按列存单值，
斜面每列高度不同照样能栅格化（探针剖面 40.03 → 44.41 线性），所以
**不需要 3D dexel**；只有 undercut（倒扣）才需要，本实验不涉及。

    PYTHONPATH=src python scripts/m5_ramp_eval.py                 # 云端 10 个
    PYTHONPATH=src python scripts/m5_ramp_eval.py --local         # 本地 5 个
    PYTHONPATH=src python scripts/m5_ramp_eval.py --models glm-5.3
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from cadcam.agents.llm import LLMClient, LLMError

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from m5_nl_eval import _iou_paths, execute  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "build", "ramp")
OUT = os.path.join(ROOT, "reports", "m5_ramp.json")
SUCCESS_IOU = 0.90
CLOUD_MODELS = ["glm-5.3", "glm-5.3-flash", "kimi-k2.8-preview", "hy4std",
                "deepseek-v4-pro", "deepseek-v4.1-flash", "mimo-v2.6-pro",
                "step-5-preview", "minimax-m3.1-flash", "space-bunny"]
LOCAL_MODELS = ["qwen3:8b", "qwen3:14b", "llama3.1:8b", "glm4:9b", "gemma3:12b"]

SYS = "你是 CAD/CAM 工艺助手。只输出 JSON，不解释。"
TOOL_DOC = """可用工具（参数单位 mm，坐标原点在毛坯左下角，z 向上，顶面 z=height）：
1. cad.create_box(length, width, height)  —— 建毛坯 [0,L]×[0,W]×[0,H]，必须是第一个调用
2. cad.pocket(center_x, center_y, width, height, depth) —— 从顶面向下切一个平底矩形腔
3. cad.pocket_ramp(center_x, center_y, width, height, depth_near, depth_far, axis)
   —— 斜底槽：槽底沿 axis（"x" 或 "y"）正方向，由 depth_near 线性变到 depth_far；
      两个深度都**从毛坯顶面算**（不是从上一级腔底算）
4. cad.hole(x, y, diameter, depth=10, through=false) —— 钻孔，通孔设 through=true"""


def box(L, W, H):
    return {"tool": "cad.create_box", "args": {"length": L, "width": W, "height": H}}


def pocket(cx, cy, w, h, d):
    return {"tool": "cad.pocket",
            "args": {"center_x": cx, "center_y": cy, "width": w, "height": h, "depth": d}}


def ramp(cx, cy, w, h, d0, d1, axis="x"):
    return {"tool": "cad.pocket_ramp",
            "args": {"center_x": cx, "center_y": cy, "width": w, "height": h,
                     "depth_near": d0, "depth_far": d1, "axis": axis}}


def hole(x, y, d, through=True, depth=None):
    a = {"x": x, "y": y, "diameter": d}
    if through:
        a["through"] = True
    else:
        a["depth"] = depth
    return {"tool": "cad.hole", "args": a}


TASKS = [
    {"id": "R1", "size": [100, 80, 50], "tag": "单坡",
     "text": "100×80×50：中央开一个 40×30 的斜底槽，槽底沿 X 正方向从深 4 线性过渡到深 9。",
     "calls": [box(100, 80, 50), ramp(50, 40, 40, 30, 4, 9, "x")]},
    {"id": "R2", "size": [120, 90, 50], "tag": "单坡",
     "text": "120×90×50：中央一个 60×40 的槽，左端深 3、右端深 12，槽底从左到右直线过渡。",
     "calls": [box(120, 90, 50), ramp(60, 45, 60, 40, 3, 12, "x")]},
    {"id": "R3", "size": [100, 100, 50], "tag": "换轴",
     "text": "100×100×50：中央 50×50 的斜底槽，Y 小的一端深 5，Y 大的一端深 15，线性过渡。",
     "calls": [box(100, 100, 50), ramp(50, 50, 50, 50, 5, 15, "y")]},
    {"id": "R4", "size": [90, 70, 50], "tag": "换轴",
     "text": "90×70×50：一条 70×20 的槽，Y 方向居中，X 从 10 到 80；"
             "X=10 那头深 2，X=80 那头深 8。",
     "calls": [box(90, 70, 50), ramp(45, 35, 70, 20, 2, 8, "x")]},
    {"id": "R5", "size": [100, 80, 50], "tag": "复合",
     "text": "100×80×50：先铣中央 60×40 深 5 的平底腔；再在这个腔的范围内做斜坡，"
             "X 小的一端仍是 5 深，X 大的一端切到 12 深。",
     "calls": [box(100, 80, 50), pocket(50, 40, 60, 40, 5),
               ramp(50, 40, 60, 40, 5, 12, "x")]},
    {"id": "R6", "size": [120, 100, 50], "tag": "复合",
     "text": "120×100×50：中央 80×30 的斜底槽，沿 X 从深 4 渐深到 10；"
             "四角各 Ø8 通孔，孔心距两边各 10。",
     "calls": [box(120, 100, 50), ramp(60, 50, 80, 30, 4, 10, "x"),
               hole(10, 10, 8), hole(110, 10, 8), hole(10, 90, 8), hole(110, 90, 8)]},
]


def messages(text):
    body = (TOOL_DOC + "\n\n工单：" + text + "\n\n"
            "输出 JSON：{\"calls\": [{\"tool\": \"cad.pocket\", \"args\": {...}}, ...]}。\n"
            "要求：第一个调用必须是 cad.create_box；坐标用绝对 mm；不要输出解释。")
    return [{"role": "system", "content": SYS}, {"role": "user", "content": body}]


def ensure_gold(t):
    root = os.path.join(WORK, t["id"])
    os.makedirs(root, exist_ok=True)
    import hashlib
    sig = hashlib.md5(json.dumps(t["calls"], sort_keys=True,
                                 ensure_ascii=False).encode()).hexdigest()[:8]
    gold = os.path.join(root, "gold-%s.step" % sig)
    if not os.path.exists(gold):
        g = execute(t["calls"], os.path.join(root, "goldsrc-%s" % sig))
        if not g["step"]:
            raise RuntimeError("标准答案构建失败：%s" % t["id"])
        os.replace(g["step"], gold)
    return gold


def run(client, tag, max_tokens):
    rows = []
    for t in TASKS:
        gold = ensure_gold(t)
        sub = os.path.join(WORK, t["id"], tag)
        try:
            data, resp = client.complete_json(messages(t["text"]),
                                              {"required": ["calls"],
                                               "properties": {"calls": {}}},
                                              max_tokens=max_tokens)
            calls, err = data.get("calls") or [], None
        except LLMError as exc:
            calls, resp, err = [], None, str(exc)[:150]
        res = execute(calls, sub)
        m = _iou_paths(gold, res["step"], t["size"]) if res["step"] else {}
        rows.append({"id": t["id"], "tag": t["tag"], "exec_ok": res["exec_ok"],
                     "legal": res["legal"], "n_calls": len(calls),
                     "iou": m.get("iou"), "removed_iou": m.get("removed_iou"),
                     "calls": calls, "error": err,
                     "tokens": (resp.prompt_tokens, resp.completion_tokens) if resp else (0, 0)})
    ok = [r for r in rows if r["exec_ok"]]
    rem = [r["removed_iou"] for r in ok if r["removed_iou"] is not None]
    return {"rows": rows, "n": len(rows), "exec": len(ok),
            "mean_removed_iou": round(sum(rem) / len(rem), 3) if rem else None,
            "exact": sum(1 for v in rem if v >= 0.9999),
            "removed_ge_0_9": sum(1 for v in rem if v >= SUCCESS_IOU),
            "legal_rate": round(sum(r["legal"] for r in rows)
                                / max(1, sum(r["n_calls"] for r in rows)), 3),
            "tokens": [sum(r["tokens"][0] for r in rows),
                       sum(r["tokens"][1] for r in rows)]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="")
    ap.add_argument("--local", action="store_true")
    ap.add_argument("--max-tokens", type=int, default=2200)
    args = ap.parse_args()
    models = ([m.strip() for m in args.models.split(",") if m.strip()]
              or (LOCAL_MODELS if args.local else CLOUD_MODELS))
    provider = "ollama" if args.local else "cloud"
    report = {"provider": provider, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
              "tool": "cad.pocket_ramp", "tasks": len(TASKS), "models": {}}
    if os.path.exists(OUT):
        try:
            with open(OUT, encoding="utf-8") as fh:
                report["models"] = json.load(fh).get("models", {})
        except Exception:                               # noqa: BLE001
            pass
    for m in models:
        client = (LLMClient(base_url="", model=m, api="cloud", retries=3, timeout=600.0)
                  if not args.local else LLMClient(model=m))
        print("\n=== %s ===" % m)
        r = run(client, m.replace(":", "_"), args.max_tokens)
        for row in r["rows"]:
            print("  %-3s %-4s 执行%-5s 调用%d IoU %-6s %s"
                  % (row["id"], row["tag"], row["exec_ok"], row["n_calls"],
                     round(row["removed_iou"], 3) if row["removed_iou"] is not None else None,
                     ("｜" + row["error"][:40]) if row["error"] else ""))
        print("  → 执行 %d/%d｜均特征 IoU %s｜精确 %d/%d｜≥0.9 %d"
              % (r["exec"], r["n"], r["mean_removed_iou"], r["exact"],
                 r["n"], r["removed_ge_0_9"]))
        report["models"][m] = r
        with open(OUT, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1)
    print("\n→ %s" % os.path.relpath(OUT, ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
