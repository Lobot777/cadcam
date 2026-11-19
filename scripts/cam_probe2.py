"""第二轮探针：按 FreeCAD 1.1 的真实 API（FeaturePython + Proxy）创建 CAM Job。"""

import json
import os
import traceback

import FreeCAD as App
import Part

OUT = os.environ.get("CADCAM_OUT", "reports/cam_probe2.json")
result = {"steps": {}}


def step(name, fn):
    try:
        value = fn()
        result["steps"][name] = {"ok": True, "value": str(value)[:500]}
        print("OK  ", name, str(value)[:200])
        return value
    except Exception as exc:
        result["steps"][name] = {"ok": False,
                                 "error": "%s: %s" % (type(exc).__name__, exc),
                                 "trace": traceback.format_exc()[-800:]}
        print("FAIL", name, type(exc).__name__, str(exc)[:200])
        return None


def main():
    doc = App.newDocument("cam2")
    shape = Part.makeBox(80, 80, 50, App.Vector(-40, -40, 2))
    model = doc.addObject("Part::Feature", "Model")
    model.Shape = shape
    doc.recompute()

    Job = step("import Path.Main.Job", lambda: __import__("Path.Main.Job", fromlist=["Job"]))
    job = None
    if Job:
        job = step("Job.Create", lambda: Job.Create("Job", [model]))
    if job:
        step("job properties", lambda: [p for p in job.PropertiesList
                                        if any(k in p for k in ("Stock", "Model", "Post", "Path", "Tool"))])
        step("job Operations", lambda: str(getattr(job, "Operations", None)))

    def list_op_modules():
        import Path.Op
        import pkgutil
        return [m.name for m in pkgutil.iter_modules(Path.Op.__path__)][:40]
    mods = step("list Path.Op modules", list_op_modules) or []

    def probe_face():
        from Path.Op import Face
        return [n for n in dir(Face) if n in ("Create", "ObjectFace", "name")]
    step("Path.Op.Face api", probe_face)

    def probe_pocket():
        from Path.Op import Pocket
        return [n for n in dir(Pocket) if n in ("Create", "ObjectPocket")]
    step("Path.Op.Pocket api", probe_pocket)

    def probe_post():
        import Path.Post
        import pkgutil
        return {"attrs": [n for n in dir(Path.Post) if not n.startswith("_")][:25],
                "modules": [m.name for m in pkgutil.iter_modules(Path.Post.__path__)][:25]}
    step("Path.Post api", probe_post)

    def probe_processor():
        from Path.Post import Processor
        return [n for n in dir(Processor) if "Post" in n or "post" in n][:25]
    step("Path.Post.Processor api", probe_processor)

    def make_manual_path():
        import Path
        cmds = [Path.Command("G0", {"X": 0, "Y": 0, "Z": 5}),
                Path.Command("G1", {"Z": -1, "F": 100}),
                Path.Command("G1", {"X": 10, "Y": 0, "F": 200})]
        p = Path.Path(cmds)
        return "commands=%d" % len(p.Commands)
    step("manual Path.Path", make_manual_path)

    def try_face_op():
        if not job:
            raise RuntimeError("no job")
        from Path.Op import Face
        op = Face.Create("FaceOp", job)
        doc.recompute()
        return "created %s" % op.Name
    step("create Face op", try_face_op)

    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
    print("wrote", OUT)


if __name__ in ("__main__", "cam_probe2"):
    main()
