# Created by Pratik Sancheti / https://github.com/psancheti6666
# Build the Windows onedir bundle -> dist\sotto\ (docs/windows-app.md, W7).
# Windows-only; CI (windows-latest) is the primary runner - no Windows dev
# hardware exists, so treat local runs as best-effort.
$ErrorActionPreference = "Stop"
# pwsh 7.3+: make native-command failures (pip etc.) honor Stop — without
# this a failed pip install only surfaces later as a confusing PyInstaller
# import error. Harmless no-op assignment on Windows PowerShell 5.1.
$PSNativeCommandUseErrorActionPreference = $true
Set-Location (Join-Path $PSScriptRoot "..")

if ($env:OS -ne "Windows_NT") {
    Write-Error "Windows only - Linux builds with linuxapp/build_app.sh"
}

$py = if ($env:PYTHON) { $env:PYTHON } else { "python" }
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet -r requirements.txt
# build-only dependency; major pinned so hook behavior doesn't drift
& $py -m pip install --quiet "pyinstaller>=6.11,<7"

if (Test-Path build) { Remove-Item -Recurse -Force build }
if (Test-Path dist\sotto) { Remove-Item -Recurse -Force dist\sotto }
& $py -m PyInstaller --noconfirm --distpath dist --workpath build `
    winapp\sotto_win.spec
if ($LASTEXITCODE -ne 0) { exit 1 }

# Import every runtime-selected backend inside the frozen app - the safety
# net for lazy imports PyInstaller can't see. Windowed exe: exit code is
# the contract - and PowerShell does NOT wait for GUI-subsystem exes on a
# bare invocation ($LASTEXITCODE would be stale from the previous native
# command: a false green, found the hard way in W8). Start-Process -Wait
# is the honest launch.
$p = Start-Process -FilePath (Resolve-Path "dist\sotto\sotto.exe") `
    -ArgumentList "--smoke" -Wait -PassThru -NoNewWindow
if ($p.ExitCode -ne 0) {
    Write-Error "smoke check failed (exit $($p.ExitCode))"
}
Write-Host "smoke OK (exit 0) - dist\sotto ready"
