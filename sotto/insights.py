# Created by Pratik Sancheti / https://github.com/psancheti6666
"""Native Insights window for the Sotto.app bundle.

A plain NSWindow hosting a WKWebView pointed at the local dashboard server —
the exact same page (stats, history, dictionary editor, theme toggle) that a
browser shows at http://127.0.0.1:<port>/, deliberately unchanged. Closing
the window hides it; the menu-bar "Insights" item brings it back. Everything
here must run on the main thread (callers dispatch via the AppKit main queue).
"""
import logging

log = logging.getLogger("sotto")

_window = None
_port = None


def configure(port: int):
    """Record the dashboard port; called once at startup before the UI runs."""
    global _port
    _port = port


def available() -> bool:
    return _port is not None


def show():
    """Create the window on first use, then bring it to the front.
    Main thread only."""
    global _window
    if _port is None:
        return
    from AppKit import NSApp
    from Foundation import NSURL, NSURLRequest

    if _window is None:
        _window = _build()
    view = _window.contentView().subviews()[0]
    if not view.URL():  # first show, or a previous load never landed
        view.loadRequest_(NSURLRequest.requestWithURL_(
            NSURL.URLWithString_(f"http://127.0.0.1:{_port}/")))
    _window.makeKeyAndOrderFront_(None)
    NSApp.activateIgnoringOtherApps_(True)


def _build():
    from AppKit import (
        NSBackingStoreBuffered, NSMakeRect, NSScreen, NSViewHeightSizable,
        NSViewWidthSizable, NSWindow,
        NSWindowCollectionBehaviorFullScreenPrimary,
        NSWindowStyleMaskClosable, NSWindowStyleMaskFullSizeContentView,
        NSWindowStyleMaskMiniaturizable, NSWindowStyleMaskResizable,
        NSWindowStyleMaskTitled, NSWindowTitleHidden)
    from WebKit import WKWebView, WKWebViewConfiguration

    W, H = 1080, 780
    win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, W, H),
        NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
        | NSWindowStyleMaskResizable | NSWindowStyleMaskMiniaturizable
        | NSWindowStyleMaskFullSizeContentView,
        NSBackingStoreBuffered, False)
    win.setTitle_("Sotto — Insights")  # Mission Control / VoiceOver name
    # Native look: no grey bar — the page runs edge-to-edge under a
    # transparent titlebar and the traffic lights float over it (the page's
    # own top padding keeps them in empty space).
    win.setTitlebarAppearsTransparent_(True)
    win.setTitleVisibility_(NSWindowTitleHidden)
    # Accessory (menu-bar) apps don't get the green-button fullscreen
    # behavior by default — opt this window in explicitly.
    win.setCollectionBehavior_(win.collectionBehavior()
                               | NSWindowCollectionBehaviorFullScreenPrimary)
    win.setReleasedWhenClosed_(False)  # close = hide; reopened from the menu
    win.setMinSize_((640, 480))

    view = WKWebView.alloc().initWithFrame_configuration_(
        NSMakeRect(0, 0, W, H), WKWebViewConfiguration.alloc().init())
    view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
    win.contentView().addSubview_(view)

    # The web view sits under the transparent titlebar and would swallow its
    # drag gestures — float an invisible strip over the top edge that moves
    # the window (and zooms on double-click), like any real titlebar. The
    # page's own top padding keeps interactive content out of this band.
    from AppKit import NSView, NSViewMinYMargin

    class _DragStrip(NSView):
        def mouseDown_(self, event):
            if event.clickCount() == 2:
                self.window().performZoom_(None)
            else:
                self.window().performWindowDragWithEvent_(event)

    strip = _DragStrip.alloc().initWithFrame_(NSMakeRect(0, H - 28, W, 28))
    strip.setAutoresizingMask_(NSViewWidthSizable | NSViewMinYMargin)
    win.contentView().addSubview_(strip)

    frame = NSScreen.mainScreen().visibleFrame()
    win.setFrameOrigin_(((frame.size.width - W) / 2 + frame.origin.x,
                         (frame.size.height - H) / 2 + frame.origin.y))
    return win


def show_soon():
    """Thread-safe: queue show() onto the AppKit main queue (runs once the
    run loop is pumping)."""
    from Foundation import NSOperationQueue
    NSOperationQueue.mainQueue().addOperationWithBlock_(show)
