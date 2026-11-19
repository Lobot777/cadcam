"""FreeCAD 建模 worker（在 FreeCAD 进程内执行单个动作）。

一次调用只做一个动作，动作序列由网关编排。这样做的好处：
1. 每次调用都能在网关侧做前置/后置校验，而不是"跑完一大段脚本才知道错了"；
2. 崩溃隔离粒度 = 单个动作；
3. 与真实 MCP 工具的调用语义一致（一 tool call 一 action）。

坐标约定（贯穿全项目，不再有第二套）：
  毛坯第一角在原点，占据 [0,L]×[0,W]×[0,H]，顶面 z = H，
  所有切削动作从顶面向下定义深度。

参数与结果都走环境变量指向的 JSON 文件 —— FreeCADCmd 会劫持命令行参数。
"""

import json
import os
import sys
import traceback

import FreeCAD as App
import Part

_SRC = os.environ.get("CADCAM_SRC")
if _SRC and _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from cadcam.adapters.freecad_worker import extract_shape  # noqa: E402


def _fail(code, message, hint=""):
    return {"ok": False, "error": {"code": code, "message": message, "hint": hint}}


def _vec(values, digits=3):
    return [round(float(v), digits) for v in values]


def _load(path_in):
    if not path_in or not os.path.exists(path_in):
        return None, _fail("E_STATE", "缺少输入几何：%s" % path_in,
                           "请先调用 cad.create_box 建立毛坯")
    shape = Part.Shape()
    shape.read(path_in)
    if shape.isNull() or not shape.Solids:
        return None, _fail("E_STATE", "输入几何为空或不是实体：%s" % path_in)
    return shape, None


def _top_z(shape):
    return shape.BoundBox.ZMax


def _do_box(shape, params):
    length, width, height = params["length"], params["width"], params["height"]
    return Part.makeBox(length, width, height), None


def _do_pocket(shape, params):
    if shape is None:
        return None, _fail("E_STATE", "挖腔需要已有毛坯", "先调用 cad.create_box")
    cx, cy = params["center_x"], params["center_y"]
    width, height, depth = params["width"], params["height"], params["depth"]
    top = _top_z(shape)
    if depth >= top - 1e-6:
        return None, _fail("E_PRECONDITION",
                           "型腔深度 %.3f 不小于毛坯高度 %.3f，会切穿工件" % (depth, top),
                           "减小 depth，或用 cad.hole(through=true) 做通孔")
    cutter = Part.makeBox(width, height, depth, App.Vector(cx - width / 2.0, cy - height / 2.0, top - depth))
    try:
        result = shape.cut(cutter)
    except Exception as exc:  # noqa: BLE001
        return None, _fail("E_GEOM_FAIL", "布尔减失败：%s" % exc,
                           "检查型腔是否完全落在毛坯内，且未与已有特征重合")
    return result, None


def _do_hole(shape, params):
    if shape is None:
        return None, _fail("E_STATE", "钻孔需要已有毛坯", "先调用 cad.create_box")
    x, y, diameter = params["x"], params["y"], params["diameter"]
    top = _top_z(shape)
    depth = top if params.get("through") else params["depth"]
    if not params.get("through") and depth >= top - 1e-6:
        return None, _fail("E_PRECONDITION",
                           "孔深 %.3f 不小于毛坯高度 %.3f，会切穿工件" % (depth, top),
                           "通孔请设置 through=true")
    # 起刀面抬到顶面之上一点，避免共面布尔产生薄片残余
    cutter = Part.makeCylinder(diameter / 2.0, depth + 0.5, App.Vector(x, y, top - depth))
    try:
        result = shape.cut(cutter)
    except Exception as exc:  # noqa: BLE001
        return None, _fail("E_GEOM_FAIL", "布尔减失败：%s" % exc,
                           "检查孔位是否落在毛坯内")
    return result, None


