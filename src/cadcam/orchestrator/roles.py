"""M4 的六个角色。

M4 阶段角色是**确定性策略**，不是 LLM：这样编排层（状态机/黑板/有界修复/记忆）
的正确性可以和模型能力解耦验证——先证明「壳子」对，M5 再换 LLM 进去。
每个角色只做一件事，只读黑板上游产物、只写自己的键，彼此不调用。

角色与产物：
    Analyst  → instances / faces / target      （几何理解）
    Planner  → plan / strategy                  （工艺规划）
    Emitter  → gcode / emit_meta                （后置处理）
    Verifier → metrics / errors                 （仿真 + DFM）
    Repairer → strategy / repair_action         （定向修复）
    Critic   → verdict_final / memory_entry     （终审与记忆写回）
"""
from __future__ import annotations

import numpy as np

from cadcam.modalities.cam import gcode_gen, heightmap, process
from cadcam.modalities.mfr.instances import group
from cadcam.verify import dexel, dfm

RES = 0.5                 # 与 M3 全部评估一致，口径不另起
ACCEPT_MM = 0.5           # ADR-001 双线阈值
SAFE_STRATEGY = "heightmap"
CHEAP_STRATEGY = "bbox"


def _radius_map(plan, meta, strategy):
    """按工序标签取刀径。不按工序取会把精刀当粗刀，残留虚高（M3.1 踩过）。"""
    if strategy == SAFE_STRATEGY:
        return {o["label"]: o["tool_d_mm"] / 2.0
                for o in meta.get("ops_detail", []) if not o.get("empty")}
    return {op["label"]: op["speeds"]["tool_d_mm"] / 2.0 for op in plan["ops"]}


class Role:
    name = "role"

    def act(self, bb):
        raise NotImplementedError


class Analyst(Role):
    """几何理解：面 → 实例 → 目标高度图。不碰工艺。"""
    name = "analyst"

    def act(self, bb):
        geom = bb.require("geom", self.name)
        labels = bb.require("labels", self.name)
        fam = [labels["faces"][str(f["index"])]["family"] for f in geom["faces"]]
        instances = group(geom, fam, families={x: x for x in set(fam)})
        target = dexel.rasterize(geom, res=RES)
        bb.put("instances", len(instances), self.name)
        bb.put("instances_obj", instances, self.name)
        bb.put("families", sorted({i.get("family") for i in instances}), self.name)
        bb.put("target", target, self.name)
        bb.log(self.name, "analyzed", faces=len(geom["faces"]),
               instances=len(instances),
               levels=int(np.unique(np.round(
                   target["top"][np.isfinite(target["top"])], 3)).size))
        return instances, target


class Planner(Role):
    """工艺规划。strategies: bbox（快、无区域掩码） / heightmap（慢、按高度层掩码）。"""
    name = "planner"

    def act(self, bb):
        geom = bb.require("geom", self.name)
        instances = bb.require("instances_obj", self.name)
        hint = bb.task.get("strategy_hint")
        strategy = bb.task.get("strategy", hint or CHEAP_STRATEGY)
        plan = process.plan(geom, instances,
                            allowance=bb.task.get("allowance", 1.0))
        bb.put("plan", plan, self.name)
        bb.put("strategy", strategy, self.name)
        bb.log(self.name, "planned", ops=len(plan["ops"]), strategy=strategy,
               allowance=bb.task.get("allowance", 1.0),
               hint=hint or None)
        return plan, strategy


class Emitter(Role):
    """后置处理：工艺计划 → 受限 G-code。"""
    name = "emitter"

    def act(self, bb):
        plan = bb.require("plan", self.name)
        strategy = bb.require("strategy", self.name)
        if strategy == SAFE_STRATEGY:
            target = bb.require("target", self.name)
            text, meta = heightmap.generate(plan, target, res=RES)
        else:
            text = gcode_gen.generate(plan)
            meta = {"ops_detail": [{"label": op["label"],
                                    "tool_d_mm": op["speeds"]["tool_d_mm"]}
                                   for op in plan["ops"]]}
        bb.put("gcode", text, self.name)
        bb.put("emit_meta", meta, self.name)
        bb.log(self.name, "emitted", strategy=strategy,
               lines=len(text.splitlines()),
               segments=gcode_gen.validate(text)["ir"]["stats"]["segments"])
        return text, meta


