"""对照基线：把 AAG 压平成固定长度向量，用梯度提升树做逐面分类。

为什么要这个对照：如果 GNN 只比「手工特征 + 树模型」高 1 个百分点，
那么论文里"架构贡献"的说法就站不住，真正的贡献是特征工程。
反过来，如果差距明显，才能说消息传递学到了邻域之外的东西。
这两组数必须同时出现在表里。
"""

import argparse
import json
import pickle
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402
from sklearn.ensemble import HistGradientBoostingClassifier  # noqa: E402

from cadcam.modalities.brep.step_labels import MFCAD_LABELS  # noqa: E402


def flatten(samples):
    """节点特征 ‖ 邻居节点特征(mean/max/min) ‖ 邻居边特征(mean/max/min)。"""
    rows, labels = [], []
    for sample in samples:
        x = np.asarray(sample["x"], dtype=np.float32)
        count = x.shape[0]
        edges = sample["edge_index"]
        edge_attr = np.asarray(sample["edge_attr"], dtype=np.float32).reshape(-1, 4)

        neighbour_sum = np.zeros_like(x)
        neighbour_max = np.full_like(x, -1e9)
        neighbour_min = np.full_like(x, 1e9)
        degree = np.zeros(count, dtype=np.float32)
        edge_sum = np.zeros((count, 4), dtype=np.float32)
        edge_max = np.full((count, 4), -1e9, dtype=np.float32)
        edge_min = np.full((count, 4), 1e9, dtype=np.float32)

        for k, (u, v) in enumerate(edges):
            # edge (u,v) 表示 v 是 u 的邻居
            neighbour_sum[u] += x[v]
            np.maximum(neighbour_max[u], x[v], out=neighbour_max[u])
            np.minimum(neighbour_min[u], x[v], out=neighbour_min[u])
            degree[u] += 1
            edge_sum[u] += edge_attr[k]
            np.maximum(edge_max[u], edge_attr[k], out=edge_max[u])
            np.minimum(edge_min[u], edge_attr[k], out=edge_min[u])

        safe = np.maximum(degree, 1.0)[:, None]
        neighbour_mean = neighbour_sum / safe
        edge_mean = edge_sum / safe
        neighbour_max = np.where(degree[:, None] > 0, neighbour_max, 0.0)
        neighbour_min = np.where(degree[:, None] > 0, neighbour_min, 0.0)
        edge_max = np.where(degree[:, None] > 0, edge_max, 0.0)
        edge_min = np.where(degree[:, None] > 0, edge_min, 0.0)

        block = np.concatenate(
            [
                x,
                neighbour_mean,
                neighbour_max,
                neighbour_min,
                edge_mean,
                edge_max,
                edge_min,
                degree[:, None],
            ],
            axis=1,
        )
        rows.append(block)
        labels.append(np.asarray(sample["y"], dtype=np.int64))
    return np.concatenate(rows), np.concatenate(labels)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", default=str(ROOT / "data/mfcad/aag/train.pkl"))
    parser.add_argument("--val", default=str(ROOT / "data/mfcad/aag/val.pkl"))
    parser.add_argument("--test", default=str(ROOT / "data/mfcad/aag/test.pkl"))
    parser.add_argument("--out", default=str(ROOT / "reports/mfr_baseline.json"))
    parser.add_argument("--max-iter", type=int, default=200)
    args = parser.parse_args()

    def load(path):
        with open(path, "rb") as handle:
            return pickle.load(handle)["samples"]

    train_samples, val_samples = load(args.train), load(args.val)
    x_train, y_train = flatten(train_samples)
    x_val, y_val = flatten(val_samples)
    print("训练 %s，验证 %s" % (x_train.shape, x_val.shape))

    clf = HistGradientBoostingClassifier(
        max_iter=args.max_iter,
        learning_rate=0.1,
        max_leaf_nodes=63,
        early_stopping=True,
        validation_fraction=0.1,
        random_state=0,
        verbose=0,
    )
    clf.fit(x_train, y_train)

    def score(x, y, tag):
        prediction = clf.predict(x)
        per_class = {}
        for index in range(25):
            support = int((y == index).sum())
            predicted = int((prediction == index).sum())
            if support == 0 and predicted == 0:
                continue
            true_positive = int(((prediction == index) & (y == index)).sum())
            union = support + predicted - true_positive
            per_class[MFCAD_LABELS.get(index, str(index))] = {
                "support": support,
                "recall": round(true_positive / support, 4) if support else 0.0,
                "precision": round(true_positive / predicted, 4) if predicted else 0.0,
                "iou": round(true_positive / union, 4) if union else 0.0,
            }
        macro = sum(v["iou"] for v in per_class.values()) / max(len(per_class), 1)
        result = {
            "faces": int(y.shape[0]),
            "accuracy": round(float((prediction == y).mean()), 4),
            "macro_iou": round(macro, 4),
            "per_class": per_class,
        }
        print("%s：面级准确率 %.4f，mIoU %.4f" % (tag, result["accuracy"], macro))
        return result

    val_result = score(x_val, y_val, "验证集")
    test_result = None
    test_path = Path(args.test)
    if test_path.exists() and str(test_path) != str(args.val):
        x_test, y_test = flatten(load(args.test))
        # 关键：对照组必须在与 GNN 完全相同的 test 上评测，否则两组数不可比
        test_result = score(x_test, y_test, "测试集")

    summary = {
        "task": "MFCAD++ face-level machining feature recognition",
        "method": "HistGradientBoosting on flattened AAG (no message passing)",
        "feature_dim": int(x_train.shape[1]),
        "train_faces": int(x_train.shape[0]),
        "val_faces": int(x_val.shape[0]),
        "val_accuracy": val_result["accuracy"],
        "val_macro_iou": val_result["macro_iou"],
        "per_class": val_result["per_class"],
    }
    if test_result is not None:
        summary["test"] = test_result
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("→", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
