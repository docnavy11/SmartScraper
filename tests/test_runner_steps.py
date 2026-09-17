"""Step internals that the end-to-end suite cannot reach from the HTTP rung.

Interaction ops, secrets, rate limiting and `custom_python` need either a
browser or a controlled fake. The fake here is deliberately thin: it records
calls and answers queries, and every assertion is about what `steps.py` decided,
not about what the fake did.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from smartscraper.dsl.models import ScrapeScript
from smartscraper.runner.artifacts import RunArtifacts
from smartscraper.runner.engines.base import InteractionUnsupported
from smartscraper.runner.engines.http import HttpEngine
from smartscraper.runner.errors import StepError, ValidationFailed
from smartscraper.runner.runlog import null_logger
from smartscraper.runner.steps import (
    MissingVariable,
    StepContext,
    interpolate,
    run_steps,
)

PAGE = """<html><body>
  <div class="row"><span class="a">one</span><span class="b">1</span></div>
  <div class="row"><span class="a">two</span><span class="b">2</span></div>
  <form><input id="user"><input id="pw"></form>
</body></html>"""


def script(body: str) -> ScrapeScript:
    return ScrapeScript.from_yaml(
        "rate_limit: {min_delay_s: 0, max_delay_s: 0}\nengine: http\n" + body
    )


def static_engine(html: str = PAGE, url: str = "https://fixture.test/p") -> HttpEngine:
    """An HTTP engine with content pushed in, so no socket is involved."""
    engine = HttpEngine()
    engine.set_content(html, url=url)
    engine.last_status = 200
    return engine


def ctx_for(body: str, engine=None, **kw) -> tuple[ScrapeScript, StepContext]:
    parsed = script(body)
    return parsed, StepContext(
        script=parsed, engine=engine or static_engine(), log=null_logger(), **kw
    )


class FakeBrowser:
    """Records interaction calls. Answers queries by delegating to lxml."""

    name = "fake-browser"
    interactive = True

    def __init__(self, html: str = PAGE) -> None:
        self._inner = static_engine(html)
        self.current_url = self._inner.current_url
        self.last_status = 200
        self.last_headers: dict[str, str] = {}
        self.calls: list[tuple] = []
        self.page = object()

    async def open(self, **kw) -> None:
        self.calls.append(("open", kw))

    async def goto(self, url, *, timeout_ms=30_000, wait_until="domcontentloaded") -> int:
        self.calls.append(("goto", url))
        self.current_url = url
        return 200

    async def content(self) -> str:
        return await self._inner.content()

    async def query(self, selector, *, limit=None):
        return await self._inner.query(selector, limit=limit)

    async def count(self, selector) -> int:
        return await self._inner.count(selector)

    async def wait_for(self, selector, *, state="visible", timeout_ms=15_000, url_contains=None):
        return await self._inner.wait_for(selector, state=state, timeout_ms=timeout_ms,
                                          url_contains=url_contains)

    async def click(self, selector, *, timeout_ms=10_000) -> None:
        self.calls.append(("click", selector.normalised))

    async def fill(self, selector, value, *, timeout_ms=10_000) -> None:
        self.calls.append(("fill", selector.normalised, value))

    async def select_option(self, selector, value, *, timeout_ms=10_000) -> None:
        self.calls.append(("select", selector.normalised, value))

    async def hover(self, selector, *, timeout_ms=10_000) -> None:
        self.calls.append(("hover", selector.normalised))

    async def press(self, key, *, selector=None, timeout_ms=10_000) -> None:
        self.calls.append(("press", key))

    async def scroll(self, *, to="bottom", selector=None, times=1, pause_ms=700) -> None:
        self.calls.append(("scroll", to, times))

    async def screenshot(self, path, *, full_page=False):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"\x89PNG\r\n")
        self.calls.append(("screenshot", str(path), full_page))
        return Path(path)

    async def close(self) -> None:
        self.calls.append(("close",))


# --------------------------------------------------------------- interpolation
def test_interpolate_substitutes_known_variables():
    _, ctx = ctx_for("steps: [{op: emit, from: x}]")
    ctx.vars.update({"page": 3, "q": "chairs"})
    assert interpolate("/p?page={{page}}&q={{ q }}", ctx) == "/p?page=3&q=chairs"


def test_interpolate_supports_dotted_lookup():
    _, ctx = ctx_for("steps: [{op: emit, from: x}]")
    ctx.vars["card"] = {"url": "/p/1"}
    assert interpolate("{{card.url}}", ctx) == "/p/1"


def test_unknown_variable_raises_rather_than_rendering_empty():
    # A URL silently built as "?page=" is exactly the silent-wrong-data failure
    # this system exists to catch, so it must be loud.
    _, ctx = ctx_for("steps: [{op: emit, from: x}]")
    with pytest.raises(MissingVariable) as exc:
        interpolate("/p?page={{missing}}", ctx)
    assert "missing" in str(exc.value)


def test_interpolate_leaves_plain_strings_untouched():
    _, ctx = ctx_for("steps: [{op: emit, from: x}]")
    assert interpolate("https://x.test/a", ctx) == "https://x.test/a"


# -------------------------------------------------------------------- secrets
FILL = """
steps:
  - {op: fill, selector: "css=#user", value: "", secret: shop_user}
  - {op: fill, selector: "css=#pw", value: "", secret: shop_pw}
  - {op: extract_list, selector: "css=.row", as: r, fields: {a: {selector: "css=.a"}}}
  - {op: emit, from: r}
