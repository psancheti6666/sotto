# Created by Pratik Sancheti / https://github.com/psancheti6666
"""PyInstaller entry point for the Linux build (docs/linux-app.md, L3).

--smoke imports every module Sotto selects lazily at runtime and exits 0.
PyInstaller cannot follow the lazy platform imports in app.py's backend
selectors, so a hidden-import gap would otherwise surface as a crash on a
user's machine; CI runs --smoke on every build instead. test_smoke_imports
pins this list against the runtime selectors — extend both together.
"""

import sys

SMOKE_IMPORTS = [
    # third-party backends the selectors reach for
    "tkinter",
    "evdev",
    "sounddevice",
    "onnxruntime",
    "onnx_asr",
    "numpy",
    "requests",
    "pyperclip",
    "rapidfuzz",
    "huggingface_hub",
    "zstandard",
    # every sotto module that is imported lazily or platform-selected
    "sotto.app",
    "sotto.asr",
    "sotto.asr_onnx",
    "sotto.audio",
    "sotto.clean",
    "sotto.dashboard",
    "sotto.dictionary",
    "sotto.firstrun",
    "sotto.firstrun_linux",
    "sotto.firstrun_tk",
    "sotto.history",
    "sotto.hotkey_evdev",
    "sotto.inject",
    "sotto.inject_linux",
    "sotto.llm_server",
    "sotto.ollama_runtime",
    "sotto.overlay_tk",
    "sotto.platform.linux",
    "sotto.update",
]


def smoke() -> int:
    import importlib
    import os
    for name in SMOKE_IMPORTS:
        importlib.import_module(name)
    from sotto import __version__
    # bundle= lets CI verify the /usr/bin/sotto launcher's SOTTO_BUNDLE=deb
    # export actually reaches the app (the deb smoke greps for bundle=deb)
    print(f"sotto {__version__} smoke OK (bundle={os.environ.get('SOTTO_BUNDLE', '-')})")
    return 0


def main():
    if "--smoke" in sys.argv[1:]:
        sys.exit(smoke())
    from sotto.app import main as run_sotto
    run_sotto()


if __name__ == "__main__":
    main()
