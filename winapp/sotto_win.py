# Created by Pratik Sancheti / https://github.com/psancheti6666
"""PyInstaller entry point for the Windows build (docs/windows-app.md, W7).

--smoke imports every module Sotto selects lazily at runtime and exits 0.
PyInstaller cannot follow the lazy platform imports in app.py's backend
selectors, so a hidden-import gap would otherwise surface as a crash on a
user's machine; CI runs --smoke on every build instead. test_smoke_imports
pins this list against the runtime selectors — extend both together.

The bundle is a WINDOWED app (no console flash at launch), which means the
bootloader sets sys.stdout/stderr to None — print() would crash. main()
repairs them to devnull first; the rotating ~/.sotto/sotto.log is the real
output surface (the same Finder-launch reality macOS already handles), and
--smoke communicates by EXIT CODE (CI asserts it), printing only when a
console is actually attached.
"""

import os
import sys

SMOKE_IMPORTS = [
    # third-party backends the selectors reach for
    "tkinter",
    "pynput",
    "sounddevice",
    "onnxruntime",
    "onnx_asr",
    "numpy",
    "requests",
    "pyperclip",
    "rapidfuzz",
    "huggingface_hub",
    # every sotto module that is imported lazily or platform-selected
    "sotto.app",
    "sotto.asr",
    "sotto.asr_onnx",
    "sotto.audio",
    "sotto.clean",
    "sotto.dashboard",
    "sotto.dictionary",
    "sotto.firstrun",
    "sotto.firstrun_windows",
    "sotto.firstrun_tk",
    "sotto.history",
    "sotto.hotkey",
    "sotto.inject",
    "sotto.inject_windows",
    "sotto.insights_windows",
    "sotto.llm_server",
    "sotto.ollama_runtime",
    "sotto.overlay_tk",
    "sotto.platform.windows",
    "sotto.tray_linux",
    "sotto.update",
]

# Bundled-presence checks (find_spec, NOT imported): importing pystray runs
# its backend auto-selection — on Windows that touches the real shell tray —
# and importing webview loads the pythonnet/.NET machinery, which belongs in
# the dedicated --smoke-webview step, not the import smoke. The smoke only
# asserts the modules made it into the bundle; runtime failures collapse to
# best-effort fallbacks (tray-less log line / browser tab).
SMOKE_FIND = [
    "pystray",
    "webview",
]


def _repair_streams():
    """Windowed bootloader leaves sys.stdout/stderr as None — anything that
    prints (or logging's console handler) would die. Devnull keeps every
    write harmless; ~/.sotto/sotto.log is the real output surface."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


def smoke() -> int:
    import importlib
    import importlib.util
    for name in SMOKE_IMPORTS:
        importlib.import_module(name)
    for name in SMOKE_FIND:
        if importlib.util.find_spec(name) is None:
            raise ImportError(f"{name} missing from the bundle")
    from sotto import __version__
    print(f"sotto {__version__} smoke OK (windows)")
    return 0


def main():
    _repair_streams()
    if "--smoke" in sys.argv[1:]:
        sys.exit(smoke())
    if "--smoke-webview" in sys.argv[1:]:
        # CI-only: render the real dashboard in the real WebView2 inside
        # the frozen bundle (docs/windows-app.md W8) — exit code contract.
        from sotto import insights_windows
        sys.exit(insights_windows.smoke(port=8399))
    from sotto.app import main as run_sotto
    run_sotto()


if __name__ == "__main__":
    main()
