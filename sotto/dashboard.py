# Created by Pratik Sancheti / https://github.com/psancheti6666
"""Local dashboard: stdlib HTTP server on 127.0.0.1 serving dashboard.html.

Strictly local — binds to the loopback interface only, serves one
self-contained page (no external requests), and reads ~/.sotto/history.jsonl
fresh on every request so it's always current.
"""

import json
import logging
import os
import platform
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import dictionary as dictionary_mod
from . import history
from .config import DICTIONARY_PATH

log = logging.getLogger("sotto")

PAGE_PATH = os.path.join(os.path.dirname(__file__), "dashboard.html")


def _user_name() -> str:
    """First name for the greeting — the account's full name where available."""
    try:
        import pwd
        gecos = pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0].strip()
        if gecos:
            return gecos.split()[0]
    except Exception:
        pass
    return os.environ.get("USER", "")


def _host_name() -> str:
    return platform.node().removesuffix(".local").removesuffix(".lan")


class _Handler(BaseHTTPRequestHandler):
    history_path = None      # set per-server; None = the default ~/.sotto files
    dictionary_path = None
    dictionary = None        # the running app's Dictionary, reloaded after edits

    def do_GET(self):
        route = self.path.split("?", 1)[0]
        if route in ("/", "/index.html"):
            try:
                with open(PAGE_PATH, "rb") as f:
                    body = f.read()
            except OSError as e:
                self.send_error(500, str(e))
                return
            self._respond(body, "text/html; charset=utf-8")
        elif route == "/api/history":
            entries = history.read_entries(**self._path_kw("history_path"))
            payload = {"entries": sorted(entries, key=lambda e: str(e.get("ts", "")),
                                         reverse=True),  # newest first for the UI
                       "stats": history.compute_stats(entries),
                       "meta": {"user": _user_name(), "host": _host_name()}}
            self._json(payload)
        elif route == "/api/dictionary":
            self._json({"terms": dictionary_mod.read_terms(self._dict_path())})
        else:
            self.send_error(404)

    def do_POST(self):
        # Mutations require the X-Sotto header: a hostile web page can fire
        # cross-origin POSTs at localhost, but a custom header forces a CORS
        # preflight, which this server never approves.
        if self.headers.get("X-Sotto") != "1":
            self.send_error(403)
            return
        if self.path != "/api/dictionary":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            add, remove = body.get("add", ""), body.get("remove", "")
        except (ValueError, TypeError) as e:
            self.send_error(400, str(e))
            return
        path = self._dict_path()
        if add:
            terms = dictionary_mod.add_term(add, path)
        elif remove:
            terms = dictionary_mod.remove_term(remove, path)
        else:
            self.send_error(400, "expected 'add' or 'remove'")
            return
        if self.dictionary:
            self.dictionary.reload()  # running app picks the change up now
        self._json({"terms": terms})

    def _dict_path(self):
        return self.dictionary_path or DICTIONARY_PATH

    def _path_kw(self, attr):
        value = getattr(self, attr)
        return {"path": value} if value else {}

    def _json(self, payload: dict):
        self._respond(json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                      "application/json; charset=utf-8")

    def _respond(self, body: bytes, content_type: str):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # keep request lines out of the terminal
        log.debug("dashboard: " + fmt, *args)


def start(port: int, history_path: str = None, dictionary_path: str = None,
          dictionary=None):
    """Serve the dashboard from a daemon thread. Returns the server, or None
    if the port is taken (Sotto keeps running without a dashboard)."""
    handler = type("Handler", (_Handler,),
                   {"history_path": history_path,
                    "dictionary_path": dictionary_path,
                    "dictionary": dictionary})
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
