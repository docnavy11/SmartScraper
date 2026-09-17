"""One JSON object per line, on stdout and into the run's log.txt.

The web UI tails log.txt and streams it over SSE, so every line must be a
complete, self-contained JSON object: `{"ts", "level", "tag", "msg", ...}`.
Nothing else may be written to stdout during a run, or the stream breaks.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

LEVELS = ("debug", "info", "warn", "error")

#: Keys the log line owns; a field using one of these names is prefixed `f_`.
RESERVED = frozenset({"ts", "level", "tag", "msg", "run_id"})


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class RunLogger:
    """Writes structured lines to a stream and optionally mirrors them to a file.

    Not thread-safe by design: a run is single-threaded inside its subprocess.
    """

    def __init__(
        self,
        *,
        stream: TextIO | None = None,
        path: Path | None = None,
        run_id: int | None = None,
        min_level: str = "debug",
        sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.stream = stream
        self.path = path
        self.run_id = run_id
        self.min_level = min_level if min_level in LEVELS else "debug"
        self.sink = sink
        self.lines: list[dict[str, Any]] = []
        self._fh: TextIO | None = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = path.open("a", encoding="utf-8")

    # -- emit ---------------------------------------------------------------
    def emit(self, level: str, tag: str, msg: str, /, **fields: Any) -> dict[str, Any]:
        """`level`, `tag` and `msg` are positional-only on purpose: callers pass
        arbitrary structured fields, and `level=0` for an escalation rung must
        not collide with the log level. A field named like a reserved key is
        prefixed rather than silently dropped."""
        rec: dict[str, Any] = {"ts": _now(), "level": level, "tag": tag, "msg": msg}
        if self.run_id is not None:
            rec["run_id"] = self.run_id
        for k, v in fields.items():
            rec[k if k not in RESERVED else f"f_{k}"] = _safe(v)
        self.lines.append(rec)
        if LEVELS.index(level) >= LEVELS.index(self.min_level):
            line = json.dumps(rec, ensure_ascii=False, default=str)
            if self.stream is not None:
                self.stream.write(line + "\n")
                self.stream.flush()
            if self._fh is not None:
                self._fh.write(line + "\n")
                self._fh.flush()
        if self.sink is not None:
            self.sink(rec)
        return rec

    def debug(self, tag: str, msg: str, /, **f: Any) -> None:
        self.emit("debug", tag, msg, **f)

    def info(self, tag: str, msg: str, /, **f: Any) -> None:
        self.emit("info", tag, msg, **f)

    def warn(self, tag: str, msg: str, /, **f: Any) -> None:
        self.emit("warn", tag, msg, **f)

    def error(self, tag: str, msg: str, /, **f: Any) -> None:
        self.emit("error", tag, msg, **f)

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def __enter__(self) -> RunLogger:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def _safe(v: Any) -> Any:
    """Keep log lines small and JSON-serialisable."""
    if isinstance(v, Path):
        return str(v)
    if isinstance(v, str) and len(v) > 2000:
        return v[:2000] + f"…(+{len(v) - 2000} chars)"
    if isinstance(v, (list, tuple)):
        return [_safe(x) for x in v][:50]
    if isinstance(v, dict):
        return {k: _safe(x) for k, x in list(v.items())[:50]}
    return v


def stdout_logger(run_id: int | None = None, path: Path | None = None) -> RunLogger:
    return RunLogger(stream=sys.stdout, path=path, run_id=run_id)


def null_logger() -> RunLogger:
    """Collects lines in memory without writing anywhere. For tests."""
    return RunLogger(stream=None, path=None)
