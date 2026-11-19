"""件 10 拓扑探针：在 (r, z) 网格上判定点是否在实体内，打印材料分布表。

比看三视图可靠——视图上 z=48 与 z=44 两个底面谁在外圈、谁在内圈，
在斜视图里被遮挡得看不出，而 isInside 是布尔事实。
"""

import json
import os
import sys
import traceback

import FreeCAD as App  # noqa: F401
import Part

AX = (-1.0, 1.0)   # 圆柱轴线（实测，见 measure10.py）


def main():
    shape = Part.Shape()
    shape.read(os.environ["CADCAM_STEP"])
    solid = shape.Solids[0] if shape.Solids else shape

    rs = [0, 5, 10, 14, 16, 18, 20, 22, 24, 25.5, 26.5, 28, 30, 32, 34,
          34.9, 35.5, 36.5, 37.5, 38.5]
    zs = [1, 20, 33, 35, 42, 44.5, 46, 47.5, 48.5, 50, 51.5, 52.5]
    print("材料分布 (r 横, z 纵)  # = 实体  . = 空")
    print("      " + "".join("%5.1f" % r for r in rs))
    table = {}
    for z in zs:
        row = []
        for r in rs:
            p = App.Vector(AX[0] + r, AX[1], z)
            try:
                inside = solid.isInside(p, 1e-6, True)
            except Exception:
                inside = None
            row.append(inside)
        table[z] = row
        print("z=%5.1f " % z + "".join("    #" if v else "    ." for v in row))
    # 顺带看 -x 方向与斜向，确认轴对称性
    print("\n对角方向 r 扫描 (z=50)")
    for r in [0, 10, 20, 26, 30, 33, 35, 36.5]:
        p = App.Vector(AX[0] + r * 0.7071, AX[1] + r * 0.7071, 50)
        print("   r=%5.1f  %s" % (r, "实体" if solid.isInside(p, 1e-6, True) else "空"))

    if os.environ.get("CADCAM_OUT"):
        with open(os.environ["CADCAM_OUT"], "w", encoding="utf-8") as fh:
            json.dump({"rs": rs, "zs": zs, "material": {str(k): v for k, v in table.items()}},
                      fh, indent=1)
        print("WROTE", os.environ["CADCAM_OUT"])


if __name__ in ("__main__", "probe10"):
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
