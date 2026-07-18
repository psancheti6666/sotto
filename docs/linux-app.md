<!-- Created by Pratik Sancheti / https://github.com/psancheti6666 -->
# Sotto for Linux — desktop app plan & decision log

Goal: ship Sotto as a normal downloadable Linux app, so non-developers never
touch a terminal — not for install, not for permissions, not for updates.
The Linux *runtime* already works (evdev hotkey, tkinter overlay,
xdotool/wtype/ydotool injection, ONNX ASR, freedesktop sounds, `~/.sotto`
log); what's missing is a distribution. Ubuntu is first-class, Fedora and
other majors work via a second artifact, X11 and Wayland both. This document
is the single source of truth for the decisions already made and the
milestone order — read it before working on the Linux app so settled
questions don't get reopened. Windows scouting notes live at the bottom.

## Decisions (settled — don't re-litigate)

| Decision | Choice | Why |
|---|---|---|
| Input mechanism | **Keep evdev (hotkey) + the existing xdotool/wtype/ydotool chain (injection).** XDG portals are not a replacement. | Researched 2026-07-18: the GlobalShortcuts portal delivers press/release but only exists on GNOME 48+/KDE, the compositor+user pick the actual binding, and binding a *bare held modifier* (Right Ctrl) is likely impossible; InputCapture is pointer-barrier-triggered only; the RemoteDesktop injection portal needs a consent grant and shows a "screen is being controlled" indicator while typing (its restore-token persistence across reboots was broken for a while — KDE bug 480235, fixed in Plasma 6.1.1 — but the consent+indicator UX and keysym-by-keysym typing remain). evdev+uinput is what espanso/ydotool/dotool all do in 2026. |
| Primary artifact | **`.deb`** (`Sotto-<ver>-amd64.deb`), double-click install via Ubuntu App Center. | Only format where permissions become part of install: `postinst` runs as root, lays down the udev rule + polkit policy, and runs `udevadm control --reload && udevadm trigger` — the install's ONE GUI password prompt is also the permission grant, and the hotkey works at first launch with no re-login. |
| Secondary artifact | **AppImage** (`Sotto-<ver>-x86_64.AppImage`) built with the **static (type-3) runtime**, for non-deb distros. Community-tested. | Static runtime avoids the libfuse2 dependency (stock Ubuntu 24.04+ doesn't preinstall `libfuse2t64`). Can't pre-install root material, so its first "Fix" click bootstraps the same rule/policy/helper via a generic pkexec admin prompt. |
| Flatpak / Snap | **Rejected** (Flatpak) / **deferred** (Snap). | Flathub explicitly refuses `--device=input` and the sandbox can't install udev rules or run pkexec — a dead end for an evdev app. Snap's `raw-input`/`uinput` interfaces need manual Canonical review per snap; maybe later. |
| Permissions | **udev `uaccess` rule** (`60-sotto-input.rules`: seat-based ACL on `/dev/input/event*` + `/dev/uinput`), installed by the .deb (or the AppImage's pkexec bootstrap). Fix button also runs an immediate `setfacl` for the current session; a `GROUP="input"` fallback rule stays staged in-repo, inactive. | `usermod -aG input` needs a re-login, and postinst has no reliable username (App Center installs via PackageKit — no `$SUDO_USER`); uaccess grants *whoever is at the seat*, immediately and persistently. Reports are mixed on ACLs landing without logout → the walkthrough checks actually `open()` a device (never a cached heuristic) and friend tests verify with `getfacl`. README documents the exposure honestly: same keylogging-exposure class as the `input` group; Sotto reads keys only while the hotkey is held. |
| Root-executed material | Everything that ever runs as root lives in `linuxapp/deb/` as reviewable, shellcheck-ed files (`sotto-perms` helper, udev rule, polkit policy, postinst) — never generated inline by build scripts. The AppImage embeds the byte-identical files. | Auditable; the polkit policy pins the helper's absolute path. |
| App bundling | **PyInstaller onedir**, spec checked in (`linuxapp/sotto.spec`), built on an **ubuntu-22.04 / glibc 2.35 baseline**. A `--smoke` flag imports every lazily-selected Linux backend. | onefile would re-extract hundreds of MB every launch. Old-glibc builds run on everything newer (Ubuntu 22.04+, Debian 12+, Fedora 36+). The smoke flag is the no-Linux-hardware safety net for missing hidden imports, run by CI on every build. |
| LLM engine | **Ollama is never bundled in the artifact.** Probe-adopt a reachable/system ollama first; else download (pinned, sha256-verified) to `~/.sotto/runtime/ollama/` on the first-run download screen, unpruned initially. | The Linux tarball is ~1.44 GB (bundles CUDA libs) — bundling would quintuple the artifact. Users download models at first run anyway. CPU-only pruning happens after live measurements (which libs a CPU box actually loads). Format note for L4: recent ollama releases ship `.tar.zst`, and Python 3.11's `tarfile` can't read zstd — the pinned version determines whether L4 needs a zstd decompressor or pins a `.tgz`-era release. |
| Updater | Reuse `update.py`'s unit-tested pure tier (parse/evaluate/due/mark_checked); its scheduled-loop / single-flight / download-progress scaffolding gets refactored to dispatch per platform in L8 (today those call macOS surfaces inline — they are reusable *shapes*, not reusable code yet). Linux backends: .deb installs via `pkexec sotto-perms install-update` (which uses `apt-get install ./file` so new Depends resolve); AppImage **self-replaces** `$APPIMAGE` and re-execs (no root, no prompt). Asset naming extends the existing `Sotto-<ver>-<arch>.<ext>` convention. | Same update UX as macOS. Source checkouts keep `run.sh`'s git-pull; the new `bundle_type()` (an L8 deliverable — today's gate is the macOS-only `enabled()`) returns None there so the updater stays silent. zsync deltas deferred (unproven savings at ~1 GB). |
| Dialog/alert surface | `zenity` → `kdialog` → `notify-send` → log-only, as **subprocesses** (zenity is a deb Depends). Tk is used ONLY for the two first-run windows. | tkinter isn't thread-safe and the overlay owns the sole mainloop; child-process dialogs work from any thread, even with the indicator off, and unit-test as pure argv builders. Also fills today's biggest Linux gap: `platform.alert()` is currently a silent no-op. |
| Tray | **Best-effort** pystray tray (Insights, Check for Updates…, Quit); app is fully usable tray-less (log line when no tray host exists). | Ubuntu ships the AppIndicator extension by default; vanilla GNOME (Fedora) hides trays without an extension. The dashboard + overlay are Sotto's primary surfaces anyway. |
| Dev/release split | **None on Linux** — one package identity; a source checkout is "dev". | No TCC analogue to protect (the macOS split existed because same-bundle-id copies fought over TCC rows). |
| Arch | **amd64 only** initially; arm64 Linux deferred (L8's `asset_suffix()` will return None there → updater silent). | No test hardware; the friend's machine is amd64. |
| Permission watchdog | **Not ported** to Linux initially. | Revocation isn't a Settings toggle on Linux; the evdev listener's retry-with-alert and the injection chain's runtime fallthrough already cover device loss. |

## Open decisions

- **Ollama CPU-only prune list** — resolved in principle (prune after L6
  measurements on the friend's CPU-only machine), exact `lib/ollama` delete
  list TBD. Alternative if disk pressure complains earlier: prune CUDA
  immediately and accept CPU-only inference (fine on 8 GB RAM).
- **Bundling AGPL ydotool/wtype inside the AppImage** (with licenses +
  source links) — planned, for zero-terminal on non-Ubuntu GNOME-Wayland.
  Alternative: an honest red walkthrough row telling that slice to install
  ydotool via their package manager.
- **Windows distribution channel** — decided when Windows work starts; see
  scouting notes below (leading option: Microsoft Store, now free for
  individuals and Store-signed = no SmartScreen).

## Milestones (one issue + PR each, in order)

Friend-test baseline: Ubuntu 24.04, X11 + Wayland sessions where noted;
every test round attaches `~/.sotto/sotto.log`.

1. **L1 — this document + README stub** — ✅ done (PR #39, issue #38).
2. **L2 — Linux `alert()`.** ✅ done (PR #41, issue #40). `platform/linux.py` zenity→kdialog→notify-send→log
   chain (pure `_alert_argv()` builder, fire-and-forget Popen). Unit:
   `test_linux_alert`. Friend: one-line alert call → zenity screenshot.
   Fixes today's silent-no-op alert gap; every later milestone's errors
   become visible through it.
3. **L2b — evdev permission misdiagnosis fix + dashboard BrokenPipeError**
   ✅ done (PR #43, issue #42; diagnosed 2026-07-17 from the friend's Ubuntu
   test; prerequisite for L3's pass criterion). python-evdev's `list_devices()` silently filters
   devices failing `os.access(R_OK|W_OK)`, so a user without input access
   gets an EMPTY list — `_open_keyboards`' `denied` flag never trips and
   `run()` loops the misleading "no keyboard with a 'ctrl_r' key found"
   warning forever instead of raising `PERMISSION_HELP`. Fix: patchable
   module helper `_list_raw()` globbing `/dev/input/event*`; in
   `_open_keyboards`, no keyboard found AND (`denied` OR raw-minus-accessible
   non-empty) → `raise RuntimeError(PERMISSION_HELP)` (flows into
   `app._run_listener`'s existing alert+retry). Genuinely-no-keyboard (all
   raw accessible, none keyboard-capable) keeps the current warning. Also:
   `dashboard._respond` swallows BrokenPipeError/ConnectionResetError
   (browser closed mid-response). Units: fake-evdev tests in the
   `test_evdev_gestures` style covering all three cases. Friend: none —
   L3's live test IS the check.
4. **L3 — PyInstaller onedir + CI smoke.** `linuxapp/{build_app.sh,sotto.spec,
   sotto_linux.py}`; CI builds and runs `--smoke` under xvfb, uploads a
   tarball on dispatch. Unit: `test_smoke_imports` (smoke list stays in sync
   with the backend selectors). Friend: run the onedir → the expected result
   is the L2 alert showing the permission help (no perms yet — that IS the
   pass; requires L2b, without which evdev's silent filtering loops "no
   keyboard found" and no alert ever fires).
5. **L4 — Ollama runtime resolution.** `sotto/ollama_runtime.py` (resolve:
   reachable URL → `which ollama` → `~/.sotto/runtime/ollama` → None;
   download pinned+sha256 with progress) wired into
   `llm_server.bundled_binary()`. Unit: `test_ollama_runtime`.
6. **L5 — Linux first-run: checks + root helper + Tk windows.** ✅ code done
   (PR #49, issue #48). `sotto/firstrun_linux.py` (rows: Keyboard access /
   Typing / Models info / Start-at-login optional; checks actually open
   devices and run the injection probes), `sotto/firstrun_tk.py` (walkthrough
   + download screen; walkthrough re-verifies on a 1 s tick, the download
   screen polls its queue at 100 ms; Start-click re-verify as AppKit,
   `os.execv` relaunch), `linuxapp/deb/{60-sotto-input.rules, polkit policy,
   sotto-perms, sotto-uinput.conf}`, app.py Linux gate, bundle-aware
   `PERMISSION_HELP`. **Security (sweep):** the root helper exposes only
   `apply` (grants the PKEXEC_UID user — never an argv-supplied name — access
   to their own seat) and `verify` (read-only); polkit pins exec.path but
   can't constrain argv, so `install-update` moved to **L8** (signature-gated)
   and `bootstrap` to **L9**. Units: `test_firstrun_linux`,
   `test_tk_firstrun_windows`; shellcheck on `deb/*` a CI merge gate. Friend
   (**UI preview only** — the helper + udev rule aren't installed until the
   .deb, so a checkout can't grant real access): `SOTTO_FIRSTRUN=1` →
   walkthrough renders, rows reflect the honest checks, gating enables/
   disables Start → screenshot. The decisive **uaccess-lands-without-logout**
   check (Fix → polkit → `getfacl`) needs the real .deb → it lives in L6.
7. **L6 — .deb + release pipeline** (the big one). ✅ code done (PR #51,
   issue #50). `make_deb.sh` + packaging payload + icons; **the `/usr/bin/sotto` launcher MUST export
   `SOTTO_BUNDLE=deb`** (the entire L5 first-run gate + bundle-aware
   `PERMISSION_HELP` are dormant without it); **the postinst MUST install
   `/usr/libexec/sotto/sotto-perms` as `0755 root:root`** — if it's ever
   group/world-writable the pinned polkit action becomes a local root
   escalation (flagged by L5's security sweep; assert it in `test_deb_layout`
   / `dpkg-deb --contents`); CI **installs the deb and smokes `/usr/bin/sotto`**;
   release job glob gains `linux-*`. Unit: `test_deb_layout`. Friend, fresh state: double-click → App Center → ONE
   password prompt → launch from app grid → **Keyboard row expected already
   green**; if gray, Fix → polkit → **paste `getfacl /dev/input/event0
   /dev/uinput`** (the uaccess verification); also note the ydotool version
   (`ydotool --version`) — jammy ships 0.1.8, noble ships 1.x, and the
   injection chain should be sanity-checked against whichever the box has →
   walkthrough → downloads
   → ready alert → dictate into gedit (Wayland) and a terminal (paste path);
   send timing lines, `getfacl`, `groups` (proves no input-group needed),
   ydotoold user-unit status if GNOME-Wayland. Hedge: App Center's local-deb
   flow is documented to install, but its Depends resolution is NOT — if it
   balks on dependencies, fall back to GNOME Software / gdebi and record
   which path worked; a Depends hiccup is a finding to design around, not a
   milestone failure.
8. **L7 — Tray (best-effort).** `sotto/tray_linux.py` via pystray: Insights
   + Quit (full clean shutdown incl. `llm_server.shutdown`); the "Check for
   Updates…" item appears only once L8's backend lands (`update.enabled()`
   stays false on Linux until then). Unit: `test_tray_menu`. Friend: tray
   visible on Ubuntu; Quit exits; tray-less GNOME still works.
9. **L8 — Updater Linux backend.** `sotto/update_linux.py` (`bundle_type()`,
   install steps, dialog/banner/progress argv builders), `update.py` grows
   pure `asset_suffix()` + suffix-based `evaluate()` + Linux dispatchers
   (today `_offer`/`_ask` import AppKit unconditionally and `evaluate()`
   would match a `-intel.dmg` on Linux — this milestone is what makes any
   Linux update path real); tray gains "Check for Updates…";
   `SOTTO_RELEASES_API` test seam. Units: `test_update` (new signature),
   `test_update_linux`. Friend: deb vN → test release vN+1 → Check for
   Updates → Update Now → polkit prompt → relaunches as vN+1. (AppImage
   self-replace is verified in L9, once that artifact exists.)
10. **L9 — AppImage.** `make_appimage.sh` + AppRun + static runtime + embedded
    setup payload + bundled wtype/ydotool (+ licenses) + pkexec `bootstrap`
    flow. Unit: `test_appimage_bootstrap`. CI: AppImage `--smoke` on the
    runner (proves no libfuse2 needed). Friend: document the exact
    right-click→Execute path, generic polkit bootstrap prompt (expected),
    full setup, dictation; delete the AppImage → permissions persist; run
    the updater's AppImage self-replace against a test release (deferred
    here from L8).
11. **L10 — Docs + release dry run.** This file's milestones marked done with
    live-test outcomes; README "Download for Linux" (incl. the uaccess
    security note); tag → draft release carries 2 DMGs + deb + AppImage →
    publish. Friend: fresh-machine end-to-end from the published URL, timed,
    counting every prompt — the true zero-terminal test.

## Constraints that apply to every milestone

- All of Sotto's ground rules: 100% local at runtime (the GitHub releases
  check and first-run downloads — now including the ollama tarball — stay
  the only non-localhost traffic, documented in README), faithful cleanup,
  user data stays in `~/.sotto`, no copyrighted assets, $0 recurring.
- The repo must keep working exactly as today for git-clone + `./run.sh`
  (macOS app users are untouched; source users keep the git-pull update).
- No Linux hardware exists here: every new logic path ships with macOS-runnable
  units in `tests/test_pipeline.py` (monkeypatch style), CI builds/smokes on
  ubuntu runners, and each milestone's friend-test checklist above is the
  live verification. Results get recorded in this file as they happen.
- Every new source file starts with the creator header; shell files after
  the shebang.

## Windows scouting (recorded 2026-07-18 — facts only, no implementation)

- **Hotkey**: `WH_KEYBOARD_LL` low-level hook is the standard press/release
  mechanism (RegisterHotKey has no release event) — and pynput already uses
  it on Windows, so `HotkeyListener` is close to working as-is.
- **Injection**: `SendInput` with `KEYEVENTF_UNICODE` primary + clipboard-
  paste fallback (same mode-router shape as `inject.py`); known Unicode
  quirks in Windows Terminal push terminals onto the paste path.
- **ASR**: onnxruntime CPU EP (DirectML is immature for encoder-decoder ASR).
- **LLM**: Ollama has a per-user, no-admin Windows installer but runs its own
  tray/autostart — needs explicit management if adopted.
- **Distribution / SmartScreen**: unsigned installers get "Windows protected
  your PC → More info → Run anyway", and hash reputation resets every
  release; EV certs no longer auto-bypass. **Microsoft Store individual
  registration is now free (Sept 2025) and the Store signs the submitted
  MSIX** — the leading $0 no-SmartScreen path, pending one live test of the
  keyboard hook + SendInput under MSIX `runFullTrust`. Fallbacks: unsigned
  Inno Setup installer (documented friction) or a Certum open-source cert
  (~€25–49 one-time).
- **Porting audit list**: fork/exec patterns, POSIX path assumptions, sounds
  (winsound/system sounds), the detached-shell relaunch idiom, data dir
  (`%USERPROFILE%\.sotto` for cross-platform consistency vs `%APPDATA%`).
