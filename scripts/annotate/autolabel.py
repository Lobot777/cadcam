"""真实零件面级标注：规则预标注 + 实例分组。

思路（也是论文里要写清楚的方法）：
  1. 面图 = 面为点、共享边为弧；凹边视为"特征边界"。
  2. 从落在包围盒六个平面上的面出发，沿**非凹边**洪水填充 -> 得到 stock 面集合。
  3. 剩下的面按同样规则连通分量 -> 每个分量就是一个候选特征实例。
     这正好补上 MFCAD++ 只有语义标签、没有实例标签的缺口。
  4. 每个分量按几何量判类（家族 + MFCAD 25 类中的具体类）。
     判不出来的**不硬猜**：写 null + 原因，交给人看。

为什么不全自动：这 10 件是真实零件，几何比 MFCAD++ 的合成件脏
（BSpline 过渡、非正交角、相交特征）。规则能覆盖大头，但"看着才知道"的
少数面必须人工确认 —— 所以输出里每条标注都带 evidence 和 source，
`source` 是 `rule` 还是 `human` 一目了然，论文里可以据此报告人工介入比例。
"""

import json
import math
import os
import sys
from collections import defaultdict, deque

TOL = 1e-3

# 家族名与网关动作族对齐（instances.FAMILY_TO_ACTION）
FAMILIES = ("stock", "hole", "pocket", "slot", "step", "fillet", "chamfer", "freeform")


# ---------------------------------------------------------------- 基础几何


def _sub(a, b):
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def _norm(v):
    return math.sqrt(sum(x * x for x in v))


def _unit(v):
    n = _norm(v)
    return [0.0, 0.0, 0.0] if n < 1e-12 else [x / n for x in v]


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _fmt(v):
    return [round(x, 2) for x in v]


def build_graph(geom):
    """返回 邻接表 与 边表（凹边单独标记）。"""
    adj = defaultdict(set)
    concave_edges = set()
    edges_of = defaultdict(list)
    for i, edge in enumerate(geom["edges"]):
        ends = edge.get("faces") or []
        if len(ends) != 2:
            continue
        a, b = ends
        adj[a].add(b)
        adj[b].add(a)
        edges_of[a].append(i)
        edges_of[b].append(i)
        if edge.get("convexity") == "concave":
            concave_edges.add(i)
    return adj, edges_of


def _on_bbox(geom, face):
    bb = geom["bbox"]
    x0, y0, z0 = bb["min"]
    x1, y1, z1 = bb["max"]
    f = face["bbox"]
    sides = []
    if abs(f[0] - x0) < 0.01:
        sides.append("-x")
    if abs(f[3] - x1) < 0.01:
        sides.append("+x")
    if abs(f[1] - y0) < 0.01:
        sides.append("-y")
    if abs(f[4] - y1) < 0.01:
        sides.append("+y")
    if abs(f[2] - z0) < 0.01:
        sides.append("-z")
    if abs(f[5] - z1) < 0.01:
        sides.append("+z")
    return sides


def _extent(face, axis):
    f = face["bbox"]
    return f[axis + 3] - f[axis]


def _is_planar_h(face):
    n = face["normal"]
    return face["surface"] == "Plane" and n is not None and abs(abs(n[2]) - 1.0) < 1e-3


def _is_planar_v(face):
    n = face["normal"]
    return face["surface"] == "Plane" and n is not None and abs(n[2]) < 1e-3


# ---------------------------------------------------------------- 结构分组


def split_stock(geom, adj=None):
    """stock 面 = 平面且其所在平面与包围盒某个面重合（法向与那一侧一致）。

    不用"沿凸边洪水填充"：凹边只在**型腔底-壁**处出现，槽口/腔口与顶面之间是凸边，
    洪泛会把槽壁一并吞进 stock（件 1 实测过，0 个特征）。
    也不用"任意落在包围盒上的面"：外轮廓圆角（圆柱面）的 bbox 同样会触到包围盒，
    那样圆角会被误判成 stock。要求"平面 + 轴向法向 + 面在包围盒平面上"三条同时成立。
    """
    bb = geom["bbox"]
    lo = bb["min"]
    hi = bb["max"]
    seeds = []
    for face in geom["faces"]:
        n = face["normal"]
        if face["surface"] != "Plane" or n is None:
            continue
        axis = [i for i in range(3) if abs(abs(n[i]) - 1.0) < 1e-6]
        if len(axis) != 1:
            continue
        i = axis[0]
        sign = 1 if n[i] > 0 else -1
        plane = hi[i] if sign > 0 else lo[i]
        if abs(face["bbox"][i] - plane) < 0.01 or abs(face["bbox"][i + 3] - plane) < 0.01:
            seeds.append(face["index"])
    return set(seeds)


