<!-- Created by Pratik Sancheti / https://github.com/psancheti6666 -->
# Sotto.app — macOS application plan & decision log

Goal: ship Sotto as a normal downloadable Mac app (DMG), so non-developers
never touch a terminal. This document is the single source of truth for the
decisions already made and the milestone order — read it before working on
the app so settled questions don't get reopened.

## Decisions (settled — don't re-litigate)

| Decision | Choice | Why |
|---|---|---|
| Distribution | Direct DMG download from GitHub Releases. **No App Store.** | Side project; no review process wanted. |
| Signing | **Unsigned for now.** No Apple Developer Program, no $99/yr. Dev builds sign with a local self-signed **"Sotto Dev"** cert (build_app.sh auto-detects it; see below) purely so TCC grants survive rebuilds — Gatekeeper still treats the app as unsigned. | Users are friends who can use Privacy & Security → "Open Anyway". A README beside the app in the DMG explains the two clicks. Revisit signing+notarization only if/when strangers start downloading. Ad-hoc signatures change hash every rebuild and macOS silently invalidates Accessibility/Input Monitoring grants (learned the hard way, 2026-07-17); a stable cert identity (`identifier + certificate leaf`) fixes that, and the release pipeline should sign with one consistent cert for the same reason. |
| App shape | **Regular app**: Dock icon (waveform tile; click opens Insights) + menu-bar waveform, like Wispr Flow. Python code stays; **py2app** bundles it into Sotto.app. No Swift rewrite. *(Revised from "no Dock icon" at Milestone 4 — Pratik wanted visible Dock presence.)* | Feature parity for free; hotkey/overlay/pipeline are already AppKit-native via pyobjc. |
| Insights UI | Native window hosting a **WKWebView** rendering the existing `dashboard.html` (same localhost server). | Reuses the whole dashboard; stops feeling like "a website". |
| Models | **Never in the DMG.** First launch downloads ASR (~600 MB MLX / ~2.4 GB ONNX) + LLM (~2.5 GB) into `~/.sotto` with progress UI. | Keeps DMG ~300–400 MB (Apple Silicon) / ~150–200 MB (Intel). Total disk after setup ~4 GB, same as the dev install. |
| Per-arch builds | **Two DMGs**: Apple Silicon (MLX backend) and Intel (ONNX backend). Built by GitHub Actions (arm64 + Intel macOS runners) on release. | Matches the existing backend split; universal2 not worth it with MLX being arm-only. |
| Updates | Manual "Check for updates" menu item hitting the GitHub releases API **only when clicked**. No auto-update, no Sparkle for now. | Privacy story stays pure (zero unsolicited network calls). run.sh's git-pull auto-update doesn't apply to the app. |
| Permissions | Info.plist usage strings (NSMicrophoneUsageDescription etc.); mic / Accessibility / Input Monitoring prompts attach to Sotto.app itself. | Strictly better than today's grant-to-terminal experience. |
| Dev/release split (added post-v0.3.0 testing) | Local builds are **"Sotto Dev"** (`…sotto.dev` bundle id, DEV-badged icon, `dist/SottoDev.app`); `SOTTO_RELEASE=1` (CI) builds the real **"Sotto"**. Two separate apps to macOS. | Same-bundle-id copies with different signatures fight over one TCC row (toggle-on-but-denied loops, hit live testing the v0.3.0 DMG). Separate identities = separate permission rows; contributors' builds never break their installed Sotto. |

## Open decisions

- ~~**LLM engine inside the app**~~ **Resolved at Milestone 2 (2026-07-17):
  option 1, bundle the `ollama` runtime** (MIT) in
  `Contents/Resources/ollama/`, spawned as a hidden child only when the
  configured `ollama_url` doesn't answer. Measured (v0.32.1): the arm64-pruned
  runtime is **48 MB** uncompressed — the tarball's optional `mlx_metal_*`
  engine (348 MB) and everything without an arm64 slice (Intel ggml CPU
  variants, x86-only dylibs) are dropped; the arm64 `llama-server` links
  ggml/Metal statically, so GGUF serving runs fully GPU-offloaded without
  them (verified with qwen3:4b-instruct on the M3). Rejected: in-process MLX-LM (would fork the
  code path for the Intel build); external install (breaks
  double-click-and-it-works). The 2.5 GB model still downloads on first use,
  into ollama's default `~/.ollama` store until Milestone 3 consolidates
  model downloads.

## Milestones (one PR each, in order)

1. **Bundling proof** — ✅ done (PR for issue #3). Unsigned menu-bar Sotto.app
   builds with py2app and runs full dictation on the dev M3 (existing brew
   Ollama is fine here). Models load from `~/.sotto` as today. Deliverable:
   `./macapp/build_app.sh` → `dist/Sotto.app`.
2. **Self-contained LLM** — ✅ done (PR for issue #5). Decision above
   resolved; `sotto/llm_server.py` probes → spawns the bundled runtime →
   auto-pulls the model (log-only); app works on a Mac with no brew, no
   Ollama installed.
3. **First-run experience** — ✅ done (PR for issue #7). `sotto/firstrun.py`:
   welcome window with live-polling permission rows (proper request APIs),
   Globe-key fix, model downloads with progress (~/.sotto on fresh machines,
   existing default stores reused), quit-&-relaunch completion (Input
   Monitoring needs a fresh process; macOS may force that restart itself —
   a one-time "Sotto is ready" note covers both paths). Replaces setup.sh
   for app users. `SOTTO_FIRSTRUN=1` forces the window for previewing.
4. **Insights window** — ✅ done (PR for issue #9). `sotto/insights.py`:
   WKWebView window rendering the existing dashboard (auto-opens in-bundle
   instead of the browser); native chrome (transparent titlebar + drag strip,
   real fullscreen); app became a **regular app** (Dock tile from the
   waveform mark, click opens Insights, main menu with Edit/Window); sticky
   History/date headers + search highlighting in the page; fixed the
   pynput/TSM main-thread crash the WKWebView exposed (inject.prewarm).
5. **DMG + release pipeline** — ✅ done (PR for issue #10).
   `macapp/make_dmg.sh` packages `dist/Sotto.app` into
   `Sotto-<version>-<arch>.dmg` (app + /Applications symlink + a README.txt
   explaining the unsigned-app "Open Anyway" steps); build_app.sh/setup_app.py
   are arch-aware (x86_64 swaps in the ONNX ASR stack and prunes ollama to
   x86_64 slices); `.github/workflows/release.yml` builds both DMGs on `v*`
   tag push into a **draft** GitHub Release (published manually) or as
   workflow artifacts on manual dispatch. Intel build is CI-built and
   community-tested (no Intel hardware available).
6. **"Check for updates"** menu item (manual, on-click only).

## Constraints that apply to every milestone

- All of Sotto's ground rules (CONTRIBUTING.md): 100% local at runtime,
  faithful cleanup, user data stays in `~/.sotto`, no copyrighted assets.
- The repo must keep working exactly as today for the git-clone + `./run.sh`
  path (developers, Linux users). The app is a packaging layer, not a fork.
- Estimated total effort: 2–3 weeks of part-time work across the milestones.
