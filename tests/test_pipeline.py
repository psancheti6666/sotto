# Created by Pratik Sancheti / https://github.com/psancheti6666
"""Pipeline verification, runnable headless (no mic/hotkey/permissions needed).

Usage:
  .venv/bin/python tests/test_pipeline.py            # regex + dictionary units (no models)
  .venv/bin/python tests/test_pipeline.py --llm      # + Ollama cleaning cases
  .venv/bin/python tests/test_pipeline.py --asr      # + ASR on `say`-synthesized speech
  .venv/bin/python tests/test_pipeline.py --asr-onnx # + the ONNX backend (Intel/Linux path;
                                                     #   needs: pip install 'onnx-asr[cpu,hub]')
  .venv/bin/python tests/test_pipeline.py --all
"""

import subprocess
import sys
import tempfile
import time
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sotto.clean import Cleaner, regex_clean
from sotto.config import load_config
from sotto.dictionary import Dictionary

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
failures = 0


def check(name: str, ok: bool, detail: str = ""):
    global failures
    if not ok:
        failures += 1
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"  {detail}" if detail and not ok else ""))


def test_regex():
    print("regex pre-pass:")
    check("fillers stripped", regex_clean("um so uh let's go") == "So let's go",
          repr(regex_clean("um so uh let's go")))
    check("repeats collapsed", regex_clean("the the plan is is ready") == "The plan is ready",
          repr(regex_clean("the the plan is is ready")))
    check("capitalizes", regex_clean("hello there")[0] == "H")
    check("empty in, empty out", regex_clean("um uh") == "")


def test_dictionary():
    print("dictionary fuzzy-fix:")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("Anthropic\nKubernetes\nSaanvi Reddy\n")
        path = f.name
    d = Dictionary(path)
    check("misheard proper noun", d.apply("I work at and thropic now") == "I work at Anthropic now",
          repr(d.apply("I work at and thropic now")))
    check("jargon fixed", "Kubernetes" in d.apply("deploy it on cuber netes today"),
          repr(d.apply("deploy it on cuber netes today")))
    check("two-word name", "Saanvi Reddy" in d.apply("ask sanvi ready about it"),
          repr(d.apply("ask sanvi ready about it")))
    check("exact term untouched", d.apply("Kubernetes is fine") == "Kubernetes is fine")
    check("punctuation preserved", d.apply("we use cooper netties.").endswith("."),
          repr(d.apply("we use cooper netties.")))
    os.unlink(path)

    # phonetic path: mishearings of short names score below the ratio
    # threshold ("pratique"→"Pratik" = 71) but share the Soundex skeleton
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("Pratik\nsotto\n")
        path = f.name
    d = Dictionary(path)
    for wrong, sent in [("pratique", "my name is pratique"),
                        ("pratiqe", "pratiqe made this"),
                        ("prateek", "I am prateek"),
                        ("soto", "the app is called soto"),
                        ("sotta", "sotta is a dictation app")]:
        got = d.apply(sent)
        check(f"phonetic mishearing fixed: {wrong}", wrong not in got
              and ("Pratik" in got or "sotto" in got), repr(got))
    for sent in ["so to speak that is fine", "this is very practical",
                 "stow the luggage", "the product is ready",
                 "in particular this part"]:
        check(f"no false positive: {sent[:24]!r}", d.apply(sent) == sent,
              repr(d.apply(sent)))
    os.unlink(path)


# (input, tone, required substrings, forbidden substrings)
LLM_CASES = [
    ("um so let's meet on tuesday uh wait no friday at like 2 pm actually make it 3",
     "casual chat message", ["Friday", "3"], ["Tuesday", "2 pm", "um", "uh"]),
    ("okay three things first update the readme second uh bump the version and third push the release",
     "plain text for a code editor (no smart quotes)", ["-"], ["um", "uh"]),
    ("hey um just checking are we still on for lunch question mark",
     "casual chat message", ["?"], ["question mark", "um"]),
    ("i think we should uh you know refactor the login module because it's like really messy",
     "professional email/document", ["refactor", "login"], ["uh", "you know", "like really"]),
    ("send the report to john comma then cc the team new line thanks",
     "professional email/document", [","], ["comma", "new line"]),
    ("what time is the standup tomorrow",
     "casual chat message", ["standup"], []),  # must NOT be answered, only cleaned
    ("the deadline is monday no wait i mean wednesday the twelfth",
     "neutral written text", ["Wednesday"], ["Monday"]),
    # Fidelity: rambling speech must keep the speaker's wording and hedges —
    # not be condensed or rewritten.
    ("so basically i was thinking that we could maybe move the standup to nine thirty "
     "because the current time clashes with the daily sync and um i also wanted to "
     "mention that the deployment pipeline is still broken and someone needs to look "
     "at it before friday",
     "casual chat message",
     ["i was thinking", "maybe", "standup", "clashes", "daily sync",
      "deployment pipeline", "broken", "friday"],
     ["um"]),
]


def test_llm():
    print("LLM cleaning (Ollama):")
    cfg = load_config()
    cleaner = Cleaner(cfg.ollama_url, cfg.ollama_model, timeout_s=30)
    cleaner.warm()
    total = 0.0
    for text, tone, required, forbidden in LLM_CASES:
        t0 = time.perf_counter()
        out = cleaner.clean(text, tone, ["Anthropic"])
        dt = time.perf_counter() - t0
        total += dt
        import re as _re
        has_word = lambda w, s: bool(_re.search(r"(?<!\w)" + _re.escape(w.lower()) + r"(?!\w)", s.lower()))
        ok = (all((r.lower() in out.lower()) if not r.isalpha() else has_word(r, out) for r in required)
              and not any(has_word(f, out) if f.isalpha() else (f.lower() in out.lower()) for f in forbidden))
        check(f"({dt:.2f}s) {text[:48]}…", ok, f"got: {out!r}")
    print(f"  mean latency: {total/len(LLM_CASES):.2f}s (timeout budget {cfg.llm_timeout_s}s)")


def test_llm_fallback():
    print("LLM guardrail fallback (Ollama unreachable → regex-cleaned, never raw/blocked):")
    cleaner = Cleaner("http://localhost:1", "nope", timeout_s=0.5)
    out = cleaner.clean("um hello there uh friend", "neutral", [])
    check("falls back to regex-cleaned", out == "Hello there friend", repr(out))


def test_llm_server():
    print("bundled-ollama manager (no server, no bundle → fast no-op):")
    from sotto import llm_server, ollama_runtime
    from sotto.config import Config

    had = os.environ.pop("RESOURCEPATH", None)
    # on a Linux box WITH ollama installed, bundled_binary() would resolve the
    # system binary and ensure() would spawn a real `ollama serve` — stub the
    # resolver so this test stays a no-op everywhere
    orig_resolve = ollama_runtime.resolve
    ollama_runtime.resolve = lambda: None
    try:
        check("no RESOURCEPATH → no bundled binary",
              llm_server.bundled_binary() is None)

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["RESOURCEPATH"] = tmp
            check("empty bundle → no binary", llm_server.bundled_binary() is None)
            os.makedirs(os.path.join(tmp, "ollama"))
            fake = os.path.join(tmp, "ollama", "ollama")
            with open(fake, "w") as f:
                f.write("#!/bin/sh\n")
            if sys.platform != "win32":
                # os.access(X_OK) is true for ANY existing file on Windows —
                # the exec-bit concept doesn't exist there (docs/windows-app.md W1)
                check("non-executable file ignored",
                      llm_server.bundled_binary() is None)
            os.chmod(fake, 0o755)
            check("executable found", llm_server.bundled_binary() == fake)
        del os.environ["RESOURCEPATH"]

        check("host:port parsed", llm_server._host_port(
            "http://localhost:11434") == "localhost:11434")
        check("defaults filled", llm_server._host_port(
            "http://127.0.0.1") == "127.0.0.1:11434")

        cfg = Config()
        cfg.ollama_url = "http://127.0.0.1:9"  # closed port → instant refusal
        t0 = time.time()
        llm_server.ensure(cfg)  # unreachable + no bundle: must no-op, not raise
        check(f"ensure() no-ops in {time.time()-t0:.2f}s without a bundle",
              time.time() - t0 < 5.0)
        check("no child spawned", llm_server._child is None)
    finally:
        ollama_runtime.resolve = orig_resolve
        if had is not None:
            os.environ["RESOURCEPATH"] = had


def test_ollama_runtime():
    print("Linux ollama runtime resolution + download logic:")
    import hashlib
    from sotto import ollama_runtime as orr

    orig_which, orig_dir = orr.shutil.which, orr.RUNTIME_DIR
    orig_asset_mod = orr._ASSET
    try:
        # the resolution/download blocks below exercise the LINUX layout by
        # name (bin/ollama) — pin the asset so they hold on windows-latest,
        # where the import-time _ASSET is the ollama.exe layout
        orr._ASSET = orr._ASSETS["linux"]
        with tempfile.TemporaryDirectory() as td:
            orr.RUNTIME_DIR = td

            orr.shutil.which = lambda cmd: "/usr/bin/ollama" if cmd == "ollama" else None
            check("system ollama wins", orr.resolve() == "/usr/bin/ollama")

            orr.shutil.which = lambda cmd: None
            check("nothing installed → None", orr.resolve() is None)

            binpath = os.path.join(td, "bin", "ollama")
            os.makedirs(os.path.dirname(binpath))
            with open(binpath, "w") as f:
                f.write("#!/bin/sh\n")
            if sys.platform != "win32":
                # no exec bit on Windows — os.access(X_OK) is always true
                check("non-executable download ignored",
                      orr.installed() is None)
            os.chmod(binpath, 0o755)
            check("downloaded runtime found", orr.resolve() == binpath)

        # --- Windows asset table + layout (W4): same pinned version, zip
        # kind, ollama.exe at the archive root
        win, lin = orr._ASSETS["win32"], orr._ASSETS["linux"]
        check("both platforms pin the same ollama version",
              orr.OLLAMA_VERSION in win["url"]
              and orr.OLLAMA_VERSION in lin["url"])
        check("windows asset is the plain amd64 zip (NOT the desktop "
              "installer)", win["url"].endswith("ollama-windows-amd64.zip")
              and win["kind"] == "zip")
        check("windows digest pinned",
              len(win["sha256"]) == 64 and win["sha256"] != lin["sha256"])
        check("windows binary is ollama.exe at the root",
              win["bin"] == ("ollama.exe",))

        orig_asset = orr._ASSET
        try:
            with tempfile.TemporaryDirectory() as td:
                orr.RUNTIME_DIR, orr._ASSET = td, win
                open(os.path.join(td, "ollama.exe"), "w").write("x")
                os.chmod(os.path.join(td, "ollama.exe"), 0o755)
                check("windows layout: installed() finds root ollama.exe",
                      orr.installed() == os.path.join(td, "ollama.exe"))
        finally:
            orr._ASSET = orig_asset

        # --- zip extraction with the zip-slip guard
        import zipfile
        with tempfile.TemporaryDirectory() as td:
            archive = os.path.join(td, "a.zip")
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("ollama.exe", "binary")
                z.writestr("lib/ollama/x.dll", "lib")
            dest = os.path.join(td, "out")
            os.makedirs(dest)
            orr._extract_zip(archive, dest)
            check("zip extracts the expected layout",
                  os.path.isfile(os.path.join(dest, "ollama.exe"))
                  and os.path.isfile(os.path.join(dest, "lib", "ollama",
                                                  "x.dll")))
            evil = os.path.join(td, "evil.zip")
            with zipfile.ZipFile(evil, "w") as z:
                z.writestr("../escape.txt", "nope")
            try:
                orr._extract_zip(evil, dest)
                check("zip-slip entry refused", False)
            except RuntimeError:
                check("zip-slip entry refused",
                      not os.path.exists(os.path.join(td, "escape.txt")))
    finally:
        orr.shutil.which, orr.RUNTIME_DIR = orig_which, orig_dir
        orr._ASSET = orig_asset_mod

    # _fetch: streaming hash + progress + checksum abort, no real network
    payload = b"x" * (2 * 1024 * 1024) + b"tail"
    good_sha = hashlib.sha256(payload).hexdigest()

    class FakeResponse:
        headers = {"content-length": str(len(payload))}
        def raise_for_status(self): pass
        def iter_content(self, chunk_size):
            for i in range(0, len(payload), chunk_size):
                yield payload[i:i + chunk_size]
        def __enter__(self): return self
        def __exit__(self, *a): return False

    orig_get = orr.requests.get
    orr.requests.get = lambda url, stream, timeout: FakeResponse()
    try:
        with tempfile.TemporaryDirectory() as td:
            dest = os.path.join(td, "archive")
            fractions = []
            orr._fetch("http://x", dest, good_sha, fractions.append)
            check("fetch writes the full payload",
                  open(dest, "rb").read() == payload)
            check("progress reaches 1.0 monotonically",
                  fractions and abs(fractions[-1] - 1.0) < 1e-9
                  and fractions == sorted(fractions), str(fractions[-3:]))
            try:
                orr._fetch("http://x", dest, "0" * 64, None)
                check("checksum mismatch raises", False, "no raise")
            except RuntimeError as e:
                check("checksum mismatch raises", "checksum" in str(e), str(e))
            check("mismatched file removed", not os.path.exists(dest))
    finally:
        orr.requests.get = orig_get

    # download(): orchestration with fetch/extract stubbed (Linux layout —
    # same pin as above)
    orig_fetch, orig_extract = orr._fetch, orr._extract
    try:
        orr._ASSET = orr._ASSETS["linux"]
        with tempfile.TemporaryDirectory() as td:
            orr.RUNTIME_DIR = td

            def fake_fetch(url, dest, sha, cb):
                open(dest, "wb").write(b"z")
            orr._fetch = fake_fetch
            orr._extract = lambda a, d: None  # extracts nothing → no binary
            try:
                orr.download()
                check("missing bin/ollama after extract raises", False, "no raise")
            except RuntimeError as e:
                check("missing bin/ollama after extract raises",
                      "bin/ollama" in str(e), str(e))
            check("partial archive cleaned up",
                  not os.path.exists(os.path.join(td, ".ollama-download.partial")))

            def good_extract(a, d):
                os.makedirs(os.path.join(d, "bin"), exist_ok=True)
                p = os.path.join(d, "bin", "ollama")
                open(p, "w").write("#!/bin/sh\n")
                os.chmod(p, 0o755)
            orr._extract = good_extract
            got = orr.download()
            check("download returns the binary path",
                  got == os.path.join(td, "bin", "ollama"), str(got))
    finally:
        orr._fetch, orr._extract, orr.RUNTIME_DIR = orig_fetch, orig_extract, orig_dir
        orr._ASSET = orig_asset_mod

    # real .tar.zst extraction — runs where zstandard is installed (Linux CI;
    # skipped on macOS, where the requirement doesn't apply)
    try:
        import zstandard
    except ImportError:
        print("  (zstandard not installed here — extract test runs on Linux CI)")
        return
    import io
    import tarfile
    with tempfile.TemporaryDirectory() as td:
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w") as tar:
            data = b"#!/bin/sh\necho ollama\n"
            info = tarfile.TarInfo("bin/ollama")
            info.size = len(data)
            info.mode = 0o755
            tar.addfile(info, io.BytesIO(data))
        archive = os.path.join(td, "a.tar.zst")
        with open(archive, "wb") as f:
            f.write(zstandard.ZstdCompressor().compress(raw.getvalue()))
        dest = os.path.join(td, "rt")
        os.makedirs(dest)
        orr._extract(archive, dest)
        out = os.path.join(dest, "bin", "ollama")
        check("tar.zst extracts bin/ollama", os.path.exists(out))
        check("payload intact", open(out, "rb").read().endswith(b"ollama\n"))
        check("exec bit survives the data filter", os.access(out, os.X_OK))


