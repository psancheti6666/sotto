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
    from sotto import llm_server
    from sotto.config import Config

    had = os.environ.pop("RESOURCEPATH", None)
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
        if had is not None:
            os.environ["RESOURCEPATH"] = had


def test_ollama_runtime():
    print("Linux ollama runtime resolution + download logic:")
    import hashlib
    from sotto import ollama_runtime as orr

    orig_which, orig_dir = orr.shutil.which, orr.RUNTIME_DIR
    try:
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
            check("non-executable download ignored", orr.installed() is None)
            os.chmod(binpath, 0o755)
            check("downloaded runtime found", orr.resolve() == binpath)
    finally:
        orr.shutil.which, orr.RUNTIME_DIR = orig_which, orig_dir

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

    # download(): orchestration with fetch/extract stubbed
    orig_fetch, orig_extract = orr._fetch, orr._extract
    try:
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
                  manifest.endswith("library/qwen3/4b-instruct"), manifest)

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

    def release(tag, assets=("apple-silicon", "intel"), **kw):
        return {"tag_name": tag,
                "assets": [{"name": f"Sotto-{tag.lstrip('v')}-{a}.dmg",
                            "browser_download_url": f"https://x/{a}.dmg"}
                           for a in assets], **kw}

    info = update.evaluate(release("v0.4.0"), "0.3.0", "arm64")
    check("newer release found", bool(info) and info["version"] == "0.4.0",
          repr(info))
    check("apple-silicon asset picked",
          bool(info) and info["url"].endswith("apple-silicon.dmg"))
    info = update.evaluate(release("v0.4.0"), "0.3.0", "x86_64")
    check("intel asset picked", bool(info) and info["url"].endswith("intel.dmg"))
    check("same version → no update",
          update.evaluate(release("v0.3.0"), "0.3.0", "arm64") is None)
    check("older version → no update",
          update.evaluate(release("v0.2.9"), "0.3.0", "arm64") is None)
    check("0.10 beats 0.9 (numeric, not lexical)",
          update.evaluate(release("v0.10.0"), "0.9.0", "arm64") is not None)
    check("draft ignored",
          update.evaluate(release("v9.9.9", draft=True), "0.3.0", "arm64") is None)
    check("prerelease ignored",
          update.evaluate(release("v9.9.9", prerelease=True), "0.3.0", "arm64") is None)
    check("missing arch asset → no update",
          update.evaluate(release("v0.4.0", assets=("intel",)), "0.3.0", "arm64") is None)

    with tempfile.TemporaryDirectory() as td:
        state = os.path.join(td, "update-state.json")
        check("due when never checked", update.due(state, 1))
        update.mark_checked(state, now=1000.0)
        check("not due right after a check",
              not update.due(state, 1, now=1000.0 + 3600))
        check("due once the interval passes",
              update.due(state, 1, now=1000.0 + 86401))


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
    if sys.platform != "darwin":
        print("  (skipped — HotkeyListener needs pynput, which is macOS-only;"
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

    orig_cfg_flag, orig_cfg_path = sc.IS_LINUX, sc.CONFIG_PATH
    try:
        sc.IS_LINUX, sc.CONFIG_PATH = True, "/nonexistent/config.toml"
        cfg = sc.load_config()
        check("Linux default hotkey is ctrl_r", cfg.hotkey == "ctrl_r", cfg.hotkey)
        check("Linux sounds use freedesktop names", cfg.done_sound == "complete", cfg.done_sound)
        check("Linux haptics off", cfg.haptics is False)
        check("Linux terminals are keystroke apps", "konsole" in cfg.keystroke_apps,
              str(cfg.keystroke_apps))
        sc.IS_LINUX = False
        cfg = sc.load_config()
        check("macOS defaults untouched", cfg.hotkey == "fn" and cfg.done_sound == "Morse",
              f"{cfg.hotkey}/{cfg.done_sound}")
    finally:
        sc.IS_LINUX, sc.CONFIG_PATH = orig_cfg_flag, orig_cfg_path


def test_linux_injector_selection():
    print("Linux injector chain selection (mocked probes):")
    import sotto.inject_linux as il
    orig_which, orig_probe, orig_session = il.shutil.which, il._probe, il.session_type

    def which_of(avail):
        return lambda name, *a, **k: f"/usr/bin/{name}" if name in avail else None

    try:
        il.session_type = lambda: "x11"
        il.shutil.which = which_of({"xdotool", "xclip"})
        il._probe = lambda cmd: True
        names = [i.name for i in il.build_injector()._injectors]
        check("X11 → xdotool, clipboard fallback", names == ["xdotool", "clipboard"], str(names))

        il.session_type = lambda: "wayland"
        il.shutil.which = which_of({"wtype", "ydotool", "wl-copy"})
        names = [i.name for i in il.build_injector()._injectors]
        check("Wayland → wtype first", names[0] == "wtype", str(names))

        il.shutil.which = which_of({"ydotool", "wl-copy"})  # GNOME: no wtype
        names = [i.name for i in il.build_injector()._injectors]
        check("Wayland without wtype → ydotool", names == ["ydotool", "clipboard"], str(names))

        il._probe = lambda cmd: False  # wtype present but compositor rejects it
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
        "sotto.firstrun", "sotto.llm_server", "sotto.ollama_runtime",
        "sotto.update", "sotto.dashboard", "zstandard",
    }
    missing = required - set(mod.SMOKE_IMPORTS)
    check("smoke list covers every runtime-selected module", not missing,
          f"missing: {sorted(missing)}")
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
    test_platform_detection()
    test_linux_injector_selection()
    test_linux_alert()
    test_smoke_imports()
    test_history()
    test_stats()
    test_dashboard()
    test_llm_fallback()
    test_llm_server()
    test_ollama_runtime()
    test_firstrun()
    test_insights_config()
    test_listener_retry()
    test_logging_setup()
    test_firstrun_cosmetics()
    test_permission_watchdog()
    test_firstrun_gating()
    test_firstrun_notifications()
    test_update()
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
