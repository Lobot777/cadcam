"""双内核 A/B 回归对比：验证 1.1.4 与 0.20.1 的几何结论是否一致。

判定口径（写入论文"可复现性"章节）：
  - 拓扑必须逐位相等：solids / faces / edges / 面类型分布
  - 连续量允许浮点噪声：volume / area / bbox 相对偏差 < 0.1%

    python scripts/ab_kernel.py
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "src" / "cadcam" / "adapters" / "freecad_worker.py"
DATA = ROOT / "data" / "zenodo_rptu"
REPORT = ROOT / "reports" / "kernel_ab.json"

KERNELS = {
    "0.20.1": Path(r"E:\FREECAD\bin\FreeCADCmd.exe"),
    "1.1.4": Path(r"E:\FreeCAD_1.1.4-Windows-x86_64-py311\bin\freecadcmd.exe"),
}
TOL = 0.001  # 0.1%


def run_worker(cmd: Path, step: Path) -> dict:
    env = dict(os.environ)
    env["CADCAM_STEP"] = str(step)
    with tempfile.TemporaryDirectory(prefix="ab_") as tmp:
        out = Path(tmp) / "g.json"
        env["CADCAM_OUT"] = str(out)
        subprocess.run([str(cmd), str(WORKER)], env=env, capture_output=True, timeout=300)
        if not out.exists():
            return {"error": "no output"}
        payload = json.loads(out.read_text(encoding="utf-8"))
    payload.pop("hashes", None)
    payload.pop("engine", None)
    return payload


def rel(a, b):
    if a is None or b is None:
        return None
    scale = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / scale


def face_types(payload):
    counts = {}
    for f in payload.get("faces", []):
        key = f.get("surface") or "?"
        counts[key] = counts.get(key, 0) + 1
    return counts


def convexity_mix(payload):
    counts = {}
    for e in payload.get("edges", []):
        key = e.get("convexity") or "none"
        counts[key] = counts.get(key, 0) + 1
    return counts


def compare(step: Path):
    views, errors = {}, {}
    for name, cmd in KERNELS.items():
        if not cmd.exists():
            errors[name] = "missing executable: %s" % cmd
            continue
        payload = run_worker(cmd, step)
        if "error" in payload:
            errors[name] = payload["error"]
        else:
            views[name] = payload

    row = {"part": step.stem, "errors": errors}
    if errors:
        row["verdict"] = "BLOCKED"
        return row
    old, new = views["0.20.1"], views["1.1.4"]
    co, cn = old["counts"], new["counts"]
    row.update(
        faces_old=co.get("faces"), faces_new=cn.get("faces"),
        edges_old=co.get("edges"), edges_new=cn.get("edges"),
        solids_old=co.get("solids"), solids_new=cn.get("solids"),
        volume_rel=rel(old.get("volume_mm3"), new.get("volume_mm3")),
        area_rel=rel(old.get("area_mm2"), new.get("area_mm2")),
        types_same=face_types(old) == face_types(new),
        convex_same=convexity_mix(old) == convexity_mix(new),
    )
    topology_ok = (
        row["faces_old"] == row["faces_new"]
        and row["edges_old"] == row["edges_new"]
        and row["solids_old"] == row["solids_new"]
        and row["types_same"]
        and row["convex_same"]
    )
    metric_ok = (row["volume_rel"] or 0) < TOL and (row["area_rel"] or 0) < TOL
    row["verdict"] = "PASS" if topology_ok and metric_ok else "DIFF"
    return row


def main():
    steps = sorted(DATA.glob("*.step"), key=lambda p: int(p.stem) if p.stem.isdigit() else 0)
    if not steps:
        print("未找到 STEP 数据")
        return 1
    rows = [compare(p) for p in steps]
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    print("%-4s %-8s %-10s %-12s %-12s %-8s" % ("件", "判定", "面(旧/新)", "边(旧/新)", "体积偏差", "面积偏差"))
    for r in rows:
        if r["verdict"] == "BLOCKED":
            print("%-4s %-8s %s" % (r["part"], "BLOCKED", r["errors"]))
            continue
        print("%-4s %-8s %-10s %-12s %-12s %-8s" % (
            r["part"], r["verdict"],
            "%d/%d" % (r["faces_old"], r["faces_new"]),
            "%d/%d" % (r["edges_old"], r["edges_new"]),
            "%.4f%%" % ((r["volume_rel"] or 0) * 100),
            "%.4f%%" % ((r["area_rel"] or 0) * 100),
        ))
    passed = sum(1 for r in rows if r["verdict"] == "PASS")
    print("\nPASS %d / %d，报告 %s" % (passed, len(rows), REPORT))
    return 0 if passed == len(rows) else 2


if __name__ == "__main__":
    sys.exit(main())
