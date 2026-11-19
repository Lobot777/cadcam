"""MFCAD++ 等数据集的 STEP 面标签解析。

MFCAD++ 的特征标签并不在几何里，而是写在每个 ``ADVANCED_FACE`` 实体的
name 字段（第一个字符串参数）上，取值是特征类别号 0–24。生成脚本见
``mfcad2-dataset/main.py::shape_with_fid_to_step``，它调用
``item.SetName(str(label_id))``。

这里的解析是纯文本的，不依赖 OCC —— 因为我们要拿它来**校验**几何内核，
若用同一个内核读标签，校验就变成了自证。
"""

import re
from collections import Counter

# MFCAD++ 的 25 类（24 类加工特征 + stock）。顺序取自 feature_labels.txt，
# 与生成脚本 Utils/parameters.py::feat_names 一致。
MFCAD_LABELS = {
    0: "chamfer",
    1: "through_hole",
    2: "triangular_passage",
    3: "rectangular_passage",
    4: "6sides_passage",
    5: "triangular_through_slot",
    6: "rectangular_through_slot",
    7: "circular_through_slot",
    8: "rectangular_through_step",
    9: "2sides_through_step",
    10: "slanted_through_step",
    11: "Oring",
    12: "blind_hole",
    13: "triangular_pocket",
    14: "rectangular_pocket",
    15: "6sides_pocket",
    16: "circular_end_pocket",
    17: "rectangular_blind_slot",
    18: "v_circular_end_blind_slot",
    19: "h_circular_end_blind_slot",
    20: "triangular_blind_step",
    21: "circular_blind_step",
    22: "rectangular_blind_step",
    23: "round",
    24: "stock",
}

STOCK_LABEL = 24

# 工艺语义分组：25 类对工艺规划太细，映射到我们网关的 6 个动作族。
# 这个映射是本文的建模选择，不是数据集自带的，写在这里便于消融实验替换。
FEATURE_FAMILY = {
    "chamfer": "chamfer",
    "round": "fillet",
    "through_hole": "hole",
    "blind_hole": "hole",
    "Oring": "groove",
    "triangular_passage": "pocket_through",
    "rectangular_passage": "pocket_through",
    "6sides_passage": "pocket_through",
    "triangular_pocket": "pocket",
    "rectangular_pocket": "pocket",
    "6sides_pocket": "pocket",
    "circular_end_pocket": "pocket",
    "triangular_through_slot": "slot",
    "rectangular_through_slot": "slot",
    "circular_through_slot": "slot",
    "rectangular_blind_slot": "slot",
    "v_circular_end_blind_slot": "slot",
    "h_circular_end_blind_slot": "slot",
    "rectangular_through_step": "step",
    "2sides_through_step": "step",
    "slanted_through_step": "step",
    "triangular_blind_step": "step",
    "circular_blind_step": "step",
    "rectangular_blind_step": "step",
    "stock": "stock",
}

# STEP 实体名 -> FreeCAD/OCC 的 Surface 类名，用于跨内核比对
SURFACE_ALIAS = {
    "PLANE": "Plane",
    "CYLINDRICAL_SURFACE": "Cylinder",
    "CONICAL_SURFACE": "Cone",
    "TOROIDAL_SURFACE": "Torus",
    "SPHERICAL_SURFACE": "Sphere",
    "B_SPLINE_SURFACE": "BSplineSurface",
    "B_SPLINE_SURFACE_WITH_KNOTS": "BSplineSurface",
    "RATIONAL_B_SPLINE_SURFACE": "BSplineSurface",
    "SURFACE_OF_REVOLUTION": "SurfaceOfRevolution",
    "SURFACE_OF_LINEAR_EXTRUSION": "SurfaceOfExtrusion",
}

_ENTITY = re.compile(r"#(\d+)\s*=\s*([A-Z][A-Z0-9_]*)\s*\(", re.I)
_FACE = re.compile(
    r"#(\d+)\s*=\s*ADVANCED_FACE\s*\(\s*'([^']*)'\s*,\s*\(([^)]*)\)\s*,\s*#(\d+)",
    re.I,
)
_STRIP_COMMENT = re.compile(r"/\*.*?\*/", re.S)


def _clean(text):
    """去掉 STEP 的 /* */ 注释，否则注释里的括号会污染正则。"""
    return _STRIP_COMMENT.sub("", text)


def parse_labels(text):
    """按文件中 ``ADVANCED_FACE`` 的**出现顺序**返回面标签列表。

    返回 ``[{"index": 0, "entity_id": 17, "label": 24, "name": "stock",
    "surface": "Plane", "raw_surface": "PLANE"}, ...]``
    """
    text = _clean(text)

    # 先建实体 id -> 类型名表，供面反查表面类型
    types = {m.group(1): m.group(2).upper() for m in _ENTITY.finditer(text)}

    faces = []
    for order, match in enumerate(_FACE.finditer(text)):
        entity_id, name, _bounds, surface_ref = match.groups()
        raw_surface = types.get(surface_ref, "UNKNOWN")
        label = int(name) if name.strip().isdigit() else -1
        faces.append(
            {
                "index": order,
                "entity_id": int(entity_id),
                "label": label,
                "name": MFCAD_LABELS.get(label, "unknown"),
                "family": FEATURE_FAMILY.get(MFCAD_LABELS.get(label, ""), "unknown"),
                "surface": SURFACE_ALIAS.get(raw_surface, raw_surface),
                "raw_surface": raw_surface,
            }
        )
    return faces


def parse_file(path):
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return parse_labels(handle.read())


def label_stats(faces):
    """标签分布：按类别计数 + 按工艺族计数。stock 通常是最大类。"""
    by_label = Counter(face["label"] for face in faces)
    by_family = Counter(face["family"] for face in faces)
    return {
        "faces": len(faces),
        "by_label": {MFCAD_LABELS.get(k, str(k)): v for k, v in sorted(by_label.items())},
        "by_family": dict(by_family.most_common()),
        "feature_face_ratio": round(
            1.0 - by_label.get(STOCK_LABEL, 0) / max(len(faces), 1), 3
        ),
    }


def surface_sequence(faces):
    """表面类型序列。用作「STEP 顺序 vs 内核读入顺序」的对齐指纹。"""
    return [face["surface"] for face in faces]
