"""B7 对照：让 LLM **直接写 G-code**，与「契约调用 + 区域掩码」比。

这是 C1（工具契约 vs 自由生成）的唯一实证。为了不把对照做成稻草人，提示里给足
人类编程员会拿到的信息：毛坯尺寸、每个特征实例的 XY 范围与目标 z、允许的 G/M 代码集、
刀具与程序头模板。**不给**的是刀路参数（步距、切深）——那正是自由生成要自己承担的部分。

判据与论文完全一致（ADR-001 双线 + 白名单），所以两边的数字可以直接并列。

    PYTHONPATH=src python scripts/m5_direct_eval.py
    PYTHONPATH=src python scripts/m5_direct_eval.py --parts 1,2,10 --max-tokens 2500
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

from cadcam.agents.llm import LLMClient, LLMError
from cadcam.modalities.cam import gcode_gen, heightmap, process
from cadcam.modalities.mfr.instances import group
from cadcam.verify import dexel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEOM = os.path.join(ROOT, "reports", "annotation", "geom")
LABELS = os.path.join(ROOT, "data", "zenodo_rptu", "labels")
OUT = os.path.join(ROOT, "reports", "m5_direct_eval.json")
ALLOWED = "G0 G1 G90 G21 G17 G54 T M6 S M3 M5 M8 M9 M30"
HEADER = "G90 G21 G17 G94\nG54\nM8\nT1 M6\nS12000 M3"

SYS = ("你是熟练的 CNC 编程员。只输出 G-code 文本，不要解释、不要 Markdown 代码块。 /no_think")


def _features(geom, labels):
    fam = [labels["faces"][str(f["index"])]["family"] for f in geom["faces"]]
    inst = group(geom, fam, families={x: x for x in set(fam)})
    rows = []
    for i in inst:
        if i.get("family") == "stock":
            continue
        box = i.get("bbox") or {}
        try:
            rows.append({"family": i.get("family"),
                         "x": [round(float(box["min"][0]), 1), round(float(box["max"][0]), 1)],
                         "y": [round(float(box["min"][1]), 1), round(float(box["max"][1]), 1)],
                         "z": [round(float(box["min"][2]), 1), round(float(box["max"][2]), 1)]})
        except (KeyError, IndexError, TypeError):
            continue
    return inst, rows


def prompt(geom, feats, stock_top, finished_top):
    bb = geom["bbox"]
    lines = ["毛坯：%.0f × %.0f mm，顶面 z = %.1f；成品顶面 z = %.1f"
             % (bb["max"][0] - bb["min"][0], bb["max"][1] - bb["min"][1],
                stock_top, finished_top),
             "坐标系：毛坯左下角为原点，z 向上。",
             "需要加工的特征（x/y 为范围，z 为目标高度）："]
    for f in feats:
        lines.append("  - %s：x %s，y %s，z %s" % (f["family"], f["x"], f["y"], f["z"]))
    body = ("\n".join(lines) + "\n\n"
            "要求：\n"
            "1. 只允许这些代码：%s；程序以 %% 开头、M30 结束；\n"
            "2. 只用一把 Ø6 平底刀（T1），可用 G0/G1 直线插补，不要用圆弧；\n"
            "3. 每次下刀都要从安全高度 z = %.1f 起；\n"
            "4. 目标是「不过切」，残留可以接受；\n"
            "5. 程序头已给：\n%s\n\n直接输出完整 G-code。"
            % (ALLOWED, stock_top + 5.0, HEADER))
    return [{"role": "system", "content": SYS}, {"role": "user", "content": body}]


def strip_code(text):
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t.strip())
    if "%" not in t:
        t = "%\n" + t
    if "M30" not in t:
        t = t + "\nM30\n%"
    # 程序头由后处理器补（本来就是模板化的东西）。不补的话对照会退化成
    # "模型会不会抄一行 M3"，而不是"它会不会规划刀路"。
    if not re.search(r"(?<!\d)M3(?![0-9])", t):    # 注意别被 M30 命中
        t = t.replace("%", "%\n" + HEADER, 1)
    return t


def run_part(client, part, max_tokens):
    with open(os.path.join(GEOM, "%s.json" % part), encoding="utf-8") as fh:
        geom = json.load(fh)
    with open(os.path.join(LABELS, "%s.json" % part), encoding="utf-8") as fh:
        labels = json.load(fh)
    inst, feats = _features(geom, labels)
    plan = process.plan(geom, inst)
    stock_top = float(plan["stock"]["top_z"])
    finished = float(plan["stock"]["finished_top_z"])
    target = dexel.rasterize(geom, res=0.5)

    msgs = prompt(geom, feats, stock_top, finished)
    try:
        resp = client.chat(msgs, max_tokens=max_tokens, json_mode=False)
        text, err = strip_code(resp.text), None
        tok = {"prompt_tokens": resp.prompt_tokens,
               "completion_tokens": resp.completion_tokens}
        secs = resp.seconds
    except LLMError as exc:
        text, err, tok, secs = "", str(exc)[:200], {}, 0.0

    os.makedirs(os.path.join(ROOT, "reports", "m5_direct_gcode"), exist_ok=True)
    if text.strip():
        with open(os.path.join(ROOT, "reports", "m5_direct_gcode",
                               "part%s.nc" % part), "w", encoding="utf-8") as fh:
            fh.write(text)

    row = {"part": part, "family": len(feats), "lines": len(text.splitlines()),
           "error": err, "seconds": round(secs, 2), **tok}

    if not text.strip("%\n "):
        row.update({"parse_ok": False, "whitelist_ok": False, "pass": False,
                    "note": "模型返回空内容"})
        return row

    v = gcode_gen.validate(text, stock=plan["stock"])
    ir = v["ir"]
    codes = sorted({e.get("code") for e in v["errors"]})
    row.update({"parse_ok": ir["stats"]["segments"] > 0,
                "whitelist_ok": bool(v["ok"]),
                "error_codes": codes,
                "segments": ir["stats"]["segments"]})

    if ir["stats"]["segments"] == 0:
        row.update({"pass": False, "note": "没有可解析的切削段"})
        return row

    m = dexel.simulate(ir, target, stock_top, tool_radius_mm=3.0, res=0.5)
    import numpy as np
    goal = target["top"]
    mask = np.isfinite(goal)
    gouge = np.where(mask, np.maximum(0.0, goal - m["cur"]), 0.0)
    row.update({"max_gouge_depth_mm": round(float(gouge.max()), 3),
                "mean_deviation_mm": round(m["mean_deviation_mm"], 3),
                "gouge_volume_mm3": m["gouge_volume_mm3"],
                "residual_volume_mm3": m["residual_volume_mm3"],
                "pass": (float(gouge.max()) <= 0.5
                         and m["mean_deviation_mm"] <= 0.5)})
    return row


def contract_baseline(parts):
    """同批零件的契约路线数字（M3.2 的区域掩码），读已有报告避免重复仿真。"""
    path = os.path.join(ROOT, "reports", "m3_heightmap_eval.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        hm = json.load(fh)["parts"]
    want = set(str(p) for p in parts)
    rows = [{"part": p["part"], **{k: p["B2"].get(k) for k in
                                   ("max_gouge_depth_mm", "mean_deviation_mm",
                                    "gouge_volume_mm3", "residual_volume_mm3")}}
            for p in hm if p["part"] in want]
    return {"route": "契约调用（B2 区域掩码）", "rows": rows,
            "pass_count": sum(1 for r in rows
                              if (r["max_gouge_depth_mm"] or 0) <= 0.5
                              and (r["mean_deviation_mm"] or 0) <= 0.5),
            "n": len(rows)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parts", default="1,2,3,4,5,6,7,8,9,10")
    ap.add_argument("--max-tokens", type=int, default=2500)
    ap.add_argument("--model", default="qwen3:8b")
    args = ap.parse_args()

    client = LLMClient(model=args.model)
    ok, models = client.available()
    print("模型 %s｜%s" % (args.model, "可用" if ok else "不可用：%s" % models[0]))
    if not ok:
        return 2

    parts = [p.strip() for p in args.parts.split(",") if p.strip()]
    rows = []
    for p in parts:
        print("→ 件 %s 生成中 …" % p, flush=True)
        r = run_part(client, p, args.max_tokens)
        rows.append(r)
        print("   行 %-4s 段 %-4s 白名单 %-5s 过切 %-8s 平均 %-7s 通过 %s"
              % (r.get("lines"), r.get("segments"), r.get("whitelist_ok"),
                 r.get("max_gouge_depth_mm"), r.get("mean_deviation_mm"),
                 r.get("pass")))

    n = len(rows)
    base = contract_baseline(parts)
    summary = {
        "route": "LLM 直写 G-code",
        "n": n,
        "parse_ok": sum(1 for r in rows if r.get("parse_ok")),
        "whitelist_ok": sum(1 for r in rows if r.get("whitelist_ok")),
        "pass": sum(1 for r in rows if r.get("pass")),
        "mean_max_gouge_mm": round(sum(r.get("max_gouge_depth_mm") or 0.0
                                       for r in rows) / n, 2),
        "mean_deviation_mm": round(sum(r.get("mean_deviation_mm") or 0.0
                                       for r in rows) / n, 2),
        "total_gouge_mm3": round(sum(r.get("gouge_volume_mm3") or 0.0 for r in rows), 1),
        "empty": sum(1 for r in rows if not r.get("segments")),
        "prompt_tokens": sum(r.get("prompt_tokens") or 0 for r in rows),
        "completion_tokens": sum(r.get("completion_tokens") or 0 for r in rows),
        "wall_seconds": round(sum(r.get("seconds") or 0.0 for r in rows), 1),
    }
    report = {"model": args.model, "criterion": "ADR-001 双线 + 白名单",
              "prompt_note": "给了毛坯/特征/目标 z/允许代码集/程序头；未给刀路参数",
              "summary": summary, "rows": rows, "baseline": base,
              "generated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)

    print("\n%-22s %-8s %-10s %-10s %-10s" % ("路线", "通过", "白名单", "均过切mm", "均偏差mm"))
    print("%-22s %d/%-6d %-10s %-10.2f %-10.2f"
          % ("LLM 直写", summary["pass"], n, summary["whitelist_ok"],
             summary["mean_max_gouge_mm"], summary["mean_deviation_mm"]))
    if base:
        print("%-22s %d/%-6d %-10s %-10.2f %-10.2f"
              % ("契约调用（B2）", base["pass_count"], base["n"], "10",
                 max((r["max_gouge_depth_mm"] or 0) for r in base["rows"]),
                 max((r["mean_deviation_mm"] or 0) for r in base["rows"])))
    print("\n→ %s" % os.path.relpath(OUT, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
