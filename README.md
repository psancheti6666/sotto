# Sotto

**Private, local dictation for macOS and Linux.** Hold a key, speak naturally —
clean, punctuated text appears at your cursor in any app. Nothing ever leaves
your machine.

Sotto is a local-first alternative to cloud dictation tools like Wispr Flow:
same hold-to-talk workflow, but the speech recognition **and** the AI text
cleanup run entirely on your machine. No account, no subscription, no audio
uploaded anywhere, **$0 to run**.

## What it does

- **Hold the hotkey, speak, release** → your words appear at the cursor, in
  whatever app has focus (Notes, Slack, VS Code, Gmail, anything with a text
  field). The hotkey is `fn` on macOS, `Right Ctrl` on Linux (configurable).
- **AI cleanup on every dictation** — filler words ("um", "uh", "you know")
  removed, punctuation and capitalization added, spoken lists formatted as
  lists, and self-corrections resolved: say *"let's meet Tuesday — wait, no,
  Friday"* and it writes *"Let's meet Friday."* Your wording is preserved —
  it cleans, it never rewrites.
- **Hands-free mode** — double-tap the hotkey (on macOS, also hold `fn` +
  press Space) and keep talking for up to 15 minutes; press the hotkey again
  to finish. A live waveform capsule at the bottom of the screen shows it's
  listening, with a countdown when time is nearly up.
- **Personal dictionary** — put names and jargon in `~/.sotto/dictionary.txt`
  (one per line, or manage it from the dashboard) and Sotto will spell them
  correctly even when the recognizer mishears them.
- **App-aware tone** — punctuation/formatting adapts to the focused app
  (chat vs. email vs. code editor).

## Platform support

| Platform | Status |
|---|---|
| macOS, Apple Silicon (M1+) | ✅ Developed and tested on real hardware |
| macOS, Intel | 🤝 Community-tested — same code except the speech engine (ONNX instead of MLX), which is exercised in CI-style tests |
| Linux, X11 (Ubuntu/Fedora…) | 🤝 Community-tested — all Linux logic is unit-tested, but not yet verified on real desktops |
| Linux, Wayland | 🤝 Community-tested — works via wtype (KDE/wlroots) or ydotool (GNOME); see Linux notes |
| Windows | ❌ Not supported |

"Community-tested" means: the code paths exist, are unit-tested, and the
speech/cleanup pipeline is verified — but the maintainer develops on an
Apple-Silicon Mac. If you run Sotto on one of these platforms, please open an
issue (working or not!) so this table can be updated.

## Requirements

**All platforms:** ~6 GB free memory while dictating, ~5 GB disk for the AI
models. Memory is only held around actual use: the cleaning LLM unloads after
5 minutes idle and ASR inference buffers are freed after every dictation, so
an idle Sotto holds ~1.5 GB.

