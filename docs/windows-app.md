<!-- Created by Pratik Sancheti / https://github.com/psancheti6666 -->
# Sotto for Windows — desktop app plan & decision log

Goal: ship Sotto as a normal downloadable Windows app — install, guided
first run, dictation works, zero terminal — matching the macOS experience
bar and reusing the Linux project's machinery (ONNX ASR, tkinter overlay,
download-at-first-run ollama, PyInstaller onedir, signature-gated updates
where the channel doesn't sign for us). Unlike Linux there is no permission
wall: no TCC, no udev — a keyboard hook and SendInput just work for a
normal user process. The hard problems are distribution (SmartScreen) and
the handful of POSIX idioms in the codebase. This document is the single
source of truth for decisions and milestone order — read it before any
Windows work; settled questions don't get reopened. Scouting facts were
recorded 2026-07-18 at the bottom of docs/linux-app.md — they are inputs
here, not re-derived.

**Hardware reality:** no local Windows machine. A friend's Windows 11
box is available for SCARCE, BATCHED validation rounds. Therefore W1 (CI
on windows-latest) lands BEFORE any feature work, every milestone ships
macOS-runnable units, and the friend checklists below are batched — the
Linux project's "green then next run fails" pattern must not repeat.

## Decisions (settled — don't re-litigate)

| Decision | Choice | Why |
|---|---|---|
| Hotkey mechanism | **pynput keyboard listener** (WH_KEYBOARD_LL under the hood) — the same `HotkeyListener` class macOS uses, with a Windows key-swallowing filter. Right Ctrl default (parity with Linux). | Scouted: pynput already uses the low-level hook on Windows; RegisterHotKey has no release event. pynput's `win32_event_filter` + `suppress_event()` allows SELECTIVE swallowing — so the full macOS gesture set (hold, hold+Space→hands-free with Space swallowed, double-tap, Escape-cancel swallowed, combo pass-through) is plausibly available, unlike Linux. W2 verifies swallowing live before the gesture set is committed; if selective suppression proves unreliable, fall back to the Linux gesture set (double-tap hands-free, Escape passes through). |
| Injection | **SendInput `KEYEVENTF_UNICODE`** primary + clipboard-paste (Ctrl+V) fallback — the same mode-router shape as `inject.py`, via pynput's Controller (which already emits unicode SendInput). | Scouted. Windows Terminal unicode quirks push terminals onto the paste path → default `keystroke_apps` excludes terminals, same policy as Linux. UIPI caveat documented honestly: hooks and SendInput don't reach elevated (admin) windows — dictation into an admin terminal needs Sotto elevated, which we do NOT do by default. |
| ASR | Existing **ONNX backend, CPUExecutionProvider** (`asr_onnx.py` unchanged). | Scouted: DirectML immature for encoder-decoder ASR. The backend already runs on the M3 for tests. |
| LLM engine | **Ollama never bundled**; probe-adopt a reachable instance first, else download the pinned, sha256-verified **Windows zip** to `~/.sotto/runtime/ollama` at first run (the L4 machinery, new platform suffix). NOT the ollama desktop installer. | The installer runs its own tray/autostart (scouted) — adopting it means two trays and an unmanaged daemon. The zip gives the same spawn-and-own lifecycle as Linux: our child, killed on quit. Downloads ≥100 MB stay behind the explicit "OK, download" consent (the Linux precedent). |
| Mic | **sounddevice / WASAPI** — the wheel bundles its own PortAudio DLL on Windows. | No system dependency at all; simpler than Linux. |
| Overlay | **Reuse `overlay_tk`** as-is first; Windows-specific polish (true click-through via `WS_EX_TRANSPARENT`) only if the friend round shows the capsule stealing clicks. | tkinter ships in python.org Windows builds; `overrideredirect` + `-topmost` + `-transparentcolor` are supported. Don't build a second overlay before the first one is proven inadequate. |
| Tray | **pystray Win32 backend** — its native, best-supported one. Menu: Insights, Check for Updates…, Quit — same `_menu_items` contract as Linux. | Shell tray is a first-class Windows citizen; no AppIndicator-style fragility. pystray gains a `sys_platform == "win32"` marker in requirements (same minor-pin + rationale as the Linux line). |
| Quit path | **Callback-based, not SIGINT.** Tray Quit calls a small `platform.request_quit()` that schedules `_root.destroy()` onto the tk loop (`after(0)`), with the existing SIGINT path kept for Ctrl+C in a console. | `tray_linux._quit`'s `os.kill(pid, SIGINT)` is a POSIX idiom: on Windows `os.kill` with SIGINT only reaches console process groups — from a windowed (no-console) PyInstaller app it is unreliable-to-fatal. tk's `after` is thread-safe and the destroy lands on the loop that owns the root. |
| Sounds | **Windows system sounds only** via `winsound.PlaySound(alias/Media wav, SND_ASYNC)` — event mapping decided in W5 by auditioning, like the macOS/Linux tables. Never Wispr's WAVs (copyrighted). | System-shipped sounds on the user's machine are not redistributed — the exact freedesktop-theme approach. |
| Data dir | **`%USERPROFILE%\.sotto`** — automatic: `config.py` already uses `os.path.expanduser("~/.sotto")`. | Cross-platform consistency (scouted; chosen over `%APPDATA%`): one doc story, one backup story, and the updater/first-run code paths stay shared. |
| App bundling | **PyInstaller onedir** on **windows-latest**, spec checked in (`winapp/sotto_win.spec`), `--smoke` flag importing every lazily-selected Windows backend — the exact L3 pattern. | Same rationale as Linux: onefile re-extracts hundreds of MB per launch; the smoke flag is the no-hardware safety net, run by CI on every build. |
| Distribution | **Microsoft Store MSIX is the leading path** ($0 individual registration since Sept 2025; Store-signed = no SmartScreen), **gated on ONE early live test** (W6) that WH_KEYBOARD_LL + SendInput work under MSIX `runFullTrust` — BEFORE any packaging milestone is built on it. Fallback, in order: unsigned Inno Setup (documented "More info → Run anyway" friction), Certum open-source cert (~€25–49). | Scouted. The runFullTrust test is cheap (a hello-world MSIX of the W2 hotkey probe) and decides the whole channel; committing first and testing later is how expensive reversals happen. |
| Updater | **Channel-dependent, same security bar as Linux.** Store path: the Store updates the app; Sotto's updater stays disabled (`bundle_type()` → "msix" = silent). Direct path (Inno): reuse `update.py`'s pure tier + a Windows backend that downloads installer + detached `.sig`, verifies RSA-SHA256 against the pinned committed pubkey (`sotto-release.pub` — same key infra as Linux) BEFORE executing; a package-name or URL check alone is false assurance. | The L8 lesson, applied from day one. No pkexec analogue needed — the installer runs as the user (per-user install dir) or triggers one UAC prompt. |
| Insights window | **WebView2** (preinstalled on Win11, auto-updated on Win10) hosting the unchanged dashboard — `insights_windows.py` with the SAME public surface as macOS/Linux (`configure`/`available`/`show_soon`), browser-tab fallback, sticky failure flag. Host library decided in W7 between **pywebview (leading)** and a hand-rolled WebView2 COM host, recorded here when validated. | One window-layer idea across three platforms (macOS hand-rolled WKWebView, Linux hand-rolled WebKitGTK, Windows = thinnest reliable WebView2 host). Hand-rolling WebView2 means COM + a Win32 message pump — pywebview does exactly that behind one function and runs its loop off-main on Windows; paying a dependency is honest when the alternative is ~500 lines of COM. The Linux-rejection reason (pywebview demands the main thread) is macOS/GTK-specific — verify the off-main claim in W7 before recording the final row. |
| First run | **No permission walkthrough** — Windows needs no grants for hooks/SendInput/mic (mic consent is a one-time OS prompt). First run = welcome + explicit model/ollama download consent screen (`firstrun_tk` reused) + optional Start-at-login (Startup-folder shortcut or MSIX StartupTask). | Reuse over rebuild; the Tk windows are already cross-platform. Honest copy: what downloads, how big, where it lives. |
| Arch | **amd64 only** initially; arm64 Windows deferred (`asset_suffix()` returns None there → updater silent — the L8 pattern). | No test hardware; friend's box is amd64. |

