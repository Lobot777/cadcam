"""B9：中文工单 → 契约调用 → 网关执行 → 体积 IoU。

两条路线吃**同一句话**（只给中文工单，不给结构化的特征表）：
  contract：模型输出 {"calls":[{tool,args}]}，由网关按契约校验后执行；
  script  ：模型写 FreeCAD Python 脚本，执行后导出 STEP。

判据是几何，不是代码：与标准答案实体的体积 IoU（交并体积比）。

    PYTHONPATH=src python scripts/m5_nl_eval.py --n 20
    PYTHONPATH=src python scripts/m5_nl_eval.py --route script --n 10
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
import time

from cadcam.adapters import freecad
from cadcam.agents.llm import LLMClient, LLMError
from cadcam.gateway.session import new_session
from cadcam.gateway.tools import get_spec

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "build", "nl_eval")
OUT = os.path.join(ROOT, "reports", "m5_nl_eval.json")
IOU_PY = os.path.join(WORK, "_iou.py")
SEED = 20261106

SYS = ("你是 CAD 编程助手。只能通过给定的工具接口建模，不允许输出 Python 脚本。"
       "只输出一个 JSON 对象。 /no_think")

TOOL_DOC = """可用工具（参数单位 mm，坐标原点在毛坯左下角，z 向上，顶面 z=height）：
1. cad.create_box(length, width, height)  —— 建毛坯 [0,L]×[0,W]×[0,H]，必须是第一个调用
2. cad.pocket(center_x, center_y, width, height, depth) —— 从顶面向下切一个矩形腔
3. cad.hole(x, y, diameter, depth=10, through=false) —— 钻孔，通孔设 through=true，盲孔给 depth"""

IOU_SRC = '''import os, json
import FreeCAD as App
import Part
a = Part.read(os.environ["A"])
b = Part.read(os.environ["B"])
inter = a.common(b).Volume
union = a.fuse(b).Volume
out = {"va": a.Volume, "vb": b.Volume,
       "iou": inter / union if union > 1e-9 else 0.0}
try:
    L, W, H = [float(v) for v in os.environ["STOCK"].split(",")]
    stock = Part.makeBox(L, W, H)
    ra, rb = stock.cut(a), stock.cut(b)
    ri, ru = ra.common(rb).Volume, ra.fuse(rb).Volume
    out["removed_iou"] = ri / ru if ru > 1e-9 else 0.0
except Exception as exc:
    out["removed_iou"] = None
    out["removed_err"] = str(exc)[:80]
print("IOUJSON" + json.dumps(out))
'''


# ---------------------------------------------------------------- 任务生成
def make_tasks(n=20, seed=SEED):
    """20 条中文工单 + 对应的标准调用序列（真值由同一序列执行得到）。"""
    rng = random.Random(seed)
    grids = [(20, 20), (40, 20), (60, 20), (20, 40), (40, 40),
             (60, 40), (20, 60), (40, 60), (60, 60)]
    tasks = []
    for i in range(n):
        L = rng.choice((60, 80, 100))
        W = rng.choice((60, 80, 100))
        H = rng.choice((40, 50, 60))
        cells = rng.sample(grids, k=rng.randint(1, 3))
        pockets = []
        for cx, cy in cells:
            w = rng.choice((20, 30, 40))
            h = rng.choice((20, 30, 40))
            d = rng.choice((5, 8, 10, 15))
            # 保证型腔落在毛坯内
            cx = min(max(cx, w / 2 + 2), L - w / 2 - 2)
            cy = min(max(cy, h / 2 + 2), W - h / 2 - 2)
            if cx <= w / 2 or cy <= h / 2:
                continue
            pockets.append({"cx": round(cx, 1), "cy": round(cy, 1),
                            "w": float(w), "h": float(h), "d": float(d)})
        if not pockets:                       # 保证至少有一个型腔，工单才说得通
            pockets = [{"cx": round(L / 2, 1), "cy": round(W / 2, 1),
                        "w": 30.0, "h": 30.0, "d": 8.0}]
        holes = []
        for _ in range(rng.randint(0, 2)):
            hx = round(rng.uniform(5, L - 5), 1)
            hy = round(rng.uniform(5, W - 5), 1)
            holes.append({"x": hx, "y": hy, "dia": float(rng.choice((6, 8, 10))),
                          "through": rng.random() < 0.5,
                          "depth": float(rng.choice((10, 15)))})

        calls = [{"tool": "cad.create_box",
                  "args": {"length": float(L), "width": float(W), "height": float(H)}}]
        for p in pockets:
            calls.append({"tool": "cad.pocket",
                          "args": {"center_x": p["cx"], "center_y": p["cy"],
                                   "width": p["w"], "height": p["h"], "depth": p["d"]}})
        for h in holes:
            args = {"x": h["x"], "y": h["y"], "diameter": h["dia"]}
            if h["through"]:
                args["through"] = True
            else:
                args["depth"] = h["depth"]
            calls.append({"tool": "cad.hole", "args": args})

        tasks.append({"id": "NL%02d" % (i + 1), "size": [L, W, H],
                      "pockets": pockets, "holes": holes,
                      "calls": calls, "text": _describe(L, W, H, pockets, holes, rng)})
    return tasks


# 30 条难工单：(id, 毛坯, 工单, 期望调用, 类型标签)
# 所有型腔/孔都留 ≥1mm 边距——工具契约要求"在毛坯内且不切穿"，贴着边是病态题。
HARD = [
    ("H01", [80, 80, 50], "80×80×50 的料，在左上角区域铣一个 30×30、深 12 的腔，"
                          "腔的左边缘距毛坯左边 5、上边缘距顶边 5。",
     [("box", 80, 80, 50), ("pocket", 20, 60, 30, 30, 12)], "方位"),
    ("H02", [80, 80, 50], "80×80×50，在右下角铣 20×20、深 8 的腔，右边缘和下边缘各留 5。",
     [("box", 80, 80, 50), ("pocket", 65, 15, 20, 20, 8)], "方位"),
    ("H03", [80, 100, 50], "80×100×50，距左边缘 20、距下边缘 20 的地方开 20×20 深 8 的腔。",
     [("box", 80, 100, 50), ("pocket", 30, 30, 20, 20, 8)], "方位"),
    ("H04", [100, 80, 50], "毛坯 100×80×50。在顶面正中央铣一个 40×30 的腔，深 10。",
     [("box", 100, 80, 50), ("pocket", 50, 40, 40, 30, 10)], "居中"),
    ("H05", [100, 80, 50], "100×80×50：两个 30×20 深 8 的腔，关于 x=50 对称，y 都在 40，"
                           "中心分别距中线 25。",
     [("box", 100, 80, 50), ("pocket", 25, 40, 30, 20, 8), ("pocket", 75, 40, 30, 20, 8)], "对称"),
    # 原表述只写"关于 y=50 上下对称"，没给距中线的距离 —— 8/9 个云端模型读成别的
    # 合理布局（见 docs/M5-CLOUD.md §4.2）。改成给定边距 + 空隙，答案唯一。
    ("H06", [80, 100, 50], "80×100×50：两个 40×20 深 6 的腔上下对称排列，x 都居中，"
                           "下腔下边缘距 y=0 留 10，两腔中间留 40 的空隙。",
     [("box", 80, 100, 50), ("pocket", 40, 20, 40, 20, 6), ("pocket", 40, 80, 40, 20, 6)], "对称"),
    ("H07", [60, 60, 40], "60×60×40，中间挖 30×30，深 1.5 cm。",
     [("box", 60, 60, 40), ("pocket", 30, 30, 30, 30, 15)], "单位"),
    ("H08", [90, 90, 50], "90×90×50，中央 40×40 深 1 cm 的腔；左下角钻 Ø10 通孔，孔心距两边各 15。",
     [("box", 90, 90, 50), ("pocket", 45, 45, 40, 40, 10), ("hole", 15, 15, 10)], "单位"),
    ("H09", [80, 60, 40], "80×60×40，四边各留 10 的壁厚，中间全部铣掉深 15。",
     [("box", 80, 60, 40), ("pocket", 40, 30, 60, 40, 15)], "壁厚"),
    ("H10", [100, 100, 50], "100×100×50，四边留 15 的壁，中间挖深 20。",
     [("box", 100, 100, 50), ("pocket", 50, 50, 70, 70, 20)], "壁厚"),
    ("H11", [100, 100, 60], "100×100×60，中央 50×50 深 20 的腔；四角各 Ø8 通孔，孔心距两边都 10。",
     [("box", 100, 100, 60), ("pocket", 50, 50, 50, 50, 20),
      ("hole", 10, 10, 8), ("hole", 90, 10, 8), ("hole", 10, 90, 8), ("hole", 90, 90, 8)], "阵列"),
    ("H12", [90, 90, 50], "90×90×50，中央 40×40 深 10 的腔；四角各钻 Ø6 深 15 的孔，孔心距两边 12。",
     [("box", 90, 90, 50), ("pocket", 45, 45, 40, 40, 10),
      ("hole", 12, 12, 6, 15), ("hole", 78, 12, 6, 15),
      ("hole", 12, 78, 6, 15), ("hole", 78, 78, 6, 15)], "阵列"),
    ("H13", [80, 80, 50], "80×80×50，两个 30×20 深 10 的腔，中心分别在 (25,40) 和 (55,40)；"
                           "中间钻 Ø10 通孔 (40,40)。",
     [("box", 80, 80, 50), ("pocket", 25, 40, 30, 20, 10), ("pocket", 55, 40, 30, 20, 10),
      ("hole", 40, 40, 10)], "多特征"),
    ("H14", [120, 80, 50], "120×80×50：三个 20×20 深 8 的腔，中心在 x=25、60、95，y 都居中。",
     [("box", 120, 80, 50), ("pocket", 25, 40, 20, 20, 8), ("pocket", 60, 40, 20, 20, 8),
      ("pocket", 95, 40, 20, 20, 8)], "多特征"),
    ("H15", [120, 80, 50], "120×80×50，沿 x 方向开两道 20×60 深 6 的槽，中心分别在 x=35 和 x=85。",
     [("box", 120, 80, 50), ("pocket", 35, 40, 20, 60, 6), ("pocket", 85, 40, 20, 60, 6)], "多特征"),
    ("H16", [80, 80, 50], "80×80×50，居中铣 40×40 的腔，腔底到 z=40。",
     [("box", 80, 80, 50), ("pocket", 40, 40, 40, 40, 10)], "深度"),
    ("H17", [100, 100, 60], "100×100×60，中央 50×50 的腔，底面 z=45。",
     [("box", 100, 100, 60), ("pocket", 50, 50, 50, 50, 15)], "深度"),
    ("H18", [120, 80, 50], "120×80×50，打 6 个 Ø8 通孔，两排三列：x=20/60/100，y=20/60。",
     [("box", 120, 80, 50), ("hole", 20, 20, 8), ("hole", 60, 20, 8), ("hole", 100, 20, 8),
      ("hole", 20, 60, 8), ("hole", 60, 60, 8), ("hole", 100, 60, 8)], "阵列"),
    ("H19", [100, 80, 50], "100×80×50，中央 40×30 深 12 的腔；左右各钻 Ø8 深 15 的孔，"
                           "孔心在 (15,40) 和 (85,40)。",
     [("box", 100, 80, 50), ("pocket", 50, 40, 40, 30, 12),
      ("hole", 15, 40, 8, 15), ("hole", 85, 40, 8, 15)], "多特征"),
    ("H20", [100, 100, 50], "100×100×50，贴着上边开一道 60×20 深 10 的槽，槽的上边缘距顶边 2，"
                            "左边缘距左边 20。",
     [("box", 100, 100, 50), ("pocket", 50, 88, 60, 20, 10)], "方位"),
    ("H21", [80, 80, 50], "80×80×50，两级台阶：先 60×60 深 10 居中，再在其中央 30×30 深到 20。",
     [("box", 80, 80, 50), ("pocket", 40, 40, 60, 60, 10), ("pocket", 40, 40, 30, 30, 20)], "多特征"),
    ("H22", [120, 120, 60], "120×120×60，100×100 深 25 的腔居中。",
     [("box", 120, 120, 60), ("pocket", 60, 60, 100, 100, 25)], "居中"),
    ("H23", [80, 80, 50], "80×80×50，40×40 深 10 的腔在左下，中心 (30,30)；右上角钻 Ø12 通孔 (70,70)。",
     [("box", 80, 80, 50), ("pocket", 30, 30, 40, 40, 10), ("hole", 70, 70, 12)], "多特征"),
    ("H24", [90, 70, 55], "90×70×55，60×40 深 12 的腔居中；四角 Ø8 通孔，距两边各 8。",
     [("box", 90, 70, 55), ("pocket", 45, 35, 60, 40, 12),
      ("hole", 8, 8, 8), ("hole", 82, 8, 8), ("hole", 8, 62, 8), ("hole", 82, 62, 8)], "阵列"),
    ("H25", [100, 80, 50], "100×80×50，铣型腔 40×40，深度 8mm，中心坐标 x=25, y=25。",
     [("box", 100, 80, 50), ("pocket", 25, 25, 40, 40, 8)], "单位"),
    ("H26", [100, 80, 50], "100×80×50，只铣一个 60×40 深 5 的腔，位置居中，不要打孔，其余面保持毛坯。",
     [("box", 100, 80, 50), ("pocket", 50, 40, 60, 40, 5)], "居中"),
    ("H27", [100, 100, 50], "100×100×50，四个 20×20 深 10 的腔，中心分别在 (25,25)、(75,25)、"
                            "(25,75)、(75,75)。",
     [("box", 100, 100, 50), ("pocket", 25, 25, 20, 20, 10), ("pocket", 75, 25, 20, 20, 10),
      ("pocket", 25, 75, 20, 20, 10), ("pocket", 75, 75, 20, 20, 10)], "阵列"),
    ("H28", [100, 60, 40], "100×60×40，左侧开一个 20×40 深 20 的深腔，中心 (20,30)。",
     [("box", 100, 60, 40), ("pocket", 20, 30, 20, 40, 20)], "方位"),
    ("H29", [100, 100, 50], "100×100×50，中央 60×60 深 15 的腔，腔底中心再钻 Ø16 通孔。",
     [("box", 100, 100, 50), ("pocket", 50, 50, 60, 60, 15), ("hole", 50, 50, 16)], "多特征"),
    ("H30", [120, 80, 50], "120×80×50：40×30 深 10 的腔在 (30,40)；20×20 深 5 的腔在 (85,40)；"
                           "Ø8 通孔 (105,65)；Ø8 深 12 的孔在 (15,15)。",
     [("box", 120, 80, 50), ("pocket", 30, 40, 40, 30, 10), ("pocket", 85, 40, 20, 20, 5),
      ("hole", 105, 65, 8), ("hole", 15, 15, 8, 12)], "多特征"),
]


def to_calls(raw):
    """把 ("pocket", cx, cy, w, h, d) 这种紧凑写法转成契约调用序列。

    两种任务集（HARD / EXTRA）共用同一套转换，避免判据不一致。
    """
    seq = []
    for c in raw:
        if c[0] == "box":
            seq.append({"tool": "cad.create_box",
                        "args": {"length": float(c[1]), "width": float(c[2]),
                                 "height": float(c[3])}})
        elif c[0] == "pocket":
            seq.append({"tool": "cad.pocket",
                        "args": {"center_x": float(c[1]), "center_y": float(c[2]),
                                 "width": float(c[3]), "height": float(c[4]),
                                 "depth": float(c[5])}})
        else:
            args = {"x": float(c[1]), "y": float(c[2]), "diameter": float(c[3])}
            if len(c) > 4:
                args["depth"] = float(c[4])
            else:
                args["through"] = True
            seq.append({"tool": "cad.hole", "args": args})
    return seq


def tasks_from(spec):
    return [{"id": tid, "size": size, "text": text, "calls": to_calls(calls),
             "tag": tag, "pockets": [], "holes": []}
            for tid, size, text, calls, tag in spec]


def make_hard_tasks():
    """30 条难工单：相对方位、cm 单位、角部、四角对称、否定约束。"""
    return tasks_from(HARD)


def _describe(L, W, H, pockets, holes, rng):
    style = rng.randint(0, 2)
    ps = []
    for p in pockets:
        ps.append("%.0f×%.0f、深 %.0f，中心 (%.0f, %.0f)"
                  % (p["w"], p["h"], p["d"], p["cx"], p["cy"]))
    hs = []
    for h in holes:
        kind = "通孔" if h["through"] else "深 %.0f 的盲孔" % h["depth"]
        hs.append("Ø%.0f %s，孔心 (%.0f, %.0f)" % (h["dia"], kind, h["x"], h["y"]))

    if style == 0:
        text = "毛坯 %.0f×%.0f×%.0f。在顶面铣 %d 个型腔：%s。" % (L, W, H, len(ps), "；".join(ps))
        text += ("再钻 %d 个孔：%s。" % (len(hs), "；".join(hs))) if hs else "不需要打孔。"
    elif style == 1:
        text = ("来料 %.0f×%.0f×%.0f 的铝块，先把顶面找平，然后" % (L, W, H))
        text += "切 %d 个方腔（%s）。" % (len(ps), "；".join(ps))
        text += ("另外打 %d 个孔（%s）。" % (len(hs), "；".join(hs))) if hs else "这批不打孔。"
    else:
        text = ("零件外形 %.0f×%.0f×%.0f，工序要求：" % (L, W, H))
        text += "；".join("铣型腔 %s" % p for p in ps) + "。"
        text += ("；".join("钻 %s" % h for h in hs) + "。") if hs else "无孔。"
    return text


# ---------------------------------------------------------------- 执行
def _iou_paths(a, b, stock=None):
    if not os.path.exists(IOU_PY):
        with open(IOU_PY, "w", encoding="utf-8") as fh:
            fh.write(IOU_SRC)
    env = dict(os.environ, A=a, B=b,
               STOCK=",".join("%.1f" % v for v in (stock or [0, 0, 0])))
    p = subprocess.run([freecad.FREECAD_CMD, IOU_PY], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180, env=env)
    for line in (p.stdout or "").splitlines():
        if line.startswith("IOUJSON"):
            return json.loads(line[len("IOUJSON"):])
    return {"iou": None, "error": (p.stderr or p.stdout or "")[-200:]}


def execute(calls, root):
    """按顺序执行调用；单条失败记下来继续（网关自带回滚）。"""
    sess = new_session(root)
    legal, errors = 0, []
    for i, c in enumerate(calls):
        name = c.get("tool")
        args = c.get("args") or {}
        try:
            spec = get_spec(name)
        except Exception as exc:                     # noqa: BLE001
            errors.append({"i": i, "tool": name, "code": "UNKNOWN_TOOL", "msg": str(exc)[:80]})
            continue
        missing = [k for k in spec.required if k not in args]
        if missing:
            errors.append({"i": i, "tool": name, "code": "MISSING", "msg": ",".join(missing)})
            continue
        legal += 1
        try:
            sess.commit(spec, args)
        except Exception as exc:                     # noqa: BLE001
            errors.append({"i": i, "tool": name,
                           "code": type(exc).__name__, "msg": str(exc)[:100]})
    step, ok = None, False
    if sess.geometry is not None:
        try:
            res = sess.commit(get_spec("cad.export_step"), {"name": "out"})
            step = res["path"]
            ok = os.path.exists(step)
        except Exception as exc:                     # noqa: BLE001
            errors.append({"i": -1, "tool": "cad.export_step", "msg": str(exc)[:100]})
    return {"legal": legal, "n": len(calls), "errors": errors,
            "exec_ok": ok, "step": step if ok else None,
            "session": sess}


def contract_messages(text):
    body = (TOOL_DOC + "\n\n工单：" + text + "\n\n"
            "输出 JSON：{\"calls\": [{\"tool\": \"cad.pocket\", \"args\": {...}}, ...]}。\n"
            "要求：第一个调用必须是 cad.create_box；坐标用绝对 mm；不要输出解释。")
    return [{"role": "system", "content": SYS}, {"role": "user", "content": body}]


def script_messages(text):
    body = ("工单：" + text + "\n\n"
            "请写一个 FreeCAD 1.1 的 Python 脚本，用 Part 模块布尔运算构建该零件，"
            "并导出 STEP 到变量 OUT_STEP（由外部提供，不要自己定义）。\n"
            "要求：只用 `import FreeCAD as App` 与 `import Part`，不读写其他文件；"
            "最后调用 `solid.exportStep(OUT_STEP)`（solid 必须是单个实体，"
            "不要用 Part.export）；只输出代码。")
    return [{"role": "system", "content": "你是 FreeCAD 脚本工程师。只输出 Python 代码。 /no_think"},
            {"role": "user", "content": body}]


def run_contract(client, tasks, max_tokens):
    rows = []
    for t in tasks:
        root = os.path.join(WORK, "contract", t["id"])
        os.makedirs(root, exist_ok=True)
        gold = os.path.join(root, "gold.step")
        if not os.path.exists(gold):
            g = execute(t["calls"], os.path.join(root, "gold"))
            os.replace(g["step"], gold) if g["step"] else None
        t0 = time.time()
        try:
            data, resp = client.complete_json(contract_messages(t["text"]),
                                              {"required": ["calls"],
                                               "properties": {"calls": {}}},
                                              max_tokens=max_tokens)
            calls = data.get("calls") or []
            err = None
        except LLMError as exc:
            calls, resp, err = [], None, str(exc)[:160]
        res = execute(calls, os.path.join(root, "run"))
        iou = (_iou_paths(gold, res["step"], t.get("size"))
               if res["step"] else {"iou": None})
        rows.append({"id": t["id"], "tag": t.get("tag"),
                     "calls": calls, "n_calls": len(calls),
                     "legal": res["legal"],
                     "exec_ok": res["exec_ok"], "iou": iou.get("iou"),
                     "removed_iou": iou.get("removed_iou"),
                     "errors": res["errors"][:3], "error": err,
                     "prompt_tokens": (resp.prompt_tokens if resp else 0),
                     "completion_tokens": (resp.completion_tokens if resp else 0),
                     "seconds": round(time.time() - t0, 1)})
        print("  %s 调用 %d（合法 %d）执行 %-5s IoU %-6s"
              % (t["id"], len(calls), res["legal"], res["exec_ok"],
                 None if rows[-1]["iou"] is None else round(rows[-1]["iou"], 3)))
    return rows


def run_script(client, tasks, max_tokens):
    import shutil
    rows = []
    for t in tasks:
        root = os.path.join(WORK, "script", t["id"])
        os.makedirs(root, exist_ok=True)
        try:
            resp = client.chat(script_messages(t["text"]), max_tokens=max_tokens)
            code = resp.text.strip()
            if code.startswith("```"):
                code = re.sub(r"^```[a-zA-Z]*\n?", "", code)
                code = re.sub(r"\n?```$", "", code.strip())
            tok = (resp.prompt_tokens, resp.completion_tokens)
        except LLMError as exc:
            rows.append({"id": t["id"], "exec_ok": False, "iou": None, "error": str(exc)[:160]})
            continue
        sp = os.path.join(root, "part.py")
        with open(sp, "w", encoding="utf-8") as fh:
            fh.write('import os as _os\nOUT_STEP = _os.environ["CADCAM_OUT"]\n' + code)
        out_path = os.path.join(root, "out.step")
        env = dict(os.environ, CADCAM_OUT=out_path)
        try:
            p = subprocess.run([freecad.FREECAD_CMD, sp], cwd=root, capture_output=True,
                               text=True, timeout=120, encoding="utf-8",
                               errors="replace", env=env)
            ok = p.returncode == 0 and os.path.exists(out_path)
            tail = (p.stderr or p.stdout or "")[-200:]
        except subprocess.TimeoutExpired:
            ok, tail = False, "TIMEOUT"
        # 文件存在 ≠ 实体有效：空 shape 也会被导出成几百字节的 STEP
        if ok:
            try:
                vol = float(freecad.geometry(out_path).get("volume_mm3") or 0.0)
                ok = vol > 1.0
                if not ok:
                    tail = "空实体（体积 %.1f mm³）" % vol
            except Exception as exc:                  # noqa: BLE001
                ok, tail = False, "无效实体：%s" % str(exc)[:100]
        gold = os.path.join(WORK, "contract", t["id"], "gold.step")
        iou = (_iou_paths(gold, os.path.join(root, "out.step"), t.get("size"))
               if ok else {"iou": None})
        rows.append({"id": t["id"], "tag": t.get("tag"),
                     "exec_ok": ok, "iou": iou.get("iou"),
                     "removed_iou": iou.get("removed_iou"),
                     "tail": tail, "prompt_tokens": tok[0], "completion_tokens": tok[1]})
        print("  %s 执行 %-5s IoU %s" % (t["id"], ok,
                                          None if rows[-1]["iou"] is None else round(rows[-1]["iou"], 3)))
    return rows


def summarize(rows, route):
    tags = {r["id"]: r.get("tag") for r in rows}
    ok = [r for r in rows if r.get("exec_ok")]
    ious = [r["iou"] for r in ok if r.get("iou") is not None]
    rems = [r["removed_iou"] for r in ok if r.get("removed_iou") is not None]
    return {"route": route, "n": len(rows),
            "mean_removed_iou": round(sum(rems) / len(rems), 3) if rems else None,
            "removed_iou_ge_0.9": sum(1 for v in rems if v >= 0.9),
            "exec_ok": len(ok),
            "mean_iou": round(sum(ious) / len(ious), 3) if ious else None,
            "iou_ge_0.9": sum(1 for v in ious if v >= 0.9),
            "iou_ge_0.75": sum(1 for v in ious if v >= 0.75),
            "legal_rate": (round(sum(r.get("legal", 0) for r in rows)
                                 / max(1, sum(r.get("n_calls", 0) for r in rows)), 3)
                           if route == "contract" else None),
            "prompt_tokens": sum(r.get("prompt_tokens") or 0 for r in rows),
            "completion_tokens": sum(r.get("completion_tokens") or 0 for r in rows),
            "by_tag": _by_tag(rows, tags)}


def _by_tag(rows, tags):
    out = {}
    for r in rows:
        tag = tags.get(r["id"]) or "未标注"
        b = out.setdefault(tag, {"n": 0, "ok": 0, "ious": []})
        b["n"] += 1
        if r.get("exec_ok"):
            b["ok"] += 1
        if r.get("removed_iou") is not None:
            b["ious"].append(r["removed_iou"])
    for b in out.values():
        v = b.pop("ious")
        b["mean_iou"] = round(sum(v) / len(v), 3) if v else None
    return out


def rescore():
    """不重跑模型，用已落盘的 STEP 重算两种 IoU（口径升级时用）。"""
    with open(OUT, encoding="utf-8") as fh:
        rep = json.load(fh)
    sizes = {t["id"]: t["size"] for t in make_tasks(20)}
    sizes.update({t["id"]: t["size"] for t in make_hard_tasks()})
    for key, run in rep.get("runs", {}).items():
        route = key.split("/")[-1]
        rows = run.get("rows", [])
        for r in rows:
            tid = r["id"]
            gold = os.path.join(WORK, "contract", tid, "gold.step")
            out = (os.path.join(WORK, "contract", tid, "run", "export_out.step")
                   if route == "contract" else os.path.join(WORK, "script", tid, "out.step"))
            if not (os.path.exists(gold) and os.path.exists(out)):
                continue
            m = _iou_paths(gold, out, sizes.get(tid))
            r["iou"] = m.get("iou")
            r["removed_iou"] = m.get("removed_iou")
        run["summary"] = summarize(rows, route)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, ensure_ascii=False, indent=1)
    for key, run in rep["runs"].items():
        s = run["summary"]
        print("%-16s IoU %-6s 去除IoU %-6s (≥0.9: %s/%s)"
              % (key, s.get("mean_iou"), s.get("mean_removed_iou"),
                 s.get("removed_iou_ge_0.9"), s["exec_ok"]))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--route", default="contract", choices=("contract", "script", "both"))
    ap.add_argument("--set", default="easy", choices=("easy", "hard"),
                    help="easy=模板生成的 20 条；hard=8 条含相对方位/单位换算的工单")
    ap.add_argument("--max-tokens", type=int, default=800)
    ap.add_argument("--model", default="qwen3:8b")
    ap.add_argument("--rescore", action="store_true", help="只重算既有 STEP 的指标")
    ap.add_argument("--rep", default="", help="重复批次标记（如 r2），不同批次各自成组")
    args = ap.parse_args()

    if args.rescore:
        return rescore()

    client = LLMClient(model=args.model)
    ok, models = client.available()
    print("模型 %s｜%s" % (args.model, "可用" if ok else "不可用：%s" % models[0]))
    if not ok:
        return 2
    os.makedirs(WORK, exist_ok=True)

    tasks = make_hard_tasks() if args.set == "hard" else make_tasks(args.n)
    print("%d 条工单，例：%s" % (len(tasks), tasks[0]["text"]))
    out = {"model": args.model, "seed": SEED,
           "rule": "两条路线都只拿到中文工单，不给结构化的特征表",
           "criterion": "执行成功 + 与标准实体的体积 IoU",
           "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "runs": {}}
    if os.path.exists(OUT):                     # 多次运行累积，别互相覆盖
        try:
            with open(OUT, encoding="utf-8") as fh:
                prev = json.load(fh)
            out["runs"] = prev.get("runs", {})
        except Exception:                       # noqa: BLE001
            pass

    if args.route in ("contract", "both"):
        print("\n→ 契约调用路线")
        rows = run_contract(client, tasks, args.max_tokens)
        key = "%s/contract%s" % (args.set, args.rep)
        out["runs"][key] = {"summary": summarize(rows, "contract"), "rows": rows}
        print(json.dumps(out["runs"][key]["summary"], ensure_ascii=False))

    if args.route in ("script", "both"):
        print("\n→ 脚本路线")
        rows = run_script(client, tasks, 1200)
        key = "%s/script%s" % (args.set, args.rep)
        out["runs"][key] = {"summary": summarize(rows, "script"), "rows": rows}
        print(json.dumps(out["runs"][key]["summary"], ensure_ascii=False))

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print("\n→ %s" % os.path.relpath(OUT, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
