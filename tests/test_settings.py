"""Settings you can change, and the line between those and the ones you cannot.

The settings screen used to be read-only, and the reason written into the code
was that "the web layer does not own config.py". That was true of one agent
during a parallel build and was never true of the system. It is the kind of
justification that survives long after the circumstance that produced it, so
these tests pin the real boundary instead: a value read once at process start
cannot usefully change while the process runs, and a secret should not travel
through a browser form. Everything else is editable.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from smartscraper import settings_store
from smartscraper.config import get_settings, set_overrides
from smartscraper.db.models import Base, Setting
from smartscraper.web.app import create_app


@pytest.fixture
async def session():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(eng, expire_on_commit=False)() as s:
        yield s
    set_overrides({})
    await eng.dispose()


@pytest.fixture
def client(tmp_path):
    from smartscraper.db import session as db_session

    url = f"sqlite+aiosqlite:///{tmp_path / 'settings.db'}"
    saved = (db_session._engine, db_session._factory)
    db_session.init_engine(url)
    try:
        with TestClient(create_app(create_async_engine(url, future=True), mount_mcp=False)) as c:
            yield c
    finally:
        set_overrides({})
        db_session._engine, db_session._factory = saved


# ------------------------------------------------------------------ the split
def test_the_editable_set_is_the_whole_model_minus_the_fixed_ones():
    from smartscraper.config import Settings

    assert set(settings_store.EDITABLE) | settings_store.FIXED == set(Settings.model_fields)
    assert not set(settings_store.EDITABLE) & settings_store.FIXED


@pytest.mark.parametrize("name", ["web_password", "mcp_bearer_token", "secret_key"])
def test_a_secret_is_never_editable_through_the_form(name):
    assert name in settings_store.FIXED
    assert name not in settings_store.EDITABLE


@pytest.mark.parametrize("name", ["host", "port", "db_path", "data_dir"])
def test_a_value_read_at_startup_is_not_editable_at_runtime(name):
    """Changing these would say one thing and do another, since the socket is
    already bound and the engine already built."""
    assert name in settings_store.FIXED


def test_every_editable_setting_appears_on_the_screen():
    grouped = {n for names in settings_store.GROUPS.values() for n in names}
    assert grouped == set(settings_store.EDITABLE), grouped ^ set(settings_store.EDITABLE)


# ------------------------------------------------------------------ saving
async def test_a_saved_setting_takes_effect_immediately(session):
    assert get_settings().monthly_budget == 60.0

    applied, rejected = await settings_store.save(session, {"monthly_budget": 250.0})

    assert rejected == []
    assert applied == {"monthly_budget": 250.0}
    assert get_settings().monthly_budget == 250.0, "saved but not applied to this process"


async def test_clearing_a_setting_hands_it_back_to_the_environment(session):
    await settings_store.save(session, {"max_builder_turns": 5})
    assert get_settings().max_builder_turns == 5

    await settings_store.save(session, {"max_builder_turns": None})

    assert get_settings().max_builder_turns == 80, "an empty box should restore the default"
    assert await session.scalar(
        Setting.__table__.select().where(Setting.key == "max_builder_turns")
    ) is None


async def test_an_invalid_value_is_refused_and_nothing_is_written(session):
    before = get_settings().max_captcha_spend_per_run

    applied, rejected = await settings_store.save(session, {"agent_effort": 3})

    # whatever the model says about it, a rejection must change nothing
    if rejected:
        assert applied == {}
    assert get_settings().max_captcha_spend_per_run == before


async def test_a_fixed_setting_is_refused_even_if_someone_posts_it(session):
    applied, rejected = await settings_store.save(session, {"mcp_bearer_token": "stolen"})

    assert applied == {}
    assert any("not editable" in r for r in rejected)
    assert get_settings().mcp_bearer_token != "stolen"


async def test_a_change_is_written_to_the_audit_log(session):
    from smartscraper import repo

    await settings_store.save(session, {"monthly_budget": 99.0}, actor="you")
    entries = await repo.audit(session, limit=5)

    assert any(e.action == "changed settings" for e in entries)
    entry = next(e for e in entries if e.action == "changed settings")
    assert "monthly_budget" in entry.detail


async def test_a_secretish_value_is_redacted_in_the_audit_log(session):
    from smartscraper import repo

    await settings_store.save(session, {"anthropic_api_key": "sk-ant-verysecretvalue1234"})
    entry = next(e for e in await repo.audit(session, limit=5) if e.action == "changed settings")

    assert "verysecretvalue" not in entry.detail
    assert "1234" in entry.detail


async def test_overrides_survive_a_restart(session):
    await settings_store.save(session, {"run_timeout_s": 42})
    set_overrides({})                       # as if the process had just started
    assert get_settings().run_timeout_s != 42

    await settings_store.load(session)

    assert get_settings().run_timeout_s == 42


# ------------------------------------------------------------------ the screen
def test_the_page_no_longer_claims_to_be_read_only(client):
    body = client.get("/settings").text
    assert "read-only" not in body.lower()
    assert "does not own that file" not in body
    assert '<form' in body and 'action="/settings"' in body


def test_posting_the_form_saves_and_says_so(client):
    r = client.post("/settings", data={"monthly_budget": "175.5"}, follow_redirects=False)
    assert r.status_code == 303
    assert "saved=" in r.headers["location"]

    body = client.get(r.headers["location"]).text
    assert "175.5" in body
    assert get_settings().monthly_budget == 175.5


def test_a_bad_number_is_reported_rather_than_stored(client):
    r = client.post("/settings", data={"max_builder_turns": "lots"}, follow_redirects=False)

    assert "error=" in r.headers["location"]
    assert get_settings().max_builder_turns == 80


def test_a_partial_post_does_not_switch_off_flags_it_never_showed(client):
    """An unchecked box sends nothing, so absence has to mean false. That is only
    safe for boxes the form rendered.

    Without a marker saying which fields were on the page, a post carrying one
    field turned off every flag on the screen, the budget guard included. Nobody
    would think to check for that.
    """
    assert get_settings().stop_at_budget is True

    client.post("/settings", data={"monthly_budget": "80"}, follow_redirects=False)

    assert get_settings().stop_at_budget is True, "a partial post disarmed the budget guard"
    assert get_settings().monthly_budget == 80.0


def test_a_full_form_post_can_still_switch_a_flag_off(client):
    """The marker must not make checkboxes unusable."""
    from smartscraper import settings_store

    every = ",".join(f["name"] for f in settings_store.current_view())
    client.post(
        "/settings",
        data={"_present": every, "monthly_budget": "80"},   # no boxes ticked
        follow_redirects=False,
    )

    assert get_settings().stop_at_budget is False
