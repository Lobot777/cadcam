"""M4 编排层的状态机。

只做一件事：把「当前处于哪个阶段、允许走到哪个阶段」写死，
让越阶调用在第一时间抛错，而不是靠开发者自觉。

阶段序列（正常路径）：
    INIT → ANALYZE → PLAN → EMIT → VERIFY → ACCEPT
失败路径：
    VERIFY → REPAIR → EMIT → VERIFY ...（REPAIR 次数由 runner 的界控制）
    VERIFY / REPAIR → ESCALATE（有界重试耗尽）
"""
from __future__ import annotations

PHASES = ("INIT", "ANALYZE", "PLAN", "EMIT", "VERIFY", "REPAIR",
          "ACCEPT", "ESCALATE")

# 允许的跳转：只列合法目标，其余一律拒绝
ALLOWED = {
    "INIT": {"ANALYZE"},
    "ANALYZE": {"PLAN", "ESCALATE"},
    "PLAN": {"EMIT", "ESCALATE"},
    "EMIT": {"VERIFY", "ESCALATE"},
    "VERIFY": {"ACCEPT", "REPAIR", "ESCALATE"},
    "REPAIR": {"EMIT", "ESCALATE"},
    "ACCEPT": set(),
    "ESCALATE": set(),
}

TERMINAL = {"ACCEPT", "ESCALATE"}


class IllegalTransition(RuntimeError):
    """越阶跳转。带上 from/to 便于在 trace 里定位是哪一角色越权。"""

    def __init__(self, src, dst):
        super().__init__("非法状态跳转 %s → %s" % (src, dst))
        self.src, self.dst = src, dst


class StateMachine:
    def __init__(self):
        self.phase = "INIT"
        self.history = ["INIT"]

    def can(self, dst):
        return dst in ALLOWED[self.phase]

    def to(self, dst):
        if dst not in PHASES:
            raise ValueError("未知阶段 %s" % dst)
        if not self.can(dst):
            raise IllegalTransition(self.phase, dst)
        self.phase = dst
        self.history.append(dst)
        return dst

    def done(self):
        return self.phase in TERMINAL