## Open decisions

- **MSIX vs Inno** — resolved by the W6 runFullTrust live test (leading:
  MSIX/Store). If MSIX: does the Store build replace CI's artifact path or
  complement it (sideload-able MSIX artifact for the friend rounds)?
- **pywebview vs hand-rolled WebView2 host** — resolved in W7 (leading:
  pywebview, pending its off-main-thread claim verified on Windows).
- **Ollama Windows zip prune** — the zip carries GPU libs; measure on the
  friend's box before pruning (the Linux precedent: prune after
  measurements, not before).
- **Key-swallowing reliability** — W2 decides the gesture set (macOS-parity
  vs Linux-parity); the answer gets recorded in the decision table.

## Milestones (one issue + PR each, in order)

Friend-test baseline: Windows 11 amd64, python.org 3.11 where a checkout
is needed; every round attaches `%USERPROFILE%\.sotto\sotto.log`.

1. **W1 — CI first: windows-latest job.** Unit tier green on real Windows
   BEFORE any feature work. `tests.yml` gains `windows-latest` to the
   matrix; fix what it surfaces — expected: requirements markers (`pynput`
   needs `sys_platform == "darwin" or sys_platform == "win32"`; evdev/
   zstandard/pystray stay linux/linux/linux+win32), POSIX-path or
   signal assumptions in tests, `say`-dependent tiers already gated behind
   flags. Deliverable: the unit tier is a Windows regression gate for every
   later PR. Friend: none (CI is the point).
