"""The browser rung, against the same fixture server.

These skip when no browser binary is present, which is the normal state of a
fresh checkout. They are worth having anyway: the HTTP rung proves `steps.py`,
but only a real browser proves that the selector strings this runner builds are
the strings Playwright's own selector engines accept, and that trace and HAR
capture are armed at the right moment.

Nothing here downloads a browser. If one is not already installed the whole
module is skipped.
"""

from __future__ import annotations

import pytest

from smartscraper.dsl.models import ScrapeScript
from smartscraper.runner import execute
from smartscraper.runner.engines.browser import BrowserEngine, probe_availability
from smartscraper.runner.locators import parse_selector

from .fixtures.serve import fixture_server

_usable, _reason = probe_availability("patchright")
pytestmark = pytest.mark.skipif(not _usable, reason=f"no browser available: {_reason}")

HEAD = """
rate_limit: {min_delay_s: 0, max_delay_s: 0}
engine: patchright
escalation: [patchright]
"""


@pytest.fixture(scope="module")
def base_url():
    with fixture_server() as url:
        yield url


@pytest.fixture
async def engine(tmp_path):
    e = BrowserEngine(engine="patchright", artifacts_dir=tmp_path, trace=True, har=True)
    await e.open(headed=False)
    try:
        yield e
    finally:
        await e.close()


def script(body: str, base_url: str) -> ScrapeScript:
    return ScrapeScript.from_yaml((HEAD + body).replace("HOST", base_url))


# ------------------------------------------------------------------- selectors
async def test_css_selector_reaches_playwright_unchanged(engine, base_url):
    await engine.goto(f"{base_url}/products_page1.html")
    assert await engine.count(parse_selector("css=.product-card")) == 4


async def test_text_selector_reaches_playwright_unchanged(engine, base_url):
    await engine.goto(f"{base_url}/products_page1.html")
    assert await engine.count(parse_selector("text=Aeron Chair")) >= 1


async def test_role_selector_reaches_playwright_unchanged(engine, base_url):
    # Playwright resolves the real accessible name here, unlike the HTTP rung's
    # approximation, so this is the test that the two agree on simple cases.
    await engine.goto(f"{base_url}/products_page1.html")
    assert await engine.count(parse_selector('role=link[name="Next"]')) == 1


async def test_xpath_selector_reaches_playwright_unchanged(engine, base_url):
    await engine.goto(f"{base_url}/products_page1.html")
    assert await engine.count(parse_selector("xpath=//article[@class='product-card']")) == 4


async def test_bare_css_is_normalised_for_playwright(engine, base_url):
    await engine.goto(f"{base_url}/products_page1.html")
    assert await engine.count(parse_selector(".product-card")) == 4


# ------------------------------------------------------------------ extraction
async def test_element_text_and_attributes(engine, base_url):
    await engine.goto(f"{base_url}/products_page1.html")
    cards = await engine.query(parse_selector("css=.product-card"))
    assert len(cards) == 4
    names = await cards[0].query(parse_selector("css=.product-name"))
    assert (await names[0].text()).strip() == "Aeron Chair"
    links = await cards[0].query(parse_selector("css=.product-link"))
    assert (await links[0].attr("href")).endswith("product_1.html")


async def test_data_attribute_reads_through(engine, base_url):
    await engine.goto(f"{base_url}/products_page1.html")
    cards = await engine.query(parse_selector("css=.product-card"), limit=1)
    assert await cards[0].attr("data-sku") == "product_1"


# ------------------------------------------------------------ full script runs
GRID = """
steps:
  - {op: goto, url: "HOST/products_page1.html"}
  - {op: wait_for, selector: "css=.product-card"}
  - op: extract_list
    selector: "css=.product-card"
    as: products
    min_items: 1
    fields:
      name:  {selector: "css=.product-name", attr: text}
      price: {selector: "css=.price", attr: text, parse: money}
      url:   {selector: "css=.product-link", attr: href, absolute: true}
  - {op: emit, from: products}
"""


async def test_a_whole_script_runs_on_the_browser_rung(base_url, tmp_path):
    outcome = await execute(script(GRID, base_url), run_id=1, out_dir=tmp_path / "1")
    assert outcome.error is None
    assert outcome.engine_used == "patchright"
    assert len(outcome.rows) == 4
    assert outcome.rows[0]["price"] == pytest.approx(1299.0)


