"""建模适配器（宿主侧）：一次调用 = 一个 CAD 动作。

与 `freecad.py`（只读几何抽取）分离的原因：
- 抽几何是无副作用的，可缓存；建模是有副作用的，必须带事务语义；
- 两条链路的失败模式不同：抽取错 → 结论错；建模错 → 状态脏。后者必须回滚。

协议仍是「子进程 + JSON 文件」：FreeCADCmd 会劫持命令行参数，所以路径走环境变量。
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path

from cadcam.adapters import freecad
from cadcam.gateway.contracts import ErrorCode, ToolError

WORKER = Path(__file__).with_name("freecad_model_worker.py")
SRC_DIR = Path(__file__).resolve().parents[2]

_CODE_BY_NAME = {code.value: code for code in ErrorCode}


def apply(action: str, params: dict, path_in: Path | None, path_out: Path,
          *, label: str = "", timeout: int = 240) -> dict:
    """在 FreeCAD 里执行一个建模动作，产出新的 STEP，并返回其几何描述。"""
    path_out = Path(path_out)
    path_out.parent.mkdir(parents=True, exist_ok=True)

    job = {
        "action": action,
        "params": params,
        "path_in": str(path_in) if path_in else None,
        "path_out": str(path_out),
        "label": label or action,
    }

    with tempfile.TemporaryDirectory(prefix="cadcam_job_") as tmp:
        job_file = Path(tmp) / "job.json"
        out_file = Path(tmp) / "result.json"
        job_file.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")

        env = dict(os.environ)
        env["CADCAM_JOB"] = str(job_file)
        env["CADCAM_OUT"] = str(out_file)
        env["CADCAM_SRC"] = str(SRC_DIR)

        completed = subprocess.run(
            [freecad.FREECAD_CMD, str(WORKER)],
            env=env, capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace",
        )
        if not out_file.exists():
            raise ToolError(
                ErrorCode.GEOM_FAIL,
                "建模进程未产出结果（exit=%s）" % completed.returncode,
                hint=(completed.stdout or "")[-400:] + (completed.stderr or "")[-400:],
            )
        payload = json.loads(out_file.read_text(encoding="utf-8"))

    if not payload.get("ok"):
        error = payload.get("error") or {}
        code = _CODE_BY_NAME.get(error.get("code", ""), ErrorCode.GEOM_FAIL)
        raise ToolError(code, error.get("message", "建模失败"), hint=error.get("hint", ""))

    payload["path"] = str(path_out)
    return payload
