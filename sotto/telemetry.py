# Created by Pratik Sancheti / https://github.com/psancheti6666
"""Anonymous, content-free usage telemetry.

Answers one question for the maintainers — "is Sotto being used, and is it
useful?" — without ever betraying the product's promise. NOTHING you say or
type leaves your machine; the only thing that does is an aggregate daily count.

What is sent — a per-day rollup, re-sent through the day as it grows (at most
every 15 minutes, and only when the counts changed), plus one final top-up for
the previous day after midnight so the last dictations of an evening aren't
lost (they were: 2392 sent vs 2837 spoken, 2026-07-22):
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

Opt-in with an explicit ask. On first run (and once for anyone updating from an
older version) Sotto shows a one-time "Share anonymous usage stats?" dialog —
Enable (the default button) / No thanks — and remembers the answer in
~/.sotto/telemetry-consent.json. Nothing is sent until the user says Enable.
Overrides: telemetry = true|false in ~/.sotto/config.toml wins over the prompt
(and suppresses it); SOTTO_NO_TELEMETRY=1 force-disables everything. The gate is
checked before any socket is opened.

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
CONSENT_PATH = os.path.join(CONFIG_DIR, "telemetry-consent.json")
CONSENT_DELAY_S = 6.0    # let the UI settle before the one-time consent dialog
POLL_S = 900.0           # re-send today's rollup every 15 min, only if it grew
#                          (hourly felt frozen on the dashboard; ~a few dozen
#                          tiny requests/day/user is nothing on the free tier)
POST_TIMEOUT_S = 5.0

_disclosed = False        # one-time "telemetry is on" log line


def endpoint() -> str:
    return os.environ.get("SOTTO_TELEMETRY_URL", _DEFAULT_ENDPOINT)


def _opted_out() -> bool:
    return os.environ.get("SOTTO_NO_TELEMETRY", "").strip() not in ("", "0", "false", "False")


# ------------------------------------------------------------------ consent --

def consent_recorded() -> bool:
    return os.path.exists(CONSENT_PATH)


def _consent_enabled() -> bool:
    try:
        with open(CONSENT_PATH) as f:
            return bool(json.load(f).get("enabled"))
    except (OSError, ValueError):
        return False


def record_consent(choice: bool):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONSENT_PATH, "w") as f:
            json.dump({"asked": True, "enabled": bool(choice)}, f)
    except OSError as e:
        log.warning("telemetry: could not save consent (%s)", e)


def enabled(cfg) -> bool:
    """On only when not force-disabled, an endpoint exists, AND the user has
    said yes — either an explicit telemetry=true in config.toml (a hard override)
    or the one-time consent prompt. Off until asked."""
    if _opted_out() or not endpoint():
        return False
    override = getattr(cfg, "telemetry", None)
    if override is not None:
        return bool(override)
    return _consent_enabled()


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
        log.info("anonymous usage stats enabled (counts only, never your words) "
                 "— turn off with telemetry=false in ~/.sotto/config.toml")
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


def _payload_for_day(day: str, history_path: str = history.HISTORY_PATH) -> dict:
    """The rollup for one local calendar day, counted from history.jsonl. Pure —
    no network, no side effects beyond ensuring the install id exists."""
    id_ = install_id()
    if not id_:
        return None
    dictations, words = _today_counts(day, history_path)
    return {"id": id_, "date": day, "platform": _platform_tag(),
            "version": __version__, "dictations": dictations, "words": words}


def build_payload(now: datetime = None, history_path: str = history.HISTORY_PATH) -> dict:
    """Today's rollup — the exact object that would be sent."""
    return _payload_for_day(_today(now), history_path)


def _should_send(payload: dict, state: dict) -> bool:
    """Send when it's a new day (heartbeat, even at 0 counts → the user was
    active today) or today's counts have grown since the last send."""
    if state.get("date") != payload["date"]:
        return True
    return (payload["dictations"] > state.get("dictations", 0)
            or payload["words"] > state.get("words", 0))


def maybe_send(cfg, now: datetime = None, _post=None) -> bool:
    """Send today's rollup if enabled and it's new/grown — after topping up the
    previous day's final count when the date has rolled over. Never raises;
    returns True only when today's payload was actually accepted."""
    if not enabled(cfg):
        return False
    # Everything below is wrapped: a corrupt history line, a state-file error,
    # or a network failure must fail silent — never raise out of the daemon
    # loop (which would kill all future heartbeats) and never touch dictation.
    try:
        post = _post
        if post is None:
            import requests
            post = lambda url, json_: requests.post(url, json=json_, timeout=POST_TIMEOUT_S)
        state = _load_state()
        today = _today(now)
        # Day rollover: the previous day's server row froze at whatever the
        # last pre-midnight send carried; anything dictated after that was
        # never re-sent (seen live: 2392 sent vs 2837 spoken, 2026-07-22).
        # History still holds the true total for that day — send it once.
        # If it fails, state stays on the old day and the whole sequence
        # retries next tick (the server upsert is monotonic, so a repeat is
        # harmless). Only the state-recorded day can be short: days with the
        # app not running have no dictations at all.
        prev = state.get("date")
        if prev and prev != today:
            final = _payload_for_day(prev)
            if final is not None and (final["dictations"] > state.get("dictations", 0)
                                      or final["words"] > state.get("words", 0)):
                resp = post(endpoint(), final)
                if resp is not None and getattr(resp, "status_code", 200) >= 400:
                    return False
        payload = build_payload(now)
        if payload is None or not _should_send(payload, state):
            return False
        resp = post(endpoint(), payload)
        if resp is not None and getattr(resp, "status_code", 200) >= 400:
            return False
    except Exception as e:
        log.debug("telemetry send failed (%s)", e)
        return False
    _save_state(payload)
    return True


