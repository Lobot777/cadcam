"""把面级类别预测聚成特征实例。

MFCAD++ 只标注「这个面属于哪类特征」，不标注「哪些面属于同一个特征」。
但工艺规划需要的恰恰是后者：三个 Ø6 孔是三道工序，不是一道。

做法是同标签 + 拓扑相邻取连通分量。这个近似在**相交特征**上会失效
（两个相邻的型腔会把壁面粘成一个实例），所以额外报告
``merge_suspect`` 标志，让上层智能体知道该下钻核实，而不是盲信。
"""

from collections import defaultdict

from cadcam.modalities.brep.step_labels import FEATURE_FAMILY, MFCAD_LABELS

DEFAULT_FAMILIES = {
    index: FEATURE_FAMILY.get(name, "unknown") for index, name in MFCAD_LABELS.items()
}

# 工艺族到我们网关动作的映射：模型输出 25 类，网关只有少数几个动作
FAMILY_TO_ACTION = {
    "hole": "cad.hole",
    "pocket": "cad.pocket",
    "pocket_through": "cad.pocket",
    "slot": "cad.pocket",
    "step": "cad.pocket",
    "chamfer": "cad.chamfer",
    "fillet": "cad.fillet",
    "groove": "cad.pocket",
    "stock": None,
}


def _adjacency(geom):
    """面索引 -> 与之共享边的面索引集合。"""
    neighbours = defaultdict(set)
    for edge in geom.get("edges", []):
        ends = edge.get("faces") or []
        if len(ends) != 2:
            continue
        a, b = ends
        neighbours[a].add(b)
        neighbours[b].add(a)
    return neighbours


def group(geom, labels, families=None, min_faces=1):
    """按 (类别, 连通性) 聚合实例。

    labels 与 ``geom["faces"]`` 同序；families 给出每类的工艺族名，
    缺省时按类别号原样分组。
    """
    faces = geom.get("faces", [])
    if len(labels) != len(faces):
        raise ValueError("标签数 %d 与面数 %d 不一致" % (len(labels), len(faces)))
    families = DEFAULT_FAMILIES if families is None else families

    neighbours = _adjacency(geom)
    by_index = {face["index"]: face for face in faces}
    order = [face["index"] for face in faces]
    label_of = {face["index"]: labels[position] for position, face in enumerate(faces)}

    seen = set()
    instances = []

    for start in order:
        if start in seen:
            continue
        label = label_of[start]
        # 广度优先，只在同标签的面上扩散
        stack, members = [start], []
        seen.add(start)
        while stack:
            current = stack.pop()
            members.append(current)
            for neighbour in neighbours.get(current, ()):
                if neighbour in seen or label_of.get(neighbour) != label:
                    continue
                seen.add(neighbour)
                stack.append(neighbour)

        if len(members) < min_faces:
            continue
        instances.append(_describe(geom, by_index, members, label, families))

    instances.sort(key=lambda item: (item["family"], -item["face_count"]))
    return instances


def _describe(geom, by_index, members, label, families):
    members = sorted(members)
    area = 0.0
    lows, highs = [], []
    for index in members:
        face = by_index[index]
        area += face.get("area_mm2") or 0.0
        # 必须用面的包围盒，不能用形心：单个平面时形心只有一个点，
        # 算出来的实例包围盒是 0×0×0，对工艺规划毫无意义
        extent = face.get("bbox")
        if extent and len(extent) == 6:
            lows.append(extent[:3])
            highs.append(extent[3:])
        elif face.get("centroid"):
            lows.append(face["centroid"])
            highs.append(face["centroid"])

    box = None
    if lows:
        low = [min(p[i] for p in lows) for i in range(3)]
        high = [max(p[i] for p in highs) for i in range(3)]
        box = {
            "min": [round(v, 3) for v in low],
            "max": [round(v, 3) for v in high],
            "size": [round(high[i] - low[i], 3) for i in range(3)],
        }

    family = (families or {}).get(label, "class_%s" % label)
    # 半径来自圆柱面，直接映射到钻头/铣刀直径，这一步对工艺最有用
    radii = sorted(
        {
            round(by_index[i]["params"]["radius"], 3)
            for i in members
            if by_index[i].get("params", {}).get("radius")
        }
    )

    instance = {
        "class_id": label,
        "family": family,
        "faces": members,
        "face_count": len(members),
        "area_mm2": round(area, 2),
        "bbox": box,
        "action": FAMILY_TO_ACTION.get(family),
    }
    if radii:
        instance["radii_mm"] = radii
        instance["diameter_mm"] = round(2 * radii[0], 3)

    # 只由一个面构成、且该面是封闭圆柱时，几乎可以肯定是孔
    if len(members) == 1:
        surface = by_index[members[0]].get("surface")
        if surface in ("Cylinder", "Cone"):
            instance["geometry"] = "cylindrical_single_face"
    return instance


def summarize(instances):
    """按族汇总，用于给 LLM 一段紧凑的 L0.5 描述。"""
    tally = defaultdict(list)
    for instance in instances:
        tally[instance["family"]].append(instance)

    lines = []
    for family, items in sorted(tally.items(), key=lambda kv: -len(kv[1])):
        diameters = sorted(
            {item["diameter_mm"] for item in items if item.get("diameter_mm")}
        )
        detail = ""
        if diameters:
            detail = "（Ø%s）" % "/".join("%g" % d for d in diameters)
        lines.append("%s ×%d%s" % (family, len(items), detail))
    return {
        "count": len(instances),
        "by_family": {family: len(items) for family, items in tally.items()},
        "text": "；".join(lines) if lines else "无加工特征",
    }
