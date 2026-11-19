"""M2 验收：面标签对齐 → 合成集识别 → 真实件零样本 → 几何交叉校验 → 网关契约。

四段各自独立可判，任一失败都能定位到具体环节。
"""

import json
import pickle
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import torch  # noqa: E402

from cadcam.adapters import freecad  # noqa: E402
from cadcam.gateway import tools  # noqa: E402
from cadcam.gateway.contracts import ErrorCode, ToolError, validate_params  # noqa: E402
from cadcam.gateway.session import new_session  # noqa: E402
from cadcam.modalities.brep import step_labels  # noqa: E402
from cadcam.modalities.mfr.predict import Recognizer, render  # noqa: E402

ZIP = Path(r"C:\Users\Admin\Downloads\MFCAD_dataset.zip")
REPORT = ROOT / "reports" / "m2_acceptance.json"
results = []


def check(name, ok, detail=""):
    results.append({"item": name, "pass": bool(ok), "detail": str(detail)})
    print("  %-4s %s%s" % ("PASS" if ok else "FAIL", name, ("  — " + str(detail)) if detail else ""))
    return ok


def section(title):
    print()
    print("[%s]" % title)


def main():
    payload = {"kernel": freecad.engine_version()}

    # ---------------------------------------------------------------- A
    section("A. STEP 面序与内核读入顺序一致性")
    with zipfile.ZipFile(ZIP) as archive:
        names = sorted(
            n for n in archive.namelist()
            if n.startswith("MFCAD++_dataset/step/test/") and n.endswith(".step")
        )[:5]
        agree = 0
        for member in names:
            text = archive.read(member).decode("utf-8", "replace")
            faces = step_labels.parse_labels(text)
            tmp = ROOT / "build" / "m2_probe.step"
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(archive.read(member))
            geom = freecad.geometry(tmp, use_cache=False)
            if step_labels.surface_sequence(faces) == [f["surface"] for f in geom["faces"]]:
                agree += 1
        check("面顺序逐位一致", agree == len(names), "%d/%d" % (agree, len(names)))
        payload["alignment"] = {"checked": len(names), "agree": agree}

    # ---------------------------------------------------------------- B
    section("B. 合成集（MFCAD++ 留出 test）识别")
    with open(ROOT / "data/mfcad/aag/test.pkl", "rb") as handle:
        samples = pickle.load(handle)["samples"][:300]
    recogniser = Recognizer()
    correct = total = 0
    confidence = []
    for sample in samples:
        batch = {
            "x": torch.tensor(sample["x"], dtype=torch.float32, device=recogniser.device),
            "edge_index": torch.tensor(sample["edge_index"], dtype=torch.long, device=recogniser.device)
            .t().contiguous(),
            "edge_attr": torch.tensor(sample["edge_attr"], dtype=torch.float32, device=recogniser.device),
        }
        with torch.no_grad():
            logits = recogniser.net(batch)
            predicted = logits.argmax(-1).tolist()
            confidence.extend(torch.softmax(logits, -1).max(-1).values.tolist())
        correct += sum(1 for p, y in zip(predicted, sample["y"]) if p == y)
        total += len(sample["y"])
    accuracy = correct / total
    check("面级准确率 ≥ 0.95", accuracy >= 0.95, "%.4f（%d 面）" % (accuracy, total))
    check("平均置信度 ≥ 0.90", sum(confidence) / len(confidence) >= 0.90,
          "%.4f" % (sum(confidence) / len(confidence)))
    payload["synthetic"] = {"faces": total, "accuracy": round(accuracy, 4)}

    # ---------------------------------------------------------------- C
    section("C. 真实件零样本（Zenodo 锚点集，未参与训练）")
    real = {}
    for index in (9, 10):
        geom = freecad.geometry(ROOT / "data/zenodo_rptu" / ("%d.step" % index))
        result = recogniser.predict(geom)
        real[index] = {
            "text": result["summary"]["text"],
            "mean_confidence": result["mean_confidence"],
            "conflicts": len(result["conflicts"]),
        }
        print("    %d.step: %s" % (index, result["summary"]["text"]))
        print("      平均置信度 %.3f，几何校验纠正 %d 处" % (
            result["mean_confidence"], len(result["conflicts"])))
    # 9 号件的 4 个 R5 是外轮廓圆角，模型会判成孔，几何必须纠回来
    check("几何校验纠正圆角误判", real[9]["conflicts"] >= 4,
          "9.step 冲突 %d 处" % real[9]["conflicts"])
    check("纠正后不再出现纯 hole 的 Ø10", "hole ×6（Ø10）" not in real[9]["text"],
          real[9]["text"])
    payload["real_parts"] = real

    # ---------------------------------------------------------------- D
    section("D. 网关工具契约")
    session = new_session(ROOT / "build" / "m2_session")
    session.geometry = freecad.geometry(ROOT / "data/zenodo_rptu" / "9.step")
    spec = tools.get_spec("feature.recognize")
    result = session.commit(spec, validate_params(spec, {}))
    check("工具可调用并返回实例", result["instance_count"] > 0,
          "%d 个实例" % result["instance_count"])
    check("返回包含冲突记录", len(result["conflicts"]) >= 4, "%d 处" % len(result["conflicts"]))

    try:
        validate_params(spec, {"min_confidence": 1.5})
        check("越界参数被拒绝", False, "未拦截")
    except ToolError as exc:
        check("越界参数被拒绝", exc.code == ErrorCode.PARAM_INVALID, exc.code)

    try:
        validate_params(spec, {"depth": 5})
        check("未知参数被拒绝", False, "未拦截")
    except ToolError as exc:
        check("未知参数被拒绝", exc.code == ErrorCode.PARAM_INVALID, exc.code)

    check("工具总数 10", len(tools.TOOLS) == 10, len(tools.TOOLS))

    # ----------------------------------------------------------------
    passed = sum(1 for item in results if item["pass"])
    payload["checks"] = results
    payload["summary"] = {"passed": passed, "total": len(results)}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print("结果 %d/%d 通过" % (passed, len(results)))
    print("报告 %s" % REPORT)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
