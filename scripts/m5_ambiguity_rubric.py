"""B17：追问质量评分——从"有没有问"升级到"问得全不全"。

关键词命中（`m5_ambiguity_eval.py`）只能判"问到点上"，判不了"是不是一次问全"。
这里给每题定义**必问要素**，逐要素判定，得出 0–2 分：

- 2 分：该题的关键要素全部提到
- 1 分：提到一部分
- 0 分：没提到（硬猜 / 问错方向）

规则版（`score_rules`）可复现；人工版（`MANUAL`）是本文件里手写的评分，
两者一起报，并给出逐条一致性——**规则判错的地方要能看见**。

    PYTHONPATH=src python scripts/m5_ambiguity_rubric.py
    PYTHONPATH=src python scripts/m5_ambiguity_rubric.py --diff   # 只看人工/规则不一致的
"""
from __future__ import annotations

import argparse
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "reports", "m5_ambiguity.json")
OUT = os.path.join(ROOT, "reports", "m5_ambiguity_rubric.json")

# 每题的必问要素 -> 关键词组（任一命中即算提到）
ASPECTS = {
    "A1": {"位置": ("位置", "坐标", "中心", "X", "Y", "基准", "center")},
    "A2": {"深度": ("深", "底厚", "多厚", "多深"),
           "是否通/底厚": ("贯穿", "通", "留底", "保留底", "底板", "铣穿", "壁厚")},
    "A3": {"数量": ("数量", "孔数", "几个", "多少", "个数", "每边", "几行", "几列", "共"),
           "排布/间距": ("间距", "距边缘", "边距", "排布", "起始", "分布")},
    "A4": {"指出超限": ("超出", "超过", "大于", "放不下", "装不", "容纳", "尺寸"),
           "处理方向": ("缩小", "改", "换", "确认", "如何", "是否", "调整", "建议")},
}

# 人工评分（2026-11-06，逐条读原文打分；A5 弱歧义不计）
# 记法：{题: 分}，2=要素齐全，1=部分，0=没问/问错
MANUAL = {
    "glm-5.3":            {"A1": 2, "A2": 2, "A3": 2, "A4": 2},
    "kimi-k2.8-preview":  {"A1": 2, "A2": 2, "A3": 2, "A4": 2},
    "hy4std":             {"A1": 2, "A2": 2, "A3": 2, "A4": 2},
    "deepseek-v4.1-flash": {"A1": 2, "A2": 2, "A3": 2, "A4": 2},
    "step-5-preview":     {"A1": 2, "A2": 2, "A3": 2, "A4": 2},
    "space-bunny":        {"A1": 2, "A2": 2, "A3": 2, "A4": 2},
    "glm-5.3-flash":      {"A1": 0, "A2": 2, "A3": 2, "A4": 2},
    "minimax-m3.1-flash": {"A1": 2, "A2": 2, "A3": 1, "A4": 1},
    "deepseek-v4-pro":    {"A1": 0, "A2": 1, "A3": 2, "A4": 1},
    "mimo-v2.6-pro":      {"A1": 0, "A2": 0, "A3": 2, "A4": 2},
    "gemma3:12b":         {"A1": 0, "A2": 1, "A3": 1, "A4": 0},
    "qwen3:8b":           {"A1": 0, "A2": 0, "A3": 0, "A4": 1},
    "qwen3:14b":          {"A1": 0, "A2": 0, "A3": 0, "A4": 1},
    "llama3.1:8b":        {"A1": 0, "A2": 0, "A3": 0, "A4": 0},
    "glm4:9b":            {"A1": 0, "A2": 0, "A3": 0, "A4": 0},
}


def score_one(cid, clarify):
    """返回 (分, 命中的要素, 缺失的要素)。"""
    if not clarify:
        return 0, [], list(ASPECTS[cid])
    hit, miss = [], []
    for name, kws in ASPECTS[cid].items():
        (hit if any(k in clarify for k in kws) else miss).append(name)
    total = len(hit) + len(miss)
    if not hit:
        return 0, hit, miss
    return (2 if not miss else 1), hit, miss


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--diff", action="store_true")
    args = ap.parse_args()
    rep = json.load(open(SRC, encoding="utf-8"))
    rows = []
    for m, e in rep["models"].items():
        per = {}
        for r in e["rows"]:
            if r["id"] == "A5":
                continue
            pts, hit, miss = score_one(r["id"], r["clarify"] or "")
            per[r["id"]] = {"score": pts, "hit": hit, "miss": miss,
                            "clarify": r["clarify"] or "",
                            "guessed": not r["clarify"]}
        rules_total = sum(v["score"] for v in per.values())
        manual = MANUAL.get(m, {})
        manual_total = sum(manual.values()) if manual else None
        agree = (sum(1 for k in per if manual.get(k) == per[k]["score"])
                 if manual else None)
        rows.append({"model": m, "rules_total": rules_total,
                     "manual_total": manual_total, "agree": agree,
                     "per": per})
    rows.sort(key=lambda r: -(r["manual_total"] if r["manual_total"] is not None
                              else r["rules_total"]))

    print("%-24s %-10s %-10s %s" % ("模型", "规则(0–8)", "人工(0–8)", "逐条一致"))
    for r in rows:
        print("%-24s %-10d %-10s %s"
              % (r["model"], r["rules_total"],
                 r["manual_total"] if r["manual_total"] is not None else "—",
                 ("%d/4" % r["agree"]) if r["agree"] is not None else "—"))

    if args.diff:
        print("\n不一致的条目：")
        for r in rows:
            for cid, v in r["per"].items():
                if MANUAL.get(r["model"], {}).get(cid) != v["score"]:
                    print("  %-22s %s 规则=%d 人工=%d｜%s"
                          % (r["model"], cid, v["score"],
                             MANUAL[r["model"]][cid], (v["clarify"] or "[硬猜]")[:52]))

    cloud = [r for r in rows if not r["model"].endswith(("8b", "14b", "9b", "12b"))
             and ":" not in r["model"]]
    local = [r for r in rows if ":" in r["model"]]
    for name, grp in (("云端", cloud), ("本地", local)):
        if grp:
            vals = [r["manual_total"] for r in grp if r["manual_total"] is not None]
            print("%s %d 个：人工分均值 %.1f / 8，最高 %d，最低 %d"
                  % (name, len(vals), sum(vals) / len(vals), max(vals), min(vals)))
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"aspects": ASPECTS, "manual": MANUAL, "rows": rows},
                  fh, ensure_ascii=False, indent=1)
    print("\n→ %s" % os.path.relpath(OUT, ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
