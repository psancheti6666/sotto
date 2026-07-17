# Created by Pratik Sancheti / https://github.com/psancheti6666
"""Configuration: ~/.sotto/config.toml overrides the defaults below.

The dataclass defaults are macOS values; on Linux, load_config() swaps in
Linux defaults (hotkey, sound names, terminal keystroke apps) BEFORE applying
the user's file, so user overrides always win.
"""

import os
import tomllib
from dataclasses import dataclass, field

from .platform import IS_LINUX

CONFIG_DIR = os.path.expanduser("~/.sotto")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.toml")
DICTIONARY_PATH = os.path.join(CONFIG_DIR, "dictionary.txt")
HISTORY_PATH = os.path.join(CONFIG_DIR, "history.jsonl")

DEFAULT_TONE_MAP = {
    # app id -> tone hint fed to the cleaning prompt. macOS keys are bundle ids
    # (reverse-DNS); Linux keys are lowercased X11 WM_CLASS names — the two
    # can't collide, so one merged map serves both platforms.
    "com.apple.mail": "professional email/document",
    "com.microsoft.Outlook": "professional email/document",
    "com.apple.iWork.Pages": "professional email/document",
    "com.google.Chrome": "neutral written text",
    "com.tinyspeck.slackmacgap": "casual chat message",
    "com.apple.MobileSMS": "casual chat message",
    "net.whatsapp.WhatsApp": "casual chat message",
    "com.hnc.Discord": "casual chat message",
    "com.microsoft.VSCode": "plain text for a code editor (no smart quotes)",
    "com.apple.Terminal": "plain text for a code editor (no smart quotes)",
    "com.googlecode.iterm2": "plain text for a code editor (no smart quotes)",
    "com.todesktop.230313mzl4w4u92": "plain text for a code editor (no smart quotes)",  # Cursor
    # Linux (WM_CLASS)
    "thunderbird": "professional email/document",
    "evolution": "professional email/document",
    "google-chrome": "neutral written text",
    "firefox": "neutral written text",
    "slack": "casual chat message",
    "discord": "casual chat message",
    "org.telegram.desktop": "casual chat message",
    "code": "plain text for a code editor (no smart quotes)",
    "gnome-terminal": "plain text for a code editor (no smart quotes)",
    "konsole": "plain text for a code editor (no smart quotes)",
    "alacritty": "plain text for a code editor (no smart quotes)",
    "kitty": "plain text for a code editor (no smart quotes)",
    "xterm": "plain text for a code editor (no smart quotes)",
}

# Linux terminals paste with Ctrl+Shift+V, not Ctrl+V — force keystroke typing there.
LINUX_TERMINAL_CLASSES = [
    "gnome-terminal", "konsole", "alacritty", "kitty", "xterm",
    "org.wezfurlong.wezterm", "xfce4-terminal", "terminator", "tilix",
]

# freedesktop sound-theme names (played from /usr/share/sounds/freedesktop/stereo)
LINUX_SOUND_DEFAULTS = {
    "start_sound": "audio-volume-change",
    "done_sound": "complete",
    "handsfree_sound": "device-added",
    "cancel_sound": "dialog-warning",
    "warn_sound": "bell",
}


@dataclass
class Config:
    # Hotkey. macOS: "fn" (Wispr-style, via a Quartz event tap) or any pynput key
    # name (alt_r, cmd_r, ctrl, f5, f13, …); hold to talk, +Space or double-tap =
    # hands-free. Linux: "ctrl_r" by default (fn never reaches the OS on PCs),
    # or any evdev key name; hold to talk, double-tap = hands-free.
    hotkey: str = "fn"
    tap_max_s: float = 0.3          # press shorter than this = tap (double-tap toggles)
    double_tap_window_s: float = 0.5

    # Audio: dictation may run up to this long (hold or hands-free); the capsule
    # shows a countdown when less than warn_remaining_s is left, then the
    # recording auto-finishes and is transcribed in full.
    sample_rate: int = 16000
    max_utterance_s: float = 900.0     # 15 minutes
    warn_remaining_s: float = 60.0
    undo_window_s: float = 3.0         # Escape/✕ cancel → Undo toast duration

    # ASR. "auto" picks MLX on Apple Silicon and ONNX everywhere else — the
    # same Parakeet model either way.
    asr_backend: str = "auto"            # auto | mlx | onnx
    asr_model: str = "mlx-community/parakeet-tdt-0.6b-v3"
    onnx_model: str = "nemo-parakeet-tdt-0.6b-v3"
    onnx_quantization: str = ""          # "" = full precision; "int8" for slow CPUs

    # Cleaning LLM (Ollama) — mandatory stage; fallback is regex-cleaned, never raw
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:4b-instruct"
    llm_timeout_s: float = 6.0
    keep_alive: str = "5m"          # idle window before Ollama unloads the model
                                    # (Ollama duration; -1 = resident forever)
    default_tone: str = "neutral written text"

    # Feedback (on-screen capsule, sounds, trackpad haptics)
    indicator: bool = True
    indicator_backend: str = "auto"   # auto | appkit | tk (debug/testing override)
    indicator_offset_y: float = 6.0   # px above the bottom edge of the screen
    sounds: bool = True
    haptics: bool = True
    start_sound: str = "Pop"          # recording started
    done_sound: str = "Morse"         # text inserted
    handsfree_sound: str = "Frog"     # entered hands-free mode
    cancel_sound: str = "Bottle"      # dictation cancelled (Escape / ✕)
    warn_sound: str = "Tink"          # one minute left before the limit

    # Dashboard: history browser + usage insights, served by the Sotto process
    # itself on 127.0.0.1 (never exposed beyond this machine). History lives in
    # ~/.sotto/history.jsonl.
    dashboard: bool = True
    dashboard_port: int = 8377
    open_dashboard_on_start: bool = True   # open the browser once on launch

    # Updates (released Sotto.app only — Sotto Dev and source checkouts skip
    # this; run.sh already self-updates via git pull). Every N days the app
    # asks api.github.com for the latest release — its ONLY network request
    # beyond localhost — and offers to install. 0 disables the scheduled
    # check; the "Check for Updates…" menu item always works.
    update_check_days: float = 1.0

    # Injection. "auto" = type the text like real keystrokes, but fall back to
    # clipboard-paste when the text has newlines (typed Enter would e.g. send a
    # chat message per line) or is very long (typing 12k chars takes minutes).
    # "type" / "paste" force one mode.
    inject_mode: str = "auto"
    type_max_chars: int = 2000
    type_interval_s: float = 0.003    # per-char delay; 0 can drop keys in some apps
    paste_restore_delay_s: float = 0.15
    keystroke_apps: list = field(default_factory=list)  # bundle ids that block paste

    tone_map: dict = field(default_factory=lambda: dict(DEFAULT_TONE_MAP))


def load_config() -> Config:
    cfg = Config()
    if IS_LINUX:
        cfg.hotkey = "ctrl_r"
        cfg.haptics = False
        for key, value in LINUX_SOUND_DEFAULTS.items():
            setattr(cfg, key, value)
        cfg.keystroke_apps = list(LINUX_TERMINAL_CLASSES)
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "rb") as f:
            data = tomllib.load(f)
        for key, value in data.items():
            if key == "tone_map":
                cfg.tone_map.update(value)
            elif hasattr(cfg, key):
                setattr(cfg, key, value)
    return cfg
