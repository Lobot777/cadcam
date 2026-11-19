"""M1 验收：建块 → 挖腔 → 快照 → 钻孔 → 回滚，并验证契约能拦住错误调用。

验收标准（可机械检查）：
  A. 正向链路每一步都成功，体积按预期下降；
  B. 回滚后几何指纹与快照点完全一致；
  C. 四类非法调用分别被对应错误码拒绝，且失败的调用不改动几何。

    python scripts/m1_demo.py
"""

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cadcam.gateway import tools  # noqa: E402
from cadcam.gateway.contracts import ErrorCode, ToolError, validate_params  # noqa: E402
from cadcam.gateway.session import new_session  # noqa: E402

REPORT = ROOT / "reports" / "m1_acceptance.json"
WORKDIR = ROOT / "build" / "m1_session"

checks = []


def check(name, ok, detail=""):
    checks.append({"check": name, "ok": bool(ok), "detail": detail})
    print("  %s %s%s" % ("PASS" if ok else "FAIL", name, (" — " + detail) if detail else ""))
    return ok


def call(session, name, **kwargs):
    spec = tools.get_spec(name)
    clean = validate_params(spec, kwargs)
    return session.commit(spec, clean)


def expect_error(session, code, name, **kwargs):
    """调用应被拒绝，且必须返回指定错误码、且不改动几何。"""
    before = session.state().get("state_hash")
    try:
        call(session, name, **kwargs)
    except ToolError as error:
        after = session.state().get("state_hash")
        ok = error.code is code and before == after
        check("拒绝 %s（%s）" % (name, code.value), ok,
              "%s | 状态未变=%s" % (error.message[:60], before == after))
        return error
    check("拒绝 %s（%s）" % (name, code.value), False, "未被拒绝")
    return None


def main():
    if WORKDIR.exists():
        shutil.rmtree(WORKDIR)
    session = new_session(WORKDIR)

    print("\n[A] 正向链路：建块 → 挖腔 → 快照 → 钻孔 → 回滚")
    box = call(session, "cad.create_box", length=100, width=80, height=40)
    check("建毛坯 100×80×40，体积 320000",
          abs(box["volume_mm3"] - 320000) < 1,
          "volume=%.0f" % box["volume_mm3"])

    pocket = call(session, "cad.pocket", center_x=50, center_y=40,
                  width=40, height=30, depth=12)
    drop = box["volume_mm3"] - pocket["volume_mm3"]
    check("挖腔 40×30×12，切除 14400",
          abs(drop - 14400) < 14400 * 0.02,
          "切除=%.0f（偏差 %.2f%%）" % (drop, abs(drop - 14400) / 14400 * 100))

    snap = call(session, "cad.snapshot", label="before-drill")
    snapshot_hash = snap["snapshot"]["state_hash"]
    check("快照打点成功", bool(snapshot_hash), "hash=%s" % snapshot_hash)

    # 孔打在型腔之外的材料上（型腔占 x∈[30,70], y∈[25,55]）
    hole = call(session, "cad.hole", x=15, y=15, diameter=10, depth=10)
    hole_drop = pocket["volume_mm3"] - hole["volume_mm3"]
    expected_hole = 3.141592653589793 * 25 * 10
    check("钻孔 Ø10×10，切除 %.0f" % expected_hole,
          abs(hole_drop - expected_hole) < expected_hole * 0.02,
          "切除=%.0f" % hole_drop)

    back = call(session, "cad.rollback", snapshot_id="before-drill")
    check("回滚后指纹与快照点一致",
          back["state"]["state_hash"] == snapshot_hash,
          "%s vs %s" % (back["state"]["state_hash"], snapshot_hash))
    check("回滚后体积回到挖腔后",
          abs(session.state()["volume_mm3"] - pocket["volume_mm3"]) < 1,
          "volume=%.0f" % session.state()["volume_mm3"])

    print("\n[B] 契约拦截：错误的调用必须被拒绝，且不留脏状态")
    expect_error(session, ErrorCode.PRECONDITION, "cad.pocket",
                 center_x=50, center_y=40, width=40, height=30, depth=45)
    expect_error(session, ErrorCode.PRECONDITION, "cad.pocket",
                 center_x=10, center_y=40, width=40, height=30, depth=5)
    expect_error(session, ErrorCode.PARAM_INVALID, "cad.pocket",
                 center_x=50, center_y=40, width=40, height=30, depth=-3)
    expect_error(session, ErrorCode.PARAM_INVALID, "cad.pocket",
                 center_x=50, center_y=40, width=40, height=30, depth=5, speed=100)
    expect_error(session, ErrorCode.UNKNOWN_TOOL, "cad.thread", diameter=8)
    expect_error(session, ErrorCode.POSTCONDITION, "cad.pocket",
                 center_x=50, center_y=40, width=40, height=30, depth=12)
    # 孔打在已挖空的型腔里：布尔运算"成功"但没切到任何材料
    expect_error(session, ErrorCode.POSTCONDITION, "cad.hole",
                 x=50, y=40, diameter=10, depth=5)

    passed = sum(1 for item in checks if item["ok"])
    print("\n结果 %d/%d 通过" % (passed, len(checks)))

    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(json.dumps({
        "session": str(WORKDIR),
        "checks": checks,
        "passed": passed,
        "total": len(checks),
        "stats": session.stats,
        "history": session.history,
        "snapshots": [s.to_dict() for s in session.snapshots],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("报告 %s" % REPORT)
    return 0 if passed == len(checks) else 2


if __name__ == "__main__":
    sys.exit(main())
