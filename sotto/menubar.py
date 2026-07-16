# Created by Pratik Sancheti / https://github.com/psancheti6666
"""App chrome for the Sotto.app bundle: menu-bar waveform (Insights / Quit),
the main menu (About, Edit for copy-paste in the Insights window, Window),
and the Dock-icon click → Insights behavior.

No-op from a terminal checkout — all of it exists only inside the py2app
bundle, so ./run.sh behavior is unchanged. install() must run on the main
thread before the AppKit run loop starts; overlay.run_forever() calls it in
exactly that window.
"""
import sys

_status_item = None  # module-level retain: without it the item is collected and vanishes
_controller = None


def running_in_bundle() -> bool:
    """True when running inside the py2app bundle (its bootstrap sets sys.frozen)."""
    return getattr(sys, "frozen", None) == "macosx_app"


def install():
    global _status_item, _controller
    if not running_in_bundle() or _status_item is not None:
        return
    from AppKit import (
        NSImage,
        NSMenu,
        NSMenuItem,
        NSObject,
        NSStatusBar,
        NSVariableStatusItemLength,
    )

    from . import insights

    item = NSStatusBar.systemStatusBar().statusItemWithLength_(
        NSVariableStatusItemLength)
    icon = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
        "waveform", "Sotto")
    if icon is not None:
        item.button().setImage_(icon)  # SF Symbol renders as a template image
    else:
        item.button().setTitle_("Sotto")

    class _AppController(NSObject):
        def insights_(self, _sender):
            insights.show()

        def applicationShouldHandleReopen_hasVisibleWindows_(self, _app, _flag):
            # Dock icon clicked → bring up Insights, like Wispr Flow
            if insights.available():
                insights.show()
            return True

    _controller = _AppController.alloc().init()
    menu = NSMenu.alloc().init()
    if insights.available():
        mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Insights", "insights:", "i")
        mi.setTarget_(_controller)
        menu.addItem_(mi)
        menu.addItem_(NSMenuItem.separatorItem())
    # nil target → the responder chain routes terminate: to NSApp, same
    # shutdown path as the SIGINT handler in overlay.run_forever().
    menu.addItem_(NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Quit Sotto", "terminate:", "q"))
    item.setMenu_(menu)
    _status_item = item

    from AppKit import NSApp
    NSApp.setDelegate_(_controller)
    NSApp.setMainMenu_(_main_menu())


def _main_menu():
    """A regular app (Dock icon) needs a real main menu: without an Edit
    menu, Cmd+C/V/X/A do nothing inside the Insights window's text fields."""
    from AppKit import NSMenu, NSMenuItem

    def item(title, action, key, mask=None):
        mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            title, action, key)
        if mask is not None:
            mi.setKeyEquivalentModifierMask_(mask)
        return mi

    main = NSMenu.alloc().init()

    app_root = NSMenuItem.alloc().init()
    main.addItem_(app_root)
    app_menu = NSMenu.alloc().init()
    app_menu.addItem_(item("About Sotto",
                           "orderFrontStandardAboutPanel:", ""))
    app_menu.addItem_(NSMenuItem.separatorItem())
    app_menu.addItem_(item("Quit Sotto", "terminate:", "q"))
    app_root.setSubmenu_(app_menu)

    edit_root = NSMenuItem.alloc().init()
    edit_root.setTitle_("Edit")
    main.addItem_(edit_root)
    edit = NSMenu.alloc().initWithTitle_("Edit")
    edit.addItem_(item("Undo", "undo:", "z"))
    edit.addItem_(item("Redo", "redo:", "Z"))
    edit.addItem_(NSMenuItem.separatorItem())
    edit.addItem_(item("Cut", "cut:", "x"))
    edit.addItem_(item("Copy", "copy:", "c"))
    edit.addItem_(item("Paste", "paste:", "v"))
    edit.addItem_(item("Select All", "selectAll:", "a"))
    edit_root.setSubmenu_(edit)

    win_root = NSMenuItem.alloc().init()
    win_root.setTitle_("Window")
    main.addItem_(win_root)
    win = NSMenu.alloc().initWithTitle_("Window")
    win.addItem_(item("Close", "performClose:", "w"))
    win.addItem_(item("Minimize", "performMiniaturize:", "m"))
    win.addItem_(item("Zoom", "performZoom:", ""))
    win_root.setSubmenu_(win)
    return main
