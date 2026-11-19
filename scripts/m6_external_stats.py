"""Part-level statistics for the 130-part external audit.

Reads reports/m6_ocl_external.json (archived third-party audit) and reports:
  - Wilson 95% intervals for part-level violation counts;
  - exact McNemar test for the paired plain-vs-guard comparison;
  - threshold sensitivity of the part-level violation count;
  - violation rate by height-layer stratum.

No geometry is recomputed and no model is called. The unit of analysis is the
part, because tool-centre samples within a part are strongly correlated.

    PYTHONPATH=src python scripts/m6_external_stats.py
    -> reports/m6_external_stats.json
"""
from __future__ import annotations

import json
import math
import os
from collections import Counter

from cadcam.verify.stats import wilson

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "reports", "m6_ocl_external.json")
OUT = os.path.join(ROOT, "reports", "m6_external_stats.json")
THRESHOLDS = [0.1, 0.25, 0.5, 0.75, 1.0, 2.0, 5.0, 10.0, 20.0]
STRATA = [(2, 10), (11, 20), (21, 40), (41, 10_000)]


def _depth(part, route):
    return part[route]["ocl_oracle"]["worst_gouge_depth_mm"]


def _count(depths, threshold):
    return sum(d > threshold for d in depths)


def _mcnemar_exact(b, c):
    """Two-sided exact McNemar p-value for discordant counts b and c."""
    n = b + c
    if n == 0:
        return 1.0
    # Two-sided exact binomial test at p=0.5, doubled one-tail, capped at 1.
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2.0 ** n)
    return min(1.0, 2.0 * tail)


