"""G 代码（.ngc）解析器 -> 规范刀路 IR + 统计。

只覆盖三轴铣削 + 钻孔循环的通用子集，这既是研究范围的边界，也是安全边界：
不在白名单内的 G/M 代码一律记录为 UNKNOWN 并计入告警，不做解释性执行。
"""

import math
import re

# 解析器语义版本：统计口径变化（如圆弧扫角、工序切分）时必须递增，
# 便于报告数据溯源 —— v1.0 把劣弧当优弧算，v1.1 修正顺逆时针。
PARSER_VERSION = "1.2.0"

WORD = re.compile(r"([A-Za-z])\s*([-+]?(?:\d+\.?\d*|\.\d+))")
COMMENT = re.compile(r"\(([^)]*)\)")

MOTION_CODES = {"G0": "rapid", "G1": "linear", "G2": "arc_cw", "G3": "arc_ccw",
                "G81": "drill", "G82": "drill_dwell", "G83": "drill_peck"}
WHITELIST_G = {
    "G0", "G1", "G2", "G3", "G17", "G18", "G19", "G20", "G21",
    "G40", "G43", "G49", "G53", "G54", "G55", "G56", "G57", "G58", "G59",
    "G64", "G73", "G80", "G81", "G82", "G83", "G90", "G91", "G94",
    "G90.1", "G91.1", "G98", "G99",
}
WHITELIST_M = {"M0", "M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9", "M30"}


def _gcode_key(letter, value):
    if letter in ("G", "M"):
        if float(value).is_integer():
            return "%s%d" % (letter, int(float(value)))
        return "%s%s" % (letter, value)
    return None


def _parse_block(line):
    """返回 (words, comments)；words 为 [(letter, float)]。"""
    comments = COMMENT.findall(line)
    stripped = COMMENT.sub(" ", line)
    stripped = stripped.split(";")[0]
    words = []
    for letter, value in WORD.findall(stripped):
        try:
            words.append((letter.upper(), float(value)))
        except ValueError:
            continue
    return words, comments


def _arc_length(start, end, center, plane, clockwise=False):
    axis = {"G17": (0, 1), "G18": (0, 2), "G19": (1, 2)}.get(plane, (0, 1))
    a, b = axis
    s = (start[a] - center[a], start[b] - center[b])
    e = (end[a] - center[a], end[b] - center[b])
    radius = math.hypot(*s)
    if radius < 1e-9:
        return _dist(start, end)
    angle_start = math.atan2(s[1], s[0])
    angle_end = math.atan2(e[1], e[0])
    sweep = angle_end - angle_start
    if clockwise:
        while sweep >= 0:
            sweep -= 2 * math.pi
    else:
        while sweep <= 0:
            sweep += 2 * math.pi
    return radius * abs(sweep)


def _dist(a, b):
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


# 后处理器常见的头部元信息关键词：出现在前导注释里，不是工序名
_META_WORDS = (
    "MASCHINE", "MACHINE", "HERSTELLER", "MANUFACTURER", "MODEL",
    "DESCRIPTION", "OPERATOR", "DATE", "FILE", "PROGRAM", "POST",
    "VERSION", "UNITS", "STOCK", "MATERIAL", "SETUP", "SPINDLE",
)


def _clean_label(comment):
    """把一行注释规范成工序名；不满足条件返回 None。

    真实工序名通常很短（PLANEN2 / FACE MILL / 型腔粗铣），而文件头注释
    是说明句。故用「长度 + 词数 + 元信息关键词 + 参数式」四道过滤。
    """
    candidate = (comment or "").strip()
    if not candidate or "=" in candidate:
        return None
    if len(candidate) > 32 or len(candidate.split()) > 4:
        return None
    upper = candidate.upper()
    if any(word in upper for word in _META_WORDS):
        return None
    # 刀具定义：(T1 D=6. CR=0. - ZMIN=-19. - SCHAFTFRSER)
    if re.match(r"^T\d+", upper):
        return None
    return candidate


def _looks_like_part(candidate):
    """头部注释里形如 STUFE_9 / PART-A 的单 token，视为零件名线索。"""
    return bool(re.match(r"^[A-Za-z0-9][A-Za-z0-9_\-. ]{0,23}$", candidate)) and len(candidate.split()) <= 2


