# Created by Pratik Sancheti / https://github.com/psancheti6666
"""Self-contained LLM: run the bundled ollama when no server is reachable.

Only the py2app bundle ships an ollama runtime (Contents/Resources/ollama/),
so from a git checkout ensure() is a no-op beyond one fast probe — ./run.sh
users keep managing Ollama themselves. Inside the bundle, if the configured
ollama_url answers (brew install, another app's server) it is used as-is;
otherwise the bundled binary is spawned as a hidden child bound to the same
host:port, the model is pulled if absent (log-only; progress UI arrives with
the first-run experience), and the child is terminated when Sotto quits.

Models live in ollama's default store (~/.ollama), mirroring the ASR models
in the Hugging Face cache; consolidating downloads under ~/.sotto is part of
the first-run milestone.
"""
import atexit
import json
import logging
import os
import subprocess
import time
import urllib.parse

import requests

from .config import CONFIG_DIR
from .platform import IS_LINUX, IS_WINDOWS

log = logging.getLogger("sotto")

READY_TIMEOUT_S = 15.0
_child: subprocess.Popen | None = None
_observer = None  # retains the NSApplicationWillTerminateNotification listener


def bundled_binary() -> str | None:
    """The ollama binary we may spawn: inside the .app on macOS, or (Linux)
    a system install / the first-run-downloaded runtime. None from a mac
    checkout — and on Linux until the runtime is downloaded."""
    res = os.environ.get("RESOURCEPATH")  # set by the py2app bootstrap
    if res:
        path = os.path.join(res, "ollama", "ollama")
        return path if os.access(path, os.X_OK) else None
    if IS_LINUX or IS_WINDOWS:
        from . import ollama_runtime
        return ollama_runtime.resolve()
    return None


def _reachable(url: str) -> bool:
    try:
        return requests.get(f"{url}/api/version", timeout=2).ok
    except requests.RequestException:
        return False


def _host_port(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return f"{parts.hostname or '127.0.0.1'}:{parts.port or 11434}"


def ensure(cfg, on_pull_progress=None) -> None:
    """Make cfg.ollama_url answer if possible. Never raises; the cleaner's
    regex fallback already covers a server that stays unreachable.
    on_pull_progress (optional) receives an int percentage while a missing
    model downloads — the first-run window feeds its bar with it."""
    url = cfg.ollama_url.rstrip("/")
    if _reachable(url):
        _ensure_model(url, cfg.ollama_model, on_pull_progress)
        return
    binary = bundled_binary()
    if binary is None:
        return  # checkout / no bundle: today's behavior, regex fallback
    _spawn(binary, url)
    if _wait_ready(url):
        _ensure_model(url, cfg.ollama_model, on_pull_progress)
    else:
        log.warning("bundled ollama did not become ready on %s — "
                    "cleanup falls back to regex", url)


def _spawn(binary: str, url: str):
    global _child
    logfile = open(os.path.join(CONFIG_DIR, "ollama-server.log"), "ab")
    env = dict(os.environ, OLLAMA_HOST=_host_port(url))
    # Fresh machines keep all Sotto data under ~/.sotto; machines that
    # already have an ~/.ollama store reuse it (no duplicate 2.5 GB pull).
    default_store = os.path.expanduser("~/.ollama/models")
    if "OLLAMA_MODELS" not in env and not os.path.isdir(
            os.path.join(default_store, "manifests")):
        env["OLLAMA_MODELS"] = os.path.expanduser("~/.sotto/ollama")
    # New session: a Ctrl+C aimed at Sotto shouldn't SIGINT the child
    # mid-request; shutdown() below is the one way it exits. If Sotto is
    # kill -9'd the orphan keeps serving and the next launch simply finds
    # the port answering and adopts it. Windows: start_new_session is
    # silently ignored — CREATE_NEW_PROCESS_GROUP is the same intent there
    # (console Ctrl events don't reach the child), and CREATE_NO_WINDOW is
    # required on top: ollama.exe is a console binary, so without it Windows
    # pops a visible black console on the desktop — and closing that window
    # kills the engine (seen live, 2026-07-21 friend round). getattr keeps
    # the flags patch-testable off-Windows (both are 0 there).
    popen_kwargs = (
        {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                          | getattr(subprocess, "CREATE_NO_WINDOW", 0)}
        if IS_WINDOWS else {"start_new_session": True})
    _child = subprocess.Popen([binary, "serve"], env=env,
                              stdout=logfile, stderr=logfile,
                              **popen_kwargs)
    log.info("started bundled ollama (pid %d) on %s", _child.pid,
             _host_port(url))
    atexit.register(shutdown)
    _register_terminate_observer()


def _wait_ready(url: str) -> bool:
    deadline = time.monotonic() + READY_TIMEOUT_S
    while time.monotonic() < deadline:
        if _child is not None and _child.poll() is not None:
            log.warning("bundled ollama exited with code %s — see %s",
                        _child.returncode,
                        os.path.join(CONFIG_DIR, "ollama-server.log"))
            return False
        if _reachable(url):
            return True
        time.sleep(0.3)
    return False


def _ensure_model(url: str, model: str, on_progress=None):
    try:
        tags = requests.get(f"{url}/api/tags", timeout=5).json()
        have = {m.get("name", "") for m in tags.get("models", [])}
    except (requests.RequestException, ValueError):
        return
    if model in have or f"{model}:latest" in have:
        return
    log.info("LLM model %s not present — pulling (~2.5 GB, cleanup uses the "
             "regex fallback until it finishes)…", model)
    try:
        with requests.post(f"{url}/api/pull",
                           json={"model": model, "stream": True},
                           stream=True, timeout=None) as r:
            last = 0.0
            for line in r.iter_lines():
                if not line:
                    continue
                status = json.loads(line)
                if status.get("error"):
                    log.warning("model pull failed: %s", status["error"])
                    return
                total, done = status.get("total"), status.get("completed")
                if total and done:
                    if on_progress is not None:
                        on_progress(100 * done // total)
                    if time.monotonic() - last > 15:
                        last = time.monotonic()
                        log.info("pulling %s: %d%%", model,
                                 100 * done // total)
        log.info("model %s ready", model)
    except (requests.RequestException, ValueError) as e:
        log.warning("model pull failed: %s", e)


def shutdown(*_):
    global _child
    if _child is None or _child.poll() is not None:
        return
    _child.terminate()
    try:
        _child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _child.kill()
    _child = None


def _register_terminate_observer():
    """NSApp.terminate_ exits the process without unwinding Python, so atexit
    alone may not run — hook the AppKit notification too (bundle = macOS)."""
    global _observer
    if _observer is not None:
        return
    try:
        from Foundation import NSNotificationCenter, NSObject

        class _Terminator(NSObject):
            def quit_(self, _note):
                shutdown()

        _observer = _Terminator.alloc().init()
        NSNotificationCenter.defaultCenter(
        ).addObserver_selector_name_object_(
            _observer, "quit:", "NSApplicationWillTerminateNotification",
            None)
    except Exception:
        pass  # atexit still covers plain-python exits
