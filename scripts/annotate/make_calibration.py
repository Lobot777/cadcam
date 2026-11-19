"""把 206 题全量包拆成「186 题正式包」+「20 题带反馈校准包」，并生成校准页。

为什么要拆：校准题当场给正确答案，如果它们还留在正式题库里，T1 轮就被泄漏了。
正式包（formal/）同时供 T0 与 T1 使用，两轮题集完全一致，只换题序，配对分析才成立。

产物：
  reports/annotation/study/formal/   186 题（samples.json / img / key.json），发给受试者
  reports/annotation/study/calib/    20 题（samples.json / img / key.json + calibration.html），仅培训环节
  reports/annotation/study/full/     原始 206 题全量包（主试留档，不发给受试者）

校准题挑选原则：排除 15 个 HARD 面（那些连主标注员都判错过，不适合当例题）；
按家族配额抽，优先挑"判据典型"的——圆柱面弧段占比接近 0.25 或 1.00、
平面法向沿轴向且面积完整、不含 BSpline。
"""

import json
import math
import os
import random
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from study_sample import HARD  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(HERE))
STUDY = os.path.join(ROOT, "reports", "annotation", "study")
FULL = os.path.join(STUDY, "full")

# 教学向配额：少放一眼就懂的 stock，多放需要判据的圆柱/腔/凸台
QUOTA = {"stock": 3, "hole": 5, "boss": 4, "pocket": 3, "step": 2, "fillet": 2, "slot": 1}
SEED = 20261105

FAM_CN = {"stock": "毛坯原始面", "hole": "孔", "pocket": "型腔", "slot": "槽",
          "step": "台阶", "boss": "凸台/岛/销", "fillet": "圆角",
          "chamfer": "倒角", "freeform": "自由曲面"}


def facts(s):
    f = s["face"]
    fb = f["bbox_mm"]
    r = (f.get("params") or {}).get("radius")
    h = fb[5] - fb[2]
    arc = f["area_mm2"] / (2 * math.pi * r * h) if (r and h > 1e-6) else None
    n = f["normal"] or [0, 0, 1]
    return {"arc": arc, "r": r, "h": h, "z0": fb[2], "z1": fb[5],
            "area": f["area_mm2"], "surface": f["surface"],
            "rd": f.get("radial_dot"),
            "axial": abs(n[2]) > 0.99, "n_neighbors": len(s["neighbors"])}


def typical(s, key):
    """越高越适合当例题：判据清晰、无歧义。"""
    d = facts(s)
    sc = 0.0
    if "BSpline" not in d["surface"] and "Bezier" not in d["surface"]:
        sc += 3
    if d["arc"] is not None:
        sc += 4 - 8 * min(abs(d["arc"] - 0.25), abs(d["arc"] - 1.0))
        sc = max(sc, 0)
    else:
        if d["axial"]:
            sc += 3
        if d["area"] > 100:
            sc += 1
    if d["n_neighbors"] >= 2:
        sc += 1
    if key.get("hard"):
        sc -= 100
    return sc


