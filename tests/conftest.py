"""Test-wide isolation from the developer's environment.

`Settings` reads `.env`, which is how this machine is actually configured: bound
to a Tailscale address, with a web password and an MCP bearer token. Without this
file the suite inherits all of it, and 50 tests fail with 401s that say nothing
about the code.

The rule is that a test run must not depend on how this particular box is
deployed. Anything a test needs, it sets for itself.
"""

from __future__ import annotations

import os
import pathlib
import tempfile

import pytest

# Every setting the deployment sets, forced to a neutral value for the whole run.
# An empty string is deliberate: it reads as "not configured" to the guards, which
# is the state the suite expects, while still shadowing the value in `.env`.
NEUTRAL: dict[str, str] = {
    "SS_HOST": "127.0.0.1",
    "SS_PORT": "8088",
    "SS_WEB_USER": "admin",
    "SS_WEB_PASSWORD": "",
    "SS_MCP_BEARER_TOKEN": "",
    "SS_ALLOW_INSECURE_BIND": "0",
    "SS_RUNNER_CMD": "",
    # A test that reaches `get_session` without pointing it somewhere first must
    # not land in the real database. Two scrapers from a journey test were found
    # in data/smartscraper.db because this was missing.
    "SS_DB_PATH": str(pathlib.Path(tempfile.gettempdir()) / "smartscraper-tests" / "test.db"),
}


def pytest_configure(config: pytest.Config) -> None:
    """Runs before collection, so module-level settings reads are covered too."""
    from smartscraper.config import Settings

    # Cut `.env` out of the run entirely. Neutralising the variables is not enough:
    # a test that *deletes* one to prove a guard fires would un-shadow `.env` and
    # resurrect the deployment's real token, so the guard would not fire and the
    # test would fail for a reason that has nothing to do with the code. It did.
    Settings.model_config["env_file"] = None

    for key, value in NEUTRAL.items():
        os.environ[key] = value
    pathlib.Path(NEUTRAL["SS_DB_PATH"]).parent.mkdir(parents=True, exist_ok=True)
    _clear_settings_cache()


def _clear_settings_cache() -> None:
    from smartscraper.config import get_settings

    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _isolated_settings():
    """Restore the neutral values after any test that changed them.

    Several tests legitimately set `SS_WEB_PASSWORD` or `SS_HOST` to exercise a
    guard. Without this they leak into whatever runs next, which is how a CLI
    test once made 44 unrelated web tests fail.
    """
    before = {k: os.environ.get(k) for k in NEUTRAL}
    try:
        yield
    finally:
        changed = any(os.environ.get(k) != v for k, v in before.items())
        for key, value in before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        if changed:
            _clear_settings_cache()
