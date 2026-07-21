# Contributing to Sotto

Thanks for wanting to help! Sotto is a small project and contributions of every
size are welcome — bug reports, docs fixes, new features, or just telling us it
works (or doesn't) on your machine.

## The ground rules

These are the principles Sotto is built on. PRs that break them will be
declined, however good the code is:

1. **100% local, always.** Sotto makes zero network requests at runtime beyond
   `localhost` (Ollama and the dashboard). No telemetry, no analytics, no CDN
   scripts or web fonts in the dashboard, no "phone home" of any kind. The
   dashboard page must keep working with Wi-Fi off.
2. **Fidelity over polish.** The cleanup stage must never paraphrase, condense,
   or reword what the user said. The prompt rules and the output-length
   guardrail in `sotto/clean.py` exist for this — don't weaken them.
3. **User data stays private.** Dictation history, the personal dictionary, and
   config live in `~/.sotto/` and are never committed, logged verbosely, or
   sent anywhere. `.gitignore` deliberately covers `history*.jsonl`,
   `dictionary.txt`, and `config.toml` — keep it that way.
4. **Supported platforms:** macOS (Apple Silicon + Intel), Linux (X11 +
   Wayland), and Windows 10/11.
5. **No copyrighted assets.** Sounds come from the OS (macOS system sounds,
   the freedesktop theme on Linux). Never bundle audio, fonts, or artwork you
   don't have rights to.

## Getting set up

```bash
git clone https://github.com/psancheti6666/sotto.git
cd sotto
./setup.sh     # installs Python deps and downloads both models
./run.sh       # start Sotto
```

The code map is short:

| Area | Files |
|---|---|
| Pipeline wiring | `sotto/app.py` |
| Hotkey | `sotto/hotkey.py` (macOS + Windows), `sotto/hotkey_evdev.py` (Linux) |
| Speech recognition | `sotto/asr.py`, `asr_mlx.py` (Apple Silicon), `asr_onnx.py` (everything else) |
| Text cleanup | `sotto/clean.py`, `sotto/dictionary.py` |
| Typing at the cursor | `sotto/inject.py`, `inject_linux.py`, `inject_windows.py` |
| On-screen capsule | `sotto/overlay.py` (AppKit), `overlay_tk.py` (tkinter, Linux + Windows) |
| Insights window | `sotto/insights.py` (WKWebView), `insights_linux.py` (WebKitGTK), `insights_windows.py` (WebView2) |
| Tray / menu bar | `sotto/menubar.py` (macOS), `tray_linux.py` (pystray, Linux + Windows) |
| First-run setup | `sotto/firstrun.py` (macOS), `firstrun_tk.py` + `firstrun_linux.py` / `firstrun_windows.py` |
| Dashboard | `sotto/dashboard.py`, `dashboard.html`, `history.py` |

The "How it works" section of the README has the full pipeline diagram.

## Tests

```bash
.venv/bin/python tests/test_pipeline.py            # units — no models, no mic, runs anywhere
.venv/bin/python tests/test_pipeline.py --llm      # + live Ollama cleaning cases
.venv/bin/python tests/test_pipeline.py --asr      # + ASR on synthesized speech (macOS `say`)
.venv/bin/python tests/test_pipeline.py --all      # everything
```

CI runs the unit tier on Ubuntu, macOS, and Windows for every PR. Please run at least the
units before opening one, and `--all` if you have Ollama set up.

## Style

- Match the surrounding code — naming, comment density, structure. Comments
  explain *why*, not *what*.
- Standard library first. The dashboard has a hard zero-dependency rule
  (stdlib server, one self-contained HTML page). New pip dependencies anywhere
  else need a strong reason.
- Keep PRs small and focused: one change per PR.

## Opening a PR

- Say **which platform(s) you tested on live** (e.g. "macOS M1", "Fedora 40
  Wayland"). The maintainer only has Apple Silicon hardware — the Intel Mac
  and Linux paths are CI- and community-tested, so a real-hardware test report
  is genuinely valuable.
- UI changes (capsule or dashboard): include a screenshot.
- New logic should come with a test in `tests/test_pipeline.py`, in the
  existing plain-functions style.

## Reporting bugs

Use the bug report template. The platform details it asks for (OS, Apple
Silicon vs Intel, X11 vs Wayland, Python version) usually decide the diagnosis,
and terminal output from the run almost always helps.