def explain(s, key):
    """生成该题的反馈文字。只用页面上看得到的数据，不引入受试者算不出的量。

    注意：一个家族里可能同时有"壁"和"底/顶面"，解释必须按面型与 z 跨度分支，
    否则会把孔底平面说成圆柱壁。
    """
    d = facts(s)
    fam = key["family"]
    flat = d["h"] < 0.05          # 水平面（底 / 顶 / 肩面）
    is_cyl = d["surface"] == "Cylinder"
    bits = ["正确答案：<b>%s（%s）</b>。" % (FAM_CN[fam], fam)]
    geo = "这个面是 %s%s，面积 %.1f mm²，z 从 %.0f 到 %.0f" % (
        d["surface"], "（水平面）" if flat else "（竖向，高 %.0f mm）" % d["h"],
        d["area"], d["z0"], d["z1"])
    if d["r"]:
        geo += "，半径 %.2f mm" % d["r"]
    if d["rd"] is not None:
        geo += "，径向点积 %+.2f" % d["rd"]
    bits.append(geo + "。")

    why = {
        "stock": "落在零件包围盒的外表面上（x/y/z 某个极值面）、法向沿某个主轴——毛坯还没被加工过的原始面，不是特征。水平顶底和四周侧壁都算。",
        "hole": ("整段圆柱壁：弧段占比接近 1.00，说明是完整的圆孔壁而不是过渡圆角；通常还能找到一个孔底平面。"
                 if is_cyl else
                 "孔底平面：把孔封住的那个底面。它本身是平的，但邻面是一圈圆柱壁，"
                 "面积常等于 πr²（r 取相邻圆柱壁的半径）——孔壁和孔底都归到 hole。"),
        "pocket": ("腔壁：竖向、法向水平，和相邻腔壁一起把材料围出一圈；配合更低的腔底可判定为型腔。"
                   if not flat else
                   "腔底：一块被四周立壁围合、且 z 明显低于周围顶面的水平面。材料被挖掉，所以是型腔不是台阶。"),
        "slot": ("槽底：细长的水平面，宽度明显小于长度，两侧各接一面互相平行的立壁。"
                    if flat else
                    "槽壁：两侧壁互相平行（法向相反），端部开口或封闭。宽度明显小于长度是它和型腔的区别。"),
        "step": ("一级高度差的肩面：两个不同 z 的平面之间留下的环带，常与一段竖壁成对出现。"
                 if flat else
                 "台阶的竖壁：连接上下两个不同高度的水平面，本身不是凸台也不是腔壁。"),
        "boss": ("凸出来的圆柱实体（圆销 / 圆柱凸台 / 岛的外壁）：弧段占比接近 1.00 是整圈圆柱而非圆角，"
                 "且顶上常有一个盖面。" if is_cyl else
                 ("凸台顶面：一块高于周围肩面的水平面，四周立壁的法向都朝外。"
                  if flat else
                  "凸台立壁：法向朝外，与同伴一起围出凸起的实体；和腔壁的区别正是法向朝外而非朝内。")),
        "fillet": "切向过渡的圆角：弧段占比接近 0.25，说明只是 ¼ 段圆弧，不是整圈圆柱。它连接两个面、自己不构成特征。",
        "chamfer": "窄斜面，与邻面成固定角度（常见 45°），面积比相邻大面小得多。",
        "freeform": "BSpline/Bezier 自由曲面，没有恒定半径，也不落在标准平面上。",
    }[fam]
    bits.append(why)

    if d["rd"] is not None:
        if d["rd"] > 0.5:
            bits.append("径向点积 <b>%+.2f（凸）</b>：法向背离轴线，对应销 / 圆柱凸台。"
                        % d["rd"])
        else:
            bits.append("径向点积 <b>%+.2f（凹）</b>：法向朝轴线。注意 <b>凹不一定就是孔</b>——"
                        "圆角也是凹的，必须再结合弧段占比：≈0.25 是圆角，≈1.00 才是孔或腔壁。"
                        % d["rd"])

    if d["arc"] is not None:
        bits.append("本题核算：<code>面积/(2πRh) = %.1f / (2π×%.2f×%.0f) = <b>%.2f</b></code>——%s。"
                    % (d["area"], d["r"], d["h"], d["arc"],
                       "≈0.25，是 ¼ 圆弧（圆角）" if abs(d["arc"] - 0.25) < 0.12
                       else ("≈1.00，是整圈圆柱" if abs(d["arc"] - 1.0) < 0.12
                             else "介于两者之间，要结合邻面看")))
    bits.append("拿不准时点「判不了」是合法答案，不扣分。")
    return " ".join(bits)


def copy_imgs(ids, src_dir, dst_dir):
    os.makedirs(dst_dir, exist_ok=True)
    for sid in ids:
        shutil.copy2(os.path.join(src_dir, sid + ".png"),
                     os.path.join(dst_dir, sid + ".png"))


def write_pack(outdir, samples, key, answers=None):
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "samples.json"), "w", encoding="utf-8") as fh:
        json.dump(samples, fh, ensure_ascii=False, indent=1)
    with open(os.path.join(outdir, "key.json"), "w", encoding="utf-8") as fh:
        json.dump(key, fh, ensure_ascii=False, indent=1)


