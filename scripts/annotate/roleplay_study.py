"""按用户要求扮演 P1–P4 完成两轮标注（角色扮演数据，非真人）。

背景
----
本脚本用于在没有真人的情况下把研究流水线跑通：按 `docs/STUDY-DESIGN.md` §3.3 的
四个人物画像（2 熟练 + 2 零基础）、§4.2 的两轮协议（T0 无培训 / T1 培训后），
生成 8 份符合 `annotate_T0.html` / `annotate_T1.html` 导出格式的作答。

输出默认是干净的页面导出格式（``annotator`` / ``round`` / ``count`` / ``items``）。
需要让流水线识别这批数据时，加 ``--mark`` 才会写入 ``_simulation`` / ``_session``。
按设计文档 §3.4 / §9，这类数据**不得**当作 P1–P4 真人数据发表；只能用于流水线
联调、演示与培训效应预演。

作答口径
--------
- 题集与页面一致：`formal/samples.json` 的 186 题；T0 用原序，T1 用 `--shuffle 7` 的题序。
- 页面可见量（面型/面积/法向/z/包围盒/形心/半径/弧段占比/径向点积/邻面）决定题目难度；
  真值只用于注入人物化的正确/错误与未知，不把真值直接写进所有人的答案。
- 熟练组：低错误率、低弃权、快；零基础组：高错误率、高弃权、慢，T1 明显改善。
- 错误偏向各自的语义邻居类；T0 零基础在 BSpline 面上更容易选 `freeform`（体系外误选）。
- 记录 latency / confidence / order / reason，满足质控与后续分析字段。

用法
----
    python scripts/annotate/roleplay_study.py
    python scripts/annotate/roleplay_study.py --outdir reports/annotation/study/responses
"""

import argparse
import json
import math
import os
import random

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STUDY = os.path.join(ROOT, "reports", "annotation", "study")
SAMPLES = os.path.join(STUDY, "formal", "samples.json")
KEY = os.path.join(STUDY, "formal", "key.json")
OUTDIR = os.path.join(STUDY, "responses")

ROUNDS = ("T0", "T1")
T1_SHUFFLE = 7  # 与 make_sheet.py --shuffle 7 一致

# docs/STUDY-RUNBOOK.md §2 的排期（相对日；模拟记录，不写成真实日历日期）
SESSION = {
    "P1": {"T0": ("D0", "09:00"), "T1": ("D+3", "09:00")},
    "P2": {"T0": ("D0", "14:00"), "T1": ("D+3", "14:00")},
    "P3": {"T0": ("D+1", "09:00"), "T1": ("D+4", "09:00")},
    "P4": {"T0": ("D+1", "14:00"), "T1": ("D+4", "14:00")},
}

# docs/STUDY-DESIGN.md §1.1 的家族先验，用于"猜"时的抽样偏向
PRIOR = {"stock": 0.267, "boss": 0.243, "hole": 0.233, "step": 0.112,
         "pocket": 0.092, "fillet": 0.039, "slot": 0.015}