def test_firstrun():
    print("first-run checks (offline model detection, store consolidation):")
    from sotto import firstrun
    from sotto.config import Config

    cfg = Config()
    saved = {k: os.environ.get(k) for k in ("HF_HOME", "SOTTO_FIRSTRUN")}
    old_hf_default, old_sotto_hf = firstrun.HF_DEFAULT_CACHE, firstrun.SOTTO_HF_HOME
    old_stores = firstrun.OLLAMA_DEFAULT_STORE, firstrun.SOTTO_OLLAMA_STORE
    try:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ.pop("HF_HOME", None)
            firstrun.HF_DEFAULT_CACHE = os.path.join(tmp, "hf-default")
            firstrun.SOTTO_HF_HOME = os.path.join(tmp, "sotto-hf")
            firstrun.OLLAMA_DEFAULT_STORE = os.path.join(tmp, "ollama")
            firstrun.SOTTO_OLLAMA_STORE = os.path.join(tmp, "sotto-ollama")

            check("ASR model absent detected",
                  not firstrun.asr_model_ok(cfg.asr_model))
            check("LLM model absent detected",
                  not firstrun.llm_model_ok(cfg.ollama_model))

            snap = os.path.join(firstrun._hf_model_dir(
                cfg.asr_model, firstrun.HF_DEFAULT_CACHE), "snapshots", "abc")
            os.makedirs(snap)
            open(os.path.join(snap, "config.json"), "w").close()
            check("ASR model found in default cache",
                  firstrun.asr_model_ok(cfg.asr_model))

            manifest = firstrun._manifest_path(
                firstrun.SOTTO_OLLAMA_STORE, cfg.ollama_model)
            os.makedirs(os.path.dirname(manifest))
            open(manifest, "w").close()
            check("LLM model found in sotto store",
                  firstrun.llm_model_ok(cfg.ollama_model))
            check("manifest path uses name/tag split",
                  manifest.replace(os.sep, "/")
                  .endswith("library/qwen3/4b-instruct"), manifest)

            firstrun.consolidate_model_stores(cfg)
            check("HF_HOME untouched when model already cached",
                  "HF_HOME" not in os.environ)

            import shutil
            shutil.rmtree(os.path.join(firstrun.HF_DEFAULT_CACHE))
            firstrun.consolidate_model_stores(cfg)
            check("HF_HOME pointed at ~/.sotto when model missing",
                  os.environ.get("HF_HOME") == firstrun.SOTTO_HF_HOME)

            os.environ["SOTTO_FIRSTRUN"] = "0"
            check("SOTTO_FIRSTRUN=0 skips the window", not firstrun.needed(cfg))
            os.environ["SOTTO_FIRSTRUN"] = "1"
            check("SOTTO_FIRSTRUN=1 forces the window", firstrun.needed(cfg))
    finally:
        firstrun.HF_DEFAULT_CACHE, firstrun.SOTTO_HF_HOME = old_hf_default, old_sotto_hf
        firstrun.OLLAMA_DEFAULT_STORE, firstrun.SOTTO_OLLAMA_STORE = old_stores
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


def test_insights_config():
    print("insights window config (pure logic, no UI):")
    from sotto import insights
    old = insights._port
    try:
        insights._port = None
        check("not available before configure", not insights.available())
        insights.show()  # unconfigured → returns before any AppKit import
        check("show() is a safe no-op when unconfigured", True)
        insights.configure(8377)
        check("available after configure", insights.available())
    finally:
        insights._port = old


def test_win32_filter():
    print("Windows hotkey filter (W2 — fake hook events, no pynput hook):")
    if sys.platform not in ("darwin", "win32"):
        # constructing HotkeyListener imports pynput, which Linux
        # deliberately does not install (evdev is its backend)
        print("  (skipped — needs pynput, darwin/win32 only)")
        return
    import types

    from sotto import hotkey as hk

    class Suppressed(Exception):
        pass

    class FakeListener:
        def suppress_event(self):
            raise Suppressed()

    events = []
    lst = hk.HotkeyListener(
        "ctrl_r", lambda: events.append("start"),
        lambda discard=False: events.append("discard" if discard else "stop"),
        on_handsfree=lambda: events.append("handsfree"),
        on_cancel=lambda: events.append("cancel"))
    lst._listener = FakeListener()

    def feed(msg, vk, flags=0):
        """Returns True when the filter swallowed the event."""
        try:
            lst._win32_filter(msg, types.SimpleNamespace(vkCode=vk,
                                                         flags=flags))
            return False
        except Suppressed:
            return True

    D, U = hk._WM_KEYDOWN, hk._WM_KEYUP

    # idle: nothing swallowed, nothing fired
    check("space passes through when idle",
          not feed(D, hk._VK_SPACE) and not feed(U, hk._VK_SPACE)
          and events == [], str(events))
    check("escape passes through when idle",
          not feed(D, hk._VK_ESCAPE) and events == [])

    # hold → space engages hands-free, down AND up swallowed, later space free
    lst._down = True
    lst._hotkey_press()
    check("hold starts dictation", events == ["start"], str(events))
    check("space down while holding is swallowed", feed(D, hk._VK_SPACE))
    check("hands-free engaged", events == ["start", "handsfree"], str(events))
    check("the matching space up is swallowed too", feed(U, hk._VK_SPACE))
    check("space in hands-free passes through (typed normally)",
          not feed(D, hk._VK_SPACE) and not feed(U, hk._VK_SPACE))

    # escape cancels hands-free, both edges swallowed
    check("escape down cancels and is swallowed", feed(D, hk._VK_ESCAPE))
    check("cancel fired", events[-1] == "cancel", str(events))
    check("the matching escape up is swallowed", feed(U, hk._VK_ESCAPE))
    lst._down = False

    # combo: another key while holding → discard, key passes through
    events.clear()
    lst._down = True
    lst._hotkey_press()
    check("combo key is NOT swallowed (shortcut must reach the app)",
          not feed(D, 0x2E))  # VK_DELETE
    check("combo discards the dictation",
          events == ["start", "discard"], str(events))
    lst._down = False

    # our own SendInput typing must never re-enter the gesture machine
    events.clear()
    lst._down = True
    lst._hotkey_press()
    check("injected events are ignored entirely",
          not feed(D, hk._VK_SPACE, flags=hk._LLKHF_INJECTED)
          and events == ["start"], str(events))
    # non-key hook messages fall straight through
    check("non-key messages pass through", not feed(0x0200, hk._VK_SPACE))
    lst._down = False
    lst.force_stop()


def test_windows_platform():
    print("Windows platform leaf functions (W3 — fake windll/winsound):")
    import ctypes
    import types

    from sotto.platform import windows as pw

    # --- active_app_id: foreground hwnd → pid → exe basename, lowercased
    class FakeUser32:
        def __init__(self, hwnd=42, pid=1234):
            self._hwnd, self._pid = hwnd, pid

        def GetForegroundWindow(self):
            return self._hwnd

        def GetWindowThreadProcessId(self, hwnd, pid_ref):
            pid_ref._obj.value = self._pid
            return 1

    class FakeKernel32:
        def __init__(self, exe="C:\\Windows\\System32\\Notepad.EXE", ok=True):
            self._exe, self._ok = exe, ok
            self.closed = []

        def OpenProcess(self, access, inherit, pid):
            return 99 if self._ok else 0

        def QueryFullProcessImageNameW(self, handle, flags, buf, size_ref):
            buf.value = self._exe
            return 1

        def CloseHandle(self, handle):
            self.closed.append(handle)

    # ctypes.windll is REAL on the windows-latest runner — save and restore
    # the genuine object in every path, or the fake leaks into the rest of
    # the suite (pynput itself uses windll there)
    orig_windll = getattr(ctypes, "windll", None)
    fake = types.SimpleNamespace(user32=FakeUser32(),
                                 kernel32=FakeKernel32())
    ctypes.windll = fake
    try:
        app_id = pw.active_app_id()
        check("foreground exe name, basename, lowercased",
              app_id == "notepad.exe", app_id)
        check("process handle closed", fake.kernel32.closed == [99])
        ctypes.windll = types.SimpleNamespace(user32=FakeUser32(hwnd=0),
                                              kernel32=FakeKernel32())
        check("no foreground window → empty", pw.active_app_id() == "")
        ctypes.windll = types.SimpleNamespace(
            user32=FakeUser32(), kernel32=FakeKernel32(ok=False))
        check("OpenProcess failure → empty, no raise", pw.active_app_id() == "")
        if orig_windll is None:  # only meaningful where windll doesn't exist
            del ctypes.windll
            check("no windll at all (non-Windows) → empty, no raise",
                  pw.active_app_id() == "")
    finally:
        if orig_windll is not None:
            ctypes.windll = orig_windll
        elif hasattr(ctypes, "windll"):
            del ctypes.windll

    # --- play_sound: alias vs absolute path pick the right winsound flags
    calls = []
    fake_ws = types.SimpleNamespace(
        SND_ASYNC=1, SND_NODEFAULT=2, SND_ALIAS=4, SND_FILENAME=8,
        PlaySound=lambda name, flags: calls.append((name, flags)))
    orig_ws = sys.modules.get("winsound")
    sys.modules["winsound"] = fake_ws
    try:
        pw.play_sound("SystemAsterisk")
        pw.play_sound("C:\\Windows\\Media\\chord.wav" if os.name == "nt"
                      else "/Windows/Media/chord.wav")
        pw.play_sound("")
        check("alias uses SND_ALIAS, async, no default fallback",
              calls[0] == ("SystemAsterisk", 4 | 1 | 2), str(calls))
        check("absolute path uses SND_FILENAME",
              calls[1][1] == 8 | 1 | 2, str(calls))
        check("empty name is a no-op", len(calls) == 2)
    finally:
        if orig_ws is None:
            sys.modules.pop("winsound", None)
        else:
            sys.modules["winsound"] = orig_ws

    # --- alert: MessageBox on a daemon thread, never raises
    shown = []
    ctypes.windll = types.SimpleNamespace(user32=types.SimpleNamespace(
        MessageBoxW=lambda h, text, title, flags: shown.append(
            (title, text, flags))))
    try:
        pw.alert("Title", "Body")
        deadline = time.time() + 5
        while not shown and time.time() < deadline:
            time.sleep(0.01)
        check("alert shows a topmost warning MessageBox",
              shown and shown[0][0] == "Title"
              and shown[0][2] == 0x30 | 0x10000 | 0x40000, str(shown))
    finally:
        if orig_windll is not None:
            ctypes.windll = orig_windll
        elif hasattr(ctypes, "windll"):
            del ctypes.windll


def test_win_injector():
    print("Windows injector (W3 — fake keyboard, no real events):")
    if sys.platform not in ("darwin", "win32"):
        print("  (skipped — needs pynput, darwin/win32 only)")
        return
    import contextlib

    import pyperclip

    from sotto import inject, inject_windows as iw

    class FakeKb:
        def __init__(self):
            self.events = []

        def type(self, ch):
            self.events.append(("type", ch))

        def press(self, key):
            self.events.append(("press", key))

        def release(self, key):
            self.events.append(("release", key))

        @contextlib.contextmanager
        def pressed(self, key):
            self.events.append(("hold", key))
            yield
            self.events.append(("unhold", key))

    inj = iw.WinInjector.__new__(iw.WinInjector)
    inj._kb = FakeKb()

    inj.type_text("hi", 0)
    check("type emits per-char events",
          inj._kb.events == [("type", "h"), ("type", "i")],
          str(inj._kb.events))

    from pynput.keyboard import Key
    board = {"content": "before"}
    orig_copy, orig_paste = pyperclip.copy, pyperclip.paste
    pyperclip.copy = lambda t: board.__setitem__("content", t)
    pyperclip.paste = lambda: board["content"]
    inj._kb.events.clear()
    try:
        inj.paste_text("hello", restore_delay_s=0)
        check("paste chord is Ctrl+V (not Cmd)",
              inj._kb.events == [("hold", Key.ctrl), ("press", "v"),
                                 ("release", "v"), ("unhold", Key.ctrl)],
              str(inj._kb.events))
        check("clipboard restored after paste", board["content"] == "before")
    finally:
        pyperclip.copy, pyperclip.paste = orig_copy, orig_paste

    # router: win32 branch returns the Windows injector
    orig_platform, orig_injector = inject.sys.platform, inject._injector
    inject.sys = types_module = __import__("types").SimpleNamespace(
        platform="win32")
    inject._injector = None
    try:
        check("inject router picks WinInjector on win32",
              isinstance(inject._get_injector(), iw.WinInjector))
    finally:
        inject.sys = __import__("sys")
        inject._injector = orig_injector


def test_firstrun_windows():
    print("Windows first-run backend (W5 — fakes, no winreg/ctypes):")
    from sotto import firstrun_windows as fw
    from sotto.config import Config

    cfg = Config()

    # --- backend surface matches what firstrun_tk consumes
    for name in ("ROWS", "GATING", "SUBTITLE", "statuses", "run_fix",
                 "engine_missing", "setup_missing", "relaunch", "needed",
                 "bundle_type"):
        check(f"backend exposes {name}", hasattr(fw, name))
    check("rows: mic / models / autostart",
          [r[0] for r in fw.ROWS] == ["mic", "models", "autostart"])
    check("only the mic gates Start (models = consent checkbox; "
          "autostart optional)", fw.GATING == ("mic",))

    # --- mic_ok: honest Deny detection, everything else fails OPEN
    check("Deny → mic not ok", not fw.mic_ok(reader=lambda sk: "Deny"))
    check("Allow → ok", fw.mic_ok(reader=lambda sk: "Allow"))
    check("missing key/any error → fails open (never false-blocks a "
          "Windows build we haven't met)",
          fw.mic_ok(reader=lambda sk: (_ for _ in ()).throw(OSError())))
    seen = []
    fw.mic_ok(reader=lambda sk: seen.append(sk) or "Allow")
    check("non-MSIX reads the NonPackaged consent subkey",
          seen == ["NonPackaged"], str(seen))

    # --- bundle_type: checkout → None (frozen paths need real Windows)
    check("source checkout → no bundle", fw.bundle_type() is None)

    # --- needed(): marker short-circuits; downloads or mic drive it
    orig_marker = fw.firstrun.PENDING_MARKER
    orig_setup, orig_mic = fw.setup_missing, fw.mic_ok
    saved_force = os.environ.pop("SOTTO_FIRSTRUN", None)
    try:
        with tempfile.TemporaryDirectory() as td:
            fw.firstrun.PENDING_MARKER = os.path.join(td, "pending")
            fw.setup_missing = lambda c: False
            fw.mic_ok = lambda reader=None: True
            check("all set → walkthrough not needed", not fw.needed(cfg))
            fw.setup_missing = lambda c: True
            check("downloads missing → needed", fw.needed(cfg))
            fw.setup_missing = lambda c: False
            fw.mic_ok = lambda reader=None: False
            check("mic toggle off → needed (silent-mic first dictation "
                  "would type nothing)", fw.needed(cfg))
            open(fw.firstrun.PENDING_MARKER, "w").close()
            check("pending marker short-circuits (consent given, "
                  "relaunch in flight)", not fw.needed(cfg))
            os.environ["SOTTO_FIRSTRUN"] = "1"
            check("SOTTO_FIRSTRUN=1 forces the window", fw.needed(cfg))
    finally:
        fw.firstrun.PENDING_MARKER = orig_marker
        fw.setup_missing, fw.mic_ok = orig_setup, orig_mic
        os.environ.pop("SOTTO_FIRSTRUN", None)
        if saved_force is not None:
            os.environ["SOTTO_FIRSTRUN"] = saved_force

    # --- relaunch argv: spawn-then-exit inputs (never execv on Windows)
    check("checkout relaunch argv is python -m sotto",
          fw.relaunch_argv() == [sys.executable, "-m", "sotto"])
    argv = fw.autostart_argv("C:\\Apps\\Sotto\\sotto.exe")
    check("autostart shortcut via powershell WScript.Shell",
          argv[0] == "powershell" and "-NonInteractive" in argv
          and "CreateShortcut" in argv[-1]
          and "C:\\Apps\\Sotto\\sotto.exe" in argv[-1], str(argv[-1]))
    # Windows allows apostrophes in usernames (O'Brien) — a raw ' would
    # break the powershell string open mid-statement (#78 review)
    argv = fw.autostart_argv("C:\\Users\\O'Brien\\sotto.exe")
    check("apostrophes in paths are powershell-escaped ('')",
          "O''Brien" in argv[-1] and "O'Brien\\sotto" not in argv[-1],
          str(argv[-1]))

    # --- firstrun_tk selects the backend by platform
    from sotto import firstrun_tk as ft
    be = ft._backend()
    check("firstrun_tk backend selection is platform-correct",
          be.__name__.endswith(
              "firstrun_windows" if sys.platform == "win32"
              else "firstrun_linux"), be.__name__)

    # --- Windows single-instance mutex (fake kernel32)
    from sotto import app as app_mod

    class FakeKernel32:
        def __init__(self, exists=False, fail=False):
            self._exists, self._fail = exists, fail
            self.names = []

        def CreateMutexW(self, attrs, initial, name):
            self.names.append(name)
            return 0 if self._fail else 7

        def GetLastError(self):
            return 183 if self._exists else 0

    k = FakeKernel32()
    check("first instance holds the mutex handle",
          app_mod._acquire_instance_lock(win_kernel32=k) == 7)
    check("mutex is per-session Local\\, fixed name",
          k.names == ["Local\\sotto-instance"], str(k.names))
    check("second live instance refused (the #63 lesson, Windows edition)",
          app_mod._acquire_instance_lock(
              win_kernel32=FakeKernel32(exists=True)) is None)
    check("mutex API failure fails open (never blocks startup)",
          app_mod._acquire_instance_lock(
              win_kernel32=FakeKernel32(fail=True)) is True)

    # --- load_config Windows defaults (branch forced; real platform's
    # flags restored after)
    from sotto import config as cfg_mod
    orig_flags = (cfg_mod.IS_LINUX, cfg_mod.IS_WINDOWS, cfg_mod.CONFIG_PATH)
    try:
        with tempfile.TemporaryDirectory() as td:
            cfg_mod.IS_LINUX, cfg_mod.IS_WINDOWS = False, True
            cfg_mod.CONFIG_PATH = os.path.join(td, "none.toml")
            wcfg = cfg_mod.load_config()
            check("Windows default hotkey is ctrl_r (fn is unmappable "
                  "there)", wcfg.hotkey == "ctrl_r")
            check("Windows: haptics off, winsound aliases, terminal exes "
                  "on the paste path",
                  not wcfg.haptics
                  and wcfg.start_sound == "SystemAsterisk"
                  and "windowsterminal.exe" in wcfg.keystroke_apps,
                  f"{wcfg.start_sound} {wcfg.keystroke_apps[:2]}")
    finally:
        cfg_mod.IS_LINUX, cfg_mod.IS_WINDOWS, cfg_mod.CONFIG_PATH = orig_flags


