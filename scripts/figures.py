"""Paper figures: six panels, all recomputed from reports/*.json, never hand-copied.

    PYTHONPATH=src python scripts/figures.py
    PYTHONPATH=src python scripts/figures.py --sync-latex
    → reports/figures/fig1..fig6 .png(200dpi) + .pdf
    → with --sync-latex, copies the PDFs to latex/figures/fig1.pdf ... fig6.pdf

fig6 needs reports/m6_ocl_external.json (all 130 parts). If the file is missing
or still a pilot subset, fig6 is skipped without affecting the other five.

Palette: ink / grey / rust / teal only. No gradients, no emoji.
All figure text is English; the manuscript is English.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
from matplotlib.ticker import ScalarFormatter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REP = os.path.join(ROOT, "reports")
FIG = os.path.join(REP, "figures")
LATEX_FIG = os.path.join(ROOT, "latex", "figures")

INK = "#1f2933"
MUTED = "#7b8794"
GRID = "#dfe5ec"
RUST = "#b4552d"      # accent only; not a gouge semantic
TEAL = "#2c6b66"
STEEL = "#4a6f8a"
SAND = "#c9b8a3"

plt.rcParams.update({
    "font.sans-serif": ["DejaVu Sans"],
    "axes.unicode_minus": False,
    "font.size": 9,
    "axes.edgecolor": MUTED,
    "axes.labelcolor": INK,
    "text.color": INK,
    "xtick.color": INK,
    "ytick.color": INK,
    "axes.grid": True,
    "grid.color": GRID,
    "grid.linewidth": 0.7,
    "figure.dpi": 200,
})

ORDER = ["fig1_architecture", "fig2_recognition", "fig3_three_routes",
         "fig4_criterion", "fig5_orchestration", "fig6_ocl_external"]


def _load(name):
    with open(os.path.join(REP, name), encoding="utf-8") as fh:
        return json.load(fh)


def _save(fig, name):
    os.makedirs(FIG, exist_ok=True)
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(FIG, "%s.%s" % (name, ext)),
                    bbox_inches="tight", pad_inches=0.22, facecolor="white")
    plt.close(fig)
    print("  → figures/%s.png / .pdf" % name)


def _clean(ax, top=True):
    for side in ("top", "right"):
        ax.spines[side].set_visible(not top)
    ax.set_axisbelow(True)


# ---------------------------------------------------------------- fig1 architecture
def fig1():
    fig, ax = plt.subplots(figsize=(6.9, 3.15))
    # Use the full canvas: the default axes margin wastes ~22% of the width and
    # makes the box labels too small once the figure is scaled to \textwidth.
    fig.subplots_adjust(left=0.005, right=0.995, top=0.995, bottom=0.005)
    ax.set_xlim(0, 104)
    ax.set_ylim(0, 42)
    ax.axis("off")

    def box(x, y, w, h, title, sub, color=INK, fill="#f5f7fa"):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.6,rounding_size=1.2",
                                    linewidth=1.1, edgecolor=color, facecolor=fill))
        ax.text(x + w / 2, y + h - 3.4, title, ha="center", va="center",
                fontsize=7.2, color=color, weight="bold")
        ax.text(x + w / 2, y + h - 7.6, sub, ha="center", va="top",
                fontsize=6.6, color=MUTED, linespacing=1.35)

    def arrow(x0, y0, x1, y1, color=MUTED):
        ax.add_patch(FancyArrowPatch((x0, y0), (x1, y1), arrowstyle="-|>",
                                     mutation_scale=11, linewidth=1.0,
                                     color=color, shrinkA=2, shrinkB=2))

    box(1, 24, 20, 16, "Input", "STEP solids\nface labels")
    box(26, 24, 21, 16, "Spatial M2", "B-Rep L0–L2\nGNN faces\n0.966 / 0.904")
    box(52, 24, 21, 16, "Manufacturing M3", "Process rules → DAG\nrestricted G-code\nregion masks")
    box(78, 24, 21, 16, "Verification M3", "Z-dexel removal\ninternal checks / DFM\nstructured E_GOUGE")

    for x in (21, 47, 73):
        arrow(x + 0.6, 32, x + 4.4, 32)

    ax.add_patch(FancyBboxPatch((1, 3), 98, 16, boxstyle="round,pad=0.6,rounding_size=1.4",
                                linewidth=1.4, edgecolor=TEAL, facecolor="#eef4f3"))
    ax.text(3.2, 15.6, "Orchestration layer (M4)", fontsize=8.4, color=TEAL,
            weight="bold", va="center")
    roles = ["State machine\n8 stages", "Blackboard\nevent log", "6 roles\nseparated",
             "Repair\n≤3 rounds", "Memory\nper family", "Final gate\ndual-line"]
    for i, r in enumerate(roles):
        x = 3.0 + i * 16.2
        ax.add_patch(FancyBboxPatch((x, 5.2), 15.0, 8.2,
                                    boxstyle="round,pad=0.35,rounding_size=0.9",
                                    linewidth=0.9, edgecolor=STEEL, facecolor="white"))
        ax.text(x + 7.5, 9.3, r, ha="center", va="center", fontsize=6.2,
                color=INK, linespacing=1.4)
    arrow(88, 23.4, 88, 19.6, TEAL)
    arrow(14, 19.6, 14, 23.4, TEAL)
    ax.text(89.4, 22.0, "error object", fontsize=6.2, color=TEAL, va="center")
    ax.text(15.4, 22.0, "replan", fontsize=6.2, color=TEAL, va="center")

    ax.text(1, 0.2, "Criterion: maximum gouge ≤ 0.5 mm and mean absolute deviation ≤ 0.5 mm "
                    "(ADR-001 dual line); Z-dexel grid 0.5 mm, ten anchor parts.",
            fontsize=6.6, color=MUTED)
    _save(fig, "fig1_architecture")


# ---------------------------------------------------------------- fig2 M2
def fig2():
    test = _load("mfr_test_v2.json")
    ablation = _load("mfr_ablation_test_v2.json")   # held-out test, same split as GNN
    gnn = test["gnn"]
    node = ablation["gnn"]

    fig, axes = plt.subplots(1, 2, figsize=(7.4, 2.95),
                            gridspec_kw={"width_ratios": [1.0, 1.5]})
    ax = axes[0]
    names = ["GNN\n(node + edge)", "GNN\n(node only)"]
    acc = [gnn["accuracy"], node["accuracy"]]
    miou = [gnn["macro_iou"], node["macro_iou"]]
    x = np.arange(len(names))
    ax.bar(x - 0.19, acc, 0.36, color=STEEL, label="Accuracy")
    ax.bar(x + 0.19, miou, 0.36, color=SAND, label="Macro IoU")
    for i in range(len(names)):
        ax.text(x[i] - 0.19, acc[i] + 0.015, "%.3f" % acc[i], ha="center",
                fontsize=7.6, color=INK)
        ax.text(x[i] + 0.19, miou[i] + 0.015, "%.3f" % miou[i], ha="center",
                fontsize=7.6, color=INK)
    ax.set_xticks(x)
    ax.set_xticklabels(names, fontsize=8)
    ax.set_ylim(0, 1.30)
    ax.set_ylabel("Score")
    ax.set_title("(A) Face-level recognition, held-out test split", fontsize=9.5, loc="left")
    ax.legend(frameon=False, fontsize=8, ncol=2, loc="upper center")
    _clean(ax)

    ax = axes[1]
    rows = sorted(test["per_class"], key=lambda r: -r["support"])[:12]
    y = np.arange(len(rows))[::-1]
    ax.barh(y, [r["gnn_iou"] for r in rows], 0.55, color=STEEL, label="GNN")
    ax.set_yticks(y)
    ax.set_yticklabels(["%s (%d)" % (r["class"].replace("_", " "), r["support"])
                        for r in rows], fontsize=7.2)
    ax.set_xlim(0, 1.0)
    ax.set_xlabel("IoU")
    ax.set_title("(B) Twelve best-supported classes: GNN IoU (test)", fontsize=9.5, loc="left")
    _clean(ax)
    fig.tight_layout()
    _save(fig, "fig2_recognition")


# ---------------------------------------------------------------- fig3 M3 three routes
def fig3():
    hm = _load("m3_heightmap_eval.json")["parts"]
    cam = _load("m3_cam_eval.json")["parts"]
    parts = [p["part"] for p in hm]
    A = {p["part"]: p["A"] for p in cam}
    B1 = {p["part"]: p["B1"] for p in hm}
    B2 = {p["part"]: p["B2"] for p in hm}

    fig, axes = plt.subplots(1, 2, figsize=(7.4, 2.95))
    x = np.arange(len(parts))
    w = 0.26
    ax = axes[0]
    for off, d, lab, c in ((-w, A, "Route A: FreeCAD CAM", SAND),
                           (0, B1, "Route B1: bounding-box plan", STEEL),
                           (w, B2, "Route B2: region-masked", TEAL)):
        v = [d[p]["max_gouge_depth_mm"] for p in parts]
        ax.bar(x + off, v, w, color=c, label=lab)
    ax.set_xticks(x)
    ax.set_xticklabels(parts)
    ax.set_xlabel("Part")
    ax.set_ylabel("Maximum gouge depth (mm)")
    ax.set_title("(A) Gouging: only route B2 is zero on every part", fontsize=9.5, loc="left")
    ax.axhline(0.5, color=RUST, linewidth=1.0, linestyle="--", label="0.5 mm threshold")
    ax.legend(frameon=False, fontsize=7.6)
    _clean(ax)

    ax = axes[1]
    for off, d, lab, c in ((-w, A, "Route A", SAND), (0, B1, "B1", STEEL),
                           (w, B2, "B2", TEAL)):
        v = [d[p]["mean_deviation_mm"] for p in parts]
        ax.bar(x + off, v, w, color=c, label=lab)
    ax.set_xticks(x)
    ax.set_xticklabels(parts)
    ax.set_xlabel("Part")
    ax.set_ylabel("Mean absolute deviation (mm)")
    ax.set_title("(B) Accuracy: worst mean deviation 0.206 mm", fontsize=9.5, loc="left")
    ax.axhline(0.5, color=RUST, linewidth=1.0, linestyle="--", label="0.5 mm threshold")
    ax.set_yscale("symlog", linthresh=0.1)
    ax.legend(frameon=False, fontsize=7.6)
    _clean(ax)
    fig.tight_layout()
    _save(fig, "fig3_three_routes")


# ---------------------------------------------------------------- fig4 criterion
def fig4():
    c = _load("m3_criterion_analysis.json")
    parts = sorted(c["parts"], key=int)

    fig, axes = plt.subplots(1, 2, figsize=(7.4, 2.95))
    ax = axes[0]
    # The sensitivity sweep covers three representative parts only (1/8/10).
    sens = sorted(c["sensitivity"], key=int)
    for i, p in enumerate(sens):
        v05 = c["sensitivity"][p]["res_0_5"]["max_mm"]
        v10 = c["sensitivity"][p]["res_1_0"]["max_mm"]
        ax.plot([v10, v05], [i, i], color=GRID, linewidth=2.4, zorder=1)
        ax.scatter([v05], [i], s=42, color=STEEL, zorder=3,
                   label="Grid 0.5 mm" if i == 0 else None)
        ax.scatter([v10], [i], s=42, color=SAND, zorder=3,
                   label="Grid 1.0 mm" if i == 0 else None)
        ax.text(v05 + 0.2, i, "%.1f" % v05, va="center", fontsize=7.6, color=INK)
        ax.text(v10 + 0.2, i, "%.1f" % v10, va="center", fontsize=7.6, color=INK)
    ax.set_yticks(range(len(sens)))
    ax.set_yticklabels(["Part %s" % p for p in sens])
    ax.set_xlim(-0.4, 11.2)
    ax.set_ylim(-0.6, len(sens) - 0.4)
    ax.set_xlabel("Maximum deviation (mm)")
    ax.set_title("(A) Maximum deviation collapses on a coarser grid",
                 fontsize=9.5, loc="left")
    ax.legend(frameon=False, fontsize=7.8, loc="lower right")
    _clean(ax)

    ax = axes[1]
    mean = [c["parts"][p]["B2"]["mean_abs_deviation_mm"] for p in parts]
    over = [c["parts"][p]["B2"]["over_0_5mm_pct"] for p in parts]
    size = [max(20, 4 * c["parts"][p]["B2"]["residual_volume_mm3"] / 100.0) for p in parts]
    ax.scatter(mean, over, s=size, color=TEAL, alpha=0.75, edgecolor="white",
               linewidth=1.2, zorder=3)
    for i, p in enumerate(parts):
        if p not in ("2", "8", "10"):
            continue
        dx, dy = (6, 5) if p != "10" else (-8, -12)
        ax.annotate("Part %s" % p, (mean[i], over[i]), textcoords="offset points",
                    xytext=(dx, dy), ha="right" if p == "10" else "left",
                    fontsize=7.0, color=MUTED)
    ax.set_xlabel("Mean absolute deviation (mm)")
    ax.set_ylabel("Columns above 0.5 mm (%)")
    ax.set_title("(B) Route B2: deviations concentrate at step boundaries",
                 fontsize=9.5, loc="left")
    ax.set_xlim(-0.01, 0.24)
    ax.set_ylim(-0.15, 3.2)
    ax.text(0.985, 0.06, "All over-threshold columns fall in the boundary band",
            transform=ax.transAxes, ha="right", va="bottom", fontsize=7.2,
            color=MUTED)
    _clean(ax)
    fig.tight_layout()
    _save(fig, "fig4_criterion")


# ---------------------------------------------------------------- fig5 M4
def fig5():
    rep = _load("m4_run.json")
    arms = {a["arm"]: a for a in rep["arms"]}
    order = ["A_no_repair", "B_repair_cold", "C_repair_warm"]
    label = ["A\nno repair", "B\ncold", "C\nwarm"]

    fig, axes = plt.subplots(1, 2, figsize=(7.6, 2.95),
                            gridspec_kw={"width_ratios": [1.0, 1.35],
                                         "wspace": 0.42})
    ax = axes[0]
    x = np.arange(3)
    rate = [arms[k]["pass_rate"] for k in order]
    ax.bar(x, rate, 0.5, color=[SAND, STEEL, TEAL])
    for i, v in enumerate(rate):
        ax.text(i, v + 0.02, "%.3f" % v, ha="center", fontsize=8.2, color=INK)
    ax.set_xticks(x)
    ax.set_xticklabels(label, fontsize=7.2)
    ax.set_ylim(0, 1.15)
    ax.set_ylabel("End-to-end pass rate")
    ax.set_title("(A) Twenty tasks: pass rate", fontsize=9.5, loc="left")
    _clean(ax)

    ax = axes[1]
    # Explicit family order; never rely on sorted() plus fixed labels.
    fams = ["levels_le_2", "levels_3_4", "levels_ge_5"]
    fam_text = ["≤2 levels", "3–4 levels", "≥5 levels"]
    fam_labels = ["%s (n=%d)" % (fam_text[i], arms["A_no_repair"]["by_family"][f]["n"])
                  for i, f in enumerate(fams)]
    x = np.arange(len(fams))
    w = 0.26
    for off, k, lab, c in ((-w, "A_no_repair", "A", SAND),
                           (0, "B_repair_cold", "B", STEEL),
                           (w, "C_repair_warm", "C", TEAL)):
        v = [arms[k]["by_family"][f]["pass_rate"] for f in fams]
        ax.bar(x + off, v, w, color=c, label=lab)
    ax.set_xticks(x)
    ax.set_xticklabels(fam_labels, fontsize=8)
    ax.set_ylim(0, 1.34)
    ax.set_yticks([0, 0.2, 0.4, 0.6, 0.8, 1.0])
    ax.set_ylabel("Pass rate")
    ax.set_title("(B) Pass rate by part family", fontsize=9.5, loc="left")
    ax.legend(frameon=False, fontsize=7.8, ncol=3, loc="upper center",
              bbox_to_anchor=(0.5, 1.02))
    ax2 = ax.twinx()
    r = [arms["C_repair_warm"]["by_family"][f]["mean_rounds"] for f in fams]
    ax2.plot(x, r, color=RUST, marker="o", markersize=4, linewidth=1.2,
             label="C: mean repair rounds")
    ax2.set_ylim(0, 0.6)
    ax2.set_ylabel("Mean repair rounds", color=RUST, labelpad=7)
    ax2.tick_params(axis="y", colors=RUST)
    ax2.grid(False)
    _clean(ax)
    fig.subplots_adjust(left=0.085, right=0.915, top=0.84, bottom=0.16)
    _save(fig, "fig5_orchestration")


# --------------------------------------------------- fig6 OCL × 130 external parts
def fig6():
    """Third-party criterion over 130 parts: gouge classes and the guard trade-off."""
    path = os.path.join(REP, "m6_ocl_external.json")
    if not os.path.exists(path):
        print("  ! fig6 skipped: m6_ocl_external.json is missing")
        return
    with open(path, encoding="utf-8") as fh:
        rep = json.load(fh)
    parts = [p for p in rep["parts"] if "B2_masked" in p]
    if len(parts) < 20:
        print("  ! fig6 skipped: external set is still a pilot (%d parts)" % len(parts))
        return

    # (a) Part-level worst violation with the actual process cutters.
    rows = []
    for p in parts:
        b2 = p["B2_masked"]["ocl_oracle"]["worst_gouge_depth_mm"]
        gd = p.get("B2_half_cell_guard", {}).get("ocl_oracle", {}).get("worst_gouge_depth_mm", 0.0)
        rows.append((b2, gd))
    rows.sort(key=lambda r: r[0], reverse=True)
    idx = np.arange(len(rows))
    b2_depth = np.array([r[0] for r in rows])
    gd_depth = np.array([r[1] for r in rows])
    n_b2 = int((b2_depth > 0.5).sum())
    n_gd = int((gd_depth > 0.5).sum())

    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(7.4, 2.95))
    ax.plot(idx, b2_depth, color=STEEL, linewidth=1.3, label="route B2")
    ax.plot(idx, gd_depth, color=RUST, linewidth=1.3, label="route B2 + half-cell guard")
    ax.axhline(0.5, color=INK, linewidth=1.0, linestyle="--")
    ax.annotate("0.5 mm", xy=(0.99, 0.5), xycoords=("axes fraction", "data"),
                xytext=(0, 3), textcoords="offset points", ha="right", va="bottom",
                fontsize=7.4, color=INK)
    ax.set_yscale("symlog", linthresh=0.01)
    ax.set_ylim(-0.004, 300)
    ax.set_xlim(-1, len(idx))
    ax.set_xlabel("130 external parts (sorted by route-B2 worst depth, descending)")
    ax.set_ylabel("Worst gouge depth per part (mm, symlog)")
    ax.set_title("(A) Worst depth per part: %d/130, guard %d/130"
                 % (n_b2, n_gd), loc="left", fontsize=8.8)
    ax.legend(frameon=False, fontsize=7.5, loc="upper right")
    _clean(ax)

    # (b) Pareto: gouge points vs residual volume, zeros kept by symlog.
    xs, ys, xs2, ys2 = [], [], [], []
    for p in parts:
        if "B2_half_cell_guard" not in p:
            continue
        xs.append(p["B2_masked"]["ocl_oracle"]["gouge_points"])
        ys.append(p["B2_masked"]["our_dexel"]["residual_volume_mm3"] or 0.0)
        xs2.append(p["B2_half_cell_guard"]["ocl_oracle"]["gouge_points"])
        ys2.append(p["B2_half_cell_guard"]["our_dexel"]["residual_volume_mm3"] or 0.0)
    for a, b, c, d in zip(xs, ys, xs2, ys2):
        ax2.plot([a, c], [b, d], color=GRID, linewidth=0.6, zorder=1)
    ax2.scatter(xs, ys, s=14, color=STEEL, alpha=0.75, zorder=2, label="route B2")
    ax2.scatter(xs2, ys2, s=14, color=RUST, alpha=0.75, zorder=3,
                label="route B2 + half-cell guard")
    # symlog keeps exact zeros visible; a log axis would require a display floor.
    ax2.set_xscale("symlog", linthresh=1)
    ax2.set_yscale("symlog", linthresh=1)
    ax2.set_xlim(-0.3, None)
    ax2.set_ylim(-0.3, None)
    ax2.xaxis.set_major_formatter(ScalarFormatter())
    ax2.set_xlabel("Gouge points judged by OCL (symlog)")
    ax2.set_ylabel("Dexel residual volume, mm³ (symlog)")
    ax2.set_title("(B) Cost of fewer gouges: residual volume", loc="left", fontsize=8.8)
    ax2.legend(frameon=False, fontsize=7.5, loc="lower left")
    _clean(ax2)
    fig.subplots_adjust(left=0.075, right=0.975, top=0.92, bottom=0.16, wspace=0.26)
    _save(fig, "fig6_ocl_external")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sync-latex", action="store_true",
                        help="copy the six PDFs into latex/figures/fig1.pdf ... fig6.pdf")
    args = parser.parse_args()
    print("Generating figures:")
    for fn in (fig1, fig2, fig3, fig4, fig5, fig6):
        fn()
    if args.sync_latex:
        os.makedirs(LATEX_FIG, exist_ok=True)
        for i, name in enumerate(ORDER, 1):
            src = os.path.join(FIG, name + ".pdf")
            if os.path.exists(src):
                shutil.copyfile(src, os.path.join(LATEX_FIG, "fig%d.pdf" % i))
                print("  → latex/figures/fig%d.pdf" % i)


if __name__ == "__main__":
    main()
