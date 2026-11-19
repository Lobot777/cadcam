"""M5 LLM 接入的可测约束：输出必须过校验，失败必须降级，绝不把坏输出喂进几何。

全部用假客户端，不依赖网络与模型——这样 CI 里也能跑。
真实模型的实验见 `scripts/m5_llm_eval.py`。
"""

import json
import os

import pytest

from cadcam.agents.llm import LLMError, _extract_json, _validate
from cadcam.agents.prompts import PLANNER_SCHEMA, REPAIR_SCHEMA
from cadcam.agents.roles_llm import LLMPlanner, LLMRepairer
from cadcam.orchestrator import EpisodicMemory, run_task

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEOM = os.path.join(ROOT, "reports", "annotation", "geom")
LABELS = os.path.join(ROOT, "data", "zenodo_rptu", "labels")


class _Resp:
    def __init__(self, prompt_tokens=100, completion_tokens=20):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.seconds = 0.01

    def as_dict(self):
        return {"prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens, "seconds": 0.01}


class FakeClient:
    """按预设回答；fail=True 时模拟超时/非法输出。"""

    def __init__(self, answer, fail=False):
        self.answer, self.fail, self.calls = answer, fail, 0

    def complete_json(self, messages, schema, max_tokens=512, json_mode=True):
        self.calls += 1
        if self.fail:
            raise LLMError("模拟超时")
        _validate(self.answer, schema)
        return dict(self.answer), _Resp()


def _task(part="1"):
    with open(os.path.join(GEOM, "%s.json" % part), encoding="utf-8") as fh:
        geom = json.load(fh)
    with open(os.path.join(LABELS, "%s.json" % part), encoding="utf-8") as fh:
        labels = json.load(fh)
    return {"part": part, "family": "llm_probe", "geom": geom, "labels": labels,
            "allowance": 1.0}


# ---------- 解析与校验 ----------

def test_extract_json_tolerates_fence_and_chatter():
    assert _extract_json('```json\n{"a": 1}\n```')["a"] == 1
    assert _extract_json('好的，结果如下：{"strategy":"bbox"} 完毕')["strategy"] == "bbox"


def test_extract_json_rejects_non_json():
    with pytest.raises(LLMError):
        _extract_json("我觉得应该用高度图")


def test_validate_catches_enum_and_range():
    with pytest.raises(LLMError):
        _validate({"strategy": "spiral", "reason": "x"}, PLANNER_SCHEMA)
    with pytest.raises(LLMError):
        _validate({"strategy": "bbox"}, PLANNER_SCHEMA)          # 缺 reason
    with pytest.raises(LLMError):
        _validate({"strategy": "bbox", "reason": "x", "ap_mm": 99}, PLANNER_SCHEMA)


def test_validate_accepts_legal_repair_action():
    assert _validate({"action": "switch_heightmap", "reason": "过切"}, REPAIR_SCHEMA)


# ---------- 角色注入 ----------

def test_llm_planner_choice_flows_into_pipeline():
    client = FakeClient({"strategy": "heightmap", "reason": "多级腔", "ap_mm": 4})
    rec = []
    r = run_task(_task("1"), EpisodicMemory(), planner=LLMPlanner(client, rec))
    assert client.calls == 1
    assert r["strategies"] == "heightmap"
    assert r["pass"] and r["rounds"] == 0
    assert rec[0]["fallback"] is False


def test_llm_planner_failure_falls_back_not_crashes():
    client = FakeClient({"strategy": "bbox", "reason": "x"}, fail=True)
    rec = []
    r = run_task(_task("1"), EpisodicMemory(), planner=LLMPlanner(client, rec))
    assert rec[0]["fallback"] is True and rec[0]["error"]
    assert rec[0]["decision"]["strategy"] == "bbox"   # 回落到确定性默认
    assert r["first_metrics"]["max_gouge_depth_mm"] > 0.5, "首轮确实是包围盒，会过切"
    assert r["pass"] and r["strategies"] == "heightmap", "降级后仍应由修复器兜住"


def test_llm_planner_respects_memory_hint_then_skips_call():
    client = FakeClient({"strategy": "bbox", "reason": "x"})
    task = _task("1")
    task["strategy_hint"] = "heightmap"
    rec = []
    r = run_task(task, EpisodicMemory(), planner=LLMPlanner(client, rec))
    assert client.calls == 0, "有记忆提示时不该再问模型"
    assert r["strategies"] == "heightmap" and r["rounds"] == 0


def test_llm_repairer_switches_on_structured_error():
    client = FakeClient({"action": "switch_heightmap", "reason": "过切 18mm"})
    rec = []
    r = run_task(_task("1"), EpisodicMemory(),
                 repairer=LLMRepairer(client, "structured", rec))
    assert client.calls == 1
    assert r["pass"] and r["rounds"] >= 1
    assert r["repairs"][0]["channel"] == "structured"


def test_llm_repairer_escalate_is_respected():
    client = FakeClient({"action": "escalate", "reason": "没救了"})
    r = run_task(_task("1"), EpisodicMemory(),
                 repairer=LLMRepairer(client, "structured", []))
    assert r["status"] == "ESCALATE" and not r["pass"]


def test_deterministic_path_unchanged_when_no_roles_injected():
    a = run_task(_task("2"), EpisodicMemory())
    assert a["strategies"] == "bbox"
    assert a["phases"][-1] in ("ACCEPT", "ESCALATE")


# ---------------- 云端后端（不联网，用假 urlopen 覆盖 SSE 解析） ----------------

def test_cloud_client_requires_config(monkeypatch, tmp_path):
    """没有 endpoint/key 就必须报错，不能静默退回到本地模型。"""
    from cadcam.agents import llm
    monkeypatch.delenv("WBCLOUD_ENDPOINT", raising=False)
    monkeypatch.delenv("WBCLOUD_KEY", raising=False)
    monkeypatch.setattr(llm, "__file__", str(tmp_path / "x" / "y" / "z" / "llm.py"))
    with pytest.raises(LLMError, match="未配置云服务"):
        llm._cloud_config()


def test_cloud_client_builds_headers_and_parses_sse(monkeypatch):
    """三个云端特有的坑：x-wb-webapp-access-key、同源 Origin、reasoning/content 分离。"""
    from cadcam.agents import llm

    monkeypatch.setenv("WBCLOUD_ENDPOINT", "https://demo.app.workbuddy.host")
    monkeypatch.setenv("WBCLOUD_KEY", "wbpk_test")
    seen = {}

    class _Stream:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __iter__(self):
            frames = [
                {"choices": [{"delta": {"role": "assistant", "content": "",
                                        "reasoning_content": ""}}]},
                {"choices": [{"delta": {"content": '{"calls"',
                                        "reasoning_content": "想一下"}}]},
                {"choices": [{"delta": {"content": ': []}', "reasoning_content": ""}}]},
                {"choices": [{"delta": {}, "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": 12, "completion_tokens": 3,
                           "quota": {"percent": 42}}},
            ]
            for f in frames:
                yield ("data: " + json.dumps(f) + "\n\n").encode()
            yield b"data: [DONE]\n\n"

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        seen["body"] = json.loads(req.data.decode())
        return _Stream()

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    c = llm.LLMClient(base_url="", model="some-cloud-model", api="cloud")
    r = c.chat([{"role": "system", "content": "s"},
                {"role": "user", "content": "u"}], max_tokens=128, json_mode=True)

    assert seen["url"].endswith("/.cloud/llm/chat/completions")
    assert seen["headers"]["x-wb-webapp-access-key"] == "wbpk_test"
    assert seen["headers"]["origin"] == "https://demo.app.workbuddy.host"
    assert seen["body"]["stream"] is True, "云端只接受流式"
    assert seen["body"]["response_format"] == {"type": "json_object"}
    # 思考内容不能混进答案，否则 JSON 解析必失败
    assert r.text == '{"calls": []}'
    assert (r.prompt_tokens, r.completion_tokens) == (12, 3)
    assert c.meter.last_usage["quota"]["percent"] == 42


def test_cloud_client_errors_when_only_reasoning_came_back(monkeypatch):
    """思考预算被吃光时 content 为空 —— 必须报错，不能把 reasoning 当答案。"""
    from cadcam.agents import llm

    monkeypatch.setenv("WBCLOUD_ENDPOINT", "https://demo.app.workbuddy.host")
    monkeypatch.setenv("WBCLOUD_KEY", "wbpk_test")

    class _Stream:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __iter__(self):
            yield ("data: " + json.dumps(
                {"choices": [{"delta": {"content": "",
                                        "reasoning_content": "想了很久"}}]}) + "\n\n").encode()
            yield b"data: [DONE]\n\n"

    monkeypatch.setattr(llm.urllib.request, "urlopen",
                        lambda req, timeout=None: _Stream())
    c = llm.LLMClient(base_url="", model="m", api="cloud", retries=0)
    with pytest.raises(LLMError, match="content 为空"):
        c.chat([{"role": "user", "content": "u"}], max_tokens=64)
