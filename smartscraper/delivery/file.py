"""Write a run's records to a local file: jsonl, csv, json or parquet.

Files land under ``data/exports`` unless the target config names a ``dir`` or a
full ``path``. The filename is a template so a scheduled scraper does not
overwrite yesterday's export; ``mode: overwrite`` opts back into a single file.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from smartscraper.config import get_settings
from smartscraper.contracts import DeliveryResult
from smartscraper.delivery.base import BaseSink, register

FORMATS = ("jsonl", "json", "csv", "parquet")
DEFAULT_TEMPLATE = "{scraper}-{run_id}-{ts}.{ext}"


def export_dir() -> Path:
    return get_settings().data_dir / "exports"


def _columns(rows: list[dict[str, Any]], config: dict[str, Any]) -> list[str]:
    explicit = config.get("columns")
    if explicit:
        return [str(c) for c in explicit]
    seen: list[str] = []
    for row in rows:
        for key in row:
            if key not in seen:
                seen.append(key)
    return seen


def _flatten(value: Any) -> Any:
    """CSV cannot hold a dict. Anything nested becomes compact JSON."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def render_jsonl(rows: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in rows)


def render_csv(rows: list[dict[str, Any]], config: dict[str, Any] | None = None) -> str:
    config = config or {}
    cols = _columns(rows, config)
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
    if config.get("header", True):
        writer.writeheader()
    for row in rows:
        writer.writerow({c: _flatten(row.get(c)) for c in cols})
    return buf.getvalue()


def resolve_path(config: dict[str, Any], *, run_id: int, scraper: str, fmt: str) -> Path:
    if config.get("path"):
        return Path(str(config["path"])).expanduser()
    directory = Path(str(config["dir"])).expanduser() if config.get("dir") else export_dir()
    template = str(config.get("filename") or DEFAULT_TEMPLATE)
    ext = "jsonl" if fmt == "jsonl" else fmt
    name = template.format(
        scraper=scraper or "scraper",
        run_id=run_id,
        ts=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        date=datetime.now(UTC).strftime("%Y-%m-%d"),
        ext=ext,
        fmt=fmt,
    )
    return directory / name


@register
class FileSink(BaseSink):
    kind = "file"
    batched = False

    async def send(
        self,
        rows: list[dict[str, Any]],
        *,
        config: dict[str, Any],
        run_id: int,
        scraper: str,
        meta: dict[str, Any],
    ) -> DeliveryResult:
        fmt = str(config.get("fmt") or "jsonl").lower()
        if fmt in ("json", "jsonl", "ndjson"):
            fmt = "jsonl" if fmt in ("jsonl", "ndjson") else "json"
        if fmt not in FORMATS:
            return DeliveryResult(ok=False, retryable=False,
                                  error=f"unsupported file format {fmt!r}; use one of {', '.join(FORMATS)}")

        path = resolve_path(config, run_id=run_id, scraper=scraper, fmt=fmt)
        path.parent.mkdir(parents=True, exist_ok=True)
        append = str(config.get("mode", "")).lower() == "append" and fmt == "jsonl"

        try:
            if fmt == "parquet":
                self._write_parquet(path, rows, config)
            elif fmt == "json":
                path.write_text(json.dumps(rows, ensure_ascii=False, default=str, indent=2), "utf-8")
            elif fmt == "csv":
                path.write_text(render_csv(rows, config), "utf-8")
            else:
                text = render_jsonl(rows)
                with path.open("a" if append else "w", encoding="utf-8") as fh:
                    fh.write(text)
        except RuntimeError as exc:
            return DeliveryResult(ok=False, retryable=False, error=str(exc))
        except OSError as exc:
            return DeliveryResult(ok=False, retryable=True, error=f"{type(exc).__name__}: {exc}")

        return DeliveryResult(ok=True, rows_sent=len(rows), detail=f"wrote {len(rows)} rows to {path}")

    @staticmethod
    def _write_parquet(path: Path, rows: list[dict[str, Any]], config: dict[str, Any]) -> None:
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise RuntimeError(
                "parquet export needs pyarrow; install the 'export' extra "
                "(pip install 'smartscraper[export]')"
            ) from exc
        cols = _columns(rows, config)
        table = pa.table({c: [r.get(c) for r in rows] for c in cols} if cols else {})
        pq.write_table(table, path)
