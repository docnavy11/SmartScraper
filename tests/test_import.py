"""Bringing in a script you already have, and the README that promises it works.

The builder wrote a scraper and registered it in one motion, which left no way
to import one: an example, one copied between machines, one written by hand. The
README told people to copy a file into `scrapers/` and run it. That did nothing,
because a run is looked up in the database and a loose file is not in it. A
stranger's first two commands failed.

So one of these tests reads the commands out of the README and checks they are
the ones that exist. Documentation that drifts from the tool is a bug with a
longer fuse than most.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from smartscraper import repo
from smartscraper.db.models import Base, VersionStatus
from smartscraper.importer import ImportRefused, import_script, name_from_url

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = (ROOT / "examples" / "books.yaml").read_text(encoding="utf-8")


@pytest.fixture
async def session(tmp_path, monkeypatch):
    from smartscraper import config, importer

    scrapers = tmp_path / "scrapers"
    scrapers.mkdir()

    class Fake:
        scrapers_dir = scrapers
        default_scraper_budget = 10.0

        def ensure_dirs(self):
            return None

    monkeypatch.setattr(importer, "get_settings", lambda: Fake())
    assert config  # imported for the reader; the patch above is what matters

    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(eng, expire_on_commit=False)() as s:
        yield s
    await eng.dispose()


# ------------------------------------------------------------------ importing
async def test_importing_the_example_makes_it_runnable(session):
    result = await import_script(session, EXAMPLE, name="books")

    scraper = await repo.get_scraper(session, "books")
    assert scraper is not None, "imported but `run` would not find it"
    assert scraper.id == result.scraper_id
    assert result.path.exists(), "the yaml was not written to scrapers/"


async def test_the_imported_version_is_active_so_a_run_picks_it_up(session):
    await import_script(session, EXAMPLE, name="books")
    scraper = await repo.get_scraper(session, "books")

    active = await repo.active_version(session, scraper.id)
    assert active is not None
    assert active.status == VersionStatus.ACTIVE
    assert active.created_by == "human"


async def test_a_file_that_is_not_a_script_is_refused_before_anything_is_written(session):
    with pytest.raises(ImportRefused) as exc:
        await import_script(session, "steps: [this is not: valid: yaml", name="junk")

    assert "not a valid scrape script" in str(exc.value)
    assert await repo.get_scraper(session, "junk") is None


async def test_a_duplicate_name_is_refused_rather_than_silently_overwriting(session):
    await import_script(session, EXAMPLE, name="books")

    with pytest.raises(ImportRefused) as exc:
        await import_script(session, EXAMPLE, name="books")

    assert "already exists" in str(exc.value)


async def test_the_import_is_audited(session):
    await import_script(session, EXAMPLE, name="books", actor="you")

    entries = await repo.audit(session, limit=5)
    assert any(e.action == "imported scraper" for e in entries)


async def test_an_imported_custom_python_step_is_flagged(session):
    """An imported file is code somebody else wrote, and that step is unsandboxed."""
    risky = EXAMPLE.replace(
        "- op: emit",
        "- op: custom_python\n  code: |\n    return 1\n- op: emit",
    )
    result = await import_script(session, risky, name="risky")

    assert result.has_custom_python
    scraper = await repo.get_scraper(session, "risky")
    version = await repo.active_version(session, scraper.id)
    assert version.has_custom_python


# ------------------------------------------------------------------ naming
@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://books.toscrape.com/catalogue/category/books/mystery_3/index.html",
         "books-mystery-3"),
        ("https://shop.example.eu/pricing?page=1", "shop-pricing"),
        ("https://news.ycombinator.com/newest", "news-newest"),
        ("https://www.example.com/", "example"),
    ],
)
def test_a_url_becomes_a_name_someone_would_type(url, expected):
    """Slugifying the whole URL produced
    `https-books-toscrape-com-catalogue-category-books-mystery-3-index-html`."""
    assert name_from_url(url) == expected


# ------------------------------------------------------------------ the README
def test_the_readme_only_tells_people_to_run_commands_that_exist():
    """The README promised `cp examples/... scrapers/` then `run`, which did
    nothing at all. Documentation that drifts from the tool is still a bug."""
    from smartscraper.cli import main

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    invoked = set(re.findall(r"^\s*smartscraper\s+([a-z-]+)", readme, re.M))
    assert invoked, "the README shows no commands at all"

    import argparse
    import contextlib
    import io

    help_text = io.StringIO()
    with contextlib.redirect_stdout(help_text), contextlib.suppress(SystemExit):
        main(["--help"])
    available = set(re.findall(r"[a-z-]+", help_text.getvalue().split("{", 1)[-1].split("}", 1)[0]))
    assert available, "could not read the subcommand list"

    missing = invoked - available
    assert not missing, f"the README uses commands that do not exist: {sorted(missing)}"
    assert argparse  # the import documents where `available` comes from


def test_the_readme_example_names_the_scraper_the_import_actually_creates():
    """`smartscraper import examples/books.yaml` creates `books`, from the file
    stem. If the README says `run something-else`, the walkthrough breaks."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    if "smartscraper import examples/books.yaml" not in readme:
        pytest.skip("the README no longer walks through the example")

    run_names = re.findall(r"smartscraper run ([a-z0-9-]+)", readme)
    assert "books" in run_names, f"README runs {run_names}, but import creates 'books'"
