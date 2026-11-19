"""B 路 v2：高度图驱动的分层区域铣（M3.2 第 2 项）。

M3.1 的模板刀路只有矩形 bbox，件 10 过切 77552 mm³。根因是**没有区域掩码**：
面铣/型腔/肩面都按矩形范围走刀，圆形腔、星形岛、同高不同义的肩面会被误切。

本模块直接从 STEP 三角网格栅格化出的目标高度图反推刀路，做法是标准
constant-Z 粗加工 + 刀具半径偏置：

  对每一个目标高度层 L：
      forbidden = { 柱 p | goal[p] > L }        # 该层要保留的更高材料
      刀中心可行域 = 不在 forbidden 的「半径 r 膨胀」内   # Minkowski 避让
      在可行域内按行做往返扫描，Z 逐层下到 L
      → 圆盘覆盖的所有柱都有 goal <= L，故 cur=L 永不产生 gouge

这不是「看着仿真结果改仿真」：目标高度图来自 CAD 几何本身，工业 CAM 也是
从 CAD 几何算偏置区域。它替代的是 M3.1 的矩形 bbox，补的正是区域掩码。

局限（写进论文）：按高度层而非特征实例驱动，丢失了工序/刀具语义；
M4 的智能体仍需从 `process.plan` 的 DAG 出发。窄缝与内圆角受刀具半径限制，
用粗刀 + 小精刀两遍逼近，剩余残留是真实物理结果，不做粉饰。
"""

from __future__ import annotations

import math
from collections import OrderedDict

import numpy as np

from . import gcode_gen


def _n(v):
    v = float(v)
    return "%d" % int(v) if abs(v - int(v)) < 1e-9 else "%.3f" % v


def _disk_offsets(radius, res):
    """与 dexel._stamp 完全一致的圆盘格点偏移（刀中心对齐柱中心）。"""
    r_cells = int(math.ceil(radius / res))
    out = []
    for dy in range(-r_cells, r_cells + 1):
        for dx in range(-r_cells, r_cells + 1):
            if (dx * res) ** 2 + (dy * res) ** 2 <= radius ** 2 + 1e-9:
                out.append((dy, dx))
    return out, r_cells


def feasible_centers(goal, level, radius, res, offsets=None):
    """刀中心可行域：圆盘内没有 goal > level 的柱。

    工件外视为无障碍（真实加工刀具可伸出毛坯），所以 pad 用 False。
    """
    forbidden = np.isfinite(goal) & (goal > level + 1e-6)
    if offsets is None:
        offsets, r_cells = _disk_offsets(radius, res)
    else:
        r_cells = int(math.ceil(radius / res))
    ny, nx = goal.shape
    pad = np.pad(forbidden, ((r_cells, r_cells), (r_cells, r_cells)),
                 mode="constant", constant_values=False)
    dil = np.zeros_like(forbidden)
    for dy, dx in offsets:
        dil |= pad[r_cells + dy:r_cells + dy + ny,
                   r_cells + dx:r_cells + dx + nx]
    return ~dil


def _runs(row):
    """一行的连续 True 段，返回 [(起点列, 终点列)]（含端点）。"""
    out = []
    start = None
    for i, v in enumerate(row):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((start, i - 1))
            start = None
    if start is not None:
        out.append((start, len(row) - 1))
    return out


