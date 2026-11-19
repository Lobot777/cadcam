"""生成标注复核页：reports/annotation/review.html

页面只做一件事 —— 让人能在 5 分钟内判断"这条标注对不对"：
左边是带面号的八视图，右边是特征表（家族 / 面号 / 依据），
下面挂逐面高亮图。所有图片走相对路径，直接双击 html 就能看。
"""

import html
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LABELS = os.path.join(ROOT, "data", "zenodo_rptu", "labels")
OUT = os.path.join(ROOT, "reports", "annotation", "review.html")

FAMILY_COLOR = {
    "stock": "#9aa5b1", "hole": "#2f80ed", "pocket": "#eb5757", "slot": "#f2994a",
    "step": "#27ae60", "boss": "#9b51e0", "fillet": "#56ccf2", "chamfer": "#f2c94c",
    "freeform": "#bdbdbd", "unknown": "#828282",
}


def part_block(part, ann):
    fid_rows = []
    for f in ann["features"]:
        color = FAMILY_COLOR.get(f["family"], "#666")
        faces = ", ".join(str(i) for i in f["faces"])
        cls = f["mfcad_class"] or "<em>null</em>"
        fid_rows.append(
            "<tr><td>%s</td><td><span class='tag' style='background:%s'>%s</span></td>"
            "<td>%s</td><td>%s</td><td class='note'>%s</td></tr>"
            % (f["id"], color, f["family"], cls, faces, html.escape(f.get("note", ""))))
    stock = ann.get("stock_faces", [])
    questions = ann.get("open_questions", [])
    q_html = ""
    if questions:
        q_html = "<div class='q'><b>待人工确认</b><ul>%s</ul></div>" % "".join(
            "<li>%s</li>" % html.escape(q) for q in questions)
    badge = ("<span class='badge warn'>需复核</span>" if ann.get("review_needed")
             else "<span class='badge ok'>已确认</span>")
    return """
<section id="p{part}">
  <h2>件 {part} {badge}<small>{n} 面 / {nf} 特征 / 来源 {src}</small></h2>
  <div class="grid">
    <figure><img src="{part}_views.png"><figcaption>八视图（面号 = JSON 里的 index）</figcaption></figure>
    <div>
      <table>
        <thead><tr><th>ID</th><th>家族</th><th>MFCAD 类</th><th>面号</th><th>判据 / 备注</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
      <p class="stock">stock 面（{ns} 个）：{stock}</p>
      {q}
    </div>
  </div>
  <details><summary>逐面高亮（确认"这个面到底是哪个部位"时用）</summary>
    <img class="sheet" src="{part}_faces.png">
  </details>
</section>
""".format(part=part, badge=badge, n=ann["n_faces"], nf=len(ann["features"]),
           src=ann.get("source", ""), rows="".join(fid_rows), ns=len(stock),
           stock=", ".join(str(s) for s in stock) if stock else "无",
           q=q_html)


def main(argv):
    parts = [str(i) for i in range(1, 11)]
    blocks, nav = [], []
    total_faces = total_feat = 0
    need = []
    for part in parts:
        path = os.path.join(LABELS, "%s.json" % part)
        with open(path, encoding="utf-8") as fh:
            ann = json.load(fh)
        total_faces += ann["n_faces"]
        total_feat += len(ann["features"])
        if ann.get("review_needed"):
            need.append(part)
        blocks.append(part_block(part, ann))
        nav.append("<a href='#p%s'>件 %s</a>" % (part, part))

    doc = """<!doctype html>
<html lang="zh-CN"><meta charset="utf-8">
<title>真实件面级标注复核</title>
<style>
 body{font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;margin:0;background:#f5f6f8;color:#1a1a1a}
 header{background:#1f2933;color:#fff;padding:18px 28px}
 header h1{margin:0 0 6px;font-size:19px}
 header p{margin:0;opacity:.75;font-size:13px}
 nav{padding:10px 28px;background:#fff;border-bottom:1px solid #e3e6ea;position:sticky;top:0;z-index:5}
 nav a{margin-right:14px;color:#2f80ed;text-decoration:none;font-size:13px}
 section{background:#fff;margin:18px 28px;padding:16px 20px;border-radius:8px;border:1px solid #e3e6ea}
 h2{font-size:16px;margin:0 0 12px}
 h2 small{font-weight:400;color:#777;margin-left:10px}
 .grid{display:grid;grid-template-columns:minmax(420px,1fr) minmax(420px,1fr);gap:18px;align-items:start}
 figure{margin:0}
 img{max-width:100%;border:1px solid #e3e6ea;border-radius:4px;background:#fff}
 figcaption{font-size:12px;color:#777;margin-top:4px}
 table{width:100%;border-collapse:collapse;font-size:13px}
 th,td{border-bottom:1px solid #eef0f3;padding:5px 6px;text-align:left;vertical-align:top}
 th{background:#fafbfc;font-weight:600;color:#444}
 .tag{color:#fff;padding:1px 7px;border-radius:9px;font-size:12px}
 .note{color:#555}
 .stock{font-size:12px;color:#666;margin-top:10px}
 .badge{font-size:12px;padding:2px 8px;border-radius:10px;margin-left:6px;vertical-align:2px}
 .badge.ok{background:#e3f7ea;color:#1e7b3c}
 .badge.warn{background:#fdecea;color:#b3261e}
 .q{background:#fff8e6;border-left:3px solid #f2c94c;padding:8px 12px;margin-top:12px;font-size:13px}
 .q ul{margin:6px 0 0 18px;padding:0}
 details{margin-top:14px}
 summary{cursor:pointer;color:#2f80ed;font-size:13px}
 .sheet{margin-top:10px}
 @media(max-width:1100px){.grid{grid-template-columns:1fr}}
</style>
<header>
  <h1>真实零件面级标注复核（Zenodo RPTU 锚点集）</h1>
  <p>共 __FACES__ 个面 / __FEATS__ 个特征实例｜标注口径：9 类家族 + MFCAD 25 类（不可判定时为 null）｜
     面号与 STEP 的面顺序一一对应，可直接回 FreeCAD 核对</p>
</header>
<nav>__NAV__</nav>
__BODY__
</html>
"""
    doc = (doc.replace("__FACES__", str(total_faces))
              .replace("__FEATS__", str(total_feat))
              .replace("__NAV__", "".join(nav))
              .replace("__BODY__", "".join(blocks)))
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(doc)
    print("写出 %s（%d 面 / %d 特征；需复核的件：%s）"
          % (os.path.relpath(OUT, ROOT), total_faces, total_feat, need or "无"))


if __name__ == "__main__":
    main(sys.argv[1:])
