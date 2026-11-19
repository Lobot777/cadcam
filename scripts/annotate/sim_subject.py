"""模拟一名受过培训的正常受试者完成盲标页（研究用「试标」数据，非真人数据）。

两种模拟口径
------------
``--mode calibrated``（默认，交付口径）：与 ``scripts/annotate/sim_power.py`` 同口径，
  以答案键为真值，按 ``docs/STUDY-DESIGN.md`` §4.3 预设的培训后效应量
  （错误率约 10%、「判不了」约 4%、15 个 HARD 面显著更难、错误偏向语义邻居类）
  生成一名典型受试者的作答。这是对「正常人培训后能考多少」的**统计估计**。

``--mode rule``（诊断口径）：不读任何答案键，只按手册三条判据 + 几何拓扑独立判读，
  用来量「手册 + 页面信息」本身的信息上界。两者之差即「图形直觉 / 工艺经验」
  必须补上的部分，也是本研究真正要测的东西。

模拟作答的共同部分：题序疲劳（每 70 题休息）、把握度、耗时、备注、低置信转
「判不了」。错误偏向手册第 2 页列出的五类典型错误与家族先验。

页面可见信息
------------
``formal/samples.json`` 里页面确实显示给受试者的字段（面型 / 面积 / 法向 /
z 范围 / 包围盒 / 形心 / 半径 / 弧段占比 / 径向点积 / 邻面的面型前 4 字符 +
面积 + z 范围）。判据 ③ 要求的「四壁法向朝内 / 朝外」页面不显示，真人靠两视角
渲染图读出，这里用 ``reports/annotation/geom`` 的边凸性与邻面法向作等价代理；
该目录只有几何，不含家族标签。任何时候都不使用 ``data/zenodo_rptu/labels``。

用途边界
--------
输出文件的 ``_simulation`` 字段明确标注为模拟。按 ``docs/STUDY-DESIGN.md`` §3.4
与 ``docs/STUDY-RECRUIT.md`` §1，AI/主试产出的数据**不得进入 κ 分析、不得充当 P1–P4
真人数据**，只能用于流水线验证、培训增益的先验估计和试标演示。

用法
----
  python scripts/annotate/sim_subject.py                       # 生成 000_T0.json（校准口径）
  python scripts/annotate/sim_subject.py --eval                # 对答案键出评估报告
  python scripts/annotate/sim_subject.py --mode rule --round T1  # 独立规则判读诊断
  python scripts/annotate/sim_subject.py --round T1            # 同一题集，T1 轮次标记
"""

import argparse
import json
import math
import os
import random
import statistics
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STUDY = os.path.join(ROOT, "reports", "annotation", "study")
SAMPLES = os.path.join(STUDY, "formal", "samples.json")
KEY = os.path.join(STUDY, "formal", "key.json")
GEOMDIR = os.path.join(ROOT, "reports", "annotation", "geom")
OUTDIR = os.path.join(STUDY, "responses")

# 手册 §1 九个类别；按钮上出现，但正式题库的标签只有 7 类（无 chamfer / freeform）
FAMILIES = ["stock", "hole", "pocket", "slot", "step", "boss",
            "fillet", "chamfer", "freeform"]

# 真实家族先验（STUDY-DESIGN.md §1.1，206 面普查口径），用于模拟"猜"时的偏向
PRIOR = {"stock": 0.267, "boss": 0.243, "hole": 0.233, "step": 0.112,
         "pocket": 0.092, "fillet": 0.039, "slot": 0.015}

# 手册第 2 页「五个最常见的错」+ 语义相邻类，作为错误时的候选
CONFUSE = {
    "stock":   ["step", "pocket", "hole"],
    "hole":    ["fillet", "pocket", "stock", "boss"],
    "pocket":  ["step", "boss", "hole", "slot"],
    "slot":    ["pocket", "step"],
    "step":    ["pocket", "stock", "boss"],
    "boss":    ["pocket", "fillet", "step"],
    "fillet":  ["hole", "boss"],
    "chamfer": ["fillet", "step", "boss"],
    "freeform": ["boss", "step", "fillet"],
    "unknown": ["pocket", "step", "boss", "stock"],
}

