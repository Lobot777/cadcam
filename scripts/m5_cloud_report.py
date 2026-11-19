"""把本地量化模型与云端模型的对照合并成一张表（论文 §5.6 用）。

读数不造数：本地取 `reports/m5_model_compare.json`，云端取
`reports/m5_cloud_compare.json`，两边判据相同（30 条难工单 + 同一份错误起点）。

    PYTHONPATH=src python scripts/m5_cloud_report.py
    PYTHONPATH=src python scripts/m5_cloud_report.py --markdown   # 只出 markdown
"""
from __future__ import annotations

import argparse
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL = os.path.join(ROOT, "reports", "m5_model_compare.json")
CLOUD = os.path.join(ROOT, "reports", "m5_cloud_compare.json")
OUT = os.path.join(ROOT, "reports", "m5_all_models.json")
EXTRA_LOCAL = os.path.join(ROOT, "reports", "m5_extra_local.json")
EXTRA_CLOUD = os.path.join(ROOT, "reports", "m5_extra_compare.json")

# 云端模型的中文厂商名（目录里 vendor 是单字母代码，对人类不可读）
VENDOR = {
    "deepseek-v4.1-flash": "DeepSeek",
    "deepseek-v4-pro": "DeepSeek",
    "glm-5.3-flash": "智谱",
    "glm-5.3": "智谱",
    "kimi-k2.8-preview": "月之暗面",
    "kimi-k3.1-pro": "月之暗面",
    "minimax-m3.1-flash": "MiniMax",
    "step-5-preview": "阶跃星辰",
    "mimo-v2.6-pro": "小米",
    "hunyuan-2.0-thinking": "腾讯",
    "space-bunny": "匿名",
    "hy4std": "腾讯",
}
PARAMS = {                       # 本地模型的参数量（云端不公开，不猜）
    "qwen3:8b": "8.2B", "qwen3:14b": "14.8B", "llama3.1:8b": "8.0B",
    "glm4:9b": "9.4B", "gemma3:12b": "12.2B",
}


def load(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh).get("models", {})


def row(name, entry, kind):
    o = entry.get("orders") or {}
    r = entry.get("repair") or {}
    v = r.get("variants") or {}
    meter = entry.get("meter") or {}
    return {
        "model": name,
        "vendor": VENDOR.get(name, "本地" if kind == "local" else "?"),
        "kind": kind,
        "params": PARAMS.get(name, "—"),
        "exec": o.get("exec", 0), "n": o.get("n", 0),
        "legal": o.get("legal_rate"),
        "iou": o.get("mean_removed_iou"),
        "ge09": o.get("removed_ge_0_9", 0),
        "vague": (v.get("vague") or {}).get("solved"),
        "struct": (v.get("structured") or {}).get("solved"),
        "coords": (v.get("structured_coords") or {}).get("solved"),
        "rep_n": (v.get("vague") or {}).get("n"),
        "seconds": entry.get("seconds"),
        "total_tokens": meter.get("total_tokens"),
        "quota": (entry.get("quota") or {}).get("percent"),
    }


def collect(which="hard"):
    local_p, cloud_p = ((EXTRA_LOCAL, EXTRA_CLOUD) if which == "extra"
                        else (LOCAL, CLOUD))
    rows = []
    for name, e in load(local_p).items():
        if (e.get("orders") or {}).get("n"):
            rows.append(row(name, e, "local"))
    for name, e in load(cloud_p).items():
        if (e.get("orders") or {}).get("n"):
            rows.append(row(name, e, "cloud"))
    rows.sort(key=lambda r: (-(r["iou"] or 0), r["model"]))
    return rows


def to_markdown(rows):
    head = ("| 模型 | 厂商 | 参数量 | 执行 | 合法率 | 特征 IoU | ≥0.9 | "
            "纠错 vague / struct / +坐标 |\n|---|---|---|---|---|---|---|---|")
    lines = [head]
    for r in rows:
        rn = r["rep_n"] or 0
        lines.append("| %s%s | %s | %s | %d/%d | %s | %s | %d/%d | %s / %s / %s |"
                     % (r["model"], "" if r["kind"] == "cloud" else "（本地 Q4）",
                        r["vendor"], r["params"], r["exec"], r["n"],
                        "%.3f" % r["legal"] if r["legal"] is not None else "—",
                        "%.3f" % r["iou"] if r["iou"] is not None else "—",
                        r["ge09"], r["n"],
                        "%s/%d" % (r["vague"], rn) if r["vague"] is not None else "—",
                        "%s/%d" % (r["struct"], rn) if r["struct"] is not None else "—",
                        "%s/%d" % (r["coords"], rn) if r["coords"] is not None else "—"))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--markdown", action="store_true")
    ap.add_argument("--set", default="hard", choices=("hard", "extra"),
                    help="hard=30 条主集；extra=20 条更难的第二套")
    args = ap.parse_args()
    rows = collect(args.set)
    if args.markdown:
        print(to_markdown(rows))
        return 0
    out_path = OUT if args.set == "hard" else os.path.join(
        ROOT, "reports", "m5_extra_all.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({"set": args.set, "rows": rows, "markdown": to_markdown(rows)},
                  fh, ensure_ascii=False, indent=1)
    print(to_markdown(rows))
    cloud = [r for r in rows if r["kind"] == "cloud"]
    local = [r for r in rows if r["kind"] == "local"]
    if cloud and local:
        best_l = max(local, key=lambda r: r["iou"] or 0)
        best_c = max(cloud, key=lambda r: r["iou"] or 0)
        print("\n本地最好 %s %.3f｜云端最好 %s %.3f｜差 %+.3f"
              % (best_l["model"], best_l["iou"], best_c["model"], best_c["iou"],
                 best_c["iou"] - best_l["iou"]))
        tot = sum(r["total_tokens"] or 0 for r in cloud)
        print("云端合计 %d token（%d 个模型）" % (tot, len(cloud)))
    print("→ %s" % os.path.relpath(out_path, ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
