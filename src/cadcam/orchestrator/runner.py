"""M4 编排循环：把六个角色按状态机串起来，带界重试与记忆热启动。

一次 run_task 的固定骨架：

    ANALYZE → PLAN → EMIT → VERIFY ─┬→ ACCEPT
                                    └→ REPAIR → EMIT → VERIFY ...（≤ max_repair 轮）
                                                 └→ ESCALATE（界耗尽）

编排队列里没有「自由对话」：每个阶段谁上场、能读什么、能写什么是写死的，
这样 trace 可以逐行复现，也能直接拿去做消融（去掉记忆 / 去掉修复）。
"""
from __future__ import annotations

from cadcam.orchestrator.blackboard import Blackboard
from cadcam.orchestrator.memory import EpisodicMemory, failure_signature
from cadcam.orchestrator.roles import (Analyst, Critic, Emitter, Planner,
                                       Repairer, Verifier)
from cadcam.orchestrator.state import StateMachine

DEFAULT_MAX_REPAIR = 3


def run_task(task, memory=None, max_repair=DEFAULT_MAX_REPAIR,
             planner=None, repairer=None):
    """跑一个任务，返回可落盘的记录。

    task 必填：part / family / geom / labels；可选 allowance（毛坯余量）。
    planner / repairer 可注入替身（M5 的 LLM 角色就是这样接进来的）；
    不传就是确定性策略，M4 的行为与数字完全不变。
    """
    memory = memory if memory is not None else EpisodicMemory()
    bb = Blackboard(task)

    # 记忆热启动：跑之前就问「这类零件上次是怎么修好的」
    hint = memory.hint(task.get("family"))
    if hint and hint.get("strategy_hint"):
        task = dict(task)
        task["strategy_hint"] = hint["strategy_hint"]
        bb.task["strategy_hint"] = hint["strategy_hint"]
        bb.log("memory", "hint_hit", family=task.get("family"),
               strategy=hint["strategy_hint"])

    sm = StateMachine()
    planner = planner if planner is not None else Planner()
    repairer = repairer if repairer is not None else Repairer()
    roles = [Analyst(), planner, Emitter(), Verifier()]
    repairs = []

    sm.to("ANALYZE")
    roles[0].act(bb)
    sm.to("PLAN")
    roles[1].act(bb)
    sm.to("EMIT")
    roles[2].act(bb)
    sm.to("VERIFY")
    roles[3].act(bb)
    first = {"metrics": dict(bb.get("metrics", {})),
             "errors": list(bb.get("errors", []))}

    while (not bb.get("verdict")["pass"]) and len(repairs) < max_repair:
        sm.to("REPAIR")
        action = repairer.act(bb)
        repairs.append(action)
        bb.log("runner", "bounded_retry", round=len(repairs))
        if action.get("exhausted"):
            break
        sm.to("EMIT")
        roles[2].act(bb)
        sm.to("VERIFY")
        roles[3].act(bb)

    verdict = bb.get("verdict")
    sm.to("ACCEPT" if verdict["pass"] else "ESCALATE")
    final = Critic().act(bb)

    metrics = bb.get("metrics", {})
    # 签名用「首轮诊断」：那才是触发修复的失败形态，末轮诊断是被修好的结果
    sig = failure_signature(task, first["errors"])
    fix = {"strategy_hint": bb.get("strategy")} if verdict["pass"] else None
    memory.record(task.get("family"), fix, verdict["pass"], len(repairs),
                  signature=sig)

    return {
        "task": {"part": task.get("part"), "family": task.get("family"),
                 "allowance": task.get("allowance", 1.0),
                 "strategy_hint": task.get("strategy_hint")},
        "status": final["status"],
        "pass": bool(verdict["pass"]),
        "rounds": len(repairs),
        "strategies": bb.get("strategy"),
        "metrics": metrics,
        "first_metrics": first["metrics"],
        "repairs": repairs,
        "signature": sig,
        "phases": sm.history,
        "events": len(bb.events),
        "blackboard_digest": bb.digest(),
    }
