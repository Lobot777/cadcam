"""把 LLM 接到编排层的两个决策点：规划策略、修复动作。

**只给模型两个"语义决策"，不给它碰几何的自由**：
- Planner 只能选 `bbox` / `heightmap` 两个策略之一（以及切深 ap）；
- Repairer 只能从 `switch_heightmap` / `escalate` 里选。

这样即使模型幻觉，后果也是"选了个更慢但安全的策略"或"提前交人"，
而不是"把刀开进材料里"。所有失败（超时/非法 JSON/越界）自动降级到确定性策略，
并在记录里标 `fallback=True`——降级率本身就是我们要报的指标。
"""
from __future__ import annotations

import numpy as np

from cadcam.agents import prompts
from cadcam.agents.llm import LLMError
from cadcam.modalities.cam import process
from cadcam.orchestrator.roles import SAFE_STRATEGY, Planner, Repairer


def _levels(target):
    top = target["top"]
    finite = np.isfinite(top)
    return int(np.unique(np.round(top[finite], 3)).size)


def _instances_brief(bb):
    out = []
    for inst in bb.get("instances_obj", []):
        box = inst.get("bbox") or {}
        try:
            z0, z1 = float(box["min"][2]), float(box["max"][2])
        except (KeyError, IndexError, TypeError):
            z0 = z1 = 0.0
        out.append((inst.get("family"), len(inst.get("faces", [])), z0, z1))
    return out


class LLMPlanner(Planner):
    """LLM 选策略；选完照旧走 process.plan，几何仍由确定性代码生成。"""
    name = "planner"

    def __init__(self, client, records=None, cost_pressure=False):
        self.client = client
        self.records = records if records is not None else []
        self.cost_pressure = cost_pressure

    def act(self, bb):
        geom = bb.require("geom", self.name)
        instances = bb.require("instances_obj", self.name)
        target = bb.require("target", self.name)
        allowance = bb.task.get("allowance", 1.0)

        hint = bb.task.get("strategy_hint")
        source, decision, err = "memory", {"strategy": hint}, None
        if not hint:
            from cadcam.modalities.brep import digest
            summary = digest.summarize(geom)
            summary["levels"] = _levels(target)
            msgs = prompts.planner_messages(summary, geom,
                                            _instances_brief(bb), allowance,
                                            cost_pressure=self.cost_pressure)
            try:
                decision, resp = self.client.complete_json(
                    msgs, prompts.PLANNER_SCHEMA, max_tokens=200)
                source = "llm"
                self.records.append({"role": "planner", "ok": True, "fallback": False,
                                     "decision": decision, **resp.as_dict()})
            except LLMError as exc:
                decision = {"strategy": "bbox", "reason": "llm 失败，回落确定性默认"}
                source, err = "fallback", str(exc)[:200]
                self.records.append({"role": "planner", "ok": False, "fallback": True,
                                     "error": err, "decision": decision})

        strategy = decision["strategy"]
        ap = float(decision.get("ap_mm") or 4.0)
        plan = process.plan(geom, instances, allowance=allowance)
        bb.put("plan", plan, self.name)
        bb.put("strategy", strategy, self.name)
        bb.put("ap_mm", ap, self.name)
        bb.put("llm_plan", {"source": source, "decision": decision,
                            "error": err}, self.name)
        bb.log(self.name, "planned", ops=len(plan["ops"]), strategy=strategy,
               source=source, ap_mm=ap,
               reason=str(decision.get("reason", ""))[:80])
        return plan, strategy


class LLMRepairer(Repairer):
    """LLM 读错误、选修复动作。`channel="structured"` 给错误对象，`text` 给散文。"""
    name = "repairer"

    def __init__(self, client, channel="structured", records=None):
        self.client = client
        self.channel = channel
        self.records = records if records is not None else []

    def act(self, bb):
        strategy = bb.require("strategy", self.name)
        metrics = bb.get("metrics", {})
        errs = bb.get("errors", [])

        if strategy == SAFE_STRATEGY:
            return super().act(bb)          # 已在安全档，直接走"认输"分支

        if self.channel == "structured":
            msgs = prompts.repair_messages_structured(errs, metrics, strategy)
        elif self.channel == "vague":
            msgs = prompts.repair_messages_vague(metrics, strategy)
        else:
            msgs = prompts.repair_messages_text(metrics, strategy)

        try:
            decision, resp = self.client.complete_json(
                msgs, prompts.REPAIR_SCHEMA, max_tokens=120)
            self.records.append({"role": "repairer", "ok": True, "fallback": False,
                                 "channel": self.channel, "decision": decision,
                                 **resp.as_dict()})
        except LLMError as exc:
            decision = {"action": "switch_heightmap", "reason": "llm 失败，回落确定性修复"}
            self.records.append({"role": "repairer", "ok": False, "fallback": True,
                                 "channel": self.channel,
                                 "error": str(exc)[:200], "decision": decision})

        action = {"from": strategy, "to": SAFE_STRATEGY,
                  "reason": "llm:%s" % decision.get("reason", "")[:40],
                  "channel": self.channel,
                  "gouge_faces": len({f for e in errs if e.get("code") == "E_GOUGE"
                                      for f in (e.get("face_ids") or [])}),
                  "shifted_mm": metrics.get("max_gouge_depth_mm")}
        if decision["action"] == "escalate":
            action["exhausted"] = True
            bb.put("repair_action", action, self.name)
            bb.log(self.name, "repaired", **action)
            return action

        bb.put("strategy", SAFE_STRATEGY, self.name)
        bb.put("repair_action", action, self.name)
        bb.log(self.name, "repaired", **action)
        return action