def components(geom, adj, remaining):
    """stock 之外的面按共享边求连通分量 —— 每个分量是一个候选特征实例。"""
    out, seen = [], set()
    for start in sorted(remaining):
        if start in seen:
            continue
        comp, queue = [], deque([start])
        seen.add(start)
        while queue:
            cur = queue.popleft()
            comp.append(cur)
            for nxt in adj[cur]:
                if nxt in remaining and nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        out.append(sorted(comp))
    return out


# ---------------------------------------------------------------- 判类


def _cyl_facts(geom, face, edges_of):
    """圆柱面的判据：切向边数量、半径、轴向、是否通到包围盒底面。"""
    edges = geom["edges"]
    smooth = 0
    concave_neighbours = 0
    for i in edges_of[face["index"]]:
        if edges[i].get("convexity") == "smooth":
            smooth += 1
        elif edges[i].get("convexity") == "concave":
            concave_neighbours += 1
    p = face["params"]
    r = p.get("radius")
    h = float(face["bbox"][5]) - float(face["bbox"][2])
    axis = p.get("axis") or [0, 0, 1]
    axis_u = _unit(axis)
    radial = _sub(face["centroid"], p.get("location") or [0, 0, 0])
    radial = _sub(radial, [axis_u[i] * _dot(radial, axis_u) for i in range(3)])
    sign = None
    if face["normal"] is not None and _norm(radial) > 1e-6:
        sign = _dot(_unit(face["normal"]), _unit(radial))
    bb = geom["bbox"]
    reaches_bottom = abs(face["bbox"][2] - bb["min"][2]) < 0.05
    reaches_top = abs(face["bbox"][5] - bb["max"][2]) < 0.05
    return {
        "radius": p.get("radius"),
        "axis": _fmt(axis_u),
        "vertical": abs(abs(axis_u[2]) - 1.0) < 1e-3,
        "arc_fraction": (round(face["area_mm2"] / (2 * 3.141592653589793 * r * h), 3)
                         if (r and h > 1e-6) else None),
        "smooth_edges": smooth,
        "concave_edges": concave_neighbours,
        "outward": None if sign is None else round(sign, 3),
        "z_span": [face["bbox"][2], face["bbox"][5]],
        "reaches_top": reaches_top,
        "reaches_bottom": reaches_bottom,
    }


def _opposite(walls):
    """两壁是否互相平行（法向相反）—— 判槽的关键。"""
    if len(walls) != 2:
        return False
    a, b = walls[0]["normal"], walls[1]["normal"]
    return _dot(_unit(a), _unit(b)) < -0.99


