# Created by Pratik Sancheti / https://github.com/psancheti6666
"""Linux backends for the updater (docs/linux-app.md, L8).

update.py owns the flow (scheduled loop, single-flight lock, offer/consent);
this module supplies the platform pieces: bundle detection, zenity dialogs
and progress (child processes, same reasoning as platform.alert() — no
main-thread choreography, works with the indicator off), and the install
step.

SECURITY: the install hands the downloaded .deb + detached .sig to
/usr/libexec/sotto/sotto-install-update via pkexec. That helper — root-side,
behind its own polkit action — re-copies both files to a root-owned workdir,
verifies the signature against the pinned key in /usr/share/sotto, and
refuses non-sotto packages and downgrades BEFORE apt-get sees anything.
Nothing on this (user-side) path is trusted by the privileged side; this
module's only security job is honest UX. (See the L5/L8 notes in
docs/linux-app.md for why the helper is separate from sotto-perms.)
"""

import logging
import os
import shlex
import shutil
import signal
import subprocess
import tempfile
import threading

log = logging.getLogger("sotto")

HELPER = "/usr/libexec/sotto/sotto-install-update"

_progress = None          # {"proc": Popen} — zenity --progress child
_progress_lock = threading.Lock()


def bundle_type():
    """"deb" | "appimage" | None — delegates to firstrun_linux (one source
    of truth for bundle detection; None keeps the updater silent on
    checkouts, where run.sh already git-pulls)."""
    from . import firstrun_linux
    return firstrun_linux.bundle_type()


# ------------------------------------------------- pure argv builders (unit-tested)

def _ask_argv(title: str, text: str):
    return ["zenity", "--question", f"--title={title}", f"--text={text}",
            "--ok-label=Update Now", "--cancel-label=Later",
            "--width=360", "--no-markup"]


def _ask_argv_kdialog(title: str, text: str):
    return ["kdialog", f"--title={title}", "--yesno", text,
            "--yes-label", "Update Now", "--no-label", "Later"]


def _progress_argv(title: str):
    # --auto-close exits when 100 lands; percentage/text stream in on stdin
    return ["zenity", "--progress", f"--title={title}", "--text=Starting…",
            "--width=360", "--auto-close", "--no-cancel"]


def _install_argv(deb_path: str, sig_path: str):
    return ["pkexec", HELPER, deb_path, sig_path]


def _relaunch_argv(pid: int):
    # detached shell: wait for THIS pid to actually exit (a fixed sleep
    # would race a slow tk/ollama teardown — the new instance would adopt
    # the old one's ollama just before atexit kills it), then start the
    # just-replaced installed launcher
    return ["/bin/sh", "-c",
            f"while kill -0 {int(pid)} 2>/dev/null; do sleep 0.5; done; "
            "exec /usr/bin/sotto"]


# ----------------------------------------------------------------- dialogs

def ask(title: str, text: str) -> bool:
    """True = Update Now. zenity → kdialog → give up quietly (the scheduled
    check will re-offer; a lost dialog must never block the app)."""
    for argv in (_ask_argv(title, text), _ask_argv_kdialog(title, text)):
        if shutil.which(argv[0]):
            try:
                return subprocess.run(
                    argv, capture_output=True, timeout=600).returncode == 0
            except (OSError, subprocess.TimeoutExpired) as e:
                log.warning("update dialog failed (%s)", e)
                return False
    log.info("no dialog tool for the update offer — skipping")
    return False


def progress_show(text: str):
    global _progress
    with _progress_lock:
        _progress_hide_locked()
        if not shutil.which("zenity"):
            log.info("update progress: %s (no zenity)", text)
            return
        try:
            proc = subprocess.Popen(
                _progress_argv("Updating Sotto"), stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                text=True)
            _progress = {"proc": proc}
        except OSError as e:
            log.warning("update progress window failed (%s)", e)
            _progress = None
    progress_set(text, None)


def progress_set(text: str, fraction=None):
    with _progress_lock:
        if _progress is None:
            return
        proc = _progress["proc"]
        try:
            # zenity --progress protocol: "N\n" sets the bar, "# text\n" the
            # label. No fraction → pulse by leaving the bar where it is.
            if fraction is not None:
                proc.stdin.write(f"{min(99, int(fraction * 100))}\n")
            proc.stdin.write(f"# {text}\n")
            proc.stdin.flush()
        except (OSError, ValueError):
            pass  # window closed — progress is best-effort


def progress_hide():
    with _progress_lock:
        _progress_hide_locked()


def _progress_hide_locked():
    # caller must hold _progress_lock
    global _progress
    if _progress is None:
        return
    proc = _progress["proc"]
    try:
        proc.stdin.close()
    except (OSError, ValueError):
        pass
    proc.terminate()
    try:
        proc.wait(timeout=2)  # reap — no zombie until interpreter exit
    except subprocess.TimeoutExpired:
        pass
    _progress = None


# ----------------------------------------------------------------- install

