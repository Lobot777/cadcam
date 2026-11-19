"""M3 的 G-code 合成器（B 路骨架）与后处理白名单校验器。

生成的是**受限 G-code 子集**（DEVELOPMENT-PLAN §4.2）：G0/G1/G2/G3/G81/G83
+ G17/G21/G54/G90/G94 + M3/M5/M6/M8/M9/M30，头部/尾部模板固定。
工序策略是模板化蛇形/环切/啄钻，目标是「可解析、可仿真、可判过切」，
**不是**工业级刀路优化；这层以后由 M4 的 LLM 选参替换。

复用 `ngc.WHITELIST_G/M` 与 `ngc.parse`，保证「生成器」和「解析器」共用同一套白名单，
不会出现自己生成、自己解析不了的代码。
"""

from __future__ import annotations

import math
import re

from . import ngc

CUTTING = {"G1", "G2", "G3", "G81", "G82", "G83", "G73"}
GM_TOKEN = re.compile(r"\b([GM])(\d+(?:\.\d+)?)\b")


def _n(v):
    v = float(v)
    return "%d" % int(v) if abs(v - int(v)) < 1e-9 else "%.3f" % v


def _xy(op, inset=0.0):
    lo, hi = op["bbox"]["min"], op["bbox"]["max"]
    return (lo[0] + inset, lo[1] + inset, hi[0] - inset, hi[1] - inset)


def _face_mill(op, feed, safe_z):
    x0, y0, x1, y1 = _xy(op)
    d = op["speeds"]["tool_d_mm"]
    step = max(0.5, 0.6 * d)
    z = op["z_bottom"]
    out = ["G0 X%s Y%s Z%s" % (_n(x0), _n(y0), _n(safe_z)),
           "G1 Z%s F%s" % (_n(z), _n(feed * 0.5))]
    y, flip = y0, False
    while y <= y1 + 1e-9:
        xa, xb = (x1, x0) if flip else (x0, x1)
        out.append("G1 X%s Y%s F%s" % (_n(xa), _n(y), _n(feed)))
        if not flip:
            out.append("G1 X%s F%s" % (_n(xb), _n(feed)))
        else:
            out.append("G1 X%s F%s" % (_n(xb), _n(feed)))
        flip = not flip
        y += step
    out.append("G0 Z%s" % _n(safe_z))
    return out


def _pocket_mill(op, feed, safe_z):
    x0, y0, x1, y1 = _xy(op)
    d = op["speeds"]["tool_d_mm"]
    stepdown = max(0.5, min(op["params"]["ap"] or 2.0, 3.0))
    zs = []
    z = op["z_top"] - stepdown
    while z > op["z_bottom"] + 1e-9:
        zs.append(z)
        z -= stepdown
    zs.append(op["z_bottom"])
    out = []
    for zz in zs:
        out.append("G0 X%s Y%s Z%s" % (_n(x0), _n(y0), _n(safe_z)))
        out.append("G1 Z%s F%s" % (_n(zz), _n(feed * 0.5)))
        inset = 0.0
        while (x1 - inset) - (x0 + inset) > d * 0.5 and (y1 - inset) - (y0 + inset) > d * 0.5:
            a, b, c, e = _xy(op, inset)
            out += ["G1 X%s Y%s F%s" % (_n(a), _n(b), _n(feed)),
                    "G1 X%s Y%s" % (_n(c), _n(b)),
                    "G1 X%s Y%s" % (_n(c), _n(e)),
                    "G1 X%s Y%s" % (_n(a), _n(e)),
                    "G1 X%s Y%s" % (_n(a), _n(b))]
            inset += max(0.5, 0.6 * d)
    out.append("G0 Z%s" % _n(safe_z))
    return out


def _drill(op, feed, safe_z):
    box = op["bbox"]
    cx = (box["min"][0] + box["max"][0]) / 2.0
    cy = (box["min"][1] + box["max"][1]) / 2.0
    pecks = max(0.5, op["params"]["ap"] or 3.0)
    return ["G0 X%s Y%s Z%s" % (_n(cx), _n(cy), _n(safe_z)),
            "G83 X%s Y%s Z%s R%s Q%s F%s" % (
                _n(cx), _n(cy), _n(op["z_bottom"]), _n(safe_z - 2.0),
                _n(pecks), _n(feed)),
            "G80", "G0 Z%s" % _n(safe_z)]


def _contour(op, feed, safe_z):
    x0, y0, x1, y1 = _xy(op)
    z = op["z_bottom"]
    return ["G0 X%s Y%s Z%s" % (_n(x0), _n(y0), _n(safe_z)),
            "G1 Z%s F%s" % (_n(z), _n(feed * 0.5))] + [
            "G1 X%s Y%s F%s" % (_n(x), _n(y), _n(feed))
            for x, y in ((x1, y0), (x1, y1), (x0, y1), (x0, y0))
            ] + ["G0 Z%s" % _n(safe_z)]


BUILDERS = {"face_mill": _face_mill, "pocket_mill": _pocket_mill,
            "drill": _drill, "contour": _contour}


