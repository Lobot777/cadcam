"""给 LLM 的提示词构造：只喂结构化摘要，不喂原始点云/三角面。

对应计划里的 C2（渐进式几何披露）：模型看到的是 L0 摘要 + 特征实例列表，
而不是 O(面数) 的面表。`m5_context_eval.py` 会实测两者的 token 差。
"""
from __future__ import annotations

STRATEGIES = {
    "bbox": "按包围盒下刀。程序短（约 80–400 行），机床时间省；"
            "但遇到同一 z 上有多个不连通区域（多级腔）会切到不该切的材料。",
    "heightmap": "按目标高度层做区域掩码，刀心只走「离所有更高材料都超过刀半径」的位置。"
                 "程序长（约 300–1600 行），几何上不会过切。",
}

PLANNER_SCHEMA = {
    "required": ["strategy", "reason"],
    "properties": {
        "strategy": {"type": "string", "enum": ["bbox", "heightmap"]},
        "reason": {"type": "string"},
        "ap_mm": {"type": "number", "minimum": 0.5, "maximum": 8.0},
    },
}

REPAIR_SCHEMA = {
    "required": ["action", "reason"],
    "properties": {
        "action": {"type": "string", "enum": ["switch_heightmap", "escalate"]},
        "reason": {"type": "string"},
    },
}

_SYS = ("你是 CNC 工艺规划助手。只输出一个 JSON 对象，不要解释、不要 Markdown。"
        " /no_think")


def l0_text(summary, geom):
    """L0 的一行式摘要：尺寸/体积/曲面构成。token 成本约 40–60。"""
    size = summary.get("size_mm") or [0, 0, 0]
    hist = summary.get("surface_histogram") or {}
    top = sorted(hist.items(), key=lambda kv: -kv[1])[:3]
    return ("零件 %s｜%d 面 / %d 边｜%.0f×%.0f×%.0f mm｜体积 %.0f mm³｜"
            "曲面 %s" % (geom.get("source", "?"), len(geom.get("faces", [])),
                        len(geom.get("edges", [])), size[0], size[1], size[2],
                        float(summary.get("volume_mm3") or 0.0),
                        "、".join("%s×%d" % (k, v) for k, v in top) or "未知"))


def planner_messages(summary, geom, instances, allowance, cost_pressure=False):
    """summary: digest.summarize() 的 L0 输出；instances: [(family, 面数, z0, z1)]"""
    lines = [l0_text(summary, geom),
             "毛坯顶面余量 %.1f mm" % allowance,
             "特征实例（共 %d 个）：" % len(instances)]
    for fam, nf, z0, z1 in instances:
        lines.append("  - %s，%d 个面，z ∈ [%.1f, %.1f]" % (fam, nf, z0, z1))
    levels = summary.get("levels")
    if levels:
        lines.append("目标高度层数：%s（层数越多，包围盒越容易切错）" % levels)

    cost = ("" if not cost_pressure else
            "\n成本口径：每 100 行 G-code 约 30 秒机床时间，heightmap 通常是 bbox 的 3–8 倍长。"
            "能不过切就不要用 heightmap。\n")
    body = ("可选策略：\n"
            "- bbox：%s\n- heightmap：%s\n%s\n"
            "任务：在「不过切」的前提下尽量省机床时间，选一个策略。\n"
            "输出 JSON：{\"strategy\": \"bbox\" 或 \"heightmap\", \"ap_mm\": 切深(0.5–8), "
            "\"reason\": \"20 字以内理由\"}\n\n%s"
            % (STRATEGIES["bbox"], STRATEGIES["heightmap"], cost, "\n".join(lines)))
    return [{"role": "system", "content": _SYS},
            {"role": "user", "content": body}]


