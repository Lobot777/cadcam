"""FreeCAD 几何抽取 worker。

运行在 FreeCAD 自带解释器内（0.20 -> Python 3.8，1.1 -> Python 3.11），
因此本文件不得使用 3.9+ 语法（无 match、无内置泛型注解、无 X | Y）。

用法：
    FreeCADCmd.exe freecad_worker.py --step part.step --out part.json
输出为 UTF-8 JSON，供宿主侧适配器解析。
"""

import argparse
import hashlib
import json
import os
import sys
import traceback

import Part


def _round(value, digits=4):
    try:
        return round(float(value), digits)
    except Exception:
        return None


def _vec(vector, digits=4):
    return [_round(vector.x, digits), _round(vector.y, digits), _round(vector.z, digits)]


def _surface_family(surface):
    return type(surface).__name__


def _surface_params(surface):
    """抽取曲面特征参数，只保留对加工决策有意义的部分。"""
    family = _surface_family(surface)
    params = {"family": family}
    try:
        if family == "Plane":
            params["normal"] = _vec(surface.Axis)
            params["offset"] = _round(surface.Axis.dot(surface.Position))
        elif family in ("Cylinder", "Cone"):
            params["axis"] = _vec(surface.Axis)
            params["radius"] = _round(getattr(surface, "Radius", None))
            if family == "Cone":
                params["semi_angle"] = _round(getattr(surface, "SemiAngle", None))
            params["location"] = _vec(surface.Center)
        elif family == "Sphere":
            params["radius"] = _round(getattr(surface, "Radius", None))
            params["center"] = _vec(surface.Center)
        elif family == "Toroid":
            params["axis"] = _vec(surface.Axis)
            params["major_radius"] = _round(getattr(surface, "MajorRadius", None))
            params["minor_radius"] = _round(getattr(surface, "MinorRadius", None))
    except Exception:
        params["partial"] = True
    return params


