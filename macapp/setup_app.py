# Created by Pratik Sancheti / https://github.com/psancheti6666
"""py2app setup for Sotto.app. Run via macapp/build_app.sh, never in CI.

Sotto selects its platform backends with lazy imports, which py2app's static
module graph cannot follow — so every runtime dependency is force-included
via `packages` (verbatim directory copies, which also preserves wheel-internal
native files: mlx's mx.metallib, portaudio/libsndfile dylibs, libllvmlite,
certifi's cacert.pem, and sotto/dashboard.html). The build is arch-aware:
only the ASR stack differs (sotto/asr.py "auto" picks MLX on Apple Silicon,
ONNX on Intel), so each arch force-includes its own backend and excludes the
other's — on arm64 the exclusion matters because onnxruntime is installed in
the dev venv and would otherwise ride in through sotto/asr.py's lazy import.
"""
import os
import pathlib
import platform
import re

from setuptools import setup

ROOT = pathlib.Path(__file__).resolve().parent.parent
VERSION = re.search(r'__version__\s*=\s*"([^"]+)"',
                    (ROOT / "sotto" / "__init__.py").read_text()).group(1)

# Dev/release split: the default local build is "Sotto Dev" — its own bundle
# id, name, and DEV-badged icon — so it coexists with an installed release
# Sotto as a completely separate app (separate permission rows, separate Dock
# tile; contributors never fight their daily-driver copy). CI sets
# SOTTO_RELEASE=1 to build the real thing.
RELEASE = os.environ.get("SOTTO_RELEASE") == "1"
APP_NAME = "Sotto" if RELEASE else "Sotto Dev"  # py2app: dist/<CFBundleName>.app
BUNDLE_ID = "io.github.psancheti6666.sotto" + ("" if RELEASE else ".dev")

ARM64 = platform.machine() == "arm64"

PACKAGES = [
    "sotto",  # includes dashboard.html and all lazily-imported backends
    # ASR-stack pieces shared by both backends (model downloads go through
    # huggingface_hub on MLX and ONNX alike)
    "numpy", "packaging",
    "huggingface_hub", "filelock", "fsspec", "tqdm", "hf_xet",
    # py2app's boot script imports pkg_resources, which resolves its jaraco.*
    # deps from setuptools/_vendor — both must ship verbatim
    "setuptools", "pkg_resources",
    # audio I/O native data (the portaudio dylib lives here)
    "_sounddevice_data", "cffi",
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

if ARM64:
    PACKAGES += [
        # MLX ASR backend (reached only via lazy imports)
        "mlx", "parakeet_mlx", "dacite",
        # librosa chain (parakeet_mlx.audio hard-imports librosa)
        "librosa", "lazy_loader", "numba", "llvmlite", "scipy", "sklearn",
        "joblib", "soxr", "audioread", "msgpack",
        "pooch", "platformdirs", "decorator",
        "_soundfile_data",  # libsndfile dylib (librosa's audio reader)
    ]
    ASR_EXCLUDES = ["onnx_asr", "onnxruntime", "onnx"]
    ASR_INCLUDES = ["soundfile", "threadpoolctl"]  # librosa / sklearn deps
else:
    PACKAGES += [
        # ONNX ASR backend. onnx-asr is deliberately lightweight: its import
        # graph is numpy + onnxruntime + huggingface_hub + typing_extensions,
        # nothing else — the librosa/numba/llvmlite chain above is
        # parakeet-mlx's and stays out of the Intel bundle. flatbuffers is
        # onnxruntime's; protobuf (`google.*` namespace package — a py2app
        # `packages` landmine like mlx below) is NOT bundled: only
        # onnxruntime's offline tools import it, never the InferenceSession
        # path onnx_asr uses. The `onnx` package likewise isn't a runtime
        # dep of onnx-asr and isn't even installed by requirements.txt.
        "onnx_asr", "onnxruntime", "flatbuffers",
    ]
    ASR_EXCLUDES = ["mlx", "parakeet_mlx", "dacite",
                    "librosa", "lazy_loader", "numba", "llvmlite", "scipy",
                    "sklearn", "joblib", "soxr", "audioread", "msgpack",
                    "pooch", "decorator"]
    ASR_INCLUDES = ["typing_extensions"]  # single module, can't go in packages

OPTIONS = {
    "packages": PACKAGES,
    # single-file modules can't go in `packages`
    "includes": ["sounddevice", "_sounddevice", "_cffi_backend", "pyperclip",
                 "PyObjCTools.MachSignals",
                 # charset_normalizer's mypyc-compiled half; modulegraph
                 # misses it and requests then can't import the package
                 "ada92cb5d92a588d1b93__mypyc"] + ASR_INCLUDES,
    "excludes": ASR_EXCLUDES + [             # the other arch's ASR backend
        "evdev",                             # Linux hotkey backend
        "tkinter", "_tkinter",              # overlay_tk fallback
        "typer", "click", "rich", "shellingham",  # parakeet_mlx CLI only
        "rumps", "PyObjCTest", "matplotlib",
        # modulegraph synthesizes a bare jaraco/__init__ stub that shadows the
        # real namespace package shipped in setuptools/_vendor — keep it out
        "jaraco",
    ],
    "plist": {
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleIdentifier": BUNDLE_ID,
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
    name=APP_NAME.replace(" ", ""),
    version=VERSION,
    app=["macapp/sotto_app.py"],
    options={"py2app": OPTIONS},
)