2. **W2 — Hotkey on Windows.** `platform/__init__.py` gains `IS_WINDOWS`;
   `app._make_listener` selects the pynput `HotkeyListener` on Windows;
   add the `win32_event_filter` swallowing filter (Space/Escape during
   gestures) behind the existing gesture state machine. Units: gesture
   state machine already covered; new filter logic unit-tested with fake
   events on macOS. Friend (batched with W3): hold-to-dictate, double-tap,
   hold+Space, Escape-cancel, fn-combo pass-through — which swallow
   cleanly? Result decides the gesture-set row above. Also the FIRST HALF
   of the MSIX gate: package the hotkey probe as a runFullTrust MSIX and
   confirm the hook fires (W6 completes the gate).
3. **W3 — Injection.** `inject_windows.py` (pynput SendInput unicode type
   + Ctrl+V paste, clipboard save/restore via pyperclip — already a dep);
   `platform/windows.py` (`active_app_id()` from GetForegroundWindow →
   exe name via ctypes, `alert()` via MessageBox, `open_url` via
   os.startfile); default `keystroke_apps` excludes WindowsTerminal/
   conhost/powershell. Units: argv/mode-router logic, fake ctypes. Friend
   (with W2): dictate into Notepad, a browser, Windows Terminal (paste
   path), non-ASCII text.
4. **W4 — Runtime: mic + ASR + LLM.** sounddevice/WASAPI capture,
   `make_asr()` "auto" → ONNX on Windows (likely zero code), ollama
   Windows-zip suffix in `ollama_runtime.py` (pinned version + sha256,
   zip extraction, `ollama.exe` spawn with clean lifecycle — the atexit
   kill path verified without POSIX signals). Units: resolver/download
   logic with a fake platform. Friend: cold first run downloads engine +
   model with visible progress; end-to-end dictation lands text.
5. **W5 — Overlay + sounds + quit path.** overlay_tk verified on Windows
   (capsule position vs taskbar, transparency); `platform/windows.py`
   sounds via winsound (event table auditioned and recorded here); tray
   Quit → `request_quit()` callback (decision above) wired for ALL
   platforms' tray without breaking the Linux SIGINT contract. Units:
   quit-callback plumbing, sound-alias table. Friend: capsule visible
   over apps, sounds distinct, Quit exits clean (Task Manager check).
6. **W6 — Packaging + the MSIX decision.** `winapp/` (build script +
   spec + `--smoke`), CI builds onedir and smokes it (W1 job extended);
   MSIX packaging of the real app, sideloaded on the friend's box:
   hook + SendInput + mic + model download under runFullTrust = the gate.
   PASS → Store channel confirmed (decision table updated), submission
   dry run. FAIL → Inno fallback activated, signature-gated updater
   (W8) becomes mandatory. Friend: install from the packaged artifact,
   full first run, dictation — zero terminal.
7. **W7 — Insights window on WebView2.** `insights_windows.py` mirroring
   the macOS/Linux surface; pywebview-vs-hand-rolled resolved and
   recorded; browser fallback sticky; CI smoke renders the real dashboard
   in the real WebView2 (the L11 xvfb-smoke equivalent — windows-latest
   has WebView2). Units: ladder/gating/dispatch with fakes (the
   `test_insights_linux` pattern). Friend: native window, close/reopen,
   dictionary save, theme, dictate-while-open.
8. **W8 — Updater backend (channel-dependent).** Store: `bundle_type()`
   "msix" → updater silent, Store handles it. Inno: Windows backend with
   the signature gate (pinned pubkey, verify BEFORE execute, no-downgrade)
   + `asset_suffix()` `-setup.exe`; release workflow signs like the Linux
   artifacts. Units: the full L8-style gate matrix. Friend: vN → vN+1
   update cycle live.
9. **W9 — Docs + release dry run.** README "Download (Windows)" (honest
   SmartScreen/UAC/UIPI notes), platform table, network-calls list
   updated; release workflow dispatch builds all platforms green; tag.

## Constraints that apply to every milestone

- All of Sotto's ground rules: 100% local at runtime (GitHub release
  check + first-run downloads are the only non-localhost traffic,
  documented), faithful cleanup untouched, user data in `~/.sotto`, no
  copyrighted assets, $0 recurring.
- The repo keeps working exactly as today for macOS (git checkout and
  bundle) and Linux (deb/AppImage) — Windows code is additive, selected
  behind `IS_WINDOWS`.
- Every new logic path ships macOS-runnable units in
  `tests/test_pipeline.py` (monkeypatch/fake style); CI (W1's job) is the
  first real execution of everything; friend rounds are batched with
  written checklists and results recorded in this file.
- Every new source file starts with the creator header.
- Windows-specific honesty in user-facing copy: UIPI (no dictation into
  elevated windows), SmartScreen behavior on the non-Store path, exactly
  what downloads at first run and how big it is.
