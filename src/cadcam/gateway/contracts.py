"""工具契约：把 CAD 动作定义成可静态校验、可回滚的封闭动作空间。

为什么不用 jsonschema 库：
1. 复现性 —— 论文要求 clone 下来零额外依赖即可跑通；
2. 错误信息面向 LLM —— 原生报错是 "is not of type 'number'"，而我们需要
   "depth 应在 [0.5, 38] 之间（毛坯厚 40 减去安全底厚 2），收到 42"。
   LLM 拿到后者能自行修正，拿到前者只能重试。

契约三件套：参数 schema（可静态校验）+ 前置条件（几何是否允许）+ 后置条件
（动作是否真的生效）。三者都在网关侧执行，内核只负责几何，不负责判对错。
"""

import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class ErrorCode(str, Enum):
    """结构化错误码。LLM 据此决定是改参数、换策略还是停止。"""

    PARAM_INVALID = "E_PARAM_INVALID"      # 参数不合法，可自行修正后重试
    PRECONDITION = "E_PRECONDITION"        # 几何状态不满足前置条件，需换动作
    GEOM_FAIL = "E_GEOM_FAIL"              # 内核执行失败（真实建模错误）
    POSTCONDITION = "E_POSTCONDITION"      # 动作执行了但结果不符合预期
    STATE = "E_STATE"                      # 会话/快照状态错误
    ROLLBACK = "E_ROLLBACK"                # 回滚失败，会话不可信
    UNKNOWN_TOOL = "E_UNKNOWN_TOOL"        # 调用了不存在的工具


class ToolError(Exception):
    """可被序列化回给 LLM 的契约错误。"""

    def __init__(self, code: ErrorCode, message: str, *, hint: str = "",
                 details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.details = details or {}

    def to_dict(self) -> dict:
        payload = {"code": self.code.value, "message": self.message}
        if self.hint:
            payload["hint"] = self.hint
        if self.details:
            payload["details"] = self.details
        return payload


@dataclass(frozen=True)
class Param:
    """单个参数的声明。单位统一 mm / 度，不做隐式换算。"""

    type: str = "number"           # number | integer | string | boolean
    desc: str = ""
    minimum: float | None = None
    maximum: float | None = None
    default: object = None
    enum: tuple | None = None
    pattern: str | None = None

    def to_json(self) -> dict:
        schema = {"type": self.type, "description": self.desc}
        if self.minimum is not None:
            schema["minimum"] = self.minimum
        if self.maximum is not None:
            schema["maximum"] = self.maximum
        if self.default is not None:
            schema["default"] = self.default
        if self.enum:
            schema["enum"] = list(self.enum)
        if self.pattern:
            schema["pattern"] = self.pattern
        return schema


@dataclass
class ToolSpec:
    name: str
    title: str
    description: str
    params: dict[str, Param] = field(default_factory=dict)
    required: tuple = ()
    mutating: bool = False
    returns: str = ""
    # 前置条件：执行前检查几何是否允许该动作。签名 (session, params) -> None
    preconditions: tuple = ()
    # 后置条件：动作后检查结果是否符合物理预期。签名 (session, params, before, result) -> None
    postconditions: tuple = ()
    handler: Callable | None = field(default=None, repr=False)

    def json_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {name: param.to_json() for name, param in self.params.items()},
            "required": list(self.required),
            "additionalProperties": False,
        }

    def to_mcp(self) -> dict:
        return {
            "name": self.name,
            "description": "%s。%s" % (self.title, self.description),
            "inputSchema": self.json_schema(),
        }


def _coerce(name: str, param: Param, value):
    """类型与范围校验，返回规范化后的值。错误信息直接可读。"""
    unit = ""

    if param.type in ("number", "integer"):
        # bool 是 int 的子类，必须先拦掉，否则 length=True 会静默变成 1.0
        if isinstance(value, bool):
            raise ToolError(
                ErrorCode.PARAM_INVALID,
                "参数 %s 需要数值，收到布尔值 %r" % (name, value),
                hint="请给出数字，单位为毫米",
            )
        if not isinstance(value, (int, float)):
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise ToolError(
                    ErrorCode.PARAM_INVALID,
                    "参数 %s 需要数值，收到 %r" % (name, value),
                    hint="请给出数字，单位为毫米",
                )
        if param.type == "integer" and not float(value).is_integer():
            raise ToolError(ErrorCode.PARAM_INVALID,
                            "参数 %s 需要整数，收到 %s" % (name, value))
        value = int(value) if param.type == "integer" else float(value)
        if param.minimum is not None and value < param.minimum:
            raise ToolError(
                ErrorCode.PARAM_INVALID,
                "参数 %s = %s 小于下限 %s" % (name, value, param.minimum),
                hint="允许范围 [%s, %s]%s" % (param.minimum, param.maximum, unit),
                details={"param": name, "value": value, "minimum": param.minimum},
            )
        if param.maximum is not None and value > param.maximum:
            raise ToolError(
                ErrorCode.PARAM_INVALID,
                "参数 %s = %s 超过上限 %s" % (name, value, param.maximum),
                hint="允许范围 [%s, %s]%s" % (param.minimum, param.maximum, unit),
                details={"param": name, "value": value, "maximum": param.maximum},
            )
        return value

    if param.type == "boolean":
        if not isinstance(value, bool):
            raise ToolError(ErrorCode.PARAM_INVALID,
                            "参数 %s 需要 true/false，收到 %r" % (name, value))
        return value

    text = str(value)
    if param.enum and text not in param.enum:
        raise ToolError(
            ErrorCode.PARAM_INVALID,
            "参数 %s = %r 不在允许集合内" % (name, text),
            hint="可选值：%s" % ", ".join(param.enum),
            details={"param": name, "enum": list(param.enum)},
        )
    if param.pattern and not re.match(param.pattern, text):
        raise ToolError(ErrorCode.PARAM_INVALID,
                        "参数 %s = %r 不符合格式 %s" % (name, text, param.pattern))
    return text


def validate_params(spec: ToolSpec, raw: dict) -> dict:
    """按契约校验并填充默认值。未知参数直接拒绝（防止模型加戏）。"""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ToolError(ErrorCode.PARAM_INVALID,
                        "参数必须是对象，收到 %s" % type(raw).__name__)

    unknown = sorted(set(raw) - set(spec.params))
    if unknown:
        raise ToolError(
            ErrorCode.PARAM_INVALID,
            "存在未声明参数：%s" % ", ".join(unknown),
            hint="该工具只接受：%s" % (", ".join(spec.params) or "（无参数）"),
            details={"unknown": unknown, "accepted": sorted(spec.params)},
        )

    missing = [name for name in spec.required if name not in raw]
    if missing:
        raise ToolError(
            ErrorCode.PARAM_INVALID,
            "缺少必需参数：%s" % ", ".join(missing),
            hint="必需参数：%s" % ", ".join(spec.required),
            details={"missing": missing},
        )

    clean = {}
    for name, param in spec.params.items():
        if name in raw:
            clean[name] = _coerce(name, param, raw[name])
        elif param.default is not None:
            clean[name] = param.default
    return clean


def finite(value: float, name: str = "value") -> float:
    """几何量必须有限：NaN/inf 会让布尔运算静默产生空结果。"""
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ToolError(ErrorCode.PARAM_INVALID, "%s 不是有限数值：%r" % (name, value))
    return float(value)
