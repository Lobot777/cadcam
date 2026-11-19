"""Z-dexel 材料去除仿真（M3.1）。

表示法：每个 (x,y) 柱只记一个「当前材料顶面高度」cur，配合由三角网格
栅格化出的「目标顶面高度」target_top。对本项目这批 2.5D 方块类零件
（从上往下加工、无倒扣/侧凹）这是精确且便宜的表示；倒扣件需要多区间
dexel，本版明确不支持，见 `docs/M3-PLAN.md` 限制节。

判据：
    cur < target_top  → 过切 (gouge)
    cur > target_top  → 残留 (residual)
两者都按柱面积换算体积，并给出最大偏差与命中柱数，供结构化错误对象使用。
"""

from __future__ import annotations

import math

import numpy as np


def rasterize(geom, res=0.5):
    """把 STEP 三角网格栅格化成高度图。

    只取接近水平的三角形（投影面积足够大）：竖直侧壁投影成线段，
    对「顶面高度」没有贡献，跳过可避免噪声。
    """
    bb = geom["bbox"]
    x0, y0 = float(bb["min"][0]), float(bb["min"][1])
    x1, y1 = float(bb["max"][0]), float(bb["max"][1])
    nx = max(1, int(math.ceil((x1 - x0) / res)))
    ny = max(1, int(math.ceil((y1 - y0) / res)))
    xs = x0 + (np.arange(nx) + 0.5) * res
    ys = y0 + (np.arange(ny) + 0.5) * res
    top = np.full((ny, nx), -np.inf, dtype=float)
    bottom = np.full((ny, nx), np.inf, dtype=float)

    for face in geom["faces"]:
        for tri in face.get("triangles", []):
            p = np.asarray(tri, dtype=float)
            if p.shape != (3, 3):
                continue
            xy = p[:, :2]
            area2 = ((xy[1, 0] - xy[0, 0]) * (xy[2, 1] - xy[0, 1])
                     - (xy[2, 0] - xy[0, 0]) * (xy[1, 1] - xy[0, 1]))
            if abs(area2) < 1e-9:
                continue
            ix0 = max(0, int(math.floor((xy[:, 0].min() - x0) / res)))
            ix1 = min(nx, int(math.ceil((xy[:, 0].max() - x0) / res)) + 1)
            iy0 = max(0, int(math.floor((xy[:, 1].min() - y0) / res)))
            iy1 = min(ny, int(math.ceil((xy[:, 1].max() - y0) / res)) + 1)
            if ix1 <= ix0 or iy1 <= iy0:
                continue
            gx, gy = np.meshgrid(xs[ix0:ix1], ys[iy0:iy1])
            px = gx.ravel()
            py = gy.ravel()
            w1 = ((px - xy[0, 0]) * (xy[2, 1] - xy[0, 1])
                  - (py - xy[0, 1]) * (xy[2, 0] - xy[0, 0])) / area2
            w2 = ((xy[1, 0] - xy[0, 0]) * (py - xy[0, 1])
                  - (xy[1, 1] - xy[0, 1]) * (px - xy[0, 0])) / area2
            w0 = 1.0 - w1 - w2
            inside = (w0 >= -1e-7) & (w1 >= -1e-7) & (w2 >= -1e-7)
            if not inside.any():
                continue
            z = w0 * p[0, 2] + w1 * p[1, 2] + w2 * p[2, 2]
            sub_t = top[iy0:iy1, ix0:ix1].ravel().copy()
            sub_b = bottom[iy0:iy1, ix0:ix1].ravel().copy()
            sub_t[inside] = np.maximum(sub_t[inside], z[inside])
            sub_b[inside] = np.minimum(sub_b[inside], z[inside])
            top[iy0:iy1, ix0:ix1] = sub_t.reshape(iy1 - iy0, ix1 - ix0)
            bottom[iy0:iy1, ix0:ix1] = sub_b.reshape(iy1 - iy0, ix1 - ix0)

    return {"top": top, "bottom": bottom, "xs": xs, "ys": ys,
            "res": res, "x0": x0, "y0": y0,
            "coverage": float(np.isfinite(top).mean())}


def _stamp(cur, target, cx, cy, z, radius, res, owner=None, tag=0):
    """把半径 radius 的平底刀在 (cx,cy,z) 处扫过的柱顶面压到 z。

    owner/tag 可选：记录「最后把该柱切到更低高度」的工序号，
    供 E_GOUGE / E_RESIDUAL 反向定位到具体工序与面号（M4 Repair Agent 用）。
    """
    x0, y0 = target["x0"], target["y0"]
    ny, nx = cur.shape
    ix0 = max(0, int(math.floor((cx - radius - x0) / res)))
    ix1 = min(nx, int(math.ceil((cx + radius - x0) / res)) + 1)
    iy0 = max(0, int(math.floor((cy - radius - y0) / res)))
    iy1 = min(ny, int(math.ceil((cy + radius - y0) / res)) + 1)
    if ix1 <= ix0 or iy1 <= iy0:
        return
    gx, gy = np.meshgrid(target["xs"][ix0:ix1], target["ys"][iy0:iy1])
    disk = (gx - cx) ** 2 + (gy - cy) ** 2 <= radius ** 2
    block = cur[iy0:iy1, ix0:ix1]
    lowered = disk & (z < block)
    block[lowered] = z
    if owner is not None:
        owner_block = owner[iy0:iy1, ix0:ix1]
        owner_block[lowered] = tag


