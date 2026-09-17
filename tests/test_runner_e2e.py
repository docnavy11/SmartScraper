"""Real scripts, a real socket, a real HTTP engine.

Everything here goes through `ScrapeScript.from_yaml` and the same `execute`
entry point the scheduler's subprocess uses, against `tests/fixtures/` served
over a random port. No browser binary is needed, which is the point: the HTTP
rung exercises `steps.py`, `locators.py`, `parsing.py`, `block_detect.py`,
`artifacts.py` and `escalation.py` together, and the browser rungs differ only
in the engine object underneath.
"""

from __future__ import annotations

import json

import pytest

from smartscraper.contracts import BlockReason
from smartscraper.dsl.models import ScrapeScript
from smartscraper.runner import execute
from smartscraper.runner.artifacts import RunArtifacts

from .fixtures.serve import fixture_server

#: The fixture server is local, so politeness delays would only slow the suite.
NO_DELAY = """
rate_limit: {min_delay_s: 0, max_delay_s: 0, max_pages_per_run: 20}
escalation: [http]
engine: http
"""


@pytest.fixture(scope="module")
def base_url():
    with fixture_server() as url:
        yield url


def script(body: str) -> ScrapeScript:
    return ScrapeScript.from_yaml(NO_DELAY + body)


async def run(body: str, tmp_path, *, run_id: int = 1, **kw):
    return await execute(script(body), run_id=run_id, out_dir=tmp_path / str(run_id), **kw)


# --------------------------------------------------------------- product grid
GRID = """
steps:
  - {op: goto, url: "HOST/products_page1.html"}
  - {op: wait_for, selector: "css=.product-card"}
  - op: extract_list
    selector: "css=.product-card"
    as: products
    min_items: 1
    fields:
      name:   {selector: "css=.product-name", attr: text, required: true}
      price:  {selector: "css=.price", attr: text, parse: money}
      url:    {selector: "css=.product-link", attr: href, absolute: true}
      stock:  {selector: "css=.stock", attr: text, parse: bool}
      rating: {selector: "css=.rating", attr: "data-rating", parse: float}
  - {op: emit, from: products}
validation: {min_rows: 4}
"""


async def test_product_grid_extracts_every_card(base_url, tmp_path):
    outcome = await run(GRID.replace("HOST", base_url), tmp_path)
    assert outcome.error is None and not outcome.blocked
    assert outcome.engine_used == "http"
    assert len(outcome.rows) == 4


async def test_product_grid_parses_field_types(base_url, tmp_path):
    rows = (await run(GRID.replace("HOST", base_url), tmp_path)).rows
    first = rows[0]
    assert first["name"] == "Aeron Chair"
    assert first["price"] == pytest.approx(1299.0)      # "€1.299,00", EU grouping
    assert first["url"].startswith(f"{base_url}/")       # made absolute
    assert first["stock"] is True
    assert first["rating"] == pytest.approx(4.6)


async def test_out_of_stock_parses_false_not_none(base_url, tmp_path):
    rows = (await run(GRID.replace("HOST", base_url), tmp_path)).rows
    monitor = next(r for r in rows if r["name"] == "Monitor Arm")
    assert monitor["stock"] is False


async def test_grid_run_writes_every_artifact(base_url, tmp_path):
    outcome = await run(GRID.replace("HOST", base_url), tmp_path, run_id=7)
    art = RunArtifacts(7, tmp_path / "7")
    assert art.records_path.exists() and art.log_path.exists()
    assert art.html_path.exists() and art.outcome_path.exists()
    assert len(art.read_records()) == len(outcome.rows)
    assert json.loads(art.outcome_path.read_text())["row_count"] == 4


async def test_log_lines_are_all_json_with_the_stream_shape(base_url, tmp_path):
    await run(GRID.replace("HOST", base_url), tmp_path, run_id=8)
    lines = RunArtifacts(8, tmp_path / "8").read_log()
    assert lines
    assert all({"ts", "level", "tag", "msg"} <= set(line) for line in lines)
    assert {line["tag"] for line in lines} >= {"goto", "extract_list", "emit"}


# ----------------------------------------------------------------- pagination
PAGINATE_URL = """
steps:
  - {op: goto, url: "HOST/products_page{{page}}.html"}
  - op: extract_list
    selector: "css=.product-card"
    as: products
    fields:
      name:  {selector: "css=.product-name", attr: text}
      price: {selector: "css=.price", attr: text, parse: money}
  - {op: paginate, next: "css=a[rel=next]", max_pages: 10, var: page}
  - {op: emit, from: products}
"""

PAGINATE_CLICK = """
steps:
  - {op: goto, url: "HOST/products_page1.html"}
  - op: extract_list
    selector: "css=.product-card"
    as: products
    fields:
      name: {selector: "css=.product-name", attr: text}
  - {op: paginate, next: "css=a.next-page", max_pages: 10}
  - {op: emit, from: products}
"""


