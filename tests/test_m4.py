"""M4 验收：状态机守门、黑板可追溯、有界修复、记忆热启动。"""

import json
import os

import pytest

from cadcam.orchestrator import (Blackboard, EpisodicMemory, IllegalTransition,
                                 StateMachine, run_task)
from cadcam.orchestrator.blackboard import _jsonable
from cadcam.orchestrator.memory import failure_signature

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEOM = os.path.join(ROOT, "reports", "annotation", "geom")
LABELS = os.path.join(ROOT, "data", "zenodo_rptu", "labels")


def _task(part="1", family="multi_level_pocket", allowance=1.0, **kw):
    with open(os.path.join(GEOM, "%s.json" % part), encoding="utf-8") as fh:
        geom = json.load(fh)
    with open(os.path.join(LABELS, "%s.json" % part), encoding="utf-8") as fh:
        labels = json.load(fh)
    t = {"part": part, "family": family, "geom": geom, "labels": labels,
         "allowance": allowance}
    t.update(kw)
    return t


# ---------- 状态机 ----------

def test_state_machine_rejects_phase_skipping():
    sm = StateMachine()
    with pytest.raises(IllegalTransition):
        sm.to("VERIFY")          # INIT 只能到 ANALYZE
    sm.to("ANALYZE")
    with pytest.raises(IllegalTransition):
        sm.to("EMIT")            # 必须先 PLAN
    assert sm.phase == "ANALYZE"


def test_state_machine_is_terminal_after_accept():
    sm = StateMachine()
    for p in ("ANALYZE", "PLAN", "EMIT", "VERIFY", "ACCEPT"):
        sm.to(p)
    assert sm.done()
    for p in ("REPAIR", "VERIFY", "ANALYZE"):
        with pytest.raises(IllegalTransition):
            sm.to(p)


# ---------- 黑板 ----------

def test_blackboard_missing_input_names_the_role():
    bb = Blackboard({"part": "1"})
    with pytest.raises(KeyError) as ei:
        bb.require("geom", "analyst")
    assert "analyst" in str(ei.value)


def test_blackboard_numpy_is_summarized_not_dumped():
    import numpy as np
    s = _jsonable({"a": np.arange(6.0).reshape(2, 3), "b": [1, 2]})
    assert s["a"]["__ndarray__"] == [2, 3] and s["a"]["max"] == 5.0
    json.dumps(s)          # 必须可序列化，否则 trace 落不了盘


# ---------- 有界修复 ----------

def test_first_pass_bbox_plan_gouges_and_repair_fixes_it():
    r = run_task(_task("1"), EpisodicMemory())
    assert r["first_metrics"]["max_gouge_depth_mm"] > 0.5, "首轮应当过切"
    assert r["pass"] and r["status"] == "ACCEPT"
    assert r["strategies"] == "heightmap"
    assert r["rounds"] >= 1
    # 状态机必须真的走过 REPAIR，而不是跳过直接 ACCEPT
    assert "REPAIR" in r["phases"]
    assert r["phases"][-1] == "ACCEPT"
    assert r["metrics"]["max_gouge_depth_mm"] <= 0.5
    assert r["metrics"]["mean_deviation_mm"] <= 0.5


def test_bounded_retry_escalates_without_repair_budget():
    r = run_task(_task("1"), EpisodicMemory(), max_repair=0)
    assert r["rounds"] == 0 and not r["pass"]
    assert r["status"] == "ESCALATE"
    assert r["phases"][-1] == "ESCALATE"


def test_escalation_payload_is_structured():
    from cadcam.orchestrator.blackboard import Blackboard
    from cadcam.orchestrator.roles import Critic
    bb = Blackboard({"task": 1})
    bb.put("verdict", {"safety": False, "accuracy": True, "whitelist": True,
                       "pass": False}, "verifier")
    bb.put("metrics", {"max_gouge_depth_mm": 9.9}, "verifier")
    bb.put("errors", [{"code": "E_GOUGE", "magnitude_mm3": 1000.0}], "verifier")
    out = Critic().act(bb)
    assert out["status"] == "ESCALATE"
    assert out["blocking"] == ["safety"]        # 指出是哪条线没过，不是「去改改」
    assert out["errors"][0]["code"] == "E_GOUGE"


