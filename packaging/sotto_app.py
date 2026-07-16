# Created by Pratik Sancheti / https://github.com/psancheti6666
"""Sotto.app entry point for the py2app bundle.

The terminal path (./run.sh → python -m sotto) does not use this file.
"""
import locale

# Finder-launched apps get no LANG, and py2app's launcher ignores PYTHON* env
# vars, so open() defaults to ASCII and chokes on the model's UTF-8
# config.json. Pin the C locale so getpreferredencoding() says UTF-8.
try:
    locale.setlocale(locale.LC_CTYPE, "UTF-8")
except locale.Error:
    pass

from sotto.app import main

if __name__ == "__main__":
    main()