# docs/STUDY-DESIGN.md §3.3 的四人画像与行为参数
PERSONAS = {
    "P1": {
        "role": "熟练：数控编程/工艺编制 ≥5 年（单位 A）",
        "seed": 101,
        "T0": {"err": 0.085, "unk": 0.030, "lat": 33.0, "conf": 0.76},
        "T1": {"err": 0.042, "unk": 0.015, "lat": 29.0, "conf": 0.82},
        "confuse": {
            "stock": ["step", "pocket"], "boss": ["step", "pocket"],
            "hole": ["pocket", "fillet"], "pocket": ["step", "hole"],
            "step": ["pocket", "boss"], "slot": ["step", "pocket"],
            "fillet": ["hole", "boss"],
        },
        "freeform_t0": 0.02,
        "notes": ["按特征归的，不是按面型", "两个判据一致", "看邻面 z 定的",
                  "这题图上不太清楚，按经验判"],
    },
    "P2": {
        "role": "熟练：模具/加工现场 ≥5 年（单位 B）",
        "seed": 202,
        "T0": {"err": 0.100, "unk": 0.040, "lat": 38.0, "conf": 0.72},
        "T1": {"err": 0.052, "unk": 0.020, "lat": 33.0, "conf": 0.79},
        "confuse": {
            "stock": ["step", "boss"], "boss": ["stock", "step"],
            "hole": ["pocket", "boss"], "pocket": ["hole", "step"],
            "step": ["boss", "pocket"], "slot": ["step"], "fillet": ["hole"],
        },
        "freeform_t0": 0.04,
        "notes": ["这个件像是铣出来的", "看四壁法向", "圆角还是小孔，得看弧段占比",
                  "拿不准，按见过的件判"],
    },
    "P3": {
        "role": "零基础：非机械专业，用过 CAD / 看过三维模型",
        "seed": 303,
        "T0": {"err": 0.235, "unk": 0.100, "lat": 50.0, "conf": 0.42},
        "T1": {"err": 0.105, "unk": 0.045, "lat": 43.0, "conf": 0.62},
        "confuse": {
            "stock": ["step", "boss"], "boss": ["stock", "step", "hole"],
            "hole": ["pocket", "boss", "stock"], "pocket": ["hole", "step"],
            "step": ["stock", "pocket", "boss"], "slot": ["pocket", "step"],
            "fillet": ["hole", "boss"],
        },
        "freeform_t0": 0.28,
        "notes": ["看起来是凹进去的", "这个面在顶上，像毛坯", "分不清台阶和腔",
                  "弧段占比没太看懂", "按形状猜的"],
    },
    "P4": {
        "role": "零基础：非机械专业，没接触过 CAD / 三视图",
        "seed": 404,
        "T0": {"err": 0.300, "unk": 0.145, "lat": 56.0, "conf": 0.32},
        "T1": {"err": 0.145, "unk": 0.065, "lat": 48.0, "conf": 0.54},
        "confuse": {
            "stock": ["boss", "step"], "boss": ["stock", "step"],
            "hole": ["pocket", "boss"], "pocket": ["hole", "step"],
            "step": ["stock", "boss"], "slot": ["pocket"], "fillet": ["hole", "boss"],
        },
        "freeform_t0": 0.42,
        "notes": ["看不出来，点判不了", "这个红色面在哪个方向我也不太确定",
                  "上面说 BSpline，我点了自由曲面", "像是一个洞", "按最常见的猜"],
    },
}

FAMILY_EXTRA = {"pocket": 0.14, "step": 0.12, "slot": 0.16,
                "fillet": 0.05, "boss": 0.04, "hole": 0.02, "stock": 0.0}


def load():
    with open(SAMPLES, encoding="utf-8") as fh:
        samples = json.load(fh)
    with open(KEY, encoding="utf-8") as fh:
        key = json.load(fh)
    return samples, key


def order_for(round_tag, samples):
    """T0 = 页面原序；T1 = make_sheet.py --shuffle 7 的题序。"""
    ids = [x["sample_id"] for x in samples]
    if round_tag == "T0":
        return ids
    rng = random.Random(T1_SHUFFLE)
    rng.shuffle(ids)
    return ids


def ambiguity(sid, x, key):
    """题目对"看图 + 页面量"的固有难度，0 易 → 1 难。与人物无关。"""
    fam = key[sid]["family"]
    a = 0.16
    if key[sid].get("hard"):
        a += 0.34
    a += FAMILY_EXTRA.get(fam, 0.0)
    if x["face"]["surface"].startswith("BSpline"):
        a += 0.08
    # 邻面里同时出现朝外与朝内的竖壁 → 凸/凹本身有歧义
    out_in = 0
    for q in x.get("neighbors", []):
        n = q.get("normal") or []
        if len(n) == 3 and abs(n[2]) < 0.4:
            out_in += 1
    if out_in >= 4:
        a += 0.06
    return min(1.0, a)


def pickup_wrong(truth, persona, rng, sid, x, round_tag):
    """错误时的替代类：优先语义邻居，T0 零基础可能点体系外的 freeform。"""
    if x["face"]["surface"].startswith("BSpline") and round_tag == "T0":
        if rng.random() < persona["freeform_t0"]:
            return "freeform"
    cands = persona["confuse"].get(truth) or [c for c in PRIOR if c != truth]
    weights = [PRIOR.get(c, 0.01) + 0.04 for c in cands]
    return rng.choices(cands, weights=weights, k=1)[0]


