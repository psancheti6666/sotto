"""Configuration: ~/.sotto/config.toml overrides the defaults below."""

import os
import tomllib
from dataclasses import dataclass, field

CONFIG_DIR = os.path.expanduser("~/.sotto")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.toml")
DICTIONARY_PATH = os.path.join(CONFIG_DIR, "dictionary.txt")

DEFAULT_TONE_MAP = {
    # bundle id (or prefix) -> tone hint fed to the cleaning prompt
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
}


@dataclass
class Config:
    # Hotkey: "fn" (Wispr-style, via a Quartz event tap) or any pynput key name
    # (alt_r, cmd_r, ctrl, f5, f13, …). Hold to talk; +Space or double-tap = hands-free.
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

    # ASR
    asr_model: str = "mlx-community/parakeet-tdt-0.6b-v3"

    # Cleaning LLM (Ollama) — mandatory stage; fallback is regex-cleaned, never raw
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:4b-instruct"
    llm_timeout_s: float = 6.0
    keep_alive: int = -1            # keep model resident
    default_tone: str = "neutral written text"

    # Feedback (on-screen capsule, sounds, trackpad haptics)
    indicator: bool = True
    indicator_offset_y: float = 6.0   # px above the bottom edge of the screen
    sounds: bool = True
    haptics: bool = True
    start_sound: str = "Pop"          # recording started
    done_sound: str = "Tink"          # text inserted
    handsfree_sound: str = "Purr"     # entered hands-free mode
    cancel_sound: str = "Morse"       # dictation cancelled (Escape / ✕)
    warn_sound: str = "Frog"          # one minute left before the limit

    # Injection
    paste_restore_delay_s: float = 0.15
    keystroke_apps: list = field(default_factory=list)  # bundle ids that block paste

    tone_map: dict = field(default_factory=lambda: dict(DEFAULT_TONE_MAP))


def load_config() -> Config:
    cfg = Config()
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "rb") as f:
            data = tomllib.load(f)
        for key, value in data.items():
            if key == "tone_map":
                cfg.tone_map.update(value)
            elif hasattr(cfg, key):
                setattr(cfg, key, value)
    return cfg
