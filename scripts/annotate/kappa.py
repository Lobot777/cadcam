"""计算两名标注员在 30 个抽样面上的一致性（Cohen's κ + bootstrap 置信区间）。

为什么不是简单算"一致率"：抽样是按家族分层的，各类先验被拉平，
一致率会被"stock 占了 8/30"这种分布人为抬高；κ 扣掉了随机一致的部分。

同时报三档：
  1. family 级（7 类，主指标）
  2. 二值级（stock vs 非 stock）—— 最粗但最稳
  3. mfcad_class 级（含 null 作为一类）—— 最细，噪声也最大
"""

import json
import os
import random
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KAPPA = os.path.join(ROOT, "reports", "annotation", "kappa")


def kappa(a, b, labels):
    n = len(a)
    assert n == len(b) and n > 0
    po = sum(1 for x, y in zip(a, b) if x == y) / float(n)
    ca, cb = Counter(a), Counter(b)
    pe = sum((ca.get(k, 0) / float(n)) * (cb.get(k, 0) / float(n)) for k in labels)
    if abs(1.0 - pe) < 1e-12:
        return 1.0, po, pe
    return (po - pe) / (1.0 - pe), po, pe


def boot(a, b, labels, rounds=5000, seed=7):
    rng = random.Random(seed)
    n = len(a)
    vals = []
    for _ in range(rounds):
        idx = [rng.randrange(n) for _ in range(n)]
        aa = [a[i] for i in idx]
        bb = [b[i] for i in idx]
        k, _, _ = kappa(aa, bb, labels)
        vals.append(k)
    vals.sort()
    return vals[int(0.025 * rounds)], vals[int(0.975 * rounds)]


def interpret(k):
    if k < 0.0:
        return "差于随机"
    if k < 0.20:
        return "极弱"
    if k < 0.40:
        return "弱"
    if k < 0.60:
        return "中等"
    if k < 0.80:
        return "较强"
    return "强"


def main():
    with open(os.path.join(KAPPA, "answer_key.json"), encoding="utf-8") as fh:
        key = json.load(fh)
    sub_path = (sys.argv[1] if len(sys.argv) > 1
                else os.path.join(KAPPA, "subagent.json"))
    if not os.path.isabs(sub_path):
        sub_path = os.path.join(KAPPA, sub_path)
    if not os.path.exists(sub_path):
        print("缺标注文件：%s" % sub_path)
        return 1
    print("对比文件：%s" % os.path.basename(sub_path))
    with open(sub_path, encoding="utf-8") as fh:
        sub = json.load(fh)
    got = {it["sample_id"]: it for it in sub["items"]}

    ids = sorted(key)
    miss = [i for i in ids if i not in got]
    extra = [i for i in got if i not in key]
    if miss or extra:
        print("样本不齐：缺 %s 多 %s" % (miss, extra))

    a = [key[i]["family"] for i in ids]
    b = [got[i]["family"] for i in ids if i in got]
    ids = [i for i in ids if i in got]
    labels = sorted(set(a) | set(b))
    k, po, pe = kappa(a, b, labels)
    lo, hi = boot(a, b, labels)

    # 混淆矩阵
    conf = defaultdict(Counter)
    for i, x, y in zip(ids, a, b):
        conf[x][y] += 1

    # 二值：stock vs 非 stock
    a2 = ["stock" if x == "stock" else "feature" for x in a]
    b2 = ["stock" if x == "stock" else "feature" for x in b]
    k2, po2, _ = kappa(a2, b2, ["stock", "feature"])

    # 类级
    a3 = []
    for i in ids:
        with open(os.path.join(ROOT, "data", "zenodo_rptu", "labels",
                               "%s.json" % key[i]["part"]), encoding="utf-8") as fh:
            lab = json.load(fh)
        meta = lab["faces"][str(key[i]["face"])]
        a3.append(meta["mfcad_class"] or "null")
    b3 = [got[i].get("mfcad_class") or "null" for i in ids]
    l3 = sorted(set(a3) | set(b3))
    k3, po3, _ = kappa(a3, b3, l3)

    # 裁决前的旧答案键（冻结于盲标时），用来同时报"裁决前/裁决后"两个 κ
    v1_path = os.path.join(KAPPA, "answer_key.v1.json")
    if os.path.exists(v1_path):
        with open(v1_path, encoding="utf-8") as fh:
            old = json.load(fh)
        a0 = [old[i]["family"] for i in ids]
        k0, po0, _ = kappa(a0, b, sorted(set(a0) | set(b)))
        print("裁决前（冻结键）: κ=%.3f  po=%.3f" % (k0, po0))

    print("n=%d" % len(ids))
    print("family 级 : κ=%.3f  po=%.3f  pe=%.3f  95%%CI[%.3f, %.3f]  %s"
          % (k, po, pe, lo, hi, interpret(k)))
    print("stock/非stock: κ=%.3f  po=%.3f" % (k2, po2))
    print("mfcad 类级: κ=%.3f  po=%.3f" % (k3, po3))
    print("\n混淆矩阵（行=主标注，列=子标注）")
    cols = sorted(set(b))
    print("        " + "".join("%9s" % c for c in cols))
    for r in sorted(set(a)):
        print("%-8s" % r + "".join("%9d" % conf[r].get(c, 0) for c in cols))

    print("\n不一致明细")
    for i, x, y in zip(ids, a, b):
        if x != y:
            it = got[i]
            print("  %s 主=%-8s 子=%-8s conf=%.2f  %s" % (i, x, y, it.get("confidence", 0),
                                                       it.get("reason", "")[:60]))

    out = {
        "n": len(ids),
        "family": {"kappa": round(k, 4), "po": round(po, 4), "pe": round(pe, 4),
                   "ci95": [round(lo, 4), round(hi, 4)], "interpret": interpret(k)},
        "binary": {"kappa": round(k2, 4), "po": round(po2, 4)},
        "mfcad": {"kappa": round(k3, 4), "po": round(po3, 4)},
        "confusion": {r: dict(conf[r]) for r in sorted(conf)},
        "disagreements": [{"sample_id": i, "main": x, "sub": y,
                           "confidence": got[i].get("confidence"),
                           "reason": got[i].get("reason")}
                          for i, x, y in zip(ids, a, b) if x != y],
        "sub_family_dist": dict(Counter(b)),
        "main_family_dist": dict(Counter(a)),
    }
    tag = os.path.splitext(os.path.basename(sub_path))[0]
    out_name = "result.json" if tag == "subagent" else "result_%s.json" % tag
    with open(os.path.join(KAPPA, out_name), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print("\n写出", os.path.join(KAPPA, out_name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
