# Created by Pratik Sancheti / https://github.com/psancheti6666
# PyInstaller onedir spec for the Windows build (docs/windows-app.md, W7).
# Build from the repo root:  pyinstaller --noconfirm winapp/sotto_win.spec
#
# The Windows sibling of linuxapp/sotto.spec: Windows backends (pynput,
# tkinter, ONNX, sounddevice, winsound/ctypes via stdlib) force-included,
# the macOS stack (pyobjc, MLX) and Linux stack (evdev, gi) excluded.
# PyInstaller cannot follow sotto's lazy platform imports, so the
# runtime-selected modules are hidden-imported here and winapp/sotto_win.py
# --smoke imports the same list in CI (no Windows dev hardware exists).

import os

from PyInstaller.utils.hooks import collect_all

REPO = os.path.abspath(os.path.join(SPECPATH, ".."))

datas, binaries, hiddenimports = [], [], []

# onnx_asr ships model configs as package data; huggingface_hub is the
# first-run model downloader (pulled in via onnx-asr[hub])
for pkg in ("onnx_asr", "huggingface_hub"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# the dashboard page is read from the package directory at runtime
datas.append((os.path.join(REPO, "sotto", "dashboard.html"), "sotto"))

# tray icon: cropped from the wordmark at runtime (no hicolor equivalent
# on Windows; same fractions as everywhere else)
datas.append((os.path.join(REPO, "logo", "sottoLogo.png"), "logo"))

# NOTE: no PortAudio handling needed here — unlike Linux, sounddevice's
# Windows wheel bundles its own portaudio DLL and PyInstaller's contrib
# hook collects it. The CI smoke (imports sounddevice inside the frozen
# app) is the proof.

hiddenimports += [
    "pynput", "tkinter", "sounddevice", "pyperclip", "rapidfuzz",
    "requests", "numpy", "onnxruntime", "winsound",
    # sotto modules reached only through lazy/platform-selected imports
    "sotto.asr_onnx", "sotto.hotkey", "sotto.inject_windows",
    "sotto.overlay_tk", "sotto.platform.windows",
    "sotto.firstrun", "sotto.firstrun_windows", "sotto.firstrun_tk",
    "sotto.llm_server", "sotto.ollama_runtime", "sotto.update",
    # tray: pystray's Win32 backend is pure win32 API — no gi anywhere
    "sotto.tray_linux", "pystray", "pystray._win32",
]

a = Analysis(
    [os.path.join(SPECPATH, "sotto_win.py")],
    pathex=[REPO],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
    excludes=[
        # macOS-only stacks
        "objc", "AppKit", "Foundation", "Quartz", "WebKit",
        "AVFoundation", "UserNotifications", "mlx", "parakeet_mlx",
        # Linux-only stacks
        "evdev", "gi", "zstandard",
        # heavyweight strays that hitchhike via numpy/huggingface_hub
        "matplotlib", "scipy", "IPython", "pytest",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="sotto",
    console=False,  # windowed: no console flash; sotto_win.py repairs the
    # None std streams and ~/.sotto/sotto.log carries all output
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="sotto",
)
