"""M5 LLM 接入：OpenAI 兼容客户端（零依赖，只用 urllib）。

四条设计约束（都是为了「接了模型也不破坏可验证性」）：

1. **只走 `/v1/chat/completions`**：Ollama / vLLM / 云 API 都兼容，换后端不改代码；
2. **结构化输出必须过本地校验**：模型说什么是它的事，字段类型/枚举/上下界由这里判；
3. **任何失败都可降级**：超时、非法 JSON、越界值 → 抛 `LLMError` 或返回 `invalid`，
   由调用方回落到确定性策略，绝不把坏输出喂进几何管线；
4. **记账**：每次调用记录 prompt/completion tokens 与耗时，成本指标由此得出。

    from cadcam.agents.llm import LLMClient
    c = LLMClient()                      # 默认 http://127.0.0.1:11434/v1
    ok, models = c.available()
    r = c.chat([{"role": "user", "content": "hi"}], max_tokens=32)
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

DEFAULT_BASE = "http://127.0.0.1:11434/v1"     # Ollama 的 OpenAI 兼容口
DEFAULT_MODEL = "qwen3:8b"

# WorkBuddy 云服务数据面：无需自备 API key，按账号积分计费。
# 配置来自 .workbuddy/cloud.json（已 gitignore）或环境变量，绝不写死在源码里。
CLOUD_CONFIG_NAME = "cloud.json"


def _cloud_config():
    """返回 (endpoint, publishableKey)。没有配置就抛错，不静默降级。"""
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))          # 项目根
    path = os.path.join(root, ".workbuddy", CLOUD_CONFIG_NAME)
    endpoint = os.environ.get("WBCLOUD_ENDPOINT", "")
    key = os.environ.get("WBCLOUD_KEY", "")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                cfg = json.load(fh)
            endpoint = endpoint or cfg.get("endpoint", "")
            key = key or cfg.get("publishableKey", "")
        except Exception:                                       # noqa: BLE001
            pass
    if not endpoint or not key:
        raise LLMError("未配置云服务：需要 .workbuddy/%s 或 WBCLOUD_ENDPOINT/WBCLOUD_KEY"
                       % CLOUD_CONFIG_NAME)
    return endpoint.rstrip("/"), key


class LLMError(RuntimeError):
    """网络、HTTP、格式三类失败统一出口。调用方据此降级。"""


@dataclass
class LLMResponse:
    text: str
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0

    def as_dict(self):
        return {"model": self.model, "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "seconds": round(self.seconds, 3)}


@dataclass
class _Meter:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    failures: int = 0
    last_usage: dict = field(default_factory=dict)   # 云端的原始 usage（含 quota）

    def add(self, r: LLMResponse):
        self.calls += 1
        self.prompt_tokens += r.prompt_tokens
        self.completion_tokens += r.completion_tokens
        self.seconds += r.seconds

    def summary(self):
        return {"calls": self.calls, "failed_calls": self.failures,
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.prompt_tokens + self.completion_tokens,
                "seconds": round(self.seconds, 2),
                "tokens_per_call": (round((self.prompt_tokens + self.completion_tokens)
                                          / self.calls, 1) if self.calls else 0.0)}


@dataclass
class LLMClient:
    base_url: str = DEFAULT_BASE
    model: str = DEFAULT_MODEL
    timeout: float = 180.0
    retries: int = 2
    temperature: float = 0.0
    api: str = "auto"          # auto | openai | ollama | cloud
    meter: _Meter = field(default_factory=_Meter)

    def __post_init__(self):
        if self.api == "auto":
            if ".workbuddy.host" in self.base_url:
                self.api = "cloud"
            else:
                self.api = "ollama" if ":11434" in self.base_url else "openai"
        if self.api == "cloud":
            # base_url 留空时自动取 .workbuddy/cloud.json 里的 endpoint
            self.base_url = self.base_url or _cloud_config()[0]

    # ---------------- 基础调用 ----------------
    def _post(self, path, payload, timeout=None):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.base_url.rstrip("/") + path, data=data,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def available(self):
        """返回 (是否可用, 已装模型列表)。不可用不抛异常——探活不该让流程崩。"""
        if self.api == "cloud":
            try:
                _cloud_config()
            except LLMError as exc:
                return False, [str(exc)]
            return True, [self.model]
        try:
            if self.api == "ollama":
                with urllib.request.urlopen(self._url("/api/tags"), timeout=5) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                return True, [m.get("name") for m in body.get("models", [])]
            with urllib.request.urlopen(self._url("/models"), timeout=5) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            return True, [m.get("id") for m in body.get("data", [])]
        except Exception as exc:                      # noqa: BLE001 - 探活吞掉一切
            return False, ["%s: %s" % (type(exc).__name__, exc)]

    def _url(self, path):
        """Ollama 原生口在根路径（/api/...），OpenAI 兼容口带 /v1。"""
        root = self.base_url.rstrip("/")
        if self.api == "ollama":
            root = root[:-3] if root.endswith("/v1") else root
        return root + path

    def chat(self, messages, *, max_tokens=512, json_mode=False,
             temperature=None, stop=None):
        if self.api == "cloud":
            return self._chat_cloud(messages, max_tokens=max_tokens,
                                    json_mode=json_mode,
                                    temperature=temperature, stop=stop)
        if self.api == "ollama":
            return self._chat_ollama(messages, max_tokens=max_tokens,
                                     json_mode=json_mode,
                                     temperature=temperature, stop=stop)
        payload = {"model": self.model, "messages": list(messages),
                   "temperature": self.temperature if temperature is None else temperature,
                   "max_tokens": int(max_tokens), "stream": False}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if stop:
            payload["stop"] = list(stop)

        last = None
        for attempt in range(self.retries + 1):
            t0 = time.time()
            try:
                body = self._post("/chat/completions", payload)
            except urllib.error.HTTPError as exc:
                last = exc
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", "replace")[:200]
                except Exception:                     # noqa: BLE001
                    pass
                # 后端不认 response_format 时退回普通模式，而不是整条失败
                if exc.code == 400 and "response_format" in payload and "response_format" in detail:
                    payload.pop("response_format", None)
                    continue
                if attempt < self.retries and exc.code >= 500:
                    time.sleep(1.0 + attempt)
                    continue
                self.meter.failures += 1
                raise LLMError("HTTP %s: %s" % (exc.code, detail)) from exc
            except Exception as exc:                  # noqa: BLE001 - 超时/连接
                last = exc
                if attempt < self.retries:
                    time.sleep(1.0 + attempt)
                    continue
                self.meter.failures += 1
                raise LLMError("%s: %s" % (type(exc).__name__, exc)) from exc

            choice = (body.get("choices") or [{}])[0]
            text = (choice.get("message") or {}).get("content") or ""
            usage = body.get("usage") or {}
            r = LLMResponse(text=text, model=body.get("model", self.model),
                            prompt_tokens=int(usage.get("prompt_tokens") or 0),
                            completion_tokens=int(usage.get("completion_tokens") or 0),
                            seconds=time.time() - t0)
            self.meter.add(r)
            return r
        raise LLMError("重试耗尽：%s" % last)

    def _chat_cloud(self, messages, *, max_tokens, json_mode, temperature, stop):
        """WorkBuddy 云服务 LLM（`/.cloud/llm/chat/completions`）。

        三个与本地后端不同的硬约束：

        1. **只支持流式**（`stream:true`），非流式会被拒；所以这里读 SSE 拼回整段。
        2. **鉴权靠 `x-wb-webapp-access-key` + Origin**，不是 Bearer；Origin 必须与
           endpoint 同源，否则 `auth_origin_mismatch`。
        3. **思考模型会先吐 `reasoning_content`**，`content` 可能空；两者分开累计，
           只把 `content` 当答案，`reasoning` 计入 token 但不进结果。

        计费按账号积分（`usage.quota.percent` 可读剩余），429 要退避重试。
        """
        endpoint, key = _cloud_config()
        payload = {"model": self.model, "messages": list(messages),
                   "stream": True,
                   "temperature": self.temperature if temperature is None else temperature,
                   "max_tokens": int(max_tokens),
                   "stream_options": {"include_usage": True}}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if stop:
            payload["stop"] = list(stop)

        last = None
        for attempt in range(self.retries + 1):
            t0 = time.time()
            try:
                req = urllib.request.Request(
                    endpoint + "/.cloud/llm/chat/completions",
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json",
                             "Accept": "text/event-stream",
                             "x-wb-webapp-access-key": key,
                             "Origin": endpoint,
                             "Referer": endpoint + "/"},
                    method="POST")
                content, reasoning, usage = "", "", {}
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    for raw in resp:
                        line = raw.decode("utf-8", "replace").strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            obj = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if obj.get("usage"):
                            usage = obj["usage"]
                        for ch in obj.get("choices") or []:
                            d = ch.get("delta") or {}
                            if d.get("content"):
                                content += d["content"]
                            if d.get("reasoning_content"):
                                reasoning += d["reasoning_content"]
            except urllib.error.HTTPError as exc:
                last = exc
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", "replace")[:200]
                except Exception:                      # noqa: BLE001
                    pass
                # 400 多半是模型不收 response_format / stream_options，退一步再试
                if exc.code == 400 and attempt <= self.retries:
                    if payload.pop("response_format", None) is not None:
                        continue
                    if payload.pop("stream_options", None) is not None:
                        continue
                if exc.code == 429 and attempt < self.retries:
                    time.sleep(3.0 * (attempt + 1))
                    continue
                if attempt < self.retries and exc.code >= 500:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                self.meter.failures += 1
                raise LLMError("HTTP %s: %s" % (exc.code, detail)) from exc
            except Exception as exc:                   # noqa: BLE001 - 超时/连接
                last = exc
                if attempt < self.retries:
                    time.sleep(1.0 + attempt)
                    continue
                self.meter.failures += 1
                raise LLMError("%s: %s" % (type(exc).__name__, exc)) from exc

            if not content.strip() and reasoning.strip():
                # 思考预算被 max_tokens 吃光：把思考当答案会污染 JSON 解析，
                # 这里明确报错，让调用方降级而不是静默拿到空串。
                self.meter.failures += 1
                raise LLMError("content 为空（reasoning %d 字），通常是 max_tokens 不足"
                               % len(reasoning))
            r = LLMResponse(
                text=content, model=self.model,
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0),
                seconds=time.time() - t0)
            self.meter.add(r)
            self.meter.last_usage = usage
            return r
        raise LLMError("重试耗尽：%s" % last)

    def _chat_ollama(self, messages, *, max_tokens, json_mode, temperature, stop):
        """Ollama 原生 /api/chat。

        为什么不用 /v1：实测该层的 `response_format` 会被忽略、`think:false` 也不生效，
        模型把预算全花在 reasoning 上，`content` 返回空串。原生口支持 `think:false`
        与 `format:"json"`，并且返回 prompt_eval_count / eval_count，记账更准。
        """
        opts = {"temperature": self.temperature if temperature is None else temperature,
                "num_predict": int(max_tokens)}
        payload = {"model": self.model, "messages": list(messages),
                   "stream": False, "think": False, "options": opts}
        if json_mode:
            payload["format"] = "json"
        if stop:
            payload["stop"] = list(stop)

        last = None
        for attempt in range(self.retries + 1):
            t0 = time.time()
            try:
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                req = urllib.request.Request(
                    self._url("/api/chat"), data=data,
                    headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
            except Exception as exc:                  # noqa: BLE001
                last = exc
                if attempt < self.retries:
                    time.sleep(1.0 + attempt)
                    continue
                self.meter.failures += 1
                raise LLMError("%s: %s" % (type(exc).__name__, exc)) from exc
            text = ((body.get("message") or {}).get("content") or "")
            r = LLMResponse(
                text=text, model=body.get("model", self.model),
                prompt_tokens=int(body.get("prompt_eval_count") or 0),
                completion_tokens=int(body.get("eval_count") or 0),
                seconds=(body.get("total_duration") or 0) / 1e9 or (time.time() - t0))
            self.meter.add(r)
            return r
        raise LLMError("重试耗尽：%s" % last)

    # ---------------- 结构化输出 ----------------
    def complete_json(self, messages, schema, *, max_tokens=512, json_mode=True):
        """要求模型输出 JSON 并按 schema 校验；不合格直接抛 LLMError。

        schema 只支持本项目的四种约束：type / enum / minimum / maximum / required。
        够用即可——引入 jsonschema 只为校验三个字段不值得。
        """
        raw = self.chat(messages, max_tokens=max_tokens, json_mode=json_mode)
        data = _extract_json(raw.text)
        _validate(data, schema)
        return data, raw


def _extract_json(text):
    """模型偶尔会包 ```json 或前后加解释，这里只取第一个完整对象。"""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        raise LLMError("输出里没有 JSON 对象：%r" % text[:120])
    try:
        return json.loads(t[i:j + 1])
    except json.JSONDecodeError as exc:
        raise LLMError("JSON 解析失败：%s｜原文 %r" % (exc, text[:160])) from exc


def _validate(data, schema):
    """极简校验器：只认 type / enum / minimum / maximum / required。"""
    if not isinstance(data, dict):
        raise LLMError("顶层必须是对象，得到 %s" % type(data).__name__)
    for key in schema.get("required", []):
        if key not in data:
            raise LLMError("缺字段 %s（实际 %s）" % (key, sorted(data)))
    for key, rule in schema.get("properties", {}).items():
        if key not in data:
            continue
        val = data[key]
        typ = rule.get("type")
        if typ == "number" and not isinstance(val, (int, float)):
            raise LLMError("字段 %s 应为 number，得到 %r" % (key, val))
        if typ == "string" and not isinstance(val, str):
            raise LLMError("字段 %s 应为 string，得到 %r" % (key, val))
        if "enum" in rule and val not in rule["enum"]:
            raise LLMError("字段 %s=%r 不在允许集 %s" % (key, val, rule["enum"]))
        if "minimum" in rule and isinstance(val, (int, float)) and val < rule["minimum"]:
            raise LLMError("字段 %s=%r 小于下限 %s" % (key, val, rule["minimum"]))
        if "maximum" in rule and isinstance(val, (int, float)) and val > rule["maximum"]:
            raise LLMError("字段 %s=%r 大于上限 %s" % (key, val, rule["maximum"]))
    return True
