"""Coercion of raw extracted strings into typed values.

Every function here returns `(value, ok)` and never raises. `ok=False` means the
raw text was present but could not be coerced; the value is then None. An empty
or missing raw value is `(None, True)`: absence is the metrics' business
(null_rate), not a parse failure.

Number parsing has to survive both "1,234.56" and "1.234,56". The rules used,
in order (design decision, not a standard):

* both separators present -> the rightmost one is the decimal separator
* only ","                -> decimal, unless exactly three digits follow it and
                             it is the only comma ("1,234" -> 1234)
* only "."                -> decimal, except for `int`, where a single "." with
                             three trailing digits is read as grouping
* several of one separator -> grouping
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from smartscraper.dsl.models import Parse

__all__ = [
    "ParsedMoney",
    "parse_bool",
    "parse_date",
    "parse_datetime",
    "parse_float",
    "parse_int",
    "parse_json",
    "parse_money",
    "parse_text",
    "parse_value",
]

# Currency symbols and ISO codes we recognise when stripping a money string.
_CURRENCY_SYMBOLS: dict[str, str] = {
    "€": "EUR",
    "$": "USD",
    "£": "GBP",
    "¥": "JPY",
    "₹": "INR",
    "₽": "RUB",
    "₩": "KRW",
    "₺": "TRY",
    "zł": "PLN",
    "kr": "SEK",
    "Kč": "CZK",
    "R$": "BRL",
    "CHF": "CHF",
}
_CURRENCY_CODES = {
    "EUR", "USD", "GBP", "JPY", "INR", "RUB", "KRW", "TRY", "PLN", "SEK", "NOK", "DKK",
    "CZK", "BRL", "CHF", "CAD", "AUD", "NZD", "CNY", "HKD", "SGD", "ZAR", "MXN",
}
# Longest first so "R$" wins over "$" and "CHF" over nothing.
_SYMBOL_ORDER = sorted(_CURRENCY_SYMBOLS, key=len, reverse=True)

_WS = re.compile(r"\s+")
_NUMBER = re.compile(r"[-+]?\d[\d.,   ']*\d|\d")
_TRUE = {"true", "t", "yes", "y", "1", "on", "in stock", "instock", "available", "ja", "oui"}
_FALSE = {"false", "f", "no", "n", "0", "off", "out of stock", "outofstock", "unavailable", "nee", "non"}

_DATE_FORMATS = (
    "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y/%m/%d",
    "%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y", "%d %B %Y %H:%M",
)
_DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M",
    "%d.%m.%Y %H:%M", "%d %B %Y %H:%M", "%B %d, %Y %H:%M",
)


@dataclass(slots=True)
class ParsedMoney:
    """An amount plus whatever currency marker was found next to it."""

    amount: float | None
    currency: str | None
    ok: bool


def _blank(raw: Any) -> bool:
    return raw is None or (isinstance(raw, str) and not raw.strip())


def _clean(raw: Any) -> str:
    """Normalise unicode spaces and collapse runs of whitespace."""
    text = str(raw)
    for space in (" ", " ", " ", "​"):
        text = text.replace(space, " ")
    return _WS.sub(" ", text).strip()


# --------------------------------------------------------------------------- text
def parse_text(raw: Any) -> tuple[str | None, bool]:
    if _blank(raw):
        return None, True
    try:
        return _clean(raw), True
    except Exception:
        return None, False


# --------------------------------------------------------------------------- numbers
def _strip_grouping(token: str, *, dot_is_grouping_when_triple: bool) -> str | None:
    """Turn a human-written number into something float() accepts, or None."""
    token = token.strip().replace(" ", "").replace("'", "")
    sign = ""
    if token[:1] in "+-":
        sign, token = ("-" if token[0] == "-" else ""), token[1:]
    if not token or not re.fullmatch(r"[\d.,]+", token):
        return None

    dots, commas = token.count("."), token.count(",")
    if dots and commas:
        dec = "." if token.rfind(".") > token.rfind(",") else ","
        grp = "," if dec == "." else "."
        token = token.replace(grp, "").replace(dec, ".")
    elif commas:
        tail = token.rsplit(",", 1)[1]
        token = token.replace(",", "") if (commas > 1 or len(tail) == 3) else token.replace(",", ".")
    elif dots:
        tail = token.rsplit(".", 1)[1]
        if dots > 1 or (dot_is_grouping_when_triple and len(tail) == 3):
            token = token.replace(".", "")
    if not token or token.count(".") > 1:
        return None
    return sign + token


def _to_float(raw: Any, *, dot_is_grouping_when_triple: bool = False) -> tuple[float | None, bool]:
    if _blank(raw):
        return None, True
    if isinstance(raw, bool):
        return None, False
    if isinstance(raw, int | float):
        return float(raw), True
    try:
        match = _NUMBER.search(_clean(raw).replace(" ", ""))
        if not match:
            return None, False
        # Keep a leading sign that the regex may have skipped over.
        start = match.start()
        prefix = _clean(raw).replace(" ", "")[:start]
        token = match.group(0)
        if prefix.endswith("-"):
            token = "-" + token
        normalised = _strip_grouping(token, dot_is_grouping_when_triple=dot_is_grouping_when_triple)
        if normalised is None:
            return None, False
        return float(normalised), True
    except Exception:
        return None, False


def parse_float(raw: Any) -> tuple[float | None, bool]:
    return _to_float(raw)


def parse_int(raw: Any) -> tuple[int | None, bool]:
    """Integers only. "3.7" is a failure, not a silent truncation."""
    if _blank(raw):
        return None, True
    if isinstance(raw, bool):
        return None, False
    if isinstance(raw, int):
        return raw, True
    value, ok = _to_float(raw, dot_is_grouping_when_triple=True)
    if not ok or value is None:
        return None, ok and value is None
    if abs(value - round(value)) > 1e-9:
        return None, False
    return int(round(value)), True


def parse_money(raw: Any) -> ParsedMoney:
    """Amount plus currency. "€8,95", "$1,234.56" and "1.234,56 €" all work."""
    if _blank(raw):
        return ParsedMoney(None, None, True)
    try:
        text = _clean(raw)
        currency: str | None = None
        for symbol in _SYMBOL_ORDER:
            if symbol in text:
                currency = _CURRENCY_SYMBOLS[symbol]
                text = text.replace(symbol, " ")
                break
        if currency is None:
            for word in re.findall(r"[A-Za-z]{3}", text):
                if word.upper() in _CURRENCY_CODES:
                    currency = word.upper()
                    text = re.sub(rf"\b{word}\b", " ", text)
                    break
        amount, ok = _to_float(text)
        if not ok or amount is None:
            return ParsedMoney(None, currency, False)
        return ParsedMoney(amount, currency, True)
    except Exception:
        return ParsedMoney(None, None, False)


# --------------------------------------------------------------------------- dates
def parse_date(raw: Any) -> tuple[str | None, bool]:
    """ISO date string, `YYYY-MM-DD`."""
    if _blank(raw):
        return None, True
    if isinstance(raw, datetime):
        return raw.date().isoformat(), True
    if isinstance(raw, date):
        return raw.isoformat(), True
    text = _clean(raw)
    try:
        return date.fromisoformat(text).isoformat(), True
    except Exception:
        pass
    stamp, ok = _parse_datetime_obj(text)
    if ok and stamp is not None:
        return stamp.date().isoformat(), True
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat(), True
        except Exception:
            continue
    return None, False


def _parse_datetime_obj(text: str) -> tuple[datetime | None, bool]:
    candidate = text.replace("Z", "+00:00") if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(candidate), True
    except Exception:
        pass
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(text, fmt), True
        except Exception:
            continue
    return None, False


def parse_datetime(raw: Any) -> tuple[str | None, bool]:
    """ISO 8601 datetime string; timezone kept when the input carried one."""
    if _blank(raw):
        return None, True
    if isinstance(raw, datetime):
        return raw.isoformat(), True
    if isinstance(raw, date):
        return datetime(raw.year, raw.month, raw.day).isoformat(), True
    text = _clean(raw)
    stamp, ok = _parse_datetime_obj(text)
    if ok and stamp is not None:
        return stamp.isoformat(), True
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).isoformat(), True
        except Exception:
            continue
    return None, False


# --------------------------------------------------------------------------- misc
def parse_bool(raw: Any) -> tuple[bool | None, bool]:
    if _blank(raw):
        return None, True
    if isinstance(raw, bool):
        return raw, True
    if isinstance(raw, int | float):
        return bool(raw), True
    text = _clean(raw).lower()
    if text in _TRUE:
        return True, True
    if text in _FALSE:
        return False, True
    return None, False


def parse_json(raw: Any) -> tuple[Any, bool]:
    if _blank(raw):
        return None, True
    if isinstance(raw, dict | list):
        return raw, True
    try:
        return json.loads(_clean(raw)), True
    except Exception:
        return None, False


# --------------------------------------------------------------------------- dispatch
def parse_value(raw: Any, kind: Parse | str = Parse.TEXT) -> tuple[Any, bool]:
    """Coerce `raw` per a DSL `parse:` kind. Never raises.

    Money returns the amount only; `parse_money` also gives the currency for
    callers that want to keep it.
    """
    try:
        kind = Parse(kind) if not isinstance(kind, Parse) else kind
    except Exception:
        return parse_text(raw)

    if kind is Parse.TEXT:
        return parse_text(raw)
    if kind is Parse.MONEY:
        money = parse_money(raw)
        return money.amount, money.ok
    if kind is Parse.INT:
        return parse_int(raw)
    if kind is Parse.FLOAT:
        return parse_float(raw)
    if kind is Parse.DATE:
        return parse_date(raw)
    if kind is Parse.DATETIME:
        return parse_datetime(raw)
    if kind is Parse.BOOL:
        return parse_bool(raw)
    if kind is Parse.JSON:
        return parse_json(raw)
    return parse_text(raw)
