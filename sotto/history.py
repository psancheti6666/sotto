"""Dictation history: append-only JSONL at ~/.sotto/history.jsonl + stats.

One JSON object per line, written after each successful dictation. The file is
the only place history lives — local, human-readable, and wiped by deleting it.
"""

import json
import logging
import os
from datetime import date, datetime, timedelta

from .config import HISTORY_PATH

log = logging.getLogger("sotto")

# Estimated typing speed used for the "time saved" insight.
TYPING_WPM = 40.0


def append_entry(record: dict, path: str = HISTORY_PATH):
    """Append one dictation record. A history write must never break
    dictation — any failure is logged and swallowed."""
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning("could not write history entry (%s)", e)


def read_entries(path: str = HISTORY_PATH) -> list:
    """All records, oldest first. Blank/corrupt lines are skipped so one bad
    write (e.g. a crash mid-line) never hides the rest of the history."""
    if not os.path.exists(path):
        return []
    entries = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if isinstance(entry, dict):
                    entries.append(entry)
    except Exception as e:
        log.warning("could not read history (%s)", e)
    return entries


def compute_stats(entries: list, today: date = None) -> dict:
    """Aggregate insights for the dashboard. `today` is injectable for tests."""
    total_words = sum(e.get("words", 0) for e in entries)
    total_audio_s = sum(e.get("duration_s", 0.0) for e in entries)
    audio_min = total_audio_s / 60.0
    # Speaking rate: words per minute of actual audio.
    avg_wpm = total_words / audio_min if audio_min > 0 else 0.0
    # Time saved vs typing the same words at TYPING_WPM (never negative).
    time_saved_min = max(0.0, total_words / TYPING_WPM - audio_min)

    if today is None:
        today = datetime.now().astimezone().date()
    days = [today - timedelta(days=i) for i in range(13, -1, -1)]
    per_day = {d.isoformat(): 0 for d in days}
    for e in entries:
        day = str(e.get("ts", ""))[:10]  # ISO-8601 prefix is the date
        if day in per_day:
            per_day[day] += e.get("words", 0)

    return {
        "total_words": total_words,
        "total_dictations": len(entries),
        "total_audio_s": round(total_audio_s, 1),
        "avg_wpm": round(avg_wpm, 1),
        "time_saved_min": round(time_saved_min, 1),
        "words_per_day": [{"date": d, "words": w} for d, w in per_day.items()],
    }
