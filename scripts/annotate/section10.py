"""截面面积交叉验证：isInside 在含内腔/薄壁时不可靠，改用布尔交 + 切片面积。

slice(z) 返回该高度的截面线框；直接算截面积，能区分
「圆盘 / 圆环 / 花形环」，比单点探针稳。
"""

import os
import sys
import traceback

import FreeCAD as App  # noqa: F401
import Part

AX = (-1.0, 1.0)


def section_area(shape, z):
    wires = shape.slice(App.Vector(0, 0, 1), z)
    out = []
    for w in wires:
        try:
            f = Part.Face(w)
            a = abs(f.Area)
        except Exception:
            a = None
        bb = w.BoundBox
        if a is not None:
            out.append({"area": round(a, 1), "r_min": round(min(
                ((q.Point.x - AX[0]) ** 2 + (q.Point.y - AX[1]) ** 2) ** 0.5 for q in w.Vertexes), 2) if w.Vertexes else None,
                "r_max": round(max(
                ((q.Point.x - AX[0]) ** 2 + (q.Point.y - AX[1]) ** 2) ** 0.5 for q in w.Vertexes), 2) if w.Vertexes else None,
                "n_edges": len(w.Edges)})
    return out


def cube_volume_at(shape, x, y, z, h=0.5):
    box = Part.makeBox(2 * h, 2 * h, 2 * h, App.Vector(x - h, y - h, z - h))
    return round(shape.common(box).Volume, 4)


def main():
    shape = Part.Shape()
    shape.read(os.environ["CADCAM_STEP"])
    print("solids=%d shells=%d faces=%d volume=%.1f" % (
        len(shape.Solids), len(shape.Shells), len(shape.Faces), shape.Volume))
    for z in (43, 44.5, 46, 47, 48.5, 50, 51):
        s = section_area(shape, z)
        print("z=%5.1f  截面积合计=%9.1f  环数=%d" % (z, sum(x["area"] for x in s), len(s)))
        for x in s:
            print("        A=%9.1f  r∈[%s, %s]  edges=%d" % (x["area"], x["r_min"], x["r_max"], x["n_edges"]))
    print("\n布尔交体积（1mm 立方）")
    for (r, z) in [(0, 46), (0, 47.8), (0, 50), (10, 46), (20, 46), (23, 46),
                   (27, 46), (27, 50), (30, 50), (33, 50), (36, 50), (36, 46),
                   (0, 44.2), (20, 44.2), (27, 44.2)]:
        p = App.Vector(AX[0] + r, AX[1], z)
        print("   r=%5.1f z=%5.1f  vol=%.4f" % (r, z, cube_volume_at(shape, p.x, p.y, z)))


if __name__ in ("__main__", "section10"):
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
