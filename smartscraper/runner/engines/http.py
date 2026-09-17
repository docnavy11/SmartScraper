"""The cheapest rung: fetch HTML over HTTP and query it with lxml.

curl_cffi does the fetching because it impersonates a browser's TLS and HTTP/2
fingerprint, which a plain requests/httpx client cannot. Whether that actually
changes pass rates on any given site is **unmeasured here**; PLAN.md section 3
records the one third-party benchmark and its limits.

What this engine cannot do is interact: no click on a JS handler, no typing, no
scrolling. `click` is the single exception, and only on an anchor, where it
means "follow this href" — which is what pagination needs and what makes
`paginate` work end to end without a browser binary.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from smartscraper.contracts import PageSnapshot, ProbeResult
from smartscraper.runner.engines.base import InteractionUnsupported, as_selector, trim
from smartscraper.runner.errors import EngineUnavailable
from smartscraper.runner.locators import Selector, SelectorSyntaxError, to_xpath

DEFAULT_IMPERSONATE = "chrome"
DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
              "image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
}


def _lxml():
    try:
        import lxml.html as lh
    except ModuleNotFoundError as exc:  # pragma: no cover - lxml is a hard dep here
        raise EngineUnavailable("the http engine needs lxml installed") from exc
    return lh


class LxmlElement:
    """One lxml node, wearing the `Element` interface."""

    __slots__ = ("_el", "_base")

    def __init__(self, el: Any, base_url: str = "") -> None:
        self._el = el
        self._base = base_url

    @property
    def node(self) -> Any:
        """The raw lxml element, for `custom_python` steps that want it."""
        return self._el

    async def text(self) -> str | None:
        return self._el.text_content()

    async def attr(self, name: str) -> str | None:
        if name in ("text", "innerText", "textContent"):
            return self._el.text_content()
        if name in ("html", "innerHTML", "inner_html"):
            return await self.inner_html()
        if name in ("outerHTML", "outer_html"):
            return _lxml().tostring(self._el, encoding="unicode")
        return self._el.get(name)

    async def inner_html(self) -> str | None:
        lh = _lxml()
        parts = [self._el.text or ""]
        parts += [lh.tostring(c, encoding="unicode") for c in self._el]
        return "".join(parts)

    async def query(self, selector: Selector, *, limit: int | None = None) -> list[LxmlElement]:
        nodes = _run_xpath(self._el, selector)
        if limit is not None:
            nodes = nodes[:limit]
        return [LxmlElement(n, self._base) for n in nodes]

    async def count(self, selector: Selector) -> int:
        return len(_run_xpath(self._el, selector))


def _run_xpath(root: Any, selector: Selector) -> list[Any]:
    """Run a compiled selector against a subtree, returning element nodes only."""
    xpath = to_xpath(selector)
    try:
        found = root.xpath(xpath)
    except Exception as exc:  # lxml raises XPathEvalError and friends
        raise SelectorSyntaxError(f"xpath {xpath!r} failed: {exc}") from exc
    if not isinstance(found, list):
        return []
    return [n for n in found if hasattr(n, "tag") and isinstance(getattr(n, "tag", None), str)]


class HttpEngine:
    """`Engine` over curl_cffi. One session, cookies kept across gotos."""

    name = "http"
    interactive = False

    def __init__(
        self,
        *,
        impersonate: str = DEFAULT_IMPERSONATE,
        verify: bool = True,
        follow_redirects: bool = True,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.impersonate = impersonate
        self.verify = verify
        self.follow_redirects = follow_redirects
        self.extra_headers = dict(extra_headers or {})
        self.current_url = ""
        self.last_status: int | None = None
        self.last_headers: dict[str, str] = {}
        self._html: str = ""
        self._doc: Any | None = None
        self._session: Any = None
        self._proxy: str | None = None

    # -- lifecycle ----------------------------------------------------------
    async def open(self, *, proxy: str | None = None, profile: str | None = None,
                   headed: bool = False) -> None:
        try:
            from curl_cffi.requests import AsyncSession
        except ModuleNotFoundError as exc:
            raise EngineUnavailable("the http engine needs curl_cffi installed") from exc
        self._proxy = proxy
        kwargs: dict[str, Any] = {
            "impersonate": self.impersonate,
            "verify": self.verify,
            "headers": {**DEFAULT_HEADERS, **self.extra_headers},
        }
        if proxy:
            kwargs["proxies"] = {"http": proxy, "https": proxy}
        self._session = AsyncSession(**kwargs)
        if profile:
            _load_cookies(self._session, profile)

    async def close(self) -> None:
        if self._session is not None:
            closer = getattr(self._session, "close", None)
            if closer is not None:
                result = closer()
                if asyncio.iscoroutine(result):
                    await result
            self._session = None

    # -- navigation ---------------------------------------------------------
    async def goto(self, url: str, *, timeout_ms: int = 30_000,
                   wait_until: str = "domcontentloaded") -> int:
        if self._session is None:
            await self.open()
        assert self._session is not None
        resp = await self._session.get(
            url,
            timeout=timeout_ms / 1000,
            allow_redirects=self.follow_redirects,
        )
        self.current_url = str(getattr(resp, "url", url))
        self.last_status = int(resp.status_code)
        self.last_headers = _headers_of(resp)
        self._set_html(resp.text or "")
        return self.last_status

    def _set_html(self, html: str) -> None:
        self._html = html
        self._doc = None

    @property
    def doc(self) -> Any:
        """Parsed document, built lazily so a block verdict costs no parse."""
        if self._doc is None:
            lh = _lxml()
            self._doc = lh.fromstring(self._html) if self._html.strip() else lh.fromstring("<html/>")
            if self.current_url:
                with contextlib.suppress(Exception):
                    self._doc.make_links_absolute(self.current_url, resolve_base_href=True)
        return self._doc

    async def content(self) -> str:
        return self._html

    def set_content(self, html: str, *, url: str = "") -> None:
        """Load HTML directly. Used by tests and by offline repair replays."""
        if url:
            self.current_url = url
        self._set_html(html)

    # -- querying -----------------------------------------------------------
    async def query(self, selector: str | Selector, *,
                    limit: int | None = None) -> list[LxmlElement]:
        nodes = _run_xpath(self.doc, as_selector(selector))
        if limit is not None:
            nodes = nodes[:limit]
        return [LxmlElement(n, self.current_url) for n in nodes]

    async def count(self, selector: str | Selector) -> int:
        return len(_run_xpath(self.doc, as_selector(selector)))

    async def wait_for(self, selector: str | Selector | None, *, state: str = "visible",
                       timeout_ms: int = 15_000, url_contains: str | None = None) -> bool:
        """Nothing changes without a new request, so this is a single check.

        `visible` cannot be distinguished from `attached` in static HTML; both
        mean "present". `detached`/`hidden` mean "absent".
        """
        if url_contains is not None:
            return url_contains in self.current_url
        if selector is None:
            return True
        present = await self.count(as_selector(selector)) > 0
        return not present if state in ("detached", "hidden") else present

    # -- interaction --------------------------------------------------------
    async def click(self, selector: str | Selector, *, timeout_ms: int = 10_000) -> bool:
        """Follow an anchor. Anything else needs a browser."""
        elements = await self.query(as_selector(selector), limit=1)
        if not elements:
            raise InteractionUnsupported("click", self.name)
        node = elements[0].node
        href = node.get("href")
        if href is None:  # an anchor inside, or an anchor wrapping, the match
            inner = node.xpath(".//a[@href]")
            outer = node.xpath("ancestor::a[@href]")
            if inner:
                href = inner[0].get("href")
            elif outer:
                href = outer[-1].get("href")
        if not href or href.startswith(("javascript:", "#")):
            raise InteractionUnsupported("click", self.name)
        await self.goto(urljoin(self.current_url or "", href), timeout_ms=timeout_ms)
        return True

    async def fill(self, selector: str | Selector, value: str, *,
                   timeout_ms: int = 10_000) -> bool:
        raise InteractionUnsupported("fill", self.name)

    async def select_option(self, selector: str | Selector, value: str, *,
                            timeout_ms: int = 10_000) -> bool:
        raise InteractionUnsupported("select", self.name)

    async def hover(self, selector: str | Selector, *, timeout_ms: int = 10_000) -> bool:
        raise InteractionUnsupported("hover", self.name)

    async def press(self, key: str, *, selector: str | Selector | None = None,
                    timeout_ms: int = 10_000) -> bool:
        raise InteractionUnsupported("press", self.name)

    async def scroll(self, *, to: str = "bottom", selector: str | Selector | None = None,
                     times: int = 1, pause_ms: int = 700) -> bool:
        """A no-op, not an error: a static document is already fully 'scrolled'.

        Raising here would fail every script written for a browser the moment it
        ran on the cheap rung, which is the opposite of what the ladder is for.
        """
        return True

    async def screenshot(self, path: Any, *, full_page: bool = False) -> Path | None:
        """No renderer, so the HTML is saved beside the requested path instead."""
        target = Path(path).with_suffix(".html")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self._html, encoding="utf-8")
        return target

    # -- introspection ------------------------------------------------------
    async def probe(self, selector: str, *, limit: int = 5) -> ProbeResult:
        from smartscraper.runner.probe import probe_engine

        return await probe_engine(self, selector, limit=limit)

    async def snapshot(self, *, budget_bytes: int = 40_000) -> PageSnapshot:
        html, truncated = trim(self._html, budget_bytes)
        return PageSnapshot(
            url=self.current_url,
            title=self._title(),
            accessibility_tree=self.outline(budget_bytes=min(budget_bytes, 8_000)),
            html=html,
            truncated=truncated,
        )

    def _title(self) -> str:
        try:
            found = self.doc.xpath("//title/text()")
        except Exception:
            return ""
        return (found[0].strip() if found else "")

    def outline(self, *, budget_bytes: int = 8_000) -> str:
        """A structural outline of the document.

        Named `outline`, not `accessibility tree`, because it is built from tag
        names and ARIA attributes in the markup, not from a rendered AX tree.
        It fills `PageSnapshot.accessibility_tree` for this engine; the browser
        engine puts a real one there.
        """
        lines: list[str] = []
        try:
            nodes = self.doc.xpath(
                "//*[self::h1 or self::h2 or self::h3 or self::nav or self::main"
                " or self::form or self::table or self::a or self::button"
                " or self::input or self::select or @role]"
            )
        except Exception:
            return ""
        for node in nodes:
            tag = node.tag if isinstance(node.tag, str) else "?"
            role = node.get("role") or tag
            label = (node.get("aria-label") or node.text_content() or node.get("value") or "").strip()
            label = " ".join(label.split())[:80]
            ident = node.get("id")
            extra = f" #{ident}" if ident else ""
            lines.append(f"{role}{extra}: {label}" if label else f"{role}{extra}")
            if sum(len(x) + 1 for x in lines) > budget_bytes:
                lines.append("… (outline truncated)")
                break
        return "\n".join(lines)

    # -- misc ---------------------------------------------------------------
    @property
    def page(self) -> HttpEngine:
        """What `custom_python` receives as `page` on this engine."""
        return self

    def origin(self) -> str:
        parts = urlparse(self.current_url or "")
        return f"{parts.scheme}://{parts.netloc}" if parts.netloc else ""


def _headers_of(resp: Any) -> dict[str, str]:
    """curl_cffi headers are multidict-ish; flatten, joining repeats with ', '."""
    raw = getattr(resp, "headers", None)
    if raw is None:
        return {}
    out: dict[str, str] = {}
    items = raw.multi_items() if hasattr(raw, "multi_items") else raw.items()
    for key, value in items:
        k = str(key).lower()
        out[k] = f"{out[k]}, {value}" if k in out else str(value)
    return out


def _load_cookies(session: Any, profile: str) -> None:
    """Reuse a browser profile's cookies for the cheap rung.

    Reads the `cookies` array of a Playwright `storage_state` JSON. localStorage
    and IndexedDB in that file are ignored: no JS runs here to read them.
    """
    import json

    from smartscraper.config import get_settings

    path = Path(profile)
    if not path.exists():
        path = get_settings().profiles_dir / f"{profile}.json"
    if not path.exists():
        return
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    for cookie in state.get("cookies", []):
        name, value = cookie.get("name"), cookie.get("value")
        if not name:
            continue
        try:
            session.cookies.set(name, value, domain=cookie.get("domain", ""),
                                path=cookie.get("path", "/"))
        except Exception:
            continue