# 各可靠性档位的答题行为。err = 漏用/误用判据后选错类的概率；
# unk = 主动点「判不了」的概率（手册明确鼓励，且它本身是要统计的信息）
# 培训后（T1 口径）的错误率参照 STUDY-DESIGN.md §4.3 的 25% -> 10%，
# 这里按难度分层，使加权总错误率落在 10% 量级。
TIER = {
    "A": {"err": 0.030, "unk": 0.010, "lat": 24.0, "why": "判据直接给出"},
    "B": {"err": 0.115, "unk": 0.055, "lat": 39.0, "why": "需结合邻面 z 与面积对账"},
    "C": {"err": 0.265, "unk": 0.175, "lat": 55.0, "why": "页面信息不足，需凭图猜"},
}

# calibrated 口径的效应量：培训后（T1）基线错误率，来自 STUDY-DESIGN.md §4.3
CAL = {
    "err_base": 0.065, "err_hard": 0.220, "unk_base": 0.028, "unk_hard": 0.090,
    "lat_easy": 31.0, "lat_mid": 44.0, "lat_hard": 58.0,
}
# 分家族固有难度（pocket / step / slot 边界最易混，见手册「五个最常见的错」）
FAM_DIFF = {"pocket": 0.050, "step": 0.040, "slot": 0.100,
            "fillet": 0.020, "boss": 0.010, "hole": 0.000, "stock": 0.000}


# ---------------------------------------------------------------- 页面可见信息
def view(x):
    """严格重建 annotate_T0.html 上一张卡片能看到的量（邻面只取前 6 个、不含法向）。"""
    f = x["face"]
    bb = f["bbox_mm"]
    pb = x["part_bbox_mm"]
    r = f.get("params", {}).get("radius")
    h = bb[5] - bb[2]
    arc = f["area_mm2"] / (2 * math.pi * r * h) if r and h > 1e-6 else None
    nbrs = []
    for q in x["neighbors"][:6]:  # build_card 里 [:6]
        z = q.get("z_mm") or [q.get("centroid_z"), q.get("centroid_z")]
        nbrs.append({"surf": q["surface"][:4].lower(),
                     "area": q["area_mm2"], "z": (z[0], z[1])})
    return {
        "sid": x["sample_id"],
        "surf": f["surface"],
        "area": f["area_mm2"],
        "n": f["normal"],
        "z0": bb[2], "z1": bb[5],
        "x0": bb[0], "x1": bb[3], "y0": bb[1], "y1": bb[4],
        "cx": f["centroid_mm"][0], "cy": f["centroid_mm"][1],
        "r": r, "arc": arc, "rd": f.get("radial_dot"),
        "ztop": pb["max"][2], "zbot": pb["min"][2],
        "px0": pb["min"][0], "px1": pb["max"][0],
        "py0": pb["min"][1], "py1": pb["max"][1],
        "nbrs": nbrs,
    }


def _near(a, b, tol=0.6):
    return abs(a - b) <= tol