def test_insights_linux():
    print("Linux insights window (pure logic — fake gi, no GTK):")
    import logging

    from sotto import insights_linux as il

    saved = (il._port, il._failed, il._window, il._webview, il._sanitized)
    orig_gi, orig_browser, orig_loop = (il._gi_modules, il._open_browser,
                                        il._ensure_loop_thread)
    try:
        # --- gating mirrors insights.py: unconfigured = safe no-op
        il._port, il._failed = None, False
        check("not available before configure", not il.available())
        il._gi_modules = lambda: (_ for _ in ()).throw(
            AssertionError("gi must not load when unconfigured"))
        il.show_soon()
        check("show_soon() is a safe no-op when unconfigured", True)
        il.configure(8377)
        check("available after configure", il.available())

        # --- WebKit2 namespace preference: newest first, 4.0 fallback
        class FakeGi:
            def __init__(self, available):
                self.available, self.calls = available, []

            def require_version(self, ns, ver):
                self.calls.append(ver)
                if ver not in self.available:
                    raise ValueError(f"no {ns} {ver}")

        gi = FakeGi({"4.1", "4.0"})
        check("prefers WebKit2 4.1", il._require_webkit(gi) == "4.1")
        gi = FakeGi({"4.0"})
        check("falls back to WebKit2 4.0 (tried in order)",
              il._require_webkit(gi) == "4.0" and gi.calls == ["4.1", "4.0"],
              str(gi.calls))
        try:
            il._require_webkit(FakeGi(set()))
            check("raises when no WebKit2 introspection exists", False)
        except ValueError:
            check("raises when no WebKit2 introspection exists", True)

        # --- fallback ladder: failure → one log line + browser, then sticky
        records = []
        handler = logging.Handler()
        handler.emit = lambda r: records.append(r.getMessage())
        il.log.addHandler(handler)
        orig_level = il.log.level
        il.log.setLevel(logging.INFO)
        opened = []
        il._open_browser = lambda: opened.append(1)
        il._gi_modules = lambda: (_ for _ in ()).throw(
            RuntimeError("Namespace WebKit2 not available"))
        try:
            il.show_soon()
            check("gi failure opens the browser instead", opened == [1])
            check("failure is remembered", il._failed)
            il._gi_modules = lambda: (_ for _ in ()).throw(
                AssertionError("sticky failure must skip gi entirely"))
            il.show_soon()
            check("later clicks go straight to the browser", opened == [1, 1])
            check("says so exactly once",
                  sum("native Insights window unavailable" in m
                      for m in records) == 1, str(records))
        finally:
            il.log.setLevel(orig_level)
            il.log.removeHandler(handler)

        # --- dispatch: show_soon queues _show once onto the GLib context
        il._failed = False

        class FakeGLib:
            def __init__(self):
                self.queued = []

            def idle_add(self, cb):
                self.queued.append(cb)

        glib = FakeGLib()
        loops = []
        il._gi_modules = lambda: (glib, None, None)
        il._ensure_loop_thread = lambda g: loops.append(g)
        il.show_soon()
        check("show_soon queues _show via idle_add", glib.queued == [il._show])
        check("a dispatching loop is guaranteed", loops == [glib])

        # --- _show: builds once, reuses the window, loads once
        class FakeWin:
            def __init__(self):
                self.presented = self.shown = self.hidden = 0

            def show_all(self):
                self.shown += 1

            def present(self):
                self.presented += 1

            def hide(self):
                self.hidden += 1

        class FakeView:
            def __init__(self):
                self.loads = []

            def get_uri(self):
                return self.loads[-1] if self.loads else None

            def load_uri(self, uri):
                self.loads.append(uri)

        win, view = FakeWin(), FakeView()
        builds = []
        orig_build = il._build
        il._build = lambda: builds.append(1) or (win, view)
        try:
            check("_show returns False (idle_add runs it once)",
                  il._show() is False)
            il._show()
            check("window is built once and reused",
                  builds == [1] and win.presented == 2, str(builds))
            check("page loads exactly once",
                  view.loads == ["http://127.0.0.1:8377/"], str(view.loads))
        finally:
            il._build = orig_build
        check("close hides, never destroys",
              il._on_delete(win, None) is True and win.hidden == 1)

        # --- _show failure also lands in the ladder, not a raise
        il._failed, opened[:] = False, []
        il._build = lambda: (_ for _ in ()).throw(RuntimeError("no display"))
        try:
            il._window = None
            check("a broken build falls back to the browser",
                  il._show() is False and opened == [1] and il._failed)
        finally:
            il._build = orig_build

        # --- async load failure: window torn down, browser fallback, sticky
        # (WebKit's web process dying is the exact failure family a frozen
        # bundle risks — it must land in the ladder, not a blank window)
        il._failed, opened[:] = False, []
        win2, destroyed = FakeWin(), []
        win2.destroy = lambda: destroyed.append(1)
        il._window, il._webview = win2, FakeView()
        check("load-failed abandons the window and falls back",
              il._on_load_failed(None, None, "http://127.0.0.1:8377/",
                                 RuntimeError("boom")) is True
              and destroyed == [1] and opened == [1]
              and il._failed and il._window is None)
        il._failed, opened[:] = False, []
        il._on_web_process_died(None, "crashed")
        check("web-process death falls back too",
              opened == [1] and il._failed)

        # --- standby-loop grace: yields the context to a live tray loop
        from sotto import tray_linux as tl_mod
        old_tray_thread = tl_mod._thread
        try:
            tl_mod._thread = None
            check("no tray thread: standby loop starts immediately",
                  il._loop_grace() == 0.0)

            class AliveThread:
                def is_alive(self):
                    return True

            tl_mod._thread = AliveThread()
            check("live tray thread: standby gives its loop a head start",
                  il._loop_grace() == il.TRAY_LOOP_GRACE_S)
        finally:
            tl_mod._thread = old_tray_thread

        # --- env sanitize: frozen only, clean_env applied; the tray's
        # typelib path and the user's own LD_LIBRARY_PATH both survive, and
        # later clean_env() calls stay idempotent (_ORIG kept)
        env_saved = {k: os.environ.get(k) for k in
                     ("LD_LIBRARY_PATH", "LD_LIBRARY_PATH_ORIG",
                      "GI_TYPELIB_PATH")}
        had_frozen = hasattr(sys, "frozen")
        had_meipass = hasattr(sys, "_MEIPASS")
        try:
            il._sanitized = False
            os.environ["LD_LIBRARY_PATH"] = "/bundle/_internal"
            os.environ["LD_LIBRARY_PATH_ORIG"] = "/users/own"
            os.environ["GI_TYPELIB_PATH"] = "/bundle/_internal/gi_typelibs"
            if not had_frozen:
                sys.frozen = True
            if not had_meipass:
                sys._MEIPASS = "/bundle/_internal"
            il.sanitize_environ()
            check("frozen: the user's own LD_LIBRARY_PATH reaches the "
                  "WebKit helpers",
                  os.environ.get("LD_LIBRARY_PATH") == "/users/own")
            check("frozen: _ORIG kept so later clean_env() calls stay "
                  "idempotent",
                  os.environ.get("LD_LIBRARY_PATH_ORIG") == "/users/own")
            from sotto.platform.linux import clean_env
            check("clean_env() after sanitize returns the same value",
                  clean_env().get("LD_LIBRARY_PATH") == "/users/own")
            check("frozen: bundled GI_TYPELIB_PATH survives (the tray needs "
                  "it; WebKit helpers never read it)",
                  os.environ.get("GI_TYPELIB_PATH")
                  == "/bundle/_internal/gi_typelibs")
            check("sanitize is one-shot", il._sanitized)
            if not had_frozen:
                del sys.frozen
            il._sanitized = False
            os.environ["LD_LIBRARY_PATH"] = "/bundle/_internal"
            il.sanitize_environ()
            check("not frozen: environment untouched",
                  os.environ.get("LD_LIBRARY_PATH") == "/bundle/_internal"
                  and not il._sanitized)
        finally:
            if not had_frozen and hasattr(sys, "frozen"):
                del sys.frozen
            if not had_meipass and hasattr(sys, "_MEIPASS"):
                del sys._MEIPASS
            for k, v in env_saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    finally:
        (il._port, il._failed, il._window, il._webview,
         il._sanitized) = saved
        il._gi_modules, il._open_browser = orig_gi, orig_browser
        il._ensure_loop_thread = orig_loop


def test_listener_retry():
    print("hotkey-permission retry (failure alerts once, retries until up):")
    from sotto import app as app_mod
    from sotto.app import Sotto

    s = Sotto.__new__(Sotto)  # the wrapper touches no __init__ state
    calls, alerts = [], []

    class FakeListener:
        def run(self):
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError("no keyboard access")

    old_alert, old_retry = app_mod.alert, Sotto.LISTENER_RETRY_S
    app_mod.alert = lambda title, text: alerts.append(title)
    Sotto.LISTENER_RETRY_S = 0.01
    try:
        s._run_listener(FakeListener())
    finally:
        app_mod.alert, Sotto.LISTENER_RETRY_S = old_alert, old_retry
    check("retries until the listener stays up", len(calls) == 3, str(calls))
    check("alerts exactly once", len(alerts) == 1, str(alerts))


def test_logging_setup():
    print("log file (rotating, thread-exception capture, no transcripts):")
    import logging
    import logging.handlers
    import threading as _threading
    from sotto import app as app_mod

    root = logging.getLogger()
    before = list(root.handlers)
    old_hooks = (sys.excepthook, _threading.excepthook)
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "sotto.log")
        try:
            app_mod.setup_logging(path)
            app_mod.log.info("hello from the log test")

            def boom():
                raise RuntimeError("thread exploded (expected)")
            t = _threading.Thread(target=boom, name="test-boom")
            t.start()
            t.join()
            for h in root.handlers:
                h.flush()
            content = open(path).read()
            check("log file created and written",
                  "hello from the log test" in content)
            check("format carries level+module",
                  "INFO" in content and "test_pipeline" in content)
            check("thread exception captured with traceback",
                  "thread exploded" in content and "Traceback" in content)
            check("second setup call is a no-op",
                  (app_mod.setup_logging(path) or
                   sum(isinstance(h, logging.handlers.RotatingFileHandler)
                       for h in root.handlers) == 1))
        finally:
            for h in list(root.handlers):
                if h not in before:
                    root.removeHandler(h)
                    h.close()
            sys.excepthook, _threading.excepthook = old_hooks

    import inspect
    src = inspect.getsource(app_mod.Sotto._process_audio)
    check("dictation log line has no transcript text (lengths only)",
          "len(raw)" in src and "%r -> %r" not in src)


def test_firstrun_cosmetics():
    print("first-run cosmetics:")
    from sotto import firstrun
    check("app name falls back to Sotto outside a bundle",
          firstrun._app_name() == "Sotto")
    im = next(r for r in firstrun.ROWS if r[0] == "input_monitoring")
    check("Input Monitoring detail carries the ＋ hint", "＋" in im[2])


def test_permission_watchdog():
    print("permission watchdog (alerts once per revocation, re-arms on re-grant):")
    from sotto import app as app_mod
    from sotto.app import Sotto

    alerts = []
    state = {"ok": True}
    old_alert = app_mod.alert
    app_mod.alert = lambda title, text: alerts.append(title)
    try:
        watched = {"Accessibility": lambda: state["ok"]}
        good = {"Accessibility": True}
        Sotto._permission_poll_once(watched, good)
        check("no alert while granted", alerts == [])
        state["ok"] = False
        Sotto._permission_poll_once(watched, good)
        check("alert on revocation", len(alerts) == 1, str(alerts))
        Sotto._permission_poll_once(watched, good)
        check("no repeat while still revoked", len(alerts) == 1, str(alerts))
        state["ok"] = True
        Sotto._permission_poll_once(watched, good)
        state["ok"] = False
        Sotto._permission_poll_once(watched, good)
        check("re-grant re-arms the alert", len(alerts) == 2, str(alerts))
    finally:
        app_mod.alert = old_alert


def test_firstrun_gating():
    print("first-run gating (permissions gate the walkthrough, models don't):")
    from sotto import firstrun
    from sotto.config import Config

    cfg = Config()
    perms = ["mic_ok", "accessibility_ok", "input_monitoring_ok",
             "globe_key_ok"]
    saved = {name: getattr(firstrun, name) for name in perms}
    saved_models = (firstrun.asr_model_ok, firstrun.llm_model_ok)
    try:
        for name in perms:
            setattr(firstrun, name, lambda: True)
        firstrun.asr_model_ok = lambda _m: False
        firstrun.llm_model_ok = lambda _m: False
        check("missing models alone do NOT reopen the walkthrough",
              not firstrun.needed(cfg))
        check("models_missing sees them", firstrun.models_missing(cfg))
        firstrun.mic_ok = lambda: False
        check("a missing permission DOES open the walkthrough",
              firstrun.needed(cfg))
        firstrun.mic_ok = lambda: True
        firstrun.asr_model_ok = lambda _m: True
        firstrun.llm_model_ok = lambda _m: True
        check("all present → nothing needed",
              not firstrun.needed(cfg) and not firstrun.models_missing(cfg))
    finally:
        for name, fn in saved.items():
            setattr(firstrun, name, fn)
        firstrun.asr_model_ok, firstrun.llm_model_ok = saved_models


