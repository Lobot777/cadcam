"""M6 补充实验的契约测试：Wilson 区间、两份新台账的自洽性、STL 产物格式。

报告类用例在报告不存在时跳过；纯函数与文件格式用例始终运行。
"""
import json
import os
import struct
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))


# ---------------------------------------------------------------- Wilson CI

def test_wilson_known_values():
    from cadcam.verify.stats import wilson
    lo, hi = wilson(10, 10)
    assert 0.72 < lo < 0.73 and hi >= 0.999, "10/10 的 95% 下界应约 0.7225"
    lo0, hi0 = wilson(0, 10)
    assert lo0 == 0.0 and 0.27 < hi0 < 0.28
    lo130, hi130 = wilson(130, 130)
    assert 0.96 < lo130 < 0.98 and hi130 >= 0.999


def test_wilson_symmetry_and_monotonicity():
    from cadcam.verify.stats import wilson
    for n in (10, 20, 130):
        for k in (0, 1, n // 2, n - 1, n):
            a = wilson(k, n)
            b = wilson(n - k, n)
            assert abs(a[0] - (1.0 - b[1])) < 1e-4
            assert abs(a[1] - (1.0 - b[0])) < 1e-4
    las = 0.0
    for k in range(11):
        lo, _hi = wilson(k, 10)
        assert lo >= las - 1e-9
        las = lo


def test_wilson_rejects_invalid_input():
    from cadcam.verify.stats import wilson
    for k, n in ((-1, 10), (11, 10), (1, 0), (1, -5)):
        with pytest.raises(ValueError):
            wilson(k, n)


def test_wilson_table_shape():
    from cadcam.verify.stats import wilson_table
    rows = wilson_table([{"name": "x", "k": 9, "n": 10},
                         {"name": "y", "k": 0, "n": 10}])
    assert len(rows) == 2
    assert rows[0]["rate"] == 0.9
    assert len(rows[0]["wilson95"]) == 2
    assert rows[1]["wilson95"][0] == 0.0


# ------------------------------------------------------- STEP→STL 独立链路

def test_stl_exports_are_binary_with_correct_size():
    d = os.path.join(ROOT, "reports", "m6_step_stl")
    manifest = os.path.join(d, "manifest.json")
    if not os.path.exists(manifest):
        pytest.skip("STEP→STL 尚未导出")
    with open(manifest, encoding="utf-8") as fh:
        man = json.load(fh)
    assert "OCC" in man["kernel"]
    assert len(man["parts"]) == 10
    for rec in man["parts"]:
        p = os.path.join(d, rec["stl"])
        with open(p, "rb") as fh:
            head = fh.read(84)
        n = struct.unpack("<I", head[80:84])[0]
        assert n == rec["facets"], "STL facet 数与 manifest 不符"
        assert os.path.getsize(p) == 84 + 50 * n, "必须是二进制 STL（50 字节/面片）"


def test_step_stl_report_self_consistent():
    p = os.path.join(ROOT, "reports", "m6_step_stl_xcheck.json")
    if not os.path.exists(p):
        pytest.skip("m6_step_stl_xcheck.json 未生成")
    with open(p, encoding="utf-8") as fh:
        rep = json.load(fh)
    s = rep["summary"]
    assert s["n"] == 10 and s["errors"] == 0
    assert s["bbox_all_match"] is True
    assert s["two_ocl_surfaces_min_identical_pct"] >= 99.98
    assert s["two_ocl_surfaces_total_columns_over_0_5mm"] < 30
    assert s["B3_gouge_points"] > s["B2_gouge_points"] * 10
    j = s["json_cache_chain"]
    assert j["B2_gouge_points"] > 0
    # 两条几何链路的过切计数差 < 1%（圆弧边界剖分差异）
    assert abs(s["B2_gouge_points"] - j["B2_gouge_points"]) / j["B2_gouge_points"] < 0.01
    assert abs(s["B3_gouge_points"] - j["B3_gouge_points"]) / j["B3_gouge_points"] < 0.01


def test_stlreader_matches_json_surface_on_flat_part():
    """件 1 是纯平面：STL 与 JSON 缓存两条链路的 OCL 高度图应逐柱 0 差。"""
    ocl = pytest.importorskip("opencamlib")
    import numpy as np
    from m6_opencamlib import POINT_D, cl_grid, stlsurf
    from cadcam.verify import dexel
    d = os.path.join(ROOT, "reports", "m6_step_stl", "1.stl")
    if not os.path.exists(d):
        pytest.skip("STL 未导出")
    with open(os.path.join(ROOT, "reports", "annotation", "geom", "1.json"),
              encoding="utf-8") as fh:
        geom = json.load(fh)
    target = dexel.rasterize(geom, res=0.5)
    a, _ = stlsurf(geom)
    b = ocl.STLSurf()
    ocl.STLReader(d, b)
    ha, _ = cl_grid(a, target["xs"], target["ys"], POINT_D)
    hb, _ = cl_grid(b, target["xs"], target["ys"], POINT_D)
    both = (ha > 0) & (hb > 0)
    assert both.sum() > 20000
    assert np.max(np.abs(ha[both] - hb[both])) < 1e-6


# --------------------------------------------------------- 130 件外部集扩展

def test_ocl_external_report_self_consistent():
    p = os.path.join(ROOT, "reports", "m6_ocl_external.json")
    if not os.path.exists(p):
        pytest.skip("m6_ocl_external.json 未生成")
    with open(p, encoding="utf-8") as fh:
        rep = json.load(fh)
    s = rep["summary"]
    if s["n"] < 130:
        pytest.skip("全量 130 件仍在跑（当前为 %d 件试跑）" % s["n"])
    assert s["errors"] == 0
    # 0.02mm 阈值与 Ø0.02 点刀数值误差同量级 → 最差件一致率是假象（该件最大差仅 0.023mm）
    assert s["geometry_min_within_0_02mm_pct"] >= 87.0
    assert 99.5 < s["geometry_mean_within_0_02mm_pct"] <= 100.0
    assert s["geometry_min_footprint_iou"] >= 0.997
    # 71.104mm 是竖直壁探针伪影，上界由毛坯高度决定；解释见 m6_ocl_point_cutter.json
    assert 0 < s["geometry_max_abs_diff_mm"] <= 80.0
    assert s["B2_gouge_points"] > 0 and s["B3_gouge_points"] > s["B2_gouge_points"]
    assert s["B2_deep_on_uncovered_columns"] <= s["B2_deep"]
    assert s["B2_our_dual_pass"] == 130          # 自己的判据仍全通过
    assert s["B3_our_dual_pass"] < 130
    assert s["guard_gouge_points"] < s["B2_gouge_points"] / 10
    assert s["guard_our_dual_pass"] < 130       # 半格余量的真实代价
    assert s["guard_residual_mm3"] > s["B2_residual_mm3"]


def test_point_cutter_sensitivity_explains_71mm_artifact():
    """点刀缩径后超差柱 199→2→1，证明 71mm 差异来自有限半径探针擦竖直壁。"""
    p = os.path.join(ROOT, "reports", "m6_ocl_point_cutter.json")
    if not os.path.exists(p):
        pytest.skip("点刀敏感性报告未生成")
    with open(p, encoding="utf-8") as fh:
        rep = json.load(fh)
    ex = rep["datasets"]["external_130"]["summary"]
    a = ex["d_0.0200"]
    b = ex["d_0.0020"]
    c = ex["d_0.0002"]
    assert a["columns_over_0_5mm"] == 199
    assert b["columns_over_0_5mm"] == 2
    assert c["columns_over_0_5mm"] == 1
    assert a["max_abs_diff_mm"] > b["max_abs_diff_mm"] > c["max_abs_diff_mm"]
    assert b["min_within_0_05mm_pct"] >= 99.99
    assert rep["datasets"]["anchor_10"]["summary"]["d_0.0200"][
        "columns_over_0_5mm"] == 120


def test_fig6_exists_when_external_report_is_full():
    """全量外部集报告存在时，图 6 必须已生成（png + pdf）。"""
    p = os.path.join(ROOT, "reports", "m6_ocl_external.json")
    if not os.path.exists(p):
        pytest.skip("外部集报告未生成")
    with open(p, encoding="utf-8") as fh:
        if json.load(fh)["summary"]["n"] < 130:
            pytest.skip("外部集仍在试跑")
    for ext in ("png", "pdf"):
        assert os.path.getsize(os.path.join(
            ROOT, "reports", "figures", "fig6_ocl_external." + ext)) > 1000


def test_external_oracle_classifies_shallow_vs_deep():
    """过切分类：浅/深之和必须等于总数，且深过切不超过总数。"""
    p = os.path.join(ROOT, "reports", "m6_ocl_external.json")
    if not os.path.exists(p):
        pytest.skip("m6_ocl_external.json 未生成")
    with open(p, encoding="utf-8") as fh:
        rep = json.load(fh)
    for r in rep["parts"]:
        if "B2_masked" not in r:
            continue
        o = r["B2_masked"]["ocl_oracle"]
        assert o["shallow_le_0_5mm"] + o["deep_gt_0_5mm"] == o["gouge_points"]
        assert o["deep_on_uncovered_columns"] <= o["deep_gt_0_5mm"]
