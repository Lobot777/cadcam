"""在留出的 test 集上评测识别器，并与对照组并列输出。

只看 val 是不够的：val 参与了早停选择，数字会偏乐观。
test 集从未参与任何决策，才是能写进论文的数。
"""

import argparse
import json
import pickle
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import torch  # noqa: E402

from cadcam.modalities.mfr import model as M  # noqa: E402
from cadcam.modalities.mfr.predict import Recognizer  # noqa: E402
from scripts.train_mfr import collate, evaluate, tensors  # noqa: E402


def load(path):
    with open(path, "rb") as handle:
        return pickle.load(handle)["samples"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--weights", default=str(ROOT / "reports/mfr_train.pt"))
    parser.add_argument("--test", default=str(ROOT / "data/mfcad/aag/test.pkl"))
    parser.add_argument("--baseline", default=str(ROOT / "reports/mfr_baseline.json"))
    parser.add_argument("--out", default=str(ROOT / "reports/mfr_test.json"))
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--drop-edges", action="store_true",
                        help="消融模型评测时必须摘掉边，否则会走进训练时没优化过的消息分支")
    args = parser.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    samples = load(args.test)
    if args.drop_edges:
        for sample in samples:
            sample["edge_index"] = []
            sample["edge_attr"] = []
        print("已摘除全部邻接边（消融模式）")
    print("test: %d 件 / %d 面" % (len(samples), sum(s["num_faces"] for s in samples)))

    net = M.FeatureNet(hidden=256, layers=3).to(device)
    payload = torch.load(args.weights, map_location="cpu", weights_only=False)
    net.load_state_dict(payload["state_dict"])
    net.eval()

    result = evaluate(net, samples, device, args.batch_size)
    print("test 面级准确率 %.4f   mIoU %.4f" % (result["accuracy"], result["macro_iou"]))

    baseline = None
    baseline_path = Path(args.baseline)
    if baseline_path.exists():
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))

    # 逐类并列，重点看 GNN 到底在哪些类上赢
    rows = []
    for name, value in result["per_class"].items():
        old = (baseline or {}).get("per_class", {}).get(name)
        rows.append({
            "class": name,
            "support": value["support"],
            "gnn_iou": value["iou"],
            "gnn_recall": value["recall"],
            "tree_iou": old["iou"] if old else None,
            "delta_iou": round(value["iou"] - old["iou"], 4) if old else None,
        })
    rows.sort(key=lambda row: row["gnn_iou"])

    print()
    print("%-30s %7s %9s %9s %8s" % ("class", "support", "GNN IoU", "Tree IoU", "Δ"))
    for row in rows:
        tree = "%.3f" % row["tree_iou"] if row["tree_iou"] is not None else "—"
        delta = "%+.3f" % row["delta_iou"] if row["delta_iou"] is not None else "—"
        print("%-30s %7d %9.3f %9s %8s" % (
            row["class"], row["support"], row["gnn_iou"], tree, delta
        ))

    summary = {
        "task": "MFCAD++ face-level machining feature recognition (held-out test)",
        "test_parts": len(samples),
        "test_faces": result["faces"],
        "gnn": {
            "accuracy": result["accuracy"],
            "macro_iou": result["macro_iou"],
            "params_m": round(sum(p.numel() for p in net.parameters()) / 1e6, 3),
        },
        "tree_baseline": {
            "accuracy": (baseline or {}).get("val_accuracy"),
            "macro_iou": (baseline or {}).get("val_macro_iou"),
            "note": "对照组的数字来自 val 集（其训练脚本未做 test 评测）",
        } if baseline else None,
        "per_class": rows,
        "confusion": result["confusion"],
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print("→", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
