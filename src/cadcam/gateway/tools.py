"""M1 的工具契约集合：建模、观测、快照、回滚。

契约的三层含义（对应论文 C1）：
1. 参数层：JSON Schema 约束，模型只能传合法参数，越界立刻被拒；
2. 前置层：几何是否允许（型腔超出毛坯、深度切穿工件）；
3. 后置层：**体积守恒**——切掉的体积必须约等于特征体积。
   第 3 条是关键：布尔运算可能"成功"但结果是错的（重叠特征、共面出错），
   只检查"没抛异常"抓不到。体积守恒把它变成一个可判定的物理约束。
"""

import math

from cadcam.adapters import model
from cadcam.gateway.contracts import ErrorCode, Param, ToolError, ToolSpec
from cadcam.modalities.brep import digest

EPS = 1e-6
SKIN_MM = 0.5           # 保底余量：不允许切穿到零壁厚
VOLUME_TOLERANCE = 0.02  # 体积守恒容差 2%


def _next_path(session, tag):
    return session.root / ("%s_%02d.step" % (tag, len(session.history) + 1))


def _bbox(geometry):
    bbox = geometry.get("bbox") or {}
    lo, hi = bbox.get("min"), bbox.get("max")
    if not lo or not hi:
        raise ToolError(ErrorCode.STATE, "当前几何缺少包围盒信息")
    return lo, hi


def _commit_shape(session, path, geometry):
    session.current = path
    session.geometry = geometry
    return {
        "state": session.state(),
        "state_hash": digest.state_hash(geometry),
        "volume_mm3": geometry.get("volume_mm3"),
        "faces": geometry.get("counts", {}).get("faces"),
    }


# ---------------------------------------------------------------- 前置条件

def _require_stock(session, params):
    if session.current is None or session.geometry is None:
        raise ToolError(ErrorCode.PRECONDITION, "当前没有毛坯",
                        hint="先调用 cad.create_box 建立毛坯")


def _pocket_inside(session, params):
    _require_stock(session, params)
    lo, hi = _bbox(session.geometry)
    left = params["center_x"] - params["width"] / 2.0
    right = params["center_x"] + params["width"] / 2.0
    front = params["center_y"] - params["height"] / 2.0
    back = params["center_y"] + params["height"] / 2.0
    if left < lo[0] - EPS or right > hi[0] + EPS or front < lo[1] - EPS or back > hi[1] + EPS:
        raise ToolError(
            ErrorCode.PRECONDITION,
            "型腔轮廓超出毛坯范围",
            hint="毛坯 X∈[%.2f, %.2f], Y∈[%.2f, %.2f]；本次申请 X∈[%.2f, %.2f], Y∈[%.2f, %.2f]"
                 % (lo[0], hi[0], lo[1], hi[1], left, right, front, back),
            details={"requested": {"left": left, "right": right, "front": front, "back": back}},
        )
    headroom = (hi[2] - lo[2]) - SKIN_MM
    if params["depth"] > headroom + EPS:
        raise ToolError(
            ErrorCode.PRECONDITION,
            "型腔深度 %.2f 超过可用切深 %.2f（保留 %.1fmm 底厚）" % (params["depth"], headroom, SKIN_MM),
            hint="depth 应 ≤ %.2f" % headroom,
            details={"depth": params["depth"], "max_depth": round(headroom, 3)},
        )


def _hole_inside(session, params):
    _require_stock(session, params)
    lo, hi = _bbox(session.geometry)
    radius = params["diameter"] / 2.0
    x, y = params["x"], params["y"]
    if (x - radius < lo[0] - EPS or x + radius > hi[0] + EPS
            or y - radius < lo[1] - EPS or y + radius > hi[1] + EPS):
        raise ToolError(
            ErrorCode.PRECONDITION,
            "孔壁超出毛坯边界",
            hint="毛坯 X∈[%.2f, %.2f], Y∈[%.2f, %.2f]；孔 R%.2f @ (%.2f, %.2f)"
                 % (lo[0], hi[0], lo[1], hi[1], radius, x, y),
        )
    if not params.get("through"):
        headroom = (hi[2] - lo[2]) - SKIN_MM
        if params["depth"] > headroom + EPS:
            raise ToolError(
                ErrorCode.PRECONDITION,
                "孔深 %.2f 超过可用切深 %.2f" % (params["depth"], headroom),
                hint="盲孔 depth ≤ %.2f；通孔请设置 through=true" % headroom,
            )


# ---------------------------------------------------------------- 后置条件

