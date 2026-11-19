"""B7 对照（下半）：让 LLM **写 FreeCAD Python 脚本**建模，与「契约调用」比。

这一半对应计划里的 C1 反面：模型自由生成脚本 vs 只能调封闭工具。
判据是几何本身，不是代码风格：脚本能不能跑通、跑出来的实体体积/包围盒对不对。

安全：脚本来自模型，执行前过一道静态闸（禁 os/subprocess/socket/eval 等），
跑的时候限定临时目录 + 超时；闸拦下的样本单独计数，不混进"失败"里。

    PYTHONPATH=src python scripts/m5_script_eval.py --parts 1,2,8,9,10
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time

from cadcam.adapters import freecad
from cadcam.agents.llm import LLMClient, LLMError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEOM = os.path.join(ROOT, "reports", "annotation", "geom")
LABELS = os.path.join(ROOT, "data", "zenodo_rptu", "labels")
WORK = os.path.join(ROOT, "build", "llm_scripts")
OUT = os.path.join(ROOT, "reports", "m5_script_eval.json")
BANNED = ("os.system", "os.remove", "os.rmdir", "os.popen", "os.walk",
          "subprocess", "shutil", "socket", "urllib", "requests",
          "eval(", "exec(", "__import__", "open(", "pathlib")
SHIM = ('import os as _os\nOUT_STEP = _os.environ["CADCAM_OUT"]\n')

SYS = ("你是 FreeCAD 脚本工程师。只输出 Python 代码，不要解释、不要 Markdown 代码块。 /no_think")


def features(geom, labels):
    from cadcam.modalities.mfr.instances import group
    fam = [labels["faces"][str(f["index"])]["family"] for f in geom["faces"]]
    rows = []
    for i in group(geom, fam, families={x: x for x in set(fam)}):
        if i.get("family") == "stock":
            continue
        b = i.get("bbox") or {}
        try:
            rows.append({"family": i.get("family"),
                         "x": [round(float(b["min"][0]), 1), round(float(b["max"][0]), 1)],
                         "y": [round(float(b["min"][1]), 1), round(float(b["max"][1]), 1)],
                         "z": [round(float(b["min"][2]), 1), round(float(b["max"][2]), 1)]})
        except (KeyError, IndexError, TypeError):
            continue
    return rows


def prompt(geom, feats, part):
    bb = geom["bbox"]
    lines = ["毛坯：x [%.1f, %.1f]，y [%.1f, %.1f]，z [%.1f, %.1f]（单位 mm）。"
             % (bb["min"][0], bb["max"][0], bb["min"][1], bb["max"][1],
                bb["min"][2], bb["max"][2]),
             "这是一个用三轴铣削从毛坯上去除材料得到的零件，要去掉的部分："]
    for f in feats:
        lines.append("  - %s：x %s，y %s，z %s" % (f["family"], f["x"], f["y"], f["z"]))
    body = ("\n".join(lines) + "\n\n"
            "请写一个 FreeCAD 1.1 的 Python 脚本，用 Part 模块的布尔运算（makeBox / "
            "makeCylinder / cut / fuse）构建该零件，并导出 STEP 到 sys.argv[1]。\n"
            "要求：\n"
            "1. 只用 `import FreeCAD as App` 与 `import Part`，不要读外部文件；\n"
            "2. 圆角/倒角可忽略；孔按圆柱减材；\n"
            "3. 输出路径已由外部提供，变量名 `OUT_STEP`；最后调用 "
            "`Part.export([solid], OUT_STEP)` 或 `solid.exportStep(OUT_STEP)`；\n"
            "4. 只输出代码，不要自己定义 OUT_STEP、不要读写其他文件。")
    return [{"role": "system", "content": SYS}, {"role": "user", "content": body}]


def strip_code(text):
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t.strip())
    return t


def unsafe(code):
    return [w for w in BANNED if w in code]


def run_script(path, out_step, timeout=120):
    env = dict(os.environ, CADCAM_OUT=out_step)
    t0 = time.time()
    try:
        p = subprocess.run([freecad.FREECAD_CMD, path], cwd=WORK,
                           capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace", env=env)
        ok = p.returncode == 0 and os.path.exists(out_step)
        return ok, round(time.time() - t0, 1), (p.stderr or p.stdout or "")[-400:]
    except subprocess.TimeoutExpired:
        return False, round(time.time() - t0, 1), "TIMEOUT"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parts", default="1,2,8,9,10")
    ap.add_argument("--max-tokens", type=int, default=1200)
    ap.add_argument("--model", default="qwen3:8b")
    args = ap.parse_args()

    client = LLMClient(model=args.model)
    ok, models = client.available()
    print("模型 %s｜%s" % (args.model, "可用" if ok else "不可用：%s" % models[0]))
    if not ok:
        return 2
    os.makedirs(WORK, exist_ok=True)

    rows = []
    for part in [p.strip() for p in args.parts.split(",") if p.strip()]:
        with open(os.path.join(GEOM, "%s.json" % part), encoding="utf-8") as fh:
            geom = json.load(fh)
        with open(os.path.join(LABELS, "%s.json" % part), encoding="utf-8") as fh:
            labels = json.load(fh)
        ref_vol = float(geom.get("volume_mm3") or 0.0)
        ref_bb = geom["bbox"]

        print("→ 件 %s 生成脚本 …" % part, flush=True)
        try:
            resp = client.chat(prompt(geom, features(geom, labels), part),
                               max_tokens=args.max_tokens, json_mode=False)
            code = strip_code(resp.text)
            tok = (resp.prompt_tokens, resp.completion_tokens)
        except LLMError as exc:
            rows.append({"part": part, "error": str(exc)[:160], "exec_ok": False})
            continue

        bad = unsafe(code)
        sp = os.path.join(WORK, "part%s.py" % part)
        with open(sp, "w", encoding="utf-8") as fh:
            fh.write(SHIM + code)
        if bad:
            rows.append({"part": part, "banned": bad, "exec_ok": False,
                         "blocked": True, "prompt_tokens": tok[0],
                         "completion_tokens": tok[1]})
            print("   被安全闸拦下：%s" % bad)
            continue

        step = os.path.join(WORK, "part%s.step" % part)
        okrun, secs, tail = run_script(sp, step)
        row = {"part": part, "exec_ok": okrun, "seconds": secs,
               "prompt_tokens": tok[0], "completion_tokens": tok[1],
               "ref_volume_mm3": ref_vol}
        if okrun:
            try:
                g = freecad.geometry(step)
                vol = float(g.get("volume_mm3") or 0.0)
                row.update({"volume_mm3": round(vol, 1),
                            "volume_rel_err": round(abs(vol - ref_vol) / max(ref_vol, 1e-6), 4),
                            "faces": len(g.get("faces", []))})
                bb = g["bbox"]
                row["bbox_err_mm"] = round(max(
                    abs(float(bb["max"][k]) - float(ref_bb["max"][k])) for k in range(3)), 2)
            except Exception as exc:              # noqa: BLE001
                row["measure_error"] = "%s: %s" % (type(exc).__name__, str(exc)[:120])
        else:
            row["tail"] = tail
        rows.append(row)
        print("   执行 %-5s 体积误差 %-8s 面数 %-4s"
              % (row.get("exec_ok"), row.get("volume_rel_err"), row.get("faces")))

    n = len(rows)
    good = [r for r in rows if r.get("exec_ok")]
    summary = {
        "route": "LLM 直写 FreeCAD 脚本",
        "n": n,
        "exec_ok": len(good),
        "blocked_by_gate": sum(1 for r in rows if r.get("blocked")),
        "volume_within_5pct": sum(1 for r in good if (r.get("volume_rel_err") or 9) <= 0.05),
        "mean_volume_rel_err": round(sum(r.get("volume_rel_err") or 0.0 for r in good)
                                     / max(1, len(good)), 4),
        "prompt_tokens": sum(r.get("prompt_tokens") or 0 for r in rows),
        "completion_tokens": sum(r.get("completion_tokens") or 0 for r in rows),
        "wall_seconds": round(sum(r.get("seconds") or 0.0 for r in rows), 1),
    }
    report = {"model": args.model, "criterion": "执行成功 + 体积相对误差 ≤5%",
              "safety_gate": list(BANNED), "summary": summary, "rows": rows,
              "generated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)

    print("\n执行成功 %d/%d｜体积误差≤5%% %d｜被安全闸拦 %d｜均误差 %.2f%%"
          % (summary["exec_ok"], n, summary["volume_within_5pct"],
             summary["blocked_by_gate"], 100 * summary["mean_volume_rel_err"]))
    print("→ %s" % os.path.relpath(OUT, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
