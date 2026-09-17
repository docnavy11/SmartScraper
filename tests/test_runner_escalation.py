"""The ladder: which failures are worth another rung, and which are not.

Engines are injected, so the whole ladder is exercised without a browser
binary. The assertions are about the routing decision, which is the only thing
this module owns.
"""

from __future__ import annotations

from typing import Any

import pytest

from smartscraper.contracts import BlockReason
from smartscraper.dsl.models import Rung, ScrapeScript
from smartscraper.runner.engines.base import InteractionUnsupported
from smartscraper.runner.engines.browser import parse_proxy, probe_availability
from smartscraper.runner.errors import (
    Blocked,
    EngineUnavailable,
    SelectorNotFound,
    StepError,
)
from smartscraper.runner.escalation import build_engine, escalate, rungs_for
from smartscraper.runner.runlog import null_logger

PAGE = '<html><body><div class="row"><span class="a">one</span></div></body></html>'

SCRIPT = """
rate_limit: {min_delay_s: 0, max_delay_s: 0}
escalation: [http, patchright, patchright+proxy]
steps:
  - {op: goto, url: "https://x.test/"}
  - {op: extract_list, selector: "css=.row", as: r, fields: {a: {selector: "css=.a"}}}
  - {op: emit, from: r}
"""


def script(body: str = SCRIPT) -> ScrapeScript:
    return ScrapeScript.from_yaml(body)


class ScriptedEngine:
    """Succeeds, or raises whatever the test told it to raise."""

    interactive = True

    def __init__(self, name: str, raises: BaseException | None = None) -> None:
        self.name = name
        self.raises = raises
        self.current_url = "https://x.test/"
        self.last_status = 200
        self.last_headers: dict[str, str] = {}
        self.opened_with: dict[str, Any] = {}
        self.closed = False
        from smartscraper.runner.engines.http import HttpEngine

        self._inner = HttpEngine()
        self._inner.set_content(PAGE, url="https://x.test/")

    async def open(self, **kw) -> None:
        self.opened_with = kw

    async def goto(self, url, *, timeout_ms=30_000, wait_until="domcontentloaded") -> int:
        if self.raises is not None:
            raise self.raises
        return 200

    async def content(self) -> str:
        return PAGE

    async def query(self, selector, *, limit=None):
        return await self._inner.query(selector, limit=limit)

    async def count(self, selector) -> int:
        return await self._inner.count(selector)

    async def wait_for(self, selector, **kw) -> bool:
        return True

    async def screenshot(self, path, *, full_page=False):
        return None

    async def close(self) -> None:
        self.closed = True


def factory_for(plan: dict[str, BaseException | None]):
    """Build an engine per rung, raising whatever `plan` says for that rung."""
    made: dict[str, ScriptedEngine] = {}

    def factory(rung, *, artifacts_dir=None, headed=False):
        if isinstance(plan.get(str(rung)), EngineUnavailable):
            raise plan[str(rung)]
        engine = ScriptedEngine(str(rung), plan.get(str(rung)))
        made[str(rung)] = engine
        return engine, ("script" if "proxy" in str(rung) else None)

    factory.made = made  # type: ignore[attr-defined]
    return factory


# ------------------------------------------------------------------ the ladder
def test_rungs_for_uses_the_scripts_ladder():
    assert rungs_for(script()) == [Rung.HTTP, Rung.PATCHRIGHT, Rung.PATCHRIGHT_PROXY]


def test_a_named_engine_skips_the_cheaper_rungs():
    # The builder already decided HTTP does not work for this site; starting
    # there again would cost a request and a page load on every scheduled run.
    parsed = script(SCRIPT.replace("escalation:", "engine: patchright\nescalation:"))
    assert rungs_for(parsed) == [Rung.PATCHRIGHT, Rung.PATCHRIGHT_PROXY]


def test_a_named_engine_outside_the_ladder_is_prepended():
    parsed = script(SCRIPT.replace("escalation: [http, patchright, patchright+proxy]",
                                   "engine: camoufox\nescalation: [http, patchright]"))
    assert rungs_for(parsed)[0] == Rung.CAMOUFOX


