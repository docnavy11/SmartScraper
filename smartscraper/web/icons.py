"""Icon paths and status glyphs, ported verbatim from design/build_screens.py.

Icons are 24x24 single-path SVGs drawn at 1.75px stroke. Status is always a
glyph plus a word, never colour alone (UI.md section 1).
"""

from __future__ import annotations

ICONS: dict[str, str] = {
    "play": "M6 4.5l12 7.5-12 7.5z",
    "pause": "M9 5v14M15 5v14",
    "stop": "M6 6h12v12H6z",
    "check": "M4.5 12.5l5 5 10-11",
    "x": "M6 6l12 12M18 6L6 18",
    "alert": "M12 4.5L2.5 20h19zM12 10v4.5M12 17.5v.5",
    "clock": "M12 3a9 9 0 100 18 9 9 0 000-18zM12 7v5.2l3.5 2.2",
    "refresh": "M20 12a8 8 0 11-2.4-5.7M20 3v4.5h-4.5",
    "code": "M9 7l-5 5 5 5M15 7l5 5-5 5",
    "download": "M12 3v12M7 11l5 5 5-5M4 20h16",
    "gear": (
        "M12 9a3 3 0 100 6 3 3 0 000-6zM12 2.5l1.6 2.6 3-.4 .5 3 2.7 1.4-1.4 2.7 1.4 2.7"
        "-2.7 1.4-.5 3-3-.4L12 21.5l-1.6-2.6-3 .4-.5-3-2.7-1.4L5.6 12 4.2 9.3l2.7-1.4 .5-3 3 .4z"
    ),
    "plus": "M12 5v14M5 12h14",
    "chev": "M9 5l7 7-7 7",
    "ext": "M14 4h6v6M20 4l-9 9M18 13v6a1 1 0 01-1 1H5a1 1 0 01-1-1V7a1 1 0 011-1h6",
    "filter": "M3 5h18l-7 8v6l-4 2v-8z",
    "search": "M11 4a7 7 0 100 14 7 7 0 000-14zM16 16l4.5 4.5",
    "term": "M4 5h16v14H4zM8 10l2.5 2.5L8 15M13 15h4",
    "db": (
        "M12 3c4.4 0 8 1.3 8 3s-3.6 3-8 3-8-1.3-8-3 3.6-3 8-3zM4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6"
        "M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"
    ),
    "globe": (
        "M12 3a9 9 0 100 18 9 9 0 000-18zM3 12h18M12 3c2.5 2.5 3.8 5.6 3.8 9S14.5 18.5 12 21"
        "c-2.5-2.5-3.8-5.6-3.8-9S9.5 5.5 12 3z"
    ),
    "key": (
        "M15.5 3a5.5 5.5 0 00-5.2 7.3L3 17.6V21h3.4l1.3-1.3v-2h2v-2h2l1.4-1.4"
        "A5.5 5.5 0 1015.5 3zM17 7.5h.01"
    ),
    "shield": "M12 3l8 3v5.5c0 4.8-3.3 8.4-8 9.5-4.7-1.1-8-4.7-8-9.5V6z",
    "zap": "M13 3L5 13.5h6L11 21l8-10.5h-6z",
    "branch": (
        "M7 4v10M7 18.5v1M17 4v4a4 4 0 01-4 4H7M7 14a2.2 2.2 0 100 4.5A2.2 2.2 0 007 14z"
        "M7 1.8A2.2 2.2 0 107 6.3 2.2 2.2 0 007 1.8zM17 1.8a2.2 2.2 0 100 4.5 2.2 2.2 0 000-4.5z"
    ),
    "file": "M14 3H6a1 1 0 00-1 1v16a1 1 0 001 1h12a1 1 0 001-1V8zM14 3v5h5M8 13h8M8 17h5",
    "trash": "M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13",
    "image": "M4 5h16v14H4zM4 16l4.5-4.5 3.5 3.5 3-3L20 16M9 9.5h.01",
    "user": "M12 12a4 4 0 100-8 4 4 0 000 8zM4.5 20.5c1-3.8 4-5.5 7.5-5.5s6.5 1.7 7.5 5.5",
    "send": "M21 3L10.5 13.5M21 3l-6.8 18-3.7-7.5L3 10z",
    "eye": (
        "M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"
        "M12 9.2a2.8 2.8 0 100 5.6 2.8 2.8 0 000-5.6z"
    ),
    "link": (
        "M10.5 13.5a4 4 0 005.7 0l3-3a4 4 0 10-5.7-5.7l-1.6 1.6M13.5 10.5a4 4 0 00-5.7 0l-3 3"
        "a4 4 0 105.7 5.7l1.6-1.6"
    ),
    "calendar": "M5 6h14v14H5zM5 10h14M9 3v4M15 3v4",
    "sun": (
        "M12 7.5a4.5 4.5 0 100 9 4.5 4.5 0 000-9zM12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22"
        "M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M19.1 4.9l-1.8 1.8M6.7 17.3l-1.8 1.8"
    ),
    "moon": "M20 14.5A8.5 8.5 0 019.5 4a8.5 8.5 0 1010.5 10.5z",
    "sliders": "M4 7h10M18 7h2M4 17h4M12 17h8M16 4v6M8 14v6",
    "cpu": "M7 7h10v10H7zM9.5 3v4M14.5 3v4M9.5 17v4M14.5 17v4M3 9.5h4M3 14.5h4M17 9.5h4M17 14.5h4",
    "copy": "M9 9h11v11H9zM5 15H4V4h11v1",
}

# glyph, tone class suffix, default word. Never colour alone.
STATUS: dict[str, tuple[str, str, str]] = {
    "ok": ("●", "ok", "passed"),
    "drift": ("▲", "drift", "drift"),
    "fail": ("✕", "fail", "failed"),
    "run": ("◐", "run", "running"),
    "queued": ("○", "queued", "queued"),
    "paused": ("‖", "paused", "paused"),
    "agent": ("◆", "agent", "agent"),
}

#: Run.status (db) -> status key above.
RUN_STATUS_KIND: dict[str, str] = {
    "queued": "queued",
    "running": "run",
    "passed": "ok",
    "validation_failed": "drift",
    "error": "fail",
    "blocked": "fail",
    "cancelled": "paused",
}

#: Run.status -> the word shown next to the glyph.
RUN_STATUS_WORD: dict[str, str] = {
    "queued": "queued",
    "running": "running",
    "passed": "passed",
    "validation_failed": "validation failed",
    "error": "error",
    "blocked": "blocked",
    "cancelled": "cancelled",
}
