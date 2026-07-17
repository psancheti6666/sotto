# Created by Pratik Sancheti / https://github.com/psancheti6666
"""PyInstaller runtime hook: make the bundled libportaudio findable.

sounddevice resolves PortAudio on Linux via ctypes.util.find_library, which
only consults the system ldconfig cache — it can never see a library inside
the onedir, so on a machine without libportaudio2 the import dies even though
we ship the .so. This hook runs before any user module (sounddevice binds
find_library at its import), and answers 'portaudio' with the bundled path.
"""

import ctypes.util
import glob
import os
import sys

_orig_find_library = ctypes.util.find_library


def _find_library(name):
    if name == "portaudio":
        hits = sorted(glob.glob(os.path.join(sys._MEIPASS, "libportaudio.so*")))
        if hits:
            return hits[0]
    return _orig_find_library(name)


ctypes.util.find_library = _find_library
