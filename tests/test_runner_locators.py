"""Selector parsing, XPath compilation, and the fallback ladder."""

from __future__ import annotations

import lxml.html
import pytest

from smartscraper.runner.locators import (
    Selector,
    SelectorSyntaxError,
    candidates_of,
    parse_selector,
    resolve_first,
    to_xpath,
)

SAMPLE = """
<html><body>
  <nav><a href="/next" rel="next">Next page</a></nav>
  <div class="grid">
    <article class="card"><h3>Alpha</h3><span class="price">10</span></article>
    <article class="card"><h3>Beta</h3><span class="price">20</span></article>
  </div>
  <button type="submit">Save changes</button>
  <input type="text" aria-label="Search products">
  <p>A paragraph mentioning Beta in passing.</p>
</body></html>
"""


@pytest.fixture
def doc():
    return lxml.html.fromstring(SAMPLE)


def find(doc, raw: str):
    return doc.xpath(to_xpath(parse_selector(raw)))


# --------------------------------------------------------------------- parsing
@pytest.mark.parametrize(
    ("raw", "kind", "value"),
    [
        ("css=.product-card", "css", ".product-card"),
        ("css = .a > .b", "css", ".a > .b"),
        ("text=Next page", "text", "Next page"),
        ("role=button", "role", "button"),
        ("xpath=//div[@id='x']", "xpath", "//div[@id='x']"),
        (".bare-css", "css", ".bare-css"),
        ("//div", "xpath", "//div"),
        ("./span", "xpath", "./span"),
        ("(//a)[1]", "xpath", "(//a)[1]"),
    ],
)
def test_parse_selector_kinds(raw, kind, value):
    sel = parse_selector(raw)
    assert (sel.kind, sel.value) == (kind, value)
    assert sel.raw == raw


def test_bare_string_is_css_not_guessed_from_content():
    # Documented behaviour: anything without a prefix or an xpath-ish start is CSS.
    assert parse_selector("div.price").kind == "css"


def test_text_selector_quoting_sets_exact():
    loose = parse_selector("text=Next")
    exact = parse_selector('text="Next"')
    assert (loose.exact, exact.exact) == (False, True)
    assert exact.value == "Next"


def test_role_selector_parses_name_and_exact():
    sel = parse_selector('role=button[name="Save changes"]')
    assert sel.kind == "role"
    assert sel.name == "Save changes"
    assert sel.value.startswith("button")


def test_role_selector_single_quotes():
    assert parse_selector("role=link[name='Next page']").name == "Next page"


@pytest.mark.parametrize("raw", ["", "   ", "css=", "text=  "])
def test_empty_selectors_raise(raw):
    with pytest.raises(SelectorSyntaxError):
        parse_selector(raw)


def test_normalised_round_trips_for_playwright():
    assert parse_selector("css=.a").normalised == "css=.a"
    assert parse_selector(".a").normalised == "css=.a"


# --------------------------------------------------------------------- xpath
def test_css_compiles_and_matches(doc):
    assert len(find(doc, "css=.card")) == 2
    assert len(find(doc, "css=.grid .card h3")) == 2


def test_bad_css_raises_selector_syntax_error():
    with pytest.raises(SelectorSyntaxError):
        to_xpath(parse_selector("css=.a >>>> .b"))


def test_xpath_passes_through(doc):
    assert len(find(doc, "xpath=//article[@class='card']")) == 2


def test_text_selector_matches_innermost_element(doc):
    found = find(doc, "text=Alpha")
    assert [e.tag for e in found] == ["h3"]


def test_text_selector_does_not_match_ancestors(doc):
    # "Beta" appears in an h3 and in a <p>; both are innermost, body is not.
    tags = sorted(e.tag for e in find(doc, "text=Beta"))
    assert tags == ["h3", "p"]


def test_exact_text_selector_excludes_substring_matches(doc):
    assert len(find(doc, 'text="Beta"')) == 1


def test_text_with_apostrophe_is_quoted_safely():
    doc = lxml.html.fromstring("<p>it's here</p>")
    assert len(find(doc, "text=it's here")) == 1


def test_role_matches_implicit_role(doc):
    assert [e.tag for e in find(doc, "role=button")] == ["button"]
    assert [e.tag for e in find(doc, "role=link")] == ["a"]


def test_role_with_name_matches_aria_label(doc):
    found = find(doc, 'role=textbox[name="Search products"]')
    assert [e.tag for e in found] == ["input"]


def test_role_with_name_matches_text_content(doc):
    assert [e.tag for e in find(doc, 'role=button[name="Save"]')] == ["button"]


def test_role_matches_explicit_role_attribute():
    doc = lxml.html.fromstring("<div role='listitem'>x</div>")
    assert len(find(doc, "role=listitem")) == 1


# --------------------------------------------------------------------- ladder
def test_candidates_of_dedupes_and_keeps_order():
    assert candidates_of("a", ["b", "a", "", None, "c"]) == ["a", "b", "c"]


async def test_resolve_first_returns_primary_when_it_matches():
    counts = {"css=.a": 3, "css=.b": 7}

    async def count(sel: Selector) -> int:
        return counts.get(sel.normalised, 0)

    res = await resolve_first("css=.a", ["css=.b"], count)
    assert res is not None
    assert (res.used, res.index, res.count, res.is_fallback) == ("css=.a", 0, 3, False)


async def test_resolve_first_falls_through_to_a_working_selector():
    async def count(sel: Selector) -> int:
        return 5 if sel.normalised == "css=.new" else 0

    res = await resolve_first("css=.old", ["css=.older", "css=.new"], count)
    assert res is not None
    assert (res.used, res.index, res.is_fallback) == ("css=.new", 2, True)
    assert res.tried == ["css=.old", "css=.older", "css=.new"]


async def test_resolve_first_returns_none_when_nothing_matches():
    async def count(sel: Selector) -> int:
        return 0

    assert await resolve_first("css=.a", ["css=.b"], count) is None


async def test_a_malformed_fallback_is_skipped_not_fatal():
    async def count(sel: Selector) -> int:
        return 2 if sel.normalised == "css=.good" else 0

    res = await resolve_first("css=.a >>>> .b", ["css=.good"], count)
    assert res is not None and res.used == "css=.good"


async def test_a_raising_candidate_is_skipped():
    async def count(sel: Selector) -> int:
        if sel.value == ".boom":
            raise RuntimeError("engine hiccup")
        return 1

    res = await resolve_first("css=.boom", ["css=.fine"], count)
    assert res is not None and res.used == "css=.fine"


async def test_minimum_lets_a_thin_match_be_rejected():
    async def count(sel: Selector) -> int:
        return {"css=.thin": 2, "css=.thick": 20}.get(sel.normalised, 0)

    res = await resolve_first("css=.thin", ["css=.thick"], count, minimum=10)
    assert res is not None and res.used == "css=.thick"
