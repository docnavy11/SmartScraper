"""The browser rungs: Playwright, Patchright, Camoufox.

Three things shape this module.

1. **It must import with no browser installed.** Every Playwright import is
   inside a function. Importing this module compiles code and nothing else, so
   the DSL, the tests and the web UI can reason about browser runs on a host
   that has never downloaded Chromium. `probe_availability()` reports the truth
   without launching anything.
2. **The stealth choice is a launcher swap, not a code fork.** Patchright
   exposes the Playwright API, so `LAUNCHERS` maps an engine name to an import
   path and the rest of the class is identical. Whether any of them beats the
   others on a real target is **unmeasured**; see PLAN.md section 3.
3. **Failure artifacts are decided up front.** A Playwright trace and a HAR must
   be armed before the first navigation, so both are started always and thrown
   away on success. `finish(failed=True)` is what keeps them.
"""

from __future__ import annotations

import contextlib
import shutil
from pathlib import Path
from typing import Any

from smartscraper.contracts import PageSnapshot, ProbeResult
from smartscraper.runner.engines.base import as_selector, trim
from smartscraper.runner.errors import EngineUnavailable
from smartscraper.runner.locators import Selector

#: engine name -> (module providing `async_playwright`, browser attribute)
LAUNCHERS: dict[str, tuple[str, str]] = {
    "playwright": ("playwright.async_api", "chromium"),
    "patchright": ("patchright.async_api", "chromium"),
    "camoufox": ("camoufox.async_api", "firefox"),
}

#: Flags Patchright's own docs call load-bearing for its detection profile:
#: no --disable-blink-features=AutomationControlled, no custom user agent.
#: **Claim**, from vendor documentation, not measured here.
SAFE_ARGS: list[str] = ["--no-sandbox", "--disable-dev-shm-usage"]


def parse_proxy(proxy: str | dict[str, Any] | None) -> dict[str, Any] | None:
    """Accept either a Playwright proxy dict or a `scheme://user:pass@host:port`
    URL, and always hand Playwright the dict form it wants."""
    if proxy is None:
        return None
    if isinstance(proxy, dict):
        return {k: v for k, v in proxy.items() if v is not None}
    from urllib.parse import unquote, urlparse

    parts = urlparse(proxy if "://" in proxy else f"http://{proxy}")
    if not parts.hostname:
        raise EngineUnavailable(f"proxy {proxy!r} has no host")
    port = f":{parts.port}" if parts.port else ""
    out: dict[str, Any] = {"server": f"{parts.scheme}://{parts.hostname}{port}"}
    if parts.username:
        out["username"] = unquote(parts.username)
    if parts.password:
        out["password"] = unquote(parts.password)
    return out


def probe_availability(engine: str = "patchright") -> tuple[bool, str]:
    """`(usable, reason)` without launching anything.

    Distinguishes "package missing" from "package present, browser binary not
    downloaded", because the fix differs and the escalation log should say which.
    """
    spec = LAUNCHERS.get(engine)
    if spec is None:
        return False, f"unknown browser engine {engine!r}"
    module, _ = spec
    import importlib.util

    if importlib.util.find_spec(module.split(".")[0]) is None:
        return False, f"package {module.split('.')[0]!r} is not installed"
    if engine == "camoufox":
        return True, "camoufox package present; binary state unknown until launch"
    paths = _browser_dirs(engine)
    found = [p for p in paths if p.exists() and any(p.iterdir())]
    if not found:
        listed = ", ".join(str(p) for p in paths)
        return False, f"no browser binary downloaded (looked in {listed}); run `{engine} install chromium`"
    return True, f"browser binaries under {found[0]}"


def _browser_dirs(engine: str) -> list[Path]:
    import os

    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if override and override not in ("0",):
        return [Path(override)]
    home = Path.home()
    if engine == "patchright":
        return [home / ".cache" / "ms-playwright", home / ".cache" / "patchright"]
    return [home / ".cache" / "ms-playwright"]


