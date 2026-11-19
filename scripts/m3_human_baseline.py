"""人工标注题目包的「平凡基线」：不看几何，只数红色像素。

用途：给人工一致率一个可比较的参照系。若一个只统计红色像素、
完全不理解「残留 vs 过切」的分类器也能拿到与真人相同的准确率，
那么真人一致率再高，也不能作为「题目测得出判读能力」的证据。

结论写入 reports/m3_human/ceiling_baseline.json（v2 题目包）。

    PYTHONPATH=src python scripts/m3_human_baseline.py
    PYTHONPATH=src python scripts/m3_human_baseline.py --dir reports/m3_human_v1
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DIR = os.path.join(ROOT, "reports", "m3_human")

# 与 m3_human_pack._png 一致：#d94a3d = (217, 74, 61)
RED = (217 / 255.0, 74 / 255.0, 61 / 255.0)
TOL = 0.06
# 右侧 colorbar 固定含红/蓝/灰三色块，必须裁掉，否则每张图都「有红」
CROP_RIGHT = 0.20


def red_pixels(png_b64: str) -> int:
    img = Image.open(io.BytesIO(base64.b64decode(png_b64))).convert("RGB")
    a = np.asarray(img, dtype=np.float32) / 255.0
    h, w = a.shape[:2]
    main = a[:, : int(w * (1.0 - CROP_RIGHT)), :3]
    r, g, b = main[:, :, 0], main[:, :, 1], main[:, :, 2]
    hit = ((np.abs(r - RED[0]) < TOL) & (np.abs(g - RED[1]) < TOL)
           & (np.abs(b - RED[2]) < TOL))
    return int(hit.sum())


def run(pack_dir: str) -> dict:
    with open(os.path.join(pack_dir, "samples.json"), encoding="utf-8") as fh:
        samples = json.load(fh)
    with open(os.path.join(pack_dir, "key.json"), encoding="utf-8") as fh:
        data = json.load(fh)
    key = data.get("items", data)  # v1 的 key 是扁平结构，v2 起带 pack_version 包一层

    rows = []
    for s in samples:
        sid = s["sample_id"]
        rows.append({"sample_id": sid, "red_px": red_pixels(s["png"]),
                     "truth": key[sid]["truth"], "kind": key[sid]["kind"]})

    neg = [r["red_px"] for r in rows if r["truth"] == "no_gouge"]
    pos = [r["red_px"] for r in rows if r["truth"] == "gouge"]
    # 阈值取「无过切样本红像素最大值 + 1」：这是对分类器最不利（最保守）的取法，
    # 不给它任何调参空间，因此得到的准确率是下界而不是上界。
    thr = (max(neg) + 1) if neg else 1
    correct = sum((r["red_px"] >= thr) == (r["truth"] == "gouge") for r in rows)

    out = {
        "pack_dir": os.path.relpath(pack_dir, ROOT).replace("\\", "/"),
        "n": len(rows),
        "red_pixels": rows,
        "no_gouge_red_px_max": max(neg) if neg else None,
        "gouge_red_px_min": min(pos) if pos else None,
        "threshold": thr,
        "pixel_baseline_accuracy": round(correct / len(rows), 4),
        "note": "只统计主图区（右侧 %d%% colorbar 已裁剪）的红色像素；"
                "阈值取无过切样本最大值+1，无调参空间" % int(CROP_RIGHT * 100),
    }
    with open(os.path.join(pack_dir, "ceiling_baseline.json"), "w",
              encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=DEFAULT_DIR, help="题目包目录（含 samples.json/key.json）")
    args = ap.parse_args()
    out = run(args.dir)
    print("题目包 %s｜%d 题" % (out["pack_dir"], out["n"]))
    print("无过切样本红像素最大值 = %s｜过切样本最小值 = %s｜阈值 = %s"
          % (out["no_gouge_red_px_max"], out["gouge_red_px_min"], out["threshold"]))
    print("平凡像素分类器准确率 = %.4f" % out["pixel_baseline_accuracy"])
    print("→ 真人一致率若等于该值，则一致率不构成判读能力的证据")


if __name__ == "__main__":
    main()
