"""生成人工盲标页（单文件 HTML，离线可用，可导出 JSON）。

用法：
  # 30 题自检包（旧行为，默认）
  python scripts/annotate/make_sheet.py

  # 正式研究第一轮 T0（无培训）
  python scripts/annotate/make_sheet.py --src reports/annotation/study/full \
      --out reports/annotation/study/full/annotate_T0.html --round T0

  # 正式研究第二轮 T1（培训后）
  python scripts/annotate/make_sheet.py --src reports/annotation/study/full \
      --out reports/annotation/study/full/annotate_T1.html --round T1 --shuffle 7

设计取向：一屏一卡、点选七类、键盘可选；几何量全部摆出来，让判读有据可依；
不出现任何既有标签（盲标的前提），也不出现零件号（避免"这是件 10 我知道"）。

研究版额外记录：受试者编号、每题耗时（首次进入视口 → 首次点选）、置信度、作答顺序。
"""

import argparse
import base64
import json
import os
import random

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAMILIES = [
    ("stock", "毛坯原始面", "平面，法向为轴向，面落在零件包围盒上"),
    ("hole", "孔", "整圈圆柱壁（弧段占比≈1.00），或与之配套的孔底平面"),
    ("pocket", "型腔", "有底 + 被壁围合，材料被挖掉"),
    ("slot", "槽", "细长，两侧壁平行，端部开口或封闭"),
    ("step", "台阶", "一级高度差的肩面 + 竖壁"),
    ("boss", "凸台/岛/销", "凸出的实体：整圈圆柱带顶盖，或四壁法向朝外"),
    ("fillet", "圆角", "切向过渡，面积/(2πRh) ≈ 0.25"),
    ("chamfer", "倒角", "窄斜面，与邻面成固定角度（常见 45°）｜本批 186 题不出现"),
    ("freeform", "自由曲面", "BSpline/Bezier，无固定半径｜本批 186 题不出现"),
]