- **macOS 14+** (Apple Silicon or Intel) with [Homebrew](https://brew.sh) —
  the setup script installs the rest.
- **Linux** (Ubuntu/Debian or Fedora-family) with `sudo` — the setup script
  installs system packages via apt/dnf.

Everything else is installed automatically by `setup.sh`, including:

- **[Ollama](https://ollama.com)** — a free, open-source runtime for running
  large language models locally. Sotto uses it to run **Qwen3-4B-Instruct**
  (~2.5 GB), the model that turns raw speech transcripts into clean text.
  It runs as a background service and is never exposed to the internet.
- **NVIDIA Parakeet-TDT-0.6B-v3** — the speech-recognition model. On Apple
  Silicon it runs on the Neural Engine via
  [MLX](https://github.com/ml-explore/mlx) (~600 MB); on Intel Macs and Linux
  the **same model** runs via ONNX on the CPU (~2.4 GB) — same accuracy,
  still around a second per utterance.

## Install

```sh
git clone https://github.com/psancheti6666/sotto.git
cd sotto
./setup.sh
```

That's the whole setup: it detects your OS, installs Python and Ollama if
missing, downloads both AI models, and handles the platform-specific bits
(the macOS Globe-key setting; the Linux `input`-group permission — **log out
and back in after the first setup** on Linux).

## Run

```sh
./run.sh
```

**macOS, first launch:** grant three permissions to your terminal app under
**System Settings → Privacy & Security**, then restart `./run.sh`:

| Permission | Why Sotto needs it |
|---|---|
| Microphone | to hear your dictation |
| Accessibility | to type the cleaned text at your cursor |
| Input Monitoring | to detect the hotkey globally |

**Linux, first launch:** if setup just added you to the `input` group, log
out and back in first — otherwise the hotkey can't be detected.

## Using Sotto

| Gesture | macOS (`fn`) | Linux (`Right Ctrl`) |
|---|---|---|
| Hold + speak + release | ✅ dictate one utterance | ✅ dictate one utterance |
| Double-tap → hands-free | ✅ | ✅ |
| Hold + press Space → hands-free | ✅ (Space is swallowed) | — (would leak Ctrl+Space; use double-tap) |
| Press hotkey again (in hands-free) | ✅ finish and insert | ✅ finish and insert |
| **Escape** while dictating | ✅ cancel (swallowed) | ✅ cancel (the Escape also reaches the app) |
| ✕ / ✓ capsule buttons (hands-free) | ✅ | ✅ |
| Hotkey used in a shortcut (fn+Delete, Ctrl+C…) | dictation silently cancels; the shortcut works | same |

While holding the key, a compact capsule at the bottom-center shows a live
waveform. In hands-free mode it grows slightly and adds clickable ✕ (cancel)
and ✓ (finish) buttons; it switches to a spinner while transcribing.
Cancelling pops a "Transcript cancelled" toast with an **Undo** button and a
progress line — click Undo within ~3 seconds and the recording is transcribed
after all. In the last minute of a long session the bars turn amber with a
seconds countdown, then the recording finishes and is transcribed **in full**
— long dictations are chunked and stitched, never truncated. A soft sound
marks start and finish (Apple system sounds on macOS, the freedesktop sound
theme on Linux).

macOS note: dictation is intentionally inactive in password fields (secure
input). **Linux has no such mechanism — Sotto will type into password fields
too**, so mind where your cursor is.

## Dashboard

Sotto keeps a local history of your dictations and serves a small dashboard at
**http://127.0.0.1:8377** (it opens in your browser when Sotto starts). There
you can:

- **Browse every past dictation**, newest first, with time, word count,
  duration, and the app it was typed into. **Click any entry to copy it** to
  the clipboard — handy when an app swallowed the text or you want it again.
- **Search** your history as you type.
- **See insights**: total words dictated (all-time and today), number of
  dictations, your speaking rate (words per minute), estimated time saved vs
  typing, a day streak, and which apps you dictate into most. Activity has
  three views — two-week bars, a monthly trend line, and a GitHub-style
  contribution heatmap of the last year.
- **Manage your personal dictionary**: view, add, and remove terms right on
  the page — edits update `~/.sotto/dictionary.txt` and apply from the very
  next dictation, no restart needed.
- Light and dark theme, with a toggle in the header (follows your system
  setting until you choose).

Like everything in Sotto, this is 100% local: the page is served by the Sotto
process itself, binds only to 127.0.0.1, loads nothing from the internet, and
the history lives in a single human-readable file, `~/.sotto/history.jsonl`
(one JSON object per line — delete the file to wipe your history). Config
switches: `dashboard` (serve it at all), `open_dashboard_on_start` (auto-open
the browser), `dashboard_port`.

## Configuration

Optional — create `~/.sotto/config.toml`:

```toml
hotkey = "fn"              # macOS: "fn", "alt_r", "cmd_r", "f13", …
                           # Linux: "ctrl_r" (default), "alt_r", "super_r", "f9", …
max_utterance_s = 900.0    # dictation limit (seconds)
ollama_model = "qwen3:4b-instruct"  # "llama3.2:3b" is faster on CPU / 8 GB machines
asr_backend = "auto"       # "mlx" (Apple Silicon) | "onnx" (everything else)
onnx_quantization = ""     # set "int8" for slow CPUs (smaller + faster, tiny accuracy cost)
indicator = true           # on-screen capsule
dashboard = true           # local history dashboard at 127.0.0.1:8377
dashboard_port = 8377
open_dashboard_on_start = true  # open the browser when Sotto starts
sounds = true              # start/finish sounds
haptics = true             # trackpad tap on start (macOS only)
indicator_offset_y = 6.0   # capsule distance from screen bottom (px)
keystroke_apps = []        # apps where paste doesn't work (bundle ids on macOS,
                           # lowercased WM_CLASS on Linux; Linux terminals are
                           # included by default — they paste with Ctrl+Shift+V)

[tone_map]                 # app id -> tone hint (bundle id / WM_CLASS)
"com.example.chat" = "casual chat message"
"signal" = "casual chat message"
```

Personal dictionary — `~/.sotto/dictionary.txt`, one term per line:

```
Anthropic
Kubernetes
Saanvi Reddy
```

## How it works

```
hold hotkey ──► mic capture (16 kHz) ──► Parakeet ASR (MLX or ONNX, ~0.3–1 s)
            ──► personal-dictionary fix ──► LLM cleanup (Ollama, ~1 s on GPU)
            ──► typed at your cursor (clipboard-paste fallback for long text)
```

Typical end-to-end latency on Apple Silicon is **1–2 seconds** from
key-release to text. On CPU-only machines (Intel Macs, Linux without a GPU)
speech recognition stays fast, but the cleanup model is the bottleneck —
expect several seconds per dictation; switch `ollama_model` to
`"llama3.2:3b"` if that's too slow. If the cleanup model is ever unavailable,
Sotto falls back to a basic regex cleanup rather than blocking or emitting
raw transcript.

Linux specifics: the hotkey is read from `/dev/input` (works on X11 **and**
Wayland; needs the `input` group), and text is injected with the best
available tool — `xdotool` on X11; `wtype` (KDE/wlroots) or `ydotool` (GNOME;
needs the `ydotoold` service) on Wayland. If no injection tool works, the
transcript is copied to the clipboard and a notification asks you to paste.

## Verify your install

```sh
.venv/bin/python tests/test_pipeline.py --all
```

Runs the unit tests plus live checks of the speech recognizer (using
OS-synthesized speech — no microphone needed; `espeak-ng` on Linux) and the
cleanup model.

## Privacy & cost

- Audio, transcripts, and cleaned text never leave your machine. Works fully
  offline after setup.
- Dictation history is stored **only** in `~/.sotto/history.jsonl` on your
  machine, for the local dashboard. It is never uploaded anywhere — there is
  no server to upload it to. Delete the file (or set `dashboard = false`) any
  time.
- No telemetry, no account, no API keys.
- Recurring cost: electricity for a few seconds of compute per dictation.

## Limitations

- English works best (the ASR model also covers 24 other European languages;
  the cleanup prompt is English-tuned).
- Very heavy phonetic mishearings can escape the personal-dictionary fix.
- Linux: keys can't be swallowed, so Escape (cancel) also reaches the focused
  app, and remapped keyboards (keyd/kmonad) may report the hotkey from its
  pre-remap position. Keyboards connected while Sotto is running are picked
  up after the next rescan.
- Windows is not supported.

## License

[MIT](LICENSE)
