"""冻结数据集 v1：给每个用到的文件算 sha256，生成可引用的清单。

M5 的第一件事是「基准冻结」——论文里的数字必须能指回一批确定的字节。
清单不替代数据卡（`docs/DATA-CARD.md`），只保证「同一份输入」。

    PYTHONPATH=src python scripts/freeze_dataset.py
    → data/DATASET-MANIFEST.json
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "DATASET-MANIFEST.json")
VERSION = "v1"

# 参与论文数字的目录（不纳入 build/ 与 reports/*.png 等派生产物）
SOURCES = [
    ("zenodo_rptu", "data/zenodo_rptu", (".step", ".ngc", ".png")),
    ("face_labels", "data/zenodo_rptu/labels", (".json",)),
    ("geom_cache", "reports/annotation/geom", (".json",)),
]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def walk(rel_dir, exts):
    base = os.path.join(ROOT, rel_dir)
    out = []
    if not os.path.isdir(base):
        return out
    for name in sorted(os.listdir(base)):
        if not name.lower().endswith(exts):
            continue
        p = os.path.join(base, name)
        if not os.path.isfile(p):
            continue
        out.append({"path": "%s/%s" % (rel_dir, name),
                    "bytes": os.path.getsize(p),
                    "sha256": sha256(p)})
    return out


def main():
    groups = {}
    total_files = total_bytes = 0
    for name, rel, exts in SOURCES:
        files = walk(rel, exts)
        groups[name] = {"dir": rel, "n_files": len(files), "files": files}
        total_files += len(files)
        total_bytes += sum(f["bytes"] for f in files)

    parts = sorted(int(f["path"].rsplit("/", 1)[-1].split(".")[0])
                   for f in groups["zenodo_rptu"]["files"] if f["path"].endswith(".step"))

    manifest = {
        "version": VERSION,
        "frozen_at": "2026-11-05",
        "parts": parts,
        "n_parts": len(parts),
        "n_files": total_files,
        "total_bytes": total_bytes,
        "groups": groups,
        "external": [{
            "name": "MFCAD++",
            "used_for": "M2 面级特征识别训练/测试（7998/1500/1998 件）",
            "in_repo": False,
            "why_not_vendored": "第三方数据集体量大，按上游许可单独分发；"
                                "复现需按其发布页下载后运行 scripts/extract_mfcad.py",
            "artifact": "reports/mfr_test_v2.json（训练产物与指标）",
        }],
        "derived": [
            "reports/m0_baseline.json", "reports/m1_acceptance.json",
            "reports/m2_acceptance.json", "reports/m3_acceptance.json",
            "reports/m3_cam_eval.json", "reports/m3_heightmap_eval.json",
            "reports/m3_criterion_analysis.json", "reports/m4_run.json",
        ],
        "note": "derived 里的文件由 scripts/ 下脚本从本清单输入重算，不入哈希链；"
                "bench 会重算并比对关键数字。",
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)

    print("数据集 %s：%d 件 / %d 个文件 / %.1f MB"
          % (VERSION, len(parts), total_files, total_bytes / 1e6))
    for name, g in groups.items():
        print("  %-12s %3d 文件  %s" % (name, g["n_files"], g["dir"]))
    print("→", os.path.relpath(OUT, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
