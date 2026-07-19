<!-- Created by Pratik Sancheti / https://github.com/psancheti6666 -->
# Windows friend rounds B & C — install/test instructions

Artifact: the `windows-app` artifact from a GitHub Actions run — ONE
folder that serves both rounds: `sotto/` (the app), `AppxManifest.xml` +
`Assets/` at the root (the loose-layout MSIX wrapper around that same
app folder), and this file. Full checklists live in docs/windows-app.md
(W6 → Round B, W7 → Round C); this is the hands-on part. Attach
`%USERPROFILE%\.sotto\sotto.log` to everything you report.

## Round B — the app itself (no packaging)
1. Unzip anywhere, double-click `sotto\sotto.exe` (SmartScreen: More info →
   Run anyway — note that it appeared; that's a data point).
2. First run: welcome window → mic row honest? → tick "OK, download" →
   downloads with progress (~5 GB total) → Sotto relaunches itself.
3. Hold **Right Ctrl**, speak, release → text lands at the cursor. Try:
   Notepad, a browser textbox, Windows Terminal (should paste, not type),
   something with `çafé 你好`.
4. Gestures: double-tap (hands-free), hold+Space (hands-free, space must
   NOT appear), Escape mid-dictation (cancel toast + Undo).
5. Capsule: visible over apps? stealing clicks? Sounds: distinct enough?
   (say which ones feel wrong — the table is provisional).
6. Tray: icon present; Insights opens the dashboard; **Quit** → check
   Task Manager: no `sotto.exe`, no `ollama.exe` left.
7. Double-launch: second `sotto.exe` must refuse with a dialog.

## Round C — the same app under MSIX runFullTrust (the Store gate)
1. Settings → System → For developers → **Developer Mode ON**.
2. In the artifact folder, PowerShell:
   `Add-AppxPackage -Register .\AppxManifest.xml`
3. Start menu → **Sotto Dev** → repeat Round B's steps 2–7 (the mic row
   should now be the OS's own per-app prompt instead of the global toggle).
4. Report any behavior difference vs Round B — hook not firing, typing not
   landing, mic prompt absent — a difference here IS the finding that
   decides Store vs Inno.
5. Cleanup: `Get-AppxPackage SottoDev | Remove-AppxPackage`, Developer
   Mode off.
