"""B7 补充：LLM 自由生成 G-code 的「验证器投影」乐观上界，结果与口径自检。

纯 Python、零 LLM 调用，不需要 ollama、云端凭据或 opencamlib。
"""
import json
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "src"))
REP = os.path.join(ROOT, "reports", "m5_autonomy_probe.json")


def test_report_contract_and_conclusion():
    """投影能压过切，但救不回精度线：失效主因是覆盖规划，不是下刀深度。"""
    if not os.path.isfile(REP):
        pytest.skip("先跑 python scripts/m5_autonomy_probe.py")
    with open(REP, encoding="utf-8") as fh:
        r = json.load(fh)
    s = r["summary"]
    assert s["n"] == 10 and len(r["parts"]) == 10
    assert s["raw_dual_line_pass"] == 0
    assert s["projected_dual_line_pass"] == 0
    assert s["mean_max_gouge_after_mm"] < s["mean_max_gouge_before_mm"]
    assert s["mean_residual_after_mm3"] > s["mean_residual_before_mm3"]
    assert s["parts_with_zero_gouge_after"] >= 1
    # 口径纪律：不得被读成「模型可以自主生成 G-code」
    assert "不是 LLM 自主生成成功" in r["rule"]
    for row in r["parts"]:
        assert row["after_projection"]["mean_deviation_mm"] > 0.5


def test_project_only_raises_z_preserves_xy_and_paths():
    """投影只抬高 z、不改 XY、不新增刀路；同一 XY 的端点取统一安全高度。"""
    from m5_autonomy_probe import project

    ir = {"segments": [
        {"from": [0.0, 0.0, 1.0], "to": [5.0, 0.0, 1.0]},
        {"from": [5.0, 0.0, 1.0], "to": [5.0, 5.0, 1.0]},
    ]}
    target = {"xs": np.array([0.0, 5.0, 10.0]),
              "ys": np.array([0.0, 5.0, 10.0]),
              "top": np.array([[5.0, 7.0, np.inf],
                               [6.0, 8.0, np.inf],
                               [np.inf, np.inf, np.inf]])}
    out, raised, total = project(ir, target)
    assert len(out["segments"]) == len(ir["segments"])
    assert (raised, total) == (4, 4)
    for a, b in zip(ir["segments"], out["segments"]):
        for key in ("from", "to"):
            assert a[key][:2] == b[key][:2]        # XY 不变
            assert b[key][2] >= a[key][2]          # 只抬高
    z = [[s["from"][2], s["to"][2]] for s in out["segments"]]
    assert z == [[5.0, 7.0], [7.0, 8.0]]
    assert ir["segments"][0]["from"][2] == 1.0    # 原 IR 未被就地改写


def test_paper_keeps_the_upper_bound_and_relocation_framing():
    """论文必须同时保留上界数字与「权限上移」口径，不能退回「降级」叙事。"""
    p = os.path.join(ROOT, "docs", "PAPER-DRAFT.md")
    with open(p, encoding="utf-8") as fh:
        t = fh.read()
    assert "## 0.2 自主性层级" in t
    assert "25 623 mm³" in t
    assert "relocated rather than reduced" in t or "relocates rather than removes" in t
    assert "降级为 bounded autonomy" not in t