def build_card(s, src):
    f = s["face"]
    n = f["normal"]
    bb = f["bbox_mm"]
    r = f.get("params", {}).get("radius")
    facts = [
        ("面型", f["surface"]),
        ("面积", "%.1f mm²" % f["area_mm2"]),
        ("法向", "(%.2f, %.2f, %.2f)" % tuple(n) if n else "—"),
        ("z 范围", "%.1f → %.1f" % (bb[2], bb[5])),
        ("包围盒", "x[%.0f,%.0f] y[%.0f,%.0f]" % (bb[0], bb[3], bb[1], bb[4])),
        ("形心", "(%.1f, %.1f, %.1f)" % tuple(f["centroid_mm"])),
    ]
    if r:
        facts.append(("半径", "%.2f mm" % r))
        h = bb[5] - bb[2]
        if h > 1e-6:
            frac = f["area_mm2"] / (2 * 3.141592653589793 * r * h)
            facts.append(("弧段占比", "%.2f  （0.25≈圆角 / 1.00≈整圆柱）" % frac))
    rd = f.get("radial_dot")
    if rd is not None:
        facts.append(("径向点积", "%+.2f" % rd))

    def _num(v):
        t = ("%.2f" % v).rstrip("0").rstrip(".")
        return "0" if t in ("", "-0") else t

    def _nbr_txt(x):
        z = x.get("z_mm")
        zs = ""
        if z:
            zs = (" z%.0f" % z[0] if abs(z[0] - z[1]) < 0.05
                  else " z%.0f–%.0f" % (z[0], z[1]))
        n = x.get("normal") or []
        ns = (" n(%s)" % ",".join(_num(v) for v in n)) if n else ""
        return "%s %.0f%s%s" % (x["surface"][:4], x["area_mm2"], zs, ns)
    nbr = " · ".join(_nbr_txt(x) for x in s["neighbors"][:8])

    with open(os.path.join(src, "img", "%s.png" % s["sample_id"]), "rb") as ih:
        b64 = base64.b64encode(ih.read()).decode("ascii")
    btns = "".join(
        '<button class="fam" data-k="%s" data-s="%s" title="%s">%s</button>'
        % (k, s["sample_id"], tip, label) for k, label, tip in FAMILIES)
    rows = "".join("<tr><th>%s</th><td>%s</td></tr>" % (k, v) for k, v in facts)
    conf = "".join('<button class="cf" data-c="%d" data-s="%s">%s</button>'
                   % (c, s["sample_id"], t)
                   for c, t in ((1, "确定"), (2, "拿不准"), (3, "猜的")))
    return """
<section class="card" id="%(sid)s">
  <header><b>%(sid)s</b><span class="meta">%(surf)s · %(area).0f mm²</span></header>
  <div class="imgs">
    <img src="%(img)s" alt="%(sid)s">
  </div>
  <table class="facts">%(rows)s</table>
  <p class="nbr"><span>邻面</span>%(nbr)s</p>
  <div class="fams">%(btns)s<button class="fam unk" data-k="unknown" data-s="%(sid)s">判不了</button></div>
  <div class="cfs"><span>把握</span>%(conf)s</div>
  <textarea class="note" data-s="%(sid)s" rows="1" placeholder="备注（可选）：缺什么信息、为什么犹豫"></textarea>
</section>""" % {"sid": s["sample_id"], "surf": f["surface"], "area": f["area_mm2"],
                 "rows": rows, "nbr": nbr or "—", "btns": btns, "conf": conf,
                 "img": "data:image/png;base64," + b64}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.path.join(ROOT, "reports", "annotation", "kappa", "blind"),
                    help="含 samples.json 与 img/ 的目录")
    ap.add_argument("--out", default=None, help="输出 html 路径（默认 <src>/annotate.html）")
    ap.add_argument("--round", default="", help="轮次标记，如 T0 / T1（写进导出 JSON）")
    ap.add_argument("--shuffle", type=int, default=0, help="题序随机种子，0=保持原序")
    ap.add_argument("--title", default=None)
    ap.add_argument("--no-tips", action="store_true",
                    help="隐去页首的'两条判据'提示（T0 裸标轮用；类名与一句话定义仍保留）")
    args = ap.parse_args()

    with open(os.path.join(args.src, "samples.json"), encoding="utf-8") as fh:
        samples = json.load(fh)
    if args.shuffle:
        random.Random(args.shuffle).shuffle(samples)

    cards = "".join(build_card(s, args.src) for s in samples)
    header_help = "".join(
        "<tr><td><code>%s</code></td><td>%s</td><td>%s</td></tr>" % (k, lab, tip)
        for k, lab, tip in FAMILIES)

    TIPS = "" if args.no_tips else (
        "  <p style=\"font-size:12.5px;color:#5b6572;margin:8px 0 0\">\n"
        "  圆柱面用两条判据：<b>①</b> <code>面积/(2πRh)</code>（页面「弧段占比」）——"
        "≈0.25 是圆角，≈1.00 是整圈圆柱。<br>\n"
        "  <b>②</b> 页面「径向点积」：<code>+1.00</code> 凸（销/凸台），"
        "<code>−1.00</code> 凹（孔/腔壁/圆角）。凹的这一档再用 ① 分开："
        "0.25 是圆角，1.00 是孔或腔壁。<br>\n"
        "  平面：四周立壁法向全朝外是凸台、朝内是腔；邻面清单给了每个邻面的 z 范围，"
        "可用来找更低的底面（有 = 孔/腔）。<br>\n"
        "  注意：径向点积只对圆柱面有意义；页面「法向」是另一样东西，"
        "<b>不要拿它和形心做点积</b>。判不准就点「判不了」，是合法答案。</p>\n")

    rnd = args.round.strip()
    title = args.title or ("盲标 %d 个面%s" % (len(samples), "（" + rnd + "）" if rnd else ""))
    out = args.out or os.path.join(args.src, "annotate%s.html" % ("_" + rnd if rnd else ""))

    doc = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>__TITLE__</title>
