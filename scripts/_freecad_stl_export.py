"""FreeCADCmd 内部宏：把 10 件 Zenodo STEP 用另一条三角化/序列化链路导成二进制 STL。

由 `scripts/m6_step_stl_xcheck.py` 调用，不要直接用系统 python 运行：
    CADCAM_ROOT=E:/CAD2CAM CADCAM_OUTDIR=... freecadcmd scripts/_freecad_stl_export.py

独立性边界（务必如实写进论文）：B-rep 读 STEP 仍用 OCC（和主链一样），
所以这**不是第二个 CAD 内核**；独立的是「Mesh 三角化 → 二进制 STL 文件 →
OCL STLReader」这条几何链路，它完全绕开我们自己的 geom JSON 缓存与
`dexel.rasterize` 的三角形解析代码。
"""
import json
import os

import FreeCAD as App
import Mesh
import Part

root = os.environ["CADCAM_ROOT"]
outdir = os.environ["CADCAM_OUTDIR"]
os.makedirs(outdir, exist_ok=True)
rows = []
for i in range(1, 11):
    step = os.path.join(root, "data", "zenodo_rptu", "%d.step" % i)
    shape = Part.Shape()
    shape.read(step)
    tess = shape.tessellate(0.05)          # (vertices, facet-index-triples)
    mesh = Mesh.Mesh(tess)                 # Mesh 构造器认这个二元组
    out = os.path.join(outdir, "%d.stl" % i)
    mesh.write(out)
    bb = shape.BoundBox
    rows.append({"part": i, "stl": "%d.stl" % i, "facets": len(mesh.Facets),
                 "vertices": len(tess[0]), "faces_brep": len(shape.Faces),
                 "volume_mm3": round(shape.Volume, 2),
                 "bbox_min": [bb.XMin, bb.YMin, bb.ZMin],
                 "bbox_max": [bb.XMax, bb.YMax, bb.ZMax]})
with open(os.path.join(outdir, "manifest.json"), "w", encoding="utf-8") as fh:
    json.dump({"kernel": "OCC via FreeCAD %s" % ".".join(App.Version()[:3]),
               "tessellation": "Part.Shape.tessellate(0.05) -> Mesh.Mesh -> binary STL",
               "parts": rows}, fh, ensure_ascii=False, indent=1)
print("STL_EXPORT_OK %d parts" % len(rows))
