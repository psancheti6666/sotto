# Created by Pratik Sancheti / https://github.com/psancheti6666
# PyInstaller onedir spec for the Linux build (docs/linux-app.md, L3).
# Build from the repo root:  pyinstaller --noconfirm linuxapp/sotto.spec
#
# The inverse of macapp/setup_app.py's lists: Linux backends (evdev, tkinter,
# ONNX, sounddevice) are force-included, the macOS stack (pyobjc, pynput, MLX)
# is excluded. PyInstaller cannot follow sotto's lazy platform imports, so the
# runtime-selected modules are hidden-imported here and linuxapp/sotto_linux.py
# --smoke imports the same list in CI as the no-Linux-hardware safety net.

import glob
import os

from PyInstaller.utils.hooks import collect_all

REPO = os.path.abspath(os.path.join(SPECPATH, ".."))

datas, binaries, hiddenimports = [], [], []

# onnx_asr ships model configs as package data; huggingface_hub is the
# first-run model downloader (pulled in via onnx-asr[hub]).
for pkg in ("onnx_asr", "huggingface_hub"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# the dashboard page is read from the package directory at runtime
datas.append((os.path.join(REPO, "sotto", "dashboard.html"), "sotto"))

# tray_linux crops its icon from the wordmark when no hicolor icon is
# installed (tarball/checkout runs)
datas.append((os.path.join(REPO, "logo", "sottoLogo.png"), "logo"))

# sounddevice loads the SYSTEM libportaudio via ctypes on Linux (its wheels
# only bundle PortAudio for mac/windows) — ctypes loads are invisible to
# PyInstaller, so bundle the library explicitly. Missing at build time is a
# hard error: the onedir would crash on any machine without libportaudio2.
# Discovery at runtime is handled by rthook_portaudio.py (find_library only
# reads the system ldconfig cache and would never see the bundled copy);
# CI proves it by smoking the tarball in a bare container with no
# libportaudio2 installed.
_pa = sorted(glob.glob("/usr/lib/*/libportaudio.so.*")
             + glob.glob("/usr/lib/libportaudio.so.*"))
if not _pa:
    raise SystemExit("libportaudio not found — apt install libportaudio2")
binaries += [(p, ".") for p in _pa]

hiddenimports += [
    "evdev", "tkinter", "sounddevice", "pyperclip", "rapidfuzz",
    "requests", "numpy", "onnxruntime", "zstandard",
    # sotto modules reached only through lazy/platform-selected imports
    "sotto.asr_onnx", "sotto.hotkey_evdev", "sotto.inject_linux",
    "sotto.overlay_tk", "sotto.platform.linux",
    "sotto.firstrun", "sotto.firstrun_linux", "sotto.firstrun_tk",
    "sotto.llm_server", "sotto.ollama_runtime", "sotto.update",
    # tray (L7): pystray picks a backend at import time; gi + the
    # gi.repository modules ride the contrib hooks, which also collect the
    # typelibs. All best-effort — PyInstaller warns (not fails) on hidden
    # imports missing from the build venv, and tray_linux degrades at
    # runtime to a "tray unavailable" log line.
    "sotto.tray_linux", "pystray", "pystray._appindicator", "pystray._xorg",
    "gi", "gi.repository.Gtk", "gi.repository.GLib", "gi.repository.GObject",
    "gi.repository.AyatanaAppIndicator3",
]

a = Analysis(
    [os.path.join(SPECPATH, "sotto_linux.py")],
    pathex=[REPO],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
    runtime_hooks=[os.path.join(SPECPATH, "rthook_portaudio.py")],
    excludes=[
        # macOS-only stacks — absent from a Linux venv anyway; listed so a
        # contributor's mixed environment can't drag them in
        "pynput", "objc", "AppKit", "Foundation", "Quartz", "WebKit",
        "AVFoundation", "UserNotifications", "mlx", "parakeet_mlx",
        # heavyweight strays that hitchhike via numpy/huggingface_hub
        "matplotlib", "scipy", "IPython", "pytest",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="sotto",
    console=True,  # keeps stdout logging for terminal runs; Linux desktop
    upx=False,     # launchers ignore it — there is no "console window" to hide
)

coll = COLLECT(exe, a.binaries, a.datas, name="sotto", upx=False)
