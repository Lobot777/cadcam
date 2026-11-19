"""用几何判据交叉校验模型的极性判断。

零样本跑真实零件时碰到的情况值得记下来：模型把 4 个外轮廓圆角判成
Ø10 孔，平均置信度 0.93，低置信度面为零 —— 它是**自信地错**。
这意味着「看置信度决定要不要复核」这条路走不通。

能纠它的只有几何本身：圆柱面到底是孔壁还是凸台，由法向与径向的
符号唯一确定（见 ``digest._cylinder_polarity``，M1 已验证）。
所以这里不复用模型，直接用几何判据复核，冲突时以几何为准并记录。

论文里这条对应 C3「验证接地的闭环」：不是让模型自证，而是用可独立
计算的物理量去否证它。
"""

from cadcam.modalities.brep import digest

# 这些族的几何前提是「凹圆柱」，若几何判为凸，则模型的判断必然错
CYLINDRICAL_FAMILIES = {"hole", "groove"}


def cross_check(geom, instances, labels=None):
    """返回 (校正后的实例, 冲突列表)。

    校正规则：
      1. 圆柱面类特征（hole/groove）：面法向背离轴线 → 不是孔，改判为凸台/圆角
      2. 判据无法给出结论（探针退化）时保留原判但标记 ``unverified``
    """
    faces = {face["index"]: face for face in geom.get("faces", [])}
    polarity = {}
    for index, face in faces.items():
        if face.get("surface") in ("Cylinder", "Cone"):
            polarity[index] = digest._radial_dot(face, geom.get("edges", ()))

    conflicts, corrected = [], []
    for instance in instances:
        copies = dict(instance)
        family = instance.get("family")
        votes = [polarity[index] for index in instance["faces"] if index in polarity]
        votes = [value for value in votes if value is not None and abs(value) > 0.05]

        if family in CYLINDRICAL_FAMILIES and votes:
            concave = sum(1 for value in votes if value < 0)
            convex = sum(1 for value in votes if value > 0)
            if convex > concave * 2:
                # 绝大多数面凸向外侧 —— 这是凸台或外轮廓圆角，不是孔
                copies["family"] = "boss_or_fillet"
                copies["action"] = None
                copies["corrected_from"] = family
                copies["correction"] = "几何判据：法向背离轴线（凸），不构成孔"
                conflicts.append({
                    "faces": instance["faces"],
                    "model_family": family,
                    "geometry_says": "convex",
                    "action": "override",
                })
            elif concave == 0 and convex == 0:
                copies["unverified"] = True
        corrected.append(copies)

    return corrected, conflicts


def report(conflicts):
    if not conflicts:
        return "无冲突"
    lines = ["%d 处模型与几何判据冲突：" % len(conflicts)]
    for item in conflicts[:6]:
        lines.append(
            "  面 %s 模型判 %s，几何判 %s → 已按几何修正"
            % (item["faces"], item["model_family"], item["geometry_says"])
        )
    return "\n".join(lines)
