"""正式研究的汇总分析：4 名受试者 × 2 轮 → κ 表 + 培训增益 + 质控报告。

与 kappa.py 的区别：那个只处理「一个标注文件 vs 30 题答案键」；
这个处理 8 份真人作答、算两两 Cohen κ、题级 bootstrap CI、分家族 / 难题子集、
培训前后 McNemar、体系外误选率，以及「纯规则 AI 基线」与人的一致性。

用法：
  python scripts/annotate/kappa_study.py                      # 收齐后跑
  python scripts/annotate/kappa_study.py --responses <dir>    # 指定作答目录
  python scripts/annotate/kappa_study.py --boot 500           # 快速预演

自动跳过：*_eval.json、带 _simulation 标记的演练数据、编号 000 的模拟受试者。
需要强制纳入演练数据做流水线验证时加 --include-simulation。
产物：reports/annotation/study/results/{summary.json, REPORT.md, ai_rule.json}
"""

import argparse
import itertools
import json
import math
import os
import random
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

ROOT = os.path.dirname(os.path.dirname(HERE))
STUDY = os.path.join(ROOT, "reports", "annotation", "study")
GEOM = os.path.join(ROOT, "reports", "annotation", "geom")
RESP = os.path.join(STUDY, "responses")
OUT = os.path.join(STUDY, "results")
KEY = os.path.join(STUDY, "formal", "key.json")

OUT_OF_TAXONOMY = {"freeform", "chamfer"}   # 通用九类里有、本批答案键里没有
ROUNDS = ("T0", "T1")


def kappa(a, b, labels=None):
    n = len(a)
    if n == 0:
        return None, 0.0, 0.0
    if labels is None:
        labels = sorted(set(a) | set(b))
    po = sum(1 for x, y in zip(a, b) if x == y) / float(n)
    ca, cb = Counter(a), Counter(b)
    pe = sum((ca.get(k, 0) / float(n)) * (cb.get(k, 0) / float(n)) for k in labels)
    if abs(1.0 - pe) < 1e-12:
        return 1.0, po, pe
    return (po - pe) / (1.0 - pe), po, pe


def boot_pairs(pairs, rounds=5000, seed=7):
    """以题为单位重采样，返回所有配对平均 κ 的 95% CI。"""
    n = len(pairs[0][1][0])   # pairs[0] = (name, (vec_a, vec_b))；取题数，别取到名字长度
    rng = random.Random(seed)
    vals = []
    labels_all = sorted({v for _, arrs in pairs for arr in arrs for v in arr})
    for _ in range(rounds):
        idx = [rng.randrange(n) for _ in range(n)]
        tot = 0.0
        for _, (a, b) in pairs:
            aa, bb = [a[i] for i in idx], [b[i] for i in idx]
            k, _, _ = kappa(aa, bb, labels_all)
            tot += k
        vals.append(tot / len(pairs))
    vals.sort()
    return vals[int(0.025 * rounds)], vals[int(0.975 * rounds) - 1]


def load_responses(directory, include_sim=False):
    out = {}
    if not os.path.isdir(directory):
        return out
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json") or name.endswith("_eval.json"):
            continue
        if "_T" not in name:
            continue
        with open(os.path.join(directory, name), encoding="utf-8") as fh:
            d = json.load(fh)
        sim = bool(d.get("_simulation"))
        pid = str(d.get("annotator", name.split("_")[0]))
        rnd = d.get("round") or ("T1" if "_T1" in name else "T0")
        # 000 是流水线演练用的模拟受试者，即使 --include-simulation 也不混进 P1–P4
        if pid == "000":
            continue
        if sim and not include_sim:
            continue
        if rnd not in ROUNDS:
            continue
        items = {x["sample_id"]: x for x in d.get("items", []) if x.get("family")}
        out[(pid, rnd)] = {"items": items, "sim": sim, "file": name}
    return out


def aligned(responses, key_ids, rnd, mode="all"):
    """取出某一轮所有受试者在同一批题上的标签向量。"""
    people = sorted({p for (p, r) in responses if r == rnd})
    ids = list(key_ids)
    if mode == "committed":
        ids = [s for s in ids
               if all(responses[(p, rnd)]["items"].get(s, {}).get("family") != "unknown"
                      and s in responses[(p, rnd)]["items"] for p in people)]
    vec = {p: [responses[(p, rnd)]["items"].get(s, {}).get("family", "MISSING")
               for s in ids] for p in people}
    return people, ids, vec


