"""M6 OpenCAMLib 对照：新增 guard 参数的向后兼容、OCL 落刀语义、结果台账自检。

第三方内核相关用例在没有 opencamlib 的环境里自动跳过；纯 Python 的
guard_mm 契约测试始终运行。
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
GEOM = os.path.join(ROOT, "reports", "annotation", "geom")
LABELS = os.path.join(ROOT, "data", "zenodo_rptu", "labels")


def _part(n):
    with open(os.path.join(GEOM, "%s.json" % n), encoding="utf-8") as fh:
        geom = json.load(fh)
    with open(os.path.join(LABELS, "%s.json" % n), encoding="utf-8") as fh:
        lab = json.load(fh)
    labels = [lab["faces"][str(f["index"])]["family"] for f in geom["faces"]]
    from cadcam.modalities.mfr.instances import group
    return geom, group(geom, labels, families={x: x for x in set(labels)})


def _simulate(text, meta, target, stock_top):
    import numpy as np
    from cadcam.modalities.cam import gcode_gen
    from cadcam.verify import dexel
    r = {o["label"]: o["tool_d_mm"] / 2.0 for o in meta["ops_detail"]}
    m = dexel.simulate(gcode_gen.validate(text)["ir"], target, stock_top,
                       tool_radius_mm=3.0, res=0.5, tool_radius_by_op=r)
    goal = target["top"]
    mask = np.isfinite(goal)
    gouge = np.where(mask, np.maximum(0.0, goal - m["cur"]), 0.0)
    resid = np.where(mask, np.maximum(0.0, m["cur"] - goal), 0.0)
    return float(gouge.sum() * 0.25), float(resid.sum() * 0.25)


def test_guard_default_keeps_m3_numbers_bit_identical():
    """guard_mm 默认 0，M3 全部既有 G-code 必须一字不差。"""
    from cadcam.modalities.cam import heightmap, process
    from cadcam.verify import dexel
    geom, inst = _part("1")
    plan = process.plan(geom, inst)
    target = dexel.rasterize(geom, res=0.5)
    a, _ = heightmap.generate(plan, target, res=0.5)
    b, _ = heightmap.generate(plan, target, res=0.5, guard_mm=0.0)
    assert a == b


def test_guard_only_removes_cutting_never_adds_gouge():
    """半格安全余量只能让可行域变小：残留不减、过切不增。"""
    from cadcam.modalities.cam import heightmap, process
    from cadcam.verify import dexel
    geom, inst = _part("10")
    plan = process.plan(geom, inst)
    target = dexel.rasterize(geom, res=0.5)
    base, mb = heightmap.generate(plan, target, res=0.5)
    guarded, mg = heightmap.generate(plan, target, res=0.5, guard_mm=0.25)
    assert base != guarded, "半格余量没有改变刀路，参数可能没接上"
    g0, r0 = _simulate(base, mb, target, plan["stock"]["top_z"])
    g1, r1 = _simulate(guarded, mg, target, plan["stock"]["top_z"])
    assert g1 <= g0 + 1e-6
    assert r1 >= r0 - 1e-6


def test_ocl_dropcutter_is_bottom_up():
    """早期探针把 CLPoint 起始 z 设成 200，全部 hit=False —— 锁住这个坑。"""
    ocl = pytest.importorskip("opencamlib")
    surf = ocl.STLSurf()
    surf.addTriangle(ocl.Triangle(ocl.Point(-20, -20, 10),
                                  ocl.Point(20, -20, 10),
                                  ocl.Point(0, 20, 10)))
    cutter = ocl.CylCutter(6.0, 60.0)
    high = ocl.CLPoint(0, 0, 200.0)
    assert cutter.dropCutterSTL(high, surf) is False
    assert high.z == 200.0
    low = ocl.CLPoint(0, 0, 0.0)
    assert cutter.dropCutterSTL(low, surf) is True
    assert abs(low.z - 10.0) < 1e-6


def test_ocl_point_cutter_confirms_rasterize_on_part1():
    """件 1 是纯平面件：点刀高度图应与我们的栅格化逐柱完全一致。"""
    ocl = pytest.importorskip("opencamlib")
    import numpy as np
    from m6_opencamlib import POINT_D, cl_grid, geometry_check, stlsurf
    from cadcam.verify import dexel
    geom, _ = _part("1")
    target = dexel.rasterize(geom, res=0.5)
    surf, n = stlsurf(geom)
    assert n == 28
    h, calls = cl_grid(surf, target["xs"], target["ys"], POINT_D)
    assert calls > 0
    mask = np.isfinite(target["top"])
    assert np.allclose(h[mask], target["top"][mask], atol=1e-4)
    out = geometry_check(surf, target)
    assert out["footprint_iou"] == 1.0
    assert out["max_abs_diff_mm"] < 0.001


def test_ocl_oracle_rejects_b3_but_accepts_safe_path():
    """相切不算过切：B2 件 1 必须 0 个点，B3 必须 >0 个点。"""
    ocl = pytest.importorskip("opencamlib")  # noqa: F841
    from m6_opencamlib import oracle_check, stlsurf
    from cadcam.modalities.cam import heightmap, process
    from cadcam.verify import dexel
    geom, inst = _part("1")
    plan = process.plan(geom, inst)
    target = dexel.rasterize(geom, res=0.5)
    surf, _ = stlsurf(geom)
    good, mg = heightmap.generate(plan, target, res=0.5)
    bad, mb = heightmap.generate(plan, target, res=0.5, inflate=False)
    assert oracle_check(surf, good, mg)["gouge_points"] == 0
    assert oracle_check(surf, bad, mb)["gouge_points"] > 0


def test_m6_ocl_report_is_self_consistent():
    """台账在仓库里就校验：四条证据的数字不能自相矛盾。"""
    path = os.path.join(ROOT, "reports", "m6_ocl.json")
    if not os.path.exists(path):
        pytest.skip("m6_ocl.json 未生成")
    with open(path, encoding="utf-8") as fh:
        rep = json.load(fh)
    s = rep["summary"]
    assert s["n"] == 10
    assert s["gui_required"] is False
    assert s["geometry_min_footprint_iou"] == 1.0
    assert s["B2_ocl_gouge_points"] > 0            # 第三方确实查出了问题
    assert s["B2_guard_ocl_gouge_points"] < s["B2_ocl_gouge_points"] / 20
    assert s["B3_ocl_gouge_points"] > s["B2_ocl_gouge_points"] * 10
    assert s["OCL_toolpath_ocl_gouge_points"] == 0
    assert s["resolution_sweep_monotone"] is True
    rates = s["resolution_sweep_rates_pct"]
    assert rates == sorted(rates, reverse=True)