def main():
    with open(os.path.join(FULL, "samples.json"), encoding="utf-8") as fh:
        samples = json.load(fh)
    with open(os.path.join(FULL, "key.json"), encoding="utf-8") as fh:
        fullkey = json.load(fh)
    by_id = {s["sample_id"]: s for s in samples}
    rng = random.Random(SEED)

    calib_ids = []
    for fam, cnt in QUOTA.items():
        cand = [s["sample_id"] for s in samples
                if fullkey[s["sample_id"]]["family"] == fam
                and not fullkey[s["sample_id"]]["hard"]]
        cand.sort(key=lambda sid: (-typical(by_id[sid], fullkey[sid]), sid))
        # 在"最典型"的前 60% 里随机挑，避免每次都是同一批、又不至于挑到怪题
        head = cand[:max(cnt, int(len(cand) * 0.6))]
        rng.shuffle(head)
        chosen = head[:cnt]
        if len(chosen) < cnt:
            chosen += [x for x in cand if x not in chosen][:cnt - len(chosen)]
        calib_ids += chosen
    calib_ids.sort()
    formal_ids = sorted(set(by_id) - set(calib_ids))

    calib_dir = os.path.join(STUDY, "calib")
    form_dir = os.path.join(STUDY, "formal")
    src_img = os.path.join(FULL, "img")
    copy_imgs(calib_ids, src_img, os.path.join(calib_dir, "img"))
    copy_imgs(formal_ids, src_img, os.path.join(form_dir, "img"))

    calib_samples = [by_id[i] for i in calib_ids]
    form_samples = [by_id[i] for i in formal_ids]
    write_pack(calib_dir, calib_samples, {i: fullkey[i] for i in calib_ids})
    write_pack(form_dir, form_samples, {i: fullkey[i] for i in formal_ids})

    answers = {i: {"family": fullkey[i]["family"], "html": explain(by_id[i], fullkey[i])}
               for i in calib_ids}
    with open(os.path.join(calib_dir, "answers.json"), "w", encoding="utf-8") as fh:
        json.dump(answers, fh, ensure_ascii=False, indent=1)
    make_html(calib_dir, calib_samples, answers)

    from collections import Counter
    print("校准题 %d 道 → %s" % (len(calib_ids), calib_dir))
    print("  家族:", dict(Counter(fullkey[i]["family"] for i in calib_ids)))
    print("正式题 %d 道 → %s" % (len(formal_ids), form_dir))
    print("  家族:", dict(Counter(fullkey[i]["family"] for i in formal_ids)))
    print("  难题保留:", sum(1 for i in formal_ids if fullkey[i]["hard"]), "道（应 15）")
    print("校准页:", os.path.join(calib_dir, "calibration.html"))


def make_html(outdir, samples, answers):
    import base64
    from make_sheet import FAMILIES, build_card

    cards = []
    for s in samples:
        sid = s["sample_id"]
        card = build_card(s, outdir).replace(
            "</section>",
            '<div class="fb" data-f="%s"></div></section>' % sid)
        cards.append(card)

    help_rows = "".join("<tr><td><code>%s</code></td><td>%s</td><td>%s</td></tr>" % (k, l, t)
                        for k, l, t in FAMILIES)
    ans_js = json.dumps({k: {"family": v["family"], "html": v["html"]}
                         for k, v in answers.items()}, ensure_ascii=False)
    cards_html = "".join(cards)

    doc = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>校准练习 20 题</title>
