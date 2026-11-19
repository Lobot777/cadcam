"""M3 DFM 检查器：把工艺约束转成结构化错误对象。

本版覆盖计划 §4.3 的四类里可以纯几何判定的三种：
内圆角 vs 刀具半径、孔深径比、薄壁/可达性。
「刀柄包络 vs 夹具」需要装夹模型，留到 M3.2。
"""

from __future__ import annotations


def check(plan):
    errors = []
    stock = plan["stock"]
    for op in plan["ops"]:
        oid = op["id"]
        d = op["speeds"]["tool_d_mm"]
        radius = d / 2.0

        # 内圆角半径小于刀具半径 → 清不出来
        for r in op.get("radii_mm", []):
            if 0 < r < radius:
                errors.append({
                    "code": "E_DFM_CORNER_RADIUS", "op_id": oid,
                    "magnitude_mm": round(radius - r, 3),
                    "cause": "tool_radius(%.2f) > corner_radius(%.2f)" % (radius, r),
                    "suggest": {"tool_radius_max": round(r, 2)},
                })

        # 深径比 > 5 需要啄钻/减转速
        if op["op"] == "drill" and op.get("diameter_mm"):
            depth = op["z_top"] - op["z_bottom"]
            ratio = depth / op["diameter_mm"]
            if ratio > 5.0:
                errors.append({
                    "code": "E_DFM_DEEP_HOLE", "op_id": oid,
                    "magnitude_mm": round(depth, 2),
                    "cause": "depth/diameter = %.2f > 5" % ratio,
                    "suggest": {"use_peck_drill": True, "reduce_feed_pct": 20},
                })

        # 加工区域必须落在毛坯 XY 范围内
        lo, hi = op["bbox"]["min"], op["bbox"]["max"]
        if (lo[0] < stock["min"][0] - 1e-6 or lo[1] < stock["min"][1] - 1e-6
                or hi[0] > stock["max"][0] + 1e-6 or hi[1] > stock["max"][1] + 1e-6):
            errors.append({
                "code": "E_DFM_OUT_OF_STOCK", "op_id": oid,
                "cause": "operation bbox outside stock XY",
                "suggest": {"recheck_wcs": True},
            })

        # z 不得穿过零件底面
        if op["z_bottom"] < stock["bottom_z"] - 1e-6:
            errors.append({
                "code": "E_DFM_BELOW_FLOOR", "op_id": oid,
                "magnitude_mm": round(stock["bottom_z"] - op["z_bottom"], 2),
                "cause": "target z below stock bottom",
                "suggest": {"clamp_z": stock["bottom_z"]},
            })

    # 参数包络被钳制过 → 记录为可审计的违规，不静默吞掉
    for v in plan.get("violations", []):
        e = dict(v)
        e["code"] = "E_PARAM_CLAMPED"
        e.setdefault("cause", "cutting parameter outside material envelope")
        errors.append(e)

    return errors
