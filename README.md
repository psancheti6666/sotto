# Sotto

**Private, local dictation for macOS.** Hold a key, speak naturally — clean,
punctuated text appears at your cursor in any app. Nothing ever leaves your Mac.

Sotto is a local-first alternative to cloud dictation tools like Wispr Flow:
same hold-to-talk workflow, but the speech recognition **and** the AI text
cleanup run entirely on your machine. No account, no subscription, no audio
uploaded anywhere, **$0 to run**.

## What it does

- **Hold `fn`, speak, release** → your words appear at the cursor, in whatever
  app has focus (Notes, Slack, VS Code, Gmail, anything with a text field).
- **AI cleanup on every dictation** — filler words ("um", "uh", "you know")
  removed, punctuation and capitalization added, spoken lists formatted as
  lists, and self-corrections resolved: say *"let's meet Tuesday — wait, no,
  Friday"* and it writes *"Let's meet Friday."* Your wording is preserved —
  it cleans, it never rewrites.
- **Hands-free mode** — press Space while holding `fn` (or double-tap `fn`)
  and keep talking for up to 15 minutes; press `fn` to finish. A live waveform
  capsule at the bottom of the screen shows it's listening, with a countdown
  when time is nearly up.
- **Personal dictionary** — put names and jargon in `~/.sotto/dictionary.txt`
  (one per line) and Sotto will spell them correctly even when the recognizer
  mishears them.
- **App-aware tone** — punctuation/formatting adapts to the frontmost app
  (chat vs. email vs. code editor).

## Requirements

- **Mac with Apple Silicon** (M1 or newer) — macOS 14+. *macOS only for now.*
- **~6 GB free memory** while running, **~4 GB disk** for the two AI models.
- **[Homebrew](https://brew.sh)** — the setup script uses it to install the rest.

Everything else is installed automatically by `setup.sh`, including:

- **[Ollama](https://ollama.com)** — a free, open-source runtime for running
  large language models locally. Sotto uses it to run **Qwen3-4B-Instruct**
  (~2.5 GB), the model that turns raw speech transcripts into clean text.
  Ollama runs as a background service on your Mac and is never exposed to
  the internet.
- **NVIDIA Parakeet-TDT-0.6B-v3** (~600 MB) — the speech-recognition model,
  running on Apple's Neural Engine via [MLX](https://github.com/ml-explore/mlx).
  Fast (a few hundred milliseconds per utterance) and highly accurate.

## Install

```sh
git clone https://github.com/psancheti6666/sotto.git
cd sotto
./setup.sh
```

That's the whole setup: it checks your machine, installs Python 3.11 and
Ollama if missing, downloads both AI models, and offers to configure the `fn`
key (macOS's Globe-key action must be set to "Do Nothing", or the emoji picker
will pop up when you dictate — the script handles this with your permission).

## Run

```sh
./run.sh
```

On **first launch**, macOS will prompt you to grant three permissions to your
terminal app under **System Settings → Privacy & Security**:

| Permission | Why Sotto needs it |
|---|---|
| Microphone | to hear your dictation |
| Accessibility | to type the cleaned text at your cursor |
| Input Monitoring | to detect the `fn` hotkey globally |

Grant them, restart `./run.sh`, and you're live: put your cursor in any text
field, **hold `fn`**, speak, release.

## Using Sotto

| Gesture | Action |
|---|---|
| Hold `fn` + speak + release | Dictate one utterance |
| Hold `fn` + press Space | Enter hands-free mode (space is swallowed) |
| Double-tap `fn` | Also enters hands-free mode |
| Press `fn` (in hands-free) | Finish and insert |
| `fn` used in a shortcut (fn+Delete…) | Dictation silently cancels; shortcut works normally |

While listening, a small capsule at the bottom-center of the screen shows a
live waveform; it switches to a spinner while transcribing. In the last minute
of a long hands-free session the bars turn amber with a seconds countdown,
then the recording finishes and is transcribed **in full** — long dictations
are chunked and stitched, never truncated. A soft sound marks start and finish.

Dictation is intentionally inactive in password fields (macOS secure input).

## Configuration

Optional — create `~/.sotto/config.toml`:

```toml
hotkey = "fn"              # or "alt_r", "cmd_r", "ctrl", "f13", …
max_utterance_s = 900.0    # dictation limit (seconds)
ollama_model = "qwen3:4b-instruct"  # "llama3.2:3b" fits 8 GB Macs
indicator = true           # on-screen capsule
sounds = true              # start/finish sounds
haptics = true             # trackpad tap on start
indicator_offset_y = 6.0   # capsule distance from screen bottom (px)
keystroke_apps = []        # bundle ids of apps where paste doesn't work

[tone_map]                 # bundle id -> tone hint
"com.example.chat" = "casual chat message"
```

Personal dictionary — `~/.sotto/dictionary.txt`, one term per line:

```
Anthropic
Kubernetes
Saanvi Reddy
```

## How it works

```
hold fn ──► mic capture (16 kHz) ──► Parakeet ASR (Neural Engine, ~0.3 s)
        ──► personal-dictionary fix ──► LLM cleanup (Ollama, ~1 s)
        ──► paste at cursor (clipboard is saved and restored)
```

Typical end-to-end latency is **1–2 seconds** from key-release to text for a
normal utterance. A full 15-minute dictation takes about 1–2 minutes to clean —
the spinner stays up while it works. If the cleanup model is ever unavailable,
Sotto falls back to a basic regex cleanup rather than blocking or emitting
raw transcript.

## Verify your install

```sh
.venv/bin/python tests/test_pipeline.py --all
```

Runs the unit tests plus live checks of the speech recognizer (using
macOS-synthesized speech — no microphone needed) and the cleanup model.

## Privacy & cost

- Audio, transcripts, and cleaned text never leave your machine. Works fully
  offline after setup.
- No telemetry, no account, no API keys.
- Recurring cost: electricity for ~1 second of compute per dictation.

## Limitations

- **macOS on Apple Silicon only** (for now). Intel Macs, Windows, and Linux
  are not supported.
- English works best (the ASR model also covers 24 other European languages;
  the cleanup prompt is English-tuned).
- Very heavy phonetic mishearings can escape the personal-dictionary fix.

## License

[MIT](LICENSE)
