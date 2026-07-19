# Created by Pratik Sancheti / https://github.com/psancheti6666
"""Standalone Windows hotkey/injection probe (docs/windows-app.md W2).

The ONE live test that decides the MSIX/Store distribution channel: does a
WH_KEYBOARD_LL hook see keys, can it swallow them, and does SendInput land
text — under MSIX runFullTrust? Run it three ways on the friend's machine:
plain python, the PyInstaller probe.exe, and the MSIX-registered probe.exe
(instructions in PROBE.md). Screenshot the summary block it prints.

No Sotto code is imported — this is deliberately the smallest possible
reproduction of exactly what the real app needs from Windows.
"""

import sys
import time

RESULTS = {}
HOLD_KEY = "ctrl_r"           # the planned default hotkey
VK_RCONTROL, VK_SPACE = 0xA3, 0x20
WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
LLKHF_INJECTED = 0x10


def main():
    if sys.platform != "win32":
        print("this probe is Windows-only")
        return 1
    from pynput import keyboard

    print("=" * 60)
    print("Sotto hotkey probe — follow the prompts, then screenshot")
    print("the RESULT block at the end.")
    print("=" * 60)

    seen = {"rctrl_down": False, "rctrl_up": False, "space_swallowed": False}
    holding = {"on": False}
    listener = None

    def win32_filter(msg, data):
        try:
            if data.flags & LLKHF_INJECTED:
                return
            if (msg in (WM_KEYDOWN, WM_SYSKEYDOWN)
                    and data.vkCode == VK_SPACE and holding["on"]):
                # the swallow test: Space while holding Right Ctrl must NOT
                # reach the console
                seen["space_swallowed"] = True
        except Exception:
            return
        if seen.get("space_swallowed") and data.vkCode == VK_SPACE:
            listener.suppress_event()

    def on_press(key):
        if key == keyboard.Key.ctrl_r:
            seen["rctrl_down"] = True
            holding["on"] = True

    def on_release(key):
        if key == keyboard.Key.ctrl_r:
            seen["rctrl_up"] = True
            holding["on"] = False

    listener = keyboard.Listener(on_press=on_press, on_release=on_release,
                                 win32_event_filter=win32_filter)
    listener.start()
    listener.wait()

    print("\n[1/2] HOOK TEST — 15 seconds:")
    print("  hold RIGHT CTRL, tap SPACE a few times while holding, release.")
    print("  (if the hook works, the spaces will NOT appear if you type in")
    print("  a notepad afterwards — they were swallowed)")
    time.sleep(15)

    print("\n[2/2] INJECTION TEST — 8 seconds:")
    print("  click into Notepad NOW; the probe will type a line there.")
    time.sleep(8)
    injected_text = "sotto probe: unicode çafé 你好 — SendInput OK"
    try:
        keyboard.Controller().type(injected_text)
        RESULTS["sendinput"] = "attempted (verify the line landed in Notepad)"
    except Exception as e:
        RESULTS["sendinput"] = f"FAILED to send: {e}"
    time.sleep(2)
    listener.stop()

    print("\n" + "=" * 60)
    print("RESULT (screenshot this):")
    print(f"  hook saw Right Ctrl down : {seen['rctrl_down']}")
    print(f"  hook saw Right Ctrl up   : {seen['rctrl_up']}")
    print(f"  Space swallow attempted  : {seen['space_swallowed']}")
    print(f"  SendInput                : {RESULTS.get('sendinput')}")
    print(f"  expected in Notepad      : {injected_text!r}")
    print(f"  python                   : {sys.version.split()[0]}"
          f"  frozen={getattr(sys, 'frozen', False)}")
    print("=" * 60)
    input("press Enter to close...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