def test_an_empty_ladder_still_has_a_default():
    parsed = script(SCRIPT.replace("escalation: [http, patchright, patchright+proxy]",
                                   "escalation: []"))
    assert rungs_for(parsed) == [Rung.HTTP, Rung.PATCHRIGHT]


# --------------------------------------------------------------------- climbing
async def test_the_first_rung_wins_when_it_works():
    result = await escalate(script(), log=null_logger(), engine_factory=factory_for({}))
    assert result.outcome.error is None
    assert len(result.attempts) == 1
    assert result.succeeded_on.rung == "http"
    assert result.outcome.escalation_level == 0


async def test_a_block_climbs_to_the_next_rung():
    plan = {"http": Blocked(BlockReason.CLOUDFLARE, status=403)}
    result = await escalate(script(), log=null_logger(), engine_factory=factory_for(plan))
    assert [a.rung for a in result.attempts] == ["http", "patchright"]
    assert result.outcome.escalation_level == 1
    assert result.attempts[0].blocked and result.attempts[0].block_reason == "cloudflare"


async def test_a_block_on_every_rung_reports_blocked_with_the_reason():
    plan = dict.fromkeys(
        ("http", "patchright", "patchright+proxy"), None
    )
    for k in plan:
        plan[k] = Blocked(BlockReason.DATADOME)
    result = await escalate(script(), log=null_logger(), engine_factory=factory_for(plan))
    assert len(result.attempts) == 3
    assert result.outcome.blocked and result.outcome.block_reason == BlockReason.DATADOME


async def test_an_unavailable_engine_climbs_rather_than_failing_the_run():
    # This is the no-browser-installed case: the HTTP rung must not be punished
    # for Chromium being absent.
    plan = {"http": Blocked("cloudflare"),
            "patchright": EngineUnavailable("no browser binary downloaded")}
    result = await escalate(script(), log=null_logger(), engine_factory=factory_for(plan))
    assert [a.rung for a in result.attempts] == ["http", "patchright", "patchright+proxy"]
    assert result.outcome.error is None


async def test_a_browser_only_step_climbs_off_the_http_rung():
    plan = {"http": InteractionUnsupported("fill", "http")}
    result = await escalate(script(), log=null_logger(), engine_factory=factory_for(plan))
    assert result.succeeded_on.rung == "patchright"


async def test_a_missing_selector_climbs_off_http_because_js_may_render_it():
    plan = {"http": SelectorNotFound("no match for css=.row", candidates=["css=.row"])}
    result = await escalate(script(), log=null_logger(), engine_factory=factory_for(plan))
    assert result.succeeded_on.rung == "patchright"


async def test_a_missing_selector_above_http_does_not_climb_again():
    # A second browser will not find a selector the first browser could not.
    plan = {
        "http": Blocked("cloudflare"),
        "patchright": SelectorNotFound("no match", candidates=["css=.row"]),
    }
    result = await escalate(script(), log=null_logger(), engine_factory=factory_for(plan))
    assert [a.rung for a in result.attempts] == ["http", "patchright"]
    assert "SelectorNotFound" in (result.outcome.error or "")


async def test_a_step_error_stops_the_ladder_immediately():
    plan = {"http": StepError("secret 'shop_pw' is not in the secret store", op="fill")}
    result = await escalate(script(), log=null_logger(), engine_factory=factory_for(plan))
    assert len(result.attempts) == 1
    assert "shop_pw" in (result.outcome.error or "")


async def test_an_unexpected_exception_stops_the_ladder():
    plan = {"http": RuntimeError("something nobody predicted")}
    result = await escalate(script(), log=null_logger(), engine_factory=factory_for(plan))
    assert len(result.attempts) == 1
    assert "RuntimeError" in (result.outcome.error or "")


async def test_the_last_rung_never_climbs_even_on_a_block():
    parsed = script(SCRIPT.replace("escalation: [http, patchright, patchright+proxy]",
                                   "escalation: [http]"))
    result = await escalate(parsed, log=null_logger(),
                            engine_factory=factory_for({"http": Blocked("cloudflare")}))
    assert len(result.attempts) == 1


