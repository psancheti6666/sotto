<!-- Created by Pratik Sancheti / https://github.com/psancheti6666 -->
# runtime-x86_64 — vendored AppImage static runtime

- Source: https://github.com/AppImage/type2-runtime (release tag `continuous`)
- Fetched: 2026-07-18
- SHA-256: `1cc49bcf1e2ccd593c379adb17c9f85a36d619088296504de95b1d06215aebbf`
- License: MIT (see the upstream repo)
- ELF: static-pie, x86-64, stripped — no libfuse2 dependency (uses the
  system's fuse3/fusermount3 when present, self-extraction otherwise via
  `APPIMAGE_EXTRACT_AND_RUN=1` / `--appimage-extract-and-run`).

Vendored (rather than downloaded at build time) so the AppImage build is
fully pinned: upstream's `continuous` tag mutates, and a silently-changed
runtime inside a signed release artifact is exactly what the build must
never absorb. make_appimage.sh verifies this hash before use. To upgrade:
download the new runtime, update the hash here and in make_appimage.sh in
the same commit, and let CI's bare-container smoke prove it still boots.
