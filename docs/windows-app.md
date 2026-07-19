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
| Quit path | **Callback-based, not SIGINT.** Tray Quit calls a small `platform.request_quit()` that routes through overlay_tk's existing command queue (drained by the tick — the codebase's own cross-thread-tk convention), with the existing SIGINT path kept for Ctrl+C in a console. | `tray_linux._quit`'s `os.kill(pid, SIGINT)` is a POSIX idiom: on Windows `os.kill` with SIGINT only reaches console process groups — from a windowed (no-console) PyInstaller app it is unreliable-to-fatal. The queue route is unambiguously thread-safe (raw cross-thread `after` is only safe on threaded Tcl builds). |
| Single instance | **Named-mutex guard on Windows** (`CreateMutexW`, per-user name), wired into the existing `_acquire_instance_lock` — which today returns a no-op token on every non-Linux platform. | The Linux VM round's WORST finding (#63: two instances = double-typed text, port race). macOS gets a free backstop from LaunchServices; Windows does not — double-launching an installed exe or Startup-shortcut + manual launch is the easy case, and MSIX runFullTrust apps are multi-instance too. This plan exists to not repeat that lesson. |
| Sounds | **Windows system sounds only** via `winsound.PlaySound(alias/Media wav, SND_ASYNC)` — event mapping decided in W6 by auditioning, like the macOS/Linux tables. Never Wispr's WAVs (copyrighted). | System-shipped sounds on the user's machine are not redistributed — the exact freedesktop-theme approach. |
| Data dir | **`%USERPROFILE%\.sotto`** — automatic: `config.py` already uses `os.path.expanduser("~/.sotto")`. | Cross-platform consistency (scouted; chosen over `%APPDATA%`): one doc story, one backup story, and the updater/first-run code paths stay shared. |
| App bundling | **PyInstaller onedir** on **windows-latest**, spec checked in (`winapp/sotto_win.spec`), `--smoke` flag importing every lazily-selected Windows backend — the exact L3 pattern. | Same rationale as Linux: onefile re-extracts hundreds of MB per launch; the smoke flag is the no-hardware safety net, run by CI on every build. |
| Distribution | **Microsoft Store MSIX is the leading path** ($0 individual registration since Sept 2025; Store-signed = no SmartScreen), **gated on ONE early live test** (probe built in W2, exercised in Round A, completed on the real app in W7) that WH_KEYBOARD_LL + SendInput work under MSIX `runFullTrust` — BEFORE any packaging milestone is built on it. Fallback, in order: unsigned Inno Setup (documented "More info → Run anyway" friction), Certum open-source cert (~€25–49). | Scouted. The runFullTrust test is cheap (a hello-world MSIX of the W2 hotkey probe) and decides the whole channel; committing first and testing later is how expensive reversals happen. |
| Updater | **Channel-dependent, same security bar as Linux.** Store path: the Store updates the app; Sotto's updater stays disabled (`bundle_type()` → "msix" = silent). Direct path (Inno): reuse `update.py`'s pure tier + a Windows backend that downloads installer + detached `.sig`, verifies RSA-SHA256 against the pinned committed pubkey (`sotto-release.pub` — same key infra as Linux) BEFORE executing; a package-name or URL check alone is false assurance. | The L8 lesson, applied from day one. No pkexec analogue needed — the installer runs as the user (per-user install dir) or triggers one UAC prompt. |
| Insights window | **WebView2 via pywebview** (DECIDED, W8/PR #84): `insights_windows.py` with the SAME public surface as macOS/Linux (`configure`/`available`/`show_soon`), browser-tab fallback, sticky failure flag. `gui="edgechromium"` FORCED (a missing WebView2 runtime must fall back to the browser, never degrade to legacy MSHTML). **Close = hide is a hard requirement on Windows**, not just macOS parity: pywebview's loop ends when its last window is destroyed and cannot restart in-process — the closing handler cancels the close. pywebview minor-pinned (`>=5.3,<6`, win32 marker). | One window-layer idea across three platforms (macOS hand-rolled WKWebView, Linux hand-rolled WebKitGTK, Windows = thinnest reliable WebView2 host). Hand-rolling WebView2 means COM vtables + a Win32 message pump — ~500 lines of exactly the code a maintained library exists for. pywebview's main-thread requirement is macOS/Cocoa-specific; on Windows its loop runs on a daemon thread (the tk overlay owns the main thread, as everywhere). CI's `--smoke-webview` renders the real dashboard in the real WebView2 inside the frozen onedir; the live look is Round D. |
| First run | **No permission walkthrough**, but not zero checks: Windows needs no grants for hooks/SendInput, and mic consent is an OS prompt only on the MSIX path — a classic Win32 build is governed by the global "desktop apps may access your microphone" toggle, which fails SILENTLY when off. So: welcome + explicit model/ollama download consent screen + one honest mic row (check + `ms-settings:privacy-microphone` deep link) on the non-MSIX path + optional Start-at-login (Startup-folder shortcut or MSIX StartupTask). Built in **W5** by decoupling `firstrun_tk` from its module-level `firstrun_linux` imports (rows/statuses/gating/relaunch become parameters or a `firstrun_windows` sibling); the relaunch idiom is **spawn-then-exit**, not `os.execv` (broken semantics on Windows: new pid, argv mangling, console returns early). | The Tk windows are conceptually reusable but factually Linux-coupled today — the decoupling is named work, not an assumption. Honest copy: what downloads, how big, where it lives. |
| Windows config defaults | `load_config()` gains an `IS_WINDOWS` branch (it only has `IS_LINUX` today — Windows would inherit macOS defaults): `hotkey = "ctrl_r"` (pynput cannot map fn on Windows; the app would be dead on arrival), winsound event table, `keystroke_apps` covering terminals, `haptics = False`. Part of **W5**. | Same shape as the existing Linux branch; without it every table row above about defaults is fiction. |
| Arch | **amd64 only** initially; arm64 Windows deferred (`asset_suffix()` returns None there → updater silent — the L8 pattern). | No test hardware; friend's box is amd64. |

## Open decisions

- **MSIX vs Inno** — resolved by the runFullTrust live test (probe in
  Round A, real app in W7; leading:
  MSIX/Store). If MSIX: does the Store build replace CI's artifact path or
  complement it (sideload-able MSIX artifact for the friend rounds)?
- **pywebview vs hand-rolled WebView2 host** — resolved in W8 (leading:
  pywebview, pending its off-main-thread claim verified on Windows).
- **Ollama Windows zip prune** — the zip carries GPU libs; measure on the
  friend's box before pruning (the Linux precedent: prune after
  measurements, not before).
