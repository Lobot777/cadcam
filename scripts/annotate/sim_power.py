"""模拟"几个人 × 多少题"对 κ 置信区间半宽的影响。

为什么不用解析式：多人标注时各人的错误相关（同一张图大家都会错），
Fleiss κ 的解析方差假设标注者独立同分布，会低估 CI。这里直接模拟
"抽题 → 多个标注者按各自错误率作答 → 算所有配对 Cohen κ 的均值 → bootstrap"，
代价是几十秒，换来的是和 kappa.py 完全一致的口径（pairwise + bootstrap）。

校准：真实家族分布取自 206 面；错误率调到 po≈0.80，与实测 AI-AI 一致
（κ=0.751, po=0.800, pe=0.198），这样模拟出的半宽可以直接读。
"""

import argparse
import itertools
import json
import os
import random

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LABELS = os.path.join(ROOT, "data", "zenodo_rptu", "labels")

# 混淆倾向：人出错时更容易落到"邻居类"而不是乱猜
NEAR = {
    "stock": ["step", "pocket", "freeform"],
    "boss": ["step", "pocket", "freeform"],
    "pocket": ["step", "slot", "boss"],
    "step": ["pocket", "boss", "stock"],
    "hole": ["fillet", "pocket", "boss"],
    "fillet": ["hole", "chamfer", "boss"],
    "slot": ["pocket", "step", "hole"],
    "chamfer": ["fillet", "step", "freeform"],
    "freeform": ["fillet", "boss", "pocket"],
}


def true_dist():
    from collections import Counter
    c = Counter()
    for p in [str(i) for i in range(1, 11)]:
        with open(os.path.join(LABELS, "%s.json" % p), encoding="utf-8") as fh:
            lab = json.load(fh)
        c.update(m["family"] for m in lab["faces"].values())
    tot = float(sum(c.values()))
    return [(k, v / tot) for k, v in c.most_common()]


def draw(dist, rng):
    x, acc = rng.random(), 0.0
    for k, w in dist:
        acc += w
        if x <= acc:
            return k
    return dist[-1][0]


def answer(y, err, rng):
    if rng.random() >= err:
        return y
    cand = NEAR.get(y) or list(NEAR)
    return rng.choice(cand)


def kappa_pair(a, b):
    n = len(a)
    po = sum(1 for x, y in zip(a, b) if x == y) / float(n)
    ka, kb = {}, {}
    for x in a:
        ka[x] = ka.get(x, 0) + 1
    for y in b:
        kb[y] = kb.get(y, 0) + 1
    pe = sum(ka.get(k, 0) / float(n) * kb.get(k, 0) / float(n) for k in set(ka) | set(kb))
    if pe >= 1.0:
        return 1.0
    return (po - pe) / (1.0 - pe)


def mean_pairwise(mat):
    ks = [kappa_pair(mat[i], mat[j]) for i, j in itertools.combinations(range(len(mat)), 2)]
    return sum(ks) / float(len(ks))


def boot_ci(mat, rng, B=120):
    B = max(40, B)
    n = len(mat[0])
    vals = []
    idx_all = range(n)
    for _ in range(B):
        idx = [rng.choice(idx_all) for _ in idx_all]
        sub = [[row[i] for i in idx] for row in mat]
        vals.append(mean_pairwise(sub))
    vals.sort()
    lo = vals[int(0.025 * B)]
    hi = vals[int(0.975 * B) - 1]
    return lo, hi


BOOT = 120


def run(n_items, n_raters, errs, dist, rng, reps=120):
    halfs, hats, pos = [], [], []
    for _ in range(reps):
        ys = [draw(dist, rng) for _ in range(n_items)]
        mat = [[answer(y, errs[j], rng) for y in ys] for j in range(n_raters)]
        hats.append(mean_pairwise(mat))
        lo, hi = boot_ci(mat, rng, BOOT)
        halfs.append((hi - lo) / 2.0)
        pos.append(sum(1 for j in range(n_raters) for i in range(n_items) if mat[j][i] == ys[i])
                   / float(n_items * n_raters))
    hats.sort()
    return (sum(halfs) / len(halfs), sum(pos) / len(pos),
            hats[int(0.025 * len(hats))], hats[int(0.975 * len(hats)) - 1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=120)
    ap.add_argument("--boot", type=int, default=120)
    args = ap.parse_args()
    global BOOT
    BOOT = args.boot
    rng = random.Random(7)
    dist = true_dist()

    # 错误率：零基础 0.16、熟练 0.08，混合组按 1 熟练 + 其余零基础
    base = 0.16
    skill = 0.07

    print("真值分布:", ", ".join("%s %.1f%%" % (k, 100 * w) for k, w in dist))
    print()
    print("配对 κ 均值的 95%% bootstrap 区间半宽（reps=%d, boot=%d）" % (args.reps, args.boot))
    print()
    hdr = "题量 n |" + "".join("   %d 人%s" % (m, "  " if m < 10 else "") for m in (2, 3, 4, 5))
    print(hdr)
    print("-" * len(hdr))
    rows = []
    for n in (40, 60, 80, 100, 160):
        cells = []
        for m in (2, 3, 4, 5):
            errs = [skill] + [base] * (m - 1)
            h, po, klo, khi = run(n, m, errs, dist, rng, reps=args.reps)
            cells.append("%.3f" % h)
            rows.append({"n": n, "m": m, "half": round(h, 4), "po": round(po, 4),
                         "k_lo": round(klo, 3), "k_hi": round(khi, 3)})
        print("  %3d   |" % n + "".join("  %s " % c for c in cells))
    print()
    print("（单元格 = 半宽。κ 真值≈0.75 时，n=80 / 4 人 → 0.75±%.3f）"
          % [r for r in rows if r["n"] == 80 and r["m"] == 4][0]["half"])
    print()
    # 分家族可报告性
    print("分家族题数（n=80 按真实比例）:")
    for k, w in dist:
        print("  %-8s %4.1f 题" % (k, 80 * w))
    print()
    out = os.path.join(ROOT, "reports", "annotation", "study", "power.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"rows": rows, "dist": dist}, fh, ensure_ascii=False, indent=1)
    print("wrote", out)


if __name__ == "__main__":
    main()
