"""M3 工艺知识库：材料 × 刀具 × 切削参数包络。

设计约束（DEVELOPMENT-PLAN §4.2）：LLM 只能在包络内选值并给出理由，
**越界由网关硬钳制并记录违规**。这里就是这个钳制器，不做任何"智能推荐"。

数值是 3 轴铣削的保守工程区间，用于把参数空间关进安全盒；不是工艺优化结果。
"""

from __future__ import annotations

import math

MATERIALS = {
    "Al6061-T6": {
        "vc": (150.0, 600.0),   # m/min 切削线速度
        "fz": (0.02, 0.20),     # mm/齿
        "ap": (0.2, 8.0),       # mm 轴向切深
        "ae": (0.5, 12.0),      # mm 径向切宽
        "note": "HB95，易切削，允许高转速",
    },
    "45steel": {
        "vc": (60.0, 200.0), "fz": (0.02, 0.15),
        "ap": (0.2, 6.0), "ae": (0.5, 10.0), "note": "中碳钢",
    },
    "Ti6Al4V": {
        "vc": (30.0, 90.0), "fz": (0.02, 0.10),
        "ap": (0.2, 4.0), "ae": (0.3, 6.0), "note": "导热差，线速度上限低",
    },
}

TOOLS = {
    "T1": {"type": "flat", "d_mm": 6.0, "flutes": 2, "note": "平底刀，与锚点集 .ngc 的 T1 D=6 一致"},
    "T2": {"type": "flat", "d_mm": 10.0, "flutes": 3, "note": "大直径平底刀，开粗"},
    "T3": {"type": "ball", "d_mm": 4.0, "flutes": 2, "note": "球头刀，清根"},
    "T4": {"type": "drill", "d_mm": 6.0, "flutes": 2, "note": "Ø6 钻头"},
    "T5": {"type": "drill", "d_mm": 10.0, "flutes": 2, "note": "Ø10 钻头"},
}

FIELDS = ("vc", "fz", "ap", "ae")


def envelope(material: str) -> dict:
    if material not in MATERIALS:
        raise KeyError("未知材料 %s，可选 %s" % (material, sorted(MATERIALS)))
    return dict(MATERIALS[material])


def clamp(material: str, *, vc: float, fz: float, ap: float, ae: float = 0.0,
           fields=FIELDS) -> tuple[dict, list]:
    """把参数钳进包络；返回 (钳制后参数, 结构化违规列表)。

    违规对象是 Repair Agent 的输入，不是给人看的字符串。
    """
    env = envelope(material)
    values = {"vc": vc, "fz": fz, "ap": ap, "ae": ae}
    out, violations = {}, []
    for key in FIELDS:
        if key not in fields:
            # 该字段对本工序不适用（如钻孔没有径向切宽 ae），不钳制也不记违规
            out[key] = None
            continue
        lo, hi = env[key]
        v = float(values[key])
        if not math.isfinite(v):
            violations.append({"code": "E_PARAM_NONFINITE", "field": key,
                               "value": str(values[key]), "clamped_to": lo})
            out[key] = lo
        elif v < lo:
            violations.append({"code": "E_PARAM_UNDER", "field": key, "value": v,
                               "bound": lo, "clamped_to": lo})
            out[key] = lo
        elif v > hi:
            violations.append({"code": "E_PARAM_OVER", "field": key, "value": v,
                               "bound": hi, "clamped_to": hi})
            out[key] = hi
        else:
            out[key] = v
    return out, violations


def speeds(tool_id: str, params: dict) -> dict:
    """由 vc / fz 换算主轴转速与进给，供 G-code 的 S / F 字使用。"""
    tool = TOOLS[tool_id]
    d = tool["d_mm"]
    rpm = 1000.0 * params["vc"] / (math.pi * d)
    feed = params["fz"] * tool["flutes"] * rpm
    return {"spindle_rpm": int(round(rpm)), "feed_mm_min": round(feed, 1),
            "tool_d_mm": d, "flutes": tool["flutes"]}


def pick_tool(diameter_mm: float | None, kind: str) -> str:
    """按孔径挑钻头；铣削固定用 T1（Ø6），避免刀库组合爆炸。"""
    if kind == "drill":
        return "T5" if (diameter_mm and diameter_mm >= 9.0) else "T4"
    if kind == "ball":
        return "T3"
    return "T2" if (diameter_mm and diameter_mm >= 12.0) else "T1"