def _do_pocket_ramp(shape, params):
    """斜底槽：槽底沿 axis 正方向从 depth_near 线性变到 depth_far。

    构造方式：在侧视平面上画一个梯形轮廓（斜边 = 槽底，上边 = 毛坯顶面），
    沿另一轴拉伸成切割体。这样切完留下的就是「斜底 + 平顶」的槽。
    """
    if shape is None:
        return None, _fail("E_STATE", "挖槽需要已有毛坯", "先调用 cad.create_box")
    cx, cy = params["center_x"], params["center_y"]
    width, height = params["width"], params["height"]
    d0, d1 = params["depth_near"], params["depth_far"]
    axis = str(params.get("axis", "x")).lower()
    if axis not in ("x", "y"):
        return None, _fail("E_PRECONDITION", "axis 只能是 x 或 y", "axis=\"x\" 表示沿 X 方向变深")
    top = _top_z(shape)
    if max(d0, d1) >= top - 1e-6:
        return None, _fail("E_PRECONDITION",
                           "槽深 %.3f 不小于毛坯高度 %.3f，会切穿工件" % (max(d0, d1), top),
                           "减小 depth_near / depth_far")
    x0, x1 = cx - width / 2.0, cx + width / 2.0
    y0, y1 = cy - height / 2.0, cy + height / 2.0
    if axis == "x":
        # 轮廓在 y=y0 的 XZ 平面上：斜底 -> 顶面 -> 回来
        pts = [App.Vector(x0, y0, top - d0), App.Vector(x1, y0, top - d1),
               App.Vector(x1, y0, top), App.Vector(x0, y0, top)]
        vec = App.Vector(0, height, 0)
    else:
        pts = [App.Vector(x0, y0, top - d0), App.Vector(x0, y1, top - d1),
               App.Vector(x0, y1, top), App.Vector(x0, y0, top)]
        vec = App.Vector(width, 0, 0)
    try:
        face = Part.Face(Part.makePolygon(pts + [pts[0]]))
        cutter = face.extrude(vec)
        result = shape.cut(cutter)
    except Exception as exc:  # noqa: BLE001
        return None, _fail("E_GEOM_FAIL", "布尔减失败：%s" % exc,
                           "检查槽是否完全落在毛坯内")
    return result, None


ACTIONS = {
    "box": _do_box,
    "pocket": _do_pocket,
    "pocket_ramp": _do_pocket_ramp,
    "hole": _do_hole,
}


def run(job):
    action = job.get("action")
    params = job.get("params") or {}
    handler = ACTIONS.get(action)
    if handler is None:
        return _fail("E_UNKNOWN_TOOL", "未知建模动作：%s" % action,
                     "可用动作：%s" % ", ".join(sorted(ACTIONS)))

    shape = None
    if action != "box":
        shape, error = _load(job.get("path_in"))
        if error:
            return error

    try:
        result, error = handler(shape, params)
    except KeyError as exc:
        return _fail("E_PARAM_INVALID", "缺少参数：%s" % exc)
    if error:
        return error
    if result is None or result.isNull() or not result.Solids:
        return _fail("E_GEOM_FAIL", "运算结果不是有效实体", "检查特征是否完全落在材料内")

    path_out = job["path_out"]
    os.makedirs(os.path.dirname(path_out) or ".", exist_ok=True)
    # Part.export 只吃文档对象，对裸 Shape 会写出没有几何的空壳 STEP
    result.exportStep(path_out)

    geometry = extract_shape(result, source=job.get("label") or action)
    return {"ok": True, "geometry": geometry, "path": path_out}


def main():
    job_path = os.environ.get("CADCAM_JOB")
    out_path = os.environ.get("CADCAM_OUT")
    if not job_path or not out_path:
        raise SystemExit("需要 CADCAM_JOB 与 CADCAM_OUT 环境变量")
    payload = None
    try:
        with open(job_path, "r", encoding="utf-8") as handle:
            job = json.load(handle)
        payload = run(job)
    except Exception:  # noqa: BLE001
        payload = _fail("E_GEOM_FAIL", "建模进程异常", traceback.format_exc()[-1500:])

    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)


# FreeCADCmd 执行脚本时 __name__ 是文件名词干且吞异常返回 0，故不用 __main__ 判定
if __name__ != "cadcam.adapters.freecad_model_worker":
    main()
