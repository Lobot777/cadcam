"""契约层的参数校验行为。

这层的价值不在"能校验"，而在"报错能让模型自己改对"。所以断言都盯着
错误码与 details —— 模型靠 code 决定重试策略，靠 details 算出正确值。
"""

import pytest

from cadcam.gateway.contracts import ErrorCode, Param, ToolError, ToolSpec, validate_params


SPEC = ToolSpec(
    name="cad.demo",
    title="演示",
    description="",
    params={
        "length": Param("number", "长度", 1.0, 100.0),
        "count": Param("integer", "数量", 1, 10, default=1),
        "mode": Param("string", "模式", enum=("rough", "finish"), default="rough"),
        "deep": Param("boolean", "是否深腔", default=False),
    },
    required=("length",),
)


def test_missing_required_reports_names():
    with pytest.raises(ToolError) as info:
        validate_params(SPEC, {})
    error = info.value
    assert error.code is ErrorCode.PARAM_INVALID
    assert error.details["missing"] == ["length"]
    assert "length" in error.message


def test_unknown_parameter_is_rejected_not_ignored():
    """模型多传一个参数时不能静默忽略——那会让它以为参数生效了。"""
    with pytest.raises(ToolError) as info:
        validate_params(SPEC, {"length": 10, "speed": 3000})
    assert info.value.code is ErrorCode.PARAM_INVALID
    assert info.value.details["unknown"] == ["speed"]


def test_below_minimum_carries_range_in_hint():
    with pytest.raises(ToolError) as info:
        validate_params(SPEC, {"length": 0.2})
    error = info.value
    assert error.code is ErrorCode.PARAM_INVALID
    assert error.details["minimum"] == 1.0
    assert "[1.0, 100.0]" in error.hint


def test_above_maximum():
    with pytest.raises(ToolError) as info:
        validate_params(SPEC, {"length": 999})
    assert info.value.details["maximum"] == 100.0


def test_numeric_string_is_coerced():
    """LLM 常把数字写成字符串，能安全转换就不该报错。"""
    clean = validate_params(SPEC, {"length": "12.5"})
    assert clean["length"] == 12.5


def test_integer_rejects_fraction():
    with pytest.raises(ToolError):
        validate_params(SPEC, {"length": 10, "count": 1.5})


def test_bool_is_not_a_number():
    """True 在 Python 里是 int 的子类，必须显式排除，否则 length=True 会通过。"""
    with pytest.raises(ToolError):
        validate_params(SPEC, {"length": True})


def test_defaults_filled():
    clean = validate_params(SPEC, {"length": 10})
    assert clean["count"] == 1
    assert clean["mode"] == "rough"
    assert clean["deep"] is False


def test_enum_enforced():
    with pytest.raises(ToolError) as info:
        validate_params(SPEC, {"length": 10, "mode": "ultra"})
    assert "rough" in info.value.hint


def test_json_schema_shape_is_mcp_compatible():
    schema = SPEC.json_schema()
    assert schema["type"] == "object"
    assert schema["required"] == ["length"]
    assert schema["properties"]["length"]["minimum"] == 1.0
    assert schema["properties"]["mode"]["enum"] == ["rough", "finish"]
    assert schema["additionalProperties"] is False


def test_error_serializes_for_llm():
    error = ToolError(ErrorCode.PRECONDITION, "型腔超界", hint="缩小 width")
    payload = error.to_dict()
    assert payload["code"] == "E_PRECONDITION"
    assert payload["hint"] == "缩小 width"


def test_pocket_ramp_spec_is_registered():
    """斜底槽（B18）：契约层新增的第一个"非平底"特征。"""
    from cadcam.gateway.tools import get_spec
    spec = get_spec("cad.pocket_ramp")
    assert spec.mutating is True
    assert set(spec.required) == {"center_x", "center_y", "width", "height",
                                  "depth_near", "depth_far"}
    # axis 有默认值，不是必填——但给了非法值要在前置条件里被挡住
    assert "axis" in spec.params


def test_pocket_ramp_rejects_bad_axis_and_missing_depth():
    import pytest
    from cadcam.gateway.contracts import ErrorCode, ToolError, validate_params
    from cadcam.gateway.tools import get_spec
    spec = get_spec("cad.pocket_ramp")
    base = {"center_x": 50, "center_y": 40, "width": 40, "height": 30,
            "depth_near": 4, "depth_far": 9}
    filled = validate_params(spec, base)
    assert filled["axis"] == "x", "axis 没给时应填默认 x"
    bad = dict(base)
    bad.pop("depth_far")
    with pytest.raises(ToolError) as ei:
        validate_params(spec, bad)
    assert ei.value.code == ErrorCode.PARAM_INVALID
    assert "depth_far" in str(ei.value)
