"""从三角网格求剖面（不依赖 FreeCAD 的 slice API）。

两个坑都踩过：
  1. `Part.Shape.slice()` 在这批 STEP 上返回的 wire 点集不成环，投影后只剩几条线；
  2. 把交线段串成折线再填充，串接顺序一错就会出现横跨全图的斜线伪影。

所以这里**不串环**：每段单独画（轮廓自然正确），面积用有向线段的和（Green 公式）算——
每个交线段的方向继承三角形绕向，Σ ½(x1·y2−x2·y1) 就是材料截面积，跟串接顺序无关。
"""

import json
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

EPS = 1e-9


def seg_of_tri(tri, axis, level):
    """三角形与 axis=level 平面的交线段（投影成 2D）。"""
    pts = []
    for i in range(3):
        a, b = tri[i], tri[(i + 1) % 3]
        da, db = a[axis] - level, b[axis] - level
        if abs(da) < EPS and abs(db) < EPS:
            continue
        if da * db > 0:
            continue
        if abs(da - db) < EPS:
            continue
        t = da / (da - db)
        p = [a[k] + (b[k] - a[k]) * t for k in range(3)]
        pts.append(p)
    if len(pts) < 2:
        return None
    keep = [k for k in range(3) if k != axis]
    return (pts[0][keep[0]], pts[0][keep[1]]), (pts[1][keep[0]], pts[1][keep[1]])


def section(geom, axis, level):
    segs = []
    for f in geom["faces"]:
        for tri in f["triangles"]:
            s = seg_of_tri(tri, axis, level)
            if s:
                segs.append(s)
    return segs


def signed_area(segs):
    """（不再用于出图）Green 公式求截面积。

    注意：只有在所有面法向一致朝外时这个和才等于材料面积。这批 STEP 里
    凹面（孔/腔壁）法向朝内，符号相反，求和会互相抵消（件 10 的 z=1 截面
    实测 3294，真值 6400）。面积一律以 FreeCAD 的面面积为准，见
    docs/MEASURE-PART10.md 的对账表。
    """
    return 0.5 * sum(a[0] * b[1] - b[0] * a[1] for a, b in segs)


def draw(ax, segs, title, h_axis, v_axis, labels, want_abs_area=False):
    for a, b in segs:
        ax.plot([a[0], b[0]], [a[1], b[1]], color="#22526b", lw=0.9,
                solid_capstyle="round")
    pts = [p for s in segs for p in s]
    if pts:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        pad = 2.0
        ax.set_xlim(min(xs) - pad, max(xs) + pad)
        ax.set_ylim(min(ys) - pad, max(ys) + pad)
    ax.set_aspect("equal")
    ax.grid(alpha=0.2, lw=0.4)
    area = abs(signed_area(segs)) if want_abs_area else None
    ax.set_title(title + ("   A=%.0f mm²" % area if area else ""), fontsize=9)
    ax.set_xlabel(h_axis)
    ax.set_ylabel(v_axis)
    for v, txt in labels:
        if pts and min(ys) <= v <= max(ys):
            ax.axhline(v, color="#c62828", lw=0.4, ls=":")
            ax.text(ax.get_xlim()[0] + 0.6, v, txt, fontsize=6.5, color="#c62828",
                    va="bottom", ha="left")


def main():
    geom = json.load(open(sys.argv[1], encoding="utf-8"))
    part, out = sys.argv[2], sys.argv[3]
    ax_xy = None
    if len(sys.argv) > 4:
        ax_xy = json.load(open(sys.argv[4], encoding="utf-8"))["axis_xy"]
    ay = ax_xy[1] if ax_xy else 0.0
    axx = ax_xy[0] if ax_xy else 0.0

    fig = plt.figure(figsize=(13.2, 9.0), facecolor="white")
    gs = fig.add_gridspec(2, 2, height_ratios=[1.25, 1])

    ax1 = fig.add_subplot(gs[0, 0])
    s1 = section(geom, 1, ay)
    draw(ax1, s1, "section y=%.1f (XZ)" % ay, "x [mm]", "z [mm]",
         [(0, "z0"), (34, "34"), (44, "44"), (48, "48"), (52, "52")])

    ax2 = fig.add_subplot(gs[0, 1])
    s2 = section(geom, 0, axx)
    draw(ax2, s2, "section x=%.1f (YZ)" % axx, "y [mm]", "z [mm]",
         [(0, "z0"), (34, "34"), (44, "44"), (48, "48"), (52, "52")])

    ax3 = fig.add_subplot(gs[1, 0])
    for lvl, col in ((46.0, "#1b5e20"), (47.0, "#6a1b9a"), (49.5, "#c62828")):
        segs = section(geom, 2, lvl)
        lab = "z=%.1f" % lvl
        for a, b in segs:
            ax3.plot([a[0], b[0]], [a[1], b[1]], color=col, lw=0.9, label=lab)
    ax3.set_aspect("equal")
    ax3.grid(alpha=0.2, lw=0.4)
    ax3.set_title("horizontal cuts (XY)", fontsize=9)
    ax3.set_xlabel("x [mm]")
    ax3.set_ylabel("y [mm]")
    h, l = ax3.get_legend_handles_labels()
    seen, hh, ll = set(), [], []
    for hi, li in zip(h, l):
        if li not in seen:
            seen.add(li)
            hh.append(hi)
            ll.append(li)
    if hh:
        ax3.legend(hh, ll, fontsize=6.5, loc="upper right")

    fig.suptitle("part %s  sections from triangle mesh" % part, fontsize=11)
    fig.tight_layout()
    fig.savefig(out, dpi=125, facecolor="white")
    print("segments:", len(s1), len(s2))


if __name__ == "__main__":
    main()
