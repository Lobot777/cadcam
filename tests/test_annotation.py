"""真实件标注文件的结构契约。

标注是论文里的"真值"，一旦结构错位（面号漂移、重复归属），
后面所有关于真实件准确率的数字都作废。所以把结构检查固化成测试，
而不是靠人记得跑一遍 check.py。
"""

import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LABELS = os.path.join(ROOT, "data", "zenodo_rptu", "labels")
PARTS = [str(i) for i in range(1, 11)]
FAMILIES = {"stock", "hole", "pocket", "slot", "step", "boss", "fillet", "chamfer",
            "freeform", "unknown"}


def load(part):
    path = os.path.join(LABELS, "%s.json" % part)
    if not os.path.exists(path):
        pytest.skip("缺少标注文件 %s" % path)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.mark.parametrize("part", PARTS)
def test_face_coverage_exactly_once(part):
    ann = load(part)
    n = ann["n_faces"]
    assert len(ann["faces"]) == n
    assert set(ann["faces"]) == {str(i) for i in range(1, n + 1)}

    assigned = [i for f in ann["features"] for i in f["faces"]]
    assert len(assigned) == len(set(assigned)), "同一面被两个特征占用"
    stock = set(ann["stock_faces"])
    assert set(assigned) | stock == set(range(1, n + 1))
    assert not (set(assigned) & stock), "面既在特征里又在 stock 里"


@pytest.mark.parametrize("part", PARTS)
def test_family_and_class_legal(part):
    ann = load(part)
    ids = {f["id"] for f in ann["features"]}
    assert len(ids) == len(ann["features"]), "特征 id 重复"
    for face in ann["faces"].values():
        assert face["family"] in FAMILIES
        if face["feature"] is not None:
            assert face["feature"] in ids
        if face["family"] == "stock":
            assert face["feature"] is None
        if face["mfcad_class"] is not None:
            assert isinstance(face["mfcad_class"], str)


@pytest.mark.parametrize("part", PARTS)
def test_face_table_matches_feature_table(part):
    ann = load(part)
    listed = {i: f["id"] for f in ann["features"] for i in f["faces"]}
    for index, fid in listed.items():
        assert ann["faces"][str(index)]["feature"] == fid


def test_total_face_count():
    """锚点集规模必须是常数：写论文时这个数会被反复引用。"""
    total = sum(load(p)["n_faces"] for p in PARTS)
    assert total == 206


# ---------------------------------------------------------------- 件 10 面积对账
# 这几条不是"看一眼对得上"，而是把 docs/MEASURE-PART10.md 的结论写成可执行断言：
# 任何一次重新抽面/改标注，只要几何解释错了，面积恒等式就会崩。

def _geom10():
    import json
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "reports", "annotation", "geom", "10.json")
    if not os.path.exists(path):
        pytest.skip("缺件 10 几何缓存")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _area(geom, index):
    return [f for f in geom["faces"] if f["index"] == index][0]["area_mm2"]


def test_part10_ring_area_closes():
    """顶面环带 + 方腔开口 = Ø74 圆面积；方腔开口 = Ø52.33 圆 + 四角肩面。"""
    import math
    g = _geom10()
    r74 = 37.0
    r52 = [f for f in g["faces"] if f["index"] == 5][0]["params"]["radius"]
    corner = sum(_area(g, i) for i in (4, 6, 7, 8))
    ring = _area(g, 18)
    assert abs(ring + math.pi * r52 ** 2 + corner - math.pi * r74 ** 2) < 0.6, \
        "顶面环带/方腔开口/Ø74 圆 三者不闭合"


def test_part10_counterbore_floor_plus_island():
    """Ø52.33 沉孔底 + 星形岛顶 = Ø52.33 圆面积。"""
    import math
    g = _geom10()
    r52 = [f for f in g["faces"] if f["index"] == 5][0]["params"]["radius"]
    assert abs(_area(g, 3) + _area(g, 2) - math.pi * r52 ** 2) < 0.6


def test_part10_lateral_areas_are_full_cylinders():
    """F13/F5 是整圆柱；F9/11/15/17 只有 1/4 圆周（角圆角，不是孔）。"""
    import math
    g = _geom10()
    r52 = [f for f in g["faces"] if f["index"] == 5][0]["params"]["radius"]
    assert abs(_area(g, 13) - 2 * math.pi * 37.0 * 8) < 0.6
    assert abs(_area(g, 5) - 2 * math.pi * r52 * 4) < 0.6
    for i in (9, 11, 15, 17):
        r = [f for f in g["faces"] if f["index"] == i][0]["params"]["radius"]
        assert abs(_area(g, i) - 2 * math.pi * r * 4 / 4.0) < 0.3, "面 %d 不是四分之一弧" % i


def test_part10_wall_length_implies_corner_radius():
    """方腔平壁长 46.3 = 52.33 - 2×R3，直接证明四角是 R3 而非直角。"""
    g = _geom10()
    r52 = [f for f in g["faces"] if f["index"] == 5][0]["params"]["radius"]
    for i in (10, 12, 14, 16):
        expect = (2 * r52 - 2 * 3.0) * 4
        assert abs(_area(g, i) - expect) < 0.3, "面 %d 壁长与 R3 角不自洽" % i


def test_part10_labels_follow_measurement():
    """量测结论要落到标注里：F5 属圆沉孔（pocket），不是孔；F9/11/15/17 是圆角。"""
    ann = load("10")
    assert ann["faces"]["5"]["family"] == "pocket"
    assert ann["faces"]["5"]["feature"] == ann["faces"]["3"]["feature"]
    for i in (9, 11, 15, 17):
        assert ann["faces"][str(i)]["family"] == "fillet"
    assert ann["review_needed"] is False