def _finalize_operations(operations, segments):
    """丢弃「零切削且无名称」的占位工序（通常是程序头回零的纯空行程）。

    只丢弃完全没切削的占位项，避免误删真实的纯定位工序；其 segment 归并到
    下一个有名工序，保证 segment.op 仍能对应到工序表。
    """
    if not operations:
        return operations
    kept, dropped = [], []
    for index, op in enumerate(operations):
        if op["cut_mm"] <= 0 and op["label"].startswith("op_"):
            successor = next(
                (o for o in operations[index + 1:] if not (o["cut_mm"] <= 0 and o["label"].startswith("op_"))),
                None,
            )
            dropped.append((op, successor or (kept[-1] if kept else None)))
            continue
        kept.append(op)
    if not kept:
        return operations
    for op, target in dropped:
        if target is None:
            continue
        for seg in segments:
            if seg["op"] == op["label"]:
                seg["op"] = target["label"]
    return kept


def parse(text):
    position = [0.0, 0.0, 0.0]
    absolute = True
    arc_absolute = False
    plane = "G17"
    feed = None
    spindle = None
    spindle_on = False
    tool = None
    motion = None

    operations = []
    pending_label = None
    current_op = None
    # 工序区起点 = 首次出现换刀(M6/T 字)或首次运动；之前属于文件头元信息区。
    # 不能只看运动：短程序可能先给工序注释再走刀；也不能只看换刀：
    # 有的后处理器在程序头先 G53 G0 Z0 回零，再到 T1 M6。
    seen_setup = False
    part_hint = None

    segments = []
    tools = []
    planes_used = set()
    warnings = []
    unknown = set()
    blocks = 0

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line == "%":
            continue
        blocks += 1
        words, comments = _parse_block(line)
        label = None
        for comment in comments:
            candidate = _clean_label(comment)
            if not candidate:
                continue
            if not seen_setup:
                # 头部注释：记录零件名线索，但不当作工序
                if part_hint is None and _looks_like_part(candidate):
                    part_hint = candidate
                continue
            label = candidate
            break
        if label:
            if current_op is None or not current_op["segments"]:
                if current_op is not None:
                    current_op["label"] = label
                pending_label = label
            else:
                current_op = {"label": label, "tool": tool, "segments": 0,
                              "cut_mm": 0.0, "rapid_mm": 0.0}
                operations.append(current_op)
                pending_label = None

        block_motion = None
        block_coords = {}
        block_feed = feed
        block_tool = None
        spindle_cmd = None
        is_tool_change = False

        for letter, value in words:
            key = _gcode_key(letter, value)
            if letter == "G":
                if key in ("G90",):
                    absolute = True
                    continue
                if key in ("G91",):
                    absolute = False
                    continue
                if key == "G90.1":
                    arc_absolute = True
                    continue
                if key == "G91.1":
                    arc_absolute = False
                    continue
                if key in ("G17", "G18", "G19"):
                    plane = key
                    planes_used.add(key)
                    continue
                if key in ("G20", "G21"):
                    continue
                if key in MOTION_CODES:
                    block_motion = key
                    motion = key
                    continue
                if key == "G80":
                    motion = None
                    continue
                if key not in WHITELIST_G and key:
                    unknown.add(key)
                continue
            if letter == "M":
                if key == "M3" or key == "M4":
                    spindle_cmd = "on"
                elif key == "M5":
                    spindle_cmd = "off"
                elif key == "M6":
                    is_tool_change = True
                    seen_setup = True
                if key not in WHITELIST_M and key:
                    unknown.add(key)
                continue
            if letter in ("X", "Y", "Z"):
                block_coords[letter] = value
                continue
            if letter in ("I", "J", "K", "R"):
                block_coords[letter] = value
                continue
            if letter == "F":
                block_feed = value
                feed = value
                continue
            if letter == "S":
                spindle = value
                continue
            if letter == "T":
                block_tool = int(value)
                tool = block_tool
                seen_setup = True
                continue

        if spindle_cmd == "on":
            spindle_on = True
        elif spindle_cmd == "off":
            spindle_on = False

        if is_tool_change or block_tool is not None:
            if block_tool is not None and block_tool not in tools:
                tools.append(block_tool)
            if current_op is not None and not current_op["segments"]:
                current_op["tool"] = block_tool if block_tool is not None else tool
            else:
                current_op = {"label": pending_label or ("T%s" % block_tool if block_tool else "op_%d" % (len(operations) + 1)),
                              "tool": block_tool if block_tool is not None else tool,
                              "segments": 0, "cut_mm": 0.0, "rapid_mm": 0.0}
                operations.append(current_op)
            pending_label = None

        if current_op is None:
            current_op = {"label": pending_label or "op_1", "tool": tool,
                          "segments": 0, "cut_mm": 0.0, "rapid_mm": 0.0}
            operations.append(current_op)
            pending_label = None

        if block_motion is None:
            if block_coords and motion is not None and any(k in block_coords for k in "XYZ"):
                block_motion = motion
            else:
                continue

        seen_setup = True
        target = list(position)
        for index, axis in enumerate("XYZ"):
            if axis in block_coords:
                target[index] = block_coords[axis] if absolute else position[index] + block_coords[axis]
            elif motion in MOTION_CODES and MOTION_CODES[motion].startswith("drill") and axis != "Z":
                target[index] = position[index]

        kind = MOTION_CODES.get(block_motion, "linear")
        length = 0.0
        if kind in ("arc_cw", "arc_ccw"):
            center = list(position)
            if block_motion is None:
                block_motion = "G1"
            if "R" in block_coords:
                radius = block_coords["R"]
                chord = _dist(position, target)
                if radius and chord <= 2 * abs(radius):
                    length = 2 * abs(radius) * math.asin(min(1.0, chord / (2 * abs(radius))))
                else:
                    length = chord
                    warnings.append("ARC_RADIUS_INVALID at block %d" % blocks)
            else:
                axis = {"G17": (0, 1, "I", "J"), "G18": (0, 2, "I", "K"), "G19": (1, 2, "J", "K")}[plane]
                center = list(position)
                for pos, letter in ((axis[0], axis[2]), (axis[1], axis[3])):
                    offset = block_coords.get(letter, 0.0)
                    center[pos] = offset if arc_absolute else position[pos] + offset
                fixed = {"G17": 2, "G18": 1, "G19": 0}[plane]
                center[fixed] = position[fixed]
                length = _arc_length(position, target, center, plane, clockwise=(kind == "arc_cw"))
                if length < 1e-9:
                    warnings.append("ARC_ZERO_LENGTH at block %d" % blocks)
        elif kind.startswith("drill"):
            length = abs(target[2] - position[2])
        else:
            length = _dist(position, target)

        segments.append({
            "block": blocks,
            "kind": kind,
            "from": [round(v, 3) for v in position],
            "to": [round(v, 3) for v in target],
            "length": round(length, 3),
            "feed": None if kind == "rapid" else block_feed,
            "op": current_op["label"],
        })
        if kind == "rapid":
            current_op["rapid_mm"] += length
        else:
            current_op["cut_mm"] += length
        current_op["segments"] += 1
        position = target

    cut = sum(s["length"] for s in segments if s["kind"] != "rapid")
    rapid = sum(s["length"] for s in segments if s["kind"] == "rapid")
    zs = [p for s in segments for p in (s["from"][2], s["to"][2])]

    return {
        "blocks": blocks,
        "segments": segments,
        "operations": _finalize_operations(operations, segments),
        "tools": tools,
        "program": {"part_hint": part_hint},
        "stats": {
            "segments": len(segments),
            "cut_mm": round(cut, 1),
            "rapid_mm": round(rapid, 1),
            "air_cut_ratio": round(rapid / (cut + rapid), 4) if (cut + rapid) else None,
            "z_min": round(min(zs), 3) if zs else None,
            "z_max": round(max(zs), 3) if zs else None,
            "planes": sorted(planes_used),
        },
        "warnings": warnings,
        "unknown_codes": sorted(unknown),
    }
