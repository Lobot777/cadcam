"""M3 工序映射：特征实例 → 工序 DAG。

输入是 M2 的实例聚合结果（`mfr.instances.group`），不是面级标签——
工艺规划需要「这三个 Ø6 孔是三道工序」，面级标签给不了这个。

本版是**可解释的模板映射**，不是优化器：每个特征族固定一个工序模板，
参数由几何量（bbox / 直径）与知识库包络决定。LLM 在 M4 才介入选参。
"""

from __future__ import annotations

from . import knowledge

# 特征族 -> 工序模板。None 表示本版不生成刀路（记录原因，不静默丢弃）
FAMILY_TO_OP = {
    "stock": "face_mill",
    "pocket": "pocket_mill",
    "slot": "pocket_mill",
    "step": "face_mill",
    "hole": "drill",
    "boss": "contour",
    "fillet": None,
    "chamfer": None,
    "freeform": None,
}

# 每族的保守默认参数（会被 knowledge.clamp 钳进材料包络）
DEFAULT_PARAMS = {
    "face_mill": {"vc": 300.0, "fz": 0.08, "ap": 0.5, "ae": 4.0},
    "pocket_mill": {"vc": 250.0, "fz": 0.06, "ap": 2.0, "ae": 3.0},
    "drill": {"vc": 200.0, "fz": 0.05, "ap": 3.0, "ae": 0.0},
    "contour": {"vc": 280.0, "fz": 0.06, "ap": 2.0, "ae": 1.5},
}


def _box_of(inst):
    b = inst.get("bbox")
    return b if b and b.get("size") else None


def plan(geom, instances, material="Al6061-T6", allowance=1.0):
    """返回 {"stock", "ops", "skipped", "violations"}。

    ops 按「面铣 → 型腔 → 钻孔 → 轮廓」排序，depends_on 只依赖前一道，
    形成线性 DAG（后续 M4 可在此基础上做并行/装夹分组）。

    allowance：毛坯顶面余量（mm）。默认 1.0；M4 用它构造不同的工艺变体，
    余量越大面铣切得越深，是真实车间会遇到的情形。
    """
    bb = geom["bbox"]
    # 毛坯在成品顶面留余量：否则「面铣」无料可切，dexel 仿真退化成空操作。
    allowance = float(allowance)
    finished_top = float(bb["max"][2])
    stock = {"min": list(bb["min"]),
             "max": [float(bb["max"][0]), float(bb["max"][1]), finished_top + allowance],
             "top_z": finished_top + allowance, "bottom_z": float(bb["min"][2]),
             "finished_top_z": finished_top, "allowance_mm": allowance}
    ops, skipped, violations = [], [], []
    counter = [0]

    def add(label, kind, inst, box, extra=None, z_bottom_override=None):
        counter[0] += 1
        oid = "OP%02d" % counter[0]
        fields = ("vc", "fz", "ap") if kind == "drill" else knowledge.FIELDS
        params, bad = knowledge.clamp(material, fields=fields, **DEFAULT_PARAMS[kind])
        for v in bad:
            v = dict(v)
            v["op_id"] = oid
            violations.append(v)
        tool = knowledge.pick_tool(inst.get("diameter_mm"), "drill" if kind == "drill" else "flat")
        z_bottom = (float(z_bottom_override) if z_bottom_override is not None
                    else max(stock["bottom_z"], float(box["min"][2])))
        op = {
            "id": oid, "label": label, "op": kind,
            "family": inst.get("family"), "faces": inst.get("faces", []),
            "tool": tool,
            "bbox": {"min": [float(x) for x in box["min"]],
                     "max": [float(x) for x in box["max"]]},
            "z_top": stock["top_z"], "z_bottom": z_bottom,
            "diameter_mm": inst.get("diameter_mm"),
            "radii_mm": inst.get("radii_mm", []),
            "params": params,
            "speeds": knowledge.speeds(tool, params),
            "depends_on": [ops[-1]["id"]] if ops else [],
        }
        if extra:
            op.update(extra)
        ops.append(op)

    # 1) 面铣：所有 stock 面合成一道（毛坯顶面一次装夹搞定）
    stock_boxes = [_box_of(i) for i in instances if i.get("family") == "stock"]
    stock_boxes = [b for b in stock_boxes if b]
    if stock_boxes:
        merged = {
            "min": [min(b["min"][k] for b in stock_boxes) for k in range(3)],
            "max": [max(b["max"][k] for b in stock_boxes) for k in range(3)],
        }
        add("FACE", "face_mill", {"family": "stock", "faces": []}, merged,
            z_bottom_override=stock["finished_top_z"])

    # 2) 型腔 / 槽 / 台阶：按面积从大到小，先开大腔
    cavities = [i for i in instances if i.get("family") in ("pocket", "slot", "step")]
    cavities.sort(key=lambda i: -(i.get("area_mm2") or 0))
    for i, inst in enumerate(cavities, start=1):
        box = _box_of(inst)
        if not box:
            skipped.append({"family": inst.get("family"), "reason": "no_bbox"})
            continue
        kind = "face_mill" if inst.get("family") == "step" else "pocket_mill"
        label = "STEP%d" % i if kind == "face_mill" else "POCKET%d" % i
        # step 是水平肩面，按 bbox.min.z 下刀会把旁边更高的凸台一起削掉；
        # 只在肩面自身高度走刀。
        zfix = float(box["max"][2]) if kind == "face_mill" else None
        add(label, kind, inst, box, z_bottom_override=zfix)

    # 3) 孔：只对面型为圆柱的实例生成钻孔循环；孔底平面不重复生成
    holes = [i for i in instances if i.get("family") == "hole"]
    for i, inst in enumerate(holes, start=1):
        box = _box_of(inst)
        if not box or not inst.get("diameter_mm"):
            skipped.append({"family": "hole", "reason": "no_diameter_or_bbox"})
            continue
        # 圆柱孔壁：z 有跨度；水平孔底平面：z 无跨度 → 跳过
        if box["size"][2] < 0.05:
            skipped.append({"family": "hole", "reason": "hole_floor_plane"})
            continue
        add("DRILL%d" % i, "drill", inst, box)

    # 4) 凸台轮廓
    bosses = [i for i in instances if i.get("family") == "boss"]
    for i, inst in enumerate(bosses, start=1):
        box = _box_of(inst)
        if not box:
            skipped.append({"family": "boss", "reason": "no_bbox"})
            continue
        # 凸台是「要保留的材料」：轮廓工序只绕顶面走，不能按 bbox 底面下刀，
        # 否则等于把凸台本体铣掉（首轮实测过切的主要来源）。
        add("CONTOUR%d" % i, "contour", inst, box,
            z_bottom_override=float(box["max"][2]))

    for inst in instances:
        if inst.get("family") not in FAMILY_TO_OP:
            skipped.append({"family": inst.get("family"), "reason": "unknown_family"})
        elif FAMILY_TO_OP[inst["family"]] is None:
            skipped.append({"family": inst.get("family"), "reason": "template_not_implemented"})

    return {"material": material, "stock": stock, "ops": ops,
            "skipped": skipped, "violations": violations}
