"""M3.1 验收：工艺库钳制、工序规划、白名单、仿真与缺陷注入自检。"""

import json
import os

import pytest

from cadcam.modalities.cam import gcode_gen, knowledge, ngc, process
from cadcam.modalities.mfr.instances import group
from cadcam.verify import dexel, dfm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEOM = os.path.join(ROOT, "reports", "annotation", "geom")
LABELS = os.path.join(ROOT, "data", "zenodo_rptu", "labels")


def _part(n):
    with open(os.path.join(GEOM, "%s.json" % n), encoding="utf-8") as fh:
        geom = json.load(fh)
    with open(os.path.join(LABELS, "%s.json" % n), encoding="utf-8") as fh:
        lab = json.load(fh)
    labels = [lab["faces"][str(f["index"])]["family"] for f in geom["faces"]]
    return geom, group(geom, labels, families={x: x for x in set(labels)})


def test_clamp_over_under_and_inapplicable_field():
    params, bad = knowledge.clamp("Al6061-T6", vc=900.0, fz=0.01, ap=2.0, ae=3.0)
    assert params["vc"] == 600.0 and params["fz"] == 0.02
    assert {v["field"] for v in bad} == {"vc", "fz"}
    assert all("clamped_to" in v for v in bad)

    params, bad = knowledge.clamp("Al6061-T6", vc=200.0, fz=0.05, ap=3.0,
                                  ae=0.0, fields=("vc", "fz", "ap"))
    assert bad == []
    assert params["ae"] is None, "钻孔没有径向切宽，不该被当成越界"


def test_speeds_scale_with_diameter():
    p = {"vc": 200.0, "fz": 0.05}
    s6 = knowledge.speeds("T4", p)
    s10 = knowledge.speeds("T5", p)
    assert s6["spindle_rpm"] > s10["spindle_rpm"]
    assert s6["feed_mm_min"] > 0 and s10["feed_mm_min"] > 0


def test_plan_produces_dag_and_stock_allowance():
    geom, inst = _part("2")
    p = process.plan(geom, inst)
    assert p["stock"]["allowance_mm"] == 1.0
    assert p["stock"]["top_z"] == geom["bbox"]["max"][2] + 1.0
    assert p["ops"][0]["label"] == "FACE"
    assert p["ops"][0]["z_bottom"] == geom["bbox"]["max"][2], "面铣只去掉余量"
    assert all(op["depends_on"] == ([p["ops"][i - 1]["id"]] if i else [])
               for i, op in enumerate(p["ops"]))
    assert p["violations"] == []


def test_generated_gcode_is_whitelisted_and_parseable():
    geom, inst = _part("2")
    text = gcode_gen.generate(process.plan(geom, inst))
    v = gcode_gen.validate(text)
    assert v["ok"], v["errors"]
    assert v["ir"]["unknown_codes"] == []
    assert v["ir"]["stats"]["segments"] > 20
    labels = [op["label"] for op in v["ir"]["operations"]]
    assert "FACE" in labels and "DRILL1" in labels
    assert "M30" in text and text.strip().startswith("%")


def test_validate_rejects_unknown_code_and_spindle_off_cutting():
    base = "G90 G21\nT1 M6\nS1000 M3\nG0 X0 Y0 Z5\nG1 Z1 F100\nM5\nM30\n"
    assert gcode_gen.validate(base)["ok"]
    bad = base.replace("G1 Z1 F100", "G1 Z1 F100 G999")
    codes = {e["code"] for e in gcode_gen.validate(bad)["errors"]}
    assert "E_SYNTAX_UNKNOWN_G" in codes
    off = base.replace("S1000 M3\n", "")
    codes = {e["code"] for e in gcode_gen.validate(off)["errors"]}
    assert "E_SPINDLE_OFF_CUT" in codes


