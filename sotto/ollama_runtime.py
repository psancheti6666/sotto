# Created by Pratik Sancheti / https://github.com/psancheti6666
"""Linux LLM engine resolution: find or download the ollama runtime.

The Linux app never bundles ollama (the linux-amd64 tarball is ~1.37 GB —
docs/linux-app.md). llm_server.ensure() still adopts a *reachable* server
first; this module only answers "which binary can we spawn": a system
`ollama` on PATH (respects a user's existing install), else the copy a
previous first-run downloaded to ~/.sotto/runtime/ollama, else None —
in which case the first-run download screen calls download().

The pinned release ships as .tar.zst; Python 3.11's tarfile has no zstd
support, so extraction streams through the zstandard package (Linux-only
requirement). Shipped unpruned initially — CPU-only pruning follows the
live measurements planned for milestone L6.
"""

import hashlib
import logging
import os
import shutil
import tarfile
import tempfile

import requests

log = logging.getLogger("sotto")

# Pinned to the same ollama version macapp/build_app.sh bundles on macOS.
# sha256 is GitHub's published asset digest for this release.
OLLAMA_VERSION = "0.32.1"
OLLAMA_URL = ("https://github.com/ollama/ollama/releases/download/"
              f"v{OLLAMA_VERSION}/ollama-linux-amd64.tar.zst")
OLLAMA_SHA256 = "83b1f22841eb7f6c4900c6797f960ebaa09466874442ea5b8ae3da6980d3914c"

RUNTIME_DIR = os.path.expanduser("~/.sotto/runtime/ollama")


def installed() -> str | None:
    """The previously-downloaded runtime's binary, or None."""
    path = os.path.join(RUNTIME_DIR, "bin", "ollama")
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
        raise RuntimeError("ollama runtime extracted but bin/ollama is missing")
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
    """Unpack a .tar.zst into dest (bin/ollama + lib/ollama/…)."""
    import zstandard  # Linux-only requirement; imported here so the module
    # itself loads (and unit-tests) on macOS
    with open(archive, "rb") as f:
        with zstandard.ZstdDecompressor().stream_reader(f) as stream:
            with tarfile.open(fileobj=stream, mode="r|") as tar:
                tar.extractall(dest, filter="data")