"""


async def test_fill_reads_the_value_from_the_secret_store():
    engine = FakeBrowser()
    parsed, ctx = ctx_for(FILL, engine=engine,
                          secrets={"shop_user": "me@x.test", "shop_pw": "hunter2"})
    await run_steps(parsed, ctx)
    fills = [c for c in engine.calls if c[0] == "fill"]
    assert [c[2] for c in fills] == ["me@x.test", "hunter2"]


async def test_a_missing_secret_is_a_clear_step_error():
    engine = FakeBrowser()
    parsed, ctx = ctx_for(FILL, engine=engine, secrets={"shop_user": "me@x.test"})
    with pytest.raises(StepError) as exc:
        await run_steps(parsed, ctx)
    assert "shop_pw" in str(exc.value)


async def test_secret_values_never_reach_the_log():
    engine = FakeBrowser()
    parsed, ctx = ctx_for(FILL, engine=engine,
                          secrets={"shop_user": "me@x.test", "shop_pw": "hunter2"})
    await run_steps(parsed, ctx)
    dumped = repr(ctx.log.lines)
    assert "hunter2" not in dumped and "me@x.test" not in dumped
    assert "<secret:shop_pw>" in dumped


async def test_fill_without_a_secret_interpolates_its_value():
    engine = FakeBrowser()
    body = """
steps:
  - {op: fill, selector: "css=#user", value: "page-{{page}}"}
  - {op: extract_list, selector: "css=.row", as: r, fields: {a: {selector: "css=.a"}}}
  - {op: emit, from: r}
"""
    parsed, ctx = ctx_for(body, engine=engine)
    await run_steps(parsed, ctx)
    assert ("fill", "css=#user", "page-1") in engine.calls


# --------------------------------------------------- engine capability routing
INTERACTIVE = """
steps:
  - {op: fill, selector: "css=#user", value: "x"}
  - {op: extract_list, selector: "css=.row", as: r, fields: {a: {selector: "css=.a"}}}
  - {op: emit, from: r}
"""


async def test_browser_only_step_on_the_http_engine_asks_to_escalate():
    parsed, ctx = ctx_for(INTERACTIVE)
    with pytest.raises(InteractionUnsupported) as exc:
        await run_steps(parsed, ctx)
    assert exc.value.op == "fill"


async def test_scroll_is_a_no_op_on_the_http_engine_not_an_error():
    # Raising would fail every browser-written script the moment it ran on the
    # cheap rung, which is the opposite of what the ladder is for.
    body = """
steps:
  - {op: scroll, to: bottom, times: 3, pause_ms: 0}
  - {op: extract_list, selector: "css=.row", as: r, fields: {a: {selector: "css=.a"}}}
  - {op: emit, from: r}
