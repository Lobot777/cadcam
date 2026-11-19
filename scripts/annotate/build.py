"""批量生成标注底稿：几何 -> 图 -> 规则标签。

用法： python scripts/annotate/build.py [--parts 1,2,3]
产物：
  reports/annotation/geom/<n>.json     带三角剖分的面表
  reports/annotation/<n>_views.png     8 视图 + 面号
  reports/annotation/<n>_faces.png     逐面高亮接触表
  data/zenodo_rptu/labels/<n>.draft.json 规则预标注（人工覆盖见 overrides/）
"""

import glob
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FREECAD = os.environ.get("FREECAD_CMD", r"E:\FreeCAD_1.1.4-Windows-x86_64-py311\bin\freecadcmd.exe")
DATA = os.path.join(ROOT, "data", "zenodo_rptu")
OUT = os.path.join(ROOT, "reports", "annotation")
GEOM = os.path.join(OUT, "geom")
LABELS = os.path.join(DATA, "labels")


def run_geom(part, step_path):
    dst = os.path.join(GEOM, "%s.json" % part)
    env = dict(os.environ, CADCAM_STEP=step_path, CADCAM_OUT=dst)
    proc = subprocess.run([FREECAD, os.path.join(ROOT, "scripts", "annotate", "geom_worker.py")],
                          env=env, capture_output=True, text=True)
    if not os.path.exists(dst):
        raise RuntimeError("geom worker 无输出: %s\n%s\n%s" % (part, proc.stdout[-500:], proc.stderr[-800:]))
    with open(dst, encoding="utf-8") as fh:
        payload = json.load(fh)
    if "error" in payload:
        raise RuntimeError("geom worker 报错: %s" % payload["error"][-800:])
    return dst


def main(argv):
    parts = [str(p) for p in range(1, 11)]
    if "--parts" in argv:
        parts = argv[argv.index("--parts") + 1].split(",")
    for path in (GEOM, LABELS):
        os.makedirs(path, exist_ok=True)

    for part in parts:
        step = os.path.join(DATA, "%s.step" % part)
        if not os.path.exists(step):
            print("跳过 %s（无 STEP）" % part)
            continue
        geom_path = run_geom(part, step)
        subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "annotate", "render.py"),
                        geom_path, part, OUT], check=True)
        subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "annotate", "autolabel.py"),
                        geom_path, os.path.join(LABELS, "%s.draft.json" % part)], check=True)
    print("done ->", OUT)


if __name__ == "__main__":
    main(sys.argv[1:])
