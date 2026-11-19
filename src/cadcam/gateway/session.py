"""会话与事务：让 CAD 建模具备"可回滚的原子动作"语义。

为什么这是 M1 的核心而不是工程细节：
LLM 规划出的动作序列经常在第三四步才暴露问题（比如型腔切穿了工件）。
如果每步都直接改同一个模型，模型就变成"一步错、后面全错"的脏状态，
智能体只能从头再来——这会让实验里的成功率被工程噪声主导。

引入快照后，每个动作是一个事务：
  前置校验 → 快照 → 执行 → 后置校验 →（不通过则）自动复原
回滚的正确性不靠"文件复制回去了"，而靠几何指纹 `state_hash` 验证
形状真的回到了快照点。
"""

import json
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from cadcam.adapters import freecad, model
from cadcam.gateway.contracts import ErrorCode, ToolError
from cadcam.modalities.brep import digest


@dataclass
class Snapshot:
    id: str
    label: str
    path: Path
    state_hash: str
    created_at: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "state_hash": self.state_hash,
            "created_at": self.created_at,
        }


@dataclass
class Session:
    root: Path
    current: Path | None = None
    geometry: dict | None = None
    snapshots: list = field(default_factory=list)
    history: list = field(default_factory=list)
    stats: dict = field(default_factory=lambda: {"calls": 0, "errors": 0, "rollbacks": 0})

    # ---------- 状态 ----------

    def state(self) -> dict:
        if self.geometry is None:
            return {"state_hash": None, "empty": True}
        counts = self.geometry.get("counts", {})
        return {
            "state_hash": digest.state_hash(self.geometry),
            "faces": counts.get("faces"),
            "edges": counts.get("edges"),
            "volume_mm3": self.geometry.get("volume_mm3"),
            "size_mm": _size(self.geometry),
            "snapshots": len(self.snapshots),
        }

    def summary(self) -> dict:
        if self.geometry is None:
            return {"level": "L0", "empty": True,
                    "hint": "当前没有几何，请先调用 cad.create_box 建毛坯"}
        data = digest.summarize(self.geometry)
        data["state_hash"] = digest.state_hash(self.geometry)
        return data

    # ---------- 快照 ----------

    def snapshot(self, label: str = "") -> Snapshot:
        if self.current is None:
            raise ToolError(ErrorCode.STATE, "当前没有几何，无法快照",
                            hint="先建毛坯：cad.create_box")
        index = len(self.snapshots) + 1
        target = self.root / "snapshots" / ("%02d_%s.step" % (index, uuid.uuid4().hex[:6]))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.current, target)
        snap = Snapshot(
            id="s%d" % index,
            label=label or "snapshot-%d" % index,
            path=target,
            state_hash=digest.state_hash(self.geometry),
            created_at=datetime.now().isoformat(timespec="seconds"),
        )
        self.snapshots.append(snap)
        return snap

    def rollback(self, snapshot_id: str | None = None) -> dict:
        if not self.snapshots:
            raise ToolError(ErrorCode.STATE, "没有任何快照可回滚",
                            hint="先用 cad.snapshot 打点")
        if snapshot_id in (None, "", "last"):
            snap = self.snapshots[-1]
        else:
            matches = [s for s in self.snapshots if s.id == snapshot_id or s.label == snapshot_id]
            if not matches:
                raise ToolError(
                    ErrorCode.STATE, "找不到快照 %s" % snapshot_id,
                    hint="可用快照：%s" % ", ".join(s.id for s in self.snapshots),
                    details={"available": [s.to_dict() for s in self.snapshots]},
                )
            snap = matches[-1]

        restore = self.root / "current.step"
        shutil.copyfile(snap.path, restore)
        geometry = _load_geometry(restore)
        actual = digest.state_hash(geometry)
        if actual != snap.state_hash:
            raise ToolError(
                ErrorCode.ROLLBACK,
                "回滚后几何指纹不一致：期望 %s，实际 %s" % (snap.state_hash, actual),
                hint="会话状态不可信，建议新建会话重新建模",
                details={"expected": snap.state_hash, "actual": actual},
            )
        # 回滚到某个快照后，该快照之后的点已失效
        index = self.snapshots.index(snap)
        self.snapshots = self.snapshots[:index + 1]
        self.current = restore
        self.geometry = geometry
        self.stats["rollbacks"] += 1
        return {"rolled_back_to": snap.to_dict(), "state": self.state()}

    # ---------- 事务 ----------

    def commit(self, spec, params: dict) -> dict:
        """执行一个动作，带前置校验、快照兜底与后置校验。"""
        self.stats["calls"] += 1

        for description, check in spec.preconditions:
            try:
                check(self, params)
            except ToolError:
                self.stats["errors"] += 1
                raise
            except Exception as exc:  # noqa: BLE001
                self.stats["errors"] += 1
                raise ToolError(ErrorCode.PRECONDITION,
                                "前置条件「%s」校验异常：%s" % (description, exc)) from exc

        before = self.state() if spec.mutating else None
        backup = None
        if spec.mutating and self.current is not None:
            backup = self.root / "rollback.step"
            shutil.copyfile(self.current, backup)

        try:
            result = spec.handler(self, params)
            for description, check in spec.postconditions:
                if check is None:
                    continue
                check(self, params, before, result)
        except ToolError as error:
            self.stats["errors"] += 1
            if backup is not None:
                _restore(self, backup)
            if error.code is ErrorCode.POSTCONDITION:
                error.details.setdefault("rolled_back", True)
                error.hint = (error.hint + "；已自动回滚到动作前状态").strip("；")
            raise
        except Exception as exc:  # noqa: BLE001
            self.stats["errors"] += 1
            if backup is not None:
                _restore(self, backup)
            raise ToolError(ErrorCode.GEOM_FAIL, "动作执行异常：%s" % exc,
                            hint="已自动回滚到动作前状态") from exc

        self.history.append({
            "tool": spec.name,
            "params": params,
            "state_hash": result.get("state_hash"),
            "at": datetime.now().isoformat(timespec="seconds"),
        })
        return result


def _size(geometry):
    bbox = geometry.get("bbox") or {}
    if not bbox.get("max") or not bbox.get("min"):
        return None
    return [round(bbox["max"][i] - bbox["min"][i], 2) for i in range(3)]


def _load_geometry(path: Path) -> dict:
    return freecad.geometry(path, use_cache=False)


def _restore(session: Session, backup: Path) -> None:
    """动作失败时把 current 换回 backup，并重算几何，保持状态自洽。"""
    if not backup.exists():
        return
    restore = session.root / "current.step"
    shutil.copyfile(backup, restore)
    try:
        session.current = restore
        session.geometry = _load_geometry(restore)
    except Exception:  # noqa: BLE001
        session.current = None
        session.geometry = None


def new_session(root: str | Path | None = None) -> Session:
    path = Path(root) if root else Path(tempfile.mkdtemp(prefix="cadcam_sess_"))
    path.mkdir(parents=True, exist_ok=True)
    return Session(root=path)
