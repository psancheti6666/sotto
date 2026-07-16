# Created by Pratik Sancheti / https://github.com/psancheti6666
"""Menu-bar presence for the Sotto.app bundle (Milestone 1: icon + Quit only).

No-op from a terminal checkout — the status item exists only inside the
py2app bundle, so ./run.sh behavior is unchanged. install() must run on the
main thread before the AppKit run loop starts; overlay.run_forever() calls it
in exactly that window.
"""
import sys

_status_item = None  # module-level retain: without it the item is collected and vanishes


def running_in_bundle() -> bool:
    """True when running inside the py2app bundle (its bootstrap sets sys.frozen)."""
    return getattr(sys, "frozen", None) == "macosx_app"


def install():
    global _status_item
    if not running_in_bundle() or _status_item is not None:
        return
    from AppKit import (
        NSImage,
        NSMenu,
        NSMenuItem,
        NSStatusBar,
        NSVariableStatusItemLength,
    )

    item = NSStatusBar.systemStatusBar().statusItemWithLength_(
        NSVariableStatusItemLength)
    icon = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
        "waveform", "Sotto")
    if icon is not None:
        item.button().setImage_(icon)  # SF Symbol renders as a template image
    else:
        item.button().setTitle_("Sotto")
    menu = NSMenu.alloc().init()
    # nil target → the responder chain routes terminate: to NSApp, same
    # shutdown path as the SIGINT handler in overlay.run_forever().
    menu.addItem_(NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Quit Sotto", "terminate:", "q"))
    item.setMenu_(menu)
    _status_item = item