# ------------------------------------------------------------------- the proxy
async def test_only_the_proxy_rung_receives_the_proxy():
    plan = {"http": Blocked("cloudflare"), "patchright": Blocked("cloudflare")}
    factory = factory_for(plan)
    await escalate(script(), log=null_logger(), engine_factory=factory,
                   proxy="http://user:pw@proxy.test:8000")
    made = factory.made  # type: ignore[attr-defined]
    assert made["http"].opened_with["proxy"] is None
    assert made["patchright"].opened_with["proxy"] is None
    assert made["patchright+proxy"].opened_with["proxy"] == "http://user:pw@proxy.test:8000"


async def test_the_profile_is_passed_to_every_rung():
    parsed = script(SCRIPT.replace("rate_limit:", "profile: site-example\nrate_limit:"))
    factory = factory_for({})
    await escalate(parsed, log=null_logger(), engine_factory=factory)
    assert factory.made["http"].opened_with["profile"] == "site-example"  # type: ignore[attr-defined]


# ------------------------------------------------------------------- reporting
async def test_the_summary_names_the_winning_rung():
    plan = {"http": Blocked("cloudflare")}
    result = await escalate(script(), log=null_logger(), engine_factory=factory_for(plan))
    assert "rung 1 (patchright)" in result.summary()


async def test_the_summary_says_when_everything_failed():
    plan = dict.fromkeys(("http", "patchright", "patchright+proxy"), Blocked("akamai"))
    result = await escalate(script(), log=null_logger(), engine_factory=factory_for(plan))
    assert "every rung failed" in result.summary()


async def test_every_attempt_is_logged():
    log = null_logger()
    plan = {"http": Blocked("cloudflare")}
    await escalate(script(), log=log, engine_factory=factory_for(plan))
    tags = [x["tag"] for x in log.lines]
    assert tags.count("escalation") >= 3     # ladder, two rungs, verdict


# ----------------------------------------------------------- real engine wiring
def test_build_engine_returns_the_http_engine_for_the_http_rung():
    engine, proxy_role = build_engine(Rung.HTTP)
    assert engine.name == "http" and proxy_role is None
    assert engine.interactive is False


def test_build_engine_arms_the_proxy_only_on_the_proxy_rung():
    assert build_engine(Rung.PATCHRIGHT)[1] is None
    assert build_engine(Rung.PATCHRIGHT_PROXY)[1] == "script"


def test_byparr_is_honestly_reported_as_not_implemented():
    with pytest.raises(EngineUnavailable) as exc:
        build_engine(Rung.BYPARR)
    assert "not implemented" in str(exc.value)


def test_an_unknown_rung_is_rejected():
    with pytest.raises(EngineUnavailable):
        build_engine("teleportation")


# --------------------------------------------------------------- browser bits
def test_browser_engine_module_imports_without_a_browser_installed():
    # The whole point of the lazy imports: this must never need Chromium.
    from smartscraper.runner.engines import browser

    assert browser.LAUNCHERS["patchright"][0] == "patchright.async_api"


def test_probe_availability_reports_without_launching():
    usable, reason = probe_availability("patchright")
    assert isinstance(usable, bool) and reason
    if not usable:
        assert "browser binary" in reason or "not installed" in reason


def test_probe_availability_rejects_an_unknown_engine():
    usable, reason = probe_availability("netscape")
    assert not usable and "unknown" in reason


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("http://h:8000", {"server": "http://h:8000"}),
        ("h:8000", {"server": "http://h:8000"}),
        ("socks5://h:1080", {"server": "socks5://h:1080"}),
        ("http://u:p@h:8000",
         {"server": "http://h:8000", "username": "u", "password": "p"}),
        ({"server": "http://h:1", "username": "u", "password": None},
         {"server": "http://h:1", "username": "u"}),
    ],
)
def test_parse_proxy(given, expected):
    assert parse_proxy(given) == expected


def test_parse_proxy_url_encoded_credentials():
    got = parse_proxy("http://user%40mail:p%40ss@h:8000")
    assert got["username"] == "user@mail" and got["password"] == "p@ss"


def test_parse_proxy_of_none_is_none():
    assert parse_proxy(None) is None


def test_parse_proxy_without_a_host_is_rejected():
    with pytest.raises(EngineUnavailable):
        parse_proxy("http://")
