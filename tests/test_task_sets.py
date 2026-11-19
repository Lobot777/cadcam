"""任务集自检：几何合法、文案与答案一致、id 不冲突。

这几条都是被真实 bug 逼出来的：

- H06 原表述"关于 y=50 对称"没给距离 → 8/9 个云端模型读成别的合理布局；
- E12 改了文案"（90,10）不钻"却忘了改答案 → 15/15 个模型全被判 0.50；
- 标准答案缓存固定文件名 → 改了答案仍复用旧实体，分数不动。

所以任务集本身也要有回归测试，不能只测代码。
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from m5_hard2 import SKIN, check, make_extra_tasks  # noqa: E402
from m5_nl_eval import make_hard_tasks  # noqa: E402


def test_extra_set_shape():
    tasks = make_extra_tasks()
    assert len(tasks) == 20
    ids = [t["id"] for t in tasks]
    assert len(set(ids)) == 20, "id 必须唯一"
    for t in tasks:
        assert len(t["calls"]) >= 2, "%s 至少要有一个特征" % t["id"]
        assert t["calls"][0]["tool"] == "cad.create_box"
        assert t["text"].strip()
        assert t["tag"]


def test_extra_set_geometry_and_copy_are_consistent():
    """几何越界、切穿、以及"文案说不做但答案里有"都在这里挡掉。"""
    bad = check()
    assert bad == [], "任务集自检失败：%s" % bad


def test_extra_set_does_not_reuse_main_ids():
    ids = {t["id"] for t in make_extra_tasks()}
    main = {t["id"] for t in make_hard_tasks()}
    assert not (ids & main), "两套任务集 id 不能重叠，否则结果会串"


def test_main_set_h06_is_unambiguous():
    """H06 的旧表述只有"上下对称"，改后必须能算出唯一坐标。"""
    t = [x for x in make_hard_tasks() if x["id"] == "H06"][0]
    assert "留 10" in t["text"] and "空隙" in t["text"], "H06 又退回到欠约束表述了"
    pockets = [c["args"] for c in t["calls"] if c["tool"] == "cad.pocket"]
    assert [p["center_y"] for p in pockets] == [20.0, 80.0]
    # 下边缘 10 + 高 20 + 空隙 40 + 高 20 + 上边缘 10 = 100（毛坯 Y 向长度）
    assert 10 + 20 + 40 + 20 + 10 == 100


@pytest.mark.parametrize("task", make_extra_tasks(), ids=lambda t: t["id"])
def test_each_extra_task_fits_in_stock(task):
    """逐条再核一遍（check() 聚合版覆盖不到"单条只剩一个特征"这类错误）。"""
    L, W, H = task["size"]
    assert len(task["calls"]) >= 2
    for c in task["calls"][1:]:
        a = c["args"]
        if c["tool"] == "cad.pocket":
            assert a["width"] > 0 and a["height"] > 0 and a["depth"] > 0
            assert a["depth"] <= H - SKIN
            assert 0 <= a["center_x"] - a["width"] / 2
            assert a["center_x"] + a["width"] / 2 <= L
            assert 0 <= a["center_y"] - a["height"] / 2
            assert a["center_y"] + a["height"] / 2 <= W
        else:
            assert 0 < a["x"] < L and 0 < a["y"] < W
            assert a["diameter"] > 0


# ---------------- 歧义实验的判据（B15） ----------------

def test_ambiguity_cases_are_the_five_we_designed():
    from m5_ambiguity_eval import CASES, STRONG
    assert len(CASES) == 5
    ids = [c[0] for c in CASES]
    assert ids == ["A1", "A2", "A3", "A4", "A5"]
    # A5（"深 1" 单位）在工程语境默认 mm，是弱歧义，不进主指标
    assert "A5" not in STRONG


def test_ambiguity_hit_requires_the_right_question():
    """只判"有没有追问"会高估能力：A4 尺寸超限时问"中心在哪"是问错了方向。"""
    from m5_ambiguity_eval import _hit
    assert _hit("A1", "腔的中心位置（X、Y 坐标）是多少？") is True
    assert _hit("A2", "挖空深度是多少？") is True
    assert _hit("A3", "孔的数量和间距未指定") is True
    assert _hit("A4", "腔体 120×120 超出毛坯 100×80，无法加工") is True
    # 问错方向
    assert _hit("A4", "腔体中心位置？") is False
    assert _hit("A1", "好的，我来铣这个腔") is False
    assert _hit("A1", "") is False


# ---------------- 外部验证集（M6）：筛选映射与缩放 ----------------

def test_mfcad_family_mapping_covers_only_three_families():
    from external_select import FAMILY, MAPPABLE
    assert set(FAMILY.values()) == {"pocket", "step", "hole"}
    assert MAPPABLE == set(FAMILY)
    # MFCAD 的 stock（15）不该被映射成特征
    assert 15 not in FAMILY


def test_external_scale_handles_both_bbox_shapes():
    """顶层 bbox 是 dict，面级 bbox 是 6 元 list —— 两种都要缩对。"""
    from external_scale import transform
    g = {"bbox": {"min": [0, 0, 0], "max": [10, 20, 30]},
         "volume_mm3": 1000.0, "area_mm2": 100.0,
         "faces": [{"index": 1, "bbox": [0, 0, 0, 10, 20, 30],
                    "centroid": [5, 10, 15], "area_mm2": 50.0,
                    "triangles": [[[0, 0, 0], [10, 0, 0], [0, 20, 0]]]}],
         "edges": [{"midpoint": [5, 10, 15], "length_mm": 10.0}]}
    out = transform(g, 2.0)
    assert out["bbox"]["max"] == [20.0, 40.0, 60.0]
    assert out["volume_mm3"] == 8000.0, "体积按 s³"
    assert out["area_mm2"] == 400.0, "面积按 s²"
    assert out["faces"][0]["bbox"] == [0.0, 0.0, 0.0, 20.0, 40.0, 60.0]
    assert out["faces"][0]["centroid"] == [10.0, 20.0, 30.0]
    assert out["faces"][0]["area_mm2"] == 200.0
    assert out["faces"][0]["triangles"][0][1] == [20.0, 0.0, 0.0]
    assert out["edges"][0]["length_mm"] == 20.0
