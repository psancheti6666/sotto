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


def prevent_app_nap(reason: str = "Sotto is transcribing"):
    """Opt out of App Nap for the duration of one dictation. A backgrounded app
    otherwise gets throttled by macOS: CPU/timers slow (dictation gets sluggish,
    audio callback blocks drop so accuracy falls) and the window server stops
    floating our panel over full-screen Spaces (the capsule then only shows on the
    desktop). Held only while actively recording/processing, then released with
    end_app_nap() so an idle Sotto stays as low-power as macOS wants it to be.

    Returns the activity token, which the CALLER MUST RETAIN until end_app_nap();
    releasing it re-allows App Nap. Returns None if unavailable.
    """
    try:
        from Foundation import (
            NSProcessInfo,
            NSActivityUserInitiatedAllowingIdleSystemSleep,
            NSActivityLatencyCritical,
        )
        # UserInitiatedAllowingIdleSystemSleep defeats App Nap while still letting
        # the Mac idle-sleep normally; LatencyCritical also stops timer coalescing
        # (steadier audio callbacks + overlay animation).
        options = (NSActivityUserInitiatedAllowingIdleSystemSleep
                   | NSActivityLatencyCritical)
        return NSProcessInfo.processInfo().beginActivityWithOptions_reason_(
            options, reason)
    except Exception:
        return None


def end_app_nap(token):
    """Release a token from prevent_app_nap(), re-allowing App Nap. No-op if None."""
    if token is None:
        return
    try:
        from Foundation import NSProcessInfo
        NSProcessInfo.processInfo().endActivity_(token)
    except Exception:
        pass