- **Key-swallowing reliability** — W2 decides the gesture set (macOS-parity
  vs Linux-parity); the answer gets recorded in the decision table.

## Milestones (one issue + PR each, in order)

Friend-test baseline: Windows 11 amd64, python.org 3.11 where a checkout
is needed; every round attaches `%USERPROFILE%\.sotto\sotto.log`.
**Friend rounds are exactly four, batched:** Round A after W3 (checkout:
hotkey + injection + the MSIX hook probe), Round B after W6 (frozen
onedir: first-run, dictation end-to-end, overlay, sounds, tray, quit,
single-instance), Round C after W7 (packaged install, zero-terminal — the
MSIX gate completes here), Round D after W9 (insights window + update
cycle). Milestones in between ship on CI + units alone.

1. **W1 — CI first: windows-latest job.** Unit tier green on real Windows
   BEFORE any feature work. `tests.yml` gains `windows-latest` in the
   matrix; fix what it surfaces — expected: requirements markers (`pynput`
   needs `darwin or win32`; evdev/zstandard stay linux, pystray gains
   win32), POSIX-path/signal assumptions, and the two exec-bit assertions
   that false-fail on Windows (`os.access(X_OK)` is true for any existing
   file there: "non-executable file ignored" in `test_llm_server`,
   "non-executable download ignored" in `test_ollama_runtime` — gate or
   branch them). `say`-dependent tiers are already flag-gated.
   Deliverable: the unit tier is a Windows regression gate for every
   later PR. Friend: none (CI is the point).
2. **W2 — Hotkey on Windows.** `platform/__init__.py` gains `IS_WINDOWS`;
   `app._make_listener` selects the pynput `HotkeyListener` on Windows;
   add the `win32_event_filter` swallowing filter (Space/Escape during
   gestures) behind the existing gesture state machine. Units: gesture
   state machine already covered; new filter logic unit-tested with fake
   events on macOS. Also build the standalone hotkey probe + a
   runFullTrust MSIX wrapping of it (first half of the MSIX gate).