class PwElement:
    """One Playwright `Locator`, wearing the `Element` interface."""

    __slots__ = ("_loc",)

    def __init__(self, locator: Any) -> None:
        self._loc = locator

    @property
    def locator(self) -> Any:
        return self._loc

    async def text(self) -> str | None:
        return await self._loc.text_content()

    async def attr(self, name: str) -> str | None:
        if name in ("text", "innerText", "textContent"):
            return await self._loc.text_content()
        if name in ("html", "innerHTML", "inner_html"):
            return await self._loc.inner_html()
        if name in ("outerHTML", "outer_html"):
            return await self._loc.evaluate("el => el.outerHTML")
        return await self._loc.get_attribute(name)

    async def inner_html(self) -> str | None:
        return await self._loc.inner_html()

    async def query(self, selector: str | Selector, *,
                    limit: int | None = None) -> list[PwElement]:
        inner = self._loc.locator(as_selector(selector).normalised)
        n = await inner.count()
        if limit is not None:
            n = min(n, limit)
        return [PwElement(inner.nth(i)) for i in range(n)]

    async def count(self, selector: str | Selector) -> int:
        return await self._loc.locator(as_selector(selector).normalised).count()


class BrowserEngine:
    """`Engine` over the Playwright async API, whichever package provides it."""

    interactive = True

    def __init__(
        self,
        *,
        engine: str = "patchright",
        artifacts_dir: Path | None = None,
        trace: bool = True,
        har: bool = True,
        persistent: bool = False,
        viewport: dict[str, int] | None = None,
        locale: str = "en-US",
        timezone: str | None = None,
        slow_mo_ms: int = 0,
    ) -> None:
        if engine not in LAUNCHERS:
            raise EngineUnavailable(f"unknown browser engine {engine!r}")
        self.name = engine
        self.artifacts_dir = Path(artifacts_dir) if artifacts_dir else None
        self.trace = trace
        self.har = har
        self.persistent = persistent
        self.viewport = viewport or {"width": 1440, "height": 900}
        self.locale = locale
        self.timezone = timezone
        self.slow_mo_ms = slow_mo_ms

        self.current_url = ""
        self.last_status: int | None = None
        self.last_headers: dict[str, str] = {}
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._tracing = False
        self._har_path: Path | None = None
        self._profile: str | None = None
        #: Which browser channel actually launched, and why a preferred one did not.
        self.channel_used: str | None = None
        self.channel_fallback_reason: str | None = None

    # -- lifecycle ----------------------------------------------------------
    async def open(self, *, proxy: str | dict[str, Any] | None = None,
                   profile: str | None = None, headed: bool = False) -> None:
        module, browser_attr = LAUNCHERS[self.name]
        try:
            mod = __import__(module, fromlist=["async_playwright"])
        except ModuleNotFoundError as exc:
            raise EngineUnavailable(f"{module} is not installed") from exc
        self._profile = profile
        proxy_dict = parse_proxy(proxy)

        self._pw = await mod.async_playwright().start()
        launcher = getattr(self._pw, browser_attr)

        context_args: dict[str, Any] = {
            "viewport": self.viewport,
            "locale": self.locale,
            "ignore_https_errors": True,
        }
        if self.timezone:
            context_args["timezone_id"] = self.timezone
        if proxy_dict:
            context_args["proxy"] = proxy_dict
        if self.har and self.artifacts_dir:
            self._har_path = Path(self.artifacts_dir) / "network.har"
            self._har_path.parent.mkdir(parents=True, exist_ok=True)
            context_args["record_har_path"] = str(self._har_path)
            context_args["record_har_content"] = "omit"

        launch_args: dict[str, Any] = {
            "headless": not headed,
            "args": SAFE_ARGS,
            "slow_mo": self.slow_mo_ms,
        }
        try:
            if self.persistent:
                user_data_dir = _profile_dir(profile)
                user_data_dir.mkdir(parents=True, exist_ok=True)
                self._context = await self._with_channel_fallback(
                    lambda **kw: launcher.launch_persistent_context(
                        str(user_data_dir), **launch_args, **context_args, **kw
                    )
                )
                self._browser = None
            else:
                self._browser = await self._with_channel_fallback(
                    lambda **kw: launcher.launch(**launch_args, **kw)
                )
                state = _storage_state(profile)
                if state is not None:
                    context_args["storage_state"] = str(state)
                self._context = await self._browser.new_context(**context_args)
        except EngineUnavailable:
            await self.close()
            raise
        except Exception as exc:
            await self.close()
            raise EngineUnavailable(f"could not launch {self.name}: {exc}") from exc

        if self.trace:
            try:
                await self._context.tracing.start(screenshots=True, snapshots=True, sources=False)
                self._tracing = True
            except Exception:
                self._tracing = False

        pages = getattr(self._context, "pages", [])
        self._page = pages[0] if pages else await self._context.new_page()
        self._page.on("response", self._note_response)

    async def _with_channel_fallback(self, launch: Any) -> Any:
        """Launch on the preferred channel, falling back to bundled Chromium.

        Patchright's docs say a real Chrome channel is part of what it relies on
        (**claim**, unmeasured here), but Chrome is frequently absent on a
        server while Chromium is present. Failing the whole rung over a missing
        channel would be a worse outcome than running on a fingerprint the
        vendor likes slightly less, so the fallback is taken and logged by the
        caller through the returned engine's `channel_used`.
        """
        preferred = _channel(self.name)
        if preferred:
            try:
                result = await launch(channel=preferred)
                self.channel_used = preferred
                return result
            except Exception as exc:
                if "is not found" not in str(exc) and "Executable doesn" not in str(exc):
                    raise
                self.channel_fallback_reason = str(exc).splitlines()[0]
        try:
            result = await launch()
        except Exception as exc:
            raise EngineUnavailable(f"could not launch {self.name}: {exc}") from exc
        self.channel_used = "chromium"
        return result

    def _note_response(self, response: Any) -> None:
        """Keep the status and headers of the main document, for block detection."""
        with contextlib.suppress(Exception):
            request = response.request
            if request.resource_type in ("document", "") and request.is_navigation_request():
                self.last_status = response.status
                self.last_headers = {k.lower(): v for k, v in response.headers.items()}

    async def close(self) -> None:
        for closer in (self._context, self._browser, self._pw):
            if closer is None:
                continue
            with contextlib.suppress(Exception):
                await (closer.stop() if closer is self._pw else closer.close())
        self._context = self._browser = self._pw = self._page = None

    async def finish(self, *, failed: bool) -> dict[str, Path]:
        """Close down, keeping trace and HAR only when the run failed.

        Kept separate from `close()` so `close()` stays safe to call from an
        exception handler that knows nothing about the run's verdict.
        """
        kept: dict[str, Path] = {}
        if self._context is not None and self._tracing:
            try:
                if failed and self.artifacts_dir:
                    dest = Path(self.artifacts_dir) / "trace.zip"
                    await self._context.tracing.stop(path=str(dest))
                    kept["trace"] = dest
                else:
                    await self._context.tracing.stop()
            except Exception:
                pass
            self._tracing = False
        await self.close()
        if self._har_path is not None and self._har_path.exists():
            if failed:
                kept["har"] = self._har_path
            else:
                self._har_path.unlink(missing_ok=True)
        return kept

    async def save_profile(self, name: str) -> Path | None:
        """Write the context's storage_state, so a manual login can be replayed.

        Persistent contexts keep their state in the user-data dir already, so
        there is nothing to write for those.
        """
        if self._context is None or self.persistent:
            return None
        from smartscraper.config import get_settings

        path = get_settings().profiles_dir / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            await self._context.storage_state(path=str(path), indexed_db=True)
        except TypeError:  # older Playwright without indexed_db
            await self._context.storage_state(path=str(path))
        return path

    # -- navigation ---------------------------------------------------------
    @property
    def page(self) -> Any:
        """The Playwright `Page`, which is what `custom_python` receives."""
        if self._page is None:
            raise EngineUnavailable("browser engine is not open")
        return self._page

    async def goto(self, url: str, *, timeout_ms: int = 30_000,
                   wait_until: str = "domcontentloaded") -> int:
        response = await self.page.goto(url, timeout=timeout_ms, wait_until=wait_until)
        self.current_url = self.page.url
        if response is not None:
            self.last_status = response.status
            try:
                self.last_headers = {k.lower(): v for k, v in response.headers.items()}
            except Exception:
                self.last_headers = {}
        return self.last_status or 0

    async def content(self) -> str:
        return await self.page.content()

    # -- querying -----------------------------------------------------------
    def _locator(self, selector: str | Selector) -> Any:
        return self.page.locator(as_selector(selector).normalised)

    async def query(self, selector: str | Selector, *,
                    limit: int | None = None) -> list[PwElement]:
        loc = self._locator(selector)
        n = await loc.count()
        if limit is not None:
            n = min(n, limit)
        return [PwElement(loc.nth(i)) for i in range(n)]

    async def count(self, selector: str | Selector) -> int:
        return await self._locator(selector).count()

    async def wait_for(self, selector: str | Selector | None, *, state: str = "visible",
                       timeout_ms: int = 15_000, url_contains: str | None = None) -> bool:
        if url_contains is not None:
            await self.page.wait_for_url(f"**{url_contains}**", timeout=timeout_ms)
            self.current_url = self.page.url
            return True
        if selector is None:
            return True
        await self._locator(selector).first.wait_for(state=state, timeout=timeout_ms)
        return True

    # -- interaction --------------------------------------------------------
    async def click(self, selector: str | Selector, *, timeout_ms: int = 10_000) -> bool:
        await self._locator(selector).first.click(timeout=timeout_ms)
        self.current_url = self.page.url
        return True

    async def fill(self, selector: str | Selector, value: str, *,
                   timeout_ms: int = 10_000) -> bool:
        await self._locator(selector).first.fill(value, timeout=timeout_ms)
        return True

    async def select_option(self, selector: str | Selector, value: str, *,
                            timeout_ms: int = 10_000) -> bool:
        await self._locator(selector).first.select_option(value, timeout=timeout_ms)
        return True

    async def hover(self, selector: str | Selector, *, timeout_ms: int = 10_000) -> bool:
        await self._locator(selector).first.hover(timeout=timeout_ms)
        return True

    async def press(self, key: str, *, selector: str | Selector | None = None,
                    timeout_ms: int = 10_000) -> bool:
        if selector is None:
            await self.page.keyboard.press(key)
        else:
            await self._locator(selector).first.press(key, timeout=timeout_ms)
        return True

    async def scroll(self, *, to: str = "bottom", selector: str | Selector | None = None,
                     times: int = 1, pause_ms: int = 700) -> bool:
        for _ in range(max(1, times)):
            if to == "selector" and selector is not None:
                await self._locator(selector).first.scroll_into_view_if_needed()
            elif to == "top":
                await self.page.evaluate("window.scrollTo(0, 0)")
            else:
                await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            if pause_ms:
                await self.page.wait_for_timeout(pause_ms)
        return True

    async def screenshot(self, path: Any, *, full_page: bool = False) -> Path | None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            await self.page.screenshot(path=str(target), full_page=full_page)
        except Exception:
            return None
        return target

    # -- introspection ------------------------------------------------------
    async def probe(self, selector: str, *, limit: int = 5) -> ProbeResult:
        from smartscraper.runner.probe import probe_engine

        return await probe_engine(self, selector, limit=limit)

    async def snapshot(self, *, budget_bytes: int = 40_000) -> PageSnapshot:
        html, truncated = trim(await self.content(), budget_bytes)
        try:
            title = await self.page.title()
        except Exception:
            title = ""
        return PageSnapshot(
            url=self.page.url,
            title=title,
            accessibility_tree=await self.aria_snapshot(budget_bytes=min(budget_bytes, 16_000)),
            html=html,
            truncated=truncated,
        )

    async def aria_snapshot(self, *, budget_bytes: int = 16_000) -> str:
        """The real accessibility tree, via whichever API this version exposes."""
        try:
            text = await self.page.locator("body").aria_snapshot()
            return trim(text or "", budget_bytes)[0]
        except Exception:
            pass
        try:  # removed in newer Playwright; still present in some versions
            tree = await self.page.accessibility.snapshot()
            import json

            return trim(json.dumps(tree, ensure_ascii=False), budget_bytes)[0]
        except Exception:
            return ""


def _channel(engine: str) -> str | None:
    """Patchright's docs say to use a real Chrome channel rather than the
    bundled Chromium. **Claim**, unmeasured here; honoured because it is free."""
    return "chrome" if engine == "patchright" else None


def _profile_dir(profile: str | None) -> Path:
    from smartscraper.config import get_settings

    settings = get_settings()
    return settings.profiles_dir / (profile or "default")


def _storage_state(profile: str | None) -> Path | None:
    if not profile:
        return None
    from smartscraper.config import get_settings

    direct = Path(profile)
    if direct.is_file():
        return direct
    candidate = get_settings().profiles_dir / f"{profile}.json"
    return candidate if candidate.is_file() else None


def copy_artifact(src: Path, dest_dir: Path, name: str) -> Path | None:  # pragma: no cover
    """Move a browser-produced file into the run's artifact directory."""
    if not src.exists():
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name
    shutil.move(str(src), dest)
    return dest
