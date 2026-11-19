"""第三轮探针：真正让 CAM 操作 headless 生成 Path 指令。"""

import json
import os
import traceback

import FreeCAD as App
import Part

OUT = os.environ.get("CADCAM_OUT", "reports/cam_probe3.json")
result = {"steps": {}}


def step(name, fn):
    try:
        value = fn()
        result["steps"][name] = {"ok": True, "value": str(value)[:600]}
        print("OK  ", name, str(value)[:220])
        return value
    except Exception as exc:
        result["steps"][name] = {"ok": False,
                                 "error": "%s: %s" % (type(exc).__name__, exc),
                                 "trace": traceback.format_exc()[-900:]}
        print("FAIL", name, type(exc).__name__, str(exc)[:220])
        return None


def main():
    from Path.Main import Job as PathJob
    doc = App.newDocument("cam3")
    shape = Part.makeBox(80, 80, 50, App.Vector(-40, -40, 2))
    model = doc.addObject("Part::Feature", "Model")
    model.Shape = shape
    doc.recompute()
    job = PathJob.Create("Job", [model])

    top_idx = None
    for i, f in enumerate(shape.Faces, start=1):
        n = f.normalAt(0, 0)
        if n.z > 0.9 and abs(f.BoundBox.ZMin - 52.0) < 1e-6:
            top_idx = i
            break
    result["top_face"] = top_idx
    print("top face", top_idx)

    def make_millface():
        from Path.Op import MillFace
        op = MillFace.Create("MillFace", parentJob=job)
        try:
            job.Operations.addObject(op)
        except Exception as exc:
            print("   (addObject skipped: %s)" % exc)
        return op
    op = step("MillFace.Create", make_millface)

    if op:
        step("op properties", lambda: [p for p in op.PropertiesList
                                       if any(k in p for k in
                                              ("Base", "Depth", "Step", "Tool", "Path", "Clearance", "Safe"))])
        step("set Base", lambda: (setattr(op, "Base", [(model, ["Face%d" % top_idx])]),
                                  doc.recompute(), str(op.Base))[-1])
        step("set depths", lambda: (setattr(op, "StartDepth", 53.0),
                                    setattr(op, "FinalDepth", 52.0),
                                    setattr(op, "StepDown", 1.0),
                                    doc.recompute(), "ok")[-1])
        step("recompute job", lambda: (doc.recompute(), "ok")[-1])
        step("op.Path commands", lambda: len(op.Path.Commands) if op.Path else 0)
        step("first commands", lambda: [str(c) for c in op.Path.Commands[:6]] if op.Path else [])
        step("job.Path commands", lambda: len(job.Path.Commands) if job.Path else 0)

    def probe_post_factory():
        from Path.Post import Processor
        f = Processor.PostProcessorFactory()
        return [n for n in dir(f) if not n.startswith("_")][:20]
    step("postprocessor factory", probe_post_factory)

    def probe_serialize():
        if not op or not op.Path:
            raise RuntimeError("no path")
        return [{"Name": c.Name, "Parameters": dict(c.Parameters)}
                for c in op.Path.Commands[:5]]
    step("serialize commands", probe_serialize)

    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
    print("wrote", OUT)


if __name__ in ("__main__", "cam_probe3"):
    main()
