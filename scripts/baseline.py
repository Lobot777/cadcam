"""M0 基线：对锚点集 10 件同时抽取几何摘要与参考 G 代码统计。

    python scripts/baseline.py --dir data/zenodo_rptu --out reports/m0_baseline.json

产出用于回答两个问题：
1. 几何侧：不同零件的面/边规模分布，是否落在 LLM 可处理区间；
2. 制造侧：参考程序的工序数、空切比、Z 深度，作为后续自生成数据的对照基准。
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cadcam.adapters import freecad
from cadcam.modalities.brep import digest
from cadcam.modalities.cam import ngc


def run_one(step_path, ngc_path):
    row = {"part": step_path.stem}
    try:
        geom = freecad.geometry(step_path)
        summary = digest.summarize(geom)
        row["geometry"] = {
            "faces": summary["counts"]["faces"],
            "edges": summary["counts"]["edges"],
            "size_mm": summary["size_mm"],
            "volume_mm3": summary["volume_mm3"],
            "surfaces": summary["surface_histogram"],
            "concave_edges": summary["convexity_histogram"].get("concave", 0),
            "openness_z": next(
                (h["openness"] for h in summary["machining_hints"] if "openness" in h), None
            ),
        }
    except Exception as exc:
        row["geometry_error"] = str(exc)

    if ngc_path and ngc_path.exists():
        try:
            result = ngc.parse(ngc_path.read_text(encoding="utf-8", errors="replace"))
            row["gcode"] = {
                "blocks": result["blocks"],
                "segments": result["stats"]["segments"],
                "operations": len(result["operations"]),
                "op_labels": [op["label"] for op in result["operations"]],
                "tools": result["tools"],
                "cut_mm": result["stats"]["cut_mm"],
                "rapid_mm": result["stats"]["rapid_mm"],
                "air_cut_ratio": result["stats"]["air_cut_ratio"],
                "z_min": result["stats"]["z_min"],
                "warnings": len(result["warnings"]),
                "unknown_codes": result["unknown_codes"],
            }
        except Exception as exc:
            row["gcode_error"] = str(exc)
    return row


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default="data/zenodo_rptu")
    parser.add_argument("--out", default="reports/m0_baseline.json")
    args = parser.parse_args(argv)

    root = Path(args.dir)
    rows = []
    for index in range(1, 11):
        step = root / ("%d.step" % index)
        if not step.exists():
            continue
        rows.append(run_one(step, root / ("%d.ngc" % index)))
        print(".", end="", flush=True)
    print()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dataset": str(root),
        "kernel": {"cmd": freecad.FREECAD_CMD, "version": freecad.engine_version()},
        "parser": ngc.PARSER_VERSION,
    }
    out_path.write_text(
        json.dumps({"meta": meta, "parts": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    header = "| 件 | 面 | 边 | 凹边 | 尺寸(mm) | 体积(mm³) | 工序 | 刀 | 切削(mm) | 空切比 | Zmin |"
    print(header)
    print("|---:|---:|---:|---:|:--|---:|---:|---:|---:|---:|---:|")
    for row in rows:
        geometry = row.get("geometry", {})
        gcode = row.get("gcode", {})
        size = geometry.get("size_mm") or [0, 0, 0]
        print("| %s | %s | %s | %s | %.0f×%.0f×%.0f | %.0f | %s | %s | %.0f | %s | %s |" % (
            row["part"], geometry.get("faces", "-"), geometry.get("edges", "-"),
            geometry.get("concave_edges", "-"), size[0], size[1], size[2],
            geometry.get("volume_mm3") or 0,
            gcode.get("operations", "-"), ",".join(str(t) for t in gcode.get("tools", [])),
            gcode.get("cut_mm") or 0, gcode.get("air_cut_ratio"), gcode.get("z_min")))
    print("\n写入 %s（%d 件）" % (out_path, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
