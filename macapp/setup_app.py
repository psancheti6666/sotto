# Created by Pratik Sancheti / https://github.com/psancheti6666
"""py2app setup for Sotto.app. Run via macapp/build_app.sh, never in CI.

Sotto selects its platform backends with lazy imports, which py2app's static
module graph cannot follow — so every runtime dependency is force-included
via `packages` (verbatim directory copies, which also preserves wheel-internal
native files: mlx's mx.metallib, portaudio/libsndfile dylibs, libllvmlite,
certifi's cacert.pem, and sotto/dashboard.html). The Linux/Intel chains are
cut with `excludes`; onnxruntime in particular is installed in the dev venv
and would otherwise ride in through sotto/asr.py's lazy import.
"""
import pathlib
import re

from setuptools import setup

ROOT = pathlib.Path(__file__).resolve().parent.parent
VERSION = re.search(r'__version__\s*=\s*"([^"]+)"',
                    (ROOT / "sotto" / "__init__.py").read_text()).group(1)

PACKAGES = [
    "sotto",  # includes dashboard.html and all lazily-imported backends
    # ASR stack (reached only via lazy imports)
    "mlx", "parakeet_mlx", "numpy", "dacite",
    "huggingface_hub", "filelock", "fsspec", "tqdm", "hf_xet",
    # librosa chain (parakeet_mlx.audio hard-imports librosa)
    "librosa", "lazy_loader", "numba", "llvmlite", "scipy", "sklearn",
    "joblib", "soxr", "audioread", "msgpack",
    "pooch", "platformdirs", "decorator", "packaging",
    # py2app's boot script imports pkg_resources, which resolves its jaraco.*
    # deps from setuptools/_vendor — both must ship verbatim
    "setuptools", "pkg_resources",
    # audio I/O native data (portaudio / libsndfile dylibs live here)
    "_sounddevice_data", "_soundfile_data", "cffi",
    # hotkey + injection
    "pynput",
    # LLM cleanup client + dictionary fuzzy matching
    "requests", "urllib3", "certifi", "charset_normalizer", "idna",
    "rapidfuzz",
    # pyobjc (overlay, sounds, haptics, event tap, menu bar);
    # PyObjCTools is a namespace package py2app can't copy wholesale —
    # its one used submodule goes through `includes` instead
    "objc", "AppKit", "Foundation", "CoreFoundation", "Quartz",
    "ApplicationServices", "CoreText",
    "AVFoundation", "CoreMedia", "CoreAudio",  # first-run mic authorization
    "WebKit",  # native Insights window
]

OPTIONS = {
    "packages": PACKAGES,
    # single-file modules can't go in `packages`
    "includes": ["sounddevice", "_sounddevice", "_cffi_backend", "pyperclip",
                 "PyObjCTools.MachSignals", "threadpoolctl", "soundfile",
                 # charset_normalizer's mypyc-compiled half; modulegraph
                 # misses it and requests then can't import the package
                 "ada92cb5d92a588d1b93__mypyc"],
    "excludes": [
        "onnx_asr", "onnxruntime", "onnx",  # Intel/Linux ASR backend
        "evdev",                            # Linux hotkey backend
        "tkinter", "_tkinter",              # overlay_tk fallback
        "typer", "click", "rich", "shellingham",  # parakeet_mlx CLI only
        "rumps", "PyObjCTest", "matplotlib",
        # modulegraph synthesizes a bare jaraco/__init__ stub that shadows the
        # real namespace package shipped in setuptools/_vendor — keep it out
        "jaraco",
    ],
    "plist": {
        "CFBundleName": "Sotto",
        "CFBundleDisplayName": "Sotto",
        "CFBundleIdentifier": "io.github.psancheti6666.sotto",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSUIElement": False,  # regular app: Dock icon + menu-bar waveform
        "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.productivity",
        "NSMicrophoneUsageDescription":
            "Sotto records your voice while you hold the dictation hotkey and "
            "transcribes it entirely on this Mac. Audio never leaves your "
            "computer.",
        "NSHumanReadableCopyright": "© 2026 Pratik Sancheti. MIT License.",
        # the Insights WKWebView loads the dashboard over plain HTTP from
        # 127.0.0.1 — local traffic only, never the network
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
    },
    "iconfile": "build/Sotto.icns",
    "argv_emulation": False,
    "strip": False,  # keep first builds debuggable; slim at the DMG milestone
    "optimize": 0,
}

setup(
    name="Sotto",
    version=VERSION,
    app=["macapp/sotto_app.py"],
    options={"py2app": OPTIONS},
)
