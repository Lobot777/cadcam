"""把几何摘要转成面邻接图（Attributed Adjacency Graph, AAG）。

为什么不喂点云或体素：加工特征的判别依据是**面之间的邻接关系**。
一个 Ø6 圆柱面既可能是孔壁也可能是凸台，单看这个面永远分不出来，
只有看它与邻面的交线是凹还是凸才行。图和这件事是同构的，
点云把拓扑关系丢了，所以精度上不去（MFCAD++ 上 PointNet++ 约 86%，
图方法约 97%+）。

节点 = 面，边 = 拓扑邻接。特征全部对本件毛坯包围盒归一化，
这样同一套权重能同时处理 10mm 和 50mm 的零件。
"""

import math

# 节点特征维度：类型 one-hot(5) + 面积 + 形心(3) + 法向(3)
#               + 轴对齐 + 半径 + 轴向 + 边数 + 凹边比 + 凸边比 = 18
#
# 最后两维是后加的：零样本跑真实零件时发现，模型把外轮廓圆角判成孔，
# 置信度还有 0.93。原因是它只能从邻边的特征间接推断这个面是凹是凸，
# 而「凹圆柱=孔、凸圆柱=凸台/圆角」恰恰是最强的判别信号。直接给进去。
NODE_DIM = 18
EDGE_DIM = 4

SURFACE_TYPES = ("Plane", "Cylinder", "Cone", "Torus", "Other")

# 数据集里毛坯是 10–50mm 的长方体，归一化用包围盒对角线
_EPS = 1e-6


def _surface_slot(name):
    return SURFACE_TYPES.index(name) if name in SURFACE_TYPES else SURFACE_TYPES.index("Other")


def _norm_point(point, origin, scale):
    return [(point[i] - origin[i]) / scale for i in range(3)]


def build(geom, labels=None):
    """构建一件零件的 AAG。

    labels 若给出（与 ``geom["faces"]`` 同序的类别 id 列表），则同时返回 y。
    """
    faces = geom.get("faces", [])
    if not faces:
        return None

    bbox = geom.get("bbox") or {}
    low = bbox.get("min") or [0.0, 0.0, 0.0]
    high = bbox.get("max") or [0.0, 0.0, 0.0]
    size = [high[i] - low[i] for i in range(3)]
    diag = math.sqrt(sum(value * value for value in size)) or 1.0
    center = [(low[i] + high[i]) / 2.0 for i in range(3)]

    # 先把每条边的凸凹归属到相邻面，节点特征要用
    convexity_by_face = {}
    for edge in geom.get("edges", []):
        kind = edge.get("convexity")
        if kind not in ("convex", "concave"):
            continue
        for index in edge.get("faces") or []:
            tally = convexity_by_face.setdefault(index, {"convex": 0, "concave": 0})
            tally[kind] += 1

    # 面索引 -> 节点下标。geom 的 face["index"] 从 1 开始，不一定连续
    slot = {}
    nodes = []
    for position, face in enumerate(faces):
        slot[face["index"]] = position
        tally = convexity_by_face.get(face["index"], {"convex": 0, "concave": 0})
        nodes.append(_node_vector(face, low, size, diag, center, tally))

    edge_pairs, edge_feats = [], []
    for edge in geom.get("edges", []):
        ends = edge.get("faces") or []
        if len(ends) != 2:
            continue
        u, v = slot.get(ends[0]), slot.get(ends[1])
        if u is None or v is None or u == v:
            continue
        feature = _edge_vector(edge, faces[u], faces[v], diag)
        # 无向图存两条有向边，聚合时邻居集合自然对称
        edge_pairs.append([u, v])
        edge_feats.append(feature)
        edge_pairs.append([v, u])
        edge_feats.append(feature)

    sample = {
        "x": nodes,
        "edge_index": edge_pairs,
        "edge_attr": edge_feats,
        "num_faces": len(nodes),
        "part": geom.get("source"),
    }
    if labels is not None:
        if len(labels) != len(faces):
            raise ValueError(
                "标签数 %d 与面数 %d 不一致（%s）"
                % (len(labels), len(faces), geom.get("source"))
            )
        sample["y"] = list(labels)
    return sample