async def test_url_driven_pagination_walks_all_three_pages(base_url, tmp_path):
    outcome = await run(PAGINATE_URL.replace("HOST", base_url), tmp_path)
    assert len(outcome.rows) == 9          # 4 + 3 + 2
    names = [r["name"] for r in outcome.rows]
    assert names[0] == "Aeron Chair" and names[-1] == "Footrest"


async def test_next_link_pagination_walks_all_three_pages(base_url, tmp_path):
    # No `{{page}}` in the goto, so the runner clicks the next link instead.
    outcome = await run(PAGINATE_CLICK.replace("HOST", base_url), tmp_path)
    assert len(outcome.rows) == 9


async def test_pagination_stops_at_the_last_page_not_at_max_pages(base_url, tmp_path):
    # Page 3 has no next link; max_pages is 10, so stopping proves the control
    # is being checked rather than the loop simply running out.
    outcome = await run(PAGINATE_URL.replace("HOST", base_url), tmp_path)
    assert len(outcome.rows) == 9


async def test_max_pages_caps_the_walk(base_url, tmp_path):
    body = (PAGINATE_URL.replace("HOST", base_url)).replace("max_pages: 10", "max_pages: 2")
    assert len((await run(body, tmp_path)).rows) == 7     # pages 1 and 2 only


async def test_max_pages_per_run_caps_the_walk(base_url, tmp_path):
    # The per-run cap is the backstop that a runaway `max_pages` cannot exceed.
    parsed = script(PAGINATE_URL.replace("HOST", base_url))
    parsed.rate_limit.max_pages_per_run = 2
    outcome = await execute(parsed, run_id=30, out_dir=tmp_path / "30")
    assert len(outcome.rows) == 7     # pages 1 and 2 only


async def test_records_are_streamed_to_disk_per_emit(base_url, tmp_path):
    await run(PAGINATE_URL.replace("HOST", base_url), tmp_path, run_id=11)
    assert len(RunArtifacts(11, tmp_path / "11").read_records()) == 9


# ------------------------------------------------------------ missing selector
MISSING = """
steps:
  - {op: goto, url: "HOST/products_page1.html"}
  - op: extract_list
    selector: "css=.does-not-exist"
    as: products
    min_items: 1
    fields:
      name: {selector: "css=h3", attr: text}
  - {op: emit, from: products}
"""


async def test_missing_selector_fails_the_run_with_the_candidates_named(base_url, tmp_path):
    outcome = await run(MISSING.replace("HOST", base_url), tmp_path)
    assert outcome.rows == []
    assert outcome.error is not None
    assert ".does-not-exist" in outcome.error
    assert not outcome.blocked      # a broken script is not a block


async def test_missing_selector_without_min_items_yields_zero_rows_not_an_error(
    base_url, tmp_path
):
    body = (MISSING.replace("HOST", base_url)).replace("min_items: 1", "min_items: 0")
    outcome = await run(body, tmp_path)
    assert outcome.rows == [] and outcome.error is None


FALLBACK = """
steps:
  - {op: goto, url: "HOST/products_renamed.html"}
  - op: extract_list
    selector: "css=.product-card"
    fallback_selectors: ["css=.item-tile"]
    as: products
    min_items: 1
    fields:
      name:  {selector: "css=.product-name", fallback_selectors: ["css=.title"], attr: text}
      price: {selector: "css=.price", fallback_selectors: ["css=.amount"], attr: text, parse: money}
  - {op: emit, from: products}
"""


async def test_a_fallback_selector_rescues_a_redesigned_page(base_url, tmp_path):
    outcome = await run(FALLBACK.replace("HOST", base_url), tmp_path, run_id=12)
    assert len(outcome.rows) == 2
    assert outcome.rows[0] == {"name": "Aeron Chair", "price": pytest.approx(1299.0)}


async def test_using_a_fallback_is_logged_as_a_warning(base_url, tmp_path):
    await run(FALLBACK.replace("HOST", base_url), tmp_path, run_id=13)
    lines = RunArtifacts(13, tmp_path / "13").read_log()
    warnings = [x for x in lines if x["tag"] == "selector" and x["level"] == "warn"]
    assert warnings, "a dead primary selector must be visible to the repair agent"
    assert warnings[0]["primary"] == "css=.product-card"
    assert warnings[0]["used"] == "css=.item-tile"


# ---------------------------------------------------------------- blocked page
BLOCKED = """
steps:
  - {op: goto, url: "HOST/blocked/cloudflare_challenge.html"}
  - op: extract_list
    selector: "css=.product-card"
    as: products
    fields: {name: {selector: "css=h3", attr: text}}
  - {op: emit, from: products}
"""