def test_firstrun_notifications():
    print("first-run notifications row (optional — must never gate setup):")
    from sotto import firstrun
    from sotto.config import Config

    check("notifications NOT in gating statuses",
          "notifications" not in firstrun.statuses(Config()))
    check("row exists in ROWS", any(r[0] == "notifications"
                                    for r in firstrun.ROWS))
    old = dict(firstrun._notif_status)
    try:
        firstrun._notif_status["value"] = None
        check("unknown status reads not-ok", not firstrun.notifications_ok())
        firstrun._notif_status["value"] = 1  # denied
        check("denied reads not-ok", not firstrun.notifications_ok())
        firstrun._notif_status["value"] = 2  # authorized
        check("authorized reads ok", firstrun.notifications_ok())
    finally:
        firstrun._notif_status.update(old)


def test_update():
    print("update check (pure logic, no network):")
    from sotto import update

    AS, INTEL, DEB = "-apple-silicon.dmg", "-intel.dmg", "-amd64.deb"

    check("suffix: mac arm64", update.asset_suffix("darwin", "arm64") == AS)
    check("suffix: mac intel", update.asset_suffix("darwin", "x86_64") == INTEL)
    check("suffix: linux amd64 deb bundle",
          update.asset_suffix("linux", "x86_64", bundle="deb") == DEB)
    check("suffix: linux amd64 appimage bundle",
          update.asset_suffix("linux", "x86_64", bundle="appimage")
          == "-x86_64.AppImage")
    check("suffix: linux arm64 → None (updater stays silent)",
          update.asset_suffix("linux", "aarch64", bundle="deb") is None)
    check("suffix: windows → None",
          update.asset_suffix("windows", "AMD64") is None)

    def release(tag, exts=(AS, INTEL, DEB + ".sig", DEB), **kw):
        v = tag.lstrip("v")
        return {"tag_name": tag,
                "assets": [{"name": f"Sotto-{v}{e}",
                            "browser_download_url": f"https://x/Sotto-{v}{e}"}
                           for e in exts], **kw}

    info = update.evaluate(release("v0.4.0"), "0.3.0", AS)
    check("newer release found", bool(info) and info["version"] == "0.4.0",
          repr(info))
    check("apple-silicon asset picked",
          bool(info) and info["url"].endswith("apple-silicon.dmg"))
    info = update.evaluate(release("v0.4.0"), "0.3.0", INTEL)
    check("intel asset picked", bool(info) and info["url"].endswith("intel.dmg"))
    info = update.evaluate(release("v0.4.0"), "0.3.0", DEB)
    check("deb asset picked with its signature",
          bool(info) and info["url"].endswith("amd64.deb")
          and info.get("sig_url", "").endswith("amd64.deb.sig"), repr(info))
    check("deb without a .sig is not offered",
          update.evaluate(release("v0.4.0", exts=(AS, INTEL, DEB)),
                          "0.3.0", DEB) is None)
    check("suffix matching is exact — a DMG can't satisfy a .deb suffix",
          update.evaluate(release("v0.4.0", exts=(AS, INTEL)),
                          "0.3.0", DEB) is None)
    check("None suffix → no update",
          update.evaluate(release("v0.4.0"), "0.3.0", None) is None)
    check("same version → no update",
          update.evaluate(release("v0.3.0"), "0.3.0", AS) is None)
    check("older version → no update",
          update.evaluate(release("v0.2.9"), "0.3.0", AS) is None)
    check("0.10 beats 0.9 (numeric, not lexical)",
          update.evaluate(release("v0.10.0"), "0.9.0", AS) is not None)
    check("draft ignored",
          update.evaluate(release("v9.9.9", draft=True), "0.3.0", AS) is None)
    check("prerelease ignored",
          update.evaluate(release("v9.9.9", prerelease=True), "0.3.0", AS) is None)
    check("missing arch asset → no update",
          update.evaluate(release("v0.4.0", exts=(INTEL,)), "0.3.0", AS) is None)

    with tempfile.TemporaryDirectory() as td:
        state = os.path.join(td, "update-state.json")
        check("due when never checked", update.due(state, 1))
        update.mark_checked(state, now=1000.0)
        check("not due right after a check",
              not update.due(state, 1, now=1000.0 + 3600))
        check("due once the interval passes",
              update.due(state, 1, now=1000.0 + 86401))


def test_update_linux():
    print("Linux updater backend (pure logic + injectable install flow):")
    import signal

    from sotto import update_linux as ul

    # bundle gate: frozen + SOTTO_BUNDLE decide (delegates to firstrun_linux)
    old = os.environ.pop("SOTTO_BUNDLE", None)
    orig_frozen = getattr(sys, "frozen", None)
    try:
        check("checkout → bundle_type None", ul.bundle_type() is None)
        os.environ["SOTTO_BUNDLE"] = "deb"
        check("unfrozen checkout stays None even with the env set",
              ul.bundle_type() is None)
        sys.frozen = True
        check("deb launcher env → bundle_type 'deb'", ul.bundle_type() == "deb")
    finally:
        if orig_frozen is None:
            del sys.frozen
        else:
            sys.frozen = orig_frozen
        os.environ.pop("SOTTO_BUNDLE", None)
        if old is not None:
            os.environ["SOTTO_BUNDLE"] = old

    argv = ul._ask_argv("T", "B")
    check("ask argv is zenity --question with Update Now/Later",
          argv[0] == "zenity" and "--question" in argv
          and any("Update Now" in a for a in argv), str(argv))
    argv = ul._ask_argv_kdialog("T", "B")
    check("kdialog fallback argv is --yesno", argv[0] == "kdialog"
          and "--yesno" in argv, str(argv))
    argv = ul._progress_argv("T")
    check("progress argv is zenity --progress --auto-close",
          argv[0] == "zenity" and "--progress" in argv
          and "--auto-close" in argv, str(argv))
    argv = ul._install_argv("/tmp/a.deb", "/tmp/a.deb.sig")
    check("install argv is pkexec + the pinned helper + deb + sig",
          argv == ["pkexec", ul.HELPER, "/tmp/a.deb", "/tmp/a.deb.sig"],
          str(argv))
    check("helper path is the packaged one",
          ul.HELPER == "/usr/libexec/sotto/sotto-install-update")
    argv = ul._relaunch_argv(12345)
    check("relaunch waits for OUR pid to vanish, then execs the launcher",
          "kill -0 12345" in argv[-1] and "/usr/bin/sotto" in argv[-1],
          str(argv))

    # install flow: pkexec dismissal (126) is a quiet no-op, helper failure
    # raises, success relaunches + SIGINTs self. All I/O injected.
    class R:
        def __init__(self, rc, err=""):
            self.returncode, self.stderr, self.stdout = rc, err, ""

    def flow(rc, err=""):
        calls = {"popen": [], "kill": []}
        info = {"version": "9.9.9", "name": "Sotto-9.9.9-amd64.deb",
                "url": "u", "sig_url": "s"}
        saved_bundle = os.environ.get("SOTTO_BUNDLE")
        os.environ["SOTTO_BUNDLE"] = "deb"
        saved_frozen = getattr(sys, "frozen", None)
        sys.frozen = True
        orig_exists, orig_kill = os.path.exists, os.kill
        # narrow patch: only the helper-presence probe is faked
        os.path.exists = (lambda p: True if p == ul.HELPER
                          else orig_exists(p))
        os.kill = lambda pid, sig: calls["kill"].append((pid, sig))

        def fake_get(url, stream=True, timeout=0):
            class Resp:
                headers = {}
                def raise_for_status(self): pass
                def iter_content(self, n): return iter([b"x"])
                def __enter__(self): return self
                def __exit__(self, *a): pass
            return Resp()

        import requests
        orig_get = requests.get
        requests.get = fake_get
        try:
            ul.download_and_install(
                info, lambda *a: None,
                runner=lambda *a, **k: R(rc, err),
                popen=lambda *a, **k: calls["popen"].append(a))
        finally:
            requests.get = orig_get
            os.path.exists, os.kill = orig_exists, orig_kill
            if saved_frozen is None:
                del sys.frozen
            else:
                sys.frozen = saved_frozen
            if saved_bundle is None:
                os.environ.pop("SOTTO_BUNDLE", None)
            else:
                os.environ["SOTTO_BUNDLE"] = saved_bundle
        return calls

    calls = flow(0)
    check("success → detached relaunch spawned",
          len(calls["popen"]) == 1, str(calls["popen"]))
    check("success → SIGINT to self (the designed shutdown path)",
          calls["kill"] == [(os.getpid(), signal.SIGINT)], str(calls["kill"]))
    calls = flow(126)
    check("polkit dismissal → no relaunch, no exit, no error",
          calls["popen"] == [] and calls["kill"] == [])
    try:
        flow(1, "signature verification FAILED")
        check("helper failure raises", False)
    except RuntimeError as e:
        check("helper failure raises with the helper's message",
              "signature verification FAILED" in str(e), str(e))

    # the offer path must never block on a missing dialog tool
    import shutil as _sh
    orig_which = _sh.which
    _sh.which = lambda name: None
    try:
        check("no zenity/kdialog → ask returns False, never raises",
              ul.ask("T", "B") is False)
    finally:
        _sh.which = orig_which


def test_appimage_bootstrap():
    print("AppImage (L9): bootstrap routing, payload pins, self-replace:")
    import signal

    from sotto import firstrun_linux as fl
    from sotto import update, update_linux as ul

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # --- fix routing: first run (no pinned helper) → generic-pkexec on a
    # STAGED copy (root can't read the FUSE mount — L9 security sweep)
    with tempfile.TemporaryDirectory() as td:
        bootstrap = os.path.join(td, "bootstrap")
        open(bootstrap, "w").write("#!/bin/sh\n")
        os.makedirs(os.path.join(td, "setup"))
        open(os.path.join(td, "setup", "sotto-perms"), "w").write("payload")
        saved = {k: os.environ.get(k) for k in ("APPIMAGE", "APPDIR",
                                                "SOTTO_BUNDLE")}
        orig_helper, orig_frozen = fl.HELPER, getattr(sys, "frozen", None)
        os.environ["APPIMAGE"] = "/x/Sotto.AppImage"
        os.environ["APPDIR"] = td
        sys.frozen = True
        fl.HELPER = os.path.join(td, "no-such-helper")
        try:
            check("appimage bundle detected", fl.bundle_type() == "appimage")
            check("update_linux delegates to the same detection",
                  ul.bundle_type() == "appimage")
            argv = fl.fix_input_argv()
            staged = argv[1] if len(argv) == 2 else ""
            check("no pinned helper → pkexec on a bootstrap copy "
                  "(generic prompt — the L5/L9 constraint)",
                  argv[0] == "pkexec"
                  and staged.replace(os.sep, "/").endswith("/bootstrap"),
                  str(argv))
            check("bootstrap is STAGED off the FUSE mount, executable, "
                  "with the setup payload beside it",
                  not staged.startswith(td)
                  and os.access(staged, os.X_OK)
                  and open(os.path.join(os.path.dirname(staged), "setup",
                                        "sotto-perms")).read() == "payload",
                  staged)
            fl.HELPER = bootstrap  # any existing file stands in for the helper
            argv = fl.fix_input_argv()
            check("pinned helper present → pinned action, bootstrap never again",
                  argv == ["pkexec", bootstrap, "apply"], str(argv))
        finally:
            fl.HELPER = orig_helper
            if orig_frozen is None:
                del sys.frozen
            else:
                sys.frozen = orig_frozen
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            if fl._staged_bootstrap_dir:
                import shutil as _sh
                _sh.rmtree(fl._staged_bootstrap_dir, ignore_errors=True)
                fl._staged_bootstrap_dir = None

    # --- bootstrap script pins (byte-identical payload, no apt helper)
    boot = open(os.path.join(root, "linuxapp", "appimage", "bootstrap")).read()
    for dest in ("/usr/lib/udev/rules.d/60-sotto-input.rules",
                 "/usr/lib/modules-load.d/sotto-uinput.conf",
                 "/usr/share/polkit-1/actions/io.github.psancheti6666.sotto.policy",
                 "/usr/libexec/sotto/sotto-perms"):
        check(f"bootstrap installs {dest.split('/')[-1]}", dest in boot)
    check("bootstrap does NOT install the apt-based updater helper",
          not any("sotto-install-update" in line
                  for line in boot.splitlines()
                  if not line.strip().startswith("#")))
    check("bootstrap grants via PKEXEC_UID, fixed install modes",
          "PKEXEC_UID" in boot and "install -D -m 755" in boot)
    apprun = open(os.path.join(root, "linuxapp", "appimage", "AppRun")).read()
    check("AppRun exports SOTTO_BUNDLE=appimage",
          "SOTTO_BUNDLE=appimage" in apprun)
    mk = open(os.path.join(root, "linuxapp", "make_appimage.sh")).read()
    for f in ("60-sotto-input.rules", "sotto-uinput.conf",
              "io.github.psancheti6666.sotto.policy", "sotto-perms",
              "sotto-release.pub"):
        check(f"make_appimage embeds {f} from linuxapp/deb (byte-identical)",
              f in mk and "linuxapp/deb/$f" in mk)
    check("make_appimage pins the vendored runtime hash",
          "RUNTIME_SHA256=" in mk and "sha256sum -c" in mk)
    # and the pin must match the actual vendored bytes AND PROVENANCE.md —
    # drift is otherwise invisible until the Linux CI build runs
    import hashlib
    import re as _re
    pinned = _re.search(r"RUNTIME_SHA256=([0-9a-f]{64})", mk).group(1)
    actual = hashlib.sha256(open(os.path.join(
        root, "linuxapp", "appimage", "runtime-x86_64"), "rb").read()).hexdigest()
    prov = open(os.path.join(root, "linuxapp", "appimage", "PROVENANCE.md")).read()
    check("pinned hash == vendored runtime bytes == PROVENANCE.md",
          pinned == actual and pinned in prov,
          f"pin={pinned[:12]} actual={actual[:12]}")

    # --- evaluate: AppImage assets need their signature too
    AI = "-x86_64.AppImage"
    rel = {"tag_name": "v9.9.9", "assets": [
        {"name": f"Sotto-9.9.9{AI}", "browser_download_url": "https://x/a"},
        {"name": f"Sotto-9.9.9{AI}.sig", "browser_download_url": "https://x/s"}]}
    info = update.evaluate(rel, "0.1.0", AI)
    check("AppImage asset picked with its signature",
          bool(info) and info["sig_url"] == "https://x/s", repr(info))
    rel["assets"].pop()
    check("AppImage without a .sig is not offered",
          update.evaluate(rel, "0.1.0", AI) is None)

    # --- self-replace flow (all I/O injected)
    def replace_flow(verify_rc):
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "Sotto.AppImage")
            open(target, "w").write("OLD")
            setup = os.path.join(td, "appdir", "setup")
            os.makedirs(setup)
            open(os.path.join(setup, "sotto-release.pub"), "w").write("PUB")
            saved = {k: os.environ.get(k) for k in ("APPIMAGE", "APPDIR")}
            os.environ["APPIMAGE"] = target
            os.environ["APPDIR"] = os.path.join(td, "appdir")
            calls = {"popen": [], "kill": [], "runner": []}
            orig_kill = os.kill
            os.kill = lambda pid, sig: calls["kill"].append((pid, sig))

            def fake_get(url, stream=True, timeout=0):
                class Resp:
                    headers = {}
                    def raise_for_status(self): pass
                    def iter_content(self, n): return iter([b"NEW"])
                    def __enter__(self): return self
                    def __exit__(self, *a): pass
                return Resp()

            import requests
            orig_get = requests.get

            class R:
                returncode = verify_rc
                stderr = stdout = ""

            requests.get = fake_get
            err = None
            try:
                ul._self_replace(
                    {"version": "9.9.9", "name": "Sotto-9.9.9-x86_64.AppImage",
                     "url": "u", "sig_url": "s"},
                    lambda *a: None,
                    runner=lambda *a, **k: (calls["runner"].append(a[0]),
                                            R())[1],
                    popen=lambda *a, **k: calls["popen"].append(a))
            except RuntimeError as e:
                err = e
            finally:
                requests.get = orig_get
                os.kill = orig_kill
                for k, v in saved.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
            leftovers = [p for p in os.listdir(td)
                         if p.startswith("Sotto.AppImage.sotto-new")]
            expected_pub = os.path.join(td, "appdir", "setup",
                                        "sotto-release.pub")
            return (open(target).read(), calls, err, leftovers,
                    expected_pub)

    content, calls, err, leftovers, expected_pub = replace_flow(0)
    check("verified update replaces $APPIMAGE atomically",
          content == "NEW" and err is None, repr((content, err)))
    check("verification uses the pubkey EMBEDDED in the running AppImage "
          "(the milestone's key invariant)",
          len(calls["runner"]) == 1 and calls["runner"][0][:5] ==
          ["openssl", "dgst", "-sha256", "-verify", expected_pub],
          str(calls["runner"]))
    check("relaunch waits for our pid then execs the new AppImage",
          len(calls["popen"]) == 1 and "Sotto.AppImage" in calls["popen"][0][0][-1],
          str(calls["popen"]))
    check("self-replace exits via the designed SIGINT path",
          calls["kill"] == [(os.getpid(), signal.SIGINT)])
    check("no temp files left beside the AppImage", leftovers == [],
          str(leftovers))
    content, calls, err, leftovers, _ = replace_flow(1)
    check("failed verification NEVER touches $APPIMAGE",
          content == "OLD" and err is not None
          and "signature verification FAILED" in str(err),
          repr((content, err)))
    check("failed verification leaves no temp files and no relaunch",
          leftovers == [] and calls["popen"] == [] and calls["kill"] == [])


