"""M5 实验：C2「渐进式几何披露」的 token 与保真度实测。

计划里 C2 的主张是「把上下文从 O(面数) 降到 O(特征数)」，但此前一直没测。
这里用同一道题（这个零件需要几道工序）喂三档上下文，读 API 返回的
`usage.prompt_tokens`——真实 token 数，不是字符估算。

  L0 摘要          ：面数/边数/体积/包围盒
  L0 + 特征列表     ：再加每个特征实例的族别与 z 范围（≈ O(特征数)）
  全量面表 L2       ：逐面类型/面积/法向/邻接（≈ O(面数)）

    PYTHONPATH=src python scripts/m5_context_eval.py
"""
from __future__ import annotations

import json
import os
import sys

from cadcam.agents import prompts
from cadcam.agents.llm import LLMClient, LLMError
from cadcam.modalities.brep import digest
from cadcam.modalities.cam import process
from cadcam.modalities.mfr.instances import group
from cadcam.verify import dexel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEOM = os.path.join(ROOT, "reports", "annotation", "geom")
LABELS = os.path.join(ROOT, "data", "zenodo_rptu", "labels")
OUT = os.path.join(ROOT, "reports", "m5_context_eval.json")
PARTS = ("1", "8", "10")
SCHEMA = {"required": ["n_ops"],
          "properties": {"n_ops": {"type": "number", "minimum": 1, "maximum": 50}}}


def load(part):
    with open(os.path.join(GEOM, "%s.json" % part), encoding="utf-8") as fh:
        geom = json.load(fh)
    with open(os.path.join(LABELS, "%s.json" % part), encoding="utf-8") as fh:
        labels = json.load(fh)
    fam = [labels["faces"][str(f["index"])]["family"] for f in geom["faces"]]
    inst = group(geom, fam, families={x: x for x in set(fam)})
    plan = process.plan(geom, inst)
    return geom, labels, len(plan["ops"])


def main():
    client = LLMClient()
    ok, models = client.available()
    print("LLM 端点 %s｜模型 %s｜%s"
          % (client.base_url, client.model, "可用" if ok else "不可用：%s" % models[0]))
    if not ok:
        return 2

    rows = []
    for part in PARTS:
        geom, labels, n_ops = load(part)
        print("\n件 %s（真实工序 %d 道）" % (part, n_ops))
        for name, text in prompts.context_variants(geom, labels, digest):
            msgs = [{"role": "system", "content": "只输出 JSON。 /no_think"},
                    {"role": "user", "content":
                     "根据下面的零件信息，估计需要多少道加工工序。\n"
                     "输出 {\"n_ops\": 整数}\n\n" + text}]
            try:
                data, resp = client.complete_json(msgs, SCHEMA, max_tokens=64)
                est, tok, sec = data["n_ops"], resp.prompt_tokens, resp.seconds
                err = None
            except LLMError as exc:
                est, tok, sec, err = None, 0, 0.0, str(exc)[:120]
            rows.append({"part": part, "variant": name, "chars": len(text),
                         "prompt_tokens": tok, "est_ops": est, "truth_ops": n_ops,
                         "abs_err": abs((est or 0) - n_ops) if est else None,
                         "seconds": round(sec, 2), "error": err})
            print("  %-14s 字符 %5d｜prompt_tokens %5d｜估计 %-4s｜误差 %-4s"
                  % (name, len(text), tok, est, rows[-1]["abs_err"]))

    # 每个零件以 L0 为基准算倍数
    for part in PARTS:
        base = next(r["prompt_tokens"] for r in rows
                    if r["part"] == part and r["variant"] == "L0 摘要") or 1
        for r in rows:
            if r["part"] == part:
                r["x_vs_L0"] = round(r["prompt_tokens"] / base, 1)

    def agg(variant, key):
        vals = [r[key] for r in rows if r["variant"] == variant and r[key] is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    summary = [{"variant": v,
                "mean_prompt_tokens": agg(v, "prompt_tokens"),
                "mean_x_vs_L0": agg(v, "x_vs_L0"),
                "mean_chars": agg(v, "chars"),
                "mean_abs_err": agg(v, "abs_err")}
               for v in ("L0 摘要", "L0+特征实例", "全量面表 L2")]

    report = {"model": client.model, "parts": list(PARTS),
              "question": "估计该零件需要多少道加工工序",
              "rows": rows, "summary": summary,
              "meter": client.meter.summary()}
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)

    print("\n%-14s %-10s %-8s %-10s" % ("上下文", "均 token", "相对 L0", "均绝对误差"))
    for s in summary:
        print("%-14s %-10s %-8s %-10s"
              % (s["variant"], s["mean_prompt_tokens"], s["mean_x_vs_L0"],
                 s["mean_abs_err"]))
    print("\n→ %s" % os.path.relpath(OUT, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
