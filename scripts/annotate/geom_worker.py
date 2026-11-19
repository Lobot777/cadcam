"""标注专用几何 worker：在 FreeCAD 解释器里跑，输出带三角剖分的面表。

与生产用的 ``cadcam.adapters.freecad_worker`` 的区别只有一条：
**多输出一层三角面片**，供宿主侧离线渲染带面号的三视图。
其余字段（index / surface / area / normal / edges / convexity）口径保持一致，
避免标注用的几何和训练用的几何是两套东西。

用法：
    set CADCAM_STEP=1.step & set CADCAM_OUT=1.geom.json
    FreeCADCmd.exe geom_worker.py
显式传参会被 Boost program_options 抢走（踩过的坑），故只走环境变量。
"""

import json
import math
import os
import sys
import traceback

import Part


def _r(v, d=4):
    try:
        return round(float(v), d)
    except Exception:
        return None


def _vec(v, d=4):
    return [_r(v.x, d), _r(v.y, d), _r(v.z, d)]


def _normal_at(face, point):
    try:
        u, v = face.Surface.parameter(point)
        return face.normalAt(u, v)
    except Exception:
        return None


def _classify(edge, fa, fb, ca, cb):
    mid = edge.CenterOfMass
    na = _normal_at(fa, mid)
    nb = _normal_at(fb, mid)
    if na is None or nb is None:
        return None
    na, nb = _vec(na), _vec(nb)
    m = (mid.x, mid.y, mid.z)
    score = sum(na[i] * (cb[i] - m[i]) for i in range(3))
    score += sum(nb[i] * (ca[i] - m[i]) for i in range(3))
    if abs(score) < 1e-6:
        return "smooth"
    return "concave" if score > 0 else "convex"


def build(step_path):
    shape = Part.Shape()
    shape.read(step_path)
    box = shape.BoundBox
    out = {
        "source": os.path.basename(step_path),
        "bbox": {"min": [_r(box.XMin), _r(box.YMin), _r(box.ZMin)],
                 "max": [_r(box.XMax), _r(box.YMax), _r(box.ZMax)]},
        "volume_mm3": _r(shape.Volume, 2),
        "area_mm2": _r(shape.Area, 2),
        "faces": [],
        "edges": [],
    }

    faces = list(shape.Faces)
    centroids = []
    for i, face in enumerate(faces, start=1):
        c = face.CenterOfMass
        centroids.append((c.x, c.y, c.z))

    edge_map = {}
    for edge in shape.Edges:
        try:
            key = (edge.hashCode(), round(edge.Length, 2))
        except Exception:
            continue
        edge_map.setdefault(key, {"edge": edge, "faces": []})
        # 面号在下面的循环里补

    # 先建 面 -> 边 的映射，再反查 边 -> 面
    for i, face in enumerate(faces, start=1):
        for edge in face.Edges:
            try:
                key = (edge.hashCode(), round(edge.Length, 2))
            except Exception:
                continue
            edge_map.setdefault(key, {"edge": edge, "faces": []})
            if i not in edge_map[key]["faces"]:
                edge_map[key]["faces"].append(i)

    for i, face in enumerate(faces, start=1):
        c = face.CenterOfMass
        normal = _normal_at(face, c)
        fb = face.BoundBox
        surf = face.Surface
        family = type(surf).__name__
        params = {"family": family}
        try:
            if family == "Plane":
                params["normal"] = _vec(surf.Axis)
                params["offset"] = _r(surf.Axis.dot(surf.Position))
            elif family in ("Cylinder", "Cone"):
                params["axis"] = _vec(surf.Axis)
                params["radius"] = _r(getattr(surf, "Radius", None))
                params["location"] = _vec(surf.Center)
                if family == "Cone":
                    params["semi_angle"] = _r(getattr(surf, "SemiAngle", None))
            elif family == "Toroid":
                params["axis"] = _vec(surf.Axis)
                params["major_radius"] = _r(surf.MajorRadius)
                params["minor_radius"] = _r(surf.MinorRadius)
        except Exception:
            params["partial"] = True

        # 三角剖分：标注渲染只做人眼判读，0.3mm 偏差足够
        tri = []
        try:
            verts, tris = face.tessellate(0.3)
            # tris 的每个元素是顶点下标三元组，不能直接拿它去索引 list（踩过一次）
            getv = lambda i: [round(verts[i].x, 3), round(verts[i].y, 3), round(verts[i].z, 3)]
            tri = [[getv(t[0]), getv(t[1]), getv(t[2])] for t in tris]
        except Exception:
            tri = []

        out["faces"].append({
            "index": i,
            "surface": family,
            "area_mm2": _r(face.Area, 3),
            "centroid": _vec(c),
            "normal": _vec(normal) if normal is not None else None,
            "bbox": [_r(fb.XMin), _r(fb.YMin), _r(fb.ZMin), _r(fb.XMax), _r(fb.YMax), _r(fb.ZMax)],
            "edge_count": len(face.Edges),
            "params": params,
            "triangles": tri,
        })

    for key, slot in edge_map.items():
        edge = slot["edge"]
        adjacent = slot["faces"]
        rec = {
            "curve": "None",
            "length_mm": None,
            "midpoint": None,
            "faces": adjacent,
            "convexity": None,
        }
        try:
            rec["curve"] = type(edge.Curve).__name__
        except Exception:
            pass
        try:
            rec["length_mm"] = _r(edge.Length, 3)
        except Exception:
            pass
        try:
            mid = edge.CenterOfMass
            rec["midpoint"] = _vec(mid)
        except Exception:
            pass
        if len(adjacent) == 2:
            try:
                rec["convexity"] = _classify(edge, faces[adjacent[0] - 1], faces[adjacent[1] - 1],
                                             centroids[adjacent[0] - 1], centroids[adjacent[1] - 1])
            except Exception:
                rec["convexity"] = None
        out["edges"].append(rec)

    return out


def main():
    step = os.environ.get("CADCAM_STEP")
    out = os.environ.get("CADCAM_OUT")
    if not step or not out:
        print("need CADCAM_STEP / CADCAM_OUT")
        return 2
    try:
        payload = build(step)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        print("OK faces=%d" % len(payload["faces"]))
        return 0
    except Exception:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump({"error": traceback.format_exc()}, fh, ensure_ascii=False)
        return 1


# FreeCADCmd 下 __name__ 可能是 "__main__" 也可能是文件名词干，这里无条件执行。
main()