"""
    parsed, ctx = ctx_for(body)
    assert len(await run_steps(parsed, ctx)) == 2


async def test_optional_click_is_skipped_when_the_engine_cannot_click():
    body = """
steps:
  - {op: click, selector: "css=.cookie-banner button", optional: true}
  - {op: extract_list, selector: "css=.row", as: r, fields: {a: {selector: "css=.a"}}}
  - {op: emit, from: r}
"""
    parsed, ctx = ctx_for(body)
    assert len(await run_steps(parsed, ctx)) == 2


# ----------------------------------------------------------------- rate limit
async def test_throttle_waits_inside_the_configured_band(monkeypatch):
    parsed = ScrapeScript.from_yaml(
        "rate_limit: {min_delay_s: 0.2, max_delay_s: 0.4}\n"
        "steps: [{op: emit, from: x}]\n"
    )
    ctx = StepContext(script=parsed, engine=static_engine(), log=null_logger())
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    delay = await ctx.throttle()
    assert 0.2 <= delay <= 0.4
    assert slept == [delay]


async def test_throttle_is_random_not_fixed(monkeypatch):
    parsed = ScrapeScript.from_yaml(
        "rate_limit: {min_delay_s: 1, max_delay_s: 5}\nsteps: [{op: emit, from: x}]\n"
    )
    ctx = StepContext(script=parsed, engine=static_engine(), log=null_logger())

    async def no_wait(seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_wait)
    delays = {await ctx.throttle() for _ in range(20)}
    assert len(delays) > 1


async def test_a_zero_width_band_does_not_sleep(monkeypatch):
    _, ctx = ctx_for("steps: [{op: emit, from: x}]")
    called = False

    async def fake_sleep(seconds):
        nonlocal called
        called = True

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    assert await ctx.throttle() == 0.0
    assert called is False


# --------------------------------------------------------------- custom_python
CUSTOM = """
steps:
  - op: custom_python
    as: extras
    code: |
      def run(page, ctx):
          return [{"from_python": True, "rows_so_far": len(ctx.rows)}]
  - {op: emit, from: extras}
"""


async def test_custom_python_result_lands_in_the_named_bag():
    parsed, ctx = ctx_for(CUSTOM)
    rows = await run_steps(parsed, ctx)
    assert rows == [{"from_python": True, "rows_so_far": 0}]
    assert ctx.custom_python_ran == 1


async def test_custom_python_logs_loudly_with_its_source():
    parsed, ctx = ctx_for(CUSTOM)
    await run_steps(parsed, ctx)
    warnings = [x for x in ctx.log.lines if x["tag"] == "custom_python" and x["level"] == "warn"]
    assert warnings, "unsandboxed code execution must be visible in the audit trail"
    assert "UNSANDBOXED" in warnings[0]["msg"]
    assert "from_python" in warnings[0]["code"]


async def test_custom_python_accepts_a_bare_body_without_a_def():
    body = """
steps:
  - op: custom_python
    as: extras
    code: "return [{'n': 1}]"
  - {op: emit, from: extras}
"""
    parsed, ctx = ctx_for(body)
    assert await run_steps(parsed, ctx) == [{"n": 1}]


async def test_custom_python_can_be_async():
    body = """
steps:
  - op: custom_python
    as: extras
    code: |
      async def run(page, ctx):
          return [{"async": True}]
  - {op: emit, from: extras}
"""
    parsed, ctx = ctx_for(body)
    assert await run_steps(parsed, ctx) == [{"async": True}]


async def test_custom_python_can_read_the_page_through_the_engine():
    body = """
steps:
  - op: custom_python
    as: extras
    code: |
      async def run(page, ctx):
          html = await page.content()
          return [{"has_rows": "class=\\"row\\"" in html}]
  - {op: emit, from: extras}
"""
    parsed, ctx = ctx_for(body)
    assert await run_steps(parsed, ctx) == [{"has_rows": True}]


async def test_custom_python_syntax_error_is_a_step_error():
    body = """