ASR_CASES = [
    "Let's meet on Friday at three PM to review the quarterly report.",
    "The quick brown fox jumps over the lazy dog.",
    "Please update the readme and push the release by Wednesday.",
]


def synthesize(sentence: str):
    """16 kHz float32 mono speech — `say` on macOS, espeak-ng on Linux."""
    import numpy as np
    import wave as wavemod
    with tempfile.TemporaryDirectory() as td:
        wav = os.path.join(td, "t.wav")
        if sys.platform == "darwin":
            subprocess.run(["say", "-o", wav, "--data-format=LEI16@16000", sentence],
                           check=True)
        else:
            subprocess.run(["espeak-ng", "-w", wav, sentence], check=True)
        with wavemod.open(wav) as w:
            sr = w.getframerate()
            audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    audio = audio.astype(np.float32) / 32768.0
    if sr != 16000:
        n = int(audio.size * 16000 / sr)
        audio = np.interp(np.linspace(0, audio.size - 1, n),
                          np.arange(audio.size), audio).astype(np.float32)
    return audio


def _match_score(expected: str, got: str) -> float:
    from rapidfuzz import fuzz
    norm = lambda s: "".join(c for c in s.lower() if c.isalnum() or c == " ")
    return fuzz.ratio(norm(expected), norm(got))


def test_asr():
    print("ASR (parakeet-mlx) on synthesized speech:")
    from sotto.asr_mlx import ParakeetASR
    asr = ParakeetASR()
    for sentence in ASR_CASES:
        audio = synthesize(sentence)
        t0 = time.perf_counter()
        out = asr.transcribe(audio)
        dt = time.perf_counter() - t0
        score = _match_score(sentence, out)
        check(f"({dt:.2f}s, {score:.0f}% match) {sentence[:40]}…", score >= 85, f"got: {out!r}")


def test_asr_onnx():
    print("ASR (ONNX backend — the Intel Mac / Linux path) on synthesized speech:")
    try:
        from sotto.asr_onnx import OnnxParakeetASR
        asr = OnnxParakeetASR()
    except ImportError:
        print("  [skip] onnx-asr not installed (pip install 'onnx-asr[cpu,hub]')")
        return
    import numpy as np
    clips = {}
    for sentence in ASR_CASES:
        clips[sentence] = synthesize(sentence)
        t0 = time.perf_counter()
        out = asr.transcribe(clips[sentence])
        dt = time.perf_counter() - t0
        score = _match_score(sentence, out)
        check(f"({dt:.2f}s, {score:.0f}% match) {sentence[:40]}…", score >= 85, f"got: {out!r}")
    # long-form: tile past the VAD threshold so the silero-segmented path runs
    clip = clips[ASR_CASES[0]]
    gap = np.zeros(8000, dtype=np.float32)
    reps = int(125 * 16000 / (clip.size + gap.size)) + 1
    audio = np.concatenate([np.concatenate([clip, gap]) for _ in range(reps)])
    t0 = time.perf_counter()
    out = asr.transcribe(audio)
    dt = time.perf_counter() - t0
    n = out.count("Friday")
    print(f"  {audio.size/16000:.0f}s of audio, {reps} repeats → transcribed in {dt:.1f}s (VAD)")
    check(f"long-form via VAD keeps every repeat ({n}/{reps})", n >= reps - 1, out[:200])


def test_recorder_truncation():
    print("recorder keeps the BEGINNING when over the cap:")
    import numpy as np
    from sotto.audio import Recorder
    r = Recorder.__new__(Recorder)  # no audio device needed for this test
    import threading
    r.sample_rate, r._max_frames = 16000, 16000  # 1 s cap
    r._frames, r._n_frames, r._recording, r._level = [], 0, False, 0.0
    r._lock = threading.Lock()
    r._recording = True
    ramp = np.arange(32000, dtype=np.float32).reshape(-1, 1) / 32000  # 2 s ramp
    for i in range(0, 32000, 1024):
        r._callback(ramp[i:i + 1024], None, None, None)
    audio = r.stop()
    check("capped at 1s", audio.size == 16000, f"{audio.size} frames")
    check("kept the FIRST second", audio.size > 0 and audio[0] == 0.0 and audio[-1] < 0.52,
          f"first={audio[0]}, last={audio[-1] if audio.size else 'n/a'}")


def test_force_stop():
    print("watchdog force-stop:")
    if sys.platform not in ("darwin", "win32"):
        print("  (skipped — HotkeyListener needs pynput, absent on Linux;"
              " the shared gesture state machine is covered by test_evdev_gestures)")
        return
    from sotto.hotkey import HotkeyListener
    ev = []
    hl = HotkeyListener("alt_r", on_start=lambda: ev.append("start"),
                        on_stop=lambda discard=False: ev.append("discard" if discard else "stop"))
    hl._hotkey_press()          # start dictating (hold)
    hl.force_stop()             # time limit reached
    check("stops and processes", ev == ["start", "stop"], str(ev))
    hl.force_stop()
    check("idempotent when idle", ev == ["start", "stop"], str(ev))


def test_evdev_gestures():
    print("evdev hotkey gestures (Linux path, synthetic kernel events):")
    from sotto.hotkey_evdev import EvdevHotkeyListener, KEY_CODES, KEY_ESC
    RC = KEY_CODES["ctrl_r"]
    KEY_A, KEY_SPACE = 30, 57

    def make(tap_max=0.0, window=0.5):
        ev = []
        hl = EvdevHotkeyListener(
            "ctrl_r",
            on_start=lambda: ev.append("start"),
            on_stop=lambda discard=False: ev.append("discard" if discard else "stop"),
            tap_max_s=tap_max, double_tap_window_s=window,
            on_handsfree=lambda: ev.append("handsfree"),
            on_cancel=lambda: ev.append("cancel"))
        return hl, ev

    hl, ev = make()  # tap_max=0 → any hold counts as speech
    hl._handle_event(RC, 1); hl._handle_event(RC, 2); hl._handle_event(RC, 0)
    check("hold → start/stop, autorepeat ignored", ev == ["start", "stop"], str(ev))

    hl, ev = make(tap_max=10)  # tap_max=10 → any press counts as a tap
    hl._handle_event(RC, 1); hl._handle_event(RC, 0)
    check("quick tap discards", ev == ["start", "discard"], str(ev))
    hl._handle_event(RC, 1)  # second tap inside the double-tap window
    check("double-tap → hands-free", ev[-2:] == ["start", "handsfree"], str(ev))
    hl._handle_event(RC, 0)  # releasing in hands-free keeps recording
    hl._handle_event(KEY_A, 1); hl._handle_event(KEY_A, 0)  # typing while hands-free is fine
    hl._handle_event(RC, 1)  # press again finishes
    check("press again stops hands-free", ev[-1] == "stop", str(ev))
    hl._handle_event(RC, 0)

    hl, ev = make()
    hl._handle_event(RC, 1); hl._handle_event(KEY_ESC, 1)
    check("Escape cancels", ev == ["start", "cancel"], str(ev))
    hl._handle_event(KEY_ESC, 0); hl._handle_event(RC, 0)
    check("hotkey release after cancel is consumed", ev == ["start", "cancel"], str(ev))

    hl, ev = make()
    hl._handle_event(RC, 1); hl._handle_event(KEY_A, 1)
    check("other key while holding = combo, discards", ev == ["start", "discard"], str(ev))

    hl, ev = make()
    hl._handle_event(RC, 1); hl._handle_event(KEY_SPACE, 1)
    check("hold+Space = combo on Linux (no key swallowing)", ev == ["start", "discard"], str(ev))


def test_evdev_permission_detection():
    print("evdev permission misdiagnosis (list_devices hides unreadable nodes):")
    from types import SimpleNamespace
    import sotto.hotkey_evdev as he
    from sotto.hotkey_evdev import EvdevHotkeyListener, PERMISSION_HELP, KEY_CODES

    RC, A, BTN_MOUSE = KEY_CODES["ctrl_r"], 30, 272

    class FakeDev:
        name = "fake"
        def __init__(self, keys): self._keys = keys
        def capabilities(self): return {1: self._keys}
        def close(self): pass

    def fake_evdev(accessible, caps, opener=None):
        return SimpleNamespace(
            ecodes=SimpleNamespace(EV_KEY=1),
            list_devices=lambda: list(accessible),
            InputDevice=opener or (lambda path: FakeDev(caps.get(path, []))))

    def expect_raise(name, evdev_obj):
        try:
            hl._open_keyboards(evdev_obj)
            check(name, False, "did not raise")
        except RuntimeError as e:
            check(name, str(e) == PERMISSION_HELP, str(e))

    hl = EvdevHotkeyListener("ctrl_r", on_start=lambda: None,
                             on_stop=lambda discard=False: None)
    orig = he._list_raw
    try:
        he._list_raw = lambda: ["/dev/input/event0", "/dev/input/event1"]
        # not in the input group: raw nodes exist, accessible list is EMPTY —
        # the case the old code misdiagnosed as "no keyboard plugged in"
        expect_raise("empty accessible + raw nodes → PERMISSION_HELP",
                     fake_evdev([], {}))
        # only a mouse is readable; the keyboard node is silently filtered
        expect_raise("mouse-only accessible → PERMISSION_HELP",
                     fake_evdev(["/dev/input/event0"],
                                {"/dev/input/event0": [BTN_MOUSE]}))
        # legacy path: InputDevice itself raises PermissionError
        def denied_open(path):
            raise PermissionError(path)
        he._list_raw = lambda: ["/dev/input/event0"]
        expect_raise("PermissionError from open → PERMISSION_HELP",
                     fake_evdev(["/dev/input/event0"], {}, opener=denied_open))
        # genuinely no keyboard: everything readable, none keyboard-capable
        devs = hl._open_keyboards(
            fake_evdev(["/dev/input/event0"], {"/dev/input/event0": [BTN_MOUSE]}))
        check("all readable, none a keyboard → no raise, empty list",
              devs == [], str(devs))
        # happy path: a readable keyboard with the hotkey + KEY_A
        he._list_raw = lambda: ["/dev/input/event0", "/dev/input/event1"]
        devs = hl._open_keyboards(
            fake_evdev(["/dev/input/event0", "/dev/input/event1"],
                       {"/dev/input/event0": [BTN_MOUSE],
                        "/dev/input/event1": [RC, A]}))
        check("readable keyboard found → returned", len(devs) == 1, str(devs))
    finally:
        he._list_raw = orig

    print("dashboard _respond swallows disconnects:")
    from sotto.dashboard import _Handler

    class Gone:
        def send_response(self, code): raise BrokenPipeError
    try:
        _Handler._respond(Gone(), b"x", "text/plain")
        check("BrokenPipeError swallowed", True, "")
    except BrokenPipeError:
        check("BrokenPipeError swallowed", False, "raised")

    class Reset:
        def send_response(self, code): raise ConnectionResetError
    try:
        _Handler._respond(Reset(), b"x", "text/plain")
        check("ConnectionResetError swallowed", True, "")
    except ConnectionResetError:
        check("ConnectionResetError swallowed", False, "raised")


