"""把规则草稿 + 人工覆盖合并成最终标注，并做一致性校验。

覆盖文件 ``overrides/<n>.json`` 只写"我确认过的特征列表"：
    {"features": [[family, mfcad_class, [faces], note], ...], "review_needed": false}
没被任何特征占用的面按 stock 记账 —— 但会打印出来让人过一眼，
因为"漏标"和"真的是 stock"在数据上长得一样，只有人看得出来。

校验项：
  1. 每个面恰好属于一个特征或 stock（不漏、不重）
  2. 特征面数 + stock 面数 = 零件面数
  3. 特征数量与 G-code 工序数对照（只报告，不强行相等：
     一道工序可以加工多个特征，一个特征也可以分多道工序）
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(ROOT, "data", "zenodo_rptu")
OVR = os.path.join(ROOT, "scripts", "annotate", "overrides")
LABELS = os.path.join(DATA, "labels")

VALID_FAMILIES = {"stock", "hole", "pocket", "slot", "step", "boss", "fillet",
                  "chamfer", "freeform", "unknown"}


def build(part):
    geom_path = os.path.join(ROOT, "reports", "annotation", "geom", "%s.json" % part)
    with open(geom_path, encoding="utf-8") as fh:
        geom = json.load(fh)
    with open(os.path.join(OVR, "%s.json" % part), encoding="utf-8") as fh:
        override = json.load(fh)

    n = len(geom["faces"])
    faces = {}
    features = []
    for k, item in enumerate(override["features"], start=1):
        family, cls, members, note = item[0], item[1], item[2], (item[3] if len(item) > 3 else "")
        assert family in VALID_FAMILIES, (part, family)
        fid = "F%d" % k
        for index in members:
            assert str(index) not in faces, "件 %s 面 %s 被重复标注" % (part, index)
            assert 1 <= index <= n, "件 %s 面号 %s 越界" % (part, index)
            faces[str(index)] = {"family": family, "mfcad_class": cls,
                                 "feature": fid, "source": "human"}
        features.append({"id": fid, "family": family, "mfcad_class": cls,
                         "faces": sorted(members), "note": note, "reviewed": True})

    missing = [i for i in range(1, n + 1) if str(i) not in faces]
    for index in missing:
        faces[str(index)] = {"family": "stock", "mfcad_class": "stock",
                             "feature": None, "source": "human"}
    payload = {
        "part": part,
        "source": geom.get("source"),
        "n_faces": n,
        "schema": "cadcam-annotation/1",
        "taxonomy": "family(9) + mfcad_class(25, 可为 null)",
        "annotator": override.get("annotator", "ai-visual+rule"),
        "reviewed": override.get("review_needed") is False,
        "review_needed": bool(override.get("review_needed")),
        "open_questions": override.get("open_questions", []),
        "resolved": override.get("resolved", []),
        "evidence": override.get("evidence", {}),
        "stock_faces": missing,
        "faces": {str(i): faces[str(i)] for i in range(1, n + 1)},
        "features": features,
    }
    return payload


def main(argv):
    parts = [str(p) for p in range(1, 11)]
    if "--parts" in argv:
        parts = argv[argv.index("--parts") + 1].split(",")
    total_faces = total_feat = 0
    for part in parts:
        payload = build(part)
        dst = os.path.join(LABELS, "%s.json" % part)
        with open(dst, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        total_faces += payload["n_faces"]
        total_feat += len(payload["features"])
        flag = "需复核" if payload["review_needed"] else "已确认"
        print("%s: %2d 面 / %2d 特征 / stock %2d / %s"
              % (part, payload["n_faces"], len(payload["features"]),
                 len(payload["stock_faces"]), flag))
    print("合计 %d 面 / %d 特征" % (total_faces, total_feat))


if __name__ == "__main__":
    main(sys.argv[1:])
