"""Block classification: a pure function, so tested as one.

The bar these tests set is deliberately two-sided. Catching a challenge page
matters, but calling a healthy page a block is worse: it burns the escalation
ladder, spends proxy bandwidth, and eventually marks a working scraper broken.
Half of what follows is false-positive defence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from smartscraper.contracts import BlockReason
from smartscraper.runner.block_detect import (
    EMPTY_TEXT_THRESHOLD,
    SUGGESTED_RUNG,
    detect_block,
    visible_text,
)

FIXTURES = Path(__file__).parent / "fixtures"

GOOD_PAGE = """<!doctype html><html><head><title>Products</title></head><body>
<h1>Products</h1>
<p>Here is a real page with real prose on it, served by a real origin, which is
long enough that nothing could mistake it for an empty shell of a document.</p>
<div class="grid"><article class="card">Aeron Chair</article></div>
<script src="/static/app.js"></script>
</body></html>"""


def cf_challenge() -> str:
    return (FIXTURES / "cloudflare_challenge.html").read_text(encoding="utf-8")


# --------------------------------------------------------------- not a block
def test_healthy_page_is_not_blocked():
    assert not detect_block(200, {"content-type": "text/html"}, GOOD_PAGE).blocked


def test_page_behind_a_cdn_is_not_a_block():
    # `server: cloudflare` is on a large share of the web. On its own it means
    # nothing, and treating it as a signal would break every Cloudflare-fronted
    # site the moment it is scraped.
    verdict = detect_block(200, {"server": "cloudflare", "cf-ray": "abc-AMS"}, GOOD_PAGE)
    assert not verdict.blocked


def test_page_merely_mentioning_a_vendor_in_prose_is_not_blocked():
    html = GOOD_PAGE.replace("Aeron Chair", "Our infrastructure runs on Cloudflare and Akamai.")
    assert not detect_block(200, {}, html).blocked


def test_a_real_500_is_an_error_not_a_block():
    verdict = detect_block(500, {}, "<html><body>Internal Server Error</body></html>")
    assert not verdict.blocked


def test_redirect_status_is_not_a_block():
    assert not detect_block(302, {"location": "/login"}, GOOD_PAGE).blocked


def test_static_page_with_no_scripts_and_little_text_is_not_a_block():
    # Short but script-free: an honest tiny page, not a JS shell.
    assert not detect_block(200, {}, "<html><body><p>ok</p></body></html>").blocked


# ------------------------------------------------------------------- vendors
def test_cloudflare_fixture_is_detected():
    verdict = detect_block(403, {"server": "cloudflare"}, cf_challenge(), url="https://x.test/")
    assert verdict.blocked
    assert verdict.reason == BlockReason.CLOUDFLARE
    assert verdict.markers


def test_cloudflare_challenge_served_as_200_is_still_detected():
    # Managed challenges are commonly 200; status alone would miss this.
    verdict = detect_block(200, {}, cf_challenge())
    assert verdict.blocked and verdict.reason == BlockReason.CLOUDFLARE


def test_cloudflare_503_interstitial():
    html = "<html><body><div id='cf-browser-verification'></div><script>x</script></body></html>"
    verdict = detect_block(503, {}, html)
    assert verdict.blocked and verdict.reason == BlockReason.CLOUDFLARE


def test_cf_mitigated_header_alone_is_enough_with_a_refusal():
    verdict = detect_block(403, {"cf-mitigated": "challenge"}, "<html><body>no</body></html>")
    assert verdict.blocked and verdict.reason == BlockReason.CLOUDFLARE


def test_datadome_from_set_cookie():
    html = "<html><body><script>var x=1;</script></body></html>"
    verdict = detect_block(403, {"set-cookie": "datadome=abc; Path=/"}, html)
    assert verdict.blocked and verdict.reason == BlockReason.DATADOME


def test_datadome_captcha_host_in_body():
    html = """<html><body><script>
      var dd={'host':'geo.captcha-delivery.com','cid':'x'};</script></body></html>"""
    verdict = detect_block(403, {}, html)
    assert verdict.blocked and verdict.reason == BlockReason.DATADOME


def test_perimeterx_block_page():
    html = """<html><head><title>Access Denied</title></head><body>
      <div id="px-captcha"></div><script src="//captcha.px-cdn.net/x.js"></script>
      </body></html>"""
    verdict = detect_block(403, {}, html)
    assert verdict.blocked and verdict.reason == BlockReason.PERIMETERX


def test_akamai_reference_page():
    html = """<html><body><p>Reference&#32;&#35;18.1a2b</p>
      <script>bmak.ak_bmsc=1;</script></body></html>"""
    verdict = detect_block(403, {"set-cookie": "_abck=xyz~-1~"}, html)
    assert verdict.blocked and verdict.reason == BlockReason.AKAMAI


# ------------------------------------------------------------------ captchas
def test_turnstile_iframe_on_an_empty_page():
    html = """<html><body><iframe
      src="https://challenges.cloudflare.com/turnstile/v0/api.js"></iframe>
      <script>go()</script></body></html>"""
    verdict = detect_block(200, {}, html)
    assert verdict.blocked
    assert verdict.reason in (BlockReason.CLOUDFLARE, BlockReason.CAPTCHA)


def test_recaptcha_on_a_403():
    html = """<html><body><div class="g-recaptcha" data-sitekey="k"></div>
      <script src="https://www.google.com/recaptcha/api.js"></script></body></html>"""
    verdict = detect_block(403, {}, html)
    assert verdict.blocked and verdict.reason == BlockReason.CAPTCHA


def test_recaptcha_on_a_normal_page_is_not_a_block():
    # A contact form with a captcha is not a refusal of the page we asked for.
    html = GOOD_PAGE.replace(
        "</body>", '<div class="g-recaptcha"></div></body>'
    )
    assert not detect_block(200, {}, html).blocked


# -------------------------------------------------------------------- status
@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (401, BlockReason.FORBIDDEN),
        (403, BlockReason.FORBIDDEN),
        (407, BlockReason.FORBIDDEN),
        (429, BlockReason.RATE_LIMIT),
        (451, BlockReason.FORBIDDEN),
    ],
)
def test_refusal_statuses_without_attribution(status, reason):
    verdict = detect_block(status, {}, GOOD_PAGE)
    assert verdict.blocked and verdict.reason == reason


def test_429_is_rate_limit_not_forbidden():
    assert detect_block(429, {}, "").reason == BlockReason.RATE_LIMIT


# ----------------------------------------------------------- js-only shells
def test_empty_body_with_scripts_is_js_only():
    html = '<html><head><script src="/app.js"></script></head><body><div id="root"></div></body></html>'
    verdict = detect_block(200, {}, html)
    assert verdict.blocked and verdict.reason == BlockReason.EMPTY_JS_ONLY


def test_completely_empty_body_is_blocked():
    assert detect_block(200, {}, "").reason == BlockReason.EMPTY_JS_ONLY


def test_script_heavy_page_with_enough_text_is_fine():
    prose = "word " * 60
    html = f'<html><body><script>var a=1;</script><p>{prose}</p></body></html>'
    assert len(visible_text(html)) > EMPTY_TEXT_THRESHOLD
    assert not detect_block(200, {}, html).blocked


def test_script_contents_do_not_count_as_visible_text():
    filler = "x" * 4000
    html = f"<html><body><script>var s='{filler}';</script><div id=root></div></body></html>"
    assert detect_block(200, {}, html).reason == BlockReason.EMPTY_JS_ONLY


# -------------------------------------------------------------------- shape
def test_verdict_is_falsy_when_not_blocked_and_truthy_when_blocked():
    assert not detect_block(200, {}, GOOD_PAGE)
    assert detect_block(403, {}, GOOD_PAGE)


def test_verdict_serialises_for_the_log():
    payload = detect_block(403, {"server": "cloudflare"}, cf_challenge()).as_dict()
    assert set(payload) == {"blocked", "reason", "status", "markers", "detail"}
    assert payload["status"] == 403


def test_none_status_and_headers_are_tolerated():
    assert detect_block(None, None, None).blocked  # empty body counts as js-only


def test_every_reason_has_a_suggested_rung():
    reasons = {v for k, v in vars(BlockReason).items() if not k.startswith("_")}
    assert reasons <= set(SUGGESTED_RUNG)