def _appimage_pubkey() -> str:
    """The release pubkey embedded in the RUNNING AppImage — self-replace
    verifies the download against it before touching $APPIMAGE."""
    appdir = os.environ.get("APPDIR", "")
    return os.path.join(appdir, "setup", "sotto-release.pub")


def _verify_argv(pubkey: str, sig: str, path: str):
    return ["openssl", "dgst", "-sha256", "-verify", pubkey,
            "-signature", sig, path]


def download_and_install(info, progress_set_cb, runner=subprocess.run,
                         popen=subprocess.Popen):
    """Route per bundle. Runs on update.py's worker thread under its
    single-flight lock. runner/popen are injectable for the unit tier."""
    bundle = bundle_type()
    if bundle == "appimage":
        return _self_replace(info, progress_set_cb, runner, popen)
    if bundle != "deb":
        raise RuntimeError("not running from an installed bundle")
    if not os.path.exists(HELPER):
        raise RuntimeError(f"{HELPER} is missing — reinstall Sotto")

    workdir = tempfile.mkdtemp(prefix="sotto-update-")
    try:
        # basename: the asset name comes from the release JSON — never let
        # it path-traverse out of the workdir
        deb = os.path.join(workdir, os.path.basename(info["name"]))
        sig = deb + ".sig"
        _download(info["sig_url"], sig, progress_set_cb, info["version"], False)
        _download(info["url"], deb, progress_set_cb, info["version"], True)

        progress_set_cb("Waiting for authorization… (Sotto verifies the "
                        "download's signature before installing)")
        try:
            r = runner(_install_argv(deb, sig), capture_output=True,
                       text=True, timeout=600)
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                "the authorization prompt or install timed out — if the "
                "install did finish, the next launch already runs the new "
                "version") from None
        if r.returncode == 126:      # user dismissed the polkit prompt
            log.info("update cancelled at the authorization prompt")
            progress_hide()          # zenity has --no-cancel; without this
            return                   # the window would outlive the flow
        if r.returncode != 0:
            detail = (r.stderr or r.stdout or "").strip()
            raise RuntimeError(
                f"install helper failed (exit {r.returncode}): {detail[-500:]}")

        log.info("update installed — relaunching as %s", info["version"])
        # cleanup + window teardown BEFORE the self-SIGINT: interpreter
        # shutdown would otherwise race this worker thread mid-rmtree
        shutil.rmtree(workdir, ignore_errors=True)
        progress_hide()
        popen(_relaunch_argv(os.getpid()), start_new_session=True)
        # same designed-shutdown path as the tray's Quit: SIGINT → tk root
        # destroyed (or KeyboardInterrupt headless) → atexit stops ollama;
        # the relaunch shell waits for this pid to vanish before starting
        os.kill(os.getpid(), signal.SIGINT)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _download(url, path, progress_set_cb, version, show_progress):
    import requests
    log.info("downloading %s", url)
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        got = 0
        with open(path, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
                got += len(chunk)
                if total and show_progress:
                    progress_set_cb(
                        f"Downloading Sotto {version}… "
                        f"{got >> 20} of {total >> 20} MB", got / total)


def _self_replace(info, progress_set_cb, runner, popen):
    """AppImage update: no root, no prompt (docs/linux-app.md decision
    table). Download the new AppImage + .sig, verify against the pubkey
    EMBEDDED in the running AppImage (same signature bar as the deb path —
    a tampered download must never replace $APPIMAGE), atomic-rename over
    $APPIMAGE, relaunch. The temp file lives NEXT TO the target: os.replace
    must stay on one filesystem, and ~/Downloads vs /tmp usually isn't."""
    target = os.environ.get("APPIMAGE")
    if not target or not os.path.exists(target):
        raise RuntimeError("$APPIMAGE not set — not running from an AppImage")
    pubkey = _appimage_pubkey()
    if not os.path.exists(pubkey):
        raise RuntimeError("embedded release key missing from this AppImage")
    if not shutil.which("openssl"):
        raise RuntimeError("openssl is required to verify the update — "
                           "install it and try again")

    new = target + ".sotto-new"
    sig = new + ".sig"
    try:
        _download(info["sig_url"], sig, progress_set_cb, info["version"], False)
        _download(info["url"], new, progress_set_cb, info["version"], True)
        progress_set_cb("Verifying signature…")
        r = runner(_verify_argv(pubkey, sig, new), capture_output=True,
                   text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError("signature verification FAILED — the download "
                               "was not accepted (it may be corrupt or not a "
                               "Sotto release)")
        os.chmod(new, 0o755)
        os.replace(new, target)  # atomic: old file vanishes, new one is live
        log.info("AppImage replaced — relaunching as %s", info["version"])
        progress_hide()
        popen(["/bin/sh", "-c",
               f"while kill -0 {os.getpid()} 2>/dev/null; do sleep 0.5; done; "
               f"exec {shlex.quote(target)}"], start_new_session=True)
        os.kill(os.getpid(), signal.SIGINT)
    finally:
        for p in (new, sig):
            try:
                os.unlink(p)
            except OSError:
                pass
