"""从 MFCAD++ 的 zip 里批量抽取 AAG 图，落成可训练的样本集。

不整包解压，直接从 zip 流式读取：59665 个 STEP 解压出来约 6GB，
而抽取完只需要几十 MB 的图数据，中间产物没必要落盘。

并行用多进程，每个进程独立开 zip、独立调 freecadcmd。实测单件 0.27s
（主要是内核启动），24 核并行 12 路可到 10 分钟量级。
"""

import argparse
import pickle
import sys
import time
import zipfile
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

ZIP = Path(r"C:\Users\Admin\Downloads\MFCAD_dataset.zip")
MEMBER_ROOT = "MFCAD++_dataset/step"


def _read_split(split):
    names_path = ROOT / "data" / "mfcad" / ("%s.txt" % split)
    with names_path.open("r", encoding="utf-8") as handle:
        return [line.strip() for line in handle if line.strip()]


def _one(job):
    """worker：从 zip 取一件，抽几何、读标签、构图。"""
    import os
    import tempfile

    from cadcam.adapters import freecad
    from cadcam.modalities.brep import step_labels
    from cadcam.modalities.mfr import aag

    part, split = job
    member = "%s/%s/%s.step" % (MEMBER_ROOT, split, part)
    try:
        with zipfile.ZipFile(ZIP) as archive:
            payload = archive.read(member)
    except KeyError:
        return {"part": part, "error": "MISSING"}

    text = payload.decode("utf-8", "replace")
    faces = step_labels.parse_labels(text)
    if not faces:
        return {"part": part, "error": "NO_LABEL"}

    handle, path = tempfile.mkstemp(suffix=".step")
    os.close(handle)
    try:
        with open(path, "wb") as out:
            out.write(payload)
        # 单件 45s 上限：实测正常件 0.27s，卡住的件会一直挂到超时，
        # 不设上限时整批会被一件坏数据拖死
        geom = freecad.geometry(path, use_cache=False, timeout=45)
        if "error" in geom:
            return {"part": part, "error": "GEOM_FAIL"}
        labels = [face["label"] for face in faces]
        sample = aag.build(geom, labels)
    except Exception as exc:  # noqa: BLE001
        # 关键：worker 绝不能抛。multiprocessing.Pool 里一个异常会让
        # imap_unordered 的整个迭代器中断，前面已抽好的样本全丢。
        return {"part": part, "error": "EXC:%s" % type(exc).__name__}
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    if sample is None:
        return {"part": part, "error": "EMPTY"}

    # 只留训练所需的字段，几何原文不落盘
    sample["part"] = part
    sample["split"] = split
    sample["stock"] = [
        geom["bbox"]["max"][i] - geom["bbox"]["min"][i] for i in range(3)
    ]
    sample.pop("source", None)
    return sample


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", default="train")
    parser.add_argument("--limit", type=int, default=6000)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    ids = _read_split(args.split)[: args.limit]
    out_path = Path(args.out) if args.out else (
        ROOT / "data" / "mfcad" / "aag" / ("%s.pkl" % args.split)
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print("抽取 %s：%d 件，%d 路并行" % (args.split, len(ids), args.workers))
    started = time.perf_counter()

    samples, errors = [], {}
    with Pool(args.workers) as pool:
        jobs = [(part, args.split) for part in ids]
        for done, result in enumerate(pool.imap_unordered(_one, jobs, chunksize=8), 1):
            if "error" in result:
                errors[result["error"]] = errors.get(result["error"], 0) + 1
            else:
                samples.append(result)
            if done % 500 == 0 or done == len(ids):
                elapsed = time.perf_counter() - started
                rate = done / elapsed if elapsed else 0
                print(
                    "  %5d/%d  ok=%-5d  %.1f 件/s  已用 %.0fs  预计剩余 %.0fs"
                    % (
                        done,
                        len(ids),
                        len(samples),
                        rate,
                        elapsed,
                        (len(ids) - done) / rate if rate else 0,
                    )
                )

    payload = {
        "split": args.split,
        "samples": samples,
        "errors": errors,
        "meta": {
            "requested": len(ids),
            "ok": len(samples),
            "elapsed_s": round(time.perf_counter() - started, 1),
            "workers": args.workers,
        },
    }
    with out_path.open("wb") as handle:
        pickle.dump(payload, handle, protocol=4)

    total_faces = sum(sample["num_faces"] for sample in samples)
    print()
    print("完成：%d 件，%d 个面，错误 %s" % (len(samples), total_faces, errors or "无"))
    print("耗时 %.0fs → %s (%.1f MB)" % (
        payload["meta"]["elapsed_s"], out_path, out_path.stat().st_size / 1048576
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