<style>
:root{--bg:#f5f6f4;--card:#fff;--line:#dde1e6;--ink:#161b22;--dim:#5b6572;--acc:#0f766e;--acc-soft:#e2f1ee}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
 font:14px/1.6 -apple-system,"Segoe UI","Noto Sans SC",system-ui,sans-serif}
.bar{position:sticky;top:0;z-index:9;display:flex;align-items:center;gap:10px;
 padding:12px 20px;background:#fff;border-bottom:1px solid var(--line);flex-wrap:wrap}
.bar h1{font-size:15px;margin:0;font-weight:600;letter-spacing:.2px}
.bar .prog{font-variant-numeric:tabular-nums;color:var(--acc);font-weight:600}
.bar .sp{flex:1}
.bar input#pid{font:inherit;padding:6px 9px;border:1px solid var(--line);border-radius:6px;width:130px}
button.act{font:inherit;padding:7px 13px;border:1px solid var(--acc);background:var(--acc);
 color:#fff;border-radius:6px;cursor:pointer}
button.act.ghost{background:#fff;color:var(--acc)}
button.act:disabled{opacity:.45;cursor:not-allowed}
details.help{margin:14px 20px 0;background:#fff;border:1px solid var(--line);border-radius:8px;
 padding:10px 14px}
details.help summary{cursor:pointer;font-weight:600;color:var(--acc)}
table.help2{border-collapse:collapse;margin-top:8px;font-size:13px}
table.help2 td{padding:4px 12px 4px 0;vertical-align:top;border-top:1px solid #eef0f2}
table.help2 code{background:var(--acc-soft);padding:1px 5px;border-radius:4px;color:#0b5d57}
.wrap{display:grid;grid-template-columns:repeat(auto-fill,minmax(430px,1fr));gap:16px;padding:16px 20px 60px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px 10px;
 display:flex;flex-direction:column;gap:9px}
.card.done{border-color:var(--acc);box-shadow:0 0 0 1px var(--acc-soft) inset}
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
.fam{font:12.5px/1 inherit;padding:6px 9px;border:1px solid var(--line);background:#fbfcfb;
 color:var(--ink);border-radius:5px;cursor:pointer}
.fam:hover{border-color:var(--acc);color:var(--acc)}
.fam.on{background:var(--acc);border-color:var(--acc);color:#fff}
.unk{border-style:dashed;color:var(--dim)}
.cfs{display:flex;gap:5px;align-items:center;font-size:12px;color:#8a94a1}
.cf{font:12px/1 inherit;padding:5px 8px;border:1px solid #e7eaee;background:#fff;color:var(--dim);
 border-radius:5px;cursor:pointer}
.cf.on{border-color:#b45309;background:#fff7ed;color:#92400e}
.note{width:100%;border:1px solid #e7eaee;border-radius:5px;padding:5px 7px;font:12.5px/1.5 inherit;
 resize:vertical;background:#fcfdfc}
.foot{padding:0 20px 40px;color:var(--dim);font-size:12.5px}
</style></head><body>

<div class="bar">
  <h1>__TITLE__</h1>
  <span class="prog" id="prog">0 / __COUNT__</span>
  <span class="sp"></span>
  <input id="pid" placeholder="受试者编号" value="">
  <button class="act ghost" id="clear">清空</button>
  <button class="act" id="save">导出 JSON</button>
</div>

<details class="help" open>
  <summary>九类怎么判（点开/收起）——判不准就点"判不了"，低置信不扣分</summary>
  <table class="help2">__HELP__</table>
__TIPS__</details>

<div class="wrap">__CARDS__</div>
<p class="foot">标完点右上"导出 JSON"，文件名会自动带上受试者编号与轮次（如 <code>P1_T0.json</code>）。
页面状态按受试者编号分别存在浏览器本地，关掉再打开不会丢；换人前请先改编号。</p>

<script>
const ROUND = "__ROUND__";
const total = __COUNT__;
let state = {}, KEY = "";

const pidEl = document.getElementById("pid");
function keyFor() {
  const p = (pidEl.value || "").trim() || "anon";
  return "cadcam-study-v1:" + p + ":" + (ROUND || "self");
}
function reload() {
  KEY = keyFor();
  try { state = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { state = {}; }
  document.querySelectorAll(".note").forEach(t => { t.value = (state[t.dataset.s] || {}).reason || ""; });
  paint();
}
function persist() { localStorage.setItem(KEY, JSON.stringify(state)); }

// 每题计时：卡片首次进入视口 → 首次点选
const t0 = {};
const io = new IntersectionObserver(es => es.forEach(e => {
  if (e.isIntersecting && !t0[e.target.id]) t0[e.target.id] = performance.now();
}), {threshold: 0.5});
document.querySelectorAll(".card").forEach(c => io.observe(c));

function paint() {
  let n = 0;
  document.querySelectorAll(".card").forEach(c => {
    const st = state[c.id] || {};
    if (st.family) n++;
    c.classList.toggle("done", !!st.family);
    c.querySelectorAll(".fam").forEach(b => b.classList.toggle("on", b.dataset.k === st.family));
    c.querySelectorAll(".cf").forEach(b => b.classList.toggle("on", +b.dataset.c === st.confidence));
  });
  document.getElementById("prog").textContent = n + " / " + total;
}

document.querySelectorAll(".fam").forEach(b => {
  b.addEventListener("click", () => {
    const sid = b.dataset.s;
    const st = state[sid] = state[sid] || {};
    st.family = (st.family === b.dataset.k) ? null : b.dataset.k;
    if (st.family && st.latency_s == null) {
      const start = t0[sid] || performance.now();
      st.latency_s = +Math.max(0, (performance.now() - start) / 1000).toFixed(1);
    }
    if (!st.family) { delete st.latency_s; delete st.confidence; }
    persist(); paint();
  });
});
document.querySelectorAll(".cf").forEach(b => {
  b.addEventListener("click", () => {
    const sid = b.dataset.s;
    const st = state[sid] = state[sid] || {};
    st.confidence = (st.confidence === +b.dataset.c) ? null : +b.dataset.c;
    persist(); paint();
  });
});
document.querySelectorAll(".note").forEach(t => {
  t.addEventListener("input", () => {
    const st = state[t.dataset.s] = state[t.dataset.s] || {};
    st.reason = t.value;
    persist();
  });
});
pidEl.addEventListener("change", reload);

document.getElementById("clear").addEventListener("click", () => {
  if (!confirm("清空当前受试者（" + ((pidEl.value || "").trim() || "anon") + "）的全部标注？")) return;
  state = {}; persist(); document.querySelectorAll(".note").forEach(t => t.value = ""); paint();
});

document.getElementById("save").addEventListener("click", () => {
  const order = {};
  document.querySelectorAll(".card").forEach((c, i) => order[c.id] = i + 1);
  const items = Object.keys(state)
    .filter(k => state[k] && state[k].family)
    .sort()
    .map(k => ({sample_id: k, family: state[k].family,
                confidence: state[k].confidence || null,
                latency_s: state[k].latency_s != null ? state[k].latency_s : null,
                order: order[k] || null,
                mfcad_class: null,
                reason: state[k].reason || ""}));
  const payload = {annotator: (pidEl.value || "").trim() || "anon",
                   round: ROUND || null, count: items.length, items: items};
  const blob = new Blob([JSON.stringify(payload, null, 1)], {type: "application/json"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = payload.annotator + "_" + (ROUND || "self") + ".json";
  a.click();
  if (items.length < total) {
    alert("只导出了 " + items.length + " / " + total + " 条，剩下的还没标。");
  }
});

reload();
</script>
</body></html>"""

    doc = (doc.replace("__CARDS__", cards)
              .replace("__HELP__", header_help)
              .replace("__COUNT__", str(len(samples)))
              .replace("__TITLE__", title)
              .replace("__ROUND__", rnd)
              .replace("__TIPS__", TIPS))
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(doc)
    print("wrote %s  (%.1f MB, %d cards, round=%s)"
          % (out, os.path.getsize(out) / 1048576.0, len(samples), rnd or "-"))


if __name__ == "__main__":
    main()
