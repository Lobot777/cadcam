"""M3 人工过切标注包（验收项：「过切检测与人工标注一致率 ≥ 90%」）。

方法学要点：**真值来自已知注入，不是 dexel 自己**。
若拿 dexel 的结果当答案键，再报「人与验证器一致率」就是同义反复。
这里每道题的过切/无过切由构造过程唯一确定：

    clean     B2 高度图刀路                   → 无过切
    residual  B2 + 0.5mm 抬刀                → 有残留但**无过切**（干扰项）
    gouge1    B2 注入 1.0mm 下刀             → 有过切
    gouge03   B2 注入 0.3mm 下刀             → 有过切（难题）

评估者只看偏差热图，独立三选一；验证器独立出 E_GOUGE。
最终报：人-验证器一致率 / Cohen κ / 双方各自对真值的准确率。

用法（系统 python，需 matplotlib）：
    PYTHONPATH=src python scripts/m3_human_pack.py
    PYTHONPATH=src python scripts/m3_human_pack.py --score human1.json human2.json
"""
from __future__ import annotations

import argparse
import base64
import html
import io
import json
import math
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from cadcam.modalities.cam import gcode_gen, heightmap, process  # noqa: E402
from cadcam.modalities.mfr.instances import group               # noqa: E402
from cadcam.verify import dexel                                 # noqa: E402

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                                  # noqa: E402
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
from matplotlib.colors import ListedColormap                     # noqa: E402
import numpy as np                                               # noqa: E402

GEOM = os.path.join(ROOT, "reports", "annotation", "geom")
LABELS = os.path.join(ROOT, "data", "zenodo_rptu", "labels")
HM_DIR = os.path.join(ROOT, "reports", "m3_gcode_hm")
OUTDIR = os.path.join(ROOT, "reports", "m3_human")
RES = 0.5
PARTS = [str(i) for i in range(1, 11)]

# 难度梯度：全灰/全红是简单题，残留是干扰项，局部过切是难题。
# 8 clean + 7 residual + 7 gouge1 + 4 gouge03 + 4 local = 30，真值 15/15 平衡。
PLAN = ([("clean", p, 0.0) for p in ("1", "2", "3", "4", "5", "6", "7", "8")]
        + [("residual", p, 0.5) for p in ("9", "10", "1", "3", "5", "7", "9")]
        + [("gouge1", p, 1.0) for p in ("1", "2", "3", "4", "5", "6", "7")]
        + [("gouge03", p, 0.3) for p in ("8", "9", "10", "2")]
        + [("local", p, 0.8) for p in ("4", "6", "8", "10")])
DIFFICULTY = {"clean": "easy", "gouge1": "easy", "residual": "medium",
              "gouge03": "medium", "local": "hard"}
PACK_VERSION = "v2"
SHUFFLE_SEED = 20261105
GOUGE_KINDS = ("gouge1", "gouge03", "local")


def ordered_plan(seed=SHUFFLE_SEED):
    """打乱题序，切断「题号 ↔ 真值」的关联。

    v1 的 PLAN 按构造类型顺序排列，导致 H01–H15 全 no_gouge、H16–H30 全 gouge，
    评估者只要发现前后半规律就能不看图全对（顺序泄漏）。v2 固定种子打乱，
    并约束「最长连续同真值 ≤ 4」，避免局部又出现长串。
    """
    for s in range(seed, seed + 500):
        rows = list(PLAN)
        random.Random(s).shuffle(rows)
        truth = [r[0] in GOUGE_KINDS for r in rows]
        longest = run = 1
        for i in range(1, len(truth)):
            run = run + 1 if truth[i] == truth[i - 1] else 1
            longest = max(longest, run)
        if longest <= 4:
            return rows, s
    raise RuntimeError("找不到满足最长连续同真值 ≤4 的题序种子")


def inject_local_gouge(text, amount):
    """只在中间某一道高度层工序里下刀过深，制造**局部**过切。

    全刀路注入会让热图整片变红，人一眼就能判，一致率虚高；
    局部注入才检验得出「验证器与人在边界样本上是否一致」。
    """
    import re
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith("(HM")]
    if len(starts) < 3:
        raise ValueError("高度图工序不足，无法做局部注入")
    k = len(starts) // 2
    a, b = starts[k], (starts[k + 1] if k + 1 < len(starts) else len(lines))
    for i in range(a, b):
        mo = re.match(r"^(G1 Z)(-?[\d.]+)(.*)$", lines[i])
        if mo:
            lines[i] = "%s%s%s" % (mo.group(1),
                                   "%.3f" % (float(mo.group(2)) - amount),
                                   mo.group(3))
    return "\n".join(lines) + "\n"


