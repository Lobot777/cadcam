"""训练 / 评测面级加工特征识别模型。

指标口径对齐文献（Hierarchical CADNet, CAD 2022）：**面级分类准确率**，
即所有测试零件的所有 B-Rep 面中，预测类别等于真值的比例。
另外报告 per-class IoU —— 准确率在类别极不平衡时（stock 占比常超 40%）
会虚高，IoU 才能看出少数类到底学到没有。
"""

import argparse
import json
import pickle
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import torch  # noqa: E402
import torch.nn.functional as F  # noqa: E402

from cadcam.modalities.brep.step_labels import MFCAD_LABELS  # noqa: E402
from cadcam.modalities.mfr import model as M  # noqa: E402


def load(path):
    with open(path, "rb") as handle:
        return pickle.load(handle)["samples"]


def tensors(sample, device):
    return {
        "x": torch.tensor(sample["x"], dtype=torch.float32, device=device),
        "edge_index": torch.tensor(sample["edge_index"], dtype=torch.long, device=device)
        .t()
        .contiguous(),
        "edge_attr": torch.tensor(sample["edge_attr"], dtype=torch.float32, device=device),
        "y": torch.tensor(sample["y"], dtype=torch.long, device=device),
    }


def collate(items, device):
    """把若干张图拼成一张大图；batch 向量只用于按图归约。"""
    xs, eis, eas, ys, batches = [], [], [], [], []
    offset = 0
    for index, item in enumerate(items):
        xs.append(item["x"])
        eis.append(item["edge_index"] + offset)
        eas.append(item["edge_attr"])
        ys.append(item["y"])
        batches.append(torch.full((item["y"].numel(),), index, dtype=torch.long, device=device))
        offset += item["y"].numel()
    return {
        "x": torch.cat(xs),
        "edge_index": torch.cat(eis, dim=1),
        "edge_attr": torch.cat(eas),
        "y": torch.cat(ys),
        "batch": torch.cat(batches),
    }


def class_counts(samples):
    counter = Counter()
    for sample in samples:
        counter.update(sample["y"])
    return counter


def evaluate(net, samples, device, batch_size=32):
    net.eval()
    confusion = torch.zeros(M.NUM_CLASSES, M.NUM_CLASSES, dtype=torch.long)
    with torch.no_grad():
        for start in range(0, len(samples), batch_size):
            chunk = [tensors(s, device) for s in samples[start : start + batch_size]]
            batch = collate(chunk, device)
            logits = net(batch)
            predicted = logits.argmax(dim=-1)
            truth = batch["y"]
            for t, p in zip(truth.tolist(), predicted.tolist()):
                confusion[t, p] += 1

    total = confusion.sum().item()
    correct = confusion.diag().sum().item()
    per_class = {}
    for index in range(M.NUM_CLASSES):
        tp = confusion[index, index].item()
        support = confusion[index].sum().item()
        predicted_count = confusion[:, index].sum().item()
        if support == 0 and predicted_count == 0:
            continue
        union = support + predicted_count - tp
        per_class[MFCAD_LABELS.get(index, str(index))] = {
            "support": support,
            "recall": round(tp / support, 4) if support else 0.0,
            "precision": round(tp / predicted_count, 4) if predicted_count else 0.0,
            "iou": round(tp / union, 4) if union else 0.0,
        }
    present = [v for v in per_class.values()]
    miou = sum(v["iou"] for v in present) / len(present) if present else 0.0
    return {
        "faces": total,
        "accuracy": round(correct / total, 4) if total else 0.0,
        "macro_iou": round(miou, 4),
        "per_class": per_class,
        "confusion": confusion.tolist(),
    }