def load_visual(samples):
    """重建「看图通道」：从纯几何数据取邻面法向与边凸性。

    页面 build_card 不显示邻面法向，但手册判据 ③ 要求看「四壁法向朝内还是朝外」，
    真人是从两视角渲染图读出这一点的。这里用 ``reports/annotation/geom`` 里的
    edges[].convexity 与邻面 normal 作为等价的机器代理。该目录只有几何
    （bbox / faces / edges），**不含家族标签**；绝不使用 data/zenodo_rptu/labels。
    样本通过 (零件 bbox, 面数) + (面面积, 面 bbox) 唯一匹配回几何面。
    """
    geoms = {}
    for fn in os.listdir(GEOMDIR):
        if fn.endswith(".json"):
            with open(os.path.join(GEOMDIR, fn), encoding="utf-8") as fh:
                geoms[fn[:-5]] = json.load(fh)
    by_sig = defaultdict(list)
    for g in geoms.values():
        by_sig[(tuple(g["bbox"]["min"]), tuple(g["bbox"]["max"]),
                len(g["faces"]))].append(g)
    out = {}
    for x in samples:
        k = (tuple(x["part_bbox_mm"]["min"]), tuple(x["part_bbox_mm"]["max"]),
             x["part_face_count"])
        g = by_sig[k][0]
        f = x["face"]
        cand = [q for q in g["faces"]
                if abs(q["area_mm2"] - f["area_mm2"]) < 0.02
                and all(abs(a - b) < 0.02 for a, b in zip(q["bbox"], f["bbox_mm"]))]
        assert len(cand) == 1, "面匹配不唯一：%s" % x["sample_id"]
        fi = cand[0]["index"]
        fmap = {q["index"]: q for q in g["faces"]}
        es = [e for e in g["edges"] if fi in e["faces"]]
        nbr_ids = sorted({j for e in es for j in e["faces"] if j != fi})
        out[x["sample_id"]] = {
            "conv": sum(1 for e in es if e["convexity"] == "convex"),
            "conc": sum(1 for e in es if e["convexity"] == "concave"),
            "nbr": [fmap[j] for j in nbr_ids if j in fmap],
            "centroid": cand[0]["centroid"],
        }
    return out


def wall_polarity(vis):
    """相邻竖壁法向相对当前面中心的朝向：>0 朝外（凸台壁），<0 朝内（腔壁）。"""
    c = vis["centroid"]
    dots = []
    for q in vis["nbr"]:
        n = q["normal"]
        if abs(n[2]) < 0.4:
            u = [q["centroid"][0] - c[0], q["centroid"][1] - c[1]]
            L = math.hypot(u[0], u[1])
            if L > 1e-6:
                dots.append((n[0] * u[0] + n[1] * u[1]) / L)
    return (sum(1 for d in dots if d > 0.3), sum(1 for d in dots if d < -0.3), dots)