def _target_and_plan(part):
    with open(os.path.join(GEOM, "%s.json" % part), encoding="utf-8") as fh:
        geom = json.load(fh)
    with open(os.path.join(LABELS, "%s.json" % part), encoding="utf-8") as fh:
        lab = json.load(fh)
    labels = [lab["faces"][str(f["index"])]["family"] for f in geom["faces"]]
    plan = process.plan(geom, group(geom, labels,
                                    families={x: x for x in set(labels)}))
    return geom, plan, dexel.rasterize(geom, res=RES)


def _deviation_map(text, target, plan):
    import re
    v = gcode_gen.validate(text)
    # B2 的工序标签 HM<pass>_L<level>：pass 1 = Ø6 粗刀，pass 2 = Ø1.5 精刀。
    # 不按工序取刀径会虚报残留（M3.1 首版踩过这个坑）。
    r_by_op = {}
    for line in text.splitlines():
        mo = re.match(r"^\((HM(\d+)_L\d+)\)$", line)
        if mo:
            r_by_op[mo.group(1)] = (6.0 if mo.group(2) == "1" else 1.5) / 2.0
    m = dexel.simulate(v["ir"], target, plan["stock"]["top_z"],
                       tool_radius_mm=3.0, res=RES,
                       tool_radius_by_op=r_by_op)
    dev = np.where(np.isfinite(target["top"]), m["cur"] - target["top"], np.nan)
    # 正 = 残留（材料多了），负 = 过切（切深了）
    return dev, m


def _png(dev, title):
    """三色偏差热图：红=过切，蓝=残留，灰=准确。不给任何结论数字。"""
    cls = np.full(dev.shape, np.nan)
    cls[np.isfinite(dev)] = 0
    cls[dev > 0.1] = 1
    cls[dev < -0.1] = 2
    fig, ax = plt.subplots(figsize=(5.4, 4.6), dpi=110)
    cmap = ListedColormap(["#e8ecef", "#4a90d9", "#d94a3d"])
    im = ax.imshow(np.nan_to_num(cls, nan=0), origin="lower", cmap=cmap,
                   vmin=0, vmax=2, interpolation="nearest", aspect="equal")
    ax.set_title(title, fontsize=11, color="#243040")
    ax.set_xlabel("X (0.5mm/格)", fontsize=9)
    ax.set_ylabel("Y (0.5mm/格)", fontsize=9)
    ax.tick_params(labelsize=8)
    cb = fig.colorbar(im, ax=ax, ticks=[0.33, 1.0, 1.67], fraction=0.046)
    cb.ax.set_yticklabels(["准确", "残留", "过切"], fontsize=9)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def build():
    os.makedirs(os.path.join(OUTDIR, "img"), exist_ok=True)
    ordered, seed = ordered_plan()
    samples, key, validator = [], {}, {}
    for n, (kind, part, amount) in enumerate(ordered, start=1):
        sid = "H%02d" % n
        geom, plan, target = _target_and_plan(part)
        with open(os.path.join(HM_DIR, "%s.nc" % part), encoding="utf-8") as fh:
            clean = fh.read()
        if kind == "clean":
            text = clean
        elif kind == "residual":
            text = gcode_gen.inject_defect(clean, "residual", amount)
        elif kind == "local":
            text = inject_local_gouge(clean, amount)
        else:
            text = gcode_gen.inject_defect(clean, "gouge", amount)
        dev, m = _deviation_map(text, target, plan)
        title = "样本 %s · 零件 %s" % (sid, part)
        b64 = _png(dev, title)
        with open(os.path.join(OUTDIR, "img", "%s.png" % sid), "wb") as fh:
            fh.write(base64.b64decode(b64))
        truth = kind in ("gouge1", "gouge03", "local")
        key[sid] = {"truth": "gouge" if truth else "no_gouge",
                    "kind": kind, "difficulty": DIFFICULTY[kind],
                    "part": part, "amount_mm": amount}
        validator[sid] = {"predicted": "gouge" if m["gouge_volume_mm3"] > 1.0
                          else "no_gouge",
                          "gouge_volume_mm3": m["gouge_volume_mm3"],
                          "residual_volume_mm3": m["residual_volume_mm3"]}
        n_ops = sum(1 for line in text.splitlines()
                    if line.startswith("(HM") and line.endswith(")"))
        samples.append({"sample_id": sid, "part": part, "title": title,
                        "png": b64,
                        "neutral_info": "工序数 %d｜刀具 Ø6/Ø1.5｜栅格 0.5mm" % n_ops})
    with open(os.path.join(OUTDIR, "samples.json"), "w", encoding="utf-8") as fh:
        json.dump(samples, fh, ensure_ascii=False)
    # 答案键与验证器输出单独存放，不放进发给评估者的页面
    with open(os.path.join(OUTDIR, "key.json"), "w", encoding="utf-8") as fh:
        json.dump({"pack_version": PACK_VERSION, "order_seed": seed,
                   "items": key}, fh, ensure_ascii=False, indent=1)
    with open(os.path.join(OUTDIR, "validator.json"), "w", encoding="utf-8") as fh:
        json.dump(validator, fh, ensure_ascii=False, indent=1)
    _write_page(samples)
    print("生成 %d 题｜真值 %d 过切 / %d 无过切｜验证器对真值 %d/%d"
          % (len(samples), sum(k["truth"] == "gouge" for k in key.values()),
             sum(k["truth"] == "no_gouge" for k in key.values()),
             sum(validator[s]["predicted"] == key[s]["truth"] for s in key),
             len(key)))
    print("wrote", OUTDIR)


PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>CAD2CAM · 过切人工判定</title><style>
*{box-sizing:border-box}body{margin:0;background:#f4f6f8;color:#1e2733;
font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
header{position:sticky;top:0;z-index:9;background:#fff;border-bottom:1px solid #dfe4ea;
padding:10px 20px;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
header b{font-size:15px}input#pid{border:1px solid #ccd3db;border-radius:5px;padding:5px 8px}
button{cursor:pointer;border:1px solid #ccd3db;background:#fff;border-radius:5px;padding:5px 11px}
button.primary{background:#25507a;color:#fff;border-color:#25507a}
main{max-width:980px;margin:0 auto;padding:18px}
.help{background:#fff;border:1px solid #dfe4ea;border-radius:8px;padding:12px 16px;margin-bottom:16px}
.card{background:#fff;border:1px solid #dfe4ea;border-radius:8px;padding:14px 16px;margin-bottom:14px;
display:grid;grid-template-columns:340px 1fr;gap:18px}
.card img{width:100%;border:1px solid #e6e9ed;border-radius:5px}
h3{margin:0 0 6px;font-size:15px}.meta{color:#66717e;font-size:12.5px;margin-bottom:10px}
.opts{display:flex;flex-direction:column;gap:7px}
.opts button{text-align:left;padding:8px 12px;font-size:13.5px}
.opts button.on{background:#25507a;color:#fff;border-color:#25507a}
.cf{margin-top:9px;display:flex;gap:6px}.cf button{font-size:12px;padding:4px 9px}
.cf button.on{background:#e8f0f8;border-color:#25507a;color:#25507a}
textarea{width:100%;min-height:34px;border:1px solid #ccd3db;border-radius:5px;padding:5px;font-size:12.5px}
</style></head><body>
<header><b>过切人工判定</b><span>编号 <input id="pid" size="8" placeholder="E1"></span>
<span id="prog">0 / __N__</span><button class="primary" onclick="exp()">导出 JSON</button>
<span style="color:#66717e;font-size:12px">进度自动保存在本机</span></header>
<main><div class="help"><b>任务</b>：看每道题的偏差热图，判断这张刀路<b>有没有过切</b>
（红色 = 过切，蓝色 = 残留/没切够，灰色 = 准确）。<br>
三选一：<b>有过切</b> / <b>无过切</b> / <b>判不了</b>。蓝<strong>不是</strong>过切；
只要出现红色区域就选「有过切」——<b>有些题只有一小块红，请仔细看</b>。
再点一下把握度，可写备注。<br>
<b>不要</b>参考任何其它文件，也不要和别人讨论。判不了是合法答案。
</div>__CARDS__</main>
<script>
const N=__N__, DATA=__DATA__;
const pidEl=document.getElementById('pid');
pidEl.value=localStorage.getItem('m3_human_pid')||'';
pidEl.addEventListener('input',()=>{localStorage.setItem('m3_human_pid',pidEl.value.trim());load()});
function keyf(){return 'm3_human:'+(pidEl.value.trim()||'_')}
function load(){let s={};try{s=JSON.parse(localStorage.getItem(keyf())||'{}')}catch(e){}
 DATA.forEach(x=>{const v=s[x.id]||{};
  document.querySelectorAll('[data-s="'+x.id+'"] .fam').forEach(b=>b.classList.toggle('on',b.dataset.v===v.family));
  document.querySelectorAll('[data-s="'+x.id+'"] .cf button').forEach(b=>b.classList.toggle('on',b.dataset.v===v.conf));
  const t=document.querySelector('[data-s="'+x.id+'"] textarea');if(t)t.value=v.note||''});
 upd()}
function state(){let s={};try{s=JSON.parse(localStorage.getItem(keyf())||'{}')}catch(e){}return s}
function setf(id,k,v){const s=state();s[id]=s[id]||{};s[id][k]=v;
 localStorage.setItem(keyf(),JSON.stringify(s));load()}
function upd(){const s=state();document.getElementById('prog').textContent=
 Object.keys(s).filter(k=>s[k].family).length+' / '+N}
document.querySelectorAll('.fam').forEach(b=>b.addEventListener('click',()=>setf(b.dataset.s,'family',b.dataset.v)));
document.querySelectorAll('.cf button').forEach(b=>b.addEventListener('click',()=>setf(b.dataset.s,'conf',b.dataset.v)));
document.querySelectorAll('textarea').forEach(t=>t.addEventListener('input',()=>setf(t.dataset.s,'note',t.value)));
function exp(){const s=state(),items=DATA.map(x=>({sample_id:x.id,family:(s[x.id]||{}).family||null,
 conf:(s[x.id]||{}).conf||null,note:(s[x.id]||{}).note||''}));
 const payload={annotator:pidEl.value.trim()||'UNKNOWN',round:'M3_HUMAN',
  pack_version:'__PACKVERSION__',count:items.length,items:items};
 const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(payload,null,1)],
 {type:'application/json'}));a.download=(pidEl.value.trim()||'UNKNOWN')+'_M3_HUMAN.json';a.click()}
load();
</script></body></html>"""


def _write_page(samples):
    cards = []
    for s in samples:
        sid = s["sample_id"]
        opts = "".join(
            '<button class="fam" data-s="%s" data-v="%s">%s</button>' % (sid, v, label)
            for v, label in (("gouge", "有过切"), ("no_gouge", "无过切"),
                             ("unknown", "判不了")))
        confs = "".join('<button data-s="%s" data-v="%s">%s</button>' % (sid, v, label)
                        for v, label in (("sure", "确定"), ("unsure", "拿不准"),
                                         ("guess", "猜的")))
        cards.append(
            '<section class="card" data-s="%s"><div><img src="data:image/png;base64,%s" alt="%s"></div>'
            '<div><h3>%s</h3><div class="meta">%s</div><div class="opts">%s</div>'
            '<div class="cf">把握度 %s</div>'
            '<textarea data-s="%s" placeholder="备注（可选）"></textarea></div></section>'
            % (sid, s["png"], sid, html.escape(s["title"]),
               html.escape(s["neutral_info"]), opts, confs, sid))
    data = json.dumps([{"id": s["sample_id"]} for s in samples], ensure_ascii=False)
    text = (PAGE.replace("__N__", str(len(samples)))
                .replace("__CARDS__", "\n".join(cards))
                .replace("__DATA__", data)
                .replace("__PACKVERSION__", PACK_VERSION))
    with open(os.path.join(OUTDIR, "annotate.html"), "w", encoding="utf-8") as fh:
        fh.write(text)


def _kappa(a, b):
    labels = ["gouge", "no_gouge", "unknown"]
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    pe = sum((a.count(L) / n) * (b.count(L) / n) for L in labels)
    return (po - pe) / (1 - pe) if pe < 1 else 0.0, po


def score(files, keydir=OUTDIR):
    with open(os.path.join(keydir, "key.json"), encoding="utf-8") as fh:
        raw = json.load(fh)
    # v2 起 key.json 带 pack_version/order_seed 外壳；v1 是裸 dict
    key = raw["items"] if isinstance(raw, dict) and "items" in raw else raw
    pack_version = raw.get("pack_version", "v1") if isinstance(raw, dict) else "v1"
    with open(os.path.join(keydir, "validator.json"), encoding="utf-8") as fh:
        validator = json.load(fh)
    ids = sorted(key)
    out = {"n_items": len(ids), "pack_version": pack_version,
           "evaluators": [], "summary": {}}
    human_preds = []
    for path in files:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        pv = d.get("pack_version", "v1")
        if pv != pack_version:
            raise SystemExit(
                "题目包版本不匹配：%s 是 %s，当前 key 是 %s。\n"
                "v1 的真人作答请用 --keydir reports/m3_human_v1 评分；\n"
                "v2 需重新发放 reports/m3_human/annotate.html 回收新作答。"
                % (path, pv, pack_version))
        got = {x["sample_id"]: x.get("family") for x in d["items"]}
        missing = [s for s in ids if s not in got or got[s] is None]
        truth = [key[s]["truth"] for s in ids]
        hv = [got.get(s, "unknown") for s in ids]
        vv = [validator[s]["predicted"] for s in ids]
        # 人-验证器一致率只在「人作了实质判断」的题上算
        paired = [(h, v) for h, v in zip(hv, vv) if h in ("gouge", "no_gouge")]
        agree = sum(h == v for h, v in paired) / len(paired) if paired else None
        kap, po = _kappa([h for h, _ in paired], [v for _, v in paired]) if paired else (None, None)
        row = {"file": os.path.basename(path), "annotator": d.get("annotator"),
               "missing": len(missing), "unknown_rate": round(
                   sum(h == "unknown" for h in hv) / len(hv), 4),
               "accuracy_vs_truth": round(sum(h == t for h, t in zip(hv, truth)) / len(truth), 4),
               "agreement_with_validator": round(agree, 4) if agree is not None else None,
               "cohen_kappa_with_validator": round(kap, 4) if kap is not None else None}
        out["evaluators"].append(row)
        human_preds.append(hv)
        print("%-24s 缺 %2d｜判不了 %.1f%%｜对真值 %.3f｜与验证器一致 %.3f｜κ %.3f"
              % (row["file"], row["missing"], 100 * row["unknown_rate"],
                 row["accuracy_vs_truth"], row["agreement_with_validator"] or 0,
                 row["cohen_kappa_with_validator"] or 0))
    if human_preds:
        majority = []
        for i in range(len(ids)):
            votes = [p[i] for p in human_preds if p[i] in ("gouge", "no_gouge")]
            majority.append(max(set(votes), key=votes.count) if votes else "unknown")
        vv = [validator[s]["predicted"] for s in ids]
        paired = [(h, v) for h, v in zip(majority, vv) if h != "unknown"]
        agree = sum(h == v for h, v in paired) / len(paired) if paired else None
        by_diff = {}
        for diff in ("easy", "medium", "hard"):
            sub = [s for s in ids if key[s]["difficulty"] == diff]
            pr = [(majority[i], validator[s]["predicted"])
                  for i, s in enumerate(ids) if s in sub
                  and majority[i] in ("gouge", "no_gouge")]
            by_diff[diff] = {
                "n": len(sub),
                "agreement": (round(sum(h == v for h, v in pr) / len(pr), 4)
                              if pr else None),
                "validator_accuracy": round(
                    sum(validator[s]["predicted"] == key[s]["truth"] for s in sub)
                    / len(sub), 4),
            }
        per = [e["agreement_with_validator"] for e in out["evaluators"]
               if e["agreement_with_validator"] is not None]
        out["summary"] = {
            "majority_agreement_with_validator": round(agree, 4) if agree is not None else None,
            "mean_evaluator_agreement": round(sum(per) / len(per), 4) if per else None,
            "validator_accuracy_vs_truth": round(
                sum(validator[s]["predicted"] == key[s]["truth"] for s in ids) / len(ids), 4),
            # 保守口径：用多数投票，避免「挑一个评估者报好看的数」
            "accept_ge_90pct": (agree is not None and agree >= 0.90),
            "n_evaluators": len(files),
            "by_difficulty": by_diff,
        }
        print("\n汇总:", json.dumps(out["summary"], ensure_ascii=False))
    dest = os.path.join(keydir, "score_result.json")
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print("wrote", dest)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--score", nargs="*", default=None,
                    help="给一份或多份人工 JSON 评分；不给则生成题目包")
    ap.add_argument("--keydir", default=OUTDIR,
                    help="答案键目录（v1 归档在 reports/m3_human_v1）")
    args = ap.parse_args()
    if args.score is not None:
        if not args.score:
            ap.error("--score 至少要给一个 JSON 文件")
        score(args.score, args.keydir)
    else:
        build()


if __name__ == "__main__":
    main()
