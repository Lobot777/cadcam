"""M2 相关模块的行为锁定：标签解析、AAG 构图、实例聚合、几何校验。"""

import pytest

from cadcam.modalities.brep import step_labels as S
from cadcam.modalities.mfr import aag, instances, verify

STEP_SNIPPET = """ISO-10303-21;
DATA;
#10 = ADVANCED_FACE('24',(#11),#40,.F.);
#11 = FACE_BOUND('',#12,.F.);
#12 = EDGE_LOOP('',(#13));
#20 = ADVANCED_FACE('1',(#21),#41,.T.);
#40 = PLANE('',#42);
#41 = CYLINDRICAL_SURFACE('',#43,3.0);
#99 = ADVANCED_FACE('14',(#98),#40,.F.);
ENDSEC;
END-ISO-10303-21;
"""


def test_parse_labels_in_file_order():
    faces = S.parse_labels(STEP_SNIPPET)
    assert [f["label"] for f in faces] == [24, 1, 14]
    assert [f["name"] for f in faces] == ["stock", "through_hole", "rectangular_pocket"]


def test_parse_labels_resolves_surface_type():
    faces = S.parse_labels(STEP_SNIPPET)
    assert [f["surface"] for f in faces] == ["Plane", "Cylinder", "Plane"]


def test_feature_family_mapping_covers_every_class():
    for label, name in S.MFCAD_LABELS.items():
        assert name in S.FEATURE_FAMILY, "缺少工艺族映射: %s" % name
        assert S.FEATURE_FAMILY[name] != "unknown"


def test_label_stats_reports_feature_ratio():
    faces = S.parse_labels(STEP_SNIPPET)
    stats = S.label_stats(faces)
    assert stats["faces"] == 3
    assert stats["by_label"]["stock"] == 1
    assert stats["feature_face_ratio"] == pytest.approx(0.667, abs=0.01)


def _geom():
    """一个最小的 L 形件：两个平面 + 一个圆柱，中间有一条凹边。"""
    return {
        "bbox": {"min": [0, 0, 0], "max": [10, 10, 10]},
        "faces": [
            {
                "index": 1, "surface": "Plane", "area_mm2": 100.0,
                "centroid": [5, 5, 10], "normal": [0, 0, 1],
                "bbox": [0, 0, 10, 10, 10, 10], "edge_count": 4,
                "params": {"family": "Plane"},
            },
            {
                "index": 2, "surface": "Plane", "area_mm2": 60.0,
                "centroid": [0, 5, 5], "normal": [-1, 0, 0],
                "bbox": [0, 0, 0, 0, 10, 10], "edge_count": 4,
                "params": {"family": "Plane"},
            },
            {
                # 法向背离轴线（+X）→ 几何判据应判为「凸」，不是孔
                "index": 3, "surface": "Cylinder", "area_mm2": 31.4,
                "centroid": [5, 5, 5], "normal": [1, 0, 0],
                "bbox": [4, 4, 0, 6, 6, 10], "edge_count": 2,
                "params": {"family": "Cylinder", "radius": 1.0,
                           "axis": [0, 0, 1], "location": [5, 5, 0]},
            },
        ],
        "edges": [
            {"faces": [1, 2], "convexity": "concave", "length_mm": 10.0, "curve": "Line"},
            {"faces": [1, 3], "convexity": "convex", "length_mm": 5.0, "curve": "Line",
             "midpoint": [6, 5, 5]},
        ],
    }


def test_aag_node_dim_matches_declared():
    sample = aag.build(_geom(), [24, 24, 1])
    assert len(sample["x"][0]) == aag.NODE_DIM


def test_aag_edges_are_symmetric():
    sample = aag.build(_geom(), [24, 24, 1])
    pairs = {tuple(edge) for edge in sample["edge_index"]}
    for u, v in pairs:
        assert (v, u) in pairs, "缺少反向边 (%d,%d)" % (v, u)


def test_aag_node_features_stay_bounded():
    sample = aag.build(_geom(), [24, 24, 1])
    for node in sample["x"]:
        for value in node:
            assert -2.0 <= value <= 2.0, node


def test_aag_rejects_label_count_mismatch():
    with pytest.raises(ValueError):
        aag.build(_geom(), [24, 1])


def test_aag_convexity_ratio_feeds_node():
    """圆柱面只与一条凸边相邻 → 凸边比应为 1。"""
    sample = aag.build(_geom(), [24, 24, 1])
    cylinder = sample["x"][2]
    assert cylinder[-1] == pytest.approx(1.0)  # convex_ratio
    assert cylinder[-2] == pytest.approx(0.0)  # concave_ratio


def test_instances_group_by_label_and_connectivity():
    geom = _geom()
    found = instances.group(geom, [24, 24, 1])
    by_family = {item["family"]: item["face_count"] for item in found}
    # 两个 stock 面相邻，应合成一个实例
    assert by_family["stock"] == 2
    assert by_family["hole"] == 1


def test_instances_bbox_uses_face_extent_not_centroid():
    found = instances.group(_geom(), [24, 24, 1])
    hole = next(item for item in found if item["family"] == "hole")
    assert hole["bbox"]["size"] != [0, 0, 0]
    assert hole["diameter_mm"] == pytest.approx(2.0)


def test_verify_flags_convex_cylinder_as_not_hole():
    geom = _geom()
    # 手工构造一个「模型说是孔，但几何是凸」的实例
    fake = [{
        "class_id": 1, "family": "hole", "faces": [3], "face_count": 1,
        "area_mm2": 31.4, "bbox": None, "action": "cad.hole",
    }]
    corrected, conflicts = verify.cross_check(geom, fake)
    assert conflicts, "凸圆柱被判成孔时必须报冲突"
    assert corrected[0]["family"] == "boss_or_fillet"
    assert corrected[0]["corrected_from"] == "hole"


def test_verify_leaves_stock_untouched():
    found = instances.group(_geom(), [24, 24, 1])
    corrected, conflicts = verify.cross_check(_geom(), found)
    stocks = [item for item in corrected if item["family"] == "stock"]
    assert stocks and not any(item.get("corrected_from") for item in stocks)