def simulate(ir, target, stock_top, tool_radius_mm=3.0, res=0.5,
             step_factor=0.8, tool_radius_by_op=None, op_faces=None):
    """沿解析后的刀路 IR 做材料去除，返回过切/残留指标。

    op_faces: {工序标签: [面号, ...]}，可选。给了就把每根过切/残留柱
    归因到「最后切到它的那道工序」，输出 gouge_by_op / residual_by_op。
    """
    cur = np.full_like(target["top"], float(stock_top))
    radius_of = tool_radius_by_op or {}
    op_labels = sorted({str(seg.get("op") or "") for seg in ir["segments"]})
    op_index = {label: i + 1 for i, label in enumerate(op_labels)}
    track = op_faces is not None
    owner = np.zeros(cur.shape, dtype=np.int16) if track else None
    for seg in ir["segments"]:
        kind = seg["kind"]
        if kind == "rapid":
            continue
        a, b = seg["from"], seg["to"]
        # 每道工序用自己的刀具半径；G83 用 Ø10 钻头却按 Ø6 仿真会虚报残留
        radius = float(radius_of.get(seg.get("op"), tool_radius_mm))
        tag = op_index.get(str(seg.get("op") or ""), 0) if track else 0
        if kind.startswith("drill"):
            cx, cy = b[0], b[1]
            _stamp(cur, target, cx, cy, min(a[2], b[2]), radius, res, owner, tag)
            continue
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        n = max(1, int(math.ceil(length / (res * step_factor))))
        for i in range(n + 1):
            t = i / float(n)
            _stamp(cur, target,
                   a[0] + (b[0] - a[0]) * t,
                   a[1] + (b[1] - a[1]) * t,
                   a[2] + (b[2] - a[2]) * t,
                   radius, res, owner, tag)

    goal = target["top"]
    mask = np.isfinite(goal)
    cell = res * res
    gouge = np.where(mask, np.maximum(0.0, goal - cur), 0.0)
    resid = np.where(mask, np.maximum(0.0, cur - goal), 0.0)
    dev = np.where(mask, np.abs(cur - goal), 0.0)
    through = np.where(np.isfinite(target["bottom"]),
                       np.maximum(0.0, target["bottom"] - cur), 0.0)
    dev_valid = dev[mask]

    def _attribute(diff):
        """按工序号聚合过切/残留柱，附带该工序的面号，供 Repair Agent 定点修复。"""
        if not track:
            return []
        out = []
        inv = {v: k for k, v in op_index.items()}
        for tag in np.unique(owner[(diff > 1e-6)]):
            sel = (owner == tag) & (diff > 1e-6)
            label = inv.get(int(tag), "")
            faces = (op_faces or {}).get(label, [])
            out.append({"op": label, "face_ids": list(faces)[:8],
                        "columns": int(sel.sum()),
                        "volume_mm3": round(float(diff[sel].sum() * cell), 2)})
        return sorted(out, key=lambda r: -r["volume_mm3"])[:5]

    return {
        "res_mm": res, "columns": int(mask.sum()),
        "gouge_volume_mm3": round(float(gouge.sum() * cell), 2),
        "residual_volume_mm3": round(float(resid.sum() * cell), 2),
        "gouge_columns": int((gouge > 1e-6).sum()),
        "residual_columns": int((resid > 1e-6).sum()),
        "max_deviation_mm": round(float(dev.max()), 3),
        # max 对单个够不到的窄缝柱极敏感，必须同时给分位数与超差占比
        "mean_deviation_mm": round(float(dev_valid.mean()), 4) if dev_valid.size else None,
        "p95_deviation_mm": round(float(np.percentile(dev_valid, 95)), 3) if dev_valid.size else None,
        "p99_deviation_mm": round(float(np.percentile(dev_valid, 99)), 3) if dev_valid.size else None,
        "columns_over_0_5mm": int((dev_valid > 0.5).sum()),
        "columns_over_0_5mm_pct": round(100.0 * float((dev_valid > 0.5).mean()), 3)
                                  if dev_valid.size else None,
        "through_cut_volume_mm3": round(float(through.sum() * cell), 2),
        "gouge_by_op": _attribute(gouge),
        "residual_by_op": _attribute(resid),
        "op_attribution": owner,
        "cur": cur,
    }


def errors(metrics, *, gouge_tol_mm3=1.0, residual_tol_mm3=1.0, op_ids=None):
    """把仿真指标转成结构化错误对象（Repair Agent 的输入）。

    op_ids: {工序标签: 工序 id}，可选。给了就在 E_GOUGE / E_RESIDUAL 上
    附 op_id / face_ids / by_op，让 M4 只重规划相关工序而不是全量重来。
    """
    def _loc(by_op):
        if not by_op:
            return {}
        top = by_op[0]
        return {"op_id": (op_ids or {}).get(top["op"], top["op"]),
                "op_label": top["op"],
                "face_ids": top["face_ids"],
                "by_op": by_op}

    out = []
    if metrics["gouge_volume_mm3"] > gouge_tol_mm3:
        out.append({"code": "E_GOUGE", "magnitude_mm3": metrics["gouge_volume_mm3"],
                    "columns": metrics["gouge_columns"],
                    "cause": "刀路切深超过目标表面",
                    "suggest": {"reduce_ap_mm": 0.5},
                    **_loc(metrics.get("gouge_by_op", []))})
    if metrics["residual_volume_mm3"] > residual_tol_mm3:
        out.append({"code": "E_RESIDUAL", "magnitude_mm3": metrics["residual_volume_mm3"],
                    "columns": metrics["residual_columns"],
                    "cause": "存在未切削到的材料",
                    "suggest": {"increase_stepover_coverage": True},
                    **_loc(metrics.get("residual_by_op", []))})
    if metrics["through_cut_volume_mm3"] > 1e-6:
        out.append({"code": "E_THROUGH_CUT", "magnitude_mm3": metrics["through_cut_volume_mm3"],
                    "cause": "刀路切穿零件底面"})
    return out
