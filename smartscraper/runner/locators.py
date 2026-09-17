"""Selector strings, and the fallback ladder that makes one broken selector
survivable.

A DSL selector is `"<kind>=<value>"` where kind is css, text, role or xpath. A
string with no recognised prefix is treated as CSS, except one starting with
`/`, `./` or `(` which is treated as XPath.

Two consumers, one parse:
  * the browser engine hands the normalised string to Playwright, whose
    selector engines use the same four names;
  * the HTTP engine compiles the selector down to XPath and runs it with lxml.

`resolve_first` walks `[selector, *fallback_selectors]` in order, returns the
first candidate that matches anything, and records which one was used so a
repair agent can see that the primary selector has rotted.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

Kind = Literal["css", "text", "role", "xpath"]
KINDS: tuple[str, ...] = ("css", "text", "role", "xpath")

_PREFIX = re.compile(r"^\s*(css|text|role|xpath)\s*=\s*(.*)$", re.DOTALL)
# role=button[name="Save"][exact] / role=link[name='Next']
_ROLE = re.compile(
    r"^(?P<role>[a-zA-Z][\w-]*)\s*(?P<attrs>(?:\[[^\]]*\])*)\s*$", re.DOTALL
)
_ROLE_ATTR = re.compile(r"\[\s*(?P<key>[\w-]+)\s*(?:=\s*(?P<val>\"[^\"]*\"|'[^']*'|[^\]]*))?\s*\]")


class SelectorSyntaxError(ValueError):
    """The selector string is not something any engine can be handed."""


@dataclass(frozen=True, slots=True)
class Selector:
    kind: Kind
    value: str
    raw: str
    name: str | None = None
    exact: bool = False

    @property
    def normalised(self) -> str:
        """The canonical `kind=value` form, which Playwright also accepts."""
        return f"{self.kind}={self.value}"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.normalised


def parse_selector(raw: str) -> Selector:
    """Parse a DSL selector string. Never guesses silently: the fallback to CSS
    is documented behaviour, not a heuristic on the value's content."""
    if not isinstance(raw, str) or not raw.strip():
        raise SelectorSyntaxError("empty selector")
    text = raw.strip()
    m = _PREFIX.match(text)
    if m:
        kind: Kind = m.group(1)  # type: ignore[assignment]
        value = m.group(2).strip()
        if not value:
            raise SelectorSyntaxError(f"selector {raw!r} has a kind but no value")
    elif text.startswith(("/", "./", "(")):
        kind, value = "xpath", text
    else:
        kind, value = "css", text

    name: str | None = None
    exact = False
    if kind == "role":
        role, attrs = _parse_role(value, raw)
        name = attrs.get("name")
        exact = attrs.get("exact", "false").lower() not in ("false", "0", "")
        value = role if not attrs else value
    if kind == "text" and len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value, exact = value[1:-1], True
    return Selector(kind=kind, value=value, raw=raw, name=name, exact=exact)


def _parse_role(value: str, raw: str) -> tuple[str, dict[str, str]]:
    m = _ROLE.match(value)
    if not m:
        raise SelectorSyntaxError(f"role selector {raw!r} is not `role=<role>[attr=value]`")
    attrs: dict[str, str] = {}
    for a in _ROLE_ATTR.finditer(m.group("attrs") or ""):
        val = a.group("val")
        if val is None:
            val = "true"
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        attrs[a.group("key")] = val
    return m.group("role"), attrs


def candidates_of(primary: str, fallbacks: Iterable[str] | None = None) -> list[str]:
    """`[primary, *fallbacks]` with duplicates and blanks removed, order kept."""
    out: list[str] = []
    for c in (primary, *(fallbacks or [])):
        if isinstance(c, str) and c.strip() and c not in out:
            out.append(c)
    return out


@dataclass(slots=True)
class Resolution:
    """Which candidate actually matched, and how well."""

    selector: Selector
    used: str
    index: int
    count: int
    tried: list[str]

    @property
    def is_fallback(self) -> bool:
        return self.index > 0


async def resolve_first(
    primary: str,
    fallbacks: Sequence[str] | None,
    count_fn: Callable[[Selector], Awaitable[int]],
    *,
    minimum: int = 1,
) -> Resolution | None:
    """Try each candidate in order; return the first with >= `minimum` matches.

    `count_fn` is supplied by the engine, so this function is engine-agnostic
    and unit-testable with a dict of fakes. A candidate that fails to parse is
    skipped rather than raised, so one malformed fallback cannot kill a run.
    """
    tried: list[str] = []
    for i, raw in enumerate(candidates_of(primary, fallbacks)):
        tried.append(raw)
        try:
            sel = parse_selector(raw)
        except SelectorSyntaxError:
            continue
        try:
            n = await count_fn(sel)
        except Exception:
            continue
        if n >= minimum:
            return Resolution(selector=sel, used=raw, index=i, count=n, tried=tried)
    return None


