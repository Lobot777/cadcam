"""件 10 的离散疑问实测：z=48 / z=44 两个底面、Ø52 圆柱（面 5）归属。

用法：freecadcmd measure10.py  ->  打印 + 写 JSON 到 CADCAM_OUT
偏置量取 0.01mm 级即可：这些件的面都在整数或 0.05 网格上。
"""

import json
import os
import sys
import traceback

import FreeCAD as App  # noqa: F401
import Part


def v(t):
    return [round(float(x), 3) for x in t]


def wire_radii(wire, axis_xy):
    """线框上各顶点到轴线(垂直投影)的半径，用于判断环形面的内外边界。"""
    rs = []
    for e in wire.Edges:
        for p in (e.Vertexes[0].Point, e.Vertexes[-1].Point):
            dx = p.x - axis_xy[0]
            dy = p.y - axis_xy[1]
            rs.append((dx * dx + dy * dy) ** 0.5)
    rs.sort()
    return [round(r, 2) for r in rs]


def main():
    step = os.environ["CADCAM_STEP"]
    out = os.environ.get("CADCAM_OUT")
    shape = Part.Shape()
    shape.read(step)
    faces = shape.Faces

    axis_xy = None
    cyl = []
    for i, f in enumerate(faces):
        surf = f.Surface
        if isinstance(surf, Part.Cylinder):
            c = surf.Center
            ax = surf.Axis
            bb = f.BoundBox
            # 法向朝轴心(凹)还是背离(凸)：取参数面中点法向与径向的点积
            u0, u1, v0, v1 = f.ParameterRange
            p = f.valueAt((u0 + u1) / 2.0, (v0 + v1) / 2.0)
            n = f.normalAt((u0 + u1) / 2.0, (v0 + v1) / 2.0)
            radial = App.Vector(p.x - c.x, p.y - c.y, 0)
            if radial.Length > 1e-6:
                radial.normalize()
            dot = round(n.x * radial.x + n.y * radial.y, 3)
            cyl.append({
                "index": i + 1,
                "radius": round(surf.Radius, 3),
                "axis": v((ax.x, ax.y, ax.z)),
                "center": v((c.x, c.y, c.z)),
                "z_range": [round(bb.ZMin, 2), round(bb.ZMax, 2)],
                "area": round(f.Area, 1),
                "normal_dot_radial": dot,
                "sense": "hole(法向朝轴)" if dot < -0.5 else ("boss(法向背离轴)" if dot > 0.5 else "?"),
            })
            if axis_xy is None and abs(ax.z) > 0.99:
                axis_xy = (c.x, c.y)
    print("=== 圆柱面 ===")
    for c in cyl:
        print("  F%-2d R=%-7.2f z=[%6.2f,%6.2f] A=%7.1f dot=%+.2f  %s"
              % (c["index"], c["radius"], c["z_range"][0], c["z_range"][1],
                 c["area"], c["normal_dot_radial"], c["sense"]))
    print("REF_AXIS_XY", v(axis_xy) if axis_xy else None)

    print("=== 水平平面（按 z 分组） ===")
    planes = []
    for i, f in enumerate(faces):
        surf = f.Surface
        if not isinstance(surf, Part.Plane):
            continue
        ax = surf.Axis
        if abs(abs(ax.z) - 1.0) > 1e-6:
            continue
        bb = f.BoundBox
        n_inner = len(f.Wires) - 1
        c = f.CenterOfMass
        r = None
        if axis_xy:
            r = round(((c.x - axis_xy[0]) ** 2 + (c.y - axis_xy[1]) ** 2) ** 0.5, 2)
        rec = {"index": i + 1, "z": round(c.z, 3), "area": round(f.Area, 1),
               "centroid": v((c.x, c.y, c.z)), "normal_z": round(ax.z, 3),
               "wires": len(f.Wires), "inner_wires": n_inner,
               "r_from_axis": r, "bbox_xy": [round(bb.XMin, 1), round(bb.XMax, 1),
                                             round(bb.YMin, 1), round(bb.YMax, 1)]}
        planes.append(rec)
    planes.sort(key=lambda d: (-d["z"], d["index"]))
    for p in planes:
        print("  F%-2d z=%7.3f nz=%+d A=%8.1f wires=%d inner=%d r=%s bbox=%s"
              % (p["index"], p["z"], int(p["normal_z"]), p["area"], p["wires"],
                 p["inner_wires"], p["r_from_axis"], p["bbox_xy"]))

    print("=== 相邻关系（面 5 与谁共边） ===")
    nb = {}
    for i, f in enumerate(faces):
        nb[i + 1] = []
    for e in shape.Edges:
        try:
            fs = [faces.index(f) + 1 for f in shape.ancestorsOfType(e, Part.Face)]
        except Exception:
            fs = []
        # 用面序号更稳：逐个面查 MapShapes
        fs = []
        for i, f in enumerate(faces):
            for ee in f.Edges:
                if ee.isSame(e):
                    fs.append(i + 1)
                    break
        if len(fs) == 2:
            nb[fs[0]].append(fs[1])
            nb[fs[1]].append(fs[0])
    for k in (1, 2, 3, 4, 5, 6, 7, 8, 13, 18):
        if k in nb:
            print("  F%-2d <-> %s" % (k, sorted(set(nb[k]))))

    # 轴向连通：z=48 面与 z=44 面各自围住谁
    print("=== 环形面内边界半径 ===")
    for p in planes:
        if p["inner_wires"] > 0:
            f = faces[p["index"] - 1]
            for w in f.Wires:
                if w.isSame(f.OuterWire):
                    continue
                print("  F%-2d 内边界顶点半径: %s" % (p["index"], wire_radii(w, axis_xy)[:6]))

    if out:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump({"cylinders": cyl, "planes": planes,
                       "neighbors": {str(k): sorted(set(v_)) for k, v_ in nb.items()},
                       "axis_xy": v(axis_xy) if axis_xy else None}, fh,
                      ensure_ascii=False, indent=1)
        print("WROTE", out)


# freecadcmd 会把 __name__ 设成文件名词干（"measure10"），不是 "__main__"，
# 用 == "__main__" 判断会导致脚本静默不执行（项目里踩过一次）。
if __name__ in ("__main__", "measure10"):
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
