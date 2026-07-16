Created by Pratik Sancheti / https://github.com/psancheti6666

Sotto — private, offline dictation for your Mac
===============================================

Sotto types what you say. Hold the fn (Globe) key, speak, release — cleaned-up,
punctuated text appears at your cursor in whatever app you're using. The speech
recognition and the language model that tidies your words both run entirely on
this Mac: nothing you say ever leaves your computer. No account, no
subscription, no network calls.

Install
-------

Drag Sotto.app onto the Applications shortcut next to it, then eject this disk
image and launch Sotto from your Applications folder.

First launch: the "Not Opened" dialog
-------------------------------------

Sotto is a free, open-source app and is not signed with an Apple Developer
certificate, so macOS blocks the very first launch. Opening it takes two extra
clicks, once:

1. Double-click Sotto.app. macOS shows a dialog saying "Sotto" was not opened /
   Apple could not verify it. Click "Done" (or "OK") — do NOT move it to Trash.
2. Open System Settings > Privacy & Security.
3. Scroll all the way down to the Security section. You'll see a line saying
   "Sotto" was blocked to protect your Mac. Click "Open Anyway".
4. Confirm in the dialog that follows (click "Open Anyway" again) and enter
   your password or Touch ID if asked.

That's it — macOS remembers the choice and every later launch is a normal
double-click. Note: on macOS 15 (Sequoia) and newer, the old trick of
right-clicking the app and choosing "Open" no longer bypasses this check;
the Privacy & Security route above is the only way.

On first run Sotto walks you through the microphone / Accessibility / Input
Monitoring permissions it needs and downloads its speech models (a few GB,
one time) into ~/.sotto.

Source code, documentation, issues:
https://github.com/psancheti6666/sotto (MIT license)
