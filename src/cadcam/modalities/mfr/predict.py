"""加载训练好的识别器，对任意 STEP 做特征识别并聚成实例。

对上层只暴露一个方法 ``predict(geom)``，返回结构化结果 —— 这样它既能
被 CLI 用，也能直接被网关的工具契约包装，不必知道模型细节。
"""

import json
from pathlib import Path

import torch

from cadcam.modalities.brep.step_labels import FEATURE_FAMILY, MFCAD_LABELS
from cadcam.modalities.mfr import aag, instances, verify
from cadcam.modalities.mfr import model as M

DEFAULT_WEIGHTS = Path(__file__).resolve().parents[4] / "data" / "mfcad" / "mfr.pt"


class Recognizer:
    def __init__(self, weights=None, device=None):
        weights = Path(weights) if weights else DEFAULT_WEIGHTS
        if not weights.exists():
            raise FileNotFoundError("找不到识别器权重：%s" % weights)
        payload = torch.load(weights, map_location="cpu", weights_only=False)
        config = payload.get("config", {})
        self.net = M.FeatureNet(
            hidden=config.get("hidden", 128),
            layers=config.get("layers", 3),
        )
        self.net.load_state_dict(payload["state_dict"])
        self.net.eval()
        self.device = torch.device(
            device or ("cuda" if torch.cuda.is_available() else "cpu")
        )
        self.net.to(self.device)
        self.weights = str(weights)
        self.meta = {k: v for k, v in payload.items() if k != "state_dict"}

    @torch.no_grad()
    def labels(self, geom):
        """返回 (每面类别 id, 每面置信度)，顺序与 geom["faces"] 一致。"""
        sample = aag.build(geom)
        if sample is None:
            return [], []
        batch = {
            "x": torch.tensor(sample["x"], dtype=torch.float32, device=self.device),
            "edge_index": torch.tensor(
                sample["edge_index"], dtype=torch.long, device=self.device
            )
            .t()
            .contiguous(),
            "edge_attr": torch.tensor(
                sample["edge_attr"], dtype=torch.float32, device=self.device
            ),
        }
        logits = self.net(batch)
        probability = torch.softmax(logits, dim=-1)
        confidence, predicted = probability.max(dim=-1)
        return predicted.tolist(), [round(v, 4) for v in confidence.tolist()]

    def predict(self, geom, min_confidence=0.0):
        labels, confidence = self.labels(geom)
        if not labels:
            return {"instances": [], "summary": {"count": 0, "text": "无面"}}

        families = {index: FEATURE_FAMILY.get(MFCAD_LABELS[index], "unknown")
                    for index in range(len(MFCAD_LABELS))}
        found = instances.group(geom, labels, families=families)
        # 把每面的最低置信度写进实例，方便上层决定要不要下钻
        for instance in found:
            scores = [confidence[i - 1] for i in instance["faces"] if 0 < i <= len(confidence)]
            instance["min_confidence"] = min(scores) if scores else None

        # 关键一步：不信任模型自报的置信度，用几何判据复核
        found, conflicts = verify.cross_check(geom, found)

        low = [index + 1 for index, value in enumerate(confidence)
               if value < min_confidence]
        low_share = len(low) / max(len(confidence), 1)
        return {
            "instances": found,
            "summary": instances.summarize(found),
            "faces": len(labels),
            "mean_confidence": round(sum(confidence) / len(confidence), 4),
            "low_confidence_share": round(low_share, 4),
            "low_confidence_faces": low,
            "conflicts": conflicts,
            "recogniser": Path(self.weights).name,
        }


def render(result, limit=12):
    """给人看的一行摘要，也给 LLM 用（比 JSON 省 token）。"""
    summary = result.get("summary", {})
    lines = [summary.get("text", "")]
    for instance in result.get("instances", [])[:limit]:
        box = instance.get("bbox") or {}
        size = box.get("size") or [0, 0, 0]
        size_text = "×".join("%g" % value for value in size)
        diameter = instance.get("diameter_mm")
        head = "%s×%d" % (instance["family"], instance["face_count"])
        extra = " Ø%g" % diameter if diameter else ""
        lines.append("- %s%s 包围盒 %s mm 面积 %g mm²" % (
            head, extra, size_text, instance["area_mm2"]
        ))
    extra = len(result.get("instances", [])) - limit
    if extra > 0:
        lines.append("… 另有 %d 个特征" % extra)
    return "\n".join(line for line in lines if line)


if __name__ == "__main__":
    import sys

    from cadcam.adapters import freecad

    path = sys.argv[1]
    geom = freecad.geometry(path)
    result = Recognizer().predict(geom, min_confidence=0.5)
    print(render(result, limit=20))
    print()
    print(json.dumps(result["summary"], ensure_ascii=False))
    print()
    print(verify.report(result.get("conflicts", [])))
