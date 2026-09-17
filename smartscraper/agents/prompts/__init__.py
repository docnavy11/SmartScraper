"""System prompts, kept as files so they can be read and diffed on their own."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

DIR = Path(__file__).resolve().parent


@lru_cache
def load(name: str) -> str:
    """Return the text of `prompts/<name>.md`."""
    path = DIR / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"no prompt named {name!r} in {DIR}")
    return path.read_text(encoding="utf-8").strip()


BUILDER = "builder"
REPAIR = "repair"
FALLBACK = "fallback"
