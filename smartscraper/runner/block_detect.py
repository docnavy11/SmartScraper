"""Decide whether a response is the page we asked for or a wall.

Pure function over `(status, headers, html)` so the whole classifier is unit
testable without a network or a browser. It returns a `BlockVerdict`; the
escalation ladder turns that into a retry on a better rung.

Evidence label: the marker table below is a **design** artifact, assembled from
publicly visible challenge-page markup and cookie names. It has **not** been
measured against a corpus of real challenge pages, so treat per-vendor
attribution as a hypothesis and the boolean `blocked` as the load-bearing part.
False negatives are the expected failure mode, since every marker is narrow on
purpose: a normal page that merely sits behind a CDN must not be called a block.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from smartscraper.contracts import BlockReason

# Status codes that are a refusal on their own.
BLOCKING_STATUS: dict[int, str] = {
    401: BlockReason.FORBIDDEN,
    403: BlockReason.FORBIDDEN,
    407: BlockReason.FORBIDDEN,
    429: BlockReason.RATE_LIMIT,
    451: BlockReason.FORBIDDEN,
}
# Ambiguous: a challenge is served as 503 by Cloudflare, but 503 is also a real
# outage. Blocked only when a marker agrees.
AMBIGUOUS_STATUS: frozenset[int] = frozenset({202, 503, 520, 521, 522, 526})

# marker -> (reason, human label). Matched case-insensitively against the HTML.
HTML_MARKERS: dict[str, tuple[str, str]] = {
    "cf-browser-verification": (BlockReason.CLOUDFLARE, "cloudflare js challenge div"),
    "cf_chl_opt": (BlockReason.CLOUDFLARE, "cloudflare challenge options"),
    "/cdn-cgi/challenge-platform": (BlockReason.CLOUDFLARE, "cloudflare challenge platform"),
    "__cf_chl_": (BlockReason.CLOUDFLARE, "cloudflare challenge token"),
    "window._cf_chl": (BlockReason.CLOUDFLARE, "cloudflare challenge bootstrap"),
    "checking your browser before accessing": (BlockReason.CLOUDFLARE, "cloudflare interstitial copy"),
    "attention required! | cloudflare": (BlockReason.CLOUDFLARE, "cloudflare block page title"),
    "enable javascript and cookies to continue": (BlockReason.CLOUDFLARE, "cloudflare managed challenge"),
    "geo.captcha-delivery.com": (BlockReason.DATADOME, "datadome captcha host"),
    "datadome": (BlockReason.DATADOME, "datadome script or cookie"),
    "px-captcha": (BlockReason.PERIMETERX, "perimeterx captcha container"),
    "perimeterx": (BlockReason.PERIMETERX, "perimeterx script"),
    "_pxhd": (BlockReason.PERIMETERX, "perimeterx cookie"),
    "captcha.px-cdn.net": (BlockReason.PERIMETERX, "perimeterx cdn"),
    "please enable js and disable any ad blocker": (BlockReason.PERIMETERX, "perimeterx block copy"),
    "errors.edgesuite.net": (BlockReason.AKAMAI, "akamai error page"),
    "akamai bot manager": (BlockReason.AKAMAI, "akamai bot manager"),
    "_abck": (BlockReason.AKAMAI, "akamai _abck cookie"),
    "ak_bmsc": (BlockReason.AKAMAI, "akamai bmsc cookie"),
}
# Cookie / header names, checked against Set-Cookie and named headers only.
HEADER_MARKERS: dict[str, tuple[str, str]] = {
    "cf-mitigated": (BlockReason.CLOUDFLARE, "cf-mitigated header"),
    "x-datadome": (BlockReason.DATADOME, "x-datadome header"),
    "x-datadome-cid": (BlockReason.DATADOME, "datadome client id header"),
    "x-px-block": (BlockReason.PERIMETERX, "perimeterx block header"),
    "x-akamai-bot": (BlockReason.AKAMAI, "akamai bot header"),
}
COOKIE_MARKERS: dict[str, tuple[str, str]] = {
    "datadome=": (BlockReason.DATADOME, "datadome cookie"),
    "_px": (BlockReason.PERIMETERX, "perimeterx cookie"),
    "_abck=": (BlockReason.AKAMAI, "akamai _abck cookie"),
    "bm_sz=": (BlockReason.AKAMAI, "akamai bm_sz cookie"),
}

# A captcha widget present anywhere in the document.
CAPTCHA_MARKERS: dict[str, str] = {
    "challenges.cloudflare.com/turnstile": "turnstile iframe",
    "cf-turnstile": "turnstile widget",
    "google.com/recaptcha/api": "recaptcha script",
    "recaptcha/api2/anchor": "recaptcha iframe",
    "g-recaptcha": "recaptcha widget",
    "hcaptcha.com/captcha": "hcaptcha iframe",
    "h-captcha": "hcaptcha widget",
    "captcha-delivery.com/captcha": "datadome captcha iframe",
}

_SCRIPT = re.compile(r"<script\b", re.IGNORECASE)
_NOSCRIPT = re.compile(r"<noscript\b", re.IGNORECASE)
_TAG = re.compile(r"(?is)<(script|style|noscript|template|svg)\b.*?</\1\s*>|<[^>]+>")
_WS = re.compile(r"[\s ]+")

#: A body with less visible text than this, but scripts in it, is a JS-only shell.
EMPTY_TEXT_THRESHOLD = 180


@dataclass(slots=True)
class BlockVerdict:
    blocked: bool
    reason: str | None = None
    status: int | None = None
    markers: list[str] = field(default_factory=list)
    detail: str = ""

    def __bool__(self) -> bool:  # pragma: no cover - trivial
        return self.blocked

    def as_dict(self) -> dict[str, object]:
        return {
            "blocked": self.blocked,
            "reason": self.reason,
            "status": self.status,
            "markers": list(self.markers),
            "detail": self.detail,
        }


def visible_text(html: str) -> str:
    """Rough text content: tags stripped, scripts and styles removed whole."""
    if not html:
        return ""
    return _WS.sub(" ", _TAG.sub(" ", html)).strip()


def detect_block(
    status: int | None,
    headers: dict[str, str] | None,
    html: str | None,
    *,
    url: str = "",
) -> BlockVerdict:
    """Classify one response. Vendor markers outrank status codes, because
    knowing *who* refused is what picks the next escalation rung."""
    html = html or ""
    lowered = html.lower()
    norm_headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    markers: list[str] = []
    vendor: str | None = None

    for name, (reason, label) in HEADER_MARKERS.items():
        if name in norm_headers:
            markers.append(label)
            vendor = vendor or reason

    cookies = norm_headers.get("set-cookie", "").lower()
    for name, (reason, label) in COOKIE_MARKERS.items():
        if name in cookies:
            markers.append(label)
            vendor = vendor or reason

    for marker, (reason, label) in HTML_MARKERS.items():
        if marker in lowered:
            markers.append(label)
            vendor = vendor or reason

    captcha_hits = [label for m, label in CAPTCHA_MARKERS.items() if m in lowered]

    blocking_status = BLOCKING_STATUS.get(status or 0)
    ambiguous = (status or 0) in AMBIGUOUS_STATUS
    server_error = (status or 0) >= 500 and not ambiguous

    # 1. A vendor marker plus any sign of refusal, or a challenge marker at all.
    if vendor and (blocking_status or ambiguous or captcha_hits or _is_challenge_shell(html, lowered)):
        return BlockVerdict(
            True, vendor, status, markers + captcha_hits,
            _detail(vendor, status, url),
        )
    # 2. A captcha widget on a page that is otherwise empty or refused. The
    #    stricter emptiness test is used here on purpose: a contact form with a
    #    reCAPTCHA on it is a form, not a wall, and the looser challenge-shell
    #    test flags short-but-genuine pages.
    if captcha_hits and (blocking_status or ambiguous or _is_empty_js_only(html, lowered)):
        return BlockVerdict(
            True, vendor or BlockReason.CAPTCHA, status, markers + captcha_hits,
            "captcha widget on a page with no content",
        )
    # 3. Refusal status with no attribution.
    if blocking_status:
        return BlockVerdict(
            True, vendor or blocking_status, status, markers + captcha_hits,
            f"HTTP {status}",
        )
    # 4. 200 with a script-only shell: the content never rendered for us.
    if not server_error and _is_empty_js_only(html, lowered):
        return BlockVerdict(
            True, BlockReason.EMPTY_JS_ONLY, status, markers + captcha_hits,
            f"{len(visible_text(html))} chars of visible text with scripts present",
        )
    # 5. A vendor marker alone on a page with real content is just a CDN.
    if ambiguous and (markers or captcha_hits):
        return BlockVerdict(
            True, vendor or BlockReason.UNKNOWN, status, markers + captcha_hits,
            f"HTTP {status} with challenge markers",
        )
    return BlockVerdict(False, None, status, markers + captcha_hits, "")


def _is_challenge_shell(html: str, lowered: str) -> bool:
    """A challenge page carries scripts and almost no prose."""
    return bool(_SCRIPT.search(lowered) or _NOSCRIPT.search(lowered)) and (
        len(visible_text(html)) < 600
    )


def _is_empty_js_only(html: str, lowered: str) -> bool:
    if not html.strip():
        return True
    if not _SCRIPT.search(lowered):
        return False
    return len(visible_text(html)) < EMPTY_TEXT_THRESHOLD


def _detail(vendor: str, status: int | None, url: str) -> str:
    where = f" at {url}" if url else ""
    return f"{vendor} challenge{where}" + (f" (HTTP {status})" if status else "")


#: Which escalation rung tends to be worth trying for a given reason. **Design**,
#: unmeasured: no benchmark in this repo supports any of these pairings yet.
SUGGESTED_RUNG: dict[str, str] = {
    BlockReason.CLOUDFLARE: "byparr",
    BlockReason.DATADOME: "camoufox",
    BlockReason.PERIMETERX: "camoufox",
    BlockReason.AKAMAI: "patchright+proxy",
    BlockReason.CAPTCHA: "byparr",
    BlockReason.RATE_LIMIT: "patchright+proxy",
    BlockReason.FORBIDDEN: "patchright+proxy",
    BlockReason.EMPTY_JS_ONLY: "patchright",
    BlockReason.UNKNOWN: "patchright",
}