async def test_a_cloudflare_challenge_marks_the_run_blocked(base_url, tmp_path):
    outcome = await run(BLOCKED.replace("HOST", base_url), tmp_path, run_id=20)
    assert outcome.blocked is True
    assert outcome.block_reason == BlockReason.CLOUDFLARE
    assert outcome.rows == []


async def test_a_blocked_run_keeps_the_challenge_page_for_the_repair_agent(
    base_url, tmp_path
):
    await run(BLOCKED.replace("HOST", base_url), tmp_path, run_id=21)
    saved = (tmp_path / "21" / "blocked.html").read_text(encoding="utf-8")
    assert "cf-browser-verification" in saved or "_cf_chl_opt" in saved


async def test_extraction_never_runs_on_a_blocked_page(base_url, tmp_path):
    await run(BLOCKED.replace("HOST", base_url), tmp_path, run_id=22)
    tags = [x["tag"] for x in RunArtifacts(22, tmp_path / "22").read_log()]
    assert "blocked" in tags
    assert "extract_list" not in tags


# -------------------------------------------------------------- other statuses
async def test_a_404_is_a_block_not_a_crash(base_url, tmp_path):
    body = """
steps:
  - {op: goto, url: "HOST/no_such_page.html"}
  - {op: extract_list, selector: "css=.x", as: p, fields: {a: {selector: "css=b"}}}
  - {op: emit, from: p}
""".replace("HOST", base_url)
    outcome = await run(body, tmp_path)
    # 404 is not in the refusal table, but the body is a tiny JS-free error page,
    # so the run simply finds nothing rather than inventing a block.
    assert outcome.rows == []


async def test_empty_grid_produces_zero_rows_and_no_error(base_url, tmp_path):
    body = """
steps:
  - {op: goto, url: "HOST/empty_grid.html"}
  - {op: extract_list, selector: "css=.product-card", as: p,
     fields: {name: {selector: "css=h3"}}}
  - {op: emit, from: p}
""".replace("HOST", base_url)
    outcome = await run(body, tmp_path)
    assert outcome.rows == [] and outcome.error is None and not outcome.blocked


# ----------------------------------------------------------------- extract one
async def test_extract_single_record_from_a_detail_page(base_url, tmp_path):
    body = """
steps:
  - {op: goto, url: "HOST/product_1.html"}
  - op: extract
    as: detail
    selector: "css=.detail"
    fields:
      sku:   {selector: "css=.sku", attr: text, regex: "SKU-(\\\\d+)"}
      price: {selector: "css=.price", attr: text, parse: money}
      blurb: {selector: "css=.description", attr: text}
  - {op: emit, from: detail}
""".replace("HOST", base_url)
    rows = (await run(body, tmp_path)).rows
    assert len(rows) == 1
    assert rows[0]["sku"] == "0001"
    assert rows[0]["price"] == pytest.approx(1299.0)


# ------------------------------------------------------------------- assert op
async def test_assert_min_count_failing_fails_the_run(base_url, tmp_path):
    body = """
steps:
  - {op: goto, url: "HOST/products_page1.html"}
  - {op: assert, selector: "css=.product-card", min_count: 99, message: "too few cards"}
  - {op: extract_list, selector: "css=.product-card", as: p,
     fields: {name: {selector: "css=h3"}}}
  - {op: emit, from: p}
""".replace("HOST", base_url)
    outcome = await run(body, tmp_path)
    assert outcome.error is not None and "too few cards" in outcome.error


async def test_assert_on_page_text_passes(base_url, tmp_path):
    body = """
steps:
  - {op: goto, url: "HOST/products_page1.html"}
  - {op: assert, text_contains: "Aeron Chair"}
  - {op: extract_list, selector: "css=.product-card", as: p,
     fields: {name: {selector: "css=.product-name"}}}
  - {op: emit, from: p}
""".replace("HOST", base_url)
    assert len((await run(body, tmp_path)).rows) == 4


# ----------------------------------------------------------------------- loop
async def test_loop_visits_each_extracted_url(base_url, tmp_path):
    body = """
steps:
  - {op: goto, url: "HOST/products_page1.html"}
  - op: extract_list
    selector: "css=.product-card"
    as: cards
    fields:
      url: {selector: "css=.product-link", attr: href, absolute: true}
  - op: loop
    over: cards
    as: card
    max_iterations: 2
    steps:
      - {op: goto, url: "{{card.url}}"}
      - op: extract
        as: details
        fields:
          sku:   {selector: "css=.sku", attr: text}
          title: {selector: "css=h1", attr: text}
  - {op: emit, from: details}
""".replace("HOST", base_url)
    rows = (await run(body, tmp_path)).rows
    assert len(rows) == 2
    assert [r["sku"] for r in rows] == ["SKU-0001", "SKU-0002"]
    assert rows[0]["title"] == "Aeron Chair"
