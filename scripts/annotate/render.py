"""把 geom_worker 的输出渲染成"带面号的三视图 + 逐面高亮接触表"。

为什么不用 FreeCAD 自己的截图：headless 下 coin 渲染依赖 GL 上下文，
在这台机器上不可靠；而且我们要的是**可寻址的面号**，不是好看的图。
自己投影 + 画家算法排序，面号就能和 JSON 里的 index 一一对上。

输出：
  reports/annotation/<part>_views.png   四视图，每面一个编号
  reports/annotation/<part>_faces.png   逐面高亮接触表（红=该面，灰=其余）
"""

import json
import math
import sys

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.colors
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection

VIEWS = {
    "iso":      (1.0, -1.0, 0.85),
    "iso_back": (-1.0, 1.0, 0.85),
    "top":      (0.0, 0.05, 1.0),
    "front":    (0.0, -1.0, 0.30),
    "back":     (0.0, 1.0, 0.30),
    "right":    (1.0, 0.0, 0.30),
    "left":     (-1.0, 0.0, 0.30),
    "bottom":   (0.0, 0.0, -1.0),
}


def _basis(direction):
    d = np.array(direction, dtype=float)
    d /= np.linalg.norm(d)
    up = np.array([0.0, 0.0, 1.0])
    if abs(float(d @ up)) > 0.99:
        up = np.array([0.0, 1.0, 0.0])
    x = np.cross(up, d)
    x /= np.linalg.norm(x)
    y = np.cross(d, x)
    return x, y, d


def _project(points, basis):
    """points: (N,3,3) -> xy (N,3,2), depth (N,3)"""
    x, y, d = basis
    flat = points.reshape(-1, 3)
    xy = np.stack([flat @ x, flat @ y], axis=1).reshape(points.shape[:-1] + (2,))
    dep = (flat @ d).reshape(points.shape[:-1])
    return xy, dep


def _shade(normal, light):
    n = np.array(normal if normal is not None else [0, 0, 1], dtype=float)
    norm = np.linalg.norm(n)
    if norm < 1e-9:
        return 0.7
    n /= norm
    return 0.45 + 0.55 * abs(float(n @ light))


def _collect(geom):
    tris, face_of = [], []
    for face in geom["faces"]:
        for tri in face["triangles"]:
            tris.append(tri)
            face_of.append(face["index"])
    return np.array(tris, dtype=float), np.array(face_of, dtype=int)


def _rgb(base):
    if isinstance(base, str):
        return np.array(matplotlib.colors.to_rgb(base))
    return np.array([float(base)] * 3)


def _front_face_at(tris_2d, tri_depth, face_of, point):
    """2D 某点上最靠前的三角形属于哪个面；没有命中返回 None。

    注意不能拿「面平均深度」和「该点最近深度」比大小——平面在斜视图下
    本身跨越大范围深度（件 1 的顶面从 1.9 到 25.6），那样比必然误判。
    正确做法是比较**面 id**：该点最近的那片三角形是谁的。
    """
    a, b, c = tris_2d[:, 0], tris_2d[:, 1], tris_2d[:, 2]
    e0, e1 = b - a, c - a
    v2 = point - a
    den = e0[:, 0] * e1[:, 1] - e1[:, 0] * e0[:, 1]
    ok = np.abs(den) > 1e-9
    safe = np.where(ok, den, 1.0)
    u = np.where(ok, (v2[:, 0] * e1[:, 1] - e1[:, 0] * v2[:, 1]) / safe, -1.0)
    v = np.where(ok, (e0[:, 0] * v2[:, 1] - v2[:, 0] * e0[:, 1]) / safe, -1.0)
    inside = ok & (u >= -1e-6) & (v >= -1e-6) & (u + v <= 1 + 1e-6)
    if not inside.any():
        return None
    idx = np.where(inside)[0]
    return int(face_of[idx[int(np.argmax(tri_depth[idx]))]])


def draw_view(ax, geom, tris, face_of, view, highlight=None, colors=None, label=True, title=""):
    basis = _basis(view)
    light = np.array(view, dtype=float)
    light /= np.linalg.norm(light)

    proj, depth = _project(tris, basis)
    order = np.argsort(depth.mean(axis=1))

    face_rgb = {}
    for face in geom["faces"]:
        base = 0.66
        if colors and face["index"] in colors:
            base = colors[face["index"]]
        if highlight is not None and face["index"] != highlight:
            base = 0.55
        shaded = _rgb(base) * (0.5 + 0.8 * _shade(face["normal"], light))
        # 同平面相邻面（如被槽分开的两块顶面）容易糊成一片，给每面加极小色偏
        if highlight is None:
            tint = 0.035 * (((face["index"] * 7) % 5) - 2)
            shaded = shaded + np.array([tint, -tint * 0.6, tint * 0.3])
        face_rgb[face["index"]] = np.clip(shaded, 0, 1)

    if highlight is not None:
        order = [k for k in order if int(face_of[k]) != highlight] + \
                [k for k in order if int(face_of[k]) == highlight]
    polys = [proj[k] for k in order]
    fcs = [face_rgb[int(face_of[k])] for k in order]
    ax.add_collection(PolyCollection(polys, facecolors=fcs, edgecolors="none", antialiased=False))
    ax.set_aspect("equal")
    ax.autoscale_view()
    ax.set_axis_off()
    if title:
        ax.set_title(title, fontsize=9, pad=2)
    if label:
        _label_faces(ax, geom, proj, depth, face_of, basis)


