"""Engine implementations, one per escalation rung.

Nothing is imported eagerly here. `browser` must stay importable on a host with
no browser binary, and importing it from this file would not break that, but
importing Playwright itself costs real time on every `smartscraper` import. The
escalation ladder imports what it needs when it needs it.
"""

from __future__ import annotations

__all__ = ["base", "browser", "http"]