# ---------------------------------------------------------------- L1 + L2 判读
def judge(v, vis):
    """返回 (label, tier, basis)。label 可为 unknown；basis 是会写进备注/报告的判据说明。"""
    surf, n, arc, rd = v["surf"], v["n"], v["arc"], v["rd"]
    z0, z1, ztop, zbot = v["z0"], v["z1"], v["ztop"], v["zbot"]
    span_z = z1 - z0
    nbrs = v["nbrs"]
    plan = [q for q in nbrs if q["surf"].startswith("plan")]
    cyl = [q for q in nbrs if q["surf"].startswith("cyli")]
    # 邻面里的"更低的水平底面"（手册判据 ③ 与数据完整性一节）
    lower = [q for q in plan if q["z"][1] < z0 - 0.5 and abs(q["z"][1] - q["z"][0]) < 0.5]
    # 邻面里的竖壁：z 有跨度（页面显示为 z44-52 这类）
    walls = [q for q in plan if q["z"][1] - q["z"][0] > 1.0]
    # 与当前面同起点的水平肩面
    shoulder = [q for q in plan if _near(q["z"][0], z0)
                and abs(q["z"][1] - q["z"][0]) < 0.5 and q["area"] > 300]

    # ---- 自由曲面：手册第 1 页「BSpline / Bezier，没有恒定半径」
    if surf.startswith("BSpline") or surf.startswith("Bezier"):
        return "freeform", "A", "面型 BSpline，按九类表判自由曲面"

    # ---- 圆柱面：判据 ① 弧段占比 × ② 径向点积，联用
    if surf.startswith("Cylinder"):
        if arc is None or rd is None:
            return "unknown", "C", "圆柱面缺弧段占比或径向点积，判不了"
        if abs(arc - 0.25) < 0.06:
            if rd < -0.5:
                return "fillet", "A", "弧段占比 %.2f≈0.25 且径向点积 −1 → 圆角" % arc
            return "unknown", "C", "¼ 圆弧但极性不配套，判不了"
        if abs(arc - 1.00) < 0.08:
            if rd > 0.5:
                return "boss", "A", "弧段占比 %.2f≈1.00 且径向点积 +1 → 凸（销/凸台）" % arc
            if rd < -0.5:
                # 凹：手册要求再找"更低的底面"分孔 / 圆腔壁
                if lower:
                    return "hole", "B", "整圈圆柱 + 径向 −1 + 邻面有更低底面 → 孔壁"
                if cyl or span_z > 8:
                    return "pocket", "C", "整圈圆柱 + 径向 −1，邻面未见更低底面 → 疑圆腔壁"
                return "hole", "B", "整圈圆柱 + 径向 −1，按孔壁判"
        return "unknown", "C", "弧段占比 %.2f 介于 0.25 与 1.00 之间，判不了" % arc

    # ---- 平面
    if not surf.startswith("Plane"):
        return "unknown", "C", "面型未覆盖，判不了"

    nz = abs(n[2])
    on_outer_x = (_near(v["x0"], v["px0"], 0.8) and _near(v["x1"], v["px0"], 0.8)) or \
                 (_near(v["x0"], v["px1"], 0.8) and _near(v["x1"], v["px1"], 0.8))
    on_outer_y = (_near(v["y0"], v["py0"], 0.8) and _near(v["y1"], v["py0"], 0.8)) or \
                 (_near(v["y0"], v["py1"], 0.8) and _near(v["y1"], v["py1"], 0.8))

    # 判据 ③ 的「四壁法向朝内 / 朝外」：页面不显示，靠边凸性和邻面法向重建
    conv, conc = vis["conv"], vis["conc"]
    w_out, w_in, _ = wall_polarity(vis)
    full_cyl = [q for q in vis["nbr"] if q["surface"].startswith("Cylinder")]
    footprint = (v["px1"] - v["px0"]) * (v["py1"] - v["py0"])
    # 面积对账（手册辅助判据）：肩面 + 上层特征顶面 ≈ 整块顶面
    hi = [q for q in vis["nbr"] if q["surface"].startswith("Plane")
          and abs(q["normal"][2]) > 0.9 and q["bbox"][2] > z0 + 0.5]
    balanced = any(abs(v["area"] + q["area_mm2"] - footprint) < 0.12 * footprint
                   for q in hi)

    # 水平面
    if nz > 0.95:
        if _near(z0, zbot):
            return "stock", "A", "水平底面落在包围盒 z 极值，未被加工 → 毛坯原始面"
        if conc > conv and conc >= 2:
            # 内角围合：孔底 / 腔底 / 凸台周围肩面（手册例 3 vs 例 4）
            if full_cyl:
                return "hole", "B", "水平面与整圈圆柱壁相接且低于顶面 → 孔底（同归 hole）"
            if balanced or w_out > w_in:
                return "step", "B", "四壁法向朝外或面积对账成立 → 凸台周围肩面，不是腔"
            if w_in > w_out:
                return "pocket", "B", "四壁法向朝内、底面低于周围顶面 → 腔底"
            return "pocket", "C", "低于顶面且被立壁围合 → 疑腔底"
        if conv > conc:
            if _near(z0, ztop) and v["area"] > 0.55 * footprint:
                return "stock", "A", "水平顶面覆盖零件顶面且周围无立壁 → 原始面"
            if _near(z0, ztop):
                return "boss", "B", "顶面高度的小面 + 凸边 → 凸台顶面 / 销顶盖"
            return "step", "B", "内部高度 + 凸边 → 一级台阶肩面"
        if _near(z0, ztop):
            return "stock", "B", "顶面高度、边界凸凹混合 → 带特征的原始顶面"
        if full_cyl:
            return "hole", "B", "内部水平面与整圈圆柱壁相接 → 孔底"
        if balanced:
            return "step", "B", "面积对账成立 → 肩面"
        return "step", "C", "边界凸凹混合，按台阶肩面判"

    # 竖直面 / 斜壁
    if (on_outer_x or on_outer_y) and span_z > 0.6 * (ztop - zbot):
        return "stock", "A", "四周侧壁落在包围盒 x/y 极值且贯通大半个高度 → 原始面"
    sx, sy = v["x1"] - v["x0"], v["y1"] - v["y0"]
    if min(sx, sy) > 0 and min(sx, sy) < 0.22 * max(sx, sy) and span_z > 2:
        return "slot", "C", "细长竖壁、两侧平行 → 疑槽壁"
    if conc > conv and w_in >= w_out:
        return "pocket", "B", "内角占多数且壁法向朝内 → 腔壁"
    if conv > conc and w_out >= w_in:
        return "boss", "B", "凸边占多数且壁法向朝外 → 凸台四壁"
    if shoulder and _near(z1, ztop) and z0 > zbot + 2:
        return "boss", "B", "竖壁从肩面高度直达顶面 → 凸台四壁"
    if conc > conv:
        return "pocket", "C", "内角占多数，凭图判腔壁"
    if _near(z1, ztop) and z0 > zbot + 2:
        return "boss", "C", "短竖壁贴顶面，凭图判凸台壁"
    if span_z > 0.3 * (ztop - zbot):
        return "step", "C", "大高度差竖壁，按台阶配套壁判"
    return "unknown", "C", "特征竖壁，边界凸凹混合，判不了"


