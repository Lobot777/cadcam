"""导出件 10 的剖切线框（含过轴线的 XZ / YZ 剖面 + 45° 斜剖面）。

单点 isInside 在薄壁与星形壁处会漏判（1mm 立方可能只压到边），
剖切面是整段几何，能一次看清 z=44 / z=48 两个底面各自的径向范围。
"""

import json
import os
import sys
import traceback

import FreeCAD as App  # noqa: F401
import Part

AX = (-1.0, 1.0)


def wire_poly(w, plane):
    """把 3D 线框采样成 2D 折线（沿 plane 指定的两个轴）。"""
    pts = []
    for e in w.Edges:
        n = max(2, int(e.Length / 0.4) + 1)
        for k in range(n):
            p = e.valueAt(e.FirstParameter + (e.LastParameter - e.FirstParameter) * k / (n - 1.0))
            pts.append((round(p[plane[0]], 3), round(p[plane[1]], 3)))
    # 按最近点排序串成闭合环
    if not pts:
        return []
    out = [pts.pop(0)]
    while pts:
        last = out[-1]
        best = min(range(len(pts)), key=lambda i: (pts[i][0] - last[0]) ** 2 + (pts[i][1] - last[1]) ** 2)
        out.append(pts.pop(best))
    return out


def main():
    shape = Part.Shape()
    shape.read(os.environ["CADCAM_STEP"])
    secs = {}
    # normal (0,1,0) offset 1.0 -> 平面 y=1，看图取 (x,z)
    for tag, normal, off, plane in [
        ("xz", (0, 1, 0), AX[1], (0, 2)),
        ("yz", (1, 0, 0), AX[0], (1, 2)),
        ("d45", (0.7071, -0.7071, 0), 0.7071 * (AX[0] - AX[1]), None),
    ]:
        try:
            wires = shape.slice(App.Vector(*normal), off)
        except Exception:
            traceback.print_exc()
            wires = []
        polys = []
        for w in wires:
            if plane is None:
                # 斜剖面：先把点投到 (u, z)
                pts = []
                for e in w.Edges:
                    n = max(2, int(e.Length / 0.4) + 1)
                    for k in range(n):
                        p = e.valueAt(e.FirstParameter + (e.LastParameter - e.FirstParameter) * k / (n - 1.0))
                        u = (p.x - AX[0]) * 0.7071 + (p.y - AX[1]) * -0.7071
                        pts.append((round(u, 3), round(p.z, 3)))
                pts.sort()
                if pts:
                    polys.append(pts)
                    continue
            polys.append(wire_poly(w, plane))
        secs[tag] = polys
        print("%s: %d wires, pts=%s" % (tag, len(polys), [len(p) for p in polys]))

    out = os.environ.get("CADCAM_OUT")
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump({"axis": AX, "sections": secs}, fh)
        print("WROTE", out)


if __name__ in ("__main__", "sec2d"):
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
