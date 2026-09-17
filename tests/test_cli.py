"""The command line, and one security property in particular.

The MCP mount checks `settings.host`, but the socket is bound to whatever the
serve command is told. Those are two different values, and a mismatch would let
`--host 0.0.0.0` pass a guard that looked at loopback and then listen publicly.
"""

from __future__ import annotations

import pytest

from smartscraper.cli import main
from smartscraper.config import get_settings

KEYS = ("SS_HOST", "SS_PORT", "SS_MCP_BEARER_TOKEN", "SS_WEB_PASSWORD",
        "SS_ALLOW_INSECURE_BIND", "SS_DB_PATH")


@pytest.fixture(autouse=True)
def _clean_settings():
    """Restore the environment by hand, not with monkeypatch.

    `serve` publishes the effective host into os.environ on purpose, so that
    every later read agrees with the socket. monkeypatch only reverts variables
    it set itself, so a leaked SS_HOST=0.0.0.0 would follow this module into the
    web tests and make an unauthenticated app refuse to start. It did, once.
    """
    import os

    saved = {k: os.environ.get(k) for k in KEYS}
    for k in KEYS:
        os.environ.pop(k, None)
    get_settings.cache_clear()
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        get_settings.cache_clear()


def test_no_subcommand_prints_help(capsys):
    assert main([]) == 1
    assert "smartscraper" in capsys.readouterr().out


def test_a_public_bind_without_a_token_is_refused(monkeypatch, capsys):
    """The whole point: the guard must see the host that will actually be bound."""
    started: list[tuple] = []
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: started.append((a, k)))

    rc = main(["serve", "--host", "0.0.0.0", "--port", "8131"])

    assert rc == 4
    assert started == [], "uvicorn was started despite the refusal"
    assert "refusing to" in capsys.readouterr().err


def test_a_public_bind_needs_the_ui_password_too(monkeypatch, capsys):
    """An MCP token alone is not enough. The UI is the bigger door: it can run
    scrapers and approve scripts containing unsandboxed Python."""
    import os

    os.environ["SS_MCP_BEARER_TOKEN"] = "a-real-token"
    get_settings.cache_clear()
    started: list[dict] = []
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: started.append(k))

    assert main(["serve", "--host", "0.0.0.0", "--port", "8132"]) == 4
    assert started == []
    assert "web UI" in capsys.readouterr().err


def test_a_public_bind_with_both_credentials_is_allowed(monkeypatch):
    import os

    os.environ["SS_MCP_BEARER_TOKEN"] = "a-real-token"
    os.environ["SS_WEB_PASSWORD"] = "a-real-password"
    get_settings.cache_clear()
    started: list[dict] = []
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: started.append(k))

    assert main(["serve", "--host", "0.0.0.0", "--port", "8132"]) == 0
    assert started and started[0]["host"] == "0.0.0.0"


def test_a_reverse_proxy_override_is_honoured(monkeypatch):
    """Explicit opt-out, for a proxy that authenticates in front."""
    import os

    os.environ["SS_MCP_BEARER_TOKEN"] = "a-real-token"
    os.environ["SS_ALLOW_INSECURE_BIND"] = "1"
    get_settings.cache_clear()
    started: list[dict] = []
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: started.append(k))

    assert main(["serve", "--host", "0.0.0.0", "--port", "8132"]) == 0
    assert started


def test_loopback_needs_no_token(monkeypatch):
    started: list[dict] = []
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: started.append(k))

    assert main(["serve", "--port", "8133"]) == 0
    assert started and started[0]["host"] == "127.0.0.1"
    assert started[0]["port"] == 8133


def test_the_flag_becomes_the_setting_every_later_read_sees(monkeypatch):
    """Publishing the effective host is what keeps the mount's own check honest."""
    import os

    os.environ["SS_MCP_BEARER_TOKEN"] = "a-real-token"
    get_settings.cache_clear()
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: None)

    main(["serve", "--host", "0.0.0.0", "--port", "8134"])

    s = get_settings()
    assert s.host == "0.0.0.0"
    assert s.port == 8134


def test_run_on_an_uninitialised_database_says_so(capsys, tmp_path, monkeypatch):
    """A first run before `init-db` is a normal state, not a stack trace."""
    import os

    os.environ["SS_DB_PATH"] = str(tmp_path / "nothing.db")
    get_settings.cache_clear()
    from smartscraper.db import session as db_session

    db_session.init_engine(f"sqlite+aiosqlite:///{tmp_path / 'nothing.db'}")

    assert main(["run", "anything"]) == 4
    assert "init-db" in capsys.readouterr().err


def test_run_reports_an_unknown_scraper_rather_than_raising(capsys, tmp_path, monkeypatch):
    import asyncio
    import os

    os.environ["SS_DB_PATH"] = str(tmp_path / "ss.db")
    get_settings.cache_clear()
    from smartscraper.db import session as db_session

    db_session.init_engine(f"sqlite+aiosqlite:///{tmp_path / 'ss.db'}")
    asyncio.run(db_session.create_all())

    assert main(["run", "definitely-not-a-scraper"]) == 2
    assert "no scraper named" in capsys.readouterr().err


def test_the_web_ui_refuses_an_unauthenticated_public_bind(monkeypatch, capsys):
    """Neither door may open to a network without credentials."""
    started: list[dict] = []
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: started.append(k))

    assert main(["serve", "--host", "0.0.0.0"]) == 4
    assert started == []
    err = capsys.readouterr().err
    assert "unsandboxed Python" in err, "the refusal should say why it matters"
