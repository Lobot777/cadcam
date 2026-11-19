"""L0 摘要的孔识别：凹圆柱才是孔，凸圆柱是凸台/外轮廓圆弧。

这条用例守的是一个具体的错：早期实现把所有 Cylinder 面的半径都列进
"圆孔类半径"，导致 10 号件把 R37 的外轮廓圆弧报成 Ø74 的孔。
如果这个错流到 LLM 上下文里，工艺规划会凭空多出一次大孔加工。
"""

from cadcam.modalities.brep import digest


def radii(items):
    """把 [{radius_mm, count}] 压成半径列表，保留实例数的断言另写。"""
    return [item["radius_mm"] for item in items]


def _cyl(index, radius, location, axis, normal, centroid):
    return {
        "id": "f%d" % index,
        "index": index,
        "surface": "Cylinder",
        "area_mm2": 100.0,
        "centroid": centroid,
        "normal": normal,
        "edge_count": 2,
        "params": {"family": "Cylinder", "radius": radius,
                   "axis": axis, "location": location},
    }


def _edge(index, faces, convexity, midpoint):
    return {"id": "e%d" % index, "curve": "Circle", "length_mm": 1.0,
            "midpoint": midpoint, "faces": faces, "convexity": convexity}


def test_concave_cylinder_is_hole_convex_is_boss():
    faces = [
        # 孔：形心在轴上，孔壁在 +X 侧，法向指向轴心(-X) -> dot < 0
        _cyl(1, 3.0, [0, 0, 0], [0, 0, 1], [-1.0, 0.0, 0.0], [0, 0, 0]),
        # 凸台：形心在轴上，凸台在 -X 侧，法向背离轴心(-X) -> dot > 0
        _cyl(2, 12.0, [0, 0, 0], [0, 0, 1], [-1.0, 0.0, 0.0], [0, 0, 0]),
    ]
    edges = [
        _edge(1, [1], "concave", [3.0, 0.0, 0.0]),
        _edge(2, [2], "convex", [-12.0, 0.0, 0.0]),
    ]
    holes, bosses, undecided = digest._cylinder_polarity(faces, edges)
    assert radii(holes) == [3.0], holes
    assert radii(bosses) == [12.0], bosses
    assert undecided == []


def test_full_cylinder_with_axis_centroid_uses_edge_probe():
    """整 360° 圆柱形心在轴上，径向为零；必须靠边中点探针推断。"""
    face = _cyl(1, 8.0, [0, 0, 0], [0, 0, 1], [1.0, 0.0, 0.0], [0, 0, 0])
    edges = [_edge(1, [1], "concave", [-8.0, 0.0, 0.0])]
    # 探针点在 -X 方向，法向在 +X 方向 -> 指向轴心 -> 孔
    assert digest._radial_dot(face, edges) < 0
    holes, _, _ = digest._cylinder_polarity([face], edges)
    assert radii(holes) == [8.0]


def test_same_radius_counts_instances():
    """R5 出现四次时，必须能看出是四个特征，而不是一个。"""
    faces = [
        _cyl(i, 5.0, [0, 0, 0], [0, 0, 1], [-1.0, 0.0, 0.0], [0, 0, 0])
        for i in (1, 2, 3, 4)
    ]
    edges = [_edge(i, [i], "concave", [5.0, 0.0, 0.0]) for i in (1, 2, 3, 4)]
    holes, _, _ = digest._cylinder_polarity(faces, edges)
    assert holes == [{"radius_mm": 5.0, "count": 4}]


def test_ambiguous_cylinder_is_reported_not_guessed():
    """参数缺失时不得瞎猜，应进入 undecided 交由下钻处理。"""
    face = {"id": "f1", "index": 1, "surface": "Cylinder", "area_mm2": 1.0,
            "centroid": [0, 0, 0], "normal": None, "edge_count": 0,
            "params": {"family": "Cylinder", "radius": 5.0}}
    edges = [_edge(1, [1], "concave", [5.0, 0, 0]),
             _edge(2, [1], "convex", [-5.0, 0, 0])]
    holes, bosses, undecided = digest._cylinder_polarity([face], edges)
    assert radii(undecided) == [5.0]
    assert holes == [] and bosses == []