async def test_the_browser_rung_writes_a_real_screenshot(base_url, tmp_path):
    await execute(script(GRID, base_url), run_id=2, out_dir=tmp_path / "2")
    shot = tmp_path / "2" / "screen.png"
    assert shot.exists() and shot.read_bytes()[:4] == b"\x89PNG"


async def test_click_pagination_works_in_a_real_browser(base_url, tmp_path):
    body = """
steps:
  - {op: goto, url: "HOST/products_page1.html"}
  - op: extract_list
    selector: "css=.product-card"
    as: products
    fields: {name: {selector: "css=.product-name", attr: text}}
  - {op: paginate, next: "css=a.next-page", max_pages: 10}
  - {op: emit, from: products}
"""
    outcome = await execute(script(body, base_url), run_id=3, out_dir=tmp_path / "3")
    assert len(outcome.rows) == 9


async def test_fill_and_submit_a_form(base_url, tmp_path):
    body = """
steps:
  - {op: goto, url: "HOST/login.html"}
  - {op: fill, selector: "css=#user", value: "", secret: shop_user}
  - {op: fill, selector: "css=#pw", value: "", secret: shop_pw}
  - {op: click, selector: 'role=button[name="Sign in"]'}
  - {op: wait_for, selector: "css=.product-card"}
  - op: extract_list
    selector: "css=.product-card"
    as: products
    fields: {name: {selector: "css=.product-name", attr: text}}
  - {op: emit, from: products}
"""
    outcome = await execute(
        script(body, base_url), run_id=4, out_dir=tmp_path / "4",
        secrets={"shop_user": "me@x.test", "shop_pw": "hunter2"},
    )
    assert outcome.error is None
    assert len(outcome.rows) == 4


async def test_secrets_do_not_appear_in_the_run_log(base_url, tmp_path):
    from smartscraper.runner.artifacts import RunArtifacts

    body = """
steps:
  - {op: goto, url: "HOST/login.html"}
  - {op: fill, selector: "css=#pw", value: "", secret: shop_pw}
  - {op: extract, as: d, fields: {title: {selector: "css=h1", attr: text}}}
  - {op: emit, from: d}
"""
    await execute(script(body, base_url), run_id=5, out_dir=tmp_path / "5",
                  secrets={"shop_pw": "hunter2"})
    assert "hunter2" not in RunArtifacts(5, tmp_path / "5").log_path.read_text()


# ------------------------------------------------------------- failure capture
async def test_a_failing_run_keeps_the_trace_and_har(base_url, tmp_path):
    body = """
steps:
  - {op: goto, url: "HOST/products_page1.html"}
  - {op: extract_list, selector: "css=.gone", as: p, min_items: 1,
     fields: {name: {selector: "css=h3"}}}
  - {op: emit, from: p}
"""
    outcome = await execute(script(body, base_url), run_id=6, out_dir=tmp_path / "6")
    assert outcome.error is not None
    assert (tmp_path / "6" / "trace.zip").exists()
    assert (tmp_path / "6" / "network.har").exists()


async def test_a_passing_run_discards_the_trace_and_har(base_url, tmp_path):
    # Traces are large. Keeping one per successful scheduled run would fill the
    # disk, which is why they are armed always and kept only on failure.
    outcome = await execute(script(GRID, base_url), run_id=7, out_dir=tmp_path / "7")
    assert outcome.error is None
    assert not (tmp_path / "7" / "trace.zip").exists()
    assert not (tmp_path / "7" / "network.har").exists()


# -------------------------------------------------------------------- snapshot
async def test_snapshot_carries_a_real_accessibility_tree(engine, base_url):
    await engine.goto(f"{base_url}/products_page1.html")
    snap = await engine.snapshot(budget_bytes=20_000)
    assert snap.title == "Products page 1"
    assert snap.url.endswith("products_page1.html")
    assert "Aeron Chair" in snap.accessibility_tree
    assert "<article" in snap.html


async def test_probe_works_on_the_browser_rung(engine, base_url):
    await engine.goto(f"{base_url}/products_page1.html")
    result = await engine.probe("css=.product-name", limit=2)
    assert result.matched == 4 and result.non_empty == 4
    assert result.samples == ["Aeron Chair", "Standing Desk"]


# --------------------------------------------------------------------- channel
async def test_the_engine_reports_which_channel_launched(engine):
    # Chrome is often absent on a server while Chromium is present; the run must
    # say which one it actually used rather than assume.
    assert engine.channel_used in ("chrome", "chromium")
    if engine.channel_used == "chromium" and engine.channel_fallback_reason:
        assert "not found" in engine.channel_fallback_reason