def test_firstrun_linux():
    print("Linux first-run: checks, gating, fix argv, relaunch:")
    from types import SimpleNamespace
    import sotto.hotkey_evdev as he
    from sotto import firstrun, firstrun_linux as fl
    from sotto.config import Config

    RC, A = he.KEY_CODES["ctrl_r"], 30

    class FakeDev:
        def __init__(self, keys): self._keys = keys
        def capabilities(self): return {1: self._keys}
        def close(self): pass

    def fake_evdev(caps):
        return SimpleNamespace(
            ecodes=SimpleNamespace(EV_KEY=1),
            InputDevice=lambda path: FakeDev(caps[path])
            if path in caps else (_ for _ in ()).throw(PermissionError(path)))

    orig_raw = he._list_raw
    try:
        he._list_raw = lambda: ["/dev/input/event0", "/dev/input/event1"]
        check("keyboard readable → input_ok",
              fl.input_ok("ctrl_r", fake_evdev(
                  {"/dev/input/event1": [RC, A]})))
        check("nothing readable → not input_ok",
              not fl.input_ok("ctrl_r", fake_evdev({})))
        check("mouse only → not input_ok",
              not fl.input_ok("ctrl_r", fake_evdev(
                  {"/dev/input/event0": [272]})))
    finally:
        he._list_raw = orig_raw

    def failing_open(path, flags):
        raise OSError("denied")
    check("uinput denied → False", not fl.uinput_ok(opener=failing_open))

    from sotto import inject_linux as il

    class FakeChain:
        def __init__(self, name): self._injectors = [type(name, (), {})()]
    orig_build = il.build_injector
    try:
        il.build_injector = lambda: FakeChain("_XdotoolInjector")
        check("typing injector → injection_ok", fl.injection_ok())
        il.build_injector = lambda: FakeChain("_ClipboardNotifyInjector")
        check("clipboard fallback → not injection_ok", not fl.injection_ok())
    finally:
        il.build_injector = orig_build
    # pin the real attribute name injection_ok reaches for (_injectors) so a
    # rename of _Chain's field can't leave injection_ok silently False
    check("real _Chain exposes _injectors",
          hasattr(il._Chain([object()]), "_injectors"))

    # gating: perms OR a pending download trigger the walkthrough (so the
    # 3-4 GB download always gets the consent checkbox — #64 review);
    # SOTTO_FIRSTRUN forces both ways; the pending marker suppresses the loop
    cfg = Config()
    orig_in, orig_inj, orig_sm = fl.input_ok, fl.injection_ok, fl.setup_missing
    orig_marker = fl.firstrun.PENDING_MARKER
    had = os.environ.pop("SOTTO_FIRSTRUN", None)
    tmpd = tempfile.mkdtemp()
    marker = os.path.join(tmpd, ".firstrun-pending")  # never touch real ~/.sotto
    fl.firstrun.PENDING_MARKER = marker
    try:
        fl.input_ok = lambda *a, **k: True
        fl.injection_ok = lambda: True
        fl.setup_missing = lambda c: False
        check("perms green + nothing to download → not needed",
              not fl.needed(cfg))
        fl.injection_ok = lambda: False
        check("injection missing → needed", fl.needed(cfg))
        # the bypass #64 closed: perms already green but models missing must
        # STILL show the walkthrough (else the download runs without consent)
        fl.injection_ok = lambda: True
        fl.setup_missing = lambda c: True
        check("perms green but download pending → needed (consent gate)",
              fl.needed(cfg))
        os.environ["SOTTO_FIRSTRUN"] = "0"
        check("SOTTO_FIRSTRUN=0 suppresses", not fl.needed(cfg))
        os.environ.pop("SOTTO_FIRSTRUN", None)
        # once consent is given (marker written), no re-loop
        open(marker, "w").close()
        check("pending marker suppresses the re-loop", not fl.needed(cfg))
        os.unlink(marker)
        os.environ["SOTTO_FIRSTRUN"] = "1"
        check("SOTTO_FIRSTRUN=1 forces", fl.needed(cfg))
    finally:
        fl.input_ok, fl.injection_ok, fl.setup_missing = (
            orig_in, orig_inj, orig_sm)
        fl.firstrun.PENDING_MARKER = orig_marker
        import shutil as _sh
        _sh.rmtree(tmpd, ignore_errors=True)
        os.environ.pop("SOTTO_FIRSTRUN", None)
        if had is not None:
            os.environ["SOTTO_FIRSTRUN"] = had

    # setup_missing: engine adoptable / resolvable / absent
    from sotto import llm_server, ollama_runtime
    orig_mm = firstrun.models_missing
    orig_reach, orig_res = llm_server._reachable, ollama_runtime.resolve
    try:
        firstrun.models_missing = lambda c: False
        llm_server._reachable = lambda url: True
        check("reachable server → nothing missing", not fl.setup_missing(cfg))
        llm_server._reachable = lambda url: False
        ollama_runtime.resolve = lambda: "/usr/bin/ollama"
        check("resolvable binary → nothing missing", not fl.setup_missing(cfg))
        ollama_runtime.resolve = lambda: None
        check("no engine anywhere → missing", fl.setup_missing(cfg))
        firstrun.models_missing = lambda c: True
        check("models missing → missing", fl.setup_missing(cfg))
    finally:
        firstrun.models_missing = orig_mm
        llm_server._reachable, ollama_runtime.resolve = orig_reach, orig_res

    # fix argv is always the benign pkexec apply — the helper derives the
    # target user from PKEXEC_UID, so no username is passed (security review)
    check("fix input → pkexec apply, no user arg",
          fl.fix_input_argv() == ["pkexec", fl.HELPER, "apply"])

    # bundle_type + relaunch argv under bundle-env permutations
    saved = {k: os.environ.get(k) for k in ("APPIMAGE", "APPDIR", "SOTTO_BUNDLE")}
    for k in saved:
        os.environ.pop(k, None)
    orig_frozen = getattr(sys, "frozen", None)
    try:
        sys.frozen = True
        os.environ["SOTTO_BUNDLE"] = "deb"
        check("deb bundle_type", fl.bundle_type() == "deb")
        del os.environ["SOTTO_BUNDLE"]
        os.environ["APPIMAGE"] = "/home/u/Sotto.AppImage"
        check("appimage bundle_type", fl.bundle_type() == "appimage")
        check("appimage relaunch = $APPIMAGE",
              fl.relaunch_argv() == ["/home/u/Sotto.AppImage"])
        del os.environ["APPIMAGE"]
        check("frozen relaunch = executable",
              fl.relaunch_argv() == [sys.executable])
        del sys.frozen
        check("checkout bundle_type None", fl.bundle_type() is None)
        check("checkout relaunch = -m sotto",
              fl.relaunch_argv() == [sys.executable, "-m", "sotto"])
    finally:
        if orig_frozen is not None:
            sys.frozen = orig_frozen
        elif hasattr(sys, "frozen"):
            del sys.frozen
        # restore deterministically regardless of where an exception landed
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # ydotoold unit: socket path must match the injector client's socket, or
    # daemon + client start fine yet never connect (Typing row stuck red)
    from sotto import inject_linux as il2
    unit = fl._ydotoold_unit("/usr/bin/ydotoold")
    check("unit starts ydotoold at %t/.ydotool_socket",
          "ExecStart=/usr/bin/ydotoold --socket-path=%t/.ydotool_socket" in unit,
          unit)
    check("unit has RestartSec (avoids start-limit wedge)", "RestartSec=2" in unit)
    check("injector client socket is XDG_RUNTIME_DIR/.ydotool_socket "
          "(matches %t)", il2.YDOTOOL_SOCKET.endswith("/.ydotool_socket")
          and il2._ydotool_env()["YDOTOOL_SOCKET"] == il2.YDOTOOL_SOCKET,
          il2.YDOTOOL_SOCKET)
    check("systemctl setup does reset-failed before enable",
          [a[2] for a in fl._YDOTOOLD_SETUP]
          == ["daemon-reload", "reset-failed", "enable"],
          str(fl._YDOTOOLD_SETUP))

    # fix_injection with no ydotoold → user-visible alert (not a silent dead
    # button). fix_injection does `from .platform import alert`, so patch there.
    from sotto import platform as spkg
    alerts = []
    orig_which2, orig_alert2 = fl.shutil.which, spkg.alert
    fl.shutil.which = lambda c: None
    spkg.alert = lambda t, x: alerts.append((t, x))
    try:
        fl.fix_injection()
        check("no ydotoold → alert, not silent",
              len(alerts) == 1 and "ydotool" in alerts[0][1], str(alerts))
    finally:
        fl.shutil.which, spkg.alert = orig_which2, orig_alert2

    # autostart writer
    orig_auto = fl.AUTOSTART_PATH
    try:
        with tempfile.TemporaryDirectory() as td:
            fl.AUTOSTART_PATH = os.path.join(td, "autostart", "sotto.desktop")
            check("no autostart file → False", not fl.autostart_ok())
            fl.fix_autostart()
            check("fix_autostart writes a desktop entry",
                  fl.autostart_ok()
                  and "Exec=" in open(fl.AUTOSTART_PATH).read())
    finally:
        fl.AUTOSTART_PATH = orig_auto


def test_tk_firstrun_windows():
    print("Linux first-run Tk windows (headless instantiate):")
    import tkinter as tk
    from sotto import firstrun_linux as fl, firstrun_tk as ft
    from sotto.config import Config
    try:
        probe = tk.Tk()
    except tk.TclError:
        print("  (no display — Tk window test runs where one exists)")
        return
    probe.destroy()

    cfg = Config()
    orig = (fl.input_ok, fl.injection_ok, fl.setup_missing, fl.autostart_ok)
    fl.input_ok = lambda *a, **k: True
    fl.injection_ok = lambda: False
    fl.setup_missing = lambda c: True
    fl.autostart_ok = lambda: False
    try:
        # backend passed EXPLICITLY: these blocks exercise the Linux flow,
        # and _backend()'s platform pick would hand windows-latest (which
        # has a real display) the Windows rows instead
        w = ft._Walkthrough(cfg, backend=fl)
        st = w.tick(loop=False)
        check("tick reports the stubbed states",
              st["input"] and not st["injection"], str(st))
        check("Start disabled while a gating row is red",
              str(w.start_btn["state"]) == "disabled")
        fl.injection_ok = lambda: True
        w.tick(loop=False)
        check("Start stays disabled until the 3-4 GB download is OK'd "
              "(VM-round product decision)",
              str(w.start_btn["state"]) == "disabled")
        w.models_ok.set(True)
        w.tick(loop=False)
        check("Start enables when gating rows green + download acknowledged",
              str(w.start_btn["state"]) == "normal")
        w.close()

        s = ft._DownloadScreen(cfg, backend=fl)
        s.q.put(("cleanup engine: 40%", 0.4))
        cont = s.drain()
        check("progress line lands in the bar",
              abs(float(s.bar["value"]) - 0.4) < 1e-6 and cont, str(s.bar["value"]))
        s.q.put(("__done__", None))
        stop = s.drain()  # setup still "missing" → must show Retry, stop polling
        check("incomplete download shows Retry and stops polling",
              s.retry.winfo_manager() != "" and stop is False)
        # Retry must re-arm: a subsequent successful run reaches relaunch.
        # relaunch is stubbed to a recorder — the REAL relaunch execv's into a
        # live Sotto, so it must never run under test.
        relaunched = []
        orig_re = fl.relaunch
        fl.relaunch = lambda: relaunched.append(True)
        try:
            fl.setup_missing = lambda c: False
            s.q.put(("__done__", None))
            s.drain()
            check("completed download relaunches", relaunched == [True])

            # engine-download-failure branch of begin().work(): the except
            # posts a failure line + exactly one __done__ (screen doesn't
            # hang), and finish() shows Retry — never relaunch. setup_missing
            # forced True so even if drain reaches finish, no relaunch fires.
            fl.setup_missing = lambda c: True
            from sotto import ollama_runtime as orr
            orig_em, orig_dl = fl.engine_missing, orr.download
            fl.engine_missing = lambda c: True
            def boom(cb=None): raise RuntimeError("network down")
            orr.download = boom
            s2 = ft._DownloadScreen(cfg, backend=fl)
            try:
                s2.begin()  # spawns worker; no mainloop, so pump manually below
                import time as _t
                for _ in range(150):  # drain until the worker's __done__ lands
                    if not s2._busy:   # __done__ consumed → finish() has run
                        break
                    s2.drain()
                    _t.sleep(0.02)
                check("engine failure lands on Retry (no hang), status shows the "
                      "failure, and no relaunch fires",
                      s2.retry.winfo_manager() != ""
                      and "failed" in str(s2.status.cget("text"))
                      and relaunched == [True],
                      f"busy={s2._busy} retry={s2.retry.winfo_manager()!r} "
                      f"status={s2.status.cget('text')!r} relaunched={relaunched}")
            finally:
                fl.engine_missing, orr.download = orig_em, orig_dl
                s2.root.destroy()
        finally:
            fl.relaunch = orig_re
    finally:
        (fl.input_ok, fl.injection_ok, fl.setup_missing, fl.autostart_ok) = orig

    # --- same windows, Windows backend (W5): mic gates Start, consent
    # checkbox still owns the models row, Start routes through the
    # backend's relaunch (spawn-then-exit — stubbed, like fl.relaunch above)
    from sotto import firstrun_windows as fw
    orig_w = (fw.mic_ok, fw.setup_missing, fw.autostart_ok, fw.relaunch)
    orig_marker = fw.firstrun.PENDING_MARKER
    relaunched_w = []
    fw.mic_ok = lambda reader=None: False
    fw.setup_missing = lambda c: True
    fw.autostart_ok = lambda: False
    fw.relaunch = lambda: relaunched_w.append(True)
    try:
        with tempfile.TemporaryDirectory() as td:
            fw.firstrun.PENDING_MARKER = os.path.join(td, "pending")
            w = ft._Walkthrough(cfg, backend=fw)
            st = w.tick(loop=False)
            check("windows backend rows render (mic/models/autostart)",
                  set(st) == {"mic", "models", "autostart"}, str(st))
            w.models_ok.set(True)
            w.tick(loop=False)
            check("mic off gates Start even with download consent",
                  str(w.start_btn["state"]) == "disabled")
            fw.mic_ok = lambda reader=None: True
            w.tick(loop=False)
            check("mic on + consent → Start enabled",
                  str(w.start_btn["state"]) == "normal")
            w.start()
            check("Start writes the marker and relaunches via the backend",
                  os.path.exists(fw.firstrun.PENDING_MARKER)
                  and relaunched_w == [True])
    finally:
        (fw.mic_ok, fw.setup_missing, fw.autostart_ok, fw.relaunch) = orig_w
        fw.firstrun.PENDING_MARKER = orig_marker


def test_platform_detection():
    print("platform detection and Linux config defaults:")
    import sotto.platform as sp
    import sotto.config as sc
    orig_flag = sp.IS_LINUX
    saved_env = {k: os.environ.pop(k, None)
                 for k in ("WAYLAND_DISPLAY", "XDG_SESSION_TYPE", "DISPLAY")}
    try:
        sp.IS_LINUX = True
        os.environ["WAYLAND_DISPLAY"] = "wayland-0"
        check("Wayland detected", sp.session_type() == "wayland", sp.session_type())
        del os.environ["WAYLAND_DISPLAY"]
        os.environ["XDG_SESSION_TYPE"] = "x11"
        check("X11 detected", sp.session_type() == "x11", sp.session_type())
        del os.environ["XDG_SESSION_TYPE"]
        os.environ["DISPLAY"] = ":0"
        check("DISPLAY alone → x11", sp.session_type() == "x11", sp.session_type())
        del os.environ["DISPLAY"]
        check("headless → ''", sp.session_type() == "", sp.session_type())
    finally:
        sp.IS_LINUX = orig_flag
        for k, v in saved_env.items():
            if v is not None:
                os.environ[k] = v

    orig_cfg_flags = (sc.IS_LINUX, sc.IS_WINDOWS, sc.CONFIG_PATH)
    try:
        sc.IS_WINDOWS = False  # both platform flags forced — the branch
        sc.IS_LINUX, sc.CONFIG_PATH = True, "/nonexistent/config.toml"
        cfg = sc.load_config()
        check("Linux default hotkey is ctrl_r", cfg.hotkey == "ctrl_r", cfg.hotkey)
        check("Linux sounds use freedesktop names", cfg.done_sound == "complete", cfg.done_sound)
        check("Linux haptics off", cfg.haptics is False)
        check("Linux terminals are keystroke apps", "konsole" in cfg.keystroke_apps,
              str(cfg.keystroke_apps))
        sc.IS_LINUX = False  # neither flag → macOS defaults, on any host
        cfg = sc.load_config()
        check("macOS defaults untouched", cfg.hotkey == "fn" and cfg.done_sound == "Morse",
              f"{cfg.hotkey}/{cfg.done_sound}")
    finally:
        sc.IS_LINUX, sc.IS_WINDOWS, sc.CONFIG_PATH = orig_cfg_flags


def test_linux_injector_selection():
    print("Linux injector chain selection (mocked probes):")
    import sotto.inject_linux as il
    orig_which, orig_probe, orig_session = il.shutil.which, il._probe, il.session_type

    def which_of(avail):
        return lambda name, *a, **k: f"/usr/bin/{name}" if name in avail else None

    try:
        il.session_type = lambda: "x11"
        il.shutil.which = which_of({"xdotool", "xclip"})
        il._probe = lambda cmd, env=None: True
        names = [i.name for i in il.build_injector()._injectors]
        check("X11 → xdotool, clipboard fallback", names == ["xdotool", "clipboard"], str(names))

        il.session_type = lambda: "wayland"
        il.shutil.which = which_of({"wtype", "ydotool", "wl-copy"})
        names = [i.name for i in il.build_injector()._injectors]
        check("Wayland → wtype first", names[0] == "wtype", str(names))

        il.shutil.which = which_of({"ydotool", "wl-copy"})  # GNOME: no wtype
        names = [i.name for i in il.build_injector()._injectors]
        check("Wayland without wtype → ydotool", names == ["ydotool", "clipboard"], str(names))

        il._probe = lambda cmd, env=None: False  # wtype present but compositor rejects it
        il.shutil.which = which_of({"wtype", "wl-copy"})
        names = [i.name for i in il.build_injector()._injectors]
        check("failed probe skips the tool", names == ["clipboard"], str(names))

        # runtime fall-through: first injector raises → chain advances
        class Boom:
            name = "boom"
            def type_text(self, *a): raise RuntimeError("nope")
        class Ok:
            name = "ok"
            def __init__(self): self.got = None
            def type_text(self, text, interval): self.got = text
        ok = Ok()
        chain = il._Chain([Boom(), ok])
        chain.type_text("hello", 0.0)
        check("runtime failure falls through", ok.got == "hello", repr(ok.got))
    finally:
        il.shutil.which, il._probe, il.session_type = orig_which, orig_probe, orig_session


