# Created by Pratik Sancheti / https://github.com/psancheti6666
"""Anonymous, content-free usage telemetry.

Answers one question for the maintainers — "is Sotto being used, and is it
useful?" — without ever betraying the product's promise. NOTHING you say or
type leaves your machine; the only thing that does is an aggregate daily count.

What is sent (once a day, when a newer count exists):
    {id, date, platform, version, dictations, words}
  - id       : a random UUID generated once, stored in ~/.sotto/telemetry_id,
               tied to nothing about you.
  - date     : the local calendar day (YYYY-MM-DD).
  - platform : e.g. "darwin-arm64" (system + CPU arch — no hostname, no user).
  - version  : Sotto's version string.
  - dictations/words : integer counts for that day, read from the same
               history.jsonl the Insights window already keeps locally.

What is NEVER sent: audio, transcripts, cleaned text, app names, per-dictation
timestamps, IP address, or anything else identifying.

Opt-out (Homebrew-style — on by default, disclosed, one line to disable):
  - telemetry = false in ~/.sotto/config.toml, or
  - SOTTO_NO_TELEMETRY=1 in the environment.
Opt-out is honored before any socket is opened.

Inert until a collection endpoint is configured (_DEFAULT_ENDPOINT below or
SOTTO_TELEMETRY_URL): with none set, enabled() is False and nothing is sent —
so merging this changes no behavior until the endpoint is deployed and filled
in (see telemetry-server/README.md).
"""

import json
import logging
import os
import platform
import threading
import time
import uuid
from datetime import datetime

from . import __version__, history
from .config import CONFIG_DIR

log = logging.getLogger("sotto")

# Paste the deployed Cloudflare Worker URL here (its /ingest route) to turn
# collection on for released builds — see telemetry-server/README.md. Empty =
# telemetry is completely inert. SOTTO_TELEMETRY_URL overrides it (test seam).
_DEFAULT_ENDPOINT = "https://sotto-telemetry.psancheti6666.workers.dev/ingest"

ID_PATH = os.path.join(CONFIG_DIR, "telemetry_id")
STATE_PATH = os.path.join(CONFIG_DIR, "telemetry-state.json")
INITIAL_DELAY_S = 45.0   # let launch settle (and stay behind the update check)
POLL_S = 3600.0          # re-send today's rollup at most hourly, only if it grew
POST_TIMEOUT_S = 5.0

_disclosed = False        # one-time "telemetry is on" log line


def endpoint() -> str:
    return os.environ.get("SOTTO_TELEMETRY_URL", _DEFAULT_ENDPOINT)


def _opted_out() -> bool:
    return os.environ.get("SOTTO_NO_TELEMETRY", "").strip() not in ("", "0", "false", "False")


def enabled(cfg) -> bool:
    """True only when the user hasn't opted out AND an endpoint is configured."""
    return bool(getattr(cfg, "telemetry", True)) and not _opted_out() and bool(endpoint())


def _platform_tag() -> str:
    return f"{platform.system()}-{platform.machine()}".lower()


def install_id() -> str:
    """Read (or create once) the anonymous install id. Creating it logs a
    one-time disclosure that telemetry is on and how to turn it off."""
    global _disclosed
    try:
        with open(ID_PATH) as f:
            existing = f.read().strip()
        if existing:
            return existing
    except OSError:
        pass
    new_id = uuid.uuid4().hex
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(ID_PATH, "w") as f:
            f.write(new_id)
    except OSError as e:
        log.warning("telemetry: could not store install id (%s)", e)
        return new_id  # transient id; still no content ever leaves
    if not _disclosed:
        log.info("anonymous usage stats are on (counts only, never your words) "
                 "— disable with telemetry=false in ~/.sotto/config.toml or "
                 "SOTTO_NO_TELEMETRY=1")
        _disclosed = True
    return new_id


def _today(now: datetime = None) -> str:
    return (now or datetime.now().astimezone()).date().isoformat()


def _today_counts(day: str, path: str = history.HISTORY_PATH) -> tuple:
    """(dictations, words) recorded in history.jsonl for the given local day."""
    dictations = words = 0
    for e in history.read_entries(path):
        if str(e.get("ts", ""))[:10] == day:
            dictations += 1
            try:
                words += int(e.get("words", 0) or 0)
            except (TypeError, ValueError):
                pass  # a locally-corrupt count must not derail the heartbeat
    return dictations, words


def _load_state() -> dict:
    try:
        with open(STATE_PATH) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(payload: dict):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(STATE_PATH, "w") as f:
            json.dump({"date": payload["date"], "dictations": payload["dictations"],
                       "words": payload["words"]}, f)
    except OSError as e:
        log.warning("telemetry: could not save state (%s)", e)


def build_payload(now: datetime = None, history_path: str = history.HISTORY_PATH) -> dict:
    """The exact object that would be sent. Pure — no network, no side effects
    beyond ensuring the install id exists. Returns None if no id is available."""
    id_ = install_id()
    if not id_:
        return None
    day = _today(now)
    dictations, words = _today_counts(day, history_path)
    return {"id": id_, "date": day, "platform": _platform_tag(),
            "version": __version__, "dictations": dictations, "words": words}


def _should_send(payload: dict, state: dict) -> bool:
    """Send when it's a new day (heartbeat, even at 0 counts → the user was
    active today) or today's counts have grown since the last send."""
    if state.get("date") != payload["date"]:
        return True
    return (payload["dictations"] > state.get("dictations", 0)
            or payload["words"] > state.get("words", 0))


def maybe_send(cfg, now: datetime = None, _post=None) -> bool:
    """Send today's rollup if enabled and it's new/grown. Never raises; returns
    True only when a payload was actually accepted."""
    if not enabled(cfg):
        return False
    # Everything below is wrapped: a corrupt history line, a state-file error,
    # or a network failure must fail silent — never raise out of the daemon
    # loop (which would kill all future heartbeats) and never touch dictation.
    try:
        payload = build_payload(now)
        if payload is None or not _should_send(payload, _load_state()):
            return False
        post = _post
        if post is None:
            import requests
            post = lambda url, json_: requests.post(url, json=json_, timeout=POST_TIMEOUT_S)
        resp = post(endpoint(), payload)
        if resp is not None and getattr(resp, "status_code", 200) >= 400:
            return False
    except Exception as e:
        log.debug("telemetry send failed (%s)", e)
        return False
    _save_state(payload)
    return True


def _loop(cfg):
    time.sleep(INITIAL_DELAY_S)
    while True:
        maybe_send(cfg)
        time.sleep(POLL_S)


def start(cfg):
    """Launch the background heartbeat. No-op when disabled (opted out or no
    endpoint), so this is safe to call unconditionally on every platform."""
    if not enabled(cfg):
        return
    threading.Thread(target=_loop, args=(cfg,), daemon=True, name="telemetry").start()
