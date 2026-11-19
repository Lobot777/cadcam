"""校验 MFCAD++ 的 STEP 面顺序与几何内核读入顺序是否一致。

这是一个必须先钉死的前提：如果能按索引对齐，标签就是免费的；
如果不能，就必须做几何匹配，而几何匹配会在相同形状的面上产生歧义
（比如一个立方体的六个面面积两两相等）。
"""

import argparse
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cadcam.adapters import freecad  # noqa: E402
from cadcam.modalities.brep import step_labels  # noqa: E402

ZIP = Path(r"C:\Users\Admin\Downloads\MFCAD_dataset.zip")
OUT = Path(__file__).resolve().parents[1] / "data" / "mfcad"


def sample_names(zip_file, split, limit):
    prefix = "MFCAD++_dataset/step/%s/" % split
    names = [n for n in zip_file.namelist() if n.startswith(prefix) and n.endswith(".step")]
    names.sort()
    return names[:limit]


def check_one(zip_file, member, tmp_dir):
    text = zip_file.read(member).decode("utf-8", "replace")
    faces = step_labels.parse_labels(text)
    if not faces:
        return {"part": member, "verdict": "NO_LABEL"}

    path = tmp_dir / "probe.step"
    path.write_bytes(zip_file.read(member))
    geom = freecad.geometry(path, use_cache=False)
    kernel_seq = [f["surface"] for f in geom["faces"]]
    step_seq = step_labels.surface_sequence(faces)

    row = {
        "part": Path(member).stem,
        "step_faces": len(step_seq),
        "kernel_faces": len(kernel_seq),
        "labels": [f["label"] for f in faces],
    }
    if len(step_seq) != len(kernel_seq):
        row["verdict"] = "COUNT_MISMATCH"
    elif step_seq == kernel_seq:
        row["verdict"] = "PASS"
    else:
        first = next(
            (i for i, (a, b) in enumerate(zip(step_seq, kernel_seq)) if a != b), -1
        )
        row["verdict"] = "ORDER_DIFF"
        row["first_diff"] = first
        row["diff_at"] = [step_seq[first], kernel_seq[first]]
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", default="test")
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--out", default=str(OUT / "order_alignment.json"))
    args = parser.parse_args()

    tmp_dir = OUT / "_probe"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    with zipfile.ZipFile(ZIP) as zip_file:
        names = sample_names(zip_file, args.split, args.limit)
        for i, member in enumerate(names, 1):
            row = check_one(zip_file, member, tmp_dir)
            rows.append(row)
            print(
                "[%3d/%3d] %-8s %-14s step=%-3d kernel=%-3d"
                % (
                    i,
                    len(names),
                    row["part"],
                    row["verdict"],
                    row.get("step_faces", -1),
                    row.get("kernel_faces", -1),
                )
            )

    passed = sum(1 for r in rows if r["verdict"] == "PASS")
    summary = {
        "split": args.split,
        "checked": len(rows),
        "pass": passed,
        "pass_rate": round(passed / max(len(rows), 1), 4),
        "kernel": freecad.engine_version(),
        "verdicts": {},
        "rows": rows,
    }
    for r in rows:
        summary["verdicts"][r["verdict"]] = summary["verdicts"].get(r["verdict"], 0) + 1

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print()
    print("对齐通过 %d/%d (%.1f%%)" % (passed, len(rows), 100.0 * passed / len(rows)))
    print("结论:", json.dumps(summary["verdicts"], ensure_ascii=False))
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