def classify(geom, comp, edges_of, faces_by_index):
    """对一个候选特征分量判类，返回 (family, mfcad_class, params, evidence, confident)。"""
    faces = [faces_by_index[i] for i in comp]
    kinds = defaultdict(int)
    for f in faces:
        kinds[f["surface"]] += 1
    evidence = ["faces=%s" % comp, "kinds=%s" % dict(kinds)]

    cyls = [f for f in faces if f["surface"] in ("Cylinder", "Cone", "Toroid")]
    planes = [f for f in faces if f["surface"] == "Plane"]
    others = [f for f in faces if f["surface"] not in ("Plane", "Cylinder", "Cone", "Toroid")]
    bb = geom["bbox"]
    top_z = bb["max"][2]

    floors = [f for f in planes if _is_planar_h(f) and f["normal"][2] > 0]
    overhang = [f for f in planes if _is_planar_h(f) and f["normal"][2] < 0]
    walls = [f for f in planes if _is_planar_v(f)]
    slanted = [f for f in planes if not _is_planar_h(f) and not _is_planar_v(f)]
    breakout = sorted({s for f in faces for s in _on_bbox(geom, f)})
    depth = None
    if floors:
        depth = round(top_z - min(f["centroid"][2] for f in floors), 2)
    evidence.append("floor=%d wall=%d overhang=%d slanted=%d depth=%s breakout=%s"
                    % (len(floors), len(walls), len(overhang), len(slanted), depth, breakout or "none"))

    # ---------- 圆柱族
    if cyls:
        facts_all = [_cyl_facts(geom, f, edges_of) for f in cyls]
        facts_all.sort(key=lambda d: (d["smooth_edges"] == 0, d["radius"] or 0))
        facts = facts_all[0]
        evidence.append("cyl=%s" % facts)
        if all(f["smooth_edges"] >= 1 and (f["arc_fraction"] or 0) <= 0.55
               for f in facts_all):
            return ("fillet", "round", facts,
                    evidence + ["切向边 + 弧段占比<=0.55 -> 圆角/倒圆"], True)
        if len(cyls) >= 2 and len({round((f["radius"] or 0), 3) for f in facts_all}) >= 2:
            return ("hole", "blind_hole", {"radii": [f["radius"] for f in facts_all]},
                    evidence + ["同轴多半径 -> 台阶孔"], False)
        if floors and len(floors) == 1 and len(walls) == 0 and not facts["reaches_bottom"]:
            return ("hole", "blind_hole", facts, evidence + ["圆柱 + 底面无壁 -> 盲孔"], True)
        if (facts["outward"] or 0) < 0:
            through = facts["reaches_top"] and facts["reaches_bottom"]
            return ("hole", "through_hole" if through else "blind_hole", facts,
                    evidence + ["法向朝轴心 -> 孔"], True)
        if (facts["outward"] or 0) > 0 and facts["vertical"]:
            return ("boss", None, facts, evidence + ["法向背离轴 + 轴向 -> 凸台/销"], False)
        return ("unknown", None, facts, evidence + ["圆柱无法定向"], False)

    if not planes:
        return ("freeform", None, {}, evidence + ["非平面非圆柱（BSpline 过渡）"], False)

    # ---------- 平面族
    if slanted and not walls and not floors:
        return ("step", "slanted_through_step", {"slanted": len(slanted)},
                evidence + ["唯一面是斜面"], False)

    if floors:
        if len(walls) == 0:
            return ("pocket", None, {"depth": depth}, evidence + ["只有底面，无侧壁"], False)
        if len(walls) == 1:
            return ("step", "rectangular_blind_step", {"depth": depth},
                    evidence + ["单壁 + 底面 = 台阶"], True)
        if len(walls) == 2 and _opposite(walls):
            open_x = ("+x" in breakout and "-x" in breakout)
            open_y = ("+y" in breakout and "-y" in breakout)
            if open_x or open_y:
                return ("slot", "rectangular_through_slot",
                        {"depth": depth, "open": breakout},
                        evidence + ["两平行壁 + 两端贯通 = 通槽"], True)
            return ("slot", "rectangular_blind_slot", {"depth": depth},
                    evidence + ["两平行壁 + 两端封闭 = 盲槽"], True)
        if len(walls) >= 3:
            return ("pocket", "rectangular_pocket",
                    {"depth": depth, "walls": len(walls)}, evidence + ["多壁围合 + 底面 = 型腔"], True)
        return ("slot", "rectangular_blind_slot", {"depth": depth, "walls": len(walls)}, evidence, False)

    # 无底面
    if len(walls) >= 4:
        return ("pocket_through", "rectangular_passage", {"walls": len(walls)},
                evidence + ["四面无底 = 通腔"], True)
    if len(walls) == 2 and _opposite(walls):
        return ("slot", "rectangular_through_slot", {"walls": 2},
                evidence + ["两平行壁无底 = 通槽"], True)
    if len(walls) == 1:
        return ("step", "rectangular_through_step", {"walls": 1},
                evidence + ["单壁无底 = 通台阶"], False)
    return ("unknown", None, {}, evidence, False)


def annotate(geom):
    adj, edges_of = build_graph(geom)
    faces_by_index = {f["index"]: f for f in geom["faces"]}
    stock = split_stock(geom, adj)
    remaining = [f["index"] for f in geom["faces"] if f["index"] not in stock]

    labels = {}
    for index in sorted(stock):
        labels[index] = {"family": "stock", "mfcad_class": "stock", "feature": None,
                         "source": "rule", "evidence": ["包围盒可达"]}

    features = []
    for k, comp in enumerate(components(geom, adj, set(remaining)), start=1):
        family, cls, params, evidence, confident = classify(geom, comp, edges_of, faces_by_index)
        fid = "F%d" % k
        features.append({
            "id": fid,
            "family": family,
            "mfcad_class": cls,
            "faces": comp,
            "params": params,
            "confidence": "high" if confident else "low",
            "evidence": evidence,
            "reviewed": False,
        })
        for index in comp:
            labels[index] = {"family": family, "mfcad_class": cls, "feature": fid,
                             "source": "rule", "evidence": []}
    return labels, features


def main(argv):
    src = argv[0]
    dst = argv[1]
    with open(src, encoding="utf-8") as fh:
        geom = json.load(fh)
    labels, features = annotate(geom)
    payload = {
        "part": os.path.basename(src).split(".")[0],
        "source": geom.get("source"),
        "n_faces": len(geom["faces"]),
        "schema": "cadcam-annotation/1",
        "annotator": "rule-draft",
        "faces": {str(i): labels[i] for i in sorted(labels)},
        "features": features,
    }
    with open(dst, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    low = [f["id"] for f in features if f["confidence"] == "low"]
    print("%s: %d faces, %d features, %d low-confidence %s"
          % (payload["part"], payload["n_faces"], len(features), len(low), low))


if __name__ == "__main__":
    main(sys.argv[1:])
