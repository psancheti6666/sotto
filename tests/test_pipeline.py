"""Pipeline verification, runnable headless (no mic/hotkey/permissions needed).

Usage:
  .venv/bin/python tests/test_pipeline.py            # regex + dictionary units (no models)
  .venv/bin/python tests/test_pipeline.py --llm      # + Ollama cleaning cases
  .venv/bin/python tests/test_pipeline.py --asr      # + ASR on `say`-synthesized speech
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


ASR_CASES = [
    "Let's meet on Friday at three PM to review the quarterly report.",
    "The quick brown fox jumps over the lazy dog.",
    "Please update the readme and push the release by Wednesday.",
]


def test_asr():
    print("ASR (parakeet-mlx) on say-synthesized speech:")
    from sotto.asr_mlx import ParakeetASR
    import numpy as np
    import wave as wavemod
    asr = ParakeetASR()
    for sentence in ASR_CASES:
        with tempfile.TemporaryDirectory() as td:
            wav = os.path.join(td, "t.wav")
            subprocess.run(["say", "-o", wav, "--data-format=LEI16@16000", sentence], check=True)
            with wavemod.open(wav) as w:
                audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
            audio = audio.astype(np.float32) / 32768.0
            t0 = time.perf_counter()
            out = asr.transcribe(audio)
            dt = time.perf_counter() - t0
            norm = lambda s: "".join(c for c in s.lower() if c.isalnum() or c == " ")
            from rapidfuzz import fuzz
            score = fuzz.ratio(norm(sentence), norm(out))
            check(f"({dt:.2f}s, {score:.0f}% match) {sentence[:40]}…", score >= 85, f"got: {out!r}")


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
    from sotto.hotkey import HotkeyListener
    ev = []
    hl = HotkeyListener("alt_r", on_start=lambda: ev.append("start"),
                        on_stop=lambda discard=False: ev.append("discard" if discard else "stop"))
    hl._hotkey_press()          # start dictating (hold)
    hl.force_stop()             # time limit reached
    check("stops and processes", ev == ["start", "stop"], str(ev))
    hl.force_stop()
    check("idempotent when idle", ev == ["start", "stop"], str(ev))


def test_asr_long():
    print("ASR long-form chunking (tiled speech, forced multi-chunk):")
    from sotto.asr_mlx import ParakeetASR
    import numpy as np
    import wave as wavemod
    asr = ParakeetASR()
    sentence = ("The first city is Amsterdam. The second city is Barcelona. "
                "The third city is Chicago. The fourth city is Denver.")
    with tempfile.TemporaryDirectory() as td:
        wav = os.path.join(td, "t.wav")
        subprocess.run(["say", "-o", wav, "--data-format=LEI16@16000", sentence], check=True)
        with wavemod.open(wav) as w:
            clip = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    clip = clip.astype(np.float32) / 32768.0
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
    test_llm_fallback()
    if run_all or "--llm" in args:
        test_llm()
    if run_all or "--asr" in args:
        test_asr()
    if run_all or "--long" in args:
        test_asr_long()
    print(f"\n{'ALL PASS' if failures == 0 else f'{failures} FAILURE(S)'}")
    sys.exit(1 if failures else 0)