# ---------- 记忆 ----------

def test_warm_memory_skips_the_repair_round():
    mem = EpisodicMemory()
    first = run_task(_task("1", family="fam_probe"), mem)
    assert first["rounds"] >= 1
    second = run_task(_task("1", family="fam_probe"), mem)
    assert second["pass"] and second["rounds"] == 0
    assert second["task"]["strategy_hint"] == "heightmap"


def test_memory_only_reuses_successful_fixes():
    mem = EpisodicMemory()
    mem.record("fam_x", {"strategy_hint": "heightmap"}, ok=False, rounds=3,
               signature="fam_x|E_GOUGE")
    assert mem.hint("fam_x") is None            # 失败经验不当建议
    mem.record("fam_x", {"strategy_hint": "heightmap"}, ok=True, rounds=1)
    assert mem.hint("fam_x")["strategy_hint"] == "heightmap"


def test_failure_signature_is_part_number_free():
    t1 = _task("1", family="fam_y")
    t2 = _task("2", family="fam_y")
    errs = [{"code": "E_GOUGE"}, {"code": "E_RESIDUAL"}, {"code": "E_GOUGE"}]
    assert failure_signature(t1, errs) == failure_signature(t2, errs)
    assert failure_signature(t1, []) == "fam_y|ok"


def test_run_is_deterministic():
    a = run_task(_task("2"), EpisodicMemory())
    b = run_task(_task("2"), EpisodicMemory())
    assert a["blackboard_digest"] == b["blackboard_digest"]
    assert a["metrics"] == b["metrics"]


# ---------- 报告一致性 ----------

def test_m4_report_arms_are_consistent():
    path = os.path.join(ROOT, "reports", "m4_run.json")
    if not os.path.exists(path):
        pytest.skip("m4_run.json 未生成")
    with open(path, encoding="utf-8") as fh:
        rep = json.load(fh)
    arms = {a["arm"]: a for a in rep["arms"]}
    a, b, c = (arms["A_no_repair"], arms["B_repair_cold"], arms["C_repair_warm"])
    assert a["n"] == b["n"] == c["n"] == 20
    assert a["pass_rate"] < 0.5, "不修复不该过半数，否则修复环节没在起作用"
    assert b["pass_rate"] >= 0.6, "M4 验收线：端到端成功率 ≥60%"
    assert c["mean_rounds"] < b["mean_rounds"], "热记忆应当减少修复轮次"


def test_repair_gives_up_when_the_safe_planner_also_fails(monkeypatch):
    """把「安全规划器」换成一个照样过切的实现：修复只有一档可升，
    升完仍不合格就必须在界内停手并上报，而不是无限重试。"""
    from cadcam.modalities.cam import gcode_gen
    from cadcam.orchestrator import roles

    def fake_generate(plan, target, res=0.5):
        text = gcode_gen.generate(plan)          # 故意返回无区域掩码的刀路
        meta = {"ops_detail": [{"label": op["label"],
                                "tool_d_mm": op["speeds"]["tool_d_mm"]}
                               for op in plan["ops"]]}
        return text, meta

    monkeypatch.setattr(roles.heightmap, "generate", fake_generate)
    r = run_task(_task("1"), EpisodicMemory(), max_repair=3)
    assert not r["pass"] and r["status"] == "ESCALATE"
    # 第 1 轮：换到「安全」规划器；第 2 轮：发现换完还是过切 → 认输
    assert r["rounds"] == 2 and r["rounds"] <= 3
    assert r["repairs"][0].get("exhausted") is None
    assert r["repairs"][-1]["exhausted"] is True
    assert r["phases"][-1] == "ESCALATE"