def train(args):
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print("设备:", device, torch.cuda.get_device_name(0) if device.type == "cuda" else "")

    train_samples = load(args.train)
    val_samples = load(args.val)
    print("训练 %d 件 / %d 面，验证 %d 件" % (
        len(train_samples), sum(s["num_faces"] for s in train_samples), len(val_samples)
    ))

    counts = class_counts(train_samples)
    weights = M.class_weights(counts).to(device)
    covered = sum(1 for v in weights.tolist() if v > 0)
    print("训练集覆盖 %d/%d 类" % (covered, M.NUM_CLASSES))

    net = M.FeatureNet(hidden=args.hidden, layers=args.layers, dropout=args.dropout).to(device)
    if args.ablation == "node-only":
        # 消融：把所有边删掉，等价于逐面独立分类（无拓扑信息）
        for sample in train_samples:
            sample["edge_index"] = []
            sample["edge_attr"] = []
        for sample in val_samples:
            sample["edge_index"] = []
            sample["edge_attr"] = []
        print("消融模式：已移除全部邻接边（退化为逐面 MLP）")

    params = sum(p.numel() for p in net.parameters())
    print("参数量: %.2fM" % (params / 1e6))

    optimiser = torch.optim.AdamW(net.parameters(), lr=args.lr, weight_decay=args.wd)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimiser, T_max=args.epochs)

    history, best = [], {"accuracy": -1.0}
    started = time.perf_counter()

    for epoch in range(1, args.epochs + 1):
        net.train()
        order = torch.randperm(len(train_samples)).tolist()
        total_loss, seen, correct, examples = 0.0, 0, 0, 0

        for start in range(0, len(order), args.batch_size):
            chunk = [
                tensors(train_samples[i], device)
                for i in order[start : start + args.batch_size]
            ]
            batch = collate(chunk, device)
            logits = net(batch)
            loss = F.cross_entropy(logits, batch["y"], weight=weights)

            optimiser.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(net.parameters(), 5.0)
            optimiser.step()

            total_loss += loss.item() * batch["y"].numel()
            seen += batch["y"].numel()
            correct += (logits.argmax(-1) == batch["y"]).sum().item()
            examples += 1

        scheduler.step()
        result = evaluate(net, val_samples, device, args.batch_size)
        train_accuracy = correct / max(seen, 1)
        history.append({
            "epoch": epoch,
            "loss": round(total_loss / max(seen, 1), 4),
            "train_accuracy": round(train_accuracy, 4),
            "val_accuracy": result["accuracy"],
            "val_macro_iou": result["macro_iou"],
        })
        print(
            "epoch %2d  loss %.4f  train %.4f  val %.4f  mIoU %.4f  (%.0fs)"
            % (
                epoch,
                total_loss / max(seen, 1),
                train_accuracy,
                result["accuracy"],
                result["macro_iou"],
                time.perf_counter() - started,
            )
        )

        if result["accuracy"] > best["accuracy"]:
            best = {
                "accuracy": result["accuracy"],
                "macro_iou": result["macro_iou"],
                "epoch": epoch,
                "state": {k: v.detach().cpu().clone() for k, v in net.state_dict().items()},
                "per_class": result["per_class"],
                "confusion": result["confusion"],
            }

    elapsed = time.perf_counter() - started
    net.load_state_dict(best["state"])
    summary = {
        "task": "MFCAD++ face-level machining feature recognition",
        "ablation": args.ablation,
        "device": str(device),
        "params_m": round(params / 1e6, 3),
        "hidden": args.hidden,
        "layers": args.layers,
        "train_parts": len(train_samples),
        "train_faces": sum(s["num_faces"] for s in train_samples),
        "val_parts": len(val_samples),
        "best_epoch": best["epoch"],
        "val_accuracy": best["accuracy"],
        "val_macro_iou": best["macro_iou"],
        "per_class": best["per_class"],
        "confusion": best["confusion"],
        "train_seconds": round(elapsed, 1),
        "history": history,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    torch.save(
        {"state_dict": best["state"], "config": {"hidden": args.hidden, "layers": args.layers}},
        out.with_suffix(".pt"),
    )
    print()
    print("最佳 epoch %d：val 面级准确率 %.4f，mIoU %.4f" % (
        best["epoch"], best["accuracy"], best["macro_iou"]
    ))
    print("权重 →", out.with_suffix(".pt"))
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", default=str(ROOT / "data/mfcad/aag/train.pkl"))
    parser.add_argument("--val", default=str(ROOT / "data/mfcad/aag/val.pkl"))
    parser.add_argument("--out", default=str(ROOT / "reports/mfr_train.json"))
    parser.add_argument("--epochs", type=int, default=25)
    parser.add_argument("--batch-size", type=int, default=24)
    parser.add_argument("--lr", type=float, default=2e-3)
    parser.add_argument("--wd", type=float, default=1e-4)
    parser.add_argument("--hidden", type=int, default=128)
    parser.add_argument("--layers", type=int, default=3)
    parser.add_argument("--dropout", type=float, default=0.2)
    parser.add_argument("--ablation", default="full", choices=["full", "node-only"])
    args = parser.parse_args()
    train(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