def _fingerprint(family, params, area, centroid):
    payload = "|".join(
        [
            family,
            "%.3f" % (area or 0.0),
            "%.3f,%.3f,%.3f" % tuple(centroid),
            json.dumps(params, sort_keys=True),
        ]
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def _normal_at(face, point):
    """取面上某点的外法向；参数域外时返回 None。"""
    try:
        u, v = face.Surface.parameter(point)
        return face.normalAt(u, v)
    except Exception:
        return None


def _edge_key(edge):
    mid = edge.CenterOfMass
    return (edge.hashCode(), round(edge.Length, 2), round(mid.x, 1), round(mid.y, 1), round(mid.z, 1))


def _classify_convexity(edge, face_a, face_b, centroid_a, centroid_b):
    """在边中点判定凹/凸。

    score = n1·(c2 - m) + n2·(c1 - m)
    score > 0 -> 凹（材料在内侧，典型如型腔转角）
    score < 0 -> 凸（外轮廓棱边）
    对自由曲面仅是近似，调用方需按 confidence 使用。
    """
    midpoint = edge.CenterOfMass
    normal_a = _normal_at(face_a, midpoint)
    normal_b = _normal_at(face_b, midpoint)
    if normal_a is None or normal_b is None:
        return None
    na, nb = _vec(normal_a), _vec(normal_b)
    m = (midpoint.x, midpoint.y, midpoint.z)
    score = sum(na[i] * (centroid_b[i] - m[i]) for i in range(3))
    score += sum(nb[i] * (centroid_a[i] - m[i]) for i in range(3))
    if abs(score) < 1e-6:
        return "smooth"
    return "concave" if score > 0 else "convex"


def extract(path):
    shape = Part.Shape()
    shape.read(path)
    return extract_shape(shape, source=path)


def extract_shape(shape, source="<shape>"):
    """对已有 Shape 做规范化抽取。

    建模 worker 与本 worker 共用这一条路径，保证"读 STEP 得到的几何"
    与"建模后得到的几何"口径完全一致 —— 否则前后置条件会在两套口径间漂移。
    """
    path = source
    box = shape.BoundBox
    payload = {
        "source": path,
        "units": "mm",
        "counts": {
            "solids": len(shape.Solids),
            "shells": len(shape.Shells),
            "faces": len(shape.Faces),
            "edges": len(shape.Edges),
            "vertices": len(shape.Vertexes),
        },
        "bbox": {
            "min": [_round(box.XMin), _round(box.YMin), _round(box.ZMin)],
            "max": [_round(box.XMax), _round(box.YMax), _round(box.ZMax)],
        },
        "volume_mm3": _round(shape.Volume, 2),
        "area_mm2": _round(shape.Area, 2),
        "faces": [],
        "edges": [],
    }

    face_by_index = {}
    face_records = []
    edge_to_faces = {}

    for index, face in enumerate(shape.Faces, start=1):
        face_by_index[index] = face
        family = _surface_family(face.Surface)
        params = _surface_params(face.Surface)
        centroid = _vec(face.CenterOfMass)
        area = _round(face.Area, 3)
        normal = _normal_at(face, face.CenterOfMass)
        record = {
            "id": _fingerprint(family, params, area, centroid),
            "index": index,
            "surface": family,
            "area_mm2": area,
            "centroid": centroid,
            "bbox": [_round(face.BoundBox.XMin), _round(face.BoundBox.YMin), _round(face.BoundBox.ZMin),
                     _round(face.BoundBox.XMax), _round(face.BoundBox.YMax), _round(face.BoundBox.ZMax)],
            "edge_count": len(face.Edges),
            "params": params,
            "normal": _vec(normal) if normal is not None else None,
        }
        face_records.append(record)
        for edge in face.Edges:
            slot = edge_to_faces.setdefault(_edge_key(edge), {"edge": edge, "faces": []})
            slot["faces"].append(index)

    edge_records = []
    for key, slot in edge_to_faces.items():
        edge = slot["edge"]
        adjacent = slot["faces"]
        record = {
            "id": hashlib.sha1(str(key).encode("utf-8")).hexdigest()[:12],
            "curve": _safe_curve(edge),
            "length_mm": _safe_length(edge),
            "midpoint": _safe_midpoint(edge),
            "faces": adjacent,
            "convexity": None,
        }
        if len(adjacent) == 2:
            a, b = adjacent[0], adjacent[1]
            try:
                record["convexity"] = _classify_convexity(
                    edge, face_by_index[a], face_by_index[b],
                    face_records[a - 1]["centroid"], face_records[b - 1]["centroid"],
                )
            except Exception:
                # 凸凹判定失败不该让整件零件作废：留 None，下游按「未知」处理
                record["convexity"] = None
        edge_records.append(record)

    payload["faces"] = face_records
    payload["edges"] = edge_records
    return payload


def _safe_curve(edge):
    """退化边没有 Curve 对象，OCC 会抛 "undefined curve type"。

    MFCAD++ 里这类边真实存在（自动生成的零件常见退化几何），
    不能让一条坏边否掉整个零件。
    """
    try:
        return type(edge.Curve).__name__
    except Exception:
        return "None"


def _safe_length(edge):
    try:
        return _round(edge.Length, 3)
    except Exception:
        return None


def _safe_midpoint(edge):
    try:
        return _vec(edge.CenterOfMass)
    except Exception:
        return None


def main(argv):
    step = os.environ.get("CADCAM_STEP")
    out = os.environ.get("CADCAM_OUT")
    if not step or not out:
        parser = argparse.ArgumentParser()
        parser.add_argument("--step", required=True)
        parser.add_argument("--out", required=True)
        args = parser.parse_args(argv)
        step, out = args.step, args.out

    try:
        payload = extract(step)
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1)
        print("OK faces=%d edges=%d" % (len(payload["faces"]), len(payload["edges"])))
        return 0
    except Exception:
        with open(out, "w", encoding="utf-8") as handle:
            json.dump({"error": traceback.format_exc(), "source": step}, handle, ensure_ascii=False)
        return 1


# FreeCADCmd 执行脚本时 __name__ 等于文件名词干（不是 "__main__"），
# 且会静默吞掉异常并返回 0 —— 因此这里用"非包导入则执行"的判定。
if __name__ not in ("cadcam.adapters.freecad_worker",):
    main(sys.argv[1:])
