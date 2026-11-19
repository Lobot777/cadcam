"""探测 freecadcmd（headless）下 CAM/Path 工作台的可用性。

用法：
  CADCAM_OUT=reports/cam_probe.json freecadcmd scripts/cam_probe.py

逐项尝试，任何一步失败都记录异常而不是中断，方便判断「哪一层」不可用。
"""

import json
import os
import traceback

import FreeCAD as App
import Part

OUT = os.environ.get("CADCAM_OUT", "reports/cam_probe.json")
result = {"freecad_version": App.Version(), "steps": {}}


def step(name, fn):
    try:
        value = fn()
        result["steps"][name] = {"ok": True, "value": value}
        print("OK  ", name, str(value)[:160])
        return value
    except Exception as exc:
        result["steps"][name] = {"ok": False,
                                 "error": "%s: %s" % (type(exc).__name__, exc),
                                 "trace": traceback.format_exc()[-600:]}
        print("FAIL", name, type(exc).__name__, exc)
        return None


def main():
    step("import Path", lambda: __import__("Path") and "Path imported")
    Path = __import__("Path")
    names = [n for n in dir(Path)
             if any(k in n.lower() for k in ("job", "area", "post", "op", "tool"))]
    result["path_api_names"] = names[:60]

    step("import PathScripts.post", lambda: __import__("PathScripts.post", fromlist=["post"]) and "ok")
    doc = App.newDocument("cam_probe")

    def make_model():
        shape = Part.makeBox(80, 80, 50, App.Vector(-40, -40, 2))
        obj = doc.addObject("Part::Feature", "Model")
        obj.Shape = shape
        doc.recompute()
        return "Part::Feature created, volume=%.1f" % shape.Volume
    step("part_feature", make_model)

    def make_job():
        job = doc.addObject("Path::Job", "Job")
        job.Model = doc.getObject("Model") if hasattr(job, "Model") else None
        doc.recompute()
        return "Path::Job created: %s" % job.TypeId
    step("path_job", make_job)

    def make_stock():
        job = doc.getObject("Job")
        if job is None:
            raise RuntimeError("no job")
        # 1.1 起 Job 用 Stock 属性（PathStock 对象）
        return "stock prop: %s" % (getattr(job, "Stock", None))
    step("job_stock", make_stock)

    def list_ops():
        import PathOp
        return [n for n in dir(PathOp) if n.startswith("Path")][:30]
    step("import PathOp", list_ops)

    def make_profile():
        job = doc.getObject("Job")
        op = doc.addObject("Path::Feature", "Profile")
        op.Label = "ProfileOp"
        # 直接给一段手工 Path 指令，验证 Job 能否产出可后处理的 Path 对象
        import Path
        path = Path.Path(["G0 X0 Y0 Z5", "G1 Z-1 F100", "G1 X10 Y0 F200"])
        op.Path = path
        job.addObject(op) if hasattr(job, "addObject") else None
        doc.recompute()
        return "Path::Feature with %d commands" % len(op.Path.Commands)
    step("manual_path_feature", make_profile)

    def post_it():
        from PathScripts import PostUtils
        return "PostUtils imported"
    step("post_utils", post_it)

    def export_gcode():
        job = doc.getObject("Job")
        if job is None:
            raise RuntimeError("no job")
        # 常见 headless 导出路径：Path.write / postprocessor 的 export
        import Path
        if hasattr(Path, "write"):
            Path.write(job, os.path.splitext(OUT)[0] + "_probe.nc")
            return "Path.write ok"
        return "Path.write absent"
    step("export_gcode", export_gcode)

    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
    print("wrote", OUT)


if __name__ in ("__main__", "cam_probe"):
    main()
