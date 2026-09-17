"""Failure kinds the runner distinguishes.

The distinction matters because it decides the exit code and whether the
escalation ladder should climb: a `Blocked` is worth retrying on a better rung,
a `StepError` is not.
"""

from __future__ import annotations


class RunnerError(Exception):
    """Base for everything this package raises deliberately."""


class StepError(RunnerError):
    """A step could not do what it was told. Not retryable by escalation."""

    def __init__(self, message: str, *, op: str = "", index: int = -1) -> None:
        super().__init__(message)
        self.op = op
        self.index = index


class SelectorNotFound(StepError):
    """No candidate selector matched. Carries every candidate that was tried."""

    def __init__(self, message: str, *, candidates: list[str] | None = None, **kw) -> None:
        super().__init__(message, **kw)
        self.candidates = candidates or []


class Blocked(RunnerError):
    """The site refused us. Retryable on a higher escalation rung."""

    def __init__(self, reason: str, *, status: int | None = None, url: str = "") -> None:
        super().__init__(f"blocked ({reason}) at {url or '?'}")
        self.reason = reason
        self.status = status
        self.url = url


class EngineUnavailable(RunnerError):
    """The engine cannot start here: no browser binary, missing package, bad proxy."""


class ValidationFailed(RunnerError):
    """Script-level assert failed, or min_items was not met."""
