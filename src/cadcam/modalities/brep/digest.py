"""B-Rep 摘要（渐进式几何披露 L0–L2）。

设计目标：把几何压缩成 LLM 可消费的有限 token 表示，并支持按需下钻。
L0 零件概要（约 120 token） -> L1 特征列表（M2 产出） -> L2 面表分页（约 600 token/页）
-> L3 单面细节（按需）-> L4 渲染图。
"""

import hashlib
import json
import math
from collections import Counter, defaultdict

MAIN_DIRECTIONS = {
    "+Z": (0, 0, 1),
    "-Z": (0, 0, -1),
    "+X": (1, 0, 0),
    "-X": (-1, 0, 0),
    "+Y": (0, 1, 0),
    "-Y": (0, -1, 0),
}


def _unit_normal(normal, tol=0.9):
    if not normal:
        return None
    for name, direction in MAIN_DIRECTIONS.items():
        dot = sum(a * b for a, b in zip(normal, direction))
        if dot >= tol:
            return name
    return None


def state_hash(geom: dict) -> str:
    """几何指纹：与面/边编号无关的状态标识。

    为什么不用文件哈希：STEP 往返会重排拓扑编号、改写头信息，同一形状
    每次导出的字节都不同。而回滚是否成功、缓存是否命中，关心的是
    「形状是否相同」，不是「文件是否相同」。

    组成：拓扑计数 + 体积/面积 + 排序后的面签名（类型、面积、形心）。
    """
    counts = geom.get("counts", {})
    faces = []
    for face in geom.get("faces", []):
        centroid = face.get("centroid") or [0, 0, 0]
        faces.append((
            face.get("surface"),
            round(face.get("area_mm2") or 0.0, 2),
            round(centroid[0], 2), round(centroid[1], 2), round(centroid[2], 2),
        ))
    faces.sort()
    material = json.dumps({
        "solids": counts.get("solids"),
        "faces_count": counts.get("faces"),
        "edges_count": counts.get("edges"),
        "volume": round(geom.get("volume_mm3") or 0.0, 2),
        "area": round(geom.get("area_mm2") or 0.0, 2),
        "faces": faces,
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(material.encode("utf-8")).hexdigest()[:16]


def summarize(geom: dict) -> dict:
    """L0：零件级摘要 —— 智能体默认看到的第一屏。"""
    faces = geom.get("faces", [])
    edges = geom.get("edges", [])
    surface_hist = Counter(face["surface"] for face in faces)
    edge_hist = Counter(edge.get("curve") for edge in edges)
    convexity = Counter(edge.get("convexity") for edge in edges)

    flat_area_by_dir = Counter()
    for face in faces:
        if face["surface"] != "Plane":
            continue
        direction = _unit_normal(face.get("normal"))
        if direction:
            flat_area_by_dir[direction] += face.get("area_mm2") or 0.0

    bbox = geom.get("bbox", {})
    size = [
        round(bbox["max"][i] - bbox["min"][i], 2) if bbox.get("max") else None for i in range(3)
    ]

    return {
        "level": "L0",
        "source": geom.get("source"),
        "counts": geom.get("counts", {}),
        "bbox": bbox,
        "size_mm": size,
        "volume_mm3": geom.get("volume_mm3"),
        "surface_histogram": dict(surface_hist),
        "edge_histogram": dict(edge_hist),
        "convexity_histogram": {k: v for k, v in convexity.items() if k},
        "planar_area_by_normal": {k: round(v, 1) for k, v in sorted(flat_area_by_dir.items())},
        "machining_hints": _machining_hints(faces, edges, size, flat_area_by_dir),
    }


def _probe_points(face, edges):
    """给出可用于求径向的点。

    整 360° 圆柱的形心落在轴线上（径向长度为零），不能直接用；
    此时退化到「属于该面的一条边的中点」，它一定在曲面上。
    """
    points = []
    if face.get("centroid"):
        points.append(face["centroid"])
    for edge in edges:
        if face["index"] in (edge.get("faces") or []) and edge.get("midpoint"):
            points.append(edge["midpoint"])
    return points


def _radial_dot(face, edges=()):
    """圆柱/圆锥面：法向与径向的点积符号。>0 外凸，<0 内凹（孔）。

    这是首选判据：孔壁的法向指向轴线（朝材料外），凸台/外轮廓圆弧的法向
    背离轴线。比数边更本质，且不受端面边数影响。
    """
    params = face.get("params") or {}
    axis, location = params.get("axis"), params.get("location")
    normal = face.get("normal")
    if not (axis and location and normal):
        return None
    for point in _probe_points(face, edges):
        delta = [point[i] - location[i] for i in range(3)]
        along = sum(delta[i] * axis[i] for i in range(3))
        radial = [delta[i] - along * axis[i] for i in range(3)]
        length = math.sqrt(sum(v * v for v in radial))
        if length < 1e-6:
            continue
        return sum(normal[i] * radial[i] for i in range(3)) / length
    return None


def _cylinder_polarity(faces, edges):
    """区分「凹圆柱（孔/型腔壁）」与「凸圆柱（凸台/外轮廓圆弧）」。

    两个判据按优先级串行：几何（法向·径向）→ 拓扑（边界边凸凹投票）。
    几何判据失效（参数缺失）时才退回投票。
    """
    votes = defaultdict(lambda: {"concave": 0, "convex": 0})
    for edge in edges:
        kind = edge.get("convexity")
        if kind not in ("concave", "convex"):
            continue
        for face_index in edge.get("faces", []):
            votes[face_index][kind] += 1

    holes, bosses, undecided = Counter(), Counter(), Counter()
    for face in faces:
        if face["surface"] not in ("Cylinder", "Cone"):
            continue
        radius = face.get("params", {}).get("radius")
        if not radius:
            continue

        dot = _radial_dot(face, edges)
        # 用符号判定，阈值只用于滤掉探针点与法向周向错位导致的近零噪声
        if dot is not None and abs(dot) > 0.05:
            (holes if dot < 0 else bosses)[radius] += 1
            continue

        tally = votes.get(face["index"], {"concave": 0, "convex": 0})
        if tally["concave"] > tally["convex"]:
            holes[radius] += 1
        elif tally["convex"] > tally["concave"]:
            bosses[radius] += 1
        else:
            undecided[radius] += 1
    return _sorted_radii(holes), _sorted_radii(bosses), _sorted_radii(undecided)


def _sorted_radii(counter):
    """半径 -> 面数，按半径升序。同一半径可能是孔也可能是圆角，所以必须保留实例数。"""
    return [{"radius_mm": radius, "count": count} for radius, count in sorted(counter.items())]


def _machining_hints(faces, edges, size, flat_area_by_dir):
    hints = []
    top_area = flat_area_by_dir.get("+Z", 0.0)
    if size and size[0] and size[1]:
        footprint = size[0] * size[1]
        if footprint:
            ratio = top_area / footprint
            hints.append({
                "access": "+Z",
                "openness": round(min(ratio, 1.0), 3),
                "note": "朝 +Z 的平面占底面积比例，越高越适合单次装夹三轴加工",
            })
    holes, bosses, undecided = _cylinder_polarity(faces, edges)
    if holes:
        hints.append({"hole_like_radii_mm": holes,
                      "note": "凹圆柱面（孔/型腔壁）：radius_mm 与面数 count，可直接映射到钻头/铣刀半径"})
    if bosses:
        hints.append({"boss_like_radii_mm": bosses,
                      "note": "凸圆柱面（凸台/外轮廓圆弧），不构成孔，勿按孔加工"})
    if undecided:
        hints.append({"ambiguous_cylinder_radii_mm": undecided,
                      "note": "边界边凸凹持平，需下钻 L3 查看单面细节再判定"})
    return hints


def face_page(geom: dict, page: int = 1, size: int = 20) -> dict:
    """L2：面表分页，每页默认 20 面。"""
    faces = geom.get("faces", [])
    start = max(0, (page - 1) * size)
    window = faces[start:start + size]
    rows = []
    for face in window:
        rows.append({
            "id": face["id"],
            "i": face["index"],
            "type": face["surface"],
            "area": round(face.get("area_mm2") or 0.0, 1),
            "normal": _unit_normal(face.get("normal")) or "free",
            "c": [round(value, 1) for value in face.get("centroid", [])],
            "edges": face.get("edge_count"),
        })
    return {
        "level": "L2",
        "page": page,
        "page_size": size,
        "total_faces": len(faces),
        "total_pages": max(1, (len(faces) + size - 1) // size),
        "rows": rows,
    }


def face_detail(geom: dict, face_id: str) -> dict:
    """L3：单面细节，含参数与相邻边（凸凹）。"""
    for face in geom.get("faces", []):
        if face["id"] == face_id or str(face["index"]) == str(face_id):
            edges = [
                edge for edge in geom.get("edges", [])
                if face["index"] in edge.get("faces", [])
            ]
            return {
                "level": "L3",
                "face": face,
                "edges": edges,
            }
    return {"level": "L3", "error": "FACE_NOT_FOUND", "face_id": face_id}


def _render_radii(items):
    """R3×4 比 [3.0, 3.0, 3.0, 3.0] 省 token，也比 [3.0] 多保留实例数。"""
    return ", ".join("R%s×%d" % (_trim(item["radius_mm"]), item["count"]) for item in items)


def _trim(value):
    return ("%.2f" % value).rstrip("0").rstrip(".")


def render_l0(summary: dict) -> str:
    """把 L0 渲染成给 LLM 的紧凑文本块。"""
    size = summary.get("size_mm") or [0, 0, 0]
    counts = summary.get("counts", {})
    surfaces = ", ".join("%s×%d" % (k, v) for k, v in summary.get("surface_histogram", {}).items())
    convex = ", ".join("%s×%d" % (k, v) for k, v in summary.get("convexity_histogram", {}).items())
    lines = [
        "零件 %s" % summary.get("source", "?"),
        "尺寸 %.1f×%.1f×%.1f mm，体积 %.0f mm³，面 %d / 边 %d" % (
            size[0] or 0, size[1] or 0, size[2] or 0,
            summary.get("volume_mm3") or 0, counts.get("faces", 0), counts.get("edges", 0)),
        "曲面构成: %s" % (surfaces or "无"),
        "棱边凸凹: %s" % (convex or "未判定"),
    ]
    for hint in summary.get("machining_hints", []):
        if "openness" in hint:
            lines.append("加工方向 %s 开敞度 %.2f" % (hint["access"], hint["openness"]))
        if "hole_like_radii_mm" in hint:
            lines.append("圆孔类: %s" % _render_radii(hint["hole_like_radii_mm"]))
        if "boss_like_radii_mm" in hint:
            lines.append("凸圆柱（非孔）: %s" % _render_radii(hint["boss_like_radii_mm"]))
        if "ambiguous_cylinder_radii_mm" in hint:
            lines.append("待定圆柱: %s" % _render_radii(hint["ambiguous_cylinder_radii_mm"]))
    return "\n".join(lines)