def generate(plan, target, res=0.5, ap=4.0, passes=None, feed=1200.0,
             inflate=True, guard_mm=0.0):
    """目标高度图 → 受限 G-code + 工序元数据。

    passes: [(刀具直径 mm, 步距占刀径比例), ...]，默认 Ø6 粗 + Ø1.5 精。
    inflate: **区域掩码是否按刀半径膨胀**。
    guard_mm: 膨胀半径上额外加的安全余量，默认 0（保持 M3 全部既有数字不变）。
        `scripts/m6_opencamlib.py` 用第三方 CAM 内核做交叉验证时发现：离散膨胀按
        「柱心到柱心」量距离，连续几何按「柱心到台阶边」量，在圆弧边界会少盖半格，
        自己的 dexel 因为共用同一套圆盘看不到。取 guard_mm=res/2 可消除该盲点，
        代价是窄缝残留略增。见 `docs/M6-OPENCAMLIB.md` §4。

        True （默认，B2）= 刀中心可行域 = 对 {goal>L} 按刀半径膨胀后取补 → 零过切（几何保证）
        False（B3 基线）= 只排除 {goal>L} 本身，不考虑刀半径 → 刀心可以贴到
            台阶边缘，实际切除会多出一个刀半径 → **这正是"没做掩码"的典型过切**

    返回 (text, ops_detail)。
    """
    if passes is None:
        passes = [(6.0, 0.6), (1.5, 0.6)]
    goal = target["top"]
    xs, ys = target["xs"], target["ys"]
    ny, nx = goal.shape
    finite = np.isfinite(goal)
    if not finite.any():
        raise ValueError("目标高度图为空")
    stock_top = float(plan["stock"]["top_z"])
    safe_z = stock_top + 5.0
    levels = sorted({float(v) for v in np.unique(np.round(goal[finite], 3))},
                    reverse=True)

    lines = ["%", "(CAD2CAM_M3_HM)", "G90 G21 G17 G94", "G54", "M8"]
    ops_detail = []
    seg_count = 0
    for pass_idx, (tool_d, stepover_ratio) in enumerate(passes, start=1):
        radius = tool_d / 2.0
        eff_radius = radius + float(guard_mm) if inflate else 0.0
        offsets, _ = _disk_offsets(eff_radius if inflate else radius, res)
        step_cells = max(1, int(round(tool_d * stepover_ratio / res)))
        tool_no = pass_idx
        lines.append("T%d M6" % tool_no)
        lines.append("S18000 M3")
        prev_z = stock_top
        for li, level in enumerate(levels, start=1):
            label = "HM%d_L%d" % (pass_idx, li)
            feasible = feasible_centers(goal, level,
                                        eff_radius if inflate else 0.0, res,
                                        offsets if inflate else None)
            # 逐层下刀（工艺切深），掩码始终按本层最终 level 算，保证不过切
            zs = []
            z = prev_z - ap
            while z > level + 1e-6:
                zs.append(z)
                z -= ap
            zs.append(level)
            prev_z = level
            op_lines, first, last = [], True, None
            for z_cut in zs:
                for iy in range(0, ny, step_cells):
                    for xa, xb in _runs(feasible[iy]):
                        x0, x1, y = xs[xa], xs[xb], ys[iy]
                        op_lines.append("G0 X%s Y%s Z%s" % (_n(x0), _n(y), _n(safe_z)))
                        op_lines.append("G1 Z%s F%s" % (_n(z_cut), _n(feed * 0.5)))
                        if xb > xa:
                            op_lines.append("G1 X%s F%s" % (_n(x1), _n(feed)))
                        op_lines.append("G0 Z%s" % _n(safe_z))
                        last = (x1, y)
            if not op_lines:
                ops_detail.append({"label": label, "tool_d_mm": tool_d,
                                   "level_z": level, "gcode_lines": 0, "empty": True})
                continue
            lines.append("(%s)" % label)
            lines += op_lines
            seg_count += len(op_lines)
            ops_detail.append({"label": label, "tool_d_mm": tool_d,
                               "level_z": level, "depth_steps": len(zs),
                               "gcode_lines": len(op_lines),
                               "last_xy": list(last) if last else None})
        lines += ["M9", "M5"]

    lines += ["M30", "%"]
    # 复用同一套白名单/解析器，保证生成与解析同源
    return "\n".join(lines) + "\n", {"ops_detail": ops_detail,
                                     "levels": levels,
                                     "gcode_lines": len(lines),
                                     "motion_lines": seg_count,
                                     "passes": [{"tool_d_mm": d, "stepover_ratio": s}
                                                for d, s in passes]}