3. **W3 — Injection.** `inject_windows.py` (pynput SendInput unicode type
   + Ctrl+V paste, clipboard save/restore via pyperclip — already a dep;
   explicit win32 branch in the `inject.py` router — today's non-Linux
   fallthrough builds the Mac injector); `platform/windows.py`
   (`active_app_id()` from GetForegroundWindow → exe name via ctypes,
   `alert()` via MessageBox, `open_url` via os.startfile). Units: router/
   argv logic, fake ctypes. **→ Round A** (checkout on the friend's box):
   all five gestures — which swallow cleanly? (result decides the
   gesture-set row); dictate into Notepad, a browser, Windows Terminal
   (paste path), non-ASCII text; run the MSIX hotkey probe — does the
   hook fire under runFullTrust?
4. **W4 — Runtime: mic + ASR + LLM.** ✅ code done (PR #76, issue #75).
   Confirmed zero-code: sounddevice (wheel bundles PortAudio/WASAPI) and
   `make_asr()` ("auto" → ONNX off Apple Silicon already). Landed:
   `ollama_runtime._ASSETS` per-platform table (win32 = pinned
   `ollama-windows-amd64.zip` v0.32.1 + published sha256, `ollama.exe` at
   the archive root, stdlib zipfile extraction with an explicit zip-slip
   guard — extractall does not sanitize like tarfile's filter="data";
   NOT the desktop installer, which runs its own tray/autostart);
   `llm_server.bundled_binary()` resolves via ollama_runtime on Windows
   too; `_spawn` uses CREATE_NEW_PROCESS_GROUP on Windows
   (start_new_session is POSIX-only — same console-Ctrl isolation
   intent). Units: asset-table pins, windows-layout installed(), real
   zip round-trip + slip refusal. Friend: deferred to Round B.
5. **W5 — First-run + config defaults + single-instance.** ✅ code done
   (PR #78, issue #77). `firstrun_tk` decoupled via a BACKEND module
   parameter (ROWS/GATING/SUBTITLE/statuses/run_fix/engine_missing/
   setup_missing/relaunch — platform-selected by `_backend()`, injectable
   for tests; Linux call sites unchanged). New `firstrun_windows.py`:
   mic row reads the REAL ConsentStore registry toggle (non-MSIX apps get
   no OS prompt — the global toggle fails silently), gates Start but
   every read uncertainty fails OPEN; Fix deep-links
   `ms-settings:privacy-microphone`; models/consent + download screen
   shared with Linux (≥100 MB gate covers engine zip + models);
   Start-at-login = Startup-folder .lnk via a pure-argv powershell
   WScript.Shell one-liner; `bundle_type()` msix/exe/None via
   GetCurrentPackageFullName; **spawn-then-exit relaunch** (DETACHED_
   PROCESS + os._exit — never execv on Windows; ollama child shut down
   first, same reason as Linux). `load_config()` `IS_WINDOWS` branch
   (ctrl_r, haptics off, provisional winsound table — auditioned in W6,
   terminal exes on the paste path). **Named-mutex single-instance**
   (`Local\sotto-instance`, fails open) wired into
   `_acquire_instance_lock`; app.py first-run gate + announce path now
   IS_LINUX-or-IS_WINDOWS. Units: backend surface pins, mic fail-open
   matrix, needed() marker/env contract, mutex accept/refuse/fail-open,
   config defaults, and the shared Tk windows driven end-to-end with the
   Windows backend (mic gating, consent, marker+relaunch). Friend:
   deferred to Round B.
6. **W6 — Overlay + sounds + tray + quit path.** ✅ code done (PR #80,
   issue #79). Tray: shared pystray module (historical `tray_linux` name
   kept — spec/tests churn not worth it) started on `IS_LINUX or
   IS_WINDOWS`; Insights action per-platform (Windows = browser tab until
   W8's WebView2; Linux = insights_linux.show_soon unchanged); pystray
   requirement marker gains win32 (same minor-pin rationale). **Quit:
   `overlay_tk.request_quit()`** — thread-safe flag consumed by the tick
   (`_consume_quit` destroys the root, mainloop returns → the normal
   teardown path), used by the Windows tray Quit (os.kill(SIGINT) is
   TerminateProcess-adjacent for a windowed app: no unwinding, no atexit,
   orphaned ollama); headless Windows = llm_server.shutdown + os._exit;
   **Linux SIGINT contract untouched** (pinned in units). Overlay code
   audit for Windows: overrideredirect/-topmost/-alpha all supported;
   the X11-specific bits are already try-guarded — live look is Round B.
   winsound: W5's provisional alias table STANDS (no Windows hardware to
   audition on — Round B auditions and this table gets settled then;
   honest deviation from the original milestone wording). Units: tray
   wiring per-platform (forced flags), the quit triple (overlay path /
   headless / Linux SIGINT), request_quit arm/consume mechanics.
   **→ Round B** (frozen onedir from CI): cold first run
   (consent screen, downloads with progress, relaunch), end-to-end
   dictation, capsule visible over apps + not stealing clicks, sounds
   distinct (audition the table), tray menu works, Quit exits clean
   (Task Manager), second launch refuses (single-instance),
   `sotto.log` attached.
7. **W7 — Packaging + the MSIX decision.** 🔄 code done (PR #82, issue
   #81). `winapp/sotto_win.py` (entry; --smoke by EXIT CODE — the
   windowed bootloader Nones the std streams, `_repair_streams()` fixes
   them and ~/.sotto/sotto.log is the output surface),
   `winapp/sotto_win.spec` (onedir, console=False, collect_all onnx_asr/
   huggingface_hub, mac+linux stacks excluded; NO PortAudio handling —
   sounddevice's Windows wheel bundles its own DLL),
   `winapp/build_app.ps1`, `winapp/msix/AppxManifest.xml` (loose-layout
   runFullTrust wrapper around the SAME onedir; internetClient +
   microphone DeviceCapability — the latter drives the per-app OS mic
   prompt W5's mic_ok defers to on MSIX; documentsLibrary deliberately
   absent, restricted + unneeded), `winapp/INSTALL-TEST.md` (Rounds B+C
   hands-on). CI: new `windows` job in release.yml builds the onedir,
   runs --smoke, uploads ONE `windows-app` artifact (onedir + manifest
   at the root — serves Round B directly and Round C via
   Add-AppxPackage -Register). test_smoke_imports pins the Windows
   smoke list (and that no Linux-only modules leak in).
   **HONESTY CORRECTION (found in W8):** this milestone's "smoke passed
   first try" was a FALSE GREEN — PowerShell does not wait for
   GUI-subsystem exes on a bare invocation, so `$LASTEXITCODE` was
   stale from the PyInstaller command and the frozen smoke never gated
   anything. Fixed in W8 (Start-Process -Wait -PassThru at every
   frozen-exe call site); the first HONEST frozen smoke ran there and
   did pass.
   **→ Round C**: sideload on the friend's box — hook + SendInput + mic
   + model download under runFullTrust = the gate completes. PASS →
   Store channel confirmed (decision table updated), submission dry
   run. FAIL → Inno fallback activated, W9's signature gate becomes
   mandatory. Zero-terminal install → dictation, every prompt counted.
8. **W8 — Insights window on WebView2.** ✅ code done (PR #84, issue
   #83). `sotto/insights_windows.py` — pywebview on a daemon thread
   (create-once; reshow surfaces the hidden window; closing handler
   hides + cancels — the loop must never end; loop death lands in the
   sticky browser fallback like every other failure), decision row
   above updated with the full rationale. Wiring: tray + app.py
   configure/show route through it (the W6 browser-tab branch
   collapsed); pywebview `>=5.3,<6 ; win32` in requirements; spec
   hidden-imports webview + edgechromium backend (the WebView2 RUNTIME
   stays system-owned, never bundled); entry gains `--smoke-webview`.
   CI: the windows job checks the registry for the Evergreen runtime,
   installs the bootstrapper only when absent, then renders the real
   dashboard in the real WebView2 inside the frozen onedir (exit-code
   contract). Units: `test_insights_windows` (gating, sticky ladder,
   create/reshow/close-cancel with a blocking fake loop, loop-death
   fallback) + tray wiring pin updated. Friend: Round D (native window
   open/close/reopen, dictionary save, dictate-while-open).
9. **W9 — Updater backend (channel-dependent).** ✅ phase 1 done (PR
   #86, issue #85): `update.enabled()` returns False on Windows **by
   design and pinned** — "msix" installs are updated by the Store (a
   self-updater would fight it) and "exe" has no distribution channel
   until Round C's verdict (previously the silence was borrowed from the
   macOS branch by accident: menubar.running_in_bundle() happening to be
   False). `asset_suffix()` already returns None on Windows (pinned).
   **Phase 2 is CONTINGENT on Round C failing the MSIX gate:** only then
   does the Inno channel exist, and with it the signature-gated backend
   (pinned pubkey, verify BEFORE execute, no-downgrade — the L8 pattern;
   `-setup.exe` suffix; evaluate()'s .sig requirement already composes;
   release workflow signs like the Linux artifacts; full L8-style gate
   matrix in units). A package-name check alone stays recognized as
   false assurance. **→ Round D**: native insights window (open/close/
   reopen, dictionary save, dictate-while-open); the vN→vN+1 update
   cycle item applies ONLY on the Inno outcome (Store handles its own).
10. **W10 — Docs + release dry run.** README "Download (Windows)" (honest
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