steps:
  - op: custom_python
    code: "def run(page, ctx:\\n  pass"
  - {op: emit, from: x}
"""
    parsed, ctx = ctx_for(body)
    with pytest.raises(StepError) as exc:
        await run_steps(parsed, ctx)
    assert "compile" in str(exc.value)


async def test_custom_python_raising_is_reported_with_its_type():
    body = """
steps:
  - op: custom_python
    code: |
      def run(page, ctx):
          raise KeyError("nope")
  - {op: emit, from: x}
"""
    parsed, ctx = ctx_for(body)
    with pytest.raises(StepError) as exc:
        await run_steps(parsed, ctx)
    assert "KeyError" in str(exc.value)


async def test_custom_python_async_timeout_is_enforced():
    body = """
steps:
  - op: custom_python
    timeout_s: 1
    code: |
      import asyncio
      async def run(page, ctx):
          await asyncio.sleep(30)
  - {op: emit, from: x}
"""
    parsed, ctx = ctx_for(body)
    parsed.steps[0].timeout_s = 0.05
    with pytest.raises(StepError) as exc:
        await run_steps(parsed, ctx)
    assert "timed out" in str(exc.value)


def test_script_reports_that_it_contains_custom_python():
    assert script(CUSTOM).has_custom_python is True


# ------------------------------------------------------------------ emit rules
async def test_emit_of_an_unknown_name_names_what_is_available():
    parsed, ctx = ctx_for("steps: [{op: emit, from: ghost}]")
    with pytest.raises(StepError) as exc:
        await run_steps(parsed, ctx)
    assert "ghost" in str(exc.value)


async def test_required_field_missing_drops_the_row_rather_than_the_run():
    body = """
steps:
  - op: extract_list
    selector: "css=.row"
    as: r
    fields:
      a:       {selector: "css=.a"}
      missing: {selector: "css=.nope", required: true}
  - {op: emit, from: r}
"""
    parsed, ctx = ctx_for(body)
    assert await run_steps(parsed, ctx) == []
    dropped = [x for x in ctx.log.lines if x["tag"] == "extract_list"][0]
    assert dropped["dropped"] == 2


async def test_a_default_fills_in_for_a_missing_field():
    body = """
steps:
  - op: extract_list
    selector: "css=.row"
    as: r
    fields:
      a:    {selector: "css=.a"}
      note: {selector: "css=.nope", default: "n/a"}
  - {op: emit, from: r}
"""
    parsed, ctx = ctx_for(body)
    rows = await run_steps(parsed, ctx)
    assert [r["note"] for r in rows] == ["n/a", "n/a"]


async def test_min_items_not_met_is_a_validation_failure():
    body = """
steps:
  - {op: extract_list, selector: "css=.row", as: r, min_items: 5,
     fields: {a: {selector: "css=.a"}}}
  - {op: emit, from: r}
"""
    parsed, ctx = ctx_for(body)
    with pytest.raises(ValidationFailed):
        await run_steps(parsed, ctx)


# ------------------------------------------------------------------ screenshot
async def test_screenshot_step_writes_into_the_run_directory(tmp_path):
    body = """
steps:
  - {op: screenshot, name: after-load, full_page: true}
  - {op: extract_list, selector: "css=.row", as: r, fields: {a: {selector: "css=.a"}}}
  - {op: emit, from: r}
"""
    artifacts = RunArtifacts(99, tmp_path / "99")
    parsed, ctx = ctx_for(body, engine=FakeBrowser(), artifacts=artifacts)
    await run_steps(parsed, ctx)
    assert (tmp_path / "99" / "after-load.png").exists()


# ----------------------------------------------------------------- step record
async def test_every_step_is_timed_and_recorded():
    body = """
steps:
  - {op: extract_list, selector: "css=.row", as: r, fields: {a: {selector: "css=.a"}}}
  - {op: emit, from: r}
"""
    parsed, ctx = ctx_for(body)
    await run_steps(parsed, ctx)
    assert [s["op"] for s in ctx.step_log] == ["extract_list", "emit"]
    assert all(s["ok"] and s["ms"] >= 0 for s in ctx.step_log)
