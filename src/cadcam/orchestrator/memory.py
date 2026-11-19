"""M4 情节记忆：把「哪类失败 → 哪个修法有效」记下来，下次直接开局就用。

刻意做成**按失败签名检索**而不是按零件号检索：同族零件（多级腔、含凸台）
的失败签名相同，第二件开始就该少走一轮。这是 M4「记忆」验收的可测形式：
冷启动 vs 热启动的修复轮次差。
"""
from __future__ import annotations

import json
import os


def failure_signature(task, diagnostics):
    """稳定签名 = 零件族 + 错误码集合 + 过切工序占比档。

    不含零件号、不含绝对数值，保证「换一件同类零件」也能命中。
    """
    codes = sorted({str(e.get("code")) for e in diagnostics if e.get("code")})
    family = str(task.get("family", "unknown"))
    return "%s|%s" % (family, ",".join(codes) if codes else "ok")


def gouge_bucket(plant):
    """过切工序占比分档：把连续量离散成 0 / few / many。"""
    if not plant or plant <= 0.0:
        return "0"
    if plant < 0.5:
        return "few"
    return "many"


class EpisodicMemory:
    """按**零件族**检索修法，按**失败签名**留档。

    为什么要分两个键：修法要能在「没跑之前」查得到（否则无法热启动），
    而失败签名依赖跑出来的诊断（要等 Verifier 之后才知道）。所以检索键用
    任务自带的零件族，签名只作为审计字段随条目存下来。
    """

    def __init__(self, entries=None):
        # entries: {family: {"fix": {...}, "seen": n, "ok": n, "rounds": n,
        #                    "signatures": [...]}}
        self.entries = dict(entries or {})

    # ---- 检索 ----
    def hint(self, family):
        e = self.entries.get(str(family))
        if not e or not e.get("fix"):
            return None
        if e.get("ok", 0) <= 0:
            return None          # 只记成功过的修法，失败经验不当作建议
        return dict(e["fix"])

    # ---- 记录 ----
    def record(self, family, fix, ok, rounds, signature=None):
        e = self.entries.setdefault(str(family),
                                    {"fix": None, "seen": 0, "ok": 0,
                                     "rounds": 0, "signatures": []})
        e["seen"] += 1
        e["rounds"] += int(rounds)
        if signature and signature not in e["signatures"]:
            e["signatures"].append(signature)
        if ok:
            e["ok"] += 1
            e["fix"] = dict(fix) if fix else e["fix"]
        return e

    # ---- 落盘 ----
    def save(self, path):
        d = os.path.dirname(os.path.abspath(path))
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.entries, fh, ensure_ascii=False, indent=1, sort_keys=True)

    @classmethod
    def load(cls, path):
        if not os.path.exists(path):
            return cls()
        with open(path, encoding="utf-8") as fh:
            return cls(json.load(fh))

    def summary(self):
        n = len(self.entries)
        learned = sum(1 for e in self.entries.values() if e.get("fix"))
        return {"signatures": n, "with_fix": learned,
                "total_seen": sum(e.get("seen", 0) for e in self.entries.values())}