def _volume_conserved(expected_mm3, what):
    def check(session, params, before, result):
        if not before or before.get("empty"):
            return
        expected = float(expected_mm3)
        if expected <= EPS:
            return
        drop = float(before.get("volume_mm3") or 0.0) - float(result.get("volume_mm3") or 0.0)
        error = abs(drop - expected) / expected
        if error <= VOLUME_TOLERANCE:
            return
        overlap = max(0.0, 1.0 - drop / expected)
        raise ToolError(
            ErrorCode.POSTCONDITION,
            "%s 体积不守恒：预期切除 %.1f mm³，实际切除 %.1f mm³（偏差 %.1f%%）"
            % (what, expected, drop, error * 100),
            hint=("切除量偏小说明该特征与已有材料/特征重叠（重叠约 %.0f%%），"
                  "请调整位置或尺寸后重试" % (overlap * 100)) if drop < expected
                 else "切除量偏大说明布尔运算异常，请检查特征是否超出毛坯",
            details={"expected_mm3": round(expected, 2), "removed_mm3": round(drop, 2),
                     "error_ratio": round(error, 4)},
        )
    return check


def _box_volume_exact(session, params, before, result):
    expected = params["length"] * params["width"] * params["height"]
    actual = float(result.get("volume_mm3") or 0.0)
    if abs(actual - expected) / expected > 1e-3:
        raise ToolError(
            ErrorCode.POSTCONDITION,
            "毛坯体积不符：预期 %.1f mm³，实际 %.1f mm³" % (expected, actual),
            hint="内核可能未按参数建模，请检查单位是否为毫米",
        )


# ---------------------------------------------------------------- handlers

def _create_box(session, params):
    out = _next_path(session, "box")
    payload = model.apply("box", params, None, out, label="box")
    # 新毛坯意味着新零件：旧快照不再有意义
    session.snapshots = []
    return _commit_shape(session, out, payload["geometry"])


def _pocket_ramp_inside(session, params):
    """斜底槽：轮廓在毛坯内，且最大切深不超过可用切深。"""
    _require_stock(session, params)
    lo, hi = _bbox(session.geometry)
    left = params["center_x"] - params["width"] / 2.0
    right = params["center_x"] + params["width"] / 2.0
    front = params["center_y"] - params["height"] / 2.0
    back = params["center_y"] + params["height"] / 2.0
    if left < lo[0] - EPS or right > hi[0] + EPS or front < lo[1] - EPS or back > hi[1] + EPS:
        raise ToolError(
            ErrorCode.PRECONDITION,
            "斜底槽轮廓超出毛坯范围",
            hint="毛坯 X∈[%.2f, %.2f], Y∈[%.2f, %.2f]" % (lo[0], hi[0], lo[1], hi[1]),
        )
    headroom = (hi[2] - lo[2]) - SKIN_MM
    worst = max(params["depth_near"], params["depth_far"])
    if worst > headroom + EPS:
        raise ToolError(
            ErrorCode.PRECONDITION,
            "斜底槽最大深度 %.2f 超过可用切深 %.2f（保留 %.1fmm 底厚）"
            % (worst, headroom, SKIN_MM),
            hint="depth_near / depth_far 都应 ≤ %.2f" % headroom,
        )
    if str(params.get("axis", "x")).lower() not in ("x", "y"):
        raise ToolError(ErrorCode.PRECONDITION, "axis 只能是 x 或 y",
                        hint='axis="x" 表示沿 X 正方向变深')


def _pocket(session, params):
    out = _next_path(session, "pocket")
    payload = model.apply("pocket", params, session.current, out, label="pocket")
    return _commit_shape(session, out, payload["geometry"])


def _pocket_ramp(session, params):
    out = _next_path(session, "pocket_ramp")
    payload = model.apply("pocket_ramp", params, session.current, out, label="pocket_ramp")
    return _commit_shape(session, out, payload["geometry"])


def _hole(session, params):
    out = _next_path(session, "hole")
    payload = model.apply("hole", params, session.current, out, label="hole")
    return _commit_shape(session, out, payload["geometry"])


def _inspect(session, params):
    return {"state": session.state(), "summary": session.summary()}


def _face_page(session, params):
    if session.geometry is None:
        raise ToolError(ErrorCode.STATE, "当前没有几何", hint="先建毛坯")
    return digest.face_page(session.geometry, page=params.get("page", 1), size=params.get("size", 20))


def _snapshot(session, params):
    snap = session.snapshot(params.get("label", ""))
    return {"snapshot": snap.to_dict(), "state": session.state()}


def _rollback(session, params):
    return session.rollback(params.get("snapshot_id") or None)


def _list_snapshots(session, params):
    return {"snapshots": [snap.to_dict() for snap in session.snapshots]}


_RECOGNISER = None


def _recogniser():
    """延迟加载。torch 是可选依赖：只做建模的会话不该被它拖累。"""
    global _RECOGNISER
    if _RECOGNISER is None:
        from cadcam.modalities.mfr.predict import Recognizer

        _RECOGNISER = Recognizer()
    return _RECOGNISER