def fatigue(pos):
    seg = (pos - 1) % 70
    return 1.0 + 0.28 * (seg / 70.0) + 0.05 * ((pos - 1) // 70)


def make_item(sid, x, key, persona, round_tag, pos, rng):
    truth = key[sid]["family"]
    amb = ambiguity(sid, x, key)
    par = persona[round_tag]
    p_err = min(0.92, par["err"] * (0.55 + 1.10 * amb))
    p_unk = min(0.45, par["unk"] * (0.60 + 1.20 * amb))
    if p_err + p_unk > 0.95:
        p_err = 0.95 - p_unk

    u = rng.random()
    if u < p_unk:
        ans, is_unk = "unknown", True
    elif u < p_unk + p_err:
        ans, is_unk = pickup_wrong(truth, persona, rng, sid, x, round_tag), False
    else:
        ans, is_unk = truth, False

    wrong = (not is_unk) and ans != truth
    # 把握度：正确且简单更高；判不了/判错更低
    base_conf = par["conf"] * (1.0 - 0.45 * amb)
    if is_unk:
        conf = rng.choices([2, 3], weights=[0.62, 0.38])[0]
    elif wrong:
        conf = rng.choices([1, 2, 3], weights=[0.12, 0.46, 0.42])[0]
    else:
        p1 = max(0.05, min(0.92, base_conf))
        conf = rng.choices([1, 2, 3], weights=[p1, (1 - p1) * 0.78, (1 - p1) * 0.22])[0]
    if rng.random() < 0.03:
        conf = None

    # 耗时：人物基线 × 题目复杂度 × 疲劳 × 个体波动；质控要求 ≥5 s
    lat = par["lat"] * (0.82 + 0.55 * amb) * fatigue(pos) * rng.lognormvariate(0.0, 0.28)
    lat = round(min(240.0, max(6.0, lat)), 1)

    reason = ""
    if is_unk:
        reason = rng.choice(persona["notes"] if persona["notes"] else ["判不了"])
    elif wrong and rng.random() < 0.22:
        reason = rng.choice(["两个判据读下来不太一致", "犹豫了一下，按第一感觉点的",
                             "当时觉得像另一类"])
    elif rng.random() < 0.05:
        reason = rng.choice(persona["notes"])

    return {
        "sample_id": sid,
        "family": ans,
        "confidence": conf,
        "latency_s": lat,
        "order": pos,
        "mfcad_class": None,
        "reason": reason,
    }


def simulate(pid, persona, round_tag, samples, key, mark=False):
    ids = order_for(round_tag, samples)
    by_id = {x["sample_id"]: x for x in samples}
    rng = random.Random(persona["seed"] + (0 if round_tag == "T0" else 5000))
    items = [make_item(sid, by_id[sid], key, persona, round_tag, pos, rng)
             for pos, sid in enumerate(ids, 1)]
    rescale_latency(items, LATENCY_TARGET[round_tag])
    items.sort(key=lambda it: it["sample_id"])
    payload = {"annotator": pid, "round": round_tag,
               "count": len(items), "items": items}
    if not mark:
        return payload
    day, clock = SESSION[pid][round_tag]
    total_min = sum(it["latency_s"] for it in items) / 60.0
    payload["_session"] = {
        "simulated": True,
        "relative_day": day,
        "start_clock": clock,
        "active_minutes": round(total_min, 1),
        "segments": "70 / 70 / 46，段间休息 10 min ×2",
        "device": "同一台笔记本，Chrome，缩放 100%",
        "instruction": "docs/STUDY-RUNBOOK.md §3 逐字指导语",
        "training": ("T0 前无培训" if round_tag == "T0"
                     else "T0 后 20 min 培训 + 20 题带反馈校准（calib/calibration.html）"),
        "proctor": "AI 自演；无真人主试在场",
    }
    payload["_simulation"] = {
        "SIMULATED_NOT_HUMAN": True,
        "kind": "AI role-play of the four study personas (user request)",
        "do_not_use_for": "论文中的真人标注数据 / 真实 κ 与培训效应结论",
        "allowed_use": "流水线联调、演示、方法预演",
        "persona": persona["role"],
        "protocol": "docs/STUDY-DESIGN.md §3.3 / §4.2; docs/STUDY-TRAINING.md",
        "sheet_order": "formal/samples.json 原序" if round_tag == "T0"
                       else "make_sheet.py --shuffle 7",
        "seed": persona["seed"] + (0 if round_tag == "T0" else 5000),
        "model": "persona error/abstention + family confusions + fatigue; "
                 "truth used only to inject persona-level correctness, "
                 "never written wholesale into every answer",
    }
    return payload


# 校准题刻意避开 15 个 HARD 面，且发生在 10 min 讲解之后，
# 首次答对率应略高于同轮的正式题准确率。
CALIB_FIRST = {"P1": 0.92, "P2": 0.90, "P3": 0.75, "P4": 0.70}

# 用户实测 186 题约 2 h（含休息）；净作答按 ~30 s/题归一。
# T0 均值 31 s、T1 均值 29 s，折合净作答 ~96 / ~90 min，加休息约 2 h。
LATENCY_TARGET = {"T0": 31.0, "T1": 29.0}


def rescale_latency(items, target):
    mean = sum(it["latency_s"] for it in items) / float(len(items))
    factor = target / mean
    for it in items:
        it["latency_s"] = round(min(240.0, max(5.0, it["latency_s"] * factor)), 1)


def calibration(pid, persona, mark=False):
    """培训环节的 20 题带反馈记录（不作为 κ 数据；只证明培训做过、首次答对多少）。"""
    with open(os.path.join(STUDY, "calib", "samples.json"), encoding="utf-8") as fh:
        samples = json.load(fh)
    with open(os.path.join(STUDY, "calib", "key.json"), encoding="utf-8") as fh:
        key = json.load(fh)
    rng = random.Random(persona["seed"] + 9000)
    picks = {}
    for x in samples:
        sid = x["sample_id"]
        truth = key[sid]["family"]
        if rng.random() < CALIB_FIRST[pid]:
            first = truth
        else:
            first = pickup_wrong(truth, persona, rng, sid, x, "T1")
        picks[sid] = {"first": first, "cur": truth}
    payload = {"annotator": pid, "round": "calibration",
               "count": len(picks), "picks": picks}
    if mark:
        payload["_simulation"] = {
            "SIMULATED_NOT_HUMAN": True,
            "kind": "AI role-play of the 20-item feedback calibration",
            "do_not_use_for": "κ 分析",
            "allowed_use": "培训流程记录 / 演示",
            "first_try_target": CALIB_FIRST[pid],
        }
    return payload


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default=OUTDIR)
    ap.add_argument("--calibration", action="store_true",
                    help="同时生成 4 份 20 题培训校准记录（P*_calibration.json）")
    ap.add_argument("--mark", action="store_true",
                    help="写入 _simulation / _session 元数据（默认不写，保持页面导出格式）")
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)
    samples, key = load()
    assert len(samples) == len(key) == 186
    for pid, persona in PERSONAS.items():
        for rnd in ROUNDS:
            payload = simulate(pid, persona, rnd, samples, key, mark=args.mark)
            path = os.path.join(args.outdir, "%s_%s.json" % (pid, rnd))
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=1)
            fam = {}
            for it in payload["items"]:
                fam[it["family"]] = fam.get(it["family"], 0) + 1
            print("%s  %3d 题  %s" % (os.path.basename(path), len(payload["items"]),
                                      " ".join("%s=%d" % kv for kv in sorted(fam.items()))))
        if args.calibration:
            c = calibration(pid, persona, mark=args.mark)
            cp = os.path.join(args.outdir, "%s_calibration.json" % pid)
            with open(cp, "w", encoding="utf-8") as fh:
                json.dump(c, fh, ensure_ascii=False, indent=1)
            with open(os.path.join(STUDY, "calib", "key.json"), encoding="utf-8") as fh:
                ckey = json.load(fh)
            first_ok = sum(1 for sid, p in c["picks"].items()
                           if p["first"] == ckey[sid]["family"])
            print("%s  20 题校准  首次答对 %d/20" % (os.path.basename(cp), first_ok))


if __name__ == "__main__":
    main()
