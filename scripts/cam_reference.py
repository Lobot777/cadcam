"""M3.2 A 路：用 FreeCAD CAM（headless）生成 10 件锚点集的参考刀路。

FreeCAD 1.1 的 CAM 已更名，Job 与 Op 都是 `Path::FeaturePython` + Proxy：
    Path.Main.Job.Create(name, [models])
    Path.Op.MillFace.Create(name, parentJob=job)
不是 `Path::Job` 文档类型——第一版探针踩过这个坑。

关键做法：**每个朝上的水平面单独建一道 MillFace，FinalDepth = 该面自身 z**。
MillFace 按面的真实轮廓（含内环/孔）生成偏置刀路，等于自带「区域掩码」，
正好解决 M3.1 模板按矩形 bbox 走刀造成的结构性过切。

用法（FreeCAD 自带解释器）：
  CADCAM_DATA=data/zenodo_rptu CADCAM_OUTDIR=reports/m3_gcode_ref \
    CADCAM_META=reports/cam_reference_meta.json freecadcmd scripts/cam_reference.py

产物：每件的受限 G-code <n>.nc + 元数据 JSON（宿主侧再做 dexel 评估）。
"""

import json
import os
import traceback

import FreeCAD as App
import Part

DATA = os.environ.get("CADCAM_DATA", "data/zenodo_rptu")
OUTDIR = os.environ.get("CADCAM_OUTDIR", "reports/m3_gcode_ref")
META = os.environ.get("CADCAM_META", "reports/cam_reference_meta.json")
PARTS = [str(i) for i in range(1, 11)]
ALLOWANCE = 1.0          # 毛坯顶面余量，与 M3.1 process.plan 一致
SAFE_ABOVE = 5.0
WORD_ORDER = ("X", "Y", "Z", "I", "J", "K", "R", "Q", "F", "S", "T")
MOTION = {"G0", "G1", "G2", "G3", "G81", "G82", "G83", "G80"}


def command_to_line(cmd):
    name = str(cmd.Name)
    if name.startswith("("):
        return "(%s)" % name.strip("()")
    if name not in MOTION:
        return None
    words = [name]
    params = cmd.Parameters
    for key in WORD_ORDER:
        if key in params:
            try:
                words.append("%s%.3f" % (key, float(params[key])))
            except (TypeError, ValueError):
                continue
    return " ".join(words)


def up_faces(shape):
    """朝上的水平平面（法向 +Z），按 z 从高到低返回 (面号, z, 面积)。"""
    out = []
    for i, face in enumerate(shape.Faces, start=1):
        try:
            if not isinstance(face.Surface, Part.Plane):
                continue
            n = face.normalAt(0, 0)
            if n.z < 0.9:
                continue
            z = float(face.BoundBox.ZMin)
            if face.Area < 1.0:
                continue
            out.append((i, z, round(float(face.Area), 1)))
        except Exception:
            continue
    out.sort(key=lambda x: (-x[1], -x[2]))
    return out


