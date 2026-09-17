"""Turn the string a selector produced into the typed value the schema wants.

Every parser returns `None` rather than raising when the input does not fit:
a null is a measurable thing the validator can act on (`max_null_rate`),
an exception halfway through a 500-row extraction is not.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any
from urllib.parse import urljoin

from smartscraper.dsl.models import Parse

# 1.234,56 / 1,234.56 / 1234.56 / 1 234,56
_NUM = re.compile(r"[-+]?\d[\d\s., ']*\d|\d")
_TRUE = {"true", "yes", "y", "1", "on", "in stock", "available", "✓"}
_FALSE = {"false", "no", "n", "0", "off", "out of stock", "unavailable", "✗"}

_DATE_FORMATS = (
    "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d.%m.%Y",
    "%Y/%m/%d", "%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y", "%Y%m%d",
)
_DATETIME_FORMATS = (
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M", "%m/%d/%Y %H:%M:%S",
)


def clean_text(value: str | None) -> str | None:
    """Collapse whitespace the way a browser renders it. Empty becomes None."""
    if value is None:
        return None
    t = re.sub(r"[\s ]+", " ", value).strip()
    return t or None


def parse_number(raw: str) -> float | None:
    """Pull the first number out of a string, guessing the decimal separator.

    Rule used: whichever of `.` or `,` appears last is the decimal separator,
    unless the tail after it is not 1-2 digits, in which case both are group
    separators. Documented heuristic, not a locale-aware parse.
    """
    text = clean_text(raw)
    if not text:
        return None
    m = _NUM.search(text)
    if not m:
        return None
    token = re.sub(r"[\s ']", "", m.group(0))
    sign = -1.0 if token.startswith("-") else 1.0
    token = token.lstrip("+-")
    last_dot, last_comma = token.rfind("."), token.rfind(",")
    dec = max(last_dot, last_comma)
    if dec == -1:
        digits, frac = token, ""
    else:
        tail = token[dec + 1 :]
        if len(tail) in (1, 2) and tail.isdigit():
            digits, frac = token[:dec], tail
        else:
            digits, frac = token, ""
    digits = re.sub(r"[.,]", "", digits)
    if not digits.isdigit():
        return None
    try:
        return sign * float(f"{digits}.{frac}" if frac else digits)
    except ValueError:
        return None


def parse_money(raw: str) -> float | None:
    """Price as a float. Currency symbol is dropped, not returned: the schema
    carries the currency, the record carries the number."""
    return parse_number(raw)


def parse_bool(raw: str) -> bool | None:
    t = (clean_text(raw) or "").lower()
    if t in _TRUE:
        return True
    if t in _FALSE:
        return False
    return None


def parse_date(raw: str) -> str | None:
    t = clean_text(raw)
    if not t:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(t, fmt).date().isoformat()
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(t)
    except ValueError:
        return None
    return (parsed.date() if isinstance(parsed, datetime) else parsed).isoformat()


def parse_datetime(raw: str) -> str | None:
    t = clean_text(raw)
    if not t:
        return None
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00")).isoformat()
    except ValueError:
        pass
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(t, fmt).isoformat()
        except ValueError:
            continue
    d = parse_date(t)
    return f"{d}T00:00:00" if d else None


def parse_json(raw: str) -> Any:
    t = clean_text(raw)
    if not t:
        return None
    try:
        return json.loads(t)
    except (ValueError, TypeError):
        return None


def apply_regex(value: str | None, pattern: str) -> str | None:
    """First capture group if the pattern has one, else the whole match."""
    if value is None:
        return None
    try:
        m = re.search(pattern, value, re.DOTALL)
    except re.error:
        return None
    if not m:
        return None
    return m.group(1) if m.groups() else m.group(0)


def absolutise(value: str | None, base_url: str | None) -> str | None:
    if value is None or not base_url:
        return value
    try:
        return urljoin(base_url, value)
    except ValueError:
        return value


def coerce(raw: str | None, parse: Parse | str) -> Any:
    """Apply one `Parse` mode. Unknown modes fall through to cleaned text."""
    kind = parse.value if isinstance(parse, Parse) else str(parse)
    if raw is None:
        return None
    if kind == Parse.TEXT:
        return clean_text(raw)
    if kind == Parse.MONEY:
        return parse_money(raw)
    if kind == Parse.INT:
        n = parse_number(raw)
        return None if n is None else int(n)
    if kind == Parse.FLOAT:
        return parse_number(raw)
    if kind == Parse.BOOL:
        return parse_bool(raw)
    if kind == Parse.DATE:
        return parse_date(raw)
    if kind == Parse.DATETIME:
        return parse_datetime(raw)
    if kind == Parse.JSON:
        return parse_json(raw)
    return clean_text(raw)


def isoformat(value: date | datetime) -> str:  # pragma: no cover - trivial helper
    return value.isoformat()