def test_deb_layout():
    print("Linux .deb manifest (FHS paths + modes, macOS-checkable):")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    manifest = os.path.join(root, "linuxapp", "deb", "manifest.txt")
    rows = []
    for line in open(manifest):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        mode, src, dest = line.split()
        rows.append((mode, src, dest))
    by_dest = {d: (m, s) for m, s, d in rows}

    check("every manifest source file exists",
          all(os.path.exists(os.path.join(root, s)) for _, s, _ in rows),
          str([s for _, s, _ in rows if not os.path.exists(os.path.join(root, s))]))
    # the security-critical one: the root helper must be 0755 (world-writable
    # would turn the pinned polkit action into a local root escalation)
    helper = by_dest.get("usr/libexec/sotto/sotto-perms")
    check("sotto-perms installs at usr/libexec/sotto/sotto-perms mode 0755",
          helper is not None and helper[0] == "0755", str(helper))
    check("launcher installs at usr/bin/sotto mode 0755",
          by_dest.get("usr/bin/sotto", ("",))[0] == "0755")
    # L8: the second root helper + the release pubkey it verifies against
    inst = by_dest.get("usr/libexec/sotto/sotto-install-update")
    check("sotto-install-update installs 0755 (same escalation rule as sotto-perms)",
          inst is not None and inst[0] == "0755", str(inst))
    pub = by_dest.get("usr/share/sotto/sotto-release.pub")
    check("release pubkey installs 0644 at usr/share/sotto",
          pub is not None and pub[0] == "0644", str(pub))
    for dest in ("usr/lib/udev/rules.d/60-sotto-input.rules",
                 "usr/share/polkit-1/actions/io.github.psancheti6666.sotto.policy",
                 "usr/lib/modules-load.d/sotto-uinput.conf",
                 "usr/share/applications/sotto.desktop"):
        check(f"manifest includes {dest}", dest in by_dest)

    # the launcher MUST export SOTTO_BUNDLE=deb or the whole L5 gate is dormant
    launcher = open(os.path.join(root, "linuxapp", "deb", "sotto-launcher")).read()
    check("launcher exports SOTTO_BUNDLE=deb",
          "SOTTO_BUNDLE=deb" in launcher and "exec /opt/sotto/sotto" in launcher)
    # the polkit exec.path must point at where the manifest installs the helper
    policy = open(os.path.join(root, "linuxapp", "deb",
                               "io.github.psancheti6666.sotto.policy")).read()
    check("polkit exec.path matches the helper's install dest",
          "/usr/libexec/sotto/sotto-perms" in policy)
    check("install action pins the install-update helper's path",
          "/usr/libexec/sotto/sotto-install-update" in policy
          and "io.github.psancheti6666.sotto.install" in policy)
    check("install action requires a fresh auth every time (no _keep)",
          "auth_admin_keep" not in policy.split("sotto.install")[1])
    # the helper must verify against the same pubkey path the manifest installs
    helper_src = open(os.path.join(root, "linuxapp", "deb",
                                   "sotto-install-update")).read()
    check("helper verifies against the packaged pubkey path",
          "/usr/share/sotto/sotto-release.pub" in helper_src)
    check("helper refuses non-sotto packages and downgrades",
          '"$PKG" = "sotto"' in helper_src
          and "compare-versions" in helper_src)
    # and the committed pubkey must be a real RSA public key
    pub_pem = open(os.path.join(root, "linuxapp", "deb",
                                "sotto-release.pub")).read()
    check("committed release pubkey is a PEM public key (no private material)",
          "BEGIN PUBLIC KEY" in pub_pem and "PRIVATE" not in pub_pem)
    # control.in has the version placeholder make_deb.sh substitutes
    control = open(os.path.join(root, "linuxapp", "deb", "control.in")).read()
    check("control.in carries @VERSION@ and Package: sotto",
          "@VERSION@" in control and "Package: sotto" in control
          and "Architecture: amd64" in control)
    check("control Depends includes acl (setfacl/getfacl) and a polkit provider",
          "acl" in control and ("pkexec" in control or "policykit" in control))
    check("control Depends includes the WebKit introspection (L11 Insights "
          "window, 4.1 with 4.0 fallback)",
          "gir1.2-webkit2-4.1 | gir1.2-webkit2-4.0" in control)


def test_tray_menu():
    print("Linux tray (pystray, best-effort) — menu gating + quit path:")
    import logging
    import signal

    from sotto import tray_linux as tl

    items = tl._menu_items(True, False)
    check("dashboard up, no updater: Insights then Quit",
          items == [("Insights", "insights"), ("Quit Sotto", "quit")],
          str(items))
    items = tl._menu_items(False, False)
    check("no dashboard: Quit only",
          items == [("Quit Sotto", "quit")], str(items))
    items = tl._menu_items(True, True)
    check("updater armed (L8): Check for Updates… appears before Quit",
          [label for label, _ in items] ==
          ["Insights", "Check for Updates…", "Quit Sotto"], str(items))
    check("Quit is always last",
          all(tl._menu_items(i, u)[-1] == ("Quit Sotto", "quit")
              for i in (False, True) for u in (False, True)))

    # pins the L7 spec: no updates item until L8's Linux backend flips this
    from sotto import update
    check("update.enabled() is False outside the mac release bundle",
          update.enabled() is False)

    # Quit = SIGINT to self — the existing Ctrl+C shutdown path
    sent = []
    orig_kill = tl.os.kill
    tl.os.kill = lambda pid, sig: sent.append((pid, sig))
    try:
        tl._quit()
    finally:
        tl.os.kill = orig_kill
    check("tray Quit delivers SIGINT to our own pid",
          sent == [(os.getpid(), signal.SIGINT)], str(sent))

    # best-effort contract: an unavailable pystray stack must mean a clean
    # thread exit and one log line — never a raise, never a hang. Pin the
    # unavailable branch deterministically: a real pystray import would
    # START a live tray (blocking X/AppKit loop, visible icon) on any
    # machine where the stack works — a checkout with a display, or a mac
    # venv with pystray pip-installed — so block the import outright.
    records = []
    handler = logging.Handler()
    handler.emit = lambda r: records.append(r.getMessage())
    tl.log.addHandler(handler)
    orig_level = tl.log.level
    tl.log.setLevel(logging.INFO)  # the fallback line is INFO; root sits at WARNING
    orig_pystray = sys.modules.get("pystray")
    sys.modules["pystray"] = None  # import pystray → ImportError
    try:
        t = tl.start(dashboard_port=8377)
        t.join(timeout=10)
        check("tray thread exits cleanly when the stack is unavailable",
              not t.is_alive())
        check("tray-less fallback logs the 'tray unavailable' line",
              any("tray unavailable" in m for m in records), str(records))
    finally:
        if orig_pystray is None:
            sys.modules.pop("pystray", None)
        else:
            sys.modules["pystray"] = orig_pystray
        tl.log.setLevel(orig_level)
        tl.log.removeHandler(handler)

    # Insights wiring: the tray action goes through insights_linux.show_soon
    # (native window with the browser fallback INSIDE it), not straight to a
    # browser tab. Functional fake pystray so _tray_thread runs to completion
    # on macOS; _icon_image stubbed (PIL isn't in the mac venv).
    import types

    class FakeMenuItem:
        def __init__(self, label, action, default=False):
            self.label, self.action, self.default = label, action, default

    class FakeMenu:
        def __init__(self, *items):
            self.items = items

    class FakeIcon:
        HAS_MENU = True
        last = None

        def __init__(self, name, image, title, menu):
            FakeIcon.last = self
            self.menu = menu

        def run(self):
            pass

    fake = types.ModuleType("pystray")
    fake.MenuItem, fake.Menu, fake.Icon = FakeMenuItem, FakeMenu, FakeIcon
    from sotto import dashboard, insights_linux
    shown, opened = [], []
    orig_pystray = sys.modules.get("pystray")
    orig_icon_image, orig_show = tl._icon_image, insights_linux.show_soon
    orig_open, orig_win = dashboard.open_in_browser, tl.IS_WINDOWS
    sys.modules["pystray"] = fake
    tl._icon_image = lambda: None
    insights_linux.show_soon = lambda: shown.append(1)
    dashboard.open_in_browser = lambda port: opened.append(port)
    try:
        tl.IS_WINDOWS = False  # platform forced — this block pins Linux
        tl._tray_thread(8377)  # fake icon.run() returns immediately
        items = FakeIcon.last.menu.items
        insights_item = next(i for i in items if i.label == "Insights")
        insights_item.action()
        check("Linux tray Insights routes through insights_linux.show_soon",
              shown == [1] and opened == [])
        check("Insights stays the left-click default action",
              insights_item.default
              and not any(i.default for i in items if i.label != "Insights"))

        tl.IS_WINDOWS = True  # Windows: browser tab until W8's WebView2
        tl._tray_thread(8377)
        next(i for i in FakeIcon.last.menu.items
             if i.label == "Insights").action()
        check("Windows tray Insights opens the dashboard in the browser "
              "(until W8)", opened == [8377] and shown == [1], str(opened))
    finally:
        if orig_pystray is None:
            sys.modules.pop("pystray", None)
        else:
            sys.modules["pystray"] = orig_pystray
        tl._icon_image = orig_icon_image
        insights_linux.show_soon = orig_show
        dashboard.open_in_browser = orig_open
        tl.IS_WINDOWS = orig_win

    # Windows quit path (W6): never SIGINT — overlay command path when a tk
    # loop exists, engine shutdown + hard exit when headless
    from sotto import llm_server, overlay_tk
    events = []
    orig_rq, orig_sd = overlay_tk.request_quit, llm_server.shutdown
    orig_exit, orig_kill2 = tl.os._exit, tl.os.kill
    overlay_tk.request_quit = lambda: events.append("rq") or True
    llm_server.shutdown = lambda: events.append("shutdown")
    tl.os._exit = lambda code: events.append(("exit", code))
    tl.os.kill = lambda pid, sig: events.append("SIGINT")
    try:
        tl.IS_WINDOWS = True
        tl._quit()
        check("Windows + overlay: quit rides the tk command path "
              "(no SIGINT, no hard exit)", events == ["rq"], str(events))
        events.clear()
        overlay_tk.request_quit = lambda: False  # headless
        tl._quit()
        check("Windows headless: engine shut down, then hard exit",
              events == ["shutdown", ("exit", 0)], str(events))
        events.clear()
        tl.IS_WINDOWS = False
        tl._quit()
        check("Linux quit keeps the SIGINT contract",
              events == ["SIGINT"], str(events))
    finally:
        overlay_tk.request_quit, llm_server.shutdown = orig_rq, orig_sd
        tl.os._exit, tl.os.kill = orig_exit, orig_kill2
        tl.IS_WINDOWS = orig_win

    # overlay_tk.request_quit mechanism (fake root — no display needed)
    orig_root = overlay_tk._root
    try:
        overlay_tk._root = None
        overlay_tk._quit_requested.clear()
        check("headless: request_quit reports no overlay",
              overlay_tk.request_quit() is False
              and not overlay_tk._quit_requested.is_set())

        destroyed = []
        overlay_tk._root = type("R", (), {
            "destroy": lambda self: destroyed.append(1)})()
        check("live overlay: request_quit arms the flag",
              overlay_tk.request_quit() is True
              and overlay_tk._quit_requested.is_set())
        check("tick consumes the quit: root destroyed, tick stops",
              overlay_tk._consume_quit() is True and destroyed == [1])
        overlay_tk._quit_requested.clear()
        check("no pending quit → tick proceeds normally",
              overlay_tk._consume_quit() is False and destroyed == [1])
    finally:
        overlay_tk._root = orig_root
        overlay_tk._quit_requested.clear()


def test_vm_round_fixes():
    print("VM-validation-round fixes (issue #63):")
    import logging

    # --- clean_env: restore PyInstaller's *_ORIG, drop the overrides
    from sotto.platform import linux as pl
    saved = {k: os.environ.get(k) for k in
             ("LD_LIBRARY_PATH", "LD_LIBRARY_PATH_ORIG", "LD_PRELOAD")}
    try:
        os.environ["LD_LIBRARY_PATH"] = "/bundle/_internal"
        os.environ["LD_LIBRARY_PATH_ORIG"] = "/usr/lib/custom"
        os.environ.pop("LD_PRELOAD", None)
        env = pl.clean_env()
        check("clean_env restores the pre-bundle LD_LIBRARY_PATH",
              env.get("LD_LIBRARY_PATH") == "/usr/lib/custom"
              and "LD_LIBRARY_PATH_ORIG" not in env, str(env.get("LD_LIBRARY_PATH")))
        del os.environ["LD_LIBRARY_PATH_ORIG"]
        env = pl.clean_env()
        check("clean_env drops the override when no _ORIG exists",
              "LD_LIBRARY_PATH" not in env)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # --- open_url: xdg-open with the sanitized env
    calls = []
    orig_which, orig_popen = pl.shutil.which, pl.subprocess.Popen
    pl.shutil.which = lambda n: "/usr/bin/xdg-open" if n == "xdg-open" else None
    pl.subprocess.Popen = lambda argv, **kw: calls.append((argv, kw)) or None
    try:
        pl.open_url("http://127.0.0.1:1")
        check("open_url uses xdg-open with a cleaned env",
              calls and calls[0][0][0] == "xdg-open"
              and "env" in calls[0][1], str(calls))
    finally:
        pl.shutil.which, pl.subprocess.Popen = orig_which, orig_popen

    # --- injection chain logs once, not once per probe tick
    from sotto import inject_linux as il
    records = []
    handler = logging.Handler()
    handler.emit = lambda r: records.append(r.getMessage())
    il.log.addHandler(handler)
    orig_level = il.log.level
    il.log.setLevel(logging.INFO)
    il._last_chain = None
    orig_ilwhich, orig_session = il.shutil.which, il.session_type
    il.shutil.which = lambda n: None
    il.session_type = lambda: "x11"
    try:
        il.build_injector()
        il.build_injector()
        chain_logs = [m for m in records if "injection chain" in m]
        check("identical chain logged exactly once across repeated probes",
              len(chain_logs) == 1, str(chain_logs))
        il.shutil.which = lambda n: "/usr/bin/" + n if n == "xdotool" else None
        il.build_injector()
        chain_logs = [m for m in records if "injection chain" in m]
        check("a CHANGED chain is logged again",
              len(chain_logs) == 2, str(chain_logs))
    finally:
        il.shutil.which, il.session_type = orig_ilwhich, orig_session
        il.log.setLevel(orig_level)
        il.log.removeHandler(handler)
        il._last_chain = None

    # --- offline-first ASR load
    from sotto import asr_onnx

    class FakeHub:
        def __init__(self, offline_fails):
            self.offline_fails = offline_fails
            self.calls = []
        def load_model(self, mid, **kw):
            offline = os.environ.get("HF_HUB_OFFLINE")
            self.calls.append(offline)
            if offline == "1" and self.offline_fails:
                raise RuntimeError("not in cache")
            return "MODEL"

    saved_off = os.environ.pop("HF_HUB_OFFLINE", None)
    try:
        hub = FakeHub(offline_fails=False)
        m = asr_onnx._load_offline_first(hub, "m", "")
        check("cached model loads OFFLINE (probe saw HF_HUB_OFFLINE=1)",
              m == "MODEL" and hub.calls == ["1"], str(hub.calls))
        check("the injected HF_HUB_OFFLINE never outlives the load "
              "(children must not inherit it)",
              os.environ.get("HF_HUB_OFFLINE") is None)
        hub = FakeHub(offline_fails=True)
        m = asr_onnx._load_offline_first(hub, "m", "")
        check("cache miss falls back to an ONLINE load",
              m == "MODEL" and hub.calls == ["1", None], str(hub.calls))
        check("HF_HUB_OFFLINE cleaned up after the online fallback too",
              os.environ.get("HF_HUB_OFFLINE") is None)
        os.environ["HF_HUB_OFFLINE"] = "0"
        hub = FakeHub(offline_fails=False)
        asr_onnx._load_offline_first(hub, "m", "")
        check("user-set HF_HUB_OFFLINE is respected, not overridden",
              hub.calls == ["0"] and os.environ.get("HF_HUB_OFFLINE") == "0",
              str(hub.calls))
    finally:
        os.environ.pop("HF_HUB_OFFLINE", None)
        if saved_off is not None:
            os.environ["HF_HUB_OFFLINE"] = saved_off

    # --- single-instance lock (path socket in XDG_RUNTIME_DIR)
    from sotto import app as app_mod

    class FakeSock:
        def __init__(self, mod):
            self.mod, self.closed, self.listening = mod, False, False
        def bind(self, path):
            if path in self.mod.bound:      # a live holder occupies it
                raise OSError(98, "address in use")
            self.mod.bound.add(path)
            self.bound_path = path
        def listen(self, n):
            self.listening = True
        def connect(self, path):
            if path not in self.mod.listening_paths:
                raise OSError(111, "connection refused")
        def close(self):
            self.closed = True

    class FakeSocketMod:
        AF_UNIX = SOCK_STREAM = 0
        def __init__(self, bound=(), listening=()):
            self.bound = set(bound)
            self.listening_paths = set(listening)
            self.socks = []
        def socket(self, *a):
            s = FakeSock(self)
            self.socks.append(s)
            return s

    saved_rt = os.environ.get("XDG_RUNTIME_DIR")
    os.environ["XDG_RUNTIME_DIR"] = "/run/user/test"
    try:
        free = FakeSocketMod()
        lock = app_mod._acquire_instance_lock(free)
        check("first instance binds+listens the lock, held not closed",
              lock is free.socks[0] and lock.listening and not lock.closed)
        # a LIVE holder: path bound AND someone listening → refuse
        busy = FakeSocketMod(bound=["/run/user/test/sotto.lock"],
                             listening=["/run/user/test/sotto.lock"])
        orig_unlink = os.unlink
        os.unlink = lambda p: (_ for _ in ()).throw(AssertionError(
            "must NOT unlink a live holder's socket"))
        try:
            lock = app_mod._acquire_instance_lock(busy)
            check("second live instance refused (None), its socket closed",
                  lock is None and busy.socks[-1].closed)
        finally:
            os.unlink = orig_unlink
        # a STALE file: path 'bound' but nobody listening → reclaim
        unlinked = []
        stale = FakeSocketMod(bound=["/run/user/test/sotto.lock"])
        os.unlink = lambda p: (unlinked.append(p),
                               stale.bound.discard(p))
        try:
            lock = app_mod._acquire_instance_lock(stale)
            check("stale lock file is reclaimed (unlinked, then bound)",
                  lock is not None and unlinked == ["/run/user/test/sotto.lock"]
                  and lock.listening)
        finally:
            os.unlink = orig_unlink
    finally:
        if saved_rt is None:
            os.environ.pop("XDG_RUNTIME_DIR", None)
        else:
            os.environ["XDG_RUNTIME_DIR"] = saved_rt

    if sys.platform == "win32":
        # the real named-mutex path (W5): a genuine handle, held for the
        # process — and this suite IS a single instance, so acquisition
        # must succeed (refusal/fail-open covered by the fake-kernel32
        # tests in test_firstrun_windows)
        check("windows acquires the real mutex",
              bool(app_mod._acquire_instance_lock()))
    elif not sys.platform.startswith("linux"):
        check("non-Linux platforms get a no-op token",
              app_mod._acquire_instance_lock() is True)


