"""macOS implementations: NSSound system sounds, trackpad haptics, frontmost app."""


def _on_main(fn):
    from AppKit import NSOperationQueue
    NSOperationQueue.mainQueue().addOperationWithBlock_(fn)


def play_sound(name: str):
    def go():
        from AppKit import NSSound
        snd = NSSound.soundNamed_(name)
        if snd is not None:
            snd.play()
    _on_main(go)


def haptic():
    try:
        from AppKit import NSHapticFeedbackManager
        _on_main(lambda: NSHapticFeedbackManager.defaultPerformer()
                 .performFeedbackPattern_performanceTime_(0, 0))
    except Exception:
        pass


def active_app_id() -> str:
    try:
        from AppKit import NSWorkspace
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        return app.bundleIdentifier() or ""
    except Exception:
        return ""