def test_dexel_detects_injected_gouge_and_residual():
    geom, inst = _part("2")
    plan = process.plan(geom, inst)
    text = gcode_gen.generate(plan)
    target = dexel.rasterize(geom, res=0.5)
    assert target["coverage"] > 0.9
    radius_by_op = {op["label"]: op["speeds"]["tool_d_mm"] / 2.0 for op in plan["ops"]}

    def metrics(t):
        ir = ngc.parse(t)
        return dexel.simulate(ir, target, plan["stock"]["top_z"],
                              tool_radius_mm=3.0, res=0.5,
                              tool_radius_by_op=radius_by_op)

    clean = metrics(text)
    assert clean["gouge_volume_mm3"] == 0.0
    assert clean["residual_volume_mm3"] == 0.0

    gouge = metrics(gcode_gen.inject_defect(text, "gouge", 1.0))
    resid = metrics(gcode_gen.inject_defect(text, "residual", 1.0))
    assert gouge["gouge_volume_mm3"] > clean["gouge_volume_mm3"] + 1.0
    assert resid["residual_volume_mm3"] > clean["residual_volume_mm3"] + 1.0
    assert any(e["code"] == "E_GOUGE" for e in dexel.errors(gouge))
    assert any(e["code"] == "E_RESIDUAL" for e in dexel.errors(resid))


def test_dfm_emits_structured_errors():
    geom, inst = _part("9")
    errs = dfm.check(process.plan(geom, inst))
    assert errs, "件 9 有 R3 圆角，应触发 DFM 检查"
    for e in errs:
        assert "code" in e and "op_id" in e and "cause" in e and "suggest" in e


def test_full_pipeline_one_part():
    geom, inst = _part("2")
    plan = process.plan(geom, inst)
    text = gcode_gen.generate(plan)
    v = gcode_gen.validate(text)
    target = dexel.rasterize(geom, res=0.5)
    r_by_op = {op["label"]: op["speeds"]["tool_d_mm"] / 2.0 for op in plan["ops"]}
    m = dexel.simulate(v["ir"], target, plan["stock"]["top_z"],
                       tool_radius_mm=3.0, res=0.5, tool_radius_by_op=r_by_op)
    assert v["ok"] and dexel.errors(m) == []
    assert m["columns"] > 10000


def test_dexel_reports_robust_deviation_stats():
    geom, inst = _part("2")
    plan = process.plan(geom, inst)
    text = gcode_gen.generate(plan)
    target = dexel.rasterize(geom, res=0.5)
    r_by_op = {op["label"]: op["speeds"]["tool_d_mm"] / 2.0 for op in plan["ops"]}
    m = dexel.simulate(gcode_gen.validate(text)["ir"], target,
                       plan["stock"]["top_z"], tool_radius_mm=3.0, res=0.5,
                       tool_radius_by_op=r_by_op)
    for key in ("mean_deviation_mm", "p95_deviation_mm", "p99_deviation_mm",
                "columns_over_0_5mm_pct"):
        assert key in m, key
    assert m["columns"] > 10000


def test_cam_reference_gcode_if_present():
    """A 路 .nc 由 freecadcmd 生成；环境没跑过生成器时跳过，不阻塞单测。"""
    import glob
    files = sorted(glob.glob(os.path.join(ROOT, "reports", "m3_gcode_ref", "*.nc")))
    if not files:
        pytest.skip("A 路参考刀路未生成，先跑 scripts/cam_reference.py")
    for path in files[:3]:
        with open(path, encoding="utf-8") as fh:
            v = gcode_gen.validate(fh.read())
        assert v["ok"] and v["ir"]["unknown_codes"] == []
        assert v["ir"]["stats"]["segments"] > 20


