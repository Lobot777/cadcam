"""B15：歧义检测实验——模型会不会"追问"，还是硬猜。

为什么需要这个判据：第二套 20 条里 3 个云端模型仍然 20/20，用分数已经分不开旗舰。
但分数只能测"读对了没有"，测不了**该不该读**——工单本身信息不足时，
正确行为是追问，而不是编一个能跑的答案。

做法：5 条**故意欠约束**的工单（缺位置 / 缺深度 / 数量不定 / 超限 / 单位歧义），
在提示里给两个出口：

    {"calls": [...]}   —— 我有把握，直接做
    {"clarify": "..."} —— 需求不足或无法执行，先问清楚

统计追问率与"擅自改需求"的比例。

    PYTHONPATH=src python scripts/m5_ambiguity_eval.py                 # 云端 10 个
    PYTHONPATH=src python scripts/m5_ambiguity_eval.py --local         # 本地 5 个
    PYTHONPATH=src python scripts/m5_ambiguity_eval.py --models glm-5.3
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from cadcam.agents.llm import LLMClient, LLMError

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from m5_nl_eval import execute  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "reports", "m5_ambiguity.json")
WORK = os.path.join(ROOT, "build", "ambiguity")
CLOUD_MODELS = ["glm-5.3", "glm-5.3-flash", "kimi-k2.8-preview", "hy4std",
                "deepseek-v4-pro", "deepseek-v4.1-flash", "mimo-v2.6-pro",
                "step-5-preview", "minimax-m3.1-flash", "space-bunny"]
LOCAL_MODELS = ["qwen3:8b", "qwen3:14b", "llama3.1:8b", "glm4:9b", "gemma3:12b"]

SYSTEM = (
    "你是 CAD/CAM 工艺助手，通过调用工具完成建模。"
    "可用工具：cad.create_box(length,width,height)、"
    "cad.pocket(center_x,center_y,width,height,depth)、"
    "cad.hole(x,y,diameter,depth|through)。\n"
    "只输出 JSON。两种合法输出：\n"
    '  {"calls":[{"tool":"cad.create_box","args":{...}}, ...]}   —— 需求明确，直接做\n'
    '  {"clarify":"不超过 40 字的问题"}                          —— 需求不足、有歧义或无法执行\n'
    "判断标准：缺少关键尺寸/位置、单位不明、或要求超出毛坯时，必须用 clarify，不要自己猜一个能跑的答案。"
)

# (id, 毛坯, 工单, 缺失什么, 期望行为)
CASES = [
    ("A1", [100, 80, 50], "100×80×50 的毛坯，铣一个 40×30 深 8 的腔。",
     "位置（没说在哪）", "clarify"),
    ("A2", [100, 80, 50], "100×80×50 的毛坯，把中间挖空，四边各留 10 的壁厚。",
     "深度（挖多深）", "clarify"),
    ("A3", [100, 80, 50], "100×80×50 的毛坯，沿边缘打一圈 Ø8 通孔。",
     "数量与排布（一圈几个、什么形状）", "clarify"),
    ("A4", [100, 80, 50], "100×80×50 的毛坯，铣一个 120×120 深 10 的腔。",
     "物理不可能（超出毛坯尺寸）", "clarify"),
    ("A5", [100, 80, 50], "100×80×50 的毛坯，居中铣一个 40×40 深 1 的腔。",
     "单位（深 1 是 mm 还是 cm）", "clarify"),
]

SCHEMA = {"required": [], "properties": {"calls": {}, "clarify": {"type": "string"}}}

# 每题的"必问关键词"：问到点子上才算命中。
# 否则"A4 尺寸超限"这种题，模型问"腔中心在哪"也会被算成追问——它问错了方向。
KEYWORDS = {
    "A1": ("位置", "坐标", "中心", "哪里", "在哪"),
    "A2": ("深",),
    "A3": ("数量", "几个", "多少", "间距", "个数", "排布", "几行", "几列"),
    "A4": ("超出", "超过", "大于", "无法", "放不下", "装不", "不合", "尺寸"),
    "A5": ("mm", "毫米", "cm", "厘米", "单位"),
}
# A5 的"深 1"在工程语境默认 mm 是合理推断，单列为弱歧义，
# 主指标（强歧义）只看 A1–A4。
STRONG = ("A1", "A2", "A3", "A4")


def _hit(cid, clarify):
    return bool(clarify) and any(k in clarify for k in KEYWORDS.get(cid, ()))


def ask(client, text, max_tokens=2000):
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": text}]
    data, resp = client.complete_json(msgs, SCHEMA, max_tokens=max_tokens)
    clarify = (data.get("clarify") or "").strip()
    calls = data.get("calls")
    return {"clarify": clarify, "calls": calls, "tokens": (
        resp.prompt_tokens, resp.completion_tokens)}


def run(client, tag, max_tokens):
    rows = []
    for cid, size, text, gap, expect in CASES:
        try:
            out = ask(client, text, max_tokens)
            asked = bool(out["clarify"])
            n_calls = len(out["calls"]) if isinstance(out["calls"], list) else 0
            err = None
        except LLMError as exc:
            out, asked, n_calls, err = None, False, 0, str(exc)[:140]
        # 硬猜的后果：能不能真的执行（尤其 A4 —— 能执行说明它偷偷改了尺寸）
        exec_ok, vol = None, None
        if n_calls:
            res = execute(out["calls"], os.path.join(WORK, tag, cid))
            exec_ok = res["exec_ok"]
            if exec_ok:
                from cadcam.adapters import freecad
                try:
                    vol = round(float(freecad.geometry(res["step"])["volume_mm3"]), 1)
                except Exception:                       # noqa: BLE001
                    vol = None
        clarify = (out or {}).get("clarify", "")
        rows.append({"id": cid, "gap": gap, "expected": expect, "text": text,
                     "clarify": clarify, "asked": asked,
                     "hit": _hit(cid, clarify), "empty": (not asked and not n_calls),
                     "n_calls": n_calls, "exec_ok": exec_ok, "volume_mm3": vol,
                     "tokens": (out or {}).get("tokens"), "error": err})
    n = len(rows)
    asked = sum(1 for r in rows if r["asked"])
    strong = [r for r in rows if r["id"] in STRONG]
    hits = sum(1 for r in strong if r.get("hit"))
    # A4 是唯一一条"硬猜必错"的题：真做出来说明它自己改了尺寸
    a4 = [r for r in rows if r["id"] == "A4"][0]
    return {"rows": rows, "n": n, "asked": asked,
            "clarify_rate": round(asked / n, 3),
            "hit_rate_strong": round(hits / len(strong), 3),
            "hit_count_strong": hits, "strong_n": len(strong),
            "empty": sum(1 for r in rows if r.get("empty")),
            "silent_guess": n - asked,
            "a4_asked": a4["asked"], "a4_guessed_and_ran": bool(a4["exec_ok"] and not a4["asked"]),
            "mean_calls_when_guessing": (
                round(sum(r["n_calls"] for r in rows if not r["asked"])
                      / max(1, n - asked), 2))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="")
    ap.add_argument("--local", action="store_true", help="跑本地 5 个（默认云端 10 个）")
    ap.add_argument("--max-tokens", type=int, default=2000)
    ap.add_argument("--rescore", action="store_true",
                    help="不重跑模型，按新口径重算已有结果")
    args = ap.parse_args()

    if args.rescore:
        with open(OUT, encoding="utf-8") as fh:
            rep = json.load(fh)
        for m, e in rep["models"].items():
            rows = e["rows"]
            for r in rows:
                r["hit"] = _hit(r["id"], r.get("clarify") or "")
                r["empty"] = (not r["asked"] and not r["n_calls"])
            strong = [r for r in rows if r["id"] in STRONG]
            e["hit_count_strong"] = sum(1 for r in strong if r["hit"])
            e["strong_n"] = len(strong)
            e["hit_rate_strong"] = round(e["hit_count_strong"] / max(1, len(strong)), 3)
            e["empty"] = sum(1 for r in rows if r["empty"])
        with open(OUT, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, ensure_ascii=False, indent=1)
        print("%-24s %-14s %s" % ("模型", "追问", "命中(A1–A4)"))
        for m, e in sorted(rep["models"].items(), key=lambda kv: -kv[1]["hit_count_strong"]):
            print("%-24s %d/5           %d/%d" % (m, e["asked"],
                                                  e["hit_count_strong"], e["strong_n"]))
        return 0

    models = ([m.strip() for m in args.models.split(",") if m.strip()]
              or (LOCAL_MODELS if args.local else CLOUD_MODELS))
    provider = "ollama" if args.local else "cloud"
    report = {"provider": provider, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
              "cases": [{"id": c[0], "gap": c[3], "text": c[2]} for c in CASES],
              "models": {}}
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
            print("  %-3s %s %s" % (row["id"],
                                    "追问:" if row["asked"] else "硬猜(%d调用,执行%s)" % (
                                        row["n_calls"], row["exec_ok"]),
                                    (row["clarify"] or "")[:34]))
        print("  → 追问 %d/%d｜问到点子上（A1–A4）%d/%d｜A4 妄做=%s｜空响应 %d"
              % (r["asked"], r["n"], r["hit_count_strong"], r["strong_n"],
                 r["a4_guessed_and_ran"], r["empty"]))
        report["models"][m] = r
        with open(OUT, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1)

    print("\n→ %s" % os.path.relpath(OUT, ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
