"""FreeCAD 适配器（宿主侧）。

策略：不 import FreeCAD，改用子进程调用 FreeCADCmd 执行 worker。
理由：
1. FreeCAD 自带解释器（0.20 为 3.8，1.1 为 3.11）与本项目宿主 Python 版本不同；
2. NX 侧同样是进程内对象模型，两者统一走"子进程 + JSON"更一致；
3. 崩溃隔离——几何内核的段错误不会带崩智能体进程。
"""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

WORKER = Path(__file__).with_name("freecad_worker.py")
CACHE_DIR = Path(os.environ.get("CADCAM_CACHE", Path.home() / ".cadcam" / "cache"))

# 便携版不会写注册表，安装版位置也随用户习惯变化 —— 因此按"环境变量 > 目录探测"解析。
_SEARCH_ROOTS = ("E:/", "D:/", "C:/Program Files", "C:/Program Files (x86)")
_ENGINE_VERSION_CACHE = {}


class FreeCADError(RuntimeError):
    pass


def _discover_cmd():
    """定位 freecadcmd 可执行文件：优先环境变量，其次扫描常见安装根目录取最新。"""
    override = os.environ.get("FREECAD_CMD")
    if override:
        return override

    candidates = []
    for root in _SEARCH_ROOTS:
        base = Path(root)
        if not base.exists():
            continue
        for exe in base.glob("FreeCAD*/bin/*.exe"):
            if exe.stem.lower() in ("freecadcmd", "freecadcmd_con"):
                candidates.append(exe)
    if not candidates:
        raise FreeCADError(
            "未找到 freecadcmd。请安装 FreeCAD 1.1+，或用环境变量 FREECAD_CMD 指定可执行文件路径。"
        )
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return str(candidates[0])


FREECAD_CMD = _discover_cmd()


def engine_version(cmd=None) -> str:
    """取内核实测版本串，用于缓存键与实验记录。"""
    cmd = cmd or FREECAD_CMD
    if cmd in _ENGINE_VERSION_CACHE:
        return _ENGINE_VERSION_CACHE[cmd]
    try:
        completed = subprocess.run(
            [cmd, "-c", "import FreeCAD;print('.'.join(FreeCAD.Version()[:3]))"],
            capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace",
        )
        version = completed.stdout.strip().splitlines()[-1] if completed.stdout.strip() else "unknown"
    except Exception:
        version = "unknown"
    _ENGINE_VERSION_CACHE[cmd] = version
    return version


def _file_digest(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]


def geometry(step_path, use_cache: bool = True, timeout: int = 180) -> dict:
    """读取 STEP，返回规范化几何描述（faces/edges/counts/bbox/volume）。"""
    step = Path(step_path)
    if not step.exists():
        raise FreeCADError("STEP 文件不存在: %s" % step)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_key = _file_digest(step)
    version = engine_version()
    engine_key = "%s|%s" % (version, Path(FREECAD_CMD).name)
    scoped_key = hashlib.sha1(("%s|%s" % (cache_key, engine_key)).encode("utf-8")).hexdigest()[:16]
    cache_file = CACHE_DIR / ("%s.json" % scoped_key)
    if use_cache and cache_file.exists():
        with cache_file.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    if not Path(FREECAD_CMD).exists():
        raise FreeCADError("找不到 FreeCADCmd：%s（可用环境变量 FREECAD_CMD 覆盖）" % FREECAD_CMD)

    temp_dir = Path(tempfile.mkdtemp(prefix="cadcam_"))
    out_file = temp_dir / "geometry.json"
    try:
        env = dict(os.environ)
        env["CADCAM_STEP"] = str(step.resolve())
        env["CADCAM_OUT"] = str(out_file)
        completed = subprocess.run(
            [FREECAD_CMD, str(WORKER)],
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        if not out_file.exists():
            raise FreeCADError(
                "FreeCAD worker 未产出结果（exit=%s）\nstdout: %s\nstderr: %s"
                % (completed.returncode, completed.stdout[-2000:], completed.stderr[-2000:])
            )
        with out_file.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if "error" in payload:
            raise FreeCADError("FreeCAD 内核执行失败:\n%s" % payload["error"])
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    payload["hashes"] = {"step": cache_key}
    payload["engine"] = {"cmd": FREECAD_CMD, "version": version}
    if use_cache:
        with cache_file.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
    return payload