def _node_vector(face, low, size, diag, center, tally=None):
    surface = face.get("surface") or "Other"
    kinds = [0.0] * len(SURFACE_TYPES)
    kinds[_surface_slot(surface)] = 1.0

    area = face.get("area_mm2") or 0.0
    # 用包围盒对角线平方归一化面积，取对数压缩动态范围
    area_feature = math.log1p(area) / math.log1p(diag * diag)

    centroid = face.get("centroid") or center
    centroid_feature = _norm_point(centroid, center, diag / 2.0)

    normal = face.get("normal") or [0.0, 0.0, 0.0]
    length = math.sqrt(sum(value * value for value in normal)) or 1.0
    normal = [value / length for value in normal]
    alignment = max(abs(normal[0]), abs(normal[1]), abs(normal[2]))

    params = face.get("params") or {}
    radius = params.get("radius")
    radius_feature = (radius / diag) if radius else 0.0

    axis = params.get("axis")
    if axis:
        axis_length = math.sqrt(sum(value * value for value in axis)) or 1.0
        axis = [value / axis_length for value in axis]
        axis_feature = max(abs(axis[0]), abs(axis[1]), abs(axis[2]))
    else:
        axis_feature = 0.0

    edge_count = min((face.get("edge_count") or 0) / 10.0, 1.0)

    tally = tally or {"convex": 0, "concave": 0}
    marked = tally["convex"] + tally["concave"]
    if marked:
        concave_ratio = tally["concave"] / marked
        convex_ratio = tally["convex"] / marked
    else:
        # 无判定边（孤立面或退化几何）时取 0.5，不偏向任何一侧
        concave_ratio = convex_ratio = 0.5

    return (
        kinds
        + [area_feature]
        + centroid_feature
        + normal
        + [alignment, radius_feature, axis_feature, edge_count,
           concave_ratio, convex_ratio]
    )


def _edge_vector(edge, face_u, face_v, diag):
    convexity = edge.get("convexity")
    sign = 1.0 if convexity == "convex" else (-1.0 if convexity == "concave" else 0.0)

    normal_u = face_u.get("normal") or [0.0, 0.0, 0.0]
    normal_v = face_v.get("normal") or [0.0, 0.0, 0.0]
    dot = sum(normal_u[i] * normal_v[i] for i in range(3))
    lu = math.sqrt(sum(value * value for value in normal_u)) or 1.0
    lv = math.sqrt(sum(value * value for value in normal_v)) or 1.0
    cos_angle = max(-1.0, min(1.0, dot / (lu * lv)))

    length = (edge.get("length_mm") or 0.0) / diag
    straight = 1.0 if edge.get("curve") == "Line" else 0.0
    return [sign, cos_angle, min(length, 1.0), straight]


def pad_batch(samples):
    """把一批图拼成一个大图（不引入 PyG，用位移索引）。

    返回 dict：x[total_nodes, NODE_DIM], edge_index[2, total_edges],
    edge_attr[total_edges, EDGE_DIM], batch[total_nodes], y[total_nodes]
    """
    xs, eis, eas, batches, ys = [], [], [], [], []
    offset = 0
    for index, sample in enumerate(samples):
        xs.extend(sample["x"])
        for u, v in sample["edge_index"]:
            eis.append((u + offset, v + offset))
        eas.extend(sample["edge_attr"])
        batches.extend([index] * sample["num_faces"])
        ys.extend(sample.get("y") or [-100] * sample["num_faces"])
        offset += sample["num_faces"]

    return {
        "x": xs,
        "edge_index": eis,
        "edge_attr": eas,
        "batch": batches,
        "y": ys,
        "num_graphs": len(samples),
        "num_nodes": offset,
    }
