"""M5 一键复现：把论文里每个数字重跑一遍，落成可引用的台账。

    PYTHONPATH=src python scripts/bench.py            # 全量（约 2 分钟）
    PYTHONPATH=src python scripts/bench.py --reuse    # 只汇总已有报告，不重跑
    PYTHONPATH=src python scripts/bench.py --only m3  # 跑某一步

产物：
    reports/bench.json  机器可读台账（每步的状态/耗时/关键数字）
    reports/bench.md    给人看的表（可直接贴附录）

设计原则：**重算的和存档的分开报**。M2 的训练要 GPU 小时，不能假装重跑；
它作为「存档产物」列出，并给出复现命令与代价说明。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REP = os.path.join(ROOT, "reports")
PY = sys.executable
sys.path.insert(0, os.path.join(ROOT, "src"))

from cadcam.verify.stats import wilson_table  # noqa: E402

# (key, 说明, 脚本, 产物)
STEPS = [
    ("m3_cam", "A 路 FreeCAD CAM 参考刀路复算", "scripts/m3_cam_eval.py",
     "m3_cam_eval.json"),
    ("m3_heightmap", "B1 包围盒 vs B2 区域掩码复算", "scripts/m3_heightmap.py",
     "m3_heightmap_eval.json"),
    ("m4", "M4 三组对照（20 个任务）", "scripts/m4_run.py", "m4_run.json"),
    ("m5_autonomy", "B7 补充：自由生成 G-code 的验证器投影乐观上界（零 LLM 调用）",
     "scripts/m5_autonomy_probe.py", "m5_autonomy_probe.json"),
]

ARCHIVED = [
    ("m2_recognition", "M2 GNN 面级识别（训练需 GPU，未在 bench 重跑；"
     "权重→指标已用 eval_mfr 复算，逐位一致）",
     "python scripts/eval_mfr.py --weights reports/mfr_train_v2.pt",
     "mfr_test_v2.json"),
    ("m1_gateway", "M1 MCP 网关验收（需 FreeCAD 二进制）", "python scripts/m1_demo.py",
     "m1_acceptance.json"),
    ("m3_acceptance", "M3.1 工艺库/注入自检", "python scripts/m3_demo.py",
     "m3_acceptance.json"),
    ("m6_external", "MFCAD 130 件外部验证集（双线判据）",
     "PYTHONPATH=src python scripts/external_eval.py", "m3_external_eval.json"),
    ("m6_camsim", "CAMotics 独立仿真，体积交叉验证",
     "PYTHONPATH=src python scripts/m3_camsim_xcheck.py", "m3_camsim_xcheck.json"),
    ("m6_b3", "B3 无膨胀基线（零过切归因）",
     "PYTHONPATH=src python scripts/m3_b3_baseline.py", "m3_b3_baseline.json"),
    ("m6_ocl", "OpenCAMLib 第三方 CAM 对照（替代 Fusion 360，无 GUI）",
     "PYTHONPATH=src python scripts/m6_opencamlib.py", "m6_ocl.json"),
    ("m6_ocl_external", "OpenCAMLib × 130 件外部集交叉验证（含半格余量对照）",
     "PYTHONPATH=src python scripts/m6_ocl_external.py", "m6_ocl_external.json"),
    ("m6_step_stl", "STEP→FreeCAD Mesh→二进制 STL→OCL 独立几何链路",
     "PYTHONPATH=src python scripts/m6_step_stl_xcheck.py",
     "m6_step_stl_xcheck.json"),
    ("m6_ocl_point_cutter", "OCL 点刀半径敏感性（解释 71mm 探针伪影）",
     "PYTHONPATH=src python scripts/m6_ocl_point_cutter.py",
     "m6_ocl_point_cutter.json"),
]


def _load(name):
    p = os.path.join(REP, name)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def _run(script, timeout=1800):
    t0 = time.time()
    env = dict(os.environ, PYTHONPATH="src")
    proc = subprocess.run([PY, script], cwd=ROOT, env=env, timeout=timeout,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    return {"status": "ok" if proc.returncode == 0 else "FAIL(%d)" % proc.returncode,
            "seconds": round(time.time() - t0, 1),
            "tail": (proc.stdout or "").strip().splitlines()[-6:],
            "stderr": (proc.stderr or "").strip().splitlines()[-3:]}


def headline():
    """论文摘要里要用的那几个数，全部从报告里现取，不手写。"""
    out = {}
    m2 = _load("mfr_test_v2.json")
    if m2:
        out["m2_gnn_accuracy"] = m2["gnn"]["accuracy"]
        out["m2_gnn_macro_iou"] = m2["gnn"]["macro_iou"]
        out["m2_tree_accuracy"] = m2["tree_baseline"]["accuracy"]
        out["m2_tree_macro_iou"] = m2["tree_baseline"]["macro_iou"]
    hm = _load("m3_heightmap_eval.json")
    if hm:
        s = hm["summary"]
        out["m3_b1_total_gouge_mm3"] = s["B1_total_gouge_mm3"]
        out["m3_b2_total_gouge_mm3"] = s["B2_total_gouge_mm3"]
        out["m3_b2_worst_mean_mm"] = s["B2_mean_deviation_worst_mm"]
        out["m3_b2_dual_pass"] = s["accept_dual_criterion_count"]
        out["m3_b2_legacy_max_pass"] = s["legacy_max_pass_count"]
    cam = _load("m3_cam_eval.json")
    if cam:
        out["m3_a_dual_pass"] = cam["summary"]["pass_count"]
        out["m3_a_total_gouge_mm3"] = cam["summary"]["A_total_gouge_mm3"]
    m4 = _load("m4_run.json")
    if m4:
        arms = {a["arm"]: a for a in m4["arms"]}
        out["m4_pass_rate_no_repair"] = arms["A_no_repair"]["pass_rate"]
        out["m4_pass_rate_repair"] = arms["B_repair_cold"]["pass_rate"]
        out["m4_rounds_cold"] = arms["B_repair_cold"]["mean_rounds"]
        out["m4_rounds_warm"] = arms["C_repair_warm"]["mean_rounds"]
    m3a = _load("m3_acceptance.json")
    if m3a:
        out["m3_whitelist_all_ok"] = m3a["summary"]["all_whitelist_ok"]
        out["m3_inject_detected"] = [m3a["summary"]["gouge_defect_detected"],
                                     m3a["summary"]["residual_defect_detected"],
                                     m3a["summary"]["collision_defect_detected"]]
    ext = _load("m3_external_eval.json")
    if ext:
        s = ext.get("summary", {})
        out["m6_external_pass"] = s.get("pass")
    xc = _load("m3_camsim_xcheck.json")
    if xc:
        out["m6_camsim_mean_rel_diff_pct"] = round(
            100 * xc["summary"]["mean_rel_diff"], 2)
    b3 = _load("m3_b3_baseline.json")
    if b3:
        out["m6_b3_dual_pass"] = b3["summary"]["pass_b3"]
    ocl = _load("m6_ocl.json")
    if ocl:
        s = ocl["summary"]
        out["m6_ocl_geometry_exact_pct"] = s["geometry_min_exact_pct"]
        out["m6_ocl_b2_gouge_points"] = s["B2_ocl_gouge_points"]
        out["m6_ocl_b3_gouge_points"] = s["B3_ocl_gouge_points"]
        out["m6_ocl_toolpath_dual_pass"] = s["OCL_toolpath_dual_pass"]
        out["m6_ocl_sweep_rates_pct"] = s["resolution_sweep_rates_pct"]
    ox = _load("m6_ocl_external.json")
    if ox:
        s = ox["summary"]
        out["m6_ocl_ext_evaluated"] = s["evaluated"]
        out["m6_ocl_ext_geometry_min_pct"] = s["geometry_min_within_0_02mm_pct"]
        out["m6_ocl_ext_b2_gouge_points"] = s["B2_gouge_points"]
        out["m6_ocl_ext_b2_deep"] = s["B2_deep"]
        out["m6_ocl_ext_guard_gouge_points"] = s.get("guard_gouge_points")
        out["m6_ocl_ext_guard_dual_pass"] = s.get("guard_our_dual_pass")
    stl = _load("m6_step_stl_xcheck.json")
    if stl:
        s = stl["summary"]
        out["m6_step_stl_min_identical_pct"] = s["two_ocl_surfaces_min_identical_pct"]
        out["m6_step_stl_b2_gouge_points"] = s["B2_gouge_points"]
        out["m6_step_stl_b3_gouge_points"] = s["B3_gouge_points"]
    pc = _load("m6_ocl_point_cutter.json")
    if pc:
        ex = pc["datasets"]["external_130"]["summary"]
        out["m6_point_cutter_over_0_5mm_d0_02"] = ex["d_0.0200"]["columns_over_0_5mm"]
        out["m6_point_cutter_over_0_5mm_d0_002"] = ex["d_0.0020"]["columns_over_0_5mm"]
        out["m6_point_cutter_over_0_5mm_d0_0002"] = ex["d_0.0002"]["columns_over_0_5mm"]
    ap = _load("m5_autonomy_probe.json")
    if ap:
        s = ap["summary"]
        out["m5_autonomy_raw_pass"] = s["raw_dual_line_pass"]
        out["m5_autonomy_projected_pass"] = s["projected_dual_line_pass"]
        out["m5_autonomy_residual_mm3"] = [s["mean_residual_before_mm3"],
                                           s["mean_residual_after_mm3"]]
        out["m5_autonomy_gouge_mm"] = [s["mean_max_gouge_before_mm"],
                                       s["mean_max_gouge_after_mm"]]
    return out


def confidence_intervals():
    """小样本通过率的 Wilson 95% 区间，全部从报告现取，不硬编码。"""
    entries = []

    def add(name, k, n):
        if k is not None and n:
            entries.append({"name": name, "k": int(k), "n": int(n)})

    hm = _load("m3_heightmap_eval.json")
    if hm:
        add("B2 区域掩码双线通过", hm["summary"].get("accept_dual_criterion_count"), 10)
    cam = _load("m3_cam_eval.json")
    if cam:
        add("A 路 FreeCAD CAM 双线通过", cam["summary"].get("pass_count"), 10)
    b3 = _load("m3_b3_baseline.json")
    if b3:
        add("B3 无膨胀基线双线通过", b3["summary"].get("pass_b3"), 10)
    ext = _load("m3_external_eval.json")
    if ext:
        add("MFCAD 外部集双线通过", ext["summary"].get("pass"),
            ext["summary"].get("evaluated", 130))
    ocl = _load("m6_ocl.json")
    if ocl:
        add("OCL 自生成刀路双线通过",
            ocl["summary"].get("OCL_toolpath_dual_pass"), 10)
    m4 = _load("m4_run.json")
    if m4:
        for a in m4.get("arms", []):
            if a["arm"] in ("A_no_repair", "B_repair_cold"):
                add("M4 %s" % a["arm"], int(round(a["pass_rate"] * 20)), 20)
    return wilson_table(entries)


def tests_summary():
    env = dict(os.environ, PYTHONPATH="src")
    proc = subprocess.run([PY, "-m", "pytest", "tests", "-q"], cwd=ROOT, env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    line = (proc.stdout or "").strip().splitlines()[-1:] or ["?"]
    return {"status": "ok" if proc.returncode == 0 else "FAIL", "summary": line[0]}


def write_md(bench):
    L = ["# bench 台账（自动生成，勿手改）", "",
         "> 生成时间：%s　｜　Python：%s" % (bench["generated_at"], bench["python"]),
         "> 重跑：`make bench`　｜　只汇总：`PYTHONPATH=src python scripts/bench.py --reuse`",
         ""]
    L += ["## 1 重算步骤", "", "| 步骤 | 说明 | 状态 | 耗时 |", "|---|---|---|---|"]
    for s in bench["steps"]:
        L.append("| `%s` | %s | %s | %.1fs |"
                 % (s["key"], s["desc"], s["status"], s["seconds"]))
    L += ["", "## 2 存档产物（未在本次重跑）", "",
          "| 产物 | 说明 | 复现命令 | 状态 |", "|---|---|---|---|"]
    for a in bench["archived"]:
        L.append("| `%s` | %s | `%s` | %s |"
                 % (a["artifact"], a["desc"], a["cmd"], a["status"]))
    L += ["", "## 3 关键数字（论文引用口径）", "", "| 指标 | 值 |", "|---|---|"]
    label = {
        "m2_gnn_accuracy": "M2 GNN 面级准确率", "m2_gnn_macro_iou": "M2 GNN mIoU",
        "m2_tree_accuracy": "M2 决策树基线准确率", "m2_tree_macro_iou": "M2 决策树 mIoU",
        "m3_b1_total_gouge_mm3": "B1 包围盒总过切 (mm³)",
        "m3_b2_total_gouge_mm3": "B2 区域掩码总过切 (mm³)",
        "m3_b2_worst_mean_mm": "B2 最坏平均绝对偏差 (mm)",
        "m3_b2_dual_pass": "B2 双线通过件数（共 10）",
        "m3_b2_legacy_max_pass": "B2 旧 max 口径通过件数",
        "m3_a_dual_pass": "A 路双线通过件数（共 10）",
        "m3_a_total_gouge_mm3": "A 路总过切 (mm³)",
        "m4_pass_rate_no_repair": "M4 不修复通过率",
        "m4_pass_rate_repair": "M4 有界修复通过率",
        "m4_rounds_cold": "M4 冷启动平均修复轮次",
        "m4_rounds_warm": "M4 热启动平均修复轮次",
        "m5_autonomy_raw_pass": "B7 模型直写 G-code 双线通过件数（共 10）",
        "m5_autonomy_projected_pass": "B7 验证器投影后双线通过件数（共 10）",
        "m5_autonomy_residual_mm3": "B7 投影前后平均残留体积 (mm³)",
        "m5_autonomy_gouge_mm": "B7 投影前后平均最深过切 (mm)",
        "m6_external_pass": "M6 MFCAD 外部 130 件双线通过数",
        "m6_camsim_mean_rel_diff_pct": "M6 CAMotics 体积相对差均值 (%)",
        "m6_b3_dual_pass": "M6 B3 无膨胀基线双线通过件数（共 10）",
        "m6_ocl_geometry_exact_pct": "M6 OpenCAMLib 目标几何逐柱一致率下限 (%)",
        "m6_ocl_b2_gouge_points": "M6 OCL 判 B2 过切刀心点数",
        "m6_ocl_b3_gouge_points": "M6 OCL 判 B3 过切刀心点数",
        "m6_ocl_toolpath_dual_pass": "M6 OCL 自生成刀路双线通过件数（共 10）",
        "m6_ocl_sweep_rates_pct": "M6 分辨率收敛过切率 (res 0.5/0.25/0.125, %)",
        "m6_ocl_ext_evaluated": "M6 OCL×外部集评测件数",
        "m6_ocl_ext_geometry_min_pct": "M6 OCL×外部集几何逐柱一致率下限 (%)",
        "m6_ocl_ext_b2_gouge_points": "M6 OCL 判外部集 B2 过切点数",
        "m6_ocl_ext_b2_deep": "M6 其中深过切 (>0.5mm) 点数",
        "m6_ocl_ext_guard_gouge_points": "M6 半格余量后剩余过切点数",
        "m6_ocl_ext_guard_dual_pass": "M6 半格余量后双线通过件数",
        "m6_step_stl_min_identical_pct": "M6 STEP→STL 两条 OCL 曲面一致率下限 (%)",
        "m6_step_stl_b2_gouge_points": "M6 STL 链路判 B2 过切点数",
        "m6_step_stl_b3_gouge_points": "M6 STL 链路判 B3 过切点数",
        "m6_point_cutter_over_0_5mm_d0_02": "M6 点刀 Ø0.02 时几何差>0.5mm 的柱",
        "m6_point_cutter_over_0_5mm_d0_002": "M6 点刀 Ø0.002 时几何差>0.5mm 的柱",
        "m6_point_cutter_over_0_5mm_d0_0002": "M6 点刀 Ø0.0002 时几何差>0.5mm 的柱",
    }
    for k, v in bench["headline"].items():
        L.append("| %s | `%s` |" % (label.get(k, k), v))
    L += ["", "## 3b 小样本通过率的 Wilson 95% 置信区间", "",
          "> 10/10 不等于 100%：下界只有 0.7225。论文只写 k/n，不写百分比。", "",
          "| 结论 | k/n | 点估计 | Wilson 95% CI |", "|---|---|---|---|"]
    for e in bench.get("wilson_95ci", []):
        L.append("| %s | %d/%d | %.3f | [%.4f, %.4f] |"
                 % (e["name"], e["k"], e["n"], e["rate"],
                    e["wilson95"][0], e["wilson95"][1]))
    L += ["", "## 4 测试", "", "```", bench["tests"]["summary"], "```", ""]
    with open(os.path.join(REP, "bench.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse", action="store_true", help="不重跑，只汇总现有报告")
    ap.add_argument("--only", default=None, help="只跑某个 key，如 m3_heightmap")
    args = ap.parse_args()

    bench = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
             "python": sys.version.split()[0],
             "seed": "无随机采样；同一输入重跑 blackboard_digest 一致（见 test_run_is_deterministic）",
             "steps": [], "archived": [], "headline": {},
             "wilson_95ci": [], "tests": {}}

    for key, desc, script, artifact in STEPS:
        if args.only and key != args.only:
            continue
        if args.reuse:
            r = {"status": "reused", "seconds": 0.0, "tail": [], "stderr": []}
        else:
            print("→ 重算 %s …" % key, flush=True)
            r = _run(script)
        rec = {"key": key, "desc": desc, "artifact": artifact, **r}
        bench["steps"].append(rec)
        print("  %-13s %-8s %.1fs" % (key, rec["status"], rec["seconds"]), flush=True)

    for key, desc, cmd, artifact in ARCHIVED:
        bench["archived"].append({"key": key, "desc": desc, "cmd": cmd,
                                  "artifact": artifact,
                                  "status": "present" if os.path.exists(
                                      os.path.join(REP, artifact)) else "MISSING"})

    bench["headline"] = headline()
    bench["wilson_95ci"] = confidence_intervals()
    bench["tests"] = tests_summary()

    with open(os.path.join(REP, "bench.json"), "w", encoding="utf-8") as fh:
        json.dump(bench, fh, ensure_ascii=False, indent=1)
    write_md(bench)

    print("\n关键数字：")
    for k, v in bench["headline"].items():
        print("  %-26s %s" % (k, v))
    print("测试：", bench["tests"]["summary"])
    print("→ reports/bench.json / reports/bench.md")
    bad = [s for s in bench["steps"] if s["status"].startswith("FAIL")]
    bad += [a for a in bench["archived"] if a["status"] == "MISSING"]
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