def rule_ai(key):
    """用 autolabel 规则 v2 生成一份独立的「纯算法标注者」标签。"""
    import autolabel
    per_part = {}
    for part in [str(i) for i in range(1, 11)]:
        with open(os.path.join(GEOM, "%s.json" % part), encoding="utf-8") as fh:
            geom = json.load(fh)
        labs, _ = autolabel.annotate(geom)
        per_part[part] = {int(k): v["family"] for k, v in labs.items()}
    return {sid: per_part[meta["part"]].get(meta["face"], "MISSING")
            for sid, meta in key.items()}


def _ranks(v):
    idx = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    i = 0
    while i < len(v):
        j = i
        while j + 1 < len(v) and v[idx[j + 1]] == v[idx[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            r[idx[k]] = avg
        i = j + 1
    return r


def spearman(a, b):
    """秩相关；错误指示是 0/1，必须用平均秩处理并列。"""
    n = len(a)
    if n < 3:
        return 0.0
    ra, rb = _ranks(a), _ranks(b)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    da = math.sqrt(sum((x - ma) ** 2 for x in ra))
    db = math.sqrt(sum((y - mb) ** 2 for y in rb))
    return num / (da * db) if da * db else 0.0


def describe(items, key, ids):
    fam = [items.get(s, {}).get("family", "MISSING") for s in ids]
    gold = [key[s]["family"] for s in ids]
    n = len(ids)
    ok = sum(1 for f, g in zip(fam, gold) if f == g)
    unk = sum(1 for f in fam if f == "unknown")
    oot = sum(1 for f in fam if f in OUT_OF_TAXONOMY)
    lat = [items[s]["latency_s"] for s in ids
           if s in items and items[s].get("latency_s") is not None]
    lat.sort()
    # 疲劳：作答顺序 vs 是否判错（unknown 不计入判错）
    seq = [(items[s]["order"],
            1 if items[s].get("family") not in (None, "unknown")
            and items[s].get("family") != key[s]["family"] else 0)
           for s in ids if s in items and items[s].get("order") is not None]
    rho = round(spearman([q[0] for q in seq], [q[1] for q in seq]), 3)
    completed = sum(1 for s in ids if s in items)
    med = round(lat[len(lat) // 2], 1) if lat else None
    return {
        "n": n, "completed": completed,
        "correct": ok, "accuracy": round(ok / float(n), 4) if n else None,
        "unknown": unk, "unknown_rate": round(unk / float(n), 4) if n else None,
        "out_of_taxonomy": oot,
        "out_of_taxonomy_rate": round(oot / float(n), 4) if n else None,
        "latency_median_s": med,
        "latency_total_min": round(sum(lat) / 60.0, 1) if lat else None,
        "under_5s": sum(1 for x in lat if x < 5),
        "fatigue_rho": rho,
        "qc_pass": completed / float(n) >= 0.9 and (med is None or med >= 8)
                   and sum(1 for x in lat if x < 5) == 0,
    }


def mcnemar(b, c):
    if b + c == 0:
        return 0.0
    return round((abs(b - c) - 1) ** 2 / float(b + c), 3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--responses", default=RESP)
    ap.add_argument("--key", default=KEY)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--boot", type=int, default=5000)
    ap.add_argument("--include-simulation", action="store_true")
    args = ap.parse_args()

    with open(args.key, encoding="utf-8") as fh:
        key = json.load(fh)
    ids = sorted(key)
    hard = [s for s in ids if key[s].get("hard")]
    easy = [s for s in ids if not key[s].get("hard")]
    resp = load_responses(args.responses, args.include_simulation)
    os.makedirs(args.out, exist_ok=True)

    report = {"key_n": len(ids), "files": {"%s_%s" % k: v["file"] for k, v in resp.items()},
              "simulation_included": args.include_simulation, "rounds": {}}

    ai = rule_ai(key)
    with open(os.path.join(args.out, "ai_rule.json"), "w", encoding="utf-8") as fh:
        json.dump({"note": "autolabel 规则 v2 的面级输出，作为独立算法基线；不是裁决后真值",
                   "labels": ai}, fh, ensure_ascii=False, indent=1)

    for rnd in ROUNDS:
        people = sorted({p for (p, r) in resp if r == rnd})
        blk = {"people": people, "per_person": {}, "pairwise": {}, "mean_kappa": None,
               "ci95": None, "committed_mean_kappa": None, "binary_mean_kappa": None,
               "by_family": {}, "hard_vs_easy": {}, "human_vs_gold": {}, "human_vs_rule": {}}
        for p in people:
            blk["per_person"][p] = describe(resp[(p, rnd)]["items"], key, ids)

        if len(people) >= 2:
            _, allids, vec = aligned(resp, ids, rnd)
            pairs = [(("%s|%s" % (a, b)), (vec[a], vec[b]))
                     for a, b in itertools.combinations(people, 2)]
            for name, (a, b) in pairs:
                k, po, pe = kappa(a, b)
                blk["pairwise"][name] = {"kappa": round(k, 4), "po": round(po, 4),
                                         "pe": round(pe, 4)}
            blk["mean_kappa"] = round(sum(v["kappa"] for v in blk["pairwise"].values())
                                      / len(pairs), 4)
            if args.boot >= 40:
                lo, hi = boot_pairs(pairs, rounds=args.boot)
                blk["ci95"] = [round(lo, 4), round(hi, 4)]

            # 仅作答题（剔除任一 unknown）
            _, cids, cvec = aligned(resp, ids, rnd, mode="committed")
            if cids:
                ck = [kappa(cvec[a], cvec[b])[0] for a, b in itertools.combinations(people, 2)]
                blk["committed_mean_kappa"] = round(sum(ck) / len(ck), 4)
                blk["committed_n"] = len(cids)

            # 二值：stock vs 非 stock
            bvec = {p: ["stock" if x == "stock" else "feature" for x in v]
                    for p, v in vec.items()}
            bk = [kappa(bvec[a], bvec[b])[0] for a, b in itertools.combinations(people, 2)]
            blk["binary_mean_kappa"] = round(sum(bk) / len(bk), 4)

            # 分家族（只报 >= 15 题）：一类对其余的二值 κ。
            # 不能只在单一家族的子集上算多类 κ——子集里几乎全是同一类，
            # pe 会趋近 po，κ 恒接近 0，指标退化。
            for fam in sorted({key[s]["family"] for s in ids}):
                sub = [i for i, s in enumerate(ids) if key[s]["family"] == fam]
                if len(sub) < 15:
                    continue
                ks = []
                for a, b in itertools.combinations(people, 2):
                    aa = ["yes" if x == fam else "other" for x in vec[a]]
                    bb = ["yes" if x == fam else "other" for x in vec[b]]
                    ks.append(kappa(aa, bb)[0])
                blk["by_family"][fam] = {"n": len(sub), "mean_kappa": round(sum(ks) / len(ks), 4),
                                         "mode": "one-vs-rest"}

            # 难题 / 常规
            for tag, sset in (("hard", hard), ("easy", easy)):
                sub = [i for i, s in enumerate(ids) if s in set(sset)]
                ks = [kappa([vec[a][i] for i in sub], [vec[b][i] for i in sub])[0]
                      for a, b in itertools.combinations(people, 2)]
                blk["hard_vs_easy"][tag] = {"n": len(sub), "mean_kappa": round(sum(ks) / len(ks), 4)}

        # 与真值 / 与规则基线（每人单独算，不需要两人）
        for p in people:
            it = resp[(p, rnd)]["items"]
            gold_v = [key[s]["family"] for s in ids]
            for tag, other in (("human_vs_gold", gold_v),
                               ("human_vs_rule", [ai[s] for s in ids])):
                v = [it.get(s, {}).get("family", "MISSING") for s in ids]
                k, po, _ = kappa(v, other)
                blk[tag][p] = {"kappa": round(k, 4), "agreement": round(po, 4)}
        report["rounds"][rnd] = blk

    # 培训前后（逐人 McNemar）
    gains = {}
    for p in sorted({p for (p, r) in resp if r == "T0"}):
        if (p, "T1") not in resp:
            continue
        a, b = resp[(p, "T0")]["items"], resp[(p, "T1")]["items"]
        up = down = 0
        for s in ids:
            g = key[s]["family"]
            c0 = a.get(s, {}).get("family") == g
            c1 = b.get(s, {}).get("family") == g
            if not c0 and c1:
                up += 1
            elif c0 and not c1:
                down += 1
        gains[p] = {
            "T0": report["rounds"]["T0"]["per_person"].get(p),
            "T1": report["rounds"]["T1"]["per_person"].get(p),
            "mcnemar_b": up, "mcnemar_c": down, "mcnemar_chi2": mcnemar(up, down),
            "accuracy_delta": (round(report["rounds"]["T1"]["per_person"][p]["accuracy"]
                                     - report["rounds"]["T0"]["per_person"][p]["accuracy"], 4)
                               if p in report["rounds"]["T1"]["per_person"] else None),
        }
    report["training_gain"] = gains

    with open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)

    # 人读报告
    lines = ["# 正式研究分析结果", "",
             "> 由 `scripts/annotate/kappa_study.py` 生成。真值 = `formal/key.json`（人工裁决后）；",
             "> `ai_rule.json` = autolabel 规则 v2 的**独立算法基线**，不是真值。",
             "> 编号 000 的演练数据默认排除。", ""]
    if args.include_simulation:
        lines += ["> **⚠️ 本报告基于模拟作答（非真人受试者），仅用于流水线联调与演示；"
                  "不得当作真人 κ 或论文结论。**", ""]
    rule_acc = sum(1 for s in ids if ai.get(s) == key[s]["family"]) / float(len(ids))
    lines += ["> 规则基线自检：`autolabel` v2 与真值逐题一致率 = %.3f。"
              "它是算法基线，不是真值。" % rule_acc, ""]
    for rnd in ROUNDS:
        b = report["rounds"][rnd]
        lines += ["## %s 轮（%d 人：%s）" % (rnd, len(b["people"]), ", ".join(b["people"]) or "—"), ""]
        if b["mean_kappa"] is None:
            lines += ["数据不足：同轮至少 2 人才能算两两 κ。当前只有单人描述统计。", ""]
        else:
            lines += ["| 指标 | 值 |", "|---|---|",
                      "| 平均两两 Cohen κ | %.3f%s |" % (
                          b["mean_kappa"],
                          "  95%% CI [%s, %s]" % tuple(b["ci95"]) if b["ci95"] else ""),
                      "| 仅作答题 κ | %s |" % b["committed_mean_kappa"],
                      "| 二值 stock/非 stock κ | %s |" % b["binary_mean_kappa"], ""]
            lines += ["| 配对 | κ | po | pe |", "|---|---|---|---|"]
            for name, v in b["pairwise"].items():
                lines.append("| %s | %.3f | %.3f | %.3f |" % (name, v["kappa"], v["po"], v["pe"]))
            lines += ["", "**分家族**（仅 ≥15 题；该家族 vs 其余的二值 κ）",
                      "", "| 家族 | n | κ |", "|---|---|---|"]
            for fam, v in b["by_family"].items():
                lines.append("| %s | %d | %.3f |" % (fam, v["n"], v["mean_kappa"]))
            lines += ["", "**难题 vs 常规**：%s" % ", ".join(
                "%s n=%d κ=%.3f" % (t, v["n"], v["mean_kappa"])
                for t, v in b["hard_vs_easy"].items()), ""]
        lines += ["| 受试者 | 完成 | 准确率 | 判不了率 | 体系外误选 | 耗时中位数 | <5s |",
                  "|---|---|---|---|---|---|---|"]
        for p, d in b["per_person"].items():
            lines.append("| %s | %d/%d | %s | %s | %s | %s s | %d |" % (
                p, d["completed"], d["n"], d["accuracy"], d["unknown_rate"],
                d["out_of_taxonomy_rate"], d["latency_median_s"], d["under_5s"]))
        lines.append("")
        if b["human_vs_gold"]:
            lines += ["**与真值 / 规则基线的一致性**（规则基线 = `ai_rule.json`，不是真值）", "",
                      "| 受试者 | vs 真值 κ | 一致率 | vs 规则 κ | 一致率 |",
                      "|---|---|---|---|---|"]
            for p in b["people"]:
                g = b["human_vs_gold"][p]
                r = b["human_vs_rule"][p]
                lines.append("| %s | %.3f | %.3f | %.3f | %.3f |" % (
                    p, g["kappa"], g["agreement"], r["kappa"], r["agreement"]))
            if b.get("ci95") and b["human_vs_rule"]:
                lo, hi = b["ci95"]
                rk = [v["kappa"] for v in b["human_vs_rule"].values()]
                inside = lo <= min(rk) and max(rk) <= hi
                lines += ["", "> 人-规则 κ %.3f–%.3f；人-人 95%% CI [%.3f, %.3f]。"
                          "规则基线%s在人类一致性带内。" % (
                              min(rk), max(rk), lo, hi, "" if inside else "不")]
            lines.append("")
    lines += ["## 培训前后", "",
              "| 受试者 | T0 准确率 | T1 准确率 | Δ | 耗时中位数 T0→T1 | 判不了 T0→T1 | "
              "体系外 T0→T1 | McNemar b/c | χ² |",
              "|---|---|---|---|---|---|---|---|---|"]
    for p, g in gains.items():
        t0, t1 = g["T0"], g["T1"]
        lines.append("| %s | %s | %s | %s | %s → %s | %s → %s | %s → %s | %d / %d | %.2f |" % (
            p, t0["accuracy"] if t0 else "—", t1["accuracy"] if t1 else "—",
            g["accuracy_delta"],
            ("%s s" % t0["latency_median_s"]) if t0 else "—",
            ("%s s" % t1["latency_median_s"]) if t1 else "—",
            t0["unknown_rate"] if t0 else "—", t1["unknown_rate"] if t1 else "—",
            t0["out_of_taxonomy_rate"] if t0 else "—",
            t1["out_of_taxonomy_rate"] if t1 else "—",
            g["mcnemar_b"], g["mcnemar_c"], g["mcnemar_chi2"]))
    lines += ["", "> χ² > 3.84 视为培训效应显著（McNemar, df=1, α=.05）。", ""]

    lines += ["## 质控", "",
              "| 受试者 | 轮次 | 完成 | 中位耗时 | <5 s | order × 判错 ρ | 判定 |",
              "|---|---|---|---|---|---|---|"]
    for rnd in ROUNDS:
        for p, d in report["rounds"][rnd]["per_person"].items():
            lines.append("| %s | %s | %d/%d | %s s | %d | %.3f | %s |" % (
                p, rnd, d["completed"], d["n"], d["latency_median_s"],
                d["under_5s"], d["fatigue_rho"], "通过" if d["qc_pass"] else "不通过"))
    lines += ["", "> 整份作废阈值：完成率 < 90% 或耗时中位数 < 8 s；单题作废：latency < 5 s。"
              " order×判错 ρ 为正表示越到后面越容易错。", ""]
    with open(os.path.join(args.out, "REPORT.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    # 控制台摘要
    print("作答文件 %d 份 → %s" % (len(resp), args.out))
    for rnd in ROUNDS:
        b = report["rounds"][rnd]
        print("  %s: %d 人 %s  平均 κ=%s  CI=%s  二值 κ=%s" % (
            rnd, len(b["people"]), ",".join(b["people"]) or "-",
            b["mean_kappa"], b["ci95"], b["binary_mean_kappa"]))
        for p, d in b["per_person"].items():
            print("     %-4s 完成 %3d/%3d  准确率 %.3f  判不了 %.3f  体系外 %.3f  中位耗时 %ss" % (
                p, d["completed"], d["n"], d["accuracy"], d["unknown_rate"],
                d["out_of_taxonomy_rate"], d["latency_median_s"]))
    if gains:
        print("  培训增益:", ", ".join("%s Δacc=%+.3f χ²=%.2f" % (p, g["accuracy_delta"], g["mcnemar_chi2"])
                                   for p, g in gains.items()))
    else:
        print("  培训增益: 尚无同时具备 T0/T1 的受试者")
    print("wrote", os.path.join(args.out, "REPORT.md"))


if __name__ == "__main__":
    main()