# ---------------------------------------------------------------- L3 人类作答
def fatigue(order, total):
    """每 70 题强制休息；段内缓慢累积疲劳，段首回落（STUDY-DESIGN.md §1.3 / §8）。"""
    seg_pos = (order - 1) % 70
    return 1.0 + 0.30 * (seg_pos / 70.0) + 0.06 * ((order - 1) // 70)


def pick_wrong(label, rng):
    cands = CONFUSE.get(label, ["stock", "boss", "hole"])
    weights = [PRIOR.get(c, 0.01) + 0.05 for c in cands]
    return rng.choices(cands, weights=weights, k=1)[0]


def pick_confidence(tier, wrong, is_unk, rng):
    if is_unk:
        return rng.choices([2, 3], weights=[0.6, 0.4])[0]
    if wrong:
        return rng.choices([1, 2, 3], weights=[0.25, 0.45, 0.30])[0]
    if tier == "A":
        return rng.choices([1, 2], weights=[0.86, 0.14])[0]
    if tier == "B":
        return rng.choices([1, 2, 3], weights=[0.50, 0.42, 0.08])[0]
    return rng.choices([1, 2, 3], weights=[0.14, 0.50, 0.36])[0]


def pick_latency(base, conf, is_unk, order, total, rng):
    if conf == 2:
        base *= 1.22
    elif conf == 3:
        base *= 0.72           # "猜的"通常点得快
    if is_unk:
        base *= 1.15
    val = base * fatigue(order, total) * rng.lognormvariate(0.0, 0.34)
    return round(min(180.0, max(6.0, val)), 1)  # 质控：不得出现 <5 s 的乱点


def note_for(label, answer, basis, is_unk, rng):
    if is_unk:
        pool = ["邻面只给了 z 范围，看不出四壁法向，判不了",
                "圆柱弧段占比不是 0.25 也不是 1.00，手册说拿不准就判不了",
                "图上凸凹看不清，不敢猜",
                "这个面看着像腔底又像肩面，面积对不上"]
        return rng.choice(pool)
    if answer != label:
        return rng.choice(["这题后来有点犹豫", "两个判据读下来不太一致", "按第一感觉点了"])
    if rng.random() < 0.06:
        return basis
    return ""


def simulate(round_tag, seed, annotator, mode):
    with open(SAMPLES, encoding="utf-8") as fh:
        samples = json.load(fh)
    key = None
    if mode == "calibrated":
        with open(KEY, encoding="utf-8") as fh:   # 统计估计口径：真值 + 效应量
            key = json.load(fh)
    rng = random.Random(seed)
    visual = load_visual(samples)
    total = len(samples)
    items, trace = [], []
    for order, x in enumerate(samples, 1):
        v = view(x)
        rule_label, tier, basis = judge(v, visual[v["sid"]])
        f = fatigue(order, total)
        if mode == "calibrated":
            truth = key[v["sid"]]["family"]
            hard = bool(key[v["sid"]]["hard"])
            diff = FAM_DIFF.get(truth, 0.0)
            p_err = (CAL["err_base"] + (CAL["err_hard"] if hard else 0) + diff) * f
            p_unk = (CAL["unk_base"] + (CAL["unk_hard"] if hard else 0)
                     + 0.5 * diff) * (1.0 + 0.25 * (f - 1.0))
            lat_base = (CAL["lat_hard"] if hard else
                        (CAL["lat_mid"] if diff > 0 else CAL["lat_easy"]))
            behav = "C" if hard else ("B" if diff > 0 else "A")
        else:
            truth = rule_label
            hard = (tier == "C")
            p_err = TIER[tier]["err"] * f
            p_unk = TIER[tier]["unk"] * (1.0 + 0.25 * (f - 1.0))
            lat_base = TIER[tier]["lat"]
            behav = tier
        u = rng.random()
        if truth == "unknown":
            # 规则就判不了：多数人照手册点「判不了」，少数按图硬猜
            if rng.random() < 0.62:
                answer, is_unk = "unknown", True
            else:
                answer, is_unk = rng.choices(list(PRIOR), weights=list(PRIOR.values()))[0], False
        elif u < p_unk:
            answer, is_unk = "unknown", True
        elif u < p_unk + p_err:
            answer, is_unk = pick_wrong(truth, rng), False
        else:
            answer, is_unk = truth, False
        wrong = (not is_unk) and answer != truth
        conf = pick_confidence(behav, wrong, is_unk, rng)
        lat = pick_latency(lat_base, conf, is_unk, order, total, rng)
        reason = note_for(truth, answer, basis, is_unk, rng)
        items.append({
            "sample_id": v["sid"], "family": answer,
            "confidence": conf if rng.random() < 0.93 else None,
            "latency_s": lat, "order": order,
            "mfcad_class": None, "reason": reason,
        })
        trace.append({"sample_id": v["sid"], "rule_label": rule_label,
                      "tier": tier, "basis": basis})

    if mode == "calibrated":
        source = ("统计估计：以 formal/key.json 真值为基准，按 STUDY-DESIGN.md §4.3 "
                  "培训后效应量注入错误/判不了；规则判定仅用于难度与耗时分层")
    else:
        source = ("独立判读：仅 formal/samples.json 页面可见量 + geom 纯几何拓扑，"
                  "未读 key.json 与 labels")
    payload = {
        "annotator": annotator,
        "round": round_tag,
        "count": len(items),
        "items": items,
        "_simulation": {
            "SIMULATED_NOT_HUMAN": True,
            "do_not_use_for": "kappa 分析 / 论文真人数据 / P1-P4 汇总",
            "allowed_use": "流水线验证、试标演示、培训增益先验估计",
            "protocol": "docs/STUDY-TRAINING.md（20 min 讲解 + 20 题带反馈校准）",
            "sheet": "reports/annotation/study/formal/annotate_T0.html（--no-tips 页面）",
            "mode": mode,
            "answer_source": source,
            "model": "L1 手册判据 + L2 邻面 z / 边凸性 / 面积对账结构重建 + L3 人类噪声与疲劳",
            "seed": seed,
            "calibrated_effect_size": CAL if mode == "calibrated" else None,
            "family_difficulty": FAM_DIFF if mode == "calibrated" else None,
            "tier_error_rates": {k: {"err": d["err"], "unk": d["unk"]}
                                 for k, d in TIER.items()},
        },
    }
    return payload, trace


# ---------------------------------------------------------------- 评估（只读此处）
def evaluate(path, trace):
    with open(path, encoding="utf-8") as fh:
        sub = json.load(fh)
    with open(KEY, encoding="utf-8") as fh:   # 仅评估分支读取答案键
        key = json.load(fh)
    got = {i["sample_id"]: i for i in sub["items"]}
    tr = {t["sample_id"]: t for t in trace}
    ids = sorted(key)
    rows = []
    for sid in ids:
        truth = key[sid]["family"]
        ans = got[sid]["family"]
        rows.append({"sid": sid, "truth": truth, "ans": ans,
                     "hard": bool(key[sid]["hard"]),
                     "tier": tr[sid]["tier"], "rule": tr[sid]["rule_label"],
                     "conf": got[sid]["confidence"], "lat": got[sid]["latency_s"]})

    n = len(rows)
    ok = sum(1 for r in rows if r["ans"] == r["truth"])
    unk = sum(1 for r in rows if r["ans"] == "unknown")
    wrong = n - ok - unk
    rule_ok = sum(1 for r in rows if r["rule"] == r["truth"])
    rule_unk = sum(1 for r in rows if r["rule"] == "unknown")

    def block(sel):
        b = [r for r in rows if sel(r)]
        return {"n": len(b),
                "accuracy": round(sum(1 for r in b if r["ans"] == r["truth"]) / len(b), 4) if b else None,
                "unknown_rate": round(sum(1 for r in b if r["ans"] == "unknown") / len(b), 4) if b else None}

    conf = defaultdict(lambda: {"n": 0, "ok": 0})
    for r in rows:
        c = r["conf"] if r["conf"] in (1, 2, 3) else 0
        conf[c]["n"] += 1
        conf[c]["ok"] += r["ans"] == r["truth"]

    confusion = defaultdict(Counter)
    for r in rows:
        confusion[r["truth"]][r["ans"]] += 1

    by_order = []
    for i in range(3):
        b = [r for r in rows if i * 62 < (got[r["sid"]]["order"]) <= (i + 1) * 62]
        by_order.append({"segment": i + 1, "n": len(b),
                         "accuracy": round(sum(1 for r in b if r["ans"] == r["truth"]) / len(b), 4),
                         "median_latency_s": round(statistics.median(r["lat"] for r in b), 1),
                         "unknown_rate": round(sum(1 for r in b if r["ans"] == "unknown") / len(b), 4)})

    lats = [r["lat"] for r in rows]
    # 手册与题库标签体系的错位诊断（只读 samples + key，不参与作答生成）
    with open(SAMPLES, encoding="utf-8") as fh:
        all_samples = json.load(fh)
    bs = [x["sample_id"] for x in all_samples
          if x["face"]["surface"].startswith(("BSpline", "Bezier"))]
    out = {
        "file": os.path.basename(path),
        "mode": sub.get("_simulation", {}).get("mode"),
        "n": n,
        "completed": len(got),
        "correct": ok, "wrong": wrong, "unknown": unk,
        "accuracy_including_unknown": round(ok / n, 4),
        "accuracy_of_committed": round(ok / (n - unk), 4) if n > unk else None,
        "unknown_rate": round(unk / n, 4),
        "error_rate": round(wrong / n, 4),
        "ideal_rule": {"correct": rule_ok, "accuracy": round(rule_ok / n, 4),
                       "abstain": rule_unk,
                       "accuracy_when_committed": round(rule_ok / (n - rule_unk), 4)},
        "rule_vs_human_gap": round(ok / n - rule_ok / n, 4),
        "design_expectation": {
            "source": "docs/STUDY-DESIGN.md §4.3（培训后错误率 25% -> 10%）",
            "target_error_rate": 0.10,
            "observed_error_rate": round(wrong / n, 4),
            "note": "判不了按设计文档 §6 单独统计，不并入错误率",
        },
        "by_tier": {t: block(lambda r, t=t: r["tier"] == t) for t in "ABC"},
        "hard_subset": block(lambda r: r["hard"]),
        "easy_subset": block(lambda r: not r["hard"]),
        "by_family": {fam: block(lambda r, fam=fam: r["truth"] == fam)
                      for fam in sorted(set(r["truth"] for r in rows))},
        "confidence_calibration": {str(c): {"n": d["n"],
                                            "accuracy": round(d["ok"] / d["n"], 4) if d["n"] else None}
                                   for c, d in sorted(conf.items())},
        "by_order_third": by_order,
        "latency_s": {"min": min(lats), "median": round(statistics.median(lats), 1),
                      "mean": round(statistics.mean(lats), 1), "max": max(lats),
                      "total_min": round(sum(lats) / 60.0, 1),
                      "under_5s": sum(1 for v in lats if v < 5)},
        "confusion": {k: dict(v) for k, v in sorted(confusion.items())},
        "answer_distribution": dict(Counter(r["ans"] for r in rows)),
        "rule_distribution": dict(Counter(r["rule"] for r in rows)),
        "manual_key_mismatch": {
            "issue": "手册九类表把 BSpline/Bezier 定义为 freeform，"
                     "但正式题库标签只有 7 类，不含 freeform / chamfer",
            "bspline_faces": bs,
            "key_families": sorted({key[q]["family"] for q in bs}),
            "rule_judgement": "freeform",
            "implication": "严格按面型判，这 %d 题必错；按手册第 1 页"
                           "「同一特征的所有面归同一类」才判得对。"
                           "两条规则在此冲突，建议培训时补一句说明。" % len(bs),
        },
        "disagreements": [{"sample_id": r["sid"], "truth": r["truth"], "ans": r["ans"],
                           "tier": r["tier"], "rule": r["rule"], "hard": r["hard"]}
                          for r in rows if r["ans"] != r["truth"]],
    }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", default="T0")
    ap.add_argument("--annotator", default="000")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--mode", choices=["calibrated", "rule"], default="calibrated",
                    help="calibrated=真值+效应量的统计估计；rule=不读答案键的独立判读诊断")
    ap.add_argument("--eval", action="store_true", help="对答案键出评估报告")
    ap.add_argument("--outdir", default=OUTDIR, help="输出目录（流水线演练用）")
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)
    path = os.path.join(args.outdir, "%s_%s.json" % (args.annotator, args.round))
    if args.eval:
        # 评估时用文件自身的 mode 与同一 seed 重放，保证 trace 与作答文件一致
        with open(path, encoding="utf-8") as fh:
            mode = json.load(fh).get("_simulation", {}).get("mode", args.mode)
        payload, trace = simulate(args.round, args.seed, args.annotator, mode)
        report = evaluate(path, trace)
        ep = os.path.join(args.outdir, "%s_%s_eval.json" % (args.annotator, args.round))
        with open(ep, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1)
        print("n=%d  正确=%d  错=%d  判不了=%d  准确率=%.3f  判不了率=%.3f"
              % (report["n"], report["correct"], report["wrong"], report["unknown"],
                 report["accuracy_including_unknown"], report["unknown_rate"]))
        print("理想规则: 正确率=%.3f 弃权=%d" % (report["ideal_rule"]["accuracy"],
                                              report["ideal_rule"]["abstain"]))
        print("hard 子集: %s" % report["hard_subset"])
        print("耗时中位数=%.1fs 总计=%.1fmin <5s 题数=%d"
              % (report["latency_s"]["median"], report["latency_s"]["total_min"],
                 report["latency_s"]["under_5s"]))
        print("写出", ep)
    else:
        payload, _ = simulate(args.round, args.seed, args.annotator, args.mode)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        print("写出 %s  (%d 题, 受试者 %s, 轮次 %s)"
              % (path, payload["count"], payload["annotator"], payload["round"]))


if __name__ == "__main__":
    main()
