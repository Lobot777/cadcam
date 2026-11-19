"""M4 黑板：角色之间唯一的共享介质。

三条约束（对应 M4 的「黑板」验收）：
1. **无直连**：角色只读写黑板，不互相调用；
2. **可追溯**：每次写入和每次决策都进事件日志，带角色名与序号；
3. **可序列化**：日志与摘要必须能落 JSON（numpy 数组只存形状与统计量）。
"""
from __future__ import annotations

import hashlib
import json

import numpy as np


def _jsonable(value):
    """把 numpy / 大数组压成可落盘的摘要，避免每次 trace 都存几 MB。"""
    if isinstance(value, np.ndarray):
        finite = value[np.isfinite(value)]
        return {"__ndarray__": list(value.shape),
                "finite": int(finite.size),
                "min": float(finite.min()) if finite.size else None,
                "max": float(finite.max()) if finite.size else None}
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


INPUT_KEYS = ("geom", "labels")


class Blackboard:
    """任务级共享状态。一个任务一块黑板，任务之间不共享。"""

    def __init__(self, task):
        self.task = dict(task)
        self._data = {}
        # 任务输入直接上板，角色不必去翻 task 字典——读写只有一条通路
        for key in INPUT_KEYS:
            if key in task:
                self._data[key] = task[key]
        self.events = []
        self._seq = 0

    # ---- 读写 ----
    def put(self, key, value, role):
        self._data[key] = value
        self.log(role, "put", key=key, value=_jsonable(value)
                 if not isinstance(value, (dict, list)) else "<obj>")

    def get(self, key, default=None):
        return self._data.get(key, default)

    def require(self, key, role):
        if key not in self._data:
            raise KeyError("角色 %s 需要 %s，但黑板上没有（前序角色未产出）"
                           % (role, key))
        return self._data[key]

    # ---- 事件 ----
    def log(self, role, action, **detail):
        self._seq += 1
        self.events.append({"seq": self._seq, "role": role, "action": action,
                            "detail": _jsonable(detail)})
        return self._seq

    def digest(self):
        """黑板内容指纹：用于「同一任务重跑结果是否一致」的自检。"""
        blob = json.dumps(_jsonable(self._data), sort_keys=True,
                          ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()[:16]

    def dump(self):
        return {"task": _jsonable(self.task), "events": self.events,
                "digest": self.digest()}