def _recognize(session, params):
    if session.geometry is None:
        raise ToolError(ErrorCode.STATE, "当前没有几何", hint="先建毛坯或导入 STEP")
    try:
        recogniser = _recogniser()
    except Exception as exc:  # noqa: BLE001
        raise ToolError(
            ErrorCode.GEOM_FAIL,
            "特征识别器不可用：%s" % exc,
            hint="需要 torch 与训练好的权重 data/mfcad/mfr.pt",
        ) from exc

    result = recogniser.predict(session.geometry, min_confidence=params.get("min_confidence", 0.0))
    limit = params.get("limit", 40)
    return {
        "state": session.state(),
        "text": result["summary"]["text"],
        "instance_count": result["summary"]["count"],
        "instances": result["instances"][:limit],
        "truncated": max(0, len(result["instances"]) - limit),
        "conflicts": result.get("conflicts", []),
        "mean_confidence": result.get("mean_confidence"),
        "recogniser": result.get("recogniser"),
    }


def _export(session, params):
    if session.current is None:
        raise ToolError(ErrorCode.STATE, "当前没有几何可导出")
    import shutil
    target = session.root / ("export_%s.step" % params.get("name", "part"))
    shutil.copyfile(session.current, target)
    return {"path": str(target), "state": session.state()}


# ---------------------------------------------------------------- 契约表

TOOLS = [
    ToolSpec(
        name="cad.create_box",
        title="建毛坯长方体",
        description="在原点建立 [0,L]×[0,W]×[0,H] 的长方体毛坯，会清空既有快照。所有切削动作从顶面 z=H 向下定义深度。",
        params={
            "length": Param("number", "X 方向长度", 1.0, 1000.0),
            "width": Param("number", "Y 方向宽度", 1.0, 1000.0),
            "height": Param("number", "Z 方向高度", 1.0, 500.0),
        },
        required=("length", "width", "height"),
        mutating=True,
        returns="state{state_hash, faces, volume_mm3, size_mm}",
        postconditions=(("毛坯体积等于 L×W×H", _box_volume_exact),),
        handler=_create_box,
    ),
    ToolSpec(
        name="cad.pocket",
        title="矩形型腔（向下切削）",
        description="从毛坯顶面沿 -Z 切出一个长方体空腔，位置用中心点 + 长宽表达。",
        params={
            "center_x": Param("number", "型腔中心 X", -1000.0, 1000.0),
            "center_y": Param("number", "型腔中心 Y", -1000.0, 1000.0),
            "width": Param("number", "型腔 X 向宽度", 0.1, 1000.0),
            "height": Param("number", "型腔 Y 向长度", 0.1, 1000.0),
            "depth": Param("number", "切削深度（从顶面向下）", 0.05, 490.0),
        },
        required=("center_x", "center_y", "width", "height", "depth"),
        mutating=True,
        returns="state{state_hash, volume_mm3, faces}",
        preconditions=(("型腔在毛坯内且不切穿", _pocket_inside),),
        postconditions=(("切除体积等于型腔体积", None),),  # 占位，运行时按参数生成
        handler=_pocket,
    ),
    ToolSpec(
        name="cad.pocket_ramp",
        title="斜底槽（槽底带坡度）",
        description="从毛坯顶面切一个矩形槽，槽底沿 axis 正方向由 depth_near 线性过渡到 "
                    "depth_far，形成斜坡底。深度都从顶面算。",
        params={
            "center_x": Param("number", "槽中心 X", -1000.0, 1000.0),
            "center_y": Param("number", "槽中心 Y", -1000.0, 1000.0),
            "width": Param("number", "槽 X 向宽度", 0.5, 1000.0),
            "height": Param("number", "槽 Y 向长度", 0.5, 1000.0),
            "depth_near": Param("number", "起点端深度（从顶面算）", 0.05, 490.0),
            "depth_far": Param("number", "终点端深度（从顶面算）", 0.05, 490.0),
            "axis": Param("string", "变深方向：x 或 y", None, None, default="x"),
        },
        required=("center_x", "center_y", "width", "height",
                  "depth_near", "depth_far"),
        mutating=True,
        returns="state{state_hash, volume_mm3, faces}",
        preconditions=(("斜底槽在毛坯内且不切穿", _pocket_ramp_inside),),
        postconditions=(("切除体积等于平均深度×底面积", None),),
        handler=_pocket_ramp,
    ),
    ToolSpec(
        name="cad.hole",
        title="圆柱孔（钻孔/铣孔）",
        description="沿 -Z 钻一个圆柱孔。盲孔给 depth，通孔设 through=true。",
        params={
            "x": Param("number", "孔心 X", -1000.0, 1000.0),
            "y": Param("number", "孔心 Y", -1000.0, 1000.0),
            "diameter": Param("number", "孔径", 0.1, 200.0),
            "depth": Param("number", "孔深（盲孔用）", 0.05, 490.0, default=1.0),
            "through": Param("boolean", "是否通孔", default=False),
        },
        required=("x", "y", "diameter"),
        mutating=True,
        returns="state{state_hash, volume_mm3, faces}",
        preconditions=(("孔在毛坯内且不误穿", _hole_inside),),
        postconditions=(("切除体积等于圆柱体积", None),),
        handler=_hole,
    ),
    ToolSpec(
        name="cad.inspect",
        title="查看当前零件 L0 摘要",
        description="返回尺寸、体积、面/边计数、曲面构成、孔与凸台半径、开敞度。智能体每步决策前应先看这个。",
        params={},
        mutating=False,
        returns="state + summary(L0)",
        handler=_inspect,
    ),
    ToolSpec(
        name="cad.faces",
        title="面表分页（L2）",
        description="按页查看面表，用于下钻到具体某个面。默认每页 20 面。",
        params={
            "page": Param("integer", "页码，从 1 开始", 1, 999, default=1),
            "size": Param("integer", "每页面数", 1, 60, default=20),
        },
        mutating=False,
        returns="rows[{id,i,type,area,normal,c,edges}]",
        handler=_face_page,
    ),
    ToolSpec(
        name="cad.snapshot",
        title="记录快照",
        description="把当前几何存为可回滚的快照点，后续动作失败时可回到这里。",
        params={"label": Param("string", "快照标签，便于回滚时引用", pattern=r"^[\w\u4e00-\u9fa5\- ]{0,40}$")},
        mutating=False,
        returns="snapshot{id,label,state_hash}",
        handler=_snapshot,
    ),
    ToolSpec(
        name="cad.rollback",
        title="回滚到快照",
        description="把零件恢复到指定快照（默认最近一个）。回滚后会用几何指纹校验形状是否真的一致。",
        params={"snapshot_id": Param("string", "快照 id 或标签；留空回到最近快照")},
        mutating=False,
        returns="rolled_back_to + state",
        handler=_rollback,
    ),
    ToolSpec(
        name="cad.snapshots",
        title="列出所有快照",
        description="查看当前会话的快照栈，用于决定回滚目标。",
        params={},
        mutating=False,
        returns="snapshots[]",
        handler=_list_snapshots,
    ),
    ToolSpec(
        name="cad.export_step",
        title="导出 STEP",
        description="把当前零件导出为 STEP 文件，供外部检查或下游 CAM 使用。",
        params={"name": Param("string", "文件名（不含扩展名）", pattern=r"^[\w\-]{1,32}$", default="part")},
        mutating=False,
        returns="path",
        handler=_export,
    ),
    ToolSpec(
        name="feature.recognize",
        title="识别加工特征",
        description=(
            "用图神经网络在当前零件的每个 B-Rep 面上识别加工特征，并聚成特征实例"
            "（型腔/槽/孔/台阶/圆角等），同时用几何判据交叉校验模型的判断。"
            "只读，不改变几何。"
        ),
        params={
            "min_confidence": Param("number", "低于此置信度的面会被标记", minimum=0.0, maximum=1.0, default=0.0),
            "limit": Param("integer", "返回的实例数上限", minimum=1, maximum=200, default=40),
        },
        mutating=False,
        returns="instances[]",
        handler=_recognize,
    ),
]