<style>
:root{--bg:#f5f6f4;--card:#fff;--line:#dde1e6;--ink:#161b22;--dim:#5b6572;--acc:#0f766e;--acc-soft:#e2f1ee}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.6 -apple-system,"Segoe UI","Noto Sans SC",system-ui,sans-serif}
.bar{position:sticky;top:0;z-index:9;display:flex;align-items:center;gap:14px;padding:12px 20px;background:#fff;border-bottom:1px solid var(--line);flex-wrap:wrap}
.bar h1{font-size:15px;margin:0;font-weight:500}
.bar .prog{color:var(--acc);font-weight:500;font-variant-numeric:tabular-nums}
.bar .sp{flex:1}
button.act{font:inherit;padding:7px 13px;border:1px solid var(--acc);background:var(--acc);color:#fff;border-radius:6px;cursor:pointer}
button.act.ghost{background:#fff;color:var(--acc)}
details.help{margin:14px 20px 0;background:#fff;border:1px solid var(--line);border-radius:8px;padding:10px 14px}
details.help summary{cursor:pointer;font-weight:500;color:var(--acc)}
table.help2{border-collapse:collapse;margin-top:8px;font-size:13px}
table.help2 td{padding:4px 12px 4px 0;vertical-align:top;border-top:1px solid #eef0f2}
table.help2 code{background:var(--acc-soft);padding:1px 5px;border-radius:4px;color:#0b5d57}
.wrap{display:grid;grid-template-columns:repeat(auto-fill,minmax(430px,1fr));gap:16px;padding:16px 20px 60px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px 10px;display:flex;flex-direction:column;gap:9px}
.card.done{border-color:var(--acc)}
.card header{display:flex;align-items:baseline;gap:10px}
.card header b{font-size:15px;font-variant-numeric:tabular-nums}
.card header .meta{color:var(--dim);font-size:12px}
.imgs img{width:100%;border:1px solid #eceef1;border-radius:6px;display:block;background:#fff}
table.facts{width:100%;border-collapse:collapse;font-size:12.5px}
table.facts th{text-align:left;color:var(--dim);font-weight:400;width:64px;padding:1px 0}
table.facts td{font-variant-numeric:tabular-nums;padding:1px 0}
.nbr{margin:0;font-size:12px;color:var(--dim)}
.nbr span{display:inline-block;margin-right:6px;color:#8a94a1}
.fams{display:flex;flex-wrap:wrap;gap:5px;margin-top:2px}
.fam{font:12.5px/1 inherit;padding:6px 9px;border:1px solid var(--line);background:#fbfcfb;color:var(--ink);border-radius:5px;cursor:pointer}
.fam:hover{border-color:var(--acc);color:var(--acc)}
.fam.on{background:var(--acc);border-color:var(--acc);color:#fff}
.fam.bad{border-color:#a32d2d;background:#fcebeb;color:#a32d2d}
.unk{border-style:dashed;color:var(--dim)}
.cfs,.note{display:none}
.fb{display:none;font-size:12.5px;line-height:1.65;border-top:1px solid var(--line);padding-top:9px;margin-top:2px}
.fb.show{display:block}
.fb.ok{color:#0b5d57}
.fb.no{color:#791f1f}
.fb code{background:var(--acc-soft);padding:1px 4px;border-radius:4px;color:#0b5d57}
.foot{padding:0 20px 40px;color:var(--dim);font-size:12.5px}
</style></head><body>
<div class="bar"><h1>校准练习 20 题</h1><span class="prog" id="prog">0 / __N__</span>
<span class="sp"></span><span id="score" style="color:#5b6572;font-size:13px">首次答对 0</span>
<button class="act ghost" id="reset">重做</button><button class="act" id="save">导出记录</button></div>
<details class="help" open><summary>九类定义与判据（培训手册第 1 页）</summary>
<table class="help2">__HELP__</table>
<p style="font-size:12.5px;color:#5b6572;margin:8px 0 0">
① 圆柱面看 <code>面积/(2πRh)</code>（页面「弧段占比」）：≈0.25 是圆角、≈1.00 是整圈圆柱。<br>
② 看页面「径向点积」：<code>+1.00</code> 凸（销/凸台），<code>−1.00</code> 凹（孔/腔壁/圆角）。
凹的这一档再用 ① 分开：0.25 是圆角，1.00 是孔或腔壁。<br>
③ 平面看四周立壁法向：全朝外是凸台，朝内是腔；邻面清单给了 z 范围，可找更低的底面（有 = 孔/腔）。<br>
径向点积是 FreeCAD 在圆柱面参数中点量出来的，<b>不要自己拿页面「法向」和形心去算</b>。判不准点「判不了」。</p></details>
<div class="wrap">__CARDS__</div>
<p class="foot">每题点一下就立刻出答案和解释，答错也可以继续，不会锁定。这一轮只用于学习，<b>不计入研究数据</b>。</p>
<script>
const ANS = __ANS__;
const picks = {};
function paint(){
  let done=0, ok=0;
  document.querySelectorAll(".fb").forEach(el=>{
    const sid=el.dataset.f, a=ANS[sid], p=picks[sid];
    if(!p) return;
    done++;
    if(p.first===a.family) ok++;
    el.className="fb show "+(p.first===a.family?"ok":"no");
    el.innerHTML=(p.first===a.family?"✓ 首次答对":"✗ 首次选了 "+p.first+"　")+a.html;
  });
  document.getElementById("prog").textContent=done+" / "+Object.keys(ANS).length;
  document.getElementById("score").textContent="首次答对 "+ok+" / "+done;
}
document.querySelectorAll(".fam").forEach(b=>{
  b.addEventListener("click",()=>{
    const sid=b.dataset.s, a=ANS[sid];
    if(!picks[sid]) picks[sid]={first:b.dataset.k, cur:b.dataset.k};
    else picks[sid].cur=b.dataset.k;
    const c=document.getElementById(sid);
    c.querySelectorAll(".fam").forEach(x=>{x.classList.toggle("on",x.dataset.k===picks[sid].cur);
      x.classList.toggle("bad",x.dataset.k===picks[sid].cur && x.dataset.k!==a.family);});
    c.classList.add("done");
    paint();
  });
});
document.getElementById("reset").addEventListener("click",()=>{
  if(!confirm("清空本轮作答重新练习？")) return;
  Object.keys(picks).forEach(k=>delete picks[k]);
  document.querySelectorAll(".card").forEach(c=>{c.classList.remove("done");
    c.querySelectorAll(".fam").forEach(x=>x.classList.remove("on","bad"));});
  document.querySelectorAll(".fb").forEach(e=>e.className="fb");
  paint();
});
document.getElementById("save").addEventListener("click",()=>{
  const blob=new Blob([JSON.stringify({round:"calibration",picks:picks},null,1)],{type:"application/json"});
  const a=document.createElement("a");a.href=URL.createObjectURL(blob);
  a.download="calibration.json";a.click();
});
paint();
</script></body></html>"""

    doc = (doc.replace("__CARDS__", cards_html)
              .replace("__HELP__", help_rows)
              .replace("__ANS__", ans_js)
              .replace("__N__", str(len(samples))))
    out = os.path.join(outdir, "calibration.html")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(doc)
    print("  wrote %s (%.1f MB)" % (out, os.path.getsize(out) / 1048576.0))


if __name__ == "__main__":
    main()
