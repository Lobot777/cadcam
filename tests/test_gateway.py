"""MCP 协议层与事务语义（不触发真实建模，保持测试秒级）。

真实建模链路由 scripts/m1_demo.py 做端到端验收；这里只锁协议形状，
因为它一旦漂移，任何客户端都连不上，而单看单元测试发现不了。
"""

import io
import json

from cadcam.gateway import tools
from cadcam.gateway.contracts import ErrorCode, ToolError, validate_params
from cadcam.gateway.server import GatewayServer
from cadcam.gateway.session import new_session


def make_server(tmp_path):
    return GatewayServer(session=new_session(tmp_path), log=io.StringIO())


def call(server, request):
    return server.handle(request)


def test_initialize_returns_protocol_and_server_info(tmp_path):
    response = call(make_server(tmp_path), {"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert response["result"]["protocolVersion"]
    assert response["result"]["capabilities"]["tools"] is not None
    assert response["result"]["serverInfo"]["name"] == "cadcam-gateway"


def test_tools_list_exposes_every_contract(tmp_path):
    response = call(make_server(tmp_path), {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    listed = response["result"]["tools"]
    assert len(listed) == len(tools.TOOLS)
    names = {tool["name"] for tool in listed}
    assert {"cad.create_box", "cad.pocket", "cad.hole", "cad.snapshot", "cad.rollback"} <= names
    for tool in listed:
        assert tool["inputSchema"]["type"] == "object"
        assert isinstance(tool["description"], str) and tool["description"]


def test_notification_gets_no_response(tmp_path):
    assert call(make_server(tmp_path), {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_unknown_method_is_protocol_error(tmp_path):
    response = call(make_server(tmp_path), {"jsonrpc": "2.0", "id": 3, "method": "resources/list"})
    assert response["error"]["code"] == -32601


def test_unknown_tool_is_tool_error_not_crash(tmp_path):
    response = call(make_server(tmp_path), {
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "cad.thread", "arguments": {}},
    })
    assert response["result"]["isError"] is True
    body = json.loads(response["result"]["content"][0]["text"])
    assert body["code"] == ErrorCode.UNKNOWN_TOOL.value
    assert "cad.pocket" in body["hint"]


def test_invalid_arguments_return_structured_error(tmp_path):
    response = call(make_server(tmp_path), {
        "jsonrpc": "2.0", "id": 5, "method": "tools/call",
        "params": {"name": "cad.create_box", "arguments": {"length": 10}},
    })
    assert response["result"]["isError"] is True
    body = json.loads(response["result"]["content"][0]["text"])
    assert body["code"] == ErrorCode.PARAM_INVALID.value
    assert "width" in body["message"] and "height" in body["message"]


def test_inspect_on_empty_session_explains_how_to_start(tmp_path):
    """空会话不是错误，但必须告诉模型下一步做什么。"""
    response = call(make_server(tmp_path), {
        "jsonrpc": "2.0", "id": 6, "method": "tools/call",
        "params": {"name": "cad.inspect", "arguments": {}},
    })
    assert response["result"]["isError"] is False
    body = json.loads(response["result"]["content"][0]["text"])
    assert body["summary"]["empty"] is True
    assert "cad.create_box" in body["summary"]["hint"]


def test_snapshot_without_geometry_fails_clearly(tmp_path):
    response = call(make_server(tmp_path), {
        "jsonrpc": "2.0", "id": 7, "method": "tools/call",
        "params": {"name": "cad.snapshot", "arguments": {}},
    })
    body = json.loads(response["result"]["content"][0]["text"])
    assert body["code"] == ErrorCode.STATE.value


def test_serves_newline_delimited_json(tmp_path):
    stdin = io.StringIO(
        '{"jsonrpc":"2.0","id":1,"method":"initialize"}\n'
        '\n'
        'not-a-json\n'
    )
    stdout = io.StringIO()
    make_server(tmp_path).serve(stdin=stdin, stdout=stdout)
    lines = [json.loads(line) for line in stdout.getvalue().strip().splitlines()]
    assert lines[0]["result"]["serverInfo"]["name"] == "cadcam-gateway"
    assert lines[1]["error"]["code"] == -32700


def test_pocket_requires_stock_before_cutting(tmp_path):
    server = make_server(tmp_path)
    spec = tools.get_spec("cad.pocket")
    clean = validate_params(spec, {"center_x": 5, "center_y": 5, "width": 4,
                                   "height": 4, "depth": 1})
    try:
        server.session.commit(spec, clean)
    except ToolError as error:
        assert error.code is ErrorCode.PRECONDITION
        assert "create_box" in error.hint
    else:
        raise AssertionError("空会话挖腔应被前置条件拒绝")