def test_heightmap_toolpath_has_zero_gouge_on_hard_parts():
    """M3.2 第 2 项：区域掩码必须把结构性过切压到 0（件 8/10 是原最差两件）。"""
    import glob
    from cadcam.modalities.cam import heightmap
    for n in ("2", "8", "10"):
        geom, inst = _part(n)
        plan = process.plan(geom, inst)
        target = dexel.rasterize(geom, res=0.5)
        text, meta = heightmap.generate(plan, target, res=0.5, ap=4.0)
        assert gcode_gen.validate(text)["ok"]
        r_by_op = {o["label"]: o["tool_d_mm"] / 2.0
                   for o in meta["ops_detail"] if not o.get("empty")}
        m = dexel.simulate(gcode_gen.validate(text)["ir"], target,
                           plan["stock"]["top_z"], tool_radius_mm=3.0,
                           res=0.5, tool_radius_by_op=r_by_op)
        assert m["gouge_volume_mm3"] == 0.0, "件 %s 仍有过切" % n
        assert m["mean_deviation_mm"] < 0.5, "件 %s 平均偏差未达标" % n


def test_errors_carry_op_id_and_face_ids_when_attribution_enabled():
    """M3.2 第 3 项：E_GOUGE 必须能定位到具体工序与面，供 Repair Agent 定点修复。"""
    geom, inst = _part("10")
    plan = process.plan(geom, inst)
    text = gcode_gen.generate(plan)
    target = dexel.rasterize(geom, res=0.5)
    r_by_op = {op["label"]: op["speeds"]["tool_d_mm"] / 2.0 for op in plan["ops"]}
    op_faces = {op["label"]: op.get("faces", []) for op in plan["ops"]}
    op_ids = {op["label"]: op["id"] for op in plan["ops"]}
    m = dexel.simulate(gcode_gen.validate(text)["ir"], target,
                       plan["stock"]["top_z"], tool_radius_mm=3.0, res=0.5,
                       tool_radius_by_op=r_by_op, op_faces=op_faces)
    errs = dexel.errors(m, op_ids=op_ids)
    g = next(e for e in errs if e["code"] == "E_GOUGE")
    assert g["op_id"].startswith("OP")
    assert g["face_ids"] and g["by_op"]
    assert g["by_op"][0]["op"] == "STEP3", "件 10 过切应归因到 z=34 的肩面工序"


def test_validate_detects_g0_collision_but_not_clean_rapids():
    """M3.2 第 4 项：G0 穿料检查要能检出注入缺陷，且对干净刀路零误报。"""
    geom, inst = _part("2")
    plan = process.plan(geom, inst)
    text = gcode_gen.generate(plan)
    clean = gcode_gen.validate(text, stock=plan["stock"])
    assert not any(e["code"] == "E_COLLISION" for e in clean["errors"])
    bad = gcode_gen.inject_defect(text, "collision",
                                  stock_top=plan["stock"]["top_z"])
    assert any(e["code"] == "E_COLLISION"
               for e in gcode_gen.validate(bad, stock=plan["stock"])["errors"])


def test_human_eval_pack_is_blind_and_balanced():
    """M3 人工验收包：真值来自注入、答案键不进答题页、验证器不循环论证。"""
    import glob
    base = os.path.join(ROOT, "reports", "m3_human")
    key_path = os.path.join(base, "key.json")
    if not os.path.exists(key_path):
        pytest.skip("人工包未生成：先跑 scripts/m3_human_pack.py")
    with open(key_path, encoding="utf-8") as fh:
        raw = json.load(fh)
    key = raw["items"] if isinstance(raw, dict) and "items" in raw else raw
    with open(os.path.join(base, "validator.json"), encoding="utf-8") as fh:
        val = json.load(fh)
    assert len(key) == 30 and set(key) == set(val)
    assert sum(v["truth"] == "gouge" for v in key.values()) == 15
    assert {v["difficulty"] for v in key.values()} == {"easy", "medium", "hard"}
    assert all(val[s]["predicted"] == key[s]["truth"] for s in key)
    # v2 起题序必须打乱：v1 的 H01–H15 全 no_gouge、H16–H30 全 gouge 是顺序泄漏
    if isinstance(raw, dict) and raw.get("pack_version") == "v2":
        truth = [key[s]["truth"] == "gouge" for s in sorted(key)]
        longest = run = 1
        for i in range(1, len(truth)):
            run = run + 1 if truth[i] == truth[i - 1] else 1
            longest = max(longest, run)
        assert longest <= 4, "题序又出现长串同真值，顺序泄漏复发"
    with open(os.path.join(base, "annotate.html"), encoding="utf-8") as fh:
        page = fh.read()
    assert page.count('<section class="card"') == 30
    assert '"truth"' not in page and "no_gouge" not in page.replace(
        'data-v="no_gouge"', ""), "答案键泄漏进答题页"
    assert len(glob.glob(os.path.join(base, "img", "H*.png"))) == 30