def repair_messages_structured(errors, metrics, strategy):
    """结构化通道：直接把 E_GOUGE 对象摊给模型（C3 主张的实证条件）。"""
    import json
    gouge = [e for e in errors if e.get("code") == "E_GOUGE"]
    body = ("上一轮规划策略：%s\n仿真结果：最大过切 %.3f mm，平均偏差 %.3f mm，"
            "过切波及工序占比 %.2f\n结构化错误对象：\n%s\n\n"
            "可选动作：\n"
            "- switch_heightmap：改用区域掩码重新规划（程序变长，但不会过切）\n"
            "- escalate：停手，交人处理（不要在有可行修法时选它）\n"
            "输出 JSON：{\"action\": \"switch_heightmap\" 或 \"escalate\", \"reason\": \"20 字以内\"}"
            % (strategy, metrics.get("max_gouge_depth_mm", 0.0),
               metrics.get("mean_deviation_mm", 0.0),
               metrics.get("gouge_op_ratio", 0.0),
               json.dumps(gouge, ensure_ascii=False)[:1200]))
    return [{"role": "system", "content": _SYS}, {"role": "user", "content": body}]


def repair_messages_text(metrics, strategy):
    """散文通道：同样的信息，但抹掉字段名与结构——用来对照「结构化」到底值多少。"""
    body = ("上一轮用的是包围盒策略。仿真跑完发现：最深的地方比目标表面多切了 "
            "%.1f 毫米，平均偏差 %.2f 毫米，大约 %.0f%% 的工序都牵涉其中。\n\n"
            "可选动作：\n"
            "- switch_heightmap：改用区域掩码重新规划（程序变长，但不会过切）\n"
            "- escalate：停手，交人处理（不要在有可行修法时选它）\n"
            "输出 JSON：{\"action\": \"switch_heightmap\" 或 \"escalate\", \"reason\": \"20 字以内\"}"
            % (metrics.get("max_gouge_depth_mm", 0.0),
               metrics.get("mean_deviation_mm", 0.0),
               100.0 * float(metrics.get("gouge_op_ratio", 0.0) or 0.0)))
    return [{"role": "system", "content": _SYS}, {"role": "user", "content": body}]


def repair_messages_vague(metrics, strategy):
    """模糊通道：只说"不合格"，不给任何数值——检验模型是不是在"默认选安全"。"""
    body = ("上一轮用的是包围盒策略。仿真跑完判定为不合格，但本次不提供具体偏差数值。\n\n"
            "可选动作：\n"
            "- switch_heightmap：改用区域掩码重新规划（程序变长，但不会过切）\n"
            "- escalate：停手，交人处理（不要在有可行修法时选它）\n"
            "输出 JSON：{\"action\": \"switch_heightmap\" 或 \"escalate\", \"reason\": \"20 字以内\"}")
    return [{"role": "system", "content": _SYS}, {"role": "user", "content": body}]


def context_variants(geom, labels, digest_mod):
    """C2 的三档上下文：L0 摘要 / L0+特征实例 / 全量面表（L2 dump）。

    中间那档是**实例**级（2–5 条），不是逐面标签——这正是 C2 主张的
    「O(面数) → O(特征数)」。token 用字符数估，真实值由调用方从 API 的
    usage.prompt_tokens 取；估算只用于排序，不作为报告数字。
    """
    from cadcam.modalities.mfr.instances import group
    s = digest_mod.summarize(geom)
    l0 = l0_text(s, geom)
    fam = [labels.get("faces", {}).get(str(f.get("index")), {}).get("family", "?")
           for f in geom.get("faces", [])]
    inst = group(geom, fam, families={x: x for x in set(fam)})
    lines = [l0, "特征实例（%d 个）：" % len(inst)]
    for i in inst:
        box = i.get("bbox") or {}
        try:
            z0, z1 = float(box["min"][2]), float(box["max"][2])
        except (KeyError, IndexError, TypeError):
            z0 = z1 = 0.0
        lines.append("  - %s，%d 面，z ∈ [%.0f, %.0f]"
                     % (i.get("family"), len(i.get("faces", [])), z0, z1))
    l1 = "\n".join(lines)
    l2 = l0 + "\n全量面表：\n" + "\n".join(
        "面%d 类型=%s 面积=%.2f 法向=%s 邻接=%s"
        % (i, f.get("surface_type", "?"), f.get("area_mm2") or 0.0,
           f.get("normal"), f.get("neighbours"))
        for i, f in enumerate(geom.get("faces", []), start=1))
    return [("L0 摘要", l0), ("L0+特征实例", l1), ("全量面表 L2", l2)]
