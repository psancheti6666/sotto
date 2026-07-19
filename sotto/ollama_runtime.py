# Created by Pratik Sancheti / https://github.com/psancheti6666
"""Linux/Windows LLM engine resolution: find or download the ollama runtime.

The packaged apps never bundle ollama (the linux-amd64 tarball is ~1.37 GB,
the windows-amd64 zip ~1.5 GB — docs/linux-app.md, docs/windows-app.md).
llm_server.ensure() still adopts a *reachable* server first; this module
only answers "which binary can we spawn": a system `ollama` on PATH
(respects a user's existing install), else the copy a previous first-run
downloaded to ~/.sotto/runtime/ollama, else None — in which case the
first-run download screen calls download().

Per-platform assets, both pinned + sha256-verified: Linux ships .tar.zst
(Python 3.11's tarfile has no zstd, so extraction streams through the
zstandard package — Linux-only requirement); Windows ships a plain zip
(stdlib zipfile, entries guarded against zip-slip) with ollama.exe at the
archive root. The Windows zip is deliberately NOT the desktop installer —
that runs its own tray/autostart; this stays OUR child with OUR lifecycle
(docs/windows-app.md decision table). Both shipped unpruned initially —
CPU-only pruning follows live measurements (L6 note / friend Round B).
"""

import hashlib
import logging
import os
import shutil
import sys
import tarfile
import tempfile

import requests

log = logging.getLogger("sotto")

# Pinned to the same ollama version macapp/build_app.sh bundles on macOS.
# sha256 values are GitHub's published asset digests for this release.
OLLAMA_VERSION = "0.32.1"
_RELEASE_BASE = ("https://github.com/ollama/ollama/releases/download/"
                 f"v{OLLAMA_VERSION}/")
_ASSETS = {
    "linux": {
        "url": _RELEASE_BASE + "ollama-linux-amd64.tar.zst",
        "sha256": "83b1f22841eb7f6c4900c6797f960ebaa09466874442ea5b8ae3da6980d3914c",
        "bin": ("bin", "ollama"),
        "kind": "tar.zst",
    },
    "win32": {
        "url": _RELEASE_BASE + "ollama-windows-amd64.zip",
        "sha256": "d5abdc21b64ee928d3c92880ac22da5e5b0a46b8b07179791dd8c711b35f8397",
        "bin": ("ollama.exe",),
        "kind": "zip",
    },
}
_ASSET = _ASSETS["win32" if sys.platform == "win32" else "linux"]
OLLAMA_URL = _ASSET["url"]
OLLAMA_SHA256 = _ASSET["sha256"]

RUNTIME_DIR = os.path.expanduser("~/.sotto/runtime/ollama")


def installed() -> str | None:
    """The previously-downloaded runtime's binary, or None."""
    path = os.path.join(RUNTIME_DIR, *_ASSET["bin"])
    return path if os.access(path, os.X_OK) else None


def resolve() -> str | None:
    """Binary to spawn: system install first, then our downloaded runtime."""
    return shutil.which("ollama") or installed()


def download(on_progress=None) -> str:
    """Fetch + verify + extract the pinned runtime; returns the binary path.
    on_progress(fraction) is called during the download (the extract phase is
    seconds, not worth its own bar). Raises on network/checksum/layout errors —
    the caller (first-run download screen, L5) owns retry UI."""
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    archive = os.path.join(RUNTIME_DIR, ".ollama-download.partial")
    try:
        _fetch(OLLAMA_URL, archive, OLLAMA_SHA256, on_progress)
        _extract(archive, RUNTIME_DIR)
    finally:
        if os.path.exists(archive):
            os.remove(archive)
    binary = installed()
    if not binary:
        raise RuntimeError("ollama runtime extracted but "
                           f"{'/'.join(_ASSET['bin'])} is missing")
    log.info("ollama runtime %s installed at %s", OLLAMA_VERSION, RUNTIME_DIR)
    return binary


def _fetch(url: str, dest: str, sha256: str, on_progress=None):
    """Stream url to dest, hashing as it goes; checksum mismatch removes the
    file and raises."""
    digest = hashlib.sha256()
    with requests.get(url, stream=True, timeout=30) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        done = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if on_progress and total:
                    on_progress(done / total)
    if digest.hexdigest() != sha256:
        os.remove(dest)
        raise RuntimeError(
            f"ollama download checksum mismatch (got {digest.hexdigest()})")


def _extract(archive: str, dest: str):
    """Unpack the platform archive into dest."""
    if _ASSET["kind"] == "zip":
        _extract_zip(archive, dest)
        return
    import zstandard  # Linux-only requirement; imported here so the module
    # itself loads (and unit-tests) on macOS
    with open(archive, "rb") as f:
        with zstandard.ZstdDecompressor().stream_reader(f) as stream:
            with tarfile.open(fileobj=stream, mode="r|") as tar:
                tar.extractall(dest, filter="data")


def _extract_zip(archive: str, dest: str):
    """Unpack a zip (ollama.exe + lib/ollama/… at the root) with a zip-slip
    guard — zipfile.extractall does NOT sanitize member paths the way
    tarfile's filter="data" does, and this archive crossed the network
    (sha256-pinned, but verify-then-extract beats trust)."""
    import zipfile
    base = os.path.realpath(dest)
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            target = os.path.realpath(os.path.join(dest, info.filename))
            if target != base and not target.startswith(base + os.sep):
                raise RuntimeError(
                    f"unsafe path in ollama archive: {info.filename!r}")
        z.extractall(dest)
