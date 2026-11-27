"""``rebench serve``: the layout canvas as a small local web app.

Standard library only (``http.server``). Edits in the browser are *staged*:
each drag or label change is an operation kept in the page, previewed through
a dry run (``POST /api/preview``), and written to ``device_2D.lua`` and
``rebench.json`` only when you press Write (``POST /api/write``). Undo is
dropping staged ops before writing, or git after.

Binds to 127.0.0.1 by default; it reads and writes the project's files, so it
is not meant to be exposed on a network.
"""

from __future__ import annotations

import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from .ops import OpError, apply_ops
from .profile import load_profile
from .project import BenchProject
from .render import panel_size, render_svg

__all__ = ["serve"]

STATIC = Path(__file__).parent / "static"


def _state(root: Path, ops: list[dict], frame: int, panel: str | None) -> dict:
    profile = load_profile(root)
    result = apply_ops(root, ops, dry_run=True, profile=profile)
    project = result.project
    panels = [p for p in ("front", "back", "folded_front", "folded_back")
              if p in project.link.hdgui.panels]
    panel = panel if panel in panels else panels[0]

    def href(path: str) -> str | None:
        png = project.link.gui2d_dir / f"{path}.png"
        return f"/gui2d/{quote(path)}.png" if png.is_file() else None

    findings = result.check.findings
    return {
        "panel": panel,
        "panels": panels,
        "size": panel_size(project, panel),
        "svg": render_svg(project, panel, findings, profile, href=href, frame=frame),
        "applied": result.applied,
        "added": [f.to_dict() for f in result.added],
        "resolved": [f.to_dict() for f in result.resolved],
        "findings": [f.to_dict() for f in findings],
        "labels": [vars(l) for l in project.sidecar.labels],
    }


def make_handler(root: Path):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # keep the terminal quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

        def do_GET(self):
            url = urlparse(self.path)
            if url.path in ("/", "/index.html"):
                return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            if url.path.startswith("/gui2d/"):
                name = Path(unquote(url.path[len("/gui2d/"):])).name  # no traversal
                file = root / "GUI2D" / name
                if file.suffix.lower() == ".png" and file.is_file():
                    return self._send(200, file.read_bytes(), "image/png")
                return self._send(404, b"not found", "text/plain")
            if url.path == "/api/state":
                q = parse_qs(url.query)
                try:
                    state = _state(root, [], int(q.get("frame", ["0"])[0]), q.get("panel", [None])[0])
                except Exception as exc:  # surface load errors in the page
                    return self._json(500, {"error": str(exc)})
                return self._json(200, state)
            self._send(404, b"not found", "text/plain")

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self._json(400, {"error": "body is not JSON"})
            ops = body.get("ops", [])
            try:
                if self.path == "/api/preview":
                    return self._json(200, _state(root, ops, int(body.get("frame", 0)), body.get("panel")))
                if self.path == "/api/write":
                    result = apply_ops(root, ops, dry_run=False)
                    return self._json(200, result.to_dict())
                if self.path == "/api/greybox":
                    from .greybox import build_greybox
                    return self._json(200, build_greybox(root).to_dict())
            except OpError as exc:
                return self._json(422, {"error": "refused", "reasons": exc.reasons})
            except (OSError, ValueError) as exc:
                return self._json(500, {"error": str(exc)})
            self._send(404, b"not found", "text/plain")

    return Handler


def serve(root: Path | str, host: str = "127.0.0.1", port: int = 8765) -> None:
    root = Path(root).resolve()
    BenchProject.load(root)  # fail fast on an unreadable project
    httpd = ThreadingHTTPServer((host, port), make_handler(root))
    print(f"RE Bench: {root} on http://{host}:{httpd.server_address[1]}/  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
