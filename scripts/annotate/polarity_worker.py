"""批量量测圆柱面的「径向点积」极性（在 freecadcmd 里跑一次，处理 10 个 STEP）。

为什么需要：geometry JSON 里的 face.normal 对圆柱面只是面上一点的法向方向，
而且题目包把 params 裁掉了圆柱轴心 location，受试者无法自己算 n·radial。
这里从 STEP 直接取圆柱面的参数中点法向，与该点的（垂直于轴的）径向单位向量点乘：

    dot < -0.5   法向朝轴心      凹 = 孔 / 沉孔 / 腔壁
    dot > +0.5   法向背离轴心    凸 = 销 / 圆柱凸台 / 岛外壁
    |dot| <= 0.5 退化（几乎不该出现），标记 unknown

用法（Windows）：
  CADCAM_DATA=<data/zenodo_rptu> CADCAM_OUT=<reports/annotation/polarity.json> \
    freecadcmd.exe scripts/annotate/polarity_worker.py

产物： { "<part>": { "<face_index>": { "dot": ..., "sense": ..., "radius": ..., "axis": ... } } }
只记录 Cylinder 面；面号与 geom/<part>.json 的 faces[].index 一致（都是从 1 开始）。
"""

import json
import os
import traceback

import FreeCAD as App
import Part

PARTS = [str(i) for i in range(1, 11)]


def v(t, d=4):
    return [round(float(x), d) for x in t]


def dot_of(face):
    surf = face.Surface
    c = surf.Center
    ax = App.Vector(surf.Axis.x, surf.Axis.y, surf.Axis.z)
    if ax.Length < 1e-9:
        return None
    ax.normalize()
    u0, u1, v0, v1 = face.ParameterRange
    um, vm = (u0 + u1) / 2.0, (v0 + v1) / 2.0
    p = face.valueAt(um, vm)
    n = face.normalAt(um, vm)
    rel = App.Vector(p.x - c.x, p.y - c.y, p.z - c.z)
    # 投影到垂直于轴的平面，才是真正的径向
    radial = rel - ax * (rel.x * ax.x + rel.y * ax.y + rel.z * ax.z)
    if radial.Length < 1e-6:
        return None
    radial.normalize()
    d = n.x * radial.x + n.y * radial.y + n.z * radial.z
    return round(float(d), 3)


def main():
    data = os.environ["CADCAM_DATA"]
    out = os.environ["CADCAM_OUT"]
    result = {}
    for part in PARTS:
        step = os.path.join(data, "%s.step" % part)
        if not os.path.exists(step):
            print("skip", part, "(no STEP)")
            continue
        shape = Part.Shape()
        shape.read(step)
        rows = {}
        for i, f in enumerate(shape.Faces, start=1):
            if not isinstance(f.Surface, Part.Cylinder):
                continue
            d = dot_of(f)
            if d is None:
                sense = "unknown"
            elif d < -0.5:
                sense = "concave"
            elif d > 0.5:
                sense = "convex"
            else:
                sense = "unknown"
            rows[str(i)] = {
                "dot": d,
                "sense": sense,
                "radius": round(float(f.Surface.Radius), 3),
                "axis": v((f.Surface.Axis.x, f.Surface.Axis.y, f.Surface.Axis.z), 3),
            }
        result[part] = rows
        print("part %-3s faces=%-3d cylinders=%-3d  %s"
              % (part, len(shape.Faces), len(rows),
                 ", ".join("F%s:%+.2f" % (k, x["dot"]) for k, x in list(rows.items())[:5]
                           if x["dot"] is not None)))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
    print("wrote", out)


if __name__ in ("__main__", "polarity_worker"):
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise
