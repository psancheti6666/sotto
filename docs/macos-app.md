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
| App shape | Menu-bar app (LSUIElement / accessory), no Dock icon. Python code stays; **py2app** bundles it into Sotto.app. No Swift rewrite. | Feature parity for free; hotkey/overlay/pipeline are already AppKit-native via pyobjc. |
| Insights UI | Native window hosting a **WKWebView** rendering the existing `dashboard.html` (same localhost server). | Reuses the whole dashboard; stops feeling like "a website". |
| Models | **Never in the DMG.** First launch downloads ASR (~600 MB MLX / ~2.4 GB ONNX) + LLM (~2.5 GB) into `~/.sotto` with progress UI. | Keeps DMG ~300–400 MB (Apple Silicon) / ~150–200 MB (Intel). Total disk after setup ~4 GB, same as the dev install. |
| Per-arch builds | **Two DMGs**: Apple Silicon (MLX backend) and Intel (ONNX backend). Built by GitHub Actions (arm64 + Intel macOS runners) on release. | Matches the existing backend split; universal2 not worth it with MLX being arm-only. |
| Updates | Manual "Check for updates" menu item hitting the GitHub releases API **only when clicked**. No auto-update, no Sparkle for now. | Privacy story stays pure (zero unsolicited network calls). run.sh's git-pull auto-update doesn't apply to the app. |
| Permissions | Info.plist usage strings (NSMicrophoneUsageDescription etc.); mic / Accessibility / Input Monitoring prompts attach to Sotto.app itself. | Strictly better than today's grant-to-terminal experience. |

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
3. **First-run experience** — welcome window: model downloads with progress,
   permission walkthrough (mic / Accessibility / Input Monitoring), Globe-key
   fix offer. Replaces setup.sh for app users.
4. **Insights window** — WKWebView window on the menu-bar menu, rendering the
   existing dashboard.
5. **DMG + release pipeline** — GitHub Actions builds both DMGs (a bundled
   README.txt in each explains the unsigned-app "Open Anyway" steps), attached
   to a GitHub Release. Intel build is CI-built and community-tested (no Intel
   hardware available).
6. **"Check for updates"** menu item (manual, on-click only).

## Constraints that apply to every milestone

- All of Sotto's ground rules (CONTRIBUTING.md): 100% local at runtime,
  faithful cleanup, user data stays in `~/.sotto`, no copyrighted assets.
- The repo must keep working exactly as today for the git-clone + `./run.sh`
  path (developers, Linux users). The app is a packaging layer, not a fork.
- Estimated total effort: 2–3 weeks of part-time work across the milestones.
