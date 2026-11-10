"""``rebench mcp``: the agent operations as an MCP server on stdio.

A minimal implementation of the Model Context Protocol (JSON-RPC 2.0, one
message per line) with no dependency beyond the standard library: it answers
``initialize``, ``tools/list`` and ``tools/call`` and ignores notifications.
Register it with an MCP client as::

    {"command": "python3", "args": ["-m", "rebench", "mcp", "--project", "/path/to/device"]}

Tools (the design's dotted names, with underscores because MCP clients do not
all accept dots): ``project_load``, ``rules_check``, ``layout_dry_run``,
``layout_apply``, ``view_render``. ``view_render`` returns the PNG as an image
so the agent can look at the panel.
"""

from __future__ import annotations

import base64
import json
import sys
import tempfile
from pathlib import Path
from typing import IO

from reblend.project.lua_reader import LuaConfigError

from . import agent
from .ops import OpError

__all__ = ["TOOLS", "handle", "run_stdio"]

PROTOCOL_VERSION = "2024-11-05"

_ROOT = {"type": "string", "description": "RE project root (the folder holding GUI2D/). "
                                          "Optional when the server was started with --project."}
_OPS = {
    "type": "array",
    "items": {"type": "object"},
    "description": (
        "Edit operations, applied as one batch. Panel coordinates are hi-res px. "
        '{"op":"move","panel":"front","node":"knob_tone","x":980,"y":210}; '
        '{"op":"nudge","panel":"front","node":"knob_tone","dx":5,"dy":0}; '
        '{"op":"set_frames","panel":"front","node":"knob_tone","frames":61}; '
        '{"op":"set_frame_size","sprite":"knob_tone","w":120,"h":120}; '
        '{"op":"set_shape","sprite":"knob_tone","shape":{"type":"knob"}}; '
        '{"op":"label","id":"lbl-tone","panel":"front","text":"TONE","x":990,"y":180,'
        '"size_px":48,"functional":true}; {"op":"remove_label","id":"lbl-tone"}'
    ),
}
_PANEL = {"type": "string", "enum": list(agent.PANELS)}

TOOLS = [
    {
        "name": "project_load",
        "description": "Panels, every node with its position, frame size and frame count, "
                       "and the sidecar's labels and sprite sizes.",
        "inputSchema": {"type": "object", "properties": {"project": _ROOT}},
    },
    {
        "name": "rules_check",
        "description": "Run the shared rule catalogue. Each finding carries its rule's source: "
                       "req (submission requirement), spec, guide, obs (observed in Reason/"
                       "RE2DRender) or house.",
        "inputSchema": {"type": "object", "properties": {"project": _ROOT}},
    },
    {
        "name": "layout_dry_run",
        "description": "Apply edit operations in memory and report the findings they add and "
                       "resolve. Writes nothing.",
        "inputSchema": {"type": "object", "properties": {"project": _ROOT, "ops": _OPS},
                        "required": ["ops"]},
    },
    {
        "name": "layout_apply",
        "description": "Apply edit operations and write device_2D.lua (positions, frames) and "
                       "GUI2D/rebench.json (frame sizes, shapes, labels). All or nothing: a "
                       "refused batch writes nothing.",
        "inputSchema": {"type": "object", "properties": {"project": _ROOT, "ops": _OPS},
                        "required": ["ops"]},
    },
    {
        "name": "view_render",
        "description": "Screenshot a panel with Reason's reserved areas and the findings drawn "
                       "in. Optional ops are previewed, not written. scale 0.5 is the lo-res view.",
        "inputSchema": {"type": "object", "properties": {
            "project": _ROOT, "panel": _PANEL, "ops": _OPS,
            "scale": {"type": "number", "default": 0.5},
            "frame": {"type": "integer", "default": 0},
            "overlays": {"type": "boolean", "default": True},
            "out": {"type": "string", "description": "Also save the PNG here."},
        }, "required": ["panel"]},
    },
]


def _text(obj) -> dict:
    return {"type": "text", "text": json.dumps(obj, indent=2)}


def _call(name: str, args: dict, default_root: str | None) -> dict:
    root = args.get("project") or default_root
    if not root:
        raise ValueError("no project: pass 'project' or start the server with --project")
    if name == "project_load":
        return {"content": [_text(agent.project_load(root))]}
    if name == "rules_check":
        return {"content": [_text(agent.rules_check(root))]}
    if name == "layout_dry_run":
        return {"content": [_text(agent.layout_dry_run(root, args.get("ops", [])))]}
    if name == "layout_apply":
        return {"content": [_text(agent.layout_apply(root, args.get("ops", [])))]}
    if name == "view_render":
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(args["out"]) if args.get("out") else Path(tmp) / "panel.png"
            reply = agent.view_render(
                root, args["panel"], out=out, scale=float(args.get("scale", 0.5)),
                frame=int(args.get("frame", 0)), overlays=bool(args.get("overlays", True)),
                ops=args.get("ops"))
            data = base64.b64encode(out.read_bytes()).decode("ascii")
        if not args.get("out"):
            reply.pop("path", None)
        return {"content": [{"type": "image", "data": data, "mimeType": "image/png"},
                            _text(reply)]}
    raise KeyError(name)


def handle(message: dict, default_root: str | None = None) -> dict | None:
    """Answer one JSON-RPC message; None for notifications."""
    method = message.get("method")
    msg_id = message.get("id")
    if msg_id is None:
        return None

    def ok(result):
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    if method == "initialize":
        return ok({
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "rebench", "version": "0.2"},
        })
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name", "")
        if name not in {t["name"] for t in TOOLS}:
            return {"jsonrpc": "2.0", "id": msg_id,
                    "error": {"code": -32602, "message": f"unknown tool {name!r}"}}
        try:
            return ok(_call(name, params.get("arguments") or {}, default_root))
        except OpError as exc:
            return ok({"isError": True, "content": [_text({"refused": True,
                                                            "reasons": exc.reasons})]})
        except (LuaConfigError, OSError, ValueError, KeyError) as exc:
            return ok({"isError": True, "content": [{"type": "text", "text": str(exc)}]})
    return {"jsonrpc": "2.0", "id": msg_id,
            "error": {"code": -32601, "message": f"method not found: {method}"}}


def run_stdio(default_root: str | None = None, stdin: IO[str] | None = None,
              stdout: IO[str] | None = None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            reply = {"jsonrpc": "2.0", "id": None,
                     "error": {"code": -32700, "message": "parse error"}}
        else:
            reply = handle(message, default_root)
        if reply is not None:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()
    return 0