def test_smoke_imports():
    print("Linux build smoke list stays in sync with the runtime selectors:")
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "linuxapp", "sotto_linux.py")
    spec = importlib.util.spec_from_file_location("sotto_linux_entry", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # imports sys only — safe on any OS

    # everything app.py's selectors (_make_listener, _overlay_module,
    # make_asr, inject router) or firstrun/llm/update reach lazily on Linux
    required = {
        "tkinter", "evdev", "sounddevice", "onnx_asr", "onnxruntime",
        "huggingface_hub",
        "sotto.app", "sotto.asr", "sotto.asr_onnx", "sotto.audio",
        "sotto.hotkey_evdev", "sotto.inject", "sotto.inject_linux",
        "sotto.overlay_tk", "sotto.platform.linux",
        "sotto.firstrun", "sotto.firstrun_linux", "sotto.firstrun_tk",
        "sotto.llm_server", "sotto.ollama_runtime",
        "sotto.update", "sotto.update_linux", "sotto.dashboard", "zstandard",
        "sotto.tray_linux", "sotto.insights_linux",
    }
    missing = required - set(mod.SMOKE_IMPORTS)
    check("smoke list covers every runtime-selected module", not missing,
          f"missing: {sorted(missing)}")
    # pystray is presence-checked, not imported: its import runs backend
    # auto-selection into gi/Gtk, which legitimately fails without the deb's
    # gir/gtk packages (tray_linux catches that at runtime — best-effort)
    check("pystray is presence-checked in the bundle",
          "pystray" in mod.SMOKE_FIND)
    check("smoke() reports a recognizable OK line",
          "smoke OK" in open(path).read(), "")


def test_linux_alert():
    print("Linux alert dispatch (zenity → kdialog → notify-send → log):")
    from sotto.platform import linux as pl

    def which_of(*names):
        return lambda cmd: f"/usr/bin/{cmd}" if cmd in names else None

    argv = pl._alert_argv("T", "hello", which_of("zenity", "kdialog", "notify-send"))
    check("zenity preferred, title/text/no-markup pinned",
          argv[0] == "zenity" and "--title=T" in argv and "--text=hello" in argv
          and "--no-markup" in argv, str(argv))
    argv = pl._alert_argv("T", "hello", which_of("kdialog", "notify-send"))
    check("kdialog next, --sorry carries the text",
          argv[0] == "kdialog" and argv[argv.index("--sorry") + 1] == "hello",
          str(argv))
    argv = pl._alert_argv("T", "hello", which_of("notify-send"))
    check("notify-send last resort: critical urgency, -- before positionals",
          argv[0] == "notify-send" and "critical" in argv
          and argv[argv.index("--") + 1:] == ["T", "hello"], str(argv))
    check("None when no tool exists", pl._alert_argv("T", "x", which_of()) is None, "")

    spawned = []
    orig_which, orig_popen = pl.shutil.which, pl.subprocess.Popen
    pl.shutil.which = which_of("zenity")
    pl.subprocess.Popen = lambda argv, **kw: spawned.append(argv)
    try:
        pl.alert("Sotto", "boom")
        check("alert spawns the chosen dialog",
              len(spawned) == 1 and spawned[0][0] == "zenity", str(spawned))
        pl.shutil.which = which_of()
        spawned.clear()
        pl.alert("Sotto", "boom")  # no tool → log-only, must not raise
        check("no tools → log-only, nothing spawned", spawned == [], str(spawned))
        pl.shutil.which = which_of("zenity")

        def exploding(argv, **kw):
            raise OSError("spawn failed")
        pl.subprocess.Popen = exploding
        pl.alert("Sotto", "boom")  # spawn failure → logged, must not raise
        check("spawn failure is swallowed and logged", True, "")
    finally:
        pl.shutil.which, pl.subprocess.Popen = orig_which, orig_popen


def test_history():
    print("history persistence (JSONL round-trip):")
    from sotto import history
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "history.jsonl")
        a = {"ts": "2026-07-16T10:00:00+05:30", "text": "hello world", "words": 2,
             "duration_s": 1.5, "app": "com.apple.Notes"}
        b = {"ts": "2026-07-16T10:01:00+05:30", "text": "second one — with unicode ✓",
             "words": 3, "duration_s": 2.0, "app": "slack"}
        history.append_entry(a, path)
        history.append_entry(b, path)
        got = history.read_entries(path)
        check("round-trip, oldest first", got == [a, b], str(got))
        with open(path, "a") as f:
            f.write("{corrupt json\n\n")
        history.append_entry(a, path)
        got = history.read_entries(path)
        check("corrupt/blank lines skipped", got == [a, b, a], f"{len(got)} entries")
    check("missing file → []", history.read_entries("/nonexistent/h.jsonl") == [])
    history.append_entry(a, "/nonexistent/dir/h.jsonl")  # must warn, never raise
    check("unwritable path only warns", True)


def test_stats():
    print("history stats (WPM, totals, per-day buckets):")
    from datetime import date
    from sotto.history import compute_stats
    today = date(2026, 7, 16)
    entries = [
        {"ts": "2026-07-16T10:00:00+05:30", "words": 100, "duration_s": 60.0},
        {"ts": "2026-07-15T09:00:00+05:30", "words": 20, "duration_s": 30.0},
        # outside the 14-day chart window: counts toward totals only
        {"ts": "2026-06-01T09:00:00+05:30", "words": 50, "duration_s": 60.0},
    ]
    s = compute_stats(entries, today=today)
    check("total words", s["total_words"] == 170, str(s["total_words"]))
    check("total dictations", s["total_dictations"] == 3)
    check("avg wpm = words / audio minutes", s["avg_wpm"] == 68.0, str(s["avg_wpm"]))
    check("time saved vs 40wpm typing", s["time_saved_min"] == 1.8, str(s["time_saved_min"]))
    days = s["words_per_day"]
    check("14 zero-filled day buckets", len(days) == 14 and days[0]["words"] == 0, str(len(days)))
    check("today bucketed", days[-1] == {"date": "2026-07-16", "words": 100}, str(days[-1]))
    check("yesterday bucketed", days[-2] == {"date": "2026-07-15", "words": 20}, str(days[-2]))
    check("today's words", s["today_words"] == 100, str(s["today_words"]))
    check("streak counts consecutive days", s["streak_days"] == 2, str(s["streak_days"]))
    # streak survives when today has no dictations yet
    s2 = compute_stats(entries[1:], today=today)
    check("streak not broken before dictating today", s2["streak_days"] == 1,
          str(s2["streak_days"]))
    apps = compute_stats([
        {"ts": "2026-07-16T10:00:00", "words": 5, "duration_s": 1, "app": "slack"},
        {"ts": "2026-07-16T10:01:00", "words": 9, "duration_s": 1, "app": "code"},
        {"ts": "2026-07-16T10:02:00", "words": 4, "duration_s": 1, "app": "code"},
    ], today=today)["top_apps"]
    check("top apps by words", [a["app"] for a in apps] == ["code", "slack"]
          and apps[0] == {"app": "code", "words": 13, "count": 2}, str(apps))
    empty = compute_stats([], today=today)
    check("empty history → zeros, no div-by-zero",
          empty["avg_wpm"] == 0.0 and empty["total_words"] == 0
          and empty["streak_days"] == 0 and empty["top_apps"] == [])


def test_dashboard():
    print("dashboard server (127.0.0.1, page + history API):")
    import json as jsonmod
    import urllib.error
    import urllib.request
    from sotto import dashboard, history
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "history.jsonl")
        history.append_entry({"ts": "2026-07-16T10:00:00+05:30", "text": "first",
                              "words": 1, "duration_s": 1.0, "app": "x"}, path)
        history.append_entry({"ts": "2026-07-16T10:05:00+05:30", "text": "second",
                              "words": 1, "duration_s": 1.0, "app": "x"}, path)
        dict_path = os.path.join(td, "dictionary.txt")
        with open(dict_path, "w") as f:
            f.write("# names\nAnthropic\n")
        server = dashboard.start(0, history_path=path,  # port 0 = ephemeral
                                 dictionary_path=dict_path)
        check("server started", server is not None)
        base = f"http://127.0.0.1:{server.server_address[1]}"

        def post(body, headers=None):
            req = urllib.request.Request(
                base + "/api/dictionary", data=jsonmod.dumps(body).encode(),
                headers={"Content-Type": "application/json", **(headers or {})})
            return jsonmod.loads(urllib.request.urlopen(req, timeout=5).read())

        try:
            page = urllib.request.urlopen(base + "/", timeout=5).read().decode()
            check("page served", "sotto" in page and "/api/history" in page)
            data = jsonmod.loads(urllib.request.urlopen(base + "/api/history",
                                                        timeout=5).read())
            texts = [e["text"] for e in data["entries"]]
            check("entries newest first", texts == ["second", "first"], str(texts))
            check("stats included", data["stats"]["total_words"] == 2,
                  str(data["stats"]))
            check("meta has user + host", "user" in data["meta"] and "host" in data["meta"],
                  str(data["meta"]))
            terms = jsonmod.loads(urllib.request.urlopen(
                base + "/api/dictionary", timeout=5).read())["terms"]
            check("dictionary read (comments skipped)", terms == ["Anthropic"], str(terms))
            got = post({"add": "Kubernetes"}, {"X-Sotto": "1"})
            check("dictionary add", got["terms"] == ["Anthropic", "Kubernetes"],
                  str(got))
            got = post({"add": "kubernetes"}, {"X-Sotto": "1"})
            check("duplicate add is a no-op", got["terms"] == ["Anthropic", "Kubernetes"],
                  str(got))
            got = post({"remove": "Anthropic"}, {"X-Sotto": "1"})
            check("dictionary remove", got["terms"] == ["Kubernetes"], str(got))
            with open(dict_path) as f:
                check("comment lines survive edits", f.read().startswith("# names"),
                      open(dict_path).read())
            try:
                post({"add": "Evil"})
                check("mutation without X-Sotto → 403", False, "no error raised")
            except urllib.error.HTTPError as e:
                check("mutation without X-Sotto → 403", e.code == 403, str(e.code))
            try:
                urllib.request.urlopen(base + "/nope", timeout=5)
                check("unknown path → 404", False, "no error raised")
            except urllib.error.HTTPError as e:
                check("unknown path → 404", e.code == 404, str(e.code))
        finally:
            server.shutdown()
            server.server_close()


def test_asr_long():
    print("ASR long-form chunking (tiled speech, forced multi-chunk):")
    from sotto.asr_mlx import ParakeetASR
    import numpy as np
    asr = ParakeetASR()
    sentence = ("The first city is Amsterdam. The second city is Barcelona. "
                "The third city is Chicago. The fourth city is Denver.")
    clip = synthesize(sentence)
    gap = np.zeros(8000, dtype=np.float32)  # 0.5 s pause between repeats
    reps = max(16, int(140 * 16000 / (clip.size + gap.size)) + 1)
    audio = np.concatenate([np.concatenate([clip, gap]) for _ in range(reps)])
    t0 = time.perf_counter()
    out = asr.transcribe(audio, chunk_duration=60.0, overlap_duration=10.0)
    dt = time.perf_counter() - t0
    print(f"  {audio.size/16000:.0f}s of audio, {reps} repeats → transcribed in {dt:.1f}s")
    for city in ("Amsterdam", "Barcelona", "Chicago", "Denver"):
        n = out.count(city)
        check(f"{city} present in every repeat ({n}/{reps})", n >= reps - 1, out[:200])


if __name__ == "__main__":
    args = sys.argv[1:]
    run_all = "--all" in args
    test_regex()
    test_dictionary()
    test_recorder_truncation()
    test_force_stop()
    test_evdev_gestures()
    test_evdev_permission_detection()
    test_firstrun_linux()
    test_tk_firstrun_windows()
    test_platform_detection()
    test_linux_injector_selection()
    test_linux_alert()
    test_deb_layout()
    test_vm_round_fixes()
    test_tray_menu()
    test_smoke_imports()
    test_history()
    test_stats()
    test_dashboard()
    test_llm_fallback()
    test_llm_server()
    test_ollama_runtime()
    test_firstrun()
    test_insights_config()
    test_win32_filter()
    test_windows_platform()
    test_win_injector()
    test_firstrun_windows()
    test_insights_linux()
    test_listener_retry()
    test_logging_setup()
    test_firstrun_cosmetics()
    test_permission_watchdog()
    test_firstrun_gating()
    test_firstrun_notifications()
    test_update()
    test_update_linux()
    test_appimage_bootstrap()
    if run_all or "--llm" in args:
        test_llm()
    if run_all or "--asr" in args:
        test_asr()
    if run_all or "--long" in args:
        test_asr_long()
    if run_all or "--asr-onnx" in args:
        test_asr_onnx()
    print(f"\n{'ALL PASS' if failures == 0 else f'{failures} FAILURE(S)'}")
    sys.exit(1 if failures else 0)
