"""小样本通过率的 Wilson score 置信区间。

论文里大量结论是「k/n 件通过」，n 常常只有 10。直接写 100% 会被审稿人
抓住：10/10 的 95% 下界只有 0.722。用 Wilson score interval 而不是
Wald（正态近似），因为 p=0 或 p=1 时 Wald 会退化成零宽区间。

    wilson(10, 10) -> (0.7225, 1.0000)
    wilson(0, 10)  -> (0.0000, 0.2775)
    wilson(130, 130) -> (0.9712, 1.0000)

参考：Wilson, E.B. (1927), JASA 22(158):209–212。
"""
from __future__ import annotations

import math


def wilson(k, n, z=1.96):
    """返回 (下界, 上界)；z=1.96 对应 95%，z=2.576 对应 99%。

    k/n 超出 [0,1] 或 n<=0 时抛 ValueError，不静默截断。
    """
    n = int(n)
    k = int(k)
    if n <= 0:
        raise ValueError("n 必须为正整数，收到 %r" % n)
    if k < 0 or k > n:
        raise ValueError("k 必须在 [0,n] 内，收到 k=%r n=%r" % (k, n))
    z2 = z * z
    p = k / n
    den = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / den
    half = (z / den) * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n))
    return (round(max(0.0, center - half), 4),
            round(min(1.0, center + half), 4))


def wilson_table(entries):
    """批量：[{"name":..., "k":..., "n":...}, ...] → 带 rate/区间的列表。"""
    out = []
    for e in entries:
        lo, hi = wilson(e["k"], e["n"])
        out.append({"name": e["name"], "k": e["k"], "n": e["n"],
                    "rate": round(e["k"] / e["n"], 4),
                    "wilson95": [lo, hi]})
    return out