def build_part(part):
    from Path.Main import Job as PathJob
    from Path.Op import MillFace, Drilling

    step_path = os.path.join(DATA, "%s.step" % part)
    shape = Part.Shape()
    shape.read(step_path)
    doc = App.newDocument("cam_ref_%s" % part)
    model = doc.addObject("Part::Feature", "Model")
    model.Shape = shape
    doc.recompute()
    job = PathJob.Create("Job", [model])

    bb = shape.BoundBox
    stock_top = float(bb.ZMax) + ALLOWANCE
    safe_z = stock_top + SAFE_ABOVE
    faces = up_faces(shape)

    # 每个面单独一道 MillFace：合并多个面时 FreeCAD 只取第一个面的范围，
    # 会退化成按包围盒走刀（实测件2 过切 13030mm³），必须拆开。
    all_lines = ["%", "(CAD2CAM_REF_%s)" % part, "G90 G21 G17 G94", "G54",
                 "T1 M6", "S18000 M3", "M8"]
    op_notes = []
    for seq, (idx, z, area) in enumerate(faces, start=1):
        label = "MILLFACE%d" % seq
        op = MillFace.Create("Face%d" % seq, parentJob=job)
        try:
            job.Operations.addObject(op)
        except Exception:
            pass
        op.Label = label
        op.Base = [(model, ["Face%d" % idx])]
        # 默认 Boundbox 会把小面扩成整个零件包围盒（件10 的 z=34 面走遍 75×75，
        # 过切 77552mm³）。Face Region 才按 Base 面的真实轮廓（含内环）走刀。
        op.BoundaryShape = "Face Region"
        op.StartDepth = stock_top
        op.FinalDepth = float(z)
        op.StepDown = 2.0
        op.ClearanceHeight = safe_z
        op.SafeHeight = safe_z - 2.0
        doc.recompute()
        cmds = list(op.Path.Commands) if op.Path else []
        all_lines.append("(%s)" % label)
        lines = [command_to_line(c) for c in cmds]
        all_lines += [x for x in lines if x]
        try:
            td = op.OpToolDiameter
            tool_d = float(td.Value) if hasattr(td, "Value") else float(td)
        except Exception:
            tool_d = None
        op_notes.append({"label": label, "z": z, "faces": [idx],
                         "commands": len(cmds), "gcode_lines": sum(1 for x in lines if x),
                         "tool_d_mm": tool_d})

    # 钻孔：必须先挂到 Job 再 recompute，Proxy 才会从 model 自动找可钻圆柱面
    # （Create 内部那次 findAllHoles 此时还拿不到 job，会返回空 Base）。
    # 不覆盖 FinalDepth，让 CAM 按孔深自动算；否则会一刀钻穿到零件底。
    drill = Drilling.Create("Drill", parentJob=job)
    job.Operations.addObject(drill)
    doc.recompute()
    drill.Label = "DRILLALL"
    try:
        drill.Proxy.findAllHoles(drill)
    except Exception:
        pass
    doc.recompute()
    dpath = drill.Path
    dcmds = list(dpath.Commands) if dpath else []
    dlines = [command_to_line(c) for c in dcmds]
    all_lines.append("(%s)" % "DRILLALL")
    all_lines += [x for x in dlines if x]
    try:
        dtool = drill.ToolController.Tool.Diameter
        dtool_d = float(dtool.Value) if hasattr(dtool, "Value") else float(dtool)
    except Exception:
        dtool_d = None
    op_notes.append({"label": "DRILLALL", "z": None,
                     "faces": [str(x) for x in (drill.Base or [])][:8],
                     "commands": len(dcmds),
                     "gcode_lines": sum(1 for x in dlines if x),
                     "tool_d_mm": dtool_d})
    all_lines += ["M9", "M5", "M30", "%"]
    text = "\n".join(all_lines) + "\n"

    os.makedirs(OUTDIR, exist_ok=True)
    with open(os.path.join(OUTDIR, "%s.nc" % part), "w", encoding="utf-8") as fh:
        fh.write(text)
    App.closeDocument(doc.Name)
    return {"part": part, "up_faces": len(faces), "ops": len(op_notes),
            "ops_detail": op_notes, "gcode_lines": len(all_lines),
            "bbox": {"min": [bb.XMin, bb.YMin, bb.ZMin],
                     "max": [bb.XMax, bb.YMax, bb.ZMax]},
            "stock_top": stock_top, "safe_z": safe_z}


def main():
    rows = []
    for part in PARTS:
        try:
            row = build_part(part)
            row["ok"] = True
            print("件 %-3s 水平面 %2d → 工序 %2d | G-code %4d 行"
                  % (part, row["up_faces"], row["ops"], row["gcode_lines"]))
        except Exception as exc:
            row = {"part": part, "ok": False,
                   "error": "%s: %s" % (type(exc).__name__, exc),
                   "trace": traceback.format_exc()[-800:]}
            print("件 %-3s FAIL %s" % (part, row["error"]))
        rows.append(row)
    os.makedirs(os.path.dirname(META) or ".", exist_ok=True)
    with open(META, "w", encoding="utf-8") as fh:
        json.dump({"freecad": App.Version(), "allowance_mm": ALLOWANCE,
                   "parts": rows}, fh, ensure_ascii=False, indent=1)
    print("wrote", META, "ok=%d/%d" % (sum(r.get("ok", False) for r in rows), len(rows)))


if __name__ in ("__main__", "cam_reference"):
    main()