def test_b2_max_deviation_is_residual_not_gouge():
    """口径决策的机械证据：B2 零过切，max 偏差全部来自可补刀的残留。

    锁住这条事实，防止后续刀路/仿真改动把「过切」悄悄带回来却仍报低平均偏差。
    """
    import numpy as np
    from cadcam.modalities.cam import heightmap
    for n in ("2", "8", "10"):
        geom, inst = _part(n)
        plan = process.plan(geom, inst)
        target = dexel.rasterize(geom, res=0.5)
        text, meta = heightmap.generate(plan, target, res=0.5)
        r_by_op = {o["label"]: o["tool_d_mm"] / 2.0
                   for o in meta["ops_detail"] if not o.get("empty")}
        m = dexel.simulate(gcode_gen.validate(text)["ir"], target,
                           plan["stock"]["top_z"], tool_radius_mm=3.0,
                           res=0.5, tool_radius_by_op=r_by_op)
        goal = target["top"]
        mask = np.isfinite(goal)
        gouge = np.where(mask, np.maximum(0.0, goal - m["cur"]), 0.0)
        assert gouge.max() <= 1e-6, "件 %s 出现过切" % n
        assert m["mean_deviation_mm"] < 0.5


def test_human_pack_trivial_color_baseline_is_documented():
    """题目包的红/灰二分把答案写在图例上，必须留下可复核的基线数字。

    背景：v1 归因「题序泄漏」是错的。v2 打乱题序后三人仍是 1.000，
    因为 colorbar 标注「红=过切」，而问题就是二分类「有没有过切」。
    这里不追求基线低（当前它就是 1.000，即题目已达天花板），
    只锁住三件事：基线可复现、与真人一致率同量级、报告里有记录。
    """
    base = os.path.join(ROOT, "reports", "m3_human")
    samples_p, key_p = (os.path.join(base, "samples.json"),
                        os.path.join(base, "key.json"))
    if not (os.path.exists(samples_p) and os.path.exists(key_p)):
        pytest.skip("v2 题目包未生成")
    import sys
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import m3_human_baseline as hb

    with open(samples_p, encoding="utf-8") as fh:
        samples = json.load(fh)
    with open(key_p, encoding="utf-8") as fh:
        key = json.load(fh)["items"]

    reds = {s["sample_id"]: hb.red_pixels(s["png"]) for s in samples}
    neg = [v for s, v in reds.items() if key[s]["truth"] == "no_gouge"]
    pos = [v for s, v in reds.items() if key[s]["truth"] == "gouge"]
    # 无过切题不该有任何红；有过切题必须一眼可见。两条同时成立，
    # 说明「数红」这个零知识规则足以解题 —— 这正是天花板所在。
    assert max(neg) == 0
    assert min(pos) > 1000
    thr = max(neg) + 1
    acc = sum((reds[s] >= thr) == (key[s]["truth"] == "gouge")
              for s in reds) / len(reds)
    assert acc >= 0.9, "基线不再平凡：题目包可能已重设计，请同步 M3-HUMAN-QA.md"

    doc = os.path.join(ROOT, "docs", "M3-HUMAN-QA.md")
    with open(doc, encoding="utf-8") as fh:
        text = fh.read()
    assert "平凡" in text and "1.0000" in text, "结案文档未记录平凡基线"
