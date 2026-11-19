"""MCP 网关服务器：把契约表暴露成 MCP 工具，走 stdio JSON-RPC。

协议层刻意做得很薄（三个方法 + ping），因为论文的贡献在契约与事务，
不在传输层。这样也便于替换成官方 MCP SDK：只要 `tools/list` 与
`tools/call` 的输出结构一致，上层智能体无需改动。

传输格式：每行一个 JSON（MCP stdio 约定）。stdout 只允许出现协议消息，
日志一律走 stderr —— 否则会污染流、把客户端解析搞崩。
"""

import json
import sys

from cadcam.gateway import tools
from cadcam.gateway.contracts import ErrorCode, ToolError, validate_params
from cadcam.gateway.session import new_session

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "cadcam-gateway", "version": "0.1.0"}

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602


class GatewayServer:
    def __init__(self, session=None, log=None):
        self.session = session or new_session()
        self.log = log or sys.stderr

    # ---------- 协议 ----------

    def handle(self, request: dict):
        """返回响应对象；通知类请求返回 None（不响应）。"""
        method = request.get("method")
        request_id = request.get("id")
        params = request.get("params") or {}

        if method is None:
            return _error(request_id, INVALID_REQUEST, "缺少 method")

        if method == "initialize":
            return _ok(request_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            })

        if method.startswith("notifications/"):
            return None

        if method == "ping":
            return _ok(request_id, {})

        if method == "tools/list":
            return _ok(request_id, {"tools": [spec.to_mcp() for spec in tools.TOOLS]})

        if method == "tools/call":
            return self._call_tool(request_id, params)

        return _error(request_id, METHOD_NOT_FOUND, "不支持的方法：%s" % method)

    def _call_tool(self, request_id, params):
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not name:
            return _error(request_id, INVALID_PARAMS, "缺少工具名 name")

        try:
            spec = tools.get_spec(name)
            clean = validate_params(spec, arguments)
            result = self.session.commit(spec, clean)
        except ToolError as error:
            self.log.write("[cadcam] %s -> %s %s\n" % (name, error.code.value, error.message))
            return _ok(request_id, {
                "content": [{"type": "text", "text": json.dumps(error.to_dict(), ensure_ascii=False)}],
                "isError": True,
            })

        text = json.dumps(result, ensure_ascii=False)
        self.log.write("[cadcam] %s ok state=%s\n"
                       % (name, (result.get("state") or {}).get("state_hash")))
        return _ok(request_id, {
            "content": [{"type": "text", "text": text}],
            "isError": False,
        })

    # ---------- 传输 ----------

    def serve(self, stdin=None, stdout=None):
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError as exc:
                _write(stdout, _error(None, PARSE_ERROR, "JSON 解析失败：%s" % exc))
                continue

            if isinstance(request, list):  # 批量请求
                responses = [r for r in (self.handle(item) for item in request) if r]
                if responses:
                    _write(stdout, responses)
                continue

            response = self.handle(request)
            if response is not None:
                _write(stdout, response)
        return 0


def _ok(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": code, "message": message}}


def _write(stdout, payload):
    stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    stdout.flush()


def main():
    return GatewayServer().serve()


if __name__ == "__main__":
    sys.exit(main())