def generate(plan):
    """工序 DAG → G-code 文本。"""
    safe_z = plan["stock"]["top_z"] + 5.0
    lines = ["%", "(CAD2CAM_M3)", "G90 G21 G17 G94", "G54"]
    for op in plan["ops"]:
        builder = BUILDERS.get(op["op"])
        if builder is None:
            continue
        sp = op["speeds"]
        lines += ["T%s M6" % op["tool"][1:],
                  "(%s)" % op["label"],
                  "S%d M3" % sp["spindle_rpm"],
                  "M8"]
        lines += builder(op, sp["feed_mm_min"], safe_z)
        lines += ["M9", "M5"]
    lines += ["M30", "%"]
    return "\n".join(lines) + "\n"


def validate(text, stock=None):
    """白名单 + 模态 + G0 穿料检查。返回结构化错误对象，供 Repair Agent 消费。

    stock 给了（process.plan 的 stock 字典）就检查 E_COLLISION：
    G0 快速移动的**终点** Z 低于毛坯顶面、且 XY 落在毛坯范围内，
    意味着刀具在材料内部横移，是必须拦截的干涉。纯垂直抬刀不受影响。
    """
    errors = []
    spindle_on = False
    ended = False
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.split(";")[0]
        line = re.sub(r"\([^)]*\)", " ", line)
        tokens = []
        for letter, digits in GM_TOKEN.findall(line):
            f = float(digits)
            tokens.append("%s%d" % (letter, int(f)) if f.is_integer()
                          else "%s%s" % (letter, digits))
        for t in tokens:
            if t.startswith("G") and t not in ngc.WHITELIST_G:
                errors.append({"code": "E_SYNTAX_UNKNOWN_G", "line": lineno, "token": t})
            if t.startswith("M") and t not in ngc.WHITELIST_M:
                errors.append({"code": "E_SYNTAX_UNKNOWN_M", "line": lineno, "token": t})
        if "M3" in tokens or "M4" in tokens:
            spindle_on = True
        if "M5" in tokens:
            spindle_on = False
        if "M30" in tokens or "M2" in tokens:
            ended = True
            continue
        if ended and tokens:
            errors.append({"code": "E_SYNTAX_AFTER_END", "line": lineno})
        if not spindle_on and any(t in CUTTING for t in tokens):
            errors.append({"code": "E_SPINDLE_OFF_CUT", "line": lineno})

    ir = ngc.parse(text)
    for code in ir["unknown_codes"]:
        errors.append({"code": "E_SYNTAX_UNKNOWN", "token": code})
    if stock is not None:
        lo, hi = stock["min"], stock["max"]
        top_z = float(stock["top_z"])
        for i, seg in enumerate(ir["segments"]):
            if seg["kind"] != "rapid":
                continue
            a, b = seg["from"], seg["to"]
            if math.hypot(b[0] - a[0], b[1] - a[1]) <= 1e-6:
                continue
            inside_xy = (lo[0] - 1e-6 <= b[0] <= hi[0] + 1e-6
                         and lo[1] - 1e-6 <= b[1] <= hi[1] + 1e-6)
            if inside_xy and b[2] < top_z - 1e-6:
                errors.append({"code": "E_COLLISION", "segment": i,
                               "op": seg.get("op"), "z_mm": round(b[2], 3),
                               "stock_top_mm": top_z,
                               "cause": "G0 快速移动在毛坯顶面以下横穿材料"})
    return {"ok": not errors, "errors": errors, "ir": ir}


def inject_defect(text, kind="gouge", amount_mm=1.0, stock_top=None):
    """给验收脚本注入已知缺陷，检验验证器是否真的能检出（不是空跑）。

    gouge     = 所有 G1 下刀再深 1mm → 应产生 E_GOUGE
    residual  = 所有 G1 下刀抬高 1mm → 应产生 E_RESIDUAL
    collision = 把带头两个 G0 水平定位的 Z 压到毛坯顶面下 1mm → 应产生 E_COLLISION
    """
    if kind not in ("gouge", "residual", "collision"):
        raise ValueError("未知缺陷类型 %s" % kind)
    if kind == "collision":
        if stock_top is None:
            raise ValueError("collision 注入需要 stock_top")
        out, touched = [], 0
        for line in text.splitlines():
            if (line.startswith("G0 ") and " X" in line and " Y" in line
                    and " Z" in line and touched < 2):
                line = re.sub(r"Z-?[\d.]+", "Z%s" % _n(float(stock_top) - 1.0), line)
                touched += 1
            out.append(line)
        if not touched:
            raise ValueError("没找到可注入的 G0 水平定位行")
        return "\n".join(out) + "\n"
    delta = -amount_mm if kind == "gouge" else amount_mm
    out = []
    for line in text.splitlines():
        m = re.match(r"^(G1 Z)(-?[\d.]+)(.*)$", line)
        if m:
            line = "%s%s%s" % (m.group(1), _n(float(m.group(2)) + delta), m.group(3))
        out.append(line)
    return "\n".join(out) + "\n"
