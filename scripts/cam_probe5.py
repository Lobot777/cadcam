"""M3.2 探针 5：Drilling 自动找孔 + ToolController 归属 + Profile/Pocket 几何选择。

只做能力验证，输出 reports/cam_probe5.json。
"""
import json
import os
import traceback

import FreeCAD as App
import Part

DATA = os.environ.get("CADCAM_DATA", "data/zenodo_rptu")
OUT = os.environ.get("CADCAM_OUT", "reports/cam_probe5.json")
PART = os.environ.get("CADCAM_PART", "2")
res = {"part": PART, "steps": {}}


def step(name, fn):
    try:
        v = fn()
        res["steps"][name] = {"ok": True, "value": str(v)[:500]}
        print("OK   %-26s %s" % (name, str(v)[:200]))
        return v
    except Exception as exc:
        res["steps"][name] = {"ok": False, "error": str(exc),
                              "trace": traceback.format_exc()[-600:]}
        print("FAIL %-26s %s" % (name, exc))
        return None


def main():
    from Path.Main import Job as PathJob
    from Path.Op import Drilling, Pocket, Profile

    shape = Part.Shape()
    shape.read(os.path.join(DATA, "%s.step" % PART))
    doc = App.newDocument("probe5")
    model = doc.addObject("Part::Feature", "Model")
    model.Shape = shape
    doc.recompute()
    job = PathJob.Create("Job", [model])
    doc.recompute()

    step("job_Group", lambda: [(o.Name, o.TypeId, o.Label) for o in job.Group])
    step("job_PropertiesList", lambda: [p for p in job.PropertiesList
                                        if "Tool" in p or "Model" in p or "Group" in p])

    # Drilling：正确的顺序是先挂到 Job，再让 Proxy 找孔
    drill = Drilling.Create("Drill", parentJob=job)
    job.Operations.addObject(drill)
    doc.recompute()
    step("drill_ToolController", lambda: str(drill.ToolController))
    step("drill_Base_initial", lambda: str(drill.Base))
    step("drill_findAllHoles", lambda: (drill.Proxy.findAllHoles(drill), str(drill.Base))[1])
    for prop, val in (("FinalDepth", float(shape.BoundBox.ZMin)),
                      ("ClearanceHeight", float(shape.BoundBox.ZMax) + 6.0),
                      ("SafeHeight", float(shape.BoundBox.ZMax) + 3.0)):
        if prop in drill.PropertiesList:
            try:
                setattr(drill, prop, val)
            except Exception as exc:
                print("   set %s: %s" % (prop, exc))
    doc.recompute()
    step("drill_commands", lambda: (
        len(drill.Path.Commands) if drill.Path else 0,
        [c.Name for c in drill.Path.Commands[:12]] if drill.Path else []))

    # Pocket / Profile：拿一个朝上水平面和一个竖直壁面试
    up_idx = side_idx = None
    for i, f in enumerate(shape.Faces, start=1):
        try:
            n = f.normalAt(0, 0)
            if isinstance(f.Surface, Part.Plane) and n.z > 0.9 and f.Area > 10 and up_idx is None:
                up_idx = i
            if isinstance(f.Surface, Part.Plane) and abs(n.z) < 0.1 and f.Area > 10 and side_idx is None:
                side_idx = i
        except Exception:
            pass
    res["up_idx"], res["side_idx"] = up_idx, side_idx
    print("up", up_idx, "side", side_idx)

    for name, mod, idx in (("Pocket", Pocket, up_idx), ("Profile", Profile, side_idx)):
        if idx is None:
            continue
        op = mod.Create(name, parentJob=job)
        job.Operations.addObject(op)
        op.Base = [(model, ["Face%d" % idx])]
        for prop, val in (("FinalDepth", float(shape.BoundBox.ZMin) + 2.0),
                          ("StartDepth", float(shape.BoundBox.ZMax) + 1.0),
                          ("ClearanceHeight", float(shape.BoundBox.ZMax) + 6.0),
                          ("SafeHeight", float(shape.BoundBox.ZMax) + 3.0)):
            if prop in op.PropertiesList:
                try:
                    setattr(op, prop, val)
                except Exception:
                    pass
        step("path_" + name, lambda op=op: (
            doc.recompute(),
            len(op.Path.Commands) if op.Path else 0,
            [c.Name for c in op.Path.Commands[:10]] if op.Path else [])[1:])

    App.closeDocument(doc.Name)
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    print("wrote", OUT)


if __name__ in ("__main__", "cam_probe5"):
    main()