class Verifier(Role):
    """仿真 + DFM。产出结构化错误，供 Repairer 定向使用。"""
    name = "verifier"

    def act(self, bb):
        text = bb.require("gcode", self.name)
        plan = bb.require("plan", self.name)
        target = bb.require("target", self.name)
        strategy = bb.require("strategy", self.name)
        meta = bb.require("emit_meta", self.name)

        v = gcode_gen.validate(text, stock=plan["stock"])
        ir = v["ir"]
        # op_faces 必须给，否则 dexel 不做逐工序归因，Repair 拿不到「哪道工序切坏的」
        op_faces = {op["label"]: op.get("faces", []) for op in plan["ops"]}
        m = dexel.simulate(ir, target, plan["stock"]["top_z"],
                           tool_radius_mm=3.0, res=RES,
                           tool_radius_by_op=_radius_map(plan, meta, strategy),
                           op_faces=op_faces)
        goal = target["top"]
        mask = np.isfinite(goal)
        gouge = np.where(mask, np.maximum(0.0, goal - m["cur"]), 0.0)
        max_gouge = float(gouge.max())

        labels = {op["label"]: op["id"] for op in plan["ops"]}
        seg_ops = {seg.get("op") for seg in ir["segments"] if seg.get("op")}
        errs = dexel.errors(m, op_ids=labels)
        dfm_errs = dfm.check(plan)
        metrics = {
            "whitelist_ok": bool(v["ok"]),
            "whitelist_errors": v["errors"][:5],
            "segments": ir["stats"]["segments"],
            "max_gouge_depth_mm": round(max_gouge, 3),
            "mean_deviation_mm": round(m["mean_deviation_mm"], 3),
            "p99_deviation_mm": round(m["p99_deviation_mm"], 3),
            "gouge_volume_mm3": round(m["gouge_volume_mm3"], 1),
            "residual_volume_mm3": round(m["residual_volume_mm3"], 1),
            "gouge_op_ratio": round(_gouge_op_ratio(errs, len(seg_ops)), 3),
            "dfm_errors": len(dfm_errs),
        }
        # 双线口径（ADR-001）：安全线 + 精度线，缺一不可
        verdict = {
            "safety": metrics["max_gouge_depth_mm"] <= ACCEPT_MM,
            "accuracy": metrics["mean_deviation_mm"] <= ACCEPT_MM,
            "whitelist": metrics["whitelist_ok"],
        }
        verdict["pass"] = all(verdict.values())
        bb.put("metrics", metrics, self.name)
        bb.put("errors", errs + dfm_errs, self.name)
        bb.put("verdict", verdict, self.name)
        bb.log(self.name, "verified", **metrics)
        return metrics, errs + dfm_errs, verdict


def _gouge_op_ratio(errs, n_ops):
    """过切波及的工序占全部工序的比例——修复策略的分岔依据。

    按工序标签统计（bbox 与 heightmap 两套标签体系都能用）：
    波及面广说明是「整体空间覆盖错」，不是单道工序参数错。
    """
    hit = set()
    for e in errs:
        if e.get("code") != "E_GOUGE":
            continue
        for row in e.get("by_op", []) or []:
            if row.get("op"):
                hit.add(row["op"])
        if e.get("op_label"):
            hit.add(e["op_label"])
    return len(hit) / max(1, n_ops)


class Repairer(Role):
    """有界修复：只做一件事——把不可逆的过切风险换成可补刀的残留。

    bbox 计划在多级腔上会把面铣/型腔按包围盒最低点下刀，切到本不该切的材料。
    修法不是调参数，而是换**带区域掩码**的规划器：目标高度层 L 的刀心可行域 =
    对 {goal > L} 的柱按刀半径膨胀后取补，几何上保证刀心不进材料。
    """
    name = "repairer"

    def act(self, bb):
        strategy = bb.require("strategy", self.name)
        errs = bb.get("errors", [])
        metrics = bb.get("metrics", {})
        gouges = [e for e in errs if e.get("code") == "E_GOUGE"]
        faces = sorted({f for e in gouges for f in (e.get("face_ids") or [])})
        action = {
            "from": strategy,
            "to": SAFE_STRATEGY if strategy != SAFE_STRATEGY else strategy,
            "reason": ("gouge_ops_ratio=%.2f" % metrics.get("gouge_op_ratio", 0.0)),
            "gouge_faces": len(faces),
            "shifted_mm": metrics.get("max_gouge_depth_mm"),
        }
        if strategy == SAFE_STRATEGY:
            action["exhausted"] = True      # 安全规划器也过不去 → 只能上报
        else:
            bb.put("strategy", SAFE_STRATEGY, self.name)
        bb.put("repair_action", action, self.name)
        bb.log(self.name, "repaired", **action)
        return action


class Critic(Role):
    """终审：按 ADR-001 双线判，写回记忆，给出可上报的结构化结论。"""
    name = "critic"

    def act(self, bb, memory=None):
        verdict = bb.require("verdict", self.name)
        metrics = bb.require("metrics", self.name)
        strategy = bb.get("strategy")
        if verdict["pass"]:
            bb.put("verdict_final", {"status": "ACCEPT", "metrics": metrics,
                                     "strategy": strategy}, self.name)
            bb.log(self.name, "accepted", strategy=strategy)
        else:
            bb.put("verdict_final", {
                "status": "ESCALATE", "metrics": metrics, "strategy": strategy,
                "blocking": [k for k, ok in verdict.items()
                             if not ok and k != "pass"],
                "errors": [e for e in bb.get("errors", [])
                           if e.get("code") in ("E_GOUGE", "E_RESIDUAL")][:5],
            }, self.name)
            bb.log(self.name, "escalated",
                   blocking=[k for k, ok in verdict.items() if not ok])
        return bb.get("verdict_final")
