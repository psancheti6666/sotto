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
| Signing | **Unsigned for now.** No Apple Developer Program, no $99/yr. | Users are friends who can use Privacy & Security → "Open Anyway". A README beside the app in the DMG explains the two clicks. Revisit signing+notarization only if/when strangers start downloading. |
| App shape | Menu-bar app (LSUIElement / accessory), no Dock icon. Python code stays; **py2app** bundles it into Sotto.app. No Swift rewrite. | Feature parity for free; hotkey/overlay/pipeline are already AppKit-native via pyobjc. |
| Insights UI | Native window hosting a **WKWebView** rendering the existing `dashboard.html` (same localhost server). | Reuses the whole dashboard; stops feeling like "a website". |
| Models | **Never in the DMG.** First launch downloads ASR (~600 MB MLX / ~2.4 GB ONNX) + LLM (~2.5 GB) into `~/.sotto` with progress UI. | Keeps DMG ~300–400 MB (Apple Silicon) / ~150–200 MB (Intel). Total disk after setup ~4 GB, same as the dev install. |
| Per-arch builds | **Two DMGs**: Apple Silicon (MLX backend) and Intel (ONNX backend). Built by GitHub Actions (arm64 + Intel macOS runners) on release. | Matches the existing backend split; universal2 not worth it with MLX being arm-only. |
| Updates | Manual "Check for updates" menu item hitting the GitHub releases API **only when clicked**. No auto-update, no Sparkle for now. | Privacy story stays pure (zero unsolicited network calls). run.sh's git-pull auto-update doesn't apply to the app. |
| Permissions | Info.plist usage strings (NSMicrophoneUsageDescription etc.); mic / Accessibility / Input Monitoring prompts attach to Sotto.app itself. | Strictly better than today's grant-to-terminal experience. |

## Open decisions

- **LLM engine inside the app** (decide at Milestone 2):
  1. **Bundle the `ollama` binary** (MIT-licensed) inside Sotto.app, run as a
     hidden child process — recommended; +~30–60 MB DMG, least code change.
  2. In-process LLM (llama.cpp / MLX-LM) — cleanest long-term, bigger rewrite
     of `clean.py`.
  3. Keep Ollama external with a guided install — rejected for the final app
     (breaks double-click-and-it-works), but it's fine during Milestone 1 on
     the dev machine.
  Size note: the 2.5 GB model downloads on first run under every option; only
  the engine binary differs.

## Milestones (one PR each, in order)

1. **Bundling proof** — ✅ done (PR for issue #3). Unsigned menu-bar Sotto.app
   builds with py2app and runs full dictation on the dev M3 (existing brew
   Ollama is fine here). Models load from `~/.sotto` as today. Deliverable:
   `./packaging/build_app.sh` → `dist/Sotto.app`.
2. **Self-contained LLM** — resolve the open decision above; app works on a
   Mac with no brew, no Ollama installed.
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
