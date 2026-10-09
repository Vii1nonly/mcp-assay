"""An MCP server with deliberate bugs, used to prove the harness catches them.

Written against raw JSON-RPC instead of the MCP SDK on purpose: the SDK would
validate arguments and block the exact misbehavior we want to exhibit.

Planted bugs:
  read_file   declares `path` as required, then happily succeeds without it.
  get_status  declares an output schema, then returns data that violates it.
  hang        stalls long enough to trip the harness timeout (`seconds`, default 5).
  crash       ends the server process mid-call without replying.
  echo        behaves correctly, so not every test fails.

Correct behaviour the harness must still credit:
  strict_echo      rejects a missing `text` with a JSON-RPC error (-32602).
  ambiguous_reject replies with -32000, a code the SDK also uses locally.
  timeout_code_reject rejects a missing `text` with -32001; the harness never
                   arms an SDK read timeout, so this can only come from the server.

More bugs the harness must grade as failures:
  malformed_reply  answers with a result that breaks the CallToolResult shape.
  method_missing   answers with -32601, so it never evaluated the arguments.

A bug the preflight must warn about rather than blame on the suite:
  bad_input_schema declares an inputSchema that is not valid JSON Schema.

Options, for testing the tools/list preflight:
  --page-size N       serve tools/list in pages of N tools, linked by nextCursor.
  --fail-tools-list   answer tools/list with a JSON-RPC error.
  --off-spec-tool     also list a tool whose inputSchema lacks type "object",
                      which breaks the MCP tools/list shape.
"""

import argparse
import json
import os
import sys
import time

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
    {
        "name": "hang",
        "description": "Eventually replies, but not soon enough to be useful.",
        "inputSchema": {"type": "object", "properties": {"seconds": {"type": "number"}}},
    },
    {
        "name": "crash",
        "description": "Exits the server process before replying.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "strict_echo",
        "description": "Return the text it was given; rejects bad input via JSON-RPC.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "ambiguous_reject",
        "description": "Always replies with a JSON-RPC error in the server range.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "timeout_code_reject",
        "description": "Rejects a missing `text` with JSON-RPC error -32001.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "malformed_reply",
        "description": "Answers with `content` as a string instead of a list.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "method_missing",
        "description": "Answers every call with -32601 Method not found.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "bad_input_schema",
        "description": "Works, but declares a type JSON Schema does not have.",
        "inputSchema": {"type": "object", "properties": {"n": {"type": "nonsense"}}},
    },
]


# BUG: MCP requires every inputSchema to be an object schema (type "object").
OFF_SPEC_TOOL = {
    "name": "untyped",
    "description": "Listed only with --off-spec-tool.",
    "inputSchema": {"properties": {"a": {"type": "string"}}},
}


class RpcError(Exception):
    """Raised by a tool to reply with a JSON-RPC error instead of a result."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


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

    if name == "hang":
        # BUG: blocks the whole server, so nothing else can be served meanwhile.
        time.sleep(arguments.get("seconds", 5))
        return text_result("sorry for the wait")

    if name == "crash":
        # BUG: dies mid-call, so the request never gets a reply.
        sys.stdout.flush()
        os._exit(1)

    if name == "get_status":
        # BUG: violates the declared outputSchema (status is a number, uptime absent).
        return {
            "content": [{"type": "text", "text": "ok"}],
            "structuredContent": {"status": 123},
        }

    if name == "strict_echo":
        if "text" not in arguments:
            raise RpcError(-32602, "Invalid params: missing required argument 'text'")
        return text_result(arguments["text"])

    if name == "ambiguous_reject":
        raise RpcError(-32000, "Server error")

    if name == "timeout_code_reject":
        if "text" not in arguments:
            raise RpcError(-32001, "Invalid params: missing required argument 'text'")
        return text_result(arguments["text"])

    if name == "malformed_reply":
        # BUG: `content` must be a list of content blocks.
        return {"content": "oops"}

    if name == "method_missing":
        # BUG: never looks at the arguments, so this is not a rejection of them.
        raise RpcError(-32601, "Method not found")

    if name == "bad_input_schema":
        return text_result("ok")

    return {"content": [{"type": "text", "text": f"unknown tool {name}"}], "isError": True}


def list_tools(cursor, options):
    if options.fail_tools_list:
        raise RpcError(-32603, "Internal error: cannot list tools")
    tools = TOOLS + [OFF_SPEC_TOOL] if options.off_spec_tool else TOOLS
    if not options.page_size:
        return {"tools": tools}
    start = int(cursor or 0)
    page = {"tools": tools[start : start + options.page_size]}
    if start + options.page_size < len(tools):
        page["nextCursor"] = str(start + options.page_size)
    return page


def handle(request, options):
    method = request.get("method")
    params = request.get("params") or {}

    if method == "initialize":
        return {
            "protocolVersion": params.get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "broken-server", "version": "0.1.0"},
        }
    if method == "tools/list":
        return list_tools(params.get("cursor"), options)
    if method == "tools/call":
        return call_tool(params.get("name"), params.get("arguments") or {})
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--page-size", type=int)
    parser.add_argument("--fail-tools-list", action="store_true")
    parser.add_argument("--off-spec-tool", action="store_true")
    options, _ = parser.parse_known_args()  # other arguments are ignored
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

        try:
            result = handle(request, options)
        except RpcError as exc:
            error = {"code": exc.code, "message": exc.message}
            send({"jsonrpc": "2.0", "id": request["id"], "error": error})
            continue
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
