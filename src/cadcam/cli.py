"""命令行入口：几何摘要 / 面表下钻 / G 代码解析。

    python -m cadcam.cli summary data/zenodo_rptu/1.step
    python -m cadcam.cli faces   data/zenodo_rptu/1.step --page 1
    python -m cadcam.cli gcode   data/zenodo_rptu/1.ngc
"""

import argparse
import json
import sys

from cadcam.adapters import freecad
from cadcam.modalities.brep import digest
from cadcam.modalities.cam import ngc


def cmd_summary(args):
    geom = freecad.geometry(args.step)
    summary = digest.summarize(geom)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(digest.render_l0(summary))
    return 0


def cmd_faces(args):
    geom = freecad.geometry(args.step)
    page = digest.face_page(geom, page=args.page, size=args.size)
    print(json.dumps(page, ensure_ascii=False, indent=2))
    return 0


def cmd_detail(args):
    geom = freecad.geometry(args.step)
    print(json.dumps(digest.face_detail(geom, args.face_id), ensure_ascii=False, indent=2))
    return 0


def cmd_gcode(args):
    with open(args.ngc, "r", encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    result = ngc.parse(text)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    stats = result["stats"]
    print("程序 %s" % args.ngc)
    print("块数 %d，刀路段 %d，刀具 %s" % (result["blocks"], stats["segments"], result["tools"]))
    print("切削 %.0f mm，空行程 %.0f mm，空切比 %.2f" % (
        stats["cut_mm"], stats["rapid_mm"], stats["air_cut_ratio"] or 0))
    print("Z 范围 %.2f ~ %.2f mm，插补平面 %s" % (stats["z_min"], stats["z_max"], ",".join(stats["planes"])))
    print("工序 %d 道：" % len(result["operations"]))
    for op in result["operations"]:
        print("  - %-14s 刀 T%s  段 %3d  切 %7.1f mm" % (op["label"], op["tool"], op["segments"], op["cut_mm"]))
    if result["unknown_codes"]:
        print("未登记指令: %s" % result["unknown_codes"])
    if result["warnings"]:
        print("告警 %d 条: %s" % (len(result["warnings"]), result["warnings"][:5]))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="cadcam")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("summary", help="打印 L0 零件摘要")
    p.add_argument("step")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_summary)

    p = sub.add_parser("faces", help="打印 L2 面表分页")
    p.add_argument("step")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--size", type=int, default=20)
    p.set_defaults(func=cmd_faces)

    p = sub.add_parser("detail", help="打印 L3 单面细节")
    p.add_argument("step")
    p.add_argument("face_id")
    p.set_defaults(func=cmd_detail)

    p = sub.add_parser("gcode", help="解析 G 代码并统计")
    p.add_argument("ngc")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_gcode)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