def _label_faces(ax, geom, proj, depth, face_of, basis):
    d = basis[2]
    tris_2d = proj
    tri_centroid = proj.mean(axis=1)
    tri_depth = depth.mean(axis=1)
    xlim, ylim = ax.get_xlim(), ax.get_ylim()
    span = max(xlim[1] - xlim[0], 1e-6)

    placed = []
    for face in geom["faces"]:
        n = face["normal"]
        if not n:
            continue
        nv = np.array(n, dtype=float)
        norm = np.linalg.norm(nv)
        if norm < 1e-9 or float((nv / norm) @ d) < 0.3:
            continue
        sel = np.where(face_of == face["index"])[0]
        if sel.size == 0:
            continue
        order = sel[np.argsort(-tri_depth[sel])]
        for k in order:
            pt = tri_centroid[k]
            if _front_face_at(tris_2d, tri_depth, face_of, pt) != face["index"]:
                continue
            if any(abs(pt[0] - p[0]) < 0.07 * span and abs(pt[1] - p[1]) < 0.07 * span for p in placed):
                continue
            placed.append(pt)
            ax.text(pt[0], pt[1], str(face["index"]), fontsize=8, ha="center", va="center",
                    color="#101010", zorder=5,
                    bbox=dict(boxstyle="round,pad=0.14", fc="#ffffff", ec="#666666", lw=0.5, alpha=0.88))
            break


def render_views(geom, out_path, part_no, size=(13.5, 13.5)):
    tris, face_of = _collect(geom)
    fig, axes = plt.subplots(3, 3, figsize=size, facecolor="white")
    for ax, (name, view) in zip(axes.ravel(), VIEWS.items()):
        draw_view(ax, geom, tris, face_of, view, title=name)
    for ax in axes.ravel()[len(VIEWS):]:
        ax.axis("off")
    fig.suptitle("part %s   faces=%d   %s" % (part_no, len(geom["faces"]), geom.get("source", "")),
                 fontsize=11)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140, facecolor="white")
    plt.close(fig)


def render_face_sheet(geom, out_path, part_no, cols=4):
    """逐面高亮。每个小图用「该面法向 + 斜向偏移」的视角，
    既有正面可判读性，又保留邻面作为上下文（纯正视图会丢掉"这是哪一侧"）。"""
    tris, face_of = _collect(geom)
    faces = geom["faces"]
    rows = int(math.ceil(len(faces) / float(cols)))
    fig, axes = plt.subplots(rows, cols, figsize=(cols * 3.0, rows * 2.6), facecolor="white")
    axes = np.atleast_2d(axes)
    tilt = np.array([0.62, -0.62, 0.5])
    for k, face in enumerate(faces):
        ax = axes[k // cols][k % cols]
        nv = np.array(face["normal"] or [0, 0, 1], dtype=float)
        norm = np.linalg.norm(nv)
        if norm > 1e-9:
            nv = nv / norm
        view = tuple(nv + 0.55 * tilt)
        draw_view(ax, geom, tris, face_of, view, highlight=face["index"], label=False,
                  colors={face["index"]: "#d64541"})
        ax.set_title("#%d %s  A=%.0f" % (face["index"], face["surface"], face["area_mm2"]),
                     fontsize=8, pad=1)
    for k in range(len(faces), rows * cols):
        axes[k // cols][k % cols].axis("off")
    fig.suptitle("part %s  per-face highlight (red = face)" % part_no, fontsize=11)
    fig.tight_layout()
    fig.savefig(out_path, dpi=110, facecolor="white")
    plt.close(fig)


def main(argv):
    geom_path, part_no = argv[0], argv[1]
    out_dir = argv[2] if len(argv) > 2 else "reports/annotation"
    with open(geom_path, encoding="utf-8") as fh:
        geom = json.load(fh)
    if "error" in geom:
        raise SystemExit("geom error: %s" % geom["error"][:400])
    render_views(geom, "%s/%s_views.png" % (out_dir, part_no), part_no)
    render_face_sheet(geom, "%s/%s_faces.png" % (out_dir, part_no), part_no)
    print("rendered part %s (%d faces)" % (part_no, len(geom["faces"])))


if __name__ == "__main__":
    main(sys.argv[1:])