# --------------------------------------------------------------------- xpath
# Implicit ARIA roles, limited on purpose to the ones a scrape script actually
# uses. Anything absent still matches an explicit role="..." attribute.
IMPLICIT_ROLES: dict[str, list[str]] = {
    "link": ["a[@href]", "area[@href]"],
    "button": ["button", "input[@type='button' or @type='submit' or @type='reset']", "summary"],
    "heading": ["h1", "h2", "h3", "h4", "h5", "h6"],
    "listitem": ["li"],
    "list": ["ul", "ol"],
    "textbox": [
        "textarea",
        "input[not(@type) or @type='text' or @type='search' or @type='email'"
        " or @type='url' or @type='tel' or @type='password']",
    ],
    "checkbox": ["input[@type='checkbox']"],
    "radio": ["input[@type='radio']"],
    "combobox": ["select"],
    "img": ["img[@alt!='']", "img[not(@alt)]"],
    "table": ["table"],
    "row": ["tr"],
    "cell": ["td"],
    "columnheader": ["th"],
    "form": ["form"],
    "navigation": ["nav"],
    "main": ["main"],
    "article": ["article"],
    "region": ["section"],
    "separator": ["hr"],
    "option": ["option"],
    "paragraph": ["p"],
}

# Normalised visible text of an element, used by text= and role name matching.
_TEXT = "normalize-space(string(.))"


def to_xpath(sel: Selector) -> str:
    """Compile a parsed selector to an XPath expression lxml can run.

    CSS goes through cssselect, which is a real translator, not a regex.
    """
    if sel.kind == "xpath":
        return sel.value
    if sel.kind == "css":
        return _css_to_xpath(sel.value)
    if sel.kind == "text":
        return _text_xpath(sel.value, exact=sel.exact)
    if sel.kind == "role":
        return _role_xpath(sel)
    raise SelectorSyntaxError(f"unsupported selector kind {sel.kind!r}")


def _css_to_xpath(css: str) -> str:
    try:
        from cssselect import GenericTranslator, SelectorError
    except ModuleNotFoundError as exc:  # pragma: no cover - cssselect is a hard dep
        raise SelectorSyntaxError("cssselect is required to run CSS selectors on HTML") from exc
    try:
        return GenericTranslator().css_to_xpath(css, prefix="descendant-or-self::")
    except SelectorError as exc:
        raise SelectorSyntaxError(f"bad CSS selector {css!r}: {exc}") from exc


def _xq(value: str) -> str:
    """Quote a string for XPath, including values containing both quote kinds."""
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    inner = ", \"'\", ".join(f"'{p}'" for p in parts)
    return f"concat({inner})"


def _text_xpath(value: str, *, exact: bool) -> str:
    cond = f"{_TEXT} = {_xq(value)}" if exact else f"contains({_TEXT}, {_xq(value)})"
    # Innermost element carrying the text, so `text=Next` does not match <body>.
    return f"descendant-or-self::*[{cond}][not(.//*[{cond}])]"


def _accessible_name_cond(name: str, *, exact: bool) -> str:
    """Approximates the accessible name: aria-label, then title/alt, then text.

    Approximation, not the ARIA algorithm: aria-labelledby and label-for are not
    resolved here. The browser engine uses Playwright's real implementation.
    """
    sources = ["@aria-label", "@title", "@alt", "@value", _TEXT]
    if exact:
        return " or ".join(f"normalize-space({s}) = {_xq(name)}" for s in sources)
    return " or ".join(f"contains(normalize-space({s}), {_xq(name)})" for s in sources)


def _role_xpath(sel: Selector) -> str:
    role = sel.value.split("[", 1)[0].strip()
    paths = [f"descendant-or-self::*[@role={_xq(role)}]"]
    for tag in IMPLICIT_ROLES.get(role, []):
        paths.append(f"descendant-or-self::{tag}")
    if sel.name:
        cond = _accessible_name_cond(sel.name, exact=sel.exact)
        paths = [f"{p}[{cond}]" for p in paths]
    return " | ".join(paths)
