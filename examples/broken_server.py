"""An MCP server with deliberate bugs, used to prove the harness catches them.

Written against raw JSON-RPC instead of the MCP SDK on purpose: the SDK would
validate arguments and block the exact misbehavior we want to exhibit.

Planted bugs:
  read_file   declares `path` as required, then happily succeeds without it.
  get_status  declares an output schema, then returns data that violates it.
  echo        behaves correctly, so not every test fails.
"""

import json
import sys

TOOLS = [
    {
        "name": "echo",
        "description": "Return the text it was given.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a file. Requires `path`.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "get_status",
        "description": "Report server status.",
        "inputSchema": {"type": "object", "properties": {}},
        "outputSchema": {
            "type": "object",
            "properties": {"status": {"type": "string"}, "uptime": {"type": "number"}},
            "required": ["status", "uptime"],
        },
    },
]


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def text_result(text):
    return {"content": [{"type": "text", "text": text}]}


def call_tool(name, arguments):
    if name == "echo":
        if "text" not in arguments:
            return {"content": [{"type": "text", "text": "missing text"}], "isError": True}
        return text_result(arguments["text"])

    if name == "read_file":
        # BUG: `path` is declared required but never checked.
        path = arguments.get("path", "<no path given>")
        return text_result(f"contents of {path}")

    if name == "get_status":
        # BUG: violates the declared outputSchema (status is a number, uptime absent).
        return {
            "content": [{"type": "text", "text": "ok"}],
            "structuredContent": {"status": 123},
        }

    return {"content": [{"type": "text", "text": f"unknown tool {name}"}], "isError": True}


def handle(request):
    method = request.get("method")
    params = request.get("params") or {}

    if method == "initialize":
        return {
            "protocolVersion": params.get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "broken-server", "version": "0.1.0"},
        }
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        return call_tool(params.get("name"), params.get("arguments") or {})
    return None


def main():
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue

        if "id" not in request:
            continue  # a notification: no reply, which is correct behavior

        result = handle(request)
        if result is None:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    "error": {"code": -32601, "message": f"unknown method {request.get('method')}"},
                }
            )
        else:
            send({"jsonrpc": "2.0", "id": request["id"], "result": result})


if __name__ == "__main__":
    main()
