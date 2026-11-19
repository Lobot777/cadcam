"""M3.2 探针：headless 下 Pocket / Drilling / Profile / ToolController 的可用属性与生成能力。

只做能力探测，不产出正式刀路。输出 reports/cam_probe4.json。
"""
import json
import os
import traceback

import FreeCAD as App
import Part

DATA = os.environ.get("CADCAM_DATA", "data/zenodo_rptu")
OUT = os.environ.get("CADCAM_OUT", "reports/cam_probe4.json")
PART = os.environ.get("CADCAM_PART", "2")

result = {"freecad": App.Version(), "part": PART, "probes": {}}


def step(name, fn):
    try:
        v = fn()
        result["probes"][name] = {"ok": True, "value": v}
        print("OK   %-24s %s" % (name, str(v)[:220]))
        return v
    except Exception as exc:
        result["probes"][name] = {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc),
                                  "trace": traceback.format_exc()[-700:]}
        print("FAIL %-24s %s" % (name, exc))
        return None


def main():
    from Path.Main import Job as PathJob
    from Path.Op import MillFace, Pocket, Drilling, Profile
    from Path.Tool import Controller as TC

    shape = Part.Shape()
    shape.read(os.path.join(DATA, "%s.step" % PART))
    doc = App.newDocument("probe4")
    model = doc.addObject("Part::Feature", "Model")
    model.Shape = shape
    doc.recompute()
    job = PathJob.Create("Job", [model])

    result["shape"] = {"faces": len(shape.Faces),
                       "zmin": shape.BoundBox.ZMin, "zmax": shape.BoundBox.ZMax}

    ops = {}
    for name, mod in (("MillFace", MillFace), ("Pocket", Pocket),
                      ("Drilling", Drilling), ("Profile", Profile)):
        obj = step("create_" + name,
                   lambda mod=mod, name=name: mod.Create(name, parentJob=job))
        if obj is not None:
            try:
                job.Operations.addObject(obj)
            except Exception as exc:
                print("   (addObject %s: %s)" % (name, exc))
            ops[name] = obj
            step("props_" + name, lambda obj=obj: sorted(obj.PropertiesList))
            for prop in ("Base", "FinalDepth", "StartDepth", "StepDown", "StepOver",
                         "ClearanceHeight", "SafeHeight", "OpToolDiameter", "ToolController",
                         "PeckDepth", "RetractHeight", "Side", "Direction", "OffsetPattern"):
                if prop in obj.PropertiesList:
                    try:
                        val = getattr(obj, prop)
                        result["probes"]["%s.%s" % (name, prop)] = {
                            "ok": True, "value": str(val)[:180]}
                    except Exception as exc:
                        result["probes"]["%s.%s" % (name, prop)] = {"ok": False, "error": str(exc)}

    step("create_ToolController", lambda: TC.Create("TC_probe", toolNumber=1))
    step("job_ToolControllers", lambda: str(job.ToolControllers))

    # 探测：给每个 Op 一个朝上水平面作 Base，看能否 recompute 出刀路
    up = []
    for i, f in enumerate(shape.Faces, start=1):
        try:
            if isinstance(f.Surface, Part.Plane) and f.normalAt(0, 0).z > 0.9 and f.Area > 10:
                up.append((i, float(f.BoundBox.ZMin), float(f.Area)))
        except Exception:
            pass
    up.sort(key=lambda x: -x[2])
    result["up_faces"] = up[:10]
    print("up faces:", up[:10])

    if up:
        idx, z, _ = up[0]
        for name, obj in ops.items():
            def build(obj=obj, name=name, idx=idx, z=z):
                obj.Base = [(model, ["Face%d" % idx])]
                for prop, val in (("StartDepth", float(shape.BoundBox.ZMax) + 1.0),
                                  ("FinalDepth", z - 1.0),
                                  ("ClearanceHeight", float(shape.BoundBox.ZMax) + 6.0),
                                  ("SafeHeight", float(shape.BoundBox.ZMax) + 3.0)):
                    if prop in obj.PropertiesList:
                        try:
                            setattr(obj, prop, val)
                        except Exception:
                            pass
                doc.recompute()
                cmds = list(obj.Path.Commands) if obj.Path else []
                return "%d cmds | %s" % (len(cmds), [c.Name for c in cmds[:8]])
            step("path_" + name, build)

    App.closeDocument(doc.Name)
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
    print("wrote", OUT)


if __name__ in ("__main__", "cam_probe4"):
    main()