def main():
    with open(SRC, encoding="utf-8") as fh:
        rep = json.load(fh)
    parts = rep["parts"]
    n = len(parts)
    b2 = [_depth(p, "B2_masked") for p in parts]
    guard = [_depth(p, "B2_half_cell_guard") for p in parts]

    sensitivity = []
    for th in THRESHOLDS:
        k2, kg = _count(b2, th), _count(guard, th)
        sensitivity.append({
            "threshold_mm": th,
            "plain_parts": k2, "plain_wilson95": list(wilson(k2, n)),
            "guard_parts": kg, "guard_wilson95": list(wilson(kg, n)),
        })

    # Paired guard effect at the 0.5 mm line.
    b = sum(1 for x, y in zip(b2, guard) if x > 0.5 and y <= 0.5)
    c = sum(1 for x, y in zip(b2, guard) if x <= 0.5 and y > 0.5)
    both_bad = sum(1 for x, y in zip(b2, guard) if x > 0.5 and y > 0.5)
    neither = sum(1 for x, y in zip(b2, guard) if x <= 0.5 and y <= 0.5)
    mcnemar = {
        "threshold_mm": 0.5,
        "plain_only_bad": b, "guard_only_bad": c,
        "both_bad": both_bad, "neither_bad": neither,
        "exact_two_sided_p": _mcnemar_exact(b, c),
    }

    strata = []
    for lo, hi in STRATA:
        group = [p for p in parts if lo <= p["levels"] <= hi]
        if not group:
            continue
        k2 = sum(_depth(p, "B2_masked") > 0.5 for p in group)
        kg = sum(_depth(p, "B2_half_cell_guard") > 0.5 for p in group)
        strata.append({
            "levels": [lo, hi] if hi < 10_000 else [lo, None],
            "n": len(group),
            "plain_parts_over_0_5": k2,
            "plain_rate": round(k2 / len(group), 4),
            "plain_wilson95": list(wilson(k2, len(group))),
            "guard_parts_over_0_5": kg,
            "guard_rate": round(kg / len(group), 4),
            "guard_wilson95": list(wilson(kg, len(group))),
        })

    # Coverage bands: fixed, interpretable cuts rather than tied quantiles.
    coverage = [p["geometry"]["coverage_pct"] for p in parts]
    bands = [("<96%", -1.0, 96.0), ("96-99%", 96.0, 99.0), (">=99%", 99.0, 1e9)]
    coverage_bands = []
    for name, lo, hi in bands:
        idx = [i for i, c in enumerate(coverage) if lo < c <= hi] if lo >= 0 else \
              [i for i, c in enumerate(coverage) if c <= hi]
        if not idx:
            continue
        k2 = sum(b2[i] > 0.5 for i in idx)
        kg = sum(guard[i] > 0.5 for i in idx)
        coverage_bands.append({"band": name, "n": len(idx),
                               "plain_over_0_5": k2,
                               "plain_rate": round(k2 / len(idx), 4),
                               "guard_over_0_5": kg,
                               "guard_rate": round(kg / len(idx), 4)})

    def _rank(values):
        # Average ranks for ties; otherwise a coverage of 100 on many parts
        # would be ordered arbitrarily and distort the correlation.
        order = sorted(range(len(values)), key=lambda i: values[i])
        ranks = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            average = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                ranks[order[k]] = average
            i = j + 1
        return ranks

    def _spearman(x, y):
        rx, ry = _rank(x), _rank(y)
        n_ = len(x)
        mx, my = sum(rx) / n_, sum(ry) / n_
        cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
        vx = sum((a - mx) ** 2 for a in rx)
        vy = sum((b - my) ** 2 for b in ry)
        return cov / math.sqrt(vx * vy)

    coverage_analysis = {
        "spearman_coverage_vs_violation": round(_spearman(coverage, [float(x > 0.5) for x in b2]), 4),
        "spearman_coverage_vs_worst_depth": round(_spearman(coverage, b2), 4),
        "bands": coverage_bands,
        "interpretation": "Association only; low coverage and difficult geometry co-occur.",
    }

    level_counts = Counter(p["levels"] for p in parts)
    result = {
        "source": os.path.relpath(SRC, ROOT).replace("\\", "/"),
        "n_parts": n,
        "unit": "part; tool-centre samples within a part are correlated",
        "threshold_sensitivity": sensitivity,
        "paired_guard_effect": mcnemar,
        "level_strata": strata,
        "coverage_analysis": coverage_analysis,
        "levels_summary": {
            "min": min(level_counts), "max": max(level_counts),
            "median": sorted(p["levels"] for p in parts)[n // 2],
            "distinct": len(level_counts),
        },
        "depth_summary": {
            "plain_max_mm": max(b2), "plain_median_mm": sorted(b2)[n // 2],
            "guard_max_mm": max(guard), "guard_median_mm": sorted(guard)[n // 2],
            "plain_over_0_5": sum(x > 0.5 for x in b2),
            "guard_over_0_5": sum(x > 0.5 for x in guard),
        },
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")

    print("n =", n)
    print("threshold  plain  95%% CI          guard  95%% CI")
    for row in sensitivity:
        print("  %5.2f    %3d/%-3d [%.4f, %.4f]   %3d/%-3d [%.4f, %.4f]" % (
            row["threshold_mm"], row["plain_parts"], n, *row["plain_wilson95"],
            row["guard_parts"], n, *row["guard_wilson95"]))
    print("McNemar: b=%d c=%d both=%d neither=%d p=%.3g" % (
        b, c, both_bad, neither, mcnemar["exact_two_sided_p"]))
    print("coverage bands:")
    for row in coverage_bands:
        print("  %-8s n=%d plain %d (%.3f) guard %d (%.3f)" % (
            row["band"], row["n"], row["plain_over_0_5"], row["plain_rate"],
            row["guard_over_0_5"], row["guard_rate"]))
    print("Spearman coverage vs violation %.3f, vs depth %.3f" % (
        coverage_analysis["spearman_coverage_vs_violation"],
        coverage_analysis["spearman_coverage_vs_worst_depth"]))
    print("strata:")
    for row in strata:
        print("  levels %s n=%d plain %d (%.3f) guard %d (%.3f)" % (
            row["levels"], row["n"], row["plain_parts_over_0_5"],
            row["plain_rate"], row["guard_parts_over_0_5"], row["guard_rate"]))
    print("→", os.path.relpath(OUT, ROOT))


if __name__ == "__main__":
    main()
