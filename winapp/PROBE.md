<!-- Created by Pratik Sancheti / https://github.com/psancheti6666 -->
# Hotkey probe — friend Round A instructions (docs/windows-app.md W2)

Goal: prove the keyboard hook + key swallowing + SendInput work on a real
Windows 11 machine, **including under MSIX runFullTrust** — the result
decides whether Sotto ships through the Microsoft Store. ~10 minutes.

Get the `windows-probe` artifact from the GitHub Actions run (it contains
`hotkey_probe.exe`, this file, and the `msix_probe/` folder), unzip it
anywhere, then:

## A. Plain exe (baseline)
1. Double-click `hotkey_probe.exe` (SmartScreen may warn — More info → Run
   anyway; that friction is itself a data point we record).
2. Follow the prompts: hold **Right Ctrl**, tap **Space** a few times while
   holding, release; then click into Notepad when told.
3. Screenshot the RESULT block. Check Notepad: did the unicode line land
   (including `çafé 你好`)? Did the swallowed spaces NOT appear anywhere?

## B. Under MSIX runFullTrust (the decisive test)
1. Settings → System → For developers → **Developer Mode ON** (needed once,
   only for this loose-layout registration — a Store install wouldn't).
2. In the unzipped folder, PowerShell:
   `Add-AppxPackage -Register .\msix_probe\AppxManifest.xml`
3. Start menu → **Sotto Hotkey Probe** → same steps as A, same screenshots.
4. Afterwards: `Get-AppxPackage SottoHotkeyProbe | Remove-AppxPackage` and
   Developer Mode back off.

## What to send back
- Both RESULT screenshots (A and B), plus the Notepad line.
- Whether Space presses leaked anywhere while held (they should be
  swallowed in the hold window).
- Windows edition/build (Settings → System → About).

PASS = hook sees Right Ctrl + swallow works + SendInput unicode lands, in
**both** A and B. B failing while A passes = Store path dies, Inno fallback
activates (docs/windows-app.md decision table).
