"""标注一致性校验 + 与 G 代码工序的交叉对照。

两类检查分开看：

A. **数据自洽**（硬校验）：每个面有且只有一个归属、面号在范围内、家族合法。
B. **跨模态对照**（软校验）：设计侧的特征家族 vs 制造侧的工序家族。
   两者不是 1:1 —— 件 2 的 4 个 Ø10 盲孔在 G 代码里是 ``2D-TASCHE``（Ø6 刀螺旋铣孔），
   因为 10 > 6，钻不了只能铣。所以这里给的是"是否有合理解释"，
   而不是"名字是否相同"。对不上且解释不了的，才值得人去查。
"""

import json
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LABELS = os.path.join(ROOT, "data", "zenodo_rptu", "labels")
GEOM = os.path.join(ROOT, "reports", "annotation", "geom")
BASELINE = os.path.join(ROOT, "reports", "m0_baseline.json")

FAMILIES = {"stock", "hole", "pocket", "slot", "step", "boss", "fillet", "chamfer",
            "freeform", "unknown"}

OP_FAMILY = [
    ("PLANEN", "facing"),
    ("NUT", "slot"),
    ("TASCHE", "cavity"),
    ("ADAPTIVE", "cavity"),
    ("KONTUR", "contour"),
    ("BOHREN", "drill"),
]

# 设计侧家族 -> 可能对应的工序家族（涵盖"为什么名字不一样也说得通"）
PLAUSIBLE = {
    "pocket": {"cavity"},
    "slot": {"cavity", "slot"},
    "step": {"cavity", "contour", "facing"},
    "boss": {"cavity", "contour"},
    "freeform": {"contour", "cavity"},
    "hole": {"drill", "cavity"},   # Ø > 刀具直径时用螺旋铣，落在 cavity
    "fillet": {"contour", "cavity"},
    "chamfer": {"contour", "cavity"},
}


def op_family(label):
    for key, family in OP_FAMILY:
        if key in label.upper():
            return family
    return "unknown"


def check_part(part):
    with open(os.path.join(LABELS, "%s.json" % part), encoding="utf-8") as fh:
        ann = json.load(fh)
    with open(os.path.join(GEOM, "%s.json" % part), encoding="utf-8") as fh:
        geom = json.load(fh)
    problems = []
    n = len(geom["faces"])
    if ann["n_faces"] != n:
        problems.append("n_faces 与几何不符: %s vs %s" % (ann["n_faces"], n))

    owner = Counter()
    for index, face in ann["faces"].items():
        owner[int(index)] += 1
        if face["family"] not in FAMILIES:
            problems.append("面 %s 家族非法 %s" % (index, face["family"]))
        if face["feature"] is not None:
            if face["feature"] not in {f["id"] for f in ann["features"]}:
                problems.append("面 %s 指向不存在的特征 %s" % (index, face["feature"]))
    for index in range(1, n + 1):
        if owner[index] == 0:
            problems.append("面 %d 无归属" % index)
        elif owner[index] > 1:
            problems.append("面 %d 重复归属" % index)

    # 特征表必须与面表一致
    listed = {i: f["id"] for f in ann["features"] for i in f["faces"]}
    for index, fid in listed.items():
        if ann["faces"].get(str(index), {}).get("feature") != fid:
            problems.append("面 %d 在特征表里属 %s，在面表里却不是" % (index, fid))
    return ann, problems


def main(argv):
    baseline = {}
    if os.path.exists(BASELINE):
        with open(BASELINE, encoding="utf-8") as fh:
            for row in json.load(fh)["parts"]:
                baseline[row["part"]] = row["gcode"]

    rows, problems_all = [], []
    family_faces = Counter()
    class_faces = Counter()
    for part in [str(i) for i in range(1, 11)]:
        ann, problems = check_part(part)
        fam = Counter(f["family"] for f in ann["features"])
        for face in ann["faces"].values():
            family_faces[face["family"]] += 1
            class_faces[face["mfcad_class"] or "(none)"] += 1
        ops = baseline.get(part, {}).get("op_labels", [])
        op_fams = Counter(op_family(x) for x in ops)
        unexplained = {}
        for family, count in fam.items():
            if family in ("stock",):
                continue
            if not (PLAUSIBLE.get(family, set()) & set(op_fams)):
                unexplained[family] = count
        rows.append({"part": part, "faces": ann["n_faces"], "features": len(ann["features"]),
                     "by_family": dict(fam), "ops": ops, "op_families": dict(op_fams),
                     "unexplained": unexplained, "review_needed": ann["review_needed"]})
        if problems:
            problems_all.append((part, problems))

    print("%-4s %-6s %-5s %-40s %s" % ("件", "面", "特征", "设计侧特征家族", "G 代码工序"))
    for row in rows:
        print("%-5s %-7d %-6d %-40s %s" % (row["part"], row["faces"], row["features"],
                                           row["by_family"], " | ".join(row["ops"])[:60]))
    print("\n面级家族分布:", dict(family_faces))
    print("MFCAD 类别: %d 类有具体类名，%d 面为 null（真实件超出 25 类体系）"
          % (len([k for k in class_faces if k != "(none)"]), class_faces.get("(none)", 0)))
    gaps = [(r["part"], r["unexplained"]) for r in rows if r["unexplained"]]
    print("无合理解释的跨模态缺口:", gaps or "无")

    if problems_all:
        print("\n!! 自洽性失败")
        for part, problems in problems_all:
            print(" ", part, problems[:8])
        return 1
    print("\n自洽性通过：%d 个面全部有且只有一个归属" % sum(r["faces"] for r in rows))

    out = os.path.join(ROOT, "reports", "annotation", "summary.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"parts": rows, "family_faces": dict(family_faces),
                   "class_faces": dict(class_faces)}, fh, ensure_ascii=False, indent=1)
    print("写出", os.path.relpath(out, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
