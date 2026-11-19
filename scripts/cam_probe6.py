"""探针 6：件 10 F32（z=34，面积 316mm²）在 MillFace 各边界模式下的实际走刀范围。

用于定位 A 路在复杂多级腔件上过切的根因。输出 reports/cam_probe6.json。
"""
import json
import os

import FreeCAD as App
import Part

DATA = os.environ.get("CADCAM_DATA", "data/zenodo_rptu")
OUT = os.environ.get("CADCAM_OUT", "reports/cam_probe6.json")
rows = []


def main():
    from Path.Main import Job as PathJob
    from Path.Op import MillFace

    shape = Part.Shape()
    shape.read(os.path.join(DATA, "10.step"))
    for boundary in ("Boundbox", "Face Region", "Perimeter", "Stock"):
        for exclude in (False, True):
            doc = App.newDocument("p6")
            model = doc.addObject("Part::Feature", "Model")
            model.Shape = shape
            doc.recompute()
            job = PathJob.Create("Job", [model])
            op = MillFace.Create("F", parentJob=job)
            job.Operations.addObject(op)
            op.Base = [(model, ["Face32"])]
            op.BoundaryShape = boundary
            op.ExcludeRaisedAreas = exclude
            op.StartDepth = float(shape.BoundBox.ZMax) + 1.0
            op.FinalDepth = 34.0
            op.StepDown = 2.0
            op.ClearanceHeight = float(shape.BoundBox.ZMax) + 6.0
            op.SafeHeight = float(shape.BoundBox.ZMax) + 3.0
            try:
                doc.recompute()
                cmds = list(op.Path.Commands) if op.Path else []
                xs = [c.Parameters["X"] for c in cmds if "X" in c.Parameters]
                ys = [c.Parameters["Y"] for c in cmds if "Y" in c.Parameters]
                row = {"boundary": boundary, "exclude_raised": exclude,
                       "commands": len(cmds),
                       "x_range": [round(min(xs), 1), round(max(xs), 1)] if xs else None,
                       "y_range": [round(min(ys), 1), round(max(ys), 1)] if ys else None}
            except Exception as exc:
                row = {"boundary": boundary, "exclude_raised": exclude,
                       "error": "%s: %s" % (type(exc).__name__, exc)}
            rows.append(row)
            print("%-12s exclude=%-5s cmds=%-4s X=%s" %
                  (boundary, exclude, row.get("commands"), row.get("x_range")))
            App.closeDocument(doc.Name)
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=1)
    print("wrote", OUT)


if __name__ in ("__main__", "cam_probe6"):
    main()
