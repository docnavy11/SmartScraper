"""Everything a run leaves behind, in one directory per run.

    data/runs/<run_id>/
        log.txt        one JSON object per line, the same stream as stdout
        page.html      the last document the run saw
        screen.png     screenshot, browser engines only
        records.jsonl  one emitted record per line
        trace.zip      Playwright trace, failures only
        network.har    HAR, failures only
        outcome.json   the serialised RunOutcome

The directory is the contract with the rest of the system: the web UI lists it,
the repair agent reads `page.html` out of it, and the retention job deletes it
by age. Nothing else may decide where these files live.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from smartscraper.contracts import RunOutcome

LOG_NAME = "log.txt"
HTML_NAME = "page.html"
SCREENSHOT_NAME = "screen.png"
RECORDS_NAME = "records.jsonl"
TRACE_NAME = "trace.zip"
HAR_NAME = "network.har"
OUTCOME_NAME = "outcome.json"


class RunArtifacts:
    """Owns one run's directory and every path inside it."""

    def __init__(self, run_id: int | str, out_dir: Path | str | None = None) -> None:
        self.run_id = run_id
        if out_dir is not None:
            self.dir = Path(out_dir)
        else:
            from smartscraper.config import get_settings

            self.dir = get_settings().runs_dir / str(run_id)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._records = 0
        self._records_fh: Any = None
        self.written: dict[str, Path] = {}

    # -- paths --------------------------------------------------------------
    @property
    def log_path(self) -> Path:
        return self.dir / LOG_NAME

    @property
    def html_path(self) -> Path:
        return self.dir / HTML_NAME

    @property
    def screenshot_path(self) -> Path:
        return self.dir / SCREENSHOT_NAME

    @property
    def records_path(self) -> Path:
        return self.dir / RECORDS_NAME

    @property
    def trace_path(self) -> Path:
        return self.dir / TRACE_NAME

    @property
    def har_path(self) -> Path:
        return self.dir / HAR_NAME

    @property
    def outcome_path(self) -> Path:
        return self.dir / OUTCOME_NAME

    def path(self, name: str) -> Path:
        """An extra artifact, e.g. a per-step screenshot."""
        return self.dir / name

    # -- writers ------------------------------------------------------------
    def save_html(self, html: str, *, name: str = HTML_NAME) -> Path:
        target = self.dir / name
        target.write_text(html or "", encoding="utf-8")
        self.written[name] = target
        return target

    def save_json(self, name: str, payload: Any) -> Path:
        target = self.dir / name
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        self.written[name] = target
        return target

    def append_record(self, record: dict[str, Any]) -> None:
        """Stream records to disk as they are emitted, so a run killed by the
        wall-clock timeout still leaves the rows it had already found."""
        if self._records_fh is None:
            self._records_fh = self.records_path.open("a", encoding="utf-8")
            self.written[RECORDS_NAME] = self.records_path
        self._records_fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        self._records_fh.flush()
        self._records += 1

    def append_records(self, records: list[dict[str, Any]]) -> None:
        for record in records:
            self.append_record(record)

    def adopt(self, src: Path | None, name: str) -> Path | None:
        """Move a file produced elsewhere (a trace, a HAR) into the run dir."""
        if src is None or not Path(src).exists():
            return None
        dest = self.dir / name
        if Path(src).resolve() != dest.resolve():
            shutil.move(str(src), dest)
        self.written[name] = dest
        return dest

    def save_outcome(self, outcome: RunOutcome) -> Path:
        return self.save_json(OUTCOME_NAME, outcome_to_dict(outcome))

    # -- reading back -------------------------------------------------------
    def read_records(self) -> list[dict[str, Any]]:
        if not self.records_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.records_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def read_log(self) -> list[dict[str, Any]]:
        if not self.log_path.exists():
            return []
        out: list[dict[str, Any]] = []
        for line in self.log_path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    @property
    def record_count(self) -> int:
        return self._records

    def existing(self) -> dict[str, Path]:
        """Every artifact actually on disk right now."""
        return {p.name: p for p in sorted(self.dir.iterdir()) if p.is_file()}

    def close(self) -> None:
        if self._records_fh is not None:
            self._records_fh.close()
            self._records_fh = None

    def __enter__(self) -> RunArtifacts:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def outcome_to_dict(outcome: RunOutcome) -> dict[str, Any]:
    """`RunOutcome` as JSON-safe data. Paths become strings."""
    data = asdict(outcome) if is_dataclass(outcome) else dict(outcome)
    data["artifacts"] = {k: str(v) for k, v in (outcome.artifacts or {}).items()}
    data["row_count"] = len(outcome.rows)
    return data


def outcome_from_dict(data: dict[str, Any]) -> RunOutcome:
    """Inverse of `outcome_to_dict`, for the scheduler reading a child's output."""
    return RunOutcome(
        rows=data.get("rows", []),
        engine_used=data.get("engine_used", ""),
        escalation_level=int(data.get("escalation_level", 0)),
        proxy_used=data.get("proxy_used"),
        blocked=bool(data.get("blocked", False)),
        block_reason=data.get("block_reason"),
        error=data.get("error"),
        artifacts={k: Path(v) for k, v in (data.get("artifacts") or {}).items()},
        steps=data.get("steps", []),
    )


def prune(root: Path, *, keep_days: int, traces_days: int, now: datetime | None = None) -> list[Path]:
    """Delete run directories past retention; traces go first, on a shorter clock.

    Returns what was removed so the caller can log it. Records and outcome.json
    are never deleted ahead of the directory as a whole: they are the run's
    evidence, and the DB rows point at them.
    """
    now = now or datetime.now(UTC)
    removed: list[Path] = []
    if not root.exists():
        return removed
    for run_dir in sorted(root.iterdir()):
        if not run_dir.is_dir():
            continue
        age_days = (now - datetime.fromtimestamp(run_dir.stat().st_mtime, UTC)).days
        if age_days >= keep_days:
            shutil.rmtree(run_dir, ignore_errors=True)
            removed.append(run_dir)
            continue
        if age_days >= traces_days:
            for name in (TRACE_NAME, HAR_NAME):
                target = run_dir / name
                if target.exists():
                    target.unlink()
                    removed.append(target)
    return removed