def _bind_volume_postconditions():
    """把"按参数算出的理论体积"注入后置条件。

    ToolSpec 在模块加载时构造，而体积依赖运行时参数，所以后置条件用
    工厂函数在调用点绑定 —— 保持契约声明式，同时能吃到实际参数。
    """
    for spec in TOOLS:
        if spec.name == "cad.pocket":
            def _pocket_check(session, params, before, result):
                expected = params["width"] * params["height"] * params["depth"]
                _volume_conserved(expected, "型腔")(session, params, before, result)
            spec.postconditions = (("切除体积等于型腔体积", _pocket_check),)
        elif spec.name == "cad.hole":
            def _hole_check(session, params, before, result):
                if params.get("through"):
                    size = (before or {}).get("size_mm") or []
                    use = size[2] if len(size) > 2 and size[2] else params["depth"]
                else:
                    use = params["depth"]
                radius = params["diameter"] / 2.0
                _volume_conserved(math.pi * radius * radius * use, "孔")(session, params, before, result)
            spec.postconditions = (("切除体积等于圆柱体积", _hole_check),)


_bind_volume_postconditions()

REGISTRY = {spec.name: spec for spec in TOOLS}


def get_spec(name: str) -> ToolSpec:
    spec = REGISTRY.get(name)
    if spec is None:
        raise ToolError(
            ErrorCode.UNKNOWN_TOOL,
            "不存在工具 %s" % name,
            hint="可用工具：%s" % ", ".join(sorted(REGISTRY)),
            details={"available": sorted(REGISTRY)},
        )
    return spec
