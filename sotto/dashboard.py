"""Local dashboard: stdlib HTTP server on 127.0.0.1 serving dashboard.html.

Strictly local — binds to the loopback interface only, serves one
self-contained page (no external requests), and reads ~/.sotto/history.jsonl
fresh on every request so it's always current.
"""

import json
import logging
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import history

log = logging.getLogger("sotto")

PAGE_PATH = os.path.join(os.path.dirname(__file__), "dashboard.html")


class _Handler(BaseHTTPRequestHandler):
    history_path = None  # set per-server; None = the default ~/.sotto file

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            try:
                with open(PAGE_PATH, "rb") as f:
                    body = f.read()
            except OSError as e:
                self.send_error(500, str(e))
                return
            self._respond(body, "text/html; charset=utf-8")
        elif self.path == "/api/history":
            kwargs = {"path": self.history_path} if self.history_path else {}
            entries = history.read_entries(**kwargs)
            payload = {"entries": entries[::-1],  # newest first for the UI
                       "stats": history.compute_stats(entries)}
            self._respond(json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                          "application/json; charset=utf-8")
        else:
            self.send_error(404)

    def _respond(self, body: bytes, content_type: str):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # keep request lines out of the terminal
        log.debug("dashboard: " + fmt, *args)


def start(port: int, history_path: str = None):
    """Serve the dashboard from a daemon thread. Returns the server, or None
    if the port is taken (Sotto keeps running without a dashboard)."""
    handler = type("Handler", (_Handler,), {"history_path": history_path})
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError as e:
        log.warning("dashboard unavailable — port %d: %s", port, e)
        return None
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("dashboard at http://127.0.0.1:%d", server.server_address[1])
    return server


def open_in_browser(port: int):
    # webbrowser resolves to `open` on macOS and xdg-open on Linux.
    threading.Thread(target=webbrowser.open,
                     args=(f"http://127.0.0.1:{port}",), daemon=True).start()