_CONSENT_TITLE = "Share anonymous usage stats?"
_CONSENT_BODY = (
    "Sotto is free and open source. To understand whether it's genuinely "
    "useful and where to improve it, the Sotto team would like to collect "
    "anonymous usage — a daily count of how many times you dictate and how "
    "many words. That's all that's ever sent.\n\n"
    "Never collected: your voice, your transcripts, the apps you type into, "
    "your name, or your IP — nothing you say or type ever leaves your "
    "machine.\n\n"
    "You can change this anytime in ~/.sotto/config.toml.")


def _ask_consent():
    """Show the one-time consent dialog. True = Enable, False = No thanks,
    None = couldn't ask (no dialog available) so try again next launch.
    Enable is the default (Return) button on every platform."""
    from .platform import IS_LINUX, IS_MACOS, IS_WINDOWS
    if IS_MACOS:
        return _ask_consent_macos()
    if IS_WINDOWS:
        return _ask_consent_windows()
    if IS_LINUX:
        return _ask_consent_linux()
    return None


def _ask_consent_windows():
    # Yes is MessageBoxW's default button (button 1) → Enable. Own ctypes call
    # rather than platform.windows.ask so a display FAILURE returns None (retry
    # next launch), not a false "No thanks" the user never chose.
    try:
        import ctypes
        MB_YESNO, MB_ICONQUESTION, MB_SETFOREGROUND, MB_TOPMOST = \
            0x4, 0x20, 0x10000, 0x40000
        res = ctypes.windll.user32.MessageBoxW(
            0, _CONSENT_BODY, _CONSENT_TITLE,
            MB_YESNO | MB_ICONQUESTION | MB_SETFOREGROUND | MB_TOPMOST)
        if res == 6:      # IDYES
            return True
        if res == 7:      # IDNO
            return False
        return None       # 0 = couldn't display → undecided, retry next launch
    except Exception as e:
        log.debug("consent dialog failed (%s)", e)
        return None


def _ask_consent_macos():
    from .platform.macos import _on_main
    done = threading.Event()
    res = {"choice": None}

    def go():
        try:
            from AppKit import NSAlert, NSAlertFirstButtonReturn, NSApp
            a = NSAlert.alloc().init()
            a.setMessageText_(_CONSENT_TITLE)
            a.setInformativeText_(_CONSENT_BODY)
            a.addButtonWithTitle_("Enable")       # first added = default (Return)
            a.addButtonWithTitle_("No thanks")
            NSApp.activateIgnoringOtherApps_(True)
            res["choice"] = a.runModal() == NSAlertFirstButtonReturn
        except Exception as e:
            log.debug("consent dialog failed (%s)", e)
        finally:
            done.set()

    _on_main(go)
    # If no AppKit run loop is pumping (headless), the block never runs — bail
    # after a bound so the daemon thread never wedges; telemetry just stays off.
    return res["choice"] if done.wait(timeout=180) else None


def _ask_consent_linux():
    import shutil
    import subprocess
    if shutil.which("zenity"):
        cmd = ["zenity", "--question", "--title", _CONSENT_TITLE,
               "--text", _CONSENT_BODY, "--ok-label", "Enable",
               "--cancel-label", "No thanks"]
    elif shutil.which("kdialog"):
        cmd = ["kdialog", "--yesno", _CONSENT_BODY, "--title", _CONSENT_TITLE,
               "--yes-label", "Enable", "--no-label", "No thanks"]
    else:
        return None  # no dialog tool — ask again next launch
    try:
        rc = subprocess.run(cmd, timeout=300).returncode
    except Exception as e:
        log.debug("consent dialog failed (%s)", e)
        return None
    if rc == 0:
        return True   # Enable
    if rc == 1:
        return False  # No thanks (or the window was closed → declined)
    return None       # other exit (e.g. no display) → undecided, retry later


def ensure_consent(cfg) -> None:
    """One-time: ask the user (Enable is the default) unless already decided or
    overridden in config, and remember the answer so it's never asked twice."""
    if _opted_out() or not endpoint():
        return
    if getattr(cfg, "telemetry", None) is not None:
        return  # explicit config override — respect it, never prompt
    if consent_recorded():
        return
    try:
        choice = _ask_consent()
    except Exception as e:
        # The prompt must never take down the telemetry thread (or anything):
        # a raised dialog leaves the decision unmade, to retry next launch.
        log.debug("consent prompt failed (%s)", e)
        return
    if choice is None:
        return  # couldn't show a dialog this run — leave undecided, retry later
    record_consent(choice)


def _loop(cfg):
    time.sleep(CONSENT_DELAY_S)
    ensure_consent(cfg)     # one-time; no-op once answered/overridden
    while True:
        maybe_send(cfg)     # re-checks enabled() each tick
        time.sleep(POLL_S)


def start(cfg):
    """Launch the background thread: the one-time consent prompt (if still
    unanswered) then the daily heartbeat. No-op only when telemetry is hard-off
    (opted out or no endpoint) — otherwise it must run so the prompt can appear
    even though enabled() is still False before the user answers."""
    if _opted_out() or not endpoint():
        return
    threading.Thread(target=_loop, args=(cfg,), daemon=True, name="telemetry").start()
